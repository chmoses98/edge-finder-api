#!/usr/bin/env python3
"""
tests/edgelab/test_resolve_heartbeat_target_script.py
================================================================
Coverage for scripts/edgelab/resolve_heartbeat_target.py -- the
workflow-side half of the heartbeat's one date-resolution path
(Heartbeat False-Failure Incident, 2026-08-27).

All date SEMANTICS are tested in tests/edgelab/test_heartbeat_target_date.py
against the pure lib.edgelab.production_date. What is tested here is the
part that cannot be pure: which GitHub-provided timestamp is used as the
run's anchor, in which order, and what is recorded when a fallback is
taken. That precedence is the entire reason a re-run of an old scheduled
heartbeat still validates the original date.
"""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab import resolve_heartbeat_target as resolver

HEARTBEAT_CRON = "45 23 * * *"
RUN_CREATED_AT = "2026-08-27T05:06:16Z"      # real run 33041444748, delayed 5h21m
RERUN_STARTED_AT = "2026-08-28T14:00:00Z"    # what run_started_at becomes on a re-run


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _fake_api(created_at, *, capture=None):
    def opener(request, timeout=None):
        if capture is not None:
            capture.append(request.full_url)
        return _FakeResponse(json.dumps({"created_at": created_at, "run_started_at": RERUN_STARTED_AT}).encode())
    return opener


def _schedule_env(**overrides):
    env = {
        "GITHUB_EVENT_NAME": "schedule",
        "HEARTBEAT_SCHEDULE_EXPRESSION": HEARTBEAT_CRON,
        "HEARTBEAT_RUN_STARTED_AT": RERUN_STARTED_AT,
        "GITHUB_REPOSITORY": "chmoses98/edge-finder-api",
        "GITHUB_RUN_ID": "33041444748",
        "GITHUB_TOKEN": "t0ken",
    }
    env.update(overrides)
    return env


class TestAnchorPrecedence:
    def test_run_created_at_is_preferred_over_the_rerun_reset_run_started_at(self):
        """The re-run case (requirement 5): run_started_at has reset to two days later,
        but created_at still identifies the run, so the original date is preserved."""
        calls = []
        record = resolver.resolve([], _schedule_env(), opener=_fake_api(RUN_CREATED_AT, capture=calls))
        assert record["targetDate"] == "2026-08-26"
        assert record["anchorTimestamp"] == RUN_CREATED_AT
        assert record["anchorSource"] == resolver.ANCHOR_SOURCE_REST_CREATED_AT
        assert calls == ["https://api.github.com/repos/chmoses98/edge-finder-api/actions/runs/33041444748"]

    def test_falls_back_to_run_started_at_when_the_api_is_unavailable_and_says_so(self):
        def failing(request, timeout=None):
            raise urllib.error.URLError("no network")
        record = resolver.resolve([], _schedule_env(HEARTBEAT_RUN_STARTED_AT=RUN_CREATED_AT), opener=failing)
        assert record["targetDate"] == "2026-08-26"
        assert record["anchorSource"] == resolver.ANCHOR_SOURCE_RUN_STARTED_AT, (
            "an artifact produced from the re-run-resettable anchor must be identifiable as such"
        )

    def test_an_explicit_anchor_argument_wins_over_everything(self):
        record = resolver.resolve(["--anchor", "2026-08-26T23:45:00Z"], _schedule_env(),
                                  opener=_fake_api("2027-01-01T00:00:00Z"))
        assert record["targetDate"] == "2026-08-26"
        assert record["anchorSource"] == resolver.ANCHOR_SOURCE_EXPLICIT

    def test_a_malformed_api_payload_degrades_to_the_next_anchor_never_to_a_wrong_date(self):
        def garbage(request, timeout=None):
            return _FakeResponse(b"<html>502</html>")
        record = resolver.resolve([], _schedule_env(HEARTBEAT_RUN_STARTED_AT=RUN_CREATED_AT), opener=garbage)
        assert record["targetDate"] == "2026-08-26"
        assert record["anchorSource"] == resolver.ANCHOR_SOURCE_RUN_STARTED_AT

    def test_no_api_call_is_made_for_a_manual_dispatch(self):
        calls = []
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "HEARTBEAT_DISPATCH_DATE": "2026-08-11"}
        record = resolver.resolve([], env, opener=_fake_api(RUN_CREATED_AT, capture=calls))
        assert record["targetDate"] == "2026-08-11"
        assert calls == []


class TestScheduleExpressionSourcing:
    def test_the_cron_is_read_from_the_github_event_payload_when_not_passed_directly(self, tmp_path):
        payload = tmp_path / "event.json"
        payload.write_text(json.dumps({"schedule": HEARTBEAT_CRON}))
        env = _schedule_env(GITHUB_EVENT_PATH=str(payload))
        env.pop("HEARTBEAT_SCHEDULE_EXPRESSION")
        record = resolver.resolve([], env, opener=_fake_api(RUN_CREATED_AT))
        assert record["scheduleExpression"] == HEARTBEAT_CRON
        assert record["targetDate"] == "2026-08-26"

    def test_a_schedule_run_with_no_discoverable_cron_exits_non_zero_rather_than_guessing(self, capsys):
        env = _schedule_env()
        env.pop("HEARTBEAT_SCHEDULE_EXPRESSION")
        exit_code = resolver.main([], env, opener=_fake_api(RUN_CREATED_AT))
        assert exit_code == 2
        assert "FATAL" in capsys.readouterr().err


class TestOutputsForTheWorkflow:
    def test_writes_the_resolution_json_and_the_step_outputs(self, tmp_path):
        out = tmp_path / "target.json"
        gh_output = tmp_path / "github_output"
        gh_output.write_text("")
        env = _schedule_env(GITHUB_OUTPUT=str(gh_output))
        exit_code = resolver.main(["--out", str(out), "--github-output"], env, opener=_fake_api(RUN_CREATED_AT))
        assert exit_code == 0

        record = json.loads(out.read_text())
        assert record["targetDate"] == "2026-08-26"
        assert record["settlementDate"] == "2026-08-25"
        assert record["scheduledCheckpointUtc"] == "2026-08-26T23:45:00Z"
        assert record["delayedRun"] is True

        outputs = dict(line.split("=", 1) for line in gh_output.read_text().strip().splitlines())
        assert outputs["target_date"] == "2026-08-26"
        assert outputs["settlement_date"] == "2026-08-25"
        assert outputs["trigger_type"] == "schedule"

    def test_the_resolver_never_reads_repository_data_to_choose_a_date(self, tmp_path, monkeypatch):
        """Requirement 14: an empty working tree and one stuffed with the adjacent date's
        artifacts must produce the same answer."""
        monkeypatch.chdir(tmp_path)
        first = resolver.resolve([], _schedule_env(), opener=_fake_api(RUN_CREATED_AT))
        (tmp_path / "data" / "edgelab" / "health").mkdir(parents=True)
        (tmp_path / "data" / "edgelab" / "health" / "2026-08-27.json").write_text("{}")
        second = resolver.resolve([], _schedule_env(), opener=_fake_api(RUN_CREATED_AT))
        assert first["targetDate"] == second["targetDate"] == "2026-08-26"
