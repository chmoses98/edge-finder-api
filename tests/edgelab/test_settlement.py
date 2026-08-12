#!/usr/bin/env python3
"""
tests/edgelab/test_settlement.py
====================================
Coverage for lib/edgelab/settlement.py: win/loss/void, three-way F3/F5/F7
tie handling, unimplemented player props, and unbet-market hypothetical
returns.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.settlement import (
    bet_needs_settlement_update,
    build_settlement_record,
    compare_confirmed_receipt_to_settlement,
    derive_bet_result,
    hypothetical_yes_return,
    merge_settlement_record,
    realized_return_for_bet,
    settle_bets_for_ticker,
    settle_market,
    was_market_ever_recommended,
)

GAME_RESULT = "game_result"
INNING_RESULT = "inning_result"
GAME_TOTAL = "game_total"
INNING_TOTAL = "inning_total"
TEAM_TOTAL = "team_total"
WINNING_MARGIN = "winning_margin"
FIRST_INNING_RUN = "first_inning_run"


def test_moneyline_away_ticker_wins():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"awayRuns": 5, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_moneyline_home_ticker_loses_when_away_wins():
    market = {"marketFamily": GAME_RESULT, "team": "CIN", "outcomeLabel": "Win"}
    outcome = {"awayRuns": 5, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_void_on_postponed_game():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"gameStatus": "Postponed"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("VOID", None, None)


def test_missing_final_score_is_unresolved_not_guessed():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"awayRuns": None, "homeRuns": None, "gameStatus": "In Progress"}
    status, result, reason = settle_market(market, outcome)
    assert status == "SETTLEMENT_UNRESOLVED"
    assert reason == "missing_final_score"
    assert result is None


def test_f5_tie_ticker_settles_yes_on_actual_tie():
    market = {"marketFamily": INNING_RESULT, "marketHorizon": "F5", "outcomeLabel": "Tie"}
    outcome = {"periodScores": {"F5": (2, 2)}, "completedInnings": 5, "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_f5_team_ticker_settles_no_when_actually_tied():
    market = {"marketFamily": INNING_RESULT, "marketHorizon": "F5", "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"periodScores": {"F5": (2, 2)}, "awayAbbr": "PIT", "homeAbbr": "CIN", "completedInnings": 5, "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_f3_and_f7_three_way_settle_same_as_f5_once_confirmed():
    # lib.research.market_taxonomy.HORIZON_MARKET_STATUS currently marks
    # F3/F5/F7 all CONFIRMED_THREE_WAY (see that module for the evidence);
    # settle_inning_result() dispatches on that single source of truth, so
    # F3/F7 settle exactly like F5 today, with no per-horizon special case
    # in this module. If a future Kalshi change ever retracts F3/F7's
    # confirmation, HORIZON_MARKET_STATUS flips back to
    # SETTLEMENT_UNRESOLVED/"structure_unverified" with no change needed here.
    for horizon in ("F3", "F5", "F7"):
        market = {"marketFamily": INNING_RESULT, "marketHorizon": horizon, "team": "PIT", "outcomeLabel": "Win"}
        outcome = {"periodScores": {horizon: (3, 1)}, "awayAbbr": "PIT", "homeAbbr": "CIN", "completedInnings": 9, "gameStatus": "Final"}
        status, result, reason = settle_market(market, outcome)
        assert (status, result, reason) == ("SETTLED", "YES", None)


def test_player_props_are_explicitly_unimplemented_not_fabricated():
    for family in ("pitcher_strikeouts", "hitter_hits", "hitter_total_bases", "hitter_rbis"):
        market = {"marketFamily": family, "threshold": 5.5}
        outcome = {"gameStatus": "Final"}
        status, result, reason = settle_market(market, outcome)
        assert status == "SETTLEMENT_UNRESOLVED"
        assert reason == "player_prop_settlement_not_implemented"
        assert result is None


def test_team_total_and_winning_margin_and_game_total():
    outcome = {"awayRuns": 6, "homeRuns": 2, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "PIT", "threshold": 3.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "CIN", "threshold": 3.5}, outcome)[:2] == ("SETTLED", "NO")
    assert settle_market({"marketFamily": WINNING_MARGIN, "team": "PIT", "threshold": 2.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": GAME_TOTAL, "threshold": 7.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": GAME_TOTAL, "threshold": 8.5}, outcome)[:2] == ("SETTLED", "NO")


def test_team_total_and_winning_margin_still_require_team_no_regression():
    """
    The inning_total fix (below) must not loosen team_total/winning_margin's
    genuine team-resolution requirement -- these two families ARE team-
    specific (Kalshi's 'SF11'-style suffix encodes team+line together, see
    lib.research.market_taxonomy._team_and_margin_from_suffix), unlike
    game_total/inning_total which are combined totals with no team at all.
    """
    outcome = {"awayRuns": 6, "homeRuns": 2, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    for family in (TEAM_TOTAL, WINNING_MARGIN):
        status, result, reason = settle_market({"marketFamily": family, "threshold": 3.5}, outcome)
        assert (status, result, reason) == ("SETTLEMENT_UNRESOLVED", None, "team_not_resolved")
        status, result, reason = settle_market({"marketFamily": family, "team": "NYY", "threshold": 3.5}, outcome)
        assert (status, result, reason) == ("SETTLEMENT_UNRESOLVED", None, "team_not_resolved")


# ---------------------------------------------------------------------------
# inning_total (F3/F5/F7 combined-period totals -- KXMLBF3TOTAL/KXMLBF5TOTAL/
# KXMLBF7TOTAL, see lib.research.market_taxonomy.SERIES_FAMILY_MAP). This
# family is a period-scoped sibling of FAMILY_GAME_TOTAL (identical
# strict-integer-suffix parsing -- see market_taxonomy._total_line_from_suffix's
# own docstring, "Shared by FAMILY_GAME_TOTAL and FAMILY_INNING_TOTAL"), never
# team-specific: real archived MarketObservation rows for this family always
# carry team=None (confirmed against production data/edgelab/observations/).
# Previously fell through into the team-required branch shared with
# FAMILY_TEAM_TOTAL/FAMILY_WINNING_MARGIN, so it could never settle
# ("team_not_resolved" on ~100% of observed inning_total markets, every
# date) -- fixed by returning alongside FAMILY_GAME_TOTAL, before that
# branch.
# ---------------------------------------------------------------------------

def test_inning_total_yes_win_when_period_total_exceeds_threshold():
    market = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 3}
    outcome = {"periodScores": {"F5": (2, 2)}}  # 4 > 3
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_inning_total_no_when_period_total_is_under_threshold():
    market = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 5}
    outcome = {"periodScores": {"F5": (1, 1)}}  # 2 < 5
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_inning_total_exact_threshold_is_no_never_a_push():
    """Kalshi's integer-suffix total contract is strictly 'over N' -- landing exactly on N settles NO, never a push."""
    market = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 4}
    outcome = {"periodScores": {"F5": (2, 2)}}  # 4 == 4
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_inning_total_never_requires_or_uses_team_identity():
    """
    Root-cause regression guard: an inning_total market must settle
    correctly with NO team field at all (the real-world/normal case), and
    must settle IDENTICALLY even if a malformed/legacy row somehow carries
    a team value -- this family's own result never depends on team
    identity, so team presence must never change the outcome or block
    settlement (never guessing which side a team-shaped field maps to).
    """
    outcome = {"periodScores": {"F5": (3, 2)}, "awayAbbr": "PIT", "homeAbbr": "CIN"}  # 5 > 4.5
    no_team = settle_market({"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 4.5}, outcome)
    with_stray_team = settle_market(
        {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 4.5, "team": "PIT"}, outcome,
    )
    assert no_team == with_stray_team == ("SETTLED", "YES", None)


def test_inning_total_f3_f5_f7_horizons_pull_their_own_period_score():
    for horizon, period, threshold, expected in (
        ("F3", (2, 1), 2.5, "YES"),   # 3 > 2.5
        ("F5", (1, 1), 2.5, "NO"),    # 2 < 2.5
        ("F7", (4, 3), 6.5, "YES"),   # 7 > 6.5
    ):
        market = {"marketFamily": INNING_TOTAL, "marketHorizon": horizon, "threshold": threshold}
        outcome = {"periodScores": {horizon: period}}
        status, result, reason = settle_market(market, outcome)
        assert (status, result, reason) == ("SETTLED", expected, None)


def test_inning_total_missing_period_score_for_horizon_is_unresolved_not_guessed():
    market = {"marketFamily": INNING_TOTAL, "marketHorizon": "F7", "threshold": 3.5}
    outcome = {"periodScores": {"F5": (2, 2)}}  # F7 never captured
    status, result, reason = settle_market(market, outcome)
    assert status == "SETTLEMENT_UNRESOLVED"
    assert reason == "missing_period_score_F7"
    assert result is None


def test_inning_total_missing_threshold_is_unresolved_malformed_contract():
    market = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5"}  # no threshold at all
    outcome = {"periodScores": {"F5": (2, 2)}}
    status, result, reason = settle_market(market, outcome)
    assert status == "SETTLEMENT_UNRESOLVED"
    assert reason == "missing_threshold"
    assert result is None


def test_inning_total_defaults_to_f5_when_horizon_omitted():
    """settle_market()'s `horizon = market.get("marketHorizon") or "F5"` fallback applies to inning_total too."""
    market = {"marketFamily": INNING_TOTAL, "threshold": 3}
    outcome = {"periodScores": {"F5": (2, 2)}}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_first_inning_run_yes_no():
    assert settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": (1, 0)})[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": (0, 0)})[:2] == ("SETTLED", "NO")
    status, result, reason = settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": None})
    assert status == "SETTLEMENT_UNRESOLVED"


