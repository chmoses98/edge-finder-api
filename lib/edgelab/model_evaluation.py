"""
lib/edgelab/model_evaluation.py
====================================
EdgeLab Phase 2 Milestone 3 (docs/EDGELAB_MODEL_EVALUATION.md): the
first-class, durable ModelEvaluation ledger -- closing the write-path gap
Milestone 2 surfaced (no settled bet carries modelFairProbability because
nothing durably records what the model actually evaluated, independent of
whether a bet was ever placed on it).

Reads the exact same source artifact lib.edgelab.recommendations already
reads (data/pipeline/<date>/recommendations.json's data.games[].marketLedger
rows) -- this module does NOT recompute any model math. It only persists,
for every row the model's pipeline already produced, whatever fair
probability/edge/confidence that row already carries, plus an explicit
evaluationStatus classifying why a probability is or isn't trustworthy.
"Do not fabricate unavailable values" (Milestone 3 scope item 3): every
field below is either copied verbatim from the source row, looked up from
an already-captured MarketObservation, or left null.

Shares Recommendation's exact ID-and-versioning scheme (Phase 1 section G):
pipeline-derived rows are keyed by the source artifact's own
meta.createdAt (not this script's run timestamp), so re-ingesting an
already-finalized recommendations.json is a pure no-op; full-universe rows
are keyed by (date, marketTicker) and upserted, since there's no decision
content to version for a market the model never touches at all.
"""

import os

from lib.edgelab import ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.pipeline_artifacts import read_stage_artifact, stage_artifact_exists
from scripts.clv_from_snapshot import implied_to_american

# Evaluation-status values this module can assign -- see
# data/edgelab/schema_v1/model_evaluation.schema.json for the meaning of
# each. Ordered here roughly by "how much of a real evaluation exists",
# most complete first, purely for readability.
EVALUATED = "EVALUATED"
PARTIAL_EVALUATION = "PARTIAL_EVALUATION"
NO_MODEL_SUPPORT = "NO_MODEL_SUPPORT"
INVALID_PROBABILITY = "INVALID_PROBABILITY"
MISSING_MARKET_PRICE = "MISSING_MARKET_PRICE"
DATA_QUALITY_BLOCK = "DATA_QUALITY_BLOCK"
PARSER_UNRESOLVED = "PARSER_UNRESOLVED"

# Which upstream script produced the marketLedger rows this module reads
# -- taken verbatim from the artifact's own meta.producedBy (never
# invented), used as ModelEvaluation.modelSource for pipeline-derived rows.
_FALLBACK_MODEL_SOURCE = "scripts/build_market_ledger.py"


def _market_implied_probability(row):
    return row.get("kalshiVF") or row.get("marketProbVF") or row.get("executableMarketProb")


def _estimated_edge(row):
    edge = row.get("calibratedEdgeVsExecutable")
    if edge is None:
        edge = row.get("edge")
    return edge


def classify_evaluation_status(row):
    """
    Pure function of one marketLedger row -> one of the 7 evaluationStatus
    values. Deliberately independent of the row's own `status` field
    (Missing Data/Rejected/Accepted) wherever the model's own probability
    already answers the question directly: a "Rejected" row (the model
    DID produce a fair probability; a later edge-threshold/portfolio rule
    declined to bet it) is just as fully EVALUATED as an "Accepted" one --
    rejection is a Recommendation-level decision, not evidence the model
    itself failed to evaluate the market. `row.get("status")` is only
    consulted as a last resort, when the row carries no modelProb at all,
    to distinguish why.
    """
    model_prob = row.get("modelProb")
    if model_prob is not None:
        if not (0 < model_prob < 100):
            return INVALID_PROBABILITY
        ticker = row.get("ticker") or row.get("marketTicker")
        if not ticker:
            return PARSER_UNRESOLVED
        if _market_implied_probability(row) is None:
            return MISSING_MARKET_PRICE
        if _estimated_edge(row) is None:
            return PARTIAL_EVALUATION
        return EVALUATED

    row_status = row.get("status")
    if row_status == "Evaluation Failed":
        err = (row.get("evaluationError") or "").lower()
        if "pars" in err or "ticker" in err:
            return PARSER_UNRESOLVED
        return DATA_QUALITY_BLOCK
    if row_status == "Missing Data":
        missing = " ".join(row.get("missingFields") or []).lower()
        if "kalshi" in missing or "odds" in missing or "price" in missing:
            return MISSING_MARKET_PRICE
        return DATA_QUALITY_BLOCK
    return NO_MODEL_SUPPORT


