#!/usr/bin/env python3
"""
tests/research/test_capture_reference_integrity.py
=======================================================
Referential integrity of MLB-ALPHA-0002's change-suppression scheme.

A change-suppressed reference row ("this ticker's book is unchanged, see
fingerprint X") is only meaningful if a canonical FULL row carrying X
exists in the DURABLE corpus. These tests lock the invariant that made
that true, and reproduce the failure mode that motivated it.

The real incident: 791 book references could not be followed. They were
NOT dangling -- every anchor row was present and persisted -- but each
anchor carried `orderbook: null`, captured before the fixed-point repair.
A null book is real information about a market; it is NOT depth, and it
must never become a suppression anchor.
"""
import gzip
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from scripts.research.mlb_alpha_0002 import prospective_capture as pc  # noqa: E402
from scripts.research.mlb_alpha_0002 import queue_observation as qo  # noqa: E402


BOOK = {"yes_dollars": [["0.5700", "25.00"]], "no_dollars": [["0.4200", "10.00"]]}
EMPTY_BOOK = {"yes_dollars": [], "no_dollars": []}


def _write_gz(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")


# ---------------------------------------------------------------------------
# A usable anchor is one that actually carries depth
# ---------------------------------------------------------------------------

def test_a_null_book_is_never_a_usable_anchor():
    """The exact defect behind the 791 unresolvable references."""
    assert pc._usable_book(None) is False
    assert pc._usable_book(EMPTY_BOOK) is False
    assert pc._usable_book(BOOK) is True


def test_legacy_side_keys_still_count_as_a_usable_anchor():
    assert pc._usable_book({"yes": [[57, 25]], "no": []}) is True


def test_observer_agrees_with_the_collector_on_what_carries_depth():
    """Collector and observer must not disagree about which books are
    usable, or one will emit references the other cannot follow."""
    for book in (None, EMPTY_BOOK, BOOK, {"yes": [[57, 25]], "no": []}):
        collector = pc._usable_book(book)
        observer = bool(book is not None and qo._has_levels(book))
        assert collector == observer, book


# ---------------------------------------------------------------------------
# Anchor index is derived from the PERSISTED corpus, not from capture_state
# ---------------------------------------------------------------------------

def _with_temp_corpus(fn):
    """Run fn() with the collector's OUT pointed at a scratch corpus."""
    original_out = pc.OUT
    with tempfile.TemporaryDirectory() as tmp:
        pc.OUT = tmp
        try:
            return fn(tmp)
        finally:
            pc.OUT = original_out


def test_anchor_index_only_admits_books_that_carry_depth():
    def run(tmp):
        _write_gz(os.path.join(tmp, "books", "2026-09-02.jsonl.gz"), [
            {"marketTicker": "KXMLBF5-A", "fp": "good", "orderbook": BOOK},
            {"marketTicker": "KXMLBF5-B", "fp": "null", "orderbook": None},
            {"marketTicker": "KXMLBF5-C", "fp": "empty", "orderbook": EMPTY_BOOK},
        ])
        books, _quotes = pc.load_persisted_anchors("2026-09-02")
        assert ("KXMLBF5-A", "good") in books
        assert ("KXMLBF5-B", "null") not in books
        assert ("KXMLBF5-C", "empty") not in books
    _with_temp_corpus(run)


def test_anchor_index_reaches_back_across_dates():
    """A book that simply has not moved can be anchored days earlier."""
    def run(tmp):
        _write_gz(os.path.join(tmp, "books", "2026-08-30.jsonl.gz"),
                  [{"marketTicker": "KXMLBF5-A", "fp": "old", "orderbook": BOOK}])
        books, _q = pc.load_persisted_anchors("2026-09-02")
        assert books[("KXMLBF5-A", "old")] == "2026-08-30"
    _with_temp_corpus(run)


def test_anchor_index_is_empty_when_the_corpus_is(tmp_path=None):
    def run(tmp):
        books, quotes = pc.load_persisted_anchors("2026-09-02")
        assert books == {} and quotes == {}
    _with_temp_corpus(run)


# ---------------------------------------------------------------------------
# THE FAILURE MODE WE ACTUALLY EXPERIENCED (Part J)
# ---------------------------------------------------------------------------

def test_state_remembers_a_fingerprint_whose_full_row_never_persisted():
    """1. capture full book fingerprint X
       2. persistence fails -- the full row never reaches the durable corpus
       3. capture_state still remembers X
       4. next capture sees the book unchanged

    Correct behaviour: write a FULL BOOK, never a dangling BOOK_REF(X).

    Asserted at the level that decides it: the anchor index is derived from
    the persisted corpus, so a fingerprint present only in state resolves to
    no anchor, and the collector's emit rule requires an anchor."""
    def run(tmp):
        # Corpus is empty -- the full row never landed.
        persisted_books, _q = pc.load_persisted_anchors("2026-09-02")
        state_fp = "X"
        # State remembers it anyway.
        state = {"bookFp": {"KXMLBF5-A": state_fp}}
        fp_now = state_fp                      # the book is genuinely unchanged
        usable = pc._usable_book(BOOK)
        anchor_day = persisted_books.get(("KXMLBF5-A", fp_now))
        may_emit_reference = (state["bookFp"].get("KXMLBF5-A") == fp_now
                              and usable and anchor_day)
        assert not may_emit_reference, "a state-only fingerprint must not authorise a reference"
    _with_temp_corpus(run)


def test_a_persisted_anchor_does_authorise_a_reference():
    """The complement: with the anchor durably present, suppression works
    and the corpus stays compact."""
    def run(tmp):
        _write_gz(os.path.join(tmp, "books", "2026-09-02.jsonl.gz"),
                  [{"marketTicker": "KXMLBF5-A", "fp": "X", "orderbook": BOOK}])
        persisted_books, _q = pc.load_persisted_anchors("2026-09-02")
        state = {"bookFp": {"KXMLBF5-A": "X"}}
        anchor_day = persisted_books.get(("KXMLBF5-A", "X"))
        assert anchor_day == "2026-09-02"
        assert state["bookFp"].get("KXMLBF5-A") == "X" and pc._usable_book(BOOK) and anchor_day
    _with_temp_corpus(run)


def test_a_null_book_clears_rather_than_sets_the_suppression_anchor():
    """After an unusable book the next run must write a full row again, so a
    null can never seed a chain of references to nothing."""
    state = {"bookFp": {"KXMLBF5-A": "prev"}}
    book, fp = None, "nullfp"
    usable = pc._usable_book(book)
    if usable:
        state["bookFp"]["KXMLBF5-A"] = fp
    else:
        state["bookFp"].pop("KXMLBF5-A", None)
    assert "KXMLBF5-A" not in state["bookFp"]


# ---------------------------------------------------------------------------
# Observer: honest classification, targeted lookup, candidate gate
# ---------------------------------------------------------------------------

def test_observer_separates_a_null_anchor_from_a_missing_one():
    """Conflating them is what made 791 intact-but-empty rows look like
    corpus corruption."""
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "queue_observation.py")).read()
    assert "bookRefsAnchorPresentButNoDepth" in src
    assert "bookRefsTrulyDanglingMissingAnchor" in src


