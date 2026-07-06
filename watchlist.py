"""
Persisted watchlist — pin specific mint addresses to get focused,
re-checkable updates on just those coins instead of re-scanning everything.
"""

import json
import os
import sys
from datetime import datetime, timezone

from config import MEMECOIN_WATCHLIST_FILE


def _load():
    if not os.path.exists(MEMECOIN_WATCHLIST_FILE):
        return {}
    try:
        with open(MEMECOIN_WATCHLIST_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to load watchlist: {e}", file=sys.stderr)
        return {}


def _save(data):
    try:
        with open(MEMECOIN_WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[warn] failed to save watchlist: {e}", file=sys.stderr)


def add(mint, name=None, symbol=None, note=None):
    data = _load()
    data[mint] = {
        "name": name,
        "symbol": symbol,
        "note": note,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(data)
    return data[mint]


def remove(mint):
    data = _load()
    removed = data.pop(mint, None)
    _save(data)
    return removed is not None


def list_all():
    return _load()
