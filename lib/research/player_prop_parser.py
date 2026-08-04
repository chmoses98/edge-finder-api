#!/usr/bin/env python3
"""
lib/research/player_prop_parser.py
=====================================
Shared, pure parser for Kalshi single-game MLB player-prop markets
(KXMLBKS/KXMLBOUTS/KXMLBHIT/KXMLBTB/KXMLBHRR/KXMLBRBI/KXMLBSB -- see
lib/research/market_taxonomy.py's SERIES_FAMILY_MAP). Used identically by
ingestion (market_taxonomy.classify_market -> lib/edgelab/market_universe.py,
so the archived MarketObservation/Market records carry a real
team/player/threshold) and by settlement
(lib/edgelab/player_prop_settlement.py, so the exact same team-
abbreviation/threshold/display-name facts drive the final YES/NO
decision) -- one parser, never two independent copies of this logic
(GitHub issue #43).

Real-data audit (Emmet Sheehan/Shohei Ohtani examples from issue #43,
cross-checked against EVERY data/kalshi_registry_snapshots/
kalshi_search_*.json snapshot this repository holds -- 46,784
player-prop market rows total, 0 exceptions found):

    KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9
    "Emmet Sheehan: 9+ strikeouts?"

  - event_ticker ("KXMLBKS-26AUG021920BOSLAD") encodes date+time+teams,
    identically to KXMLBGAME -- see lib.kalshi_mlb_contract_parser.
  - The market-ticker suffix after the event ticker ("LADESHEEHAN80-9")
    splits via ONE rsplit("-", 1) into a player token ("LADESHEEHAN80")
    and an integer threshold ("9"). Verified true for all 46,784 rows.
  - The player token is ALWAYS exactly
    {teamAbbr}{firstInitial}{lastNameCompact}{trailingDigits}, with
    teamAbbr an exact prefix match against the game's own away/home
    abbreviation (verified against every one of the 46,784 rows; never
    ambiguous, never requiring a 2-vs-3-letter guess when away/home are
    already known from game context). trailingDigits is a
    jersey-number-STYLE token, NOT an MLBAM player id (see
    lib.edgelab.player_resolution's docstring) -- kept here only as weak
    secondary corroboration, never as an identity.
  - lastNameCompact is Kalshi's OWN, sometimes-inconsistent rendering of
    the last name (periods/apostrophes stripped, and accented characters
    are sometimes dropped entirely rather than transliterated -- e.g.
    the same real last name "Hernández" appears as both "HERNANDEZ" and
    "HERNNDEZ" in different tickers in this archive). This inconsistency
    is PROOF the token must never be trusted as an authoritative identity
    source -- this module still extracts it (for audit/corroboration
    only), but the market TITLE's display name (always a clean
    human-readable "First Last" string, optionally suffixed with
    "Jr."/"II"/etc., optionally followed by a parenthetical team tag e.g.
    "Max Muncy (LAD)") is the real identity signal consumed by player
    resolution.
  - Threshold cross-checked between ticker suffix and title's "N+" text
    for all 46,784 rows: always identical. The ticker value is treated
    as authoritative (simpler, more structured extraction); a mismatch
    (never observed, but not assumed impossible) is recorded rather than
    silently ignored or silently trusted.

This module makes no network calls and never raises on a malformed
ticker/title -- an unparseable market still returns a result dict, with
parseStatus="UNPARSEABLE" and a specific unparseableReason, never a
fabricated guess.
"""
import re
import unicodedata

from lib.kalshi_mlb_contract_parser import TWO_LETTER_TEAM_ABBRS

_TOKEN_RE = re.compile(r"^([A-Z])([A-Z]+?)(\d+)$")
_TITLE_RE = re.compile(r"^(?P<name>.+?)\s*:\s*(?P<threshold>\d+)\+\s*(?P<stat>.+?)\s*\?\s*$")
_PARENTHETICAL_TEAM_RE = re.compile(r"^(?P<core>.+?)\s*\((?P<team>[A-Za-z.]{2,5})\)\s*$")

