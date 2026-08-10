"""
lib/edgelab/query.py
========================
Cross-chat read-only query interface over the canonical placed-bet
ledger (Canonical Placed-Bet Ledger milestone, requirement 9). Every
function here is a pure filter/aggregate over an already-loaded list of
PlacedBet (or BankrollTransaction) records -- no file I/O, so any
project chat/script/test can call these directly against whatever it
already has loaded, and scripts/edgelab/query_bets.py wraps them as a
CLI for anything that instead wants to shell out.

Read-only by construction: nothing in this module ever calls
lib.edgelab.bets.write_placed_bet or lib.edgelab.storage.append_records/
upsert_records. Before answering a question about actual wagers, a chat
should read through here (or the CLI), never rely on its own memory of
the conversation or on the recommendation list (see
docs/CANONICAL_BET_LEDGER.md's cross-chat operating protocol).
"""

from collections import defaultdict

from lib.edgelab.calibration import calibration_status
from lib.edgelab.settlement import was_market_ever_recommended


def _entry_date(bet):
    """Prefer the explicit gameDate; fall back to entryTimestamp's date component."""
    return bet.get("gameDate") or (bet.get("entryTimestamp") or "")[:10] or None


def by_date(bets, date):
    return [b for b in bets if _entry_date(b) == date]


def by_date_range(bets, start_date, end_date):
    return [b for b in bets if start_date <= (_entry_date(b) or "") <= end_date]


def unsettled(bets):
    """
    Real, still-open wagers -- excludes CANCELLED (a bet logged in error
    is not a genuinely open position, even while its `status` field
    still reads "pending"; found during the maintainer review of this
    milestone -- see also compute_bankroll_summary's identical fix).
    """
    return [b for b in active(bets) if b.get("status") == "pending"]


def settled(bets):
    return [b for b in active(bets) if b.get("status") == "settled"]


def voided(bets):
    return [b for b in active(bets) if b.get("status") == "void"]


def by_market_family(bets, market_family):
    return [b for b in bets if b.get("marketFamily") == market_family]


def by_game(bets, game_id):
    return [b for b in bets if b.get("gameId") == game_id]


def linked_to_snapshot(bets, snapshot_id=None):
    if snapshot_id is not None:
        return [b for b in bets if b.get("snapshotId") == snapshot_id]
    return [b for b in bets if b.get("snapshotId")]


def linked_to_recommendation(bets, recommendation_id=None):
    if recommendation_id is not None:
        return [b for b in bets if b.get("recommendationId") == recommendation_id]
    return [b for b in bets if b.get("recommendationId")]


def manual_without_model_support(bets):
    """
    A MANUAL-sourced bet with no genuine model backing: modelSupported is
    not True AND there is no modelEvaluationId to fall back on (a
    pre-milestone row may have the link but not yet the modelSupported
    field -- never treated as "no model support" just because the newer
    field is null on an old row).
    """
    return [
        b for b in bets
        if b.get("source") == "MANUAL" and not b.get("modelEvaluationId") and b.get("modelSupported") is not True
    ]


def active(bets):
    """Excludes CANCELLED rows -- the normal filter to apply before any ROI/exposure aggregation."""
    return [b for b in bets if (b.get("recordStatus") or "ACTIVE") != "CANCELLED"]


def todays_card(bets, date):
    """
    Every ACTIVE bet placed on `date`, plus totals -- the "today's
    complete placed-bet card" (requirement 9). Never computes a win/loss
    -- only what was staked and what could return, exactly like a
    per-bet receipt.
    """
    day_bets = active(by_date(bets, date))
    total_staked = round(sum(b.get("stake") or 0 for b in day_bets), 2)
    by_family = defaultdict(lambda: {"count": 0, "staked": 0.0})
    for b in day_bets:
        fam = b.get("marketFamily") or "UNKNOWN"
        by_family[fam]["count"] += 1
        by_family[fam]["staked"] = round(by_family[fam]["staked"] + (b.get("stake") or 0), 2)
    return {
        "date": date,
        "betCount": len(day_bets),
        "totalStaked": total_staked,
        "pendingCount": sum(1 for b in day_bets if b.get("status") == "pending"),
        "settledCount": sum(1 for b in day_bets if b.get("status") == "settled"),
        "voidCount": sum(1 for b in day_bets if b.get("status") == "void"),
        "byMarketFamily": dict(by_family),
        "bets": day_bets,
    }


