#!/usr/bin/env python3
"""
tests/edgelab/test_settle_markets_script.py
================================================
Coverage for scripts/edgelab/settle_markets.py's linescore-to-game-outcome
parsing (the one piece of settlement wiring that isn't already covered
by tests/edgelab/test_settlement.py's pure settle_market() tests).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "settle_markets_script",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "settle_markets.py"),
)
settle_markets_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(settle_markets_script)


def _linescore(away_runs, home_runs, innings):
    return {
        "teams": {"away": {"runs": away_runs}, "home": {"runs": home_runs}},
        "innings": innings,
    }


def test_build_game_outcome_extracts_full_and_period_scores():
    innings = [
        {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 2, "away": {"runs": 0}, "home": {"runs": 2}},
        {"num": 3, "away": {"runs": 0}, "home": {"runs": 0}},
        {"num": 4, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 5, "away": {"runs": 0}, "home": {"runs": 1}},
        {"num": 6, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 7, "away": {"runs": 0}, "home": {"runs": 0}},
    ]
    linescore = _linescore(3, 3, innings)
    outcome = settle_markets_script.build_game_outcome_from_linescore(linescore, "Final")
    assert outcome["awayRuns"] == 3
    assert outcome["homeRuns"] == 3
    assert outcome["completedInnings"] == 7
    assert outcome["firstInningRuns"] == (1, 0)
    assert outcome["periodScores"]["F5"] == (2, 3)
    assert outcome["periodScores"]["F3"] == (1, 2)
    assert outcome["periodScores"]["F7"] == (3, 3)


def test_build_game_outcome_returns_none_for_missing_linescore():
    assert settle_markets_script.build_game_outcome_from_linescore(None, "Final") is None


def test_cancelled_bets_are_excluded_from_settlement(tmp_path, monkeypatch):
    """
    Maintainer review regression: a CANCELLED bet (logged in error) must
    never gain a result/netProfitLoss from a settlement run, and must
    never become a Settlement record's representative betId -- it isn't
    a real wager.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-03"
    ticker = "KXMLBGAME-TEST-DET"
    game_id = "2026-08-03_DET_ATH"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 999999, "awayTeam": "DET", "homeTeam": "ATH", "status": "Final",
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "game_result",
        "marketHorizon": "FULL_GAME", "team": "DET", "outcomeLabel": "Win",
    }])

    active_bet = {
        "betId": "active-bet", "marketTicker": ticker, "side": "YES", "stake": 10.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "ACTIVE",
    }
    cancelled_bet = {
        "betId": "cancelled-bet", "marketTicker": ticker, "side": "YES", "stake": 999.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "CANCELLED",
    }
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [active_bet, cancelled_bet])

    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    # gamePk 999999 is a fake test id, not a real game -- without this
    # mock, _fetch_authoritative_game_context would make a REAL network
    # call to the live MLB Stats API for it in any environment with real
    # network access (unlike this repo's own sandboxed test runs, which
    # silently get None back and mask the gap). Explicitly mocked to
    # None here so this test's outcome depends only on the fixture data
    # above, never on what a real gamePk 999999 happens to resolve to.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: None)
    monkeypatch.setattr(sys, "argv", ["settle_markets.py", "--date", date])
    exit_code = settle_markets_script.main()
    assert exit_code == 0

    rows = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}
    assert rows["active-bet"]["status"] == "settled"
    assert rows["active-bet"]["result"] == "WIN"  # DET (away) won 5-2
    assert rows["cancelled-bet"]["status"] == "pending"  # untouched
    assert rows["cancelled-bet"].get("result") is None

    settlements = list(storage.read_records(storage.partition_path("settlements", date)))
    assert settlements[0]["betId"] == "active-bet"  # never the cancelled bet


def _live_feed(status="Final", away_abbr="DET", home_abbr="ATH"):
    return {"gameData": {"status": {"detailedState": status}, "teams": {
        "away": {"abbreviation": away_abbr}, "home": {"abbreviation": home_abbr},
    }}}


