#!/usr/bin/env python3
"""
tests/test_fetch_lineups_immutable.py
========================================
Golden-equivalence regression suite for scripts/fetch_lineups.py's Phase 5
immutable-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish a
golden baseline, then re-run UNCHANGED after the refactor to prove
identical output. Follows the existing convention established in
tests/test_clv_hardening.py::TestFetchSavantPitchers: import the real
module directly (os.chdir'd into an isolated tmp dir, sys.modules reset
before each import) rather than reimplementing its logic separately —
unlike tests/test_phase1_lineup_fields.py's `_simulate_lineup_fetch()`
helper, which reimplements the same logic standalone and can silently
drift from the real script; this file always calls the actual functions.

Network isolation: fetch_lineups.py's ONLY network entry point is its
module-level fetch_json(url, timeout) function. Every test monkeypatches
fetch_lineups.fetch_json directly to a fake returning canned boxscore-shaped
dicts (or None) keyed by URL — no real HTTP call is ever made, and no test
sleeps in real time (time.sleep is monkeypatched to a no-op).
"""

import copy
import json
import os
import sys
import tempfile
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)


MISSING_FIELDS = {
    'lineupConfirmed': False,
    'lineupPosted': False,
    'lineupStatus': 'missing',
    'lineupConfirmedOfficial': False,
    'lineupSource': 'mlb_stats_api',
    'lineupBattersExpected': 9,
    'lineupBattersFound': 0,
    'lineupBattersResolved': 0,
    'lineupAdjAvailable': False,
    'lineupAdjApplied': False,
    'lineupDataQuality': 'none',
    'lineupWOBADelta': None,
    'lineupAdj': None,
}


