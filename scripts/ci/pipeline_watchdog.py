#!/usr/bin/env python3
"""
scripts/ci/pipeline_watchdog.py
===============================
Re-arms the MLB current-slate chain when GitHub's best-effort scheduler
does not fire it. Called from .github/workflows/app-export.yml (which runs
after every producer via workflow_run, so it keeps firing even on days
when this repository's crons are throttled).

WHY (2026-10-07, MLB postseason)
--------------------------------
app/latest published 809 markets, 0 events and 0 model prices all day:

* Fetch Slate Data is cron-only (16:00/20:00/22:00 UTC). On this repository
  GitHub starts those crons 3-5h late or not at all: on 10-07 the 16:00 UTC
  cron had still not fired at 19:48 UTC, so data/slates/2026-10-07 never
  existed. On 10-05/10-06 the first slate of the day landed at 22:18 and
  20:45 UTC -- after the postseason's 16:00/18:00 ET first pitches.
* Kalshi capture (capture-snapshots-scheduled.yml, every 30 min from 16:00
  UTC) had not fired since 10:46 UTC, so market data was 9h stale.
* scripts/app_export.py selects the newest date with a markets partition
  (2026-10-07 from the 10:46 capture). With no slate, the Kalshi-discovered
  games carry no MLB gamePk (identity is reconciled against the pipeline's
  normalized slate), so every market was exported without an event and no
  model price could exist.

The fix is not a different model or a looser exporter: it is making the
existing producers run. This module decides, from committed data and the
recent run list of each workflow, which producer (if any) to dispatch.

DECISIONS (all pure, see decide())
----------------------------------
  slate     today (ET) has Kalshi-discovered MLB games (games or markets
            partition) but no data/slates/<today>/authoritative.json, it is
            at/after SLATE_EARLIEST_ET_HOUR, and no Fetch Slate run was
            created in the last SLATE_MIN_GAP_MINUTES -> dispatch
            fetch-slate.yml with date=<today>, unattended=true. unattended
            gives the run EXACTLY the semantics of a scheduled run
            (non-final slate-day gate attempt, SCHEDULED_REFRESH slate
            authority, execution/bet-logging chain skipped) -- an unattended
            trigger never becomes a human's authoritative betting run.
  capture   inside the capture cron window and the newest observation for
            today is older than CAPTURE_STALE_MINUTES (or absent while today
            has games), and no capture run in the last CAPTURE_MIN_GAP_MINUTES
            -> dispatch capture-snapshots-scheduled.yml (read-only price
            capture). If a raw capture for today is already NEWER than the
            newest observation, it was never ingested -- a run dispatched with
            GITHUB_TOKEN does not start its workflow_run successor (EdgeLab
            Market Capture) -- so the ingest, edgelab-capture.yml, is
            dispatched instead, under the same gap and in-flight guard.
  model     today's slate exists, the newest model evaluation for today is
            older than MODEL_STALE_MINUTES (or absent), and no model-snapshot
            run in the last MODEL_MIN_GAP_MINUTES -> dispatch
            model-snapshot-scheduler.yml (prospective evaluations only).

Each gap is the producer's own cron cadence, so the watchdog never runs a
producer more often than its schedule intends; it only replaces missed
firings. Nothing here touches a model, a threshold, a stake or a bet.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from lib.edgelab.production_date import et_date_for_instant  # noqa: E402

FETCH_SLATE = "fetch-slate.yml"
CAPTURE = "capture-snapshots-scheduled.yml"
INGEST = "edgelab-capture.yml"
MODEL = "model-snapshot-scheduler.yml"

#: Postseason starters are announced the day before; 09:00 ET gives the
#: Vercel slate endpoint probables + odds for the earliest (12:00-13:00 ET)
#: playoff first pitches while leaving the regular cron cadence untouched.
SLATE_EARLIEST_ET_HOUR = 9
SLATE_MIN_GAP_MINUTES = 50
CAPTURE_STALE_MINUTES = 40
CAPTURE_MIN_GAP_MINUTES = 25
MODEL_STALE_MINUTES = 45
MODEL_MIN_GAP_MINUTES = 20
#: capture-snapshots-scheduled.yml's own window: 16:00-05:59 UTC.
CAPTURE_WINDOW_UTC_HOURS = frozenset(list(range(16, 24)) + list(range(0, 6)))


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


_FETCHED_AT_RE = re.compile(r'"fetched_at"\s*:\s*"([^"]+)"')


def latest_raw_snapshot_at(data_root, today):
    """fetched_at of the newest raw Kalshi capture for today
    (data/kalshi_registry_snapshots/kalshi_search_<today>_*.json). Only the head of each file
    is read: the capture writes fetched_at among its first keys."""
    folder = os.path.join(data_root, "kalshi_registry_snapshots")
    newest = None
    if not os.path.isdir(folder):
        return None
    for name in os.listdir(folder):
        if not (name.startswith(f"kalshi_search_{today}_") and name.endswith(".json")):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                m = _FETCHED_AT_RE.search(fh.read(2048))
        except OSError:
            continue
        t = _parse(m.group(1)) if m else None
        if t and (newest is None or t > newest):
            newest = t
    return newest


def read_state(data_root, today):
    """Facts about today's production data (pure file reads)."""
    games = list(_iter_jsonl(os.path.join(data_root, "edgelab", "games", f"{today}.jsonl")))
    market_count = sum(1 for _ in _iter_jsonl(os.path.join(data_root, "edgelab", "markets", f"{today}.jsonl")))
    obs_latest = None
    for o in _iter_jsonl(os.path.join(data_root, "edgelab", "observations", f"{today}.jsonl")):
        t = _parse(o.get("capturedAt"))
        if t and (obs_latest is None or t > obs_latest):
            obs_latest = t
    eval_latest = None
    for r in _iter_jsonl(os.path.join(data_root, "edgelab", "model_evaluations", f"{today}.jsonl")):
        t = _parse(r.get("createdAt"))
        if t and (eval_latest is None or t > eval_latest):
            eval_latest = t
    game_keys = {(g.get("awayTeam"), g.get("homeTeam")) for g in games if g.get("awayTeam") and g.get("homeTeam")}
    return {
        "today": today,
        "kalshi_game_count": len(game_keys),
        "market_count": market_count,
        "slate_exists": os.path.exists(os.path.join(data_root, "slates", today, "authoritative.json")),
        "latest_observation_at": obs_latest,
        "latest_raw_snapshot_at": latest_raw_snapshot_at(data_root, today),
        "latest_model_evaluation_at": eval_latest,
    }


