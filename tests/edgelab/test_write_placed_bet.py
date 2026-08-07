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

from lib.edgelab import schema, storage
from lib.edgelab.bets import build_manual_bet_record, cancel_placed_bet, write_placed_bet


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


def test_model_supported_true_without_a_real_model_evaluation_id_raises():
    """
    Maintainer review regression: model_supported=True with no
    model_evaluation_id previously passed through silently and validated
    cleanly against the schema -- a purely manual bet could falsely claim
    real model backing, corrupting the model-supported-vs-manual
    postmortem attribution this milestone itself reports on.
    """
    try:
        build_manual_bet_record(
            "KXMLBF5-FAKE-MODEL", "x", 5.0, 0.5, "2026-08-03T18:00:00Z",
            model_supported=True,
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_model_supported_true_with_a_real_model_evaluation_id_is_allowed():
    rec = build_manual_bet_record(
        "KXMLBF5-REAL-MODEL", "x", 5.0, 0.5, "2026-08-03T18:00:00Z",
        model_supported=True, model_evaluation_id="me-1",
    )
    assert rec["modelSupported"] is True
    assert rec["modelEvaluationId"] == "me-1"


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


# ---------------------------------------------------------------------------
# Maintainer review regressions: a correction (on_conflict="overwrite") or
# an identical retry must never silently reset a bet's downstream
# settlement/CLV/linkage state -- found via adversarial testing during the
# Canonical Placed-Bet Ledger milestone's production-readiness review.
# ---------------------------------------------------------------------------

def _settle_in_place(path, bet_id, **fields):
    """Simulate settle_markets.py/collect_clv.py updating an existing bet row out of band."""
    rows = list(storage.read_records(path))
    updated = []
    for row in rows:
        if row["betId"] == bet_id:
            row = dict(row)
            row.update(fields)
        updated.append(row)
    storage.write_all_records(path, updated)


def test_correction_never_resets_settlement_or_clv_state(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    _settle_in_place(
        path, rec["betId"], status="settled", result="WIN", netProfitLoss=4.9,
        returnAmount=4.9, clv=2.5, closingPrice=0.48, clvQuoteId="clvq-123",
    )

    corrected = _rec(stake=6.0)  # unrelated entry-time fix, e.g. a typo'd stake
    receipt = write_placed_bet(corrected, path=path, on_conflict="overwrite")
    assert receipt["success"] is True
    assert receipt["duplicateStatus"] == "CORRECTED"

    row = list(storage.read_records(path))[0]
    assert row["stake"] == 6.0
    assert row["status"] == "settled"
    assert row["result"] == "WIN"
    assert row["netProfitLoss"] == 4.9
    assert row["returnAmount"] == 4.9
    assert row["clv"] == 2.5
    assert row["closingPrice"] == 0.48
    assert row["clvQuoteId"] == "clvq-123"
    assert row["recordStatus"] == "CORRECTED"


def test_identical_retry_after_settlement_is_still_a_noop_not_a_conflict(tmp_path):
    """
    A resent/retried entry-time submission (e.g. a retried GitHub Actions
    run) must still resolve to DUPLICATE_NOOP after the bet has since been
    settled -- the retry never claims to know about settlement fields, so
    it must not be treated as conflicting with them.
    """
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    _settle_in_place(path, rec["betId"], status="settled", result="WIN", netProfitLoss=4.9)

    retry = _rec(created_at=rec["createdAt"])
    receipt = write_placed_bet(retry, path=path)
    assert receipt["duplicateStatus"] == "DUPLICATE_NOOP"
    row = list(storage.read_records(path))[0]
    assert row["status"] == "settled"  # untouched by the no-op


def test_correction_never_resets_recommendation_or_model_linkage(tmp_path):
    """
    scripts/edgelab/build_recommendations.py backfills recommendationId/
    modelEvaluationId/modelSupported onto an already-logged bet, hours or
    days after entry. An unrelated correction must not silently null that
    linkage back out.
    """
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    _settle_in_place(
        path, rec["betId"], recommendationId="rec-999", modelEvaluationId="me-999", modelSupported=True,
    )

    corrected = _rec(stake=6.0)
    write_placed_bet(corrected, path=path, on_conflict="overwrite")
    row = list(storage.read_records(path))[0]
    assert row["stake"] == 6.0
    assert row["recommendationId"] == "rec-999"
    assert row["modelEvaluationId"] == "me-999"
    assert row["modelSupported"] is True


def test_correction_can_still_explicitly_override_a_linkage_field(tmp_path):
    """The preserve-if-not-supplied behavior must not prevent a genuine, explicit correction of a linkage field."""
    path = str(tmp_path / "bets.jsonl")
    rec = _rec(recommendation_id="rec-A")
    write_placed_bet(rec, path=path)

    override = _rec(recommendation_id="rec-B", stake=6.0)
    write_placed_bet(override, path=path, on_conflict="overwrite")
    row = list(storage.read_records(path))[0]
    assert row["recommendationId"] == "rec-B"


def test_new_bet_insert_path_is_unaffected_by_lifecycle_inheritance(tmp_path):
    """A brand-new betId has no existing row to inherit from -- must insert exactly as given."""
    path = str(tmp_path / "bets.jsonl")
    receipt = write_placed_bet(_rec(), path=path)
    assert receipt["duplicateStatus"] == "NEW"
    row = list(storage.read_records(path))[0]
    assert row["status"] == "pending"
    assert row["clv"] is None


# ---------------------------------------------------------------------------
# cancel_placed_bet -- previously CANCELLED was a documented schema value
# with no write function able to actually set it (found during the
# maintainer review of this milestone).
# ---------------------------------------------------------------------------

def test_cancel_placed_bet_marks_cancelled_without_touching_entry_fields(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)

    result = cancel_placed_bet(rec["betId"], "Logged in error, never actually placed", path=path)
    assert result["success"] is True
    assert result["recordStatus"] == "CANCELLED"

    row = list(storage.read_records(path))[0]
    assert row["recordStatus"] == "CANCELLED"
    assert row["recordStatusReason"] == "Logged in error, never actually placed"
    assert row["stake"] == 5.0  # untouched
    assert row["marketTicker"] == rec["marketTicker"]  # untouched
    assert schema.validate_record("placed_bet", row) == []


def test_cancel_placed_bet_is_idempotent():
    import tempfile
    path = tempfile.mktemp(suffix=".jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    r1 = cancel_placed_bet(rec["betId"], "reason A", path=path)
    r2 = cancel_placed_bet(rec["betId"], "reason A", path=path)
    assert r1["alreadyCancelled"] is False
    assert r2["alreadyCancelled"] is True
    rows = list(storage.read_records(path))
    assert len(rows) == 1  # never duplicated by re-cancelling
    os.remove(path)


def test_cancel_placed_bet_not_found_is_a_structured_failure_not_an_exception(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    result = cancel_placed_bet("bet-does-not-exist", "reason", path=path)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_cancel_placed_bet_requires_a_reason(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    try:
        cancel_placed_bet(rec["betId"], "", path=path)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_cancelled_bet_is_excluded_from_active_query():
    from lib.edgelab import query
    rec = dict(_rec())
    rec["recordStatus"] = "CANCELLED"
    assert query.active([rec]) == []


# ---------------------------------------------------------------------------
# confirm_realized_return / realized_bet_economics -- separates the
# OBJECTIVE contract settlement outcome (result/status, always
# independently derived by settlement) from the actual realized cash
# economics of a bet, for cases (e.g. a Kalshi partial-fill/fee-adjusted
# payout) this system's binary WIN/LOSS/PUSH/VOID model cannot represent.
# ---------------------------------------------------------------------------

def test_confirm_realized_return_never_touches_objective_settlement_fields(tmp_path):
    from lib.edgelab.bets import confirm_realized_return, realized_bet_economics

    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    bet_id = rec["betId"]

    # Simulate settlement: objectively a LOSS, $0 derived return.
    rows = list(storage.read_records(path))
    rows[0]["status"] = "settled"
    rows[0]["result"] = "LOSS"
    rows[0]["returnAmount"] = -5.0
    rows[0]["netProfitLoss"] = -5.0
    storage.write_all_records(path, rows)

    # A real Kalshi receipt shows a $1.45 partial return on the $5 stake.
    result = confirm_realized_return(bet_id, 1.45, -3.55, "MANUAL_POSTMORTEM_RECEIPT", "partial fill", path=path)
    assert result["success"] is True
    assert result["duplicateStatus"] == "NEW"

    row = list(storage.read_records(path))[0]
    assert row["result"] == "LOSS"  # objective outcome untouched
    assert row["status"] == "settled"
    assert row["returnAmount"] == -5.0  # derived settlement economics untouched
    assert row["netProfitLoss"] == -5.0
    assert row["confirmedReceiptReturn"] == 1.45
    assert row["confirmedReceiptNetProfitLoss"] == -3.55
    assert row["confirmedReceiptSource"] == "MANUAL_POSTMORTEM_RECEIPT"
    assert schema.validate_record("placed_bet", row) == []

    gross, net = realized_bet_economics(row)
    assert (gross, net) == (1.45, -3.55)  # confirmed receipt wins over the derived LOSS/-5.0


def test_realized_bet_economics_falls_back_to_derived_settlement_when_no_receipt(tmp_path):
    from lib.edgelab.bets import realized_bet_economics

    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    rows = list(storage.read_records(path))
    rows[0]["status"] = "settled"
    rows[0]["result"] = "WIN"
    rows[0]["netProfitLoss"] = 4.95
    storage.write_all_records(path, rows)

    row = list(storage.read_records(path))[0]
    gross, net = realized_bet_economics(row)
    assert (gross, net) == (round(5.0 + 4.95, 2), 4.95)  # ordinary derived economics, no confirmed receipt


def test_confirm_realized_return_is_idempotent(tmp_path):
    from lib.edgelab.bets import confirm_realized_return

    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    bet_id = rec["betId"]

    r1 = confirm_realized_return(bet_id, 1.45, -3.55, "MANUAL_POSTMORTEM_RECEIPT", path=path)
    r2 = confirm_realized_return(bet_id, 1.45, -3.55, "MANUAL_POSTMORTEM_RECEIPT", path=path)
    assert r1["duplicateStatus"] == "NEW"
    assert r2["duplicateStatus"] == "DUPLICATE_NOOP"
    rows = list(storage.read_records(path))
    assert len(rows) == 1  # never duplicated by re-confirming the identical receipt

    r3 = confirm_realized_return(bet_id, 2.00, -3.00, "MANUAL_POSTMORTEM_RECEIPT", "corrected", path=path)
    assert r3["duplicateStatus"] == "CORRECTED"
    row = list(storage.read_records(path))[0]
    assert row["confirmedReceiptReturn"] == 2.00
    assert row["confirmedReceiptNetProfitLoss"] == -3.00


def test_confirm_realized_return_unknown_bet_id_raises(tmp_path):
    from lib.edgelab.bets import confirm_realized_return
    path = str(tmp_path / "bets.jsonl")
    try:
        confirm_realized_return("no-such-bet", 1.0, -4.0, "MANUAL_POSTMORTEM_RECEIPT", path=path)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_confirm_realized_return_requires_a_source(tmp_path):
    from lib.edgelab.bets import confirm_realized_return
    path = str(tmp_path / "bets.jsonl")
    rec = _rec()
    write_placed_bet(rec, path=path)
    try:
        confirm_realized_return(rec["betId"], 1.0, -4.0, "", path=path)
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Near-duplicate detection against legacy-shaped timestamps (offset format,
# fractional seconds) -- found during the maintainer review that the
# strict "...Z"-only parser silently dropped all warning coverage for
# every legacy-ingested bet.
# ---------------------------------------------------------------------------

def test_near_duplicate_detection_handles_legacy_offset_timestamp_format(tmp_path):
    path = str(tmp_path / "bets.jsonl")
    legacy_rec = build_manual_bet_record(
        "KXMLBTEAMTOTAL-LEGACY", "x", 5.0, 0.505, "2026-06-17T22:45:46.170900+00:00",
    )
    write_placed_bet(legacy_rec, path=path)

    close_tranche = build_manual_bet_record(
        "KXMLBTEAMTOTAL-LEGACY", "x", 3.0, 0.51, "2026-06-17T22:46:00Z",
    )
    receipt = write_placed_bet(close_tranche, path=path)
    assert receipt["duplicateStatus"] == "NEW"
    assert len(receipt["nearDuplicateWarnings"]) == 1
