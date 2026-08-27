import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (
    _ROOT,
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "scripts", "edgelab"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_edge_persistence_experiment as exp  # noqa: E402
import run_edge_monotonicity_experiment as rsch0001  # noqa: E402


def _row(ticker, checkpoint, captured_at, edge, game_id="G1", game_date="2026-08-01", is_closing=False,
         executable_yes_price=0.5, settlement_result="YES", family="game_result", full_universe_move=None):
    return {
        "marketTicker": ticker, "researchCheckpoint": checkpoint, "capturedAt": captured_at,
        "contemporaneousEdge": edge, "gameId": game_id, "gameDate": game_date, "isClosingQuote": is_closing,
        "executableYesPrice": executable_yes_price, "settlementResult": settlement_result,
        "settlementStatus": "SETTLED", "canonicalMarketFamily": family, "modelEvaluationAvailable": True,
        "modelFairProbability": executable_yes_price + edge if edge is not None else None,
        "fairMarketProbability": executable_yes_price, "modelEvaluationId": f"EVAL-{ticker}-{checkpoint}",
        "fullUniverseMarketMovementToClose": full_universe_move,
    }


# ── preregistration ordering (structural) ───────────────────────────────

RESULT_PRODUCING_CALL_NAMES = {"build_opportunity_rows", "usable_rows_and_coverage", "_population_analysis"}


def _call_names_in_order(func_node):
    names = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.append(f.id)
            elif isinstance(f, ast.Attribute):
                names.append(f.attr)
    return names


def _find_function_node(name):
    source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_edge_persistence_experiment.py")).read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestPreregistrationOrdering:
    def test_registration_call_exists_in_main(self):
        names = _call_names_in_order(_find_function_node("main"))
        assert "register_control_and_experiment" in names

    def test_registration_happens_before_every_result_producing_call(self):
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_control_and_experiment")
        for result_call in RESULT_PRODUCING_CALL_NAMES:
            occurrences = [i for i, n in enumerate(names) if n == result_call]
            assert occurrences, f"expected main() to call {result_call!r} at least once"
            assert min(occurrences) > registration_index, f"{result_call!r} is called before registration"


# ── reuse-not-reimplementation ───────────────────────────────────────────

class TestReuseNotReimplementation:
    def test_fair_market_probability_is_the_real_rsch0001_function(self):
        assert exp.rsch0001.fair_market_probability is rsch0001.fair_market_probability

    def test_edge_buckets_are_the_real_rsch0001_constant(self):
        assert exp.rsch0001.EDGE_BUCKETS is rsch0001.EDGE_BUCKETS

    def test_assign_edge_bucket_is_the_real_rsch0001_function(self):
        assert exp.rsch0001.assign_edge_bucket is rsch0001.assign_edge_bucket

    def test_analyze_segment_is_the_real_rsch0001_function(self):
        assert exp.rsch0001.analyze_segment is rsch0001.analyze_segment

    def test_filter_trusted_production_only_is_the_real_rsch0001_function(self):
        assert exp.rsch0001.filter_trusted_production_only is rsch0001.filter_trusted_production_only

    def test_filter_canonical_era_is_the_real_rsch0001_function(self):
        assert exp.rsch0001.filter_canonical_era is rsch0001.filter_canonical_era

    def test_script_does_not_redefine_edge_buckets(self):
        source = open(exp.__file__).read()
        assert "EDGE_BUCKETS = (" not in source

    def test_script_does_not_redefine_fair_market_probability(self):
        source = open(exp.__file__).read()
        assert "def fair_market_probability" not in source

    def test_min_games_confident_reuses_rsch0001_constant_not_a_new_one(self):
        assert exp.classify_persistence_signal.__defaults__[1] == rsch0001.MIN_GAMES_EXPLORATORY


# ── causal / chronological sequence construction ─────────────────────────

class TestBuildTickerSequences:
    def test_groups_by_ticker(self):
        rows = [_row("T1", "T_MINUS_90", "2026-08-01T10:00:00Z", 0.05), _row("T2", "T_MINUS_90", "2026-08-01T10:00:00Z", 0.03)]
        sequences = exp.build_ticker_sequences(rows)
        assert set(sequences.keys()) == {"T1", "T2"}

    def test_sorts_chronologically_by_captured_at(self):
        rows = [
            _row("T1", "T_MINUS_30", "2026-08-01T12:00:00Z", 0.05),
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.04),
            _row("T1", "T_MINUS_60", "2026-08-01T10:30:00Z", 0.045),
        ]
        seq = exp.build_ticker_sequences(rows)["T1"]
        assert [r["researchCheckpoint"] for r in seq] == ["T_MINUS_90", "T_MINUS_60", "T_MINUS_30"]

    def test_single_row_ticker_gets_length_one_sequence(self):
        rows = [_row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.02)]
        seq = exp.build_ticker_sequences(rows)["T1"]
        assert len(seq) == 1


# ── persistence tier classification ───────────────────────────────────────

