#!/usr/bin/env python3
"""
lib/kalshi_mlb_contract_parser.py
====================================
Canonical Kalshi MLB contract parser — the first stage of the universal
market engine (docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md, Phase 2).

Turns one raw Kalshi market dict (the shape returned by
`GET /trade-api/v2/markets`: ticker, event_ticker, series_ticker, title,
subtitle/yes_sub_title, status, close_time, yes_bid, yes_ask, no_bid,
no_ask, ...) into the canonical contract schema every downstream stage
(classifier, probability adapters, slate exposure, research database)
consumes.

This module is pure: no file I/O, no network, no clock reads. It never
raises on a malformed/unrecognized ticker — an unparseable contract still
returns a canonical dict with as many fields as could be determined and
the rest left `None` (never a fabricated placeholder, never zero).

Design notes:
  - The calendar date is derived directly from the ticker's own
    YYMONDD-encoded event suffix (e.g. "26JUL30" -> "2026-07-30") rather
    than requiring the caller to supply it — a contract is
    self-describing.
  - Team abbreviation splitting reuses the same 2-letter-vs-3-letter
    disambiguation rule already proven in
    scripts/build_kalshi_registry.py's parse_suffix() (TB, AZ, SF, SD,
    KC) — duplicated here deliberately rather than imported, because
    build_kalshi_registry.py has no `if __name__` guard and fires real
    network calls at import time, which would be unsafe to import from
    a pure library module or any test.
  - Doubleheader isolation: a Kalshi ticker's own date+time encoding
    already disambiguates two games between the same two teams on the
    same date (different HHMM), so game identity is never ambiguous by
    ticker alone. `doubleheaderGameNumber` (1-indexed, ordered by
    scheduled time) is only ever set when the caller supplies
    `known_games` context for that date — never guessed from the ticker
    alone, since Kalshi does not label which leg of a doubleheader a
    ticker refers to.
"""
import re

MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
MONTH_INDEX = {m: i + 1 for i, m in enumerate(MONTHS)}

# 2-letter MLB team abbreviations Kalshi uses on its tickers. Everything
# else is assumed 3-letter. Matches build_kalshi_registry.py's
# TWO_LETTER_ABBRS exactly (kept in sync manually; see module docstring
# for why this is a deliberate duplication, not an oversight).
TWO_LETTER_TEAM_ABBRS = {'TB', 'AZ', 'SF', 'SD', 'KC'}


def kalshi_date_code_to_iso(code):
    """'26JUL30' -> '2026-07-30'. Returns None if unparseable."""
    if not code or len(code) < 7:
        return None
    m = re.match(r'^(\d{2})([A-Z]{3})(\d{2})$', code)
    if not m:
        return None
    yy, mon, dd = m.groups()
    if mon not in MONTH_INDEX:
        return None
    year = 2000 + int(yy)
    month = MONTH_INDEX[mon]
    try:
        day = int(dd)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _split_teams(rest):
    """
    Split a "{HHMM}{AWAY}{HOME}" tail (with HHMM already stripped) into
    (away, home), preferring a known 2-letter abbreviation split when
    ambiguous. Returns (None, None) if no valid split exists.
    """
    candidates = []
    for a_len in (2, 3):
        if len(rest) <= a_len:
            continue
        away, home = rest[:a_len], rest[a_len:]
        if not away.isalpha() or not home.isalpha():
            continue
        score = 1 if away in TWO_LETTER_TEAM_ABBRS else 0
        candidates.append((score, a_len, away, home))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    _, _, away, home = candidates[0]
    return away, home


_DOUBLEHEADER_MARKER_RE = re.compile(r"^(?P<teams>[A-Z]{4,6})G(?P<number>[1-9])$")


