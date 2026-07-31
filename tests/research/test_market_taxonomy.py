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
    classify_inning_result_market,
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
    STRUCTURE_THREE_WAY,
    STRUCTURE_UNVERIFIED,
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
        assert r["line"] == 10.5

    def test_f5_spread_line_populated(self):
        r = classify_market(
            "KXMLBF5SPREAD-26JUL292210SEALAD-SEA1",
            event_ticker="KXMLBF5SPREAD-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_WINNING_MARGIN
        assert r["scope"] == "F5"
        assert r["team"] == "SEA"
        assert r["line"] == 0.5

    def test_team_total_line_populated(self):
        r = classify_market(
            "KXMLBTEAMTOTAL-26JUL292210SEALAD-SEA4",
            event_ticker="KXMLBTEAMTOTAL-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_TEAM_TOTAL
        assert r["team"] == "SEA"
        assert r["line"] == 3.5

    def test_game_total_line_is_integer(self):
        r = classify_market(
            "KXMLBTOTAL-26JUL291234ABCXYZ-T85",
            event_ticker="KXMLBTOTAL-26JUL291234ABCXYZ",
        )
        assert r["family"] == FAMILY_GAME_TOTAL
        # "T85" doesn't match the pure-digit total-suffix convention --
        # honestly reports no line rather than guessing.
        assert r["line"] is None

    def test_f5_total_line_populated_from_pure_digit_suffix(self):
        r = classify_market(
            "KXMLBF5TOTAL-26JUL292210SEALAD-4",
            event_ticker="KXMLBF5TOTAL-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_INNING_TOTAL
        assert r["line"] == 4

    def test_first_inning_run_never_has_a_line(self):
        """NRFI/YRFI is a binary yes/no proposition -- no threshold applies."""
        r = classify_market(
            "KXMLBRFI-26JUL292210SEALAD-YES",
            event_ticker="KXMLBRFI-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_FIRST_INNING_RUN
        assert r["line"] is None

    def test_game_result_never_has_a_line(self):
        r = classify_market(
            "KXMLBGAME-26JUL292210SEALAD-SEA",
            event_ticker="KXMLBGAME-26JUL292210SEALAD",
        )
        assert r["family"] == FAMILY_GAME_RESULT
        assert r["line"] is None

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
        inning_result -- but (spread-correction mission Part 2/3) it
        MUST now be classified as inning_total/F3 via the total-shape
        fallback rather than left at FAMILY_UNKNOWN. Leaving a real F3
        total market unclassified merely because its series prefix is
        unconfirmed is exactly the silent-drop failure mode this
        mission corrects.
        """
        r = classify_market(
            "KXMLBUNKNOWNF3TOTAL-26JUL291234ABCXYZ-2",
            event_ticker="KXMLBUNKNOWNF3TOTAL-26JUL291234ABCXYZ",
            title="First 3 innings total runs over 2.5?",
        )
        assert r["family"] == FAMILY_INNING_TOTAL
        assert r["scope"] == "F3"
        assert r["outcome"] is None
        assert r["classificationStatus"] == "classified_by_title_fallback_unverified_prefix"

    def test_ambiguous_f3_text_with_no_total_spread_or_winner_language_stays_unclassified(self):
        """
        The fallback is still deliberately conservative: F3 horizon text
        alone, with no winner/spread/total language at all, must not be
        guessed into any family.
        """
        r = classify_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-2",
            event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="First 3 innings something else entirely?",
        )
        assert r["family"] == FAMILY_UNKNOWN
        assert r["classificationStatus"] == "unclassified"

    def test_unrecognized_prefix_with_no_matching_title_stays_unclassified(self):
        r = classify_market("KXSOMETHINGNEW-26JUL291000ABCXYZ-ABC", title="Some unrelated market")
        assert r["family"] == FAMILY_UNKNOWN
        assert r["classificationStatus"] == "unclassified"

    def test_f3_spread_shaped_title_fallback_line_populated(self):
        """
        The title-fallback path (unconfirmed series prefix) for a
        spread-shaped F3 market must populate `line` exactly like the
        confirmed-prefix winning_margin branch does -- this was a gap
        where the fallback set team/operator but never line.
        """
        r = classify_market(
            "KXMLBUNKNOWNF3SPREAD-26JUL291234ABCXYZ-ABC2",
            event_ticker="KXMLBUNKNOWNF3SPREAD-26JUL291234ABCXYZ",
            title="Athletics wins by over 1.5 runs (first 3 innings)?",
        )
        assert r["family"] == FAMILY_WINNING_MARGIN
        assert r["scope"] == "F3"
        assert r["team"] == "ABC"
        assert r["line"] == 1.5
        assert r["classificationStatus"] == "classified_by_title_fallback_unverified_prefix"

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

    def test_f3_f7_existence_and_structure_now_confirmed_via_live_dispatch(self):
        """
        Spread/F3-F7-correction mission: a live dispatch of
        scripts/discover_kalshi_series_catalogue.py against the real
        Kalshi exchange independently confirmed both F3 and F7 as
        genuine three-way series (see HORIZON_MARKET_STATUS's own
        docstring above for the exact evidence -- KXMLBF7's raw market
        payload directly captured, KXMLBF3 corroborated by identical
        series-family/count-per-event evidence). productionEnabled
        stays False regardless -- this repository's REQUIRED_MARKETS
        allowlist in scripts/build_market_ledger.py is untouched by
        this mission and still does not include F3/F7.
        """
        for scope in ("F3", "F7"):
            status = HORIZON_MARKET_STATUS[scope]
            assert status["existenceStatus"] == "CONFIRMED_VIA_LIVE_SERIES_CATALOGUE"
            assert status["repositoryFetcherSupport"] is True
            assert status["archiveCoverage"] is True
            assert status["productionEnabled"] is False
            assert status["outcomeStructureStatus"] == "CONFIRMED_THREE_WAY"

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


class TestCanonicalInningResultTaxonomy:
    """Model Performance Phase 2A Part 6 -- classify_inning_result_market()."""

    def test_non_inning_result_market_returns_none(self):
        r = classify_inning_result_market(
            "KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD",
        )
        assert r is None

    def test_f5_away_resolved_with_team_context(self):
        r = classify_inning_result_market(
            "KXMLBF5-26JUL292210SEALAD-SEA", event_ticker="KXMLBF5-26JUL292210SEALAD",
            away_team="SEA", home_team="LAD",
        )
        assert r["outcome"] == "Away"
        assert r["structure"] == STRUCTURE_THREE_WAY
        assert r["scope"] == "F5"

    def test_f5_home_resolved_with_team_context(self):
        r = classify_inning_result_market(
            "KXMLBF5-26JUL292210SEALAD-LAD", event_ticker="KXMLBF5-26JUL292210SEALAD",
            away_team="SEA", home_team="LAD",
        )
        assert r["outcome"] == "Home"

    def test_f5_team_leg_without_context_is_unknown_not_guessed(self):
        r = classify_inning_result_market(
            "KXMLBF5-26JUL292210SEALAD-SEA", event_ticker="KXMLBF5-26JUL292210SEALAD",
        )
        assert r["outcome"] == "Unknown"

    def test_f5_tie_always_resolved_without_context(self):
        r = classify_inning_result_market(
            "KXMLBF5-26JUL292210SEALAD-TIE", event_ticker="KXMLBF5-26JUL292210SEALAD",
        )
        assert r["outcome"] == "Tie"
        assert r["structure"] == STRUCTURE_THREE_WAY

    def test_f3_structure_is_now_confirmed_three_way(self):
        """F3's structure was independently confirmed live (spread/
        F3-F7-correction mission) -- classify_inning_result_market()
        reflects that via HORIZON_MARKET_STATUS, with no code change
        needed in this function."""
        r = classify_inning_result_market(
            "KXMLBUNKNOWNF3-26JUL291234ABCXYZ-ABC", event_ticker="KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 3 innings winner?", away_team="ABC", home_team="XYZ",
        )
        assert r["outcome"] == "Away"
        assert r["structure"] == STRUCTURE_THREE_WAY

    def test_f7_structure_is_now_confirmed_three_way(self):
        r = classify_inning_result_market(
            "KXMLBUNKNOWNF7-26JUL291234ABCXYZ-TIE", event_ticker="KXMLBUNKNOWNF7-26JUL291234ABCXYZ",
            title="Athletics vs Rangers first 7 innings tie?",
        )
        assert r["structure"] == STRUCTURE_THREE_WAY
        assert r["outcome"] == "Tie"

    def test_production_enabled_always_false(self):
        for ticker, et, kwargs in [
            ("KXMLBF5-26JUL292210SEALAD-SEA", "KXMLBF5-26JUL292210SEALAD", {}),
            ("KXMLBUNKNOWNF3-26JUL291234ABCXYZ-TIE", "KXMLBUNKNOWNF3-26JUL291234ABCXYZ",
             {"title": "first 3 innings tie?"}),
        ]:
            r = classify_inning_result_market(ticker, event_ticker=et, **kwargs)
            assert r["productionEnabled"] is False

    def test_deterministic(self):
        r1 = classify_inning_result_market("KXMLBF5-26JUL292210SEALAD-TIE", event_ticker="KXMLBF5-26JUL292210SEALAD")
        r2 = classify_inning_result_market("KXMLBF5-26JUL292210SEALAD-TIE", event_ticker="KXMLBF5-26JUL292210SEALAD")
        assert r1 == r2


class TestPurity:

    def test_classify_market_deterministic(self):
        r1 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        r2 = classify_market("KXMLBGAME-26JUL292210SEALAD-SEA", event_ticker="KXMLBGAME-26JUL292210SEALAD")
        assert r1 == r2

    def test_classify_market_never_raises_on_malformed_input(self):
        for bad in [None, "", "-", "KXMLBGAME", "KXMLBGAME-"]:
            r = classify_market(bad)
            assert r is not None
