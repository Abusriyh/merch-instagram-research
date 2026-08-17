from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import hf_hub_download
from PIL import Image, ImageDraw, ImageFont, ImageOps
from transformers import AutoImageProcessor, AutoModelForImageClassification
from ultralytics import YOLO


FASHION_REPO = "AltaDaily/yolo11n-fashionpedia"
FASHION_FILE = "best.pt"
CLASSIFIER_REPO = "dima806/clothes_image_detection"

# FashionPedia upper-body categories that can plausibly contain/mask a T-shirt.
UPPER_LABELS = {
    "shirt/blouse",
    "shirt, blouse",
    "top/t-shirt/sweatshirt",
    "top, t-shirt, sweatshirt",
    "sweater",
    "cardigan",
    "jacket",
    "vest",
    "coat",
    "dress",
    "jumpsuit",
}

NEGATIVE_UPPER = {"Polo", "Shirt", "Hoodie", "Sweater", "Jacket", "Blazer", "Coat", "Sports Jacket"}


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    label: str

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


def intersection_over_garment(garment: Box, person: Box) -> float:
    ix1 = max(garment.x1, person.x1)
    iy1 = max(garment.y1, person.y1)
    ix2 = min(garment.x2, person.x2)
    iy2 = min(garment.y2, person.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return inter / garment.area if garment.area > 0 else 0.0


def expand_box(box: Box, w: int, h: int, frac: float = 0.12) -> tuple[int, int, int, int]:
    bw = box.x2 - box.x1
    bh = box.y2 - box.y1
    x1 = max(0, int(box.x1 - bw * frac))
    y1 = max(0, int(box.y1 - bh * frac))
    x2 = min(w, int(box.x2 + bw * frac))
    y2 = min(h, int(box.y2 + bh * frac))
    return x1, y1, x2, y2


def load_metadata(root: Path) -> dict[str, dict[str, Any]]:
    meta_path = root / "metadata.json"
    if not meta_path.exists():
        return {}
    try:
        rows = json.loads(meta_path.read_text(encoding="utf-8"))
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
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def to_boxes(result, names: dict[int, str], label_filter: set[str] | None = None) -> list[Box]:
    out: list[Box] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return out
    for xyxy, conf, cls in zip(boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist()):
        label = str(names.get(int(cls), int(cls)))
        if label_filter is not None and label not in label_filter:
            continue
        out.append(Box(float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3]), float(conf), label))
    return out


def classify_crops(crops: list[Image.Image], processor, model, batch_size: int = 16) -> list[dict[str, float]]:
    outputs: list[dict[str, float]] = []
    labels = {int(k): v for k, v in model.config.id2label.items()} if isinstance(model.config.id2label, dict) else model.config.id2label
    for start in range(0, len(crops), batch_size):
        batch = crops[start : start + batch_size]
        inputs = processor(images=batch, return_tensors="pt")
        with torch.inference_mode():
            logits = model(**inputs).logits
            probs = torch.softmax(logits, dim=-1).cpu()
        for row in probs:
            outputs.append({str(labels[i]): float(row[i]) for i in range(len(row))})
    return outputs