def _split_doubleheader_marker(teams):
    """
    Split a team-pair segment into (teams, doubleheader_game_number).

    Kalshi encodes a doubleheader leg directly in the event ticker by
    appending G1/G2 to the team pair: "BOSNYYG1" / "BOSNYYG2". That digit
    makes the segment non-alphabetic, so _split_teams -- which requires
    both halves to be alphabetic, deliberately, so it never invents a team
    -- returned (None, None) for EVERY doubleheader market. The whole
    downstream chain then collapsed: no awayTeam/homeTeam, therefore no
    gameId, therefore no Game dimension row, therefore markets carrying
    gameId=null, therefore settlement recording "missing_final_score".

    Stripping the marker here keeps that identity instead of discarding
    it, and is unambiguous: MLB team abbreviations are alphabetic, so a
    trailing "G<digit>" is never part of one. A segment without the marker
    is returned unchanged with a None game number.
    """
    if not teams:
        return teams, None
    match = _DOUBLEHEADER_MARKER_RE.match(teams)
    if not match:
        return teams, None
    return match.group("teams"), int(match.group("number"))


def parse_event_suffix(series_ticker, event_ticker):
    """
    Parse a Kalshi event ticker's suffix (everything after the series
    prefix) into (date_iso, time_str, away, home). Returns a dict with
    all four keys, any of which may be None if not determinable.

    e.g. series_ticker="KXMLBGAME", event_ticker="KXMLBGAME-26JUL302140BOSATH"
    -> {"date": "2026-07-30", "time_str": "2140", "away": "BOS", "home": "ATH",
        "game_number": None}

    Kalshi marks a doubleheader leg by appending G1/G2 to the team pair
    ("...1305BOSNYYG1"); that marker is stripped before the team split and
    returned as `game_number`, so the leg identity Kalshi already supplies
    survives parsing instead of being destroyed by it (see
    _split_doubleheader_marker).
    """
    out = {"date": None, "time_str": None, "away": None, "home": None, "game_number": None}
    if not event_ticker or not series_ticker:
        return out
    prefix = series_ticker + "-"
    if not event_ticker.startswith(prefix):
        return out
    suffix = event_ticker[len(prefix):]
    if len(suffix) < 11:  # YYMONDD(7) + HHMM(4) minimum
        return out
    date_code, rest = suffix[:7], suffix[7:]
    out["date"] = kalshi_date_code_to_iso(date_code)
    if len(rest) < 6:  # HHMM(4) + at least 2 team chars
        return out
    time_str, teams = rest[:4], rest[4:]
    if not time_str.isdigit():
        return out
    out["time_str"] = time_str
    teams, out["game_number"] = _split_doubleheader_marker(teams)
    away, home = _split_teams(teams)
    out["away"], out["home"] = away, home
    return out


def resolve_doubleheader_game_number(date_iso, away, home, time_str, known_games):
    """
    Determine which leg of a doubleheader this contract belongs to.

    known_games: optional list of dicts, each with at least
    {"date", "away", "home", "time_str"}, describing every game
    scheduled for this date (e.g. from the slate). Only games matching
    the same date+away+home are considered; they are sorted by
    time_str and this contract's 1-indexed position among them is
    returned. Returns None if known_games is not supplied, if fewer
    than 2 matching games exist (no doubleheader), or if time_str is
    missing.
    """
    if not known_games or not time_str or not date_iso or not away or not home:
        return None
    matches = sorted({
        g.get("time_str") for g in known_games
        if g.get("date") == date_iso and g.get("away") == away and g.get("home") == home
        and g.get("time_str")
    })
    if len(matches) < 2:
        return None
    try:
        return matches.index(time_str) + 1
    except ValueError:
        return None


def _price_to_pct(cents_or_dollars):
    """Normalize a Kalshi price (0-100 cents or 0-1 dollars) to a 0-100 pct. None stays None."""
    if cents_or_dollars is None:
        return None
    try:
        v = float(cents_or_dollars)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if v > 1.0 else round(v * 100, 2)


