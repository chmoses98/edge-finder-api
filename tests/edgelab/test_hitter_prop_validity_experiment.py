#!/usr/bin/env python3
"""
tests/edgelab/test_hitter_prop_validity_experiment.py
=====================================================
Coverage for scripts/edgelab/run_hitter_prop_validity_experiment.py --
MLB-RSCH-0028's hitter-prop probability validity audit.

The load-bearing guarantees are about INDEPENDENCE and BENCHMARK CHOICE.
This corpus is row-rich and independence-poor (~20 rows per player-game),
and the archive carries both an executable ask and a reconstructable
vig-free mid. Getting either wrong would manufacture a result.
"""
import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_hitter_prop_validity_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_hitter_prop_validity_experiment.py")
SOURCE = open(SCRIPT_PATH).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestNothingIsFitted:
    def test_no_fitting_function_is_defined(self):
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_"), node.name

    def test_no_optimizer_present(self):
        for banned in ("scipy.optimize", "minimize(", "newton", "gradient"):
            assert banned not in SOURCE

    def test_artifact_declares_no_correction_fitted(self):
        main = _fn("main")
        assert '"parametersFitted": 0' in main
        assert '"correctionFitted": False' in main


class TestMarketBenchmarkIsVigFreeNotAsk:
    """RSCH-0024 measured the ask carrying a +0.049 upward bias. Using it as
    the predictive benchmark would fabricate a model advantage on every YES
    contract."""

    def test_fair_price_is_the_bid_ask_midpoint(self):
        src = _fn("build_corpus")
        assert "(bid + ask) / 2.0" in src

    def test_executable_ask_is_kept_separate_from_marketp(self):
        src = _fn("build_corpus")
        assert '"marketP": round(fair, 6)' in src
        assert '"executableAsk"' in src

    def test_scoring_never_reads_the_executable_ask(self):
        for name in ("paired_brier_delta", "paired_log_loss_delta", "score_block", "brier", "log_loss"):
            assert "executableAsk" not in _fn(name), f"{name} must not score against the ask"

    def test_only_economics_uses_the_ask(self):
        assert "executableAsk" in _fn("secondary_economics")

    def test_economics_uses_the_canonical_fee_engine(self):
        assert "taker_fee(" in _fn("secondary_economics")


class TestPointInTimeIntegrity:
    def test_quotes_require_the_archive_pregame_guards(self):
        src = _fn("load_pregame_quotes")
        assert "isValidPregameObservation" in src
        assert "gameStartedAtCapture" in src

    def test_contemporaneous_quote_never_returns_a_later_quote(self):
        quotes = [("2026-08-19T10:00:00Z", 10.0, 12.0),
                  ("2026-08-19T12:00:00Z", 20.0, 22.0),
                  ("2026-08-19T14:00:00Z", 30.0, 32.0)]
        got = exp.contemporaneous_quote(quotes, "2026-08-19T12:30:00Z")
        assert got[0] == "2026-08-19T12:00:00Z"

    def test_contemporaneous_quote_accepts_an_exact_tie(self):
        quotes = [("2026-08-19T12:00:00Z", 20.0, 22.0)]
        assert exp.contemporaneous_quote(quotes, "2026-08-19T12:00:00Z")[0] == "2026-08-19T12:00:00Z"

    def test_contemporaneous_quote_returns_none_when_all_are_later(self):
        quotes = [("2026-08-19T14:00:00Z", 30.0, 32.0)]
        assert exp.contemporaneous_quote(quotes, "2026-08-19T12:00:00Z") is None

    def test_outcome_is_never_read_by_a_forecast_function(self):
        # outcome may only appear in scoring, never in corpus feature building
        src = _fn("build_corpus")
        assert 'settled[ticker]' in src  # the target, explicitly
        assert "outcome" not in src.split('"outcome": settled[ticker]')[0].replace("NO_SETTLED_OUTCOME", "")


class TestIndependenceIsRespected:
    """~20 rows per player-game. Treating rows as independent would
    overstate precision by roughly an order of magnitude."""

    def test_primary_cluster_unit_is_player_game(self):
        assert exp.CLUSTER_KEY == "playerGameKey"

    def test_clustered_ci_defaults_to_the_player_game_key(self):
        assert 'cluster_key=CLUSTER_KEY' in _fn("clustered_ci")

    def test_score_block_reports_all_three_independence_levels(self):
        rows = [{"modelP": 0.4, "marketP": 0.4, "outcome": i % 2, "playerGameKey": f"P{i//5}",
                 "gameId": f"G{i//10}", "date": "2026-08-20", "playerId": f"p{i}"} for i in range(60)]
        blk = exp.score_block(rows, "t")
        assert blk["playerGameKeys"] == 12
        assert blk["independentGames"] == 6
        assert blk["independentDates"] == 1
        assert blk["rowsPerPlayerGameKey"] == 5.0

    def test_score_block_reports_game_and_date_clustered_variants(self):
        rows = [{"modelP": 0.4, "marketP": 0.5, "outcome": i % 2, "playerGameKey": f"P{i//5}",
                 "gameId": f"G{i//10}", "date": f"2026-08-2{i%3}", "playerId": f"p{i}"} for i in range(60)]
        blk = exp.score_block(rows, "t")
        assert "pairedBrierDeltaCI_gameClustered" in blk
        assert "pairedBrierDeltaCI_dateClustered" in blk

    def test_player_market_key_pins_the_exact_ladder_rung(self):
        src = _fn("build_corpus")
        assert '"playerMarketKey"' in src
        # must include family AND threshold, else different contracts get paired
        assert 'family, s.get("threshold")' in src