class FetchLineupsHarness:
    """Shared fixture-building + isolated-execution helper for fetch_lineups.py."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]
        import fetch_lineups as fl
        self.fl = fl
        # No real network calls, no real sleeps, ever.
        self._url_responses = {}
        self._calls = []

        def _fake_fetch_json(url, timeout=20):
            self._calls.append((url, timeout))
            for substr, response in self._url_responses.items():
                if substr in url:
                    return copy.deepcopy(response) if response is not None else None
            return None

        self.fl.fetch_json = _fake_fetch_json
        self.fl.time.sleep = lambda *a, **k: None

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]

    def set_boxscore_response(self, game_pk, response):
        self._url_responses[f"/game/{game_pk}/boxscore"] = response

    def _write(self, filename, data):
        with open(os.path.join(self.data_dir, filename), "w") as f:
            json.dump(data, f)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            return json.load(f)

    def make_slate(self, games, date="2026-07-27"):
        return {"date": date, "games": games}

    def make_game(self, game_pk="12345", away_abbr="NYY", home_abbr="PHI", status="Scheduled", extra=None):
        g = {
            "gameId": game_pk,
            "away": {"abbr": away_abbr, "team": self._full_name(away_abbr)},
            "home": {"abbr": home_abbr, "team": self._full_name(home_abbr)},
            "status": status,
        }
        if extra:
            g.update(extra)
        return g

    def _full_name(self, abbr):
        return {"NYY": "New York Yankees", "PHI": "Philadelphia Phillies",
                "BOS": "Boston Red Sox", "TB": "Tampa Bay Rays"}.get(abbr, abbr)

    def make_savant_team(self, batters=None, teams=None):
        return {"batters": batters or {}, "teams": teams or {}}

    def make_boxscore(self, away_order=None, home_order=None, away_players=None, home_players=None):
        def _side(order, players):
            return {"battingOrder": order or [], "players": players or {}}
        return {"teams": {"away": _side(away_order, away_players), "home": _side(home_order, home_players)}}

    def make_player(self, name="Player", position="OF"):
        return {"person": {"fullName": name}, "position": {"abbreviation": position}}

    def run_main(self):
        self.fl.main()


class TestCompleteConfirmedLineup(FetchLineupsHarness):

    def test_full_lineup_with_real_xwoba_is_confirmed(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        batters = {str(100 + i): 0.340 for i in range(9)}
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))
        order = [100 + i for i in range(9)]
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("12345", self.make_boxscore(order, order, players, players))

        self.run_main()
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupConfirmed"] is True
        assert g["awayTeamStats"]["lineupConfirmedOfficial"] is True
        assert g["awayTeamStats"]["lineupBattersResolved"] == 9
        assert g["awayTeamStats"]["lineupAdjApplied"] is True
        assert g["awayTeamStats"]["lineupAdj"] is not None
        assert g["homeTeamStats"]["lineupConfirmed"] is True


class TestUnconfirmedLineup(FetchLineupsHarness):

    def test_no_batting_order_is_unconfirmed_missing(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", self.make_boxscore(away_order=[], home_order=[]))

        self.run_main()
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupConfirmed"] is False
        assert g["awayTeamStats"]["lineupStatus"] == "missing"
        assert g["awayTeamStats"]["lineupPosted"] is False
        assert g["awayTeamStats"]["lineupConfirmedOfficial"] is False


class TestPartialLineup(FetchLineupsHarness):

    def test_batting_order_present_but_few_batters_resolved_is_partial(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        # Only 2 of 9 batters have real xwOBA -> below MIN_BATTERS_FOR_CONFIRMED (6)
        batters = {"100": 0.340, "101": 0.330}
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))
        order = list(range(100, 109))
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("12345", self.make_boxscore(order, order, players, players))

        self.run_main()
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupConfirmed"] is False, "partial resolution must not count as confirmed"
        assert g["awayTeamStats"]["lineupConfirmedOfficial"] is True, "battingOrder WAS posted -- official"
        assert g["awayTeamStats"]["lineupBattersResolved"] == 2
        assert g["awayTeamStats"]["lineupAdjApplied"] is False
        assert g["awayTeamStats"]["lineupAdj"] is None
        assert g["awayTeamStats"]["lineupDataQuality"] == "partial"


class TestMissingLineup(FetchLineupsHarness):

    def test_no_game_id_gets_missing_block_without_any_fetch(self):
        game = self.make_game(game_pk=None)
        del game["gameId"]
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())

        self.run_main()
        assert self._calls == [], "no gameId means fetch_json must never be called for this game"
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupStatus"] == "missing"
        assert "No gameId" in g["awayTeamStats"]["lineupStatusReason"]

    def test_api_returns_nothing_gets_missing_block(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", None)

        self.run_main()
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupStatus"] == "missing"
        assert "no data" in g["awayTeamStats"]["lineupStatusReason"].lower()


@pytest.mark.parametrize("status", ["Postponed", "Cancelled", "Suspended", "In Progress", "Final", "Scheduled"])
class TestGameStatusInvariance(FetchLineupsHarness):
    """
    fetch_lineups.py never reads game['status'] anywhere -- it always
    attempts the fetch when gameId is present, regardless of status.
    """

    def test_lineup_still_fetched_regardless_of_status(self, status):
        game = self.make_game(status=status)
        self._write("slate.json", self.make_slate([game]))
        batters = {str(100 + i): 0.340 for i in range(9)}
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))
        order = list(range(100, 109))
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("12345", self.make_boxscore(order, order, players, players))

        self.run_main()
        assert len(self._calls) == 1, f"status={status} must not prevent the boxscore fetch"
        slate = self._read_slate()
        assert slate["games"][0]["awayTeamStats"]["lineupConfirmed"] is True


class TestExcludedGame(FetchLineupsHarness):

    def test_excluded_game_still_gets_lineup_fetched(self):
        """excludedFromSlate is a Recommendation-layer concept; fetch_lineups.py doesn't check it."""
        game = self.make_game(extra={"excludedFromSlate": True, "exclusionReason": "test"})
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", self.make_boxscore(away_order=[], home_order=[]))

        self.run_main()
        assert len(self._calls) == 1
        slate = self._read_slate()
        assert slate["games"][0]["excludedFromSlate"] is True, "unrelated fields must be preserved untouched"


