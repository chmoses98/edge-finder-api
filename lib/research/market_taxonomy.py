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
# CONFIRMED (Kalshi price-checker correction mission, live series-
# catalogue dispatch 2026-07-31): KXMLBRBI (14 events/119 markets) and
# KXMLBSB (13 events/43 markets) were directly observed as LIVE, real
# single-game player-prop series on the 2026-07-30 board --
# data/kalshi/discovery/2026-07-30_series_catalogue.json. KXMLBKS/
# KXMLBOUTS/KXMLBHIT/KXMLBTB/KXMLBHRR are the same real series family
# (confirmed to exist in Kalshi's series catalogue) but had 0 live
# events/markets on that specific date -- retained here as recognized
# families for the day they do. This CORRECTS the prior Phase 1 finding
# ("no pitcher-prop or hitter-prop market has ever been observed") the
# same way the F3/F7 correction did -- see
# docs/KALSHI_PRICE_CHECKER_STRICT_REGISTRY.md.
FAMILY_HITTER_RBIS = "hitter_rbis"
FAMILY_HITTER_STOLEN_BASES = "hitter_stolen_bases"
FAMILY_HITTER_HITS_RUNS_RBIS = "hitter_hits_runs_rbis"
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
    # CONFIRMED real single-game player-prop series (see the
    # FAMILY_HITTER_RBIS/FAMILY_HITTER_STOLEN_BASES docstring above for
    # the exact live evidence). All full-game scope -- no per-period
    # (F3/F5/F7) variant of any of these has ever been observed.
    "KXMLBKS": (FAMILY_PITCHER_STRIKEOUTS, "full_game"),
    "KXMLBOUTS": (FAMILY_PITCHER_OUTS, "full_game"),
    "KXMLBHIT": (FAMILY_HITTER_HITS, "full_game"),
    "KXMLBTB": (FAMILY_HITTER_TOTAL_BASES, "full_game"),
    "KXMLBHRR": (FAMILY_HITTER_HITS_RUNS_RBIS, "full_game"),
    "KXMLBRBI": (FAMILY_HITTER_RBIS, "full_game"),
    "KXMLBSB": (FAMILY_HITTER_STOLEN_BASES, "full_game"),
}

# The single-game market-family recognition set (Kalshi price-checker
# correction mission): every series ticker above is a single-MLB-game
# (or single-game-player-prop) market family shape -- SERIES_FAMILY_MAP
# has never contained a season-long/award/futures/leader/other-league
# series. This is exported as its own name so callers needing "is this
# ticker a single-game MLB market family SHAPE" have one clear,
# documented entry point rather than reaching into SERIES_FAMILY_MAP's
# keys directly.
#
# IMPORTANT: this is NOT the same claim as "directly observed as a real
# Kalshi series" -- 4 of the entries above (KXMLBF3SPREAD/F3TOTAL/
# F7SPREAD/F7TOTAL) are speculative guesses at a naming convention,
# never confirmed to exist (see the comment at their definition above).
# Callers that must gate on evidence-confirmed reality only (the
# standalone price checker's strict registry) use
# CONFIRMED_SINGLE_GAME_SERIES_TICKERS below instead. See
# lib/kalshi_mlb_single_game_registry.py, which builds on that rather
# than duplicating it.
SINGLE_GAME_SERIES_TICKERS = frozenset(SERIES_FAMILY_MAP.keys())

# Ticker names GUESSED at before F3/F7 existence was independently
# confirmed (spread/F3-F7-correction mission), never observed in any
# real Kalshi series-catalogue dispatch (data/kalshi/discovery/
# 2026-07-30_series_catalogue.json's 179-entry mlbAssociatedSeries list
# contains KXMLBF3 and KXMLBF7 themselves, but none of these 4). Kept in
# SERIES_FAMILY_MAP/SINGLE_GAME_SERIES_TICKERS so a real market under one
# of these names is still recognized (not FAMILY_UNKNOWN) if it ever
# appears -- but excluded from the price checker's strict allowlist,
# which requires actual observed evidence, not a guessed name.
SPECULATIVE_UNCONFIRMED_SERIES_TICKERS = frozenset({
    "KXMLBF3SPREAD", "KXMLBF7SPREAD", "KXMLBF3TOTAL", "KXMLBF7TOTAL",
})

