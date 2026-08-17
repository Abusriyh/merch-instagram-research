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
USERS = ["inthestreetsla", "watchingnewyork", "atxstreetstyle", "ballardstreetstyle", "cpplunkett"]


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


def recursive_keys(obj: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            lk = str(k).lower()
            if any(t in lk for t in ("chain", "suggest", "related", "similar", "recommend")):
                out.append(p)
            out.extend(recursive_keys(v, p, depth + 1))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            out.extend(recursive_keys(v, f"{prefix}[{i}]", depth + 1))
    return out


def extract_chaining(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    candidates: list[Any] = []
    for key in ("users", "items", "suggested_users"):
        if isinstance(data.get(key), list):
            candidates.extend(data[key])
    out = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        u = item.get("user") if isinstance(item.get("user"), dict) else item
        if isinstance(u, dict) and u.get("username"):
            out.append(compact_user(u))
    return out


def main():
    report: dict[str, Any] = {"anonymous": True, "users": {}}
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
                row["related_like_keys"] = sorted(set(recursive_keys(user)))
                uid = str(user.get("id") or "")
            else:
                uid = ""
        except Exception as exc:
            row["profile_error"] = repr(exc)
            uid = ""

        if uid:
            attempts = [
                ("discover_chaining_www", f"https://www.instagram.com/api/v1/discover/chaining/", {"target_id": uid}),
                ("discover_chaining_i", f"https://i.instagram.com/api/v1/discover/chaining/", {"target_id": uid}),
                ("suggested_users", f"https://www.instagram.com/api/v1/friendships/{uid}/following/", {"count": 12}),
            ]
            for name, url, params in attempts:
                try:
                    q = requests.get(url, params=params, headers=headers(username), impersonate="safari", timeout=18, allow_redirects=True)
                    qd = safe_json(q)
                    row[name] = {
                        "status": q.status_code,
                        "final_url": str(q.url),
                        "bytes": len(q.content),
                        "top_keys": list(qd.keys()) if isinstance(qd, dict) else [],
                        "message": qd.get("message") if isinstance(qd, dict) else None,
                        "users": extract_chaining(qd)[:30],
                    }
                except Exception as exc:
                    row[name] = {"error": repr(exc)}
        report["users"][username] = row
        print(json.dumps({"username": username, "result": row}, ensure_ascii=False), flush=True)

    Path("api_probe_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
