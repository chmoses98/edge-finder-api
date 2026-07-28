#!/usr/bin/env python3
"""
tests/test_post_fetch_gate_immutable.py
==========================================
Golden-equivalence regression suite for scripts/post_fetch_gate.py's Phase 6
pure-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish a
golden baseline, then re-run UNCHANGED after the refactor to prove
identical behavior. Unlike fetch_lineups.py/fetch_savant_pitchers.py
(Phase 5), post_fetch_gate.py v2.1 has NO main()/importable functions at
all before this phase -- the entire script is top-level module code with
no `if __name__ == '__main__':` guard. Every existing test that exercises
it (tests/test_fire_fixes.py::TestPostFetchGateQuarantine,
tests/test_clv_hardening.py::TestPostFetchGate) does so via
subprocess.run(...). This file follows that same convention for the
pre-refactor baseline (Part 3), so it stays valid even before Part 4's
refactor introduces real functions.

PRE-REFACTOR AUDIT (Phase 6 Part 2)
-------------------------------------
Invocation: exactly one workflow caller --
.github/workflows/fetch-slate.yml:234,
`python3 scripts/post_fetch_gate.py "${{ env.DATE }}"` -- confirmed by
grepping the workflow file directly, not by trusting the module
docstring's claim. Runs after fetch_savant_pitchers.py/fetch_lineups.py,
before odds fetch / Kalshi registry build / merge_odds.py / enrich_data.py.

File reads: data/slate.json (required; hard-fails if missing).

File writes:
  - data/fetch_status.json -- ALWAYS written, on both pass and fail paths,
    via write_fetch_status(). Plain json.dump(payload, f, indent=2), not
    atomic, preceded by os.makedirs("data", exist_ok=True).
  - data/slate.json -- written back ONLY if quarantined_games is
    non-empty (i.e. only when >=1 game was quarantined this run). Plain
    json.dump(slate, f) (no indent), not atomic -- the same
    truncation-on-serialization-failure risk found and fixed in
    fetch_lineups.py/fetch_savant_pitchers.py during Phase 5.
  - REAL FINDING: this write-back happens unconditionally once
    quarantined_games is non-empty, with NO dependency on whether
    `errors` is later found to be non-empty. A run that quarantines one
    game AND separately hard-fails on a different game (e.g. all-RpG-null)
    still persists the quarantine marker to data/slate.json before
    printing GATE FAILED and exiting 1. This is real, load-bearing
    partial-write-before-failure behavior that must survive the refactor
    exactly, not be "fixed" into a rollback.

Imports: stdlib only (json, sys, os, datetime/timezone/timedelta). No
project helper modules.

Environment variables: none read directly. REQUESTED_DATE comes from
sys.argv[1] -- the workflow passes ${{ env.DATE }} as a positional CLI
arg, not as an env var the script reads itself.

Date/time dependencies:
  - TODAY = datetime.now(ET).strftime(...) at module level -- fallback
    default for REQUESTED_DATE only when no CLI arg is given (never
    happens in production).
  - write_fetch_status() calls datetime.now(timezone.utc) for
    fetchedAt/failedAt -- a real wall-clock read on every invocation.
  - Per-game startTime/gameTime parsing is deterministic given input (no
    clock read); ET = timezone(timedelta(hours=-4)) is a fixed UTC-4
    offset that does NOT observe EST/EDT DST transitions -- a
    pre-existing quirk, left untouched, not this phase's concern.

Gate conditions, in execution order:
  1. slate.json missing -> hard fail, exit 1, status=FAILED_STALE_DATE.
  2. games empty/missing -> hard fail, exit 1, status=FAILED_STALE_DATE.
  3. slate['date'] missing/empty -> hard fail, exit 1,
     status=FAILED_STALE_DATE.
  4. slate['date'] != REQUESTED_DATE -> hard fail, exit 1,
     status=FAILED_STALE_DATE.
  5. Per-game startTime/gameTime (ISO8601, trailing 'Z' normalized to
     '+00:00') converted to ET; date mismatch -> hard fail, exit 1,
     IMMEDIATELY on the first offending game (remaining games never
     checked this pass). Unparseable startTime is silently tolerated
     (bare `except Exception: pass`) -- skipped, not fatal, no warning.
  6. Per-game, per-side pitcherSavant checks (skips games already
     excludedFromSlate): None -> WARN (message varies on whether
     pitcher.name is present) + tbd_starters++; present but not a dict ->
     fail() (accumulated, NOT an immediate exit); dict with
     xFIP+seasonFIP both None -> recorded in sides_with_null_fip for this
     game; xFIP None but seasonFIP present -> WARN; recentFIP present and
     negative -> WARN (cross-script invariant check against
     fetch_savant_pitchers.py v5.1's floor-to-0.0 behavior).
  7. Per-game aggregate: both sides fully missing xFIP+seasonFIP -> fail()
     (whole-game unprojectable, NOT quarantined -- a slate-level failure
     signal); exactly one side in sides_with_null_fip ->
     quarantine_game() mutates the game dict IN PLACE
     (excludedFromSlate=True, exclusionReason=...) -- the only place this
     script mutates its input -- and does NOT call fail().
  8. null_xfip_games > len(games)*0.5 -> fail() (majority-dual-null,
     on top of any already-failed per-game entries).
  9. Per-game, per-side teamStats checks (skips quarantined games): block
     missing -> WARN; lineupConfirmed is None -> WARN + counter++;
     last7RpG/last15RpG/(runsPerGame or seasonRpG) all None -> fail() +
     no_rolling_rpg++ (this counter is computed but NEVER read again
     anywhere -- pre-existing dead code, left untouched); rolling both
     None but season present -> WARN only.
  10. If quarantined_games non-empty -> rewrite data/slate.json (see the
      REAL FINDING above).
  11. errors non-empty -> print warnings then errors to stderr,
      status=FAILED_GATE, exit 1. Else -> status=OK, print GATE PASSED,
      exit 0.

Fields read: slate['games'], slate['date']; per game: away/home (each:
abbr, pitcher.name, pitcherSavant.{xFIP,seasonFIP,recentFIP,
startsSampled}), awayTeamStats/homeTeamStats (each: lineupConfirmed,
last7RpG, last15RpG, runsPerGame, seasonRpG), startTime/gameTime,
excludedFromSlate (read-only re-quarantine guard).

Fields added/modified: ONLY excludedFromSlate (bool) and exclusionReason
(str), added in place, only on quarantined games. No other slate field
is ever added, removed, or modified.

Sentinel handling: NONE. This script has no concept of sentinel
prices/odds at all -- that belongs to merge_odds.py/downstream
odds-consuming scripts. The mission's "all odds rules" checklist item has
no corresponding behavior here; post_fetch_gate.py never reads or
references odds data.

Stale-data handling: the 3-checkpoint STALE DATE mechanism (items 1-5
above) -- requested-date vs slate-date vs per-game-startTime-derived-date,
each independently checked, any mismatch is an immediate hard fail with a
"STALE SLATE ABORT"-prefixed stderr message.

Lineup rules: only lineupConfirmed is None -> WARN; no other lineup field
(lineupStatus, lineupBattersResolved, etc.) is read or gated on here.

Pitcher-data rules: see item 6 above.

Logging: print() (stdout) for informational/warning messages;
print(..., file=sys.stderr) for STALE SLATE ABORT and GATE FAILED
messages only.

Exit codes: 0 (pass, including pass-with-quarantine and pass-with-warnings),
1 (any hard fail).

Tolerated malformed inputs: pitcherSavant=None (TBD), non-dict side value
(safe_side() coerces any non-dict `away`/`home` to {} defensively --
so `away: null` or `away: "garbage"` never crashes, just falls into the
missing-field WARN paths), unparseable startTime (silently skipped),
missing awayTeamStats/homeTeamStats block.

Fatal malformed inputs: missing slate.json, empty games, missing/empty
date field, date mismatch (3 checkpoints), pitcherSavant present but not
a dict, both-sides-dual-null-fip, majority-dual-null-fip,
all-rpg-fields-null.

Partial changes before failure: YES (see REAL FINDING above) -- this is
real, load-bearing behavior to preserve exactly, not "fix."

Idempotency: a game already excludedFromSlate=True from a prior run is
skipped (`continue`) in BOTH the pitcherSavant loop and the teamStats
loop, so it is never re-evaluated or re-quarantined on a second run.
Consequently, a second run's quarantined_games / fetch_status.json
"quarantinedGames" list reflects only NEWLY-quarantined games from that
run, not the full historical set already persisted on the game dicts
themselves (those survive naturally since `slate` is loaded from disk
with the old flags already present, and never cleared). This script
never clears excludedFromSlate/exclusionReason once set -- quarantine is
a one-way latch across reruns, by construction. This is intentional,
pre-existing behavior to preserve, not a bug.

A separate, unrelated script, scripts/validate_slate_pre.py, has its own
independent stale-date-check implementation (validate_pre()) that
existing tests (tests/test_stale_date_guard.py) already treat as "the
same pattern used in post_fetch_gate logic" -- a parallel, pre-existing
duplicate-logic risk. It is out of scope for Phase 6 (not named in the
mission, and Phase 6 explicitly forbids converting validate_slate_final.py/
protect_slate.py) and is left untouched.
"""

