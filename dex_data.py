"""
DexScreener wrapper — post-migration DEX pair data (price, volume,
liquidity, tx counts, price change across timeframes). Free, keyless API.
"""

import sys
import requests

from config import DEXSCREENER_API_BASE


def get_pairs_for_token(mint):
    """Returns all known DEX pairs for a token mint address (usually just
    one on Solana/Raydium for a pump.fun graduate), or [] if not found
    (token hasn't migrated off the bonding curve yet, or isn't indexed)."""
    try:
        resp = requests.get(f"{DEXSCREENER_API_BASE}/tokens/{mint}", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("pairs") or []
    except requests.RequestException as e:
        print(f"[warn] DexScreener fetch failed for {mint}: {e}", file=sys.stderr)
        return []


def get_best_pair(mint):
    """Returns the highest-liquidity pair for a token, or None."""
    pairs = get_pairs_for_token(mint)
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0)


def social_presence(pair):
    """
    Returns {"has_socials": bool, "websites": int, "socials": int} from
    DexScreener's "info" field (populated for migrated tokens with a
    filled-out pump.fun profile). No socials/website at all is a common
    low-effort/spam signal.
    """
    if not pair:
        return {"has_socials": False, "websites": 0, "socials": 0}
    info = pair.get("info") or {}
    websites = len(info.get("websites") or [])
    socials = len(info.get("socials") or [])
    return {"has_socials": (websites + socials) > 0, "websites": websites, "socials": socials}


def format_pair_summary(pair):
    if not pair:
        return "No DEX pair found (likely still pre-migration on pump.fun's bonding curve)."

    base = pair.get("baseToken", {})
    txns = pair.get("txns", {})
    volume = pair.get("volume", {})
    price_change = pair.get("priceChange", {})
    liq = (pair.get("liquidity") or {}).get("usd") or 0
    mcap = pair.get("marketCap") or 0

    def tf(label, key):
        t = txns.get(key, {})
        change = price_change.get(key)
        change_str = f"{change:+.1f}%" if change is not None else "n/a"
        return f"{label}: {change_str} | vol ${volume.get(key, 0):,.0f} | buys {t.get('buys', 0)}/sells {t.get('sells', 0)}"

    return (
        f"{base.get('symbol')} ({base.get('name')}) | ${pair.get('priceUsd')} | "
        f"mcap ${mcap:,.0f} | liquidity ${liq:,.0f}\n"
        f"  {tf('5m', 'm5')}\n  {tf('1h', 'h1')}\n  {tf('6h', 'h6')}\n  {tf('24h', 'h24')}"
    )
