from __future__ import annotations

import json
import os
import time
from pathlib import Path
from curl_cffi import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
HANDLE = os.environ["HANDLE"]


def fetch():
    url = f"https://thepicuki.com/api/instagram.php?username={HANDLE}"
    last = None
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://thepicuki.com/", "Accept": "application/json,*/*"},
                impersonate="chrome",
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise last or RuntimeError("fetch failed")


def find_urls(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(find_urls(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_urls(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and (obj.startswith("http") or obj.startswith("/api/")):
        found.append((path, obj))
    return found


def compact(obj, depth=0):
    if depth > 5:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        return {k: compact(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [compact(v, depth + 1) for v in obj[:12]]
    if isinstance(obj, str) and len(obj) > 700:
        return obj[:700] + "..."
    return obj


def main():
    data = fetch()
    posts = data.get("posts") or []
    sideposts = [p for p in posts if p.get("type") == "GraphSidecar"]
    examples = []
    for p in sideposts[:2]:
        sc = p.get("sidecar")
        examples.append({
            "shortcode": p.get("shortcode"),
            "sidecar_type": type(sc).__name__,
            "sidecar": compact(sc),
            "sidecar_urls": find_urls(sc),
            "all_post_urls": find_urls(p),
        })
    report = {
        "handle": HANDLE,
        "success": data.get("success"),
        "posts": len(posts),
        "sidecar_posts": len(sideposts),
        "examples": examples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    Path(f"sidecar_{HANDLE}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