# ---------------------------------------------------------------------------
# Research Query Surface (Part 6 of the MLB Market Research Corpus &
# Frictionless Manual Logging milestone): read-only functions over the
# broader corpus (MarketObservation/Market/Game/Recommendation/Settlement/
# Postmortem), alongside the bet-focused functions above. Every function
# here is a pure filter/aggregate over already-loaded lists -- exactly the
# same convention as the PlacedBet functions above -- so
# scripts/edgelab/query_research.py can wrap them as a read-only CLI.
# Nothing in this section ever calls a write function from
# lib.edgelab.bets/storage/postmortems.
# ---------------------------------------------------------------------------

def observed_markets_for_game(observations, game_id):
    """Every observed market (all capture ticks) for one gameId -- Part 6: 'show every observed market for a given game.'"""
    return [o for o in observations if o.get("gameId") == game_id]


def alternate_thresholds(observations, market_family, market_horizon=None):
    """Distinct (marketTicker, threshold) pairs for one family/date -- Part 6: 'show all alternate team-total thresholds for a date.'"""
    seen = {}
    for o in observations:
        if o.get("marketFamily") != market_family:
            continue
        if market_horizon and o.get("marketHorizon") != market_horizon:
            continue
        ticker = o.get("marketTicker")
        if ticker not in seen:
            seen[ticker] = {"marketTicker": ticker, "threshold": o.get("threshold"), "team": o.get("team"), "player": o.get("player")}
    return sorted(seen.values(), key=lambda r: (r["threshold"] if r["threshold"] is not None else float("-inf"), r["marketTicker"]))


def pitcher_strikeout_closings(observations, settlements, market_family="pitcher_strikeouts"):
    """Every pitcher-strikeout market ticker with its closing settlement result -- Part 6."""
    settlement_by_ticker = {s["marketTicker"]: s for s in settlements}
    tickers = sorted({o["marketTicker"] for o in observations if o.get("marketFamily") == market_family})
    out = []
    for ticker in tickers:
        s = settlement_by_ticker.get(ticker)
        out.append({
            "marketTicker": ticker,
            "settlementStatus": s.get("settlementStatus") if s else "SETTLEMENT_UNRESOLVED",
            "result": s.get("result") if s else None,
            "unavailableReason": s.get("unavailableReason") if s else "not_yet_settled",
        })
    return out


def checkpoint_price_comparison(observations, market_ticker):
    """
    For one exact ticker: first observed / lineup-confirmed / closing
    prices side by side -- Part 6: 'compare first observed, lineup-
    confirmed, and closing prices.' Any checkpoint never observed is null,
    never guessed.
    """
    rows = sorted((o for o in observations if o.get("marketTicker") == market_ticker), key=lambda o: o.get("capturedAt") or "")
    by_checkpoint = {}
    for o in rows:
        cp = o.get("checkpoint")
        if cp and cp not in by_checkpoint:
            by_checkpoint[cp] = o
    closing = next((o for o in reversed(rows) if o.get("isClosingCandidate")), None)
    return {
        "marketTicker": market_ticker,
        "firstObserved": by_checkpoint.get("FIRST_DAILY"),
        "lineupConfirmed": by_checkpoint.get("LINEUP_CONFIRMATION"),
        "closing": closing,
    }


def observed_never_recommended(observations, recommendations):
    """Part 6: 'show markets observed but never recommended.'"""
    recommended_tickers = {r["marketTicker"] for r in recommendations if r.get("marketTicker")}
    observed_tickers = {o["marketTicker"] for o in observations}
    return sorted(observed_tickers - recommended_tickers)


