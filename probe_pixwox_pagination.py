from __future__ import annotations

import json
import re
from pathlib import Path

from curl_cffi import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
BASE = "https://www.pixwox.com"
HANDLE = "watchingnewyork"


def get(url):
    return requests.get(
        url,
        headers={"User-Agent": UA, "Referer": BASE + "/", "Accept": "application/json,text/javascript,*/*;q=0.8"},
        impersonate="chrome",
        timeout=18,
        allow_redirects=True,
    )


def compact(obj, depth=0):
    if depth > 5:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        return {k: compact(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [compact(v, depth + 1) for v in obj[:5]] + ([f"... +{len(obj)-5}"] if len(obj) > 5 else [])
    if isinstance(obj, str) and len(obj) > 500:
        return obj[:500] + "..."
    return obj


def main():
    report = {"js": {}, "api_attempts": []}
    js_url = BASE + "/assets/js/app.w1n0s2j6.js"
    try:
        r = get(js_url)
        report["js"].update({"status": r.status_code, "bytes": len(r.content), "final_url": str(r.url)})
        text = r.text
        snippets = []
        low = text.lower()
        for kw in ["/api/posts?", "getposts", "maxid", "max_id", "userid", "next"]:
            pos = 0
            for _ in range(10):
                i = low.find(kw.lower(), pos)
                if i < 0:
                    break
                snippets.append({"keyword": kw, "snippet": text[max(0, i-800):min(len(text), i+1500)]})
                pos = i + len(kw)
        report["js"]["snippets"] = snippets
    except Exception as exc:
        report["js"]["error"] = repr(exc)

    urls = [
        f"{BASE}/api/posts?username={HANDLE}",
        f"{BASE}/api/posts/?username={HANDLE}",
        f"{BASE}/api/posts?user={HANDLE}",
        f"{BASE}/api/posts?q={HANDLE}",
    ]
    first_json = None
    for url in urls:
        try:
            r = get(url)
            row = {
                "url": url, "status": r.status_code, "final_url": str(r.url),
                "content_type": r.headers.get("content-type"), "bytes": len(r.content),
                "head": r.text[:500],
            }
            try:
                data = r.json()
                row["json"] = compact(data)
                if first_json is None and r.status_code == 200 and isinstance(data, dict):
                    first_json = data
            except Exception as exc:
                row["json_error"] = repr(exc)
            report["api_attempts"].append(row)
        except Exception as exc:
            report["api_attempts"].append({"url": url, "error": repr(exc)})

    Path("pixwox_pagination_probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
