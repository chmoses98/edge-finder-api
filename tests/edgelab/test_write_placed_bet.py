#!/usr/bin/env python3
"""
tests/edgelab/test_write_placed_bet.py
==========================================
Coverage for lib/edgelab/bets.py's write_placed_bet -- THE canonical
write function every entry surface (log_bet.py, the "Record Placed Bet"
GitHub Actions form, any future chat-driven writer) must go through.
Canonical Placed-Bet Ledger milestone, requirement 6: duplicate
detection, conflict detection, tranche preservation, receipts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet


def _rec(**overrides):
    defaults = dict(
        market_ticker="KXMLBF5-TEST-DET", selection="DET F5 moneyline",
        stake=5.0, entry_price=0.505, entry_timestamp="2026-08-03T18:00:00Z",
        entry_method="MANUAL_CHAT_CONFIRMED",
    )
    defaults.update(overrides)
    return build_manual_bet_record(
        defaults.pop("market_ticker"), defaults.pop("selection"), defaults.pop("stake"),
        defaults.pop("entry_price"), defaults.pop("entry_timestamp"), **defaults,
    )


def test_new_bet_is_written_and_receipt_reflects_it(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    receipt = write_placed_bet(_rec(), path=path)
    assert receipt["success"] is True
    assert receipt["duplicateStatus"] == "NEW"
    assert receipt["stake"] == 5.0
    assert receipt["entryPrice"] == 0.505
    assert receipt["potentialGrossReturn"] == round(5.0 * (1 / 0.505), 2)
    assert receipt["linkageStatus"] == "UNLINKED"
    assert receipt["settlementStatus"] == "pending"
    assert receipt["clvStatus"] == "UNAVAILABLE"
    rows = list(storage.read_records(path))
    assert len(rows) == 1


def test_exact_retry_is_a_deterministic_noop(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    r1 = write_placed_bet(rec, path=path)
    retry = _rec(created_at=rec["createdAt"])  # same content, simulating a resend
    r2 = write_placed_bet(retry, path=path)
    assert r2["success"] is True
    assert r2["duplicateStatus"] == "DUPLICATE_NOOP"
    assert r2["betId"] == r1["betId"]
    rows = list(storage.read_records(path))
    assert len(rows) == 1  # never a second row


def test_conflicting_duplicate_is_refused_by_default(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    write_placed_bet(_rec(), path=path)
    conflicting = _rec(stake=999.0)  # same ticker+timestamp -> same betId, different stake
    receipt = write_placed_bet(conflicting, path=path)
    assert receipt["success"] is False
    assert receipt["duplicateStatus"] == "CONFLICT"
    assert any(f["field"] == "stake" for f in receipt["conflictingFields"])
    rows = list(storage.read_records(path))
    assert len(rows) == 1
    assert rows[0]["stake"] == 5.0  # untouched


def test_conflict_resolved_explicitly_with_overwrite_marks_corrected(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    write_placed_bet(_rec(), path=path)
    corrected = _rec(stake=7.5)
    receipt = write_placed_bet(corrected, path=path, on_conflict="overwrite")
    assert receipt["success"] is True
    assert receipt["duplicateStatus"] == "CORRECTED"
    rows = list(storage.read_records(path))
    assert len(rows) == 1
    assert rows[0]["stake"] == 7.5
    assert rows[0]["recordStatus"] == "CORRECTED"


def test_second_tranche_is_never_treated_as_a_duplicate(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    r1 = write_placed_bet(_rec(), path=path)
    tranche2 = _rec(entry_timestamp="2026-08-03T18:05:00Z", stake=3.0, entry_price=0.51)
    r2 = write_placed_bet(tranche2, path=path)
    assert r1["betId"] != r2["betId"]
    assert r2["duplicateStatus"] == "NEW"
    rows = list(storage.read_records(path))
    assert len(rows) == 2


def test_near_duplicate_within_window_is_flagged_but_still_written(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    write_placed_bet(_rec(), path=path)
    close_tranche = _rec(entry_timestamp="2026-08-03T18:01:00Z", stake=3.0, entry_price=0.51)
    receipt = write_placed_bet(close_tranche, path=path)
    assert receipt["success"] is True
    assert receipt["duplicateStatus"] == "NEW"
    assert len(receipt["nearDuplicateWarnings"]) == 1


def test_far_apart_bets_on_same_ticker_are_not_flagged(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    write_placed_bet(_rec(), path=path)
    far_tranche = _rec(entry_timestamp="2026-08-03T20:00:00Z", stake=3.0, entry_price=0.51)
    receipt = write_placed_bet(far_tranche, path=path, near_duplicate_window_seconds=180)
    assert receipt["nearDuplicateWarnings"] == []


def test_invalid_record_is_never_written(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    del rec["stake"]  # required field missing
    receipt = write_placed_bet(rec, path=path)
    assert receipt["success"] is False
    assert receipt["duplicateStatus"] == "INVALID"
    assert receipt["errors"]
    assert list(storage.read_records(path)) == []


def test_missing_ticker_fails_schema_validation(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = build_manual_bet_record(None, "x", 1.0, 0.5, "2026-08-03T18:00:00Z")
    receipt = write_placed_bet(rec, path=path)
    assert receipt["success"] is False
    assert receipt["duplicateStatus"] == "INVALID"


def test_manual_bet_without_model_support_never_fabricates_model_fields(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    receipt = write_placed_bet(_rec(source="MANUAL"), path=path)
    row = list(storage.read_records(path))[0]
    assert row["modelSupported"] is None
    assert row["modelFairProbability"] is None
    assert row["modelEvaluationId"] is None
    assert receipt["linkageStatus"] == "UNLINKED"


def test_production_recommendation_confirmed_bet_carries_linkage(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec(
        source="MODEL", entry_method="PRODUCTION_RECOMMENDATION_CONFIRMED",
        recommendation_id="rec-1", model_evaluation_id="me-1", model_supported=True,
        production_run_id="prod-1", snapshot_id="snap-1",
    )
    receipt = write_placed_bet(rec, path=path)
    assert receipt["linkageStatus"] == "LINKED"
    assert set(receipt["linkedEntities"]) >= {"recommendation", "modelEvaluation", "productionRun", "snapshot"}


def test_invalid_on_conflict_value_raises_programming_error(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    try:
        write_placed_bet(_rec(), path=path, on_conflict="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass
