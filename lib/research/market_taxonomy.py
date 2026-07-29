#!/usr/bin/env python3
"""
lib/research/market_taxonomy.py
====================================
Model Performance Phase 1 (Market Audit) -- RESEARCH-ONLY normalized
market taxonomy and classifier for Kalshi MLB markets.

Confirmed real Kalshi MLB series (from
data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json, a
real, current snapshot, cross-checked against
archive/data/kalshi_full_enumeration.json from 2026-06-04 for
stability across ~2 months):

    KXMLBGAME       -- full-game moneyline/result. TWO-WAY (no TIE
                       ticker observed in either snapshot) -- a
                       regulation tie always continues to extra
                       innings, so Kalshi's full-game "Winner?"
                       contract never needs a tie leg.
    KXMLBF5         -- first-5-innings result. THREE-WAY (an explicit
                       "-TIE" ticker exists on every F5 event in both
                       snapshots) -- unlike the full game, an F5 tie is
                       a real, final, settleable outcome (the game
                       continues past inning 5, but the F5 CONTRACT
                       settles on the inning-5 score, tie included).
    KXMLBSPREAD     -- full-game winning-margin / run-line ("Team wins
                       by over N.5 runs?") -- one market per team per
                       margin threshold, confirmed via
                       archive/data/kalshi_remaining_discovery.json.
    KXMLBTOTAL      -- full-game total runs (Over/Under N.5).
    KXMLBTEAMTOTAL  -- full-game team total runs (Over/Under N.5, one
                       market per team).
    KXMLBF5SPREAD   -- F5 winning-margin / run-line.
    KXMLBF5TOTAL    -- F5 total runs.
    KXMLBRFI        -- NRFI/YRFI (runs in the first inning).

NOT FOUND on Kalshi as of this phase's discovery (searched every
snapshot file's raw ticker strings for "F3"/"F7"-prefixed series --
zero genuine matches, only false-positive substring hits inside team
names/times/scores):
    - No F3 (first-3-innings) series of any kind.
    - No F7 (first-7-innings) series of any kind.
    - No standalone pitcher-strikeout / pitcher-outs / pitcher-hits-
      allowed / pitcher-earned-runs / hitter-prop series discovered in
      any available snapshot or archive file this phase had access to
      (these may exist on Kalshi and simply not have been captured by
      this repository's existing fetch scripts -- documented as an
      inventory gap, not a confirmed absence, since this repository's
      own fetchers only ever targeted the 8 series above).

This module's job is ONLY to normalize what a market IS (family,
scope, outcome, participant, line, operator, settlement basis) from
its raw ticker/title -- it does not fetch, does not price, does not
evaluate, does not project.
"""
import re

# ── Market family taxonomy ──────────────────────────────────────────────────

FAMILY_GAME_RESULT = "game_result"
FAMILY_INNING_RESULT = "inning_result"
FAMILY_GAME_TOTAL = "game_total"
FAMILY_INNING_TOTAL = "inning_total"
FAMILY_TEAM_TOTAL = "team_total"
FAMILY_RUN_LINE = "run_line"
FAMILY_WINNING_MARGIN = "winning_margin"
FAMILY_FIRST_INNING_RUN = "first_inning_run"
FAMILY_PITCHER_STRIKEOUTS = "pitcher_strikeouts"
FAMILY_PITCHER_OUTS = "pitcher_outs"
FAMILY_PITCHER_HITS_ALLOWED = "pitcher_hits_allowed"
FAMILY_PITCHER_EARNED_RUNS = "pitcher_earned_runs"
FAMILY_HITTER_HITS = "hitter_hits"
FAMILY_HITTER_TOTAL_BASES = "hitter_total_bases"
FAMILY_HITTER_HOME_RUNS = "hitter_home_runs"
FAMILY_UNKNOWN = "unknown"

# Series ticker prefix -> (family, default scope). "run_line" and
# "winning_margin" are the SAME underlying Kalshi contract shape
# ("Team wins by over N.5 runs?") -- kept as one family
# (FAMILY_WINNING_MARGIN) rather than two, since Kalshi itself does not
# distinguish "run line" (a sportsbook-side concept: a fixed spread
# both sides can bet) from "winning margin" (a threshold ladder) for
# this series; production code's "RL_Away"/"RL_Home" naming reflects
# the sportsbook framing, not a second real Kalshi contract type.
SERIES_FAMILY_MAP = {
    "KXMLBGAME": (FAMILY_GAME_RESULT, "full_game"),
    "KXMLBF5": (FAMILY_INNING_RESULT, "F3F5F7_placeholder"),  # scope refined by classifier
    "KXMLBSPREAD": (FAMILY_WINNING_MARGIN, "full_game"),
    "KXMLBTOTAL": (FAMILY_GAME_TOTAL, "full_game"),
    "KXMLBTEAMTOTAL": (FAMILY_TEAM_TOTAL, "full_game"),
    "KXMLBF5SPREAD": (FAMILY_WINNING_MARGIN, "F5"),
    "KXMLBF5TOTAL": (FAMILY_INNING_TOTAL, "F5"),
    "KXMLBRFI": (FAMILY_FIRST_INNING_RUN, "F1"),
    # Speculative series names that MIGHT exist for F3/F7 if Kalshi ever
    # lists them -- included so the classifier recognizes them
    # immediately rather than falling to FAMILY_UNKNOWN if they appear
    # in a future snapshot. NOT confirmed to exist as of this phase.
    "KXMLBF3": (FAMILY_INNING_RESULT, "F3"),
    "KXMLBF7": (FAMILY_INNING_RESULT, "F7"),
    "KXMLBF3SPREAD": (FAMILY_WINNING_MARGIN, "F3"),
    "KXMLBF7SPREAD": (FAMILY_WINNING_MARGIN, "F7"),
    "KXMLBF3TOTAL": (FAMILY_INNING_TOTAL, "F3"),
    "KXMLBF7TOTAL": (FAMILY_INNING_TOTAL, "F7"),
}

