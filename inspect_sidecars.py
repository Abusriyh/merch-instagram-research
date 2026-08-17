from __future__ import annotations

import json
from curl_cffi import requests

ACCOUNTS = ["watchingnewyork", "genstreetstyle", "ballardstreetstyle", "cpplunkett", "mikeando.photo", "jraffshoots"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"


def fetch(username):
    url = f"https://thepicuki.com/api/instagram.php?username={username}"
    r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://thepicuki.com/"}, impersonate="chrome", timeout=20)
    return r.json()


def summarize(obj, depth=0):
    if depth > 4:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and len(v) > 450:
                out[k] = v[:450] + "..."
            else:
                out[k] = summarize(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [summarize(v, depth + 1) for v in obj[:8]] + ([f"... +{len(obj)-8} more"] if len(obj) > 8 else [])
    return obj


def find_urls(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(find_urls(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_urls(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and obj.startswith("http"):
        found.append((path, obj))
    return found


def main():
    report = []
    for handle in ACCOUNTS:
        d = fetch(handle)
        posts = d.get("posts") or []
        side_posts = [p for p in posts if p.get("type") == "GraphSidecar"]
        row = {"handle": handle, "returned_posts": len(posts), "sidecar_posts": len(side_posts), "examples": []}
        for p in side_posts[:3]:
            sc = p.get("sidecar")
            row["examples"].append({
                "shortcode": p.get("shortcode"),
                "post_keys": list(p.keys()),
                "sidecar_type": type(sc).__name__,
                "sidecar_summary": summarize(sc),
                "sidecar_urls": find_urls(sc),
                "post_urls": find_urls(p),
            })
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    with open("sidecar_inspection.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
