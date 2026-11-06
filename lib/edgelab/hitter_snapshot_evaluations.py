"""
lib/edgelab/hitter_snapshot_evaluations.py
==========================================
Pure builder: prospective hitter projection snapshots -> ModelEvaluation-shaped records in their OWN entity
(model_evaluations_hitter_snapshot). The canonical model_evaluations partitions are never read for writing or
changed; the existing NO_MODEL_SUPPORT rows stay exactly as archived.

Each record is a prediction that really existed before the event:
  * pipelineRunId = the snapshot's own projectionGeneratedAt (UTC ISO) -- the causal "when the model decided" stamp
    lib.edgelab.temporal_alignment requires, so the existing no-look-ahead join (ticker + pipelineRunId <= capturedAt)
    applies unchanged;
  * emitted only for PROJECTED snapshots with a numeric probability in [0, 1], a tz-aware generation instant, and
    generated at or before the first pitch encoded in the exact ticker (a ticker whose start cannot be parsed is
    REJECTED, not guessed);
  * modelFairProbability is stored in percent (the convention of every other ModelEvaluation row);
  * nothing is derived from outcomes or any later information.

Idempotent: the id is a function of the snapshot id only and records carry no wall-clock fields.
"""
import hashlib

from lib.edgelab.hitter_snapshot_join import STATUS_PROJECTED, parse_utc, ticker_event_start_utc

ENTITY = "model_evaluations_hitter_snapshot"
SOURCE = "hitter_projection_snapshot"
MODEL_SOURCE = "lib.research.hitter_board_builder.build_hitter_projection_rows"

REJECT_NOT_PROJECTED = "SNAPSHOT_STATUS_NOT_PROJECTED"
REJECT_BAD_PROBABILITY = "SNAPSHOT_PROBABILITY_MISSING_OR_INVALID"
REJECT_BAD_TIME = "SNAPSHOT_TIMESTAMP_INVALID"
REJECT_NO_TICKER = "SNAPSHOT_TICKER_MISSING"
REJECT_NO_ID = "SNAPSHOT_ID_MISSING"
REJECT_START_UNPARSEABLE = "TICKER_START_UNPARSEABLE"
REJECT_AFTER_START = "SNAPSHOT_GENERATED_AFTER_GAME_START"


def _unit(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def evaluation_id(snapshot_id):
    return hashlib.sha1(f"{ENTITY}/v1:{snapshot_id}".encode()).hexdigest()


def record_from_snapshot(snap):
    """Returns (record | None, rejectReason | None)."""
    ticker = snap.get("marketTicker")
    if not isinstance(ticker, str) or not ticker:
        return None, REJECT_NO_TICKER
    sid = snap.get("hitterProjectionSnapshotId")
    if not isinstance(sid, str) or not sid:
        return None, REJECT_NO_ID
    if snap.get("projectionStatus") != STATUS_PROJECTED:
        return None, REJECT_NOT_PROJECTED
    prob = snap.get("modelProbability")
    if not _unit(prob):
        return None, REJECT_BAD_PROBABILITY
    generated = parse_utc(snap.get("projectionGeneratedAt"))
    if generated is None:
        return None, REJECT_BAD_TIME
    start = ticker_event_start_utc(ticker)
    if start is None:
        return None, REJECT_START_UNPARSEABLE
    if generated > start:
        return None, REJECT_AFTER_START
    price = snap.get("executableKalshiPrice")
    implied_pct = round(price * 100, 3) if _unit(price) else None
    raw_edge = snap.get("rawProbabilityEdge")
    return {
        "schemaVersion": "1",
        "modelEvaluationId": evaluation_id(sid),
        "runId": snap.get("researchRunId"),
        "pipelineRunId": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "marketTicker": ticker,
        "gameId": snap.get("gameId"),
        "marketFamily": snap.get("marketFamily"),
        "threshold": snap.get("threshold"),
        "selection": snap.get("naturalLanguageMarket"),
        "side": None,
        "evaluationStatus": "EVALUATED" if implied_pct is not None else "PARTIAL_EVALUATION",
        "qualityTier": "RESEARCH_ONLY",
        "modelFairProbability": round(float(prob) * 100, 3),
        "marketImpliedProbability": implied_pct,
        "estimatedEdge": round(raw_edge * 100, 3) if _num(raw_edge) else None,
        "modelSource": MODEL_SOURCE,
        "modelCommitSha": snap.get("engineCommitSha"),
        "checkpoint": snap.get("checkpoint"),
        "source": SOURCE,
        "sourceSnapshotId": sid,
        "sourceMarketObservedAt": snap.get("marketObservedAt"),
        "gameStartUtc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "partitionDate": generated.strftime("%Y-%m-%d"),
        "validationStatus": "valid",
        "provenance": {"sourceSystem": SOURCE, "sourceKey": sid, "sourceFile": "data/edgelab/hitter_projection_snapshots"},
    }, None


def build_records(snapshots):
    """Returns (records sorted by id, rejectCounts). Duplicate snapshot ids collapse to one record."""
    records, rejects = {}, {}
    for snap in snapshots:
        rec, reason = record_from_snapshot(snap)
        if rec is None:
            rejects[reason] = rejects.get(reason, 0) + 1
        else:
            records[rec["modelEvaluationId"]] = rec
    return [records[k] for k in sorted(records)], rejects
