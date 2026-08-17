#!/usr/bin/env python3
"""
lib/edgelab/mlb_schedule.py
===============================
Network adapter + pure parsers for the MLB Stats API's schedule-by-date
endpoint -- a SECOND, independent source of authoritative
(awayTeam, homeTeam) -> mlbGamePk identity, alongside the existing
data/pipeline/<date>/normalized_slate.json source
(lib.edgelab.market_universe.load_game_context).

Why this exists: a standalone/manual-only Kalshi research day (one where
scripts/enrich_data.py's production slate pipeline never ran -- no
data/pipeline/<date>/normalized_slate.json was ever written) can still
fully archive its Kalshi market universe (games/<date>.jsonl,
markets/<date>.jsonl), but every Game row on that date is permanently
stuck at mlbGamePk=null: lib.edgelab.market_universe.
backfill_missing_game_pks/mark_superseded_game_identities are already
self-healing, but BOTH only ever consult load_game_context, which itself
only ever reads normalized_slate.json -- so a date that never had a
pipeline run has no game_context to backfill from, ever, no matter how
many times ingestion or repair re-runs. This blocks
scripts/edgelab/settle_markets.py's authoritative linescore/boxscore
fetch (which needs a real gamePk) for every market that date, regardless
of market family -- not specific to pitcher/hitter player props (GitHub
issue #43 is a separate, already-closed gap: automatic settlement logic
for those families exists once a gamePk/boxscore is available at all).

This module fetches statsapi.mlb.com/api/v1/schedule?sportId=1&date=...
directly by date -- no pipeline artifact required -- and builds a
game_context dict in the EXACT same shape load_game_context returns
({(awayAbbr, homeAbbr): {"gameId": <gamePk str>, "scheduledStart":,
"status":, "venue":, "kalshiKey": None}}), so it is a drop-in second
source for backfill_missing_game_pks (see
backfill_missing_game_pks_via_schedule below) -- no change to that pure
function's own signature or behavior.

Team identity is resolved via MLB's own numeric teamId (TEAM_ID_TO_ABBR
below), never by parsing/guessing a team NAME string -- team names in the
schedule response are internally inconsistent across API surfaces (e.g.
"Athletics" vs "Oakland Athletics") and a name-based table used elsewhere
in this repo for a DIFFERENT ledger (clv_update.py's TEAM_TO_ABBR) maps
Arizona to "ARI", while this repo's own archived Game/Market rows (and
Kalshi's own tickers -- see lib.kalshi_mlb_contract_parser's
TWO_LETTER_TEAM_ABBRS) use "AZ" -- reusing that table here would silently
mismatch every Arizona/Athletics game. teamId is MLB's own stable,
permanent identifier (never renamed, never reused), so TEAM_ID_TO_ABBR is
deterministic ground truth, not a guess.

Doubleheader-safe by construction: a schedule date with two games for the
same (away, home) pair is NEVER collapsed into a single context entry
(that would silently pick one leg over the other) -- see
build_schedule_game_context's docstring. This repo already has an
explicit precedent against reverse-engineering a Kalshi ticker's ET-local
HHMM encoding to infer a UTC start time (lib.edgelab.market_universe's
module docstring: "that would require guessing a DST-aware timezone
conversion this repo does not otherwise perform"), so this module never
attempts that either -- an ambiguous team pair is left out of the
returned context entirely and reported in warnings, matching this
codebase's "explicit unresolved state is better than a guessed match"
convention throughout (lib.edgelab.ticker_resolution, lib.edgelab.
market_universe, lib.edgelab.settlement all follow the same rule).
"""
import json
import urllib.request

from lib.edgelab import ids

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"

