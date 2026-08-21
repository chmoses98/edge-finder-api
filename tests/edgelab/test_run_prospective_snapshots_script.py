#!/usr/bin/env python3
"""
tests/edgelab/test_run_prospective_snapshots_script.py
============================================================
Coverage for scripts/edgelab/run_prospective_snapshots.py -- its pure,
testable compute_run_status() helper (reliability pass, spec section 9)
and slate_staleness_reason() (ModelEvaluation Prospective Coverage
Reliability mission -- the fix for the real, confirmed 2026-08-11
through 2026-08-15 gap: data/slate.json went stale for 6 days because
fetch-slate.yml has no cron trigger of its own, and every scheduled
model-snapshot-scheduler.yml cycle during that window silently reported
the ordinary, harmless "no_op" status against the same dead slate --
indistinguishable from a normal quiet cycle). The rest of the script is
a thin I/O wrapper around
lib.edgelab.prospective_snapshot.run_prospective_snapshot_cycle, already
covered by tests/edgelab/test_prospective_snapshot.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from scripts.edgelab.run_prospective_snapshots import compute_run_status, main, slate_staleness_reason


def test_no_op_when_nothing_evaluated_and_no_failures():
    assert compute_run_status(evaluated_count=0, genuine_failure_count=0) == "no_op"


def test_success_when_something_evaluated_and_no_failures():
    assert compute_run_status(evaluated_count=3, genuine_failure_count=0) == "success"


def test_partial_when_some_succeeded_and_some_genuinely_failed():
    assert compute_run_status(evaluated_count=11, genuine_failure_count=1) == "partial"


def test_failed_when_attempted_but_nothing_succeeded():
    assert compute_run_status(evaluated_count=0, genuine_failure_count=1) == "failed"


def test_never_returns_success_merely_because_process_reached_the_end():
    """A run with genuine failures must never be reported 'success', regardless of how many other games succeeded."""
    for evaluated in (0, 1, 5, 100):
        assert compute_run_status(evaluated_count=evaluated, genuine_failure_count=1) != "success"


class TestSlateStalenessReason:
    def test_fresh_slate_same_day_is_not_stale(self):
        slate = {"date": "2026-08-20"}
        assert slate_staleness_reason(slate, "2026-08-20T14:00:00Z") is None

    def test_slate_from_normal_one_day_ago_cadence_is_not_stale(self):
        """Real observed fetch-slate.yml cadence in this repo ranges ~19-29h; must never false-positive inside that range."""
        slate = {"date": "2026-08-19"}
        assert slate_staleness_reason(slate, "2026-08-20T14:00:00Z") is None

    def test_slate_stale_for_six_days_is_flagged(self):
        """The exact real-world shape of the 2026-08-11..15 gap: slate.json still says 2026-08-10 five days later."""
        slate = {"date": "2026-08-10"}
        reason = slate_staleness_reason(slate, "2026-08-15T20:00:00Z")
        assert reason is not None
        assert "2026-08-10" in reason

    def test_slate_stale_just_over_threshold_is_flagged(self):
        slate = {"date": "2026-08-18"}
        reason = slate_staleness_reason(slate, "2026-08-20T01:00:00Z")  # 37h later
        assert reason is not None

    def test_missing_slate_is_flagged(self):
        assert slate_staleness_reason(None, "2026-08-20T14:00:00Z") is not None
        assert slate_staleness_reason({}, "2026-08-20T14:00:00Z") is not None

    def test_unparseable_date_is_flagged_not_crashed(self):
        assert slate_staleness_reason({"date": "not-a-date"}, "2026-08-20T14:00:00Z") is not None

    def test_precise_timestamp_preferred_over_bare_date_when_fresh(self):
        """A same-day slate generated a few hours ago must never be flagged, even though the bare 'date' field is only day-granular."""
        slate = {"date": "2026-08-20", "executionSlipGeneratedAt": "2026-08-20T10:00:00+00:00"}
        assert slate_staleness_reason(slate, "2026-08-20T14:00:00Z") is None

    def test_precise_timestamp_flags_staleness_within_the_same_calendar_day_gap(self):
        """Precise timestamp catches staleness the coarse date-only fallback would miss (both dates are 'yesterday' vs 'today', a 1-day gap the fallback tolerates, but the real elapsed time already exceeds the hourly threshold)."""
        slate = {"date": "2026-08-19", "executionSlipGeneratedAt": "2026-08-19T01:00:00+00:00"}
        reason = slate_staleness_reason(slate, "2026-08-20T14:00:00Z")  # 37h later
        assert reason is not None
        assert "executionSlipGeneratedAt" in reason


class TestMainStaleSlateExitsNonZeroAndSurfacesRunRecord:
    """
    End-to-end: main() against a real, tmp_path-isolated data/ tree with a
    6-day-stale slate.json must (a) exit non-zero -- making the GitHub
    Actions step, and therefore the whole job, visibly red instead of the
    old silent success -- and (b) still write an explicit, findable
    research_run record (never a record buried under the stale date --
    filed under the actual run date) so a human auditing "why is today
    empty" finds a direct answer instead of nothing at all.
    """

    def test_stale_slate_exits_nonzero_and_writes_findable_run_record(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        game = {
            "gameId": 111, "away": {"abbr": "BOS"}, "home": {"abbr": "NYY"},
            "startTime": "2026-08-10T23:00:00Z",
        }
        with open("data/slate.json", "w") as f:
            json.dump({"date": "2026-08-10", "games": [game]}, f)

        monkeypatch.setattr(sys, "argv", ["run_prospective_snapshots.py"])
        monkeypatch.setenv("GITHUB_RUN_ID", "12345")
        # A run occurring 5 days after the slate's own date -- exactly the
        # real 2026-08-11..15 gap's shape.
        import lib.edgelab.ids as ids_module
        monkeypatch.setattr(ids_module, "utc_now_iso", lambda: "2026-08-15T20:00:00Z")

        exit_code = main()
        assert exit_code == 1

        captured = capsys.readouterr()
        assert "STALE SLATE" in captured.err

        # Findable under TODAY's date, not buried under the stale
        # 2026-08-10 slate date.
        run_path = storage.partition_path("research_runs", "2026-08-15")
        assert os.path.exists(run_path)
        records = list(storage.read_records(run_path))
        assert len(records) == 1
        assert records[0]["status"] == "stale_slate"
        assert records[0]["runType"] == "PROSPECTIVE_SNAPSHOT"
        assert records[0]["errors"]
        assert "2026-08-10" in records[0]["errors"][0]

        # No ModelEvaluation rows fabricated for the stale slate's games.
        eval_path = storage.partition_path("model_evaluations", "2026-08-10")
        assert not os.path.exists(eval_path)

    def test_explicit_date_override_bypasses_staleness_check(self, tmp_path, monkeypatch):
        """An operator explicitly requesting a specific date (backfill/manual test) is a deliberate override, never flagged as 'stale'."""
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        game = {
            "gameId": 222, "away": {"abbr": "SEA"}, "home": {"abbr": "TEX"},
            "startTime": "2026-08-10T23:00:00Z",
        }
        with open("data/slate.json", "w") as f:
            json.dump({"date": "2026-08-10", "games": [game]}, f)

        monkeypatch.setattr(sys, "argv", ["run_prospective_snapshots.py", "--date", "2026-08-10", "--dry-run"])
        import lib.edgelab.ids as ids_module
        monkeypatch.setattr(ids_module, "utc_now_iso", lambda: "2026-08-15T20:00:00Z")

        exit_code = main()
        # Bypasses the staleness short-circuit entirely and reaches the
        # normal dry-run path (the game is correctly excluded as STARTED
        # by the ordinary eligibility check, not by staleness detection).
        assert exit_code == 0