def recommended_not_placed(recommendations, bets):
    """Part 6: 'show markets recommended but not placed.' Uses Recommendation.betPlaced, never inferred from the bets ledger alone."""
    placed_tickers = {b["marketTicker"] for b in active(bets) if b.get("marketTicker")}
    surfaced = {"WATCH", "RECOMMENDED", "RECOMMENDED_NOT_BET", "BET_PLACED"}
    return [
        r for r in recommendations
        if r.get("status") in surfaced and not r.get("betPlaced") and r.get("marketTicker") not in placed_tickers
    ]


def manual_bets_without_slate(bets, games):
    """
    Part 6: 'show manually placed bets without a production slate' -- a
    bet whose gameId has no corresponding Game dimension row for that
    date (i.e. the production slate never ran/observed that game at all,
    so this bet's only evidence is the manual import + linkage).
    """
    known_game_ids = {g["gameId"] for g in games if g.get("gameId")}
    return [b for b in active(bets) if b.get("gameId") and b["gameId"] not in known_game_ids]


def performance_by_family_all_observed(settlements, bets):
    """
    Part 6: 'show performance by market family across all observed
    markets' -- hypothetical returns for EVERY settled market (not only
    placed ones), broken out by family, alongside the real placed-bet P/L
    for comparison.
    """
    by_family = defaultdict(lambda: {
        "marketsSettled": 0, "hypotheticalReturnSum": 0.0,
        "betsPlaced": 0, "realizedPnl": 0.0,
    })
    bets_by_ticker = defaultdict(list)
    for b in active(bets):
        if b.get("marketTicker"):
            bets_by_ticker[b["marketTicker"]].append(b)

    for s in settlements:
        if s.get("settlementStatus") != "SETTLED":
            continue
        fam = s.get("marketFamily") or "UNKNOWN"
        stats = by_family[fam]
        stats["marketsSettled"] += 1
        checkpoints_returns = [
            c["hypotheticalYesReturn"] for c in (s.get("hypotheticalReturnsByCheckpoint") or [])
            if c.get("hypotheticalYesReturn") is not None
        ]
        if checkpoints_returns:
            stats["hypotheticalReturnSum"] = round(stats["hypotheticalReturnSum"] + checkpoints_returns[-1], 4)
        for bet in bets_by_ticker.get(s["marketTicker"], []):
            stats["betsPlaced"] += 1
            stats["realizedPnl"] = round(stats["realizedPnl"] + (bet.get("netProfitLoss") or 0), 2)
    return dict(by_family)


def market_corpus_capture_for_bet(bet, research_runs_by_id=None):
    """Part 6: 'show the market-corpus capture linked to a given bet.' Reads the bet's own marketObservationLinkage; never re-derives it."""
    linkage = bet.get("marketObservationLinkage") or {}
    run_id = linkage.get("marketCorpusRunId")
    result = dict(linkage)
    if run_id and research_runs_by_id:
        result["captureRun"] = research_runs_by_id.get(run_id)
    return result


def postmortem_for_date(postmortems_by_date, game_date):
    """Part 6: 'show the postmortem linked to a given date.' postmortems_by_date: {gameDate: current-revision-postmortem-dict}."""
    return postmortems_by_date.get(game_date)


def postmortem_for_bet(bet_id, postmortems):
    """Part 6: 'show the postmortem linked to a given bet.' postmortems: list of current-revision postmortem dicts."""
    for pm in postmortems:
        if bet_id in (pm.get("linkedBetIds") or []):
            return pm
    return None


# ---------------------------------------------------------------------------
# Full-Universe Research Engine: read-only analysis over EVERY settled
# OBSERVED market -- never recommended, recommended-but-not-bet, and
# actually-bet alike -- not only the markets a bet was placed on. Built by
# joining Settlement (hypothetical returns), MarketObservation (descriptive
# market attributes), ModelEvaluation (fair probability/edge/confidence/
# tags), Recommendation (decision status) and the canonical PlacedBet ledger
# (real P/L) purely by marketTicker/gameId. Every function below is a pure
# filter/aggregate over already-loaded lists -- the same convention as every
# other function in this module -- so scripts/edgelab/query_research.py's
# `research-query` subcommand can wrap build_research_rows +
# filter_research_rows + aggregate_research_rows as a read-only CLI.
# Hypothetical research performance (this section), recommendation
# performance (recommendationBreakdown), and actual user betting P/L
# (actualBetPerformance) are always kept in separate fields -- never
# blended into one number.
# ---------------------------------------------------------------------------

