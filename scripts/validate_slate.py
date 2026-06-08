#!/usr/bin/env python3
"""
validate_slate.py v2.0 — Pre-analysis validation gate
Uses the ACTUAL slate.json field schema as written by merge_odds.py and enrich_data.py.

Field paths (correct):
  Starters:      g['away']['pitcher']['name'], g['home']['pitcher']['name']
  Run projections: g['allEdges'][n]['awayProjRuns'] (NOT at top level)
  Kalshi ML:     g['odds']['kalshi']['ml']['away']
  Kalshi F5:     g['odds']['kalshi']['f5ml']['away']
  Kalshi TT:     g['odds']['kalshi']['team_totals']['away']['best_ticker']
  Kalshi NRFI:   g['odds']['kalshi']['nrfi_yrfi']['nrfi_american']
  Kalshi Total:  g['odds']['kalshi']['total']['line']
  Kalshi RL:     g['odds']['kalshi']['rl']['best_ticker']
  Pinnacle VF:   g['pinnacleVF']['away']
  Lineup:        g['awayTeamStats']['lineupConfirmed']
  Baseline:      g['awayTeamStats']['offenseBaselineAdj']
  Market ledger: g['marketLedger'] — list of dicts, one per required market

Market ledger (written by build_market_ledger.py, run after merge_odds):
  Each entry: { market, status, kalshiPrice, kalshiImplied, modelProb, edge,
                confidence, rejectionReason, missingFields, evaluationError }
  status must be one of: Accepted | Rejected | Missing Data | Evaluation Failed

Exit 0 = OK. Exit 1 = fail with specifics.
"""

