#!/usr/bin/env python3
"""
tests/test_hitter_feature_context.py
=======================================
Unit tests for lib/research/hitter_feature_context.py -- the Hitter
Projection Engine Phase 1 canonical feature foundation.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.hitter_feature_context import (
    build_hitter_feature_context,
    STATUS_AVAILABLE,
    STATUS_PARTIAL,
    STATUS_NOT_COMPUTED,
    STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
)
from lib.research.platoon_context import (
    STATUS_OK,
    STATUS_LINEUP_UNCONFIRMED,
    STATUS_MISSING_DATA,
)


def _hitter(order, bat_side="R", player_id="p1", name="Test Hitter", season_woba=0.320,
            vs_lhp=None, vs_rhp=None):
    return {
        "order": order,
        "playerId": player_id,
        "name": name,
        "batSide": bat_side,
        "seasonWOBA": season_woba,
        "seasonPA": 400,
        "platoonSplits": {"vsLHP": vs_lhp, "vsRHP": vs_rhp},
    }


def _confirmed_game(confirmed_lineup, opp_pitch_hand="L", opp_savant=None, opp_bullpen=None, park=None):
    return {
        "gameId": 12345,
        "away": {"team": "Away Team", "abbr": "AWY"},
        "home": {"team": "Home Team", "abbr": "HOM"},
        "awayTeamStats": {
            "lineupConfirmedOfficial": True,
            "confirmedLineup": confirmed_lineup,
            "teamSeasonWOBA": 0.315,
        },
        "homeTeamStats": {},
        "home": {
            "team": "Home Team",
            "abbr": "HOM",
            "pitcher": {"id": 999, "name": "Opposing Starter", "pitchHand": opp_pitch_hand},
            "pitcherSavant": opp_savant or {},
            "bullpen": opp_bullpen or {},
        },
        "park": park if park is not None else {"name": "Test Park", "dome": False, "parkFactor": 100},
    }


def _full_lineup():
    return [_hitter(i + 1, "R", player_id=f"p{i+1}", name=f"Hitter {i+1}") for i in range(9)]


class TestConfirmedHitterReceivesFeatureRecord:
    def test_confirmed_lineup_produces_one_record_per_hitter(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["status"] == STATUS_OK
        assert ctx["lineupConfirmed"] is True
        assert len(ctx["hitters"]) == 9
        first = ctx["hitters"][0]
        assert first["playerIdentity"]["playerId"] == "p1"
        assert first["lineupContext"]["order"] == 1
        assert first["lineupContext"]["topOrderWeighted"] is True

    def test_record_exposes_all_required_domain_blocks(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        required_blocks = {
            "playerIdentity", "lineupContext", "paContext", "baselineTalent",
            "platoonContext", "statcastContact", "plateDiscipline", "batTracking",
            "starterContext", "pitchTypeMatchup", "velocityMatchup", "pitchShapeContext",
            "locationContext", "countContext", "bullpenContext", "parkContext",
            "weatherContext", "sprayContext", "defenseContext", "catcherContext",
            "umpireContext", "recentChangeContext", "dataAvailability", "dataFreshness",
            "sampleSizes", "fallbacksUsed", "uncertaintyFlags",
        }
        assert required_blocks.issubset(hitter.keys())


class TestUnconfirmedLineupDoesNotFabricate:
    def test_unconfirmed_lineup_returns_no_hitters(self):
        g = _confirmed_game([])
        g["awayTeamStats"]["lineupConfirmedOfficial"] = False
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED
        assert ctx["hitters"] == []

    def test_confirmed_flag_true_but_empty_lineup_still_no_fabrication(self):
        g = _confirmed_game([])
        g["awayTeamStats"]["lineupConfirmedOfficial"] = True
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED
        assert ctx["hitters"] == []

    def test_missing_team_stats_entirely(self):
        g = {"gameId": 1, "away": {}, "home": {}}
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED
        assert ctx["hitters"] == []


class TestHandednessFlowsFromPR77:
    def test_batside_and_effective_side_resolved_from_confirmed_lineup(self):
        lineup = _full_lineup()
        lineup[0]["batSide"] = "S"
        g = _confirmed_game(lineup, opp_pitch_hand="L")
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["playerIdentity"]["batSide"] == "S"
        assert hitter["platoonContext"]["opposingStarterHand"] == "L"
        # switch hitter vs LHP resolves to batting right-handed
        assert hitter["platoonContext"]["effectiveBatSide"] == "R"

    def test_platoon_context_uses_real_split_when_sample_adequate(self):
        lineup = [_hitter(1, "R", vs_lhp={"woba": 0.410, "pa": 50})]
        g = _confirmed_game(lineup, opp_pitch_hand="L")
        # relax the 6-batter platoon floor irrelevant here -- this tests hitter_platoon_value directly
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["platoonContext"]["platoonWOBA"] == 0.410
        assert hitter["platoonContext"]["usedSeasonFallback"] is False
        assert hitter["platoonContext"]["status"] == STATUS_AVAILABLE

    def test_platoon_context_shrinks_below_pa_floor(self):
        lineup = [_hitter(1, "R", season_woba=0.300, vs_lhp={"woba": 0.410, "pa": 5})]
        g = _confirmed_game(lineup, opp_pitch_hand="L")
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["platoonContext"]["platoonWOBA"] == 0.300
        assert hitter["platoonContext"]["usedSeasonFallback"] is True
        assert "platoonContext: shrunk to season wOBA" in hitter["fallbacksUsed"][0]

    def test_missing_starter_hand_yields_missing_platoon_data(self):
        lineup = _full_lineup()
        g = _confirmed_game(lineup, opp_pitch_hand=None)
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["platoonContext"]["opposingStarterHand"] is None
        assert hitter["platoonContext"]["status"] == STATUS_MISSING_DATA


class TestStarterArsenalAssociatedWithCorrectHitter:
    def test_away_hitters_see_home_starter(self):
        opp_savant = {"xERA": 3.50, "kPct": 24.0, "bbPct": 7.0, "vsLHH": {"pa": 100, "xERA": 3.2}}
        g = _confirmed_game(_full_lineup(), opp_savant=opp_savant)
        ctx = build_hitter_feature_context(g, "away")
        for hitter in ctx["hitters"]:
            assert hitter["starterContext"]["pitcherId"] == 999
            assert hitter["starterContext"]["xERA"] == 3.50
            assert hitter["starterContext"]["status"] == STATUS_AVAILABLE

    def test_home_hitters_see_away_starter(self):
        lineup = _full_lineup()
        g = {
            "gameId": 1,
            "away": {
                "team": "Away Team", "abbr": "AWY",
                "pitcher": {"id": 111, "name": "Away Starter", "pitchHand": "R"},
                "pitcherSavant": {"xERA": 4.10},
                "bullpen": {},
            },
            "home": {"team": "Home Team", "abbr": "HOM"},
            "homeTeamStats": {
                "lineupConfirmedOfficial": True,
                "confirmedLineup": lineup,
                "teamSeasonWOBA": 0.315,
            },
            "awayTeamStats": {},
            "park": {"name": "Home Park", "dome": False, "parkFactor": 100},
        }
        ctx = build_hitter_feature_context(g, "home")
        assert ctx["hitters"][0]["starterContext"]["pitcherId"] == 111
        assert ctx["hitters"][0]["starterContext"]["xERA"] == 4.10

    def test_missing_starter_arsenal_still_flagged(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        arsenal = ctx["hitters"][0]["starterContext"]["pitchArsenal"]
        assert arsenal["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES


class TestPitchLevelFieldsRetainDimensions:
    def test_pitch_type_velocity_shape_location_count_blocks_all_present(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["pitchTypeMatchup"]["status"] == STATUS_NOT_COMPUTED
        assert hitter["velocityMatchup"]["status"] == STATUS_NOT_COMPUTED
        assert hitter["velocityMatchup"]["buckets"] == ["<93", "93-95", "95-97", "97-99", "99+"]
        # Phase 2: the raw-pitch-archive ingestion path now exists
        # (scripts/fetch_statcast_pitch_log.py) -- with no archive for
        # this specific batter these are NOT_COMPUTED (a wiring/data
        # gap for this batter), not UNAVAILABLE_FROM_CURRENT_SOURCES
        # (no source exists at all), which was the correct Phase 1
        # status before that ingestion path was built.
        assert hitter["pitchShapeContext"]["status"] == STATUS_NOT_COMPUTED
        assert hitter["locationContext"]["status"] == STATUS_NOT_COMPUTED
        assert hitter["countContext"]["status"] == STATUS_NOT_COMPUTED


class TestMissingBatTrackingFailsGracefully:
    def test_bat_tracking_block_present_but_unavailable_no_exception(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        bt = ctx["hitters"][0]["batTracking"]
        assert bt["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
        assert "fields" in bt
        assert bt["fields"]["avgBatSpeed"]["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
        assert bt["fields"]["avgBatSpeed"]["value"] is None


class TestMissingCatcherUmpireFailsGracefully:
    def test_catcher_and_umpire_blocks_present_and_unavailable(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert hitter["catcherContext"]["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
        assert hitter["umpireContext"]["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
        # never raises, never fabricates a value
        assert "value" not in hitter["catcherContext"] or hitter["catcherContext"].get("value") is None


class TestSnapshotTimestampsPreserved:
    def test_source_meta_echoed_into_data_freshness(self):
        g = _confirmed_game(_full_lineup())
        meta = {"savantTeamFetchedAt": "2026-08-10T12:00:00Z", "weatherUpdatedAt": "2026-08-10T11:00:00Z"}
        ctx = build_hitter_feature_context(g, "away", source_meta=meta)
        hitter = ctx["hitters"][0]
        assert hitter["dataFreshness"]["savantTeamFetchedAt"] == "2026-08-10T12:00:00Z"
        assert hitter["dataFreshness"]["weatherUpdatedAt"] == "2026-08-10T11:00:00Z"

    def test_no_source_meta_does_not_raise(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["hitters"][0]["dataFreshness"] == {}


class TestHistoricalSnapshotNotOverwrittenByCurrentValues:
    def test_function_never_mutates_input_game_dict(self):
        g = _confirmed_game(_full_lineup())
        import copy
        g_before = copy.deepcopy(g)
        build_hitter_feature_context(g, "away")
        assert g == g_before

    def test_function_never_mutates_hitter_dict(self):
        lineup = _full_lineup()
        g = _confirmed_game(lineup)
        import copy
        lineup_before = copy.deepcopy(lineup)
        build_hitter_feature_context(g, "away")
        assert lineup == lineup_before


class TestSampleSizesAndFallbackStateExposed:
    def test_sample_sizes_present(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        assert "seasonPA" in hitter["sampleSizes"]
        assert hitter["sampleSizes"]["seasonPA"] == 400

    def test_data_availability_summarizes_every_block_status(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        hitter = ctx["hitters"][0]
        avail = hitter["dataAvailability"]
        assert avail["batTracking"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
        assert avail["starterContext"] == STATUS_AVAILABLE
        assert avail["baselineTalent"] == STATUS_PARTIAL

    def test_uncertainty_flags_reflect_missing_domains(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        flags = ctx["hitters"][0]["uncertaintyFlags"]
        assert any("batTracking" in f for f in flags)
        assert any("catcherContext" in f for f in flags)
        assert any("umpireContext" in f for f in flags)


class TestBaselineTalentHorizons:
    def test_current_season_woba_available_other_horizons_unavailable(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        horizons = ctx["hitters"][0]["baselineTalent"]["horizons"]
        assert horizons["currentSeason"]["status"] == STATUS_AVAILABLE
        assert horizons["currentSeason"]["stats"]["wOBA"] == 0.320
        for key in ("career", "previousSeason", "rolling90d", "rolling60d", "rolling30d"):
            assert horizons[key]["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES


class TestParkAndWeatherContext:
    def test_park_reused_from_game_dict(self):
        g = _confirmed_game(_full_lineup(), park={"name": "Coors Field", "dome": False, "parkFactor": 115})
        ctx = build_hitter_feature_context(g, "away")
        park_ctx = ctx["hitters"][0]["parkContext"]
        assert park_ctx["status"] == STATUS_PARTIAL
        assert park_ctx["runFactor"] == 115
        assert park_ctx["hrFactor"]["status"] == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES

    def test_weather_missing_by_default(self):
        g = _confirmed_game(_full_lineup())
        ctx = build_hitter_feature_context(g, "away")
        assert ctx["hitters"][0]["weatherContext"]["status"] == STATUS_MISSING_DATA

    def test_weather_supplied_via_lookup(self):
        g = _confirmed_game(_full_lineup())
        weather_lookup = {"Home Team": {"dome": False, "temp": 85, "wind": 10, "windDir": "SW"}}
        ctx = build_hitter_feature_context(g, "away", weather_by_team=weather_lookup)
        weather_ctx = ctx["hitters"][0]["weatherContext"]
        assert weather_ctx["status"] == STATUS_AVAILABLE
        assert weather_ctx["temp"] == 85


class TestNoDataFallbackPreservesExistingBehavior:
    def test_platoon_context_result_matches_direct_platoon_module_call(self):
        """Sanity check that this module reuses (not duplicates) platoon_context's
        own computation -- the platoonWOBA value must match hitter_platoon_value()
        called directly on the same inputs."""
        from lib.research.platoon_context import hitter_platoon_value
        h = _hitter(1, "R", season_woba=0.300, vs_lhp={"woba": 0.410, "pa": 50})
        g = _confirmed_game([h], opp_pitch_hand="L")
        ctx = build_hitter_feature_context(g, "away")
        expected_woba, expected_pa, expected_fallback = hitter_platoon_value(h, "L")
        result = ctx["hitters"][0]["platoonContext"]
        assert result["platoonWOBA"] == expected_woba
        assert result["platoonPA"] == expected_pa
        assert result["usedSeasonFallback"] == expected_fallback
