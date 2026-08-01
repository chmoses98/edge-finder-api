"""
lib/edgelab/recommendations.py
=================================
The decision-layer ledger (Phase 1 section G). Ingests from two
pre-existing pipeline artifacts rather than re-implementing decision
logic:
  - data/pipeline/<date>/recommendations.json ("data.games[].marketLedger[]",
    the 11-market model config's per-game decision rows -- see
    docs/CANONICAL_SCHEMAS.md's Recommendation object).
  - data/pipeline/<date>/execution.json (risk_gate.py's post-portfolio-rules
    decision, joined by (game, market)).

Then extends coverage to every OTHER market EdgeLab observed that day
(the ~6 of 17 strict-registry families the 11-market config never
evaluates at all, plus specific tickers within a covered family the
model didn't happen to pick) -- explicitly, never silently dropped.

Two different update cadences, deliberately:
  - Pipeline-derived rows use the SOURCE ARTIFACT's own meta.createdAt
    (not this script's own run timestamp) as part of recommendationId,
    so re-running ingestion against the same already-finalized
    recommendations.json is a pure no-op (idempotent rerun), while an
    actual fetch-slate rerun that produces a new artifact naturally
    creates new decision rows -- a real, intended history of how the
    decision changed through the day.
  - Full-universe extension rows are keyed by (date, marketTicker), not
    by run, and UPSERTED rather than appended -- there is no decision
    content to version for a market the model never touches; one
    current row per market per day is enough, refreshed as often as
    ingestion runs.
"""

import json
import os

from lib.edgelab import ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.pipeline_artifacts import read_stage_artifact, stage_artifact_exists

RULES_PATH = os.path.join("config", "rules.json")


def load_model_covered_series(rules_path=RULES_PATH):
    """Series tickers the 11-market model config can evaluate at all (config/rules.json's market_list)."""
    if not os.path.exists(rules_path):
        return frozenset()
    with open(rules_path) as f:
        rules = json.load(f)
    return frozenset(m["series"] for m in rules.get("market_list", []) if m.get("series"))


def _map_rejection_reason(reason):
    r = (reason or "").lower()
    if "liquidit" in r:
        return "PASS_LOW_LIQUIDITY"
    if "correlat" in r:
        return "PASS_CORRELATION"
    if "dominat" in r:
        return "PASS_DOMINATED_MARKET"
    if "price" in r or "beyond max" in r:
        return "PASS_PRICE_TOO_HIGH"
    if "lineup" in r or "data" in r or "stale" in r or "missing" in r or "ticker" in r:
        return "PASS_DATA_QUALITY"
    return "PASS_NO_EDGE"


def _classify_ledger_row(row, game_status, has_bet):
    """Returns (status, passReason)."""
    row_status = row.get("status")
    if row_status == "Missing Data":
        return "PASS_DATA_QUALITY", "; ".join(row.get("missingFields") or []) or "Missing data"
    if row_status == "Evaluation Failed":
        return "PASS_DATA_QUALITY", row.get("evaluationError") or "Evaluation failed"
    if row_status == "Rejected":
        reason = row.get("rejectionReason") or ""
        return _map_rejection_reason(reason), reason or None
    if row_status == "Accepted":
        if has_bet:
            return "BET_PLACED", None
        if game_status and "final" in game_status.lower():
            return "RECOMMENDED_NOT_BET", None
        return "RECOMMENDED", None
    return "NOT_EVALUATED", None


