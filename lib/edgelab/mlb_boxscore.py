#!/usr/bin/env python3
"""
lib/edgelab/mlb_boxscore.py
==============================
Network adapter + pure parsers for authoritative final player statistics
(GitHub issue #43: automatic settlement for pitcher/hitter player-prop
markets).

Fetches https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live -- ONE
network call per gamePk returns BOTH the game's authoritative status
(gameData.status) AND every player's final batting/pitching stat line
(liveData.boxscore.teams.{away,home}.players), so settling many
player-prop markets on the same game never needs more than one fetch --
see scripts/edgelab/settle_markets.py's per-gamePk cache.

Mirrors scripts/fetch_lineups.py's fetch_json() convention (a bare
try/except that returns None on ANY failure -- network error, timeout,
non-2xx, malformed JSON) deliberately duplicated rather than imported,
for the same reason fetch_lineups.py itself gives for not importing
scripts/build_kalshi_registry.py: this is a pure-adapter module other
code (and tests) import freely, so it must never fire a network call at
import time or depend on a script module with side effects.
"""
import hashlib
import json
import urllib.request

MLB_STATS_API = "https://statsapi.mlb.com/api/v1.1"

# Matches scripts/fetch_opp_quality.py's COMPLETED_STATUSES convention --
# any of these `status.detailedState` values means the game is truly
# over and final stats are authoritative. A live/suspended/delayed/
# postponed/incomplete game must NEVER be treated as final.
FINAL_DETAILED_STATES = frozenset({"Final", "Game Over", "Completed Early"})


def fetch_game_feed(game_pk, timeout=15):
    """
    Network adapter: fetch the raw MLB Stats API live-feed JSON for one
    game. Returns the parsed JSON dict, or None on any failure (network
    error, timeout, non-2xx, malformed JSON -- swallowed uniformly, same
    as fetch_lineups.py's fetch_json()). Makes no attempt to interpret
    the response shape -- that is this module's other, pure functions'
    job.
    """
    if not game_pk:
        return None
    url = f"{MLB_STATS_API}/game/{game_pk}/feed/live"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def extract_game_status(feed):
    """Pure. `feed.gameData.status.detailedState`, or None if unavailable."""
    if not feed:
        return None
    return ((feed.get("gameData") or {}).get("status") or {}).get("detailedState")


def extract_teams(feed):
    """
    Pure. `(awayAbbreviation, homeAbbreviation)` from
    `feed.gameData.teams`, or `(None, None)` if unavailable. Used to
    cross-check that a stored gamePk's own live feed actually describes
    the matchup we archived it for, before trusting anything else in the
    feed -- see scripts/edgelab/settle_markets.py's
    _fetch_authoritative_game_context.
    """
    if not feed:
        return None, None
    teams = (feed.get("gameData") or {}).get("teams") or {}
    return (teams.get("away") or {}).get("abbreviation"), (teams.get("home") or {}).get("abbreviation")


def is_final_status(detailed_state):
    """Pure. True only for a detailedState confirming the game is genuinely, officially over."""
    return detailed_state in FINAL_DETAILED_STATES


def extract_boxscore_teams(feed):
    """
    Pure. Returns `feed.liveData.boxscore.teams` (a dict with "away"/
    "home" keys, each holding a "players" dict keyed "ID<mlbamId>") or
    {} if unavailable -- never raises on a malformed/partial feed.
    """
    if not feed:
        return {}
    return (((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {})


def payload_hash(feed):
    """
    Pure. sha1 of the raw feed payload (sorted-key JSON re-serialization)
    -- lets a settlement's evidence trace back to the EXACT response it
    was derived from without committing the full feed itself (issue
    #43's "smallest auditable storage design"). None for a falsy feed.
    """
    if not feed:
        return None
    return hashlib.sha1(json.dumps(feed, sort_keys=True).encode("utf-8")).hexdigest()
