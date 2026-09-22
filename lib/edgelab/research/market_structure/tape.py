"""
Trade-tape decomposition (MRV-EXEC-002 / -003).

For each taker print we measure, in cents per contract, from the taker's
point of view:
  halfSpread  = executed price - pre-trade fair mid (mid of the candle that
                closed one minute before the print)
  fee         = taker fee at the executed price (per contract, unrounded)
  drift_h     = signed fair-mid move from the pre-trade mid to +h minutes,
                positive when it moves in the taker's favour
  markToMid_h = drift_h - halfSpread - fee     (taker's net edge at +h)
Maker realized spread at +h = halfSpread - drift_h - makerFee.

A print whose pre-trade mid is unavailable (no two-sided candle at t-1) is
skipped and counted.  Prints after scheduled first pitch are excluded.
"""
import gzip
import json
import os
from datetime import datetime, timezone

from lib.edgelab.research.market_structure import economics as E
from lib.edgelab.research.market_structure.candles import mid

HORIZONS_MIN = (5, 30)


def _minute_end_ts(created_time):
    dt = datetime.strptime(created_time[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    return (ts // 60) * 60 + 60  # end of the minute containing the print


def iter_trade_records(root, date):
    path = os.path.join(root, "trades", "%s.jsonl.gz" % date)
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def decompose_print(tr, series, start_ts, last_pregame_ts, maker_mult=E.MAKER_MULT_CONSERVATIVE):
    """
    tr: one trade dict; series: minute series of the same ticker; returns a
    row dict or None (reason in the caller's counters).
    """
    try:
        yes_price = int(round(float(tr["yes_price_dollars"]) * 100))
        count = float(tr.get("count_fp") or 0)
    except (TypeError, ValueError, KeyError):
        return None
    if count <= 0 or not (0 < yes_price < 100):
        return None
    t_end = _minute_end_ts(tr["created_time"])
    if t_end > start_ts:
        return None  # in-game
    pre = series.get(t_end - 60)
    m0 = mid(pre)
    if m0 is None:
        return None
    side = (tr.get("taker_side") or "").lower()
    if side not in ("yes", "no"):
        return None
    price = yes_price if side == "yes" else 100 - yes_price
    sign = 1.0 if side == "yes" else -1.0            # taker gains when YES mid rises (yes) / falls (no)
    half_spread = sign * (yes_price - m0)              # >= 0 normally
    fee = E.fee_drag_cents(price)
    row = {"tsEnd": t_end, "side": side, "price": price, "count": count, "halfSpread": half_spread, "fee": fee,
           "minutesToStart": (start_ts - t_end) / 60.0}
    for h in HORIZONS_MIN:
        q = series.get(t_end + 60 * h)
        mh = mid(q)
        row["drift_%d" % h] = None if mh is None else sign * (mh - m0)
    q = series.get(last_pregame_ts)
    ml = mid(q)
    row["drift_last"] = None if ml is None else sign * (ml - m0)
    for k in ("5", "30", "last"):
        d = row["drift_%s" % k]
        row["takerNet_%s" % k] = None if d is None else d - half_spread - fee
        row["makerNet_%s" % k] = None if d is None else half_spread - d - E.fee_drag_cents(price, maker_mult)
    return row


def last_two_sided_pregame_minute(series, start_ts):
    best = None
    for ts, q in series.items():
        if ts <= start_ts and q[0] is not None and q[1] is not None and (best is None or ts > best):
            best = ts
    return best


def weighted_mean(rows, key, weight="count"):
    num = den = 0.0
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        w = r.get(weight) or 0.0
        num += v * w
        den += w
    return (num / den) if den > 0 else None
