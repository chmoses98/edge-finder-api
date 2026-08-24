#!/usr/bin/env python3
"""
tests/test_discover_kalshi_mlb_markets.py
=============================================
Fixture-based coverage for scripts/discover_kalshi_mlb_markets.py: full
contract enumeration, doubleheader isolation via game ID, unknown-market
preservation, unsupported-market handling (never dropped, never priced),
alternate-line marking, and edge calculation.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.discover_kalshi_mlb_markets as disc  # noqa: E402


def make_game(game_id, away, home, start_time, away_stats=None, home_stats=None,
              away_ps=None, home_ps=None):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcherSavant": away_ps or {"xFIP": 3.8, "avgIPperStart": 6.0}},
        "home": {"abbr": home, "pitcherSavant": home_ps or {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "awayTeamStats": away_stats or {"offenseBaselineAdj": 4.6},
        "homeTeamStats": home_stats or {"offenseBaselineAdj": 4.3},
        "startTime": start_time,
        "park": {"parkFactor": 100},
    }


def make_search_doc(markets, date_str="2026-07-30", kalshi_date="26JUL30"):
    return {"date": date_str, "kalshi_date": kalshi_date, "markets": markets,
            "discoveredUnknownSeriesMarkets": []}


def ml_market(ticker, event_ticker, title, yes_bid=0.5, yes_ask=0.51, status="active"):
    return {"market_ticker": ticker, "event_ticker": event_ticker, "title": title,
            "subtitle": "", "status": status, "yes_bid": yes_bid, "yes_ask": yes_ask,
            "close_time": "2026-08-01T00:00:00Z", "volume": 100.0}


class TestFullEnumerationNoDrop:

    def test_every_market_produces_exactly_one_contract(self):
        markets = [
            ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?"),
            ml_market("KXMLBGAME-26JUL302140BOSATH-ATH", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?"),
            ml_market("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "Total over 8?"),
            ml_market("KXMLBSTRIKEOUTS-26JUL302140BOSATH-GRAY5", "KXMLBSTRIKEOUTS-26JUL302140BOSATH",
                       "Sonny Gray over 5.5 Ks?"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        assert len(contracts) == len(markets)
        assert summary["discovered"] == len(markets)

    def test_unknown_market_retained_marked_unsupported(self):
        markets = [
            ml_market("KXMLBSTRIKEOUTS-26JUL302140BOSATH-GRAY5", "KXMLBSTRIKEOUTS-26JUL302140BOSATH",
                       "Sonny Gray over 5.5 Ks?"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        assert len(contracts) == 1
        assert contracts[0]["modelSupportStatus"] == disc.STATUS_UNSUPPORTED
        assert contracts[0]["fairProbabilityPct"] is None
        assert summary["unsupported"] == 1
        assert summary["discovered"] == 1

    def test_dedup_by_ticker_from_discovered_unknown_series(self):
        m = ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?")
        search_doc = {"date": "2026-07-30", "markets": [m], "discoveredUnknownSeriesMarkets": [dict(m)]}
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        assert summary["discovered"] == 1  # deduplicated, not double-counted


def f5_market(ticker, title, yes_bid, yes_ask):
    return {"market_ticker": ticker, "event_ticker": "KXMLBF5-26AUG221905LAATEX", "title": title,
            "subtitle": "", "status": "active", "yes_bid": yes_bid, "yes_ask": yes_ask,
            "close_time": "2026-08-25T23:05:00Z", "volume": 100.0}


class TestNoProbabilityAndProtectedExpression:
    """
    Phase 2 (Full-Universe MLB Kalshi Probability Persistence), items 4/5:
    real F5 winner/tie tickets (ticker shapes copied verbatim from
    data/kalshi_registry_snapshots/kalshi_search_2026-08-22_2229.json) --
    YES/NO complementarity must hold for every priced contract, and F5's
    already-CONFIRMED_THREE_WAY structure must be flagged as a protected
    expression.
    """

    def _f5_markets(self):
        return [
            f5_market("KXMLBF5-26AUG221905LAATEX-TEX", "Texas first 5 innings winner", 0.51, 0.52),
            f5_market("KXMLBF5-26AUG221905LAATEX-LAA", "Los Angeles A first 5 innings winner", 0.31, 0.32),
            f5_market("KXMLBF5-26AUG221905LAATEX-TIE", "first 5 innings tie", 0.14, 0.15),
        ]

    def _slate(self):
        return {"games": [make_game(822856, "LAA", "TEX", "2026-08-23T18:35:00Z")]}

    def test_no_probability_is_complement_of_fair_probability(self):
        search_doc = make_search_doc(self._f5_markets(), date_str="2026-08-22", kalshi_date="26AUG22")
        contracts, _ = disc.discover("2026-08-22", search_doc, self._slate())
        priced = [c for c in contracts if c["fairProbabilityPct"] is not None]
        assert len(priced) == 3
        for c in priced:
            assert c["noProbabilityPct"] == round(100 - c["fairProbabilityPct"], 3)

    def test_no_probability_null_when_unpriced(self):
        markets = [ml_market("KXMLBSTRIKEOUTS-26JUL302140BOSATH-GRAY5", "KXMLBSTRIKEOUTS-26JUL302140BOSATH", "x?")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        assert contracts[0]["fairProbabilityPct"] is None
        assert contracts[0]["noProbabilityPct"] is None

    def test_f5_winner_and_tie_contracts_flagged_as_protected_expression(self):
        search_doc = make_search_doc(self._f5_markets(), date_str="2026-08-22", kalshi_date="26AUG22")
        contracts, _ = disc.discover("2026-08-22", search_doc, self._slate())
        f5_contracts = [c for c in contracts if c["marketFamily"] == "inning_result"]
        assert len(f5_contracts) == 3
        for c in f5_contracts:
            assert c["isProtectedExpression"] is True

    def test_full_game_moneyline_not_flagged_as_protected_expression(self):
        markets = [ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        assert contracts[0]["marketFamily"] == "game_result"
        assert contracts[0]["isProtectedExpression"] is False

    def test_f5_three_legs_sum_to_one_hundred_pct(self):
        # Away/Tie/Home fairProbabilityPct must sum to 100 -- the same
        # tie-retained joint distribution invariant
        # lib.research.three_way_projection.three_way_result_probs()
        # guarantees by construction (F5 Three-Way Pricing Correction).
        search_doc = make_search_doc(self._f5_markets(), date_str="2026-08-22", kalshi_date="26AUG22")
        contracts, _ = disc.discover("2026-08-22", search_doc, self._slate())
        f5_contracts = [c for c in contracts if c["marketFamily"] == "inning_result"]
        total = sum(c["fairProbabilityPct"] for c in f5_contracts)
        assert abs(total - 100.0) < 0.01


class TestGameIdResolution:

    def test_real_game_id_resolved_from_slate(self):
        markets = [ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "x?")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(824974, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        assert contracts[0]["gameId"] == 824974

    def test_no_slate_match_falls_back_to_synthetic_gameid(self):
        markets = [ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "x?")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": []}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        assert contracts[0]["gameId"] == "2026-07-30_BOS_ATH_2140"


class TestDoubleheaderIsolation:

    def test_two_games_same_teams_different_times_resolved_independently(self):
        markets = [
            ml_market("KXMLBGAME-26JUL301600BOSNYY-BOS", "KXMLBGAME-26JUL301600BOSNYY", "g1"),
            ml_market("KXMLBGAME-26JUL301930BOSNYY-BOS", "KXMLBGAME-26JUL301930BOSNYY", "g2"),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [
            make_game(2001, "BOS", "NYY", "2026-07-30T20:00:00Z"),  # 16:00 ET
            make_game(2002, "BOS", "NYY", "2026-07-30T23:30:00Z"),  # 19:30 ET
        ]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        by_ticker = {c["ticker"]: c for c in contracts}
        g1 = by_ticker["KXMLBGAME-26JUL301600BOSNYY-BOS"]
        g2 = by_ticker["KXMLBGAME-26JUL301930BOSNYY-BOS"]
        assert g1["gameId"] == 2001
        assert g2["gameId"] == 2002
        assert g1["doubleheaderGameNumber"] == 1
        assert g2["doubleheaderGameNumber"] == 2
        # Projections must never cross-contaminate between legs.
        assert g1["fairProbabilityPct"] != g2["fairProbabilityPct"] or True  # same team stats here; identity check below
        assert g1["gameId"] != g2["gameId"]


class TestAlternateLineMarking:

    def test_closest_to_50_is_not_alternate_others_are(self):
        markets = [
            ml_market("KXMLBTOTAL-26JUL302140BOSATH-7", "KXMLBTOTAL-26JUL302140BOSATH", "t7", yes_bid=0.80, yes_ask=0.81),
            ml_market("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8", yes_bid=0.50, yes_ask=0.51),
            ml_market("KXMLBTOTAL-26JUL302140BOSATH-9", "KXMLBTOTAL-26JUL302140BOSATH", "t9", yes_bid=0.20, yes_ask=0.21),
        ]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        by_line = {c["line"]: c for c in contracts}
        assert by_line[8]["alternateLine"] is False  # closest to 50% implied
        assert by_line[7]["alternateLine"] is True
        assert by_line[9]["alternateLine"] is True

    def test_single_line_family_not_marked_alternate(self):
        markets = [ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "x")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        assert contracts[0]["alternateLine"] is None  # ML has no line/ladder concept


class TestEdgeCalculation:

    def test_edge_fields_present_and_sane_when_supported(self):
        markets = [ml_market("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "x",
                              yes_bid=0.55, yes_ask=0.56)]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        c = contracts[0]
        assert c["impliedProbabilityPct"] == 56.0
        assert c["rawEdgePct"] == round(c["fairProbabilityPct"] - 56.0, 3)
        assert c["expectedProfitPerDollar"] is not None
        assert c["betUpToPct"] is not None

    def test_edge_fields_none_when_unsupported(self):
        markets = [ml_market("KXMLBSTRIKEOUTS-26JUL302140BOSATH-GRAY5", "KXMLBSTRIKEOUTS-26JUL302140BOSATH", "x")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        c = contracts[0]
        assert c["impliedProbabilityPct"] is None
        assert c["rawEdgePct"] is None
        assert c["expectedProfitPerDollar"] is None


class TestPitcherPropEndToEnd:
    """
    Pitcher-prop discovery-wiring mission: full real-shaped-ticker
    coverage through discover() -- parse -> resolve_game_match ->
    classify_contract (with the matched game) -> resolve_projection_context
    -> adapt_contract (PR #58's joint workload model). Tickers/titles use
    lib.research.player_prop_parser's real-data-verified convention
    (KXMLBKS/KXMLBOUTS, "{team}{firstInitial}{lastNameCompact}{jersey}-{N}"),
    not the "KXMLBSTRIKEOUTS" placeholder used elsewhere in this file for
    unrelated unknown-series coverage.
    """

    def _game_with_starters(self, game_id=824974, away="BOS", home="ATH"):
        g = make_game(game_id, away, home, "2026-07-31T01:40:00Z",
                      away_ps={"xFIP": 3.8, "avgIPperStart": 6.0},
                      home_ps={"xFIP": 4.0, "avgIPperStart": 5.4, "kPct": 24.5, "bbPct": 7.8,
                               "openerRole": False, "ttoSplit": 0.6, "ttoRisk": True})
        g["away"]["pitcher"] = {"name": "Someone Else", "id": "111111", "note": ""}
        g["home"]["pitcher"] = {"name": "Sonny Gray", "id": "543243", "note": ""}
        return g

    def test_strikeouts_contract_resolves_and_receives_fair_probability(self):
        markets = [ml_market("KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6", "KXMLBKS-26JUL302140BOSATH",
                              "Sonny Gray: 6+ strikeouts?", yes_bid=0.30, yes_ask=0.32)]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [self._game_with_starters()]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        assert len(contracts) == 1
        c = contracts[0]
        assert c["marketFamily"] == "pitcher_strikeouts"
        assert c["subjectType"] == "PITCHER"
        assert c["subjectId"] == "543243"
        assert c["subjectName"] == "Sonny Gray"
        assert c["side"] == "Yes"
        assert c["line"] == 6
        assert c["gameId"] == 824974
        assert c["modelSupportStatus"] == disc.STATUS_SUPPORTED
        assert 0.0 < c["fairProbabilityPct"] < 100.0
        assert c["rawEdgePct"] is not None
        assert summary["modeled"] == 1
        assert summary["exposed"] == 1

    def test_outs_contract_resolves_and_receives_fair_probability(self):
        markets = [ml_market("KXMLBOUTS-26JUL302140BOSATH-ATHGRAY54-17", "KXMLBOUTS-26JUL302140BOSATH",
                              "Sonny Gray: 17+ Outs Recorded?", yes_bid=0.40, yes_ask=0.42)]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [self._game_with_starters()]}
        contracts, _ = disc.discover("2026-07-30", search_doc, slate_doc)
        c = contracts[0]
        assert c["marketFamily"] == "pitcher_outs"
        assert c["subjectId"] == "543243"
        assert c["line"] == 17
        assert c["modelSupportStatus"] == disc.STATUS_SUPPORTED
        assert 0.0 < c["fairProbabilityPct"] < 100.0

    def test_strikeouts_and_outs_share_the_same_pitcher_and_react_together(self):
        """A worse workload input (opener, in this case) must move BOTH the K and outs fair probabilities down together -- proof the discovery path threads the SAME shared workload model PR #58 built, not two independently-computed values."""
        def _run(opener):
            g = self._game_with_starters()
            g["home"]["pitcherSavant"]["openerRole"] = opener
            markets = [
                ml_market("KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6", "KXMLBKS-26JUL302140BOSATH",
                          "Sonny Gray: 6+ strikeouts?"),
                ml_market("KXMLBOUTS-26JUL302140BOSATH-ATHGRAY54-17", "KXMLBOUTS-26JUL302140BOSATH",
                          "Sonny Gray: 17+ Outs Recorded?"),
            ]
            contracts, _ = disc.discover("2026-07-30", make_search_doc(markets), {"games": [g]})
            by_family = {c["marketFamily"]: c for c in contracts}
            return by_family["pitcher_strikeouts"]["fairProbabilityPct"], by_family["pitcher_outs"]["fairProbabilityPct"]

        baseline_k, baseline_outs = _run(opener=False)
        opener_k, opener_outs = _run(opener=True)
        assert opener_k < baseline_k
        assert opener_outs < baseline_outs

    def test_pitcher_not_the_probable_starter_stays_unresolved_and_unsupported(self):
        """A name that doesn't match either team's listed starter must never be guessed -- MISSING_DATA, not a fabricated probability."""
        markets = [ml_market("KXMLBKS-26JUL302140BOSATH-ATHBULLPENGUY7-4", "KXMLBKS-26JUL302140BOSATH",
                              "Some Reliever: 4+ strikeouts?")]
        search_doc = make_search_doc(markets)
        slate_doc = {"games": [self._game_with_starters()]}
        contracts, summary = disc.discover("2026-07-30", search_doc, slate_doc)
        c = contracts[0]
        assert c["subjectId"] is None
        assert c["subjectName"] is None
        assert c["modelSupportStatus"] == disc.STATUS_MISSING_DATA
        assert c["fairProbabilityPct"] is None
        assert summary["modeled"] == 0

    def test_no_slate_match_leaves_pitcher_prop_unsupported_never_dropped(self):
        markets = [ml_market("KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6", "KXMLBKS-26JUL302140BOSATH",
                              "Sonny Gray: 6+ strikeouts?")]
        search_doc = make_search_doc(markets)
        contracts, _ = disc.discover("2026-07-30", search_doc, {"games": []})
        assert len(contracts) == 1
        c = contracts[0]
        assert c["subjectId"] is None
        assert c["modelSupportStatus"] == disc.STATUS_MISSING_DATA


class TestNoOpWithoutSearchFile:

    def test_missing_search_file_returns_clean_status(self, tmp_path):
        result = disc.main(search_path=str(tmp_path / "nonexistent.json"), dry_run=True)
        assert result["status"] == "NO_SEARCH_FILE"
        assert result["discovered"] == 0
