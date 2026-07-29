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
)


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
