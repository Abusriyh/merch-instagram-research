from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path

from merge_batches import dedupe, load_items, ordered_items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="collected")
    ap.add_argument("--out", default="candidate_pool")
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)

    items, profiles = load_items(root)
    ordered, ordering_method, ts_coverage = ordered_items(items)
    unique = dedupe(ordered)

    metadata = []
    for idx, row in enumerate(unique, start=1):
        dst = images / f"{idx:05d}.jpg"
        shutil.copy2(row["_src_file"], dst)
        public = {k: v for k, v in row.items() if not k.startswith("_")}
        public["index"] = idx
        public["selected_file"] = str(dst.relative_to(out))
        public["sort_timestamp"] = row.get("_timestamp", 0.0)
        metadata.append(public)

    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = sorted({k for row in metadata for k in row.keys()}) if metadata else ["index", "selected_file"]
    with (out / "metadata.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metadata)

    profile_status = Counter("usable" if p.get("ok") and p.get("downloaded", 0) else "unusable" for p in profiles)
    summary = {
        "meta_files_found": len(profiles),
        "profile_status": dict(profile_status),
        "raw_downloaded_photos": len(items),
        "unique_candidate_photos": len(unique),
        "ordering_method": ordering_method,
        "valid_timestamp_coverage": round(ts_coverage, 4),
        "city_counts": dict(Counter(str(x.get("city") or "unknown") for x in metadata)),
        "handle_counts": dict(Counter(str(x.get("handle") or "unknown") for x in metadata).most_common()),
        "carousel_child_photos": sum(1 for x in metadata if x.get("is_carousel_child")),
        "note": "This is a candidate pool only. No photo becomes part of the final research sample until it passes the worn-T-shirt vision filter and QA.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
