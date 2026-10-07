#!/usr/bin/env python3
"""
tests/test_pipeline_watchdog.py
===============================
scripts/ci/pipeline_watchdog.py, wired into .github/workflows/app-export.yml after the
2026-10-07 missed-cron incident. Pure decision cases plus main() with gh mocked: nothing
here calls GitHub or writes repository data.
"""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location("pipeline_watchdog", os.path.join(ROOT, "scripts", "ci", "pipeline_watchdog.py"))
W = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(W)

TODAY = "2026-10-07"
NOW = datetime(2026, 10, 7, 19, 45, tzinfo=timezone.utc)          # 15:45 ET, inside the capture window
FRESH = NOW - timedelta(minutes=5)
STALE = NOW - timedelta(hours=3)


def state(**kw):
    s = {"today": TODAY, "kalshi_game_count": 4, "market_count": 663, "slate_exists": True,
         "latest_observation_at": FRESH, "latest_raw_snapshot_at": None, "latest_model_evaluation_at": FRESH}
    s.update(kw)
    return s


def run(created=None, status="completed"):
    return {"status": status, "createdAt": (created or (NOW - timedelta(hours=2))).strftime("%Y-%m-%dT%H:%M:%SZ")}


def workflows(out):
    return [d["workflow"] for d in out["dispatch"]]


# ---------------------------------------------------------------- decision cases

def test_fresh_slate_capture_and_model_dispatch_nothing():
    assert W.decide(state(), {}, NOW)["dispatch"] == []


def test_missing_slate_after_9_et_with_current_kalshi_games_dispatches_exactly_one_unattended_fetch():
    out = W.decide(state(slate_exists=False), {}, NOW)
    slate = [d for d in out["dispatch"] if d["workflow"] == W.FETCH_SLATE]
    assert len(slate) == 1
    assert slate[0]["inputs"] == {"date": TODAY, "unattended": "true"}
    assert workflows(out) == [W.FETCH_SLATE]           # capture fresh, model not evaluated without a slate


def test_missing_slate_before_9_et_waits():
    early = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)   # 08:30 ET
    assert W.FETCH_SLATE not in workflows(W.decide(state(slate_exists=False), {}, early))


@pytest.mark.parametrize("runs", [[run(status="in_progress")], [run(status="queued")],
                                  [run(created=NOW - timedelta(minutes=10))]])
def test_recent_or_in_progress_slate_workflow_blocks_a_duplicate(runs):
    assert W.decide(state(slate_exists=False), {W.FETCH_SLATE: runs}, NOW)["dispatch"] == []


def test_slate_rearms_once_its_last_run_is_older_than_the_gap():
    old = [run(created=NOW - timedelta(minutes=W.SLATE_MIN_GAP_MINUTES + 1))]
    assert workflows(W.decide(state(slate_exists=False), {W.FETCH_SLATE: old}, NOW)) == [W.FETCH_SLATE]


def test_stale_capture_inside_the_window_dispatches_one_capture():
    out = W.decide(state(latest_observation_at=STALE), {}, NOW)
    assert workflows(out) == [W.CAPTURE]
    assert out["dispatch"][0]["inputs"] == {"date": TODAY}


@pytest.mark.parametrize("hour", [6, 9, 12, 15])
def test_stale_capture_outside_the_window_dispatches_nothing(hour):
    t = datetime(2026, 10, 7, hour, 30, tzinfo=timezone.utc)
    out = W.decide(state(latest_observation_at=STALE, latest_model_evaluation_at=t), {}, t)
    assert W.CAPTURE not in workflows(out)


def test_recent_or_in_progress_capture_blocks_a_duplicate():
    for runs in ([run(status="in_progress")], [run(created=NOW - timedelta(minutes=10))]):
        assert W.decide(state(latest_observation_at=STALE), {W.CAPTURE: runs}, NOW)["dispatch"] == []


def test_uningested_newer_raw_capture_dispatches_one_ingest_not_another_capture():
    """2026-10-07: captures dispatched by github-actions[bot] at 21:42 / 21:53 / 23:42 were never
    ingested (a GITHUB_TOKEN dispatch does not start the workflow_run successor)."""
    st = state(latest_observation_at=NOW - timedelta(minutes=74), latest_raw_snapshot_at=NOW - timedelta(minutes=5))
    out = W.decide(st, {}, NOW)
    assert workflows(out) == [W.INGEST] and out["dispatch"][0]["inputs"] == {"date": TODAY}
    for runs in ([run(status="in_progress")], [run(created=NOW - timedelta(minutes=10))]):
        assert W.decide(st, {W.INGEST: runs}, NOW)["dispatch"] == []      # no duplicate ingest
    # a recent raw capture does not block the ingest; only a recent ingest does
    assert workflows(W.decide(st, {W.CAPTURE: [run(status="in_progress")]}, NOW)) == [W.INGEST]


