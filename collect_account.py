from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests
from PIL import Image, ImageOps

HANDLE = os.environ["HANDLE"].strip().lstrip("@")
CITY = os.environ["CITY"].strip()
KIND = os.environ.get("KIND", "fashion").strip()
OUT = Path("batch") / CITY / HANDLE
OUT.mkdir(parents=True, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"


def fetch_json() -> dict[str, Any]:
    url = f"https://thepicuki.com/api/instagram.php?username={HANDLE}"
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://thepicuki.com/", "Accept": "application/json,*/*"},
                impersonate="chrome",
                timeout=18,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
                raise RuntimeError("API returned non-object JSON")
            last = RuntimeError(f"profile HTTP {r.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(1.0 + attempt * 1.5)
    raise last or RuntimeError("profile fetch failed")


def download(url: str) -> bytes | None:
    if not url:
        return None
    if url.startswith("/"):
        url = "https://thepicuki.com" + url
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": "https://thepicuki.com/", "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
                impersonate="chrome",
                timeout=20,
                allow_redirects=True,
            )
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code == 200 and len(r.content) > 4000 and (ctype.startswith("image/") or r.content[:3] == b"\xff\xd8\xff"):
                return r.content
        except Exception:
            pass
        time.sleep(0.7 + attempt)
    return None


def normalize_image(blob: bytes, path: Path) -> tuple[int, int, str] | None:
    try:
        im = Image.open(io.BytesIO(blob))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        w, h = im.size
        im.save(path, "JPEG", quality=85, optimize=True, progressive=True)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return w, h, sha
    except Exception:
        return None


def main() -> None:
    meta_path = OUT / f"batch_meta_{HANDLE}.json"
    report: dict[str, Any] = {
        "handle": HANDLE,
        "city": CITY,
        "kind": KIND,
        "ok": False,
        "profile": {},
        "items": [],
        "errors": [],
    }
    try:
        data = fetch_json()
        raw_text = json.dumps(data, ensure_ascii=False).lower()
        if "sample demo data" in raw_text:
            report["errors"].append("demo_fallback_detected")
            meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"handle": HANDLE, "city": CITY, "status": "demo_fallback"}), flush=True)
            return
        if not data.get("success"):
            report["errors"].append("api_success_false")
            meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"handle": HANDLE, "city": CITY, "status": "api_success_false"}), flush=True)
            return
        report["profile"] = {
            "username": data.get("username"),
            "full_name": data.get("full_name"),
            "biography": data.get("biography"),
            "followers": data.get("followers"),
            "posts_count": data.get("posts_count"),
            "is_private": data.get("is_private"),
        }
        if data.get("is_private"):
            report["errors"].append("private_profile")
            meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        posts = data.get("posts") or []
        for idx, post in enumerate(posts[:12]):
            if not isinstance(post, dict):
                continue
            shortcode = str(post.get("shortcode") or f"post{idx}")
            candidates = [post.get("display_url"), post.get("thumbnail"), post.get("display_proxy")]
            blob = None
            used = None
            for candidate in candidates:
                if isinstance(candidate, str) and candidate:
                    blob = download(candidate)
                    if blob:
                        used = candidate
                        break
            if not blob:
                report["errors"].append(f"media_failed:{shortcode}")
                continue
            image_path = OUT / f"{idx:02d}_{shortcode}.jpg"
            info = normalize_image(blob, image_path)
            if not info:
                report["errors"].append(f"decode_failed:{shortcode}")
                continue
            w, h, sha = info
            caption = post.get("caption") or ""
            if not isinstance(caption, str):
                caption = str(caption)
            report["items"].append({
                "handle": HANDLE,
                "city": CITY,
                "kind": KIND,
                "shortcode": shortcode,
                "source_url": f"https://www.instagram.com/p/{shortcode}/",
                "timestamp": post.get("timestamp"),
                "post_type": post.get("type"),
                "is_video": bool(post.get("is_video")),
                "like_count": post.get("like_count"),
                "comment_count": post.get("comment_count"),
                "caption": caption[:1200],
                "file": str(image_path),
                "width": w,
                "height": h,
                "sha256": sha,
                "media_source_kind": "display_url" if used == post.get("display_url") else "fallback",
            })
        report["ok"] = len(report["items"]) > 0
    except Exception as exc:
        report["errors"].append(f"fatal:{type(exc).__name__}:{exc}")
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "handle": HANDLE,
        "city": CITY,
        "ok": report["ok"],
        "downloaded": len(report["items"]),
        "errors": report["errors"][:3],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
