"""
lib/edgelab/market_intelligence.py
=======================================
EdgeLab Phase 2 Milestone 6 (docs/EDGELAB_MARKET_INTELLIGENCE.md): a
RESEARCH-ONLY Market Intelligence Engine. Turns "tracking what happened"
(Milestones 1-3), "measuring how well-calibrated the model is"
(Milestone 2), and "which expression of an edge was cleanest"
(Milestone 5) into "what historically works" -- expression performance
profiles, opportunity-cost measurement, pass analysis, hypothetical
strategy replays, edge stability, and market health scores.

This module DOES NOT change production recommendations, staking, or bet
selection in any way. It is read-only over lib.edgelab.analytics's
existing views, reuses lib.edgelab.calibration's existing sample-size
gate and metrics rather than re-deriving them, and reuses
lib.edgelab.market_comparison's clustering/domination/comparison-status
output rather than re-clustering. Every strategy_experiments() result is
explicitly labeled SIMULATION_LABEL -- a hypothetical replay of the real
settled PlacedBet ledger under a named rule, never a claim about future
performance, and never a change to any stored record.
"""
import collections
import math

from lib.edgelab.calibration import (
    MIN_N_CALIBRATED,
    calibration_status,
    market_family_calibration,
)
from lib.edgelab.market_comparison import (
    HORIZON_F5,
    STATUS_BEST_EXPRESSION,
    STATUS_DOMINATED_MARKET,
    build_comparisons,
)

# Matches lib.edgelab.calibration.edge_bucket_calibration's own bucket
# width -- a consistent estimatedEdge scale across modules, not a new
# convention.
EDGE_BUCKET_WIDTH = 2.0

# Every strategy_experiments() result dict carries this literal marker so
# no downstream consumer can mistake a hypothetical replay for a real
# recorded outcome.
SIMULATION_LABEL = "HYPOTHETICAL_SIMULATION"

STABLE = "STABLE"
VOLATILE = "VOLATILE"
FALSE_EDGE = "FALSE_EDGE"
UNKNOWN_STABILITY = "UNKNOWN"

_LINEUP_UNCONFIRMED_STATES = frozenset({"PROJECTED", "PARTIAL", "UNCONFIRMED"})

HEALTH_WEIGHTS = {
    "sampleQuality": 0.25,
    "clvQuality": 0.20,
    "calibrationQuality": 0.25,
    "stability": 0.20,
    "recommendationQuality": 0.10,
}


def _fetch_dicts(session, sql):
    rel = session.sql(sql)
    cols = rel.columns
    return [dict(zip(cols, r)) for r in rel.fetchall()]


def _comparisons_by_eval_id(comparisons):
    return {c["modelEvaluationId"]: c for c in comparisons if c.get("modelEvaluationId")}


def _bets_by_eval_id(bets):
    return {b["modelEvaluationId"]: b for b in bets if b.get("modelEvaluationId")}


def _settlement_by_ticker(session):
    """{} when the `settlements` entity isn't available in this session -- never guessed."""
    if not session.is_available("settlements"):
        return {}
    return {r["marketTicker"]: r for r in _fetch_dicts(session, "SELECT * FROM v_settlements") if r.get("marketTicker")}


def _settled_bet_result_by_eval_id(session):
    """
    {modelEvaluationId: 'WIN'/'LOSS'} for every settled PlacedBet with a
    real WIN/LOSS result. This is the ONLY honest source of "did this
    market's edge win" in this schema: Settlement.result is YES/NO (did
    THIS TICKER's YES side settle true), not WIN/LOSS, and turning a
    YES/NO into a WIN/LOSS requires knowing which side (YES/NO) was
    backed -- lib.edgelab.settlement.derive_bet_result() needs exactly
    that `side`. PlacedBet.side is always recorded (required at bet
    time); ModelEvaluation.side is documented as "usually null at
    evaluation time" and confirmed always null in every real committed
    record -- there is no reliable side to attribute a Settlement's
    YES/NO to for a market that was never actually bet. So this module
    never derives a hypothetical win/loss for an unbet market -- see
    pass_analysis()'s and _classify_market_edge_stability()'s docstrings.
    """
    if not session.is_available("bets"):
        return {}
    return {
        b["modelEvaluationId"]: b["result"]
        for b in _fetch_dicts(session, "SELECT * FROM v_placed_bets WHERE status = 'settled' AND result IN ('WIN', 'LOSS')")
        if b.get("modelEvaluationId")
    }