# Suffix tokens dropped to build the alternate ("core name") comparison
# key -- MLB Stats API's own fullName and Kalshi's title text are not
# perfectly consistent about including these (e.g. "Bobby Witt Jr." vs
# a hypothetical boxscore fullName of just "Bobby Witt").
_SUFFIX_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

# Correction round (issue #43 review): the EXACT stat-text wording Kalshi
# uses per family, verified against every one of the 46,784 real
# player-prop rows this repository's archive holds (0 exceptions --
# e.g. "9+ strikeouts?" for every KXMLBKS row, "17+ Outs Recorded?" for
# every KXMLBOUTS row). Compared case-insensitively/whitespace-
# normalized (see _normalize_stat_text) since Kalshi's own casing is
# inconsistent ("RBIs" vs a hypothetical "rbis"), but the WORDING itself
# must match exactly -- this is what lets settle_player_prop_market()
# catch a market whose title text doesn't actually describe the family
# its own series ticker implies (a corrupted/re-purposed ticker), rather
# than trusting the family blindly.
FAMILY_STAT_TEXT = {
    "pitcher_strikeouts": "strikeouts",
    "pitcher_outs": "outs recorded",
    "hitter_hits": "hits",
    "hitter_total_bases": "total bases",
    "hitter_hits_runs_rbis": "hits + runs + rbis",
    "hitter_rbis": "rbis",
    "hitter_stolen_bases": "stolen bases",
}