class TestDoubleheader(FetchLineupsHarness):

    def test_two_games_same_teams_same_day_get_independent_correct_lineups(self):
        """
        Doubleheader identity check (Phase 5 Part 7): fetch_lineups.py
        matches purely by each game's own gameId (MLB's unique gamePk),
        never by away/home team name -- two games with identical team
        abbreviations must still get correctly-attributed, independent
        results keyed off their distinct gameId values.
        """
        g1 = self.make_game(game_pk="1001", away_abbr="NYY", home_abbr="PHI")
        g2 = self.make_game(game_pk="1002", away_abbr="NYY", home_abbr="PHI")
        self._write("slate.json", self.make_slate([g1, g2]))
        self._write("savant_team.json", self.make_savant_team(
            teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))

        order_g1 = list(range(200, 209))
        players_g1 = {f"ID{pid}": self.make_player() for pid in order_g1}
        self.set_boxscore_response("1001", self.make_boxscore(order_g1, order_g1, players_g1, players_g1))
        # Game 2: no batting order posted yet (different result from game 1
        # despite identical teams -- proves no cross-game leakage).
        self.set_boxscore_response("1002", self.make_boxscore(away_order=[], home_order=[]))

        self.run_main()
        slate = self._read_slate()
        g1_result, g2_result = slate["games"][0], slate["games"][1]
        assert g1_result["awayTeamStats"]["lineupPosted"] is True
        assert g2_result["awayTeamStats"]["lineupPosted"] is False, (
            "game 2 must not inherit game 1's lineup data despite identical teams"
        )
        urls_called = [url for url, _ in self._calls]
        assert any("/game/1001/" in u for u in urls_called)
        assert any("/game/1002/" in u for u in urls_called)


class TestMismatchedTeamNames(FetchLineupsHarness):

    def test_team_abbr_not_in_woba_map_falls_back_to_league_average(self):
        game = self.make_game(away_abbr="ZZZ")
        self._write("slate.json", self.make_slate([game]))
        batters = {str(100 + i): 0.340 for i in range(9)}
        # "ZZZ" deliberately absent from teams map
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"PHI": {"xwoba": 0.310}}))
        order = list(range(100, 109))
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("12345", self.make_boxscore(order, order, players, players))

        self.run_main()
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["teamSeasonWOBA"] == self.fl.LEAGUE_AVG_WOBA


