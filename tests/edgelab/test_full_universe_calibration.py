#!/usr/bin/env python3
"""
tests/edgelab/test_full_universe_calibration.py
===============================================
Coverage for the full-universe market calibration research script.

These pin the statistics and, more importantly, the EXCLUSIONS: the
value of this research rests on what it refuses to score.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_spec = importlib.util.spec_from_file_location(
    "run_market_calibration",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "research",
                 "full_universe", "run_market_calibration.py"),
)
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def _audit_row(**over):
    row = {"ticker": "T", "gameId": "g1", "gameDate": "2026-09-20",
           "marketFamily": "game_total", "settlementStatus": "SETTLED",
           "settlementResult": "YES", "secondsBeforeStart": 600,
           "lastPrestartYesAsk": 0.60, "coverageClass": "TRUE_CLOSE"}
    row.update(over)
    return row


def _load(tmp_path, rows):
    path = tmp_path / "audit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return cal.load(str(path))


def test_bucket_labels_partition_the_unit_interval():
    assert cal.bucket_label(0.01) == "0.00-0.05"
    assert cal.bucket_label(0.35) == "0.30-0.40"
    assert cal.bucket_label(0.999) == "0.95-1.00"


def test_unsettled_markets_are_excluded_not_scored(tmp_path):
    rows, excluded = _load(tmp_path, [_audit_row(settlementStatus="SETTLEMENT_UNRESOLVED")])
    assert rows == [] and excluded["not_settled"] == 1


def test_market_with_no_prestart_quote_is_excluded(tmp_path):
    rows, excluded = _load(tmp_path, [_audit_row(secondsBeforeStart=None)])
    assert rows == [] and excluded["no_valid_prestart_quote"] == 1


def test_degenerate_prices_are_excluded_never_clamped(tmp_path):
    """0 and 1 are an absent or already-resolved book, not probabilities."""
    rows, excluded = _load(tmp_path, [_audit_row(lastPrestartYesAsk=0.0),
                                      _audit_row(lastPrestartYesAsk=1.0)])
    assert rows == []
    assert excluded["price_not_strictly_between_0_and_1"] == 2


def test_settled_without_a_yes_no_result_is_excluded(tmp_path):
    rows, excluded = _load(tmp_path, [_audit_row(settlementResult=None)])
    assert rows == [] and excluded["settled_without_yes_no_result"] == 1


def test_the_scored_price_is_the_executable_ask(tmp_path):
    rows, _ = _load(tmp_path, [_audit_row(lastPrestartYesAsk=0.62)])
    assert rows[0]["price"] == 0.62


def test_perfectly_calibrated_sample_reports_zero_gap():
    rows = ([{"price": 0.5, "resolvedYes": True, "gameId": "g%d" % i, "gameDate": "2026-09-20"}
             for i in range(50)] +
            [{"price": 0.5, "resolvedYes": False, "gameId": "h%d" % i, "gameDate": "2026-09-20"}
             for i in range(50)])
    s = cal.stats(rows)
    assert s["n"] == 100
    assert s["calibrationGap"] == 0.0
    assert s["brier"] == 0.25
    assert s["marketWithinInterval"] is True


def test_a_clearly_overpriced_sample_is_flagged_outside_the_interval():
    rows = [{"price": 0.9, "resolvedYes": i < 10, "gameId": "g%d" % i, "gameDate": "2026-09-20"}
            for i in range(100)]
    s = cal.stats(rows)
    assert s["calibrationGap"] < -0.5
    assert s["marketWithinInterval"] is False


def test_wilson_interval_widens_as_n_shrinks():
    wide_lo, wide_hi = cal.wilson(5, 10)
    tight_lo, tight_hi = cal.wilson(500, 1000)
    assert (wide_hi - wide_lo) > (tight_hi - tight_lo)


def test_independent_games_is_reported_so_correlation_is_visible():
    """n alone overstates the evidence when markets share games."""
    rows = [{"price": 0.5, "resolvedYes": True, "gameId": "same", "gameDate": "2026-09-20"}
            for _ in range(200)]
    s = cal.stats(rows)
    assert s["n"] == 200
    assert s["independentGames"] == 1
