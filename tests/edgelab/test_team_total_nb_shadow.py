"""Tests for MLB-RSCH-0035 (TEAM_TOTAL_NB_V1 prospective shadow).

Two things must be impossible here: the shadow affecting production, and
the candidate quietly ceasing to be frozen. Most of these tests exist to
make one of those two failures loud.
"""
import ast
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))

from lib.edgelab import team_total_nb_shadow as tt  # noqa: E402
from lib.edgelab.shadow_distribution import FROZEN_DISPERSION  # noqa: E402
from scripts.build_market_ledger import p_over_total  # noqa: E402
import run_team_total_nb_shadow_experiment as exp  # noqa: E402

MODULE_SOURCE = open(tt.__file__, encoding="utf-8").read()
EXP_SOURCE = open(exp.__file__, encoding="utf-8").read()
ARTIFACT = (json.load(open(exp.ARTIFACT_PATH, encoding="utf-8"))
            if os.path.exists(exp.ARTIFACT_PATH) else None)


class TestTheCandidateIsFrozen:
    def test_the_dispersion_is_the_imported_frozen_one(self):
        assert FROZEN_DISPERSION == 0.281513
        assert "FROZEN_DISPERSION" in MODULE_SOURCE

    def test_nothing_is_fitted_anywhere(self):
        for source in (MODULE_SOURCE, EXP_SOURCE):
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "attr", getattr(node.func, "id", "")) or ""
                    assert "fit" not in name.lower(), f"a fitting routine is called: {name}"

    def test_no_calibration_map_or_market_anchoring(self):
        lowered = MODULE_SOURCE.lower()
        for banned in ("calibration_map", "calibrated", "anchor_to_market", "shrink"):
            assert banned not in lowered

    def test_the_market_is_recorded_but_never_an_input(self):
        """marketVigFreeProbability may be stored; it must never reach the
        candidate probability."""
        tree = ast.parse(MODULE_SOURCE)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "candidate_probability")
        names = {getattr(x, "id", "") for x in ast.walk(fn) if isinstance(x, ast.Name)}
        for banned in ("marketVigFreeProbability", "market", "executablePrice", "ask", "price"):
            assert banned not in names

    def test_the_candidate_depends_on_nothing_but_mean_threshold_dispersion(self):
        import inspect
        params = list(inspect.signature(tt.candidate_probability).parameters)
        assert params == ["team_proj", "threshold", "dispersion"]

    def test_the_fingerprint_changes_if_the_definition_changes(self):
        before = exp.candidate_fingerprint()
        original = exp.FROZEN_THRESHOLD_WEIGHTS
        try:
            mutated = dict(original)
            mutated[4] = mutated[4] + 1          # int keys: no ** unpacking
            exp.FROZEN_THRESHOLD_WEIGHTS = mutated
            assert exp.candidate_fingerprint() != before
        finally:
            exp.FROZEN_THRESHOLD_WEIGHTS = original
        assert exp.candidate_fingerprint() == before


