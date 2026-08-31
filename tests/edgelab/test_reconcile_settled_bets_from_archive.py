"""Tests for the accounting-only ledger reconciliation.

This script settles real money outcomes in the canonical wager ledger, so
every refusal it claims is asserted here against a constructed case, not
merely read out of its docstring.
"""
import copy
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))

import reconcile_settled_bets_from_archive as rec  # noqa: E402


def _bet(**over):
    base = {
        "betId": "b1", "status": "pending", "marketTicker": "KXMLBGAME-26AUG091915SFATL-SF",
        "marketFamily": "game_result", "side": "YES", "stake": 10.0, "entryPrice": 0.5,
        "gameDate": "2026-08-09", "gameId": "823268",
    }
    base.update(over)
    return base


def _settlement(**over):
    base = {"marketTicker": "KXMLBGAME-26AUG091915SFATL-SF", "settlementStatus": "SETTLED",
            "result": "YES", "settlementId": "s1"}
    base.update(over)
    return base


def _index(*records):
    out = {}
    for r in records:
        out.setdefault(r["marketTicker"], []).append(r)
    return out


class TestOnlyExactMatchesSettle:
    def test_an_exact_ticker_match_settles(self):
        updates, skipped, receipts = rec.plan([_bet()], _index(_settlement()))
        assert len(updates) == 1 and receipts[0]["betResult"] == "WIN"

    def test_a_near_miss_ticker_does_not_settle(self):
        """No normalisation, no prefix matching, no fuzzy fallback."""
        idx = _index(_settlement(marketTicker="KXMLBGAME-26AUG091915SFATL-SF-EXTRA"))
        updates, skipped, _ = rec.plan([_bet()], idx)
        assert not updates
        assert skipped["NO_SETTLEMENT_RECORD"] == 1

    def test_a_case_differing_ticker_does_not_settle(self):
        idx = _index(_settlement(marketTicker="kxmlbgame-26aug091915sfatl-sf"))
        updates, _, _ = rec.plan([_bet()], idx)
        assert not updates

    def test_no_settlement_record_leaves_the_bet_pending(self):
        updates, skipped, _ = rec.plan([_bet()], {})
        assert not updates and skipped["NO_SETTLEMENT_RECORD"] == 1


class TestAmbiguityIsRefused:
    def test_conflicting_results_on_one_ticker_are_skipped(self):
        idx = _index(_settlement(result="YES", settlementId="a"),
                     _settlement(result="NO", settlementId="b"))
        updates, skipped, _ = rec.plan([_bet()], idx)
        assert not updates
        assert skipped["AMBIGUOUS_SETTLEMENT_RECORDS"] == 1

    def test_conflicting_statuses_on_one_ticker_are_skipped(self):
        idx = _index(_settlement(settlementStatus="SETTLED", settlementId="a"),
                     _settlement(settlementStatus="SETTLEMENT_UNRESOLVED", settlementId="b"))
        updates, skipped, _ = rec.plan([_bet()], idx)
        assert not updates
        assert skipped["AMBIGUOUS_SETTLEMENT_RECORDS"] == 1

    @pytest.mark.parametrize("status", ["SETTLEMENT_UNRESOLVED", "VOID", "PENDING", ""])
    def test_a_non_settled_status_never_settles_a_bet(self, status):
        updates, skipped, _ = rec.plan([_bet()], _index(_settlement(settlementStatus=status)))
        assert not updates
        assert skipped["SETTLEMENT_NOT_DEFINITE"] == 1

    @pytest.mark.parametrize("result", [None, "", "PUSH", "UNKNOWN"])
    def test_an_indefinite_result_never_settles_a_bet(self, result):
        updates, skipped, _ = rec.plan([_bet()], _index(_settlement(result=result)))
        assert not updates
        assert skipped["SETTLEMENT_NOT_DEFINITE"] == 1


class TestAlreadySettledWagersAreUntouched:
    def test_a_settled_bet_is_never_reprocessed(self):
        bet = _bet(status="settled", result="WIN")
        updates, skipped, _ = rec.plan([bet], _index(_settlement()))
        assert not updates
        assert skipped["NOT_PENDING"] == 1

    def test_a_void_bet_is_never_reprocessed(self):
        updates, skipped, _ = rec.plan([_bet(status="void")], _index(_settlement()))
        assert not updates and skipped["NOT_PENDING"] == 1

    def test_the_input_ledger_is_never_mutated(self):
        bets = [_bet()]
        before = copy.deepcopy(bets)
        rec.plan(bets, _index(_settlement()))
        assert bets == before, "plan() mutated the ledger it was given"


