#!/usr/bin/env python3
"""
tests/edgelab/test_team_batting_reconstruction.py
=========================================================
Coverage for lib/edgelab/backtest/team_batting_reconstruction.py --
MLB-RSCH-0012's PIT-safe batting-component data layer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.backtest import team_batting_reconstruction as tbr


def _boxscore(away=None, home=None):
    return {
        "teams": {
            "away": {"teamStats": {"batting": away or {}}} if away is not None else {},
            "home": {"teamStats": {"batting": home or {}}} if home is not None else {},
        }
    }


class TestExtractTeamBattingLine:
    def test_extracts_all_fields(self):
        box = _boxscore(away={
            "plateAppearances": 38, "atBats": 34, "hits": 9, "doubles": 2, "triples": 0,
            "homeRuns": 1, "baseOnBalls": 3, "strikeOuts": 8, "hitByPitch": 1, "sacFlies": 0, "runs": 5,
        })
        line = tbr.extract_team_batting_line(box, "away")
        assert line["plateAppearances"] == 38
        assert line["hits"] == 9
        assert line["homeRuns"] == 1

    def test_missing_boxscore_returns_none(self):
        assert tbr.extract_team_batting_line(None, "away") is None

    def test_missing_batting_block_returns_none(self):
        assert tbr.extract_team_batting_line({"teams": {"away": {}}}, "away") is None

    def test_missing_plate_appearances_returns_none(self):
        box = _boxscore(away={"hits": 5})
        assert tbr.extract_team_batting_line(box, "away") is None

    def test_never_fabricates_zero_for_missing_field(self):
        box = _boxscore(away={"plateAppearances": 38, "atBats": 34})
        line = tbr.extract_team_batting_line(box, "away")
        assert line["hits"] is None  # never a fabricated 0


class TestDerivedBattingRates:
    def test_computes_all_rates(self):
        line = {
            "plateAppearances": 40, "atBats": 35, "hits": 10, "doubles": 2, "triples": 1,
            "homeRuns": 1, "baseOnBalls": 4, "strikeOuts": 9, "hitByPitch": 1, "sacFlies": 0,
        }
        rates = tbr.derived_batting_rates(line)
        assert rates["bbRate"] == round(4 / 40, 6)
        assert rates["kRate"] == round(9 / 40, 6)
        assert rates["hrRate"] == round(1 / 40, 6)
        assert rates["xbhRate"] == round(4 / 40, 6)  # 2+1+1
        # OBP = (H+BB+HBP)/(AB+BB+HBP+SF) = (10+4+1)/(35+4+1+0) = 15/40
        assert rates["obpProxy"] == round(15 / 40, 6)
        # total bases = 10 + 2 + 2*1 + 3*1 = 17; SLG = 17/35
        assert rates["sluggingProxy"] == round(17 / 35, 6)
        avg = round(10 / 35, 6)
        assert rates["isoProxy"] == round(rates["sluggingProxy"] - avg, 6)

    def test_none_line_returns_none(self):
        assert tbr.derived_batting_rates(None) is None

    def test_zero_at_bats_never_divides_by_zero(self):
        line = {"plateAppearances": 5, "atBats": 0, "hits": 0, "doubles": 0, "triples": 0,
                "homeRuns": 0, "baseOnBalls": 5, "strikeOuts": 0, "hitByPitch": 0, "sacFlies": 0}
        rates = tbr.derived_batting_rates(line)
        assert rates["sluggingProxy"] is None
        assert rates["isoProxy"] is None
        assert rates["bbRate"] == 1.0

    def test_missing_hbp_sacflies_defaults_to_zero_not_none(self):
        """hitByPitch/sacFlies are legitimately 0 far more often than missing -- treated as 0 for OBP, never propagated as None when the OTHER required fields are present."""
        line = {"plateAppearances": 10, "atBats": 9, "hits": 3, "doubles": 0, "triples": 0,
                "homeRuns": 0, "baseOnBalls": 1, "strikeOuts": 2, "hitByPitch": None, "sacFlies": None}
        rates = tbr.derived_batting_rates(line)
        assert rates["obpProxy"] == round((3 + 1) / (9 + 1), 6)


class TestTeamBattingGames:
    def test_attaches_correct_side_and_rates(self):
        team_games = [
            {"gamePk": 1, "date": "2023-04-01", "gameNumber": 1, "side": "home"},
            {"gamePk": 2, "date": "2023-04-02", "gameNumber": 1, "side": "away"},
        ]
        lines_by_pk = {
            1: {"home": {"plateAppearances": 38, "atBats": 34, "hits": 9, "doubles": 1, "triples": 0,
                          "homeRuns": 1, "baseOnBalls": 3, "strikeOuts": 8, "hitByPitch": 0, "sacFlies": 0, "runs": 5},
                "away": {"plateAppearances": 36, "atBats": 33, "hits": 7, "doubles": 0, "triples": 0,
                         "homeRuns": 0, "baseOnBalls": 2, "strikeOuts": 10, "hitByPitch": 0, "sacFlies": 0, "runs": 2}},
            2: {"away": None, "home": None},
        }
        out = tbr.team_batting_games(team_games, lines_by_pk)
        assert out[0]["hits"] == 9  # home side of game 1
        assert out[0]["bbRate"] is not None
        assert out[1]["hits"] is None  # game 2 has no cached line at all

    def test_uncached_game_pk_produces_all_none_fields(self):
        team_games = [{"gamePk": 999, "date": "2023-04-01", "gameNumber": 1, "side": "home"}]
        out = tbr.team_batting_games(team_games, {})
        assert out[0]["hits"] is None
        assert out[0]["bbRate"] is None

    def test_preserves_original_game_metadata(self):
        team_games = [{"gamePk": 1, "date": "2023-04-01", "gameNumber": 2, "side": "away", "opponentTeamId": 147}]
        out = tbr.team_batting_games(team_games, {})
        assert out[0]["gamePk"] == 1
        assert out[0]["gameNumber"] == 2
        assert out[0]["opponentTeamId"] == 147

    def test_season_to_date_rate_reuse_works_on_derived_fields(self):
        """Proves the field-agnostic reuse contract: season_to_date_rate (MLB-RSCH-0005, unchanged) works directly on this module's own derived rate fields."""
        from lib.edgelab.backtest.team_offense_recency_reconstruction import season_to_date_rate
        team_games = [
            {"gamePk": 1, "date": "2023-04-01", "gameNumber": 1, "side": "home"},
            {"gamePk": 2, "date": "2023-04-02", "gameNumber": 1, "side": "home"},
        ]
        lines_by_pk = {
            1: {"home": {"plateAppearances": 40, "atBats": 35, "hits": 10, "doubles": 0, "triples": 0,
                         "homeRuns": 2, "baseOnBalls": 5, "strikeOuts": 8, "hitByPitch": 0, "sacFlies": 0, "runs": 5}},
            2: {"home": {"plateAppearances": 40, "atBats": 36, "hits": 8, "doubles": 0, "triples": 0,
                         "homeRuns": 0, "baseOnBalls": 4, "strikeOuts": 10, "hitByPitch": 0, "sacFlies": 0, "runs": 3}},
        }
        games = tbr.team_batting_games(team_games, lines_by_pk)
        rate = season_to_date_rate(games, "hrRate")
        assert rate == (2 / 40 + 0 / 40) / 2
