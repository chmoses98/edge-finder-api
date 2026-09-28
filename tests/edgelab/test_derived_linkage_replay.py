#!/usr/bin/env python3
"""
tests/edgelab/test_derived_linkage_replay.py
============================================
THE 2026-09-22 ROUTER CONFLICT, and the contract that ends the class.

`marketObservationLinkage` is DERIVED at write time from the market corpus
("the latest valid pregame observation for this exact ticker"), and the
corpus keeps changing after the row is written. kalshi-bet-router re-imports
its whole open MLB batch on every delivery run, so a row whose derivation had
moved came back CONFLICT on that one field forever, the gate refused the
batch, and every other wager in it sat unmerged behind it.

The production shape, reproduced here from the real ledger and observation
partition (identity and provenance only; no economics beyond what the
fixture needs to validate):

    wager      5911cb7b... KXMLBGAME-26SEP222040AZCOL-AZ  written 2026-09-22T23:58:18Z
    stored     observation 04f904cd  captured 19:42:24.372Z  (latest valid pregame AT THE WRITE)
    later      observation b920d982  captured 23:41:21.010Z  INGESTED 2026-09-23T00:58:59Z
               -- taken before the write, still pregame, landed in the
                  partition an hour after the row existed.

Neither value is wrong. The contract (lib.edgelab.bets._DERIVED_CONTEXT_FIELDS):

  * a replay never moves derived context -- the stored derivation is carried
    onto the candidate, so a replay differing in nothing else is DUPLICATE_NOOP
    and the ledger is byte-identical afterwards;
  * reported content that differs is still a CONFLICT, and the receipt names
    only what the caller actually changed;
  * re-settling derived context is explicit (`resettle_derived_context=True`),
    which only the repair script passes, and only once the game has started.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import bets as bets_lib  # noqa: E402
from lib.edgelab import storage  # noqa: E402
from lib.edgelab.observation_linkage import build_linkage_field  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_script(name):
    path = os.path.join(ROOT, "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TICKER = "KXMLBGAME-26SEP222040AZCOL-AZ"
GAME_DATE = "2026-09-22"
BETS_PATH = os.path.join("data", "edgelab", "bets", "bets.jsonl")


def _obs(oid, captured_at, *, valid_pregame=True, yes_ask=0.65):
    return {
        "marketObservationId": oid, "runId": f"MARKET_OBSERVATION_INGEST_{oid}",
        "marketTicker": TICKER, "capturedAt": captured_at, "yesAsk": yes_ask, "noAsk": round(1 - yes_ask, 2),
        "isValidPregameObservation": valid_pregame, "source": "kalshi_registry_snapshots",
    }


EARLY = _obs("04f904cd0b8a7402c8e5e070eac2a5c69807f2ef", "2026-09-22T19:42:24.372Z", yes_ask=0.65)
LATE = _obs("b920d982b69cc0fd7ef047028e2e2ac37a553227", "2026-09-22T23:41:21.010Z", yes_ask=0.63)
POSTGAME = _obs("postgame", "2026-09-23T03:10:00.000Z", valid_pregame=False, yes_ask=0.99)


def _stored_row(**overrides):
    """The canonical router row as it sits on the ledger: linked to EARLY."""
    row = {
        "schemaVersion": "1", "betId": "5911cb7b6fd31c0ff46c09e9cc5751890a9aeecf",
        "sourceBetKey": "kalshi:v1:b8e1868c99151cff5469003cf4c1831c0b5abee033b55e722a2196d765e7b4ca",
        "importBatchId": "kalshi-router-v1", "marketTicker": TICKER, "gameDate": GAME_DATE,
        "side": "YES", "scheduledStart": None, "stake": 50.7145, "entryPrice": 0.63,
        "selection": "game_result FULL_GAME", "source": "MANUAL", "validationStatus": "valid",
        "status": "settled", "result": "WIN", "recordStatus": "ACTIVE",
        "recordedAt": "2026-09-22T23:58:18Z", "createdAt": "2026-09-22T23:58:18Z",
        "updatedAt": "2026-09-23T19:02:48Z",
        "provenance": {"sourceSystem": "manual_entry", "capturedAt": None, "ingestedAt": "2026-09-22T23:58:18Z"},
        # Every production row carries the async-linkage group as explicit
        # nulls (build_manual_bet_record emits them); a fixture that omitted
        # them would differ from its own replay by key presence alone.
        "recommendationId": None, "modelEvaluationId": None, "modelSupported": None,
        "snapshotId": None, "productionRunId": None, "replayRunId": None,
        "marketObservationLinkage": build_linkage_field([EARLY], side="YES"),
    }
    row.update(overrides)
    return row


def _ledger(tmp_path, monkeypatch, *rows):
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return ledger


def _replay(row, **changes):
    """What the router's re-import hands the write path: the same reported
    content, a FRESH derivation of the linkage from a corpus that now also
    holds the later capture, and a fresh recordedAt."""
    candidate = dict(row)
    candidate.update(changes)
    candidate["marketObservationLinkage"] = build_linkage_field([EARLY, LATE], side=row["side"])
    candidate["recordedAt"] = "2026-09-28T12:52:07Z"
    candidate["createdAt"] = "2026-09-28T12:52:07Z"
    candidate["updatedAt"] = None
    return candidate


# ── the production case ───────────────────────────────────────────────

def test_the_fixture_reproduces_the_production_drift():
    stored = _stored_row()
    fresh = _replay(stored)["marketObservationLinkage"]
    assert stored["marketObservationLinkage"]["observedAt"] == "2026-09-22T19:42:24.372Z"
    assert fresh["observedAt"] == "2026-09-22T23:41:21.010Z"
    assert fresh != stored["marketObservationLinkage"]


def test_a_replay_whose_only_difference_is_derived_linkage_is_a_duplicate_noop(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    before = ledger.read_bytes()

    receipt = bets_lib.write_placed_bet(_replay(_stored_row()))

    assert receipt["success"], receipt
    assert receipt["duplicateStatus"] == "DUPLICATE_NOOP"
    assert receipt.get("conflictingFields") in (None, [])
    assert ledger.read_bytes() == before, "a no-op must not touch the ledger"


def test_the_stored_derivation_stays_canonical_after_the_replay(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    bets_lib.write_placed_bet(_replay(_stored_row()))
    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["marketObservationLinkage"]["observationId"] == EARLY["marketObservationId"]
    assert after["recordStatus"] == "ACTIVE"


def test_an_unlinked_row_is_not_silently_upgraded_by_a_replay(tmp_path, monkeypatch):
    """null -> LINKED is still a move of derived context; it goes through
    the repair path once the game has started, never through a replay."""
    stored = _stored_row(marketObservationLinkage=build_linkage_field([], side="YES"))
    assert stored["marketObservationLinkage"]["linkageStatus"] == "UNLINKED"
    ledger = _ledger(tmp_path, monkeypatch, stored)
    before = ledger.read_bytes()

    receipt = bets_lib.write_placed_bet(_replay(stored))

    assert receipt["duplicateStatus"] == "DUPLICATE_NOOP"
    assert ledger.read_bytes() == before


def test_the_replay_is_idempotent_across_runs(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    first = bets_lib.write_placed_bet(_replay(_stored_row()))
    snapshot = ledger.read_bytes()
    second = bets_lib.write_placed_bet(_replay(_stored_row()))
    assert first["duplicateStatus"] == second["duplicateStatus"] == "DUPLICATE_NOOP"
    assert ledger.read_bytes() == snapshot


# ── what is STILL a conflict ──────────────────────────────────────────

def test_reported_content_that_differs_is_still_refused_and_named_precisely(tmp_path, monkeypatch):
    """The contract narrows the comparison to what the caller reported; it
    does not weaken it. A different stake is a CONFLICT, and the receipt
    names the stake -- not the linkage the caller never reported."""
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    before = ledger.read_bytes()

    receipt = bets_lib.write_placed_bet(_replay(_stored_row(), stake=75.0))

    assert receipt["success"] is False
    assert receipt["duplicateStatus"] == "CONFLICT"
    names = {d["field"] for d in receipt["conflictingFields"]}
    assert "stake" in names
    assert "marketObservationLinkage" not in names
    assert ledger.read_bytes() == before


def test_a_different_side_is_still_a_conflict(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, _stored_row())
    receipt = bets_lib.write_placed_bet(_replay(_stored_row(), side="NO"))
    assert receipt["duplicateStatus"] == "CONFLICT"
    assert {d["field"] for d in receipt["conflictingFields"]} >= {"side"}


def test_a_different_entry_price_is_still_a_conflict(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, _stored_row())
    receipt = bets_lib.write_placed_bet(_replay(_stored_row(), entryPrice=0.61))
    assert receipt["duplicateStatus"] == "CONFLICT"
    assert "entryPrice" in {d["field"] for d in receipt["conflictingFields"]}


# ── the explicit repair path still works, and only it moves the linkage ──

def test_an_overwrite_correction_of_reported_content_keeps_the_stored_linkage(tmp_path, monkeypatch):
    """Correcting a stake on purpose must not drag a fresh derivation along
    with it: derived context moves only when a caller says so."""
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    receipt = bets_lib.write_placed_bet(_replay(_stored_row(), stake=75.0), on_conflict="overwrite")
    assert receipt["duplicateStatus"] == "CORRECTED"
    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["stake"] == 75.0
    assert after["marketObservationLinkage"]["observationId"] == EARLY["marketObservationId"]


def test_resettling_on_purpose_replaces_the_linkage_and_nothing_else(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    stored = _stored_row()
    candidate = dict(stored)
    candidate["marketObservationLinkage"] = build_linkage_field([EARLY, LATE, POSTGAME], side="YES")

    receipt = bets_lib.write_placed_bet(candidate, on_conflict="overwrite", resettle_derived_context=True)

    assert receipt["duplicateStatus"] == "CORRECTED"
    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["marketObservationLinkage"]["observationId"] == LATE["marketObservationId"]
    assert after["recordStatus"] == "CORRECTED"
    changed = {f for f in set(stored) | set(after) if stored.get(f) != after.get(f)}
    assert changed <= {"marketObservationLinkage", "recordStatus", "updatedAt"}, sorted(changed)
    assert after["stake"] == stored["stake"] and after["result"] == "WIN"


def test_after_a_resettle_the_next_replay_is_a_duplicate_noop(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    candidate = dict(_stored_row())
    candidate["marketObservationLinkage"] = build_linkage_field([EARLY, LATE, POSTGAME], side="YES")
    bets_lib.write_placed_bet(candidate, on_conflict="overwrite", resettle_derived_context=True)
    snapshot = ledger.read_bytes()

    receipt = bets_lib.write_placed_bet(_replay(_stored_row()))
    assert receipt["duplicateStatus"] == "DUPLICATE_NOOP"
    assert ledger.read_bytes() == snapshot


def test_the_repair_script_is_the_one_caller_that_resettles():
    text = open(os.path.join(ROOT, "scripts", "edgelab", "repair_stale_observation_linkage.py")).read()
    assert "resettle_derived_context=True" in text
    importer = open(os.path.join(ROOT, "scripts", "edgelab", "import_bet_batch.py")).read()
    assert "resettle_derived_context" not in importer, "the importer must never re-settle on a replay"


def test_the_repair_script_still_repairs_through_the_new_contract(tmp_path, monkeypatch):
    """Guards the guard: if the write path ever inherited derived context
    unconditionally, the repair would become a DUPLICATE_NOOP that fixed
    nothing. It must still CORRECT once the game has started."""
    repair = _load_script("repair_stale_observation_linkage.py")

    class FakeStorage:
        def partition_path(self, entity, date, compressed=False):
            return f"{entity}/{date}"

        def read_records(self, path):
            return [EARLY, LATE, POSTGAME] if path.endswith(GAME_DATE) else []

    ledger = _ledger(tmp_path, monkeypatch, _stored_row())
    (row, fresh, reason), = repair.find_candidates([_stored_row()], storage_module=FakeStorage())
    assert reason is None
    candidate = dict(row)
    candidate["marketObservationLinkage"] = fresh
    receipt = bets_lib.write_placed_bet(candidate, on_conflict="overwrite", resettle_derived_context=True)
    assert receipt["duplicateStatus"] == "CORRECTED"
    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["marketObservationLinkage"]["observedAt"] == "2026-09-22T23:41:21.010Z"


# ── no unrelated row is touched ───────────────────────────────────────

def test_the_replay_touches_no_other_row(tmp_path, monkeypatch):
    other = _stored_row(betId="other-bet", sourceBetKey="kalshi:v1:other", marketTicker="KXMLBGAME-OTHER")
    ledger = _ledger(tmp_path, monkeypatch, other, _stored_row())
    before = ledger.read_text().splitlines()

    bets_lib.write_placed_bet(_replay(_stored_row()))
    bets_lib.write_placed_bet(_replay(_stored_row(), stake=75.0))  # refused

    assert ledger.read_text().splitlines() == before


# ── the whole importer, end to end, on the production shape ───────────

ROUTER_ROW = {
    "sourceBetKey": "kalshi:v1:b8e1868c99151cff5469003cf4c1831c0b5abee033b55e722a2196d765e7b4ca",
    "gameDate": GAME_DATE, "marketTicker": TICKER, "side": "YES",
    "stake": 50.7145, "entryPrice": 0.63, "contracts": 79.47,
    "entryMethod": "IMPORTED_RECEIPT", "trackingType": "REAL",
}


def _seed_observations(*observations):
    path = storage.partition_path("observations", GAME_DATE, compressed=True)
    storage.write_all_records(path, list(observations))


def _run_importer(import_script, monkeypatch):
    payload = {"importBatchId": "kalshi-router-v1", "rows": [dict(ROUTER_ROW)]}
    receipts_out = "receipts.json"
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload),
                                      "--receipts-out", receipts_out])
    code = import_script.main()
    receipts = json.load(open(receipts_out))
    rows = receipts["rows"] if isinstance(receipts, dict) and "rows" in receipts else receipts
    return code, rows


def test_the_router_batch_replays_clean_after_a_late_ingested_pregame_capture(tmp_path, monkeypatch):
    """prepare the corpus as it was at 23:58Z (EARLY only) -> import (NEW) ->
    the 23:41Z capture is ingested an hour later -> the router replays its
    batch -> DUPLICATE_NOOP, ledger byte-identical, stored linkage EARLY."""
    monkeypatch.chdir(tmp_path)
    import_script = _load_script("import_bet_batch.py")

    _seed_observations(EARLY)
    code, receipts = _run_importer(import_script, monkeypatch)
    assert code == 0, receipts
    assert [r["duplicateStatus"] for r in receipts] == ["NEW"]
    written, = list(storage.read_records(BETS_PATH))
    assert written["marketObservationLinkage"]["observationId"] == EARLY["marketObservationId"]
    before = open(BETS_PATH, "rb").read()

    _seed_observations(EARLY, LATE)  # the recovered snapshot lands
    code, receipts = _run_importer(import_script, monkeypatch)
    assert code == 0, receipts
    assert [r["duplicateStatus"] for r in receipts] == ["DUPLICATE_NOOP"]
    assert open(BETS_PATH, "rb").read() == before

    code, receipts = _run_importer(import_script, monkeypatch)  # and again: idempotent
    assert [r["duplicateStatus"] for r in receipts] == ["DUPLICATE_NOOP"]
    assert open(BETS_PATH, "rb").read() == before


def test_the_importer_still_refuses_a_genuinely_different_row(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import_script = _load_script("import_bet_batch.py")
    _seed_observations(EARLY)
    assert _run_importer(import_script, monkeypatch)[0] == 0
    before = open(BETS_PATH, "rb").read()

    _seed_observations(EARLY, LATE)
    payload = {"importBatchId": "kalshi-router-v1", "rows": [dict(ROUTER_ROW, stake=75.0)]}
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload),
                                      "--receipts-out", "receipts.json"])
    code = import_script.main()
    receipts = json.load(open("receipts.json"))
    rows = receipts["rows"] if isinstance(receipts, dict) and "rows" in receipts else receipts
    assert code != 0
    assert rows[0]["duplicateStatus"] == "CONFLICT"
    assert "stake" in {d["field"] for d in rows[0]["conflictingFields"]}
    assert open(BETS_PATH, "rb").read() == before
