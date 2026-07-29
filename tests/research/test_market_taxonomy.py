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
    HORIZON_MARKET_STATUS,
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


class TestTitleFallbackClassification:
    """
    Model Performance Phase 1 CORRECTION -- proves the classifier no
    longer requires a series to be pre-approved by ticker prefix before
    it can be classified (mission Part 6/10). These tickers use an
    UNRECOGNIZED prefix on purpose (simulating this repository's actual
    guessed-wrong-prefix situation for F3/F7) -- classification here can
    only succeed via title/subtitle text, never via SERIES_FAMILY_MAP.
    """

    def test_f3_title_classifies_as_scope_f3_with_unknown_prefix(self):
        r = classify_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-ABC",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings winner?",
        )
        assert r["family"] == FAMILY_INNING_RESULT
        assert r["scope"] == "F3"
        assert r["classificationStatus"] == "classified_by_title_fallback_unverified_prefix"

    def test_f7_title_classifies_as_scope_f7_with_unknown_prefix(self):
        r = classify_market(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-XYZ",
            event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings winner?",
        )
        assert r["family"] == FAMILY_INNING_RESULT
        assert r["scope"] == "F7"
        assert r["classificationStatus"] == "classified_by_title_fallback_unverified_prefix"

    def test_f3_away_home_outcomes_remain_separate(self):
        away = classify_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-ABC",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings winner?",
        )
        home = classify_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-XYZ",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings winner?",
        )
        assert away["team"] == "ABC"
        assert home["team"] == "XYZ"
        assert away["outcome"] == home["outcome"] == "Win"
        assert away != home

    def test_f7_away_home_outcomes_remain_separate(self):
        away = classify_market(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-ABC",
            event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings winner?",
        )
        home = classify_market(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-XYZ",
            event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings winner?",
        )
        assert away["team"] == "ABC"
        assert home["team"] == "XYZ"
        assert away != home

    def test_f3_tie_never_treated_as_push(self):
        r = classify_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-TIE",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings tie?",
        )
        assert r["outcome"] == "Tie"
        assert r["operator"] == "equals"
        assert is_three_way_family(r["family"], r["scope"]) is True

    def test_f7_tie_never_treated_as_push(self):
        r = classify_market(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-TIE",
            event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings tie?",
        )
        assert r["outcome"] == "Tie"
        assert r["operator"] == "equals"
        assert is_three_way_family(r["family"], r["scope"]) is True

    def test_total_shaped_f3_text_not_misclassified_as_result_market(self):
        """
        A total/spread-shaped market sharing F3 horizon text but with no
        "winner"/"wins" language must NOT be misclassified as
        inning_result -- the fallback is deliberately conservative.
        """
        r = classify_market(
            "KXMLBUNKNOWNF3TOTAL-26JUL291234ABCXYZ-2",
            event_ticker="KXMLBUNKNOWNF3TOTAL-26JUL291234ABCXYZ",
            title="First 3 innings total runs over 2.5?",
        )
        assert r["family"] == FAMILY_UNKNOWN
        assert r["classificationStatus"] == "unclassified"

    def test_unrecognized_prefix_with_no_matching_title_stays_unclassified(self):
        r = classify_market("KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC", title="Some unrelated market")
        assert r["family"] == FAMILY_UNKNOWN
        assert r["classificationStatus"] == "unclassified"

    def test_f5_prefix_not_affected_by_title_fallback_addition(self):
        """F5's existing precise prefix-based match must be unaffected."""
        r = classify_market(
            "KXMLBF5-26JUL292210SEALAD-TIE",
            event_ticker="KXMLBF5-26JUL292210SEALAD",
        )
        assert r["classificationStatus"] == "classified"


class TestHorizonMarketStatusCorrection:
    """
    Proves the corrected, honest existence/support status distinction
    (Model Performance Phase 1 CORRECTION) -- the single source of truth
    reused by the inventory builder and projection comparison scripts.
    """

    def test_f3_f7_existence_confirmed_by_user_not_repository(self):
        for scope in ("F3", "F7"):
            status = HORIZON_MARKET_STATUS[scope]
            assert status["existenceStatus"] == "EXISTS_ON_KALSHI_USER_CONFIRMED"
            assert status["repositoryFetcherSupport"] is False
            assert status["archiveCoverage"] is False
            assert status["productionEnabled"] is False
            assert status["outcomeStructureStatus"] == "UNVERIFIED"

    def test_f5_and_full_game_remain_confirmed_via_repository(self):
        assert HORIZON_MARKET_STATUS["F5"]["existenceStatus"] == "CONFIRMED_VIA_REPOSITORY_SNAPSHOT"
        assert HORIZON_MARKET_STATUS["F5"]["outcomeStructureStatus"] == "CONFIRMED_THREE_WAY"
        assert HORIZON_MARKET_STATUS["full_game"]["existenceStatus"] == "CONFIRMED_VIA_REPOSITORY_SNAPSHOT"
        assert HORIZON_MARKET_STATUS["full_game"]["outcomeStructureStatus"] == "CONFIRMED_TWO_WAY"

    def test_f3_f7_normalization_and_projection_support_are_real(self):
        """
        Even though F3/F7 are not ingested, this repository's
        classifier and projection math already support them -- this
        must not be conflated with "not supported at all."
        """
        for scope in ("F3", "F7"):
            status = HORIZON_MARKET_STATUS[scope]
            assert status["normalizationSupport"] is True
            assert status["projectionSupport"] is True

    def test_no_status_dict_claims_nonexistence(self):
        import json
        blob = json.dumps(HORIZON_MARKET_STATUS)
        assert "does not exist" not in blob
        assert "does not appear to offer" not in blob


class TestPurity:

    def test_classify_market_deterministic(self):
        r1 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        r2 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        assert r1 == r2

    def test_classify_market_never_raises_on_malformed_input(self):
        for bad in [None, "", "-", "KXMLBGAME", "KXMLBGAME-"]:
            r = classify_market(bad)
            assert r is not None
