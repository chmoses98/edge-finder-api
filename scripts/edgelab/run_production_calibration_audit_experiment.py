#!/usr/bin/env python3
"""
scripts/edgelab/run_production_calibration_audit_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0022: "Production Probability
Calibration & Market-Relative Skill". RESEARCH ONLY. NO production
changes, no staking/execution/fee-logic changes, no bet-behavior
changes.

CORE QUESTION (the most profit-relevant question answerable from data
that already exists in this repository): are PRODUCTION's own archived
pregame probabilities (data/edgelab/model_evaluations/, EVALUATED rows)
(a) well calibrated against settled Kalshi outcomes
    (data/edgelab/settlements/), and
(b) skillful relative to Kalshi's own contemporaneous prices
    (each row's own archived marketImpliedProbability),
by market family and price band?

WHY THIS MATTERS FOR THE REMAINDER OF 2026: this is not a proxy study.
These are the EXACT probabilities production used to pick bets, archived
prospectively (each row was written before its game started -- walk-
forward by construction), joined to real Kalshi settlement outcomes.
Its findings say directly which market families production's numbers can
currently be trusted in, which families the market is beating us in, and
where systematic calibration bias exists -- i.e., bet-FILTERING
information usable (after human review) for the remaining ~30 regular-
season days plus the postseason.

EVIDENCE LEVEL: E4_PROSPECTIVE_SHADOW -- per this repository's own
PIT-provenance framework (lib.edgelab.pit_provenance), prospectively-
captured predictive inputs support E4/E5 ('a live, forward-looking
claim'), never E2/E3 (which assert proven HISTORICAL point-in-time
depth). These rows are the strongest possible instance of prospective
evidence: production's OWN live pregame predictions, archived before
their outcomes existed, evaluated only after settlement. No shadow
variant model is involved and nothing is historically reconstructed --
the E4 label here means exactly 'prospectively captured, prospectively
evaluated'.

DESIGN -- AUDIT, NOT FITTING: nothing is fit in this experiment. There
is no candidate model, no recalibration map, no tuned threshold. The
preregistered structure is measurement + replication:
  DEV half:  settlement dates 2026-08-02 .. 2026-08-17
  VAL half:  settlement dates 2026-08-18 .. 2026-08-28
  FORWARD:   dates settling after this run (the genuine holdout --
             preregistered here, evaluated in a future session as the
             season's remaining games settle; nothing about it is
             computed now).
A family-level finding "stands" only if directionally consistent in BOTH
halves (preregistered replication rule). Benjamini-Hochberg FDR is
applied across families for the model-vs-market paired-Brier tests.

PRIMARY ROW RULE (preregistered): one row per marketTicker -- the LAST
EVALUATED row by createdAt (production's most-informed pregame
estimate). Robustness: the FIRST row per ticker is evaluated as a
secondary comparison (does calibration improve as information arrives?).

PROBABILITY SCALE: modelFairProbability and marketImpliedProbability are
archived on a 0-100 scale; both are divided by 100 here. Rows missing
either probability, or without a YES/NO settled outcome, are excluded
and counted.

ECONOMICS ARE SECONDARY AND DESCRIPTIVE: fee-aware expected value is
reported for FIXED, preregistered model-market disagreement bands using
lib.edgelab.kalshi_fees.taker_fee -- no threshold is optimized, nothing
is fit to realized ROI, and no finding is selected on ROI.

MAX IMPLEMENTATION READINESS this experiment can assign: LEVEL 1
(shadow/monitoring candidate). It cannot promote anything to production.
"""
import json
import math
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.storage import read_records
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    independent_unit_count,
    sample_size_status,
    expected_calibration_error,
    brier_and_log_loss_summary,
    calibration_slope_intercept,
    game_clustered_bootstrap_ci,
)

EXPERIMENT_ID = "MLB-RSCH-0022"
REGISTRATION_TIMESTAMP = "2026-08-28T19:50:00Z"

MODEL_EVALUATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "model_evaluations")
SETTLEMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "settlements")

