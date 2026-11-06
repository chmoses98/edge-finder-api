"""
#309: the player-prop settler must say WHERE a game's status came from, and must never report "game_not_final"
for a game whose status is merely unknown (no gamePk, feed unavailable/failed, identity conflict, feed without a
status). Deterministic: no network, no corpus (tmp_path storage, monkeypatched feed).
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.player_prop_settlement import SETTLEMENT_UNRESOLVED, settle_player_prop_market

_spec = importlib.util.spec_from_file_location(
    "settle_markets_status_source",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "settle_markets.py"),
)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)


def _feed(status, away="CWS", home="BOS"):
    return {"gameData": {"status": {"detailedState": status}, "teams": {"away": {"abbreviation": away}, "home": {"abbreviation": home}}},
            "liveData": {"boxscore": {"teams": {"away": {"players": {}}, "home": {"players": {}}}}}}


GAME = {"gameId": "g1", "mlbGamePk": 111, "awayTeam": "CWS", "homeTeam": "BOS", "status": "Pre-Game"}


# --- context source labels ---------------------------------------------------------------------------------

def test_no_gamepk_is_labeled_and_keeps_archived_status(monkeypatch):
    monkeypatch.setattr(sm.mlb_boxscore, "fetch_game_feed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    ctx = sm._fetch_authoritative_game_context({**GAME, "mlbGamePk": None, "status": None}, [])
    assert ctx["statusSource"] == sm.STATUS_SOURCE_ARCHIVED_NO_GAMEPK and ctx["gameStatus"] is None


def test_missing_game_record_is_no_gamepk():
    ctx = sm._fetch_authoritative_game_context(None, [])
    assert ctx["statusSource"] == sm.STATUS_SOURCE_ARCHIVED_NO_GAMEPK


def test_feed_exception_and_none_are_feed_unavailable_with_warning(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("timeout")

    for fetch in (boom, lambda *a, **k: None):
        monkeypatch.setattr(sm.mlb_boxscore, "fetch_game_feed", fetch)
        warnings = []
        ctx = sm._fetch_authoritative_game_context(GAME, warnings)
        assert ctx["statusSource"] == sm.STATUS_SOURCE_ARCHIVED_FEED_UNAVAILABLE
        assert ctx["gameStatus"] == "Pre-Game" and warnings


def test_identity_conflict_keeps_archived_status_and_is_labeled(monkeypatch):
    monkeypatch.setattr(sm.mlb_boxscore, "fetch_game_feed", lambda *a, **k: _feed("Final", away="NYY", home="TOR"))
    warnings = []
    ctx = sm._fetch_authoritative_game_context(GAME, warnings)
    assert ctx["statusSource"] == sm.STATUS_SOURCE_ARCHIVED_IDENTITY_CONFLICT and ctx["gameStatus"] == "Pre-Game"
    assert ctx["boxscoreFetchMeta"]["identityConflict"] is True and "identity conflict" in warnings[0]


def test_live_feed_status_is_labeled_live_even_when_not_final(monkeypatch):
    monkeypatch.setattr(sm.mlb_boxscore, "fetch_game_feed", lambda *a, **k: _feed("In Progress"))
    ctx = sm._fetch_authoritative_game_context(GAME, [])
    assert ctx["statusSource"] == sm.STATUS_SOURCE_LIVE_FEED and ctx["gameStatus"] == "In Progress"


def test_feed_without_status_falls_back_labeled(monkeypatch):
    feed = _feed("Final")
    feed["gameData"]["status"] = {}
    monkeypatch.setattr(sm.mlb_boxscore, "fetch_game_feed", lambda *a, **k: feed)
    ctx = sm._fetch_authoritative_game_context(GAME, [])
    assert ctx["statusSource"] == sm.STATUS_SOURCE_ARCHIVED_FEED_STATUS_MISSING and ctx["gameStatus"] == "Pre-Game"


# --- reason mapping ----------------------------------------------------------------------------------------

MARKET = {"marketTicker": "KXMLBHIT-26AUG021920BOSCWS-CWSPLAYER1-1", "eventTicker": "KXMLBHIT-26AUG021920BOSCWS",
          "title": "Some Player: 1+ hits?", "marketFamily": "hitter_hits", "gameId": "g1"}


def _settle(status, source):
    return settle_player_prop_market(MARKET, game_status=status, boxscore_teams={}, away_abbr="CWS", home_abbr="BOS", game_status_source=source)


def test_genuinely_unfinished_live_game_is_game_not_final():
    for source in ("LIVE_FEED", None):  # None = legacy callers that pass no source
        s, r, reason, ev = _settle("In Progress", source)
        assert (s, r, reason) == (SETTLEMENT_UNRESOLVED, None, "game_not_final")
    assert _settle("Postponed", "LIVE_FEED")[2] == "game_not_final"
    assert _settle("In Progress", "LIVE_FEED")[3]["gameStatusSource"] == "LIVE_FEED"


def test_unknown_status_sources_are_never_reported_as_not_final():
    for source in ("ARCHIVED_NO_GAMEPK", "ARCHIVED_FEED_UNAVAILABLE", "ARCHIVED_IDENTITY_CONFLICT", "ARCHIVED_FEED_STATUS_MISSING"):
        for stale in (None, "Pre-Game", "In Progress"):
            s, r, reason, ev = _settle(stale, source)
            assert s == SETTLEMENT_UNRESOLVED and r is None
            assert reason == f"game_status_unverified_{source.lower()}"
            assert ev["gameStatusSource"] == source


# --- end to end: observations are not games ---------------------------------------------------------------

def test_settle_date_reports_distinct_games_and_separates_reasons(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import mlb_boxscore, storage

    date = "2026-08-04"
    games = [
        {"gameId": "nopk", "mlbGamePk": None, "awayTeam": "CWS", "homeTeam": "BOS", "status": None},
        {"gameId": "live", "mlbGamePk": 222, "awayTeam": "NYY", "homeTeam": "TOR", "status": "Pre-Game"},
        {"gameId": "down", "mlbGamePk": 333, "awayTeam": "LAD", "homeTeam": "SD", "status": "Pre-Game"},
    ]
    storage.write_all_records(storage.partition_path("games", date), games)
    markets = []
    for gid in ("nopk", "live", "down"):
        for i in range(3):  # 3 hitter markets per game -> 3 observations, 1 game
            markets.append({"marketTicker": f"KXMLBHIT-T-{gid.upper()}{i}-1", "eventTicker": "KXMLBHIT-T", "title": f"Player {i}: 1+ hits?",
                            "marketFamily": "hitter_hits", "gameId": gid})
    storage.write_all_records(storage.partition_path("markets", date), markets)
    monkeypatch.setattr(sm, "fetch_mlb_linescore", lambda game_pk: None)

    def fetch(game_pk, timeout=15):
        if game_pk == 222:
            return _feed("In Progress", away="NYY", home="TOR")
        raise TimeoutError("down")

    monkeypatch.setattr(mlb_boxscore, "fetch_game_feed", fetch)
    summary = sm.settle_date(date, dry_run=True)

    reasons = summary["unresolvedReasonsByFamily"]["hitter_hits"]
    assert reasons == {"game_status_unverified_archived_no_gamepk": 3, "game_not_final": 3,
                       "game_status_unverified_archived_feed_unavailable": 3}
    assert summary["unresolvedDistinctGamesByReason"]["hitter_hits"] == {
        "game_status_unverified_archived_no_gamepk": 1, "game_not_final": 1, "game_status_unverified_archived_feed_unavailable": 1}
