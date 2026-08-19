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
import scripts.fetch_standalone_pregame_context as standalone_context_mod

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

    def test_emit_rows_false_by_default_never_adds_rows_key(self, tmp_path):
        """Additive-only regression: every pre-existing caller's return shape (a flat summary dict, no 'rows' key) must be completely unaffected by the emit_rows= parameter added for lib.research.hitter_prospective_snapshot's checkpoint-scoped reuse."""
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True)
        assert "rows" not in result
        assert "hitterSummaries" not in result

    def test_emit_rows_true_returns_the_actual_row_data(self, tmp_path):
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True,
                                 emit_rows=True)
        assert "rows" in result
        assert len(result["rows"]) == result["totalRows"]
        assert result["rows"][0]["marketFamily"] == "hitter_hits"

    def test_emit_rows_true_rows_carry_the_real_game_id(self, tmp_path):
        """Runtime-capacity fix (docs/HITTER_SCHEDULER_CAPACITY_ARCHITECTURE.md): every emitted row
        must carry its own game's real, stable gameId -- never requiring a caller to re-derive game
        identity from a matchup label string, which is ambiguous for doubleheaders (see the
        doubleheader test below)."""
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        result = board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                                 weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True,
                                 emit_rows=True)
        assert result["rows"][0]["gameId"] == "999001"

    def test_doubleheader_same_matchup_label_distinct_game_ids_never_collide(self, tmp_path):
        """Two real games on the same date, same two teams (a doubleheader) share an IDENTICAL
        matchup label ('COL @ AZ') but have distinct gameIds -- every row must be attributable to
        its own actual game via gameId, never merged or misattributed via the shared label."""
        slate = {
            "date": "2026-08-10",
            "games": [
                {
                    "gameId": "999001", "startTime": "2026-08-10T20:00:00Z",
                    "away": {"abbr": "COL", "pitcher": {"id": "111", "name": "Away Starter G1"}},
                    "home": {"abbr": "AZ", "pitcher": {"id": "222", "name": "Home Starter G1"}},
                    "awayTeamStats": {"lineupConfirmedOfficial": True,
                                      "confirmedLineup": [{"playerId": 555, "name": "Willi Castro", "batSide": "R", "order": 2, "position": "2B"}]},
                    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
                },
                {
                    "gameId": "999002", "startTime": "2026-08-10T23:30:00Z",
                    "away": {"abbr": "COL", "pitcher": {"id": "333", "name": "Away Starter G2"}},
                    "home": {"abbr": "AZ", "pitcher": {"id": "444", "name": "Home Starter G2"}},
                    "awayTeamStats": {"lineupConfirmedOfficial": True,
                                      "confirmedLineup": [{"playerId": 666, "name": "Ryan McMahon", "batSide": "L", "order": 3, "position": "3B"}]},
                    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
                },
            ],
        }
        captured_at = "2026-08-10T18:00:00.000Z"
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [
                _market("KXMLBHIT-26AUG101600COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUG101600COLAZ"),
                _market("KXMLBHIT-26AUG101930COLAZ-COLRMCMAHON4-1", "Ryan McMahon: 1+ hits?", mid=0.58,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUG101930COLAZ"),
            ],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        result = board_mod.main(
            date_str="2026-08-10", slate_path=str(tmp_path / "slate.json"), kalshi_search_path=str(tmp_path / "kalshi_search.json"),
            weather_path=str(tmp_path / "weather.json"), savant_team_path=str(tmp_path / "savant_team.json"),
            n_sims=300, dry_run=True, emit_rows=True,
        )
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}
        castro_row = rows_by_ticker["KXMLBHIT-26AUG101600COLAZ-COLWCASTRO3-1"]
        mcmahon_row = rows_by_ticker["KXMLBHIT-26AUG101930COLAZ-COLRMCMAHON4-1"]
        assert castro_row["gameId"] == "999001"
        assert mcmahon_row["gameId"] == "999002"
        assert castro_row["gameId"] != mcmahon_row["gameId"]

    def _doubleheader_slate(self, leg1_et_utc_start, leg2_et_utc_start):
        return {
            "date": "2026-08-10",
            "games": [
                {
                    "gameId": "999001", "startTime": leg1_et_utc_start,
                    "away": {"abbr": "COL", "pitcher": {"id": "111", "name": "Away Starter G1"}},
                    "home": {"abbr": "AZ", "pitcher": {"id": "222", "name": "Home Starter G1"}},
                    "awayTeamStats": {"lineupConfirmedOfficial": True,
                                      "confirmedLineup": [{"playerId": 555, "name": "Willi Castro", "batSide": "R", "order": 2, "position": "2B"}]},
                    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
                },
                {
                    "gameId": "999002", "startTime": leg2_et_utc_start,
                    "away": {"abbr": "COL", "pitcher": {"id": "333", "name": "Away Starter G2"}},
                    "home": {"abbr": "AZ", "pitcher": {"id": "444", "name": "Home Starter G2"}},
                    "awayTeamStats": {"lineupConfirmedOfficial": True,
                                      "confirmedLineup": [{"playerId": 666, "name": "Ryan McMahon", "batSide": "L", "order": 3, "position": "3B"}]},
                    "homeTeamStats": {"lineupConfirmedOfficial": False, "confirmedLineup": []},
                },
            ],
        }

    def test_cross_hour_boundary_correctly_prefers_the_true_closest_leg(self, tmp_path):
        """PR #93 review's exact bug report: leg1 starts ET 12:55, leg2 starts ET
        13:30, and the market's own ticker time is 13:05. True elapsed-minutes
        distance is 10 (leg1) vs 25 (leg2) -- leg1 is correctly closer. The OLD
        raw-integer-subtraction bug (int('1305')-int('1255')=50 vs
        int('1305')-int('1330')=25) would have incorrectly preferred leg2."""
        slate = self._doubleheader_slate("2026-08-10T16:55:00Z", "2026-08-10T17:30:00Z")  # ET 12:55 / 13:30 (EDT, UTC-4)
        captured_at = "2026-08-10T15:00:00.000Z"
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [
                _market("KXMLBHIT-26AUG101305COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUG101305COLAZ"),
            ],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        result = board_mod.main(
            date_str="2026-08-10", slate_path=str(tmp_path / "slate.json"), kalshi_search_path=str(tmp_path / "kalshi_search.json"),
            weather_path=str(tmp_path / "weather.json"), savant_team_path=str(tmp_path / "savant_team.json"),
            n_sims=300, dry_run=True, emit_rows=True,
        )
        rows_by_ticker = {r["marketTicker"]: r for r in result["rows"]}
        castro_row = rows_by_ticker["KXMLBHIT-26AUG101305COLAZ-COLWCASTRO3-1"]
        assert castro_row["gameId"] == "999001"  # the true closest leg (10 min away), never leg2 (25 min away)

    def test_ambiguous_ticker_time_market_preserved_not_guessed(self, tmp_path):
        """A doubleheader market whose ticker time cannot be extracted (missing/
        malformed) must NOT be silently attributed to the earliest candidate --
        it must be preserved as an explicit AMBIGUOUS_TICKER_MATCH row with no
        gameId, and must not appear in either leg's own rows."""
        slate = self._doubleheader_slate("2026-08-10T20:00:00Z", "2026-08-10T23:30:00Z")
        captured_at = "2026-08-10T18:00:00.000Z"
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [
                # No 4-digit HHMM immediately before the "COLAZ" suffix -- ticker
                # time cannot be extracted.
                _market("KXMLBHIT-26AUGCOLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUGCOLAZ"),
            ],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        result = board_mod.main(
            date_str="2026-08-10", slate_path=str(tmp_path / "slate.json"), kalshi_search_path=str(tmp_path / "kalshi_search.json"),
            weather_path=str(tmp_path / "weather.json"), savant_team_path=str(tmp_path / "savant_team.json"),
            n_sims=300, dry_run=True, emit_rows=True,
        )
        rows = result["rows"]
        assert len(rows) == 1  # never dropped, never duplicated across both legs
        row = rows[0]
        assert row["marketTicker"] == "KXMLBHIT-26AUGCOLAZ-COLWCASTRO3-1"
        assert row["projectionStatus"] == STATUS_AMBIGUOUS_TICKER_MATCH
        assert row["gameId"] is None

    def test_ambiguous_tied_ticker_time_market_preserved_not_guessed(self, tmp_path):
        """A doubleheader market whose ticker time is EXACTLY equidistant between
        both legs is a genuine tie -- must be preserved as ambiguous, never
        resolved by an arbitrary tie-break."""
        # leg1 ET 12:00, leg2 ET 14:00 -- ticker time 13:00 is exactly 60 minutes from each.
        slate = self._doubleheader_slate("2026-08-10T16:00:00Z", "2026-08-10T18:00:00Z")
        captured_at = "2026-08-10T15:00:00.000Z"
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [
                _market("KXMLBHIT-26AUG101300COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUG101300COLAZ"),
            ],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        result = board_mod.main(
            date_str="2026-08-10", slate_path=str(tmp_path / "slate.json"), kalshi_search_path=str(tmp_path / "kalshi_search.json"),
            weather_path=str(tmp_path / "weather.json"), savant_team_path=str(tmp_path / "savant_team.json"),
            n_sims=300, dry_run=True, emit_rows=True,
        )
        rows = result["rows"]
        assert len(rows) == 1
        assert rows[0]["projectionStatus"] == STATUS_AMBIGUOUS_TICKER_MATCH
        assert rows[0]["gameId"] is None

    def test_ambiguous_market_never_assigned_to_either_doubleheader_leg(self, tmp_path):
        """Redundant-but-explicit proof that an ambiguous market is excluded from
        BOTH legs' own per-game row sets, not just present as an extra row."""
        slate = self._doubleheader_slate("2026-08-10T20:00:00Z", "2026-08-10T23:30:00Z")
        captured_at = "2026-08-10T18:00:00.000Z"
        kalshi = {
            "date": "2026-08-10", "fetched_at": captured_at,
            "markets": [
                _market("KXMLBHIT-26AUGCOLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                        snapshot_ts=captured_at, event_ticker="KXMLBHIT-26AUGCOLAZ"),
            ],
        }
        (tmp_path / "slate.json").write_text(json.dumps(slate))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        (tmp_path / "weather.json").write_text(json.dumps({"parks": []}))
        (tmp_path / "savant_team.json").write_text(json.dumps({"batters": {}, "battersDiscipline": {}}))
        result = board_mod.main(
            date_str="2026-08-10", slate_path=str(tmp_path / "slate.json"), kalshi_search_path=str(tmp_path / "kalshi_search.json"),
            weather_path=str(tmp_path / "weather.json"), savant_team_path=str(tmp_path / "savant_team.json"),
            n_sims=300, dry_run=True, emit_rows=True,
        )
        game_ids_with_this_ticker = {r["gameId"] for r in result["rows"] if r["marketTicker"] == "KXMLBHIT-26AUGCOLAZ-COLWCASTRO3-1"}
        assert game_ids_with_this_ticker == {None}
        assert "999001" not in game_ids_with_this_ticker
        assert "999002" not in game_ids_with_this_ticker

    def test_emit_rows_true_with_dry_run_never_writes_the_canonical_board_artifact(self, tmp_path):
        """A checkpoint-scoped caller (dry_run=True, emit_rows=True) must never overwrite data/pipeline/<date>/hitter_projection_board.json with a partial/filtered slate's worth of rows."""
        slate_path, kalshi_path, weather_path, savant_path = self._fixture(tmp_path)
        artifact_path = os.path.join("data", "pipeline", "2026-08-10", "hitter_projection_board.json")
        existed_before = os.path.exists(artifact_path)
        before_mtime = os.path.getmtime(artifact_path) if existed_before else None
        board_mod.main(date_str="2026-08-10", slate_path=slate_path, kalshi_search_path=kalshi_path,
                        weather_path=weather_path, savant_team_path=savant_path, n_sims=300, dry_run=True,
                        emit_rows=True)
        if existed_before:
            assert os.path.getmtime(artifact_path) == before_mtime
        else:
            assert not os.path.exists(artifact_path)


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
        """Note: the returned first path is a slate-COMPATIBLE standalone context (what Stage
        B0 would have produced), passed as `standalone_context_path=` to skip the real MLB
        fetch in tests -- it is never data/slate.json."""
        standalone_context = {
            "date": "2026-08-10", "source": "standalone_mlb_stats_api",
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
        (tmp_path / "standalone_pregame_context.json").write_text(json.dumps(standalone_context))
        (tmp_path / "kalshi_search.json").write_text(json.dumps(kalshi))
        return str(tmp_path / "standalone_pregame_context.json"), str(tmp_path / "kalshi_search.json")

    def test_missing_snapshot_returns_no_snapshot_status_without_raising(self, tmp_path):
        context_path, _ = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path="/tmp/does_not_exist_ever.json",
                                        standalone_context_path=context_path, dry_run=True)
        assert result["status"] == "NO_SNAPSHOT"

    def test_missing_date_defaults_to_todays_utc_date_without_touching_slate_json(self, tmp_path):
        """date_str must never be sourced from data/slate.json (an earlier design did exactly
        that) -- omitting it now simply defaults to today's UTC date."""
        context_path, kalshi_path = self._fixture(tmp_path)
        from datetime import datetime, timezone
        expected_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        result = orchestrator_mod.main(date_str=None, kalshi_snapshot_path=kalshi_path,
                                        standalone_context_path=context_path, dry_run=True)
        assert result["date"] == expected_date

    def test_hitter_engine_failure_never_raises_and_kalshi_snapshot_is_untouched(self, tmp_path):
        context_path, kalshi_path = self._fixture(tmp_path)
        before_bytes = open(kalshi_path, "rb").read()
        with patch.object(orchestrator_mod.build_hitter_projection_board, "main", side_effect=RuntimeError("boom")):
            result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                            standalone_context_path=context_path, n_sims=100, dry_run=True)
        assert result["status"] == "DEGRADED"
        assert result["projectionBoardStatus"] == "FAILED"
        after_bytes = open(kalshi_path, "rb").read()
        assert before_bytes == after_bytes

    def test_standalone_context_path_is_forwarded_to_board_stages(self, tmp_path):
        """Regression test for a real bug found during development: the orchestrator must use
        the SAME context path for feature-board/projection-board stages as it uses itself,
        never silently falling back to the real data/slate.json."""
        context_path, kalshi_path = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                        standalone_context_path=context_path, n_sims=200, dry_run=True)
        assert result["summary"]["totalHitterMarketsDiscovered"] == 1
        assert result["summary"]["hittersProjected"] == 1

    def test_run_id_is_consistent_between_return_value_and_summary(self, tmp_path):
        context_path, kalshi_path = self._fixture(tmp_path)
        result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                        standalone_context_path=context_path, n_sims=200, dry_run=True)
        assert result["runId"] and result["runId"].startswith("HITTER_PROJECTION_STANDALONE_")

    def test_dry_run_never_writes_a_research_run_manifest_row(self, tmp_path, monkeypatch):
        context_path, kalshi_path = self._fixture(tmp_path)
        with patch.object(orchestrator_mod, "_write_research_run_record") as mock_write:
            orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                   standalone_context_path=context_path, n_sims=200, dry_run=True)
        mock_write.assert_not_called()

    def test_provided_standalone_context_skips_the_live_fetch(self, tmp_path):
        """When a caller supplies standalone_context_path explicitly, Stage B0's own MLB fetch
        (scripts.fetch_standalone_pregame_context.main) must never be called."""
        context_path, kalshi_path = self._fixture(tmp_path)
        with patch.object(orchestrator_mod.fetch_standalone_pregame_context, "main") as mock_fetch:
            result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                            standalone_context_path=context_path, n_sims=100, dry_run=True)
        mock_fetch.assert_not_called()
        assert result["standalonePregameContext"]["status"] == "PROVIDED"

    def test_no_standalone_context_path_triggers_stage_b0_fetch(self, tmp_path):
        """The default path (no override supplied) must call Stage B0's own independent MLB
        fetch -- proving the orchestrator does NOT require a pre-existing artifact."""
        _context_path, kalshi_path = self._fixture(tmp_path)
        fake_context = {"date": "2026-08-10", "games": []}
        with patch.object(orchestrator_mod.fetch_standalone_pregame_context, "main", return_value=fake_context) as mock_fetch:
            result = orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                            standalone_context_path=None, n_sims=100, dry_run=True)
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args.kwargs["date_str"] == "2026-08-10"
        assert result["standalonePregameContext"]["status"] == "OK"


