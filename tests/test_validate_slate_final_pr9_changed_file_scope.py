#!/usr/bin/env python3
"""
tests/test_validate_slate_final_pr9_changed_file_scope.py
==============================================================
PR #9 hardening review, Part 25: regression guard proving the only
intentionally-refactored PRODUCTION file on this PR/branch is
scripts/validate_slate_final.py. Supporting changes (tests, docs,
frozen legacy snapshots) are expected and excluded from this check.

Base SHA (fe0a19ceccec340c84e1bb3e77244ac7afaf6091) is PINNED, matching
the established pattern in tests/test_risk_gate_review_parts_i_to_m.py
(a moving ref like `origin/main...HEAD` becomes meaningless -- and, as
that file's own docstring documents, was caught producing a real false
failure -- once main advances past or through this branch). HEAD was
originally read live via `git rev-parse HEAD` so the check kept holding
as further review commits (which only ever touched tests/docs) were
added during PR #9's own hardening pass, without needing to be
re-pinned after every commit.

**Update (Phase 9):** PR #9 has now merged (merge SHA
b006c39263db1b0d2e47f15a7469f6abab517ff5), and -- exactly as this
docstring predicted -- this check has become historical. Phase 9 work
continues on a new branch built on top of that merge and legitimately
changes a different production file (scripts/protect_slate.py), which
made the live-`HEAD` comparison fail: not a regression, just this
guard correctly outliving the PR it was scoped to. The head ref is now
pinned to the PR #9 merge SHA itself, so this file permanently verifies
PR #9's own historical diff (validate_slate_final.py only) regardless
of what later phases change. Phase 9's own equivalent guard lives in
tests/test_protect_slate_rerun_and_scope.py::TestChangedFileScope.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PR9_BASE_SHA = 'fe0a19ceccec340c84e1bb3e77244ac7afaf6091'
PR9_MERGE_SHA = 'b006c39263db1b0d2e47f15a7469f6abab517ff5'

EXPECTED_PRODUCTION_FILES = {'scripts/validate_slate_final.py'}


def _changed_files(base_sha, head_ref=PR9_MERGE_SHA):
    result = subprocess.run(
        ['git', 'diff', '--name-only', f'{base_sha}..{head_ref}'],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _is_production_file(path):
    if path.startswith('tests/'):
        return False
    if path.startswith('docs/'):
        return False
    return True


class TestPR9ChangedFileScope:

    def test_only_expected_production_file_changed(self):
        changed = _changed_files(PR9_BASE_SHA)
        production_changed = {p for p in changed if _is_production_file(p)}
        assert production_changed == EXPECTED_PRODUCTION_FILES, (
            f'Unexpected production files changed on this PR: '
            f'{production_changed - EXPECTED_PRODUCTION_FILES} '
            f'(expected only {EXPECTED_PRODUCTION_FILES})'
        )

    def test_no_data_or_ledger_files_touched(self):
        """
        Belt-and-suspenders: even though data/ and historical ledger
        files would already fail _is_production_file's tests/docs
        exclusion (they're neither), this asserts directly against the
        specific paths the mission explicitly forbids mutating.
        """
        changed = set(_changed_files(PR9_BASE_SHA))
        forbidden = {
            'data/slate.json', 'data/meta.json', 'data/bets.json',
            'data/authoritative.json', 'BET_LOG.md', 'config/rules.json',
            'RULES.md', 'bets.json',
        }
        touched_forbidden = changed & forbidden
        assert touched_forbidden == set(), f'Forbidden files touched: {touched_forbidden}'

    def test_no_workflow_files_touched(self):
        changed = _changed_files(PR9_BASE_SHA)
        workflow_files = [p for p in changed if p.startswith('.github/workflows/')]
        assert workflow_files == [], f'Workflow files touched: {workflow_files}'

    def test_legacy_snapshot_files_present_and_frozen(self):
        """
        The frozen legacy snapshots are supporting test infrastructure,
        not production files -- but they must exist and must not have
        been edited after their initial add (a snapshot that gets
        "improved" after the fact defeats its entire purpose as a
        frozen pre-refactor baseline).
        """
        # Deliberately NOT --follow: this file was created fresh in
        # Phase 8, not renamed from an older one. --follow's
        # content-similarity rename heuristic was confirmed (while
        # writing this test) to spuriously match an unrelated file
        # inside the giant initial-import squash commit, producing a
        # false second "commit" for this path -- plain `git log`
        # (exact path history only, no rename detection) is the
        # correct tool here.
        result = subprocess.run(
            ['git', 'log', '--oneline',
             '--', 'tests/_legacy_snapshots/validate_slate_final_phase8_base.py'],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        commits = [l for l in result.stdout.splitlines() if l.strip()]
        assert len(commits) == 1, (
            f'Expected the frozen snapshot to have exactly one commit '
            f'(its initial add, never edited afterward), found {len(commits)}: {commits}'
        )
