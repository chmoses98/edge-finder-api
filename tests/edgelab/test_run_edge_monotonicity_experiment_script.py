#!/usr/bin/env python3
"""
tests/edgelab/test_run_edge_monotonicity_experiment_script.py
==================================================================
Coverage for scripts/edgelab/run_edge_monotonicity_experiment.py --
Research Lab Milestone 1 (MLB-RSCH-0001, "Edge Validity / Edge
Monotonicity"). Every test runs against small, synthetic, deterministic
fixtures -- never the real committed corpus (matching this repo's
`tests/edgelab/test_snapshot.py`/`test_replay.py` discipline).
"""
import gzip
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import scripts.edgelab.run_edge_monotonicity_experiment as exp  # noqa: E402
from lib.edgelab import dispositions as disp  # noqa: E402
from lib.edgelab import evidence_levels as ev  # noqa: E402


def _row(game_id="g1", ticker="T1", checkpoint="CLOSING", edge=0.05, model_prob=0.55, exec_yes=0.5,
         result="YES", family="team_total", game_date="2026-08-01", model_eval_available=True,
         model_eval_id="EVAL1", unavailable_reason=None, side=None, model_selection_ambiguous=False,
         settlement_status="SETTLED"):
    """Minimal opportunity-row-shaped dict (the shape
    lib.edgelab.research_dataset.build_opportunity_rows emits), for
    testing the pure analysis functions directly without going through
    the full observation/settlement/evaluation join."""
    return {
        "gameId": game_id, "marketTicker": ticker, "researchCheckpoint": checkpoint, "checkpoint": checkpoint,
        "gameDate": game_date, "canonicalMarketFamily": family,
        "contemporaneousEdge": edge, "modelFairProbability": model_prob, "executableYesPrice": exec_yes,
        "settlementStatus": settlement_status, "settlementResult": result,
        "modelEvaluationAvailable": model_eval_available, "modelEvaluationUnavailableReason": unavailable_reason,
        "modelEvaluationId": model_eval_id, "side": side, "modelSelectionAmbiguous": model_selection_ambiguous,
    }


# ── Edge bucket / price band assignment ─────────────────────────────────

@pytest.mark.parametrize("edge,expected", [
    (-0.5, "<0%"), (-0.0001, "<0%"),
    (0.0, "0-2.5%"), (0.024, "0-2.5%"),
    (0.025, "2.5-5%"), (0.049, "2.5-5%"),
    (0.05, "5-7.5%"), (0.075, "7.5-10%"), (0.0999, "7.5-10%"),
    (0.10, "10-15%"), (0.1499, "10-15%"),
    (0.15, "15%+"), (5.0, "15%+"),
])
def test_assign_edge_bucket_boundaries(edge, expected):
    assert exp.assign_edge_bucket(edge) == expected


def test_edge_buckets_are_exhaustive_and_never_overlap():
    """Every real number lands in exactly one bucket -- probed at a fine grid."""
    for i in range(-200, 200):
        edge = i / 1000.0
        label = exp.assign_edge_bucket(edge)
        assert label in {b[0] for b in exp.EDGE_BUCKETS}


@pytest.mark.parametrize("price,expected", [(0.05, "<20%"), (0.20, "20-50%"), (0.49, "20-50%"), (0.5, "50-80%"), (0.8, "80%+"), (0.99, "80%+")])
def test_assign_price_band_boundaries(price, expected):
    assert exp.assign_price_band(price) == expected


# ── Usable-row filtering / coverage accounting ──────────────────────────

def test_usable_rows_excludes_unsettled_and_unavailable_and_no_edge():
    rows = [
        _row(settlement_status="PENDING"),  # not settled
        _row(model_eval_available=False, unavailable_reason="NO_EVALUATIONS_FOR_TICKER"),  # no causal eval
        _row(edge=None),  # settled + eval available but no computable edge
        _row(side="NO"),  # non-YES side, excluded per this experiment's own eligibility criteria
        _row(),  # genuinely usable
    ]
    usable, coverage = exp.usable_rows_and_coverage(rows)
    assert len(usable) == 1
    assert coverage["totalArchivedOpportunityRows"] == 5
    assert coverage["rowsExcludedForPitTimingReasons"] == {"NO_EVALUATIONS_FOR_TICKER": 1}
    assert coverage["rowsExcludedNonYesSide"] == 1
    assert coverage["usableRows"] == 1


