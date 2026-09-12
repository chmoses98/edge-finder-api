#!/usr/bin/env python3
"""
lib/wager_settlement_semantics.py
=================================
WAVE 1, subwave A. THE canonical answer to two questions, and nothing else:

    1. Which exact side of which exact contract did this wager purchase?
    2. Given terminal truth for that contract, did this wager WIN or LOSE?

W1-C proved *which game and which contract* a row is about. This module is the
next link and the last one before money: it proves *which end of that contract
we owned*, and refuses when it cannot.

THE GOVERNING INVARIANT
-----------------------
A WAGER MAY BE CALLED WON OR LOST ONLY WHEN THE EXACT CONTRACT, THE EXACT SIDE
OWNED, AND THE TERMINAL TRUTH ARE ALL PROVEN.

Missing evidence is not permission to infer. Every function here returns a
refusal as a first-class value, never a fallback grade.

WHAT THIS REPLACES, AND WHY IT HAD TO BE REPLACED
-------------------------------------------------
Before this module, four independent implementations decided a wager's side,
and every one of them defaulted rather than refused. Measured on the corpus
committed at the time of writing (565 rows in bets.json, 457 in
data/edgelab/bets/bets.jsonl):

  * `clv_update.get_betside()` returns AWAY/HOME/None. It returns None on
    203 of 565 rows -- and 175 of those 203 are already graded WIN or LOSS.
    Each of its three consumers then supplied its own default for the None:

      - `determine_result()` moneyline:  `bet_side == winner` -- None is never
        equal to 'AWAY'/'HOME', so an unproven side grades LOSS.
      - `determine_result()` run line:   `if bet_side == 'HOME': ... else:` --
        an unproven side silently becomes AWAY.
      - `determine_result()` total:      `is_over = not is_under` -- an
        unproven direction silently becomes OVER.
      - `determine_result()` team total: `is_away_side = ... or away_abbr in
        bet_str` -- falsy silently becomes HOME, and `is_over = 'OVER' in
        bet_str or '+' in bet_str` silently becomes UNDER.
      - the F5 path:                     `settle_f5_bet_from_linescore(b,
        game_pk, bet_side or 'away')` -- an unproven side is graded AS IF IT
        BOUGHT AWAY, against real linescore truth, and written to `pl`.

    It also has no YES/NO concept at all, so it answers a question RFI and
    total contracts do not ask: it returns 'AWAY' for 42 archived YRFI wagers,
    purely because the away team's abbreviation appears in the free-text bet
    string ("LAA/TB YRFI"). AWAY is not a side of KXMLBRFI.

  * `lib.edgelab.settlement.settle_bets_for_ticker()` graded with
    `bet.get("side") or "YES"`. Five archived PlacedBet rows carry side=null
    (all multi_market_combo, all still pending, none with a ticker), so this
    default has not yet mis-graded a real row -- but it is one ticker
    assignment away from doing so, and "not fired yet" is not a control.

  * `lib.edgelab.bets._derive_side()` answers `"NO" if "NRFI" in market else
    "YES"`. The NRFI half is correct and is preserved here. The other half is
    a default wearing a rule's clothes: every Under, every NO-side total and
    every NO-side team total in the corpus is a counter-example, and the
    archive contains 40 of them.

  * `lib.edgelab.decision_side.resolve_side()` is NOT one of these. It is
    W1-B1's *pricing* resolver, it already refuses rather than defaults, and
    it decides which quote to compare a model number against -- not what a
    wager owns. It is left exactly as it is; see its own module docstring.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
------------------------------------------
It does not re-parse ticker semantics. `lib.kalshi_mlb_contract_parser`
(W1-C) is the one statement of what a Kalshi MLB ticker's YES side settles on,
and `lib.edgelab.market_identity` (W1-C) is the one statement of a series'
family/horizon and of this repository's side/direction/horizon vocabulary.
Both are imported, neither is duplicated. A second ticker grammar in the
settlement path is exactly how a contract ends up meaning two things.

It does not compute a probability, a price, an edge, a stake or a fee, and it
does not create a wager. A recommendation is not a wager; nothing here turns
one into one.

SEPARATION OF CONCEPTS
----------------------
Three things are kept apart, because collapsing them is what produced the
'AWAY' YRFI rows:

    contract side       YES / NO          -- the trade. What settlement pays.
    semantic expression selection,        -- the claim the trade expresses.
                        direction,           A team, an over/under, a strike,
                        threshold,           a period. Never a side by itself.
                        horizon
    orientation         AWAY / HOME       -- a *role in a matchup*, which is
                                             a way of naming a team and is
                                             meaningless for RFI and game
                                             totals. Resolved to a team before
                                             it is allowed near a contract.

MISSING EVIDENCE vs POSITIVE CONTRADICTION
-------------------------------------------
Both refuse, and they stay distinguishable: every refusal carries a
`refusalClass` of MISSING_EVIDENCE, CONTRADICTION or DEFERRED. "We never
recorded the side" and "the row says AWAY while its ticker says the home team"
are different problems with different remedies, and a single UNKNOWN would
hide the second inside the first.

PLAYER PROPS
------------
Archived, researchable, NOT automatically settled -- tracked under
https://github.com/chmoses98/edge-finder-api/issues/43. A player-prop wager
presented here DEFERS explicitly. No prop settlement semantics are invented
by this module.
"""

from lib import kalshi_mlb_contract_parser as kmcp
from lib.edgelab import market_identity as mi

# ── Contract side ────────────────────────────────────────────────────────────
# Re-exported from W1-C so a caller needs only this module, and so there is
# provably one spelling of YES in the settlement path.
SIDE_YES = mi.SIDE_YES
SIDE_NO = mi.SIDE_NO
VALID_SIDES = (SIDE_YES, SIDE_NO)

# ── Semantic expression ──────────────────────────────────────────────────────
# Re-exported from W1-C for the same reason. These describe the CLAIM, never
# the trade.
DIRECTION_WIN = mi.DIRECTION_WIN
DIRECTION_TIE = mi.DIRECTION_TIE
DIRECTION_OVER = mi.DIRECTION_OVER
DIRECTION_UNDER = mi.DIRECTION_UNDER
DIRECTION_EVENT_OCCURS = mi.DIRECTION_EVENT_OCCURS
DIRECTION_EVENT_DOES_NOT_OCCUR = mi.DIRECTION_EVENT_DOES_NOT_OCCUR

HORIZON_FULL_GAME = mi.HORIZON_FULL_GAME
HORIZON_F3 = mi.HORIZON_F3
HORIZON_F5 = mi.HORIZON_F5
HORIZON_F7 = mi.HORIZON_F7
HORIZON_FIRST_INNING = mi.HORIZON_FIRST_INNING
HORIZON_PLAYER_PROP = mi.HORIZON_PLAYER_PROP

# ── Orientation ──────────────────────────────────────────────────────────────
# A role in a matchup. Never a contract side, and never accepted as one.
ORIENTATION_AWAY = "AWAY"
ORIENTATION_HOME = "HOME"

# ── Canonical wager outcome ──────────────────────────────────────────────────
# Six states, chosen so that "we have not finished the game yet" and "we cannot
# prove what this row owned" can never be read as the same thing.
OUTCOME_WON = "WON"                      # terminal, the wager's side paid
OUTCOME_LOST = "LOST"                    # terminal, the wager's side did not pay
OUTCOME_PUSH = "PUSH"                    # terminal, stake returned (family must
                                         # genuinely be able to push)
OUTCOME_VOID = "VOID"                    # contract cancelled/voided by the venue
OUTCOME_NOT_TERMINAL = "NOT_TERMINAL"    # truth does not exist yet; not a refusal
OUTCOME_UNRESOLVED = "UNRESOLVED"        # refused: evidence insufficient or
                                         # contradictory. NEVER a grade.

TERMINAL_OUTCOMES = (OUTCOME_WON, OUTCOME_LOST, OUTCOME_PUSH, OUTCOME_VOID)

# How the canonical outcome is written into the two ledgers that already exist.
# This module does NOT invent a new persisted vocabulary: bets.json and
# data/edgelab/bets/bets.jsonl already agree on WIN/LOSS/PUSH/VOID (see
# lib.edgelab.bets._RESULT_ENUM and data/edgelab/schema_v1/placed_bet.schema.json),
# and gratuitous schema churn on a money ledger is its own risk. The refusal
# state is carried ADDITIVELY, in `settlementRefusalReason`, precisely so an
# UNRESOLVED row is not silently indistinguishable from a pending one.
LEDGER_RESULT_BY_OUTCOME = {
    OUTCOME_WON: "WIN",
    OUTCOME_LOST: "LOSS",
    OUTCOME_PUSH: "PUSH",
    OUTCOME_VOID: "VOID",
    OUTCOME_NOT_TERMINAL: None,
    OUTCOME_UNRESOLVED: None,
}
LEDGER_STATUS_BY_OUTCOME = {
    OUTCOME_WON: "settled",
    OUTCOME_LOST: "settled",
    OUTCOME_PUSH: "settled",
    OUTCOME_VOID: "void",
    OUTCOME_NOT_TERMINAL: "pending",
    OUTCOME_UNRESOLVED: "pending",
}

# ── Refusal classes ──────────────────────────────────────────────────────────
REFUSAL_MISSING_EVIDENCE = "MISSING_EVIDENCE"
REFUSAL_CONTRADICTION = "CONTRADICTION"
REFUSAL_DEFERRED = "DEFERRED"

