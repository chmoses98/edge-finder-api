#!/usr/bin/env python3
"""MLB-ALPHA-0002 live queue-observation layer.

Proves the properties that make the observed queue a legitimate
replacement for maker_simulation's swept `queueAhead` parameter:

  * a price touch is NEVER a fill -- only opposite-side taker volume is
  * the queue must actually be consumed (queueAhead + our 25 contracts)
    before our modelled order fills
  * an observation carries no outcome/settlement field when it is opened
  * adverse-selection signs come from lib/edgelab/clv_convention.py
  * re-running over the same capture appends nothing (idempotence)
  * a missing book never falls back to a swept grid value

Stdlib + pytest only (CI is numpy-free).
"""

import gzip
import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "scripts", "research", "mlb_alpha_0002")
sys.path.insert(0, REPO)

from lib.edgelab import clv_convention as cc                                  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qo = load("queue_observation")
ms = load("maker_simulation")

DATE = "2026-08-20"
EVENT = "KXMLBF5-26AUG201905BOSNYY"          # scheduled start 2026-08-20T23:05Z
TICKER = EVENT + "-NYY"
PLACED = "2026-08-20T22:00:00Z"

# best YES bid 40 (30 resting -- the queue ahead of us), best NO bid 56,
# so the derived YES ask is 44 and the book agrees with the quote below.
BOOK = {"yes": [[39, 50.0], [40, 30.0]], "no": [[55, 40.0], [56, 20.0]]}

SIGNAL = {"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-C01-F5REV",
          "ruleSha256": "f" * 64, "episodeKey": "20260820T2200", "marketTicker": TICKER,
          "eventTicker": EVENT, "capturedAt": PLACED, "writtenAt": PLACED,
          "signalSide": "YES", "dMid60Cents": -4.0, "yesBid": 40, "yesAsk": 44,
          "executablePriceCents": 44, "passiveLimitCents": 40, "spreadCents": 4,
          "volume": 900, "openInterest": 400, "outcomeFieldsPresent": False}


def quote(at, yes_bid, yes_ask, fp="q"):
    return {"runId": "R", "capturedAt": at, "marketTicker": TICKER, "eventTicker": EVENT,
            "fp": fp, "yesBid": yes_bid, "yesAsk": yes_ask, "noBid": 100 - yes_ask,
            "noAsk": 100 - yes_bid, "volume": 900, "openInterest": 400,
            "status": "active", "closeTime": "2026-08-21T03:00:00Z"}


def trade(at, minute_iso, taker_side, yes_price_cents, qty, trade_id):
    """Shaped exactly like the committed raw-data manifest's trade schema:
    dollar-string prices and a `count_fp` quantity."""
    return {"runId": "R", "capturedAt": at, "ticker": TICKER, "trade_id": trade_id,
            "created_time": minute_iso, "count_fp": "%0.1f" % qty,
            "yes_price_dollars": "%0.4f" % (yes_price_cents / 100.0),
            "no_price_dollars": "%0.4f" % ((100 - yes_price_cents) / 100.0),
            "taker_side": taker_side, "taker_outcome_side": taker_side,
            "taker_book_side": taker_side, "is_block_trade": False}


