#!/usr/bin/env python3
"""
tests/edgelab/test_settle_markets_player_props_integration.py
===================================================================
Integration coverage for scripts/edgelab/settle_markets.py's player-prop
wiring (GitHub issue #43): one boxscore fetch per game, game-level
markets unaffected, all bet tranches settle, cancelled bets untouched,
one game's failure never blocks another, settlement evidence shape,
idempotent rerun, corrected-stat rerun, and an end-to-end fixture
modeled on the real 2026-08-02 archived player-prop universe.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_spec = importlib.util.spec_from_file_location(
    "settle_markets_script",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "settle_markets.py"),
)
settle_markets_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(settle_markets_script)

from lib.edgelab import mlb_boxscore, storage

DATE = "2026-08-02"


def _sheehan_feed(status="Final", strikeouts=9):
    return {
        "gameData": {"status": {"detailedState": status}},
        "liveData": {"boxscore": {"teams": {
            "away": {"players": {}},
            "home": {"players": {
                "ID660271": {
                    "person": {"id": 660271, "fullName": "Emmet Sheehan"},
                    "jerseyNumber": "80",
                    "stats": {"pitching": {"strikeOuts": strikeouts, "inningsPitched": "6.0"}},
                },
            }},
        }}},
    }


def _write_prop_market(ticker, event_ticker, title, family, game_id):
    return {
        "marketTicker": ticker, "eventTicker": event_ticker, "title": title,
        "gameId": game_id, "marketFamily": family,
    }


def test_one_boxscore_fetch_per_game_despite_many_prop_markets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_id = "12345"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "KXMLBKS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 9+ strikeouts?", "pitcher_strikeouts", game_id),
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-8", "KXMLBKS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 8+ strikeouts?", "pitcher_strikeouts", game_id),
        _write_prop_market("KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17", "KXMLBOUTS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 17+ Outs Recorded?", "pitcher_outs", game_id),
    ])

    call_count = {"n": 0}

    def _counting_fetch(game_pk, timeout=15):
        call_count["n"] += 1
        return _sheehan_feed()

    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", _counting_fetch)
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)

    summary = settle_markets_script.settle_date(DATE)
    assert call_count["n"] == 1
    assert summary["byFamily"]["pitcher_strikeouts"]["settled"] == 2
    assert summary["byFamily"]["pitcher_outs"]["settled"] == 1


def test_game_level_markets_continue_settling_unchanged_alongside_props(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_id = "2026-08-02_BOS_LAD"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Final"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        {"marketTicker": "KXMLBGAME-TEST-BOS", "gameId": game_id, "marketFamily": "game_result",
         "marketHorizon": "FULL_GAME", "team": "BOS", "outcomeLabel": "Win"},
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "KXMLBKS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 9+ strikeouts?", "pitcher_strikeouts", game_id),
    ])
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed())

    summary = settle_markets_script.settle_date(DATE)
    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", DATE))}
    assert settlements["KXMLBGAME-TEST-BOS"]["result"] == "YES"  # BOS (away) won 5-2, unaffected by prop wiring
    assert settlements["KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"]["result"] == "YES"


def test_all_matching_bet_tranches_settle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_id = "12345"
    ticker = "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market(ticker, "KXMLBKS-26AUG021920BOSLAD", "Emmet Sheehan: 9+ strikeouts?",
                            "pitcher_strikeouts", game_id),
    ])
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [
        {"betId": "tranche-1", "marketTicker": ticker, "side": "YES", "stake": 10.0, "entryPrice": 0.5,
         "status": "pending", "recordStatus": "ACTIVE"},
        {"betId": "tranche-2", "marketTicker": ticker, "side": "NO", "stake": 5.0, "entryPrice": 0.4,
         "status": "pending", "recordStatus": "ACTIVE"},
        {"betId": "cancelled", "marketTicker": ticker, "side": "YES", "stake": 999.0, "entryPrice": 0.5,
         "status": "pending", "recordStatus": "CANCELLED"},
    ])
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed(strikeouts=9))

    settle_markets_script.settle_date(DATE)

    bets = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}
    assert bets["tranche-1"]["result"] == "WIN"   # YES bet, result YES
    assert bets["tranche-2"]["result"] == "LOSS"  # NO bet, result YES -> loss
    assert bets["cancelled"]["status"] == "pending"  # cancelled bets remain untouched
    assert bets["cancelled"].get("result") is None


def test_one_failed_game_does_not_block_another(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    good_game, bad_game = "good-game", "bad-game"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": good_game, "mlbGamePk": 111, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
        {"gameId": bad_game, "mlbGamePk": 222, "awayTeam": "NYY", "homeTeam": "TOR", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "KXMLBKS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 9+ strikeouts?", "pitcher_strikeouts", good_game),
        _write_prop_market("KXMLBKS-26AUG021920NYYTOR-TORFAKEPLAYR1-9", "KXMLBKS-26AUG021920NYYTOR",
                            "Fake Player: 9+ strikeouts?", "pitcher_strikeouts", bad_game),
    ])
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)

    def _flaky_fetch(game_pk, timeout=15):
        if game_pk == 222:
            raise RuntimeError("simulated network failure")
        return _sheehan_feed()

    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", _flaky_fetch)

    summary = settle_markets_script.settle_date(DATE)
    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", DATE))}
    assert settlements["KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"]["settlementStatus"] == "SETTLED"
    assert settlements["KXMLBKS-26AUG021920NYYTOR-TORFAKEPLAYR1-9"]["settlementStatus"] == "SETTLEMENT_UNRESOLVED"
    assert any("boxscore fetch failed" in w for w in summary["warnings"])


def test_settlement_evidence_shape_matches_schema_properties(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_id = "12345"
    ticker = "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market(ticker, "KXMLBKS-26AUG021920BOSLAD", "Emmet Sheehan: 9+ strikeouts?",
                            "pitcher_strikeouts", game_id),
    ])
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed())

    settle_markets_script.settle_date(DATE)
    record = list(storage.read_records(storage.partition_path("settlements", DATE)))[0]

    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "edgelab", "schema_v1", "settlement.schema.json")
    with open(schema_path) as f:
        schema = json.load(f)
    allowed_top_level = set(schema["properties"].keys())
    assert set(record.keys()) <= allowed_top_level

    evidence_props = set(schema["properties"]["settlementEvidence"]["properties"].keys())
    assert set(record["settlementEvidence"].keys()) <= evidence_props


def test_exact_rerun_is_byte_for_byte_idempotent(tmp_path, monkeypatch):
    """
    GitHub issue #43 correction round: an identical rerun against
    equivalent authoritative final facts must leave the canonical
    settlements file byte-for-byte unchanged -- not just "same result",
    but the EXACT same createdAt/updatedAt/settledAt, even though the
    boxscore feed is refetched (with a fresh fetchedAt/sourcePayloadHash)
    on every run.
    """
    monkeypatch.chdir(tmp_path)
    game_id = "12345"
    ticker = "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market(ticker, "KXMLBKS-26AUG021920BOSLAD", "Emmet Sheehan: 9+ strikeouts?",
                            "pitcher_strikeouts", game_id),
    ])
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [
        {"betId": "b1", "marketTicker": ticker, "side": "YES", "stake": 10.0, "entryPrice": 0.5,
         "status": "pending", "recordStatus": "ACTIVE"},
    ])
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)

    call_count = {"n": 0}

    def _fresh_feed_each_call(game_pk, timeout=15):
        call_count["n"] += 1
        return _sheehan_feed(strikeouts=9)  # same authoritative fact every time, "fresh" object each call

    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", _fresh_feed_each_call)

    summary1 = settle_markets_script.settle_date(DATE)
    first_settlements = list(storage.read_records(storage.partition_path("settlements", DATE)))
    first_bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    assert len(first_settlements) == 1
    assert summary1["counts"]["settlementsMeaningfullyChanged"] == 1  # first-ever settlement
    assert summary1["counts"]["betsSettled"] == 1  # first-ever bet settlement

    summary2 = settle_markets_script.settle_date(DATE)
    second_settlements = list(storage.read_records(storage.partition_path("settlements", DATE)))
    second_bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))

    assert call_count["n"] == 2  # the feed WAS refetched -- proves this isn't a no-op from skipping the fetch
    assert len(second_settlements) == 1
    assert second_settlements[0] == first_settlements[0]  # byte-for-byte identical, including createdAt/updatedAt/settledAt
    assert second_bets == first_bets  # bet ledger untouched too
    assert summary2["counts"]["settlementsMeaningfullyChanged"] == 0  # true no-op reported
    assert summary2["counts"]["betsSettled"] == 0


def test_corrected_final_stat_updates_existing_record_and_bet_safely(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    game_id = "12345"
    ticker = "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market(ticker, "KXMLBKS-26AUG021920BOSLAD", "Emmet Sheehan: 9+ strikeouts?",
                            "pitcher_strikeouts", game_id),
    ])
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [
        {"betId": "b1", "marketTicker": ticker, "side": "YES", "stake": 10.0, "entryPrice": 0.5,
         "status": "pending", "recordStatus": "ACTIVE"},
    ])
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)

    # Initial (incorrect/preliminary) final stat: 8 strikeouts -> NO -> bet loses.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed(strikeouts=8))
    settle_markets_script.settle_date(DATE)
    settlement_v1 = list(storage.read_records(storage.partition_path("settlements", DATE)))[0]
    bet_before = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}["b1"]
    assert bet_before["result"] == "LOSS"
    original_created_at = settlement_v1["createdAt"]

    # MLB corrects the official strikeout total to 9 -> YES -> bet wins on rerun.
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed(strikeouts=9))
    summary = settle_markets_script.settle_date(DATE)

    settlements = list(storage.read_records(storage.partition_path("settlements", DATE)))
    assert len(settlements) == 1  # no duplicate settlement record
    assert settlements[0]["result"] == "YES"
    assert settlements[0]["settlementEvidence"]["actualValue"] == 9
    assert settlements[0]["createdAt"] == original_created_at  # createdAt never changes
    assert settlements[0] != settlement_v1  # the record genuinely changed (result flipped) -- not a no-op
    assert settlements[0]["settledAt"] is not None  # settledAt is freshly (re)computed for this genuine correction
    assert summary["counts"]["settlementsMeaningfullyChanged"] == 1

    bets_after = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    assert len(bets_after) == 1  # no duplicate bet record
    assert bets_after[0]["result"] == "WIN"
    assert bets_after[0]["netProfitLoss"] > 0
    assert summary["counts"]["betsSettled"] == 1

    # A third rerun against the now-stable corrected fact is a true no-op again.
    settlement_v2 = settlements[0]
    summary3 = settle_markets_script.settle_date(DATE)
    settlement_v3 = list(storage.read_records(storage.partition_path("settlements", DATE)))[0]
    assert settlement_v3 == settlement_v2
    assert summary3["counts"]["settlementsMeaningfullyChanged"] == 0
    assert summary3["counts"]["betsSettled"] == 0


def test_player_prop_backfill_does_not_churn_unrelated_game_level_settlement(tmp_path, monkeypatch):
    """
    GitHub issue #43 correction round: rerunning settlement for a date
    that mixes game-level and player-prop markets (as any real backfill
    would) must leave an unrelated, unchanged game-level settlement
    completely untouched -- not just "same result", byte-for-byte.
    """
    monkeypatch.chdir(tmp_path)
    game_id = "2026-08-02_BOS_LAD"
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Final"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        {"marketTicker": "KXMLBGAME-TEST-BOS", "gameId": game_id, "marketFamily": "game_result",
         "marketHorizon": "FULL_GAME", "team": "BOS", "outcomeLabel": "Win"},
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "KXMLBKS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 9+ strikeouts?", "pitcher_strikeouts", game_id),
    ])
    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: _sheehan_feed(strikeouts=9))

    settle_markets_script.settle_date(DATE)
    first = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", DATE))}

    # Simulates a player-prop-focused backfill rerun of the whole date --
    # the game-level record must be untouched even though the run also
    # reprocesses player-prop markets on the same date.
    summary2 = settle_markets_script.settle_date(DATE)
    second = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", DATE))}

    assert second["KXMLBGAME-TEST-BOS"] == first["KXMLBGAME-TEST-BOS"]
    assert second["KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"] == first["KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"]
    assert summary2["counts"]["settlementsMeaningfullyChanged"] == 0


def test_end_to_end_fixture_modeled_on_real_august_2_archived_universe(tmp_path, monkeypatch):
    """
    Modeled directly on real tickers/titles from
    data/kalshi_registry_snapshots/kalshi_search_2026-08-02.json (one
    per family), settled against a single synthetic-but-realistic final
    boxscore for that same BOS@LAD game.
    """
    monkeypatch.chdir(tmp_path)
    game_id = "2026-08-02_BOS_LAD"
    event_ticker = "KXMLBKS-26AUG021920BOSLAD"  # shared date/time/teams encoding across all these families' events
    storage.write_all_records(storage.partition_path("games", DATE), [
        {"gameId": game_id, "mlbGamePk": 824404, "awayTeam": "BOS", "homeTeam": "LAD", "status": "Preview"},
    ])
    storage.write_all_records(storage.partition_path("markets", DATE), [
        _write_prop_market("KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", event_ticker,
                            "Emmet Sheehan: 9+ strikeouts?", "pitcher_strikeouts", game_id),
        _write_prop_market("KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17", "KXMLBOUTS-26AUG021920BOSLAD",
                            "Emmet Sheehan: 17+ Outs Recorded?", "pitcher_outs", game_id),
        _write_prop_market("KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBHIT-26AUG021920BOSLAD",
                            "Shohei Ohtani: 2+ hits?", "hitter_hits", game_id),
        _write_prop_market("KXMLBTB-26AUG021920BOSLAD-LADSOHTANI17-5", "KXMLBTB-26AUG021920BOSLAD",
                            "Shohei Ohtani: 5+ total bases?", "hitter_total_bases", game_id),
        _write_prop_market("KXMLBHRR-26AUG021920BOSLAD-LADSOHTANI17-3", "KXMLBHRR-26AUG021920BOSLAD",
                            "Shohei Ohtani: 3+ hits + runs + RBIs?", "hitter_hits_runs_rbis", game_id),
        _write_prop_market("KXMLBRBI-26AUG021920BOSLAD-LADSOHTANI17-2", "KXMLBRBI-26AUG021920BOSLAD",
                            "Shohei Ohtani: 2+ RBIs?", "hitter_rbis", game_id),
        _write_prop_market("KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1", "KXMLBSB-26AUG021920BOSLAD",
                            "Shohei Ohtani: 1+ stolen bases?", "hitter_stolen_bases", game_id),
    ])

    feed = {
        "gameData": {"status": {"detailedState": "Final"}},
        "liveData": {"boxscore": {"teams": {
            "away": {"players": {}},
            "home": {"players": {
                "ID660271": {
                    "person": {"id": 660271, "fullName": "Emmet Sheehan"},
                    "jerseyNumber": "80",
                    "stats": {"pitching": {"strikeOuts": 9, "inningsPitched": "5.2"}},
                },
                "ID660272": {
                    "person": {"id": 660272, "fullName": "Shohei Ohtani"},
                    "jerseyNumber": "17",
                    "stats": {"batting": {"gamesPlayed": 1, "hits": 2, "runs": 1, "rbi": 0, "doubles": 1, "triples": 0, "homeRuns": 0}},
                },
            }},
        }}},
    }
    monkeypatch.setattr(settle_markets_script, "fetch_mlb_linescore", None)
    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", lambda game_pk, timeout=15: feed)

    summary = settle_markets_script.settle_date(DATE)
    settlements = {r["marketTicker"]: r for r in storage.read_records(storage.partition_path("settlements", DATE))}

    assert settlements["KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"]["result"] == "YES"       # 9 strikeouts >= 9
    assert settlements["KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17"]["result"] == "YES"    # 5.2 IP -> 17 outs >= 17
    assert settlements["KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2"]["result"] == "YES"       # 2 hits >= 2
    assert settlements["KXMLBTB-26AUG021920BOSLAD-LADSOHTANI17-5"]["result"] == "NO"         # TB = 1 single + 2*1 double = 3 < 5
    assert settlements["KXMLBHRR-26AUG021920BOSLAD-LADSOHTANI17-3"]["result"] == "YES"       # hits+runs+rbi = 2+1+0 = 3 >= 3
    assert settlements["KXMLBRBI-26AUG021920BOSLAD-LADSOHTANI17-2"]["result"] == "NO"        # 0 RBIs < 2
    assert settlements["KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1"]["result"] is None         # no stolenBases field -> unresolved, never guessed

    assert summary["byFamily"]["pitcher_strikeouts"]["settled"] == 1
    assert summary["byFamily"]["hitter_stolen_bases"]["unresolved"] == 1  # missing_stolenBases -- honest, not guessed
