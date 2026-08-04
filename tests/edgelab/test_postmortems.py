#!/usr/bin/env python3
"""
tests/edgelab/test_postmortems.py
=====================================
Structured Postmortem Ingestion milestone: linkage only to real canonical
bets, idempotent re-import, explicit corrections (never a silent
overwrite), and canonical P/L agreement with the ledger.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage
from lib.edgelab.postmortems import (
    build_postmortem_record,
    compute_canonical_totals,
    resolve_bet_references,
    write_postmortem,
)

BET_A = {"betId": "betA", "stake": 10.0, "status": "settled", "result": "WIN", "netProfitLoss": 5.0, "recordStatus": "ACTIVE", "trackingType": None}
BET_B = {"betId": "betB", "stake": 20.0, "status": "settled", "result": "LOSS", "netProfitLoss": -20.0, "recordStatus": "ACTIVE", "trackingType": None}
ALL_BETS = {"betA": BET_A, "betB": BET_B}


def test_resolve_bet_references_splits_known_and_unknown():
    resolved, unresolved = resolve_bet_references(["betA", "betZZZ"], ALL_BETS)
    assert [b["betId"] for b in resolved] == ["betA"]
    assert unresolved == [{"reference": "betZZZ", "reason": "no canonical bet with this betId exists"}]


def test_compute_canonical_totals_matches_manual_arithmetic():
    totals = compute_canonical_totals([BET_A, BET_B])
    assert totals["totalRisked"] == 30.0
    assert totals["netProfitLoss"] == -15.0
    assert totals["totalReturned"] == 10.0 + 5.0  # only BET_A returns anything (a WIN)
    assert totals["roi"] == round(-15.0 / 30.0 * 100, 2)


def test_build_postmortem_record_never_substitutes_recommendation_for_missing_bet():
    record = build_postmortem_record("2026-08-03", ["betA", "doesNotExist"], ALL_BETS)
    assert record["linkedBetIds"] == ["betA"]
    assert record["unresolvedBetReferences"][0]["reference"] == "doesNotExist"
    assert record["validationStatus"] == "warning"  # never silently "valid" with an unresolved reference
    assert schema.validate_record("postmortem", record) == []


def test_totals_match_flag_reflects_real_discrepancy():
    record = build_postmortem_record(
        "2026-08-03", ["betA", "betB"], ALL_BETS,
        reported_totals={"totalRisked": 999.0, "totalReturned": 0, "netProfitLoss": 0, "roi": 0},
    )
    assert record["totalsMatch"] is False
    assert record["canonicalTotals"]["totalRisked"] == 30.0  # canonical figure is never replaced by the caller's claim


def test_write_postmortem_new_then_idempotent_rerun(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record = build_postmortem_record("2026-08-03", ["betA"], ALL_BETS)
    result1 = write_postmortem(record, "# Postmortem\n")
    assert result1["success"] is True
    assert result1["duplicateStatus"] == "NEW"
    assert result1["revision"] == 1

    record_again = build_postmortem_record("2026-08-03", ["betA"], ALL_BETS)
    result2 = write_postmortem(record_again, "# Postmortem\n")
    assert result2["duplicateStatus"] == "DUPLICATE_NOOP"
    assert result2["revision"] == 1

    json_path = os.path.join("data", "edgelab", "postmortems", "2026-08-03", "postmortem.json")
    assert os.path.exists(json_path)
    assert not os.path.exists(os.path.join("data", "edgelab", "postmortems", "2026-08-03", "revisions.jsonl"))


def test_write_postmortem_correction_preserves_prior_revision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_v1 = build_postmortem_record("2026-08-03", ["betA"], ALL_BETS, analytical_wins=[{"note": "first pass"}])
    write_postmortem(record_v1, "# v1\n")

    record_v2 = build_postmortem_record("2026-08-03", ["betA", "betB"], ALL_BETS, analytical_wins=[{"note": "corrected pass"}])
    result2 = write_postmortem(record_v2, "# v2\n")
    assert result2["duplicateStatus"] == "CORRECTED"
    assert result2["revision"] == 2

    pm_dir = os.path.join("data", "edgelab", "postmortems", "2026-08-03")
    revisions = list(storage.read_records(os.path.join(pm_dir, "revisions.jsonl")))
    assert len(revisions) == 1
    assert revisions[0]["revision"] == 1
    assert revisions[0]["recordStatus"] == "CORRECTED"
    assert revisions[0]["supersededBy"] == 2

    with open(os.path.join(pm_dir, "postmortem.md")) as f:
        assert f.read() == "# v2\n"


def test_write_postmortem_rejects_invalid_record():
    bad_record = {"schemaVersion": "1"}  # missing required fields
    result = write_postmortem(bad_record, "# x\n")
    assert result["success"] is False
    assert result["duplicateStatus"] == "INVALID"
