#!/usr/bin/env python3
"""
tests/test_wave0_06_restore_matrix.py
=====================================
WAVE 0.06. Guards on the restore-matrix audit tool.

The matrix exists because Wave 0 scoped the six-date restore from the wrong
population -- the root wager ledger, which holds one row in the whole window --
and would therefore have "succeeded" while leaving the real gap untouched.
These tests pin the distinctions that make the matrix correct, above all that
outcome truth and closing-price truth are never conflated.
"""

import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
TOOL = os.path.join(ROOT, "scripts", "audit", "wave0_06_restore_matrix.py")

sys.path.insert(0, os.path.join(ROOT, "scripts", "audit"))
from wave0_06_restore_matrix import (  # noqa: E402
    EDGELAB_FAMILIES, WINDOW, build_matrix,
)


@pytest.fixture(scope="module")
def matrix():
    return build_matrix()


def test_the_tool_is_read_only_and_imports_without_side_effects():
    """Audit tooling must never execute or write on import."""
    probe = (
        "import sys, io, contextlib\n"
        "sys.path.insert(0, %r)\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):\n"
        "    import wave0_06_restore_matrix\n"
        "assert buf.getvalue() == '', buf.getvalue()[:200]\n"
        "print('CLEAN')\n"
    ) % os.path.join(ROOT, "scripts", "audit")
    proc = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert "CLEAN" in proc.stdout


def test_every_window_date_is_classified(matrix):
    assert sorted(matrix["dates"]) == sorted(WINDOW)
    for date in WINDOW:
        entry = matrix["dates"][date]
        for family in EDGELAB_FAMILIES:
            assert family in entry["populationA_edgelabObservedMarketUniverse"]["disposition"]


def test_the_two_wager_populations_are_counted_independently(matrix):
    """
    Population B must never be inferred from population A. Root and canonical
    ledgers are separate counts, and the whole point of the matrix is that they
    disagree with the EdgeLab universe.
    """
    for date in WINDOW:
        b = matrix["dates"][date]["populationB_placedWagers"]
        assert "rootBetsJson" in b and "canonicalBetsJsonl" in b
        assert b["rootBetsJson"]["rows"] >= 0
        assert b["canonicalBetsJsonl"]["rows"] >= 0

    # The finding that corrects Wave 0's scope.
    assert matrix["summary"]["populationB_rootBetsJsonRowsInWindow"] \
        < matrix["summary"]["populationB_canonicalBetsJsonlRowsInWindow"], (
        "the root ledger is supposed to be the SMALLER population here; if this "
        "flips, the matrix's central correction needs re-deriving")


def test_outcome_truth_and_closing_price_truth_are_tracked_separately(matrix):
    """
    THE invariant. In this window every canonical wager is unsettled while
    almost all already carry a closing price and CLV. A matrix that collapsed
    the two would report the window as needing CLV work, which is false.
    """
    assert matrix["summary"]["populationB_settledInWindow"] == 0
    assert matrix["summary"]["populationB_withClvInWindow"] > 0, (
        "closing-price/CLV evidence exists independently of settlement; that "
        "separation is the point of this matrix")


def test_no_local_outcome_truth_exists_anywhere_in_the_window(matrix):
    """
    The gap a durable result sidecar must fill. If this ever becomes True, the
    result cache has landed and the restore plan should be re-derived.
    """
    assert matrix["summary"]["outcomeTruthPresentAnywhereLocally"] is False
    for date in WINDOW:
        assert matrix["dates"][date]["outcomeTruthAvailableLocally"] is False


def test_game_identity_is_already_durable_via_gamepk(matrix):
    """
    Identity is present even though outcome is not -- so the result cache can
    key on mlbGamePk rather than inventing a new identity scheme.
    """
    for date in WINDOW:
        ident = matrix["dates"][date]["identityEvidence"]
        assert ident["present"] is True
        assert ident["distinctGamePk"] > 0
        assert ident["gamePkCoveragePct"] == 100.0, (
            "%s has incomplete gamePk coverage (%s%%)"
            % (date, ident["gamePkCoveragePct"]))


def test_team_pair_collisions_are_recorded_so_a_naive_key_is_provably_unsafe(matrix):
    """
    date+away+home would collapse multiple records on every date in this
    window. Recording the collision count is what proves gamePk is required.
    """
    for date in WINDOW:
        ident = matrix["dates"][date]["identityEvidence"]
        assert ident["teamPairCollisions"] > 0, (
            "%s reports no team-pair collision; the claim that a naive "
            "date+away+home key is unsafe would need re-deriving" % date)
        assert ident["distinctGamePk"] < ident["recordRows"], (
            "gamePk must disambiguate records that a team-pair key merges")


def test_the_doubleheader_date_is_identified(matrix):
    """2026-09-04 carries genuine doubleheaderGameNumber records."""
    dh = {d: matrix["dates"][d]["identityEvidence"]["doubleheaderFlaggedRecords"]
          for d in WINDOW}
    assert dh["2026-09-04"] > 0, (
        "2026-09-04 was identified as the doubleheader-sensitive date; if that "
        "is no longer true the rehearsal date choice must change")


def test_thin_model_evaluation_partitions_are_not_called_postgame_evaluations(matrix):
    """
    The window's model_evaluations partitions are prospective_snapshot research
    capture, NOT partial postgame evaluations. Mislabelling them would make the
    restore look partially complete when it has not started.
    """
    for date in WINDOW:
        fam = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["families"]
        me = fam["model_evaluations"]
        assert me["present"] is True
        assert me["isPostgameEvaluationPopulation"] is False
        disp = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["disposition"]
        assert disp["model_evaluations"] == "PRESENT_BUT_DIFFERENT_ARTIFACT_CLASS"


def test_settlements_and_recommendations_are_absent_across_the_window(matrix):
    """The actual gap. If either becomes present, the restore has begun."""
    for date in WINDOW:
        disp = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["disposition"]
        assert disp["settlements"] == "ABSENT", "%s settlements no longer absent" % date
        assert disp["recommendations"] == "ABSENT", "%s recommendations no longer absent" % date


def test_every_unresolved_row_carries_an_explicit_reason(matrix):
    for date in WINDOW:
        for entry in matrix["dates"][date]["populationC_expectedUnresolved"]:
            assert entry["category"]
            assert entry["reason"] and len(entry["reason"]) > 20, (
                "population C entries must carry a durable explicit reason")


def test_safety_invariants_are_declared_in_the_artifact(matrix):
    joined = " ".join(matrix["safetyInvariants"]).lower()
    assert "never assumed to have been placed" in joined
    assert "midpoint" in joined and "sibling contract" in joined
    assert matrix["mode"] == "AUDIT_ONLY_READ_ONLY"


def test_cli_runs_and_emits_valid_json_without_writing(tmp_path):
    """--json must not write the artifact."""
    before = os.path.join(ROOT, "data", "edgelab", "operational_health",
                          "wave0_06_restore_matrix.json")
    stamp = os.path.getmtime(before) if os.path.exists(before) else None

    proc = subprocess.run([sys.executable, TOOL, "--json"], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-1000:]
    parsed = json.loads(proc.stdout)
    assert parsed["wave"] == "WAVE_0_06"

    if stamp is not None:
        assert os.path.getmtime(before) == stamp, (
            "--json must not rewrite the artifact")
