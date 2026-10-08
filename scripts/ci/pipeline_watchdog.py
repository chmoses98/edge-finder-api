#!/usr/bin/env python3
"""
scripts/ci/pipeline_watchdog.py
===============================
State reconciliation for the MLB current-slate pipeline. ONE implementation,
two callers:

  --host conductor   .github/workflows/mlb-pipeline-conductor.yml evaluates every
                     few minutes in a bounded loop and chains its own successor:
                     it owns stages A-E below.
  --host app-export  .github/workflows/app-export.yml (runs after producers) is a
                     liveness backstop only: it revives a stopped conductor chain
                     and otherwise dispatches nothing, so the two callers never
                     race. With the conductor switched off (MLB_CONDUCTOR_ENABLED
                     = false) it falls back to reconciling stages A-D itself.
  --successor        last step of a conductor run: request its ONE successor.

WHY (2026-10-07, MLB postseason)
--------------------------------
GitHub started this repository's crons hours late or not at all (Fetch Slate's
16:00 UTC cron had not fired at 19:48 UTC), so app/latest published 0 events /
0 model prices with four playoff games on Kalshi. And a run dispatched with
GITHUB_TOKEN never starts its workflow_run successors: captures dispatched by
github-actions[bot] wrote raw snapshots EdgeLab never ingested. So pipeline
advancement cannot rest on cron or on workflow_run; it is decided here from the
committed artifacts themselves (workflow_run stays as a fast path).

STAGES (pure decide(); every dispatch is guarded by the producer's own run list:
a queued/in-progress run or one created within its gap blocks it, and an
unreadable run list counts as in flight -- unknown state never dispatches)
---------------------------------------------------------------------------
  A SLATE    today (ET) has Kalshi-discovered games/markets, no
             data/slates/<today>/authoritative.json, at/after 09:00 ET
             -> fetch-slate.yml date=<today> unattended=true (scheduled-run
             semantics: SCHEDULED_REFRESH authority, no risk gate / bet logging).
  B CAPTURE  inside the capture window (16:00-05:59 UTC, the capture cron's own)
             and the newest raw Kalshi capture that carries markets is missing
             or older than CAPTURE_STALE_MINUTES (a FAILED / empty capture never
             counts as fresh), and the newest capture is not awaiting ingest
             -> capture-snapshots-scheduled.yml. Not gated on games having
             started: complete market archival follows the capture window.
  C INGEST   the newest raw capture for today (the file EdgeLab's ingest selects)
             carries markets and is not the source of any EdgeLab observation
             (observation provenance.sourceFile) -> edgelab-capture.yml.
             Takes precedence over B: an un-ingested capture is never answered
             with another capture.
  D MODEL    today's slate exists, at least one slate game is still genuinely
             pregame (app_export's authoritative status + first-pitch gate), the
             newest model evaluation is stale, no recent model run
             -> model-snapshot-scheduler.yml. With no pregame game left the date
             is closed for snapshot production.
  E EXPORT   the newest commit touching today's upstream artifacts is not
             contained in the commit app/latest/manifest.json was built from
             -> app-export.yml (conductor only; unknown git state never dispatches).

Each gap equals the producer's own designed cadence. Nothing here touches a
model, a threshold, a stake or a bet.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.edgelab.production_date import et_date_for_instant  # noqa: E402

FETCH_SLATE = "fetch-slate.yml"
CAPTURE = "capture-snapshots-scheduled.yml"
INGEST = "edgelab-capture.yml"
MODEL = "model-snapshot-scheduler.yml"
EXPORT = "app-export.yml"
CONDUCTOR = "mlb-pipeline-conductor.yml"
PRODUCERS = (FETCH_SLATE, CAPTURE, INGEST, MODEL, EXPORT)

#: Postseason starters are announced the day before; 09:00 ET gives the slate
#: endpoint probables + odds before the earliest (12:00-13:00 ET) first pitches.
SLATE_EARLIEST_ET_HOUR = 9
SLATE_MIN_GAP_MINUTES = 50
CAPTURE_STALE_MINUTES = 40
CAPTURE_MIN_GAP_MINUTES = 25
MODEL_STALE_MINUTES = 45
MODEL_MIN_GAP_MINUTES = 20
EXPORT_MIN_GAP_MINUTES = 10
#: a conductor run lasts ~50 min; none active and none created this long ago = chain stopped
CONDUCTOR_ALIVE_MINUTES = 70
#: capture-snapshots-scheduled.yml's own window: 16:00-05:59 UTC.
CAPTURE_WINDOW_UTC_HOURS = frozenset(list(range(16, 24)) + list(range(0, 6)))
KILL_VALUES = ("false", "0", "no", "off", "disabled")
ACTIVE_STATUSES = ("queued", "in_progress", "waiting", "requested", "pending")


def _parse(ts):
    if not ts:
        return None
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def _et_hour(now):
    return now.astimezone(ZoneInfo("America/New_York")).hour


def _iter_jsonl(path):
    for candidate in (path, path + ".gz"):
        if os.path.exists(candidate):
            opener = gzip.open if candidate.endswith(".gz") else open
            with opener(candidate, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
            return


def conductor_enabled(raw):
    return (raw or "").strip().lower() not in KILL_VALUES


# ------------------------------------------------------------------ state (file / git reads)

_TOTAL_MARKETS_RE = re.compile(r'"total_markets"\s*:\s*(\d+)')


def _capture_head(path):
    try:
        with open(path, encoding="utf-8") as fh:
            m = _TOTAL_MARKETS_RE.search(fh.read(4096))
    except OSError:
        return None
    return int(m.group(1)) if m else None


def raw_captures(data_root, today):
    """Today's raw Kalshi captures as EdgeLab's ingest sees them: the SAME glob and fetched_at
    ordering (lib.edgelab.market_universe.find_snapshots_for_date), so "the newest capture" here
    is exactly the file `ingest_market_observations.py` ingests.
    -> {"newest": repo-relative path | None, "newest_nonempty": bool | None,
        "last_success_at": fetched_at of the newest capture that carries markets | None}"""
    from lib.edgelab.market_universe import find_snapshots_for_date, snapshot_captured_at
    folder = os.path.join(data_root, "kalshi_registry_snapshots")
    files = find_snapshots_for_date(today, folder) if os.path.isdir(folder) else []
    out = {"newest": None, "newest_nonempty": None, "last_success_at": None}
    if not files:
        return out
    out["newest"] = f"data/kalshi_registry_snapshots/{os.path.basename(files[-1])}"
    out["newest_nonempty"] = (_capture_head(files[-1]) or 0) > 0
    for path in reversed(files):
        if (_capture_head(path) or 0) > 0:
            out["last_success_at"] = _parse(snapshot_captured_at(path))
            break
    return out


_APP_EXPORT = None


def _app_export():
    """scripts/app_export.py: the authoritative event status mapping and first-pitch gate."""
    global _APP_EXPORT
    if _APP_EXPORT is None:
        spec = importlib.util.spec_from_file_location("app_export", os.path.join(REPO_ROOT, "scripts", "app_export.py"))
        mod = sys.modules.get("app_export")
        if mod is None:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["app_export"] = mod
            spec.loader.exec_module(mod)
        _APP_EXPORT = mod
    return _APP_EXPORT


def pregame_horizon(slate, now):
    """{status: count} of the slate's games through the SAME status mapping and first-pitch
    gate the app export uses (a stale "Pre-Game" past first pitch is LIVE; postponed /
    cancelled / final are never pregame). Only SCHEDULED is pregame."""
    ae = _app_export()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {}
    for g in (slate or {}).get("games") or []:
        start = g.get("startTime")
        status = ae._event_status(g.get("status"))
        if start:
            status, _basis = ae._time_gate(status, ae._ts(start), "SCHEDULED", now_iso)
        else:
            status = "UNKNOWN"
        counts[status] = counts.get(status, 0) + 1
    return counts


#: today's inputs to app/latest; a commit touching any of them after the exported one is unpublished state
def upstream_paths(today):
    p = []
    for entity in ("observations", "model_evaluations", "hitter_projection_snapshots", "games", "markets",
                   "recommendations", "settlements"):
        p += [f"data/edgelab/{entity}/{today}.jsonl", f"data/edgelab/{entity}/{today}.jsonl.gz"]
    return p + [f"data/slates/{today}", f"data/kalshi/discovery/{today}.json"]


def export_state(repo_root, today, runner=subprocess.run):
    """{"stale": True/False/None, ...}: None when git cannot answer (shallow history, no git)."""
    def git(*args):
        r = runner(["git", "-C", repo_root, *args], capture_output=True, text=True, timeout=60)
        return r.returncode, (r.stdout or "").strip()

    try:
        with open(os.path.join(repo_root, "app", "latest", "manifest.json"), encoding="utf-8") as fh:
            exported = json.load(fh).get("commit_sha")
    except (OSError, ValueError):
        return {"stale": None, "reason": "app/latest/manifest.json unreadable"}
    try:
        rc, upstream = git("log", "-1", "--format=%H", "--", *upstream_paths(today))
        if rc != 0:
            return {"stale": None, "reason": "git log failed"}
        if not upstream:
            return {"stale": False, "reason": f"no upstream commit for {today} in the fetched history",
                    "exported_from": exported}
        if not exported:
            return {"stale": True, "reason": "manifest carries no commit_sha", "upstream": upstream}
        rc_present, _ = git("cat-file", "-e", f"{exported}^{{commit}}")
        if rc_present != 0:
            return {"stale": None, "reason": f"exported commit {exported[:9]} not in the fetched history"}
        rc_anc, _ = git("merge-base", "--is-ancestor", upstream, exported)
    except Exception as exc:  # noqa: BLE001 -- unknown git state never dispatches
        return {"stale": None, "reason": f"git unavailable: {exc}"}
    if rc_anc == 0:
        return {"stale": False, "reason": "app/latest was built from a commit containing today's newest upstream commit",
                "upstream": upstream, "exported_from": exported}
    return {"stale": True, "reason": f"upstream commit {upstream[:9]} is newer than the export's source {exported[:9]}",
            "upstream": upstream, "exported_from": exported}


def read_state(data_root, today, now, *, repo_root=None, with_export=True, runner=subprocess.run):
    """Facts about today's production data (file reads, plus git for the export stage)."""
    games = list(_iter_jsonl(os.path.join(data_root, "edgelab", "games", f"{today}.jsonl")))
    market_count = sum(1 for _ in _iter_jsonl(os.path.join(data_root, "edgelab", "markets", f"{today}.jsonl")))
    obs_latest, ingested = None, set()
    for o in _iter_jsonl(os.path.join(data_root, "edgelab", "observations", f"{today}.jsonl")):
        t = _parse(o.get("capturedAt"))
        if t and (obs_latest is None or t > obs_latest):
            obs_latest = t
        src = (o.get("provenance") or {}).get("sourceFile")
        if src:
            ingested.add(src)
    eval_latest = None
    for r in _iter_jsonl(os.path.join(data_root, "edgelab", "model_evaluations", f"{today}.jsonl")):
        t = _parse(r.get("createdAt"))
        if t and (eval_latest is None or t > eval_latest):
            eval_latest = t
    slate_path = os.path.join(data_root, "slates", today, "authoritative.json")
    slate = None
    if os.path.exists(slate_path):
        try:
            with open(slate_path, encoding="utf-8") as fh:
                slate = json.load(fh)
        except (OSError, ValueError):
            slate = {}
    horizon = pregame_horizon(slate, now) if slate is not None else {}
    raw = raw_captures(data_root, today)
    game_keys = {(g.get("awayTeam"), g.get("homeTeam")) for g in games if g.get("awayTeam") and g.get("homeTeam")}
    state = {
        "today": today,
        "kalshi_game_count": len(game_keys),
        "market_count": market_count,
        "slate_exists": slate is not None,
        "slate_status_counts": horizon,
        "pregame_game_count": horizon.get("SCHEDULED", 0),
        # what ingest would read next, and whether EdgeLab already holds observations from it
        # (observation provenance.sourceFile); None when it is a failed / empty capture
        "latest_raw_snapshot": raw["newest"],
        "latest_raw_snapshot_ingested": (None if raw["newest"] is None or not raw["newest_nonempty"]
                                         else raw["newest"] in ingested),
        # freshness of the archive = the newest capture that actually carries markets
        "latest_raw_snapshot_at": raw["last_success_at"],
        "latest_observation_at": obs_latest,
        "latest_model_evaluation_at": eval_latest,
    }
    if with_export:
        state["export"] = export_state(repo_root or REPO_ROOT, today, runner)
    return state