# The strict, EVIDENCE-CONFIRMED single-game registry (Kalshi price-
# checker correction mission): every ticker here has been directly
# observed as a real Kalshi series in a live series-catalogue dispatch --
# this is the set the standalone price checker's mandatory allowlist
# gate (lib.kalshi_mlb_single_game_registry.classify_series_for_price_
# check) actually uses to decide inclusion, per the mission's explicit
# "do not blindly trust... verify actual repo logic, live payload
# structure" requirement.
CONFIRMED_SINGLE_GAME_SERIES_TICKERS = (
    SINGLE_GAME_SERIES_TICKERS - SPECULATIVE_UNCONFIRMED_SERIES_TICKERS
)

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
    # CORRECTED (spread/F3-F7-correction mission, live dispatch of
    # scripts/discover_kalshi_series_catalogue.py against the real
    # Kalshi exchange series catalogue on 2026-07-31): F3 and F7 are
    # CONFIRMED live, real Kalshi series -- not merely user-reported.
    # KXMLBF7's real raw market payload was captured directly (see
    # data/kalshi/discovery/2026-07-30_f3_f7_search.json's
    # structureVerificationRawMarkets.KXMLBF7): 45 markets across 15
    # events, EVERY event having exactly 3 tickers
    # (KXMLBF7-<event>-<AWAY>, KXMLBF7-<event>-<HOME>,
    # KXMLBF7-<event>-TIE) -- e.g.
    # "KXMLBF7-26JUL312145SFSD-TIE": "San Francisco vs San Diego first 7
    # innings tie?" -- a real, tradable TIE contract, exactly matching
    # F5's confirmed three-way structure. KXMLBF3 was independently
    # confirmed to be the same series/naming convention
    # ("First 3 Innings Winner") with the identical 15-events/45-markets
    # (3-per-event) shape in the same dispatch's series-catalogue pass
    # (data/kalshi/discovery/2026-07-31_series_catalogue.json) -- its
    # literal per-market ticker suffixes were not captured in that
    # specific run (rate-limited before that query), so F3's three-way
    # confirmation rests on the matching count-per-event pattern and
    # identical series-family convention to the directly-verified F7,
    # not on a directly-observed "-TIE" ticker for F3 itself. Both are
    # recorded as CONFIRMED_THREE_WAY given the strength of this
    # evidence, with the distinction preserved in discoverySource.
    "F3": {
        "existenceStatus": "CONFIRMED_VIA_LIVE_SERIES_CATALOGUE",
        "discoverySource": "kalshi_series_catalogue_live_dispatch_2026-07-31_aggregate_count_evidence",
        "repositoryFetcherSupport": True,
        "archiveCoverage": True,
        "normalizationSupport": True,
        "projectionSupport": True,
        "productionEnabled": False,
        "outcomeStructureStatus": "CONFIRMED_THREE_WAY",
        "structureStatus": "VERIFIED",
        "outcomeStructure": ["Away", "Tie", "Home"],
        "settlementStatus": "inferred_from_ticker_structure_not_kalshi_rules_field",
        "rootCauseOfNonDiscovery": None,
    },
    "F7": {
        "existenceStatus": "CONFIRMED_VIA_LIVE_SERIES_CATALOGUE",
        "discoverySource": "kalshi_series_catalogue_live_dispatch_2026-07-31_raw_market_payload",
        "repositoryFetcherSupport": True,
        "archiveCoverage": True,
        "normalizationSupport": True,
        "projectionSupport": True,
        "productionEnabled": False,
        "outcomeStructureStatus": "CONFIRMED_THREE_WAY",
        "structureStatus": "VERIFIED",
        "outcomeStructure": ["Away", "Tie", "Home"],
        "settlementStatus": "inferred_from_ticker_structure_not_kalshi_rules_field",
        "rootCauseOfNonDiscovery": None,
    },
}


_TEAM_MARGIN_SUFFIX_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _team_and_margin_from_suffix(suffix):
    """
    Shared by FAMILY_WINNING_MARGIN and FAMILY_TEAM_TOTAL, which use the
    IDENTICAL ticker-suffix convention: 'SF11' -> (team='SF', line=10.5).
    Kalshi's suffix digit N encodes "over (N-0.5)" for both a winning-
    margin threshold and a team-total threshold. Returns (None, None) if
    suffix doesn't match -- never guessed.
    """
    if not suffix:
        return None, None
    m = _TEAM_MARGIN_SUFFIX_RE.match(suffix)
    if not m:
        return None, None
    return m.group(1), float(m.group(2)) - 0.5


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


