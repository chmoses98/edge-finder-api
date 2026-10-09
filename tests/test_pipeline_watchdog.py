#!/usr/bin/env python3
"""
tests/test_pipeline_watchdog.py
===============================
The MLB pipeline state machine (scripts/ci/pipeline_watchdog.py) run by
.github/workflows/mlb-pipeline-conductor.yml (stages A-E, bounded self-chaining loop)
and by .github/workflows/app-export.yml (liveness backstop). Deterministic: gh and
the clock are injected; git runs only in tmp_path repositories.
"""
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location("pipeline_watchdog", os.path.join(ROOT, "scripts", "ci", "pipeline_watchdog.py"))
W = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(W)

TODAY = "2026-10-07"
NOW = datetime(2026, 10, 7, 21, 30, tzinfo=timezone.utc)          # 17:30 ET, inside the capture window
FRESH = NOW - timedelta(minutes=5)
STALE = NOW - timedelta(hours=2)
RAW = "data/kalshi_registry_snapshots/kalshi_search_2026-10-07_2125.json"


def state(**kw):
    """A fully healthy, settled day: every stage fresh."""
    s = {"today": TODAY, "kalshi_game_count": 4, "market_count": 1100, "slate_exists": True,
         "slate_status_counts": {"SCHEDULED": 2, "LIVE": 2}, "pregame_game_count": 2,
         "latest_raw_snapshot": RAW, "latest_raw_snapshot_at": FRESH, "latest_raw_snapshot_ingested": True,
         "latest_observation_at": FRESH, "latest_model_evaluation_at": FRESH,
         "export": {"stale": False, "reason": "current"}}
    s.update(kw)
    return s


