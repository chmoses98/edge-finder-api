"""
tests/edgelab/test_combo_contract_wagers.py
=============================================
A Kalshi COMBO contract (wagerStructure == COMBO_CONTRACT): ONE canonical wager for the one exchange contract held,
graded only from that contract's own final exchange result.

The motivating row: kalshi-bet-router classifies a combo by its legs (every leg MLB) and, until this, refused it as
`combo_not_recordable_by_destination` because this repository settles its own wagers from MLB Stats API game
outcomes and a combo names no game. The router's row is reproduced here in its exact shape
(kalshi_router.production.to_import_row plus the combo fields), with synthetic values.
"""
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage  # noqa: E402
from lib.edgelab.bets import build_manual_bet_record  # noqa: E402
from lib.edgelab.combo_contract_settlement import (  # noqa: E402
    UNRESOLVED_FETCH_FAILED,
    UNRESOLVED_NON_BINARY,
    UNRESOLVED_NOT_FINAL,
    UNRESOLVED_NOT_RETURNED,
    UNRESOLVED_TICKER_MISMATCH,
    combo_contract_outcome,
    settle_combo_contract,
)
from lib.edgelab.schema import validate_record  # noqa: E402


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import_script = _load_script("import_bet_batch.py")
settle_script = _load_script("settle_combo_contracts.py")

BETS_PATH = os.path.join("data", "edgelab", "bets", "bets.jsonl")
COMBO = "KXMVESPORTSMULTIGAMEEXTENDED-S2026SYNTH0003-SYNTHCOMBO"
LEGS = [
    {"marketTicker": "KXMLBGAME-26OCT032010SYNAWYHOM-HOM", "eventTicker": "KXMLBGAME-26OCT032010SYNAWYHOM", "side": "YES"},
    {"marketTicker": "KXMLBTOTAL-26OCT032010SYNAWYHOM-8", "eventTicker": "KXMLBTOTAL-26OCT032010SYNAWYHOM", "side": "NO"},
]


def _router_row(key="kalshi:v1:" + "c" * 64, side="YES", legs=LEGS, **over):
    row = {
        "sourceBetKey": key, "gameDate": "2026-10-03", "marketTicker": COMBO, "side": side,
        "stake": 2.6, "entryPrice": 0.25, "contracts": 10.0,
        "executionEconomics": {
            "contractCost": 2.5, "averageFillPrice": 0.25, "totalFees": 0.1, "actualCashConsumed": 2.6,
            "executionStatus": "HELD_TO_SETTLEMENT", "feeStatus": "ACTUAL_API_FILL",
            "feeSource": "EXACT_ORDER_EXECUTION", "economicsSource": "EXACT_API_EXECUTION",
            "economicsConfidence": "HIGH",
        },
        "status": "pending", "source": "OTHER", "entryMethod": "IMPORTED_RECEIPT", "trackingType": "REAL",
        "wagerStructure": "COMBO_CONTRACT", "marketFamily": "multi_market_combo",
    }
    if legs is not None:
        row["comboLegs"] = legs
    row.update(over)
    return row


def _import(monkeypatch, rows):
    payload = {"importBatchId": "kalshi-router-v1", "rows": rows}
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    return import_script.main()


def _market(result, status="finalized", ticker=COMBO):
    return {"market": {"ticker": ticker, "status": status, "result": result}}


# ------------------------------------------------------------------------------------------------- import

def test_a_combo_contract_imports_as_one_canonical_wager_with_its_own_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row()]) == 0
    rows = list(storage.read_records(BETS_PATH))
    assert len(rows) == 1, "a combo is ONE wager, never one row per leg"
    bet = rows[0]
    assert bet["wagerStructure"] == "COMBO_CONTRACT"
    assert bet["marketTicker"] == COMBO and bet["side"] == "YES"
    assert bet["entryPrice"] == 0.25 and bet["stake"] == 2.6 and bet["contracts"] == 10.0
    assert bet["actualCashConsumed"] == 2.6 and bet["totalFees"] == 0.1
    assert bet["sourceBetKey"] == "kalshi:v1:" + "c" * 64 and bet["importBatchId"] == "kalshi-router-v1"
    assert bet["legs"] == [], "legs is the MULTI_LEG shape; a combo contract's legs are provenance only"
    assert [l["marketTicker"] for l in bet["comboLegs"]] == [l["marketTicker"] for l in LEGS]
    assert bet["status"] == "pending" and bet.get("result") is None
    validate_record("placed_bet", bet)


def test_a_duplicate_combo_import_is_a_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row()]) == 0
    before = open(BETS_PATH).read()
    capsys.readouterr()
    assert _import(monkeypatch, [_router_row()]) == 0
    assert "DUPLICATE_NOOP" in capsys.readouterr().out
    assert open(BETS_PATH).read() == before
    assert len(list(storage.read_records(BETS_PATH))) == 1


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_yes_and_no_combo_positions_keep_their_side_and_distinct_identity(tmp_path, monkeypatch, side):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row(key=f"k-{side}", side=side)]) == 0
    assert list(storage.read_records(BETS_PATH))[0]["side"] == side


