#!/usr/bin/env python3
"""
tests/test_production_health_gate.py
====================================
REMEDIATION WAVE 0, section C. Tests for the operational health gate.

The single most important property under test is NEGATIVE: the gate must go
RED for the exact state the system was in from 2026-09-02 to 2026-09-07, when
`corpus-health-check.yml` reported success every single day. A health check
that cannot fail is not a health check, so every assertion below drives the
pure evaluator with a hand-built state and checks BOTH directions -- the
healthy state passes and the outage state fails.

`evaluate_health` is a pure function of a state dict, which is what makes this
possible without a filesystem, a network, or a clock.
"""

import os
import sys
from datetime import datetime, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "ci"))

import production_health_gate as G  # noqa: E402

NOW = datetime(2026, 9, 7, 13, 0, 0, tzinfo=timezone.utc)


def _state(**overrides):
    """A fully healthy baseline; each test perturbs exactly one dimension."""
    base = {
        "now": NOW,
        "root": ROOT,
        "slateDate": "2026-09-06",
        "settlementLatest": "2026-09-06",
        "recommendationLatest": "2026-09-06",
        "modelEvaluationLatest": "2026-09-06",
        "researchHeartbeatLatest": "2026-09-06",
        "betsLedgerCommitDate": "2026-09-07",
        "bets": [],
        "acknowledgedUnresolvableBetIds": set(),
    }
    base.update(overrides)
    return base


def _by_id(results):
    return {r["id"]: r for r in results}


def _verdict(state):
    return G.summarize(G.evaluate_health(state))


# ── the baseline must be green, or every other test is meaningless ───────────

def test_healthy_state_is_green():
    verdict = _verdict(_state())
    assert verdict["overall"] == "HEALTHY", verdict


# ── THE regression: the actual 2026-09 outage state must be RED ──────────────

def test_the_actual_september_outage_state_is_red():
    """
    Reproduces the durable state on 2026-09-07 exactly as it stood on main:
    slate pipeline producing daily, model evaluations current, settlements and
    recommendations frozen at 2026-08-31, ledger uncommitted since 09-02, 156
    unsettled bets. corpus-health-check.yml called this SUCCESS.
    """
    state = _state(
        settlementLatest="2026-08-31",
        recommendationLatest="2026-08-31",
        modelEvaluationLatest="2026-09-06",
        betsLedgerCommitDate="2026-09-02",
        bets=[{"id": "b%d" % i, "date": "2026-08-20", "result": None, "status": "pending"}
              for i in range(156)],
    )
    results = G.evaluate_health(state)
    verdict = G.summarize(results)
    assert verdict["overall"] == "CRITICAL"
    for assertion_id in ("PROD-2", "PROD-3", "PROD-4", "PROD-5", "PROD-7", "PROD-8"):
        assert assertion_id in verdict["criticalFailureIds"], (
            "%s should have fired for the September outage state" % assertion_id)


def test_blind_but_green_is_named_explicitly():
    """
    PROD-8 exists so the report NAMES the pathology rather than leaving a human
    to notice that PROD-1 passed while PROD-2 failed.
    """
    result = _by_id(G.evaluate_health(_state(settlementLatest="2026-08-31")))["PROD-8"]
    assert result["status"] == G.FAIL
    assert "BLIND BUT GREEN" in result["summary"]


# ── individual assertions, each in both directions ───────────────────────────

@pytest.mark.parametrize("assertion_id,field,stale_value", [
    ("PROD-2", "settlementLatest", "2026-08-31"),
    ("PROD-3", "recommendationLatest", "2026-08-31"),
    ("PROD-6", "modelEvaluationLatest", "2026-08-31"),
])
def test_freshness_assertions_fire_when_stale_and_pass_when_current(
        assertion_id, field, stale_value):
    fresh = _by_id(G.evaluate_health(_state()))[assertion_id]
    assert fresh["status"] == G.PASS
    stale = _by_id(G.evaluate_health(_state(**{field: stale_value})))[assertion_id]
    assert stale["status"] == G.FAIL


def test_skip_cascade_detected_from_partition_divergence():
    """
    PROD-4 detects edgelab-postgame.yml being skipped WITHOUT the Actions API,
    by noticing predictions advancing while outcomes do not.
    """
    result = _by_id(G.evaluate_health(_state(
        modelEvaluationLatest="2026-09-06", settlementLatest="2026-08-31")))["PROD-4"]
    assert result["status"] == G.FAIL
    assert result["detail"]["divergenceDays"] == 6


def test_skip_cascade_not_reported_when_both_sides_lag_together():
    """
    A shared upstream pause is PROD-6's business, not PROD-4's. PROD-4 must not
    double-report it as a cascade, or every outage produces two alarms and the
    specific cascade signal loses its meaning.
    """
    state = _state(modelEvaluationLatest="2026-09-01", settlementLatest="2026-09-01")
    results = _by_id(G.evaluate_health(state))
    assert results["PROD-4"]["status"] == G.PASS
    assert results["PROD-6"]["status"] == G.FAIL