def run(created=None, status="completed"):
    return {"status": status, "createdAt": (created or (NOW - timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%SZ")}


def wfs(out):
    return [d["workflow"] for d in out["dispatch"]]


# ---------------------------------------------------------------- stage decisions

def test_no_games_or_markets_dispatches_nothing():
    out = W.decide(state(kalshi_game_count=0, market_count=0, slate_exists=False, latest_raw_snapshot=None,
                         latest_raw_snapshot_at=None, latest_raw_snapshot_ingested=None,
                         latest_model_evaluation_at=None, export={"stale": True}), {}, NOW)
    assert out["dispatch"] == []


def test_settled_healthy_day_dispatches_nothing():
    assert W.decide(state(), {}, NOW)["dispatch"] == []


def test_missing_slate_dispatches_exactly_one_unattended_fetch():
    out = W.decide(state(slate_exists=False, pregame_game_count=0, slate_status_counts={}), {}, NOW)
    assert wfs(out) == [W.FETCH_SLATE]
    assert out["dispatch"][0]["inputs"] == {"date": TODAY, "unattended": "true"}


def test_missing_slate_waits_for_9_et():
    early = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)    # 08:30 ET
    assert W.FETCH_SLATE not in wfs(W.decide(state(slate_exists=False), {}, early))


@pytest.mark.parametrize("runs", [[run(status="in_progress")], [run(status="queued")],
                                  [run(created=NOW - timedelta(minutes=10))], None])
def test_recent_in_flight_or_unknown_slate_run_blocks_a_duplicate(runs):
    assert W.decide(state(slate_exists=False), {W.FETCH_SLATE: runs}, NOW)["dispatch"] == []


def test_stale_raw_capture_dispatches_one_capture():
    out = W.decide(state(latest_raw_snapshot_at=STALE), {}, NOW)
    assert wfs(out) == [W.CAPTURE] and out["dispatch"][0]["inputs"] == {"date": TODAY}
    missing = W.decide(state(latest_raw_snapshot=None, latest_raw_snapshot_at=None, latest_raw_snapshot_ingested=None), {}, NOW)
    assert wfs(missing) == [W.CAPTURE]


@pytest.mark.parametrize("hour", [6, 9, 12, 15])
def test_capture_follows_the_capture_window(hour):
    t = datetime(2026, 10, 7, hour, 30, tzinfo=timezone.utc)
    out = W.decide(state(latest_raw_snapshot_at=STALE, latest_model_evaluation_at=t), {}, t)
    assert W.CAPTURE not in wfs(out)


def test_capture_continues_after_every_game_started_complete_archival():
    """Market archival follows the capture window, not the pregame horizon."""
    out = W.decide(state(latest_raw_snapshot_at=STALE, pregame_game_count=0,
                         slate_status_counts={"LIVE": 3, "FINAL": 1}), {}, NOW)
    assert wfs(out) == [W.CAPTURE]


def test_uningested_raw_capture_dispatches_ingest_not_another_capture():
    st = state(latest_raw_snapshot_at=STALE, latest_raw_snapshot_ingested=False)   # raw ALSO stale
    out = W.decide(st, {}, NOW)
    assert wfs(out) == [W.INGEST] and out["dispatch"][0]["inputs"] == {"date": TODAY}
    fresh = W.decide(state(latest_raw_snapshot_ingested=False), {}, NOW)
    assert wfs(fresh) == [W.INGEST]
    # outside the capture window an existing capture is still ingested
    t = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
    assert W.INGEST in wfs(W.decide(state(latest_raw_snapshot_ingested=False, latest_model_evaluation_at=t), {}, t))


@pytest.mark.parametrize("runs", [[run(status="in_progress")], [run(created=NOW - timedelta(minutes=10))], None])
def test_recent_in_flight_or_unknown_ingest_blocks_a_duplicate_and_never_falls_back_to_capture(runs):
    out = W.decide(state(latest_raw_snapshot_ingested=False), {W.INGEST: runs}, NOW)
    assert out["dispatch"] == []


def test_fresh_ingest_stale_model_and_a_pregame_game_dispatches_one_model():
    out = W.decide(state(latest_model_evaluation_at=STALE), {}, NOW)
    assert wfs(out) == [W.MODEL] and out["dispatch"][0]["inputs"] == {}
    assert wfs(W.decide(state(latest_model_evaluation_at=None), {}, NOW)) == [W.MODEL]


@pytest.mark.parametrize("runs", [[run(status="in_progress")], [run(created=NOW - timedelta(minutes=5))], None])
def test_recent_in_flight_or_unknown_model_run_blocks_a_duplicate(runs):
    assert W.decide(state(latest_model_evaluation_at=STALE), {W.MODEL: runs}, NOW)["dispatch"] == []


def test_stale_model_with_every_game_started_closes_snapshot_production():
    out = W.decide(state(latest_model_evaluation_at=STALE, pregame_game_count=0,
                         slate_status_counts={"LIVE": 2, "FINAL": 2}), {}, NOW)
    assert W.MODEL not in wfs(out)
    assert any("no remaining pregame MLB events -- snapshot production closed for 2026-10-07" in n for n in out["notes"])


def test_model_never_runs_without_todays_slate():
    out = W.decide(state(slate_exists=False, latest_model_evaluation_at=STALE),
                   {W.FETCH_SLATE: [run(status="in_progress")]}, NOW)
    assert W.MODEL not in wfs(out)


def test_upstream_newer_than_the_publication_dispatches_one_export():
    out = W.decide(state(export={"stale": True, "reason": "upstream abc newer"}), {}, NOW)
    assert wfs(out) == [W.EXPORT] and out["dispatch"][0]["inputs"] == {}


@pytest.mark.parametrize("runs", [[run(status="in_progress")], [run(created=NOW - timedelta(minutes=4))], None])
def test_recent_in_flight_or_unknown_export_blocks_a_duplicate(runs):
    assert W.decide(state(export={"stale": True}), {W.EXPORT: runs}, NOW)["dispatch"] == []


def test_unknown_export_state_never_dispatches():
    out = W.decide(state(export={"stale": None, "reason": "git log failed"}), {}, NOW)
    assert out["dispatch"] == [] and any("export: state unknown" in n for n in out["notes"])


def test_every_run_list_unknown_fails_closed_everywhere():
    everything_due = state(slate_exists=False, latest_raw_snapshot_ingested=False, export={"stale": True})
    assert W.decide(everything_due, {wf: None for wf in W.PRODUCERS}, NOW)["dispatch"] == []
    model_due = state(latest_model_evaluation_at=STALE, latest_raw_snapshot_at=STALE)
    assert W.decide(model_due, {wf: None for wf in W.PRODUCERS}, NOW)["dispatch"] == []


# ---------------------------------------------------------------- sequence simulation

def test_sequence_missing_slate_to_settled_system():
    """missing slate -> slate -> capture -> ingest -> model -> export -> zero dispatches.
    Each producer's run appears in its run list right after it is dispatched; the next
    evaluation sees the artifact it committed."""
    t = NOW
    runs = {wf: [] for wf in W.PRODUCERS}
    s = state(slate_exists=False, slate_status_counts={}, pregame_game_count=0,
              latest_raw_snapshot=None, latest_raw_snapshot_at=None, latest_raw_snapshot_ingested=None,
              latest_observation_at=None, latest_model_evaluation_at=None, export={"stale": True, "reason": "x"})
    history = []

    def step(minutes):
        nonlocal t
        t = t + timedelta(minutes=minutes)
        out = W.decide(s, runs, t)
        for d in out["dispatch"]:
            runs[d["workflow"]] = [run(created=t, status="queued")]
        history.append(sorted(wfs(out)))
        return out

    step(0)                                   # slate missing, nothing captured, nothing exported
    assert history[-1] == sorted([W.FETCH_SLATE, W.CAPTURE, W.EXPORT])
    step(2)                                   # all three in flight: nothing new
    assert history[-1] == []
    for wf in runs:                            # they complete
        runs[wf] = [run(created=t - timedelta(minutes=2))]
    s.update(slate_exists=True, slate_status_counts={"SCHEDULED": 4}, pregame_game_count=4,
             latest_raw_snapshot=RAW, latest_raw_snapshot_at=t, latest_raw_snapshot_ingested=False,
             export={"stale": True, "reason": "slate committed"})
    step(30)                                  # raw not ingested -> INGEST; slate -> MODEL; export -> EXPORT
    assert history[-1] == sorted([W.INGEST, W.MODEL, W.EXPORT])
    for wf in runs:
        runs[wf] = [run(created=t - timedelta(minutes=1))]
    s.update(latest_raw_snapshot_ingested=True, latest_observation_at=t, latest_model_evaluation_at=t,
             export={"stale": True, "reason": "observations + evaluations committed"})
    step(11)                                  # only the export still lags
    assert history[-1] == [W.EXPORT]
    s.update(export={"stale": False, "reason": "current"})
    runs[W.EXPORT] = [run(created=t)]
    step(5)
    assert history[-1] == []                  # settled
    step(5)
    assert history[-1] == []
    # never two dispatches of one producer while its previous run was in flight
    assert sum(1 for h in history for wf in h if wf == W.FETCH_SLATE) == 1


# ---------------------------------------------------------------- pregame horizon (real app_export gate)

def _slate(*games):
    return {"date": TODAY, "games": [{"gameId": i, "status": st, "startTime": start} for i, (st, start) in enumerate(games)]}


H_NOW = datetime(2026, 10, 7, 22, 30, tzinfo=timezone.utc)


def test_horizon_one_pregame_two_live_allows_model_refresh():
    h = W.pregame_horizon(_slate(("In Progress", "2026-10-07T20:00:00Z"), ("Pre-Game", "2026-10-07T22:00:00Z"),
                                 ("Pre-Game", "2026-10-08T00:00:00Z")), H_NOW)
    assert h == {"LIVE": 2, "SCHEDULED": 1}
    out = W.decide(state(pregame_game_count=h["SCHEDULED"], slate_status_counts=h, latest_model_evaluation_at=STALE), {}, H_NOW)
    assert W.MODEL in wfs(out)


def test_horizon_all_live_or_final_closes_the_model():
    h = W.pregame_horizon(_slate(("Final", "2026-10-07T17:00:00Z"), ("In Progress", "2026-10-07T20:00:00Z"),
                                 ("Game Over", "2026-10-07T21:00:00Z")), H_NOW)
    assert h.get("SCHEDULED", 0) == 0


def test_horizon_postponed_game_is_never_pregame():
    h = W.pregame_horizon(_slate(("Postponed", "2026-10-08T00:00:00Z"), ("Final", "2026-10-07T17:00:00Z")), H_NOW)
    assert h == {"POSTPONED": 1, "FINAL": 1}
    out = W.decide(state(pregame_game_count=0, slate_status_counts=h, latest_model_evaluation_at=STALE), {}, H_NOW)
    assert W.MODEL not in wfs(out)


def test_horizon_stale_pre_game_status_after_first_pitch_counts_as_started():
    h = W.pregame_horizon(_slate(("Pre-Game", "2026-10-07T22:00:00Z"), ("Warmup", "2026-10-07T22:29:00Z")), H_NOW)
    assert h == {"LIVE": 2}


def test_horizon_unknown_or_missing_start_is_not_pregame():
    h = W.pregame_horizon(_slate(("Suspended: Rain", "2026-10-08T00:00:00Z"), ("Pre-Game", None)), H_NOW)
    assert h.get("SCHEDULED", 0) == 0


# ---------------------------------------------------------------- read_state / export_state (real files, real git)

def _jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_read_state_detects_ingestion_by_observation_provenance(tmp_path):
    root = tmp_path / "data"
    _jsonl(str(root / "edgelab" / "games" / f"{TODAY}.jsonl"), [{"awayTeam": "LAD", "homeTeam": "ATL"}])
    _jsonl(str(root / "edgelab" / "markets" / f"{TODAY}.jsonl"), [{"marketTicker": "X"}])
    snaps = root / "kalshi_registry_snapshots"
    snaps.mkdir(parents=True)
    (snaps / f"kalshi_search_{TODAY}_2232.json").write_text('{"date":"x","fetched_at":"2026-10-07T22:32:50.000Z","total_markets":900}')
    (snaps / f"kalshi_search_{TODAY}_0014.json").write_text('{"date":"x","fetched_at":"2026-10-08T00:14:41.000Z","total_markets":955}')
    (snaps / "kalshi_search_2026-10-06_2359.json").write_text('{"fetched_at":"2026-10-08T03:59:00.000Z","total_markets":5}')
    # observation timestamps are per quote (00:14:33 < the file's 00:14:41): provenance decides
    obs = [{"capturedAt": "2026-10-08T00:14:33Z",
            "provenance": {"sourceFile": f"data/kalshi_registry_snapshots/kalshi_search_{TODAY}_2232.json"}}]
    _jsonl(str(root / "edgelab" / "observations" / f"{TODAY}.jsonl"), obs)
    s = W.read_state(str(root), TODAY, NOW, with_export=False)
    assert s["latest_raw_snapshot"].endswith("_0014.json") and s["latest_raw_snapshot_ingested"] is False
    obs.append({"capturedAt": "2026-10-08T00:14:33Z",
                "provenance": {"sourceFile": f"data/kalshi_registry_snapshots/kalshi_search_{TODAY}_0014.json"}})
    _jsonl(str(root / "edgelab" / "observations" / f"{TODAY}.jsonl"), obs)
    assert W.read_state(str(root), TODAY, NOW, with_export=False)["latest_raw_snapshot_ingested"] is True
    assert s["slate_exists"] is False and s["pregame_game_count"] == 0
    assert s["latest_raw_snapshot_at"] == datetime(2026, 10, 8, 0, 14, 41, tzinfo=timezone.utc)

    # a FAILED / empty capture (Kalshi error) is never "awaiting ingest" and never "fresh"
    (snaps / f"kalshi_search_{TODAY}_0045.json").write_text(
        '{"date":"x","fetched_at":"2026-10-08T00:45:00.000Z","total_markets":0,"markets":[],"captureStatus":"FAILED"}')
    f = W.read_state(str(root), TODAY, NOW, with_export=False)
    assert f["latest_raw_snapshot"].endswith("_0045.json") and f["latest_raw_snapshot_ingested"] is None
    assert f["latest_raw_snapshot_at"] == datetime(2026, 10, 8, 0, 14, 41, tzinfo=timezone.utc)


def test_failed_newest_capture_is_recaptured_not_endlessly_ingested():
    st = state(latest_raw_snapshot_ingested=None, latest_raw_snapshot_at=STALE)
    out = W.decide(st, {}, NOW)
    assert wfs(out) == [W.CAPTURE]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def _commit(repo, rel, content, msg):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    _git(repo, "add", rel)
    return _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    return r


def test_export_state_from_git_ancestry(repo):
    obs = f"data/edgelab/observations/{TODAY}.jsonl"
    c1 = _commit(repo, obs, "{}\n", "edgelab capture 1")
    _commit(repo, "app/latest/manifest.json", json.dumps({"commit_sha": c1}), "app export from c1")
    assert W.export_state(str(repo), TODAY)["stale"] is False
    c3 = _commit(repo, obs, "{}\n{}\n", "edgelab capture 2")
    st = W.export_state(str(repo), TODAY)
    assert st["stale"] is True and st["upstream"] == c3
    _commit(repo, "app/latest/manifest.json", json.dumps({"commit_sha": c3}), "app export from c3")
    assert W.export_state(str(repo), TODAY)["stale"] is False
    # an export commit never makes the export stale again (no export -> export loop)
    _commit(repo, "app/latest/board.json", "{}", "app export again")
    assert W.export_state(str(repo), TODAY)["stale"] is False


def test_export_state_unknown_fails_closed(repo, tmp_path):
    assert W.export_state(str(repo), TODAY)["stale"] is None                         # no manifest
    _commit(repo, f"data/slates/{TODAY}/authoritative.json", "{}", "slate")
    _commit(repo, "app/latest/manifest.json", json.dumps({"commit_sha": "f" * 40}), "export from unknown sha")
    assert W.export_state(str(repo), TODAY)["stale"] is None                         # source not in history
    assert W.export_state(str(tmp_path / "nogit"), TODAY)["stale"] is None


# ---------------------------------------------------------------- app-export host (liveness backstop)

def test_app_export_host_defers_while_the_conductor_is_alive():
    due = state(slate_exists=False, latest_raw_snapshot_ingested=False)
    for runs in ([run(status="in_progress")], [run(created=NOW - timedelta(minutes=50))], None):
        assert W.decide_app_export_host(due, {W.CONDUCTOR: runs}, NOW, enabled=True)["dispatch"] == []


def test_app_export_host_revives_a_stopped_conductor_and_nothing_else():
    out = W.decide_app_export_host(state(slate_exists=False), {W.CONDUCTOR: [run(created=NOW - timedelta(hours=3))]},
                                   NOW, enabled=True)
    assert wfs(out) == [W.CONDUCTOR]


def test_app_export_host_with_conductor_switched_off_reconciles_a_to_d_but_never_exports():
    out = W.decide_app_export_host(state(slate_exists=False, export={"stale": True}), {}, NOW, enabled=False)
    assert wfs(out) == [W.FETCH_SLATE]
    assert W.EXPORT not in wfs(W.decide_app_export_host(state(export={"stale": True}), {}, NOW, enabled=False))


@pytest.mark.parametrize("raw,expected", [("", True), (None, True), ("true", True), ("false", False),
                                          ("OFF", False), ("0", False), ("disabled", False)])
def test_kill_switch_values(raw, expected):
    assert W.conductor_enabled(raw) is expected


# ---------------------------------------------------------------- successor (chain pattern)

class FakeRunner:
    def __init__(self, listing):
        self.listing, self.calls = listing, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "run", "list"]:
            if self.listing is None:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.listing), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _dispatches(r):
    return [c for c in r.calls if c[:3] == ["gh", "workflow", "run"]]


