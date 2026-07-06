"""
Generates the public memecoin dashboard (dashboard.html) from
dashboard_template.html with a fresh live snapshot: general market pulse,
memecoin new launches, trending movers by timeframe, and accumulated
accuracy stats. Meant to run in GitHub Actions on a schedule — stateless
per run except for memecoin_accuracy_log.jsonl, which is committed back
to the repo each time so accuracy history accumulates for real.
"""

import sys
import json
import asyncio
from datetime import datetime, timezone, timedelta

from fetch_market import get_market_movers, get_global_snapshot
from pumpfun_feed import PumpFunWatcher
from trending_movers import get_trending_by_timeframe
from memecoin_agent import build_full_assessment, assess_many
import accuracy_log
from config import (
    MEMECOIN_CHART_TIMEFRAMES,
    MEMECOIN_CHART_LIMIT_PER_TIMEFRAME,
    MEMECOIN_ACCURACY_MIN_AGE_HOURS,
    MEMECOIN_ACCURACY_LOOKBACK_HOURS,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

TEMPLATE_FILE = "dashboard_template.html"
OUTPUT_FILE = "dashboard.html"
MARKER = "/*__MEMECOIN_DASHBOARD_DATA__*/{}"

NEW_LAUNCH_SCAN_SECONDS = 40


def gather_market_pulse():
    movers = get_market_movers()
    global_data = get_global_snapshot()

    def top(rows, n=5):
        return [
            {
                "symbol": c["symbol"],
                "change_pct": c.get("change_24h"),
                "price": c.get("price"),
            }
            for c in rows[:n]
        ]

    return {
        "total_mcap": (global_data.get("total_market_cap") or {}).get("usd"),
        "mcap_change_24h": global_data.get("market_cap_change_percentage_24h_usd"),
        "btc_dominance": (global_data.get("market_cap_percentage") or {}).get("btc"),
        "gainers_24h": top(movers.get("gainers_24h", [])),
        "losers_24h": top(movers.get("losers_24h", [])),
    }


async def gather_new_launches():
    watcher = PumpFunWatcher()
    launches = await watcher.collect_launches(NEW_LAUNCH_SCAN_SECONDS)
    assessments = [build_full_assessment(l, source="public_dashboard_new") for l in launches]
    rows = []
    for a in assessments:
        rows.append({
            "mint": a["mint"],
            "symbol": a["symbol"],
            "name": a["name"],
            "verdict": "RISK" if a["high_risk"] else "WATCH",
            "migrated": a["migrated"],
            "top_flag": a["flags"][0] if a["flags"] else "No flags detected",
            "flag_count": len(a["flags"]),
            "all_flags": a["flags"],
        })
    return rows


def gather_movers_by_timeframe():
    by_tf = get_trending_by_timeframe(MEMECOIN_CHART_TIMEFRAMES, MEMECOIN_CHART_LIMIT_PER_TIMEFRAME)
    all_summaries = [
        {"mint": p["mint"], "name": p["name"], "symbol": p["symbol"], "flags": []}
        for pools in by_tf.values()
        for p in pools
    ]
    assessed = assess_many(all_summaries, source="public_dashboard_movers")

    out = {}
    for d, pools in by_tf.items():
        rows = []
        for p in pools:
            a = assessed.get(p["mint"])
            if not a:
                continue
            rows.append({
                "mint": p["mint"],
                "symbol": a["symbol"],
                "name": a["name"],
                "verdict": "RISK" if a["high_risk"] else "WATCH",
                "change_pct": p.get("price_change_pct"),
                "volume_usd": p.get("volume_usd"),
                "top_flag": a["flags"][0] if a["flags"] else "No flags detected",
                "flag_count": len(a["flags"]),
                "all_flags": a["flags"],
                "migrated": a["migrated"],
            })
        out[d] = rows
    return out


def gather_accuracy_stats():
    records = accuracy_log._read_log()
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

    from dex_data import get_best_pair

    summary = {"worth_watching": {"pumped": 0, "rugged": 0, "flat": 0}, "filtered_out": {"pumped": 0, "rugged": 0, "flat": 0}}
    total_checked = 0
    for r in eligible[:80]:  # cap live re-checks per run to keep workflow duration reasonable
        pair = get_best_pair(r["mint"])
        current_price = None
        if pair and pair.get("priceUsd") not in (None, ""):
            try:
                current_price = float(pair["priceUsd"])
            except (TypeError, ValueError):
                current_price = None
        outcome = accuracy_log._classify_outcome(r.get("price_usd"), current_price)
        verdict = r.get("verdict", "worth_watching")
        bucket = summary.setdefault(verdict, {"pumped": 0, "rugged": 0, "flat": 0})
        if outcome.startswith("pumped"):
            bucket["pumped"] += 1
        elif outcome.startswith("rugged"):
            bucket["rugged"] += 1
        else:
            bucket["flat"] += 1
        total_checked += 1

    return {"total_checked": total_checked, "total_logged": len(records), "summary": summary}


async def main():
    print("Gathering general market pulse...", file=sys.stderr)
    market_pulse = gather_market_pulse()

    print(f"Watching pump.fun for {NEW_LAUNCH_SCAN_SECONDS}s (new launches)...", file=sys.stderr)
    new_launches = await gather_new_launches()

    print("Fetching trending movers by timeframe...", file=sys.stderr)
    movers = gather_movers_by_timeframe()

    print("Computing accuracy stats...", file=sys.stderr)
    accuracy = gather_accuracy_stats()

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_pulse": market_pulse,
        "new_launches": new_launches,
        "movers": movers,
        "accuracy": accuracy,
    }

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template = f.read()

    if MARKER not in template:
        print(f"[error] marker not found in {TEMPLATE_FILE}", file=sys.stderr)
        sys.exit(1)

    output = template.replace(MARKER, json.dumps(snapshot, ensure_ascii=False))
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)
    # index.html is the actual page Cloudflare/GitHub Pages serve at the
    # site root — keep it identical to dashboard.html on every run.
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)

    print(f"Wrote {OUTPUT_FILE} + index.html: {len(new_launches)} new launches, "
          f"{sum(len(v) for v in movers.values())} mover rows, "
          f"{accuracy['total_checked']} accuracy re-checks.")


if __name__ == "__main__":
    asyncio.run(main())
