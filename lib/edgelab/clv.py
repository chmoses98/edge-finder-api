"""
lib/edgelab/clv.py
=====================
CLV collector (Phase 1 section H). Builds ClvQuote history from the
MarketObservation time series already captured by
lib/edgelab/market_universe.py (no separate polling of its own -- the
"poll exact market tickers on a recurring schedule" requirement is
satisfied by ingest_market_observations.py's existing cadence; this
module PROJECTS observations into the CLV-focused store and computes
CLV, it does not fetch anything itself), and computes per-bet CLV using
the same reused formula as scripts/clv_from_snapshot.py (the repo's
existing "PRIMARY CLV source").

CLV formula (documented here, not reinvented):
    entry_implied   = the bet's own entryPrice (already a 0-1 implied probability)
    closing_implied = the executable ASK-side probability of the closing
                       quote for a YES bet, or (1 - closing YES bid) for a
                       NO bet (NO's own executable price) -- "the
                       executable side relevant to the bet", never the
                       midpoint.
    clvCents         = round((closing_implied - entry_implied) * 100, 2)
                       via lib.edgelab.clv_convention (POSITIVE_IS_GOOD_V1)
    probabilityClv   = clvCents / 100 (same number, 0-1 scale, for callers
                       that want a probability delta rather than "cents")
CANONICAL SIGN CONVENTION (corrected; see docs/EDGELAB_CLV_SIGN_AUDIT.md).
This function now delegates to lib.edgelab.clv_convention, the repository's
single source of truth:

    clvCents = (closing side-relevant implied - entry implied) * 100
    POSITIVE = entered CHEAPER than the close = GOOD.

It previously computed `entry - closing`, the exact negation, while
documenting a positive value as good -- so every consumer that read the
sign read it backwards. Historical rows written under the old convention
were migrated by
scripts/edgelab/migrate_clv_sign.py (recomputed from side/entry/closing,
never blanket-negated); the migration receipt records every changed row.
Output now carries clvConvention=POSITIVE_IS_GOOD_V1.

American odds fields are for display only, derived via
scripts/clv_from_snapshot.implied_to_american() -- the repo's existing
implied-probability -> American-odds conversion, reused rather than
reimplemented.
"""

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION
from lib.edgelab import clv_convention
from lib.edgelab import checkpoints
from lib.edgelab.checkpoints import classify_checkpoint, select_closing_quote
from scripts.clv_from_snapshot import implied_to_american

_STANDARD_CHECKPOINTS = {
    "FIRST_DAILY", "LINEUP_CONFIRMATION", "T_MINUS_90", "T_MINUS_60",
    "T_MINUS_30", "T_MINUS_15", "T_MINUS_5", "CLOSING",
}


