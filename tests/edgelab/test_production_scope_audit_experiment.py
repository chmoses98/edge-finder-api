#!/usr/bin/env python3
"""
tests/edgelab/test_production_scope_audit_experiment.py
======================================================
Coverage for scripts/edgelab/run_production_scope_audit_experiment.py --
MLB-RSCH-0027's production-scope integrity and family-resolved skill audit.

The load-bearing guarantees here are governance ones: that nothing is
fitted, that the preregistered decision rule cannot be quietly relaxed,
that the recovered corpus is settled from postgame data used ONLY as an
evaluation target, and that the forward window is never touched.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

import run_production_scope_audit_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_production_scope_audit_experiment.py")
SOURCE = open(SCRIPT_PATH).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestNothingIsFitted:
    """The whole point of this experiment is that it does not fit anything.
    Four predecessors died fitting corrections to this archive."""

    def test_no_fitting_function_is_defined(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_"), f"{node.name} looks like a fitting routine"

    def test_no_optimizer_is_imported_or_called(self):
        for banned in ("scipy.optimize", "minimize(", "newton", "golden", "gradient"):
            assert banned not in SOURCE, f"{banned} suggests a parameter is being estimated"

    def test_artifact_declares_zero_parameters_fitted(self):
        assert '"parametersFitted": 0' in _fn("main")


class TestScopeDefinitions:
    def test_production_and_research_families_are_disjoint(self):
        assert not (exp.PRODUCTION_FAMILIES & exp.RESEARCH_ONLY_FAMILIES)

    def test_scope_of_classifies_each_known_family(self):
        assert exp.scope_of("KXMLBTEAMTOTAL") == "PRODUCTION"
        assert exp.scope_of("pitcher_strikeouts") == "RESEARCH_ONLY"
        assert exp.scope_of("something_new") == "UNCLASSIFIED"

    def test_recoverable_families_are_a_subset_of_synthetic_key_families(self):
        assert exp.RECOVERABLE_FAMILIES <= exp.SYNTHETIC_KEY_FAMILIES

    def test_recoverable_families_are_only_those_a_final_score_can_settle(self):
        # NRFI/YRFI need first-inning runs and F5 needs a score through five;
        # neither is derivable from a final score, so neither may be recovered.
        assert exp.RECOVERABLE_FAMILIES == {"ML_Home", "ML_Away"}
        for f in ("NRFI", "YRFI", "F5_ML_Home", "F5_ML_Away"):
            assert f not in exp.RECOVERABLE_FAMILIES


class TestSyntheticKeyParsing:
    def test_parses_a_synthetic_key(self):
        assert exp.parse_synthetic_key("823514:ML_Home") == ("823514", "ML_Home")

    def test_rejects_a_real_kalshi_ticker(self):
        assert exp.parse_synthetic_key("KXMLBGAME-26JUL301910MIANYM-NYM") == (None, None)

    @pytest.mark.parametrize("bad", ["", None, "abc:ML_Home", "823514:NOT_A_FAMILY", "823514"])
    def test_rejects_malformed_keys(self, bad):
        assert exp.parse_synthetic_key(bad) == (None, None)


class TestMoneylineRecoveryIsCorrect:
    FINALS = {"1": {"homeScore": 5, "awayScore": 3, "officialDate": "2026-08-20"},
              "2": {"homeScore": 2, "awayScore": 7, "officialDate": "2026-08-21"},
              "3": {"homeScore": 4, "awayScore": 4, "officialDate": "2026-08-22"}}

    def _row(self, ticker):
        return {"marketTicker": ticker, "createdAt": "2026-08-20T00:00:00Z", "gameId": None,
                "modelP": 0.5, "marketP": 0.5, "confidence": None}

    def test_home_win_settles_home_yes_and_away_no(self):
        rows, _ = exp.recover_moneyline_rows(
            [self._row("1:ML_Home"), self._row("1:ML_Away")], self.FINALS)
        by = {r["marketFamily"]: r["outcome"] for r in rows}
        assert by == {"ML_Home": 1, "ML_Away": 0}

    def test_away_win_settles_away_yes_and_home_no(self):
        rows, _ = exp.recover_moneyline_rows(
            [self._row("2:ML_Home"), self._row("2:ML_Away")], self.FINALS)
        by = {r["marketFamily"]: r["outcome"] for r in rows}
        assert by == {"ML_Home": 0, "ML_Away": 1}

    def test_a_tie_is_dropped_never_settled(self):
        rows, diag = exp.recover_moneyline_rows([self._row("3:ML_Home")], self.FINALS)
        assert rows == []
        assert diag["droppedNoFinalScoreOrTie"] == 1

    def test_a_game_with_no_final_score_is_dropped(self):
        rows, diag = exp.recover_moneyline_rows([self._row("99:ML_Home")], self.FINALS)
        assert rows == [] and diag["droppedNoFinalScoreOrTie"] == 1

    def test_unrecoverable_families_are_counted_but_never_settled(self):
        rows, diag = exp.recover_moneyline_rows([self._row("1:NRFI"), self._row("1:F5_ML_Home")], self.FINALS)
        assert rows == []
        assert diag["syntheticKeyRowsByFamily"] == {"NRFI": 1, "F5_ML_Home": 1}

    def test_keeps_the_last_evaluation_per_ticker(self):
        early = dict(self._row("1:ML_Home"), createdAt="2026-08-01T00:00:00Z", modelP=0.11)
        late = dict(self._row("1:ML_Home"), createdAt="2026-08-19T00:00:00Z", modelP=0.99)
        rows, _ = exp.recover_moneyline_rows([early, late], self.FINALS)
        assert len(rows) == 1 and rows[0]["modelP"] == 0.99

    def test_settle_date_comes_from_the_game_not_the_evaluation(self):
        rows, _ = exp.recover_moneyline_rows([self._row("2:ML_Home")], self.FINALS)
        assert rows[0]["settleDate"] == "2026-08-21"


class TestScoringDirectionality:
    """A sign error here would invert the experiment's conclusion."""

    def _rows(self, model_p, market_p, outcome):
        return [{"modelP": model_p, "marketP": market_p, "outcome": outcome, "gameId": f"G{i}"}
                for i in range(50)]

    def test_negative_delta_means_production_is_better(self):
        # production nails it, market does not
        assert exp.paired_brier_delta(self._rows(0.9, 0.5, 1)) < 0

    def test_positive_delta_means_production_is_worse(self):
        assert exp.paired_brier_delta(self._rows(0.1, 0.5, 1)) > 0

    def test_identical_forecasts_give_exactly_zero(self):
        assert exp.paired_brier_delta(self._rows(0.4, 0.4, 1)) == 0.0

    def test_log_loss_delta_agrees_in_sign_with_brier(self):
        rows = self._rows(0.9, 0.5, 1)
        assert exp.paired_log_loss_delta(rows) < 0 and exp.paired_brier_delta(rows) < 0

    def test_log_loss_is_finite_at_a_degenerate_price(self):
        # research boards carry prices at exactly 0.0/1.0; clamping must keep
        # the metric finite rather than yielding inf and poisoning a mean
        assert math_isfinite(exp.log_loss(self._rows(1.0, 1.0, 0), "modelP"))


