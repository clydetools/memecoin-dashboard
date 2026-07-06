"""
RugCheck.xyz wrapper — on-chain security checks for Solana tokens:
mint/freeze authority, LP lock status, top-holder concentration, and
RugCheck's own aggregated risk score/risk list.

Free public API, no key required for report lookups.
"""

import sys
import requests

from config import (
    RUGCHECK_API_BASE,
    MEMECOIN_MIN_LP_LOCKED_PCT,
    MEMECOIN_MAX_TOP10_HOLDER_PCT,
    MEMECOIN_DEV_HOLDING_PCT_THRESHOLD,
)


def get_rug_report(mint):
    """Fetch RugCheck's report for a token mint address. Returns None if
    the token isn't indexed yet (common for very fresh pump.fun tokens)."""
    try:
        resp = requests.get(f"{RUGCHECK_API_BASE}/tokens/{mint}/report", timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[warn] RugCheck fetch failed for {mint}: {e}", file=sys.stderr)
        return None


def _max_lp_locked_pct(report):
    markets = report.get("markets") or []
    pcts = [m.get("lp", {}).get("lpLockedPct", 0) or 0 for m in markets]
    return max(pcts) if pcts else 0.0


def _top_holder_pct(report, n=10):
    holders = report.get("topHolders") or []
    non_insider = [h for h in holders if not h.get("insider")]
    return sum(h.get("pct", 0) for h in non_insider[:n])


def assess_rug_risk(mint, migrated=True):
    """
    Runs a token through the rug-risk checks and returns a dict:
      {
        "mint": ..., "available": bool,
        "flags": [list of human-readable risk reasons],
        "score": RugCheck's own score (higher = riskier, per their convention),
        "lp_locked_pct": float, "top10_holder_pct": float,
        "mint_authority_active": bool, "freeze_authority_active": bool,
        "high_risk": bool,   # True if any hard-fail flag tripped
      }
    "available": False means RugCheck hasn't indexed this token yet (too new) —
    treat as unknown risk, not as "safe".

    `migrated` should be False for tokens still on pump.fun's bonding curve:
    pre-migration, RugCheck's "top holders" list is dominated by the bonding
    curve program's own escrow (typically ~99% of unsold supply) — that's
    normal, not a concentration risk, so the holder-concentration check is
    skipped (but still computed/returned) until the token has a real market.
    Same logic applies to LP-lock: there's no AMM pool yet, so "0% locked"
    pre-migration is meaningless, not a red flag.
    """
    report = get_rug_report(mint)
    if report is None:
        return {
            "mint": mint,
            "available": False,
            "flags": ["Not yet indexed by RugCheck — too new to assess, treat as unknown risk"],
            "score": None,
            "lp_locked_pct": None,
            "top10_holder_pct": None,
            "mint_authority_active": None,
            "freeze_authority_active": None,
            "high_risk": False,
        }

    flags = []

    token = report.get("token") or {}
    mint_authority_active = token.get("mintAuthority") is not None
    freeze_authority_active = token.get("freezeAuthority") is not None
    if mint_authority_active:
        flags.append("Mint authority still active — supply can be inflated arbitrarily")
    if freeze_authority_active:
        flags.append("Freeze authority still active — holder accounts can be frozen")

    lp_locked_pct = _max_lp_locked_pct(report)
    if migrated and lp_locked_pct < MEMECOIN_MIN_LP_LOCKED_PCT:
        flags.append(
            f"Only {lp_locked_pct:.1f}% of LP locked (threshold {MEMECOIN_MIN_LP_LOCKED_PCT}%) — liquidity can be pulled"
        )

    top10_pct = _top_holder_pct(report)
    if migrated and top10_pct > MEMECOIN_MAX_TOP10_HOLDER_PCT:
        flags.append(
            f"Top 10 non-insider holders control {top10_pct:.1f}% (threshold {MEMECOIN_MAX_TOP10_HOLDER_PCT}%) — dump risk"
        )

    creator_balance_pct = None
    supply = token.get("supply")
    creator_balance = report.get("creatorBalance")
    if supply and creator_balance is not None and supply > 0:
        creator_balance_pct = creator_balance / supply * 100
        if creator_balance_pct > MEMECOIN_DEV_HOLDING_PCT_THRESHOLD:
            flags.append(
                f"Creator/deployer still holds {creator_balance_pct:.1f}% of supply "
                f"(threshold {MEMECOIN_DEV_HOLDING_PCT_THRESHOLD}%) — easy dump risk"
            )

    for risk in report.get("risks") or []:
        name = risk.get("name")
        if name and name not in " ".join(flags):
            flags.append(f"RugCheck flag: {name} (score {risk.get('score')})")

    high_risk = mint_authority_active or freeze_authority_active or (
        migrated and lp_locked_pct < MEMECOIN_MIN_LP_LOCKED_PCT
    )

    return {
        "mint": mint,
        "available": True,
        "flags": flags,
        "score": report.get("score"),
        "lp_locked_pct": lp_locked_pct,
        "top10_holder_pct": top10_pct,
        "creator_balance_pct": creator_balance_pct,
        "mint_authority_active": mint_authority_active,
        "freeze_authority_active": freeze_authority_active,
        "high_risk": high_risk,
    }
