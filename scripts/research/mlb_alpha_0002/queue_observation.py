#!/usr/bin/env python3
"""MLB-ALPHA-0002-QUEUE-OBSERVATION-V1 -- prospective LIVE queue evidence.

WHY THIS EXISTS
---------------
maker_simulation.py can only ever INFER a passive fill. Its single
biggest unknown is the QUEUE AHEAD of our hypothetical resting order:
Kalshi's historical candlesticks carry prices but no sizes, and the
exchange publishes no order-book history, so historically the queue is
genuinely unknowable and is swept over a declared grid
(maker_simulation.QUEUE_AHEAD_GRID), with every historical result
labelled COUNTERFACTUAL_QUEUE_UNKNOWN.

Prospective capture DOES record the live book. This module turns that
book into the real observed analogue of the swept parameter, one record
per C01-F5REV signal:

    queueAheadObserved = displayed quantity resting AT our hypothetical
                         passive price, in the latest book captured AT OR
                         BEFORE the modelled placement moment

The substitution is one-directional and that direction is recorded on
every row: an OBSERVED queue may replace the swept assumption; the swept
assumption may NEVER be substituted for a missing observation. When no
book was captured near placement, the record says so
(queueAheadBasis = UNOBSERVED_NO_BOOK_NEAR_PLACEMENT), the conservative
fill is reported as NOT_EVALUABLE, and no grid value is filled in.

WHAT IS RECORDED, PER SIGNAL
----------------------------
  * best bid / best ask at signal time, derived from the live book and
    cross-checked against the signal's own quote (bookQuoteAgrees)
  * displayed depth: the touch on BOTH sides, the level at the
    hypothetical passive price, and the top LEVELS_KEPT levels of each side
  * quantity resting AT the hypothetical passive price
  * queue ahead at the modelled placement moment (the key number)
  * subsequent book states for that ticker/level over the following
    observation windows (an append-only book trail)
  * opposite-side taker prints that would consume the queue, from the tape
  * queue depletion progress over time
  * modelled 25-contract fill / no-fill under maker_simulation's OWN fill
    definition -- this module never reimplements it, it calls
    ms.simulate_passive_fill, so a price touch can never become a fill:
    fills are inferred only from opposite-side taker volume actually
    consuming queueAhead + our modelled size
  * time to fill, when filled
  * signal expiration, under the frozen MAKER-A-JOIN-BEST expiry rules
    (max wait, T-5 to scheduled start, scheduled start, signal fully
    retraced)
  * adverse selection at +1, +5, +10, +30 minutes and at the pregame
    close (the last two-sided quote at or before scheduled start), signed
    by lib/edgelab/clv_convention.py -- imported, never reimplemented

INCREMENTAL DESIGN (chosen shape, stated once)
----------------------------------------------
PURE APPEND-ONLY EVENT LOG, STATE DERIVED BY REPLAY. There is no mutable
state file at all. Three gzipped JSONL partition families under
prospective/queue_observations/:

    opened/<date>.jsonl.gz        one immutable OPEN row per signal
    observations/<date>.jsonl.gz  append-only OBSERVATION rows, one per
                                  open record per capture run
    finalized/<date>.jsonl.gz     one FINAL row per record when it ends

The set of still-open records is recomputed on every run as
(OPEN ids over the lookback window) - (FINAL ids), so a lost or stale
cache can never desynchronise from the data. Nothing is ever rewritten.

IDEMPOTENCE. Every row carries a deterministic key derived from the
input data, never from the wall clock:
    OPEN         observationId
    OBSERVATION  (observationId, evidenceThroughAt)
    FINAL        observationId
where evidenceThroughAt is the latest capture timestamp present in the
loaded partitions -- the run's data cutoff. Re-running over unchanged
input therefore appends nothing. Re-running after a new capture appends
exactly one new OBSERVATION row per open record.

NO OUTCOME FIELDS. OPEN and OBSERVATION rows are written before any
settlement exists and carry none: no settlement result, no realised
return, no profit or loss. Fill economics are modelled COSTS only
(the frozen maker fee), and appear only once a fill has been inferred.

DATA SOURCE. Disk only. This module makes NO network calls of any kind:
everything it needs (books, books_unchanged, quotes, quotes_unchanged,
trades) is already written by prospective_capture.py, and re-fetching
would both duplicate its 429-managed budget and destroy the as-captured
provenance. Change-suppressed reference rows are resolved back to the
full book/quote they fingerprint, so the series is reconstructed exactly
as captured.

HONEST LIMITATIONS
------------------
  * Our 25 contracts were never in the book. Nothing here is an observed
    fill; the queue is observed, the fill is still inferred.
  * The book is sampled at the ~10 minute capture cadence, not streamed.
    queueAheadObserved is the depth in the latest book captured at or
    before placement -- never a later one, which would import depth that
    did not exist yet -- and every row carries that lag (bookLagSeconds,
    always <= 0) so a stale observation is visible rather than silent.
  * For the same reason the +1 and +5 minute adverse-selection horizons
    cannot resolve finer than the capture cadence. They are recorded
    with their true lag and a degenerate flag when the nearest quote is
    the placement quote itself -- never interpolated, never faked.
  * Displayed depth is all we can see. Queue position also depends on
    order age, and a shrinking level cannot be told apart from a
    withdrawn one by size alone.
  * Adverse selection is anchored at the modelled placement moment for
    every record so filled and unfilled records stay comparable, and
    additionally at the inferred fill moment when a fill occurred.
    Its entry leg is our hypothetical PASSIVE price while its horizon leg
    is the taker-executable price of clv_convention, so the level is
    conservative; the movement ACROSS horizons is the meaningful signal.
  * Book level prices are read as integer cents in [0, 100], the same
    shape shadow_writers.py already reads. That unit is not verifiable
    from this sandbox, so it is checked rather than assumed: every record
    reports bookQuoteAgrees, comparing the book-derived touch with the
    quote the signal itself was built from.

RESEARCH ONLY. Read-only local files, no network, no exchange write
surface of any kind, no import of and no write path to any ledger,
recommendation, staking, eligibility or risk-gate module.
"""

