#!/usr/bin/env python3
"""
lib/postponed_guard.py
=======================
Postponed/Cancelled Game Guard

Pregame check: if MLB game status is Postponed/Cancelled/Suspended/Delayed:
  - Skip market generation (pregame)
  - Mark any already-tracked bets as VOID
  - Set skipReason
  - Do not count as win/loss, do not affect bankroll

Regression: DET@CLE June 14 was postponed → all bets VOID
"""

from typing import Optional

# ── Status strings that mean the game will not be played normally ─────────────
POSTPONED_STATUSES = {
    "Postponed",
    "Cancelled",
    "Canceled",
    "Suspended",
    "Delayed",
    "Delayed Start",
    "Rain Delay",
    "Postponed - Rain",
    "Postponed - Other",
}

# Status strings that mean the game has NOT started but will play
ACTIVE_PREGAME_STATUSES = {
    "Scheduled",
    "Pre-Game",
    "Warmup",
    "Pregame",
}

# Status strings that mean the game is in play
IN_PLAY_STATUSES = {
    "In Progress",
    "Live",
    "Manager Challenge",
    "Instant Replay",
    "Delay",
    "Delayed",
    "Rain Delay",
}

# Status strings for completed games
FINAL_STATUSES = {
    "Final",
    "Game Over",
    "Completed",
    "Completed Early",
    "Completed - Postponed",
    "Completed - Suspended",
}


def is_postponed(game_status: Optional[str]) -> bool:
    """
    Return True if the game status indicates the game will not be played as scheduled.
    Case-insensitive comparison.
    """
    if not game_status:
        return False
    status = game_status.strip()
    if status in POSTPONED_STATUSES:
        return True
    lower = status.lower()
    for ps in POSTPONED_STATUSES:
        if ps.lower() in lower:
            return True
    return False


def is_pregame(game_status: Optional[str]) -> bool:
    """Return True if game has not yet started."""
    if not game_status:
        return True  # Assume pregame if no status
    status = game_status.strip()
    return status in ACTIVE_PREGAME_STATUSES


def is_in_play(game_status: Optional[str]) -> bool:
    """Return True if game is currently in progress."""
    if not game_status:
        return False
    status = game_status.strip()
    return status in IN_PLAY_STATUSES


def is_final(game_status: Optional[str]) -> bool:
    """Return True if game is complete."""
    if not game_status:
        return False
    status = game_status.strip()
    return status in FINAL_STATUSES


def check_game_status(game_dict) -> dict:
    """
    Check a game dict's status and return a status result.

    Args:
        game_dict: dict with 'status' field from MLB API or slate

    Returns:
        dict with:
            shouldSkip: bool — True if market generation should be skipped
            skipReason: str | None
            voidExisting: bool — True if existing tracked bets should be voided
            gameStatus: str — normalized status
            isPostponed: bool
            isPregame: bool
            isInPlay: bool
            isFinal: bool
    """
    game_pk = game_dict.get("gameId") or game_dict.get("gamePk") or "unknown"
    raw_status = game_dict.get("status") or game_dict.get("gameStatus") or ""
    status = raw_status.strip()

    away = (game_dict.get("away") or {}).get("abbr", "AWAY")
    home = (game_dict.get("home") or {}).get("abbr", "HOME")
    matchup = f"{away}@{home}"

    postponed = is_postponed(status)
    pregame   = is_pregame(status)
    in_play   = is_in_play(status)
    final     = is_final(status)

    # Resolve overlapping statuses: statuses that appear in both POSTPONED and
    # IN_PLAY/FINAL sets (e.g. "Rain Delay", "Completed - Postponed") indicate
    # the game started and was then delayed/suspended/completed.  These are
    # live-game blocks, not pre-first-pitch postponements — check in_play/final
    # first so they are never incorrectly routed to the postpone branch.

    if postponed and not in_play and not final:
        return {
            "shouldSkip": True,
            "skipReason": f"postponed",
            "voidExisting": True,
            "gameStatus": status,
            "gamePk": game_pk,
            "matchup": matchup,
            "isPostponed": True,
            "isPregame": False,
            "isInPlay": False,
            "isFinal": False,
            "liveGameBlocked": False,
            "message": f"{matchup} (gamePk={game_pk}) is {status} — skipping market generation, voiding existing bets"
        }

    # Live game hard block — pregame-only mode cannot recommend/log bets for games
    # that have already started. This covers In Progress, Final, Completed, etc.
    # A game may only be analyzed in LIVE_BET mode (never as official pregame real-money).
    if in_play or final:
        block_reason = "LIVE_GAME_BLOCKED" if in_play else "PREGAME_ONLY_STARTED_GAME"
        return {
            "shouldSkip": True,
            "skipReason": block_reason,
            "voidExisting": False,       # do not void; game played normally, just too late
            "gameStatus": status,
            "gamePk": game_pk,
            "matchup": matchup,
            "isPostponed": False,
            "isPregame": False,
            "isInPlay": in_play,
            "isFinal": final,
            "liveGameBlocked": True,
            "message": (
                f"{matchup} is {status} — pregame-only mode blocks all real-money "
                f"recommendations. Use LIVE_BET mode to analyze in-progress markets."
            )
        }

    return {
        "shouldSkip": False,
        "skipReason": None,
        "voidExisting": False,
        "gameStatus": status,
        "gamePk": game_pk,
        "matchup": matchup,
        "isPostponed": False,
        "isPregame": pregame,
        "isInPlay": in_play,
        "isFinal": final,
        "liveGameBlocked": False,
        "message": f"{matchup} status: {status or 'unknown'}"
    }


