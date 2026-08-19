#!/usr/bin/env python3
"""
tests/test_doubleheader_resolver_consistency.py
======================================================
PR #93 review: two independent doubleheader-disambiguation resolvers --
scripts/discover_kalshi_mlb_markets.py's resolve_game_match (generic
Kalshi discovery) and scripts/build_hitter_projection_board.py's
_raw_markets_for_game/_resolve_doubleheader_market (hitter board) --
both now share the SAME canonical elapsed-clock-minutes distance helper
(lib.kalshi_ticker_time.closest_by_hhmm). This proves they agree on
which candidate game is closest given equivalent inputs, rather than
silently drifting into two subtly different implementations again.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.discover_kalshi_mlb_markets as discover_mod
import scripts.build_hitter_projection_board as board_mod


def _candidates(pairs):
    """[(time_str, gameId), ...] -- the shape both resolvers' candidate lists share."""
    return list(pairs)


class TestBothResolversAgreeOnClosestCandidate:
    def test_cross_hour_boundary_example(self):
        """The exact PR #93 review bug report: candidate at ET 12:55 vs candidate
        at ET 13:30, ticker time 13:05 -- both resolvers must pick the 12:55
        candidate (true distance 10 < 25), never the raw-integer-subtraction
        bug's incorrect 13:30 preference."""
        candidates = _candidates([("1255", "leg1"), ("1330", "leg2")])
        ticker_time = "1305"

        hitter_choice = board_mod._resolve_doubleheader_market(
            f"KXMLBHIT-26AUG10{ticker_time}COLAZ-X-1", "COLAZ", candidates,
        )

        slate_index_candidates = [{"gameId": gid, "time_str": t, "game": {"gameId": gid}} for t, gid in candidates]
        discover_choice_id, _ = discover_mod.resolve_game_match(
            {"date": "2026-08-10", "awayTeam": "COL", "homeTeam": "AZ", "scheduledTimeStr": ticker_time},
            {("2026-08-10", "COL", "AZ"): slate_index_candidates},
        )

        assert hitter_choice == "leg1"
        assert discover_choice_id == "leg1"
        assert hitter_choice == discover_choice_id

    def test_exact_match_example(self):
        candidates = _candidates([("1600", "leg1"), ("1930", "leg2")])
        ticker_time = "1600"

        hitter_choice = board_mod._resolve_doubleheader_market(
            f"KXMLBHIT-26AUG10{ticker_time}COLAZ-X-1", "COLAZ", candidates,
        )
        slate_index_candidates = [{"gameId": gid, "time_str": t, "game": {"gameId": gid}} for t, gid in candidates]
        discover_choice_id, _ = discover_mod.resolve_game_match(
            {"date": "2026-08-10", "awayTeam": "COL", "homeTeam": "AZ", "scheduledTimeStr": ticker_time},
            {("2026-08-10", "COL", "AZ"): slate_index_candidates},
        )
        assert hitter_choice == discover_choice_id == "leg1"

    def test_normal_same_hour_example(self):
        candidates = _candidates([("1300", "leg1"), ("1930", "leg2")])
        ticker_time = "1345"

        hitter_choice = board_mod._resolve_doubleheader_market(
            f"KXMLBHIT-26AUG10{ticker_time}COLAZ-X-1", "COLAZ", candidates,
        )
        slate_index_candidates = [{"gameId": gid, "time_str": t, "game": {"gameId": gid}} for t, gid in candidates]
        discover_choice_id, _ = discover_mod.resolve_game_match(
            {"date": "2026-08-10", "awayTeam": "COL", "homeTeam": "AZ", "scheduledTimeStr": ticker_time},
            {("2026-08-10", "COL", "AZ"): slate_index_candidates},
        )
        assert hitter_choice == discover_choice_id == "leg1"
