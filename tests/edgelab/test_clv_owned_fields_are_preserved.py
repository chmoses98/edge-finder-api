#!/usr/bin/env python3
"""
tests/edgelab/test_clv_owned_fields_are_preserved.py
====================================================
THE SAME BUG, THREE TIMES. THIS IS THE ONE THAT STOPS THE FOURTH.

`scripts/edgelab/collect_clv.py` writes a group of fields onto a canonical
bet row after the fact, from the finalized closing quote. The importer
(`build_manual_bet_record`) has no caller-facing parameter for any of them,
so a freshly built re-import candidate carries NO KEY at all for them.

`_content_fingerprint` compares key presence. So unless every such field is
named in `_ALWAYS_PRESERVE_FIELDS` -- where `_inherit_lifecycle_fields`
copies it onto the candidate, mirroring the stored row's key presence --
an ordinary, faithful re-import of a CLV-scored row is refused as a
CONFLICT against values the caller never supplied and could not know.

  * 2026-09-16: clv/clvQuoteId/closingPrice were listed; clvConvention and
    clvUnit were not. The router's single already-delivered wager was
    refused on those two alone.
  * 2026-09-21: the closing-coverage fields -- closingCoverageClass,
    closingSecondsBeforeStart, closingCheckpoint -- written in the SAME
    assignment block, were not listed either. Router run 35591679347 got
    65 rows: 2 DUPLICATE_NOOP and 63 CONFLICT, on exactly those three
    names. The 63 were exactly the rows CLV had scored; the 2 were the
    only two it had not.

Neither time did anything disagree about a value. The canonical rows were
correct both times and no ledger repair was needed. The defect was
entirely in the declaration.

So this file does not test three field names. It tests the RULE: whatever
collect_clv.py writes onto a bet must be declared pipeline-owned.
"""
import ast
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import bets as bets_lib  # noqa: E402
from lib.edgelab import storage  # noqa: E402

COLLECT_CLV = os.path.join(ROOT, "scripts", "edgelab", "collect_clv.py")

#: The three that broke production on 2026-09-21, named explicitly as well
#: as covered by the rule below -- a named regression is easier to read in
#: a failure report than a derived set.
CLOSING_COVERAGE_FIELDS = (
    "closingCoverageClass", "closingSecondsBeforeStart", "closingCheckpoint",
)


def _fields_collect_clv_writes_onto_a_bet():
    """Every `updated_bet["..."] = ...` key in collect_clv.py.

    Parsed, not grepped: a string that merely appears in the file (a
    reason code, a log line) is not an assignment, and this must not
    demand that unrelated strings be preserved.
    """
    tree = ast.parse(open(COLLECT_CLV).read())
    written = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "updated_bet"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                written.add(target.slice.value)
    return written


# ── the rule ──────────────────────────────────────────────────────────

def test_every_field_the_clv_pipeline_writes_is_declared_pipeline_owned():
    """THE GUARD. Add a field to collect_clv.py's write block without
    adding it here and this fails -- instead of production discovering it
    as a wall of CONFLICTs weeks later."""
    written = _fields_collect_clv_writes_onto_a_bet()
    assert written, "the parser found no updated_bet assignments; it has drifted"

    owned = set(bets_lib._ALWAYS_PRESERVE_FIELDS) | set(
        bets_lib._PRESERVE_IF_NOT_SUPPLIED_FIELDS)
    # updatedAt is volatile bookkeeping that _content_fingerprint already
    # pops, so it can never be the basis of a conflict.
    undeclared = sorted(written - owned - {"updatedAt"})
    assert undeclared == [], (
        f"scripts/edgelab/collect_clv.py writes {undeclared} onto a canonical "
        "bet, but lib/edgelab/bets.py does not declare them pipeline-owned. "
        "Every faithful re-import of a CLV-scored row will be refused as a "
        "CONFLICT on them -- this is the 2026-09-16 and 2026-09-21 defect "
        "recurring. Add them to _ALWAYS_PRESERVE_FIELDS.")


def test_the_three_closing_coverage_fields_are_preserved_by_name():
    for field in CLOSING_COVERAGE_FIELDS:
        assert field in bets_lib._ALWAYS_PRESERVE_FIELDS, field