# ── Side refusal reasons (machine-readable) ──────────────────────────────────
# MISSING_EVIDENCE
SIDE_UNPROVEN_NO_SIDE_EVIDENCE = "SIDE_UNPROVEN_ROW_RECORDS_NO_SIDE_OR_SELECTION"
SIDE_UNPROVEN_UNRECOGNIZED_TOKEN = "SIDE_UNPROVEN_SIDE_TOKEN_IS_NOT_IN_THE_CONTROLLED_VOCABULARY"
SIDE_UNPROVEN_NO_CONTRACT = "SIDE_UNPROVEN_NO_MARKET_TICKER_ON_THE_ROW"
SIDE_UNPROVEN_CONTRACT_UNPARSEABLE = "SIDE_UNPROVEN_TICKER_DOES_NOT_PARSE_TO_A_CONTRACT_CONDITION"
SIDE_UNPROVEN_SERIES_NOT_DESCRIBED = "SIDE_UNPROVEN_SERIES_HAS_NO_DESCRIBED_CONTRACT_GRAMMAR"
SIDE_UNPROVEN_NO_DIRECTION = "SIDE_UNPROVEN_EXPRESSION_CARRIES_NO_DIRECTION"
SIDE_UNPROVEN_NO_SELECTION = "SIDE_UNPROVEN_EXPRESSION_NAMES_NO_TEAM_AND_THIS_CONTRACT_NAMES_ONE"
SIDE_UNPROVEN_NO_THRESHOLD = "SIDE_UNPROVEN_EXPRESSION_CARRIES_NO_THRESHOLD_AND_THIS_CONTRACT_HAS_A_STRIKE"
SIDE_UNPROVEN_TEAMS_UNKNOWN = "SIDE_UNPROVEN_CANNOT_NAME_THE_TEAMS_AN_ORIENTATION_REFERS_TO"
SIDE_UNPROVEN_SEGMENT_IS_NOT_BINARY = (
    "SIDE_UNPROVEN_PERIOD_CAN_TIE_SO_THE_SIBLING_TEAMS_CONTRACT_IS_NOT_A_COMPLEMENT"
)
SIDE_UNPROVEN_COMPLEMENT_NOT_PROVEN_BINARY = (
    "SIDE_UNPROVEN_COMPLEMENT_REQUIRES_A_PROVEN_BINARY_CONTRACT"
)
# CONTRADICTION
SIDE_CONTRADICTED_FIELDS_DISAGREE = "SIDE_CONTRADICTED_TWO_RECORDED_SIDE_FIELDS_DISAGREE"
SIDE_CONTRADICTED_DECLARED_VS_EXPRESSION = (
    "SIDE_CONTRADICTED_DECLARED_CONTRACT_SIDE_DISAGREES_WITH_THE_ROWS_OWN_EXPRESSION"
)
SIDE_CONTRADICTED_TEAM_NOT_ON_THIS_CONTRACT = (
    "SIDE_CONTRADICTED_SELECTED_TEAM_IS_NOT_A_TEAM_THIS_CONTRACTS_EVENT_NAMES"
)
SIDE_CONTRADICTED_SIBLING_TEAM_IS_A_DIFFERENT_CONTRACT = (
    "SIDE_CONTRADICTED_SIBLING_TEAMS_THRESHOLD_CONTRACT_IS_A_DIFFERENT_CONTRACT_NOT_A_COMPLEMENT"
)
SIDE_CONTRADICTED_THRESHOLD = "SIDE_CONTRADICTED_EXPRESSION_STRIKE_IS_NOT_THIS_CONTRACTS_STRIKE"
SIDE_CONTRADICTED_FAMILY = "SIDE_CONTRADICTED_EXPRESSION_FAMILY_IS_NOT_THIS_CONTRACTS_CONDITION"
SIDE_CONTRADICTED_HORIZON = "SIDE_CONTRADICTED_EXPRESSION_HORIZON_IS_NOT_THIS_CONTRACTS_HORIZON"
# DEFERRED
SIDE_DEFERRED_PLAYER_PROP = "SIDE_DEFERRED_PLAYER_PROP_SETTLEMENT_TRACKED_UNDER_ISSUE_43"
SIDE_DEFERRED_MULTI_MARKET_COMBO = "SIDE_DEFERRED_MULTI_MARKET_COMBO_HAS_NO_SINGLE_CONTRACT_SIDE"

# ── Settlement refusal reasons ───────────────────────────────────────────────
SETTLEMENT_UNPROVEN_SIDE = "SETTLEMENT_UNPROVEN_PURCHASED_SIDE_NOT_PROVEN"
SETTLEMENT_UNPROVEN_NO_MARKET_SETTLEMENT = "SETTLEMENT_UNPROVEN_NO_TERMINAL_TRUTH_FOR_THIS_CONTRACT"
SETTLEMENT_UNPROVEN_MARKET_RESULT_MISSING = "SETTLEMENT_UNPROVEN_MARKET_IS_SETTLED_BUT_CARRIES_NO_YES_NO_RESULT"
SETTLEMENT_CONTRADICTED_TICKER = "SETTLEMENT_CONTRADICTED_SETTLEMENT_TICKER_IS_NOT_THE_WAGERS_TICKER"
SETTLEMENT_CONTRADICTED_GAME = "SETTLEMENT_CONTRADICTED_SETTLEMENT_GAME_IS_NOT_THE_WAGERS_GAME"
SETTLEMENT_DEFERRED_PLAYER_PROP = "SETTLEMENT_DEFERRED_PLAYER_PROP_TRACKED_UNDER_ISSUE_43"

REFUSAL_CLASS_BY_REASON = {
    SIDE_UNPROVEN_NO_SIDE_EVIDENCE: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_UNRECOGNIZED_TOKEN: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_NO_CONTRACT: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_CONTRACT_UNPARSEABLE: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_SERIES_NOT_DESCRIBED: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_NO_DIRECTION: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_NO_SELECTION: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_NO_THRESHOLD: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_TEAMS_UNKNOWN: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_SEGMENT_IS_NOT_BINARY: REFUSAL_MISSING_EVIDENCE,
    SIDE_UNPROVEN_COMPLEMENT_NOT_PROVEN_BINARY: REFUSAL_MISSING_EVIDENCE,
    SIDE_CONTRADICTED_FIELDS_DISAGREE: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_DECLARED_VS_EXPRESSION: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_TEAM_NOT_ON_THIS_CONTRACT: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_SIBLING_TEAM_IS_A_DIFFERENT_CONTRACT: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_THRESHOLD: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_FAMILY: REFUSAL_CONTRADICTION,
    SIDE_CONTRADICTED_HORIZON: REFUSAL_CONTRADICTION,
    SIDE_DEFERRED_PLAYER_PROP: REFUSAL_DEFERRED,
    SIDE_DEFERRED_MULTI_MARKET_COMBO: REFUSAL_DEFERRED,
    SETTLEMENT_UNPROVEN_SIDE: REFUSAL_MISSING_EVIDENCE,
    SETTLEMENT_UNPROVEN_NO_MARKET_SETTLEMENT: REFUSAL_MISSING_EVIDENCE,
    SETTLEMENT_UNPROVEN_MARKET_RESULT_MISSING: REFUSAL_MISSING_EVIDENCE,
    SETTLEMENT_CONTRADICTED_TICKER: REFUSAL_CONTRADICTION,
    SETTLEMENT_CONTRADICTED_GAME: REFUSAL_CONTRADICTION,
    SETTLEMENT_DEFERRED_PLAYER_PROP: REFUSAL_DEFERRED,
}

# ── How a side was proven ────────────────────────────────────────────────────
BASIS_DECLARED_CONTRACT_SIDE = "DECLARED_CONTRACT_SIDE_ON_THE_ROW"
BASIS_EXPRESSION_ASSERTS_CONTRACT_CONDITION = "EXPRESSION_ASSERTS_THIS_CONTRACTS_YES_CONDITION"
BASIS_EXPRESSION_NEGATES_CONTRACT_CONDITION = "EXPRESSION_NEGATES_THIS_CONTRACTS_YES_CONDITION"
BASIS_BINARY_COMPLEMENT = "EXPRESSION_IS_THE_PROVEN_STRICT_COMPLEMENT_OF_THIS_CONTRACTS_YES"

# ─────────────────────────────────────────────────────────────────────────────
# Team abbreviations
# ─────────────────────────────────────────────────────────────────────────────
# The 30 clubs as Kalshi spells them on its own tickers, plus the handful of
# legacy spellings this repository's ledgers actually contain. EXACT MATCH ONLY.
#
# There is deliberately no fuzzy fallback here. `clv_update.to_abbr()` ends with
# "last resort: first 3 chars uppercased", which turns any unrecognized string
# into a confident-looking abbreviation -- "Over" becomes "OVE" and then
# compares unequal to everything, and a typo becomes a team. A name this table
# does not know is an unknown team, and an unknown team refuses.
KALSHI_TEAM_ABBRS = frozenset({
    "ARI", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE", "COL", "DET",
    "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN", "NYM", "NYY", "ATH",
    "PHI", "PIT", "SD", "SF", "SEA", "STL", "TB", "TEX", "TOR", "WSH",
    # Kalshi writes Arizona as AZ on tickers (see
    # kalshi_mlb_contract_parser.TWO_LETTER_TEAM_ABBRS); both spellings are
    # real and both are canonical members of the vocabulary.
    "AZ",
})

# Spellings that mean the same club. Applied ONLY as an explicit, enumerated
# alias -- never derived. Both directions are kept distinct so that a
# comparison can be made without asserting which spelling is "the" right one.
_TEAM_ALIAS_GROUPS = (
    frozenset({"ARI", "AZ"}),
    frozenset({"ATH", "OAK"}),
)
_TEAM_ALIASES = {}
for _group in _TEAM_ALIAS_GROUPS:
    for _member in _group:
        _TEAM_ALIASES[_member] = _group


