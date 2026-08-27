#!/usr/bin/env python3
"""
scripts/edgelab/run_edge_monotonicity_experiment.py
========================================================
Research Lab Milestone 1: MLB-RSCH-0001, "Edge Validity / Edge
Monotonicity" -- the first substantive experiment run on top of the
Milestone 0A Research Lab Control & Experiment Contract
(docs/EDGELAB_RESEARCH_LAB.md).

RESEARCH QUESTION: Does larger declared model edge correspond to
genuinely greater predictive advantage over the Kalshi market, and does
that relationship differ by market family?

RESEARCH-ONLY / READ-ONLY: this script never writes to
data/edgelab/observations/, model_evaluations/, settlements/,
recommendations/, games/, bets/, or config/rules.json -- only to
data/edgelab/control_models/, data/edgelab/experiments/,
data/edgelab/experiment_reports/, and data/edgelab/reports/ (the new
Milestone 0A registry directories plus the existing human-readable
report convention). Nothing here changes production model probabilities,
features, recommendation logic, thresholds, confidence, fees, staking,
market eligibility, or risk gates.

WHAT THIS REUSES (per Milestone 0A: "do not build parallel
infrastructure"):
  - lib.edgelab.research_dataset.build_opportunity_rows -- the ONE
    canonical, causally-joined (no-look-ahead) full-universe opportunity
    dataset. This script builds NO independent join of its own.
  - lib.edgelab.temporal_alignment (via research_dataset) for the
    ModelEvaluation<->checkpoint causal join.
  - lib.edgelab.research_stats for Brier score / log loss reuse
    (via replay.py), calibration error, sample-size status, and the
    game-clustered bootstrap.
  - lib.edgelab.paired_evaluation.pair_eligible_observations /
    evaluate_probability_model_pair -- reused here for MODEL-vs-MARKET
    scoring (not the module's originally-envisioned control-model-vs-
    candidate-variant use, but the exact same statistical contract:
    two probability series over identical eligible observations,
    scored with the same game-clustered methodology). See
    _model_vs_market_pairing() below for exactly how "control"/
    "candidate" are repurposed, clearly labeled so this is never
    confused with the experiment's own registered controlModelId.
  - lib.edgelab.kalshi_fees -- the one fee-aware execution-economics
    engine, for the SECONDARY hypothetical P/L section only.
  - lib.edgelab.control_identity / experiment_registry / pit_provenance /
    experiment_report / dispositions / evidence_levels -- the full
    Milestone 0A governance contract. The experiment is registered
    BEFORE any bucket is computed (see main()) -- literally sequential,
    not merely documented.

WHAT THIS DOES NOT DO: propose a candidate model variant (this is a
control-only experiment -- see docs/EDGELAB_RESEARCH_LAB.md section on
control-only experiments), recommend a production change, tune bucket
boundaries after viewing results (the 7 EDGE_BUCKETS below are declared
as a module-level constant, never touched after this script was first
run), or optimize historical ROI (fee-aware P/L is explicitly reported
as SECONDARY evidence only -- see the module docstring's PRIMARY-vs-
SECONDARY framing and lib.edgelab.paired_evaluation's own warning).
"""
import argparse
import glob
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import candidate_identity as cand_id  # noqa: F401  (imported for parity/documentation -- this is a control-only experiment, see module docstring)
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import experiment_report as er
from lib.edgelab import kalshi_fees as kf
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import pit_provenance as pit
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab import storage
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    brier_and_log_loss_summary,
    expected_calibration_error,
    independent_unit_count,
    sample_size_status,
)

EXPERIMENT_ID = "MLB-RSCH-0001"
EXPERIMENT_TITLE = "Edge Validity / Edge Monotonicity"

# Fixed, never-wall-clock registration timestamp (reproducibility, spec
# section "REPRODUCIBILITY REQUIREMENTS": "Do not depend on wall-clock
# ordering"). Both the control and the experiment registration are
# write-once/content-addressed -- pinning this constant means a rerun of
# this script against the same corpus always re-derives byte-identical
# registration content, rather than failing the write-once check on a
# harmless registeredAt timestamp difference.
REGISTRATION_TIMESTAMP = "2026-08-27T00:00:00Z"

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")
MACHINE_REPORT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0001_edge_monotonicity.json")
MARKDOWN_REPORT_PATH = os.path.join(REPORTS_DIR, "mlb_rsch_0001_edge_monotonicity_summary.md")

# ── 4. FIXED EDGE BUCKETS (preregistered -- never reordered/rebounded
# after viewing results; this is the one and only place these
# boundaries are declared) ──────────────────────────────────────────────
EDGE_BUCKETS = (
    ("<0%", None, 0.0),
    ("0-2.5%", 0.0, 0.025),
    ("2.5-5%", 0.025, 0.05),
    ("5-7.5%", 0.05, 0.075),
    ("7.5-10%", 0.075, 0.10),
    ("10-15%", 0.10, 0.15),
    ("15%+", 0.15, None),
)

EXECUTABLE_PRICE_BANDS = (
    ("<20%", None, 0.20),
    ("20-50%", 0.20, 0.50),
    ("50-80%", 0.50, 0.80),
    ("80%+", 0.80, None),
)

# Interpretation thresholds (spec section 8) -- guidance, never a reason
# to hide a smaller bucket.
MIN_GAMES_EXPLORATORY = 50
MIN_GAMES_INTERPRETABLE = 100
MIN_GAMES_SUBSTANTIAL = 200

FALSE_DISCOVERY_Q = 0.10  # Benjamini-Hochberg FDR level for exploratory segment screening


# ── Data loading (same pattern as scripts/edgelab/run_research_reports.py) ─

