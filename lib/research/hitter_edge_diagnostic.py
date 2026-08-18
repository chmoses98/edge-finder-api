#!/usr/bin/env python3
"""
lib/research/hitter_edge_diagnostic.py
==========================================
Edge-inversion diagnostic (ANALYSIS ONLY -- see this milestone's explicit
instruction: investigate, do not tune). The retrospective hitter
projection audit (data/edgelab/hitter_validation/summary.md Sec.6) found
that declared edge above roughly 5 percentage points is an ANTI-signal
in the archived corpus: larger |modelProbability - executableKalshiPrice|
correlates with WORSE calibration and WORSE simulated ROI, not better.
This module breaks that finding down across every dimension the
archived data actually supports, and checks whether it survives basic
de-correlation (equal weighting by game/player cluster instead of by
raw row) -- per this repository's own established caution elsewhere
that raw row count overstates independent evidence for this corpus
(docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md Sec.9;
data/edgelab/hitter_validation/summary.md Sec.12: N=1,783 resolvable
rows come from only 3 calendar dates / 8 distinct game-dates / 125
distinct player-dates).

DOES NOT CHANGE, TUNE, OR RECOMMEND CHANGING any model formula,
threshold, weight, prior, shrinkage level, or edge/confidence cutoff.
Produces findings and hypotheses only -- see
data/edgelab/hitter_validation/edge_inversion_diagnostic.md for the
human-readable writeup this module's output feeds.

Reuses lib.research.hitter_projection_audit's own graded-row shape and
calibration/ROI primitives directly (never a second grading
implementation) -- this module's only job is to SLICE that already-
graded corpus along more dimensions than the primary audit report does
by default, and to compare row-weighted vs cluster-weighted results.
"""
import statistics
from collections import defaultdict

from lib.research.hitter_projection_audit import (
    EDGE_BUCKETS,
    fee_adjusted_break_even_probability,
    overall_calibration,
    roi_simulation,
)

LARGE_EDGE_THRESHOLD = 0.05  # matches the audit's own 5pp finding exactly


def net_executable_edge(row):
    """
    modelProbability minus the FEE-ADJUSTED break-even probability at
    the row's own entry price -- distinct from computedEdge/
    rawProbabilityEdge (which compare against the raw, fee-blind
    executable price). Reuses
    lib.edgelab.kalshi_fees.fee_adjusted_break_even_probability (via
    hitter_projection_audit's own import) -- never a second fee
    formula. None if either input is unavailable.
    """
    model_prob = row.get("modelProbability")
    entry_price = row.get("executableKalshiPrice")
    if model_prob is None or entry_price is None or not (0 < entry_price < 1):
        return None
    break_even = fee_adjusted_break_even_probability(entry_price)
    if break_even is None:
        return None
    return round(model_prob - break_even, 6)


def is_large_edge(row, threshold=LARGE_EDGE_THRESHOLD):
    edge = row.get("computedEdge")
    return edge is not None and abs(edge) >= threshold


def split_by_edge_magnitude(rows, threshold=LARGE_EDGE_THRESHOLD):
    small, large = [], []
    for r in rows:
        if r.get("computedEdge") is None:
            continue
        (large if is_large_edge(r, threshold) else small).append(r)
    return small, large


# ---------------------------------------------------------------------------
# Dimension breakdowns (large-edge cohort only, compared to the small-edge
# cohort and to the overall corpus)
# ---------------------------------------------------------------------------

def _dimension_breakdown(rows, key_fn, min_n_for_status=1):
    by_key = defaultdict(list)
    for r in rows:
        by_key[key_fn(r)].append(r)
    out = []
    for key, key_rows in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        calib = overall_calibration(key_rows)
        roi = roi_simulation(key_rows)
        out.append({
            "key": key, "n": calib["n"], "status": calib["status"],
            "calibrationError": calib["calibrationError"], "actualWinRate": calib["actualWinRate"],
            "brierScore": calib["brierScore"], "roi": roi["roi"], "netPL": roi["netPL"],
            "independentEvidence": calib["independentEvidence"],
        })
    return out


def large_edge_by_market_family(large_edge_rows):
    return _dimension_breakdown(large_edge_rows, lambda r: r.get("marketFamily"))


