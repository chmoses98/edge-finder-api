#!/usr/bin/env python3
"""
scripts/regression_test.py v1.0
=================================
Asserts ledger completeness after build_market_ledger.py.
Fails the workflow (exit 1) if any assertion fails.

Assertions:
  A1. Every game has a marketLedger
  A2. Every game has exactly 11 market rows (one per REQUIRED_MARKETS)
  A3. No market row has status not in {Accepted, Rejected, Missing Data, Evaluation Failed}
  A4. Every Rejected row has a non-empty rejectionReason
  A5. Every Missing Data row has a non-empty missingFields list
  A6. Every Evaluation Failed row has a non-empty evaluationError
  A7. Every Accepted row has: edge (not null), confidence in {HIGH,MEDIUM,PAPER}, kalshiPrice (not null)
  A8. Total ledger rows = games * 11 (no silent omissions)
  A9. No game has 0 rows (complete market silence is a pipeline failure)
"""

import json, sys, os

REQUIRED_MARKETS = [
    'NRFI', 'YRFI',
    'F5_ML_Away', 'F5_ML_Home',
    'TT_Away_Over', 'TT_Home_Over',
    'ML_Away', 'ML_Home',
    'Game_Total', 'RL_Away', 'RL_Home',
]
VALID_STATUSES   = {'Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed'}
VALID_CONFIDENCE = {'HIGH', 'MEDIUM', 'PAPER'}

slate_path = 'data/slate.json'
if not os.path.exists(slate_path):
    print("REGRESSION FAIL: data/slate.json not found", file=sys.stderr)
    sys.exit(1)

with open(slate_path) as f:
    slate = json.load(f)

games  = slate.get('games', [])
fails  = []
totals = {'Accepted': 0, 'Rejected': 0, 'Missing Data': 0, 'Evaluation Failed': 0}

for g in games:
    away = g.get('away', {}).get('abbr', '?')
    home  = g.get('home', {}).get('abbr', '?')
    gid   = f"{away}@{home}"

    ledger = g.get('marketLedger')

    # A1
    if ledger is None:
        fails.append(f"A1 {gid}: marketLedger key missing from game block")
        continue
    if not isinstance(ledger, list):
        fails.append(f"A1 {gid}: marketLedger is not a list (got {type(ledger).__name__})")
        continue

    # A9
    if len(ledger) == 0:
        fails.append(f"A9 {gid}: marketLedger is empty list — complete market silence")
        continue

    ledger_markets = [r.get('market') for r in ledger]

    # A2: all required markets present
    for req in REQUIRED_MARKETS:
        if req not in ledger_markets:
            fails.append(f"A2 {gid}: required market '{req}' absent from ledger "
                         f"(present: {sorted(set(ledger_markets))})")

    # A2: exactly 11 rows (no duplicates, no extras beyond required)
    if len(ledger) != len(REQUIRED_MARKETS):
        fails.append(f"A2 {gid}: expected {len(REQUIRED_MARKETS)} rows, got {len(ledger)} "
                     f"(markets: {ledger_markets})")

    for row in ledger:
        mkt    = row.get('market', 'UNKNOWN')
        status = row.get('status')

        # A3
        if status not in VALID_STATUSES:
            fails.append(f"A3 {gid}/{mkt}: invalid status '{status}'")
            continue

        totals[status] += 1

        # A4
        if status == 'Rejected':
            reason = row.get('rejectionReason')
            if not reason or not str(reason).strip():
                fails.append(f"A4 {gid}/{mkt}: status=Rejected but rejectionReason is empty/null")

        # A5
        if status == 'Missing Data':
            mf = row.get('missingFields')
            if not mf or not isinstance(mf, list) or len(mf) == 0:
                fails.append(f"A5 {gid}/{mkt}: status=Missing Data but missingFields is empty/null")

        # A6
        if status == 'Evaluation Failed':
            ee = row.get('evaluationError')
            if not ee or not str(ee).strip():
                fails.append(f"A6 {gid}/{mkt}: status=Evaluation Failed but evaluationError is empty/null")

        # A7
        if status == 'Accepted':
            if row.get('edge') is None:
                fails.append(f"A7 {gid}/{mkt}: status=Accepted but edge is null")
            if row.get('confidence') not in VALID_CONFIDENCE:
                fails.append(f"A7 {gid}/{mkt}: status=Accepted but confidence='{row.get('confidence')}'")
            if row.get('kalshiPrice') is None:
                fails.append(f"A7 {gid}/{mkt}: status=Accepted but kalshiPrice is null")

# A8: total row count
expected_total = len(games) * len(REQUIRED_MARKETS)
actual_total   = sum(totals.values())
if actual_total != expected_total:
    fails.append(f"A8: total rows={actual_total}, expected={expected_total} "
                 f"({len(games)} games × {len(REQUIRED_MARKETS)} markets)")

# ── Output ─────────────────────────────────────────────────────────────────────
print(f"Regression test: {len(games)} games × {len(REQUIRED_MARKETS)} markets = {expected_total} expected rows")
print(f"  Actual rows: {actual_total}")
for status, count in totals.items():
    print(f"  {status}: {count}")

if fails:
    print(f"\nREGRESSION FAILED — {len(fails)} assertion(s):", file=sys.stderr)
    for f in fails:
        print(f"  ✗ {f}", file=sys.stderr)
    sys.exit(1)

print(f"\nALL ASSERTIONS PASSED ({len(REQUIRED_MARKETS)} markets × {len(games)} games verified)")
sys.exit(0)
