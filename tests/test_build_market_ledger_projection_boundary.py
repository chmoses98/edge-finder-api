#!/usr/bin/env python3
"""
tests/test_build_market_ledger_projection_boundary.py
=========================================================
Phase 6 Parts 6-13: golden-equivalence and new-behavior tests for
scripts/build_market_ledger.py's projection/recommendation boundary.

DEPENDENCY MAP (Phase 6 Part 6)
---------------------------------
1. slate loading: main() reads data/slate.json once, at the top.
2. projection computation: BEFORE this phase, compute_projections(g) was
   called from TWO independent sites on the same game object -- once in
   main()'s projections.json-building loop, once again inside
   evaluate_game() itself. Both calls always agreed (compute_projections
   is pure; nothing mutates a game's projection-input fields between the
   two calls -- verified by grepping evaluate_game() for any `g[...] =`
   assignment before its own compute_projections(g) call: none exist),
   but were not structurally guaranteed to. This phase computes each
   game's projection context exactly ONCE (compute_game_projection_context,
   called from a single list comprehension in main(), BEFORE either
   consumer), making the guarantee exact by construction instead of
   incidental.
3. projection artifact publication: main() writes
   data/pipeline/<date>/projections.json from that same game_contexts
   list -- see lib/pipeline_artifacts.write_stage_artifact(). Best-effort:
   wrapped in try/except, a failure here only prints a WARNING and never
   raises.
4. per-game recommendation evaluation: evaluate_game(g, projection_context)
   consumes the SAME game_contexts[i] entry that fed projections.json,
   passed explicitly by main(). Direct callers that omit
   projection_context (all of tests/test_lineup_gate.py,
   tests/test_rule40_rfi_gate.py, tests/test_bet_eligibility.py, and any
   future one) still work unchanged -- evaluate_game() falls back to
   computing it internally via compute_game_projection_context(g) itself,
   exactly as it always implicitly did.
5. marketLedger construction: unchanged -- evaluate_game() still returns
   one row per REQUIRED_MARKETS entry; main() still validates completeness
   and appends failed_row() for anything missing.
6. recommendation artifact publication: unchanged -- main() still
   publishes data/pipeline/<date>/recommendations.json (transitional,
   full-slate snapshot) independently, also best-effort.
7. slate write: unchanged -- one plain (non-atomic; out of scope for
   Phase 6, which only touches post_fetch_gate.py's/fetch_lineups.py's/
   fetch_savant_pitchers.py's atomic writes) json.dump(slate, f) at the
   end of main().
8. error handling: per-game evaluate_game() exceptions are caught in
   main()'s own try/except (unchanged) and converted to failed_row() for
   every required market -- this pre-dates and is untouched by this
   phase; a compute_game_projection_context(g) exception during the new
   game_contexts list comprehension would propagate uncaught (matching
   the pre-existing behavior of both original compute_projections(g)
   call sites, neither of which was ever guarded either).

compute_projections(g) call sites: exactly ONE now, inside
compute_game_projection_context(g), called from exactly ONE place in the
normal path: main()'s `game_contexts = [compute_game_projection_context(g)
for g in games]`. evaluate_game(g) calls it a second, independent time
ONLY when called directly with no projection_context (the
backward-compatible fallback path for direct callers).

evaluate_game() fields read from `g`: unchanged by this phase -- see the
function body (odds.kalshi, pinnacleVF, away/home.pitcherSavant,
awayTeamStats/homeTeamStats, kalshiKey, kalshiGameTime, oddsApiCommenceTime,
kalshiSnapshotTs/snapshot_ts, park -- via compute_projections internally).

evaluate_game() projection values consumed: awayProjRuns, homeProjRuns,
totalProj, f5AwayProj, f5HomeProj, missingFields -- all now sourced from
the passed-in (or internally-computed-as-fallback) projection_context
dict, in the same dict shape data/pipeline/<date>/projections.json's
per-game records already used before this phase.

evaluate_game() recommendation fields produced: unchanged -- marketTicker,
status (Accepted/Rejected/Missing Data/Evaluation Failed), reason,
modelProb, edge %, confidence, gatesFired, etc., built by make_row()/
accepted_row()/rejected_row()/missing_row()/failed_row(), none of which
changed in this phase.

Hidden dependencies audited: no module-level mutable state, no config
file, no environment variable read by compute_projections()/
evaluate_game()/compute_game_projection_context(); no wall-clock
dependency in the projection math itself (only make_row()'s downstream
edge-calculation call sites read snapshot_ts, which is a slate field, not
a live clock read).

missing_row()/failed_row() paths: unchanged -- neither ever receives
**proj_context (pre-existing, confirmed by grep), so their own
awayProjRuns/etc. fields are always None regardless of what the
projection context actually contains. This phase does not add projection
fields to those rows (that would be an output change, forbidden by the
mission).

Callers of evaluate_game(): scripts/build_market_ledger.py's own main()
(now passes projection_context explicitly) plus three existing test
files that call it directly with a single argument (test_lineup_gate.py,
test_rule40_rfi_gate.py, test_bet_eligibility.py) -- all three re-run
unchanged in this file's own test run to confirm zero regression.
"""

