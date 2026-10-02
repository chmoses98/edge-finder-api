"""scripts/ci/offday_guard.py + its wiring in discover-kalshi-mlb-markets.yml."""
import importlib.util
import json
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("offday_guard", os.path.join(ROOT, "scripts", "ci", "offday_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

D = "2026-09-28"


def _status(**over):
    s = {"status": "NO_GAMES_SCHEDULED", "requestedDate": D,
         "scheduleEvidence": {"date": D, "status": "NO_GAMES_SCHEDULED", "scheduledGames": 0}}
    s.update(over)
    return s


def test_verified_off_day():
    assert guard.is_verified_off_day(_status(), D) is True


def test_other_date_status_or_missing_evidence_is_not_an_off_day():
    assert guard.is_verified_off_day(_status(), "2026-09-29") is False
    assert guard.is_verified_off_day(_status(status="FAILED_STALE_DATE"), D) is False
    assert guard.is_verified_off_day(_status(status="OK"), D) is False
    assert guard.is_verified_off_day(_status(scheduleEvidence=None), D) is False
    assert guard.is_verified_off_day(_status(scheduleEvidence={"date": D, "status": "SCHEDULE_UNKNOWN"}), D) is False
    assert guard.is_verified_off_day(_status(scheduleEvidence={"date": "2026-09-27", "status": "NO_GAMES_SCHEDULED"}), D) is False
    assert guard.is_verified_off_day(None, D) is False
    assert guard.is_verified_off_day([], D) is False


def test_cli_writes_output_and_never_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert guard.main([D]) == 0  # no fetch_status.json at all -> runs as before
    assert out.read_text() == "off_day=false\n"
    os.makedirs("data")
    (tmp_path / "data" / "fetch_status.json").write_text(json.dumps(_status()))
    out.write_text("")
    assert guard.main([D]) == 0
    assert out.read_text() == "off_day=true\n"
    (tmp_path / "data" / "fetch_status.json").write_text("{not json")
    out.write_text("")
    assert guard.main([D]) == 0
    assert out.read_text() == "off_day=false\n"


def test_workflow_gates_every_work_step_on_the_guard_for_workflow_run_only():
    with open(os.path.join(ROOT, ".github", "workflows", "discover-kalshi-mlb-markets.yml")) as f:
        wf = yaml.safe_load(f)
    steps = next(iter(wf["jobs"].values()))["steps"]
    names = [s.get("name", "") for s in steps]
    g = names.index("Off-day guard (schedule-verified, workflow_run only)")
    assert steps[g]["if"] == "github.event_name == 'workflow_run'"
    for s in steps[g + 1:]:
        assert s.get("if") == "steps.offday.outputs.off_day != 'true'", s.get("name")
