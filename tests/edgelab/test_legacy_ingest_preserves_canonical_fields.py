"""Regression: legacy re-ingest must not erase canonical fields it does not author.

scripts/edgelab/ingest_existing_bets.py runs nightly in
.github/workflows/edgelab-postgame.yml. reconcile_with_existing used to
build its outgoing row FROM the freshly-normalized legacy record, which
only ever contains the ~50 keys lib.edgelab.bets' two legacy normalizers
emit. Every other canonical field survived only if it was ALSO named in
_ALWAYS_PRESERVE_FIELDS/_PRESERVE_IF_NOT_SUPPLIED_FIELDS -- an allow-list
of things to save, so any field added to the schema later was silently
destroyed by the next re-ingest.

clvConvention/clvUnit were added by the CLV sign migration
(docs/EDGELAB_CLV_SIGN_AUDIT.md) and never added to those lists, so the
nightly run stripped canonical CLV provenance off every row it touched
and tests/edgelab/test_clv_convention.py::test_migration_is_idempotent
went red until migrate_clv_sign.py was re-run by hand. The same hole
covered the confirmedReceipt* group, shareCardEvidence,
marketObservationLinkage, the execution-economics block, and the
importBatchId/sourceBetKey import identities.

The fix inverts the default: only _LEGACY_SOURCE_AUTHORED_FIELDS are
overlaid onto the stored row, so anything the legacy source does not
author is preserved by construction -- including fields that do not
exist yet.
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.bets import (  # noqa: E402
    _ALWAYS_PRESERVE_FIELDS,
    _LEGACY_SOURCE_AUTHORED_FIELDS,
    _PRESERVE_IF_NOT_SUPPLIED_FIELDS,
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
    reconcile_with_existing,
)

SCHEMA = os.path.join(_ROOT, "data", "edgelab", "schema_v1", "placed_bet.schema.json")

# Canonical CLV provenance, exactly as collect_clv.py / migrate_clv_sign.py
# leave it.
CLV_FIELDS = {"clv": 11.0, "clvConvention": "POSITIVE_IS_GOOD_V1", "clvUnit": "PERCENTAGE_POINTS"}

# Representative canonical-only enrichment across every group the legacy
# normalizers never author.
CANONICAL_ONLY = {
    "confirmedReceiptReturn": 23.42,
    "confirmedReceiptNetProfitLoss": 13.42,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "confirmedReceiptNote": "user-confirmed screenshot",
    "confirmedReceiptAt": "2026-09-08T00:00:00Z",
    "shareCardEvidence": {"shareCardInitialCost": 9.8},
    "marketObservationLinkage": {"linkageStatus": "LINKED", "observationId": "obs-1"},
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "sourceBetKey": "2026-09-02|MIA-KC|MIA_ML|YES|15.00|49",
    "contractCost": 9.8,
    "totalFees": 0.2,
    "executionStatus": "HELD_TO_SETTLEMENT",
    "executablePriceAtEntry": 0.52,
    "marketHorizon": "F5",
    "contracts": 19,
    "manualFairProbability": 0.58,
    "thesisTags": ["starting_pitcher_edge"],
}


def _stored(**over):
    """A canonical row after settlement, CLV collection and manual enrichment."""
    base = {
        "betId": "b1", "marketTicker": "KXMLBGAME-26AUG091915SFATL-SF", "side": "YES",
        "stake": 10.0, "entryPrice": 0.5, "createdAt": "2026-08-09T00:00:00Z",
        "status": "settled", "result": "WIN", "netProfitLoss": 9.7, "returnAmount": 9.7,
        "closingPrice": 0.61, "clvQuoteId": "q1", "recordStatus": "ACTIVE",
    }
    base.update(CLV_FIELDS)
    base.update(CANONICAL_ONLY)
    base.update(over)
    return base


def _legacy(**over):
    """The same bet as the legacy ledger describes it: entry-time only.

    Mirrors the real normalizers -- the CLV provenance keys are ABSENT
    entirely, and the canonical-identity keys are present but hard-coded
    None because the legacy formats have no such column.
    """
    base = {
        "betId": "b1", "marketTicker": "KXMLBGAME-26AUG091915SFATL-SF", "side": "YES",
        "stake": 10.0, "entryPrice": 0.5, "createdAt": "2026-09-08T00:00:00Z",
        "updatedAt": "2026-09-08T00:00:00Z", "recordedAt": "2026-09-08T00:00:00Z",
        "status": "pending", "result": None, "netProfitLoss": None, "returnAmount": None,
        "closingPrice": None, "clv": None, "clvQuoteId": None, "recordStatus": "ACTIVE",
        "entryMethod": "LEGACY_BACKFILL", "importBatchId": None, "sourceBetKey": None,
        "sourceRow": None, "marketObservationLinkage": None, "contracts": None,
        "marketHorizon": None, "manualFairProbability": None, "thesisTags": [],
    }
    base.update(over)
    return base


def _reingest(stored, legacy):
    return reconcile_with_existing(dict(legacy), {stored["betId"]: dict(stored)})


# --------------------------------------------------------------- TEST A
class TestAClvPreservation:
    """A canonical row's CLV provenance survives a legacy row that lacks it."""

    def test_all_three_clv_fields_are_value_identical_after_reingest(self):
        stored = _stored()
        out = _reingest(stored, _legacy())
        for field, value in CLV_FIELDS.items():
            assert out[field] == value, f"{field} was not preserved"

    @pytest.mark.parametrize("field", sorted(CLV_FIELDS))
    def test_the_field_is_still_present_not_merely_none(self, field):
        out = _reingest(_stored(), _legacy())
        assert field in out, f"{field} was dropped from the record entirely"

    def test_it_survives_even_when_the_legacy_row_forces_a_real_change(self):
        """The bug only bit when content differed at all -- an unchanged
        rerun returned the stored row verbatim and hid it."""
        out = _reingest(_stored(), _legacy(stake=12.0))
        assert out["stake"] == 12.0
        for field, value in CLV_FIELDS.items():
            assert out[field] == value