class TestEmptyAndMalformedAPIResponse(FetchLineupsHarness):

    def test_empty_dict_response_treated_as_no_lineup(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", {})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["awayTeamStats"]["lineupStatus"] == "missing"

    def test_malformed_teams_shape_does_not_crash_whole_script(self):
        """teams.away is a list instead of a dict -- must be caught per-side, not crash main()."""
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", {"teams": {"away": ["not", "a", "dict"], "home": {"battingOrder": []}}})

        self.run_main()  # must not raise
        slate = self._read_slate()
        g = slate["games"][0]
        assert g["awayTeamStats"]["lineupStatus"] == "unknown"
        assert "Error fetching lineup" in g["awayTeamStats"]["lineupStatusReason"]
        # The OTHER side, unaffected by away's malformed shape, still processes normally.
        assert g["homeTeamStats"]["lineupStatus"] == "missing"


class TestRealFetchJsonExceptionSwallowing:
    """
    fetch_json() itself (the REAL, un-monkeypatched implementation) swallows
    ALL exceptions (timeout, auth/transport failure, DNS failure, malformed
    response, etc.) uniformly and returns None. This class exercises the
    real function directly (not FetchLineupsHarness's fake, which replaces
    fetch_json entirely) via a mocked urllib.request.urlopen, to lock in
    that contract at the boundary where it actually lives.
    """

    def setup_method(self):
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]
        import fetch_lineups as fl
        self.fl = fl
        self._orig_urlopen = fl.urllib.request.urlopen

    def teardown_method(self):
        self.fl.urllib.request.urlopen = self._orig_urlopen
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]

    def _patch_urlopen(self, side_effect):
        def _fake(req, timeout=None):
            raise side_effect
        self.fl.urllib.request.urlopen = _fake

    @pytest.mark.parametrize("exc", [
        TimeoutError("simulated timeout"),
        ConnectionError("simulated transport failure"),
        OSError("simulated network unreachable"),
    ])
    def test_network_failures_are_swallowed_and_return_none(self, exc):
        self._patch_urlopen(exc)
        result = self.fl.fetch_json("https://statsapi.mlb.com/api/v1/game/1/boxscore")
        assert result is None

    def test_http_error_is_swallowed_and_returns_none(self):
        import urllib.error
        import io
        err = urllib.error.HTTPError(url="https://x", code=401, msg="Unauthorized", hdrs={}, fp=io.BytesIO(b""))
        self._patch_urlopen(err)
        result = self.fl.fetch_json("https://statsapi.mlb.com/api/v1/game/1/boxscore")
        assert result is None

    def test_malformed_json_response_is_swallowed_and_returns_none(self):
        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{not valid json"

        self.fl.urllib.request.urlopen = lambda req, timeout=None: _FakeResponse()
        result = self.fl.fetch_json("https://statsapi.mlb.com/api/v1/game/1/boxscore")
        assert result is None


class TestAPIFailureModes(FetchLineupsHarness):
    """
    From fetch_lineups.py's main()/fetch_lineup_for_game() perspective, a
    timeout, an auth failure, and any other transport failure are all
    indistinguishable (fetch_json swallows them uniformly into None, per
    TestRealFetchJsonExceptionSwallowing above) -- this class proves
    main()'s OWN handling of "the fetch failed for any reason" is uniform:
    always the same "missing" lineup block, regardless of which underlying
    failure produced the None.
    """

    def test_any_fetch_failure_produces_missing_lineup(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", None)

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["awayTeamStats"]["lineupStatus"] == "missing"
        assert slate["games"][0]["awayTeamStats"]["lineupStatusReason"] == (
            "MLB Stats API returned no data for this game"
        )


class TestMixedSuccessAcrossGames(FetchLineupsHarness):

    def test_one_game_succeeds_one_fails_independently(self):
        g1 = self.make_game(game_pk="1", away_abbr="NYY", home_abbr="PHI")
        g2 = self.make_game(game_pk="2", away_abbr="BOS", home_abbr="TB")
        self._write("slate.json", self.make_slate([g1, g2]))
        batters = {str(100 + i): 0.340 for i in range(9)}
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))
        order = list(range(100, 109))
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("1", self.make_boxscore(order, order, players, players))
        self.set_boxscore_response("2", None)  # second game's fetch fails

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["awayTeamStats"]["lineupConfirmed"] is True
        assert slate["games"][1]["awayTeamStats"]["lineupStatus"] == "missing"


class TestOrderingAndTopLevelPreservation(FetchLineupsHarness):

    def test_game_order_and_top_level_fields_preserved(self):
        g1 = self.make_game(game_pk="1", away_abbr="NYY", home_abbr="PHI")
        g2 = self.make_game(game_pk="2", away_abbr="BOS", home_abbr="TB")
        slate_in = self.make_slate([g1, g2])
        slate_in["someOtherTopLevelField"] = "preserved"
        self._write("slate.json", slate_in)
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("1", self.make_boxscore(away_order=[], home_order=[]))
        self.set_boxscore_response("2", self.make_boxscore(away_order=[], home_order=[]))

        self.run_main()
        slate = self._read_slate()
        assert [g["away"]["abbr"] for g in slate["games"]] == ["NYY", "BOS"]
        assert slate["someOtherTopLevelField"] == "preserved"


class TestIdempotency(FetchLineupsHarness):

    def test_rerun_with_unchanged_inputs_produces_identical_output(self):
        game = self.make_game()
        self._write("slate.json", self.make_slate([game]))
        batters = {str(100 + i): 0.340 for i in range(9)}
        self._write("savant_team.json", self.make_savant_team(
            batters=batters, teams={"NYY": {"xwoba": 0.320}, "PHI": {"xwoba": 0.310}}))
        order = list(range(100, 109))
        players = {f"ID{pid}": self.make_player() for pid in order}
        self.set_boxscore_response("12345", self.make_boxscore(order, order, players, players))

        self.run_main()
        first = self._read_slate()

        # Re-seed slate.json (main() mutates it in place across runs) and rerun.
        self._write("slate.json", self.make_slate([self.make_game()]))
        self.run_main()
        second = self._read_slate()

        assert first["games"][0]["awayTeamStats"] == second["games"][0]["awayTeamStats"]


