"""
tests/test_w1a_canonical_settlement_semantics.py
================================================
WAVE 1, subwave A. The adversarial matrix for canonical settlement semantics
and symmetric bet-side resolution.

THE INVARIANT UNDER TEST
    A WAGER MAY BE CALLED WON OR LOST ONLY WHEN THE EXACT CONTRACT, THE EXACT
    SIDE OWNED, AND THE TERMINAL TRUTH ARE ALL PROVEN.

Every symmetric case is asserted in BOTH directions. A suite that tests only
YES proves only that the common path works, and the common path is not where a
side inversion hides -- it hid in NRFI, in an Under, and in a missing field
that defaulted.

Refusals are asserted as VALUES, not as exceptions and not as "falsy". A
refusal that cannot be told apart from a grade is not fail-closed.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import clv_update  # noqa: E402
from lib import wager_settlement_semantics as wss  # noqa: E402
from lib.edgelab import settlement as edgelab_settlement  # noqa: E402

# One real event, used throughout so every case is about semantics and never
# about a differently-shaped fixture. COL @ DET, 2026-09-11, 18:40.
EVENT = "26SEP111840COLDET"
GAME = "COL@DET"
ML_AWAY = "KXMLBGAME-%s-COL" % EVENT
ML_HOME = "KXMLBGAME-%s-DET" % EVENT
F5_AWAY = "KXMLBF5-%s-COL" % EVENT
F5_HOME = "KXMLBF5-%s-DET" % EVENT
F5_TIE = "KXMLBF5-%s-TIE" % EVENT
F3_AWAY = "KXMLBF3-%s-COL" % EVENT
F7_AWAY = "KXMLBF7-%s-COL" % EVENT
RFI = "KXMLBRFI-%s" % EVENT
TOTAL_9 = "KXMLBTOTAL-%s-9" % EVENT
TT_COL_4 = "KXMLBTEAMTOTAL-%s-COL4" % EVENT
TT_DET_4 = "KXMLBTEAMTOTAL-%s-DET4" % EVENT
RL_COL_2 = "KXMLBSPREAD-%s-COL2" % EVENT
PROP = "KXMLBKS-%s-SMITH7" % EVENT


def side_of(**wager):
    wager.setdefault("game", GAME)
    return wss.resolve_wager_side(wager)


# ─────────────────────────────────────────────────────────────────────────────
# Explicit YES / explicit NO, and their symmetry
# ─────────────────────────────────────────────────────────────────────────────

def test_explicit_yes_is_resolved_from_the_row():
    out = side_of(market="ML", betSide="YES", marketTicker=ML_AWAY)
    assert out["side"] == wss.SIDE_YES
    assert out["basis"] == wss.BASIS_DECLARED_CONTRACT_SIDE
    assert out["refusalReason"] is None


def test_explicit_no_is_resolved_from_the_row_with_the_same_basis():
    out = side_of(market="ML", betSide="NO", marketTicker=ML_AWAY)
    assert out["side"] == wss.SIDE_NO
    assert out["basis"] == wss.BASIS_DECLARED_CONTRACT_SIDE
    assert out["refusalReason"] is None


def test_yes_and_no_are_symmetric_on_one_binary_ticker():
    """
    The same ticker, the same evidence, the only difference being the side
    declared. Neither answer may require more evidence than the other, and
    neither may borrow a default.
    """
    base = dict(market="ML", marketTicker=ML_AWAY)
    yes = side_of(betSide="YES", **base)
    no = side_of(betSide="NO", **base)
    assert (yes["side"], no["side"]) == (wss.SIDE_YES, wss.SIDE_NO)
    assert yes["basis"] == no["basis"]
    assert yes["refusalReason"] is no["refusalReason"] is None
    # And the grade inverts with the side against identical terminal truth.
    truth = {"marketTicker": ML_AWAY, "settlementStatus": "SETTLED", "result": "YES"}
    wager = {"marketTicker": ML_AWAY}
    assert wss.resolve_wager_outcome(yes, truth, wager=wager)["outcome"] == wss.OUTCOME_WON
    assert wss.resolve_wager_outcome(no, truth, wager=wager)["outcome"] == wss.OUTCOME_LOST


def test_a_missing_side_never_becomes_yes():
    """The defect this subwave exists to remove, asserted directly."""
    out = side_of(market="ML", marketTicker=ML_AWAY)
    assert out["side"] is None
    assert out["refusalClass"] == wss.REFUSAL_MISSING_EVIDENCE
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_NO_SELECTION


# ─────────────────────────────────────────────────────────────────────────────
# HOME / AWAY, and the full-game binary complement
# ─────────────────────────────────────────────────────────────────────────────

def test_away_selection_on_the_away_teams_own_ticker_is_yes():
    out = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    assert out["side"] == wss.SIDE_YES
    assert out["basis"] == wss.BASIS_EXPRESSION_ASSERTS_CONTRACT_CONDITION


def test_home_selection_on_the_home_teams_own_ticker_is_yes():
    out = side_of(market="ML", betSide="HOME", marketTicker=ML_HOME)
    assert out["side"] == wss.SIDE_YES
    assert out["basis"] == wss.BASIS_EXPRESSION_ASSERTS_CONTRACT_CONDITION


def test_home_selection_on_the_away_teams_ticker_is_the_proven_complement():
    """A full MLB game cannot end level, so 'not COL' IS 'DET'."""
    out = side_of(market="ML", betSide="HOME", marketTicker=ML_AWAY)
    assert out["side"] == wss.SIDE_NO
    assert out["basis"] == wss.BASIS_BINARY_COMPLEMENT


def test_the_complement_is_symmetric_between_home_and_away():
    away_on_home_ticker = side_of(market="ML", betSide="AWAY", marketTicker=ML_HOME)
    home_on_away_ticker = side_of(market="ML", betSide="HOME", marketTicker=ML_AWAY)
    assert away_on_home_ticker["side"] == home_on_away_ticker["side"] == wss.SIDE_NO
    assert away_on_home_ticker["basis"] == home_on_away_ticker["basis"]


def test_full_game_moneyline_is_the_only_wins_family_declared_binary():
    assert wss.is_binary_event_space(
        "TEAM_WINS", wss.HORIZON_FULL_GAME) is True
    for horizon in (wss.HORIZON_F3, wss.HORIZON_F5, wss.HORIZON_F7):
        assert wss.is_binary_event_space("TEAM_WINS", horizon) is False
    # An unlisted pair is False. Absence of a recorded tie is not evidence that
    # there cannot be one.
    assert wss.is_binary_event_space("SOME_FUTURE_CONDITION", "SOME_HORIZON") is False


# ─────────────────────────────────────────────────────────────────────────────
# F3 / F5 / F7 -- the three-way families
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,market", [
    (F3_AWAY, "F3 ML"), (F5_AWAY, "F5 ML"), (F7_AWAY, "F7 ML"),
])
def test_period_moneyline_team_a_yes(ticker, market):
    out = side_of(market=market, betSide="AWAY", marketTicker=ticker)
    assert out["side"] == wss.SIDE_YES


@pytest.mark.parametrize("ticker,market", [
    (F3_AWAY, "F3 ML"), (F5_AWAY, "F5 ML"), (F7_AWAY, "F7 ML"),
])
def test_period_moneyline_team_b_is_NOT_manufactured_into_team_a_no(ticker, market):
    """
    THE THREE-WAY RULE. A first-3/5/7-innings segment can end TIED -- Kalshi
    lists a separate TIE contract in the same event -- so 'DET wins the segment'
    is NOT the complement of 'COL wins the segment'. Manufacturing a binary
    complement here buys a strictly different bet.
    """
    out = side_of(market=market, betSide="HOME", marketTicker=ticker)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_SEGMENT_IS_NOT_BINARY


def test_period_tie_is_its_own_contract_and_settles_as_itself():
    out = side_of(market="F5 TIE", marketTicker=F5_TIE)
    assert out["side"] == wss.SIDE_YES
    assert out["contract"]["condition"] == "PERIOD_TIE"


def test_a_period_moneyline_claim_landing_on_the_tie_contract_refuses():
    out = side_of(market="F5 ML", betSide="AWAY", marketTicker=F5_TIE)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_FAMILY
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_f5_no_and_full_game_no_are_different_answers_on_identical_input_shape():
    """The single clearest statement of the three-way rule: identical rows,
    differing only in horizon, must NOT get the same answer."""
    full_game = side_of(market="ML", betSide="HOME", marketTicker=ML_AWAY)
    segment = side_of(market="F5 ML", betSide="HOME", marketTicker=F5_AWAY)
    assert full_game["side"] == wss.SIDE_NO
    assert segment["side"] is None


# ─────────────────────────────────────────────────────────────────────────────
# NRFI / YRFI -- one ticker, two ends
# ─────────────────────────────────────────────────────────────────────────────

def test_yrfi_and_nrfi_are_opposite_sides_of_the_same_exact_ticker():
    yrfi = side_of(market="YRFI", marketTicker=RFI)
    nrfi = side_of(market="NRFI", marketTicker=RFI)
    assert yrfi["side"] == wss.SIDE_YES
    assert nrfi["side"] == wss.SIDE_NO
    # THE SAME TICKER. Not two markets that happen to be opposites.
    assert yrfi["contract"]["ticker"] == nrfi["contract"]["ticker"] == RFI
    assert yrfi["contract"]["condition"] == nrfi["contract"]["condition"]


def test_yrfi_and_nrfi_grade_inversely_from_one_settlement():
    truth = {"marketTicker": RFI, "settlementStatus": "SETTLED", "result": "YES"}
    wager = {"marketTicker": RFI}
    yrfi = wss.resolve_wager_outcome(side_of(market="YRFI", marketTicker=RFI), truth, wager=wager)
    nrfi = wss.resolve_wager_outcome(side_of(market="NRFI", marketTicker=RFI), truth, wager=wager)
    assert (yrfi["outcome"], nrfi["outcome"]) == (wss.OUTCOME_WON, wss.OUTCOME_LOST)
    no_run = dict(truth, result="NO")
    yrfi = wss.resolve_wager_outcome(side_of(market="YRFI", marketTicker=RFI), no_run, wager=wager)
    nrfi = wss.resolve_wager_outcome(side_of(market="NRFI", marketTicker=RFI), no_run, wager=wager)
    assert (yrfi["outcome"], nrfi["outcome"]) == (wss.OUTCOME_LOST, wss.OUTCOME_WON)


def test_an_rfi_row_is_never_given_an_away_or_home_side():
    """
    KXMLBRFI has no away side to buy. The pre-W1-A resolver returned 'AWAY' for
    42 archived YRFI rows because the away abbreviation appeared inside the
    free-text bet string "LAA/TB YRFI".
    """
    row = {"game": "LAA@TB", "market": "YRFI", "bet": "LAA/TB YRFI"}
    assert clv_update.get_betside(row, "LAA", "TB") is None
    expression, refusal = wss.build_expression(row, away="LAA", home="TB")
    assert refusal is None
    assert expression["selection"] is None          # names the GAME, not a side
    assert expression["direction"] == wss.DIRECTION_EVENT_OCCURS


# ─────────────────────────────────────────────────────────────────────────────
# Totals, team totals, run lines -- strike and team must both be exact
# ─────────────────────────────────────────────────────────────────────────────

def test_game_total_over_and_under_are_symmetric_on_one_ticker():
    over = side_of(market="Total Over", line=9, marketTicker=TOTAL_9)
    under = side_of(market="Total Under", line=9, marketTicker=TOTAL_9)
    assert (over["side"], under["side"]) == (wss.SIDE_YES, wss.SIDE_NO)
    assert over["contract"]["minimumInclusive"] == under["contract"]["minimumInclusive"] == 9


def test_game_total_with_no_direction_refuses_rather_than_defaulting_to_over():
    out = side_of(market="Total", line=9, marketTicker=TOTAL_9)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_NO_DIRECTION


def test_game_total_on_the_wrong_strike_is_a_contradiction():
    out = side_of(market="Total Over", line=10, marketTicker=TOTAL_9)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_THRESHOLD
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_team_total_requires_the_exact_team_and_the_exact_strike():
    assert side_of(market="TT_Away_Over", line=4, marketTicker=TT_COL_4)["side"] == wss.SIDE_YES
    assert side_of(market="TT_Home_Over", line=4, marketTicker=TT_DET_4)["side"] == wss.SIDE_YES
    assert side_of(market="TT_Away_Under", line=4, marketTicker=TT_COL_4)["side"] == wss.SIDE_NO
    assert side_of(market="TT_Home_Under", line=4, marketTicker=TT_DET_4)["side"] == wss.SIDE_NO


def test_the_opposing_teams_total_can_never_substitute_for_this_one():
    """
    'DET scores 4+' is not the complement of 'COL scores 4+' -- both can be
    true, and both can be false. A sibling club's threshold contract is a
    DIFFERENT CONTRACT, and this must be a contradiction, never a complement.
    """
    out = side_of(market="TT_Home_Over", line=4, marketTicker=TT_COL_4)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_SIBLING_TEAM_IS_A_DIFFERENT_CONTRACT
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_run_line_needs_the_exact_team_and_the_exact_encoded_margin():
    # The ledger writes 1.5; the ticker encodes rung 2 ("wins by over 1.5").
    assert side_of(market="Run Line", side="COL", betSide="OVER", line=1.5,
                   marketTicker=RL_COL_2)["side"] == wss.SIDE_YES
    # A different rung is a different contract.
    assert side_of(market="Run Line", side="COL", betSide="OVER", line=2.5,
                   marketTicker=RL_COL_2)["refusalReason"] == wss.SIDE_CONTRADICTED_THRESHOLD
    # The sibling club's spread is a different contract, not a complement.
    assert side_of(market="Run Line", side="DET", betSide="OVER", line=1.5,
                   marketTicker=RL_COL_2)["refusalReason"] == \
        wss.SIDE_CONTRADICTED_SIBLING_TEAM_IS_A_DIFFERENT_CONTRACT


def test_run_line_direction_is_never_inferred_from_the_price_or_the_label():
    out = side_of(market="Run Line", side="COL", line=1.5, marketTicker=RL_COL_2)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_NO_DIRECTION


# ─────────────────────────────────────────────────────────────────────────────
# Null, unknown and contradictory side evidence
# ─────────────────────────────────────────────────────────────────────────────

def test_null_side_refuses():
    out = side_of(market="Total", line=9, marketTicker=TOTAL_9, betSide=None, side=None)
    assert out["side"] is None
    assert out["refusalClass"] == wss.REFUSAL_MISSING_EVIDENCE


def test_unknown_side_token_refuses_and_says_the_token_was_unreadable():
    out = side_of(market="ML", betSide="LEFT", marketTicker=ML_AWAY)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_UNRECOGNIZED_TOKEN


def test_contradictory_side_fields_refuse_as_a_contradiction_not_a_gap():
    out = side_of(market="ML", betSide="AWAY", side="DET", marketTicker=ML_AWAY)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_FIELDS_DISAGREE
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_a_declared_side_that_contradicts_the_rows_own_expression_refuses():
    out = side_of(market="NRFI", betSide="YES", marketTicker=RFI)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_DECLARED_VS_EXPRESSION


def test_missing_evidence_and_contradiction_stay_distinguishable():
    missing = side_of(market="ML", marketTicker=ML_AWAY)
    contradicted = side_of(market="ML", betSide="AWAY", side="DET", marketTicker=ML_AWAY)
    assert missing["refusalClass"] == wss.REFUSAL_MISSING_EVIDENCE
    assert contradicted["refusalClass"] == wss.REFUSAL_CONTRADICTION
    assert missing["refusalClass"] != contradicted["refusalClass"]


def test_mixed_case_legacy_aliases_are_accepted():
    for spelling in ("Away", "AWAY", "away"):
        assert side_of(market="ML", betSide=spelling, marketTicker=ML_AWAY)["side"] == wss.SIDE_YES
    # Legacy club spellings the corpus actually contains.
    assert wss.same_team("AZ", "ARI")
    assert wss.same_team("OAK", "ATH")


def test_an_unknown_club_string_is_never_turned_into_a_team():
    """`clv_update.to_abbr` ended with 'first 3 chars uppercased'. Nothing here
    does, so 'Over' is not a club called OVE and a surname is not a club."""
    for junk in ("Over", "Rodriguez", "OVE", "", "Sox"):
        assert wss.normalize_team(junk) is None


# ─────────────────────────────────────────────────────────────────────────────
# Wrong contract, wrong game, wrong leg
# ─────────────────────────────────────────────────────────────────────────────

def test_a_wrong_ticker_refuses_rather_than_resolving():
    out = side_of(market="ML", side="NYY", marketTicker=ML_AWAY)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_CONTRADICTED_TEAM_NOT_ON_THIS_CONTRACT


def test_a_settlement_for_a_different_ticker_can_never_grade_this_wager():
    proven = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    out = wss.resolve_wager_outcome(
        proven,
        {"marketTicker": ML_HOME, "settlementStatus": "SETTLED", "result": "YES"},
        wager={"marketTicker": ML_AWAY})
    assert out["outcome"] == wss.OUTCOME_UNRESOLVED
    assert out["refusalReason"] == wss.SETTLEMENT_CONTRADICTED_TICKER
    assert out["ledgerResult"] is None


def test_a_correct_ticker_assigned_to_the_wrong_gamepk_refuses():
    proven = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    out = wss.resolve_wager_outcome(
        proven,
        {"marketTicker": ML_AWAY, "gameId": "823357",
         "settlementStatus": "SETTLED", "result": "YES"},
        wager={"marketTicker": ML_AWAY, "gameId": "823356"})
    assert out["outcome"] == wss.OUTCOME_UNRESOLVED
    assert out["refusalReason"] == wss.SETTLEMENT_CONTRADICTED_GAME
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_an_unresolved_market_leaves_the_wager_not_terminal_not_lost():
    proven = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    out = wss.resolve_wager_outcome(
        proven, {"marketTicker": ML_AWAY, "settlementStatus": "SETTLEMENT_UNRESOLVED",
                 "result": None, "unavailableReason": "missing_final_score"},
        wager={"marketTicker": ML_AWAY})
    assert out["outcome"] == wss.OUTCOME_NOT_TERMINAL
    assert out["ledgerResult"] is None
    assert out["evidence"]["marketUnavailableReason"] == "missing_final_score"


def test_a_void_market_voids_the_wager():
    proven = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    out = wss.resolve_wager_outcome(
        proven, {"marketTicker": ML_AWAY, "settlementStatus": "VOID", "result": None},
        wager={"marketTicker": ML_AWAY})
    assert out["outcome"] == wss.OUTCOME_VOID
    assert out["ledgerResult"] == "VOID"


def test_not_terminal_and_unresolved_are_never_the_same_state():
    proven = side_of(market="ML", betSide="AWAY", marketTicker=ML_AWAY)
    unproven = side_of(market="ML", marketTicker=ML_AWAY)
    still_playing = wss.resolve_wager_outcome(
        proven, {"marketTicker": ML_AWAY, "settlementStatus": None, "result": None},
        wager={"marketTicker": ML_AWAY})
    refused = wss.resolve_wager_outcome(
        unproven, {"marketTicker": ML_AWAY, "settlementStatus": "SETTLED", "result": "YES"},
        wager={"marketTicker": ML_AWAY})
    assert still_playing["outcome"] == wss.OUTCOME_NOT_TERMINAL
    assert refused["outcome"] == wss.OUTCOME_UNRESOLVED
    # Both write nothing to `result`, and they are still distinguishable.
    assert still_playing["ledgerResult"] is refused["ledgerResult"] is None
    assert still_playing["outcome"] != refused["outcome"]


# ─────────────────────────────────────────────────────────────────────────────
# Doubleheader composition with W1-C
# ─────────────────────────────────────────────────────────────────────────────
# Two physical games, same date, same clubs, different gamePk, different Kalshi
# event (different HHMM) and therefore different tickers.
DH_LEG1_EVENT = "26JUL111605MILPIT"
DH_LEG2_EVENT = "26JUL111915MILPIT"
DH_LEG1_TICKER = "KXMLBGAME-%s-MIL" % DH_LEG1_EVENT
DH_LEG2_TICKER = "KXMLBGAME-%s-MIL" % DH_LEG2_EVENT
DH_LEG1_GAMEPK = "823356"
DH_LEG2_GAMEPK = "823357"


def _dh_wager(ticker, game_pk):
    return {"game": "MIL@PIT", "market": "ML", "betSide": "AWAY",
            "marketTicker": ticker, "gameId": game_pk}


def test_doubleheader_leg1_cannot_settle_from_leg2_truth():
    wager = _dh_wager(DH_LEG1_TICKER, DH_LEG1_GAMEPK)
    out = wss.resolve_wager_outcome(
        wss.resolve_wager_side(wager),
        {"marketTicker": DH_LEG2_TICKER, "gameId": DH_LEG2_GAMEPK,
         "settlementStatus": "SETTLED", "result": "YES"},
        wager=wager)
    assert out["outcome"] == wss.OUTCOME_UNRESOLVED
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_doubleheader_leg2_cannot_settle_from_leg1_truth():
    wager = _dh_wager(DH_LEG2_TICKER, DH_LEG2_GAMEPK)
    out = wss.resolve_wager_outcome(
        wss.resolve_wager_side(wager),
        {"marketTicker": DH_LEG1_TICKER, "gameId": DH_LEG1_GAMEPK,
         "settlementStatus": "SETTLED", "result": "YES"},
        wager=wager)
    assert out["outcome"] == wss.OUTCOME_UNRESOLVED
    assert out["refusalClass"] == wss.REFUSAL_CONTRADICTION


def test_doubleheader_exact_ticker_and_gamepk_settles_correctly():
    for ticker, game_pk in ((DH_LEG1_TICKER, DH_LEG1_GAMEPK),
                            (DH_LEG2_TICKER, DH_LEG2_GAMEPK)):
        wager = _dh_wager(ticker, game_pk)
        out = wss.resolve_wager_outcome(
            wss.resolve_wager_side(wager),
            {"marketTicker": ticker, "gameId": game_pk,
             "settlementStatus": "SETTLED", "result": "YES"},
            wager=wager)
        assert out["outcome"] == wss.OUTCOME_WON


def test_doubleheader_ambiguous_legacy_evidence_refuses():
    """A row with only a date and a matchup names no leg. Both legs exist, so
    there is nothing to settle it against -- and W1-A must not pick one."""
    wager = {"game": "MIL@PIT", "date": "2026-07-11", "market": "ML", "betSide": "AWAY"}
    out = wss.resolve_wager_side(wager)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_NO_CONTRACT


def test_the_two_legs_are_genuinely_different_contracts():
    """Guards the fixture itself: if these ever became one ticker the three
    tests above would pass vacuously."""
    assert DH_LEG1_TICKER != DH_LEG2_TICKER
    assert DH_LEG1_GAMEPK != DH_LEG2_GAMEPK
    leg1 = wss.resolve_wager_side(_dh_wager(DH_LEG1_TICKER, DH_LEG1_GAMEPK))
    leg2 = wss.resolve_wager_side(_dh_wager(DH_LEG2_TICKER, DH_LEG2_GAMEPK))
    assert leg1["side"] == leg2["side"] == wss.SIDE_YES
    assert leg1["contract"]["eventTickerSuffix"] != leg2["contract"]["eventTickerSuffix"]


# ─────────────────────────────────────────────────────────────────────────────
# Player props -- issue #43
# ─────────────────────────────────────────────────────────────────────────────

def test_player_prop_defers_and_says_so_in_the_refusal_class():
    for wager in ({"game": GAME, "market": "K Prop"},
                  {"game": GAME, "market": "Pitcher Prop"},
                  {"game": GAME, "market": "ML", "marketTicker": PROP},
                  {"game": GAME, "marketFamily": "pitcher_strikeouts"}):
        out = wss.resolve_wager_side(wager)
        assert out["side"] is None, wager
        assert out["refusalClass"] == wss.REFUSAL_DEFERRED, wager
        assert out["refusalReason"] == wss.SIDE_DEFERRED_PLAYER_PROP, wager


def test_a_player_prop_deferral_never_becomes_a_grade_even_with_terminal_truth():
    wager = {"game": GAME, "market": "K Prop", "marketTicker": PROP}
    out = wss.resolve_wager_outcome(
        wss.resolve_wager_side(wager),
        {"marketTicker": PROP, "settlementStatus": "SETTLED", "result": "YES"},
        wager=wager)
    assert out["outcome"] == wss.OUTCOME_UNRESOLVED
    assert out["refusalReason"] == wss.SETTLEMENT_DEFERRED_PLAYER_PROP
    assert out["ledgerResult"] is None


def test_a_player_prop_bet_string_is_never_parsed_into_a_market_claim():
    """'Sale K Over 8' must not yield a total of 8."""
    for text in ("Sale K Over 8", "Rodriguez K Under 5.5", "Bassitt ER Over 2.5"):
        assert wss.read_bet_string(text) is None


def test_determine_result_declines_player_props():
    scores = {("COL", "DET"): {"away_score": 5, "home_score": 3, "completed": True}}
    for market in ("K Prop", "Pitcher Prop"):
        result, _, _ = clv_update.determine_result(
            {"game": GAME, "market": market, "bet": "Sale K Over 8"},
            scores, "COL", "DET", market)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# A recommendation is not a wager
# ─────────────────────────────────────────────────────────────────────────────

def test_resolving_a_side_never_creates_a_wager_or_a_settlement():
    """
    The canonical module is pure. It reads a row and returns a verdict; it
    opens no file, writes no ledger and mutates no input. A recommendation
    handed to it gets a verdict about a side and nothing else -- no wager
    appears anywhere as a result.
    """
    recommendation = {"game": GAME, "market": "ML", "betSide": "AWAY",
                      "marketTicker": ML_AWAY, "status": "RECOMMENDED"}
    before = json.dumps(recommendation, sort_keys=True)
    out = wss.resolve_wager_side(recommendation)
    assert json.dumps(recommendation, sort_keys=True) == before  # input untouched
    assert out["side"] == wss.SIDE_YES
    assert "betId" not in out and "stake" not in out and "netProfitLoss" not in out


def test_settle_bets_for_ticker_creates_no_bet_from_an_empty_bet_list():
    assert edgelab_settlement.settle_bets_for_ticker([], "SETTLED", "YES") == []


def test_a_settled_market_with_no_placed_bet_produces_no_wager():
    """Settlement of an observed market is research; it is not a P/L event."""
    settled = edgelab_settlement.settle_bets_for_ticker([], "SETTLED", "NO")
    assert settled == []


# ─────────────────────────────────────────────────────────────────────────────
# EdgeLab seam: the `or "YES"` default is gone
# ─────────────────────────────────────────────────────────────────────────────

def test_a_placed_bet_with_no_side_is_refused_not_graded_as_yes():
    bet = {"betId": "x", "marketTicker": ML_AWAY, "side": None,
           "stake": 10.0, "entryPrice": 0.3, "status": "pending", "result": None}
    settled = edgelab_settlement.settle_bets_for_ticker([bet], "SETTLED", "YES")
    assert len(settled) == 1
    assert settled[0]["result"] is None
    assert settled[0]["status"] == "pending"
    assert settled[0].get("netProfitLoss") is None
    assert settled[0].get("returnAmount") is None
    assert settled[0]["settlementRefusalReason"] == wss.SETTLEMENT_UNPROVEN_SIDE
    assert settled[0]["settlementRefusalClass"] == wss.REFUSAL_MISSING_EVIDENCE


def test_a_placed_bet_with_a_proven_side_still_grades_both_ways():
    for side, result, expected in (("YES", "YES", "WIN"), ("YES", "NO", "LOSS"),
                                   ("NO", "NO", "WIN"), ("NO", "YES", "LOSS")):
        bet = {"betId": "x", "marketTicker": ML_AWAY, "side": side,
               "stake": 10.0, "entryPrice": 0.5, "status": "pending", "result": None}
        settled = edgelab_settlement.settle_bets_for_ticker([bet], "SETTLED", result)
        assert settled[0]["result"] == expected
        assert settled[0]["status"] == "settled"
        assert settled[0].get("settlementRefusalReason") is None


def test_derive_bet_side_refuses_rather_than_defaulting_to_yes():
    """lib.edgelab.bets._derive_side used to answer YES for everything that was
    not NRFI. A row with no contract now gets null and a recorded reason."""
    from lib.edgelab import bets as edgelab_bets
    side, basis, refusal, refusal_class = edgelab_bets._derive_side(
        {"game": GAME, "market": "Total", "bet": "Total Under 8.5", "line": 8.5})
    assert side is None
    assert refusal == wss.SIDE_UNPROVEN_NO_CONTRACT
    assert refusal_class == wss.REFUSAL_MISSING_EVIDENCE
    assert basis is None
    # And an Under on a real contract is NO, which the old rule called YES.
    side, _, _, _ = edgelab_bets._derive_side(
        {"game": GAME, "market": "Total Under", "line": 9, "marketTicker": TOTAL_9})
    assert side == wss.SIDE_NO


# ─────────────────────────────────────────────────────────────────────────────
# Idempotence
# ─────────────────────────────────────────────────────────────────────────────

def test_resolving_the_same_row_twice_gives_the_same_answer():
    row = {"game": GAME, "market": "TT_Away_Over", "line": 4, "marketTicker": TT_COL_4}
    first = wss.resolve_wager_side(dict(row))
    second = wss.resolve_wager_side(dict(row))
    assert json.dumps(first, sort_keys=True, default=str) == \
        json.dumps(second, sort_keys=True, default=str)


def test_settling_the_same_bet_twice_does_not_double_count_or_flip():
    bet = {"betId": "x", "marketTicker": ML_AWAY, "side": "YES",
           "stake": 10.0, "entryPrice": 0.5, "status": "pending", "result": None}
    first = edgelab_settlement.settle_bets_for_ticker([bet], "SETTLED", "YES")[0]
    second = edgelab_settlement.settle_bets_for_ticker([first], "SETTLED", "YES")[0]
    assert first["result"] == second["result"] == "WIN"
    assert first["netProfitLoss"] == second["netProfitLoss"]
    # The second pass is a genuine no-op and must not be written again.
    assert edgelab_settlement.bet_needs_settlement_update(first, second) is False


def test_re_refusing_an_unproven_bet_is_a_no_op():
    """A refusal must be as idempotent as a grade -- otherwise every nightly run
    rewrites the same row with a fresh timestamp forever."""
    bet = {"betId": "x", "marketTicker": ML_AWAY, "side": None,
           "stake": 10.0, "entryPrice": 0.3, "status": "pending", "result": None}
    first = edgelab_settlement.settle_bets_for_ticker([bet], "SETTLED", "YES")[0]
    second = edgelab_settlement.settle_bets_for_ticker([first], "SETTLED", "YES")[0]
    assert edgelab_settlement.bet_needs_settlement_update(first, second) is False


def test_resolution_never_mutates_the_row_it_reads():
    row = {"game": GAME, "market": "ML", "betSide": "AWAY", "marketTicker": ML_AWAY}
    snapshot = json.dumps(row, sort_keys=True)
    wss.resolve_wager_side(row)
    wss.build_expression(row)
    assert json.dumps(row, sort_keys=True) == snapshot


# ─────────────────────────────────────────────────────────────────────────────
# Legacy vocabulary normalization -- raw preserved beside canonical
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("WIN", wss.OUTCOME_WON), ("WON", wss.OUTCOME_WON), ("W", wss.OUTCOME_WON),
    ("LOSS", wss.OUTCOME_LOST), ("LOST", wss.OUTCOME_LOST), ("L", wss.OUTCOME_LOST),
    ("PUSH", wss.OUTCOME_PUSH),
    ("VOID", wss.OUTCOME_VOID), ("voided", wss.OUTCOME_VOID),
    ("CANCELLED", wss.OUTCOME_VOID), ("NO_ACTION", wss.OUTCOME_VOID),
    ("PENDING", wss.OUTCOME_NOT_TERMINAL), ("open", wss.OUTCOME_NOT_TERMINAL),
])
def test_legacy_result_spellings_normalize(raw, expected):
    assert wss.normalize_result(raw) == expected


def test_a_lifecycle_or_bet_class_status_is_never_read_as_an_outcome():
    assert wss.normalize_result("SETTLED") is None
    assert wss.normalize_result("PAPER") is None
    assert wss.is_bet_class_status("PAPER") is True
    assert wss.normalize_lifecycle("SETTLED") == "settled"
    assert wss.normalize_lifecycle("PAPER") is None


def test_normalization_preserves_the_raw_values_beside_the_canonical_reading():
    row = {"result": "WIN", "status": "SETTLED"}
    out = wss.normalize_row_semantics(row)
    assert out["rawResult"] == "WIN" and out["rawStatus"] == "SETTLED"
    assert out["canonicalOutcome"] == wss.OUTCOME_WON
    assert out["canonicalLifecycle"] == "settled"
    # And the source row is untouched -- normalization is additive, not a
    # destructive rewrite of history.
    assert row == {"result": "WIN", "status": "SETTLED"}


def test_every_canonical_outcome_maps_to_exactly_one_ledger_shape():
    for outcome in (wss.OUTCOME_WON, wss.OUTCOME_LOST, wss.OUTCOME_PUSH,
                    wss.OUTCOME_VOID, wss.OUTCOME_NOT_TERMINAL, wss.OUTCOME_UNRESOLVED):
        assert outcome in wss.LEDGER_RESULT_BY_OUTCOME
        assert outcome in wss.LEDGER_STATUS_BY_OUTCOME
    # The two non-terminal states write no result, and only the terminal ones do.
    assert wss.LEDGER_RESULT_BY_OUTCOME[wss.OUTCOME_UNRESOLVED] is None
    assert wss.LEDGER_RESULT_BY_OUTCOME[wss.OUTCOME_NOT_TERMINAL] is None
    for outcome in wss.TERMINAL_OUTCOMES:
        assert wss.LEDGER_RESULT_BY_OUTCOME[outcome] is not None


def test_every_refusal_reason_carries_a_class():
    module_reasons = {
        value for name, value in vars(wss).items()
        if name.startswith(("SIDE_UNPROVEN_", "SIDE_CONTRADICTED_", "SIDE_DEFERRED_",
                            "SETTLEMENT_UNPROVEN_", "SETTLEMENT_CONTRADICTED_",
                            "SETTLEMENT_DEFERRED_"))
        and isinstance(value, str)
    }
    unclassified = sorted(r for r in module_reasons if r not in wss.REFUSAL_CLASS_BY_REASON)
    assert unclassified == [], "refusal reasons with no class: %s" % unclassified


# ─────────────────────────────────────────────────────────────────────────────
# clv_update: the removed defaults, asserted as removed
# ─────────────────────────────────────────────────────────────────────────────

SCORES = {("COL", "DET"): {"away_score": 5, "home_score": 3, "completed": True}}


def test_moneyline_with_an_unproven_side_is_declined_not_graded_loss():
    result, _, _ = clv_update.determine_result(
        {"game": GAME, "market": "ML"}, SCORES, "COL", "DET", "ML")
    assert result is None


def test_run_line_with_an_unproven_side_is_declined_not_graded_as_away():
    result, _, _ = clv_update.determine_result(
        {"game": GAME, "market": "Run Line", "line": 1.5}, SCORES, "COL", "DET", "Run Line")
    assert result is None


def test_total_with_an_unproven_direction_is_declined_not_graded_as_over():
    result, _, _ = clv_update.determine_result(
        {"game": GAME, "market": "Total", "line": 7.5}, SCORES, "COL", "DET", "Total")
    assert result is None


def test_team_total_with_an_unproven_team_or_direction_is_declined():
    assert clv_update.determine_result(
        {"game": GAME, "market": "Team Total", "line": 4}, SCORES, "COL", "DET",
        "Team Total")[0] is None
    # Team proven, direction not.
    assert clv_update.determine_result(
        {"game": GAME, "market": "Team Total", "line": 4, "betSide": "AWAY"},
        SCORES, "COL", "DET", "Team Total")[0] is None


def test_total_grades_both_directions_when_the_direction_is_proven():
    # 5 + 3 = 8 against a line of 7.5.
    assert clv_update.determine_result(
        {"game": GAME, "market": "Total", "line": 7.5, "betSide": "OVER"},
        SCORES, "COL", "DET", "Total")[0] == "WIN"
    assert clv_update.determine_result(
        {"game": GAME, "market": "Total", "line": 7.5, "betSide": "UNDER"},
        SCORES, "COL", "DET", "Total")[0] == "LOSS"


def test_team_total_grades_both_teams_and_both_directions():
    # COL (away) 5, DET (home) 3, line 4.
    cases = [("AWAY", "OVER", "WIN"), ("AWAY", "UNDER", "LOSS"),
             ("HOME", "OVER", "LOSS"), ("HOME", "UNDER", "WIN")]
    for orientation, direction, expected in cases:
        row = {"game": GAME, "market": "Team Total", "line": 4,
               "betSide": "%s %s" % (orientation, direction)}
        assert clv_update.determine_result(
            row, SCORES, "COL", "DET", "Team Total")[0] == expected, row


def test_get_betside_never_returns_home_for_an_unrecognized_team():
    """The `if ta: return 'HOME'` default, asserted gone."""
    assert clv_update.get_betside(
        {"game": GAME, "betTeam": "Nobody FC"}, "COL", "DET") is None
    assert clv_update.get_betside(
        {"game": GAME, "betTeam": "NYY"}, "COL", "DET") is None


def test_get_betside_does_not_substring_match_free_text():
    row = {"game": "COL@DET", "market": "YRFI", "bet": "COL/DET YRFI"}
    assert clv_update.get_betside(row, "COL", "DET") is None


def test_extract_closing_is_dead_code_and_stays_uncalled():
    """
    It contains four of the side defaults this subwave removed and is reachable
    only by someone re-wiring it. If that ever happens, this fails and they read
    the docstring first.
    """
    with open(os.path.join(ROOT, "clv_update.py")) as handle:
        source = handle.read()
    assert source.count("extract_closing(") == 1, (
        "extract_closing is documented as dead code but now has a caller; it must "
        "be rewritten against wager_settlement_semantics before being used.")


# ─────────────────────────────────────────────────────────────────────────────
# Composition with W1-C -- settlement must not re-parse tickers
# ─────────────────────────────────────────────────────────────────────────────

def test_settlement_semantics_delegates_ticker_parsing_to_w1c():
    """
    The module must not carry its own ticker grammar. Two independent checks:
    it imports W1-C's parser, and it declares no series table of its own.
    """
    import lib.kalshi_mlb_contract_parser as kmcp
    assert wss.kmcp is kmcp
    source_path = os.path.join(ROOT, "lib", "wager_settlement_semantics.py")
    with open(source_path) as handle:
        source = handle.read()
    for series in ("KXMLBGAME", "KXMLBF5", "KXMLBTOTAL", "KXMLBTEAMTOTAL", "KXMLBRFI"):
        assert "\"%s\":" % series not in source, (
            "%s: settlement code must not restate the contract grammar; "
            "lib/kalshi_mlb_contract_parser.py owns it." % series)


def test_the_canonical_side_vocabulary_is_w1cs_own():
    from lib.edgelab import market_identity as mi
    assert (wss.SIDE_YES, wss.SIDE_NO) == (mi.SIDE_YES, mi.SIDE_NO)
    assert wss.DIRECTION_OVER == mi.DIRECTION_OVER
    assert wss.HORIZON_F5 == mi.HORIZON_F5


def test_an_undescribed_series_refuses_rather_than_assuming_a_moneyline():
    out = side_of(market="ML", betSide="AWAY", marketTicker="KXNOTAREALSERIES-%s-COL" % EVENT)
    assert out["side"] is None
    assert out["refusalReason"] == wss.SIDE_UNPROVEN_SERIES_NOT_DESCRIBED


def test_a_malformed_suffix_on_a_known_series_refuses_instead_of_crashing():
    """
    Regression. `_refusal(..., reason=contract.get("reason"))` collided with
    _refusal's own positional parameter and raised TypeError -- reachable from
    ANY ticker whose series is known but whose market suffix is malformed, i.e.
    the exact class of row this module exists to refuse safely. Both helpers now
    take underscore-prefixed positionals so no evidence key can collide.
    """
    for bad in ("KXMLBGAME-%s-NOTATEAM" % EVENT,      # team not in the event
                "KXMLBTEAMTOTAL-%s-COL" % EVENT,      # team_count suffix with no count
                "KXMLBTOTAL-%s-NINE" % EVENT,         # count suffix that is not a number
                "KXMLBGAME-%s" % EVENT,               # no market suffix at all
                "KXMLBRFI-%s-EXTRA" % EVENT):         # suffix on a no-suffix series
        out = side_of(market="ML", betSide="AWAY", marketTicker=bad)
        assert out["side"] is None, bad
        assert out["refusalReason"] in (
            wss.SIDE_UNPROVEN_CONTRACT_UNPARSEABLE,
            wss.SIDE_CONTRADICTED_HORIZON,
            wss.SIDE_CONTRADICTED_FAMILY,
        ), (bad, out["refusalReason"])


def test_every_row_in_both_committed_ledgers_resolves_without_raising():
    """
    The module is handed every canonical wager this repository actually holds.
    It must return a verdict for each -- a side or a classified refusal -- and
    must not raise on any of them. A settlement resolver that crashes on a real
    row is not fail-closed; it is just failed.
    """
    rows = []
    with open(os.path.join(ROOT, "bets.json")) as handle:
        rows.extend(json.load(handle))
    edgelab = os.path.join(ROOT, "data", "edgelab", "bets", "bets.jsonl")
    if os.path.exists(edgelab):
        with open(edgelab) as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    assert rows, "no committed wagers found -- this test would be vacuous"
    for row in rows:
        out = wss.resolve_wager_side(row)
        assert (out["side"] in (wss.SIDE_YES, wss.SIDE_NO)) != (out["refusalReason"] is not None), (
            "a row must be either resolved or refused, never both and never neither: %s"
            % (row.get("id") or row.get("betId")))
        if out["side"] is None:
            assert out["refusalClass"] in (
                wss.REFUSAL_MISSING_EVIDENCE, wss.REFUSAL_CONTRADICTION,
                wss.REFUSAL_DEFERRED), (row.get("id"), out["refusalReason"])
