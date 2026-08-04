#!/usr/bin/env python3
"""
tests/edgelab/test_import_workflows_structure.py
====================================================
Structural content tests for the two new GitHub Actions workflows in the
MLB Market Research Corpus & Frictionless Manual Logging milestone:
.github/workflows/import-manual-bets.yml and import-postmortem.yml.
Follows this repository's established convention (see
tests/test_kalshi_price_check_workflow.py) of testing workflow files via
source-content assertions rather than executing them.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")
IMPORT_BETS_PATH = os.path.join(WORKFLOWS_DIR, "import-manual-bets.yml")
IMPORT_POSTMORTEM_PATH = os.path.join(WORKFLOWS_DIR, "import-postmortem.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def test_both_workflows_valid_yaml_and_manual_dispatch_only():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        doc = _load(path)
        assert doc is not None
        on = doc.get(True) or doc.get("on")
        assert "workflow_dispatch" in on
        assert "push" not in on
        assert "pull_request" not in on


def test_import_bets_workflow_calls_only_the_canonical_import_script():
    src = _read(IMPORT_BETS_PATH)
    assert "scripts/edgelab/import_bet_batch.py" in src
    for forbidden in ("build_market_ledger.py", "risk_gate.py", "protect_slate.py", "validate_slate_final.py"):
        assert forbidden not in src


def test_import_postmortem_workflow_calls_only_the_canonical_import_script():
    src = _read(IMPORT_POSTMORTEM_PATH)
    assert "scripts/edgelab/import_postmortem.py" in src
    for forbidden in ("build_market_ledger.py", "risk_gate.py", "protect_slate.py", "validate_slate_final.py"):
        assert forbidden not in src


def test_no_order_placement_language_in_either_workflow():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        text = _read(path).lower()
        for forbidden in ("/portfolio/orders", "place_order", "createorder", "kelly", "auto_stake", "auto_bet"):
            assert forbidden not in text


def test_import_bets_requires_exactly_one_payload_source():
    src = _read(IMPORT_BETS_PATH)
    assert "payload_json" in src and "payload_file" in src
    assert "Provide exactly one of payload_json or payload_file" in src
    assert "Provide only one of payload_json or payload_file" in src


def test_import_postmortem_requires_markdown_and_findings():
    src = _read(IMPORT_POSTMORTEM_PATH)
    assert "Provide markdown_text or markdown_file" in src
    assert "Provide findings_json or findings_file" in src


def test_import_postmortem_concurrency_group_is_scoped_per_date():
    """Two different dates' postmortem imports must not serialize behind each other unnecessarily."""
    doc = _load(IMPORT_POSTMORTEM_PATH)
    assert "${{ inputs.date }}" in doc["concurrency"]["group"]


def test_both_workflows_upload_a_receipt_or_artifact():
    for path in (IMPORT_BETS_PATH, IMPORT_POSTMORTEM_PATH):
        assert "upload-artifact" in _read(path)
