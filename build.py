"""Fetch the feeds in feeds.opml and render a static, bubo-style reader to public/."""

import calendar
import html
import socket
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import feedparser

OPML = Path("feeds.opml")
OUT = Path("public")
ITEMS_PER_FEED = 20   # cap per feed, so busy feeds (R-bloggers) don't flood the list
MAX_AGE_DAYS = 90     # only show posts from the last 90 days
TIMEOUT = 20
USER_AGENT = "feeds.basjacobs.com feed reader (+https://feeds.basjacobs.com)"


socket.setdefaulttimeout(TIMEOUT)


def load_feeds():
    seen, feeds = set(), []
    for node in ET.parse(OPML).iter("outline"):
        url = node.get("xmlUrl")
        if url and url not in seen:
            seen.add(url)
            feeds.append({"url": url, "title": node.get("title") or node.get("text") or url,
                          "site": node.get("htmlUrl") or url})
    return feeds


def fetch(feed):
    try:
        parsed = feedparser.parse(feed["url"], agent=USER_AGENT)
    except Exception as e:  # network errors etc.
        return {**feed, "items": [], "error": str(e)}
    if not parsed.entries:
        status = parsed.get("status")
        err = f"HTTP {status}" if status and status >= 400 else (getattr(parsed, "bozo_exception", None) or "no entries")
        return {**feed, "items": [], "error": str(err)}

    items = []
    for e in parsed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(t) if t else 0
        items.append({"title": e.get("title") or "(untitled)", "link": e.get("link") or feed["site"], "ts": ts})
    items.sort(key=lambda i: i["ts"], reverse=True)
    return {**feed, "items": items[:ITEMS_PER_FEED], "error": None}


def fmt_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""


def render(results):
    now = time.time()
    ok = [r for r in results if r["items"]]
    failed = sorted((r for r in results if not r["items"]), key=lambda r: r["title"].lower())
    e = html.escape

    # One merged list of all posts, newest first, grouped under a heading per day.
    posts = [{**i, "feed": r["title"], "site": r["site"]} for r in ok for i in r["items"]]
    posts = [p for p in posts if 0 < p["ts"] <= now + 86400 and now - p["ts"] < MAX_AGE_DAYS * 86400]
    posts.sort(key=lambda p: p["ts"], reverse=True)

    sections, day = [], None
    for p in posts:
        d = fmt_date(p["ts"])
        if d != day:
            if day:
                sections.append("  </ul>")
            label = datetime.fromtimestamp(p["ts"], timezone.utc).strftime("%A %-d %B %Y")
            sections.append(f'  <h2>{label}</h2>\n  <ul>')
            day = d
        sections.append(
            f'    <li data-ts="{p["ts"]}"><a href="{e(p["link"])}" target="_blank" rel="noopener">{e(p["title"])}</a>'
            f' <a class="feed" href="{e(p["site"])}" target="_blank" rel="noopener">{e(p["feed"])}</a></li>'
        )
    if day:
        sections.append("  </ul>")

    errors = ""
    if failed:
        lis = "\n".join(f'    <li><a href="{e(r["url"])}">{e(r["title"])}</a> — {e(r["error"] or "")}</li>' for r in failed)
        errors = f'  <details class="errors">\n    <summary>{len(failed)} feeds failed to load</summary>\n    <ul>\n{lis}\n    </ul>\n  </details>'

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    template = Path("template.html").read_text()
    return (template.replace("{{feeds}}", "\n".join(sections))
                    .replace("{{errors}}", errors)
                    .replace("{{updated}}", updated)
                    .replace("{{count}}", str(len(ok))))


def main():
    feeds = load_feeds()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(fetch, feeds))
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(render(results))
    n_failed = sum(1 for r in results if not r["items"])
    print(f"{len(feeds)} feeds, {n_failed} failed -> {OUT / 'index.html'}")


if __name__ == "__main__":
    main()
