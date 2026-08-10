#!/usr/bin/env python3
"""
tests/test_kalshi_projection_board.py
=========================================
Coverage for lib/kalshi_projection_board.py -- the Stage 1 pre-gate
full-market projection board for game-derived MLB Kalshi markets.

Exercises the coherence guarantees required by the Stage 1 mission:
  - F3/F5/F7 Away+Tie+Home sum to 1
  - total Over probabilities decline monotonically as thresholds rise
  - team-total Over probabilities decline monotonically as thresholds rise
  - complementary YES/NO prices/probabilities behave correctly
  - every archived rung appears on the board (not just best_line())
  - a downstream PASS/PAPER/Rejected automated status never removes a
    market from the board
  - unsupported/missing-data markets are retained with an honest status,
    never a fabricated probability
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.discover_kalshi_mlb_markets as disc  # noqa: E402
import lib.kalshi_projection_board as board  # noqa: E402
from scripts.build_market_ledger import p_team_wins, compute_game_projection_context  # noqa: E402


def make_game(game_id, away, home, start_time, away_stats=None, home_stats=None,
              away_ps=None, home_ps=None, market_ledger=None):
    g = {
        "gameId": game_id,
        "away": {"abbr": away, "pitcherSavant": away_ps or {"xFIP": 3.8, "avgIPperStart": 6.0}},
        "home": {"abbr": home, "pitcherSavant": home_ps or {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "awayTeamStats": away_stats or {"offenseBaselineAdj": 4.6},
        "homeTeamStats": home_stats or {"offenseBaselineAdj": 4.3},
        "startTime": start_time,
        "park": {"parkFactor": 100},
    }
    if market_ledger is not None:
        g["marketLedger"] = market_ledger
    return g


def make_search_doc(markets, date_str="2026-07-30"):
    return {"date": date_str, "markets": markets, "discoveredUnknownSeriesMarkets": []}


def kmkt(ticker, event_ticker, title, yes_bid=0.5, yes_ask=0.51, status="active"):
    return {"market_ticker": ticker, "event_ticker": event_ticker, "title": title,
            "subtitle": "", "status": status, "yes_bid": yes_bid, "yes_ask": yes_ask,
            "close_time": "2026-08-01T00:00:00Z", "volume": 100.0}


def game_with_starters(game_id=824974, away="BOS", home="ATH", opener=False):
    """Same shape as tests/test_discover_kalshi_mlb_markets.py's
    TestPitcherPropEndToEnd._game_with_starters -- a game with a resolvable
    home starter (Sonny Gray) production's identity resolution can match."""
    g = make_game(game_id, away, home, "2026-07-31T01:40:00Z",
                  away_ps={"xFIP": 3.8, "avgIPperStart": 6.0},
                  home_ps={"xFIP": 4.0, "avgIPperStart": 5.4, "kPct": 24.5, "bbPct": 7.8,
                           "openerRole": opener, "ttoSplit": 0.6, "ttoRisk": True})
    g["away"]["pitcher"] = {"name": "Someone Else", "id": "111111", "note": ""}
    g["home"]["pitcher"] = {"name": "Sonny Gray", "id": "543243", "note": ""}
    return g


def ks_market(threshold, yes_bid=0.30, yes_ask=0.32):
    return kmkt(f"KXMLBKS-26JUL302140BOSATH-ATHGRAY54-{threshold}", "KXMLBKS-26JUL302140BOSATH",
                f"Sonny Gray: {threshold}+ strikeouts?", yes_bid=yes_bid, yes_ask=yes_ask)


def outs_market(threshold, yes_bid=0.40, yes_ask=0.42):
    return kmkt(f"KXMLBOUTS-26JUL302140BOSATH-ATHGRAY54-{threshold}", "KXMLBOUTS-26JUL302140BOSATH",
                f"Sonny Gray: {threshold}+ Outs Recorded?", yes_bid=yes_bid, yes_ask=yes_ask)


def run_board(markets, games, date_str="2026-07-30"):
    search_doc = make_search_doc(markets, date_str)
    slate_doc = {"games": games}
    contracts, _ = disc.discover(date_str, search_doc, slate_doc)
    return board.build_projection_board(date_str, contracts, games)


