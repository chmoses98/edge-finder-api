"""
lib/edgelab/scored_replay.py
================================
Scored Postgame Replay milestone: scores the immutable, already-written
output of lib.edgelab.replay (a completed ReplayRun's ReplayResults)
against canonical postgame evidence -- settlement, CLV, and placed-bet
records -- once outcomes become available, so future model changes can
be evaluated objectively against what the model actually said pregame.

WHAT THIS DOES NOT DO
------------------------
- Never modifies or overwrites a ReplayRun/ReplayResult
  (data/edgelab/replay_runs/<id>/*). Every function here only ever
  READS them via lib.edgelab.replay.load_replay_run/load_replay_results.
  Output is written to a physically separate tree,
  data/edgelab/scored_replay_runs/<scoredReplayRunId>/, so there is no
  file this module could accidentally collide with or mutate.
- Never recomputes a probability, threshold, tier, recommendation, or
  wager. Every scored field is either copied verbatim from the
  ReplayResult's own `original*` fields (the actual pregame/forward
  values production wrote before any outcome existed) or looked up from
  an already-written canonical record (settlement, CLV quote,
  ModelEvaluation, Recommendation, PlacedBet). A record that does not
  exist for a market stays null -- never fabricated, never inferred from
  a related-but-different market or estimated from a formula.
- Never uses `replayed*` fields (the CANDIDATE_MODEL re-run of CURRENT
  code) -- those exist for regression testing against historical inputs,
  not for scoring what was actually predicted/recommended at decision
  time. This milestone is about the ORIGINAL, immutable pregame output.

WHY THIS IS IDEMPOTENT BUT NOT WRITE-ONCE
--------------------------------------------
A ReplayRun's own identity is deterministic over inputs that never
change once captured (a frozen Snapshot). A ScoredReplayRun's identity
(build_scored_replay_run_id) is deterministic over (replayRunId,
scoringFrameworkVersion) ONLY -- deliberately NOT over the settlement/
CLV/bet content, because that content is allowed to improve after the
fact (a corrected settlement, a late-arriving CLV quote, a bet's
receipt getting confirmed). Rerunning scoring against UNCHANGED
canonical inputs re-derives byte-identical content and is a true no-op
(see write_scored_replay_outputs); rerunning after a genuine correction
updates the SAME scored record in place. The original ReplayRun this
scores is never touched either way.
"""

import os
import tempfile
import json

from lib.edgelab import ids
from lib.edgelab import replay as replay_engine
from lib.edgelab import storage
from lib.edgelab.calibration import calibration_status

SCORING_FRAMEWORK_VERSION = "1"
SCORED_REPLAY_RUNS_ROOT = os.path.join("data", "edgelab", "scored_replay_runs")

# ── Wager evaluation stage (requirement 4) ───────────────────────────────
PREDICTION_UNAVAILABLE = "PREDICTION_UNAVAILABLE"
EVALUATED_NO_BET_PLACED = "EVALUATED_NO_BET_PLACED"
RECOMMENDED_NO_CONFIRMED_BET = "RECOMMENDED_NO_CONFIRMED_BET"
CONFIRMED_BET = "CONFIRMED_BET"

MARKET_SETTLED = "MARKET_SETTLED"
UNRESOLVED_SETTLEMENT = "UNRESOLVED_SETTLEMENT"

CLV_AVAILABLE = "CLV_AVAILABLE"
CLV_UNAVAILABLE = "CLV_UNAVAILABLE"

PREDICTION_AVAILABLE = "AVAILABLE"
PREDICTION_STATUS_UNAVAILABLE = "UNAVAILABLE"

# recordStatus values a bet must NOT carry to count as a genuine confirmed
# wager -- mirrors lib.edgelab.query.active()'s exact exclusion.
_CANCELLED_RECORD_STATUS = "CANCELLED"

_ACCEPTED_RECOMMENDATION_STATUS = "Accepted"


# ── 1. Pure scoring: one ReplayResult -> one ScoredReplayResult ─────────

def _wager_evaluation_stage(prediction_available, recommendation_action_status, bet_record):
    if not prediction_available:
        return PREDICTION_UNAVAILABLE
    if bet_record is not None:
        return CONFIRMED_BET
    if recommendation_action_status == _ACCEPTED_RECOMMENDATION_STATUS:
        return RECOMMENDED_NO_CONFIRMED_BET
    return EVALUATED_NO_BET_PLACED


