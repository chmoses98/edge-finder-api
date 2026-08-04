"""
lib/edgelab/observation_linkage.py
======================================
Bet-to-Observation Linkage milestone (Part 4 of the MLB Market Research
Corpus & Frictionless Manual Logging milestone): for a manually-imported
wager, locate the best archived pregame MarketObservation for the EXACT
same marketTicker, so the user never has to look up or reconstruct a
price snapshot by hand.

This module NEVER claims an observation's own capturedAt is the bet's
actual placement time -- entryTimestamp/recordedAt (lib/edgelab/bets.py)
remain the only fields with that meaning. Linkage only ever supplies
CONTEXT: which archived quote is the most relevant one to compare the
user's reported entry price against.

Selection rule (deliberately simple and deterministic, matching this
milestone's priority order):
  1. Only observations for the exact marketTicker are ever candidates --
     never a "similar" or related market.
  2. Only VALID PREGAME observations qualify -- isValidPregameObservation
     is True (tradable, and strictly before this game's actual/scheduled
     start). A post-start observation is never selected when ANY valid
     pregame observation exists; if NONE exists at all (e.g. the market
     was never captured before first pitch), the bet is saved UNLINKED
     rather than inventing a link to a post-start quote.
  3. Among valid pregame candidates, a manually-triggered standalone
     price-check capture (source == "standalone_price_check") is
     preferred over a scheduled/automated capture when both exist at the
     same latest moment -- see _rank -- since that is the capture most
     likely to be the exact quote the user actually looked at while
     deciding. Otherwise the LATEST valid pregame observation wins (the
     one closest to the actual decision/entry moment).
"""

from datetime import datetime, timedelta

from lib.edgelab import storage as _default_storage

STANDALONE_SOURCE = "standalone_price_check"


def _is_valid_pregame_candidate(observation):
    return observation.get("isValidPregameObservation") is True


def _rank(observation):
    """Sort key: standalone-triggered captures outrank automated ones at the same capturedAt; later capturedAt wins overall."""
    is_standalone = observation.get("source") == STANDALONE_SOURCE
    return (observation.get("capturedAt") or "", is_standalone)


def select_linked_observation(observations_for_ticker):
    """
    Pure. `observations_for_ticker`: every MarketObservation row already
    filtered to one exact marketTicker (any date/source). Returns
    (observation_or_None, method_or_None, unavailable_reason_or_None).
    """
    candidates = [o for o in observations_for_ticker if _is_valid_pregame_candidate(o)]
    if not candidates:
        return None, None, "no_valid_pregame_observation_for_ticker"

    candidates.sort(key=_rank)
    best = candidates[-1]
    method = "EXACT_TICKER_STANDALONE_CHECK" if best.get("source") == STANDALONE_SOURCE else "EXACT_TICKER_PREGAME_LATEST"
    return best, method, None


def build_linkage_field(observations_for_ticker, side="YES"):
    """
    Builds the exact dict shape stored on PlacedBet.marketObservationLinkage
    (see data/edgelab/schema_v1/placed_bet.schema.json). Never raises --
    an empty/unmatched candidate list simply yields an explicit UNLINKED
    record, never a fabricated link.
    """
    observation, method, reason = select_linked_observation(observations_for_ticker)
    if observation is None:
        return {
            "observationId": None,
            "marketCorpusRunId": None,
            "observedAt": None,
            "observedPrice": None,
            "linkageMethod": None,
            "linkageStatus": "UNLINKED",
            "linkageConfidence": None,
            "unavailableReason": reason,
        }

    observed_price = observation.get("yesAsk") if side == "YES" else observation.get("noAsk")
    confidence = "HIGH" if method == "EXACT_TICKER_STANDALONE_CHECK" else "MEDIUM"
    return {
        "observationId": observation.get("marketObservationId"),
        "marketCorpusRunId": observation.get("runId"),
        "observedAt": observation.get("capturedAt"),
        "observedPrice": observed_price,
        "linkageMethod": method,
        "linkageStatus": "LINKED",
        "linkageConfidence": confidence,
        "unavailableReason": None,
    }


def load_observations_for_ticker(market_ticker, dates, storage_module=None):
    """
    Convenience loader: reads data/edgelab/observations/<date>.jsonl.gz
    for each date in `dates` (typically the bet's own gameDate, and the
    UTC-adjacent date for a late start) and returns only rows for
    `market_ticker`. storage_module defaults to lib.edgelab.storage;
    overridable for tests. Never raises on a missing date partition --
    lib.edgelab.storage.read_records already yields nothing for a file
    that doesn't exist yet.
    """
    storage_module = storage_module or _default_storage
    rows = []
    seen_dates = set()
    for date in dates:
        if not date or date in seen_dates:
            continue
        seen_dates.add(date)
        path = storage_module.partition_path("observations", date, compressed=True)
        rows.extend(r for r in storage_module.read_records(path) if r.get("marketTicker") == market_ticker)
    return rows


def link_bet_to_observation(market_ticker, game_date, side="YES", scheduled_start=None, storage_module=None):
    """
    End-to-end convenience: loads this ticker's observations for
    game_date (and the following UTC calendar date, since a late-start
    game's pregame captures can land after UTC midnight) and returns the
    linkage field dict ready to attach to a PlacedBet record.
    """
    dates = [game_date]
    if game_date:
        try:
            next_day = (datetime.strptime(game_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            dates.append(next_day)
        except ValueError:
            pass
    observations = load_observations_for_ticker(market_ticker, dates, storage_module=storage_module)
    return build_linkage_field(observations, side=side)