class TestSemanticsMatchTheContract:
    def test_the_ticker_parser_is_exact(self):
        parsed = tt.parse_team_total_ticker("KXMLBTEAMTOTAL-26AUG301410CWSMIN-CWS5")
        assert parsed == {"team": "CWS", "threshold": 5, "side": "AWAY",
                          "away": "CWS", "home": "MIN"}

    def test_home_and_away_are_resolved_from_the_event_ticker(self):
        assert tt.parse_team_total_ticker(
            "KXMLBTEAMTOTAL-26AUG301410CWSMIN-MIN5")["side"] == "HOME"

    def test_a_suffix_team_not_in_the_event_is_refused(self):
        assert tt.parse_team_total_ticker("KXMLBTEAMTOTAL-26AUG301410CWSMIN-BOS5") is None

    @pytest.mark.parametrize("bad", ["", None, "KXMLBGAME-26AUG301410CWSMIN-CWS",
                                     "KXMLBF5-26AUG301410CWSMIN-CWS",
                                     "kxmlbteamtotal-26aug301410cwsmin-cws5"])
    def test_non_team_total_tickers_are_refused(self, bad):
        assert tt.parse_team_total_ticker(bad) is None

    def test_the_candidate_prices_at_least_n(self):
        """P(X >= N) = 1 - P(X <= N-1)."""
        from lib.edgelab.backtest.run_distributions import negative_binomial_pmf
        mean, n = 4.5, 4
        expected = 1.0 - sum(negative_binomial_pmf(k, mean, FROZEN_DISPERSION) for k in range(0, n))
        assert tt.candidate_probability(mean, n) == pytest.approx(expected)

    def test_the_control_is_productions_own_function(self):
        """Not a reimplementation: p_over_total(mean, N-1), capped at 0.95."""
        for mean in (3.0, 4.5, 6.0):
            for n in (3, 4, 5):
                assert tt.control_probability(mean, n, p_over_total) == pytest.approx(
                    min(p_over_total(mean, n - 1), 0.95))

    def test_the_control_reproduces_productions_cap(self):
        assert tt.control_probability(9.0, 1, p_over_total) == pytest.approx(0.95)

    def test_candidate_and_control_actually_differ(self):
        assert tt.candidate_probability(4.5, 5) != tt.control_probability(4.5, 5, p_over_total)

    def test_the_candidate_is_monotone_in_the_mean(self):
        vals = [tt.candidate_probability(m, 4) for m in (3.0, 3.5, 4.0, 4.5, 5.0)]
        assert vals == sorted(vals)

    def test_the_candidate_has_a_fatter_upper_tail_than_the_control(self):
        assert tt.candidate_probability(4.5, 8) > tt.control_probability(4.5, 8, p_over_total)


