#!/usr/bin/env python3
"""
tests/edgelab/test_bankroll.py
==================================
Coverage for lib/edgelab/bankroll.py -- Canonical Placed-Bet Ledger
milestone, requirement 11. Deliberately does NOT test any staking/sizing
behavior (there is none here -- see the module docstring and
tests/test_risk_gate_rule71_81_bankroll_absence.py, which guards that
scripts/risk_gate.py itself stays bankroll-free).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage
from lib.edgelab.bankroll import build_bankroll_transaction, compute_bankroll_summary, write_bankroll_transaction


def test_build_starting_balance_validates():
    t = build_bankroll_transaction("STARTING_BALANCE", 1000.0, "2026-06-01T00:00:00Z")
    assert schema.validate_record("bankroll_transaction", t) == []


def test_adjustment_without_reason_raises():
    try:
        build_bankroll_transaction("ADJUSTMENT", -5.0, "2026-08-01T00:00:00Z")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_adjustment_with_reason_is_valid():
    t = build_bankroll_transaction("ADJUSTMENT", -5.0, "2026-08-01T00:00:00Z", reason="Kalshi fee correction")
    assert schema.validate_record("bankroll_transaction", t) == []


def test_unknown_type_raises():
    try:
        build_bankroll_transaction("BOGUS", 1.0, "2026-08-01T00:00:00Z")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_write_is_idempotent_on_retry(tmp_path):
    path = str(tmp_path / "transactions.jsonl")
    t = build_bankroll_transaction("DEPOSIT", 100.0, "2026-08-01T00:00:00Z")
    r1 = write_bankroll_transaction(t, path=path)
    assert r1["duplicateStatus"] == "NEW"
    r2 = write_bankroll_transaction(t, path=path)
    assert r2["duplicateStatus"] == "DUPLICATE_NOOP"
    rows = list(storage.read_records(path))
    assert len(rows) == 1


def test_summary_distinguishes_settled_exposure_available_and_user_reported():
    transactions = [
        build_bankroll_transaction("STARTING_BALANCE", 1000.0, "2026-06-01T00:00:00Z"),
        build_bankroll_transaction("DEPOSIT", 200.0, "2026-07-01T00:00:00Z"),
        build_bankroll_transaction("WITHDRAWAL", 50.0, "2026-07-15T00:00:00Z"),
        build_bankroll_transaction("USER_REPORTED_BALANCE", 1130.0, "2026-08-01T00:00:00Z"),
    ]
    bets = [
        {"trackingType": "REAL", "status": "settled", "netProfitLoss": 20.0, "stake": 10},
        {"trackingType": "REAL", "status": "pending", "stake": 15.0, "netProfitLoss": None},
        {"trackingType": "PAPER", "status": "settled", "netProfitLoss": 500.0, "stake": 5},
    ]
    summary = compute_bankroll_summary(transactions, bets)
    assert summary["settledBankroll"] == 1170.0  # 1000+200-50+20, paper excluded
    assert summary["totalExposure"] == 15.0
    assert summary["availableBankroll"] == 1155.0
    assert summary["userReportedBalance"] == 1130.0
    assert summary["userReportedDelta"] == round(1130.0 - 1170.0, 2)


def test_unsettled_bets_are_exposure_never_loss_or_gain():
    """Requirement 11: never infer the real account balance from unsettled bets -- a pending bet only reduces availability, never settledBankroll."""
    transactions = [build_bankroll_transaction("STARTING_BALANCE", 500.0, "2026-06-01T00:00:00Z")]
    bets = [{"trackingType": "REAL", "status": "pending", "stake": 100.0, "netProfitLoss": None}]
    summary = compute_bankroll_summary(transactions, bets)
    assert summary["settledBankroll"] == 500.0
    assert summary["totalExposure"] == 100.0
    assert summary["availableBankroll"] == 400.0


def test_no_transactions_or_bets_gives_zero_summary():
    summary = compute_bankroll_summary([], [])
    assert summary["settledBankroll"] == 0.0
    assert summary["totalExposure"] == 0.0
    assert summary["availableBankroll"] == 0.0
    assert summary["userReportedBalance"] is None


def test_cancelled_bet_never_counts_toward_exposure_or_settled_bankroll():
    """
    Maintainer review regression: a CANCELLED bet (logged in error) must
    never count toward totalExposure or settledBankroll, exactly like
    build_postmortem and lib.edgelab.query.active() already exclude it.
    """
    transactions = [build_bankroll_transaction("STARTING_BALANCE", 100.0, "2026-06-01T00:00:00Z")]
    bets = [
        {"trackingType": "REAL", "status": "pending", "stake": 500.0, "netProfitLoss": None, "recordStatus": "CANCELLED"},
        {"trackingType": "REAL", "status": "settled", "stake": 50.0, "netProfitLoss": 1000.0, "recordStatus": "CANCELLED"},
    ]
    summary = compute_bankroll_summary(transactions, bets)
    assert summary["totalExposure"] == 0.0
    assert summary["settledBankroll"] == 100.0


def test_null_record_status_is_treated_as_active_not_cancelled():
    """A pre-existing row written before recordStatus existed (None, not "ACTIVE") must still count normally."""
    transactions = [build_bankroll_transaction("STARTING_BALANCE", 100.0, "2026-06-01T00:00:00Z")]
    bets = [{"trackingType": "REAL", "status": "pending", "stake": 25.0, "netProfitLoss": None, "recordStatus": None}]
    summary = compute_bankroll_summary(transactions, bets)
    assert summary["totalExposure"] == 25.0


def test_negative_deposit_withdrawal_or_starting_balance_raises():
    """
    Maintainer review regression: a negative DEPOSIT/WITHDRAWAL/
    STARTING_BALANCE previously passed schema validation and silently
    reduced settledBankroll under a label that claims to increase it.
    """
    for t in ("DEPOSIT", "WITHDRAWAL", "STARTING_BALANCE"):
        try:
            build_bankroll_transaction(t, -50.0, "2026-08-01T00:00:00Z")
            assert False, f"expected ValueError for negative {t}"
        except ValueError:
            pass


def test_negative_adjustment_is_still_allowed():
    t = build_bankroll_transaction("ADJUSTMENT", -5.0, "2026-08-01T00:00:00Z", reason="fee correction")
    assert t["amount"] == -5.0


def test_paper_and_probe_bets_never_affect_real_totals():
    transactions = [build_bankroll_transaction("STARTING_BALANCE", 100.0, "2026-06-01T00:00:00Z")]
    bets = [
        {"trackingType": "PAPER", "status": "settled", "netProfitLoss": 1000.0, "stake": 1},
        {"trackingType": "REAL_PROBE", "status": "pending", "stake": 1000.0, "netProfitLoss": None},
    ]
    summary = compute_bankroll_summary(transactions, bets)
    assert summary["settledBankroll"] == 100.0
    assert summary["totalExposure"] == 0.0