def _lineup_confirmation_state(row):
    """
    Maps the marketLedger row's own lineup fields to MarketObservation's
    existing CONFIRMED/PROJECTED/UNKNOWN/null vocabulary (never a new,
    fourth vocabulary) -- null (not "UNKNOWN") when the row carries no
    lineup assessment at all, since "genuinely not assessed" and
    "assessed as unknown" are different claims and this module never
    fabricates the stronger one.
    """
    if row.get("lineupConfirmedOfficial") is True:
        return "CONFIRMED"
    status = (row.get("lineupStatus") or "").strip().lower()
    if status == "confirmed":
        return "CONFIRMED"
    if status:
        return "PROJECTED"
    return None


def _model_fair_odds(model_fair_probability):
    if model_fair_probability is None:
        return None
    return implied_to_american(model_fair_probability / 100.0)


def _ticker_lookup_from_observations(observations):
    """{marketTicker: (eventTicker, seriesTicker)} built from already-captured
    MarketObservation rows for this date -- reused, never re-parsed."""
    lookup = {}
    for obs in observations:
        ticker = obs.get("marketTicker")
        if ticker and ticker not in lookup:
            lookup[ticker] = (obs.get("eventTicker"), obs.get("seriesTicker"))
    return lookup


def build_model_evaluations_from_pipeline(date, run_id, observations):
    """
    One ModelEvaluation per data/pipeline/<date>/recommendations.json
    marketLedger row -- every row, not just Accepted/Rejected ones, so a
    market the model looked at but couldn't evaluate (Missing Data,
    Evaluation Failed) is still durably recorded with its reason. Returns
    (records, warnings); empty records + a warning if the artifact
    doesn't exist yet, mirroring
    lib.edgelab.recommendations.build_recommendations_from_pipeline
    exactly (same source file, same non-fabrication contract).

    Returns records keyed by the same (source_run_key, market_key)
    scheme lib.edgelab.recommendations uses for recommendationId, so
    lib.edgelab.recommendations can look up "the ModelEvaluation for
    this exact row" by recomputing the identical key -- see
    build_recommendation_and_evaluation_ids().
    """
    if not stage_artifact_exists("recommendations", date):
        return [], [f"no data/pipeline/{date}/recommendations.json artifact"]

    rec_env = read_stage_artifact("recommendations", date)
    source_run_key = rec_env["meta"]["createdAt"]
    model_source = rec_env["meta"].get("producedBy") or _FALLBACK_MODEL_SOURCE
    games = (rec_env.get("data") or {}).get("games") or []
    ticker_lookup = _ticker_lookup_from_observations(observations)

    now = ids.utc_now_iso()
    source_file = os.path.join("data", "pipeline", date, "recommendations.json")
    records = []

    for g in games:
        game_id = g.get("gameId")
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")

        for row in g.get("marketLedger") or []:
            market_name = row.get("market")
            ticker = row.get("ticker") or row.get("marketTicker")
            # market_name is always part of the key -- see the identical
            # comment in lib.edgelab.recommendations.build_recommendations_from_pipeline;
            # both modules must derive the same market_key for the same
            # row so their IDs cross-link correctly.
            market_key = f"{ticker}:{market_name}" if ticker else f"{game_id}:{market_name}"
            evaluation_status = classify_evaluation_status(row)
            model_fair_probability = row.get("modelProb") if evaluation_status in (EVALUATED, PARTIAL_EVALUATION) else None
            observed_event_ticker, observed_series_ticker = ticker_lookup.get(ticker, (None, None))

            records.append({
                "schemaVersion": SCHEMA_VERSION,
                "modelEvaluationId": ids.build_model_evaluation_id(source_run_key, market_key),
                "runId": run_id,
                "gameId": game_id,
                "sport": DEFAULT_SPORT,
                "platform": DEFAULT_PLATFORM,
                "marketTicker": ticker or market_key,
                "eventTicker": observed_event_ticker,
                "seriesTicker": observed_series_ticker or row.get("seriesTicker"),
                "marketFamily": ticker.split("-", 1)[0] if ticker else None,
                "selection": market_name,
                "side": None,
                "threshold": row.get("line"),
                "evaluationStatus": evaluation_status,
                "modelFairProbability": model_fair_probability,
                "modelFairOdds": _model_fair_odds(model_fair_probability),
                "modelVersion": None,
                "modelSource": model_source,
                "calibrationVersion": None,
                "marketImpliedProbability": _market_implied_probability(row) if evaluation_status in (EVALUATED, PARTIAL_EVALUATION) else None,
                "estimatedEdge": _estimated_edge(row) if evaluation_status == EVALUATED else None,
                "evPerDollar": None,
                "confidence": row.get("confidenceTier") or row.get("confidence"),
                "lineupConfirmationState": _lineup_confirmation_state(row),
                "dataQuality": row.get("lineupDataQuality"),
                "thesisTags": [],
                "correlationGroup": None,
                "recommendationId": ids.build_recommendation_id(source_run_key, market_key),
                "createdAt": now,
                "source": "pipeline_recommendations",
                "validationStatus": "valid",
                "provenance": {
                    "sourceSystem": "pipeline_recommendations",
                    "sourceFile": source_file,
                    "sourceKey": f"{away}@{home}|{market_name}",
                    "capturedAt": source_run_key,
                    "ingestedAt": now,
                },
            })

    return records, []


