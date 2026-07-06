"""
Memecoin (pump.fun / Solana DEX) mover + rug-filter watcher.
Read-only analysis only — never places trades.

Usage:
    python memecoin_agent.py scan                  # collect NEW launches for
                                                     # a window (default from
                                                     # config), then assess
                                                     # and filter
    python memecoin_agent.py scan --duration 120    # custom collection window
    python memecoin_agent.py watch                  # continuous real-time
                                                     # watch of new launches
                                                     # (Ctrl+C to stop)
    python memecoin_agent.py movers                  # scan currently-trending
                                                     # EXISTING tokens (already
                                                     # launched) instead of new ones
    python memecoin_agent.py movers --duration 24h   # trending window: 5m/1h/6h/24h
    python memecoin_agent.py watchlist add <mint> [--note "..."]
    python memecoin_agent.py watchlist remove <mint>
    python memecoin_agent.py watchlist list           # show pinned coins, no re-check
    python memecoin_agent.py watchlist check           # re-assess all pinned coins now
    python memecoin_agent.py alerts                    # single-pass check for
                                                        # push-worthy new launches
                                                        # + movers, posts to webhook
                                                        # if ALERT_WEBHOOK_URL set.
                                                        # Meant to be triggered on a
                                                        # schedule (Task Scheduler),
                                                        # not run as a long loop.
    python memecoin_agent.py accuracy                  # check back on past verdicts:
                                                        # did "worth watching" coins
                                                        # pump? did "filtered out" ones
                                                        # rug? Every scan/movers/chart/
                                                        # watchlist/alert run logs
                                                        # automatically.

Combines:
  - pumpfun_feed.py     — live launches + early-trade bundle/dev-holding detection
  - trending_movers.py  — discovers currently-trending EXISTING tokens (GeckoTerminal)
  - rugcheck.py         — mint/freeze authority, LP lock, holder concentration
  - dex_data.py         — post-migration DEX price/volume/liquidity
  - chart_patterns.py   — parabolic/bleed/sell-pressure heuristics
  - whale_watch.py      — large individual buy/sell detection
"""

import sys
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor

