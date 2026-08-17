from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curl_cffi import requests

APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
QUERIES = [
    "nyc streetwear",
    "new york street style",
    "los angeles streetwear",
    "graphic tee style",
    "mens streetwear usa",
    "vintage tee outfit",
]


def headers(referer: str = "https://www.instagram.com/") -> dict[str, str]:
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
        users = data.get("users") or []
        out["users_len"] = len(users)
        out["users"] = []
        for entry in users[:25]:
            user = (entry or {}).get("user") if isinstance(entry, dict) else None
            if not isinstance(user, dict):
                continue
            out["users"].append({
                "username": user.get("username"),
                "full_name": user.get("full_name"),
                "pk": user.get("pk"),
                "is_private": user.get("is_private"),
                "is_verified": user.get("is_verified"),
                "follower_count": user.get("follower_count"),
            })
    else:
        out["head"] = r.text[:300]
    return out


def main():
    report: dict[str, Any] = {"anonymous": True, "queries": {}}
    for query in QUERIES:
        row: dict[str, Any] = {}
        attempts = [
            ("web_topsearch", "https://www.instagram.com/web/search/topsearch/", {"query": query, "context": "blended"}),
            ("api_fb_topsearch", "https://www.instagram.com/api/v1/fbsearch/topsearch/", {"query": query, "context": "blended", "count": "30"}),
            ("api_users_search", "https://www.instagram.com/api/v1/users/search/", {"q": query, "count": "30", "timezone_offset": "0", "rank_token": "0"}),
        ]
        for name, url, params in attempts:
            try:
                r = requests.get(
                    url,
                    params=params,
                    headers=headers(),
                    impersonate="safari",
                    timeout=18,
                    allow_redirects=True,
                )
                row[name] = summarize(r, safe_json(r))
            except Exception as exc:
                row[name] = {"error": repr(exc)}
        report["queries"][query] = row
        print(json.dumps({"query": query, "result": row}, ensure_ascii=False), flush=True)

    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