class TestPerRowIsolation:
    def _rows(self, **over):
        base = {"gameId": "g1", "checkpoint": "PRE_GAME_DECISION",
                "marketTicker": "KXMLBTEAMTOTAL-26AUG301410CWSMIN-CWS5", "teamProj": 4.5}
        base.update(over)
        return [base]

    def _build(self, rows):
        return tt.build_team_total_shadow_records(
            rows, run_id="R", experiment_id="MLB-RSCH-0035",
            evidence_level="E4_PROSPECTIVE_SHADOW", p_over_total_fn=p_over_total)

    def test_a_good_row_computes(self):
        records, failures = self._build(self._rows())
        assert not failures and records[0]["computationStatus"] == "COMPUTED"

    @pytest.mark.parametrize("over,reason", [
        ({"marketTicker": None}, tt.FAILURE_NO_TICKER),
        ({"marketTicker": "KXMLBGAME-X"}, tt.FAILURE_UNPARSEABLE),
        ({"marketTicker": "KXMLBTEAMTOTAL-26AUG301410CWSMIN-BOS5"}, tt.FAILURE_TEAM_NOT_IN_EVENT),
        ({"teamProj": None}, tt.FAILURE_NO_PROJECTION),
    ])
    def test_a_bad_row_fails_in_isolation_with_an_explicit_reason(self, over, reason):
        records, failures = self._build(self._rows(**over))
        assert records[0]["computationStatus"] == "FAILED_ISOLATED"
        assert records[0]["failureReason"] == reason
        assert len(failures) == 1

    def test_a_failed_row_carries_no_probability(self):
        records, _ = self._build(self._rows(teamProj=None))
        for field in ("candidateProbability", "controlProbability", "candidateMinusControl"):
            assert field not in records[0], "a failed row fabricated a probability"

    def test_one_bad_row_never_aborts_the_others(self):
        rows = self._rows() + self._rows(gameId="g2", teamProj=None) + self._rows(gameId="g3")
        records, failures = self._build(rows)
        assert len(records) == 3 and len(failures) == 1
        assert [r["computationStatus"] for r in records] == [
            "COMPUTED", "FAILED_ISOLATED", "COMPUTED"]

    def test_every_record_gets_a_distinct_id(self):
        rows = self._rows() + self._rows(gameId="g2") + self._rows(gameId="g3")
        records, _ = self._build(rows)
        assert len({r["shadowEvaluationId"] for r in records}) == 3

    def test_the_builder_writes_nothing(self):
        tree = ast.parse(MODULE_SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", "")) or ""
                assert name not in ("append_records", "upsert_records", "write_all_records", "open"), (
                    f"the shadow module performs I/O: {name}")


class TestProductionIsolation:
    def test_the_shadow_never_calls_a_production_decision_function(self):
        for banned in ("bet_size", "confidence_from_edge", "accepted_row", "rejected_row",
                       "realized_pl_for_bet", "write_placed_bet"):
            assert banned not in MODULE_SOURCE, f"shadow touches production decisioning: {banned}"

    def test_the_step_is_wired_after_the_core_write(self):
        runner = open(os.path.join(_ROOT, "scripts", "edgelab",
                                   "run_prospective_snapshots.py"), encoding="utf-8").read()
        assert "run_team_total_nb_shadow_step" in runner
        core = runner.index("run_prospective_snapshot_cycle(")
        step = runner.index("tt_written, tt_skipped_dup, tt_error = run_team_total_nb_shadow_step")
        assert core < step, "the shadow step must run strictly AFTER the core cycle"

    def test_the_step_swallows_every_exception(self):
        runner = open(os.path.join(_ROOT, "scripts", "edgelab",
                                   "run_prospective_snapshots.py"), encoding="utf-8").read()
        fn = runner[runner.index("def run_team_total_nb_shadow_step"):]
        fn = fn[:fn.index("\ndef ")]
        assert "except Exception as exc" in fn
        assert "return 0, 0, str(exc)" in fn

    def test_the_shadow_uses_its_own_storage_entity(self):
        assert exp.SHADOW_ENTITY == "team_total_nb_shadow_evaluations"
        assert "model_evaluations" not in MODULE_SOURCE


class TestPersistenceIsWiredEverywhere:
    WORKFLOW = os.path.join(_ROOT, ".github", "workflows", "model-snapshot-scheduler.yml")

    def test_the_entity_is_in_the_pre_commit_backup(self):
        body = open(self.WORKFLOW, encoding="utf-8").read()
        assert "cp -r data/edgelab/team_total_nb_shadow_evaluations /tmp/prospective-snapshot-backup/" in body

    def test_the_entity_is_in_git_data_commit(self):
        body = open(self.WORKFLOW, encoding="utf-8").read()
        # Anchor on the actual run block, not the first textual mention --
        # git_data_commit.py is discussed in a header comment far above the
        # command that invokes it.
        commit = body[body.index("python3 scripts/ci/git_data_commit.py"):]
        commit = commit[:commit.index("- name:")]
        assert "data/edgelab/team_total_nb_shadow_evaluations/" in commit

    def test_the_failure_artifact_message_names_it(self):
        body = open(self.WORKFLOW, encoding="utf-8").read()
        assert "MLB-RSCH-0035 team-total-NB-shadow" in body

    def test_it_reuses_the_existing_e4_path_rather_than_a_new_one(self):
        """Alongside the two sidecars already proven to persist."""
        body = open(self.WORKFLOW, encoding="utf-8").read()
        for existing in ("mlb_rsch_0011_shadow_evaluations", "uncertainty_capture_snapshots"):
            assert existing in body
        assert body.count("team_total_nb_shadow_evaluations") >= 3


class TestPreregistrationIsLocked:
    def test_the_weights_are_frozen_in_source_not_derived(self):
        """A forward run must not be able to recompute its own weights."""
        assert isinstance(exp.FROZEN_THRESHOLD_WEIGHTS, dict)
        tree = ast.parse(EXP_SOURCE)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "threshold_standardized")
        names = {getattr(x, "id", "") for x in ast.walk(fn) if isinstance(x, ast.Name)}
        assert "FROZEN_THRESHOLD_WEIGHTS" in names
        assert "Counter" not in names, "weights are being counted from the scored rows"

    def test_the_preregistration_is_frozen_and_complete(self):
        pre = exp.preregistration()
        with pytest.raises(Exception):
            pre.effect_floor = 0.0
        assert pre.min_independent_games == 100
        assert pre.min_independent_dates == 10
        assert pre.required_transport == "WALK_FORWARD_REPLICATION"
        assert pre.require_executable_capacity is True

    def test_registration_is_idempotent(self):
        a, _ = exp.register_experiment()
        b, _ = exp.register_experiment()
        assert a["controlModelId"] == b["controlModelId"]

    def test_the_experiment_is_confirmatory_and_clustered_by_game(self):
        _, definition = exp.register_experiment()
        assert definition["experimentType"] == "CONFIRMATORY"
        assert definition["clusteringUnit"] == "gameId"
        assert definition["minimumSampleRequirement"]["independentGames"] == 100

    def test_retrospective_rows_are_excluded_by_registration(self):
        _, definition = exp.register_experiment()
        assert any("captured before registration" in c for c in definition["exclusionCriteria"])

    def test_the_evidence_level_is_prospective_shadow(self):
        _, definition = exp.register_experiment()
        assert definition["evidenceLevel"] == "E4_PROSPECTIVE_SHADOW"


