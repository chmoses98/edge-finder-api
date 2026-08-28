#!/usr/bin/env python3
"""
tests/edgelab/test_heartbeat_target_date.py
================================================================
Regression coverage for lib.edgelab.production_date -- the single
authoritative answer to "which production date is this heartbeat run
supposed to validate?" (Heartbeat False-Failure Incident, 2026-08-27).

The incident, as the fixtures below reproduce it: the EdgeLab Daily
Pipeline Heartbeat is scheduled at 23:45 UTC, GitHub actually started
the 2026-08-26 checkpoint's run at 2026-08-27T05:06:16Z, and the health
script -- which took its date from the wall clock at process start --
validated 2026-08-27 with settlementDateChecked=2026-08-26, hours
before Aug 27's own slate cycle had begun. Every MISSING_* reason it
reported was a false positive of that one substitution.

THE invariant these tests exist to protect:
    DELAY MUST NOT CHANGE THE DATE BEING VALIDATED.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import production_date as pd
from lib.edgelab.production_date import TargetDateError

HEARTBEAT_CRON = "45 23 * * *"

# The real incident, from the GitHub Actions API (run 33041444748):
INTENDED_CHECKPOINT = "2026-08-26T23:45:00Z"
ACTUAL_DELAYED_START = "2026-08-27T05:06:16Z"
INCIDENT_TARGET_DATE = "2026-08-26"
INCIDENT_SETTLEMENT_DATE = "2026-08-25"


def _schedule(anchor, cron=HEARTBEAT_CRON, **kwargs):
    return pd.resolve_target_date(event_name="schedule", schedule_expression=cron, anchor=anchor, **kwargs)


class TestScheduledOnTime:
    """Requirement 1: an on-time run validates its intended production date."""

    def test_on_time_2345z_run_validates_that_days_production_date(self):
        record = _schedule("2026-08-26T23:45:02Z")
        assert record["targetDate"] == INCIDENT_TARGET_DATE
        assert record["settlementDate"] == INCIDENT_SETTLEMENT_DATE
        assert record["scheduledCheckpointUtc"] == INTENDED_CHECKPOINT
        assert record["delayedRun"] is False

    def test_trigger_type_and_schedule_expression_are_recorded_for_audit(self):
        record = _schedule("2026-08-26T23:45:02Z")
        assert record["triggerType"] == pd.TRIGGER_SCHEDULE
        assert record["scheduleExpression"] == HEARTBEAT_CRON
        assert record["productionDateTimezone"] == "America/New_York"


class TestScheduledDelayed:
    """Requirements 2/3/4: delay -- of any size, across any midnight -- never moves the date."""

    def test_the_actual_incident_run_resolves_to_the_intended_date_not_its_start_date(self):
        record = _schedule(ACTUAL_DELAYED_START)
        assert record["targetDate"] == INCIDENT_TARGET_DATE, (
            "a run delayed past midnight UTC must still validate the production date of its "
            "intended 23:45 UTC checkpoint -- resolving 2026-08-27 here IS the incident"
        )
        assert record["settlementDate"] == INCIDENT_SETTLEMENT_DATE
        assert record["scheduledCheckpointUtc"] == INTENDED_CHECKPOINT
        assert record["delayedRun"] is True
        assert record["delaySeconds"] == 19276

    @pytest.mark.parametrize("actual_start", [
        "2026-08-27T00:00:30Z",   # 15 minutes late -- crosses UTC midnight only
        "2026-08-27T03:59:59Z",   # still the same ET evening (23:59 ET Aug 26)
        "2026-08-27T04:00:01Z",   # crosses America/New_York midnight too
        "2026-08-27T05:06:16Z",   # the observed incident
        "2026-08-27T12:00:00Z",   # 12h late, well into the next production day
        "2026-08-27T23:44:59Z",   # one minute before the NEXT checkpoint
    ])
    def test_every_delay_short_of_the_next_checkpoint_keeps_the_same_target(self, actual_start):
        assert _schedule(actual_start)["targetDate"] == INCIDENT_TARGET_DATE

    def test_the_next_checkpoint_is_a_different_run_and_a_different_date(self):
        """The floor is the run's OWN checkpoint: once 23:45Z Aug 27 has passed, that is a
        different scheduled run, and its (possibly also delayed) run validates Aug 27."""
        assert _schedule("2026-08-27T23:45:00Z")["targetDate"] == "2026-08-27"

    def test_delay_metadata_never_changes_the_answer_only_describes_it(self):
        on_time = _schedule("2026-08-26T23:45:00Z")
        delayed = _schedule(ACTUAL_DELAYED_START)
        assert on_time["targetDate"] == delayed["targetDate"]
        assert on_time["scheduledCheckpointUtc"] == delayed["scheduledCheckpointUtc"]
        assert (on_time["delayedRun"], delayed["delayedRun"]) == (False, True)


class TestScheduledRerun:
    """Requirement 5: re-running an old scheduled run still targets that run's own date.

    GitHub does not create a new run for a re-run -- the run id and its
    `created_at` survive, only `run_attempt` increments and
    `run_started_at` resets. So anchoring on `created_at` (what
    scripts/edgelab/resolve_heartbeat_target.py prefers) makes a re-run
    days later resolve exactly as attempt 1 did.
    """

    def test_rerun_days_later_anchored_on_run_created_at_targets_the_original_date(self):
        attempt_1 = _schedule(ACTUAL_DELAYED_START, anchor_source="github_rest_run_created_at")
        # Clicked "Re-run all jobs" on 2026-08-28; created_at is unchanged.
        attempt_2 = _schedule(ACTUAL_DELAYED_START, anchor_source="github_rest_run_created_at")
        assert attempt_2["targetDate"] == attempt_1["targetDate"] == INCIDENT_TARGET_DATE

    def test_a_rerun_anchored_on_the_reset_run_started_at_is_the_documented_limitation(self):
        """If created_at is ever unavailable and the resolver falls back to
        github.run_started_at (which RESETS on re-run), a re-run more than one cron
        period late resolves to the re-run's own checkpoint. The fallback is recorded in
        `anchorSource` precisely so such an artifact is identifiable rather than silent."""
        fallback = _schedule("2026-08-28T14:00:00Z", anchor_source=pd.TRIGGER_SCHEDULE)
        assert fallback["targetDate"] == "2026-08-27"
        assert fallback["anchorSource"] is not None


class TestManualDispatch:
    """Requirements 6/7: explicit date wins outright; a blank date means today ET."""

    def test_explicit_dispatch_date_is_authoritative_even_on_a_schedule_shaped_call(self):
        record = pd.resolve_target_date(
            event_name="workflow_dispatch", dispatch_date="2026-08-11",
            schedule_expression=HEARTBEAT_CRON, anchor=ACTUAL_DELAYED_START,
        )
        assert record["targetDate"] == "2026-08-11"
        assert record["settlementDate"] == "2026-08-10"
        assert record["triggerType"] == pd.TRIGGER_DISPATCH_EXPLICIT

    def test_blank_dispatch_date_uses_the_current_eastern_production_date(self):
        # 2026-08-27T02:00Z is 22:00 ET on 2026-08-26: the documented manual
        # convention is the CURRENT America/New_York production date, which is
        # what an operator pressing "Run workflow" at 10pm ET means -- not the
        # UTC date, which has already rolled over.
        record = pd.resolve_target_date(
            event_name="workflow_dispatch", dispatch_date="", now="2026-08-27T02:00:00Z",
        )
        assert record["targetDate"] == "2026-08-26"
        assert record["triggerType"] == pd.TRIGGER_DISPATCH_CURRENT_DAY

    def test_blank_dispatch_date_after_eastern_midnight_is_the_new_production_date(self):
        record = pd.resolve_target_date(
            event_name="workflow_dispatch", dispatch_date="", now="2026-08-27T05:06:16Z",
        )
        assert record["targetDate"] == "2026-08-27"   # 01:06 ET

    def test_manual_dispatch_never_inherits_scheduled_checkpoint_semantics(self):
        record = pd.resolve_target_date(
            event_name="workflow_dispatch", dispatch_date=None,
            schedule_expression=HEARTBEAT_CRON, now="2026-08-27T18:00:00Z",
        )
        assert record["targetDate"] == "2026-08-27"
        assert record["scheduledCheckpointUtc"] is None

    @pytest.mark.parametrize("bad", ["2026-8-1", "08/26/2026", "2026-08-26T00:00:00Z", "2026-02-30", "yesterday", "", "  "])
    def test_a_malformed_explicit_date_is_rejected_never_coerced(self, bad):
        """Requirement 15: the date reaching the health check is the date its artifact is
        filed under, so it is validated strictly as YYYY-MM-DD (real calendar dates only)."""
        if bad.strip():
            with pytest.raises(TargetDateError):
                pd.resolve_target_date(event_name="workflow_dispatch", dispatch_date=bad)
        else:
            # A blank input is "no date given", not a malformed one.
            assert pd.resolve_target_date(event_name="workflow_dispatch", dispatch_date=bad,
                                          now="2026-08-27T18:00:00Z")["targetDate"] == "2026-08-27"


class TestDaylightSavingTime:
    """Requirements 8/9: ET conversion is zoneinfo-based, never fixed UTC-4/-5 arithmetic."""

    def test_spring_forward_day_23_45z_checkpoint_is_that_ET_date(self):
        # 2026-03-08 is the US spring-forward date; 23:45 UTC is 19:45 EDT (UTC-4).
        assert _schedule("2026-03-08T23:45:00Z")["targetDate"] == "2026-03-08"

    def test_spring_forward_delayed_run_keeps_the_pre_transition_target(self):
        # Started 2026-03-09T09:00Z, after the transition; the checkpoint is unchanged.
        assert _schedule("2026-03-09T09:00:00Z")["targetDate"] == "2026-03-08"

    def test_fall_back_day_23_45z_checkpoint_is_that_ET_date(self):
        # 2026-11-01 is the US fall-back date; 23:45 UTC is 18:45 EST (UTC-5).
        assert _schedule("2026-11-01T23:45:00Z")["targetDate"] == "2026-11-01"

    def test_an_03_00z_checkpoint_belongs_to_the_PREVIOUS_eastern_date_in_both_offsets(self):
        """A fixed-offset implementation gets one of these two wrong; zoneinfo gets both right."""
        assert _schedule("2026-07-15T03:00:00Z", cron="0 3 * * *")["targetDate"] == "2026-07-14"  # EDT, 23:00
        assert _schedule("2026-12-15T03:00:00Z", cron="0 3 * * *")["targetDate"] == "2026-12-14"  # EST, 22:00

    def test_et_date_for_timestamp_is_dst_aware_for_stored_captured_at_values(self):
        assert pd.et_date_for_timestamp("2026-08-27T02:18:17Z") == "2026-08-26"   # 22:18 EDT
        assert pd.et_date_for_timestamp("2026-08-27T04:00:00Z") == "2026-08-27"   # 00:00 EDT
        assert pd.et_date_for_timestamp("2026-12-27T04:30:00Z") == "2026-12-26"   # 23:30 EST
        assert pd.et_date_for_timestamp("not a timestamp") is None


class TestSettlementDate:
    """Requirement: settlement date stays target-1, and follows the REPAIRED target."""

    def test_settlement_date_follows_the_intended_target_not_the_start_date(self):
        assert _schedule(ACTUAL_DELAYED_START)["settlementDate"] == INCIDENT_SETTLEMENT_DATE

    def test_settlement_date_is_calendar_arithmetic_across_month_and_dst_boundaries(self):
        assert pd.previous_date("2026-09-01") == "2026-08-31"
        assert pd.previous_date("2026-03-09") == "2026-03-08"   # spring forward
        assert pd.previous_date("2026-11-02") == "2026-11-01"   # fall back
        assert pd.previous_date("2028-03-01") == "2028-02-29"   # leap year

    def test_explicit_manual_date_settlement_is_predictable(self):
        record = pd.resolve_target_date(event_name="workflow_dispatch", dispatch_date="2026-08-26")
        assert (record["targetDate"], record["settlementDate"]) == ("2026-08-26", "2026-08-25")


class TestNoGuessingAndNoDataPeeking:
    """Requirement 14: the date can never come from a clock cutoff or from which date has data."""

    def test_a_schedule_run_without_its_cron_expression_fails_loudly_rather_than_guessing(self):
        with pytest.raises(TargetDateError):
            pd.resolve_target_date(event_name="schedule", anchor=ACTUAL_DELAYED_START)

    def test_a_schedule_run_without_an_anchor_fails_loudly(self):
        with pytest.raises(TargetDateError):
            pd.resolve_target_date(event_name="schedule", schedule_expression=HEARTBEAT_CRON)

    def test_resolution_is_identical_regardless_of_what_exists_on_disk(self, tmp_path, monkeypatch):
        """Same inputs, two completely different working trees (one empty, one full of
        artifacts for the adjacent date) -- the answer must not move."""
        monkeypatch.chdir(tmp_path)
        empty_tree = _schedule(ACTUAL_DELAYED_START)
        (tmp_path / "data" / "pipeline" / "2026-08-27").mkdir(parents=True)
        (tmp_path / "data" / "pipeline" / "2026-08-27" / "recommendations.json").write_text("{}")
        populated_tree = _schedule(ACTUAL_DELAYED_START)
        assert empty_tree["targetDate"] == populated_tree["targetDate"] == INCIDENT_TARGET_DATE

    def test_the_resolver_module_imports_nothing_that_can_read_repository_data(self):
        source = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "lib", "edgelab", "production_date.py",
        )).read()
        for forbidden in ("import os", "open(", "storage", "urllib", "requests", "subprocess"):
            assert forbidden not in source, (
                f"lib/edgelab/production_date.py must stay pure -- {forbidden!r} would let a "
                "date depend on repository data or the network"
            )


class TestCronEvaluation:
    """The workflow's own cron literal is the source of truth -- no second scheduler here."""

    def test_no_cron_literal_is_baked_into_the_resolver_only_documented_in_prose(self):
        """Changing the workflow's schedule changes the semantics with it: neither this
        workflow's 23:45 nor any fetch-slate cron time exists as executable data here, so
        there is no second scheduler implementation that can drift out of sync."""
        import ast
        import re as _re
        source = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "lib", "edgelab", "production_date.py",
        )).read()
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    docstrings.add(id(first.value))
        cron_like = _re.compile(r"^[\d*/,\- ]+$")
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert not (len(node.value.split()) == 5 and cron_like.match(node.value)), (
                    f"cron literal {node.value!r} must not live in the resolver -- it belongs to "
                    "the workflow's own `schedule:` block and reaches here via github.event.schedule"
                )

    @pytest.mark.parametrize("cron,anchor,expected", [
        ("45 23 * * *", "2026-08-27T05:06:16Z", "2026-08-26T23:45:00Z"),
        ("0 */6 * * *", "2026-08-27T05:06:16Z", "2026-08-27T00:00:00Z"),
        ("30 12 * * 1-5", "2026-08-30T09:00:00Z", "2026-08-28T12:30:00Z"),   # Sun -> previous Friday
        ("0 0 1 * *", "2026-08-27T05:06:16Z", "2026-08-01T00:00:00Z"),
        ("15,45 * * * *", "2026-08-27T05:06:16Z", "2026-08-27T04:45:00Z"),
    ])
    def test_latest_occurrence_at_or_before(self, cron, anchor, expected):
        assert pd.to_utc_iso(pd.latest_cron_occurrence_at_or_before(cron, anchor)) == expected

    def test_an_occurrence_exactly_at_the_anchor_counts_as_at_or_before(self):
        assert pd.to_utc_iso(pd.latest_cron_occurrence_at_or_before("45 23 * * *", "2026-08-26T23:45:00Z")) \
            == "2026-08-26T23:45:00Z"

    @pytest.mark.parametrize("bad", ["", "45 23 * *", "not a cron", "45 23 * * MON", "99 23 * * *", "45 23 * * */0"])
    def test_an_unparseable_or_unsupported_cron_fails_loudly(self, bad):
        with pytest.raises(TargetDateError):
            pd.latest_cron_occurrence_at_or_before(bad, "2026-08-27T05:06:16Z")
