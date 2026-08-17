from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageDraw, ImageFont, ImageOps
from transformers import AutoImageProcessor, AutoModelForImageClassification
from ultralytics import YOLO

CLASSIFIER_REPO = "dima806/clothes_image_detection"
NEGATIVE_UPPER = {"Polo", "Shirt", "Hoodie", "Sweater", "Jacket", "Blazer", "Coat", "Sports Jacket", "Dresses"}


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


def clamp_box(box: tuple[float, float, float, float], w: int, h: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    return (
        max(0, min(w - 1, int(x1))),
        max(0, min(h - 1, int(y1))),
        max(1, min(w, int(x2))),
        max(1, min(h, int(y2))),
    )


def torso_from_pose(person_box: list[float], xy: list[list[float]] | None, conf: list[float] | None, w: int, h: int) -> tuple[tuple[int, int, int, int], str]:
    px1, py1, px2, py2 = person_box
    pw, ph = max(1.0, px2 - px1), max(1.0, py2 - py1)

    def kp_ok(i: int) -> bool:
        if xy is None or i >= len(xy):
            return False
        if conf is not None and i < len(conf) and float(conf[i]) < 0.25:
            return False
        return float(xy[i][0]) > 1 and float(xy[i][1]) > 1

    if all(kp_ok(i) for i in (5, 6, 11, 12)):
        pts = [xy[i] for i in (5, 6, 11, 12)]
        xs = [float(p[0]) for p in pts]
        shoulder_y = min(float(xy[5][1]), float(xy[6][1]))
        hip_y = max(float(xy[11][1]), float(xy[12][1]))
        torso_h = max(20.0, hip_y - shoulder_y)
        torso_w = max(20.0, max(xs) - min(xs))
        box = (
            min(xs) - torso_w * 0.38,
            shoulder_y - torso_h * 0.16,
            max(xs) + torso_w * 0.38,
            hip_y - torso_h * 0.03,
        )
        return clamp_box(box, w, h), "pose_shoulders_hips"

    if kp_ok(5) and kp_ok(6):
        sx1, sx2 = sorted([float(xy[5][0]), float(xy[6][0])])
        sy = min(float(xy[5][1]), float(xy[6][1]))
        sw = max(20.0, sx2 - sx1)
        box = (sx1 - sw * 0.48, sy - ph * 0.04, sx2 + sw * 0.48, sy + ph * 0.43)
        return clamp_box(box, w, h), "pose_shoulders"

    box = (px1 + pw * 0.08, py1 + ph * 0.10, px2 - pw * 0.08, py1 + ph * 0.60)
    return clamp_box(box, w, h), "person_geometry"


def classify_crops(crops: list[Image.Image], processor, model, batch_size: int = 12) -> list[dict[str, float]]:
    outputs: list[dict[str, float]] = []
    labels = {int(k): v for k, v in model.config.id2label.items()} if isinstance(model.config.id2label, dict) else model.config.id2label
    for start in range(0, len(crops), batch_size):
        batch = crops[start : start + batch_size]
        inputs = processor(images=batch, return_tensors="pt")
        with torch.inference_mode():
            probs = torch.softmax(model(**inputs).logits, dim=-1).cpu()
        for row in probs:
            outputs.append({str(labels[i]): float(row[i]) for i in range(len(row))})
    return outputs


def make_sheet(rows: list[dict[str, Any]], image_root: Path, out_path: Path, accepted: bool) -> None:
    cols, rcount = 4, 4
    cell_w, cell_h, label_h = 500, 500, 92
    canvas = Image.new("RGB", (cols * cell_w, rcount * cell_h), "white")
    draw = ImageDraw.Draw(canvas)
    f1, f2 = font(21, True), font(15, False)
    for pos, row in enumerate(rows[: cols * rcount]):
        rr, cc = divmod(pos, cols)
        x0, y0 = cc * cell_w, rr * cell_h
        path = image_root / row["source_name"]
        try:
            im = Image.open(path).convert("RGB")
            ann = row.get("torso_box")
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
        draw.text((x0 + 8, y0 + 35), f"T={row.get('tshirt_prob',0):.2f} margin={row.get('margin',0):.2f} person={row.get('person_conf',0):.2f}", fill="black", font=f2)
        draw.text((x0 + 8, y0 + 60), f"{row.get('crop_method','')} · @{row.get('handle','')}"[:57], fill="black", font=f2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=88, optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="source")
    ap.add_argument("--out", default="vision_audit")
    ap.add_argument("--tshirt-threshold", type=float, default=0.50)
    ap.add_argument("--margin-threshold", type=float, default=0.10)
    ap.add_argument("--person-conf", type=float, default=0.35)
    ap.add_argument("--min-person-area", type=float, default=0.07)
    ap.add_argument("--lightweight", action="store_true", help="Write only result metadata; skip image copies/contact sheets")
    args = ap.parse_args()

    root = Path(args.root)
    image_root = root / "images" if (root / "images").exists() else root
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.lightweight:
        for d in ("accepted", "rejected_top", "contact_sheets", "crops"):
            (out / d).mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(root)
    paths = sorted([p for p in image_root.glob("*.jpg") if p.is_file()])
    if not paths:
        (out / "vision_results.json").write_text("[]", encoding="utf-8")
        (out / "preliminary_rejections.json").write_text("[]", encoding="utf-8")
        (out / "summary.json").write_text(json.dumps({"input_images": 0, "accepted_tshirts": 0}), encoding="utf-8")
        print("No JPG images found; wrote empty results", flush=True)
        return

    print(f"images={len(paths)}", flush=True)
    pose = YOLO("yolo11n-pose.pt")
    processor = AutoImageProcessor.from_pretrained(CLASSIFIER_REPO)
    classifier = AutoModelForImageClassification.from_pretrained(CLASSIFIER_REPO)
    classifier.eval()

    candidate_rows: list[dict[str, Any]] = []
    candidate_crops: list[Image.Image] = []
    prelim_rejected: list[dict[str, Any]] = []

    # Stream pose results instead of materializing all result tensors at once. This keeps memory bounded
    # and prevents the hosted runner shutdown seen when processing hundreds of photos in one list().
    result_stream = pose.predict([str(p) for p in paths], imgsz=512, conf=args.person_conf, verbose=False, stream=True, device="cpu")
    for seq, (path, res) in enumerate(zip(paths, result_stream), start=1):
        try:
            im = Image.open(path).convert("RGB")
        except Exception as exc:
            prelim_rejected.append({"source_name": path.name, "decision": "decode_error", "error": repr(exc)})
            continue
        w, h = im.size
        image_area = float(w * h)
        boxes = getattr(res, "boxes", None)
        keypoints = getattr(res, "keypoints", None)
        if boxes is None or len(boxes) == 0:
            prelim_rejected.append({"source_name": path.name, "decision": "no_person"})
            continue

        xyxys = boxes.xyxy.cpu().tolist()
        bconfs = boxes.conf.cpu().tolist()
        kxy = keypoints.xy.cpu().tolist() if keypoints is not None and keypoints.xy is not None else []
        kconf = keypoints.conf.cpu().tolist() if keypoints is not None and keypoints.conf is not None else []

        people: list[tuple[float, int]] = []
        for i, b in enumerate(xyxys):
            area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
            ratio = area / image_area if image_area else 0.0
            if ratio >= args.min_person_area:
                people.append((float(bconfs[i]) * math.sqrt(ratio), i))
        people.sort(reverse=True)
        if not people:
            prelim_rejected.append({"source_name": path.name, "decision": "person_too_small"})
            continue

        meta = metadata.get(path.name, {})
        for _, i in people[:2]:
            xy = kxy[i] if i < len(kxy) else None
            cf = kconf[i] if i < len(kconf) else None
            torso_box, method = torso_from_pose(xyxys[i], xy, cf, w, h)
            x1, y1, x2, y2 = torso_box
            if x2 - x1 < 70 or y2 - y1 < 70:
                continue
            candidate_rows.append({
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
                "person_conf": float(bconfs[i]),
                "person_box": [float(v) for v in xyxys[i]],
                "torso_box": list(torso_box),
                "crop_method": method,
            })
            candidate_crops.append(im.crop(torso_box))

    print(f"candidate_torsos={len(candidate_crops)} prelim_rejected={len(prelim_rejected)}", flush=True)
    probs = classify_crops(candidate_crops, processor, classifier)
    candidate_crops.clear()

    best_by_image: dict[str, dict[str, Any]] = {}
    for row, prob in zip(candidate_rows, probs):
        tshirt = float(prob.get("T-shirt", 0.0))
        negatives = sorted(((name, float(prob.get(name, 0.0))) for name in NEGATIVE_UPPER), key=lambda x: x[1], reverse=True)
        best_neg_name, best_neg_prob = negatives[0] if negatives else ("", 0.0)
        margin = tshirt - best_neg_prob
        top_label, top_prob = max(prob.items(), key=lambda kv: kv[1])
        combined = tshirt * (0.7 + 0.3 * float(row["person_conf"]))
        row.update({
            "classifier_top": top_label,
            "classifier_top_prob": float(top_prob),
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
        ok = row["classifier_top"] == "T-shirt" and row["tshirt_prob"] >= args.tshirt_threshold and row["margin"] >= args.margin_threshold
        row["decision"] = "accepted_tshirt" if ok else "classifier_reject"
        (accepted if ok else rejected_scored).append(row)

    accepted.sort(key=lambda r: ((r.get("timestamp") or 0), r["combined_score"]), reverse=True)
    rejected_scored.sort(key=lambda r: r["tshirt_prob"], reverse=True)

    if not args.lightweight:
        for n, row in enumerate(accepted, start=1):
            src = image_root / row["source_name"]
            shutil.copy2(src, out / "accepted" / f"{n:04d}_{row['source_name']}")
            try:
                im = Image.open(src).convert("RGB")
                crop = im.crop(tuple(int(v) for v in row["torso_box"]))
                crop.save(out / "crops" / f"{n:04d}_{row['source_name']}", "JPEG", quality=90)
            except Exception:
                pass
        for n, row in enumerate(rejected_scored[:96], start=1):
            shutil.copy2(image_root / row["source_name"], out / "rejected_top" / f"{n:04d}_{row['source_name']}")
        for start in range(0, len(accepted), 16):
            make_sheet(accepted[start:start+16], image_root, out / "contact_sheets" / f"accepted_{start//16+1:03d}.jpg", True)
        for start in range(0, min(len(rejected_scored), 96), 16):
            make_sheet(rejected_scored[start:start+16], image_root, out / "contact_sheets" / f"borderline_rejected_{start//16+1:03d}.jpg", False)

    fields = [
        "index", "source_name", "decision", "city", "handle", "source_url", "timestamp", "parent_shortcode", "shortcode", "child_index", "is_carousel_child",
        "person_conf", "crop_method", "classifier_top", "classifier_top_prob", "tshirt_prob", "best_negative", "best_negative_prob", "margin", "combined_score", "person_box", "torso_box",
    ]
    all_rows = accepted + rejected_scored
    with (out / "vision_results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    (out / "vision_results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "preliminary_rejections.json").write_text(json.dumps(prelim_rejected, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "input_images": len(paths),
        "candidate_torsos": len(candidate_rows),
        "images_with_scored_torso": len(best_by_image),
        "accepted_tshirts": len(accepted),
        "classifier_rejected": len(rejected_scored),
        "preliminary_rejected": len(prelim_rejected),
        "thresholds": {
            "tshirt_probability": args.tshirt_threshold,
            "margin": args.margin_threshold,
            "person_conf": args.person_conf,
            "min_person_area_ratio": args.min_person_area,
        },
        "method": "Streaming YOLO11 pose person localization -> pose-derived torso crop -> independent ViT clothing classifier. Acceptance requires T-shirt as top class plus probability and margin thresholds.",
        "lightweight": args.lightweight,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