# The single checkpoint used as a market's STANDARDIZED pregame executable
# price for hypothetical research ROI, in priority order: the official
# CLOSING quote (lib.edgelab.checkpoints.select_closing_quote -- the final
# valid pre-suspension/pre-first-pitch tradable quote) first, else the
# latest standardized pregame checkpoint actually captured. POST_START is
# never eligible -- it is by definition captured after first pitch, never a
# pregame executable price. INTERMEDIATE is also never eligible -- it is an
# unclassified recurring-poll tick with no fixed distance-from-start
# meaning, so it can't be compared consistently across markets the way the
# other checkpoints can. This ordering is the ONE definition of "the
# standardized hypothetical price" used everywhere in this section --
# never redefined per query.
_STANDARDIZED_PRICE_CHECKPOINT_PRIORITY = (
    "CLOSING", "T_MINUS_5", "T_MINUS_15", "T_MINUS_30", "T_MINUS_60", "T_MINUS_90",
    "LINEUP_CONFIRMATION", "FIRST_DAILY",
)

# A model-fair-probability vs market-implied-probability gap (percentage
# points, both 0-100 scale) smaller than this is treated as "the model
# agrees with the market" rather than a genuine disagreement -- filters out
# rounding-level noise, not a claim about betting significance.
_DISAGREEMENT_EPSILON_PCT = 0.5


def standardized_pregame_price(settlement):
    """
    The one checkpoint entry from Settlement.hypotheticalReturnsByCheckpoint
    to use as this market's standardized pregame executable price -- see
    _STANDARDIZED_PRICE_CHECKPOINT_PRIORITY. Returns None (never guessed or
    interpolated) when no eligible checkpoint was ever captured for this
    market, e.g. a market first observed after the game already started, or
    with only an INTERMEDIATE tick.
    """
    if not settlement:
        return None
    by_checkpoint = {
        c["checkpoint"]: c for c in (settlement.get("hypotheticalReturnsByCheckpoint") or [])
        if c.get("checkpoint") in _STANDARDIZED_PRICE_CHECKPOINT_PRIORITY
    }
    for checkpoint in _STANDARDIZED_PRICE_CHECKPOINT_PRIORITY:
        if checkpoint in by_checkpoint:
            return by_checkpoint[checkpoint]
    return None


def _disagreement_label(model_fair_probability, market_implied_probability):
    if model_fair_probability is None or market_implied_probability is None:
        return None
    diff = model_fair_probability - market_implied_probability
    if diff > _DISAGREEMENT_EPSILON_PCT:
        return "MODEL_HIGHER"
    if diff < -_DISAGREEMENT_EPSILON_PCT:
        return "MODEL_LOWER"
    return "AGREE"