def _bet_is_confirmed(bet_record):
    if bet_record is None:
        return False
    return (bet_record.get("recordStatus") or "ACTIVE") != _CANCELLED_RECORD_STATUS


def score_replay_result(result, *, model_evaluation_id=None, recommendation_id=None,
                         settlement_record=None, bet_record=None, scored_at=None):
    """
    Pure. `result` is one ReplayResult dict exactly as written by
    lib.edgelab.replay (never mutated here). The four keyword lookups
    are each either a genuine matching canonical record/id, or None when
    no such record was ever found -- this function never guesses one
    into existence.

    `settlement_record` (optional): the FULL Settlement record for this
    ticker (not just the narrow settlementLinkage already embedded on
    `result`), used only for its `betId` -- the objective outcome itself
    is read directly from `result["settlementLinkage"]`, never
    recomputed, since that is the exact linkage the replay engine
    already resolved against the frozen postgame snapshot.

    `bet_record` (optional): the PlacedBet this market's settlement.betId
    points to, when a genuine, non-CANCELLED confirmed bet exists.
    """
    if scored_at is None:
        scored_at = ids.utc_now_iso()

    original_prob = result.get("originalModelProbability")
    prediction_available = original_prob is not None

    market_prob_pregame = result.get("originalExecutableMarketProb")
    if market_prob_pregame is None:
        market_prob_pregame = result.get("originalMarketPrice")

    settlement_linkage = result.get("settlementLinkage") or {"status": "UNRESOLVED", "result": None, "reason": "NO_SETTLEMENT_LINKAGE_ON_REPLAY_RESULT"}
    clv_linkage = result.get("clvLinkage") or {"status": "UNRESOLVED", "clvValue": None, "reason": "NO_CLV_LINKAGE_ON_REPLAY_RESULT"}

    objective_outcome = {
        "settlementStatus": MARKET_SETTLED if settlement_linkage.get("status") == "RESOLVED" else UNRESOLVED_SETTLEMENT,
        "result": settlement_linkage.get("result"),
        "reason": settlement_linkage.get("reason"),
    }
    clv = {
        # clvLinkage is derived exclusively from the isClosingQuote=True
        # row (lib.edgelab.replay._closing_clv_by_ticker /
        # lib.edgelab.clv.select_closing_quote) -- "the final valid
        # pre-suspension/pre-start tradable quote", i.e. a valid
        # pre-first-pitch close by construction. A market with no such
        # quote is CLV_UNAVAILABLE, never substituted with a later or
        # non-closing price.
        "clvStatus": CLV_AVAILABLE if clv_linkage.get("status") == "RESOLVED" else CLV_UNAVAILABLE,
        "value": clv_linkage.get("clvValue"),
        "reason": clv_linkage.get("reason"),
    }

    confirmed_bet = bet_record if _bet_is_confirmed(bet_record) else None
    wager_stage = _wager_evaluation_stage(prediction_available, result.get("originalRecommendationStatus"), confirmed_bet)

    gross_return, net_pl = (None, None)
    if confirmed_bet is not None:
        from lib.edgelab.bets import realized_bet_economics
        gross_return, net_pl = realized_bet_economics(confirmed_bet)

    wager = {
        "evaluationStage": wager_stage,
        "betId": confirmed_bet.get("betId") if confirmed_bet else (settlement_record or {}).get("betId"),
        "result": confirmed_bet.get("result") if confirmed_bet else None,
        "stake": confirmed_bet.get("stake") if confirmed_bet else None,
        "grossReturn": gross_return,
        "netProfitLoss": net_pl,
    }

    # Brier score requires BOTH a real pregame prediction and a resolved,
    # genuinely binary settlement result -- never computed from a partial
    # pair (see requirement 5: "Brier score where probability + binary
    # outcome exist").
    brier_score = None
    binary_outcome = None
    if prediction_available and objective_outcome["settlementStatus"] == MARKET_SETTLED and objective_outcome["result"] in ("YES", "NO"):
        binary_outcome = 1 if objective_outcome["result"] == "YES" else 0
        brier_score = replay_engine.brier_score(original_prob / 100.0, binary_outcome)

    return {
        "schemaVersion": "1",
        "scoredReplayResultId": ids.build_scored_replay_result_id(result.get("replayResultId"), SCORING_FRAMEWORK_VERSION),
        "replayResultId": result.get("replayResultId"),
        "replayRunId": result.get("replayRunId"),
        "gameId": result.get("gameId"),
        "marketTicker": result.get("marketTicker"),
        "marketFamily": result.get("marketFamily"),
        "selection": result.get("selection"),
        "scoringFrameworkVersion": SCORING_FRAMEWORK_VERSION,
        "modelEvaluationId": model_evaluation_id,
        "recommendationId": recommendation_id,
        "predictionStatus": PREDICTION_AVAILABLE if prediction_available else PREDICTION_STATUS_UNAVAILABLE,
        "predictedFairProbability": original_prob,
        "marketProbabilityPregame": market_prob_pregame,
        "confidenceTier": result.get("originalTier"),
        "recommendationActionStatus": result.get("originalRecommendationStatus"),
        "objectiveOutcome": objective_outcome,
        "clv": clv,
        "wager": wager,
        "binaryOutcome": binary_outcome,
        "brierScore": brier_score,
        "scoredAt": scored_at,
        "provenance": {
            "sourceSystem": "scored_replay",
            "sourceFile": None,
            "sourceKey": result.get("replayResultId"),
            "capturedAt": scored_at,
            "ingestedAt": scored_at,
        },
    }


