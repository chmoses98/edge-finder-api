"""
Fetch Slate Data: the slate-day gate (verified off-days conclude SUCCESS,
non-final attempts defer instead of going red, the dead `pre_exit` capture).

Deterministic: no network (schedule evidence is injected or the conftest
`EDGEFINDER_SCHEDULE_EVIDENCE=offline` seam is used), no wall clock, no
live committed data. Shell-level tests run the workflow's own `run:` bodies
under `bash -e` -- GitHub's default shell -- against stub scripts.

Regression anchors:
  * 2026-09-28 off-day: runs 36486867929 / 36502136929 / 36507307766 went
    red although the MLB schedule had no games that day.
  * `bash -e` aborted the old pre-validate step before `PRE_STATUS=$?`, so
    `pre_exit` was only ever written for 0.
"""

import json
import os
import shutil
import stat
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "ci"))

import validate_slate_pre as vsp  # noqa: E402
import slate_day_gate as gate  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")
DATE = "2026-09-28"


# ── fixtures ──────────────────────────────────────────────────────────────

def _game(away="NYY", home="BOS", away_p="A Pitcher", home_p="B Pitcher"):
    return {"away": {"abbr": away, "pitcher": {"name": away_p} if away_p else None},
            "home": {"abbr": home, "pitcher": {"name": home_p} if home_p else None}}


def _schedule(date, games):
    """An MLB Stats API schedule response for one date."""
    return {"dates": [{"date": date, "games": games}] if games else []}


def _sched_game(game_type="R", state="Scheduled"):
    return {"gameType": game_type, "status": {"detailedState": state}}


def _run(tmp_path, slate, loader, argv=(DATE,), monkeypatch=None):
    slate_path = tmp_path / "slate.json"
    status_path = tmp_path / "fetch_status.json"
    slate_path.write_text(json.dumps(slate))
    with pytest.raises(SystemExit) as exc:
        vsp.main(argv=list(argv), slate_path=str(slate_path),
                 fetch_status_path=str(status_path), schedule_loader=loader)
    status = json.loads(status_path.read_text()) if status_path.exists() else None
    return exc.value.code, status, status_path


def _loader_returning(response):
    calls = []

    def loader(date):
        calls.append(date)
        return response
    loader.calls = calls
    return loader


def _loader_must_not_be_called(date):
    raise AssertionError("schedule must not be consulted for this slate shape")


@pytest.fixture(autouse=True)
def _no_github_files(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


# ── validate_slate_pre: the off-day contract on the empty-slate path ──────

def test_verified_off_day_exits_3_and_records_no_games_scheduled(tmp_path):
    loader = _loader_returning(_schedule(DATE, []))
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []}, loader)
    assert code == vsp.EXIT_NO_GAMES_SCHEDULED == 3
    assert loader.calls == [DATE]
    assert status["status"] == "NO_GAMES_SCHEDULED"
    assert status["requestedDate"] == DATE
    assert status["scheduleEvidence"]["status"] == "NO_GAMES_SCHEDULED"
    assert status["scheduleEvidence"]["date"] == DATE


def test_every_game_postponed_is_a_verified_off_day(tmp_path):
    resp = _schedule(DATE, [_sched_game(state="Postponed"), _sched_game(state="Cancelled")])
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []}, _loader_returning(resp))
    assert code == 3
    assert status["status"] == "NO_GAMES_SCHEDULED"


def test_off_day_record_is_idempotent_across_attempts(tmp_path):
    """2nd/3rd scheduled attempts of an off-day must not rewrite the file
    (no timestamp-only commit three times a day all offseason)."""
    loader = _loader_returning(_schedule(DATE, []))
    _, _, path = _run(tmp_path, {"date": DATE, "games": []}, loader)
    first = path.read_bytes()
    _run(tmp_path, {"date": DATE, "games": []}, loader)
    assert path.read_bytes() == first


def test_schedule_unknown_is_never_an_off_day(tmp_path):
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []}, _loader_returning(None))
    assert code == vsp.EXIT_HARD_FAIL == 1
    assert status["status"] == "FAILED_STALE_DATE"
    assert status["scheduleEvidence"]["status"] == "SCHEDULE_UNKNOWN"


def test_malformed_schedule_is_never_an_off_day(tmp_path):
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []},
                           _loader_returning({"totalGames": 0}))
    assert code == 1
    assert status["scheduleEvidence"]["status"] == "SCHEDULE_UNKNOWN"


def test_default_loader_uses_the_offline_test_seam_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGEFINDER_SCHEDULE_EVIDENCE", "offline")
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []}, None)
    assert code == 1
    assert status["scheduleEvidence"]["status"] == "SCHEDULE_UNKNOWN"