# Club names, as the ledgers actually spell them, mapped to the abbreviation.
# EXACT MATCH ONLY -- this is a lookup table, not a matcher.
#
# It exists because the root ledger records some rows only by name
# ('Phillies TT Under 4.5', 'Yankees ML', 'Dodgers ML'): 31 settled rows carry
# no abbreviation anywhere. A name is DETERMINISTIC evidence of which club was
# backed, and refusing it would be discarding evidence, not being careful.
#
# What is NOT here is the thing that made `clv_update.to_abbr()` dangerous: its
# fuzzy pass ("match by first word", then "last resort: first 3 chars
# uppercased"), which returned a confident abbreviation for literally any
# string, including 'Over' -> 'OVE' and a pitcher's surname -> a club.
#
# AMBIGUOUS NAMES ARE DELIBERATELY ABSENT. 'SOX' is both Boston and Chicago and
# is therefore not a club name; neither is 'CHICAGO', 'NEW YORK' or 'LOS
# ANGELES'. A two-club name resolves to nothing and refuses, which is the
# correct answer to an ambiguous question.
_TEAM_NAMES = {
    "ARIZONA DIAMONDBACKS": "ARI", "DIAMONDBACKS": "ARI", "DBACKS": "ARI",
    "ATLANTA BRAVES": "ATL", "BRAVES": "ATL",
    "BALTIMORE ORIOLES": "BAL", "ORIOLES": "BAL",
    "BOSTON RED SOX": "BOS", "RED SOX": "BOS",
    "CHICAGO CUBS": "CHC", "CUBS": "CHC",
    "CHICAGO WHITE SOX": "CWS", "WHITE SOX": "CWS",
    "CINCINNATI REDS": "CIN", "REDS": "CIN",
    "CLEVELAND GUARDIANS": "CLE", "GUARDIANS": "CLE",
    "COLORADO ROCKIES": "COL", "ROCKIES": "COL",
    "DETROIT TIGERS": "DET", "TIGERS": "DET",
    "HOUSTON ASTROS": "HOU", "ASTROS": "HOU",
    "KANSAS CITY ROYALS": "KC", "ROYALS": "KC",
    "LOS ANGELES ANGELS": "LAA", "ANGELS": "LAA",
    "LOS ANGELES DODGERS": "LAD", "DODGERS": "LAD",
    "MIAMI MARLINS": "MIA", "MARLINS": "MIA",
    "MILWAUKEE BREWERS": "MIL", "BREWERS": "MIL",
    "MINNESOTA TWINS": "MIN", "TWINS": "MIN",
    "NEW YORK METS": "NYM", "METS": "NYM",
    "NEW YORK YANKEES": "NYY", "YANKEES": "NYY",
    "OAKLAND ATHLETICS": "ATH", "LAS VEGAS ATHLETICS": "ATH", "ATHLETICS": "ATH",
    "PHILADELPHIA PHILLIES": "PHI", "PHILLIES": "PHI",
    "PITTSBURGH PIRATES": "PIT", "PIRATES": "PIT",
    "SAN DIEGO PADRES": "SD", "PADRES": "SD",
    "SAN FRANCISCO GIANTS": "SF", "GIANTS": "SF",
    "SEATTLE MARINERS": "SEA", "MARINERS": "SEA",
    "ST. LOUIS CARDINALS": "STL", "ST LOUIS CARDINALS": "STL", "CARDINALS": "STL",
    "TAMPA BAY RAYS": "TB", "RAYS": "TB",
    "TEXAS RANGERS": "TEX", "RANGERS": "TEX",
    "TORONTO BLUE JAYS": "TOR", "BLUE JAYS": "TOR",
    "WASHINGTON NATIONALS": "WSH", "NATIONALS": "WSH",
}

# The single words above that are unambiguous on their own, so a tokenizer can
# recognize 'PHILLIES' inside 'PHILLIES TT UNDER 4.5' without a phrase lookup.
# Multi-word names ('RED SOX', 'BLUE JAYS') are matched as phrases instead.
_SINGLE_WORD_TEAM_NAMES = {name: abbr for name, abbr in _TEAM_NAMES.items()
                           if " " not in name}
_MULTI_WORD_TEAM_NAMES = {name: abbr for name, abbr in _TEAM_NAMES.items()
                          if " " in name}


def normalize_team(value):
    """
    A club abbreviation this vocabulary knows, or None.

    Exact match only, against the abbreviations and the enumerated club-name
    table. There is no fuzzy pass and no positional fallback: an unrecognized
    string is an unknown club, and an unknown club refuses.
    """
    if value is None:
        return None
    token = " ".join(str(value).strip().upper().split())
    if token in KALSHI_TEAM_ABBRS or token in _TEAM_ALIASES:
        return token
    return _TEAM_NAMES.get(token)


def same_team(a, b):
    """True when two abbreviations name the same club, alias-aware."""
    a, b = normalize_team(a), normalize_team(b)
    if a is None or b is None:
        return False
    if a == b:
        return True
    return b in _TEAM_ALIASES.get(a, frozenset())


# ─────────────────────────────────────────────────────────────────────────────
# The binary complement rule
# ─────────────────────────────────────────────────────────────────────────────
# Every Kalshi binary contract has a YES and a NO, so "NO on ticker T" is always
# a well-defined trade. That is NOT what this rule is about.
#
# This rule is about the OTHER complement -- the cross-contract one: is
# "team B wins" the same statement as "team A does not win"? For a full MLB
# game, yes: the game cannot end level, so the two contracts partition the
# event space and NO on A's ticker IS a bet on B. For a first-3/5/7-inning
# segment, NO: the segment can end tied, Kalshi lists a separate TIE contract
# in the same event, and "A did not win the segment" includes the tie. Treating
# those as complements buys a strictly different bet.
#
# So the complement is allowed only where the event space is PROVEN to have
# exactly two outcomes, stated here per (condition, horizon) rather than
# inferred from two labels looking opposite.
_BINARY_EVENT_SPACE = {
    # A full MLB game plays to a decision. Complement is sound.
    (kmcp.CONDITION_TEAM_WINS, HORIZON_FULL_GAME): True,
    # F3/F5/F7 can tie. Complement is NOT sound.
    (kmcp.CONDITION_TEAM_WINS, HORIZON_F3): False,
    (kmcp.CONDITION_TEAM_WINS, HORIZON_F5): False,
    (kmcp.CONDITION_TEAM_WINS, HORIZON_F7): False,
}


def is_binary_event_space(condition, horizon):
    """
    Can a sibling contract's claim be read as the strict negation of this one?

    True only where this repository has a positive statement that the event
    space has exactly two outcomes. An UNLISTED (condition, horizon) pair is
    False -- absence of a recorded tie is not evidence there cannot be one.
    """
    return bool(_BINARY_EVENT_SPACE.get((condition, horizon), False))


# Conditions whose contract is a THRESHOLD on a quantity, and whose own NO is
# therefore exactly "the quantity fell short". For these, an UNDER claim on the
# SAME ticker is the contract's own NO -- no cross-contract reasoning involved,
# so no tie can hide in it.
_THRESHOLD_CONDITIONS = frozenset({
    kmcp.CONDITION_COMBINED_RUNS_AT_LEAST,
    kmcp.CONDITION_TEAM_RUNS_AT_LEAST,
    kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST,
    kmcp.CONDITION_FIRST_INNING_RUNS_AT_LEAST,
})

# Families that can genuinely PUSH, i.e. return the stake on an exact tie with
# the line. Kalshi's MLB contracts are binary threshold contracts on integer
# quantities with half-point or minimum-inclusive strikes -- there is no rung a
# whole-number outcome can land exactly on, so no Kalshi MLB family in this
# table pushes. PUSH survives in the canonical vocabulary because the ROOT
# ledger carries 9 historical PUSH rows from sportsbook-style whole-number
# lines, and erasing a recorded push would be rewriting history, not
# normalizing it.
PUSH_CAPABLE_CONTRACT_CONDITIONS = frozenset()


# ─────────────────────────────────────────────────────────────────────────────
# Stage A -- the semantic expression
# ─────────────────────────────────────────────────────────────────────────────
# The controlled vocabulary of side/selection tokens the two ledgers actually
# contain, measured over bets.json (565 rows) and
# data/edgelab/bets/bets.jsonl (457 rows). Anything outside it refuses; nothing
# here is a guess about a spelling that has never appeared.
#
# Each token maps to (contract_side, orientation, direction, event_token).
# A token may supply some of those and not others -- 'AWAY' names an
# orientation and says nothing about direction, and that is the point: an
# orientation alone is not a side.
_TOKEN_CONTRACT_SIDE = {
    "YES": SIDE_YES,
    "NO": SIDE_NO,
}
_TOKEN_ORIENTATION = {
    "AWAY": ORIENTATION_AWAY,
    "HOME": ORIENTATION_HOME,
}
_TOKEN_DIRECTION = {
    "OVER": DIRECTION_OVER,
    "UNDER": DIRECTION_UNDER,
    "O": DIRECTION_OVER,
    "U": DIRECTION_UNDER,
}
# The two RFI decisions. ONE contract (KXMLBRFI), two ends of it.
_TOKEN_RFI_DIRECTION = {
    "YRFI": DIRECTION_EVENT_OCCURS,
    "NRFI": DIRECTION_EVENT_DOES_NOT_OCCUR,
}

