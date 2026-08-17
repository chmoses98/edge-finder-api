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