def write_gz(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def read_gz(path):
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """A synthetic prospective-capture tree, wired into the module."""
    cap = tmp_path / "prospective"
    monkeypatch.setattr(qo, "CAP", str(cap))
    monkeypatch.setattr(qo, "SHADOW", str(cap / "shadows"))
    monkeypatch.setattr(qo, "OUT", str(cap / "queue_observations"))
    qo.STATS.clear()

    def build(trades=(), books=None, quotes=None, signals=(SIGNAL,), post_start=True):
        qs = list(quotes if quotes is not None else [
            quote("2026-08-20T21:50:00Z", 44, 48, "q0"),
            quote(PLACED, 40, 44, "q1"),
            quote("2026-08-20T22:10:00Z", 40, 48, "q2"),
            quote("2026-08-20T22:30:00Z", 40, 44, "q3"),
            quote("2026-08-20T23:00:00Z", 30, 34, "q4"),
        ])
        if post_start:
            qs.append(quote("2026-08-20T23:10:00Z", 31, 35, "q5"))
        write_gz(str(cap / "quotes" / (DATE + ".jsonl.gz")), qs)
        for row in (books if books is not None else
                    [{"runId": "R", "capturedAt": PLACED, "marketTicker": TICKER,
                      "fp": "b1", "orderbook": BOOK}]):
            write_gz(str(cap / "books" / (DATE + ".jsonl.gz")), [row])
        if trades:
            write_gz(str(cap / "trades" / (DATE + ".jsonl.gz")), list(trades))
        shadow = cap / "shadows" / qo.CANDIDATE_ID / (DATE + ".jsonl")
        os.makedirs(os.path.dirname(str(shadow)), exist_ok=True)
        with open(str(shadow), "a") as fh:
            for s in signals:
                fh.write(json.dumps(s, sort_keys=True) + "\n")
        return str(cap)

    return build


def run(argv=None):
    return qo.main(argv or ["--date", DATE, "--lookback-days", "1"])


def partition(cap, kind):
    return read_gz(os.path.join(cap, "queue_observations", kind, DATE + ".jsonl.gz"))


# ------------------------------------------------------------- open rows
def test_open_row_records_the_observed_queue_not_a_swept_assumption(capture):
    cap = capture()
    assert run() == 0
    rows = partition(cap, "opened")
    assert len(rows) == 1
    r = rows[0]
    assert r["queueAheadObserved"] == 30.0              # displayed depth AT our passive price
    assert r["queueAheadBasis"] == qo.QUEUE_BASIS_OBSERVED
    assert r["restingQtyAtPassivePrice"] == 30.0
    assert r["sweptQueueParameterSubstituted"] is False
    assert r["sweptParameterMayNeverReplaceObservation"] is True
    assert r["modelledContracts"] == 25                 # frozen, read from MAKER-A
    assert r["protocolSha256"] == qo.PROTOCOL["protocolSha256"]
    # both touches and the level at our passive price are recorded
    assert r["bookState"]["bestYesBidCents"] == 40 and r["bookState"]["bestYesBidQty"] == 30.0
    assert r["bookState"]["bestNoBidCents"] == 56 and r["bookState"]["bestNoBidQty"] == 20.0
    assert r["bookState"]["derivedYesAskCents"] == 44
    assert r["bookQuoteAgrees"] is True
    assert r["scheduledStartUtc"] == "2026-08-20T23:05:00Z"
    assert r["deadlineBasis"] == "MAX_WAIT_MINUTES"     # placement + 30 < start - 5


def test_open_row_carries_no_outcome_or_settlement_field(capture):
    """(3) An observation is opened strictly before any outcome exists."""
    cap = capture()
    assert run() == 0
    row = partition(cap, "opened")[0]
    blob = json.dumps(row).lower()
    for banned in ("settlement", "settledresult", "netprofitloss", "realizedreturn",
                   "realisedreturn", "profit", "payout", "won", "closingprice", "roi"):
        assert banned not in blob, "open row leaked an outcome field (%s): %s" % (banned, row)
    assert row["outcomeFieldsPresent"] is False
    assert row["recordType"] == "OPEN"


def test_a_missing_book_never_falls_back_to_the_swept_grid(capture):
    cap = capture(books=[])                              # nothing captured near placement
    assert run() == 0
    row = partition(cap, "opened")[0]
    assert row["bookAvailable"] is False
    assert row["queueAheadObserved"] is None
    assert row["queueAheadBasis"] == qo.QUEUE_BASIS_UNOBSERVED
    assert row["sweptQueueParameterSubstituted"] is False
    obs = partition(cap, "observations")[0]
    assert obs["fillEvaluable"] is False
    assert obs["conservativeFillObservedQueue"] is None
    assert obs["orderState"] == qo.ORDER_NOT_EVALUABLE


# ------------------------------------------------------------ fill logic
def _evaluate(cap, trades_rows):
    ev = qo.load_evidence([DATE])
    rec = partition(cap, "opened")[0]
    return qo.evaluate(rec, ev, ev.through_dt())


def test_a_price_touch_alone_is_not_a_fill(capture):
    """(1) Prints at our exact price on the SAME side as our resting bid
    are aggressors buying what we want to buy: they touch our price and
    can never fill us."""
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "yes", 40, 999.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["oppositeSideTakerFlow"] == 0.0
    assert obs["conservativeFillObservedQueue"]["filled"] is False
    assert obs["optimisticBoundZeroQueue"]["filled"] is False   # not even at zero queue
    assert obs["orderState"] == qo.ORDER_EXPIRED
    assert obs["minutesToFill"] is None
    assert obs["queueDepletionFraction"] == 0.0


def test_fill_requires_opposite_flow_to_exceed_queue_ahead_plus_our_size(capture):
    """(2) 30 resting ahead + 25 modelled contracts = 55 needed."""
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 40, 54.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["oppositeSideTakerFlow"] == 54.0
    assert obs["conservativeFillObservedQueue"]["flowRequired"] == 55
    assert obs["conservativeFillObservedQueue"]["filled"] is False
    assert obs["queueDepletionFraction"] == 1.0          # queue ahead is gone...
    assert obs["orderState"] == qo.ORDER_EXPIRED         # ...but we are not filled
    assert obs["optimisticBoundZeroQueue"]["filled"] is True   # bound only, never pooled


def test_one_more_contract_of_opposite_flow_fills_and_times_the_fill(capture):
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 40, 54.0, "t1"),
                          trade("2026-08-20T22:25:00Z", "2026-08-20T22:20:00Z",
                                "no", 39, 1.0, "t2")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["oppositeSideTakerFlow"] == 55.0
    assert obs["conservativeFillObservedQueue"]["filled"] is True
    assert obs["orderState"] == qo.ORDER_FILLED
    assert obs["minutesToFill"] == 20
    assert obs["filledAt"] == "2026-08-20T22:20:00Z"
    assert obs["expiryReason"] == "FILLED"
    assert obs["hypotheticalFillCost"]["contracts"] == 25
    assert obs["hypotheticalFillCost"]["costOnly"] is True