# `market` label -> (horizon, family, orientation, direction). The ledgers
# encode part of the claim in the market name itself ('F5_ML_Away',
# 'TT_Home_Over', 'NRFI'), and this is the ONE place that says what each label
# asserts. Labels this table does not list are not interpreted.
#
# LEDGER_MARKET_SEMANTICS in lib.edgelab.market_identity covers the machine-
# generated decision labels. This table covers the WAGER ledger's own market
# spellings, which are a different and messier set (19 distinct values in
# bets.json, including both 'ML' and 'ML_Away'), and it maps them onto exactly
# the same vocabulary rather than inventing a parallel one.
_MARKET_LABEL_SEMANTICS = {
    # full-game moneyline: the team is named elsewhere on the row
    "ML":            (HORIZON_FULL_GAME, "MONEYLINE", None, DIRECTION_WIN),
    "MONEYLINE":     (HORIZON_FULL_GAME, "MONEYLINE", None, DIRECTION_WIN),
    "ML_AWAY":       (HORIZON_FULL_GAME, "MONEYLINE", ORIENTATION_AWAY, DIRECTION_WIN),
    "ML_HOME":       (HORIZON_FULL_GAME, "MONEYLINE", ORIENTATION_HOME, DIRECTION_WIN),
    # partial-game moneyline
    "F3 ML":         (HORIZON_F3, "MONEYLINE", None, DIRECTION_WIN),
    "F3_ML_AWAY":    (HORIZON_F3, "MONEYLINE", ORIENTATION_AWAY, DIRECTION_WIN),
    "F3_ML_HOME":    (HORIZON_F3, "MONEYLINE", ORIENTATION_HOME, DIRECTION_WIN),
    "F5":            (HORIZON_F5, "MONEYLINE", None, DIRECTION_WIN),
    "F5 ML":         (HORIZON_F5, "MONEYLINE", None, DIRECTION_WIN),
    "F5ML":          (HORIZON_F5, "MONEYLINE", None, DIRECTION_WIN),
    "F5_ML_AWAY":    (HORIZON_F5, "MONEYLINE", ORIENTATION_AWAY, DIRECTION_WIN),
    "F5_ML_HOME":    (HORIZON_F5, "MONEYLINE", ORIENTATION_HOME, DIRECTION_WIN),
    "F7 ML":         (HORIZON_F7, "MONEYLINE", None, DIRECTION_WIN),
    "F7_ML_AWAY":    (HORIZON_F7, "MONEYLINE", ORIENTATION_AWAY, DIRECTION_WIN),
    "F7_ML_HOME":    (HORIZON_F7, "MONEYLINE", ORIENTATION_HOME, DIRECTION_WIN),
    # the explicit period tie is its own contract, never a complement
    "F3 TIE":        (HORIZON_F3, "PERIOD_TIE", None, DIRECTION_TIE),
    "F5 TIE":        (HORIZON_F5, "PERIOD_TIE", None, DIRECTION_TIE),
    "F7 TIE":        (HORIZON_F7, "PERIOD_TIE", None, DIRECTION_TIE),
    # run line / spread. Direction is NOT implied by the label: a run line
    # names a team and a margin, and whether the row backs that margin being
    # made or missed has to be recorded, not assumed.
    "RL":            (HORIZON_FULL_GAME, "RUN_LINE", None, None),
    "RUN LINE":      (HORIZON_FULL_GAME, "RUN_LINE", None, None),
    "RUN_LINE":      (HORIZON_FULL_GAME, "RUN_LINE", None, None),
    "RUNLINE":       (HORIZON_FULL_GAME, "RUN_LINE", None, None),
    "RL_AWAY":       (HORIZON_FULL_GAME, "RUN_LINE", ORIENTATION_AWAY, None),
    "RL_HOME":       (HORIZON_FULL_GAME, "RUN_LINE", ORIENTATION_HOME, None),
    "F5 RL":         (HORIZON_F5, "RUN_LINE", None, None),
    "F5_RL_AWAY":    (HORIZON_F5, "RUN_LINE", ORIENTATION_AWAY, None),
    "F5_RL_HOME":    (HORIZON_F5, "RUN_LINE", ORIENTATION_HOME, None),
    # game total. 'Total' alone names a market and not a direction.
    "TOTAL":         (HORIZON_FULL_GAME, "GAME_TOTAL", None, None),
    "GAME TOTAL":    (HORIZON_FULL_GAME, "GAME_TOTAL", None, None),
    "GAMETOTAL":     (HORIZON_FULL_GAME, "GAME_TOTAL", None, None),
    "GAME_TOTAL":    (HORIZON_FULL_GAME, "GAME_TOTAL", None, None),
    "TOTAL OVER":    (HORIZON_FULL_GAME, "GAME_TOTAL", None, DIRECTION_OVER),
    "TOTAL UNDER":   (HORIZON_FULL_GAME, "GAME_TOTAL", None, DIRECTION_UNDER),
    "F5 TOTAL":      (HORIZON_F5, "GAME_TOTAL", None, None),
    # team total
    "TT":            (HORIZON_FULL_GAME, "TEAM_TOTAL", None, None),
    "TEAM TOTAL":    (HORIZON_FULL_GAME, "TEAM_TOTAL", None, None),
    "TEAMTOTAL":     (HORIZON_FULL_GAME, "TEAM_TOTAL", None, None),
    "TT OVER":       (HORIZON_FULL_GAME, "TEAM_TOTAL", None, DIRECTION_OVER),
    "TT_OVER":       (HORIZON_FULL_GAME, "TEAM_TOTAL", None, DIRECTION_OVER),
    "TT_UNDER":      (HORIZON_FULL_GAME, "TEAM_TOTAL", None, DIRECTION_UNDER),
    "TT_AWAY_OVER":  (HORIZON_FULL_GAME, "TEAM_TOTAL", ORIENTATION_AWAY, DIRECTION_OVER),
    "TT_HOME_OVER":  (HORIZON_FULL_GAME, "TEAM_TOTAL", ORIENTATION_HOME, DIRECTION_OVER),
    "TT_AWAY_UNDER": (HORIZON_FULL_GAME, "TEAM_TOTAL", ORIENTATION_AWAY, DIRECTION_UNDER),
    "TT_HOME_UNDER": (HORIZON_FULL_GAME, "TEAM_TOTAL", ORIENTATION_HOME, DIRECTION_UNDER),
    # first-inning run: one contract, two named ends
    "NRFI":          (HORIZON_FIRST_INNING, "RFI", None, DIRECTION_EVENT_DOES_NOT_OCCUR),
    "YRFI":          (HORIZON_FIRST_INNING, "RFI", None, DIRECTION_EVENT_OCCURS),
}

# Wager-ledger market labels that name a player-prop family. These DEFER; see
# the module docstring and issue #43.
_PLAYER_PROP_MARKET_LABELS = frozenset({
    "K PROP", "PITCHER PROP", "HITTER PROP", "PLAYER PROP",
    "PITCHER_STRIKEOUTS", "PITCHER_OUTS", "HITTER_HITS", "HITTER_TOTAL_BASES",
    "HITTER_HITS_RUNS_RBIS", "HITTER_RBIS", "HITTER_STOLEN_BASES",
})
_MULTI_MARKET_LABELS = frozenset({"MULTI_MARKET_COMBO", "PARLAY", "COMBO"})

# The EdgeLab PlacedBet marketFamily spellings for player props, which differ
# from the wager-ledger's own labels above (see lib.edgelab.settlement's
# _PLAYER_PROP_FAMILIES and lib.edgelab.market_identity.PLAYER_PROP_SERIES).
_PLAYER_PROP_FAMILY_TOKENS = frozenset(
    {f.upper() for f in mi.PLAYER_PROP_SERIES.values()}
    | {s.upper() for s in mi.PLAYER_PROP_SERIES}
    | _PLAYER_PROP_MARKET_LABELS
)


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_game_matchup(game):
    """
    'AWAY @ HOME' / 'AWAY@HOME' -> (away, home), both known abbreviations.

    (None, None) when either half is not a club this vocabulary knows. There is
    no fuzzy match and no positional fallback: a matchup string that cannot be
    read exactly is not evidence about which team an orientation refers to.
    """
    if not game or "@" not in str(game):
        return None, None
    away, home = str(game).split("@", 1)
    return normalize_team(away), normalize_team(home)


def _collect_side_tokens(wager):
    """
    Every explicitly recorded side/selection value on the row, as raw strings.

    Reads ONLY named fields. The free-text `bet`/`notes` strings are
    deliberately NOT read: `clv_update.get_betside()` searched `bet` for the
    away abbreviation and thereby labelled 42 archived YRFI wagers 'AWAY',
    because "LAA/TB YRFI" contains "LAA". A substring of prose is not a
    recorded side.
    """
    out = []
    for field in ("side", "betSide", "betTeam", "selection"):
        value = wager.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out.append((field, text))
    return out


def _read_token(text):
    """
    One recorded value -> whatever parts of the expression it states.

    Returns a dict with any of contractSide/orientation/direction/selection
    populated, or None when the value is outside the controlled vocabulary.
    A composite like 'AWAY OVER' states two parts and both are kept.
    """
    token = str(text).strip().upper()
    if not token:
        return None

    # A whole token that is itself a club abbreviation names the selection.
    team = normalize_team(token)
    if team:
        return {"selection": team}

    parts = [p for p in token.replace("/", " ").split() if p]
    read = {}
    understood = 0
    for part in parts:
        if part in _TOKEN_CONTRACT_SIDE:
            read["contractSide"] = _TOKEN_CONTRACT_SIDE[part]
            understood += 1
        elif part in _TOKEN_ORIENTATION:
            read["orientation"] = _TOKEN_ORIENTATION[part]
            understood += 1
        elif part in _TOKEN_RFI_DIRECTION:
            read["direction"] = _TOKEN_RFI_DIRECTION[part]
            read["family"] = "RFI"
            understood += 1
        elif part in _TOKEN_DIRECTION:
            read["direction"] = _TOKEN_DIRECTION[part]
            understood += 1
        else:
            selection = normalize_team(part)
            if selection:
                read["selection"] = selection
                understood += 1
            else:
                # An element nobody can read makes the WHOLE value
                # uninterpretable. 'PIT Over 4' is not "PIT + Over" with a
                # harmless leftover -- the 4 is a strike, and silently dropping
                # it is how a row gets settled against the wrong rung.
                return None
    return read if understood == len(parts) and read else None


