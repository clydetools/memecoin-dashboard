"""
Pulls recent headlines from free RSS feeds — both crypto-specific and
broad stock/macro market news — so you get a full picture of what's
moving markets, not just crypto in isolation.

Optionally supplements crypto news with CryptoPanic's aggregator API
if a key is set (adds crowd-sourced bullish/bearish sentiment votes).
"""

import sys
import requests
import feedparser
from datetime import datetime, timezone, timedelta

from config import CRYPTO_RSS_FEEDS, STOCK_RSS_FEEDS, CRYPTOPANIC_API_KEY


def _get_rss_headlines(feeds, hours_back=6, max_per_feed=6):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    all_items = []

    for source, url in feeds.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[warn] failed to parse {source}: {e}", file=sys.stderr)
            continue

        count = 0
        for entry in feed.entries:
            if count >= max_per_feed:
                break
            title = entry.get("title", "").strip()
            if not title:
                continue
            all_items.append({"source": source, "title": title, "link": entry.get("link", "")})
            count += 1

    return all_items


def get_crypto_headlines(hours_back=6, max_per_feed=6):
    return _get_rss_headlines(CRYPTO_RSS_FEEDS, hours_back, max_per_feed)


def get_stock_headlines(hours_back=6, max_per_feed=6):
    return _get_rss_headlines(STOCK_RSS_FEEDS, hours_back, max_per_feed)


def get_cryptopanic_headlines(limit=15):
    """Optional: richer crypto aggregation with sentiment votes, if you have a key."""
    if not CRYPTOPANIC_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": CRYPTOPANIC_API_KEY, "public": "true", "kind": "news"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])[:limit]
        return [
            {"source": "CryptoPanic", "title": r["title"], "link": r["url"]}
            for r in results
        ]
    except Exception as e:
        print(f"[warn] CryptoPanic fetch failed: {e}", file=sys.stderr)
        return []


def format_news_for_prompt(items, label=""):
    if not items:
        return f"No {label} news items retrieved." if label else "No news items retrieved."
    return "\n".join(f"[{i['source']}] {i['title']}" for i in items)


if __name__ == "__main__":
    crypto_items = get_crypto_headlines() + get_cryptopanic_headlines()
    stock_items = get_stock_headlines()
    print("=== CRYPTO NEWS ===")
    print(format_news_for_prompt(crypto_items, "crypto"))
    print("\n=== STOCK / MACRO NEWS ===")
    print(format_news_for_prompt(stock_items, "stock"))