DEV_DATE_MAX = "2026-08-17"   # DEV: settlement dates <= this
VAL_DATE_MAX = "2026-08-28"   # VAL: settlement dates in (DEV_DATE_MAX, VAL_DATE_MAX]
# FORWARD (genuine holdout): settlement dates > VAL_DATE_MAX -- preregistered,
# NOT computed in this run; evaluated in a future session as games settle.

# Preregistered interpretation floor: a family is PRIMARY-interpretable only
# with at least this many unique tickers AND games in the pooled sample.
MIN_TICKERS_PRIMARY = 100
MIN_GAMES_PRIMARY = 30

PRICE_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
DISAGREEMENT_BANDS = ((0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01))  # |model - market|, fixed, never optimized
FDR_ALPHA = 0.10
BOOTSTRAP_RESAMPLES_FOR_P = 2000

# Preregistered bet-filter dimensions (descriptive; no thresholds fit):
FILTER_DIMENSIONS = ("confidence", "dataQuality", "lineupConfirmationState")


def _current_git_commit_sha():
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def register_experiment():
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None
    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0022_production_calibration_audit_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0022 production calibration audit v1: PRODUCTION's own archived "
                        "EVALUATED pregame probabilities (last-per-ticker), joined to settled Kalshi "
                        "outcomes, audited for calibration and market-relative skill by family/price band. "
                        "AUDIT ONLY -- nothing fit, no recalibration map, no threshold tuned."
        ),
        probability_adapter_identity="production pipeline's own archived modelFairProbability (scripts/build_market_ledger.py at each row's own archived modelCommitSha) -- consumed read-only, never recomputed",
        model_engine_family="production_probability_walk_forward_audit_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived", "model_evaluation_probability_prospective_snapshot", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Walk-forward audit of production's own archived pregame Kalshi-market probabilities: "
            "calibration against settled outcomes and paired proper-scoring skill versus the market's "
            "own contemporaneous price, by market family and fixed price band. The most directly "
            "profit-relevant measurement available from existing data for the remainder of 2026."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Production Probability Calibration & Market-Relative Skill",
        hypothesis=(
            "H1: production's archived pregame probabilities are NOT uniformly calibrated -- calibration "
            "quality differs materially by market family. H2: in at least one family production's "
            "probability is SKILLFUL relative to Kalshi's own price (paired Brier better, FDR-controlled), "
            "and in at least one family the market is better -- i.e., family identity carries real "
            "bet-filtering information. H3 (classic favorite-longshot direction, tested not assumed): "
            "miscalibration, where present, is worst in the extreme price bands."
        ),
        research_question=(
            "Which Kalshi market families can production's current probabilities be trusted in -- where are "
            "they calibrated, where do they beat the market's own price under proper scoring, and where is "
            "the market beating us?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every EVALUATED row in data/edgelab/model_evaluations/ (2026-08-01 .. 2026-08-28) whose "
            "marketTicker has a YES/NO settled outcome in data/edgelab/settlements/ -- last row per ticker "
            "primary. ~4,100 unique tickers across ~390 games and 13 archived market families."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin", "pitcher_props", "inning_markets", "f5_markets"],
        eligibility_criteria=[
            "evaluationStatus == EVALUATED with a non-null modelFairProbability AND marketImpliedProbability",
            "marketTicker has a YES/NO settled outcome (unsettled/None excluded and counted)",
            "primary row = LAST evaluation per marketTicker by createdAt; FIRST per ticker used only as a preregistered robustness comparison",
        ],
        exclusion_criteria=[
            "fitting of ANY kind -- no recalibration map, no threshold optimization, no ROI-fit selection",
            "families below the preregistered interpretation floor are reported descriptively only, never as primary findings",
            "no new API calls of any kind -- archived data only",
        ],
        prediction_checkpoints=["ARCHIVED_PRODUCTION_PREGAME"],
        primary_metric="per-family paired Brier delta (production probability minus Kalshi market-implied probability), game-clustered bootstrap CI, BH-FDR across families",
        secondary_metrics=[
            "per-family Brier/log-loss/expected-calibration-error for model and market",
            "fixed-price-band reliability (favorite-longshot diagnostic)",
            "fixed |model-market| disagreement-band outcome rates and fee-aware descriptive EV (taker fees, never optimized)",
            "preregistered filter dimensions (confidence, dataQuality, lineupConfirmationState) descriptive splits",
            "first-vs-last-evaluation-per-ticker robustness",
        ],
        chronological_split_policy=(
            f"DATE_BASED replication halves: DEV = settlement dates <= {DEV_DATE_MAX}, VAL = ({DEV_DATE_MAX}, {VAL_DATE_MAX}]. "
            "FORWARD (genuine holdout) = dates settling after this run -- preregistered, evaluated in a future session, "
            "not computed here. A family finding stands only if directionally consistent in both halves."
        ),
        minimum_sample_requirement={"independentGames": MIN_GAMES_PRIMARY},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "model_evaluation_probability_prospective_snapshot": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW: prospectively-captured live production predictions evaluated "
            "against subsequently-settled outcomes (the PIT framework's own designated level for "
            "PROSPECTIVE_ONLY predictive inputs); no shadow variant model involved, nothing reconstructed. AUDIT ONLY -- nothing fit. Economics secondary/descriptive via frozen taker-fee "
            "formula. Max implementation readiness assignable: LEVEL 1 (shadow/monitoring candidate). "
            "This is a probability-calibration experiment; Methodology V2's mean-metric rules do not apply "
            "(no expected-run mean candidate is being selected) -- proper scoring rules are the primary "
            "metrics throughout, which is what V2 itself requires for probability targets."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus assembly (archived data only -- zero network) ──────────────────

def load_settled_outcomes():
    """{marketTicker: {"outcome": 1|0, "settleDate": "YYYY-MM-DD", "gameId": ...}}
    from every settlement partition. YES->1, NO->0; None outcomes skipped
    (counted by the caller via len differences)."""
    out = {}
    unsettled = 0
    for fn in sorted(os.listdir(SETTLEMENTS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        settle_date = fn.split(".jsonl")[0]
        for d in read_records(os.path.join(SETTLEMENTS_DIR, fn)):
            t = d.get("marketTicker")
            if not t:
                continue
            if d.get("outcome") == "YES":
                out[t] = {"outcome": 1, "settleDate": settle_date, "gameId": d.get("gameId"), "marketFamily": d.get("marketFamily")}
            elif d.get("outcome") == "NO":
                out[t] = {"outcome": 0, "settleDate": settle_date, "gameId": d.get("gameId"), "marketFamily": d.get("marketFamily")}
            else:
                unsettled += 1
    return out, unsettled


def load_evaluated_rows():
    """Every EVALUATED model-evaluation row with both probabilities present
    (normalized 0-1). Returns (rows, n_excluded_missing_prob)."""
    rows = []
    excluded_missing = 0
    for fn in sorted(os.listdir(MODEL_EVALUATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in read_records(os.path.join(MODEL_EVALUATIONS_DIR, fn)):
            if d.get("evaluationStatus") != "EVALUATED":
                continue
            model_p, market_p = d.get("modelFairProbability"), d.get("marketImpliedProbability")
            if model_p is None or market_p is None:
                excluded_missing += 1
                continue
            rows.append({
                "marketTicker": d["marketTicker"], "marketFamily": d.get("marketFamily"),
                "gameId": d.get("gameId"), "createdAt": d.get("createdAt") or "",
                "modelP": round(float(model_p) / 100.0, 6), "marketP": round(float(market_p) / 100.0, 6),
                "confidence": d.get("confidence"), "dataQuality": d.get("dataQuality"),
                "lineupConfirmationState": d.get("lineupConfirmationState"),
            })
    return rows, excluded_missing


def build_audit_rows(evaluated_rows, outcomes, *, pick="last"):
    """One row per marketTicker (pick='last' primary / 'first' robustness),
    joined to its settled outcome. The settlement record's own marketFamily
    is preferred for family labeling (uniform naming across both the
    pipeline-derived and prospective-snapshot evaluation paths, whose own
    marketFamily field uses two different naming schemes)."""
    by_ticker = {}
    for r in evaluated_rows:
        t = r["marketTicker"]
        prev = by_ticker.get(t)
        if prev is None:
            by_ticker[t] = r
        elif (r["createdAt"] > prev["createdAt"]) == (pick == "last"):
            by_ticker[t] = r
    audit = []
    for t, r in by_ticker.items():
        o = outcomes.get(t)
        if o is None:
            continue
        audit.append(dict(
            r, outcome=o["outcome"], settleDate=o["settleDate"],
            family=o.get("marketFamily") or r.get("marketFamily") or "UNKNOWN",
            gameId=r.get("gameId") or o.get("gameId") or t.rsplit("-", 1)[0],
        ))
    return sorted(audit, key=lambda r: (r["settleDate"], r["marketTicker"]))


def split_rows(audit_rows):
    dev = [r for r in audit_rows if r["settleDate"] <= DEV_DATE_MAX]
    val = [r for r in audit_rows if DEV_DATE_MAX < r["settleDate"] <= VAL_DATE_MAX]
    return dev, val


# ── Metrics ───────────────────────────────────────────────────────────────

def _clustered_bootstrap_pvalue(rows, value_fn, *, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED, n_resamples=BOOTSTRAP_RESAMPLES_FOR_P):
    """Two-sided bootstrap sign p-value for value_fn's statistic, using the
    EXACT resampling scheme of research_stats.game_clustered_bootstrap_ci
    (whole clusters with replacement, deterministic seed) -- implemented
    locally because the canonical helper returns only the interval, and
    this experiment additionally needs the bootstrap distribution's own
    sign fractions for Benjamini-Hochberg across families. The canonical
    module is deliberately NOT modified."""
    import random as _random
    from collections import defaultdict as _dd
    rows_by_cluster = _dd(list)
    for r in rows:
        key = r.get(cluster_key)
        if key is not None:
            rows_by_cluster[key].append(r)
    clusters = sorted(rows_by_cluster.keys(), key=str)
    if not clusters:
        return None
    rng = _random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled = [rng.choice(clusters) for _ in clusters]
        value = value_fn([row for c in sampled for row in rows_by_cluster[c]])
        if value is not None:
            estimates.append(value)
    if not estimates:
        return None
    frac_pos = sum(1 for e in estimates if e > 0) / len(estimates)
    return round(min(1.0, 2 * min(frac_pos, 1 - frac_pos)), 4)


def family_metrics(rows):
    """Model + market proper scores, calibration, and the paired
    model-minus-market Brier delta with game-clustered CI + bootstrap
    two-sided p-value (for BH across families)."""
    model_pairs = [(r["modelP"], r["outcome"]) for r in rows]
    market_pairs = [(r["marketP"], r["outcome"]) for r in rows]
    paired = [{"gameId": r["gameId"], "d": (r["modelP"] - r["outcome"]) ** 2 - (r["marketP"] - r["outcome"]) ** 2} for r in rows]

    def _mean_delta(subset):
        return sum(x["d"] for x in subset) / len(subset) if subset else None

    point = _mean_delta(paired)
    lo, hi, _method = game_clustered_bootstrap_ci(paired, _mean_delta, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED) if paired else (None, None, None)
    p_two_sided = _clustered_bootstrap_pvalue(paired, _mean_delta) if paired else None

    n_games = independent_unit_count(rows, key="gameId")
    return {
        "n": len(rows), "independentGames": n_games,
        "sampleSizeStatus": sample_size_status(len(rows), independent_games=n_games),
        "model": brier_and_log_loss_summary(model_pairs),
        "market": brier_and_log_loss_summary(market_pairs),
        "modelEce": expected_calibration_error(model_pairs),
        "marketEce": expected_calibration_error(market_pairs),
        "modelCalibration": calibration_slope_intercept(model_pairs),
        "pairedBrierDelta": round(point, 6) if point is not None else None,
        "pairedBrierDeltaCI": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
        "pTwoSided": p_two_sided,
        "interpretation": "negative == production model beats Kalshi's own price under Brier",
    }


def benjamini_hochberg(pvalues_by_key, alpha=FDR_ALPHA):
    """Standard BH step-up. Returns {key: True/False} significance map."""
    items = [(k, p) for k, p in pvalues_by_key.items() if p is not None]
    items.sort(key=lambda kv: kv[1])
    m = len(items)
    significant = set()
    max_i = 0
    for i, (k, p) in enumerate(items, start=1):
        if p <= alpha * i / m:
            max_i = i
    for i, (k, p) in enumerate(items, start=1):
        if i <= max_i:
            significant.add(k)
    return {k: (k in significant) for k, _ in items}


def price_band_reliability(rows, prob_key):
    out = {}
    for lo, hi in PRICE_BANDS:
        band = [r for r in rows if lo <= r[prob_key] < hi]
        n = len(band)
        out[f"{lo:.1f}_{hi:.1f}"] = {
            "n": n,
            "meanProb": round(sum(r[prob_key] for r in band) / n, 4) if n else None,
            "outcomeRate": round(sum(r["outcome"] for r in band) / n, 4) if n else None,
            "bias": round(sum(r[prob_key] - r["outcome"] for r in band) / n, 4) if n else None,
        }
    return out


def disagreement_band_economics(rows):
    """Descriptive ONLY, preregistered fixed bands, taker fees, $1-per-
    contract convention. For each fixed |model-market| band: take the
    model's side (buy YES if modelP > marketP else buy NO at 1-marketP),
    report outcome-realized gross and fee-aware net EV per contract.
    Nothing is optimized; every band is always reported."""
    out = {}
    for lo, hi in DISAGREEMENT_BANDS:
        band = [r for r in rows if lo <= abs(r["modelP"] - r["marketP"]) < hi]
        n = len(band)
        gross, net, wins = 0.0, 0.0, 0
        for r in band:
            buy_yes = r["modelP"] > r["marketP"]
            price = r["marketP"] if buy_yes else 1 - r["marketP"]
            price = min(max(price, 0.01), 0.99)
            won = (r["outcome"] == 1) == buy_yes
            payoff = (1 - price) if won else (-price)
            fee = taker_fee(1, price)
            gross += payoff
            net += payoff - fee
            wins += 1 if won else 0
        out[f"{lo:.2f}_{hi:.2f}"] = {
            "n": n, "independentGames": independent_unit_count(band, key="gameId"),
            "winRate": round(wins / n, 4) if n else None,
            "grossEvPerContract": round(gross / n, 4) if n else None,
            "feeAwareNetEvPerContract": round(net / n, 4) if n else None,
            "note": "descriptive only -- taker fee, $1/contract, model-side convention; never a tuned rule",
        }
    return out


def filter_dimension_splits(rows):
    """Preregistered descriptive splits of the paired Brier delta by
    archived filter dimensions. No thresholds fit."""
    out = {}
    for dim in FILTER_DIMENSIONS:
        values = {}
        for r in rows:
            values.setdefault(str(r.get(dim)), []).append(r)
        dim_out = {}
        for v, subset in sorted(values.items()):
            deltas = [(x["modelP"] - x["outcome"]) ** 2 - (x["marketP"] - x["outcome"]) ** 2 for x in subset]
            dim_out[v] = {
                "n": len(subset),
                "pairedBrierDelta": round(sum(deltas) / len(deltas), 6) if deltas else None,
            }
        out[dim] = dim_out
    return out


def audit_split(rows, label):
    """Full per-family audit of one split (DEV or VAL)."""
    by_family = {}
    for r in rows:
        by_family.setdefault(r["family"], []).append(r)
    fam_results = {}
    pvals = {}
    for fam, fam_rows in sorted(by_family.items()):
        m = family_metrics(fam_rows)
        m["primaryInterpretable"] = len(fam_rows) >= MIN_TICKERS_PRIMARY and m["independentGames"] >= MIN_GAMES_PRIMARY
        fam_results[fam] = m
        if m["primaryInterpretable"]:
            pvals[fam] = m["pTwoSided"]
    fdr = benjamini_hochberg(pvals) if pvals else {}
    for fam, sig in fdr.items():
        fam_results[fam]["fdrSignificantAt10pct"] = sig
    overall = family_metrics(rows) if rows else None
    return {
        "label": label, "n": len(rows), "families": fam_results, "overall": overall,
        "modelReliabilityByPriceBand": price_band_reliability(rows, "modelP"),
        "marketReliabilityByPriceBand": price_band_reliability(rows, "marketP"),
        "disagreementEconomics": disagreement_band_economics(rows),
        "filterDimensionSplits": filter_dimension_splits(rows),
    }


def replication_verdicts(dev_result, val_result):
    """Preregistered rule: a family finding STANDS only if the paired
    Brier delta has the same sign in both halves AND the family is
    primary-interpretable in the pooled sample."""
    verdicts = {}
    for fam in set(dev_result["families"]) | set(val_result["families"]):
        d = dev_result["families"].get(fam, {}).get("pairedBrierDelta")
        v = val_result["families"].get(fam, {}).get("pairedBrierDelta")
        if d is None or v is None:
            verdicts[fam] = "INSUFFICIENT_ONE_HALF"
        elif (d < 0) == (v < 0):
            verdicts[fam] = "REPLICATES_" + ("MODEL_BETTER" if d < 0 else "MARKET_BETTER")
        else:
            verdicts[fam] = "DOES_NOT_REPLICATE"
    return verdicts


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] loading settled outcomes + evaluated production rows (archived only)...")
    outcomes, n_unsettled = load_settled_outcomes()
    evaluated_rows, n_missing_prob = load_evaluated_rows()
    print(f"[{EXPERIMENT_ID}] settled tickers={len(outcomes)} evaluatedRows={len(evaluated_rows)} excludedMissingProb={n_missing_prob} unsettledRecords={n_unsettled}")

    audit_rows = build_audit_rows(evaluated_rows, outcomes, pick="last")
    audit_rows_first = build_audit_rows(evaluated_rows, outcomes, pick="first")
    print(f"[{EXPERIMENT_ID}] audit rows (last-per-ticker): {len(audit_rows)} across {independent_unit_count(audit_rows, key='gameId')} games")

    dev_rows, val_rows = split_rows(audit_rows)
    print(f"[{EXPERIMENT_ID}] DEV(<= {DEV_DATE_MAX}): {len(dev_rows)} | VAL: {len(val_rows)}")

    dev_result = audit_split(dev_rows, "DEV")
    val_result = audit_split(val_rows, "VAL")
    pooled_result = audit_split(audit_rows, "POOLED")
    verdicts = replication_verdicts(dev_result, val_result)
    print(f"[{EXPERIMENT_ID}] replication verdicts: {verdicts}")

    # First-vs-last robustness (does production's LAST estimate beat its FIRST?)
    first_last = {}
    first_by_ticker = {r["marketTicker"]: r for r in audit_rows_first}
    paired_fl = []
    for r in audit_rows:
        f = first_by_ticker.get(r["marketTicker"])
        if f is None:
            continue
        paired_fl.append({
            "gameId": r["gameId"],
            "d": (r["modelP"] - r["outcome"]) ** 2 - (f["modelP"] - f["outcome"]) ** 2,
        })
    if paired_fl:
        def _mean_d(subset):
            return sum(x["d"] for x in subset) / len(subset) if subset else None
        pt = _mean_d(paired_fl)
        lo, hi, _ = game_clustered_bootstrap_ci(paired_fl, _mean_d, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
        first_last = {"n": len(paired_fl), "lastMinusFirstBrierDelta": round(pt, 6), "ci": {"low": lo, "high": hi},
                      "interpretation": "negative == the LAST (most-informed) evaluation scores better than the FIRST"}
    print(f"[{EXPERIMENT_ID}] first-vs-last robustness: {first_last}")

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {
            "settledTickers": len(outcomes), "evaluatedRows": len(evaluated_rows),
            "auditRowsLastPerTicker": len(audit_rows), "games": independent_unit_count(audit_rows, key="gameId"),
            "excludedMissingProbability": n_missing_prob, "unsettledRecordsSeen": n_unsettled,
            "devRows": len(dev_rows), "valRows": len(val_rows),
            "forwardHoldout": f"settlement dates > {VAL_DATE_MAX} -- preregistered, NOT computed in this run",
        },
        "dev": dev_result, "val": val_result, "pooled": pooled_result,
        "replicationVerdicts": verdicts,
        "firstVsLastRobustness": first_last,
        "governance": {
            "nothingFit": True, "noRoiSelection": True, "noNewApiCalls": True,
            "maxImplementationReadiness": "LEVEL_1_SHADOW_CANDIDATE",
            "productionChanged": False,
        },
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0022_production_calibration_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
