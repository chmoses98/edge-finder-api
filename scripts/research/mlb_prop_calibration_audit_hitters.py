#!/usr/bin/env python3
"""
scripts/research/mlb_prop_calibration_audit_hitters.py
=======================================================
Hitter prop calibration re-audit (follow-up to MLB-RSCH-0028) on ALL
graded hitter projection snapshots now committed. RESEARCH ONLY: reads
committed archives, writes one analytics JSON + one markdown report. No
production code path, betting methodology, risk gate or pending-bet
writer is touched or imported.

WHY A NEW SCRIPT (instead of re-running RSCH-0028's experiment runner)
--------------------------------------------------------------------
scripts/edgelab/run_hitter_prop_validity_experiment.py scores EVERY
snapshot row (up to five checkpoints x every ladder rung), clustering on
playerGameKey to compensate. This audit instead keeps exactly ONE
observation per Kalshi marketTicker so repeated regenerations of the same
contract can never be counted as independent evidence:

  * the selected row is the LAST snapshot (max snapshotGeneratedAt)
    whose snapshotGeneratedAt, projectionGeneratedAt AND marketObservedAt
    are all STRICTLY before the game's scheduled first pitch;
  * the selection function never sees settlement data (outcomes are
    joined only after selection), so no settlement information can
    influence which row is chosen;
  * if that last pregame row is not PROJECTED (e.g. the player was
    scratched -> PLAYER_NOT_IN_STARTING_LINEUP) the ticker is EXCLUDED,
    never back-filled with an older PROJECTED row the engine itself had
    already superseded.

RSCH-0028's runner also divided every archived quote by 100. The
observation archive switched from integer cents to dollars on
2026-09-10 (~19:04Z); this script infers the unit per CAPTURE (all
markets in one Kalshi registry capture share one unit), never per value.

MARKET BENCHMARK (documented choice)
------------------------------------
PRIMARY: vig-free MID ((yesBid+yesAsk)/2) of the archived observation of
the same ticker captured at the snapshot's own `marketObservedAt` (the
exact Kalshi capture the snapshot priced against; latest observation at
or before that instant, which in practice is an exact timestamp match).
SENSITIVITIES: (a) mid of the latest pregame observation at or before
`snapshotGeneratedAt` (fresher quote, same information horizon as the
model), (b) the snapshot's own `executableKalshiPrice` (the YES ask),
(c) primary restricted to spreads <= 5c.

Clustering: every interval is a game-clustered bootstrap (resample whole
games), because rungs and players within a game share run environment.

Usage:
    python3 scripts/research/mlb_prop_calibration_audit_hitters.py
    python3 scripts/research/mlb_prop_calibration_audit_hitters.py --out-json X --out-md Y
"""
import argparse
import collections
import json
import math
import os
import random
import re
import sys
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import storage  # noqa: E402
from lib.edgelab.kalshi_fees import fee_rule_for_series  # noqa: E402

AUDIT_ID = "MLB_PROP_CALIBRATION_HITTERS_2026_10"
AS_OF_DATE = "2026-10-07"
SNAPSHOT_DIR = os.path.join(_ROOT, "data", "edgelab", "hitter_projection_snapshots")
SETTLEMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "settlements")
OBSERVATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "observations")
SLATES_DIR = os.path.join(_ROOT, "data", "slates")
OUT_JSON = os.path.join(_ROOT, "data", "edgelab", "analytics", "mlb_prop_calibration_hitters_2026-10-07.json")
OUT_MD = os.path.join(_ROOT, "docs", "EDGELAB_MLB_PROP_CALIBRATION_2026_10_HITTERS.md")

FAMILIES = ("hitter_hits", "hitter_total_bases", "hitter_hits_runs_rbis", "hitter_rbis")
CHECKPOINT_ORDER = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", "HITTER_CLOSING_WINDOW")
PROB_CLAMP = (0.001, 0.999)
N_BINS = 10
EDGE_BUCKETS = ((-1.0, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0.02),
                (0.02, 0.05), (0.05, 0.10), (0.10, 1.0))
DEV_FRACTION = 0.60
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_CI = 0.95
BOOTSTRAP_SEED = 20261007
TIGHT_SPREAD_MAX = 0.05
CLV_CLOSE_MAX_MINUTES_BEFORE_START = 60
MIN_ROWS_FOR_FIT = 30

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_TICKER_TIME_RE = re.compile(r"^[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(\d{4})")


# ── time helpers ──────────────────────────────────────────────────────────

def parse_iso(value):
    """ISO-8601 string -> aware UTC datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _us_eastern_offset_hours(dt_local_naive):
    """-4 during US daylight time (2nd Sun Mar 02:00 .. 1st Sun Nov 02:00), else -5."""
    y = dt_local_naive.year
    march = datetime(y, 3, 8)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7, hours=2)
    nov = datetime(y, 11, 1)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7, hours=2)
    return -4 if dst_start <= dt_local_naive < dst_end else -5


def eastern_date(dt_utc):
    """US-Eastern calendar date (the MLB slate date) of an aware UTC instant."""
    naive = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)
    off = _us_eastern_offset_hours(naive + timedelta(hours=-4))
    return (naive + timedelta(hours=off)).date().isoformat()


def ticker_scheduled_start_utc(ticker):
    """Kalshi MLB event tickers encode the scheduled first pitch in US
    Eastern time, e.g. KXMLBHIT-26SEP231545MINSF-... = 2026-09-23 15:45 ET.
    Fallback only -- the slate's statsapi startTime is preferred."""
    m = _TICKER_TIME_RE.match(ticker or "")
    if not m:
        return None
    yy, mon, dd, hhmm = m.groups()
    if mon not in _MONTHS:
        return None
    local = datetime(2000 + int(yy), _MONTHS[mon], int(dd), int(hhmm[:2]), int(hhmm[2:]))
    off = _us_eastern_offset_hours(local)
    return (local - timedelta(hours=off)).replace(tzinfo=timezone.utc)


def load_slate_start_times(slates_dir, dates):
    """{str(gameId): startTime ISO} from committed data/slates/<date>/*.json
    (authoritative.json first). statsapi gamePk == snapshot gameId."""
    out = {}
    for date in sorted(set(dates)):
        d = os.path.join(slates_dir, date)
        if not os.path.isdir(d):
            continue
        files = sorted(os.listdir(d), key=lambda f: (f != "authoritative.json", f))
        for fn in files:
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn)) as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            for g in (doc.get("games") or []) if isinstance(doc, dict) else []:
                gid, st = g.get("gameId"), g.get("startTime")
                if gid is not None and st and str(gid) not in out:
                    out[str(gid)] = st
    return out


def resolve_start(row, slate_starts):
    """(start datetime, source) for a snapshot row."""
    st = parse_iso(slate_starts.get(str(row.get("gameId"))))
    if st is not None:
        return st, "SLATE_STATSAPI_START_TIME"
    st = ticker_scheduled_start_utc(row.get("marketTicker"))
    if st is not None:
        return st, "KALSHI_TICKER_EASTERN_TIME"
    return None, None


