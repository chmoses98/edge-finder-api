#!/usr/bin/env python3
"""
tests/test_risk_gate_execution_schema_docs_sync.py
======================================================
PR #8 hardening review, Part N: execution.json schema review.

docs/CANONICAL_SCHEMAS.md had NO entry for data/pipeline/<date>/
execution.json before this review -- a real documentation gap (the
artifact is labeled "canonical" in its own envelope metadata, per
lib/pipeline_artifacts.py's status vocabulary, but was undocumented in
the one file whose entire purpose is cataloguing canonical schemas).
Fixed by adding CANONICAL_SCHEMAS.md's new §9.

This file is the executable regression guard tying that documentation
to the actual code: it asserts the payload's top-level and per-candidate
keys are EXACTLY what §9's table documents -- no more, no fewer -- so a
future field addition/removal must deliberately update both, not drift
silently.

Also confirms (via repository-wide grep, not assumed) that there is
currently exactly ONE reference to the 'execution' pipeline stage name
anywhere in the codebase -- the write call itself in risk_gate.py's
main(). No script currently reads data/pipeline/<date>/execution.json,
so there is no existing consumer contract this schema could break.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_game, make_slate

DOCUMENTED_TOP_LEVEL_KEYS = {'date', 'decision', 'decisionReason', 'rulesVersion', 'candidates'}
DOCUMENTED_CANDIDATE_KEYS = {
    'game', 'market', 'sourceRecommendationTicker', 'status', 'tier',
    'realMoneyEligible', 'rejectionReason', 'approvedStake', 'approvedPrice',
    'gameExcluded', 'order',
}
EXCLUDED_TERMS = ('pnl', 'settlement', 'finalscore', 'final_score', 'secret', 'password', 'token')


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestSchemaDocSync:

    def test_payload_top_level_keys_exactly_match_documented_schema(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        payload = rg.build_execution_artifact_payload(
            {'date': '2026-01-01', 'games': [make_game('A', 'B', [entry])]}, 'GO', 'reason'
        )
        assert set(payload.keys()) == DOCUMENTED_TOP_LEVEL_KEYS, (
            f"payload top-level keys diverged from docs/CANONICAL_SCHEMAS.md §9: "
            f"actual={set(payload.keys())} documented={DOCUMENTED_TOP_LEVEL_KEYS}"
        )

    def test_candidate_keys_exactly_match_documented_schema(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        payload = rg.build_execution_artifact_payload(
            {'date': '2026-01-01', 'games': [make_game('A', 'B', [entry])]}, 'GO', 'reason'
        )
        assert set(payload['candidates'][0].keys()) == DOCUMENTED_CANDIDATE_KEYS, (
            f"candidate keys diverged from docs/CANONICAL_SCHEMAS.md §9: "
            f"actual={set(payload['candidates'][0].keys())} documented={DOCUMENTED_CANDIDATE_KEYS}"
        )

    def test_no_excluded_settlement_or_secret_terms_anywhere_in_payload(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        payload = rg.build_execution_artifact_payload(
            {'date': '2026-01-01', 'games': [make_game('A', 'B', [entry])]}, 'GO', 'reason'
        )
        all_keys = set(payload.keys()) | set(payload['candidates'][0].keys())
        for key in all_keys:
            for term in EXCLUDED_TERMS:
                assert term not in key.lower(), f"key {key!r} suggests forbidden content: {term}"

    def test_docs_file_actually_contains_the_new_section(self):
        with open(os.path.join(ROOT, "docs", "CANONICAL_SCHEMAS.md")) as f:
            content = f.read()
        assert "ExecutionDecision" in content
        assert "execution.json" in content
        for key in DOCUMENTED_TOP_LEVEL_KEYS | DOCUMENTED_CANDIDATE_KEYS:
            assert key in content, f"documented key {key!r} not actually mentioned in CANONICAL_SCHEMAS.md"


class TestNoExistingReaderOfExecutionArtifact:

    def test_grep_confirms_exactly_one_reference_to_execution_stage_repo_wide(self):
        """
        Repository-wide search (not scoped to risk_gate.py alone) for the
        'execution' pipeline-stage string -- confirms zero existing
        readers anywhere in scripts/, lib/, or .github/workflows/, so
        this new artifact currently has no consumer contract to break.
        A future reader appearing here would need to explicitly opt into
        this schema, not silently inherit assumptions.
        """
        result = subprocess.run(
            ['grep', '-rn', "'execution'", 'scripts/', 'lib/', '.github/'],
            cwd=ROOT, capture_output=True, text=True,
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        # Filter to lines that plausibly reference the PIPELINE STAGE name
        # (not an unrelated use of the word 'execution', e.g. in a comment
        # about "execution layer" or "execution ordering").
        stage_lines = [l for l in lines if "'execution'," in l or '"execution",' in l]
        assert len(stage_lines) == 1, (
            f"expected exactly 1 reference to the 'execution' stage name "
            f"(the write call in risk_gate.py), found {len(stage_lines)}: {stage_lines}"
        )
        assert 'risk_gate.py' in stage_lines[0]
