#!/usr/bin/env python3
"""
tests/edgelab/test_capture_completeness_classification.py
=========================================================
PHASES I/J: classify what a capture PROVED, and never more than that.

The load-bearing test in this file is the one asserting that a LEGACY
snapshot with zero recorded failures is UNKNOWN_LEGACY rather than
COMPLETE. Under the pre-v4 path a live cursor at maxPages=10 was
discarded with no failure recorded anywhere, so `fetchFailureCount: 0`
means "nothing reported an error", which is strictly weaker than
"everything was retrieved".

Getting that wrong would launder an absence of evidence into evidence of
absence -- on exactly the busiest captures, which are the ones a page cap
bites first -- and would hand biased data to family-level research under
a COMPLETE label. Every other test here exists to keep that one honest.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.capture_completeness import (
    COMPLETE, FAILED, PARTIAL, UNKNOWN_LEGACY, classify_snapshot,
    is_research_qualified, reconcile_snapshot,
)
from scripts.edgelab.build_capture_completeness_ledger import build_ledger, snapshot_paths

V4 = "kalshi_capture_v4"


def _v4(paginations, markets=3, status=None, failures=0):
    snap = {"captureContractVersion": V4, "pagination": paginations,
            "markets": [{"ticker": "t%d" % i} for i in range(markets)],
            "fetchFailureCount": failures}
    if status:
        snap["captureStatus"] = status
    return snap


def _pg(series="KXMLBGAME", complete=True, reason=None, scope="series"):
    return {"scope": scope, "series": series, "complete": complete,
            "truncationReason": reason}


# ── the one that matters ────────────────────────────────────────────────────

def test_a_clean_legacy_snapshot_is_unknown_never_complete():
    """fetchFailureCount=0 predates any cursor evidence. The old path could
    discard a live cursor at the page cap with nothing reported."""
    result = classify_snapshot({"fetchFailureCount": 0, "markets": [{"ticker": "a"}]})
    assert result["class"] == UNKNOWN_LEGACY
    assert "not" in result["reason"] and "retrieved" in result["reason"]


def test_a_legacy_snapshot_with_no_completeness_fields_at_all_is_unknown():
    assert classify_snapshot({"markets": []})["class"] == UNKNOWN_LEGACY


def test_unknown_legacy_is_not_research_qualified_by_default():
    assert is_research_qualified(UNKNOWN_LEGACY) is False
    assert is_research_qualified(UNKNOWN_LEGACY, allow_unknown_legacy=True) is True


def test_partial_is_never_research_qualified_even_when_asked_nicely():
    """Its missingness is systematic, so it adds bias, not noise."""
    assert is_research_qualified(PARTIAL) is False
    assert is_research_qualified(PARTIAL, allow_unknown_legacy=True) is False
    assert is_research_qualified(FAILED, allow_unknown_legacy=True) is False


# ── legacy evidence that IS conclusive ──────────────────────────────────────

def test_a_legacy_snapshot_with_recorded_failures_is_provably_partial():
    result = classify_snapshot({
        "fetchFailureCount": 2, "markets": [{"ticker": "a"}],
        "fetchFailures": [{"url": "x?series_ticker=KXMLBSB&status=open"},
                          {"url": "x?series_ticker=KXMLBHRR&status=open"}]})
    assert result["class"] == PARTIAL
    assert result["incompleteSeries"] == ["KXMLBHRR", "KXMLBSB"]


def test_a_legacy_broad_discovery_failure_is_named_distinctly():
    result = classify_snapshot({
        "fetchFailureCount": 1, "markets": [],
        "fetchFailures": [{"url": "https://api/markets?status=open"}]})
    assert result["incompleteSeries"] == ["__broad_discovery__"]


# ── v4 evidence ─────────────────────────────────────────────────────────────

def test_v4_with_every_scope_exhausted_is_complete():
    result = classify_snapshot(_v4([_pg("A"), _pg(None, scope="discovery")]))
    assert result["class"] == COMPLETE
    assert is_research_qualified(result["class"]) is True


def test_v4_with_a_live_cursor_at_the_page_cap_is_partial():
    result = classify_snapshot(_v4(
        [_pg("A"), _pg("KXMLBHRR", complete=False,
                       reason="PAGE_CAP_REACHED_WITH_LIVE_CURSOR")]))
    assert result["class"] == PARTIAL
    assert result["incompleteSeries"] == ["KXMLBHRR"]
    assert result["truncationReasons"] == ["PAGE_CAP_REACHED_WITH_LIVE_CURSOR"]


def test_v4_with_an_incomplete_broad_pass_is_partial():
    result = classify_snapshot(_v4(
        [_pg("A"), _pg(None, complete=False, scope="discovery",
                       reason="ENTRY_CAP_REACHED")]))
    assert result["class"] == PARTIAL


def test_v4_that_retrieved_nothing_and_failed_is_failed():
    result = classify_snapshot(_v4([_pg("A", complete=False, reason="TRANSPORT_ERROR")],
                                   markets=0, status="FAILED"))
    assert result["class"] == FAILED


def test_v4_claiming_completeness_with_no_evidence_is_not_taken_on_trust():
    """A version stamp is a claim; the pagination block is the evidence."""
    assert classify_snapshot(_v4([]))["class"] == UNKNOWN_LEGACY


def test_v4_recorded_failures_override_a_complete_looking_pagination_block():
    result = classify_snapshot(_v4([_pg("A")], failures=1))
    assert result["class"] == PARTIAL


# ── PHASE I: per-capture reconciliation ─────────────────────────────────────

def test_a_balanced_capture_reconciles_to_zero_unaccounted():
    snap = {"reconciliation": {"sourceRecordsReceived": 100, "marketsArchived": 70,
                               "discoveredUnknownSeriesArchived": 5,
                               "explicitlyExcluded": 25}}
    result = reconcile_snapshot(snap)
    assert result["unaccounted"] == 0 and result["balanced"] is True


def test_a_row_the_source_returned_that_we_can_neither_show_nor_explain_is_unaccounted():
    snap = {"reconciliation": {"sourceRecordsReceived": 100, "marketsArchived": 70,
                               "discoveredUnknownSeriesArchived": 0,
                               "explicitlyExcluded": 25}}
    result = reconcile_snapshot(snap)
    assert result["unaccounted"] == 5 and result["balanced"] is False


def test_a_legacy_snapshot_is_reported_unreconcilable_not_scored_as_balanced():
    """It never recorded what the source returned, so there is nothing to
    check -- which must be said, not silently passed."""
    result = reconcile_snapshot({"markets": [], "fetchFailureCount": 0})
    assert result["reconcilable"] is False
    assert result["unaccounted"] is None


# ── the ledger over real evidence ───────────────────────────────────────────

def test_the_ledger_classifies_every_snapshot_it_finds(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    (snap_dir / "kalshi_search_2026-09-19_2124.json").write_text(
        json.dumps({"fetchFailureCount": 0, "markets": [{"ticker": "a"}]}))
    (snap_dir / "kalshi_search_2026-09-19_2326.json").write_text(
        json.dumps({"fetchFailureCount": 1, "markets": [],
                    "fetchFailures": [{"url": "x?series_ticker=KXMLBSB"}]}))
    (snap_dir / "kalshi_search_2026-09-19_2400.json").write_text("{broken")

    ledger = build_ledger(snapshot_paths(str(snap_dir)))
    totals = ledger["totals"]
    assert totals["snapshots"] == 3
    assert totals[UNKNOWN_LEGACY] == 2      # the clean one and the unreadable one
    assert totals[PARTIAL] == 1
    assert totals[COMPLETE] == 0
    assert totals["unreadable"] == 1


def test_the_rolling_undated_snapshot_is_not_double_counted(tmp_path):
    """kalshi_search_<date>.json is a copy of whichever capture ran last;
    counting it would double count that capture under a name that hides
    which one it was."""
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    (snap_dir / "kalshi_search_2026-09-19_2124.json").write_text(
        json.dumps({"fetchFailureCount": 0, "markets": []}))
    (snap_dir / "kalshi_search_2026-09-19.json").write_text(
        json.dumps({"fetchFailureCount": 0, "markets": []}))
    assert build_ledger(snapshot_paths(str(snap_dir)))["totals"]["snapshots"] == 1


def test_an_unreadable_snapshot_is_never_counted_as_complete(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    (snap_dir / "kalshi_search_2026-09-19_2124.json").write_text("{not json")
    ledger = build_ledger(snapshot_paths(str(snap_dir)))
    assert ledger["totals"][COMPLETE] == 0
    assert ledger["totals"]["researchQualifiedSnapshots"] == 0


def test_research_qualified_counts_only_proven_complete_captures(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    (snap_dir / "kalshi_search_2026-09-19_2124.json").write_text(json.dumps(
        _v4([_pg("A")])))
    (snap_dir / "kalshi_search_2026-09-19_2326.json").write_text(json.dumps(
        {"fetchFailureCount": 0, "markets": [{"ticker": "z"}]}))
    totals = build_ledger(snapshot_paths(str(snap_dir)))["totals"]
    assert totals[COMPLETE] == 1
    assert totals[UNKNOWN_LEGACY] == 1
    assert totals["researchQualifiedSnapshots"] == 1
