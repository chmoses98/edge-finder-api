"""Regression: legacy re-ingest must not un-settle a settled bet.

scripts/edgelab/ingest_existing_bets.py runs in
.github/workflows/edgelab-postgame.yml IMMEDIATELY AFTER
scripts/edgelab/settle_markets.py. Before this fix, every nightly run
wrote settlements and then partially reverted them, because
reconcile_with_existing returned the legacy entry-time record wholesale
whenever its content differed at all.

A legacy source ledger records how a bet was ENTERED. It cannot know how
that bet later SETTLED.
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.bets import (  # noqa: E402
    _ALWAYS_PRESERVE_FIELDS, _PRESERVE_IF_NOT_SUPPLIED_FIELDS, reconcile_with_existing,
)


def _settled(**over):
    """A bet the settlement pipeline has already graded."""
    base = {
        "betId": "b1", "marketTicker": "KXMLBGAME-26AUG091915SFATL-SF",
        "side": "YES", "stake": 10.0, "entryPrice": 0.5, "createdAt": "2026-08-09T00:00:00Z",
        "status": "settled", "result": "WIN", "netProfitLoss": 9.7, "returnAmount": 9.7,
        "closingPrice": 0.61, "clv": 0.11, "clvQuoteId": "q1", "recordStatus": "ACTIVE",
        "recommendationId": "rec-1", "modelEvaluationId": "me-1", "modelSupported": True,
        "snapshotId": "snap-1", "productionRunId": "prod-1", "replayRunId": "replay-1",
    }
    base.update(over)
    return base


def _legacy(**over):
    """The same bet as the legacy ledger still describes it: entry-time
    only, so pending, ungraded, and with no P/L."""
    base = {
        "betId": "b1", "marketTicker": "KXMLBGAME-26AUG091915SFATL-SF",
        "side": "YES", "stake": 10.0, "entryPrice": 0.5, "createdAt": "2026-08-09T00:00:00Z",
        "status": "pending", "result": None, "netProfitLoss": None, "returnAmount": None,
        "closingPrice": None, "clv": None, "clvQuoteId": None, "recordStatus": "ACTIVE",
        "recommendationId": None, "modelEvaluationId": None, "modelSupported": None,
        "snapshotId": None, "productionRunId": None, "replayRunId": None,
    }
    base.update(over)
    return base


class TestSettlementSurvivesReIngest:
    def test_a_settled_bet_is_not_sent_back_to_pending(self):
        """The exact production bug: one unscoped re-ingest reverted 36
        settled wagers."""
        out = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert out["status"] == "settled"
        assert out["result"] == "WIN"

    def test_the_realized_pl_is_not_wiped(self):
        out = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert out["netProfitLoss"] == 9.7
        assert out["returnAmount"] == 9.7

    def test_clv_fields_are_not_wiped(self):
        out = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert out["closingPrice"] == 0.61 and out["clv"] == 0.11

    @pytest.mark.parametrize("field", _ALWAYS_PRESERVE_FIELDS)
    def test_every_pipeline_owned_field_survives(self, field):
        out = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert out[field] == _settled()[field], f"{field} was reset by re-ingest"

    @pytest.mark.parametrize("field", _PRESERVE_IF_NOT_SUPPLIED_FIELDS)
    def test_async_backfilled_linkage_survives_when_not_supplied(self, field):
        out = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert out[field] == _settled()[field], f"{field} was nulled by re-ingest"

    def test_a_settled_bet_whose_legacy_row_is_otherwise_unchanged_is_a_true_noop(self):
        existing = _settled()
        out = reconcile_with_existing(_legacy(), {"b1": existing})
        assert out is existing, "an unchanged re-ingest must not rewrite the row at all"

    def test_it_shares_write_placed_bets_field_lists(self):
        """One definition of 'pipeline-owned', not two that can drift."""
        import ast
        source = open(os.path.join(_ROOT, "lib", "edgelab", "bets.py"), encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_inherit_lifecycle_if_not_supplied")
        names = {getattr(x, "id", "") for x in ast.walk(fn) if isinstance(x, ast.Name)}
        assert "_ALWAYS_PRESERVE_FIELDS" in names
        assert "_PRESERVE_IF_NOT_SUPPLIED_FIELDS" in names


class TestLegitimateCorrectionsStillApply:
    def test_a_genuine_entry_correction_is_still_written(self):
        """Inheritance must not freeze the record entirely -- a corrected
        stake or price is exactly what re-ingest is FOR."""
        out = reconcile_with_existing(_legacy(stake=25.0), {"b1": _settled()})
        assert out["stake"] == 25.0
        assert out["status"] == "settled", "the correction must not revive the revert"

    def test_a_legacy_row_may_SET_an_outcome_where_none_exists(self):
        """CASE 1. A legacy ledger is the ONLY record of an outcome for
        manual days and for every bet predating the settlement archive,
        so where nothing canonical exists it must still be able to
        establish one."""
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", netProfitLoss=-10.0),
            {"b1": _unsettled()})
        assert out["result"] == "LOSS"
        assert out["status"] == "settled"


def _unsettled(**over):
    """A stored canonical row with NO outcome yet."""
    base = _settled(result=None, status="pending", netProfitLoss=None, returnAmount=None,
                    closingPrice=None, clv=None, clvQuoteId=None)
    base.update(over)
    return base


class TestPrecedenceIsDecidedByTheStoredRow:
    """The corrected rule.

    An earlier version of _inherit_lifecycle_if_not_supplied keyed on the
    INCOMING record -- "a legacy row that supplies a result is a
    settlement source". That is not safe enough: it lets a stale legacy
    ledger silently REPLACE an already-settled canonical result. Authority
    belongs to the stored row.

        a legacy ledger may SET an outcome where none exists;
        it may never UNSET OR REPLACE a finalized canonical outcome.
    """

    # ---- CASE 1: stored row has no canonical outcome -> legacy may set --

    def test_1_pending_stored_plus_legacy_loss_becomes_settled_loss(self):
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", netProfitLoss=-10.0),
            {"b1": _unsettled()})
        assert (out["result"], out["status"]) == ("LOSS", "settled")

    def test_2_pending_stored_plus_legacy_win_becomes_settled_win(self):
        out = reconcile_with_existing(
            _legacy(result="WIN", status="settled", netProfitLoss=9.7),
            {"b1": _unsettled()})
        assert (out["result"], out["status"]) == ("WIN", "settled")
        assert out["netProfitLoss"] == 9.7

    # ---- CASE 2: stored row is finalized -> legacy may not overwrite ----

    def test_3_settled_win_plus_legacy_none_remains_win(self):
        out = reconcile_with_existing(_legacy(), {"b1": _settled(result="WIN")})
        assert (out["result"], out["status"]) == ("WIN", "settled")

    def test_4_settled_win_plus_conflicting_legacy_loss_remains_win(self):
        """The case the previous rule got wrong."""
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", netProfitLoss=-10.0,
                    returnAmount=-10.0),
            {"b1": _settled(result="WIN", netProfitLoss=9.7, returnAmount=9.7)})
        assert out["result"] == "WIN", "a stale legacy result replaced a canonical one"
        assert out["status"] == "settled"

    def test_5_settled_loss_plus_conflicting_legacy_win_remains_loss(self):
        out = reconcile_with_existing(
            _legacy(result="WIN", status="settled", netProfitLoss=9.7),
            {"b1": _settled(result="LOSS", netProfitLoss=-10.0, returnAmount=-10.0)})
        assert out["result"] == "LOSS"
        assert out["netProfitLoss"] == -10.0

    @pytest.mark.parametrize("legacy_result", ["WIN", "LOSS"])
    def test_6_void_stored_row_remains_void(self, legacy_result):
        """Void is final even though it carries no WIN/LOSS."""
        out = reconcile_with_existing(
            _legacy(result=legacy_result, status="settled", netProfitLoss=5.0),
            {"b1": _settled(result=None, status="void", netProfitLoss=0.0)})
        assert out["status"] == "void"
        assert out["result"] is None

    def test_7_canonical_pl_and_return_survive_a_stale_conflicting_result(self):
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", netProfitLoss=-22.0,
                    returnAmount=-22.0),
            {"b1": _settled(result="WIN", netProfitLoss=9.7, returnAmount=9.7)})
        assert out["netProfitLoss"] == 9.7
        assert out["returnAmount"] == 9.7

    def test_8_canonical_clv_survives_a_stale_conflicting_result(self):
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", closingPrice=0.10,
                    clv=-0.40, clvQuoteId="stale"),
            {"b1": _settled(result="WIN", closingPrice=0.61, clv=0.11, clvQuoteId="q1")})
        assert out["closingPrice"] == 0.61
        assert out["clv"] == 0.11
        assert out["clvQuoteId"] == "q1"

    def test_9_entry_correction_applies_without_altering_lifecycle(self):
        out = reconcile_with_existing(
            _legacy(stake=25.0, entryPrice=0.44, result="LOSS", status="settled"),
            {"b1": _settled(result="WIN", netProfitLoss=9.7)})
        assert out["stake"] == 25.0 and out["entryPrice"] == 0.44
        assert (out["result"], out["status"], out["netProfitLoss"]) == ("WIN", "settled", 9.7)

    def test_10_async_linkage_still_behaves(self):
        preserved = reconcile_with_existing(_legacy(), {"b1": _settled()})
        assert preserved["recommendationId"] == "rec-1"
        supplied = reconcile_with_existing(_legacy(recommendationId="rec-NEW"),
                                           {"b1": _settled()})
        assert supplied["recommendationId"] == "rec-NEW"

    def test_11_a_stored_row_with_no_result_is_not_treated_as_final(self):
        """CASE 1 must not be narrowed by accident -- this is the
        historical/manual path the existing suite depends on."""
        from lib.edgelab.bets import _has_canonical_outcome
        assert _has_canonical_outcome(_unsettled()) is False
        assert _has_canonical_outcome({"result": None, "status": "pending"}) is False
        for final in ("WIN", "LOSS", "PUSH", "VOID"):
            assert _has_canonical_outcome({"result": final, "status": "settled"}) is True
        assert _has_canonical_outcome({"result": None, "status": "void"}) is True

    def test_the_rule_reads_authority_from_the_stored_row(self):
        """Guards against a regression back to keying on the incoming
        record, which is what made a stale overwrite possible."""
        import ast
        source = open(os.path.join(_ROOT, "lib", "edgelab", "bets.py"), encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_inherit_lifecycle_if_not_supplied")
        called = {getattr(c.func, "id", getattr(c.func, "attr", ""))
                  for c in ast.walk(fn) if isinstance(c, ast.Call)}
        assert "_has_canonical_outcome" in called

    def test_the_final_state_constants_are_not_a_second_field_list(self):
        """_FINAL_RESULTS/_FINAL_STATUSES describe lifecycle STATES; the
        preserved FIELDS must still come from write_placed_bet's own
        constants."""
        import ast
        source = open(os.path.join(_ROOT, "lib", "edgelab", "bets.py"), encoding="utf-8").read()
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_inherit_lifecycle_if_not_supplied")
        names = {getattr(x, "id", "") for x in ast.walk(fn) if isinstance(x, ast.Name)}
        assert "_ALWAYS_PRESERVE_FIELDS" in names
        assert "_PRESERVE_IF_NOT_SUPPLIED_FIELDS" in names

    def test_a_supplied_linkage_still_overrides(self):
        out = reconcile_with_existing(_legacy(recommendationId="rec-NEW"), {"b1": _settled()})
        assert out["recommendationId"] == "rec-NEW"

    def test_created_at_is_still_preserved_from_the_existing_row(self):
        out = reconcile_with_existing(_legacy(stake=25.0, createdAt="2026-01-01T00:00:00Z"),
                                      {"b1": _settled(createdAt="2026-08-09T00:00:00Z")})
        assert out["createdAt"] == "2026-08-09T00:00:00Z"

    def test_a_genuinely_new_bet_is_returned_verbatim(self):
        record = _legacy(betId="brand-new")
        assert reconcile_with_existing(record, {}) is record

    def test_a_void_bet_is_not_revived(self):
        out = reconcile_with_existing(_legacy(stake=25.0), {"b1": _settled(status="void")})
        assert out["status"] == "void"


class TestTheWorkflowOrderingThatMadeThisReachable:
    def test_reingest_runs_after_settlement_in_the_postgame_workflow(self):
        body = open(os.path.join(_ROOT, ".github", "workflows", "edgelab-postgame.yml"),
                    encoding="utf-8").read()
        settle = body.index("scripts/edgelab/settle_markets.py")
        ingest = body.index("scripts/edgelab/ingest_existing_bets.py")
        assert settle < ingest, (
            "this test documents WHY the bug was reachable: re-ingest runs after "
            "settlement in the same job, so any lifecycle reset lands on freshly "
            "written settlements")


class TestRealLedgerNoRegression:
    """Requirement 12, against the REAL committed ledger rather than a
    fixture: a full re-ingest must not lose a settled row, must not
    disturb the void row, and must not move P/L except by legitimately
    settling a row that had no canonical outcome."""

    LEDGER = os.path.join(_ROOT, "data", "edgelab", "bets", "bets.jsonl")

    def _ledger(self):
        import json
        with open(self.LEDGER, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _reingested(self, rows):
        """Apply the reconcile step every row would go through, using each
        row's own stored state as the existing record and its entry-time
        shape as the incoming legacy record."""
        by_id = {r["betId"]: r for r in rows}
        out = []
        for row in rows:
            legacy = dict(row)
            # what a legacy ledger actually carries: entry-time only
            for field in ("status", "result", "netProfitLoss", "returnAmount",
                          "closingPrice", "clv", "clvQuoteId"):
                legacy[field] = None
            legacy["status"] = "pending"
            out.append(reconcile_with_existing(legacy, by_id))
        return out

    def test_settled_count_does_not_decrease(self):
        before = self._ledger()
        settled_before = sum(1 for r in before if r.get("status") == "settled")
        after = self._reingested(before)
        settled_after = sum(1 for r in after if r.get("status") == "settled")
        assert settled_after >= settled_before, (
            f"re-ingest lost settled rows: {settled_before} -> {settled_after}")

    def test_the_void_row_is_untouched(self):
        before = self._ledger()
        void_before = [r["betId"] for r in before if r.get("status") == "void"]
        after = {r["betId"]: r for r in self._reingested(before)}
        for bet_id in void_before:
            assert after[bet_id].get("status") == "void"

    def test_ledger_pl_is_unchanged(self):
        before = self._ledger()
        pl_before = sum(r["netProfitLoss"] for r in before
                        if r.get("netProfitLoss") is not None)
        after = self._reingested(before)
        pl_after = sum(r["netProfitLoss"] for r in after
                       if r.get("netProfitLoss") is not None)
        assert pl_after == pytest.approx(pl_before), (
            f"re-ingest moved ledger P/L: {pl_before} -> {pl_after}")

    def test_no_finalized_row_loses_its_result(self):
        before = self._ledger()
        after = {r["betId"]: r for r in self._reingested(before)}
        for row in before:
            if row.get("result") in ("WIN", "LOSS", "PUSH", "VOID"):
                assert after[row["betId"]]["result"] == row["result"], (
                    f"{row['betId']} lost its canonical result")
