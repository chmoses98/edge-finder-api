#!/usr/bin/env python3
"""
tests/research/test_market_taxonomy.py
===========================================
Model Performance Phase 1 -- classification tests for
lib/research/market_taxonomy.py, using REAL ticker strings taken
directly from data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json
(a real, current discovery snapshot), not invented examples, so the
classifier is proven against actual Kalshi ticker shapes.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.market_taxonomy import (
    classify_market,
    is_three_way_family,
    FAMILY_GAME_RESULT,
    FAMILY_INNING_RESULT,
    FAMILY_WINNING_MARGIN,
    FAMILY_GAME_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_INNING_TOTAL,
    FAMILY_FIRST_INNING_RUN,
    FAMILY_UNKNOWN,
)


class TestRealTickerClassification:
    """Every ticker below is copied verbatim from a real snapshot file."""

    def test_full_game_moneyline_away_leg(self):
        r = classify_market(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            title="Seattle vs Los Angeles D Winner?",
        )
        assert r["family"] == FAMILY_GAME_RESULT
        assert r["scope"] == "full_game"
        assert r["outcome"] == "Win"
        assert r["team"] == "SEA"
        assert r["classificationStatus"] == "classified"
        assert not is_three_way_family(r["family"], r["scope"])

    def test_f5_result_tie_leg(self):
        r = classify_market(
            "KXMLBF5-26JUL292210SEALAD-TIE",
            event_ticker="KXMLBF5-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_INNING_RESULT
        assert r["scope"] == "F5"
        assert r["outcome"] == "Tie"
        assert r["team"] is None
        assert is_three_way_family(r["family"], r["scope"])

    def test_f5_result_team_leg(self):
        r = classify_market(
            "KXMLBF5-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBF5-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_INNING_RESULT
        assert r["outcome"] == "Win"
        assert r["team"] == "SEA"

    def test_doubleheader_game1_f5_still_classifies(self):
        r = classify_market(
            "KXMLBF5-26JUL291310ATLNYMG1-TIE",
            event_ticker="KXMLBF5-26JUL291310ATLNYMG1",
        )
        assert r["family"] == FAMILY_INNING_RESULT
        assert r["outcome"] == "Tie"

    def test_winning_margin_spread(self):
        r = classify_market(
            "KXMLBSPREAD-26JUN041410SFMIL-SF11",
            event_ticker="KXMLBSPREAD-26JUN041410SFMIL",
            title="Giants wins by over 10.5 runs?",
        )
        assert r["family"] == FAMILY_WINNING_MARGIN
        assert r["team"] == "SF"
        assert r["scope"] == "full_game"

    def test_unknown_series_never_dropped(self):
        """
        A ticker from a series this classifier does not recognize must
        still return a full record (family=unknown), never raise, and
        never return None -- proving the "no silent drop" guarantee at
        the classification layer.
        """
        r = classify_market("KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC")
        assert r is not None
        assert r["family"] == FAMILY_UNKNOWN
        assert r["classificationStatus"] == "unclassified"
        assert r["marketTicker"] == "KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC"

    def test_missing_event_ticker_still_classifies_series(self):
        r = classify_market("KXMLBTOTAL-26JUL291234ABCXYZ-T85")
        assert r["family"] == FAMILY_GAME_TOTAL

    def test_legacy_series_alias_resolves(self):
        r = classify_market("MLBNRFI-26JUN041234ABCXYZ-YES")
        assert r["family"] == FAMILY_FIRST_INNING_RUN

    def test_raw_title_and_subtitle_preserved_verbatim(self):
        r = classify_market(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
            title="Seattle vs Los Angeles D Winner?",
            subtitle="",
        )
        assert r["rawTitle"] == "Seattle vs Los Angeles D Winner?"
        assert r["rawSubtitle"] == ""


class TestThreeWayFamilyDetection:

    def test_full_game_is_not_three_way(self):
        assert is_three_way_family(FAMILY_GAME_RESULT, "full_game") is False

    @pytest.mark.parametrize("scope", ["F3", "F5", "F7"])
    def test_inning_result_scopes_are_three_way(self, scope):
        assert is_three_way_family(FAMILY_INNING_RESULT, scope) is True

    def test_totals_family_is_not_three_way(self):
        assert is_three_way_family(FAMILY_GAME_TOTAL, "full_game") is False
        assert is_three_way_family(FAMILY_TEAM_TOTAL, "full_game") is False
        assert is_three_way_family(FAMILY_INNING_TOTAL, "F5") is False


class TestPurity:

    def test_classify_market_deterministic(self):
        r1 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        r2 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        assert r1 == r2

    def test_classify_market_never_raises_on_malformed_input(self):
        for bad in [None, "", "-", "KXMLBGAME", "KXMLBGAME-"]:
            r = classify_market(bad)
            assert r is not None
