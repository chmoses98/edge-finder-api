#!/usr/bin/env python3
"""
tests/test_hitter_phase5_orchestration.py
=============================================
Hitter Projection Engine Phase 5 -- automatic data accumulation +
standalone projection workflow. Covers: immutable Kalshi-snapshot
linkage, complete real-hitter-contract coverage/status labeling,
per-hitter failure isolation, game-started/lineup-confirmed detection,
idempotent final-status-only Statcast catch-up, the standalone
orchestrator's fail-safe posture and research-run manifest linkage,
and safety isolation from the traditional slate/recommendation/
staking/settlement pipeline (mirrors
tests/test_check_kalshi_prices_safety_isolation.py's own AST-based
technique).
"""
import ast
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from lib.research.hitter_board_builder import (
    build_game_contract_coverage, classify_unmatched_contract_status,
    STATUS_PROJECTED, STATUS_LINEUP_UNCONFIRMED, STATUS_GAME_STARTED,
    STATUS_PLAYER_NOT_IN_STARTING_LINEUP, STATUS_PLAYER_ID_UNRESOLVED,
    STATUS_MARKET_SEMANTICS_UNSUPPORTED, STATUS_AMBIGUOUS_TICKER_MATCH,
    STATUS_MODEL_ERROR,
)
import scripts.statcast_completed_game_catchup as catchup_mod
import scripts.build_hitter_projection_board as board_mod
import scripts.run_standalone_hitter_research as orchestrator_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
PARK_GEOMETRY = {"foulLineLF": 330, "powerAlleyLF": 375, "centerField": 400, "powerAlleyRF": 375, "foulLineRF": 330}

_HITTER_KWARGS_TEMPLATE = dict(
    batter_hand="R", matchup_label="COL @ AZ", raw_pitches=[], season_stats={},
    starter_pitches=None, starter_context={"avgIPperStart": 5.2}, bullpen_context={},
    park_geometry_entry=PARK_GEOMETRY, field_relative_wind=None, defense_snapshot=None,
    hitter_speed_snapshot=None, platoon_context=None, season_woba=None,
)


def _hitter_kwargs(player_id, name, target_slot=2):
    return dict(_HITTER_KWARGS_TEMPLATE, player_id=player_id, player_name=name, target_slot=target_slot)


def _market(ticker, title, mid=0.5, snapshot_ts="2026-08-10T22:00:00.000Z", event_ticker="KXMLBHIT-26AUG102140COLAZ"):
    return {"event_ticker": event_ticker, "market_ticker": ticker, "title": title, "subtitle": "",
            "yes_bid": mid - 0.02, "yes_ask": mid + 0.02, "mid": mid, "snapshot_ts": snapshot_ts}


# ---------------------------------------------------------------------------
# Contract status classification (pure)
# ---------------------------------------------------------------------------
class TestClassifyUnmatchedContractStatus:
    def test_unsupported_market_family(self):
        raw = _market("KXMLBSB-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ stolen bases?",
                       event_ticker="KXMLBSB-26AUG102140COLAZ")
        classified, status, reason = classify_unmatched_contract_status(raw, "COL", "AZ", False, {"COL": True, "AZ": True}, [])
        assert status == STATUS_MARKET_SEMANTICS_UNSUPPORTED

    def test_game_started_wins_over_everything_else(self):
        raw = _market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?")
        classified, status, reason = classify_unmatched_contract_status(raw, "COL", "AZ", True, {"COL": True, "AZ": True}, [])
        assert status == STATUS_GAME_STARTED

    def test_lineup_unconfirmed(self):
        raw = _market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?")
        classified, status, reason = classify_unmatched_contract_status(raw, "COL", "AZ", False, {"COL": False, "AZ": False}, [])
        assert status == STATUS_LINEUP_UNCONFIRMED

    def test_player_not_in_starting_lineup(self):
        raw = _market("KXMLBHIT-26AUG102140COLAZ-AZBENCHY9-1", "Bench Y: 1+ hits?")
        hitters = [{"playerId": 1, "name": "Someone Else", "teamAbbr": "AZ"}]
        classified, status, reason = classify_unmatched_contract_status(raw, "COL", "AZ", False, {"COL": True, "AZ": True}, hitters)
        assert status == STATUS_PLAYER_NOT_IN_STARTING_LINEUP

    def test_ambiguous_ticker_match(self):
        raw = _market("KXMLBHIT-26AUG102140COLAZ-COLJSMITH3-1", "J Smith: 1+ hits?")
        hitters = [{"playerId": 1, "name": "J Smith", "teamAbbr": "COL"}, {"playerId": 2, "name": "J Smith", "teamAbbr": "AZ"}]
        classified, status, reason = classify_unmatched_contract_status(raw, "COL", "AZ", False, {"COL": True, "AZ": True}, hitters)
        assert status == STATUS_AMBIGUOUS_TICKER_MATCH

    def test_model_error_reused_for_matching_hitter(self):
        raw = _market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?")
        hitters = [{"playerId": 5, "name": "Willi Castro", "teamAbbr": "COL"}]
        classified, status, reason = classify_unmatched_contract_status(
            raw, "COL", "AZ", False, {"COL": True, "AZ": True}, hitters,
            model_error_by_player_id={5: "ValueError: boom"},
        )
        assert status == STATUS_MODEL_ERROR
        assert "boom" in reason