def test_coverage_never_treats_raw_rows_as_independent_games():
    rows = [_row(game_id="g1", ticker=f"T{i}") for i in range(10)]  # 10 rows, ONE game
    usable, coverage = exp.usable_rows_and_coverage(rows)
    assert coverage["usableRows"] == 10
    assert coverage["independentGamesUsable"] == 1


# ── Segment analysis ─────────────────────────────────────────────────────

def test_analyze_segment_empty_rows_is_labeled_insufficient_not_an_error():
    result = exp.analyze_segment([], "EMPTY")
    assert result["rawRows"] == 0
    assert result["interpretability"] == "INSUFFICIENT"


def test_analyze_segment_computes_expected_brier_scores():
    # Control (market) always predicts 0.5 (a coinflip); model predicts closer to truth.
    rows = []
    for i in range(20):
        outcome = "YES" if i % 2 == 0 else "NO"
        model_p = 0.8 if outcome == "YES" else 0.2
        rows.append(_row(game_id=f"g{i}", ticker=f"T{i}", exec_yes=0.5, model_prob=model_p, result=outcome, game_date="2026-08-01"))
    result = exp.analyze_segment(rows, "TEST")
    assert result["marketBenchmarkBrierScore"] == pytest.approx(0.25)
    assert result["modelBrierScore"] == pytest.approx(0.04)
    assert result["pairedBrierDelta_modelMinusMarket"] < 0  # model is better here


def test_analyze_segment_interpretability_thresholds():
    def _games(n):
        return [_row(game_id=f"g{i}", ticker=f"T{i}", game_date="2026-08-01") for i in range(n)]

    assert exp.analyze_segment(_games(10), "x")["interpretability"] == "INSUFFICIENT"
    assert exp.analyze_segment(_games(60), "x")["interpretability"] == "EXPLORATORY"
    assert exp.analyze_segment(_games(150), "x")["interpretability"] == "INTERPRETABLE"
    assert exp.analyze_segment(_games(250), "x")["interpretability"] == "SUBSTANTIAL"


def test_analyze_segment_never_crashes_with_too_few_games_for_bootstrap():
    rows = [_row(game_id="g1", ticker="T1"), _row(game_id="g2", ticker="T2")]
    result = exp.analyze_segment(rows, "TINY")
    assert result["pairedDeltaConfidenceInterval90"]["method"] == "TOO_FEW_GAMES_FOR_BOOTSTRAP"
    assert result["pairedBrierDelta_modelMinusMarket"] is not None


# ── Model-vs-market pairing (must be a perfect 1:1 match by construction) ──

def test_model_vs_market_pairing_never_drops_a_row():
    rows = [_row(game_id=f"g{i}", ticker=f"T{i}") for i in range(15)]
    pairing = exp._model_vs_market_pairing(rows)
    assert pairing["nControlOnly"] == 0
    assert pairing["nCandidateOnly"] == 0
    assert pairing["nPaired"] == 15


# ── False-discovery handling (Benjamini-Hochberg) ───────────────────────

def test_benjamini_hochberg_known_example():
    # Classic textbook-style example: 2 of 5 p-values should survive at q=0.10.
    pvalues = {"a": 0.001, "b": 0.008, "c": 0.039, "d": 0.041, "e": 0.9}
    significant = exp.benjamini_hochberg(pvalues, q=0.10)
    assert significant["a"] is True
    assert significant["b"] is True
    assert significant["e"] is False


def test_benjamini_hochberg_handles_none_pvalues_gracefully():
    significant = exp.benjamini_hochberg({"a": None, "b": 0.01}, q=0.10)
    assert significant["a"] is False


def test_benjamini_hochberg_empty_input():
    assert exp.benjamini_hochberg({}, q=0.10) == {}


# ── Disposition / signal classification never over-claims ──────────────

def test_determine_disposition_never_returns_shadow_or_promotion_candidate():
    for overall_delta in (-0.5, -0.01, 0.0, 0.01, 0.5):
        for monotonic in (True, False, None):
            overall = {"pairedBrierDelta_modelMinusMarket": overall_delta}
            checks = {"monotonicNonIncreasingDeltaAcrossBuckets": monotonic}
            result = exp.determine_disposition(overall, checks)
            assert result in (disp.REJECT, disp.RESEARCH_CANDIDATE)


def test_determine_disposition_rejects_on_material_problem():
    overall = {"pairedBrierDelta_modelMinusMarket": 0.05}
    checks = {"monotonicNonIncreasingDeltaAcrossBuckets": False}
    assert exp.determine_disposition(overall, checks) == disp.REJECT


