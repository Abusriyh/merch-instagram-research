from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


def parse_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def find_meta_files(root: Path) -> list[Path]:
    return sorted(root.rglob("batch_meta_*.json"))


def load_items(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    for meta_path in find_meta_files(root):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            profiles.append({"meta_file": str(meta_path), "ok": False, "error": f"read:{exc}"})
            continue
        profile_row = {
            "handle": data.get("handle"),
            "city": data.get("city"),
            "kind": data.get("kind"),
            "ok": bool(data.get("ok")),
            "downloaded": len(data.get("items") or []),
            "errors": data.get("errors") or [],
            "profile": data.get("profile") or {},
            "meta_file": str(meta_path),
        }
        profiles.append(profile_row)
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            rel = raw.get("file")
            if not rel:
                continue
            # Artifact download may flatten the original batch/ prefix depending on merge mode.
            candidates = [
                root / rel,
                root / str(rel).removeprefix("batch/"),
                meta_path.parent / Path(str(rel)).name,
            ]
            src = next((p for p in candidates if p.exists()), None)
            if src is None:
                continue
            row = dict(raw)
            row["_src_file"] = str(src)
            row["_timestamp"] = parse_timestamp(row.get("timestamp"))
            items.append(row)
    return items, profiles


def dedupe_and_sort(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Prefer content hash; shortcode is a secondary guard.
    seen_hash: set[str] = set()
    seen_shortcode: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in sorted(items, key=lambda x: x.get("_timestamp", 0.0), reverse=True):
        sha = str(row.get("sha256") or "")
        shortcode = str(row.get("shortcode") or "")
        if sha and sha in seen_hash:
            continue
        if shortcode and shortcode in seen_shortcode:
            continue
        if sha:
            seen_hash.add(sha)
        if shortcode:
            seen_shortcode.add(shortcode)
        out.append(row)
    return out


def copy_selected(selected: list[dict[str, Any]], final_dir: Path) -> list[dict[str, Any]]:
    images_dir = final_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    output: list[dict[str, Any]] = []
    for idx, row in enumerate(selected, start=1):
        dst = images_dir / f"{idx:04d}.jpg"
        shutil.copy2(row["_src_file"], dst)
        public = {k: v for k, v in row.items() if not k.startswith("_")}
        public["index"] = idx
        public["selected_file"] = str(dst.relative_to(final_dir))
        public["sort_timestamp"] = row.get("_timestamp", 0.0)
        output.append(public)
    return output


def text_font(size: int = 24):
    # DejaVu is normally available on Ubuntu; Pillow default is a safe fallback.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_contact_sheets(metadata: list[dict[str, Any]], final_dir: Path) -> int:
    sheet_dir = final_dir / "contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    cols, rows = 4, 4
    cell_w, cell_h = 450, 450
    label_h = 62
    sheet_w, sheet_h = cols * cell_w, rows * cell_h
    font_big = text_font(28)
    font_small = text_font(18)
    count = 0

    for start in range(0, len(metadata), cols * rows):
        batch = metadata[start : start + cols * rows]
        canvas = Image.new("RGB", (sheet_w, sheet_h), "white")
        draw = ImageDraw.Draw(canvas)
        for pos, row in enumerate(batch):
            r, c = divmod(pos, cols)
            x0, y0 = c * cell_w, r * cell_h
            img_path = final_dir / row["selected_file"]
            try:
                im = Image.open(img_path).convert("RGB")
                # Leave label strip at top of each cell.
                fitted = ImageOps.contain(im, (cell_w - 10, cell_h - label_h - 10), Image.Resampling.LANCZOS)
                ix = x0 + (cell_w - fitted.width) // 2
                iy = y0 + label_h + (cell_h - label_h - fitted.height) // 2
                canvas.paste(fitted, (ix, iy))
            except Exception:
                pass
            draw.rectangle((x0, y0, x0 + cell_w - 1, y0 + cell_h - 1), outline="black", width=2)
            idx = int(row["index"])
            city = str(row.get("city") or "")
            handle = str(row.get("handle") or "")
            draw.text((x0 + 8, y0 + 4), f"#{idx:04d}", fill="black", font=font_big)
            label = f"{city} · @{handle}"
            draw.text((x0 + 112, y0 + 12), label[:38], fill="black", font=font_small)
        count += 1
        canvas.save(sheet_dir / f"sheet_{count:03d}.jpg", "JPEG", quality=88, optimize=True)
    return count


def write_csv(metadata: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "index", "city", "handle", "kind", "shortcode", "source_url", "timestamp",
        "sort_timestamp", "post_type", "is_video", "like_count", "comment_count",
        "caption", "selected_file", "width", "height", "sha256", "media_source_kind",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metadata)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="collected")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    root = Path(args.root)
    final_dir = Path("final")
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)

    items, profiles = load_items(root)
    unique = dedupe_and_sort(items)
    selected = unique[: args.limit]
    metadata = copy_selected(selected, final_dir)
    sheets = make_contact_sheets(metadata, final_dir)

    (final_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(metadata, final_dir / "metadata.csv")

    city_counts = Counter(str(x.get("city") or "unknown") for x in metadata)
    handle_counts = Counter(str(x.get("handle") or "unknown") for x in metadata)
    type_counts = Counter(str(x.get("post_type") or "unknown") for x in metadata)
    profile_status = Counter(
        "usable" if p.get("ok") and p.get("downloaded", 0) else "unusable" for p in profiles
    )
    summary = {
        "meta_files_found": len(profiles),
        "profile_status": dict(profile_status),
        "raw_downloaded_items": len(items),
        "unique_items": len(unique),
        "selected_items": len(metadata),
        "requested_limit": args.limit,
        "contact_sheets": sheets,
        "city_counts": dict(city_counts),
        "handle_counts": dict(handle_counts.most_common()),
        "post_type_counts": dict(type_counts),
        "profiles": profiles,
        "note": "Geography-balanced, fashion/street-style-enriched public Instagram panel; not a random sample of all US Instagram uploads.",
    }
    (final_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "raw": len(items),
        "unique": len(unique),
        "selected": len(metadata),
        "sheets": sheets,
        "cities": dict(city_counts),
        "profiles": dict(profile_status),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
