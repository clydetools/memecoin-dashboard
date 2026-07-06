"""
Flags individual large buys/sells relative to a token's pool size —
whether that's the pump.fun bonding curve (pre-migration) or DEX
liquidity (post-migration).

Consumes trade events from pumpfun_feed.py (PumpPortal's schema — see
that module for field assumptions/caveats).
"""

from config import MEMECOIN_WHALE_TRADE_PCT_OF_LIQUIDITY


def check_bonding_curve_trade(trade_event):
    """
    trade_event: a dict from PumpPortal's trade stream, expected fields
    (per PumpPortal's documented schema): txType ("buy"/"sell"),
    solAmount, traderPublicKey, mint, vSolInBondingCurve.

    Returns a flag string if this trade is large relative to the current
    bonding curve size, else None.
    """
    sol_amount = trade_event.get("solAmount")
    pool_sol = trade_event.get("vSolInBondingCurve")
    if not sol_amount or not pool_sol:
        return None

    pct = sol_amount / pool_sol * 100
    if pct >= MEMECOIN_WHALE_TRADE_PCT_OF_LIQUIDITY:
        direction = trade_event.get("txType", "trade")
        trader = trade_event.get("traderPublicKey", "unknown")
        return (
            f"Whale {direction}: {sol_amount:.2f} SOL ({pct:.1f}% of bonding curve pool) "
            f"by {trader[:8]}..."
        )
    return None


def check_dex_trade(sol_or_usd_amount, pool_liquidity_usd, direction="trade", trader="unknown"):
    """Same idea, for post-migration DEX trades, if/when an individual
    trade-size feed for the DEX pool is available (DexScreener's public
    API only gives aggregated tx counts, not per-trade size — this is
    here for when a trade-level feed, e.g. via PumpPortal's Raydium
    coverage or an RPC log subscription, supplies one)."""
    if not sol_or_usd_amount or not pool_liquidity_usd:
        return None
    pct = sol_or_usd_amount / pool_liquidity_usd * 100
    if pct >= MEMECOIN_WHALE_TRADE_PCT_OF_LIQUIDITY:
        return (
            f"Whale {direction}: ${sol_or_usd_amount:,.0f} ({pct:.1f}% of pool liquidity) "
            f"by {trader[:8] if trader != 'unknown' else trader}..."
        )
    return None
