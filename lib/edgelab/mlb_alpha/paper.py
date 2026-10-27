"""
lib/edgelab/mlb_alpha/paper.py
==============================
Paper trading: simulated orders, fills, settlement and performance, with the same
discipline real execution would need (idempotency, limits, reconciliation) and none
of the risk. Nothing here touches a network, a credential or the canonical bet ledger.

The ledger is an append-only JSONL file of events; every aggregate is recomputed
from the events, so a report can never drift from the record.

EVENTS
  INTENT      what the engine wanted (rule id, probabilities, prices) -- idempotent by key
  FILL        simulated execution against an archived/captured book (taker) or tape (maker)
  REJECT      refused by a limit or a duplicate; recorded, never silent
  SETTLEMENT  outcome applied once, with the realised P/L

Fill models
  TAKER_AT_CAPTURE   walks the book exactly as captured at decision time (optimistic: zero latency)
  TAKER_NEXT_BOOK    fills against the NEXT captured book if it is worse (stale-quote / latency
                     sensitivity: the order lands after the market has moved)
  MAKER_TAPE         a resting order fills only from subsequent taker prints at or through its price,
                     after an assumed queue ahead is consumed; no touch is treated as a fill
"""

import hashlib
import json
import os

from lib.edgelab.mlb_alpha import execution as ex

TAKER_AT_CAPTURE = "TAKER_AT_CAPTURE"
TAKER_NEXT_BOOK = "TAKER_NEXT_BOOK"
MAKER_TAPE = "MAKER_TAPE"


def intent_key(run_id, ticker, side, rule_id):
    """Idempotency key: the same decision can never create two positions."""
    return hashlib.sha1(("|".join([run_id, ticker, side, rule_id])).encode()).hexdigest()[:20]


def simulate_resting_fill(tape, side, price, size, placed_ts, queue_ahead=0.0, cancel_ts=None):
    """
    A resting BUY of `size` contracts on `side` at `price` (dollars).
    tape: iterable of {'ts', 'yes_price', 'count', 'taker_side'} sorted by ts, where taker_side is the
    outcome the TAKER bought ('yes' / 'no').
      resting YES bid at P  is hit by takers buying NO  with yes_price <= P   (trade through or at the level)
      resting NO bid  at Q  is hit by takers buying YES with no_price  <= Q   (no_price = 1 - yes_price)
    Volume at our exact level must first exhaust `queue_ahead`; volume at a strictly better price for the
    taker reaches us only after... it does not: a print THROUGH our level means every order at our level was
    filled first, so it counts fully. Returns {'filled', 'avgPrice', 'firstFillTs', 'sawVolumeAtLevel'}.
    """
    filled, queue = 0.0, float(queue_ahead)
    first = None
    saw = 0.0
    for t in tape:
        if t["ts"] < placed_ts:
            continue
        if cancel_ts is not None and t["ts"] >= cancel_ts:
            break
        if side == "YES":
            hit = t["taker_side"] == "no" and t["yes_price"] <= price + 1e-9
            level = abs(t["yes_price"] - price) < 1e-9
        else:
            no_price = 1.0 - t["yes_price"]
            hit = t["taker_side"] == "yes" and no_price <= price + 1e-9
            level = abs(no_price - price) < 1e-9
        if not hit:
            continue
        vol = float(t["count"])
        saw += vol
        if level:
            take_q = min(queue, vol)
            queue -= take_q
            vol -= take_q
        if vol <= 0:
            continue
        take = min(vol, size - filled)
        if take > 0:
            filled += take
            first = first if first is not None else t["ts"]
        if filled >= size - 1e-9:
            break
    return {"filled": filled, "avgPrice": price if filled else None, "firstFillTs": first, "sawVolumeAtLevel": saw}