def _discover_dates():
    paths = glob.glob(storage.partition_path("observations", "*", compressed=True)) + glob.glob(storage.partition_path("observations", "*", compressed=False))
    return sorted({os.path.basename(p).split(".")[0] for p in paths})


def _load_universe(dates):
    observations, settlements, evaluations, recommendations, games = [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=True)))
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=False)))
        settlements.extend(storage.read_partition("settlements", date))
        evaluations.extend(storage.read_partition("model_evaluations", date))
        recommendations.extend(storage.read_partition("recommendations", date))
        games.extend(storage.read_records(storage.partition_path("games", date)))
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    return observations, settlements, evaluations, recommendations, games, bets


# ── Coverage / eligibility (spec section 2) ─────────────────────────────

def usable_rows_and_coverage(all_rows):
    """
    Splits `all_rows` (lib.edgelab.research_dataset.build_opportunity_rows
    output) into the usable analysis population and a full, honest
    coverage/exclusion accounting. "Usable" means: settled with a real
    YES/NO result, a causally-valid model probability was selected
    (modelEvaluationAvailable), the canonical contemporaneousEdge could
    be computed (requires both modelFairProbability and this checkpoint's
    own executable price for the model's side), and the model's own
    side defaults/resolves to YES (see module docstring -- the corpus
    has zero NO-side causally-linked rows as of this experiment; a
    NO-side row would need contemporaneousEdge reinterpreted and is
    excluded rather than silently mishandled -- see IMPORTANT_CHECKS).
    """
    total = len(all_rows)
    settled = [r for r in all_rows if r["settlementStatus"] == "SETTLED" and r["settlementResult"] in ("YES", "NO")]
    with_model_eval = [r for r in all_rows if r["modelEvaluationAvailable"]]
    settled_with_eval = [r for r in settled if r["modelEvaluationAvailable"]]

    unavailable_reasons = Counter(r["modelEvaluationUnavailableReason"] for r in all_rows if not r["modelEvaluationAvailable"])

    usable, excluded_no_edge, excluded_non_yes_side = [], 0, 0
    for r in settled_with_eval:
        if r["contemporaneousEdge"] is None or r["executableYesPrice"] is None:
            excluded_no_edge += 1
            continue
        side = r.get("side") or "YES"
        if side != "YES":
            excluded_non_yes_side += 1
            continue
        usable.append(r)

    coverage = {
        "totalArchivedOpportunityRows": total,
        "rowsWithSettlement": len(settled),
        "rowsWithCausallyValidModelProbability": len(with_model_eval),
        "rowsSettledAndWithModelProbability": len(settled_with_eval),
        "rowsExcludedForPitTimingReasons": dict(unavailable_reasons),
        "rowsExcludedMissingEdgeOrExecutablePrice": excluded_no_edge,
        "rowsExcludedNonYesSide": excluded_non_yes_side,
        "usableRows": len(usable),
        "independentGamesUsable": independent_unit_count(usable, key="gameId"),
        "independentDatesUsable": len({r.get("gameDate") for r in usable if r.get("gameDate")}),
        "uniqueTickersUsable": len({r.get("marketTicker") for r in usable}),
        "marketFamiliesUsable": sorted({r.get("canonicalMarketFamily") for r in usable if r.get("canonicalMarketFamily")}),
        "checkpointCoverageUsable": dict(Counter(r.get("researchCheckpoint") for r in usable)),
        "modelSelectionAmbiguousCount": sum(1 for r in usable if r.get("modelSelectionAmbiguous")),
    }
    return usable, coverage


_NULL_KEY_LABEL = "UNKNOWN_NULL"  # JSON object keys must be homogeneously sortable/typed -- a real None value is relabeled to this string sentinel, never silently dropped or left as a mixed-type dict key.


def _artifact_source_breakdown(usable, evaluations):
    """PIT-provenance-relevant: split usable rows by which capture pathway produced their ModelEvaluation (see pit_provenance manifest)."""
    eval_by_id = {e["modelEvaluationId"]: e for e in evaluations if e.get("modelEvaluationId")}
    by_source = Counter((eval_by_id.get(r["modelEvaluationId"], {}).get("artifactSource") or _NULL_KEY_LABEL) for r in usable)
    return dict(by_source)


def _quality_tier_by_bucket(usable, evaluations):
    """Important check: are large declared edges concentrated in RESEARCH_ONLY/UNSUPPORTED (non-TRUSTED_PRODUCTION) qualityTier rows?"""
    eval_by_id = {e["modelEvaluationId"]: e for e in evaluations if e.get("modelEvaluationId")}
    out = defaultdict(Counter)
    for r in usable:
        bucket = assign_edge_bucket(r["contemporaneousEdge"])
        tier = eval_by_id.get(r["modelEvaluationId"], {}).get("qualityTier") or _NULL_KEY_LABEL
        out[bucket][tier] += 1
    return {b: dict(c) for b, c in out.items()}


# ── Edge bucket assignment (pure, deterministic) ────────────────────────

def assign_edge_bucket(edge_fraction):
    """edge_fraction: contemporaneousEdge, a 0-1 fraction (e.g. 0.05 == 5 percentage points). Returns one of EDGE_BUCKETS' labels; raises for an edge outside every bucket (should never happen -- the 7 buckets are exhaustive over (-inf, +inf))."""
    for label, low, high in EDGE_BUCKETS:
        if low is not None and edge_fraction < low:
            continue
        if high is not None and edge_fraction >= high:
            continue
        return label
    raise ValueError(f"edge_fraction {edge_fraction!r} did not match any EDGE_BUCKETS entry")


