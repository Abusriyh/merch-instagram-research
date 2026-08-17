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

IG_APP_ID = "936619743392459"
SAFARI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"


def ig_headers() -> dict[str, str]:
    return {
        "X-IG-App-ID": IG_APP_ID,
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": SAFARI_UA,
        "Referer": f"https://www.instagram.com/{HANDLE}/",
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def caption_from_node(node: dict[str, Any]) -> str:
    try:
        edges = ((node.get("edge_media_to_caption") or {}).get("edges") or [])
        if edges:
            return str(((edges[0] or {}).get("node") or {}).get("text") or "")
    except Exception:
        pass
    return ""


def normalize_instagram_user(user: dict[str, Any]) -> dict[str, Any]:
    timeline = user.get("edge_owner_to_timeline_media") or {}
    edges = timeline.get("edges") or []
    posts: list[dict[str, Any]] = []
    for edge in edges[:12]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        like_count = None
        for key in ("edge_liked_by", "edge_media_preview_like"):
            val = node.get(key)
            if isinstance(val, dict) and val.get("count") is not None:
                like_count = val.get("count")
                break
        comment_count = None
        val = node.get("edge_media_to_comment")
        if isinstance(val, dict):
            comment_count = val.get("count")
        posts.append({
            "shortcode": node.get("shortcode"),
            "display_url": node.get("display_url") or node.get("thumbnail_src"),
            "thumbnail": node.get("thumbnail_src"),
            "timestamp": node.get("taken_at_timestamp"),
            "type": node.get("__typename"),
            "is_video": bool(node.get("is_video")),
            "like_count": like_count,
            "comment_count": comment_count,
            "caption": caption_from_node(node),
        })
    return {
        "success": True,
        "source": "instagram_web_profile_info",
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "biography": user.get("biography"),
        "followers": ((user.get("edge_followed_by") or {}).get("count")),
        "posts_count": timeline.get("count"),
        "is_private": user.get("is_private"),
        "posts": posts,
        "has_next_page": ((timeline.get("page_info") or {}).get("has_next_page")),
        "end_cursor": ((timeline.get("page_info") or {}).get("end_cursor")),
    }


def fetch_instagram() -> dict[str, Any]:
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={HANDLE}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers=ig_headers(),
                impersonate="safari",
                timeout=18,
                allow_redirects=True,
            )
            if r.status_code == 200:
                data = r.json()
                user = ((data.get("data") or {}).get("user")) if isinstance(data, dict) else None
                if isinstance(user, dict):
                    return normalize_instagram_user(user)
                last = RuntimeError("instagram JSON had no user")
            else:
                last = RuntimeError(f"instagram HTTP {r.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(0.8 + attempt * 1.2)
    raise last or RuntimeError("instagram fetch failed")


def fetch_thepicuki() -> dict[str, Any]:
    url = f"https://thepicuki.com/api/instagram.php?username={HANDLE}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": CHROME_UA, "Referer": "https://thepicuki.com/", "Accept": "application/json,*/*"},
                impersonate="chrome",
                timeout=18,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    raw_text = json.dumps(data, ensure_ascii=False).lower()
                    if "sample demo data" in raw_text:
                        raise RuntimeError("thepicuki demo fallback")
                    data["source"] = "thepicuki"
                    return data
                last = RuntimeError("ThePicuki returned non-object JSON")
            else:
                last = RuntimeError(f"ThePicuki HTTP {r.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(0.8 + attempt * 1.2)
    raise last or RuntimeError("ThePicuki fetch failed")


def fetch_json() -> dict[str, Any]:
    try:
        return fetch_instagram()
    except Exception as ig_exc:
        try:
            data = fetch_thepicuki()
            data["instagram_error"] = f"{type(ig_exc).__name__}:{ig_exc}"
            return data
        except Exception as tp_exc:
            raise RuntimeError(
                f"instagram={type(ig_exc).__name__}:{ig_exc}; thepicuki={type(tp_exc).__name__}:{tp_exc}"
            ) from tp_exc


def download(url: str, source: str) -> bytes | None:
    if not url:
        return None
    if url.startswith("/"):
        url = "https://thepicuki.com" + url
    referer = f"https://www.instagram.com/{HANDLE}/" if source == "instagram_web_profile_info" else "https://thepicuki.com/"
    ua = SAFARI_UA if source == "instagram_web_profile_info" else CHROME_UA
    impersonate = "safari" if source == "instagram_web_profile_info" else "chrome"
    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": ua, "Referer": referer, "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
                impersonate=impersonate,
                timeout=20,
                allow_redirects=True,
            )
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code == 200 and len(r.content) > 4000 and (ctype.startswith("image/") or r.content[:3] == b"\xff\xd8\xff"):
                return r.content
        except Exception:
            pass
        time.sleep(0.5 + attempt * 0.8)
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
        source = str(data.get("source") or "unknown")
        if not data.get("success"):
            report["errors"].append("api_success_false")
            meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"handle": HANDLE, "city": CITY, "status": "api_success_false"}), flush=True)
            return
        report["profile"] = {
            "source": source,
            "username": data.get("username"),
            "full_name": data.get("full_name"),
            "biography": data.get("biography"),
            "followers": data.get("followers"),
            "posts_count": data.get("posts_count"),
            "is_private": data.get("is_private"),
            "has_next_page": data.get("has_next_page"),
            "end_cursor": data.get("end_cursor"),
        }
        if data.get("instagram_error"):
            report["errors"].append(f"instagram_fallback:{data.get('instagram_error')}")
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
                    blob = download(candidate, source)
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
                "profile_rank": idx + 1,
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
                "profile_source": source,
            })
        report["ok"] = len(report["items"]) > 0
    except Exception as exc:
        report["errors"].append(f"fatal:{type(exc).__name__}:{exc}")
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "handle": HANDLE,
        "city": CITY,
        "ok": report["ok"],
        "source": report.get("profile", {}).get("source"),
        "downloaded": len(report["items"]),
        "errors": report["errors"][:3],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