# ── the behaviour that rule buys ──────────────────────────────────────

def _scored_row(**overrides):
    """A canonical router row the CLV pipeline has already scored."""
    row = {
        "schemaVersion": "1",
        "betId": "row-under-test",
        "sourceBetKey": "kalshi:v1:abc",
        "importBatchId": "kalshi-router-v1",
        "marketTicker": "KXMLBGAME-26SEP20TEST-AAA",
        "gameDate": "2026-09-20",
        "scheduledStart": None,
        "side": "YES",
        "selection": "game_result FULL_GAME",
        "source": "MANUAL",
        "validationStatus": "valid",
        "provenance": {"sourceSystem": "manual_entry", "capturedAt": None,
                       "ingestedAt": "2026-09-20T23:00:00Z"},
        "recordedAt": "2026-09-20T23:00:00Z",
        "createdAt": "2026-09-20T23:00:00Z",
        "updatedAt": "2026-09-20T23:00:00Z",
        "recordStatus": "ACTIVE",
        "status": "pending",
        "result": None,
        # build_manual_bet_record emits every one of these, defaulting to
        # None, so a real stored row and a real candidate BOTH carry the
        # keys. Omitting them here would make _PRESERVE_IF_NOT_SUPPLIED_FIELDS
        # add keys the stored row lacks and test a row shape that cannot
        # occur.
        "recommendationId": None, "modelEvaluationId": None,
        "modelSupported": None, "snapshotId": None,
        "productionRunId": None, "replayRunId": None,
        # execution economics -- must survive untouched
        "stake": 12.0,
        "entryPrice": 0.41,
        "contracts": 29,
        "totalFees": 0.21,
        "actualCashConsumed": 12.11,
        # what the CLV pipeline wrote afterwards
        "clv": -3.0,
        "closingPrice": 0.38,
        "clvQuoteId": "quote-1",
        "closingCoverageClass": "PRE_CLOSE",
        "closingSecondsBeforeStart": 58437.084,
        "closingCheckpoint": "FIRST_DAILY",
    }
    row.update(overrides)
    return row


#: What `build_manual_bet_record` genuinely cannot emit, stated OUTRIGHT
#: rather than derived from `_ALWAYS_PRESERVE_FIELDS`. Deriving it from the
#: constant under test would make every behaviour test below pass against
#: the broken code too: drop only what is already preserved and nothing can
#: ever conflict. status/result/recordStatus are NOT here -- the importer
#: does emit those.
_PIPELINE_ONLY_FIELDS = (
    "clv", "closingPrice", "clvQuoteId", "clvConvention", "clvUnit",
    "closingCoverageClass", "closingSecondsBeforeStart", "closingCheckpoint",
)


def _reimport_candidate(row):
    """What the importer rebuilds: the same entry-time facts, with no key
    at all for anything only the CLV pipeline can know."""
    return {k: v for k, v in row.items() if k not in _PIPELINE_ONLY_FIELDS}


def test_a_reimport_of_a_scored_row_is_a_no_op_not_a_conflict():
    """THE PRODUCTION SYMPTOM, in one row."""
    stored = _scored_row()
    merged = bets_lib._inherit_lifecycle_fields(_reimport_candidate(stored), stored)
    assert (bets_lib._content_fingerprint(stored)
            == bets_lib._content_fingerprint(merged)), bets_lib._diff_fields(stored, merged)


def test_a_row_the_pipeline_has_not_scored_still_no_ops():
    """The counterpart. `_inherit_lifecycle_fields` MIRRORS key presence
    rather than assigning None, so a never-scored row does not acquire
    closing keys its stored row lacks -- which would be the same conflict
    with the sign flipped."""
    stored = _scored_row()
    for field in CLOSING_COVERAGE_FIELDS + ("clv", "closingPrice", "clvQuoteId"):
        stored.pop(field, None)
    merged = bets_lib._inherit_lifecycle_fields(_reimport_candidate(stored), stored)
    for field in CLOSING_COVERAGE_FIELDS:
        assert field not in merged, field
    assert (bets_lib._content_fingerprint(stored)
            == bets_lib._content_fingerprint(merged))


