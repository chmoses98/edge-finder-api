#!/usr/bin/env python3
"""
tests/test_bankroll_context.py
==================================
Bankroll context: where the number comes from, how old it is, and whether
it may be staked against.

The rules being pinned:
  * a router-published artifact wins when one exists;
  * otherwise this repository's EXISTING canonical ledger is used -- no
    second bankroll authority is created;
  * no literal dollar amount is ever hard-coded as a fallback;
  * a stale or unresolvable value is explicit and disables sizing;
  * a human-typed "user reported balance" can never become the sizing
    basis.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import bankroll_context as bc  # noqa: E402

NOW = datetime(2026, 9, 17, 18, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── source 1: a router-published artifact wins ──────────────────────────

def test_a_fresh_router_artifact_is_used_and_allows_sizing(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps({
        "bankroll": 1234.56, "observedAt": _iso(NOW - timedelta(hours=2)),
        "valueType": bc.VALUE_OBSERVED_BALANCE,
    }))
    ctx = bc.load_bankroll_context(now=NOW, router_path=str(path))
    assert ctx["bankroll"] == 1234.56
    assert ctx["source"] == bc.SOURCE_ROUTER
    assert ctx["valueType"] == bc.VALUE_OBSERVED_BALANCE
    assert ctx["status"] == bc.STATUS_FRESH
    assert ctx["sizingAllowed"] is True


def test_a_router_artifact_is_preferred_over_the_local_ledger(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps({"balance": 900.0, "observedAt": _iso(NOW - timedelta(minutes=5))}))
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(path),
        transactions=[{"type": "STARTING_BALANCE", "amount": 1.0, "occurredAt": _iso(NOW)}],
        bets=[])
    assert ctx["source"] == bc.SOURCE_ROUTER
    assert ctx["bankroll"] == 900.0


def test_a_stale_router_artifact_is_labelled_stale_and_blocks_sizing(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps({"bankroll": 500.0, "observedAt": _iso(NOW - timedelta(hours=72))}))
    ctx = bc.load_bankroll_context(now=NOW, router_path=str(path))
    assert ctx["status"] == bc.STATUS_STALE
    assert ctx["sizingAllowed"] is False
    assert "older than the" in ctx["unavailableReason"]
    # The value is still SHOWN -- a handicap may proceed; only staking stops.
    assert ctx["bankroll"] == 500.0


def test_a_router_artifact_with_no_timestamp_cannot_be_trusted(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps({"bankroll": 500.0}))
    ctx = bc.load_bankroll_context(now=NOW, router_path=str(path))
    assert ctx["status"] == bc.STATUS_UNAVAILABLE
    assert ctx["bankroll"] is None
    assert "age cannot be judged" in ctx["unavailableReason"]


def test_a_future_dated_observation_is_refused(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps({"bankroll": 500.0, "observedAt": _iso(NOW + timedelta(hours=9))}))
    ctx = bc.load_bankroll_context(now=NOW, router_path=str(path))
    assert ctx["status"] == bc.STATUS_UNAVAILABLE
    assert ctx["bankroll"] is None


@pytest.mark.parametrize("body", ['not json', '[1,2,3]', '{"observedAt": "2026-09-17T17:00:00Z"}',
                                  '{"bankroll": "lots", "observedAt": "2026-09-17T17:00:00Z"}'])
def test_a_malformed_router_artifact_falls_back_with_a_reason(tmp_path, body):
    path = tmp_path / "bankroll.json"
    path.write_text(body)
    artifact, reason = bc.load_router_artifact(str(path))
    assert artifact is None
    assert reason

    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(path),
        transactions=[{"type": "STARTING_BALANCE", "amount": 350.0, "occurredAt": _iso(NOW)}],
        bets=[])
    assert ctx["source"] == bc.SOURCE_EDGELAB_LEDGER
    assert ctx["detail"]["routerArtifactReason"]


def test_a_missing_router_artifact_reports_why_the_router_has_none(tmp_path):
    artifact, reason = bc.load_router_artifact(str(tmp_path / "nope.json"))
    assert artifact is None
    assert "/portfolio/balance" in reason
    assert "privacy" in reason


# ── source 2: the EXISTING canonical ledger ─────────────────────────────

def test_the_local_fallback_reuses_the_existing_canonical_ledger(tmp_path):
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(tmp_path / "absent.json"),
        transactions=[{"type": "STARTING_BALANCE", "amount": 1000.0,
                       "occurredAt": _iso(NOW - timedelta(hours=1))}],
        bets=[{"trackingType": "REAL", "status": "pending", "stake": 100.0,
               "updatedAt": _iso(NOW - timedelta(minutes=30))}])
    assert ctx["source"] == bc.SOURCE_EDGELAB_LEDGER
    assert ctx["valueType"] == bc.VALUE_DERIVED_LEDGER
    # availableBankroll = settled (1000) - exposure (100)
    assert ctx["bankroll"] == 900.0
    assert ctx["detail"]["sizingField"] == "availableBankroll"
    assert ctx["status"] == bc.STATUS_FRESH


def test_the_derived_value_is_labelled_as_derived_not_an_account_balance(tmp_path):
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(tmp_path / "absent.json"),
        transactions=[{"type": "STARTING_BALANCE", "amount": 100.0, "occurredAt": _iso(NOW)}],
        bets=[])
    assert ctx["valueType"] == bc.VALUE_DERIVED_LEDGER
    assert "not an observed" in ctx["detail"]["note"]


def test_a_stale_ledger_blocks_sizing(tmp_path):
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(tmp_path / "absent.json"),
        transactions=[{"type": "STARTING_BALANCE", "amount": 500.0,
                       "occurredAt": _iso(NOW - timedelta(days=9))}],
        bets=[])
    assert ctx["status"] == bc.STATUS_STALE
    assert ctx["sizingAllowed"] is False


def test_an_empty_ledger_is_unavailable_never_a_hardcoded_number(tmp_path):
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(tmp_path / "absent.json"), transactions=[], bets=[])
    assert ctx["status"] == bc.STATUS_UNAVAILABLE
    assert ctx["bankroll"] is None
    assert ctx["sizingAllowed"] is False


def test_a_user_reported_balance_is_never_the_sizing_basis(tmp_path):
    """A human typing what they think their balance is must not become
    canonical."""
    ctx = bc.load_bankroll_context(
        now=NOW, router_path=str(tmp_path / "absent.json"),
        transactions=[
            {"type": "STARTING_BALANCE", "amount": 100.0, "occurredAt": _iso(NOW)},
            {"type": "USER_REPORTED_BALANCE", "amount": 9999.0, "occurredAt": _iso(NOW)},
        ],
        bets=[])
    assert ctx["bankroll"] == 100.0, "the user-reported number leaked into the sizing basis"
    assert ctx["userReportedBalance"] == 9999.0
    assert ctx["userReportedBalanceIsInformationalOnly"] is True


# ── invariants ──────────────────────────────────────────────────────────

def test_no_dollar_amount_is_hardcoded_anywhere_in_the_module():
    """A literal bankroll fallback is the one thing this module must never
    contain."""
    import re
    source = open(os.path.join(ROOT, "lib", "bankroll_context.py")).read()
    code = "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#")
    ).split('"""')
    # Keep only the executable segments (docstrings sit at odd indices).
    executable = "".join(seg for index, seg in enumerate(code) if index % 2 == 0)
    money_literals = re.findall(r"(?<![\w.])\d+\.\d{2}(?![\w])", executable)
    assert money_literals == [], f"hard-coded money literal(s) found: {money_literals}"