class TestStage1FamilyFiltering:

    def test_hitter_prop_excluded_but_pitcher_prop_now_included(self):
        """
        Stage 2 (docs/PROJECTION_BOARD.md): pitcher_strikeouts/pitcher_outs
        join the board; hitter props remain out of scope for every stage.
        """
        markets = [
            kmkt("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?"),
            kmkt("KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6", "KXMLBKS-26JUL302140BOSATH", "Sonny Gray: 6+ Ks?"),
            kmkt("KXMLBHIT-26JUL302140BOSATH-DEVERS8-1", "KXMLBHIT-26JUL302140BOSATH", "Devers: 1+ hits?"),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        families = {r["marketFamily"] for r in rows}
        assert "hitter_hits" not in families
        assert "game_result" in families
        assert "pitcher_strikeouts" in families
        # No probable-starter match in this fixture's game -> identity
        # unresolved -> MISSING_DATA, never dropped, never fabricated.
        ks_row = next(r for r in rows if r["marketFamily"] == "pitcher_strikeouts")
        assert ks_row["projectionStatus"] == "MISSING_DATA"
        assert ks_row["subjectId"] is None
        assert ks_row["limitationReason"] is not None


class TestComplementarySynthesis:

    def test_game_total_produces_over_and_under_rows_summing_near_100(self):
        markets = [kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "Total over 8?",
                        yes_bid=0.58, yes_ask=0.60)]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        assert len(rows) == 2
        by_side = {r["side"]: r for r in rows}
        assert set(by_side) == {"Over", "Under"}
        over, under = by_side["Over"], by_side["Under"]
        assert over["isComplementaryLeg"] is False
        assert under["isComplementaryLeg"] is True
        assert abs((over["modelFairProbabilityPct"] + under["modelFairProbabilityPct"]) - 100.0) < 1e-6
        # Under's executable price comes from the ticker's own NO side, not a
        # guess: no_ask defaults to (100 - yes_bid) when noAsk isn't supplied.
        assert under["executableMarketPriceCents"] == 100 - 58.0
        assert not summary["coherenceWarnings"]

    def test_first_inning_run_produces_yrfi_and_nrfi(self):
        markets = [kmkt("KXMLBRFI-26JUL302140BOSATH", "KXMLBRFI-26JUL302140BOSATH", "Run in 1st?",
                        yes_bid=0.48, yes_ask=0.50)]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, _ = run_board(markets, games)
        labels = {r["displayLabel"] for r in rows}
        assert labels == {"YRFI", "NRFI"}

    def test_team_total_under_never_governed_by_automated_ledger(self):
        markets = [kmkt("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "KXMLBTEAMTOTAL-26JUL302140BOSATH", "BOS total over 3.5?")]
        ledger = [{"market": "TT_Away_Over", "ticker": "KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4",
                   "status": "Accepted", "confidence": "HIGH", "gatesFired": []}]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z", market_ledger=ledger)]
        rows, _ = run_board(markets, games)
        by_side = {r["side"]: r for r in rows}
        assert by_side["Over"]["automatedRecommendation"]["automatedStatus"] == "Accepted"
        assert by_side["Under"]["automatedRecommendation"]["automatedStatus"] == "NOT_GOVERNED_BY_AUTOMATED_LEDGER"


class TestThreeWayCoherence:

    def _f5_markets(self):
        return [
            kmkt("KXMLBF5-26JUL302140BOSATH-BOS", "KXMLBF5-26JUL302140BOSATH", "BOS wins F5?", yes_bid=0.40, yes_ask=0.42),
            kmkt("KXMLBF5-26JUL302140BOSATH-TIE", "KXMLBF5-26JUL302140BOSATH", "F5 tie?", yes_bid=0.10, yes_ask=0.12),
            kmkt("KXMLBF5-26JUL302140BOSATH-ATH", "KXMLBF5-26JUL302140BOSATH", "ATH wins F5?", yes_bid=0.45, yes_ask=0.47),
        ]

    def test_f5_away_tie_home_sum_to_100(self):
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(self._f5_markets(), games)
        f5_rows = [r for r in rows if r["marketFamily"] == "inning_result" and r["horizon"] == "F5"]
        assert {r["side"] for r in f5_rows} == {"Away", "Tie", "Home"}
        total = sum(r["modelFairProbabilityPct"] for r in f5_rows)
        assert abs(total - 100.0) < 1e-6
        assert not summary["coherenceWarnings"]

    def test_f7_winner_market_also_confirmed_three_way(self):
        markets = [
            kmkt("KXMLBF7-26JUL302140BOSATH-BOS", "KXMLBF7-26JUL302140BOSATH", "First 7 innings winner?", yes_bid=0.40, yes_ask=0.42),
            kmkt("KXMLBF7-26JUL302140BOSATH-TIE", "KXMLBF7-26JUL302140BOSATH", "First 7 innings tie?", yes_bid=0.08, yes_ask=0.10),
            kmkt("KXMLBF7-26JUL302140BOSATH-ATH", "KXMLBF7-26JUL302140BOSATH", "First 7 innings winner?", yes_bid=0.48, yes_ask=0.50),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        f7_rows = [r for r in rows if r["marketFamily"] == "inning_result" and r["horizon"] == "F7"]
        assert len(f7_rows) == 3
        assert all(r["projectionStatus"] == "PROJECTED" for r in f7_rows)
        total = sum(r["modelFairProbabilityPct"] for r in f7_rows)
        assert abs(total - 100.0) < 1e-6
        assert not summary["coherenceWarnings"]


class TestMonotonicAlternateLines:

    def test_game_total_over_probability_declines_as_threshold_rises(self):
        markets = [
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-6", "KXMLBTOTAL-26JUL302140BOSATH", "t6"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-7", "KXMLBTOTAL-26JUL302140BOSATH", "t7"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-9", "KXMLBTOTAL-26JUL302140BOSATH", "t9"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-10", "KXMLBTOTAL-26JUL302140BOSATH", "t10"),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        overs = sorted(
            (r for r in rows if r["marketFamily"] == "game_total" and r["side"] == "Over"),
            key=lambda r: r["threshold"],
        )
        assert len(overs) == 5
        probs = [r["modelFairProbabilityPct"] for r in overs]
        assert probs == sorted(probs, reverse=True)
        assert not summary["coherenceWarnings"]

    def test_every_archived_rung_appears_not_just_best_line(self):
        markets = [
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-6", "KXMLBTOTAL-26JUL302140BOSATH", "t6"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-7", "KXMLBTOTAL-26JUL302140BOSATH", "t7"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-9", "KXMLBTOTAL-26JUL302140BOSATH", "t9"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-10", "KXMLBTOTAL-26JUL302140BOSATH", "t10"),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, _ = run_board(markets, games)
        over_tickers = {r["marketTicker"] for r in rows if r["side"] == "Over"}
        assert over_tickers == {m["market_ticker"] for m in markets}

    def test_team_total_over_probability_declines_as_threshold_rises(self):
        markets = [
            kmkt("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS3", "KXMLBTEAMTOTAL-26JUL302140BOSATH", "BOS over 2.5?"),
            kmkt("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "KXMLBTEAMTOTAL-26JUL302140BOSATH", "BOS over 3.5?"),
            kmkt("KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS5", "KXMLBTEAMTOTAL-26JUL302140BOSATH", "BOS over 4.5?"),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        overs = sorted(
            (r for r in rows if r["marketFamily"] == "team_total" and r["side"] == "Over" and r["subjectId"] == "BOS"),
            key=lambda r: r["threshold"],
        )
        assert len(overs) == 3
        probs = [r["modelFairProbabilityPct"] for r in overs]
        assert probs == sorted(probs, reverse=True)
        assert not summary["coherenceWarnings"]


class TestAutomatedRecommendationAdvisory:

    def test_rejected_or_paper_status_never_removes_row_from_board(self):
        markets = [kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8")]
        ledger = [{"market": "Game_Total", "ticker": "KXMLBTOTAL-26JUL302140BOSATH-8",
                   "status": "Rejected", "confidence": None,
                   "rejectionReason": "Rule 71: Game Total WR 41% — paper only",
                   "gatesFired": ["Rule71"]}]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z", market_ledger=ledger)]
        rows, _ = run_board(markets, games)
        over = next(r for r in rows if r["side"] == "Over")
        assert over["automatedRecommendation"]["automatedStatus"] == "Rejected"
        assert over["automatedRecommendation"]["automatedRejectionReason"].startswith("Rule 71")
        # Row is still on the board despite the Rejected automated status.
        assert over["projectionStatus"] == "PROJECTED"

    def test_alternate_line_not_matching_best_line_reported_honestly(self):
        markets = [
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-7", "KXMLBTOTAL-26JUL302140BOSATH", "t7"),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8"),
        ]
        # Automated ledger only ever evaluated the "8" rung (best_line).
        ledger = [{"market": "Game_Total", "ticker": "KXMLBTOTAL-26JUL302140BOSATH-8",
                   "status": "Accepted", "confidence": "PAPER", "gatesFired": []}]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z", market_ledger=ledger)]
        rows, _ = run_board(markets, games)
        over_7 = next(r for r in rows if r["side"] == "Over" and r["marketTicker"].endswith("-7"))
        over_8 = next(r for r in rows if r["side"] == "Over" and r["marketTicker"].endswith("-8"))
        assert over_8["automatedRecommendation"]["automatedStatus"] == "Accepted"
        assert over_8["automatedRecommendation"]["matchedAtSameThreshold"] is True
        assert over_7["automatedRecommendation"]["automatedStatus"] == "NOT_BEST_LINE_ALTERNATE_RUNG"
        assert over_7["automatedRecommendation"]["matchedAtSameThreshold"] is False


class TestNeverFabricate:

    def test_unsupported_family_row_has_no_probability(self):
        # F3/F7 spreads: an unrecognized-shape/unconfirmed-ticker family
        # this repo cannot yet price -- still a Stage 1 in-scope family
        # (winning_margin), but with no data resolvable for it here.
        markets = [kmkt("KXMLBSPREAD-26JUL302140BOSATH-BOS1", "KXMLBSPREAD-26JUL302140BOSATH", "BOS wins by 1+?")]
        # Non-empty dicts lacking offenseBaselineAdj -- make_game's "or
        # default" fallback only triggers on a falsy ({}) arg, so this
        # genuinely leaves offenseBaselineAdj unset -> MISSING_DATA.
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z",
                            away_stats={"note": "no baseline"}, home_stats={"note": "no baseline"})]
        rows, _ = run_board(markets, games)
        assert len(rows) == 1
        assert rows[0]["projectionStatus"] == "MISSING_DATA"
        assert rows[0]["modelFairProbabilityPct"] is None
        assert rows[0]["limitationReason"] is not None


class TestNoRegressionVsProduction:

    def test_full_game_ml_matches_production_formula(self):
        markets = [kmkt("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "BOS wins?",
                        yes_bid=0.55, yes_ask=0.57)]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, _ = run_board(markets, games)
        row = rows[0]

        # Reuse production's own projection context (compute_game_projection_context)
        # rather than re-deriving the formula here -- this is exactly what the
        # board's underlying adapter does, so this asserts no drift from the
        # real production computation, not from a hand-copied formula.
        ctx = compute_game_projection_context(games[0])
        p_win, p_push = p_team_wins(ctx["awayProjRuns"], ctx["homeProjRuns"])
        expected_pct = round((p_win / (1 - p_push)) * 100, 3)
        assert abs(row["modelFairProbabilityPct"] - expected_pct) < 0.01

    def test_stage1_game_derived_rows_unchanged_when_pitcher_props_also_on_slate(self):
        """
        Stage 2 requirement 8: adding pitcher_strikeouts/pitcher_outs to
        BOARD_FAMILIES must not alter a single Stage-1 game-derived
        probability/threshold/side. Build the SAME game-derived markets
        twice -- alone, and mixed with pitcher-prop tickers -- and assert
        every field of every Stage-1 row is byte-for-byte identical.
        """
        game_derived_markets = [
            kmkt("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "BOS wins?", yes_bid=0.55, yes_ask=0.57),
            kmkt("KXMLBTOTAL-26JUL302140BOSATH-8", "KXMLBTOTAL-26JUL302140BOSATH", "t8"),
            kmkt("KXMLBRFI-26JUL302140BOSATH", "KXMLBRFI-26JUL302140BOSATH", "Run in 1st?"),
        ]
        games = [game_with_starters()]
        rows_alone, _ = run_board(list(game_derived_markets), games)

        mixed_markets = game_derived_markets + [ks_market(6), outs_market(17)]
        rows_mixed, _ = run_board(mixed_markets, games)

        game_derived_families = board.STAGE1_FAMILIES
        stage1_rows_from_mixed = [r for r in rows_mixed if r["marketFamily"] in game_derived_families]

        def key(r):
            return (r["marketTicker"], r["side"])

        by_key_alone = {key(r): r for r in rows_alone}
        by_key_mixed = {key(r): r for r in stage1_rows_from_mixed}
        assert set(by_key_alone) == set(by_key_mixed)
        for k, row_alone in by_key_alone.items():
            assert row_alone == by_key_mixed[k], f"Stage 1 row changed for {k}"


class TestPitcherPropBoardIntegration:

    def test_pitcher_strikeouts_and_outs_resolve_full_identity_and_pricing(self):
        markets = [ks_market(6), outs_market(17)]
        games = [game_with_starters()]
        rows, summary = run_board(markets, games)
        assert len(rows) == 4  # 2 tickers x (Yes + synthesized No)

        ks_yes = next(r for r in rows if r["marketFamily"] == "pitcher_strikeouts" and r["side"] == "Yes")
        assert ks_yes["gameId"] == 824974
        assert ks_yes["marketTicker"] == "KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6"
        assert ks_yes["subjectId"] == "543243"
        assert ks_yes["subjectName"] == "Sonny Gray"
        assert ks_yes["team"] == "ATH"
        assert ks_yes["threshold"] == 6
        assert ks_yes["displayLabel"] == "Sonny Gray (ATH) 6+ Strikeouts"
        assert ks_yes["projectionStatus"] == "PROJECTED"
        assert 0.0 < ks_yes["modelFairProbabilityPct"] < 100.0
        assert ks_yes["modelFairAmericanOdds"] is not None
        assert ks_yes["marketAmericanOdds"] is not None
        assert ks_yes["executableEdgePct"] is not None
        assert ks_yes["automatedRecommendation"]["automatedStatus"] == "NOT_GOVERNED_BY_AUTOMATED_LEDGER"

        outs_yes = next(r for r in rows if r["marketFamily"] == "pitcher_outs" and r["side"] == "Yes")
        assert outs_yes["subjectId"] == "543243"
        assert outs_yes["threshold"] == 17
        assert outs_yes["displayLabel"] == "Sonny Gray (ATH) 17+ Outs"
        assert outs_yes["projectionStatus"] == "PROJECTED"

        assert summary["byMarketFamily"]["pitcher_strikeouts"] == 2
        assert summary["byMarketFamily"]["pitcher_outs"] == 2
        assert not summary["coherenceWarnings"]

    def test_complementary_yes_no_sums_to_100(self):
        markets = [ks_market(6, yes_bid=0.30, yes_ask=0.32)]
        games = [game_with_starters()]
        rows, summary = run_board(markets, games)
        by_side = {r["side"]: r for r in rows}
        assert set(by_side) == {"Yes", "No"}
        assert by_side["No"]["isComplementaryLeg"] is True
        assert by_side["No"]["executableMarketPriceCents"] == 100 - 30.0  # no_ask defaults to 100 - yes_bid
        total = by_side["Yes"]["modelFairProbabilityPct"] + by_side["No"]["modelFairProbabilityPct"]
        assert abs(total - 100.0) < 1e-6
        assert not summary["coherenceWarnings"]

    def test_every_archived_pitcher_rung_appears(self):
        markets = [ks_market(4), ks_market(5), ks_market(6), ks_market(7), ks_market(8)]
        games = [game_with_starters()]
        rows, _ = run_board(markets, games)
        yes_tickers = {r["marketTicker"] for r in rows if r["side"] == "Yes"}
        assert yes_tickers == {m["market_ticker"] for m in markets}

    def test_strikeout_probability_declines_as_threshold_rises(self):
        markets = [ks_market(3), ks_market(5), ks_market(7), ks_market(9)]
        games = [game_with_starters()]
        rows, summary = run_board(markets, games)
        yes_rows = sorted(
            (r for r in rows if r["marketFamily"] == "pitcher_strikeouts" and r["side"] == "Yes"),
            key=lambda r: r["threshold"],
        )
        probs = [r["modelFairProbabilityPct"] for r in yes_rows]
        assert probs == sorted(probs, reverse=True)
        assert not summary["coherenceWarnings"]

    def test_outs_probability_declines_as_threshold_rises(self):
        markets = [outs_market(12), outs_market(15), outs_market(18), outs_market(21)]
        games = [game_with_starters()]
        rows, summary = run_board(markets, games)
        yes_rows = sorted(
            (r for r in rows if r["marketFamily"] == "pitcher_outs" and r["side"] == "Yes"),
            key=lambda r: r["threshold"],
        )
        probs = [r["modelFairProbabilityPct"] for r in yes_rows]
        assert probs == sorted(probs, reverse=True)
        assert not summary["coherenceWarnings"]

    def test_worsening_workload_assumption_moves_k_and_outs_coherently(self):
        """Shared survival-curve proof at the BOARD level (PR #58's joint
        model): an opener flag must push BOTH K and outs fair probability
        down together, never independently -- see
        lib/research/pitcher_workload_projection.py's module docstring."""
        def run(opener):
            markets = [ks_market(6), outs_market(17)]
            games = [game_with_starters(opener=opener)]
            rows, _ = run_board(markets, games)
            k = next(r for r in rows if r["marketFamily"] == "pitcher_strikeouts" and r["side"] == "Yes")
            o = next(r for r in rows if r["marketFamily"] == "pitcher_outs" and r["side"] == "Yes")
            return k["modelFairProbabilityPct"], o["modelFairProbabilityPct"]

        baseline_k, baseline_outs = run(opener=False)
        opener_k, opener_outs = run(opener=True)
        assert opener_k < baseline_k
        assert opener_outs < baseline_outs

    def test_identity_unresolved_never_fabricates_and_stays_visible(self):
        """A ticker whose parsed name doesn't match either team's probable
        starter must never guess an identity or probability -- MISSING_DATA,
        never dropped from the board."""
        markets = [kmkt("KXMLBKS-26JUL302140BOSATH-ATHBULLPENGUY7-4", "KXMLBKS-26JUL302140BOSATH",
                        "Some Reliever: 4+ strikeouts?")]
        games = [game_with_starters()]
        rows, summary = run_board(markets, games)
        yes_row = next(r for r in rows if r["side"] == "Yes")
        assert yes_row["subjectId"] is None
        assert yes_row["subjectName"] is None
        assert yes_row["team"] is None
        assert yes_row["projectionStatus"] == "MISSING_DATA"
        assert yes_row["modelFairProbabilityPct"] is None
        assert yes_row["limitationReason"] is not None
        assert summary["missingData"] >= 1

    def test_rejected_pitcher_ledger_row_never_removes_board_row(self):
        """Requirement 7: PASS/PAPER/Rejected pitcher markets remain
        visible. Production's marketLedger never actually carries a
        pitcher-prop row today (pitcher_strikeouts/outs are not in
        REQUIRED_MARKETS) -- this proves the invariant holds even if one
        existed, not just that it's currently a moot case."""
        markets = [ks_market(6)]
        games = [game_with_starters()]
        games[0]["marketLedger"] = [{
            "market": "PitcherK_Gray_6", "ticker": "KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6",
            "status": "Rejected", "confidence": None, "rejectionReason": "hypothetical future gate",
            "gatesFired": [],
        }]
        rows, _ = run_board(markets, games)
        yes_row = next(r for r in rows if r["side"] == "Yes")
        # No _expected_ledger_market_names() entry exists for pitcher
        # families today, so this ledger row is correctly reported as not
        # governing this rung -- and, critically, the board row is still here.
        assert yes_row["automatedRecommendation"]["automatedStatus"] == "NOT_GOVERNED_BY_AUTOMATED_LEDGER"
        assert yes_row["projectionStatus"] == "PROJECTED"
