#!/usr/bin/env python3
"""
scripts/research/audit_full_universe_calibration_20260824.py
==================================================================
One-off, read-only, full-archived-MLB-Kalshi-market-universe calibration
coverage audit (session request: "READ-ONLY FULL-UNIVERSE MLB KALSHI
CALIBRATION AUDIT", main SHA 266880d3f9bc03f8cf50b6594f63e7077fee3bf8).

PURPOSE: prove (from code + persisted artifacts, never inferred) the
real end-to-end coverage of the archived MLB Kalshi market universe --
ticker -> family -> game/date -> pregame model probability -> executable
price -> settlement -> CLV/closing-quote -- and quantify exactly where
and why that chain breaks. This is Phase 1-9 of that audit.

This script does NOT reimplement any data-loading, join, fee, or
statistics logic that already exists in this repo. It reuses, verbatim:
  - lib.edgelab.research_dataset.build_opportunity_rows -- the existing
    canonical (marketTicker x checkpoint) full-universe row builder
    (never-bet, never-recommended markets included by construction).
  - lib.edgelab.research_reports' existing report functions
    (research_data_quality, market_calibration, model_calibration,
    edge_backtest, market_family_research, checkpoint_research).
  - lib.edgelab.research_stats' existing statistics primitives (Brier,
    log loss, ECE, game-clustered bootstrap CI, sample-size status).
  - lib.edgelab.market_family_mapping.canonicalize_market_family for the
    one canonical family vocabulary.
This script's OWN code is limited to: (a) loading the full archived
universe exactly like scripts/edgelab/run_market_price_calibration_audit.py
already does, (b) the specific coverage/classification/bucket cuts this
audit's spec asks for that do not already exist elsewhere, and (c)
writing the result to a NEW research path.

READ-ONLY: makes zero writes to data/edgelab/**, data/pipeline/**,
data/slate*.json, data/bets.json, or any other production/canonical
artifact. Writes only to data/research/calibration_audit_2026-08-24/.
Changes NO production model probabilities, betting thresholds, fee
logic, stake sizing, market-family qualification logic, or canonical
bet records.

Usage:
    python3 scripts/research/audit_full_universe_calibration_20260824.py
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import ids, storage  # noqa: E402
from lib.edgelab.calibration import calibration_status  # noqa: E402
from lib.edgelab.market_family_mapping import canonicalize_market_family, UNKNOWN, UNMAPPED  # noqa: E402
from lib.edgelab.recommendations import load_model_covered_series  # noqa: E402
from lib.edgelab.research_dataset import build_opportunity_rows  # noqa: E402
from lib.edgelab.research_reports import (  # noqa: E402
    checkpoint_research, edge_backtest, market_calibration, market_family_research,
    research_data_quality, model_calibration, _price_bucket_pct, _settled_rows,
    # Underscore-prefixed but deliberately reused (not reimplemented) so
    # this audit's own custom bucket boundaries (Phase 5/6 spec) apply
    # the EXACT SAME row-eligibility/fee/edge computation these existing,
    # tested functions already use -- only the bucket-grouping key
    # differs from market_calibration()/model_calibration()/edge_backtest()'s
    # own built-in bucket widths.
    _model_eligible_rows, _edge_side_opportunities,
)
from lib.edgelab.research_stats import (  # noqa: E402
    brier_and_log_loss_summary, expected_calibration_error, game_clustered_bootstrap_ci,
    independent_unit_count, sample_size_status, win_rate_value_fn,
)
from lib.edgelab.settlement import derive_bet_result  # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "research", "calibration_audit_2026-08-24")
AUDITED_MAIN_SHA = "266880d3f9bc03f8cf50b6594f63e7077fee3bf8"
SCHEMA_VERSION = "1"

# ── This audit's own 4-tier sample-size scheme (Phase 5-8 spec: it wants
# INSUFFICIENT_SAMPLE / DESCRIPTIVE_ONLY / DIRECTIONAL /
# RELIABLE_ENOUGH_FOR_RESEARCH, a finer scheme than either of the two
# already-established conventions in this repo -- lib.edgelab.analytics's
# 2-tier n<20 gate and lib.edgelab.calibration's 3-tier n<20/n<100/n>=100
# gate). Documented choice: extends the SAME n<20 floor and n>=100
# ceiling both existing conventions already use (never a new, competing
# floor/ceiling), splitting the 20-99 middle band this repo already
# labels "DESCRIPTIVE_ONLY" into two: 20-49 stays DESCRIPTIVE_ONLY (a
# real number, not yet suggestive of a pattern), 50-99 becomes DIRECTIONAL
# (suggestive, still not research-reliable). n>=100 is relabeled
# RELIABLE_ENOUGH_FOR_RESEARCH (same threshold as the existing
# "CALIBRATED" tier, renamed here only because CALIBRATED could be
# misread as "the probabilities ARE calibrated" rather than "n is large
# enough to read a calibration number seriously" -- the actual finding
# might be miscalibration).
RESEARCH_N_INSUFFICIENT = 20
RESEARCH_N_DIRECTIONAL = 50
RESEARCH_N_RELIABLE = 100


def research_status(n):
    if n < RESEARCH_N_INSUFFICIENT:
        return "INSUFFICIENT_SAMPLE"
    if n < RESEARCH_N_DIRECTIONAL:
        return "DESCRIPTIVE_ONLY"
    if n < RESEARCH_N_RELIABLE:
        return "DIRECTIONAL"
    return "RELIABLE_ENOUGH_FOR_RESEARCH"


# ── Loading (mirrors scripts/edgelab/run_market_price_calibration_audit.py's _load_universe,
# extended with raw `markets` -- the actual Market-dimension registry --
# which that script does not load, since THIS audit's primary population
# is explicitly the archived market registry itself, not only its
# observations). ──────────────────────────────────────────────────────

def _discover_dates():
    paths = (
        glob.glob(storage.partition_path("markets", "*", compressed=True))
        + glob.glob(storage.partition_path("markets", "*", compressed=False))
    )
    return sorted({os.path.basename(p).split(".")[0] for p in paths})


def _load_universe(dates):
    observations, settlements, evaluations, recommendations, games, markets = [], [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_partition("observations", date))
        settlements.extend(storage.read_partition("settlements", date))
        evaluations.extend(storage.read_partition("model_evaluations", date))
        recommendations.extend(storage.read_partition("recommendations", date))
        games.extend(storage.read_partition("games", date))
        markets.extend(storage.read_partition("markets", date))
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    return observations, settlements, evaluations, recommendations, games, markets, bets


# ── Family support classification (Phase 3) ──────────────────────────────
#
# Which canonical families the production 11-REQUIRED_MARKETS pipeline
# can price at all (config/rules.json's market_list -- 6 distinct Kalshi
# series backing NRFI/YRFI, F5 ML, team total, full-game ML, game total,
# and RL, per lib.edgelab.recommendations.load_model_covered_series).
PIPELINE_COVERED_FAMILIES = {
    "game_result", "inning_result", "team_total", "game_total", "first_inning_run", "winning_margin",
}
# winning_margin (RL_Away/RL_Home) IS one of the 11 REQUIRED_MARKETS and
# therefore gets a ModelEvaluation row every pipeline run, but
# scripts/build_market_ledger.py unconditionally rejects it via
# rejected_row() BEFORE computing modelProb ("Rule 81: RL suspended...",
# build_market_ledger.py:1525-1536) -- confirmed by this audit's own data
# (see PHASE3 findings: 0 EVALUATED rows for winning_margin anywhere in
# the archive). Tracked separately so its 0% coverage is correctly
# explained as "adapter exists, production refuses to run it" rather than
# lumped in with genuinely adapter-less families below.
STRUCTURALLY_SUSPENDED_FAMILIES = {"winning_margin"}

# Families with a real, code-confirmed probability adapter OUTSIDE the
# 11-market pipeline (research-discovery bridges), reachable only via
# lib.edgelab.model_evaluation.extend_full_universe_evaluations(), which
# itself only ever runs inside scripts/edgelab/build_recommendations.py.
DISCOVERY_BRIDGE_COVERED_FAMILIES = {
    "pitcher_strikeouts",   # lib/research/pitcher_workload_projection.py via kalshi_discovery_bridge
    "pitcher_outs",         # same
    "hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis",  # lib/edgelab/hitter_board_bridge.py
}
# Families confirmed by this audit's Explore agents to have NO
# probability adapter anywhere in the codebase (lib/kalshi_probability_adapters.py's
# own _NEVER_MODELED_FAMILIES list, and lib/edgelab/hitter_board_bridge.py's
# module docstring for hitter_stolen_bases specifically). inning_total
# has no adapter found in any of the 4 independent architecture searches
# this audit ran.
NO_ADAPTER_ANYWHERE_FAMILIES = {"hitter_stolen_bases", "inning_total"}

# The date RECOMMENDATION_SYNC (scripts/edgelab/build_recommendations.py)
# last ran in this archive, per this audit's Explore-agent finding
# (research_runs/*.jsonl scan: RECOMMENDATION_SYNC present through
# 2026-08-16, absent 2026-08-17 onward as of this audit). Verified
# independently below in phase9_recommendation_sync_gap() rather than
# trusted at face value.
RECOMMENDATION_SYNC_LAST_SEEN_DATE = "2026-08-16"


def family_support_tier(canonical_family):
    if canonical_family in DISCOVERY_BRIDGE_COVERED_FAMILIES:
        return "DISCOVERY_BRIDGE_ADAPTER"
    if canonical_family in STRUCTURALLY_SUSPENDED_FAMILIES:
        return "ADAPTER_EXISTS_BUT_PRODUCTION_SUSPENDED"
    if canonical_family in PIPELINE_COVERED_FAMILIES:
        return "PIPELINE_ADAPTER"
    if canonical_family in NO_ADAPTER_ANYWHERE_FAMILIES:
        return "NO_ADAPTER_ANYWHERE"
    return "UNCLASSIFIED"  # should not occur among the 14 confirmed archived families; flagged if it does


# ── Phase 2: coverage tables ──────────────────────────────────────────────

def _index_by_ticker(records):
    out = defaultdict(list)
    for r in records:
        t = r.get("marketTicker")
        if t:
            out[t].append(r)
    return out


def build_ticker_facts(markets, evaluations, settlements, opportunity_rows):
    """
    One fact-row per distinct archived marketTicker (the primary
    population, per this audit's Interpretation Rule 1) -- NOT one row
    per opportunity-row/checkpoint. Booleans A-G map directly to the
    audit spec's Phase 2 letter list.
    """
    eval_by_ticker = _index_by_ticker(evaluations)
    settlement_by_ticker = _index_by_ticker(settlements)
    obs_rows_by_ticker = defaultdict(list)
    for r in opportunity_rows:
        obs_rows_by_ticker[r["marketTicker"]].append(r)

    facts = {}
    for m in markets:
        ticker = m.get("marketTicker")
        if not ticker or ticker in facts:
            continue
        raw_family = m.get("marketFamily")
        canon_family = canonicalize_market_family(raw_family)
        evals = eval_by_ticker.get(ticker, [])
        settle_rows = settlement_by_ticker.get(ticker, [])
        opp_rows = obs_rows_by_ticker.get(ticker, [])

        best_prob = next((e.get("modelFairProbability") for e in evals if e.get("modelFairProbability") is not None), None)
        eval_statuses = sorted({e.get("evaluationStatus") for e in evals if e.get("evaluationStatus")})
        settled = next((s for s in settle_rows if s.get("settlementStatus") == "SETTLED" and s.get("result") in ("YES", "NO")), None)
        settlement_status_seen = sorted({s.get("settlementStatus") for s in settle_rows if s.get("settlementStatus")})
        has_price = any(r.get("executableYesPrice") is not None for r in opp_rows)
        has_close = any(r.get("isClosingQuote") for r in opp_rows)
        game_linked = bool(m.get("gameId"))

        facts[ticker] = {
            "marketTicker": ticker,
            "gameDate": m.get("gameDate"),
            "gameId": m.get("gameId"),
            "rawMarketFamily": raw_family,
            "canonicalMarketFamily": canon_family,
            "familySupportTier": family_support_tier(canon_family),
            "A_archived": True,
            "B_recognizedFamily": canon_family not in (UNKNOWN, UNMAPPED, None),
            "C_gameLinked": game_linked,
            "D_hasModelProbability": best_prob is not None,
            "E_hasExecutablePrice": has_price,
            "F_settled": settled is not None,
            "G_hasValidClose": has_close,
            "modelFairProbability": best_prob,
            "evaluationStatusesSeen": eval_statuses,
            "hasAnyModelEvaluationRow": bool(evals),
            "settlementStatusesSeen": settlement_status_seen,
            "settlementResult": settled.get("result") if settled else None,
        }
    return facts


def phase2_overall(facts):
    n = len(facts)

    def _cnt(pred):
        c = sum(1 for f in facts.values() if pred(f))
        return {"count": c, "pct": round(100.0 * c / n, 2) if n else None}

    calibration_joinable = _cnt(lambda f: f["A_archived"] and f["B_recognizedFamily"] and f["C_gameLinked"] and f["D_hasModelProbability"] and f["E_hasExecutablePrice"] and f["F_settled"])
    clv_joinable = _cnt(lambda f: f["A_archived"] and f["G_hasValidClose"])
    return {
        "totalArchivedUniqueMarketInstances": n,
        "withRecognizedFamily": _cnt(lambda f: f["B_recognizedFamily"]),
        "withGameDateLinkage": _cnt(lambda f: f["C_gameLinked"]),
        "withPersistedModelProbability": _cnt(lambda f: f["D_hasModelProbability"]),
        "withExecutablePrice": _cnt(lambda f: f["E_hasExecutablePrice"]),
        "settled": _cnt(lambda f: f["F_settled"]),
        "fullyCalibrationJoinable": calibration_joinable,
        "clvJoinable": clv_joinable,
        "missingProbability": _cnt(lambda f: not f["D_hasModelProbability"]),
        "missingSettlement": _cnt(lambda f: not f["F_settled"]),
        "missingPrice": _cnt(lambda f: not f["E_hasExecutablePrice"]),
        "unrecognizedFamily": _cnt(lambda f: not f["B_recognizedFamily"]),
    }


def phase2_by_family(facts):
    groups = defaultdict(list)
    for f in facts.values():
        groups[f["canonicalMarketFamily"]].append(f)

    def _pct(part, whole):
        return round(100.0 * part / whole, 2) if whole else None

    rows = []
    for fam, fam_facts in groups.items():
        n = len(fam_facts)
        recognized = sum(1 for f in fam_facts if f["B_recognizedFamily"])
        with_prob = sum(1 for f in fam_facts if f["D_hasModelProbability"])
        with_price = sum(1 for f in fam_facts if f["E_hasExecutablePrice"])
        settled = sum(1 for f in fam_facts if f["F_settled"])
        joinable = sum(1 for f in fam_facts if f["A_archived"] and f["B_recognizedFamily"] and f["C_gameLinked"] and f["D_hasModelProbability"] and f["E_hasExecutablePrice"] and f["F_settled"])
        with_close = sum(1 for f in fam_facts if f["G_hasValidClose"])
        support_tiers = sorted({f["familySupportTier"] for f in fam_facts})
        rows.append({
            "marketFamily": fam,
            "archivedCount": n,
            "recognizedCount": recognized,
            "withModelProbability": with_prob,
            "probabilityCoveragePct": _pct(with_prob, n),
            "withExecutablePrice": with_price,
            "priceCoveragePct": _pct(with_price, n),
            "settledCount": settled,
            "settlementCoveragePct": _pct(settled, n),
            "fullyCalibrationJoinable": joinable,
            "calibrationJoinablePct": _pct(joinable, n),
            "withValidClose": with_close,
            "clvCoveragePct": _pct(with_close, n),
            "missingProbability": n - with_prob,
            "missingSettlement": n - settled,
            "missingPrice": n - with_price,
            "unrecognized": n - recognized,
            "familySupportTiers": support_tiers,
            "notes": _family_note(fam, support_tiers),
        })
    return sorted(rows, key=lambda r: -r["archivedCount"])


def _family_note(fam, support_tiers):
    if "NO_ADAPTER_ANYWHERE" in support_tiers:
        return "UNSUPPORTED_FAMILY -- no probability projection adapter exists anywhere in this codebase for this family (confirmed by 4 independent code searches)."
    if "ADAPTER_EXISTS_BUT_PRODUCTION_SUSPENDED" in support_tiers:
        return "A research-only probability adapter exists, but scripts/build_market_ledger.py unconditionally rejects this family before computing a probability (Rule 81 RL suspension) -- 0% probability coverage is production behavior by design, not a data gap."
    if "DISCOVERY_BRIDGE_ADAPTER" in support_tiers:
        return "Probability adapter exists but is reachable ONLY via scripts/edgelab/build_recommendations.py's extend_full_universe_evaluations() -- coverage for this family depends entirely on whether/how often that script ran on a given date (see Phase 9 finding)."
    if "PIPELINE_ADAPTER" in support_tiers:
        return "Covered by the production 11-REQUIRED_MARKETS pipeline (scripts/build_market_ledger.py) in principle; per-instance gaps are due to threshold-rung truncation, missing starter/lineup data, or the market not matching a pipeline-evaluated game -- see Phase 3 per-ticker reason codes."
    return ""


def phase2_by_date(facts):
    groups = defaultdict(list)
    for f in facts.values():
        groups[f["gameDate"]].append(f)
    rows = []
    for date, date_facts in sorted(groups.items(), key=lambda kv: kv[0] or ""):
        n = len(date_facts)
        with_prob = sum(1 for f in date_facts if f["D_hasModelProbability"])
        settled = sum(1 for f in date_facts if f["F_settled"])
        joinable = sum(1 for f in date_facts if f["A_archived"] and f["B_recognizedFamily"] and f["C_gameLinked"] and f["D_hasModelProbability"] and f["E_hasExecutablePrice"] and f["F_settled"])
        rows.append({
            "gameDate": date,
            "archivedCount": n,
            "withModelProbability": with_prob,
            "probabilityCoveragePct": round(100.0 * with_prob / n, 2) if n else None,
            "settledCount": settled,
            "fullyCalibrationJoinable": joinable,
        })
    return rows


# ── Phase 3: reason-code classification for missing probability ─────────

def classify_missing_probability_reason(fact):
    """
    Best-evidence root-cause classification for one ticker with
    D_hasModelProbability == False, using only facts this audit
    independently verified (family support tier, any ModelEvaluation row
    ever seen for this ticker + its evaluationStatus values, date vs. the
    RECOMMENDATION_SYNC gap). This is a classification, not a certainty
    -- see the accompanying note on every row for the specific evidence
    used, so a reader can audit the call rather than trust a bare label.
    """
    tier = fact["familySupportTier"]
    statuses = set(fact["evaluationStatusesSeen"])
    has_any_row = fact["hasAnyModelEvaluationRow"]
    date = fact.get("gameDate") or ""

    if tier == "NO_ADAPTER_ANYWHERE":
        return "UNSUPPORTED_FAMILY", "No probability adapter exists anywhere in the codebase for this family."
    if tier == "ADAPTER_EXISTS_BUT_PRODUCTION_SUSPENDED":
        return "UNSUPPORTED_FAMILY", "Adapter exists in research code only; production (build_market_ledger.py) unconditionally rejects this family before computing a probability (Rule 81)."

    if "PARSER_UNRESOLVED" in statuses:
        return "PARSER/REGISTRY_OMISSION", "A ModelEvaluation row exists with evaluationStatus=PARSER_UNRESOLVED."
    if "DATA_QUALITY_BLOCK" in statuses:
        return "MISSING_STARTER_DATA", "A ModelEvaluation row exists with evaluationStatus=DATA_QUALITY_BLOCK -- build_market_ledger.py's compute_game_projection_context() early-returns on missing xFIP/offenseBaselineAdj (starter/team-stat inputs) before pricing."
    if "MISSING_MARKET_PRICE" in statuses:
        return "OTHER", "A ModelEvaluation row exists with evaluationStatus=MISSING_MARKET_PRICE (the Kalshi price itself was unavailable at evaluation time, not a projection-input gap)."
    if "NOT_EVALUATED" in statuses:
        return "TRUNCATION", "A ModelEvaluation row exists (evaluationStatus=NOT_EVALUATED, source=market_universe_extension) confirming the family IS pipeline-covered in principle, but this specific game/instance was never fed into build_market_ledger.py's evaluate_game() that day."

    if not has_any_row:
        if tier == "DISCOVERY_BRIDGE_ADAPTER":
            if date > RECOMMENDATION_SYNC_LAST_SEEN_DATE:
                return "OTHER", f"No ModelEvaluation row of any kind exists for this ticker. This family's only adapter path (extend_full_universe_evaluations) runs exclusively inside scripts/edgelab/build_recommendations.py, which (per this audit's Phase 9 finding) has not run since {RECOMMENDATION_SYNC_LAST_SEEN_DATE} -- an operational gap, not an architectural one."
            return "NO_PROJECTION_ADAPTER", "No ModelEvaluation row exists for this ticker on a date when RECOMMENDATION_SYNC was still running -- the discovery-bridge adapter path did not pick up this specific ticker; root cause not further isolated by this audit."
        if tier == "PIPELINE_ADAPTER":
            return "TRUNCATION", "No ModelEvaluation row of any kind exists for this pipeline-covered-family ticker -- most consistent with build_market_ledger.py's REQUIRED_MARKETS evaluating only ONE specific threshold rung per team/game while Kalshi archives many alternate-threshold rungs for team_total/game_total; this ticker's specific threshold was never one the pipeline attempted."
    return "OTHER", "Missing probability with no ModelEvaluation row and no evaluationStatus evidence to further classify; residual/unexplained."


def phase3_classification(facts):
    missing = [f for f in facts.values() if not f["D_hasModelProbability"]]
    reason_counts = Counter()
    reason_by_family = defaultdict(Counter)
    for f in missing:
        reason, note = classify_missing_probability_reason(f)
        reason_counts[reason] += 1
        reason_by_family[f["canonicalMarketFamily"]][reason] += 1
    return {
        "totalMissingProbability": len(missing),
        "totalArchived": len(facts),
        "reasonCounts": dict(reason_counts),
        "reasonCountsByFamily": {fam: dict(c) for fam, c in reason_by_family.items()},
    }


# ── Phase 9: verify the RECOMMENDATION_SYNC gap directly (never trust the agent's claim at face value) ──

def phase9_recommendation_sync_gap(dates, evaluations):
    """
    File-existence-level proof, independent of parsing research_runs
    logs: data/edgelab/recommendations/<date>.jsonl(.gz) existing at all
    is direct evidence scripts/edgelab/build_recommendations.py
    (RECOMMENDATION_SYNC) ran for that date, since that script is this
    repo's only writer of the recommendations/ entity. Cross-checked
    against each date's model_evaluations `source` distribution: a date
    with zero market_universe_extension/kalshi_discovery_extension rows
    confirms extend_full_universe_evaluations() (also only called from
    build_recommendations.py) did not run that date either.

    Per-date source counts are read fresh per date-partition here
    (rather than recovered from the already-flattened `evaluations`
    list's own provenance.sourceFile) -- an earlier version of this
    function tried to recover each row's date by parsing
    provenance.sourceFile assuming it always pointed into
    model_evaluations/<date>.jsonl, but a market_universe_extension row's
    provenance is (correctly) the ORIGINAL raw Kalshi snapshot path
    (data/kalshi_registry_snapshots/...), not a model_evaluations path,
    so that heuristic silently mis-dated (in fact, entirely dropped)
    every such row. Caught by independently re-querying
    data/edgelab/model_evaluations/2026-08-15.jsonl.gz directly (5,074
    real market_universe_extension rows exist there, not zero as the
    buggy version reported) before trusting this function's output.
    """
    dates_with_recommendations = sorted(d for d in dates if storage.partition_exists("recommendations", d))
    dates_without_recommendations = sorted(set(dates) - set(dates_with_recommendations))

    source_by_date = defaultdict(Counter)
    for date in dates:
        for e in storage.read_partition("model_evaluations", date):
            source_by_date[date][e.get("source") or "UNKNOWN"] += 1

    full_universe_extension_dates = sorted(
        d for d, counts in source_by_date.items()
        if counts.get("market_universe_extension", 0) > 0 or counts.get("kalshi_discovery_extension", 0) > 0
    )
    return {
        "datesWithRecommendationsFile": dates_with_recommendations,
        "datesWithoutRecommendationsFile": dates_without_recommendations,
        "datesWithFullUniverseExtensionModelEvaluationRows": full_universe_extension_dates,
        "modelEvaluationSourceCountsByDate": {d: dict(c) for d, c in sorted(source_by_date.items())},
    }


# ── Phase 5: fixed reliability buckets over MODEL probability (spec's exact ranges) ──

RELIABILITY_BUCKET_EDGES = [
    (0.10, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50),
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
    (0.70, 0.75), (0.75, 0.80), (0.80, 1.0001),
]


def _bucket_label_for(p, edges):
    for lo, hi in edges:
        if lo <= p < hi:
            return f"{int(round(lo * 100))}-{int(round(min(hi, 1.0) * 100))}" if hi <= 1.0 else f"{int(round(lo * 100))}+"
    return "OTHER"


def _reliability_row(bucket, items):
    """items: list of (row, side, result) tuples, same shape as _model_eligible_rows()'s output."""
    n = len(items)
    pairs = [(r["modelFairProbability"], 1 if result == "WIN" else 0) for r, side, result in items]
    avg_pred = sum(p for p, _ in pairs) / n
    actual = sum(o for _, o in pairs) / n
    brier, log_loss = brier_and_log_loss_summary(pairs)
    independent_games = independent_unit_count([r for r, _, _ in items], key="gameId")
    ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(
        [r for r, _, _ in items],
        win_rate_value_fn(lambda row: derive_bet_result(row["settlementResult"], row.get("side") or "YES") == "WIN"),
    )
    return {
        "bucket": bucket,
        "n": n,
        "independentGames": independent_games,
        "predictedAvg": round(avg_pred, 4),
        "actualWinRate": round(actual, 4),
        "difference": round(actual - avg_pred, 4),
        "brierScore": brier,
        "logLoss": log_loss,
        "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
        "sampleStatus": research_status(n),
        "sampleStatusDetail": sample_size_status(n, independent_games),
    }


def phase5_reliability_buckets(rows, group_fn=None, group_label="ALL"):
    eligible = _model_eligible_rows(rows)  # [(row, side, result), ...] -- settled, model-linked, causally valid
    if group_fn is not None:
        eligible = [item for item in eligible if group_fn(item[0])]
    buckets = defaultdict(list)
    for item in eligible:
        r, side, result = item
        buckets[_bucket_label_for(r["modelFairProbability"], RELIABILITY_BUCKET_EDGES)].append(item)
    out = [_reliability_row(b, items) for b, items in buckets.items()]
    overall = _reliability_row("ALL", eligible) if eligible else None
    return {"groupLabel": group_label, "overall": overall, "byBucket": sorted(out, key=lambda r: r["bucket"])}


def phase5_by_family(rows):
    families = sorted({r.get("canonicalMarketFamily") for r in rows if r.get("canonicalMarketFamily")})
    return {fam: phase5_reliability_buckets(rows, lambda row, f=fam: row.get("canonicalMarketFamily") == f, fam) for fam in families}


def phase5_by_side(rows):
    return {
        side: phase5_reliability_buckets(rows, lambda row, s=side: (row.get("side") or "YES") == s, side)
        for side in ("YES", "NO")
    }


# ── Phase 6: fee-adjusted executable-edge monotonicity (spec's exact edge buckets) ──

EDGE_MONOTONICITY_BUCKET_EDGES = [
    (-999.0, 0.0), (0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0), (10.0, 15.0), (15.0, 999.0),
]


def _edge_bucket_label(edge_pct):
    for lo, hi in EDGE_MONOTONICITY_BUCKET_EDGES:
        if lo <= edge_pct < hi:
            if lo <= -900:
                return "<=0%"
            if hi >= 900:
                return "15%+"
            return f"{int(lo)}-{int(hi)}%"
    return "OTHER"


def phase6_edge_monotonicity(rows, group_fn=None, group_label="ALL"):
    opportunities = _edge_side_opportunities(rows)
    if group_fn is not None:
        opportunities = [o for o in opportunities if group_fn(o)]
    buckets = defaultdict(list)
    for o in opportunities:
        buckets[_edge_bucket_label(o["opportunityEdge"] * 100.0)].append(o)

    out = []
    for label, items in buckets.items():
        n = len(items)
        independent_games = independent_unit_count(items, key="gameId")
        avg_edge = sum(o["opportunityEdge"] for o in items) / n
        win_rate = sum(1 for o in items if o["opportunityWin"]) / n
        clv_values = [o["opportunityMovement"] for o in items if o.get("opportunityMovement") is not None]
        avg_clv = sum(clv_values) / len(clv_values) if clv_values else None
        median_clv = sorted(clv_values)[len(clv_values) // 2] if clv_values else None
        positive_clv_rate = (sum(1 for c in clv_values if c > 0) / len(clv_values)) if clv_values else None
        pairs = [(o["opportunityModelProbability"], 1 if o["opportunityWin"] else 0) for o in items]
        brier, log_loss = brier_and_log_loss_summary(pairs)
        returns = [o["opportunityReturn"] for o in items if o.get("opportunityReturn") is not None]
        realistic_returns = [o["opportunityReturnRealisticExecution"] for o in items if o.get("opportunityReturnRealisticExecution") is not None]
        ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(items, win_rate_value_fn(lambda o: o["opportunityWin"]))
        out.append({
            "edgeBucket": label,
            "n": n,
            "independentGames": independent_games,
            "avgPredictedEdge": round(avg_edge, 4),
            "actualWinRate": round(win_rate, 4),
            "actualROI_grossFeeAware": round(sum(returns) / len(returns), 4) if returns else None,
            "actualROI_realisticExecution": round(sum(realistic_returns) / len(realistic_returns), 4) if realistic_returns else None,
            "avgCLV": round(avg_clv, 4) if avg_clv is not None else None,
            "medianCLV": round(median_clv, 4) if median_clv is not None else None,
            "positiveCLVRate": round(positive_clv_rate, 4) if positive_clv_rate is not None else None,
            "brierScore": brier,
            "logLoss": log_loss,
            "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
            "sampleStatus": research_status(n),
            "sampleStatusDetail": sample_size_status(n, independent_games),
        })
    label_order = {"<=0%": 0, "0-2%": 1, "2-4%": 2, "4-6%": 3, "6-8%": 4, "8-10%": 5, "10-15%": 6, "15%+": 7}
    return {"groupLabel": group_label, "byEdgeBucket": sorted(out, key=lambda r: label_order.get(r["edgeBucket"], 99))}


def phase6_monotonicity_flag(edge_buckets):
    """
    Non-monotonic flag: True if actualWinRate does NOT trend upward
    (allowing ties) as the edge bucket increases, among buckets with
    sampleStatus != INSUFFICIENT_SAMPLE. Purely descriptive -- never
    used to silently drop a family from the report.
    """
    usable = [b for b in edge_buckets if b["sampleStatus"] != "INSUFFICIENT_SAMPLE" and b["actualWinRate"] is not None]
    if len(usable) < 2:
        return {"evaluable": False, "nonMonotonic": None, "reason": "fewer than 2 buckets with sufficient sample"}
    rates = [b["actualWinRate"] for b in usable]
    non_monotonic = any(rates[i] > rates[i + 1] + 0.03 for i in range(len(rates) - 1))  # 3pp tolerance for noise
    return {"evaluable": True, "nonMonotonic": non_monotonic, "usableBucketCount": len(usable)}


# ── Phase 8: best-expression comparison ──────────────────────────────────

def phase8_best_expression(rows):
    """
    Groups causally-valid, settled, model-linked opportunities by
    (canonicalMarketFamily, marketHorizon, outcomeLabel side-orientation)
    so a genuine F5 'Win' team-YES expression is never pooled with an F5
    'Tie' expression or a protected-NO expression just because both
    canonicalize to inning_result -- same principle as
    inning_result_outcome_label_calibration() in
    scripts/edgelab/run_market_price_calibration_audit.py, applied here
    to MODEL-probability calibration + CLV instead of market-price
    calibration.
    """
    eligible = _model_eligible_rows(rows)

    def _expression_key(r):
        fam = r.get("canonicalMarketFamily")
        horizon = r.get("marketHorizon")
        label = r.get("outcomeLabel")
        return f"{fam}:{horizon or ''}:{label or ''}"

    groups = defaultdict(list)
    for item in eligible:
        groups[_expression_key(item[0])].append(item)

    out = []
    for key, items in groups.items():
        row = _reliability_row(key, items)
        clv_values = [it[0].get("fullUniverseMarketMovementToClose") for it in items if it[0].get("fullUniverseMarketMovementToClose") is not None]
        row["avgCLVProxy"] = round(sum(clv_values) / len(clv_values), 4) if clv_values else None
        row["expressionKey"] = key
        row["canonicalMarketFamily"] = items[0][0].get("canonicalMarketFamily")
        row["marketHorizon"] = items[0][0].get("marketHorizon")
        row["outcomeLabel"] = items[0][0].get("outcomeLabel")
        out.append(row)
    return sorted(out, key=lambda r: -r["n"])


# ── Markdown rendering ─────────────────────────────────────────────────────

def _fmt_pct(v):
    return f"{v:.1f}%" if v is not None else "n/a"


def render_markdown(payload):
    p = payload["phases"]
    lines = []
    lines.append("# Full-Universe MLB Kalshi Calibration Audit — 2026-08-24")
    lines.append("")
    lines.append(f"**Main SHA audited:** `{payload['mainShaAudited']}`  ")
    lines.append(f"**Dates in archived universe:** {payload['dates'][0]} to {payload['dates'][-1]} ({len(payload['dates'])} dates)  ")
    lines.append(f"**Generated:** {payload['generatedAt']}")
    lines.append("")
    lines.append("This is a READ-ONLY research audit. No production model probabilities, thresholds, fee logic, "
                  "stake sizing, market-family qualification logic, or canonical bet records were changed. "
                  "See the accompanying full_universe_calibration_audit.json for every number in machine-readable form; "
                  "this file is a narrative summary.")
    lines.append("")
    lines.append("## Phase 2 — Overall coverage")
    lines.append("")
    ov = p["phase2Overall"]
    lines.append(f"- Total archived unique MLB market instances: **{ov['totalArchivedUniqueMarketInstances']}**")
    for k in ("withRecognizedFamily", "withGameDateLinkage", "withPersistedModelProbability", "withExecutablePrice", "settled", "fullyCalibrationJoinable", "clvJoinable"):
        v = ov[k]
        lines.append(f"- {k}: **{v['count']}** ({_fmt_pct(v['pct'])})")
    lines.append("")
    lines.append("## Phase 2 — Coverage by market family")
    lines.append("")
    lines.append("| family | archived | recognized | withProb | prob% | withPrice | price% | settled | settle% | joinable | joinable% | withClose | clv% | notes |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in p["phase2ByFamily"]:
        lines.append(
            f"| {r['marketFamily']} | {r['archivedCount']} | {r['recognizedCount']} | {r['withModelProbability']} | "
            f"{_fmt_pct(r['probabilityCoveragePct'])} | {r['withExecutablePrice']} | {_fmt_pct(r['priceCoveragePct'])} | "
            f"{r['settledCount']} | {_fmt_pct(r['settlementCoveragePct'])} | {r['fullyCalibrationJoinable']} | "
            f"{_fmt_pct(r['calibrationJoinablePct'])} | {r['withValidClose']} | {_fmt_pct(r['clvCoveragePct'])} | {r['notes'][:60]}... |"
        )
    lines.append("")
    lines.append("## Phase 3 — Missing-probability reason codes")
    lines.append("")
    pc = p["phase3Classification"]
    lines.append(f"Total missing probability: **{pc['totalMissingProbability']}** of {pc['totalArchived']} archived instances.")
    lines.append("")
    lines.append("| reason | count |")
    lines.append("|---|---|")
    for reason, count in sorted(pc["reasonCounts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {reason} | {count} |")
    lines.append("")
    lines.append("## Phase 9 — RECOMMENDATION_SYNC gap (verified independently)")
    lines.append("")
    rs = p["phase9RecommendationSyncGap"]
    lines.append(f"- Dates WITH a recommendations/ file (RECOMMENDATION_SYNC ran): {rs['datesWithRecommendationsFile']}")
    lines.append(f"- Dates WITHOUT a recommendations/ file (RECOMMENDATION_SYNC did not run): {rs['datesWithoutRecommendationsFile']}")
    lines.append("")
    lines.append("See full_universe_calibration_audit.json for Phase 5 (reliability buckets), Phase 6 (edge monotonicity), "
                  "Phase 7/8 (family/expression comparisons), and every underlying number this summary references.")
    return "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    dates = _discover_dates()
    print(f"dates discovered: {dates[0]} to {dates[-1]} ({len(dates)} dates)", file=sys.stderr)

    observations, settlements, evaluations, recommendations, games, markets, bets = _load_universe(dates)
    print(f"loaded: {len(observations)} observations, {len(settlements)} settlements, {len(evaluations)} evaluations, "
          f"{len(recommendations)} recommendations, {len(games)} games, {len(markets)} markets, {len(bets)} bets", file=sys.stderr)

    rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations, recommendations=recommendations, bets=bets, games=games)
    print(f"opportunity rows: {len(rows)}", file=sys.stderr)

    facts = build_ticker_facts(markets, evaluations, settlements, rows)
    print(f"ticker facts: {len(facts)} (markets archive distinct tickers: {len({m['marketTicker'] for m in markets if m.get('marketTicker')})})", file=sys.stderr)

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "mainShaAudited": AUDITED_MAIN_SHA,
        "generatedAt": ids.utc_now_iso(),
        "dates": dates,
        "counts": {
            "observations": len(observations), "settlements": len(settlements), "modelEvaluations": len(evaluations),
            "recommendations": len(recommendations), "games": len(games), "markets": len(markets), "bets": len(bets),
            "opportunityRows": len(rows), "distinctTickersInFacts": len(facts),
        },
        "phases": {},
    }
    payload["phases"]["phase2Overall"] = phase2_overall(facts)
    payload["phases"]["phase2ByFamily"] = phase2_by_family(facts)
    payload["phases"]["phase2ByDate"] = phase2_by_date(facts)
    payload["phases"]["phase3Classification"] = phase3_classification(facts)
    payload["phases"]["phase5ReliabilityOverall"] = phase5_reliability_buckets(rows)
    payload["phases"]["phase5ReliabilityByFamily"] = phase5_by_family(rows)
    payload["phases"]["phase5ReliabilityBySide"] = phase5_by_side(rows)
    payload["phases"]["phase6EdgeMonotonicityOverall"] = phase6_edge_monotonicity(rows)
    payload["phases"]["phase6EdgeMonotonicityOverall"]["monotonicityFlag"] = phase6_monotonicity_flag(payload["phases"]["phase6EdgeMonotonicityOverall"]["byEdgeBucket"])
    families_for_edge = sorted({r.get("canonicalMarketFamily") for r in rows if r.get("canonicalMarketFamily")})
    phase6_by_family = {}
    for fam in families_for_edge:
        fam_result = phase6_edge_monotonicity(rows, lambda o, f=fam: o.get("canonicalMarketFamily") == f, fam)
        fam_result["monotonicityFlag"] = phase6_monotonicity_flag(fam_result["byEdgeBucket"])
        phase6_by_family[fam] = fam_result
    payload["phases"]["phase6EdgeMonotonicityByFamily"] = phase6_by_family
    payload["phases"]["phase8BestExpression"] = phase8_best_expression(rows)
    payload["phases"]["phase9RecommendationSyncGap"] = phase9_recommendation_sync_gap(dates, evaluations)

    # Existing, already-built reports reused verbatim (no reimplementation) --
    # these are the library's OWN, already-tested calibration/edge/family/
    # checkpoint/data-quality computations, included here so this audit's
    # single JSON artifact is self-contained (a reader doesn't have to
    # separately run scripts/edgelab/run_research_reports.py to see them).
    payload["reusedLibraryReports"] = {
        "researchDataQuality": research_data_quality(rows, observations=observations, settlements=settlements, evaluations=evaluations),
        "marketPriceCalibration": market_calibration(rows),
        "modelProbabilityCalibration": model_calibration(rows),
        "edgeBacktest": edge_backtest(rows),
        "marketFamilyResearch": market_family_research(rows),
        "checkpointResearch": checkpoint_research(rows),
    }

    out_json = os.path.join(OUT_DIR, "full_universe_calibration_audit.json")
    with open(out_json, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"written: {out_json}", file=sys.stderr)

    out_md = os.path.join(OUT_DIR, "SUMMARY.md")
    with open(out_md, "w") as f:
        f.write(render_markdown(payload))
        f.write("\n")
    print(f"written: {out_md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

