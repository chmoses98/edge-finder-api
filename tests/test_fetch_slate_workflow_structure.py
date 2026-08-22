#!/usr/bin/env python3
"""
tests/test_fetch_slate_workflow_structure.py
==============================================
Structural regression test for .github/workflows/fetch-slate.yml.

Guards the decoupling introduced to fix the 2026-07-25/07-26 incident:
optional execution/logging steps (risk gate, bet logging, CLV capture)
must never be able to block publication of the authoritative slate and
data/meta.json. This test does not run the workflow — it asserts the YAML
structure enforces the invariant:

  1. The step that commits data/meta.json ("publish_slate") runs BEFORE
     the execution/logging steps (risk_gate, write_pending_bets,
     validate_bet_logging, write_tracked_tickers, capture_closing_lines).
  2. Every execution/logging step has continue-on-error: true, so a
     failure there does not fail the job and does not skip later steps.
  3. The final stage-status step runs unconditionally (if: always()) so
     a stage-status artifact is always produced, and it runs AFTER the
     execution/logging chain so it can report on their outcomes.
"""

import os
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")

OPTIONAL_STEP_IDS = [
    "risk_gate",
    "write_pending_bets",
    "validate_bet_logging",
    "write_tracked_tickers",
    "capture_closing_lines",
]