class TestForwardScoringDiscipline:
    def _rows(self, n_games, n_dates, threshold=4):
        rows = []
        for i in range(n_games):
            rows.append({"gameId": "g%d" % i, "settleDate": "2026-09-%02d" % (i % n_dates + 1),
                         "threshold": threshold, "outcome": i % 2,
                         "candidateProbability": 0.5, "controlProbability": 0.55,
                         "marketVigFreeProbability": 0.52})
        return rows

    def test_below_the_material_floor_no_inference_is_drawn(self):
        out = exp.score_forward(self._rows(60, 5))
        assert out["inferencePermitted"] is False
        assert "candidateVsControl" not in out
        assert out["status"] == "ACCRUING_BELOW_MATERIAL_FLOOR"

    def test_an_empty_forward_set_is_insufficient(self):
        assert exp.score_forward([])["status"] == "INSUFFICIENT_FORWARD_DATA"

    def test_at_the_material_floor_inference_becomes_available(self):
        out = exp.score_forward(self._rows(120, 12))
        assert out["inferencePermitted"] is True
        assert out["status"] == "MATERIAL_CHECK_AVAILABLE"
        assert "candidateVsControl" in out and "candidateVsMarket" in out

    def test_both_raw_pooled_and_standardized_are_always_reported(self):
        out = exp.score_forward(self._rows(120, 12))
        market = out["candidateVsMarket"]
        assert "rawPooledBrier" in market and "thresholdStandardized" in market

    def test_no_status_is_an_approval_token(self):
        for status in exp.FORWARD_STATUSES:
            assert "APPROV" not in status.upper()
            assert "PROMOT" not in status.upper()
            assert "PRODUCTION" not in status.upper()

    def test_both_brier_and_log_loss_are_reported(self):
        out = exp.score_forward(self._rows(120, 12))
        assert "brier" in out["candidateVsControl"] and "logLoss" in out["candidateVsControl"]

    def test_checkpoints_are_monotone_and_start_at_zero(self):
        games = [c["minGames"] for c in exp.CHECKPOINTS]
        dates = [c["minDates"] for c in exp.CHECKPOINTS]
        assert games == sorted(games) and dates == sorted(dates)
        assert games[0] == 0

    def test_inference_is_only_permitted_at_or_above_100_games(self):
        for cp in exp.CHECKPOINTS:
            if cp["inference"]:
                assert cp["minGames"] >= 100

    def test_a_stratum_below_the_row_floor_is_excluded_from_standardization(self):
        rows = self._rows(120, 12, threshold=4) + self._rows(10, 3, threshold=7)
        out = exp.threshold_standardized(rows, "candidateProbability", "marketVigFreeProbability")
        assert all(c["threshold"] != 7 for c in out["perThreshold"])


class TestItCannotPromote:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_artifact_states_it_cannot_promote(self):
        assert "cannot promote" in ARTIFACT["cannotPromote"].lower()

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_all_six_promotion_criteria_are_recorded(self):
        assert len(ARTIFACT["promotionCriteria"]) == 6

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_candidate_fingerprint_is_recorded(self):
        assert ARTIFACT["candidate"]["fingerprint"] == exp.candidate_fingerprint()
        assert ARTIFACT["candidate"]["frozenDispersion"] == 0.281513

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_persistence_path_is_recorded_and_reuses_e4(self):
        p = ARTIFACT["persistence"]
        assert p["entity"] == "team_total_nb_shadow_evaluations"
        assert p["reusesExistingE4Path"] is True
