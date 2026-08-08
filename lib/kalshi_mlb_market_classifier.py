#!/usr/bin/env python3
"""
lib/kalshi_mlb_market_classifier.py
======================================
Market-family classifier stage of the universal Kalshi MLB market engine
(docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md, Phase 2).

Wraps the existing, already-tested `lib/research/market_taxonomy.py`
classifier (built in an earlier research phase, never before wired to
production) rather than reimplementing ticker-family classification from
scratch, and extends its output with the additional canonical-schema
fields the universal engine needs: `subjectType`, `subjectId`,
`subjectName`, `side`, and `line`.

Never raises on an unrecognized contract, and never drops one — a
contract this module cannot classify still returns a full canonical
result with `marketFamily="unknown"` and `classificationStatus`
reflecting exactly what went wrong.

Pitcher-prop discovery-wiring mission: pitcher_strikeouts/pitcher_outs
subjectId/subjectName/side/line are now resolved (see
_resolve_pitcher_prop_subject below) when the caller supplies the
matched slate `game` dict -- previously "deliberately left
unimplemented" here regardless of whether a game was known, which is
exactly why every pitcher-prop contract was permanently unroutable to
lib.kalshi_probability_adapters.adapt_pitcher_strikeouts/
adapt_pitcher_outs (PR #58) even after those adapters existed. `game`
is optional and defaults to None, so every existing caller that doesn't
pass it (there are several, across tests and scripts/
discover_kalshi_mlb_markets.py's own historical call sites) keeps its
prior behavior byte-for-byte -- this is additive only.
"""
from lib.research.market_taxonomy import (
    classify_market,
    FAMILY_GAME_RESULT,
    FAMILY_INNING_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
    FAMILY_FIRST_INNING_RUN,
    FAMILY_PITCHER_STRIKEOUTS,
    FAMILY_PITCHER_OUTS,
    FAMILY_PITCHER_HITS_ALLOWED,
    FAMILY_PITCHER_EARNED_RUNS,
    FAMILY_HITTER_HITS,
    FAMILY_HITTER_TOTAL_BASES,
    FAMILY_HITTER_HOME_RUNS,
    FAMILY_HITTER_RBIS,
    FAMILY_HITTER_STOLEN_BASES,
    FAMILY_HITTER_HITS_RUNS_RBIS,
    FAMILY_UNKNOWN,
)
from lib.research.player_prop_parser import (
    parse_player_prop_market,
    normalize_player_name,
    TEAM_RESOLVED,
)

SUBJECT_TEAM = "TEAM"
SUBJECT_GAME = "GAME"
SUBJECT_PITCHER = "PITCHER"
SUBJECT_HITTER = "HITTER"
SUBJECT_INNING = "INNING"
SUBJECT_OTHER = "OTHER"

_PITCHER_FAMILIES = {
    FAMILY_PITCHER_STRIKEOUTS, FAMILY_PITCHER_OUTS,
    FAMILY_PITCHER_HITS_ALLOWED, FAMILY_PITCHER_EARNED_RUNS,
}
_HITTER_FAMILIES = {
    FAMILY_HITTER_HITS, FAMILY_HITTER_TOTAL_BASES, FAMILY_HITTER_HOME_RUNS,
    FAMILY_HITTER_RBIS, FAMILY_HITTER_STOLEN_BASES, FAMILY_HITTER_HITS_RUNS_RBIS,
}

# The only two pitcher-prop families lib.kalshi_probability_adapters can
# actually price today (PR #58's joint workload/K/outs model) -- subject
# resolution is scoped to exactly these two, not the full _PITCHER_FAMILIES
# set, so pitcher_hits_allowed/pitcher_earned_runs (which have no model
# and are not part of this mission) are left completely untouched.
_MODELED_PITCHER_PROP_FAMILIES = {FAMILY_PITCHER_STRIKEOUTS, FAMILY_PITCHER_OUTS}


def _resolve_pitcher_prop_subject(parsed_contract, family, away, home, game):
    """
    Pure. Resolves (subjectId, subjectName, side, line) for one
    pitcher_strikeouts/pitcher_outs contract, GIVEN its already-matched
    slate `game` dict (the same shape scripts/discover_kalshi_mlb_markets.py's
    build_slate_index()/resolve_game_match() already produce -- this
    function does no matching of its own).

    side/line are structural ticker facts (AT_LEAST threshold, single
    YES/NO ticker per rung -- same convention as
    FAMILY_FIRST_INNING_RUN's side="Yes") and are always returned when
    the ticker parses, independent of whether player identity resolves.

    subjectId/subjectName require an EXACT match (via
    lib.research.player_prop_parser.normalize_player_name -- no fuzzy/
    edit-distance matching, ever) between the ticker/title's own parsed
    display name and `game[<resolved side>]['pitcher']['name']` -- the
    ONE probable starter slate.json records for that team side (there is
    no pre-game roster/boxscore to search more broadly against, unlike
    lib.edgelab.player_resolution's post-game settlement-time
    resolution). Conservative by construction: a single expected
    candidate, exact-name-or-nothing. Returns (None, None, side, line)
    whenever the ticker's own team can't be resolved exactly, the title
    carries no usable display name, or that name does not match the
    slate's probable starter for the resolved team -- never a guess,
    and never a fuzzy match to "the closest name."
    """
    parsed = parse_player_prop_market(
        parsed_contract.get("ticker"), event_ticker=parsed_contract.get("eventTicker"),
        title=parsed_contract.get("marketTitle"), subtitle=parsed_contract.get("marketSubtitle"),
        away_team=away, home_team=home, family=family,
    )
    line = parsed["threshold"]
    side = "Yes" if parsed["parseStatus"] == "PARSED" else None

    if parsed["parseStatus"] != "PARSED" or parsed["teamResolutionStatus"] != TEAM_RESOLVED:
        return None, None, side, line
    if not parsed["normalizedNameVariants"]:
        return None, None, side, line

    team_abbr = parsed["teamAbbr"]
    if team_abbr == away:
        pitcher_info = (game.get("away") or {}).get("pitcher") or {}
    elif team_abbr == home:
        pitcher_info = (game.get("home") or {}).get("pitcher") or {}
    else:
        pitcher_info = {}

    probable_name = pitcher_info.get("name")
    if not probable_name or normalize_player_name(probable_name) not in parsed["normalizedNameVariants"]:
        return None, None, side, line

    return pitcher_info.get("id"), probable_name, side, line