def _market_key(row):
    ticker = row.get("marketTicker")
    return ticker if ticker else (row.get("gameId"), row.get("selection"), row.get("side"), row.get("threshold"))


def edge_bucket(estimated_edge, bucket_width=EDGE_BUCKET_WIDTH):
    if estimated_edge is None:
        return None
    return math.floor(estimated_edge / bucket_width) * bucket_width


# ── Expression Performance Profiles (item 3) ──────────────────────────────

def expression_performance_profiles(session, comparisons=None):
    """
    One profile per canonicalMarketFamily combining
    lib.edgelab.calibration.market_family_calibration()'s existing
    n/winRate/roi/avgClv/calibrationError/status (never re-derived here)
    with four NEW frequency measures:
      - recommendationFrequency: BET_PLACED recommendations / total ModelEvaluation rows in that family
      - passFrequency: PASS_* recommendations / total ModelEvaluation rows in that family
      - bestExpressionFrequency: BEST_EXPRESSION / total CLUSTERED market_comparison rows in that family
      - dominatedFrequency: DOMINATED_MARKET / total CLUSTERED market_comparison rows in that family
    "Total ModelEvaluation rows" is the same denominator
    lib.edgelab.model_evaluation's population reports already use.
    """
    if comparisons is None:
        comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []

    calibration_rows = {r["canonicalMarketFamily"]: r for r in market_family_calibration(session)}

    eval_totals = {}
    if session.is_available("model_evaluations"):
        for family, n in session.fetchall("SELECT canonicalMarketFamily, COUNT(*) FROM v_model_evaluations GROUP BY 1"):
            eval_totals[family] = n

    rec_counts = collections.defaultdict(collections.Counter)
    if session.is_available("recommendations"):
        for family, status, n in session.fetchall("SELECT canonicalMarketFamily, status, COUNT(*) FROM v_recommendations GROUP BY 1, 2"):
            rec_counts[family][status] = n

    cluster_counts = collections.defaultdict(collections.Counter)
    for c in comparisons:
        if c.get("clusterId"):
            cluster_counts[c["canonicalMarketFamily"]][c["comparisonStatus"]] += 1

    def _freq(numerator, denominator):
        return (numerator / denominator) if denominator else None

    families = {f for f in (set(eval_totals) | set(calibration_rows) | set(rec_counts) | set(cluster_counts)) if f}
    results = []
    for family in sorted(families):
        total_evaluated = eval_totals.get(family)
        rec = rec_counts.get(family, collections.Counter())
        passed = sum(n for status, n in rec.items() if status and status.startswith("PASS_"))
        clustered = cluster_counts.get(family, collections.Counter())
        clustered_total = sum(clustered.values())

        profile = {
            "canonicalMarketFamily": family,
            "totalEvaluated": total_evaluated,
            "recommendationFrequency": _freq(rec.get("BET_PLACED", 0), total_evaluated),
            "passFrequency": _freq(passed, total_evaluated),
            "clusteredComparisonCount": clustered_total,
            "bestExpressionFrequency": _freq(clustered.get(STATUS_BEST_EXPRESSION, 0), clustered_total),
            "dominatedFrequency": _freq(clustered.get(STATUS_DOMINATED_MARKET, 0), clustered_total),
        }
        cal = calibration_rows.get(family)
        if cal:
            profile.update({
                "n": cal["n"], "winRate": cal["winRate"], "roi": cal["roi"], "avgClv": cal["avgClv"],
                "calibrationError": cal["calibrationError"], "calibrationStatus": cal["status"],
            })
        else:
            profile.update({
                "n": 0, "winRate": None, "roi": None, "avgClv": None,
                "calibrationError": None, "calibrationStatus": calibration_status(0),
            })
        results.append(profile)
    return results


