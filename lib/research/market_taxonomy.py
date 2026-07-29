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

CORRECTION (Model Performance Phase 1 amendment -- read this before
trusting any "F3/F7" claim elsewhere in this repository's history):
this module previously stated "No F3 (first-3-innings) series of any
kind" / "No F7 (first-7-innings) series of any kind" exists on Kalshi.
THAT CLAIM WAS FALSE, and has been retracted. A user with direct Kalshi
account access has confirmed placing real wagers on both MLB F3 and F7
markets, which were visible and tradable in the Kalshi interface. The
original claim was an invalid inference from "this repository's own
snapshots and archives never contain an F3/F7 ticker" to "Kalshi does
not offer F3/F7" -- those are not the same statement. The real,
independently-confirmed root cause (see
docs/research/KALSHI_MARKET_TAXONOMY.md, "F3/F7 correction" section,
and docs/research/PROJECTION_AUDIT.md) is that every fetch entry point
this repository owns (`api/kalshisearch.js`'s `ALL_SERIES` list,
`scripts/build_kalshi_registry.py`'s `SERIES_CATALOGUE` dict,
`scripts/fetch_kalshi_markets.py`'s single hardcoded `SERIES_TICKER`)
queries Kalshi's `/markets?series_ticker=<known-prefix>` endpoint for a
FIXED, hardcoded list of exactly 8 series tickers -- none of which is
an F3 or F7 ticker -- and none of them ever calls any Kalshi endpoint
capable of enumerating series without already knowing its prefix.
`data/kalshi_registry_snapshots/*.json` (the archive this module's
classification claims were originally based on) is populated
exclusively by `api/kalshisearch.js`'s output, so by construction it
can never contain a series this repository's fetcher never asked
Kalshi about. Absence from every file this repository controls is
therefore proof of a repository ingestion gap, NOT proof of Kalshi
market nonexistence. See `HORIZON_MARKET_STATUS` below for the
corrected, honest status distinctions (existence vs. discovery vs.
archival vs. normalization vs. projection support vs. production
support) that replace the retracted claim.

Also still true (unaffected by the above correction): no standalone
pitcher-strikeout / pitcher-outs / pitcher-hits-allowed / pitcher-
earned-runs / hitter-prop series was discovered in any available
snapshot or archive file this phase had access to -- this remains
documented as an inventory gap, not a confirmed absence, for the exact
same reason (this repository's own fetchers never targeted any such
series either).

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

# ── Corrected horizon existence/support status (Phase 1 amendment) ─────────
# Single, honest source of truth distinguishing what is CONFIRMED via this
# repository's own real snapshot/archive evidence from what is
# USER-CONFIRMED-BUT-NOT-API-VERIFIED, so no downstream artifact (inventory
# JSON, projection comparison, docs) can re-assert the retracted "F3/F7 does
# not exist" claim. Keyed by inning_result SCOPE (F3/F5/F7) plus full_game,
# not by ticker prefix -- the ticker prefixes used for F3/F7 elsewhere in
# this module (KXMLBF3/KXMLBF7) are an UNCONFIRMED GUESS at Kalshi's real
# naming, not a verified fact, which is itself part of why this repository's
# fetchers cannot yet reliably query them by name.
_ROOT_CAUSE_TEXT = (
    "api/kalshisearch.js's ALL_SERIES list, scripts/build_kalshi_registry.py's "
    "SERIES_CATALOGUE dict, and scripts/fetch_kalshi_markets.py's single "
    "hardcoded SERIES_TICKER never include an F{n} series ticker, and none of "
    "this repository's fetchers ever calls a Kalshi endpoint capable of "
    "enumerating series without already knowing its ticker prefix -- see "
    "docs/research/KALSHI_MARKET_TAXONOMY.md's 'F3/F7 correction' section."
)

HORIZON_MARKET_STATUS = {
    "full_game": {
        "existenceStatus": "CONFIRMED_VIA_REPOSITORY_SNAPSHOT",
        "discoverySource": "kalshi_search_api_snapshot",
        "repositoryFetcherSupport": True,
        "archiveCoverage": True,
        "normalizationSupport": True,
        "projectionSupport": True,
        "productionEnabled": True,
        "outcomeStructureStatus": "CONFIRMED_TWO_WAY",
        "structureStatus": "VERIFIED",
        "outcomeStructure": ["Away", "Home"],
        "settlementStatus": "inferred_from_ticker_structure_not_kalshi_rules_field",
        "rootCauseOfNonDiscovery": None,
    },
    "F5": {
        "existenceStatus": "CONFIRMED_VIA_REPOSITORY_SNAPSHOT",
        "discoverySource": "kalshi_search_api_snapshot",
        "repositoryFetcherSupport": True,
        "archiveCoverage": True,
        "normalizationSupport": True,
        "projectionSupport": True,
        # Team legs (Away/Home) reach production; the Tie leg is fetched
        # (merge_odds.py) but never evaluated (build_market_ledger.py) --
        # see docs/research/PROJECTION_AUDIT.md's f5_tie_am finding.
        "productionEnabled": True,
        "outcomeStructureStatus": "CONFIRMED_THREE_WAY",
        "structureStatus": "VERIFIED",
        "outcomeStructure": ["Away", "Tie", "Home"],
        "settlementStatus": "inferred_from_ticker_structure_not_kalshi_rules_field",
        "rootCauseOfNonDiscovery": None,
    },
    "F3": {
        "existenceStatus": "EXISTS_ON_KALSHI_USER_CONFIRMED",
        "discoverySource": "user_reported_observation_not_api_verified",
        "repositoryFetcherSupport": False,
        "archiveCoverage": False,
        # The taxonomy classifier below CAN classify an F3 ticker/title as
        # inning_result/F3 the moment one is ever observed (either via the
        # speculative KXMLBF3 prefix or the title-text fallback) -- that
        # capability is real and tested, independent of whether any F3
        # market has ever actually reached this repository.
        "normalizationSupport": True,
        # lib.research.three_way_projection is horizon-generic (F3 is a
        # first-class entry in HORIZON_INNINGS) -- the math already
        # supports F3 without any change; it has simply never been fed
        # real F3 market data.
        "projectionSupport": True,
        "productionEnabled": False,
        "outcomeStructureStatus": "UNVERIFIED",
        "structureStatus": "UNVERIFIED",
        "outcomeStructure": None,
        "settlementStatus": "UNVERIFIED",
        "rootCauseOfNonDiscovery": _ROOT_CAUSE_TEXT.replace("F{n}", "F3"),
    },
    "F7": {
        "existenceStatus": "EXISTS_ON_KALSHI_USER_CONFIRMED",
        "discoverySource": "user_reported_observation_not_api_verified",
        "repositoryFetcherSupport": False,
        "archiveCoverage": False,
        "normalizationSupport": True,
        "projectionSupport": True,
        "productionEnabled": False,
        "outcomeStructureStatus": "UNVERIFIED",
        "structureStatus": "UNVERIFIED",
        "outcomeStructure": None,
        "settlementStatus": "UNVERIFIED",
        "rootCauseOfNonDiscovery": _ROOT_CAUSE_TEXT.replace("F{n}", "F7"),
    },
}


def _series_from_ticker(ticker):
    """Extract the leading series-ticker component from an event/market ticker."""
    if not ticker:
        return None
    return ticker.split("-", 1)[0]


_F3_TEXT_MARKERS = ("first 3 innings", "first three innings", "through 3 innings", "after 3 innings")
_F7_TEXT_MARKERS = ("first 7 innings", "first seven innings", "through 7 innings", "after 7 innings")


def _infer_unconfirmed_inning_scope_from_text(title, subtitle, ticker):
    """
    Best-effort scope inference from title/subtitle/ticker TEXT ALONE, used
    ONLY when the series ticker prefix is not recognized in
    SERIES_FAMILY_MAP. This exists specifically so an F3/F7 (or any other
    not-yet-catalogued inning-horizon) market is never permanently stuck at
    FAMILY_UNKNOWN merely because this repository guessed the wrong ticker
    prefix -- classification-by-title is a real, independent path to a
    correct family/scope, per the mission's Part 6 requirement that
    discovery must not require a series to be pre-approved by prefix.
    Returns "F3", "F7", or None. Deliberately does NOT infer "F5" here --
    F5 is already reliably matched by ticker prefix (KXMLBF5), so a
    title-based F5 fallback is not needed and would only broaden the
    already-precise match unnecessarily.
    """
    combined = f"{title or ''} {subtitle or ''} {ticker or ''}".lower()
    if any(marker in combined for marker in _F3_TEXT_MARKERS) or re.search(r"\bf3\b", combined):
        return "F3"
    if any(marker in combined for marker in _F7_TEXT_MARKERS) or re.search(r"\bf7\b", combined):
        return "F7"
    return None


def _looks_like_result_market(title, subtitle):
    """
    Heuristic distinguishing a "who wins?" result-type market from a
    total/spread-type market sharing the same horizon text (e.g. "first 3
    innings TOTAL runs over 2.5?" is NOT a result market). Deliberately
    conservative: a total/spread-shaped F3/F7 market that doesn't clearly
    say "win"/"wins"/"winner" is left unclassified rather than guessed at,
    consistent with this module never fabricating a classification it
    cannot support from the text actually available.
    """
    combined = f"{title or ''} {subtitle or ''}".lower()
    return any(w in combined for w in ("winner", "wins", " win?", " win "))


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
        # Title/subtitle-based fallback (Phase 1 amendment, Part 6): a
        # series ticker prefix this module does not recognize (e.g. because
        # this repository guessed the wrong prefix for a real Kalshi
        # series, as happened with F3/F7) must still be classifiable from
        # its title text alone, rather than permanently stuck at
        # FAMILY_UNKNOWN. This does NOT pre-approve any prefix -- it is the
        # opposite: classification-by-content instead of classification-
        # by-pre-known-prefix.
        inferred_scope = _infer_unconfirmed_inning_scope_from_text(title, subtitle, market_ticker)
        fallback_suffix = None
        if market_ticker and event_ticker and market_ticker.startswith(event_ticker + "-"):
            fallback_suffix = market_ticker[len(event_ticker) + 1:]
        elif market_ticker and "-" in market_ticker:
            fallback_suffix = market_ticker.rsplit("-", 1)[-1]

        # A tie leg's title commonly says "...tie?" rather than
        # "...winner?" -- a TIE-suffixed ticker is itself sufficient
        # evidence this is a result-type market, independent of
        # _looks_like_result_market()'s winner/wins/win text check.
        is_result_market = _looks_like_result_market(title, subtitle) or fallback_suffix in _OUTCOME_TIE_SUFFIXES

        if inferred_scope and is_result_market:
            result["family"] = FAMILY_INNING_RESULT
            result["scope"] = inferred_scope
            result["classificationStatus"] = "classified_by_title_fallback_unverified_prefix"
            result["settlementBasis"] = _settlement_basis_for_scope(inferred_scope)

            if fallback_suffix in _OUTCOME_TIE_SUFFIXES:
                result["outcome"] = "Tie"
                result["operator"] = "equals"
            elif fallback_suffix:
                result["outcome"] = "Win"
                result["team"] = fallback_suffix
                result["operator"] = "greater_than"
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
    Pure. Returns True for families/scopes that must be TREATED as
    genuine three-way (Away/Tie/Home) Kalshi contracts for canonical-
    probability purposes (i.e. never renormalized after removing a tie).
    Full-game (KXMLBGAME) is deliberately NOT included -- it is confirmed
    two-way.

    IMPORTANT (Phase 1 amendment): this function does NOT distinguish
    "confirmed three-way via real snapshot evidence" (F5 only) from
    "existence confirmed by the user but outcome structure not
    independently verified this phase" (F3, F7). Both are included here
    because the conservative, safe default -- given F3/F7 are structurally
    the same kind of partial-game snapshot as F5, which IS confirmed
    three-way -- is to never renormalize away a tie that might be real,
    rather than silently assume F3/F7 are two-way and discard a tie
    outcome that turns out to exist. Use
    `HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"]` to see the
    actual confidence level ("CONFIRMED_THREE_WAY" vs. "UNVERIFIED")
    behind this True return for a given scope -- never report an
    UNVERIFIED scope's three-way treatment as a confirmed fact.
    """
    return family == FAMILY_INNING_RESULT and scope in ("F3", "F5", "F7")


KNOWN_FAMILIES = {
    FAMILY_GAME_RESULT, FAMILY_INNING_RESULT, FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL, FAMILY_RUN_LINE, FAMILY_WINNING_MARGIN, FAMILY_FIRST_INNING_RUN,
    FAMILY_PITCHER_STRIKEOUTS, FAMILY_PITCHER_OUTS, FAMILY_PITCHER_HITS_ALLOWED,
    FAMILY_PITCHER_EARNED_RUNS, FAMILY_HITTER_HITS, FAMILY_HITTER_TOTAL_BASES,
    FAMILY_HITTER_HOME_RUNS,
}


# ── Canonical inning-result taxonomy (Model Performance Phase 2A, Part 6) ───
STRUCTURE_THREE_WAY = "THREE_WAY"
STRUCTURE_TWO_WAY = "TWO_WAY"
STRUCTURE_BINARY_PROPOSITION = "BINARY_PROPOSITION"
STRUCTURE_UNVERIFIED = "UNVERIFIED"


def classify_inning_result_market(market_ticker, event_ticker=None, title=None,
                                   subtitle=None, away_team=None, home_team=None):
    """
    Pure. Builds the Phase 2A Part 6 canonical inning-result schema on top
    of classify_market(). Returns None for a market that classify_market()
    did not resolve to FAMILY_INNING_RESULT (this function only concerns
    itself with inning-result markets -- game_result/totals/spreads/etc are
    out of scope here).

    `outcome` is "Away"/"Home"/"Tie"/"Unknown" -- NEVER coerced. Resolving
    "Away" vs. "Home" requires knowing which team abbreviation is the away
    team (context classify_market() intentionally does not have, since it
    is a pure per-ticker classifier); pass `away_team`/`home_team` when
    available (the shadow-ledger builder has this from the game object).
    Without them, a team-leg market's outcome is honestly "Unknown" rather
    than guessed.

    `structure` reflects HORIZON_MARKET_STATUS's outcomeStructureStatus for
    the market's scope: STRUCTURE_THREE_WAY only for scopes confirmed
    three-way (F5 today), STRUCTURE_UNVERIFIED for scopes whose structure
    has not been independently verified (F3, F7) -- never assumed to match
    F5 merely by horizon-family similarity.
    """
    base = classify_market(market_ticker, event_ticker=event_ticker, title=title, subtitle=subtitle)
    if base["family"] != FAMILY_INNING_RESULT:
        return None

    scope = base["scope"]
    status = HORIZON_MARKET_STATUS.get(scope, {})

    if base["outcome"] == "Tie":
        outcome = "Tie"
    elif base["outcome"] == "Win" and base["team"] and away_team and base["team"] == away_team:
        outcome = "Away"
    elif base["outcome"] == "Win" and base["team"] and home_team and base["team"] == home_team:
        outcome = "Home"
    else:
        outcome = "Unknown"

    outcome_structure_status = status.get("outcomeStructureStatus")
    structure = STRUCTURE_THREE_WAY if outcome_structure_status == "CONFIRMED_THREE_WAY" else STRUCTURE_UNVERIFIED

    return {
        "family": base["family"],
        "scope": scope,
        "outcome": outcome,
        "structure": structure,
        "ticker": base["marketTicker"],
        "eventTicker": base["eventTicker"],
        "seriesTicker": base["seriesTicker"],
        "rawTitle": base["rawTitle"],
        "rawSubtitle": base["rawSubtitle"],
        "settlementBasis": base["settlementBasis"],
        "settlementStatus": status.get("settlementStatus", "UNVERIFIED"),
        "discoveryStatus": base["classificationStatus"],
        "productionEnabled": False,
    }
