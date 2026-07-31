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


def classify_contract(parsed_contract, away_team=None, home_team=None):
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
        # subjectId/subjectName resolution still requires a real observed
        # market-suffix payload for this family (not yet captured -- only
        # aggregate event/market counts have been seen so far), so it is
        # deliberately left unimplemented rather than guessed; a contract
        # in this family is always routed to modelSupportStatus=UNSUPPORTED
        # downstream regardless.

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
