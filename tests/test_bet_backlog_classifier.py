#!/usr/bin/env python3
"""
tests/test_bet_backlog_classifier.py
=========================================
Coverage for lib/bet_backlog_classifier.py and
scripts/remediate_bet_backlog.py (Production Reliability and Settlement
Recovery milestone). Real 2026-06-19 shapes (multi-tranche same-ticker
bets, one automated + one manual) are used verbatim as regression
fixtures for the duplicate detector, since that exact case was found
during this milestone's investigation to be a legitimate non-duplicate.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.bet_backlog_classifier import (
    CATEGORY_DUPLICATE,
    CATEGORY_LEGITIMATELY_PENDING,
    CATEGORY_MALFORMED_RECORD,
    CATEGORY_MISSING_SOURCE_DATA,
    CATEGORY_PIPELINE_FAILURE,
    CATEGORY_REQUIRES_MANUAL_REVIEW,
    CATEGORY_UNSUPPORTED_MARKET_FAMILY,
    build_plan,
    classify_bet,
    find_duplicates,
    is_non_terminal,
    parse_game_teams,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = "2026-08-02"


def _bet(bet_id, date="2026-07-20", game="NYY @ BOS", market="ML", status="pending", **overrides):
    rec = {"id": bet_id, "date": date, "game": game, "market": market, "status": status, "result": None}
    rec.update(overrides)
    return rec


# ── parse_game_teams ──────────────────────────────────────────────────────

def test_parse_game_teams_at_separator_with_spaces():
    assert parse_game_teams("NYY @ BOS") == ("NYY", "BOS")


def test_parse_game_teams_at_separator_no_spaces():
    assert parse_game_teams("SD@TEX") == ("SD", "TEX")


def test_parse_game_teams_unparseable_returns_none():
    assert parse_game_teams("") == (None, None)
    assert parse_game_teams(None) == (None, None)
    assert parse_game_teams("garbage no separator") == (None, None)


# ── is_non_terminal ───────────────────────────────────────────────────────

def test_is_non_terminal_true_for_pending_any_casing():
    assert is_non_terminal(_bet("b1", status="pending"))
    assert is_non_terminal(_bet("b2", status="PENDING"))
    assert is_non_terminal(_bet("b3", status="open"))


def test_is_non_terminal_false_for_terminal_results():
    for result in ("WIN", "LOSS", "PUSH", "VOID", "NO_ACTION"):
        assert not is_non_terminal(_bet("b", status="settled", result=result))


def test_is_non_terminal_result_field_wins_over_status():
    """Mirrors clv_update.py's own get_result(): result field takes priority over status."""
    assert not is_non_terminal(_bet("b", status="pending", result="WIN"))


# ── find_duplicates ───────────────────────────────────────────────────────

def test_multi_tranche_same_ticker_never_flagged_as_duplicate():
    """Real 2026-06-19 finding: two independent tranches on the same F5 ML ticker (one automated, one manual) must never be treated as duplicates."""
    tranche_a = _bet(
        "2026-06-19-114", date="2026-06-19", game="SD@TEX", market="F5_ML_Away",
        stake=4.5, entryTimestamp="2026-06-19T22:59:22.840694+00:00", source="data/slate.json",
    )
    tranche_b = _bet(
        "2026-06-19-124", date="2026-06-19", game="SD@TEX", market="F5_ML_Away",
        stake=1.5, entryTimestamp="2026-06-19T23:05:59.320394+00:00", source="claude_session_2026-06-19",
    )
    dupes = find_duplicates([tranche_a, tranche_b])
    assert dupes == set()


def test_true_content_duplicate_detected():
    original = _bet("b1", date="2026-06-19", game="SD@TEX", market="F5_ML_Away", stake=4.5)
    # Same record content, different id only (e.g. accidentally re-logged).
    copy = dict(original)
    copy["id"] = "b2"
    dupes = find_duplicates([original, copy])
    assert dupes == {"b2"}  # only the SECOND occurrence is flagged


def test_no_duplicates_in_real_bets_json():
    """Regression guard matching this milestone's real-data finding: 0 true content-duplicates in the actual ledger."""
    with open(os.path.join(ROOT, "bets.json")) as f:
        bets = json.load(f)
    assert find_duplicates(bets) == set()


# ── classify_bet ──────────────────────────────────────────────────────────

def test_classify_malformed_record_missing_date():
    bet = _bet("b1", date=None)
    assert classify_bet(bet, TODAY) == CATEGORY_MALFORMED_RECORD


def test_classify_malformed_record_unparseable_game():
    bet = _bet("b1", game="not a real game string")
    assert classify_bet(bet, TODAY) == CATEGORY_MALFORMED_RECORD


def test_classify_malformed_record_future_dated():
    bet = _bet("b1", date="2099-01-01")
    assert classify_bet(bet, TODAY) == CATEGORY_MALFORMED_RECORD


def test_classify_legitimately_pending_within_window():
    assert classify_bet(_bet("b1", date="2026-08-01"), TODAY) == CATEGORY_LEGITIMATELY_PENDING
    assert classify_bet(_bet("b1", date="2026-07-31"), TODAY) == CATEGORY_LEGITIMATELY_PENDING


