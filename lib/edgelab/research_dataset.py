"""
lib/edgelab/research_dataset.py
====================================
EdgeLab Research Trustworthiness milestone (docs/EDGELAB_PHASE1.md
section D and this milestone's own spec): the canonical, READ-ONLY
historical "opportunity" dataset. The fundamental row is one
marketTicker x one standardized PRE-GAME checkpoint, built over the
FULL observed Kalshi MLB market universe -- never-recommended and
never-bet markets included, not only markets a bet was placed on.

This module does not recompute any model math, does not mutate any
canonical corpus file, and never feeds anything back into production
recommendation/betting logic. It is a pure builder over already-loaded
lists (same convention as lib.edgelab.query), reusing rather than
reimplementing:
  - lib.edgelab.checkpoints for checkpoint classification and the
    canonical closing-quote selection rule (never "just the last tick").
  - lib.edgelab.temporal_alignment for the no-look-ahead
    ModelEvaluation<->checkpoint join (never a ticker-only,
    unordered-list "last element" pick -- see that module's docstring
    for why lib.edgelab.query.build_research_rows's own
    `evals_for_ticker[-1]` pattern is NOT reused here).
  - lib.edgelab.market_family_mapping.canonicalize_market_family for
    the one canonical family vocabulary.
  - lib.edgelab.settlement for hypothetical-return math and the
    was-this-market-ever-recommended/placed helpers.

SCALE CONVENTIONS enforced by this module (verified against real data,
not assumed -- see this milestone's audit):
  - MarketObservation yesBid/yesAsk/noBid/noAsk/lastPrice are 0-100
    (cents/percent) on disk. Every *Price field this module emits is
    normalized to 0-1 (an "implied probability" a bettor would recognize
    as matching PlacedBet.entryPrice's own convention).
  - ModelEvaluation.modelFairProbability/marketImpliedProbability are
    0-100 on disk. Every *Probability field this module emits is
    normalized to 0-1, matching the price fields above so `edge`
    (probability minus price) is always a same-scale subtraction.
  - ModelEvaluation.estimatedEdge is native percentage-POINT scale
    (already documented as such elsewhere in this repo) and is passed
    through unconverted, but ALWAYS under a name that makes clear it
    was computed by the pipeline against ITS OWN price snapshot, not
    this row's checkpoint price -- see `estimatedEdgeAtEvaluationTime`
    vs `contemporaneousEdge` below (spec section 9: "Do not reuse an
    edge that was calculated from a different price observation unless
    explicitly labeled as such").
"""

from collections import defaultdict
from datetime import datetime, timezone

from lib.edgelab import checkpoints as ckpt
from lib.edgelab import temporal_alignment as ta
from lib.edgelab.market_family_mapping import canonicalize_market_family
from lib.edgelab.settlement import hypothetical_yes_return, was_market_ever_recommended

# The named, standardized pregame checkpoints this dataset ever emits a
# row for -- CLOSING is deliberately NOT in this set: it is never a
# label MarketObservation.checkpoint itself carries (see
# lib.edgelab.checkpoints's own docstring -- classify_checkpoint never
# returns "CLOSING"), it is a derived boolean (isClosingQuote) attached
# to whichever named-or-INTERMEDIATE observation
# lib.edgelab.checkpoints.select_closing_quote() actually selects.
# POST_START and INTERMEDIATE observations never become a row on their
# own -- spec section 6: "Do not silently substitute arbitrary
# INTERMEDIATE observations when a named checkpoint is missing."
NAMED_CHECKPOINTS = (
    "FIRST_DAILY", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5", "LINEUP_CONFIRMATION",
)

CLOSING = "CLOSING"