class TestScoringDirectionality:
    def _rows(self, mp, kp, outcome, n=40):
        return [{"modelP": mp, "marketP": kp, "outcome": outcome, "playerGameKey": f"P{i}",
                 "gameId": f"G{i}", "date": "2026-08-20", "playerId": f"p{i}"} for i in range(n)]

    def test_negative_delta_means_model_better(self):
        assert exp.paired_brier_delta(self._rows(0.9, 0.5, 1)) < 0

    def test_positive_delta_means_market_better(self):
        assert exp.paired_brier_delta(self._rows(0.1, 0.5, 1)) > 0

    def test_identical_forecasts_are_exactly_zero(self):
        assert exp.paired_brier_delta(self._rows(0.4, 0.4, 1)) == 0.0

    def test_log_loss_is_finite_at_the_extremes(self):
        import math
        assert math.isfinite(exp.log_loss(self._rows(1.0, 1.0, 0), "modelP"))


class TestVerdictVocabulary:
    def _blk(self, lo, hi, rows=1000, keys=200):
        return {"rows": rows, "playerGameKeys": keys, "pairedBrierDeltaCI": {"low": lo, "high": hi}}

    def test_ci_straddling_zero_is_parity_never_a_win(self):
        assert exp.classify(self._blk(-0.01, 0.01), min_rows=200) == "PARITY"

    def test_ci_entirely_below_zero_is_a_model_win(self):
        assert exp.classify(self._blk(-0.02, -0.001), min_rows=200) == "MODEL_BEATS_MARKET"

    def test_ci_entirely_above_zero_is_a_market_win(self):
        assert exp.classify(self._blk(0.001, 0.02), min_rows=200) == "MARKET_BEATS_MODEL"

    def test_below_floor_is_insufficient_even_when_favourable(self):
        assert exp.classify(self._blk(-0.05, -0.01, rows=10), min_rows=200) == "INSUFFICIENT_SAMPLE"

    def test_below_key_floor_is_insufficient(self):
        assert exp.classify(self._blk(-0.05, -0.01, keys=2), min_rows=200, min_keys=50) == "INSUFFICIENT_SAMPLE"

    def test_production_approved_is_not_in_the_vocabulary(self):
        assert "PRODUCTION_APPROVED" not in SOURCE


class TestPreregisteredConstantsAreFixed:
    def test_edge_buckets_are_the_preregistered_set(self):
        assert exp.EDGE_BUCKETS == ((-1.00, 0.0), (0.0, 0.025), (0.025, 0.05), (0.05, 0.075),
                                    (0.075, 0.10), (0.10, 0.15), (0.15, 1.01))

    def test_price_bands_match_prior_lab_convention(self):
        assert exp.PRICE_BANDS == ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))

    def test_floors_and_fdr_are_constants(self):
        assert exp.MIN_FAMILY_ROWS == 200 and exp.MIN_FAMILY_KEYS == 50
        assert exp.MIN_CHECKPOINT_ROWS == 200 and exp.FDR_ALPHA == 0.10

    def test_eligible_families_are_the_four_supported_hitter_families(self):
        assert exp.ELIGIBLE_FAMILIES == ("hitter_hits", "hitter_total_bases",
                                         "hitter_hits_runs_rbis", "hitter_rbis")

    def test_stolen_bases_are_never_audited_or_created(self):
        assert "hitter_stolen_bases" in exp.UNSUPPORTED_FAMILIES
        assert "hitter_stolen_bases" not in exp.ELIGIBLE_FAMILIES


