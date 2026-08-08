#!/usr/bin/env python3
"""
tests/test_settlement_reliability_milestone.py
====================================================
Production Reliability and Settlement Recovery milestone: regression
coverage locking in settlement-reliability behaviors that were audited
and confirmed CORRECT during this milestone (not changed -- these tests
exist because the milestone's own task list explicitly calls for
coverage of each, even where no fix was needed):

  - Repeated settlement reruns are idempotent (already-terminal bets are
    never re-graded or double-counted).
  - Multiple bet tranches on the same ticker all settle independently
    (clv_update.py's settlement loop iterates every bet, never dedupes
    by ticker).
  - Malformed settlement data (unparseable game string) is safely
    skipped, not a crash.
  - F5 ML and NRFI/YRFI are never auto-graded from raw scores --
    determine_result() always returns None for them, forcing manual
    settlement (the "YES/NO ticker vs placed-bet WIN/LOSS" ambiguity
    this milestone's task list flags is structurally avoided by never
    auto-grading these families at all, not by a heuristic that could
    get it wrong).
  - Manual bets with no marketTicker still appear in the final bets.json
    after a full run (never silently dropped).

Runs clv_update.main() fully offline: fetch_scores and
fetch_mlb_schedule_gamepks are monkeypatched to fixed, deterministic
values -- no network access, no live API dependency.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def cu(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "dummy-test-key")
    if "clv_update" in sys.modules:
        del sys.modules["clv_update"]
    import clv_update as _cu
    monkeypatch.setattr(_cu, "fetch_scores", lambda date_str: {
        ("SD", "TEX"): {"away_score": 5, "home_score": 2, "completed": True},
    })
    monkeypatch.setattr(_cu, "fetch_mlb_schedule_gamepks", lambda date_str: {})
    return _cu


def _wire(tmp_path, monkeypatch, bets):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    with open(tmp_path / "bets.json", "w") as f:
        json.dump(bets, f)
    return tmp_path


def _run(cu, tmp_path, date="2026-08-02"):
    sys.argv = ["clv_update.py", date]
    cu.main()
    return json.loads((tmp_path / "bets.json").read_text())


class TestRepeatedSettlementReruns:

    def test_settled_bet_is_not_double_counted_on_rerun(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "betTimeLine": -120, "status": "pending", "pl": None, "result": None},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        first = _run(cu, root)
        assert first[0]["result"] == "WIN"  # SD (away) scored 5 > TEX's 2
        first_pl = first[0].get("pl")

        second = _run(cu, root)
        assert second[0]["result"] == "WIN"
        assert second[0].get("pl") == first_pl  # not doubled/re-computed differently

    def test_rerun_with_unchanged_input_produces_byte_identical_ledger(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "betTimeLine": -120, "status": "pending", "pl": None, "result": None},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        first = _run(cu, root)
        second = _run(cu, root)
        assert first == second


class TestMultipleTranchesOnOneTicker:

    def test_all_tranches_on_the_same_ticker_settle_independently(self, cu, tmp_path, monkeypatch):
        """
        Three separate bet records, same game/market/ticker, different
        stake sizes and ids -- clv_update.py's settlement loop must grade
        every one of them, not just the first (or a deduped single row).
        """
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "betTimeLine": -120, "status": "pending", "betSize": 2.0,
             "marketTicker": "KXMLBGAME-26AUG02-SD"},
            {"id": "2026-08-02-002", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "betTimeLine": -115, "status": "pending", "betSize": 3.0,
             "marketTicker": "KXMLBGAME-26AUG02-SD"},
            {"id": "2026-08-02-003", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "betTimeLine": -110, "status": "pending", "betSize": 1.5,
             "marketTicker": "KXMLBGAME-26AUG02-SD"},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        result = _run(cu, root)
        assert len(result) == 3
        assert all(b["result"] == "WIN" for b in result)
        assert {b["id"] for b in result} == {"2026-08-02-001", "2026-08-02-002", "2026-08-02-003"}


class TestMalformedSettlementData:

    def test_unparseable_game_string_is_skipped_not_crashed(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "not a valid game string",
             "market": "ML", "betSide": "AWAY", "status": "pending"},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        result = _run(cu, root)  # must not raise
        assert result[0].get("result") in (None, "pending", "PUSH", "WIN", "LOSS")
        # Specifically: an unparseable game must NOT have been silently
        # graded WIN/LOSS from garbage input.
        assert result[0].get("result") not in ("WIN", "LOSS")

    def test_missing_game_field_entirely_is_skipped_not_crashed(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "market": "ML", "status": "pending"},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        result = _run(cu, root)  # must not raise
        assert result[0].get("result") not in ("WIN", "LOSS")

    def test_unrecognized_market_is_skipped_not_crashed(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "SD @ TEX",
             "market": "Some Made Up Market Type", "status": "pending"},
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        result = _run(cu, root)  # must not raise
        assert result[0].get("result") not in ("WIN", "LOSS")


class TestUnsupportedMarketsNeverAutoGraded:
    """
    F5 ML and NRFI/YRFI are structurally routed to manual settlement --
    determine_result() returns None for them unconditionally, regardless
    of what the final score was. This is the actual safeguard against the
    "YES/NO ticker vs WIN/LOSS" ambiguity: these families are never
    auto-graded from a raw score at all.
    """

    def test_f5_ml_never_auto_graded_from_full_game_score(self):
        import clv_update as cu
        result, away_sc, home_sc = cu.determine_result(
            {"betSide": "AWAY"},
            {("SD", "TEX"): {"away_score": 5, "home_score": 2, "completed": True}},
            "SD", "TEX", "F5 ML",
        )
        assert result is None
        assert away_sc == 5 and home_sc == 2

    def test_nrfi_never_auto_graded_from_full_game_score(self):
        import clv_update as cu
        result, _, _ = cu.determine_result(
            {"betSide": "AWAY"},
            {("SD", "TEX"): {"away_score": 5, "home_score": 2, "completed": True}},
            "SD", "TEX", "NRFI",
        )
        assert result is None

    def test_yrfi_never_auto_graded_from_full_game_score(self):
        import clv_update as cu
        result, _, _ = cu.determine_result(
            {"betSide": "AWAY"},
            {("SD", "TEX"): {"away_score": 5, "home_score": 2, "completed": True}},
            "SD", "TEX", "YRFI",
        )
        assert result is None


class TestManualBetsWithoutTickerLinkageStayVisible:

    def test_bet_with_no_market_ticker_survives_a_full_run(self, cu, tmp_path, monkeypatch):
        bets = [
            {"id": "2026-08-02-001", "date": "2026-08-02", "game": "SD @ TEX", "market": "ML",
             "betSide": "AWAY", "status": "pending"},  # no marketTicker at all
        ]
        root = _wire(tmp_path, monkeypatch, bets)
        result = _run(cu, root)
        assert len(result) == 1
        assert result[0]["id"] == "2026-08-02-001"
        # Still gradeable by score even without a ticker -- ML doesn't need one.
        assert result[0]["result"] == "WIN"


class TestNoProductionRecommendationChanges:
    """
    Written for the Production Reliability and Settlement Recovery
    milestone, which explicitly must NOT change probability calculations,
    recommendation thresholds, betting tiers, stake sizing, market
    selection, or F5 tie pricing (that milestone deferred the F5 pricing
    bug to a separate future milestone). That milestone has since merged.

    `scripts/build_market_ledger.py` and `api/slate.js` are deliberately
    REMOVED from core_files here: the F5 Three-Way Pricing Correction
    milestone (a later one) was explicitly authorized to change F5
    fair probabilities in build_market_ledger.py, because the prior
    two-way renormalization was mathematically incorrect (see
    docs/F5_THREE_WAY_PRICING.md), and it ADDS new, additive,
    F5-specific pure functions to api/slate.js for cross-language parity
    fixtures -- full-game ML logic in that file
    (gameProbs/calcModelProb) must still be byte-for-byte unchanged,
    which is checked separately below rather than via a blanket
    zero-diff requirement on the whole file.

    `scripts/risk_gate.py` is ALSO now removed from core_files: the
    Portfolio Correlation Gate milestone (a still later one) is
    explicitly authorized to add same-game correlation/concentration
    handling there (evaluate_correlation_gate/apply_correlation_gate) --
    an entirely new, downgrade-only, additive pass that runs before the
    existing TT/portfolio-composition gates, never altering probability
    calculations, edge computation, or executable pricing. Everything
    else in this list remains a genuine "must not change" boundary for
    every milestone since, including this one: executable-price
    convention, settlement (lib/f5_settlement.py -- game-score-based
    grading, untouched), and config/rules.json/RULES.md (no rule values
    altered).
    """

    def test_core_handicapping_files_have_zero_working_tree_changes(self):
        core_files = [
            "scripts/executable_price.py",
            "scripts/reason_codes.py",
            "lib/f5_settlement.py",
            "config/rules.json",
            "RULES.md",
        ]
        result = subprocess.run(
            ["git", "status", "--short", "--"] + core_files,
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected handicapping-logic changes: {result.stdout}"

    def test_full_game_js_probability_functions_byte_identical(self):
        """
        api/slate.js's full-game win-probability engine (gameProbs,
        calcModelProb -- including the extra-inning blend and the 72%
        win-probability cap) must be byte-for-byte unchanged by this
        milestone. New F5-specific parity functions may be ADDED
        elsewhere in the same file, so this checks the specific function
        bodies via `git diff`'s own function-context hunk headers rather
        than requiring a zero-diff on the whole file.
        """
        result = subprocess.run(
            ["git", "diff", "--", "api/slate.js"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        diff = result.stdout
        assert "function gameProbs" not in diff
        assert "function calcModelProb" not in diff

    def test_determine_result_function_body_unchanged_by_this_milestone(self):
        """
        Belt-and-suspenders: even though clv_update.py itself WAS touched
        this milestone (atomic-write migration, run-summary output),
        determine_result() -- the actual WIN/LOSS/PUSH grading logic --
        must be byte-for-byte unchanged. Confirmed via `git diff` scoped
        to the function's own line range never showing a hunk.
        """
        result = subprocess.run(
            ["git", "diff", "--", "clv_update.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        diff = result.stdout
        # A diff touching determine_result would show a hunk header
        # containing its name (git's default function-context detection).
        assert "def determine_result" not in diff
