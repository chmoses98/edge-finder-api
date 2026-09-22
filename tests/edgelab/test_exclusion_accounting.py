#!/usr/bin/env python3
"""
tests/edgelab/test_exclusion_accounting.py
==========================================
PHASE H: every attributable response row must end in a counted category.

Two gaps closed here.

`if not ticker: continue` in build_observations_from_snapshot was the one
exclusion in the whole path that was neither recorded nor counted. A row
entered the pipeline and left no trace of where it went, so
observationsBuilt simply came out lower than the raw market count and
nothing could tell the difference between "the exchange sent fewer
markets" and "we dropped some".

And the registry gate has always built a full record for every excluded
market -- ticker, series, title, reason -- then reported only the integer
`marketsExcluded`. The audit trail existed in memory and was discarded,
so "3,000 markets were excluded" could never be interrogated.

A malformed row is still NOT archived. Forcing it in would corrupt the
archive to make a counter balance. It is accounted for instead.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.market_universe import MARKET_TICKER_MISSING, summarize_exclusions


def _ex(reason, series="KXMLBGAME", ticker="T1"):
    return {"exclusionReason": reason, "seriesTicker": series, "marketTicker": ticker}


# --------------------------------------------------------------------------
# the aggregate
# --------------------------------------------------------------------------

def test_exclusions_are_counted_by_reason():
    summary = summarize_exclusions([
        _ex("FUTURES_OR_AWARD", "KXWS", "a"), _ex("FUTURES_OR_AWARD", "KXWS", "b"),
        _ex("NON_MLB_COMPETITION", "KXNFL", "c")])
    assert summary["total"] == 3
    assert summary["byReason"]["FUTURES_OR_AWARD"]["count"] == 2
    assert summary["byReason"]["NON_MLB_COMPETITION"]["count"] == 1


def test_each_reason_names_the_series_it_affected():
    """Which families an exclusion hits is the question worth asking of it."""
    summary = summarize_exclusions([
        _ex("FUTURES_OR_AWARD", "KXWS", "a"), _ex("FUTURES_OR_AWARD", "KXMVP", "b")])
    assert summary["byReason"]["FUTURES_OR_AWARD"]["seriesAffected"] == ["KXMVP", "KXWS"]


def test_samples_are_bounded_so_a_bad_day_cannot_grow_the_record_unbounded():
    summary = summarize_exclusions(
        [_ex("FUTURES_OR_AWARD", "KXWS", "t%d" % i) for i in range(500)])
    assert summary["byReason"]["FUTURES_OR_AWARD"]["count"] == 500
    assert len(summary["byReason"]["FUTURES_OR_AWARD"]["sampleTickers"]) == 5


def test_the_digest_covers_the_whole_set_not_just_the_sample():
    """Sampling must not make two different exclusion sets look identical."""
    many = [_ex("FUTURES_OR_AWARD", "KXWS", "t%d" % i) for i in range(50)]
    assert summarize_exclusions(many)["digest"] != summarize_exclusions(many[:-1])["digest"]


def test_the_digest_is_stable_under_snapshot_ordering():
    """Two runs over the same exclusions must compare equal however the
    snapshot happened to order its markets."""
    rows = [_ex("A", "S1", "x"), _ex("B", "S2", "y"), _ex("A", "S1", "z")]
    assert summarize_exclusions(rows)["digest"] == summarize_exclusions(rows[::-1])["digest"]


def test_an_exclusion_with_no_reason_is_still_counted_somewhere():
    """Nothing may fall out of the accounting for want of a label."""
    summary = summarize_exclusions([{"marketTicker": "a"}])
    assert summary["total"] == 1
    assert summary["byReason"]["UNKNOWN"]["count"] == 1


def test_no_exclusions_is_a_clean_empty_summary_not_a_missing_one():
    summary = summarize_exclusions([])
    assert summary["total"] == 0 and summary["byReason"] == {}
    assert summary["digest"]


# --------------------------------------------------------------------------
# the previously silent path
# --------------------------------------------------------------------------

def test_a_row_with_no_ticker_is_excluded_with_a_reason_not_dropped():
    from lib.edgelab.market_universe import build_observations_from_snapshot
    import json
    import tempfile

    snapshot = {
        "fetched_at": "2026-09-20T17:25:00.000Z",
        "markets": [
            {"ticker": "KXMLBGAME-26SEP201740CHCCIN-CHC",
             "event_ticker": "KXMLBGAME-26SEP201740CHCCIN",
             "title": "Cubs vs Reds Winner?", "status": "active",
             "yes_bid": 0.44, "yes_ask": 0.46},
            {"event_ticker": "KXMLBGAME-26SEP201740CHCCIN",   # no ticker at all
             "title": "malformed", "status": "active"},
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(snapshot, fh)
        path = fh.name
    try:
        observations, excluded = build_observations_from_snapshot(path, "run1", {})
    finally:
        os.unlink(path)

    assert len(observations) == 1, "the malformed row must NOT be archived"
    missing = [e for e in excluded if e["exclusionReason"] == MARKET_TICKER_MISSING]
    assert len(missing) == 1, "...but it must be accounted for"
    assert missing[0]["marketTicker"] is None
    # Enough to audit the malformed row without archiving it.
    assert "event_ticker" in missing[0]["rawFieldsPresent"]
    assert "title" in missing[0]["rawFieldsPresent"]


def test_every_raw_market_ends_up_either_built_or_excluded():
    """The identity Phase H exists to make true: nothing vanishes."""
    from lib.edgelab.market_universe import build_observations_from_snapshot
    import json
    import tempfile

    raw = [
        {"ticker": "KXMLBGAME-26SEP201740CHCCIN-CHC",
         "event_ticker": "KXMLBGAME-26SEP201740CHCCIN",
         "title": "Cubs vs Reds Winner?", "status": "active", "yes_ask": 0.46},
        {"event_ticker": "X", "title": "no ticker", "status": "active"},
        {"ticker": "KXNFLGAME-26SEP20-KC", "event_ticker": "KXNFLGAME-26SEP20",
         "title": "Chiefs Winner?", "status": "active"},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"fetched_at": "2026-09-20T17:25:00.000Z", "markets": raw}, fh)
        path = fh.name
    try:
        observations, excluded = build_observations_from_snapshot(path, "run1", {})
    finally:
        os.unlink(path)

    assert len(observations) + len(excluded) == len(raw), (
        "a raw market that is neither built nor excluded has vanished silently")
