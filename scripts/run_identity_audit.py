#!/usr/bin/env python3
"""
scripts/run_identity_audit.py
Workflow helper: run identity audit and write data/identity_audit.json.
Called by clv-update.yml Step 3.
No model logic — infrastructure wrapper only.
"""
import sys, json, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import audit_bet_identity

os.makedirs('data', exist_ok=True)
results, summary = audit_bet_identity.run_audit('bets.json')
print(json.dumps(summary, indent=2))

with open('data/identity_audit.json', 'w') as f:
    json.dump({
        'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'summary': summary,
        'bets': results,
    }, f, indent=2)