def test_determine_disposition_research_candidate_when_model_beats_market():
    overall = {"pairedBrierDelta_modelMinusMarket": -0.05}
    checks = {"monotonicNonIncreasingDeltaAcrossBuckets": True}
    assert exp.determine_disposition(overall, checks) == disp.RESEARCH_CANDIDATE


def test_classify_edge_signal_returns_one_of_the_four_spec_labels():
    valid_labels = {"STRONGLY VALID", "PARTIALLY VALID", "WEAK / UNPROVEN", "MATERIAL PROBLEM FOUND"}
    coverage = {"independentGamesUsable": 200}
    for delta, ci, monotonic in [(-0.05, {"low": -0.1, "high": -0.01}, True), (0.05, {}, False), (None, {}, None), (-0.001, {"low": -0.05, "high": 0.02}, True)]:
        overall = {"pairedBrierDelta_modelMinusMarket": delta, "pairedDeltaConfidenceInterval90": ci}
        checks = {"monotonicNonIncreasingDeltaAcrossBuckets": monotonic}
        assert exp.classify_edge_signal(overall, checks, coverage) in valid_labels


def test_classify_edge_signal_weak_when_insufficient_games():
    coverage = {"independentGamesUsable": 5}
    overall = {"pairedBrierDelta_modelMinusMarket": -0.5, "pairedDeltaConfidenceInterval90": {"low": -0.6, "high": -0.4}}
    checks = {"monotonicNonIncreasingDeltaAcrossBuckets": True}
    assert exp.classify_edge_signal(overall, checks, coverage) == "WEAK / UNPROVEN"


# ── Registration happens, is idempotent, and precedes any result ───────

def _tiny_evaluations():
    return [
        {"modelEvaluationId": "EVAL1", "modelCommitSha": "sha1", "modelConfigVersion": "1.0", "artifactSource": "recommendations", "qualityTier": "TRUSTED_PRODUCTION"},
        {"modelEvaluationId": "EVAL2", "modelCommitSha": "sha2", "modelConfigVersion": "1.0", "artifactSource": "prospective_snapshot", "qualityTier": None},
    ]


def test_register_control_and_experiment_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("config", exist_ok=True)
    with open(os.path.join("config", "rules.json"), "w") as f:
        json.dump({"_version": "1.0"}, f)

    control1, definition1 = exp.register_control_and_experiment(_tiny_evaluations())
    control2, definition2 = exp.register_control_and_experiment(_tiny_evaluations())
    assert control1 == control2
    assert definition1 == definition2
    assert definition1["experimentId"] == exp.EXPERIMENT_ID
    assert definition1["candidateVariantId"] is None  # control-only experiment
    assert definition1["status"] != disp.PRODUCTION


