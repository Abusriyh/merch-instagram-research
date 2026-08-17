from __future__ import annotations

import json
import os
from pathlib import Path

from curl_cffi import requests

HANDLE = os.environ["HANDLE"].strip().lstrip("@")
COUNT = int(os.environ.get("COUNT", "12"))
APP_ID = "936619743392459"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def h():
    return {
        "X-IG-App-ID": APP_ID,
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": UA,
        "Referer": f"https://www.instagram.com/{HANDLE}/",
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


def main():
    out = {"handle": HANDLE, "count_requested": COUNT}
    try:
        r = requests.get(
            "https://www.instagram.com/api/v1/users/web_profile_info/",
            params={"username": HANDLE}, headers=h(), impersonate="safari", timeout=18,
        )
        out["profile_status"] = r.status_code
        d = r.json() if r.status_code == 200 else {}
        user = ((d.get("data") or {}).get("user")) if isinstance(d, dict) else None
        uid = str((user or {}).get("id") or "")
        out["user_id"] = uid
        if not uid:
            raise RuntimeError("no user id")
        f = requests.get(
            f"https://www.instagram.com/api/v1/feed/user/{uid}/",
            params={"count": COUNT}, headers=h(), impersonate="safari", timeout=22,
        )
        out["feed_status"] = f.status_code
        try:
            fd = f.json()
        except Exception:
            fd = {}
        items = fd.get("items") or [] if isinstance(fd, dict) else []
        out.update({
            "items_len": len(items),
            "num_results": fd.get("num_results") if isinstance(fd, dict) else None,
            "more_available": fd.get("more_available") if isinstance(fd, dict) else None,
            "next_max_id": fd.get("next_max_id") if isinstance(fd, dict) else None,
            "message": fd.get("message") if isinstance(fd, dict) else None,
            "codes": [x.get("code") for x in items[:60] if isinstance(x, dict)],
        })
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
    Path("feed_count_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
