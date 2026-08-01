"""
lib/edgelab/market_family_mapping.py
========================================
The ONE maintainable location for canonicalizing every historical
spelling of "market family" EdgeLab has ever written into the 17-family
controlled vocabulary from lib.research.market_taxonomy. This exists
because PlacedBet.marketFamily is free text copied at ingestion time
from two different legacy ledgers (bets.json's config/rules.json market
names like "F5_ML_Away", data/bets.json's sportsbook-style abbreviations
like "ML"/"YRFI") plus raw Kalshi series tickers like "KXMLBGAME" --
measured directly against the real committed data/edgelab/bets/bets.jsonl,
these 3 conventions produce 11 different spellings for what is really
only 5 distinct families.

Deliberately NOT a set of scattered SQL CASE WHEN clauses -- every
canonicalizing view in lib/edgelab/analytics.py joins against ONE lookup
table built from this ONE dict, so adding support for a new observed
spelling is a one-line addition here, not a hunt through several SQL
strings.

To add a new mapping: add one entry below, run
tests/edgelab/test_analytics.py's canonicalization tests, and re-run
scripts/edgelab/run_analytics.py's unmapped-values audit to confirm the
new spelling no longer shows up there. Never repurpose an existing key's
meaning -- add a new key instead, so old runs' output stays explainable.

Matching is exact/case-sensitive (every spelling actually observed in
the real data is consistently cased) -- a differently-cased variant that
shows up in the future safely falls through to UNMAPPED (see
lib/edgelab/analytics.py's canonicalize_market_family() docstring),
never a silent guess, and gets added here once confirmed.
"""

from lib.research.market_taxonomy import (
    FAMILY_FIRST_INNING_RUN,
    FAMILY_GAME_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_HITTER_HITS,
    FAMILY_HITTER_HITS_RUNS_RBIS,
    FAMILY_HITTER_RBIS,
    FAMILY_HITTER_STOLEN_BASES,
    FAMILY_HITTER_TOTAL_BASES,
    FAMILY_INNING_RESULT,
    FAMILY_INNING_TOTAL,
    FAMILY_PITCHER_OUTS,
    FAMILY_PITCHER_STRIKEOUTS,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
)

# Sentinels -- never a guessed family name, always one of these two when
# the raw value can't be mapped to a real family.
UNKNOWN = "UNKNOWN"     # raw value is null/empty/a known "no value" placeholder (e.g. "N/A")
UNMAPPED = "UNMAPPED"    # raw value is a real, non-empty string not yet in MARKET_FAMILY_ALIASES

# Raw values treated as "no family recorded" rather than "an unrecognized
# spelling" -- these are placeholders some legacy record actually
# contains, not real attempts at naming a family.
_NULL_LIKE_RAW_VALUES = frozenset({"n/a", "na", "none", "null", "unknown", ""})

MARKET_FAMILY_ALIASES = {
    # Canonical taxonomy values pass through unchanged.
    FAMILY_GAME_RESULT: FAMILY_GAME_RESULT,
    FAMILY_INNING_RESULT: FAMILY_INNING_RESULT,
    FAMILY_GAME_TOTAL: FAMILY_GAME_TOTAL,
    FAMILY_INNING_TOTAL: FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL: FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN: FAMILY_WINNING_MARGIN,
    FAMILY_FIRST_INNING_RUN: FAMILY_FIRST_INNING_RUN,
    FAMILY_PITCHER_STRIKEOUTS: FAMILY_PITCHER_STRIKEOUTS,
    FAMILY_PITCHER_OUTS: FAMILY_PITCHER_OUTS,
    FAMILY_HITTER_HITS: FAMILY_HITTER_HITS,
    FAMILY_HITTER_TOTAL_BASES: FAMILY_HITTER_TOTAL_BASES,
    FAMILY_HITTER_RBIS: FAMILY_HITTER_RBIS,
    FAMILY_HITTER_STOLEN_BASES: FAMILY_HITTER_STOLEN_BASES,
    FAMILY_HITTER_HITS_RUNS_RBIS: FAMILY_HITTER_HITS_RUNS_RBIS,

    # Raw Kalshi series tickers -- PlacedBet.marketFamily sometimes copied
    # the series prefix directly instead of the model's own market name.
    "KXMLBGAME": FAMILY_GAME_RESULT,
    "KXMLBSPREAD": FAMILY_WINNING_MARGIN,
    "KXMLBTOTAL": FAMILY_GAME_TOTAL,
    "KXMLBTEAMTOTAL": FAMILY_TEAM_TOTAL,
    "KXMLBRFI": FAMILY_FIRST_INNING_RUN,
    "KXMLBF5": FAMILY_INNING_RESULT,
    "KXMLBF3": FAMILY_INNING_RESULT,
    "KXMLBF7": FAMILY_INNING_RESULT,
    "KXMLBF5SPREAD": FAMILY_WINNING_MARGIN,
    "KXMLBF5TOTAL": FAMILY_INNING_TOTAL,
    "KXMLBKS": FAMILY_PITCHER_STRIKEOUTS,
    "KXMLBOUTS": FAMILY_PITCHER_OUTS,
    "KXMLBHIT": FAMILY_HITTER_HITS,
    "KXMLBTB": FAMILY_HITTER_TOTAL_BASES,
    "KXMLBHRR": FAMILY_HITTER_HITS_RUNS_RBIS,
    "KXMLBRBI": FAMILY_HITTER_RBIS,
    "KXMLBSB": FAMILY_HITTER_STOLEN_BASES,

    # config/rules.json's 11-market model naming convention
    # (scripts/log_manual_bet.py / write_pending_bets.py's "market" field).
    "F5_ML_Away": FAMILY_INNING_RESULT,
    "F5_ML_Home": FAMILY_INNING_RESULT,
    "TT_Away_Over": FAMILY_TEAM_TOTAL,
    "TT_Home_Over": FAMILY_TEAM_TOTAL,
    "ML_Away": FAMILY_GAME_RESULT,
    "ML_Home": FAMILY_GAME_RESULT,
    "Game_Total": FAMILY_GAME_TOTAL,
    "RL_Away": FAMILY_WINNING_MARGIN,
    "RL_Home": FAMILY_WINNING_MARGIN,
    "NRFI": FAMILY_FIRST_INNING_RUN,
    "YRFI": FAMILY_FIRST_INNING_RUN,

    # Older, even-more-abbreviated sportsbook-style spellings observed in
    # the real committed bets.jsonl (data/bets.json's "market" field).
    "ML": FAMILY_GAME_RESULT,
    "F5 ML": FAMILY_INNING_RESULT,
}


def canonicalize_market_family(raw_value):
    """
    Pure Python reference implementation (the SQL view in
    lib/edgelab/analytics.py does the same thing via the family_mapping
    lookup table, for use inside DuckDB queries) -- kept in sync
    deliberately so this module's mapping is the single source of truth
    testable directly, not just indirectly through SQL.
    """
    if raw_value is None or str(raw_value).strip().lower() in _NULL_LIKE_RAW_VALUES:
        return UNKNOWN
    return MARKET_FAMILY_ALIASES.get(raw_value, UNMAPPED)