# ── 2. Pure aggregation over any list of ScoredReplayResult ─────────────

def _avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _brier_group_stats(rows):
    briers = [r["brierScore"] for r in rows if r["brierScore"] is not None]
    outcomes = [r["binaryOutcome"] for r in rows if r["binaryOutcome"] is not None]
    n = len(briers)
    if n == 0:
        return {"n": 0, "sampleSizeStatus": calibration_status(0), "avgBrierScore": None, "winRate": None}
    return {
        "n": n,
        "sampleSizeStatus": calibration_status(n),
        "avgBrierScore": _avg(briers),
        "winRate": round(sum(outcomes) / n, 4) if outcomes else None,
    }


_CALIBRATION_BUCKET_WIDTH = 10  # percentage points, 0-100


def _calibration_buckets(rows):
    """
    Deciles of predictedFairProbability (0-10%, 10-20%, ..., 90-100%),
    over rows with both a real prediction and a resolved binary outcome
    -- the same population brierScore is computed over. A market whose
    prediction is unavailable or whose settlement is unresolved
    contributes to no bucket (never guessed into one), matching
    requirement 6 exactly.
    """
    buckets = {}
    for r in rows:
        if r["brierScore"] is None or r["predictedFairProbability"] is None:
            continue
        p = r["predictedFairProbability"]
        bucket_start = min(90, int(p // _CALIBRATION_BUCKET_WIDTH) * _CALIBRATION_BUCKET_WIDTH)
        buckets.setdefault(bucket_start, []).append(r)

    out = []
    for start in sorted(buckets):
        rows_in_bucket = buckets[start]
        n = len(rows_in_bucket)
        avg_predicted = _avg([r["predictedFairProbability"] for r in rows_in_bucket])
        actual_rate = round(sum(r["binaryOutcome"] for r in rows_in_bucket) / n, 4)
        out.append({
            "bucket": f"{start}-{start + _CALIBRATION_BUCKET_WIDTH}%",
            "n": n,
            "sampleSizeStatus": calibration_status(n),
            "avgPredictedProbability": avg_predicted,
            "actualOutcomeRate": round(actual_rate * 100, 2),
        })
    return out


def _group_by(rows, key_fn):
    groups = {}
    for r in rows:
        key = key_fn(r)
        groups.setdefault(key, []).append(r)
    return groups


def _tally(values):
    counts = {}
    for v in values:
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
    return counts


def aggregate_scored_results(scored_results):
    """
    Pure. Computes every aggregate requirement 5 asks for over ANY list
    of ScoredReplayResult dicts -- one run's worth, or a caller-assembled
    concatenation across many runs/dates (this function has no concept
    of "which run" a row came from). Returns None only if the list is
    empty (never a fabricated all-zero report, same convention as
    lib.edgelab.replay.score_resolved_results).
    """
    n = len(scored_results)
    if n == 0:
        return None

    prediction_available_rows = [r for r in scored_results if r["predictionStatus"] == PREDICTION_AVAILABLE]
    settled_rows = [r for r in scored_results if r["objectiveOutcome"]["settlementStatus"] == MARKET_SETTLED]
    unresolved_rows = [r for r in scored_results if r["objectiveOutcome"]["settlementStatus"] == UNRESOLVED_SETTLEMENT]
    clv_rows = [r for r in scored_results if r["clv"]["clvStatus"] == CLV_AVAILABLE]
    confirmed_bet_rows = [r for r in scored_results if r["wager"]["evaluationStage"] == CONFIRMED_BET]
    recommended_rows = [r for r in scored_results if r["recommendationActionStatus"] == _ACCEPTED_RECOMMENDATION_STATUS]
    passed_rows = [r for r in scored_results if r["predictionStatus"] == PREDICTION_AVAILABLE and r["recommendationActionStatus"] != _ACCEPTED_RECOMMENDATION_STATUS]

    total_staked = sum(r["wager"]["stake"] for r in confirmed_bet_rows if r["wager"]["stake"] is not None)
    total_net_pl_rows = [r["wager"]["netProfitLoss"] for r in confirmed_bet_rows if r["wager"]["netProfitLoss"] is not None]
    total_net_pl = round(sum(total_net_pl_rows), 2) if total_net_pl_rows else None

    model_eval_linked = sum(1 for r in scored_results if r.get("modelEvaluationId") is not None)
    recommendation_linked = sum(1 for r in scored_results if r.get("recommendationId") is not None)
    recommended_no_confirmed_rows = [r for r in scored_results if r["wager"]["evaluationStage"] == RECOMMENDED_NO_CONFIRMED_BET]

    return {
        "n": n,
        "predictionAvailableCount": len(prediction_available_rows),
        "predictionUnavailableCount": n - len(prediction_available_rows),
        "settlement": {
            "settledCount": len(settled_rows),
            "unresolvedCount": len(unresolved_rows),
        },
        "brier": _brier_group_stats(scored_results),
        "calibrationBuckets": _calibration_buckets(scored_results),
        "byMarketFamily": {
            family: _brier_group_stats(rows)
            for family, rows in _group_by(scored_results, lambda r: r.get("marketFamily") or "UNKNOWN").items()
        },
        "byConfidenceTier": {
            (tier or "NONE"): _brier_group_stats(rows)
            for tier, rows in _group_by(scored_results, lambda r: r.get("confidenceTier")).items()
        },
        "recommendedVsPassed": {
            "recommended": _brier_group_stats(recommended_rows),
            "passed": _brier_group_stats(passed_rows),
        },
        "clv": {
            "coverageCount": len(clv_rows),
            "coverageRate": round(len(clv_rows) / n, 4),
            "avgClv": _avg([r["clv"]["value"] for r in clv_rows]),
        },
        "realizedPnl": {
            "confirmedBetCount": len(confirmed_bet_rows),
            "totalStaked": round(total_staked, 2) if confirmed_bet_rows else None,
            "totalNetProfitLoss": total_net_pl,
            "roi": round(total_net_pl / total_staked, 4) if (total_net_pl is not None and total_staked) else None,
        },
        "linkageCoverage": {
            "modelEvaluationLinkedCount": model_eval_linked,
            "modelEvaluationLinkedRate": round(model_eval_linked / n, 4),
            "recommendationLinkedCount": recommendation_linked,
            "recommendationLinkedRate": round(recommendation_linked / n, 4),
            "confirmedBetCount": len(confirmed_bet_rows),
            "recommendedNoConfirmedBetCount": len(recommended_no_confirmed_rows),
            # Rate among rows that were actually recommended (evaluationStage
            # is CONFIRMED_BET or RECOMMENDED_NO_CONFIRMED_BET) -- the
            # population a wager could plausibly exist for. Null (not 0.0)
            # when nothing was ever recommended, so an empty denominator is
            # never silently reported as "0% linked".
            "wagerLinkageRateAmongRecommended": (
                round(len(confirmed_bet_rows) / (len(confirmed_bet_rows) + len(recommended_no_confirmed_rows)), 4)
                if (confirmed_bet_rows or recommended_no_confirmed_rows) else None
            ),
        },
        "missingCoverageReasons": {
            "settlementUnresolvedReasons": _tally(r["objectiveOutcome"]["reason"] for r in unresolved_rows),
            "clvUnavailableReasons": _tally(r["clv"]["reason"] for r in scored_results if r["clv"]["clvStatus"] == CLV_UNAVAILABLE),
        },
    }


# ── 3. Canonical lookups (I/O) ───────────────────────────────────────────

def _model_eval_and_recommendation_lookup(date):
    """
    Reads the already-ingested canonical model_evaluations/<date>.jsonl
    and recommendations/<date>.jsonl (lib.edgelab.model_evaluation /
    lib.edgelab.recommendations' own output -- never recomputed here),
    keyed by the SAME (marketTicker, marketName) market_key both writers
    already use for id derivation. A date with no ingested file yet
    yields empty lookups -- ids simply stay null on every result, never
    guessed from a hash of inputs that may not correspond to a record
    that was actually ingested.
    """
    model_eval_by_key = {}
    for row in storage.read_partition("model_evaluations", date):
        key = (row.get("marketTicker"), row.get("selection"))
        model_eval_by_key[key] = row.get("modelEvaluationId")

    recommendation_by_key = {}
    for row in storage.read_partition("recommendations", date):
        key = (row.get("marketTicker"), row.get("marketName"))
        recommendation_by_key[key] = row.get("recommendationId")

    return model_eval_by_key, recommendation_by_key


def _bets_by_id():
    return {
        row.get("betId"): row
        for row in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))
        if row.get("betId")
    }


