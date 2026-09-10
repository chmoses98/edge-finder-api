#!/usr/bin/env python3
"""
lib/edgelab/market_identity.py
==============================
WAVE 1, subwave C. The one canonical answer to "which contract is this, and
which physical baseball game does it belong to".

W1-B2 established what a contract COSTS. This establishes WHICH CONTRACT IT IS.
The two compose and neither rescues the other: a proven price on an unproven
contract is still refused, because knowing the price of something you cannot
name is not knowing anything.

THE CHAIN
---------
    PHYSICAL MLB GAME -> KALSHI EVENT -> EXACT CONTRACT
      -> FAMILY/HORIZON -> SELECTION/TEAM/DIRECTION -> THRESHOLD/STRIKE -> SIDE

Three identity layers, deliberately kept apart because they answer different
questions and have different uniqueness guarantees:

  PhysicalGame  the baseball game. Anchored on the MLB gamePk, which is
                already distinct per physical game -- including per
                doubleheader leg. This is the anchor precisely because it is
                the only identifier in the system that a doubleheader cannot
                collapse.

  ExchangeEvent the Kalshi event. `eventTicker` and the date+time+teams suffix
                it encodes.

  Contract      the exact tradable ticker, plus the semantics needed to say
                what buying it means: family, horizon, selection, direction,
                threshold, side.

WHY A TEAM PAIR IS NOT AN IDENTITY (audit CR-3)
-----------------------------------------------
`kalshiKey` is `f"{away}{home}"`. On 2026-06-17 SF@ATL and 2026-07-11 MIL@PIT
that string named TWO different baseball games. The registry keyed by it, so
the second leg silently overwrote the first; the slate join then returned the
first matching key out of a `set`, so both legs were handed the same markets.
Measured in the archive: `KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4` is attached to
gamePk 823357 AND gamePk 823356. One exact contract, two physical games, at
most one of which can be right.

So this module never accepts a team pair as proof of a physical game, and never
resolves a tie by taking the first candidate. See `docs/W1C_MARKET_IDENTITY_TRACE.md`.

FAIL CLOSED
-----------
Every resolution returns an explicit outcome. There is no "probably". A caller
that cannot prove identity must not price, and must not trade.
"""

from lib.kalshi_ticker_time import closest_by_hhmm, hhmm_distance_minutes

# ── Outcome vocabulary ───────────────────────────────────────────────────────
# One of these is attached to every resolution, and the refusals name what
# could not be PROVEN rather than merely reporting that something was absent.
IDENTITY_PROVEN = "IDENTITY_PROVEN"
IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME = "IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME"
IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH = "IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH"
IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE = "IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE"
IDENTITY_REFUSED_NO_CONTRACT = "IDENTITY_REFUSED_NO_CONTRACT"
IDENTITY_REFUSED_UNKNOWN_FAMILY = "IDENTITY_REFUSED_UNKNOWN_FAMILY"
IDENTITY_REFUSED_TICKER_CLAIMED_BY_ANOTHER_GAME = (
    "IDENTITY_REFUSED_TICKER_CLAIMED_BY_ANOTHER_GAME")
# The caller asserted one family and the ticker's own prefix says another. One
# of the two is wrong and there is no way to tell which, so neither is trusted.
IDENTITY_REFUSED_SERIES_MISMATCH = "IDENTITY_REFUSED_SERIES_MISMATCH"

REFUSALS = (
    IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME,
    IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH,
    IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE,
    IDENTITY_REFUSED_NO_CONTRACT,
    IDENTITY_REFUSED_UNKNOWN_FAMILY,
    IDENTITY_REFUSED_TICKER_CLAIMED_BY_ANOTHER_GAME,
    IDENTITY_REFUSED_SERIES_MISMATCH,
)

# ── Horizons ─────────────────────────────────────────────────────────────────
# The segment of the game a contract settles on. A horizon is never inferred
# from a probability or a price; it comes from the series ticker.
HORIZON_FULL_GAME = "FULL_GAME"
HORIZON_F3 = "F3"
HORIZON_F5 = "F5"
HORIZON_F7 = "F7"
HORIZON_FIRST_INNING = "FIRST_INNING"
HORIZON_PLAYER_PROP = "PLAYER_PROP"

