#!/usr/bin/env python3
"""
tests/test_validate_slate_final_process_differential.py
============================================================
Pre-merge hardening addition (PR #9 review): closes a real gap in
tests/test_validate_slate_final_differential.py -- that harness
compares validate_final()/generate_execution_slip() RETURN VALUES
between the frozen legacy snapshot and the current implementation, but
never captures or diffs stdout, stderr, or exit codes side-by-side.
This file runs BOTH implementations as real, separate OS subprocesses
(not in-process imports) against byte-identical fixture inputs and
diffs their process-level output directly.

Both the legacy snapshot and the current script resolve their
lib/postponed_guard import as `dirname(dirname(abspath(__file__)))/lib`
-- to invoke either as a real subprocess without touching the real
repo, each must be copied into its OWN sandboxed
<root>/scripts/validate_slate_final.py + <root>/lib/*.py tree (same
technique as tests/test_validate_slate_final_workflow_compat.py's
TestSubprocessWorkflowCompatibility, applied here to run the legacy
snapshot as well, which that file never does).

The ONE known, documented, intentional difference between the two
processes' output is accounted for explicitly in each assertion: the
current implementation prints one additional line
("  validation pipeline artifact written for {date}") and writes one
additional file (data/pipeline/<date>/validation.json) that the legacy
implementation does not -- the new Phase 8 validation artifact. Every
other byte of stdout/stderr, and the exit code, must match exactly.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
LEGACY_SNAPSHOT = os.path.join(ROOT, "tests", "_legacy_snapshots", "validate_slate_final_phase8_base.py")
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import make_good_game, make_slate  # noqa: E402

ARTIFACT_LINE_RE = re.compile(r'^ {2}validation pipeline artifact written for .*\n', re.MULTILINE)


def _build_sandbox(tmp_path, name, script_source_path, lib_files):
    root = tmp_path / name
    scripts_dir = root / 'scripts'
    scripts_dir.mkdir(parents=True)
    lib_dir = root / 'lib'
    lib_dir.mkdir(parents=True)
    (root / 'data').mkdir()
    shutil.copy(script_source_path, scripts_dir / 'validate_slate_final.py')
    for f in lib_files:
        shutil.copy(os.path.join(LIB_DIR, f), lib_dir / f)
    return root


def _write_slate(root, games, date='2026-06-16'):
    with open(root / 'data' / 'slate.json', 'w') as f:
        json.dump({'date': date, 'games': games}, f)


def _run(root, date='2026-06-16'):
    return subprocess.run(
        [sys.executable, 'scripts/validate_slate_final.py', date],
        cwd=str(root), capture_output=True, text=True,
    )


@pytest.fixture
def sandboxes(tmp_path):
    legacy_root = _build_sandbox(tmp_path, 'legacy', LEGACY_SNAPSHOT, ['postponed_guard.py'])
    current_root = _build_sandbox(
        tmp_path, 'current', os.path.join(SCRIPTS_DIR, 'validate_slate_final.py'),
        ['postponed_guard.py', 'atomic_json.py', 'pipeline_artifacts.py'],
    )
    return legacy_root, current_root


def _strip_artifact_line(stdout):
    return ARTIFACT_LINE_RE.sub('', stdout)


def _strip_sandbox_root(text, *roots):
    """
    load_slate()'s diagnostic print embeds the absolute __file__-relative
    path when falling back off a missing cwd-relative slate.json --
    since the legacy/current sandboxes necessarily live at DIFFERENT
    absolute tmp_path locations, that one line legitimately differs by
    sandbox root path alone. Normalizing both roots to a placeholder
    isolates any REAL content difference from this expected artifact of
    running two separate sandboxes side by side.
    """
    for root in roots:
        text = text.replace(str(root), '<SANDBOX_ROOT>')
    return text


# Order-independent matcher for the malformed-ledger sort crash. The operand
# order depends on hash(None), which is address-derived on CPython 3.11 and
# therefore environment-dependent -- see
# test_malformed_ledger_row_crash_path_stdout_stderr_exit_code.
_INCOMPARABLE_SORT_CRASH = re.compile(
    r"TypeError: '<' not supported between instances of "
    r"(?:'NoneType' and 'str'|'str' and 'NoneType')"
)


class TestProcessLevelDifferential:

    def test_full_pass_path_stdout_stderr_exit_code(self, sandboxes):
        legacy_root, current_root = sandboxes
        g = make_good_game()
        _write_slate(legacy_root, [g])
        _write_slate(current_root, [g])

        legacy_result = _run(legacy_root)
        current_result = _run(current_root)

        assert legacy_result.returncode == current_result.returncode == 0
        assert legacy_result.stderr == current_result.stderr == ''
        assert _strip_artifact_line(current_result.stdout) == legacy_result.stdout
        assert '  validation pipeline artifact written for 2026-06-16' in current_result.stdout
        assert (current_root / 'data' / 'pipeline' / '2026-06-16' / 'validation.json').exists()
        assert not (legacy_root / 'data' / 'pipeline').exists()

    def test_fail_path_stdout_stderr_exit_code(self, sandboxes):
        legacy_root, current_root = sandboxes
        g = make_good_game()
        g['marketLedger'] = []
        _write_slate(legacy_root, [g])
        _write_slate(current_root, [g])

        legacy_result = _run(legacy_root)
        current_result = _run(current_root)

        assert legacy_result.returncode == current_result.returncode == 1
        assert legacy_result.stderr == current_result.stderr
        assert _strip_artifact_line(current_result.stdout) == legacy_result.stdout
        assert (current_root / 'data' / 'pipeline' / '2026-06-16' / 'validation.json').exists()

    def test_missing_slate_json_stdout_stderr_exit_code(self, sandboxes):
        legacy_root, current_root = sandboxes
        # no slate.json written at all

        legacy_result = _run(legacy_root)
        current_result = _run(current_root)

        assert legacy_result.returncode == current_result.returncode == 1
        assert (_strip_sandbox_root(legacy_result.stdout, legacy_root, current_root) ==
                _strip_sandbox_root(current_result.stdout, legacy_root, current_root))
        assert legacy_result.stderr == current_result.stderr
        assert 'data/slate.json not found' in legacy_result.stderr

    def test_malformed_ledger_row_crash_path_stdout_stderr_exit_code(self, sandboxes):
        """
        Part 9: independently reproduces the pre-existing malformed-
        marketLedger-row TypeError crash as real, separate subprocesses
        for BOTH implementations, proving the refactor changed nothing
        about where the exception originates, what main() prints, or
        the resulting exit code. This crash happens INSIDE the
        try/except around the validate_final() call -- before the new
        validation-artifact-publishing code even runs -- so NO stdout
        difference is expected here at all (unlike the pass/fail paths
        above), and none is allowed by this assertion.
        """
        g = make_good_game()
        g['marketLedger'][0] = {'status': 'Accepted', 'edge': 4.0, 'confidence': 'HIGH', 'kalshiPrice': -110}
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') != 'RL_Home']
        legacy_root, current_root = sandboxes
        _write_slate(legacy_root, [g])
        _write_slate(current_root, [g])

        legacy_result = _run(legacy_root)
        current_result = _run(current_root)

        assert legacy_result.returncode == current_result.returncode == 1
        assert 'VALIDATE CRASH' in legacy_result.stdout
        assert 'VALIDATE CRASH' in current_result.stdout
        assert 'VALIDATE CRASH' in legacy_result.stderr
        assert 'VALIDATE CRASH' in current_result.stderr
        # The SAME incomparable-types TypeError must originate from the
        # identical sorted(ledger_markets) call in both implementations.
        #
        # Asserting one fixed operand ORDER here was flaky. validate_final
        # builds `ledger_markets` as a SET, and CPython names whichever pair
        # sorted() happened to compare first -- which is a pure function of
        # where the incomparable member lands in the set's internal layout,
        # i.e. of hash(None).
        #
        # On CPython 3.11 hash(None) is ADDRESS-DERIVED (hash(None) ==
        # id(None) >> 4, verified), so it varies between interpreter builds
        # and environments. It is NOT governed by PYTHONHASHSEED: sweeping
        # seeds 0..199 on this exact set yields the same order 200/200 times
        # locally, yet CI (a different 3.11 patch release on a different
        # runner) reported the OPPOSITE order and failed this assertion.
        # Pinning either order therefore breaks in some environments, and
        # asserting the two stderrs are byte-equal would be fragile for the
        # same reason.
        #
        # Assert the invariant instead: a TypeError from comparing NoneType
        # against str, either way round.
        for result in (legacy_result, current_result):
            assert _INCOMPARABLE_SORT_CRASH.search(result.stderr), result.stderr
        # no validation.json artifact on the crash path -- the
        # try/except around validate_final() exits before that code runs
        assert not (current_root / 'data' / 'pipeline').exists()
        assert not (legacy_root / 'data' / 'pipeline').exists()
        # no slip files either -- crash happens before any of that code
        assert not list((legacy_root / 'data').glob('execution_slip_*'))
        assert not list((current_root / 'data').glob('execution_slip_*'))

    def test_no_games_stdout_stderr_exit_code(self, sandboxes):
        legacy_root, current_root = sandboxes
        _write_slate(legacy_root, [])
        _write_slate(current_root, [])

        legacy_result = _run(legacy_root)
        current_result = _run(current_root)

        assert legacy_result.returncode == current_result.returncode == 1
        assert _strip_artifact_line(current_result.stdout) == legacy_result.stdout
        assert legacy_result.stderr == current_result.stderr
        assert (current_root / 'data' / 'pipeline' / '2026-06-16' / 'validation.json').exists()

    def test_real_repo_untouched_by_either_sandbox(self, sandboxes):
        real_slate = os.path.join(ROOT, 'data', 'slate.json')
        with open(real_slate, 'rb') as f:
            before = f.read()
        legacy_root, current_root = sandboxes
        _write_slate(legacy_root, [make_good_game()])
        _write_slate(current_root, [make_good_game()])
        _run(legacy_root)
        _run(current_root)
        with open(real_slate, 'rb') as f:
            after = f.read()
        assert before == after
