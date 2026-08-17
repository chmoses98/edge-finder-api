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

modelEvaluationId (Phase 2 Milestone 3, docs/EDGELAB_MODEL_EVALUATION.md):
computed here via the exact same ids.build_model_evaluation_id(key, ticker)
call, over the exact same key each row already uses for its own
recommendationId (source_run_key/market_key for pipeline rows, date/ticker
for extension rows) -- lib.edgelab.model_evaluation independently
recomputes the identical ID from the identical key when it builds the
actual ModelEvaluation record, so the two ledgers link by matching
deterministic IDs with no lookup, no join table, and no ordering
dependency between which module runs first.
"""

import os

from lib.edgelab import ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.pipeline_artifacts import read_stage_artifact, stage_artifact_exists
from lib.rules_config import load_rules_config, RULES_PATH


# Ticker-resolution status vocabulary (market-integrity milestone): the
# previously-overloaded marketTicker=null (and previously-unverified
# marketTicker!=null) split into a specific, non-fabricated reason -- see
# classify_ticker_resolution().
TICKER_RESOLVED = "RESOLVED"
TICKER_NOT_APPLICABLE = "NOT_APPLICABLE"
TICKER_NOT_COMPUTED = "NOT_COMPUTED"
TICKER_PARSER_UNRESOLVED = "PARSER_UNRESOLVED"
TICKER_AMBIGUOUS = "AMBIGUOUS"

# game_total/inning_total tickers encode a STRICT integer line N ("total
# > N") -- the sportsbook-natural equivalent is "Over N.5" (see
# lib/research/market_taxonomy.py's _total_line_from_suffix docstring:
# "no half-run lines on this series"). team_total's own suffix
# convention already encodes N-0.5 directly
# (_team_and_margin_from_suffix), so it's used verbatim. Any family not
# listed here (including every literal Kalshi N+ count market -- player
# props) gets no label from format_threshold_label(): those already have
# their own correct "N+" wording (lib.research.player_prop_parser) and
# must never be relabeled here.
_RAW_INTEGER_TOTAL_FAMILIES = frozenset({"game_total", "inning_total"})
_NATURAL_HALF_RUN_FAMILIES = frozenset({"team_total"})

# market_name (config/rules.json's own naming, e.g. row.get("market")) ->
# (marketFamily, direction) for the pipeline-derived rows that carry a
# totals-shaped threshold. Deliberately only the markets REQUIRED_MARKETS
# (scripts/build_market_ledger.py) actually emits with an Over-side name
# today -- there is no "Game_Total_Under"/"TT_*_Under" market name to map.
_PIPELINE_THRESHOLD_MARKETS = {
    "Game_Total": ("game_total", "OVER"),
    "TT_Away_Over": ("team_total", "OVER"),
    "TT_Home_Over": ("team_total", "OVER"),
}


def format_threshold_label(market_family, threshold, direction):
    """
    Pure. Natural sportsbook-style label for a totals-shaped market, e.g.
    "Over 3.5", "Under 7.5", "Team Total Over 4.5" -- built entirely from
    the market's own already-archived family/threshold/direction, never
    a guess. Returns None for anything without a safe, verified
    convention here (unknown family, missing threshold, or a
    direction other than OVER/UNDER -- e.g. AT_LEAST/YES/NO/None, which
    covers every literal Kalshi N+ count market and every non-totals
    market). Naively displaying the raw archived integer for
    game_total/inning_total (or worse, an "N+ runs" label borrowed from
    the unrelated prop convention) is exactly the off-by-one mislabeling
    this function exists to prevent.
    """
    if threshold is None or direction not in ("OVER", "UNDER"):
        return None
    if market_family in _RAW_INTEGER_TOTAL_FAMILIES:
        natural = threshold + 0.5
    elif market_family in _NATURAL_HALF_RUN_FAMILIES:
        natural = threshold
    else:
        return None
    word = "Over" if direction == "OVER" else "Under"
    if market_family == "team_total":
        return f"Team Total {word} {natural:g}"
    return f"{word} {natural:g}"


def _archived_game_id_by_ticker(observations):
    """{marketTicker: gameId} built once per date from real, already-
    archived MarketObservation rows -- the ground truth used to verify a
    pipeline-claimed ticker actually exists, and belongs to the game it
    claims. Never re-parsed/re-derived -- read straight off the archive."""
    lookup = {}
    for obs in observations:
        ticker = obs.get("marketTicker")
        if ticker and ticker not in lookup:
            lookup[ticker] = obs.get("gameId")
    return lookup


def classify_ticker_resolution(row, game_id, archived_game_id_by_ticker):
    """
    Pure. Cross-checks a marketLedger row's claimed ticker against the
    date's actually-archived MarketObservation corpus before it is ever
    trusted as "the exact archived Kalshi ticker" -- never guesses, never
    silently trusts an unverified string, never picks among multiple
    candidates. Does NOT change marketTicker itself (that field keeps
    its existing value/behavior for every downstream consumer); this is
    purely an additional, non-fabricated diagnostic.

    Returns one of TICKER_RESOLVED / TICKER_AMBIGUOUS /
    TICKER_PARSER_UNRESOLVED / TICKER_NOT_COMPUTED / TICKER_NOT_APPLICABLE.
    """
    ticker = row.get("ticker") or row.get("marketTicker")
    if ticker:
        archived_game_id = archived_game_id_by_ticker.get(ticker)
        if archived_game_id is None:
            return TICKER_PARSER_UNRESOLVED
        if game_id is not None and archived_game_id != game_id:
            return TICKER_AMBIGUOUS
        return TICKER_RESOLVED

    row_status = row.get("status")
    if row_status == "Missing Data":
        missing = " ".join(row.get("missingFields") or []).lower()
        if "ticker" in missing or "kalshi" in missing:
            return TICKER_NOT_COMPUTED
        return TICKER_NOT_APPLICABLE
    if row_status == "Evaluation Failed":
        err = (row.get("evaluationError") or "").lower()
        if "pars" in err or "ticker" in err:
            return TICKER_PARSER_UNRESOLVED
        return TICKER_NOT_APPLICABLE

    # kalshiPrice is only ever populated once scripts/build_market_ledger.py
    # has actually priced a real, tradable market for this row (confirmed
    # against real data/pipeline/*/recommendations.json artifacts: every
    # Accepted/Rejected row with kalshiPrice set corresponds to a market
    # that genuinely exists and was evaluated) -- so a row that HAS a
    # price but no ticker string proves a real ticker existed and was
    # known internally at evaluation time, it just wasn't threaded into
    # this row's own ticker/marketTicker field (e.g. ML_Away/ML_Home,
    # F5_ML_Away/F5_ML_Home, NRFI, YRFI: unlike RL_Away/RL_Home and
    # Game_Total/TT_Away_Over/TT_Home_Over, their Rejected-branch
    # rejected_row() calls in scripts/build_market_ledger.py don't pass
    # `**identity(ticker, ...)`). NOT_APPLICABLE would wrongly claim no
    # market of this kind exists for this game; NOT_COMPUTED is the
    # accurate, non-fabricated state -- never guessed at the ticker
    # string itself, only at WHY it's missing. A row with no price at
    # all (e.g. RL_Away/RL_Home, unconditionally Rule-81-rejected before
    # any price is even fetched) keeps the pre-existing NOT_APPLICABLE
    # fallback below -- there is no positive evidence a ticker exists
    # for those.
    if row.get("kalshiPrice") is not None:
        return TICKER_NOT_COMPUTED
    return TICKER_NOT_APPLICABLE


def load_model_covered_series(rules_path=RULES_PATH):
    """Series tickers the 11-market model config can evaluate at all (config/rules.json's market_list)."""
    if not os.path.exists(rules_path):
        return frozenset()
    rules = load_rules_config(rules_path)
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


def _ev_per_dollar_for_row(row):
    """
    F5 Three-Way Pricing Correction milestone: contract_pricing()
    (scripts/build_market_ledger.py) computes expectedValuePerDollar for
    F5_ML_Away/F5_ML_Home, nested under f5ContractPricing (this row's
    own side). Copied here verbatim when present -- previously this
    field was hardcoded None for every row unconditionally, even though
    the upstream data already existed for roughly all F5 rows (see the
    identical helper in lib.edgelab.model_evaluation, which both
    modules build from the same marketLedger row). No other market
    family has a per-dollar EV concept computed anywhere in the
    pipeline, so evPerDollar correctly stays None for every non-F5 row,
    never fabricated.
    """
    return (row.get("f5ContractPricing") or {}).get("expectedValuePerDollar")


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


def build_recommendations_from_pipeline(date, run_id, placed_bet_tickers, observations=None,
                                         comparison_lookup=None):
    """
    placed_bet_tickers: {marketTicker: betId} for every currently-tracked
    placed bet -- a dict, not a bare set, so a matched row can link
    Recommendation.betId back to the actual bet (previously this was
    always left null even when betPlaced was true).

    observations (optional, defaults to none available): this date's
    already-archived MarketObservation rows, used only to cross-check
    each row's claimed ticker via classify_ticker_resolution() -- never
    consulted for anything that changes marketTicker/status/betPlaced
    themselves. Omitting it (or passing []) just means every ticker gets
    TICKER_PARSER_UNRESOLVED (nothing to verify against) rather than a
    crash -- existing callers that don't care about this diagnostic keep
    working unchanged.

    comparison_lookup (optional, MLB Model Expression Guardrails
    milestone): {marketTicker: [otherMarketTicker, ...]} -- the OTHER
    tickers lib.edgelab.market_comparison.build_comparisons() found in
    the same comparison cluster as this ticker (alternate horizons/
    instruments expressing the same underlying thesis), keyed by ticker
    so this function stays completely decoupled from the DuckDB session
    market_comparison.py itself requires -- the caller builds the lookup
    once (if it has a session available) and passes it in. Omitting it
    (the default) preserves the exact prior behavior: comparisonMarkets
    is always []. Never fabricated -- a ticker missing from the lookup
    (or an unresolved/None ticker) still gets [].

    Returns (records, warnings). Empty records + a warning if
    recommendations.json doesn't exist for this date (e.g. the slate
    pipeline hasn't run yet) -- never fabricated.
    """
    comparison_lookup = comparison_lookup or {}
    if not stage_artifact_exists("recommendations", date):
        return [], [f"no data/pipeline/{date}/recommendations.json artifact"]

    rec_env = read_stage_artifact("recommendations", date)
    source_run_key = rec_env["meta"]["createdAt"]
    games = (rec_env.get("data") or {}).get("games") or []
    archived_game_id_by_ticker = _archived_game_id_by_ticker(observations or [])

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
            # market_name is ALWAYS part of the key, even when a ticker
            # resolves: a two-sided single-ticker market (e.g. a run-line
            # spread) produces two marketLedger rows -- one per side
            # (RL_Away/RL_Home) -- that share the exact same Kalshi
            # ticker. Keying by ticker alone would collapse both sides'
            # distinct model evaluations onto one recommendationId/
            # modelEvaluationId, silently dropping one. Found via testing
            # against the real 2026-07-31 artifact (Milestone 3), not a
            # hypothetical -- see docs/EDGELAB_MODEL_EVALUATION.md.
            market_key = f"{ticker}:{market_name}" if ticker else f"{game_id}:{market_name}"

            ticker_resolution_status = classify_ticker_resolution(row, game_id, archived_game_id_by_ticker)
            threshold_family_direction = _PIPELINE_THRESHOLD_MARKETS.get(market_name)
            threshold_display = (
                format_threshold_label(threshold_family_direction[0], row.get("line"), threshold_family_direction[1])
                if threshold_family_direction else None
            )

            records.append({
                "schemaVersion": SCHEMA_VERSION,
                "recommendationId": ids.build_recommendation_id(source_run_key, market_key),
                "runId": run_id,
                "gameId": game_id,
                "sport": DEFAULT_SPORT,
                "platform": DEFAULT_PLATFORM,
                "marketTicker": ticker,
                "marketName": market_name,
                # Raw value as evaluated, never canonicalized here (see
                # lib.edgelab.market_family_mapping) -- the Kalshi series
                # ticker prefix when a ticker resolved, else the model
                # config's own market name (e.g. "NRFI", "F5_ML_Away").
                # MARKET_FAMILY_ALIASES already recognizes every one of
                # REQUIRED_MARKETS' market names as a raw spelling (they
                # reach it today via a different legacy path -- PlacedBet.
                # marketFamily), so this fallback fills a real gap with a
                # value the canonicalization layer already understands,
                # rather than leaving marketFamily null whenever a Rejected
                # row's ticker wasn't threaded through (the same root cause
                # tickerResolutionStatus=NOT_COMPUTED now flags above).
                "marketFamily": ticker.split("-", 1)[0] if ticker else market_name,
                "status": status,
                "modelEvaluationId": ids.build_model_evaluation_id(source_run_key, market_key),
                "modelFairProbability": row.get("modelProb"),
                "marketImpliedProbability": row.get("kalshiVF") or row.get("marketProbVF"),
                "estimatedEdge": row.get("calibratedEdgeVsExecutable") or row.get("edge"),
                "evPerDollar": _ev_per_dollar_for_row(row),
                "rankWithinGame": None,
                "priceCeiling": row.get("maxBetPrice"),
                "confidence": row.get("confidenceTier"),
                "passReason": pass_reason,
                "comparisonMarkets": comparison_lookup.get(ticker, []) if ticker else [],
                "betPlaced": has_bet,
                "betId": bet_id,
                "tickerResolutionStatus": ticker_resolution_status,
                "thresholdDisplay": threshold_display,
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
        threshold_display = format_threshold_label(
            obs.get("marketFamily"), obs.get("threshold"), obs.get("comparisonOperator"),
        )
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
            "modelEvaluationId": ids.build_model_evaluation_id(date, ticker),
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
            # Always RESOLVED: this row's marketTicker IS the literal,
            # already-archived observation's own ticker -- there is
            # nothing to cross-check it against, unlike a pipeline-
            # derived row whose ticker is a separate claim to verify.
            "tickerResolutionStatus": TICKER_RESOLVED,
            "thresholdDisplay": threshold_display,
            "createdAt": now,
            "updatedAt": now,
            "source": "market_universe_extension",
            "validationStatus": "valid",
            "provenance": dict(obs["provenance"], ingestedAt=now),
        })
    return extra
