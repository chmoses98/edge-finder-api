#!/usr/bin/env python3
"""
tests/edgelab/test_replay.py
================================
Level 2 Historical Replay Engine milestone: coverage for
lib/edgelab/replay.py + scripts/run_replay.py + data/edgelab/schema_v1/
replay_run.schema.json + replay_result.schema.json.

Every test runs inside an isolated tmp_path (monkeypatch.chdir), never
against the real repository's data/ tree -- same discipline as
tests/edgelab/test_snapshot.py.
"""
import gzip
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import lib.pipeline_artifacts as pipeline_artifacts  # noqa: E402
from lib.edgelab import ids  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import schema  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402
from scripts import risk_gate as _risk_gate  # noqa: E402
from scripts.build_market_ledger import compute_game_projection_context, evaluate_game  # noqa: E402

DATE = "2026-07-31"


# ── Fixtures shared across this file ─────────────────────────────────────

def _write(path, obj_or_bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(obj_or_bytes, (bytes, bytearray)):
        with open(path, "wb") as f:
            f.write(obj_or_bytes)
    else:
        with open(path, "w") as f:
            json.dump(obj_or_bytes, f)


def _write_pipeline_artifact(stage, date, data, produced_by, created_at=None):
    """Same convention as tests/edgelab/test_snapshot.py's helper of the
    same name -- lets a test pin meta.createdAt so temporal-skew behavior
    doesn't depend on real wall-clock time relative to a fixed DATE."""
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


def _make_game():
    """
    Minimal but real game dict suitable for compute_game_projection_context()
    / evaluate_game() -- adapted from tests/test_lineup_gate.py's
    _make_game() (both lineups confirmed, default odds qualify YRFI/TT/ML
    for a real edge) so this file exercises the ACTUAL production pricing
    functions rather than a hand-rolled substitute.
    """
    return {
        'gameId': '555555',
        'away': {
            'abbr': 'AAA', 'team': 'Away Team', 'pitcher': {'name': 'SP Away'},
            'pitcherSavant': {
                'xFIP': 5.5, 'seasonFIP': 5.5, 'recentFIP': 5.4,
                'avgIPperStart': 5.0, 'openerRole': False,
                'ttoSplit': 0.3, 'ttoAvailable': True,
                'tto1': {'fip': 5.5, 'gamesUsed': 5}, 'tto3': {'fip': 5.2, 'gamesUsed': 3},
            },
            'bullpen': {'xFIP': 4.5, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.5},
        },
        'home': {
            'abbr': 'HHH', 'team': 'Home Team', 'pitcher': {'name': 'SP Home'},
            'pitcherSavant': {
                'xFIP': 3.5, 'seasonFIP': 3.5, 'recentFIP': 3.4,
                'avgIPperStart': 6.0, 'openerRole': False,
                'ttoSplit': 0.1, 'ttoAvailable': True,
                'tto1': {'fip': 3.5, 'gamesUsed': 5}, 'tto3': {'fip': 3.4, 'gamesUsed': 3},
            },
            'bullpen': {'xFIP': 3.8, 'hlGrade': 'ABOVE_AVERAGE', 'hlAvailable': True, 'hlXFIP': 3.7},
        },
        'awayTeamStats': {
            'offenseBaselineAdj': 5.2, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
            'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
            'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
            'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
            'lineupStatusReason': '', 'lineupAdj': 0.05,
        },
        'homeTeamStats': {
            'offenseBaselineAdj': 4.0, 'lineupConfirmed': True, 'lineupConfirmedOfficial': True,
            'lineupPosted': True, 'lineupStatus': 'confirmed', 'lineupSource': 'mlb_stats_api',
            'lineupBattersExpected': 9, 'lineupBattersFound': 9, 'lineupBattersResolved': 9,
            'lineupAdjAvailable': True, 'lineupAdjApplied': True, 'lineupDataQuality': 'official',
            'lineupStatusReason': '', 'lineupAdj': 0.02,
        },
        'park': {'parkFactor': 100},
        'pinnacleVF': {'away': 48.0, 'home': 52.0},
        'oddsApiCommenceTime': '2026-07-31T19:45:00Z',
        'kalshiKey': 'AAAHH',
        'kalshiGameTime': '1545',
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


def _run_production_pipeline_for_game(game):
    """
    Runs the SAME production functions replay.run_candidate_replay() calls
    (never a reimplementation) once, up front, so a test can write their
    output as the "original" pipeline artifacts -- a first replay against
    that exact same input is then expected to be byte-identical
    (UNCHANGED), proving the replay engine doesn't quietly duplicate or
    diverge from the real math.
    """
    projection_context = compute_game_projection_context(game)
    ledger = evaluate_game(game, projection_context)
    slate = {"date": DATE, "games": [{**game, "marketLedger": ledger}]}
    _risk_gate.apply_tt_safety(slate)
    decision, report = _risk_gate.apply_portfolio_rules(slate)
    if decision == "PAPER_ONLY":
        replay._apply_paper_only_downgrade(slate, report["decision_reason"])
    execution_payload = _risk_gate.build_execution_artifact_payload(slate, decision, report["decision_reason"])
    return ledger, execution_payload


def _wire_full_pregame_fixture(tmp_path, monkeypatch, game=None, recommendations_created_at=None):
    """
    A minimal but complete set of real-shaped inputs for PRE_GAME_DECISION
    -- adapted from tests/edgelab/test_snapshot.py's fixture of the same
    name, with an optional real `game` embedded (via _make_game()) so
    replay's candidate-mode execution has real markets to evaluate rather
    than an empty games list.
    """
    monkeypatch.chdir(tmp_path)

    if game is None:
        rec_games, proj_games, norm_games = [], [], []
    else:
        ledger, execution_payload = _run_production_pipeline_for_game(game)
        rec_games = [{"gameId": game["gameId"], "away": game["away"], "home": game["home"], "marketLedger": ledger}]
        proj_games = [{"gameId": game["gameId"]}]
        norm_games = [game]
        _write_pipeline_artifact("execution", DATE, execution_payload, "scripts/risk_gate.py", created_at=recommendations_created_at)

    _write_pipeline_artifact(
        "recommendations", DATE, {"games": rec_games},
        "scripts/build_market_ledger.py", created_at=recommendations_created_at,
    )
    _write_pipeline_artifact("projections", DATE, {"games": proj_games}, "scripts/build_market_ledger.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("normalized_slate", DATE, {"games": norm_games}, "scripts/enrich_data.py", created_at=recommendations_created_at)
    if game is None:
        _write_pipeline_artifact("execution", DATE, {"rulesVersion": "1.0", "candidates": []}, "scripts/risk_gate.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("validation", DATE, {"errors": []}, "scripts/validate_slate_final.py", created_at=recommendations_created_at)
    _write_pipeline_artifact("protection", DATE, {"runType": "OFFICIAL_PREGAME"}, "scripts/protect_slate.py", created_at=recommendations_created_at)

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


def _build_manifest(tmp_path, monkeypatch, game=None, recommendations_created_at=None):
    _wire_full_pregame_fixture(tmp_path, monkeypatch, game=game, recommendations_created_at=recommendations_created_at)
    result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
    assert result["outcome"] == "created"
    return result["manifest"]


# ── Item 14: snapshot eligibility ────────────────────────────────────────

class TestSnapshotEligibility:
    def test_full_fixture_is_eligible_level_2(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.ELIGIBLE_LEVEL_2

    def test_missing_nice_to_have_component_caps_at_level_1(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=_make_game())
        os.remove(os.path.join("data", "weather.json"))
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        eligibility = replay.assess_replay_eligibility(result["manifest"])
        assert eligibility["eligibilityStatus"] == replay.ELIGIBLE_LEVEL_1_ONLY
        assert any("WEATHER" in r for r in eligibility["limitationReasons"])

    def test_missing_required_component_is_ineligible(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=_make_game())
        os.remove(pipeline_artifacts.artifact_path("recommendations", DATE))
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        eligibility = replay.assess_replay_eligibility(result["manifest"])
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_MISSING_INPUT
        assert any("RECOMMENDATION_OUTPUT" in r for r in eligibility["limitationReasons"])

    def test_unsupported_schema_version_is_ineligible(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        manifest = dict(manifest)
        manifest["schemaVersion"] = "999"
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_UNSUPPORTED_VERSION


class TestPartialConfigHandling:
    """EFFECTIVE_CONFIG being merely PARTIAL (the normal case) must NOT by
    itself block Level 2 eligibility -- only a genuinely unknown
    rulesConfigVersion does (item 2's documented distinction)."""

    def test_partial_effective_config_does_not_block_level_2(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        effective_config = next(c for c in manifest["components"] if c["componentType"] == "EFFECTIVE_CONFIG")
        assert effective_config["availabilityStatus"] == snap.PARTIAL  # real, always-partial today
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.ELIGIBLE_LEVEL_2
        assert "EFFECTIVE_CONFIG_PARTIAL" in eligibility["limitationReasons"]

    def test_unknown_rules_config_version_is_config_ambiguity(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        manifest = dict(manifest)
        manifest["rulesConfigVersion"] = None
        manifest["manifestHash"] = snap.compute_manifest_hash(manifest)
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_CONFIG_AMBIGUITY


class TestTemporalSkewRejection:
    def test_skewed_snapshot_is_ineligible(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=None, recommendations_created_at="2026-07-31T22:00:00Z")
        path = pipeline_artifacts.artifact_path("validation", DATE)
        with open(path) as f:
            env = json.load(f)
        env["meta"]["createdAt"] = "2026-07-31T10:00:00Z"
        with open(path, "w") as f:
            json.dump(env, f)
        result = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)
        assert result["manifest"]["temporalConsistency"]["skewDetected"] is True
        eligibility = replay.assess_replay_eligibility(result["manifest"])
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_TEMPORAL_SKEW


class TestTamperedSnapshotRejection:
    def test_tampered_frozen_bytes_are_ineligible(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        rec_component = next(c for c in manifest["components"] if c["componentType"] == "RECOMMENDATION_OUTPUT")
        with open(rec_component["snapshotPath"], "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 8)
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_INTEGRITY_FAILURE

    def test_tampered_manifest_hash_is_ineligible(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        manifest = dict(manifest)
        manifest["snapshotDate"] = "2099-01-01"  # mutate content without recomputing manifestHash
        eligibility = replay.assess_replay_eligibility(manifest)
        assert eligibility["eligibilityStatus"] == replay.INELIGIBLE_INTEGRITY_FAILURE


# ── Item 14: no automatic fidelity downgrade ─────────────────────────────

class TestNoAutomaticFidelityDowngrade:
    def test_level_1_only_snapshot_refused_without_explicit_flag(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=_make_game())
        os.remove(os.path.join("data", "weather.json"))
        manifest = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)["manifest"]
        run, results = replay.execute_replay(manifest, allow_level_1=False)
        assert run["runStatus"] == replay.RUN_STATUS_REJECTED_INELIGIBLE
        assert results == []

    def test_level_1_only_snapshot_runs_when_explicitly_allowed(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=_make_game())
        os.remove(os.path.join("data", "weather.json"))
        manifest = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)["manifest"]
        run, results = replay.execute_replay(manifest, allow_level_1=True)
        assert run["runStatus"] == replay.RUN_STATUS_COMPLETED
        assert run["replayFidelity"] == snap.LEVEL_1_APPROXIMATE

    def test_level_2_eligible_snapshot_never_downgraded_even_if_flag_passed(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, _results = replay.execute_replay(manifest, allow_level_1=True)
        assert run["runStatus"] == replay.RUN_STATUS_COMPLETED
        assert run["replayFidelity"] == snap.LEVEL_2_PRODUCTION_EQUIVALENT


# ── Item 6: candidate vs historical-production mode ──────────────────────

class TestOriginalVsCandidateMode:
    def test_historical_production_mode_always_rejected(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest, replay_mode=replay.MODE_HISTORICAL_PRODUCTION)
        assert run["runStatus"] == replay.RUN_STATUS_REJECTED_UNSUPPORTED_MODE
        assert results == []
        assert "HISTORICAL_PRODUCTION_REPLAY_UNSUPPORTED_THIS_MILESTONE" in run["limitationReasons"]

    def test_candidate_mode_completes(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest, replay_mode=replay.MODE_CANDIDATE)
        assert run["runStatus"] == replay.RUN_STATUS_COMPLETED
        assert run["replayMode"] == replay.MODE_CANDIDATE
        assert len(results) > 0

    def test_unknown_mode_raises(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        with pytest.raises(ValueError):
            replay.execute_replay(manifest, replay_mode="NOT_A_REAL_MODE")


# ── Item 5: deterministic replay + no math duplication ──────────────────

class TestDeterministicReplay:
    def test_same_manifest_replayed_twice_is_byte_identical(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run1, _r1 = replay.execute_replay(manifest)
        run2, _r2 = replay.execute_replay(manifest)
        assert run1["manifestHash"] == run2["manifestHash"]
        assert run1["replayRunId"] == run2["replayRunId"]

    def test_real_game_with_identical_original_and_replayed_pipeline_is_unchanged(self, tmp_path, monkeypatch):
        """Original recommendations/execution artifacts were built from the
        SAME production functions replay calls -- a first replay must
        reproduce them exactly, proving replay does not duplicate or
        diverge from the real math (this module's docstring claim)."""
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        assert run["runStatus"] == replay.RUN_STATUS_COMPLETED
        assert results, "expected at least one evaluated market row"
        assert run["summary"]["decisionsChanged"] == 0
        comparable_rows = 0
        for r in results:
            # Markets this fixture's odds don't populate a probability for
            # on EITHER side (e.g. F5/RL with no tie/spread data) legitimately
            # classify NOT_COMPARABLE -- every other row must be byte-identical
            # (UNCHANGED), since original and replayed both ran the same
            # production functions against the same input.
            assert r["changedDecision"] is False
            if r["comparisonClassification"] == replay.CMP_NOT_COMPARABLE:
                assert r["originalModelProbability"] is None
                assert r["replayedModelProbability"] is None
            else:
                assert r["comparisonClassification"] == replay.CMP_UNCHANGED, r
                assert r["originalModelProbability"] == r["replayedModelProbability"]
                comparable_rows += 1
        assert comparable_rows > 0


# ── Item 9: walk-forward integrity / postgame leakage prevention ────────

class TestPostgameLeakagePrevention:
    def test_run_candidate_replay_rejects_non_pregame_manifest(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        manifest = dict(manifest)
        manifest["snapshotStage"] = snap.STAGE_POST_GAME_SETTLEMENT
        with pytest.raises(replay.ReplayError):
            replay.run_candidate_replay(manifest)

    def test_run_candidate_replay_rejects_available_settlement_component(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        manifest = dict(manifest)
        manifest["components"] = [
            {**c, "availabilityStatus": snap.AVAILABLE} if c["componentType"] == "SETTLEMENT" else c
            for c in manifest["components"]
        ]
        with pytest.raises(replay.ReplayError):
            replay.run_candidate_replay(manifest)

    def test_settlement_and_clv_are_not_applicable_on_a_real_pregame_manifest(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        for component_type in ("SETTLEMENT", "CLV"):
            component = next(c for c in manifest["components"] if c["componentType"] == component_type)
            assert component["availabilityStatus"] != snap.AVAILABLE


# ── Item 7: decision comparison classification ───────────────────────────

class TestDecisionComparisonClassification:
    def _candidate(self, status, tier):
        return {"status": status, "tier": tier}

    def test_unchanged(self):
        original = {"modelProb": 55.0, "edge": 3.0}
        replayed = {"modelProb": 55.0, "edge": 3.0}
        classification, _ = replay.classify_comparison(
            original, replayed, self._candidate("Accepted", "HIGH"), self._candidate("Accepted", "HIGH"),
        )
        assert classification == replay.CMP_UNCHANGED

    def test_probability_changed_only(self):
        original = {"modelProb": 55.0, "edge": 3.0}
        replayed = {"modelProb": 60.0, "edge": 3.0}
        classification, reasons = replay.classify_comparison(
            original, replayed, self._candidate("Rejected", "PAPER"), self._candidate("Rejected", "PAPER"),
        )
        assert classification == replay.CMP_PROBABILITY_CHANGED_ONLY
        assert "PROBABILITY_CHANGED" in reasons

    def test_edge_changed(self):
        original = {"modelProb": 55.0, "edge": 3.0}
        replayed = {"modelProb": 55.0, "edge": 8.0}
        classification, reasons = replay.classify_comparison(
            original, replayed, self._candidate("Rejected", "PAPER"), self._candidate("Rejected", "PAPER"),
        )
        assert classification == replay.CMP_EDGE_CHANGED
        assert "EDGE_CHANGED" in reasons

    def test_recommendation_added(self):
        original = {"modelProb": 55.0, "edge": 1.0}
        replayed = {"modelProb": 55.0, "edge": 6.0}
        classification, reasons = replay.classify_comparison(
            original, replayed, self._candidate("Rejected", None), self._candidate("Accepted", "HIGH"),
        )
        assert classification == replay.CMP_RECOMMENDATION_ADDED
        assert "RECOMMENDATION_NEWLY_ACCEPTED" in reasons

    def test_recommendation_removed(self):
        original = {"modelProb": 55.0, "edge": 6.0}
        replayed = {"modelProb": 55.0, "edge": 1.0}
        classification, reasons = replay.classify_comparison(
            original, replayed, self._candidate("Accepted", "HIGH"), self._candidate("Rejected", None),
        )
        assert classification == replay.CMP_RECOMMENDATION_REMOVED
        assert "RECOMMENDATION_NO_LONGER_ACCEPTED" in reasons

    def test_tier_upgrade(self):
        original = {"modelProb": 55.0, "edge": 4.0}
        replayed = {"modelProb": 55.0, "edge": 4.0}
        classification, _ = replay.classify_comparison(
            original, replayed, self._candidate("Accepted", "MEDIUM"), self._candidate("Accepted", "HIGH"),
        )
        assert classification == replay.CMP_TIER_UPGRADE

    def test_tier_downgrade(self):
        original = {"modelProb": 55.0, "edge": 4.0}
        replayed = {"modelProb": 55.0, "edge": 4.0}
        classification, _ = replay.classify_comparison(
            original, replayed, self._candidate("Accepted", "HIGH"), self._candidate("Accepted", "MEDIUM"),
        )
        assert classification == replay.CMP_TIER_DOWNGRADE

    def test_not_comparable_when_neither_side_has_a_probability(self):
        classification, _ = replay.classify_comparison({"modelProb": None}, {"modelProb": None}, None, None)
        assert classification == replay.CMP_NOT_COMPARABLE

    def test_original_data_missing(self):
        classification, reasons = replay.classify_comparison(None, {"modelProb": 55.0}, None, None)
        assert classification == replay.CMP_ORIGINAL_DATA_MISSING
        assert "NO_ORIGINAL_ROW_FOR_THIS_MARKET" in reasons

    def test_market_expression_fields_always_null_this_milestone(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        _run, results = replay.execute_replay(manifest)
        for r in results:
            assert r["originalPreferredExpression"] is None
            assert r["replayedPreferredExpression"] is None


# ── Item 10: mismatch categorization ──────────────────────────────────────

class TestMismatchClassification:
    def test_f5_pricing_version_signal_takes_priority(self):
        original_row = {"f5PricingVersion": "legacy_two_way"}
        reason, evidence = replay.classify_mismatch_reason(original_row, "F5_ML_Away", [])
        assert reason == replay.MISMATCH_EXPECTED_MODEL_VERSION_CHANGED
        assert "legacy_two_way" in evidence

    def test_config_partial_signal_when_no_pricing_signal(self):
        reason, _evidence = replay.classify_mismatch_reason({}, "ML_Away", ["EFFECTIVE_CONFIG_PARTIAL"])
        assert reason == replay.MISMATCH_EXPECTED_CONFIG_INCOMPLETE

    def test_falls_through_to_suspected_defect_when_no_signal(self):
        reason, _evidence = replay.classify_mismatch_reason({}, "ML_Away", [])
        assert reason == replay.MISMATCH_REPLAY_ENGINE_DEFECT_SUSPECTED


# ── Item 8: settlement / CLV linkage ─────────────────────────────────────

class TestSettlementLinkage:
    def test_resolved_settlement(self):
        settlement_by_ticker = {"TICKER-1": {"settlementStatus": "SETTLED", "result": "YES"}}
        linkage = replay._settlement_linkage_for_ticker(settlement_by_ticker, None, "TICKER-1")
        assert linkage == {"status": "RESOLVED", "result": "YES", "reason": None}

    def test_unsupported_settlement_stays_unresolved(self):
        """A ticker with no settlement evidence at all -- never inferred
        from a related-but-different market."""
        settlement_by_ticker = {}
        linkage = replay._settlement_linkage_for_ticker(settlement_by_ticker, None, "TICKER-UNKNOWN")
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["result"] is None
        assert linkage["reason"] == "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"

    def test_unavailable_reason_short_circuits(self):
        linkage = replay._settlement_linkage_for_ticker({}, "NO_POSTGAME_SNAPSHOT_FOR_DATE", "TICKER-1")
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "NO_POSTGAME_SNAPSHOT_FOR_DATE"

    def test_not_settled_status_is_unresolved(self):
        settlement_by_ticker = {"TICKER-1": {"settlementStatus": "VOID", "unavailableReason": "game_postponed"}}
        linkage = replay._settlement_linkage_for_ticker(settlement_by_ticker, None, "TICKER-1")
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "game_postponed"


class TestClvLinkage:
    def test_resolved_clv_is_closing_minus_entry(self):
        clv_by_ticker = {"TICKER-1": {"yesBid": 60.0, "yesAsk": 64.0}}
        linkage = replay._clv_linkage_for_ticker(clv_by_ticker, None, "TICKER-1", entry_implied_pct=55.0)
        assert linkage["status"] == "RESOLVED"
        assert linkage["clvValue"] == pytest.approx(7.0, abs=1e-6)  # (60+64)/2 - 55

    def test_no_clv_quote_is_unresolved(self):
        linkage = replay._clv_linkage_for_ticker({}, None, "TICKER-1", entry_implied_pct=55.0)
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "NO_CLV_QUOTE_FOR_THIS_MARKET"

    def test_incomplete_quote_is_unresolved(self):
        clv_by_ticker = {"TICKER-1": {"yesBid": None, "yesAsk": 64.0}}
        linkage = replay._clv_linkage_for_ticker(clv_by_ticker, None, "TICKER-1", entry_implied_pct=55.0)
        assert linkage["status"] == "UNRESOLVED"
        assert linkage["reason"] == "INCOMPLETE_CLOSING_QUOTE_OR_ENTRY_PRICE"


class TestSettlementClvLinkageEndToEnd:
    def test_no_postgame_snapshot_yields_unresolved_run(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        assert run["summary"]["settledUnresolved"] == run["summary"]["marketsEvaluated"]
        assert run["summary"]["settledResolved"] == 0
        assert any("SETTLEMENT_CLV_UNAVAILABLE" in r for r in run["limitationReasons"])
        for r in results:
            assert r["settlementLinkage"]["status"] == "UNRESOLVED"


# ── Item 8: Brier score / log loss / sample-size gating ──────────────────

class TestBrierAndLogLoss:
    def test_brier_score_perfect_prediction_is_zero(self):
        assert replay.brier_score(1.0, 1) == pytest.approx(0.0)
        assert replay.brier_score(0.0, 0) == pytest.approx(0.0)

    def test_brier_score_worst_prediction_is_one(self):
        assert replay.brier_score(0.0, 1) == pytest.approx(1.0)
        assert replay.brier_score(1.0, 0) == pytest.approx(1.0)

    def test_log_loss_confident_correct_is_near_zero(self):
        assert replay.log_loss(0.99, 1) < 0.02

    def test_log_loss_confident_wrong_is_large(self):
        assert replay.log_loss(0.01, 1) > 3.0


class TestSampleSizeGating:
    def _resolved(self, n, probability=0.5, outcome=1):
        return [{"probability": probability, "outcome": outcome} for _ in range(n)]

    def test_zero_resolved_is_none_not_fabricated(self):
        assert replay.score_resolved_results([]) is None

    def test_below_insufficient_threshold(self):
        result = replay.score_resolved_results(self._resolved(19))
        assert result["sampleSizeStatus"] == "INSUFFICIENT_SAMPLE"
        assert result["n"] == 19

    def test_at_descriptive_only_threshold(self):
        result = replay.score_resolved_results(self._resolved(20))
        assert result["sampleSizeStatus"] == "DESCRIPTIVE_ONLY"

    def test_at_calibrated_threshold(self):
        result = replay.score_resolved_results(self._resolved(100))
        assert result["sampleSizeStatus"] == "CALIBRATED"

    def test_roi_always_null_this_milestone(self):
        result = replay.score_resolved_results(self._resolved(20))
        assert result["roi"] is None

    def test_calibration_error_matches_actual_minus_expected(self):
        resolved = self._resolved(10, probability=0.5, outcome=1) + self._resolved(10, probability=0.5, outcome=0)
        result = replay.score_resolved_results(resolved)
        assert result["winRate"] == pytest.approx(0.5)
        assert result["expectedWinRate"] == pytest.approx(0.5)
        assert result["calibrationError"] == pytest.approx(0.0)


# ── Item 9: chronological walk-forward ordering ──────────────────────────

class TestChronologicalWalkForwardOrdering:
    def test_sorted_snapshot_dates_is_chronological_regardless_of_input_order(self):
        dates = ["2026-08-02", "2026-07-30", "2026-08-01", "2026-07-31"]
        assert replay.sorted_snapshot_dates(dates) == ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02"]

    def test_sorted_snapshot_dates_does_not_mutate_input(self):
        dates = ["2026-08-02", "2026-07-30"]
        original = list(dates)
        replay.sorted_snapshot_dates(dates)
        assert dates == original


# ── Item 14: duplicate replay-run prevention / write-once discipline ────

class TestDuplicateReplayRunPrevention:
    def test_second_write_of_identical_run_is_noop_verified(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        first = replay.write_replay_outputs(run, results)
        assert first["outcome"] == "created"
        second = replay.write_replay_outputs(run, results)
        assert second["outcome"] == "noop_verified"
        assert second["path"] == first["path"]

    def test_conflicting_content_under_same_id_is_flagged_not_overwritten(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        replay.write_replay_outputs(run, results)

        tampered_run = dict(run)
        tampered_run["limitationReasons"] = list(run["limitationReasons"]) + ["INJECTED_FOR_TEST"]
        tampered_run["manifestHash"] = replay.compute_run_manifest_hash(tampered_run)
        result = replay.write_replay_outputs(tampered_run, results)
        assert result["outcome"] == "conflict"

        # Original committed run must be untouched.
        stored = replay.load_replay_run(run["replayRunId"])
        assert stored["manifestHash"] == run["manifestHash"]


# ── Item 3: research/live-data isolation ─────────────────────────────────

class TestResearchLiveDataIsolation:
    def test_replay_never_touches_production_files(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        protected_paths = [
            os.path.join("config", "rules.json"),
            os.path.join("data", "slates", DATE, "authoritative.json"),
        ]
        before = {}
        for p in protected_paths:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    before[p] = f.read()

        run, results = replay.execute_replay(manifest)
        replay.write_replay_outputs(run, results)

        for p, content in before.items():
            with open(p, "rb") as f:
                assert f.read() == content, f"{p} was modified by the replay engine"
        assert not os.path.exists(os.path.join("data", "bets.json"))

    def test_output_never_written_under_recommendations_or_bets_directories(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        write_result = replay.write_replay_outputs(run, results)
        assert write_result["path"].startswith(os.path.join("data", "edgelab", "replay_runs"))


# ── Schema validation of real output (item 3/11) ────────────────────────

class TestSchemaValidation:
    def test_real_replay_run_and_results_pass_schema_validation(self, tmp_path, monkeypatch):
        manifest = _build_manifest(tmp_path, monkeypatch, game=_make_game())
        run, results = replay.execute_replay(manifest)
        assert schema.validate_record("replay_run", run) == []
        for r in results:
            assert schema.validate_record("replay_result", r) == []

    def test_rejected_ineligible_run_passes_schema_validation(self, tmp_path, monkeypatch):
        _wire_full_pregame_fixture(tmp_path, monkeypatch, game=_make_game())
        os.remove(os.path.join("data", "weather.json"))
        manifest = snap.build_snapshot(snap.STAGE_PRE_GAME_DECISION, DATE)["manifest"]
        run, _results = replay.execute_replay(manifest, allow_level_1=False)
        assert schema.validate_record("replay_run", run) == []

    def test_market_family_null_is_not_a_schema_violation(self):
        """Regression test for the marketFamily-required-but-nullable bug
        found while dogfooding scripts/run_replay.py against real data:
        a market row with no ticker legitimately has marketFamily=None,
        and that must not be reported as a missing required field (same
        class of bug snapshot_component.schema.json's storageMode field
        had, already fixed elsewhere in this codebase)."""
        result = {
            "schemaVersion": "1", "replayResultId": "x", "replayRunId": "y",
            "gameId": None, "marketTicker": None, "marketFamily": None, "selection": "ML_Away",
            "side": None, "threshold": None, "originalModelProbability": None,
            "replayedModelProbability": None, "originalMarketPrice": None,
            "originalEdge": None, "replayedEdge": None,
            "originalRecommendationStatus": None, "replayedRecommendationStatus": None,
            "originalTier": None, "replayedTier": None,
            "originalPassReason": None, "replayedPassReason": None,
            "originalPreferredExpression": None, "replayedPreferredExpression": None,
            "changedDecision": False, "changeReasons": [],
            "comparisonClassification": "NOT_COMPARABLE",
            "settlementLinkage": {"status": "UNRESOLVED", "result": None, "reason": "x"},
            "clvLinkage": {"status": "UNRESOLVED", "clvValue": None, "reason": "x"},
            "comparisonMetadata": {}, "validationStatus": "valid",
        }
        assert schema.validate_record("replay_result", result) == []


# ── Item 12: identifiers ──────────────────────────────────────────────────

class TestReplayIds:
    def test_replay_run_id_is_deterministic(self):
        a = ids.build_replay_run_id("snap1", "CANDIDATE_MODEL", "abc123", "1")
        b = ids.build_replay_run_id("snap1", "CANDIDATE_MODEL", "abc123", "1")
        assert a == b

    def test_replay_run_id_changes_with_snapshot(self):
        a = ids.build_replay_run_id("snap1", "CANDIDATE_MODEL", "abc123", "1")
        b = ids.build_replay_run_id("snap2", "CANDIDATE_MODEL", "abc123", "1")
        assert a != b

    def test_replay_result_id_is_deterministic(self):
        a = ids.build_replay_result_id("run1", "game1", "TICKER:ML_Away")
        b = ids.build_replay_result_id("run1", "game1", "TICKER:ML_Away")
        assert a == b