class TestPersistenceTierClassification:
    def test_single_checkpoint_is_transient(self):
        seq = [_row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.05)]
        assert exp.classify_persistence_tier(seq) == exp.SINGLETON_TRANSIENT

    def test_sign_change_is_transient(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", -0.03),
        ]
        assert exp.classify_persistence_tier(seq) == exp.SINGLETON_TRANSIENT

    def test_two_consecutive_same_sign_is_two_checkpoint_persistent(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "T_MINUS_60", "2026-08-01T11:00:00Z", 0.04),
        ]
        assert exp.classify_persistence_tier(seq) == exp.TWO_CHECKPOINT_PERSISTENT

    def test_three_consecutive_same_sign_is_three_plus_persistent(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "T_MINUS_60", "2026-08-01T11:00:00Z", 0.04),
            _row("T1", "T_MINUS_30", "2026-08-01T13:00:00Z", 0.06),
        ]
        assert exp.classify_persistence_tier(seq) == exp.THREE_PLUS_CHECKPOINT_PERSISTENT

    def test_a_zero_edge_breaks_the_run(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "T_MINUS_60", "2026-08-01T11:00:00Z", 0.0),
            _row("T1", "T_MINUS_30", "2026-08-01T13:00:00Z", 0.05),
        ]
        assert exp.classify_persistence_tier(seq) == exp.SINGLETON_TRANSIENT

    def test_run_resets_after_a_sign_change_then_rebuilds(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "T_MINUS_60", "2026-08-01T10:00:00Z", -0.05),
            _row("T1", "T_MINUS_30", "2026-08-01T11:00:00Z", -0.03),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", -0.02),
        ]
        # longest run is the trailing 3 negative-sign checkpoints
        assert exp.classify_persistence_tier(seq) == exp.THREE_PLUS_CHECKPOINT_PERSISTENT


# ── lineup-confirmed / late-surviving flags ───────────────────────────────

class TestLineupConfirmedPersistent:
    def test_false_when_no_lineup_confirmation_checkpoint_observed(self):
        seq = [_row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05), _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.04)]
        assert exp.is_lineup_confirmed_persistent(seq) is False

    def test_true_when_positive_before_and_at_lineup_confirmation(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "LINEUP_CONFIRMATION", "2026-08-01T15:00:00Z", 0.03),
        ]
        assert exp.is_lineup_confirmed_persistent(seq) is True

    def test_false_when_lineup_confirmation_edge_is_not_positive(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05),
            _row("T1", "LINEUP_CONFIRMATION", "2026-08-01T15:00:00Z", -0.01),
        ]
        assert exp.is_lineup_confirmed_persistent(seq) is False

    def test_false_when_no_earlier_positive_edge(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", -0.02),
            _row("T1", "LINEUP_CONFIRMATION", "2026-08-01T15:00:00Z", 0.03),
        ]
        assert exp.is_lineup_confirmed_persistent(seq) is False


class TestLateSurviving:
    def test_false_with_single_checkpoint(self):
        seq = [_row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.05)]
        assert exp.is_late_surviving(seq) is False

    def test_true_when_positive_early_and_positive_at_close(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.02, is_closing=True),
        ]
        assert exp.is_late_surviving(seq) is True

    def test_false_when_final_edge_not_positive(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", -0.01, is_closing=True),
        ]
        assert exp.is_late_surviving(seq) is False

    def test_uses_own_closing_row_not_chronologically_last_when_present(self):
        # closing row is NOT the last chronologically -- still must be selected as "final"
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "CLOSING", "2026-08-01T12:00:00Z", 0.02, is_closing=True),
            _row("T1", "INTERMEDIATE", "2026-08-01T15:00:00Z", -0.5, is_closing=False),
        ]
        assert exp.is_late_surviving(seq) is True


# ── market movement direction ─────────────────────────────────────────────

class TestMarketMovedWithModel:
    def test_none_with_single_checkpoint(self):
        seq = [_row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.05)]
        assert exp.market_moved_with_model(seq) is None

    def test_true_when_price_rises_toward_positive_edge(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, executable_yes_price=0.40),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.02, executable_yes_price=0.45),
        ]
        assert exp.market_moved_with_model(seq) is True

    def test_false_when_price_falls_despite_positive_edge(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, executable_yes_price=0.40),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.08, executable_yes_price=0.37),
        ]
        assert exp.market_moved_with_model(seq) is False

    def test_none_when_initial_edge_is_zero(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.0, executable_yes_price=0.40),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.02, executable_yes_price=0.45),
        ]
        assert exp.market_moved_with_model(seq) is None


# ── paired-row identity / pseudoreplication guard ─────────────────────────

