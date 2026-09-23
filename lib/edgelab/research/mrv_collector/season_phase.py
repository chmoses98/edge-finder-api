"""
Season-phase / game-type provenance for the MRV collector.

The raw upstream field is the MLB Stats API `gameType` code (schedule:
games[].gameType; live feed: gameData.game.type).  Codes and their meaning
are MLB's own (GET /api/v1/gameTypes):
  R  Regular Season
  F  Wild Card Game   D  Division Series   L  League Championship Series
  W  World Series     C  Championship      P  Playoffs
  S  Spring Training  E  Exhibition        A  All-Star Game
  I  Intrasquad       N  Nineteenth Century Series

Coarse phase, documented and never guessed:
  REGULAR_SEASON    gameType == "R"
  POSTSEASON        gameType in {F, D, L, W, C, P}
  OTHER_OR_UNKNOWN  any other code, a missing code, or schedule and feed
                    codes that disagree

FROZEN SCIENTIFIC RULE (MRV_SEASON_PHASE_RULE_V1_2026_09_23):
  Regular-season inferential MRV research requires its minimum game / date /
  contract sample ENTIRELY from REGULAR_SEASON observations.  POSTSEASON
  observations are a separate regime/stratum: collected and preserved, but
  they never satisfy the regular-season readiness gate.  Regular season and
  postseason are not pooled in any inferential analysis unless a hypothesis /
  specification predeclares cross-regime pooling or stratification before the
  result is examined.  OTHER_OR_UNKNOWN never counts toward either sample.
"""

SEASON_PHASE_RULE_VERSION = "MRV_SEASON_PHASE_RULE_V1_2026_09_23"
REGULAR_SEASON = "REGULAR_SEASON"
POSTSEASON = "POSTSEASON"
OTHER_OR_UNKNOWN = "OTHER_OR_UNKNOWN"
REGULAR_CODES = frozenset({"R"})
POSTSEASON_CODES = frozenset({"F", "D", "L", "W", "C", "P"})


def phase_for_code(code):
    if not isinstance(code, str) or not code.strip():
        return OTHER_OR_UNKNOWN
    c = code.strip().upper()
    if c in REGULAR_CODES:
        return REGULAR_SEASON
    if c in POSTSEASON_CODES:
        return POSTSEASON
    return OTHER_OR_UNKNOWN


def resolve(schedule_code, feed_code=None):
    """
    -> {gameType, gameTypeSchedule, gameTypeFeed, seasonPhase, phaseBasis}
    The schedule code is primary; the feed code confirms it.  A disagreement
    or an absent code yields OTHER_OR_UNKNOWN, never a guess.
    """
    s = schedule_code if isinstance(schedule_code, str) and schedule_code.strip() else None
    f = feed_code if isinstance(feed_code, str) and feed_code.strip() else None
    if s and f and s.strip().upper() != f.strip().upper():
        return {"gameType": None, "gameTypeSchedule": s, "gameTypeFeed": f, "seasonPhase": OTHER_OR_UNKNOWN,
                "phaseBasis": "SCHEDULE_FEED_CONFLICT"}
    code = s or f
    return {"gameType": code, "gameTypeSchedule": s, "gameTypeFeed": f, "seasonPhase": phase_for_code(code),
            "phaseBasis": ("SCHEDULE" if s else ("FEED" if f else "ABSENT"))}