def project_observations_to_clv_quotes(observations, placed_bet_tickers, run_id):
    """
    observations: MarketObservation dicts for one or more dates, already
    captured (see ingest_market_observations.py).
    placed_bet_tickers: {marketTicker: betId} for every currently-tracked
    placed bet -- every quote for these tickers is kept (high priority,
    "preserve every valid quote"), regardless of checkpoint. Every other
    market is kept only at a standardized checkpoint (never every raw
    tick -- that firehose already lives in MarketObservation).

    Returns a list of ClvQuote records. Does not decide which one is the
    closing quote across REPEATED calls (a market's full history may span
    multiple ingestion runs) -- call finalize_closing_quotes() once you
    have the full day's ClvQuote rows for a ticker.
    """
    now = ids.utc_now_iso()
    by_ticker = {}
    for obs in observations:
        by_ticker.setdefault(obs["marketTicker"], []).append(obs)

    quotes = []
    for ticker, obs_list in by_ticker.items():
        obs_list = sorted(obs_list, key=lambda o: o["capturedAt"])
        bet_id = placed_bet_tickers.get(ticker)
        scheduled_start = next((o.get("scheduledStart") for o in obs_list if o.get("scheduledStart")), None)

        for i, obs in enumerate(obs_list):
            checkpoint = obs.get("checkpoint") or classify_checkpoint(
                obs["capturedAt"], scheduled_start, is_first_of_day=(i == 0),
            )
            if not bet_id and checkpoint not in _STANDARD_CHECKPOINTS:
                continue
            quotes.append({
                "schemaVersion": SCHEMA_VERSION,
                "clvQuoteId": obs["marketObservationId"],
                "runId": run_id,
                "betId": bet_id,
                "marketTicker": ticker,
                "gameId": obs.get("gameId"),
                "capturedAt": obs["capturedAt"],
                "checkpoint": checkpoint,
                "yesBid": obs.get("yesBid"),
                "yesAsk": obs.get("yesAsk"),
                "noBid": obs.get("noBid"),
                "noAsk": obs.get("noAsk"),
                "lastPrice": obs.get("lastPrice"),
                # The market's own scheduled start, carried so closing-quote
                # coverage (distance from start) is computable from the quote
                # ALONE -- without it every consumer had to re-join back to
                # the observations partition, and none of them did, which is
                # how a 14-hour-old FIRST_DAILY quote came to be reported as
                # closing-line value. See docs/EDGELAB_CLOSING_QUOTE_POLICY.md.
                "scheduledStart": scheduled_start,
                # The unit the four price fields above are denominated in,
                # carried EXPLICITLY so no consumer has to guess (the 100x
                # CLV defect -- see _executable_closing_implied). A
                # MarketObservation's prices are 0-1 probabilities: since
                # W1-B2 lib/edgelab/market_universe.py copies them straight
                # from the snapshot's declared `*_dollars` fields.
                "priceUnit": clv_convention.UNIT_PROBABILITY,
                "marketStatus": obs.get("marketStatus"),
                "isClosingQuote": False,
                "createdAt": now,
                "source": "edgelab_clv_collector",
                "validationStatus": "valid",
                "provenance": dict(obs["provenance"], ingestedAt=now),
            })

    return quotes


def finalize_closing_quotes(clv_quotes, scheduled_start=None, actual_start=None):
    """
    Given the full set of ClvQuote rows for ONE market ticker, mark the
    one that qualifies as the official closing quote (see
    lib.edgelab.checkpoints.select_closing_quote). Returns a new list
    (does not mutate the input); at most one row has isClosingQuote=True.
    A market with no valid pre-suspension/pre-start candidate is returned
    unchanged -- never guesses a closing quote.
    """
    closing = select_closing_quote(clv_quotes, scheduled_start=scheduled_start, actual_start=actual_start)
    if closing is None:
        return list(clv_quotes)
    closing_id = closing["clvQuoteId"]
    return [
        dict(q, isClosingQuote=True) if q["clvQuoteId"] == closing_id else dict(q, isClosingQuote=False)
        for q in clv_quotes
    ]


def _executable_closing_implied(closing_quote, side):
    """
    The executable probability (0-1) a bettor on `side` would have faced
    at close: YES side -> yesAsk (what you'd pay to buy in); NO side ->
    the NO-side ask, derived from yesBid when a direct noAsk isn't
    available. Returns None (never a guess) if neither is present.

    UNIT SAFETY (the 100x CLV defect). This function used to divide
    every ClvQuote price by 100.0 unconditionally, i.e. it assumed the
    quote was in integer CENTS. That was true of archived rows only up
    to 2026-09-10. From 2026-09-11 the Kalshi snapshots this repository
    captures carry the `*_dollars` fixed-point fields, so
    lib/edgelab/market_universe.py writes MarketObservation -- and
    therefore project_observations_to_clv_quotes() writes ClvQuote --
    prices as 0-1 probabilities. The consumer was never told. A 0.66 NO
    ask became a 0.0066 closing probability and CLV came out ~100x too
    negative against a correctly-scaled entryPrice (entryPrice is
    independently corroborated by contractCost/contracts on every
    receipt-imported row, so the closing side is provably the wrong one).

    The fix is the rule lib/edgelab/price_units.py already states for
    every other money path: the unit is a property of the FIELD, never
    of the value. A ClvQuote now DECLARES its priceUnit, and a quote
    that does not declare one is UNRESOLVABLE rather than assumed --
    guessing is what produced the defect.
    """
    unit = closing_quote.get("priceUnit")
    if unit not in (clv_convention.UNIT_CENTS, clv_convention.UNIT_PROBABILITY):
        return None
    price = clv_convention.executable_price(closing_quote, side, unit)
    if price is None:
        return None
    return clv_convention.convert(price, unit, clv_convention.UNIT_PROBABILITY)