def test_classify_unsupported_market_family_nrfi_yrfi():
    assert classify_bet(_bet("b1", date="2026-07-01", market="NRFI"), TODAY) == CATEGORY_UNSUPPORTED_MARKET_FAMILY
    assert classify_bet(_bet("b1", date="2026-07-01", market="YRFI"), TODAY) == CATEGORY_UNSUPPORTED_MARKET_FAMILY


def test_classify_unsupported_market_family_wins_over_missing_source_data():
    """A pre-workflow-creation NRFI bet is unsupported_market_family, not missing_source_data -- it would never auto-settle regardless of when it was placed."""
    bet = _bet("b1", date="2026-06-06", market="NRFI")
    assert classify_bet(bet, TODAY) == CATEGORY_UNSUPPORTED_MARKET_FAMILY


def test_classify_missing_source_data_before_workflow_creation():
    bet = _bet("b1", date="2026-06-08", market="ML")
    assert classify_bet(bet, TODAY) == CATEGORY_MISSING_SOURCE_DATA


def test_classify_pipeline_failure_known_failed_date():
    bet = _bet("b1", date="2026-06-15", market="ML")
    assert classify_bet(bet, TODAY) == CATEGORY_PIPELINE_FAILURE


def test_classify_requires_manual_review_fallback():
    bet = _bet("b1", date="2026-06-19", market="ML")
    assert classify_bet(bet, TODAY) == CATEGORY_REQUIRES_MANUAL_REVIEW


def test_classify_deterministic_across_repeated_calls():
    bet = _bet("b1", date="2026-06-19", market="ML")
    results = {classify_bet(bet, TODAY) for _ in range(5)}
    assert len(results) == 1


# ── build_plan ────────────────────────────────────────────────────────────

def test_build_plan_never_populates_auto_safe_changes_without_evidence():
    bets = [_bet("b1", date="2026-06-19", market="ML")]
    plan = build_plan(bets, today=TODAY)
    assert plan["autoSafeChanges"] == []


def test_build_plan_date_range_scoping():
    bets = [
        _bet("b1", date="2026-06-06", market="ML"),
        _bet("b2", date="2026-07-20", market="ML"),
    ]
    plan = build_plan(bets, today=TODAY, date_from="2026-07-01")
    ids = {item["id"] for cat in plan["classificationDetail"].values() for item in cat}
    assert ids == {"b2"}


def test_build_plan_skips_terminal_bets():
    bets = [_bet("b1", date="2026-06-19", status="settled", result="WIN")]
    plan = build_plan(bets, today=TODAY)
    assert plan["totalConsidered"] == 0


def test_build_plan_recommends_manual_rerun_dates():
    bets = [_bet("b1", date="2026-06-15", market="ML")]  # known pipeline_failure date
    plan = build_plan(bets, today=TODAY)
    assert "2026-06-15" in plan["recommendedManualRerunDates"]


def test_build_plan_deterministic_and_reproducible():
    bets = [
        _bet("b1", date="2026-06-06", market="NRFI"),
        _bet("b2", date="2026-06-19", market="ML"),
        _bet("b3", date="2026-07-31", market="ML"),
    ]
    plan_a = build_plan(bets, today=TODAY)
    plan_b = build_plan(bets, today=TODAY)
    assert plan_a["classificationCounts"] == plan_b["classificationCounts"]


def test_build_plan_matches_real_ledger_classification_counts():
    """
    Real-data regression: running this classifier against the actual
    committed bets.json (as of this milestone) must always sum to the
    total number of non-terminal bets considered -- a coarse but
    meaningful sanity check that every considered bet lands in exactly
    one category.
    """
    with open(os.path.join(ROOT, "bets.json")) as f:
        bets = json.load(f)
    plan = build_plan(bets, today=TODAY)
    assert sum(plan["classificationCounts"].values()) == plan["totalConsidered"]
    assert plan["totalConsidered"] > 0


# ── CLI (scripts/remediate_bet_backlog.py) ───────────────────────────────

def test_cli_dry_run_never_modifies_bets_json(tmp_path):
    bets_path = tmp_path / "bets.json"
    original = [_bet("b1", date="2026-06-19", market="ML")]
    with open(bets_path, "w") as f:
        json.dump(original, f)

    plan_out = tmp_path / "plan.json"
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "remediate_bet_backlog.py"),
         "--bets-path", str(bets_path), "--plan-out", str(plan_out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    with open(bets_path) as f:
        after = json.load(f)
    assert after == original
    assert plan_out.exists()
    with open(plan_out) as f:
        plan = json.load(f)
    assert plan["mode"] == "DRY_RUN"


def test_cli_execute_with_no_auto_safe_changes_does_not_modify_bets_json(tmp_path):
    bets_path = tmp_path / "bets.json"
    original = [_bet("b1", date="2026-06-19", market="ML")]
    with open(bets_path, "w") as f:
        json.dump(original, f)

    plan_out = tmp_path / "plan.json"
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "remediate_bet_backlog.py"),
         "--bets-path", str(bets_path), "--plan-out", str(plan_out), "--execute"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    with open(bets_path) as f:
        after = json.load(f)
    assert after == original  # no autoSafeChanges exist in this milestone -- nothing to apply