def test_ledger_persistence_failure_is_detected():
    """
    PROD-5 catches the outage's true signature -- work computed then discarded
    because the commit step never ran.
    """
    assert _by_id(G.evaluate_health(_state(betsLedgerCommitDate="2026-09-02")))["PROD-5"]["status"] == G.FAIL
    assert _by_id(G.evaluate_health(_state()))["PROD-5"]["status"] == G.PASS


# ── the backlog assertion must not be permanently red ────────────────────────

def test_backlog_ignores_rows_acknowledged_as_legitimately_unresolvable():
    """
    A permanent, understood residue (NRFI/YRFI with no automated settlement
    path, rows predating the settlement workflow) must not hold the gate red
    forever -- a gate that is always red is a gate everyone ignores, and a new
    genuine backlog would hide behind it.
    """
    bets = [{"id": "b%d" % i, "date": "2026-06-01", "result": None, "status": "pending"}
            for i in range(40)]
    unacknowledged = _by_id(G.evaluate_health(_state(bets=bets)))["PROD-7"]
    assert unacknowledged["status"] == G.FAIL

    acknowledged = _by_id(G.evaluate_health(_state(
        bets=bets,
        acknowledgedUnresolvableBetIds={b["id"] for b in bets})))["PROD-7"]
    assert acknowledged["status"] == G.PASS
    assert acknowledged["detail"]["unexplainedBacklog"] == 0


def test_backlog_still_fires_for_unacknowledged_rows_hiding_behind_acknowledged_ones():
    """The acknowledgement list must never become a blanket amnesty."""
    acked = [{"id": "old%d" % i, "date": "2026-06-01", "result": None, "status": "pending"}
             for i in range(40)]
    new = [{"id": "new%d" % i, "date": "2026-08-25", "result": None, "status": "pending"}
           for i in range(30)]
    result = _by_id(G.evaluate_health(_state(
        bets=acked + new,
        acknowledgedUnresolvableBetIds={b["id"] for b in acked})))["PROD-7"]
    assert result["status"] == G.FAIL
    assert result["detail"]["unexplainedBacklog"] == 30


def test_recent_bets_are_not_counted_as_backlog():
    """Bets inside the grace window have not had a settlement pass yet."""
    bets = [{"id": "b%d" % i, "date": "2026-09-06", "result": None, "status": "pending"}
            for i in range(100)]
    assert _by_id(G.evaluate_health(_state(bets=bets)))["PROD-7"]["status"] == G.PASS


# ── noise control ────────────────────────────────────────────────────────────

def test_offseason_suspends_freshness_assertions_instead_of_screaming():
    """
    With no recent slate there are no games to settle. Every downstream
    freshness assertion must report NOT_APPLICABLE rather than firing daily --
    otherwise the gate is red all winter and is worthless by spring.
    """
    # Dates well in the PAST of NOW (2026-09-07) and beyond
    # INACTIVE_PIPELINE_DAYS -- a future date would read as zero lag and pass
    # for the wrong reason, which would make this test pass vacuously.
    results = _by_id(G.evaluate_health(_state(
        slateDate="2026-07-01",
        settlementLatest="2026-07-01",
        recommendationLatest="2026-07-01",
        modelEvaluationLatest="2026-07-01",
        betsLedgerCommitDate="2026-07-01",
    )))
    for assertion_id in ("PROD-1", "PROD-2", "PROD-3", "PROD-4", "PROD-5", "PROD-6", "PROD-8"):
        assert results[assertion_id]["status"] == G.NOT_APPLICABLE, assertion_id
    assert G.summarize(list(results.values()))["overall"] == "HEALTHY"


def test_research_degradation_can_never_fail_the_gate():
    """
    Design principle 2. Research capture is the healthiest part of this system;
    letting it page the operator is exactly the noise that trains people to
    ignore a gate.
    """
    results = G.evaluate_health(_state(researchHeartbeatLatest="2026-07-01"))
    heartbeat = _by_id(results)["RSCH-1"]
    assert heartbeat["severity"] == G.RESEARCH_DEGRADATION
    assert heartbeat["status"] == G.FAIL
    verdict = G.summarize(results)
    assert verdict["overall"] == "HEALTHY"
    assert verdict["researchDegradationCount"] == 1


def test_a_single_late_run_does_not_fire_the_gate():
    """
    One-day lag is normal: settlement targets 'yesterday ET'. The gate must
    tolerate a single late or retried run and only fire on a second consecutive
    miss, or it will be re-run to green out of habit.
    """
    assert _verdict(_state(
        settlementLatest="2026-09-05",
        recommendationLatest="2026-09-05",
    ))["overall"] == "HEALTHY"


# ── exit-code contract ───────────────────────────────────────────────────────

def test_every_assertion_carries_a_severity_and_a_summary():
    for result in G.evaluate_health(_state()):
        assert result["severity"] in (G.CRITICAL_PRODUCTION, G.RESEARCH_DEGRADATION)
        assert result["status"] in (G.PASS, G.FAIL, G.NOT_APPLICABLE)
        assert result["summary"], result["id"]
        assert result["id"]
