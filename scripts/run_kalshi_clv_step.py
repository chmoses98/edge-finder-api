#!/usr/bin/env python3
"""
scripts/run_kalshi_clv_step.py
Workflow helper: run Kalshi historical CLV (fetch_kalshi_clv_v2.py) for a given date.
Called by clv-update.yml Step 2. Takes DATE as argv[1] or DATE env var.
No model logic — infrastructure wrapper only.
"""
import sys, json, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import fetch_kalshi_clv_v2 as clv

date = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DATE', '')
if not date:
    print('ERROR: DATE argument required (argv[1] or DATE env var)')
    sys.exit(1)

with open('bets.json') as f:
    bets = json.load(f)

targets = [b for b in bets
           if b.get('date') == date
           and b.get('marketTicker')
           and b.get('clv') is None]

print(f'Kalshi CLV targets for {date}: {len(targets)}')
if not targets:
    print('No targets -- either no tickers or CLV already set.')
    sys.exit(0)

ids = [b['id'] for b in targets]
results, summary = clv.run_clv(bets_path='bets.json', write=True, bet_ids=ids)
print('Kalshi CLV complete:', json.dumps(summary))