def extend_full_universe_evaluations(covered_tickers, observations, date):
    """
    One NO_MODEL_SUPPORT ModelEvaluation per observed marketTicker NOT
    already covered by a pipeline-derived evaluation -- the model's
    11-market config never even attempts these, so there is no
    modelProb/evaluationError to classify against; NO_MODEL_SUPPORT is
    the only honest answer. Mirrors
    lib.edgelab.recommendations.extend_with_full_universe's own
    (date, marketTicker)-keyed upsert scheme (one current row per market
    per day, not versioned per run -- there's no decision content to
    version for a market the model never touches).
    """
    now = ids.utc_now_iso()
    seen = set(covered_tickers)
    extra = []
    for obs in observations:
        ticker = obs["marketTicker"]
        if ticker in seen:
            continue
        seen.add(ticker)
        extra.append({
            "schemaVersion": SCHEMA_VERSION,
            "modelEvaluationId": ids.build_model_evaluation_id(date, ticker),
            "runId": obs["runId"],
            "gameId": obs.get("gameId"),
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "marketTicker": ticker,
            "eventTicker": obs.get("eventTicker"),
            "seriesTicker": obs.get("seriesTicker"),
            "marketFamily": obs.get("marketFamily"),
            "selection": None,
            "side": None,
            "threshold": None,
            "evaluationStatus": NO_MODEL_SUPPORT,
            "modelFairProbability": None,
            "modelFairOdds": None,
            "modelVersion": None,
            "modelSource": None,
            "calibrationVersion": None,
            "marketImpliedProbability": None,
            "estimatedEdge": None,
            "evPerDollar": None,
            "confidence": None,
            "lineupConfirmationState": None,
            "dataQuality": None,
            "thesisTags": [],
            "correlationGroup": None,
            "recommendationId": ids.build_recommendation_id(date, ticker),
            "createdAt": now,
            "source": "market_universe_extension",
            "validationStatus": "valid",
            "provenance": dict(obs["provenance"], ingestedAt=now),
        })
    return extra


# ── Data-population report (Milestone 3 scope item 11) ──────────────────
#
# Read-only queries over lib.edgelab.analytics's v_model_evaluations
# (and, where a real link exists, v_placed_bets/v_settlements) -- never
# per-row Python materialization, same convention as
# lib.edgelab.calibration.

def _pct(session, total, where_sql):
    n = session.fetchall(f"SELECT COUNT(*) FROM v_model_evaluations WHERE {where_sql}")[0][0]
    return {"count": n, "pct": round(100.0 * n / total, 2) if total else None}