class TestAliasingAndIdentity:
    """
    Phase 5 Part 4: object-identity proofs (not just value equality) that
    apply_lineups_immutable()/compute_game_lineup_stats_fields() never
    mutate their inputs and never alias caller-owned mutable state into
    their output. Imports fetch_lineups.py directly (it has a
    __main__ guard, so importing it performs no I/O and is safe without
    any tmp-dir isolation) and calls the pure functions with hand-built
    fixtures.
    """

    def setup_method(self):
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]
        import fetch_lineups as fl
        self.fl = fl

    def teardown_method(self):
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]

    def _game(self, away_ts=None, home_ts=None):
        g = {"away": {"abbr": "NYY"}, "home": {"abbr": "PHI"}}
        if away_ts is not None:
            g["awayTeamStats"] = away_ts
        if home_ts is not None:
            g["homeTeamStats"] = home_ts
        return g

    def test_input_game_dict_never_mutated(self):
        pre_existing_away_ts = {"offenseBaselineAdj": 4.5}
        game = self._game(away_ts=pre_existing_away_ts)
        snapshot = copy.deepcopy(game)
        lineup_result = {"away": {"lineupConfirmed": True}, "home": {"lineupConfirmed": False}}

        self.fl.compute_game_lineup_stats_fields(game, lineup_result)

        assert game == snapshot, "compute_game_lineup_stats_fields must never mutate its `game` argument"

    def test_returned_stats_dicts_are_not_the_same_object_as_input(self):
        pre_existing_away_ts = {"offenseBaselineAdj": 4.5}
        game = self._game(away_ts=pre_existing_away_ts)
        lineup_result = {"away": {"lineupConfirmed": True}, "home": {"lineupConfirmed": False}}

        away_ts, home_ts = self.fl.compute_game_lineup_stats_fields(game, lineup_result)

        assert away_ts is not pre_existing_away_ts, "must be a copy, never the same dict object"
        assert away_ts["offenseBaselineAdj"] == 4.5, "pre-existing unrelated keys must be preserved by value"
        assert away_ts["lineupConfirmed"] is True

    def test_mutating_returned_stats_does_not_mutate_original_game(self):
        pre_existing_away_ts = {"offenseBaselineAdj": 4.5}
        game = self._game(away_ts=pre_existing_away_ts)
        lineup_result = {"away": {"lineupConfirmed": True}, "home": {}}

        away_ts, home_ts = self.fl.compute_game_lineup_stats_fields(game, lineup_result)
        away_ts["offenseBaselineAdj"] = 999
        assert pre_existing_away_ts["offenseBaselineAdj"] == 4.5, (
            "mutating the returned stats dict must never leak back into the "
            "original game's awayTeamStats"
        )

    def test_apply_lineups_immutable_returns_new_slate_and_new_game_objects(self):
        g1 = self._game()
        slate = {"date": "2026-07-27", "games": [g1]}
        lineup_results = [{"away": {"lineupConfirmed": True}, "home": {"lineupConfirmed": False}}]

        new_slate = self.fl.apply_lineups_immutable(slate, lineup_results)

        assert new_slate is not slate
        assert new_slate["games"] is not slate["games"]
        assert new_slate["games"][0] is not g1
        assert g1.get("awayTeamStats") is None, "the original game dict must remain untouched"

    def test_mutating_new_slate_does_not_affect_original_slate(self):
        g1 = self._game()
        slate = {"date": "2026-07-27", "games": [g1]}
        lineup_results = [{"away": {"lineupConfirmed": True}, "home": {"lineupConfirmed": False}}]

        new_slate = self.fl.apply_lineups_immutable(slate, lineup_results)
        new_slate["games"][0]["awayTeamStats"]["lineupConfirmed"] = False

        assert "awayTeamStats" not in g1, "the original slate's game objects must never be touched"

    def test_shared_lineup_result_across_two_games_does_not_cross_contaminate(self):
        """Two games receiving references to related-but-distinct lineup_result dicts must not interfere."""
        g1, g2 = self._game(), self._game()
        slate = {"date": "2026-07-27", "games": [g1, g2]}
        shared_style_result = {"lineupConfirmed": True, "lineupAdj": 0.1}
        lineup_results = [
            {"away": dict(shared_style_result), "home": {}},
            {"away": dict(shared_style_result), "home": {}},
        ]

        new_slate = self.fl.apply_lineups_immutable(slate, lineup_results)
        new_slate["games"][0]["awayTeamStats"]["lineupAdj"] = 0.99
        assert new_slate["games"][1]["awayTeamStats"]["lineupAdj"] == 0.1, (
            "mutating game 1's output must not affect game 2's independently built output"
        )


