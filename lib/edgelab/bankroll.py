"""
lib/edgelab/bankroll.py
===========================
Canonical bankroll ledger (Canonical Placed-Bet Ledger milestone,
requirement 11). Tracking/observability only: this module never sizes a
stake, never feeds scripts/risk_gate.py or any production betting
decision, and never infers the user's real account balance from
recommendations or unsettled bets. See
tests/test_risk_gate_rule71_81_bankroll_absence.py, which guards that
scripts/risk_gate.py itself stays bankroll-free -- this module is
deliberately a separate, independent tracker.

Only five transaction TYPES are ever written directly as
BankrollTransaction rows: STARTING_BALANCE, DEPOSIT, WITHDRAWAL,
ADJUSTMENT, USER_REPORTED_BALANCE. Everything bet-driven (stake
reserved while pending, stake returned/realized P&L once settled) is
computed live from the PlacedBet ledger by compute_bankroll_summary
below -- never stored as its own transaction row -- so the two ledgers
can never drift out of sync with each other.

Four distinct numbers, each meaning something different (requirement
11's "clearly distinguish" list):
  - settledBankroll: manual cash transactions (starting balance +
    deposits - withdrawals + adjustments) plus realized P&L from every
    SETTLED, REAL-tracked bet. The ledger's own idea of "cash actually
    in hand right now."
  - totalExposure: stake currently at risk in PENDING, REAL-tracked
    bets. Not a loss -- just money not available to restake until the
    market settles.
  - availableBankroll: settledBankroll - totalExposure. What could be
    staked right now without going negative on paper.
  - userReportedBalance: the latest USER_REPORTED_BALANCE transaction's
    amount, verbatim, plus the delta against settledBankroll -- purely
    informational, for the user to reconcile against their own account
    view. NEVER substituted for settledBankroll/availableBankroll in any
    calculation.
"""

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION

_CASH_TYPES = {"STARTING_BALANCE", "DEPOSIT", "WITHDRAWAL", "ADJUSTMENT"}
_ALL_TYPES = _CASH_TYPES | {"USER_REPORTED_BALANCE"}


def build_bankroll_transaction(
    transaction_type, amount, occurred_at,
    *, reason=None, reference=None, entered_by=None, created_at=None,
):
    """
    Build one BankrollTransaction row. Does not write anything -- pass
    the result to write_bankroll_transaction(). Raises ValueError for a
    caller-programming-error type/reason combination (an ADJUSTMENT with
    no reason is a caller bug, not a routine validation outcome -- see
    write_placed_bet's parallel convention of raising only for
    programming errors, never for routine data issues).
    """
    if transaction_type not in _ALL_TYPES:
        raise ValueError(f"type must be one of {sorted(_ALL_TYPES)}, got {transaction_type!r}")
    if transaction_type == "ADJUSTMENT" and not reason:
        raise ValueError("ADJUSTMENT requires a reason")

    now = created_at or ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "transactionId": ids.build_bankroll_transaction_id(transaction_type, occurred_at, reference),
        "type": transaction_type,
        "amount": amount,
        "occurredAt": occurred_at,
        "reason": reason,
        "reference": reference,
        "enteredBy": entered_by,
        "createdAt": now,
        "updatedAt": None,
        "validationStatus": "valid",
        "provenance": {
            "sourceSystem": "manual_entry",
            "sourceFile": None,
            "sourceKey": None,
            "capturedAt": occurred_at,
            "ingestedAt": now,
        },
    }


def write_bankroll_transaction(record, *, path=None):
    """
    Canonical write path for a BankrollTransaction -- append-only (a
    transaction is an immutable event, never revised in place; a mistaken
    entry gets a compensating ADJUSTMENT, never an edit). Idempotent by
    transactionId: retrying the exact same submission is a no-op, not a
    duplicate row. Deliberately mirrors lib.edgelab.bets.write_placed_bet's
    "one canonical write function per entity" convention, but simpler
    (append-only, no conflict/correction path) since transactions are
    never revised.
    """
    from lib.edgelab import schema, storage

    path = path or storage.singleton_path("bankroll", "transactions.jsonl")
    errors = schema.validate_record("bankroll_transaction", record)
    if errors:
        return {"success": False, "transactionId": record.get("transactionId"), "errors": errors, "written": False}

    written, skipped = storage.append_records(path, [record], "transactionId")
    return {
        "success": True,
        "transactionId": record["transactionId"],
        "errors": [],
        "written": written == 1,
        "duplicateStatus": "NEW" if written == 1 else "DUPLICATE_NOOP",
    }


def compute_bankroll_summary(transactions, bets, *, as_of=None):
    """
    Pure aggregation over already-loaded lists -- no file I/O, directly
    unit-testable. `bets` should be every REAL-tracked PlacedBet the
    caller wants counted (typically all of them; trackingType PAPER/
    REAL_PROBE bets are excluded from every dollar total here so paper
    trading never pollutes real bankroll numbers -- see PlacedBet.
    trackingType's own field description).
    """
    real_bets = [b for b in bets if b.get("trackingType") in (None, "REAL")]
    active_cash_txns = [t for t in transactions if t["type"] in _CASH_TYPES]

    cash_total = 0.0
    for t in active_cash_txns:
        amt = t.get("amount")
        if amt is None:
            continue
        cash_total += -abs(amt) if t["type"] == "WITHDRAWAL" else amt

    realized_pnl = sum(
        b["netProfitLoss"] for b in real_bets
        if b.get("status") == "settled" and b.get("netProfitLoss") is not None
    )
    settled_bankroll = round(cash_total + realized_pnl, 2)

    total_exposure = round(sum(
        b["stake"] for b in real_bets
        if b.get("status") == "pending" and b.get("stake") is not None
    ), 2)

    available_bankroll = round(settled_bankroll - total_exposure, 2)

    user_reported = None
    for t in sorted(
        (t for t in transactions if t["type"] == "USER_REPORTED_BALANCE"),
        key=lambda t: t.get("occurredAt") or "",
    ):
        user_reported = t

    return {
        "asOf": as_of or ids.utc_now_iso(),
        "settledBankroll": settled_bankroll,
        "totalExposure": total_exposure,
        "availableBankroll": available_bankroll,
        "userReportedBalance": user_reported.get("amount") if user_reported else None,
        "userReportedBalanceAsOf": user_reported.get("occurredAt") if user_reported else None,
        "userReportedDelta": (
            round(user_reported["amount"] - settled_bankroll, 2)
            if user_reported and user_reported.get("amount") is not None else None
        ),
        "pendingRealBetCount": sum(1 for b in real_bets if b.get("status") == "pending"),
        "settledRealBetCount": sum(1 for b in real_bets if b.get("status") == "settled"),
        "cashTransactionCount": len(active_cash_txns),
    }
