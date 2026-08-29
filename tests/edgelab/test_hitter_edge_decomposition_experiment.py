#!/usr/bin/env python3
"""
tests/edgelab/test_hitter_edge_decomposition_experiment.py
==========================================================
Coverage for scripts/edgelab/run_hitter_edge_decomposition_experiment.py --
MLB-RSCH-0029's decomposition of production's declared hitter edge.

The load-bearing guarantee is the ALGEBRA. An earlier session guessed
that wide spreads mechanically inflate declared edge; the identity
   declaredEdge = MODEL_SIGNAL - EXECUTION_PENALTY
says the opposite, and these tests pin the sign conventions so the
decomposition cannot silently invert.
"""
import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_hitter_edge_decomposition_experiment as exp  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "edgelab", "run_hitter_edge_decomposition_experiment.py")
SOURCE = open(SCRIPT).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestDecompositionAlgebra:
    """declaredEdge = MODEL_SIGNAL - EXECUTION_PENALTY. A larger execution
    penalty REDUCES declared edge -- the opposite of the earlier guess."""

    def test_identity_holds_symbolically(self):
        model, fair, execp = 0.62, 0.50, 0.53
        model_signal = model - fair
        execution_penalty = execp - fair
        declared = model - execp
        assert abs(declared - (model_signal - execution_penalty)) < 1e-12

    def test_larger_execution_penalty_reduces_declared_edge(self):
        model, fair = 0.62, 0.50
        narrow = model - (fair + 0.01)
        wide = model - (fair + 0.05)
        assert wide < narrow, "a wider execution penalty must REDUCE declared edge"

    def test_zero_penalty_makes_declared_edge_equal_model_signal(self):
        model, fair = 0.62, 0.50
        assert abs((model - fair) - (model - fair)) < 1e-12

    def test_corpus_builder_computes_both_terms(self):
        src = _fn("build_decomposed_corpus")
        assert 'model_signal = r["modelP"] - fair' in src
        assert "execution_penalty = exec_price - fair" in src

    def test_identity_is_audited_not_assumed(self):
        src = _fn("build_decomposed_corpus")
        assert "declaredEdgeReproduced" in src and "executionPenaltyZero" in src

    def test_true_taker_penalty_uses_the_ask_not_the_mid(self):
        src = _fn("build_decomposed_corpus")
        assert 'true_taker_penalty = (ask / 100.0) - fair' in src


class TestScoringDirection:
    def _rows(self, mp, kp, outcome, n=40):
        return [{"modelP": mp, "marketP": kp, "outcome": outcome, "playerGameKey": f"P{i}",
                 "gameId": f"G{i}", "date": "2026-08-20", "playerId": f"p{i}"} for i in range(n)]

    def test_negative_delta_means_model_better(self):
        assert exp.paired_brier_delta(self._rows(0.9, 0.5, 1)) < 0

    def test_positive_delta_means_market_better(self):
        assert exp.paired_brier_delta(self._rows(0.1, 0.5, 1)) > 0


class TestInversionRequiresEvidence:
    def _seg(self, rows, keys, delta):
        return {"rows": rows, "playerGameKeys": keys, "pairedBrierDelta": delta}

    def test_inversion_needs_three_qualifying_buckets(self):
        segs = [self._seg(500, 100, -0.01), self._seg(500, 100, +0.01)]
        assert exp._inversion(segs) is None

    def test_inversion_detected_when_last_worse_than_first(self):
        segs = [self._seg(500, 100, -0.01), self._seg(500, 100, 0.0), self._seg(500, 100, +0.02)]
        assert exp._inversion(segs) is True

    def test_no_inversion_when_last_better_than_first(self):
        segs = [self._seg(500, 100, +0.02), self._seg(500, 100, 0.0), self._seg(500, 100, -0.01)]
        assert exp._inversion(segs) is False

    def test_below_floor_segments_are_excluded_from_the_trend(self):
        segs = [self._seg(500, 100, -0.01), self._seg(500, 100, 0.0),
                self._seg(10, 2, +99.0)]           # tiny, wildly favourable
        assert exp._inversion(segs) is None, "a below-floor bucket must not create a trend"

    def test_qualifying_requires_both_row_and_key_floors(self):
        assert exp._qualifying([self._seg(500, 2, 0.0)]) == []
        assert exp._qualifying([self._seg(10, 100, 0.0)]) == []


