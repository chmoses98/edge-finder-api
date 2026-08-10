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


def run_board(markets, games, date_str="2026-07-30"):
    search_doc = make_search_doc(markets, date_str)
    slate_doc = {"games": games}
    contracts, _ = disc.discover(date_str, search_doc, slate_doc)
    return board.build_projection_board(date_str, contracts, games)


class TestStage1FamilyFiltering:

    def test_pitcher_prop_excluded_from_board_even_though_discovered(self):
        markets = [
            kmkt("KXMLBGAME-26JUL302140BOSATH-BOS", "KXMLBGAME-26JUL302140BOSATH", "Boston vs A's Winner?"),
            kmkt("KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6", "KXMLBKS-26JUL302140BOSATH", "Sonny Gray: 6+ Ks?"),
        ]
        games = [make_game(1001, "BOS", "ATH", "2026-07-31T01:40:00Z")]
        rows, summary = run_board(markets, games)
        families = {r["marketFamily"] for r in rows}
        assert "pitcher_strikeouts" not in families
        assert "game_result" in families
        assert summary["totalRows"] == len(rows) == 1


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