# ── Opportunity Cost Analysis (item 4) ────────────────────────────────────

def opportunity_cost_analysis(session, comparisons=None):
    """
    For every PLACED bet whose market belongs to a multi-member
    lib.edgelab.market_comparison cluster where it was NOT the top-ranked
    (comparisonRank == 1) expression, measures:
      - lostEstimatedEdge: the top-ranked market's estimatedEdge minus this bet's
      - lostClv: the top-ranked market's clv minus this bet's clv --
        ONLY when the top-ranked alternative was ALSO itself placed and
        has a real recorded clv (never fabricated for a market that was
        never actually bet)
      - lostRoi: the top-ranked alternative's realized ROI (netProfitLoss/stake)
        minus this bet's own realized ROI -- same "only when the
        alternative was itself placed and settled" rule
      - dominatedByBestExpression: True when this bet's own market
        carries comparisonStatus DOMINATED_MARKET
    Gated by lib.edgelab.calibration.calibration_status() -- "how often"
    is reported, but no superiority claim is made below the sample
    threshold. Never recommends a change; this is measurement only.
    """
    if comparisons is None:
        comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []
    bets_by_eval_id = _bets_by_eval_id(_fetch_dicts(session, "SELECT * FROM v_placed_bets")) if session.is_available("bets") else {}

    def _roi(eval_id):
        bet = bets_by_eval_id.get(eval_id)
        if not bet or bet.get("status") != "settled" or bet.get("result") not in ("WIN", "LOSS") or not bet.get("stake"):
            return None
        return (bet.get("netProfitLoss") or 0.0) / bet["stake"]

    by_cluster = collections.defaultdict(list)
    for c in comparisons:
        if c.get("clusterId"):
            by_cluster[c["clusterId"]].append(c)

    cases = []
    placed_and_clustered = 0
    for members in by_cluster.values():
        if len(members) < 2:
            continue
        ranked = [c for c in members if c.get("comparisonRank")]
        if not ranked:
            continue
        top = min(ranked, key=lambda c: c["comparisonRank"])
        for c in members:
            if not c.get("placedBetIndicator"):
                continue
            placed_and_clustered += 1
            if c["marketTicker"] == top["marketTicker"]:
                continue  # already the top-ranked expression -- no opportunity cost

            lost_edge = None
            if top.get("estimatedEdge") is not None and c.get("estimatedEdge") is not None:
                lost_edge = top["estimatedEdge"] - c["estimatedEdge"]

            lost_clv, lost_roi = None, None
            if top.get("placedBetIndicator"):
                if top.get("clv") is not None and c.get("clv") is not None:
                    lost_clv = top["clv"] - c["clv"]
                top_roi, own_roi = _roi(top.get("modelEvaluationId")), _roi(c.get("modelEvaluationId"))
                if top_roi is not None and own_roi is not None:
                    lost_roi = top_roi - own_roi

            cases.append({
                "gameId": c["gameId"],
                "clusterId": c["clusterId"],
                "betMarketTicker": c["marketTicker"],
                "betId": c.get("betId"),
                "betterExpressionMarketTicker": top["marketTicker"],
                "betterExpressionWasAlsoPlaced": bool(top.get("placedBetIndicator")),
                "lostEstimatedEdge": lost_edge,
                "lostClv": lost_clv,
                "lostRoi": lost_roi,
                "dominatedByBestExpression": c["comparisonStatus"] == STATUS_DOMINATED_MARKET,
            })

    n = placed_and_clustered
    return {
        "sampleSize": n,
        "sampleStatus": calibration_status(n),
        "opportunityCostCaseCount": len(cases),
        "opportunityCostFrequency": (len(cases) / n) if n else None,
        "cases": cases,
    }