def _full_settlement_rows_for_run(run):
    """
    Reuses lib.edgelab.replay's own settlement/CLV linkage source (the
    linked POST_GAME_SETTLEMENT snapshot's frozen SETTLEMENT component)
    rather than a second, independent read of storage.partition_path
    ("settlements", date) -- the exact same integrity-verified rows the
    original ReplayResult.settlementLinkage was already computed from,
    so a betId join can never disagree with the outcome already frozen
    on the ReplayResult.

    DURABILITY (requirement: "determine why scored replay currently
    depends on a manifest that may later be pruned"): earlier versions
    of this function called lib.edgelab.snapshot.find_manifest_by_id(),
    which walks the ENTIRE data/edgelab/snapshots/ tree on disk looking
    for the PRE_GAME_DECISION manifest just to read two scalar fields
    off it -- snapshotId and snapshotDate.
    replay_engine._linked_settlement_and_clv() never reads anything else
    from that manifest. Both fields are ALREADY stored durably on the
    ReplayRun record itself (data/edgelab/replay_runs/<id>/
    replay_run.json, committed by scripts/run_forward_replay.py the
    same day the snapshot is captured) -- there is no need to re-locate
    the (potentially large, differently-retained, or simply not-yet-
    committed-on-this-runner) full manifest just to recover two
    identifiers this run already carries. Building a minimal
    {"snapshotId", "snapshotDate"} dict here removes that dependency
    entirely while preserving the EXACT SAME integrity guarantee
    _linked_settlement_and_clv already enforces (the postgame snapshot
    must list this snapshotId in its own linkedSnapshotIds) -- nothing
    about wager linkage is weakened, only the unnecessary manifest
    lookup is removed. Never duplicates the bet ledger or the settlement
    ledger -- still the same canonical lib.edgelab.replay function,
    still the same frozen SETTLEMENT component.

    Returns ({}, reason) when the run's own snapshot identity is
    missing, or the postgame link is unavailable -- callers degrade to
    "no wager linkage possible" rather than failing the whole score.
    """
    snapshot_id = run.get("snapshotId")
    snapshot_date = run.get("snapshotDate")
    if not snapshot_id or not snapshot_date:
        return {}, "REPLAY_RUN_MISSING_SNAPSHOT_IDENTITY"
    settlement_rows, _clv_rows, reason = replay_engine._linked_settlement_and_clv(
        {"snapshotId": snapshot_id, "snapshotDate": snapshot_date}
    )
    if reason:
        return {}, reason
    return {r.get("marketTicker"): r for r in (settlement_rows or []) if r.get("marketTicker")}, None