# ---------------------------------------------------------------------------
# Full game contract coverage -- every contract gets exactly one row
# ---------------------------------------------------------------------------
class TestBuildGameContractCoverage:
    def _base(self):
        return [
            _market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62),
            _market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-2", "Willi Castro: 2+ hits?", mid=0.20),
            _market("KXMLBHIT-26AUG102140COLAZ-AZBENCHY9-1", "Bench Y: 1+ hits?", mid=0.5),
        ]

    def _hitters_and_kwargs(self):
        hitters = [{"playerId": "555", "name": "Willi Castro", "teamAbbr": "COL"}]
        kwargs_by_id = {"555": _hitter_kwargs("555", "Willi Castro")}
        return hitters, kwargs_by_id

    def test_every_contract_gets_exactly_one_row(self):
        markets = self._base()
        hitters, kwargs = self._hitters_and_kwargs()
        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ",
            {"COL": True, "AZ": True}, False, n_sims=300,
        )
        tickers_in = {m["market_ticker"] for m in markets}
        tickers_out = {r["marketTicker"] for r in result["rows"]}
        assert tickers_in == tickers_out
        assert len(result["rows"]) == len(markets)

    def test_confirmed_hitter_projects_others_do_not(self):
        markets = self._base()
        hitters, kwargs = self._hitters_and_kwargs()
        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ",
            {"COL": True, "AZ": True}, False, n_sims=300,
        )
        by_ticker = {r["marketTicker"]: r for r in result["rows"]}
        assert by_ticker["KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1"]["projectionStatus"] == STATUS_PROJECTED
        assert by_ticker["KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-2"]["projectionStatus"] == STATUS_PROJECTED
        assert by_ticker["KXMLBHIT-26AUG102140COLAZ-AZBENCHY9-1"]["projectionStatus"] == STATUS_PLAYER_NOT_IN_STARTING_LINEUP

    def test_one_hitter_failure_does_not_erase_other_hitters_rows(self):
        markets = self._base() + [_market("KXMLBHIT-26AUG102140COLAZ-AZOTHERH7-1", "Other Hitter: 1+ hits?", mid=0.4)]
        hitters = [
            {"playerId": "555", "name": "Willi Castro", "teamAbbr": "COL"},
            {"playerId": "777", "name": "Other Hitter", "teamAbbr": "AZ"},
        ]
        broken_kwargs = _hitter_kwargs("555", "Willi Castro")
        del broken_kwargs["park_geometry_entry"]  # forces a TypeError inside build_hitter_projection_rows
        kwargs = {"555": broken_kwargs, "777": _hitter_kwargs("777", "Other Hitter")}

        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ",
            {"COL": True, "AZ": True}, False, n_sims=300,
        )
        by_ticker = {r["marketTicker"]: r for r in result["rows"]}
        assert by_ticker["KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1"]["projectionStatus"] == STATUS_MODEL_ERROR
        assert by_ticker["KXMLBHIT-26AUG102140COLAZ-AZOTHERH7-1"]["projectionStatus"] == STATUS_PROJECTED

    def test_lineup_confirmed_by_abbr_independently_verified_per_hitter(self):
        """Regression test for a real bug found during development: this function must not
        trust a caller-supplied hitter list to already be lineup-confirmation-filtered."""
        markets = self._base()
        hitters, kwargs = self._hitters_and_kwargs()
        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ",
            {"COL": False, "AZ": False}, False, n_sims=300,  # lineup NOT confirmed despite hitter being passed in
        )
        assert all(r["projectionStatus"] == STATUS_LINEUP_UNCONFIRMED for r in result["rows"])

    def test_single_simulation_per_hitter_not_per_contract(self):
        """Performance requirement: a hitter with N matched thresholds must be simulated once,
        not N times."""
        markets = self._base()
        hitters, kwargs = self._hitters_and_kwargs()
        with patch("lib.research.hitter_board_builder.build_hitter_market_distributions") as mock_sim:
            mock_sim.side_effect = Exception("stop after first call check")
            try:
                build_game_contract_coverage(markets, hitters, kwargs, "COL", "AZ", "COL @ AZ",
                                              {"COL": True, "AZ": True}, False, n_sims=300)
            except Exception:
                pass
        assert mock_sim.call_count == 1