# ── Pass Analysis (item 5) ────────────────────────────────────────────────

# Real recommendation.status vocabulary this repo's pipeline actually
# writes (confirmed against committed data): RECOMMENDED, BET_PLACED,
# PASS_NO_EDGE, PASS_DATA_QUALITY. "RECOMMENDED" always carries
# betPlaced=False -- it IS the "recommended but not bet" category the
# milestone asks for, under its real name (lib.edgelab.calibration's own
# recommendation_path_calibration() checks for a literal
# 'RECOMMENDED_NOT_BET' string that never appears in real data -- a
# pre-existing, out-of-scope mismatch this module does not inherit).
_STATUS_TO_PASS_CATEGORY = {
    "RECOMMENDED": "RECOMMENDED_NOT_BET",
    "PASS_NO_EDGE": "PASS_NO_EDGE",
    "PASS_DATA_QUALITY": "INSUFFICIENT_SUPPORT",
}


def pass_analysis(session, comparisons=None):
    """
    Groups Recommendation rows into RECOMMENDED_NOT_BET / PASS_NO_EDGE /
    INSUFFICIENT_SUPPORT (see _STATUS_TO_PASS_CATEGORY) plus a DOMINATED
    category (recommendations whose linked ModelEvaluation carries
    comparisonStatus == DOMINATED_MARKET in
    lib.edgelab.market_comparison). For each group, reports n and, ONLY
    when this session's `settlements` entity is available, how many of
    that group's markets eventually reached each settlementStatus
    (SETTLED / VOID / UNAVAILABLE / SETTLEMENT_UNRESOLVED).

    This deliberately does NOT compute a hypothetical win/loss or return
    for these markets -- a REAL-DATA FINDING made while building this
    function: Settlement.result is YES/NO (did THIS TICKER's YES side
    settle true), not WIN/LOSS, and turning that into a win/loss requires
    knowing which side (YES/NO) the recommendation implicitly favored.
    Recommendation carries no `side` field at all, and ModelEvaluation's
    own `side` field is documented as "usually null at evaluation time"
    and is in fact null on every real committed record -- there is no
    non-fabricated side to attribute a settlement outcome to for a
    market that was never actually bet. (Contrast with
    strategy_experiments(), which only ever replays REAL settled
    PlacedBet rows -- those always carry a real `side` and a real
    pipeline-derived WIN/LOSS `result`, so no side is ever guessed
    there either.) See docs/EDGELAB_MARKET_INTELLIGENCE.md's limitations.
    """
    if not session.is_available("recommendations"):
        return []
    if comparisons is None:
        comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []
    comparisons_by_eval_id = _comparisons_by_eval_id(comparisons)
    settlement_by_ticker = _settlement_by_ticker(session)

    groups = collections.defaultdict(list)
    for r in _fetch_dicts(session, "SELECT * FROM v_recommendations"):
        category = _STATUS_TO_PASS_CATEGORY.get(r.get("status"))
        if category:
            groups[category].append(r)
        comparison = comparisons_by_eval_id.get(r.get("modelEvaluationId"))
        if comparison and comparison.get("comparisonStatus") == STATUS_DOMINATED_MARKET:
            groups["DOMINATED"].append(r)

    results = []
    for category in sorted(groups):
        group_rows = groups[category]
        settlement_status_counts = collections.Counter()
        for r in group_rows:
            ticker = r.get("marketTicker")
            settlement = settlement_by_ticker.get(ticker) if ticker else None
            if settlement:
                settlement_status_counts[settlement.get("settlementStatus")] += 1

        results.append({
            "category": category,
            "n": len(group_rows),
            "status": calibration_status(len(group_rows)),
            "settlementStatusCounts": dict(settlement_status_counts),
            "note": "No hypothetical win/loss or return is computed for these markets -- Recommendation/"
                    "ModelEvaluation never record which side (YES/NO) was implicitly favored, so a "
                    "settlement outcome cannot be honestly attributed. Only settlement STATUS coverage "
                    "(did the market resolve at all) is reported.",
        })
    return results