def test_raw_capture_not_newer_than_observations_falls_back_to_capture():
    st = state(latest_observation_at=STALE, latest_raw_snapshot_at=STALE - timedelta(minutes=1))
    assert workflows(W.decide(st, {}, NOW)) == [W.CAPTURE]


def test_ingest_is_never_dispatched_outside_the_capture_window_or_when_fresh():
    t = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
    st = state(latest_observation_at=STALE, latest_raw_snapshot_at=t, latest_model_evaluation_at=t)
    assert W.INGEST not in workflows(W.decide(st, {}, t))
    assert W.decide(state(latest_raw_snapshot_at=NOW), {}, NOW)["dispatch"] == []


def test_raw_snapshot_time_is_read_from_the_capture_head(tmp_path):
    folder = tmp_path / "kalshi_registry_snapshots"
    folder.mkdir()
    (folder / f"kalshi_search_{TODAY}_2342.json").write_text('{"date":"2026-10-07","fetched_at":"2026-10-07T23:42:04.000Z","markets":[]}')
    (folder / f"kalshi_search_{TODAY}_0034.json").write_text('{"date":"2026-10-07","fetched_at":"2026-10-07T04:34:00.000Z"}')
    (folder / "kalshi_search_2026-10-06_2359.json").write_text('{"fetched_at":"2026-10-07T03:59:00.000Z"}')
    assert W.latest_raw_snapshot_at(str(tmp_path), TODAY) == datetime(2026, 10, 7, 23, 42, 4, tzinfo=timezone.utc)


def test_stale_model_with_todays_slate_dispatches_one_model_snapshot():
    out = W.decide(state(latest_model_evaluation_at=STALE), {}, NOW)
    assert workflows(out) == [W.MODEL] and out["dispatch"][0]["inputs"] == {}
    none_yet = W.decide(state(latest_model_evaluation_at=None), {}, NOW)
    assert workflows(none_yet) == [W.MODEL]


def test_recent_or_in_progress_model_blocks_a_duplicate():
    for runs in ([run(status="in_progress")], [run(created=NOW - timedelta(minutes=5))]):
        assert W.decide(state(latest_model_evaluation_at=STALE), {W.MODEL: runs}, NOW)["dispatch"] == []


def test_model_is_never_dispatched_without_todays_slate():
    out = W.decide(state(slate_exists=False, latest_model_evaluation_at=STALE),
                   {W.FETCH_SLATE: [run(status="in_progress")]}, NOW)
    assert W.MODEL not in workflows(out)


def test_no_current_mlb_games_or_markets_dispatches_nothing():
    out = W.decide(state(kalshi_game_count=0, market_count=0, slate_exists=False,
                         latest_observation_at=None, latest_model_evaluation_at=None), {}, NOW)
    assert out["dispatch"] == []


# ---------------------------------------------------------------- main(): gh mocked

def _data_root(tmp_path, *, slate=False, obs_at=None, eval_at=None):
    root = tmp_path / "data"
    (root / "edgelab" / "games").mkdir(parents=True)
    (root / "edgelab" / "games" / f"{TODAY}.jsonl").write_text(json.dumps({"awayTeam": "LAD", "homeTeam": "ATL"}) + "\n")
    (root / "edgelab" / "markets").mkdir(parents=True)
    (root / "edgelab" / "markets" / f"{TODAY}.jsonl").write_text(json.dumps({"marketTicker": "X"}) + "\n")
    if obs_at:
        (root / "edgelab" / "observations").mkdir(parents=True)
        (root / "edgelab" / "observations" / f"{TODAY}.jsonl").write_text(json.dumps({"capturedAt": obs_at}) + "\n")
    if eval_at:
        (root / "edgelab" / "model_evaluations").mkdir(parents=True)
        (root / "edgelab" / "model_evaluations" / f"{TODAY}.jsonl").write_text(json.dumps({"createdAt": eval_at}) + "\n")
    if slate:
        (root / "slates" / TODAY).mkdir(parents=True)
        (root / "slates" / TODAY / "authoritative.json").write_text("{}")
    return str(root)