def test_successor_is_requested_exactly_once_when_no_other_conductor_is_active(capsys):
    r = FakeRunner([{"databaseId": 1, "status": "in_progress", "headBranch": "main"}])
    assert W.request_successor({"GITHUB_RUN_ID": "1"}, runner=r) == 0
    assert _dispatches(r) == [["gh", "workflow", "run", W.CONDUCTOR, "--ref", "main"]]


def test_successor_skipped_when_another_conductor_is_queued_or_switched_off():
    r = FakeRunner([{"databaseId": 1, "status": "in_progress", "headBranch": "main"},
                    {"databaseId": 2, "status": "pending", "headBranch": "main"}])
    W.request_successor({"GITHUB_RUN_ID": "1"}, runner=r)
    assert _dispatches(r) == []
    off = FakeRunner([])
    W.request_successor({"GITHUB_RUN_ID": "1", "MLB_CONDUCTOR_ENABLED": "false"}, runner=off)
    assert _dispatches(off) == []


def test_successor_with_unreadable_run_list_relies_on_the_concurrency_group():
    r = FakeRunner(None)
    W.request_successor({"GITHUB_RUN_ID": "1"}, runner=r)
    assert len(_dispatches(r)) == 1


# ---------------------------------------------------------------- main(): gh mocked