def assess_ingestion_readiness(date):
    """
    I/O. Checks whether the canonical upstream ledgers this scorer reads
    have been ingested AT ALL for `date` -- a simple file-existence
    check (never inspects row content/counts, and never blocks
    scoring -- see score_replay_run's docstring). Missing entirely
    (rather than merely containing per-market gaps, which is normal and
    already tracked per-row via UNRESOLVED_SETTLEMENT/CLV_UNAVAILABLE)
    is the signal that the postgame pipeline simply hasn't reached this
    date yet, so a scoring attempt this early would misreport near-total
    coverage gaps as if they were genuine settlement/CLV absence rather
    than "haven't looked yet."

    Returns {"recommendationsIngested": bool, "modelEvaluationsIngested":
    bool, "settlementsIngested": bool, "reasons": [...]}.
    """
    recommendations_ingested = storage.partition_exists("recommendations", date)
    model_evaluations_ingested = storage.partition_exists("model_evaluations", date)
    settlements_ingested = storage.partition_exists("settlements", date)

    reasons = []
    if not recommendations_ingested:
        reasons.append("RECOMMENDATIONS_NOT_YET_INGESTED_FOR_DATE")
    if not model_evaluations_ingested:
        reasons.append("MODEL_EVALUATIONS_NOT_YET_INGESTED_FOR_DATE")
    if not settlements_ingested:
        reasons.append("SETTLEMENTS_NOT_YET_INGESTED_FOR_DATE")

    return {
        "recommendationsIngested": recommendations_ingested,
        "modelEvaluationsIngested": model_evaluations_ingested,
        "settlementsIngested": settlements_ingested,
        "reasons": reasons,
    }