class TestIssue43PropsRemainPending:
    @pytest.mark.parametrize("family", sorted(rec.PLAYER_PROP_FAMILIES))
    def test_every_player_prop_family_is_refused(self, family):
        updates, skipped, _ = rec.plan([_bet(marketFamily=family)], _index(_settlement()))
        assert not updates
        assert skipped["PLAYER_PROP_ISSUE_43"] == 1

    @pytest.mark.parametrize("prefix", rec.PLAYER_PROP_TICKER_PREFIXES)
    def test_a_prop_ticker_is_refused_even_with_a_settlement_record(self, prefix):
        ticker = prefix + "26AUG211610ATLMIL-X"
        updates, skipped, _ = rec.plan(
            [_bet(marketTicker=ticker, marketFamily="unknown")],
            _index(_settlement(marketTicker=ticker)))
        assert not updates
        assert skipped["PLAYER_PROP_ISSUE_43"] == 1


class TestIncompleteBetsAreRefused:
    @pytest.mark.parametrize("field,value", [("side", None), ("side", "MAYBE"),
                                             ("stake", None)])
    def test_a_bet_missing_required_fields_is_skipped(self, field, value):
        updates, skipped, _ = rec.plan([_bet(**{field: value})], _index(_settlement()))
        assert not updates
        assert skipped["MISSING_BET_FIELDS"] == 1

    def test_a_bet_with_no_ticker_is_skipped(self):
        updates, skipped, _ = rec.plan([_bet(marketTicker=None)], _index(_settlement()))
        assert not updates and skipped["NO_CANONICAL_TICKER"] == 1

    def test_the_na_placeholder_family_is_skipped(self):
        updates, skipped, _ = rec.plan(
            [_bet(marketFamily="N/A", marketTicker="N/A-Kal-TT-null")], {})
        assert not updates and skipped["NO_CANONICAL_TICKER"] == 1


class TestGradingUsesTheCanonicalLogic:
    def test_a_matching_side_is_a_win_and_an_opposing_side_is_a_loss(self):
        win, _, wr = rec.plan([_bet(side="YES")], _index(_settlement(result="YES")))
        loss, _, lr = rec.plan([_bet(side="NO")], _index(_settlement(result="YES")))
        assert wr[0]["betResult"] == "WIN" and lr[0]["betResult"] == "LOSS"

    def test_a_loss_returns_a_negative_amount_from_the_fee_aware_path(self):
        """NOT simply -stake.

        lib.edgelab.execution_economics.realized_pl_for_bet is fee-aware,
        so a $10 losing stake does not return exactly -10.00. Asserting
        -stake here would be asserting a formula this script deliberately
        does NOT own -- so the expected value is taken from the canonical
        function itself, and only its sign and bound are asserted
        independently."""
        from lib.edgelab.execution_economics import realized_pl_for_bet
        bet = _bet(side="NO", stake=10.0)
        _, _, receipts = rec.plan([bet], _index(_settlement(result="YES")))
        expected = realized_pl_for_bet(
            execution_status=bet.get("executionStatus"), stake=10.0, bet_result="LOSS",
            entry_price=0.5, contracts=None, exit_sale_proceeds=None)
        assert receipts[0]["netProfitLoss"] == pytest.approx(expected)
        assert -10.0 <= receipts[0]["netProfitLoss"] < 0, (
            "a loss must be negative and never exceed the stake")

    def test_the_status_becomes_settled(self):
        updates, _, _ = rec.plan([_bet()], _index(_settlement()))
        assert updates[0]["status"] == "settled"

    def test_grading_is_not_reimplemented_here(self):
        """The script must delegate to lib.edgelab.settlement rather than
        computing WIN/LOSS or P&L itself."""
        source = open(rec.__file__, encoding="utf-8").read()
        assert "settle_bets_for_ticker" in source
        assert "bet_needs_settlement_update" in source
        for banned in ("def derive_bet_result", "def realized_return_for_bet",
                       "1.0 / entry_price", "1/entry_price"):
            assert banned not in source, f"reconciliation reimplements grading: {banned}"


class TestDeterminism:
    def test_the_plan_is_deterministic(self):
        bets = [_bet(betId="a"), _bet(betId="b", marketTicker="KXMLBF5-26AUG091915SFATL-SF")]
        idx = _index(_settlement(), _settlement(marketTicker="KXMLBF5-26AUG091915SFATL-SF",
                                                result="NO", settlementId="s2"))
        first = rec.plan(bets, idx)
        second = rec.plan(bets, idx)
        assert first[0] == second[0]
        assert rec.summarise(first[2]) == rec.summarise(second[2])

    def test_reconciliation_is_idempotent(self):
        """Re-running over an already-settled ledger must plan zero writes."""
        updates, _, _ = rec.plan([_bet()], _index(_settlement()))
        again, skipped, _ = rec.plan(updates, _index(_settlement()))
        assert not again, "a second pass would rewrite already-settled bets"

    def test_the_summary_totals_reconcile_with_the_receipts(self):
        bets = [_bet(betId="a", side="YES"), _bet(betId="b", side="NO",
                                                  marketTicker="KXMLBF5-X")]
        idx = _index(_settlement(), _settlement(marketTicker="KXMLBF5-X", settlementId="s2"))
        _, _, receipts = rec.plan(bets, idx)
        summary = rec.summarise(receipts)
        assert summary["betsSettled"] == summary["wins"] + summary["losses"] + summary["other"]
        assert summary["netProfitLoss"] == pytest.approx(
            round(sum(r["netProfitLoss"] for r in receipts), 4))