def build_research_rows(observations, settlements, evaluations=None, recommendations=None, bets=None, games=None):
    """
    One row per observed marketTicker -- the FULL observed-market
    population (never-recommended, recommended-but-not-bet, and
    actually-bet markets alike), joined by marketTicker (and gameId for the
    Game dimension) against every canonical source. A ticker with no
    Settlement row at all is still included (settlementStatus=
    "NOT_SETTLED") -- this never silently drops an observed market instead
    of reporting it as a gap.

    evaluations/recommendations/bets/games each default to None, meaning
    "this source was not supplied" -- distinct from an explicitly empty
    list ("supplied, but no rows for this population"). Fields that can
    only be trusted when a source was actually checked (wasRecommended,
    wasPlaced) stay None rather than being guessed False when that source
    was never loaded at all.
    """
    evaluations_loaded = evaluations is not None
    recommendations_loaded = recommendations is not None
    bets_loaded = bets is not None
    evaluations = evaluations or []
    recommendations = recommendations or []
    bets = bets or []
    games = games or []

    game_dates_by_id = {g["gameId"]: g.get("gameDate") for g in games if g.get("gameId")}
    settlement_by_ticker = {s["marketTicker"]: s for s in settlements}

    evaluations_by_ticker = defaultdict(list)
    for e in evaluations:
        if e.get("marketTicker"):
            evaluations_by_ticker[e["marketTicker"]].append(e)

    recommendations_by_ticker = defaultdict(list)
    for r in recommendations:
        if r.get("marketTicker"):
            recommendations_by_ticker[r["marketTicker"]].append(r)

    bets_by_ticker = defaultdict(list)
    for b in active(bets):
        if b.get("marketTicker"):
            bets_by_ticker[b["marketTicker"]].append(b)

    # One observation per ticker for display attributes (family/threshold/
    # team/player never change tick-to-tick for the same ticker) -- prefer
    # the most-recently-captured tick when several exist.
    observation_by_ticker = {}
    for o in observations:
        ticker = o.get("marketTicker")
        if not ticker:
            continue
        existing = observation_by_ticker.get(ticker)
        if existing is None or (o.get("capturedAt") or "") > (existing.get("capturedAt") or ""):
            observation_by_ticker[ticker] = o

    rows = []
    for ticker, obs in observation_by_ticker.items():
        settlement = settlement_by_ticker.get(ticker)
        game_id = obs.get("gameId")
        evals_for_ticker = evaluations_by_ticker.get(ticker, [])
        evaluation = evals_for_ticker[-1] if evals_for_ticker else None
        recs_for_ticker = recommendations_by_ticker.get(ticker, [])
        recommendation = recs_for_ticker[-1] if recs_for_ticker else None
        bets_for_ticker = bets_by_ticker.get(ticker, [])
        bet = bets_for_ticker[0] if bets_for_ticker else None

        standardized_price = standardized_pregame_price(settlement)

        was_recommended = (settlement or {}).get("wasRecommended")
        if was_recommended is None and recommendations_loaded:
            was_recommended = was_market_ever_recommended(recs_for_ticker)

        was_placed = (settlement or {}).get("wasPlaced")
        if was_placed is None and bets_loaded:
            was_placed = bool(bets_for_ticker)

        model_fair_probability = (evaluation or {}).get("modelFairProbability")
        market_implied_probability = (evaluation or {}).get("marketImpliedProbability")

        thesis_tags = []
        if evaluation and evaluation.get("thesisTags"):
            thesis_tags = evaluation["thesisTags"]
        elif bet and bet.get("thesisTags"):
            thesis_tags = bet["thesisTags"]

        rows.append({
            "marketTicker": ticker,
            "gameId": game_id,
            "gameDate": game_dates_by_id.get(game_id) or (obs.get("scheduledStart") or "")[:10] or None,
            "marketFamily": obs.get("marketFamily"),
            "marketHorizon": obs.get("marketHorizon"),
            "threshold": obs.get("threshold"),
            "team": obs.get("team"),
            "player": obs.get("player"),
            "comparisonOperator": obs.get("comparisonOperator"),
            "side": (bet or {}).get("side") or (evaluation or {}).get("side"),
            "settlementStatus": (settlement or {}).get("settlementStatus") or "NOT_SETTLED",
            "settlementResult": (settlement or {}).get("result"),
            "unavailableReason": (settlement or {}).get("unavailableReason"),
            "standardizedCheckpoint": (standardized_price or {}).get("checkpoint"),
            "standardizedYesPrice": (standardized_price or {}).get("yesPrice"),
            "hypotheticalYesReturn": (standardized_price or {}).get("hypotheticalYesReturn"),
            "wasRecommended": was_recommended if (evaluations_loaded or recommendations_loaded or settlement) else None,
            "recommendationStatus": (recommendation or {}).get("status"),
            "passReason": (recommendation or {}).get("passReason"),
            "wasPlaced": was_placed,
            "betId": bet.get("betId") if bet else None,
            "betSide": bet.get("side") if bet else None,
            "betStake": bet.get("stake") if bet else None,
            "betEntryPrice": bet.get("entryPrice") if bet else None,
            "betResult": bet.get("result") if bet else None,
            "betNetProfitLoss": bet.get("netProfitLoss") if bet else None,
            "betClv": bet.get("clv") if bet else None,
            "betClvStatus": ("VALID" if bet.get("clv") is not None else "UNAVAILABLE") if bet else None,
            "evaluationStatus": (evaluation or {}).get("evaluationStatus"),
            "modelFairProbability": model_fair_probability,
            "marketImpliedProbability": market_implied_probability,
            "estimatedEdge": (evaluation or {}).get("estimatedEdge"),
            "confidence": (evaluation or {}).get("confidence"),
            "thesisTags": thesis_tags,
            "modelVsMarketDisagreement": _disagreement_label(model_fair_probability, market_implied_probability),
        })
    return rows