def test_missing_leg_metadata_is_not_ambiguous(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row(legs=None)]) == 0
    bet = list(storage.read_records(BETS_PATH))[0]
    assert bet["wagerStructure"] == "COMBO_CONTRACT" and bet["comboLegs"] is None
    validate_record("placed_bet", bet)


def test_a_combo_row_without_its_own_ticker_is_refused_not_resolved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row(marketTicker=None)]) == 1
    assert not os.path.exists(BETS_PATH) or not list(storage.read_records(BETS_PATH))


def test_combo_legs_are_rejected_on_any_other_shape_and_legs_are_rejected_on_a_combo():
    with pytest.raises(ValueError, match="comboLegs are only valid"):
        build_manual_bet_record("KXMLBGAME-X", "x", 1.0, 0.5, None, import_batch_id="b", source_bet_key="k",
                                combo_legs=LEGS)
    with pytest.raises(ValueError, match="provenance only"):
        build_manual_bet_record(COMBO, "x", 1.0, 0.5, None, import_batch_id="b", source_bet_key="k",
                                wager_structure="COMBO_CONTRACT",
                                legs=[{"legKey": "a", "selection": "a"}, {"legKey": "b", "selection": "b"}])


def test_existing_straight_and_multi_leg_rows_keep_their_exact_shape():
    straight = build_manual_bet_record("KXMLBGAME-X", "x", 1.0, 0.5, None, import_batch_id="b", source_bet_key="k")
    assert straight["wagerStructure"] == "SINGLE" and "comboLegs" not in straight
    parlay = build_manual_bet_record(None, "p", 1.0, None, None, import_batch_id="b", source_bet_key="k2",
                                     legs=[{"legKey": "a", "selection": "a"}, {"legKey": "b", "selection": "b"}])
    assert parlay["wagerStructure"] == "MULTI_LEG" and "comboLegs" not in parlay


# --------------------------------------------------------------------------------------------- settlement

def _imported(tmp_path, monkeypatch, **over):
    monkeypatch.chdir(tmp_path)
    assert _import(monkeypatch, [_router_row(**over)]) == 0
    return list(storage.read_records(BETS_PATH))[0]


def test_combo_settles_WIN_from_its_own_contract_result(tmp_path, monkeypatch):
    bet = _imported(tmp_path, monkeypatch)
    to_write, record = settle_combo_contract([bet], COMBO, _market("yes"))
    assert record["settlementStatus"] == "SETTLED" and record["result"] == "YES"
    assert record["settlementSource"] == "kalshi_combo_contract_result" and record["gameId"] is None
    assert len(to_write) == 1 and to_write[0]["result"] == "WIN" and to_write[0]["status"] == "settled"
    # exact evidence: 10 contracts x $1 - $2.60 actually consumed
    assert to_write[0]["netProfitLoss"] == pytest.approx(7.4)


def test_combo_settles_LOSS_from_its_own_contract_result(tmp_path, monkeypatch):
    bet = _imported(tmp_path, monkeypatch)
    to_write, record = settle_combo_contract([bet], COMBO, _market("no"))
    assert record["result"] == "NO" and to_write[0]["result"] == "LOSS"
    assert to_write[0]["netProfitLoss"] == pytest.approx(-2.6)


def test_a_NO_side_combo_wins_when_the_contract_resolves_no(tmp_path, monkeypatch):
    bet = _imported(tmp_path, monkeypatch, side="NO")
    to_write, _ = settle_combo_contract([bet], COMBO, _market("no"))
    assert to_write[0]["result"] == "WIN"


@pytest.mark.parametrize("payload, reason", [
    (None, UNRESOLVED_FETCH_FAILED),
    ({}, UNRESOLVED_NOT_RETURNED),
    (_market("yes", ticker="KXMVESOMETHINGELSE-X"), UNRESOLVED_TICKER_MISMATCH),
    (_market("", status="active"), UNRESOLVED_NOT_FINAL),
    (_market("yes", status="determined"), UNRESOLVED_NOT_FINAL),
    (_market("void"), UNRESOLVED_NON_BINARY),
    (_market("scalar"), UNRESOLVED_NON_BINARY),
    (_market(""), UNRESOLVED_NON_BINARY),
])
def test_an_unproven_combo_outcome_leaves_the_wager_pending(tmp_path, monkeypatch, payload, reason):
    bet = _imported(tmp_path, monkeypatch)
    to_write, record = settle_combo_contract([bet], COMBO, payload)
    assert to_write == [], "nothing is written for an outcome the exchange has not proven"
    assert record["settlementStatus"] == "SETTLEMENT_UNRESOLVED" and record["result"] is None
    assert record["unavailableReason"].startswith(reason)
    status, result, _ = combo_contract_outcome(COMBO, payload)
    assert (status, result) == ("SETTLEMENT_UNRESOLVED", None)