def math_isfinite(x):
    import math
    return math.isfinite(x)


class TestPreregisteredRuleCannotBeRelaxed:
    def test_all_four_conditions_are_required(self):
        source = _fn("family_analysis")
        assert "c1 and c2 and c3 and c4" in source, "the rule must require ALL four conditions"

    def test_sample_floors_are_the_preregistered_constants(self):
        assert exp.MIN_ROWS_FAMILY == 100
        assert exp.MIN_GAMES_FAMILY == 20
        assert exp.FDR_ALPHA == 0.10

    def test_a_family_below_the_floor_cannot_be_called_skilful(self):
        rows = [{"modelP": 0.01, "marketP": 0.99, "outcome": 0, "gameId": f"G{i}",
                 "marketFamily": "KXMLBF5", "settleDate": "2026-08-26"} for i in range(30)]
        entries = exp.family_analysis(rows)
        assert entries[0]["pairedBrierDelta"] < 0            # production is better here
        assert entries[0]["verdict"] == "INSUFFICIENT_SAMPLE"  # and still cannot be claimed

    def test_an_in_window_win_that_reverses_in_holdout_is_not_skill(self):
        train = [{"modelP": 0.01, "marketP": 0.99, "outcome": 0, "gameId": f"T{i}",
                  "marketFamily": "F", "settleDate": "2026-08-20"} for i in range(200)]
        holdout = [{"modelP": 0.99, "marketP": 0.01, "outcome": 0, "gameId": f"H{i}",
                    "marketFamily": "F", "settleDate": "2026-08-27"} for i in range(60)]
        entry = exp.family_analysis(train + holdout)[0]
        assert entry["conditions"]["4_directionHoldsInHoldout"] is False
        assert entry["verdict"] != "PRODUCTION_SHOWS_SKILL"

    def test_holdout_boundary_is_the_preregistered_date(self):
        assert exp.TRAIN_DATE_MAX == "2026-08-24"


