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
# The canonical Kalshi MLB contract parser -- the module that already owns
# "what does this ticker say". W1-C's final correction extends IT with the
# market-suffix condition rather than growing a second ticker parser in here:
# two parsers is how the exchange's meaning and our reading of it drift apart,
# and that drift is the whole defect class this subwave exists to close.
from lib import kalshi_mlb_contract_parser as kmcp

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
# A POSITIVE CONTRADICTION, not an absence: the exact market ticker encodes one
# Kalshi event, and the event this physical game resolved to is a different
# one. Deliberately NOT folded into NO_CONTRACT or AMBIGUOUS -- "we could not
# tell" and "we can tell, and it is wrong" need different names or the second
# hides inside the first.
IDENTITY_REFUSED_EVENT_GAME_MISMATCH = "IDENTITY_REFUSED_EVENT_GAME_MISMATCH"
# No event could be bound to this physical game at all, so there is nothing for
# a ticker to agree or disagree with.
IDENTITY_REFUSED_NO_EVENT_FOR_GAME = "IDENTITY_REFUSED_NO_EVENT_FOR_GAME"
# A POSITIVE CONTRADICTION about MEANING: the exchange's own contract says one
# thing and the caller claims another -- the wrong team, the wrong strike, the
# wrong direction, or a side that buys the opposite of what was intended.
# Deliberately NOT folded into NO_CONTRACT, UNKNOWN_FAMILY or the generic
# INCOMPLETE: those all say "something is missing", and the whole point here is
# that nothing is missing. Every field was populated. They were populated wrong.
IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH = "IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH"
# The exchange side could not be read at all, so there is nothing to check the
# claim against. An absence, not a contradiction -- and still a refusal, because
# a contract whose meaning cannot be established is not one to trade.
IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE = (
    "IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE")
# This system has never written down what a contract in this series MEANS. Kept
# apart from UNPARSEABLE ("the grammar exists and this string does not fit it")
# because the two have different fixes: one is a data problem, the other is a
# piece of work nobody has done. Neither is PROVEN.
IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNDESCRIBED = (
    "IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNDESCRIBED")

