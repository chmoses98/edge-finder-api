#!/usr/bin/env python3
"""
tests/test_fetch_savant_pitchers_immutable.py
================================================
Golden-equivalence regression suite for scripts/fetch_savant_pitchers.py's
Phase 5 immutable-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish a
golden baseline, then re-run UNCHANGED after the refactor to prove
identical output. Follows the same module-import + os.chdir + sys.modules
reset convention already established for this exact script in
tests/test_clv_hardening.py::TestFetchSavantPitchers.

Two isolation layers are used depending on what a test needs to exercise:
  - Most tests monkeypatch fetch_batch() directly (the per-endpoint-type
    batch fetcher) to a fake keyed by endpoint_type -- fast, and bypasses
    HTTP/retry entirely for tests that only care about parse/merge logic.
  - TestRetryAndBackoff monkeypatches urllib.request.urlopen instead, to
    exercise fetch_json()'s own retry/backoff loop for real (with
    time.sleep mocked to a no-op recording call args, so no test ever
    sleeps in real time).
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


class FetchSavantPitchersHarness:

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp

        self._batch_responses = {}  # endpoint_type -> {pid: data}
        self._batch_calls = []

        def _fake_fetch_batch(endpoint_type, pitcher_ids, batch_num, timeout=50):
            self._batch_calls.append((endpoint_type, tuple(pitcher_ids)))
            resp = self._batch_responses.get(endpoint_type, {})
            return {pid: copy.deepcopy(resp[pid]) for pid in pitcher_ids if pid in resp}

        self.fsp.fetch_batch = _fake_fetch_batch
        self.fsp.time.sleep = lambda *a, **k: None

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def set_batch_response(self, endpoint_type, pid_to_data):
        self._batch_responses[endpoint_type] = pid_to_data

    def _write(self, filename, data):
        with open(os.path.join(self.data_dir, filename), "w") as f:
            json.dump(data, f)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            return json.load(f)

    def make_slate(self, games, date="2026-07-27"):
        return {"date": date, "games": games}

    def make_pitcher(self, pid="605400", name="Pitcher A"):
        return {"id": pid, "name": name}

    def make_game(self, away_abbr="NYY", home_abbr="PHI",
                  away_pitcher=None, home_pitcher=None,
                  away_ps=None, home_ps=None, extra=None):
        g = {
            "away": {"abbr": away_abbr, "pitcher": away_pitcher,
                     "pitcherSavant": away_ps if away_ps is not None else {}},
            "home": {"abbr": home_abbr, "pitcher": home_pitcher,
                     "pitcherSavant": home_ps if home_ps is not None else {}},
        }
        if extra:
            g.update(extra)
        return g

    def run_main(self):
        self.fsp.main()


class TestBothPitchersFound(FetchSavantPitchersHarness):

    def test_both_starters_resolve_all_fields(self):
        away_p, home_p = self.make_pitcher("100", "Away Ace"), self.make_pitcher("200", "Home Ace")
        game = self.make_game(away_pitcher=away_p, home_pitcher=home_p,
                               away_ps={"xFIP": 3.5}, home_ps={"xFIP": 4.0})
        self._write("slate.json", self.make_slate([game]))

        self.set_batch_response("pitcherfbpct", {"100": 0.35, "200": 0.30})
        self.set_batch_response("velocity", {
            "100": {"velocityRecent": 94.2, "velocitySeason": 94.5, "velocityStartsN": 5},
            "200": {"velocityRecent": 91.0, "velocitySeason": 91.0, "velocityStartsN": 6},
        })
        self.set_batch_response("tto", {
            "100": {"ttoSplit": 0.02, "ttoRisk": False, "available": True, "tto1": 0.30, "tto3": 0.32},
            "200": {"ttoSplit": 0.04, "ttoRisk": True, "available": True, "tto1": 0.29, "tto3": 0.33},
        })

        self.run_main()
        slate = self._read_slate()
        away_ps, home_ps = slate["games"][0]["away"]["pitcherSavant"], slate["games"][0]["home"]["pitcherSavant"]
        assert away_ps["fbPct"] == 0.35
        assert away_ps["velocityRecent"] == 94.2
        assert away_ps["ttoSplit"] == 0.02
        assert away_ps["xFIP"] == 3.5, "pre-existing xFIP (from Vercel slate API) must be untouched"
        assert home_ps["fbPct"] == 0.30
        assert home_ps["ttoRisk"] is True


class TestOnlyAwayPitcherFound(FetchSavantPitchersHarness):

    def test_home_pitcher_tbd_leaves_home_untouched(self):
        away_p = self.make_pitcher("100", "Away Ace")
        game = self.make_game(away_pitcher=away_p, home_pitcher=None,
                               away_ps={"xFIP": 3.5}, home_ps={"xFIP": 4.0})
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"100": 0.35})
        self.set_batch_response("velocity", {"100": {"velocityRecent": 94.0, "velocitySeason": 94.0, "velocityStartsN": 5}})
        self.set_batch_response("tto", {"100": {"ttoSplit": 0.02, "available": True}})

        self.run_main()
        slate = self._read_slate()
        away_ps, home_ps = slate["games"][0]["away"]["pitcherSavant"], slate["games"][0]["home"]["pitcherSavant"]
        assert away_ps["fbPct"] == 0.35
        assert home_ps == {"xFIP": 4.0}, "TBD home pitcher's pitcherSavant block must be left completely alone"
        assert ("100",) in [c[1] for c in self._batch_calls]


class TestOnlyHomePitcherFound(FetchSavantPitchersHarness):

    def test_away_pitcher_tbd_leaves_away_untouched(self):
        home_p = self.make_pitcher("200", "Home Ace")
        game = self.make_game(away_pitcher=None, home_pitcher=home_p,
                               away_ps={"xFIP": 3.5}, home_ps={"xFIP": 4.0})
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"200": 0.30})
        self.set_batch_response("velocity", {})
        self.set_batch_response("tto", {})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"] == {"xFIP": 3.5}
        assert slate["games"][0]["home"]["pitcherSavant"]["fbPct"] == 0.30


class TestNeitherPitcherFound(FetchSavantPitchersHarness):

    def test_both_tbd_makes_no_enrich_calls(self):
        game = self.make_game(away_pitcher=None, home_pitcher=None,
                               away_ps={"xFIP": 3.5}, home_ps={"xFIP": 4.0})
        self._write("slate.json", self.make_slate([game]))

        self.run_main()
        assert self._batch_calls == []
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"] == {"xFIP": 3.5}
        assert slate["games"][0]["home"]["pitcherSavant"] == {"xFIP": 4.0}


class TestNullPitcherCrashRegression(FetchSavantPitchersHarness):
    """The exact v5.0 crash this script was hardened against (pitcher=null -> AttributeError)."""

    def test_null_pitcher_value_does_not_crash(self):
        game = self.make_game(away_pitcher=None, home_pitcher=self.make_pitcher("200"))
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"200": 0.30})

        self.run_main()  # must not raise
        slate = self._read_slate()
        assert slate["games"][0]["home"]["pitcherSavant"].get("fbPct") == 0.30


class TestNameNormalization(FetchSavantPitchersHarness):

    def test_pitcher_ids_are_stringified_consistently(self):
        """safe_pitcher_id() must coerce int IDs to strings for map lookups."""
        game = self.make_game(away_pitcher={"id": 605400, "name": "Snell"})  # int, not str
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"605400": 0.28})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"].get("fbPct") == 0.28


class TestDuplicatePlayerNames(FetchSavantPitchersHarness):

    def test_two_different_pitchers_same_name_resolve_independently_by_id(self):
        p1 = self.make_pitcher("100", "John Smith")
        p2 = self.make_pitcher("200", "John Smith")
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI", away_pitcher=p1, home_pitcher=p2)
        self._write("slate.json", self.make_slate([g1]))
        self.set_batch_response("pitcherfbpct", {"100": 0.20, "200": 0.40})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.20
        assert slate["games"][0]["home"]["pitcherSavant"]["fbPct"] == 0.40


class TestMissingPlayerID(FetchSavantPitchersHarness):

    def test_pitcher_dict_without_id_is_treated_as_tbd(self):
        game = self.make_game(away_pitcher={"name": "No ID Guy"})  # no 'id' key
        self._write("slate.json", self.make_slate([game]))

        self.run_main()
        assert self._batch_calls == []


class TestMissingAndNullMetricFields(FetchSavantPitchersHarness):

    def test_missing_fbpct_falls_back_to_savant_team_json(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"))
        self._write("slate.json", self.make_slate([game]))
        self._write("savant_team.json", {"pitchers": {"100": {"fbPct": 0.25}}})
        self.set_batch_response("pitcherfbpct", {})  # enrich has nothing

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.25

    def test_null_velocity_fields_stay_null_no_fallback_source(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"))
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("velocity", {"100": {"velocityRecent": None, "velocitySeason": None, "velocityStartsN": None}})

        self.run_main()
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["velocityRecent"] is None
        assert ps["velocitySeason"] is None


class TestMalformedNumericFields(FetchSavantPitchersHarness):

    def test_recentfip_negative_is_floored_to_zero(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"),
                               away_ps={"recentFIP": -1.5, "startsSampled": 5})
        self._write("slate.json", self.make_slate([game]))

        self.run_main()
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["recentFIP"] == 0.0
        assert ps["recentFIPSanitized"] is True
        assert ps["recentFIPOriginal"] == -1.5

    def test_recentfip_cleared_when_starts_sampled_below_3(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"),
                               away_ps={"recentFIP": 2.8, "startsSampled": 1})
        self._write("slate.json", self.make_slate([game]))

        self.run_main()
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["recentFIP"] is None
        assert ps["recentFIPCleared"] is True

    def test_recentfip_untouched_when_valid_and_enough_samples(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"),
                               away_ps={"recentFIP": 3.2, "startsSampled": 5})
        self._write("slate.json", self.make_slate([game]))

        self.run_main()
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["recentFIP"] == 3.2
        assert "recentFIPSanitized" not in ps
        assert "recentFIPCleared" not in ps


class TestEmptyAndPartialAPIResponse(FetchSavantPitchersHarness):

    def test_empty_response_for_all_endpoints_does_not_crash(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"))
        self._write("slate.json", self.make_slate([game]))
        # No batch responses configured at all -> every fetch_batch call returns {}

        self.run_main()  # must not raise
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["fbPct"] is None
        assert ps["velocityRecent"] is None
        assert ps["ttoSplit"] is None

    def test_partial_response_missing_some_pitcher_ids(self):
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI", away_pitcher=self.make_pitcher("100"))
        g2 = self.make_game(away_abbr="BOS", home_abbr="TB", away_pitcher=self.make_pitcher("200"))
        self._write("slate.json", self.make_slate([g1, g2]))
        self.set_batch_response("pitcherfbpct", {"100": 0.35})  # 200 absent from response

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.35
        assert slate["games"][1]["away"]["pitcherSavant"]["fbPct"] is None


class TestMultiBatchMalformedAfterSuccess:
    """
    Section G gap: BATCH=15 means >15 starters trigger a second
    pitcherfbpct batch. A malformed response on batch 2 must not affect
    batch 1's already-successful results -- each fetch_batch() call is
    independent. Exercises the REAL fetch_batch()/fetch_json() (not the
    harness's per-endpoint fake) via a mocked urlopen, so main()'s actual
    batching loop runs for real, with time.sleep mocked to a no-op.
    """

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp
        fsp.time.sleep = lambda *a, **k: None

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def _write(self, filename, data):
        with open(os.path.join(self.data_dir, filename), "w") as f:
            json.dump(data, f)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            return json.load(f)

    def test_second_pitcherfbpct_batch_malformed_first_batch_survives(self):
        # 17 starters -> BATCH=15 means batch 1 = first 15 IDs, batch 2 = remaining 2.
        pids = [str(100 + i) for i in range(17)]
        games = [
            {"away": {"abbr": f"T{i}A", "pitcher": {"id": pids[i]}, "pitcherSavant": {"xFIP": 4.0}},
             "home": {"abbr": f"T{i}H", "pitcher": None, "pitcherSavant": {}}}
            for i in range(17)
        ]
        self._write("slate.json", {"date": "2026-07-27", "games": games})

        call_log = []

        def _fake_urlopen(req, timeout=None):
            call_log.append(req.full_url)

            class _Resp:
                def __init__(self, body):
                    self._body = body
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return self._body

            if len(call_log) == 1:
                # Batch 1: valid response for the first 15 IDs.
                body = json.dumps({"ok": True, "pitchers": {pid: 0.30 for pid in pids[:15]}}).encode()
                return _Resp(body)
            else:
                # Batch 2 (remaining 2 IDs): malformed JSON.
                return _Resp(b"{not valid json")

        self.fsp.urllib.request.urlopen = _fake_urlopen

        self.fsp.main()  # must not raise

        slate = self._read_slate()
        by_abbr = {g["away"]["abbr"]: g for g in slate["games"]}
        for i in range(15):
            assert by_abbr[f"T{i}A"]["away"]["pitcherSavant"]["fbPct"] == 0.30, (
                f"batch 1 pitcher {pids[i]} must retain its successfully-fetched fbPct "
                f"despite batch 2's malformed response"
            )
        for i in range(15, 17):
            assert by_abbr[f"T{i}A"]["away"]["pitcherSavant"]["fbPct"] is None, (
                "batch 2's pitchers must gracefully get null fbPct, not crash the whole run"
            )


class TestNoGamesInSlate(FetchSavantPitchersHarness):

    def test_empty_games_list_exits_cleanly(self):
        self._write("slate.json", self.make_slate([]))
        self.run_main()  # must return cleanly, no exception, no sys.exit


class TestMissingOrMalformedSlate(FetchSavantPitchersHarness):

    def test_missing_slate_json_exits_1(self):
        with pytest.raises(SystemExit) as exc_info:
            self.run_main()
        assert exc_info.value.code == 1

    def test_malformed_slate_json_exits_1(self):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            f.write("{not valid json")
        with pytest.raises(SystemExit) as exc_info:
            self.run_main()
        assert exc_info.value.code == 1


class TestUnhandledExceptionExitCode:
    """
    fetch_savant_pitchers.py's __main__ guard wraps main() in try/except
    and calls sys.exit(1) on any unhandled exception, printing a full
    traceback to stderr. This is real, load-bearing behavior distinct
    from fetch_lineups.py (which has no such guard) and must survive the
    refactor unchanged.
    """

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def test_module_script_invocation_exits_1_on_unhandled_error(self):
        import subprocess
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            f.write("{not valid json")
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "fetch_savant_pitchers.py")],
            cwd=self.tmp, capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "ERROR" in result.stderr or "not valid JSON" in result.stderr


class TestDoubleheader(FetchSavantPitchersHarness):

    def test_two_games_same_teams_same_day_attribute_correctly_by_pitcher_id(self):
        """
        Doubleheader identity check (Phase 5 Part 7): fetch_savant_pitchers.py
        never matches games by team name at all -- both starter-ID
        collection (safe_pitcher_id(game, side)) and the merge-back pass
        read each game's OWN embedded pitcher.id directly, by iterating
        the same slate['games'] list both times. Two games with
        identical team abbreviations but distinct starting pitchers
        (the normal doubleheader case -- the same pitcher cannot start
        both games of a doubleheader) must still get independently
        correct enrichment.
        """
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI",
                             away_pitcher=self.make_pitcher("100"), home_pitcher=self.make_pitcher("101"))
        g2 = self.make_game(away_abbr="NYY", home_abbr="PHI",
                             away_pitcher=self.make_pitcher("300"), home_pitcher=self.make_pitcher("301"))
        self._write("slate.json", self.make_slate([g1, g2]))
        self.set_batch_response("pitcherfbpct", {"100": 0.10, "101": 0.20, "300": 0.30, "301": 0.40})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.10
        assert slate["games"][0]["home"]["pitcherSavant"]["fbPct"] == 0.20
        assert slate["games"][1]["away"]["pitcherSavant"]["fbPct"] == 0.30, (
            "game 2 must not inherit game 1's fbPct despite identical team abbreviations"
        )
        assert slate["games"][1]["home"]["pitcherSavant"]["fbPct"] == 0.40

    def test_doubleheader_where_one_side_is_missing_a_pitcher_id(self):
        """A TBD starter in game 2 of a doubleheader must not inherit game 1's resolved pitcher's data."""
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI",
                             away_pitcher=self.make_pitcher("100"), home_pitcher=self.make_pitcher("101"))
        g2 = self.make_game(away_abbr="NYY", home_abbr="PHI",
                             away_pitcher=None, home_pitcher=self.make_pitcher("301"))
        self._write("slate.json", self.make_slate([g1, g2]))
        self.set_batch_response("pitcherfbpct", {"100": 0.10, "101": 0.20, "301": 0.40})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.10
        assert slate["games"][1]["away"]["pitcherSavant"] == {}, (
            "game 2's TBD away pitcher must not inherit game 1's away pitcher's fbPct"
        )
        assert slate["games"][1]["home"]["pitcherSavant"]["fbPct"] == 0.40

    def test_reordered_doubleheader_fixtures_still_attribute_correctly(self):
        """Swapping which doubleheader game comes first in the slate must not change per-game attribution."""
        g_first_in_list = self.make_game(away_abbr="NYY", home_abbr="PHI",
                                          away_pitcher=self.make_pitcher("300"), home_pitcher=self.make_pitcher("301"))
        g_second_in_list = self.make_game(away_abbr="NYY", home_abbr="PHI",
                                           away_pitcher=self.make_pitcher("100"), home_pitcher=self.make_pitcher("101"))
        self._write("slate.json", self.make_slate([g_first_in_list, g_second_in_list]))
        self.set_batch_response("pitcherfbpct", {"100": 0.10, "101": 0.20, "300": 0.30, "301": 0.40})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.30
        assert slate["games"][1]["away"]["pitcherSavant"]["fbPct"] == 0.10


class TestMixedSuccessAcrossGames(FetchSavantPitchersHarness):

    def test_mixed_resolution_across_multiple_games(self):
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI",
                             away_pitcher=self.make_pitcher("100"), home_pitcher=self.make_pitcher("101"))
        g2 = self.make_game(away_abbr="BOS", home_abbr="TB",
                             away_pitcher=None, home_pitcher=self.make_pitcher("201"))
        self._write("slate.json", self.make_slate([g1, g2]))
        self.set_batch_response("pitcherfbpct", {"100": 0.35, "101": 0.30, "201": 0.28})

        self.run_main()
        slate = self._read_slate()
        assert slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.35
        assert slate["games"][0]["home"]["pitcherSavant"]["fbPct"] == 0.30
        assert slate["games"][1]["away"]["pitcherSavant"] == {}
        assert slate["games"][1]["home"]["pitcherSavant"]["fbPct"] == 0.28


class TestOrderingAndTopLevelPreservation(FetchSavantPitchersHarness):

    def test_game_order_and_unrelated_fields_preserved(self):
        g1 = self.make_game(away_abbr="NYY", home_abbr="PHI", extra={"kalshiKey": "NYYPHI"})
        g2 = self.make_game(away_abbr="BOS", home_abbr="TB", extra={"kalshiKey": "BOSTB"})
        slate_in = self.make_slate([g1, g2])
        slate_in["someOtherField"] = "preserved"
        self._write("slate.json", slate_in)

        self.run_main()
        slate = self._read_slate()
        assert [g["away"]["abbr"] for g in slate["games"]] == ["NYY", "BOS"]
        assert slate["someOtherField"] == "preserved"
        assert slate["games"][0]["kalshiKey"] == "NYYPHI"


class TestIdempotency(FetchSavantPitchersHarness):

    def test_rerun_with_unchanged_inputs_produces_identical_output(self):
        game = self.make_game(away_pitcher=self.make_pitcher("100"))
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"100": 0.35})

        self.run_main()
        first = self._read_slate()

        self._write("slate.json", self.make_slate([self.make_game(away_pitcher=self.make_pitcher("100"))]))
        self.run_main()
        second = self._read_slate()

        assert first["games"][0]["away"]["pitcherSavant"] == second["games"][0]["away"]["pitcherSavant"]


class TestRetryAndBackoff:
    """
    Exercises fetch_json()'s real retry/backoff loop directly (not via the
    harness's fetch_batch fake) with a mocked urllib.request.urlopen and a
    no-op, call-recording time.sleep -- no test in this class sleeps in
    real time or makes a real network call.
    """

    def setup_method(self):
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp
        self._orig_urlopen = fsp.urllib.request.urlopen
        self.sleep_calls = []
        fsp.time.sleep = lambda secs: self.sleep_calls.append(secs)

    def teardown_method(self):
        self.fsp.urllib.request.urlopen = self._orig_urlopen
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def test_transient_5xx_then_success_retries_and_recovers(self):
        import urllib.error, io
        calls = {"n": 0}

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"ok": True, "pitchers": {"100": {"fbPct": 0.3}}}).encode()

        def _fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(url="x", code=503, msg="Service Unavailable", hdrs={}, fp=io.BytesIO(b""))
            return _FakeResponse()

        self.fsp.urllib.request.urlopen = _fake_urlopen
        data, err = self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct")
        assert err is None
        assert data == {"ok": True, "pitchers": {"100": {"fbPct": 0.3}}}
        assert calls["n"] == 2, "must have retried exactly once after the transient 503"
        assert len(self.sleep_calls) == 1
        assert self.sleep_calls[0] == pytest.approx(1.5, abs=0.01), "first backoff = backoff * 2**0"

    def test_all_retries_exhausted_returns_error(self):
        import urllib.error, io

        def _always_503(req, timeout=None):
            raise urllib.error.HTTPError(url="x", code=503, msg="Service Unavailable", hdrs={}, fp=io.BytesIO(b""))

        self.fsp.urllib.request.urlopen = _always_503
        data, err = self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct", retries=2)
        assert data is None
        assert err == "HTTP 503"
        assert len(self.sleep_calls) == 2, "2 retries means 2 backoff sleeps, then give up"

    def test_permanent_4xx_error_does_not_retry(self):
        import urllib.error, io

        def _always_404(req, timeout=None):
            raise urllib.error.HTTPError(url="x", code=404, msg="Not Found", hdrs={}, fp=io.BytesIO(b""))

        self.fsp.urllib.request.urlopen = _always_404
        data, err = self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct", retries=2)
        assert data is None
        assert err == "HTTP 404"
        assert self.sleep_calls == [], "a permanent 4xx (not 429) must never retry"

    def test_429_rate_limit_retries_like_5xx(self):
        import urllib.error, io
        calls = {"n": 0}

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"ok": True, "pitchers": {}}).encode()

        def _fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(url="x", code=429, msg="Too Many Requests", hdrs={}, fp=io.BytesIO(b""))
            return _FakeResponse()

        self.fsp.urllib.request.urlopen = _fake_urlopen
        data, err = self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct")
        assert err is None
        assert calls["n"] == 2

    def test_timeout_retries_then_succeeds(self):
        calls = {"n": 0}

        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"ok": True, "pitchers": {}}).encode()

        def _fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("simulated timeout")
            return _FakeResponse()

        self.fsp.urllib.request.urlopen = _fake_urlopen
        data, err = self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct")
        assert err is None
        assert calls["n"] == 2

    def test_backoff_durations_increase_exponentially(self):
        import urllib.error, io

        def _always_503(req, timeout=None):
            raise urllib.error.HTTPError(url="x", code=503, msg="Service Unavailable", hdrs={}, fp=io.BytesIO(b""))

        self.fsp.urllib.request.urlopen = _always_503
        self.fsp.fetch_json("https://x/api/enrich?type=pitcherfbpct", retries=3, backoff=1.0)
        assert self.sleep_calls == pytest.approx([1.0, 2.0, 4.0], abs=0.01)


class TestAliasingAndIdentity:
    """
    Phase 5 Part 4: object-identity proofs (not just value equality) that
    compute_game_pitcher_savant_fields()/apply_savant_enrichment_immutable()
    never mutate their inputs and never alias caller-owned mutable state
    into their output. fetch_savant_pitchers.py has a __main__ guard, so
    importing it performs no I/O and needs no tmp-dir isolation.
    """

    def setup_method(self):
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp

    def teardown_method(self):
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def _game(self, away_ps=None, home_ps=None, away_pid="100", home_pid="200"):
        return {
            "away": {"abbr": "NYY", "pitcher": {"id": away_pid, "name": "Away Ace"},
                     "pitcherSavant": away_ps if away_ps is not None else {}},
            "home": {"abbr": "PHI", "pitcher": {"id": home_pid, "name": "Home Ace"},
                     "pitcherSavant": home_ps if home_ps is not None else {}},
        }

    def test_input_game_dict_never_mutated_by_compute_pitcher_savant_enrichment(self):
        pre_ps = {"xFIP": 3.5}
        snapshot = copy.deepcopy(pre_ps)
        new_ps = self.fsp.compute_pitcher_savant_enrichment(
            pre_ps, "100", {"100": 0.3}, {}, {}, {}
        )
        assert pre_ps == snapshot, "must never mutate its `ps` argument"
        assert new_ps is not pre_ps
        assert new_ps["fbPct"] == 0.3
        assert new_ps["xFIP"] == 3.5, "pre-existing unrelated keys must be preserved by value"

    def test_input_game_dict_never_mutated_by_compute_game_pitcher_savant_fields(self):
        pre_existing_away_ps = {"xFIP": 3.5}
        game = self._game(away_ps=pre_existing_away_ps)
        snapshot = copy.deepcopy(game)

        self.fsp.compute_game_pitcher_savant_fields(game, {"100": 0.3}, {}, {}, {})

        assert game == snapshot, "compute_game_pitcher_savant_fields must never mutate its `game` argument"
        assert game["away"]["pitcherSavant"] is pre_existing_away_ps, (
            "confirms the original nested dict object was truly untouched, not just equal-by-value"
        )

    def test_returned_pitcher_savant_is_not_same_object_as_input(self):
        pre_existing_away_ps = {"xFIP": 3.5}
        game = self._game(away_ps=pre_existing_away_ps)

        new_game, _ = self.fsp.compute_game_pitcher_savant_fields(game, {"100": 0.3}, {}, {}, {})

        assert new_game["away"]["pitcherSavant"] is not pre_existing_away_ps
        assert new_game is not game
        assert new_game["away"] is not game["away"]

    def test_mutating_returned_pitcher_savant_does_not_mutate_original(self):
        pre_existing_away_ps = {"xFIP": 3.5}
        game = self._game(away_ps=pre_existing_away_ps)

        new_game, _ = self.fsp.compute_game_pitcher_savant_fields(game, {"100": 0.3}, {}, {}, {})
        new_game["away"]["pitcherSavant"]["fbPct"] = 999

        assert "fbPct" not in pre_existing_away_ps, (
            "mutating the returned pitcherSavant dict must never leak back into the original"
        )

    def test_apply_savant_enrichment_immutable_returns_new_slate_and_game_objects(self):
        g1 = self._game()
        slate = {"date": "2026-07-27", "games": [g1]}

        new_slate, _ = self.fsp.apply_savant_enrichment_immutable(slate, {"100": 0.3}, {}, {}, {})

        assert new_slate is not slate
        assert new_slate["games"] is not slate["games"]
        assert new_slate["games"][0] is not g1
        assert g1["away"]["pitcherSavant"] == {}, "the original game dict must remain untouched"

    def test_shared_style_pitcher_savant_across_two_games_does_not_cross_contaminate(self):
        """Two games whose fixtures happen to start from equal-but-distinct pitcherSavant dicts must not interfere."""
        shared_style_ps = {"xFIP": 4.0}
        g1 = self._game(away_ps=dict(shared_style_ps), away_pid="100")
        g2 = self._game(away_ps=dict(shared_style_ps), away_pid="200")
        slate = {"date": "2026-07-27", "games": [g1, g2]}

        new_slate, _ = self.fsp.apply_savant_enrichment_immutable(
            slate, {"100": 0.11, "200": 0.22}, {}, {}, {}
        )
        new_slate["games"][0]["away"]["pitcherSavant"]["fbPct"] = -999

        assert new_slate["games"][1]["away"]["pitcherSavant"]["fbPct"] == 0.22, (
            "mutating game 1's output must not affect game 2's independently computed output"
        )

    def test_apply_savant_enrichment_immutable_is_idempotent_on_its_own_output(self):
        """
        Pre-merge hardening addition (PR #6 review, Section F). Feeding
        apply_savant_enrichment_immutable()'s own output back in as the
        next call's `slate`, with the same fetched maps, must reproduce
        byte-for-byte identical output.
        """
        game = self._game()
        slate = {"date": "2026-07-27", "games": [game]}

        once, _ = self.fsp.apply_savant_enrichment_immutable(slate, {"100": 0.3, "200": 0.4}, {}, {}, {})
        twice, _ = self.fsp.apply_savant_enrichment_immutable(once, {"100": 0.3, "200": 0.4}, {}, {}, {})

        assert once == twice
        assert once["games"][0] is not twice["games"][0]

    def test_sanitize_recent_fip_does_not_mutate_input(self):
        pre_ps = {"recentFIP": -1.5, "startsSampled": 5}
        snapshot = copy.deepcopy(pre_ps)

        new_ps = self.fsp.sanitize_recent_fip(pre_ps)

        assert pre_ps == snapshot, "sanitize_recent_fip must never mutate its `ps` argument"
        assert new_ps is not pre_ps
        assert new_ps["recentFIP"] == 0.0

    def test_sanitize_recent_fip_returns_none_when_no_change_needed(self):
        pre_ps = {"recentFIP": 3.2, "startsSampled": 5}
        assert self.fsp.sanitize_recent_fip(pre_ps) is None
        assert self.fsp.sanitize_recent_fip({"xFIP": 4.0}) is None  # no recentFIP key at all
        assert self.fsp.sanitize_recent_fip(None) is None  # not a dict


class TestPartialFailureSemantics(FetchSavantPitchersHarness):
    """Phase 5 Part 5: prior unrelated field values must survive a full main() run."""

    def test_prior_unrelated_fields_on_pitcher_savant_are_preserved(self):
        game = self.make_game(
            away_pitcher=self.make_pitcher("100"),
            away_ps={"xFIP": 3.5, "seasonFIP": 3.8, "someOtherField": "keep-me"},
        )
        self._write("slate.json", self.make_slate([game]))
        self.set_batch_response("pitcherfbpct", {"100": 0.30})

        self.run_main()
        slate = self._read_slate()
        ps = slate["games"][0]["away"]["pitcherSavant"]
        assert ps["xFIP"] == 3.5
        assert ps["seasonFIP"] == 3.8
        assert ps["someOtherField"] == "keep-me"
        assert ps["fbPct"] == 0.30, "the new enrichment fields must still be added additively"


class TestCrashBeforeWriteLeavesSlateUntouched:
    """
    fetch_savant_pitchers.py loads the whole slate, enriches, and writes
    back exactly once at the end of main(). A crash between load and
    write must leave data/slate.json exactly as it was before this run.
    Unlike fetch_lineups.py, this script's __main__ guard catches any
    such exception and exits 1 with a structured traceback -- verified
    here too, alongside the untouched-file guarantee.
    """

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def test_exception_during_apply_leaves_prior_slate_json_untouched(self):
        import fetch_savant_pitchers as fsp

        pre_run_slate = {
            "date": "2026-07-27",
            "games": [{"away": {"abbr": "NYY", "pitcher": {"id": "100"}, "pitcherSavant": {}},
                       "home": {"abbr": "PHI", "pitcher": None, "pitcherSavant": {}}}],
            "marker": "pre-run-content",
        }
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump(pre_run_slate, f)

        def _boom(*a, **k):
            raise RuntimeError("simulated crash during apply")

        fsp.fetch_batch = lambda *a, **k: {}
        fsp.apply_savant_enrichment_immutable = _boom

        with pytest.raises(RuntimeError):
            fsp.main()

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            on_disk = json.load(f)
        assert on_disk == pre_run_slate, (
            "a crash before the single end-of-main write must leave the prior "
            "slate.json completely untouched"
        )

    def test_uncaught_exception_exits_1_via_subprocess_with_structured_traceback(self):
        import subprocess
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            f.write("{not valid json")
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
        result = subprocess.run(
            [sys.executable, os.path.join(scripts_dir, "fetch_savant_pitchers.py")],
            cwd=self.tmp, capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "not valid JSON" in result.stderr


class TestAtomicWrite:
    """Phase 5 Part 8: _write_slate_atomic() must never corrupt the prior valid file on a serialization failure."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def test_serialization_failure_leaves_prior_file_untouched(self):
        prior = {"date": "2026-07-27", "games": [], "marker": "prior-good-content"}
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump(prior, f)

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            self.fsp._write_slate_atomic({"bad": Unserializable()})

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            on_disk = json.load(f)
        assert on_disk == prior
        assert os.listdir(self.data_dir) == ["slate.json"], "no stray temp file should remain"

    def test_successful_write_matches_plain_json_dump_byte_for_byte(self):
        slate = {"date": "2026-07-27", "games": [{"a": 1}, {"b": 2}]}
        self.fsp._write_slate_atomic(slate)
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            written = f.read()
        assert written == json.dumps(slate)


class TestPureFunctionsNeverTouchNetworkOrIO:
    """
    Pre-merge hardening addition (PR #6 review, Section D). Booby-traps
    fetch_json()/fetch_batch()/urlopen()/time.sleep() to raise if called,
    then proves compute_pitcher_savant_enrichment()/sanitize_recent_fip()/
    compute_game_pitcher_savant_fields()/apply_savant_enrichment_immutable()
    complete successfully anyway -- structurally incapable of reaching
    the network, not just untested against it.
    """

    def setup_method(self):
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]
        import fetch_savant_pitchers as fsp
        self.fsp = fsp

        def _boom(*a, **k):
            raise AssertionError("a pure function must never call fetch_json/fetch_batch/urlopen/sleep")

        self._orig_fetch_json = fsp.fetch_json
        self._orig_fetch_batch = fsp.fetch_batch
        self._orig_urlopen = fsp.urllib.request.urlopen
        self._orig_sleep = fsp.time.sleep
        fsp.fetch_json = _boom
        fsp.fetch_batch = _boom
        fsp.urllib.request.urlopen = _boom
        fsp.time.sleep = _boom

    def teardown_method(self):
        self.fsp.fetch_json = self._orig_fetch_json
        self.fsp.fetch_batch = self._orig_fetch_batch
        self.fsp.urllib.request.urlopen = self._orig_urlopen
        self.fsp.time.sleep = self._orig_sleep
        if "fetch_savant_pitchers" in sys.modules:
            del sys.modules["fetch_savant_pitchers"]

    def _no_file_io(self, monkeypatch):
        monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("a pure function must never open a file")
        ))

    def test_compute_pitcher_savant_enrichment_never_touches_network_or_io(self, monkeypatch):
        self._no_file_io(monkeypatch)
        new_ps = self.fsp.compute_pitcher_savant_enrichment(
            {"xFIP": 3.5}, "100", {"100": 0.3}, {}, {}, {}
        )
        assert new_ps["fbPct"] == 0.3

    def test_sanitize_recent_fip_never_touches_network_or_io(self, monkeypatch):
        self._no_file_io(monkeypatch)
        result = self.fsp.sanitize_recent_fip({"recentFIP": -1.5, "startsSampled": 5})
        assert result["recentFIP"] == 0.0

    def test_compute_game_pitcher_savant_fields_never_touches_network_or_io(self, monkeypatch):
        self._no_file_io(monkeypatch)
        game = {"away": {"abbr": "NYY", "pitcher": {"id": "100"}, "pitcherSavant": {"xFIP": 3.5}},
                "home": {"abbr": "PHI", "pitcher": None, "pitcherSavant": {}}}
        new_game, reports = self.fsp.compute_game_pitcher_savant_fields(game, {"100": 0.3}, {}, {}, {})
        assert new_game["away"]["pitcherSavant"]["fbPct"] == 0.3

    def test_apply_savant_enrichment_immutable_never_touches_network_or_io(self, monkeypatch):
        self._no_file_io(monkeypatch)
        game = {"away": {"abbr": "NYY", "pitcher": {"id": "100"}, "pitcherSavant": {"xFIP": 3.5}},
                "home": {"abbr": "PHI", "pitcher": None, "pitcherSavant": {}}}
        slate = {"date": "2026-07-27", "games": [game]}
        new_slate, reports = self.fsp.apply_savant_enrichment_immutable(slate, {"100": 0.3}, {}, {}, {})
        assert new_slate["games"][0]["away"]["pitcherSavant"]["fbPct"] == 0.3

    def test_no_module_level_mutable_response_state_leaks_between_calls(self):
        r1 = self.fsp.compute_pitcher_savant_enrichment({}, "100", {"100": 0.1}, {}, {}, {})
        r2 = self.fsp.compute_pitcher_savant_enrichment({}, "200", {"200": 0.2}, {}, {}, {})
        assert r1["fbPct"] == 0.1
        assert r2["fbPct"] == 0.2
        r1_again = self.fsp.compute_pitcher_savant_enrichment({}, "100", {"100": 0.1}, {}, {}, {})
        assert r1_again == r1
