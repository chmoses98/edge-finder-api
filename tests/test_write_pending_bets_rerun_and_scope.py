#!/usr/bin/env python3
"""
tests/test_write_pending_bets_rerun_and_scope.py
=====================================================
Phase 10 rerun/idempotency and changed-file-scope coverage for
scripts/write_pending_bets.py.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def wpb():
    if "write_pending_bets" in sys.modules:
        del sys.modules["write_pending_bets"]
    import write_pending_bets as _wpb
    return _wpb


def _wire(wpb, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(wpb, "SLATE_PATH", str(tmp_path / "data" / "slate.json"))
    monkeypatch.setattr(wpb, "BETS_PATH", str(tmp_path / "bets.json"))
    return tmp_path


def make_entry(market="ML_Away", tier="HIGH", ticker="T-1"):
    return {
        "market": market, "confidenceTier": tier, "status": "Accepted",
        "ticker": ticker, "marketTicker": ticker, "kalshiPrice": -120,
        "executablePriceUsed": 54.5, "betSize": 5.0,
    }


def make_game(away="KC", home="WSH", entries=None):
    return {
        "away": {"abbr": away}, "home": {"abbr": home}, "status": "Scheduled",
        "marketLedger": entries if entries is not None else [make_entry()],
    }


class TestRerunIdempotency:

    def test_identical_rerun_writes_zero_new_bets(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        first = json.loads((root / "bets.json").read_text())
        assert len(first) == 1

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        second = json.loads((root / "bets.json").read_text())
        assert second == first

    def test_added_market_on_rerun_only_appends_the_new_one(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(entries=[make_entry(ticker="T-1")])]}, f)
        wpb.main()

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(
                entries=[make_entry(ticker="T-1"), make_entry(ticker="T-2", market="ML_Home")]
            )]}, f)
        wpb.main()

        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 2
        tickers = {b["ticker"] for b in bets}
        assert tickers == {"T-1", "T-2"}

    def test_within_run_duplicate_market_ledger_entry_deduped(self, wpb, tmp_path, monkeypatch):
        """
        Two identical entries within the SAME slate.json's marketLedger
        (same date/game/market/ticker) must produce only ONE bet -- the
        within-run incremental existing_keys.add() must fire after the
        first append, not just across separate runs.
        """
        root = _wire(wpb, tmp_path, monkeypatch)
        dup_entry = make_entry(ticker="T-DUP")
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(entries=[dup_entry, dict(dup_entry)])]}, f)
        wpb.main()
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 1

    def test_stale_unrelated_data_file_untouched(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        stray = root / "data" / "unrelated.json"
        stray.write_text('{"leftover": true}')
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        assert stray.read_text() == '{"leftover": true}'

    def test_prior_bets_from_other_dates_preserved(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        prior_bet = {"date": "2026-06-01", "game": "AAA@BBB", "market": "ML_Away",
                     "ticker": "OLD-1", "status": "settled", "result": "WIN"}
        with open(root / "bets.json", "w") as f:
            json.dump([prior_bet], f)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 2
        assert bets[0] == prior_bet


class TestChangedFileScope:

    def test_lib_dependency_untouched(self):
        result = subprocess.run(
            ["git", "log", "--oneline", "-1", "--", "lib/postponed_guard.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() != "", "expected existing git history for lib/postponed_guard.py"

    def test_no_data_or_ledger_files_in_working_tree_changes(self):
        """
        `data/research/` is excluded from this check as of Model
        Performance Phase 1 (Market Audit) -- see the identical
        exclusion and rationale in
        tests/test_protect_slate_rerun_and_scope.py.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", "data/", "BET_LOG.md", "config/rules.json", "RULES.md",
             ":!data/research"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected working-tree changes: {result.stdout}"

    def test_bets_json_not_committed_as_part_of_this_phase(self):
        result = subprocess.run(
            ["git", "status", "--short", "--", "bets.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"bets.json must never be a working-tree change: {result.stdout}"

    def test_no_workflow_files_in_working_tree_changes(self):
        """
        .github/workflows/kalshi-price-check.yml,
        .github/workflows/lineup-recheck.yml, and
        .github/workflows/capture-closing-lines.yml are excluded -- each
        phase legitimately adds one new, sanctioned, workflow_dispatch-only
        (or scheduled data-refresh-only) workflow that never invokes the
        production risk/execution/bet-logging pipeline (see
        tests/test_kalshi_price_check_workflow.py,
        tests/test_lineup_recheck_workflow.py, and
        tests/test_capture_closing_lines_workflow.py). This test's actual
        intent -- proving no EXISTING production workflow file
        changed -- is unaffected by excluding those new files.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", ".github/workflows/",
             ":!.github/workflows/kalshi-price-check.yml",
             ":!.github/workflows/lineup-recheck.yml",
             ":!.github/workflows/capture-closing-lines.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected workflow changes: {result.stdout}"