def make_sheet(rows: list[dict[str, Any]], image_root: Path, out_path: Path, accepted: bool) -> None:
    cols, rcount = 4, 4
    cell_w, cell_h = 500, 500
    label_h = 92
    canvas = Image.new("RGB", (cols * cell_w, rcount * cell_h), "white")
    draw = ImageDraw.Draw(canvas)
    f1 = font(22, True)
    f2 = font(16, False)
    for pos, row in enumerate(rows[: cols * rcount]):
        rr, cc = divmod(pos, cols)
        x0, y0 = cc * cell_w, rr * cell_h
        path = image_root / row["source_name"]
        try:
            im = Image.open(path).convert("RGB")
            ann = row.get("garment_box")
            if ann:
                d = ImageDraw.Draw(im)
                x1, y1, x2, y2 = [int(v) for v in ann]
                d.rectangle((x1, y1, x2, y2), outline="lime" if accepted else "red", width=max(3, im.width // 250))
            fitted = ImageOps.contain(im, (cell_w - 8, cell_h - label_h - 8), Image.Resampling.LANCZOS)
            ix = x0 + (cell_w - fitted.width) // 2
            iy = y0 + label_h + (cell_h - label_h - fitted.height) // 2
            canvas.paste(fitted, (ix, iy))
        except Exception:
            pass
        draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + cell_h - 1), outline="black", width=2)
        draw.text((x0 + 8, y0 + 5), f"#{row.get('index','?')} {row.get('decision','')}", fill="black", font=f1)
        score = row.get("tshirt_prob", 0.0)
        margin = row.get("margin", 0.0)
        draw.text((x0 + 8, y0 + 36), f"T={score:.2f} margin={margin:.2f} det={row.get('fashion_conf',0):.2f}", fill="black", font=f2)
        draw.text((x0 + 8, y0 + 60), f"@{row.get('handle','')} · {row.get('city','')}"[:55], fill="black", font=f2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=88, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="source")
    ap.add_argument("--out", default="vision_audit")
    ap.add_argument("--tshirt-threshold", type=float, default=0.48)
    ap.add_argument("--margin-threshold", type=float, default=0.08)
    ap.add_argument("--person-conf", type=float, default=0.25)
    ap.add_argument("--fashion-conf", type=float, default=0.20)
    ap.add_argument("--garment-person-overlap", type=float, default=0.72)
    ap.add_argument("--min-garment-area", type=float, default=0.018)
    args = ap.parse_args()

    root = Path(args.root)
    image_root = root / "images" if (root / "images").exists() else root
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "accepted").mkdir(parents=True, exist_ok=True)
    (out / "rejected_top").mkdir(parents=True, exist_ok=True)
    (out / "contact_sheets").mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(root)
    paths = sorted([p for p in image_root.glob("*.jpg") if p.is_file()])
    if not paths:
        raise SystemExit(f"No JPG images found in {image_root}")

    print(f"images={len(paths)}", flush=True)
    fashion_weights = hf_hub_download(repo_id=FASHION_REPO, filename=FASHION_FILE)
    fashion = YOLO(fashion_weights)
    people = YOLO("yolo11n.pt")
    processor = AutoImageProcessor.from_pretrained(CLASSIFIER_REPO)
    classifier = AutoModelForImageClassification.from_pretrained(CLASSIFIER_REPO)
    classifier.eval()

    # Run both detectors over all files. Ultralytics streams results in source order.
    person_results = list(people.predict([str(p) for p in paths], imgsz=640, conf=args.person_conf, classes=[0], verbose=False, stream=True))
    fashion_results = list(fashion.predict([str(p) for p in paths], imgsz=640, conf=args.fashion_conf, verbose=False, stream=True))

    candidate_rows: list[dict[str, Any]] = []
    candidate_crops: list[Image.Image] = []
    prelim_rejected: list[dict[str, Any]] = []

    for idx, (path, pres, fres) in enumerate(zip(paths, person_results, fashion_results), start=1):
        try:
            im = Image.open(path).convert("RGB")
        except Exception as exc:
            prelim_rejected.append({"index": idx, "source_name": path.name, "decision": "decode_error", "error": repr(exc)})
            continue
        w, h = im.size
        image_area = float(w * h)
        person_boxes = to_boxes(pres, pres.names, {"person"})
        if not person_boxes:
            prelim_rejected.append({"index": idx, "source_name": path.name, "decision": "no_person"})
            continue

        upper_boxes = [b for b in to_boxes(fres, fres.names) if b.label.lower() in {x.lower() for x in UPPER_LABELS}]
        if not upper_boxes:
            prelim_rejected.append({"index": idx, "source_name": path.name, "decision": "no_upper_garment"})
            continue

        plausible: list[tuple[float, Box, float]] = []
        for g in upper_boxes:
            area_ratio = g.area / image_area if image_area else 0.0
            if area_ratio < args.min_garment_area:
                continue
            overlap = max((intersection_over_garment(g, p) for p in person_boxes), default=0.0)
            if overlap < args.garment_person_overlap:
                continue
            # Ranking favors a confident, clearly visible garment that is actually on a person.
            rank = g.conf * math.sqrt(max(area_ratio, 1e-9)) * overlap
            plausible.append((rank, g, overlap))

        if not plausible:
            prelim_rejected.append({"index": idx, "source_name": path.name, "decision": "garment_not_clear_on_person"})
            continue

        plausible.sort(key=lambda x: x[0], reverse=True)
        # Keep at most the two best upper garments in group shots.
        for rank, g, overlap in plausible[:2]:
            crop_box = expand_box(g, w, h, 0.12)
            crop = im.crop(crop_box)
            if crop.width < 80 or crop.height < 80:
                continue
            meta = metadata.get(path.name, {})
            candidate_rows.append({
                "index": int(meta.get("index") or idx),
                "source_name": path.name,
                "city": meta.get("city"),
                "handle": meta.get("handle"),
                "source_url": meta.get("source_url"),
                "timestamp": meta.get("timestamp"),
                "fashion_label": g.label,
                "fashion_conf": g.conf,
                "person_overlap": overlap,
                "garment_box": [g.x1, g.y1, g.x2, g.y2],
                "crop_box": list(crop_box),
            })
            candidate_crops.append(crop)

    print(f"candidate_crops={len(candidate_crops)} prelim_rejected={len(prelim_rejected)}", flush=True)
    probs = classify_crops(candidate_crops, processor, classifier, batch_size=16)

    # Multiple candidate garments can come from one image. Keep the strongest T-shirt decision per image.
    best_by_image: dict[str, dict[str, Any]] = {}
    for row, prob in zip(candidate_rows, probs):
        tshirt = float(prob.get("T-shirt", 0.0))
        negatives = sorted(((name, float(prob.get(name, 0.0))) for name in NEGATIVE_UPPER), key=lambda x: x[1], reverse=True)
        best_neg_name, best_neg_prob = negatives[0] if negatives else ("", 0.0)
        margin = tshirt - best_neg_prob
        top_label, top_prob = max(prob.items(), key=lambda kv: kv[1])
        combined = tshirt * float(row["fashion_conf"]) * float(row["person_overlap"])
        row.update({
            "classifier_top": top_label,
            "classifier_top_prob": top_prob,
            "tshirt_prob": tshirt,
            "best_negative": best_neg_name,
            "best_negative_prob": best_neg_prob,
            "margin": margin,
            "combined_score": combined,
            "classifier_probs": prob,
        })
        prev = best_by_image.get(row["source_name"])
        if prev is None or row["combined_score"] > prev["combined_score"]:
            best_by_image[row["source_name"]] = row

    accepted: list[dict[str, Any]] = []
    rejected_scored: list[dict[str, Any]] = []
    for row in best_by_image.values():
        # Conservative: T-shirt must be the classifier's top label, with an absolute probability
        # and a meaningful margin over the most likely long-sleeve/formal alternative.
        ok = (
            row["classifier_top"] == "T-shirt"
            and row["tshirt_prob"] >= args.tshirt_threshold
            and row["margin"] >= args.margin_threshold
        )
        row["decision"] = "accepted_tshirt" if ok else "classifier_reject"
        (accepted if ok else rejected_scored).append(row)

    accepted.sort(key=lambda r: (r.get("timestamp") or 0, r["combined_score"]), reverse=True)
    rejected_scored.sort(key=lambda r: r["tshirt_prob"], reverse=True)

    for n, row in enumerate(accepted, start=1):
        src = image_root / row["source_name"]
        shutil.copy2(src, out / "accepted" / f"{n:04d}_{row['source_name']}")
    for n, row in enumerate(rejected_scored[:96], start=1):
        src = image_root / row["source_name"]
        shutil.copy2(src, out / "rejected_top" / f"{n:04d}_{row['source_name']}")

    # Annotated contact sheets are the human QA layer.
    for start in range(0, len(accepted), 16):
        make_sheet(accepted[start : start + 16], image_root, out / "contact_sheets" / f"accepted_{start//16+1:03d}.jpg", True)
    for start in range(0, min(len(rejected_scored), 96), 16):
        make_sheet(rejected_scored[start : start + 16], image_root, out / "contact_sheets" / f"borderline_rejected_{start//16+1:03d}.jpg", False)

    all_rows = accepted + rejected_scored
    fields = [
        "index", "source_name", "decision", "city", "handle", "source_url", "timestamp",
        "fashion_label", "fashion_conf", "person_overlap", "classifier_top", "classifier_top_prob",
        "tshirt_prob", "best_negative", "best_negative_prob", "margin", "combined_score",
        "garment_box", "crop_box",
    ]
    with (out / "vision_results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    (out / "vision_results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "preliminary_rejections.json").write_text(json.dumps(prelim_rejected, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "input_images": len(paths),
        "candidate_garment_crops": len(candidate_crops),
        "images_with_scored_upper_garment": len(best_by_image),
        "accepted_tshirts": len(accepted),
        "classifier_rejected": len(rejected_scored),
        "preliminary_rejected": len(prelim_rejected),
        "thresholds": {
            "tshirt_probability": args.tshirt_threshold,
            "margin": args.margin_threshold,
            "person_conf": args.person_conf,
            "fashion_conf": args.fashion_conf,
            "garment_person_overlap": args.garment_person_overlap,
            "min_garment_area_ratio": args.min_garment_area,
        },
        "method": "COCO person detection + FashionPedia garment detection + independent ViT clothing classification. Conservative acceptance requires a visibly worn upper garment classified as T-shirt with probability and margin thresholds.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