def test_flow_away_from_our_price_never_counts(capture):
    """A taker hitting a WORSE bid (38) than ours cannot have consumed our
    level -- the frozen definition only counts prints at or through it."""
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 41, 999.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["oppositeSideTakerFlow"] == 0.0
    assert obs["conservativeFillObservedQueue"]["filled"] is False


def test_fill_definition_is_delegated_to_the_frozen_simulator(capture):
    """The module must not carry its own copy of the fill rule."""
    src = open(os.path.join(SCRIPTS, "queue_observation.py")).read()
    assert "ms.simulate_passive_fill" in src
    assert "ms.opposite_taker_side" in src
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 40, 60.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    direct = ms.simulate_passive_fill(
        [{"created_minute": qo.minute_of(qo.parse_ts("2026-08-20T22:05:00Z")),
          "taker_side": "no", "yes_price_cents": 40, "quantity": 60.0}],
        qo.minute_of(qo.parse_ts(PLACED)), obs["evaluatedThroughMinute"], "YES", 40, 30.0, 25)
    assert obs["conservativeFillObservedQueue"] == direct


def test_queue_depletion_progress_and_book_trail_are_recorded(capture):
    books = [{"runId": "R", "capturedAt": PLACED, "marketTicker": TICKER, "fp": "b1",
              "orderbook": BOOK},
             {"runId": "R", "capturedAt": "2026-08-20T22:10:00Z", "marketTicker": TICKER,
              "fp": "b2", "orderbook": {"yes": [[40, 12.0]], "no": [[56, 20.0]]}}]
    cap = capture(books=books,
                  trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 40, 18.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["queueRemaining"] == 12.0
    assert obs["queueDepletionFraction"] == 0.6
    assert obs["flowRequiredForFill"] == 55
    assert obs["bookStatesObserved"] == 2
    assert obs["restingQtyAtPassivePriceLatest"] == 12.0
    assert obs["bookTrail"][1]["minutesSincePlacement"] == 10.0
    assert obs["windowFlow"]["T+5m"]["oppositeSideTakerFlow"] == 18.0
    assert obs["windowFlow"]["T+1m"]["oppositeSideTakerFlow"] == 0.0


# ------------------------------------------------------- adverse selection
def test_adverse_selection_signs_follow_the_canonical_clv_convention(capture):
    """(4) Positive = the market moved TOWARD our purchased side."""
    cap = capture()
    assert run() == 0
    obs = partition(cap, "observations")[0]
    adv = obs["adverseSelectionFromPlacement"]
    assert obs["clvConvention"] == cc.CONVENTION_ID and obs["clvUnit"] == cc.UNIT_CENTS

    # +10m: yesAsk 44 -> 48, i.e. YES got more expensive after we wanted it
    at10 = adv["T+10m"]
    expected = cc.clv_for_side(40, cc.executable_price_cents({"yesBid": 40, "yesAsk": 48},
                                                             cc.SIDE_YES), cc.SIDE_YES,
                               unit=cc.UNIT_CENTS)
    assert at10["clvCents"] == expected == 8.0
    assert at10["adverse"] is False
    assert at10["quoteCapturedAt"] == "2026-08-20T22:10:00Z"

    # pregame close: the market collapsed away from us -> adverse
    close = adv[qo.HORIZON_CLOSE]
    expected_close = cc.clv_for_side(40, cc.executable_price_cents({"yesBid": 30, "yesAsk": 34},
                                                                   cc.SIDE_YES), cc.SIDE_YES,
                                     unit=cc.UNIT_CENTS)
    assert close["clvCents"] == expected_close == -6.0
    assert close["adverse"] is True
    assert close["quoteCapturedAt"] == "2026-08-20T23:00:00Z"    # last PREGAME two-sided quote


def test_no_side_adverse_selection_uses_the_no_executable_price(capture):
    sig = dict(SIGNAL, signalSide="NO", dMid60Cents=4.0, passiveLimitCents=56,
               executablePriceCents=60, episodeKey="20260820T2200N")
    cap = capture(signals=[sig])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    at10 = obs["adverseSelectionFromPlacement"]["T+10m"]
    q10 = {"yesBid": 40, "yesAsk": 48, "noBid": 52, "noAsk": 60}
    expected = cc.clv_for_side(56, cc.executable_price_cents(q10, cc.SIDE_NO), cc.SIDE_NO,
                               unit=cc.UNIT_CENTS)
    assert at10["clvCents"] == expected == 4.0
    assert obs["adverseSelectionFromPlacement"][qo.HORIZON_CLOSE]["clvCents"] == 14.0


def test_horizons_are_declared_not_silently_missing(capture):
    cap = capture(post_start=False)                      # evidence stops before first pitch
    assert run() == 0
    obs = partition(cap, "observations")[0]
    close = obs["adverseSelectionFromPlacement"][qo.HORIZON_CLOSE]
    assert close["measured"] is False and close["settled"] is False
    assert close["reason"] == "PREGAME_WINDOW_STILL_OPEN"
    assert partition(cap, "finalized") == []             # nothing finalises early


def test_adverse_selection_from_fill_is_anchored_at_the_inferred_fill(capture):
    cap = capture(trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:10:00Z",
                                "no", 40, 60.0, "t1")])
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["orderState"] == qo.ORDER_FILLED
    assert obs["adverseSelectionFromFill"] is not None
    assert obs["adverseSelectionFromPlacement"]["T+30m"]["quoteCapturedAt"] == \
        "2026-08-20T22:30:00Z"


