#!/usr/bin/env python3
"""
tests/edgelab/test_repair_stale_observation_linkage.py
=====================================================
THE 2026-09-19 ROUTER CONFLICT, and the three ways it must NOT be
"fixed".

`marketObservationLinkage` is derived at import time from the market
corpus, and the corpus keeps gaining valid pregame captures until first
pitch. A batch that is RE-imported (only kalshi-bet-router's is)
therefore re-derives a newer linkage, `_content_fingerprint` compares
that field, neither preserve list protects it, and the row comes back
CONFLICT on every delivery run forever.

scripts/edgelab/repair_stale_observation_linkage.py re-settles exactly
that, through the canonical correction path, and only once the input is
frozen. These tests pin the boundaries, because a script that edits the
canonical ledger is only as safe as the cases it refuses.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import bets as bets_lib  # noqa: E402
from lib.edgelab import storage  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "repair_stale_observation_linkage",
    os.path.join(ROOT, "scripts", "edgelab", "repair_stale_observation_linkage.py"),
)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)

TICKER = "KXMLBKS-26SEP192010NYYAZ-TEST-7"
GAME_DATE = "2026-09-19"
ROUTER_BATCH = "kalshi-router-v1"


def _obs(oid, captured_at, valid_pregame=True):
    return {
        "marketObservationId": oid, "runId": f"RUN-{oid}", "marketTicker": TICKER,
        "capturedAt": captured_at, "yesAsk": 0.31, "noAsk": 0.69,
        "isValidPregameObservation": valid_pregame,
        "source": "kalshi_registry_snapshots",
    }


class FakeStorage:
    """Only what observation_linkage asks of a storage module."""

    def __init__(self, by_date):
        self.by_date = by_date

    def partition_path(self, entity, date, compressed=False):
        return f"{entity}/{date}"

    def read_records(self, path):
        return list(self.by_date.get(path.split("/")[-1], []))


def _row(**overrides):
    """A canonical router row, linked to the EARLIER capture."""
    row = {
        "betId": "bet-under-test", "sourceBetKey": "kalshi:v1:abc",
        "importBatchId": ROUTER_BATCH, "marketTicker": TICKER, "gameDate": GAME_DATE,
        "side": "YES", "scheduledStart": None, "stake": 12.0, "entryPrice": 0.31,
        "status": "pending", "result": None, "recordStatus": "ACTIVE",
        "recordedAt": "2026-09-19T23:09:05Z", "createdAt": "2026-09-19T23:09:05Z",
        "updatedAt": "2026-09-19T23:09:05Z",
        # The schema's required set, so write_placed_bet validates this
        # fixture the same way it validates the production row.
        "selection": "pitcher_strikeouts FULL_GAME", "source": "MANUAL",
        "validationStatus": "valid",
        "provenance": {"sourceSystem": "manual_entry", "capturedAt": None,
                       "ingestedAt": "2026-09-19T23:09:05Z"},
        "marketObservationLinkage": {
            "observationId": "early", "marketCorpusRunId": "RUN-early",
            "observedAt": "2026-09-19T21:24:19.751Z", "observedPrice": 0.31,
            "linkageMethod": "EXACT_TICKER_PREGAME_LATEST", "linkageStatus": "LINKED",
            "linkageConfidence": "MEDIUM", "unavailableReason": None,
        },
    }
    row.update(overrides)
    return row


STARTED = FakeStorage({GAME_DATE: [
    _obs("early", "2026-09-19T21:24:19.751Z"),
    _obs("late", "2026-09-19T23:26:44.691Z"),
    _obs("postgame", "2026-09-20T01:31:43.347Z", valid_pregame=False),
]})

NOT_STARTED = FakeStorage({GAME_DATE: [
    _obs("early", "2026-09-19T21:24:19.751Z"),
    _obs("late", "2026-09-19T23:26:44.691Z"),
]})

COMPACTED = FakeStorage({})


def _candidates(rows, storage_module, **kwargs):
    return repair.find_candidates(rows, storage_module=storage_module, **kwargs)


# ── what it acts on ───────────────────────────────────────────────────

def test_a_later_pregame_capture_after_first_pitch_is_actionable():
    """The production case: the row linked to 21:24, a further pregame
    capture landed at 23:26, and the game has since started -- so the set
    of valid pregame candidates can never grow again and the rule has one
    final answer."""
    (row, fresh, reason), = _candidates([_row()], STARTED)
    assert reason is None
    assert fresh["observedAt"] == "2026-09-19T23:26:44.691Z"
    assert fresh["linkageStatus"] == "LINKED"


def test_a_row_whose_linkage_still_agrees_is_not_a_candidate():
    row = _row()
    row["marketObservationLinkage"] = repair.rederive(row, storage_module=STARTED)
    assert _candidates([row], STARTED) == []


# ── what it refuses ───────────────────────────────────────────────────

def test_a_game_that_has_not_started_is_refused():
    """Its linkage may still move again for an ordinary, correct reason.
    Re-settling it now would just be noise, and the next capture would
    re-open the same conflict."""
    (_row_, _fresh, reason), = _candidates([_row()], NOT_STARTED)
    assert reason is not None
    assert "has not started" in reason


def test_a_ticker_whose_observations_were_compacted_away_is_refused_for_a_DIFFERENT_reason():
    """THE FIRST DRY RUN'S LESSON. Run repo-wide this check also matches
    100+ historical manual rows whose partitions have been compacted --
    their derivation differs because the EVIDENCE IS GONE, not because a
    later capture arrived. Re-settling those would rewrite old rows to
    UNLINKED for no operational reason, and reporting it with the
    same wording as a not-yet-started game hides that entirely."""
    (_row_, fresh, reason), = _candidates([_row()], COMPACTED)
    assert reason is not None
    assert "compaction" in reason
    assert "has not started" not in reason
    assert fresh["linkageStatus"] == "UNLINKED"


def test_only_a_batch_that_is_re_imported_is_in_scope_by_default():
    """Only the router re-imports, so only the router's batch can produce
    this conflict. A manual postmortem row is never touched."""
    manual = _row(betId="manual-row", importBatchId="mlb-manual-2026-09-07-postmortem-v1")
    assert _candidates([manual], STARTED) == []
    assert len(_candidates([manual], STARTED,
                           import_batch_ids=("mlb-manual-2026-09-07-postmortem-v1",))) == 1


def test_bet_id_and_batch_filters_are_both_applied():
    rows = [_row(), _row(betId="other", sourceBetKey="kalshi:v1:def")]
    assert len(_candidates(rows, STARTED)) == 2
    assert len(_candidates(rows, STARTED, bet_ids={"other"})) == 1


# ── what the correction may change ────────────────────────────────────

def test_the_correction_changes_the_linkage_and_nothing_else(tmp_path, monkeypatch):
    """Through the canonical write path, starting from the STORED row --
    so no field can be dropped by a payload this script failed to
    rebuild. Everything but the linkage and the two stamps the correction
    path owns must survive byte for byte."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)

    stored = _row()
    stored["schemaVersion"] = "1"
    ledger.write_text(json.dumps(stored, sort_keys=True) + "\n")

    fresh = repair.rederive(stored, storage_module=STARTED)
    candidate = dict(stored)
    candidate["marketObservationLinkage"] = fresh
    receipt = bets_lib.write_placed_bet(candidate, on_conflict="overwrite")
    assert receipt["success"], receipt
    assert receipt["duplicateStatus"] == "CORRECTED"

    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    touched = set(repair.changed_fields(stored, after))
    assert touched <= repair.CORRECTION_STAMPED_FIELDS, sorted(
        touched - repair.CORRECTION_STAMPED_FIELDS)
    assert "marketObservationLinkage" in touched
    assert after["marketObservationLinkage"]["observedAt"] == "2026-09-19T23:26:44.691Z"
    assert after["recordStatus"] == "CORRECTED"
    assert after["createdAt"] == stored["createdAt"], "the correction reset createdAt"
    assert after["stake"] == stored["stake"], "execution economics were touched"
    assert after["entryPrice"] == stored["entryPrice"]
    assert after["betId"] == stored["betId"], "canonical identity changed"
    assert after["sourceBetKey"] == stored["sourceBetKey"]