def read_bet_string(text):
    """
    The root ledger's semi-structured `bet` field, parsed WHOLE or not at all.

    416 of 565 archived rows carry one, in 290 distinct spellings: 'MIN ML',
    'Total Under 8.5', 'ATL RL -1.5', 'LAA/TB YRFI', 'Over 7.5'. For many rows
    it is the only place the team or the direction was ever written down, so
    refusing to read it at all would throw away real recorded evidence -- and
    that is not the same thing as refusing to GUESS.

    THE DIFFERENCE FROM WHAT THIS REPLACES. `clv_update.get_betside()` did:

        if away_abbr and away_abbr in bet_str.upper(): return 'AWAY'

    -- a SUBSTRING test against free prose, which labelled 42 YRFI wagers
    'AWAY' because "LAA/TB YRFI" contains "LAA". This function tokenizes and
    requires EVERY token to be in the controlled vocabulary. Nothing is scanned
    for, nothing is skipped, and there is no leftover:

      * one unreadable token makes the WHOLE string unreadable (returns None);
      * TWO distinct clubs in one string is ambiguity, not a pick -- 'LAA/TB
        YRFI' names both teams and backs neither, so it returns None and the
        caller refuses;
      * a number is read as a threshold, never dropped. 'PIT Over 4' without
        the 4 would be a different bet.

    Returns a dict with any of selection/direction/threshold/family/horizon, or
    None when the string cannot be read in full.
    """
    if text is None:
        return None
    raw = str(text).strip().upper()
    if not raw:
        return None

    # '@' separates a matchup ('YRFI SEA@BAL'); '(' and ')' bracket an
    # annotation ('TOR ML (Kalshi NO)'). Both are punctuation, and neither is a
    # token. Splitting on them changes nothing about the strictness below --
    # every resulting token still has to be understood.
    for separator in ("/", ",", "@", "(", ")"):
        raw = raw.replace(separator, " ")
    parts = [p for p in raw.split() if p]
    if not parts:
        return None

    read = {}
    teams = set()
    index = 0
    while index < len(parts):
        # Longest market label first: 'TEAM TOTAL' and 'RUN LINE' are two words
        # and must not be read as 'TEAM' + 'TOTAL'.
        matched = False
        for span in (3, 2):
            if index + span > len(parts):
                continue
            phrase = " ".join(parts[index:index + span])
            label = _MARKET_LABEL_SEMANTICS.get(phrase)
            if label is not None:
                _apply_market_label(read, label)
                index += span
                matched = True
                break
            # Multi-word club names ('RED SOX', 'BLUE JAYS'). Matched as a
            # phrase, never as their halves -- 'SOX' alone names two clubs and
            # is deliberately not in the vocabulary.
            named = _MULTI_WORD_TEAM_NAMES.get(phrase)
            if named is not None:
                teams.add(named)
                index += span
                matched = True
                break
        if matched:
            continue

        part = parts[index]
        index += 1

        team = normalize_team(part)
        if team:
            teams.add(team)
            continue
        if part in _TOKEN_CONTRACT_SIDE:
            # An explicitly written contract side. '(Kalshi NO)' says outright
            # which end was bought, and an explicit NO is read with exactly the
            # rigour of an explicit YES -- same branch, same handling.
            side = _TOKEN_CONTRACT_SIDE[part]
            if read.get("contractSide") not in (None, side):
                return None
            read["contractSide"] = side
            continue
        if part in _TOKEN_RFI_DIRECTION:
            read["direction"] = _TOKEN_RFI_DIRECTION[part]
            read["family"] = "RFI"
            read["horizon"] = HORIZON_FIRST_INNING
            continue
        if part in _TOKEN_DIRECTION:
            read["direction"] = _TOKEN_DIRECTION[part]
            continue
        if part in _TOKEN_ORIENTATION:
            read["orientation"] = _TOKEN_ORIENTATION[part]
            continue
        label = _MARKET_LABEL_SEMANTICS.get(part)
        if label is not None:
            _apply_market_label(read, label)
            continue
        if part in _BET_STRING_ANNOTATIONS:
            continue
        number = _as_float(part)
        if number is not None:
            if number < 0:
                # A signed handicap ('ATL RL -1.5'). The magnitude is a strike
                # and the sign says who lays it, but this repository has no
                # written statement of that convention -- W1-B1's own resolver
                # refuses run lines for exactly this reason
                # (REFUSE_RUN_LINE_HANDICAP_UNPROVEN). Recorded so it is
                # visible, never converted into a strike.
                read["signedHandicap"] = number
                continue
            read["threshold"] = number
            continue
        # Anything else -- a player name, a club nickname this vocabulary does
        # not carry, a note, a typo. One unreadable token and the WHOLE string
        # is unreadable; there is no partial credit.
        return None

    if len(teams) > 1:
        # Both clubs named. 'LAA/TB YRFI' and 'MIN/PIT Under 8' identify the
        # GAME, not a side -- so no selection is established. That is not fatal
        # by itself: RFI and game-total contracts name no team, and the
        # families that DO name one refuse below on a missing selection.
        read["ambiguousTeams"] = sorted(teams)
    elif teams:
        read["selection"] = next(iter(teams))
    return read or None


def _apply_market_label(read, label):
    horizon, family, orientation, direction = label
    read["horizon"] = read.get("horizon") or horizon
    read["family"] = read.get("family") or family
    if orientation:
        read["orientation"] = orientation
    if direction:
        read.setdefault("direction", direction)


# Words that appear inside a `bet` string as ANNOTATION and carry no side
# meaning. Enumerated from the corpus, never pattern-matched: 'KALSHI' is the
# venue in '(Kalshi NO)'. A word not on this list is unreadable, which is the
# whole point -- a player's surname must not be quietly ignored on its way to
# grading a pitcher-prop wager.
_BET_STRING_ANNOTATIONS = frozenset({"KALSHI"})


def read_market_label(market):
    """
    (horizon, family, orientation, direction) for a wager-ledger market label.

    None when this system has no statement of what the label means -- which is
    a refusal condition for the caller, never a licence to assume a full-game
    moneyline.
    """
    if market is None:
        return None
    return _MARKET_LABEL_SEMANTICS.get(str(market).strip().upper())


def is_player_prop(wager):
    """True when the row names a player-prop family in any of its spellings."""
    for field in ("market", "marketFamily", "seriesTicker"):
        value = wager.get(field)
        if value and str(value).strip().upper() in _PLAYER_PROP_FAMILY_TOKENS:
            return True
    ticker = wager.get("marketTicker") or wager.get("ticker")
    series = mi.series_of(ticker) if ticker else None
    return bool(series and series in mi.PLAYER_PROP_SERIES)


def is_multi_market(wager):
    """True when the row is a combo/parlay -- no single contract side exists."""
    for field in ("market", "marketFamily"):
        value = wager.get(field)
        if value and str(value).strip().upper() in _MULTI_MARKET_LABELS:
            return True
    return False


# The positional parameters below are underscore-prefixed on purpose. Both
# functions take arbitrary evidence as **kwargs, and an evidence key that
# happened to match a parameter name raised
# `TypeError: _refusal() got multiple values for argument 'reason'` at the one
# call site that passed the contract parser's own `reason` through -- a crash
# reachable from any ticker whose series is known but whose suffix is malformed.
# Prefixing makes the collision impossible rather than relying on nobody
# choosing an unlucky key.
def _refusal(_reason, **evidence):
    return {
        "side": None,
        "basis": None,
        "refusalReason": _reason,
        "refusalClass": REFUSAL_CLASS_BY_REASON.get(_reason),
        "expression": evidence.pop("expression", None),
        "contract": evidence.pop("contract", None),
        "evidence": evidence,
    }


def _resolved(_side, _basis, _expression, _contract, **evidence):
    return {
        "side": _side,
        "basis": _basis,
        "refusalReason": None,
        "refusalClass": None,
        "expression": _expression,
        "contract": _contract,
        "evidence": evidence,
    }


