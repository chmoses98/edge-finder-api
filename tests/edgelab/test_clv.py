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
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS", "yesBid": 48, "yesAsk": 50, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.45, "side": "YES"}
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingImpliedProbability"] == 0.5
    # CANONICAL (POSITIVE_IS_GOOD_V1): entered at 0.45 against a 0.50 closing
    # ask -> bought CHEAPER than the close -> POSITIVE. Previously asserted
    # -5.0; the old comment's own "-> wait" is the moment the inverted
    # formula contradicted the stated intent. See docs/EDGELAB_CLV_SIGN_AUDIT.md.
    assert result["clvCents"] == 5.0


def test_clv_no_side_uses_no_ask_derived_from_yes_bid():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS", "yesBid": 40, "yesAsk": 42, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.55, "side": "NO"}  # bought NO at 0.55 implied
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvStatus"] == "VALID"
    # NO-side executable close = 1 - yesBid/100 = 1 - 0.40 = 0.60
    assert result["closingImpliedProbability"] == 0.6
    assert result["clvCents"] == 5.0   # canonical: closing 0.60 - entry 0.55


def test_clv_positive_when_entered_better_than_close():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS", "yesBid": 55, "yesAsk": 57, "noBid": None, "noAsk": None}
    bet = {"entryPrice": 0.50, "side": "YES"}
    result = compute_clv_for_bet(bet, [closing_quote])
    assert result["clvCents"] == 7.0   # canonical: closing 0.57 - entry 0.50
    assert result["probabilityClv"] == 0.07   # same value, 0-1 scale


def test_entry_price_missing_is_unavailable_not_zero():
    result = compute_clv_for_bet({"entryPrice": None, "side": "YES"}, [{"isClosingQuote": True, "priceUnit": "CENTS", "yesAsk": 50}])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "ENTRY_PRICE_MISSING"


def test_closing_quote_missing_executable_price_is_unavailable():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS", "yesBid": None, "yesAsk": None, "noBid": None, "noAsk": None}
    result = compute_clv_for_bet({"entryPrice": 0.5, "side": "YES"}, [closing_quote])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE"


def test_no_candidates_returns_input_unchanged_never_fabricates():
    empty_result = select_closing_quote([], scheduled_start="2026-07-31T22:10:00Z")
    assert empty_result is None


def test_stale_last_quote_is_still_used_as_closing():
    """A quote captured hours before start (a gap in polling) is still the best available closing candidate -- never discarded just for being old."""
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-07-31T18:00:00Z", "marketStatus": "active", "isClosingQuote": False},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start="2026-07-31T22:10:00Z")
    assert finalized[0]["isClosingQuote"] is True


def test_finalize_closing_quotes_selects_none_when_start_timing_unresolved():
    """
    Production-CLV-layer regression for the market-price-calibration-audit
    measurement bug: with scheduledStart/actualStart both unresolved,
    finalize_closing_quotes() must leave every quote's isClosingQuote
    False (never guess a start time to justify picking the last tick),
    and compute_clv_for_bet() must then report NO_VALID_PRE_CLOSE_QUOTE
    rather than a CLV computed against a possibly-post-start price.
    """
    quotes = [
        {"clvQuoteId": "a", "capturedAt": "2026-08-14T05:26:45Z", "marketStatus": "active", "isClosingQuote": False, "yesBid": 5.0, "yesAsk": 8.0, "noBid": None, "noAsk": None},
        {"clvQuoteId": "b", "capturedAt": "2026-08-14T23:53:18Z", "marketStatus": "active", "isClosingQuote": False, "yesBid": 0.0, "yesAsk": 97.0, "noBid": None, "noAsk": None},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start=None, actual_start=None)
    assert all(q["isClosingQuote"] is False for q in finalized)
    assert finalized == quotes

    clv_result = compute_clv_for_bet({"entryPrice": 0.08, "side": "YES"}, finalized)
    assert clv_result["clvStatus"] == "UNAVAILABLE"
    assert clv_result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"


