#!/usr/bin/env python3
"""
validate_slate_final.py — FINAL VALIDATION gate

Runs after all enrichment pipeline steps complete:
  fetch_savant_pitchers → fetch_lineups → enrich_data → build_market_ledger

Checks everything validate_slate.py v2.0 checked, organized into
clear error categories so the failure reason is immediately actionable.

Exit codes:
  0 = all checks passed
  1 = one or more errors found (pipeline output is not analysis-ready)

This is the gating step before Write meta and commit.
The Kalshi snapshot has already been archived before this runs —
a failure here does NOT un-archive the snapshot.
"""

import json, os, sys
from datetime import datetime, timezone, timedelta


REQUIRED_MARKETS = [
    'NRFI', 'YRFI', 'F5_ML_Away', 'F5_ML_Home',
    'TT_Away_Over', 'TT_Home_Over',
    'ML_Away', 'ML_Home',
    'Game_Total', 'RL_Away', 'RL_Home',
]
VALID_STATUSES = {'Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed'}


def load_slate():
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'slate.json')
    if not os.path.exists(path):
        print('FINAL VALIDATION FAIL: data/slate.json not found', file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def expected_date():
    if len(sys.argv) > 1 and sys.argv[1]:
        return sys.argv[1]
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    return et_now.strftime('%Y-%m-%d')


def gid(g):
    return f"{g.get('away',{}).get('abbr','?')}@{g.get('home',{}).get('abbr','?')}"


def validate_final(slate, exp_date):
    errors = []
    warnings = []
    games = slate.get('games', [])

    if not games:
        errors.append(f'slate.json has no games for {exp_date}')
        return errors, warnings

    for g in games:
        name = gid(g)

        # ── Starters ──────────────────────────────────────────────────────────
        for side in ['away', 'home']:
            p = g.get(side, {}).get('pitcher', {})
            if not p.get('name'):
                errors.append(f'{name}: {side} starter missing (away.pitcher.name)')

        # ── Pinnacle VF ───────────────────────────────────────────────────────
        pvf = g.get('pinnacleVF', {})
        if not pvf or pvf.get('away') is None:
            errors.append(f'{name}: pinnacleVF.away missing — Rule 71 gap check impossible')

        # ── Lineup + offense baseline (from fetch_lineups + enrich_data) ──────
        for side_key in ['awayTeamStats', 'homeTeamStats']:
            ts = g.get(side_key, {})
            if not ts:
                errors.append(f'{name}: {side_key} block entirely missing')
                continue
            if ts.get('lineupConfirmed') is None:
                errors.append(f'{name}: {side_key}.lineupConfirmed missing — '
                               'fetch_lineups.py may have failed')
            if ts.get('offenseBaselineAdj') is None:
                errors.append(f'{name}: {side_key}.offenseBaselineAdj missing — '
                               'enrich_data.py may have failed')

        # ── Kalshi prices (warnings only — market may not list every game) ────
        kalshi = g.get('odds', {}).get('kalshi', {})
        kalshi_checks = [
            ('ML',         kalshi.get('ml', {}).get('away'),                   'odds.kalshi.ml.away'),
            ('F5 ML',      kalshi.get('f5ml', {}).get('away'),                 'odds.kalshi.f5ml.away'),
            ('TT Away',    kalshi.get('team_totals', {}).get('away', {}).get('best_ticker'),
                           'odds.kalshi.team_totals.away.best_ticker'),
            ('TT Home',    kalshi.get('team_totals', {}).get('home', {}).get('best_ticker'),
                           'odds.kalshi.team_totals.home.best_ticker'),
            ('NRFI/YRFI',  kalshi.get('nrfi_yrfi', {}).get('nrfi_american'),   'odds.kalshi.nrfi_yrfi.nrfi_american'),
            ('Game Total', kalshi.get('total', {}).get('line'),                 'odds.kalshi.total.line'),
            ('RL',         kalshi.get('rl', {}).get('best_ticker'),             'odds.kalshi.rl.best_ticker'),
        ]
        for mkt_name, value, path in kalshi_checks:
            if value is None:
                warnings.append(f'{name}: Kalshi {mkt_name} not in slate ({path}=null) — '
                                 'must show Missing Data in marketLedger')

        # ── Run projections (from merge_odds + Poisson engine) ─────────────
        all_edges = g.get('allEdges', [])
        proj_found = any(e.get('awayProjRuns') is not None for e in all_edges)
        if not proj_found:
            ledger = g.get('marketLedger', [])
            proj_in_ledger = any(e.get('awayProjRuns') is not None for e in ledger)
            if not proj_in_ledger:
                errors.append(f'{name}: awayProjRuns not found in allEdges or marketLedger — '
                               'Poisson engine has not run')

        # ── Market ledger (from build_market_ledger.py) ────────────────────
        ledger = g.get('marketLedger', [])
        if not ledger:
            errors.append(f'{name}: marketLedger empty/missing — '
                           f'build_market_ledger.py must produce {len(REQUIRED_MARKETS)} rows')
            continue

        ledger_markets = {row.get('market') for row in ledger}
        for req in REQUIRED_MARKETS:
            if req not in ledger_markets:
                errors.append(f'{name}: required market "{req}" absent from marketLedger '
                               f'(present: {sorted(ledger_markets)})')

        for row in ledger:
            mkt = row.get('market', 'UNKNOWN')
            status = row.get('status')
            if status not in VALID_STATUSES:
                errors.append(f'{name}/{mkt}: invalid status "{status}"')
                continue
            if status == 'Rejected' and not row.get('rejectionReason'):
                errors.append(f'{name}/{mkt}: Rejected but rejectionReason empty')
            if status == 'Missing Data' and not row.get('missingFields'):
                errors.append(f'{name}/{mkt}: Missing Data but missingFields empty')
            if status == 'Evaluation Failed' and not row.get('evaluationError'):
                errors.append(f'{name}/{mkt}: Evaluation Failed but evaluationError empty')
            if status == 'Accepted':
                if row.get('edge') is None:
                    errors.append(f'{name}/{mkt}: Accepted but edge is null')
                if row.get('confidence') not in ('HIGH', 'MEDIUM', 'PAPER'):
                    errors.append(f'{name}/{mkt}: Accepted but confidence="{row.get("confidence")}"')
                if row.get('kalshiPrice') is None:
                    errors.append(f'{name}/{mkt}: Accepted but kalshiPrice is null')

        # ── pitcherSavant (from fetch_savant_pitchers.py) ─────────────────
        for side in ['away', 'home']:
            ps = g.get(side, {}).get('pitcherSavant', {})
            recent_fip = ps.get('recentFIP')
            if recent_fip is not None and recent_fip < 0:
                errors.append(f'{name}/{side}: recentFIP={recent_fip} is negative — '
                               f'computation error (startsSampled={ps.get("startsSampled")})')
            if not ps:
                errors.append(f'{name}/{side}: pitcherSavant block missing — '
                               'fetch_savant_pitchers.py may have failed silently')
            elif ps.get('xFIP') is None:
                warnings.append(f'{name}/{side}: pitcherSavant.xFIP=null — '
                                 'true_xFIP regression will use fallback')

    return errors, warnings


def write_github_output(key, value):
    gho = os.environ.get('GITHUB_OUTPUT', '')
    if gho:
        with open(gho, 'a') as f:
            f.write(f'{key}={value}\n')


def main():
    exp_date = expected_date()
    slate = load_slate()
    errors, warnings = validate_final(slate, exp_date)

    games = slate.get('games', [])

    if warnings:
        print(f'WARNINGS ({len(warnings)}):')
        for w in warnings:
            print(f'  ⚠  {w}')
        print()

    if errors:
        # Categorize errors for clear reporting
        starter_errs  = [e for e in errors if 'starter' in e or 'pinnacle' in e.lower()]
        lineup_errs   = [e for e in errors if 'lineupConfirmed' in e or 'offenseBaseline' in e]
        ledger_errs   = [e for e in errors if 'marketLedger' in e or 'market' in e.lower()]
        proj_errs     = [e for e in errors if 'awayProjRuns' in e or 'Poisson' in e]
        savant_errs   = [e for e in errors if 'pitcherSavant' in e or 'recentFIP' in e]
        other_errs    = [e for e in errors if e not in starter_errs + lineup_errs +
                          ledger_errs + proj_errs + savant_errs]

        print(f'FINAL VALIDATION FAILED — {len(errors)} error(s):', file=sys.stderr)
        for category, errs, label in [
            (starter_errs,  starter_errs,  'STARTERS/PINNACLE'),
            (lineup_errs,   lineup_errs,   'LINEUPS/BASELINE'),
            (proj_errs,     proj_errs,     'RUN PROJECTIONS'),
            (ledger_errs,   ledger_errs,   'MARKET LEDGER'),
            (savant_errs,   savant_errs,   'PITCHER SAVANT'),
            (other_errs,    other_errs,    'OTHER'),
        ]:
            if errs:
                print(f'\n  [{label}] ({len(errs)} errors)', file=sys.stderr)
                for e in errs[:5]:  # cap output per category
                    print(f'    ✗ {e}', file=sys.stderr)
                if len(errs) > 5:
                    print(f'    ... and {len(errs)-5} more', file=sys.stderr)

        write_github_output('final_validation_status', 'fail')
        write_github_output('final_validation_errors', str(len(errors)))
        sys.exit(1)

    ledger_counts = [len(g.get('marketLedger', [])) for g in games]
    accepted = sum(
        1 for g in games
        for row in g.get('marketLedger', [])
        if row.get('status') == 'Accepted'
    )
    print(f'FINAL VALIDATION PASSED — {len(games)} games | '
          f'{sum(ledger_counts)} market ledger rows | {accepted} Accepted')
    write_github_output('final_validation_status', 'ok')
    sys.exit(0)


if __name__ == '__main__':
    main()