def assign_price_band(price_fraction):
    for label, low, high in EXECUTABLE_PRICE_BANDS:
        if low is not None and price_fraction < low:
            continue
        if high is not None and price_fraction >= high:
            continue
        return label
    raise ValueError(f"price_fraction {price_fraction!r} did not match any EXECUTABLE_PRICE_BANDS entry")


# ── Model-vs-market paired scoring (reuses lib.edgelab.paired_evaluation) ──

def _model_vs_market_pairing(rows):
    """
    Builds the two row-lists lib.edgelab.paired_evaluation.pair_eligible_observations
    expects, repurposing its control/candidate contract for MODEL-vs-
    MARKET scoring: "market_rows" (positionally passed as this call's
    `control_rows`) carries the market's own contemporaneous executable-
    price probability under the key 'modelFairProbability' (the field
    name evaluate_probability_model_pair reads by default); "model_rows"
    (positionally `candidate_rows`) carries the model's own
    modelFairProbability unchanged. Both lists are built from the SAME
    source rows sharing the SAME identity key (gameId, marketTicker,
    researchCheckpoint), so pairing is a perfect 1:1 match by
    construction (nControlOnly == nCandidateOnly == 0 always) -- this is
    not a claim about two DIFFERENT observation sets, it is reusing the
    paired-evaluator's identical-observations guarantee to keep the
    model/market comparison honest.

    THIS IS NOT the experiment's registered controlModelId/candidate
    concept (lib.edgelab.control_identity) -- see module docstring.
    """
    market_rows, model_rows = [], []
    for r in rows:
        outcome = 1 if r["settlementResult"] == "YES" else 0
        identity = {
            "gameId": r["gameId"], "marketTicker": r["marketTicker"], "researchCheckpoint": r["researchCheckpoint"],
            "gameDate": r["gameDate"], "outcome": outcome,
        }
        market_rows.append({**identity, "modelFairProbability": r["executableYesPrice"]})
        model_rows.append({**identity, "modelFairProbability": r["modelFairProbability"]})
    pairing = pe.pair_eligible_observations(market_rows, model_rows)
    return pairing


def paired_model_vs_market(rows, n_resamples=None, seed=None):
    pairing = _model_vs_market_pairing(rows)
    return pe.evaluate_probability_model_pair(pairing, n_resamples=n_resamples, seed=seed), pairing


# ── Segment-level (BH-corrected) significance for exploratory screening ──

def _clustered_bootstrap_two_sided_pvalue(rows, cluster_key="gameId", n_resamples=DEFAULT_BOOTSTRAP_RESAMPLES, seed=DEFAULT_BOOTSTRAP_SEED):
    """
    Empirical two-sided game-clustered bootstrap p-value for
    H0: mean(modelBrier) - mean(marketBrier) == 0, using the SAME
    cluster-resampling discipline (and default seed/resample-count
    constants) as lib.edgelab.research_stats.game_clustered_bootstrap_ci
    -- not a new bootstrap engine, the same resampling idea applied to a
    p-value instead of a percentile interval, scoped to this experiment
    script (per Milestone 0A: exploratory-vs-confirmatory false-discovery
    handling is explicitly left to each experiment, not solved generically
    by the framework).
    """
    from lib.edgelab.replay import brier_score

    rows_by_cluster = defaultdict(list)
    for r in rows:
        key = r.get("gameId")
        if key is not None:
            rows_by_cluster[key].append(r)
    clusters = sorted(rows_by_cluster.keys(), key=str)
    if not clusters:
        return None

    def _delta(rows_subset):
        valid = [r for r in rows_subset if r["modelFairProbability"] is not None and r["executableYesPrice"] is not None]
        if not valid:
            return None
        outcomes = [1 if r["settlementResult"] == "YES" else 0 for r in valid]
        model_mean = sum(brier_score(r["modelFairProbability"], o) for r, o in zip(valid, outcomes)) / len(valid)
        market_mean = sum(brier_score(r["executableYesPrice"], o) for r, o in zip(valid, outcomes)) / len(valid)
        return model_mean - market_mean

    rng = random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled = [rng.choice(clusters) for _ in clusters]
        resampled_rows = [row for c in sampled for row in rows_by_cluster[c]]
        v = _delta(resampled_rows)
        if v is not None:
            estimates.append(v)
    if not estimates:
        return None
    n = len(estimates)
    frac_le_0 = sum(1 for e in estimates if e <= 0) / n
    frac_ge_0 = sum(1 for e in estimates if e >= 0) / n
    return min(2 * min(frac_le_0, frac_ge_0), 1.0)


def benjamini_hochberg(pvalues_by_label, q=FALSE_DISCOVERY_Q):
    """Standard BH step-up procedure. Returns {label: significantAtQ (bool)}; a label with pvalue None is never marked significant."""
    items = sorted(((label, p) for label, p in pvalues_by_label.items() if p is not None), key=lambda pair: pair[1])
    m = len(items)
    max_k = 0
    for i, (_label, p) in enumerate(items, start=1):
        if p <= (i / m) * q:
            max_k = i
    significant_labels = {label for label, _p in items[:max_k]}
    return {label: (label in significant_labels) for label in pvalues_by_label}


# ── Per-segment metric bundle (the "same fixed analysis", spec section 5/6) ─

