"""
Configuration for the crypto market agent.
All secrets are read from environment variables — never hardcode keys here.

Required:
  ANTHROPIC_API_KEY   - your Anthropic API key (console.anthropic.com)

Optional (features degrade gracefully if missing):
  X_BEARER_TOKEN       - X/Twitter API v2 bearer token (needs paid Basic tier or higher
                          for recent search: https://developer.x.com/en/portal/products)
  CRYPTOPANIC_API_KEY  - free tier key from https://cryptopanic.com/developers/api/
"""

import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN")  # optional
CRYPTOPANIC_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY")  # optional

CLAUDE_MODEL = "claude-sonnet-4-6"  # good balance of cost/quality for synthesis

# How many coins to pull for gainers/losers, per timeframe
TOP_N_MOVERS = 8

# How many coins to scan (from top market-cap coins) when computing momentum.
# Higher = more thorough but slower/more data through the sparkline call.
MOVERS_SCAN_SIZE = 150

# RSS feeds used for crypto news (no API key required)
CRYPTO_RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "TheBlock": "https://www.theblock.co/rss.xml",
}

# RSS feeds used for broad stock/macro market news (no API key required)
STOCK_RSS_FEEDS = {
    "YahooFinance": "https://finance.yahoo.com/rss/topstories",
    "MarketWatch": "http://feeds.marketwatch.com/marketwatch/topstories/",
    "CNBCMarkets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "InvestingCom": "https://www.investing.com/rss/news_25.rss",
}

# --- Alerting (watch mode) ---

# % move (absolute value) required to trigger an alert, per timeframe.
# Tune these to your risk appetite / how much noise you want.
ALERT_THRESHOLDS = {
    "1h": 5.0,
    "4h": 8.0,
    "24h": 15.0,
}

# Don't re-alert the same coin+timeframe again within this many minutes,
# even if it's still above threshold on the next poll.
ALERT_COOLDOWN_MINUTES = 60

# Where alert state (what's already been alerted) is persisted between runs.
ALERT_STATE_FILE = "alert_state.json"

# Optional: a Slack or Discord "incoming webhook" URL. If set, alerts are
# posted there in addition to the terminal. Leave unset to just print.
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL")

# Default polling interval for `python agent.py watch`, in seconds.
DEFAULT_WATCH_INTERVAL_SECONDS = 300

# Twitter/X accounts worth watching if you have API access
WATCHED_X_ACCOUNTS = ["WatcherGuru", "WuBlockchain", "DegenerateNews"]

# --- Memecoin watcher (pump.fun / Solana DEX) ---
# Read-only analysis and filtering only. Never places trades.

PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"
RUGCHECK_API_BASE = "https://api.rugcheck.xyz/v1"
DEXSCREENER_API_BASE = "https://api.dexscreener.com/latest/dex"
GECKOTERMINAL_API_BASE = "https://api.geckoterminal.com/api/v2"

# Network slug GeckoTerminal uses for Solana pools
GECKOTERMINAL_NETWORK = "solana"

# Default lookback window for "existing movers" (trending pools) —
# one of "5m", "1h", "6h", "24h" per GeckoTerminal's API.
MEMECOIN_TRENDING_DURATION = "1h"

# How many trending pools to pull and run through the rug filter.
MEMECOIN_TRENDING_LIMIT = 20

# Chart-style multi-timeframe view: which windows to show side by side,
# and how many coins to list per timeframe section.
MEMECOIN_CHART_TIMEFRAMES = ["5m", "1h", "6h", "24h"]
MEMECOIN_CHART_LIMIT_PER_TIMEFRAME = 15

# How many rug-check/DexScreener lookups to run concurrently when
# assessing a batch of coins (these are independent blocking HTTP calls,
# so threading them cuts wall-clock time roughly proportionally).
MEMECOIN_ASSESS_CONCURRENCY = 10

# Where pinned watchlist coins are persisted between runs.
MEMECOIN_WATCHLIST_FILE = "watchlist.json"

# --- Push alerts (memecoin) ---
# Notifies via the same ALERT_WEBHOOK_URL used by the main crypto_agent's
# price alerts, so both land in the same Slack/Discord channel.

# Only alert on a NEW launch if it passes the rug filter AND shows at
# least this many buys during the scan window (proxy for "some real
# organic interest", not just a silent mint with 1-2 buys).
MEMECOIN_ALERT_NEW_LAUNCH_MIN_BUYS = 5

