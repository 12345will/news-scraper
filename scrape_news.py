#!/usr/bin/env python3
"""
Daily news scraper.

Reads one or more RSS feeds (including Google News query feeds), fetches each
article, extracts the *full* body text (not just the RSS summary), keeps the
ones that match your keywords, and appends new results to news_log.csv.

You only ever need to edit the CONFIG block below.
"""

import csv
import re
import datetime as dt
from pathlib import Path

import feedparser
import requests
import trafilatura

# ============================== CONFIG =================================
# Add as many feeds as you want. Google News query feeds work here too —
# tune the q= part of the URL to change what you track.
FEEDS = [
    "https://news.google.com/rss/search?q=Vestas+wind+when:1d&hl=en-US&gl=US&ceid=US:en",
    # "https://www.technologyreview.com/feed/",
    # "https://some-publisher.com/rss",
]

# An article is kept if its text contains ANY of these words (whole-word,
# case-insensitive — so "air" will NOT match "repair"). Leave as [] to keep all.
KEYWORDS = ["vestas", "offshore", "turbine", "order", "MW"]

MAX_ARTICLES_PER_RUN = 30      # safety cap so a busy day can't blow up the log
LOOKBACK_HOURS = 26            # ignore items older than this (slight overlap w/ a daily run)
OUTPUT_FILE = "news_log.csv"
# ======================================================================

REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-scraper/1.0)"}
FIELDS = ["Date", "Article Name", "Article Source", "Key Phrases", "Summary"]


def strip_html(s: str) -> str:
    """Crude HTML-tag remover for RSS summary fallbacks."""
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Return the keywords that appear as whole words in text (case-insensitive)."""
    hits = []
    for k in keywords:
        if re.search(r"\b" + re.escape(k) + r"\b", text, flags=re.IGNORECASE):
            hits.append(k)
    return hits


def fetch_article_text(url: str) -> str:
    """Best-effort: follow redirects, return clean full-body text. '' on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        text = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
        return text or ""
    except Exception as e:
        print(f"  ! fetch failed: {e}")
        return ""


def load_seen_links(path: str) -> set[str]:
    """Links already logged, so we never write the same article twice."""
    seen = set()
    p = Path(path)
    if p.exists():
        with p.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row.get("Article Source", ""))
    return seen


def append_rows(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    exists = Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def main() -> None:
    seen = load_seen_links(OUTPUT_FILE)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_HOURS)
    new_rows: list[dict] = []
    kept = 0

    for feed_url in FEEDS:
        print(f"Feed: {feed_url}")
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries:
            if kept >= MAX_ARTICLES_PER_RUN:
                break

            link = entry.get("link", "")
            if not link or link in seen:
                continue

            # date filter
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub:
                pub_dt = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
                if pub_dt < cutoff:
                    continue
            else:
                pub_dt = dt.datetime.now(dt.timezone.utc)

            title = (entry.get("title") or "").strip()
            body = fetch_article_text(link)
            # what we keyword-match against: title + full body (or RSS summary if body failed)
            haystack = title + " " + (body or strip_html(entry.get("summary", "")))

            if KEYWORDS:
                matches = keyword_hits(haystack, KEYWORDS)
                if not matches:
                    continue
            else:
                matches = []

            if body:
                summary = body[:1000] + ("…" if len(body) > 1000 else "")
            else:
                summary = strip_html(entry.get("summary", ""))[:1000]

            new_rows.append({
                "Date": pub_dt.strftime("%Y-%m-%d %H:%M"),
                "Article Name": title,
                "Article Source": link,
                "Key Phrases": ", ".join(matches),
                "Summary": summary,
            })
            seen.add(link)
            kept += 1
            print(f"  ✓ kept: {title[:70]}")

    append_rows(OUTPUT_FILE, new_rows)
    print(f"\nDone. {len(new_rows)} new article(s) added to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
