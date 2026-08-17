from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from curl_cffi import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
HANDLES = ["watchingnewyork", "coffeecameranyc", "ballardstreetstyle", "genstreetstyle", "cpplunkett"]
BASES = ["https://www.pixnoy.com", "https://www.pixwox.com"]


def req(url, referer=None):
    return requests.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **({"Referer": referer} if referer else {}),
        },
        impersonate="chrome",
        timeout=18,
        allow_redirects=True,
    )


def parse_profile(html: str):
    soup = BeautifulSoup(html, "html.parser")
    hidden = {}
    for name in ["username", "userid", "hl", "str_loading"]:
        el = soup.select_one(f'input[name="{name}"]')
        if el:
            hidden[name] = el.get("value")
    return soup, hidden


def compact(obj, depth=0):
    if depth > 4:
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        return {k: compact(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [compact(v, depth + 1) for v in obj[:5]] + ([f"... +{len(obj)-5}"] if len(obj) > 5 else [])
    if isinstance(obj, str) and len(obj) > 450:
        return obj[:450] + "..."
    return obj


def main():
    out = []
    for handle in HANDLES:
        found = None
        attempts = []
        for base in BASES:
            for path in [f"/profile/{handle}/", f"/profile/{handle}", f"/{handle}/"]:
                url = base + path
                try:
                    r = req(url, base + "/")
                    soup, hidden = parse_profile(r.text)
                    row = {
                        "url": url,
                        "status": r.status_code,
                        "final_url": str(r.url),
                        "bytes": len(r.content),
                        "title": soup.title.get_text(" ", strip=True) if soup.title else None,
                        "hidden": hidden,
                        "contains_handle": handle.lower() in r.text.lower(),
                        "cf_challenge": "challenge-platform" in r.text.lower() or "cf-chl" in r.text.lower(),
                        "post_items": len(soup.select(".posts .item")),
                    }
                    attempts.append(row)
                    if hidden.get("userid") and hidden.get("username"):
                        found = (str(r.url).split("/profile/")[0], hidden, str(r.url))
                        break
                except Exception as exc:
                    attempts.append({"url": url, "error": repr(exc)})
            if found:
                break
        result = {"handle": handle, "attempts": attempts, "api": None}
        if found:
            base, hidden, referer = found
            params = {
                "username": hidden.get("username") or handle,
                "userid": hidden.get("userid") or "",
                "hl": hidden.get("hl") or "en",
            }
            api_url = base + "/api/posts?" + urlencode(params)
            try:
                rr = req(api_url, referer)
                apirow = {
                    "url": api_url,
                    "status": rr.status_code,
                    "final_url": str(rr.url),
                    "bytes": len(rr.content),
                    "content_type": rr.headers.get("content-type"),
                    "head": rr.text[:300],
                }
                try:
                    data = rr.json()
                    apirow["json"] = compact(data)
                except Exception as exc:
                    apirow["json_error"] = repr(exc)
                result["api"] = apirow
            except Exception as exc:
                result["api"] = {"error": repr(exc)}
        out.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    Path("pixnoy_profile_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
