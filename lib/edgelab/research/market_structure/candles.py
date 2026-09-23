"""
Loader for the recovered Kalshi 1-minute candle record (Release
MLB-ALPHA-0002-KALSHI-RAW-V1, hydrated OUTSIDE git).  The root directory is
injected; nothing here knows a repo path.

Each record line: {ticker, family, gameDate, startTs, endTs, candlesticks:[
  {end_period_ts, yes_bid{open/high/low/close_dollars}, yes_ask{...},
   price{...}, volume_fp, open_interest_fp}]}.

A minute series is a dict minute_ts(int, end of minute) -> (bid_cents, ask_cents,
volume, open_interest).  bid 0 and ask >= 100 are treated as "no quote on
that side" (None) so an empty book is never mistaken for a 0/100 price.
"""
import gzip
import json
import os

from lib.edgelab.research.market_structure.identity import parse_ticker


def _cents(d, key="close_dollars"):
    if not d or d.get(key) is None:
        return None
    try:
        c = int(round(float(d[key]) * 100))
    except (TypeError, ValueError):
        return None
    return c


def series_from_record(rec):
    """-> {minute_ts: (bid, ask, vol, oi)} with None for absent sides."""
    out = {}
    for c in rec.get("candlesticks") or []:
        ts = c.get("end_period_ts")
        if ts is None:
            continue
        bid = _cents(c.get("yes_bid"))
        ask = _cents(c.get("yes_ask"))
        if bid is not None and bid <= 0:
            bid = None
        if ask is not None and ask >= 100:
            ask = None
        try:
            vol = float(c.get("volume_fp") or 0.0)
            oi = float(c.get("open_interest_fp") or 0.0)
        except (TypeError, ValueError):
            vol, oi = 0.0, 0.0
        out[int(ts)] = (bid, ask, vol, oi)
    return out


def iter_date(root, date, families=None):
    """
    Yield (identity, minute_series, record_meta) for every parseable ticker in
    <root>/candles/<date>.jsonl.gz.  `families` filters on the parsed family.
    Unparseable tickers are skipped (counted by the caller if needed).
    """
    path = os.path.join(root, "candles", "%s.jsonl.gz" % date)
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            ident = parse_ticker(rec.get("ticker"))
            if ident.get("status") != "RESOLVED":
                continue
            if families and ident["family"] not in families:
                continue
            yield ident, series_from_record(rec), {"gameDate": rec.get("gameDate"),
                                                    "startTs": rec.get("startTs"), "endTs": rec.get("endTs")}


def available_dates(root):
    d = os.path.join(root, "candles")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-9] for f in os.listdir(d) if f.endswith(".jsonl.gz"))


def group_by_game(items):
    """[(ident, series, meta)] -> {physicalGameKey: [(ident, series, meta)]}"""
    out = {}
    for ident, series, meta in items:
        out.setdefault(ident["physicalGameKey"], []).append((ident, series, meta))
    return out


def scheduled_start_ts(ident):
    """Scheduled first pitch as a unix ts (from the event ticker, ET+4h)."""
    from datetime import timezone
    return int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp())


def quote_at(series, minute_ts):
    """(bid, ask, vol, oi) at exactly minute_ts or None (no forward fill: as-of the same minute only)."""
    return series.get(minute_ts)


def mid(q):
    if q is None or q[0] is None or q[1] is None:
        return None
    return (q[0] + q[1]) / 2.0