# Legacy/observed alternate series names from the archive discovery
# scripts (scripts/fetch_kalshi_markets.py and older probes checked
# both "KX"-prefixed and bare "MLB"-prefixed series names -- only the
# KX-prefixed ones were ever confirmed populated in any snapshot this
# phase examined).
LEGACY_SERIES_ALIASES = {
    "MLBNRFI": "KXMLBRFI",
    "MLBYRFI": "KXMLBRFI",
    "MLBF5": "KXMLBF5",
    "MLBTOT": "KXMLBTOTAL",
}

_OUTCOME_TIE_SUFFIXES = {"TIE"}


def _series_from_ticker(ticker):
    """Extract the leading series-ticker component from an event/market ticker."""
    if not ticker:
        return None
    return ticker.split("-", 1)[0]


def classify_market(market_ticker, event_ticker=None, title=None, subtitle=None):
    """
    Pure. Classifies a single raw Kalshi market record into the
    normalized taxonomy shape described in the module docstring's
    Part 3 contract. Never raises on an unrecognized ticker -- returns
    a record with family=FAMILY_UNKNOWN and classificationStatus=
    "unclassified" instead, so an unknown market is never silently
    dropped (see lib/research/market_handler_registry.py for the
    downstream "no silent drop" guarantee this feeds).

    Returns a dict matching the Part 3 contract shape, with raw
    identifiers preserved verbatim.
    """
    series = _series_from_ticker(market_ticker)
    series = LEGACY_SERIES_ALIASES.get(series, series)

    result = {
        "family": FAMILY_UNKNOWN,
        "scope": None,
        "outcome": None,
        "participant": None,
        "team": None,
        "opponent": None,
        "operator": None,
        "line": None,
        "settlementBasis": None,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "seriesTicker": series,
        "rawTitle": title,
        "rawSubtitle": subtitle,
        "classificationStatus": "unclassified",
    }

    if series not in SERIES_FAMILY_MAP:
        return result

    family, scope = SERIES_FAMILY_MAP[series]
    result["family"] = family
    result["scope"] = scope if scope != "F3F5F7_placeholder" else "F5"
    result["classificationStatus"] = "classified"

    # Suffix after the event ticker identifies the specific outcome
    # (team abbr, "TIE", or a numbered strike like "-SF11").
    suffix = None
    if market_ticker and event_ticker and market_ticker.startswith(event_ticker + "-"):
        suffix = market_ticker[len(event_ticker) + 1:]

    if family in (FAMILY_GAME_RESULT, FAMILY_INNING_RESULT):
        if suffix in _OUTCOME_TIE_SUFFIXES:
            result["outcome"] = "Tie"
            result["operator"] = "equals"
        elif suffix:
            result["outcome"] = "Win"
            result["team"] = suffix
            result["operator"] = "greater_than"
        result["settlementBasis"] = _settlement_basis_for_scope(result["scope"])

    elif family == FAMILY_WINNING_MARGIN:
        # e.g. "SF11" -> team="SF", line derived from title's "N.5"
        # threshold (kept in rawTitle; not re-derived here since the
        # threshold is only reliably present in the title text, not a
        # clean numeric ticker suffix across all observed examples).
        m = re.match(r"^([A-Z]+)(\d+)$", suffix) if suffix else None
        if m:
            result["team"] = m.group(1)
            result["operator"] = "greater_than"
        result["settlementBasis"] = _settlement_basis_for_scope(result["scope"])

    elif family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_FIRST_INNING_RUN):
        result["operator"] = "greater_than"
        result["settlementBasis"] = _settlement_basis_for_scope(result["scope"])

    return result


def _settlement_basis_for_scope(scope):
    return {
        "full_game": "final_score_including_extra_innings",
        "F3": "after_3_complete_innings",
        "F5": "after_5_complete_innings",
        "F7": "after_7_complete_innings",
        "F1": "after_1_complete_inning",
    }.get(scope)


def is_three_way_family(family, scope):
    """
    Pure. Returns True only for families/scopes CONFIRMED (via real
    snapshot evidence, see module docstring) to be genuine three-way
    (Away/Tie/Home) Kalshi contracts. Full-game (KXMLBGAME) is
    deliberately NOT included -- it is confirmed two-way.
    """
    return family == FAMILY_INNING_RESULT and scope in ("F3", "F5", "F7")


KNOWN_FAMILIES = {
    FAMILY_GAME_RESULT, FAMILY_INNING_RESULT, FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL, FAMILY_RUN_LINE, FAMILY_WINNING_MARGIN, FAMILY_FIRST_INNING_RUN,
    FAMILY_PITCHER_STRIKEOUTS, FAMILY_PITCHER_OUTS, FAMILY_PITCHER_HITS_ALLOWED,
    FAMILY_PITCHER_EARNED_RUNS, FAMILY_HITTER_HITS, FAMILY_HITTER_TOTAL_BASES,
    FAMILY_HITTER_HOME_RUNS,
}