@pytest.mark.parametrize("game_type", ["R", "F", "D", "L", "W"])
def test_games_scheduled_but_slate_empty_is_a_collection_failure(tmp_path, game_type):
    resp = _schedule(DATE, [_sched_game(game_type=game_type)])
    code, status, _ = _run(tmp_path, {"date": DATE, "games": []}, _loader_returning(resp))
    assert code == 1
    assert status["status"] == "FAILED_STALE_DATE"
    assert "the MLB schedule has 1 playable game" in status["reason"]


def test_wrong_dated_empty_slate_stays_a_hard_fail_without_schedule_lookup(tmp_path):
    code, status, _ = _run(tmp_path, {"date": "2026-09-27", "games": []},
                           _loader_must_not_be_called)
    assert code == 1
    assert status is None, "a stale slate must not write an off-day verdict"


def test_slate_without_games_list_stays_a_hard_fail_without_schedule_lookup(tmp_path):
    code, status, _ = _run(tmp_path, {"date": DATE}, _loader_must_not_be_called)
    assert code == 1
    assert status is None


def test_tbd_starters_still_exit_2_and_never_consult_the_schedule(tmp_path):
    slate = {"date": DATE, "games": [_game(away_p=None)]}
    code, status, _ = _run(tmp_path, slate, _loader_must_not_be_called)
    assert code == vsp.EXIT_NOT_READY == 2
    assert status is None


def test_ready_slate_exits_0(tmp_path):
    code, status, _ = _run(tmp_path, {"date": DATE, "games": [_game()]},
                           _loader_must_not_be_called)
    assert code == 0
    assert status is None


# ── slate_day_gate.decide: the decision table ────────────────────────────

FIRST = "0 16 * * *"
MIDDLE = "0 20 * * *"
FINAL = gate.FINAL_CRON


@pytest.mark.parametrize("event,cron,final", [
    ("schedule", FIRST, False),
    ("schedule", MIDDLE, False),
    ("schedule", FINAL, True),
    ("schedule", "", False),
    ("workflow_dispatch", "", True),
    ("push", "", True),
])
def test_final_attempt_is_decided_by_event_and_cron_never_the_clock(event, cron, final):
    assert gate.is_final_attempt(event, cron) is final


@pytest.mark.parametrize("event,cron", [("schedule", FIRST), ("schedule", FINAL),
                                        ("workflow_dispatch", ""), ("push", "")])
def test_verified_off_day_never_proceeds_and_is_not_applicable(event, cron):
    d = gate.decide(event, cron, True, 3)
    assert (d["verdict"], d["proceed"], d["health"]) == ("OFF_DAY", False, "NOT_APPLICABLE")
    assert d["warnings"] == []


@pytest.mark.parametrize("event,cron", [("schedule", FIRST), ("schedule", FINAL),
                                        ("workflow_dispatch", "")])
def test_game_day_always_proceeds(event, cron):
    d = gate.decide(event, cron, True, 0)
    assert (d["verdict"], d["proceed"], d["health"]) == ("GAME_DAY", True, "HEALTHY")


@pytest.mark.parametrize("event,cron", [("schedule", FIRST), ("schedule", FINAL),
                                        ("workflow_dispatch", "")])
def test_tbd_starters_proceed_with_a_warning_at_every_attempt(event, cron):
    d = gate.decide(event, cron, True, 2)
    assert d["proceed"] is True
    assert d["verdict"] == "GAME_DAY"
    assert d["warnings"] and "TBD" in d["warnings"][0]


@pytest.mark.parametrize("http_ok,pre_exit,verdict", [(True, 1, "NOT_VERIFIED"),
                                                     (False, None, "SLATE_FETCH_FAILED")])
def test_unverifiable_slate_defers_on_non_final_scheduled_attempts(http_ok, pre_exit, verdict):
    for cron in (FIRST, MIDDLE):
        d = gate.decide("schedule", cron, http_ok, pre_exit)
        assert (d["verdict"], d["proceed"], d["health"]) == (verdict, False, "DEGRADED")
        assert d["warnings"], "a deferral must be visible as a warning"


@pytest.mark.parametrize("http_ok,pre_exit,verdict", [(True, 1, "NOT_VERIFIED"),
                                                     (False, None, "SLATE_FETCH_FAILED")])
@pytest.mark.parametrize("event,cron", [("schedule", FINAL), ("workflow_dispatch", ""), ("push", "")])
def test_unverifiable_slate_runs_the_fetch_job_on_the_final_attempt(http_ok, pre_exit, verdict,
                                                                   event, cron):
    d = gate.decide(event, cron, http_ok, pre_exit)
    assert (d["verdict"], d["proceed"], d["health"]) == (verdict, True, "FAILING")


