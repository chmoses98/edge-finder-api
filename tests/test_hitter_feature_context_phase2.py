#!/usr/bin/env python3
"""
tests/test_hitter_feature_context_phase2.py
==============================================
Integration tests: lib/research/hitter_feature_context.py actually
consumes Phase 2 raw-pitch-archive / battersDiscipline / bat-tracking
data when supplied via source_meta, and degrades to Phase 1 behavior
(never raising, never fabricating) when it isn't.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.hitter_feature_context import (
    build_hitter_feature_context, STATUS_AVAILABLE, STATUS_NOT_COMPUTED,
)


def _hitter(order=1, player_id="p1", bat_side="R"):
    return {
        "order": order, "playerId": player_id, "name": "Test Hitter", "batSide": bat_side,
        "seasonWOBA": 0.330, "seasonPA": 400, "platoonSplits": {"vsLHP": None, "vsRHP": None},
    }


def _game(lineup):
    return {
        "gameId": 1,
        "away": {"team": "Away Team", "abbr": "AWY"},
        "home": {
            "team": "Home Team", "abbr": "HOM",
            "pitcher": {"id": 999, "name": "Starter", "pitchHand": "L"},
            "pitcherSavant": {"xERA": 3.5}, "bullpen": {},
        },
        "awayTeamStats": {"lineupConfirmedOfficial": True, "confirmedLineup": lineup, "teamSeasonWOBA": 0.315},
        "homeTeamStats": {},
        "park": {"name": "Test Park", "dome": False, "parkFactor": 100},
    }


def _raw_pitches(player_id, n_singles=5, n_strikeouts=2):
    pitches = []
    for i in range(n_singles):
        pitches.append({
            "gameDate": "2026-06-01", "batterId": player_id, "batterHand": "R",
            "pitchType": "FF", "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0,
            "balls": 1, "strikes": 1, "plateX": 0.0, "plateZ": 2.5, "szTop": 3.5, "szBot": 1.5,
            "pitchCallType": "in_play", "events": "single",
            "launchSpeed": 97.0, "launchAngle": 12.0, "battedBallType": "line_drive",
        })
    for i in range(n_strikeouts):
        pitches.append({
            "gameDate": "2026-06-02", "batterId": player_id, "batterHand": "R",
            "pitchType": "SL", "pitchName": "Slider", "releaseSpeed": 85.0,
            "balls": 0, "strikes": 2, "plateX": 1.5, "plateZ": 1.8, "szTop": 3.5, "szBot": 1.5,
            "pitchCallType": "swinging_strike", "events": "strikeout",
        })
    return pitches


class TestReceivesAvailableNewFieldsWhenSupplied:
    def test_baseline_talent_becomes_available_with_raw_archive(self):
        g = _game([_hitter()])
        source_meta = {
            "asOfDate": "2026-08-11",
            "rawPitchesByBatter": {"p1": _raw_pitches("p1")},
        }
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        current_season = hitter["baselineTalent"]["horizons"]["currentSeason"]
        assert current_season["status"] == STATUS_AVAILABLE
        assert current_season["stats"]["PA"] == 7
        assert current_season["stats"]["H"] == 5
        assert current_season["stats"]["K"] == 2

    def test_pitch_type_and_velocity_matchup_available(self):
        g = _game([_hitter()])
        source_meta = {"asOfDate": "2026-08-11", "rawPitchesByBatter": {"p1": _raw_pitches("p1")}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["pitchTypeMatchup"]["status"] == STATUS_AVAILABLE
        assert "four_seam" in hitter["pitchTypeMatchup"]["byPitchType"]
        assert "slider" in hitter["pitchTypeMatchup"]["byPitchType"]
        assert hitter["velocityMatchup"]["status"] == STATUS_AVAILABLE
        assert "four_seam" in hitter["velocityMatchup"]["byVelocityBucket"]

    def test_location_and_count_context_available(self):
        g = _game([_hitter()])
        source_meta = {"asOfDate": "2026-08-11", "rawPitchesByBatter": {"p1": _raw_pitches("p1")}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["locationContext"]["status"] == STATUS_AVAILABLE
        assert hitter["countContext"]["status"] == STATUS_AVAILABLE

    def test_battersDiscipline_flows_into_statcast_and_plate_discipline_blocks(self):
        g = _game([_hitter()])
        source_meta = {
            "savantBattersDiscipline": {"p1": {"kPct": 22.0, "bbPct": 9.5, "whiffPct": 28.0,
                                                "hardHitPct": 41.0, "barrelPct": 8.5, "exitVeloAvg": 89.5}},
        }
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["statcastContact"]["fields"]["hardHitPct"]["status"] == STATUS_AVAILABLE
        assert hitter["statcastContact"]["fields"]["hardHitPct"]["value"] == 41.0
        assert hitter["plateDiscipline"]["fields"]["kPct"]["status"] == STATUS_AVAILABLE
        assert hitter["plateDiscipline"]["fields"]["kPct"]["value"] == 22.0

    def test_bat_tracking_available_when_snapshot_supplied(self):
        g = _game([_hitter()])
        source_meta = {
            "batTrackingByBatter": {
                "p1": {"latest": {"asOfDate": "2026-08-01", "avgBatSpeed": 72.4, "swingLength": 7.1},
                       "history": [{"asOfDate": "2026-08-01", "avgBatSpeed": 72.4, "swingLength": 7.1}]},
            },
        }
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["batTracking"]["status"] == STATUS_AVAILABLE
        assert hitter["batTracking"]["fields"]["avgBatSpeed"]["value"] == 72.4
        assert hitter["batTracking"]["fields"]["swingLength"]["status"] == STATUS_AVAILABLE

    def test_recent_change_context_computed_with_enough_history(self):
        g = _game([_hitter()])
        recent = [{"gameDate": "2026-08-05", "batterId": "p1", "batterHand": "R", "pitchType": "FF",
                   "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0, "balls": 0, "strikes": 1,
                   "pitchCallType": "swinging_strike", "events": None}] * 5
        baseline = [{"gameDate": "2026-02-01", "batterId": "p1", "batterHand": "R", "pitchType": "FF",
                     "pitchName": "4-Seam Fastball", "releaseSpeed": 95.0, "balls": 0, "strikes": 0,
                     "pitchCallType": "ball", "events": None}] * 5
        source_meta = {"asOfDate": "2026-08-11", "rawPitchesByBatter": {"p1": recent + baseline}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["recentChangeContext"]["status"] == STATUS_AVAILABLE
        assert "plateDiscipline" in hitter["recentChangeContext"]["comparisons"]


class TestDegradesToPhase1WithoutNewData:
    def test_no_source_meta_keeps_phase1_statuses(self):
        g = _game([_hitter()])
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["pitchTypeMatchup"]["status"] == STATUS_NOT_COMPUTED
        assert hitter["baselineTalent"]["horizons"]["currentSeason"]["status"] == STATUS_AVAILABLE
        assert hitter["baselineTalent"]["horizons"]["rolling30d"]["status"] != STATUS_AVAILABLE

    def test_missing_batter_in_raw_pitches_lookup_does_not_raise(self):
        g = _game([_hitter(player_id="unmapped")])
        source_meta = {"asOfDate": "2026-08-11", "rawPitchesByBatter": {"p1": _raw_pitches("p1")}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["pitchTypeMatchup"]["status"] == STATUS_NOT_COMPUTED

    def test_raw_pitches_without_as_of_date_still_safe(self):
        """Without asOfDate, window-bounded derivation can't run -- must not raise or silently use wrong bounds."""
        g = _game([_hitter()])
        source_meta = {"rawPitchesByBatter": {"p1": _raw_pitches("p1")}}
        ctx = build_hitter_feature_context(g, "away", source_meta=source_meta)
        hitter = ctx["hitters"][0]
        assert hitter["baselineTalent"]["horizons"]["currentSeason"]["status"] in (STATUS_AVAILABLE,)
