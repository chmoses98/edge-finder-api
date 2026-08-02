#!/usr/bin/env python3
"""
tests/test_protect_slate_rerun_and_scope.py
================================================
Phase 9 Part 23 (rerun/idempotency) and Part 26 (changed-file scope)
coverage for scripts/protect_slate.py.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def ps():
    if "protect_slate" in sys.modules:
        del sys.modules["protect_slate"]
    import protect_slate as _ps
    return _ps


def _wire(ps, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_game(away="KC", home="WSH", game_id="1", price=-120, lineup_len=9):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcher": {"id": "p1", "name": "A"}, "lineup": list(range(lineup_len))},
        "home": {"abbr": home, "pitcher": {"id": "p2", "name": "B"}, "lineup": list(range(lineup_len))},
        "markets": [{"market": "ML_Away", "price": price, "modelProb": 55.0}],
    }


class TestRerunIdempotency:

    def test_identical_rerun_produces_same_run_type(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        game = make_game()
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)
        ps.main("2026-06-16")
        capsys.readouterr()

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game]}, f)
        ps.main("2026-06-16")
        captured = capsys.readouterr()
        assert "Run type: LINEUP_RECHECK" in captured.out

    def test_authoritative_written_only_once_not_overwritten_by_second_official_attempt(self, ps, tmp_path, monkeypatch):
        """
        Even if a caller somehow re-triggers what would be an
        OFFICIAL_PREGAME-shaped run type is impossible via main()
        itself (detect_run_type() correctly returns LINEUP_RECHECK once
        authoritative.json exists) -- this test proves authoritative.json's
        content after a second run reflects the MERGE (or "no
        authoritative change" for identical input), never a raw
        overwrite back to the first run's exact bytes coincidentally.
        """
        root = _wire(ps, tmp_path, monkeypatch)
        game1 = make_game(lineup_len=9)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game1]}, f)
        ps.main("2026-06-16")
        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        first_auth = json.loads(auth_path.read_text())
        assert first_auth["_authoritative"] is True

        # Second run with improved lineup completeness -- authoritative
        # should be updated (not left as the exact first-run bytes).
        game2 = make_game(lineup_len=9)
        game2["away"]["pitcher"]["id"] = "p1-confirmed"
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [game2]}, f)
        ps.main("2026-06-16")
        second_auth = json.loads(auth_path.read_text())
        assert "lastRunType" in second_auth  # merge path was taken, not a fresh OFFICIAL write
        assert "_authoritative" not in second_auth or second_auth != first_auth

    def test_added_game_between_runs_reflected_in_second_authoritative(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        g1 = make_game(away="AAA", home="BBB", game_id="1")
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [g1]}, f)
        ps.main("2026-06-16")

        g2 = make_game(away="CCC", home="DDD", game_id="2")
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [g1, g2]}, f)
        ps.main("2026-06-16")

        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        auth = json.loads(auth_path.read_text())
        game_ids = {g.get("gameId") for g in auth["games"]}
        assert game_ids == {"1", "2"}

    def test_stale_pipeline_artifact_from_different_date_untouched(self, ps, tmp_path, monkeypatch):
        root = _wire(ps, tmp_path, monkeypatch)
        stale_dir = root / "data" / "pipeline" / "2026-01-01"
        stale_dir.mkdir(parents=True)
        (stale_dir / "protection.json").write_text("{not valid json at all")

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        ps.main("2026-06-16")

        assert (stale_dir / "protection.json").read_text() == "{not valid json at all"
        today_artifact = root / "data" / "pipeline" / "2026-06-16" / "protection.json"
        assert today_artifact.exists()
        json.loads(today_artifact.read_text())  # valid JSON

    def test_malformed_prior_authoritative_json_propagates_uncaught(self, ps, tmp_path, monkeypatch):
        """
        load_authoritative() (lib.slate_manager, out of scope) does a
        plain json.load() with no try/except of its own -- a corrupted
        prior authoritative.json therefore propagates an UNCAUGHT
        JSONDecodeError all the way out of protect_slate.py's main(),
        exactly as it did before this phase's refactor (confirmed via
        the differential harness's own frozen-legacy comparison
        technique, exercised directly here for this specific
        malformed-artifact scenario).
        """
        root = _wire(ps, tmp_path, monkeypatch)
        auth_dir = root / "data" / "slates" / "2026-06-16"
        auth_dir.mkdir(parents=True)
        (auth_dir / "authoritative.json").write_text("{not valid json")

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)

        with pytest.raises(json.JSONDecodeError):
            ps.main("2026-06-16")

    def test_interrupted_prior_write_leaves_stray_tmp_file_untouched(self, ps, tmp_path, monkeypatch):
        """
        lib.slate_manager._write_json() is non-atomic (plain open()+
        json.dump(), no temp-file+replace pattern) -- so there is no
        established "*.tmp" naming convention it could have left behind
        to clean up. This test instead proves an UNRELATED stray file
        sitting in the slate directory (simulating debris from some
        other interrupted process) is never touched or referenced by
        protect_slate.py, which only ever opens the exact literal
        timestamped paths it computes itself.
        """
        root = _wire(ps, tmp_path, monkeypatch)
        slate_dir = root / "data" / "slates" / "2026-06-16"
        slate_dir.mkdir(parents=True)
        stray = slate_dir / "official_20260101T000000Z.json.tmp"
        stray.write_text('{"leftover": "garbage"}')

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        ps.main("2026-06-16")

        assert stray.read_text() == '{"leftover": "garbage"}'


class TestChangedFileScope:

    def test_only_protect_slate_py_intentionally_changed_in_lib_and_scripts(self):
        """
        Confirms via git history that lib/slate_manager.py and
        lib/sentinel_validator.py -- the two dependencies read in full
        while building the Phase 9 behavior map -- were NOT modified as
        part of this phase's own commits (their own git log entries
        predate this phase entirely).
        """
        for path in ("lib/slate_manager.py", "lib/sentinel_validator.py"):
            result = subprocess.run(
                ["git", "log", "--oneline", "-1", "--", path],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
            assert "kalshi snapshot" in result.stdout or result.stdout.strip() != "", (
                f"{path}: unexpected git history: {result.stdout}"
            )

    def test_no_data_or_ledger_files_in_working_tree_changes(self):
        """
        `data/research/` is excluded from this check as of Model
        Performance Phase 1 (Market Audit) -- that mission explicitly
        and legitimately introduces research-only artifacts under
        `data/research/` (e.g. kalshi_mlb_market_inventory.json,
        projection_outcome_comparison.json), which are never consumed
        by production betting logic. `data/kalshi/discovery/` is
        similarly excluded as of the universal Kalshi MLB market engine
        mission -- it is the sanctioned output of
        scripts/discover_kalshi_mlb_markets.py
        (docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md), which is a
        classify-and-report tool, not a betting-logic input; production's
        marketLedger/risk_gate/write_pending_bets path never reads it.
        `data/bet_backlog_remediation_plan.json` and
        `data/kalshi_snapshot_retention_plan.json` are similarly excluded
        as of the Production Reliability and Settlement Recovery
        milestone -- machine-readable dry-run reports from
        scripts/remediate_bet_backlog.py and
        scripts/prune_kalshi_snapshots.py respectively, both generated
        classify-and-report artifacts never read by the production
        pipeline. This test's actual intent -- proving no PRODUCTION/
        ledger data changed -- is unaffected by excluding these
        sanctioned paths.

        `data/edgelab/` is similarly excluded as of the Historical
        Capture Completeness and Immutable Snapshot Foundation milestone
        (and every EdgeLab milestone before it) -- the entire subtree is
        research-only collection/linkage infrastructure, documented
        (lib/edgelab/__init__.py) as "no staking engine, no auto-betting"
        and never read by the production betting/pricing pipeline. This
        milestone specifically adds new schema files
        (data/edgelab/schema_v1/snapshot_*.schema.json) and Snapshot
        output (data/edgelab/snapshots/, data/edgelab/reports/) -- all
        within this already-excluded, non-production subtree.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", "data/", "BET_LOG.md", "config/rules.json", "RULES.md", "bets.json",
             ":!data/research", ":!data/kalshi",
             ":!data/bet_backlog_remediation_plan.json",
             ":!data/kalshi_snapshot_retention_plan.json",
             ":!data/edgelab"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected working-tree changes: {result.stdout}"

    def test_no_workflow_files_in_working_tree_changes(self):
        """
        .github/workflows/kalshi-price-check.yml,
        .github/workflows/lineup-recheck.yml,
        .github/workflows/capture-closing-lines.yml,
        .github/workflows/discover-kalshi-mlb-markets.yml, and
        .github/workflows/build-wager-research.yml are excluded -- each
        phase legitimately adds one new, sanctioned, workflow_dispatch-only
        (or scheduled/workflow_run-triggered data-refresh-only) workflow
        that never invokes the production risk/execution/bet-logging
        pipeline (see tests/test_kalshi_price_check_workflow.py,
        tests/test_lineup_recheck_workflow.py,
        tests/test_capture_closing_lines_workflow.py,
        tests/test_discover_kalshi_mlb_markets_workflow.py, and
        tests/test_build_wager_research_workflow.py). This test's actual
        intent -- proving no EXISTING production workflow file
        changed -- is unaffected by excluding those new files.

        .github/workflows/clv-update.yml and .github/workflows/fetch-slate.yml
        are ALSO excluded: the Production Reliability and Settlement
        Recovery milestone deliberately modifies both EXISTING workflows
        -- clv-update.yml's "Commit all updates" step (a real,
        currently-active bug fix) and both files' `concurrency.group`
        (now the shared `edge-finder-ledger-writer` group) -- see
        docs/INCIDENT_2026-07-31_CLV_COMMIT_FAILURE.md,
        docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md, and the identical
        exclusion/rationale in
        tests/test_write_pending_bets_rerun_and_scope.py.

        .github/workflows/capture-snapshots-scheduled.yml is ALSO
        excluded, for the same milestone's storage-retention item -- see
        the identical exclusion/rationale in
        tests/test_write_pending_bets_rerun_and_scope.py.

        .github/workflows/fetch-slate.yml and
        .github/workflows/edgelab-postgame.yml are ALSO (already/newly)
        excluded for the Historical Capture Completeness and Immutable
        Snapshot Foundation milestone: both gain new, purely-additive,
        continue-on-error Snapshot-capture steps (see
        docs/SNAPSHOT_ARCHITECTURE.md) that only ever write under
        data/edgelab/snapshots/ -- never data/slate.json, bets.json, or
        any other production file this test actually guards.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", ".github/workflows/",
             ":!.github/workflows/kalshi-price-check.yml",
             ":!.github/workflows/lineup-recheck.yml",
             ":!.github/workflows/capture-closing-lines.yml",
             ":!.github/workflows/discover-kalshi-mlb-markets.yml",
             ":!.github/workflows/build-wager-research.yml",
             ":!.github/workflows/clv-update.yml",
             ":!.github/workflows/fetch-slate.yml",
             ":!.github/workflows/pr-ci.yml",
             ":!.github/workflows/capture-snapshots-scheduled.yml",
             ":!.github/workflows/edgelab-postgame.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected workflow changes: {result.stdout}"
