from __future__ import annotations

import json
import re
import sys
import traceback
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as crequests

TARGETS = [
    ("imginn_profile", "https://imginn.com/inthestreetsla/"),
    ("imginn_profile_nyc", "https://imginn.com/watchingnewyork/"),
    ("imginn_tag", "https://imginn.com/tags/NewYorkCity/"),
    ("instagram_profile", "https://www.instagram.com/inthestreetsla/"),
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


def inspect_html(name: str, url: str) -> dict:
    row = {"name": name, "url": url}
    try:
        r = crequests.get(
            url,
            headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
            impersonate="chrome",
            timeout=30,
            allow_redirects=True,
        )
        row.update({"status": r.status_code, "final_url": str(r.url), "bytes": len(r.content)})
        soup = BeautifulSoup(r.text, "html.parser")
        row["title"] = soup.title.get_text(" ", strip=True) if soup.title else None
        imgs = []
        for tag in soup.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                val = tag.get(attr)
                if val:
                    imgs.append(urljoin(str(r.url), val))
            srcset = tag.get("srcset")
            if srcset:
                for part in srcset.split(","):
                    val = part.strip().split(" ")[0]
                    if val:
                        imgs.append(urljoin(str(r.url), val))
        links = [urljoin(str(r.url), a.get("href")) for a in soup.find_all("a", href=True)]
        row["img_count"] = len(set(imgs))
        row["post_link_count"] = len({x for x in links if re.search(r"/(?:p|reel)/", x)})
        row["sample_images"] = list(dict.fromkeys(imgs))[:5]
        row["sample_post_links"] = list(dict.fromkeys(x for x in links if re.search(r"/(?:p|reel)/", x)))[:5]
        with open(f"diag_{name}.html", "w", encoding="utf-8") as f:
            f.write(r.text[:2_000_000])
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc(limit=4)
    return row


def inspect_instaloader() -> dict:
    row = {"name": "instaloader_profile_api", "handle": "inthestreetsla"}
    try:
        import instaloader

        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )
        profile = instaloader.Profile.from_username(loader.context, "inthestreetsla")
        row["profile_id"] = profile.userid
        row["followers"] = profile.followers
        row["posts_count"] = profile.mediacount
        posts = []
        for i, post in enumerate(profile.get_posts()):
            posts.append(
                {
                    "shortcode": post.shortcode,
                    "date_utc": post.date_utc.isoformat(),
                    "typename": post.typename,
                    "url": post.url,
                }
            )
            if i >= 2:
                break
        row["sample_posts"] = posts
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc(limit=5)
    return row


def main() -> int:
    results = [inspect_html(name, url) for name, url in TARGETS]
    results.append(inspect_instaloader())
    print(json.dumps(results, indent=2, ensure_ascii=False))
    with open("diagnostic_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    # Diagnostic workflow should finish successfully even if individual routes fail.
    return 0


if __name__ == "__main__":
    sys.exit(main())
