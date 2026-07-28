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
