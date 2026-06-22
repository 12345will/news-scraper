#!/usr/bin/env python3
"""
Daily news scraper.

Reads one or more RSS feeds, including Google News query feeds, fetches each
article, extracts the full body text, keeps the ones that match your keywords,
and appends new results to news_log.csv.

It also creates/updates news_log.xlsx so the results can be opened directly
in Excel.
"""

import csv
import re
import datetime as dt
import urllib.parse
from pathlib import Path

import feedparser
import requests
import trafilatura
import pandas as pd

# ============================== CONFIG =================================
# Companies / topics to track. Each line becomes a Google News search over the
# last 24 hours. Add or remove freely. Wrap multi-word names in single quotes
# with inner double quotes to force an exact phrase, e.g. '"Siemens Gamesa"'.
# Append a word like "wind" to disambiguate common names.
GOOGLE_NEWS_QUERIES = [
    # --- Turbine manufacturers (OEMs) ---
    "Vestas wind",
    '"GE Vernova" wind',
    "Goldwind",
    '"Envision Energy" wind',
    "Mingyang wind",
    "Nordex wind",
    "Enercon wind",
    "Suzlon wind",

    # --- Developers / operators ---
    "Orsted offshore wind",
    "RWE wind",
    "Iberdrola wind",
    '"NextEra Energy" wind',
    "Equinor wind",

    # --- Extras: uncomment to widen coverage ---
    # '"EDP Renewables" wind',
    # "Engie wind",
    # "Acciona wind",
    # '"Dongfang Electric" wind',
    # '"Shanghai Electric" wind',
    # "Windey wind turbine",
    # "Sany wind turbine",
    # "offshore wind farm order",
]

# Plain publisher RSS feeds.
EXTRA_FEEDS = [
    # "https://www.windpowermonthly.com/rss",
    # "https://www.technologyreview.com/feed/",
]

# An article is kept if its text contains ANY of these words.
# Leave as [] to keep everything.
KEYWORDS = [
    "order", "orders", "MW", "GW", "gigawatt", "megawatt",
    "offshore", "onshore", "floating", "turbine", "nacelle", "blade",
    "contract", "repowering", "prototype", "factory", "facility",
    "acquisition", "partnership", "joint venture", "supply",
]

MAX_ARTICLES_PER_RUN = 50
LOOKBACK_HOURS = 26

OUTPUT_FILE = "news_log.csv"
EXCEL_FILE = "news_log.xlsx"

# Any article whose title or body contains one of these is dropped.
# Leave as [] to disable.
EXCLUDE_KEYWORDS = []
# ======================================================================


def _google_news_feed(query: str) -> str:
    """Build a 'last 24 hours' Google News RSS search URL from a plain query."""
    q = urllib.parse.quote_plus(query + " when:1d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


FEEDS = [_google_news_feed(q) for q in GOOGLE_NEWS_QUERIES] + EXTRA_FEEDS

REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-scraper/1.0)"}
FIELDS = ["Date", "Article Name", "Article Source", "Key Phrases", "Summary"]


def strip_html(s: str) -> str:
    """Crude HTML-tag remover for RSS summary fallbacks."""
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Return the keywords that appear as whole words in text."""
    hits = []
    for k in keywords:
        if re.search(r"\b" + re.escape(k) + r"\b", text, flags=re.IGNORECASE):
            hits.append(k)
    return hits


def fetch_article_text(url: str) -> str:
    """Best-effort: follow redirects and return clean full-body text. Empty string on failure."""
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
        )
        return text or ""
    except Exception as e:
        print(f"  ! fetch failed: {e}")
        return ""


def load_seen_links(path: str) -> set[str]:
    """Load links already logged so the same article is not written twice."""
    seen = set()
    p = Path(path)

    if p.exists():
        with p.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row.get("Article Source", ""))

    return seen


def append_rows(path: str, rows: list[dict]) -> None:
    """Append new article rows to the CSV file."""
    if not rows:
        return

    exists = Path(path).exists()

    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)

        if not exists:
            w.writeheader()

        w.writerows(rows)


def update_excel_from_csv(csv_path: str, excel_path: str) -> None:
    """Create or update an Excel file from the CSV log."""
    p = Path(csv_path)

    if not p.exists():
        print(f"No CSV file found at {csv_path}. Excel file not updated.")
        return

    df = pd.read_csv(csv_path)

    # Keep the columns in the intended order.
    df = df[FIELDS]

    df.to_excel(excel_path, index=False)
    print(f"Excel file updated: {excel_path}")


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

            # Date filter.
            pub = entry.get("published_parsed") or entry.get("updated_parsed")

            if pub:
                pub_dt = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)

                if pub_dt < cutoff:
                    continue
            else:
                pub_dt = dt.datetime.now(dt.timezone.utc)

            title = (entry.get("title") or "").strip()
            body = fetch_article_text(link)

            # Keyword match against title + full body, or RSS summary if body failed.
            haystack = title + " " + (body or strip_html(entry.get("summary", "")))

            # Hard exclusions.
            if EXCLUDE_KEYWORDS and keyword_hits(haystack, EXCLUDE_KEYWORDS):
                continue

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
    update_excel_from_csv(OUTPUT_FILE, EXCEL_FILE)

    print(f"\nDone. {len(new_rows)} new article(s) added to {OUTPUT_FILE}.")
    print(f"Excel file available at {EXCEL_FILE}.")


if __name__ == "__main__":
    main()