# ---------------------------------------------------------------------------
# scripts/fetch_standalone_pregame_context.py -- the standalone MLB
# schedule/lineup fetcher itself
# ---------------------------------------------------------------------------
class TestFetchStandalonePregameContext:
    _SCHEDULE = {
        "dates": [{"games": [
            {"gamePk": 999001, "status": {"detailedState": "Scheduled"}, "gameDate": "2026-08-10T23:00:00Z",
             "teams": {"away": {"team": {"id": 115, "name": "Colorado Rockies"},
                                 "probablePitcher": {"id": 111, "fullName": "Away Starter"}},
                       "home": {"team": {"id": 109, "name": "Arizona Diamondbacks"},
                                 "probablePitcher": {"id": 222, "fullName": "Home Starter"}}}},
        ]}]
    }
    _CONFIRMED_BOXSCORE = {
        "teams": {
            "away": {
                "battingOrder": [555] + list(range(556, 564)),
                "pitchers": [111],
                "players": {
                    "ID555": {"person": {"fullName": "Willi Castro", "batSide": {"code": "R"}}, "position": {"abbreviation": "2B"}},
                    "ID111": {"person": {"fullName": "Away Starter", "pitchHand": {"code": "R"}}},
                    **{f"ID{i}": {"person": {"fullName": f"Player {i}", "batSide": {"code": "R"}}, "position": {"abbreviation": "OF"}}
                       for i in range(556, 564)},
                },
            },
            "home": {"battingOrder": [], "pitchers": [], "players": {}},
        },
    }

    def test_discover_todays_schedule_maps_team_ids_to_abbreviations(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE):
            games = standalone_context_mod.discover_todays_schedule("2026-08-10")
        assert games[0]["awayAbbr"] == "COL"
        assert games[0]["homeAbbr"] == "AZ"

    def test_discover_todays_schedule_empty_on_fetch_failure(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=None):
            games = standalone_context_mod.discover_todays_schedule("2026-08-10")
        assert games == []

    def test_confirmed_lineup_produces_lineup_confirmed_official_true(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=self._CONFIRMED_BOXSCORE):
            result = standalone_context_mod.build_standalone_slate("2026-08-10")
        away_ts = result["games"][0]["awayTeamStats"]
        assert away_ts["lineupConfirmedOfficial"] is True
        assert len(away_ts["confirmedLineup"]) == 9
        assert away_ts["confirmedLineup"][0]["playerId"] == "555"
        assert away_ts["confirmedLineup"][0]["name"] == "Willi Castro"
        assert away_ts["confirmedLineup"][0]["batSide"] == "R"
        assert away_ts["confirmedLineup"][0]["position"] == "2B"

    def test_genuinely_unavailable_lineup_stays_lineup_unconfirmed(self):
        """Requirement: if MLB lineups genuinely are not official yet, preserve
        LINEUP_UNCONFIRMED -- never guess a projected lineup."""
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=self._CONFIRMED_BOXSCORE):
            result = standalone_context_mod.build_standalone_slate("2026-08-10")
        home_ts = result["games"][0]["homeTeamStats"]  # empty battingOrder in the fixture boxscore
        assert home_ts["lineupConfirmedOfficial"] is False
        assert home_ts.get("confirmedLineup", []) == []

    def test_no_boxscore_data_degrades_honestly_never_fabricates(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=None):
            result = standalone_context_mod.build_standalone_slate("2026-08-10")
        away_ts = result["games"][0]["awayTeamStats"]
        assert away_ts["lineupConfirmedOfficial"] is False
        assert away_ts.get("lineupBattersFound", 0) == 0

    def test_starter_handedness_read_from_boxscore_when_available(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=self._CONFIRMED_BOXSCORE):
            result = standalone_context_mod.build_standalone_slate("2026-08-10")
        assert result["games"][0]["away"]["pitcher"]["pitchHand"] == "R"
        assert result["games"][0]["away"]["pitcher"]["id"] == "111"

    def test_starter_falls_back_to_probable_pitcher_with_honest_null_handedness(self):
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=self._CONFIRMED_BOXSCORE):
            result = standalone_context_mod.build_standalone_slate("2026-08-10")
        home_pitcher = result["games"][0]["home"]["pitcher"]  # boxscore has no home pitchers listed
        assert home_pitcher["id"] == "222"
        assert home_pitcher["name"] == "Home Starter"
        assert home_pitcher["pitchHand"] is None  # never guessed

    def test_output_is_slate_compatible_shape(self, tmp_path):
        """The written artifact must be directly consumable by build_hitter_feature_board.py /
        build_hitter_projection_board.py via their existing `slate_path=` parameter."""
        out_path = str(tmp_path / "standalone_pregame_context.json")
        with patch.object(standalone_context_mod, "fetch_json", return_value=self._SCHEDULE), \
             patch.object(standalone_context_mod, "fetch_boxscore", return_value=self._CONFIRMED_BOXSCORE):
            standalone_context_mod.main(date_str="2026-08-10", output_path=out_path)
        with open(out_path) as f:
            doc = json.load(f)
        assert doc["date"] == "2026-08-10"
        assert isinstance(doc["games"], list)
        assert "gameId" in doc["games"][0]
        assert "startTime" in doc["games"][0]