REFUSALS = (
    IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME,
    IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH,
    IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE,
    IDENTITY_REFUSED_NO_CONTRACT,
    IDENTITY_REFUSED_UNKNOWN_FAMILY,
    IDENTITY_REFUSED_TICKER_CLAIMED_BY_ANOTHER_GAME,
    IDENTITY_REFUSED_SERIES_MISMATCH,
    IDENTITY_REFUSED_EVENT_GAME_MISMATCH,
    IDENTITY_REFUSED_NO_EVENT_FOR_GAME,
    IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH,
    IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE,
    IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNDESCRIBED,
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
    # Direction is required even here, where a moneyline has no strike and the
    # side looks like it says everything. It does not: `side` is the trade, and
    # `direction` is the claim the trade is supposed to express. With no
    # direction stated there is nothing for the side to be checked AGAINST, and
    # an unchecked side is how a contract gets bought from the wrong end.
    "KXMLBGAME":      (HORIZON_FULL_GAME, "MONEYLINE", ("selection", "direction")),
    "KXMLBF3":        (HORIZON_F3, "MONEYLINE", ("selection", "direction")),
    "KXMLBF5":        (HORIZON_F5, "MONEYLINE", ("selection", "direction")),
    "KXMLBF7":        (HORIZON_F7, "MONEYLINE", ("selection", "direction")),
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


# ── Reconciling the ledger's numbers with the exchange's ─────────────────────
#
# The ledger and Kalshi both say "4" and mean different things, per family, and
# comparing the numerals directly would call two different contracts the same
# one. This table is the ONE statement of what each family's ledger threshold
# is denominated in, and it is derived from the merge that produces it:
#
#   GAME_TOTAL  kalshi.total.line       = registry `total`       = the integer N
#   TEAM_TOTAL  kalshi.team_totals.*.line = registry `over_n`    = the integer N
#   RUN_LINE    kalshi.rl.wins_by_over  = registry `win_by_over` = N - 0.5
#
# So a run line claiming 1.5 and a ticker ending -BOS2 are THE SAME CONTRACT,
# and a team total claiming 1.5 against -BOS2 is not. Both facts are invisible
# to a numeric comparison, which is why there is not one.
MINIMUM_INCLUSIVE = "MINIMUM_INCLUSIVE"          # the number IS the strike
HALF_POINT_BELOW = "HALF_POINT_BELOW"            # the number is strike - 0.5

LEDGER_THRESHOLD_CONVENTION = {
    "GAME_TOTAL": MINIMUM_INCLUSIVE,
    "TEAM_TOTAL": MINIMUM_INCLUSIVE,
    "RUN_LINE": HALF_POINT_BELOW,
}

# Which of the two things a direction asserts about the contract's own YES
# condition: that it HOLDS, or that it does NOT. The side then has to agree --
# asserting a condition holds while buying NO is buying the opposite trade, and
# that is the NRFI/YRFI reversal in its general form.
_DIRECTION_ASSERTS_CONDITION = {
    DIRECTION_WIN: True,
    DIRECTION_TIE: True,
    DIRECTION_OVER: True,
    DIRECTION_EVENT_OCCURS: True,
    DIRECTION_UNDER: False,
    DIRECTION_EVENT_DOES_NOT_OCCUR: False,
}

# family -> the exchange condition a claim in that family must be describing.
# A MONEYLINE claim that lands on a TIE contract, or a TEAM_TOTAL claim that
# lands on a margin contract, is a different trade wearing the right label.
_FAMILY_CONDITION = {
    "MONEYLINE": kmcp.CONDITION_TEAM_WINS,
    "RUN_LINE": kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST,
    "GAME_TOTAL": kmcp.CONDITION_COMBINED_RUNS_AT_LEAST,
    "TEAM_TOTAL": kmcp.CONDITION_TEAM_RUNS_AT_LEAST,
    "RFI": kmcp.CONDITION_FIRST_INNING_RUNS_AT_LEAST,
}


def normalize_ledger_claim(family, *, selection=None, direction=None,
                           threshold=None, side=None):
    """
    The ledger's claim, restated in the exchange's own terms.

    Returns a dict with `condition`, `selection`, `minimumInclusive`,
    `assertsCondition` and `side`, or `None` for `condition` when this system
    has no statement of what a claim in that family describes.

    `minimumInclusive` is None when the family has no strike (moneyline, RFI)
    and also when the supplied threshold cannot be converted under the family's
    own convention -- a run line claiming a whole number, say, which is not a
    "wins by over X.5" line at all.
    """
    claim = {
        "condition": _FAMILY_CONDITION.get(family),
        "selection": selection,
        "minimumInclusive": None,
        "direction": direction,
        "assertsCondition": _DIRECTION_ASSERTS_CONDITION.get(direction),
        "side": side,
        "thresholdConvention": LEDGER_THRESHOLD_CONVENTION.get(family),
        # Moneyline has no strike, and RFI's ("at least one run") is carried by
        # the condition itself rather than by a number the caller supplies. For
        # those families the threshold is not part of the claim, and comparing
        # one would manufacture a disagreement out of a field that correctly
        # does not exist.
        "thresholdIsPartOfClaim": family in LEDGER_THRESHOLD_CONVENTION,
    }
    if threshold is None:
        return claim
    convention = claim["thresholdConvention"]
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        return claim
    if convention == MINIMUM_INCLUSIVE:
        minimum = value
    elif convention == HALF_POINT_BELOW:
        minimum = value + 0.5
    else:
        return claim
    # A strike is a count of runs. Anything that does not land on a whole
    # number under its own family's convention is not a rung of this ladder,
    # and rounding it to the nearest one would be inventing a contract.
    if abs(minimum - round(minimum)) > 1e-9:
        return claim
    claim["minimumInclusive"] = int(round(minimum))
    return claim


def compare_contract_claim(parsed, claim):
    """
    Where does the caller's claim disagree with the exchange's contract?

    Returns a list of disagreements, each naming the field, what the caller
    claimed and what the contract says. Empty means every element of the claim
    is the contract's own.
    """
    disagreements = []

    def note(field, claimed, parsed_value):
        disagreements.append({"field": field, "claimed": claimed,
                              "parsed": parsed_value})

    if claim.get("condition") != parsed.get("condition"):
        note("condition", claim.get("condition"), parsed.get("condition"))

    # A team the contract does not name, or a team named where the contract
    # names none (and the reverse), are both wrong answers.
    claimed_selection = claim.get("selection")
    parsed_selection = parsed.get("selection")
    if (claimed_selection or None) != (parsed_selection or None):
        note("selection", claimed_selection, parsed_selection)

    if claim.get("thresholdIsPartOfClaim"):
        if claim.get("minimumInclusive") != parsed.get("minimumInclusive"):
            note("threshold", claim.get("minimumInclusive"),
                 parsed.get("minimumInclusive"))

    asserts = claim.get("assertsCondition")
    if asserts is None:
        # A direction this system has no reading of. It cannot be checked
        # against anything, so it cannot be trusted.
        note("direction", claim.get("direction"), None)
    else:
        expected_side = SIDE_YES if asserts else SIDE_NO
        if claim.get("side") != expected_side:
            note("side", claim.get("side"), expected_side)

    return disagreements


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
        if any(_leg_number(c) is not None for c in candidates):
            # The candidates DO state their legs and none of them is the leg we
            # were asked for (or two claim to be). That is a contradiction, not
            # an absence, and falling through to start times would resolve it by
            # ignoring the field that disagreed.
            evidence["basis"] = "LEG_NUMBER_STATED_AND_DOES_NOT_MATCH"
            return None, IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME, evidence
        # No candidate states a leg at all, so there is nothing to contradict:
        # fall through to the start-time evidence below.

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
    """
    The game's scheduled start as an **Eastern** 'HHMM', or None. Never guessed.

    THE TIME ZONE IS THE WHOLE POINT. Kalshi encodes the event time in the
    ticker in EASTERN local time -- `KXMLBGAME-26JUN171915SFATL` is 7:15 PM ET
    -- while MLB's `startTime` is UTC. Comparing the two clocks directly is not
    an off-by-a-bit inaccuracy, it silently names the wrong doubleheader leg:

        SF@ATL 2026-06-17, legs at 18:00Z (2:00 PM ET) and 23:15Z (7:15 PM ET).
        Kalshi's event says '1915'. Read as UTC, 1915 is 75 minutes from 1800
        and 240 from 2315, so the FIRST leg wins -- confidently, uniquely, and
        wrongly. Read as ET, it is 0 minutes from the second leg, which is the
        game Kalshi actually listed.

    An earlier revision of this function did exactly that. It is the same class
    of defect as CR-3 itself: a resolver that produces a unique answer from
    evidence it has misread is more dangerous than one that refuses.

    A bare 4-digit field is already Eastern by this repository's convention
    (`lib/kalshi_ticker_time`'s contract). An ISO timestamp is converted.
    """
    game = game or {}
    for field in ("scheduledTimeStr", "time_str", "kalshiGameTimeStr"):
        value = game.get(field)
        if value and len(str(value).strip()) == 4 and str(value).strip().isdigit():
            return str(value).strip()
    # `startTime` is what data/slate.json actually calls it, and it is the
    # field the archived slates carry. Omitting it meant the resolver could not
    # read a real production game's start at all -- every doubleheader would
    # have refused for lack of evidence that was sitting right there.
    return _et_hhmm(game.get("scheduledStartTime") or game.get("startTime")
                    or game.get("gameDate") or game.get("commence_time")
                    or game.get("oddsApiCommenceTime"))


def _et_hhmm(iso_ts):
    """
    An ISO timestamp -> Eastern 'HHMM', or None when it cannot be parsed.

    A timestamp with no offset is read as UTC, matching what
    `scripts/discover_kalshi_mlb_markets._et_time_str` and
    `scripts/build_hitter_projection_board._et_time_str` -- two existing copies
    of this conversion -- already do. Those live on the discovery and hitter
    board paths; unifying all three is W1-D's job, not this subwave's, so this
    deliberately matches their behaviour rather than diverging from it.
    """
    if not iso_ts:
        return None
    from datetime import datetime, timedelta, timezone
    try:
        parsed = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        parsed = parsed.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # No tz database available. -4 is Eastern Daylight Time, which covers
        # the entire MLB regular season; this branch is a fallback for a
        # stripped runtime, not the normal path.
        parsed = parsed.astimezone(timezone(timedelta(hours=-4)))
    return parsed.strftime("%H%M")


# ── The event ↔ physical game binding ────────────────────────────────────────
#
# `resolve_physical_game` answers "which game does this event belong to".
# Production needs the same relation read the other way -- "which of these
# events is THIS game's" -- and it needs the answer to be the SAME relation,
# not a second implementation that can disagree with the first. Both live here.
#
# This is the binding the rest of the system was missing. Proving that a valid
# gamePk exists and separately proving that a valid contract exists does not
# prove the contract belongs to the game: a ticker from the OTHER leg, appearing
# on one game only, satisfies both halves and no exclusivity check can see it,
# because the ticker is claimed exactly once.

def event_suffix_of(ticker):
    """
    The event suffix a market or event ticker encodes, or None.

    `KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4` -> `26JUL111605MILPIT`. That string
    carries the date, the EASTERN start time and both teams, so it is distinct
    per doubleheader leg -- which is exactly why it, and not the team pair, is
    what a contract can be bound to.

    W1-C micro-fix 2: this used to carry its OWN regex, which permitted only
    alphabetic characters after the HHMM. Kalshi's explicit doubleheader marker
    is a DIGIT (`...1305BOSNYYG1`), so every marked leg failed to match here --
    in the one module whose entire job is telling doubleheader legs apart, and
    while the canonical parser three imports away read the marker correctly.
    It now delegates to that parser, so there is one reading of a suffix.
    """
    if not ticker:
        return None
    for part in str(ticker).split("-")[1:]:
        candidate = part.strip().upper()
        if kmcp.parse_raw_event_suffix(candidate)["parsed"]:
            return candidate
    return None


def event_game_number(event_or_ticker):
    """The doubleheader leg number Kalshi itself states, or None.

    Reads it from the event suffix, which is where the exchange publishes it.
    `None` means Kalshi did not say -- never "leg 1".
    """
    if not event_or_ticker:
        return None
    if isinstance(event_or_ticker, dict):
        explicit = _leg_number(event_or_ticker)
        if explicit is not None:
            return int(explicit)
        suffix = (event_or_ticker.get("event_ticker_suffix")
                  or event_or_ticker.get("eventTickerSuffix"))
    else:
        suffix = event_suffix_of(event_or_ticker) or event_or_ticker
    return kmcp.parse_raw_event_suffix(suffix)["game_number"] if suffix else None


def event_hhmm(event):
    """The Eastern 'HHMM' an event declares, from its own field or its suffix."""
    event = event or {}
    value = event.get("time_str")
    if value and len(str(value).strip()) == 4 and str(value).strip().isdigit():
        return str(value).strip()
    suffix = event.get("event_ticker_suffix") or event.get("eventTickerSuffix")
    if suffix and len(str(suffix)) >= 11:
        digits = str(suffix)[7:11]
        if digits.isdigit():
            return digits
    return None


def resolve_event_for_game(game, event_candidates, *, sibling_games=None,
                           doubleheader_game_number=None):
    """
    Which ONE Kalshi event belongs to this physical MLB game?

    `event_candidates` are the registry events already narrowed to the same
    date and teams -- everything a team-pair key could tell you.
    `sibling_games` are the MLB games on that same date with those same teams,
    INCLUDING this one. Both are needed, and the reason is the whole point:

    ONE CANDIDATE IS NOT AUTOMATICALLY A MATCH. On 2026-07-11 only one leg's
    Kalshi event survives in the archive, and both physical games still carry
    the same team pair. A resolver that proved "the only candidate" would hand
    that single event to BOTH legs -- which is the original defect wearing the
    new code's clothes. What has to be unique is not the number of candidates
    but the MAPPING.

    So on a doubleheader the question is asked the other way round, through the
    same relation `resolve_physical_game` already implements: each event is
    resolved to a game, and this game gets an event only if exactly one event
    resolves to IT.

    Returns (event_or_None, outcome, evidence).

      0 candidates                -> IDENTITY_REFUSED_NO_EVENT_FOR_GAME
      1 sibling game, 1 candidate -> proven; the ordinary single-game case
      otherwise                   -> a unique event->this-game mapping, or
                                     REFUSE

    There is deliberately no "pick the earliest" and no "pick the first".
    """
    candidates = [c for c in (event_candidates or []) if c]
    siblings = [s for s in (sibling_games or []) if s]
    game_key = physical_game_key(game)
    game_hhmm = _start_hhmm(game)
    evidence = {
        "candidateCount": len(candidates),
        "candidateEventSuffixes": [
            (c.get("event_ticker_suffix") or c.get("eventTickerSuffix"))
            for c in candidates],
        "siblingGameCount": len(siblings),
        "gameStartHHMM": game_hhmm,
        "gamePk": game_key,
        "basis": None,
    }

    if not candidates:
        return None, IDENTITY_REFUSED_NO_EVENT_FOR_GAME, evidence

    if doubleheader_game_number is not None:
        matched = [c for c in candidates
                   if _leg_number(c) is not None
                   and int(_leg_number(c)) == int(doubleheader_game_number)]
        if len(matched) == 1:
            evidence["basis"] = "DOUBLEHEADER_GAME_NUMBER"
            return matched[0], IDENTITY_PROVEN, evidence

    # The ordinary slate: one game with these teams on this date, and one event
    # for it. Nothing to disambiguate, and this is what production has always
    # done for a single game.
    if len(candidates) == 1 and len(siblings) <= 1:
        evidence["basis"] = "SINGLE_EVENT_AND_SINGLE_GAME_FOR_DATE_AND_TEAMS"
        return candidates[0], IDENTITY_PROVEN, evidence

    # A doubleheader on either side. Resolve each event to a game and keep the
    # events that land on THIS one.
    if len(siblings) > 1 and game_key:
        mine = []
        contradictions = []
        for candidate in candidates:
            owner, basis, conflict = _owner_of_event(candidate, siblings)
            if conflict:
                contradictions.append(conflict)
                continue
            if owner is not None and physical_game_key(owner) == game_key:
                evidence["basis"] = basis
                mine.append(candidate)
        if contradictions:
            # A POSITIVE CONTRADICTION between the two strongest pieces of leg
            # evidence there are: Kalshi's own G1/G2 marker and the MLB start
            # times. Falling back to closest-time here would be choosing the
            # weaker evidence precisely because the stronger one disagreed with
            # it, which is how a confident wrong answer gets produced.
            evidence["basis"] = "LEG_NUMBER_CONTRADICTS_PHYSICAL_EVIDENCE"
            evidence["legNumberContradictions"] = contradictions
            return None, IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME, evidence
        if len(mine) == 1:
            evidence["basis"] = evidence["basis"] or "UNIQUE_EVENT_RESOLVING_TO_THIS_GAME"
            evidence["doubleheaderGameNumber"] = event_game_number(mine[0])
            evidence["distanceMinutes"] = (
                hhmm_distance_minutes(game_hhmm, event_hhmm(mine[0]))
                if game_hhmm else None)
            return mine[0], IDENTITY_PROVEN, evidence
        evidence["basis"] = ("NO_EVENT_RESOLVES_TO_THIS_GAME" if not mine
                             else "MULTIPLE_EVENTS_RESOLVE_TO_THIS_GAME")
        return None, IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME, evidence

    # More than one event but only one known game (or no gamePk): fall back to
    # the game's own start being uniquely closest to one event's.
    if game_hhmm:
        timed = [c for c in candidates if event_hhmm(c)]
        if timed:
            best, unique = closest_by_hhmm(game_hhmm, timed, key=event_hhmm)
            if unique:
                evidence["basis"] = "UNIQUE_CLOSEST_EVENT_START"
                evidence["distanceMinutes"] = hhmm_distance_minutes(
                    game_hhmm, event_hhmm(best))
                return best, IDENTITY_PROVEN, evidence
            evidence["basis"] = "TIED_OR_UNPARSEABLE_EVENT_START_TIMES"

    return None, IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME, evidence


def _leg_ranked_siblings(siblings):
    """Sibling games ordered by start, or None when the order is not provable.

    "Game 1" and "game 2" of a doubleheader are the earlier and later games --
    MLB's own convention. That ordering is only usable when EVERY sibling has a
    readable start and no two share one; otherwise there is no rank to speak of
    and this returns None rather than an arbitrary order.
    """
    starts = [_start_hhmm(s) for s in siblings]
    if any(s is None for s in starts) or len(set(starts)) != len(starts):
        return None
    return [game for _start, game in sorted(zip(starts, siblings),
                                            key=lambda pair: pair[0])]


def _owner_of_event(candidate, siblings):
    """
    Which sibling game does this event belong to?

    Returns `(game_or_None, basis, contradiction_or_None)`.

    Two independent sources of leg evidence are consulted:

      * Kalshi's own G1/G2 marker, which the exchange publishes in the event
        suffix. This is the strongest statement available -- it is the venue
        saying which leg it listed -- and it is matched against a sibling's own
        stated leg number when there is one, else against the start-time rank.

      * the event's HHMM against the games' starts, which is what production
        has always used.

    When both are determinable and they name DIFFERENT games, that is a
    contradiction and the caller must refuse. The marker can therefore only ever
    remove an answer or supply one where time could not -- it can never quietly
    move a contract from the game the clock says to another one.
    """
    marker = event_game_number(candidate)
    by_marker = None
    if marker is not None:
        stated = [s for s in siblings if _leg_number(s) is not None
                  and int(_leg_number(s)) == int(marker)]
        if len(stated) == 1:
            by_marker = stated[0]
        elif not stated:
            ranked = _leg_ranked_siblings(siblings)
            if ranked and 1 <= int(marker) <= len(ranked):
                by_marker = ranked[int(marker) - 1]

    by_time = None
    hhmm = event_hhmm(candidate)
    if hhmm:
        owner, outcome, _ = resolve_physical_game(siblings, event_time_hhmm=hhmm)
        if is_proven(outcome):
            by_time = owner

    if by_marker is not None and by_time is not None:
        if physical_game_key(by_marker) != physical_game_key(by_time):
            return None, None, {
                "eventTickerSuffix": (candidate.get("event_ticker_suffix")
                                      or candidate.get("eventTickerSuffix")),
                "doubleheaderGameNumber": marker,
                "gamePkByLegNumber": physical_game_key(by_marker),
                "gamePkByStartTime": physical_game_key(by_time),
            }
        return by_marker, "DOUBLEHEADER_GAME_NUMBER_AND_START_AGREE", None

    if by_marker is not None:
        return by_marker, "DOUBLEHEADER_GAME_NUMBER", None
    return by_time, "UNIQUE_EVENT_RESOLVING_TO_THIS_GAME", None


def ticker_belongs_to_event(market_ticker, event_suffix):
    """
    Does this exact contract belong to this exact Kalshi event?

    None when either side is unknown -- the caller must treat that as
    unproven, never as agreement. False is a positive contradiction.
    """
    if not market_ticker or not event_suffix:
        return None
    ticker_suffix = event_suffix_of(market_ticker)
    if not ticker_suffix:
        return None
    return ticker_suffix == str(event_suffix).strip().upper()


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

    # ── THE CLAIM CHECK ──────────────────────────────────────────────────────
    # Everything above proves the CALLER FILLED IN THE REQUIRED FIELDS. That is
    # not the same statement as "the exact exchange contract says these fields",
    # and the gap between them is a tradable one: a caller could name ticker
    # ...-MIL4 while claiming selection PIT, threshold 7, direction OVER and
    # satisfy every completeness rule above. Nothing is missing there. The
    # fields are simply not this contract's.
    #
    # So the exchange's own contract is parsed and the claim is compared to it,
    # in normalized terms -- because "4+", "over 4" and "over 3.5" are three
    # different statements and the ledger's families do not all use the same one.
    parsed = kmcp.parse_contract_condition(market_ticker)
    identity["contractCondition"] = parsed.get("condition")
    identity["contractConditionEvidence"] = parsed.get("evidence")
    identity["parsedSelection"] = parsed.get("selection")
    identity["parsedMinimumInclusive"] = parsed.get("minimumInclusive")

    if parsed.get("parseStatus") == kmcp.PARSE_STATUS_SERIES_NOT_DESCRIBED:
        # A series this system states no contract grammar for -- today, the
        # research-only player props. Inventing a grammar for them just to make
        # them pass would be guessing, so they are REFUSED.
        #
        # An earlier revision returned IDENTITY_PROVEN here with
        # `contractClaimVerified: False`, which is a contradiction in one
        # object: PROVEN has to mean the claim was actually proven, or the word
        # stops carrying information and every reader downstream has to know
        # about an exception. Research-only contracts stay archived,
        # researchable and unsettled -- they simply do not get to be called
        # proven. If a future "partial identity" is ever wanted, it gets its own
        # status; PROVEN is not overloaded.
        identity["contractClaimVerified"] = False
        identity["contractClaimUnverifiedReason"] = "SERIES_NOT_DESCRIBED"
        return identity, IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNDESCRIBED

    if parsed.get("parseStatus") != kmcp.PARSE_STATUS_PARSED:
        identity["contractClaimVerified"] = False
        identity["contractClaimUnverifiedReason"] = parsed.get("reason")
        return identity, IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE

    claim = normalize_ledger_claim(family, selection=selection,
                                   direction=direction, threshold=threshold,
                                   side=side)
    identity["claimedMinimumInclusive"] = claim.get("minimumInclusive")
    identity["thresholdConvention"] = claim.get("thresholdConvention")
    disagreements = compare_contract_claim(parsed, claim)
    if disagreements:
        identity["contractClaimVerified"] = False
        identity["contractClaimMismatch"] = disagreements
        return identity, IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH

    identity["contractClaimVerified"] = True
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
