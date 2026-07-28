#!/usr/bin/env python3
"""
scripts/post_fetch_gate.py v2.2
================================
Data quality gate - runs after fetch_savant_pitchers.py and fetch_lineups.py,
BEFORE odds fetch, Kalshi registry build, merge_odds, and enrich_data.

Phase 6 (v2.2): converted to a network/file-adapter-free pure-transform
plus orchestration-adapter shape (see docs/IMMUTABLE_PIPELINE.md), matching
the pattern established for merge_odds.py (Phase 4) and
fetch_lineups.py/fetch_savant_pitchers.py (Phase 5). No gate decision,
message text, exit code, or file-write condition changed -- every check
below still fires under the exact same conditions it always did; only the
code doing so was reorganized into pure per-game evaluators
(evaluate_game_pitcher_savant, evaluate_game_team_stats), a pure
per-slate transform (apply_post_fetch_gate_immutable), a pure stale-date
lookup (find_stale_slate_issue), and an orchestration adapter (main())
that does all file I/O, printing, and process-exit side effects. Before
this phase the entire script ran as top-level module code with no
main()/importable functions and no `if __name__` guard -- every existing
test exercised it via subprocess. The `if __name__ == '__main__':` guard
added here changes nothing about production behavior (the workflow always
invokes this file directly, which always sets __name__ == '__main__');
it exists solely so the new pure functions can be imported and exercised
directly without triggering a live run as an import side effect.

NEW in v2.1:
  - Game-aware quarantine: a single game with one-side null xFIP/seasonFIP
    is quarantined (excludedFromSlate=True) instead of aborting the whole slate.
    This handles resumed/suspended games whose continuation starter has no Savant data.
  - Normal game with both sides null xFIP is still a hard fail.
  - >50% of games with dual null xFIP is still a hard fail.
  - Quarantined games are listed in fetch_status.json; all their markets are
    blocked from real-money output by the downstream risk_gate.py.

NEW in v2.0:
  - Accepts requested_date as first CLI argument (passed from GitHub Actions as $DATE)
  - Hard-fails if slate.json date != requested_date (STALE DATE detection)
  - Writes data/fetch_status.json on both pass and fail
  - Prints STALE SLATE ABORT messages on date mismatch

At this point in the pipeline:
  - slate.json has games + pitcherSavant blocks (from Vercel)
  - savant enrichment has run (fbPct, TTO, velocity - may be partial)
  - lineups have been fetched (may be partial if not yet posted)
  - teamstats are loaded

Hard FAIL (exit 1) - pipeline genuinely broken:
  - slate.json missing or empty
  - slate.json date != requested_date (STALE DATA)
  - BOTH starters in the same game have null xFIP AND null seasonFIP
  - >50% of games have dual null xFIP (fetch_savant_pitchers likely fully failed)

QUARANTINE (exclude game, continue) - single-game data issue:
  - ONE side of a game has pitcherSavant dict but xFIP=null AND seasonFIP=null
  - This matches resumed/suspended games whose continuation starter has no Savant data
  - Quarantined game: excludedFromSlate=True, all markets EXCLUDED, no real-money output

WARN (continue, log) - data incomplete but pipeline can recover:
  - Single side pitcherSavant=null (entire block null, starter TBD)
  - lineupConfirmed=null
  - last7RpG and last15RpG both null
  - xFIP=null but seasonFIP available

Real finding preserved exactly (Phase 6 pre-refactor audit): the
quarantine-marker write-back to data/slate.json has no dependency on
whether hard errors are later found in the teamStats pass -- a run that
quarantines one game AND separately hard-fails on a different game (e.g.
all-null RpG) still persists the quarantine marker to data/slate.json
before exiting 1. This is real, load-bearing behavior, preserved by
keeping the write-back (main(), guarded only by `if quarantined_games:`)
strictly before the final errors-check-and-exit block, exactly as before.

Not carried forward: `months_abbr`, a module-level list of month
abbreviations that was assigned in the original script but never once
referenced anywhere in the file (confirmed by grep) -- genuinely inert
dead code with zero observable effect, not a decision branch, so it is
not reproduced here. `no_rolling_rpg`, a counter incremented in the
original teamStats loop's all-RpG-null branch but never read again
anywhere, is similarly not carried into evaluate_game_team_stats()'s
return value -- omitting it changes no observable output.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.atomic_json import write_json_atomic

ET = timezone(timedelta(hours=-4))


def _today():
    return datetime.now(ET).strftime('%Y-%m-%d')


def safe_side(g, side):
    """Return side dict safely - never raises even if side is None."""
    v = g.get(side)
    return v if isinstance(v, dict) else {}


def game_id(g):
    """Pure: 'AWAY@HOME' identifier string for logging, built the same way everywhere."""
    away_abbr = safe_side(g, 'away').get('abbr', '?')
    home_abbr = safe_side(g, 'home').get('abbr', '?')
    return f"{away_abbr}@{home_abbr}"


def load_inputs(path='data/slate.json'):
    """
    I/O adapter: read and json-decode `path`. Raises FileNotFoundError if
    missing, json.JSONDecodeError if malformed -- main() handles the
    missing-file case explicitly with its own message before ever calling
    this; a malformed-JSON error is allowed to propagate uncaught (matches
    legacy: the original script's own `json.load(f)` call was likewise
    unguarded).
    """
    with open(path) as f:
        return json.load(f)


def find_stale_slate_issue(slate, requested_date):
    """
    Pure: the slate-level and per-game stale-date checks (legacy
    checkpoints 2-5 -- checkpoint 1, the missing-file case, is handled by
    main() before load_inputs() is even reached). Returns None if every
    check passes, otherwise a dict describing the FIRST issue found, in
    the same check order and first-match-wins semantics the legacy
    top-level code used (empty games, then missing date field, then
    slate-date mismatch, then per-game startTime/gameTime in game-list
    order -- stopping at the first game whose derived ET date does not
    match `requested_date`, exactly as the legacy immediate-exit loop
    did). Performs no I/O, no network call, reads no environment
    variable, and uses no wall clock (ET is a fixed UTC-4 offset, not
    "now").
    """
    games = slate.get('games', [])
    if not games:
        return {'actual': 'no-games', 'reason': 'slate.json has no games',
                 'source': 'data/slate.json', 'log_suffix': '',
                 'gate_fail_prefix': 'GATE FAIL: data/slate.json has no games'}

    slate_date = slate.get('date', '')
    if not slate_date:
        return {'actual': 'missing-date-field', 'reason': 'slate.json has no date field',
                 'source': 'data/slate.json', 'log_suffix': '', 'gate_fail_prefix': None}

    if slate_date != requested_date:
        return {
            'actual': slate_date,
            'reason': f"Fetched slate date {slate_date!r} did not match requested date {requested_date!r}",
            'source': 'data/slate.json',
            'log_suffix': ' - Vercel API returned wrong-date slate',
            'gate_fail_prefix': None,
        }

    for g in games:
        start_time = g.get('startTime') or g.get('gameTime')
        if not start_time:
            continue
        try:
            st = start_time
            if st.endswith('Z'):
                st = st[:-1] + '+00:00'
            dt = datetime.fromisoformat(st)
            dt_et = dt.astimezone(ET)
            game_date_et = dt_et.strftime('%Y-%m-%d')
        except Exception:
            continue  # Unparseable startTime - skip, matches legacy tolerance
        if game_date_et != requested_date:
            gid = game_id(g)
            return {
                'actual': game_date_et,
                'reason': f"Game {gid} startTime maps to {game_date_et}, not {requested_date}",
                'source': f'data/slate.json[{gid}].startTime',
                'log_suffix': '',
                'gate_fail_prefix': None,
            }
    return None


def evaluate_game_pitcher_savant(g):
    """
    Pure: given ONE game dict (never mutated), return this game's
    pitcherSavant-phase gate result. Mirrors the legacy per-game
    pitcherSavant-loop body exactly, including message text:

      {
        'gid': str,
        'warnings': [str, ...],
        'errors': [str, ...],
        'quarantine_reason': str or None,
        'dual_null_fip': bool,   # both sides fully missing xFIP+seasonFIP
        'tbd_starters': int,
      }

    Performs no I/O, no network call, reads no environment variable, uses
    no wall clock.
    """
    away_side = safe_side(g, 'away')
    home_side = safe_side(g, 'home')
    gid = game_id(g)

    warnings = []
    errors = []
    tbd_starters = 0
    sides_with_null_fip = []

    for side_label, side_data in [('away', away_side), ('home', home_side)]:
        ps = side_data.get('pitcherSavant')

        if ps is None:
            pitcher = side_data.get('pitcher')
            pitcher_name = pitcher.get('name', '') if isinstance(pitcher, dict) else ''
            if pitcher_name:
                warnings.append(f"{gid}/{side_label}: pitcherSavant=null for {pitcher_name} "
                     f"- Savant data not available (new pitcher or not in leaderboard)")
            else:
                warnings.append(f"{gid}/{side_label}: pitcherSavant=null, starter TBD "
                     f"- game will use league-average xFIP fallback")
            tbd_starters += 1
            continue

        if not isinstance(ps, dict):
            errors.append(f"{gid}/{side_label}: pitcherSavant is not a dict (type={type(ps).__name__})")
            continue

        xfip       = ps.get('xFIP')
        season_fip = ps.get('seasonFIP')
        if xfip is None and season_fip is None:
            sides_with_null_fip.append(side_label)
        elif xfip is None:
            warnings.append(f"{gid}/{side_label}: xFIP=null, fallback to seasonFIP={season_fip}")

        rfip = ps.get('recentFIP')
        if rfip is not None and rfip < 0:
            warnings.append(f"{gid}/{side_label}: recentFIP={rfip} is negative "
                 f"(startsSampled={ps.get('startsSampled')}) - "
                 f"should have been cleared by fetch_savant_pitchers.py v5.1")

    away_ps = away_side.get('pitcherSavant') or {}
    home_ps = home_side.get('pitcherSavant') or {}
    away_has_xfip = isinstance(away_ps, dict) and (
        away_ps.get('xFIP') is not None or away_ps.get('seasonFIP') is not None)
    home_has_xfip = isinstance(home_ps, dict) and (
        home_ps.get('xFIP') is not None or home_ps.get('seasonFIP') is not None)

    dual_null_fip = False
    quarantine_reason = None

    if not away_has_xfip and not home_has_xfip:
        dual_null_fip = True
        errors.append(f"{gid}: BOTH starters have no xFIP/seasonFIP - "
             f"game projection completely impossible")
    elif len(sides_with_null_fip) == 1:
        bad_side = sides_with_null_fip[0]
        quarantine_reason = (
            f"ABNORMAL_GAME_STATUS_MISSING_PITCHER_DATA: {bad_side} pitcher has "
            f"pitcherSavant dict but xFIP=null AND seasonFIP=null - "
            f"likely resumed/suspended game with TBD/new starter. "
            f"Game excluded from real-money evaluation."
        )

    return {
        'gid': gid,
        'warnings': warnings,
        'errors': errors,
        'quarantine_reason': quarantine_reason,
        'dual_null_fip': dual_null_fip,
        'tbd_starters': tbd_starters,
    }


def evaluate_game_team_stats(g):
    """
    Pure: given ONE game dict (never mutated), return this game's
    teamStats-phase gate result, mirroring the legacy per-game
    teamStats-loop body exactly:

      {'warnings': [str, ...], 'errors': [str, ...], 'lineup_not_confirmed': int}

    Performs no I/O, no network call, reads no environment variable, uses
    no wall clock.
    """
    gid = game_id(g)
    warnings = []
    errors = []
    lineup_not_confirmed = 0

    for side_key in ('awayTeamStats', 'homeTeamStats'):
        ts = g.get(side_key)
        if not ts:
            warnings.append(f"{gid}/{side_key}: teamStats block missing - "
                 f"team may not be in teamstats.json (expansion team?) "
                 f"or enrich_data.py hasn't run yet")
            continue

        lc = ts.get('lineupConfirmed')
        if lc is None:
            warnings.append(f"{gid}/{side_key}: lineupConfirmed=null - "
                 f"lineups not yet posted (expected, safe to continue)")
            lineup_not_confirmed += 1

        l7  = ts.get('last7RpG')
        l15 = ts.get('last15RpG')
        szn = ts.get('runsPerGame') or ts.get('seasonRpG')
        if l7 is None and l15 is None and szn is None:
            errors.append(f"{gid}/{side_key}: last7RpG, last15RpG, AND runsPerGame all null - "
                 f"offense baseline computation impossible")
        elif l7 is None and l15 is None:
            warnings.append(f"{gid}/{side_key}: rolling R/G null, using season ({szn}) only")

    return {'warnings': warnings, 'errors': errors, 'lineup_not_confirmed': lineup_not_confirmed}


def apply_post_fetch_gate_immutable(slate):
    """
    Pure per-slate transform: given the already-stale-date-validated
    slate dict, return (new_slate, result).

    new_slate is a NEW object -- never `slate` itself, and no game dict
    inside new_slate['games'] is the same object as the corresponding
    game in slate['games'] -- with excludedFromSlate/exclusionReason
    applied in-place ON THE COPIES for newly-quarantined games this run.
    Games already excludedFromSlate=True on input are copied through
    unchanged and skipped by both scan passes below (the legacy
    one-way-quarantine-latch: once quarantined, a game is never
    re-evaluated, re-quarantined, or un-quarantined by a later run).

    result aggregates errors/warnings/quarantined_games/counts across
    BOTH scan passes, in the SAME two-pass order the legacy script used:
    ALL pitcherSavant-phase findings across all games (in game order),
    THEN ALL teamStats-phase findings across all games (in game order)
    -- never interleaved per-game. This ordering is externally
    observable (fetch_status.json's FAILED_GATE reason joins
    errors[:3]; stdout's WARNINGS/GATE FAILED lists print in this order)
    so it is preserved exactly, not merely made "equivalent."

    Does not mutate `slate` or any game dict inside it.
    """
    games = slate.get('games', [])
    new_games = [dict(g) for g in games]

    errors = []
    warnings = []
    quarantined_games = []
    null_xfip_games = 0
    tbd_starters = 0

    # Pass A: pitcherSavant checks + quarantine decisions (legacy section 2)
    for g in new_games:
        if g.get('excludedFromSlate'):
            continue
        r = evaluate_game_pitcher_savant(g)
        warnings.extend(r['warnings'])
        errors.extend(r['errors'])
        tbd_starters += r['tbd_starters']
        if r['dual_null_fip']:
            null_xfip_games += 1
        if r['quarantine_reason']:
            g['excludedFromSlate'] = True
            g['exclusionReason'] = r['quarantine_reason']
            quarantined_games.append({'game': r['gid'], 'reason': r['quarantine_reason']})

    if null_xfip_games > len(games) * 0.5:
        errors.append(f"{null_xfip_games}/{len(games)} games with dual null xFIP - "
             f"fetch_savant_pitchers.py likely failed entirely")

    # Pass B: teamStats checks (legacy section 3) -- games quarantined in
    # Pass A above are already excludedFromSlate on `new_games`, so they
    # are skipped here too, exactly as legacy skipped them in the same run.
    lineup_not_confirmed = 0
    for g in new_games:
        if g.get('excludedFromSlate'):
            continue
        r = evaluate_game_team_stats(g)
        warnings.extend(r['warnings'])
        errors.extend(r['errors'])
        lineup_not_confirmed += r['lineup_not_confirmed']

    new_slate = dict(slate)
    new_slate['games'] = new_games

    result = {
        'errors': errors,
        'warnings': warnings,
        'quarantined_games': quarantined_games,
        'tbd_starters': tbd_starters,
        'lineup_not_confirmed': lineup_not_confirmed,
        'null_xfip_games': null_xfip_games,
    }
    return new_slate, result


def write_fetch_status(status, requested_date, actual_date, quarantined_games, reason=None,
                        path='data/fetch_status.json'):
    """Write `path` (default data/fetch_status.json) with the current gate result."""
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if status == "OK":
        payload = {
            "status": "OK",
            "requestedDate": requested_date,
            "actualDate": actual_date,
            "fetchedAt": now_utc,
            "source": "fetch-slate/post_fetch_gate",
            "quarantinedGames": quarantined_games,
        }
    else:
        payload = {
            "status": status,
            "requestedDate": requested_date,
            "actualDate": actual_date,
            "failedAt": now_utc,
            "source": "fetch-slate/post_fetch_gate",
            "reason": reason or "Gate check failed",
            "quarantinedGames": quarantined_games,
        }
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def main():
    requested_date = sys.argv[1] if len(sys.argv) > 1 else _today()
    slate_path = 'data/slate.json'

    # ── 1. slate.json baseline ────────────────────────────────────────────
    if not os.path.exists(slate_path):
        print("GATE FAIL: data/slate.json not found", file=sys.stderr)
        print(f"STALE SLATE ABORT: requested={requested_date} actual=missing source=data/slate.json",
              file=sys.stderr)
        write_fetch_status("FAILED_STALE_DATE", requested_date, "missing", [],
                           "slate.json not found")
        sys.exit(1)

    slate = load_inputs(slate_path)

    # ── 1b/1c. STALE DATE GUARD (slate-level + per-game startTime) ────────
    issue = find_stale_slate_issue(slate, requested_date)
    if issue is not None:
        if issue['gate_fail_prefix']:
            print(issue['gate_fail_prefix'], file=sys.stderr)
        print(
            f"STALE SLATE ABORT: requested={requested_date} actual={issue['actual']} "
            f"source={issue['source']}{issue['log_suffix']}",
            file=sys.stderr
        )
        write_fetch_status("FAILED_STALE_DATE", requested_date, issue['actual'], [], issue['reason'])
        sys.exit(1)

    games = slate.get('games', [])
    slate_date = slate.get('date', '')
    # This literal still says "v2.1", not "v2.2" -- deliberate, not an
    # oversight. It is observable stdout text, and this phase's mandate is
    # byte-identical production output; the module docstring's version
    # header is documentation, not part of what a caller/log-scraper reads.
    print(f'post_fetch_gate v2.1: {len(games)} games loaded from slate.json '
          f'(date: {slate_date}) - requested: {requested_date}')

    # ── 2+3. pitcherSavant + teamstats/lineup checks (pure transform) ─────
    new_slate, result = apply_post_fetch_gate_immutable(slate)

    # Legacy quarantine_game() printed these two lines immediately, in game
    # order, as each game was quarantined during the scan. The pure
    # transform above cannot print (it performs no I/O), so main() prints
    # them here instead, in the same game order the quarantine_games list
    # was built in -- before the TBD/quarantined-count summaries below,
    # exactly as they preceded those summaries in the legacy single-pass loop.
    for q in result['quarantined_games']:
        print(f"  [QUARANTINE] {q['game']}: {q['reason']}")
        print(f"  [QUARANTINE] All markets for {q['game']} will be excluded from real-money output.")

    if result['tbd_starters'] > 0:
        print(f"  TBD/null pitcherSavant: {result['tbd_starters']} starters "
              f"(will use league-average xFIP=4.50 fallback in projections)")

    if result['quarantined_games']:
        print(f"  Quarantined games: {len(result['quarantined_games'])} "
              f"({', '.join(q['game'] for q in result['quarantined_games'])})")

    if result['lineup_not_confirmed'] > 0:
        print(f"  Lineups not yet confirmed: {result['lineup_not_confirmed']} teams "
              f"(expected before ~1pm ET)")

    # ── 4. Write quarantine markers back to slate.json ─────────────────────
    # If any games were quarantined, persist their excludedFromSlate flag to
    # slate.json so downstream steps (build_market_ledger, risk_gate) see
    # the exclusion. Unconditional on `result['errors']` -- see the
    # module-docstring's "real finding preserved exactly" note. Phase 6
    # Part 5: this write is now atomic (shared lib/atomic_json helper,
    # also used by fetch_lineups.py/fetch_savant_pitchers.py) instead of
    # the legacy plain open()+json.dump(), which could leave a truncated
    # file on a mid-serialization failure -- output content is
    # byte-for-byte unchanged, only the write mechanism is hardened.
    if result['quarantined_games']:
        write_json_atomic(new_slate, slate_path)
        print(f"  Quarantine markers written to {slate_path}")

    # ── 5. Output ────────────────────────────────────────────────────────
    print()
    if result['warnings']:
        print(f"WARNINGS ({len(result['warnings'])}):")
        for w in result['warnings']:
            print(f"  [WARN]  {w}")
        print()

    if result['errors']:
        print(f"GATE FAILED - {len(result['errors'])} hard error(s):", file=sys.stderr)
        for e in result['errors']:
            print(f"  [FAIL] {e}", file=sys.stderr)
        print("\nThese are pipeline failures, not data timing issues.", file=sys.stderr)
        write_fetch_status(
            "FAILED_GATE",
            requested_date,
            slate_date,
            result['quarantined_games'],
            f"{len(result['errors'])} hard gate error(s): " + "; ".join(result['errors'][:3])
        )
        sys.exit(1)

    active_games = len(games) - len(result['quarantined_games'])
    write_fetch_status("OK", requested_date, slate_date, result['quarantined_games'])
    print(f"GATE PASSED - {active_games} active games, "
          f"{len(result['quarantined_games'])} quarantined, "
          f"{len(result['warnings'])} warnings, date={slate_date} OK")
    sys.exit(0)


if __name__ == '__main__':
    main()
