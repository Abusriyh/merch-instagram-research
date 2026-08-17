from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup
from curl_cffi import requests as requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

SITES = {
    "pixwox": "https://www.pixwox.com/",
    "greatfon": "https://greatfon.io/",
    "anonig_me": "https://www.anonig.me/",
    "anonyig_online": "https://anonyig.online/",
    "anonyig_com": "https://anonyig.com/en/instagram-viewer/",
    "thepicuki": "https://thepicuki.com/",
}
USERNAME = "inthestreetsla"


def fetch(session, url, *, method="GET", data=None, params=None):
    return session.request(
        method,
        url,
        data=data,
        params=params,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": url,
        },
        impersonate="chrome",
        timeout=15,
        allow_redirects=True,
    )


def analyze_site(name, base):
    out = {"name": name, "base": base}
    s = requests.Session()
    try:
        r = fetch(s, base)
        out["home"] = {"status": r.status_code, "final_url": str(r.url), "bytes": len(r.content)}
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        forms = []
        for idx, form in enumerate(soup.find_all("form")):
            action = form.get("action") or ""
            method = (form.get("method") or "GET").upper()
            fields = []
            for inp in form.find_all(["input", "textarea", "select"]):
                fields.append({
                    "tag": inp.name,
                    "name": inp.get("name"),
                    "type": inp.get("type"),
                    "value": inp.get("value"),
                    "placeholder": inp.get("placeholder"),
                })
            forms.append({"index": idx, "action": action, "method": method, "fields": fields})
        out["forms"] = forms
        scripts = []
        for script in soup.find_all("script"):
            src = script.get("src")
            if src:
                scripts.append(urljoin(str(r.url), src))
        out["scripts"] = scripts[:30]

        text_blobs = [html]
        # Pull same-origin JS only; enough to discover API/search paths.
        for src in scripts[:15]:
            if not src.startswith(str(r.url).split('/', 3)[0] + '//' + str(r.url).split('/', 3)[2]):
                continue
            try:
                jr = fetch(s, src)
                if jr.status_code == 200 and len(jr.text) < 3_000_000:
                    text_blobs.append(jr.text)
            except Exception:
                pass
        joined = "\n".join(text_blobs)
        candidates = sorted(set(re.findall(r"['\"]([^'\"]*(?:api|search|profile|user|instagram)[^'\"]*)['\"]", joined, flags=re.I)))
        out["endpoint_candidates"] = [c for c in candidates if len(c) < 240][:120]

        # Automatically exercise plausible search forms with only harmless public username input.
        attempts = []
        for f in forms:
            named = [x for x in f["fields"] if x.get("name")]
            userfield = None
            for x in named:
                hint = " ".join(str(x.get(k) or "") for k in ("name", "placeholder", "type")).lower()
                if any(k in hint for k in ("user", "search", "query", "username", "url", "nick")):
                    userfield = x
                    break
            if not userfield:
                # a single non-hidden text-like field is usually the search box
                text_fields = [x for x in named if (x.get("type") or "text") not in ("hidden", "submit", "button", "checkbox", "radio")]
                if len(text_fields) == 1:
                    userfield = text_fields[0]
            if not userfield:
                continue
            payload = {}
            for x in named:
                if x["name"] == userfield["name"]:
                    payload[x["name"]] = USERNAME
                elif x.get("type") == "hidden" and x.get("value") is not None:
                    payload[x["name"]] = x["value"]
            target = urljoin(str(r.url), f["action"] or str(r.url))
            try:
                rr = fetch(s, target, method=f["method"], data=payload if f["method"] != "GET" else None, params=payload if f["method"] == "GET" else None)
                bs = BeautifulSoup(rr.text, "html.parser")
                imgs = []
                for im in bs.find_all("img"):
                    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                        v = im.get(attr)
                        if v and not v.startswith("data:"):
                            imgs.append(urljoin(str(rr.url), v))
                links = [urljoin(str(rr.url), a.get("href")) for a in bs.find_all("a", href=True)]
                attempts.append({
                    "form": f["index"], "method": f["method"], "target": target,
                    "payload_keys": list(payload), "status": rr.status_code, "final_url": str(rr.url),
                    "bytes": len(rr.content), "title": bs.title.get_text(" ", strip=True) if bs.title else None,
                    "img_count": len(set(imgs)), "sample_images": list(dict.fromkeys(imgs))[:5],
                    "instagramish_links": [x for x in links if USERNAME.lower() in x.lower() or "instagram.com" in x.lower()][:10],
                    "contains_username": USERNAME.lower() in rr.text.lower(),
                    "snippet": re.sub(r"\s+", " ", bs.get_text(" ", strip=True))[:500],
                })
            except Exception as exc:
                attempts.append({"form": f["index"], "target": target, "error": repr(exc)})
        out["search_attempts"] = attempts

        with open(f"mirror_{name}.html", "w", encoding="utf-8") as f:
            f.write(html[:2_000_000])
    except Exception as exc:
        out["error"] = repr(exc)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def main():
    results = [analyze_site(name, url) for name, url in SITES.items()]
    with open("mirror_diagnostic_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