def _normalize_stat_text(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def normalize_player_name(name):
    """
    Lowercase, accent-stripped (Unicode NFKD decompose + drop combining
    marks), punctuation-stripped (periods/apostrophes/commas), single-
    spaced form of a display name. Pure string normalization -- this is
    the canonical key two spellings of the "same" name are compared
    under; it is NOT itself a fuzzy match (no edit-distance, no
    substring matching -- see lib.edgelab.player_resolution).
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[.'’,]", "", no_accents)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def normalized_name_variants(name):
    """
    Both the full normalized name AND a suffix-stripped variant (drops a
    trailing jr/sr/ii/iii/iv/v token), as a frozenset -- a caller
    comparing against a boxscore fullName should accept either form
    matching, since MLB Stats API and Kalshi's own title text are not
    perfectly consistent about including a player's suffix. Empty for a
    falsy `name`.
    """
    full = normalize_player_name(name)
    if not full:
        return frozenset()
    tokens = full.split(" ")
    variants = {full}
    if len(tokens) > 1 and tokens[-1] in _SUFFIX_TOKENS:
        variants.add(" ".join(tokens[:-1]))
    return frozenset(variants)


TEAM_RESOLVED = "RESOLVED"
TEAM_UNRESOLVED_NO_CONTEXT = "UNRESOLVED_NO_CONTEXT"
TEAM_UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"


def _resolve_team_abbr(raw_token, away_team=None, home_team=None):
    """
    Prefer an exact prefix match against the game's own known away/home
    abbreviation (authoritative -- see module docstring's 46,784-row
    verification). Falls back to the same 2-vs-3-letter heuristic
    lib.kalshi_mlb_contract_parser uses for away/home splitting only
    when the caller doesn't have game context at all. Returns
    (team_abbr, status):

      status == TEAM_RESOLVED: `team_abbr` is an exact, authoritative
        match against away_team/home_team.
      status == TEAM_UNRESOLVED_NO_CONTEXT: neither away_team nor
        home_team was supplied at all -- `team_abbr` is a best-effort
        heuristic guess (or None), fine for research/classification
        labeling but NOT authoritative enough to settle money on (see
        lib.edgelab.player_prop_settlement, which always supplies both
        and therefore never sees this status).
      status == TEAM_UNRESOLVED_CONFLICT: away_team AND/OR home_team
        WAS supplied, but the ticker's own token doesn't start with
        either one -- a genuine integrity failure (a malformed or
        cross-game ticker), never silently guessed at. `team_abbr` is
        None in this case.
    """
    if away_team and raw_token.startswith(away_team):
        return away_team, TEAM_RESOLVED
    if home_team and raw_token.startswith(home_team):
        return home_team, TEAM_RESOLVED
    if away_team or home_team:
        return None, TEAM_UNRESOLVED_CONFLICT
    if raw_token[:2] in TWO_LETTER_TEAM_ABBRS:
        return raw_token[:2], TEAM_UNRESOLVED_NO_CONTEXT
    if len(raw_token) > 3:
        return raw_token[:3], TEAM_UNRESOLVED_NO_CONTEXT
    return None, TEAM_UNRESOLVED_NO_CONTEXT


def parse_player_prop_market(market_ticker, event_ticker=None, title=None, subtitle=None,
                              away_team=None, home_team=None, family=None):
    """
    Pure. Returns a dict (never None, never raises) describing everything
    this module can determine from the raw ticker+title alone. Stays
    deliberately LENIENT/best-effort at the top-level parseStatus (so
    ingestion -- lib.research.market_taxonomy.classify_market -- can
    still label a market for research even from odd/partial data,
    matching this repository's "never silently drop, always warn"
    convention); every INTEGRITY concern below is instead surfaced as
    its own explicit field, which lib.edgelab.player_prop_settlement
    (which always supplies away_team/home_team/family) checks and turns
    into a specific SETTLEMENT_UNRESOLVED reason -- a hard requirement
    is never silently downgraded to a soft one, but ingestion and
    settlement can each apply the strictness level appropriate to their
    own stakes from the SAME parse.

      parseStatus: "PARSED" | "UNPARSEABLE"
      unparseableReason: str | None -- set only for a structurally
        broken ticker (can't even extract team/threshold).
      teamAbbr: str | None -- resolved via away_team/home_team when
        given (authoritative), else a best-effort 2-vs-3-letter
        heuristic guess.
      teamResolutionStatus: TEAM_RESOLVED | TEAM_UNRESOLVED_NO_CONTEXT |
        TEAM_UNRESOLVED_CONFLICT -- see _resolve_team_abbr. Settlement
        must treat anything other than TEAM_RESOLVED as a hard block
        (GitHub issue #43 correction: "the ticker team cannot be
        resolved as exactly one of the game's teams" must never fall
        back to searching both rosters).
      rawPlayerToken: str | None -- the ticker's own player-identifying
        segment, verbatim (e.g. "LADESHEEHAN80"). Audit/corroboration
        only -- see module docstring.
      tokenFirstInitial / tokenLastNameCompact / tokenNumericSuffix:
        str | None -- structural decomposition of rawPlayerToken, when
        it matches the expected shape. tokenNumericSuffix is a
        JERSEY-NUMBER-STYLE STRING, explicitly never an MLB player id.
      tokenMalformed: bool -- True when rawPlayerToken exists but its
        post-team remainder does NOT match the expected
        {firstInitial}{lastNameCompact}{digits} shape at all.
      threshold: int | None -- the contract's "N+" line, from the ticker
        suffix (authoritative).
      titleThreshold: int | None -- the same line, independently parsed
        from the title text, for cross-checking.
      thresholdMismatch: bool -- True only if both were determined and
        disagree (never observed in this repository's archive, but
        checked rather than assumed).
      titleParseStatus: "PARSED" | "UNPARSEABLE" | "NOT_PROVIDED" --
        whether the title matched the expected "Name: N+ stat?" shape
        at all.
      comparisonOperator: "AT_LEAST" | None -- these are always literal
        N+ contracts (YES iff actual >= N), never the game-total
        family's "over N.5" framing -- see
        lib.edgelab.player_prop_settlement's module docstring for why
        the two must never share logic.
      displayNameRaw: str | None -- the title's player name with any
        trailing parenthetical team tag stripped (e.g. "Max Muncy").
      displayNameParentheticalTeam: str | None -- e.g. "LAD" when the
        title carried "Max Muncy (LAD)".
      parentheticalTeamConflict: bool -- True when a parenthetical team
        tag is present AND differs from the ticker-resolved teamAbbr
        (both known).
      statTextText: str | None -- the title's own stat-description text
        verbatim (e.g. "total bases"), before the family cross-check.
      statTextFamilyMismatch: bool -- True only when `family` was
        supplied AND the title's stat text does not match
        FAMILY_STAT_TEXT[family] (case/whitespace-insensitively).
        Always False when `family` isn't supplied (nothing to check).
      normalizedNameVariants: frozenset[str] -- see
        normalized_name_variants(); empty if displayNameRaw is None.
    """
    result = {
        "parseStatus": "UNPARSEABLE",
        "unparseableReason": None,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "teamAbbr": None,
        "teamResolutionStatus": None,
        "rawPlayerToken": None,
        "tokenFirstInitial": None,
        "tokenLastNameCompact": None,
        "tokenNumericSuffix": None,
        "tokenMalformed": False,
        "threshold": None,
        "titleThreshold": None,
        "thresholdMismatch": False,
        "titleParseStatus": "NOT_PROVIDED",
        "comparisonOperator": None,
        "displayNameRaw": None,
        "displayNameParentheticalTeam": None,
        "parentheticalTeamConflict": False,
        "statText": None,
        "statTextFamilyMismatch": False,
        "normalizedNameVariants": frozenset(),
    }

    if not market_ticker or not event_ticker or not market_ticker.startswith(event_ticker + "-"):
        result["unparseableReason"] = "ticker_missing_event_prefix"
        return result

    suffix = market_ticker[len(event_ticker) + 1:]
    if "-" not in suffix:
        result["unparseableReason"] = "ticker_suffix_not_two_part"
        return result

    raw_token, threshold_str = suffix.rsplit("-", 1)
    if not raw_token or not threshold_str.isdigit():
        result["unparseableReason"] = "ticker_threshold_not_numeric"
        return result

    result["rawPlayerToken"] = raw_token
    result["threshold"] = int(threshold_str)
    result["comparisonOperator"] = "AT_LEAST"
    result["parseStatus"] = "PARSED"

    team_abbr, team_status = _resolve_team_abbr(raw_token, away_team, home_team)
    result["teamAbbr"] = team_abbr
    result["teamResolutionStatus"] = team_status

    remainder = raw_token[len(team_abbr):] if team_abbr else raw_token
    token_match = _TOKEN_RE.match(remainder)
    if token_match:
        result["tokenFirstInitial"] = token_match.group(1)
        result["tokenLastNameCompact"] = token_match.group(2)
        result["tokenNumericSuffix"] = token_match.group(3)
    else:
        result["tokenMalformed"] = True

    if title:
        title_match = _TITLE_RE.match(title.strip())
        if not title_match:
            result["titleParseStatus"] = "UNPARSEABLE"
        else:
            result["titleParseStatus"] = "PARSED"
            name_part = title_match.group("name").strip()
            title_threshold = int(title_match.group("threshold"))
            result["titleThreshold"] = title_threshold
            result["thresholdMismatch"] = title_threshold != result["threshold"]
            result["statText"] = title_match.group("stat").strip()

            if family is not None:
                expected_stat_text = FAMILY_STAT_TEXT.get(family)
                result["statTextFamilyMismatch"] = (
                    expected_stat_text is not None
                    and _normalize_stat_text(result["statText"]) != expected_stat_text
                )

            paren_match = _PARENTHETICAL_TEAM_RE.match(name_part)
            if paren_match:
                result["displayNameRaw"] = paren_match.group("core").strip()
                result["displayNameParentheticalTeam"] = paren_match.group("team")
                if team_abbr and result["displayNameParentheticalTeam"] != team_abbr:
                    result["parentheticalTeamConflict"] = True
            else:
                result["displayNameRaw"] = name_part

            result["normalizedNameVariants"] = normalized_name_variants(result["displayNameRaw"])

    return result