# ── 4. Orchestration (I/O) ────────────────────────────────────────────────

def score_replay_run(replay_run_id, scored_at=None):
    """
    Reads a completed ReplayRun + its ReplayResults (read-only,
    lib.edgelab.replay.load_replay_run/load_replay_results) and scores
    every result against canonical postgame evidence. Returns
    (scored_run, scored_results); NEITHER is written to disk here --
    call write_scored_replay_outputs() separately (mirrors
    lib.edgelab.replay.execute_replay's build-then-commit split).

    Returns (None, []) if the ReplayRun doesn't exist, or if it never
    completed (runStatus != COMPLETED -- a REJECTED/FAILED run has no
    ReplayResults worth scoring).

    Never refuses to score merely because upstream ingestion for the
    date is incomplete -- see assess_ingestion_readiness()'s docstring
    for why a hard gate isn't needed: every per-row linkage already
    degrades honestly (null id / UNRESOLVED_SETTLEMENT / CLV_UNAVAILABLE)
    when the corresponding ledger hasn't been ingested yet, scoring is
    idempotent (a later rerun with more data lands as an "updated"
    write, never a duplicate), and the ORIGINATING workflow step
    ordering (.github/workflows/edgelab-postgame.yml) already runs this
    AFTER settlement/recommendation ingestion in the normal case.
    ingestionReadiness is attached to the output specifically so a
    still-incomplete date is never silently mistaken for "fully scored".
    """
    run = replay_engine.load_replay_run(replay_run_id)
    if run is None or run.get("runStatus") != replay_engine.RUN_STATUS_COMPLETED:
        return None, []

    if scored_at is None:
        scored_at = ids.utc_now_iso()

    results = replay_engine.load_replay_results(replay_run_id)
    date = run.get("snapshotDate")

    model_eval_by_key, recommendation_by_key = _model_eval_and_recommendation_lookup(date) if date else ({}, {})
    bets_by_id = _bets_by_id()
    settlement_by_ticker, wager_linkage_unavailable_reason = _full_settlement_rows_for_run(run)
    ingestion_readiness = assess_ingestion_readiness(date) if date else {
        "recommendationsIngested": False, "modelEvaluationsIngested": False,
        "settlementsIngested": False, "reasons": ["REPLAY_RUN_MISSING_SNAPSHOT_DATE"],
    }

    scored_results = []
    for result in results:
        market_key = (result.get("marketTicker"), result.get("selection"))
        settlement_record = settlement_by_ticker.get(result.get("marketTicker"))
        bet_record = bets_by_id.get(settlement_record.get("betId")) if settlement_record and settlement_record.get("betId") else None

        scored_results.append(score_replay_result(
            result,
            model_evaluation_id=model_eval_by_key.get(market_key),
            recommendation_id=recommendation_by_key.get(market_key),
            settlement_record=settlement_record,
            bet_record=bet_record,
            scored_at=scored_at,
        ))

    limitation_reasons = list(run.get("limitationReasons") or [])
    if wager_linkage_unavailable_reason:
        limitation_reasons.append(f"WAGER_LINKAGE_UNAVAILABLE: {wager_linkage_unavailable_reason}")
    limitation_reasons.extend(ingestion_readiness["reasons"])

    scored_run = {
        "schemaVersion": "1",
        "scoredReplayRunId": ids.build_scored_replay_run_id(replay_run_id, SCORING_FRAMEWORK_VERSION),
        "replayRunId": replay_run_id,
        "snapshotId": run.get("snapshotId"),
        "snapshotDate": date,
        "scoringFrameworkVersion": SCORING_FRAMEWORK_VERSION,
        "scoredAt": scored_at,
        "ingestionReadiness": ingestion_readiness,
        "wagerLinkageAvailable": wager_linkage_unavailable_reason is None,
        "limitationReasons": sorted(set(limitation_reasons)),
        "summary": aggregate_scored_results(scored_results),
        "provenance": {
            "sourceSystem": "scored_replay",
            "sourceFile": None,
            "sourceKey": replay_run_id,
            "capturedAt": scored_at,
            "ingestedAt": scored_at,
        },
    }
    scored_run["contentHash"] = compute_scored_run_content_hash(scored_run, scored_results)
    return scored_run, scored_results


