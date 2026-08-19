#!/usr/bin/env python3
"""
tests/test_hitter_scheduler_board_doubleheader_integration.py
====================================================================
End-to-end regression coverage for the filtered-slate doubleheader-
identity bug (PR #93 final targeted fix): the prospective scheduler
writes a FILTERED slate containing only this cycle's DUE games (cost
containment -- see lib/research/hitter_prospective_snapshot.py's own
docstring), but scripts/build_hitter_projection_board.py's doubleheader
disambiguation previously built its away/home-pair candidate lookup from
that SAME filtered `games` list. When only one leg of a real doubleheader
was due, the lookup saw exactly one candidate for that (away, home) pair
and took the single-game fast path -- silently attributing the OTHER
(not-due) leg's own Kalshi hitter markets to the due leg's gameId.

This test uses the REAL lib.research.hitter_prospective_snapshot.write_filtered_hitter_slate
(never a fake) and the REAL scripts.build_hitter_projection_board.main
(never mocked) -- this is specifically an integration test of the
CONTRACT between those two modules, which a fake on either side would not
actually exercise.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.hitter_prospective_snapshot import write_filtered_hitter_slate
import scripts.build_hitter_projection_board as board_mod

GAME_1 = {
    "gameId": "999001", "startTime": "2026-08-10T20:00:00Z",  # ET 16:00 (EDT, UTC-4)
    "away": {"abbr": "COL", "pitcher": {"id": "111", "name": "Away Starter G1"}},
    "home": {"abbr": "AZ", "pitcher": {"id": "222", "name": "Home Starter G1"}},
    "awayTeamStats": {"lineupConfirmedOfficial": True,
                      "confirmedLineup": [{"playerId": 555, "name": "Willi Castro", "batSide": "R", "order": 2, "position": "2B"}]},
    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
}
GAME_2 = {
    "gameId": "999002", "startTime": "2026-08-10T23:30:00Z",  # ET 19:30 (EDT, UTC-4)
    "away": {"abbr": "COL", "pitcher": {"id": "333", "name": "Away Starter G2"}},
    "home": {"abbr": "AZ", "pitcher": {"id": "444", "name": "Home Starter G2"}},
    "awayTeamStats": {"lineupConfirmedOfficial": True,
                      "confirmedLineup": [{"playerId": 666, "name": "Ryan McMahon", "batSide": "L", "order": 3, "position": "3B"}]},
    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
}
CASTRO_TICKER = "KXMLBHIT-26AUG101600COLAZ-COLWCASTRO3-1"
MCMAHON_TICKER = "KXMLBHIT-26AUG101930COLAZ-COLRMCMAHON4-1"


def _kalshi_snapshot(captured_at):
    """Both doubleheader legs' hitter markets present in the SAME immutable
    Kalshi snapshot -- exactly the real-world condition (one archived capture
    covers the whole day's slate, regardless of which leg is due this cycle)."""
    def _m(ticker, event_ticker, title, mid):
        return {"event_ticker": event_ticker, "market_ticker": ticker, "title": title, "subtitle": "",
                "yes_bid": mid - 0.02, "yes_ask": mid + 0.02, "mid": mid, "snapshot_ts": captured_at}
    return {
        "date": "2026-08-10", "fetched_at": captured_at,
        "markets": [
            _m(CASTRO_TICKER, "KXMLBHIT-26AUG101600COLAZ", "Willi Castro: 1+ hits?", 0.62),
            _m(MCMAHON_TICKER, "KXMLBHIT-26AUG101930COLAZ", "Ryan McMahon: 1+ hits?", 0.58),
        ],
    }


def _run_filtered_cycle(tmp_path, due_games, market_resolution_games, run_id):
    """Writes a REAL filtered slate (via the production write_filtered_hitter_slate,
    exactly as lib.research.hitter_prospective_snapshot's cycle does) and runs the
    REAL board builder against it. Returns board_mod.main()'s result dict."""
    captured_at = "2026-08-10T18:00:00.000Z"
    kalshi_path = str(tmp_path / f"kalshi_{run_id}.json")
    with open(kalshi_path, "w") as f:
        json.dump(_kalshi_snapshot(captured_at), f)
    weather_path = str(tmp_path / "weather.json")
    with open(weather_path, "w") as f:
        json.dump({"parks": []}, f)
    savant_path = str(tmp_path / "savant_team.json")
    with open(savant_path, "w") as f:
        json.dump({"batters": {}, "battersDiscipline": {}}, f)

    slate_path = write_filtered_hitter_slate(
        "2026-08-10", run_id, "T_MINUS_90", due_games,
        market_resolution_games=market_resolution_games, output_root=str(tmp_path / "pipeline"),
    )
    return board_mod.main(
        date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
        weather_path=weather_path, savant_team_path=savant_path,
        n_sims=300, dry_run=True, emit_rows=True,
    )


class TestFilteredSlateDoubleheaderIdentity:
    def test_only_leg_one_due_never_misattributes_leg_twos_market(self, tmp_path):
        result = _run_filtered_cycle(tmp_path, [GAME_1], [GAME_1, GAME_2], "RUN_LEG1")
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}

        # Only Game 1 was in compute scope -- Monte Carlo ran for exactly one game.
        assert set(rows_by_ticker) == {CASTRO_TICKER}
        assert rows_by_ticker[CASTRO_TICKER]["gameId"] == "999001"
        # The core bug this test guards: Game 2's own market must NEVER be
        # silently priced using Game 1's context/gameId just because Game 2
        # wasn't in the filtered slate's compute set.
        assert MCMAHON_TICKER not in rows_by_ticker

    def test_only_leg_two_due_never_misattributes_leg_ones_market(self, tmp_path):
        result = _run_filtered_cycle(tmp_path, [GAME_2], [GAME_1, GAME_2], "RUN_LEG2")
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}

        assert set(rows_by_ticker) == {MCMAHON_TICKER}
        assert rows_by_ticker[MCMAHON_TICKER]["gameId"] == "999002"
        assert CASTRO_TICKER not in rows_by_ticker

    def test_both_legs_due_same_cycle_each_gets_only_its_own_market(self, tmp_path):
        result = _run_filtered_cycle(tmp_path, [GAME_1, GAME_2], [GAME_1, GAME_2], "RUN_BOTH")
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}

        assert set(rows_by_ticker) == {CASTRO_TICKER, MCMAHON_TICKER}
        assert rows_by_ticker[CASTRO_TICKER]["gameId"] == "999001"
        assert rows_by_ticker[MCMAHON_TICKER]["gameId"] == "999002"

    def test_leg_two_projected_normally_once_its_own_checkpoint_becomes_due(self, tmp_path):
        """Sequential-cycles proof: leg 1 due-only in cycle 1 never poisons leg 2's
        own later, independent cycle when leg 2 itself finally becomes due."""
        cycle1 = _run_filtered_cycle(tmp_path, [GAME_1], [GAME_1, GAME_2], "RUN_SEQ_1")
        cycle1_rows = {r["marketTicker"]: r for r in cycle1["rows"]}
        assert cycle1_rows[CASTRO_TICKER]["gameId"] == "999001"
        assert MCMAHON_TICKER not in cycle1_rows

        cycle2 = _run_filtered_cycle(tmp_path, [GAME_2], [GAME_1, GAME_2], "RUN_SEQ_2")
        cycle2_rows = {r["marketTicker"]: r for r in cycle2["rows"]}
        assert cycle2_rows[MCMAHON_TICKER]["gameId"] == "999002"
        assert cycle2_rows[MCMAHON_TICKER]["gameId"] != cycle1_rows[CASTRO_TICKER]["gameId"]

    def test_market_resolution_games_never_expands_compute_scope(self, tmp_path):
        """The full 2-game identity context must never cause BOTH games to be
        simulated when only one is actually due -- identity resolution and
        Monte Carlo compute scope are deliberately separate concepts."""
        result = _run_filtered_cycle(tmp_path, [GAME_1], [GAME_1, GAME_2], "RUN_SCOPE")
        assert result["totalGames"] == 1  # only the due game was in the filtered slate's own compute list
        assert result["totalHitters"] == 1  # only Game 1's one confirmed hitter (Castro) was ever evaluated

    def test_fallback_to_ordinary_games_when_no_market_resolution_games_supplied(self, tmp_path):
        """A normal/manual caller (no marketResolutionGames field at all -- e.g. a
        direct research run against the canonical daily slate.json) must still
        resolve doubleheader identity correctly when BOTH legs are simply present
        in `games` itself -- the pre-existing behavior, unaffected by this fix."""
        slate_path = str(tmp_path / "manual_slate.json")
        with open(slate_path, "w") as f:
            json.dump({"date": "2026-08-10", "games": [GAME_1, GAME_2]}, f)
        captured_at = "2026-08-10T18:00:00.000Z"
        kalshi_path = str(tmp_path / "kalshi_manual.json")
        with open(kalshi_path, "w") as f:
            json.dump(_kalshi_snapshot(captured_at), f)
        weather_path = str(tmp_path / "weather2.json")
        with open(weather_path, "w") as f:
            json.dump({"parks": []}, f)
        savant_path = str(tmp_path / "savant2.json")
        with open(savant_path, "w") as f:
            json.dump({"batters": {}, "battersDiscipline": {}}, f)

        result = board_mod.main(
            date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
            weather_path=weather_path, savant_team_path=savant_path,
            n_sims=300, dry_run=True, emit_rows=True,
        )
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}
        assert rows_by_ticker[CASTRO_TICKER]["gameId"] == "999001"
        assert rows_by_ticker[MCMAHON_TICKER]["gameId"] == "999002"