def _recent(runs, now, minutes):
    """True when any run of the workflow was created within the last `minutes`
    or is still queued/in progress (a stuck run must not be stacked on)."""
    cutoff = now - timedelta(minutes=minutes)
    for r in runs or []:
        if str(r.get("status") or "").lower() in ("queued", "in_progress", "waiting", "requested", "pending"):
            return True
        created = _parse(r.get("createdAt") or r.get("created_at"))
        if created and created >= cutoff:
            return True
    return False


def decide(state, runs_by_workflow, now):
    """Pure. -> list of {workflow, inputs, reason} dispatches plus skip reasons."""
    out, notes = [], []
    today = state["today"]
    has_games = state["kalshi_game_count"] > 0 or state["market_count"] > 0

    if not has_games:
        notes.append(f"{today}: no Kalshi-discovered MLB game or market yet -- nothing to arm")
        return {"dispatch": out, "notes": notes}

    if not state["slate_exists"]:
        if _et_hour(now) < SLATE_EARLIEST_ET_HOUR:
            notes.append(f"slate: {today} missing but before {SLATE_EARLIEST_ET_HOUR}:00 ET")
        elif _recent(runs_by_workflow.get(FETCH_SLATE), now, SLATE_MIN_GAP_MINUTES):
            notes.append(f"slate: {today} missing; a Fetch Slate run is in flight or <{SLATE_MIN_GAP_MINUTES}m old")
        else:
            out.append({"workflow": FETCH_SLATE, "inputs": {"date": today, "unattended": "true"},
                        "reason": f"no data/slates/{today}/authoritative.json while {state['kalshi_game_count']} "
                                  f"Kalshi-discovered game(s) / {state['market_count']} market(s) exist"})
    else:
        latest = state["latest_model_evaluation_at"]
        stale = latest is None or (now - latest) > timedelta(minutes=MODEL_STALE_MINUTES)
        if not stale:
            notes.append("model: today's evaluations are fresh")
        elif _recent(runs_by_workflow.get(MODEL), now, MODEL_MIN_GAP_MINUTES):
            notes.append(f"model: stale but a model-snapshot run is in flight or <{MODEL_MIN_GAP_MINUTES}m old")
        else:
            age = "none yet" if latest is None else f"{int((now - latest).total_seconds() // 60)}m old"
            out.append({"workflow": MODEL, "inputs": {}, "reason": f"newest {today} model evaluation {age}"})

    if now.hour not in CAPTURE_WINDOW_UTC_HOURS:
        notes.append("capture: outside the capture window")
    else:
        latest = state["latest_observation_at"]
        stale = latest is None or (now - latest) > timedelta(minutes=CAPTURE_STALE_MINUTES)
        if not stale:
            notes.append("capture: market observations are fresh")
        else:
            age = "none yet" if latest is None else f"{int((now - latest).total_seconds() // 60)}m old"
            raw = state.get("latest_raw_snapshot_at")
            if raw is not None and (latest is None or raw > latest):
                # A newer raw capture exists but was never ingested: a capture dispatched with
                # GITHUB_TOKEN does not start its workflow_run successor (EdgeLab Market Capture),
                # so re-arm the ingest itself rather than capturing again.
                if _recent(runs_by_workflow.get(INGEST), now, CAPTURE_MIN_GAP_MINUTES):
                    notes.append(f"capture: raw snapshot {raw.isoformat()} not ingested; an ingest run is in "
                                 f"flight or <{CAPTURE_MIN_GAP_MINUTES}m old")
                else:
                    out.append({"workflow": INGEST, "inputs": {"date": today},
                                "reason": f"newest {today} observation {age}; raw snapshot {raw.isoformat()} not ingested"})
            elif _recent(runs_by_workflow.get(CAPTURE), now, CAPTURE_MIN_GAP_MINUTES):
                notes.append(f"capture: stale but a capture run is in flight or <{CAPTURE_MIN_GAP_MINUTES}m old")
            else:
                out.append({"workflow": CAPTURE, "inputs": {"date": today}, "reason": f"newest {today} observation {age}"})
    return {"dispatch": out, "notes": notes}


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry-run", action="store_true", help="decide and print; never call gh")
    args = ap.parse_args(argv)
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    today = et_date_for_instant(now)
    state = read_state(args.data_root, today)
    runs = {}
    for wf in (FETCH_SLATE, CAPTURE, INGEST, MODEL):
        listed = [] if args.dry_run else _gh_runs(wf)
        if listed is None:
            # Unknown run state: mark as in flight so nothing is stacked on top of it.
            print(f"gh run list {wf}: UNKNOWN -> failed closed (treated as in flight, no dispatch)")
            listed = [{"status": "in_progress"}]
        elif not args.dry_run:
            print(f"gh run list {wf}: {len(listed)} run(s), newest created "
                  f"{max((r.get('createdAt') or '' for r in listed), default='') or 'n/a'}")
        runs[wf] = listed
    verdict = decide(state, runs, now)
    printable = dict(state)
    for k in ("latest_observation_at", "latest_raw_snapshot_at", "latest_model_evaluation_at"):
        printable[k] = printable[k].isoformat() if printable[k] else None
    print(json.dumps({"state": printable, **verdict}, indent=2))
    failures = 0
    for item in verdict["dispatch"]:
        print(f"::notice title=pipeline watchdog::dispatch {item['workflow']} {item['inputs']} -- {item['reason']}")
        if args.dry_run:
            continue
        try:
            _dispatch(item)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"::warning::pipeline watchdog dispatch of {item['workflow']} failed: {exc}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Pipeline watchdog\n\n")
            for item in verdict["dispatch"]:
                fh.write(f"- dispatched `{item['workflow']}` {item['inputs']}: {item['reason']}\n")
            for n in verdict["notes"]:
                fh.write(f"- {n}\n")
    # A watchdog failure must never fail the export it rides on.
    return 0


if __name__ == "__main__":
    sys.exit(main())