def _main(monkeypatch, data_root, runs_by_wf, dispatch_raises=False):
    sent = []

    def fake_dispatch(item):
        if dispatch_raises:
            raise RuntimeError("HTTP 403")
        sent.append(item)

    monkeypatch.setattr(W, "_gh_runs", lambda wf: runs_by_wf.get(wf, []))
    monkeypatch.setattr(W, "_dispatch", fake_dispatch)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    rc = W.main(["--data-root", data_root, "--now", "2026-10-07T19:45:00Z"])
    return rc, sent


def test_unknown_github_run_list_state_fails_closed(monkeypatch, tmp_path, capsys):
    root = _data_root(tmp_path)                       # slate, capture and model all stale/missing
    rc, sent = _main(monkeypatch, root, {W.FETCH_SLATE: None, W.CAPTURE: None, W.INGEST: None, W.MODEL: None})
    assert rc == 0 and sent == []
    out = capsys.readouterr().out
    assert out.count("UNKNOWN -> failed closed") == 4


def test_main_dispatches_exactly_what_decide_returns_and_logs_each_run_list(monkeypatch, tmp_path, capsys):
    root = _data_root(tmp_path, obs_at="2026-10-07T19:40:00Z")
    rc, sent = _main(monkeypatch, root, {W.FETCH_SLATE: [], W.CAPTURE: [], W.INGEST: [], W.MODEL: []})
    assert rc == 0
    assert [(d["workflow"], d["inputs"]) for d in sent] == [(W.FETCH_SLATE, {"date": TODAY, "unattended": "true"})]
    out = capsys.readouterr().out
    for wf in (W.FETCH_SLATE, W.CAPTURE, W.INGEST, W.MODEL):
        assert f"gh run list {wf}: 0 run(s)" in out


def test_main_with_everything_fresh_dispatches_nothing(monkeypatch, tmp_path):
    root = _data_root(tmp_path, slate=True, obs_at="2026-10-07T19:40:00Z", eval_at="2026-10-07T19:30:00Z")
    rc, sent = _main(monkeypatch, root, {W.FETCH_SLATE: [], W.CAPTURE: [], W.MODEL: []})
    assert rc == 0 and sent == []


def test_a_failed_dispatch_never_fails_the_step(monkeypatch, tmp_path):
    rc, _sent = _main(monkeypatch, _data_root(tmp_path), {}, dispatch_raises=True)
    assert rc == 0


def test_dispatch_command_targets_main_with_the_decided_inputs(monkeypatch):
    calls = []
    monkeypatch.setattr(W.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    W._dispatch({"workflow": W.FETCH_SLATE, "inputs": {"date": TODAY, "unattended": "true"}})
    assert calls == [["gh", "workflow", "run", "fetch-slate.yml", "--ref", "main",
                      "-f", f"date={TODAY}", "-f", "unattended=true"]]


def test_gh_run_list_failure_returns_unknown(monkeypatch):
    def boom(*a, **k):
        raise OSError("gh: not found")
    monkeypatch.setattr(W.subprocess, "run", boom)
    assert W._gh_runs(W.CAPTURE) is None


# ---------------------------------------------------------------- wiring

def _workflow(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_app_export_runs_the_watchdog_with_exactly_contents_and_actions_write():
    doc = _workflow("app-export.yml")
    assert doc["permissions"] == {"contents": "write", "actions": "write"}
    steps = doc["jobs"]["export"]["steps"]
    names = [s.get("name") for s in steps]
    i = names.index("Re-arm missed MLB producers")
    step = steps[i]
    assert step["run"].strip() == "python3 scripts/ci/pipeline_watchdog.py"
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    assert step.get("continue-on-error") is True          # can never block the export
    assert names.index("Checkout repo") < i < names.index("Build app/latest from committed data")
    assert "permissions" not in doc["jobs"]["export"]     # no job-level override


def test_every_producer_the_watchdog_dispatches_accepts_its_inputs():
    for wf, inputs in ((W.FETCH_SLATE, {"date", "unattended"}), (W.CAPTURE, {"date"}), (W.INGEST, {"date"}),
                       (W.MODEL, set())):
        on = _workflow(wf).get("on") or _workflow(wf).get(True)
        declared = set(((on.get("workflow_dispatch") or {}).get("inputs") or {}).keys())
        assert inputs <= declared, (wf, inputs, declared)


def test_unattended_slate_dispatch_skips_every_bet_logging_step():
    steps = {s.get("id"): s for s in _workflow(W.FETCH_SLATE)["jobs"]["fetch"]["steps"] if s.get("id")}
    for sid in ("risk_gate", "write_pending_bets", "validate_bet_logging", "write_tracked_tickers"):
        assert "github.event.inputs.unattended != 'true'" in steps[sid]["if"], sid
