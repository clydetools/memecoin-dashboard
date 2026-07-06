"""
Logs every rug-filter verdict (worth watching vs filtered out) with a
price snapshot, so accuracy can be checked back later: did "worth
watching" coins actually pump? Did "filtered out" coins actually rug,
or did the filter throw out a winner?
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

from dex_data import get_best_pair
from config import (
    MEMECOIN_ACCURACY_LOG_FILE,
    MEMECOIN_ACCURACY_MIN_AGE_HOURS,
    MEMECOIN_ACCURACY_LOOKBACK_HOURS,
    MEMECOIN_ACCURACY_PUMP_THRESHOLD_PCT,
    MEMECOIN_ACCURACY_RUG_THRESHOLD_PCT,
)


def log_assessment(a, source="unspecified"):
    """Appends one record for a build_full_assessment() result. Skips
    logging if there's no baseline price yet (e.g. pre-migration tokens
    with no DEX pair) since accuracy can't be measured without one."""
    price_usd = a.get("price_usd")
    if price_usd is None:
        return

    record = {
        "mint": a["mint"],
        "symbol": a.get("symbol"),
        "name": a.get("name"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "price_usd": price_usd,
        "verdict": "filtered_out" if a["high_risk"] else "worth_watching",
        "top_flags": (a.get("flags") or [])[:3],
        "source": source,
    }
    try:
        with open(MEMECOIN_ACCURACY_LOG_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[warn] failed to write accuracy log: {e}", file=sys.stderr)


def _read_log():
    if not os.path.exists(MEMECOIN_ACCURACY_LOG_FILE):
        return []
    records = []
    try:
        with open(MEMECOIN_ACCURACY_LOG_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"[warn] failed to read accuracy log: {e}", file=sys.stderr)
    return records


def _classify_outcome(entry_price, current_price):
    if current_price is None:
        return "delisted/no data"
    if not entry_price:
        return "unknown (bad baseline)"
    change_pct = (current_price - entry_price) / entry_price * 100
    if change_pct >= MEMECOIN_ACCURACY_PUMP_THRESHOLD_PCT:
        return f"pumped ({change_pct:+.0f}%)"
    if change_pct <= MEMECOIN_ACCURACY_RUG_THRESHOLD_PCT:
        return f"rugged/crashed ({change_pct:+.0f}%)"
    return f"flat ({change_pct:+.0f}%)"


def check_accuracy():
    """
    Re-fetches current price for each unique mint logged within the
    lookback window (excluding anything too fresh to have moved yet),
    classifies the outcome, and summarizes "worth watching" vs "filtered
    out" accuracy separately.
    """
    records = _read_log()
    now = datetime.now(timezone.utc)
    cutoff_old_enough = now - timedelta(hours=MEMECOIN_ACCURACY_MIN_AGE_HOURS)
    cutoff_lookback = now - timedelta(hours=MEMECOIN_ACCURACY_LOOKBACK_HOURS)

    seen_mints = set()
    eligible = []
    for r in records:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
        except (KeyError, ValueError):
            continue
        if not (cutoff_lookback <= ts <= cutoff_old_enough):
            continue
        if r["mint"] in seen_mints:
            continue
        seen_mints.add(r["mint"])
        eligible.append(r)

    if not eligible:
        return (
            f"No logged coins old enough to check yet within the last "
            f"{MEMECOIN_ACCURACY_LOOKBACK_HOURS}h (need at least "
            f"{MEMECOIN_ACCURACY_MIN_AGE_HOURS}h since flagging), or the log is still empty. "
            f"Run some scans/movers/chart checks first — they log automatically."
        )

    results = {"worth_watching": [], "filtered_out": []}
    for r in eligible:
        pair = get_best_pair(r["mint"])
        current_price = None
        if pair and pair.get("priceUsd") not in (None, ""):
            try:
                current_price = float(pair["priceUsd"])
            except (TypeError, ValueError):
                current_price = None
        outcome = _classify_outcome(r["price_usd"], current_price)
        results.setdefault(r.get("verdict", "worth_watching"), []).append((r, outcome))

    lines = [
        f"Accuracy check — {len(eligible)} unique coins from the last "
        f"{MEMECOIN_ACCURACY_LOOKBACK_HOURS}h (min {MEMECOIN_ACCURACY_MIN_AGE_HOURS}h old):\n"
    ]

    for verdict, label in [
        ("worth_watching", "WORTH WATCHING (did these pump?)"),
        ("filtered_out", "FILTERED OUT (did these rug, or did we filter a winner?)"),
    ]:
        entries = results.get(verdict, [])
        pumped = sum(1 for _, o in entries if o.startswith("pumped"))
        rugged = sum(1 for _, o in entries if o.startswith("rugged"))
        lines.append(f"\n=== {label} — {len(entries)} ===")
        if entries:
            lines.append(f"  Pumped: {pumped} | Rugged/crashed: {rugged} | Other: {len(entries) - pumped - rugged}")
        for r, outcome in entries:
            symbol = r.get("symbol") or r["mint"]
            lines.append(f"  {symbol}: {outcome} (flagged {r['timestamp']}, source={r.get('source')})")

    return "\n".join(lines)
