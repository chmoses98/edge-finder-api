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
    clvCents         = round((entry_implied - closing_implied) * 100, 2)
    probabilityClv   = clvCents / 100 (same number, 0-1 scale, for callers
                       that want a probability delta rather than "cents")
Positive clvCents means the bet was entered at a better (cheaper) price
than the market's closing price implies -- good CLV, independent of the
eventual settlement outcome.

American odds fields are for display only, derived via
scripts/clv_from_snapshot.implied_to_american() -- the repo's existing
implied-probability -> American-odds conversion, reused rather than
reimplemented.
"""

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION
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
    The executable probability a bettor on `side` would have faced at
    close: YES side -> yesAsk (what you'd pay to buy in); NO side -> the
    NO-side ask, derived as (1 - yesBid) when a direct noAsk isn't
    available (Kalshi's NO ask is economically 100 - YES bid). Returns
    None (never a guess) if neither is present.
    """
    if side == "NO":
        no_ask = closing_quote.get("noAsk")
        if no_ask is not None:
            return no_ask / 100.0
        yes_bid = closing_quote.get("yesBid")
        return (1.0 - yes_bid / 100.0) if yes_bid is not None else None

    yes_ask = closing_quote.get("yesAsk")
    if yes_ask is not None:
        return yes_ask / 100.0
    yes_bid = closing_quote.get("yesBid")
    return yes_bid / 100.0 if yes_bid is not None else None


def compute_clv_for_bet(bet, clv_quotes_for_ticker):
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

    side = bet.get("side") or "YES"
    closing_implied = _executable_closing_implied(closing_quote, side)
    if closing_implied is None:
        return {"clvStatus": "UNAVAILABLE", "unavailableReason": "CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE"}

    entry_implied = bet["entryPrice"]
    clv_cents = round((entry_implied - closing_implied) * 100, 2)

    return {
        "clvStatus": "VALID",
        "clvQuoteId": closing_quote["clvQuoteId"],
        "clvCents": clv_cents,
        "entryImpliedProbability": round(entry_implied, 4),
        "closingImpliedProbability": round(closing_implied, 4),
        "probabilityClv": round(clv_cents / 100.0, 4),
        "entryAmericanOdds": implied_to_american(entry_implied),
        "closingAmericanOdds": implied_to_american(closing_implied),
    }
