#!/usr/bin/env python3
"""
tests/edgelab/test_mlb_schedule.py
======================================
Coverage for lib/edgelab/mlb_schedule.py -- the second, live-MLB-schedule
identity source for a standalone/manual-only Kalshi betting day that
never had a data/pipeline/<date>/normalized_slate.json run (the real
2026-08-11 case: all 15 archived games stayed mlbGamePk=null forever
because lib.edgelab.market_universe.backfill_missing_game_pks only ever
had that one source to consult).

fetch_schedule itself is a thin network adapter (mirrors
lib.edgelab.mlb_boxscore.fetch_game_feed's convention) -- not exercised
here with live network, only via monkeypatch, same as
tests/edgelab/test_settle_markets_script.py already does for
mlb_boxscore.fetch_game_feed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import mlb_schedule
from lib.edgelab.market_universe import backfill_missing_game_pks
from lib.edgelab.mlb_schedule import (
    backfill_missing_game_pks_via_schedule,
    build_schedule_game_context,
    fetch_schedule,
    parse_schedule_games,
    resolve_doubleheader_candidate,
    resolve_schedule_game_context,
)


def _schedule_json(games):
    """games: list of (gamePk, awayTeamId, homeTeamId, gameDate, status, venue, gameNumber)."""
    return {
        "dates": [{
            "games": [
                {
                    "gamePk": g[0],
                    "teams": {
                        "away": {"team": {"id": g[1]}},
                        "home": {"team": {"id": g[2]}},
                    },
                    "gameDate": g[3],
                    "status": {"detailedState": g[4]},
                    "venue": {"name": g[5]},
                    "gameNumber": g[6] if len(g) > 6 else 1,
                }
                for g in games
            ],
        }],
    }


# ---------------------------------------------------------------------------
# parse_schedule_games -- pure parsing
# ---------------------------------------------------------------------------

def test_parse_schedule_games_extracts_one_dict_per_game():
    raw = _schedule_json([
        (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),  # KC(118) @ LAD(119)
    ])
    games = parse_schedule_games(raw)
    assert len(games) == 1
    g = games[0]
    assert g["gamePk"] == 745123
    assert g["awayTeamId"] == 118
    assert g["homeTeamId"] == 119
    assert g["scheduledStart"] == "2026-08-11T02:10:00Z"
    assert g["status"] == "Final"
    assert g["venue"] == "Dodger Stadium"


def test_parse_schedule_games_skips_entries_with_no_gamePk():
    raw = {"dates": [{"games": [{"teams": {}}]}]}
    assert parse_schedule_games(raw) == []


def test_parse_schedule_games_handles_missing_or_malformed_response():
    assert parse_schedule_games(None) == []
    assert parse_schedule_games({}) == []
    assert parse_schedule_games({"dates": []}) == []


# ---------------------------------------------------------------------------
# build_schedule_game_context -- team-ID based, doubleheader-safe
# ---------------------------------------------------------------------------

def test_build_schedule_game_context_resolves_unique_team_pair():
    """Scenario 2: a single scheduled game for a team pair resolves cleanly, keyed by MLB's own stable teamId -- never a name-based guess."""
    parsed = parse_schedule_games(_schedule_json([
        (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),  # KC @ LAD
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert warnings == []
    assert context == {
        ("KC", "LAD"): {
            "gameId": "745123", "scheduledStart": "2026-08-11T02:10:00Z",
            "status": "Final", "venue": "Dodger Stadium", "kalshiKey": None,
        },
    }


def test_build_schedule_game_context_uses_azimuth_abbreviation_not_ari():
    """
    Regression: this repo's own archived Game/Market rows and Kalshi's
    own tickers use 'AZ' for Arizona (see
    lib.kalshi_mlb_contract_parser.TWO_LETTER_TEAM_ABBRS), NOT 'ARI' --
    unlike the unrelated older clv_update.py ledger's TEAM_TO_ABBR table.
    Team id 109 is Arizona's real, stable MLB teamId.
    """
    parsed = parse_schedule_games(_schedule_json([
        (745200, 115, 109, "2026-08-11T02:40:00Z", "Final", "Chase Field"),  # COL(115) @ AZ(109)
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert warnings == []
    assert ("COL", "AZ") in context
    assert "ARI" not in str(context)


def test_build_schedule_game_context_uses_ath_not_oak_for_athletics():
    parsed = parse_schedule_games(_schedule_json([
        (745300, 139, 133, "2026-08-11T01:40:00Z", "Final", "Sutter Health Park"),  # TB(139) @ ATH(133)
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert warnings == []
    assert ("TB", "ATH") in context


def test_build_schedule_game_context_excludes_ambiguous_doubleheader_pair():
    """Scenario 3: two scheduled games for the same team pair/date must NEVER be collapsed into one context entry -- refused, not guessed."""
    parsed = parse_schedule_games(_schedule_json([
        (745400, 121, 144, "2026-08-11T17:10:00Z", "Final", "Truist Park", 1),  # NYM @ ATL, game 1
        (745401, 121, 144, "2026-08-11T21:10:00Z", "Final", "Truist Park", 2),  # NYM @ ATL, game 2
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert ("NYM", "ATL") not in context
    assert len(warnings) == 1
    assert "multiple scheduled games" in warnings[0]
    assert "NYM@ATL" in warnings[0]


def test_build_schedule_game_context_reports_unmapped_teamId_never_silently_drops():
    parsed = parse_schedule_games(_schedule_json([
        (745500, 999999, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert context == {}
    assert len(warnings) == 1
    assert "unmapped MLB teamId" in warnings[0]
    assert "999999" in warnings[0]


def test_build_schedule_game_context_unrelated_pairs_stay_independent():
    """A doubleheader for ONE pair must never block resolution of every OTHER pair that date."""
    parsed = parse_schedule_games(_schedule_json([
        (745400, 121, 144, "2026-08-11T17:10:00Z", "Final", "Truist Park", 1),
        (745401, 121, 144, "2026-08-11T21:10:00Z", "Final", "Truist Park", 2),
        (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),
    ]))
    context, warnings = build_schedule_game_context(parsed)
    assert ("NYM", "ATL") not in context
    assert ("KC", "LAD") in context
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# resolve_doubleheader_candidate -- disambiguate via genuine, already-known
# signals only (never a reverse-engineered ticker HHMM -- see module docstring)
# ---------------------------------------------------------------------------

def _candidates():
    return [
        {"gamePk": 745400, "gameNumber": 1, "scheduledStart": "2026-08-11T17:10:00Z"},
        {"gamePk": 745401, "gameNumber": 2, "scheduledStart": "2026-08-11T21:10:00Z"},
    ]


def test_resolve_doubleheader_candidate_via_game_number():
    row = {"doubleheaderGameNumber": 2}
    chosen, reason = resolve_doubleheader_candidate(row, _candidates())
    assert reason is None
    assert chosen["gamePk"] == 745401


def test_resolve_doubleheader_candidate_via_unambiguous_scheduled_start():
    row = {"doubleheaderGameNumber": None, "scheduledStartTime": "2026-08-11T17:05:00Z"}
    chosen, reason = resolve_doubleheader_candidate(row, _candidates())
    assert reason is None
    assert chosen["gamePk"] == 745400


def test_resolve_doubleheader_candidate_refuses_when_no_signal_at_all():
    """Scenario 3 (the actual Aug 11 case): an archived row with neither signal is left unresolved, never guessed."""
    row = {"doubleheaderGameNumber": None, "scheduledStartTime": None}
    chosen, reason = resolve_doubleheader_candidate(row, _candidates())
    assert chosen is None
    assert "no doubleheaderGameNumber or scheduledStartTime" in reason


def test_resolve_doubleheader_candidate_refuses_when_near_tie():
    row = {"doubleheaderGameNumber": None, "scheduledStartTime": "2026-08-11T19:10:00Z"}  # exactly between both legs
    chosen, reason = resolve_doubleheader_candidate(row, _candidates())
    assert chosen is None
    assert "within 5 minutes" in reason


# ---------------------------------------------------------------------------
# resolve_schedule_game_context -- orchestration, network mocked
# ---------------------------------------------------------------------------

def test_resolve_schedule_game_context_success(monkeypatch):
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([(745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium")]),
    )
    context, warnings = resolve_schedule_game_context("2026-08-11")
    assert warnings == []
    assert ("KC", "LAD") in context


def test_resolve_schedule_game_context_fetch_failure_never_fabricates(monkeypatch):
    """Scenario 1: the actual live fetch failing (network policy, outage, etc.) must return an explicit empty context + reason, never raise, never guess."""
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: None)
    context, warnings = resolve_schedule_game_context("2026-08-11")
    assert context == {}
    assert len(warnings) == 1
    assert "MLB schedule fetch failed" in warnings[0]
    assert "2026-08-11" in warnings[0]


# ---------------------------------------------------------------------------
# backfill_missing_game_pks_via_schedule -- the actual repair-game-identity
# entry point, composing resolve_schedule_game_context with the EXISTING,
# unmodified lib.edgelab.market_universe.backfill_missing_game_pks
# ---------------------------------------------------------------------------

def _stuck_game(game_id="2026-08-11_KC_LAD_2210", away="KC", home="LAD"):
    return {
        "schemaVersion": "1", "gameId": game_id, "sport": "MLB", "platform": "KALSHI",
        "mlbGamePk": None, "gameDate": "2026-08-11", "scheduledStartTime": None,
        "actualStartTime": None, "awayTeam": away, "homeTeam": home, "venue": None,
        "status": None, "doubleheaderGameNumber": None, "kalshiKey": None,
        "createdAt": "2026-08-12T03:35:15Z", "updatedAt": None, "source": "kalshi_registry_snapshots",
        "validationStatus": "warning",
        "provenance": {"sourceSystem": "kalshi_registry_snapshots", "sourceFile": "x.json", "sourceKey": game_id, "capturedAt": "2026-08-12T03:35:15Z", "ingestedAt": "2026-08-12T03:35:15Z"},
    }


def test_backfill_via_schedule_resolves_a_standalone_day_with_no_pipeline_slate(monkeypatch):
    """
    Scenario 1+2 end to end: a standalone/manual-only Kalshi day (the
    real 2026-08-11 shape -- every archived Game row still mlbGamePk=null,
    no data/pipeline/2026-08-11/normalized_slate.json ever existed) gets
    resolved entirely from the live MLB schedule, with the SAME
    unmodified backfill_missing_game_pks pure function main line ingest
    already uses for the pipeline-slate source.
    """
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([(745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium")]),
    )
    games = [_stuck_game()]
    updated, warnings = backfill_missing_game_pks_via_schedule(games, "2026-08-11")
    assert warnings == []
    assert len(updated) == 1
    fixed = updated[0]
    assert fixed["gameId"] == "2026-08-11_KC_LAD_2210"  # never renamed
    assert fixed["mlbGamePk"] == "745123"
    assert fixed["status"] == "Final"
    assert fixed["venue"] == "Dodger Stadium"
    assert fixed["mlbGamePkBackfill"]["method"] == "DATE_AWAY_HOME_UNIQUE_MATCH"
    assert "statsapi.mlb.com" in fixed["mlbGamePkBackfill"]["matchedAgainst"]  # provenance says schedule, not a slate path


def test_backfill_via_schedule_never_fetches_when_nothing_missing(monkeypatch):
    """No wasted network call for an already-fully-resolved date."""
    calls = []
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: calls.append(date) or None)
    games = [dict(_stuck_game(), mlbGamePk="999999")]
    updated, warnings = backfill_missing_game_pks_via_schedule(games, "2026-08-11")
    assert updated == []
    assert warnings == []
    assert calls == []  # fetch_schedule never even called


def test_backfill_via_schedule_never_touches_an_already_resolved_row(monkeypatch):
    """One resolved row + one still-missing row: only the missing one is ever touched."""
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([
            (745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium"),
            (745300, 139, 133, "2026-08-11T01:40:00Z", "Final", "Sutter Health Park"),
        ]),
    )
    already_resolved = dict(_stuck_game(game_id="745999", away="TB", home="ATH"), mlbGamePk="745999")
    still_missing = _stuck_game()
    updated, warnings = backfill_missing_game_pks_via_schedule([already_resolved, still_missing], "2026-08-11")
    assert warnings == []
    assert len(updated) == 1
    assert updated[0]["gameId"] == "2026-08-11_KC_LAD_2210"


def test_backfill_via_schedule_refuses_a_genuine_doubleheader_pair(monkeypatch):
    """Scenario 3 via the full orchestration path: an ambiguous pair is left untouched and reported, never guessed."""
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([
            (745400, 121, 144, "2026-08-11T17:10:00Z", "Final", "Truist Park", 1),
            (745401, 121, 144, "2026-08-11T21:10:00Z", "Final", "Truist Park", 2),
        ]),
    )
    games = [_stuck_game(game_id="2026-08-11_NYM_ATL_1710", away="NYM", home="ATL")]
    updated, warnings = backfill_missing_game_pks_via_schedule(games, "2026-08-11")
    assert updated == []
    assert len(warnings) == 1
    assert "doubleheader ambiguity" in warnings[0]


def test_backfill_via_schedule_is_idempotent(monkeypatch):
    """Scenario 4: running the repair twice in a row never double-writes/produces a second backfill marker -- the second call is a true no-op once the row is resolved."""
    monkeypatch.setattr(
        mlb_schedule, "fetch_schedule",
        lambda date, timeout=15: _schedule_json([(745123, 118, 119, "2026-08-11T02:10:00Z", "Final", "Dodger Stadium")]),
    )
    games = [_stuck_game()]
    first_updated, _ = backfill_missing_game_pks_via_schedule(games, "2026-08-11")
    assert len(first_updated) == 1

    # Apply the first pass's result the same way a caller (repair_game_identity.py) would.
    games_after_first_pass = [first_updated[0]]
    second_updated, second_warnings = backfill_missing_game_pks_via_schedule(games_after_first_pass, "2026-08-11")
    assert second_updated == []  # no-op: mlbGamePk is already set, never re-fetched or re-touched
    assert second_warnings == []


def test_fetch_schedule_returns_none_on_any_failure_without_raising():
    """Thin network-adapter smoke test -- a real network call in this sandboxed test environment fails fast (no live MLB Stats API access), and must never raise."""
    result = fetch_schedule("2026-08-11", timeout=1)
    assert result is None or isinstance(result, dict)