def test_registered_experiment_evidence_level_is_e1_not_forced_to_e2(tmp_path, monkeypatch):
    """Direct proof this experiment does not force E2/E3 despite depending on a PROSPECTIVE_ONLY-classified PIT input."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("config", exist_ok=True)
    with open(os.path.join("config", "rules.json"), "w") as f:
        json.dump({"_version": "1.0"}, f)
    _control, definition = exp.register_control_and_experiment(_tiny_evaluations())
    assert definition["evidenceLevel"] == ev.E1_RECONSTRUCTED_RETROSPECTIVE
    assert ev.rank(definition["evidenceLevel"]) < ev.rank(ev.E2_PIT_HISTORICAL)


# ── Important checks / secondary economics ──────────────────────────────

def test_important_checks_flags_inverted_buckets():
    bucket_results = [
        {"label": "<0%", "rawRows": 5, "pairedBrierDelta_modelMinusMarket": -0.05, "calibrationGap": 0.01, "sampleSizeStatus": {"gameConcentrationWarning": False}},
        {"label": "0-2.5%", "rawRows": 5, "pairedBrierDelta_modelMinusMarket": -0.02, "calibrationGap": 0.0, "sampleSizeStatus": {"gameConcentrationWarning": False}},
        {"label": "2.5-5%", "rawRows": 5, "pairedBrierDelta_modelMinusMarket": -0.10, "calibrationGap": -0.02, "sampleSizeStatus": {"gameConcentrationWarning": False}},
    ]
    checks = exp.important_checks(bucket_results, {}, [], [])
    # -0.02 (2nd bucket) > -0.05 (1st bucket) -- an inversion (got worse, not better).
    assert "0-2.5%" in checks["invertedOrFlatBuckets"]
    # -0.10 (3rd bucket) < -0.02 (2nd bucket) -- correctly NOT flagged.
    assert "2.5-5%" not in checks["invertedOrFlatBuckets"]


def test_bucket_economics_is_labeled_secondary_and_reuses_canonical_fee_engine():
    rows = [_row(game_id=f"g{i}", ticker=f"T{i}", exec_yes=0.5, result="YES") for i in range(5)]
    econ = exp._bucket_economics(rows)
    assert econ["nSettledOrdersSimulated"] == 5
    assert "SECONDARY" in econ["warning"]
    assert econ["orderSizeAssumption"] > 0


# ── Structural production-safety guard (same spirit as Milestone 0A's own) ─

def test_script_never_references_a_production_file_write_path():
    path = os.path.join(ROOT, "scripts", "edgelab", "run_edge_monotonicity_experiment.py")
    with open(path) as f:
        lines = f.readlines()
    write_indicators = ("open(", "os.makedirs(", ".write(")
    forbidden = ("data/slate.json", "data/bets.json", "BET_LOG.md", "config/rules.json", "data/pipeline/")
    for lineno, line in enumerate(lines, start=1):
        if not any(ind in line for ind in write_indicators):
            continue
        for bad in forbidden:
            assert bad not in line, f"{path}:{lineno}: write-shaped line references {bad!r}"


def test_script_disposition_can_never_be_production_or_promotion_track():
    """Structural: neither PRODUCTION nor SHADOW_CANDIDATE/PROMOTION_CANDIDATE ever appear as a literal disposition value assignable by this script's own logic."""
    import inspect
    source = inspect.getsource(exp.determine_disposition)
    assert "disp.PRODUCTION" not in source
    assert "disp.SHADOW_CANDIDATE" not in source
    assert "disp.PROMOTION_CANDIDATE" not in source


# ── End-to-end smoke test against a tiny synthetic corpus ──────────────

def _obs(obs_id, ticker="T1", captured_at="2026-08-01T18:00:00Z", checkpoint="T_MINUS_30",
         scheduled_start="2026-08-01T18:30:00Z", yes_bid=44.0, yes_ask=46.0, no_bid=54.0, no_ask=56.0,
         game_id="g1", market_family="KXMLBGAME"):
    return {
        "marketObservationId": obs_id, "marketTicker": ticker, "capturedAt": captured_at, "checkpoint": checkpoint,
        "scheduledStart": scheduled_start, "gameId": game_id, "marketFamily": market_family,
        "yesBid": yes_bid, "yesAsk": yes_ask, "noBid": no_bid, "noAsk": no_ask, "lastPrice": yes_ask,
        "marketStatus": "active", "isValidPregameObservation": True, "isClosingCandidate": True,
        "threshold": None, "comparisonOperator": None, "team": None, "player": None, "outcomeLabel": None,
        "marketHorizon": "FULL_GAME", "lineupConfirmationState": None, "source": "edgelab_test",
    }


def _settlement(ticker="T1", result="YES"):
    return {"marketTicker": ticker, "settlementStatus": "SETTLED", "result": result, "unavailableReason": None}


def _evaluation(eval_id, ticker="T1", pipeline_run_id="2026-08-01T12:00:00Z", model_fair_probability=60.0):
    return {
        "modelEvaluationId": eval_id, "marketTicker": ticker, "pipelineRunId": pipeline_run_id,
        "modelFairProbability": model_fair_probability, "marketImpliedProbability": 50.0,
        "selection": "ML_Away", "side": None, "threshold": None, "evaluationStatus": "EVALUATED",
        "modelCommitSha": "deadbeef", "modelConfigVersion": "1.0", "modelSource": "test",
        "artifactSource": "recommendations", "qualityTier": "TRUSTED_PRODUCTION", "estimatedEdge": 10.0,
        "confidence": "HIGH", "dataQuality": "full", "thesisTags": [], "correlationGroups": [],
        "checkpoint": None, "inputFreshnessNote": None,
    }


