#!/usr/bin/env python3
"""
lib/edgelab/decision_side.py
============================
WAVE 1, subwave B1. Which side of a Kalshi contract does a decision buy?

FAIL CLOSED ON AMBIGUITY. This module answers YES, NO, or "I cannot prove it",
and the third answer is a first-class result, not a fallback. An earlier draft
of the B1 shadow report did this instead:

    return YES, "assumed_yes_no_side_field_in_record"

which is exactly the wrong shape for a money path. `side` is null on 100% of
144,937 archived model-evaluation rows, so that assumption was firing on every
single row while reading as though it fired on none of them. Assuming YES on a
decision that actually buys NO does not merely mislabel it -- it prices it at
the wrong end of the book, and on a 30c contract that is a 40-cent error.

WHY A DECISION'S SIDE IS RECOVERABLE AT ALL
-------------------------------------------
Every Kalshi binary contract has exactly one YES meaning, and the observation
archive records it: `title`, `team`, `outcomeLabel`, `comparisonOperator` and
`threshold`. Every decision record names what the model wants to buy, in
`selection`. The side is then a comparison, not a guess:

    selection means the same event as the contract's YES   -> buy YES
    selection means the exact complement of the YES        -> buy NO
    anything else                                          -> refuse

"Exact complement" is doing real work in that middle line and is applied only
where the archive proves the event space is binary. See RULE 4 and RULE 6.

SCOPE. This decides which side the B1 price comparison is PRICING. It is not
W1-A's settlement `get_betside` mission and shares no code with it: nothing here
grades a wager, resolves an outcome, or touches a ledger.
"""

# Sides, re-exported so callers need only this module.
SIDE_YES = "YES"
SIDE_NO = "NO"

# How a side was established. Every resolved side carries exactly one.
BASIS_DECLARED = "DECLARED_ON_RECORD"
BASIS_TITLE_IDENTITY = "SELECTION_EQUALS_CONTRACT_YES_TITLE"
BASIS_TEAM_MATCHES_CONTRACT = "SELECTION_TEAM_EQUALS_CONTRACT_TEAM"
BASIS_MONEYLINE_COMPLEMENT = "SELECTION_IS_THE_STRICT_COMPLEMENT_OF_A_FULL_GAME_MONEYLINE"
BASIS_FIRST_INNING_RUN_YES = "SELECTION_YRFI_EQUALS_CONTRACT_YES_A_RUN_SCORES"
BASIS_FIRST_INNING_RUN_NO = "SELECTION_NRFI_IS_THE_STRICT_COMPLEMENT_OF_A_RUN_SCORING"
BASIS_TEAM_TOTAL_SAME_LINE = "SELECTION_TEAM_AND_LINE_EQUAL_THE_CONTRACTS_OVER_LINE"

VALID_BASES = (
    BASIS_DECLARED, BASIS_TITLE_IDENTITY,
    BASIS_TEAM_MATCHES_CONTRACT, BASIS_MONEYLINE_COMPLEMENT,
    BASIS_FIRST_INNING_RUN_YES, BASIS_FIRST_INNING_RUN_NO,
    BASIS_TEAM_TOTAL_SAME_LINE,
)

# Refusals. Each names what could not be proven, never merely "unknown".
REFUSE_NO_SELECTION = "SIDE_UNPROVEN_DECISION_NAMES_NO_SELECTION"
REFUSE_NO_CONTRACT = "SIDE_UNPROVEN_NO_CONTRACT_SEMANTICS_AVAILABLE"
REFUSE_NO_DIRECTION = "SIDE_UNPROVEN_SELECTION_CARRIES_NO_OVER_UNDER_DIRECTION"
REFUSE_SEGMENT_NOT_BINARY = "SIDE_UNPROVEN_SEGMENT_CAN_TIE_SO_THE_SIBLING_IS_NOT_A_COMPLEMENT"
REFUSE_TEAM_UNKNOWN = "SIDE_UNPROVEN_CANNOT_NAME_THE_TEAM_THIS_SELECTION_BACKS"
REFUSE_TEAM_MISMATCH = "SIDE_UNPROVEN_SELECTION_TEAM_IS_NOT_THIS_CONTRACTS_TEAM"
REFUSE_LINE_MISMATCH = "SIDE_UNPROVEN_SELECTION_LINE_IS_NOT_THIS_CONTRACTS_LINE"
REFUSE_OPERATOR = "SIDE_UNPROVEN_CONTRACT_IS_NOT_AN_OVER_CONTRACT"
REFUSE_RUN_LINE_HANDICAP_UNPROVEN = "SIDE_UNPROVEN_RUN_LINE_HANDICAP_DIRECTION_NOT_RECORDED"
REFUSE_UNMAPPED = "SIDE_UNPROVEN_NO_RULE_RELATES_THIS_SELECTION_TO_THIS_CONTRACT"

# Contract families, as `marketFamily` on an observation row.
FAMILY_GAME_RESULT = "game_result"
FAMILY_INNING_RESULT = "inning_result"
FAMILY_FIRST_INNING_RUN = "first_inning_run"
FAMILY_TEAM_TOTAL = "team_total"

