from __future__ import annotations

import json
import re
from collections import Counter
from curl_cffi import requests

SEEDS = {
    "new_york": ["watchingnewyork", "newyorkfrombehind", "coffeecameranyc", "whatpeoplearewearing"],
    "los_angeles": ["streetgeist", "inthestreetsla"],
    "chicago": ["cpplunkett", "rach.bires", "mikeando.photo"],
    "miami": ["genstreetstyle", "shootmejade"],
    "seattle": ["ballardstreetstyle"],
    "san_francisco": ["amfuku"],
    "atlanta": ["jraffshoots", "mario.fernando"],
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36"
MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{2,30})")


def fetch(username):
    url = f"https://thepicuki.com/api/instagram.php?username={username}"
    r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://thepicuki.com/"}, impersonate="chrome", timeout=20)
    try:
        data = r.json()
    except Exception:
        return {"success": False, "status": r.status_code, "error": r.text[:200]}
    data["_status"] = r.status_code
    return data


def count_media(posts):
    media = 0
    for p in posts:
        media += 1
        sc = p.get("sidecar")
        if isinstance(sc, list):
            media += len(sc)
        elif isinstance(sc, dict):
            items = sc.get("items") or sc.get("edges") or sc.get("nodes") or []
            if isinstance(items, list):
                media += len(items)
    return media


def main():
    results = []
    all_mentions = Counter()
    for city, handles in SEEDS.items():
        for handle in handles:
            d = fetch(handle)
            posts = d.get("posts") or []
            mentions = Counter()
            for p in posts:
                cap = p.get("caption") or ""
                for m in MENTION_RE.findall(cap):
                    if m.lower() != handle.lower():
                        mentions[m.lower()] += 1
                        all_mentions[m.lower()] += 1
            row = {
                "city": city,
                "handle": handle,
                "success": bool(d.get("success")),
                "status": d.get("_status"),
                "full_name": d.get("full_name"),
                "bio": (d.get("biography") or "")[:240],
                "is_private": d.get("is_private"),
                "posts_count": d.get("posts_count"),
                "returned_posts": len(posts),
                "returned_media_estimate": count_media(posts),
                "mention_count": sum(mentions.values()),
                "unique_mentions": len(mentions),
                "top_mentions": mentions.most_common(20),
                "post_types": Counter(str(p.get("type")) for p in posts),
                "videos": sum(bool(p.get("is_video")) for p in posts),
            }
            row["post_types"] = dict(row["post_types"])
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {
        "seeds_tested": len(results),
        "successful": sum(r["success"] for r in results),
        "returned_posts": sum(r["returned_posts"] for r in results if r["success"]),
        "returned_media_estimate": sum(r["returned_media_estimate"] for r in results if r["success"]),
        "unique_mentions": len(all_mentions),
        "top_mentions": all_mentions.most_common(100),
    }
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    with open("seed_audit.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "seeds": results}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