_SCORED_RUN_HASH_EXCLUDED_FIELDS = frozenset({"contentHash", "scoredAt", "provenance"})
_SCORED_RESULT_HASH_EXCLUDED_FIELDS = frozenset({"scoredAt", "provenance"})


def compute_scored_run_content_hash(scored_run, scored_results):
    """
    Content-only hash (timestamps/provenance excluded) over BOTH the run
    summary and every result -- used by write_scored_replay_outputs to
    tell "rerun against unchanged canonical inputs" (identical hash, true
    no-op) apart from "a later corrected settlement changed the content"
    (different hash, in-place update) -- requirement 8, exactly.
    """
    run_candidate = {k: v for k, v in scored_run.items() if k not in _SCORED_RUN_HASH_EXCLUDED_FIELDS}
    results_candidate = [
        {k: v for k, v in r.items() if k not in _SCORED_RESULT_HASH_EXCLUDED_FIELDS}
        for r in scored_results
    ]
    return replay_engine.sha256_bytes(replay_engine.canonical_json_bytes({"run": run_candidate, "results": results_candidate}))


# ── 5. Output writer (I/O) ────────────────────────────────────────────────

def scored_replay_run_dir(scored_replay_run_id: str) -> str:
    return os.path.join(SCORED_REPLAY_RUNS_ROOT, scored_replay_run_id)


def _atomic_write_json(dest_path, obj):
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".scored_replay.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_scored_replay_outputs(scored_run, scored_results):
    """
    Idempotent, in-place-updatable writer -- deliberately NOT write-once
    like lib.edgelab.replay.write_replay_outputs (see this module's
    docstring for why). Returns {"outcome": "created"|"noop_unchanged"|
    "updated", "path": ...}.

    Never touches data/edgelab/replay_runs/ -- writes exclusively under
    SCORED_REPLAY_RUNS_ROOT, keyed by scored_run['scoredReplayRunId']
    (deterministic per replayRunId + scoringFrameworkVersion, so a
    second scoring attempt for the same replay run always targets the
    SAME output location, whether that's a true no-op or a content
    update).
    """
    out_dir = scored_replay_run_dir(scored_run["scoredReplayRunId"])
    run_path = os.path.join(out_dir, "scored_replay_run.json")
    results_path = os.path.join(out_dir, "scored_replay_results.jsonl")

    if os.path.exists(run_path):
        with open(run_path) as f:
            existing = json.load(f)
        if existing.get("contentHash") == scored_run.get("contentHash"):
            return {"outcome": "noop_unchanged", "path": out_dir}
        _write_scored_replay_files(run_path, results_path, scored_run, scored_results)
        return {"outcome": "updated", "path": out_dir}

    _write_scored_replay_files(run_path, results_path, scored_run, scored_results)
    return {"outcome": "created", "path": out_dir}


