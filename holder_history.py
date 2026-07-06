"""
Tracks top10_holder_pct over time per mint, so the rug filter can say
whether concentration is rising (worse — insiders/whales accumulating)
or falling (more organic distribution), not just report a single
snapshot value. Persisted locally; grows slowly since only the last
MEMECOIN_HOLDER_HISTORY_MAX_SNAPSHOTS per mint are kept.
"""

import json
import os
import sys
import threading
from datetime import datetime, timezone

from config import (
    MEMECOIN_HOLDER_HISTORY_FILE,
    MEMECOIN_HOLDER_HISTORY_MAX_SNAPSHOTS,
    MEMECOIN_HOLDER_TREND_MIN_PCT_DELTA,
)

# Same concurrency issue as spam_filter.py: assess_many() calls this from
# multiple threads at once, and concurrent read-modify-write cycles on
# the history file corrupt it without a lock.
_lock = threading.Lock()


def _load():
    if not os.path.exists(MEMECOIN_HOLDER_HISTORY_FILE):
        return {}
    try:
        with open(MEMECOIN_HOLDER_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to load holder history: {e}", file=sys.stderr)
        return {}


def _save(data):
    try:
        with open(MEMECOIN_HOLDER_HISTORY_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[warn] failed to save holder history: {e}", file=sys.stderr)


def record_and_get_trend(mint, top10_pct):
    """
    Records a new top10_pct snapshot for `mint` and returns a trend
    description string if there's enough history to say something
    meaningful (a first-vs-latest comparison beyond the noise
    threshold), else None.
    """
    if top10_pct is None:
        return None

    with _lock:
        data = _load()
        history = data.get(mint, [])
        now = datetime.now(timezone.utc).isoformat()

        history.append({"ts": now, "top10_pct": top10_pct})
        history = history[-MEMECOIN_HOLDER_HISTORY_MAX_SNAPSHOTS:]
        data[mint] = history
        _save(data)

    if len(history) < 2:
        return None

    first, latest = history[0], history[-1]
    delta = latest["top10_pct"] - first["top10_pct"]
    if abs(delta) < MEMECOIN_HOLDER_TREND_MIN_PCT_DELTA:
        return None

    direction = "rising" if delta > 0 else "falling"
    return (
        f"Top10 concentration {direction}: {first['top10_pct']:.1f}% → {latest['top10_pct']:.1f}% "
        f"since {first['ts']}"
    )
