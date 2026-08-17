from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


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


def make_sheets(rows: list[dict[str, Any]], final: Path) -> int:
    out = final / "contact_sheets"
    out.mkdir(parents=True, exist_ok=True)
    cols, rows_per = 4, 4
    cw, ch, label_h = 480, 480, 76
    f1, f2 = font(22, True), font(15, False)
    sheet_count = 0
    for start in range(0, len(rows), cols * rows_per):
        canvas = Image.new("RGB", (cw * cols, ch * rows_per), "white")
        draw = ImageDraw.Draw(canvas)
        for pos, row in enumerate(rows[start:start + cols * rows_per]):
            rr, cc = divmod(pos, cols)
            x0, y0 = cc * cw, rr * ch
            try:
                im = Image.open(final / row["selected_file"]).convert("RGB")
                fitted = ImageOps.contain(im, (cw - 8, ch - label_h - 8), Image.Resampling.LANCZOS)
                ix = x0 + (cw - fitted.width) // 2
                iy = y0 + label_h + (ch - label_h - fitted.height) // 2
                canvas.paste(fitted, (ix, iy))
            except Exception:
                pass
            draw.rectangle((x0, y0, x0 + cw - 1, y0 + ch - 1), outline="black", width=2)
            draw.text((x0 + 7, y0 + 5), f"#{row['index']:04d} T={row.get('tshirt_prob',0):.2f}", fill="black", font=f1)
            draw.text((x0 + 7, y0 + 35), f"{row.get('city','')} · @{row.get('handle','')}"[:52], fill="black", font=f2)
            draw.text((x0 + 7, y0 + 55), f"post {row.get('parent_shortcode') or row.get('shortcode') or ''}"[:55], fill="black", font=f2)
        sheet_count += 1
        canvas.save(out / f"sheet_{sheet_count:03d}.jpg", "JPEG", quality=88, optimize=True)
    return sheet_count


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate_root", nargs="?", default="candidate_pool")
    ap.add_argument("vision_root", nargs="?", default="vision_audit")
    ap.add_argument("--out", default="final_tshirts")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    cand = Path(args.candidate_root)
    vision = Path(args.vision_root)
    final = Path(args.out)
    if final.exists():
        shutil.rmtree(final)
    images_out = final / "images"
    images_out.mkdir(parents=True, exist_ok=True)

    candidate_meta = json.loads((cand / "metadata.json").read_text(encoding="utf-8"))
    by_name = {Path(str(r.get("selected_file") or "")).name: r for r in candidate_meta if isinstance(r, dict)}
    vision_rows = json.loads((vision / "vision_results.json").read_text(encoding="utf-8"))
    accepted = [r for r in vision_rows if isinstance(r, dict) and r.get("decision") == "accepted_tshirt"]

    # Newest-first is primary; classification confidence only breaks ties.
    accepted.sort(key=lambda r: (float(r.get("timestamp") or 0), float(r.get("tshirt_prob") or 0)), reverse=True)
    accepted = accepted[: args.limit]

    output: list[dict[str, Any]] = []
    for idx, vr in enumerate(accepted, start=1):
        source_name = str(vr.get("source_name") or "")
        meta = dict(by_name.get(source_name, {}))
        src = cand / "images" / source_name
        if not src.exists():
            continue
        dst = images_out / f"{idx:04d}.jpg"
        shutil.copy2(src, dst)
        meta.update({
            "index": idx,
            "selected_file": str(dst.relative_to(final)),
            "vision_source_name": source_name,
            "tshirt_prob": vr.get("tshirt_prob"),
            "classifier_top": vr.get("classifier_top"),
            "classifier_top_prob": vr.get("classifier_top_prob"),
            "best_negative": vr.get("best_negative"),
            "best_negative_prob": vr.get("best_negative_prob"),
            "margin": vr.get("margin"),
            "person_conf": vr.get("person_conf"),
            "torso_box": vr.get("torso_box"),
            "vision_decision": vr.get("decision"),
        })
        output.append(meta)

    (final / "metadata.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = sorted({k for row in output for k in row.keys()}) if output else ["index", "selected_file"]
    with (final / "metadata.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)

    sheets = make_sheets(output, final)
    summary = {
        "accepted_by_vision_before_limit": len([r for r in vision_rows if isinstance(r, dict) and r.get("decision") == "accepted_tshirt"]),
        "final_selected": len(output),
        "requested": args.limit,
        "target_reached": len(output) >= args.limit,
        "contact_sheets": sheets,
        "city_counts": dict(Counter(str(x.get("city") or "unknown") for x in output)),
        "handle_counts": dict(Counter(str(x.get("handle") or "unknown") for x in output).most_common()),
        "carousel_children": sum(1 for x in output if x.get("is_carousel_child")),
        "method": "Only images accepted by the worn-T-shirt vision gate are eligible. Final ordering is newest-first; no general-fashion image can be selected before the T-shirt gate.",
        "qa_required": True,
    }
    (final / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
