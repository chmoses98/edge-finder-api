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
  1 = hard failure (slate missing, wrong date, no games — abort)
  2 = soft failure (starters/pinnacle missing — too early, retry later)
      Exit 2 written to $GITHUB_OUTPUT as pre_validation_status=not_ready
      The calling workflow uses this to skip to commit-snapshot-only path.
"""

import json, os, sys
from datetime import datetime, timezone, timedelta


def load_slate():
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'slate.json')
    if not os.path.exists(path):
        print('PRE-VALIDATION HARD FAIL: data/slate.json not found', file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f'PRE-VALIDATION HARD FAIL: slate.json is not valid JSON: {e}', file=sys.stderr)
            sys.exit(1)


def expected_date():
    """Return expected slate date from CLI arg or today ET."""
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
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


def main():
    exp_date = expected_date()
    slate = load_slate()
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
        sys.exit(1)

    if soft_errors:
        print(f'\nPRE-VALIDATION NOT READY — {len(soft_errors)} soft issue(s):')
        for e in soft_errors:
            print(f'  ⏳ {e}')
        write_github_output('pre_validation_status', 'not_ready')
        write_github_output('pre_validation_date', slate_date)
        # Exit 2 = soft fail: caller should archive Kalshi but skip full pipeline
        sys.exit(2)

    print(f'\nPRE-VALIDATION PASSED — {len(games)} games, starters confirmed')
    write_github_output('pre_validation_status', 'ok')
    write_github_output('pre_validation_date', slate_date)
    sys.exit(0)


if __name__ == '__main__':
    main()