def population_report(session):
    """
    Overall coverage of every ModelEvaluation ever persisted: how many
    carry a real modelFairProbability/estimatedEdge/confidence/thesisTags,
    and how many are actually LINKED (by ID, not just ticker
    co-occurrence) to a Recommendation, PlacedBet, or Settlement. Returns
    None (not a fabricated all-zero report) when the entity has no files
    at all yet.
    """
    if not session.is_available("model_evaluations"):
        return None

    total = session.fetchall("SELECT COUNT(*) FROM v_model_evaluations")[0][0]
    result = {
        "total": total,
        "modelFairProbability": _pct(session, total, "modelFairProbability IS NOT NULL"),
        "estimatedEdge": _pct(session, total, "estimatedEdge IS NOT NULL"),
        "confidence": _pct(session, total, "confidence IS NOT NULL"),
        "thesisTags": _pct(session, total, "thesisTags IS NOT NULL AND len(thesisTags) > 0"),
        "linkedToRecommendation": _pct(session, total, "recommendationId IS NOT NULL"),
    }

    if session.is_available("bets"):
        result["linkedToPlacedBet"] = _pct(
            session, total,
            "modelEvaluationId IN (SELECT modelEvaluationId FROM v_placed_bets WHERE modelEvaluationId IS NOT NULL)",
        )
    else:
        result["linkedToPlacedBet"] = None

    if session.is_available("settlements"):
        # Settlement carries no modelEvaluationId field (deliberately --
        # see docs/EDGELAB_MODEL_EVALUATION.md's linkage-rules section on
        # why this link is query-time-by-ticker, not a new stored FK on
        # an entity Milestone 1 established should never be rewritten).
        result["linkedToSettlement"] = _pct(
            session, total,
            "marketTicker IN (SELECT marketTicker FROM v_settlements)",
        )
    else:
        result["linkedToSettlement"] = None

    return result


def population_by_canonical_family(session):
    """Same four coverage percentages as population_report(), broken out per canonical market family."""
    if not session.is_available("model_evaluations"):
        return []
    rows = session.fetchall("""
        SELECT
            canonicalMarketFamily,
            COUNT(*) AS n,
            SUM(CASE WHEN modelFairProbability IS NOT NULL THEN 1 ELSE 0 END) AS withProb,
            SUM(CASE WHEN estimatedEdge IS NOT NULL THEN 1 ELSE 0 END) AS withEdge,
            SUM(CASE WHEN confidence IS NOT NULL THEN 1 ELSE 0 END) AS withConfidence,
            SUM(CASE WHEN thesisTags IS NOT NULL AND len(thesisTags) > 0 THEN 1 ELSE 0 END) AS withTags
        FROM v_model_evaluations
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [
        {
            "canonicalMarketFamily": family, "n": n,
            "pctModelFairProbability": round(100.0 * with_prob / n, 2) if n else None,
            "pctEstimatedEdge": round(100.0 * with_edge / n, 2) if n else None,
            "pctConfidence": round(100.0 * with_conf / n, 2) if n else None,
            "pctThesisTags": round(100.0 * with_tags / n, 2) if n else None,
        }
        for family, n, with_prob, with_edge, with_conf, with_tags in rows
    ]


def population_by_model_version_and_source(session):
    """
    One row per (modelVersion, modelSource) pair actually observed --
    surfaces the honest current gap that modelVersion is null for every
    real pipeline-derived evaluation (docs/EDGELAB_MODEL_EVALUATION.md),
    rather than hiding it behind a single aggregate.
    """
    if not session.is_available("model_evaluations"):
        return []
    rows = session.fetchall("""
        SELECT COALESCE(modelVersion, 'UNKNOWN') AS modelVersion, COALESCE(modelSource, 'UNKNOWN') AS modelSource, COUNT(*) AS n
        FROM v_model_evaluations
        GROUP BY 1, 2
        ORDER BY n DESC, modelVersion, modelSource
    """)
    return [{"modelVersion": r[0], "modelSource": r[1], "n": r[2]} for r in rows]
