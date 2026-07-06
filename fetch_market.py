"""
Pulls short-term price momentum (1h, 4h, 24h) for coins so you can catch
trends early instead of waiting for a 24h move to show up.

No API key needed (CoinGecko free tier, rate limited to ~10-30 calls/min).

How the 4h number works: CoinGecko's free /coins/markets endpoint natively
gives 1h/24h/7d change, but not 4h. Rather than making one extra API call
per coin (slow, burns through the free rate limit fast), we request the
7-day hourly "sparkline" array in the same call and compute the 4h change
from its last 4 data points. One request, three timeframes.
"""

import requests
from config import TOP_N_MOVERS, MOVERS_SCAN_SIZE

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def _pct_change(old, new):
    if old in (None, 0):
        return None
    return (new - old) / old * 100


def get_market_movers(vs_currency="usd"):
    """
    Fetches the top MOVERS_SCAN_SIZE coins by market cap with 1h/24h/7d
    change plus a 7-day hourly sparkline, derives a 4h change from the
    sparkline, then returns top gainers/losers for each of 1h, 4h, 24h.
    """
    url = f"{COINGECKO_BASE}/coins/markets"
    params = {
        "vs_currency": vs_currency,
        "order": "market_cap_desc",
        "per_page": MOVERS_SCAN_SIZE,
        "page": 1,
        "price_change_percentage": "1h,24h,7d",
        "sparkline": "true",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    coins = resp.json()

    enriched = []
    for c in coins:
        spark = (c.get("sparkline_in_7d") or {}).get("price") or []
        change_4h = None
        if len(spark) >= 5:
            change_4h = _pct_change(spark[-5], spark[-1])  # 4 hourly steps back

        enriched.append({
            "symbol": c["symbol"].upper(),
            "name": c["name"],
            "price": c["current_price"],
            "market_cap": c.get("market_cap"),
            "change_1h": c.get("price_change_percentage_1h_in_currency"),
            "change_4h": change_4h,
            "change_24h": c.get("price_change_percentage_24h"),
        })

    def top(field, n=TOP_N_MOVERS, reverse=True):
        valid = [c for c in enriched if c.get(field) is not None]
        return sorted(valid, key=lambda c: c[field], reverse=reverse)[:n]

    return {
        "gainers_1h": top("change_1h"),
        "losers_1h": top("change_1h", reverse=False),
        "gainers_4h": top("change_4h"),
        "losers_4h": top("change_4h", reverse=False),
        "gainers_24h": top("change_24h"),
        "losers_24h": top("change_24h", reverse=False),
    }


def get_global_snapshot():
    """Total market cap, 24h volume, BTC dominance, etc."""
    resp = requests.get(f"{COINGECKO_BASE}/global", timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {})


def _fmt_pct(v):
    return f"{v:+.2f}%" if v is not None else "n/a"


def format_movers_for_prompt(movers):
    def line(c):
        return (
            f"{c['symbol']}: ${c['price']:,} | "
            f"1h {_fmt_pct(c['change_1h'])} | "
            f"4h {_fmt_pct(c['change_4h'])} | "
            f"24h {_fmt_pct(c['change_24h'])} | "
            f"mcap ${c['market_cap']:,}" if c['market_cap'] else ""
        )

    sections = []
    for label, key in [("1H", "1h"), ("4H", "4h"), ("24H", "24h")]:
        gainers = "\n".join(line(c) for c in movers[f"gainers_{key}"])
        losers = "\n".join(line(c) for c in movers[f"losers_{key}"])
        sections.append(
            f"TOP GAINERS ({label}):\n{gainers}\n\nTOP LOSERS ({label}):\n{losers}"
        )
    return "\n\n".join(sections)


if __name__ == "__main__":
    movers = get_market_movers()
    print(format_movers_for_prompt(movers))
