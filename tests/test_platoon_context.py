#!/usr/bin/env python3
"""
tests/test_platoon_context.py
==================================
Unit tests for lib/research/platoon_context.py -- the confirmed-lineup
handedness/platoon engine (Baseball Input Data / Platoon Context mission).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.research.platoon_context import (
    classify_hand,
    resolve_effective_hand,
    lineup_handedness_composition,
    hitter_platoon_value,
    weighted_lineup_platoon_woba,
    starter_hand_mix_split,
    build_offense_platoon_context,
    STATUS_OK,
    STATUS_LINEUP_UNCONFIRMED,
    STATUS_MISSING_DATA,
    MIN_PA_HITTER_SPLIT,
    MIN_PA_STARTER_SPLIT,
    PLATOON_ADJ_CAP_RPG,
)


def _hitter(order, bat_side, season_woba=0.320, vs_lhp=None, vs_rhp=None):
    return {
        "order": order,
        "playerId": f"p{order}",
        "batSide": bat_side,
        "seasonWOBA": season_woba,
        "platoonSplits": {"vsLHP": vs_lhp, "vsRHP": vs_rhp},
    }


def _full_lineup(hands):
    """hands: list of 9 'L'/'R'/'S' codes."""
    return [_hitter(i + 1, h) for i, h in enumerate(hands)]


class TestClassifyHand:
    def test_normalizes_codes(self):
        assert classify_hand("L") == "L"
        assert classify_hand("Left") == "L"
        assert classify_hand("r") == "R"
        assert classify_hand("Switch") == "S"
        assert classify_hand("B") == "S"

    def test_none_and_unknown(self):
        assert classify_hand(None) is None
        assert classify_hand("") is None
        assert classify_hand("X") is None


class TestResolveEffectiveHand:
    def test_switch_hitter_opposes_pitcher(self):
        assert resolve_effective_hand("S", "L") == "R"
        assert resolve_effective_hand("S", "R") == "L"

    def test_non_switch_unaffected(self):
        assert resolve_effective_hand("L", "R") == "L"
        assert resolve_effective_hand("R", "L") == "R"

    def test_unresolvable(self):
        assert resolve_effective_hand(None, "L") is None
        assert resolve_effective_hand("S", None) is None


class TestLineupHandednessComposition:
    def test_counts_and_top3(self):
        lineup = _full_lineup(["L", "R", "S", "R", "R", "L", "R", "R", "L"])
        comp = lineup_handedness_composition(lineup)
        assert comp["countL"] == 3
        assert comp["countR"] == 5
        assert comp["countS"] == 1
        assert comp["top3Handedness"] == "LRS"
        assert comp["top3Resolved"] == 3

    def test_empty_lineup(self):
        comp = lineup_handedness_composition([])
        assert comp["countL"] == comp["countR"] == comp["countS"] == 0
        assert comp["top3Handedness"] is None


class TestHitterPlatoonValue:
    def test_uses_real_split_when_pa_adequate(self):
        h = _hitter(1, "R", season_woba=0.300, vs_lhp={"woba": 0.410, "pa": MIN_PA_HITTER_SPLIT})
        woba, pa, fallback = hitter_platoon_value(h, "L")
        assert woba == 0.410
        assert fallback is False

    def test_shrinks_to_season_woba_below_pa_floor(self):
        h = _hitter(1, "R", season_woba=0.300, vs_lhp={"woba": 0.410, "pa": MIN_PA_HITTER_SPLIT - 1})
        woba, pa, fallback = hitter_platoon_value(h, "L")
        assert woba == 0.300
        assert fallback is True

    def test_switch_hitter_split_keyed_by_pitcher_hand(self):
        # platoonSplits.vsLHP/vsRHP are keyed by which hand the PITCHER
        # threw with, regardless of which side the batter stood on that
        # PA (a switch hitter's own vsLHP split already reflects them
        # batting right-handed in each of those PAs) -- so facing an LHP
        # always selects vsLHP, for a switch hitter exactly like anyone
        # else. resolve_effective_hand() is used only as an
        # availability guard (do we know this batter's side at all),
        # never to pick which split key to read.
        h = _hitter(1, "S", season_woba=0.300, vs_lhp={"woba": 0.450, "pa": 100})
        woba, pa, fallback = hitter_platoon_value(h, "L")
        assert woba == 0.450
        assert fallback is False

    def test_unresolvable_batside_falls_back_to_season(self):
        h = _hitter(1, None, season_woba=0.300, vs_lhp={"woba": 0.450, "pa": 100})
        woba, pa, fallback = hitter_platoon_value(h, "L")
        assert woba == 0.300
        assert fallback is True

    def test_nothing_available_returns_none(self):
        h = {"order": 1, "batSide": "R", "platoonSplits": {}}
        woba, pa, fallback = hitter_platoon_value(h, "L")
        assert woba is None and pa is None and fallback is False


class TestWeightedLineupPlatoonWoba:
    def test_top3_weighted_more_heavily(self):
        # Top-3 all have a big platoon edge, rest are neutral -- the
        # weighted average should sit closer to the top-3 value than a
        # flat unweighted average would.
        lineup = []
        for i in range(1, 10):
            if i <= 3:
                lineup.append(_hitter(i, "R", vs_lhp={"woba": 0.450, "pa": 100}))
            else:
                lineup.append(_hitter(i, "R", vs_lhp={"woba": 0.300, "pa": 100}))
        weighted, n_used, n_real, n_fallback = weighted_lineup_platoon_woba(lineup, "L")
        flat_avg = (3 * 0.450 + 6 * 0.300) / 9
        assert weighted > flat_avg
        assert n_used == 9 and n_real == 9 and n_fallback == 0

    def test_no_lineup_returns_none(self):
        weighted, n_used, n_real, n_fallback = weighted_lineup_platoon_woba([], "L")
        assert weighted is None and n_used == 0


class TestStarterHandMixSplit:
    def test_blends_by_hand_counts(self):
        ps = {
            "vsLHH": {"xERA": 3.00, "pa": 100},
            "vsRHH": {"xERA": 5.00, "pa": 100},
        }
        # Lineup is mostly left-handed -> blended xERA should sit closer to 3.00
        xera, coverage, extras = starter_hand_mix_split(ps, {"L": 7, "R": 2})
        assert xera < 4.0
        assert coverage == 1.0

    def test_below_pa_floor_excluded(self):
        ps = {
            "vsLHH": {"xERA": 3.00, "pa": MIN_PA_STARTER_SPLIT - 1},
            "vsRHH": {"xERA": 5.00, "pa": 100},
        }
        xera, coverage, extras = starter_hand_mix_split(ps, {"L": 5, "R": 4})
        # Only the R split counts -- coverage should be < 1.0
        assert coverage < 1.0
        assert xera == 5.00

    def test_missing_splits_returns_none(self):
        xera, coverage, extras = starter_hand_mix_split({}, {"L": 5, "R": 4})
        assert xera is None and coverage == 0.0

    def test_power_indicator_surfaced_as_extra(self):
        ps = {"vsLHH": {"xERA": 3.00, "pa": 100, "hardHitPct": 45.0}}
        xera, coverage, extras = starter_hand_mix_split(ps, {"L": 9, "R": 0})
        assert extras.get("hardHitPctVsHandMix") == 45.0


class TestBuildOffensePlatoonContext:
    def _game(self, lineup_confirmed=True, confirmed_lineup=None, opp_hand="L",
              opp_vs_lhh=None, opp_vs_rhh=None, team_season_woba=0.320):
        return {
            "awayTeamStats": {
                "lineupConfirmedOfficial": lineup_confirmed,
                "confirmedLineup": confirmed_lineup,
                "teamSeasonWOBA": team_season_woba,
            },
            "home": {
                "pitcher": {"pitchHand": opp_hand},
                "pitcherSavant": {"vsLHH": opp_vs_lhh, "vsRHH": opp_vs_rhh},
            },
        }

    def test_unconfirmed_lineup_is_honest_and_zero_adjustment(self):
        g = self._game(lineup_confirmed=False, confirmed_lineup=None)
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED
        assert ctx["aggregatePlatoonAdvantageRPG"] == 0.0
        assert ctx["lineupConfirmed"] is False

    def test_missing_confirmed_lineup_list_is_unconfirmed(self):
        g = self._game(lineup_confirmed=True, confirmed_lineup=[])
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED

    def test_missing_starter_hand_is_missing_data(self):
        g = self._game(confirmed_lineup=_full_lineup(["R"] * 9), opp_hand=None)
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["status"] == STATUS_MISSING_DATA
        assert ctx["aggregatePlatoonAdvantageRPG"] == 0.0

    def test_ok_status_with_both_components(self):
        lineup = _full_lineup(["L"] * 9)
        for h in lineup:
            h["platoonSplits"]["vsRHP"] = {"woba": 0.420, "pa": 100}
        g = self._game(
            confirmed_lineup=lineup, opp_hand="R",
            opp_vs_lhh={"xERA": 5.20, "pa": 100},
            team_season_woba=0.320,
        )
        # give the starter a season baseline
        g["home"]["pitcherSavant"]["xFIP"] = 4.00
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["status"] == STATUS_OK
        assert ctx["aggregatePlatoonAdvantageRPG"] > 0  # favorable matchup -> positive
        assert abs(ctx["aggregatePlatoonAdvantageRPG"]) <= PLATOON_ADJ_CAP_RPG

    def test_adjustment_bounded_by_cap(self):
        lineup = _full_lineup(["L"] * 9)
        for h in lineup:
            h["platoonSplits"]["vsRHP"] = {"woba": 0.700, "pa": 200}  # absurdly extreme
        g = self._game(
            confirmed_lineup=lineup, opp_hand="R",
            opp_vs_lhh={"xERA": 9.00, "pa": 200},
            team_season_woba=0.250,
        )
        g["home"]["pitcherSavant"]["xFIP"] = 2.00
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["aggregatePlatoonAdvantageRPG"] == PLATOON_ADJ_CAP_RPG

    def test_favorable_vs_unfavorable_direction(self):
        """
        Favorable platoon lineup (great wOBA vs the specific starter hand)
        must produce a HIGHER aggregatePlatoonAdvantageRPG than an
        unfavorable one, all else equal (Requirement 5).
        """
        def make(woba):
            lineup = _full_lineup(["L"] * 9)
            for h in lineup:
                h["platoonSplits"]["vsRHP"] = {"woba": woba, "pa": 100}
            g = self._game(confirmed_lineup=lineup, opp_hand="R", team_season_woba=0.320)
            return g

        favorable = build_offense_platoon_context(make(0.420), "away")
        unfavorable = build_offense_platoon_context(make(0.220), "away")
        assert favorable["aggregatePlatoonAdvantageRPG"] > unfavorable["aggregatePlatoonAdvantageRPG"]

    def test_handedness_swap_moves_starter_component_direction(self):
        """Same lineup, opposing starter's better hand faced vs worse hand faced."""
        lineup = _full_lineup(["L"] * 9)
        for h in lineup:
            h["platoonSplits"]["vsRHP"] = {"woba": 0.320, "pa": 100}

        g_weak_vs_l = self._game(
            confirmed_lineup=lineup, opp_hand="R",
            opp_vs_lhh={"xERA": 5.50, "pa": 100},
            team_season_woba=0.320,
        )
        g_weak_vs_l["home"]["pitcherSavant"]["xFIP"] = 3.50

        g_strong_vs_l = self._game(
            confirmed_lineup=lineup, opp_hand="R",
            opp_vs_lhh={"xERA": 2.50, "pa": 100},
            team_season_woba=0.320,
        )
        g_strong_vs_l["home"]["pitcherSavant"]["xFIP"] = 3.50

        ctx_weak = build_offense_platoon_context(g_weak_vs_l, "away")
        ctx_strong = build_offense_platoon_context(g_strong_vs_l, "away")
        assert ctx_weak["aggregatePlatoonAdvantageRPG"] > ctx_strong["aggregatePlatoonAdvantageRPG"]

    def test_thin_lineup_below_platoon_floor_skips_hitter_component(self):
        g = self._game(confirmed_lineup=_full_lineup(["L", "R", "S"]))  # only 3 of 9
        ctx = build_offense_platoon_context(g, "away")
        assert ctx["components"]["lineupWobaComponent"] is None
        assert "below" in ctx["hitterSplitAvailability"]

    def test_never_raises_on_empty_game(self):
        ctx = build_offense_platoon_context({}, "away")
        assert ctx["status"] == STATUS_LINEUP_UNCONFIRMED
        assert ctx["aggregatePlatoonAdvantageRPG"] == 0.0