class PaperLedger:
    def __init__(self, path, bankroll=1000.0, per_trade_usd=10.0, per_game_usd=25.0, max_open_usd=200.0):
        self.path = path
        self.bankroll = float(bankroll)
        self.per_trade_usd = per_trade_usd
        self.per_game_usd = per_game_usd
        self.max_open_usd = max_open_usd
        self.events = []
        if os.path.exists(path):
            with open(path) as fh:
                self.events = [json.loads(l) for l in fh if l.strip()]

    # ---------------------------------------------------------------- io
    def _append(self, ev):
        self.events.append(ev)
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a") as fh:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")

    def _fills(self):
        return [e for e in self.events if e["type"] == "FILL"]

    def _settled(self):
        return {e["fillId"]: e for e in self.events if e["type"] == "SETTLEMENT"}

    def has_key(self, key):
        return any(e.get("key") == key for e in self.events if e["type"] in ("FILL", "REJECT"))

    # ---------------------------------------------------------------- exposure
    def open_cash(self, game=None):
        settled = self._settled()
        return sum(f["cash"] for f in self._fills() if f["id"] not in settled and (game is None or f["game"] == game))

    # ---------------------------------------------------------------- orders
    def submit_taker(self, *, run_id, rule_id, ticker, game, side, contracts, book, next_book=None, model_prob=None,
                     mode=TAKER_AT_CAPTURE, ts=None, meta=None):
        key = intent_key(run_id, ticker, side, rule_id)
        base = {"key": key, "runId": run_id, "rule": rule_id, "ticker": ticker, "game": game, "side": side, "ts": ts}
        if self.has_key(key):
            return self._reject(base, "DUPLICATE_INTENT")
        want = int(contracts)
        if want <= 0:
            return self._reject(base, "ZERO_SIZE")
        fill = ex.fill_buy(book, side, want)
        if mode == TAKER_NEXT_BOOK and next_book is not None:
            f2 = ex.fill_buy(next_book, side, want)
            if f2["filled"] < fill["filled"] or (f2["avgPrice"] and fill["avgPrice"] and f2["avgPrice"] > fill["avgPrice"]):
                fill = f2
        if fill["filled"] <= 0:
            return self._reject(base, "NO_LIQUIDITY")
        if fill["cash"] > self.per_trade_usd * 1.05 + 1e-9:
            return self._reject(base, "PER_TRADE_LIMIT")
        if self.open_cash(game) + fill["cash"] > self.per_game_usd + 1e-9:
            return self._reject(base, "PER_GAME_LIMIT")
        if self.open_cash() + fill["cash"] > self.max_open_usd + 1e-9:
            return self._reject(base, "PORTFOLIO_LIMIT")
        ev = dict(base, type="FILL", id=key, mode=mode, requested=want, contracts=fill["filled"], partial=fill["partial"],
                  avgPrice=fill["avgPrice"], topPrice=fill["topPrice"], slippage=fill["slippage"], fee=fill["fee"],
                  cash=fill["cash"], feeBasis=fill["feeBasis"], modelProb=model_prob, meta=meta or {})
        self._append(ev)
        return ev

    def _reject(self, base, reason):
        ev = dict(base, type="REJECT", reason=reason)
        self._append(ev)
        return ev

    def settle(self, ticker, outcome_yes, settled_at=None):
        """Apply one settlement to every unsettled fill on `ticker`. Idempotent."""
        done = self._settled()
        out = []
        for f in self._fills():
            if f["ticker"] != ticker or f["id"] in done:
                continue
            pl = ex.settle(f["side"], outcome_yes, f["contracts"], f["cash"])
            ev = {"type": "SETTLEMENT", "fillId": f["id"], "ticker": ticker, "game": f["game"], "outcomeYes": bool(outcome_yes),
                  "net": pl, "settledAt": settled_at}
            self._append(ev)
            out.append(ev)
        return out

    # ---------------------------------------------------------------- reporting
    def report(self):
        settled = self._settled()
        fills = self._fills()
        done = [(f, settled[f["id"]]) for f in fills if f["id"] in settled]
        cash = sum(f["cash"] for f, _ in done)
        net = sum(s["net"] for _, s in done)
        curve, run, peak, mdd = [], 0.0, 0.0, 0.0
        for f, s in sorted(done, key=lambda x: x[1].get("settledAt") or ""):
            run += s["net"]
            peak = max(peak, run)
            mdd = max(mdd, peak - run)
            curve.append(round(run, 2))
        rej = {}
        for e in self.events:
            if e["type"] == "REJECT":
                rej[e["reason"]] = rej.get(e["reason"], 0) + 1
        by_game = {}
        for f in fills:
            by_game.setdefault(f["game"], 0.0)
            by_game[f["game"]] += f["cash"]
        return {"fills": len(fills), "settled": len(done), "open": len(fills) - len(done),
                "openCash": round(self.open_cash(), 2), "cashSettled": round(cash, 2), "net": round(net, 2),
                "roi": (net / cash) if cash > 0 else None, "wins": sum(1 for _, s in done if s["net"] > 0),
                "maxDrawdown": round(mdd, 2), "bankroll": self.bankroll, "rejections": rej,
                "maxGameExposure": round(max(by_game.values()), 2) if by_game else 0.0,
                "partialFills": sum(1 for f in fills if f["partial"]),
                "meanSlippage": (sum(f["slippage"] or 0 for f in fills) / len(fills)) if fills else None}
