from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curl_cffi import requests

USERS = ["inthestreetsla", "watchingnewyork", "cpplunkett"]
APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def headers(username: str) -> dict[str, str]:
    return {
        "X-IG-App-ID": APP_ID,
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": UA,
        "Referer": f"https://www.instagram.com/{username}/",
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


def main():
    report: dict[str, Any] = {"anonymous": True, "users": {}}
    for username in USERS:
        row: dict[str, Any] = {}
        h = headers(username)
        user_id = None

        # Test both hosts used by current/open-source Instagram clients.
        for host in ["www.instagram.com", "i.instagram.com"]:
            url = f"https://{host}/api/v1/users/web_profile_info/?username={username}"
            try:
                r = requests.get(url, headers=h, impersonate="safari", timeout=15, allow_redirects=True)
                d = safe_json(r)
                info = summarize(r, d)
                user = ((d or {}).get("data") or {}).get("user") if isinstance(d, dict) else None
                if isinstance(user, dict):
                    info["user"] = {
                        "id": user.get("id"),
                        "username": user.get("username"),
                        "full_name": user.get("full_name"),
                        "is_private": user.get("is_private"),
                        "edge_owner_to_timeline_media_count": ((user.get("edge_owner_to_timeline_media") or {}).get("count")),
                    }
                    user_id = str(user.get("id") or user_id or "") or None
                row[f"profile_{host}"] = info
            except Exception as exc:
                row[f"profile_{host}"] = {"error": repr(exc)}

        # If anonymous profile lookup yielded an id, test current v1 feed + second page.
        if user_id:
            feed_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/"
            max_id = None
            pages = []
            for page in range(1, 3):
                params = {"count": 12}
                if max_id:
                    params["max_id"] = max_id
                try:
                    r = requests.get(feed_url, headers=h, params=params, impersonate="safari", timeout=15, allow_redirects=True)
                    d = safe_json(r)
                    info = summarize(r, d)
                    if isinstance(d, dict):
                        items = d.get("items") or []
                        info.update({
                            "items_len": len(items),
                            "more_available": d.get("more_available"),
                            "next_max_id": d.get("next_max_id"),
                            "codes": [str(x.get("code")) for x in items[:12] if isinstance(x, dict) and x.get("code")],
                            "taken_at": [x.get("taken_at") for x in items[:12] if isinstance(x, dict)],
                        })
                        max_id = d.get("next_max_id")
                    pages.append(info)
                    if not isinstance(d, dict) or not d.get("more_available") or not max_id:
                        break
                except Exception as exc:
                    pages.append({"error": repr(exc)})
                    break
            row["feed"] = pages
        else:
            row["feed"] = [{"skipped": "no anonymous user_id"}]

        report["users"][username] = row
        print(json.dumps({"username": username, "result": row}, ensure_ascii=False), flush=True)

    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