def test_a_settled_row_is_never_reset_to_pending_by_the_correction(tmp_path, monkeypatch):
    """`_inherit_lifecycle_fields` runs first, so the settlement/CLV
    lifecycle is taken from the STORED row. A linkage correction can
    never walk a graded wager back to pending."""
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)

    stored = _row(status="settled", result="WIN", netProfitLoss=8.4, schemaVersion="1")
    ledger.write_text(json.dumps(stored, sort_keys=True) + "\n")

    candidate = dict(stored, status="pending", result=None, netProfitLoss=None)
    candidate["marketObservationLinkage"] = repair.rederive(stored, storage_module=STARTED)
    receipt = bets_lib.write_placed_bet(candidate, on_conflict="overwrite")
    assert receipt["success"], receipt

    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["status"] == "settled"
    assert after["result"] == "WIN"
    assert after["netProfitLoss"] == 8.4


# ── the thing it must never become ────────────────────────────────────

def test_the_script_names_no_bet_id_of_its_own():
    """A repair script with a betId baked into it is a hand edit wearing
    a script's clothes. Every row it acts on has to come from the drift
    check against the live corpus."""
    source = open(os.path.join(
        ROOT, "scripts", "edgelab", "repair_stale_observation_linkage.py")).read()
    body = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#") and "Usage" not in line)
    # The production betId appears only in the module docstring's worked
    # example and the usage line, never in an expression.
    assert "9e2cc436a80e1f64f1cb34c46b48a5f02b922c19" not in body.split('"""')[2]


def test_it_writes_nothing_in_dry_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)
    stored = _row(schemaVersion="1")
    original = json.dumps(stored, sort_keys=True) + "\n"
    ledger.write_text(original)

    monkeypatch.setattr(repair, "rederive",
                        lambda row, storage_module=None: repair.link_bet_to_observation(
                            row.get("marketTicker"), row.get("gameDate"),
                            side="YES", storage_module=STARTED))
    monkeypatch.setattr(repair, "observations_for",
                        lambda row, storage_module=None: STARTED.by_date[GAME_DATE])

    assert repair.main(["--dry-run"]) == 0
    assert ledger.read_text() == original
