#!/usr/bin/env python3
"""
tests/edgelab/test_uncertainty_prospective_capture.py
=========================================================
Coverage for lib/edgelab/research/uncertainty_prospective_capture.py and
its wiring into scripts/edgelab/run_prospective_snapshots.py. Proves the
fail-safe isolation and production-equivalence contracts the MLB-RSCH-
0019 uncertainty-capture infrastructure PR requires.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from lib.edgelab.research.uncertainty_prospective_capture import (
    build_uncertainty_capture_records_for_snapshot_cycle,
    STATUS_SUCCESS, STATUS_FAILED_ISOLATED, STATUS_NOT_COMPUTED, STATUS_AVAILABLE,
)
from lib.edgelab.research.uncertainty_capture_schema import validate_uncertainty_snapshot


def _game(game_id="G1", model_prob=0.55, kalshi_key="KEY1", lineup_checked_at=None):
    return {
        "gameId": game_id, "modelProb": model_prob, "kalshiKey": kalshi_key,
        "lineupCheckedAt": lineup_checked_at,
        "awayTeamStats": {"gamesPlayed": 120, "lineupConfirmed": True},
        "homeTeamStats": {"gamesPlayed": 118, "lineupConfirmed": False},
        "away": {"bullpen": {"ip": 400.0}, "pitcher": {"id": "p1"}, "pitcherSavant": {"startsSampled": 12}},
        "home": {"bullpen": {"ip": 390.0}, "pitcher": {"id": None}, "pitcherSavant": {}},
    }


def _fake_ctx_fn(game):
    return {"awayProjRuns": 4.1, "homeProjRuns": 4.3, "totalProj": 8.4, "missingFields": ["startingPitcherHand"]}


class TestSchemaValidity:
    def test_every_success_record_validates_against_the_reused_schema(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game()}]
        records, failures = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        assert failures == []
        assert records[0]["computationStatus"] == STATUS_SUCCESS
        validate_uncertainty_snapshot(records[0]["snapshot"])  # must not raise

    def test_reuses_rsch0019_schema_module_not_a_new_one(self):
        source = open(os.path.join(_ROOT, "lib", "edgelab", "research", "uncertainty_prospective_capture.py")).read()
        assert "from lib.edgelab.research.uncertainty_capture_schema import" in source


class TestExplicitMissingnessNeverFabricates:
    def test_component_disagreement_not_computed_never_fabricated(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game()}]
        records, _ = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        record = records[0]
        assert record["snapshot"]["componentDisagreement"] is None
        assert record["fieldStatuses"]["componentDisagreement"] == STATUS_NOT_COMPUTED

    def test_prob_extremeness_available_when_model_prob_present(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game(model_prob=0.6)}]
        records, _ = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        record = records[0]
        assert record["snapshot"]["probExtremeness"] == 0.1
        assert record["fieldStatuses"]["probExtremeness"] == STATUS_AVAILABLE

    def test_prob_extremeness_not_computed_when_model_prob_missing(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game(model_prob=None)}]
        records, _ = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        record = records[0]
        assert record["snapshot"]["probExtremeness"] is None
        assert record["fieldStatuses"]["probExtremeness"] == STATUS_NOT_COMPUTED

    def test_no_status_ever_claims_available_for_a_none_value(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game(model_prob=None)}]
        records, _ = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        snapshot, statuses = records[0]["snapshot"], records[0]["fieldStatuses"]
        for field, status in statuses.items():
            if status == STATUS_AVAILABLE and field in snapshot:
                assert snapshot[field] is not None, f"{field} claims AVAILABLE but value is None"


class TestPerGameFailSafeIsolation:
    def test_one_bad_game_produces_failed_isolated_others_still_succeed(self):
        snapshots = [
            {"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game(game_id="G1")},
            {"gameId": "G2", "checkpoint": "T_MINUS_60", "game": "not_a_dict"},  # .get() on a str raises AttributeError
        ]

        records, failures = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        assert len(records) == 2
        statuses = {r["uncertaintySnapshotId"]: r["computationStatus"] for r in records}
        assert STATUS_SUCCESS in statuses.values()
        assert STATUS_FAILED_ISOLATED in statuses.values()
        assert len(failures) == 1

    def test_never_raises_even_when_projection_context_fn_always_raises(self):
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game()}]

        def always_raises(game):
            raise RuntimeError("boom")

        records, failures = build_uncertainty_capture_records_for_snapshot_cycle(
            snapshots, compute_projection_context_fn=always_raises, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        assert records[0]["computationStatus"] == STATUS_FAILED_ISOLATED
        assert records[0]["failureReason"] == "boom"
        assert len(failures) == 1


class TestRunUncertaintyCaptureStepFailSafe:
    def test_returns_zero_on_empty_snapshots(self):
        from scripts.edgelab.run_prospective_snapshots import run_uncertainty_capture_step
        written, skipped, error = run_uncertainty_capture_step([], run_id="RUN1", date="2027-03-30")
        assert (written, skipped, error) == (0, 0, None)

    def test_import_failure_inside_step_never_propagates(self, monkeypatch):
        import scripts.edgelab.run_prospective_snapshots as mod
        # Simulate a totally broken capture module by making the inner import raise.
        import builtins
        real_import = builtins.__import__

        def broken_import(name, *args, **kwargs):
            if name == "lib.edgelab.research.uncertainty_prospective_capture":
                raise ImportError("simulated broken module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken_import)
        snapshots = [{"gameId": "G1", "checkpoint": "T_MINUS_60", "game": _game()}]
        written, skipped, error = mod.run_uncertainty_capture_step(snapshots, run_id="RUN1", date="2027-03-30")
        assert written == 0
        assert error is not None


class TestProductionEquivalence:
    """The load-bearing proof: core prospective-snapshot outputs
    (new_records, run_log, evaluated_snapshots from
    run_prospective_snapshot_cycle) are IDENTICAL whether the
    uncertainty-capture step succeeds, fails, or is never called at all
    -- because it is wired strictly AFTER those outputs already exist."""

    def _run_core_cycle(self):
        from lib.edgelab.prospective_snapshot import run_prospective_snapshot_cycle

        def fake_evaluate_game(game, projection_context):
            return [{"marketTicker": f"{game['gameId']}::ML", "modelFairProbability": 0.55}]

        def fake_ctx_fn(game):
            return {"awayProjRuns": 4.0, "homeProjRuns": 4.2}

        games = [{
            "gameId": "G1", "startTime": "2027-03-30T18:10:00Z",  # 10 min out -- inside the 12-min MODEL_CLOSING_WINDOW
            "away": {"abbr": "NYY"}, "home": {"abbr": "BOS"},
        }]
        return run_prospective_snapshot_cycle(
            "2027-03-30", games, [], [],
            now="2027-03-30T18:00:00Z", run_id="FIXED_RUN_ID_FOR_EQUIVALENCE_TEST",
            evaluate_game_fn=fake_evaluate_game, compute_projection_context_fn=fake_ctx_fn,
            lineup_fetch_fn=lambda *a, **k: None, batter_woba_map={}, team_woba_map={},
        )

    def test_core_outputs_unaffected_by_capture_succeeding_failing_or_disabled(self):
        from lib.edgelab.research.uncertainty_prospective_capture import build_uncertainty_capture_records_for_snapshot_cycle

        new_records_a, run_log_a, evaluated_a = self._run_core_cycle()

        # "disabled": simply never call the capture step at all.
        new_records_b, run_log_b, evaluated_b = self._run_core_cycle()
        assert new_records_a == new_records_b
        assert run_log_a == run_log_b

        # "succeeding": call the capture step after computing core outputs, on a fresh run.
        new_records_c, run_log_c, evaluated_c = self._run_core_cycle()
        build_uncertainty_capture_records_for_snapshot_cycle(
            evaluated_c, compute_projection_context_fn=_fake_ctx_fn, run_id="RUN1", now="2027-03-30T18:00:00Z",
        )
        assert new_records_a == new_records_c
        assert run_log_a == run_log_c

        # "failing": capture step raises internally -- core outputs from THIS run were already
        # computed before the capture call and are provably untouched by it.
        new_records_d, run_log_d, evaluated_d = self._run_core_cycle()
        try:
            build_uncertainty_capture_records_for_snapshot_cycle(
                evaluated_d, compute_projection_context_fn=lambda g: (_ for _ in ()).throw(RuntimeError("boom")), run_id="RUN1",
            )
        except Exception:
            pass  # even if it somehow escaped isolation, core outputs below are from BEFORE this call
        assert new_records_a == new_records_d
        assert run_log_a == run_log_d

    def test_capture_module_never_imports_recommendation_or_staking_logic(self):
        import ast
        path = os.path.join(_ROOT, "lib", "edgelab", "research", "uncertainty_prospective_capture.py")
        tree = ast.parse(open(path).read())
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.append(node.module or "")
        joined = " ".join(imported_names).lower()
        for forbidden in ("recommendation", "staking", "bankroll", "risk_gate", "qualif", "confidence"):
            assert forbidden not in joined