import json
import os
import sys
import subprocess
import tempfile
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
GATE_PATH = os.path.join(SCRIPTS_DIR, "post_fetch_gate.py")
sys.path.insert(0, SCRIPTS_DIR)


class PostFetchGateHarness:
    """Shared fixture-building + subprocess-execution helper."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_slate(self, games, date="2026-07-27"):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": date, "games": games}, f)

    def _write_raw(self, filename, content):
        with open(os.path.join(self.data_dir, filename), "w") as f:
            f.write(content)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            return json.load(f)

    def _read_status(self):
        with open(os.path.join(self.data_dir, "fetch_status.json")) as f:
            return json.load(f)

    def run_gate(self, date="2026-07-27"):
        return subprocess.run(
            [sys.executable, GATE_PATH, date],
            capture_output=True, text=True, cwd=self.tmp,
        )

    def good_game(self, away="NYY", home="PHI", start_time="2026-07-27T17:05:00Z"):
        return {
            "away": {"abbr": away, "pitcher": {"name": "P1", "id": "111"},
                      "pitcherSavant": {"xFIP": 3.8, "seasonFIP": 3.9}},
            "home": {"abbr": home, "pitcher": {"name": "P2", "id": "222"},
                      "pitcherSavant": {"xFIP": 4.0, "seasonFIP": 4.1}},
            "awayTeamStats": {"lineupConfirmed": True, "last7RpG": 4.2, "last15RpG": 4.3,
                               "runsPerGame": 4.1},
            "homeTeamStats": {"lineupConfirmed": True, "last7RpG": 4.4, "last15RpG": 4.2,
                               "runsPerGame": 4.2},
            "startTime": start_time,
        }


class TestValidSlatePasses(PostFetchGateHarness):

    def test_completely_valid_slate_passes(self):
        self._write_slate([self.good_game()])
        r = self.run_gate()
        assert r.returncode == 0
        assert "GATE PASSED" in r.stdout
        status = self._read_status()
        assert status["status"] == "OK"
        assert status["quarantinedGames"] == []

    def test_header_print_still_says_v2_1_not_v2_2(self):
        """
        PR #7 pre-merge hardening finding (Section B): the module
        docstring's version header was bumped to v2.2 for this phase, but
        the `post_fetch_gate v2.1: N games loaded...` stdout line is
        observable production output, not documentation -- it is
        deliberately left unchanged at "v2.1" text to keep stdout
        byte-identical to legacy. This locks that choice in explicitly so
        a future reader doesn't "fix" it as an apparent version-bump
        oversight.
        """
        self._write_slate([self.good_game()])
        r = self.run_gate()
        assert "post_fetch_gate v2.1: 1 games loaded from slate.json" in r.stdout
        assert "post_fetch_gate v2.2" not in r.stdout


class TestMissingOrMalformedSlate(PostFetchGateHarness):

    def test_missing_slate_file_exits_1(self):
        r = self.run_gate()
        assert r.returncode == 1
        assert "not found" in r.stderr
        status = self._read_status()
        assert status["status"] == "FAILED_STALE_DATE"
        assert status["actualDate"] == "missing"

    def test_malformed_slate_json_exits_nonzero(self):
        self._write_raw("slate.json", "{not valid json")
        r = self.run_gate()
        assert r.returncode != 0

    def test_empty_games_list_exits_1(self):
        self._write_slate([])
        r = self.run_gate()
        assert r.returncode == 1
        assert "no games" in r.stderr
        status = self._read_status()
        assert status["actualDate"] == "no-games"

    def test_missing_date_field_exits_1(self):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"games": [self.good_game()]}, f)
        r = self.run_gate()
        assert r.returncode == 1
        assert "missing-date-field" in r.stdout + r.stderr or \
            self._read_status()["actualDate"] == "missing-date-field"


class TestStaleDateGuard(PostFetchGateHarness):

    def test_slate_date_mismatch_exits_1(self):
        self._write_slate([self.good_game()], date="2026-07-26")
        r = self.run_gate(date="2026-07-27")
        assert r.returncode == 1
        assert "STALE SLATE ABORT" in r.stderr
        status = self._read_status()
        assert status["status"] == "FAILED_STALE_DATE"
        assert status["actualDate"] == "2026-07-26"

    def test_game_starttime_date_mismatch_exits_1(self):
        game = self.good_game(start_time="2026-07-26T23:05:00Z")  # UTC 23:05 -> ET 19:05, still 26th
        self._write_slate([game], date="2026-07-27")
        r = self.run_gate(date="2026-07-27")
        assert r.returncode == 1
        assert "STALE SLATE ABORT" in r.stderr

    def test_unparseable_starttime_is_tolerated_not_fatal(self):
        game = self.good_game()
        game["startTime"] = "not-a-real-timestamp"
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0, f"unparseable startTime must be silently skipped\n{r.stderr}"


class TestPitcherSavantWarnPaths(PostFetchGateHarness):

    def test_null_pitcher_savant_with_pitcher_name_warns_not_fails(self):
        game = self.good_game()
        game["away"]["pitcherSavant"] = None
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "Savant data not available" in r.stdout

    def test_null_pitcher_savant_no_pitcher_name_warns_tbd(self):
        game = self.good_game()
        game["away"]["pitcher"] = None
        game["away"]["pitcherSavant"] = None
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "starter TBD" in r.stdout

    def test_xfip_null_seasonfip_present_warns(self):
        game = self.good_game()
        game["away"]["pitcherSavant"]["xFIP"] = None
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "fallback to seasonFIP" in r.stdout

    def test_negative_recent_fip_warns(self):
        game = self.good_game()
        game["away"]["pitcherSavant"]["recentFIP"] = -0.5
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "recentFIP=-0.5" in r.stdout

    def test_pitcher_savant_not_a_dict_fails(self):
        game = self.good_game()
        game["away"]["pitcherSavant"] = "garbage"
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 1
        assert "not a dict" in r.stderr


class TestDualNullFipHardFail(PostFetchGateHarness):

    def test_both_sides_null_fip_hard_fails(self):
        game = self.good_game()
        game["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        game["home"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 1
        assert "BOTH starters" in r.stderr

    def test_majority_dual_null_fip_hard_fails(self):
        bad = self.good_game("SF", "CHC")
        bad["away"]["pitcherSavant"] = {}
        bad["home"]["pitcherSavant"] = {}
        games = [bad] * 4 + [self.good_game("NYY", "BOS")]
        self._write_slate(games)
        r = self.run_gate()
        assert r.returncode == 1


class TestQuarantine(PostFetchGateHarness):

    def test_single_side_null_fip_quarantines_game_not_slate(self):
        game = self.good_game("SF", "ATL")
        game["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([self.good_game(), game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "QUARANTINE" in r.stdout
        assert "GATE PASSED" in r.stdout

    def test_quarantined_game_gets_excluded_flag_in_slate(self):
        game = self.good_game("SF", "ATL")
        game["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([self.good_game(), game])
        self.run_gate()
        slate = self._read_slate()
        sf_atl = next(g for g in slate["games"] if g["away"]["abbr"] == "SF")
        assert sf_atl["excludedFromSlate"] is True
        assert "ABNORMAL_GAME_STATUS_MISSING_PITCHER_DATA" in sf_atl["exclusionReason"]

    def test_other_games_unaffected_by_quarantine(self):
        good1 = self.good_game("NYY", "BOS")
        good2 = self.good_game("KC", "MIN")
        bad = self.good_game("SF", "ATL")
        bad["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([good1, bad, good2])
        self.run_gate()
        slate = self._read_slate()
        by_id = {f"{g['away']['abbr']}@{g['home']['abbr']}": g for g in slate["games"]}
        assert not by_id["NYY@BOS"].get("excludedFromSlate", False)
        assert not by_id["KC@MIN"].get("excludedFromSlate", False)
        assert by_id["SF@ATL"]["excludedFromSlate"] is True

    def test_fetch_status_lists_quarantined_games(self):
        bad = self.good_game("SF", "ATL")
        bad["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([self.good_game(), bad])
        self.run_gate()
        status = self._read_status()
        assert status["status"] == "OK"
        assert len(status["quarantinedGames"]) == 1
        assert status["quarantinedGames"][0]["game"] == "SF@ATL"

    def test_already_quarantined_game_is_not_reevaluated(self):
        """
        Idempotency: a game already excludedFromSlate=True is skipped by
        both scan loops on a rerun -- it does not appear in this run's
        quarantined_games again, and its flag is never cleared even if
        (hypothetically) its data looks fine on this run.
        """
        bad = self.good_game("SF", "ATL")
        bad["excludedFromSlate"] = True
        bad["exclusionReason"] = "pre-existing quarantine from a prior run"
        self._write_slate([self.good_game(), bad])
        r = self.run_gate()
        assert r.returncode == 0
        status = self._read_status()
        assert status["quarantinedGames"] == [], (
            "an already-quarantined game must not be re-listed as newly quarantined"
        )
        slate = self._read_slate()
        sf_atl = next(g for g in slate["games"] if g["away"]["abbr"] == "SF")
        assert sf_atl["excludedFromSlate"] is True
        assert sf_atl["exclusionReason"] == "pre-existing quarantine from a prior run"

    def test_quarantine_marker_persisted_to_slate_even_when_gate_later_hard_fails(self):
        """
        REAL FINDING (Part 2 audit): the slate.json write-back for
        quarantine markers has no dependency on whether `errors` is later
        found non-empty. A run that quarantines one game AND separately
        hard-fails (via a different, non-quarantined game's all-null-RpG)
        must still persist the quarantine marker before exiting 1.
        """
        quarantined = self.good_game("SF", "ATL")
        quarantined["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}

        broken = self.good_game("KC", "MIN")
        broken["awayTeamStats"] = {"last7RpG": None, "last15RpG": None,
                                     "runsPerGame": None, "seasonRpG": None}

        self._write_slate([quarantined, broken])
        r = self.run_gate()
        assert r.returncode == 1, "the broken game's all-null RpG must still hard-fail the run"
        assert "GATE FAILED" in r.stderr

        slate = self._read_slate()
        sf_atl = next(g for g in slate["games"] if g["away"]["abbr"] == "SF")
        assert sf_atl.get("excludedFromSlate") is True, (
            "the quarantine marker must be persisted to slate.json even though "
            "this same run later hard-fails and exits 1"
        )


class TestTeamStatsWarnAndFailPaths(PostFetchGateHarness):

    def test_missing_teamstats_block_warns(self):
        game = self.good_game()
        del game["awayTeamStats"]
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "teamStats block missing" in r.stdout

    def test_null_lineup_confirmed_warns(self):
        game = self.good_game()
        game["awayTeamStats"]["lineupConfirmed"] = None
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "lineupConfirmed=null" in r.stdout

    def test_rolling_rpg_null_season_present_warns_only(self):
        game = self.good_game()
        game["awayTeamStats"]["last7RpG"] = None
        game["awayTeamStats"]["last15RpG"] = None
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0
        assert "rolling R/G null" in r.stdout

    def test_all_rpg_fields_null_hard_fails(self):
        game = self.good_game()
        game["awayTeamStats"] = {"last7RpG": None, "last15RpG": None,
                                   "runsPerGame": None, "seasonRpG": None}
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 1
        assert "null" in r.stderr


class TestTwoPassOrdering(PostFetchGateHarness):
    """
    PR #7 review, Section E: direct regression tests proving the exact
    legacy two-pass sequence (ALL pitcherSavant-phase findings across all
    games, in game order, THEN ALL teamStats-phase findings across all
    games, in game order) via real subprocess runs -- checking actual
    stdout/stderr/fetch_status.json content, not just the internal
    warnings/errors list order a unit-level test could observe.

    The discriminating fixture in each test below is deliberately
    cross-cutting: game 1 has ONLY a teamStats-phase finding, game 2 has
    ONLY a pitcherSavant-phase finding. A (wrong) per-game interleaved
    implementation would emit game 1's finding before game 2's (since
    game 1 is visited first and both its checks would run together).
    The correct two-pass implementation emits game 2's pitcherSavant
    finding FIRST (pass A visits game 2 during the pitcher pass) and
    game 1's teamStats finding SECOND (pass B visits game 1 during the
    team pass) -- the opposite order, which is exactly what proves the
    two passes are real, not just two labels for one merged loop.
    """

    def test_pitcher_warning_in_game2_precedes_team_warning_in_game1(self):
        game1 = self.good_game("AAA", "BBB")
        game1["awayTeamStats"]["lineupConfirmed"] = None  # team-phase warning only

        game2 = self.good_game("CCC", "DDD")
        game2["away"]["pitcher"] = None
        game2["away"]["pitcherSavant"] = None  # pitcher-phase warning only (TBD)

        self._write_slate([game1, game2])
        r = self.run_gate()
        assert r.returncode == 0

        pitcher_warn_idx = r.stdout.index("CCC@DDD/away: pitcherSavant=null, starter TBD")
        team_warn_idx = r.stdout.index("AAA@BBB/awayTeamStats: lineupConfirmed=null")
        assert pitcher_warn_idx < team_warn_idx, (
            "game 2's pitcher-phase warning must precede game 1's team-phase warning in stdout -- "
            "proves the pitcherSavant pass runs across ALL games before the teamStats pass starts"
        )

    def test_pitcher_hard_error_in_game2_precedes_team_hard_error_in_game1(self):
        game1 = self.good_game("AAA", "BBB")
        game1["awayTeamStats"] = {"last7RpG": None, "last15RpG": None,
                                    "runsPerGame": None, "seasonRpG": None}  # team-phase hard error

        game2 = self.good_game("CCC", "DDD")
        game2["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        game2["home"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}  # pitcher-phase hard error

        self._write_slate([game1, game2])
        r = self.run_gate()
        assert r.returncode == 1

        pitcher_err_idx = r.stderr.index("CCC@DDD: BOTH starters have no xFIP/seasonFIP")
        team_err_idx = r.stderr.index(
            "AAA@BBB/awayTeamStats: last7RpG, last15RpG, AND runsPerGame all null")
        assert pitcher_err_idx < team_err_idx, (
            "game 2's pitcher-phase hard error must precede game 1's team-phase hard error in "
            "stderr -- proves the pitcherSavant pass runs across ALL games before teamStats"
        )
        status = self._read_status()
        assert status["reason"].index("CCC@DDD") < status["reason"].index("AAA@BBB"), (
            "fetch_status.json's FAILED_GATE reason must join errors in the same two-pass order"
        )

    def test_multiple_quarantines_plus_multiple_hard_errors(self):
        good = self.good_game("NYY", "BOS")
        q1 = self.good_game("SF", "ATL")
        q1["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        q2 = self.good_game("KC", "MIN")
        q2["home"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        bad1 = self.good_game("TEX", "HOU")
        bad1["away"]["pitcherSavant"] = {}
        bad1["home"]["pitcherSavant"] = {}
        bad2 = self.good_game("LAD", "SD")
        bad2["awayTeamStats"] = {"last7RpG": None, "last15RpG": None,
                                   "runsPerGame": None, "seasonRpG": None}

        self._write_slate([good, q1, q2, bad1, bad2])
        r = self.run_gate()
        assert r.returncode == 1, r.stderr
        assert "GATE FAILED" in r.stderr

        slate = self._read_slate()
        by_id = {f"{g['away']['abbr']}@{g['home']['abbr']}": g for g in slate["games"]}
        assert by_id["SF@ATL"]["excludedFromSlate"] is True
        assert by_id["KC@MIN"]["excludedFromSlate"] is True
        assert not by_id["NYY@BOS"].get("excludedFromSlate", False)
        assert not by_id["TEX@HOU"].get("excludedFromSlate", False)
        assert not by_id["LAD@SD"].get("excludedFromSlate", False)

        status = self._read_status()
        assert len(status["quarantinedGames"]) == 2, (
            "both quarantines must persist even though the run also hard-fails"
        )
        assert {q["game"] for q in status["quarantinedGames"]} == {"SF@ATL", "KC@MIN"}

    def test_errors_beyond_first_three_are_present_but_reason_only_lists_three(self):
        games = []
        for i, (away, home) in enumerate([("A1", "B1"), ("A2", "B2"), ("A3", "B3"), ("A4", "B4")]):
            g = self.good_game(away, home)
            g["away"]["pitcherSavant"] = {}
            g["home"]["pitcherSavant"] = {}
            games.append(g)

        self._write_slate(games)
        r = self.run_gate()
        assert r.returncode == 1
        for away, home in [("A1", "B1"), ("A2", "B2"), ("A3", "B3"), ("A4", "B4")]:
            assert f"{away}@{home}: BOTH starters have no xFIP/seasonFIP" in r.stderr, (
                "ALL 4 hard errors must appear in the full stderr listing, not just the first 3"
            )
        status = self._read_status()
        listed = [gid for gid in ["A1@B1", "A2@B2", "A3@B3", "A4@B4"] if gid in status["reason"]]
        assert len(listed) == 3, (
            f"fetch_status.json's FAILED_GATE reason must join only errors[:3], got {status['reason']!r}"
        )
        assert "A1@B1" in status["reason"] and "A2@B2" in status["reason"] and "A3@B3" in status["reason"]
        assert "A4@B4" not in status["reason"]

    def test_reordered_games_produce_reordered_two_pass_output(self):
        game1 = self.good_game("AAA", "BBB")
        game1["awayTeamStats"]["lineupConfirmed"] = None
        game2 = self.good_game("CCC", "DDD")
        game2["away"]["pitcher"] = None
        game2["away"]["pitcherSavant"] = None

        # Reordered: game2 (pitcher warning) now listed FIRST, game1 (team warning) SECOND.
        self._write_slate([game2, game1])
        r = self.run_gate()
        assert r.returncode == 0
        pitcher_warn_idx = r.stdout.index("CCC@DDD/away: pitcherSavant=null, starter TBD")
        team_warn_idx = r.stdout.index("AAA@BBB/awayTeamStats: lineupConfirmed=null")
        assert pitcher_warn_idx < team_warn_idx, (
            "regardless of list order, ALL pitcher-phase warnings still precede ALL "
            "team-phase warnings -- the two-pass structure, not game position, determines order"
        )


class TestGameStatusFieldsIgnored(PostFetchGateHarness):
    """
    post_fetch_gate.py never reads game['status'] at all -- confirmed by
    grepping the script. These tests lock that in: a postponed/cancelled/
    suspended/live/final game with otherwise-valid data passes exactly
    like a scheduled game.
    """

    @pytest.mark.parametrize("status", [
        "Postponed", "Cancelled", "Suspended", "In Progress", "Final", "Scheduled",
    ])
    def test_game_status_does_not_affect_gate_outcome(self, status):
        game = self.good_game()
        game["status"] = status
        self._write_slate([game])
        r = self.run_gate()
        assert r.returncode == 0, f"status={status} must not affect gate outcome\n{r.stderr}"


class TestExcludedGamePreExisting(PostFetchGateHarness):

    def test_pre_excluded_game_with_bad_data_does_not_trigger_hard_fail(self):
        excluded = self.good_game("SF", "ATL")
        excluded["excludedFromSlate"] = True
        excluded["exclusionReason"] = "prior quarantine"
        excluded["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        excluded["home"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        excluded["awayTeamStats"] = {"last7RpG": None, "last15RpG": None,
                                       "runsPerGame": None, "seasonRpG": None}
        self._write_slate([self.good_game(), excluded])
        r = self.run_gate()
        assert r.returncode == 0, (
            "an already-excluded game's bad data must not be re-evaluated or hard-fail the run"
        )


class TestDoubleheader(PostFetchGateHarness):

    def test_doubleheader_same_teams_distinct_status_independent(self):
        g1 = self.good_game("NYY", "BOS")
        g2 = self.good_game("NYY", "BOS")
        g2["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([g1, g2])
        r = self.run_gate()
        assert r.returncode == 0
        slate = self._read_slate()
        assert slate["games"][0].get("excludedFromSlate", False) is False
        assert slate["games"][1]["excludedFromSlate"] is True

    def test_reordered_doubleheader_still_attributes_correctly(self):
        g1 = self.good_game("NYY", "BOS")
        g2 = self.good_game("NYY", "BOS")
        g1["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([g1, g2])  # quarantined game listed FIRST this time
        r = self.run_gate()
        assert r.returncode == 0
        slate = self._read_slate()
        assert slate["games"][0]["excludedFromSlate"] is True
        assert slate["games"][1].get("excludedFromSlate", False) is False


class TestMixedValidityMultiGameSlate(PostFetchGateHarness):

    def test_all_games_invalid_hard_fails(self):
        bad1 = self.good_game("SF", "ATL")
        bad1["away"]["pitcherSavant"] = {}
        bad1["home"]["pitcherSavant"] = {}
        bad2 = self.good_game("KC", "MIN")
        bad2["away"]["pitcherSavant"] = {}
        bad2["home"]["pitcherSavant"] = {}
        self._write_slate([bad1, bad2])
        r = self.run_gate()
        assert r.returncode == 1

    def test_mixed_validity_only_bad_game_quarantined(self):
        good = self.good_game("NYY", "BOS")
        one_null = self.good_game("SF", "ATL")
        one_null["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        warn_only = self.good_game("KC", "MIN")
        warn_only["awayTeamStats"]["lineupConfirmed"] = None
        self._write_slate([good, one_null, warn_only])
        r = self.run_gate()
        assert r.returncode == 0
        slate = self._read_slate()
        by_id = {f"{g['away']['abbr']}@{g['home']['abbr']}": g for g in slate["games"]}
        assert not by_id["NYY@BOS"].get("excludedFromSlate", False)
        assert by_id["SF@ATL"]["excludedFromSlate"] is True
        assert not by_id["KC@MIN"].get("excludedFromSlate", False)


class TestRepeatedExecution(PostFetchGateHarness):

    def test_repeated_execution_on_valid_slate_is_stable(self):
        self._write_slate([self.good_game()])
        r1 = self.run_gate()
        slate_after_1 = self._read_slate()
        r2 = self.run_gate()
        slate_after_2 = self._read_slate()
        assert r1.returncode == r2.returncode == 0
        assert slate_after_1 == slate_after_2

    def test_repeated_execution_after_quarantine_does_not_reaccumulate(self):
        bad = self.good_game("SF", "ATL")
        bad["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        self._write_slate([self.good_game(), bad])
        self.run_gate()
        status1 = self._read_status()
        self.run_gate()
        status2 = self._read_status()
        assert len(status1["quarantinedGames"]) == 1
        assert status2["quarantinedGames"] == [], (
            "a second run must not re-list an already-quarantined game as newly quarantined"
        )


class TestWriteOnlyOccursWhenQuarantined(PostFetchGateHarness):

    def test_slate_json_mtime_unchanged_when_nothing_quarantined(self):
        self._write_slate([self.good_game()])
        slate_path = os.path.join(self.data_dir, "slate.json")
        before = os.stat(slate_path).st_mtime_ns
        import time as _t
        _t.sleep(0.01)
        self.run_gate()
        after = os.stat(slate_path).st_mtime_ns
        assert before == after, "slate.json must not be rewritten when nothing is quarantined"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 6 Part 4: object-identity, ownership, and no-I/O purity proofs for the
# refactored pure functions (evaluate_game_pitcher_savant,
# evaluate_game_team_stats, apply_post_fetch_gate_immutable,
# find_stale_slate_issue). These import scripts/post_fetch_gate.py directly
# rather than via subprocess -- safe now that the refactor added the
# `if __name__ == '__main__':` guard, so importing the module no longer
# triggers a live run as a side effect.
# ══════════════════════════════════════════════════════════════════════════════

class PostFetchGateModuleHarness:

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_dir = os.getcwd()
        os.chdir(self.tmp)
        if "post_fetch_gate" in sys.modules:
            del sys.modules["post_fetch_gate"]
        import post_fetch_gate as pfg
        self.pfg = pfg

    def teardown_method(self):
        os.chdir(self._orig_dir)
        shutil.rmtree(self.tmp, ignore_errors=True)
        if "post_fetch_gate" in sys.modules:
            del sys.modules["post_fetch_gate"]

    def good_game(self, away="NYY", home="PHI"):
        return {
            "away": {"abbr": away, "pitcher": {"name": "P1", "id": "111"},
                      "pitcherSavant": {"xFIP": 3.8, "seasonFIP": 3.9}},
            "home": {"abbr": home, "pitcher": {"name": "P2", "id": "222"},
                      "pitcherSavant": {"xFIP": 4.0, "seasonFIP": 4.1}},
            "awayTeamStats": {"lineupConfirmed": True, "last7RpG": 4.2, "last15RpG": 4.3,
                               "runsPerGame": 4.1},
            "homeTeamStats": {"lineupConfirmed": True, "last7RpG": 4.4, "last15RpG": 4.2,
                               "runsPerGame": 4.2},
            "startTime": "2026-07-27T17:05:00Z",
        }

    def one_null_game(self, away="SF", home="ATL"):
        g = self.good_game(away, home)
        g["away"]["pitcherSavant"] = {"xFIP": None, "seasonFIP": None}
        return g


class TestAliasingAndIdentity(PostFetchGateModuleHarness):

    def test_apply_does_not_mutate_input_slate_or_games(self):
        game = self.one_null_game()
        slate = {"date": "2026-07-27", "games": [game, self.good_game("KC", "MIN")]}
        import copy
        slate_before = copy.deepcopy(slate)
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert slate == slate_before, "apply_post_fetch_gate_immutable must never mutate its input slate"
        assert game.get("excludedFromSlate") is None, "the original game dict must not be mutated"

    def test_new_slate_and_games_are_new_objects(self):
        slate = {"date": "2026-07-27", "games": [self.good_game()]}
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert new_slate is not slate
        assert new_slate["games"] is not slate["games"]
        assert new_slate["games"][0] is not slate["games"][0]

    def test_quarantined_game_copy_is_independent_object(self):
        slate = {"date": "2026-07-27", "games": [self.one_null_game()]}
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert new_slate["games"][0] is not slate["games"][0]
        assert new_slate["games"][0]["excludedFromSlate"] is True
        assert "excludedFromSlate" not in slate["games"][0]

    def test_evaluate_game_pitcher_savant_does_not_mutate_game(self):
        game = self.one_null_game()
        import copy
        before = copy.deepcopy(game)
        self.pfg.evaluate_game_pitcher_savant(game)
        assert game == before

    def test_evaluate_game_team_stats_does_not_mutate_game(self):
        game = self.good_game()
        game["awayTeamStats"]["lineupConfirmed"] = None
        import copy
        before = copy.deepcopy(game)
        self.pfg.evaluate_game_team_stats(game)
        assert game == before

    def test_find_stale_slate_issue_does_not_mutate_slate(self):
        slate = {"date": "2026-07-26", "games": [self.good_game()]}
        import copy
        before = copy.deepcopy(slate)
        self.pfg.find_stale_slate_issue(slate, "2026-07-27")
        assert slate == before

    def test_apply_is_idempotent_on_its_own_output(self):
        """
        Re-feeding apply_post_fetch_gate_immutable()'s own output back in
        (with the same requested-date context implicit -- no stale-date
        recheck needed since apply_post_fetch_gate_immutable() itself
        never touches dates) reproduces byte-identical content the second
        time, since the already-quarantined game is skipped by both
        passes -- matching the legacy one-way-latch behavior -- while
        still returning independently-owned objects each call.
        """
        slate = {"date": "2026-07-27", "games": [self.one_null_game(), self.good_game("KC", "MIN")]}
        once, result1 = self.pfg.apply_post_fetch_gate_immutable(slate)
        twice, result2 = self.pfg.apply_post_fetch_gate_immutable(once)
        assert once == twice
        assert once["games"][0] is not twice["games"][0]
        assert result1["quarantined_games"] == [
            {"game": "SF@ATL", "reason": once["games"][0]["exclusionReason"]}
        ]
        assert result2["quarantined_games"] == [], (
            "the second call must not re-list an already-quarantined game as newly quarantined"
        )

    def test_mutation_after_return_does_not_affect_original_input(self):
        """
        PR #7 review, Section D: mutation-after-return, not just
        does-not-mutate-input -- mutate the RETURNED new_slate/new_games
        top-level fields after the call and confirm the ORIGINAL input is
        unaffected. This catches aliasing bugs the simpler
        "input unchanged after the call" tests above cannot: if
        new_games were somehow the *same* list/dicts as the input's
        (rather than independent copies), mutating the output here would
        also mutate the input.
        """
        game = self.good_game()
        slate = {"date": "2026-07-27", "games": [game]}
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)

        new_slate["extraTopLevelField"] = "mutated-after-return"
        new_slate["games"][0]["excludedFromSlate"] = True
        new_slate["games"][0]["exclusionReason"] = "mutated-after-return"
        new_slate["games"].append({"fabricated": True})

        assert "extraTopLevelField" not in slate
        assert "excludedFromSlate" not in slate["games"][0]
        assert "excludedFromSlate" not in game
        assert len(slate["games"]) == 1

    def test_shallow_copy_boundary_nested_dicts_are_shared_by_reference(self):
        """
        Documents the intentional shallow-copy contract (matching
        docs/IMMUTABLE_PIPELINE.md §10's pattern for every other
        conversion in this repo): apply_post_fetch_gate_immutable() only
        ever writes NEW top-level keys (excludedFromSlate/exclusionReason)
        onto its per-game copies -- it never writes into a nested block
        like awayTeamStats/pitcherSavant, so those nested dicts are safely
        shared by reference between the input and the output (a full deep
        copy would be wasted work). This is not a bug; a caller that
        mutates a nested block on the OUTPUT would also see it on the
        INPUT, which is fine precisely because this function never does
        that itself.
        """
        game = self.good_game()
        slate = {"date": "2026-07-27", "games": [game]}
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert new_slate["games"][0]["awayTeamStats"] is game["awayTeamStats"]


class TestPureFunctionsNeverTouchIO(PostFetchGateModuleHarness):
    """
    Booby-traps open()/os.makedirs()/time.sleep() to raise if called, then
    proves each pure function completes successfully anyway -- a stronger
    guarantee than merely observing no failure, since these functions are
    proven structurally incapable of reaching the filesystem/clock even if
    given the chance.
    """

    def test_evaluate_game_pitcher_savant_never_touches_io(self, monkeypatch):
        game = self.one_null_game()

        def _boom(*a, **k):
            raise AssertionError("a pure function must never open a file")
        monkeypatch.setattr("builtins.open", _boom)
        result = self.pfg.evaluate_game_pitcher_savant(game)
        assert result["quarantine_reason"] is not None

    def test_evaluate_game_team_stats_never_touches_io(self, monkeypatch):
        game = self.good_game()

        def _boom(*a, **k):
            raise AssertionError("a pure function must never open a file")
        monkeypatch.setattr("builtins.open", _boom)
        result = self.pfg.evaluate_game_team_stats(game)
        assert result["errors"] == []

    def test_apply_post_fetch_gate_immutable_never_touches_io(self, monkeypatch):
        slate = {"date": "2026-07-27", "games": [self.one_null_game(), self.good_game("KC", "MIN")]}

        def _boom(*a, **k):
            raise AssertionError("a pure function must never open a file")
        monkeypatch.setattr("builtins.open", _boom)
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert len(result["quarantined_games"]) == 1

    def test_find_stale_slate_issue_never_touches_io(self, monkeypatch):
        slate = {"date": "2026-07-26", "games": [self.good_game()]}

        def _boom(*a, **k):
            raise AssertionError("a pure function must never open a file")
        monkeypatch.setattr("builtins.open", _boom)
        issue = self.pfg.find_stale_slate_issue(slate, "2026-07-27")
        assert issue["actual"] == "2026-07-26"

    def test_no_pure_function_sleeps(self, monkeypatch):
        import time
        def _boom(*a, **k):
            raise AssertionError("a pure function must never sleep")
        monkeypatch.setattr(time, "sleep", _boom)
        slate = {"date": "2026-07-27", "games": [self.one_null_game()]}
        self.pfg.apply_post_fetch_gate_immutable(slate)
        self.pfg.find_stale_slate_issue(slate, "2026-07-27")

    def _apply_comprehensive_booby_traps(self, monkeypatch):
        """
        PR #7 review, Section D: a single combined trap covering every
        way a "pure" function could secretly reach outside its own
        arguments -- open(), write_json_atomic (the shared atomic
        writer), print(), sys.exit(), os.environ, and datetime.now()
        (the wall clock) all raise if touched. datetime.fromisoformat()/
        .astimezone() are deliberately left working, since
        find_stale_slate_issue() legitimately parses a game's own
        startTime field -- that's deterministic input parsing, not a
        clock read, and booby-trapping it would produce a false failure.
        """
        def _boom(name):
            def _raise(*a, **k):
                raise AssertionError(f"a pure function must never call {name}")
            return _raise

        monkeypatch.setattr("builtins.open", _boom("open"))
        monkeypatch.setattr("builtins.print", _boom("print"))
        monkeypatch.setattr(self.pfg, "write_json_atomic", _boom("write_json_atomic"))
        monkeypatch.setattr(self.pfg.sys, "exit", _boom("sys.exit"))

        class _NoClockDatetime(self.pfg.datetime):
            @classmethod
            def now(cls, *a, **k):
                raise AssertionError("a pure function must never read the wall clock")

        monkeypatch.setattr(self.pfg, "datetime", _NoClockDatetime)

        class _BoobyTrappedEnviron(dict):
            def __getitem__(self, key):
                raise AssertionError("a pure function must never read os.environ")
            def get(self, key, default=None):
                raise AssertionError("a pure function must never read os.environ")
        monkeypatch.setattr(self.pfg.os, "environ", _BoobyTrappedEnviron())

    def test_evaluate_game_pitcher_savant_comprehensive_booby_trap(self, monkeypatch):
        self._apply_comprehensive_booby_traps(monkeypatch)
        result = self.pfg.evaluate_game_pitcher_savant(self.one_null_game())
        assert result["quarantine_reason"] is not None

    def test_evaluate_game_team_stats_comprehensive_booby_trap(self, monkeypatch):
        self._apply_comprehensive_booby_traps(monkeypatch)
        result = self.pfg.evaluate_game_team_stats(self.good_game())
        assert result["errors"] == []

    def test_apply_post_fetch_gate_immutable_comprehensive_booby_trap(self, monkeypatch):
        self._apply_comprehensive_booby_traps(monkeypatch)
        slate = {"date": "2026-07-27", "games": [self.one_null_game(), self.good_game("KC", "MIN")]}
        new_slate, result = self.pfg.apply_post_fetch_gate_immutable(slate)
        assert len(result["quarantined_games"]) == 1

    def test_find_stale_slate_issue_comprehensive_booby_trap(self, monkeypatch):
        self._apply_comprehensive_booby_traps(monkeypatch)
        slate = {"date": "2026-07-27",
                  "games": [self.good_game(), {"away": {"abbr": "X"}, "home": {"abbr": "Y"},
                                                  "startTime": "2026-07-27T17:05:00Z"}]}
        issue = self.pfg.find_stale_slate_issue(slate, "2026-07-27")
        assert issue is None


class TestAtomicWrite(PostFetchGateModuleHarness):
    """
    Phase 6 Part 5: post_fetch_gate.py's quarantine-marker write-back to
    data/slate.json now goes through the shared lib/atomic_json helper
    (also used by fetch_lineups.py/fetch_savant_pitchers.py) instead of
    a plain open()+json.dump(). lib/atomic_json's own test suite
    (tests/test_atomic_json.py) proves the helper's general contract;
    these tests prove post_fetch_gate.py's specific call site inherits
    it correctly -- a failure here must leave the prior slate.json
    (with its previously-confirmed content) completely untouched, and
    the write must only happen at all when a quarantine occurred.
    """

    def _write_slate(self, games, date="2026-07-27"):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": date, "games": games}, f)

    def test_write_json_atomic_failure_during_quarantine_write_propagates(self):
        prior = {"date": "2026-07-27",
                  "games": [self.one_null_game()], "marker": "prior-content"}
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump(prior, f)

        real = self.pfg.write_json_atomic

        def _boom(*a, **k):
            raise OSError("simulated write failure")

        self.pfg.write_json_atomic = _boom
        sys_argv_backup = sys.argv
        sys.argv = ["post_fetch_gate.py", "2026-07-27"]
        try:
            with pytest.raises(OSError):
                self.pfg.main()
        finally:
            self.pfg.write_json_atomic = real
            sys.argv = sys_argv_backup

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            on_disk = json.load(f)
        assert on_disk == prior, "a write failure must leave the prior slate.json completely untouched"

    def test_write_only_invoked_when_quarantine_occurs(self):
        """No quarantine -> write_json_atomic must never be called for slate.json."""
        self._write_slate([self.good_game()])
        calls = []
        real = self.pfg.write_json_atomic

        def _tracking(*a, **k):
            calls.append(a)
            return real(*a, **k)

        self.pfg.write_json_atomic = _tracking
        try:
            sys_argv_backup = sys.argv
            sys.argv = ["post_fetch_gate.py", "2026-07-27"]
            try:
                with pytest.raises(SystemExit) as exc:
                    self.pfg.main()
                assert exc.value.code == 0
            finally:
                sys.argv = sys_argv_backup
        finally:
            self.pfg.write_json_atomic = real
        assert calls == [], "write_json_atomic must not be called when nothing is quarantined"

    def test_write_invoked_exactly_once_when_quarantine_occurs(self):
        self._write_slate([self.good_game(), self.one_null_game()])
        calls = []
        real = self.pfg.write_json_atomic

        def _tracking(*a, **k):
            calls.append(a[1] if len(a) > 1 else k.get('path'))
            return real(*a, **k)

        self.pfg.write_json_atomic = _tracking
        try:
            sys.argv = ["post_fetch_gate.py", "2026-07-27"]
            with pytest.raises(SystemExit) as exc:
                self.pfg.main()
            assert exc.value.code == 0
        finally:
            self.pfg.write_json_atomic = real
        assert calls == ["data/slate.json"]


class TestRequestedDateResolution(PostFetchGateModuleHarness):
    """
    PR #7 pre-merge hardening finding (Section B): the legacy script
    computed TODAY = datetime.now(ET)... unconditionally at MODULE level,
    as a separate statement executed before the
    `sys.argv[1] if len(sys.argv) > 1 else TODAY` ternary even ran -- so
    the wall clock was read on every invocation, even when argv[1] made
    its result unused. The refactored `requested_date = sys.argv[1] if
    len(sys.argv) > 1 else _today()` inside main() short-circuits: when
    argv[1] is present, `_today()` is never called at all. Confirmed
    safe: TODAY's value was never read again once argv[1] existed in the
    legacy script either (dead computation), so this changes no
    observable output -- proven directly here rather than left as an
    unverified inference.
    """

    def test_today_is_not_called_when_argv_date_is_supplied(self, monkeypatch):
        self._write_slate = lambda games, date="2026-07-27": None  # unused helper stub
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": "2026-07-27", "games": [self.good_game()]}, f)

        def _boom():
            raise AssertionError("_today() must not be called when sys.argv[1] is supplied")

        monkeypatch.setattr(self.pfg, "_today", _boom)
        sys_argv_backup = sys.argv
        sys.argv = ["post_fetch_gate.py", "2026-07-27"]
        try:
            with pytest.raises(SystemExit) as exc:
                self.pfg.main()
            assert exc.value.code == 0
        finally:
            sys.argv = sys_argv_backup

    def test_argv_date_is_used_verbatim_as_requested_date(self):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": "2026-07-27", "games": [self.good_game()]}, f)
        sys_argv_backup = sys.argv
        sys.argv = ["post_fetch_gate.py", "2026-07-27"]
        try:
            with pytest.raises(SystemExit) as exc:
                self.pfg.main()
            assert exc.value.code == 0
        finally:
            sys.argv = sys_argv_backup
        with open(os.path.join(self.data_dir, "fetch_status.json")) as f:
            status = json.load(f)
        assert status["requestedDate"] == "2026-07-27"