def test_stale_archived_game_status_does_not_block_settlement_when_live_feed_reports_final(tmp_path, monkeypatch):
    """
    Root-cause regression (Aug 3-6 reconciliation): the archived
    Game.status field is captured once at initial ingest and never
    refreshed, so a game captured while "Pre-Game" stays stuck
    SETTLEMENT_UNRESOLVED forever even once it has actually finished --
    even though the live linescore fetch (which already runs every time)
    proves a final score exists. Settlement must use the CURRENT status
    from the live game feed, not the frozen archived snapshot.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-04"
    ticker = "KXMLBGAME-TEST-CWS"
    game_id = "824731"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 824731, "awayTeam": "CWS", "homeTeam": "BOS",
        "status": "Pre-Game",  # stale -- captured before the game started, never refreshed
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "game_result",
        "marketHorizon": "FULL_GAME", "team": "CWS", "outcomeLabel": "Win",
    }])
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [{
        "betId": "b1", "marketTicker": ticker, "side": "YES", "stake": 10.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "ACTIVE",
    }])

    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed(status="Final", away_abbr="CWS", home_abbr="BOS"))

    summary = settle_markets_script.settle_date(date)

    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", date))}
    assert settlements[ticker]["settlementStatus"] == "SETTLED"
    assert settlements[ticker]["result"] == "YES"  # CWS (away) won 5-2
    assert summary["counts"]["betsSettled"] == 1

    bets = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}
    assert bets["b1"]["status"] == "settled"
    assert bets["b1"]["result"] == "WIN"


def test_gamePk_identity_conflict_is_recorded_not_settled(tmp_path, monkeypatch):
    """
    A stored gamePk whose live feed describes a DIFFERENT matchup than
    the archived Game record is a genuine identity conflict, not
    something to settle against silently -- the fix must record the
    conflict (both matchups, visibly) and fall back to the archived
    status rather than guess or fuzzy-match.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-04"
    ticker = "KXMLBGAME-TEST-CWS"
    game_id = "824731"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 824731, "awayTeam": "CWS", "homeTeam": "BOS", "status": "Pre-Game",
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "game_result",
        "marketHorizon": "FULL_GAME", "team": "CWS", "outcomeLabel": "Win",
    }])

    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    # The live feed for gamePk 824731 actually describes a totally different game.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed(status="Final", away_abbr="NYY", home_abbr="TOR"))

    summary = settle_markets_script.settle_date(date)

    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", date))}
    assert settlements[ticker]["settlementStatus"] == "SETTLEMENT_UNRESOLVED"
    assert any("identity conflict" in w and "CWS@BOS" in w and "NYY@TOR" in w for w in summary["warnings"])