def analyze_segment(rows, label):
    """
    The full metric set spec section 5 requires, for one bucket/segment
    of `rows` (already filtered to the usable population). Returns a
    dict; never raises on an empty/tiny segment -- degrades honestly via
    sample_size_status/insufficient labeling instead.
    """
    n = len(rows)
    independent_games = independent_unit_count(rows, key="gameId")
    independent_dates = len({r.get("gameDate") for r in rows if r.get("gameDate")})

    result = {
        "label": label,
        "rawRows": n,
        "independentGames": independent_games,
        "independentDates": independent_dates,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
        "interpretability": (
            "INSUFFICIENT" if independent_games < MIN_GAMES_EXPLORATORY else
            "EXPLORATORY" if independent_games < MIN_GAMES_INTERPRETABLE else
            "INTERPRETABLE" if independent_games < MIN_GAMES_SUBSTANTIAL else
            "SUBSTANTIAL"
        ),
    }
    if n == 0:
        result["secondaryFeeAdjustedEconomics"] = _bucket_economics(rows)  # rows is [] here -- _bucket_economics handles that gracefully, never crashes
        return result

    edges = [r["contemporaneousEdge"] for r in rows if r["contemporaneousEdge"] is not None]
    model_probs = [r["modelFairProbability"] for r in rows if r["modelFairProbability"] is not None]
    outcomes01 = [1 if r["settlementResult"] == "YES" else 0 for r in rows]

    result["meanDeclaredEdge"] = round(sum(edges) / len(edges), 4) if edges else None
    result["meanModelProbability"] = round(sum(model_probs) / len(model_probs), 4) if model_probs else None
    result["observedHitRate"] = round(sum(outcomes01) / n, 4) if n else None
    result["calibrationGap"] = (
        round(result["observedHitRate"] - result["meanModelProbability"], 4)
        if result["observedHitRate"] is not None and result["meanModelProbability"] is not None else None
    )

    model_pairs = [(r["modelFairProbability"], (1 if r["settlementResult"] == "YES" else 0)) for r in rows]
    market_pairs = [(r["executableYesPrice"], (1 if r["settlementResult"] == "YES" else 0)) for r in rows]
    model_brier, model_logloss = brier_and_log_loss_summary(model_pairs)
    market_brier, market_logloss = brier_and_log_loss_summary(market_pairs)
    result["modelBrierScore"] = model_brier
    result["modelLogLoss"] = model_logloss
    result["marketBenchmarkBrierScore"] = market_brier
    result["marketBenchmarkLogLoss"] = market_logloss
    result["modelCalibrationErrorECE"] = expected_calibration_error(model_pairs)
    result["marketCalibrationErrorECE"] = expected_calibration_error(market_pairs)

    n_resamples = 500 if independent_games >= 5 else 0
    if n_resamples:
        evaluation, pairing = paired_model_vs_market(rows, n_resamples=n_resamples, seed=DEFAULT_BOOTSTRAP_SEED)
        result["pairedBrierDelta_modelMinusMarket"] = evaluation["pairedDelta"]["brierScore"]
        result["pairedLogLossDelta_modelMinusMarket"] = evaluation["pairedDelta"]["logLoss"]
        result["pairedDeltaConfidenceInterval90"] = evaluation["pairedDeltaConfidenceInterval"]
        assert pairing["nControlOnly"] == 0 and pairing["nCandidateOnly"] == 0, "model/market pairing must always be a perfect 1:1 match by construction"
    else:
        result["pairedBrierDelta_modelMinusMarket"] = round(model_brier - market_brier, 6) if model_brier is not None and market_brier is not None else None
        result["pairedLogLossDelta_modelMinusMarket"] = round(model_logloss - market_logloss, 6) if model_logloss is not None and market_logloss is not None else None
        result["pairedDeltaConfidenceInterval90"] = {"low": None, "high": None, "method": "TOO_FEW_GAMES_FOR_BOOTSTRAP", "metric": "brierScoreDelta"}

    # Secondary evidence only (spec section 5: "Do not optimize historical ROI").
    result["secondaryFeeAdjustedEconomics"] = _bucket_economics(rows)
    return result


