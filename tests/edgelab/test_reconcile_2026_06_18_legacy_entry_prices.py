#!/usr/bin/env python3
"""
tests/edgelab/test_reconcile_2026_06_18_legacy_entry_prices.py
====================================================================
Coverage for scripts/edgelab/reconcile_2026_06_18_legacy_entry_prices.py
-- a narrowly-scoped, one-time reconciliation for exactly two bets
(data/bets.json indices 88/89, 2026-06-18) previously flagged
AMBIGUOUS_REQUIRES_REVIEW. Proves: only these two rows ever change,
entryPrice/entryOdds are never altered (only annotated), the script
refuses to run if either bet's entryPrice doesn't match what the
evidence review confirmed, reruns are byte-identical, and no
normalization behavior (lib.edgelab.bets._classify_price_value and
friends) is touched by this change at all.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage

_spec = importlib.util.spec_from_file_location(
    "reconcile_2026_06_18_legacy_entry_prices",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "reconcile_2026_06_18_legacy_entry_prices.py"),
)
reconcile_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile_script)

_BET_ID_88 = "12cd9391595975ffb29b4839497c5773043676cf"
_BET_ID_89 = "b4bac80dca6ffd6c306994a0e9b447646db937d5"


def _make_row(bet_id, entry_price, **overrides):
    row = {
        "schemaVersion": "1", "betId": bet_id, "gameId": "2026-06-18_X_Y", "gameDate": "2026-06-18",
        "matchup": "X@Y", "sport": "MLB", "platform": "KALSHI", "marketTicker": "KXMLBGAME-TEST",
        "eventTicker": None, "seriesTicker": None, "marketFamily": "ML_Away", "marketHorizon": None,
        "selection": "ML_Away X", "side": "YES", "threshold": None, "stake": 3.0,
        "entryPrice": entry_price, "entryOdds": None, "entryTimestamp": "2026-06-18T21:30:00Z",
        "contracts": None, "estimatedPayout": None, "scheduledStart": None, "source": "MANUAL",
        "entryMethod": "LEGACY_BACKFILL", "recommendationId": None, "modelEvaluationId": None,
        "productionRunId": None, "snapshotId": None, "replayRunId": None, "manualFairProbability": None,
        "modelFairProbability": 54.3, "estimatedEdgeAtEntry": 1.47, "modelSupported": None,
        "confidence": "MEDIUM", "dataQuality": None, "correlationGroup": None, "correlationGroups": [],
        "trackingType": "REAL", "thesisTags": [], "rationale": None, "recordStatus": "ACTIVE",
        "status": "pending", "closingPrice": None, "clvQuoteId": None, "clv": None, "result": None,
        "returnAmount": None, "netProfitLoss": None, "createdAt": "2026-07-31T23:53:10Z",
        "updatedAt": "2026-08-03T05:46:48Z", "validationStatus": "valid",
        "provenance": {
            "sourceSystem": "data_bets_json", "sourceFile": "data/bets.json", "sourceKey": "88",
            "capturedAt": "2026-06-18T21:30:00Z", "ingestedAt": "2026-08-03T05:46:48Z",
        },
    }
    row.update(overrides)
    return row


def _write_fixture_ledger(path, extra_rows=()):
    rows = [
        _make_row(_BET_ID_88, 0.4854),
        _make_row(_BET_ID_89, 0.5146, marketTicker="KXMLBGAME-TEST-2", gameId="2026-06-18_A_B",
                  provenance={
                      "sourceSystem": "data_bets_json", "sourceFile": "data/bets.json", "sourceKey": "89",
                      "capturedAt": "2026-06-18T21:30:00Z", "ingestedAt": "2026-08-03T05:46:48Z",
                  }),
        *extra_rows,
    ]
    storage.write_all_records(path, rows)
    return rows


@pytest.fixture(autouse=True)
def _default_argv(monkeypatch):
    """main() reads sys.argv unconditionally -- default to no flags for every test; individual tests override for --dry-run."""
    monkeypatch.setattr(sys, "argv", ["reconcile_2026_06_18_legacy_entry_prices.py"])


def test_reconciliation_updates_only_the_two_target_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    other_row = _make_row("some-other-bet-id", 0.51, marketTicker="KXMLBGAME-OTHER")
    _write_fixture_ledger(path, extra_rows=[other_row])

    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)
    exit_code = reconcile_script.main()
    assert exit_code == 0

    rows = {r["betId"]: r for r in storage.read_records(path)}
    assert rows[_BET_ID_88]["reconciliation"] is not None
    assert rows[_BET_ID_89]["reconciliation"] is not None
    assert rows["some-other-bet-id"].get("reconciliation") is None  # untouched


def test_entry_price_is_never_altered_only_annotated(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    reconcile_script.main()

    rows = {r["betId"]: r for r in storage.read_records(path)}
    assert rows[_BET_ID_88]["entryPrice"] == 0.4854
    assert rows[_BET_ID_89]["entryPrice"] == 0.5146
    assert rows[_BET_ID_88]["entryOdds"] is None  # never fabricated
    assert rows[_BET_ID_89]["entryOdds"] is None


def test_all_other_fields_preserved_exactly(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    original_rows = _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    reconcile_script.main()

    updated_rows = {r["betId"]: r for r in storage.read_records(path)}
    for original in original_rows:
        updated = dict(updated_rows[original["betId"]])
        updated.pop("reconciliation", None)
        assert updated == original, f"unexpected field change on {original['betId']}"


def test_rerun_is_byte_identical_noop(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    reconcile_script.main()
    with open(path, "rb") as f:
        first_bytes = f.read()

    exit_code = reconcile_script.main()
    assert exit_code == 0
    with open(path, "rb") as f:
        second_bytes = f.read()
    assert first_bytes == second_bytes


def test_refuses_to_run_if_entry_price_does_not_match_expected(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    rows = [_make_row(_BET_ID_88, 0.9999)]  # someone already changed it -- must refuse
    storage.write_all_records(path, rows)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    exit_code = reconcile_script.main()
    assert exit_code == 1

    unchanged = list(storage.read_records(path))
    assert unchanged[0]["entryPrice"] == 0.9999  # untouched, not silently corrected
    assert unchanged[0].get("reconciliation") is None


def test_refuses_to_run_if_bet_id_not_found(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    storage.write_all_records(path, [_make_row("unrelated-bet", 0.5)])
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    exit_code = reconcile_script.main()
    assert exit_code == 1


def test_dry_run_makes_no_changes(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)
    monkeypatch.setattr(sys, "argv", ["reconcile_2026_06_18_legacy_entry_prices.py", "--dry-run"])

    with open(path, "rb") as f:
        before = f.read()
    exit_code = reconcile_script.main()
    assert exit_code == 0
    with open(path, "rb") as f:
        after = f.read()
    assert before == after


def test_reconciled_rows_pass_schema_validation(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    reconcile_script.main()
    for row in storage.read_records(path):
        assert schema.validate_record("placed_bet", row) == []


def test_reconciliation_metadata_matches_the_documented_evidence(tmp_path, monkeypatch):
    path = str(tmp_path / "bets.jsonl")
    _write_fixture_ledger(path)
    monkeypatch.setattr(storage, "singleton_path", lambda entity, filename: path)

    reconcile_script.main()
    rows = {r["betId"]: r for r in storage.read_records(path)}

    r88 = rows[_BET_ID_88]["reconciliation"]
    assert r88["classification"] == "SAFE_MANUAL_FIX"
    assert r88["originalSourceFile"] == "data/bets.json"
    assert r88["originalSourceIndex"] == "88"
    assert r88["originalRawValue"] == 48.54
    assert r88["corroboratingArtifactPath"] == "data/execution_slip_2026-06-18.json"
    assert r88["generalizedInferenceRuleUsed"] is False
    assert r88["reconciliationMethod"] == "MANUAL_CROSS_ARTIFACT_REVIEW"

    r89 = rows[_BET_ID_89]["reconciliation"]
    assert r89["originalSourceIndex"] == "89"
    assert r89["originalRawValue"] == 51.46


def test_normalization_functions_are_completely_unused_by_this_script():
    """
    This reconciliation is deliberately NOT implemented via
    lib.edgelab.bets's normalization functions -- it never CALLS
    _classify_price_value/_normalize_kalshi_native_price/
    _normalize_session_bets_price/build_manual_bet_record/write_placed_bet
    (checked as call sites, "name(" -- the module docstring mentions some
    of these by name in prose, which is fine), confirming no
    normalization behavior changed as a side effect of this PR. Also
    confirms the script imports nothing from lib.edgelab.bets at all.
    """
    with open(reconcile_script.__file__) as f:
        source = f.read()
    assert "from lib.edgelab import bets" not in source
    assert "from lib.edgelab.bets import" not in source
    for forbidden_call in (
        "_classify_price_value(", "_normalize_kalshi_native_price(",
        "_normalize_session_bets_price(", "build_manual_bet_record(", "write_placed_bet(",
    ):
        assert forbidden_call not in source
