#!/usr/bin/env python3
"""
tests/test_wave0_07_prod7_backlog_truth.py
==========================================
WAVE 0.07. Guards the truthfulness of health-gate assertion PROD-7.

WHAT THE AUDIT OF ALL 86 COUNTED ROWS FOUND
-------------------------------------------
PROD-7 counted 86 rows. Individually classified against committed evidence:

    37  a canonical EdgeLab record already holds a TERMINAL result while the
        root ledger still says non-terminal -- a lifecycle propagation gap
    45  no settlement partition exists for the game date at all (the corpus
        begins 2026-08-02; these are June/July games) -- missing evidence
     4  no canonical counterpart matched -- needs human review

So PROD-7's red is LEGITIMATE, not a false alarm: every one of the 86 is
genuinely unexplained backlog. It is not silenced here, and the threshold is
not touched.

THE ROOT CAUSE OF THE MIS-CLASSIFICATION
----------------------------------------
`lib.bet_backlog_classifier.classify_bet` called
`find_local_settlement_evidence(bet)` with no `settlement_index`, so
CATEGORY_SETTLEABLE_FROM_EVIDENCE was structurally unreachable and every
otherwise-healthy row fell through to the coarse CATEGORY_REQUIRES_MANUAL_REVIEW
bucket. That function's own docstring said the repository had no post-game
score archive and that a later milestone would supply one. The restore made
that premise false; nothing had wired it up.

THE LATENT HAZARD FIXED ALONGSIDE
---------------------------------
30 root-ledger rows carry no `id`. The gate acknowledged rows by `betId`, so a
single acknowledged id-less row would have put `None` into the acknowledged
set, and `bet.get("id") in acknowledged` would then have silently excluded ALL
30 id-less rows at once. Fixed by refusing falsy ids, which can only make the
gate stricter.
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
from lib import bet_backlog_classifier as C  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_audit_settlement_backlog", os.path.join(ROOT, "scripts", "audit_settlement_backlog.py"))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

NOW = datetime(2026, 9, 9, 13, 0, 0, tzinfo=timezone.utc)
TODAY = "2026-09-09"


def _state(**overrides):
    base = {
        "now": NOW,
        "root": ROOT,
        "slateDate": "2026-09-08",
        "settlementLatest": "2026-09-08",
        "recommendationLatest": "2026-09-08",
        "modelEvaluationLatest": "2026-09-08",
        "researchHeartbeatLatest": "2026-09-08",
        "betsLedgerCommitDate": "2026-09-09",
        "bets": [],
        "acknowledgedUnresolvableBetIds": set(),
    }
    base.update(overrides)
    return base


def _prod7(state):
    return {r["id"]: r for r in G.evaluate_health(state)}["PROD-7"]


def _root_bet(bet_id="r1", date="2026-06-20", game="STL @ LAD", market="ML Away"):
    return {"id": bet_id, "date": date, "game": game, "market": market, "result": None}


def _canonical(result="LOSS", date="2026-06-20", matchup="STL @ LAD",
               family="ML_AWAY", bet_id="c1"):
    return {"betId": bet_id, "result": result, "gameDate": date,
            "matchup": matchup, "marketFamily": family}


# ── the settlement index: the fix, and what it must never do ─────────────────

def test_settleable_from_evidence_was_unreachable_without_an_index():
    """The defect itself: no index means the category can never be produced."""
    bet = _root_bet()
    assert C.classify_bet(bet, TODAY) == C.CATEGORY_REQUIRES_MANUAL_REVIEW


def test_an_index_makes_settleable_from_evidence_reachable():
    bet = _root_bet()
    index = A.build_settlement_index([bet], [_canonical()])
    assert index, "a terminal canonical counterpart must produce evidence"
    assert C.classify_bet(bet, TODAY, settlement_index=index) == \
        C.CATEGORY_SETTLEABLE_FROM_EVIDENCE


def test_a_non_terminal_canonical_row_is_never_treated_as_evidence():
    """
    The safety property. An unsettled canonical row proves nothing; indexing it
    would manufacture a settlement out of thin air.
    """
    for result in (None, "", "PENDING", "OPEN"):
        index = A.build_settlement_index([_root_bet()], [_canonical(result=result)])
        assert index == {}, "result=%r must not count as settlement evidence" % result


def test_evidence_requires_the_same_game_and_market():
    """Matching reuses the reconciler's key; a different game must not match."""
    bet = _root_bet(date="2026-06-20", game="STL @ LAD")
    assert A.build_settlement_index([bet], [_canonical(date="2026-06-21")]) == {}
    assert A.build_settlement_index([bet], [_canonical(matchup="NYY @ BOS")]) == {}
    assert A.build_settlement_index([bet], [_canonical(family="TT_OVER")]) == {}


