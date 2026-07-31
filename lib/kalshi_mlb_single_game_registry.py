#!/usr/bin/env python3
"""
lib/kalshi_mlb_single_game_registry.py
==========================================
Strict single-game MLB market registry (Kalshi price-checker correction
mission).

Root cause this module fixes: the standalone daily MLB price checker must
never treat "ticker begins with KXMLB" or "title mentions MLB/baseball" as
sufficient grounds for inclusion (that broad heuristic lives in
scripts/discover_kalshi_series_catalogue.py and is correct for its own
audit/catalogue purpose, but is far too broad for a daily game-price
check). Evidence: a real live series-catalogue dispatch
(data/kalshi/discovery/2026-07-30_series_catalogue.json) found 179
"MLB-associated" series under that broad heuristic, of which only 17 are
genuine single-game (or single-game player-prop) MLB market families --
the other 162 are season-long leaders/awards, division/pennant/World
Series futures, season win totals, draft picks, trades, Home Run Derby,
streaks, other non-game novelty markets, and markets for OTHER
competitions entirely (World Baseball Classic, Mexican Baseball League,
college baseball, the Congressional Baseball Game) that merely share the
KXMLB ticker prefix or mention "baseball" in their title.

THE ALLOW DECISION IS ALLOWLIST-ONLY. `classify_series_for_price_check()`
below decides eligibility purely by membership in
`lib.research.market_taxonomy.CONFIRMED_SINGLE_GAME_SERIES_TICKERS` --
the set of series tickers this repository has directly confirmed, from
that same real catalogue dispatch, to be single-game/single-game-player-
prop MLB markets (deliberately excludes 4 speculative-but-unconfirmed
ticker guesses that are otherwise recognized elsewhere in the taxonomy --
see that constant's docstring). The title/prefix pattern tables in this
module are used ONLY to assign a MORE SPECIFIC exclusion reason for audit
telemetry (e.g. distinguishing "this is a World Baseball Classic market"
from "this is a season leader board") -- they never grant inclusion. A
series that does not match any pattern below still correctly excludes via
the generic SERIES_NOT_ALLOWLISTED fallback. This is the opposite of a
title blacklist: nothing is included because its title looks safe, and
nothing that fails the allowlist can become included no matter what a
pattern table says.

FUTURE-PROOFING: if Kalshi ever introduces a genuinely new KXMLB*-
prefixed single-game series this repository has no evidence for either
way, `detect_new_unclassified_mlb_series()` below flags it with a
non-fatal `NEW_UNCLASSIFIED_MLB_SERIES` audit warning recommending manual
review -- it is NEVER auto-added to the allowlist.
"""
from lib.research.market_taxonomy import CONFIRMED_SINGLE_GAME_SERIES_TICKERS

# Exclusion reason codes (Kalshi price-checker correction mission,
# verbatim per requirements).
SERIES_NOT_ALLOWLISTED = "SERIES_NOT_ALLOWLISTED"
NOT_SINGLE_GAME_MARKET = "NOT_SINGLE_GAME_MARKET"
NON_MLB_COMPETITION = "NON_MLB_COMPETITION"
DATE_MISMATCH = "DATE_MISMATCH"
TEAM_MAPPING_FAILED = "TEAM_MAPPING_FAILED"
PLAYER_GAME_MAPPING_FAILED = "PLAYER_GAME_MAPPING_FAILED"
FUTURES_OR_AWARD = "FUTURES_OR_AWARD"
CLOSED_OR_INACTIVE = "CLOSED_OR_INACTIVE"
MALFORMED_EVENT = "MALFORMED_EVENT"

ALL_EXCLUSION_REASONS = frozenset({
    SERIES_NOT_ALLOWLISTED, NOT_SINGLE_GAME_MARKET, NON_MLB_COMPETITION,
    DATE_MISMATCH, TEAM_MAPPING_FAILED, PLAYER_GAME_MAPPING_FAILED,
    FUTURES_OR_AWARD, CLOSED_OR_INACTIVE, MALFORMED_EVENT,
})

# Audit-only WARNING (distinct from the exclusion reasons above -- a
# market with this warning is ALSO excluded, via SERIES_NOT_ALLOWLISTED;
# this is additional telemetry, never a second way to include a market).
NEW_UNCLASSIFIED_MLB_SERIES = "NEW_UNCLASSIFIED_MLB_SERIES"

# Prefix used to decide whether an unrecognized series is "MLB-related
# enough to be worth a human looking at" -- the SAME broad heuristic
# scripts/discover_kalshi_series_catalogue.py's audit pass uses, so this
# warning fires for exactly the universe of tickers that heuristic would
# also flag, narrowed to the ones this module could not otherwise explain.
_MLB_PREFIX = "KXMLB"

# Real, observed non-MLB-competition series (evidence: the same 179-entry
# catalogue). Ticker prefix ALONE is not reliable here -- KXMLBWORLD
# ("World Baseball Classic") and KXWSAL/KXWSNL ("MLB American/National
# League champion") share the KXMLB/KXWS prefix family with genuine MLB
# series, so title text is always checked too, not just the prefix.
_NON_MLB_COMPETITION_TICKER_PREFIXES = (
    "KXWBC",              # World Baseball Classic: KXWBCHIT/SPREAD/TOTAL/RFI/KS/HR/GAME
    "KXLMB",              # Mexican Baseball League: KXLMBGAME
    "KXNCAA",             # College baseball: KXNCAABB*, KXNCAAMBACHAMP, KXNCAABASEBALL
    "KXTEAMSINNCAABBWS",  # Teams in College Baseball Finals
    "KXCONGRESSBASEBALL",  # Congressional Baseball Game
)
_NON_MLB_COMPETITION_TITLE_MARKERS = (
    "world baseball classic",
    "college baseball",
    "mexican baseball league",
    "congressional baseball",
)

