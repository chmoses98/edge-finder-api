#!/usr/bin/env python3
"""
tests/test_f5_three_way_pricing.py
======================================
F5 Three-Way Pricing Correction milestone: coverage for the F5 pricing
fix in scripts/build_market_ledger.py (vig_free_3way, contract_pricing,
validate_f5_three_way, american_to_ask_cents, F5PricingError) and the
full-game invariance guarantee.

Root cause (see docs/F5_THREE_WAY_PRICING.md for the full writeup):
Kalshi's F5 market has a real, separately tradable TIE contract
(confirmed via a live market snapshot,
data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json --
every KXMLBF5 event lists exactly 3 tickers, e.g.
KXMLBF5-26JUL291310ATLNYMG1-{ATL,NYM,TIE}), unlike full-game KXMLBGAME
events (exactly 2 tickers, no tie -- a real MLB game always continues to
extra innings until a winner is decided). Production's prior F5 pricing
computed the correct three-way Poisson probability then discarded the
tie by renormalizing away/home to sum to 1 -- systematically overstating
both team-side fair probabilities. This is fixed by NEVER renormalizing:
away + tie + home now sum to 1 directly from the same score-distribution
model (lib.research.three_way_projection.three_way_result_probs).
"""
import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

import build_market_ledger as bml  # noqa: E402
from build_market_ledger import evaluate_game  # noqa: E402
from lib.research.three_way_projection import three_way_result_probs  # noqa: E402
from test_lineup_gate import _make_game  # noqa: E402


def _row(ledger, market):
    for r in ledger:
        if r["market"] == market:
            return r
    raise KeyError(f"Market {market!r} not found in ledger")


def _game_with_f5_tie(tie_american=545, tie_ticker="KXMLBF5-26JUN101545AAAHH-TIE", **kwargs):
    """_make_game() (tests/test_lineup_gate.py) predates the F5 tie contract
    fields -- add them here rather than duplicating the whole fixture."""
    game = _make_game(**kwargs)
    game["odds"]["kalshi"]["f5ml"]["tie"] = tie_american
    game["odds"]["kalshi"]["f5ml"]["tie_american"] = tie_american
    game["odds"]["kalshi"]["f5ml"]["tie_ticker"] = tie_ticker
    return game


# ── Known Poisson win/tie/loss fixture (also used in the milestone's
# before/after numerical writeup, docs/F5_THREE_WAY_PRICING.md) ────────────
FIXTURE_AWAY_PROJ = 2.3
FIXTURE_HOME_PROJ = 1.9
# Computed once via three_way_result_probs(2.3, 1.9, max_runs=20):
FIXTURE_P_AWAY = 0.4759229464373686
FIXTURE_P_TIE = 0.1982552389362656
FIXTURE_P_HOME = 0.3258218146263657


class TestKnownPoissonFixture:

    def test_fixture_matches_precomputed_values(self):
        r = three_way_result_probs(FIXTURE_AWAY_PROJ, FIXTURE_HOME_PROJ, max_runs=20)
        assert r["awayWinProb"] == pytest.approx(FIXTURE_P_AWAY, abs=1e-9)
        assert r["tieProb"] == pytest.approx(FIXTURE_P_TIE, abs=1e-9)
        assert r["homeWinProb"] == pytest.approx(FIXTURE_P_HOME, abs=1e-9)

    def test_sum_to_one(self):
        r = three_way_result_probs(FIXTURE_AWAY_PROJ, FIXTURE_HOME_PROJ, max_runs=20)
        total = r["awayWinProb"] + r["tieProb"] + r["homeWinProb"]
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_tie_probability_is_nonzero_and_material(self):
        """The tie is not a rounding artifact -- it's ~20% of the outcome space."""
        r = three_way_result_probs(FIXTURE_AWAY_PROJ, FIXTURE_HOME_PROJ, max_runs=20)
        assert r["tieProb"] > 0.15

    def test_no_two_way_renormalization_present_in_model_probabilities(self):
        """
        The legacy bug: p_away_net = p_away_win / (1 - p_tie). Prove the
        CORRECTED awayWinProb is NOT equal to that renormalized value --
        i.e. this fixture actually exercises the fix, not a no-op.
        """
        r = three_way_result_probs(FIXTURE_AWAY_PROJ, FIXTURE_HOME_PROJ, max_runs=20)
        legacy_renormalized_away = r["awayWinProb"] / (1 - r["tieProb"])
        assert r["awayWinProb"] < legacy_renormalized_away
        assert legacy_renormalized_away == pytest.approx(0.593609050598505, abs=1e-6)


