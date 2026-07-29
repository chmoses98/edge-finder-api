#!/usr/bin/env python3
"""
tests/test_write_pending_bets_workflow_compat.py
=====================================================
Phase 10 workflow-compatibility coverage for
scripts/write_pending_bets.py: real subprocess invocation matching the
exact command .github/workflows/fetch-slate.yml uses --
`python3 scripts/write_pending_bets.py` (no arguments), invoked with
cwd == the checkout root (no working-directory: override exists
anywhere in that workflow, confirmed by grep during this phase's
review). ROOT/SLATE_PATH/BETS_PATH are __file__-relative, so pointing a
subprocess at the REAL scripts/write_pending_bets.py with only cwd= set
would read/write the real repo's data/ -- sandboxed here by copying the
script + its one lib dependency into a tmp scripts/+lib/ tree first.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")


def _sandbox(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (tmp_path / "data").mkdir(exist_ok=True)
    shutil.copy(os.path.join(SCRIPTS_DIR, "write_pending_bets.py"), scripts_dir / "write_pending_bets.py")
    shutil.copy(os.path.join(LIB_DIR, "postponed_guard.py"), lib_dir / "postponed_guard.py")
    return scripts_dir / "write_pending_bets.py"


def _run(tmp_path, env=None):
    script_path = _sandbox(tmp_path)
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, str(script_path)], cwd=str(tmp_path),
        capture_output=True, text=True, env=run_env,
    )


def make_game():
    return {
        "away": {"abbr": "KC"}, "home": {"abbr": "WSH"}, "status": "Scheduled",
        "marketLedger": [{
            "market": "ML_Away", "confidenceTier": "HIGH", "status": "Accepted",
            "ticker": "T-1", "kalshiPrice": -120, "executablePriceUsed": 54.5, "betSize": 5.0,
        }],
    }


class TestSubprocessWorkflowCompatibility:

    def test_full_run_exits_0_via_subprocess(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        result = self._run_helper(tmp_path)
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert (tmp_path / "bets.json").exists()

    def test_missing_slate_json_exits_1_via_subprocess(self, tmp_path):
        result = self._run_helper(tmp_path)
        assert result.returncode == 1

    def _run_helper(self, tmp_path):
        return _run(tmp_path)

    def test_subprocess_never_touches_real_repo_data(self, tmp_path):
        real_bets_path = os.path.join(ROOT, "bets.json")
        before = None
        if os.path.exists(real_bets_path):
            with open(real_bets_path, "rb") as f:
                before = f.read()
        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        self._run_helper(tmp_path)
        after = None
        if os.path.exists(real_bets_path):
            with open(real_bets_path, "rb") as f:
                after = f.read()
        assert before == after, "real repository bets.json must never be touched by a sandboxed subprocess test"