# Display/comparison order used by checkpoint_research reports -- CLOSING
# is its own bucket (isClosingQuote wins), everything else falls back to
# its own named checkpoint label.
STANDARDIZED_CHECKPOINT_ORDER = ("FIRST_DAILY", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5", "LINEUP_CONFIRMATION", CLOSING)

MODEL_UNAVAILABLE_NO_EVALUATIONS = ta.NO_EVALUATIONS_FOR_TICKER
MODEL_UNAVAILABLE_NO_TIMESTAMP = ta.NO_CAUSAL_TIMESTAMP
MODEL_UNAVAILABLE_FUTURE_ONLY = ta.ALL_EVALUATIONS_AFTER_CHECKPOINT


def _parse_iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct_to_fraction(value):
    """0-100 -> 0-1. None-safe. Never clamps/guesses -- a value already out of [0,100] is returned as-is divided, so a data-quality bug upstream stays visible rather than silently clipped."""
    return (value / 100.0) if value is not None else None


def _minutes_to_start(captured_at, scheduled_start):
    captured_dt, scheduled_dt = _parse_iso(captured_at), _parse_iso(scheduled_start)
    if captured_dt is None or scheduled_dt is None:
        return None
    return round((scheduled_dt - captured_dt).total_seconds() / 60.0, 2)


def _executable_yes_price(obs):
    """The price a bettor would actually pay to buy YES at this observation -- yesAsk preferred, yesBid as a documented fallback when ask is missing (never a guessed value)."""
    ask, bid = obs.get("yesAsk"), obs.get("yesBid")
    raw = ask if ask is not None else bid
    return _pct_to_fraction(raw)


def _executable_no_price(obs):
    """The price a bettor would actually pay to buy NO -- noAsk preferred, else (100 - yesBid) (Kalshi's NO ask is economically 100 minus the YES bid), same convention lib.edgelab.clv._executable_closing_implied already uses."""
    no_ask = obs.get("noAsk")
    if no_ask is not None:
        return _pct_to_fraction(no_ask)
    yes_bid = obs.get("yesBid")
    return _pct_to_fraction(100.0 - yes_bid) if yes_bid is not None else None


def hypothetical_no_return(no_price, result):
    """
    Price-dependent hypothetical return per $1 staked on the NO side at
    `no_price` (0-1 fraction) -- the NO-side mirror of
    lib.edgelab.settlement.hypothetical_yes_return, kept here (not added
    to that module) since it exists purely for full-universe research,
    never for production settlement. Never fabricates a return for a
    missing price -- returns None instead.
    """
    if no_price is None or no_price <= 0 or no_price >= 1:
        return None
    if result is None:
        return 0.0
    if result == "NO":
        return round((1.0 - no_price) / no_price, 4)
    if result == "YES":
        return -1.0
    return 0.0


def _select_named_checkpoint_observations(obs_list):
    """
    One observation per NAMED_CHECKPOINTS label for this ticker -- when
    more than one real observation happens to classify into the same
    label (rare; a real recurring-poll artifact, not expected but
    possible), picks the earliest capturedAt for a fully deterministic
    result, never an arbitrary/last one.
    """
    by_label = {}
    for obs in obs_list:
        label = obs.get("checkpoint")
        if label not in NAMED_CHECKPOINTS:
            continue
        current = by_label.get(label)
        if current is None or (obs.get("capturedAt") or "") < (current.get("capturedAt") or ""):
            by_label[label] = obs
    return by_label


# ── Model-evaluation-time provenance (Prospective Model Snapshots reliability pass) ──
#
# marketPriceAgeSeconds answers a DIFFERENT causal question than this
# module's own primary (market-checkpoint -> model) selection above:
# "given the model's own evaluation instant, what was the MOST RECENT
# Kalshi price that already existed by then?" -- i.e. how stale was the
# market data available to whatever process ran the model, not how
# stale the model's opinion is relative to a later market tick. Spec
# section 4's definition: marketPriceAgeSeconds = modelEvaluatedAt -
# marketObservationCapturedAt; never negative in a valid pairing (an
# observation captured AFTER the model evaluated is never a valid
# contemporaneous input for that model state); unavailable (never
# future-filled) when no prior observation exists at all.

PRICE_AGE_LE_5MIN = "<=5min"
PRICE_AGE_5_15MIN = ">5-15min"
PRICE_AGE_15_30MIN = ">15-30min"
PRICE_AGE_GT_30MIN = ">30min"
PRICE_AGE_UNAVAILABLE = "unavailable"

# Nominal clock-distance target (minutes to scheduled start) for the
# checkpoints that ARE genuinely time-target-based -- MODEL_CLOSING_WINDOW
# and LINEUP_CONFIRMATION have no fixed target (a window and a state
# change, respectively), so checkpointTimingErrorSeconds is correctly
# left None for those rather than measured against an arbitrary number.
_CHECKPOINT_NOMINAL_TARGET_MINUTES = {"T_MINUS_90": 90, "T_MINUS_60": 60, "T_MINUS_30": 30}


def market_price_age_bucket(age_seconds):
    """Pure. One of the PRICE_AGE_* buckets above, or PRICE_AGE_UNAVAILABLE for None."""
    if age_seconds is None:
        return PRICE_AGE_UNAVAILABLE
    minutes = age_seconds / 60.0
    if minutes <= 5:
        return PRICE_AGE_LE_5MIN
    if minutes <= 15:
        return PRICE_AGE_5_15MIN
    if minutes <= 30:
        return PRICE_AGE_15_30MIN
    return PRICE_AGE_GT_30MIN


def _seconds_between(later_iso, earlier_iso):
    """(later - earlier) in seconds. Returns None (never a fabricated/negative value) if either timestamp is unparseable or the result would be negative."""
    later_dt, earlier_dt = _parse_iso(later_iso), _parse_iso(earlier_iso)
    if later_dt is None or earlier_dt is None:
        return None
    delta = (later_dt - earlier_dt).total_seconds()
    return round(delta, 1) if delta >= 0 else None


def _select_latest_observation_at_or_before(obs_list_sorted, target_iso):
    """
    The latest observation in `obs_list_sorted` (already ascending by
    capturedAt) whose capturedAt <= target_iso -- the reverse-direction
    sibling of lib.edgelab.temporal_alignment.select_temporally_valid_evaluation
    (that one finds the latest EVALUATION at-or-before an OBSERVATION;
    this finds the latest OBSERVATION at-or-before an EVALUATION). Never
    future-fills: returns None when every observation for this ticker
    was captured after `target_iso`, or `target_iso` itself is
    unparseable -- the caller must leave the linkage unavailable rather
    than attaching a later quote.
    """
    target_dt = _parse_iso(target_iso)
    if target_dt is None:
        return None
    best = None
    for obs in obs_list_sorted:
        obs_dt = _parse_iso(obs.get("capturedAt"))
        if obs_dt is None or obs_dt > target_dt:
            continue
        if best is None or obs_dt > _parse_iso(best.get("capturedAt")):
            best = obs
    return best


def build_opportunity_rows(observations, settlements=None, evaluations=None, recommendations=None, bets=None, games=None):
    """
    One row per (marketTicker, standardized checkpoint) across the full
    observed market universe. `settlements`/`evaluations`/
    `recommendations`/`bets`/`games` each default to None, meaning "this
    source was not supplied" -- distinct from an explicitly empty list.
    Fields sourced only from a supplied source stay None/False rather
    than being guessed when that source was never loaded (same
    convention as lib.edgelab.query.build_research_rows).

    Returns a list of dicts, described in this module's docstring and in
    docs/EDGELAB_RESEARCH_TRUSTWORTHINESS.md.
    """
    evaluations_loaded = evaluations is not None
    recommendations_loaded = recommendations is not None
    bets_loaded = bets is not None

    settlements = settlements or []
    evaluations = evaluations or []
    recommendations = recommendations or []
    bets = bets or []
    games = games or []

    game_by_id = {g["gameId"]: g for g in games if g.get("gameId")}
    settlement_by_ticker = {s["marketTicker"]: s for s in settlements if s.get("marketTicker")}

    evaluations_by_ticker = defaultdict(list)
    for e in evaluations:
        if e.get("marketTicker"):
            evaluations_by_ticker[e["marketTicker"]].append(e)

    recommendations_by_eval_id = {}
    recommendations_by_ticker = defaultdict(list)
    for r in recommendations:
        if r.get("modelEvaluationId"):
            recommendations_by_eval_id[r["modelEvaluationId"]] = r
        if r.get("marketTicker"):
            recommendations_by_ticker[r["marketTicker"]].append(r)

    bets_by_ticker = defaultdict(list)
    for b in bets:
        if b.get("marketTicker") and (b.get("recordStatus") or "ACTIVE") != "CANCELLED":
            bets_by_ticker[b["marketTicker"]].append(b)

    obs_by_ticker = defaultdict(list)
    for o in observations:
        if o.get("marketTicker"):
            obs_by_ticker[o["marketTicker"]].append(o)

    rows = []
    for ticker, obs_list in obs_by_ticker.items():
        obs_list = sorted(obs_list, key=lambda o: o.get("capturedAt") or "")
        display_obs = obs_list[-1]  # descriptive attributes (family/threshold/team/player) don't change tick-to-tick
        game_id = display_obs.get("gameId")
        game = game_by_id.get(game_id) or {}
        scheduled_start = display_obs.get("scheduledStart") or game.get("scheduledStartTime")
        actual_start = game.get("actualStartTime")

        named_by_label = _select_named_checkpoint_observations(obs_list)
        closing_obs = ckpt.select_closing_quote(obs_list, scheduled_start=scheduled_start, actual_start=actual_start)

        candidate_obs_by_id = {o["marketObservationId"]: o for o in named_by_label.values()}
        closing_obs_id = None
        if closing_obs is not None:
            closing_obs_id = closing_obs.get("marketObservationId")
            candidate_obs_by_id.setdefault(closing_obs_id, closing_obs)

        settlement = settlement_by_ticker.get(ticker)
        evals_for_ticker = evaluations_by_ticker.get(ticker, [])
        recs_for_ticker = recommendations_by_ticker.get(ticker, [])
        bets_for_ticker = bets_by_ticker.get(ticker, [])

        was_recommended = (settlement or {}).get("wasRecommended")
        if was_recommended is None and recommendations_loaded:
            was_recommended = was_market_ever_recommended(recs_for_ticker)
        was_placed = (settlement or {}).get("wasPlaced")
        if was_placed is None and bets_loaded:
            was_placed = bool(bets_for_ticker)

        game_date = game.get("gameDate") or (scheduled_start or "")[:10] or None

        for obs_id, obs in candidate_obs_by_id.items():
            checkpoint_label = obs.get("checkpoint")
            is_closing_quote = obs_id == closing_obs_id
            captured_at = obs["capturedAt"]

            yes_price = _executable_yes_price(obs)
            no_price = _executable_no_price(obs)
            bid_ask_spread = None
            if obs.get("yesAsk") is not None and obs.get("yesBid") is not None:
                bid_ask_spread = round(_pct_to_fraction(obs["yesAsk"] - obs["yesBid"]), 4)

            row = {
                # IDENTITY
                "gameDate": game_date,
                "gameId": game_id,
                "marketTicker": ticker,
                "canonicalMarketFamily": canonicalize_market_family(obs.get("marketFamily")),
                "rawMarketFamily": obs.get("marketFamily"),
                "marketHorizon": obs.get("marketHorizon"),
                "threshold": obs.get("threshold"),
                "comparisonOperator": obs.get("comparisonOperator"),
                "team": obs.get("team"),
                "player": obs.get("player"),
                "outcomeLabel": obs.get("outcomeLabel"),
                "scheduledStart": scheduled_start,

                # MARKET OBSERVATION
                "marketObservationId": obs_id,
                "checkpoint": checkpoint_label,
                "researchCheckpoint": CLOSING if is_closing_quote else checkpoint_label,
                "isClosingQuote": is_closing_quote,
                "capturedAt": captured_at,
                "minutesToStart": _minutes_to_start(captured_at, scheduled_start),
                "yesBid": _pct_to_fraction(obs.get("yesBid")),
                "yesAsk": _pct_to_fraction(obs.get("yesAsk")),
                "noBid": _pct_to_fraction(obs.get("noBid")),
                "noAsk": _pct_to_fraction(obs.get("noAsk")),
                "lastPrice": _pct_to_fraction(obs.get("lastPrice")),
                "executableYesPrice": yes_price,
                "executableNoPrice": no_price,
                "bidAskSpread": bid_ask_spread,
                "marketStatus": obs.get("marketStatus"),
                "isValidPregameObservation": obs.get("isValidPregameObservation"),
                "isClosingCandidate": obs.get("isClosingCandidate"),
                "lineupConfirmationState": obs.get("lineupConfirmationState"),
                "sourceSystem": obs.get("source"),

                # OUTCOME
                "settlementStatus": (settlement or {}).get("settlementStatus"),
                "settlementResult": (settlement or {}).get("result"),
                "settlementUnavailableReason": (settlement or {}).get("unavailableReason"),
                "hypotheticalYesReturn": None,
                "hypotheticalNoReturn": None,
                "wasRecommended": was_recommended,
                "wasPlaced": was_placed,

                # MODEL STATE (filled below)
                "modelEvaluationAvailable": False,
                "modelEvaluationUnavailableReason": None,
                "modelEvaluationId": None,
                "modelEvaluationTimestamp": None,
                "modelVersion": None,
                "modelCommitSha": None,
                "modelSource": None,
                "selection": None,
                "side": None,
                "evaluationStatus": None,
                "modelFairProbability": None,
                "marketImpliedProbabilityAtEvaluation": None,
                "estimatedEdgeAtEvaluationTime": None,
                "contemporaneousEdge": None,
                "confidence": None,
                "dataQuality": None,
                "thesisTags": [],
                "correlationGroups": [],
                "recommendationStatus": None,
                "modelSelectionAmbiguous": False,
                "modelEvaluationAlternateCount": 0,

                # MODEL-EVALUATION-TIME PROVENANCE (Prospective Model
                # Snapshots reliability pass -- spec sections 3/4/6/7).
                # These describe the MODEL's own evaluation moment,
                # deliberately distinct from this row's own market-side
                # `checkpoint`/`researchCheckpoint` fields above (which
                # describe WHEN THIS OBSERVATION was captured, not when
                # the model ran) -- never conflate the two. See module
                # docstring section "MODEL-EVALUATION-TIME PROVENANCE".
                "modelEvaluationCheckpoint": None,
                "inputFreshnessNote": None,
                "modelEvaluationMinutesToStart": None,
                "checkpointTimingErrorSeconds": None,
                "marketObservationCapturedAtForModelEval": None,
                "marketPriceAgeSeconds": None,
                "marketPriceAgeBucket": PRICE_AGE_UNAVAILABLE,

                # PRICE MOVEMENT (filled in a second pass, see _attach_price_movement)
                "nextCheckpointExecutableYesPrice": None,
                "closingExecutableYesPrice": None,
                "priceMoveToNextCheckpoint": None,
                "fullUniverseMarketMovementToClose": None,
            }

            if settlement and settlement.get("settlementStatus") == "SETTLED":
                result = settlement.get("result")
                row["hypotheticalYesReturn"] = hypothetical_yes_return(yes_price, result)
                row["hypotheticalNoReturn"] = hypothetical_no_return(no_price, result)

            if evaluations_loaded:
                selected, candidates, reason = ta.select_temporally_valid_evaluation(evals_for_ticker, captured_at)
                if selected is None:
                    row["modelEvaluationUnavailableReason"] = reason
                else:
                    model_fair_probability = _pct_to_fraction(selected.get("modelFairProbability"))
                    row.update({
                        "modelEvaluationAvailable": True,
                        "modelEvaluationId": selected.get("modelEvaluationId"),
                        "modelEvaluationTimestamp": selected.get("pipelineRunId"),
                        "modelVersion": selected.get("modelVersion"),
                        "modelCommitSha": selected.get("modelCommitSha"),
                        "modelSource": selected.get("modelSource"),
                        "selection": selected.get("selection"),
                        "side": selected.get("side"),
                        "evaluationStatus": selected.get("evaluationStatus"),
                        "modelFairProbability": model_fair_probability,
                        "marketImpliedProbabilityAtEvaluation": _pct_to_fraction(selected.get("marketImpliedProbability")),
                        "estimatedEdgeAtEvaluationTime": selected.get("estimatedEdge"),
                        "confidence": selected.get("confidence"),
                        "dataQuality": selected.get("dataQuality"),
                        "thesisTags": list(selected.get("thesisTags") or []),
                        "correlationGroups": list(selected.get("correlationGroups") or []),
                        "modelSelectionAmbiguous": len(candidates) > 1,
                        "modelEvaluationAlternateCount": max(0, len(candidates) - 1),
                    })
                    if model_fair_probability is not None:
                        side = selected.get("side") or "YES"
                        checkpoint_price = yes_price if side == "YES" else no_price
                        if checkpoint_price is not None:
                            row["contemporaneousEdge"] = round(model_fair_probability - checkpoint_price, 4)
                    rec = recommendations_by_eval_id.get(selected.get("modelEvaluationId"))
                    if rec is not None:
                        row["recommendationStatus"] = rec.get("status")

                    model_eval_checkpoint = selected.get("checkpoint")
                    model_eval_at = selected.get("pipelineRunId")
                    row["modelEvaluationCheckpoint"] = model_eval_checkpoint
                    row["inputFreshnessNote"] = selected.get("inputFreshnessNote")

                    model_eval_minutes_to_start = _minutes_to_start(model_eval_at, scheduled_start)
                    row["modelEvaluationMinutesToStart"] = model_eval_minutes_to_start
                    nominal_target = _CHECKPOINT_NOMINAL_TARGET_MINUTES.get(model_eval_checkpoint)
                    if nominal_target is not None and model_eval_minutes_to_start is not None:
                        row["checkpointTimingErrorSeconds"] = round((model_eval_minutes_to_start - nominal_target) * 60.0, 1)

                    # marketPriceAgeSeconds (spec section 4): the LATEST
                    # MarketObservation for this exact ticker at or
                    # BEFORE the model's own evaluation instant -- the
                    # REVERSE causal direction from this row's own
                    # (market-checkpoint -> model) selection above. Never
                    # future-fills: if no observation existed before the
                    # model ran, the linkage stays unavailable rather
                    # than attaching a later quote.
                    prior_obs = _select_latest_observation_at_or_before(obs_list, model_eval_at)
                    if prior_obs is not None:
                        prior_captured_at = prior_obs["capturedAt"]
                        age_seconds = _seconds_between(model_eval_at, prior_captured_at)
                        row["marketObservationCapturedAtForModelEval"] = prior_captured_at
                        row["marketPriceAgeSeconds"] = age_seconds
                        row["marketPriceAgeBucket"] = market_price_age_bucket(age_seconds)

            rows.append(row)

    _attach_price_movement(rows)
    return rows


def _attach_price_movement(rows):
    """
    Second pass, per ticker: fills nextCheckpointExecutableYesPrice/
    closingExecutableYesPrice/priceMoveToNextCheckpoint/
    fullUniverseMarketMovementToClose IN PLACE. This is deliberately
    NOT "user CLV" (lib.edgelab.clv.compute_clv_for_bet, which requires
    a real placed bet's own entryPrice) -- it is a hypothetical,
    full-universe price-movement measure available for every observed
    market/checkpoint, settled or not, bet or not. Kept under its own
    field name so no caller can mistake it for real bet CLV (spec
    section 13).
    """
    by_ticker = defaultdict(list)
    for row in rows:
        by_ticker[row["marketTicker"]].append(row)

    for ticker_rows in by_ticker.values():
        ticker_rows.sort(key=lambda r: r["capturedAt"])
        closing_price = next((r["executableYesPrice"] for r in ticker_rows if r["isClosingQuote"]), None)
        for i, row in enumerate(ticker_rows):
            if i + 1 < len(ticker_rows):
                row["nextCheckpointExecutableYesPrice"] = ticker_rows[i + 1]["executableYesPrice"]
            row["closingExecutableYesPrice"] = closing_price
            if row["executableYesPrice"] is not None:
                if row["nextCheckpointExecutableYesPrice"] is not None:
                    row["priceMoveToNextCheckpoint"] = round(row["nextCheckpointExecutableYesPrice"] - row["executableYesPrice"], 4)
                if closing_price is not None and not row["isClosingQuote"]:
                    row["fullUniverseMarketMovementToClose"] = round(closing_price - row["executableYesPrice"], 4)