# ------------------------------------------------------------------ decision (pure)

def _recent(runs, now, minutes):
    """True when a run of the workflow is queued/in progress or was created within
    `minutes`. runs=None (unreadable) is treated as in flight."""
    if runs is None:
        return True
    cutoff = now - timedelta(minutes=minutes)
    for r in runs:
        if str(r.get("status") or "").lower() in ACTIVE_STATUSES:
            return True
        created = _parse(r.get("createdAt") or r.get("created_at"))
        if created and created >= cutoff:
            return True
    return False


def _age(now, t):
    return "none yet" if t is None else f"{int((now - t).total_seconds() // 60)}m old"


def decide(state, runs_by_workflow, now, *, stages="ABCDE"):
    """Pure. -> {"dispatch": [{workflow, inputs, reason}], "notes": [...]}.
    runs_by_workflow: {workflow: [runs] | None(unknown)}; a missing key means "no runs"."""
    out, notes = [], []
    runs = lambda wf: runs_by_workflow.get(wf, [])  # noqa: E731
    today = state["today"]

    if not (state["kalshi_game_count"] > 0 or state["market_count"] > 0):
        notes.append(f"{today}: no Kalshi-discovered MLB game or market -- nothing to reconcile")
        return {"dispatch": out, "notes": notes}

    if "A" in stages and not state["slate_exists"]:
        if _et_hour(now) < SLATE_EARLIEST_ET_HOUR:
            notes.append(f"slate: {today} missing but before {SLATE_EARLIEST_ET_HOUR}:00 ET")
        elif _recent(runs(FETCH_SLATE), now, SLATE_MIN_GAP_MINUTES):
            notes.append(f"slate: {today} missing; a Fetch Slate run is in flight, <{SLATE_MIN_GAP_MINUTES}m old or unknown")
        else:
            out.append({"workflow": FETCH_SLATE, "inputs": {"date": today, "unattended": "true"},
                        "reason": f"no data/slates/{today}/authoritative.json while {state['kalshi_game_count']} "
                                  f"Kalshi-discovered game(s) / {state['market_count']} market(s) exist"})

    if "D" in stages and state["slate_exists"]:
        latest = state["latest_model_evaluation_at"]
        if state.get("pregame_game_count", 0) <= 0:
            notes.append(f"model: no remaining pregame MLB events -- snapshot production closed for {today} "
                         f"(slate statuses {state.get('slate_status_counts')})")
        elif latest is not None and (now - latest) <= timedelta(minutes=MODEL_STALE_MINUTES):
            notes.append("model: today's evaluations are fresh")
        elif _recent(runs(MODEL), now, MODEL_MIN_GAP_MINUTES):
            notes.append(f"model: stale but a model-snapshot run is in flight, <{MODEL_MIN_GAP_MINUTES}m old or unknown")
        else:
            out.append({"workflow": MODEL, "inputs": {},
                        "reason": f"newest {today} model evaluation {_age(now, latest)}; "
                                  f"{state['pregame_game_count']} game(s) still pregame"})

    raw_path, raw_at = state.get("latest_raw_snapshot"), state.get("latest_raw_snapshot_at")
    if "C" in stages and raw_path and state.get("latest_raw_snapshot_ingested") is False:
        # Never answered with another capture: a capture dispatched with GITHUB_TOKEN does not
        # start its workflow_run successor (EdgeLab Market Capture), so the ingest is re-armed.
        if _recent(runs(INGEST), now, CAPTURE_MIN_GAP_MINUTES):
            notes.append(f"ingest: {raw_path} not ingested; an ingest run is in flight, "
                         f"<{CAPTURE_MIN_GAP_MINUTES}m old or unknown")
        else:
            out.append({"workflow": INGEST, "inputs": {"date": today},
                        "reason": f"{raw_path} (fetched {raw_at.isoformat() if raw_at else 'unknown'}) "
                                  f"is not the source of any observation"})
    elif "B" in stages:
        if now.hour not in CAPTURE_WINDOW_UTC_HOURS:
            notes.append("capture: outside the capture window")
        elif raw_at is not None and (now - raw_at) <= timedelta(minutes=CAPTURE_STALE_MINUTES):
            notes.append("capture: newest raw capture is fresh and ingested")
        elif _recent(runs(CAPTURE), now, CAPTURE_MIN_GAP_MINUTES):
            notes.append(f"capture: stale but a capture run is in flight, <{CAPTURE_MIN_GAP_MINUTES}m old or unknown")
        else:
            out.append({"workflow": CAPTURE, "inputs": {"date": today},
                        "reason": f"newest {today} raw capture {_age(now, raw_at)}"})

    if "E" in stages:
        ex = state.get("export") or {}
        if ex.get("stale") is None:
            notes.append(f"export: state unknown ({ex.get('reason')}) -- no dispatch")
        elif not ex["stale"]:
            notes.append("export: app/latest is current with today's committed upstream state")
        elif _recent(runs(EXPORT), now, EXPORT_MIN_GAP_MINUTES):
            notes.append(f"export: stale but an app export is in flight, <{EXPORT_MIN_GAP_MINUTES}m old or unknown")
        else:
            out.append({"workflow": EXPORT, "inputs": {}, "reason": ex.get("reason")})
    return {"dispatch": out, "notes": notes}