import json, sys, os

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
        print('FAIL: data/slate.json not found', file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def gid(g):
    return f"{g.get('away',{}).get('abbr','?')}@{g.get('home',{}).get('abbr','?')}"


def validate(slate):
    errors = []
    warnings = []
    games = slate.get('games', [])

    if not games:
        errors.append('slate.json has no games array')
        return errors, warnings

    for g in games:
        name = gid(g)

        # ── Starters (correct path: g['away']['pitcher']['name']) ──────────
        away_pitcher = g.get('away', {}).get('pitcher', {})
        home_pitcher = g.get('home', {}).get('pitcher', {})
        if not away_pitcher.get('name'):
            errors.append(f'{name}: away starter missing at away.pitcher.name')
        if not home_pitcher.get('name'):
            errors.append(f'{name}: home starter missing at home.pitcher.name')

        # ── Pinnacle VF (correct path: g['pinnacleVF']['away']) ────────────
        pvf = g.get('pinnacleVF', {})
        if not pvf or pvf.get('away') is None:
            errors.append(f'{name}: pinnacleVF.away missing — Rule 71 gap check impossible')

        # ── Lineup flags (correct path: g['awayTeamStats']['lineupConfirmed']) ─
        for side_key, side_label in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
            ts = g.get(side_key, {})
            if not ts:
                errors.append(f'{name}: {side_key} block entirely missing')
                continue
            if ts.get('lineupConfirmed') is None:
                errors.append(f'{name}: {side_key}.lineupConfirmed missing')
            if ts.get('offenseBaselineAdj') is None:
                errors.append(f'{name}: {side_key}.offenseBaselineAdj missing — '
                              f'run enrich_data.py before analysis')

        # ── Kalshi market prices (correct paths per merge_odds.py) ─────────
        kalshi = g.get('odds', {}).get('kalshi', {})

        checks = [
            ('ML',         kalshi.get('ml', {}).get('away'),              'odds.kalshi.ml.away'),
            ('F5 ML',      kalshi.get('f5ml', {}).get('away'),            'odds.kalshi.f5ml.away'),
            ('TT Away',    kalshi.get('team_totals', {}).get('away', {}).get('best_ticker'),
                           'odds.kalshi.team_totals.away.best_ticker'),
            ('TT Home',    kalshi.get('team_totals', {}).get('home', {}).get('best_ticker'),
                           'odds.kalshi.team_totals.home.best_ticker'),
            ('NRFI/YRFI',  kalshi.get('nrfi_yrfi', {}).get('nrfi_american'), 'odds.kalshi.nrfi_yrfi.nrfi_american'),
            ('Game Total', kalshi.get('total', {}).get('line'),           'odds.kalshi.total.line'),
            ('RL',         kalshi.get('rl', {}).get('best_ticker'),       'odds.kalshi.rl.best_ticker'),
        ]
        for market_name, value, path in checks:
            if value is None:
                warnings.append(f'{name}: Kalshi {market_name} not in slate ({path}=null) — '
                                 f'market must show Missing Data status in marketLedger')

        # ── Run projections (correct path: allEdges[n]['awayProjRuns']) ────
        all_edges = g.get('allEdges', [])
        proj_found = any(e.get('awayProjRuns') is not None for e in all_edges)
        if not proj_found:
            # Also check marketLedger
            ledger = g.get('marketLedger', [])
            proj_in_ledger = any(e.get('awayProjRuns') is not None for e in ledger)
            if not proj_in_ledger:
                errors.append(f'{name}: awayProjRuns not found in allEdges or marketLedger — '
                               f'Poisson engine has not run for this game')

        # ── Market ledger completeness ─────────────────────────────────────
        ledger = g.get('marketLedger', [])

        if not ledger:
            errors.append(f'{name}: marketLedger is empty or missing — '
                           f'run build_market_ledger.py. All {len(REQUIRED_MARKETS)} required '
                           f'markets must have a row.')
            continue  # no point checking rows if ledger is empty

        ledger_markets = {row.get('market') for row in ledger}
        for req in REQUIRED_MARKETS:
            if req not in ledger_markets:
                errors.append(f'{name}: required market "{req}" absent from marketLedger '
                               f'(present: {sorted(ledger_markets)})')

        for row in ledger:
            market = row.get('market', 'UNKNOWN')
            status = row.get('status')

            if status not in VALID_STATUSES:
                errors.append(f'{name}/{market}: invalid status "{status}" — '
                               f'must be one of {sorted(VALID_STATUSES)}')
                continue

            if status == 'Rejected' and not row.get('rejectionReason'):
                errors.append(f'{name}/{market}: status=Rejected but rejectionReason is empty')

            if status == 'Missing Data' and not row.get('missingFields'):
                errors.append(f'{name}/{market}: status=Missing Data but missingFields is empty')

            if status == 'Evaluation Failed' and not row.get('evaluationError'):
                errors.append(f'{name}/{market}: status=Evaluation Failed but evaluationError is empty')

            if status == 'Accepted':
                if row.get('edge') is None:
                    errors.append(f'{name}/{market}: status=Accepted but edge is null')
                if row.get('confidence') not in ('HIGH', 'MEDIUM', 'PAPER'):
                    errors.append(f'{name}/{market}: status=Accepted but confidence="{row.get("confidence")}"')
                if row.get('kalshiPrice') is None:
                    errors.append(f'{name}/{market}: status=Accepted but kalshiPrice is null')

        # ── recentFIP sanity check ─────────────────────────────────────────
        for side in ['away', 'home']:
            ps = g.get(side, {}).get('pitcherSavant', {})
            recent_fip = ps.get('recentFIP')
            if recent_fip is not None and recent_fip < 0:
                errors.append(f'{name}/{side}: recentFIP={recent_fip} is negative — '
                               f'computation error (startsSampled={ps.get("startsSampled")}). '
                               f'true_xFIP regression will produce invalid result.')

        # ── pitcherSavant data present ─────────────────────────────────────
        for side in ['away', 'home']:
            ps = g.get(side, {}).get('pitcherSavant', {})
            if not ps:
                errors.append(f'{name}/{side}: pitcherSavant block missing — '
                               f'fetch_savant_pitchers.py may have failed silently '
                               f'(continue-on-error:true in fetch-slate.yml)')
            elif ps.get('xFIP') is None:
                warnings.append(f'{name}/{side}: pitcherSavant.xFIP=null — '
                                 f'true_xFIP regression will use fallback')

    return errors, warnings


def main():
    slate = load_slate()
    errors, warnings = validate(slate)

    if warnings:
        print(f'WARNINGS ({len(warnings)}):')
        for w in warnings:
            print(f'  ⚠  {w}')
        print()

    if errors:
        print(f'VALIDATION FAILED — {len(errors)} error(s):', file=sys.stderr)
        for e in errors:
            print(f'  ✗ {e}', file=sys.stderr)
        sys.exit(1)
    else:
        games = slate.get('games', [])
        ledger_counts = [len(g.get('marketLedger', [])) for g in games]
        total_rows = sum(ledger_counts)
        accepted = sum(
            1 for g in games
            for row in g.get('marketLedger', [])
            if row.get('status') == 'Accepted'
        )
        print(f'VALIDATION PASSED — {len(games)} games | '
              f'{total_rows} market ledger rows | {accepted} Accepted')
        sys.exit(0)


if __name__ == '__main__':
    main()
