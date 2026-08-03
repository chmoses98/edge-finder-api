#!/usr/bin/env python3
"""
tests/edgelab/test_production_provenance.py
================================================
Forward Replay Corpus and Production Provenance milestone: coverage for
scripts/capture_production_provenance.py, lib/edgelab/snapshot.py's
PRODUCTION_PROVENANCE component + expanded capture_effective_config(),
scripts/run_forward_replay.py, scripts/corpus_health_report.py,
scripts/snapshot_storage_report.py's replay-runs bucket, and
scripts/check_snapshot_capture.py's per-current-run detection fix.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree -- same discipline as
tests/edgelab/test_snapshot.py / test_replay.py.
"""
import gzip
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402
import scripts.capture_production_provenance as capture_provenance  # noqa: E402

DATE = "2026-07-31"


def _write(path, obj_or_bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(obj_or_bytes, (bytes, bytearray)):
        with open(path, "wb") as f:
            f.write(obj_or_bytes)
    else:
        with open(path, "w") as f:
            json.dump(obj_or_bytes, f)


def _write_pipeline_artifact(stage, date, data, produced_by, created_at=None):
    if created_at is None:
        pipeline_artifacts.write_stage_artifact(stage, date, data, produced_by=produced_by)
        return
    path = pipeline_artifacts.artifact_path(stage, date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "meta": {"stage": stage, "slateDate": date, "createdAt": created_at,
                     "schemaVersion": "1.0", "producedBy": produced_by,
                     "status": "transitional", "sourceStage": None},
            "data": data,
        }, f)