# ── Strategy Experiments (item 6) -- research-only simulations ──────────

def _bet_roi_stats(bets):
    settled = [b for b in bets if b.get("status") == "settled" and b.get("result") in ("WIN", "LOSS") and b.get("stake")]
    n = len(settled)
    total_stake = sum(b["stake"] for b in settled)
    total_pl = sum(b.get("netProfitLoss") or 0.0 for b in settled)
    wins = sum(1 for b in settled if b["result"] == "WIN")
    return {
        "n": n,
        "status": calibration_status(n),
        "winRate": (wins / n) if n else None,
        "roi": (total_pl / total_stake) if total_stake else None,
        "totalStake": total_stake if n else None,
        "totalNetProfitLoss": total_pl if n else None,
    }


def _cluster_lookup(comparisons):
    by_cluster = collections.defaultdict(list)
    for c in comparisons:
        if c.get("clusterId"):
            by_cluster[c["clusterId"]].append(c)
    return by_cluster


def _dominated_replacement_target(comparison, by_cluster):
    if comparison.get("comparisonStatus") != STATUS_DOMINATED_MARKET:
        return None
    dominant_ticker = comparison.get("dominantMarketTicker")
    for c in by_cluster.get(comparison.get("clusterId"), []):
        if c["marketTicker"] == dominant_ticker:
            return c
    return None


def _f5_preference_target(comparison, by_cluster):
    if comparison.get("horizon") == HORIZON_F5:
        return None  # already F5, nothing to swap
    for c in by_cluster.get(comparison.get("clusterId"), []):
        if c.get("horizon") == HORIZON_F5:
            return c
    return None


def _simulate_swap(all_bets, comparisons_by_eval_id, bets_by_eval_id, by_cluster, pick_target):
    """
    Replays the settled bet ledger, replacing any bet for which
    `pick_target(comparison_row, by_cluster)` returns a DIFFERENT
    comparison row -- ONLY when that target market was ALSO itself
    actually placed AND settled (a real recorded outcome, never
    fabricated). The swap preserves the ORIGINAL bet's stake and
    substitutes the target bet's own realized return-per-dollar
    (netProfitLoss/stake): a claim about which expression paid better
    per dollar risked, not a claim about what stake would have been
    used. Bets with no eligible target pass through unchanged. Returns
    (simulated_bets, swappedCount).
    """
    simulated = []
    swapped = 0
    for bet in all_bets:
        if bet.get("status") != "settled" or bet.get("result") not in ("WIN", "LOSS") or not bet.get("stake"):
            simulated.append(bet)
            continue
        comparison = comparisons_by_eval_id.get(bet.get("modelEvaluationId"))
        target = pick_target(comparison, by_cluster) if comparison else None
        if target is None or target["marketTicker"] == comparison["marketTicker"] or not target.get("placedBetIndicator"):
            simulated.append(bet)
            continue
        target_bet = bets_by_eval_id.get(target.get("modelEvaluationId"))
        if not target_bet or target_bet.get("status") != "settled" or target_bet.get("result") not in ("WIN", "LOSS") or not target_bet.get("stake"):
            simulated.append(bet)
            continue
        return_per_dollar = (target_bet.get("netProfitLoss") or 0.0) / target_bet["stake"]
        simulated_bet = dict(bet)
        simulated_bet["netProfitLoss"] = bet["stake"] * return_per_dollar
        simulated_bet["result"] = target_bet["result"]
        simulated.append(simulated_bet)
        swapped += 1
    return simulated, swapped


def _delta_roi(stats, baseline):
    if stats.get("roi") is None or baseline.get("roi") is None:
        return None
    return stats["roi"] - baseline["roi"]