# --------------------------------------------------------- expiry / state
def test_a_fully_retraced_signal_expires_under_the_frozen_rule(capture):
    quotes = [quote(PLACED, 40, 44, "q1"),
              quote("2026-08-20T22:10:00Z", 45, 47, "q2"),      # mid 46 == reference mid
              quote("2026-08-20T23:00:00Z", 45, 47, "q3"),
              quote("2026-08-20T23:10:00Z", 45, 47, "q4")]
    cap = capture(quotes=quotes,
                  trades=[trade("2026-08-20T22:25:00Z", "2026-08-20T22:20:00Z",
                                "no", 40, 999.0, "t1")])       # flow arrives AFTER invalidation
    assert run() == 0
    obs = partition(cap, "observations")[0]
    assert obs["invalidatedAtMinute"] is not None
    assert obs["expiryReason"] == "SIGNAL_INVALIDATED"
    assert obs["conservativeFillObservedQueue"]["filled"] is False


def test_completed_records_are_finalized_once(capture):
    cap = capture()
    assert run() == 0
    finals = partition(cap, "finalized")
    assert len(finals) == 1 and finals[0]["recordType"] == "FINAL"
    assert finals[0]["observationState"] == qo.STATE_COMPLETE
    assert finals[0]["queueAheadObservedReplacesSweep"] is True
    assert run() == 0                                    # already final -> never reopened
    assert len(partition(cap, "finalized")) == 1


