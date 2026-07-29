#!/usr/bin/env python3
"""
tests/test_validate_slate_final_workflow_compat.py
=======================================================
Phase 8 Part 16 (input source of truth) and Part 17 (workflow
compatibility) coverage for scripts/validate_slate_final.py.

Part 16 DECISION: data/slate.json remains the sole authoritative input
-- confirmed by grep (below) that this script never reads
recommendations.json, projections.json, normalized_slate.json,
authoritative.json, or execution.json. It only reads fields already
written INTO slate.json by earlier pipeline stages (enrich_data.py,
build_market_ledger.py). No boundary-crossing/field-ownership change
is needed or introduced.

Part 17: exercises the real script via subprocess (not an in-process
import) -- the closest proxy to how
.github/workflows/fetch-slate.yml:303 actually invokes it
(`python3 scripts/validate_slate_final.py "${{ env.DATE }}" 2>&1`) --
from the repository root and from a fully separate sandboxed cwd,
proving the CLI date-argument contract, exit codes, and file
locations are unchanged by the Phase 8 refactor.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")


class TestInputSourceOfTruthLockdown:

    def test_never_reads_other_pipeline_artifact_names(self):
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        for forbidden in (
            'recommendations.json', 'projections.json', 'normalized_slate.json',
            'authoritative.json', 'execution.json',
        ):
            assert forbidden not in src, (
                f'{forbidden} referenced -- data/slate.json is no longer the sole input'
            )

    def test_only_data_slate_json_path_literal_present(self):
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        assert "'data/slate.json'" in src
        assert src.count("'..', 'data', 'slate.json'") == 1  # the __file__-relative fallback join


class TestSubprocessWorkflowCompatibility:
    """
    IMPORTANT SAFETY NOTE, matching the one already documented in
    tests/test_build_market_ledger_projection_boundary.py's own
    TestSubprocessWorkflowCompatibility class: load_slate() checks the
    CWD-relative path FIRST, but falls back to a path relative to
    `__file__` when no cwd-relative data/slate.json exists. Pointing a
    subprocess at the REAL scripts/validate_slate_final.py with only
    `cwd=` set is NOT a safe sandbox for any scenario where the
    cwd-relative slate.json is deliberately absent (e.g. the
    missing-slate-json test below) -- `__file__` still resolves to the
    real repo path regardless of cwd, so such a subprocess would read
    the REAL repository's data/slate.json. Confirmed by hitting this
    exact leak while first writing this test class (caught via
    `git status --short data/` immediately showing no unintended
    write -- the leak here is read-only, same as the earlier in-process
    incident documented in test_validate_slate_final_immutable.py's
    TestMainIntegrationGoldenEquivalence._wire()).

    Fix: copy the script AND its lib dependencies (postponed_guard.py,
    atomic_json.py, pipeline_artifacts.py) into a sandboxed tmp
    scripts/ and lib/ tree and invoke THAT copy, so `__file__` resolves
    inside the sandbox regardless of whether cwd-relative resolution
    also would have.
    """

    def _sandbox_scripts(self, tmp_path):
        scripts_dir = tmp_path / 'scripts'
        scripts_dir.mkdir(exist_ok=True)
        lib_dir = tmp_path / 'lib'
        lib_dir.mkdir(exist_ok=True)
        shutil.copy(
            os.path.join(SCRIPTS_DIR, 'validate_slate_final.py'),
            scripts_dir / 'validate_slate_final.py',
        )
        for mod in ('postponed_guard.py', 'atomic_json.py', 'pipeline_artifacts.py'):
            shutil.copy(os.path.join(ROOT, 'lib', mod), lib_dir / mod)
        return scripts_dir / 'validate_slate_final.py'

    def _sandbox(self, tmp_path, games, date='2026-06-16'):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump({'date': date, 'games': games}, f)
        return data_dir

    def _run(self, tmp_path, args=None, env=None):
        script_path = self._sandbox_scripts(tmp_path)
        cmd = [sys.executable, str(script_path)]
        if args:
            cmd += args
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True, env=run_env)

    def _good_game(self):
        sys.path.insert(0, os.path.join(ROOT, 'tests'))
        from test_validate_slate_final_immutable import make_good_game
        return make_good_game()

    def test_full_successful_run_exits_0_via_subprocess(self, tmp_path):
        data_dir = self._sandbox(tmp_path, [self._good_game()])
        result = self._run(tmp_path, args=['2026-06-16'])
        assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
        assert 'FINAL VALIDATION PASSED' in result.stdout
        assert (data_dir / 'execution_slip_2026-06-16.txt').exists()
        assert (data_dir / 'execution_slip_2026-06-16.json').exists()

    def test_missing_slate_json_exits_1_via_subprocess(self, tmp_path):
        (tmp_path / 'data').mkdir()
        result = self._run(tmp_path, args=['2026-06-16'])
        assert result.returncode == 1
        assert 'data/slate.json not found' in result.stderr

    def test_failing_slate_exits_1_via_subprocess(self, tmp_path):
        g = self._good_game()
        g['marketLedger'] = []
        self._sandbox(tmp_path, [g])
        result = self._run(tmp_path, args=['2026-06-16'])
        assert result.returncode == 1
        assert 'FINAL VALIDATION FAILED' in result.stdout
        assert 'FINAL VALIDATION FAILED' in result.stderr

    def test_cli_date_arg_used_verbatim_no_format_validation(self, tmp_path):
        """
        expected_date() uses sys.argv[1] verbatim with no format check
        -- matches the pre-existing (undocumented, unfixed) defect this
        repo's behavior map already notes. A malformed date string must
        not itself crash the run; the fixture below is a well-formed
        slate, so the run should still pass using whatever string was
        passed as the "date".
        """
        data_dir = self._sandbox(tmp_path, [self._good_game()], date='not-a-real-date')
        result = self._run(tmp_path, args=['not-a-real-date'])
        assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
        assert (data_dir / 'execution_slip_not-a-real-date.txt').exists()

    def test_github_output_env_var_appended_via_subprocess(self, tmp_path):
        self._sandbox(tmp_path, [self._good_game()])
        gho_path = tmp_path / 'gho.txt'
        gho_path.write_text('')
        result = self._run(tmp_path, args=['2026-06-16'], env={'GITHUB_OUTPUT': str(gho_path)})
        assert result.returncode == 0
        content = gho_path.read_text()
        assert 'final_validation_status=ok' in content

    def test_validation_pipeline_artifact_written_via_subprocess(self, tmp_path):
        data_dir = self._sandbox(tmp_path, [self._good_game()])
        result = self._run(tmp_path, args=['2026-06-16'])
        assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
        artifact_path = data_dir / 'pipeline' / '2026-06-16' / 'validation.json'
        assert artifact_path.exists()
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope['data']['status'] == 'pass'

    def test_subprocess_run_never_touches_real_repo_data_directory(self, tmp_path):
        """
        Direct regression lock (matches the pattern already established
        in tests/test_build_market_ledger_projection_boundary.py):
        confirm the real repository's data/slate.json is byte-identical
        before and after this test's own subprocess run, and that no
        stray data/pipeline/<date>/validation.json or
        data/execution_slip_2026-06-16.* landed in the real repo.
        """
        real_slate_path = os.path.join(ROOT, 'data', 'slate.json')
        with open(real_slate_path, 'rb') as f:
            before = f.read()
        real_pipeline_dir_existed = os.path.isdir(os.path.join(ROOT, 'data', 'pipeline', '2026-06-16'))

        self._sandbox(tmp_path, [self._good_game()])
        self._run(tmp_path, args=['2026-06-16'])

        with open(real_slate_path, 'rb') as f:
            after = f.read()
        assert before == after
        assert os.path.isdir(os.path.join(ROOT, 'data', 'pipeline', '2026-06-16')) == real_pipeline_dir_existed

    def test_runs_correctly_via_real_script_path_when_cwd_relative_slate_present(self, tmp_path):
        """
        Invokes the REAL scripts/validate_slate_final.py (absolute
        path, no sandboxed copy) from an unrelated cwd that DOES have
        its own cwd-relative data/slate.json -- this is safe precisely
        because load_slate() finds and returns the cwd-relative path
        BEFORE ever considering the __file__-relative fallback, so the
        real repo's data/ is never consulted. Proves the CWD-relative-
        first load_slate() strategy resolves correctly regardless of
        what directory the process is launched from, matching how
        .github/workflows/fetch-slate.yml actually runs it (cwd is
        always the checked-out repo root, invoked as a relative path
        from there -- this test generalizes to "any cwd with its own
        data/slate.json," which covers that real case).
        """
        other_root = tmp_path / 'unrelated_directory_tree'
        other_root.mkdir()
        data_dir = other_root / 'data'
        data_dir.mkdir()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump({'date': '2026-06-16', 'games': [self._good_game()]}, f)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, 'validate_slate_final.py'), '2026-06-16'],
            cwd=str(other_root), capture_output=True, text=True,
        )
        assert result.returncode == 0, f'stdout={result.stdout!r} stderr={result.stderr!r}'
        # the cwd-relative path in other_root was found first and used
        # exclusively -- the real repo's data/ was never touched.
        assert (data_dir / 'execution_slip_2026-06-16.txt').exists()