# ── Directions ───────────────────────────────────────────────────────────────
DIRECTION_OVER = "OVER"
DIRECTION_UNDER = "UNDER"
DIRECTION_WIN = "WIN"
DIRECTION_TIE = "TIE"
DIRECTION_EVENT_OCCURS = "EVENT_OCCURS"          # YRFI: "a run scores"
DIRECTION_EVENT_DOES_NOT_OCCUR = "EVENT_DOES_NOT_OCCUR"   # NRFI

SIDE_YES = "YES"
SIDE_NO = "NO"

# ── What each Kalshi series means ────────────────────────────────────────────
# series ticker -> (horizon, family, what must be non-null to be actionable)
#
# The requirement list is the point of this table. A run line without a team or
# a win margin is not "a run line with some fields missing" -- it is two
# different contracts wearing the same label, and there is no safe way to pick
# between them. Anything listed here must be PROVEN, not defaulted.
SERIES_SEMANTICS = {
    "KXMLBGAME":      (HORIZON_FULL_GAME, "MONEYLINE", ("selection",)),
    "KXMLBF3":        (HORIZON_F3, "MONEYLINE", ("selection",)),
    "KXMLBF5":        (HORIZON_F5, "MONEYLINE", ("selection",)),
    "KXMLBF7":        (HORIZON_F7, "MONEYLINE", ("selection",)),
    "KXMLBSPREAD":    (HORIZON_FULL_GAME, "RUN_LINE", ("selection", "threshold", "direction")),
    "KXMLBF5SPREAD":  (HORIZON_F5, "RUN_LINE", ("selection", "threshold", "direction")),
    "KXMLBTOTAL":     (HORIZON_FULL_GAME, "GAME_TOTAL", ("threshold", "direction")),
    "KXMLBF5TOTAL":   (HORIZON_F5, "GAME_TOTAL", ("threshold", "direction")),
    "KXMLBTEAMTOTAL": (HORIZON_FULL_GAME, "TEAM_TOTAL", ("selection", "threshold", "direction")),
    "KXMLBRFI":       (HORIZON_FIRST_INNING, "RFI", ("direction",)),
}

# Player-prop series. Research-only today; listed so an unknown family is
# distinguishable from a known research family.
PLAYER_PROP_SERIES = {
    "KXMLBKS": "PITCHER_STRIKEOUTS", "KXMLBOUTS": "PITCHER_OUTS",
    "KXMLBHIT": "HITTER_HITS", "KXMLBTB": "HITTER_TOTAL_BASES",
    "KXMLBHRR": "HITTER_HITS_RUNS_RBIS", "KXMLBRBI": "HITTER_RBIS",
    "KXMLBSB": "HITTER_STOLEN_BASES",
}


# ── What each decision row means ─────────────────────────────────────────────
# The market ledger evaluates a fixed set of labelled decisions ('ML_Away',
# 'TT_Home_Over', 'NRFI', ...). This table is the ONE place that says what each
# label asserts about the contract underneath it:
#
#     label -> (selection role, direction, side)
#
# The label is the engine's own statement of WHICH SIDE OF WHAT it wants. It is
# emphatically NOT evidence about which physical game or which exact ticker --
# 'ML_Away' names a side, and on a doubleheader date two different baseball
# games both have an away team. The ticker and the gamePk still have to be
# proven separately; this table only removes the need to re-derive the side
# semantics at eight separate call sites, which is how they drift apart.
#
# The selection role resolves to a team abbreviation at the call site, because
# only the caller knows which teams are playing. The threshold is likewise
# supplied by the caller: it is the contract's strike, and it is real data, not
# something a label can imply.
LEDGER_MARKET_SEMANTICS = {
    "ML_Away":      ("away", DIRECTION_WIN, SIDE_YES),
    "ML_Home":      ("home", DIRECTION_WIN, SIDE_YES),
    "RL_Away":      ("away", DIRECTION_OVER, SIDE_YES),
    "RL_Home":      ("home", DIRECTION_OVER, SIDE_YES),
    "Game_Total":   (None, DIRECTION_OVER, SIDE_YES),
    "TT_Away_Over": ("away", DIRECTION_OVER, SIDE_YES),
    "TT_Home_Over": ("home", DIRECTION_OVER, SIDE_YES),
    "F5_ML_Away":   ("away", DIRECTION_WIN, SIDE_YES),
    "F5_ML_Home":   ("home", DIRECTION_WIN, SIDE_YES),
    # One RFI contract, two decisions. KXMLBRFI YES settles true iff a run
    # scores in the first inning, so YRFI is the YES side and NRFI is the NO
    # side of the SAME ticker -- not a separate market. Anything that treats
    # NRFI as a YES buy is buying the opposite of what it means to.
    "YRFI":         (None, DIRECTION_EVENT_OCCURS, SIDE_YES),
    "NRFI":         (None, DIRECTION_EVENT_DOES_NOT_OCCUR, SIDE_NO),
}


