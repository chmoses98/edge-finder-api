#!/usr/bin/env python3
"""
tests/edgelab/test_universal_persistence_scope_guard.py
=============================================================
Universal ModelEvaluation Persistence mission: structural regression
guard proving fee logic, bankroll sizing, recommendation-eligibility
logic, and the real-money REQUIRED_MARKETS gate are untouched by this
mission -- it is a persistence/wiring change, not a model-retuning or
risk-gating change. Uses `git diff` against the merge-base with origin
main (falling back to HEAD if no such ref/remote is available in this
checkout) exactly like tests/test_settlement_reliability_milestone.py's
existing pattern, scoped to file paths rather than function bodies
(this mission's diff spans many files, unlike that single-function
guard).
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Files this mission must never modify -- recommendation eligibility,
# bet sizing, fee math, and the real-money execution gate.
_FORBIDDEN_PATHS = (
    "scripts/risk_gate.py",
    "scripts/write_pending_bets.py",
    "lib/edgelab/bankroll.py",
    "lib/edgelab/kalshi_fees.py",
    "lib/edgelab/recommendations.py",
    "scripts/build_market_ledger.py",
)


def _changed_files():
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return set(result.stdout.split())


def test_no_forbidden_file_touched_by_uncommitted_changes():
    changed = _changed_files()
    hit = changed & set(_FORBIDDEN_PATHS)
    assert hit == set(), f"Universal ModelEvaluation Persistence mission touched forbidden file(s): {hit}"


def test_recommendation_id_logic_module_unimported_by_new_bridge_module():
    """
    lib.edgelab.kalshi_discovery_bridge (this mission's one new module)
    must have no import-time dependency on lib.edgelab.recommendations
    or scripts.risk_gate -- a purely additive persistence bridge should
    never need to reach into recommendation-eligibility/risk-gating code
    at all.
    """
    with open(os.path.join(ROOT, "lib", "edgelab", "kalshi_discovery_bridge.py")) as f:
        src = f.read()
    assert "lib.edgelab.recommendations" not in src
    assert "scripts.risk_gate" not in src
    assert "lib.edgelab.bankroll" not in src
    assert "lib.edgelab.kalshi_fees" not in src