class TestBenjaminiHochberg:
    def test_rejects_nothing_when_all_p_values_are_large(self):
        assert exp.benjamini_hochberg([0.9, 0.8, 0.7], 0.10) == set()

    def test_rejects_a_clearly_significant_family(self):
        assert 0 in exp.benjamini_hochberg([0.0001, 0.9, 0.8], 0.10)

    def test_ignores_families_with_no_p_value(self):
        assert exp.benjamini_hochberg([None, None], 0.10) == set()

    def test_is_stricter_than_uncorrected_testing(self):
        # A lone p=0.04 among five null families passes an uncorrected 0.10
        # test but must NOT survive BH: at rank 1 the threshold is
        # (1/6)*0.10 = 0.0167. This is the multiplicity protection that stops
        # one lucky family out of six from being called a discovery.
        assert exp.benjamini_hochberg([0.04, 0.9, 0.9, 0.9, 0.9, 0.9], 0.10) == set()

    def test_rejects_all_when_every_family_is_jointly_significant(self):
        # BH is not merely "stricter" -- when all six agree at p=0.04 the
        # step-up procedure does reject all six, and that is correct.
        assert exp.benjamini_hochberg([0.04] * 6, 0.10) == set(range(6))


class TestForwardWindowUntouched:
    def test_forward_start_date_matches_the_frozen_forward_convention(self):
        assert exp.FORWARD_START_DATE == "2026-08-28"

    def test_main_reports_how_many_forward_rows_were_touched(self):
        assert "forwardWindowRowsTouched" in _fn("main")


class TestGovernance:
    def test_economics_are_computed_only_for_a_family_that_already_passed(self):
        main = _fn("main")
        assert "for e in passing:" in main, "economics must iterate only over passing families"

    def test_economics_use_the_canonical_fee_engine(self):
        assert "taker_fee(" in _fn("secondary_economics")

    def test_maximum_disposition_is_level_1(self):
        main = _fn("main")
        assert '"LEVEL_1_SHADOW_CANDIDATE"' in main
        assert "LEVEL_2" not in main and "PRODUCTION_APPROVED" not in SOURCE

    def test_production_activation_is_explicitly_not_authorized(self):
        assert '"productionActivationAuthorized": False' in _fn("main")

    def test_prior_artifacts_are_not_rewritten(self):
        assert '"priorExperimentArtifactsRewritten": False' in _fn("main")
        # the only files this experiment writes are its own
        assert _fn("main").count("open(ARTIFACT_PATH") == 1

    def test_scoping_observations_are_disclosed_as_not_preregistered(self):
        assert _fn("main").count('"observedDuringScopingNotPreregistered": True') == 2

    def test_postgame_score_is_declared_an_evaluation_target_only(self):
        registration = _fn("register_experiment")
        assert '"team_recent_game_log_reconstruction": "EVALUATION_TARGET"' in registration
        assert '"settlement_outcome": "EVALUATION_TARGET"' in registration

    def test_schedule_loader_is_documented_as_never_predictive(self):
        assert "never used as a predictive input" in _fn("load_schedule_finals")


class TestDeterminism:
    def test_bootstrap_p_value_is_deterministic(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "outcome": i % 2, "gameId": f"G{i // 3}"}
                for i in range(150)]
        a = exp.game_clustered_bootstrap_pvalue(rows, exp.paired_brier_delta)
        b = exp.game_clustered_bootstrap_pvalue(rows, exp.paired_brier_delta)
        assert a == b and a is not None

    def test_p_value_is_a_probability(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "outcome": i % 2, "gameId": f"G{i // 3}"}
                for i in range(150)]
        assert 0.0 <= exp.game_clustered_bootstrap_pvalue(rows, exp.paired_brier_delta) <= 1.0

    def test_family_analysis_is_order_independent(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "outcome": i % 2, "gameId": f"G{i}",
                 "marketFamily": "F", "settleDate": "2026-08-26"} for i in range(150)]
        assert exp.family_analysis(rows) == exp.family_analysis(list(reversed(rows)))