class TestEdgeBucketsMeasureNotEngineer:
    def _rows(self, edge, delta_sign, n=200):
        # model worse when delta_sign>0
        mp = 0.5 + 0.3 * delta_sign
        return [{"modelP": mp, "marketP": 0.5, "outcome": 0, "declaredEdge": edge,
                 "playerGameKey": f"P{i}", "gameId": f"G{i}", "date": "2026-08-20",
                 "playerId": f"p{i}", "marketFamily": "hitter_hits"} for i in range(n)]

    def test_edge_bucket_assignment_is_by_fixed_boundaries(self):
        assert exp._edge_bucket(-0.5) == "[-1.000,+0.000)"
        assert exp._edge_bucket(0.01) == "[+0.000,+0.025)"
        assert exp._edge_bucket(0.30) == "[+0.150,+1.010)"

    def test_none_edge_is_unbucketed_not_guessed(self):
        assert exp._edge_bucket(None) is None

    def test_inversion_is_detected_when_high_edge_is_worse(self):
        # three qualifying buckets, model degrading as declared edge rises
        rows = self._rows(0.01, -1) + self._rows(0.06, 0) + self._rows(0.12, +1)
        out = exp.edge_bucket_analysis(rows)
        assert out["edgeInversion"] is True

    def test_no_inversion_when_high_edge_is_better(self):
        rows = self._rows(0.01, +1) + self._rows(0.06, 0) + self._rows(0.12, -1)
        out = exp.edge_bucket_analysis(rows)
        assert out["edgeInversion"] is False

    def test_inversion_is_none_below_three_qualifying_buckets(self):
        """The guard that stops a two-bucket accident being called a trend."""
        out = exp.edge_bucket_analysis(self._rows(0.01, -1) + self._rows(0.12, +1))
        assert out["edgeInversion"] is None


class TestBenjaminiHochberg:
    def test_lone_marginal_family_does_not_survive(self):
        assert exp.benjamini_hochberg([0.04, 0.9, 0.9, 0.9]) == set()

    def test_clearly_significant_family_survives(self):
        assert 0 in exp.benjamini_hochberg([0.0001, 0.9, 0.9, 0.9])

    def test_none_pvalues_ignored(self):
        assert exp.benjamini_hochberg([None, None]) == set()


class TestGovernance:
    def test_user_wagers_are_never_used(self):
        main = _fn("main")
        assert '"usesUserConfirmedWagers": False' in main
        assert '"impliesRecommendationsWereBet": False' in main
        for token in ("bets/", "bankroll", "recommendations/"):
            assert token not in SOURCE, f"{token} must never be read by this experiment"

    def test_issue_43_is_not_claimed_as_implemented(self):
        assert '"issue43AutoSettlementImplemented": False' in _fn("main")

    def test_maximum_disposition_is_shadow_candidate(self):
        main = _fn("main")
        assert '"maximumDisposition": "SHADOW_CANDIDATE"' in main
        assert '"productionActivationAuthorized": False' in main

    def test_economics_are_computed_after_the_verdict(self):
        main = _fn("main")
        assert main.index("families = family_analysis") < main.index("economics = {")

    def test_exclusions_are_reason_coded_never_silent(self):
        src = _fn("build_corpus")
        for code in ("NO_SETTLED_OUTCOME", "MODEL_PROBABILITY_NULL", "THRESHOLD_UNRESOLVED",
                     "NO_VALID_PREGAME_QUOTE_AT_OR_BEFORE_CHECKPOINT", "DEGENERATE_FAIR_MIDPOINT"):
            assert code in src

    def test_ambiguous_joins_are_excluded_not_guessed(self):
        src = _fn("build_corpus")
        # ticker equality only: the join is exact or the row is excluded.
        # There is no player/threshold string parsing and no alternate-key
        # fallback anywhere in the corpus builder.
        assert "ticker not in settled" in src
        assert 'excl["NO_SETTLED_OUTCOME"]' in src
        # Checked against the corpus builder's CODE, not the file's prose
        # (the join-audit description in main() legitimately names the
        # mechanisms that are deliberately NOT used).
        for fallback in ("sourceBetKey", "difflib", "SequenceMatcher", "startswith(player"):
            assert fallback not in src, f"alternate-key fallback {fallback!r} present in build_corpus"
        assert "difflib" not in SOURCE and "SequenceMatcher" not in SOURCE


class TestDeterminism:
    def test_bootstrap_pvalue_is_deterministic(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "outcome": i % 2, "playerGameKey": f"P{i//3}",
                 "gameId": f"G{i//6}", "date": "2026-08-20", "playerId": f"p{i}"} for i in range(120)]
        a = exp.clustered_pvalue(rows, exp.paired_brier_delta)
        b = exp.clustered_pvalue(rows, exp.paired_brier_delta)
        assert a == b and 0.0 <= a <= 1.0

    def test_family_analysis_is_order_independent(self):
        rows = [{"modelP": 0.6, "marketP": 0.5, "outcome": i % 2, "playerGameKey": f"P{i}",
                 "gameId": f"G{i}", "date": "2026-08-20", "playerId": f"p{i}",
                 "marketFamily": "hitter_hits", "declaredEdge": 0.01,
                 "checkpoint": "T_MINUS_30"} for i in range(300)]
        assert exp.family_analysis(rows) == exp.family_analysis(list(reversed(rows)))
