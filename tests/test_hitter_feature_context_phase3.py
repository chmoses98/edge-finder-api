#!/usr/bin/env python3
"""
tests/test_hitter_feature_context_phase3.py
==============================================
Integration tests: lib/research/hitter_feature_context.py actually
consumes Phase 3 park-geometry / field-relative-wind / spray / defense /
sprint-speed / catcher / umpire data when supplied via source_meta, and
degrades honestly (never raising, never fabricating) when it isn't.
Also verifies the schema still exposes no probability/pricing field
anywhere (this milestone is data foundation only).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.hitter_feature_context import (
    build_hitter_feature_context, STATUS_AVAILABLE, STATUS_MISSING_DATA, STATUS_NOT_COMPUTED,
)


def _hitter(order=1, player_id="p1", bat_side="R", position=None):
    h = {
        "order": order, "playerId": player_id, "name": "Test Hitter", "batSide": bat_side,
        "seasonWOBA": 0.330, "seasonPA": 400, "platoonSplits": {"vsLHP": None, "vsRHP": None},
    }
    if position:
        h["position"] = position
    return h


def _game(away_lineup, home_abbr="NYY", away_abbr="BOS", dome=False, home_catcher_entry=None):
    home_ts_lineup = [home_catcher_entry] if home_catcher_entry else []
    return {
        "gameId": 1,
        "away": {"team": "Away Team", "abbr": away_abbr},
        "home": {
            "team": "Home Team", "abbr": home_abbr,
            "pitcher": {"id": 999, "name": "Starter", "pitchHand": "L"},
            "pitcherSavant": {"xERA": 3.5}, "bullpen": {},
        },
        "awayTeamStats": {"lineupConfirmedOfficial": True, "confirmedLineup": away_lineup, "teamSeasonWOBA": 0.315},
        "homeTeamStats": {"lineupConfirmedOfficial": bool(home_ts_lineup), "confirmedLineup": home_ts_lineup},
        "park": {"name": "Test Park", "dome": dome, "parkFactor": 100},
    }


def _raw_pitches_with_batted_balls(player_id):
    return [{
        "gameDate": "2026-06-01", "batterId": player_id, "batterHand": "R",
        "pitchType": "FF", "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0,
        "balls": 1, "strikes": 1, "pitchCallType": "in_play", "events": "single",
        "hitCoordX": 80, "hitCoordY": 150, "launchSpeed": 97.0, "launchAngle": 15.0,
        "battedBallType": "line_drive",
    }]


class TestParkGeometryWiring:
    def test_geometry_available_for_known_home_team(self):
        g = _game([_hitter()], home_abbr="NYY")
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        park_ctx = ctx["hitters"][0]["parkContext"]
        assert park_ctx["geometry"]["status"] == STATUS_AVAILABLE
        assert park_ctx["geometry"]["venueName"] == "Yankee Stadium"
        assert park_ctx["wallDistances"]["status"] == STATUS_AVAILABLE

    def test_geometry_missing_for_unknown_team(self):
        g = _game([_hitter()], home_abbr="ZZZ")
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        park_ctx = ctx["hitters"][0]["parkContext"]
        assert park_ctx["geometry"]["status"] == STATUS_MISSING_DATA

    def test_empirical_factors_kept_separate_from_geometry(self):
        g = _game([_hitter()], home_abbr="NYY")
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        park_ctx = ctx["hitters"][0]["parkContext"]
        assert "geometry" in park_ctx and "empiricalFactors" in park_ctx
        assert park_ctx["empiricalFactors"] is not park_ctx["geometry"]
        assert park_ctx["empiricalFactors"]["byEvent"]["hrFactor"]["status"] == STATUS_NOT_COMPUTED


class TestFieldRelativeWindWiring:
    def test_available_with_weather_and_known_park(self):
        g = _game([_hitter()], home_abbr="NYY", dome=False)
        weather_lookup = {"Home Team": {"dome": False, "wind": 12, "windDeg": 270}}
        ctx = build_hitter_feature_context(g, "away", weather_by_team=weather_lookup, source_meta={"asOfDate": "2026-08-11"})
        wind = ctx["hitters"][0]["weatherContext"]["windRelativeToParkOrientation"]
        assert wind["status"] == STATUS_AVAILABLE

    def test_dome_disables_outdoor_wind(self):
        g = _game([_hitter()], home_abbr="NYY", dome=True)
        weather_lookup = {"Home Team": {"dome": True}}
        ctx = build_hitter_feature_context(g, "away", weather_by_team=weather_lookup, source_meta={"asOfDate": "2026-08-11"})
        weather_ctx = ctx["hitters"][0]["weatherContext"]
        assert weather_ctx["fieldRelativeWind"]["status"] == "NOT_APPLICABLE"


class TestSprayParkWindMatchup:
    def test_spray_context_includes_park_wind_matchup_when_all_available(self):
        g = _game([_hitter()], home_abbr="NYY", dome=False)
        weather_lookup = {"Home Team": {"dome": False, "wind": 12, "windDeg": 270}}
        source_meta = {"asOfDate": "2026-08-11", "rawPitchesByBatter": {"p1": _raw_pitches_with_batted_balls("p1")}}
        ctx = build_hitter_feature_context(g, "away", weather_by_team=weather_lookup, source_meta=source_meta)
        spray = ctx["hitters"][0]["sprayContext"]
        assert spray["status"] == STATUS_AVAILABLE
        assert "meanSprayAngleDeg" in spray
        assert spray["parkWindContext"]["status"] == STATUS_AVAILABLE
        assert spray["parkWindContext"]["hitterPullSideDirection"] == "toward_lf"  # RHB

    def test_spray_context_not_computed_without_raw_archive(self):
        g = _game([_hitter()])
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        assert ctx["hitters"][0]["sprayContext"]["status"] == STATUS_NOT_COMPUTED


class TestDefenseAttachesToCorrectOpponent:
    def test_defense_snapshot_resolved_by_opposing_team_abbr(self):
        g = _game([_hitter()], home_abbr="NYY", away_abbr="BOS")
        source_meta = {"asOfDate": "2026-08-11", "defenseByTeam": {"NYY": {"teamOAA": 15.0, "asOfDate": "2026-08-01"}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        defense = ctx["hitters"][0]["defenseContext"]
        assert defense["opponentDefense"]["status"] == STATUS_AVAILABLE
        assert defense["opponentDefense"]["teamOAA"] == 15.0

    def test_defense_snapshot_for_wrong_team_not_attached(self):
        g = _game([_hitter()], home_abbr="NYY", away_abbr="BOS")
        source_meta = {"asOfDate": "2026-08-11", "defenseByTeam": {"BOS": {"teamOAA": 15.0}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        # away hitters face the HOME (NYY) defense, not their own team's (BOS)
        assert ctx["hitters"][0]["defenseContext"]["opponentDefense"]["status"] == STATUS_NOT_COMPUTED


class TestSprintSpeedAttachesToCorrectHitter:
    def test_speed_snapshot_resolved_by_playerId(self):
        g = _game([_hitter(player_id="p1"), _hitter(order=2, player_id="p2")])
        source_meta = {"asOfDate": "2026-08-11", "sprintSpeedByBatter": {"p1": {"sprintSpeedFtPerSec": 29.0}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitters = {h["playerIdentity"]["playerId"]: h for h in ctx["hitters"]}
        assert hitters["p1"]["defenseContext"]["hitterSpeed"]["status"] == STATUS_AVAILABLE
        assert hitters["p2"]["defenseContext"]["hitterSpeed"]["status"] == STATUS_NOT_COMPUTED


class TestCatcherContext:
    def test_catcher_identity_attaches_to_correct_game(self):
        catcher_entry = {"order": 2, "playerId": "c1", "name": "Catcher One", "batSide": "R",
                          "position": "C", "seasonWOBA": 0.300, "seasonPA": 300, "platoonSplits": {}}
        g = _game([_hitter()], home_catcher_entry=catcher_entry)
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        catcher_ctx = ctx["hitters"][0]["catcherContext"]
        assert catcher_ctx["status"] in (STATUS_AVAILABLE, "PARTIAL")
        assert catcher_ctx["catcherId"] == "c1"

    def test_missing_catcher_fails_honestly(self):
        g = _game([_hitter()])  # no home lineup / no catcher entry
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        assert ctx["hitters"][0]["catcherContext"]["status"] == STATUS_MISSING_DATA

    def test_framing_snapshot_attaches_to_correct_catcher(self):
        catcher_entry = {"order": 2, "playerId": "c1", "name": "Catcher One", "batSide": "R",
                          "position": "C", "seasonWOBA": 0.300, "seasonPA": 300, "platoonSplits": {}}
        g = _game([_hitter()], home_catcher_entry=catcher_entry)
        source_meta = {"asOfDate": "2026-08-11", "catcherFramingByCatcher": {"c1": {"framingRunsExtra": 6.0}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        catcher_ctx = ctx["hitters"][0]["catcherContext"]
        assert catcher_ctx["status"] == STATUS_AVAILABLE
        assert catcher_ctx["fields"]["framingRunsExtra"]["value"] == 6.0


class TestUmpireContext:
    def test_umpire_identity_attaches_only_when_known(self):
        g = _game([_hitter()])
        source_meta = {"asOfDate": "2026-08-11",
                        "umpireByGame": {1: {"status": "AVAILABLE", "umpireId": "u1", "umpireName": "Ump One", "capturedAt": "t"}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        umpire_ctx = ctx["hitters"][0]["umpireContext"]
        assert umpire_ctx["status"] == STATUS_AVAILABLE
        assert umpire_ctx["umpireId"] == "u1"

    def test_missing_pregame_umpire_fails_honestly(self):
        g = _game([_hitter()])
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        umpire_ctx = ctx["hitters"][0]["umpireContext"]
        assert umpire_ctx["status"] == STATUS_MISSING_DATA
        assert umpire_ctx["historicalClassification"] == "PROSPECTIVE_SNAPSHOT_REQUIRED"

    def test_umpire_status_not_available_treated_as_missing(self):
        g = _game([_hitter()])
        source_meta = {"asOfDate": "2026-08-11", "umpireByGame": {1: {"status": "MISSING_DATA"}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        assert ctx["hitters"][0]["umpireContext"]["status"] == STATUS_MISSING_DATA

    def test_wrong_game_umpire_not_attached(self):
        g = _game([_hitter()])
        source_meta = {"asOfDate": "2026-08-11", "umpireByGame": {999: {"status": "AVAILABLE", "umpireId": "u1"}}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        assert ctx["hitters"][0]["umpireContext"]["status"] == STATUS_MISSING_DATA


class TestPR78MissingDataSemanticsIntact:
    def test_every_block_carries_a_status(self):
        g = _game([_hitter()])
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        hitter = ctx["hitters"][0]
        required_blocks = [
            "playerIdentity", "lineupContext", "paContext", "baselineTalent", "platoonContext",
            "statcastContact", "plateDiscipline", "batTracking", "starterContext", "pitchTypeMatchup",
            "velocityMatchup", "pitchShapeContext", "locationContext", "countContext", "bullpenContext",
            "parkContext", "weatherContext", "sprayContext", "defenseContext", "catcherContext",
            "umpireContext", "recentChangeContext",
        ]
        for block_name in required_blocks:
            assert "status" in hitter[block_name], f"{block_name} missing status"
        assert set(required_blocks).issubset(hitter["dataAvailability"].keys())

    def test_no_fields_fabricated_never_a_bare_zero_for_missing(self):
        g = _game([_hitter()])
        ctx = build_hitter_feature_context(g, "away", source_meta={"asOfDate": "2026-08-11"})
        umpire_ctx = ctx["hitters"][0]["umpireContext"]
        assert umpire_ctx.get("umpireId") in (None,)


class TestNoHitterProbabilitiesGenerated:
    """This milestone is data-foundation only -- no probability/pricing field should exist anywhere in the schema."""
    def test_no_probability_or_price_keys_anywhere_in_the_record(self):
        catcher_entry = {"order": 2, "playerId": "c1", "name": "Catcher One", "batSide": "R",
                          "position": "C", "seasonWOBA": 0.300, "seasonPA": 300, "platoonSplits": {}}
        g = _game([_hitter()], home_catcher_entry=catcher_entry)
        source_meta = {
            "asOfDate": "2026-08-11",
            "rawPitchesByBatter": {"p1": _raw_pitches_with_batted_balls("p1")},
            "defenseByTeam": {"NYY": {"teamOAA": 10.0}},
            "sprintSpeedByBatter": {"p1": {"sprintSpeedFtPerSec": 28.0}},
            "catcherFramingByCatcher": {"c1": {"framingRunsExtra": 3.0}},
            "umpireByGame": {1: {"status": "AVAILABLE", "umpireId": "u1", "umpireName": "U", "capturedAt": "t"}},
        }
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        forbidden_substrings = ("probability", "fairPrice", "americanOdds", "edge", "kellyStake", "recommendation")

        def _walk(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    lower = k.lower()
                    for bad in forbidden_substrings:
                        assert bad.lower() not in lower, f"found forbidden key {k!r} at {path}"
                    _walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    _walk(v, f"{path}[{i}]")

        _walk(ctx)