# Selection tokens that name a team by the side of the matchup they back.
_AWAY_SUFFIX = "_AWAY"
_HOME_SUFFIX = "_HOME"

# Kalshi writes a team total as "over 4.5 runs"; the model writes the same line
# as "5+ runs". Measured over every archived team-total decision: the offset is
# exactly +0.5 on 1,432 of 1,432 rows, with no other value. It is therefore a
# convention, and treated as one -- any OTHER offset is a genuinely different
# line and refuses.
TEAM_TOTAL_LINE_OFFSET = 0.5


def _teams_from_source_key(source_key):
    """'WSH@SD|ML_Away' -> ('WSH', 'SD'); (None, None) when not that shape."""
    if not source_key or "|" not in str(source_key):
        return None, None
    matchup = str(source_key).split("|", 1)[0]
    if "@" not in matchup:
        return None, None
    away, home = matchup.split("@", 1)
    return (away.strip().upper() or None), (home.strip().upper() or None)


def _selection_team(selection, away, home):
    """The team a directional selection token backs, or None."""
    token = str(selection or "").upper()
    if token.endswith(_AWAY_SUFFIX):
        return away
    if token.endswith(_HOME_SUFFIX):
        return home
    return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _refuse(reason, **evidence):
    return None, None, reason, evidence


def _accept(side, basis, **evidence):
    return side, basis, None, evidence