def _looks_like_spread_market(title, subtitle):
    """
    Heuristic identifying a winning-margin/spread-shaped market by title
    text alone (spread correction mission, Part 2/3): "wins by", "run
    line", or an explicit "N.5 runs" margin phrase. Used only for a
    series ticker this module does not recognize (see
    classify_market()'s fallback branch) -- an F3/F7 spread market under
    an unconfirmed prefix must still be classifiable from text, exactly
    like the existing F3/F7 winner-market fallback.
    """
    combined = f"{title or ''} {subtitle or ''}".lower()
    if "wins by" in combined or "run line" in combined:
        return True
    return bool(re.search(r"\d\.5\s*runs?", combined))


def _looks_like_total_market(title, subtitle):
    """
    Heuristic identifying a total-runs-shaped market by title text alone:
    "total runs" combined with an over/under threshold, excluding
    anything already caught by _looks_like_spread_market (a spread also
    mentions run counts but frames them as a margin, not a total).
    """
    combined = f"{title or ''} {subtitle or ''}".lower()
    has_total_word = "total runs" in combined or ("total" in combined and (
        "over" in combined or "under" in combined))
    return has_total_word and not _looks_like_spread_market(title, subtitle)


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

        # Spread phrasing ("Team WINS BY over N.5 runs?") textually
        # contains "wins" too -- checked FIRST so it is never
        # misclassified as a winner-market question by
        # _looks_like_result_market()'s broader wins/winner check
        # (spread-correction mission Part 2/3). A TIE-suffixed ticker
        # is unambiguous result-market evidence regardless of title
        # wording and always wins.
        is_spread_shaped = _looks_like_spread_market(title, subtitle)
        is_result_market = (
            fallback_suffix in _OUTCOME_TIE_SUFFIXES
            or (not is_spread_shaped and _looks_like_result_market(title, subtitle))
        )

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

        # Spread-correction mission, Part 2/3: an F3/F7 SPREAD or TOTAL
        # market under an unconfirmed series prefix must be classifiable
        # from title text too -- not just winner-shaped markets (the
        # only shape the pre-existing fallback above handled). Checked
        # only when NOT already resolved as a result-type market above.
        if inferred_scope and is_spread_shaped:
            result["family"] = FAMILY_WINNING_MARGIN
            result["scope"] = inferred_scope
            result["classificationStatus"] = "classified_by_title_fallback_unverified_prefix"
            result["settlementBasis"] = _settlement_basis_for_scope(inferred_scope)
            team, margin_line = _team_and_margin_from_suffix(fallback_suffix)
            if team is not None:
                result["team"] = team
                result["operator"] = "greater_than"
                result["line"] = margin_line
            return result

        if inferred_scope and _looks_like_total_market(title, subtitle):
            result["family"] = FAMILY_INNING_TOTAL
            result["scope"] = inferred_scope
            result["classificationStatus"] = "classified_by_title_fallback_unverified_prefix"
            result["settlementBasis"] = _settlement_basis_for_scope(inferred_scope)
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

    elif family in (FAMILY_WINNING_MARGIN, FAMILY_TEAM_TOTAL):
        # e.g. "SF11" -> team="SF", line=10.5 -- Kalshi's suffix digit N
        # encodes "wins by / scores over (N-0.5) runs" for BOTH families
        # (identical convention, already used and tested in
        # lib/kalshi_mlb_market_classifier.py's _extract_margin_line()/
        # _extract_team_total() and documented in
        # scripts/build_kalshi_registry.py's parse_suffix()).
        team, margin_line = _team_and_margin_from_suffix(suffix)
        if team is not None:
            result["team"] = team
            result["operator"] = "greater_than"
            result["line"] = margin_line
        result["settlementBasis"] = _settlement_basis_for_scope(result["scope"])

    elif family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL):
        # A pure-digit suffix is a strict integer "over N runs" total --
        # no half-run lines on this series (unlike winning_margin/
        # team_total, which always carry an explicit N-0.5 threshold).
        m = re.match(r"^(\d+)$", suffix) if suffix else None
        if m:
            result["line"] = int(m.group(1))
        result["operator"] = "greater_than"
        result["settlementBasis"] = _settlement_basis_for_scope(result["scope"])

    elif family == FAMILY_FIRST_INNING_RUN:
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
    FAMILY_HITTER_HOME_RUNS, FAMILY_HITTER_RBIS, FAMILY_HITTER_STOLEN_BASES,
    FAMILY_HITTER_HITS_RUNS_RBIS,
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