class TestPartialFailureSemantics(FetchLineupsHarness):
    """
    Phase 5 Part 5/8: fetch_lineups.py has no top-level try/except around
    main() (unlike fetch_savant_pitchers.py) -- an uncaught exception
    propagates as Python's default traceback-then-exit-nonzero. This
    class locks that documented asymmetry in as intentional, unchanged
    behavior, and proves prior unrelated field values survive a run.
    """

    def test_prior_unrelated_fields_on_team_stats_are_preserved_across_a_full_run(self):
        game = self.make_game(extra={
            "awayTeamStats": {"offenseBaselineAdj": 4.8, "someOtherField": "keep-me"},
        })
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", self.make_savant_team())
        self.set_boxscore_response("12345", self.make_boxscore(away_order=[], home_order=[]))

        self.run_main()
        slate = self._read_slate()
        ats = slate["games"][0]["awayTeamStats"]
        assert ats["offenseBaselineAdj"] == 4.8
        assert ats["someOtherField"] == "keep-me"
        assert ats["lineupStatus"] == "missing", "the new lineup fields must still be added additively"


class TestCrashBeforeWriteLeavesSlateUntouched:
    """
    fetch_lineups.py loads the ENTIRE slate into memory and writes it
    back exactly once, at the very end of main(). If something raises
    before that write (uncaught, since there's no top-level guard here),
    data/slate.json on disk must be left exactly as it was before this
    run -- never partially updated.
    """

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_lineups" in sys.modules:
            del sys.modules["fetch_lineups"]

    def test_exception_during_apply_leaves_prior_slate_json_untouched(self):
        import fetch_lineups as fl

        pre_run_slate = {"date": "2026-07-27", "games": [], "marker": "pre-run-content"}
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump(pre_run_slate, f)
        with open(os.path.join(self.data_dir, "savant_team.json"), "w") as f:
            json.dump({"batters": {}, "teams": {}}, f)

        def _boom(*a, **k):
            raise RuntimeError("simulated crash during apply")

        fl.apply_lineups_immutable = _boom

        with pytest.raises(RuntimeError):
            fl.main()

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            on_disk = json.load(f)
        assert on_disk == pre_run_slate, (
            "a crash before the single end-of-main write must leave the prior "
            "slate.json completely untouched, not partially updated"
        )

    def test_uncaught_exception_produces_nonzero_exit_via_subprocess(self):
        """
        Documents (and locks in) that fetch_lineups.py, unlike
        fetch_savant_pitchers.py, has no top-level try/except -- an
        uncaught exception propagates as Python's default traceback and
        a nonzero exit code, not a clean sys.exit(1) with a structured
        message. This is pre-existing, intentional-by-omission behavior,
        not something this phase changes.
        """
        import subprocess
        # Malformed slate.json -> json.load() raises uncaught (no try/except
        # around this read in fetch_lineups.py, unlike fetch_savant_pitchers.py).
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            f.write("{not valid json")
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
        result = subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "fetch_lineups.py")],
            cwd=self.tmp, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "Traceback" in result.stderr, "must be Python's default traceback, not a structured error message"
