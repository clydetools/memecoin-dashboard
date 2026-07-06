"""
Copycat/duplicate-name spam filter.

Memecoins on pump.fun frequently get cloned: dozens of different mints
all using the same trending symbol (we've seen ANSEM, NEYMAR, 公牛 each
show up 2-3x with different mint addresses in the same scan). The
original is sometimes legit; the copies are almost always spam trying
to ride the name's hype with a fresh, unrelated contract.

This keeps a small persisted registry of {normalized symbol -> first
mint seen}. The first time a symbol appears, it's registered and passes
through clean. Any later coin reusing that symbol on a different mint
is flagged as a likely copycat.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone

from config import MEMECOIN_NAME_REGISTRY_FILE

# assess_many() runs build_full_assessment concurrently across threads;
# without this lock, concurrent read-modify-write cycles on the registry
# file corrupt it (observed: "Extra data" JSON errors from interleaved writes).
_lock = threading.Lock()


def _normalize(symbol):
    return re.sub(r"[^a-z0-9]", "", (symbol or "").lower())


def _load_registry():
    if not os.path.exists(MEMECOIN_NAME_REGISTRY_FILE):
        return {}
    try:
        with open(MEMECOIN_NAME_REGISTRY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to load name registry: {e}", file=sys.stderr)
        return {}


def _save_registry(registry):
    try:
        with open(MEMECOIN_NAME_REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        print(f"[warn] failed to save name registry: {e}", file=sys.stderr)


def check_and_register(symbol, mint):
    """
    Returns a copycat-flag dict {"original_mint", "first_seen"} if this
    symbol was already registered under a DIFFERENT mint. Returns None
    (and registers the symbol) if this is the first time it's been seen,
    or if it's the same mint re-checked again.
    """
    key = _normalize(symbol)
    if not key:
        return None

    with _lock:
        registry = _load_registry()
        existing = registry.get(key)

        if existing is None:
            registry[key] = {"mint": mint, "first_seen": datetime.now(timezone.utc).isoformat()}
            _save_registry(registry)
            return None

        if existing["mint"] == mint:
            return None

        return {"original_mint": existing["mint"], "first_seen": existing["first_seen"]}