from pumpfun_feed import PumpFunWatcher
from trending_movers import get_trending_pools, get_trending_by_timeframe
from dex_data import get_best_pair, format_pair_summary, social_presence
from rugcheck import assess_rug_risk
from chart_patterns import analyze_chart_shape, volume_liquidity_ratio_flag
import watchlist as watchlist_store
import accuracy_log
import spam_filter
import holder_history
from memecoin_alerts import find_new_launch_alerts, find_mover_alerts, deliver_memecoin_alerts, format_memecoin_alert
from config import (
    MEMECOIN_SCAN_WINDOW_SECONDS,
    MEMECOIN_TRENDING_DURATION,
    MEMECOIN_CHART_TIMEFRAMES,
    MEMECOIN_CHART_LIMIT_PER_TIMEFRAME,
    MEMECOIN_ASSESS_CONCURRENCY,
    MEMECOIN_ALERT_SCAN_SECONDS,
    MEMECOIN_ALERT_MOVER_DURATION,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def build_full_assessment(launch_summary, source="unspecified"):
    """Combines pump.fun launch data with RugCheck + DexScreener + chart
    pattern checks into one assessment dict. Auto-logs the verdict (with
    a price snapshot) to the accuracy log, tagged with `source`, for
    later check-back — skipped automatically if there's no DEX price yet."""
    mint = launch_summary["mint"]
    flags = list(launch_summary.get("flags", []))

    copycat = spam_filter.check_and_register(launch_summary.get("symbol"), mint)
    is_copycat = copycat is not None
    if is_copycat:
        flags.append(
            f"Copycat spam: symbol already used by an earlier token ({copycat['original_mint']}, "
            f"first seen {copycat['first_seen']}) — likely riding the original's name"
        )

    pair = get_best_pair(mint)
    # DexScreener tracks pump.fun's bonding curve itself as a pair
    # (dexId "pumpfun") with $0 real liquidity — that's not a graduated
    # AMM market, so gate "migrated" on actual liquidity, not pair presence.
    migrated = pair is not None and ((pair.get("liquidity") or {}).get("usd") or 0) > 0

    rug = assess_rug_risk(mint, migrated=migrated)
    flags.extend(rug["flags"])

    if pair:
        flags.extend(analyze_chart_shape(pair))
        vl_flag = volume_liquidity_ratio_flag(pair)
        if vl_flag:
            flags.append(vl_flag)

        social = social_presence(pair)
        if migrated and not social["has_socials"]:
            flags.append("No website/social links found — common low-effort/spam signal")

    if migrated and rug.get("top10_holder_pct") is not None:
        trend = holder_history.record_and_get_trend(mint, rug["top10_holder_pct"])
        if trend:
            flags.append(trend)

    hard_fail = rug.get("high_risk", False) or is_copycat or any(
        "insider bundle" in f.lower() or "dev bought" in f.lower()
        for f in launch_summary.get("flags", [])
    )

    txns = (pair.get("txns") or {}) if pair else {}
    price_change = (pair.get("priceChange") or {}) if pair else {}
    price_usd = None
    if pair and pair.get("priceUsd") not in (None, ""):
        try:
            price_usd = float(pair["priceUsd"])
        except (TypeError, ValueError):
            price_usd = None

    result = {
        "mint": mint,
        "name": launch_summary.get("name"),
        "symbol": launch_summary.get("symbol"),
        "migrated": migrated,
        "has_dex_data": pair is not None,
        "dex_summary": format_pair_summary(pair) if pair else None,
        "price_usd": price_usd,
        "market_cap": (pair.get("marketCap") if pair else None),
        "price_change_pct": {tf: price_change.get(tf) for tf in ("m5", "h1", "h6", "h24")},
        "buys_h1": (txns.get("h1") or {}).get("buys"),
        "sells_h1": (txns.get("h1") or {}).get("sells"),
        "rug_available": rug.get("available"),
        "rug_score": rug.get("score"),
        "lp_unlock_date": rug.get("lp_unlock_date"),
        "creator_token_count": rug.get("creator_token_count", 0),
        "insider_network_count": rug.get("insider_network_count", 0),
        "has_socials": social_presence(pair)["has_socials"] if pair else None,
        "is_copycat": is_copycat,
        "flags": flags,
        "high_risk": hard_fail,
    }
    accuracy_log.log_assessment(result, source=source)
    return result


def format_assessment(a):
    verdict = "HIGH RISK - FILTER OUT" if a["high_risk"] else "worth watching"
    lines = [f"=== {a['symbol']} ({a['name']}) — {a['mint']} ===", f"Verdict: {verdict}"]
    if a["has_dex_data"]:
        lines.append(a["dex_summary"])
        if not a["migrated"]:
            lines.append("(pre-migration bonding curve pricing — no real AMM liquidity yet)")
    else:
        lines.append("Status: no DEX data yet")
    if a["flags"]:
        lines.append("Flags:")
        lines.extend(f"  - {f}" for f in a["flags"])
    else:
        lines.append("Flags: none detected")
    return "\n".join(lines)


def assess_many(launch_summaries, source="unspecified"):
    """
    Runs build_full_assessment concurrently over a batch (each call is
    independent blocking HTTP I/O — RugCheck + DexScreener lookups — so
    threading them keeps wall-clock time roughly flat as the batch grows
    instead of scaling linearly). Dedupes by mint, keeping the first
    occurrence's name/symbol.
    """
    by_mint = {}
    for l in launch_summaries:
        by_mint.setdefault(l["mint"], l)

    unique = list(by_mint.values())
    with ThreadPoolExecutor(max_workers=MEMECOIN_ASSESS_CONCURRENCY) as pool:
        results = list(pool.map(lambda l: build_full_assessment(l, source=source), unique))

    return {a["mint"]: a for a in results}


def format_assessment_compact(a, tf_change_pct=None, tf_volume_usd=None):
    """One-line chart-style row: SYMBOL | price | tf change | tf volume | verdict."""
    verdict = "RISK" if a["high_risk"] else "WATCH"
    change_str = f"{tf_change_pct:+.1f}%" if tf_change_pct is not None else "n/a"
    vol_str = f"${tf_volume_usd:,.0f}" if tf_volume_usd is not None else "n/a"
    top_flag = a["flags"][0] if a["flags"] else "no flags"
    return (
        f"[{verdict:5}] {a['symbol']:12} {change_str:>8}  vol {vol_str:>12}  "
        f"— {top_flag}  ({a['mint']})"
    )


def build_chart(durations=None, limit_per_timeframe=MEMECOIN_CHART_LIMIT_PER_TIMEFRAME):
    """
    Fetches trending pools separately per timeframe, assesses the full
    unique set once (deduped, concurrent), then renders a chart grouped
    by timeframe — more coins per section, one compact line each.
    """
    durations = durations or MEMECOIN_CHART_TIMEFRAMES
    by_tf = get_trending_by_timeframe(durations, limit_per_timeframe)

    all_summaries = [
        {"mint": p["mint"], "name": p["name"], "symbol": p["symbol"], "flags": []}
        for pools in by_tf.values()
        for p in pools
    ]
    assessed = assess_many(all_summaries, source="chart")

    lines = []
    for d in durations:
        pools = by_tf.get(d, [])
        lines.append(f"\n{'='*70}\n{d.upper()} TRENDING ({len(pools)})\n{'='*70}")
        for p in pools:
            a = assessed.get(p["mint"])
            if not a:
                continue
            lines.append(format_assessment_compact(a, p.get("price_change_pct"), p.get("volume_usd")))
    return "\n".join(lines)


def add_to_watchlist(mint, note=None):
    """Pins a mint to the watchlist, resolving its name/symbol from
    DexScreener now if it has DEX data (falls back to the mint itself
    for pre-migration tokens without a pair yet)."""
    pair = get_best_pair(mint)
    name = symbol = None
    if pair:
        base = pair.get("baseToken", {})
        name, symbol = base.get("name"), base.get("symbol")
    return watchlist_store.add(mint, name=name, symbol=symbol, note=note)


def remove_from_watchlist(mint):
    return watchlist_store.remove(mint)


def format_watchlist_pins():
    entries = watchlist_store.list_all()
    if not entries:
        return "Watchlist is empty."
    lines = [f"Watchlist ({len(entries)} pinned):"]
    for mint, e in entries.items():
        label = e.get("symbol") or e.get("name") or mint
        note = f" — {e['note']}" if e.get("note") else ""
        lines.append(f"  {label} ({mint}){note} [added {e.get('added_at', 'unknown')}]")
    return "\n".join(lines)


def check_watchlist():
    """Re-assesses every pinned coin right now (rug flags, chart shape,
    whale/volume signals) — same pipeline as scan/movers, just scoped to
    exactly the coins you've pinned."""
    entries = watchlist_store.list_all()
    if not entries:
        return "Watchlist is empty — nothing to check."

    summaries = [
        {"mint": mint, "name": e.get("name") or mint, "symbol": e.get("symbol") or mint, "flags": []}
        for mint, e in entries.items()
    ]
    assessed = assess_many(summaries, source="watchlist")

    lines = [f"Watchlist check ({len(entries)} coins):\n"]
    for mint, e in entries.items():
        a = assessed.get(mint)
        if not a:
            continue
        lines.append(format_assessment(a))
        if e.get("note"):
            lines.append(f"Note: {e['note']}")
        lines.append("")
    return "\n".join(lines)


async def run_scan(duration_seconds):
    print(f"Scanning new pump.fun launches for {duration_seconds}s...", file=sys.stderr)
    watcher = PumpFunWatcher()
    launches = await watcher.collect_launches(duration_seconds)
    print(f"Collected {len(launches)} launches above min market cap. Assessing...", file=sys.stderr)

    assessments = [build_full_assessment(l, source="scan") for l in launches]
    passed = [a for a in assessments if not a["high_risk"]]
    filtered = [a for a in assessments if a["high_risk"]]

    print(f"\n{'='*60}\nWORTH WATCHING ({len(passed)})\n{'='*60}")
    for a in passed:
        print(format_assessment(a))
        print()

    print(f"\n{'='*60}\nFILTERED OUT ({len(filtered)})\n{'='*60}")
    for a in filtered:
        print(format_assessment(a))
        print()


def run_movers(duration):
    print(f"Fetching trending Solana pools ({duration} window)...", file=sys.stderr)
    pools = get_trending_pools(duration=duration)
    print(f"Got {len(pools)} trending pools. Assessing...", file=sys.stderr)

    launch_summaries = [
        {"mint": p["mint"], "name": p["name"], "symbol": p["symbol"], "flags": []}
        for p in pools
    ]
    assessments = [build_full_assessment(l, source="movers") for l in launch_summaries]
    passed = [a for a in assessments if not a["high_risk"]]
    filtered = [a for a in assessments if a["high_risk"]]

    print(f"\n{'='*60}\nWORTH WATCHING ({len(passed)})\n{'='*60}")
    for a in passed:
        print(format_assessment(a))
        print()

    print(f"\n{'='*60}\nFILTERED OUT ({len(filtered)})\n{'='*60}")
    for a in filtered:
        print(format_assessment(a))
        print()


async def run_alert_check():
    """
    Single-pass check across both NEW launches (short live-feed window)
    and EXISTING movers (one trending-pools pull), pushing anything
    alert-worthy to the webhook (no-ops if ALERT_WEBHOOK_URL isn't set —
    still returns a summary either way). Meant to be triggered on a
    schedule rather than run as a long-lived loop.
    """
    watcher = PumpFunWatcher()
    launches = await watcher.collect_launches(MEMECOIN_ALERT_SCAN_SECONDS)
    launch_assessments = [build_full_assessment(l, source="alert_new") for l in launches]
    new_alerts = find_new_launch_alerts(launch_assessments)

    pools = get_trending_pools(duration=MEMECOIN_ALERT_MOVER_DURATION)
    mover_summaries = [
        {"mint": p["mint"], "name": p["name"], "symbol": p["symbol"], "flags": []}
        for p in pools
    ]
    mover_assessed = assess_many(mover_summaries, source="alert_mover")
    mover_alerts = find_mover_alerts(list(mover_assessed.values()))

    all_alerts = new_alerts + mover_alerts
    deliver_memecoin_alerts(all_alerts)

    if not all_alerts:
        return "No alertable coins this check."
    return f"Sent {len(all_alerts)} alert(s):\n\n" + "\n\n".join(format_memecoin_alert(a) for a in all_alerts)


async def run_watch():
    print("Watching new pump.fun launches in real time (Ctrl+C to stop)...", file=sys.stderr)
    watcher = PumpFunWatcher()
    printed_whale_counts = {}

    def on_launch(summary):
        a = build_full_assessment(summary, source="watch")
        print(format_assessment(a))
        print()
        printed_whale_counts[summary["mint"]] = len(summary.get("whale_trades", []))

    def on_trade(event, summary):
        mint = summary["mint"]
        whale_trades = summary.get("whale_trades", [])
        already_seen = printed_whale_counts.get(mint, 0)
        for new_flag in whale_trades[already_seen:]:
            print(f"[{summary.get('symbol')}] {new_flag}")
        printed_whale_counts[mint] = len(whale_trades)

    await watcher.stream_launches(on_launch, on_trade)


def main():
    parser = argparse.ArgumentParser(description="Memecoin (pump.fun/DEX) mover + rug filter watcher")
    sub = parser.add_subparsers(dest="command")

    scan_parser = sub.add_parser("scan", help="Collect launches for a window, then assess and filter")
    scan_parser.add_argument(
        "--duration", type=int, default=MEMECOIN_SCAN_WINDOW_SECONDS, help="Seconds to collect launches for"
    )

    sub.add_parser("watch", help="Continuously watch new launches in real time")

    movers_parser = sub.add_parser("movers", help="Scan currently-trending EXISTING tokens (already launched)")
    movers_parser.add_argument(
        "--duration", type=str, default=MEMECOIN_TRENDING_DURATION,
        choices=["5m", "1h", "6h", "24h"], help="Trending window"
    )

    chart_parser = sub.add_parser(
        "chart", help="Multi-timeframe trending chart (5m/1h/6h/24h side by side, more coins per section)"
    )
    chart_parser.add_argument(
        "--limit", type=int, default=MEMECOIN_CHART_LIMIT_PER_TIMEFRAME, help="Coins per timeframe section"
    )

    watchlist_parser = sub.add_parser("watchlist", help="Pin coins and get focused re-checks")
    watchlist_sub = watchlist_parser.add_subparsers(dest="watchlist_action")

    wl_add = watchlist_sub.add_parser("add", help="Pin a mint address")
    wl_add.add_argument("mint")
    wl_add.add_argument("--note", type=str, default=None, help="Optional note to remember why you pinned it")

    wl_remove = watchlist_sub.add_parser("remove", help="Unpin a mint address")
    wl_remove.add_argument("mint")

    watchlist_sub.add_parser("list", help="Show pinned coins (no re-check)")
    watchlist_sub.add_parser("check", help="Re-assess all pinned coins now")

    sub.add_parser(
        "alerts",
        help="Single-pass push-alert check (new launches + movers), posts to webhook. Meant for scheduling.",
    )

    sub.add_parser("accuracy", help="Check back on past verdicts: did flagged coins pump or rug?")

    args = parser.parse_args()

    if args.command == "watch":
        asyncio.run(run_watch())
    elif args.command == "movers":
        run_movers(args.duration)
    elif args.command == "chart":
        print(build_chart(limit_per_timeframe=args.limit))
    elif args.command == "alerts":
        print(asyncio.run(run_alert_check()))
    elif args.command == "accuracy":
        print(accuracy_log.check_accuracy())
    elif args.command == "watchlist":
        if args.watchlist_action == "add":
            entry = add_to_watchlist(args.mint, note=args.note)
            label = entry.get("symbol") or entry.get("name") or args.mint
            print(f"Pinned {label} ({args.mint}).")
        elif args.watchlist_action == "remove":
            removed = remove_from_watchlist(args.mint)
            print(f"{'Removed' if removed else 'Not found:'} {args.mint}")
        elif args.watchlist_action == "check":
            print(check_watchlist())
        else:
            print(format_watchlist_pins())
    else:
        duration = args.duration if args.command == "scan" else MEMECOIN_SCAN_WINDOW_SECONDS
        asyncio.run(run_scan(duration))


if __name__ == "__main__":
    main()