def test_wide_spread_quote_still_computes_clv():
    """CLV validity is gated on marketStatus/executable price presence, never on spread width."""
    wide_spread_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS", "yesBid": 10, "yesAsk": 90, "noBid": None, "noAsk": None}
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
        "clvQuoteId": "q1", "marketTicker": "T", "isClosingQuote": True, "priceUnit": "CENTS",
        "yesBid": 48, "yesAsk": 50, "noBid": None, "noAsk": None,
    }
    tranche1 = {"betId": "bet-1", "marketTicker": "T", "side": "YES", "entryPrice": 0.45}
    tranche2 = {"betId": "bet-2", "marketTicker": "T", "side": "YES", "entryPrice": 0.52}

    result1 = compute_clv_for_bet(tranche1, [closing_quote])
    result2 = compute_clv_for_bet(tranche2, [closing_quote])

    assert result1["clvStatus"] == "VALID" and result2["clvStatus"] == "VALID"
    assert result1["clvQuoteId"] == result2["clvQuoteId"] == "q1"
    assert result1["clvCents"] != result2["clvCents"]  # different entry price -> different CLV
    assert result1["clvCents"] == round((0.50 - 0.45) * 100, 2)
    assert result2["clvCents"] == round((0.50 - 0.52) * 100, 2)


# ---------------------------------------------------------------------------
# Regression: the 100x CLV scale defect at the ClvQuote producer/consumer
# boundary.
#
# ClvQuote prices used to carry no declared unit. The consumer
# (_executable_closing_implied) divided by 100 unconditionally -- correct
# only while MarketObservation prices were integer cents, which stopped
# being true on 2026-09-11 when the captured Kalshi snapshots switched to
# the declared `*_dollars` fixed-point fields. From that date a 0.66 NO ask
# was read as a 0.0066 closing probability, and CLV came out ~100x too
# negative against a correctly-scaled entryPrice.
#
# entryPrice is provably the SOUND side of that comparison: on every
# receipt-imported row it equals contractCost/contracts exactly, so these
# tests pin the CLOSING side's unit handling.
# ---------------------------------------------------------------------------

def test_clv_quotes_declare_their_price_unit():
    """The producer states the unit; it is never left for a consumer to guess."""
    quotes = project_observations_to_clv_quotes(
        [_obs("T", "2026-07-31T12:00:00Z", 0.34, 0.37)], {"T": "bet-1"}, run_id="r1",
    )
    assert quotes, "producer emitted no quote"
    assert all(q["priceUnit"] == "PROBABILITY" for q in quotes)


def test_probability_denominated_close_is_not_divided_by_100_again():
    """The live defect vector: KXMLBTOTAL-26SEP201435TORTEX-10, bet 997fd52d.

    Archived closing quote yesBid=0.34 / noAsk=0.66 against a NO entry at
    0.72. Stored CLV was -71.34 (closingPrice 0.0066). The truth is a
    6-cent adverse move, not a 71-cent one.
    """
    closing_quote = {
        "clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "PROBABILITY",
        "yesBid": 0.34, "yesAsk": 0.37, "noBid": 0.63, "noAsk": 0.66,
    }
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"}, [closing_quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingImpliedProbability"] == 0.66
    assert result["clvCents"] == -6.0


def test_no_side_derived_from_yes_bid_respects_the_declared_unit():
    """(one contract - yesBid) is 1.0-0.34 in PROBABILITY, never 100-0.34."""
    closing_quote = {
        "clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "PROBABILITY",
        "yesBid": 0.34, "yesAsk": 0.37, "noBid": None, "noAsk": None,
    }
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"}, [closing_quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingImpliedProbability"] == 0.66


def test_same_book_in_either_unit_yields_identical_clv():
    """Unit is a property of the field, so it must not change the answer."""
    bet = {"entryPrice": 0.45, "side": "YES"}
    cents = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "CENTS",
             "yesBid": 48, "yesAsk": 50, "noBid": None, "noAsk": None}
    probability = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "PROBABILITY",
                   "yesBid": 0.48, "yesAsk": 0.50, "noBid": None, "noAsk": None}
    a, b = compute_clv_for_bet(bet, [cents]), compute_clv_for_bet(bet, [probability])
    assert a["clvStatus"] == b["clvStatus"] == "VALID"
    assert a["clvCents"] == b["clvCents"] == 5.0
    assert a["closingImpliedProbability"] == b["closingImpliedProbability"] == 0.50


