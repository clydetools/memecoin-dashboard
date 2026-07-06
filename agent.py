"""
Crypto + Stock Market Briefing Agent (terminal tool)
=====================================================
Pulls short-term crypto price momentum (1h/4h/24h), crypto news, and
broad stock/macro news, then asks Claude to synthesize a briefing.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python agent.py                 # full briefing (default)
    python agent.py briefing        # same as above
    python agent.py movers          # just raw price-momentum data, no LLM
    python agent.py news            # just raw headlines, no LLM
    python agent.py ask "your question here"   # ask Claude something
                                                  # using the latest data as context
    python agent.py watch                       # poll continuously, alert
                                                  # only on threshold-crossing
                                                  # moves (no LLM, no cost)
    python agent.py watch --interval 60 --once  # single check, custom interval

Run this on a schedule (cron, GitHub Actions, a systemd timer, etc.)
for a recurring digest. See README.md for scheduling examples.
"""

import sys
import time
import argparse
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DEFAULT_WATCH_INTERVAL_SECONDS
from fetch_market import get_market_movers, get_global_snapshot, format_movers_for_prompt
from fetch_news import (
    get_crypto_headlines,
    get_cryptopanic_headlines,
    get_stock_headlines,
    format_news_for_prompt,
)
from fetch_twitter import get_recent_crypto_posts, format_posts_for_prompt
from alerts import find_new_alerts, deliver_alerts

SYSTEM_PROMPT = """You are a markets analyst producing a concise briefing for \
someone tracking both crypto and stocks who wants situational awareness, not \
advice. Rules:
- Never tell the user to buy, sell, or hold anything.
- Pay special attention to short-term momentum (1h/4h changes) as signals of \
  emerging trends, not just 24h moves. Note when 1h/4h momentum is accelerating, \
  reversing, or diverging from the 24h trend.
- Flag notable correlations between news and price moves, but label them as \
  observations, not causal claims.
- Be skeptical of hype language; note when a mover looks like low-liquidity \
  noise (small mcap, no clear catalyst) vs. a broad market-wide move.
- Treat crypto and stock/macro news as one connected picture — note when \
  macro news (rates, inflation, regulation) is likely spilling into crypto \
  or vice versa.
- Structure output with short headers and bullet points. No fluff, no \
  disclaimers beyond one line at the end.
"""


def gather_data():
    print("Fetching price momentum (1h/4h/24h)...", file=sys.stderr)
    movers = get_market_movers()
    global_data = get_global_snapshot()

    print("Fetching crypto news...", file=sys.stderr)
    crypto_news = get_crypto_headlines() + get_cryptopanic_headlines()

    print("Fetching stock/macro news...", file=sys.stderr)
    stock_news = get_stock_headlines()

    print("Fetching social data (if configured)...", file=sys.stderr)
    posts = get_recent_crypto_posts()

    return {
        "movers": movers,
        "global_data": global_data,
        "crypto_news": crypto_news,
        "stock_news": stock_news,
        "posts": posts,
    }


def build_data_snapshot(data):
    movers_txt = format_movers_for_prompt(data["movers"])
    crypto_news_txt = format_news_for_prompt(data["crypto_news"], "crypto")
    stock_news_txt = format_news_for_prompt(data["stock_news"], "stock")
    posts_txt = format_posts_for_prompt(data["posts"])

    global_data = data["global_data"]
    mcap = global_data.get("total_market_cap", {}).get("usd")
    mcap_change = global_data.get("market_cap_change_percentage_24h_usd")
    btc_dom = global_data.get("market_cap_percentage", {}).get("btc")

    global_txt = (
        f"Total crypto market cap: ${mcap:,.0f} ({mcap_change:+.2f}% 24h)\n"
        f"BTC dominance: {btc_dom:.1f}%"
        if mcap else "Global snapshot unavailable."
    )

    return f"""Data snapshot ({datetime.now(timezone.utc).isoformat()}):

## GLOBAL CRYPTO MARKET
{global_txt}

## CRYPTO PRICE MOMENTUM (1h / 4h / 24h)
{movers_txt}

## CRYPTO NEWS
{crypto_news_txt}

## STOCK / MACRO NEWS
{stock_news_txt}

## SOCIAL (X)
{posts_txt}
"""