def test_describe_for_output_always_names_the_bankroll_used():
    fresh = bc.build_context(bankroll=750.0, source=bc.SOURCE_ROUTER,
                             value_type=bc.VALUE_OBSERVED_BALANCE,
                             observed_at=_iso(NOW - timedelta(hours=1)), now=NOW)
    assert "750.00" in bc.describe_for_output(fresh)
    assert "sizing allowed" in bc.describe_for_output(fresh)

    stale = bc.build_context(bankroll=750.0, source=bc.SOURCE_EDGELAB_LEDGER,
                             value_type=bc.VALUE_DERIVED_LEDGER,
                             observed_at=_iso(NOW - timedelta(days=4)), now=NOW)
    assert "STALE" in bc.describe_for_output(stale)
    assert "do NOT size" in bc.describe_for_output(stale)

    gone = bc.build_context(bankroll=None, source=bc.SOURCE_EDGELAB_LEDGER,
                            value_type=bc.VALUE_DERIVED_LEDGER, observed_at=None, now=NOW)
    assert "UNAVAILABLE" in bc.describe_for_output(gone)
    assert "NOT current" in bc.describe_for_output(gone)


def test_retrieval_never_writes_anything(tmp_path, monkeypatch):
    """Bankroll retrieval is read-only -- it must not mutate this repo and
    cannot reach the router repo at all."""
    monkeypatch.chdir(tmp_path)
    before = sorted(os.listdir(tmp_path))
    bc.load_bankroll_context(now=NOW, transactions=[], bets=[])
    assert sorted(os.listdir(tmp_path)) == before


def test_the_real_repository_resolves_a_context_with_an_explicit_status():
    """Against the real committed ledger: whatever the answer is, it must
    carry a status and never silently claim freshness."""
    ctx = bc.load_bankroll_context()
    assert ctx["status"] in (bc.STATUS_FRESH, bc.STATUS_STALE, bc.STATUS_UNAVAILABLE)
    assert ctx["sizingAllowed"] is (ctx["status"] == bc.STATUS_FRESH)
    if ctx["status"] != bc.STATUS_FRESH:
        assert ctx["unavailableReason"]