def ledger_market_semantics(market):
    """(selection_role, direction, side) for a ledger market label, or None.

    None means this system has no statement of what the label means, which is a
    refusal condition -- never a licence to assume full-game moneyline YES.
    """
    return LEDGER_MARKET_SEMANTICS.get(market)


def series_of(market_ticker):
    """The series prefix of a Kalshi ticker, or None."""
    if not market_ticker or "-" not in str(market_ticker):
        return None
    return str(market_ticker).split("-", 1)[0].strip().upper() or None


def semantics_for(market_ticker):
    """
    (horizon, family, required_fields) for a ticker's series, or None when the
    series is unknown to this system.

    Unknown is NOT a synonym for full-game moneyline. A series nobody has
    written semantics for is a contract nobody can say the meaning of.
    """
    series = series_of(market_ticker)
    if series in SERIES_SEMANTICS:
        return SERIES_SEMANTICS[series]
    if series in PLAYER_PROP_SERIES:
        return (HORIZON_PLAYER_PROP, PLAYER_PROP_SERIES[series], ("selection", "threshold"))
    return None


# ── Physical game ────────────────────────────────────────────────────────────

def physical_game_key(game):
    """
    The authoritative physical-game identity: the MLB gamePk.

    Returned as a string so it can key a dict without int/str ambiguity, and
    None when absent -- callers must refuse rather than fall back to teams.
    """
    if not game:
        return None
    for field in ("gamePk", "mlbGamePk", "gameId"):
        value = game.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def resolve_physical_game(candidates, *, event_time_hhmm=None,
                          doubleheader_game_number=None):
    """
    Choose the ONE physical MLB game a Kalshi event belongs to.

    `candidates` are the MLB games already narrowed to the same date and the
    same away/home teams -- i.e. everything a team-pair key could tell you.
    This function's entire job is what happens NEXT, when that narrowing leaves
    more than one game standing.

    Returns (game_or_None, outcome, evidence_dict).

    Resolution order, strongest evidence first:

      0 candidates  -> IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH
      1 candidate   -> proven; teams+date happened to be unique, which is the
                       ordinary single-game case
      >1 candidates -> a doubleheader. Disambiguate by LEG NUMBER if the event
                       carries one, else by START TIME, and only if the winner
                       is UNIQUELY closest. A tie, or no usable time, refuses.

    There is deliberately no "pick the earliest" and no "pick the first".
    Picking a leg we cannot prove is how the same contract ended up on two
    different baseball games.
    """
    candidates = [c for c in (candidates or []) if c]
    evidence = {
        "candidateCount": len(candidates),
        "candidateGamePks": [physical_game_key(c) for c in candidates],
        "eventTimeHHMM": event_time_hhmm,
        "doubleheaderGameNumber": doubleheader_game_number,
        "basis": None,
    }

    if not candidates:
        return None, IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH, evidence

    if len(candidates) == 1:
        evidence["basis"] = "SINGLE_CANDIDATE_FOR_DATE_AND_TEAMS"
        return candidates[0], IDENTITY_PROVEN, evidence

    # From here on it is a doubleheader (or worse), and teams+date is provably
    # not an identity.
    if doubleheader_game_number is not None:
        matched = [c for c in candidates
                   if _leg_number(c) is not None
                   and int(_leg_number(c)) == int(doubleheader_game_number)]
        if len(matched) == 1:
            evidence["basis"] = "DOUBLEHEADER_GAME_NUMBER"
            return matched[0], IDENTITY_PROVEN, evidence
        # 0 or 2+ matches on an explicit leg number is worse than no leg
        # number at all: the one field that should settle it, did not.

    if event_time_hhmm:
        timed = [c for c in candidates if _start_hhmm(c)]
        if timed:
            best, unique = closest_by_hhmm(event_time_hhmm, timed, key=_start_hhmm)
            if unique:
                evidence["basis"] = "UNIQUE_CLOSEST_SCHEDULED_START"
                evidence["distanceMinutes"] = hhmm_distance_minutes(
                    event_time_hhmm, _start_hhmm(best))
                return best, IDENTITY_PROVEN, evidence
            evidence["basis"] = "TIED_OR_UNPARSEABLE_START_TIMES"

    return None, IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME, evidence


