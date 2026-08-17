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
USERS = ["inthestreetsla", "watchingnewyork", "ballardstreetstyle", "cpplunkett", "aimeesong", "imjennim", "sincerelyjules"]


def headers(username: str = "") -> dict[str, str]:
    referer = f"https://www.instagram.com/{username}/" if username else "https://www.instagram.com/"
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


def compact_user(u: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": u.get("id") or u.get("pk"),
        "username": u.get("username"),
        "full_name": u.get("full_name"),
        "is_private": u.get("is_private"),
        "is_verified": u.get("is_verified"),
        "category_name": u.get("category_name"),
        "biography": u.get("biography"),
    }


def embedded_related(user: dict[str, Any]) -> list[dict[str, Any]]:
    block = user.get("edge_related_profiles") or {}
    edges = block.get("edges") or [] if isinstance(block, dict) else []
    out: list[dict[str, Any]] = []
    for edge in edges:
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if isinstance(node, dict) and node.get("username"):
            out.append(compact_user(node))
    return out


def main():
    report: dict[str, Any] = {"anonymous": True, "users": {}}
    all_related: dict[str, dict[str, Any]] = {}
    for username in USERS:
        row: dict[str, Any] = {}
        try:
            r = requests.get(
                "https://www.instagram.com/api/v1/users/web_profile_info/",
                params={"username": username},
                headers=headers(username), impersonate="safari", timeout=18, allow_redirects=True,
            )
            d = safe_json(r)
            user = ((d or {}).get("data") or {}).get("user") if isinstance(d, dict) else None
            row["profile_status"] = r.status_code
            row["profile_bytes"] = len(r.content)
            if isinstance(user, dict):
                row["profile"] = compact_user(user)
                rel = embedded_related(user)
                row["embedded_related_count"] = len(rel)
                row["embedded_related"] = rel
                for u in rel:
                    if u.get("username"):
                        all_related[str(u["username"])] = u
            else:
                row["embedded_related_count"] = 0
                row["embedded_related"] = []
        except Exception as exc:
            row["profile_error"] = repr(exc)
        report["users"][username] = row
        print(json.dumps({"username": username, "result": row}, ensure_ascii=False), flush=True)

    report["unique_related"] = list(all_related.values())
    report["unique_related_count"] = len(all_related)
    print(json.dumps({"unique_related_count": len(all_related), "usernames": sorted(all_related)[:100]}, ensure_ascii=False), flush=True)
    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
