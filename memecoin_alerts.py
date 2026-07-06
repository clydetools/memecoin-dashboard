"""
Push alerts for memecoins — notifies (via the same webhook used by the
main crypto_agent's price alerts, ALERT_WEBHOOK_URL) when:
  - a NEW launch passes the rug filter AND shows real early buy interest
  - an EXISTING mover passes the rug filter AND crosses a momentum threshold

De-dupes per mint via a cooldown window persisted to disk, same pattern
as alerts.py's price-threshold alerts.
"""

import json
import os
import sys
from datetime import datetime, timezone

from alerts import post_to_webhook
from config import (
    MEMECOIN_ALERT_NEW_LAUNCH_MIN_BUYS,
    MEMECOIN_ALERT_MOVER_MIN_PCT,
    MEMECOIN_ALERT_MOVER_DURATION,
    MEMECOIN_ALERT_COOLDOWN_MINUTES,
    MEMECOIN_ALERT_STATE_FILE,
)

_TF_KEY = {"5m": "m5", "1h": "h1", "6h": "h6", "24h": "h24"}


def _load_state():
    if not os.path.exists(MEMECOIN_ALERT_STATE_FILE):
        return {}
    try:
        with open(MEMECOIN_ALERT_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(MEMECOIN_ALERT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[warn] failed to save memecoin alert state: {e}", file=sys.stderr)


def _on_cooldown(state, mint, now):
    last = state.get(mint)
    if last is None:
        return False
    elapsed_min = (now - datetime.fromisoformat(last)).total_seconds() / 60
    return elapsed_min < MEMECOIN_ALERT_COOLDOWN_MINUTES


def find_new_launch_alerts(assessments):
    """
    assessments: build_full_assessment() dicts for freshly-scanned new
    launches. Alertable = passes the rug filter AND has at least
    MEMECOIN_ALERT_NEW_LAUNCH_MIN_BUYS buys (proxy for real early
    interest, not a silent mint nobody's touched).
    """
    state = _load_state()
    now = datetime.now(timezone.utc)
    alertable = []

    for a in assessments:
        if a["high_risk"]:
            continue
        buys = a.get("buys_h1") or 0
        if buys < MEMECOIN_ALERT_NEW_LAUNCH_MIN_BUYS:
            continue
        if _on_cooldown(state, a["mint"], now):
            continue
        alertable.append({**a, "alert_reason": f"new launch, {buys} early buys, passed rug filter"})
        state[a["mint"]] = now.isoformat()

    _save_state(state)
    return alertable


def find_mover_alerts(assessments, duration=MEMECOIN_ALERT_MOVER_DURATION):
    """
    assessments: build_full_assessment() dicts for currently-trending
    existing movers. Alertable = passes the rug filter AND moved at
    least MEMECOIN_ALERT_MOVER_MIN_PCT in `duration`.
    """
    tf_key = _TF_KEY.get(duration, "h1")
    state = _load_state()
    now = datetime.now(timezone.utc)
    alertable = []

    for a in assessments:
        if a["high_risk"]:
            continue
        change = (a.get("price_change_pct") or {}).get(tf_key)
        if change is None or change < MEMECOIN_ALERT_MOVER_MIN_PCT:
            continue
        if _on_cooldown(state, a["mint"], now):
            continue
        alertable.append({**a, "alert_reason": f"+{change:.0f}% in {duration}, passed rug filter"})
        state[a["mint"]] = now.isoformat()

    _save_state(state)
    return alertable


def format_memecoin_alert(a):
    return f"{a['symbol']} ({a['name']}) — {a['alert_reason']}\n{a['mint']}"


def deliver_memecoin_alerts(alerts):
    if not alerts:
        return
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    lines = [f"[{timestamp}] MEMECOIN ALERT"] + [format_memecoin_alert(a) for a in alerts]
    message = "\n\n".join(lines)
    print(message)
    post_to_webhook(message)