def _bucket_economics(rows):
    """SECONDARY evidence: fee-adjusted hypothetical P/L/ROI for buying YES at the executable price whenever the model's declared edge fell in this segment, sized at kf.DEFAULT_RESEARCH_ORDER_SIZE per row, via the canonical kf.simulate_settlement_order -- no second fee formula."""
    order_size = kf.DEFAULT_RESEARCH_ORDER_SIZE
    total_pl, total_cash, n_settled = 0.0, 0.0, 0
    for r in rows:
        price = r["executableYesPrice"]
        won = r["settlementResult"] == "YES"
        sim = kf.simulate_settlement_order(order_size, price, won, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
        if sim is None:
            continue
        total_pl += sim["netProfitLoss"]
        total_cash += sim["actualCashConsumed"]
        n_settled += 1
    roi = (total_pl / total_cash) if total_cash else None
    return {
        "orderSizeAssumption": order_size,
        "nSettledOrdersSimulated": n_settled,
        "totalHypotheticalPl": round(total_pl, 4),
        "totalCashConsumed": round(total_cash, 4),
        "hypotheticalRoi": round(roi, 4) if roi is not None else None,
        "warning": "SECONDARY evidence only -- never the primary basis for a disposition. See paired Brier/log-loss delta above for the primary predictive-advantage evidence.",
    }


# ── Primary + segmentation analyses ─────────────────────────────────────

def primary_bucket_analysis(usable):
    buckets = []
    for label, _low, _high in EDGE_BUCKETS:
        rows = [r for r in usable if assign_edge_bucket(r["contemporaneousEdge"]) == label]
        buckets.append(analyze_segment(rows, label))
    overall = analyze_segment(usable, "ALL")
    return buckets, overall


def family_segmentation(usable):
    families = sorted({r["canonicalMarketFamily"] for r in usable if r.get("canonicalMarketFamily")})
    per_family = {fam: analyze_segment([r for r in usable if r["canonicalMarketFamily"] == fam], fam) for fam in families}

    # Same fixed edge-bucket breakdown WITHIN each family (finer-grained, expect many INSUFFICIENT cells).
    per_family_per_bucket = {}
    for fam in families:
        fam_rows = [r for r in usable if r["canonicalMarketFamily"] == fam]
        per_family_per_bucket[fam] = {
            label: analyze_segment([r for r in fam_rows if assign_edge_bucket(r["contemporaneousEdge"]) == label], f"{fam}/{label}")
            for label, _low, _high in EDGE_BUCKETS
        }

    # False-discovery treatment (BH, q=0.10) over the family-level model-vs-market Brier delta.
    pvalues = {fam: _clustered_bootstrap_two_sided_pvalue([r for r in usable if r["canonicalMarketFamily"] == fam]) for fam in families}
    significance = benjamini_hochberg(pvalues, q=FALSE_DISCOVERY_Q)
    fdr_screening = {
        fam: {"pValueApprox": pvalues[fam], "significantAtQ10_BH": significance[fam]}
        for fam in families
    }
    return per_family, per_family_per_bucket, fdr_screening


def checkpoint_segmentation(usable):
    checkpoints = sorted({r["researchCheckpoint"] for r in usable if r.get("researchCheckpoint")})
    return {c: analyze_segment([r for r in usable if r["researchCheckpoint"] == c], c) for c in checkpoints}


def price_band_segmentation(usable):
    return {
        label: analyze_segment([r for r in usable if assign_price_band(r["executableYesPrice"]) == label], label)
        for label, _low, _high in EXECUTABLE_PRICE_BANDS
    }


# ── Important checks (spec section 7) ───────────────────────────────────

def important_checks(bucket_results, family_results, usable, evaluations):
    checks = {}

    ordered = [b for b in bucket_results if b["rawRows"] > 0]
    deltas = [(b["label"], b["pairedBrierDelta_modelMinusMarket"]) for b in ordered if b["pairedBrierDelta_modelMinusMarket"] is not None]
    checks["bucketDeltaSequence"] = deltas
    monotonic_decreasing = all(deltas[i][1] >= deltas[i + 1][1] - 1e-9 for i in range(len(deltas) - 1)) if len(deltas) > 1 else None
    checks["monotonicNonIncreasingDeltaAcrossBuckets"] = monotonic_decreasing

    checks["invertedOrFlatBuckets"] = [
        b["label"] for i, b in enumerate(ordered)
        if i > 0 and b["pairedBrierDelta_modelMinusMarket"] is not None and ordered[i - 1]["pairedBrierDelta_modelMinusMarket"] is not None
        and b["pairedBrierDelta_modelMinusMarket"] > ordered[i - 1]["pairedBrierDelta_modelMinusMarket"] + 1e-9
    ]

    checks["familiesWhereModelWorseThanMarketOverall"] = [
        fam for fam, r in family_results.items()
        if r.get("pairedBrierDelta_modelMinusMarket") is not None and r["pairedBrierDelta_modelMinusMarket"] > 0
    ]

    checks["calibrationGapByBucket"] = {b["label"]: b.get("calibrationGap") for b in ordered}

    checks["gameConcentrationWarningByBucket"] = {
        b["label"]: b["sampleSizeStatus"]["gameConcentrationWarning"] for b in ordered
    }

    checks["qualityTierByBucket"] = _quality_tier_by_bucket(usable, evaluations)

    checks["artifactSourceBreakdown"] = _artifact_source_breakdown(usable, evaluations)

    return checks


# ── Registration (must run before any result is examined) ──────────────

def _current_git_commit_sha():
    import subprocess
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, Exception):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _config_fingerprint():
    path = os.path.join("config", "rules.json")
    if not os.path.exists(path):
        return rlids.config_fingerprint(config_text="MISSING_config/rules.json")
    with open(path) as f:
        return rlids.config_fingerprint(config_text=f.read())