def compute_clv_for_bet(bet, clv_quotes_for_ticker, scheduled_start=None):
    """
    Returns a dict: either the full CLV computation, or
    {"clvStatus": "UNAVAILABLE", "unavailableReason": "..."} -- never a
    fabricated number. clv_quotes_for_ticker should be every ClvQuote row
    for this bet's exact marketTicker (finalize_closing_quotes() already
    applied) so the isClosingQuote flag is trustworthy.
    """
    if bet.get("entryPrice") is None:
        return {"clvStatus": "UNAVAILABLE", "unavailableReason": "ENTRY_PRICE_MISSING"}

    closing_quote = next((q for q in clv_quotes_for_ticker if q.get("isClosingQuote")), None)
    if closing_quote is None:
        return {"clvStatus": "UNAVAILABLE", "unavailableReason": "NO_VALID_PRE_CLOSE_QUOTE"}

    # Coverage verdict for the selected quote. Selection itself is unchanged
    # (finalize_closing_quotes already picked the latest valid pre-start quote
    # by timestamp); this states how close to the start that quote actually
    # was, so a caller can never again treat "a quote exists" as "closing-line
    # evidence exists". Derived from the quote's OWN recorded scheduledStart
    # where present; when absent (archived rows written before that field
    # existed) the distance is simply unknown and is reported as such rather
    # than assumed favourable.
    start_used = scheduled_start or closing_quote.get("scheduledStart")
    seconds_to_start = checkpoints.seconds_before_start(closing_quote.get("capturedAt"), start_used)
    coverage_class = (
        checkpoints.classify_closing_coverage(seconds_to_start)
        if seconds_to_start is not None else None
    )
    coverage = {
        "closingCoverageClass": coverage_class,
        "closingSecondsBeforeStart": seconds_to_start,
        "closingCheckpoint": closing_quote.get("checkpoint"),
        "closingCapturedAt": closing_quote.get("capturedAt"),
        "closingScheduledStart": start_used,
    }

    # A post-start quote must never score CLV. finalize_closing_quotes should
    # already have excluded it, so reaching here means the stored isClosingQuote
    # flag disagrees with the timestamps -- fail closed rather than trust a flag
    # over the evidence that produced it.
    if seconds_to_start is not None and seconds_to_start < 0:
        return dict(coverage, clvStatus="UNAVAILABLE",
                    unavailableReason="CLOSING_QUOTE_IS_POST_START")

    side = bet.get("side") or "YES"
    # An undeclared price unit is reported as its OWN reason, never folded
    # into "missing executable price": the two demand different remedies
    # (backfill the unit declaration vs. no book on that side), and the
    # 100x CLV defect stayed invisible for ten days precisely because a
    # unit problem could masquerade as a merely absent quote.
    if closing_quote.get("priceUnit") not in (
        clv_convention.UNIT_CENTS, clv_convention.UNIT_PROBABILITY,
    ):
        return dict(coverage, clvStatus="UNAVAILABLE",
                    unavailableReason="CLOSING_QUOTE_PRICE_UNIT_UNDECLARED")
    closing_implied = _executable_closing_implied(closing_quote, side)
    if closing_implied is None:
        return dict(coverage, clvStatus="UNAVAILABLE",
                    unavailableReason="CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE")

    entry_implied = bet["entryPrice"]
    # CANONICAL: closing - entry, positive is good. Delegated to the single
    # source of truth rather than re-deriving a sign here.
    clv_cents = round(clv_convention.good_clv_from_implied(
        entry_implied, closing_implied,
        unit=clv_convention.UNIT_PERCENTAGE_POINTS), 2)

    return dict(coverage, **{
        "clvStatus": "VALID",
        "clvQuoteId": closing_quote["clvQuoteId"],
        "clvCents": clv_cents,
        "entryImpliedProbability": round(entry_implied, 4),
        "closingImpliedProbability": round(closing_implied, 4),
        "probabilityClv": round(clv_cents / 100.0, 4),
        "entryAmericanOdds": implied_to_american(entry_implied),
        "closingAmericanOdds": implied_to_american(closing_implied),
        "clvConvention": clv_convention.CONVENTION_ID,
    })