def void_bets_for_game(bets_list, game_pk=None, matchup=None, reason="postponed"):
    """
    Mark all bets for a game as VOID.
    At least one of game_pk or matchup must be provided.

    Args:
        bets_list: list of bet dicts (mutated in place)
        game_pk: str — MLB game primary key
        matchup: str — e.g. "DET@CLE"
        reason: skip reason string

    Returns:
        list of voided bet IDs
    """
    voided = []
    game_pk_str = str(game_pk) if game_pk else None

    for bet in bets_list:
        bet_game_pk = str(bet.get("gamePk") or bet.get("gameId") or "")
        bet_game = bet.get("game", "")

        match_pk = game_pk_str and (bet_game_pk == game_pk_str)
        match_matchup = matchup and (matchup in bet_game or bet_game in matchup)

        if match_pk or match_matchup:
            if bet.get("result") not in ("VOID", "WIN", "LOSS", "PUSH"):
                bet["result"] = "VOID"
                bet["pl"] = 0
                bet["actuallyPlaced"] = False
                bet["skipReason"] = reason
                existing_notes = bet.get("notes", "")
                bet["notes"] = f"VOID — game {reason}. {existing_notes}".strip()
                voided.append(bet.get("id") or bet.get("ticker", "unknown"))

    return voided


if __name__ == "__main__":
    # Test
    test_game = {
        "gameId": "777",
        "status": "Postponed",
        "away": {"abbr": "DET"},
        "home": {"abbr": "CLE"},
    }
    result = check_game_status(test_game)
    print(result)

    # Test VOID
    test_bets = [
        {"id": "001", "game": "DET@CLE", "result": None, "pl": 5.0},
        {"id": "002", "game": "NYY@TOR", "result": None, "pl": 3.0},
    ]
    voided = void_bets_for_game(test_bets, matchup="DET@CLE", reason="postponed")
    print(f"Voided: {voided}")
    print(test_bets[0])


def is_live_game_blocked(game_status_result: dict) -> bool:
    """
    Return True if a game was blocked by the live-game pregame-only gate.
    Use this to distinguish live-game blocks from postponement blocks.
    """
    return bool(game_status_result.get("liveGameBlocked", False))


def check_first_pitch_passed(scheduled_start_utc: str, current_utc: str = None) -> bool:
    """
    Returns True if the scheduled first pitch has already passed.
    Used as an additional live-game detection signal when game status is unavailable.

    Args:
        scheduled_start_utc: ISO 8601 string of scheduled first pitch (UTC)
        current_utc: ISO 8601 string of current time (UTC). Defaults to now.
    """
    from datetime import datetime, timezone
    if not scheduled_start_utc:
        return False
    try:
        fp = datetime.fromisoformat(scheduled_start_utc.replace('Z', '+00:00'))
        now = (
            datetime.fromisoformat(current_utc.replace('Z', '+00:00'))
            if current_utc
            else datetime.now(tz=timezone.utc)
        )
        return now > fp
    except (ValueError, TypeError):
        return False
