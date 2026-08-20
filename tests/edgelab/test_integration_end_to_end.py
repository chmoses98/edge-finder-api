#!/usr/bin/env python3
"""
tests/edgelab/test_integration_end_to_end.py
=================================================
Integration coverage that chains the REAL output of each stage into the
REAL input of the next, rather than hand-rolled dicts shaped to match
whatever a function currently expects. This is deliberately a separate
file from the per-module unit tests: it exists specifically to catch
schema-drift bugs where one module's writer and another module's reader
silently disagree on a field name — exactly the class of bug a fully
mocked unit test cannot see (see settle_market()'s prior 'outcome' vs
'outcomeLabel' mismatch, found during review: every settlement unit test
passed because its hand-built Market fixtures happened to use the same
wrong key the buggy code read, while the REAL Market records
build_market_records() produces never had that key at all).

Traces: observed -> normalized (Market/Game) -> evaluated/recommended or
passed -> bet placed or not -> quote history -> closing quote ->
settlement -> CLV and P/L, for every observed eligible market, including
ones nobody bets.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, schema
from lib.edgelab.bets import build_manual_bet_record
from lib.edgelab.clv import compute_clv_for_bet, finalize_closing_quotes, project_observations_to_clv_quotes
from lib.edgelab.market_universe import build_game_records, build_market_records, build_observations_from_snapshot
from lib.edgelab.recommendations import extend_with_full_universe
from lib.edgelab.settlement import settle_bets_for_ticker, settle_market

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")
RUN_ID = "integration-run-1"


def _real_markets_and_observations():
    observations, _ = build_observations_from_snapshot(FIXTURE, RUN_ID, game_context={})
    markets = build_market_records(observations)
    markets_by_ticker = {m["marketTicker"]: m for m in markets}
    return observations, markets_by_ticker


def test_tie_ticker_settles_correctly_through_the_real_market_pipeline():
    """
    The regression this test guards: settle_market() must read whatever
    field build_market_records() ACTUALLY writes for a ticker's Win/Tie
    shape, not a field name that only exists in a hand-rolled test dict.
    """
    _, markets_by_ticker = _real_markets_and_observations()
    tie_market = markets_by_ticker["KXMLBF5-26JUL312145SFSD-TIE"]
    sf_market = markets_by_ticker["KXMLBF5-26JUL312145SFSD-SF"]

    # Real Market records must actually carry the outcomeLabel this test
    # depends on -- if a future refactor drops/renames it again without
    # updating settle_market(), this assertion (not just the settlement
    # result below) fails loudly.
    assert tie_market["outcomeLabel"] == "Tie"
    assert sf_market["outcomeLabel"] == "Win"
    assert sf_market["team"] == "SF"

    tied_game = {
        "periodScores": {"F5": (2, 2)}, "completedInnings": 5, "gameStatus": "Final",
        "awayAbbr": "SF", "homeAbbr": "SD",
    }
    assert settle_market(tie_market, tied_game) == ("SETTLED", "YES", None)
    assert settle_market(sf_market, tied_game) == ("SETTLED", "NO", None)

    sf_wins_game = {
        "periodScores": {"F5": (4, 1)}, "completedInnings": 5, "gameStatus": "Final",
        "awayAbbr": "SF", "homeAbbr": "SD",
    }
    assert settle_market(tie_market, sf_wins_game) == ("SETTLED", "NO", None)
    assert settle_market(sf_market, sf_wins_game) == ("SETTLED", "YES", None)


def test_full_chain_observed_to_settled_for_a_bet_market():
    """
    observed -> normalized -> evaluated/recommended -> bet placed ->
    quote history -> closing quote -> settlement -> CLV and P/L, using
    real functions end to end for one moneyline ticker.
    """
    observations, markets_by_ticker = _real_markets_and_observations()
    ticker = "KXMLBGAME-26JUL312210BOSLAD-BOS"
    assert ticker in markets_by_ticker  # 1. observed
    market = markets_by_ticker[ticker]
    assert market["marketFamily"] == "game_result"  # 2. normalized

    base_obs = next(o for o in observations if o["marketTicker"] == ticker)
    # This fixture's markets were never joined against a real slate
    # (game_context={}), so scheduledStart is genuinely None here -- keep
    # that as-is (never fabricate one). With BOTH scheduledStart and
    # actualStart unresolved, select_closing_quote() can no longer verify
    # any observation is genuinely pre-start, so it now (correctly)
    # selects no closing quote at all -- see the
    # test_no_closing_quote_when_start_timing_unresolved regression in
    # tests/edgelab/test_checkpoints.py for the isolated unit-level proof.
    scheduled_start = base_obs["scheduledStart"]
    assert scheduled_start is None

    # Build a small real quote history: the fixture gives us one snapshot
    # at 22:34:16Z; simulate two more ticks moving the price AFTER that,
    # using the SAME ID scheme ingestion actually uses (not a fabricated ID).
    history = [base_obs]
    for captured_at, yes_bid, yes_ask in [
        ("2026-07-31T22:40:00.000Z", 50, 52),
        ("2026-07-31T22:45:00.000Z", 53, 55),
    ]:
        obs = dict(base_obs)
        obs["capturedAt"] = captured_at
        obs["yesBid"], obs["yesAsk"] = yes_bid, yes_ask
        obs["marketObservationId"] = ids.build_market_observation_id(ticker, captured_at)
        history.append(obs)

    # 3. evaluated: this market's series (KXMLBGAME) IS one the model
    # config supports, but no marketLedger row exists in this test -> NOT_EVALUATED.
    recs = extend_with_full_universe(
        covered_tickers=set(), observations=[base_obs], model_covered_series=frozenset({"KXMLBGAME"}), date="2026-07-31",
    )
    assert recs[0]["status"] == "NOT_EVALUATED"
    assert schema.validate_record("recommendation", recs[0]) == []

    # 4. bet placed (a real bet, unrecommended by the model -- exactly the
    # "bet without model support" case).
    bet = build_manual_bet_record(ticker, "BOS moneyline", stake=10.0, entry_price=0.52, entry_timestamp=history[1]["capturedAt"])
    assert schema.validate_record("placed_bet", bet) == []

    # Re-running the recommendation extension now that a bet exists must
    # surface BET_PLACED, not silently stay NOT_EVALUATED.
    recs_with_bet = extend_with_full_universe(
        covered_tickers=set(), observations=[base_obs], model_covered_series=frozenset({"KXMLBGAME"}),
        date="2026-07-31", placed_bet_tickers={ticker: bet["betId"]},
    )
    assert recs_with_bet[0]["status"] == "BET_PLACED"
    assert recs_with_bet[0]["betId"] == bet["betId"]

    # 5. quote history + 6. closing quote. With scheduledStart unresolved
    # (and no actualStart either), select_closing_quote() can't verify any
    # tick is genuinely pre-start, so it selects none -- see
    # lib.edgelab.checkpoints.select_closing_quote's docstring and
    # data/edgelab/reports/market_price_calibration_audit.md for the real
    # bug this guards against (a post-start quote on an unresolved-start
    # ticker previously got misclassified as the pregame closing price).
    quotes = project_observations_to_clv_quotes(history, {ticker: bet["betId"]}, RUN_ID)
    assert len(quotes) == len(history)  # every tick kept -- this ticker is bet-tracked
    finalized = finalize_closing_quotes(quotes, scheduled_start=scheduled_start)
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert len(closing) == 0
    assert finalized == quotes  # returned unchanged, per finalize_closing_quotes's "never guesses" contract

    # 7. settlement.
    game_outcome = {"awayRuns": 6, "homeRuns": 2, "awayAbbr": "BOS", "homeAbbr": "LAD", "gameStatus": "Final"}
    status, result, reason = settle_market(market, game_outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)  # BOS (away) won

    # 8. CLV and P/L: no closing quote exists, so CLV is honestly
    # unavailable rather than computed against a non-pregame price.
    clv_result = compute_clv_for_bet(bet, finalized)
    assert clv_result["clvStatus"] == "UNAVAILABLE"
    assert clv_result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"

    settled_bets = settle_bets_for_ticker([bet], status, result)
    assert settled_bets[0]["result"] == "WIN"
    # Kalshi Fee-Aware Execution Economics milestone (correction pass):
    # fee-aware WIN P/L on ACTUAL cash consumed, not the full allocated
    # stake -- see tests/edgelab/test_settlement.py's
    # test_settle_bets_for_ticker_settles_every_bet_not_just_the_first
    # for the derivation pattern. $10.00 at $0.52 affords 18 whole
    # contracts, actually consuming $9.68 (18*0.52 + fee); the payout is
    # 18 * $1.00 = $18.00, net = $18.00 - $9.68 = $8.32.
    assert settled_bets[0]["netProfitLoss"] == 8.32


def test_full_chain_settles_an_unbet_market_with_hypothetical_return_only():
    """An observed-but-never-bet market must still be settleable, with a
    price-dependent hypothetical return and no realized return."""
    observations, markets_by_ticker = _real_markets_and_observations()
    ticker = "KXMLBTOTAL-26JUL312145SFSD-9"
    market = markets_by_ticker[ticker]
    # game_total/inning_total suffixes are strict integers ("over N runs",
    # no half-run line) -- unlike winning_margin/team_total's N-0.5
    # convention. See market_taxonomy._total_line_from_suffix's docstring.
    assert market["threshold"] == 9

    game_outcome = {"awayRuns": 5, "homeRuns": 5, "gameStatus": "Final"}  # 10 total runs, over 9
    status, result, reason = settle_market(market, game_outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)

    obs = next(o for o in observations if o["marketTicker"] == ticker)
    from lib.edgelab.settlement import hypothetical_yes_return
    yes_price = obs["yesAsk"] / 100.0
    hyp_return = hypothetical_yes_return(yes_price, result)
    assert hyp_return == round((1 - yes_price) / yes_price, 4)
    assert hyp_return > 0  # the YES side won, at a real, price-dependent payout

    settled_bets = settle_bets_for_ticker([], status, result)
    assert settled_bets == []  # no bet existed -- nothing to settle, no fabricated realized return