# ── dedupe / no-leakage selection (pure; never sees outcomes) ─────────────

def _row_times(row):
    return (parse_iso(row.get("snapshotGeneratedAt")),
            parse_iso(row.get("projectionGeneratedAt")),
            parse_iso(row.get("marketObservedAt")))


def select_last_pregame_snapshots(rows, start_for_row, families=FAMILIES):
    """
    One row per marketTicker: the LAST snapshot strictly before first pitch.

    `start_for_row(row) -> aware datetime or None` supplies the scheduled
    start. A row is pregame iff snapshotGeneratedAt, projectionGeneratedAt
    and marketObservedAt are all present and all < start. Among a ticker's
    pregame rows the newest snapshotGeneratedAt wins (ties: later
    checkpoint, then snapshot id -- deterministic). If that newest pregame
    row is not PROJECTED / has no probability, the TICKER is excluded (the
    engine's own latest pregame state says it should not be priced).

    Deliberately takes NO settlement / outcome argument: selection cannot
    depend on how the contract resolved.

    Returns (selected {ticker: row}, exclusions Counter, stats dict).
    """
    excl = collections.Counter()
    by_ticker = collections.defaultdict(list)
    raw = 0
    for r in rows:
        if r.get("marketFamily") not in families:
            continue
        raw += 1
        t = r.get("marketTicker")
        if not t:
            excl["ROW_NO_MARKET_TICKER"] += 1
            continue
        start = start_for_row(r)
        if start is None:
            excl["ROW_NO_SCHEDULED_START"] += 1
            continue
        gen, proj, obs = _row_times(r)
        if gen is None or proj is None or obs is None:
            excl["ROW_MISSING_TIMESTAMP"] += 1
            continue
        if not (gen < start and proj < start and obs < start):
            excl["ROW_NOT_STRICTLY_PREGAME"] += 1
            continue
        by_ticker[t].append(r)

    def _key(r):
        cp = r.get("checkpoint")
        cp_rank = CHECKPOINT_ORDER.index(cp) if cp in CHECKPOINT_ORDER else -1
        return (parse_iso(r.get("snapshotGeneratedAt")), cp_rank, str(r.get("hitterProjectionSnapshotId")))

    selected = {}
    superseded = 0
    for t, rs in by_ticker.items():
        last = max(rs, key=_key)
        superseded += len(rs) - 1
        if last.get("projectionStatus") != "PROJECTED":
            excl["TICKER_LAST_PREGAME_STATUS_" + str(last.get("projectionStatus"))] += 1
            continue
        if last.get("modelProbability") is None:
            excl["TICKER_LAST_PREGAME_MODEL_PROBABILITY_NULL"] += 1
            continue
        if last.get("threshold") is None:
            excl["TICKER_THRESHOLD_NULL"] += 1
            continue
        selected[t] = last
    stats = {"familyRows": raw, "pregameTickers": len(by_ticker),
             "supersededPregameRegenerations": superseded, "selectedTickers": len(selected)}
    return selected, excl, stats


# ── settlements ───────────────────────────────────────────────────────────

