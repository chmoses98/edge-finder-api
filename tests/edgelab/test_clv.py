#!/usr/bin/env python3
"""
tests/edgelab/test_clv.py
=============================
Coverage for lib/edgelab/clv.py and lib/edgelab/checkpoints.py's closing-
quote selection: entry-to-closing calculation, YES/NO handling, bid/ask
selection, final valid pre-close quote, suspended markets, delayed
games, missing close, stale/wide-spread quotes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.checkpoints import select_closing_quote
from lib.edgelab.clv import (
    compute_clv_for_bet,
    finalize_closing_quotes,
    project_observations_to_clv_quotes,
)


def _obs(ticker, captured_at, yes_bid, yes_ask, *, status="active", scheduled_start="2026-07-31T22:10:00Z", game_id="g1"):
    return {
        "marketObservationId": f"{ticker}|{captured_at}",
        "marketTicker": ticker,
        "capturedAt": captured_at,
        "gameId": game_id,
        "scheduledStart": scheduled_start,
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": None,
        "noAsk": None,
        "lastPrice": None,
        "marketStatus": status,
        "checkpoint": None,
        "provenance": {"sourceSystem": "s", "sourceFile": "f", "sourceKey": ticker, "capturedAt": captured_at},
    }


def test_high_priority_bet_ticker_keeps_every_quote_regardless_of_checkpoint():
    ticker = "KXMLBF5-26JUL312140DETATH-DET"
    observations = [
        _obs(ticker, "2026-07-31T20:00:00Z", 50, 51),
        _obs(ticker, "2026-07-31T20:03:00Z", 50, 52),  # not near any standard checkpoint
        _obs(ticker, "2026-07-31T20:06:00Z", 51, 52),
    ]
    quotes = project_observations_to_clv_quotes(observations, {ticker: "bet-1"}, run_id="run1")
    assert len(quotes) == 3
    assert all(q["betId"] == "bet-1" for q in quotes)


def test_non_bet_ticker_keeps_only_standard_checkpoints():
    ticker = "KXMLBHIT-26JUL312140DETATH-PLAYER1"
    observations = [
        _obs(ticker, "2026-07-31T20:40:00Z", 50, 51),  # first-of-day -> FIRST_DAILY (kept)
        _obs(ticker, "2026-07-31T21:25:00Z", 50, 51),  # 45 min out -- squarely between the T_MINUS_60/30 targets, dropped
        _obs(ticker, "2026-07-31T21:40:00Z", 51, 52),  # 30 min out -> T_MINUS_30 (kept)
    ]
    quotes = project_observations_to_clv_quotes(observations, {}, run_id="run1")
    assert len(quotes) == 2
    checkpoints = {q["checkpoint"] for q in quotes}
    assert checkpoints == {"FIRST_DAILY", "T_MINUS_30"}


def test_final_valid_pre_close_quote_selected_over_earlier_and_later():
    ticker = "T"
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T21:00:00Z", "marketStatus": "active"},
        {"clvQuoteId": "b", "capturedAt": "2026-07-31T22:05:00Z", "marketStatus": "active"},
        {"clvQuoteId": "c", "capturedAt": "2026-07-31T22:15:00Z", "marketStatus": "active"},  # after start
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert len(closing) == 1
    assert closing[0]["clvQuoteId"] == "b"


def test_suspended_market_quote_excluded_from_closing_candidates():
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T21:55:00Z", "marketStatus": "active"},
        {"clvQuoteId": "b", "capturedAt": "2026-07-31T22:05:00Z", "marketStatus": "suspended"},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert closing[0]["clvQuoteId"] == "a"


def test_delayed_game_uses_actual_start_not_scheduled():
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T22:05:00Z", "marketStatus": "active"},
        {"clvQuoteId": "b", "capturedAt": "2026-07-31T22:20:00Z", "marketStatus": "active"},  # after actual, before scheduled+delay
    ]
    # Scheduled 22:10, but game actually started late at 22:30 -- quote b should count.
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z", actual_start="2026-07-31T22:30:00Z")
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert closing[0]["clvQuoteId"] == "b"


def test_missing_close_never_guesses():
    quotes = [{"clvQuoteId": "a", "capturedAt": "2026-07-31T22:15:00Z", "marketStatus": "active", "isClosingQuote": False}]  # only a post-start quote exists
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    assert all(not q["isClosingQuote"] for q in finalized)

    bet = {"entryPrice": 0.5, "side": "YES"}
    result = compute_clv_for_bet(bet, finalized)
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"


def test_clv_yes_side_uses_yes_ask():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "yesBid": 48, "yesAsk": 50, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.45, "side": "YES"}
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingImpliedProbability"] == 0.5
    assert result["clvCents"] == -5.0  # entered cheaper (0.45) than the 0.50 closing ask -> wait: 0.45-0.50=-0.05*100=-5


def test_clv_no_side_uses_no_ask_derived_from_yes_bid():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "yesBid": 40, "yesAsk": 42, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.55, "side": "NO"}  # bought NO at 0.55 implied
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvStatus"] == "VALID"
    # NO-side executable close = 1 - yesBid/100 = 1 - 0.40 = 0.60
    assert result["closingImpliedProbability"] == 0.6
    assert result["clvCents"] == -5.0  # 0.55 - 0.60


def test_clv_positive_when_entered_better_than_close():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "yesBid": 55, "yesAsk": 57, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.50, "side": "YES"}
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvCents"] == -7.0  # 0.50 - 0.57
    assert result["probabilityClv"] == -0.07


def test_entry_price_missing_is_unavailable_not_zero():
    result = compute_clv_for_bet({"entryPrice": None, "side": "YES"}, [{"isClosingQuote": True, "yesAsk": 50}])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "ENTRY_PRICE_MISSING"


def test_closing_quote_missing_executable_price_is_unavailable():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "yesBid": None, "yesAsk": None, "noBid": None, "noAsk": None}
    result = compute_clv_for_bet({"entryPrice": 0.5, "side": "YES"}, [closing_quote])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE"


def test_no_candidates_returns_input_unchanged_never_fabricates():
    empty_result = select_closing_quote([], scheduled_start="2026-07-31T22:10:00Z")
    assert empty_result is None


# ---------------------------------------------------------------------------
# scheduledStart/CLV metadata fix: the CLV fail-safe. Before this fix,
# select_closing_quote with NEITHER scheduled_start NOR actual_start known
# silently degraded to "the last active-status quote of the day, unbounded
# by time" -- on a standalone/manual-research day whose scheduledStart
# never resolved (the real 2026-08-15 case), this could confidently
# mislabel an arbitrary (possibly post-game) quote as the market close
# instead of refusing outright. A trustworthy start boundary is now
# REQUIRED to select a closing quote at all.
# ---------------------------------------------------------------------------

def test_unresolved_schedule_never_falls_back_to_last_active_quote():
    """The exact bug this fix closes: with no scheduled_start/actual_start known, refuse -- never guess the last active tick."""
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T18:00:00Z", "marketStatus": "active"},
        {"clvQuoteId": "b", "capturedAt": "2026-07-31T23:59:00Z", "marketStatus": "active"},  # would have won under the old "last active tick" fallback
    ]
    result = select_closing_quote(quotes, scheduled_start=None, actual_start=None)
    assert result is None


def test_unresolved_schedule_yields_clv_unavailable_not_a_fabricated_number():
    """End-to-end: an unresolved scheduledStart must surface as CLV_UNAVAILABLE (NO_VALID_PRE_CLOSE_QUOTE), never a computed CLV number."""
    ticker = "T"
    observations = [
        _obs(ticker, "2026-07-31T18:00:00Z", 50, 51, scheduled_start=None),
        _obs(ticker, "2026-07-31T23:59:00Z", 53, 55, scheduled_start=None),
    ]
    quotes = project_observations_to_clv_quotes(observations, {ticker: "bet-1"}, run_id="run1")
    finalized = finalize_closing_quotes(quotes, scheduled_start=None, actual_start=None)
    assert all(not q["isClosingQuote"] for q in finalized)

    bet = {"entryPrice": 0.5, "side": "YES"}
    result = compute_clv_for_bet(bet, finalized)
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"


def test_post_start_active_observation_never_selected_merely_because_status_is_active():
    """
    Requirement: CLV checkpoint selection must never pick a post-start/
    postgame quote just because Kalshi's own marketStatus field still
    reads 'active' -- the time bound (once a trustworthy scheduledStart
    IS known) is what excludes it, independent of status.
    """
    quotes = [
        {"clvQuoteId": "pregame", "capturedAt": "2026-07-31T21:55:00Z", "marketStatus": "active"},
        {"clvQuoteId": "post_start_but_still_active", "capturedAt": "2026-07-31T23:30:00Z", "marketStatus": "active"},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert len(closing) == 1
    assert closing[0]["clvQuoteId"] == "pregame"


def test_stale_last_quote_is_still_used_as_closing():
    """A quote captured hours before start (a gap in polling) is still the best available closing candidate -- never discarded just for being old."""
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T18:00:00Z", "marketStatus": "active", "isClosingQuote": False},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    assert finalized[0]["isClosingQuote"] is True


def test_wide_spread_quote_still_computes_clv():
    """CLV validity is gated on marketStatus/executable price presence, never on spread width."""
    wide_spread_quote = {"clvQuoteId": "c", "isClosingQuote": True, "yesBid": 10, "yesAsk": 90, "noBid": None, "noAsk": None}
    result = compute_clv_for_bet({"entryPrice": 0.5, "side": "YES"}, [wide_spread_quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingImpliedProbability"] == 0.9


def test_batch_with_some_malformed_observations_still_processes_the_rest():
    """One observation missing bid/ask (a partial-data record) must not prevent the rest of the batch from being classified."""
    ticker = "T"
    good = _obs(ticker, "2026-07-31T20:40:00Z", 50, 51)
    malformed = _obs(ticker, "2026-07-31T20:45:00Z", None, None)
    quotes = project_observations_to_clv_quotes([good, malformed], {ticker: "bet-1"}, run_id="run1")
    assert len(quotes) == 2  # both preserved -- missing prices are a data-quality fact, not a reason to drop the row
    malformed_quote = next(q for q in quotes if q["capturedAt"] == "2026-07-31T20:45:00Z")
    assert malformed_quote["yesBid"] is None and malformed_quote["yesAsk"] is None


def test_multiple_tranches_on_one_ticker_each_get_own_clv_from_the_shared_closing_quote():
    """
    Canonical Placed-Bet Ledger milestone, requirement 12: multiple bet
    tranches may share a closing quote but remain separate bets -- each
    tranche's own entryPrice produces its own CLV, all referencing the
    same clvQuoteId.
    """
    closing_quote = {
        "clvQuoteId": "q1", "marketTicker": "T", "isClosingQuote": True,
        "yesBid": 48, "yesAsk": 50, "noBid": None, "noAsk": None,
    }
    tranche1 = {"betId": "bet-1", "marketTicker": "T", "side": "YES", "entryPrice": 0.45}
    tranche2 = {"betId": "bet-2", "marketTicker": "T", "side": "YES", "entryPrice": 0.52}

    result1 = compute_clv_for_bet(tranche1, [closing_quote])
    result2 = compute_clv_for_bet(tranche2, [closing_quote])

    assert result1["clvStatus"] == "VALID" and result2["clvStatus"] == "VALID"
    assert result1["clvQuoteId"] == result2["clvQuoteId"] == "q1"
    assert result1["clvCents"] != result2["clvCents"]  # different entry price -> different CLV
    assert result1["clvCents"] == round((0.45 - 0.50) * 100, 2)
    assert result2["clvCents"] == round((0.52 - 0.50) * 100, 2)
