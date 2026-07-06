"""
Real-time pump.fun launch + trade feed via PumpPortal's free public
WebSocket API (wss://pumpportal.fun/api/data). Verified live schema
(as of testing this module):

  "create" event: {txType:"create", mint, traderPublicKey, name, symbol,
                   initialBuy, solAmount, vTokensInBondingCurve,
                   vSolInBondingCurve, marketCapSol, bondingCurveKey, pool}

  "buy"/"sell" trade events (via subscribeTokenTrade): same general shape,
  txType is "buy" or "sell" instead of "create".

pump.fun's bonding curve starts with a fixed virtual reserve of
1,073,000,000 tokens / 30 SOL (confirmed from a live "create" event with
no initial buy) — used as an approximation for "% of curve" math, not
the token's true total supply.
"""

import asyncio
import json
import sys
import time

import websockets

from config import (
    PUMPPORTAL_WS_URL,
    MEMECOIN_MIN_INITIAL_MCAP_SOL,
    MEMECOIN_BUNDLE_WALLET_COUNT_THRESHOLD,
    MEMECOIN_BUNDLE_WINDOW_SECONDS,
    MEMECOIN_DEV_HOLDING_PCT_THRESHOLD,
    MEMECOIN_EXCLUDED_MINTS,
)

PUMP_FUN_INITIAL_VIRTUAL_TOKEN_RESERVE = 1_073_000_000


def _dev_holding_pct(create_event):
    initial_buy = create_event.get("initialBuy") or 0
    if not initial_buy:
        return 0.0
    return initial_buy / PUMP_FUN_INITIAL_VIRTUAL_TOKEN_RESERVE * 100


class PumpFunWatcher:
    """
    Tracks new pump.fun launches and their early trades over a live
    WebSocket connection. Read-only — never sends anything but
    subscription requests.
    """

    def __init__(self):
        self.tracked = {}  # mint -> dict of state

    def _init_tracked(self, create_event):
        mint = create_event["mint"]
        self.tracked[mint] = {
            "mint": mint,
            "name": create_event.get("name"),
            "symbol": create_event.get("symbol"),
            "creator": create_event.get("traderPublicKey"),
            "created_at": time.monotonic(),
            "initial_mcap_sol": create_event.get("marketCapSol"),
            "dev_holding_pct": _dev_holding_pct(create_event),
            "bundle_wallets": set(),
            "whale_trades": [],
            "latest": create_event,
        }
        creator = create_event.get("traderPublicKey")
        if create_event.get("initialBuy"):
            self.tracked[mint]["bundle_wallets"].add(creator)

    def _handle_trade(self, event):
        mint = event.get("mint")
        state = self.tracked.get(mint)
        if not state:
            return
        state["latest"] = event
        elapsed = time.monotonic() - state["created_at"]
        if event.get("txType") == "buy" and elapsed <= MEMECOIN_BUNDLE_WINDOW_SECONDS:
            trader = event.get("traderPublicKey")
            if trader:
                state["bundle_wallets"].add(trader)

        from whale_watch import check_bonding_curve_trade
        whale_flag = check_bonding_curve_trade(event)
        if whale_flag:
            state["whale_trades"].append(whale_flag)

    def finalize(self, mint):
        """Returns the closing summary dict for a tracked mint, with
        bundle/dev-holding risk flags applied."""
        state = self.tracked.get(mint)
        if not state:
            return None

        flags = []
        bundle_count = len(state["bundle_wallets"])
        if bundle_count >= MEMECOIN_BUNDLE_WALLET_COUNT_THRESHOLD:
            flags.append(
                f"Possible insider bundle: {bundle_count} distinct wallets bought within "
                f"{MEMECOIN_BUNDLE_WINDOW_SECONDS}s of launch"
            )
        if state["dev_holding_pct"] >= MEMECOIN_DEV_HOLDING_PCT_THRESHOLD:
            flags.append(
                f"Dev bought {state['dev_holding_pct']:.1f}% of the bonding curve's virtual reserve at launch "
                f"(threshold {MEMECOIN_DEV_HOLDING_PCT_THRESHOLD}%)"
            )
        flags.extend(state["whale_trades"])

        return {**state, "bundle_wallet_count": bundle_count, "flags": flags}

    async def collect_launches(self, duration_seconds):
        """
        Connects, watches new launches for `duration_seconds`, tracking
        each one's early trades for bundle/whale detection. Returns a
        list of finalized summaries for tokens that met the minimum
        market cap filter.
        """
        deadline = time.monotonic() + duration_seconds
        async with websockets.connect(PUMPPORTAL_WS_URL) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))

            while time.monotonic() < deadline:
                timeout = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "message" in event and "mint" not in event:
                    continue  # subscription ack

                tx_type = event.get("txType")
                if tx_type == "create":
                    if not event.get("name") or not event.get("symbol"):
                        continue  # not a standard new-coin launch event, skip
                    if event.get("mint") in MEMECOIN_EXCLUDED_MINTS:
                        continue
                    mcap = event.get("marketCapSol") or 0
                    if mcap < MEMECOIN_MIN_INITIAL_MCAP_SOL:
                        continue
                    self._init_tracked(event)
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [event["mint"]]}))
                elif tx_type in ("buy", "sell"):
                    self._handle_trade(event)

            return [self.finalize(mint) for mint in self.tracked]

    async def stream_launches(self, on_launch, on_trade=None, reconnect_delay=5):
        """
        Runs indefinitely, calling on_launch(finalized_summary) once per
        new token that passes the min-mcap filter (called immediately on
        detection; caller can re-fetch/re-finalize later for updated
        bundle/whale data). on_trade(event, finalized_summary) fires for
        every buy/sell on a tracked mint, if provided.
        Auto-reconnects on connection drops.
        """
        while True:
            try:
                async with websockets.connect(PUMPPORTAL_WS_URL) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if "message" in event and "mint" not in event:
                            continue

                        tx_type = event.get("txType")
                        if tx_type == "create":
                            if not event.get("name") or not event.get("symbol"):
                                continue
                            if event.get("mint") in MEMECOIN_EXCLUDED_MINTS:
                                continue
                            mcap = event.get("marketCapSol") or 0
                            if mcap < MEMECOIN_MIN_INITIAL_MCAP_SOL:
                                continue
                            self._init_tracked(event)
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [event["mint"]]}))
                            on_launch(self.finalize(event["mint"]))
                        elif tx_type in ("buy", "sell"):
                            self._handle_trade(event)
                            if on_trade:
                                on_trade(event, self.finalize(event["mint"]))
            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                print(f"[warn] PumpPortal connection dropped ({e}), reconnecting in {reconnect_delay}s...", file=sys.stderr)
                await asyncio.sleep(reconnect_delay)