# --------------------------------------------------------------- TEST B
class TestBLegitimateLegacyUpdatesStillFlow:
    """The legacy source keeps the authority it legitimately has."""

    def test_a_legacy_result_still_settles_a_row_with_no_canonical_outcome(self):
        stored = _stored(status="pending", result=None, netProfitLoss=None, returnAmount=None)
        out = _reingest(stored, _legacy(status="settled", result="LOSS", netProfitLoss=-10.0))
        assert (out["status"], out["result"], out["netProfitLoss"]) == ("settled", "LOSS", -10.0)

    def test_that_settlement_does_not_cost_the_clv_provenance(self):
        stored = _stored(status="pending", result=None, netProfitLoss=None, returnAmount=None)
        out = _reingest(stored, _legacy(status="settled", result="LOSS", netProfitLoss=-10.0))
        for field, value in CLV_FIELDS.items():
            assert out[field] == value

    def test_a_genuine_entry_correction_still_applies(self):
        out = _reingest(_stored(), _legacy(stake=12.0, entryPrice=0.55))
        assert (out["stake"], out["entryPrice"]) == (12.0, 0.55)

    def test_a_finalized_outcome_is_still_never_replaced(self):
        out = _reingest(_stored(result="WIN"), _legacy(status="settled", result="LOSS", netProfitLoss=-10.0))
        assert (out["status"], out["result"], out["netProfitLoss"]) == ("settled", "WIN", 9.7)


# --------------------------------------------------------------- TEST C
class TestCCanonicalOnlyFieldPreservation:
    """Every canonical-only field the legacy source does not author survives."""

    @pytest.mark.parametrize("field", sorted(CANONICAL_ONLY))
    def test_the_field_survives_a_reingest_that_changes_something_else(self, field):
        out = _reingest(_stored(), _legacy(stake=12.0))
        assert out[field] == CANONICAL_ONLY[field]

    def test_an_unknown_future_canonical_field_survives_by_construction(self):
        """The regression-proofing: a field nobody has thought of yet must
        be preserved WITHOUT anyone remembering to extend a list. This is
        the property whose absence caused the original bug."""
        stored = _stored(someFutureCanonicalField={"written_by": "a later milestone"})
        out = _reingest(stored, _legacy(stake=12.0))
        assert out["someFutureCanonicalField"] == {"written_by": "a later milestone"}

    def test_a_hardcoded_none_in_the_legacy_row_does_not_null_canonical_state(self):
        """importBatchId/sourceBetKey/marketObservationLinkage are PRESENT
        in the legacy record and hard-coded None -- the second loss mode."""
        out = _reingest(_stored(), _legacy(stake=12.0))
        assert out["importBatchId"] == CANONICAL_ONLY["importBatchId"]
        assert out["sourceBetKey"] == CANONICAL_ONLY["sourceBetKey"]
        assert out["marketObservationLinkage"] == CANONICAL_ONLY["marketObservationLinkage"]


