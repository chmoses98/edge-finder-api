#!/usr/bin/env python3
"""
tests/edgelab/test_import_postmortem_script.py
==================================================
Structured Postmortem Ingestion milestone: end-to-end coverage for
scripts/edgelab/import_postmortem.py.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import_pm_script = _load_script("import_postmortem.py")

PM_DIR = os.path.join("data", "edgelab", "postmortems", "2026-08-03")


def _seed_real_bet(tmp_path):
    record = build_manual_bet_record(
        "KXMLBF5-TEST-SF", "SF F5 moneyline", 12.0, 0.55, "2026-08-03T22:00:00Z",
        game_date="2026-08-03", entry_method="MANUAL_CHAT_CONFIRMED",
    )
    receipt = write_placed_bet(record)
    return receipt["betId"]


def test_import_postmortem_links_only_real_bets_and_regenerates_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bet_id = _seed_real_bet(tmp_path)
    findings = {
        "betIds": [bet_id, "doesNotExist"],
        "analyticalWins": [{"note": "correctly faded the public"}],
        "processErrors": [], "proposedInvestigations": [],
    }
    monkeypatch.setattr(sys, "argv", [
        "import_postmortem.py", "--date", "2026-08-03",
        "--markdown-text", "# Postmortem\nGood day.\n",
        "--findings-json-inline", json.dumps(findings),
        "--skip-report-regeneration",
    ])
    exit_code = import_pm_script.main()
    assert exit_code == 0

    with open(os.path.join(PM_DIR, "postmortem.json")) as f:
        record = json.load(f)
    assert record["linkedBetIds"] == [bet_id]
    assert record["unresolvedBetReferences"][0]["reference"] == "doesNotExist"

    with open(os.path.join(PM_DIR, "bet_linkage.json")) as f:
        linkage = json.load(f)
    assert linkage["linkedBets"][0]["betId"] == bet_id

    with open(os.path.join(PM_DIR, "import_receipts.json")) as f:
        receipts = json.load(f)
    assert len(receipts) == 1
    assert receipts[0]["success"] is True


def test_reimport_identical_payload_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bet_id = _seed_real_bet(tmp_path)
    findings = {"betIds": [bet_id]}
    argv = [
        "import_postmortem.py", "--date", "2026-08-03",
        "--markdown-text", "# Postmortem\n",
        "--findings-json-inline", json.dumps(findings),
        "--skip-report-regeneration",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    import_pm_script.main()
    monkeypatch.setattr(sys, "argv", argv)
    exit_code = import_pm_script.main()
    assert exit_code == 0

    with open(os.path.join(PM_DIR, "postmortem.json")) as f:
        record = json.load(f)
    assert record["revision"] == 1
    assert not os.path.exists(os.path.join(PM_DIR, "revisions.jsonl"))


def test_correction_creates_new_revision_and_preserves_old(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bet_id = _seed_real_bet(tmp_path)
    base_argv = [
        "import_postmortem.py", "--date", "2026-08-03",
        "--markdown-text", "# v1\n", "--skip-report-regeneration",
    ]
    monkeypatch.setattr(sys, "argv", base_argv + ["--findings-json-inline", json.dumps({"betIds": [bet_id], "analyticalWins": [{"note": "v1"}]})])
    import_pm_script.main()

    monkeypatch.setattr(sys, "argv", [
        "import_postmortem.py", "--date", "2026-08-03", "--markdown-text", "# v2\n", "--skip-report-regeneration",
        "--findings-json-inline", json.dumps({"betIds": [bet_id], "analyticalWins": [{"note": "v2 corrected"}]}),
    ])
    exit_code = import_pm_script.main()
    assert exit_code == 0

    with open(os.path.join(PM_DIR, "postmortem.json")) as f:
        record = json.load(f)
    assert record["revision"] == 2
    revisions = list(storage.read_records(os.path.join(PM_DIR, "revisions.jsonl")))
    assert len(revisions) == 1
    assert revisions[0]["analyticalWins"] == [{"note": "v1"}]

    with open(os.path.join(PM_DIR, "import_receipts.json")) as f:
        receipts = json.load(f)
    assert len(receipts) == 2
    assert receipts[1]["duplicateStatus"] == "CORRECTED"


def test_missing_markdown_and_findings_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "import_postmortem.py", "--date", "2026-08-03", "--findings-json-inline", "{}",
    ])
    exit_code = import_pm_script.main()
    assert exit_code == 1
    assert not os.path.exists(os.path.join(PM_DIR, "postmortem.json"))


def test_realistic_multiline_markdown_and_findings_with_shell_metacharacters(tmp_path, monkeypatch):
    """
    The corrected "Import Daily Postmortem" workflow routes
    markdown_text/findings_json through `env:` and reads them from the
    environment, never interpolating them directly into shell source
    (see tests/edgelab/test_import_workflows_structure.py's
    test_no_raw_input_interpolation_inside_run_blocks) -- passing the raw
    strings straight into argv (as this test does via sys.argv, exactly
    matching how `--markdown-text "$MARKDOWN_TEXT"` hands the environment
    variable's value to the process) is the safe path. This proves the
    importer round-trips a real multi-paragraph Markdown postmortem and
    a JSON findings payload -- both containing double quotes, apostrophes,
    `$(...)`/backticks, parentheses, and embedded newlines -- byte-for-byte,
    with no shell ever involved.
    """
    monkeypatch.chdir(tmp_path)
    bet_id = _seed_real_bet(tmp_path)
    nasty_markdown = (
        "# Postmortem — 2026-08-03\n\n"
        'Overall a "disciplined" day. Faded the public on SF F5 -- don\'t $(rm -rf /) or `echo pwned`.\n\n'
        "## Wins\n- Correctly identified overreaction (see \"sharp money\" note)\n\n"
        "## Notes\nLine with (parentheses), a trailing backslash\\, and a $VAR-looking reference.\n"
    )
    nasty_findings = {
        "betIds": [bet_id],
        "analyticalWins": [{"note": 'Faded the public because of "sharp money"; don\'t chase $(losses).'}],
        "processErrors": [{"note": "Logged late -- (should automate this) next time.\nSecond line."}],
    }
    monkeypatch.setattr(sys, "argv", [
        "import_postmortem.py", "--date", "2026-08-03",
        "--markdown-text", nasty_markdown,
        "--findings-json-inline", json.dumps(nasty_findings),
        "--skip-report-regeneration",
    ])
    exit_code = import_pm_script.main()
    assert exit_code == 0

    with open(os.path.join(PM_DIR, "postmortem.md")) as f:
        assert f.read() == nasty_markdown
    with open(os.path.join(PM_DIR, "postmortem.json")) as f:
        record = json.load(f)
    assert record["analyticalWins"] == nasty_findings["analyticalWins"]
    assert record["processErrors"] == nasty_findings["processErrors"]


def test_never_writes_bets_ledger_directly():
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "import_postmortem.py")) as f:
        source = f.read()
    assert "write_placed_bet" not in source
    assert "storage.append_records(storage.singleton_path(\"bets\"" not in source