def test_a_wager_imported_before_its_closing_capture_converges_later(tmp_path, monkeypatch):
    """The lifecycle that produced the 63: imported while still pending,
    scored afterwards by the pipeline, then re-imported by the router's
    next run. The last step must be a DUPLICATE_NOOP, and must not undo
    the scoring."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)

    unscored = _scored_row()
    for field in CLOSING_COVERAGE_FIELDS + ("clv", "closingPrice", "clvQuoteId"):
        unscored.pop(field, None)
    receipt = bets_lib.write_placed_bet(unscored)
    assert receipt["duplicateStatus"] == "NEW", receipt

    # The CLV pipeline scores it -- a direct storage write, exactly as
    # collect_clv.py does (storage.upsert_records), never through this path.
    scored = _scored_row()
    storage.upsert_records(str(ledger), [scored], "betId")

    # The router's next run resubmits the same entry-time facts.
    again = bets_lib.write_placed_bet(_reimport_candidate(scored))
    assert again["success"] is True, again
    assert again["duplicateStatus"] == "DUPLICATE_NOOP", again["conflictingFields"]

    # A third, identical run converges too -- no oscillation.
    third = bets_lib.write_placed_bet(_reimport_candidate(scored))
    assert third["duplicateStatus"] == "DUPLICATE_NOOP", third["conflictingFields"]

    landed, = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    for field in CLOSING_COVERAGE_FIELDS:
        assert landed[field] == scored[field], field
    assert landed["clv"] == -3.0 and landed["closingPrice"] == 0.38


def test_the_reimport_changes_no_execution_economics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)
    scored = _scored_row()
    ledger.write_text(json.dumps(scored, sort_keys=True) + "\n")

    bets_lib.write_placed_bet(_reimport_candidate(scored))
    landed, = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
    for field in ("stake", "entryPrice", "contracts", "totalFees",
                  "actualCashConsumed", "betId", "sourceBetKey", "createdAt",
                  "status", "result", "recordStatus"):
        assert landed[field] == scored[field], field


def test_no_unrelated_row_is_touched(tmp_path, monkeypatch):
    """A no-op writes NOTHING, so a ledger of other people's wagers comes
    back byte for byte."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)
    scored = _scored_row()
    other = _scored_row(betId="someone-elses", sourceBetKey="manual:v1:zzz",
                        importBatchId="mlb-manual-2026-09-07-postmortem-v1")
    before = (json.dumps(scored, sort_keys=True) + "\n"
              + json.dumps(other, sort_keys=True) + "\n")
    ledger.write_text(before)

    bets_lib.write_placed_bet(_reimport_candidate(scored))
    assert ledger.read_text() == before


# ── the closing derivation itself is unchanged, and still fails closed ──

def test_a_post_start_quote_can_never_become_closing_evidence():
    """Not changed by this fix, and pinned so it cannot be loosened in the
    name of making conflicts go away."""
    from lib.edgelab.clv import compute_clv_for_bet
    bet = {"entryPrice": 0.41, "side": "YES"}
    post_start = {
        "isClosingQuote": True, "capturedAt": "2026-09-21T00:30:00Z",
        "scheduledStart": "2026-09-21T00:10:00Z", "checkpoint": "T_MINUS_0",
        "yesAsk": 0.5, "priceUnit": "PROBABILITY",
    }
    result = compute_clv_for_bet(bet, [post_start])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "CLOSING_QUOTE_IS_POST_START"
    assert result["closingSecondsBeforeStart"] < 0


def test_missing_closing_evidence_fails_closed():
    from lib.edgelab.clv import compute_clv_for_bet
    result = compute_clv_for_bet({"entryPrice": 0.41, "side": "YES"}, [])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"
    assert "clvCents" not in result


def test_a_scored_row_is_not_rescored_once_it_is_decided():
    """collect_clv.py skips a non-pending bet that already has a clv, so
    finalized closing metadata is stable on every later pass."""
    source = open(COLLECT_CLV).read()
    assert 'if bet.get("status") != "pending" and bet.get("clv") is not None:' in source
    assert "continue" in source
