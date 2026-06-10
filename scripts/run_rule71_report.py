#!/usr/bin/env python3
"""
scripts/run_rule71_report.py
Workflow helper: generate Rule 71 tracking report and write data/rule71_report.json.
Called by clv-update.yml Step 4.
No model logic — infrastructure wrapper only.
"""
import sys, json, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import rule71_tracker as r71

os.makedirs('data', exist_ok=True)
report = r71.generate_rule71_report('bets.json')
r71.print_rule71_report(report)

with open('data/rule71_report.json', 'w') as f:
    json.dump(report, f, indent=2)

print('Rule 71 report written.')