# How long to watch the new-launch feed per alert check cycle (kept short
# since this runs on a schedule, e.g. every few minutes via Task Scheduler).
MEMECOIN_ALERT_SCAN_SECONDS = 30

# Only alert on an EXISTING mover if it passes the rug filter AND moved
# at least this % in the checked timeframe.
MEMECOIN_ALERT_MOVER_MIN_PCT = 50.0
MEMECOIN_ALERT_MOVER_DURATION = "1h"

# Don't re-alert the same mint again within this many minutes.
MEMECOIN_ALERT_COOLDOWN_MINUTES = 60

# Where memecoin alert cooldown state is persisted between runs.
MEMECOIN_ALERT_STATE_FILE = "memecoin_alert_state.json"

# --- Accuracy tracking ---
# Logs every rug-filter verdict with a price snapshot, so you can check
# back later whether "worth watching" coins actually pumped, or whether
# "filtered out" coins actually rugged (vs. filtering out a winner).

MEMECOIN_ACCURACY_LOG_FILE = "memecoin_accuracy_log.jsonl"

# Only check entries at least this many hours old (give price time to move).
MEMECOIN_ACCURACY_MIN_AGE_HOURS = 1

# Only consider entries within this many hours back (older ones ignored).
MEMECOIN_ACCURACY_LOOKBACK_HOURS = 72

# Classification thresholds for the check-back comparison.
MEMECOIN_ACCURACY_PUMP_THRESHOLD_PCT = 50.0
MEMECOIN_ACCURACY_RUG_THRESHOLD_PCT = -80.0

# Well-known blue-chip mints to always exclude from memecoin analysis —
# these show up as base tokens in high-volume pools (e.g. SOL/USDC) and
# aren't memecoins, so rug-risk heuristics built for memecoins produce
# nonsense results on them (e.g. "0% LP locked" on wrapped SOL itself).
MEMECOIN_EXCLUDED_MINTS = {
    "So11111111111111111111111111111111111111112",  # Wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# How long (seconds) to collect new pump.fun launches before running the
# filter pass, in `memecoin_agent.py scan` mode.
MEMECOIN_SCAN_WINDOW_SECONDS = 60

# Skip tokens below this initial market cap (in SOL) — very tiny launches
# are mostly noise/instant-rug bait even before other checks.
MEMECOIN_MIN_INITIAL_MCAP_SOL = 5.0

# Bonding curve migrates to Raydium around this much SOL raised (pump.fun's
# historical target). Tune if pump.fun changes this — treat as an estimate,
# not a guaranteed constant.
MEMECOIN_BONDING_CURVE_MIGRATION_SOL = 85.0

# Flag as "insider bundle risk" if this many distinct wallets buy within
# the first N seconds of creation (classic coordinated-launch rug setup).
MEMECOIN_BUNDLE_WALLET_COUNT_THRESHOLD = 5
MEMECOIN_BUNDLE_WINDOW_SECONDS = 10

# Flag the creator/deployer as high risk if their initial buy is more than
# this % of total supply (heavy dev bag = easy dump).
MEMECOIN_DEV_HOLDING_PCT_THRESHOLD = 8.0

# Rug filter thresholds (post-migration, via RugCheck + DexScreener)
MEMECOIN_MIN_LP_LOCKED_PCT = 50.0       # below this, treat liquidity as pullable
MEMECOIN_MAX_TOP10_HOLDER_PCT = 40.0    # concentration above this = high dump risk
MEMECOIN_MIN_RUGCHECK_SCORE = None      # RugCheck score is unbounded/relative; set
                                        # a number here once you've seen enough
                                        # reports to know what "too risky" looks like

# Whale-watch: flag a single buy/sell as notable if it moves this % of the
# pool's liquidity (or, pre-migration, this % of the bonding curve's SOL).
MEMECOIN_WHALE_TRADE_PCT_OF_LIQUIDITY = 3.0

# Chart-pattern heuristics (approximate — based on DexScreener's
# multi-timeframe price/volume/tx fields, not raw OHLC candles).
MEMECOIN_PARABOLIC_H1_PCT = 100.0        # +100% in 1h with no consolidation
MEMECOIN_BLEED_AFTER_PUMP_H24_PCT = 50.0 # up big on 24h...
MEMECOIN_BLEED_AFTER_PUMP_H1_PCT = -15.0 # ...but reversing hard on 1h = distribution
