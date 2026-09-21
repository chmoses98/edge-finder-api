#!/usr/bin/env python3
"""
tests/edgelab/test_market_coverage_audit.py
===========================================
Coverage for scripts/edgelab/build_market_coverage_audit.py -- the
full-universe close-coverage audit.

Its whole value is that it never flatters the archive, so most of these
assert a refusal or an exclusion.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_spec = importlib.util.spec_from_file_location(
    "build_market_coverage_audit",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab",
                 "build_market_coverage_audit.py"),
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

START = "2026-09-20T17:40:00Z"


def _row(**over):
    row = {
        "ticker": "T", "observations": 1, "gameDate": "2026-09-20", "gameId": "g1",
        "marketFamily": "game_total", "threshold": 8.5, "awayTeam": "TOR", "homeTeam": "TEX",
        "scheduledStart": START, "title": "t", "firstObservedAt": None, "lastObservedAt": None,
        "lastPrestartAt": None, "lastPrestartSeconds": None, "lastPrestart": None,
        "postStartObservations": 0,
    }
    row.update(over)
    return row


def _built(**over):
    return audit.build_rows({"T": _row(**over)}, {}, set())[0]


def test_buckets_are_nested_and_ordered():
    assert audit.bucket_for(120) == "<=5m"
    assert audit.bucket_for(600) == "<=15m"
    assert audit.bucket_for(1700) == "<=30m"
    assert audit.bucket_for(3500) == "<=60m"
    assert audit.bucket_for(5000) == "<=90m"
    assert audit.bucket_for(99999) == ">90m"
    assert audit.bucket_for(None) == "no_valid_prestart_quote"


def test_boundaries_land_in_the_tighter_bucket():
    assert audit.bucket_for(300) == "<=5m"
    assert audit.bucket_for(301) == "<=15m"
    assert audit.bucket_for(1800) == "<=30m"
    assert audit.bucket_for(1801) == "<=60m"


def test_a_market_with_no_prestart_quote_is_not_given_one():
    row = _built(lastPrestartSeconds=None)
    assert row["coverageBucket"] == "no_valid_prestart_quote"
    assert row["coverageClass"] == "NO_VALID_PRESTART_QUOTE"
    assert row["secondsBeforeStart"] is None


def test_unresolved_start_is_its_own_class_never_assumed_covered():
    row = _built(scheduledStart=None, lastPrestartSeconds=None)
    assert row["coverageBucket"] == "start_unresolved"
    assert row["coverageClass"] == "start_unresolved"


def test_coverage_class_matches_the_canonical_30_minute_policy():
    assert _built(lastPrestartSeconds=1200)["coverageClass"] == "TRUE_CLOSE"
    assert _built(lastPrestartSeconds=1800)["coverageClass"] == "TRUE_CLOSE"
    assert _built(lastPrestartSeconds=1801)["coverageClass"] == "PRE_CLOSE"


def test_a_quote_with_no_executable_side_is_not_evidence():
    assert audit._executable_present({"yesAsk": 0.4}) is True
    assert audit._executable_present({"noBid": 0.0}) is True   # a real zero is a fact
    assert audit._executable_present({"lastPrice": 0.4}) is False
    assert audit._executable_present({}) is False


def test_settlement_is_read_not_recomputed():
    rows = audit.build_rows({"T": _row(lastPrestartSeconds=600)},
                            {"T": ("SETTLED", "YES")}, set())
    assert rows[0]["settlementStatus"] == "SETTLED"
    assert rows[0]["settlementResult"] == "YES"


def test_unwagered_markets_are_retained_not_filtered_out():
    """The universe is the point: an unbet, unmodelled market still counts."""
    rows = audit.build_rows({"T": _row(marketFamily="some_unmodelled_family",
                                       lastPrestartSeconds=600)}, {}, set())
    assert len(rows) == 1
    assert rows[0]["wageredByUs"] is False
    assert rows[0]["marketFamily"] == "some_unmodelled_family"


def test_summary_counts_are_nested_consistently():
    rows = audit.build_rows({
        "a": _row(ticker="a", lastPrestartSeconds=120),
        "b": _row(ticker="b", lastPrestartSeconds=600),
        "c": _row(ticker="c", lastPrestartSeconds=1700),
        "d": _row(ticker="d", lastPrestartSeconds=None),
        "e": _row(ticker="e", scheduledStart=None, lastPrestartSeconds=None),
    }, {"a": ("SETTLED", "YES"), "b": ("SETTLED", "NO")}, set())
    s = audit.summarize(rows)
    assert s["totalAttributableMarkets"] == 5
    assert s["marketsWithAnyPrestartEvidence"] == 3
    assert s["t5Markets"] == 1
    assert s["t15Markets"] == 2          # nested: t5 ⊆ t15
    assert s["t30Markets"] == 3          # nested: t15 ⊆ t30
    assert s["marketsWithNoPrestartEvidence"] == 1
    assert s["marketsWithUnresolvedStart"] == 1
    assert s["settledMarkets"] == 2
    assert s["settledWithT30"] == 2


def test_post_start_observations_are_counted_but_never_become_evidence():
    row = _built(postStartObservations=7, lastPrestartSeconds=None)
    assert row["postStartObservations"] == 7
    assert row["secondsBeforeStart"] is None
    assert row["coverageBucket"] == "no_valid_prestart_quote"