def build_expression(wager, *, away=None, home=None):
    """
    Stage A. The CLAIM this row records, restated in the canonical vocabulary.

    Returns (expression, refusal_or_None). The expression is:

        {"selection", "direction", "threshold", "horizon", "family",
         "declaredContractSide", "orientation", "sources"}

    Every field is either proven from a named field on the row or None. This
    function never consults a price, a model probability, a recommendation
    label or free text -- a side chosen from the price is not a side, it is a
    bet on the price.

    `away`/`home` resolve an orientation to a team. When they are unknown, an
    orientation stays an orientation and the caller refuses rather than
    guessing which club 'AWAY' meant.
    """
    # Callers supply away/home from their own parsers, and some of those are
    # permissive -- `clv_update.parse_game` ultimately falls back to "first 3
    # characters uppercased" and so returns a confident-looking abbreviation for
    # any string at all. Normalizing on the way IN means an unrecognized club
    # arrives here as None (missing evidence) rather than as a plausible-looking
    # string that would later be reported as a CONTRADICTION with the contract's
    # team. The refusal CLASS has to stay truthful, not just the refusal.
    away, home = normalize_team(away), normalize_team(home)

    expression = {
        "selection": None, "direction": None, "threshold": None,
        "horizon": None, "family": None, "declaredContractSide": None,
        "orientation": None, "sources": {},
    }

    label = read_market_label(wager.get("market"))
    if label is not None:
        horizon, family, orientation, direction = label
        expression["horizon"] = horizon
        expression["family"] = family
        expression["orientation"] = orientation
        expression["direction"] = direction
        expression["sources"]["market"] = wager.get("market")

    # Recorded side/selection fields. Each may state a contract side, an
    # orientation, a direction, a team -- or several at once.
    declared_sides = {}
    orientations = {}
    selections = {}
    directions = {}
    for field, raw in _collect_side_tokens(wager):
        read = _read_token(raw)
        if read is None:
            # An unreadable value is recorded as such rather than skipped: a
            # row that says something we cannot parse is not a row that said
            # nothing.
            expression["sources"].setdefault("unreadable", []).append({field: raw})
            continue
        expression["sources"][field] = raw
        if "contractSide" in read:
            declared_sides[field] = read["contractSide"]
        if "orientation" in read:
            orientations[field] = read["orientation"]
        if "selection" in read:
            selections[field] = read["selection"]
        if "direction" in read:
            directions[field] = read["direction"]
            if read.get("family") == "RFI":
                expression["family"] = expression["family"] or "RFI"
                expression["horizon"] = expression["horizon"] or HORIZON_FIRST_INNING

    # Two recorded fields that state DIFFERENT contract sides, orientations or
    # teams is a positive contradiction, not a tie to be broken.
    for bucket, reason in ((declared_sides, SIDE_CONTRADICTED_FIELDS_DISAGREE),
                           (orientations, SIDE_CONTRADICTED_FIELDS_DISAGREE),
                           (directions, SIDE_CONTRADICTED_FIELDS_DISAGREE)):
        if len(set(bucket.values())) > 1:
            return expression, _refusal(reason, disagreeingFields=dict(bucket),
                                        expression=expression)
    if len({normalize_team(v) for v in selections.values()}) > 1:
        return expression, _refusal(SIDE_CONTRADICTED_FIELDS_DISAGREE,
                                    disagreeingFields=dict(selections),
                                    expression=expression)

    if declared_sides:
        expression["declaredContractSide"] = next(iter(declared_sides.values()))
    if orientations:
        recorded = next(iter(orientations.values()))
        if expression["orientation"] and expression["orientation"] != recorded:
            return expression, _refusal(
                SIDE_CONTRADICTED_FIELDS_DISAGREE,
                marketLabelOrientation=expression["orientation"],
                recordedOrientation=recorded, expression=expression)
        expression["orientation"] = recorded
    if selections:
        expression["selection"] = next(iter(selections.values()))
    if directions:
        recorded = next(iter(directions.values()))
        if expression["direction"] and expression["direction"] != recorded:
            return expression, _refusal(
                SIDE_CONTRADICTED_FIELDS_DISAGREE,
                marketLabelDirection=expression["direction"],
                recordedDirection=recorded, expression=expression)
        expression["direction"] = recorded

    # An orientation names a team once -- and only once -- the matchup is known.
    if expression["orientation"] and expression["selection"] is None:
        if away is None or home is None:
            away, home = parse_game_matchup(wager.get("game"))
        if away is None or home is None:
            expression["sources"]["orientationUnresolved"] = expression["orientation"]
        else:
            expression["selection"] = away if expression["orientation"] == ORIENTATION_AWAY else home
            expression["sources"]["orientationResolvedFrom"] = wager.get("game")

    # An orientation AND a team that disagree is a contradiction, not a
    # preference. 'AWAY' on a row whose recorded team is the home club is
    # exactly the shape a mis-sided wager takes.
    if expression["orientation"] and expression["selection"] and (away or home):
        expected = away if expression["orientation"] == ORIENTATION_AWAY else home
        if expected and not same_team(expected, expression["selection"]):
            return expression, _refusal(
                SIDE_CONTRADICTED_FIELDS_DISAGREE,
                orientation=expression["orientation"], orientationTeam=expected,
                recordedSelection=expression["selection"], expression=expression)

    threshold = _as_float(wager.get("line"))
    if threshold is None:
        threshold = _as_float(wager.get("threshold"))
    expression["threshold"] = threshold

    # ── The `bet` string: read WHOLE, used to FILL, never to OVERRIDE.
    #
    # It is the only place many older rows ever recorded a team or a direction,
    # so discarding it would be discarding evidence -- but it is also the field
    # the old substring matcher abused, so the rules around it are strict:
    # read_bet_string() returns nothing at all unless every token parses, and a
    # value it supplies that DISAGREES with a named field is a contradiction,
    # not a tie-break. A named field always wins ties it cannot lose.
    parsed_bet = read_bet_string(wager.get("bet"))
    if parsed_bet:
        expression["sources"]["bet"] = wager.get("bet")
        expression["sources"]["betParsed"] = dict(parsed_bet)
        for field, key in (("selection", "selection"), ("direction", "direction"),
                           ("orientation", "orientation"), ("family", "family"),
                           ("horizon", "horizon"), ("threshold", "threshold"),
                           ("declaredContractSide", "contractSide")):
            supplied = parsed_bet.get(key)
            if supplied is None:
                continue
            current = expression.get(field)
            if current is None:
                expression[field] = supplied
            elif field == "selection":
                if not same_team(current, supplied):
                    return expression, _refusal(
                        SIDE_CONTRADICTED_FIELDS_DISAGREE, recordedSelection=current,
                        betStringSelection=supplied, betString=wager.get("bet"),
                        expression=expression)
            elif current != supplied:
                return expression, _refusal(
                    SIDE_CONTRADICTED_FIELDS_DISAGREE, field=field, recorded=current,
                    betStringValue=supplied, betString=wager.get("bet"),
                    expression=expression)
        # An orientation the bet string supplied still has to be turned into a
        # club, and still refuses if the matchup cannot be read.
        if expression["orientation"] and expression["selection"] is None and away and home:
            expression["selection"] = (away if expression["orientation"] == ORIENTATION_AWAY
                                       else home)
            expression["sources"]["orientationResolvedFrom"] = wager.get("game")
    elif wager.get("bet"):
        expression["sources"].setdefault("unreadable", []).append({"bet": wager.get("bet")})

    return expression, None


# ─────────────────────────────────────────────────────────────────────────────
# Stage B -- the contract side
# ─────────────────────────────────────────────────────────────────────────────
# Maps this repository's direction vocabulary onto the one question the
# contract can answer: does the claim say the contract's YES condition HOLDS,
# or that it does NOT? A direction with no entry here cannot be checked against
# anything, and an unchecked direction is not evidence.
_DIRECTION_ASSERTS = {
    DIRECTION_WIN: True,
    DIRECTION_TIE: True,
    DIRECTION_OVER: True,
    DIRECTION_EVENT_OCCURS: True,
    DIRECTION_UNDER: False,
    DIRECTION_EVENT_DOES_NOT_OCCUR: False,
}

# The exchange condition each expression family must be describing. An
# expression labelled TEAM_TOTAL that lands on a winning-margin contract is a
# different trade wearing the right label.
_FAMILY_CONDITION = {
    "MONEYLINE": kmcp.CONDITION_TEAM_WINS,
    "PERIOD_TIE": kmcp.CONDITION_PERIOD_TIE,
    "RUN_LINE": kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST,
    "GAME_TOTAL": kmcp.CONDITION_COMBINED_RUNS_AT_LEAST,
    "TEAM_TOTAL": kmcp.CONDITION_TEAM_RUNS_AT_LEAST,
    "RFI": kmcp.CONDITION_FIRST_INNING_RUNS_AT_LEAST,
}

# How each family's recorded `line` relates to the contract's own integer rung.
# Reuses W1-C's measured conventions verbatim rather than restating them: a run
# line of 1.5 and a ticker ending -BOS2 ARE the same contract, and a team total
# of 1.5 against -BOS2 is not.
_LEDGER_THRESHOLD_CONVENTION = dict(mi.LEDGER_THRESHOLD_CONVENTION)
_LEDGER_THRESHOLD_CONVENTION.setdefault("GAME_TOTAL", mi.MINIMUM_INCLUSIVE)


def _ledger_threshold_to_rung(family, threshold):
    """
    A recorded line, restated as the contract's own minimum-inclusive rung.

    None when the family has no strike, when no line was recorded, or when the
    line does not land on a whole-number rung under its own family's
    convention -- a run line of 2.0 is not a "wins by over X.5" line at all,
    and rounding it to the nearest rung would be inventing a contract.
    """
    convention = _LEDGER_THRESHOLD_CONVENTION.get(family)
    if convention is None or threshold is None:
        return None
    if convention == mi.MINIMUM_INCLUSIVE:
        rung = float(threshold)
    elif convention == mi.HALF_POINT_BELOW:
        rung = float(threshold) + 0.5
    else:
        return None
    if abs(rung - round(rung)) > 1e-9:
        return None
    return int(round(rung))


_CONDITION_HORIZON_BY_SERIES = {
    series: semantics[0] for series, semantics in mi.SERIES_SEMANTICS.items()
}