def test_derive_bet_result_win_loss():
    assert derive_bet_result("YES", "YES") == "WIN"
    assert derive_bet_result("NO", "YES") == "LOSS"
    assert derive_bet_result("YES", "NO") == "LOSS"
    assert derive_bet_result(None, "YES") is None  # not yet settled -- never guessed


def test_realized_return_win_loss_void():
    assert realized_return_for_bet(10.0, 0.5, "WIN") == 10.0  # 10 * (1/0.5 - 1) = 10
    assert realized_return_for_bet(10.0, 0.5, "LOSS") == -10.0
    assert realized_return_for_bet(10.0, 0.5, "PUSH") == 0.0
    assert realized_return_for_bet(10.0, 0.5, "VOID") == 0.0
    assert realized_return_for_bet(10.0, None, "WIN") is None  # never fabricate without an entry price


def test_settle_bets_for_ticker_settles_every_bet_not_just_the_first():
    """
    A ticker can carry multiple bet tranches (see
    tests/edgelab/test_bets.py::test_multiple_bets_on_one_market_get_distinct_ids).
    Every one of them must be settled -- a prior version of
    scripts/edgelab/settle_markets.py only ever settled matching_bets[0],
    silently leaving every additional tranche pending forever.
    """
    bets = [
        {"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5},
        {"betId": "b2", "side": "YES", "stake": 5.0, "entryPrice": 0.4},
        {"betId": "b3", "side": "NO", "stake": 3.0, "entryPrice": 0.6},
    ]
    updated = settle_bets_for_ticker(bets, "SETTLED", "YES")
    assert len(updated) == 3
    by_id = {b["betId"]: b for b in updated}
    assert by_id["b1"]["result"] == "WIN"
    assert by_id["b1"]["status"] == "settled"
    assert by_id["b1"]["netProfitLoss"] == 10.0
    assert by_id["b2"]["result"] == "WIN"
    assert by_id["b2"]["netProfitLoss"] == 7.5
    assert by_id["b3"]["result"] == "LOSS"  # bought NO, market settled YES
    assert by_id["b3"]["netProfitLoss"] == -3.0


def test_settle_bets_for_ticker_leaves_bets_untouched_when_not_settled():
    bets = [{"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5}]
    assert settle_bets_for_ticker(bets, "VOID", None) == []
    assert settle_bets_for_ticker(bets, "SETTLEMENT_UNRESOLVED", None) == []


def test_settle_bets_for_ticker_does_not_mutate_input():
    original = {"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5, "status": "pending"}
    bets = [original]
    settle_bets_for_ticker(bets, "SETTLED", "YES")
    assert original["status"] == "pending"  # input list/dicts must not be mutated in place


# ---------------------------------------------------------------------------
# compare_confirmed_receipt_to_settlement / settle_bets_for_ticker's
# integration of it (Aug 11 2026 game-identity repair mission, scenario 6:
# "existing manual receipt economics surviving later automatic
# settlement"). A bet settled via lib.edgelab.bets.confirm_realized_return
# BEFORE canonical settlement was possible (e.g. a standalone/manual
# betting day with no resolved mlbGamePk yet -- see
# lib.edgelab.mlb_schedule) must keep its confirmedReceipt* fields
# completely untouched once objective settlement DOES become available,
# while gaining an explicit agree/disagree flag.
# ---------------------------------------------------------------------------

def test_compare_confirmed_receipt_to_settlement_none_when_no_receipt():
    bet = {"betId": "b1", "side": "YES", "stake": 10.0}
    computed = {"result": "WIN", "netProfitLoss": 10.0}
    assert compare_confirmed_receipt_to_settlement(bet, computed) is None


def test_compare_confirmed_receipt_to_settlement_agrees():
    bet = {
        "betId": "b1", "side": "YES", "stake": 10.0,
        "confirmedReceiptReturn": 20.0, "confirmedReceiptNetProfitLoss": 10.0,
    }
    computed = {"result": "WIN", "netProfitLoss": 10.0}
    cmp = compare_confirmed_receipt_to_settlement(bet, computed, now="2026-08-12T00:00:00Z")
    assert cmp["agrees"] is True
    assert cmp["resultsAgree"] is True
    assert cmp["amountsAgree"] is True
    assert cmp["objectiveResult"] == "WIN"
    assert cmp["confirmedReceiptImpliedResult"] == "WIN"
    assert cmp["comparedAt"] == "2026-08-12T00:00:00Z"


def test_compare_confirmed_receipt_to_settlement_flags_result_disagreement():
    """A manually confirmed receipt says WIN but objective settlement says LOSS -- must be flagged, never silently reconciled either way."""
    bet = {
        "betId": "b1", "side": "YES", "stake": 10.0,
        "confirmedReceiptReturn": 20.0, "confirmedReceiptNetProfitLoss": 10.0,
    }
    computed = {"result": "LOSS", "netProfitLoss": -10.0}
    cmp = compare_confirmed_receipt_to_settlement(bet, computed)
    assert cmp["agrees"] is False
    assert cmp["resultsAgree"] is False
    assert cmp["objectiveResult"] == "LOSS"
    assert cmp["confirmedReceiptImpliedResult"] == "WIN"


def test_compare_confirmed_receipt_to_settlement_flags_amount_disagreement_same_result():
    """Both sides say WIN but the dollar amounts genuinely differ (beyond rounding tolerance) -- e.g. a data-entry mistake in the original receipt."""
    bet = {
        "betId": "b1", "side": "YES", "stake": 10.0,
        "confirmedReceiptReturn": 15.0, "confirmedReceiptNetProfitLoss": 5.0,
    }
    computed = {"result": "WIN", "netProfitLoss": 10.0}
    cmp = compare_confirmed_receipt_to_settlement(bet, computed)
    assert cmp["resultsAgree"] is True
    assert cmp["amountsAgree"] is False
    assert cmp["agrees"] is False


def test_settle_bets_for_ticker_never_overwrites_confirmed_receipt_and_flags_disagreement():
    """
    Full integration: a bet already carries a confirmed manual receipt
    (recorded while canonical settlement was still pending). Once
    settle_bets_for_ticker computes a fresh objective outcome for it,
    BOTH provenance paths must survive side by side -- the objective
    result/netProfitLoss/status are the freshly computed ones, the
    confirmedReceipt* fields are completely untouched, and a comparison
    flag is attached.
    """
    bet = {
        "betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5,
        "confirmedReceiptReturn": 0.0, "confirmedReceiptNetProfitLoss": -10.0,
        "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    }
    updated = settle_bets_for_ticker([bet], "SETTLED", "YES", now="2026-08-12T00:00:00Z")
    assert len(updated) == 1
    row = updated[0]
    # Objective settlement result -- freshly computed, side=YES market settled YES -> WIN.
    assert row["result"] == "WIN"
    assert row["status"] == "settled"
    assert row["netProfitLoss"] == 10.0
    # Confirmed receipt fields -- completely untouched.
    assert row["confirmedReceiptReturn"] == 0.0
    assert row["confirmedReceiptNetProfitLoss"] == -10.0
    assert row["confirmedReceiptSource"] == "MANUAL_POSTMORTEM_RECEIPT"
    # Disagreement flagged (receipt implied LOSS, objective settlement says WIN).
    cmp = row["confirmedReceiptSettlementComparison"]
    assert cmp["agrees"] is False
    assert cmp["objectiveResult"] == "WIN"
    assert cmp["confirmedReceiptImpliedResult"] == "LOSS"


def test_settle_bets_for_ticker_no_comparison_field_when_no_receipt():
    """The vast majority of bets never have a confirmed receipt -- no new field should ever appear for them."""
    bet = {"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5}
    updated = settle_bets_for_ticker([bet], "SETTLED", "YES")
    assert "confirmedReceiptSettlementComparison" not in updated[0]


def test_bet_needs_settlement_update_true_when_comparison_first_computed():
    """
    A bet that was ALREADY canonically settled (unchanged result/
    netProfitLoss/returnAmount) but has since gained a confirmed receipt
    (or this comparison feature is running for the first time) must
    still be persisted -- the comparison itself is new information.
    """
    original = {
        "status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0,
        "confirmedReceiptNetProfitLoss": 10.0,
    }
    computed = dict(original, confirmedReceiptSettlementComparison={"agrees": True})
    assert bet_needs_settlement_update(original, computed) is True


def test_bet_needs_settlement_update_false_when_comparison_unchanged():
    comparison = {"agrees": True, "objectiveResult": "WIN"}
    original = {
        "status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0,
        "confirmedReceiptSettlementComparison": comparison,
    }
    computed = dict(original)
    assert bet_needs_settlement_update(original, computed) is False


def test_hypothetical_return_for_unbet_market_uses_price_not_just_win_rate():
    # A cheap YES win pays much more than an expensive YES win -- must be price-dependent.
    cheap_win = hypothetical_yes_return(0.20, "YES")
    expensive_win = hypothetical_yes_return(0.80, "YES")
    assert cheap_win == 4.0     # (1-0.2)/0.2
    assert expensive_win == 0.25  # (1-0.8)/0.8
    assert cheap_win > expensive_win
    assert hypothetical_yes_return(0.5, "NO") == -1.0
    assert hypothetical_yes_return(0.5, None) == 0.0
    assert hypothetical_yes_return(None, "YES") is None


def test_was_market_ever_recommended_true_for_surfaced_statuses():
    assert was_market_ever_recommended([{"status": "PASS_NO_EDGE"}, {"status": "WATCH"}]) is True
    assert was_market_ever_recommended([{"status": "RECOMMENDED_NOT_BET"}]) is True
    assert was_market_ever_recommended([{"status": "BET_PLACED"}]) is True


def test_was_market_ever_recommended_false_for_extension_only_or_empty():
    assert was_market_ever_recommended([{"status": "NOT_EVALUATED"}]) is False
    assert was_market_ever_recommended([{"status": "PASS_NO_EDGE"}, {"status": "PASS_DATA_QUALITY"}]) is False
    assert was_market_ever_recommended([]) is False


def test_settlement_record_carries_was_recommended_and_was_placed():
    rec = build_settlement_record(
        "TICKER", "GAME1", "game_result", "SETTLED", "YES", "test_source", "2026-08-01T00:00:00Z",
        was_recommended=True, bet_id="bet1",
    )
    assert rec["wasRecommended"] is True
    assert rec["wasPlaced"] is True  # derived from bet_id being set


def test_settlement_record_was_placed_defaults_false_when_no_bet():
    rec = build_settlement_record(
        "TICKER", "GAME1", "game_result", "SETTLED", "YES", "test_source", "2026-08-01T00:00:00Z",
        was_recommended=False,
    )
    assert rec["wasPlaced"] is False
    assert rec["wasRecommended"] is False


# ── merge_settlement_record / bet_needs_settlement_update (GitHub issue #43 correction round) ──

def _settlement_record(**overrides):
    base = build_settlement_record(
        "TICKER", "GAME1", "pitcher_strikeouts", "SETTLED", "YES", "test_source", "2026-08-02T00:00:00Z",
        settlement_evidence={"actualValue": 9, "threshold": 9, "fetchedAt": "2026-08-02T00:00:00Z",
                              "sourcePayloadHash": "hash1"},
    )
    base.update(overrides)
    return base


def test_merge_settlement_record_no_existing_returns_new_verbatim():
    new_record = _settlement_record()
    assert merge_settlement_record(None, new_record) is new_record


def test_merge_settlement_record_identical_content_returns_existing_unchanged():
    existing = _settlement_record(createdAt="2026-08-01T00:00:00Z", updatedAt="2026-08-01T00:00:00Z",
                                    settledAt="2026-08-01T00:00:00Z")
    # A "fresh" run: same outcome/threshold/actualValue, but a NEW fetch
    # timestamp/payload hash and new createdAt/updatedAt/settledAt --
    # none of that should register as a difference.
    new_record = _settlement_record(
        createdAt="2026-08-02T12:00:00Z", updatedAt="2026-08-02T12:00:00Z", settledAt="2026-08-02T12:00:00Z",
        settlementEvidence={"actualValue": 9, "threshold": 9, "fetchedAt": "2026-08-02T12:00:00Z",
                             "sourcePayloadHash": "hash2"},
    )
    merged = merge_settlement_record(existing, new_record)
    assert merged is existing  # byte-for-byte the SAME object, not just equal


def test_merge_settlement_record_changed_content_preserves_original_created_at():
    existing = _settlement_record(createdAt="2026-08-01T00:00:00Z", updatedAt="2026-08-01T00:00:00Z",
                                    settledAt="2026-08-01T00:00:00Z")
    new_record = _settlement_record(
        outcome="NO", result="NO", createdAt="2026-08-02T12:00:00Z", updatedAt="2026-08-02T12:00:00Z",
        settledAt="2026-08-02T12:00:00Z",
        settlementEvidence={"actualValue": 8, "threshold": 9, "fetchedAt": "2026-08-02T12:00:00Z",
                             "sourcePayloadHash": "hash2"},
    )
    merged = merge_settlement_record(existing, new_record)
    assert merged is not existing
    assert merged["createdAt"] == "2026-08-01T00:00:00Z"  # preserved
    assert merged["updatedAt"] == "2026-08-02T12:00:00Z"  # advanced
    assert merged["settledAt"] == "2026-08-02T12:00:00Z"  # advanced
    assert merged["result"] == "NO"


def test_merge_settlement_record_ignores_only_fetch_metadata_changes():
    """A changed fetchedAt/sourcePayloadHash ALONE (nothing else different) must never register as a change."""
    existing = _settlement_record()
    new_record = _settlement_record(
        settlementEvidence={"actualValue": 9, "threshold": 9, "fetchedAt": "some-other-time",
                             "sourcePayloadHash": "totally-different-hash"},
    )
    assert merge_settlement_record(existing, new_record) is existing


def test_bet_needs_settlement_update_false_when_unchanged():
    original = {"status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0}
    computed = {"status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0}
    assert bet_needs_settlement_update(original, computed) is False


def test_bet_needs_settlement_update_true_when_pending():
    original = {"status": "pending", "result": None, "netProfitLoss": None, "returnAmount": None}
    computed = {"status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0}
    assert bet_needs_settlement_update(original, computed) is True


def test_bet_needs_settlement_update_true_when_result_flips_on_correction():
    original = {"status": "settled", "result": "LOSS", "netProfitLoss": -10.0, "returnAmount": -10.0}
    computed = {"status": "settled", "result": "WIN", "netProfitLoss": 10.0, "returnAmount": 10.0}
    assert bet_needs_settlement_update(original, computed) is True