def _data_root(tmp_path):
    root = tmp_path / "data"
    _jsonl(str(root / "edgelab" / "games" / f"{TODAY}.jsonl"), [{"awayTeam": "LAD", "homeTeam": "ATL"}])
    _jsonl(str(root / "edgelab" / "markets" / f"{TODAY}.jsonl"), [{"marketTicker": "X"}])
    return str(root)


def test_main_fails_closed_on_unknown_run_lists_and_a_failed_dispatch_is_visible_not_fatal(monkeypatch, tmp_path, capsys):
    root = _data_root(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(W, "_gh_runs", lambda wf: None)
    sent = []
    monkeypatch.setattr(W, "_dispatch", lambda item: sent.append(item))
    assert W.main(["--data-root", root, "--repo-root", str(tmp_path), "--now", "2026-10-07T21:30:00Z"]) == 0
    assert sent == []
    assert capsys.readouterr().out.count("UNKNOWN -> failed closed") == len(W.PRODUCERS)

    monkeypatch.setattr(W, "_gh_runs", lambda wf: [])

    def boom(item):
        raise RuntimeError("HTTP 403")
    monkeypatch.setattr(W, "_dispatch", boom)
    assert W.main(["--data-root", root, "--repo-root", str(tmp_path), "--now", "2026-10-07T21:30:00Z"]) == 0
    out = capsys.readouterr().out
    assert "dispatch of fetch-slate.yml FAILED: HTTP 403" in out


def test_main_app_export_host_lists_only_the_conductor_while_enabled(monkeypatch, tmp_path):
    listed = []
    monkeypatch.setattr(W, "_gh_runs", lambda wf: listed.append(wf) or [run(status="in_progress")])
    monkeypatch.setattr(W, "_dispatch", lambda item: pytest.fail("must not dispatch"))
    monkeypatch.delenv("MLB_CONDUCTOR_ENABLED", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert W.main(["--host", "app-export", "--data-root", _data_root(tmp_path), "--now", "2026-10-07T21:30:00Z"]) == 0
    assert listed == [W.CONDUCTOR]


def test_dispatch_command_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(W.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    W._dispatch({"workflow": W.FETCH_SLATE, "inputs": {"date": TODAY, "unattended": "true"}})
    assert calls == [["gh", "workflow", "run", "fetch-slate.yml", "--ref", "main", "-f", f"date={TODAY}", "-f", "unattended=true"]]


# ---------------------------------------------------------------- wiring

def _wf(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_conductor_workflow_is_bounded_serialized_and_dispatch_only():
    doc = _wf(W.CONDUCTOR)
    on = doc.get("on") or doc.get(True)
    assert "workflow_dispatch" in on and len(on["schedule"]) == 1          # cron = restart backstop
    assert doc["permissions"] == {"contents": "read", "actions": "write"}
    assert doc["concurrency"] == {"group": "mlb-pipeline-conductor", "cancel-in-progress": False}
    job = doc["jobs"]["conduct"]
    assert "permissions" not in job
    assert job["timeout-minutes"] <= 60
    env = job["env"]
    assert int(env["EVALUATIONS"]) * int(env["INTERVAL_SECONDS"]) <= 55 * 60
    steps = {s.get("name"): s for s in job["steps"]}
    loop = steps["Reconcile the MLB pipeline (bounded loop)"]["run"]
    assert "pipeline_watchdog.py --host conductor" in loop and "git reset --quiet --hard FETCH_HEAD" in loop
    succ = steps["Request the successor conductor"]
    assert "--successor" in succ["run"] and "!cancelled()" in succ["if"]
    assert "MLB_CONDUCTOR_ENABLED" in job["if"]                             # kill switch skips cron runs


def test_app_export_runs_the_same_script_as_liveness_backstop():
    doc = _wf("app-export.yml")
    assert doc["permissions"] == {"contents": "write", "actions": "write"}
    step = next(s for s in doc["jobs"]["export"]["steps"] if s.get("name") == "Re-arm missed MLB producers")
    assert step["run"].strip() == "python3 scripts/ci/pipeline_watchdog.py --host app-export"
    assert step.get("continue-on-error") is True


def test_every_dispatched_producer_accepts_its_inputs():
    for wf, inputs in ((W.FETCH_SLATE, {"date", "unattended"}), (W.CAPTURE, {"date"}), (W.INGEST, {"date"}),
                       (W.MODEL, set()), (W.EXPORT, set()), (W.CONDUCTOR, set())):
        on = _wf(wf).get("on") or _wf(wf).get(True)
        assert "workflow_dispatch" in on, wf
        declared = set(((on.get("workflow_dispatch") or {}).get("inputs") or {}).keys())
        assert inputs <= declared, (wf, inputs, declared)


def test_unattended_slate_dispatch_skips_every_bet_logging_step():
    steps = {s.get("id"): s for s in _wf(W.FETCH_SLATE)["jobs"]["fetch"]["steps"] if s.get("id")}
    for sid in ("risk_gate", "write_pending_bets", "validate_bet_logging", "write_tracked_tickers"):
        assert "github.event.inputs.unattended != 'true'" in steps[sid]["if"], sid


def test_the_only_fetch_slate_dispatch_is_unattended():
    out = W.decide(state(slate_exists=False), {}, NOW)
    assert all(d["inputs"].get("unattended") == "true" for d in out["dispatch"] if d["workflow"] == W.FETCH_SLATE)


# ---------------------------------------------------------------- stage F: game state

def test_a_started_game_without_a_final_word_dispatches_one_game_state_refresh():
    out = W.decide(state(overdue_started_games=["849832"], game_state_at=None), {}, NOW)
    assert wfs(out) == [W.GAME_STATE] or W.GAME_STATE in wfs(out)
    item = next(d for d in out["dispatch"] if d["workflow"] == W.GAME_STATE)
    assert item["inputs"] == {"date": TODAY}
    assert "849832" in item["reason"]


def test_a_recent_game_state_read_or_in_flight_refresh_blocks_a_duplicate():
    recent = NOW - timedelta(minutes=10)
    assert W.GAME_STATE not in wfs(W.decide(state(overdue_started_games=["849832"], game_state_at=recent), {}, NOW))
    assert W.GAME_STATE not in wfs(W.decide(state(overdue_started_games=["849832"], game_state_at=None), {W.GAME_STATE: [run(recent, "in_progress")]}, NOW))
    assert W.GAME_STATE not in wfs(W.decide(state(overdue_started_games=["849832"], game_state_at=None), {W.GAME_STATE: None}, NOW))


def test_no_overdue_game_dispatches_no_refresh():
    out = W.decide(state(overdue_started_games=[], game_state_at=None), {}, NOW)
    assert W.GAME_STATE not in wfs(out)
    assert any("every started game has a final word" in n for n in out["notes"])


def test_overdue_started_games_is_the_slate_without_a_final_word_from_slate_or_feed():
    slate = _slate(("Pre-Game", "2026-10-07T18:00:00Z"),      # started 4.5h ago, slate still Pre-Game: overdue
                   ("Final", "2026-10-07T17:00:00Z"),         # the slate says final
                   ("In Progress", "2026-10-07T21:30:00Z"),   # started 1h ago: not yet overdue
                   ("Pre-Game", "2026-10-08T00:00:00Z"))      # pregame
    assert W.overdue_started_games(slate, None, H_NOW) == ["0"]
    feed = {"as_of": "2026-10-07T22:00:00Z", "games": {"0": {"abstract_game_state": "Final", "detailed_state": "Final"}}}
    assert W.overdue_started_games(slate, feed, H_NOW) == []
    postponed = {"as_of": "2026-10-07T22:00:00Z", "games": {"0": {"abstract_game_state": "Final", "detailed_state": "Postponed"}}}
    assert W.overdue_started_games(slate, postponed, H_NOW) == []
    live = {"as_of": "2026-10-07T22:00:00Z", "games": {"0": {"abstract_game_state": "Live", "detailed_state": "In Progress"}}}
    assert W.overdue_started_games(slate, live, H_NOW) == ["0"]


# ---------------------------------------------------------------- stage F keys on the published slate's date

def test_after_midnight_et_stage_f_reads_yesterdays_slate_and_dispatches_its_date(tmp_path):
    """01:00 ET on 2026-10-09: no 2026-10-09 slate or discovery yet; the 2026-10-08 slate's 20:00 ET game is
    still Pre-Game in the snapshot. The production case (CLE@CWS LIVE for thirteen hours): the refresh must
    target 2026-10-08, the slate app/latest publishes, and today's empty discovery must not short-circuit it."""
    root = tmp_path / "data"
    slate_dir = root / "slates" / "2026-10-08"
    slate_dir.mkdir(parents=True)
    slate_dir.joinpath("authoritative.json").write_text(json.dumps(
        {"date": "2026-10-08", "games": [{"gameId": 849832, "status": "Pre-Game", "startTime": "2026-10-09T00:00:00Z"}]}))
    now = datetime(2026, 10, 9, 5, 0, tzinfo=timezone.utc)
    s = W.read_state(str(root), "2026-10-09", now, with_export=False)
    assert s["today"] == "2026-10-09" and s["game_state_date"] == "2026-10-08"
    assert s["overdue_started_games"] == ["849832"] and s["game_state_at"] is None
    assert s["kalshi_game_count"] == 0 and s["market_count"] == 0
    out = W.decide(s, {}, now)
    assert wfs(out) == [W.GAME_STATE]
    assert out["dispatch"][0]["inputs"] == {"date": "2026-10-08"}
    assert "2026-10-08 slate" in out["dispatch"][0]["reason"]
    # the feed's own FINAL for that slate ends the dispatch
    slate_dir.joinpath("game_state.json").write_text(json.dumps(
        {"as_of": "2026-10-09T03:10:00Z", "games": {"849832": {"abstract_game_state": "Final", "detailed_state": "Final"}}}))
    s2 = W.read_state(str(root), "2026-10-09", now, with_export=False)
    assert s2["overdue_started_games"] == []
    assert W.GAME_STATE not in wfs(W.decide(s2, {}, now))


def test_stage_f_uses_todays_slate_when_it_is_published(tmp_path):
    root = tmp_path / "data"
    for d, start in (("2026-10-08", "2026-10-08T23:00:00Z"), ("2026-10-09", "2026-10-09T17:00:00Z")):
        p = root / "slates" / d
        p.mkdir(parents=True)
        p.joinpath("authoritative.json").write_text(json.dumps(
            {"date": d, "games": [{"gameId": int(d[-2:]), "status": "Pre-Game", "startTime": start}]}))
    now = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)
    s = W.read_state(str(root), "2026-10-09", now, with_export=False)
    assert s["game_state_date"] == "2026-10-09"
    assert s["overdue_started_games"] == ["9"]   # yesterday's slate is no longer the published one
