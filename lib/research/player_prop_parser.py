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


def _resolve_team_abbr(raw_token, away_team=None, home_team=None):
    """
    Prefer an exact prefix match against the game's own known away/home
    abbreviation (authoritative -- see module docstring's 46,784-row
    verification). Falls back to the same 2-vs-3-letter heuristic
    lib.kalshi_mlb_contract_parser uses for away/home splitting only
    when the caller doesn't have game context yet. Returns None rather
    than guessing when known teams were supplied but neither matches.
    """
    if away_team and raw_token.startswith(away_team):
        return away_team
    if home_team and raw_token.startswith(home_team):
        return home_team
    if away_team or home_team:
        return None
    if raw_token[:2] in TWO_LETTER_TEAM_ABBRS:
        return raw_token[:2]
    if len(raw_token) > 3:
        return raw_token[:3]
    return None


def parse_player_prop_market(market_ticker, event_ticker=None, title=None, subtitle=None,
                              away_team=None, home_team=None):
    """
    Pure. Returns a dict (never None, never raises) describing everything
    this module can determine from the raw ticker+title alone:

      parseStatus: "PARSED" | "UNPARSEABLE"
      unparseableReason: str | None
      teamAbbr: str | None -- resolved via away_team/home_team when
        given (authoritative), else a best-effort 2-vs-3-letter
        heuristic guess.
      rawPlayerToken: str | None -- the ticker's own player-identifying
        segment, verbatim (e.g. "LADESHEEHAN80"). Audit/corroboration
        only -- see module docstring.
      tokenFirstInitial / tokenLastNameCompact / tokenNumericSuffix:
        str | None -- structural decomposition of rawPlayerToken, when
        it matches the expected shape. tokenNumericSuffix is a
        JERSEY-NUMBER-STYLE STRING, explicitly never an MLB player id.
      threshold: int | None -- the contract's "N+" line, from the ticker
        suffix (authoritative).
      titleThreshold: int | None -- the same line, independently parsed
        from the title text, for cross-checking.
      thresholdMismatch: bool -- True only if both were determined and
        disagree (never observed in this repository's archive, but
        checked rather than assumed).
      comparisonOperator: "AT_LEAST" | None -- these are always literal
        N+ contracts (YES iff actual >= N), never the game-total
        family's "over N.5" framing -- see
        lib.edgelab.player_prop_settlement's module docstring for why
        the two must never share logic.
      displayNameRaw: str | None -- the title's player name with any
        trailing parenthetical team tag stripped (e.g. "Max Muncy").
      displayNameParentheticalTeam: str | None -- e.g. "LAD" when the
        title carried "Max Muncy (LAD)".
      normalizedNameVariants: frozenset[str] -- see
        normalized_name_variants(); empty if displayNameRaw is None.
    """
    result = {
        "parseStatus": "UNPARSEABLE",
        "unparseableReason": None,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "teamAbbr": None,
        "rawPlayerToken": None,
        "tokenFirstInitial": None,
        "tokenLastNameCompact": None,
        "tokenNumericSuffix": None,
        "threshold": None,
        "titleThreshold": None,
        "thresholdMismatch": False,
        "comparisonOperator": None,
        "displayNameRaw": None,
        "displayNameParentheticalTeam": None,
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

    team_abbr = _resolve_team_abbr(raw_token, away_team, home_team)
    result["teamAbbr"] = team_abbr

    remainder = raw_token[len(team_abbr):] if team_abbr else raw_token
    token_match = _TOKEN_RE.match(remainder)
    if token_match:
        result["tokenFirstInitial"] = token_match.group(1)
        result["tokenLastNameCompact"] = token_match.group(2)
        result["tokenNumericSuffix"] = token_match.group(3)

    if title:
        title_match = _TITLE_RE.match(title.strip())
        if title_match:
            name_part = title_match.group("name").strip()
            title_threshold = int(title_match.group("threshold"))
            result["titleThreshold"] = title_threshold
            result["thresholdMismatch"] = title_threshold != result["threshold"]

            paren_match = _PARENTHETICAL_TEAM_RE.match(name_part)
            if paren_match:
                result["displayNameRaw"] = paren_match.group("core").strip()
                result["displayNameParentheticalTeam"] = paren_match.group("team")
            else:
                result["displayNameRaw"] = name_part

            result["normalizedNameVariants"] = normalized_name_variants(result["displayNameRaw"])

    return result