def _write_scored_replay_files(run_path, results_path, scored_run, scored_results):
    _atomic_write_json(run_path, scored_run)
    dest_dir = os.path.dirname(results_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    lines = [json.dumps(r, sort_keys=True) for r in scored_results]
    fd, tmp_path = tempfile.mkstemp(prefix=".scored_replay.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, results_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_scored_replay_run(scored_replay_run_id: str):
    path = os.path.join(scored_replay_run_dir(scored_replay_run_id), "scored_replay_run.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_scored_replay_results(scored_replay_run_id: str):
    path = os.path.join(scored_replay_run_dir(scored_replay_run_id), "scored_replay_results.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ── 6. Date-level coverage report (requirement 4) ─────────────────────────

DATE_REPORTS_DIR = os.path.join("data", "edgelab", "reports", "scored_replay")


def build_date_report(scored_run, scored_results):
    """
    Pure. Reshapes one date's (scored_run, scored_results) -- exactly
    what score_replay_run() returns -- into the date-keyed coverage
    summary a human or dashboard actually wants: scoreable vs. total
    predictions, settlement/linkage/CLV coverage, Brier/calibration
    where available, and explicit reasons for any missing coverage.
    Every number here is read straight off scored_run['summary']
    (aggregate_scored_results' own output) -- this function computes
    nothing new, it only re-labels/re-groups for date-level reporting.

    Returns None if scored_run is None (nothing to report) or has zero
    scored results.
    """
    if scored_run is None or not scored_results:
        return None

    summary = scored_run["summary"]
    if summary is None:
        return None

    return {
        "schemaVersion": "1",
        "date": scored_run.get("snapshotDate"),
        "scoredReplayRunId": scored_run.get("scoredReplayRunId"),
        "replayRunId": scored_run.get("replayRunId"),
        "snapshotId": scored_run.get("snapshotId"),
        "generatedAt": scored_run.get("scoredAt"),
        "ingestionReadiness": scored_run.get("ingestionReadiness"),
        "wagerLinkageAvailable": scored_run.get("wagerLinkageAvailable"),
        "predictions": {
            "scoreable": summary["predictionAvailableCount"],
            "total": summary["n"],
            "scoreableRate": round(summary["predictionAvailableCount"] / summary["n"], 4) if summary["n"] else None,
        },
        "settlementCoverage": {
            "settled": summary["settlement"]["settledCount"],
            "unresolved": summary["settlement"]["unresolvedCount"],
            "rate": round(summary["settlement"]["settledCount"] / summary["n"], 4) if summary["n"] else None,
        },
        "recommendationLinkageCoverage": {
            "linked": summary["linkageCoverage"]["recommendationLinkedCount"],
            "rate": summary["linkageCoverage"]["recommendationLinkedRate"],
        },
        "modelEvaluationLinkageCoverage": {
            "linked": summary["linkageCoverage"]["modelEvaluationLinkedCount"],
            "rate": summary["linkageCoverage"]["modelEvaluationLinkedRate"],
        },
        "wagerLinkageCoverage": {
            "confirmedBetCount": summary["linkageCoverage"]["confirmedBetCount"],
            "recommendedNoConfirmedBetCount": summary["linkageCoverage"]["recommendedNoConfirmedBetCount"],
            "rateAmongRecommended": summary["linkageCoverage"]["wagerLinkageRateAmongRecommended"],
        },
        "clvCoverage": summary["clv"],
        "brier": summary["brier"],
        "calibrationBuckets": summary["calibrationBuckets"],
        "realizedPnl": summary["realizedPnl"],
        "missingCoverageReasons": summary["missingCoverageReasons"],
        "limitationReasons": scored_run.get("limitationReasons", []),
    }


def date_report_path(date: str) -> str:
    return os.path.join(DATE_REPORTS_DIR, f"{date}.json")


def write_scored_replay_date_report(report):
    """
    Always overwrites in place -- this report is a pure, deterministic
    projection of the already-idempotent ScoredReplayRun/ScoredReplayResult
    data (see build_date_report), so it carries no separate identity or
    conflict-detection concern of its own; regenerating it from unchanged
    inputs produces byte-identical content. Returns the path written, or
    None if there was nothing to report.
    """
    if report is None:
        return None
    path = date_report_path(report["date"])
    _atomic_write_json(path, report)
    return path
