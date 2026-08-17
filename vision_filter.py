from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageDraw, ImageFont, ImageOps
from transformers import (
    AutoImageProcessor,
    AutoModelForObjectDetection,
    AutoModelForZeroShotImageClassification,
    AutoProcessor,
)

FASHION_MODEL = "valentinafevu/yolos-fashionpedia"
CLIP_MODEL = "openai/clip-vit-base-patch32"

PROMPTS = [
    "a regular short sleeve crew neck T-shirt",
    "a sleeveless tank top or camisole",
    "a polo shirt with a collar",
    "a soccer jersey, basketball jersey, baseball jersey, or sports jersey",
    "a button-up shirt or blouse",
    "a sweatshirt or hoodie",
    "a sweater or knit top",
    "a jacket or coat",
    "a dress or one-piece outfit",
]
PROMPT_NAMES = ["tshirt", "tank", "polo", "sports_jersey", "button_up", "sweatshirt", "sweater", "jacket", "dress"]

UPPER_LABELS = {
    "top, t-shirt, sweatshirt",
    "shirt, blouse",
    "sweater",
    "cardigan",
    "jacket",
    "vest",
    "coat",
    "dress",
    "jumpsuit",
}
TLIKE_LABEL = "top, t-shirt, sweatshirt"


def load_metadata(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "metadata.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        selected = str(row.get("selected_file") or "")
        if selected:
            out[Path(selected).name] = row
    return out


def font(size: int, bold: bool = False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(p, size=size)
    except Exception:
        return ImageFont.load_default()


def expand(box: list[float], w: int, h: int, f: float = 0.10) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    return (
        max(0, int(x1 - bw * f)),
        max(0, int(y1 - bh * f)),
        min(w, int(x2 + bw * f)),
        min(h, int(y2 + bh * f)),
    )


def clip_scores(crops: list[Image.Image], processor, model) -> list[dict[str, float]]:
    if not crops:
        return []
    inputs = processor(text=PROMPTS, images=crops, return_tensors="pt", padding=True)
    with torch.inference_mode():
        probs = torch.softmax(model(**inputs).logits_per_image, dim=1).cpu().tolist()
    return [{PROMPT_NAMES[i]: float(row[i]) for i in range(len(row))} for row in probs]


def rank_score(row: dict[str, Any]) -> float:
    score = float(row.get("tshirt_prob") or 0.0) + max(-0.5, float(row.get("tshirt_margin") or 0.0))
    if row.get("fashion_label") == TLIKE_LABEL:
        score += 0.55
    else:
        score -= 0.40
    if row.get("clip_top") == "tshirt":
        score += 0.55
    return score


def make_sheets(rows: list[dict[str, Any]], image_root: Path, out_dir: Path, prefix: str, limit: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = rows[:limit]
    cols, per = 5, 25
    cw, ch, lh = 420, 440, 94
    f1, f2 = font(17, True), font(13, False)
    sheets = 0
    for start in range(0, len(rows), per):
        chunk = rows[start:start + per]
        canvas = Image.new("RGB", (cw * cols, ch * 5), "white")
        draw = ImageDraw.Draw(canvas)
        for pos, row in enumerate(chunk):
            rr, cc = divmod(pos, cols)
            x0, y0 = cc * cw, rr * ch
            path = image_root / row["source_name"]
            try:
                im = Image.open(path).convert("RGB")
                box = row.get("garment_box")
                if box:
                    x1, y1, x2, y2 = [int(v) for v in box]
                    outline = "lime" if row.get("decision") == "accepted_tshirt" else "orange"
                    ImageDraw.Draw(im).rectangle((x1, y1, x2, y2), outline=outline, width=max(3, im.width // 260))
                fitted = ImageOps.contain(im, (cw - 8, ch - lh - 8), Image.Resampling.LANCZOS)
                canvas.paste(fitted, (x0 + (cw - fitted.width) // 2, y0 + lh + (ch - lh - fitted.height) // 2))
            except Exception:
                pass
            draw.rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline="black", width=2)
            draw.text((x0 + 5, y0 + 4), f"#{row.get('index','?')} {row.get('decision','')}", fill="black", font=f1)
            draw.text((x0 + 5, y0 + 28), f"{row.get('clip_top','')} T={row.get('tshirt_prob',0):.2f} m={row.get('tshirt_margin',0):.2f}", fill="black", font=f2)
            draw.text((x0 + 5, y0 + 48), f"FP {row.get('fashion_label','')} {row.get('fashion_conf',0):.2f}"[:58], fill="black", font=f2)
            draw.text((x0 + 5, y0 + 68), f"@{row.get('handle','')} · {row.get('city','')}"[:58], fill="black", font=f2)
        sheets += 1
        canvas.save(out_dir / f"{prefix}_{sheets:03d}.jpg", "JPEG", quality=87, optimize=True)
    return sheets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="candidate_pool")
    ap.add_argument("--out", default="garment_audit")
    ap.add_argument("--det-threshold", type=float, default=0.18)
    ap.add_argument("--min-garment-area", type=float, default=0.012)
    ap.add_argument("--accept-prob", type=float, default=0.50)
    ap.add_argument("--accept-margin", type=float, default=0.35)
    ap.add_argument("--borderline-margin", type=float, default=0.05)
    ap.add_argument("--review-limit", type=int, default=1200)
    ap.add_argument("--lightweight", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    image_root = root / "images" if (root / "images").exists() else root
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(root)
    paths = sorted(image_root.glob("*.jpg"))
    if not paths:
        (out / "vision_results.json").write_text("[]", encoding="utf-8")
        (out / "summary.json").write_text(json.dumps({"input_images": 0}), encoding="utf-8")
        return

    print(f"input_images={len(paths)}", flush=True)
    fashion_processor = AutoImageProcessor.from_pretrained(FASHION_MODEL)
    fashion = AutoModelForObjectDetection.from_pretrained(FASHION_MODEL)
    fashion.eval()
    clip_processor = AutoProcessor.from_pretrained(CLIP_MODEL)
    clip = AutoModelForZeroShotImageClassification.from_pretrained(CLIP_MODEL)
    clip.eval()

    id2label = fashion.config.id2label
    if isinstance(id2label, dict):
        id2label = {int(k): v for k, v in id2label.items()}

    rows: list[dict[str, Any]] = []
    no_upper: list[dict[str, Any]] = []

    for seq, path in enumerate(paths, start=1):
        try:
            im = Image.open(path).convert("RGB")
        except Exception as exc:
            no_upper.append({"source_name": path.name, "reason": f"decode:{exc}"})
            continue
        w, h = im.size
        area = float(max(1, w * h))
        inputs = fashion_processor(images=im, return_tensors="pt")
        with torch.inference_mode():
            outputs = fashion(**inputs)
        det = fashion_processor.post_process_object_detection(outputs, threshold=args.det_threshold, target_sizes=torch.tensor([[h, w]]))[0]

        candidates: list[tuple[dict[str, Any], Image.Image]] = []
        for score, label_id, box_tensor in zip(det["scores"], det["labels"], det["boxes"]):
            label = str(id2label[int(label_id)])
            if label not in UPPER_LABELS:
                continue
            box = [float(v) for v in box_tensor.tolist()]
            x1, y1, x2, y2 = box
            area_ratio = max(0.0, x2 - x1) * max(0.0, y2 - y1) / area
            if area_ratio < args.min_garment_area:
                continue
            cb = expand(box, w, h, 0.10)
            crop = im.crop(cb)
            if crop.width < 65 or crop.height < 65:
                continue
            candidates.append(({"fashion_label": label, "fashion_conf": float(score), "garment_box": box, "crop_box": list(cb)}, crop))

        if not candidates:
            no_upper.append({"source_name": path.name, "reason": "no_clear_upper_garment"})
            continue

        probs = clip_scores([crop for _, crop in candidates], clip_processor, clip)
        meta = metadata.get(path.name, {})
        best: dict[str, Any] | None = None
        for (base, _), prob in zip(candidates, probs):
            order = sorted(prob, key=prob.get, reverse=True)
            top, second = order[0], order[1]
            row = {
                "index": int(meta.get("index") or seq),
                "source_name": path.name,
                "city": meta.get("city"),
                "handle": meta.get("handle"),
                "source_url": meta.get("source_url"),
                "timestamp": meta.get("timestamp"),
                "parent_shortcode": meta.get("parent_shortcode"),
                "shortcode": meta.get("shortcode"),
                "child_index": meta.get("child_index"),
                "is_carousel_child": meta.get("is_carousel_child"),
                **base,
                "clip_top": top,
                "clip_top_prob": prob[top],
                "clip_second": second,
                "clip_second_prob": prob[second],
                "tshirt_prob": prob["tshirt"],
                "tshirt_margin": prob["tshirt"] - max(v for k, v in prob.items() if k != "tshirt"),
                "clip_probs": prob,
            }
            row["rank_score"] = rank_score(row)
            if best is None or row["rank_score"] > best["rank_score"]:
                best = row

        if best is None:
            continue
        high = best["fashion_label"] == TLIKE_LABEL and best["clip_top"] == "tshirt" and best["tshirt_prob"] >= args.accept_prob and best["tshirt_margin"] >= args.accept_margin
        borderline = best["fashion_label"] == TLIKE_LABEL and best["clip_top"] == "tshirt" and best["tshirt_margin"] >= args.borderline_margin
        best["decision"] = "accepted_tshirt" if high else ("borderline_tshirt" if borderline else "rejected")
        rows.append(best)

        if seq % 50 == 0:
            hi = sum(r.get("decision") == "accepted_tshirt" for r in rows)
            bord = sum(r.get("decision") == "borderline_tshirt" for r in rows)
            print(f"processed={seq}/{len(paths)} high={hi} borderline={bord}", flush=True)

    high = [r for r in rows if r["decision"] == "accepted_tshirt"]
    borderline = [r for r in rows if r["decision"] == "borderline_tshirt"]
    rejected = [r for r in rows if r["decision"] == "rejected"]
    high.sort(key=lambda r: (float(r.get("timestamp") or 0), r["rank_score"]), reverse=True)
    borderline.sort(key=lambda r: r["rank_score"], reverse=True)
    rejected.sort(key=lambda r: r["rank_score"], reverse=True)
    review_queue = sorted(high + borderline + rejected, key=lambda r: r["rank_score"], reverse=True)

    all_rows = high + borderline + rejected
    (out / "vision_results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "review_queue.json").write_text(json.dumps(review_queue[:args.review_limit], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "preliminary_rejections.json").write_text(json.dumps(no_upper, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = ["index", "source_name", "decision", "rank_score", "city", "handle", "source_url", "timestamp", "parent_shortcode", "shortcode", "child_index", "is_carousel_child", "fashion_label", "fashion_conf", "clip_top", "clip_top_prob", "clip_second", "clip_second_prob", "tshirt_prob", "tshirt_margin", "garment_box", "crop_box"]
    with (out / "vision_results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    sheet_count = 0
    if not args.lightweight:
        sheet_count += make_sheets(high, image_root, out / "contact_sheets", "high_confidence", 500)
        sheet_count += make_sheets(borderline, image_root, out / "contact_sheets", "borderline", 700)
        sheet_count += make_sheets(review_queue, image_root, out / "review_sheets", "ranked_review", args.review_limit)

    summary = {
        "input_images": len(paths),
        "images_with_upper_garment": len(rows),
        "no_clear_upper_garment": len(no_upper),
        "high_confidence_tshirts": len(high),
        "borderline_tshirts": len(borderline),
        "rejected": len(rejected),
        "clip_top_counts": dict(Counter(r["clip_top"] for r in rows)),
        "fashion_label_counts": dict(Counter(r["fashion_label"] for r in rows)),
        "thresholds": {"detector": args.det_threshold, "min_garment_area": args.min_garment_area, "accept_probability": args.accept_prob, "accept_margin": args.accept_margin, "borderline_margin": args.borderline_margin},
        "review_sheet_count": sheet_count,
        "method": "FashionPedia garment detection first, then CLIP comparison among T-shirt/tank/polo/sports jersey/button-up/sweatshirt/sweater/jacket/dress. High-confidence acceptance requires detector and classifier agreement. Borderline candidates require human visual QA.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