def _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at=None, provenance_commit_sha=None):
    """Same convention as tests/edgelab/test_snapshot.py's fixture of the
    same name. provenance_commit_sha=False omits the provenance artifact
    entirely (MISSING case); a string pins a specific commitSha; None uses
    a default real-shaped value."""
    monkeypatch.chdir(tmp_path)

    _write_pipeline_artifact(
        "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
        "scripts/build_market_ledger.py", created_at=recommendations_created_at,
    )
    _write_pipeline_artifact("projections", DATE, {"games": []}, "scripts/build_market_ledger.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("normalized_slate", DATE, {"games": []}, "scripts/enrich_data.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("execution", DATE, {"rulesVersion": "1.0", "candidates": []}, "scripts/risk_gate.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("validation", DATE, {"errors": []}, "scripts/validate_slate_final.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("protection", DATE, {"runType": "OFFICIAL_PREGAME"}, "scripts/protect_slate.py", created_at=recommendations_created_at)
    if provenance_commit_sha is not False:
        _write_pipeline_artifact(
            "provenance", DATE,
            {"commitSha": provenance_commit_sha or ("deadbeef" * 5), "workflowRunId": "123456",
             "workflowRunAttempt": "1", "ref": "refs/heads/main", "refName": "main",
             "repository": "chmoses98/edge-finder-api", "workflow": "Fetch Slate Data",
             "job": "fetch", "eventName": "push"},
            "scripts/capture_production_provenance.py", created_at=recommendations_created_at,
        )

    _write(os.path.join("data", "slates", DATE, "authoritative.json"), {"date": DATE, "games": []})
    _write(os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{DATE}.json"), {"markets": []})
    _write(os.path.join("data", "weather.json"), {"parks": [{"team": "SD", "temp": 72}]})
    _write(os.path.join("data", "bullpen.json"), {"bullpens": {"SD": {"era": 4.0}}})
    observations_path = os.path.join("data", "edgelab", "observations", f"{DATE}.jsonl.gz")
    os.makedirs(os.path.dirname(observations_path), exist_ok=True)
    with gzip.open(observations_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"marketTicker": "KXMLBGAME-TEST-AAA", "capturedAt": f"{DATE}T20:00:00Z"}) + "\n")
    _write(
        os.path.join("config", "rules.json"),
        {"_version": "1.0", "calibration": {}, "edge_thresholds": {}, "base_sizes": {"High": 4.0},
         "multipliers": {}, "market_list": [], "validation": {"required_per_game": [], "required_per_market_row": [],
                                                                "rejection_required_if_no_bet": True,
                                                                "min_qualifying_bets_full_slate": 12}},
    )


# ── Item 2: authoritative productionCommitSha capture ────────────────────

class TestProductionProvenanceCapture:
    def test_capture_script_writes_pipeline_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_SHA", "abc123def456")
        monkeypatch.setenv("GITHUB_RUN_ID", "999")
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
        monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
        monkeypatch.setenv("GITHUB_REF_NAME", "main")
        monkeypatch.setenv("GITHUB_REPOSITORY", "chmoses98/edge-finder-api")
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == "abc123def456"
        assert payload["workflowRunId"] == "999"
        assert payload["workflowRunAttempt"] == "2"
        assert payload["ref"] == "refs/heads/main"
        assert payload["repository"] == "chmoses98/edge-finder-api"
        envelope = pipeline_artifacts.read_stage_artifact("provenance", DATE)
        assert envelope["data"]["commitSha"] == "abc123def456"

    def test_capture_script_falls_back_to_local_git_when_no_github_sha(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.setattr(capture_provenance, "_local_git_commit_sha", lambda: "local-fallback-sha")
        payload = capture_provenance.capture_provenance(DATE)
        assert payload["commitSha"] == "local-fallback-sha"

    def test_snapshot_captures_real_production_commit_sha(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha="realcommitsha123")
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "realcommitsha123")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] == "realcommitsha123"
        assert manifest["productionProvenance"]["status"] == "CAPTURED"
        assert manifest["productionProvenance"]["workflowRunId"] == "123456"
        assert manifest["productionProvenance"]["ref"] == "refs/heads/main"
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.AVAILABLE

    def test_missing_provenance_downgrades_completeness_to_missing_required_input(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] is None
        assert manifest["productionProvenance"]["status"] == "MISSING"
        assert manifest["completenessStatus"] == snap.MISSING_REQUIRED_INPUT
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.MISSING

    def test_ambiguous_commit_sha_never_trusted(self, tmp_path, monkeypatch):
        """Maintainer-review-grade honesty check: if the captured
        provenance commitSha disagrees with the live snapshotWriterCommitSha
        (same job, no re-checkout in between -- they must always agree),
        productionCommitSha must NOT be trusted, ever."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha="stale-commit-sha")
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "actually-different-sha")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        manifest = result["manifest"]
        assert manifest["productionCommitSha"] is None
        assert manifest["productionProvenance"]["status"] == "AMBIGUOUS"
        assert manifest["completenessStatus"] in (snap.PARTIAL_REPLAY, snap.MISSING_REQUIRED_INPUT)
        provenance_component = next(c for c in manifest["components"] if c["componentType"] == "PRODUCTION_PROVENANCE")
        assert provenance_component["availabilityStatus"] == snap.PARTIAL
        assert provenance_component["limitationReason"] == "PRODUCTION_COMMIT_AMBIGUOUS"

    def test_never_reconstructed_from_ingestion_time_git_state_alone(self, tmp_path, monkeypatch):
        """Even if the live checkout's git state DOES resolve a real SHA,
        productionCommitSha must come from the EARLY-captured provenance
        artifact, never be silently synthesized from
        snapshotWriterCommitSha when the artifact itself is missing."""
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "some-real-git-sha")
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["productionCommitSha"] is None


# ── Multiple runs / rerun-from-changed-commit ─────────────────────────────

class TestMultipleProductionRunsAndReruns:
    def test_two_production_runs_same_date_get_distinct_snapshots_and_commits(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z", provenance_commit_sha="commitA")
        result1 = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        # A genuine rerun (lineup recheck): new recommendations.json
        # createdAt (-> new productionRunKey) AND a real commit change
        # (e.g. a hotfix deployed between runs).
        _write_pipeline_artifact(
            "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
            "scripts/build_market_ledger.py", created_at="2026-07-31T21:00:00Z",
        )
        _write_pipeline_artifact(
            "provenance", DATE,
            {"commitSha": "commitB", "workflowRunId": "999999", "workflowRunAttempt": "1",
             "ref": "refs/heads/main", "refName": "main", "repository": "chmoses98/edge-finder-api",
             "workflow": "Fetch Slate Data", "job": "fetch", "eventName": "push"},
            "scripts/capture_production_provenance.py", created_at="2026-07-31T21:00:00Z",
        )
        monkeypatch.setattr(snap, "_git_commit_sha", lambda: "commitB")
        result2 = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        assert result1["manifest"]["snapshotId"] != result2["manifest"]["snapshotId"]
        # No live git checkout to cross-check against for the first run
        # (tmp_path is not a real git repo) -- the captured provenance
        # value is trusted as-is, since there's no disagreement to detect.
        assert result1["manifest"]["productionCommitSha"] == "commitA"
        assert result2["manifest"]["productionCommitSha"] == "commitB"
        assert len(snap.list_pregame_run_dirs(DATE)) == 2


# ── Item 3: effective-config capture ──────────────────────────────────────

class TestEffectiveConfigCapture:
    def test_live_constants_introspected_from_real_modules(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1", production_commit_sha="prodsha", production_run_id="runkey")
        assert record["liveConstants"]["THRESHOLD_HIGH"] == 3.0
        assert record["liveConstants"]["REAL_MONEY_TIERS"] == ["HIGH", "MEDIUM"]
        assert "REQUIRED_MARKETS" in record["liveConstants"]
        assert record["productionCommitSha"] == "prodsha"
        assert record["productionRunId"] == "runkey"

    def test_hardcoded_logic_source_hashes_present_and_stable(self, tmp_path, monkeypatch):
        # Deliberately NOT chdir'd to tmp_path here: the source files this
        # hashes (scripts/build_market_ledger.py etc.) only exist relative
        # to the real repo root -- this proves the real-checkout behavior.
        record1 = snap.capture_effective_config(DATE, "sha1")
        record2 = snap.capture_effective_config(DATE, "sha1")
        assert record1["hardcodedLogicSourceHashes"] == record2["hardcodedLogicSourceHashes"]
        assert all(h is not None for h in record1["hardcodedLogicSourceHashes"].values())

    def test_source_hash_honestly_null_when_file_does_not_exist_at_cwd(self, tmp_path, monkeypatch):
        """CWD-relative by the same convention as every other path in this
        module -- a chdir'd-away cwd (no scripts/ directory present) must
        report None, never crash or silently resolve against ROOT_DIR."""
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1")
        assert all(h is None for h in record["hardcodedLogicSourceHashes"].values())

    def test_unrepresented_logic_explicitly_classified(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record = snap.capture_effective_config(DATE, "sha1")
        assert len(record["unrepresentedLogic"]) > 0
        for entry in record["unrepresentedLogic"]:
            assert "description" in entry and "location" in entry

    def test_effective_config_hash_is_deterministic_and_content_bound(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        record1 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p1")
        record2 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p1")
        record3 = snap.capture_effective_config(DATE, "sha1", production_commit_sha="p2")
        assert record1["effectiveConfigHash"] == record2["effectiveConfigHash"]
        assert record1["effectiveConfigHash"] != record3["effectiveConfigHash"]


# ── Item 6: automatic candidate replay ────────────────────────────────────

class TestAutomaticForwardReplay:
    def test_no_snapshot_yet_is_skipped_honestly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        status_path = os.path.join(tmp_path, "data", "edgelab", "forward_replay_status.json")
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["outcome"] == "no_snapshot"

    def test_valid_snapshot_produces_completed_replay(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["runStatus"] == "COMPLETED"
        assert output["eligibilityStatus"] == "ELIGIBLE_LEVEL_2"
        status_path = os.path.join(tmp_path, "data", "edgelab", "forward_replay_status.json")
        with open(status_path) as f:
            status = json.load(f)
        assert status[DATE]["runStatus"] == "COMPLETED"

    def test_forward_replay_never_writes_production_files(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        _write("data/bets.json", {"bets": ["SENTINEL"]})
        with open("data/bets.json", "rb") as f:
            before = f.read()

        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        with open("data/bets.json", "rb") as f:
            assert f.read() == before

    def test_ineligible_snapshot_is_rejected_not_downgraded(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, provenance_commit_sha=False)
        os.remove(os.path.join("data", "weather.json"))
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0  # never fails the workflow
        output = json.loads(result.stdout)
        assert output["runStatus"] == "REJECTED_INELIGIBLE"


def _make_minimal_game():
    return {
        'gameId': '555555',
        'away': {'abbr': 'AAA', 'team': 'Away Team', 'pitcher': {'name': 'SP Away'},
                  'pitcherSavant': {'xFIP': 5.5, 'seasonFIP': 5.5, 'recentFIP': 5.4, 'avgIPperStart': 5.0,
                                     'openerRole': False, 'ttoSplit': 0.3, 'ttoAvailable': True,
                                     'tto1': {'fip': 5.5, 'gamesUsed': 5}, 'tto3': {'fip': 5.2, 'gamesUsed': 3}},
                  'bullpen': {'xFIP': 4.5, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.5}},
        'home': {'abbr': 'HHH', 'team': 'Home Team', 'pitcher': {'name': 'SP Home'},
                  'pitcherSavant': {'xFIP': 3.5, 'seasonFIP': 3.5, 'recentFIP': 3.4, 'avgIPperStart': 6.0,
                                     'openerRole': False, 'ttoSplit': 0.1, 'ttoAvailable': True,
                                     'tto1': {'fip': 3.5, 'gamesUsed': 5}, 'tto3': {'fip': 3.4, 'gamesUsed': 3}},
                  'bullpen': {'xFIP': 3.8, 'hlGrade': 'ABOVE_AVERAGE', 'hlAvailable': True, 'hlXFIP': 3.7}},
        'awayTeamStats': {'offenseBaselineAdj': 5.2, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
                           'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
                           'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
                           'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
                           'lineupStatusReason': '', 'lineupAdj': 0.05},
        'homeTeamStats': {'offenseBaselineAdj': 4.0, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
                           'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
                           'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
                           'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
                           'lineupStatusReason': '', 'lineupAdj': 0.02},
        'park': {'parkFactor': 100}, 'pinnacleVF': {'away': 48.0, 'home': 52.0},
        'oddsApiCommenceTime': '2026-07-31T19:45:00Z', 'kalshiKey': 'AAAHH', 'kalshiGameTime': '1545',
        'odds': {'kalshi': {
            'ml': {'away': -130, 'home': 120, 'away_ticker': 'KXMLBGAME-26JUL311545AAAHH-AAA',
                   'home_ticker': 'KXMLBGAME-26JUL311545AAAHH-HHH', 'source': 'kalshi_registry'},
            'nrfi_yrfi': {'ticker': 'KXMLBRFI-26JUL311545AAAHH', 'nrfi_american': -115, 'yrfi_american': 108,
                          'nrfi_implied': 53.0, 'yrfi_implied': 47.0, 'source': 'kalshi_registry'},
            'f5ml': {'away': -120, 'home': 110, 'away_ticker': 'KXMLBF5-26JUL311545AAAHH-AAA',
                     'home_ticker': 'KXMLBF5-26JUL311545AAAHH-HHH', 'source': 'kalshi_registry'},
            'team_totals': {
                'away': {'best_ticker': 'KXMLBTEAMTOTAL-26JUL311545AAAHH-AAA5', 'line': 5, 'american': 120, 'implied_pct': 44.0},
                'home': {'best_ticker': 'KXMLBTEAMTOTAL-26JUL311545AAAHH-HHH4', 'line': 4, 'american': 130, 'implied_pct': 43.0},
            },
            'rl': {'best_ticker': 'KXMLBSPREAD-26JUL311545AAAHH-HHH2', 'american': 133, 'implied_pct': 43.0, 'team': 'HHH'},
            'total': {'best_ticker': 'KXMLBTOTAL-26JUL311545AAAHH-9', 'line': 8, 'american': -105},
        }},
    }


def _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game):
    from scripts.build_market_ledger import compute_game_projection_context, evaluate_game
    from scripts import risk_gate as _risk_gate
    from lib.edgelab import replay as _replay

    _wire_full_pregame_fixture(tmp_path, monkeypatch)
    projection_context = compute_game_projection_context(game)
    ledger = evaluate_game(game, projection_context)
    slate = {"date": DATE, "games": [{**game, "marketLedger": ledger}]}
    _risk_gate.apply_tt_safety(slate)
    decision, report = _risk_gate.apply_portfolio_rules(slate)
    if decision == "PAPER_ONLY":
        _replay._apply_paper_only_downgrade(slate, report["decision_reason"])
    execution_payload = _risk_gate.build_execution_artifact_payload(slate, decision, report["decision_reason"])

    _write_pipeline_artifact("execution", DATE, execution_payload, "scripts/risk_gate.py")
    _write_pipeline_artifact(
        "recommendations", DATE,
        {"games": [{"gameId": game["gameId"], "away": game["away"], "home": game["home"], "marketLedger": ledger}]},
        "scripts/build_market_ledger.py",
    )
    _write_pipeline_artifact("normalized_slate", DATE, {"games": [game]}, "scripts/enrich_data.py")


# ── Item 8: CLV closing-quote disambiguation ──────────────────────────────

class TestClosingClvDisambiguation:
    def test_only_isclosingquote_row_is_used(self):
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": False, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert resolved["T1"]["yesBid"] == 60
        assert ambiguous == []

    def test_no_closing_flagged_row_is_unresolved_not_guessed(self):
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": False, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "FIRST_DAILY", "isClosingQuote": False, "yesBid": 5, "yesAsk": 8},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert "T1" not in resolved
        assert ambiguous == []
        linkage = replay._clv_linkage_for_ticker(resolved, None, "T1", 50.0)
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "NO_CLV_QUOTE_FOR_THIS_MARKET"

    def test_multiple_closing_flagged_rows_is_ambiguous(self):
        """A genuine upstream data-quality issue -- never expected in
        practice, but must be reported, never silently resolved by
        picking whichever one happens to iterate last."""
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_90", "isClosingQuote": True, "yesBid": 10, "yesAsk": 15},
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
        ]
        resolved, ambiguous = replay._closing_clv_by_ticker(rows)
        assert "T1" not in resolved
        assert ambiguous == ["T1"]

    def test_last_row_in_file_order_is_not_silently_preferred(self):
        """Direct regression test for the real defect found against
        2026-08-02 data: 620/4844 tickers had multiple rows, and the OLD
        dict-comprehension logic silently kept whichever was last in the
        file -- here the LAST row is deliberately the non-closing one."""
        rows = [
            {"marketTicker": "T1", "checkpoint": "T_MINUS_5", "isClosingQuote": True, "yesBid": 60, "yesAsk": 64},
            {"marketTicker": "T1", "checkpoint": "POST_START", "isClosingQuote": False, "yesBid": 1, "yesAsk": 2},
        ]
        resolved, _ambiguous = replay._closing_clv_by_ticker(rows)
        assert resolved["T1"]["yesBid"] == 60


# ── Item 4: missing-snapshot detection distinguishes reruns ───────────────

class TestPerRunSnapshotDetection:
    def test_current_production_run_key_matches_latest_recommendations(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        assert snap.current_production_run_key(DATE) == "2026-07-31T20:00:00Z"

    def test_second_run_without_its_own_snapshot_is_detected_as_missing(self, tmp_path, monkeypatch):
        """Maintainer-review-grade fix: an EARLIER run's snapshot existing
        must not hide a LATER (current) run's own missing snapshot."""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import check_snapshot_capture as checker
        import importlib
        importlib.reload(checker)

        _wire_full_pregame_fixture(tmp_path, monkeypatch, recommendations_created_at="2026-07-31T20:00:00Z")
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert checker._has_pregame_snapshot_for_current_run(DATE) is True

        # A second, later run overwrites recommendations.json with a new
        # run key -- its OWN snapshot has not been captured yet.
        _write_pipeline_artifact(
            "recommendations", DATE, {"games": [{"gameId": "1", "marketLedger": []}]},
            "scripts/build_market_ledger.py", created_at="2026-07-31T21:30:00Z",
        )
        assert checker._has_pregame_snapshot_for_current_run(DATE) is False


