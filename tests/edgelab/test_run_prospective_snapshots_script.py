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
from scripts.edgelab import run_prospective_snapshots as rps
from scripts.edgelab.run_prospective_snapshots import compute_run_status, main, run_shadow_step, slate_staleness_reason


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


class TestMlbRsch0011ProductionEquivalence:
    """
    MLB-RSCH-0011's own required regression: production-facing output
    (data/edgelab/model_evaluations/<date>.jsonl content, and this
    script's own exit code/run_status) must be BYTE-IDENTICAL whether
    the MLB-RSCH-0011 shadow step succeeds, fails, or was never added at
    all -- since run_shadow_step is only ever called AFTER the core
    write already happened (see main()'s own ordering), nothing it does
    can retroactively change what was already written. This is exercised
    end-to-end through main() against a real (tmp_path-isolated)
    filesystem tree, using fake evaluate_game_fn/compute_projection_context_fn
    (same convention as tests/edgelab/test_prospective_snapshot.py -- the
    real production functions' own correctness is unaffected by this
    milestone since scripts/build_market_ledger.py is never modified by
    it at all).
    """

    def _write_slate(self, date, game_id=333):
        os.makedirs("data", exist_ok=True)
        # T_MINUS_90 window: game starts 90 minutes after `now` below --
        # same exact shape as tests/edgelab/test_prospective_snapshot.py's
        # own T_MINUS_90 fixtures.
        game = {
            "gameId": game_id, "away": {"abbr": "SEA"}, "home": {"abbr": "TEX"},
            "startTime": f"{date}T23:00:00Z",
        }
        with open("data/slate.json", "w") as f:
            json.dump({"date": date, "games": [game], "executionSlipGeneratedAt": f"{date}T15:00:00Z"}, f)
        return game

    def _fake_evaluate_game(self, g, ctx):
        return [{"market": "ML_Away", "ticker": f"T-{g['gameId']}", "modelProb": 55.0, "status": "Accepted", "kalshiVF": 50.0, "edge": 5.0}]

    def _fake_projection_context(self, g):
        return {"awayProjRuns": 3.8, "homeProjRuns": 4.2, "totalProj": 8.0, "missingFields": []}

    def _run_main(self, tmp_path, monkeypatch, date, now_iso, shadow_should_fail=False):
        os.makedirs(tmp_path, exist_ok=True)
        monkeypatch.chdir(tmp_path)
        self._write_slate(date)
        monkeypatch.setattr(sys, "argv", ["run_prospective_snapshots.py"])
        monkeypatch.setattr(rps, "evaluate_game", self._fake_evaluate_game)
        monkeypatch.setattr(rps, "compute_game_projection_context", self._fake_projection_context)
        # No real network access in a test -- fake the lineup poll/wOBA
        # loaders too (this checkpoint, T_MINUS_90, doesn't depend on
        # their content, only that main() can call them without hitting
        # the network).
        monkeypatch.setattr(rps, "fetch_lineup_for_game", lambda *a, **k: None)
        monkeypatch.setattr(rps, "load_batter_woba", lambda: {})
        monkeypatch.setattr(rps, "load_team_woba", lambda: {})
        monkeypatch.setattr(rps, "_live_status_by_team_pair", lambda date: {})
        import lib.edgelab.ids as ids_module
        monkeypatch.setattr(ids_module, "utc_now_iso", lambda: now_iso)
        if shadow_should_fail:
            def _boom(*a, **k):
                raise RuntimeError("simulated MLB-RSCH-0011 shadow failure")
            monkeypatch.setattr(
                "lib.edgelab.shadow_distribution.build_shadow_records_for_snapshot_cycle", _boom,
            )
        exit_code = main()
        eval_path = storage.partition_path("model_evaluations", date)
        model_eval_content = open(eval_path).read() if os.path.exists(eval_path) else None
        return exit_code, model_eval_content

    def test_model_evaluations_output_identical_whether_shadow_succeeds_or_fails(self, tmp_path, monkeypatch):
        date, now_iso = "2026-08-20", "2026-08-20T21:30:00Z"

        exit_ok, content_shadow_ok = self._run_main(tmp_path / "a", monkeypatch, date, now_iso, shadow_should_fail=False)
        exit_fail, content_shadow_fail = self._run_main(tmp_path / "b", monkeypatch, date, now_iso, shadow_should_fail=True)

        assert exit_ok == 0 and exit_fail == 0
        assert content_shadow_ok is not None and content_shadow_fail is not None

        # runId/modelEvaluationId embed lib.edgelab.ids' own random
        # uniqueness token (pre-existing, unrelated to MLB-RSCH-0011) --
        # every OTHER field, including every production probability/
        # evaluationStatus/marketFairProbability field, must be
        # byte-identical regardless of whether the shadow step succeeded.
        def _normalized(content):
            rows = [json.loads(line) for line in content.strip().splitlines()]
            for row in rows:
                row.pop("runId", None)
                row.pop("modelEvaluationId", None)
                row.pop("recommendationId", None)
            return rows

        assert _normalized(content_shadow_ok) == _normalized(content_shadow_fail)

    def test_shadow_step_never_changes_this_scripts_exit_code(self, tmp_path, monkeypatch):
        date, now_iso = "2026-08-21", "2026-08-21T21:30:00Z"
        exit_code, _ = self._run_main(tmp_path, monkeypatch, date, now_iso, shadow_should_fail=True)
        assert exit_code == 0

    def test_successful_shadow_run_writes_its_own_separate_research_only_file(self, tmp_path, monkeypatch):
        date, now_iso = "2026-08-22", "2026-08-22T21:30:00Z"
        self._run_main(tmp_path, monkeypatch, date, now_iso, shadow_should_fail=False)
        shadow_path = storage.partition_path("mlb_rsch_0011_shadow_evaluations", date)
        assert os.path.exists(shadow_path)
        records = list(storage.read_records(shadow_path))
        assert len(records) == 1
        assert records[0]["computationStatus"] == "SUCCESS"
        assert records[0]["cells"]

    def test_failed_shadow_run_writes_no_shadow_file_but_model_evaluations_still_written(self, tmp_path, monkeypatch):
        date, now_iso = "2026-08-23", "2026-08-23T21:30:00Z"
        exit_code, model_eval_content = self._run_main(tmp_path, monkeypatch, date, now_iso, shadow_should_fail=True)
        assert exit_code == 0
        assert model_eval_content is not None and "ML_Away" in model_eval_content
        shadow_path = storage.partition_path("mlb_rsch_0011_shadow_evaluations", date)
        assert not os.path.exists(shadow_path)


class TestRunShadowStepIsolation:
    def test_empty_evaluated_snapshots_is_a_pure_no_op(self):
        written, skipped, error = run_shadow_step([], run_id="r1", date="2026-08-20")
        assert (written, skipped, error) == (0, 0, None)

    def test_import_failure_inside_shadow_module_is_caught_and_reported_never_raised(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "lib.edgelab.shadow_distribution":
                raise ImportError("simulated broken shadow module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        written, skipped, error = run_shadow_step(
            [{"gameId": "g1", "checkpoint": "T_MINUS_90", "game": {"gameId": "g1"}}], run_id="r1", date="2026-08-20",
        )
        assert (written, skipped) == (0, 0)
        assert error is not None and "simulated broken shadow module" in error