def test_authoritative_status_fetch_reused_for_player_props(tmp_path, monkeypatch):
    """
    The live game-feed fetch that derives the authoritative gameStatus
    must be the SAME fetch player-prop settlement already uses -- never
    a second network call for a game that has both a game-level and a
    player-prop market on it (issue #43's original "one boxscore fetch
    per game" bound, now shared across all families).
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-04"
    game_id = "824731"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 824731, "awayTeam": "CWS", "homeTeam": "BOS", "status": "Pre-Game",
    }])
    storage.write_all_records(storage.partition_path("markets", date), [
        {"marketTicker": "KXMLBGAME-TEST-CWS", "gameId": game_id, "marketFamily": "game_result",
         "marketHorizon": "FULL_GAME", "team": "CWS", "outcomeLabel": "Win"},
        {"marketTicker": "KXMLBKS-TEST-CWSPLAYER1-9", "gameId": game_id, "marketFamily": "pitcher_strikeouts",
         "eventTicker": "KXMLBKS-TEST", "title": "Fake Player: 9+ strikeouts?"},
    ])
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )

    call_count = {"n": 0}

    def _counting_fetch(game_pk, timeout=15):
        call_count["n"] += 1
        return _live_feed(status="Final", away_abbr="CWS", home_abbr="BOS")

    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", _counting_fetch)

    settle_markets_script.settle_date(date)

    assert call_count["n"] == 1
    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", date))}
    assert settlements["KXMLBGAME-TEST-CWS"]["settlementStatus"] == "SETTLED"


# ---------------------------------------------------------------------------
# Aug 11 2026 game-identity repair mission -- scenario 5 ("settlement
# following repaired identity") and scenario 8 ("player props remaining
# unresolved by the automatic settlement path until a real boxscore is
# available, independent of identity resolution").
# ---------------------------------------------------------------------------

def test_settlement_follows_repaired_identity_end_to_end(tmp_path, monkeypatch):
    """
    Scenario 5, full pipeline: a standalone/manual-only day's Game row
    starts mlbGamePk=null (no data/pipeline/<date>/normalized_slate.json
    ever existed for it -- the real 2026-08-11 shape). Repairing identity
    via the live MLB schedule (lib.edgelab.mlb_schedule, mocked) resolves
    it, and settle_markets.py -- unchanged -- immediately picks up the
    newly-populated mlbGamePk and settles a bet on it, exactly as it
    already does for a pipeline-slate-resolved game.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, mlb_schedule, storage
    from scripts.edgelab.repair_game_identity import repair_date

    date = "2026-08-11"
    ticker = "KXMLBF5-TEST-KCLAD-LAD"
    game_id = "2026-08-11_KC_LAD_2210"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": None, "awayTeam": "KC", "homeTeam": "LAD",
        "gameDate": date, "status": None,
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "inning_result",
        "marketHorizon": "F5", "team": "LAD", "comparisonOperator": "OVER",
    }])
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [{
        "betId": "b1", "marketTicker": ticker, "side": "YES", "stake": 24.59,
        "entryPrice": 0.53, "status": "pending", "recordStatus": "ACTIVE",
    }])

    # Nothing settleable yet -- identity unresolved.
    presettle = settle_markets_script.settle_date(date, dry_run=True)
    assert presettle["counts"]["betsSettled"] == 0

    # Step 1: repair identity via the live MLB schedule.
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda d, timeout=15: {
            "dates": [{"games": [{
                "gamePk": 745123,
                "teams": {"away": {"team": {"id": 118}}, "home": {"team": {"id": 119}}},  # KC, LAD
                "gameDate": "2026-08-11T02:10:00Z", "status": {"detailedState": "Final"},
                "venue": {"name": "Dodger Stadium"}, "gameNumber": 1,
            }]}],
        },
    )
    counts = repair_date(date)
    assert counts["gamesBackfilledMlbGamePkViaSchedule"] == 1

    fixed_game = list(storage.read_records(storage.partition_path("games", date)))[0]
    assert fixed_game["mlbGamePk"] == "745123"
    assert fixed_game["gameId"] == game_id  # never renamed -- the market/bet above still join on it

    # Step 2: settlement now proceeds normally, completely unchanged.
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {
            "teams": {"away": {"runs": 1}, "home": {"runs": 4}},
            "innings": [{"num": n, "away": {"runs": 0}, "home": {"runs": 1 if n <= 4 else 0}} for n in range(1, 10)],
        },
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed(status="Final", away_abbr="KC", home_abbr="LAD"))

    summary = settle_markets_script.settle_date(date)
    assert summary["counts"]["betsSettled"] == 1

    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", date))}
    assert settlements[ticker]["settlementStatus"] == "SETTLED"

    bets = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}
    assert bets["b1"]["status"] == "settled"
    assert bets["b1"]["result"] == "WIN"  # LAD (home) led 4-0 through 5 -- F5 YES on LAD


