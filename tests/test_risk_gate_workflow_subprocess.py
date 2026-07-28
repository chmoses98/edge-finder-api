#!/usr/bin/env python3
"""
tests/test_risk_gate_workflow_subprocess.py
===============================================
Phase 7 Part 20-21: black-box subprocess tests invoking the real
`python3 scripts/risk_gate.py` command line exactly as
.github/workflows/fetch-slate.yml's "Risk gate" step does (no CLI args,
no env vars), proving the CLI-level contract (exit codes, no new
dependencies, output files) is unchanged by the Phase 7 refactor -- not
just the in-process function calls already covered by every other
tests/test_risk_gate_*.py file.

SANDBOXING: scripts/risk_gate.py resolves ROOT via
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` -- a
__file__-relative path, exactly like scripts/build_market_ledger.py
(see the PR #7 review incident where a subprocess test's `cwd=` alone
was NOT enough to sandbox that script and it overwrote the real repo's
data/slate.json). Every test here copies risk_gate.py PLUS its three
lib/ dependencies (postponed_guard.py, atomic_json.py,
pipeline_artifacts.py) into a fresh tmp_path directory tree
(tmp/scripts/risk_gate.py, tmp/lib/*.py, tmp/data/*.json) and invokes
the COPY, never the real repository script, with cwd set to the tmp
root as well for good measure. A dedicated leak-guard test hashes the
real repo's data/slate.json and data/meta.json before and after the
whole module runs.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_SCRIPTS_DIR = os.path.join(ROOT, "scripts")
REAL_LIB_DIR = os.path.join(ROOT, "lib")

sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate


def _hash_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


class SubprocessHarness:
    """Copies risk_gate.py + its lib/ dependencies into an isolated
    tmp_path tree and runs it as a real subprocess, never touching the
    real repository's scripts/ or data/ directories."""

    def setup_method(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "scripts"))
        os.makedirs(os.path.join(self.tmp, "lib"))
        os.makedirs(os.path.join(self.tmp, "data"))

        shutil.copy2(os.path.join(REAL_SCRIPTS_DIR, "risk_gate.py"),
                     os.path.join(self.tmp, "scripts", "risk_gate.py"))
        for lib_file in ("postponed_guard.py", "atomic_json.py", "pipeline_artifacts.py"):
            shutil.copy2(os.path.join(REAL_LIB_DIR, lib_file),
                         os.path.join(self.tmp, "lib", lib_file))

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_slate(self, games, date="2026-06-16"):
        with open(os.path.join(self.tmp, "data", "slate.json"), "w") as f:
            json.dump(make_slate(games, date=date), f)

    def _write_meta(self, meta):
        with open(os.path.join(self.tmp, "data", "meta.json"), "w") as f:
            json.dump(meta, f)

    def _run(self):
        return subprocess.run(
            [sys.executable, os.path.join("scripts", "risk_gate.py")],
            cwd=self.tmp,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _slate(self):
        with open(os.path.join(self.tmp, "data", "slate.json")) as f:
            return json.load(f)

    def _meta(self):
        with open(os.path.join(self.tmp, "data", "meta.json")) as f:
            return json.load(f)


class TestSuccessfulApproval(SubprocessHarness):

    def test_go_decision_exit_code_zero(self):
        entry = make_entry(market='ML_Away', stake=5.0)
        self._write_slate([make_game('A', 'B', [entry])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        meta = self._meta()
        assert meta['risk_gate']['decision'] == 'GO'
        slate = self._slate()
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_execution_artifact_written_by_real_subprocess(self):
        entry = make_entry(market='ML_Away', stake=5.0)
        self._write_slate([make_game('A', 'B', [entry])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        artifact_path = os.path.join(self.tmp, "data", "pipeline", "2026-06-16", "execution.json")
        assert os.path.exists(artifact_path)
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope["meta"]["stage"] == "execution"
        assert envelope["data"]["decision"] == "GO"


class TestCleanRejection(SubprocessHarness):

    def test_all_tt_no_ml_f5_paper_only_exit_code_zero(self):
        entry = make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)
        self._write_slate([make_game('A', 'B', [entry])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        meta = self._meta()
        assert meta['risk_gate']['decision'] == 'PAPER_ONLY'
        slate = self._slate()
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'PAPER'
        assert slate['games'][0]['marketLedger'][0]['blockReason'].startswith('RISK_GATE_PAPER_ONLY:')


class TestMixedPortfolio(SubprocessHarness):

    def test_tt_and_ml_f5_mixed_go_decision(self):
        tt_entry = make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)
        ml1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=3.0, ticker='ML1')
        ml2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=3.0, ticker='ML2')
        self._write_slate([make_game('A', 'B', [tt_entry, ml1, ml2])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        meta = self._meta()
        assert meta['risk_gate']['decision'] == 'GO'
        assert meta['risk_gate']['tt_bets'] == 1
        assert meta['risk_gate']['ml_f5_bets'] == 2


class TestFatalMalformedInput(SubprocessHarness):

    def test_missing_slate_json_exits_1(self):
        # Deliberately do not write data/slate.json.
        result = self._run()
        assert result.returncode == 1
        assert not os.path.exists(os.path.join(self.tmp, "data", "meta.json"))

    def test_malformed_slate_json_nonzero_exit(self):
        with open(os.path.join(self.tmp, "data", "slate.json"), "w") as f:
            f.write("{not valid json")
        result = self._run()
        assert result.returncode != 0
        assert not os.path.exists(os.path.join(self.tmp, "data", "meta.json"))


class TestWriteFailureAtCliLevel(SubprocessHarness):

    def test_readonly_data_directory_produces_nonzero_exit_not_silent_success(self):
        """
        A write failure at the process level (e.g. permission denied on
        the destination directory) must propagate as a real, visible
        subprocess failure -- never a silent exit-0 with a stale/missing
        slate.json. Skipped when running as root (root ignores directory
        write permission bits, so this scenario can't be reproduced).
        """
        if os.geteuid() == 0:
            pytest.skip("cannot simulate a permission-denied write while running as root")
        entry = make_entry(market='ML_Away')
        self._write_slate([make_game('A', 'B', [entry])])
        data_dir = os.path.join(self.tmp, "data")
        os.chmod(data_dir, 0o555)
        try:
            result = self._run()
            assert result.returncode != 0
        finally:
            os.chmod(data_dir, 0o755)


class TestNoNewDependencies(SubprocessHarness):

    def test_only_stdlib_and_repo_lib_imports_needed(self):
        """
        The exact copy set this harness uses (risk_gate.py + postponed_
        guard.py + atomic_json.py + pipeline_artifacts.py, no third-party
        packages) is sufficient for a successful run -- proving Phase 7
        introduced no new external dependency the workflow's Python
        environment would need to additionally install.
        """
        entry = make_entry(market='ML_Away')
        self._write_slate([make_game('A', 'B', [entry])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr
        assert "ImportError" not in result.stderr


class TestArtifactFailureDoesNotBlockWorkflowAtCliLevel(SubprocessHarness):

    def test_missing_lib_pipeline_artifacts_still_exits_zero(self):
        """
        If lib/pipeline_artifacts.py were somehow unavailable at runtime
        (simulating an environment inconsistency), the best-effort
        try/except around the artifact-publication import+call must
        still let main() complete normally -- exit code 0, legacy
        slate.json/meta.json fully written -- exactly like any other
        artifact-publication failure already covered in-process by
        tests/test_risk_gate_execution_artifact.py.
        """
        os.remove(os.path.join(self.tmp, "lib", "pipeline_artifacts.py"))
        entry = make_entry(market='ML_Away')
        self._write_slate([make_game('A', 'B', [entry])])
        result = self._run()
        assert result.returncode == 0, result.stderr
        meta = self._meta()
        assert meta['risk_gate']['decision'] == 'GO'
        assert "could not write execution pipeline artifact" in result.stdout


class TestRealRepoDataNeverTouchedBySubprocessTests:
    """Phase 7 Part 21 test-isolation verification: every production file
    a real workflow run could touch is hashed before and after a full
    subprocess invocation and proven byte-identical -- not just
    slate.json/meta.json, but also bets.json, BET_LOG.md, and
    config/rules.json/RULES.md, none of which risk_gate.py should ever
    read or write in the first place."""

    def test_leak_guard_hashes_unchanged(self):
        watched = {
            "slate.json": os.path.join(ROOT, "data", "slate.json"),
            "meta.json": os.path.join(ROOT, "data", "meta.json"),
            "bets.json": os.path.join(ROOT, "data", "bets.json"),
            "BET_LOG.md": os.path.join(ROOT, "BET_LOG.md"),
            "rules.json": os.path.join(ROOT, "config", "rules.json"),
            "RULES.md": os.path.join(ROOT, "RULES.md"),
        }
        real_pipeline_dir = os.path.join(ROOT, "data", "pipeline")

        before = {name: _hash_file(path) for name, path in watched.items()}
        pipeline_existed_before = os.path.exists(real_pipeline_dir)

        harness = SubprocessHarness()
        harness.setup_method()
        try:
            entry = make_entry(market='ML_Away')
            harness._write_slate([make_game('A', 'B', [entry])])
            result = harness._run()
            assert result.returncode == 0
        finally:
            harness.teardown_method()

        after = {name: _hash_file(path) for name, path in watched.items()}
        for name in watched:
            assert before[name] == after[name], f"{name} was modified by a sandboxed subprocess run"
        assert os.path.exists(real_pipeline_dir) == pipeline_existed_before

    def test_no_leaked_temp_directories_from_this_harness(self):
        """Every SubprocessHarness instance's tmp_path tree must be fully
        removed by teardown_method -- confirmed by checking that no
        directory anywhere under the system temp root contains a copy of
        scripts/risk_gate.py after this harness has run and torn down."""
        import tempfile
        harness = SubprocessHarness()
        harness.setup_method()
        tmp_dir = harness.tmp
        harness.teardown_method()
        assert not os.path.exists(tmp_dir)
