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
`lib.research.market_taxonomy.SINGLE_GAME_SERIES_TICKERS` -- the set of
series tickers this repository has directly confirmed, from that same
real catalogue dispatch, to be single-game/single-game-player-prop MLB
markets. The title/prefix pattern tables in this module are used ONLY to
assign a MORE SPECIFIC exclusion reason for audit telemetry (e.g.
distinguishing "this is a World Baseball Classic market" from "this is a
season leader board") -- they never grant inclusion. A series that does
not match any pattern below still correctly excludes via the generic
SERIES_NOT_ALLOWLISTED fallback. This is the opposite of a title
blacklist: nothing is included because its title looks safe, and nothing
that fails the allowlist can become included no matter what a pattern
table says.
"""
from lib.research.market_taxonomy import SINGLE_GAME_SERIES_TICKERS

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
    if series_ticker in SINGLE_GAME_SERIES_TICKERS:
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