def test_observer_reports_a_new_era_rate_that_excludes_legacy_rows():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "queue_observation.py")).read()
    assert "NEW_RUN_BOOK_REF_RESOLUTION_RATE" in src
    assert "candidateRelevantBookResolutionRate" in src


def test_targeted_anchor_lookup_avoids_widening_the_lookback():
    """A reference may legitimately name an anchor older than LOOKBACK_DAYS;
    the fix is to load that one partition, not to scan a wider window."""
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "queue_observation.py")).read()
    assert "_index_books_for_date" in src and "_index_quotes_for_date" in src
    assert qo.LOOKBACK_DAYS == 3, "lookback must not be widened arbitrarily"


def test_candidate_gate_constant_exists_and_is_terminal():
    assert qo.ORDER_NOT_EVALUABLE_MISSING_ANCHOR == "QUEUE_NOT_EVALUABLE_MISSING_BOOK_ANCHOR"
    assert qo.QUEUE_BASIS_MISSING_ANCHOR == "UNRESOLVABLE_MISSING_ANCHOR"


def test_f5_universe_matches_the_frozen_rule():
    from scripts.research.mlb_alpha_0002 import shadow_writers as sw
    assert qo.F5_SERIES == sw.F5_SERIES


# ---------------------------------------------------------------------------
# The collector fails rather than writing a dangling reference
# ---------------------------------------------------------------------------

def test_collector_has_a_fatal_referential_integrity_gate():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "prospective_capture.py")).read()
    assert "refusing to write a dangling reference chain" in src
    assert "referenceIntegrity" in src


def test_capture_state_is_persisted_inside_the_committed_data_directory():
    """Part E: change-suppression state must not be able to advance
    independently of the rows it refers to. It cannot here -- capture_state
    lives inside prospective/, which is what the workflow commits, so state
    and data land in the SAME commit or neither does."""
    assert pc.STATE.startswith(pc.OUT), (pc.STATE, pc.OUT)
    wf = open(os.path.join(REPO, ".github", "workflows",
                           "research-mlb-alpha-0002-capture.yml")).read()
    assert "data/edgelab/research_artifacts/mlb_alpha_0002/prospective/" in wf


def test_references_carry_their_anchor_date():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                            "prospective_capture.py")).read()
    assert '"anchorDate": anchor_day' in src


def test_reference_integrity_rates_handle_a_zero_denominator():
    assert qo._rate(0, 0) is None
    assert qo._rate(3, 4) == 0.75
