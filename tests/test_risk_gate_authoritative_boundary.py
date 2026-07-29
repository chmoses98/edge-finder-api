#!/usr/bin/env python3
"""
tests/test_risk_gate_authoritative_boundary.py
==================================================
Phase 7 Part 19: risk_gate.py's relationship to data/slates/<date>/
authoritative.json and scripts/protect_slate.py, documented and
grep-verified rather than assumed. Mission: "No structural change to
this boundary in Phase 7 unless required to prevent a direct regression
introduced by the risk-gate refactor" -- these tests document the
EXISTING relationship; they do not (and this phase does not) touch
protect_slate.py or lib/slate_manager.py at all.

THE RELATIONSHIP, VERIFIED FROM THE WORKFLOW AND SOURCE
----------------------------------------------------------
.github/workflows/fetch-slate.yml's step order (verified by reading the
file, not assumed):

  1. "Apply slate protection" (id: protect_slate) runs scripts/
     protect_slate.py, which reads data/slate.json, routes it through
     lib/slate_manager.py's run-type detection, and — for an
     OFFICIAL_PREGAME or LINEUP_RECHECK run — writes
     data/slates/<date>/authoritative.json. It then copies
     authoritative.json BACK onto data/slate.json ("backwards compat").
     This is the ONLY writer of authoritative.json anywhere in the
     pipeline.

  2. "Write meta and commit authoritative slate" (id: publish_slate)
     commits data/slate.json + data/meta.json to git as they stand at
     this point — i.e. the authoritative-derived snapshot from step 1,
     with no TT-safety or portfolio-composition decisions applied yet.

  3. "Risk gate" (id: risk_gate) — scripts/risk_gate.py — runs AFTER
     both of the above and mutates data/slate.json IN PLACE (TT
     downgrades, PAPER_ONLY forcing). protect_slate.py does NOT run
     again afterward.

CONSEQUENCE: authoritative.json is a snapshot of the slate BEFORE
risk_gate.py's decisions exist. data/slate.json's final, post-workflow
content (and now the Phase 7 execution.json artifact) reflects the
slate AFTER risk_gate.py's decisions. These two are NOT reconciled by
anything in this pipeline -- they represent different, intentionally
distinct pipeline stages (recommendation-time vs execution-time), not a
staleness bug. The existing fetch-slate.yml workflow comment (BLOCK 8)
already documents this exact divergence as "intentional, not staleness."

risk_gate.py itself never reads, writes, or otherwise references
authoritative.json (grep-verified below) or protect_slate.py/
slate_manager.py (also grep-verified) -- it is entirely unaware that
authoritative.json exists. There is therefore no code-level "which
source wins" conflict inside risk_gate.py itself; the divergence exists
purely because the two files are snapshots of different pipeline moments,
authored by two scripts that never call each other or share state.

Recommendation for a future phase (NOT acted on here, per the mission's
explicit "no structural change... unless required to prevent a direct
regression"): if a future Phase 8 wants a single authoritative record of
BOTH the recommendation and the final execution decision, the cleanest
path is the Phase 7 execution.json artifact (already sourceStage=
"recommendations", already schema-versioned) becoming the canonical
execution-time counterpart to authoritative.json's recommendation-time
snapshot -- not merging risk_gate.py's mutation into authoritative.json
itself, which would blur protect_slate.py's single-writer invariant for
that file.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RISK_GATE_PATH = os.path.join(ROOT, "scripts", "risk_gate.py")
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")


class TestRiskGateNeverReferencesAuthoritativeOrProtectSlate:

    def test_no_authoritative_references_in_risk_gate_source(self):
        with open(RISK_GATE_PATH) as f:
            source = f.read()
        assert "authoritative" not in source.lower()

    def test_no_protect_slate_or_slate_manager_references_in_risk_gate_source(self):
        with open(RISK_GATE_PATH) as f:
            source = f.read()
        assert "protect_slate" not in source.lower()
        assert "slate_manager" not in source.lower()


class TestWorkflowStepOrderingMatchesDocumentedBoundary:
    """
    Grep-verified, not assumed: protect_slate runs before publish_slate,
    which runs before risk_gate -- the exact ordering this file's
    docstring depends on to justify the "authoritative.json predates
    risk_gate.py's decisions" finding.
    """

    def test_protect_slate_step_exists_before_publish_slate(self):
        with open(WORKFLOW_PATH) as f:
            lines = f.readlines()
        protect_idx = next(i for i, l in enumerate(lines) if "id: protect_slate" in l)
        publish_idx = next(i for i, l in enumerate(lines) if "id: publish_slate" in l)
        risk_gate_idx = next(i for i, l in enumerate(lines) if "id: risk_gate" in l)
        assert protect_idx < publish_idx < risk_gate_idx

    def test_risk_gate_step_depends_on_publish_slate_success(self):
        with open(WORKFLOW_PATH) as f:
            content = f.read()
        assert "if: steps.publish_slate.outcome == 'success'" in content

    def test_protect_slate_does_not_run_again_after_risk_gate(self):
        """
        Only one "id: protect_slate" step exists in the entire workflow --
        protect_slate.py (and therefore authoritative.json) is never
        re-invoked after risk_gate.py mutates data/slate.json.
        """
        with open(WORKFLOW_PATH) as f:
            content = f.read()
        assert content.count("id: protect_slate") == 1