def large_edge_by_probability_bucket(large_edge_rows):
    def bucket(r):
        p = r.get("modelProbability")
        if p is None:
            return "UNKNOWN"
        for lo, hi, label in [
            (0.0, 0.35, "<35%"), (0.35, 0.50, "35-49.9%"), (0.50, 0.65, "50-64.9%"),
            (0.65, 0.80, "65-79.9%"), (0.80, 1.0001, "80%+"),
        ]:
            if lo <= p < hi:
                return label
        return "UNKNOWN"
    return _dimension_breakdown(large_edge_rows, bucket)


def large_edge_by_threshold(large_edge_rows):
    return _dimension_breakdown(large_edge_rows, lambda r: f"{r.get('marketFamily')}::{r.get('threshold')}")


def large_edge_by_player(large_edge_rows, min_n=3):
    """Only players with >= min_n large-edge rows -- a single row per player is not a per-player finding, just a data point; see the correlation/concentration check below for whether a few players dominate the pattern."""
    breakdown = _dimension_breakdown(large_edge_rows, lambda r: f"{r.get('player')} ({r.get('playerId')})")
    return [b for b in breakdown if b["n"] >= min_n]


def large_edge_by_game_date(large_edge_rows):
    return _dimension_breakdown(large_edge_rows, lambda r: f"{r.get('sourceDate')}::{r.get('matchup')}")


def large_edge_by_lineup_slot(large_edge_rows):
    return _dimension_breakdown(large_edge_rows, lambda r: (r.get("segment") or {}).get("lineupSlot"))


def large_edge_by_home_away(large_edge_rows):
    return _dimension_breakdown(large_edge_rows, lambda r: (r.get("segment") or {}).get("offenseSide"))


def large_edge_by_executable_price_bucket(large_edge_rows):
    def bucket(r):
        p = r.get("executableKalshiPrice")
        if p is None:
            return "UNKNOWN"
        for lo, hi, label in [
            (0.0, 0.10, "<10c"), (0.10, 0.30, "10-29c"), (0.30, 0.50, "30-49c"),
            (0.50, 0.70, "50-69c"), (0.70, 0.90, "70-89c"), (0.90, 1.0001, "90c+"),
        ]:
            if lo <= p < hi:
                return label
        return "UNKNOWN"
    return _dimension_breakdown(large_edge_rows, bucket)


def large_edge_by_net_executable_edge_bucket(large_edge_rows):
    def bucket(r):
        net_edge = net_executable_edge(r)
        if net_edge is None:
            return "UNKNOWN"
        abs_edge = abs(net_edge)
        for lo, hi, label in EDGE_BUCKETS:
            if lo <= abs_edge < hi:
                return label
        return "20pp+"
    return _dimension_breakdown(large_edge_rows, bucket)


def _threshold_rank_by_family(rows):
    """{marketFamily: sorted distinct thresholds} -- used to classify 'tail' (top 1-2 rungs actually observed) vs 'non-tail' without hardcoding a threshold value per family (families have different natural ranges)."""
    by_family = defaultdict(set)
    for r in rows:
        if r.get("marketFamily") and r.get("threshold") is not None:
            by_family[r["marketFamily"]].add(r["threshold"])
    return {family: sorted(thresholds) for family, thresholds in by_family.items()}


def large_edge_tail_vs_non_tail(large_edge_rows, all_primary_rows, tail_rank_from_top=2):
    """
    'Tail' = a threshold among the top `tail_rank_from_top` rungs
    ACTUALLY OBSERVED for that market family in the full primary corpus
    (never a hardcoded absolute threshold -- hitter_hits tops out around
    3-4+, hitter_total_bases around 5-6+; a fixed cutoff would silently
    misclassify one family's normal range as another's tail).
    """
    rank_by_family = _threshold_rank_by_family(all_primary_rows)
    tail_thresholds = {
        family: set(thresholds[-tail_rank_from_top:])
        for family, thresholds in rank_by_family.items()
    }

    def label(r):
        family, threshold = r.get("marketFamily"), r.get("threshold")
        if family not in tail_thresholds or threshold is None:
            return "UNKNOWN"
        return "TAIL" if threshold in tail_thresholds[family] else "NON_TAIL"

    return _dimension_breakdown(large_edge_rows, label)