def test_the_contract_result_wins_over_any_leg_reading(tmp_path, monkeypatch):
    """Every leg here 'won' on its own terms (the YES leg's market resolved yes, the NO leg's resolved no), so a
    leg-by-leg grader would call the combo a WIN. The combo contract itself resolved NO. The contract wins."""
    legs = [dict(l) for l in LEGS]
    bet = _imported(tmp_path, monkeypatch, legs=legs)
    to_write, record = settle_combo_contract([bet], COMBO, _market("no"))
    assert record["result"] == "NO" and to_write[0]["result"] == "LOSS"


def test_settlement_is_idempotent_and_never_duplicates_rows(tmp_path, monkeypatch):
    _imported(tmp_path, monkeypatch)
    calls = []

    def fetch(ticker):
        calls.append(ticker)
        return _market("yes")

    def run():
        bets = list(storage.read_records(BETS_PATH))
        existing = lambda date: {r["settlementId"]: r for r in storage.read_records(
            storage.resolve_partition_path("settlements", date))}
        updates, by_date, summary = settle_script.settle_pending_combos(bets, existing, fetch=fetch)
        for date, records in by_date.items():
            storage.upsert_records(storage.resolve_partition_path("settlements", date), records, "settlementId")
        if updates:
            storage.upsert_records(BETS_PATH, updates, "betId")
        return summary

    first = run()
    assert first["settled"] == 1 and first["betsWritten"] == 1
    bets_after = open(BETS_PATH).read()
    settlements_path = storage.resolve_partition_path("settlements", "2026-10-03")
    settlements_after = open(settlements_path).read()
    second = run()
    assert second["pendingCombos"] == 0 and second["betsWritten"] == 0 and calls == [COMBO]
    assert open(BETS_PATH).read() == bets_after
    assert open(settlements_path).read() == settlements_after
    assert len(list(storage.read_records(BETS_PATH))) == 1
    assert len(list(storage.read_records(settlements_path))) == 1


def test_a_pending_combo_is_re_asked_and_settles_once_the_contract_is_final(tmp_path, monkeypatch):
    _imported(tmp_path, monkeypatch)
    answers = iter([_market("", status="closed"), _market("yes")])
    existing = lambda date: {r["settlementId"]: r for r in storage.read_records(
        storage.resolve_partition_path("settlements", date))}
    for _ in range(2):
        bets = list(storage.read_records(BETS_PATH))
        updates, by_date, _summary = settle_script.settle_pending_combos(bets, existing, fetch=lambda t: next(answers))
        for date, records in by_date.items():
            storage.upsert_records(storage.resolve_partition_path("settlements", date), records, "settlementId")
        if updates:
            storage.upsert_records(BETS_PATH, updates, "betId")
    bet = list(storage.read_records(BETS_PATH))[0]
    assert bet["status"] == "settled" and bet["result"] == "WIN"
    records = list(storage.read_records(storage.resolve_partition_path("settlements", "2026-10-03")))
    assert len(records) == 1 and records[0]["settlementStatus"] == "SETTLED"


def test_straight_wagers_player_props_and_parlays_are_never_touched_by_combo_settlement():
    straight = {"betId": "s", "marketTicker": "KXMLBGAME-X", "side": "YES", "status": "pending",
                "wagerStructure": "SINGLE", "gameDate": "2026-10-03"}
    prop = {"betId": "p", "marketTicker": "KXMLBKS-X-10", "side": "YES", "status": "pending",
            "marketFamily": "pitcher_strikeouts", "gameDate": "2026-10-03"}
    parlay = {"betId": "m", "marketTicker": None, "side": None, "status": "pending", "wagerStructure": "MULTI_LEG",
              "gameDate": "2026-10-03"}
    fetched = []
    updates, by_date, summary = settle_script.settle_pending_combos(
        [straight, prop, parlay], lambda d: {}, fetch=lambda t: fetched.append(t) or _market("yes"))
    assert (updates, by_date, fetched, summary["pendingCombos"]) == ([], {}, [], 0)
    _, record = settle_combo_contract([straight], "KXMLBGAME-X", _market("yes"))
    assert record["betId"] is None, "a non-combo bet passed in by mistake is ignored, never graded"


def test_fetch_market_treats_404_as_an_answer_and_retries_transient_failures():
    import urllib.error

    def not_found(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, None)

    assert settle_script.fetch_market(COMBO, opener=not_found, sleep=lambda s: None) == {}
    attempts = []

    def flaky(request, timeout):
        attempts.append(1)
        raise urllib.error.URLError("down")

    assert settle_script.fetch_market(COMBO, opener=flaky, sleep=lambda s: None) is None
    assert len(attempts) == settle_script.ATTEMPTS
