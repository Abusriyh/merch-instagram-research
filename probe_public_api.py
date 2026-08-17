from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curl_cffi import requests

USERS = ["watchingnewyork"]
TAGS = ["tshirtoutfit", "graphictee", "graphicteestyle", "tshirtstyle", "vintagetee"]
APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def base_headers(referer: str) -> dict[str, str]:
    return {
        "X-IG-App-ID": APP_ID,
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": UA,
        "Referer": referer,
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def safe_json(r) -> Any:
    try:
        return r.json()
    except Exception:
        return None


def summarize(r, data: Any) -> dict[str, Any]:
    out = {
        "status": r.status_code,
        "final_url": str(r.url),
        "content_type": r.headers.get("content-type"),
        "bytes": len(r.content),
        "redirect_history": [str(x.url) for x in r.history],
    }
    if isinstance(data, dict):
        out["top_keys"] = list(data.keys())
        out["status_field"] = data.get("status")
        out["message"] = data.get("message")
    else:
        out["head"] = r.text[:500]
    return out


def extract_hashtag_nodes(data: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(data, dict):
        return result
    hashtag = None
    if isinstance(data.get("graphql"), dict):
        hashtag = data["graphql"].get("hashtag")
    if hashtag is None and isinstance(data.get("data"), dict):
        hashtag = data["data"]
    if not isinstance(hashtag, dict):
        return result
    result["hashtag_keys"] = list(hashtag.keys())
    for key in ["edge_hashtag_to_media", "edge_hashtag_to_top_posts"]:
        block = hashtag.get(key)
        if isinstance(block, dict):
            edges = block.get("edges") or []
            result[key] = {
                "count": block.get("count"),
                "edges_len": len(edges),
                "page_info": block.get("page_info"),
                "codes": [((e.get("node") or {}).get("shortcode")) for e in edges[:12] if isinstance(e, dict)],
            }
    for key in ["recent", "top"]:
        val = hashtag.get(key)
        if isinstance(val, dict):
            result[key] = {"keys": list(val.keys())}
        elif isinstance(val, list):
            result[key] = {"len": len(val)}
    return result


def extract_sections(data: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(data, dict):
        return out
    sections = data.get("sections") or []
    media_codes: list[str] = []
    media_rows = 0
    for section in sections:
        layout = (section or {}).get("layout_content") or {}
        for media in layout.get("medias") or []:
            media_rows += 1
            m = (media or {}).get("media") or {}
            code = m.get("code")
            if code:
                media_codes.append(str(code))
    out.update({
        "sections_len": len(sections),
        "media_rows": media_rows,
        "codes": media_codes[:30],
        "more_available": data.get("more_available"),
        "next_max_id": data.get("next_max_id"),
        "next_page": data.get("next_page"),
    })
    return out


def main():
    report: dict[str, Any] = {"anonymous": True, "users": {}, "hashtags": {}}

    # Sanity check: keep one profile lookup so we know the runner/IP can still reach Instagram.
    for username in USERS:
        h = base_headers(f"https://www.instagram.com/{username}/")
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        try:
            r = requests.get(url, headers=h, impersonate="safari", timeout=15, allow_redirects=True)
            d = safe_json(r)
            info = summarize(r, d)
            user = ((d or {}).get("data") or {}).get("user") if isinstance(d, dict) else None
            if isinstance(user, dict):
                info["user"] = {"id": user.get("id"), "username": user.get("username")}
            report["users"][username] = info
        except Exception as exc:
            report["users"][username] = {"error": repr(exc)}

    for tag in TAGS:
        row: dict[str, Any] = {}
        referer = f"https://www.instagram.com/explore/tags/{tag}/"
        h = base_headers(referer)

        # Public metadata/recent grid endpoint used by Instaloader's Hashtag model.
        for host in ["www.instagram.com", "i.instagram.com"]:
            url = f"https://{host}/api/v1/tags/web_info/"
            try:
                r = requests.get(
                    url,
                    params={"tag_name": tag, "__a": "1", "__d": "dis"},
                    headers=h,
                    impersonate="safari",
                    timeout=18,
                    allow_redirects=True,
                )
                d = safe_json(r)
                info = summarize(r, d)
                info.update(extract_hashtag_nodes(d))
                row[f"web_info_{host}"] = info
            except Exception as exc:
                row[f"web_info_{host}"] = {"error": repr(exc)}

        # Sections endpoint: establish anonymous cookies first, then try a recent-grid POST.
        try:
            s = requests.Session(impersonate="safari")
            home = s.get("https://www.instagram.com/", headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
            csrf = s.cookies.get("csrftoken")
            sh = dict(h)
            sh["Content-Type"] = "application/x-www-form-urlencoded"
            if csrf:
                sh["X-CSRFToken"] = csrf
            r = s.post(
                f"https://www.instagram.com/api/v1/tags/{tag}/sections/",
                headers=sh,
                data={"include_persistent": "0", "page": "1", "surface": "grid", "tab": "recent"},
                timeout=18,
                allow_redirects=True,
            )
            d = safe_json(r)
            info = summarize(r, d)
            info["home_status"] = home.status_code
            info["csrf_present"] = bool(csrf)
            info.update(extract_sections(d))
            row["sections_www"] = info
        except Exception as exc:
            row["sections_www"] = {"error": repr(exc)}

        report["hashtags"][tag] = row
        print(json.dumps({"tag": tag, "result": row}, ensure_ascii=False), flush=True)

    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