class TestVigFree3Way:

    def test_sums_to_one(self):
        vf_a, vf_t, vf_h = bml.vig_free_3way(-130, 260, 150)
        assert vf_a + vf_t + vf_h == pytest.approx(1.0, abs=1e-9)

    def test_matches_hand_computed_example(self):
        # From docs/F5_THREE_WAY_PRICING.md's before/after example.
        vf_a, vf_t, vf_h = bml.vig_free_3way(-130, 260, 150)
        assert vf_a == pytest.approx(0.4547, abs=1e-3)
        assert vf_t == pytest.approx(0.2235, abs=1e-3)
        assert vf_h == pytest.approx(0.3218, abs=1e-3)

    def test_none_on_missing_tie_price(self):
        """Never silently falls back to a two-way calc on partial data."""
        assert bml.vig_free_3way(-130, None, 150) == (None, None, None)

    def test_none_on_any_missing_price(self):
        assert bml.vig_free_3way(None, 260, 150) == (None, None, None)
        assert bml.vig_free_3way(-130, 260, None) == (None, None, None)

    def test_differs_from_two_way_calc_on_same_away_home_odds(self):
        """The market-side twin of the model-side renormalization bug."""
        vf_a_2way, vf_h_2way = bml.vig_free_2way(-130, 150)
        vf_a_3way, _, vf_h_3way = bml.vig_free_3way(-130, 260, 150)
        assert vf_a_3way < vf_a_2way
        assert vf_h_3way < vf_h_2way


class TestContractPricing:

    def test_all_five_fields_present(self):
        result = bml.contract_pricing(0.4759, 0.4547, 56.0)
        assert set(result) == {
            "modelFairProbability", "modelFairPrice", "marketImpliedProbability",
            "estimatedEdge", "expectedValuePerDollar",
            # Production Fee-Aware Net EV Integration milestone: additive
            # fee-aware companion to expectedValuePerDollar, informational
            # only (this block is display-only, see contract_pricing()'s
            # docstring) -- never removes/renames the original five.
            "netExpectedValuePerDollar",
        }

    def test_model_fair_price_is_probability_as_cents(self):
        result = bml.contract_pricing(0.4759, 0.4547, 56.0)
        assert result["modelFairProbability"] == 47.59
        assert result["modelFairPrice"] == 47.59

    def test_expected_value_per_dollar_positive_when_model_beats_market(self):
        # model 47.59% fair vs ask price of 40 cents (cheap relative to model) -> positive EV
        result = bml.contract_pricing(0.4759, 0.4547, 40.0)
        assert result["expectedValuePerDollar"] > 0

    def test_expected_value_per_dollar_negative_when_overpriced(self):
        result = bml.contract_pricing(0.20, 0.22, 90.0)
        assert result["expectedValuePerDollar"] < 0

    def test_none_ask_price_yields_none_ev(self):
        result = bml.contract_pricing(0.4759, 0.4547, None)
        assert result["expectedValuePerDollar"] is None

    def test_none_model_prob_yields_all_none(self):
        result = bml.contract_pricing(None, 0.4547, 56.0)
        assert result["modelFairProbability"] is None
        assert result["modelFairPrice"] is None
        assert result["expectedValuePerDollar"] is None


class TestF5PricingSafetyGates:

    def test_valid_three_way_passes(self):
        bml.validate_f5_three_way(
            FIXTURE_P_AWAY, FIXTURE_P_TIE, FIXTURE_P_HOME,
            "AWAY-TICKER", "TIE-TICKER", "HOME-TICKER",
            -130, 260, 150,
        )  # must not raise

    def test_sum_not_one_raises(self):
        with pytest.raises(bml.F5PricingError, match="sum to"):
            bml.validate_f5_three_way(0.5, 0.3, 0.3, "A", "T", "H", -130, 260, 150)

    def test_out_of_range_probability_raises(self):
        with pytest.raises(bml.F5PricingError, match="out of \\[0, 1\\] range"):
            bml.validate_f5_three_way(1.5, -0.3, -0.2, "A", "T", "H", -130, 260, 150)

    def test_missing_tie_price_with_away_home_present_raises(self):
        """
        A three-way F5 market missing its tie price must fail loudly,
        never silently revert to a two-way calculation.
        """
        with pytest.raises(bml.F5PricingError, match="no tie price"):
            bml.validate_f5_three_way(
                FIXTURE_P_AWAY, FIXTURE_P_TIE, FIXTURE_P_HOME,
                "AWAY-TICKER", "TIE-TICKER", "HOME-TICKER",
                -130, None, 150,
            )

    def test_missing_all_prices_does_not_raise_the_tie_gate(self):
        """If away/home are ALSO missing, that's a different (Missing Data) case handled upstream, not this gate."""
        bml.validate_f5_three_way(
            FIXTURE_P_AWAY, FIXTURE_P_TIE, FIXTURE_P_HOME,
            "AWAY-TICKER", "TIE-TICKER", "HOME-TICKER",
            None, None, None,
        )  # must not raise -- no away/home price means this specific gate doesn't fire

    def test_duplicate_ticker_raises(self):
        with pytest.raises(bml.F5PricingError, match="duplicate"):
            bml.validate_f5_three_way(
                FIXTURE_P_AWAY, FIXTURE_P_TIE, FIXTURE_P_HOME,
                "SAME-TICKER", "TIE-TICKER", "SAME-TICKER",
                -130, 260, 150,
            )

    def test_null_tickers_never_falsely_flagged_as_duplicates(self):
        bml.validate_f5_three_way(
            FIXTURE_P_AWAY, FIXTURE_P_TIE, FIXTURE_P_HOME,
            None, "TIE-TICKER", None,
            -130, 260, 150,
        )  # must not raise -- two Nones are not "the same ticker"