def register_control_and_experiment(evaluations):
    """
    Registers the control identity and the experiment definition. Called
    unconditionally, BEFORE any bucket/segment result is computed (see
    main()) -- the literal spec requirement "Register the experiment
    before examining its results."

    identity_confidence=HISTORICAL_AMBIGUOUS: the real corpus (checked
    directly, not assumed) carries 105 distinct modelCommitSha values
    across its 26 committed dates (continuous deployment) -- there is no
    single exact commit this registration can honestly claim for the
    whole corpus. sourceGitCommitSha records the commit this SCRIPT is
    running from (this registration's own provenance); the true
    per-row commit diversity is documented explicitly in the report's
    limitations instead of being papered over.
    """
    commit_counts = Counter(e.get("modelCommitSha") for e in evaluations if e.get("modelCommitSha"))
    config_versions = sorted({e.get("modelConfigVersion") for e in evaluations if e.get("modelConfigVersion")})

    control = ctrl_id.build_control_registration(
        name="edgelab_production_model_corpus_2026_08",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version=(config_versions[0] if len(config_versions) == 1 else "MULTIPLE_" + "_".join(config_versions)),
        config_fingerprint=_config_fingerprint(),
        probability_adapter_identity="scripts/build_market_ledger.py;lib.kalshi_probability_adapters.adapt_contract",
        model_engine_family="rules_based_v1_11_market_plus_full_universe_extension",
        required_input_provenance=[
            "archived_kalshi_market_observation", "kalshi_bid_ask_executable_price",
            "model_evaluation_probability_pipeline_derived", "model_evaluation_probability_prospective_snapshot",
            "settlement_outcome", "kalshi_closing_market_quote",
        ],
        identity_confidence=ctrl_id.IDENTITY_HISTORICAL_AMBIGUOUS,
        description=(
            f"The production EdgeLab model corpus as observed across every committed research date this "
            f"experiment analyzes. {len(commit_counts)} distinct modelCommitSha values were found across the "
            f"underlying ModelEvaluation records (continuous deployment) -- this registration deliberately does "
            f"NOT claim one exact commit for the whole corpus (see identityConfidence)."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title=EXPERIMENT_TITLE,
        hypothesis=(
            "Larger declared model edge (contemporaneousEdge, model probability minus this checkpoint's own "
            "executable market price) corresponds to a larger genuine predictive advantage of the model over "
            "the market (a more negative model-minus-market paired Brier/log-loss delta), and this relationship "
            "is not uniform across canonical market families."
        ),
        research_question=(
            "Does larger declared model edge correspond to genuinely greater predictive advantage over the "
            "Kalshi market, and does that relationship differ by market family?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        target_population="Every settled MLB Kalshi opportunity row in the archived observation corpus with a causally-valid (no-look-ahead) model probability",
        market_families=["game_result", "team_total", "first_inning_run", "inning_result", "inning_total", "winning_margin", "pitcher_strikeouts", "pitcher_outs"],
        eligibility_criteria=[
            "settlementStatus == SETTLED with settlementResult in (YES, NO)",
            "modelEvaluationAvailable == True (temporal_alignment causal join succeeded)",
            "contemporaneousEdge is not null (both modelFairProbability and this checkpoint's own executable price available)",
            "side defaults/resolves to YES (no NO-side causally-linked rows exist in this corpus as of this experiment)",
        ],
        exclusion_criteria=[
            "Unsettled or push/void markets",
            "No causally-valid ModelEvaluation for this checkpoint (NO_EVALUATIONS_FOR_TICKER / NO_CAUSAL_TIMESTAMP_ON_ANY_CANDIDATE / ALL_EVALUATIONS_AFTER_CHECKPOINT)",
            "Missing executable price at this checkpoint",
        ],
        prediction_checkpoints=["FIRST_DAILY", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5", "LINEUP_CONFIRMATION", "CLOSING"],
        primary_metric="pairedBrierDelta_modelMinusMarket (game-clustered, by preregistered edge bucket)",
        secondary_metrics=[
            "pairedLogLossDelta_modelMinusMarket", "modelCalibrationErrorECE", "marketCalibrationErrorECE",
            "observedHitRate", "secondaryFeeAdjustedEconomics (ROI/P&L, explicitly non-primary)",
        ],
        chronological_split_policy="NONE -- this milestone is a pooled retrospective descriptive/exploratory analysis over the full corpus, not a chronological train/holdout confirmatory test (see evidenceLevel/experimentType)",
        minimum_sample_requirement={"independentGames": MIN_GAMES_EXPLORATORY, "independentDates": 10},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT,
            "kalshi_bid_ask_executable_price": pit.ROLE_PREDICTIVE_INPUT,
            "model_evaluation_probability_pipeline_derived": pit.ROLE_PREDICTIVE_INPUT,
            "model_evaluation_probability_prospective_snapshot": pit.ROLE_PREDICTIVE_INPUT,
            "settlement_outcome": pit.ROLE_EVALUATION_TARGET,
            "kalshi_closing_market_quote": pit.ROLE_EVALUATION_TARGET,
        },
        notes=(
            "Registered evidenceLevel is E1_RECONSTRUCTED_RETROSPECTIVE, not E2, because "
            "model_evaluation_probability_prospective_snapshot carries pitStatus=PROSPECTIVE_ONLY in the "
            "Milestone 0A PIT manifest, which lib.edgelab.pit_provenance.check_predictive_compatibility does not "
            "certify as PIT-safe for a PREDICTIVE_INPUT role at E2/E3, and ~60% of this experiment's usable rows "
            "come from that pathway. A recommendations-artifactSource-only subset (E2-eligible) is reported "
            "separately as a secondary/robustness segment. Do not force E2 by omitting this input's honest status."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    reg.register_experiment(definition)
    return control, definition


# ── Report assembly ──────────────────────────────────────────────────────

def determine_disposition(overall, checks):
    """
    Never SHADOW_CANDIDATE/PROMOTION_CANDIDATE for a control-only
    validation experiment (spec: "Do NOT recommend production changes
    yet. Even a strong result should only inform the next research
    stage.") -- only REJECT or RESEARCH_CANDIDATE are ever returned.
    REJECT only for a genuine material problem (inverted monotonicity
    AND the model performing worse than the market overall); otherwise
    RESEARCH_CANDIDATE regardless of how strong or weak the positive
    signal looks, since evidence level E1/EXPLORATORY never supports
    more than "worth continued research" per lib.edgelab.dispositions.
    """
    overall_delta = overall.get("pairedBrierDelta_modelMinusMarket")
    material_problem = overall_delta is not None and overall_delta > 0 and checks.get("monotonicNonIncreasingDeltaAcrossBuckets") is False
    return disp.REJECT if material_problem else disp.RESEARCH_CANDIDATE


def classify_edge_signal(overall, checks, coverage):
    """One of the four spec-mandated labels -- a plain-language summary judgment, computed from already-reported numbers, never a hidden extra computation."""
    if coverage["independentGamesUsable"] < MIN_GAMES_EXPLORATORY:
        return "WEAK / UNPROVEN"
    overall_delta = overall.get("pairedBrierDelta_modelMinusMarket")
    ci = overall.get("pairedDeltaConfidenceInterval90") or {}
    ci_excludes_zero_favorably = ci.get("high") is not None and ci["high"] < 0
    if overall_delta is None:
        return "WEAK / UNPROVEN"
    if overall_delta > 0:
        return "MATERIAL PROBLEM FOUND" if checks.get("monotonicNonIncreasingDeltaAcrossBuckets") is False else "WEAK / UNPROVEN"
    if ci_excludes_zero_favorably and checks.get("monotonicNonIncreasingDeltaAcrossBuckets"):
        return "STRONGLY VALID"
    if overall_delta < 0:
        return "PARTIALLY VALID"
    return "WEAK / UNPROVEN"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    dates = _discover_dates()
    if args.start_date:
        dates = [d for d in dates if d >= args.start_date]
    if args.end_date:
        dates = [d for d in dates if d <= args.end_date]
    if not dates:
        print("No observation dates found for the requested range.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets = _load_universe(dates)

    # ---- Registration FIRST, before any result is examined. ----
    control, definition = register_control_and_experiment(evaluations)

    all_rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations, recommendations=recommendations, bets=bets, games=games)
    usable, coverage = usable_rows_and_coverage(all_rows)

    bucket_results, overall = primary_bucket_analysis(usable)
    per_family, per_family_per_bucket, family_fdr = family_segmentation(usable)
    per_checkpoint = checkpoint_segmentation(usable)
    per_price_band = price_band_segmentation(usable)
    checks = important_checks(bucket_results, per_family, usable, evaluations)

    # Robustness segment: recommendations-artifactSource-only (E2-eligible pathway).
    eval_by_id = {e["modelEvaluationId"]: e for e in evaluations if e.get("modelEvaluationId")}
    pipeline_only = [r for r in usable if eval_by_id.get(r["modelEvaluationId"], {}).get("artifactSource") == "recommendations"]
    pipeline_only_result = analyze_segment(pipeline_only, "PIPELINE_DERIVED_ONLY_E2_ELIGIBLE_SUBSET")

    disposition = determine_disposition(overall, checks)
    signal_classification = classify_edge_signal(overall, checks, coverage)

    pairing_for_report = _model_vs_market_pairing(usable)
    overall_evaluation, _ = paired_model_vs_market(usable, n_resamples=2000, seed=DEFAULT_BOOTSTRAP_SEED)

    report = er.build_experiment_report(
        experiment=definition, control_registration=control, candidate_registration=None,
        pairing_result=pairing_for_report, probability_evaluation=overall_evaluation,
        disposition=disposition, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        evaluation_date_range=[min(dates), max(dates)] if dates else None,
        pit_provenance_status="MIXED -- see secondaryMetrics.artifactSourceBreakdown; evidenceLevel capped at E1 because of the PROSPECTIVE_ONLY-classified prospective_snapshot pathway",
        pit_limitations=[
            "model_evaluation_probability_prospective_snapshot carries pitStatus=PROSPECTIVE_ONLY in the Milestone 0A manifest; ~60% of usable rows come from this pathway, capping evidenceLevel at E1 for the pooled analysis.",
            "season_to_date_stats/hitter_snapshot/pitcher_snapshot inputs feeding the model's own probability computation are marked UNKNOWN_REQUIRES_AUDIT in the PIT manifest and were not independently re-audited by this experiment.",
        ],
        methodological_limitations=[
            "105 distinct modelCommitSha values were observed across the corpus (continuous deployment) -- controlModelId's identityConfidence is HISTORICAL_AMBIGUOUS, not EXACT.",
            "Market benchmark probability is the executable YES ask/bid-fallback price at this checkpoint (matching lib.edgelab.research_dataset's own contemporaneousEdge convention), not a vig-free midpoint -- this may modestly overstate apparent model edge in wide-spread markets.",
            "This is a pooled retrospective analysis with no chronological train/holdout split -- a walk-forward confirmatory follow-up would be required to reach E3.",
            f"Independent-game counts range from tiny to moderate per segment (overall {coverage['independentGamesUsable']} games / {coverage['independentDatesUsable']} dates) -- see each segment's own interpretability label.",
        ],
        leakage_warnings=[],
        secondary_metrics={
            "coverage": coverage,
            "edgeBuckets": bucket_results,
            "overall": overall,
            "byMarketFamily": per_family,
            "byMarketFamilyByEdgeBucket": per_family_per_bucket,
            "familyFalseDiscoveryScreening": family_fdr,
            "byCheckpoint": per_checkpoint,
            "byExecutablePriceBand": per_price_band,
            "pipelineDerivedOnlySubset_E2Eligible": pipeline_only_result,
            "importantChecks": checks,
            "edgeSignalClassification": signal_classification,
        },
        market_economic_metrics={"overall": overall["secondaryFeeAdjustedEconomics"], "byEdgeBucket": {b["label"]: b["secondaryFeeAdjustedEconomics"] for b in bucket_results}},
        generated_at=REGISTRATION_TIMESTAMP,
    )
    er.write_experiment_report(report)

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(MACHINE_REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    markdown = render_markdown_summary(report, definition, control)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(MARKDOWN_REPORT_PATH, "w") as f:
        f.write(markdown)

    print(f"Experiment {EXPERIMENT_ID} registered. Report: {report['experimentReportId']}")
    print(f"Usable rows: {coverage['usableRows']} | independent games: {coverage['independentGamesUsable']} | dates: {coverage['independentDatesUsable']}")
    print(f"Overall model-vs-market paired Brier delta: {overall.get('pairedBrierDelta_modelMinusMarket')}")
    print(f"Edge signal classification: {signal_classification} | disposition: {disposition}")
    return 0


def render_markdown_summary(report, definition, control):
    sm = report["secondaryMetrics"]
    coverage = sm["coverage"]
    overall = sm["overall"]
    lines = []
    lines.append(f"# {EXPERIMENT_ID}: {EXPERIMENT_TITLE}\n")
    lines.append(f"Research question: {definition['researchQuestion']}\n")
    lines.append(f"Evidence level: **{report['evidenceLevel']}** | Experiment type: {definition['experimentType']} | Disposition: **{report['disposition']}**\n")
    lines.append(f"Edge signal classification: **{sm['edgeSignalClassification']}**\n")
    lines.append("RESEARCH ONLY. productionBehaviorChanged: false. No production model, recommendation, fee, staking, eligibility, or risk-gate logic was changed by this experiment.\n")

    lines.append("## Usable data\n")
    lines.append(f"- Total archived opportunity rows: {coverage['totalArchivedOpportunityRows']:,}")
    lines.append(f"- Rows with settlement: {coverage['rowsWithSettlement']:,}")
    lines.append(f"- Rows with a causally-valid model probability: {coverage['rowsWithCausallyValidModelProbability']:,}")
    lines.append(f"- Usable rows (settled + causal model probability + computable edge): **{coverage['usableRows']:,}**")
    lines.append(f"- Independent games: **{coverage['independentGamesUsable']}** | independent dates: **{coverage['independentDatesUsable']}** | unique tickers: {coverage['uniqueTickersUsable']}")
    lines.append(f"- Market families represented: {', '.join(coverage['marketFamiliesUsable'])}")
    lines.append(f"- Rows excluded for PIT/timing reasons: {coverage['rowsExcludedForPitTimingReasons']}\n")

    lines.append("## Primary result (overall, all buckets pooled)\n")
    lines.append(f"- Model Brier score: {overall.get('modelBrierScore')} | Market benchmark Brier score: {overall.get('marketBenchmarkBrierScore')}")
    lines.append(f"- Paired Brier delta (model - market, negative = model better): **{overall.get('pairedBrierDelta_modelMinusMarket')}**")
    lines.append(f"- 90% game-clustered bootstrap CI on delta: {overall.get('pairedDeltaConfidenceInterval90')}")
    lines.append(f"- Paired log-loss delta: {overall.get('pairedLogLossDelta_modelMinusMarket')}\n")

    lines.append("## Edge bucket table\n")
    lines.append("| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for b in sm["edgeBuckets"]:
        ci = b.get("pairedDeltaConfidenceInterval90") or {}
        lines.append(
            f"| {b['label']} | {b['rawRows']} | {b['independentGames']} | {b['independentDates']} | "
            f"{b.get('meanDeclaredEdge')} | {b.get('observedHitRate')} | {b.get('modelBrierScore')} | "
            f"{b.get('marketBenchmarkBrierScore')} | {b.get('pairedBrierDelta_modelMinusMarket')} | "
            f"[{ci.get('low')}, {ci.get('high')}] | {b['interpretability']} |"
        )
    lines.append("")

    lines.append("## Market family findings\n")
    lines.append("| Family | Rows | Games | Dates | Paired delta | BH-significant (q=0.10) | Interpretability |")
    lines.append("|---|---|---|---|---|---|---|")
    for fam, r in sm["byMarketFamily"].items():
        fdr = sm["familyFalseDiscoveryScreening"].get(fam, {})
        lines.append(f"| {fam} | {r['rawRows']} | {r['independentGames']} | {r['independentDates']} | {r.get('pairedBrierDelta_modelMinusMarket')} | {fdr.get('significantAtQ10_BH')} | {r['interpretability']} |")
    lines.append("")

    checks = sm["importantChecks"]
    lines.append("## Important checks\n")
    lines.append(f"- Monotonic non-increasing delta across buckets: **{checks['monotonicNonIncreasingDeltaAcrossBuckets']}**")
    lines.append(f"- Inverted/flat buckets (delta increased vs the prior bucket): {checks['invertedOrFlatBuckets']}")
    lines.append(f"- Families where model performed worse than market: {checks['familiesWhereModelWorseThanMarketOverall']}")
    lines.append(f"- Game-concentration warning by bucket: {checks['gameConcentrationWarningByBucket']}")
    lines.append(f"- qualityTier by bucket: {checks['qualityTierByBucket']}")
    lines.append(f"- artifactSource breakdown (PIT pathway mix): {checks['artifactSourceBreakdown']}\n")

    lines.append("## Robustness: pipeline-derived-only subset (E2-eligible pathway)\n")
    p = sm["pipelineDerivedOnlySubset_E2Eligible"]
    lines.append(f"- Rows: {p['rawRows']} | Games: {p['independentGames']} | Dates: {p['independentDates']} | Interpretability: {p['interpretability']}")
    lines.append(f"- Paired Brier delta: {p.get('pairedBrierDelta_modelMinusMarket')}\n")

    lines.append("## Secondary evidence: fee-aware hypothetical economics (NOT the primary basis for any conclusion)\n")
    econ = report["marketEconomicMetrics"]["overall"]
    lines.append(f"- Hypothetical ROI (overall, fee-adjusted, {econ['nSettledOrdersSimulated']} simulated orders): {econ.get('hypotheticalRoi')}\n")

    lines.append("## Limitations\n")
    for item in report["methodologicalLimitations"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## PIT limitations\n")
    for item in report["pitLimitations"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("## Disposition\n")
    lines.append(
        f"**{report['disposition']}** -- per Milestone 0A policy, this control-only validation experiment can "
        f"never be assigned SHADOW_CANDIDATE/PROMOTION_CANDIDATE regardless of result strength. This experiment "
        f"does not recommend any production change; it informs the next research stage only.\n"
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