def strategy_experiments(session, comparisons=None):
    """
    Research-only, clearly-labeled (SIMULATION_LABEL) hypothetical
    replays of the REAL settled PlacedBet ledger under a named rule
    change. Every experiment reports the ACTUAL baseline (all settled
    bets, unmodified) alongside the simulated variant so the delta is
    always visible, gated by lib.edgelab.calibration.calibration_status()
    -- never a superiority claim below the sample threshold. Nothing
    here changes any stored record; every result is a pure in-memory
    replay.
    """
    if not session.is_available("bets"):
        return {"simulationLabel": SIMULATION_LABEL, "baseline": None, "experiments": []}

    all_bets = _fetch_dicts(session, "SELECT * FROM v_placed_bets")
    baseline = _bet_roi_stats(all_bets)

    if comparisons is None:
        comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []
    comparisons_by_eval_id = _comparisons_by_eval_id(comparisons)
    bets_by_eval_id = _bets_by_eval_id(all_bets)
    by_cluster = _cluster_lookup(comparisons)

    experiments = []

    dominated_bets, swapped = _simulate_swap(all_bets, comparisons_by_eval_id, bets_by_eval_id, by_cluster, _dominated_replacement_target)
    dominated_stats = _bet_roi_stats(dominated_bets)
    experiments.append({
        "name": "DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION",
        "description": "Every settled bet on a DOMINATED_MARKET is replaced with its dominant "
                        "alternative's realized return-per-dollar, ONLY when that alternative was "
                        "itself actually placed and settled.",
        "swappedBetCount": swapped,
        **dominated_stats,
        "deltaRoiVsBaseline": _delta_roi(dominated_stats, baseline),
    })

    f5_bets, f5_swapped = _simulate_swap(all_bets, comparisons_by_eval_id, bets_by_eval_id, by_cluster, _f5_preference_target)
    f5_stats = _bet_roi_stats(f5_bets)
    experiments.append({
        "name": "ALWAYS_PREFER_F5",
        "description": "Every settled non-F5 WIN-thesis bet is replaced with its cluster's F5 "
                        "alternative's realized return-per-dollar, ONLY when that alternative was "
                        "itself actually placed and settled.",
        "swappedBetCount": f5_swapped,
        **f5_stats,
        "deltaRoiVsBaseline": _delta_roi(f5_stats, baseline),
    })

    no_bullpen_bets = [
        b for b in all_bets
        if not (b.get("canonicalMarketFamily") == "game_result" and "BULLPEN_DISADVANTAGE" in (b.get("thesisTags") or []))
    ]
    no_bullpen_stats = _bet_roi_stats(no_bullpen_bets)
    experiments.append({
        "name": "NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE",
        "description": "Excludes every settled full-game ML bet tagged BULLPEN_DISADVANTAGE "
                        "(lib.edgelab.model_evaluation's own evidence-backed tag) -- a pure "
                        "subtraction, no substitute bet fabricated.",
        "excludedBetCount": len(all_bets) - len(no_bullpen_bets),
        **no_bullpen_stats,
        "deltaRoiVsBaseline": _delta_roi(no_bullpen_stats, baseline),
    })

    no_neg_clv_bets = [b for b in all_bets if not (b.get("clv") is not None and b["clv"] < 0)]
    no_neg_clv_stats = _bet_roi_stats(no_neg_clv_bets)
    experiments.append({
        "name": "REMOVE_NEGATIVE_CLV_MARKETS",
        "description": "Excludes every settled bet with a recorded negative CLV -- a pure "
                        "subtraction, no substitute bet fabricated.",
        "excludedBetCount": len(all_bets) - len(no_neg_clv_bets),
        **no_neg_clv_stats,
        "deltaRoiVsBaseline": _delta_roi(no_neg_clv_stats, baseline),
    })

    return {"simulationLabel": SIMULATION_LABEL, "baseline": baseline, "experiments": experiments}


# ── Edge Stability (item 7) ───────────────────────────────────────────────

