from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
USERNAME = "inthestreetsla"
OUT = Path("api_probe_images")
OUT.mkdir(exist_ok=True)


def get(url, referer=None, timeout=20):
    return requests.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **({"Referer": referer} if referer else {}),
        },
        impersonate="chrome",
        timeout=timeout,
        allow_redirects=True,
    )


def safe_keys(obj):
    return list(obj.keys()) if isinstance(obj, dict) else []


def media_candidates(post):
    if not isinstance(post, dict):
        return []
    preferred = [
        "display_url", "image_url", "image", "url", "thumbnail_url", "thumbnail",
        "media_url", "photo_url", "image_proxy", "proxy", "video_proxy", "video_url"
    ]
    vals = []
    for k in preferred:
        v = post.get(k)
        if isinstance(v, str) and v.startswith("http"):
            vals.append((k, v))
    for k, v in post.items():
        if isinstance(v, str) and v.startswith("http") and ("img" in k.lower() or "image" in k.lower() or "url" in k.lower() or "proxy" in k.lower()):
            vals.append((k, v))
    seen = set()
    return [(k, v) for k, v in vals if not (v in seen or seen.add(v))]


def main():
    report = {}

    api = f"https://thepicuki.com/api/instagram.php?username={USERNAME}"
    r = get(api, "https://thepicuki.com/")
    report["thepicuki"] = {
        "status": r.status_code,
        "final_url": str(r.url),
        "content_type": r.headers.get("content-type"),
        "bytes": len(r.content),
        "text_head": r.text[:500],
    }
    try:
        data = r.json()
        report["thepicuki"]["json_type"] = type(data).__name__
        report["thepicuki"]["keys"] = safe_keys(data)
        if isinstance(data, dict):
            # Do not log huge raw responses; summarize structure.
            report["thepicuki"]["username"] = data.get("username")
            report["thepicuki"]["full_name"] = data.get("full_name")
            report["thepicuki"]["posts_count_field"] = data.get("posts_count")
            posts = data.get("posts") or data.get("items") or data.get("data") or []
            if isinstance(posts, dict):
                posts = posts.get("items") or posts.get("posts") or []
            report["thepicuki"]["returned_posts"] = len(posts) if isinstance(posts, list) else None
            samples = []
            if isinstance(posts, list):
                for idx, p in enumerate(posts[:5]):
                    sample = {"index": idx, "keys": safe_keys(p)}
                    if isinstance(p, dict):
                        for k in ("shortcode", "taken_at", "timestamp", "date", "caption", "is_video", "likes", "comments"):
                            if k in p:
                                val = p.get(k)
                                sample[k] = val[:300] if isinstance(val, str) else val
                        sample["media_candidates"] = media_candidates(p)[:10]
                    samples.append(sample)
                report["thepicuki"]["samples"] = samples

                # Attempt actual binary image retrieval for up to first 3 posts.
                downloads = []
                for idx, p in enumerate(posts[:10]):
                    if len(downloads) >= 3:
                        break
                    for key, url in media_candidates(p):
                        try:
                            mr = get(url, "https://thepicuki.com/", timeout=20)
                            ct = (mr.headers.get("content-type") or "").lower()
                            if mr.status_code == 200 and ct.startswith("image/") and len(mr.content) > 5000:
                                ext = ".jpg"
                                if "png" in ct: ext = ".png"
                                elif "webp" in ct: ext = ".webp"
                                fp = OUT / f"{idx:02d}_{key}{ext}"
                                fp.write_bytes(mr.content)
                                downloads.append({
                                    "post_index": idx, "field": key, "url": url,
                                    "status": mr.status_code, "content_type": ct, "bytes": len(mr.content),
                                    "sha256": hashlib.sha256(mr.content).hexdigest(), "file": str(fp),
                                })
                                break
                        except Exception as exc:
                            downloads.append({"post_index": idx, "field": key, "url": url, "error": repr(exc)})
                            # Don't count failed attempts toward the three successful images.
                            if downloads[-1].get("error"):
                                downloads.pop()
                report["thepicuki"]["downloads"] = downloads
    except Exception as exc:
        report["thepicuki"]["json_error"] = repr(exc)

    # AnonIG exposes profile route in its client bundle; probe it as independent fallback.
    anon_urls = [
        f"https://www.anonig.me/instagram-viewer/{USERNAME}",
        f"https://anonyig.online/user/{USERNAME}",
        f"https://anonyig.online/u/{USERNAME}",
    ]
    anon = []
    for u in anon_urls:
        try:
            rr = get(u)
            anon.append({
                "url": u, "status": rr.status_code, "final_url": str(rr.url),
                "content_type": rr.headers.get("content-type"), "bytes": len(rr.content),
                "contains_username": USERNAME.lower() in rr.text.lower(),
                "contains_cdninstagram": "cdninstagram.com" in rr.text.lower(),
                "head": rr.text[:300],
            })
        except Exception as exc:
            anon.append({"url": u, "error": repr(exc)})
    report["anon_fallbacks"] = anon

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