def filter_research_rows(
    rows, *, market_family=None, market_horizon=None, threshold=None,
    min_threshold=None, max_threshold=None, side=None,
    min_price=None, max_price=None,
    min_fair_probability=None, max_fair_probability=None,
    min_edge=None, max_edge=None, confidence=None,
    settlement_status=None, recommendation_status=None,
    was_recommended=None, was_placed=None, disagreement=None,
    thesis_tag=None, game_id=None, date=None, start_date=None, end_date=None,
    clv_available=None,
):
    """
    Pure AND-filter over build_research_rows() output. Every keyword
    defaults to None ("no filter applied") -- only the filters a caller
    actually passes narrow the population. `threshold` is an exact match
    (a specific rung); min_threshold/max_threshold instead select a range
    of rungs. `date`/`start_date`/`end_date` filter on gameDate.
    """
    if market_family is not None:
        rows = [r for r in rows if r["marketFamily"] == market_family]
    if market_horizon is not None:
        rows = [r for r in rows if r["marketHorizon"] == market_horizon]
    if threshold is not None:
        rows = [r for r in rows if r["threshold"] == threshold]
    if min_threshold is not None:
        rows = [r for r in rows if r["threshold"] is not None and r["threshold"] >= min_threshold]
    if max_threshold is not None:
        rows = [r for r in rows if r["threshold"] is not None and r["threshold"] <= max_threshold]
    if side is not None:
        rows = [r for r in rows if r["side"] == side]
    if min_price is not None:
        rows = [r for r in rows if r["standardizedYesPrice"] is not None and r["standardizedYesPrice"] >= min_price]
    if max_price is not None:
        rows = [r for r in rows if r["standardizedYesPrice"] is not None and r["standardizedYesPrice"] <= max_price]
    if min_fair_probability is not None:
        rows = [r for r in rows if r["modelFairProbability"] is not None and r["modelFairProbability"] >= min_fair_probability]
    if max_fair_probability is not None:
        rows = [r for r in rows if r["modelFairProbability"] is not None and r["modelFairProbability"] <= max_fair_probability]
    if min_edge is not None:
        rows = [r for r in rows if r["estimatedEdge"] is not None and r["estimatedEdge"] >= min_edge]
    if max_edge is not None:
        rows = [r for r in rows if r["estimatedEdge"] is not None and r["estimatedEdge"] <= max_edge]
    if confidence is not None:
        rows = [r for r in rows if r["confidence"] == confidence]
    if settlement_status is not None:
        rows = [r for r in rows if r["settlementStatus"] == settlement_status]
    if recommendation_status is not None:
        rows = [r for r in rows if r["recommendationStatus"] == recommendation_status]
    if was_recommended is not None:
        rows = [r for r in rows if r["wasRecommended"] == was_recommended]
    if was_placed is not None:
        rows = [r for r in rows if r["wasPlaced"] == was_placed]
    if disagreement is not None:
        rows = [r for r in rows if r["modelVsMarketDisagreement"] == disagreement]
    if thesis_tag is not None:
        rows = [r for r in rows if thesis_tag in (r["thesisTags"] or [])]
    if game_id is not None:
        rows = [r for r in rows if r["gameId"] == game_id]
    if date is not None:
        rows = [r for r in rows if r["gameDate"] == date]
    if start_date is not None:
        rows = [r for r in rows if (r["gameDate"] or "") >= start_date]
    if end_date is not None:
        rows = [r for r in rows if (r["gameDate"] or "") <= end_date]
    if clv_available is not None:
        rows = [r for r in rows if (r["betClvStatus"] == "VALID") == clv_available]
    return rows


