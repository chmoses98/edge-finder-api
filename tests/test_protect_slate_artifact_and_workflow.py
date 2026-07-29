#!/usr/bin/env python3
"""
tests/test_protect_slate_artifact_and_workflow.py
======================================================
Phase 9 Part 16 (protection.json artifact schema/failure-isolation)
and Part 21 (workflow/subprocess compatibility) coverage for
scripts/protect_slate.py.

Part 21: .github/workflows/fetch-slate.yml invokes this script as
`python3 scripts/protect_slate.py "${{ env.DATE }}"`, no other args,
immediately after `validate_slate_final.py` ("Final validate slate")
and immediately before "Publish authoritative slate + metadata"
(BLOCK 6) -- confirmed by reading the workflow file directly. No
`continue-on-error:` on this step -- a nonzero exit fails the whole
job, and nothing in BLOCK 6/7 (publish_slate, risk_gate,
write_pending_bets, validate_bet_logging, write_tracked_tickers,
capture_closing_lines) runs.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def ps():
    if "protect_slate" in sys.modules:
        del sys.modules["protect_slate"]
    import protect_slate as _ps
    return _ps


def _wire(ps, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_game(away="KC", home="WSH", game_id="1", price=-120):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcher": {"id": "p1", "name": "A"}, "lineup": list(range(9))},
        "home": {"abbr": home, "pitcher": {"id": "p2", "name": "B"}, "lineup": list(range(9))},
        "markets": [{"market": "ML_Away", "price": price, "modelProb": 55.0}],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Part 16: protection.json artifact schema + failure isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildProtectionArtifactPayloadPure:

    def test_ok_status_for_official_pregame(self, ps):
        result = {"savedPaths": ["a", "b"], "authoritativeWritten": True}
        payload = ps.build_protection_artifact_payload(
            "2026-06-16", "OFFICIAL_PREGAME", [], result, True, True,
        )
        assert payload["status"] == "ok"
        assert payload["runType"] == "OFFICIAL_PREGAME"
        assert payload["sentinelCount"] == 0

    def test_quarantined_status_for_rejected_contaminated(self, ps):
        result = {"savedPaths": ["x"], "runReport": {"quarantined": True}}
        payload = ps.build_protection_artifact_payload(
            "2026-06-16", "REJECTED_CONTAMINATED",
            [{"path": "a", "value": 19900, "type": "sentinel_american_price"}],
            result, False, False,
        )
        assert payload["status"] == "quarantined"
        assert payload["sentinelCount"] == 1

    def test_narrow_schema_no_per_game_detail_no_settlement_fields(self, ps):
        result = {"savedPaths": [], "runReport": {
            "acceptedCount": 1, "rejectedCount": 0, "frozenCount": 0, "quarantined": False,
            "accepted": [{"gamePk": "1", "action": "UPDATED"}],  # per-game detail -- must be excluded
        }}
        payload = ps.build_protection_artifact_payload(
            "2026-06-16", "LINEUP_RECHECK", [], result, True, True,
        )
        assert set(payload.keys()) == {
            "date", "runType", "status", "sentinelCount", "savedPaths",
            "authoritativeWritten", "authoritativeUpdated", "runReportSummary",
            "syncedLegacySlateJson", "authoritativeExists",
        }
        assert "accepted" not in payload["runReportSummary"]
        assert set(payload["runReportSummary"].keys()) == {
            "acceptedCount", "rejectedCount", "frozenCount", "quarantined",
        }
        assert "pnl" not in payload
        assert "settlement" not in payload

    def test_run_report_summary_none_when_no_run_report(self, ps):
        payload = ps.build_protection_artifact_payload(
            "2026-06-16", "OFFICIAL_PREGAME", [], {"savedPaths": []}, True, True,
        )
        assert payload["runReportSummary"] is None

    def test_does_not_mutate_result_argument(self, ps):
        import copy
        result = {"savedPaths": ["a"], "runReport": {"acceptedCount": 1}}
        before = copy.deepcopy(result)
        ps.build_protection_artifact_payload("2026-06-16", "OFFICIAL_PREGAME", [], result, True, True)
        assert result == before

    def test_does_not_mutate_sentinels_argument(self, ps):
        """
        PR #10 hardening review: test_does_not_mutate_result_argument
        covered `result` but not the `sentinels` argument -- a real gap
        given evaluate_sentinel_gate_pure's own sibling non-mutation
        test exists for the identical parameter shape.
        """
        import copy
        sentinels = [{"path": "a.price", "value": 19900, "type": "sentinel_american_price"}]
        before = copy.deepcopy(sentinels)
        ps.build_protection_artifact_payload(
            "2026-06-16", "REJECTED_CONTAMINATED", sentinels, {"savedPaths": []}, False, False,
        )
        assert sentinels == before


class TestProtectionArtifactWiredIntoMain:

    def test_writes_artifact_with_ok_status_on_first_official_run(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        ps.main("2026-06-16")

        artifact_path = root / "data" / "pipeline" / "2026-06-16" / "protection.json"
        assert artifact_path.exists()
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope["meta"]["stage"] == "protection"
        assert envelope["meta"]["producedBy"] == "scripts/protect_slate.py"
        assert envelope["meta"]["sourceStage"] == "validation"
        assert envelope["data"]["status"] == "ok"
        assert envelope["data"]["runType"] == "OFFICIAL_PREGAME"

    def test_writes_artifact_with_quarantined_status_on_sentinel_run(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(price=19900)]}, f)
        ps.main("2026-06-16")

        artifact_path = root / "data" / "pipeline" / "2026-06-16" / "protection.json"
        assert artifact_path.exists()
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope["data"]["status"] == "quarantined"
        assert envelope["data"]["sentinelCount"] == 1

    def test_artifact_write_failure_does_not_change_return_value_or_legacy_files(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)

        # protect_slate.py imports via `from lib.pipeline_artifacts import
        # write_stage_artifact` (package-qualified, matching its own
        # `from lib.slate_manager import ...` convention) -- patching a
        # bare `pipeline_artifacts` import would target a DIFFERENT
        # sys.modules cache entry and silently not take effect (found
        # the hard way while writing this test).
        import lib.pipeline_artifacts as pipeline_artifacts

        def _boom(*a, **kw):
            raise OSError("simulated disk-full")

        monkeypatch.setattr(pipeline_artifacts, "write_stage_artifact", _boom)

        before_slate = (root / "data" / "slate.json").read_bytes()
        result = ps.main("2026-06-16")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING: could not write protection pipeline artifact" in captured.out
        assert not (root / "data" / "pipeline").exists()
        # legacy slate.json sync still happened normally
        after_slate = (root / "data" / "slate.json").read_bytes()
        assert after_slate != before_slate  # synced to match authoritative.json
        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        assert auth_path.exists()

    def test_artifact_never_written_on_missing_slate_json_path(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            ps.main("2026-06-16")
        assert not (root / "data" / "pipeline").exists()

    def test_artifact_never_written_on_malformed_json_path(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        (root / "data" / "slate.json").write_text("{not valid")
        with pytest.raises(SystemExit):
            ps.main("2026-06-16")
        assert not (root / "data" / "pipeline").exists()


# ══════════════════════════════════════════════════════════════════════════════
# Part 21: workflow / subprocess compatibility
# ══════════════════════════════════════════════════════════════════════════════

class TestSubprocessWorkflowCompatibility:
    """
    protect_slate.py's ROOT_DIR is __file__-relative (dirname(dirname(
    abspath(__file__)))), same class of hazard as build_market_ledger.py's
    own subprocess tests already documented: pointing a subprocess at
    the real scripts/protect_slate.py with only cwd= set does NOT
    sandbox it -- __file__ still resolves to the real repo regardless
    of cwd, so it would read/write the REAL repository's data/. Fixed
    by copying the script and its lib dependencies into a sandboxed
    tmp scripts/ + lib/ tree and invoking THAT copy.
    """

    def _sandbox(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        (tmp_path / "data").mkdir(exist_ok=True)
        shutil.copy(os.path.join(SCRIPTS_DIR, "protect_slate.py"), scripts_dir / "protect_slate.py")
        for f in ("slate_manager.py", "sentinel_validator.py", "pipeline_artifacts.py", "atomic_json.py"):
            shutil.copy(os.path.join(LIB_DIR, f), lib_dir / f)
        # lib is imported as a package (`from lib.slate_manager import ...`)
        (lib_dir / "__init__.py").write_text("")
        return scripts_dir / "protect_slate.py"

    def _run(self, tmp_path, args, env=None):
        script_path = self._sandbox(tmp_path)
        cmd = [sys.executable, str(script_path)] + args
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True, env=run_env)

    def test_full_official_pregame_run_exits_0_via_subprocess(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        result = self._run(tmp_path, ["2026-06-16"])
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Run type: OFFICIAL_PREGAME" in result.stdout
        assert (tmp_path / "data" / "slates" / "2026-06-16" / "authoritative.json").exists()

    def test_missing_slate_json_exits_1_via_subprocess(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        result = self._run(tmp_path, ["2026-06-16"])
        assert result.returncode == 1
        assert "data/slate.json not found" in result.stderr

    def test_sentinel_quarantine_exits_0_via_subprocess(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(price=19900)]}, f)
        result = self._run(tmp_path, ["2026-06-16"])
        assert result.returncode == 0
        assert "SENTINEL PRICES DETECTED" in result.stdout
        assert "REJECTED_CONTAMINATED" in result.stdout

    def test_cwd_relative_pipeline_root_diverges_from_root_dir_when_invoked_elsewhere(
        self, tmp_path,
    ):
        """
        PR #10 hardening review, Part 9: independently re-verifies the
        documented CWD-relative lib.pipeline_artifacts.PIPELINE_ROOT
        hazard by invoking the sandboxed script with `cwd` set to a
        directory OTHER than ROOT_DIR (the actual production workflow
        never does this -- .github/workflows/fetch-slate.yml sets no
        working-directory override anywhere, so every step's cwd is
        always the checkout root == ROOT_DIR -- but this proves the
        hazard exists for any invocation pattern that isn't the exact
        one production uses, and that Phase 9 did not worsen it).

        Every ROOT_DIR-anchored file (data/slate.json,
        data/slates/<date>/authoritative.json) must still land under
        ROOT_DIR regardless of cwd. Only protection.json -- the one
        write that goes through write_stage_artifact()'s bare
        PIPELINE_ROOT = os.path.join("data", "pipeline") -- must land
        under the DIFFERENT cwd instead, proving the divergence is
        real and isolated to exactly that one write path.
        """
        script_path = self._sandbox(tmp_path)
        elsewhere = tmp_path.parent / f"{tmp_path.name}_elsewhere"
        elsewhere.mkdir()

        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)

        result = subprocess.run(
            [sys.executable, str(script_path), "2026-06-16"],
            cwd=str(elsewhere), capture_output=True, text=True, env=dict(os.environ),
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

        # ROOT_DIR-anchored files: correct regardless of cwd.
        assert (tmp_path / "data" / "slates" / "2026-06-16" / "authoritative.json").exists()
        assert not (elsewhere / "data" / "slates").exists()

        # protection.json: diverges to cwd, NOT ROOT_DIR -- the hazard.
        assert (elsewhere / "data" / "pipeline" / "2026-06-16" / "protection.json").exists()
        assert not (tmp_path / "data" / "pipeline").exists()

    def test_protection_artifact_written_via_subprocess(self, tmp_path):
        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        result = self._run(tmp_path, ["2026-06-16"])
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        artifact_path = tmp_path / "data" / "pipeline" / "2026-06-16" / "protection.json"
        assert artifact_path.exists()

    def test_subprocess_run_never_touches_real_repo_data_directory(self, tmp_path):
        real_slate = os.path.join(ROOT, "data", "slate.json")
        with open(real_slate, "rb") as f:
            before = f.read()
        real_pipeline_dir_existed = os.path.isdir(os.path.join(ROOT, "data", "pipeline", "2026-06-16"))

        (tmp_path / "data").mkdir(exist_ok=True)
        with open(tmp_path / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        self._run(tmp_path, ["2026-06-16"])

        with open(real_slate, "rb") as f:
            after = f.read()
        assert before == after
        assert os.path.isdir(os.path.join(ROOT, "data", "pipeline", "2026-06-16")) == real_pipeline_dir_existed