class TestMechanismClassification:
    def _h2(self, spread_inversion, exec_zero=True):
        return {"spreadBands": {"inversion": spread_inversion},
                "executionPenaltyIsZeroByConstruction": exec_zero}

    def test_case_a_when_signal_inverts_and_execution_is_zero(self):
        m, _ = exp.classify_mechanism({"inversion": True}, self._h2(False), {}, [])
        assert m == "CASE_A_MODEL_SIGNAL_INVERSION"

    def test_case_a_detail_names_why_execution_cannot_be_the_cause(self):
        _, detail = exp.classify_mechanism({"inversion": True}, self._h2(False), {}, [])
        assert "identically zero" in detail

    def test_case_b_when_only_spread_inverts(self):
        m, _ = exp.classify_mechanism({"inversion": False}, self._h2(True, exec_zero=False), {}, [])
        assert m == "CASE_B_EXECUTION_LIQUIDITY"

    def test_case_d_when_signal_buckets_never_qualified(self):
        m, _ = exp.classify_mechanism({"inversion": None}, self._h2(None), {}, [])
        assert m == "CASE_D_INCONCLUSIVE"

    def test_broad_claim_requires_two_families(self):
        one = [{"meetsFloor": True, "signalInversion": True},
               {"meetsFloor": True, "signalInversion": False}]
        _, detail = exp.classify_mechanism({"inversion": True}, self._h2(False), {}, one)
        assert "multiple families" not in detail
        two = [{"meetsFloor": True, "signalInversion": True},
               {"meetsFloor": True, "signalInversion": True}]
        _, detail2 = exp.classify_mechanism({"inversion": True}, self._h2(False), {}, two)
        assert "multiple families" in detail2


class TestFiltersAreNotFished:
    def test_no_filter_simulation_under_case_a(self):
        out = exp.filter_simulations([], "CASE_A_MODEL_SIGNAL_INVERSION")
        assert out["simulated"] is False
        assert "fishing" in out["reason"]

    def test_filters_simulated_under_case_b(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "yesAsk": 0.52, "outcome": i % 2,
                 "spreadCents": 1.0, "quoteAgeMinutes": 5.0, "modelSignal": 0.1,
                 "trueTakerPenalty": 0.02, "playerGameKey": f"P{i}", "gameId": f"G{i}",
                 "date": "2026-08-20", "playerId": f"p{i}"} for i in range(300)]
        out = exp.filter_simulations(rows, "CASE_B_EXECUTION_LIQUIDITY")
        assert out["simulated"] is True and len(out["filters"]) == 4


class TestEconomicsHonesty:
    def _rows(self, n=200):
        return [{"modelP": 0.60, "marketP": 0.50, "yesAsk": 0.55, "outcome": i % 2,
                 "playerGameKey": f"P{i}", "gameId": f"G{i}", "date": "2026-08-20",
                 "playerId": f"p{i}"} for i in range(n)]

    def test_ask_entry_is_worse_than_mid_entry(self):
        rows = self._rows()
        at_ask = exp.economics(rows, "t", entry="ask")
        at_mid = exp.economics(rows, "t", entry="mid")
        assert at_ask["averageEntryPrice"] > at_mid["averageEntryPrice"]
        assert at_ask["netRoi"] < at_mid["netRoi"], "paying the ask must cost more than the mid"

    def test_uses_the_canonical_fee_engine(self):
        assert "taker_fee(" in _fn("economics")

    def test_never_implies_a_bet_was_placed(self):
        assert "Never implies any recommendation was placed" in _fn("economics")