def _leg_number(game):
    for field in ("doubleheaderGameNumber", "gameNumber", "game_number"):
        value = (game or {}).get(field)
        if value is not None and str(value).strip():
            return value
    return None


def _start_hhmm(game):
    """The game's scheduled start as 'HHMM', or None. Never guessed."""
    game = game or {}
    for field in ("scheduledTimeStr", "time_str", "kalshiGameTimeStr"):
        value = game.get(field)
        if value and len(str(value).strip()) == 4 and str(value).strip().isdigit():
            return str(value).strip()
    start = game.get("scheduledStartTime") or game.get("gameDate")
    if start and "T" in str(start):
        clock = str(start).split("T", 1)[1]
        digits = clock.replace(":", "")[:4]
        if len(digits) == 4 and digits.isdigit():
            return digits
    return None


# ── Contract ─────────────────────────────────────────────────────────────────

def resolve_contract(market_ticker, *, selection=None, direction=None,
                     threshold=None, side=None, expected_series=None):
    """
    Prove what buying `market_ticker` actually means.

    Returns (identity_dict, outcome). The identity is complete or the outcome
    is a refusal -- there is no partially-identified contract, because a
    partially-identified contract is one you can trade by accident.

    `threshold` of 0 is a real strike and is preserved; only None is missing.

    `expected_series` is the family the CALLER believes it is pricing. When it
    is supplied and disagrees with the ticker's own prefix, one of the two is
    wrong and nothing here can say which, so the contract is refused rather
    than silently resolved in favour of either.
    """
    identity = {
        "marketTicker": market_ticker,
        "seriesTicker": series_of(market_ticker),
        "horizon": None, "family": None,
        "selection": selection, "direction": direction,
        "threshold": threshold, "side": side,
        "missing": [],
    }

    if not market_ticker:
        return identity, IDENTITY_REFUSED_NO_CONTRACT

    if expected_series and series_of(market_ticker) != str(expected_series).strip().upper():
        identity["expectedSeriesTicker"] = str(expected_series).strip().upper()
        return identity, IDENTITY_REFUSED_SERIES_MISMATCH

    semantics = semantics_for(market_ticker)
    if semantics is None:
        return identity, IDENTITY_REFUSED_UNKNOWN_FAMILY

    horizon, family, required = semantics
    identity["horizon"] = horizon
    identity["family"] = family

    supplied = {"selection": selection, "direction": direction,
                "threshold": threshold}
    missing = [f for f in required if supplied.get(f) is None]

    # The side is required for every family without exception: it is the
    # difference between buying and selling the same event.
    if side not in (SIDE_YES, SIDE_NO):
        missing.append("side")

    identity["missing"] = missing
    if missing:
        return identity, IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE
    return identity, IDENTITY_PROVEN


def is_proven(outcome):
    return outcome == IDENTITY_PROVEN


# ── Ticker exclusivity ───────────────────────────────────────────────────────

def assert_ticker_exclusivity(assignments):
    """
    A Kalshi contract belongs to exactly ONE physical game.

    `assignments` is an iterable of (market_ticker, physical_game_key). Returns
    a list of violations -- each a ticker claimed by two or more distinct
    gamePks. Empty means the exclusivity invariant holds.

    This is the direct, checkable form of the CR-3 defect: in the archived
    2026-07-11 slate, `KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4` is claimed by
    gamePk 823357 and by gamePk 823356. Whatever else identity resolution
    does, that must become impossible.
    """
    claims = {}
    for ticker, game_key in assignments:
        if not ticker or not game_key:
            continue
        claims.setdefault(str(ticker), set()).add(str(game_key))
    return [{"marketTicker": t, "gamePks": sorted(g)}
            for t, g in sorted(claims.items()) if len(g) > 1]


def registry_key(*, game_key=None, event_ticker_suffix=None):
    """
    The key authoritative registry state may be stored under.

    Prefers the physical gamePk; falls back to the FULL Kalshi event suffix
    (`26JUL111605MILPIT`), which encodes date, start time and teams and is
    therefore distinct per doubleheader leg.

    What it will never return is a bare team pair. `f"{away}{home}"` is the key
    that let one leg overwrite the other, and the start time it discarded was
    parsed one line earlier.
    """
    if game_key:
        return "gamePk:%s" % game_key
    if event_ticker_suffix:
        return "event:%s" % event_ticker_suffix
    return None