def decide_app_export_host(state, runs_by_workflow, now, enabled):
    """app-export.yml's role: revive a stopped conductor chain; reconcile producers itself only
    when the conductor is switched off. While the conductor is alive it dispatches nothing."""
    if not enabled:
        verdict = decide(state, runs_by_workflow, now, stages="ABCD")
        verdict["notes"].insert(0, "conductor switched off (MLB_CONDUCTOR_ENABLED): app export reconciles stages A-D")
        return verdict
    conductor_runs = runs_by_workflow.get(CONDUCTOR, [])
    if _recent(conductor_runs, now, CONDUCTOR_ALIVE_MINUTES):
        return {"dispatch": [], "notes": ["conductor alive (or run state unknown): app export defers to it"]}
    return {"dispatch": [{"workflow": CONDUCTOR, "inputs": {},
                          "reason": f"no conductor run active or created in the last {CONDUCTOR_ALIVE_MINUTES}m"}],
            "notes": []}


# ------------------------------------------------------------------ gh I/O

def _gh_runs(workflow):
    try:
        raw = subprocess.run(
            ["gh", "run", "list", "--workflow", workflow, "--limit", "15",
             "--json", "createdAt,status,conclusion,event,databaseId"],
            check=True, capture_output=True, text=True, timeout=60).stdout
        return json.loads(raw or "[]")
    except Exception as exc:  # noqa: BLE001 -- unknown run state => do not dispatch
        print(f"::warning::pipeline watchdog could not list {workflow} runs: {exc}")
        return None