def aggregate_research_rows(rows, stake_unit=1.0):
    """
    Aggregate metrics over a (typically already-filtered) set of
    build_research_rows() rows. Hypothetical research performance
    (everything under the top level), recommendation performance
    (recommendationBreakdown), and actual user betting P/L
    (actualBetPerformance) are always reported as separate sections --
    never blended into one number.

    Hypothetical ROI is derived exclusively from
    standardizedYesPrice/hypotheticalYesReturn (a real archived pregame
    executable checkpoint price -- see standardized_pregame_price();
    never a post-start price), and is computed over ONLY the rows that
    actually have one (settled AND a standardized price was captured) --
    a VOID/unresolved/never-priced market contributes to the observed/
    settlement counts below but never to the ROI math itself, and is
    never treated as a loss.

    sampleSizeStatus reuses lib.edgelab.calibration's exact three-tier
    convention (n<20 INSUFFICIENT_SAMPLE, 20<=n<100 DESCRIPTIVE_ONLY,
    n>=100 CALIBRATED) -- the same statistical-significance bar already
    used everywhere else in this codebase, not a new one invented here.
    """
    observed_count = len(rows)
    settled_rows = [r for r in rows if r["settlementStatus"] == "SETTLED"]
    void_count = sum(1 for r in rows if r["settlementStatus"] == "VOID")
    unresolved_count = sum(1 for r in rows if r["settlementStatus"] in ("SETTLEMENT_UNRESOLVED", "UNAVAILABLE"))
    not_settled_count = sum(1 for r in rows if r["settlementStatus"] == "NOT_SETTLED")

    priced_rows = [r for r in settled_rows if r["hypotheticalYesReturn"] is not None]
    wins = sum(1 for r in priced_rows if r["settlementResult"] == "YES")
    losses = sum(1 for r in priced_rows if r["settlementResult"] == "NO")

    n = len(priced_rows)
    standardized_stake = round(n * stake_unit, 2)
    net_pnl = round(sum(r["hypotheticalYesReturn"] for r in priced_rows) * stake_unit, 4)
    gross_return = round(net_pnl + standardized_stake, 4)
    roi_pct = round(100.0 * net_pnl / standardized_stake, 4) if standardized_stake else None

    edge_values = [r["estimatedEdge"] for r in rows if r["estimatedEdge"] is not None]
    avg_edge = round(sum(edge_values) / len(edge_values), 4) if edge_values else None

    brier_rows = [
        r for r in rows
        if r["evaluationStatus"] in ("EVALUATED", "PARTIAL_EVALUATION")
        and r["modelFairProbability"] is not None
        and r["settlementResult"] in ("YES", "NO")
    ]
    brier_score = None
    if brier_rows:
        squared_errors = [
            ((r["modelFairProbability"] / 100.0) - (1.0 if r["settlementResult"] == "YES" else 0.0)) ** 2
            for r in brier_rows
        ]
        brier_score = round(sum(squared_errors) / len(squared_errors), 6)

    bet_rows = [r for r in rows if r["wasPlaced"]]
    clv_valid_rows = [r for r in bet_rows if r["betClvStatus"] == "VALID"]
    clv_coverage = round(len(clv_valid_rows) / len(bet_rows), 4) if bet_rows else None
    avg_clv = round(sum(r["betClv"] for r in clv_valid_rows) / len(clv_valid_rows), 4) if clv_valid_rows else None

    settled_bet_rows = [r for r in bet_rows if r["betResult"] in ("WIN", "LOSS", "PUSH", "VOID")]
    bet_stake_sum = round(sum(r["betStake"] or 0 for r in settled_bet_rows), 2)
    bet_net_pnl_sum = round(sum(r["betNetProfitLoss"] or 0 for r in settled_bet_rows if r["betNetProfitLoss"] is not None), 2)
    bet_roi_pct = round(100.0 * bet_net_pnl_sum / bet_stake_sum, 4) if bet_stake_sum else None

    recommendation_breakdown = {
        "neverRecommended": sum(1 for r in rows if r["wasRecommended"] is False),
        "recommendedNotBet": sum(1 for r in rows if r["wasRecommended"] is True and not r["wasPlaced"]),
        "betPlaced": sum(1 for r in rows if r["wasPlaced"]),
        "recommendationStatusUnknown": sum(1 for r in rows if r["wasRecommended"] is None),
    }

    sample_size_status = calibration_status(n)

    return {
        "observedCount": observed_count,
        "sampleSize": n,
        "sampleSizeStatus": sample_size_status,
        "smallSampleWarning": sample_size_status == "INSUFFICIENT_SAMPLE",
        "smallSampleMessage": (
            f"Only {n} settled+priced market(s) in this slice -- below the n<20 significance floor; "
            "treat any ROI/edge/CLV figure here as noise, not evidence."
        ) if sample_size_status == "INSUFFICIENT_SAMPLE" else None,
        "wins": wins,
        "losses": losses,
        "void": void_count,
        "unresolved": unresolved_count,
        "notSettled": not_settled_count,
        "standardizedHypotheticalStake": standardized_stake,
        "hypotheticalReturn": gross_return,
        "hypotheticalNetPnl": net_pnl,
        "hypotheticalRoiPct": roi_pct,
        "averageModelEdge": avg_edge,
        "edgeSampleSize": len(edge_values),
        "brierScore": brier_score,
        "brierSampleSize": len(brier_rows),
        "clvCoverage": clv_coverage,
        "averageClv": avg_clv,
        "clvEligibleCount": len(bet_rows),
        "recommendationBreakdown": recommendation_breakdown,
        "actualBetPerformance": {
            "betCount": len(bet_rows),
            "settledBetCount": len(settled_bet_rows),
            "stake": bet_stake_sum,
            "netProfitLoss": bet_net_pnl_sum,
            "roiPct": bet_roi_pct,
        },
    }


