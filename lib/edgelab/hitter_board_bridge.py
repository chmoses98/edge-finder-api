"""
lib/edgelab/hitter_board_bridge.py
=======================================
Hitter Prop Methodology Repair mission: pure read bridge between
scripts/build_hitter_projection_board.py's already-computed per-contract
hitter-prop probabilities (data/pipeline/<date>/hitter_projection_board.json)
and lib.edgelab.model_evaluation.extend_full_universe_evaluations() --
the exact same integration pattern lib.edgelab.kalshi_discovery_bridge
already established for the non-hitter families PR #103 wired in.

Only hitter_hits/hitter_total_bases/hitter_rbis/hitter_hits_runs_rbis
are read here (the four families this repository's own market_taxonomy
confirms are real Kalshi series AND that this mission's methodology
audit found (and fixed) a legitimate, platoon/pitcher-quality-aware,
real-teammate-rate-aware projection method for -- see
lib.research.hitter_board_builder.SUPPORTED_REAL_FAMILIES).
hitter_stolen_bases is a confirmed real series but has NO real signal
anywhere in this codebase (no attempt-rate or catcher CS% ingestion --
confirmed by this mission's own audit) and stays completely untouched:
it never appears in hitter_projection_board.json's PROJECTED rows in
the first place, so it is structurally impossible for this bridge to
fabricate a probability for it.

Returns a lookup in the EXACT SAME shape
lib.edgelab.kalshi_discovery_bridge.load_discovery_lookup() already
produces ({marketTicker: {"modelSupportStatus", "fairProbabilityPct",
"impliedProbabilityPct", "marketTitle", "line", "marketFamily",
"unsupportedReason", "rawEdgePct", "expectedProfitPerDollar"}}), so
lib.edgelab.model_evaluation._discovery_extension_fields() (already
written, already tested) needs no changes at all to consume it --
callers simply merge this lookup into the same discovery_lookup dict
passed to extend_full_universe_evaluations().
"""
from lib.pipeline_artifacts import read_stage_artifact, stage_artifact_exists

STAGE = "hitter_projection_board"

STATUS_PROJECTED = "PROJECTED"
# Every real archived hitter contract this run attempted to project but
# couldn't, for a context-dependent reason (lineup not yet confirmed,
# game already started, ...) -- the projection METHOD exists, this
# specific contract/moment's inputs were insufficient. Maps to
# MISSING_DATA, matching lib.edgelab.model_evaluation's own
# DATA_QUALITY_BLOCK evaluationStatus for that discovery status.
_MISSING_DATA_STATUSES = frozenset({
    "LINEUP_UNCONFIRMED", "GAME_STARTED", "PLAYER_NOT_IN_STARTING_LINEUP",
    "PLAYER_ID_UNRESOLVED", "AMBIGUOUS_TICKER_MATCH", "MISSING_REQUIRED_CONTEXT", "MODEL_ERROR",
})
# The contract's own ticker/title never classified into a family+threshold this engine supports at all.
_UNSUPPORTED_STATUSES = frozenset({"MARKET_SEMANTICS_UNSUPPORTED"})


def load_hitter_board_lookup(date):
    """
    Returns {marketTicker: contract_dict} for every row in
    data/pipeline/<date>/hitter_projection_board.json, or {} if that
    artifact doesn't exist for this date (never raises -- exactly
    mirrors kalshi_discovery_bridge.load_discovery_lookup's degrade-to-
    empty contract for a date this board hasn't been built for).
    """
    if not stage_artifact_exists(STAGE, date):
        return {}
    try:
        envelope = read_stage_artifact(STAGE, date)
    except (OSError, ValueError):
        return {}
    rows = ((envelope.get("data") or {}).get("rows")) or []

    lookup = {}
    for row in rows:
        ticker = row.get("marketTicker")
        if not ticker:
            continue
        status = row.get("projectionStatus")
        model_prob = row.get("modelProbability")

        if status == STATUS_PROJECTED and model_prob is not None:
            model_support_status = "SUPPORTED"
            fair_prob_pct = round(model_prob * 100, 3)
            executable_price = row.get("executableKalshiPrice")
            implied_pct = round(executable_price * 100, 3) if executable_price is not None else None
            raw_edge = row.get("rawProbabilityEdge")
            raw_edge_pct = round(raw_edge * 100, 3) if raw_edge is not None else None
            unsupported_reason = None
        elif status in _UNSUPPORTED_STATUSES:
            model_support_status = "UNSUPPORTED"
            fair_prob_pct = None
            implied_pct = None
            raw_edge_pct = None
            unsupported_reason = row.get("projectionStatusReason")
        else:
            # Covers _MISSING_DATA_STATUSES and any future/unrecognized
            # status defensively -- never SUPPORTED without a real
            # modelProbability, never a fabricated probability.
            model_support_status = "MISSING_DATA"
            fair_prob_pct = None
            implied_pct = None
            raw_edge_pct = None
            unsupported_reason = row.get("projectionStatusReason") or f"hitter board projectionStatus={status!r}"

        lookup[ticker] = {
            "ticker": ticker,
            "marketFamily": row.get("marketFamily"),
            "marketTitle": row.get("naturalLanguageMarket"),
            "line": row.get("threshold"),
            "modelSupportStatus": model_support_status,
            "fairProbabilityPct": fair_prob_pct,
            "impliedProbabilityPct": implied_pct,
            "unsupportedReason": unsupported_reason,
            "rawEdgePct": raw_edge_pct,
            "expectedProfitPerDollar": row.get("expectedValuePerDollar"),
            "modelSource": "lib.research.hitter_board_builder.build_hitter_projection_rows",
        }
    return lookup
