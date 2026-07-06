"""
Threshold-based alerting for watch mode.

Checks the latest 1h/4h/24h momentum data against configured thresholds,
de-dupes against recently-fired alerts (persisted to a local JSON file so
it survives restarts), and delivers new alerts to the terminal and
optionally a Slack/Discord webhook.
"""

import json
import os
import sys
import requests
from datetime import datetime, timezone

from config import ALERT_THRESHOLDS, ALERT_COOLDOWN_MINUTES, ALERT_STATE_FILE, ALERT_WEBHOOK_URL


def _load_state():
    if not os.path.exists(ALERT_STATE_FILE):
        return {}
    try:
        with open(ALERT_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(ALERT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[warn] failed to save alert state: {e}", file=sys.stderr)


def _on_cooldown(state, key, now):
    last = state.get(key)
    if last is None:
        return False
    elapsed_min = (now - datetime.fromisoformat(last)).total_seconds() / 60
    return elapsed_min < ALERT_COOLDOWN_MINUTES


def find_new_alerts(movers):
    """
    Compares movers dict (from fetch_market.get_market_movers) against
    thresholds. Returns a list of alert dicts for anything that just
    crossed a threshold and isn't on cooldown, and updates alert state.
    """
    state = _load_state()
    now = datetime.now(timezone.utc)
    new_alerts = []

    for timeframe, threshold in ALERT_THRESHOLDS.items():
        for direction, key in [("gainer", f"gainers_{timeframe}"), ("loser", f"losers_{timeframe}")]:
            for coin in movers.get(key, []):
                change = coin.get(f"change_{timeframe}")
                if change is None or abs(change) < threshold:
                    continue

                state_key = f"{coin['symbol']}_{timeframe}"
                if _on_cooldown(state, state_key, now):
                    continue

                new_alerts.append({
                    "symbol": coin["symbol"],
                    "name": coin["name"],
                    "timeframe": timeframe,
                    "direction": direction,
                    "change": change,
                    "price": coin["price"],
                })
                state[state_key] = now.isoformat()

    _save_state(state)
    return new_alerts


def format_alert(a):
    arrow = "\u25b2" if a["change"] > 0 else "\u25bc"
    return (
        f"{arrow} {a['symbol']} ({a['name']}) {a['change']:+.2f}% in {a['timeframe']} "
        f"\u2014 now ${a['price']:,}"
    )


def post_to_webhook(message, webhook_url=ALERT_WEBHOOK_URL):
    """Posts a message to a Slack or Discord incoming webhook — both
    accept a JSON body with a "content" or "text" field; sending both
    covers either. Shared by price-threshold alerts and memecoin alerts."""
    if not webhook_url:
        return
    try:
        requests.post(
            webhook_url,
            json={"content": message, "text": message},
            timeout=10,
        )
    except Exception as e:
        print(f"[warn] failed to deliver webhook alert: {e}", file=sys.stderr)


def deliver_alerts(alerts):
    if not alerts:
        return

    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    lines = [f"[{timestamp}] MARKET ALERT"] + [format_alert(a) for a in alerts]
    message = "\n".join(lines)

    print(message)
    post_to_webhook(message)