def _dispatch(item):
    cmd = ["gh", "workflow", "run", item["workflow"], "--ref", "main"]
    for k, v in item["inputs"].items():
        cmd += ["-f", f"{k}={v}"]
    subprocess.run(cmd, check=True, timeout=60)


def _chain_successor():
    path = os.path.join(REPO_ROOT, "scripts", "research", "mrv_collector", "chain_successor.py")
    spec = importlib.util.spec_from_file_location("chain_successor", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def request_successor(env, runner=subprocess.run, dry_run=False):
    """The repository's established chain pattern (scripts/research/mrv_collector/chain_successor.py):
    kill switch -> none; another conductor run already queued/active -> it IS the successor;
    otherwise exactly one `gh workflow run`. The non-cancelling concurrency group admits one
    running + one pending conductor, so a redundant request can never overlap two."""
    cs = _chain_successor()
    d = cs.decide(env.get("MLB_CONDUCTOR_ENABLED", ""), cs.list_runs(CONDUCTOR, runner), env.get("GITHUB_RUN_ID"), "main")
    if d["action"] == "DISPATCH":
        d["command"] = ["gh", "workflow", "run", CONDUCTOR, "--ref", "main"]
        if not dry_run:
            d["dispatchReturnCode"] = runner(d["command"]).returncode
    print(json.dumps(d, sort_keys=True))
    return 0 if d.get("dispatchReturnCode", 0) == 0 else 1


def _printable(state):
    out = {}
    for k, v in state.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--repo-root", default=REPO_ROOT)
    ap.add_argument("--host", choices=("conductor", "app-export"), default="conductor")
    ap.add_argument("--successor", action="store_true", help="request the conductor's successor and exit")
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry-run", action="store_true", help="decide and print; never call gh")
    args = ap.parse_args(argv)
    if args.successor:
        return request_successor(os.environ, dry_run=args.dry_run)

    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    today = et_date_for_instant(now)
    enabled = conductor_enabled(os.environ.get("MLB_CONDUCTOR_ENABLED"))
    state = read_state(args.data_root, today, now, repo_root=args.repo_root, with_export=(args.host == "conductor"))
    workflows = PRODUCERS if args.host == "conductor" else ((CONDUCTOR,) if enabled else PRODUCERS[:4])
    runs = {}
    for wf in workflows:
        listed = [] if args.dry_run else _gh_runs(wf)
        if listed is None:
            print(f"gh run list {wf}: UNKNOWN -> failed closed (treated as in flight, no dispatch)")
        elif not args.dry_run:
            print(f"gh run list {wf}: {len(listed)} run(s), newest created "
                  f"{max((r.get('createdAt') or '' for r in listed), default='') or 'n/a'}")
        runs[wf] = listed
    if args.host == "conductor":
        verdict = decide(state, runs, now)
    else:
        verdict = decide_app_export_host(state, runs, now, enabled)
    print(json.dumps({"host": args.host, "state": _printable(state), **verdict}, indent=2, default=str))
    failures = 0
    for item in verdict["dispatch"]:
        print(f"::notice title=pipeline watchdog::dispatch {item['workflow']} {item['inputs']} -- {item['reason']}")
        if args.dry_run:
            continue
        try:
            _dispatch(item)
        except Exception as exc:  # noqa: BLE001 -- visible, never fatal; the next evaluation retries
            failures += 1
            print(f"::warning title=pipeline watchdog::dispatch of {item['workflow']} FAILED: {exc}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Pipeline watchdog ({args.host}, {now.strftime('%H:%M:%SZ')})\n\n")
            for item in verdict["dispatch"]:
                fh.write(f"- dispatched `{item['workflow']}` {item['inputs']}: {item['reason']}\n")
            for n in verdict["notes"]:
                fh.write(f"- {n}\n")
            if failures:
                fh.write(f"- **{failures} dispatch(es) failed** (see warnings)\n")
    # Never fatal: a failed evaluation or dispatch must not stop the conductor loop or an export.
    return 0


if __name__ == "__main__":
    sys.exit(main())