@pytest.mark.parametrize("bad", [4, 127, -1, None])
def test_unexpected_validator_result_is_refused(bad):
    with pytest.raises(ValueError):
        gate.decide("schedule", FIRST, True, bad)


def test_cli_writes_outputs_and_summary(tmp_path, monkeypatch, capsys):
    out, summ = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summ))
    rc = gate.main(["--date", DATE, "--event", "schedule", "--cron", FIRST,
                    "--slate-http-ok", "true", "--pre-exit", "1"])
    assert rc == 0
    assert "verdict=NOT_VERIFIED\nproceed=false\nhealth=DEGRADED\nfinal=false\n" == out.read_text()
    assert "NOT_VERIFIED (DEGRADED)" in summ.read_text()
    assert "::warning title=Fetch slate %s::" % DATE in capsys.readouterr().out


def test_cli_refuses_unexpected_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    assert gate.main(["--date", DATE, "--event", "schedule", "--slate-http-ok", "true",
                      "--pre-exit", "9"]) == 2
    assert not (tmp_path / "out").exists()


# ── workflow structure ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def wf():
    with open(WORKFLOW) as f:
        return yaml.safe_load(f)


def _step(job, step_id):
    return next(s for s in job["steps"] if s.get("id") == step_id)


def test_final_cron_is_the_last_scheduled_cron(wf):
    crons = [c["cron"] for c in (wf.get(True) or wf["on"])["schedule"]]
    assert crons[-1] == gate.FINAL_CRON
    assert sorted(crons, key=lambda c: int(c.split()[1]))[-1] == gate.FINAL_CRON


def test_refresh_and_fetch_are_skipped_unless_the_gate_says_proceed(wf):
    jobs = wf["jobs"]
    assert list(jobs)[0] == "slate_day"
    assert jobs["refresh_bankroll"]["needs"] == "slate_day"
    assert jobs["refresh_bankroll"]["if"] == "needs.slate_day.outputs.proceed == 'true'"
    assert set(jobs["fetch"]["needs"]) == {"slate_day", "refresh_bankroll"}
    assert "needs.slate_day.outputs.proceed == 'true'" in jobs["fetch"]["if"]
    assert "!cancelled()" in jobs["fetch"]["if"]
    assert set(jobs["slate_day"]["outputs"]) == {"date", "verdict", "proceed"}


def test_fetch_job_uses_the_validated_date_from_slate_day(wf):
    run = _step(wf["jobs"]["fetch"], "set_date")["run"]
    assert "needs.slate_day.outputs.date" in run
    assert "github.event.inputs.date" not in run


def test_slate_day_commits_only_fetch_status_and_only_when_it_stops_the_run(wf):
    job = wf["jobs"]["slate_day"]
    commits = [s for s in job["steps"] if "git_data_commit.py" in (s.get("run") or "")]
    assert len(commits) == 1
    assert commits[0]["if"] == "steps.gate.outputs.proceed == 'false'"
    assert commits[0]["run"].rstrip().endswith("data/fetch_status.json")
    assert job["permissions"] == {"contents": "write"}


def test_pre_validate_step_captures_exit_code_and_is_the_single_enforcement(wf):
    step = _step(wf["jobs"]["fetch"], "pre_validate")
    run = step["run"]
    assert run.index("set +e") < run.index("PRE_STATUS=$?")
    assert "continue-on-error" not in step
    names = [s.get("name") or "" for s in wf["jobs"]["fetch"]["steps"]]
    assert not [n for n in names if n.startswith("Fail if pre-validation")]
    assert not [n for n in names if "snapshot-only" in n]


def test_bet_authority_gating_is_untouched(wf):
    steps = wf["jobs"]["fetch"]["steps"]
    for step_id in ("risk_gate", "write_pending_bets", "validate_bet_logging", "write_tracked_tickers"):
        assert "github.event_name != 'schedule'" in _step({"steps": steps}, step_id)["if"]
    for job_name in ("slate_day", "refresh_bankroll"):
        text = json.dumps(wf["jobs"][job_name])
        for script in ("risk_gate.py", "write_pending_bets.py", "write_tracked_tickers.py"):
            assert script not in text


# ── shell-level: the real run: bodies under `bash -e` ─────────────────────