# ── Item 10: corpus health report ─────────────────────────────────────────

class TestCorpusHealthReport:
    def test_report_runs_and_reflects_real_captured_state(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["productionRuns"] == 1
        assert report["snapshotsSuccessfullyCaptured"] == 1
        assert report["candidateReplays"]["completed"] == 1
        assert report["missingSnapshots"] == []
        report_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.json")
        assert os.path.exists(report_path)
        md_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.md")
        assert os.path.exists(md_path)

    def test_missing_snapshot_is_flagged_degraded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
        _write(os.path.join("data", "pipeline", DATE, "recommendations.json"),
               {"meta": {"createdAt": "2026-07-31T20:00:00Z"}, "data": {"games": []}})
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "corpus_health_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        report = json.loads(result.stdout)
        assert DATE in report["missingSnapshots"]
        report_path = os.path.join(tmp_path, "data", "edgelab", "reports", "corpus_health_report.json")
        with open(report_path) as f:
            full_report = json.load(f)
        rec = next(r for r in full_report["perDate"] if r["date"] == DATE)
        assert rec["gateStatus"] == "DEGRADED_MISSING_SNAPSHOT"


# ── Item 12: storage report replay-runs bucket ────────────────────────────

class TestStorageReportReplayBucket:
    def test_replay_bucket_reflects_real_replay_output(self, tmp_path, monkeypatch):
        game = _make_minimal_game()
        _wire_full_pregame_fixture_with_game(tmp_path, monkeypatch, game)
        snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "run_forward_replay.py"), DATE],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "snapshot_storage_report.py")],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["replayRuns"]["runs"] == 1
        assert report["replayRuns"]["totalBytes"] > 0
        assert "1Season" in report["totalProjectedBytes"]
        assert "3Seasons" in report["totalProjectedBytes"]
        assert "5Seasons" in report["totalProjectedBytes"]