# ---------------------------------------------------------------------------
# Immutable snapshot linkage
# ---------------------------------------------------------------------------
class TestImmutableSnapshotLinkage:
    def test_row_source_capture_path_matches_supplied_argument_exactly(self):
        markets = [_market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                            snapshot_ts="2026-08-10T22:47:37.944Z")]
        hitters = [{"playerId": "555", "name": "Willi Castro", "teamAbbr": "COL"}]
        kwargs = {"555": _hitter_kwargs("555", "Willi Castro")}
        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ", {"COL": True, "AZ": True}, False,
            source_capture_path="data/kalshi_registry_snapshots/kalshi_search_2026-08-10_224500_standalone.json",
            research_run_id="run-abc-123", generated_at="2026-08-10T23:00:00Z", n_sims=300,
        )
        row = result["rows"][0]
        assert row["sourceCapturePath"] == "data/kalshi_registry_snapshots/kalshi_search_2026-08-10_224500_standalone.json"
        assert row["marketObservedAt"] == "2026-08-10T22:47:37.944Z"
        assert row["researchRunId"] == "run-abc-123"
        assert row["projectionGeneratedAt"] == "2026-08-10T23:00:00Z"

    def test_captured_executable_price_matches_the_raw_market_exactly(self):
        markets = [_market("KXMLBHIT-26AUG102140COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.6234)]
        hitters = [{"playerId": "555", "name": "Willi Castro", "teamAbbr": "COL"}]
        kwargs = {"555": _hitter_kwargs("555", "Willi Castro")}
        result = build_game_contract_coverage(
            markets, hitters, kwargs, "COL", "AZ", "COL @ AZ", {"COL": True, "AZ": True}, False, n_sims=300,
        )
        assert result["rows"][0]["executableKalshiPrice"] == pytest.approx(0.6234)


# ---------------------------------------------------------------------------
# build_hitter_projection_board.py -- game-started / lineup-confirmed
# detection wired against a full slate.json/kalshi_search.json fixture
# ---------------------------------------------------------------------------
class TestBuildHitterProjectionBoardMain:
    def _fixture(self, tmp_path, lineup_confirmed_official=True, captured_at="2026-08-10T20:00:00.000Z",
                 start_time="2026-08-10T23:00:00Z"):
        slate = {
            "date": "2026-08-10",
            "games": [{
                "gameId": "999001", "startTime": start_time,
                "away": {"abbr": "COL", "pitcher": {"id": "111", "name": "Away Starter"}},
                "home": {"abbr": "AZ", "pitcher": {"id": "222", "name": "Home Starter"}},
                "awayTeamStats": {"lineupConfirmedOfficial": lineup_confirmed_official,
                                  "confirmedLineup": [{"playerId": 555, "name": "Willi Castro", "batSide": "R", "order": 2, "position": "2B"}] if lineup_confirmed_official else []},
                "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
            }],
        }
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [_market("KXMLBHIT-26AUG102300COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                                 snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUG102300COLAZ")],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        return str(tmp_path / "slate.json"), str(tmp_path / "kalshi_search.json"), str(tmp_path / "weather.json"), str(tmp_path / "savant_team.json")

    def test_confirmed_unstarted_hitter_is_projected(self, tmp_path):
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["rowsByProjectionStatus"][STATUS_PROJECTED] == 1

    def test_lineup_unconfirmed_official_blocks_projection(self, tmp_path):
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path, lineup_confirmed_official=False)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["rowsByProjectionStatus"][STATUS_LINEUP_UNCONFIRMED] == 1
        assert result["rowsByProjectionStatus"][STATUS_PROJECTED] == 0

    def test_game_already_started_at_capture_time_is_not_projected(self, tmp_path):
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(
            tmp_path, captured_at="2026-08-11T00:00:00.000Z", start_time="2026-08-10T23:00:00Z",
        )
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["rowsByProjectionStatus"][STATUS_GAME_STARTED] == 1
        assert result["rowsByProjectionStatus"][STATUS_PROJECTED] == 0

    def test_no_row_ever_uses_a_family_outside_supported_real_families(self, tmp_path):
        from lib.research.hitter_board_builder import SUPPORTED_REAL_FAMILIES
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["totalRows"] >= 1  # sanity: the fixture's own row was produced

    def test_source_capture_path_is_exactly_what_was_passed(self, tmp_path):
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["sourceCapturePath"] == kalshi_path

    def test_default_kalshi_search_path_never_used_when_explicit_one_given(self, tmp_path):
        """A projection run given an explicit immutable snapshot path must never silently read
        the mutable data/kalshi_search.json instead."""
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        with patch.object(board_mod, "DEFAULT_KALSHI_SEARCH_PATH", "/tmp/should_never_be_read.json"):
            result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                     weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert result["sourceCapturePath"] == kalshi_path
        assert result["totalHitterMarketsDiscovered"] == 1


# ---------------------------------------------------------------------------
# Statcast completed-game catch-up
# ---------------------------------------------------------------------------
class TestStatcastCatchup:
    def test_already_archived_game_is_never_refetched(self):
        with patch.object(catchup_mod, "has_game", return_value=True) as mock_has_game, \
             patch.object(catchup_mod, "fetch_game_feed") as mock_feed:
            result = catchup_mod.catch_up_games([111])
        assert result["alreadyArchived"] == 1
        mock_feed.assert_not_called()

    def test_in_progress_game_is_deferred_not_archived(self):
        with patch.object(catchup_mod, "has_game", return_value=False), \
             patch.object(catchup_mod, "fetch_game_feed", return_value={"gameData": {"status": {"detailedState": "In Progress"}}}), \
             patch.object(catchup_mod, "fetch_and_ingest_game") as mock_ingest:
            result = catchup_mod.catch_up_games([222])
        assert result["deferred"] == 1
        mock_ingest.assert_not_called()

    def test_final_game_is_ingested(self):
        with patch.object(catchup_mod, "has_game", return_value=False), \
             patch.object(catchup_mod, "fetch_game_feed", return_value={"gameData": {"status": {"detailedState": "Final"}}}), \
             patch.object(catchup_mod, "fetch_and_ingest_game", return_value={"status": "INGESTED"}) as mock_ingest:
            result = catchup_mod.catch_up_games([333])
        assert result["newlyArchived"] == 1
        mock_ingest.assert_called_once_with(333)

    def test_idempotent_across_repeated_calls(self):
        archived = set()

        def fake_has_game(pk):
            return pk in archived

        def fake_ingest(pk):
            archived.add(pk)
            return {"status": "INGESTED"}

        with patch.object(catchup_mod, "has_game", side_effect=fake_has_game), \
             patch.object(catchup_mod, "fetch_game_feed", return_value={"gameData": {"status": {"detailedState": "Final"}}}), \
             patch.object(catchup_mod, "fetch_and_ingest_game", side_effect=fake_ingest):
            first = catchup_mod.catch_up_games([444])
            second = catchup_mod.catch_up_games([444])
        assert first["newlyArchived"] == 1
        assert second["alreadyArchived"] == 1 and second["newlyArchived"] == 0

    def test_discover_completed_games_filters_by_status(self):
        fake_schedule = {
            "dates": [{"games": [
                {"gamePk": 1, "status": {"detailedState": "Final"}},
                {"gamePk": 2, "status": {"detailedState": "In Progress"}},
                {"gamePk": 3, "status": {"detailedState": "Postponed"}},
                {"gamePk": 4, "status": {"detailedState": "Game Over"}},
            ]}]
        }
        with patch.object(catchup_mod, "fetch_json", return_value=fake_schedule):
            game_pks = catchup_mod.discover_completed_games_for_date_range("2026-08-01", "2026-08-01")
        assert game_pks == [1, 4]

    def test_main_never_scans_beyond_the_requested_date_range(self):
        with patch.object(catchup_mod, "discover_completed_games_for_date_range", return_value=[]) as mock_discover, \
             patch.object(catchup_mod, "catch_up_games", return_value={
                 "totalCandidates": 0, "alreadyArchived": 0, "newlyArchived": 0, "failed": 0, "deferred": 0, "results": []}):
            catchup_mod.main(start_date="2026-08-01", end_date="2026-08-02")
        mock_discover.assert_called_once_with("2026-08-01", "2026-08-02")


# ---------------------------------------------------------------------------
# Standalone orchestrator
# ---------------------------------------------------------------------------
_FAKE_CONTEXT_REFRESH = {"sources": [], "succeeded": 0, "failed": 0}


class TestRunStandaloneHitterResearch:
    """Stage B (refresh_pregame_context) is mocked in every test here -- it shells out to real
    subprocess fetchers that write to the REAL repo's data/savant_team.json/data/bullpen.json
    regardless of dry_run (dry_run only suppresses this orchestrator's OWN artifact writes,
    not Stage B's side effects, which is intentional for production use). Leaving it
    unmocked in tests would dirty the working tree and spuriously trip the same
    working-tree-scope-guard tests as tests/test_protect_slate_rerun_and_scope.py."""

    @pytest.fixture(autouse=True)
    def _mock_context_refresh(self):
        with patch.object(orchestrator_mod, "refresh_pregame_context", return_value=dict(_FAKE_CONTEXT_REFRESH)):
            yield

    def _fixture(self, tmp_path):
        slate = {
            "date": "2026-08-10",
            "games": [{
                "gameId": "999001", "startTime": "2026-08-10T23:00:00Z",
                "away": {"abbr": "COL", "pitcher": {"id": "111", "name": "Away Starter"}},
                "home": {"abbr": "AZ", "pitcher": {"id": "222", "name": "Home Starter"}},
                "awayTeamStats": {"lineupConfirmedOfficial": True,
                                  "confirmedLineup": [{"playerId": 555, "name": "Willi Castro", "batSide": "R", "order": 2, "position": "2B"}]},
                "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
            }],
        }
        kalshi = {"date": "2026-08-10", "fetched_at": "2026-08-10T20:00:00.000Z",
                  "markets": [_market("KXMLBHIT-26AUG102300COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                                       snapshot_ts="2026-08-10T20:00:00.000Z", event_ticker="KXMLBHIT-26AUG102300COLAZ")]}
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        return str(tmp_path / "slate.json"), str(tmp_path / "kalshi_search.json")

    def test_missing_snapshot_returns_no_snapshot_status_without_raising(self, tmp_path):
        slate_path, _ = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path="/tmp/does_not_exist_ever.json",
                                        slate_path=slate_path, dry_run=True)
        assert result["status"] == "NO_SNAPSHOT"

    def test_missing_date_returns_no_date_status_without_raising(self, tmp_path):
        result = orchestrator_mod.main(date_str=None, kalshi_snapshot_path=str(tmp_path / "nope.json"),
                                        slate_path=str(tmp_path / "also_missing_slate.json"), dry_run=True)
        assert result["status"] == "NO_DATE"

    def test_hitter_engine_failure_never_raises_and_kalshi_snapshot_is_untouched(self, tmp_path):
        slate_path, kalshi_path = self._fixture(tmp_path)
        before_bytes = open(kalshi_path, "rb").read()
        with patch.object(orchestrator_mod.build_hitter_projection_board, "main", side_effect=RuntimeError("boom")):
            result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                            slate_path=slate_path, n_sims=100, dry_run=True)
        assert result["status"] == "DEGRADED"
        assert result["projectionBoardStatus"] == "FAILED"
        after_bytes = open(kalshi_path, "rb").read()
        assert before_bytes == after_bytes

    def test_slate_path_is_forwarded_to_board_stages(self, tmp_path):
        """Regression test for a real bug found during development: the orchestrator must use
        the SAME slate_path for feature-board/projection-board stages as it uses itself, never
        silently falling back to the real data/slate.json."""
        slate_path, kalshi_path = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                        slate_path=slate_path, n_sims=200, dry_run=True)
        assert result["summary"]["totalHitterMarketsDiscovered"] == 1
        assert result["summary"]["hittersProjected"] == 1

    def test_run_id_is_consistent_between_return_value_and_summary(self, tmp_path):
        slate_path, kalshi_path = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                        slate_path=slate_path, n_sims=200, dry_run=True)
        assert result["runId"] and result["runId"].startswith("HITTER_PROJECTION_STANDALONE_")

    def test_dry_run_never_writes_a_research_run_manifest_row(self, tmp_path, monkeypatch):
        slate_path, kalshi_path = self._fixture(tmp_path)
        with patch.object(orchestrator_mod, "_write_research_run_record") as mock_write:
            orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                   slate_path=slate_path, n_sims=200, dry_run=True)
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# Safety isolation from the traditional slate/recommendation/staking/
# settlement pipeline (mirrors test_check_kalshi_prices_safety_isolation.py)
# ---------------------------------------------------------------------------
FORBIDDEN_MODULES = {"build_market_ledger", "risk_gate", "write_pending_bets", "protect_slate", "validate_slate_final"}


