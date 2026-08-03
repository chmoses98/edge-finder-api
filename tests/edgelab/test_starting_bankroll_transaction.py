#!/usr/bin/env python3
"""
tests/edgelab/test_starting_bankroll_transaction.py
=======================================================
Coverage for the real canonical-era STARTING_BALANCE transaction
(data/edgelab/bankroll/transactions.jsonl, PR #41): recorded exactly
once, exactly $350.00, effective 2026-08-03, and idempotent on rerun --
running scripts/edgelab/record_bankroll_transaction.py again with the
same inputs must never create a second row.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEDGER_PATH = os.path.join(_REPO_ROOT, "data", "edgelab", "bankroll", "transactions.jsonl")
_SCRIPT_PATH = os.path.join(_REPO_ROOT, "scripts", "edgelab", "record_bankroll_transaction.py")


def _real_transactions():
    return list(storage.read_records(_LEDGER_PATH))


def test_exactly_one_transaction_in_the_real_ledger():
    rows = _real_transactions()
    assert len(rows) == 1


def test_the_one_transaction_is_the_expected_starting_balance():
    row = _real_transactions()[0]
    assert row["type"] == "STARTING_BALANCE"
    assert row["amount"] == 350.0
    assert row["occurredAt"] == "2026-08-03T00:00:00Z"
    assert row["reason"] == "Initial bankroll for canonical betting era"


def test_rerunning_the_record_script_is_a_deterministic_noop():
    before = _real_transactions()
    result = subprocess.run(
        [sys.executable, _SCRIPT_PATH,
         "--type", "STARTING_BALANCE", "--amount", "350.00",
         "--occurred-at", "2026-08-03T00:00:00Z",
         "--reason", "Initial bankroll for canonical betting era"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "DUPLICATE_NOOP" in result.stdout

    after = _real_transactions()
    assert after == before
    assert len(after) == 1


def test_transaction_id_is_stable_across_reruns():
    row = _real_transactions()[0]
    from lib.edgelab import ids
    expected_id = ids.build_bankroll_transaction_id(
        "STARTING_BALANCE", "2026-08-03T00:00:00Z", None,
    )
    assert row["transactionId"] == expected_id