# ---------------------------------------------------------------------------
# De-correlation / concentration check
# ---------------------------------------------------------------------------

def cluster_weighted_vs_row_weighted(rows, cluster_key_fn):
    """
    Compares the ROW-weighted mean simulated net P/L (what
    roi_simulation's own netPL/ROI already reports -- every row counted
    once, so a hitter with 6 thresholds contributes 6x the weight of a
    hitter with 1) against the CLUSTER-weighted mean (every cluster --
    e.g. one (date, player) pair -- contributes exactly once, via that
    cluster's OWN mean P/L first). If the row-weighted finding survives
    at roughly the same sign/magnitude under cluster weighting, it is
    not merely an artifact of a few heavily-thresholded hitters/games
    dominating the row count. Returns None fields (never a fabricated
    number) when a cluster has no qualifying (bet-graded) rows.
    """
    by_cluster = defaultdict(list)
    for r in rows:
        by_cluster[cluster_key_fn(r)].append(r)

    cluster_means = []
    for cluster_rows in by_cluster.values():
        pls = [r["simulatedBetNetPL"] for r in cluster_rows if r.get("simulatedBetNetPL") is not None]
        if pls:
            cluster_means.append(statistics.mean(pls))

    row_pls = [r["simulatedBetNetPL"] for r in rows if r.get("simulatedBetNetPL") is not None]

    return {
        "distinctClusters": len(by_cluster),
        "clustersWithQualifyingBets": len(cluster_means),
        "rowWeightedMeanNetPL": round(statistics.mean(row_pls), 4) if row_pls else None,
        "rowWeightedN": len(row_pls),
        "clusterWeightedMeanNetPL": round(statistics.mean(cluster_means), 4) if cluster_means else None,
        "pctClustersWithNegativeMeanNetPL": round(sum(1 for m in cluster_means if m < 0) / len(cluster_means), 4) if cluster_means else None,
    }


def concentration_check(large_edge_rows):
    """
    Does the large-edge underperformance come from a small number of
    outlier hitters/games, or is it broadly spread? Reports both
    (sourceDate, playerId) and (sourceDate, matchup) cluster weighting
    (see cluster_weighted_vs_row_weighted) -- a finding that only
    survives row-weighting and reverses/vanishes under BOTH cluster
    weightings would be a real concentration artifact; a finding that
    survives both is broad-based, not a few bad actors.
    """
    return {
        "byPlayerDate": cluster_weighted_vs_row_weighted(
            large_edge_rows, lambda r: (r.get("sourceDate"), r.get("playerId") or r.get("player")),
        ),
        "byGameDate": cluster_weighted_vs_row_weighted(
            large_edge_rows, lambda r: (r.get("sourceDate"), r.get("matchup")),
        ),
    }


def build_edge_inversion_diagnostic(primary_rows, threshold=LARGE_EDGE_THRESHOLD):
    """Top-level entry point: returns the full diagnostic report dict."""
    small_edge, large_edge = split_by_edge_magnitude(primary_rows, threshold)

    return {
        "largeEdgeThreshold": threshold,
        "smallEdgeCohort": {
            "calibration": overall_calibration(small_edge),
            "roi": roi_simulation(small_edge),
        },
        "largeEdgeCohort": {
            "calibration": overall_calibration(large_edge),
            "roi": roi_simulation(large_edge),
        },
        "byMarketFamily": large_edge_by_market_family(large_edge),
        "byProbabilityBucket": large_edge_by_probability_bucket(large_edge),
        "byThreshold": large_edge_by_threshold(large_edge),
        "byPlayer": large_edge_by_player(large_edge),
        "byGameDate": large_edge_by_game_date(large_edge),
        "byLineupSlot": large_edge_by_lineup_slot(large_edge),
        "byHomeAway": large_edge_by_home_away(large_edge),
        "byExecutablePriceBucket": large_edge_by_executable_price_bucket(large_edge),
        "byNetExecutableEdgeBucket": large_edge_by_net_executable_edge_bucket(large_edge),
        "tailVsNonTail": large_edge_tail_vs_non_tail(large_edge, primary_rows),
        "concentrationCheck": concentration_check(large_edge),
    }
