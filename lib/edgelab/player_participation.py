#!/usr/bin/env python3
"""
lib/edgelab/player_participation.py
=======================================
Explicit, conservative participation verification for player-prop
settlement (GitHub issue #43 correction round).

Being present in an MLB Stats API boxscore's `players` dict is NOT
proof of participation: a game's boxscore lists every player on both
teams' active rosters for that day, including players who sat on the
bench the entire game and never entered. Without this module, a bench
player's entirely-zero-filled `stats.batting`/`stats.pitching`
sub-object would be indistinguishable from a player who genuinely
played and recorded zero of the relevant statistic -- silently
mis-settling a DNP as a real NO.

Participation signal (MLB Stats API convention): a player's per-game
`gamesPlayed` (batting) / `gamesPitched` (pitching) stat split is
1 for ANY player who appeared in the game in ANY capacity that stat
group tracks -- including a PINCH RUNNER who scored or stole a base
without ever batting (that appearance still counts as a game played
for batting purposes). This is why `gamesPlayed`, not
`plateAppearances`/`atBats`, is the participation gate used here: a
pinch-runner-only appearance genuinely has `plateAppearances == 0` but
still `gamesPlayed == 1`, and must count as verified participation for
`hitter_hits_runs_rbis`/`hitter_stolen_bases` exactly as it would for
any other hitter family.

Never infers participation from the player merely being listed, and
never infers NON-participation either -- missing or inconclusive
evidence is always UNVERIFIED, never silently treated as "didn't play"
(which per lib/edgelab/player_prop_settlement.py's own module docstring
must never automatically become a settled NO or VOID without a directly
verified Kalshi rule).
"""

RESOLVED = "RESOLVED"
UNVERIFIED = "UNVERIFIED"

_ZERO_LIKE = (None, "", "0", "0.0", 0, 0.0)


def _positive_number(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def verify_pitcher_participation(player_entry):
    """
    Pure. Returns (status, reason, evidence) for a PITCHER prop.
    RESOLVED only when the authoritative pitching stat split positively
    shows the player pitched: `gamesPitched` (or `gamesPlayed` within
    the pitching stat group) >= 1, or a non-zero `inningsPitched`.
    Otherwise UNVERIFIED with reason "player_participation_unverified"
    -- never inferred from the player merely being listed in the
    boxscore, and never from a pitching sub-object that is present but
    entirely zero/empty (indistinguishable from a pitcher who never
    entered the game).
    """
    player_entry = player_entry or {}
    pitching = (player_entry.get("stats") or {}).get("pitching") or {}
    games_pitched = pitching.get("gamesPitched")
    games_played = pitching.get("gamesPlayed")
    innings_pitched = pitching.get("inningsPitched")
    evidence = {"gamesPitched": games_pitched, "gamesPlayed": games_played, "inningsPitched": innings_pitched}

    if _positive_number(games_pitched) or _positive_number(games_played):
        return RESOLVED, None, evidence
    if innings_pitched not in _ZERO_LIKE:
        return RESOLVED, None, evidence
    return UNVERIFIED, "player_participation_unverified", evidence


def verify_hitter_participation(player_entry):
    """
    Pure. Returns (status, reason, evidence) for a HITTER (or
    pinch-runner) prop. RESOLVED only when the authoritative batting
    stat split positively shows the player appeared in the game --
    `gamesPlayed` >= 1 (covers a full plate appearance AND a
    pinch-runner-only appearance alike -- see module docstring), or a
    positive `plateAppearances`/`atBats` as a secondary signal.
    Otherwise UNVERIFIED with reason "player_participation_unverified"
    -- never inferred merely from the player being listed, and never
    from a batting sub-object that is present but entirely zero-filled
    (indistinguishable from an unused bench player's entry).
    """
    player_entry = player_entry or {}
    batting = (player_entry.get("stats") or {}).get("batting") or {}
    games_played = batting.get("gamesPlayed")
    plate_appearances = batting.get("plateAppearances")
    at_bats = batting.get("atBats")
    evidence = {"gamesPlayed": games_played, "plateAppearances": plate_appearances, "atBats": at_bats}

    if _positive_number(games_played):
        return RESOLVED, None, evidence
    if _positive_number(plate_appearances) or _positive_number(at_bats):
        return RESOLVED, None, evidence
    return UNVERIFIED, "player_participation_unverified", evidence


def verify_participation(player_entry, stat_category):
    """
    Dispatches to verify_pitcher_participation/verify_hitter_participation
    by `stat_category` ("pitching"/"batting", from
    lib.edgelab.player_stats.STAT_CATEGORY_BY_FAMILY). Returns
    (UNVERIFIED, "unrecognized_stat_category", {}) for anything else --
    defensive only, never reached by a caller that already validated
    the family.
    """
    if stat_category == "pitching":
        return verify_pitcher_participation(player_entry)
    if stat_category == "batting":
        return verify_hitter_participation(player_entry)
    return UNVERIFIED, "unrecognized_stat_category", {}
