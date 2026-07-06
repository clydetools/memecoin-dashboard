"""
Chart-pattern heuristics for memecoins, using DexScreener's multi-timeframe
price-change/volume/tx fields (not raw OHLC candles — DexScreener's public
API doesn't expose those, so these are approximations based on the
timeframe deltas and buy/sell tx counts that ARE available).
"""

from config import (
    MEMECOIN_PARABOLIC_H1_PCT,
    MEMECOIN_BLEED_AFTER_PUMP_H24_PCT,
    MEMECOIN_BLEED_AFTER_PUMP_H1_PCT,
)


def analyze_chart_shape(pair):
    """
    Takes a DexScreener pair dict and returns a list of human-readable
    pattern flags (empty list = nothing notable detected).
    """
    if not pair:
        return []

    flags = []
    price_change = pair.get("priceChange", {}) or {}
    txns = pair.get("txns", {}) or {}

    h1 = price_change.get("h1")
    h6 = price_change.get("h6")
    h24 = price_change.get("h24")
    m5 = price_change.get("m5")

    def sell_pressure_ratio(tf):
        t = txns.get(tf, {})
        buys, sells = t.get("buys", 0), t.get("sells", 0)
        total = buys + sells
        return sells / total if total else None

    # Parabolic pump with no consolidation: huge 1h move stacked directly
    # on top of a huge 6h move (no pullback/basing in between).
    if h1 is not None and h6 is not None and h1 >= MEMECOIN_PARABOLIC_H1_PCT and h6 >= MEMECOIN_PARABOLIC_H1_PCT:
        flags.append(
            f"Parabolic, no consolidation: +{h1:.0f}% (1h) stacked on +{h6:.0f}% (6h) — unsustainable pump shape"
        )

    # Bleed after pump: big 24h gain, but reversing hard on the 1h —
    # classic "pump then distribute into late buyers" shape.
    if (
        h24 is not None
        and h1 is not None
        and h24 >= MEMECOIN_BLEED_AFTER_PUMP_H24_PCT
        and h1 <= MEMECOIN_BLEED_AFTER_PUMP_H1_PCT
    ):
        flags.append(
            f"Bleed-after-pump: +{h24:.0f}% on 24h now reversing {h1:.0f}% on 1h — looks like distribution, not a dip-buy"
        )

    # Sell-pressure ratio rising sharply short-term vs longer-term —
    # proxy for "selling into strength" (can't see wicks directly, but a
    # jump in the sell-side share of recent tx count is the closest signal
    # available from this API).
    r5 = sell_pressure_ratio("m5")
    r1h = sell_pressure_ratio("h1")
    if r5 is not None and r1h is not None and r5 - r1h >= 0.25:
        flags.append(
            f"Rising sell pressure: {r5:.0%} of last-5m txns are sells vs {r1h:.0%} over the last hour — possible top forming"
        )

    if m5 is not None and h1 is not None and m5 <= -20 and h1 > 0:
        flags.append(f"Sudden 5m drop ({m5:+.0f}%) against an otherwise positive 1h — could be a large sell hitting thin liquidity")

    return flags


def volume_liquidity_ratio_flag(pair, ratio_threshold=5.0):
    """Flags when 24h volume is many multiples of pool liquidity — thin
    liquidity relative to reported activity is easy to manipulate/wash-trade."""
    if not pair:
        return None
    liq = (pair.get("liquidity") or {}).get("usd")
    vol24 = (pair.get("volume") or {}).get("h24")
    if not liq or vol24 is None:
        return None
    ratio = vol24 / liq
    if ratio >= ratio_threshold:
        return f"Volume/liquidity ratio {ratio:.1f}x (24h vol ${vol24:,.0f} vs ${liq:,.0f} liquidity) — thin liquidity, easy to move/wash-trade"
    return None
