#!/usr/bin/env python3
"""
tests/edgelab/test_no_automatic_wagering.py
================================================
Grep-verified absence guardrails (same pattern as
tests/test_risk_gate_rule71_81_bankroll_absence.py): the entire
Canonical Placed-Bet Ledger milestone -- lib/edgelab/bets.py,
lib/edgelab/bankroll.py, lib/edgelab/query.py, and every
scripts/edgelab/*bet* / *bankroll* script -- must never place a real
order, size a stake automatically, or touch production recommendation
logic. If a future change ever adds any of that, these tests fail
loudly rather than letting it drift in silently.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEDGER_FILES = [
    os.path.join(ROOT, "lib", "edgelab", "bets.py"),
    os.path.join(ROOT, "lib", "edgelab", "bankroll.py"),
    os.path.join(ROOT, "lib", "edgelab", "query.py"),
    os.path.join(ROOT, "scripts", "edgelab", "log_bet.py"),
    os.path.join(ROOT, "scripts", "edgelab", "record_bet_from_workflow.py"),
    os.path.join(ROOT, "scripts", "edgelab", "record_bankroll_transaction.py"),
    os.path.join(ROOT, "scripts", "edgelab", "query_bets.py"),
    os.path.join(ROOT, "scripts", "edgelab", "generate_postmortem.py"),
    os.path.join(ROOT, "scripts", "edgelab", "reconcile_bet_history.py"),
    os.path.join(ROOT, "scripts", "edgelab", "cancel_bet.py"),
]

FORBIDDEN_PATTERNS = [
    r"place_order", r"createorder", r"submit_order", r"kalshi\.post",
    r"requests\.(post|put)\(", r"kelly", r"auto[_-]?stake", r"auto[_-]?bet",
]


def test_ledger_files_exist():
    for path in LEDGER_FILES:
        assert os.path.exists(path), path


def test_no_order_placement_or_auto_staking_language_in_ledger_code():
    for path in LEDGER_FILES:
        with open(path) as f:
            source = f.read()
        for pattern in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, source, re.IGNORECASE), f"{path}: unexpected match for {pattern!r}"


def test_record_placed_bet_workflow_has_no_network_calls():
    """The GitHub Actions form's backing script must be pure local file I/O -- no HTTP client of any kind."""
    path = os.path.join(ROOT, "scripts", "edgelab", "record_bet_from_workflow.py")
    with open(path) as f:
        source = f.read()
    for forbidden in ("import requests", "import httpx", "urllib.request", "http.client"):
        assert forbidden not in source


def test_edgelab_bet_ledger_never_imports_kalshi_client():
    """No bet-ledger module reaches for a Kalshi API client module -- it only ever records what the user reports."""
    for path in LEDGER_FILES:
        with open(path) as f:
            source = f.read()
        assert "import kalshi" not in source.lower()
        assert "from lib.kalshi" not in source.lower()


def test_no_edgelab_workflow_calls_a_kalshi_order_endpoint():
    workflows_dir = os.path.join(ROOT, ".github", "workflows")
    for path in glob.glob(os.path.join(workflows_dir, "*.yml")):
        with open(path) as f:
            text = f.read().lower()
        for forbidden in ("/portfolio/orders", "place_order", "createorder"):
            assert forbidden not in text, f"{path}: unexpected order-placement reference"


def test_recommendations_module_is_untouched_read_path_for_bets():
    """
    lib.edgelab.bets only ever READS recommendation ids to link them
    (link_bets_to_recommendations) -- it must never import or call
    anything from the production recommendation-building pipeline that
    could mutate a Recommendation's own decision fields.
    """
    path = os.path.join(ROOT, "lib", "edgelab", "bets.py")
    with open(path) as f:
        source = f.read()
    assert "from lib.edgelab.recommendations import" not in source
    assert "from lib.edgelab import recommendations" not in source
