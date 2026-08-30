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

    def test_a_legacy_row_that_SUPPLIES_a_result_is_still_a_settlement_source(self):
        """The asymmetry. A legacy ledger is the ONLY record of an outcome
        for manual days and for every bet predating the settlement
        archive, so it must still be able to SET a result -- it may only
        no longer UNSET one by silence."""
        out = reconcile_with_existing(
            _legacy(result="LOSS", status="settled", netProfitLoss=-10.0),
            {"b1": _settled(result=None, status="pending", netProfitLoss=None)})
        assert out["result"] == "LOSS"
        assert out["status"] == "settled"

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