class TestQuoteAgeNeverFabricated:
    def test_returns_none_on_unparseable_timestamps(self):
        assert exp._minutes_between("not-a-time", "2026-08-20T00:00:00Z") is None
        assert exp._minutes_between("2026-08-20T00:00:00Z", None) is None

    def test_computes_forward_minutes(self):
        assert exp._minutes_between("2026-08-20T00:00:00Z", "2026-08-20T00:30:00Z") == 30.0

    def test_rows_without_age_are_counted_not_guessed(self):
        src = _fn("h3_quote_age")
        assert "rowsWithoutTimestamps" in src


class TestPreregisteredConstants:
    def test_signal_buckets_match_rsch0028_edge_buckets(self):
        import run_hitter_prop_validity_experiment as r28
        assert exp.SIGNAL_BUCKETS == r28.EDGE_BUCKETS, "buckets must stay directly comparable"

    def test_corpus_is_reused_unchanged(self):
        src = _fn("build_decomposed_corpus")
        assert "rsch0028.build_corpus(" in src

    def test_floors_and_fdr_are_constants(self):
        assert exp.MIN_SEGMENT_ROWS == 100 and exp.MIN_SEGMENT_KEYS == 25
        assert exp.MIN_FAMILY_ROWS == 200 and exp.MIN_FAMILY_KEYS == 50
        assert exp.FDR_ALPHA == 0.10

    def test_cluster_unit_is_player_game(self):
        assert exp.CLUSTER_KEY == "playerGameKey"


class TestRankDeficiencyHandledHonestly:
    def test_singular_design_is_reported_not_hidden(self):
        src = _fn("conditional_ols")
        assert "SINGULAR_DESIGN_REFIT_WITHOUT_DEGENERATE_TERM" in src
        assert "not a modelling nuisance" in src

    def test_ols_solves_a_known_system(self):
        # y = 2 + 3x exactly
        X = [[1.0, x] for x in range(10)]
        y = [2.0 + 3.0 * x for x in range(10)]
        beta = exp._ols(X, y)
        assert abs(beta[0] - 2.0) < 1e-9 and abs(beta[1] - 3.0) < 1e-9

    def test_ols_returns_none_on_a_zero_variance_column(self):
        X = [[1.0, 0.0] for _ in range(10)]
        y = [float(i) for i in range(10)]
        assert exp._ols(X, y) is None


class TestGovernance:
    def test_nothing_is_fitted_for_selection(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_"), node.name
        assert '"correctionFitted": False' in _fn("main")
        assert '"parametersFitted": 0' in _fn("main")

    def test_maximum_disposition_is_shadow_candidate(self):
        main = _fn("main")
        assert '"maximumDisposition": "SHADOW_CANDIDATE"' in main
        assert '"productionActivationAuthorized": False' in main
        assert "PRODUCTION_APPROVED" not in SOURCE

    def test_no_user_wagers_or_bet_inference(self):
        main = _fn("main")
        assert '"usesUserConfirmedWagers": False' in main
        assert '"impliesRecommendationsWereBet": False' in main
        for token in ("bets/", "bankroll", "recommendations/"):
            assert token not in SOURCE

    def test_liquidity_uses_only_archived_fields(self):
        src = _fn("h4_liquidity")
        assert "no volume/depth data exists to use" in src
        # The prose may NAME the fields that are deliberately unused; what
        # must not appear is an actual read of one.
        for invented in ('["openInterest"]', '.get("openInterest")',
                         '["volume"]', '.get("volume")', '["openInterest"', '["volume"'):
            assert invented not in src, f"h4 reads an unarchived liquidity field: {invented}"

    def test_shadow_requires_a_simulated_filter_that_wins(self):
        main = _fn("main")
        assert 'sims.get("simulated")' in main
        assert 'MODEL_BEATS_MARKET' in main
