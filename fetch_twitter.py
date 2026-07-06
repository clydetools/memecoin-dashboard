"""
Optional: pulls recent posts mentioning crypto tickers/keywords from X.

Requires X_BEARER_TOKEN in your environment. The free X API tier does NOT
include search access — you need at least the paid "Basic" tier
(https://developer.x.com/en/portal/products) to use recent search.

If no token is set, this module returns an empty list and the agent
just skips the social layer — everything else still works.
"""

import sys
import requests
from config import X_BEARER_TOKEN, WATCHED_X_ACCOUNTS

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


def get_recent_crypto_posts(query="crypto OR bitcoin OR ethereum", max_results=10):
    if not X_BEARER_TOKEN:
        return []

    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params = {
        "query": f"({query}) -is:retweet lang:en",
        "max_results": max_results,
        "tweet.fields": "created_at,public_metrics,author_id",
    }
    try:
        resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [{"text": t["text"], "metrics": t.get("public_metrics", {})} for t in data]
    except Exception as e:
        print(f"[warn] X search failed: {e}", file=sys.stderr)
        return []


def format_posts_for_prompt(posts):
    if not posts:
        return "No X/Twitter data available (set X_BEARER_TOKEN to enable)."
    return "\n".join(f"- {p['text'][:200]}" for p in posts)


if __name__ == "__main__":
    posts = get_recent_crypto_posts()
    print(format_posts_for_prompt(posts))