def test_undeclared_price_unit_fails_closed_and_never_fabricates_a_number():
    """An archived row with no declared unit is UNRESOLVABLE, not assumed.

    Assuming is exactly what produced the defect, so the absence of a
    declaration gets its own reason rather than a plausible-looking CLV.
    """
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True,
                     "yesBid": 0.34, "yesAsk": 0.37, "noBid": None, "noAsk": 0.66}
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"}, [closing_quote])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_PRICE_UNIT_UNDECLARED"
    assert "clvCents" not in result


def test_unrecognised_price_unit_is_rejected_not_coerced():
    closing_quote = {"clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "DOLLARS",
                     "yesBid": 0.34, "yesAsk": 0.37, "noBid": None, "noAsk": 0.66}
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"}, [closing_quote])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_PRICE_UNIT_UNDECLARED"


# ---------------------------------------------------------------------------
# Regression: FIRST_DAILY must never silently masquerade as closing-line
# evidence.
#
# 57 of 94 recent CLV rows were scored against a FIRST_DAILY quote sitting a
# median 863 minutes -- 14.4 hours -- before first pitch, and reported as
# "Average CLV" without qualification. Selection was never wrong (the latest
# valid pre-start quote IS the right choice when it is the only one); what was
# missing was any statement of how far from the close that quote actually was.
# See docs/EDGELAB_CLOSING_QUOTE_POLICY.md.
# ---------------------------------------------------------------------------

_PROB_QUOTE = {
    "clvQuoteId": "c", "isClosingQuote": True, "priceUnit": "PROBABILITY",
    "yesBid": 0.34, "yesAsk": 0.37, "noBid": 0.63, "noAsk": 0.66,
}
_START = "2026-09-20T17:40:00Z"


def _closing(captured_at, checkpoint="FIRST_DAILY"):
    return dict(_PROB_QUOTE, capturedAt=captured_at, scheduledStart=_START, checkpoint=checkpoint)


def test_stale_first_daily_close_is_classified_pre_close_not_true_close():
    """The live defect vector: captured 08:11, game starts 17:40."""
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T08:11:00Z")])
    assert result["clvStatus"] == "VALID"          # still computed
    assert result["closingCoverageClass"] == "PRE_CLOSE"
    assert result["closingSecondsBeforeStart"] == 34140.0
    assert result["closingCheckpoint"] == "FIRST_DAILY"


def test_first_daily_near_start_is_true_close_label_does_not_decide():
    """Coverage is decided by the clock, never by the provenance label."""
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T17:20:00Z", checkpoint="FIRST_DAILY")])
    assert result["closingCoverageClass"] == "TRUE_CLOSE"
    assert result["closingSecondsBeforeStart"] == 1200.0
    assert result["closingCheckpoint"] == "FIRST_DAILY"


def test_t_minus_90_label_far_from_start_is_still_pre_close():
    """The mirror case: a near-close-sounding label on a stale quote."""
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T03:00:00Z", checkpoint="T_MINUS_90")])
    assert result["closingCoverageClass"] == "PRE_CLOSE"


def test_exactly_at_threshold_is_true_close():
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T17:10:00Z")])
    assert result["closingSecondsBeforeStart"] == 1800.0
    assert result["closingCoverageClass"] == "TRUE_CLOSE"


def test_one_second_past_threshold_is_pre_close():
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T17:09:59Z")])
    assert result["closingSecondsBeforeStart"] == 1801.0
    assert result["closingCoverageClass"] == "PRE_CLOSE"


def test_post_start_quote_never_scores_clv_even_if_flagged_closing():
    """A stored isClosingQuote flag never outranks the timestamps."""
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"},
                                 [_closing("2026-09-20T18:00:00Z")])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_IS_POST_START"
    assert "clvCents" not in result


def test_unknown_start_reports_unknown_distance_rather_than_assuming_closeness():
    quote = dict(_PROB_QUOTE, capturedAt="2026-09-20T08:11:00Z", checkpoint="FIRST_DAILY")
    result = compute_clv_for_bet({"entryPrice": 0.72, "side": "NO"}, [quote])
    assert result["clvStatus"] == "VALID"
    assert result["closingCoverageClass"] is None
    assert result["closingSecondsBeforeStart"] is None


def test_produced_quotes_carry_scheduled_start_for_coverage():
    quotes = project_observations_to_clv_quotes(
        [_obs("T", "2026-07-31T12:00:00Z", 0.34, 0.37)], {"T": "bet-1"}, run_id="r1",
    )
    assert all(q["scheduledStart"] == "2026-07-31T22:10:00Z" for q in quotes)
