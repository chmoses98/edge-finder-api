#!/usr/bin/env python3
"""
lib/edgelab/player_resolution.py
====================================
Conservative player resolution for player-prop settlement (GitHub issue
#43). Restricts search to the two rosters of the EXACT MLB game a market
belongs to -- never an unrestricted league-wide fuzzy match -- and
requires an exact match on a normalized display name (see
lib.research.player_prop_parser.normalized_name_variants). A ticker's
own player token / jersey-number-style suffix is used ONLY as secondary
corroboration to break a tie among multiple exact-name candidates, never
as a primary identity signal and never to resolve a market whose name
signal alone found zero candidates.

Resolution order (per issue #43):
  1. Exact MLB gamePk (enforced by the caller -- this module only ever
     sees one game's boxscore_teams at a time, already scoped by
     lib.edgelab.mlb_boxscore.extract_boxscore_teams for that gamePk).
  2. Correct team within that game (teamAbbr, when known).
  3. Normalized full player name from the market title.
  4. The raw ticker token / jersey number, ONLY as secondary
     corroboration when step 3 alone leaves more than one candidate --
     never promoted to a primary identity signal.
  5. A unique MLB player result -- zero or multiple candidates after all
     of the above is left unresolved (never a "closest name" guess).
"""
from lib.research.player_prop_parser import normalize_player_name

RESOLVED = "RESOLVED"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"


def _iter_side_players(boxscore_teams, side):
    players = ((boxscore_teams.get(side) or {}).get("players")) or {}
    return players.values()


def _candidate_from_player(player, side):
    person = player.get("person") or {}
    return {
        "playerId": person.get("id"),
        "playerName": person.get("fullName"),
        "side": side,
        "jerseyNumber": player.get("jerseyNumber"),
    }


def resolve_player_in_game(boxscore_teams, normalized_name_variants, team_abbr=None,
                            away_abbr=None, home_abbr=None, ticker_numeric_suffix=None):
    """
    Pure. boxscore_teams: {"away": {"players": {...}}, "home": {...}} --
    already scoped to ONE exact game. normalized_name_variants: a
    non-empty set/frozenset of acceptable normalized names (see
    lib.research.player_prop_parser.normalized_name_variants) -- if
    empty/falsy, returns NOT_FOUND immediately (no name signal to search
    on at all -- never a guess).

    Returns:
      {"status": RESOLVED | NOT_FOUND | AMBIGUOUS,
       "candidate": {"playerId", "playerName", "side", "jerseyNumber"} | None,
       "candidates": [...same shape, every exact-name match found...],
       "corroboratedBy": "jersey_number" | None}
    """
    result = {"status": NOT_FOUND, "candidate": None, "candidates": [], "corroboratedBy": None}
    if not normalized_name_variants:
        return result

    if team_abbr and away_abbr and team_abbr == away_abbr:
        sides = ["away"]
    elif team_abbr and home_abbr and team_abbr == home_abbr:
        sides = ["home"]
    else:
        # Team unresolved (or doesn't match either side) -- still scoped
        # to this exact game's two rosters, never wider.
        sides = ["away", "home"]

    candidates = []
    for side in sides:
        for player in _iter_side_players(boxscore_teams, side):
            person = player.get("person") or {}
            full_name = person.get("fullName")
            if not full_name:
                continue
            if normalize_player_name(full_name) in normalized_name_variants:
                candidates.append(_candidate_from_player(player, side))

    result["candidates"] = candidates

    if len(candidates) == 1:
        result["status"] = RESOLVED
        result["candidate"] = candidates[0]
        return result
    if len(candidates) == 0:
        return result

    # More than one exact-name match (e.g. team unresolved and the same
    # name legitimately appears on both rosters, or a duplicate boxscore
    # entry) -- jersey number is corroboration ONLY, never a tiebreak
    # promoted to primary evidence, and never consulted unless the name
    # match already produced more than one candidate.
    if ticker_numeric_suffix is not None:
        corroborated = [c for c in candidates if str(c.get("jerseyNumber")) == str(ticker_numeric_suffix)]
        if len(corroborated) == 1:
            result["status"] = RESOLVED
            result["candidate"] = corroborated[0]
            result["corroboratedBy"] = "jersey_number"
            return result

    result["status"] = AMBIGUOUS
    return result