def resolve_side(record, observation, ticker_method=None):
    """
    Returns (side, basis, refusal_reason, evidence).

    `side` is SIDE_YES, SIDE_NO, or None. When it is None, `refusal_reason` says
    what could not be proven and the caller MUST treat the executable price as
    absent -- never substitute a default side.

    `record`      one decision row (model evaluation).
    `observation` the joined order-book observation for the contract actually
                  being priced; its fields carry the contract's YES meaning.
    `ticker_method` how the contract was identified, from observation_join.
                  Accepted for provenance only -- see the note where RULE 2 used
                  to be. No rule here may decide a side from HOW the contract was
                  found, only from what the contract MEANS.
    """
    record = record or {}
    observation = observation or {}
    selection = record.get("selection")

    # RULE 0 -- an explicit side on the record wins over any inference.
    # Zero archived rows carry one today; honoured so that populating the field
    # upstream later is a strict improvement rather than a silent no-op.
    declared = str(record.get("side") or "").upper()
    if declared in (SIDE_YES, SIDE_NO):
        return _accept(declared, BASIS_DECLARED, declaredSide=declared)

    if not selection:
        return _refuse(REFUSE_NO_SELECTION)
    if not observation:
        return _refuse(REFUSE_NO_CONTRACT, selection=selection)

    family = observation.get("marketFamily")
    contract_team = (observation.get("team") or None)
    operator = (observation.get("comparisonOperator") or "").upper()
    away, home = _teams_from_source_key((record.get("provenance") or {}).get("sourceKey"))

    # RULE 1 -- the selection IS the contract's own YES title, verbatim.
    # The strongest evidence available: no interpretation is involved, the two
    # strings are byte-identical. Measured: 9,490 of 9,490 title-style decision
    # rows in the archive match their contract's title exactly, none disagree.
    title = observation.get("title")
    if title is not None and str(selection) == str(title):
        return _accept(SIDE_YES, BASIS_TITLE_IDENTITY, contractTitle=title)

    # NOTE ON A RULE THAT USED TO BE HERE, AND WHY IT IS GONE.
    #
    # An earlier version short-circuited on `ticker_method ==
    # JOIN_RESOLVED_VIA_GAMEPK` and returned YES, reasoning that the join finds a
    # synthetic key's contract by matching the team abbreviation this selection
    # backs, so the contract must be this selection's own side.
    #
    # That reasoning holds ONLY for families whose contract is picked out by a
    # team suffix. It is false for the first-inning-run family, where the event
    # lists a single contract and the gamePk alone resolves it: NRFI then
    # resolved to the "a run scores" contract and was declared YES -- the exact
    # opposite of the bet. The corpus said so plainly once the fresh-quote view
    # existed: legacy 63.5c against a shadow 37.0c on the same row, complements
    # of each other to the cent.
    #
    # So the shortcut is deleted rather than narrowed. The family rules below
    # already cover every synthetic family (ML -> RULE 4, F5 -> RULE 5,
    # NRFI/YRFI -> RULE 3) and each proves the side from the contract's own
    # semantics instead of from the search that located it. A rule whose
    # correctness depends on how a DIFFERENT function happened to find something
    # is exactly the kind of coupling that hides an inverted side.

    token = str(selection).upper()

    # RULE 3 -- first-inning run. The contract's YES is "1st inning: Over 0.5
    # runs", i.e. at least one run scores. A run either scores or it does not,
    # with no third state, so NRFI is the STRICT complement and prices as NO.
    if family == FAMILY_FIRST_INNING_RUN:
        if token == "YRFI":
            return _accept(SIDE_YES, BASIS_FIRST_INNING_RUN_YES, contractTitle=title)
        if token == "NRFI":
            return _accept(SIDE_NO, BASIS_FIRST_INNING_RUN_NO, contractTitle=title)
        return _refuse(REFUSE_UNMAPPED, selection=selection, family=family)

    # RULE 4 -- full-game moneyline. Away and home are strict complements: a
    # regular-season MLB game cannot end tied, so "not home" IS "away".
    if family == FAMILY_GAME_RESULT and token.startswith("ML_"):
        team = _selection_team(selection, away, home)
        if not team:
            return _refuse(REFUSE_TEAM_UNKNOWN, selection=selection,
                           sourceKey=(record.get("provenance") or {}).get("sourceKey"))
        if contract_team and team == contract_team:
            return _accept(SIDE_YES, BASIS_TEAM_MATCHES_CONTRACT,
                           selectionTeam=team, contractTeam=contract_team)
        if contract_team and contract_team in (away, home):
            return _accept(SIDE_NO, BASIS_MONEYLINE_COMPLEMENT,
                           selectionTeam=team, contractTeam=contract_team)
        return _refuse(REFUSE_TEAM_MISMATCH, selectionTeam=team,
                       contractTeam=contract_team)

    # RULE 5 -- partial-game moneyline (F5/F3/F7). Same team-match rule as
    # RULE 4 for the matching side...
    if family == FAMILY_INNING_RESULT and token.startswith(("F5_ML", "F3_ML", "F7_ML")):
        team = _selection_team(selection, away, home)
        if not team:
            return _refuse(REFUSE_TEAM_UNKNOWN, selection=selection)
        if contract_team and team == contract_team:
            return _accept(SIDE_YES, BASIS_TEAM_MATCHES_CONTRACT,
                           selectionTeam=team, contractTeam=contract_team)
        # ...but NOT the complement rule. A first-5-innings segment CAN end
        # tied -- Kalshi lists a separate TIE contract in the same event, and
        # the archive carries 672 observations of one. So NO on the sibling
        # team's contract means "that team did not win the segment", which
        # includes the tie, and is a DIFFERENT BET from this selection.
        return _refuse(REFUSE_SEGMENT_NOT_BINARY, selectionTeam=team,
                       contractTeam=contract_team, family=family)

    # RULE 6 -- team total. The contract's YES is "team T scores over X runs".
    # The selection must name the same team AND the same line; the model writes
    # the line as "X+1 or more" where Kalshi writes "over X + 0.5".
    if family == FAMILY_TEAM_TOTAL and token.startswith("TT_"):
        if operator != "OVER":
            return _refuse(REFUSE_OPERATOR, operator=operator)
        if not token.endswith("_OVER"):
            # TT_*_Under has never appeared in this corpus; if one is ever
            # produced it must not be silently priced as an Over.
            return _refuse(REFUSE_NO_DIRECTION, selection=selection)
        team = _selection_team(str(selection).replace("_Over", "").replace("_OVER", ""),
                               away, home)
        if not team:
            return _refuse(REFUSE_TEAM_UNKNOWN, selection=selection)
        if not contract_team or team != contract_team:
            return _refuse(REFUSE_TEAM_MISMATCH, selectionTeam=team,
                           contractTeam=contract_team)
        decision_line = _as_float(record.get("threshold"))
        contract_line = _as_float(observation.get("threshold"))
        if decision_line is None or contract_line is None:
            return _refuse(REFUSE_LINE_MISMATCH, decisionLine=decision_line,
                           contractLine=contract_line)
        if abs((decision_line - contract_line) - TEAM_TOTAL_LINE_OFFSET) > 1e-9:
            return _refuse(REFUSE_LINE_MISMATCH, decisionLine=decision_line,
                           contractLine=contract_line)
        return _accept(SIDE_YES, BASIS_TEAM_TOTAL_SAME_LINE, selectionTeam=team,
                       decisionLine=decision_line, contractLine=contract_line)

    # RULE 7 -- a selection with no direction in it. "Game_Total" names a market
    # but not an Over or an Under, and the only things that could supply the
    # missing direction are the model's own probability or the market's price --
    # both forbidden as side evidence, for the obvious reason that a side chosen
    # from the price is not a side, it is a bet on the price.
    if token in ("GAME_TOTAL", "TOTAL"):
        return _refuse(REFUSE_NO_DIRECTION, selection=selection, family=family)

    # RULE 8 -- the run line. RL_Away/RL_Home name a team, but not the handicap
    # that team is taking: the favourite lays it (-1.5) and the underdog takes
    # it (+1.5), and which of those a given row means is nowhere in the record.
    # Kalshi's own winning-margin contract is one-directional ("T wins by over
    # X"), so mapping the wrong one buys a contract that pays on the opposite
    # result. No archived RL row reaches a contract at all today (every one
    # carries a synthetic ticker whose family has no known series), so this
    # refuses on evidence rather than on a convention nobody has written down.
    if token in ("RL_AWAY", "RL_HOME"):
        return _refuse(REFUSE_RUN_LINE_HANDICAP_UNPROVEN, selection=selection,
                       family=family)

    return _refuse(REFUSE_UNMAPPED, selection=selection, family=family)
