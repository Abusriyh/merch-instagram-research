from __future__ import annotations

import json
import os
from pathlib import Path
from curl_cffi import requests

HANDLE = os.environ["HANDLE"].strip().lstrip("@")
APP_ID = "936619743392459"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def headers():
    return {
        "X-IG-App-ID": APP_ID,
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": UA,
        "Referer": f"https://www.instagram.com/{HANDLE}/",
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
    }


def main():
    out = {"handle": HANDLE}
    try:
        r = requests.get(
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": HANDLE}, headers=headers(), impersonate="safari", timeout=18,
        )
        out["status"] = r.status_code
        d = r.json() if r.status_code == 200 else {}
        user = ((d.get("data") or {}).get("user")) if isinstance(d, dict) else None
        timeline = (user or {}).get("edge_owner_to_timeline_media") or {}
        edges = timeline.get("edges") or []
        posts = []
        total_visuals = 0
        sidecar_posts = 0
        for rank, edge in enumerate(edges[:12], start=1):
            node = (edge or {}).get("node") or {}
            children = (((node.get("edge_sidecar_to_children") or {}).get("edges")) or [])
            visual_children = []
            for child_edge in children:
                ch = (child_edge or {}).get("node") or {}
                visual_children.append({
                    "shortcode": ch.get("shortcode"),
                    "typename": ch.get("__typename"),
                    "is_video": ch.get("is_video"),
                    "has_display_url": bool(ch.get("display_url") or ch.get("thumbnail_src")),
                })
            if visual_children:
                sidecar_posts += 1
                count = len(visual_children)
            else:
                count = 1
            total_visuals += count
            posts.append({
                "rank": rank,
                "shortcode": node.get("shortcode"),
                "typename": node.get("__typename"),
                "child_count": len(visual_children),
                "children": visual_children,
            })
        out.update({
            "posts_returned": len(edges[:12]),
            "sidecar_posts": sidecar_posts,
            "total_visuals_if_expanded": total_visuals,
            "posts": posts,
        })
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
    Path("sidecar_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out.get(k) for k in ("handle","status","posts_returned","sidecar_posts","total_visuals_if_expanded","error")}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
