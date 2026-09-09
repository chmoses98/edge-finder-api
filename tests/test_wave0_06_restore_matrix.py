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


# Population B closing-price evidence as it stood BEFORE the authorized
# production restore of 2026-09-01..09-06: 51 canonical wagers in the window,
# 0 settled, 49 carrying CLV, 2 with clv=None for want of genuine closing
# evidence. The restore settled the outcomes; it was forbidden to touch the
# economics. These are therefore frozen historical numbers, not a moving
# target -- the window is closed and nothing may legitimately change them.
POP_B_ROWS_IN_WINDOW = 51
POP_B_WITH_CLV_IN_WINDOW = 49


def test_outcome_truth_and_closing_price_truth_are_tracked_separately(matrix):
    """
    THE invariant, and the reason this file exists.

    Before the restore these two counts were maximally separated: 0 settled,
    49 with CLV. The restore settled 50 of the 51 wagers. The separation must
    survive that: settling a wager supplies OUTCOME truth and must never
    manufacture CLOSING-PRICE truth. So CLV coverage must be exactly what it
    was -- if it rose in step with settlement, CLV was being synthesized from
    the outcome, which is the single thing the restore was most forbidden to
    do.
    """
    summary = matrix["summary"]
    assert summary["populationB_canonicalBetsJsonlRowsInWindow"] == POP_B_ROWS_IN_WINDOW

    assert summary["populationB_withClvInWindow"] == POP_B_WITH_CLV_IN_WINDOW, (
        "CLV coverage in this closed historical window changed from %d to %d. "
        "Settlement must never create, recompute or backfill CLV; the two "
        "wagers without genuine archived closing evidence stay clv=null."
        % (POP_B_WITH_CLV_IN_WINDOW, summary["populationB_withClvInWindow"]))

    # The counts must remain genuinely independent quantities: more wagers are
    # now settled than carry CLV, which is only possible if the two are
    # tracked separately.
    assert summary["populationB_settledInWindow"] > summary["populationB_withClvInWindow"], (
        "outcome truth and closing-price truth have collapsed into one count")


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


def test_settlements_and_recommendations_are_present_across_the_window(matrix):
    """
    The gap this matrix was built to measure, now closed.

    Wave 0.06 wrote this test as a tripwire in the opposite direction -- it
    asserted both families were ABSENT on all six dates and said in its own
    docstring "if either becomes present, the restore has begun". The
    CEO-authorized production restore of 2026-09-01..09-06 is that event, so
    the tripwire has fired as designed and is inverted here rather than
    deleted: it now guards the restored corpus against silent regression or
    truncation.
    """
    for date in WINDOW:
        disp = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["disposition"]
        assert disp["settlements"] != "ABSENT", (
            "%s settlements have gone missing since the restore" % date)
        assert disp["recommendations"] != "ABSENT", (
            "%s recommendations have gone missing since the restore" % date)

        fam = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["families"]
        assert fam["settlements"]["rows"] > 0
        assert fam["recommendations"]["rows"] > 0
        # Every date settled a large majority of what it observed. A partition
        # that exists but is nearly empty is a failed restore wearing the
        # appearance of a successful one.
        assert fam["settlements"]["rows"] >= 0.9 * fam["recommendations"]["rows"], (
            "%s settled only %d of %d observed markets" % (
                date, fam["settlements"]["rows"], fam["recommendations"]["rows"]))


def test_artifact_source_counts_are_json_sortable(matrix):
    """
    Regression guard. The counter keying artifactSources took its key straight
    from the row, so a row with no artifactSource produced a None key; the
    matrix is serialised with sort_keys=True, and sorting str against None
    raises TypeError. Pre-restore every row was prospective_snapshot, so the
    defect was invisible until the restored corpus introduced rows without the
    field, at which point `--json` crashed.
    """
    for date in WINDOW:
        fam = matrix["dates"][date]["populationA_edgelabObservedMarketUniverse"]["families"]
        sources = fam["model_evaluations"].get("artifactSources", {})
        for key in sources:
            assert isinstance(key, str), (
                "%s artifactSources has a non-string key %r" % (date, key))
    json.dumps(matrix, sort_keys=True)


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
