from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="vision_parts")
    ap.add_argument("--out", default="combined_vision")
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for p in sorted(root.rglob("vision_results.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in data if isinstance(data, list) else []:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("handle") or ""), str(row.get("shortcode") or row.get("source_name") or ""), str(row.get("child_index") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    for p in sorted(root.rglob("summary.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["source_summary"] = str(p)
                summaries.append(data)
        except Exception:
            pass

    accepted = [r for r in rows if r.get("decision") == "accepted_tshirt"]
    rejected = [r for r in rows if r.get("decision") != "accepted_tshirt"]
    accepted.sort(key=lambda r: (float(r.get("timestamp") or 0), float(r.get("tshirt_prob") or 0)), reverse=True)
    rejected.sort(key=lambda r: float(r.get("tshirt_prob") or 0), reverse=True)
    combined = accepted + rejected

    (out / "vision_results.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "parts_found": len(summaries),
        "scored_images": len(rows),
        "accepted_tshirts": len(accepted),
        "rejected_scored": len(rejected),
        "part_summaries": summaries,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
