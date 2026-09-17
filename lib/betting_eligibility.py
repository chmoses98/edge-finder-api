"""
lib/betting_eligibility.py
==============================
THE one answer to "may this game appear on the real-money handicapping
card?", and the vocabulary that keeps five different questions from being
confused with each other.

THE FIVE AXES (never blur them)
-------------------------------
1. **GAME ELIGIBILITY** — may this game be on the confirmed-lineup
   real-money card at all? Decided here. Not started, BOTH official
   lineups confirmed, data-integrity gates pass.
2. **MARKET AVAILABILITY** — does Kalshi actually list this contract?
   Decided by the archive (`lib.kalshi_price_check`), not by us.
3. **PRODUCTION MODEL SUPPORT** — does the production model price it?
   The 11-row `g['marketLedger']` universe. Narrow, and deliberately so.
4. **MANUAL HANDICAPPING ELIGIBILITY** — can the analyst reasonably
   evaluate it from the evidence? Broader than 3: an F3, an alternate
   total or a prop can be a perfectly good manual expression with no
   production adapter behind it.
5. **AUTOMATIC SETTLEMENT SUPPORT** — can this repo grade it
   automatically afterwards? Narrower again, and orthogonal to all of
   the above.

A market with no production adapter is NOT invisible to a handicapper.
A market with no automatic settlement support is NOT forbidden — it is
flagged, because a wager that cannot settle automatically has to be
reconciled by hand. And a game whose lineups are not confirmed cannot
reach the real-money card no matter how attractive its markets look.

WHY THE LINEUP GATE IS THE HARD ONE
-----------------------------------
This repository already enforces confirmed lineups per market family --
Rules 50/51/52/53 in `scripts/build_market_ledger.py` downgrade TT / ML /
YRFI-NRFI / F5 to PAPER when `lineupConfirmedOfficial` is false on either
side. That is a per-ROW downgrade inside the production ledger. The
manual handicapping card needs the same rule applied one level up, at the
GAME, because the card's whole premise is "inspect every market for this
game" -- and per-row downgrades do not exist for the families the
production ledger has never modelled.

So: the game is gated once, here, and everything downstream of an
eligible game may be compared freely.

`lineupConfirmedOfficial` is the ONLY field trusted. `lineupConfirmed`
(the looser flag) and any probable/projected lineup are explicitly NOT
sufficient -- see `LINEUP_FIELD` and `classify_game_eligibility`.

Pure: no I/O, no network. Callers supply an already-loaded slate game.
"""
from lib.postponed_guard import check_game_status

# The ONE lineup field that means "an official lineup was posted". The
# looser `lineupConfirmed` can be set by a partial/probable parse, so it
# is deliberately not accepted as a substitute (scripts/fetch_lineups.py
# is what distinguishes them).
LINEUP_FIELD = "lineupConfirmedOfficial"
LOOSE_LINEUP_FIELD = "lineupConfirmed"

ELIGIBLE = "BETTING_ELIGIBLE"
BLOCKED_STARTED = "BLOCKED_GAME_STARTED"
BLOCKED_POSTPONED = "BLOCKED_GAME_POSTPONED"
BLOCKED_LINEUPS = "BLOCKED_LINEUPS_UNCONFIRMED"
BLOCKED_DATA = "BLOCKED_DATA_INTEGRITY"

# Every status that is NOT ELIGIBLE keeps the game fully archived and
# fully researchable; it only keeps it off the real-money card.
RESEARCH_ONLY_STATUSES = (BLOCKED_STARTED, BLOCKED_POSTPONED, BLOCKED_LINEUPS, BLOCKED_DATA)


# Slate games carry their first pitch as `startTime`. lib/postponed_guard's
# timestamp fallback looks for `scheduledStartTime`/`gameTime`/`firstPitch`/
# `scheduledStart` and does NOT read `startTime`, so on a slate game that
# fallback is inert -- a slate fetched before first pitch still reads
# "Pre-Game" hours later, and only the explicit status would block it.
#
# This module normalizes the field before delegating, so the card's gate
# gets the two-signal block the guard was designed to give. It deliberately
# does NOT change lib/postponed_guard itself: that module is on the
# real-money execution path (risk_gate, write_pending_bets,
# validate_bet_logging), and widening its inputs is a production behaviour
# change that belongs in its own reviewed change, not in a card's gate.
_START_TIME_FIELDS = ("scheduledStartTime", "gameTime", "firstPitch", "scheduledStart", "startTime")


def _with_normalized_start(game):
    """Pure. A shallow copy whose `scheduledStartTime` is populated from
    whichever start field this game actually carries. Never mutates the
    caller's game."""
    if game.get("scheduledStartTime"):
        return game
    for field in _START_TIME_FIELDS:
        if game.get(field):
            return {**game, "scheduledStartTime": game[field]}
    return game


def _team_stats(game, side):
    return game.get(f"{side}TeamStats") or {}