def aggregate_research_rows_by(rows, group_by_field, stake_unit=1.0):
    """
    Breakdown of aggregate_research_rows() metrics, one entry per distinct
    value of `group_by_field` (e.g. "marketFamily", "confidence",
    "modelVsMarketDisagreement") -- the general form of the family-by-family
    breakdown performance_by_family_all_observed() already provides for
    settlements alone, extended to any field on a research row and to the
    full metric set above.
    """
    groups = defaultdict(list)
    for r in rows:
        groups[r.get(group_by_field)].append(r)
    return {
        str(key): aggregate_research_rows(group_rows, stake_unit=stake_unit)
        for key, group_rows in groups.items()
    }


def render_human(bets, *, title="Bets"):
    """Compact human-readable table, for a chat/terminal reader rather than a script."""
    lines = [f"# {title}", ""]
    if not bets:
        lines.append("(none)")
        return "\n".join(lines) + "\n"
    lines.append("| betId | date | ticker | selection | side | stake | entry | status | result |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for b in bets:
        lines.append(
            f"| {b.get('betId', '')[:12]} | {_entry_date(b) or ''} | {b.get('marketTicker', '')} | "
            f"{b.get('selection', '')} | {b.get('side') or ''} | {b.get('stake')} | {b.get('entryPrice')} | "
            f"{b.get('status', '')} | {b.get('result') or ''} |"
        )
    return "\n".join(lines) + "\n"