def resolve_wager_side(wager, *, away=None, home=None, contract=None):
    """
    THE canonical bet-side resolver. Which end of which contract did this row
    buy?

    Returns a dict:

        {"side": "YES"|"NO"|None,
         "basis": <why, when side is not None>,
         "refusalReason": <machine-readable, when side is None>,
         "refusalClass": MISSING_EVIDENCE|CONTRADICTION|DEFERRED|None,
         "expression": <the Stage-A claim>,
         "contract": <the W1-C parsed contract condition>,
         "evidence": {...}}

    SYMMETRY. An explicit NO is resolved by exactly the same rules as an
    explicit YES, and every rule below is stated over (condition, direction)
    rather than over a preferred side. There is no branch that reaches YES with
    less evidence than it takes to reach NO, and no field whose absence
    produces a side of any kind.

    `contract` may be supplied to skip re-parsing the ticker; when omitted it
    is parsed from the row's own marketTicker/ticker via W1-C's
    kalshi_mlb_contract_parser. This module never parses a ticker itself.
    """
    wager = wager or {}

    # DEFERRALS come first: a player prop has no generic settlement semantics
    # in this repository at all, and answering "which side" for it would be
    # the first step toward inventing some.
    if is_multi_market(wager):
        return _refusal(SIDE_DEFERRED_MULTI_MARKET_COMBO,
                        market=wager.get("market"),
                        marketFamily=wager.get("marketFamily"))
    if is_player_prop(wager):
        return _refusal(SIDE_DEFERRED_PLAYER_PROP,
                        market=wager.get("market"),
                        marketFamily=wager.get("marketFamily"),
                        issue="https://github.com/chmoses98/edge-finder-api/issues/43")

    # Same normalization as build_expression, for the same reason: a club a
    # caller's own parser invented must not survive into the team gate below.
    away, home = normalize_team(away), normalize_team(home)
    if away is None or home is None:
        away, home = parse_game_matchup(wager.get("game"))

    expression, refusal = build_expression(wager, away=away, home=home)
    if refusal is not None:
        return refusal

    ticker = wager.get("marketTicker") or wager.get("ticker")
    if contract is None:
        if not ticker:
            return _refusal(SIDE_UNPROVEN_NO_CONTRACT, expression=expression)
        contract = kmcp.parse_contract_condition(ticker)

    if contract.get("parseStatus") == kmcp.PARSE_STATUS_SERIES_NOT_DESCRIBED:
        return _refusal(SIDE_UNPROVEN_SERIES_NOT_DESCRIBED, expression=expression,
                        contract=contract, seriesTicker=contract.get("seriesTicker"))
    if contract.get("parseStatus") != kmcp.PARSE_STATUS_PARSED:
        return _refusal(SIDE_UNPROVEN_CONTRACT_UNPARSEABLE, expression=expression,
                        contract=contract, parseReason=contract.get("reason"))

    condition = contract.get("condition")
    contract_team = contract.get("selection")
    contract_rung = contract.get("minimumInclusive")
    contract_horizon = _CONDITION_HORIZON_BY_SERIES.get(contract.get("seriesTicker"))

    # ── The horizon gate. A claim about the first five innings settled against
    # a full-game contract is a different bet, and the two disagree exactly
    # whenever the game turns over after the 5th.
    if expression["horizon"] and contract_horizon and expression["horizon"] != contract_horizon:
        return _refusal(SIDE_CONTRADICTED_HORIZON, expression=expression, contract=contract,
                        expressionHorizon=expression["horizon"],
                        contractHorizon=contract_horizon)

    # ── The family gate. An expression family that is not this contract's own
    # condition is a mislabelled trade, whichever side it claims.
    expression_condition = _FAMILY_CONDITION.get(expression["family"])
    if expression_condition is not None and expression_condition != condition:
        # The one legitimate crossing: a MONEYLINE claim landing on the event's
        # explicit TIE contract is not a moneyline at all. It is still refused
        # -- the row has to say TIE to buy TIE.
        return _refusal(SIDE_CONTRADICTED_FAMILY, expression=expression, contract=contract,
                        expressionCondition=expression_condition,
                        contractCondition=condition)

    declared = expression["declaredContractSide"]

    # ── Team gate. Every condition except the game-level ones names a club,
    # and the row must name the same one.
    # A value the row DID record but nobody can read is its own finding, and a
    # louder one than "nothing was recorded": it means a side exists upstream
    # in a spelling this vocabulary has never seen, and settling the row would
    # be settling a sentence we did not understand.
    unreadable = expression["sources"].get("unreadable")
    if unreadable and expression["declaredContractSide"] is None and expression["selection"] is None:
        return _refusal(SIDE_UNPROVEN_UNRECOGNIZED_TOKEN, expression=expression,
                        contract=contract, unreadableValues=unreadable)

    team_required = condition in (kmcp.CONDITION_TEAM_WINS,
                                  kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST,
                                  kmcp.CONDITION_TEAM_RUNS_AT_LEAST)
    selection = expression["selection"]
    selection_matches_contract = None
    if team_required:
        if selection is None:
            if declared is None:
                return _refusal(SIDE_UNPROVEN_NO_SELECTION, expression=expression,
                                contract=contract, contractTeam=contract_team)
        else:
            selection_matches_contract = same_team(selection, contract_team)
            if not selection_matches_contract:
                # The row names a club that is not this contract's. Two
                # different things can be true of that, and they are not the
                # same finding:
                #
                #   * the club is not even in this event -> flat contradiction;
                #   * the club is the SIBLING in this event -> a complement
                #     question, and only for a WINS condition. "DET scores 4+"
                #     is not the complement of "COL scores 4+" (both can be
                #     true), and "DET wins by 2+" is not the complement of
                #     "COL wins by 2+" either. For those families a sibling
                #     club is simply a different contract, and settling one
                #     from the other is the substitution this subwave exists
                #     to stop.
                event_teams = _event_teams(contract)
                if event_teams and not any(same_team(selection, t) for t in event_teams):
                    return _refusal(SIDE_CONTRADICTED_TEAM_NOT_ON_THIS_CONTRACT,
                                    expression=expression, contract=contract,
                                    selection=selection, contractTeam=contract_team,
                                    eventTeams=sorted(event_teams))
                if condition != kmcp.CONDITION_TEAM_WINS:
                    return _refusal(SIDE_CONTRADICTED_SIBLING_TEAM_IS_A_DIFFERENT_CONTRACT,
                                    expression=expression, contract=contract,
                                    selection=selection, contractTeam=contract_team,
                                    contractCondition=condition,
                                    note="a sibling club's threshold contract is a "
                                         "different contract, never a complement")

    # ── Strike gate. A threshold family must land on this contract's own rung.
    if expression["family"] in _LEDGER_THRESHOLD_CONVENTION and contract_rung is not None:
        claimed_rung = _ledger_threshold_to_rung(expression["family"], expression["threshold"])
        if claimed_rung is None:
            if declared is None:
                return _refusal(SIDE_UNPROVEN_NO_THRESHOLD, expression=expression,
                                contract=contract, recordedLine=expression["threshold"],
                                contractRung=contract_rung)
        elif claimed_rung != contract_rung:
            return _refusal(SIDE_CONTRADICTED_THRESHOLD, expression=expression,
                            contract=contract, claimedRung=claimed_rung,
                            contractRung=contract_rung,
                            recordedLine=expression["threshold"])

    # ── Direction -> side. This is the whole resolution, and it is one
    # symmetric statement: the claim either asserts the contract's YES
    # condition or negates it.
    direction = expression["direction"]
    asserts = _DIRECTION_ASSERTS.get(direction) if direction else None

    inferred = None
    inferred_basis = None
    if asserts is not None:
        if team_required and selection is not None and selection_matches_contract is False:
            # The row backs the OTHER club in this event, on a WINS condition
            # (every other family already refused above). That is this
            # contract's NO only where the event space is PROVEN to have
            # exactly two outcomes. A full MLB game qualifies; F3/F5/F7 do not,
            # because the segment can end tied and Kalshi lists a separate TIE
            # contract in the same event.
            #
            # A row that could not be corroborated this way is NOT graded from
            # the expression -- but neither does it override a side the row
            # states outright. Inability to corroborate is recorded as such
            # below; only a positive contradiction refuses.
            if not is_binary_event_space(condition, contract_horizon):
                if declared is None:
                    return _refusal(SIDE_UNPROVEN_SEGMENT_IS_NOT_BINARY,
                                    expression=expression, contract=contract,
                                    selection=selection, contractTeam=contract_team,
                                    contractHorizon=contract_horizon,
                                    contractCondition=condition)
            elif not asserts:
                # "The other club does NOT win" is not the complement of "this
                # club wins" -- on a two-outcome space it is a restatement of
                # this club winning, but the row has expressed it the long way
                # round and there is no reason to accept the extra inversion.
                if declared is None:
                    return _refusal(SIDE_UNPROVEN_COMPLEMENT_NOT_PROVEN_BINARY,
                                    expression=expression, contract=contract,
                                    selection=selection, contractTeam=contract_team)
            else:
                inferred, inferred_basis = SIDE_NO, BASIS_BINARY_COMPLEMENT
        elif team_required and selection is None:
            # The contract names a club and the row does not. A direction alone
            # ("ML" implies WIN) says the row asserts SOME team's winning -- it
            # does not say it asserts THIS team's, so it corroborates nothing.
            # Without this branch a row declaring side NO on a moneyline ticker
            # would be read as contradicting an inference drawn from a claim it
            # never made, and an explicit NO would be harder to honour than an
            # explicit YES. It stays uncorroborated instead; if no side was
            # declared either, the missing-selection refusal above already fired.
            inferred, inferred_basis = None, None
        elif asserts:
            inferred, inferred_basis = SIDE_YES, BASIS_EXPRESSION_ASSERTS_CONTRACT_CONDITION
        else:
            # Negating a THRESHOLD or EVENT condition is the contract's own NO
            # on the SAME ticker -- no cross-contract reasoning, so no tie can
            # hide in it. Negating a WINS condition on this club's own contract
            # ("this club does not win") is only the same as the sibling's YES
            # where the space is binary, and that is not what is being asked
            # here: NO on this ticker is always well defined.
            inferred, inferred_basis = SIDE_NO, BASIS_EXPRESSION_NEGATES_CONTRACT_CONDITION

    # ── Reconcile a declared side with the row's own expression.
    if declared is not None and inferred is not None and declared != inferred:
        return _refusal(SIDE_CONTRADICTED_DECLARED_VS_EXPRESSION,
                        expression=expression, contract=contract,
                        declaredSide=declared, expressionImpliesSide=inferred)
    if declared is not None:
        return _resolved(declared, BASIS_DECLARED_CONTRACT_SIDE, expression, contract,
                         corroboratedByExpression=inferred is not None,
                         contractTeam=contract_team, contractRung=contract_rung,
                         contractCondition=condition, contractHorizon=contract_horizon,
                         marketTicker=ticker)
    if inferred is not None:
        return _resolved(inferred, inferred_basis, expression, contract,
                         contractTeam=contract_team, contractRung=contract_rung,
                         contractCondition=condition, contractHorizon=contract_horizon,
                         marketTicker=ticker)

    if direction is None:
        # A market named without a direction. "Total" says which market; it
        # does not say Over or Under, and the only things that could supply the
        # missing direction are the model's probability or the market's price,
        # both forbidden as side evidence.
        return _refusal(SIDE_UNPROVEN_NO_DIRECTION, expression=expression, contract=contract)
    if not _collect_side_tokens(wager) and expression["family"] is None:
        return _refusal(SIDE_UNPROVEN_NO_SIDE_EVIDENCE, expression=expression, contract=contract)
    return _refusal(SIDE_UNPROVEN_UNRECOGNIZED_TOKEN, expression=expression, contract=contract,
                    direction=direction)