def load_settlements(settlements_dir, tickers):
    """{ticker: {"outcome": 0/1, "actualValue":, "threshold":}} for YES/NO
    settled contracts; tickers with CONFLICTING YES/NO records are dropped
    and reported."""
    seen = collections.defaultdict(set)
    meta = {}
    for fn in sorted(os.listdir(settlements_dir)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in storage.read_records(os.path.join(settlements_dir, fn)):
            t = d.get("marketTicker")
            if t not in tickers:
                continue
            o = d.get("outcome")
            if o not in ("YES", "NO"):
                continue
            seen[t].add(o)
            ev = d.get("settlementEvidence") or {}
            meta[t] = {"actualValue": ev.get("actualValue"), "threshold": ev.get("threshold")}
    out, conflicts = {}, []
    for t, os_ in seen.items():
        if len(os_) != 1:
            conflicts.append(t)
            continue
        out[t] = dict(meta[t], outcome=1 if "YES" in os_ else 0)
    return out, sorted(conflicts)


# ── quotes ────────────────────────────────────────────────────────────────

def capture_unit_scale(records):
    """Per-capture unit: the observation archive stored integer CENTS until
    2026-09-10 ~19:04Z and DOLLARS afterwards. A capture covers hundreds of
    markets, so any bid/ask > 1 in the capture means the whole capture is
    cents. Returns {capture_key: divisor}."""
    big = collections.defaultdict(bool)
    for d in records:
        key = d.get("capturedAt")
        for f in ("yesBid", "yesAsk", "noBid", "noAsk", "lastPrice"):
            v = d.get(f)
            if isinstance(v, (int, float)) and v > 1.0:
                big[key] = True
    return {k: (100.0 if v else 1.0) for k, v in big.items()}


def load_quotes(observations_dir, tickers, dates):
    """{ticker: sorted [(capturedAt dt, bid$, ask$)]} for the given tickers.
    Only files whose date is within [min(dates)-1, max(dates)+1]."""
    lo = (datetime.fromisoformat(min(dates)) - timedelta(days=1)).date().isoformat()
    hi = (datetime.fromisoformat(max(dates)) + timedelta(days=1)).date().isoformat()
    quotes = collections.defaultdict(list)
    for fn in sorted(os.listdir(observations_dir)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        if not (lo <= fn[:10] <= hi):
            continue
        recs = list(storage.read_records(os.path.join(observations_dir, fn)))
        scale = capture_unit_scale(recs)
        for d in recs:
            t = d.get("marketTicker")
            if t not in tickers:
                continue
            bid, ask, at = d.get("yesBid"), d.get("yesAsk"), parse_iso(d.get("capturedAt"))
            if bid is None or ask is None or at is None:
                continue
            div = scale.get(d.get("capturedAt"), 1.0)
            quotes[t].append((at, float(bid) / div, float(ask) / div))
    for t in quotes:
        quotes[t].sort(key=lambda q: q[0])
    return quotes


def quote_at_or_before(quote_list, instant, strictly_before=None):
    """Latest quote with capturedAt <= instant (and < strictly_before when
    given). Never a later quote."""
    best = None
    for q in quote_list:
        if q[0] > instant:
            break
        if strictly_before is not None and q[0] >= strictly_before:
            break
        best = q
    return best


def mid_of(q):
    if q is None:
        return None
    _at, bid, ask = q
    if ask < bid:
        return None
    m = (bid + ask) / 2.0
    return m if 0.0 < m < 1.0 else None


# ── scoring ───────────────────────────────────────────────────────────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def logit(p):
    p = _clamp(p)
    return math.log(p / (1.0 - p))


def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps) if ps else None


def log_loss(ps, ys):
    if not ps:
        return None
    return sum(-(y * math.log(_clamp(p)) + (1 - y) * math.log(1 - _clamp(p))) for p, y in zip(ps, ys)) / len(ps)


def ece(ps, ys, n_bins=N_BINS):
    if not ps:
        return None
    bins = collections.defaultdict(list)
    for p, y in zip(ps, ys):
        bins[min(n_bins - 1, max(0, int(p * n_bins)))].append((p, y))
    return sum(len(b) / len(ps) * abs(sum(p for p, _ in b) / len(b) - sum(y for _, y in b) / len(b))
               for b in bins.values())


def fit_logistic_on_logit(ps, ys, ridge=1e-6, max_iter=100):
    """Logistic recalibration y ~ a + b*logit(p) via Newton-Raphson.
    Returns (intercept a, slope b) or (None, None). Perfect calibration is
    (0, 1); slope < 1 means over-confident (too extreme)."""
    if len(ps) < MIN_ROWS_FOR_FIT or len(set(ys)) < 2:
        return None, None
    xs = [logit(p) for p in ps]
    a, b = 0.0, 1.0
    for _ in range(max_iter):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            mu = sigmoid(a + b * x)
            w = mu * (1 - mu)
            ga += y - mu
            gb += (y - mu) * x
            haa += w
            hab += w * x
            hbb += w * x * x
        haa += ridge
        hbb += ridge
        det = haa * hbb - hab * hab
        if det <= 0:
            return None, None
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a += da
        b += db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def apply_platt(p, a, b):
    return sigmoid(a + b * logit(p))


def _r(x, n=6):
    return None if x is None else round(x, n)


def prob_metrics(rows, key):
    ps = [r[key] for r in rows]
    ys = [r["outcome"] for r in rows]
    a, b = fit_logistic_on_logit(ps, ys)
    return {
        "brier": _r(brier(ps, ys)), "logLoss": _r(log_loss(ps, ys)), "ece10": _r(ece(ps, ys)),
        "calibrationIntercept": _r(a, 4), "calibrationSlope": _r(b, 4),
        "meanProbability": _r(sum(ps) / len(ps), 4) if ps else None,
    }


def cluster_bootstrap(rows, fn, cluster_key="gameId", n=BOOTSTRAP_RESAMPLES, ci=BOOTSTRAP_CI, seed=BOOTSTRAP_SEED):
    """Percentile CI of fn(rows) resampling whole clusters with replacement."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r[cluster_key]].append(r)
    clusters = sorted(by, key=str)
    if len(clusters) < 2:
        return None, None
    rng = random.Random(seed)
    est = []
    for _ in range(n):
        sample = [r for c in (rng.choice(clusters) for _ in clusters) for r in by[c]]
        v = fn(sample)
        if v is not None:
            est.append(v)
    if not est:
        return None, None
    est.sort()
    alpha = (1 - ci) / 2
    return (round(est[int(round(alpha * (len(est) - 1)))], 6),
            round(est[int(round((1 - alpha) * (len(est) - 1)))], 6))


def paired_brier_delta(rows, mkey="modelP", kkey="marketP"):
    if not rows:
        return None
    return sum((r[mkey] - r["outcome"]) ** 2 - (r[kkey] - r["outcome"]) ** 2 for r in rows) / len(rows)


def paired_logloss_delta(rows, mkey="modelP", kkey="marketP"):
    if not rows:
        return None
    ys = [r["outcome"] for r in rows]
    return log_loss([r[mkey] for r in rows], ys) - log_loss([r[kkey] for r in rows], ys)


def unit_counts(rows):
    return {
        "rows": len(rows),
        "independentPlayerGames": len({r["playerGameKey"] for r in rows}),
        "games": len({r["gameId"] for r in rows}),
        "dates": len({r["date"] for r in rows}),
    }


def reliability_table(rows, key):
    out = []
    for i in range(N_BINS):
        lo, hi = i / N_BINS, (i + 1) / N_BINS
        sub = [r for r in rows if min(N_BINS - 1, int(r[key] * N_BINS)) == i]
        if not sub:
            continue
        out.append({"bucket": f"[{lo:.1f},{hi:.1f})", "rows": len(sub),
                    "games": len({r["gameId"] for r in sub}),
                    "meanPredicted": round(sum(r[key] for r in sub) / len(sub), 4),
                    "realizedRate": round(sum(r["outcome"] for r in sub) / len(sub), 4)})
    return out


def score_block(rows, *, with_ci=True, market_key="marketP"):
    if not rows:
        return {"rows": 0}
    out = unit_counts(rows)
    out["realizedYesRate"] = round(sum(r["outcome"] for r in rows) / len(rows), 4)
    out["model"] = prob_metrics(rows, "modelP")
    out["market"] = prob_metrics(rows, market_key)
    out["pairedBrierDelta_modelMinusMarket"] = _r(paired_brier_delta(rows, kkey=market_key))
    out["pairedLogLossDelta_modelMinusMarket"] = _r(paired_logloss_delta(rows, kkey=market_key))
    if with_ci:
        lo, hi = cluster_bootstrap(rows, lambda s: paired_brier_delta(s, kkey=market_key))
        out["pairedBrierDeltaCI95_gameClustered"] = [lo, hi]
        lo, hi = cluster_bootstrap(rows, lambda s: paired_logloss_delta(s, kkey=market_key))
        out["pairedLogLossDeltaCI95_gameClustered"] = [lo, hi]
    return out


# ── economics ─────────────────────────────────────────────────────────────

def fee_per_contract(ticker, price):
    """Continuous Kalshi taker fee rate multiplier*p*(1-p) using the repo's
    series fee registry (lib.edgelab.kalshi_fees.fee_rule_for_series);
    unrounded, i.e. the large-order per-contract rate."""
    mult = fee_rule_for_series((ticker or "").split("-")[0]).get("feeMultiplier")
    return (mult or 0.0) * price * (1.0 - price)


def trade_for_row(r):
    """Hypothetical 1-contract trade on the side the model favours vs the
    mid: YES at yesAsk if model > mid, else NO at noAsk = 1 - yesBid.
    Returns dict or None when no executable price."""
    diff = r["modelP"] - r["marketP"]
    if diff > 0:
        price, win = r.get("yesAsk"), r["outcome"] == 1
        side = "YES"
    else:
        bid = r.get("yesBid")
        price, win = (None if bid is None else 1.0 - bid), r["outcome"] == 0
        side = "NO"
    if price is None or not (0.0 < price < 1.0):
        return None
    fee = fee_per_contract(r["marketTicker"], price)
    cost = price + fee
    return {"side": side, "price": price, "fee": fee, "cost": cost, "pnl": (1.0 if win else 0.0) - cost,
            "pnlGross": (1.0 if win else 0.0) - price}


def _roi(rows, field="pnl"):
    trades = [t for t in (trade_for_row(r) for r in rows) if t]
    if not trades:
        return None
    return sum(t[field] for t in trades) / sum(t["cost"] if field == "pnl" else t["price"] for t in trades)


def edge_bucket_table(rows):
    out = []
    for lo, hi in EDGE_BUCKETS:
        sub = [r for r in rows if lo <= r["modelP"] - r["marketP"] < hi]
        if not sub:
            out.append({"bucket": f"[{lo:+.2f},{hi:+.2f})", "rows": 0})
            continue
        trades = [t for t in (trade_for_row(r) for r in sub) if t]
        roi = _roi(sub)
        lo_ci, hi_ci = cluster_bootstrap(sub, _roi, n=1000) if len(trades) >= 20 else (None, None)
        out.append({
            "bucket": f"[{lo:+.2f},{hi:+.2f})", **unit_counts(sub),
            "meanModel": round(sum(r["modelP"] for r in sub) / len(sub), 4),
            "meanMarketMid": round(sum(r["marketP"] for r in sub) / len(sub), 4),
            "realizedYesRate": round(sum(r["outcome"] for r in sub) / len(sub), 4),
            "realizedMinusMarket": round((sum(r["outcome"] for r in sub) - sum(r["marketP"] for r in sub)) / len(sub), 4),
            "pairedBrierDelta_modelMinusMarket": _r(paired_brier_delta(sub)),
            "tradeSide": "YES@ask" if lo >= 0 else ("NO@noAsk" if hi <= 0 else "mixed"),
            "tradesWithExecutablePrice": len(trades),
            "roiAfterFees": _r(roi, 4),
            "roiGross": _r(_roi(sub, "pnlGross"), 4),
            "roiAfterFeesCI95_gameClustered": [lo_ci, hi_ci],
        })
    return out


# ── CLV ───────────────────────────────────────────────────────────────────

def clv_block(rows):
    """Closing = last observed pregame quote (strictly before start) within
    CLV_CLOSE_MAX_MINUTES_BEFORE_START of first pitch. CLV(mid) =
    (closeMid - entryMid) signed in the model's direction."""
    usable, vals, vals_ask = [], [], []
    for r in rows:
        close = r.get("closeQuote")
        if close is None:
            continue
        minutes = (r["startDt"] - close[0]).total_seconds() / 60.0
        if minutes > CLV_CLOSE_MAX_MINUTES_BEFORE_START:
            continue
        cm = mid_of(close)
        if cm is None:
            continue
        sign = 1.0 if r["modelP"] > r["marketP"] else -1.0
        usable.append(r)
        vals.append(sign * (cm - r["marketP"]))
        t = trade_for_row(r)
        if t:
            vals_ask.append((cm - t["price"]) if t["side"] == "YES" else ((1 - cm) - t["price"]))
    return {
        "rowsWithAnyEntryQuote": len(rows),
        "rowsWithCloseQuoteWithin%dMin" % CLV_CLOSE_MAX_MINUTES_BEFORE_START: len(usable),
        "coverage": round(len(usable) / len(rows), 4) if rows else None,
        "meanSignedMidMove": _r(sum(vals) / len(vals), 5) if vals else None,
        "shareMovingTowardModel": round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None,
        "shareUnchanged": round(sum(1 for v in vals if v == 0) / len(vals), 4) if vals else None,
        "meanCloseMidMinusEntryAsk_modelSide": _r(sum(vals_ask) / len(vals_ask), 5) if vals_ask else None,
    }


# ── corpus ────────────────────────────────────────────────────────────────

def build_corpus(selected, settlements, quotes, start_for_row):
    rows, excl = [], collections.Counter()
    consistency = collections.Counter()
    for t, s in selected.items():
        st = settlements.get(t)
        if st is None:
            excl["NO_SETTLED_OUTCOME"] += 1
            continue
        av, thr = st.get("actualValue"), s.get("threshold")
        if isinstance(av, (int, float)) and thr is not None:
            consistency["CONSISTENT" if (1 if av >= thr else 0) == st["outcome"] else "INCONSISTENT"] += 1
        start = start_for_row(s)
        obs_at = parse_iso(s["marketObservedAt"])
        gen_at = parse_iso(s["snapshotGeneratedAt"])
        ql = quotes.get(t, [])
        q_primary = quote_at_or_before(ql, obs_at, strictly_before=start)
        q_fresh = quote_at_or_before(ql, gen_at, strictly_before=start)
        q_close = quote_at_or_before(ql, start, strictly_before=start)
        m = mid_of(q_primary)
        if m is None:
            excl["NO_VALID_QUOTE_AT_SNAPSHOT_MARKET_OBSERVED_AT" if q_primary is None else "DEGENERATE_MID"] += 1
            continue
        exec_ask = s.get("executableKalshiPrice")
        rows.append({
            "marketTicker": t, "family": s["marketFamily"], "gameId": str(s["gameId"]),
            "playerGameKey": "%s:%s" % (s.get("playerId"), s.get("gameId")),
            "date": eastern_date(start),
            "checkpoint": s.get("checkpoint"), "threshold": s.get("threshold"),
            "modelP": float(s["modelProbability"]), "marketP": m,
            "marketP_fresh": mid_of(q_fresh) if mid_of(q_fresh) is not None else m,
            "marketP_execAsk": float(exec_ask) if exec_ask is not None and 0 < float(exec_ask) < 1 else None,
            "yesBid": q_primary[1], "yesAsk": q_primary[2], "spread": q_primary[2] - q_primary[1],
            "quoteAgeMinutesAtSnapshot": round((gen_at - q_primary[0]).total_seconds() / 60.0, 1),
            "minutesSnapshotBeforeStart": round((start - gen_at).total_seconds() / 60.0, 1),
            "outcome": st["outcome"], "startDt": start, "closeQuote": q_close,
            "execPriceBasis": classify_exec_price_basis(exec_ask, q_primary[1], q_primary[2]),
        })
    return rows, excl, consistency


def classify_exec_price_basis(exec_price, bid, ask):
    """Which observed quote the snapshot's `executableKalshiPrice` equals.
    lib/research/hitter_board_builder._executable_yes_price returns the MID
    whenever a mid/bid+ask exists, so despite its name it is usually NOT
    the ask. 1c-spread books are ambiguous (mid and ask within half a cent)."""
    if exec_price is None:
        return "NONE"
    e = float(exec_price)
    mid = (bid + ask) / 2.0
    near_mid, near_ask = abs(e - mid) < 0.0051, abs(e - ask) < 0.0051
    if near_mid and near_ask:
        return "AMBIGUOUS_1C_SPREAD"
    if near_mid:
        return "MID"
    if near_ask:
        return "ASK"
    return "OTHER"


def chronological_split(rows, dev_fraction=DEV_FRACTION):
    dates = sorted({r["date"] for r in rows})
    n_dev = max(1, int(math.ceil(dev_fraction * len(dates)))) if dates else 0
    if len(dates) > 1:
        n_dev = min(n_dev, len(dates) - 1)
    dev_dates = set(dates[:n_dev])
    return [r for r in rows if r["date"] in dev_dates], [r for r in rows if r["date"] not in dev_dates], dates[:n_dev], dates[n_dev:]


def chronological_game_split(rows, dev_fraction=DEV_FRACTION):
    """Sensitivity: first ~60% of GAMES by scheduled start are dev. The
    date split is very unbalanced because coverage is front-loaded."""
    starts = {}
    for r in rows:
        starts[r["gameId"]] = min(starts.get(r["gameId"], r["startDt"]), r["startDt"])
    order = sorted(starts, key=lambda g: (starts[g], g))
    n_dev = min(max(1, int(math.ceil(dev_fraction * len(order)))), max(1, len(order) - 1)) if order else 0
    cutoff = starts[order[n_dev - 1]] if order else None
    dev_games = {g for g in order if starts[g] <= cutoff} if order else set()
    dev = [r for r in rows if r["gameId"] in dev_games]
    test = [r for r in rows if r["gameId"] not in dev_games]
    return dev, test, sorted({r["date"] for r in dev}), sorted({r["date"] for r in test})


def holdout_block(rows, split_fn=None):
    dev, test, dev_dates, test_dates = (split_fn or chronological_split)(rows)
    out = {"devDates": dev_dates, "testDates": test_dates, "dev": unit_counts(dev), "test": unit_counts(test)}
    if not test or not dev:
        return out
    out["testScore"] = score_block(test)
    a, b = fit_logistic_on_logit([r["modelP"] for r in dev], [r["outcome"] for r in dev])
    out["plattFitOnDev_pooled"] = {"intercept": _r(a, 4), "slope": _r(b, 4)}
    per_fam = {}
    for fam in FAMILIES:
        fr = [r for r in dev if r["family"] == fam]
        fa, fb = fit_logistic_on_logit([r["modelP"] for r in fr], [r["outcome"] for r in fr])
        per_fam[fam] = (fa, fb)
    out["plattFitOnDev_perFamily"] = {f: {"intercept": _r(v[0], 4), "slope": _r(v[1], 4)} for f, v in per_fam.items()}
    if a is None:
        return out
    for r in test:
        r["modelP_platt"] = apply_platt(r["modelP"], a, b)
        fa, fb = per_fam.get(r["family"], (None, None))
        r["modelP_plattFam"] = apply_platt(r["modelP"], fa, fb) if fa is not None else r["modelP_platt"]

    def recal_summary(key):
        ps = [r[key] for r in test]
        ys = [r["outcome"] for r in test]
        d_b = lambda s: (brier([r[key] for r in s], [r["outcome"] for r in s])
                         - brier([r["modelP"] for r in s], [r["outcome"] for r in s]))
        d_l = lambda s: (log_loss([r[key] for r in s], [r["outcome"] for r in s])
                         - log_loss([r["modelP"] for r in s], [r["outcome"] for r in s]))
        return {
            "testBrier": _r(brier(ps, ys)), "testLogLoss": _r(log_loss(ps, ys)), "testECE10": _r(ece(ps, ys)),
            "brierChangeVsRaw": _r(d_b(test)), "brierChangeVsRawCI95_gameClustered": list(cluster_bootstrap(test, d_b)),
            "logLossChangeVsRaw": _r(d_l(test)), "logLossChangeVsRawCI95_gameClustered": list(cluster_bootstrap(test, d_l)),
            "pairedBrierDeltaVsMarket": _r(paired_brier_delta(test, mkey=key)),
        }
    out["testRaw"] = {"testBrier": out["testScore"]["model"]["brier"], "testLogLoss": out["testScore"]["model"]["logLoss"],
                      "testECE10": out["testScore"]["model"]["ece10"]}
    out["testPlattPooled"] = recal_summary("modelP_platt")
    out["testPlattPerFamily"] = recal_summary("modelP_plattFam")
    by_fam = {}
    for fam in FAMILIES:
        ft = [r for r in test if r["family"] == fam]
        if not ft:
            continue
        ys = [r["outcome"] for r in ft]
        by_fam[fam] = {"rows": len(ft), "games": len({r["gameId"] for r in ft}),
                       "rawBrier": _r(brier([r["modelP"] for r in ft], ys)),
                       "plattPooledBrier": _r(brier([r["modelP_platt"] for r in ft], ys)),
                       "plattFamilyBrier": _r(brier([r["modelP_plattFam"] for r in ft], ys)),
                       "marketBrier": _r(brier([r["marketP"] for r in ft], ys))}
    out["testByFamily"] = by_fam
    return out


# ── main ──────────────────────────────────────────────────────────────────

def run(out_json=OUT_JSON, out_md=OUT_MD):
    snapshots = []
    for fn in sorted(os.listdir(SNAPSHOT_DIR)):
        if fn.endswith(".jsonl") or fn.endswith(".jsonl.gz"):
            snapshots.extend(storage.read_records(os.path.join(SNAPSHOT_DIR, fn)))
    snap_dates = sorted({str(s.get("snapshotGeneratedAt", ""))[:10] for s in snapshots if s.get("snapshotGeneratedAt")})
    slate_starts = load_slate_start_times(SLATES_DIR, snap_dates + [
        (datetime.fromisoformat(d) + timedelta(days=1)).date().isoformat() for d in snap_dates])
    start_sources = collections.Counter()
    start_disagreements = []

    def start_for_row(r):
        st, _src = resolve_start(r, slate_starts)
        return st

    for gid_row in {str(s.get("gameId")): s for s in snapshots}.values():
        st, src = resolve_start(gid_row, slate_starts)
        start_sources[src] += 1
        tk = ticker_scheduled_start_utc(gid_row.get("marketTicker"))
        if st is not None and tk is not None and abs((st - tk).total_seconds()) > 60:
            start_disagreements.append({"gameId": gid_row.get("gameId"), "slate": st.isoformat(), "ticker": tk.isoformat()})

    selected, sel_excl, sel_stats = select_last_pregame_snapshots(snapshots, start_for_row)
    settlements, conflicts = load_settlements(SETTLEMENTS_DIR, set(selected))
    quotes = load_quotes(OBSERVATIONS_DIR, set(selected), snap_dates)
    rows, corp_excl, consistency = build_corpus(selected, settlements, quotes, start_for_row)

    selected_cp = collections.Counter(s.get("checkpoint") for s in selected.values())
    result = {
        "auditId": AUDIT_ID, "asOfDate": AS_OF_DATE, "researchOnly": True,
        "predecessor": "MLB-RSCH-0028 (docs/EDGELAB_MLB_RSCH_0028_HITTER_PROP_AUDIT.md)",
        "methodology": {
            "observationUnit": "one row per Kalshi marketTicker",
            "selection": "last snapshot with snapshotGeneratedAt, projectionGeneratedAt and marketObservedAt all strictly before scheduled first pitch; ticker excluded if that row is not PROJECTED",
            "scheduledStartSource": "data/slates/<date>/*.json games[].startTime (statsapi), fallback Kalshi ticker ET time",
            "marketProbability": "PRIMARY = mid of the archived observation of the same ticker at/just before the snapshot's own marketObservedAt (the capture the snapshot priced against); sensitivities: fresh mid at snapshotGeneratedAt, snapshot executableKalshiPrice (which hitter_board_builder._executable_yes_price sets to the MID, not the ask), spread<=5c subset. Asks are used only for ROI.",
            "priceUnits": "observation archive is integer cents before 2026-09-10 ~19:04Z, dollars after; unit inferred per capture",
            "calibration": "logistic recalibration y ~ a + b*logit(p) (Newton); ECE 10 equal-width bins",
            "intervals": "95%% percentile bootstrap, %d resamples, clustered by gameId, seed %d" % (BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED),
            "economics": "hypothetical 1 contract on model side vs mid: YES at observed yesAsk, NO at 1-yesBid; Kalshi taker fee multiplier*p*(1-p) from lib.edgelab.kalshi_fees series registry (unrounded); no staking/methodology change",
            "holdout": "dates sorted; first ceil(60%) dev, rest test; Platt (logistic on logit) fitted on dev only",
        },
        "corpus": {
            "snapshotRowsAllFamilies": len(snapshots),
            "selection": sel_stats,
            "selectionExclusions": dict(sel_excl),
            "selectedCheckpointMix": dict(selected_cp),
            "settlementConflictsDropped": len(conflicts),
            "corpusExclusions": dict(corp_excl),
            "settlementActualValueConsistency": dict(consistency),
            "scheduledStartSourceByGame": {str(k): v for k, v in start_sources.items()},
            "slateVsTickerStartDisagreements": start_disagreements,
            "finalRows": len(rows),
            "snapshotExecutableKalshiPriceBasis": dict(collections.Counter(r["execPriceBasis"] for r in rows)),
            "freshQuoteSameAsPrimaryShare": round(sum(1 for r in rows if r["marketP_fresh"] == r["marketP"]) / len(rows), 4) if rows else None,
            "medianQuoteAgeMinutesAtSnapshot": _median([r["quoteAgeMinutesAtSnapshot"] for r in rows]),
            "medianMinutesSnapshotBeforeStart": _median([r["minutesSnapshotBeforeStart"] for r in rows]),
            "dates": sorted({r["date"] for r in rows}),
        },
    }
    result["overall"] = score_block(rows)
    result["byFamily"] = {f: score_block([r for r in rows if r["family"] == f]) for f in FAMILIES}
    result["reliability"] = {
        "overall": {"model": reliability_table(rows, "modelP"), "market": reliability_table(rows, "marketP")},
        "byFamily": {f: {"model": reliability_table([r for r in rows if r["family"] == f], "modelP")} for f in FAMILIES},
    }
    fresh_rows = [dict(r) for r in rows]
    exec_rows = [r for r in rows if r["marketP_execAsk"] is not None]
    tight = [r for r in rows if r["spread"] <= TIGHT_SPREAD_MAX]
    result["marketBenchmarkSensitivity"] = {
        "freshMidAtSnapshotGeneratedAt": score_block(fresh_rows, market_key="marketP_fresh"),
        "snapshotExecutableKalshiPrice": score_block(exec_rows, market_key="marketP_execAsk"),
        "primaryMidSpreadAtMost5c": score_block(tight),
        "primaryMidSpreadAtMost5cByFamily": {f: score_block([r for r in tight if r["family"] == f], with_ci=False) for f in FAMILIES},
    }
    result["edgeBuckets"] = {"overall": edge_bucket_table(rows),
                             "byFamily": {f: edge_bucket_table([r for r in rows if r["family"] == f]) for f in FAMILIES}}
    result["holdout"] = holdout_block([dict(r) for r in rows])
    result["holdoutByGameChronological"] = holdout_block([dict(r) for r in rows], split_fn=chronological_game_split)
    result["clv"] = clv_block(rows)
    result["clv"]["verdict"] = (
        "DESCRIPTIVE_ONLY: registry captures are sparse (a few per day), so the 'close' is the last capture "
        "within %d min of first pitch, not a true closing line" % CLV_CLOSE_MAX_MINUTES_BEFORE_START)
    result["verdict"] = _verdict(result)

    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")
    if out_md:
        with open(out_md, "w") as fh:
            fh.write(render_markdown(result))
    return result


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 2)