import argparse
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab import clv_convention as cc                                  # noqa: E402
from lib.edgelab.mlb_alpha_identity import parse_event_ticker                 # noqa: E402
from scripts.research.mlb_alpha_0002 import maker_simulation as ms            # noqa: E402

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
CAP = os.path.join(ART, "prospective")
SHADOW = os.path.join(CAP, "shadows")
OUT = os.path.join(CAP, "queue_observations")

PROGRAM = "MLB-ALPHA-0002"
CANDIDATE_ID = "MLB-ALPHA-0002-C01-F5REV"
OBSERVER_ID = "MLB-ALPHA-0002-QUEUE-OBSERVATION-V1"
SCHEMA_VERSION = 1
EPOCH = datetime(1970, 1, 1)

# The execution protocol is FROZEN elsewhere; read it rather than
# restating it, so size and wait can never drift apart from the
# historical simulator they must be compared against.
PROTOCOL = [p for p in ms.maker_protocols() if p["protocolId"] == "MAKER-A-JOIN-BEST"][0]
MODELLED_CONTRACTS = PROTOCOL["modelledContracts"]          # 25
MAX_WAIT_MINUTES = PROTOCOL["maxWaitMinutes"]               # 30
MINUTES_BEFORE_START_DEADLINE = 5                           # frozen expiry rule "T-5 minutes"

HORIZON_MINUTES = (1, 5, 10, 30)
HORIZON_CLOSE = "PREGAME_CLOSE"
QUOTE_TOLERANCE_MIN = 15          # 1.5 capture cadences
BOOK_TOLERANCE_MIN = 20           # 2 capture cadences
LEVELS_KEPT = 10
PRINTS_KEPT = 50
LOOKBACK_DAYS = 3

STATE_OPEN = "OPEN"
STATE_COMPLETE = "COMPLETE"
ORDER_WORKING = "WORKING"
ORDER_FILLED = "FILLED"
ORDER_EXPIRED = "EXPIRED"
ORDER_NOT_EVALUABLE = "NOT_EVALUABLE_QUEUE_UNOBSERVED"

QUEUE_BASIS_OBSERVED = "OBSERVED_BOOK_AT_PLACEMENT"
QUEUE_BASIS_UNOBSERVED = "UNOBSERVED_NO_BOOK_NEAR_PLACEMENT"

STATS = Counter()


# ------------------------------------------------------------------- io
def iter_gz(path):
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    STATS["malformedLines"] += 1


def iter_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    STATS["malformedLines"] += 1


def append_gz(kind, date, rows):
    if not rows:
        return 0
    path = os.path.join(OUT, kind, date + ".jsonl.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at") as fh:                     # append-only, never "w"
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(rows)