# ------------------------------------------------------- OWNERSHIP MODEL
class TestOwnershipModelIsCoherent:
    def test_authored_fields_are_all_real_schema_properties(self):
        properties = set(json.load(open(SCHEMA))["properties"])
        assert _LEGACY_SOURCE_AUTHORED_FIELDS <= properties, (
            "authored set names fields that are not in the PlacedBet schema: "
            f"{sorted(_LEGACY_SOURCE_AUTHORED_FIELDS - properties)}")

    def test_canonical_provenance_groups_are_never_authored_by_a_legacy_ledger(self):
        must_not_own = set(CLV_FIELDS) | set(CANONICAL_ONLY)
        must_not_own.discard("clv")  # a legacy ledger legitimately carries its own clv column
        overlap = must_not_own & _LEGACY_SOURCE_AUTHORED_FIELDS
        assert overlap == set(), f"legacy source must not own: {sorted(overlap)}"

    def test_the_async_linkage_group_keeps_its_preserve_if_not_supplied_authority(self):
        """That group is deliberately overlayable: a legacy row supplying a
        recommendationId may override the stored one, while one leaving it
        empty preserves it (tests/edgelab/test_legacy_ingest_preserves_settlement.py
        pins both directions). It must stay ONE definition, not a second
        list that can drift."""
        assert set(_PRESERVE_IF_NOT_SUPPLIED_FIELDS) <= _LEGACY_SOURCE_AUTHORED_FIELDS

    def test_a_supplied_linkage_overrides_but_an_absent_one_preserves(self):
        stored = _stored(recommendationId="rec-1")
        assert _reingest(stored, _legacy(recommendationId="rec-NEW"))["recommendationId"] == "rec-NEW"
        assert _reingest(stored, _legacy(recommendationId=None))["recommendationId"] == "rec-1"

    def test_every_field_the_normalizers_actually_emit_is_accounted_for(self):
        """No normalizer key may fall outside BOTH the authored set and the
        preserve lists -- that gap is exactly where the bug lived."""
        emitted = set(from_legacy_root_bets_record({"date": "2026-08-01", "game": "SF @ LAD",
                                                    "ticker": "T-1", "market": "SF F5"}, 0))
        emitted |= set(from_legacy_session_bets_record({"date": "2026-08-01", "game": "SF @ LAD",
                                                        "ticker": "T-1", "market": "SF F5"}, 0))
        accounted = (_LEGACY_SOURCE_AUTHORED_FIELDS
                     | set(_ALWAYS_PRESERVE_FIELDS) | set(_PRESERVE_IF_NOT_SUPPLIED_FIELDS)
                     | {"importBatchId", "sourceBetKey", "sourceRow", "marketObservationLinkage",
                        "contracts", "estimatedPayout", "marketHorizon", "manualFairProbability",
                        "correlationGroup", "correlationGroups", "thesisTags"})
        assert emitted <= accounted, f"unaccounted normalizer fields: {sorted(emitted - accounted)}"


# --------------------------------------------------------------- TEST F
class TestFManualImportRowsAreSafe:
    """The Sep 2-7 manual imports (PR #194) are exactly the shape that was
    most exposed: receipt provenance + import identities + CLV."""

    def test_a_manual_import_row_is_untouched_by_an_unrelated_legacy_reingest(self):
        manual = _stored(
            betId="272b06c91f065f30f3ea74af8897ef89c131470c",
            entryMethod="IMPORTED_RECEIPT",
            importBatchId="mlb-manual-2026-09-02-postmortem-v1",
            sourceBetKey="2026-09-02|SD-CIN|F5_SIDE|SD_YES|60.00|51",
        )
        # A legacy row for a DIFFERENT bet must not touch it at all.
        out = reconcile_with_existing(_legacy(betId="other"), {manual["betId"]: dict(manual)})
        assert out["betId"] == "other"

    def test_a_colliding_legacy_row_still_cannot_strip_receipt_provenance(self):
        manual = _stored(entryMethod="IMPORTED_RECEIPT")
        out = _reingest(manual, _legacy(stake=12.0))
        assert out["confirmedReceiptSource"] == "MANUAL_POSTMORTEM_RECEIPT"
        assert out["confirmedReceiptReturn"] == 23.42
        assert out["confirmedReceiptNetProfitLoss"] == 13.42
        assert out["importBatchId"] == "mlb-manual-2026-09-02-postmortem-v1"
        assert out["sourceBetKey"] == "2026-09-02|MIA-KC|MIA_ML|YES|15.00|49"
