#!/usr/bin/env python3
"""
validate_slate_pre.py — PRE-VALIDATION gate

Runs immediately after the Vercel slate fetch, BEFORE Kalshi archiving.
Only checks fields that are populated by the Vercel API itself:
  - slate.json exists and has games
  - slate date matches the expected date (stale data guard)
  - game structure is parseable (away/home abbr present)
  - starters posted (away.pitcher.name, home.pitcher.name)

Does NOT check (these require later pipeline steps):
  - pinnacleVF.away       (populated by merge_odds.py — checked post-merge)
  - lineupConfirmed       (fetch_lineups.py)
  - offenseBaselineAdj    (enrich_data.py)
  - odds.kalshi.*         (merge_odds.py)
  - allEdges/awayProjRuns (merge_odds.py)
  - marketLedger          (build_market_ledger.py)
  - pitcherSavant         (fetch_savant_pitchers.py)

Exit codes:
  0 = passed (pipeline may continue, Kalshi archive may proceed)
  1 = hard failure (slate missing, wrong date, no games on a day the MLB
      schedule says has games, schedule unknown -- abort)
  2 = soft failure (starters not posted yet). NOT a pipeline failure: the
      workflow continues with a warning, exactly as it has in practice
      (TBD starters use the league-average fallback downstream).
      Written to $GITHUB_OUTPUT as pre_validation_status=not_ready.
  3 = VERIFIED OFF-DAY. The slate is empty AND correctly dated AND the MLB
      schedule (regular season + every postseason round) says no playable
      games -- the off-day contract in lib/edgelab/slate_day_contract.py,
      applied through scripts/post_fetch_gate.py's resolve_empty_slate().
      data/fetch_status.json records NO_GAMES_SCHEDULED with the schedule
      evidence. This is a success state (nothing to fetch), never inferred
      from the empty file alone: an unreachable/malformed schedule is
      SCHEDULE_UNKNOWN and stays exit 1.
"""

import json, os, sys
from datetime import datetime, timezone, timedelta

EXIT_OK = 0
EXIT_HARD_FAIL = 1
EXIT_NOT_READY = 2
EXIT_NO_GAMES_SCHEDULED = 3

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SLATE_PATH = os.path.join(_SCRIPTS_DIR, '..', 'data', 'slate.json')
DEFAULT_FETCH_STATUS_PATH = os.path.join(_SCRIPTS_DIR, '..', 'data', 'fetch_status.json')


def load_slate(path=DEFAULT_SLATE_PATH):
    if not os.path.exists(path):
        print('PRE-VALIDATION HARD FAIL: data/slate.json not found', file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f'PRE-VALIDATION HARD FAIL: slate.json is not valid JSON: {e}', file=sys.stderr)
            sys.exit(1)


def expected_date(argv=None):
    """Return expected slate date from CLI arg or today ET."""
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0]:
        return argv[0]
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')


def validate_pre(slate, exp_date):
    hard_errors = []   # abort-worthy: wrong date, empty file
    soft_errors = []   # not-ready: starters not posted yet
    warnings = []

    # ── Date guard: reject stale data ────────────────────────────────────────
    slate_date = slate.get('date', '')
    if not slate_date:
        hard_errors.append('slate.json missing "date" field — cannot verify freshness')
    elif slate_date != exp_date:
        hard_errors.append(
            f'STALE DATA: slate.json date={slate_date!r} but expected {exp_date!r}. '
            f'Vercel API returned wrong date. Aborting — will NOT archive a stale snapshot.'
        )

    # ── Games present ─────────────────────────────────────────────────────────
    games = slate.get('games', [])
    if not games:
        hard_errors.append(f'slate.json has no games array for date {exp_date}')
        return hard_errors, soft_errors, warnings

    # ── Per-game checks ───────────────────────────────────────────────────────
    starter_missing = 0

    for g in games:
        away_abbr = g.get('away', {}).get('abbr', '?')
        home_abbr = g.get('home', {}).get('abbr', '?')
        name = f'{away_abbr}@{home_abbr}'

        # Starters — soft failure (not posted yet early in day)
        # Use 'or {}' to safely handle pitcher=null (TBD starters)
        away_pitcher = (g.get('away') or {}).get('pitcher') or {}
        home_pitcher = (g.get('home') or {}).get('pitcher') or {}
        if not away_pitcher.get('name'):
            soft_errors.append(f'{name}: away starter not posted (away.pitcher.name)')
            starter_missing += 1
        if not home_pitcher.get('name'):
            soft_errors.append(f'{name}: home starter not posted (home.pitcher.name)')
            starter_missing += 1

        # NOTE: pinnacleVF is NOT checked here.
        # pinnacleVF is populated by merge_odds.py (Odds API step), which runs AFTER
        # pre-validation. Checking it here would cause false "not ready" failures
        # on every run. Post-merge check in merge_odds.py emits DATA-HEALTH warnings.

    if starter_missing > 0:
        soft_errors.insert(0,
            f'Slate not ready: {starter_missing} starters missing. '
            f'Re-run after ~3pm ET when lineups post.'
        )

    return hard_errors, soft_errors, warnings


def write_github_output(key, value):
    """Write to $GITHUB_OUTPUT if running in CI."""
    gho = os.environ.get('GITHUB_OUTPUT', '')
    if gho:
        with open(gho, 'a') as f:
            f.write(f'{key}={value}\n')


