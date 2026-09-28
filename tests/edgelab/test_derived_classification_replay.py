#!/usr/bin/env python3
"""
tests/edgelab/test_derived_classification_replay.py
===================================================
THE 2026-09-28 16:04Z ROUTER CONFLICT: the derived-context class, again,
on the classification fields.

The moment edge-finder-api #247 merged and kalshi-bet-router re-seeded its
MLB work tree from main, 11 of the 78 rows it replays came back CONFLICT on
`marketFamily, marketHorizon, selection, threshold` (deliver run
36447265721). They had been written on 2026-09-24..27 while the markets
partition for their dates did not yet hold their tickers, so process_row
stored what it could derive -- null, null, "", null -- and the corpus has
since captured those markets, so the same derivation now says
team_total / FULL_GAME / 3.5. The router's payload never carries any of
those fields.

Contract (lib.edgelab.bets, `derived_context_fields`): a value the ROW
supplied is reported content and a difference is a CONFLICT; a value this
importer DERIVED is context, inherited from the stored row on a replay.
Enriching a stored null is a destination-owned job, never a replay's side
effect.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import bets as bets_lib  # noqa: E402
from lib.edgelab import storage  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BETS_PATH = os.path.join("data", "edgelab", "bets", "bets.jsonl")
GAME_DATE = "2026-09-27"
TICKER = "KXMLBTEAMTOTAL-26SEP271510AZSD-AZ3"


def _load_importer():
    path = os.path.join(ROOT, "scripts", "edgelab", "import_bet_batch.py")
    spec = importlib.util.spec_from_file_location("import_bet_batch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The router's row: identity, economics, side. No classification, no game id.
ROUTER_ROW = {
    "sourceBetKey": "kalshi:v1:e85514bb02d828058d198fd898c0bde1d0cf33ea64d5f7e272de30d468f22b10",
    "gameDate": GAME_DATE, "marketTicker": TICKER, "side": "YES",
    "stake": 24.9932, "entryPrice": 0.74, "contracts": 33.47,
    "entryMethod": "IMPORTED_RECEIPT", "trackingType": "REAL",
}
MARKET = {"marketTicker": TICKER, "gameId": "776501", "marketFamily": "team_total",
          "marketHorizon": "FULL_GAME", "team": "AZ", "threshold": 2.5}
GAME = {"gameId": "776501", "gameDate": GAME_DATE, "awayTeam": "AZ", "homeTeam": "SD",
        "scheduledStartTime": "2026-09-27T19:10:00Z"}


def _seed_market_and_game():
    storage.append_records(storage.partition_path("markets", GAME_DATE), [MARKET], "marketTicker")
    storage.append_records(storage.partition_path("games", GAME_DATE), [GAME], "gameId")


def _run(importer, monkeypatch, row=None):
    payload = {"importBatchId": "kalshi-router-v1", "rows": [dict(row or ROUTER_ROW)]}
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload),
                                      "--receipts-out", "receipts.json"])
    code = importer.main()
    receipts = json.load(open("receipts.json"))
    rows = receipts["rows"] if isinstance(receipts, dict) and "rows" in receipts else receipts
    return code, rows


def _ledger_row():
    row, = list(storage.read_records(BETS_PATH))
    return row


# ── the production shape, end to end ──────────────────────────────────

def test_a_row_written_before_its_market_was_captured_replays_clean_after_it_is(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()

    code, receipts = _run(importer, monkeypatch)          # corpus empty for this date
    assert code == 0 and [r["duplicateStatus"] for r in receipts] == ["NEW"]
    stored = _ledger_row()
    assert (stored["marketFamily"], stored["marketHorizon"], stored["threshold"], stored["selection"]) == (None, None, None, "")
    assert stored["gameId"] is None
    before = open(BETS_PATH, "rb").read()

    _seed_market_and_game()                                # the corpus fills in
    code, receipts = _run(importer, monkeypatch)
    assert code == 0, receipts
    assert [r["duplicateStatus"] for r in receipts] == ["DUPLICATE_NOOP"], receipts
    assert open(BETS_PATH, "rb").read() == before

    code, receipts = _run(importer, monkeypatch)           # and again
    assert [r["duplicateStatus"] for r in receipts] == ["DUPLICATE_NOOP"]
    assert open(BETS_PATH, "rb").read() == before


def test_a_row_written_with_its_market_present_stores_the_derived_classification(tmp_path, monkeypatch):
    """The first write still records what the corpus knows. Only a REPLAY
    leaves the stored derivation alone."""
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()
    _seed_market_and_game()
    code, receipts = _run(importer, monkeypatch)
    assert code == 0 and receipts[0]["duplicateStatus"] == "NEW"
    stored = _ledger_row()
    assert (stored["marketFamily"], stored["marketHorizon"], stored["threshold"]) == ("team_total", "FULL_GAME", 2.5)
    assert stored["selection"] == "team_total FULL_GAME"
    # A router row names no away/home, so game resolution has nothing to
    # match on and gameId stays null; that is the importer's existing rule.
    assert stored["gameId"] is None


def test_a_replay_after_the_market_record_changes_is_still_a_noop(tmp_path, monkeypatch):
    """A corpus that later re-classifies the market (a corrected threshold in
    the archive) moves the derivation too; the stored row is still canonical."""
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()
    _seed_market_and_game()
    assert _run(importer, monkeypatch)[1][0]["duplicateStatus"] == "NEW"
    before = open(BETS_PATH, "rb").read()

    storage.upsert_records(storage.partition_path("markets", GAME_DATE), [dict(MARKET, threshold=3.5)], "marketTicker")
    code, receipts = _run(importer, monkeypatch)
    assert code == 0 and receipts[0]["duplicateStatus"] == "DUPLICATE_NOOP", receipts
    assert open(BETS_PATH, "rb").read() == before


# ── what the ROW supplies is still reported content ───────────────────

def test_a_row_that_supplies_a_different_threshold_is_a_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()
    _seed_market_and_game()
    assert _run(importer, monkeypatch)[1][0]["duplicateStatus"] == "NEW"
    before = open(BETS_PATH, "rb").read()

    code, receipts = _run(importer, monkeypatch, row=dict(ROUTER_ROW, threshold=4.5))
    assert code != 0
    assert receipts[0]["duplicateStatus"] == "CONFLICT"
    assert "threshold" in {d["field"] for d in receipts[0]["conflictingFields"]}
    assert open(BETS_PATH, "rb").read() == before


def test_a_row_that_supplies_a_different_market_family_is_a_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()
    _seed_market_and_game()
    assert _run(importer, monkeypatch)[1][0]["duplicateStatus"] == "NEW"
    code, receipts = _run(importer, monkeypatch, row=dict(ROUTER_ROW, marketFamily="game_total"))
    assert receipts[0]["duplicateStatus"] == "CONFLICT"
    assert "marketFamily" in {d["field"] for d in receipts[0]["conflictingFields"]}


def test_economics_that_differ_are_still_a_conflict_named_without_the_derived_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importer = _load_importer()
    assert _run(importer, monkeypatch)[1][0]["duplicateStatus"] == "NEW"   # nulls stored
    _seed_market_and_game()                                                 # derivation moved too
    code, receipts = _run(importer, monkeypatch, row=dict(ROUTER_ROW, stake=30.0))
    assert receipts[0]["duplicateStatus"] == "CONFLICT"
    names = {d["field"] for d in receipts[0]["conflictingFields"]}
    assert "stake" in names
    assert names.isdisjoint({"marketFamily", "marketHorizon", "threshold", "selection", "gameId", "scheduledStart"}), names


# ── the write path's own contract ─────────────────────────────────────

def _stored(**overrides):
    row = {
        "schemaVersion": "1", "betId": "bet-under-test", "sourceBetKey": "kalshi:v1:x",
        "importBatchId": "kalshi-router-v1", "marketTicker": TICKER, "gameDate": GAME_DATE,
        "side": "YES", "stake": 24.9932, "entryPrice": 0.74, "selection": "", "source": "MANUAL",
        "validationStatus": "valid", "status": "pending", "result": None, "recordStatus": "ACTIVE",
        "marketFamily": None, "marketHorizon": None, "threshold": None, "gameId": None, "scheduledStart": None,
        "recordedAt": "2026-09-27T17:30:42Z", "createdAt": "2026-09-27T17:30:42Z", "updatedAt": None,
        "provenance": {"sourceSystem": "manual_entry", "capturedAt": None, "ingestedAt": "2026-09-27T17:30:42Z"},
        "recommendationId": None, "modelEvaluationId": None, "modelSupported": None,
        "snapshotId": None, "productionRunId": None, "replayRunId": None,
        "marketObservationLinkage": None,
    }
    row.update(overrides)
    return row


def _ledger(tmp_path, monkeypatch, *rows):
    monkeypatch.chdir(tmp_path)
    ledger = tmp_path / storage.singleton_path("bets", "bets.jsonl")
    ledger.parent.mkdir(parents=True)
    ledger.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return ledger


def test_declared_derived_fields_are_inherited_and_undeclared_ones_are_not(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored())
    candidate = _stored(marketFamily="team_total", marketHorizon="FULL_GAME", threshold=2.5,
                        selection="team_total FULL_GAME", recordedAt="2026-09-28T16:04:00Z")

    declared = bets_lib.write_placed_bet(
        candidate, derived_context_fields=("marketFamily", "marketHorizon", "threshold", "selection"))
    assert declared["duplicateStatus"] == "DUPLICATE_NOOP"

    undeclared = bets_lib.write_placed_bet(candidate)
    assert undeclared["duplicateStatus"] == "CONFLICT"
    assert {d["field"] for d in undeclared["conflictingFields"]} == {"marketFamily", "marketHorizon", "threshold", "selection"}
    assert ledger.read_text().count("\n") == 1


def test_resettling_on_purpose_still_replaces_declared_derived_fields(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path, monkeypatch, _stored())
    candidate = _stored(marketFamily="team_total", marketHorizon="FULL_GAME", threshold=2.5, selection="team_total FULL_GAME")
    receipt = bets_lib.write_placed_bet(
        candidate, on_conflict="overwrite", resettle_derived_context=True,
        derived_context_fields=("marketFamily", "marketHorizon", "threshold", "selection"))
    assert receipt["duplicateStatus"] == "CORRECTED"
    after, = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert after["threshold"] == 2.5 and after["recordStatus"] == "CORRECTED"


def test_the_importer_never_declares_a_field_the_row_supplied():
    text = open(os.path.join(ROOT, "scripts", "edgelab", "import_bet_batch.py")).read()
    for name in ("marketFamily", "marketHorizon", "threshold", "selection"):
        assert f'("{name}", ' in text, name
    assert '("gameId", False)' in text and '("scheduledStart", False)' in text
