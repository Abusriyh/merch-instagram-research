from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import torch
from PIL import Image, ImageDraw, ImageFont, ImageOps
from transformers import AutoImageProcessor, AutoModelForObjectDetection, AutoModelForZeroShotImageClassification, AutoProcessor

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
CANDIDATE_FASHION = {"top, t-shirt, sweatshirt", "shirt, blouse", "sweater", "cardigan", "jacket", "vest", "coat", "dress", "jumpsuit"}


def font(size: int, bold: bool = False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(p, size=size)
    except Exception:
        return ImageFont.load_default()


def expand(box, w, h, f=0.10):
    x1, y1, x2, y2 = box
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    return (max(0, int(x1 - bw * f)), max(0, int(y1 - bh * f)), min(w, int(x2 + bw * f)), min(h, int(y2 + bh * f)))


def main():
    root = Path("source")
    out = Path("garment_probe")
    out.mkdir(exist_ok=True)
    imgs = sorted(root.rglob("*.jpg"))[:120]
    print("images", len(imgs), flush=True)

    fashion_processor = AutoImageProcessor.from_pretrained(FASHION_MODEL)
    fashion = AutoModelForObjectDetection.from_pretrained(FASHION_MODEL)
    fashion.eval()
    clip_processor = AutoProcessor.from_pretrained(CLIP_MODEL)
    clip = AutoModelForZeroShotImageClassification.from_pretrained(CLIP_MODEL)
    clip.eval()

    id2label = fashion.config.id2label
    if isinstance(id2label, dict):
        id2label = {int(k): v for k, v in id2label.items()}

    refs: list[tuple] = []
    crops: list[Image.Image] = []

    # Object detection is intentionally per-image on CPU for bounded memory.
    for n, p in enumerate(imgs, start=1):
        im = Image.open(p).convert("RGB")
        w, h = im.size
        area = float(w * h)
        inputs = fashion_processor(images=im, return_tensors="pt")
        with torch.inference_mode():
            outputs = fashion(**inputs)
        target_sizes = torch.tensor([[h, w]])
        detections = fashion_processor.post_process_object_detection(outputs, threshold=0.18, target_sizes=target_sizes)[0]
        for score, label_id, box in zip(detections["scores"], detections["labels"], detections["boxes"]):
            label = str(id2label[int(label_id)])
            if label not in CANDIDATE_FASHION:
                continue
            x1, y1, x2, y2 = [float(v) for v in box.tolist()]
            ar = max(0, x2 - x1) * max(0, y2 - y1) / max(1.0, area)
            if ar < 0.012:
                continue
            cb = expand((x1, y1, x2, y2), w, h, 0.10)
            crop = im.crop(cb)
            if crop.width < 70 or crop.height < 70:
                continue
            refs.append((p, label, float(score), [x1, y1, x2, y2], list(cb)))
            crops.append(crop)
        if n % 10 == 0:
            print(f"detected {n}/{len(imgs)} images, crops={len(crops)}", flush=True)

    print("garment crops", len(crops), flush=True)
    rows: list[dict[str, Any]] = []
    for start in range(0, len(crops), 12):
        batch = crops[start:start + 12]
        inputs = clip_processor(text=PROMPTS, images=batch, return_tensors="pt", padding=True)
        with torch.inference_mode():
            probs = torch.softmax(clip(**inputs).logits_per_image, dim=1).cpu().tolist()
        for k, pr in enumerate(probs):
            p, label, det, box, cb = refs[start + k]
            order = sorted(range(len(pr)), key=lambda i: pr[i], reverse=True)
            top, second = order[0], order[1]
            rows.append({
                "source": str(p),
                "fashion_label": label,
                "fashion_conf": det,
                "garment_box": box,
                "crop_box": cb,
                "clip_top": PROMPT_NAMES[top],
                "clip_top_prob": pr[top],
                "clip_second": PROMPT_NAMES[second],
                "clip_second_prob": pr[second],
                "tshirt_prob": pr[0],
                "tshirt_margin": pr[0] - max(pr[1:]),
                "all_probs": {PROMPT_NAMES[i]: pr[i] for i in range(len(pr))},
            })

    # Keep the garment that looks most T-shirt-like per source image.
    best: dict[str, tuple[float, dict[str, Any]]] = {}
    for r in rows:
        key = r["source"]
        detector_bonus = 0.08 if r["fashion_label"] == "top, t-shirt, sweatshirt" else 0.0
        score = r["tshirt_prob"] + detector_bonus
        if key not in best or score > best[key][0]:
            best[key] = (score, r)
    ranked = [v[1] for v in best.values()]
    ranked.sort(key=lambda r: (r["clip_top"] == "tshirt", r["tshirt_margin"], r["tshirt_prob"]), reverse=True)

    (out / "results.json").write_text(json.dumps(ranked, indent=2), encoding="utf-8")
    summary = {
        "input_images": len(imgs),
        "garment_crops": len(crops),
        "images_with_upper_garment": len(ranked),
        "clip_top_counts": dict(Counter(r["clip_top"] for r in ranked)),
        "fashion_label_counts": dict(Counter(r["fashion_label"] for r in ranked)),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)

    chosen = ranked[:32]
    cw, ch, cols, rowsn, lh = 520, 520, 4, 8, 105
    canvas = Image.new("RGB", (cw * cols, ch * rowsn), "white")
    d = ImageDraw.Draw(canvas)
    f1, f2 = font(19, True), font(14)
    for n, r in enumerate(chosen):
        rr, cc = divmod(n, cols)
        x0, y0 = cc * cw, rr * ch
        im = Image.open(r["source"]).convert("RGB")
        x1, y1, x2, y2 = [int(x) for x in r["garment_box"]]
        ImageDraw.Draw(im).rectangle((x1, y1, x2, y2), outline="lime" if r["clip_top"] == "tshirt" else "red", width=max(3, im.width // 250))
        fit = ImageOps.contain(im, (cw - 8, ch - lh - 8), Image.Resampling.LANCZOS)
        canvas.paste(fit, (x0 + (cw - fit.width) // 2, y0 + lh + (ch - lh - fit.height) // 2))
        d.rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline="black", width=2)
        d.text((x0 + 6, y0 + 5), f"{n + 1}. {r['clip_top']} T={r['tshirt_prob']:.2f} m={r['tshirt_margin']:.2f}", fill="black", font=f1)
        d.text((x0 + 6, y0 + 36), f"2nd={r['clip_second']} {r['clip_second_prob']:.2f}", fill="black", font=f2)
        d.text((x0 + 6, y0 + 59), f"FP={r['fashion_label']} {r['fashion_conf']:.2f}", fill="black", font=f2)
        d.text((x0 + 6, y0 + 81), Path(r["source"]).name[:55], fill="black", font=f2)
    canvas.save(out / "top32.jpg", "JPEG", quality=88, optimize=True)


if __name__ == "__main__":
    main()
