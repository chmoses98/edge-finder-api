#!/usr/bin/env python3
"""
tests/test_discover_kalshi_mlb_markets_status_fields.py
=============================================================
Spread-correction mission coverage: analysis-vs-execution status-field
separation, ranking, and F3/F7 spread/total modeling via period-scaled
projections in scripts/discover_kalshi_mlb_markets.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.discover_kalshi_mlb_markets import discover, compute_status_fields, assign_ranks  # noqa: E402


def make_game(game_id=1, away="BOS", home="NYY", start="2026-07-30T19:10:00Z"):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "awayTeamStats": {"offenseBaselineAdj": 4.5},
        "homeTeamStats": {"offenseBaselineAdj": 4.5},
        "park": {"parkFactor": 100},
        "startTime": start,
        "status": "Scheduled",
    }


def make_market(ticker, event_ticker, title, yes_bid=40.0, yes_ask=45.0):
    return {
        "market_ticker": ticker, "event_ticker": event_ticker, "title": title,
        "yes_bid": yes_bid, "yes_ask": yes_ask, "status": "active",
    }


class TestRule81BlockedSpreadStillFullyAnalyzed:

    def test_full_game_spread_blocked_but_analyzed_ranked_paper_tracked(self):
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXMLBSPREAD-26JUL301910BOSNYY-BOS1", "KXMLBSPREAD-26JUL301910BOSNYY",
                        "Boston wins by over 0.5 runs?", yes_ask=30.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "winning_margin"
        assert c["analysisStatus"] == "ANALYZED"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["paperTrackingStatus"] == "ELIGIBLE"
        assert c["clvTrackingStatus"] == "ELIGIBLE"
        assert c["settlementSupportStatus"] == "SUPPORTED"
        assert c["realMoneyEligibilityStatus"] == "BLOCKED"
        assert c["realMoneyBlockReasons"] == ["RULE_81"]
        assert c["rank"] is not None

    def test_f5_spread_blocked_with_distinct_reason_from_rule_81(self):
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXMLBF5SPREAD-26JUL301910BOSNYY-BOS1", "KXMLBF5SPREAD-26JUL301910BOSNYY",
                        "Boston wins first 5 innings by over 0.5 runs?", yes_ask=20.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "winning_margin"
        assert c["period"] == "F5"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["realMoneyEligibilityStatus"] == "BLOCKED"
        assert c["realMoneyBlockReasons"] == ["NOT_YET_ACTIVATED_NO_HISTORICAL_PAPER_SAMPLE"]


class TestF3F7SpreadTotalModeling:

    def test_f3_spread_via_title_fallback_gets_independent_fair_probability(self):
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXUNKNOWNF3SPREAD-26JUL301910BOSNYY-BOS1", "KXUNKNOWNF3SPREAD-26JUL301910BOSNYY",
                        "Boston wins first 3 innings by over 0.5 runs?", yes_ask=30.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "winning_margin"
        assert c["period"] == "F3"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["fairProbabilityPct"] is not None

    def test_f7_total_via_title_fallback_gets_independent_fair_probability(self):
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXUNKNOWNF7TOTAL-26JUL301910BOSNYY-4", "KXUNKNOWNF7TOTAL-26JUL301910BOSNYY",
                        "First 7 innings total runs over 3.5?", yes_ask=40.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "inning_total"
        assert c["period"] == "F7"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["fairProbabilityPct"] is not None

    def test_f3_winner_market_now_modeled_but_blocked_pending_activation_review(self):
        """
        Spread/F3-F7-correction mission: a live dispatch of
        scripts/discover_kalshi_series_catalogue.py confirmed F3 is a
        genuine three-way Kalshi series -- the winner market is now
        SUPPORTED and settlement-SUPPORTED (period-scaled projection
        context resolved automatically by resolve_projection_context()),
        but real-money eligibility stays BLOCKED: production has zero
        historical calibration for this newly-modeled market family
        (it is not in scripts/build_market_ledger.py's REQUIRED_MARKETS),
        so it is paper-only pending the same activation review as
        F3/F5/F7 spread.
        """
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXUNKNOWNF3-26JUL301910BOSNYY-BOS", "KXUNKNOWNF3-26JUL301910BOSNYY",
                        "Boston wins first 3 innings?", yes_ask=40.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "inning_result"
        assert c["period"] == "F3"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["settlementSupportStatus"] == "SUPPORTED"
        assert c["realMoneyEligibilityStatus"] == "BLOCKED"
        assert c["realMoneyBlockReasons"] == ["NOT_YET_ACTIVATED_NO_HISTORICAL_PAPER_SAMPLE"]


class TestNoDriftInExistingProbabilities:

    def test_ml_and_f5_ml_unaffected_by_new_status_fields(self):
        slate = {"date": "2026-07-30", "games": [make_game()]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXMLBGAME-26JUL301910BOSNYY-BOS", "KXMLBGAME-26JUL301910BOSNYY",
                        "Boston wins?", yes_ask=55.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["marketFamily"] == "game_result"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["realMoneyEligibilityStatus"] == "NOT_GOVERNED_BY_THIS_ARTIFACT"
        assert c["realMoneyBlockReasons"] == []


class TestLiveGameNeverPaperTracked:

    def test_live_game_spread_analyzed_but_not_paper_eligible(self):
        game = make_game()
        game["status"] = "Live"
        slate = {"date": "2026-07-30", "games": [game]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXMLBSPREAD-26JUL301910BOSNYY-BOS1", "KXMLBSPREAD-26JUL301910BOSNYY",
                        "Boston wins by over 0.5 runs?", yes_ask=30.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        c = contracts[0]
        assert c["analysisStatus"] == "ANALYZED"
        assert c["modelSupportStatus"] == "SUPPORTED"
        assert c["paperTrackingStatus"] == "NOT_ELIGIBLE"

    def test_final_game_spread_not_paper_eligible(self):
        game = make_game()
        game["status"] = "Final"
        slate = {"date": "2026-07-30", "games": [game]}
        search = {"date": "2026-07-30", "markets": [
            make_market("KXMLBSPREAD-26JUL301910BOSNYY-BOS1", "KXMLBSPREAD-26JUL301910BOSNYY",
                        "Boston wins by over 0.5 runs?", yes_ask=30.0),
        ]}
        contracts, summary = discover("2026-07-30", search, slate)
        assert contracts[0]["paperTrackingStatus"] == "NOT_ELIGIBLE"

    def test_unmatched_game_treated_as_not_pregame(self):
        """A contract that could not be matched to any slate game
        (game_status=None) must never default to paper-eligible."""
        fields = compute_status_fields(
            {"marketFamily": "winning_margin", "period": "full_game",
             "classificationStatus": "classified"},
            model_status="SUPPORTED", real_game_id=None, ticker="T-1", game_status=None,
        )
        assert fields["paperTrackingStatus"] == "NOT_ELIGIBLE"


class TestUnsupportedNeverPaperOrRealEligible:

    def test_pitcher_strikeouts_family_not_paper_or_real_eligible(self):
        assert compute_status_fields(
            {"marketFamily": "pitcher_strikeouts", "period": None, "classificationStatus": "classified"},
            model_status="UNSUPPORTED", real_game_id=1, ticker="T-1",
        )["paperTrackingStatus"] == "NOT_ELIGIBLE"

    def test_unclassified_contract_not_analyzed(self):
        fields = compute_status_fields(
            {"marketFamily": None, "period": None, "classificationStatus": "unclassified"},
            model_status="UNSUPPORTED", real_game_id=None, ticker=None,
        )
        assert fields["analysisStatus"] == "NOT_CLASSIFIED"
        assert fields["clvTrackingStatus"] == "NOT_ELIGIBLE"


class TestRanking:

    def test_higher_edge_ranks_first(self):
        contracts = [
            {"rawEdgePct": 2.0}, {"rawEdgePct": 10.0}, {"rawEdgePct": None}, {"rawEdgePct": 5.0},
        ]
        assign_ranks(contracts)
        by_edge = {c.get("rawEdgePct"): c["rank"] for c in contracts if c.get("rawEdgePct") is not None}
        assert by_edge[10.0] == 1
        assert by_edge[5.0] == 2
        assert by_edge[2.0] == 3
        assert contracts[2]["rank"] is None

    def test_blocked_spread_ranks_alongside_everything_else(self):
        """A Rule-81-blocked contract must not be excluded from ranking
        just because it is real-money ineligible."""
        contracts = [
            {"rawEdgePct": 20.0, "realMoneyEligibilityStatus": "NOT_GOVERNED_BY_THIS_ARTIFACT"},
            {"rawEdgePct": 30.0, "realMoneyEligibilityStatus": "BLOCKED"},
        ]
        assign_ranks(contracts)
        assert contracts[1]["rank"] == 1
        assert contracts[0]["rank"] == 2