def run_briefing():
    if not ANTHROPIC_API_KEY:
        print("ERROR: set ANTHROPIC_API_KEY in your environment.", file=sys.stderr)
        sys.exit(1)

    data = gather_data()
    snapshot = build_data_snapshot(data)

    print("Synthesizing with Claude...\n", file=sys.stderr)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = snapshot + """
Produce a briefing with these sections: Market Overview (crypto + stocks/macro \
together), Momentum Watch (coins with notable 1h/4h moves and whether they look \
like early trends or noise), Key News (crypto and stock, connect the dots where \
relevant), and Watch Items (things worth monitoring next). Keep it under 450 words."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    briefing = "".join(block.text for block in response.content if block.type == "text")
    print(briefing)
    return briefing


def run_movers_only():
    movers = get_market_movers()
    print(format_movers_for_prompt(movers))


def run_news_only():
    crypto_news = get_crypto_headlines() + get_cryptopanic_headlines()
    stock_news = get_stock_headlines()
    print("=== CRYPTO NEWS ===")
    print(format_news_for_prompt(crypto_news, "crypto"))
    print("\n=== STOCK / MACRO NEWS ===")
    print(format_news_for_prompt(stock_news, "stock"))


def run_ask(question):
    if not ANTHROPIC_API_KEY:
        print("ERROR: set ANTHROPIC_API_KEY in your environment.", file=sys.stderr)
        sys.exit(1)

    data = gather_data()
    snapshot = build_data_snapshot(data)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = f"{snapshot}\n\nUser question: {question}\n\nAnswer using the data above where relevant."

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    answer = "".join(block.text for block in response.content if block.type == "text")
    print(answer)
    return answer


def run_watch(interval_seconds, once=False):
    print(
        f"Watching for threshold-crossing moves every {interval_seconds}s "
        f"(Ctrl+C to stop)...",
        file=sys.stderr,
    )
    while True:
        try:
            movers = get_market_movers()
            alerts = find_new_alerts(movers)
            if alerts:
                deliver_alerts(alerts)
            else:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                print(f"[{ts}] no new threshold-crossing moves", file=sys.stderr)
        except Exception as e:
            print(f"[warn] watch cycle failed: {e}", file=sys.stderr)

        if once:
            break
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="Crypto + stock market briefing agent")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("briefing", help="Full synthesized briefing (default)")
    sub.add_parser("movers", help="Raw 1h/4h/24h price momentum data only, no LLM call")
    sub.add_parser("news", help="Raw crypto + stock headlines only, no LLM call")

    ask_parser = sub.add_parser("ask", help="Ask Claude a question using live market data as context")
    ask_parser.add_argument("question", nargs="+", help="Your question")

    watch_parser = sub.add_parser(
        "watch", help="Poll continuously, alert only on threshold-crossing moves (no LLM call)"
    )
    watch_parser.add_argument(
        "--interval", type=int, default=DEFAULT_WATCH_INTERVAL_SECONDS,
        help=f"Seconds between polls (default: {DEFAULT_WATCH_INTERVAL_SECONDS})",
    )
    watch_parser.add_argument(
        "--once", action="store_true", help="Run a single check and exit (useful for cron instead of an internal loop)"
    )

    args = parser.parse_args()

    if args.command == "movers":
        run_movers_only()
    elif args.command == "news":
        run_news_only()
    elif args.command == "ask":
        run_ask(" ".join(args.question))
    elif args.command == "watch":
        run_watch(args.interval, once=args.once)
    else:
        run_briefing()


if __name__ == "__main__":
    main()
