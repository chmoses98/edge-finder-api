#!/usr/bin/env python3
"""
tests/research/test_market_handler_registry.py
====================================================
Model Performance Phase 1 -- dynamic market-discovery registry tests.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.market_handler_registry import (
    evaluate_market_research,
    evaluate_market_batch_research,
    STATUS_EVALUATED,
    STATUS_UNSUPPORTED_MARKET,
    STATUS_MISSING_DATA,
    STATUS_CLASSIFICATION_FAILED,
    STATUS_SETTLEMENT_RULE_UNRESOLVED,
    STATUS_STRUCTURE_UNRESOLVED,
)


class TestStructureUnresolved:
    """Model Performance Phase 2A Part 12 -- F3/F7 get a MORE SPECIFIC
    status than generic settlement-rule-unresolved."""

    def test_f3_title_fallback_market_clears_structure_gate_blocked_by_settlement_gate(self):
        """
        Spread/F3-F7-correction mission: a live dispatch of
        scripts/discover_kalshi_series_catalogue.py confirmed F3 is a
        genuine three-way Kalshi series, so it now clears this
        module's FIRST gate (outcome-structure verification) --
        previously it never got past STATUS_STRUCTURE_UNRESOLVED. It
        is still stopped by the SECOND, independently stricter gate
        (SETTLEMENT_VERIFIED_FAMILIES), which requires this repository
        to have actually read Kalshi's own rules_primary/rules_secondary
        text -- ticker/title structure evidence is not sufficient for
        THAT bar, and F3/F7 are deliberately not added to
        SETTLEMENT_VERIFIED_FAMILIES by this mission (see
        lib.research.inning_result_settlement, which settles F3/F7 from
        the score directly -- a materially different, less strict
        claim than "Kalshi's own rules text has been read").
        """
        row = evaluate_market_research(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-TIE",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings tie?",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_SETTLEMENT_RULE_UNRESOLVED
        assert row["family"] == "inning_result"
        assert row["scope"] == "F3"

    def test_f7_title_fallback_market_clears_structure_gate_blocked_by_settlement_gate(self):
        row = evaluate_market_research(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-ABC",
            event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings winner?",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_SETTLEMENT_RULE_UNRESOLVED
        assert row["scope"] == "F7"

    def test_f5_unaffected_still_evaluated(self):
        row = evaluate_market_research(
            "KXMLBF5-26JUL292210SEALAD-TIE",
            event_ticker="KXMLBF5-26JUL292210SEALAD",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_EVALUATED

    def test_title_fallback_market_never_hits_classification_failed(self):
        row = evaluate_market_research(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-TIE",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings tie?",
        )
        assert row["status"] != STATUS_CLASSIFICATION_FAILED

    def test_batch_reconciliation_with_mixed_known_and_unknown_horizons(self):
        markets = [
            {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD"},
            {"market_ticker": "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-TIE",
             "event_ticker": "KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
             "title": "Athletics vs Rangers first 3 innings tie?"},
            {"market_ticker": "KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC"},
        ]
        rows = evaluate_market_batch_research(markets, context={"awayFullProj": 4.5, "homeFullProj": 4.3})
        assert len(rows) == len(markets)
        statuses = {r["status"] for r in rows}
        assert STATUS_EVALUATED in statuses  # KXMLBF5
        # F3 now clears the structure gate (spread/F3-F7-correction
        # mission) but is still stopped by the independently stricter
        # settlement-rules-text gate (SETTLEMENT_VERIFIED_FAMILIES).
        assert STATUS_SETTLEMENT_RULE_UNRESOLVED in statuses
        assert STATUS_CLASSIFICATION_FAILED in statuses  # KXSOMETHINGNEW with no matching title
        assert all(r["status"] is not None for r in rows)


class TestNoSilentDrop:

    def test_every_input_market_produces_exactly_one_output_row(self):
        markets = [
            {"market_ticker": "KXMLBGAME-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBGAME-26JUL292210SEALAD"},
            {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD"},
            {"market_ticker": "KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC"},
            {"market_ticker": "KXMLBTEAMTOTAL-26JUL291000ABCXYZ-SF-O4"},
            {"market_ticker": None},
        ]
        rows = evaluate_market_batch_research(markets, context={"awayFullProj": 4.5, "homeFullProj": 4.3})
        assert len(rows) == len(markets)
        for row in rows:
            assert row["status"] is not None

    def test_unknown_series_gets_classification_failed_not_dropped(self):
        row = evaluate_market_research("KXNEWSERIES-26JUL29ABCXYZ-ABC")
        assert row["status"] == STATUS_CLASSIFICATION_FAILED
        assert row["reasonCodes"]

    def test_none_ticker_never_raises(self):
        row = evaluate_market_research(None)
        assert row["status"] == STATUS_CLASSIFICATION_FAILED


class TestStatusAssignment:

    def test_supported_family_with_full_context_evaluates(self):
        row = evaluate_market_research(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_EVALUATED
        assert row["result"] is not None
        assert "awayWinProb" in row["result"]
        assert "tieProb" in row["result"]
        assert "homeWinProb" in row["result"]

    def test_f5_result_evaluates_three_way(self):
        row = evaluate_market_research(
            "KXMLBF5-26JUL292210SEALAD-TIE",
            event_ticker="KXMLBF5-26JUL292210SEALAD",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_EVALUATED
        assert row["result"]["tieProb"] > 0

    def test_missing_projection_data_yields_missing_data_status(self):
        row = evaluate_market_research(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            context={},  # no awayFullProj/homeFullProj
        )
        assert row["status"] == STATUS_MISSING_DATA
        assert "awayFullProj" in row["reasonCodes"]

    def test_unimplemented_family_yields_unsupported_market(self):
        """
        game_total is classified and its settlement is verified, but
        Phase 1 deliberately shipped a placeholder handler for it
        (_unimplemented_handler) -- proving the registry correctly
        reports "Missing Data" (the placeholder always returns
        missing=[...]) rather than crashing or silently omitting the
        market. This documents the CURRENT state honestly: the
        placeholder is wired in and reachable, but does not yet
        produce a real research probability.
        """
        row = evaluate_market_research(
            "KXMLBTOTAL-26JUL291234ABCXYZ-T85",
            event_ticker="KXMLBTOTAL-26JUL291234ABCXYZ",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert row["status"] == STATUS_MISSING_DATA

    def test_unresolved_settlement_family_status(self):
        """
        pitcher_strikeouts is classified in the taxonomy module's
        SERIES_FAMILY_MAP concept but has NO series ticker mapping at
        all in this phase's confirmed inventory (no pitcher-prop
        series was discovered on Kalshi this phase) -- so any ticker
        claiming that family via an unmapped series resolves to
        Classification Failed, not Settlement Rule Unresolved, since
        classification itself fails first. This test instead directly
        exercises a KNOWN series/scope pair not in
        SETTLEMENT_VERIFIED_FAMILIES to prove that status path.
        """
        from lib.research.market_handler_registry import SETTLEMENT_VERIFIED_FAMILIES
        assert ("pitcher_strikeouts", None) not in SETTLEMENT_VERIFIED_FAMILIES


class TestDeterministicDispatch:

    def test_repeated_dispatch_identical(self):
        r1 = evaluate_market_research(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        r2 = evaluate_market_research(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            context={"awayFullProj": 4.5, "homeFullProj": 4.3},
        )
        assert r1 == r2

    def test_no_context_mutation(self):
        import copy
        context = {"awayFullProj": 4.5, "homeFullProj": 4.3}
        before = copy.deepcopy(context)
        evaluate_market_research(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            context=context,
        )
        assert context == before