def parse_contract(raw_market, known_games=None):
    """
    Parse one raw Kalshi market dict into the canonical contract schema.

    Args:
        raw_market: dict with Kalshi's native field names — at minimum
            `ticker`; ideally also `event_ticker`, `series_ticker` (or
            it is derived from `ticker`'s leading segment),
            `title`/`subtitle`/`yes_sub_title`, `status`, `close_time`,
            `yes_bid`, `yes_ask`, `no_bid`, `no_ask`.
        known_games: optional list of {"date","away","home","time_str"}
            dicts for doubleheader disambiguation (see
            resolve_doubleheader_game_number).

    Returns a dict with exactly the canonical schema fields. Missing
    values are None — never zero, never a fabricated placeholder.
    """
    ticker = raw_market.get("ticker") or raw_market.get("market_ticker")
    event_ticker = raw_market.get("event_ticker") or raw_market.get("eventTicker")
    series_ticker = raw_market.get("series_ticker") or raw_market.get("seriesTicker")

    if not series_ticker and ticker:
        series_ticker = ticker.split("-", 1)[0]
    if not event_ticker and ticker and series_ticker:
        # Event ticker is everything up to (but not including) the final
        # "-{suffix}" market-specific segment; since we don't know the
        # suffix boundary a priori, fall back to series prefix + first
        # dash-delimited date/time/teams segment only when the caller
        # didn't supply event_ticker directly.
        parts = ticker.split("-")
        if len(parts) >= 2:
            event_ticker = f"{parts[0]}-{parts[1]}"

    parsed_suffix = parse_event_suffix(series_ticker, event_ticker) if series_ticker and event_ticker else {
        "date": None, "time_str": None, "away": None, "home": None,
    }

    market_suffix = None
    if ticker and event_ticker and ticker.startswith(event_ticker + "-"):
        market_suffix = ticker[len(event_ticker) + 1:]

    # Kalshi's own G<n> marker is authoritative when present -- it is the
    # upstream source's explicit statement of which leg this contract is,
    # so it wins over resolve_doubleheader_game_number's ordering
    # inference over a supplied slate (which is only a fallback for the
    # older tickers that carry no marker).
    doubleheader_game_number = parsed_suffix.get("game_number")
    if doubleheader_game_number is None:
        doubleheader_game_number = resolve_doubleheader_game_number(
            parsed_suffix["date"], parsed_suffix["away"], parsed_suffix["home"],
            parsed_suffix["time_str"], known_games,
        )

    title = raw_market.get("title")
    subtitle = raw_market.get("subtitle") or raw_market.get("yes_sub_title")

    yes_bid = _price_to_pct(raw_market.get("yes_bid_dollars") if raw_market.get("yes_bid_dollars") is not None else raw_market.get("yes_bid"))
    yes_ask = _price_to_pct(raw_market.get("yes_ask_dollars") if raw_market.get("yes_ask_dollars") is not None else raw_market.get("yes_ask"))
    no_bid = _price_to_pct(raw_market.get("no_bid_dollars") if raw_market.get("no_bid_dollars") is not None else raw_market.get("no_bid"))
    no_ask = _price_to_pct(raw_market.get("no_ask_dollars") if raw_market.get("no_ask_dollars") is not None else raw_market.get("no_ask"))

    return {
        "ticker": ticker,
        "eventTicker": event_ticker,
        "seriesTicker": series_ticker,
        "marketTitle": title,
        "marketSubtitle": subtitle,
        "gameId": f"{parsed_suffix['date']}_{parsed_suffix['away']}_{parsed_suffix['home']}_{parsed_suffix['time_str']}"
                  if all(parsed_suffix.get(k) for k in ("date", "away", "home", "time_str")) else None,
        "date": parsed_suffix["date"],
        "awayTeam": parsed_suffix["away"],
        "homeTeam": parsed_suffix["home"],
        "scheduledTimeStr": parsed_suffix["time_str"],
        "doubleheaderGameNumber": doubleheader_game_number,
        "marketSuffix": market_suffix,
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": no_bid,
        "noAsk": no_ask,
        "lastPrice": _price_to_pct(raw_market.get("last_price_dollars") if raw_market.get("last_price_dollars") is not None else raw_market.get("last_price")),
        "volume": raw_market.get("volume"),
        "marketStatus": raw_market.get("status"),
        "closeTime": raw_market.get("close_time") or raw_market.get("closeTime"),
        "raw": raw_market,
    }
