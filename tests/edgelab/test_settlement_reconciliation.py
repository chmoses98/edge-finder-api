#!/usr/bin/env python3
"""
tests/edgelab/test_settlement_reconciliation.py
===================================================
Destination-side settlement catch-up (Mission 1).

Covers the eight scenarios the mission named, end to end against a
sandboxed data tree (monkeypatch.chdir(tmp_path) -- never the real
canonical evidence; see tests/conftest.py):

  1. a bet imported AFTER the normal postgame workflow already ran
  2. a bet imported BEFORE the game ends
  3. the workflow rerun twice (idempotence)
  4. no affected wagers at all
  5. settlement evidence unavailable
  6. multiple affected dates
  7. an already-settled wager
  8. a partially supported slate containing an unsupported market family
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import settlement_reconciliation as recon  # noqa: E402
from lib.edgelab import storage  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "reconcile_settlement_catchup",
    os.path.join(ROOT, "scripts", "edgelab", "reconcile_settlement_catchup.py"),
)
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)


# ── fixtures / helpers ──────────────────────────────────────────────────

DATE = "2026-09-15"
OTHER_DATE = "2026-09-16"
GAME_ID = "824901"
TICKER = "KXMLBGAME-26SEP151840MILPIT-MIL"
UNSUPPORTED_TICKER = "KXMLBWEIRD-26SEP151840MILPIT-XYZ"


def _game(date=DATE, game_pk=824901, status="Final"):
    return {
        "gameId": GAME_ID, "mlbGamePk": game_pk, "awayTeam": "MIL", "homeTeam": "PIT",
        "status": status, "gameDate": date,
    }


def _market(ticker=TICKER, family="game_result", team="MIL"):
    return {
        "marketTicker": ticker, "gameId": GAME_ID, "marketFamily": family,
        "marketHorizon": "FULL_GAME", "team": team, "outcomeLabel": "Win",
        "threshold": None, "comparisonOperator": None, "title": f"Will {team} win?",
    }


def _bet(bet_id, *, date=DATE, ticker=TICKER, status="pending", result=None,
         record_status="ACTIVE"):
    row = {
        "betId": bet_id, "gameDate": date, "marketTicker": ticker, "side": "YES",
        "stake": 25.0, "entryPrice": 0.66, "contracts": 37.87, "status": status,
        "result": result, "recordStatus": record_status, "trackingType": "REAL",
        "gameId": GAME_ID, "platform": "KALSHI", "sport": "MLB",
    }
    return row


def _seed(date=DATE, *, markets=None, games=None, bets=(), game_status="Final"):
    storage.write_all_records(storage.partition_path("games", date),
                              games if games is not None else [_game(date, status=game_status)])
    storage.write_all_records(storage.partition_path("markets", date),
                              markets if markets is not None else [_market()])
    if bets:
        path = storage.singleton_path("bets", "bets.jsonl")
        existing = list(storage.read_records(path))
        storage.write_all_records(path, existing + list(bets))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def final_game_feed(monkeypatch):
    """MIL 5, PIT 2, Final -- an authoritative, resolvable result."""
    import scripts.edgelab.settle_markets as settle_markets_mod
    from lib.edgelab import mlb_boxscore

    innings = [{"num": i, "away": {"runs": 1 if i <= 5 else 0}, "home": {"runs": 1 if i <= 2 else 0}}
               for i in range(1, 10)]
    linescore = {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": innings}

    monkeypatch.setattr(settle_markets_mod, "fetch_mlb_linescore", lambda pk: linescore)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda pk, **kw: {"__fake__": True})
    monkeypatch.setattr(mlb_boxscore, "extract_teams", lambda feed: ("MIL", "PIT"))
    monkeypatch.setattr(mlb_boxscore, "extract_game_status", lambda feed: "Final")
    monkeypatch.setattr(mlb_boxscore, "extract_boxscore_teams", lambda feed: {})
    monkeypatch.setattr(mlb_boxscore, "payload_hash", lambda feed: "deadbeef")
    return linescore


@pytest.fixture
def unavailable_game_feed(monkeypatch):
    """Every authoritative source refuses -- nothing may be graded."""
    import scripts.edgelab.settle_markets as settle_markets_mod
    from lib.edgelab import mlb_boxscore

    def _boom(*a, **kw):
        raise OSError("network unreachable")

    monkeypatch.setattr(settle_markets_mod, "fetch_mlb_linescore", _boom)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda pk, **kw: None)


def _read_bets():
    return {b["betId"]: b for b in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}


# ── pure decision-layer tests ───────────────────────────────────────────

def test_new_wager_makes_its_date_affected():
    before = ""
    after = json.dumps({"betId": "a", "gameDate": DATE})
    analysis = recon.analyze_ledger_change(before, after)
    assert analysis["affectedDates"] == [DATE]
    assert analysis["counts"]["newWagers"] == 1


def test_settlement_echo_never_makes_a_date_affected():
    """The recursion brake: settlement writing its OWN result fields back
    onto a row must never re-trigger reconciliation for that date."""
    before = json.dumps({"betId": "a", "gameDate": DATE, "status": "pending", "result": None}, sort_keys=True)
    after = json.dumps({"betId": "a", "gameDate": DATE, "status": "settled", "result": "WIN",
                        "netProfitLoss": 12.9, "returnAmount": 12.9, "updatedAt": "2026-09-16T01:00:00Z"},
                       sort_keys=True)
    analysis = recon.analyze_ledger_change(before, after)
    assert analysis["affectedDates"] == []
    assert analysis["echoOnlyDates"] == [DATE]
    assert analysis["counts"]["settlementEchoes"] == 1


def test_material_change_to_an_existing_row_is_affected():
    before = json.dumps({"betId": "a", "gameDate": DATE, "marketTicker": "X", "status": "settled", "result": "WIN"})
    after = json.dumps({"betId": "a", "gameDate": DATE, "marketTicker": "Y", "status": "settled", "result": "WIN"})
    analysis = recon.analyze_ledger_change(before, after)
    assert analysis["affectedDates"] == [DATE]
    assert analysis["counts"]["materialChanges"] == 1


def test_unchanged_ledger_yields_no_affected_dates():
    text = json.dumps({"betId": "a", "gameDate": DATE, "status": "pending"}, sort_keys=True)
    analysis = recon.analyze_ledger_change(text, text)
    assert analysis["affectedDates"] == []
    assert analysis["counts"]["unchanged"] == 1


def test_a_removed_row_never_becomes_an_affected_date():
    before = json.dumps({"betId": "a", "gameDate": DATE})
    analysis = recon.analyze_ledger_change(before, "")
    assert analysis["affectedDates"] == []
    assert analysis["counts"]["removed"] == 1


def test_malformed_ledger_lines_are_skipped_not_fatal():
    after = "not json\n" + json.dumps({"betId": "a", "gameDate": DATE}) + "\n\n"
    analysis = recon.analyze_ledger_change("", after)
    assert analysis["affectedDates"] == [DATE]


def test_cancelled_and_settled_bets_are_not_settlement_candidates():
    assert recon.is_settlement_candidate(_bet("a")) is True
    assert recon.is_settlement_candidate(_bet("a", status="settled", result="WIN")) is False
    assert recon.is_settlement_candidate(_bet("a", record_status="CANCELLED")) is False


def test_pending_wager_dates_respects_the_lookback_window():
    rows = [_bet("a", date="2026-09-01"), _bet("b", date="2026-09-15")]
    assert recon.pending_wager_dates(rows, "2026-09-13", "2026-09-17") == ["2026-09-15"]


def test_select_dates_refuses_an_absurdly_wide_set_rather_than_truncating():
    dates = recon.date_range("2026-01-01", "2026-06-01")
    selected, refusal = recon.select_dates(explicit=dates, max_dates=45)
    assert selected == []
    assert "refusing to process" in refusal


def test_explicit_dates_suppress_the_diff_and_sweep_sources():
    selected, refusal = recon.select_dates(
        explicit=[DATE], ledger_change_dates=["2026-01-01"], sweep_dates=["2026-02-02"])
    assert (selected, refusal) == ([DATE], None)


def test_report_regeneration_only_when_canonical_state_actually_changed():
    assert recon.settlement_changed_canonical_state(
        {"counts": {"settlementsMeaningfullyChanged": 0, "betsSettled": 0, "settlementsUpdated": 40}}) is False
    assert recon.settlement_changed_canonical_state(
        {"counts": {"settlementsMeaningfullyChanged": 0, "betsSettled": 1}}) is True
    assert recon.settlement_changed_canonical_state(None) is False


# ── scenario 1: bet imported AFTER postgame already ran ─────────────────

def test_scenario_late_import_after_postgame_is_settled_by_reconciliation(sandbox, final_game_feed):
    # The nightly pass already ran for this date, with no bets at all.
    _seed()
    first = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert first["counts"]["betsSettled"] == 0

    # The router imports a wager hours later.
    _seed(bets=[_bet("late-bet")])
    assert recon.pending_wager_dates(list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))) == [DATE]

    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert result["counts"]["betsSettled"] == 1
    assert result["pendingWagersBefore"] == 1
    assert result["pendingWagersAfter"] == 0
    settled = _read_bets()["late-bet"]
    assert settled["status"] == "settled"
    assert settled["result"] == "WIN"   # MIL won 5-2, YES on MIL
    assert settled["netProfitLoss"] is not None


# ── scenario 2: bet imported BEFORE the game ends ───────────────────────

def test_scenario_import_before_game_ends_stays_pending_then_settles_later(sandbox, monkeypatch):
    import scripts.edgelab.settle_markets as settle_markets_mod
    from lib.edgelab import mlb_boxscore

    _seed(game_status="In Progress", bets=[_bet("early-bet")])

    # While the game is live nothing may be graded, no matter how
    # available the (partial) linescore is.
    live = {"teams": {"away": {"runs": 2}, "home": {"runs": 1}},
            "innings": [{"num": i, "away": {"runs": 1}, "home": {"runs": 0}} for i in range(1, 4)]}
    monkeypatch.setattr(settle_markets_mod, "fetch_mlb_linescore", lambda pk: live)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda pk, **kw: {"__fake__": True})
    monkeypatch.setattr(mlb_boxscore, "extract_teams", lambda feed: ("MIL", "PIT"))
    monkeypatch.setattr(mlb_boxscore, "extract_game_status", lambda feed: "In Progress")
    monkeypatch.setattr(mlb_boxscore, "extract_boxscore_teams", lambda feed: {})
    monkeypatch.setattr(mlb_boxscore, "payload_hash", lambda feed: "d1")

    during = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert during["counts"]["betsSettled"] == 0
    assert during["pendingWagersAfter"] == 1
    assert _read_bets()["early-bet"]["result"] is None

    # Later sweep: the game is final now.
    final = {"teams": {"away": {"runs": 5}, "home": {"runs": 2}},
             "innings": [{"num": i, "away": {"runs": 1 if i <= 5 else 0}, "home": {"runs": 1 if i <= 2 else 0}}
                         for i in range(1, 10)]}
    monkeypatch.setattr(settle_markets_mod, "fetch_mlb_linescore", lambda pk: final)
    monkeypatch.setattr(mlb_boxscore, "extract_game_status", lambda feed: "Final")

    after = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert after["counts"]["betsSettled"] == 1
    assert _read_bets()["early-bet"]["result"] == "WIN"


# ── scenario 3: rerun twice (idempotence) ───────────────────────────────

def test_scenario_rerunning_reconciliation_twice_changes_nothing_the_second_time(sandbox, final_game_feed):
    _seed(bets=[_bet("bet-1")])
    first = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert first["changedCanonicalState"] is True

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    settlements_path = storage.resolve_partition_path("settlements", DATE)
    before_bets = open(bets_path, "rb").read()
    before_settlements = open(settlements_path, "rb").read()

    second = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert second["changedCanonicalState"] is False
    assert second["counts"]["betsSettled"] == 0
    assert second["reportRegenerated"] is False
    assert open(bets_path, "rb").read() == before_bets
    assert open(settlements_path, "rb").read() == before_settlements


# ── scenario 4: nothing affected ────────────────────────────────────────

def test_scenario_no_affected_wagers_is_a_clean_no_op(sandbox, final_game_feed):
    _seed()  # markets and a final game, but zero wagers
    rows = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    assert recon.pending_wager_dates(rows, *recon.lookback_window(5, DATE)) == []

    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert result["counts"]["betsSettled"] == 0
    assert result["pendingWagersAfter"] == 0
    assert result["reportRegenerated"] is False


# ── scenario 5: settlement evidence unavailable ─────────────────────────

def test_scenario_unavailable_evidence_leaves_the_wager_explicitly_unresolved(sandbox, unavailable_game_feed):
    _seed(bets=[_bet("bet-no-evidence")])
    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)

    assert result["counts"]["betsSettled"] == 0
    assert result["pendingWagersAfter"] == 1
    bet = _read_bets()["bet-no-evidence"]
    assert bet["result"] is None, "a wager with no authoritative evidence must never be graded"
    assert bet["status"] != "settled"

    settlements = list(storage.read_partition("settlements", DATE))
    assert settlements, "an unresolved market must still be RECORDED as unresolved, with a reason"
    assert all(s["settlementStatus"] == "SETTLEMENT_UNRESOLVED" for s in settlements)
    assert all(s.get("unavailableReason") for s in settlements)


def test_scenario_unresolved_game_pk_is_never_guessed(sandbox, final_game_feed):
    """No mlbGamePk means no settlement -- never a fuzzy match onto some
    other game that happens to share the matchup."""
    _seed(games=[{"gameId": GAME_ID, "mlbGamePk": None, "awayTeam": "MIL", "homeTeam": "PIT", "status": "Final"}],
          bets=[_bet("bet-no-pk")])
    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert result["counts"]["betsSettled"] == 0
    assert _read_bets()["bet-no-pk"]["result"] is None
    assert any("no MLB gamePk resolved" in w for w in result["settlementWarnings"])


# ── scenario 6: multiple affected dates ─────────────────────────────────

def test_scenario_multiple_affected_dates_are_all_reconciled(sandbox, final_game_feed):
    # Each date gets its OWN ticker: a ticker is globally unique in
    # reality, and reusing one across dates would have both dates' bets
    # settle on whichever date was processed first.
    tickers = {DATE: TICKER, OTHER_DATE: "KXMLBGAME-26SEP161840MILPIT-MIL"}
    for date in (DATE, OTHER_DATE):
        _seed(date, markets=[_market(tickers[date])],
              bets=[_bet(f"bet-{date}", date=date, ticker=tickers[date])])

    before = ""
    after = "\n".join(json.dumps(b, sort_keys=True) for b in
                      [_bet(f"bet-{DATE}", date=DATE, ticker=tickers[DATE]),
                       _bet(f"bet-{OTHER_DATE}", date=OTHER_DATE, ticker=tickers[OTHER_DATE])])
    analysis = recon.analyze_ledger_change(before, after)
    assert analysis["affectedDates"] == [DATE, OTHER_DATE]

    dates, refusal = recon.select_dates(ledger_change_dates=analysis["affectedDates"])
    assert (dates, refusal) == ([DATE, OTHER_DATE], None)

    results = [reconcile.reconcile_date(d, skip_ingest=True, skip_report=True) for d in dates]
    assert [r["counts"]["betsSettled"] for r in results] == [1, 1]
    graded = _read_bets()
    assert graded[f"bet-{DATE}"]["result"] == "WIN"
    assert graded[f"bet-{OTHER_DATE}"]["result"] == "WIN"


# ── scenario 7: already-settled wager ───────────────────────────────────

def test_scenario_already_settled_wager_is_never_rewritten(sandbox, final_game_feed):
    _seed(bets=[_bet("bet-1")])
    reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    settled_at = _read_bets()["bet-1"]["updatedAt"]

    again = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    assert again["counts"]["betsSettled"] == 0
    assert _read_bets()["bet-1"]["updatedAt"] == settled_at, "an unchanged settled bet must keep its original updatedAt"


# ── scenario 8: partially supported slate ───────────────────────────────

def test_scenario_unsupported_market_family_stays_unresolved_while_the_rest_settles(sandbox, final_game_feed):
    """
    The invariant: reconciliation must never invent settlement support
    for a family the canonical settler does not handle. The supported
    wager on the same date settles; the unsupported one stays explicitly
    unresolved, with a reason.
    """
    _seed(markets=[_market(), _market(UNSUPPORTED_TICKER, family="totally_unsupported_family", team=None)],
          bets=[_bet("supported-bet"), _bet("unsupported-bet", ticker=UNSUPPORTED_TICKER)])

    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)

    assert result["counts"]["betsSettled"] == 1
    graded = _read_bets()
    assert graded["supported-bet"]["result"] == "WIN"
    assert graded["unsupported-bet"]["result"] is None
    assert graded["unsupported-bet"]["status"] != "settled"
    assert result["pendingWagersAfter"] == 1

    by_ticker = {s["marketTicker"]: s for s in storage.read_partition("settlements", DATE)}
    assert by_ticker[TICKER]["settlementStatus"] == "SETTLED"
    assert by_ticker[UNSUPPORTED_TICKER]["settlementStatus"] == "SETTLEMENT_UNRESOLVED"
    assert by_ticker[UNSUPPORTED_TICKER]["unavailableReason"]


# ── receipt ─────────────────────────────────────────────────────────────

def test_receipt_records_what_is_still_pending(sandbox, unavailable_game_feed):
    _seed(bets=[_bet("bet-pending")])
    result = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    receipt = recon.build_receipt(
        dates=[DATE], per_date=[result], trigger="SCHEDULED_SWEEP", dry_run=False,
        started_at="2026-09-16T01:00:00Z", completed_at="2026-09-16T01:00:05Z")
    assert receipt["counts"]["wagersStillPending"] == 1
    assert receipt["counts"]["betsSettled"] == 0
    # The date DID change canonical state -- a first-ever
    # SETTLEMENT_UNRESOLVED record with an explicit reason is new,
    # honest evidence and is worth persisting. What must never happen is
    # the wager gaining a RESULT, which the assertion above covers.
    assert receipt["counts"]["datesChanged"] == 1
    assert receipt["receiptType"] == "SETTLEMENT_RECONCILIATION"


def test_dry_run_writes_nothing(sandbox, final_game_feed):
    _seed(bets=[_bet("bet-dry")])
    result = reconcile.reconcile_date(DATE, dry_run=True, skip_ingest=True, skip_report=True)
    assert result["counts"]["betsSettled"] >= 0
    assert _read_bets()["bet-dry"]["result"] is None
    assert not os.path.exists(storage.partition_path("settlements", DATE))


def test_a_dry_run_never_overwrites_the_rolling_status_file(sandbox, final_game_feed):
    """
    settlement_reconciliation_status.json is what a human (or a fresh
    chat asking "is yesterday finished?") reads. A dry run computes what
    WOULD happen; it must never rewrite the record of what actually did.
    """
    _seed(bets=[_bet("bet-1")])
    live = reconcile.reconcile_date(DATE, skip_ingest=True, skip_report=True)
    reconcile.write_receipt(
        recon.build_receipt(dates=[DATE], per_date=[live], trigger="SCHEDULED_SWEEP", dry_run=False,
                            started_at="2026-09-16T01:00:00Z", completed_at="2026-09-16T01:00:05Z"),
        reconcile.RECEIPT_PATH)
    real_status = open(reconcile.STATUS_PATH, "rb").read()

    dry = reconcile.reconcile_date(DATE, dry_run=True, skip_ingest=True, skip_report=True)
    reconcile.write_receipt(
        recon.build_receipt(dates=[DATE], per_date=[dry], trigger="MANUAL_DISPATCH", dry_run=True,
                            started_at="2026-09-16T02:00:00Z", completed_at="2026-09-16T02:00:05Z"),
        reconcile.RECEIPT_PATH)

    assert open(reconcile.STATUS_PATH, "rb").read() == real_status
    with open(reconcile.RECEIPT_PATH) as f:
        assert json.load(f)["dryRun"] is True, "the dry run's own receipt IS still written"