def _classify_market_edge_stability(session):
    """
    Groups ALL ModelEvaluation snapshots (the FULL history -- unlike
    lib.edgelab.market_comparison.latest_evaluations_per_market, this
    needs every snapshot to see whether a market's edge changed) by
    market key, and for each market with a real first-snapshot edge,
    classifies it as STABLE / VOLATILE / FALSE_EDGE / UNKNOWN by checking
    whether the edge bucket held across:
      - time ("market movement" -- every snapshot's edge bucket)
      - lineup confirmation (an UNCONFIRMED/PROJECTED/PARTIAL snapshot's
        edge bucket vs a CONFIRMED snapshot's, when both exist)
      - settlement -- ONLY for a market with a REAL linked, settled
        PlacedBet (a real recorded WIN/LOSS via
        _settled_bet_result_by_eval_id(), never a Settlement.result
        YES/NO guessed against an unknown side; see that function's
        docstring for why an unbet market's edge can never be scored
        WIN/LOSS here)
    Returns a flat list of {"canonicalMarketFamily", "bucket", "classification"} --
    the shared raw material for both edge_stability()'s by-bucket report
    and market_health_scores()'s per-family stability component.
    """
    if not session.is_available("model_evaluations"):
        return []

    all_rows = _fetch_dicts(session, "SELECT * FROM v_model_evaluations ORDER BY createdAt ASC")
    by_market = collections.defaultdict(list)
    for row in all_rows:
        by_market[_market_key(row)].append(row)

    settled_result_by_eval_id = _settled_bet_result_by_eval_id(session)

    items = []
    for snapshots in by_market.values():
        first_edge = snapshots[0].get("estimatedEdge")
        bucket = edge_bucket(first_edge)
        if bucket is None:
            continue

        edges = [s.get("estimatedEdge") for s in snapshots if s.get("estimatedEdge") is not None]
        buckets_seen = {edge_bucket(e) for e in edges}
        movement_stable = len(buckets_seen) <= 1

        confirmed_edges = [s["estimatedEdge"] for s in snapshots if s.get("lineupConfirmationState") == "CONFIRMED" and s.get("estimatedEdge") is not None]
        unconfirmed_edges = [s["estimatedEdge"] for s in snapshots if s.get("lineupConfirmationState") in _LINEUP_UNCONFIRMED_STATES and s.get("estimatedEdge") is not None]
        lineup_checkpoint_known = bool(confirmed_edges) and bool(unconfirmed_edges)
        lineup_stable = (edge_bucket(confirmed_edges[-1]) == edge_bucket(unconfirmed_edges[0])) if lineup_checkpoint_known else None

        settled_result = None
        for s in snapshots:
            eval_id = s.get("modelEvaluationId")
            if eval_id in settled_result_by_eval_id:
                settled_result = settled_result_by_eval_id[eval_id]
                break
        settlement_known = settled_result is not None
        won = (settled_result == "WIN") if settlement_known else None

        checkpoints_known = (len(snapshots) > 1) + lineup_checkpoint_known + settlement_known
        if checkpoints_known < 1:
            classification = UNKNOWN_STABILITY
        elif not movement_stable or lineup_stable is False:
            classification = VOLATILE
        elif settlement_known and not won:
            classification = FALSE_EDGE
        else:
            classification = STABLE

        items.append({
            "canonicalMarketFamily": snapshots[-1].get("canonicalMarketFamily"),
            "bucket": bucket,
            "classification": classification,
        })
    return items