def build_recommendations_from_pipeline(date, run_id, placed_bet_tickers):
    """
    placed_bet_tickers: {marketTicker: betId} for every currently-tracked
    placed bet -- a dict, not a bare set, so a matched row can link
    Recommendation.betId back to the actual bet (previously this was
    always left null even when betPlaced was true).

    Returns (records, warnings). Empty records + a warning if
    recommendations.json doesn't exist for this date (e.g. the slate
    pipeline hasn't run yet) -- never fabricated.
    """
    if not stage_artifact_exists("recommendations", date):
        return [], [f"no data/pipeline/{date}/recommendations.json artifact"]

    rec_env = read_stage_artifact("recommendations", date)
    source_run_key = rec_env["meta"]["createdAt"]
    games = (rec_env.get("data") or {}).get("games") or []

    now = ids.utc_now_iso()
    source_file = os.path.join("data", "pipeline", date, "recommendations.json")
    records = []

    for g in games:
        game_id = g.get("gameId")
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")
        game_status = g.get("status")

        for row in g.get("marketLedger") or []:
            market_name = row.get("market")
            ticker = row.get("ticker") or row.get("marketTicker")
            bet_id = placed_bet_tickers.get(ticker) if ticker else None
            has_bet = bet_id is not None
            status, pass_reason = _classify_ledger_row(row, game_status, has_bet)
            market_key = ticker or f"{game_id}:{market_name}"

            records.append({
                "schemaVersion": SCHEMA_VERSION,
                "recommendationId": ids.build_recommendation_id(source_run_key, market_key),
                "runId": run_id,
                "gameId": game_id,
                "sport": DEFAULT_SPORT,
                "platform": DEFAULT_PLATFORM,
                "marketTicker": ticker,
                "marketName": market_name,
                "marketFamily": ticker.split("-", 1)[0] if ticker else None,
                "status": status,
                "modelEvaluationId": None,
                "modelFairProbability": row.get("modelProb"),
                "marketImpliedProbability": row.get("kalshiVF") or row.get("marketProbVF"),
                "estimatedEdge": row.get("calibratedEdgeVsExecutable") or row.get("edge"),
                "evPerDollar": None,
                "rankWithinGame": None,
                "priceCeiling": row.get("maxBetPrice"),
                "confidence": row.get("confidenceTier"),
                "passReason": pass_reason,
                "comparisonMarkets": [],
                "betPlaced": has_bet,
                "betId": bet_id,
                "createdAt": now,
                "updatedAt": now,
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


def extend_with_full_universe(covered_tickers, observations, model_covered_series, date, placed_bet_tickers=None):
    """
    One additional row per observed marketTicker NOT already covered by
    a pipeline-derived recommendation: NOT_EVALUATED if its series IS one
    the model config supports in general (just not this exact
    ticker/threshold), INSUFFICIENT_MODEL_SUPPORT if the model has no
    method for the family at all.

    placed_bet_tickers ({marketTicker: betId}, optional) overrides that
    default status to BET_PLACED for a ticker the model never evaluated
    at all -- this is exactly the "bet placed without a model
    recommendation" case section G asks to keep researchable; without
    this check every such bet would be misreported as NOT_EVALUATED/
    INSUFFICIENT_MODEL_SUPPORT despite money actually being on it.
    modelFairProbability stays null in this case (the model still never
    produced one) so a later query can distinguish
    "status=BET_PLACED AND modelFairProbability IS NULL" from a
    model-driven bet.
    """
    placed_bet_tickers = placed_bet_tickers or {}
    now = ids.utc_now_iso()
    seen = set(covered_tickers)
    extra = []
    for obs in observations:
        ticker = obs["marketTicker"]
        if ticker in seen:
            continue
        seen.add(ticker)
        bet_id = placed_bet_tickers.get(ticker)
        if bet_id is not None:
            status = "BET_PLACED"
        else:
            status = "NOT_EVALUATED" if obs["seriesTicker"] in model_covered_series else "INSUFFICIENT_MODEL_SUPPORT"
        extra.append({
            "schemaVersion": SCHEMA_VERSION,
            "recommendationId": ids.build_recommendation_id(date, ticker),
            "runId": obs["runId"],
            "gameId": obs.get("gameId"),
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "marketTicker": ticker,
            "marketName": None,
            "marketFamily": obs.get("marketFamily"),
            "status": status,
            "modelEvaluationId": None,
            "modelFairProbability": None,
            "marketImpliedProbability": None,
            "estimatedEdge": None,
            "evPerDollar": None,
            "rankWithinGame": None,
            "priceCeiling": None,
            "confidence": None,
            "passReason": None,
            "comparisonMarkets": [],
            "betPlaced": bet_id is not None,
            "betId": bet_id,
            "createdAt": now,
            "updatedAt": now,
            "source": "market_universe_extension",
            "validationStatus": "valid",
            "provenance": dict(obs["provenance"], ingestedAt=now),
        })
    return extra