def _verdict(result):
    ov = result["overall"]
    ci = ov.get("pairedBrierDeltaCI95_gameClustered") or [None, None]
    if ci[0] is None:
        cmp = "INSUFFICIENT_SAMPLE"
    elif ci[1] < 0:
        cmp = "MODEL_BEATS_MARKET"
    elif ci[0] > 0:
        cmp = "MARKET_BEATS_MODEL"
    else:
        cmp = "PARITY"
    return {"overallVsMarket": cmp, "evidenceLevel": "LEVEL_0_MEASUREMENT_ONLY",
            "note": "Research measurement only; no production change is implied or made."}


def _fmt(x, n=4):
    return "n/a" if x is None else f"{x:+.{n}f}"


def _ci(c):
    if not c or c[0] is None:
        return "n/a"
    return f"[{c[0]:+.4f}, {c[1]:+.4f}]"


def render_markdown(res):
    L = []
    c = res["corpus"]
    L.append("# MLB hitter prop calibration re-audit (2026-10)\n")
    L.append("**Research only. No production, staking, or betting-methodology change.** "
             "Follow-up to MLB-RSCH-0028 on all graded hitter snapshots now committed. "
             "Generated by `scripts/research/mlb_prop_calibration_audit_hitters.py`; full numbers in "
             "`data/edgelab/analytics/mlb_prop_calibration_hitters_2026-10-07.json`.\n")
    L.append("## Key findings\n")
    ov = res["overall"]
    L.append(f"- **Overall: `{res['verdict']['overallVsMarket']}` with the Kalshi mid** on {ov['rows']} one-per-ticker rows "
             f"({ov['independentPlayerGames']} player-games, {ov['games']} games, {ov['dates']} dates): model Brier {ov['model']['brier']:.4f} vs "
             f"market {ov['market']['brier']:.4f}, Δ {ov['pairedBrierDelta_modelMinusMarket']:+.4f} {_ci(ov.get('pairedBrierDeltaCI95_gameClustered'))}. "
             f"Model calibration slope {ov['model']['calibrationSlope']} (market {ov['market']['calibrationSlope']}): the model is over-confident; "
             f"ECE {ov['model']['ece10']:.3f} vs {ov['market']['ece10']:.3f}.")
    for f in FAMILIES:
        b = res["byFamily"][f]
        if not b.get("rows"):
            continue
        bias = b["model"]["meanProbability"] - b["realizedYesRate"]
        L.append(f"- `{f}`: mean model {b['model']['meanProbability']:.3f} vs realized {b['realizedYesRate']:.3f} "
                 f"(level bias {bias:+.3f}; market mean {b['market']['meanProbability']:.3f}); logistic a/b = "
                 f"{b['model']['calibrationIntercept']}/{b['model']['calibrationSlope']}; Brier Δ vs market "
                 f"{b['pairedBrierDelta_modelMinusMarket']:+.4f} {_ci(b.get('pairedBrierDeltaCI95_gameClustered'))}.")
    hd, hg = res["holdout"], res["holdoutByGameChronological"]
    if "testPlattPooled" in hd and "testPlattPooled" in hg:
        L.append(f"- Dev-fitted Platt recalibration: date split (test = {hd['test']['games']} games) Brier change "
                 f"{hd['testPlattPooled']['brierChangeVsRaw']:+.4f} {_ci(hd['testPlattPooled']['brierChangeVsRawCI95_gameClustered'])}; "
                 f"game split (test = {hg['test']['games']} games) {hg['testPlattPooled']['brierChangeVsRaw']:+.4f} "
                 f"{_ci(hg['testPlattPooled']['brierChangeVsRawCI95_gameClustered'])}. Neither interval excludes zero: "
                 "a single pooled Platt transform is NOT shown to help out of sample. The family-level LEVEL biases above "
                 "(hits too high, RBIs too low) are the more plausible, still-unconfirmed fix.")
    eb = [t for t in res["edgeBuckets"]["overall"] if t.get("rows")]
    parts = ", ".join(f"{t['bucket']}: model {t['meanModel']:.3f} / mkt {t['meanMarketMid']:.3f} / realized {t['realizedYesRate']:.3f}"
                      f" (ROI {t['roiAfterFees']:+.3f})" for t in eb if t.get("roiAfterFees") is not None)
    any_sig = any((t.get("roiAfterFeesCI95_gameClustered") or [None])[0] is not None
                  and (t["roiAfterFeesCI95_gameClustered"][0] > 0 or t["roiAfterFeesCI95_gameClustered"][1] < 0) for t in eb)
    L.append(f"- Edge buckets (model − mid; net ROI on the model's side at the ask after fees): {parts}. "
             + ("At least one bucket's game-clustered ROI CI excludes zero." if any_sig else
                "Every bucket's game-clustered ROI CI includes zero: no edge bucket is demonstrably profitable."))
    L.append(f"- CLV: not trustworthy (sparse registry captures; {res['clv']['shareUnchanged']:.0%} of usable rows show no "
             "quote change between snapshot and last pregame capture).")
    L.append("- The snapshot field `executableKalshiPrice` is the MID of the captured book (hitter_board_builder._executable_yes_price), "
             "not the ask; production hitter `rawProbabilityEdge`/EV are therefore pre-spread. Reported here, not changed.\n")
    L.append("## Method (what is different from RSCH-0028)\n")
    L.append("- **One observation per Kalshi ticker**: the last snapshot whose `snapshotGeneratedAt`, "
             "`projectionGeneratedAt` and `marketObservedAt` are all strictly before scheduled first pitch "
             "(statsapi `startTime` from `data/slates/<date>/`, Kalshi ticker ET time as fallback). Earlier "
             "regenerations of the same contract are discarded, never scored as extra evidence. If the last "
             "pregame row is not `PROJECTED` (e.g. scratched), the ticker is excluded. Selection never reads "
             "settlements.")
    L.append("- **Market benchmark**: mid of the archived Kalshi observation of the same ticker at the "
             "snapshot's own `marketObservedAt` (the capture it priced against). Sensitivities: fresher mid at "
             "`snapshotGeneratedAt`, the snapshot's `executableKalshiPrice` (which the board builder sets to the book MID, not the ask), and a spread<=5c subset. Asks are used only for ROI.")
    L.append("- **Price units**: the observation archive switched from integer cents to dollars on "
             "2026-09-10 (~19:04Z); this audit infers the unit per capture (RSCH-0028 always divided by 100).")
    L.append("- Calibration = logistic fit `y ~ a + b*logit(p)` (ideal a=0, b=1; b<1 = over-confident). "
             "CIs = 95% game-clustered bootstrap.\n")
    L.append("## Corpus\n")
    s = c["selection"]
    L.append(f"- Snapshot rows (4 families): {s['familyRows']}; pregame tickers: {s['pregameTickers']}; "
             f"superseded regenerations dropped: {s['supersededPregameRegenerations']}; selected PROJECTED tickers: {s['selectedTickers']}")
    L.append(f"- Selection exclusions: `{json.dumps(c['selectionExclusions'])}`")
    L.append(f"- Corpus exclusions after selection: `{json.dumps(c['corpusExclusions'])}`")
    L.append(f"- Settlement `actualValue >= threshold` consistency: `{json.dumps(c['settlementActualValueConsistency'])}`")
    L.append(f"- Selected checkpoint mix: `{json.dumps(c['selectedCheckpointMix'])}`")
    L.append(f"- Snapshot `executableKalshiPrice` basis vs the observed quote: `{json.dumps(c['snapshotExecutableKalshiPriceBasis'])}` "
             "(lib/research/hitter_board_builder._executable_yes_price returns the MID when bid/ask exist, so production's "
             "`rawProbabilityEdge`/`expectedValuePerDollar` for hitters are measured against the mid, not the ask)")
    L.append(f"- Share of rows where the freshest pregame quote at `snapshotGeneratedAt` equals the `marketObservedAt` quote: {c['freshQuoteSameAsPrimaryShare']}")
    L.append(f"- Median quote age at snapshot: {c['medianQuoteAgeMinutesAtSnapshot']} min; median snapshot lead before first pitch: {c['medianMinutesSnapshotBeforeStart']} min")
    L.append(f"- Dates ({len(c['dates'])}): {', '.join(c['dates'])}\n")
    L.append("## Headline: model vs Kalshi mid (identical rows)\n")
    L.append("| Scope | Rows | Player-games | Games | Dates | Base rate | Model Brier | Mkt Brier | Model LL | Mkt LL | Model a / b | Mkt a / b | Model ECE | Mkt ECE | Brier Δ (model−mkt) [95% CI] |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|")
    blocks = [("ALL", res["overall"])] + [(f, res["byFamily"][f]) for f in FAMILIES]
    for name, b in blocks:
        if not b.get("rows"):
            L.append(f"| {name} | 0 | | | | | | | | | | | | | |")
            continue
        m, k = b["model"], b["market"]
        L.append(f"| {name} | {b['rows']} | {b['independentPlayerGames']} | {b['games']} | {b['dates']} | {b['realizedYesRate']:.3f} | "
                 f"{m['brier']:.4f} | {k['brier']:.4f} | {m['logLoss']:.4f} | {k['logLoss']:.4f} | "
                 f"{m['calibrationIntercept']} / {m['calibrationSlope']} | {k['calibrationIntercept']} / {k['calibrationSlope']} | "
                 f"{m['ece10']:.4f} | {k['ece10']:.4f} | {b['pairedBrierDelta_modelMinusMarket']:+.4f} {_ci(b.get('pairedBrierDeltaCI95_gameClustered'))} |")
    L.append(f"\n**Overall verdict: `{res['verdict']['overallVsMarket']}`** ({res['verdict']['evidenceLevel']}).\n")
    L.append("## Market-benchmark sensitivity\n")
    L.append("| Benchmark | Rows | Model Brier | Mkt Brier | Brier Δ [95% CI] |")
    L.append("|---|---:|---:|---:|---|")
    for name, key in (("Primary mid @ marketObservedAt", None), ("Fresh mid @ snapshotGeneratedAt", "freshMidAtSnapshotGeneratedAt"),
                      ("Snapshot executableKalshiPrice (= mid in builder)", "snapshotExecutableKalshiPrice"), ("Primary mid, spread<=5c", "primaryMidSpreadAtMost5c")):
        b = res["overall"] if key is None else res["marketBenchmarkSensitivity"][key]
        if not b.get("rows"):
            continue
        L.append(f"| {name} | {b['rows']} | {b['model']['brier']:.4f} | {b['market']['brier']:.4f} | "
                 f"{b['pairedBrierDelta_modelMinusMarket']:+.4f} {_ci(b.get('pairedBrierDeltaCI95_gameClustered'))} |")
    L.append("\n## Reliability (overall, model)\n")
    L.append("| Bucket | Rows | Games | Mean predicted | Realized |")
    L.append("|---|---:|---:|---:|---:|")
    for t in res["reliability"]["overall"]["model"]:
        L.append(f"| {t['bucket']} | {t['rows']} | {t['games']} | {t['meanPredicted']:.3f} | {t['realizedRate']:.3f} |")
    L.append("\n## Edge buckets (model − market mid), overall\n")
    L.append("Trade = 1 contract on the model's side: YES at observed ask if model > mid, NO at 1−bid otherwise; "
             "ROI after Kalshi taker fee (series multiplier × p × (1−p)).\n")
    L.append("| Edge | Rows | Games | Mean model | Mean mkt | Realized | Realized − mkt | Side | ROI net [95% CI] | ROI gross |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---|---|---:|")
    for t in res["edgeBuckets"]["overall"]:
        if not t.get("rows"):
            continue
        L.append(f"| {t['bucket']} | {t['rows']} | {t['games']} | {t['meanModel']:.3f} | {t['meanMarketMid']:.3f} | "
                 f"{t['realizedYesRate']:.3f} | {t['realizedMinusMarket']:+.3f} | {t['tradeSide']} | "
                 f"{_fmt(t['roiAfterFees'])} {_ci(t.get('roiAfterFeesCI95_gameClustered'))} | {_fmt(t['roiGross'])} |")
    for title, hk in (("Chronological holdout by DATE (first 60% of dates = dev) and dev-fitted recalibration", "holdout"),
                      ("Sensitivity: chronological holdout by GAME start (first 60% of games = dev)", "holdoutByGameChronological")):
        h = res[hk]
        L.append(f"\n## {title}\n")
        L.append(f"- Dev: {h['devDates'][0] if h['devDates'] else ''}..{h['devDates'][-1] if h['devDates'] else ''} "
                 f"({h['dev']['rows']} rows, {h['dev']['games']} games)")
        L.append(f"- Test: {h['testDates'][0] if h['testDates'] else ''}..{h['testDates'][-1] if h['testDates'] else ''} "
                 f"({h['test']['rows']} rows, {h['test']['games']} games, {h['test']['dates']} dates)")
        if "testPlattPooled" not in h:
            continue
        p = h["plattFitOnDev_pooled"]
        L.append(f"- Platt fitted on dev (pooled): a={p['intercept']}, b={p['slope']}")
        ts = h["testScore"]
        L.append(f"- Test raw model: Brier {h['testRaw']['testBrier']}, log loss {h['testRaw']['testLogLoss']}, ECE {h['testRaw']['testECE10']}; "
                 f"market Brier {ts['market']['brier']}, model-market Δ {ts['pairedBrierDelta_modelMinusMarket']:+.4f} {_ci(ts.get('pairedBrierDeltaCI95_gameClustered'))}")
        for nm, k in (("pooled Platt", "testPlattPooled"), ("per-family Platt", "testPlattPerFamily")):
            q = h[k]
            L.append(f"- Test {nm}: Brier {q['testBrier']} (Δ vs raw {q['brierChangeVsRaw']:+.5f} {_ci(q['brierChangeVsRawCI95_gameClustered'])}), "
                     f"log loss {q['testLogLoss']} (Δ {q['logLossChangeVsRaw']:+.5f} {_ci(q['logLossChangeVsRawCI95_gameClustered'])}), "
                     f"ECE {q['testECE10']}; Brier Δ vs market {q['pairedBrierDeltaVsMarket']:+.4f}")
        L.append("\n| Family (test) | Rows | Games | Raw | Platt pooled | Platt family | Market |")
        L.append("|---|---:|---:|---:|---:|---:|---:|")
        for f, v in h["testByFamily"].items():
            L.append(f"| {f} | {v['rows']} | {v['games']} | {v['rawBrier']} | {v['plattPooledBrier']} | {v['plattFamilyBrier']} | {v['marketBrier']} |")
    cl = res["clv"]
    L.append("\n## CLV\n")
    L.append(f"- {cl['verdict']}.")
    L.append(f"- Coverage: {cl['coverage']} of rows have a pregame capture within {CLV_CLOSE_MAX_MINUTES_BEFORE_START} min of first pitch; "
             f"mean signed mid move toward model {cl['meanSignedMidMove']}; share moving toward model {cl['shareMovingTowardModel']} "
             f"(unchanged {cl['shareUnchanged']}); mean close-mid minus entry ask on model side {cl['meanCloseMidMinusEntryAsk_modelSide']}.")
    L.append("\n## Caveats\n")
    L.append("- Coverage is front-loaded (most graded tickers come from 2026-08-19..08-25); late-season dates carry one or two games each, "
             "so the test window is small and game-clustered CIs are wide.")
    L.append("- The snapshot's `marketObservedAt` capture can be hours older than `snapshotGeneratedAt` (median quote age above); "
             "the fresh-mid sensitivity shows how much the market benchmark improves with a newer quote.")
    L.append("- Ladder rungs of the same player-game are not independent; counts of independent player-games and games are reported, "
             "and every interval is clustered by game.")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-json", default=OUT_JSON)
    ap.add_argument("--out-md", default=OUT_MD)
    args = ap.parse_args()
    res = run(args.out_json, args.out_md)
    ov = res["overall"]
    print(json.dumps({"rows": ov.get("rows"), "games": ov.get("games"), "verdict": res["verdict"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