def edge_stability(session):
    """Per-edge-bucket STABLE/VOLATILE/FALSE_EDGE/UNKNOWN counts, gated by lib.edgelab.calibration.calibration_status()."""
    items = _classify_market_edge_stability(session)
    counts = collections.defaultdict(collections.Counter)
    for item in items:
        counts[item["bucket"]][item["classification"]] += 1

    results = []
    for bucket in sorted(counts):
        bucket_counts = counts[bucket]
        n = sum(bucket_counts.values())
        results.append({
            "edgeBucket": bucket,
            "n": n,
            "status": calibration_status(n),
            "stableCount": bucket_counts.get(STABLE, 0),
            "volatileCount": bucket_counts.get(VOLATILE, 0),
            "falseEdgeCount": bucket_counts.get(FALSE_EDGE, 0),
            "unknownCount": bucket_counts.get(UNKNOWN_STABILITY, 0),
        })
    return results


def _stability_ratio_by_family(items):
    by_family = collections.defaultdict(collections.Counter)
    for item in items:
        if item["classification"] != UNKNOWN_STABILITY and item.get("canonicalMarketFamily"):
            by_family[item["canonicalMarketFamily"]][item["classification"]] += 1
    ratios = {}
    for family, counter in by_family.items():
        total = sum(counter.values())
        ratios[family] = (counter.get(STABLE, 0) / total) if total else None
    return ratios


# ── Market Health Scores (item 8) ─────────────────────────────────────────

def market_health_scores(session, comparisons=None):
    """
    Per canonicalMarketFamily, a transparent weighted health score (same
    "visible named components, no black box" principle as
    lib.edgelab.market_comparison.comparison_score) combining:
      - sampleQuality: n / MIN_N_CALIBRATED, clamped to [0,1]
      - clvQuality: fraction of that family's settled bets with clv > 0
      - calibrationQuality: 1 - min(1, abs(calibrationError))
      - stability: STABLE / (STABLE+VOLATILE+FALSE_EDGE) among that
        family's classified edge-stability markets (see
        _classify_market_edge_stability())
      - recommendationQuality: bestExpressionFrequency from
        expression_performance_profiles()
    A missing component is excluded from the weighted average and the
    remaining HEALTH_WEIGHTS renormalized -- never imputed with a
    guessed neutral value.
    """
    if comparisons is None:
        comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []

    calibration_rows = {r["canonicalMarketFamily"]: r for r in market_family_calibration(session)}
    profiles = {p["canonicalMarketFamily"]: p for p in expression_performance_profiles(session, comparisons)}
    stability_ratio = _stability_ratio_by_family(_classify_market_edge_stability(session))

    clv_by_family = {}
    if session.is_available("bets"):
        for family, positive, total in session.fetchall("""
            SELECT canonicalMarketFamily,
                SUM(CASE WHEN clv > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN clv IS NOT NULL THEN 1 ELSE 0 END)
            FROM v_placed_bets WHERE status = 'settled' AND result IN ('WIN', 'LOSS')
            GROUP BY 1
        """):
            clv_by_family[family] = (positive / total) if total else None

    families = {f for f in (set(calibration_rows) | set(profiles) | set(stability_ratio) | set(clv_by_family)) if f}
    results = []
    for family in sorted(families):
        cal = calibration_rows.get(family)
        n = cal["n"] if cal else 0
        sample_quality = min(1.0, n / MIN_N_CALIBRATED) if n else 0.0
        calibration_quality = None
        if cal and cal.get("calibrationError") is not None:
            calibration_quality = 1.0 - min(1.0, abs(cal["calibrationError"]))

        components = {
            "sampleQuality": sample_quality,
            "clvQuality": clv_by_family.get(family),
            "calibrationQuality": calibration_quality,
            "stability": stability_ratio.get(family),
            "recommendationQuality": profiles.get(family, {}).get("bestExpressionFrequency") if family in profiles else None,
        }
        available = {k: v for k, v in components.items() if v is not None}
        health_score = None
        if available:
            weight_sum = sum(HEALTH_WEIGHTS[k] for k in available)
            health_score = sum(HEALTH_WEIGHTS[k] * v for k, v in available.items()) / weight_sum

        results.append({
            "canonicalMarketFamily": family,
            "healthScore": health_score,
            "components": components,
            "sampleSize": n,
            "sampleStatus": calibration_status(n),
        })
    return results