# ------------------------------------------------------------ idempotence
def test_rerunning_over_the_same_capture_appends_nothing(capture):
    """(5) The workflow runs this every ~10 minutes; a repeat over
    unchanged input must not duplicate a single row."""
    cap = capture(post_start=False,
                  trades=[trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z",
                                "no", 40, 10.0, "t1")])
    assert run() == 0
    first = {k: len(partition(cap, k)) for k in ("opened", "observations", "finalized")}
    assert first == {"opened": 1, "observations": 1, "finalized": 0}
    for _ in range(3):
        assert run() == 0
    assert {k: len(partition(cap, k)) for k in ("opened", "observations", "finalized")} == first
    ids = [r["observationId"] for r in partition(cap, "opened")]
    assert len(ids) == len(set(ids))


def test_new_capture_data_appends_exactly_one_more_observation(capture):
    cap = capture(post_start=False, quotes=[quote(PLACED, 40, 44, "q1"),
                                            quote("2026-08-20T22:10:00Z", 40, 48, "q2"),
                                            quote("2026-08-20T22:30:00Z", 40, 44, "q3")])
    assert run() == 0
    assert len(partition(cap, "observations")) == 1
    write_gz(os.path.join(cap, "quotes", DATE + ".jsonl.gz"),
             [quote("2026-08-20T22:40:00Z", 41, 45, "q9")])
    assert run() == 0
    rows = partition(cap, "observations")
    assert len(rows) == 2
    assert rows[0]["evidenceThroughAt"] != rows[1]["evidenceThroughAt"]
    assert rows[0]["observationId"] == rows[1]["observationId"]


def test_open_set_is_replayed_from_the_log_with_no_state_file(capture):
    cap = capture(post_start=False)
    assert run() == 0
    listing = os.listdir(os.path.join(cap, "queue_observations"))
    assert sorted(listing) == ["observations", "opened"]      # no cache file of any kind
    assert not any(name.endswith(".json") for name in listing)


# -------------------------------------------------------------- plumbing
def test_change_suppressed_reference_rows_are_resolved_back_to_full_state(capture):
    cap = capture()
    write_gz(os.path.join(cap, "books_unchanged", DATE + ".jsonl.gz"),
             [{"runId": "R2", "capturedAt": "2026-08-20T22:20:00Z", "marketTicker": TICKER,
               "unchanged": True, "fp": "b1"}])
    write_gz(os.path.join(cap, "quotes_unchanged", DATE + ".jsonl.gz"),
             [{"runId": "R2", "capturedAt": "2026-08-20T22:20:00Z", "marketTicker": TICKER,
               "unchanged": True, "fp": "q1"}])
    ev = qo.load_evidence([DATE])
    assert len(ev.books[TICKER]) == 2
    restated = [q for dt, q in ev.quotes[TICKER] if q["capturedAt"] == "2026-08-20T22:20:00Z"]
    assert restated and restated[0]["yesBid"] == 40 and restated[0]["yesAsk"] == 44


