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
# Section J (PR #7 review): evaluate_game() backward-compatibility deep dive
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateGameBackwardCompatibilityEdgeCases:
    """
    Audits the exact contract of evaluate_game(game, projection_context=None):

      - `is None` (identity), not truthiness, is the only recomputation
        trigger -- an empty dict `{}` is falsy but NOT None, so it is
        used as-is, not silently replaced by a fresh computation.
      - projection_context['awayProjRuns'] etc. use direct key access
        (`[...]`), not `.get(...)` -- a malformed/partial/empty context
        raises KeyError immediately rather than silently degrading.
        This propagates to main()'s existing per-game try/except
        exactly like any other evaluate_game() exception, converting to
        failed_row() for every required market -- no new exception
        class or swallowing behavior was introduced.
      - Extra keys in the context are simply never read -- harmless.
      - None values for the five keys are the NORMAL "missing
        projection data" case (matches what compute_projections()
        itself returns when data is missing), not an error.
    """

    def test_empty_dict_context_raises_keyerror_not_silently_recomputed(self):
        """
        `{}` is falsy but not None -- must NOT trigger the `is None`
        fallback recomputation. Since evaluate_game() then does direct
        `projection_context['awayProjRuns']` key access, this must raise
        KeyError, proving the empty dict was used as given, not silently
        replaced.
        """
        g = _full_priced_game()
        with pytest.raises(KeyError):
            bml.evaluate_game(g, projection_context={})

    def test_partial_dict_missing_one_key_raises_keyerror(self):
        g = _full_priced_game()
        real_ctx = bml.compute_game_projection_context(g)
        partial = dict(real_ctx)
        del partial["f5HomeProj"]
        with pytest.raises(KeyError):
            bml.evaluate_game(g, projection_context=partial)

    def test_context_with_all_none_values_is_treated_as_missing_projection(self):
        """
        All five keys present but None -- this is exactly what
        compute_game_projection_context() itself produces for a game
        with unresolvable projection data, so it must be handled
        identically to the "real" missing-data path, not raise.
        """
        g = _full_priced_game()
        none_ctx = {
            "awayProjRuns": None, "homeProjRuns": None, "totalProj": None,
            "f5AwayProj": None, "f5HomeProj": None, "missingFields": ["synthetic"],
        }
        rows = {r["market"]: r for r in bml.evaluate_game(g, projection_context=none_ctx)}
        assert rows["ML_Away"]["status"] == "Missing Data"
        assert rows["ML_Away"]["missingFields"] == ["synthetic"]

    def test_extra_keys_in_context_are_ignored(self):
        g = _full_priced_game()
        real_ctx = bml.compute_game_projection_context(g)
        extra = dict(real_ctx)
        extra["somethingNobodyReads"] = "should have zero effect"
        extra["executionDecision"] = "ACCEPTED"  # a recommendation/execution-only field, must not leak in
        rows_real = bml.evaluate_game(g, projection_context=real_ctx)
        rows_extra = bml.evaluate_game(g, projection_context=extra)
        assert rows_real == rows_extra, "extra keys in the context must have zero effect on output"

    def test_malformed_type_in_context_fails_only_the_affected_markets(self):
        """
        REAL FINDING (Section J): evaluate_game() wraps EACH market's
        computation in its own try/except (pre-existing, not touched by
        this phase) -- so a malformed projection_context value does NOT
        crash the whole evaluate_game() call. It surfaces as
        status='Evaluation Failed' with an evaluationError message on
        just the markets that actually use the bad arithmetic (ML_Away/
        ML_Home here, since p_team_wins(away_proj, home_proj) is where
        the TypeError actually occurs), while evaluate_game() as a whole
        still returns a complete row for every required market -- no new
        validation was added, and no exception is silently swallowed
        without a trace (the row's evaluationError field carries it).
        """
        g = _full_priced_game()
        bad_ctx = {
            "awayProjRuns": "not-a-number", "homeProjRuns": 4.0, "totalProj": None,
            "f5AwayProj": 2.5, "f5HomeProj": 2.0, "missingFields": [],
        }
        rows = {r["market"]: r for r in bml.evaluate_game(g, projection_context=bad_ctx)}
        assert rows["ML_Away"]["status"] == "Evaluation Failed"
        assert "TypeError" in rows["ML_Away"]["evaluationError"]
        assert rows["ML_Home"]["status"] == "Evaluation Failed"
        assert len(rows) == len(bml.REQUIRED_MARKETS), (
            "every required market must still get exactly one row even when "
            "the projection context is malformed"
        )

    def test_context_object_reused_across_multiple_games_is_safe(self):
        """
        The exact same context dict object passed to evaluate_game() for
        TWO different games must not leak game-specific state between
        them (evaluate_game() must not mutate the shared context), and
        both games must get identical projection-derived row content
        (since they share the same context) while differing only in
        whatever else differs between the two games (odds, lineup, etc.).
        """
        shared_ctx = {
            "awayProjRuns": 4.2, "homeProjRuns": 3.8, "totalProj": 8.0,
            "f5AwayProj": 2.3, "f5HomeProj": 2.1, "missingFields": [],
        }
        g1 = _full_priced_game()
        g2 = _full_priced_game(ml_away_am=-150, ml_home_am=+140)  # a distinct game, different odds
        rows1 = {r["market"]: r for r in bml.evaluate_game(g1, projection_context=shared_ctx)}
        rows2 = {r["market"]: r for r in bml.evaluate_game(g2, projection_context=shared_ctx)}
        assert shared_ctx == {
            "awayProjRuns": 4.2, "homeProjRuns": 3.8, "totalProj": 8.0,
            "f5AwayProj": 2.3, "f5HomeProj": 2.1, "missingFields": [],
        }, "the shared context object must not be mutated by either call"
        # Both games share the same underlying projection numbers, so their
        # projection-derived fields (not odds-derived ones) must agree.
        assert rows1["ML_Away"]["modelProb"] == rows2["ML_Away"]["modelProb"]

    def test_caller_mutating_context_after_return_does_not_affect_prior_result(self):
        g = _full_priced_game()
        ctx = bml.compute_game_projection_context(g)
        rows = bml.evaluate_game(g, projection_context=ctx)
        import copy
        rows_before_mutation = copy.deepcopy(rows)

        ctx["awayProjRuns"] = 999.0
        ctx["missingFields"].append("mutated-after-return")

        assert rows == rows_before_mutation, (
            "mutating the context dict after evaluate_game() has already returned must not "
            "retroactively change the already-built row objects"
        )

    def test_evaluate_game_does_not_mutate_context_during_evaluation(self):
        g = _full_priced_game()
        ctx = bml.compute_game_projection_context(g)
        import copy
        ctx_before = copy.deepcopy(ctx)
        bml.evaluate_game(g, projection_context=ctx)
        assert ctx == ctx_before

    def test_all_existing_positional_and_keyword_call_styles_remain_valid(self):
        g = _full_priced_game()
        ctx = bml.compute_game_projection_context(g)
        r1 = bml.evaluate_game(g)                                    # positional-only (legacy)
        r2 = bml.evaluate_game(g, ctx)                                # positional context
        r3 = bml.evaluate_game(g, projection_context=ctx)             # keyword context
        r4 = bml.evaluate_game(g=g, projection_context=ctx)           # all-keyword
        assert r2 == r3 == r4
        # r1 (no context, internal recomputation) must equal r3 (explicit
        # context equal to what internal recomputation would produce).
        assert r1 == r3


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

    def test_duplicate_kalshi_key_also_not_deduplicated(self):
        """Same reasoning as the duplicate-gameId case, for the kalshiKey fallback tier."""
        g1 = {"kalshiKey": "SAME"}
        g2 = {"kalshiKey": "SAME"}
        assert bml.game_projection_identity(g1, 0) == bml.game_projection_identity(g2, 1)

    def test_gameid_zero_is_falsy_falls_back_to_kalshi_key(self):
        """
        PR #7 review, Section M: game_projection_identity() uses a
        truthiness check (`if gid:`), not `is not None` -- an integer
        gameId of 0 is falsy in Python, so it is treated the SAME as a
        missing gameId and falls back to kalshiKey. Real MLB gamePks are
        never actually 0 in practice, but this is worth confirming and
        documenting explicitly rather than leaving as an unverified
        assumption, since a caller passing gameId=0 would silently NOT
        get gameId-based identity.
        """
        g = {"gameId": 0, "kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("kalshiKey", "NYYPHI")

    def test_gameid_empty_string_falls_back_to_kalshi_key(self):
        g = {"gameId": "", "kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("kalshiKey", "NYYPHI")

    def test_gameid_none_falls_back_to_kalshi_key(self):
        g = {"gameId": None, "kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("kalshiKey", "NYYPHI")

    def test_gameid_malformed_type_list_is_still_used_if_truthy(self):
        """
        game_projection_identity() does no type validation on gameId --
        a non-empty list is truthy and would be accepted and returned
        as-is (an unusual identity value, but not a crash). This
        documents that no new validation was introduced; the function
        never claimed to sanitize its inputs' types.
        """
        g = {"gameId": [1, 2], "kalshiKey": "NYYPHI"}
        assert bml.game_projection_identity(g, 0) == ("gameId", [1, 2])

    def test_kalshikey_empty_string_falls_back_to_index(self):
        g = {"gameId": None, "kalshiKey": ""}
        assert bml.game_projection_identity(g, 7) == ("index", 7)

    def test_kalshikey_none_falls_back_to_index(self):
        g = {"gameId": None, "kalshiKey": None}
        assert bml.game_projection_identity(g, 2) == ("index", 2)

    def test_deterministic_across_repeated_calls(self):
        g = {"gameId": "12345", "kalshiKey": "NYYPHI"}
        results = [bml.game_projection_identity(g, 0) for _ in range(5)]
        assert len(set(results)) == 1

    def test_identity_function_is_currently_orphaned_not_wired_into_main(self):
        """
        REAL FINDING (PR #7 review, Section M): game_projection_identity()
        is NOT called anywhere in main() at all -- the `gameId` field
        added to each projections.json record comes from a separate,
        direct `_g.get('gameId')` call, completely independent of this
        function's gameId>kalshiKey>index preference logic. An earlier
        docstring draft incorrectly claimed this function was used for
        that labeling; both the docstring and this test have been
        corrected to state the actual, current status: a standalone,
        independently tested policy function for a future phase's
        potential use (e.g. a keyed disk-artifact lookup), not yet
        wired into any call site. Whether or not it's ever called, the
        actual projection-to-evaluate_game() wiring in main() is --
        and must remain -- positional (zip over `games`/`game_contexts`,
        same order, single pass), so this function's output can never
        influence which projection a game's recommendation is built
        from, wired in or not.
        """
        import inspect
        main_src = inspect.getsource(bml.main)
        assert "game_projection_identity(" not in main_src, (
            "if this ever changes (the function gets wired in), update this test "
            "and the function's docstring together -- don't let them silently diverge again"
        )


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
# Section K (PR #7 review): one-projection-per-game, proven with object
# tokens rather than only equality/counting.
# ══════════════════════════════════════════════════════════════════════════════

class TestOneProjectionPerGameTokenProof(MainRunHarness):
    """
    Tags each compute_game_projection_context() return value with a
    unique, otherwise-inert marker key (`_token`, a fresh object() per
    call -- never JSON-serialized, stripped before the real artifact
    write) and proves, by object IDENTITY, that the exact same context
    object reaches evaluate_game() that was produced for that game --
    not just an equal-by-value copy. A spy that only checked equality
    could pass even if a second, coincidentally-identical computation
    happened; identity cannot.
    """

    def _install_token_spy(self, monkeypatch):
        tokens_by_call = []
        real_compute = bml.compute_game_projection_context

        def _tagging_compute(g):
            ctx = real_compute(g)
            token = object()
            tokens_by_call.append(token)
            ctx = dict(ctx)
            ctx["_token"] = token
            return ctx

        contexts_seen_by_evaluate = []
        real_evaluate = bml.evaluate_game

        def _capturing_evaluate(g, projection_context=None):
            contexts_seen_by_evaluate.append(projection_context)
            # Strip the test-only _token key before calling the real
            # function -- it has no meaning to production code.
            clean_ctx = {k: v for k, v in projection_context.items() if k != "_token"}
            return real_evaluate(g, projection_context=clean_ctx)

        monkeypatch.setattr(bml, "compute_game_projection_context", _tagging_compute)
        monkeypatch.setattr(bml, "evaluate_game", _capturing_evaluate)
        return tokens_by_call, contexts_seen_by_evaluate

    def _run_scenario(self, monkeypatch, games):
        tokens_by_call, contexts_seen = self._install_token_spy(monkeypatch)
        self._write_slate(games)
        bml.main()
        return tokens_by_call, contexts_seen

    def test_one_game_token_identity(self, monkeypatch):
        tokens, seen = self._run_scenario(monkeypatch, [_full_priced_game()])
        assert len(tokens) == 1
        assert seen[0]["_token"] is tokens[0]

    def test_multiple_games_each_gets_its_own_token_in_order(self, monkeypatch):
        games = [_full_priced_game(), _full_priced_game(ml_away_am=-150)]
        tokens, seen = self._run_scenario(monkeypatch, games)
        assert len(tokens) == 2
        assert tokens[0] is not tokens[1]
        assert [s["_token"] for s in seen] == tokens, (
            "each game's evaluate_game() call must receive exactly the token "
            "produced for THAT game, in the same order"
        )

    def test_reordered_games_still_get_correctly_paired_tokens(self, monkeypatch):
        gA = _full_priced_game(ml_away_am=-130)
        gB = _full_priced_game(ml_away_am=-999)
        tokens, seen = self._run_scenario(monkeypatch, [gB, gA])
        assert [s["_token"] for s in seen] == tokens

    def test_excluded_game_gets_a_token_computed_but_never_sees_evaluate_game(self, monkeypatch):
        excluded = _full_priced_game()
        excluded["excludedFromSlate"] = True
        excluded["exclusionReason"] = "test fixture"
        tokens, seen = self._run_scenario(monkeypatch, [excluded, _full_priced_game()])
        # main() still computes a projection context for the excluded game
        # (compute_game_projection_context has no excludedFromSlate
        # awareness -- see test_projections_computed_even_for_excluded_game),
        # but only the non-excluded game's token reaches evaluate_game().
        assert len(tokens) == 2
        assert len(seen) == 1
        assert seen[0]["_token"] is tokens[1]

    @pytest.mark.parametrize("status", ["Postponed", "In Progress", "Final", "Scheduled"])
    def test_game_status_does_not_affect_token_pairing(self, monkeypatch, status):
        g = _full_priced_game()
        g["status"] = status
        tokens, seen = self._run_scenario(monkeypatch, [g])
        assert seen[0]["_token"] is tokens[0]

    def test_evaluation_exception_for_one_game_does_not_trigger_recomputation_for_another(self, monkeypatch):
        """
        Game 1 raises inside evaluate_game() (caught by main()'s own
        per-game try/except, converted to failed_row() for every
        market); game 2 must still get exactly one projection
        computation of its own -- game 1's failure must not cause a
        retry/recompute for game 2, and game 2's token must still reach
        its own evaluate_game() call untouched.
        """
        tokens_by_call, contexts_seen = self._install_token_spy(monkeypatch)
        real_evaluate = bml.evaluate_game

        def _boom_on_first_game(g, projection_context=None):
            if g.get("_marker") == "boom":
                raise RuntimeError("simulated evaluation exception")
            clean_ctx = {k: v for k, v in projection_context.items() if k != "_token"}
            return real_evaluate(g, projection_context=clean_ctx)

        monkeypatch.setattr(bml, "evaluate_game", _boom_on_first_game)

        g1 = _full_priced_game()
        g1["_marker"] = "boom"
        g2 = _full_priced_game(ml_away_am=-150)
        self._write_slate([g1, g2])
        bml.main()  # must not raise -- main()'s own try/except catches it

        assert len(tokens_by_call) == 2, (
            "exactly one projection computation per game, regardless of game 1's failure"
        )
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            slate = json.load(f)
        assert all(row["status"] == "Evaluation Failed" for row in slate["games"][0]["marketLedger"])
        assert any(row["status"] != "Evaluation Failed" for row in slate["games"][1]["marketLedger"]), (
            "game 2 must evaluate normally despite game 1's exception"
        )

    def test_artifact_write_exception_does_not_trigger_recomputation(self, monkeypatch):
        tokens_by_call, contexts_seen = self._install_token_spy(monkeypatch)

        def _boom(*a, **k):
            raise RuntimeError("simulated artifact write failure")

        monkeypatch.setattr(pa, "write_stage_artifact", _boom)
        self._write_slate([_full_priced_game()])
        bml.main()  # must not raise -- artifact write is best-effort

        assert len(tokens_by_call) == 1, (
            "the artifact-write failure must not cause a second projection computation"
        )
        assert contexts_seen[0]["_token"] is tokens_by_call[0]


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