@pytest.fixture(scope="module")
def steps():
    with open(WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    return data["jobs"]["fetch"]["steps"]


def _index_by_id(steps, step_id):
    for i, s in enumerate(steps):
        if s.get("id") == step_id:
            return i
    raise AssertionError(f"No step with id={step_id!r} found in {WORKFLOW_PATH}")


def test_publish_slate_step_exists_and_commits_meta(steps):
    idx = _index_by_id(steps, "publish_slate")
    assert "meta.json" in steps[idx]["run"]


def test_publish_slate_precedes_every_optional_execution_step(steps):
    """
    The authoritative slate/meta commit must run before any step whose
    failure is allowed (continue-on-error) — otherwise a downstream
    execution/logging failure can still race the publish step or leave
    ordering unclear on future edits.
    """
    publish_idx = _index_by_id(steps, "publish_slate")
    for step_id in OPTIONAL_STEP_IDS:
        opt_idx = _index_by_id(steps, step_id)
        assert opt_idx > publish_idx, (
            f"step id={step_id!r} (index {opt_idx}) must come AFTER "
            f"publish_slate (index {publish_idx}) so its failure cannot "
            f"block authoritative slate publication"
        )


def test_every_optional_execution_step_has_continue_on_error(steps):
    for step_id in OPTIONAL_STEP_IDS:
        idx = _index_by_id(steps, step_id)
        assert steps[idx].get("continue-on-error") is True, (
            f"step id={step_id!r} must set continue-on-error: true so its "
            f"failure does not fail the job or block later steps"
        )


def test_publish_slate_step_itself_is_not_continue_on_error(steps):
    """
    publish_slate must remain a hard step (no continue-on-error) — if
    committing the authoritative slate itself fails, that IS a real
    publication failure and must be visible as a job failure, not silently
    swallowed.
    """
    idx = _index_by_id(steps, "publish_slate")
    assert steps[idx].get("continue-on-error") is not True


def _index_by_name_substring(steps, substring):
    for i, s in enumerate(steps):
        if substring in (s.get("name") or ""):
            return i
    raise AssertionError(f"No step with name containing {substring!r} found in {WORKFLOW_PATH}")


def test_stage_status_step_runs_always_and_after_optional_steps(steps):
    """
    Historical Capture Completeness and Immutable Snapshot Foundation
    milestone: two new, purely-additive Snapshot-capture steps
    ("Create immutable PRE_GAME_DECISION snapshot", "Commit snapshot
    artifacts") now run AFTER the stage-status step, so it is no longer
    literally the LAST step in the job -- but it remains the last
    PRODUCTION-artifact step, and still runs unconditionally so a
    stage-status artifact is always produced even if execution/logging
    steps failed. The snapshot steps that follow it never touch
    data/pipeline_status.json, bets.json, or data/slate.json (see
    docs/SNAPSHOT_ARCHITECTURE.md) -- this test's actual invariant (stage
    status reflects the full production run, unconditionally) is
    unaffected by their addition.
    """
    idx = _index_by_name_substring(steps, "Write pipeline stage-status")
    stage_status_step = steps[idx]
    assert stage_status_step.get("if") == "always()", (
        "the stage-status step must run unconditionally (if: always()) "
        "so a stage-status artifact is produced even if execution/logging "
        "steps failed"
    )
    assert "pipeline_status.json" in stage_status_step["run"]
    for step_id in OPTIONAL_STEP_IDS:
        opt_idx = _index_by_id(steps, step_id)
        assert opt_idx < idx, (
            f"step id={step_id!r} must run before the stage-status step"
        )


def test_snapshot_capture_steps_run_after_stage_status_and_are_non_fatal(steps):
    """
    The new Snapshot-capture steps must run strictly after the
    stage-status step (every production artifact they could reference
    already exists or has definitively failed to by then), and must never
    be able to fail the overall workflow -- continue-on-error: true, per
    docs/SNAPSHOT_ARCHITECTURE.md's explicit "safest behavior" decision.
    """
    stage_status_idx = _index_by_name_substring(steps, "Write pipeline stage-status")
    snapshot_idx = _index_by_name_substring(steps, "Create immutable PRE_GAME_DECISION snapshot")
    assert snapshot_idx > stage_status_idx
    assert steps[snapshot_idx].get("continue-on-error") is True
    assert steps[snapshot_idx].get("if") == "always()"


# ── Prerequisite-dependency conditions (pre-merge hardening pass) ────────────
#
# continue-on-error alone only stops a failure from failing the *job* — it
# does not stop GitHub Actions from still running the *next* step by default.
# Each optional step must therefore carry an explicit `if:` that checks the
# actual outcome of whatever it depends on, so a failed prerequisite is never
# silently followed by a step that assumes it succeeded.

EXPECTED_IF_CONDITIONS = {
    # risk_gate mutates data/slate.json written by publish_slate; no other
    # prerequisite in the optional chain. Also gated to non-schedule
    # events (Prospective/CLV Measurement Reliability mission): automated
    # bet placement must stay a workflow_dispatch/push-only action, never
    # triggered by the new scheduled slate-refresh cron.
    "risk_gate": "github.event_name != 'schedule' && steps.publish_slate.outcome == 'success'",
    # write_pending_bets reads data/slate.json AFTER risk_gate's in-place
    # mutation (TT downgrades) — must not run against a slate risk_gate
    # failed to produce. Also schedule-gated (see risk_gate above).
    "write_pending_bets": "github.event_name != 'schedule' && steps.risk_gate.outcome == 'success'",
    # validate_bet_logging compares bets.json against the ledger; bets.json
    # is only trustworthy once write_pending_bets has finished. Also
    # schedule-gated (see risk_gate above).
    "validate_bet_logging": "github.event_name != 'schedule' && steps.write_pending_bets.outcome == 'success'",
    # write_tracked_tickers registers CLV tracking for bets that were both
    # logged AND confirmed consistent with the ledger — requires both.
    # Also schedule-gated (see risk_gate above).
    "write_tracked_tickers": (
        "github.event_name != 'schedule' && "
        "steps.write_pending_bets.outcome == 'success' && "
        "steps.validate_bet_logging.outcome == 'success'"
    ),
    # capture_closing_lines (snapshot mode) reads only
    # data/kalshi_market_registry.json, built earlier in the required
    # section of the job — independent of the bet-logging chain. NOT
    # schedule-gated: it is read-only price capture for already-open bets,
    # never new bet placement, so it is safe (and useful) to keep running
    # on every trigger, including the new scheduled slate refresh.
    "capture_closing_lines": "steps.publish_slate.outcome == 'success'",
}


def test_optional_steps_have_the_expected_prerequisite_conditions(steps):
    for step_id, expected_if in EXPECTED_IF_CONDITIONS.items():
        idx = _index_by_id(steps, step_id)
        actual_if = steps[idx].get("if")
        assert actual_if == expected_if, (
            f"step id={step_id!r} if-condition mismatch.\n"
            f"  expected: {expected_if!r}\n"
            f"  actual:   {actual_if!r}"
        )


def test_write_pending_bets_does_not_run_unconditionally(steps):
    """
    Guards specifically against the reviewed gap: continue-on-error alone
    would let write_pending_bets run even after risk_gate failed. It must
    have a condition at all (not None), and that condition must reference
    risk_gate's outcome.
    """
    idx = _index_by_id(steps, "write_pending_bets")
    cond = steps[idx].get("if")
    assert cond is not None, "write_pending_bets must not run unconditionally"
    assert "risk_gate" in cond and "success" in cond


def test_validate_bet_logging_step_name_clarifies_scope(steps):
    """
    The step name must make clear this is a hard gate for the execution
    chain only, not for authoritative slate publication (which already
    completed in publish_slate, earlier in the job).
    """
    idx = _index_by_id(steps, "validate_bet_logging")
    name = steps[idx]["name"].lower()
    assert "execution" in name or "does not affect" in name or "not affect" in name, (
        f"validate_bet_logging step name must clarify it is scoped to the "
        f"execution chain, not slate publication. Got: {steps[idx]['name']!r}"
    )


# ── Prospective/CLV Measurement Reliability mission: automated scheduled
# refresh, upstream fix for the 2026-08-11..15 stale-slate gap ──────────

BET_PLACEMENT_STEP_IDS = ["risk_gate", "write_pending_bets", "validate_bet_logging", "write_tracked_tickers"]


@pytest.fixture(scope="module")
def workflow_on():
    with open(WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    # PyYAML parses the bare `on:` key as the boolean True in YAML 1.1.
    return data.get("on", data.get(True)) or {}


def test_schedule_trigger_present_with_three_daily_cron_entries(workflow_on):
    schedule = workflow_on.get("schedule")
    assert schedule is not None, "fetch-slate.yml must have a schedule: trigger so data/slate.json refreshes without manual dispatch"
    crons = [entry["cron"] for entry in schedule]
    assert len(crons) == 3, f"expected exactly 3 scheduled attempts per day (early/main/retry), got {crons}"
    # Every entry must be a genuine once-daily UTC hour (minute 0, single
    # hour, every day) -- never more frequent than this job's own real
    # single-success-per-day operating history warrants (see this file's
    # own network-cost rationale).
    for cron in crons:
        minute, hour, dom, month, dow = cron.split()
        assert minute == "0"
        assert dom == "*" and month == "*" and dow == "*"
        assert hour.isdigit(), f"cron hour field must be a single fixed hour, got {hour!r} in {cron!r}"


def test_push_and_workflow_dispatch_triggers_are_preserved(workflow_on):
    assert "push" in workflow_on
    assert workflow_on["push"]["paths"] == [".fetch-trigger"]
    assert "workflow_dispatch" in workflow_on
    assert "date" in workflow_on["workflow_dispatch"]["inputs"]


def test_bet_placement_steps_are_gated_off_on_schedule_events(steps):
    """
    Core safety invariant for this mission: a scheduled slate-refresh run
    must never place a bet on its own. Each of the four bet-placement
    steps' own `if:` must explicitly exclude the schedule event, in
    addition to (never instead of) its existing prerequisite condition.
    """
    for step_id in BET_PLACEMENT_STEP_IDS:
        idx = _index_by_id(steps, step_id)
        cond = steps[idx].get("if") or ""
        assert "github.event_name != 'schedule'" in cond, (
            f"step id={step_id!r} must exclude the schedule event from its "
            f"if: condition so automated bet placement never runs "
            f"unattended. Got: {cond!r}"
        )


def test_capture_closing_lines_is_not_schedule_gated(steps):
    """
    capture_closing_lines is read-only price capture for already-open
    bets, never new bet placement -- it must keep running on every
    trigger (including the new schedule event), unlike the four
    bet-placement steps above.
    """
    idx = _index_by_id(steps, "capture_closing_lines")
    cond = steps[idx].get("if") or ""
    assert "github.event_name" not in cond


def test_publish_slate_and_stage_status_are_not_schedule_gated(steps):
    """
    Slate fetch/publish (the actual goal of the new schedule trigger) and
    the always-run stage-status step must never themselves be
    schedule-gated -- only the bet-placement chain is.
    """
    for step_id in ("publish_slate",):
        idx = _index_by_id(steps, step_id)
        cond = steps[idx].get("if") or ""
        assert "github.event_name" not in cond
    stage_status_idx = _index_by_name_substring(steps, "Write pipeline stage-status")
    assert steps[stage_status_idx].get("if") == "always()"


class TestVercelFetchDiagnostics:
    """
    docs/PRODUCTION_INCIDENT_SLATE_FS_IMPORT.md: /api/slate started failing
    on every request while teamstats/pitchers/weather/bullpen kept
    succeeding, but `curl -sf` suppressed the HTTP status and response
    body on failure -- the workflow log showed only "ERROR: slate fetch
    failed", no signal pointing at the actual cause. These tests guard the
    fixed "Fetch non-odds data from Vercel" step's diagnostics.
    """

    def _fetch_step(self, steps):
        idx = _index_by_name_substring(steps, "Fetch non-odds data from Vercel")
        return steps[idx]

    def _fetch_step_code_only(self, steps):
        """
        The `run:` script with `#`-comment lines stripped, so pattern
        checks below scan actual shell code -- not this step's own
        explanatory comment, which necessarily quotes the OLD `curl -sf`
        pattern by name to explain why it was removed.
        """
        script = self._fetch_step(steps)["run"]
        return "\n".join(
            line for line in script.split("\n")
            if not line.strip().startswith("#")
        )

    def test_bare_fail_silent_curl_flag_is_gone(self, steps):
        """
        `curl -sf` (or `-fs`) makes curl discard the response body on a
        non-2xx status -- the exact suppression that hid the root cause.
        The fixed step must not use it for these requests.
        """
        script = self._fetch_step_code_only(steps)
        assert not re.search(r"curl\s+-sf\b", script)
        assert not re.search(r"curl\s+-fs\b", script)
        assert not re.search(r"curl\s+[^\n]*\s-f\b", script), (
            "a bare -f/--fail flag would still suppress the response body "
            "needed for diagnostics"
        )

    def test_reports_http_status_code(self, steps):
        script = self._fetch_step(steps)["run"]
        assert "%{http_code}" in script
        assert "HTTP status" in script or "HTTP $http_code" in script

    def test_distinguishes_transport_from_http_level_failure(self, steps):
        script = self._fetch_step(steps)["run"]
        assert "TRANSPORT_ERROR" in script
        assert "HTTP_ERROR" in script
        assert "curl_exit" in script

    def test_reports_requested_url_on_failure(self, steps):
        script = self._fetch_step(steps)["run"]
        # Both failure branches must echo the URL that was requested.
        assert script.count("URL: $url") >= 2

    def test_reports_truncated_response_body_on_http_failure(self, steps):
        script = self._fetch_step(steps)["run"]
        assert "Response body" in script
        assert re.search(r"head\s+-c\s+\d+", script), "body must be truncated, not dumped unbounded"

    def test_never_prints_request_headers_or_env_secrets(self, steps):
        """
        No secret is ever sent to these specific endpoints (no Authorization
        header, no API key in these query strings), but the diagnostics
        must still never echo request headers or any `secrets.`/env
        credential reference, as a durable safety property independent of
        today's endpoint list.
        """
        script = self._fetch_step(steps)["run"]
        assert "secrets." not in script
        # The two error branches (not the curl invocation line itself) must
        # never echo a header value.
        for line in script.split("\n"):
            if "echo" in line:
                assert "Authorization" not in line
                assert "Accept:" not in line
                assert "Cache-Control:" not in line

    def test_retry_flags_preserved(self, steps):
        """
        The diagnostics rewrite must not weaken retry behavior -- curl's
        --retry already retries transient/5xx statuses independent of -f,
        so removing -f loses nothing here.
        """
        script = self._fetch_step(steps)["run"]
        assert "--retry 3" in script
        assert "--retry-delay 5" in script

    def test_still_exits_nonzero_on_slate_failure(self, steps):
        """
        The workflow must still fail loudly if /api/slate (or any of the
        other fetched endpoints) is unusable -- diagnostics must not come
        at the cost of silently continuing on failure.
        """
        script = self._fetch_step(steps)["run"]
        assert "exit 1" in script