def _event_teams(contract):
    """The two clubs this contract's own event suffix encodes, as a set."""
    series = contract.get("seriesTicker")
    suffix = contract.get("eventTickerSuffix")
    if not series or not suffix:
        return set()
    event = kmcp.parse_event_suffix(series, "%s-%s" % (series, suffix))
    return {t for t in (event.get("away"), event.get("home")) if t}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal truth -> canonical wager outcome
# ─────────────────────────────────────────────────────────────────────────────

def resolve_wager_outcome(side_resolution, market_settlement, *, wager=None):
    """
    The last link. Given a proven side and terminal truth for the SAME exact
    contract, what did this wager do?

    `side_resolution` is resolve_wager_side()'s own return value.
    `market_settlement` is the settlement record / tuple for the contract:
        {"marketTicker", "settlementStatus", "result", "gameId",
         "unavailableReason"}
      -- i.e. exactly the shape lib.edgelab.settlement.settle_market() produces
      and data/edgelab/schema_v1/settlement.schema.json stores. `result` is
      whether the YES side of THAT EXACT TICKER settled true.

    Returns:
        {"outcome": <OUTCOME_*>,
         "side": ..., "settlementResult": ...,
         "refusalReason": ..., "refusalClass": ...,
         "ledgerResult": "WIN"|"LOSS"|"PUSH"|"VOID"|None,
         "ledgerStatus": "settled"|"void"|"pending",
         "evidence": {...}}

    A wager is called WON or LOST only when the contract, the side and the
    truth are all proven and all agree about which contract they are talking
    about. Anything else is NOT_TERMINAL (truth does not exist yet) or
    UNRESOLVED (we refuse), and those two are never merged.
    """
    wager = wager or {}
    side_resolution = side_resolution or {}
    market_settlement = market_settlement or {}

    side = side_resolution.get("side")
    status = market_settlement.get("settlementStatus")
    result = market_settlement.get("result")

    evidence = {
        "marketTicker": market_settlement.get("marketTicker"),
        "wagerTicker": wager.get("marketTicker") or wager.get("ticker"),
        "settlementStatus": status,
        "settlementResult": result,
        "sideBasis": side_resolution.get("basis"),
    }

    # ── Contradiction gate, BEFORE any grade. Terminal truth for a different
    # contract is not this wager's truth, however plausible the match looks.
    # This is the settlement-side form of W1-C's exclusivity invariant: one
    # ticker, one physical game, one settlement.
    wager_ticker = evidence["wagerTicker"]
    settlement_ticker = evidence["marketTicker"]
    if wager_ticker and settlement_ticker and str(wager_ticker) != str(settlement_ticker):
        return _outcome(OUTCOME_UNRESOLVED, side, result,
                        SETTLEMENT_CONTRADICTED_TICKER, evidence)
    wager_game = wager.get("gameId") or wager.get("gamePk")
    settlement_game = market_settlement.get("gameId")
    if wager_game and settlement_game and str(wager_game) != str(settlement_game):
        evidence["wagerGameId"] = str(wager_game)
        evidence["settlementGameId"] = str(settlement_game)
        return _outcome(OUTCOME_UNRESOLVED, side, result,
                        SETTLEMENT_CONTRADICTED_GAME, evidence)

    # ── Deferrals propagate as deferrals, never as a grade and never as a
    # plain "pending" that a reader would mistake for "the game is still on".
    if side_resolution.get("refusalClass") == REFUSAL_DEFERRED:
        reason = (SETTLEMENT_DEFERRED_PLAYER_PROP
                  if side_resolution.get("refusalReason") == SIDE_DEFERRED_PLAYER_PROP
                  else side_resolution.get("refusalReason"))
        return _outcome(OUTCOME_UNRESOLVED, side, result, reason, evidence)

    # ── A voided contract voids the wager regardless of side: nothing was
    # owned at settlement, so no side is needed to know the stake came back.
    if status == "VOID":
        return _outcome(OUTCOME_VOID, side, result, None, evidence)

    if side not in VALID_SIDES:
        evidence["sideRefusalReason"] = side_resolution.get("refusalReason")
        evidence["sideRefusalClass"] = side_resolution.get("refusalClass")
        return _outcome(OUTCOME_UNRESOLVED, side, result, SETTLEMENT_UNPROVEN_SIDE, evidence)

    if status is None:
        return _outcome(OUTCOME_NOT_TERMINAL, side, result,
                        SETTLEMENT_UNPROVEN_NO_MARKET_SETTLEMENT, evidence)
    if status != "SETTLED":
        # SETTLEMENT_UNRESOLVED / UNAVAILABLE. Truth is not established; the
        # market's own reason is carried through rather than re-invented.
        evidence["marketUnavailableReason"] = market_settlement.get("unavailableReason")
        return _outcome(OUTCOME_NOT_TERMINAL, side, result,
                        SETTLEMENT_UNPROVEN_NO_MARKET_SETTLEMENT, evidence)
    if result not in VALID_SIDES:
        return _outcome(OUTCOME_UNRESOLVED, side, result,
                        SETTLEMENT_UNPROVEN_MARKET_RESULT_MISSING, evidence)

    return _outcome(OUTCOME_WON if result == side else OUTCOME_LOST,
                    side, result, None, evidence)


def _outcome(outcome, side, settlement_result, refusal_reason, evidence):
    return {
        "outcome": outcome,
        "side": side,
        "settlementResult": settlement_result,
        "refusalReason": refusal_reason,
        "refusalClass": REFUSAL_CLASS_BY_REASON.get(refusal_reason) if refusal_reason else None,
        "ledgerResult": LEDGER_RESULT_BY_OUTCOME[outcome],
        "ledgerStatus": LEDGER_STATUS_BY_OUTCOME[outcome],
        "evidence": evidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Legacy vocabulary normalization
# ─────────────────────────────────────────────────────────────────────────────
# The root ledger records the same six facts in twelve `status` spellings and
# six `result` spellings, and `status` additionally carries two things that are
# not statuses at all: an OUTCOME (WIN/LOSS/PUSH/VOID, 60 rows) and a BET CLASS
# (PAPER, 23 rows). Measured on bets.json as committed.
#
# These maps read that history. They do NOT rewrite it: normalize_* returns the
# canonical reading and the caller keeps the raw value beside it, so "what did
# the source actually say" and "what did we decide it meant" are both
# answerable afterwards.
_RESULT_ALIASES = {
    "WIN": OUTCOME_WON, "WON": OUTCOME_WON, "W": OUTCOME_WON,
    "LOSS": OUTCOME_LOST, "LOST": OUTCOME_LOST, "L": OUTCOME_LOST, "LOSE": OUTCOME_LOST,
    "PUSH": OUTCOME_PUSH, "TIE": OUTCOME_PUSH,
    "VOID": OUTCOME_VOID, "VOIDED": OUTCOME_VOID,
    "CANCELLED": OUTCOME_VOID, "CANCELED": OUTCOME_VOID,
    "NO_ACTION": OUTCOME_VOID, "NO ACTION": OUTCOME_VOID,
    "PENDING": OUTCOME_NOT_TERMINAL, "OPEN": OUTCOME_NOT_TERMINAL,
    "UNRESOLVED": OUTCOME_UNRESOLVED,
}

# `status` values that state a LIFECYCLE rather than an outcome. These say
# nothing about who won and must never be read as if they did.
_LIFECYCLE_STATUSES = {
    "SETTLED": "settled", "OPEN": "pending", "PENDING": "pending",
    "VOID": "void", "VOIDED": "void",
}

# `status` values that state a BET CLASS. 'PAPER' is not a settlement state; it
# says the wager was not real money. It is reported, never graded from.
_BET_CLASS_STATUSES = {"PAPER", "PAPER_DOWNGRADED", "REAL"}


def normalize_result(raw):
    """
    A recorded result/status string -> a canonical outcome, or None.

    None means "this string does not state an outcome" -- which covers both an
    unrecognized spelling and a value like 'SETTLED' or 'PAPER' that is a
    lifecycle or a bet class. Returning None rather than a default is the
    point: a reader must not be able to turn 'PAPER' into a win.
    """
    if raw is None:
        return None
    return _RESULT_ALIASES.get(str(raw).strip().upper())


def normalize_lifecycle(raw):
    """A recorded status string -> 'settled'|'pending'|'void', or None."""
    if raw is None:
        return None
    token = str(raw).strip().upper()
    if token in _LIFECYCLE_STATUSES:
        return _LIFECYCLE_STATUSES[token]
    # A status that states an OUTCOME also states that the wager is finished.
    outcome = _RESULT_ALIASES.get(token)
    if outcome in (OUTCOME_WON, OUTCOME_LOST, OUTCOME_PUSH):
        return "settled"
    if outcome == OUTCOME_VOID:
        return "void"
    if outcome == OUTCOME_NOT_TERMINAL:
        return "pending"
    return None


def is_bet_class_status(raw):
    """True for a `status` value that names a bet class, not a settlement state."""
    return raw is not None and str(raw).strip().upper() in _BET_CLASS_STATUSES


def normalize_row_semantics(row):
    """
    One ledger row -> its canonical settlement reading, with the raw values
    preserved beside it.

    Additive by construction: the returned dict names the raw fields it read
    and the canonical meaning it assigned, and a caller merging it onto a row
    never overwrites `result`/`status`. This is the shape that makes both
    questions answerable after normalization:

        rawResult / rawStatus          -- what did the source actually say?
        canonicalOutcome / canonicalLifecycle
                                       -- what meaning did we assign?
    """
    raw_result = row.get("result")
    raw_status = row.get("status")
    outcome = normalize_result(raw_result)
    if outcome is None:
        outcome = normalize_result(raw_status)
    lifecycle = normalize_lifecycle(raw_status)
    if lifecycle is None and outcome is not None:
        lifecycle = LEDGER_STATUS_BY_OUTCOME[outcome]
    return {
        "rawResult": raw_result,
        "rawStatus": raw_status,
        "canonicalOutcome": outcome,
        "canonicalLifecycle": lifecycle,
        "statusIsBetClass": is_bet_class_status(raw_status),
    }