def test_player_prop_stays_unresolved_when_boxscore_unavailable_even_with_resolved_identity(tmp_path, monkeypatch):
    """
    Scenario 8: gamePk resolution (this mission's fix) and player-prop
    boxscore availability (GitHub issue #43's separate, already-closed
    concern -- lib.edgelab.player_prop_settlement) are independent gates.
    A resolved mlbGamePk alone must never be enough to fabricate a
    player-prop result -- if the live game feed's boxscore is still
    empty/unavailable, the market stays SETTLEMENT_UNRESOLVED with an
    explicit reason, exactly like every other never-guessed settlement
    path in this module.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-04"
    game_id = "824731"
    ticker = "KXMLBKS-TEST-CWSPLAYER1-9"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 824731, "awayTeam": "CWS", "homeTeam": "BOS", "status": "Pre-Game",
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "pitcher_strikeouts",
        "eventTicker": "KXMLBKS-TEST", "title": "Fake Player: 9+ strikeouts?",
    }])

    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", lambda game_pk: None)
    # Live feed resolves (gamePk/identity is fine) but carries NO boxscore data yet.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed(status="Final", away_abbr="CWS", home_abbr="BOS"))

    settle_markets_script.settle_date(date)

    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", date))}
    assert settlements[ticker]["settlementStatus"] == "SETTLEMENT_UNRESOLVED"
    assert settlements[ticker]["unavailableReason"]  # a specific reason, never blank


# ---------------------------------------------------------------------------
# Regression: the destructive terminal->unresolved rewrite, end to end.
#
# The real incident: a settle_markets run whose authoritative source was
# unreachable computed SETTLEMENT_UNRESOLVED for every market on every game
# and overwrote 8,264 stored SETTLED records, while reporting
# settled_or_void=0 bets_settled=0 -- indistinguishable from a clean no-op.
# See docs/EDGELAB_SETTLEMENT_REGRESSION_ON_FETCH_FAILURE.md.
#
# These tests reproduce that exact shape deterministically -- no network, no
# live MLB/Kalshi access -- by settling a fixture successfully and then
# re-running it blind.
# ---------------------------------------------------------------------------

def _seed_settled_slate(storage, date, market_count):
    """A game whose markets all settle cleanly from a successful fetch."""
    game_id = "2026-08-05_DET_ATH"
    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 777001, "awayTeam": "DET", "homeTeam": "ATH",
        "status": "Final",
    }])
    markets = [{
        "marketTicker": f"KXMLBTOTAL-TEST-{i}", "gameId": game_id,
        "marketFamily": "game_total", "marketHorizon": "FULL_GAME",
        # Offset so no threshold is 0.0: a total settles YES at >= threshold,
        # so a 0.0 line is YES for every conceivable score and could never
        # show a correction changing anything.
        "team": None, "outcomeLabel": "Over", "threshold": float(i) + 6.0,
    } for i in range(market_count)]
    storage.write_all_records(storage.partition_path("markets", date), markets)
    return game_id


def _fetch_succeeds(monkeypatch, mlb_boxscore):
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed())


def _fetch_fails(monkeypatch, mlb_boxscore):
    """Exactly what a 403/unreachable upstream looks like to this script."""
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", lambda game_pk: None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: None)


def test_blind_rerun_makes_zero_destructive_changes_to_many_settled_markets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-05"
    market_count = 40
    _seed_settled_slate(storage, date, market_count)
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [])

    _fetch_succeeds(monkeypatch, mlb_boxscore)
    first = settle_markets_script.settle_date(date)
    assert first["counts"]["settledOrVoid"] == market_count
    assert first["counts"]["terminalSettlementRegressionPrevented"] == 0

    settlements_path = storage.partition_path("settlements", date)
    before = {r["settlementId"]: r for r in storage.read_records(settlements_path)}
    assert len(before) == market_count
    assert all(r["settlementStatus"] == "SETTLED" for r in before.values())

    # ...now the authoritative source goes away.
    _fetch_fails(monkeypatch, mlb_boxscore)
    second = settle_markets_script.settle_date(date)

    after = {r["settlementId"]: r for r in storage.read_records(settlements_path)}
    assert after == before, "a blind rerun rewrote stored settlement facts"
    assert all(r["settlementStatus"] == "SETTLED" for r in after.values())
    # Nothing was downgraded, and nothing was quietly "corrected" either.
    assert second["counts"]["terminalSettlementRegressionPrevented"] == market_count
    assert second["counts"]["settlementsMeaningfullyChanged"] == 0


def test_blind_rerun_reports_prevented_regressions_rather_than_looking_like_a_no_op(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-05"
    _seed_settled_slate(storage, date, 12)
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [])

    _fetch_succeeds(monkeypatch, mlb_boxscore)
    settle_markets_script.settle_date(date)

    _fetch_fails(monkeypatch, mlb_boxscore)
    summary = settle_markets_script.settle_date(date)

    assert summary["counts"]["terminalSettlementRegressionPrevented"] == 12
    regression_warnings = [w for w in summary["warnings"] if "REGRESSION PREVENTED" in w]
    assert len(regression_warnings) == 1, "expected exactly one aggregate warning, not one per market"
    assert "12 market(s)" in regression_warnings[0]

    # The run record persists the count, so a blinded run is auditable after the fact.
    runs = list(storage.read_records(storage.partition_path("research_runs", date)))
    assert runs[-1]["counts"]["terminalSettlementRegressionPrevented"] == 12
    assert runs[-1]["status"] == "partial"   # warnings present -> never reported as clean


def test_blind_rerun_leaves_the_bet_ledger_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-05"
    game_id = _seed_settled_slate(storage, date, 3)
    ticker = "KXMLBTOTAL-TEST-0"
    bets_path = storage.singleton_path("bets", "bets.jsonl")

    settled_bet = {
        "betId": "settled-bet", "marketTicker": ticker, "side": "YES", "stake": 10.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "ACTIVE", "gameDate": date,
    }
    # A wager on a market this run can never authoritatively grade.
    ungradeable_bet = {
        "betId": "ungradeable-bet", "marketTicker": "KXMLBTOTAL-NOT-OBSERVED", "side": "YES",
        "stake": 25.0, "entryPrice": 0.4, "status": "pending", "recordStatus": "ACTIVE",
        "gameDate": date,
    }
    storage.write_all_records(bets_path, [settled_bet, ungradeable_bet])

    _fetch_succeeds(monkeypatch, mlb_boxscore)
    settle_markets_script.settle_date(date)
    before = {r["betId"]: r for r in storage.read_records(bets_path)}
    assert before["settled-bet"]["status"] == "settled"
    assert before["ungradeable-bet"]["status"] == "pending"

    _fetch_fails(monkeypatch, mlb_boxscore)
    summary = settle_markets_script.settle_date(date)

    after = {r["betId"]: r for r in storage.read_records(bets_path)}
    assert after == before, "a blind rerun modified the canonical bet ledger"
    assert summary["counts"]["betsSettled"] == 0
    assert after["ungradeable-bet"]["status"] == "pending"
    assert after["ungradeable-bet"].get("result") is None


def test_successful_correction_still_flows_through_after_a_blind_run(tmp_path, monkeypatch):
    """The guard must not strand a market in a stale terminal state forever."""
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-05"
    _seed_settled_slate(storage, date, 1)
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [])
    settlements_path = storage.partition_path("settlements", date)

    _fetch_succeeds(monkeypatch, mlb_boxscore)
    settle_markets_script.settle_date(date)
    assert list(storage.read_records(settlements_path))[0]["result"] == "YES"   # 5+2=7 > 0.0

    _fetch_fails(monkeypatch, mlb_boxscore)
    settle_markets_script.settle_date(date)

    # The source comes back, and genuinely reports a different final score.
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 0}, "home": {"runs": 0}}, "innings": []},
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _live_feed())
    summary = settle_markets_script.settle_date(date)

    assert summary["counts"]["terminalSettlementRegressionPrevented"] == 0
    corrected = list(storage.read_records(settlements_path))[0]
    assert corrected["result"] == "NO"          # 0+0=0, not over 0.0
    assert corrected["settlementStatus"] == "SETTLED"
