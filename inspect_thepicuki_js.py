from __future__ import annotations

import json
import re
from pathlib import Path
from curl_cffi import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
URLS = [
    "https://thepicuki.com/assets/js/main.js",
    "https://thepicuki.com/assets/js/tools.js",
]
KEYWORDS = ["instagram.php", "sidecar", "shortcode", "max_id", "maxid", "cursor", "page", "posts", "loadmore", "next", "download.php"]


def main():
    report = []
    for url in URLS:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://thepicuki.com/"}, impersonate="chrome", timeout=15)
        text = r.text
        endpoints = sorted(set(re.findall(r"[\"']([^\"']*(?:/api/|\.php)[^\"']*)[\"']", text, flags=re.I)))
        snippets = []
        low = text.lower()
        for kw in KEYWORDS:
            start = 0
            count = 0
            while True:
                i = low.find(kw.lower(), start)
                if i < 0 or count >= 8:
                    break
                a = max(0, i - 500)
                b = min(len(text), i + 900)
                snippets.append({"keyword": kw, "snippet": text[a:b]})
                start = i + len(kw)
                count += 1
        row = {"url": url, "status": r.status_code, "bytes": len(r.content), "endpoints": endpoints, "snippets": snippets}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    Path("thepicuki_js_inspection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