class TestAmericanToAskCents:

    def test_prefers_real_yes_ask(self):
        assert bml.american_to_ask_cents({"yes_ask": 55}, -130) == 55

    def test_falls_back_to_american_derived_implied_prob(self):
        # -130 implied = 130/230 = 56.52%
        result = bml.american_to_ask_cents({}, -130)
        assert result == pytest.approx(56.52, abs=0.01)

    def test_none_when_both_unavailable(self):
        assert bml.american_to_ask_cents({}, None) is None


class TestF5PricingVersion:

    def test_current_version_is_three_way(self):
        assert bml.F5_PRICING_VERSION_CURRENT == bml.F5_PRICING_VERSION_THREE_WAY

    def test_legacy_and_current_versions_are_distinct_strings(self):
        assert bml.F5_PRICING_VERSION_LEGACY_TWO_WAY != bml.F5_PRICING_VERSION_THREE_WAY


class TestEndToEndWiring:
    """evaluate_game() end-to-end, via the real fixture builder + tie fields."""

    def test_f5_rows_carry_pricing_version_and_three_way_block(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        for market in ("F5_ML_Away", "F5_ML_Home"):
            row = _row(ledger, market)
            assert row["f5PricingVersion"] == bml.F5_PRICING_VERSION_CURRENT
            assert row["f5ThreeWay"] is not None
            total = (row["f5ThreeWay"]["awayWinProbability"]
                     + row["f5ThreeWay"]["tieProbability"]
                     + row["f5ThreeWay"]["homeWinProbability"])
            # Each of the three fields is independently rounded to 2
            # decimal places for display, so up to ~0.015 of cumulative
            # rounding error is expected -- the underlying, unrounded
            # probabilities are what actually sum to 1 (see
            # TestKnownPoissonFixture.test_sum_to_one for that guarantee).
            assert total == pytest.approx(100.0, abs=0.02)

    def test_away_and_home_rows_share_the_identical_three_way_block(self):
        """Both rows must reflect ONE shared computation -- never two independently-computed, potentially-drifting values."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        home_row = _row(ledger, "F5_ML_Home")
        assert away_row["f5ThreeWay"] == home_row["f5ThreeWay"]
        assert away_row["f5TieContract"] == home_row["f5TieContract"]

    def test_tie_contract_ticker_is_the_real_tie_ticker(self):
        game = _game_with_f5_tie(tie_ticker="KXMLBF5-26JUN101545AAAHH-TIE")
        ledger = evaluate_game(game)
        row = _row(ledger, "F5_ML_Away")
        assert row["f5TieContract"]["ticker"] == "KXMLBF5-26JUN101545AAAHH-TIE"

    def test_away_home_ticker_mapping_unaffected_by_the_fix(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        home_row = _row(ledger, "F5_ML_Home")
        if away_row["status"] == "Accepted":
            assert away_row["marketTicker"] == "KXMLBF5-26JUN101545AAAHH-AAA"
        if home_row["status"] == "Accepted":
            assert home_row["marketTicker"] == "KXMLBF5-26JUN101545AAAHH-HHH"

    def test_missing_tie_price_routes_f5_rows_to_missing_data_not_two_way_fallback(self):
        """
        Away/home F5 prices present, tie price absent -- must fail loudly
        (Missing Data), never silently fall back to the legacy two-way
        calculation.
        """
        game = _make_game()  # no tie fields added -- f5ml has away/home only
        ledger = evaluate_game(game)
        for market in ("F5_ML_Away", "F5_ML_Home"):
            row = _row(ledger, market)
            assert row["status"] == "Missing Data"
            assert "F5PricingError" in " ".join(row.get("missingFields") or []) or \
                   any("tie" in (f or "").lower() for f in (row.get("missingFields") or []))

    def test_duplicate_ticker_routes_to_missing_data_not_a_crash(self):
        game = _game_with_f5_tie(tie_ticker="KXMLBF5-26JUN101545AAAHH-AAA")  # collides with away_ticker
        ledger = evaluate_game(game)
        for market in ("F5_ML_Away", "F5_ML_Home"):
            row = _row(ledger, market)
            assert row["status"] == "Missing Data"

    def test_repeated_evaluation_is_deterministic(self):
        game = _game_with_f5_tie()
        ledger1 = evaluate_game(copy.deepcopy(game))
        ledger2 = evaluate_game(copy.deepcopy(game))
        assert _row(ledger1, "F5_ML_Away") == _row(ledger2, "F5_ML_Away")
        assert _row(ledger1, "F5_ML_Home") == _row(ledger2, "F5_ML_Home")


class TestFullGameInvarianceUnderTheF5Fix:
    """
    Full-game MLB winner markets are two-way after extra innings (a real
    game always continues until a winner is decided -- no tradable tie
    contract exists, confirmed via a live market snapshot). This fix must
    NOT touch ML_Away/ML_Home at all.
    """

    def test_ml_rows_unaffected_by_f5_tie_fields(self):
        """Adding F5 tie data to the game must not change ML_Away/ML_Home at all."""
        game_without_tie = _make_game()
        game_with_tie = _game_with_f5_tie()
        ledger_without = evaluate_game(game_without_tie)
        ledger_with = evaluate_game(game_with_tie)
        for market in ("ML_Away", "ML_Home"):
            row_without = _row(ledger_without, market)
            row_with = _row(ledger_with, market)
            assert row_without["modelProb"] == row_with["modelProb"]
            assert row_without["kalshiVF"] == row_with["kalshiVF"]
            assert row_without["status"] == row_with["status"]
            assert row_without.get("confidence") == row_with.get("confidence")

    def test_ml_rows_never_carry_f5_pricing_fields(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        for market in ("ML_Away", "ML_Home"):
            row = _row(ledger, market)
            assert "f5PricingVersion" not in row
            assert "f5ThreeWay" not in row
            assert "f5TieContract" not in row

    def test_ml_still_uses_the_legacy_two_way_renormalization_by_design(self):
        """
        Full-game ML SHOULD still renormalize away from any push mass --
        a completed MLB game never actually ties. Proves this fix left
        that formula (p_team_wins + vig_free_2way) untouched for ML.
        """
        game = _game_with_f5_tie(ml_away_am=-130, ml_home_am=+150)
        ledger = evaluate_game(game)
        away_row = _row(ledger, "ML_Away")
        vf_away, vf_home = bml.vig_free_2way(-130, 150)
        assert away_row["kalshiVF"] == pytest.approx(round(vf_away * 100, 2), abs=0.01)

    def test_full_game_moneyline_source_unchanged_string_match(self):
        """
        Belt-and-suspenders: the ML_Away/ML_Home evaluation block's own
        source lines (p_team_wins/vig_free_2way call, renormalization)
        must still be present verbatim -- this fix only touches the F5
        block.
        """
        with open(os.path.join(SCRIPTS_DIR, "build_market_ledger.py")) as f:
            src = f.read()
        assert "p_away_win, p_push = p_team_wins(away_proj, home_proj)" in src
        assert "vf_away, vf_home = vig_free_2way(ml_away_am, ml_home_am)" in src


class TestNoProductionFallbackToLegacyF5Math:
    """
    Safety-gate requirement (item 11): a three-way market must never be
    accidentally routed through two-way normalization. Structural, not
    just a runtime check -- the F5 evaluation block below no longer
    calls vig_free_2way or the renormalize-after-p_team_wins pattern at
    all, so this failure mode is eliminated by construction, not just
    detected after the fact.
    """

    def test_f5_block_never_calls_vig_free_2way(self):
        with open(os.path.join(SCRIPTS_DIR, "build_market_ledger.py")) as f:
            src = f.read()
        f5_block_start = src.index("# ── F5_ML_Away / F5_ML_Home")
        f5_block_end = src.index("# ── NRFI / YRFI")
        f5_block = src[f5_block_start:f5_block_end]
        assert "vig_free_2way(" not in f5_block
        assert "p_team_wins(" not in f5_block
        assert "vig_free_3way(" in f5_block
        assert "three_way_result_probs(" in f5_block

    def test_f5_block_never_renormalizes_by_dividing_out_the_tie(self):
        with open(os.path.join(SCRIPTS_DIR, "build_market_ledger.py")) as f:
            src = f.read()
        f5_block_start = src.index("# ── F5_ML_Away / F5_ML_Home")
        f5_block_end = src.index("# ── NRFI / YRFI")
        f5_block = src[f5_block_start:f5_block_end]
        assert "/ (1 - p_push" not in f5_block
        assert "1 - p_push_f5" not in f5_block