def test_duplicate_trade_ids_are_counted_once(capture):
    row = trade("2026-08-20T22:15:00Z", "2026-08-20T22:05:00Z", "no", 40, 60.0, "t1")
    cap = capture(trades=[row, dict(row, capturedAt="2026-08-20T22:25:00Z")])
    ev = qo.load_evidence([DATE])
    assert len(ev.trades[TICKER]) == 1
    assert run() == 0
    assert partition(cap, "observations")[0]["oppositeSideTakerFlow"] == 60.0


def test_book_levels_are_read_by_price_not_by_position():
    state = qo.book_state({"yes": [[12, 5.0], [44, 7.0], [30, 6.0]], "no": [[10, 1.0], [50, 2.0]]})
    assert state["bestYesBidCents"] == 44 and state["bestYesBidQty"] == 7.0
    assert state["bestNoBidCents"] == 50 and state["derivedYesAskCents"] == 50
    assert qo.qty_at_price([(44, 7.0), (30, 6.0)], 30) == 6.0
    assert qo.qty_at_price([(44, 7.0)], 30) is None       # absent is not zero


def test_module_makes_no_network_call_at_all():
    src = open(os.path.join(SCRIPTS, "queue_observation.py")).read()
    for forbidden in ("import requests", "import httpx", "urllib.request", "urllib.error",
                      "http.client", "socket"):
        assert forbidden not in src, "queue observation must read captured files only"


# ---------------------------------------------------------------------------
# Kalshi fixed-point migration: the orderbook payload moved to `orderbook_fp`
# and the two keys use different price units. A dollar price read as cents
# rounds to 1c -- inside the valid range, so it would be silently WRONG.
# ---------------------------------------------------------------------------

def test_integer_cent_books_are_detected_as_cents():
    book = {"yes": [[57, 25], [56, 10]], "no": [[43, 12]]}
    assert qo.detect_price_unit(book) == qo.PRICE_UNIT_CENTS
    assert qo.side_levels(book, "yes") == [(57, 25.0), (56, 10.0)]


def test_fixed_point_dollar_books_are_detected_and_scaled_to_cents():
    book = {"yes": [["0.5700", "25.0000"], ["0.5600", "10.0000"]],
            "no": [["0.4300", "12.0000"]]}
    assert qo.detect_price_unit(book) == qo.PRICE_UNIT_DOLLARS
    assert qo.side_levels(book, "yes") == [(57, 25.0), (56, 10.0)]


def test_a_dollar_price_is_never_silently_read_as_one_cent():
    """The specific failure this guards: float("0.5700") -> round -> 1c,
    which passes the 0..100 range check and would corrupt every queue-ahead
    figure built on it while looking entirely plausible."""
    assert qo.level_pair(["0.5700", "25"], qo.PRICE_UNIT_CENTS) == (1, 25.0)   # the wrong reading
    assert qo.level_pair(["0.5700", "25"], qo.PRICE_UNIT_DOLLARS) == (57, 25.0)  # the right one
    # and detection must pick the right one for a whole dollar-denominated book
    book = {"yes": [["0.5700", "25"]], "no": []}
    assert qo.side_levels(book, "yes") == [(57, 25.0)]


def test_a_mixed_or_out_of_range_book_is_ambiguous_and_parses_nothing():
    mixed = {"yes": [[57, 25], ["0.5600", 10]], "no": []}
    assert qo.detect_price_unit(mixed) == qo.PRICE_UNIT_AMBIGUOUS
    assert qo.side_levels(mixed, "yes") == []


def test_ambiguous_unit_refuses_every_level_rather_than_guessing():
    assert qo.level_pair([57, 25], qo.PRICE_UNIT_AMBIGUOUS) is None
    assert qo.level_pair(["0.57", 25], qo.PRICE_UNIT_AMBIGUOUS) is None


def test_an_empty_book_is_ambiguous_not_silently_cents():
    assert qo.detect_price_unit({"yes": [], "no": []}) == qo.PRICE_UNIT_AMBIGUOUS
    assert qo.detect_price_unit(None) == qo.PRICE_UNIT_AMBIGUOUS
