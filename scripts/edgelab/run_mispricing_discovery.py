#!/usr/bin/env python3
"""
scripts/edgelab/run_mispricing_discovery.py
==================================================
Hypothesis generation / validation research over the clean, settled
Kalshi MLB market archive: mine for specific, repeatable pockets of
price-vs-outcome mispricing, building on the market-price calibration
audit (data/edgelab/reports/market_price_calibration_audit.md, PR #98)
and its shared closing-quote fix (lib/edgelab/checkpoints.py, PR #99).

NOT a production-rule change. NOT a model-validation report (that's
data/edgelab/reports/retrospective_validation_audit.md). This module
never invents a "fair probability" -- every finding is priced-vs-
realized-outcome only, or a relative comparison between two market
prices for economically-equivalent/related contracts.

Reuses, never reimplements:
  - lib.edgelab.research_dataset.build_opportunity_rows for the row
    corpus (unchanged).
  - lib.edgelab.checkpoints.select_closing_quote (via research_dataset)
    for the canonical, now-fixed closing-quote selection (PR #99) --
    the CLOSING dataset here needs no workaround at all.
  - lib.edgelab.research_reports.market_family_research for the
    (family, horizon, threshold, operator) ladder base.
  - lib.edgelab.research_splits.chronological_split for DEV/VALIDATION/
    HOLDOUT (unmodified -- this audit does NOT change the 30+-date
    maturity requirement for production adoption).
  - lib.edgelab.research_stats for Brier/CI/independent-unit-count/
    sample-size-status helpers.
  - lib.edgelab.kalshi_fees's already-computed per-row hypothetical
    return fields (via research_dataset), never recomputed.

STATISTICAL DISCIPLINE (high multiple-comparison risk by design -- this
script searches hundreds of segments):
  - Every segment reports raw contract n, independent game count, and
    distinct date count -- never just n.
  - Full game-clustered bootstrap CI is expensive and is deliberately
    NOT computed for every one of the hundreds of scanned segments
    (would dominate runtime for no decision-relevant benefit at the
    scanning stage); it IS computed for every segment that reaches the
    final ranked/appendix output, so every number a human actually sees
    carries an honest interval.
  - Every finding is classified DISCOVERY / REPLICATED / ACTIONABLE_CANDIDATE
    (see classify_finding()) -- a pattern seen only in the full sample or
    only in DEVELOPMENT is DISCOVERY at best, never promoted further.
  - Findings driven by a small number of games/teams/players are flagged,
    never silently presented as broad.

READ-ONLY: writes only to data/edgelab/analytics/latest_mispricing_discovery.json
and data/edgelab/reports/mispricing_discovery.md (plus an appendix JSON
for lower-confidence findings, kept out of the main ranked report).
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_reports import _player_game_key, _price_bucket_pct, market_family_research
from lib.edgelab.research_splits import DEVELOPMENT, HOLDOUT, VALIDATION, chronological_split, label_rows_with_split
from lib.edgelab.research_stats import (
    brier_and_log_loss_summary,
    game_clustered_bootstrap_ci,
    independent_unit_count,
    sample_size_status,
    win_rate_value_fn,
)

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")
SCHEMA_VERSION = "1"

MIN_N_DISCOVERY = 20
MIN_GAMES_DISCOVERY = 10
MIN_N_ACTIONABLE = 100
MIN_GAMES_ACTIONABLE = 20
MIN_CALIB_GAP_MATERIAL = 0.03          # 3 points -- below this, don't even call it a DISCOVERY
CONCENTRATION_FLAG_THRESHOLD = 0.40    # a single game/team/player contributing >40% of n/games is flagged


# ── Loading (mirrors run_market_price_calibration_audit.py's _load_universe) ──

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


def _settled_priced(rows):
    return [
        r for r in rows
        if r.get("settlementStatus") == "SETTLED"
        and r.get("settlementResult") in ("YES", "NO")
        and r.get("executableYesPrice") is not None
    ]


def checkpoint_datasets(rows):
    """One row per contract per checkpoint. CLOSING uses the canonical, now-fixed isClosingQuote flag (PR #99) -- no workaround needed. T_MINUS_90/60/30 are already one row per contract within that checkpoint by the row schema's own construction."""
    settled = _settled_priced(rows)
    return {
        "CLOSING": [r for r in settled if r.get("isClosingQuote")],
        "T_MINUS_30": [r for r in settled if r.get("researchCheckpoint") == "T_MINUS_30"],
        "T_MINUS_60": [r for r in settled if r.get("researchCheckpoint") == "T_MINUS_60"],
        "T_MINUS_90": [r for r in settled if r.get("researchCheckpoint") == "T_MINUS_90"],
    }


# ── Core segment statistics (fast path: no bootstrap CI) ────────────────

def segment_stats(rows, price_field="executableYesPrice", result_positive="YES", compute_ci=False):
    n = len(rows)
    if n == 0:
        return None
    avg_implied = sum(r[price_field] for r in rows) / n
    actual_rate = sum(1 for r in rows if r["settlementResult"] == result_positive) / n
    games = independent_unit_count(rows, key="gameId")
    dates = len({r["gameDate"] for r in rows if r.get("gameDate")})

    def _mean(field):
        vals = [r[field] for r in rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    yes_prefix = "hypotheticalYes" if price_field == "executableYesPrice" else "hypotheticalNo"
    out = {
        "n": n,
        "independentGames": games,
        "dateCount": dates,
        "avgImpliedProbability": round(avg_implied, 4),
        "actualHitRate": round(actual_rate, 4),
        "calibrationGap": round(actual_rate - avg_implied, 4),
        "sampleSize": sample_size_status(n, games),
        "grossROI": _mean(f"{yes_prefix}Return"),
        "feeOnlyROI": _mean(f"{yes_prefix}ReturnFeeOnly"),
        "realisticROI": _mean(f"{yes_prefix}ReturnRealisticExecution"),
    }
    if compute_ci:
        pairs = [(r[price_field], 1 if r["settlementResult"] == result_positive else 0) for r in rows]
        brier, _ = brier_and_log_loss_summary(pairs)
        ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(rows, win_rate_value_fn(lambda r: r["settlementResult"] == result_positive))
        out["brierScore"] = brier
        out["confidenceInterval"] = {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90}
    return out


def concentration_check(rows, key_fn, label):
    """Fraction of n contributed by the single largest value of key_fn -- flags a segment that is really just 'one game/team/player', not a broad pattern."""
    n = len(rows)
    if n == 0:
        return {"label": label, "topShare": None, "topValue": None}
    counts = Counter(key_fn(r) for r in rows if key_fn(r) is not None)
    if not counts:
        return {"label": label, "topShare": None, "topValue": None}
    top_value, top_count = counts.most_common(1)[0]
    return {"label": label, "topShare": round(top_count / n, 4), "topValue": top_value, "flagged": (top_count / n) >= CONCENTRATION_FLAG_THRESHOLD}


def date_partition_split(closing_rows):
    dates = [r["gameDate"] for r in closing_rows if r.get("gameDate")]
    return chronological_split(dates)


def partition_stats(rows, split_map, price_field="executableYesPrice", result_positive="YES"):
    labeled = label_rows_with_split(rows, split_map)
    out = {}
    for label in (DEVELOPMENT, VALIDATION, HOLDOUT):
        part_rows = [r for r in labeled if r.get("researchSplit") == label]
        out[label] = segment_stats(part_rows, price_field, result_positive) if part_rows else None
    return out


def classify_finding(overall, partitions, checkpoint_signs, concentration_flags, fee_survives):
    """
    DISCOVERY: material gap in the full sample, minimum descriptive
    sample, nothing else checked yet.
    REPLICATED: DISCOVERY + same-signed calibration gap in >=2 of 3 date
    partitions (never DEV alone) + (where evaluable) same-signed gap at
    >=1 other checkpoint.
    ACTIONABLE_CANDIDATE: REPLICATED + same sign in ALL THREE partitions
    + realistic-execution ROI keeps the gross-ROI sign + CALIBRATED-tier
    sample (n>=100, games>=20) in the primary dataset + not concentrated
    in one game/team/player.
    """
    if overall is None or overall["n"] < MIN_N_DISCOVERY or overall["independentGames"] < MIN_GAMES_DISCOVERY:
        return "INSUFFICIENT_SAMPLE"
    gap = overall["calibrationGap"]
    if abs(gap) < MIN_CALIB_GAP_MATERIAL:
        return "NO_MATERIAL_GAP"

    sign = 1 if gap > 0 else -1
    partition_signs = []
    for label in (DEVELOPMENT, VALIDATION, HOLDOUT):
        p = partitions.get(label)
        if p and p["n"] >= MIN_N_DISCOVERY:
            partition_signs.append(1 if p["calibrationGap"] > 0 else -1)
        else:
            partition_signs.append(None)
    non_dev_signs = [s for s in partition_signs[1:] if s is not None]
    all_signs = [s for s in partition_signs if s is not None]

    replicated_by_partition = len(non_dev_signs) >= 1 and all(s == sign for s in non_dev_signs) and len(all_signs) >= 2 and all(s == sign for s in all_signs)
    all_three_agree = len(all_signs) == 3 and all(s == sign for s in all_signs)

    other_checkpoint_signs = [s for cp, s in checkpoint_signs.items() if cp != "CLOSING" and s is not None]
    replicated_by_checkpoint = any(s == sign for s in other_checkpoint_signs) if other_checkpoint_signs else None

    concentrated = any(c.get("flagged") for c in concentration_flags)

    if not (replicated_by_partition and (replicated_by_checkpoint is not False)):
        return "DISCOVERY"

    if (all_three_agree and overall["n"] >= MIN_N_ACTIONABLE and overall["independentGames"] >= MIN_GAMES_ACTIONABLE
            and fee_survives and not concentrated):
        return "ACTIONABLE_CANDIDATE"
    return "REPLICATED"


def _grouped(rows, key_fn):
    groups = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is not None:
            groups[k].append(r)
    return groups


def _fee_survives(overall):
    if overall is None or overall.get("grossROI") is None or overall.get("realisticROI") is None:
        return False
    return (overall["grossROI"] > 0) == (overall["realisticROI"] > 0)


# ── Dimension searches ───────────────────────────────────────────────────
# Each search returns a list of "candidate" dicts: {"segment": {...descriptive
# key/value pairs...}, "rows": [...]} -- rows are consumed by the evaluation
# pass below and never themselves written to the output JSON (row-level data
# stays out of git per this audit's explicit constraint).

def search_family_price_bucket(closing_rows, side="YES"):
    price_field = "executableYesPrice" if side == "YES" else "executableNoPrice"
    result_positive = "YES" if side == "YES" else "NO"
    eligible = [r for r in closing_rows if r.get(price_field) is not None and r.get("canonicalMarketFamily")]
    groups = _grouped(eligible, lambda r: (r["canonicalMarketFamily"], _price_bucket_pct(r[price_field], width=10)))
    out = []
    for (family, bucket), grp in groups.items():
        out.append({
            "segment": {"dimension": "family_x_price_bucket", "family": family, "priceBucket": bucket, "side": side},
            "rows": grp, "priceField": price_field, "resultPositive": result_positive,
        })
    return out


def search_family_orientation(closing_rows):
    out = []
    for family in {r.get("canonicalMarketFamily") for r in closing_rows if r.get("canonicalMarketFamily")}:
        fam_rows = [r for r in closing_rows if r.get("canonicalMarketFamily") == family]
        yes_rows = [r for r in fam_rows if r.get("executableYesPrice") is not None]
        no_rows = [r for r in fam_rows if r.get("executableNoPrice") is not None]
        out.append({"segment": {"dimension": "family_orientation", "family": family, "side": "YES"}, "rows": yes_rows, "priceField": "executableYesPrice", "resultPositive": "YES"})
        out.append({"segment": {"dimension": "family_orientation", "family": family, "side": "NO"}, "rows": no_rows, "priceField": "executableNoPrice", "resultPositive": "NO"})
    return out


def search_favorite_underdog(closing_rows):
    out = []
    for family in {r.get("canonicalMarketFamily") for r in closing_rows if r.get("canonicalMarketFamily")}:
        fam_rows = [r for r in closing_rows if r.get("canonicalMarketFamily") == family and r.get("executableYesPrice") is not None]
        favorite = [r for r in fam_rows if r["executableYesPrice"] >= 0.50]
        underdog = [r for r in fam_rows if r["executableYesPrice"] < 0.50]
        out.append({"segment": {"dimension": "favorite_underdog", "family": family, "orientation": "FAVORITE_YES_GE_50C"}, "rows": favorite, "priceField": "executableYesPrice", "resultPositive": "YES"})
        out.append({"segment": {"dimension": "favorite_underdog", "family": family, "orientation": "UNDERDOG_YES_LT_50C"}, "rows": underdog, "priceField": "executableYesPrice", "resultPositive": "YES"})
    return out


def search_lineup_confirmation(closing_rows):
    out = []
    for family in {r.get("canonicalMarketFamily") for r in closing_rows if r.get("canonicalMarketFamily")}:
        fam_rows = [r for r in closing_rows if r.get("canonicalMarketFamily") == family]
        groups = _grouped(fam_rows, lambda r: r.get("lineupConfirmationState") or "UNKNOWN")
        for state, grp in groups.items():
            out.append({"segment": {"dimension": "lineup_confirmation", "family": family, "lineupConfirmationState": state}, "rows": [r for r in grp if r.get("executableYesPrice") is not None], "priceField": "executableYesPrice", "resultPositive": "YES"})
    return out


def search_tie_protected_structures(closing_rows):
    inning = [r for r in closing_rows if r.get("canonicalMarketFamily") == "inning_result" and r.get("executableYesPrice") is not None]
    groups = _grouped(inning, lambda r: (r.get("marketHorizon"), r.get("outcomeLabel")))
    out = []
    for (horizon, label), grp in groups.items():
        out.append({"segment": {"dimension": "tie_protected_structure", "marketHorizon": horizon, "outcomeLabel": label}, "rows": grp, "priceField": "executableYesPrice", "resultPositive": "YES"})
    return out


def search_scoring_environment_proxy(closing_rows):
    """Proxy for 'game scoring environment': this game's own closing game_total YES price (higher = market expects a higher-scoring game). Buckets other families' rows by their game's game_total price tercile."""
    game_total_price_by_game = {}
    for r in closing_rows:
        if r.get("canonicalMarketFamily") == "game_total" and r.get("gameId") and r.get("executableYesPrice") is not None:
            game_total_price_by_game.setdefault(r["gameId"], []).append(r["executableYesPrice"])
    game_total_avg = {g: sum(v) / len(v) for g, v in game_total_price_by_game.items()}
    if not game_total_avg:
        return []
    sorted_prices = sorted(game_total_avg.values())
    n = len(sorted_prices)
    if n < 15:
        return []
    lo_cut, hi_cut = sorted_prices[n // 3], sorted_prices[(2 * n) // 3]

    def _tercile(game_id):
        p = game_total_avg.get(game_id)
        if p is None:
            return None
        if p <= lo_cut:
            return "LOW_SCORING_ENV"
        if p >= hi_cut:
            return "HIGH_SCORING_ENV"
        return "MID_SCORING_ENV"

    out = []
    other_families = {"pitcher_strikeouts", "pitcher_outs", "hitter_hits", "hitter_total_bases", "hitter_hits_runs_rbis", "hitter_rbis", "first_inning_run"}
    for family in other_families:
        fam_rows = [r for r in closing_rows if r.get("canonicalMarketFamily") == family and r.get("executableYesPrice") is not None]
        groups = _grouped(fam_rows, lambda r: _tercile(r.get("gameId")))
        for tercile, grp in groups.items():
            out.append({"segment": {"dimension": "scoring_environment_proxy", "family": family, "scoringEnvTercile": tercile}, "rows": grp, "priceField": "executableYesPrice", "resultPositive": "YES"})
    return out


def search_price_movement_into_close(non_closing_rows_by_checkpoint):
    """Does a large price movement between an early checkpoint and CLOSING predict the eventual outcome beyond what the early price alone implies? Uses fullUniverseMarketMovementToClose (closingPrice - thisCheckpointPrice), already computed on research_dataset rows -- never recomputed here."""
    out = []
    for checkpoint, rows in non_closing_rows_by_checkpoint.items():
        eligible = [r for r in rows if r.get("fullUniverseMarketMovementToClose") is not None and r.get("executableYesPrice") is not None]
        if len(eligible) < MIN_N_DISCOVERY:
            continue
        moved_toward_yes = [r for r in eligible if r["fullUniverseMarketMovementToClose"] >= 0.05]
        moved_toward_no = [r for r in eligible if r["fullUniverseMarketMovementToClose"] <= -0.05]
        stable = [r for r in eligible if abs(r["fullUniverseMarketMovementToClose"]) < 0.05]
        for label, grp in (("MOVED_TOWARD_YES_5C_PLUS", moved_toward_yes), ("MOVED_TOWARD_NO_5C_PLUS", moved_toward_no), ("STABLE_UNDER_5C", stable)):
            out.append({"segment": {"dimension": "price_movement_into_close", "checkpoint": checkpoint, "movement": label}, "rows": grp, "priceField": "executableYesPrice", "resultPositive": "YES"})
    return out


def search_threshold_ladder(closing_rows):
    """
    Per (family, horizon, comparisonOperator, threshold) calibration --
    reuses market_family_research's own grouping/stats logic verbatim
    (never reimplemented) on the closing-only dedup dataset, then re-
    expresses each row as a "candidate" the same evaluation pass below can
    classify identically to every other dimension's candidates.
    """
    ladder_rows = market_family_research(closing_rows)
    out = []
    fam_groups = _grouped(closing_rows, lambda r: r.get("canonicalMarketFamily"))
    for row in ladder_rows:
        if row["threshold"] is None or row["settledContracts"] < MIN_N_DISCOVERY:
            continue
        fam_rows = fam_groups.get(row["canonicalMarketFamily"], [])
        matching = [
            r for r in fam_rows
            if r.get("marketHorizon") == row["marketHorizon"] and r.get("threshold") == row["threshold"]
            and r.get("comparisonOperator") == row["comparisonOperator"] and r.get("executableYesPrice") is not None
        ]
        out.append({
            "segment": {
                "dimension": "threshold_ladder", "family": row["canonicalMarketFamily"], "marketHorizon": row["marketHorizon"],
                "threshold": row["threshold"], "comparisonOperator": row["comparisonOperator"],
            },
            "rows": matching, "priceField": "executableYesPrice", "resultPositive": "YES",
        })
    return out


def ladder_adjacent_rung_check(closing_rows):
    """
    Adjacent-rung consistency for AT_LEAST/OVER threshold families:
    compares each rung's calibration gap to its immediate neighbor's,
    per (family, marketHorizon) -- NOT per-player, so a family showing
    the same-signed gap at every rung is real breadth, not one player's
    outcome echoed across thresholds sharing his games.
    """
    ladder_rows = market_family_research(closing_rows)
    groups = _grouped(
        [r for r in ladder_rows if r["threshold"] is not None and r["comparisonOperator"] in ("AT_LEAST", "OVER") and r["settledContracts"] >= MIN_N_DISCOVERY],
        lambda r: (r["canonicalMarketFamily"], r["marketHorizon"]),
    )
    out = []
    for (family, horizon), rungs in groups.items():
        rungs_sorted = sorted(rungs, key=lambda r: r["threshold"])
        if len(rungs_sorted) < 2:
            continue
        gaps = [(r["threshold"], r["calibrationError"], r["settledContracts"], r["independentGames"]) for r in rungs_sorted if r["calibrationError"] is not None]
        signs = [1 if g > 0 else (-1 if g < 0 else 0) for _, g, _, _ in gaps]
        same_sign_count = max(signs.count(1), signs.count(-1))
        out.append({
            "family": family, "marketHorizon": horizon,
            "rungs": [{"threshold": t, "calibrationGap": g, "n": n, "games": games} for t, g, n, games in gaps],
            "rungCount": len(gaps),
            "consistentSignFraction": round(same_sign_count / len(gaps), 4) if gaps else None,
        })
    return out


def search_cross_market_consistency(closing_rows):
    """
    Relative-pricing comparisons within the SAME game between economically
    related contracts (F3 vs F5 vs F7 vs full-game, same team/side; main
    total vs alternate totals). Reports a PRICE gap between two markets'
    implied probabilities for the "same" underlying proposition, and each
    side's own calibration gap separately -- never a fabricated fair
    probability, per this audit's explicit constraint.
    """
    # F3/F5/F7/full-game "Win" side, same game+team.
    win_rows = [
        r for r in closing_rows
        if r.get("outcomeLabel") == "Win" and r.get("canonicalMarketFamily") in ("inning_result", "game_result")
        and r.get("executableYesPrice") is not None and r.get("team") and r.get("gameId")
    ]
    by_game_team = _grouped(win_rows, lambda r: (r["gameId"], r["team"]))
    horizon_gaps = []
    for (game_id, team), rows in by_game_team.items():
        by_horizon = {r.get("marketHorizon"): r for r in rows}
        horizons_present = [h for h in ("F3", "F5", "F7", "FULL_GAME") if h in by_horizon]
        if len(horizons_present) >= 2:
            for i in range(len(horizons_present) - 1):
                h1, h2 = horizons_present[i], horizons_present[i + 1]
                horizon_gaps.append({
                    "gameId": game_id, "team": team, "horizonA": h1, "horizonB": h2,
                    "priceA": by_horizon[h1]["executableYesPrice"], "priceB": by_horizon[h2]["executableYesPrice"],
                    "resultA": by_horizon[h1]["settlementResult"], "resultB": by_horizon[h2]["settlementResult"],
                })

    # Compact, aggregate-only output (never the full per-game row list --
    # this audit's own constraint against committing row-level files):
    # per horizon-pair summary stats plus the 3 most extreme inversions
    # (shorter horizon priced richer than the longer one for the same
    # team, which real accumulating-win-probability structure should
    # essentially never produce) as concrete, spot-checkable examples.
    by_pair = _grouped(horizon_gaps, lambda g: (g["horizonA"], g["horizonB"]))
    pair_summaries = []
    for (h1, h2), pair_rows in by_pair.items():
        n = len(pair_rows)
        avg_gap = sum(g["priceA"] - g["priceB"] for g in pair_rows) / n
        inverted = [g for g in pair_rows if g["priceA"] > g["priceB"]]
        top_inversions = sorted(inverted, key=lambda g: -(g["priceA"] - g["priceB"]))[:3]
        pair_summaries.append({
            "horizonA": h1, "horizonB": h2, "n": n,
            "avgPriceGapAMinusB": round(avg_gap, 4),
            "inversionCount": len(inverted),
            "inversionRate": round(len(inverted) / n, 4) if n else None,
            "topInversionExamples": [
                {"gameId": g["gameId"], "team": g["team"], "priceA": g["priceA"], "resultA": g["resultA"], "priceB": g["priceB"], "resultB": g["resultB"]}
                for g in top_inversions
            ],
        })
    return {"horizonPairSummaries": sorted(pair_summaries, key=lambda p: -p["n"])}


# ── Evaluation pipeline ───────────────────────────────────────────────────

def _segment_key(segment):
    return tuple(sorted((k, v) for k, v in segment.items() if k != "checkpoint"))


def _player_key(row):
    return _player_game_key(row)


def _team_key(row):
    return row.get("team")


def _game_key(row):
    return row.get("gameId")


def evaluate_candidates(candidates_by_checkpoint, split_map, primary="CLOSING"):
    """
    candidates_by_checkpoint: {checkpoint: [candidate, ...]} for ONE
    dimension, all built by calling the same search_* function once per
    checkpoint dataset. Returns evaluated findings for the `primary`
    checkpoint's candidates only, with checkpoint_signs populated from
    whichever other checkpoints happen to contain the same segment.
    """
    sign_lookup = defaultdict(dict)
    for checkpoint, candidates in candidates_by_checkpoint.items():
        for c in candidates:
            overall = segment_stats(c["rows"], c["priceField"], c["resultPositive"])
            if overall is not None and overall["n"] >= MIN_N_DISCOVERY:
                sign_lookup[_segment_key(c["segment"])][checkpoint] = 1 if overall["calibrationGap"] > 0 else -1

    findings = []
    for c in candidates_by_checkpoint.get(primary, []):
        segment, rows, price_field, result_positive = c["segment"], c["rows"], c["priceField"], c["resultPositive"]
        overall = segment_stats(rows, price_field, result_positive)
        if overall is None:
            continue
        partitions = partition_stats(rows, split_map, price_field, result_positive)
        checkpoint_signs = dict(sign_lookup.get(_segment_key(segment), {}))
        concentration = [
            concentration_check(rows, _game_key, "gameId"),
            concentration_check(rows, _team_key, "team"),
            concentration_check(rows, _player_key, "playerGame"),
        ]
        fee_survives = _fee_survives(overall)
        classification = classify_finding(overall, partitions, checkpoint_signs, concentration, fee_survives)
        findings.append({
            "segment": segment, "overall": overall, "partitions": partitions,
            "checkpointSigns": checkpoint_signs, "concentration": concentration,
            "feeSurvives": fee_survives, "classification": classification,
        })
    return findings


def _rank_score(finding):
    """Composite score for the top-N ranking: magnitude, sample, partition consistency, fee ROI, checkpoint consistency, independence. Descriptive ordering aid only, not a statistical test."""
    o = finding["overall"]
    tier_weight = {"ACTIONABLE_CANDIDATE": 3.0, "REPLICATED": 2.0, "DISCOVERY": 1.0}.get(finding["classification"], 0.0)
    magnitude = abs(o["calibrationGap"])
    sample_weight = min(1.0, o["independentGames"] / 100.0)
    fee_weight = 1.0 if finding["feeSurvives"] else 0.3
    concentration_penalty = 0.5 if any(c.get("flagged") for c in finding["concentration"]) else 1.0
    checkpoint_bonus = 1.0 + 0.15 * sum(1 for s in finding["checkpointSigns"].values() if s == (1 if o["calibrationGap"] > 0 else -1))
    return tier_weight * magnitude * (0.5 + 0.5 * sample_weight) * fee_weight * concentration_penalty * checkpoint_bonus


def main():
    dates = _discover_dates()
    if not dates:
        print("No observation dates found -- nothing to do.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets = _load_universe(dates)
    rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations, recommendations=recommendations, bets=bets, games=games)
    cp_datasets = checkpoint_datasets(rows)
    closing_rows = cp_datasets["CLOSING"]
    split_map = date_partition_split(closing_rows)

    print(f"Dates analyzed: {dates[0]} to {dates[-1]} ({len(dates)} dates)")
    for cp, cp_rows in cp_datasets.items():
        print(f"{cp}: {len(cp_rows)} contract rows, {independent_unit_count(cp_rows, key='gameId')} games")
    print(f"Date partition: {split_map['maturity']} ({split_map['totalDates']} dates)")

    all_findings = []

    # Dimensions run once per checkpoint (for checkpoint-sign persistence).
    for search_fn, side_variants in ((search_family_price_bucket, ("YES", "NO")), (search_favorite_underdog, (None,))):
        for side in side_variants:
            candidates_by_checkpoint = {}
            for cp, cp_rows in cp_datasets.items():
                candidates_by_checkpoint[cp] = search_fn(cp_rows, side) if side else search_fn(cp_rows)
            all_findings.extend(evaluate_candidates(candidates_by_checkpoint, split_map))

    candidates_by_checkpoint = {cp: search_family_orientation(cp_rows) for cp, cp_rows in cp_datasets.items()}
    all_findings.extend(evaluate_candidates(candidates_by_checkpoint, split_map))

    # Dimensions run only on CLOSING (checkpoint sample too thin elsewhere, or concept is CLOSING-specific).
    for search_fn in (search_lineup_confirmation, search_tie_protected_structures, search_scoring_environment_proxy, search_threshold_ladder):
        all_findings.extend(evaluate_candidates({"CLOSING": search_fn(closing_rows)}, split_map))

    # Price-movement-into-close: checkpoint-specific by construction.
    movement_candidates = search_price_movement_into_close({cp: cp_rows for cp, cp_rows in cp_datasets.items() if cp != "CLOSING"})
    movement_by_checkpoint = defaultdict(list)
    for c in movement_candidates:
        movement_by_checkpoint[c["segment"]["checkpoint"]].append(c)
    for cp, cands in movement_by_checkpoint.items():
        all_findings.extend(evaluate_candidates({cp: cands}, split_map, primary=cp))

    ladder_rungs = ladder_adjacent_rung_check(closing_rows)
    cross_market = search_cross_market_consistency(closing_rows)

    material = [f for f in all_findings if f["classification"] not in ("INSUFFICIENT_SAMPLE", "NO_MATERIAL_GAP")]
    for f in material:
        f["rankScore"] = round(_rank_score(f), 5)
    material.sort(key=lambda f: -f["rankScore"])

    top_n = material[:15]
    appendix = material[15:]

    tier_counts = Counter(f["classification"] for f in all_findings)

    generated_at = ids.utc_now_iso()
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "datesAnalyzed": dates,
        "report": {
            "coverage": {
                "checkpointContractCounts": {cp: len(cp_rows) for cp, cp_rows in cp_datasets.items()},
                "checkpointGameCounts": {cp: independent_unit_count(cp_rows, key="gameId") for cp, cp_rows in cp_datasets.items()},
                "datePartition": split_map,
            },
            "totalCandidatesScanned": len(all_findings),
            "classificationCounts": dict(tier_counts),
            "topFindings": top_n,
            "ladderAdjacentRungChecks": ladder_rungs,
            "crossMarketConsistency": cross_market,
        },
    }
    appendix_payload = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "report": {"appendixFindings": appendix}}

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(os.path.join(ANALYTICS_DIR, "latest_mispricing_discovery.json"), "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    with open(os.path.join(ANALYTICS_DIR, "latest_mispricing_discovery_appendix.json"), "w") as f:
        json.dump(appendix_payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"Total candidates scanned: {len(all_findings)}")
    print(f"Classification counts: {dict(tier_counts)}")
    print(f"Top findings written: {len(top_n)}; appendix: {len(appendix)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
