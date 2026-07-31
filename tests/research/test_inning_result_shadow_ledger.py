#!/usr/bin/env python3
"""
tests/research/test_inning_result_shadow_ledger.py
=======================================================
Model Performance Phase 2A Part 9/10 -- tests for
lib/research/inning_result_shadow_ledger.py.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_shadow_ledger import (
    build_shadow_ledger_row,
    build_shadow_ledger,
)

F5_AWAY = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
           "yes_bid": 0.42, "yes_ask": 0.44, "volume": 120}
F5_HOME = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-LAD", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
           "yes_bid": 0.37, "yes_ask": 0.39, "volume": 95}
F5_TIE = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
          "yes_bid": 0.17, "yes_ask": 0.19, "volume": 40}
GAME_RESULT = {"market_ticker": "KXMLBGAME-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBGAME-26JUL292210SEALAD",
               "yes_bid": 0.5, "yes_ask": 0.52}
F3_UNKNOWN = {"market_ticker": "KXMLBUNKNOWNF3-26JUL292210SEALAD-SEA",
              "event_ticker": "KXMLBUNKNOWNF3-26JUL292210SEALAD",
              "title": "Seattle first 3 innings winner?", "yes_bid": 0.40, "yes_ask": 0.45, "volume": 10}

CONTEXT = {"away_team": "SEA", "home_team": "LAD", "away_full_proj": 4.5, "home_full_proj": 4.3,
           "snapshot_timestamp": "2026-07-29T22:10:00Z"}


class TestF5RowsVerified:

    def test_f5_away_row_has_canonical_and_legacy_probs(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_AWAY, CONTEXT)
        assert row["scope"] == "F5"
        assert row["outcome"] == "Away"
        assert row["marketStructure"] == "THREE_WAY"
        assert row["canonicalModelProb"] is not None
        assert row["legacyConditionalProb"] is not None
        assert row["status"] == "Evaluated"

    def test_f5_tie_row_has_canonical_prob_no_legacy(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_TIE, CONTEXT)
        assert row["outcome"] == "Tie"
        assert row["canonicalModelProb"] is not None
        assert row["legacyConditionalProb"] is None

    def test_f5_rows_paper_eligible_not_real_money_eligible(self):
        for m in (F5_AWAY, F5_HOME, F5_TIE):
            row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", m, CONTEXT)
            assert row["researchEligible"] is True
            assert row["paperEligible"] is True
            assert row["realMoneyEligible"] is False
            assert row["activationStatus"] == "PAPER_ONLY"

    def test_no_bid_ask_derived_from_yes_side(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_AWAY, CONTEXT)
        assert row["noBid"] == pytest.approx(1.0 - F5_AWAY["yes_ask"])
        assert row["noAsk"] == pytest.approx(1.0 - F5_AWAY["yes_bid"])

    def test_midpoint_not_labeled_executable_field_names(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_AWAY, CONTEXT)
        assert "midpoint" in row
        assert "executableYesEdge" in row and "executableNoEdge" in row
        # midpoint itself must never be used as the edge basis
        assert row["executableYesEdge"] == pytest.approx(row["canonicalModelProb"] - row["yesAsk"], abs=1e-3)

    def test_executable_no_edge_uses_no_ask(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_AWAY, CONTEXT)
        assert row["executableNoEdge"] == pytest.approx((1.0 - row["canonicalModelProb"]) - row["noAsk"], abs=1e-3)

    def test_missing_projection_context_yields_missing_data_status(self):
        ctx = {"away_team": "SEA", "home_team": "LAD"}
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F5_AWAY, ctx)
        assert row["status"] == "Missing Data"
        assert row["canonicalModelProb"] is None


class TestF3NowVerifiedStructure:
    """
    Spread/F3-F7-correction mission: a live dispatch of
    scripts/discover_kalshi_series_catalogue.py against the real
    Kalshi exchange confirmed F3 is a genuine three-way series (see
    lib.research.market_taxonomy.HORIZON_MARKET_STATUS docstring for
    the exact evidence). F3 now behaves exactly like F5 here -- no
    code change was needed in this module, only the taxonomy flag
    flip that this class's own module (build_shadow_ledger_row)
    already consulted via classify_inning_result_market().
    """

    def test_f3_row_evaluated_not_structure_unresolved(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F3_UNKNOWN, CONTEXT)
        assert row["status"] == "Evaluated"
        assert row["marketStructure"] == "THREE_WAY"

    def test_f3_row_has_real_model_edge(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F3_UNKNOWN, CONTEXT)
        assert row["canonicalModelProb"] is not None
        assert row["executableYesEdge"] is not None

    def test_f3_row_paper_eligible_not_real_money_eligible(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F3_UNKNOWN, CONTEXT)
        assert row["researchEligible"] is True
        assert row["paperEligible"] is True
        assert row["realMoneyEligible"] is False
        assert row["activationStatus"] == "PAPER_ONLY"

    def test_f3_row_raw_prices_still_preserved(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", F3_UNKNOWN, CONTEXT)
        assert row["yesBid"] == F3_UNKNOWN["yes_bid"]
        assert row["yesAsk"] == F3_UNKNOWN["yes_ask"]


class TestGameResultExcluded:

    def test_non_inning_result_market_excluded_from_this_ledger(self):
        row = build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", GAME_RESULT, CONTEXT)
        assert row is None


class TestNoRealMoneyEligibility:

    def test_no_row_is_ever_real_money_eligible(self):
        rows = build_shadow_ledger("2026-07-29", "g1", "SEA@LAD",
                                    [F5_AWAY, F5_HOME, F5_TIE, F3_UNKNOWN], CONTEXT)
        assert len(rows) == 4  # game_result excluded, all 4 inning-result markets present
        for row in rows:
            assert row["realMoneyEligible"] is False


class TestPurityAndDeterminism:

    def test_build_shadow_ledger_deterministic(self):
        r1 = build_shadow_ledger("2026-07-29", "g1", "SEA@LAD", [F5_AWAY, F5_TIE], CONTEXT)
        r2 = build_shadow_ledger("2026-07-29", "g1", "SEA@LAD", [F5_AWAY, F5_TIE], CONTEXT)
        assert r1 == r2

    def test_does_not_mutate_input_market_dicts(self):
        market_copy = dict(F5_AWAY)
        build_shadow_ledger_row("2026-07-29", "g1", "SEA@LAD", market_copy, CONTEXT)
        assert market_copy == F5_AWAY
