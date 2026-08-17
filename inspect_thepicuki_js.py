from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curl_cffi import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
BASE = "https://thepicuki.com"
HANDLE = "watchingnewyork"
API = BASE + "/api/instagram.php"


def req(method: str = "GET", params: dict[str, Any] | None = None, data: dict[str, Any] | None = None):
    return requests.request(
        method,
        API,
        params=params,
        data=data,
        headers={
            "User-Agent": UA,
            "Referer": BASE + "/",
            "Accept": "application/json,*/*",
            "X-Requested-With": "XMLHttpRequest",
        },
        impersonate="chrome",
        timeout=18,
        allow_redirects=True,
    )


def shortcodes(obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return []
    posts = obj.get("posts") or []
    out = []
    for p in posts:
        if isinstance(p, dict) and p.get("shortcode"):
            out.append(str(p["shortcode"]))
    return out


def interesting_keys(obj: Any, path: str = "$") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    words = ("next", "cursor", "max", "page", "end", "offset", "more", "has_")
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{path}.{k}"
            if any(w in str(k).lower() for w in words):
                vv = v
                if isinstance(vv, (dict, list)):
                    vv = str(vv)[:600]
                out.append({"path": kp, "value": vv})
            out.extend(interesting_keys(v, kp))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            out.extend(interesting_keys(v, f"{path}[{i}]"))
    return out


def summarize_response(label: str, r, baseline: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "label": label,
        "status": r.status_code,
        "final_url": str(r.url),
        "bytes": len(r.content),
        "content_type": r.headers.get("content-type"),
        "body_sha256": hashlib.sha256(r.content).hexdigest(),
    }
    try:
        d = r.json()
        codes = shortcodes(d)
        row.update({
            "success": d.get("success") if isinstance(d, dict) else None,
            "top_keys": list(d.keys()) if isinstance(d, dict) else [],
            "posts_len": len(d.get("posts") or []) if isinstance(d, dict) else 0,
            "shortcodes": codes,
            "same_as_baseline": bool(baseline) and codes == baseline,
            "overlap_with_baseline": len(set(codes) & set(baseline)),
            "interesting": interesting_keys(d)[:100],
        })
    except Exception as exc:
        row["json_error"] = repr(exc)
        row["head"] = r.text[:500]
    return row


def main():
    report: dict[str, Any] = {"api": API, "handle": HANDLE, "baseline": {}, "tests": [], "assets": []}

    # Baseline — this endpoint is already proven to return real Instagram media.
    try:
        r = req(params={"username": HANDLE})
        base_data = r.json() if r.status_code == 200 else {}
        base_codes = shortcodes(base_data)
        report["baseline"] = summarize_response("baseline", r, [])
        report["baseline"]["interesting_all"] = interesting_keys(base_data)[:250]
        if isinstance(base_data, dict):
            # Preserve compact structural hints without image URLs/body bloat.
            report["baseline"]["profile_scalars"] = {
                k: v for k, v in base_data.items()
                if not isinstance(v, (dict, list, str)) or (isinstance(v, str) and len(v) < 180)
            }
            posts = base_data.get("posts") or []
            if posts and isinstance(posts[0], dict):
                report["baseline"]["post_keys"] = list(posts[0].keys())
    except Exception as exc:
        report["baseline"] = {"error": repr(exc)}
        base_codes = []

    # Common pagination conventions. The comparison tells us immediately if a parameter is honored.
    tests = [
        ("page_2", "GET", {"username": HANDLE, "page": 2}, None),
        ("page_3", "GET", {"username": HANDLE, "page": 3}, None),
        ("offset_12", "GET", {"username": HANDLE, "offset": 12}, None),
        ("offset_24", "GET", {"username": HANDLE, "offset": 24}, None),
        ("start_12", "GET", {"username": HANDLE, "start": 12}, None),
        ("limit_50", "GET", {"username": HANDLE, "limit": 50}, None),
        ("count_50", "GET", {"username": HANDLE, "count": 50}, None),
        ("per_page_50", "GET", {"username": HANDLE, "per_page": 50}, None),
        ("max_id_12", "GET", {"username": HANDLE, "max_id": 12}, None),
        ("maxid_12", "GET", {"username": HANDLE, "maxid": 12}, None),
        ("cursor_12", "GET", {"username": HANDLE, "cursor": 12}, None),
        ("after_12", "GET", {"username": HANDLE, "after": 12}, None),
        ("post_page_2", "POST", None, {"username": HANDLE, "page": 2}),
        ("post_offset_12", "POST", None, {"username": HANDLE, "offset": 12}),
    ]
    for label, method, params, data in tests:
        try:
            r = req(method, params=params, data=data)
            report["tests"].append(summarize_response(label, r, base_codes))
        except Exception as exc:
            report["tests"].append({"label": label, "error": repr(exc)})
        time.sleep(0.25)

    # Inspect likely frontend assets, but never fail the probe if an asset stalls.
    for url in [BASE + "/", BASE + "/assets/js/main.js", BASE + "/assets/js/tools.js"]:
        row = {"url": url}
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Referer": BASE + "/"}, impersonate="chrome", timeout=6)
            text = r.text
            row.update({"status": r.status_code, "bytes": len(r.content)})
            hits = []
            for kw in ["instagram.php", "page", "offset", "cursor", "max_id", "loadmore", "next"]:
                for m in list(re.finditer(kw, text, flags=re.I))[:4]:
                    hits.append({"keyword": kw, "snippet": text[max(0,m.start()-250):m.start()+550]})
            row["hits"] = hits[:40]
        except Exception as exc:
            row["error"] = repr(exc)
        report["assets"].append(row)

    Path("thepicuki_js_inspection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    winners = [x for x in report["tests"] if x.get("status") == 200 and x.get("shortcodes") and not x.get("same_as_baseline")]
    print(json.dumps({
        "baseline_posts": len(base_codes),
        "baseline_shortcodes": base_codes,
        "winner_count": len(winners),
        "winners": winners,
        "baseline_interesting": report.get("baseline", {}).get("interesting_all", [])[:30],
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
