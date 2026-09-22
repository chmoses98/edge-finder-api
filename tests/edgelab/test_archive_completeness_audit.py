#!/usr/bin/env python3
"""
tests/edgelab/test_archive_completeness_audit.py
================================================
Coverage for scripts/edgelab/build_archive_completeness_audit.py.

The audit answers whether discovered == archived + explicitly excluded.
Its hardest requirement is what it must NOT call loss: deduplication is a
working mechanism, and counting a deduplicated tick as a missing market
manufactures a defect out of correct behaviour. The reconciliation
identity and the lower-bound truncation measure are both tested against
that line.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab.build_archive_completeness_audit import (
    PEER_SERIES_SIGNIFICANCE, SCHEMA_VERSION, failed_series, load_snapshots,
    reconcile_run, truncation_loss,
)


def _fail(series):
    return {"status": 429, "url": "x?series_ticker=%s&status=open" % series}


def _run(built, written, dropped=0, dup=0, excluded=0, status="success"):
    return {"startedAt": "2026-09-19T21:25:54Z", "status": status, "counts": {
        "observationsBuilt": built, "observationsWritten": written,
        "observationsDroppedNoChange": dropped, "observationsSkippedDuplicate": dup,
        "marketsExcluded": excluded}}


# --------------------------------------------------------------------------
# the reconciliation identity
# --------------------------------------------------------------------------

def test_a_balanced_run_leaves_nothing_unaccounted():
    assert reconcile_run(_run(3880, 3408, dropped=472))["unaccountedObservations"] == 0


def test_deduplication_is_reconciled_not_counted_as_loss():
    """observationsDroppedNoChange is a tick identical on every price and
    status field to the last retained row. The archive is a change log; the
    price at that moment is known from the row it duplicates. Calling it a
    missing market invents a defect out of a working mechanism."""
    result = reconcile_run(_run(4247, 0, dropped=2563, dup=1684))
    assert result["unaccountedObservations"] == 0
    assert result["droppedNoChange"] == 2563


def test_a_market_that_vanishes_with_no_counter_is_unaccounted():
    """The one thing the identity exists to catch."""
    assert reconcile_run(_run(4247, 3853, dropped=0))["unaccountedObservations"] == 394


def test_a_run_with_no_observation_counts_is_skipped_not_scored_as_balanced():
    assert reconcile_run({"status": "success", "counts": {"gamesUpserted": 15}}) is None


def test_the_reported_status_is_carried_through_not_recomputed():
    assert reconcile_run(_run(10, 10, status="partial"))["status"] == "partial"


# --------------------------------------------------------------------------
# naming the truncated series
# --------------------------------------------------------------------------

def test_a_series_failure_is_named_from_its_own_url():
    assert failed_series({"fetchFailures": [_fail("KXMLBHRR"), _fail("KXMLBSB")]}) == [
        "KXMLBHRR", "KXMLBSB"]


def test_the_broad_discovery_pass_is_named_distinctly():
    snapshot = {"fetchFailures": [{"status": 429, "url": ".../markets?status=open"}]}
    assert failed_series(snapshot) == ["__broad_discovery__"]


def test_a_clean_snapshot_names_nothing():
    assert failed_series({"fetchFailures": []}) == []


# --------------------------------------------------------------------------
# lower-bound truncation loss
# --------------------------------------------------------------------------

def test_a_series_absent_that_complete_peers_saw_is_counted_as_loss():
    partial = {"series_counts": {"KXMLBTB": 400}}
    peers = [{"series_counts": {"KXMLBTB": 405, "KXMLBHRR": 1234}}]
    assert truncation_loss(partial, peers) == [
        {"seriesTicker": "KXMLBHRR", "peerPeakMarkets": 1234}]


def test_a_series_merely_smaller_than_its_peers_is_not_counted():
    """Partial truncation within a series is invisible here, and claiming
    it would overstate a figure whose whole value is being a lower bound."""
    partial = {"series_counts": {"KXMLBHRR": 12}}
    peers = [{"series_counts": {"KXMLBHRR": 1234}}]
    assert truncation_loss(partial, peers) == []


def test_a_tiny_series_absence_is_not_evidence_of_truncation():
    """A slate that carried none of a rare series is not a fetch failure."""
    peers = [{"series_counts": {"KXMLBRARE": PEER_SERIES_SIGNIFICANCE - 1}}]
    assert truncation_loss({"series_counts": {}}, peers) == []


def test_with_no_complete_peer_nothing_can_be_proven():
    assert truncation_loss({"series_counts": {}}, []) == []


def test_losses_are_ordered_worst_first():
    peers = [{"series_counts": {"A": 100, "B": 900, "C": 50}}]
    assert [row["seriesTicker"] for row in truncation_loss({"series_counts": {}}, peers)] == [
        "B", "A", "C"]


# --------------------------------------------------------------------------
# reading snapshots
# --------------------------------------------------------------------------

def test_an_unreadable_snapshot_is_surfaced_rather_than_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
    snap_dir.mkdir(parents=True)
    (snap_dir / "kalshi_search_2026-09-19_2124.json").write_text("{not json")
    (snap_dir / "kalshi_search_2026-09-19_2326.json").write_text(json.dumps({"total_markets": 5}))
    loaded = load_snapshots("2026-09-19")
    assert len(loaded) == 2
    assert "__unreadable__" in loaded[0][1]
    assert "__unreadable__" not in loaded[1][1]


def test_schema_version_is_pinned():
    assert SCHEMA_VERSION == "archive_completeness_v2"