def _render(run, mapping):
    for k, v in mapping.items():
        run = run.replace("${{ %s }}" % k, v)
    assert "${{" not in run, run
    return run


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _bash_e(script, cwd, env_extra):
    out, summ = cwd / "gh_output", cwd / "gh_summary"
    out.write_text("")
    summ.write_text("")
    env = dict(os.environ, GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summ), **env_extra)
    proc = subprocess.run(["bash", "-e", "-c", script], cwd=str(cwd), env=env,
                          capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return proc, outputs, summ.read_text()


@pytest.mark.parametrize("pre_exit,step_rc", [(0, 0), (2, 0), (1, 1), (3, 3)])
def test_pre_validate_step_body_under_bash_e(tmp_path, wf, pre_exit, step_rc):
    """The 2026-09-28 bug: under `bash -e` the old body never wrote pre_exit
    for a non-zero validator exit. Now pre_exit is always written, exit 2
    (TBD starters) is a warning, and 1/3 fail the step."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "validate_slate_pre.py").write_text(
        "import sys; sys.exit(%d)\n" % pre_exit)
    run = _render(_step(wf["jobs"]["fetch"], "pre_validate")["run"], {"env.DATE": DATE})
    proc, outputs, summary = _bash_e(run, tmp_path, {})
    assert proc.returncode == step_rc, proc.stdout + proc.stderr
    assert outputs["pre_exit"] == str(pre_exit)
    if pre_exit == 2:
        assert "::warning" in proc.stdout
        assert "NOT_READY" in summary


@pytest.mark.parametrize("http,pre_exit,event,cron,proceed,verdict", [
    ("200", 3, "schedule", FIRST, "false", "OFF_DAY"),
    ("200", 3, "schedule", FINAL, "false", "OFF_DAY"),
    ("200", 0, "schedule", FIRST, "true", "GAME_DAY"),
    ("200", 2, "schedule", FIRST, "true", "GAME_DAY"),
    ("200", 1, "schedule", FIRST, "false", "NOT_VERIFIED"),
    ("200", 1, "schedule", FINAL, "true", "NOT_VERIFIED"),
    ("200", 1, "workflow_dispatch", "", "true", "NOT_VERIFIED"),
    ("503", None, "schedule", MIDDLE, "false", "SLATE_FETCH_FAILED"),
    ("503", None, "push", "", "true", "SLATE_FETCH_FAILED"),
])
def test_slate_day_gate_step_body_under_bash_e(tmp_path, wf, http, pre_exit, event, cron,
                                               proceed, verdict):
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "scripts", "ci", "slate_day_gate.py"),
                tmp_path / "scripts" / "ci" / "slate_day_gate.py")
    (tmp_path / "scripts" / "validate_slate_pre.py").write_text(
        "import sys; sys.exit(%d)\n" % (pre_exit if pre_exit is not None else 99))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Fake curl: writes the -o target and prints the configured HTTP code.
    _exe(bindir / "curl", "#!/usr/bin/env bash\n"
         "while [ $# -gt 0 ]; do if [ \"$1\" = -o ]; then shift; echo '{}' > \"$1\"; fi; shift; done\n"
         "printf '%s' \"$FAKE_HTTP\"\n")
    job = wf["jobs"]["slate_day"]
    run = _step(job, "gate")["run"]
    env = {"DATE": DATE, "EVENT_NAME": event, "CRON": cron, "FAKE_HTTP": http,
           "PATH": str(bindir) + os.pathsep + os.environ["PATH"]}
    proc, outputs, summary = _bash_e(run, tmp_path, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["proceed"] == proceed
    assert outputs["verdict"] == verdict
    assert verdict in summary


@pytest.mark.parametrize("value,ok", [("2026-09-28", True), ("2026/09/14", False),
                                      ("09-19-2026", False), ("2026-02-30", False), ("", True)])
def test_date_step_rejects_malformed_dates_before_anything_runs(tmp_path, wf, value, ok):
    run = _step(wf["jobs"]["slate_day"], "date")["run"]
    proc, outputs, _ = _bash_e(run, tmp_path, {"INPUT_DATE": value, "REF_NAME": "main"})
    if ok:
        assert proc.returncode == 0, proc.stderr
        assert len(outputs["date"]) == 10
        if value:
            assert outputs["date"] == value
    else:
        assert proc.returncode == 1
        assert "date" not in outputs
        assert "::error title=Invalid slate date::" in proc.stdout


def test_date_step_reads_run_fetch_branch_date(tmp_path, wf):
    run = _step(wf["jobs"]["slate_day"], "date")["run"]
    proc, outputs, _ = _bash_e(run, tmp_path, {"INPUT_DATE": "", "REF_NAME": "run/fetch-2026-09-20"})
    assert proc.returncode == 0
    assert outputs["date"] == "2026-09-20"