# MLB's own permanent numeric team IDs -> the exact 2/3-letter abbreviation
# convention this repository already uses everywhere else (archived Game/
# Market rows, Kalshi tickers themselves -- see
# lib.kalshi_mlb_contract_parser.TWO_LETTER_TEAM_ABBRS). Team IDs are
# stable/permanent (MLB never renumbers or reuses them even across a
# franchise relocation/rename), so this table is deterministic ground
# truth -- never a fuzzy or name-based guess. Deliberately NOT the same
# table as clv_update.py's TEAM_TO_ABBR (that table serves the older
# root bets.json ledger and maps Arizona to "ARI", not "AZ" -- reusing it
# here would silently mismatch every Arizona/Athletics game against this
# repo's own archived abbreviation convention).
TEAM_ID_TO_ABBR = {
    108: "LAA", 109: "AZ", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def fetch_schedule(date, timeout=15):
    """
    Network adapter: fetch the raw MLB Stats API schedule-by-date JSON
    for `date` (YYYY-MM-DD). Returns the parsed JSON dict, or None on ANY
    failure (network error, timeout, non-2xx, malformed JSON) -- mirrors
    lib.edgelab.mlb_boxscore.fetch_game_feed's bare try/except convention
    exactly, for the same reason: a pure-adapter module other code (and
    tests, via monkeypatch.setattr(mlb_schedule, "fetch_schedule", ...))
    imports freely must never fire a real network call at import time or
    raise into a caller that only wants "did this work or not".

    gameType=R restricts to regular-season games, matching this
    repository's existing schedule-fetch convention (clv_update.py's
    fetch_mlb_schedule_gamepks uses the identical endpoint/params).
    """
    url = f"{MLB_STATS_API}/schedule?sportId=1&date={date}&gameType=R"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def parse_schedule_games(schedule_json):
    """
    Pure. One dict per game from a raw MLB schedule-by-date response:
    {"gamePk", "awayTeamId", "homeTeamId", "gameNumber", "scheduledStart"
    (the feed's own gameDate -- a real ISO-8601 UTC timestamp, never
    derived/guessed here), "status", "venue"}. A game entry missing a
    gamePk is skipped (never fabricated). Never raises on a malformed/
    partial response -- missing sub-keys just produce None fields.
    """
    games = []
    for date_entry in (schedule_json or {}).get("dates", []):
        for g in date_entry.get("games", []):
            game_pk = g.get("gamePk")
            if not game_pk:
                continue
            teams = g.get("teams") or {}
            away_team = ((teams.get("away") or {}).get("team") or {})
            home_team = ((teams.get("home") or {}).get("team") or {})
            games.append({
                "gamePk": game_pk,
                "awayTeamId": away_team.get("id"),
                "homeTeamId": home_team.get("id"),
                "gameNumber": g.get("gameNumber"),
                "scheduledStart": g.get("gameDate"),
                "status": (g.get("status") or {}).get("detailedState"),
                "venue": (g.get("venue") or {}).get("name"),
            })
    return games


def build_schedule_game_context(parsed_games):
    """
    Pure. Groups parse_schedule_games' output by (awayAbbr, homeAbbr)
    via TEAM_ID_TO_ABBR (never a name-based guess -- see module
    docstring). Returns (context, warnings):

    - context: {(awayAbbr, homeAbbr): {"gameId": str(gamePk),
      "scheduledStart":, "status":, "venue":, "kalshiKey": None}} --
      the SAME shape lib.edgelab.market_universe.load_game_context
      returns, so it is a drop-in second source for
      backfill_missing_game_pks/mark_superseded_game_identities. Holds
      ONLY team pairs with exactly one scheduled game that date.
    - warnings: one entry per game this could NOT place in context --
      either an unmapped MLB teamId (should not happen for a real sportId=1
      schedule response, but never silently dropped if it does), or a
      genuine doubleheader (2+ games for the same (away, home) pair on
      this date). A doubleheader pair is deliberately EXCLUDED from
      context entirely rather than picking either leg -- see module
      docstring's "refuse ambiguous matches" rationale. A caller with an
      archived Game row that already carries a real (not reverse-
      engineered) scheduledStartTime or doubleheaderGameNumber may still
      disambiguate it independently; see resolve_doubleheader_candidate.
    """
    by_pair = {}
    warnings = []
    for g in parsed_games:
        away = TEAM_ID_TO_ABBR.get(g["awayTeamId"])
        home = TEAM_ID_TO_ABBR.get(g["homeTeamId"])
        if not away or not home:
            warnings.append(
                f"unmapped MLB teamId in schedule response: "
                f"away={g['awayTeamId']!r} home={g['homeTeamId']!r} gamePk={g['gamePk']}"
            )
            continue
        by_pair.setdefault((away, home), []).append(g)

    context = {}
    for (away, home), candidates in by_pair.items():
        if len(candidates) == 1:
            g = candidates[0]
            context[(away, home)] = {
                "gameId": str(g["gamePk"]),
                "scheduledStart": g["scheduledStart"],
                "status": g["status"],
                "venue": g["venue"],
                "kalshiKey": None,
            }
        else:
            game_pks = [c["gamePk"] for c in candidates]
            warnings.append(
                f"multiple scheduled games for {away}@{home} on this date "
                f"(gamePks {game_pks}) -- refusing to guess which leg archived "
                f"data refers to (doubleheader ambiguity); resolve via "
                f"resolve_doubleheader_candidate if the archived row has a "
                f"real scheduledStartTime or doubleheaderGameNumber"
            )
    return context, warnings


def resolve_doubleheader_candidate(game_row, candidates):
    """
    Pure. Attempts to pick ONE of `candidates` (parse_schedule_games'
    dicts for one ambiguous (away, home) pair -- 2+ entries) for
    `game_row` (an archived Game record), using ONLY genuine,
    already-known signals already stored on `game_row` -- never a
    reverse-engineered one:

      1. game_row["doubleheaderGameNumber"], matched exactly against a
         candidate's "gameNumber".
      2. game_row["scheduledStartTime"] (a real, previously-captured UTC
         timestamp -- e.g. from an earlier pipeline run, never derived
         here from a ticker's ET-local HHMM suffix), matched to the
         candidate whose own "scheduledStart" is closest, but ONLY when
         that candidate is unambiguously closer (>=5 minutes closer than
         every other candidate) -- a near-tie is left unresolved rather
         than guessed.

    Returns (candidate_or_None, reason). reason is None on a successful
    resolution, otherwise a specific string explaining why no candidate
    could be safely chosen -- this game_row's pair stays out of the
    caller's context, exactly like build_schedule_game_context already
    does for a pair with no archived-row signal at all.
    """
    game_number = game_row.get("doubleheaderGameNumber")
    if game_number is not None:
        matches = [c for c in candidates if c.get("gameNumber") == game_number]
        if len(matches) == 1:
            return matches[0], None
        return None, (
            f"doubleheaderGameNumber={game_number} matched {len(matches)} schedule "
            f"candidates (expected exactly 1) -- refusing to guess"
        )

    scheduled = game_row.get("scheduledStartTime")
    if scheduled:
        from datetime import datetime, timezone

        def _parse(ts):
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        target = _parse(scheduled)
        if target is None:
            return None, f"archived scheduledStartTime {scheduled!r} is not a parseable ISO-8601 timestamp"
        deltas = []
        for c in candidates:
            cand_dt = _parse(c.get("scheduledStart"))
            if cand_dt is None:
                continue
            deltas.append((abs((cand_dt - target).total_seconds()), c))
        if len(deltas) < len(candidates):
            return None, "one or more schedule candidates has no parseable scheduledStart -- refusing to guess"
        deltas.sort(key=lambda pair: pair[0])
        if len(deltas) == 1:
            return deltas[0][1], None
        closest_seconds, second_seconds = deltas[0][0], deltas[1][0]
        if second_seconds - closest_seconds >= 300:  # unambiguously closer by >= 5 minutes
            return deltas[0][1], None
        return None, (
            f"archived scheduledStartTime is within 5 minutes of {len(deltas)} schedule "
            f"candidates -- refusing to guess which doubleheader leg this is"
        )

    return None, "archived Game row has no doubleheaderGameNumber or scheduledStartTime to disambiguate"


def resolve_schedule_game_context(date):
    """
    Orchestrates fetch_schedule + parse_schedule_games +
    build_schedule_game_context for `date`. Calls fetch_schedule via this
    module's own attribute (not a local import) so tests can
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", fake) exactly
    like tests/edgelab/test_settle_markets_script.py already does for
    settle_markets_script.fetch_mlb_linescore.

    Returns (context, warnings). A fetch failure (fetch_schedule
    returning None -- network error, non-2xx, timeout, malformed JSON)
    returns ({}, [a warning naming the endpoint]) -- never raises, never
    fabricates a context.
    """
    schedule_json = fetch_schedule(date)
    if schedule_json is None:
        return {}, [f"MLB schedule fetch failed for date={date} ({MLB_STATS_API}/schedule?sportId=1&date={date}&gameType=R)"]
    parsed = parse_schedule_games(schedule_json)
    return build_schedule_game_context(parsed)


def backfill_missing_game_pks_via_schedule(games, date, *, now=None):
    """
    Second-source companion to
    lib.edgelab.market_universe.backfill_missing_game_pks, for a date
    whose pipeline-slate-derived game_context (load_game_context) is
    empty or incomplete -- most commonly because
    data/pipeline/<date>/normalized_slate.json never existed at all (a
    standalone/manual-only Kalshi research day; see module docstring).

    Reuses backfill_missing_game_pks COMPLETELY UNCHANGED -- this
    function only supplies it a DIFFERENT game_context (sourced from the
    live MLB schedule instead of the pipeline slate) and a descriptive
    source_path so the resulting mlbGamePkBackfill.matchedAgainst records
    which source actually resolved this row (an MLB Stats API endpoint
    URL rather than a normalized_slate.json path -- both are valid
    strings for that field). Every safety property
    backfill_missing_game_pks already has (never touches an
    already-resolved row, never a fuzzy/partial match, idempotent by
    construction) applies here unchanged.

    Never fetches when nothing is missing (no wasted network call for an
    already-fully-resolved date). Returns (updated_rows, warnings) --
    updated_rows is exactly what backfill_missing_game_pks would return,
    for the caller to upsert; warnings covers fetch failures and any
    doubleheader/unmapped-team ambiguity build_schedule_game_context
    reported (never silently dropped).
    """
    if not any(g.get("mlbGamePk") is None for g in games):
        return [], []

    from lib.edgelab.market_universe import backfill_missing_game_pks

    context, warnings = resolve_schedule_game_context(date)
    if not context:
        return [], warnings

    now = now or ids.utc_now_iso()
    source_path = f"{MLB_STATS_API}/schedule?sportId=1&date={date}&gameType=R"
    updated = backfill_missing_game_pks(games, context, source_path=source_path, now=now)
    return updated, warnings


def backfill_via_schedule(games, date, *, now=None):
    """
    Combined second-source backfill: mlbGamePk (lib.edgelab.market_universe.
    backfill_missing_game_pks) AND scheduledStartTime (lib.edgelab.
    market_universe.backfill_missing_scheduled_start) from ONE live MLB
    schedule fetch, for a date whose pipeline-slate-derived game_context
    (load_game_context) is empty or incomplete -- most commonly because
    data/pipeline/<date>/normalized_slate.json never existed at all (a
    standalone/manual-only Kalshi research day; see this module's own
    docstring for the full root-cause writeup).

    Deliberately combined into a single fetch rather than two independent
    per-field fetchers: both backfills consume the exact same schedule
    response, and scripts/edgelab/ingest_market_observations.py /
    scripts/edgelab/repair_game_identity.py both need BOTH fields
    backfilled from this same second source -- fetching twice would
    double the live network calls (and, in a test/CI environment mocking
    fetch_schedule, double the mock call count) for zero additional
    information. backfill_missing_game_pks_via_schedule (above) is kept
    unchanged and still covers the mlbGamePk-only case on its own for any
    other caller that only needs that half.

    Never fetches when nothing is missing (neither field, on any row) --
    a no-op, no-network-call path for an already-fully-resolved date or
    an ordinary slate-backed day. Returns (mlb_gamepk_updated_rows,
    scheduled_start_updated_rows, warnings); apply
    mlb_gamepk_updated_rows to storage before re-reading for any
    subsequent pass, matching the existing two-pass upsert pattern both
    callers already use for the pipeline-slate case.
    """
    if not any(g.get("mlbGamePk") is None or g.get("scheduledStartTime") is None for g in games):
        return [], [], []

    from lib.edgelab.market_universe import backfill_missing_game_pks, backfill_missing_scheduled_start

    context, warnings = resolve_schedule_game_context(date)
    if not context:
        return [], [], warnings

    now = now or ids.utc_now_iso()
    source_path = f"{MLB_STATS_API}/schedule?sportId=1&date={date}&gameType=R"
    gamepk_updated = backfill_missing_game_pks(games, context, source_path=source_path, now=now)
    # Merge gamepk_updated into games BEFORE computing scheduled_start_updated:
    # storage.upsert_records replaces a row's ENTIRE content by gameId, so if
    # a row gets both fields backfilled from this same call, writing
    # scheduled_start_updated's copy of it afterward (computed from the
    # pre-mlbGamePk-backfill row) would silently clobber the mlbGamePk
    # gamepk_updated just wrote back to null.
    by_id = {g["gameId"]: g for g in gamepk_updated}
    merged_games = [by_id.get(g["gameId"], g) for g in games]
    scheduled_start_updated = backfill_missing_scheduled_start(merged_games, context, source_path=source_path, now=now)
    return gamepk_updated, scheduled_start_updated, warnings