def test_id_less_rows_are_never_indexed():
    """
    30 root rows have no id. The index is keyed by id, so guessing a key for
    them could attach another row's outcome to them.
    """
    bet = _root_bet()
    del bet["id"]
    assert A.build_settlement_index([bet], [_canonical()]) == {}
    assert C.find_local_settlement_evidence(bet, {None: {"x": 1}}) is None


def test_classify_bet_is_unchanged_when_no_index_is_supplied():
    """Back-compatibility: every existing caller keeps its exact behavior."""
    for bet, expected in (
        ({"id": "a", "date": "2026-05-01", "game": "STL @ LAD", "market": "ML Away"},
         C.CATEGORY_MISSING_SOURCE_DATA),
        ({"id": "b", "date": "2026-06-20", "game": "STL @ LAD", "market": "NRFI"},
         C.CATEGORY_UNSUPPORTED_MARKET_FAMILY),
        ({"id": "c", "date": "2026-06-20", "game": "nonsense", "market": "ML Away"},
         C.CATEGORY_MALFORMED_RECORD),
    ):
        assert C.classify_bet(bet, TODAY) == expected


# ── the acknowledgement hazard ───────────────────────────────────────────────

def test_a_falsy_bet_id_never_enters_the_acknowledged_set(tmp_path):
    """
    The mass-acknowledge hazard. One acknowledged id-less row must not silence
    every id-less row in the ledger.
    """
    import json
    health = tmp_path / "data" / "edgelab" / "operational_health"
    health.mkdir(parents=True)
    (health / "settlement_backlog_classification.json").write_text(json.dumps({
        "acknowledgedClassifications": ["EXPECTED_UNRESOLVED"],
        "rows": [
            {"betId": None, "classification": "EXPECTED_UNRESOLVED"},
            {"betId": "",   "classification": "EXPECTED_UNRESOLVED"},
            {"betId": "real-1", "classification": "EXPECTED_UNRESOLVED"},
        ],
    }))
    (tmp_path / "bets.json").write_text("[]")
    state = G.collect_state(root=str(tmp_path), now=NOW)
    acknowledged = state["acknowledgedUnresolvableBetIds"]
    assert None not in acknowledged and "" not in acknowledged
    assert "real-1" in acknowledged


def test_id_less_backlog_rows_still_count_toward_prod7():
    """
    Consequence of the fix, asserted directly: rows without an id can never be
    excluded, so they always keep the gate honest.
    """
    bets = [{"id": None, "date": "2026-06-20", "result": None} for _ in range(40)]
    result = _prod7(_state(bets=bets, acknowledgedUnresolvableBetIds=set()))
    assert result["status"] == G.FAIL
    assert result["detail"]["unexplainedBacklog"] == 40


# ── PROD-7 must still be able to fail ────────────────────────────────────────

def test_a_synthetic_newly_unexplained_backlog_turns_prod7_red():
    """
    THE proof the mission requires. After all remediation, introducing genuinely
    actionable unexplained backlog must still turn PROD-7 RED.
    """
    healthy = _prod7(_state(bets=[]))
    assert healthy["status"] == G.PASS, "baseline must be green or this proves nothing"

    synthetic = [{"id": "synthetic-%03d" % i, "date": "2026-08-01", "result": None}
                 for i in range(G.MAX_UNEXPLAINED_BACKLOG + 1)]
    red = _prod7(_state(bets=synthetic))
    assert red["status"] == G.FAIL
    assert red["detail"]["unexplainedBacklog"] == G.MAX_UNEXPLAINED_BACKLOG + 1
    assert G.summarize(G.evaluate_health(_state(bets=synthetic)))["overall"] != "OK"


def test_the_prod7_threshold_was_not_weakened():
    """No remediation may buy green by moving the line."""
    assert G.MAX_UNEXPLAINED_BACKLOG == 15
    assert G.BACKLOG_GRACE_DAYS == 3


def test_should_settle_dispositions_are_never_acknowledged():
    """
    SETTLEMENT_AVAILABLE and PIPELINE_BACKLOG both describe rows that SHOULD
    settle. Acknowledging either would hide real backlog behind a taxonomy.
    """
    assert A.SETTLEMENT_AVAILABLE not in A.ACKNOWLEDGED_CLASSIFICATIONS
    assert A.PIPELINE_BACKLOG not in A.ACKNOWLEDGED_CLASSIFICATIONS
    assert set(A.ACKNOWLEDGED_CLASSIFICATIONS) == {
        A.EXPECTED_UNRESOLVED, A.IDENTITY_BLOCKED}


def test_the_classifier_never_settles_or_writes_the_wager_ledger():
    """The restraint that makes this whole tool safe to run."""
    with open(os.path.join(ROOT, "scripts", "audit_settlement_backlog.py")) as handle:
        source = handle.read()
    for forbidden in ('open(args.bets_path, "w")', "bets.json\", \"w\"",
                      "json.dump(bets", "'result'] =", '"result"] ='):
        assert forbidden not in source, (
            "the backlog classifier must never write a result or the ledger")