class TestFinalRowSelection:
    def test_ticker_summary_uses_closing_row_as_final_when_present(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.02, is_closing=True),
        ]
        summary = exp.build_ticker_summary("T1", seq)
        assert summary["finalRow"]["researchCheckpoint"] == "CLOSING"
        assert summary["finalEdge"] == 0.02

    def test_ticker_summary_uses_last_row_when_no_closing_row(self):
        seq = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "T_MINUS_30", "2026-08-01T13:00:00Z", 0.03, is_closing=False),
        ]
        summary = exp.build_ticker_summary("T1", seq)
        assert summary["finalRow"]["researchCheckpoint"] == "T_MINUS_30"

    def test_analyze_segment_never_receives_more_than_one_row_per_ticker(self):
        """The pseudoreplication guard: build_ticker_summaries -> _final_rows_for
        must produce exactly one row per ticker, regardless of how many
        checkpoints that ticker had."""
        rows = [
            _row("T1", "T_MINUS_90", "2026-08-01T09:00:00Z", 0.05, is_closing=False),
            _row("T1", "T_MINUS_60", "2026-08-01T11:00:00Z", 0.04, is_closing=False),
            _row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.03, is_closing=True),
            _row("T2", "CLOSING", "2026-08-01T18:00:00Z", 0.02, is_closing=True),
        ]
        summaries = exp.build_ticker_summaries(rows)
        final_rows = exp._final_rows_for(summaries, lambda s: True)
        assert len(final_rows) == 2  # one per ticker, not one per checkpoint row (would be 4)


# ── missing checkpoints never fabricated ──────────────────────────────────

class TestMissingCheckpointsNeverFabricated:
    def test_no_lineup_confirmation_anywhere_yields_zero_not_a_crash(self):
        rows = [_row("T1", "CLOSING", "2026-08-01T18:00:00Z", 0.05)]
        summaries = exp.build_ticker_summaries(rows)
        result = exp.flag_analysis(summaries, "lineupConfirmedPersistent", "LINEUP_CONFIRMED_PERSISTENT")
        assert result["rawRows"] == 0
        assert result["independentGames"] == 0


# ── final signal classification ───────────────────────────────────────────

class TestClassifyPersistenceSignal:
    def _overall(self, delta, lo, hi, games):
        return {"independentGames": games, "pairedBrierDelta_modelMinusMarket": delta, "pairedDeltaConfidenceInterval90": {"low": lo, "high": hi}}

    def test_below_reportable_floor_is_weak(self):
        persistent = self._overall(-0.05, -0.1, 0.0, games=2)
        assert exp.classify_persistence_signal(persistent, None) == exp.SIGNAL_WEAK

    def test_small_sample_confident_ci_still_capped_below_strong_or_negative(self):
        """Regression test for the exact bug found during manual verification:
        a fragile small-sample (15-game) CI that confidently excludes zero
        on the unfavorable side must NOT trigger NEGATIVE_SIGNAL -- only a
        sample >= MIN_GAMES_EXPLORATORY (reused from RSCH-0001) can."""
        persistent = self._overall(0.02, 0.0005, 0.04, games=15)  # confidently positive (bad), but n=15 < 50
        transient = self._overall(0.05, 0.01, 0.09, games=57)
        result = exp.classify_persistence_signal(persistent, transient)
        assert result not in (exp.SIGNAL_STRONG, exp.SIGNAL_NEGATIVE)

    def test_confident_negative_at_full_sample_is_negative_signal(self):
        persistent = self._overall(0.02, 0.005, 0.035, games=60)
        assert exp.classify_persistence_signal(persistent, None) == exp.SIGNAL_NEGATIVE

    def test_confident_strong_requires_full_sample_and_incremental_advantage(self):
        persistent = self._overall(-0.03, -0.05, -0.01, games=60)
        transient = self._overall(0.01, -0.01, 0.03, games=60)
        assert exp.classify_persistence_signal(persistent, transient) == exp.SIGNAL_STRONG

    def test_incremental_but_not_confident_is_partial(self):
        persistent = self._overall(0.005, -0.02, 0.03, games=39)
        transient = self._overall(0.02, 0.005, 0.035, games=144)
        assert exp.classify_persistence_signal(persistent, transient) == exp.SIGNAL_PARTIAL

    def test_no_incremental_advantage_is_no_useful(self):
        persistent = self._overall(0.03, -0.01, 0.07, games=60)
        transient = self._overall(0.01, -0.01, 0.03, games=60)
        assert exp.classify_persistence_signal(persistent, transient) == exp.SIGNAL_NO_USEFUL


# ── production unchanged ───────────────────────────────────────────────

class TestProductionUnchanged:
    def test_script_writes_only_to_research_lab_output_paths(self):
        source = open(exp.__file__).read()
        assert 'os.path.join("data", "edgelab", "analytics")' in source
        assert 'os.path.join("data", "edgelab", "reports")' in source
        for forbidden_write_target in ("config/rules.json", 'open("data/edgelab/observations', 'open("data/edgelab/bets'):
            assert forbidden_write_target not in source

    def test_registration_reuses_the_same_control_identity_inputs_as_rsch0001(self):
        """The id itself is never hardcoded (control_identity derives it from
        name+commit+config_fingerprint) -- this checks the INPUTS that
        produce it match RSCH-0001's registration exactly, which is what
        makes the re-registration a write-once no-op rather than minting a
        new control."""
        source = open(exp.__file__).read()
        assert 'name="edgelab_production_model_corpus_2026_08"' in source
        assert 'source_git_commit_sha="68bf46e6acde8e48e347ccb762f0e518cbcb16a5"' in source