def lineup_confirmation(game):
    """
    Pure. Per-side official-lineup confirmation, plus exactly why a side
    is not confirmed. Never infers confirmation from a probable or
    partial lineup: a missing `lineupConfirmedOfficial` field is treated
    as NOT confirmed (the fail-closed direction), and the fact that the
    field was missing entirely is reported separately from an explicit
    False so a data gap is never mistaken for a posted-but-incomplete
    lineup.
    """
    sides = {}
    for side in ("away", "home"):
        stats = _team_stats(game, side)
        official = stats.get(LINEUP_FIELD)
        sides[side] = {
            "team": (game.get(side) or {}).get("abbr"),
            "lineupConfirmedOfficial": bool(official),
            "fieldPresent": LINEUP_FIELD in stats,
            "looseLineupConfirmed": bool(stats.get(LOOSE_LINEUP_FIELD)),
            "battersResolved": stats.get("lineupBattersResolved"),
            "lineupCheckedAt": stats.get("lineupCheckedAt") or game.get("lineupCheckedAt"),
            "reason": (
                None if official
                else ("lineupConfirmedOfficial field absent from the slate -- treated as UNCONFIRMED"
                      if LINEUP_FIELD not in stats
                      else "official lineup not posted yet")
            ),
        }
    sides["bothConfirmed"] = sides["away"]["lineupConfirmedOfficial"] and sides["home"]["lineupConfirmedOfficial"]
    sides["unconfirmedSides"] = [s for s in ("away", "home") if not sides[s]["lineupConfirmedOfficial"]]
    return sides


def classify_game_eligibility(game, *, now_utc=None):
    """
    Pure. THE game-level gate.

    Returns:
      {"status": <one of the constants>,
       "eligible": bool,
       "reason": str,
       "lineups": <lineup_confirmation(game)>,
       "gameStatus": {...check_game_status() verbatim...},
       "gameId", "matchup", "scheduledStart"}

    Order matters and is deliberate: a started or postponed game is
    reported as such even when its lineups WERE confirmed, because
    "confirmed but already playing" and "not confirmed" are different
    operational facts and a card that conflates them teaches the wrong
    lesson.

    `check_game_status` is reused unchanged -- it already implements the
    two-signal pregame-only block (explicit status plus a first-pitch
    timestamp fallback), so this module adds no second opinion about
    whether a game has started.
    """
    status_result = check_game_status(_with_normalized_start(game), current_utc=now_utc)
    lineups = lineup_confirmation(game)
    away = (game.get("away") or {}).get("abbr")
    home = (game.get("home") or {}).get("abbr")

    base = {
        "gameId": game.get("gameId"),
        "matchup": f"{away}@{home}" if away and home else None,
        "scheduledStart": (game.get("startTime") or game.get("scheduledStartTime")
                           or game.get("gameTime")),
        "lineups": lineups,
        "gameStatus": status_result,
    }

    if status_result.get("isPostponed"):
        return {**base, "status": BLOCKED_POSTPONED, "eligible": False,
                "reason": f"game is postponed ({status_result.get('gameStatus')})"}

    if status_result.get("liveGameBlocked") or status_result.get("isInPlay") or status_result.get("isFinal"):
        detail = "first-pitch timestamp has passed" if status_result.get("timestampBlocked") \
            else f"status is {status_result.get('gameStatus')}"
        return {**base, "status": BLOCKED_STARTED, "eligible": False,
                "reason": f"game has already started -- {detail}"}

    if not lineups["bothConfirmed"]:
        missing = ", ".join(
            f"{side}({lineups[side]['team'] or '?'}): {lineups[side]['reason']}"
            for side in lineups["unconfirmedSides"]
        )
        return {**base, "status": BLOCKED_LINEUPS, "eligible": False,
                "reason": (
                    "BOTH official lineups must be confirmed for real-money analysis; "
                    f"unconfirmed: {missing}"
                )}

    if not base["gameId"]:
        return {**base, "status": BLOCKED_DATA, "eligible": False,
                "reason": "game carries no gameId -- markets cannot be attributed to it safely"}

    return {**base, "status": ELIGIBLE, "eligible": True,
            "reason": "not started, both official lineups confirmed, integrity gates pass"}


def partition_slate(slate, *, now_utc=None):
    """
    Pure. Split a slate's games into the real-money candidate set and the
    research-only set, with a reason on every excluded game.

    Both sets are returned. Nothing is dropped: a game that cannot be bet
    is still fully visible, still archived, still researchable -- it is
    simply not a real-money candidate. That distinction is the entire
    point of this function.
    """
    eligible, research_only = [], []
    for game in (slate or {}).get("games", []) or []:
        verdict = classify_game_eligibility(game, now_utc=now_utc)
        (eligible if verdict["eligible"] else research_only).append(verdict)

    by_reason = {}
    for verdict in research_only:
        by_reason[verdict["status"]] = by_reason.get(verdict["status"], 0) + 1

    return {
        "date": (slate or {}).get("date"),
        "gamesTotal": len(eligible) + len(research_only),
        "bettingEligible": eligible,
        "researchOnly": research_only,
        "bettingEligibleCount": len(eligible),
        "researchOnlyCount": len(research_only),
        "researchOnlyByReason": dict(sorted(by_reason.items())),
        "policy": (
            "Only games that have NOT started and have BOTH official lineups confirmed "
            "(lineupConfirmedOfficial on awayTeamStats AND homeTeamStats) are real-money "
            "candidates. Probable/projected lineups are never treated as confirmed. Every "
            "other game stays archived and researchable and must not produce a real-money "
            "recommendation from the normal slate card."
        ),
    }
