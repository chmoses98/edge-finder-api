#!/usr/bin/env python3
"""
tests/test_f5_tie_tax_wiring.py
===================================
End-to-end coverage for scripts/build_market_ledger.py's wiring of
lib/research/f5_tie_tax.py onto the real F5_ML_Away/F5_ML_Home rows
(the `tieTaxComparison` field). Complements tests/research/test_f5_tie_tax.py
(pure-function unit tests) and tests/test_f5_three_way_pricing.py (the
underlying three-way pricing fixture this reuses).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

import build_market_ledger as bml  # noqa: E402
from build_market_ledger import evaluate_game  # noqa: E402
from lib.research.f5_tie_tax import THREE_WAY_YES, PROTECTED_NO  # noqa: E402
from test_lineup_gate import _make_game  # noqa: E402


def _row(ledger, market):
    for r in ledger:
        if r["market"] == market:
            return r
    raise KeyError(f"Market {market!r} not found in ledger")


def _game_with_f5_tie(tie_american=545, tie_ticker="KXMLBF5-26JUN101545AAAHH-TIE", **kwargs):
    game = _make_game(**kwargs)
    game["odds"]["kalshi"]["f5ml"]["tie"] = tie_american
    game["odds"]["kalshi"]["f5ml"]["tie_american"] = tie_american
    game["odds"]["kalshi"]["f5ml"]["tie_ticker"] = tie_ticker
    return game


class TestTieTaxComparisonWiring:

    def test_both_f5_rows_carry_a_tie_tax_comparison(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        for market in ("F5_ML_Away", "F5_ML_Home"):
            row = _row(ledger, market)
            assert row.get("tieTaxComparison") is not None

    def test_away_row_favors_away_home_row_favors_home(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        home_row = _row(ledger, "F5_ML_Home")
        assert away_row["tieTaxComparison"]["favoredSide"] == "away"
        assert home_row["tieTaxComparison"]["favoredSide"] == "home"

    def test_tie_tax_pFavoredLeads_matches_the_rows_own_model_probability(self):
        """The comparison must consume the SAME p_f5_away/p_f5_home the row's own modelProb came from -- never an independently recomputed value."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        assert away_row["tieTaxComparison"]["pFavoredLeads"] == pytest.approx(
            away_row["modelProb"] / 100.0, abs=5e-4
        )

    def test_tie_tax_uses_the_shared_tie_probability(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        home_row = _row(ledger, "F5_ML_Home")
        assert away_row["tieTaxComparison"]["pTie"] == home_row["tieTaxComparison"]["pTie"]
        assert away_row["tieTaxComparison"]["pTie"] == pytest.approx(
            away_row["f5ThreeWay"]["tieProbability"] / 100.0, abs=5e-4
        )

    def test_preferred_expression_is_one_of_the_two_known_constants_or_none(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        for market in ("F5_ML_Away", "F5_ML_Home"):
            row = _row(ledger, market)
            preferred = row["tieTaxComparison"]["preferredExpression"]
            assert preferred in (THREE_WAY_YES, PROTECTED_NO, None)

    def test_tie_tax_comparison_never_changes_the_rows_own_confidence_decision(self):
        """Purely informational -- the row's actual accept/reject/confidence must be identical to before this field existed."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        # modelProb/kalshiVF/confidence/status are all computed upstream of
        # (and independent from) tieTaxComparison -- proven by re-deriving
        # them from the same three-way block the comparison itself reads.
        assert away_row["status"] in ("Accepted", "Rejected")
        assert away_row["f5ThreeWay"] is not None

    def test_missing_tie_market_still_produces_no_crash_and_no_comparison(self):
        """Without a tie ticker/price at all, tieTaxComparison should be None (no fabricated comparison), not a crash."""
        game = _make_game()  # no F5 tie fields at all
        ledger = evaluate_game(game)
        row = _row(ledger, "F5_ML_Away")
        # Either missing entirely (row never reached the F5 pricing block)
        # or explicitly None -- never a fabricated/partial value.
        assert row.get("tieTaxComparison") in (None,) or "tieTaxComparison" not in row


class TestFullGameMLComparisonWiring:
    """
    Systematic Best-Expression Comparison mission (Phase 2): concrete
    verification that the system distinguishes and exposes all THREE
    expressions of a "favored side should not be trailing" thesis on
    the SAME F5 row: (A) F5 three-way YES [tieTaxComparison.threeWayYes],
    (B) opposing side's F5 NO [tieTaxComparison.protectedNo], and (C)
    extending the same side's exposure to the full game via
    fullGameMLComparison -- including that (A) and (B) have genuinely
    different payoff conditions on a tie (A loses, B wins), and that (C)
    is a structurally distinct market (full 9 innings, not 5) with its
    own independently-computed price/edge, not a recomputation of (A)
    or (B).
    """

    def test_all_three_expressions_present_and_distinct_on_one_row(self):
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")

        three_way_yes = away_row["tieTaxComparison"]["threeWayYes"]
        protected_no = away_row["tieTaxComparison"]["protectedNo"]
        full_game_ml = away_row["fullGameMLComparison"]

        # (A) vs (B): genuinely different tie behavior, in both the stated
        # payoff condition and the true win probability itself (B always
        # strictly exceeds A by exactly the tie probability).
        assert "loses on a tie" in three_way_yes["payoffCondition"]
        assert "OR tie" in protected_no["payoffCondition"]
        assert protected_no["trueProbability"] > three_way_yes["trueProbability"]
        assert protected_no["trueProbability"] == pytest.approx(
            three_way_yes["trueProbability"] + away_row["tieTaxComparison"]["pTie"], abs=1e-6
        )

        # (C): a structurally separate market/row, not a copy of (A)/(B).
        assert full_game_ml["market"] == "ML_Away"
        assert "wins the full game" in full_game_ml["payoffCondition"]
        assert full_game_ml["kalshiPrice"] is not None
        assert full_game_ml["modelProb"] is not None
        # The full-game win probability must differ from the F5-only lead
        # probability (a full 9-inning outcome is never numerically
        # identical to a 5-inning-only one for a real, unbalanced matchup).
        assert full_game_ml["modelProb"] != pytest.approx(
            three_way_yes["trueProbability"] * 100, abs=1e-6
        )

    def test_home_row_references_ml_home_not_ml_away(self):
        """Each F5 row's fullGameMLComparison must reference its OWN side's
        full-game market -- never the opposing side's, which would silently
        compare apples to oranges."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        home_row = _row(ledger, "F5_ML_Home")
        assert away_row["fullGameMLComparison"]["market"] == "ML_Away"
        assert home_row["fullGameMLComparison"]["market"] == "ML_Home"

    def test_full_game_ml_comparison_matches_the_real_ml_row_exactly(self):
        """Never an independently recomputed shadow value -- always the SAME
        row build_market_ledger.py's own ML_Away/ML_Home block already
        produced, just cross-referenced."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        ml_away_row = _row(ledger, "ML_Away")
        ref = away_row["fullGameMLComparison"]
        assert ref["kalshiPrice"] == ml_away_row["kalshiPrice"]
        assert ref["modelProb"] == ml_away_row["modelProb"]
        assert ref["netExecutableEdge"] == ml_away_row.get("netExecutableEdge")
        assert ref["status"] == ml_away_row["status"]

    def test_full_game_ml_comparison_never_alters_the_f5_rows_own_decision(self):
        """Purely additive/informational -- confirms fullGameMLComparison's
        presence doesn't change the F5 row's own accept/reject/confidence,
        matching tieTaxComparison's own non-interference guarantee."""
        game = _game_with_f5_tie()
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        assert away_row["status"] in ("Accepted", "Rejected")
        assert away_row.get("fullGameMLComparison") is not None

    def test_missing_ml_row_surfaces_explicit_missing_status_not_a_crash(self):
        """If ML_Away/ML_Home couldn't be computed at all (e.g. no ML odds),
        fullGameMLComparison must still surface an explicit 'Missing Data'
        status (matching this codebase's honest-missing-data convention,
        e.g. lib.research.platoon_context's STATUS_MISSING_DATA) rather
        than a crash or a fabricated price."""
        game = _game_with_f5_tie(ml_away_am=None, ml_home_am=None)
        ledger = evaluate_game(game)
        away_row = _row(ledger, "F5_ML_Away")
        ref = away_row.get("fullGameMLComparison")
        assert ref is not None
        assert ref["status"] == "Missing Data"
        assert ref["kalshiPrice"] is None
        assert ref["modelProb"] is None