def parse_ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=None)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def minute_of(dt):
    return int((dt - EPOCH).total_seconds() // 60)


def dates_window(date, days):
    d0 = datetime.strptime(date, "%Y-%m-%d")
    return [(d0 - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(days - 1, -1, -1)]


# ------------------------------------------------------------ book parse
# Kalshi's fixed-point migration renamed BOTH the payload key and the side
# keys. Measured from the first run that captured real depth
# (ALPHA0002_20260903T000920Z): every book arrives as
#   {"yes_dollars": [["0.0300", "35.00"], ...], "no_dollars": [...]}
# -- fixed-point dollar price strings and FRACTIONAL quantity strings.
# The legacy plain "yes"/"no" keys are still read so historical rows and any
# future rollback keep parsing.
SIDE_KEYS = {
    "yes": ("yes_dollars", "yes"),
    "no": ("no_dollars", "no"),
}


def side_raw_levels(orderbook, side):
    """Raw (unparsed) levels for one side, under whichever side key the
    payload actually uses. Returns [] when the side is absent."""
    ob = orderbook or {}
    for key in SIDE_KEYS.get(side, (side,)):
        levels = ob.get(key)
        if levels:
            return levels
    return []


PRICE_UNIT_CENTS = "CENTS"
PRICE_UNIT_DOLLARS = "FIXED_POINT_DOLLARS"
PRICE_UNIT_AMBIGUOUS = "AMBIGUOUS"


def detect_price_unit(orderbook):
    """Decide, ONCE PER BOOK, whether prices are integer cents or
    fixed-point dollars -- and refuse to guess when it cannot be told.

    Kalshi's fixed-point migration changed the orderbook payload: the
    endpoint now answers under `orderbook_fp` rather than `orderbook`, and
    the two use different price units. That matters more than it looks: a
    dollar-denominated "0.5700" fed through a cents parser rounds to 1c --
    inside the valid 0..100 range, so it would be silently WRONG rather
    than rejected, and every queue-ahead figure built on it would be
    garbage while looking plausible.

    Rule, applied to the whole book so one odd level cannot flip it:
      every price an integer in [0, 100]        -> CENTS
      every price a non-integer inside (0, 1)   -> FIXED_POINT_DOLLARS
      anything else, or a mix                   -> AMBIGUOUS (parse nothing)

    AMBIGUOUS is deliberately unusable: the caller records the book as
    having no readable depth rather than inventing a scale.
    """
    prices = []
    for side in ("yes", "no"):
        for lvl in side_raw_levels(orderbook, side):
            if not isinstance(lvl, (list, tuple)) or len(lvl) < 2:
                continue
            try:
                prices.append(float(lvl[0]))
            except (TypeError, ValueError):
                continue
    if not prices:
        return PRICE_UNIT_AMBIGUOUS
    if all(float(p).is_integer() and 0 <= p <= 100 for p in prices):
        return PRICE_UNIT_CENTS
    if all(0.0 < p < 1.0 for p in prices):
        return PRICE_UNIT_DOLLARS
    return PRICE_UNIT_AMBIGUOUS


def level_pair(level, unit=PRICE_UNIT_CENTS):
    """One displayed level as (priceCents, quantity), scaled by `unit`.

    Kalshi publishes each side of the book as [price, quantity] pairs and
    that is the only shape read here -- the same shape shadow_writers.py
    already relies on. Anything else, and any level under an AMBIGUOUS
    unit, is counted as unparsable rather than guessed at.
    """
    if unit == PRICE_UNIT_AMBIGUOUS:
        return None
    if not isinstance(level, (list, tuple)) or len(level) < 2:
        return None
    try:
        raw_price = float(level[0])
        qty = float(level[1])
    except (TypeError, ValueError):
        return None
    price = int(round(raw_price * 100)) if unit == PRICE_UNIT_DOLLARS else int(round(raw_price))
    if price < 0 or price > 100 or qty < 0:
        return None
    return (price, qty)


def side_levels(orderbook, side):
    """Displayed levels for one side, BEST (highest resting bid) first.

    Position in the payload is never trusted for ordering; the levels are
    sorted by price here so the touch is correct either way."""
    raw = side_raw_levels(orderbook, side)
    unit = detect_price_unit(orderbook)
    STATS["bookPriceUnit:" + unit] += 1
    out = []
    for lvl in raw:
        pair = level_pair(lvl, unit)
        if pair is None:
            STATS["bookLevelsUnparsable"] += 1
            continue
        out.append(pair)
    out.sort(key=lambda pq: -pq[0])
    return out


def qty_at_price(levels, price_cents):
    """Displayed quantity resting AT one exact price, or None when the
    price is not a level of that side (which is not the same as zero)."""
    if price_cents is None:
        return None
    found = [q for p, q in levels if p == price_cents]
    return sum(found) if found else None


def book_state(orderbook):
    """Both touches, the derived ask, and the kept depth of each side.

    Kalshi prices each side's resting bids in that side's own cents, so
    the YES ask is the complement of the best NO bid and vice versa --
    the same complement clv_convention uses for a NO executable price."""
    yes = side_levels(orderbook, "yes")
    no = side_levels(orderbook, "no")
    best_yes_bid = yes[0][0] if yes else None
    best_no_bid = no[0][0] if no else None
    return {
        "bestYesBidCents": best_yes_bid,
        "bestYesBidQty": yes[0][1] if yes else None,
        "bestNoBidCents": best_no_bid,
        "bestNoBidQty": no[0][1] if no else None,
        "derivedYesAskCents": (100 - best_no_bid) if best_no_bid is not None else None,
        "derivedNoAskCents": (100 - best_yes_bid) if best_yes_bid is not None else None,
        "yesLevels": [list(pq) for pq in yes[:LEVELS_KEPT]],
        "noLevels": [list(pq) for pq in no[:LEVELS_KEPT]],
        "yesLevelCount": len(yes),
        "noLevelCount": len(no),
    }


def passive_side_key(signal_side):
    """Which side of the book our hypothetical resting BUY sits on.

    Buying YES passively rests a YES bid; buying NO passively rests a NO
    bid, priced in NO cents -- which is exactly how shadow_writers.py
    computes passiveLimitCents (100 - yesAsk)."""
    return "yes" if signal_side == cc.SIDE_YES else "no"


def resting_at_passive(orderbook, signal_side, passive_limit_cents):
    return qty_at_price(side_levels(orderbook, passive_side_key(signal_side)), passive_limit_cents)


# ----------------------------------------------------------- trade parse
def trade_quantity(tr):
    """count_fp is the field the committed raw-data manifest documents;
    build_candle_panel.py already tolerates a plain `count` beside it."""
    for key in ("count_fp", "count"):
        if tr.get(key) is None:
            continue
        try:
            return float(tr[key]), key
        except (TypeError, ValueError):
            return None, key
    return None, None


def trade_price_cents(tr):
    """yes_price_dollars is the documented field (dollar string). A plain
    integer-cent `yes_price` is accepted as a fallback and COUNTED
    separately, so a schema change shows up in the run summary instead of
    being silently absorbed."""
    if tr.get("yes_price_dollars") is not None:
        return ms.cents(tr.get("yes_price_dollars")), "yes_price_dollars"
    if tr.get("yes_price") is not None:
        try:
            return int(round(float(tr["yes_price"]))), "yes_price"
        except (TypeError, ValueError):
            return None, "yes_price"
    return None, None


def normalize_trade(tr):
    """-> the exact shape maker_simulation.simulate_passive_fill consumes,
    plus provenance. Returns None when the print cannot be read."""
    created = tr.get("created_time")
    if not created:
        STATS["tradesWithoutCreatedTime"] += 1
        return None
    try:
        dt = parse_ts(created)
    except ValueError:
        STATS["tradesWithoutCreatedTime"] += 1
        return None
    qty, qty_field = trade_quantity(tr)
    price, price_field = trade_price_cents(tr)
    if qty is None:
        STATS["tradesWithoutQuantity"] += 1
        return None
    if price is None:
        STATS["tradesWithUnreadablePrice"] += 1
    STATS["tradePriceField:%s" % price_field] += 1
    STATS["tradeQuantityField:%s" % qty_field] += 1
    return {"created_minute": minute_of(dt), "created_time": created,
            "taker_side": tr.get("taker_side"), "yes_price_cents": price,
            "quantity": qty, "trade_id": tr.get("trade_id"),
            "is_block_trade": tr.get("is_block_trade")}


# --------------------------------------------------------------- loading
class Evidence(object):
    """Everything prospective_capture.py has written for a date window,
    reassembled: change-suppressed reference rows resolved back to the
    full book/quote they fingerprint, trades deduplicated by trade_id."""

    def __init__(self):
        self.books = defaultdict(list)          # ticker -> [(dt, book)]
        self.quotes = defaultdict(list)         # ticker -> [(dt, quote)]
        self.trades = defaultdict(list)         # ticker -> [normalized print]
        self.through = None                     # latest capture timestamp seen

    def _mark(self, captured_at):
        if captured_at and (self.through is None or captured_at > self.through):
            self.through = captured_at

    def through_dt(self):
        return parse_ts(self.through) if self.through else None

    def sort(self):
        for series in (self.books, self.quotes):
            for key in series:
                series[key].sort(key=lambda x: x[0])
        for key in self.trades:
            self.trades[key].sort(key=lambda t: t["created_minute"])


def load_evidence(dates):
    ev = Evidence()
    book_by_fp, quote_by_fp = {}, {}
    seen_trades = set()
    for date in dates:
        for r in iter_gz(os.path.join(CAP, "books", date + ".jsonl.gz")):
            ticker, at = r.get("marketTicker"), r.get("capturedAt")
            book = r.get("orderbook")
            if not ticker or not at or book is None:
                continue
            ev._mark(at)
            book_by_fp[(ticker, r.get("fp"))] = book
            ev.books[ticker].append((parse_ts(at), book))
            STATS["booksLoaded"] += 1
        for r in iter_gz(os.path.join(CAP, "books_unchanged", date + ".jsonl.gz")):
            ticker, at = r.get("marketTicker"), r.get("capturedAt")
            if not ticker or not at:
                continue
            ev._mark(at)
            book = book_by_fp.get((ticker, r.get("fp")))
            if book is None:
                STATS["bookRefsUnresolved"] += 1
                continue
            ev.books[ticker].append((parse_ts(at), book))
            STATS["bookRefsResolved"] += 1
        for r in iter_gz(os.path.join(CAP, "quotes", date + ".jsonl.gz")):
            ticker, at = r.get("marketTicker"), r.get("capturedAt")
            if not ticker or not at:
                continue
            ev._mark(at)
            quote_by_fp[(ticker, r.get("fp"))] = r
            ev.quotes[ticker].append((parse_ts(at), r))
            STATS["quotesLoaded"] += 1
        for r in iter_gz(os.path.join(CAP, "quotes_unchanged", date + ".jsonl.gz")):
            ticker, at = r.get("marketTicker"), r.get("capturedAt")
            if not ticker or not at:
                continue
            ev._mark(at)
            quote = quote_by_fp.get((ticker, r.get("fp")))
            if quote is None:
                STATS["quoteRefsUnresolved"] += 1
                continue
            restated = dict(quote)
            restated["capturedAt"] = at
            ev.quotes[ticker].append((parse_ts(at), restated))
            STATS["quoteRefsResolved"] += 1
        for r in iter_gz(os.path.join(CAP, "trades", date + ".jsonl.gz")):
            ev._mark(r.get("capturedAt"))
            key = r.get("trade_id")
            if key is not None:
                if key in seen_trades:
                    STATS["tradesDeduplicated"] += 1
                    continue
                seen_trades.add(key)
            norm = normalize_trade(r)
            if norm is None:
                continue
            ev.trades[r.get("ticker")].append(norm)
            STATS["tradesLoaded"] += 1
    ev.sort()
    return ev


def load_signals(dates):
    """C01-F5REV entry rows, oldest first. Written by shadow_writers.py."""
    out = []
    for date in dates:
        for r in iter_jsonl(os.path.join(SHADOW, CANDIDATE_ID, date + ".jsonl")):
            if (r.get("marketTicker") and r.get("capturedAt")
                    and r.get("passiveLimitCents") is not None
                    and r.get("signalSide") in (cc.SIDE_YES, cc.SIDE_NO)):
                out.append((date, r))
            else:
                STATS["signalsSkippedIncomplete"] += 1
    out.sort(key=lambda pair: pair[1]["capturedAt"])
    return out


def observation_id(marketTicker, episodeKey):
    return "%s|%s" % (marketTicker, episodeKey)


def existing_keys(kind, dates, key_fn):
    keys = set()
    for date in dates:
        for r in iter_gz(os.path.join(OUT, kind, date + ".jsonl.gz")):
            keys.add(key_fn(r))
    return keys


def open_records(dates):
    """The still-open set, REPLAYED from the append-only log: every OPEN
    row over the window minus every FINAL row. No cache, so no drift."""
    finalized = existing_keys("finalized", dates, lambda r: r.get("observationId"))
    out = []
    for date in dates:
        for r in iter_gz(os.path.join(OUT, "opened", date + ".jsonl.gz")):
            if r.get("observationId") not in finalized:
                out.append((date, r))
    return out


# ------------------------------------------------------------ quote maths
def quote_two_sided(q):
    bid, ask = q.get("yesBid"), q.get("yesAsk")
    if bid is None or ask is None:
        return False
    return 1 <= ask <= 99 and 1 <= (100 - bid) <= 99 and ask >= bid


def mid_cents(q):
    if not quote_two_sided(q):
        return None
    return (q["yesBid"] + q["yesAsk"]) / 2.0


def nearest_quote(series, target_dt, tolerance_min=QUOTE_TOLERANCE_MIN):
    """The two-sided quote closest to `target_dt` within tolerance, with
    its SIGNED lag in seconds (negative = captured before the target).
    Never interpolated."""
    best, best_gap = None, None
    for dt, q in series:
        if not quote_two_sided(q):
            continue
        gap = abs((dt - target_dt).total_seconds())
        if gap > tolerance_min * 60:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = (dt, q), gap
    if best is None:
        return None, None
    return best, (best[0] - target_dt).total_seconds()


def last_quote_at_or_before(series, cutoff_dt):
    found = None
    for dt, q in series:
        if dt > cutoff_dt:
            break
        if quote_two_sided(q):
            found = (dt, q)
    return found


def book_at_placement(series, target_dt, tolerance_min=BOOK_TOLERANCE_MIN):
    """The latest book captured AT OR BEFORE the modelled placement moment,
    with its lag in seconds (<= 0).

    Never a later book: the queue we would have joined is the one that was
    displayed when we would have placed, and reading forward would import
    depth that did not exist yet. prospective_capture.py stamps a run's
    quotes and books with the SAME capture timestamp, so the book from the
    run that produced the signal is found at lag 0."""
    best = None
    for dt, book in series:
        if dt > target_dt:
            break
        if (target_dt - dt).total_seconds() > tolerance_min * 60:
            continue
        best = (dt, book)
    if best is None:
        return None, None
    return best, (best[0] - target_dt).total_seconds()


def adverse_selection_cents(entry_price_cents, quote, side):
    """THE canonical sign, from lib/edgelab/clv_convention.py -- positive
    means the market moved TOWARD our purchased side (good), negative is
    adverse selection. The entry leg is our hypothetical passive price;
    the horizon leg is the side-relevant executable price."""
    horizon = cc.executable_price_cents(quote, side)
    if horizon is None:
        return None, None
    return cc.clv_for_side(entry_price_cents, horizon, side, unit=cc.UNIT_CENTS), horizon


def measure_horizons(anchor_dt, entry_price_cents, side, quote_series, start_dt, through_dt):
    """Adverse selection at each horizon plus the pregame close.

    A horizon is either measured, not yet elapsed, or declared
    unmeasurable with a reason -- never silently absent."""
    out = {}
    for minutes in HORIZON_MINUTES:
        target = anchor_dt + timedelta(minutes=minutes)
        key = "T+%dm" % minutes
        if through_dt is None or through_dt < target:
            out[key] = {"measured": False, "settled": False, "reason": "HORIZON_NOT_YET_ELAPSED"}
            continue
        hit, lag = nearest_quote(quote_series, target)
        if hit is None:
            out[key] = {"measured": False, "settled": True, "reason": "NO_TWO_SIDED_QUOTE_WITHIN_TOLERANCE"}
            continue
        value, horizon_price = adverse_selection_cents(entry_price_cents, hit[1], side)
        out[key] = {"measured": value is not None, "settled": True,
                    "clvCents": value, "adverse": (value is not None and value < 0),
                    "horizonExecutablePriceCents": horizon_price,
                    "quoteCapturedAt": iso(hit[0]), "quoteLagSeconds": round(lag, 1),
                    "degenerateSameAsAnchorQuote": abs((hit[0] - anchor_dt).total_seconds()) < 1.0,
                    "yesBid": hit[1].get("yesBid"), "yesAsk": hit[1].get("yesAsk")}
    if start_dt is None:
        out[HORIZON_CLOSE] = {"measured": False, "settled": True,
                              "reason": "SCHEDULED_START_UNRESOLVED"}
    elif through_dt is None or through_dt < start_dt:
        out[HORIZON_CLOSE] = {"measured": False, "settled": False,
                              "reason": "PREGAME_WINDOW_STILL_OPEN"}
    else:
        hit = last_quote_at_or_before(quote_series, start_dt)
        if hit is None:
            out[HORIZON_CLOSE] = {"measured": False, "settled": True,
                                  "reason": "NO_TWO_SIDED_PREGAME_QUOTE"}
        else:
            value, horizon_price = adverse_selection_cents(entry_price_cents, hit[1], side)
            out[HORIZON_CLOSE] = {"measured": value is not None, "settled": True,
                                  "clvCents": value, "adverse": (value is not None and value < 0),
                                  "horizonExecutablePriceCents": horizon_price,
                                  "quoteCapturedAt": iso(hit[0]),
                                  "minutesBeforeScheduledStart":
                                      round((start_dt - hit[0]).total_seconds() / 60.0, 1),
                                  "yesBid": hit[1].get("yesBid"), "yesAsk": hit[1].get("yesAsk")}
    return out


def horizons_settled(horizons):
    return all(h.get("settled") for h in horizons.values())


# --------------------------------------------------------- expiry rules
def invalidation_minute(quote_series, placed_dt, deadline_minute, side, reference_mid):
    """The frozen MAKER-A rule "signal invalidation (the triggering move
    fully retraces)": the first captured two-sided quote after placement
    whose mid has returned to the pre-move reference."""
    if reference_mid is None:
        return None
    for dt, q in quote_series:
        if dt <= placed_dt:
            continue
        minute = minute_of(dt)
        if minute > deadline_minute:
            return None
        mid = mid_cents(q)
        if mid is None:
            continue
        if side == cc.SIDE_YES and mid >= reference_mid:
            return minute
        if side == cc.SIDE_NO and mid <= reference_mid:
            return minute
    return None


# ---------------------------------------------------------------- rows
def build_open_row(signal, ev, opened_at):
    """The immutable OPEN record: the observed book at the modelled
    placement moment, and nothing that could depend on an outcome."""
    ticker = signal["marketTicker"]
    side = signal.get("signalSide")
    placed_dt = parse_ts(signal["capturedAt"])
    passive = signal.get("passiveLimitCents")
    identity = parse_event_ticker(signal.get("eventTicker"))
    start_dt = identity.get("scheduledStartUtc") if identity.get("status") == "RESOLVED" else None

    hit, lag = book_at_placement(ev.books.get(ticker, []), placed_dt)
    book = hit[1] if hit else None
    state = book_state(book) if book is not None else None
    resting = resting_at_passive(book, side, passive) if book is not None else None
    queue_ahead = resting if book is not None else None
    if book is not None and resting is None:
        queue_ahead = 0.0                     # our price is a level nobody is resting on
    basis = QUEUE_BASIS_OBSERVED if book is not None else QUEUE_BASIS_UNOBSERVED

    deadline_minute = minute_of(placed_dt) + MAX_WAIT_MINUTES
    deadline_basis = "MAX_WAIT_MINUTES"
    if start_dt is not None:
        start_cutoff = minute_of(start_dt) - MINUTES_BEFORE_START_DEADLINE
        if start_cutoff < deadline_minute:
            deadline_minute, deadline_basis = start_cutoff, "T_MINUS_5_TO_SCHEDULED_START"

    signal_mid = None
    if signal.get("yesBid") is not None and signal.get("yesAsk") is not None:
        signal_mid = (signal["yesBid"] + signal["yesAsk"]) / 2.0
    reference_mid = None
    if signal_mid is not None and signal.get("dMid60Cents") is not None:
        reference_mid = signal_mid - signal["dMid60Cents"]

    row = {
        "programId": PROGRAM, "candidateId": CANDIDATE_ID, "observerId": OBSERVER_ID,
        "schemaVersion": SCHEMA_VERSION, "recordType": "OPEN",
        "observationId": observation_id(ticker, signal.get("episodeKey")),
        "marketTicker": ticker, "eventTicker": signal.get("eventTicker"),
        "episodeKey": signal.get("episodeKey"), "gameDate": identity.get("gameDate"),
        "signalCapturedAt": signal["capturedAt"], "openedAt": opened_at,
        "signalRuleSha256": signal.get("ruleSha256"),
        "scheduledStartUtc": iso(start_dt) if start_dt else None,
        "scheduledStartResolved": start_dt is not None,
        "scheduledStartUnresolvedReason": identity.get("unresolvedReason"),
        # signal state, copied forward so the record stands alone
        "signalSide": side, "dMid60Cents": signal.get("dMid60Cents"),
        "signalYesBid": signal.get("yesBid"), "signalYesAsk": signal.get("yesAsk"),
        "signalMidCents": signal_mid, "referenceMidCents": reference_mid,
        "signalSpreadCents": signal.get("spreadCents"),
        "signalVolume": signal.get("volume"), "signalOpenInterest": signal.get("openInterest"),
        "executablePriceCents": signal.get("executablePriceCents"),
        # the modelled passive placement
        "hypotheticalPassiveLimitCents": passive,
        "passiveBookSide": passive_side_key(side),
        "modelledContracts": MODELLED_CONTRACTS,
        "protocolId": PROTOCOL["protocolId"], "protocolSha256": PROTOCOL["protocolSha256"],
        "maxWaitMinutes": MAX_WAIT_MINUTES,
        "placedMinute": minute_of(placed_dt),
        "deadlineMinute": deadline_minute, "deadlineAt": iso(EPOCH + timedelta(minutes=deadline_minute)),
        "deadlineBasis": deadline_basis,
        # observed book evidence
        "bookAvailable": book is not None,
        "bookCapturedAt": iso(hit[0]) if hit else None,
        "bookLagSeconds": round(lag, 1) if lag is not None else None,
        "bookState": state,
        "restingQtyAtPassivePrice": resting,
        "queueAheadObserved": queue_ahead,
        "queueAheadBasis": basis,
        "queueAheadIsDisplayedDepthOnly": True,
        # the one-directional substitution rule, recorded on every row
        "sweptQueueParameterSubstituted": False,
        "observedQueueMayReplaceSweptParameter": True,
        "sweptParameterMayNeverReplaceObservation": True,
        "historicalSweepGrid": list(ms.QUEUE_AHEAD_GRID),
        "observedQueueWithinSweepGridRange": (
            queue_ahead is not None and queue_ahead <= max(ms.QUEUE_AHEAD_GRID)),
        "bookQuoteAgrees": None if state is None else (
            signal.get("yesBid") is not None
            and state["bestYesBidCents"] == signal.get("yesBid")
            and state["derivedYesAskCents"] == signal.get("yesAsk")),
        "outcomeFieldsPresent": False,
        "readOnly": True, "networkCallsMade": 0,
    }
    row.update(cc.convention_marker(cc.UNIT_CENTS))
    return row


def evaluate(rec, ev, through_dt):
    """All time-varying evidence for one open record, recomputed from the
    append-only capture. Pure with respect to the wall clock."""
    ticker = rec["marketTicker"]
    side = rec["signalSide"]
    passive = rec["hypotheticalPassiveLimitCents"]
    placed_dt = parse_ts(rec["signalCapturedAt"])
    placed_minute = rec["placedMinute"]
    deadline_minute = rec["deadlineMinute"]
    queue_ahead = rec.get("queueAheadObserved")
    quotes = ev.quotes.get(ticker, [])
    books = ev.books.get(ticker, [])
    trades = ev.trades.get(ticker, [])
    start_dt = parse_ts(rec["scheduledStartUtc"]) if rec.get("scheduledStartUtc") else None

    # the frozen expiry rules can end the record before its deadline
    invalidated = invalidation_minute(quotes, placed_dt, deadline_minute, side,
                                      rec.get("referenceMidCents"))
    effective_deadline = min(deadline_minute, invalidated) if invalidated is not None else deadline_minute

    through_minute = minute_of(through_dt) if through_dt else placed_minute
    evaluate_to = min(effective_deadline, through_minute)

    # THE fill definition is maker_simulation's, called not copied: a
    # price touch is never a fill, only opposite-side taker volume that
    # consumes queueAhead + our modelled size is.
    conservative = None
    if queue_ahead is not None:
        conservative = ms.simulate_passive_fill(trades, placed_minute, evaluate_to, side,
                                                passive, queue_ahead, MODELLED_CONTRACTS)
    optimistic = ms.simulate_passive_fill(trades, placed_minute, evaluate_to, side,
                                          passive, 0, MODELLED_CONTRACTS)
    flow = (conservative or optimistic)["aggressiveFlowAtOrThroughLevel"]

    if conservative is None:
        order_state = ORDER_NOT_EVALUABLE if through_minute >= effective_deadline else ORDER_WORKING
        filled_dt = None
    elif conservative["filled"]:
        order_state = ORDER_FILLED
        filled_dt = EPOCH + timedelta(minutes=conservative["filledAtMinute"])
    elif through_minute >= effective_deadline:
        order_state = ORDER_EXPIRED
        filled_dt = None
    else:
        order_state = ORDER_WORKING
        filled_dt = None

    expiry_reason = None
    if order_state == ORDER_FILLED:
        expiry_reason = "FILLED"
    elif order_state in (ORDER_EXPIRED, ORDER_NOT_EVALUABLE):
        expiry_reason = "SIGNAL_INVALIDATED" if invalidated is not None else rec["deadlineBasis"]

    # queue depletion over time
    remaining = None if queue_ahead is None else max(0.0, queue_ahead - flow)
    depletion = None
    if queue_ahead:
        depletion = round(min(1.0, flow / queue_ahead), 4)
    required = (queue_ahead + MODELLED_CONTRACTS) if queue_ahead is not None else None
    progress = round(min(1.0, flow / required), 4) if required else None

    # per-window flow, each measured with the same frozen definition
    windows = {}
    for minutes in HORIZON_MINUTES:
        end = min(placed_minute + minutes, evaluate_to)
        if end < placed_minute:
            windows["T+%dm" % minutes] = None
            continue
        step = ms.simulate_passive_fill(trades, placed_minute, end, side, passive,
                                        queue_ahead if queue_ahead is not None else 0,
                                        MODELLED_CONTRACTS)
        windows["T+%dm" % minutes] = {
            "elapsed": through_minute >= placed_minute + minutes,
            "oppositeSideTakerFlow": step["aggressiveFlowAtOrThroughLevel"],
            "filledByThen": step["filled"] if queue_ahead is not None else None,
            "queueEvaluable": queue_ahead is not None}

    # the prints that could consume the queue, and the book trail
    need = ms.opposite_taker_side(side)
    prints = [t for t in trades
              if placed_minute <= t["created_minute"] <= evaluate_to and t["taker_side"] == need]
    trail = []
    for dt, book in books:
        if dt < placed_dt or minute_of(dt) > evaluate_to:
            continue
        state = book_state(book)
        trail.append({"capturedAt": iso(dt),
                      "minutesSincePlacement": round((dt - placed_dt).total_seconds() / 60.0, 2),
                      "bestYesBidCents": state["bestYesBidCents"],
                      "bestYesBidQty": state["bestYesBidQty"],
                      "bestNoBidCents": state["bestNoBidCents"],
                      "bestNoBidQty": state["bestNoBidQty"],
                      "derivedYesAskCents": state["derivedYesAskCents"],
                      "restingQtyAtPassivePrice": resting_at_passive(book, side, passive)})

    horizons = measure_horizons(placed_dt, passive, side, quotes, start_dt, through_dt)
    horizons_from_fill = None
    if filled_dt is not None:
        horizons_from_fill = measure_horizons(filled_dt, passive, side, quotes, start_dt, through_dt)

    fill_cost = None
    if order_state == ORDER_FILLED:
        price = passive / 100.0
        fill_cost = {"contracts": MODELLED_CONTRACTS, "limitPriceCents": passive,
                     "makerFeeConservative": ms.fee_for(MODELLED_CONTRACTS, price,
                                                        ms.MAKER_MULTIPLIER_CONSERVATIVE),
                     "makerFeeOptimistic": ms.fee_for(MODELLED_CONTRACTS, price,
                                                      ms.MAKER_MULTIPLIER_OPTIMISTIC),
                     "feeSource": ms.FEE_SOURCE,
                     "costOnly": True, "outcomeFieldsPresent": False}

    return {
        "orderState": order_state,
        "fillEvaluable": queue_ahead is not None,
        "queueAheadObserved": queue_ahead,
        "queueAheadBasis": rec.get("queueAheadBasis"),
        "invalidatedAtMinute": invalidated,
        "effectiveDeadlineMinute": effective_deadline,
        "expiryReason": expiry_reason,
        "evaluatedThroughMinute": evaluate_to,
        "conservativeFillObservedQueue": conservative,
        "optimisticBoundZeroQueue": optimistic,
        "oppositeSideTakerFlow": flow,
        "oppositeSideTakerPrints": prints[:PRINTS_KEPT],
        "oppositeSideTakerPrintCount": len(prints),
        "oppositeSideTakerPrintsTruncated": len(prints) > PRINTS_KEPT,
        "queueRemaining": remaining,
        "queueDepletionFraction": depletion,
        "flowRequiredForFill": required,
        "flowProgressFraction": progress,
        "windowFlow": windows,
        "bookTrail": trail,
        "bookStatesObserved": len(trail),
        "restingQtyAtPassivePriceLatest": trail[-1]["restingQtyAtPassivePrice"] if trail else None,
        "filledAt": iso(filled_dt) if filled_dt else None,
        "minutesToFill": (conservative["filledAtMinute"] - placed_minute)
                         if (conservative and conservative["filled"]) else None,
        "adverseSelectionFromPlacement": horizons,
        "adverseSelectionFromFill": horizons_from_fill,
        "hypotheticalFillCost": fill_cost,
        "horizonsSettled": horizons_settled(horizons),
    }


def observation_row(rec, result, evidence_through, observed_at):
    row = {"programId": PROGRAM, "candidateId": CANDIDATE_ID, "observerId": OBSERVER_ID,
           "schemaVersion": SCHEMA_VERSION, "recordType": "OBSERVATION",
           "observationId": rec["observationId"], "marketTicker": rec["marketTicker"],
           "episodeKey": rec["episodeKey"], "signalSide": rec["signalSide"],
           "signalCapturedAt": rec["signalCapturedAt"],
           "hypotheticalPassiveLimitCents": rec["hypotheticalPassiveLimitCents"],
           "modelledContracts": MODELLED_CONTRACTS,
           "protocolId": PROTOCOL["protocolId"], "protocolSha256": PROTOCOL["protocolSha256"],
           "evidenceThroughAt": evidence_through, "observedAt": observed_at,
           "sweptQueueParameterSubstituted": False,
           "outcomeFieldsPresent": False, "readOnly": True, "networkCallsMade": 0}
    row.update(result)
    row.update(cc.convention_marker(cc.UNIT_CENTS))
    return row


def final_row(rec, result, evidence_through, finalized_at):
    row = observation_row(rec, result, evidence_through, finalized_at)
    row["recordType"] = "FINAL"
    row["finalizedAt"] = finalized_at
    row["observationState"] = STATE_COMPLETE
    row["queueAheadObservedReplacesSweep"] = rec.get("queueAheadBasis") == QUEUE_BASIS_OBSERVED
    return row


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%d"))
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    dates = dates_window(args.date, max(1, args.lookback_days))
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = load_evidence(dates)
    through = ev.through
    through_dt = ev.through_dt()
    counts = Counter()

    # (a) open a record for every new signal
    already_open = existing_keys("opened", dates, lambda r: r.get("observationId"))
    new_open = defaultdict(list)
    for date, signal in load_signals(dates):
        oid = observation_id(signal["marketTicker"], signal.get("episodeKey"))
        if oid in already_open:
            counts["signalsAlreadyOpen"] += 1
            continue
        already_open.add(oid)
        row = build_open_row(signal, ev, now)
        new_open[date].append(row)
        counts["opened"] += 1
        counts["openedWithObservedQueue" if row["queueAheadBasis"] == QUEUE_BASIS_OBSERVED
               else "openedWithoutBook"] += 1

    opened_written = 0
    if not args.dry_run:
        for date, rows in new_open.items():
            opened_written += append_gz("opened", date, rows)

    # (b) advance every still-open record against the newly captured data
    seen_updates = existing_keys("observations", dates,
                                 lambda r: (r.get("observationId"), r.get("evidenceThroughAt")))
    updates, finals = defaultdict(list), defaultdict(list)
    for date, rec in open_records(dates):
        result = evaluate(rec, ev, through_dt)
        counts["orderState:" + result["orderState"]] += 1
        terminal = result["orderState"] in (ORDER_FILLED, ORDER_EXPIRED, ORDER_NOT_EVALUABLE)
        complete = terminal and result["horizonsSettled"]
        if (rec["observationId"], through) in seen_updates:
            counts["updatesSuppressedNoNewEvidence"] += 1
        else:
            seen_updates.add((rec["observationId"], through))
            updates[date].append(observation_row(rec, result, through, now))
            counts["observationsAppended"] += 1
        if complete:
            finals[date].append(final_row(rec, result, through, now))
            counts["finalized"] += 1
            if result["orderState"] == ORDER_FILLED:
                counts["finalizedFilled"] += 1
        else:
            counts["stillOpen"] += 1

    updates_written = finals_written = 0
    if not args.dry_run:
        for date, rows in updates.items():
            updates_written += append_gz("observations", date, rows)
        for date, rows in finals.items():
            finals_written += append_gz("finalized", date, rows)

    summary = {
        "programId": PROGRAM, "candidateId": CANDIDATE_ID, "observerId": OBSERVER_ID,
        "schemaVersion": SCHEMA_VERSION, "ranAt": now, "gameDate": args.date,
        "datesScanned": dates, "evidenceThroughAt": through,
        "counts": dict(counts),
        "written": {"opened": opened_written, "observations": updates_written,
                    "finalized": finals_written},
        "inputs": {k: v for k, v in sorted(STATS.items())},
        "method": {"fillDefinition": "maker_simulation.simulate_passive_fill (frozen; a price "
                                     "touch is never a fill)",
                   "queueSource": "observed displayed depth at the hypothetical passive price",
                   "sweptQueueParameterSubstituted": False,
                   "stateModel": "append-only log; open set replayed as OPEN minus FINAL",
                   "network": "none -- reads prospective capture partitions only"},
        "readOnly": True, "networkCallsMade": 0, "outcomeFieldsPresent": False,
    }
    summary.update(cc.convention_marker(cc.UNIT_CENTS))
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