def _game(game_id="g1", date="2026-08-01"):
    return {"gameId": game_id, "gameDate": date, "scheduledStartTime": f"{date}T18:30:00Z", "actualStartTime": None}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def _write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _wire_tiny_corpus(tmp_path, date="2026-08-01", n_games=12):
    """A small but bootstrap-eligible (>=5 games) synthetic corpus -- enough for the script to run its full pipeline end to end without erroring on tiny-sample edge cases."""
    observations, settlements, evaluations, games = [], [], [], []
    for i in range(n_games):
        ticker = f"KXMLBGAME-T{i}"
        game_id = f"g{i}"
        observations.append(_obs(f"obs{i}", ticker=ticker, game_id=game_id))
        result = "YES" if i % 2 == 0 else "NO"
        settlements.append(_settlement(ticker=ticker, result=result))
        evaluations.append(_evaluation(f"eval{i}", ticker=ticker, model_fair_probability=55.0 + i))
        games.append(_game(game_id=game_id, date=date))

    _write_jsonl(os.path.join("data", "edgelab", "observations", f"{date}.jsonl"), observations)
    _write_jsonl(os.path.join("data", "edgelab", "settlements", f"{date}.jsonl"), settlements)
    _write_jsonl(os.path.join("data", "edgelab", "model_evaluations", f"{date}.jsonl"), evaluations)
    _write_jsonl(os.path.join("data", "edgelab", "games", f"{date}.jsonl"), games)
    _write_jsonl(os.path.join("data", "edgelab", "recommendations", f"{date}.jsonl"), [])
    os.makedirs(os.path.join("data", "edgelab", "bets"), exist_ok=True)
    _write_jsonl(os.path.join("data", "edgelab", "bets", "bets.jsonl"), [])
    _write(os.path.join("config", "rules.json"), {"_version": "1.0"})


def test_end_to_end_script_run_produces_all_deliverables(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _wire_tiny_corpus(tmp_path)

    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab", "run_edge_monotonicity_experiment.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    assert os.path.exists(os.path.join("data", "edgelab", "control_models"))
    assert os.path.exists(os.path.join("data", "edgelab", "experiments", "MLB-RSCH-0001.json"))
    with open(os.path.join("data", "edgelab", "experiments", "MLB-RSCH-0001.json")) as f:
        definition = json.load(f)
    assert definition["candidateVariantId"] is None
    assert definition["evidenceLevel"] == "E1_RECONSTRUCTED_RETROSPECTIVE"

    report_dir = os.path.join("data", "edgelab", "experiment_reports", "MLB-RSCH-0001")
    assert os.path.isdir(report_dir)
    report_files = [f for f in os.listdir(report_dir) if f.endswith(".json")]
    assert len(report_files) == 1
    with open(os.path.join(report_dir, report_files[0])) as f:
        report = json.load(f)
    assert report["productionBehaviorChanged"] is False
    assert report["disposition"] in (disp.REJECT, disp.RESEARCH_CANDIDATE)
    assert "edgeBuckets" in report["secondaryMetrics"]
    assert len(report["secondaryMetrics"]["edgeBuckets"]) == len(exp.EDGE_BUCKETS)

    assert os.path.exists(os.path.join("data", "edgelab", "reports", "mlb_rsch_0001_edge_monotonicity_summary.md"))
    with open(os.path.join("data", "edgelab", "reports", "mlb_rsch_0001_edge_monotonicity_summary.md")) as f:
        markdown = f.read()
    assert "MLB-RSCH-0001" in markdown
    assert "RESEARCH ONLY" in markdown


def test_end_to_end_script_never_writes_production_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _wire_tiny_corpus(tmp_path)

    sentinels = {
        "data/slate.json": b'{"date": "SENTINEL_SLATE"}',
        "data/bets.json": b'{"bets": ["SENTINEL_BET_LEDGER"]}',
        "config/rules.json": b'{"_version": "SENTINEL_CONFIG_SHOULD_NOT_BE_TOUCHED"}',
        os.path.join("data", "edgelab", "observations", "2026-08-01.jsonl"): None,  # captured below, not overwritten
    }
    # Deliberately leave observations/settlements/etc alone (the fixture);
    # only sentinel-check files that must NEVER be written by this script.
    for path, content in list(sentinels.items()):
        if content is None:
            continue
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    before = {}
    for path in ("config/rules.json",):
        with open(path, "rb") as f:
            before[path] = f.read()

    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab", "run_edge_monotonicity_experiment.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    for path, expected in before.items():
        with open(path, "rb") as f:
            assert f.read() == expected, f"{path} was modified by run_edge_monotonicity_experiment.py"

    for path in ("data/slate.json", "data/bets.json"):
        with open(path, "rb") as f:
            assert f.read() == sentinels[path]