def is_empty_slate_for(slate, exp_date):
    """Pure: True only for a well-formed, correctly dated slate whose games
    list is present and empty -- the one shape the off-day contract can
    classify. A missing games key, a wrong date or a non-list stays on the
    ordinary hard-fail path (exit 1)."""
    return (isinstance(slate, dict)
            and isinstance(slate.get('games'), list)
            and not slate['games']
            and slate.get('date') == exp_date)


def resolve_empty_slate_day(slate, exp_date, fetch_status_path=DEFAULT_FETCH_STATUS_PATH,
                            schedule_loader=None, now_iso=None):
    """
    Adapter: decide what an empty slate means using the EXISTING off-day
    contract (post_fetch_gate.resolve_empty_slate -> slate_day_contract),
    and record it in data/fetch_status.json exactly the way the post-fetch
    gate does. Returns (fetch_status, reason, day_verdict).

    schedule_loader defaults to post_fetch_gate.load_schedule_response,
    which honours the EDGEFINDER_SCHEDULE_EVIDENCE test/replay seam.
    """
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    import post_fetch_gate as gate

    loader = schedule_loader or gate.load_schedule_response
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    status, reason, evidence, day = gate.resolve_empty_slate(
        slate, exp_date, loader(exp_date), now_iso)
    if not (status == 'NO_GAMES_SCHEDULED'
            and _already_recorded_off_day(fetch_status_path, exp_date)):
        # An off-day already recorded for this date is left byte-identical,
        # so the 2nd/3rd scheduled attempt of an off-day commits nothing.
        gate.write_fetch_status(status, exp_date, 'no-games', [], reason,
                                path=fetch_status_path, schedule_evidence=evidence)
    return status, reason, day['verdict']


def _already_recorded_off_day(fetch_status_path, exp_date):
    try:
        with open(fetch_status_path) as f:
            existing = json.load(f)
    except (OSError, ValueError):
        return False
    return (isinstance(existing, dict)
            and existing.get('status') == 'NO_GAMES_SCHEDULED'
            and existing.get('requestedDate') == exp_date
            and (existing.get('scheduleEvidence') or {}).get('status') == 'NO_GAMES_SCHEDULED')


def main(argv=None, slate_path=DEFAULT_SLATE_PATH,
         fetch_status_path=DEFAULT_FETCH_STATUS_PATH, schedule_loader=None):
    exp_date = expected_date(argv)
    slate = load_slate(slate_path)

    # Off-day contract: an empty, correctly dated slate is an off-day ONLY
    # when MLB schedule evidence says so. Any other verdict (schedule
    # unknown, games scheduled but none collected) falls through to the
    # unchanged hard-fail path below.
    if is_empty_slate_for(slate, exp_date):
        status, reason, verdict = resolve_empty_slate_day(
            slate, exp_date, fetch_status_path, schedule_loader)
        write_github_output('slate_day_verdict', verdict)
        if status == 'NO_GAMES_SCHEDULED':
            print(f'PRE-VALIDATION for {exp_date}')
            print(f'  slate.json date: {slate.get("date")}')
            print(f'  games found:     0')
            print(f'\nNO GAMES SCHEDULED for {exp_date} -- {reason}')
            print('Verified off-day (MLB schedule evidence). Nothing to fetch.')
            write_github_output('pre_validation_status', 'no_games_scheduled')
            write_github_output('pre_validation_date', slate.get('date', ''))
            sys.exit(EXIT_NO_GAMES_SCHEDULED)
        print(f'Empty slate is NOT a verified off-day ({verdict}): {reason}', file=sys.stderr)

    hard_errors, soft_errors, warnings = validate_pre(slate, exp_date)

    slate_date = slate.get('date', 'unknown')
    games = slate.get('games', [])

    print(f'PRE-VALIDATION for {exp_date}')
    print(f'  slate.json date: {slate_date}')
    print(f'  games found:     {len(games)}')

    if warnings:
        for w in warnings:
            print(f'  ⚠  {w}')

    if hard_errors:
        print(f'\nPRE-VALIDATION HARD FAIL — {len(hard_errors)} critical error(s):',
              file=sys.stderr)
        for e in hard_errors:
            print(f'  ✗ {e}', file=sys.stderr)
        write_github_output('pre_validation_status', 'hard_fail')
        write_github_output('pre_validation_date', slate_date)
        sys.exit(EXIT_HARD_FAIL)

    if soft_errors:
        print(f'\nPRE-VALIDATION NOT READY — {len(soft_errors)} soft issue(s):')
        for e in soft_errors:
            print(f'  ⏳ {e}')
        write_github_output('pre_validation_status', 'not_ready')
        write_github_output('pre_validation_date', slate_date)
        # Exit 2 = not ready (TBD starters). The workflow proceeds with a
        # warning; downstream gates treat TBD starters as warnings.
        sys.exit(EXIT_NOT_READY)

    print(f'\nPRE-VALIDATION PASSED — {len(games)} games, starters confirmed')
    write_github_output('pre_validation_status', 'ok')
    write_github_output('pre_validation_date', slate_date)
    sys.exit(EXIT_OK)


if __name__ == '__main__':
    main()
