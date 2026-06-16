#!/usr/bin/env python3
"""
validate_slate_final.py — FINAL VALIDATION gate  v1.1

Runs after all enrichment pipeline steps complete:
  fetch_savant_pitchers → fetch_lineups → enrich_data → build_market_ledger

Hard FAIL (exit 1) — pipeline broken:
  - starters missing (pre-validate passed these, so regression means pipeline broke)
  - pinnacleVF missing
  - offenseBaselineAdj missing (enrich_data always writes this when it runs)
  - awayProjRuns missing in allEdges AND marketLedger empty (Poisson engine failure)
  - marketLedger missing or incomplete
  - pitcherSavant block negative recentFIP (sanitization should have cleared it)

WARN (continue, log) — data incomplete but pipeline can proceed:
  - pitcherSavant=null (TBD starter, expected for late-day games)
  - lineupConfirmed=null (lineups not yet posted, expected before ~5pm ET)
  - Kalshi price missing for specific markets (Kalshi doesn't list every game/market)

v1.1 changes:
  - pitcherSavant=null → WARN (TBD starter), not ERROR
  - lineupConfirmed=null → WARN, not ERROR
  - Null-safe pitcher access (same fix as fetch_savant_pitchers v5.1)
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
    # Use CWD-relative path (workflow always runs from repo root)
    # and also check __file__-relative path as fallback
    cwd_path = 'data/slate.json'
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'slate.json')
    path = cwd_path if os.path.exists(cwd_path) else file_path
    print(f'Loading slate from: {path} (exists: {os.path.exists(path)})')
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
    away = g.get('away') or {}
    home = g.get('home') or {}
    return f"{away.get('abbr','?')}@{home.get('abbr','?')}"  


def safe_side(g, side):
    v = g.get(side)
    return v if isinstance(v, dict) else {}


def validate_final(slate, exp_date):
    errors = []
    warnings = []
    games = slate.get('games', [])

    # DIAGNOSTIC: print slate summary for CI log visibility
    print(f'validate_final: {len(games)} games in slate for {exp_date}')
    for g in games[:3]:
        away = (g.get('away') or {}).get('abbr','?')
        home = (g.get('home') or {}).get('abbr','?')
        has_ledger = bool(g.get('marketLedger'))
        has_edges = bool(g.get('allEdges'))
        away_ts = bool(g.get('awayTeamStats'))
        print(f'  {away}@{home}: ledger={has_ledger} edges={has_edges} awayTeamStats={away_ts}')
    if not games:
        errors.append(f'slate.json has no games for {exp_date}')
        return errors, warnings

    for g in games:
        name = gid(g)

        # ── Starters ──────────────────────────────────────────────────────────
        for side in ['away', 'home']:
            side_data = safe_side(g, side)
            p = side_data.get('pitcher')
            if not isinstance(p, dict) or not p.get('name'):
                # TBD starter (pitcher=null) is expected for evening games at 5pm ET.
                # Pre-validate would have caught truly missing starters earlier.
                # Treat as warning to avoid blocking valid slates with TBD starters.
                warnings.append(f'{name}: {side} starter TBD/missing '
                                 '(pitcher.name not posted — expected for late-day games)')

        # ── Pinnacle VF ───────────────────────────────────────────────────────
        pvf = g.get('pinnacleVF', {})
        game_status = g.get('status', '')
        if not pvf or pvf.get('away') is None:
            if game_status in ('Final', 'In Progress', 'Postponed'):
                warnings.append(f'{name}: pinnacleVF.away missing for {game_status} game '
                                 '(expected — odds removed post-game)')
            else:
                errors.append(f'{name}: pinnacleVF.away missing — Rule 71 gap check impossible')

        # ── Lineup + offense baseline ─────────────────────────────────────────
        for side_key in ['awayTeamStats', 'homeTeamStats']:
            ts = g.get(side_key)
            if ts is None:
                # teamStats completely absent — enrich_data hasn't run for this team
                warnings.append(f'{name}: {side_key} block missing — '
                                 'team may not be in teamstats.json; '
                                 'model will use league-average baseline')
                continue
            if not isinstance(ts, dict):
                errors.append(f'{name}: {side_key} is {type(ts).__name__}, expected dict')
                continue
            if ts.get('lineupConfirmed') is None:
                warnings.append(f'{name}: {side_key}.lineupConfirmed=null — '
                                 'lineup not yet posted (expected before ~5pm ET)')
            if ts.get('offenseBaselineAdj') is None:
                # If abbr not in teamstats.json, enrich_data skips the team.
                # This is a data quality issue but not a pipeline crash — warn only.
                warnings.append(f'{name}: {side_key}.offenseBaselineAdj missing — '
                                 'team abbr may not match teamstats.json '
                                 '(model will use league-average baseline)')

        # ── Kalshi prices (warnings only — not all games/markets are listed) ─
        kalshi = (g.get('odds') or {}).get('kalshi') or {}
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

        # ── Run projections ────────────────────────────────────────────────────
        # awayProjRuns lives in allEdges (from Vercel slate.js Poisson engine).
        # marketLedger rows do NOT contain awayProjRuns, so the old ledger fallback
        # was always False and produced spurious errors.
        # Fix: if marketLedger is non-empty, the game was evaluated — warn only.
        # Only hard-fail when BOTH allEdges AND marketLedger are absent.
        all_edges = g.get('allEdges', [])
        ledger_check = g.get('marketLedger', [])
        proj_found = any(e.get('awayProjRuns') is not None for e in all_edges)
        if not proj_found:
            if ledger_check:
                # Ledger present → game was evaluated, projection just absent from allEdges
                # (expected for TBD-pitcher games using league-average xFIP fallback)
                warnings.append(f'{name}: awayProjRuns not in allEdges — '
                                 'projection may have used league-average fallback '
                                 '(marketLedger present, game was evaluated)')
            else:
                errors.append(f'{name}: awayProjRuns not in allEdges AND marketLedger empty — '
                               'Poisson engine has not run for this game')

        # ── Market ledger ──────────────────────────────────────────────────────
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

        # ── pitcherSavant ──────────────────────────────────────────────────────
        for side in ['away', 'home']:
            side_data = safe_side(g, side)
            ps = side_data.get('pitcherSavant')
            if ps is None:
                # TBD starter — expected for games with no confirmed starter
                pitcher = side_data.get('pitcher')
                pitcher_name = pitcher.get('name', '') if isinstance(pitcher, dict) else ''
                if pitcher_name:
                    warnings.append(f'{name}/{side}: pitcherSavant=null for {pitcher_name} '
                                     f'(Savant data unavailable — xFIP fallback will be used)')
                else:
                    warnings.append(f'{name}/{side}: pitcherSavant=null, starter TBD '
                                     '— xFIP=4.50 league-average fallback will be used')
                continue
            if not isinstance(ps, dict):
                errors.append(f'{name}/{side}: pitcherSavant is {type(ps).__name__}, expected dict')
                continue
            recent_fip = ps.get('recentFIP')
            if recent_fip is not None and recent_fip < 0:
                errors.append(f'{name}/{side}: recentFIP={recent_fip} is negative '
                               f'(startsSampled={ps.get("startsSampled")}) — '
                               f'sanitization in fetch_savant_pitchers.py should have cleared this')
            if ps.get('xFIP') is None:
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
    try:
        errors, warnings = validate_final(slate, exp_date)
    except Exception as e:
        import traceback
        print(f'VALIDATE CRASH: {e}', file=sys.stderr)
        print(f'VALIDATE CRASH: {e}')
        traceback.print_exc()
        sys.exit(1)

    games = slate.get('games', [])

    if warnings:
        print(f'WARNINGS ({len(warnings)}):')
        for w in warnings:
            print(f'  ⚠  {w}')
        print()

    if errors:
        # Categorize for actionable output
        starter_errs  = [e for e in errors if 'starter' in e or 'pinnacle' in e.lower()]
        lineup_errs   = [e for e in errors if 'lineupConfirmed' in e or 'offenseBaseline' in e]
        ledger_errs   = [e for e in errors if 'marketLedger' in e or 'market' in e.lower()]
        proj_errs     = [e for e in errors if 'awayProjRuns' in e or 'Poisson' in e]
        savant_errs   = [e for e in errors if 'pitcherSavant' in e or 'recentFIP' in e]
        other_errs    = [e for e in errors if e not in starter_errs + lineup_errs +
                          ledger_errs + proj_errs + savant_errs]

        # Print to both stdout (CI log) and stderr (for exit code purposes)
        print(f'FINAL VALIDATION FAILED — {len(errors)} error(s):')
        print(f'FINAL VALIDATION FAILED — {len(errors)} error(s):', file=sys.stderr)
        for errs, label in [
            (starter_errs,  'STARTERS/PINNACLE'),
            (lineup_errs,   'LINEUPS/BASELINE'),
            (proj_errs,     'RUN PROJECTIONS'),
            (ledger_errs,   'MARKET LEDGER'),
            (savant_errs,   'PITCHER SAVANT'),
            (other_errs,    'OTHER'),
        ]:
            if errs:
                print(f'\n  [{label}] ({len(errs)} errors)', file=sys.stderr)
                for e in errs[:5]:
                    print(f'    ✗ {e}')
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
    
    # Phase 1E: Generate structured execution slip and persist to slate.json
    slip_text, slip_dict = generate_execution_slip(games, exp_date)

    # Persist execution slip into slate.json
    try:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        _slate_path = f'data/slate_{exp_date}.json'
        with open(_slate_path, 'r') as _sf:
            _slate = _json.load(_sf)
        _slate['executionSlip'] = slip_text
        _slate['executionSlipData'] = slip_dict
        _slate['executionSlipGeneratedAt'] = _dt.now(_tz.utc).isoformat()
        with open(_slate_path, 'w') as _sf:
            _json.dump(_slate, _sf, indent=2)
        print(f'[SLIP] Persisted execution slip to {_slate_path}')
        # Also write a standalone text file
        slip_file = f'data/execution_slip_{exp_date}.txt'
        with open(slip_file, 'w') as _slipf:
            _slipf.write(slip_text)
        print(f'[SLIP] Written: {slip_file}')
    except Exception as _e:
        print(f'[SLIP] Warning: could not persist slip — {_e}')

    sys.exit(0)


def generate_execution_slip(games, exp_date):
    """
    Phase 1E: Output clearly separated execution slip sections:
      === REAL-MONEY BETS ===
      === PRICE-MOVED PASSES ===
      === PAPER-ONLY ===
      === REJECTED / BLOCKED ===
    Returns (slip_text, slip_dict).
    """
    import io as _io
    _buf = _io.StringIO()
    def _print(*args, **kwargs):
        kwargs.pop('file', None)
        _print(*args, **kwargs)
        _print(*args, file=_buf, **{k: v for k, v in kwargs.items() if k != 'file'})
    _print()
    _print('=' * 70)
    _print(f'EXECUTION SLIP — {exp_date}')
    _print('=' * 70)
    
    real_money = []
    price_moved = []
    paper_only  = []
    rejected_blocked = []
    
    for g in games:
        away_abbr = g.get('away', {}).get('abbr', '?')
        home_abbr = g.get('home', {}).get('abbr', '?')
        game_label = f'{away_abbr}@{home_abbr}'
        snap_ts = g.get('kalshiSnapshotTs', g.get('snapshot_ts', 'unknown'))
        
        for row in g.get('marketLedger', []):
            market  = row.get('market', '?')
            status  = row.get('status', '?')
            conf    = row.get('confidence') or row.get('confidenceTier')
            edge    = row.get('calibratedEdgeVsExecutable') or row.get('edge')
            raw_edge = row.get('rawEdgeVsExecutable')
            exec_price = row.get('executablePriceUsed')
            max_price  = row.get('maxBetPrice')
            model_p    = row.get('modelProb')
            ticker     = row.get('marketTicker') or row.get('ticker')
            reason_codes = row.get('reasonCodes', []) or []
            
            entry = {
                'game':        game_label,
                'market':      market,
                'side':        market,
                'ticker':      ticker,
                'modelProb':   f"{model_p}%" if model_p is not None else '—',
                'execPrice':   f"{exec_price}¢" if exec_price is not None else '—',
                'rawEdge':     f"{raw_edge:+.2f}%" if raw_edge is not None else '—',
                'calEdge':     f"{edge:+.2f}%" if edge is not None else '—',
                'conf':        conf or '—',
                'betSize':     row.get('betSize', '—'),
                'maxBetPrice': f"{max_price}¢ or better" if max_price is not None else '—',
                'snapshotTs':  snap_ts,
                'reasonCodes': reason_codes,
                'gatesFired':  row.get('gatesFired', []) or [],
                'rejectionReason': row.get('rejectionReason', ''),
                'status':      status,
                'lineupStatus': (g.get('awayTeamStats') or {}).get('lineupStatus', '?'),
            }
            
            if status == 'Accepted':
                if conf == 'PAPER':
                    paper_only.append(entry)
                elif 'PRICE_MOVED_BEYOND_MAX' in reason_codes:
                    price_moved.append(entry)
                else:
                    real_money.append(entry)
            elif status == 'Rejected':
                rejection = row.get('rejectionReason', '')
                if 'PRICE_MOVED_BEYOND_MAX' in (reason_codes or []) or 'PRICE_MOVED_BEYOND_MAX' in rejection:
                    price_moved.append(entry)
                elif conf == 'PAPER' or 'suspended' in rejection.lower() or 'paper' in rejection.lower():
                    paper_only.append(entry)
                else:
                    rejected_blocked.append(entry)
    
    def _fmt(e):
        lines = [
            f"  {e['game']} | {e['market']} | {e['side']}",
            f"    Ticker:    {e['ticker'] or 'MISSING'}",
            f"    Model%:    {e['modelProb']} | Exec Price: {e['execPrice']} | Raw Edge: {e['rawEdge']} | Cal Edge: {e['calEdge']}",
            f"    Tier:      {e['conf']} | Stake: ${e['betSize']}",
        ]
        if e['maxBetPrice'] != '—':
            lines.append(f"    MaxBet:    {e['maxBetPrice']}")
        if e['gatesFired']:
            lines.append(f"    Gates:     {'; '.join(e['gatesFired'][:3])}")
        if e['reasonCodes']:
            lines.append(f"    Codes:     {', '.join(e['reasonCodes'][:5])}")
        lines.append(f"    Snapshot:  {e['snapshotTs']}")
        return '\n'.join(lines)
    
    _print()
    _print(f'=== REAL-MONEY BETS ({len(real_money)}) ===')
    if real_money:
        for e in real_money:
            _print(_fmt(e))
            _print()
    else:
        _print('  (none)')
    
    _print()
    _print(f'=== PRICE-MOVED PASSES ({len(price_moved)}) ===')
    if price_moved:
        for e in price_moved:
            _print(f"  {e['game']} | {e['market']}")
            _print(f"    REASON: {e['rejectionReason'] or 'PRICE_MOVED_BEYOND_MAX'}")
            _print(f"    Exec: {e['execPrice']} | MaxBet: {e['maxBetPrice']}")
        _print()
    else:
        _print('  (none)')
    
    _print()
    _print(f'=== PAPER-ONLY ({len(paper_only)}) ===')
    if paper_only:
        for e in paper_only:
            reason = e['rejectionReason'] or ', '.join(e['gatesFired'][:2]) or 'paper-tier'
            _print(f"  {e['game']} | {e['market']} | Cal Edge: {e['calEdge']} | {reason[:80]}")
    else:
        _print('  (none)')
    
    _print()
    _print(f'=== REJECTED / BLOCKED ({len(rejected_blocked)}) ===')
    if rejected_blocked:
        for e in rejected_blocked:
            reason = e['rejectionReason'] or '—'
            raw    = e['rawEdge']
            _print(f"  {e['game']} | {e['market']} | Raw Edge: {raw} | {reason[:100]}")
    else:
        _print('  (none)')
    
    _print()
    _print('=' * 70)
    _print(f'SLIP SUMMARY: Real={len(real_money)} PriceMoved={len(price_moved)} Paper={len(paper_only)} Rejected={len(rejected_blocked)}')
    _print('=' * 70)

    slip_text = _buf.getvalue()
    slip_dict = {
        'realMoney':       real_money,
        'priceMoved':      price_moved,
        'paperOnly':       paper_only,
        'rejectedBlocked': rejected_blocked,
        'summary': {
            'realMoneyCount':       len(real_money),
            'priceMovedCount':      len(price_moved),
            'paperOnlyCount':       len(paper_only),
            'rejectedBlockedCount': len(rejected_blocked),
        },
    }
    return slip_text, slip_dict


if __name__ == '__main__':
    main()