# Real, observed season-long/award/futures/transaction series (same
# catalogue evidence): KXLEADERMLB* (15 stat-leader series), KXMLBWINS-<team>
# (~26 season win-total series), KXMLBHRDERBY* (Home Run Derby, an
# exhibition event with no single game), KXMLBDRAFTPICK/TOP, and the
# individual award/division/pennant/transaction/streak/misc series listed
# by title marker below (KXMLBALCY, KXMLBNLMVP, KXMLBAL, KXMLBNL, KXMLBWS,
# KXWSAL, KXWSNL, KXMLBTRADE, KXMLBNEXTTEAM, KXMLBWSTREAK, KXMLBOAK,
# KXMLBSTRIKE, MLBCBA/KXMLBCBA, KXCITYMLBEXPAND, KXMLBSTAT, etc).
_FUTURES_OR_AWARD_TICKER_PREFIXES = (
    "KXLEADERMLB",
    "KXMLBWINS-",
    "KXMLBHRDERBY",
    "KXMLBDRAFT",
)
_FUTURES_OR_AWARD_TITLE_MARKERS = (
    "cy young", "mvp", "rookie of the year", "manager of the year",
    "comeback player", "reliever of the year", "silver slugger",
    "gold glove", "executive of the year", "of the week",
    "of the month", "hank aaron", "award combo", "best mlb player",
    "division", "championship", "pennant", "world series", "champion",
    "leader", "east winner", "west winner", "central winner",
    "draft", "trade", "next team", "next manager", "next home run",
    "next homerun", "player return", "player debut", "pro baseball debut",
    "streak", "relocate", "expansion", "strike", "cba",
    "season stat", "season home run", "team stat", "playoff qualifier",
    "best record", "worst record", "teams at .500", "extra innings",
    "coach out", "managers out", "futures game", "series exact result",
    "series total games", "fastest pitch",
    "all-star", "all star",
)


def classify_series_for_price_check(series_ticker, title=None):
    """
    Pure. Decides whether `series_ticker` is eligible to be queried/
    included by the standalone daily MLB game price checker.

    Returns (allowed: bool, reason_code_or_None) -- reason_code is always
    None when allowed is True, and always one of ALL_EXCLUSION_REASONS
    when allowed is False.
    """
    if series_ticker in CONFIRMED_SINGLE_GAME_SERIES_TICKERS:
        return True, None

    ticker = series_ticker or ""
    combined_title = (title or "").lower()

    if ticker.startswith(_NON_MLB_COMPETITION_TICKER_PREFIXES) or any(
        marker in combined_title for marker in _NON_MLB_COMPETITION_TITLE_MARKERS
    ):
        return False, NON_MLB_COMPETITION

    if ticker.startswith(_FUTURES_OR_AWARD_TICKER_PREFIXES) or any(
        marker in combined_title for marker in _FUTURES_OR_AWARD_TITLE_MARKERS
    ):
        return False, FUTURES_OR_AWARD

    return False, SERIES_NOT_ALLOWLISTED


def detect_new_unclassified_mlb_series(excluded_records):
    """
    Pure. Future-proofing safeguard: scans strict-gate-excluded records
    (from lib.kalshi_price_check.apply_strict_game_registry()) for ones
    whose series ticker starts with KXMLB but was excluded via the
    GENERIC `SERIES_NOT_ALLOWLISTED` reason -- i.e. neither a confirmed
    single-game family, NOR a recognized non-game pattern (award/
    futures/other-competition). This is exactly the "Kalshi introduced
    something new" case: a market this repository has no evidence about
    either way.

    NEVER auto-includes it -- the strict allowlist gate has already
    excluded it, and this function only ever reads already-excluded
    records. Returns one warning dict per distinct series ticker
    (deduplicated, sorted for determinism), each shaped:

        {"warning": NEW_UNCLASSIFIED_MLB_SERIES, "seriesTicker": ...,
         "title": ..., "detectedDate": ..., "recommendation": ...}

    Never raises, never fails the caller's run -- an empty list is a
    completely normal, expected result on most days.
    """
    seen = {}
    for r in excluded_records:
        if r.get("exclusionReason") != SERIES_NOT_ALLOWLISTED:
            continue
        ticker = r.get("seriesTicker") or ""
        if not ticker.upper().startswith(_MLB_PREFIX):
            continue
        if ticker in seen:
            continue
        seen[ticker] = {
            "warning": NEW_UNCLASSIFIED_MLB_SERIES,
            "seriesTicker": ticker,
            "title": r.get("title"),
            "detectedDate": r.get("date"),
            "recommendation": (
                f"Series {ticker!r} is not in CONFIRMED_SINGLE_GAME_SERIES_TICKERS and did not "
                f"match any known non-game pattern -- manual review required (verify real "
                f"market/event payload structure) before adding it to the strict registry."
            ),
        }
    return sorted(seen.values(), key=lambda w: w["seriesTicker"])