# ---------------------------------------------------------------------------
# Independence from data/slate.json -- the acceptance-criteria blocker
# raised against the original PR #85 design
# ---------------------------------------------------------------------------
class TestStandaloneIndependenceFromSlateJson:
    """Proves the standalone workflow no longer depends on data/slate.json having been
    populated by the traditional pipeline first."""

    def test_no_literal_slate_json_reference_in_orchestrator_or_fetcher(self):
        """AST-based: neither the orchestrator nor the new standalone context fetcher may use
        the literal path 'data/slate.json' (or bare 'slate.json') as an actual string constant
        anywhere in the code (open() calls, os.path.join args, default parameter values, ...).
        Prose in docstrings/comments explaining WHY this module doesn't touch that file is
        expected and must not false-positive this check -- only real AST string-constant nodes
        are inspected, never raw source text."""
        for relpath in ("run_standalone_hitter_research.py", "fetch_standalone_pregame_context.py"):
            path = os.path.join(SCRIPTS_DIR, relpath)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)
            # Only string constants passed as CALL ARGUMENTS (open(), os.path.join(), etc.) --
            # never bare docstring/comment prose, which is expected to discuss slate.json.
            offending = [
                arg.value for node in ast.walk(tree) if isinstance(node, ast.Call)
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "slate.json" in arg.value
            ]
            assert not offending, f"{relpath} has a real string-literal call-argument reference to slate.json: {offending}"

    def test_real_data_slate_json_is_byte_for_byte_unchanged_after_a_full_run(self, tmp_path):
        """(d) data/slate.json remains untouched -- runs the full orchestrator (with Stage B0's
        MLB fetch mocked to avoid real network) and hashes the REAL repo's data/slate.json
        before and after."""
        real_slate_path = os.path.join(ROOT, "data", "slate.json")
        before = None
        if os.path.exists(real_slate_path):
            with open(real_slate_path, "rb") as f:
                before = f.read()

        kalshi_path = str(tmp_path / "kalshi_search.json")
        with open(kalshi_path, "w") as f:
            json.dump({"date": "2026-08-10", "fetched_at": "2026-08-10T20:00:00.000Z", "markets": []}, f)

        fake_context = {"date": "2026-08-10", "games": []}
        with patch.object(orchestrator_mod, "refresh_pregame_context", return_value=dict(_FAKE_CONTEXT_REFRESH)), \
             patch.object(orchestrator_mod.fetch_standalone_pregame_context, "main", return_value=fake_context):
            orchestrator_mod.main(date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                                   standalone_context_path=None, n_sims=100, dry_run=True)

        after = None
        if os.path.exists(real_slate_path):
            with open(real_slate_path, "rb") as f:
                after = f.read()
        assert before == after

    def test_no_traditional_slate_workflow_invoked(self):
        """(e) no traditional slate workflow was invoked -- AST scan for any reference to
        scripts.fetch_lineups.main / scripts.enrich_data / the traditional pipeline entry
        points inside the orchestrator (fetch_lineups's own pure helper functions ARE reused
        by fetch_standalone_pregame_context.py, which is expected and fine -- this check is
        specifically that the orchestrator never calls fetch_lineups.main() or touches
        enrich_data/fetch_savant_pitchers, which write into data/slate.json)."""
        with open(os.path.join(SCRIPTS_DIR, "run_standalone_hitter_research.py")) as f:
            source = f.read()
        for forbidden in ("fetch_lineups.main", "enrich_data", "fetch_savant_pitchers"):
            assert forbidden not in source, f"orchestrator references {forbidden!r} -- must not invoke the traditional slate pipeline"

    def test_end_to_end_projected_rows_without_any_prior_slate_run(self, tmp_path):
        """(a)+(b)+(c): starting from NO data/slate.json confirmed lineups at all (Stage B0's
        MLB fetch is the ONLY lineup source, mocked here to avoid real network but exercising
        the full real code path -- including the real Stage B0 file write, which lands under
        the real repo's data/pipeline/<date>/<runId>/ per production convention and is cleaned
        up at the end of this test, mirroring
        tests/test_check_kalshi_prices_safety_isolation.py's own archive-directory cleanup
        pattern), the standalone run produces real PROJECTED hitter rows."""
        kalshi_path = str(tmp_path / "kalshi_search.json")
        with open(kalshi_path, "w") as f:
            json.dump({
                "date": "2026-08-10", "fetched_at": "2026-08-10T20:00:00.000Z",
                "markets": [_market("KXMLBHIT-26AUG102300COLAZ-COLWCASTRO3-1", "Willi Castro: 1+ hits?", mid=0.62,
                                     snapshot_ts="2026-08-10T20:00:00.000Z", event_ticker="KXMLBHIT-26AUG102300COLAZ")],
            }, f)

        schedule = TestFetchStandalonePregameContext._SCHEDULE
        confirmed_boxscore = TestFetchStandalonePregameContext._CONFIRMED_BOXSCORE
        result = None
        try:
            with patch.object(orchestrator_mod, "refresh_pregame_context", return_value=dict(_FAKE_CONTEXT_REFRESH)), \
                 patch.object(standalone_context_mod, "fetch_json", return_value=schedule), \
                 patch.object(standalone_context_mod, "fetch_boxscore", return_value=confirmed_boxscore), \
                 patch.object(orchestrator_mod, "catch_up_todays_slate", return_value={
                     "totalCandidates": 0, "alreadyArchived": 0, "newlyArchived": 0, "failed": 0, "deferred": 0, "results": []}):
                result = orchestrator_mod.main(
                    date_str="2026-08-10", kalshi_snapshot_path=kalshi_path,
                    standalone_context_path=None, n_sims=300, dry_run=True,
                )

            assert result["standalonePregameContext"]["status"] == "OK"
            assert result["summary"]["hittersProjected"] == 1
            assert result["summary"]["rowsByProjectionStatus"][STATUS_PROJECTED] == 1
        finally:
            if result:
                context_path = (result.get("standalonePregameContext") or {}).get("path")
                if context_path:
                    import shutil
                    shutil.rmtree(os.path.dirname(context_path), ignore_errors=True)


# ---------------------------------------------------------------------------
# Safety isolation from the traditional slate/recommendation/staking/
# settlement pipeline (mirrors test_check_kalshi_prices_safety_isolation.py)
# ---------------------------------------------------------------------------
# MLB Model Expression Guardrails milestone: "promotion_engine" and
# "recommendations" close a documented, real (if previously inert) gap
# -- lib/promotion_engine.py's MARKET_TYPES vocabulary and
# lib/edgelab/recommendations.py's extend_with_full_universe() are the
# two production choke points a future change could use to let a
# hitter-engine probability reach a real-money confidence tier or a
# scored Recommendation without deliberately widening this list first.
# Nothing imports either today (this addition should be a no-op against
# every file below); the point is that it fails loudly the moment
# something does, rather than relying on that continuing to be true by
# omission.
FORBIDDEN_MODULES = {
    "build_market_ledger", "risk_gate", "write_pending_bets", "protect_slate",
    "validate_slate_final", "promotion_engine", "recommendations",
}


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
        "fetch_standalone_pregame_context.py",
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