def classify_contract(parsed_contract, away_team=None, home_team=None, game=None):
    """
    Classify one parsed contract (from
    lib.kalshi_mlb_contract_parser.parse_contract()) into the canonical
    marketFamily/period/subjectType/subjectId/subjectName/side/line
    fields.

    Args:
        parsed_contract: dict from parse_contract().
        away_team/home_team: explicit team abbreviations; falls back to
            parsed_contract's own awayTeam/homeTeam if omitted (both
            may still be None, in which case a team-leg market's side
            is reported as the raw ticker-suffix team abbreviation
            rather than "Away"/"Home" — never guessed).
        game: optional matched slate game dict (same shape
            scripts/discover_kalshi_mlb_markets.py's build_slate_index()/
            resolve_game_match() produce) -- used ONLY to resolve
            pitcher_strikeouts/pitcher_outs subjectId/subjectName
            against that game's probable starters (see
            _resolve_pitcher_prop_subject). Every other family is
            unaffected by this argument. Omitting it (the default)
            leaves pitcher-prop subjectId/subjectName/side/line exactly
            as unresolved as before this argument existed -- never a
            behavior change for a caller that doesn't supply it.

    Returns a dict with: marketFamily, period, subjectType, subjectId,
    subjectName, side, line, classificationStatus, rawTaxonomy (the
    full underlying lib.research.market_taxonomy.classify_market()
    output, kept for audit/debugging).
    """
    away = away_team or parsed_contract.get("awayTeam")
    home = home_team or parsed_contract.get("homeTeam")

    base = classify_market(
        parsed_contract.get("ticker"),
        event_ticker=parsed_contract.get("eventTicker"),
        title=parsed_contract.get("marketTitle"),
        subtitle=parsed_contract.get("marketSubtitle"),
    )

    family = base["family"]
    scope = base["scope"]

    subject_type = SUBJECT_OTHER
    subject_id = None
    subject_name = None
    side = None
    line = None

    if family in (FAMILY_GAME_RESULT, FAMILY_INNING_RESULT):
        subject_type = SUBJECT_GAME
        if base["outcome"] == "Tie":
            side = "Tie"
        elif base["outcome"] == "Win" and base["team"]:
            if away and base["team"] == away:
                side = "Away"
            elif home and base["team"] == home:
                side = "Home"
            else:
                side = base["team"]  # raw abbr fallback — never fabricated as Away/Home
            subject_id = base["team"]

    elif family == FAMILY_WINNING_MARGIN:
        # base["team"]/base["line"] are already resolved by
        # classify_market() from the identical ticker suffix (same
        # ticker/eventTicker inputs) -- reused here rather than
        # re-derived a second time.
        subject_type = SUBJECT_TEAM
        subject_id = base["team"]
        side = base["team"]
        line = base["line"]

    elif family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL):
        subject_type = SUBJECT_GAME
        side = "Over"  # Kalshi total contracts: YES == over N; NO == under N (same ticker)
        line = base["line"]

    elif family == FAMILY_TEAM_TOTAL:
        subject_type = SUBJECT_TEAM
        subject_id = base["team"]
        side = "Over"
        line = base["line"]

    elif family == FAMILY_FIRST_INNING_RUN:
        subject_type = SUBJECT_INNING
        side = "Yes"  # YES == a run scores in the 1st (YRFI); NO == NRFI

    elif family in _PITCHER_FAMILIES:
        subject_type = SUBJECT_PITCHER
        # CONFIRMED real series (Kalshi price-checker correction mission,
        # live series-catalogue dispatch -- KXMLBKS/KXMLBOUTS exist, see
        # lib/research/market_taxonomy.py's FAMILY_HITTER_RBIS docstring).
        # side/line are structural ticker facts (resolved for the two
        # families with a real probability model -- PR #58 -- regardless
        # of whether `game` was supplied); subjectId/subjectName
        # additionally require `game` (the matched slate game, to check
        # identity against) -- see _resolve_pitcher_prop_subject, which
        # degrades gracefully to (None, None, side, line) when `game` is
        # omitted. pitcher_hits_allowed/pitcher_earned_runs (no model
        # exists for either) are left exactly as unresolved as before.
        if family in _MODELED_PITCHER_PROP_FAMILIES:
            subject_id, subject_name, side, line = _resolve_pitcher_prop_subject(
                parsed_contract, family, away, home, game or {},
            )

    elif family in _HITTER_FAMILIES:
        subject_type = SUBJECT_HITTER
        # Same real-series/no-real-suffix-payload-yet situation as pitcher
        # families above.

    return {
        "marketFamily": family if family != FAMILY_UNKNOWN else None,
        "period": scope,
        "subjectType": subject_type,
        "subjectId": subject_id,
        "subjectName": subject_name,
        "side": side,
        "line": line,
        "classificationStatus": base["classificationStatus"],
        "rawTaxonomy": base,
    }