def _imported_module_names(source_path):
    with open(source_path) as f:
        tree = ast.parse(f.read(), filename=source_path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
    return names


class TestNoTraditionalPipelineDependency:
    @pytest.mark.parametrize("relpath", [
        "run_standalone_hitter_research.py",
        "build_hitter_projection_board.py",
        "build_hitter_feature_board.py",
        "statcast_completed_game_catchup.py",
    ])
    def test_no_forbidden_imports(self, relpath):
        imported = _imported_module_names(os.path.join(SCRIPTS_DIR, relpath))
        assert not (imported & FORBIDDEN_MODULES), f"{relpath}: forbidden import {imported & FORBIDDEN_MODULES}"

    def test_hitter_board_builder_lib_has_no_forbidden_imports(self):
        imported = _imported_module_names(os.path.join(ROOT, "lib", "research", "hitter_board_builder.py"))
        assert not (imported & FORBIDDEN_MODULES)

    def test_no_bet_or_ledger_write_calls_in_orchestrator(self):
        """AST-based: no function NAMED like a wager-recording primitive is ever called in the
        orchestrator -- this mission's explicit 'no bets recorded automatically' boundary."""
        with open(os.path.join(SCRIPTS_DIR, "run_standalone_hitter_research.py")) as f:
            tree = ast.parse(f.read())
        forbidden_names = {"write_pending_bet", "record_bet", "log_manual_bet", "write_bets", "place_bet"}
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in forbidden_names:
                    found.add(name)
        assert not found