import json
import os
import sys
import shutil
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, SCRIPTS_DIR)

import build_market_ledger as bml
from test_lineup_gate import _make_game as _full_priced_game
import pipeline_artifacts as pa  # noqa: E402 -- matches build_market_ledger.py's own
                                   # `from pipeline_artifacts import ...` import path
                                   # exactly, so monkeypatching pa.write_stage_artifact
                                   # here patches the SAME module object bml uses at
                                   # runtime (a bare `import lib.pipeline_artifacts`
                                   # would cache under a different sys.modules key and
                                   # silently patch an unrelated module instance).


def _fully_computable_game(away="NYY", home="PHI"):
    return {
        "away": {"abbr": away, "pitcherSavant": {"xFIP": 3.5}, "bullpen": {}},
        "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.2}, "bullpen": {}},
        "awayTeamStats": {"offenseBaselineAdj": 4.8},
        "homeTeamStats": {"offenseBaselineAdj": 4.1},
        "park": {"parkFactor": 105},
        "odds": {"kalshi": {}},
    }


def _partially_missing_game(away="BOS", home="TB"):
    return {
        "away": {"abbr": away},
        "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0}, "bullpen": {}},
        "awayTeamStats": {},
        "homeTeamStats": {"offenseBaselineAdj": 4.1},
        "odds": {"kalshi": {}},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Part 10: evaluate_game()'s transitional projection_context adapter
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateGameProjectionContextAdapter:

    def test_explicit_context_matches_implicit_computation(self):
        """
        evaluate_game(g, ctx) with ctx == compute_game_projection_context(g)
        must produce byte-identical rows to evaluate_game(g) with no
        context at all (the pre-Phase-6 behavior every existing direct
        caller still relies on).
        """
        g = _fully_computable_game()
        ctx = bml.compute_game_projection_context(g)
        rows_implicit = bml.evaluate_game(g)
        rows_explicit = bml.evaluate_game(g, projection_context=ctx)
        assert rows_implicit == rows_explicit

    def test_explicit_context_matches_implicit_computation_missing_data(self):
        g = _partially_missing_game()
        ctx = bml.compute_game_projection_context(g)
        rows_implicit = bml.evaluate_game(g)
        rows_explicit = bml.evaluate_game(g, projection_context=ctx)
        assert rows_implicit == rows_explicit

    def test_context_is_not_mutated_by_evaluate_game(self):
        g = _fully_computable_game()
        ctx = bml.compute_game_projection_context(g)
        import copy
        ctx_before = copy.deepcopy(ctx)
        bml.evaluate_game(g, projection_context=ctx)
        assert ctx == ctx_before

    def test_game_dict_is_not_mutated_beyond_legacy_behavior(self):
        """
        evaluate_game() has never mutated its `g` argument (it only reads
        from it and builds new row dicts) -- confirmed still true whether
        or not an explicit projection_context is supplied.
        """
        g = _fully_computable_game()
        import copy
        g_before = copy.deepcopy(g)
        ctx = bml.compute_game_projection_context(g)
        bml.evaluate_game(g, projection_context=ctx)
        assert g == g_before

    def test_a_wrong_context_actually_changes_output(self):
        """
        Sanity check that projection_context is actually being consumed,
        not silently ignored -- feeding in a deliberately different
        context must change the ML_Away/ML_Home rows' projection-derived
        content relative to the game's own true projection.
        """
        g = _full_priced_game()  # has real kalshi ml/nrfi_yrfi/f5ml/team_totals prices
        real_ctx = bml.compute_game_projection_context(g)
        fake_ctx = dict(real_ctx)
        fake_ctx["awayProjRuns"] = (real_ctx["awayProjRuns"] or 4.0) + 1.5
        fake_ctx["homeProjRuns"] = real_ctx["homeProjRuns"]
        fake_ctx["totalProj"] = round(fake_ctx["awayProjRuns"] + fake_ctx["homeProjRuns"], 3)

        rows_real = {r["market"]: r for r in bml.evaluate_game(g, projection_context=real_ctx)}
        rows_fake = {r["market"]: r for r in bml.evaluate_game(g, projection_context=fake_ctx)}
        assert rows_real["ML_Away"] != rows_fake["ML_Away"], (
            "projection_context must actually drive row computation, not be a no-op parameter"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Part 6/7: compute_game_projection_context() purity and shape
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeGameProjectionContext:

    def test_matches_compute_projections_tuple_exactly(self):
        g = _fully_computable_game()
        away, home, f5a, f5h, missing = bml.compute_projections(g)
        ctx = bml.compute_game_projection_context(g)
        assert ctx["awayProjRuns"] == away
        assert ctx["homeProjRuns"] == home
        assert ctx["f5AwayProj"] == f5a
        assert ctx["f5HomeProj"] == f5h
        assert ctx["missingFields"] == missing
        assert ctx["totalProj"] == round(away + home, 3)

    def test_missing_data_produces_all_none_context(self):
        g = _partially_missing_game()
        ctx = bml.compute_game_projection_context(g)
        assert ctx["awayProjRuns"] is None
        assert ctx["homeProjRuns"] is None
        assert ctx["totalProj"] is None
        assert ctx["missingFields"] != []

    def test_does_not_mutate_input_game(self):
        g = _fully_computable_game()
        import copy
        before = copy.deepcopy(g)
        bml.compute_game_projection_context(g)
        assert g == before

    def test_never_touches_io(self, monkeypatch):
        g = _fully_computable_game()

        def _boom(*a, **k):
            raise AssertionError("compute_game_projection_context must never open a file")
        monkeypatch.setattr("builtins.open", _boom)
        ctx = bml.compute_game_projection_context(g)
        assert ctx["awayProjRuns"] is not None

    def test_deterministic_repeated_calls(self):
        g = _fully_computable_game()
        ctx1 = bml.compute_game_projection_context(g)
        ctx2 = bml.compute_game_projection_context(g)
        assert ctx1 == ctx2
        assert ctx1 is not ctx2


# ══════════════════════════════════════════════════════════════════════════════
# Part 9: projection identity policy
# ══════════════════════════════════════════════════════════════════════════════

class TestGameProjectionIdentity:

    def test_prefers_game_id_when_present(self):
        g = {"gameId": "12345", "kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("gameId", "12345")

    def test_falls_back_to_kalshi_key_when_gameid_missing(self):
        g = {"kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("kalshiKey", "NYYPHI")

    def test_falls_back_to_index_when_neither_present(self):
        g = {}
        assert bml.game_projection_identity(g, 3) == ("index", 3)

    def test_doubleheader_same_kalshi_key_distinct_game_id(self):
        """
        The exact doubleheader shape that made kalshiKey ambiguous in
        merge_odds.py (Phase 4): two games sharing a team-based key but
        with their own distinct gameId. The identity helper must produce
        two DISTINCT identities here, since gameId is preferred.
        """
        g1 = {"gameId": "111", "kalshiKey": "NYYBOS"}
        g2 = {"gameId": "222", "kalshiKey": "NYYBOS"}
        id1 = bml.game_projection_identity(g1, 0)
        id2 = bml.game_projection_identity(g2, 1)
        assert id1 != id2
        assert id1 == ("gameId", "111")
        assert id2 == ("gameId", "222")

    def test_reordered_games_still_produce_stable_per_game_identity(self):
        g1 = {"gameId": "111", "kalshiKey": "NYYBOS"}
        g2 = {"gameId": "222", "kalshiKey": "NYYBOS"}
        # identity does not depend on order, only on the game's own fields
        assert bml.game_projection_identity(g1, 5) == bml.game_projection_identity(g1, 0)
        assert bml.game_projection_identity(g2, 5) == bml.game_projection_identity(g2, 0)

    def test_missing_game_id_falls_back_for_that_game_only(self):
        g1 = {"gameId": "111"}
        g2 = {"kalshiKey": "NYYBOS"}  # no gameId at all
        assert bml.game_projection_identity(g1, 0) == ("gameId", "111")
        assert bml.game_projection_identity(g2, 1) == ("kalshiKey", "NYYBOS")

    def test_duplicate_game_id_is_not_deduplicated_by_this_function(self):
        """
        Documents current, intentional behavior: this is a pure per-game
        identity SELECTOR, not a collision-detecting registry. Two games
        that pathologically share the same gameId produce the SAME
        identity tuple -- Phase 6 does not redesign global identity or
        attempt to detect/repair this (out of scope; see the mission's
        explicit "do not fix the kalshiKey doubleheader issue" and "do
        not redesign global game identity" constraints). The actual
        projections-to-evaluate_game() wiring in main() never performs a
        keyed lookup at all (it is purely positional -- see
        game_contexts' construction), so this hypothetical collision
        cannot actually cause cross-game contamination in the current
        pipeline; this identity value is informational
        (projections.json's own record labeling) only.
        """
        g1 = {"gameId": "999", "kalshiKey": "AAABBB"}
        g2 = {"gameId": "999", "kalshiKey": "CCCDDD"}
        assert bml.game_projection_identity(g1, 0) == bml.game_projection_identity(g2, 1)


# ══════════════════════════════════════════════════════════════════════════════
# Part 12: projections written == projections used (structural proof)
# ══════════════════════════════════════════════════════════════════════════════

class MainRunHarness:

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "scripts"))
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        self._orig_file = bml.__file__
        bml.__file__ = os.path.join(self.tmp, "scripts", "build_market_ledger.py")
        self._orig_root = pa.PIPELINE_ROOT
        pa.PIPELINE_ROOT = os.path.join(self.tmp, "data", "pipeline")

    def teardown_method(self):
        bml.__file__ = self._orig_file
        pa.PIPELINE_ROOT = self._orig_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_slate(self, games, date="2026-07-27"):
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": date, "games": games}, f)


class TestProjectionsWrittenEqualsProjectionsUsed(MainRunHarness):

    def test_single_call_per_game_structurally_guaranteed(self, monkeypatch):
        """
        Directly proves the Part 7 requirement: compute_projections() is
        called exactly ONCE per game in main()'s normal path (inside
        compute_game_projection_context(), inside the single
        game_contexts list comprehension) -- not once for the artifact
        and again inside evaluate_game().
        """
        self._write_slate([_fully_computable_game(), _partially_missing_game()])
        call_count = {"n": 0}
        real = bml.compute_projections

        def _counting(*a, **k):
            call_count["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(bml, "compute_projections", _counting)
        bml.main()
        assert call_count["n"] == 2, (
            f"expected exactly 1 compute_projections() call per game (2 games), got {call_count['n']}"
        )

    def test_artifact_and_recommendation_rows_derive_from_the_same_object(self, monkeypatch):
        self._write_slate([_fully_computable_game(), _partially_missing_game()])

        contexts_seen = []
        real_evaluate = bml.evaluate_game

        def _capturing_evaluate(g, projection_context=None):
            contexts_seen.append(projection_context)
            return real_evaluate(g, projection_context=projection_context)

        monkeypatch.setattr(bml, "evaluate_game", _capturing_evaluate)
        bml.main()

        proj_games = pa.read_stage_artifact("projections", "2026-07-27")["data"]["games"]
        # Both games reach evaluate_game() -- neither is excludedFromSlate,
        # only "quarantined" games are skipped by main()'s loop; the second
        # game merely has missing projection data (its own separate case).
        assert len(contexts_seen) == 2
        for seen, proj in zip(contexts_seen, proj_games):
            assert seen["awayProjRuns"] == proj["awayProjRuns"]
            assert seen["homeProjRuns"] == proj["homeProjRuns"]
            assert seen["f5AwayProj"] == proj["f5AwayProj"]
            assert seen["f5HomeProj"] == proj["f5HomeProj"]
            assert seen["totalProj"] == proj["totalProj"]


# ══════════════════════════════════════════════════════════════════════════════
# Part 13: failure isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestFailureIsolation(MainRunHarness):

    def test_projections_artifact_write_failure_does_not_alter_recommendations(self, monkeypatch):
        """
        Even if the projections.json artifact write raises, the SAME
        in-memory game_contexts list must still power evaluate_game() --
        marketLedger rows must be identical to a run where the artifact
        write succeeds.
        """
        game = _fully_computable_game()

        # Baseline run: artifact write succeeds.
        self._write_slate([game])
        bml.main()
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            baseline_ledger = json.load(f)["games"][0]["marketLedger"]

        # Second run: artifact write forced to fail.
        self._write_slate([game])
        real_write = pa.write_stage_artifact

        def _fail_projections(stage, *a, **k):
            if stage == "projections":
                raise RuntimeError("simulated artifact backend failure")
            return real_write(stage, *a, **k)

        monkeypatch.setattr(pa, "write_stage_artifact", _fail_projections)
        bml.main()  # must not raise
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            failed_ledger = json.load(f)["games"][0]["marketLedger"]

        assert failed_ledger == baseline_ledger, (
            "a projections-artifact write failure must not change recommendation output at all"
        )

    def test_slate_json_not_corrupted_when_artifact_write_fails(self, monkeypatch):
        self._write_slate([_fully_computable_game()])
        real_write = pa.write_stage_artifact

        def _fail_projections(stage, *a, **k):
            if stage == "projections":
                raise RuntimeError("simulated failure")
            return real_write(stage, *a, **k)

        monkeypatch.setattr(pa, "write_stage_artifact", _fail_projections)
        bml.main()

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            slate = json.load(f)
        assert slate["games"][0]["marketLedger"]

    def test_bets_json_and_authoritative_untouched_when_artifact_write_fails(self, monkeypatch):
        bets_path = os.path.join(self.data_dir, "bets.json")
        with open(bets_path, "w") as f:
            json.dump({"bets": ["untouched"]}, f)
        auth_dir = os.path.join(self.data_dir, "slates", "2026-07-27")
        os.makedirs(auth_dir)
        auth_path = os.path.join(auth_dir, "authoritative.json")
        with open(auth_path, "w") as f:
            json.dump({"authoritative": "untouched"}, f)

        self._write_slate([_fully_computable_game()])
        real_write = pa.write_stage_artifact

        def _fail_projections(stage, *a, **k):
            if stage == "projections":
                raise RuntimeError("simulated failure")
            return real_write(stage, *a, **k)

        monkeypatch.setattr(pa, "write_stage_artifact", _fail_projections)
        bml.main()

        with open(bets_path) as f:
            assert json.load(f) == {"bets": ["untouched"]}
        with open(auth_path) as f:
            assert json.load(f) == {"authoritative": "untouched"}

    def test_recommendations_artifact_still_written_when_projections_artifact_fails(self, monkeypatch):
        self._write_slate([_fully_computable_game()])
        real_write = pa.write_stage_artifact

        def _fail_projections(stage, *a, **k):
            if stage == "projections":
                raise RuntimeError("simulated failure")
            return real_write(stage, *a, **k)

        monkeypatch.setattr(pa, "write_stage_artifact", _fail_projections)
        bml.main()

        assert not pa.stage_artifact_exists("projections", "2026-07-27")
        assert pa.stage_artifact_exists("recommendations", "2026-07-27")

    def test_game_contexts_computed_even_if_projections_artifact_directory_uncreatable(self, monkeypatch):
        """
        A directory-creation failure inside write_stage_artifact() (e.g.
        os.makedirs raising) must be caught by main()'s existing
        try/except around the artifact-publication block -- it must
        never reach the game_contexts computation (which already
        happened, before this block, per Part 7) or the evaluate_game()
        loop after it.
        """
        self._write_slate([_fully_computable_game()])

        def _boom_makedirs(*a, **k):
            raise OSError("simulated permission denied creating pipeline dir")

        monkeypatch.setattr(pa.os, "makedirs", _boom_makedirs)
        bml.main()  # must not raise

        with open(os.path.join(self.data_dir, "slate.json")) as f:
            slate = json.load(f)
        assert slate["games"][0]["marketLedger"]