class TestItNeverInsertsNewBets:
    def test_the_apply_path_refuses_insertions(self):
        source = open(rec.__file__, encoding="utf-8").read()
        assert "if inserted:" in source and "FATAL" in source, (
            "the apply path must abort if the upsert inserted a new bet")

    def test_no_settlement_record_is_ever_written(self):
        """Reading a settlementId onto a receipt is fine; UPSERTING into the
        settlements entity is not. Scoped to the write call so a legitimate
        read is not flagged."""
        import ast
        source = open(rec.__file__, encoding="utf-8").read()
        writes = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "upsert_records":
                writes.append([a.value for a in node.args
                               if isinstance(a, ast.Constant)])
        assert writes, "expected exactly one canonical upsert call"
        for args in writes:
            assert "settlementId" not in args, (
                "this script must never upsert into the settlements entity")
            assert "betId" in args, "the only permitted upsert is onto bets by betId"


class TestManualOnlyPopulationsStayOutOfAutomaticSettlement:
    """Synthetic MANUALCOMBO parlays have no Kalshi market and therefore no
    archive outcome; they must stay manual/user-confirmed rather than being
    force-fitted into automatic settlement (parlay settlement is a separate,
    unimplemented concern)."""

    def test_a_manualcombo_wager_never_settles(self):
        combo = _bet(betId="combo1", marketFamily="multi_market_combo",
                     marketTicker="MANUALCOMBO-20260829-PARLAY-BAL-NYY-MILF5-MILTT-024")
        updates, skipped, receipts = rec.plan([combo], _index(_settlement()))
        assert updates == [] and receipts == []
        assert skipped["NO_SETTLEMENT_RECORD"] == 1

    def test_a_manualcombo_wager_never_settles_even_beside_settleable_ones(self):
        """One unsettleable combo must not block a real market's settlement."""
        combo = _bet(betId="combo1", marketFamily="multi_market_combo",
                     marketTicker="MANUALCOMBO-20260829-PARLAY-BAL-NYY-MILF5-MILTT-024")
        updates, _skipped, receipts = rec.plan([combo, _bet()], _index(_settlement()))
        assert len(updates) == 1
        assert [r["betId"] for r in receipts] == ["b1"]


class TestConfirmedReceiptEconomicsSurviveSettlement:
    """Settlement may change objective lifecycle status only. The user's own
    confirmed cash evidence is separate data and must come through untouched
    (lib.edgelab.bets.realized_bet_economics reads it back for reporting)."""

    _RECEIPT_FIELDS = {
        "confirmedReceiptReturn": 51.15,
        "confirmedReceiptNetProfitLoss": 26.15,
        "confirmedReceiptSource": "user_screenshot_confirmation",
        "confirmedReceiptNote": "2026-08-24 slate postmortem import batch",
        "confirmedReceiptAt": "2026-08-31T05:04:38Z",
        "shareCardEvidence": {"shareCardInitialCost": 25.0, "shareCardPaidOut": 51.15},
    }

    def test_every_confirmed_receipt_field_survives(self):
        updates, _skipped, _receipts = rec.plan([_bet(**self._RECEIPT_FIELDS)],
                                                _index(_settlement()))
        assert len(updates) == 1
        for field, value in self._RECEIPT_FIELDS.items():
            assert updates[0][field] == value, field

    def test_settlement_changes_only_lifecycle_and_derived_amounts(self):
        before = _bet(**self._RECEIPT_FIELDS)
        updates, _skipped, _receipts = rec.plan([before], _index(_settlement()))
        changed = {k for k in set(before) | set(updates[0])
                   if before.get(k) != updates[0].get(k)}
        assert changed <= {"status", "result", "netProfitLoss", "returnAmount",
                           "updatedAt", "confirmedReceiptSettlementComparison"}

    def test_the_users_exact_stake_is_never_rewritten(self):
        updates, _skipped, _receipts = rec.plan([_bet(stake=24.99, **self._RECEIPT_FIELDS)],
                                                _index(_settlement()))
        assert updates[0]["stake"] == 24.99
