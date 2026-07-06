"""
GeckoTerminal wrapper — discovers currently-trending Solana pools (already
launched, not brand new). Free public API, no key required.

This module only discovers WHICH mints are hot right now; the actual
rug-risk/chart-pattern analysis reuses the same DexScreener + RugCheck +
chart_patterns pipeline already built for new-launch scanning
(see memecoin_agent.build_full_assessment), so both "new launches" and
"existing movers" modes are judged by identical criteria.
"""

import sys
import time
import requests

from config import (
    GECKOTERMINAL_API_BASE,
    GECKOTERMINAL_NETWORK,
    MEMECOIN_TRENDING_LIMIT,
    MEMECOIN_EXCLUDED_MINTS,
)


def get_trending_pools(duration="1h", limit=MEMECOIN_TRENDING_LIMIT, max_retries=3):
    """
    duration: one of "5m", "1h", "6h", "24h" (GeckoTerminal's supported windows).
    Returns a list of dicts: {mint, symbol, name, pool_name, volume_usd,
    price_change_pct, reserve_usd, dex}.

    GeckoTerminal's free tier rate-limits bursts of requests (observed
    429s when hitting it 4x back to back for a multi-timeframe chart) —
    retries with backoff on 429 specifically.
    """
    data = []
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                f"{GECKOTERMINAL_API_BASE}/networks/{GECKOTERMINAL_NETWORK}/trending_pools",
                params={"duration": duration},
                headers={"Accept": "application/json;version=20230302"},
                timeout=20,
            )
            if resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"[warn] GeckoTerminal rate-limited, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json().get("data", [])
            break
        except requests.RequestException as e:
            print(f"[warn] GeckoTerminal trending_pools fetch failed: {e}", file=sys.stderr)
            return []
    else:
        print(f"[warn] GeckoTerminal still rate-limited after {max_retries} retries, giving up for duration={duration}", file=sys.stderr)
        return []

    results = []
    seen_mints = set()
    for pool in data:
        if len(results) >= limit:
            break

        attrs = pool.get("attributes", {})
        rels = pool.get("relationships", {})
        base_token_id = (rels.get("base_token", {}).get("data") or {}).get("id", "")
        mint = base_token_id.split("_", 1)[-1] if "_" in base_token_id else base_token_id
        if not mint or mint in MEMECOIN_EXCLUDED_MINTS or mint in seen_mints:
            continue
        seen_mints.add(mint)

        pool_name = attrs.get("name", "")
        symbol_guess = pool_name.split(" / ")[0].strip() if " / " in pool_name else pool_name

        results.append({
            "mint": mint,
            "symbol": symbol_guess,
            "name": symbol_guess,
            "pool_name": pool_name,
            "volume_usd": _to_float((attrs.get("volume_usd") or {}).get(_duration_key(duration))),
            "price_change_pct": _to_float((attrs.get("price_change_percentage") or {}).get(_duration_key(duration))),
            "reserve_usd": _to_float(attrs.get("reserve_in_usd")),
            "dex": (rels.get("dex", {}).get("data") or {}).get("id"),
        })
    return results


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_key(duration):
    # GeckoTerminal's timeframe keys use m/h without a leading zero,
    # matching the duration param values directly for our supported set.
    return {"5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24"}.get(duration, "h1")


def get_trending_by_timeframe(durations, limit_per_duration, pause_between_calls=1.2):
    """
    Fetches trending pools separately for each duration in `durations`
    (each is its own independent trending ranking from GeckoTerminal, not
    a slice of one shared list). Returns {duration: [pool dicts]}.

    Pauses briefly between calls to stay under GeckoTerminal's free-tier
    burst rate limit (in addition to get_trending_pools' own retry logic).
    """
    result = {}
    for i, d in enumerate(durations):
        if i > 0:
            time.sleep(pause_between_calls)
        result[d] = get_trending_pools(duration=d, limit=limit_per_duration)
    return result
