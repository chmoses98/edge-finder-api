#!/usr/bin/env python3
"""
scripts/run_standalone_hitter_research.py
=============================================
Hitter Projection Engine Phase 5 -- standalone research-capture
orchestrator.

This is the ONE new entry point the standalone "Kalshi Price Check"
workflow (.github/workflows/kalshi-price-check.yml) calls, immediately
after that workflow's own existing corpus-archive step has already
written an immutable Kalshi snapshot
(data/kalshi_registry_snapshots/kalshi_search_<date>_<time>_standalone.json)
and ingested it into the EdgeLab research corpus
(scripts/edgelab/ingest_market_observations.py). The user never needs to
run scripts/build_hitter_feature_board.py or
scripts/build_hitter_projection_board.py manually -- this script does,
automatically, in order:

  A. mint a research-run id for THIS orchestration (lib.edgelab.ids
     conventions -- same scheme scripts/edgelab/ingest_market_observations.py
     already uses for its own run manifest, not a second identity scheme)
  B0. independently resolve today's MLB schedule + OFFICIAL starting
     lineups directly from the MLB Stats API
     (scripts.fetch_standalone_pregame_context -- see "INDEPENDENCE FROM
     data/slate.json" below), written to an isolated, run-scoped,
     slate-COMPATIBLE artifact this run's own hitter feature/projection
     stages then consume via their existing `slate_path=` parameter
  B. refresh pregame context from ISOLATED-FILE sources only (Savant
     team/batting -> data/savant_team.json, bullpen usage ->
     data/bullpen.json, weather -> data/weather.json)
  C. catch up any of TODAY's own games that have already finished
     (scripts.statcast_completed_game_catchup, final-status-only,
     idempotent -- most of today's own games will legitimately come
     back DEFERRED at a pregame run, that's expected)
  F. build the canonical hitter feature board
     (scripts.build_hitter_feature_board), reading the Stage B0 artifact
  G. build the canonical hitter projection board AGAINST THE EXACT
     IMMUTABLE KALSHI SNAPSHOT this run was given (never the mutable
     data/kalshi_search.json), also reading the Stage B0 artifact --
     scripts.build_hitter_projection_board
  H. write one linked "hitter_research_capture" artifact (run identity,
     summary, and a pointer to the projection-board artifact -- not a
     duplicate copy of its rows, see WHY NO DUPLICATION below) and
     append a research-run manifest row to
     data/edgelab/research_runs/<date>.jsonl

INDEPENDENCE FROM data/slate.json: an earlier version of this
orchestrator read data/slate.json's own lineupConfirmedOfficial state
directly -- meaning the promised "run the standalone Kalshi price
check once -> get real hitter projections" UX silently degraded every
hitter market to LINEUP_UNCONFIRMED whenever the traditional slate
pipeline (fetch-slate.yml/lineup-recheck.yml) hadn't already run that
day. Stage B0 fixes this: it independently resolves the exact same
"today's games + official lineups" information straight from the MLB
Stats API (reusing scripts.fetch_lineups's own already-tested
fetch/parse functions, not a reimplementation) into a NEW, run-scoped,
slate-compatible artifact -- data/pipeline/<date>/<runId>/
standalone_pregame_context.json -- that this run's own hitter
feature/projection stages consume INSTEAD of data/slate.json. This
script NEVER reads, writes, or mutates data/slate.json at any point
(verified in tests/test_hitter_phase5_orchestration.py's
TestStandalonePregameContextIndependence). data/slate.json's
lineup-confirmation fields remain solely owned by the traditional
fetch-slate.yml / lineup-recheck.yml pipeline, which already shares the
`edge-finder-ledger-writer` concurrency group specifically to serialize
concurrent writers to that one file -- this script's own, deliberately
DIFFERENT concurrency group never touches it, so there is no race to
avoid in the first place.

WHY NO ROW DUPLICATION: embedding every hitter_projection_board.json
row a second time inside hitter_research_capture.json would double this
artifact's size for no benefit -- the research capture instead carries
a `projectionBoardPath` pointer plus a compact summary. Both files are
written from the exact same run/snapshot, so a consumer following the
pointer never risks a mismatched pair.

FAIL-SAFE: every stage below is independently try/except-wrapped. A
Kalshi snapshot handed to this script is never at risk from a hitter-
engine failure -- this script writes ONLY new files
(data/pipeline/<date>/hitter_features.json,
data/pipeline/<date>/hitter_projection_board.json,
data/pipeline/<date>/hitter_research_capture.json,
data/edgelab/research_runs/<date>.jsonl, and whatever Statcast/context
files stage B/C touch) and never modifies or deletes the Kalshi
snapshot or its own EdgeLab ingestion output. This script itself always
exits 0 -- a degraded/failed hitter-projection stage is recorded
explicitly in the research-run manifest and printed to stdout, never
silently swallowed and never propagated as a process failure that could
block the calling workflow's own Kalshi-archive commit step.

NO BETS RECORDED: this script never writes to the canonical wager
ledger, staking, recommendation-gate, or settlement paths -- it only
produces research artifacts (Phase 4/5's own explicit scope boundary).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.edgelab import ids, storage  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402
import scripts.build_hitter_feature_board as build_hitter_feature_board  # noqa: E402
import scripts.build_hitter_projection_board as build_hitter_projection_board  # noqa: E402
import scripts.fetch_standalone_pregame_context as fetch_standalone_pregame_context  # noqa: E402
from scripts.statcast_completed_game_catchup import catch_up_todays_slate  # noqa: E402

VERCEL_BASE = "https://edge-finder-api.vercel.app"
DEFAULT_WEATHER_PATH = os.path.join("data", "weather.json")
PYTHON = sys.executable


def _run_subprocess_stage(script_relpath: str, timeout: int = 90) -> dict:
    """Best-effort, non-fatal invocation of an existing fetcher script exactly as the
    production workflows already run it (bare `python3 scripts/xxx.py`, no args) --
    isolates a network/import failure in one fetcher from every other stage."""
    started = time.time()
    try:
        proc = subprocess.run([PYTHON, os.path.join(ROOT_DIR, script_relpath)],
                               cwd=ROOT_DIR, capture_output=True, text=True, timeout=timeout)
        status = "OK" if proc.returncode == 0 else "FAILED"
        return {"script": script_relpath, "status": status, "returncode": proc.returncode,
                "elapsedSeconds": round(time.time() - started, 2),
                "stderr": proc.stderr[-2000:] if status == "FAILED" else None}
    except Exception as e:
        return {"script": script_relpath, "status": "FAILED", "error": f"{type(e).__name__}: {e}",
                "elapsedSeconds": round(time.time() - started, 2)}


def _refresh_weather(weather_path: str = DEFAULT_WEATHER_PATH, timeout: int = 30) -> dict:
    """Mirrors fetch-slate.yml's own `fetch_endpoint weather` curl step exactly (same
    endpoint, same output file) -- there is no dedicated Python fetcher for weather
    anywhere in this repo, so this is a minimal, non-duplicated re-use of the existing
    production endpoint rather than a new data source."""
    started = time.time()
    url = f"{VERCEL_BASE}/api/weather?_cb={int(time.time())}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
        with open(weather_path, "wb") as f:
            f.write(payload)
        return {"script": "weather (api/weather)", "status": "OK", "elapsedSeconds": round(time.time() - started, 2)}
    except Exception as e:
        return {"script": "weather (api/weather)", "status": "FAILED", "error": f"{type(e).__name__}: {e}",
                "elapsedSeconds": round(time.time() - started, 2)}


def refresh_pregame_context() -> dict:
    """Stage B. Every source here writes to an ISOLATED file (data/savant_team.json,
    data/bullpen.json, data/weather.json) -- never data/slate.json (see module docstring's
    SCOPE BOUNDARY). Each source is independent and non-fatal; one failing never blocks
    another or any later stage."""
    results = [
        _run_subprocess_stage(os.path.join("scripts", "fetch_savant_team.py")),
        _run_subprocess_stage(os.path.join("scripts", "fetch_bullpen_usage.py")),
        _refresh_weather(),
    ]
    return {"sources": results, "succeeded": sum(1 for r in results if r["status"] == "OK"),
            "failed": sum(1 for r in results if r["status"] == "FAILED")}


def _mint_run_id(date_str: str, kalshi_snapshot_path: str) -> str:
    content_signature = ids.build_run_content_signature("hitter_projection_standalone", date_str, kalshi_snapshot_path)
    return ids.new_run_id(
        "HITTER_PROJECTION_STANDALONE",
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        github_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
        content_signature=content_signature,
    )


def _write_research_run_record(date_str: str, record: dict) -> None:
    runs_path = storage.partition_path("research_runs", date_str)
    storage.append_records(runs_path, [record], "runId")


def _build_standalone_pregame_context(date_str: str, run_id: str, standalone_context_path=None) -> dict:
    """
    Stage B0. If `standalone_context_path` is already supplied (a test
    fixture, or a prior stage's own output), it's used AS-IS and no
    fetch happens. Otherwise this independently fetches today's MLB
    schedule + official lineups (scripts.fetch_standalone_pregame_context,
    NEVER data/slate.json) and writes it to an isolated, run-scoped path.
    Returns {"path":, "status": "PROVIDED"|"OK"|"FAILED", "error": str|None}.
    """
    if standalone_context_path is not None:
        return {"path": standalone_context_path, "status": "PROVIDED", "error": None}

    path = os.path.join("data", "pipeline", date_str, run_id, "standalone_pregame_context.json")
    try:
        result = fetch_standalone_pregame_context.main(date_str=date_str, output_path=path)
        return {"path": path, "status": "OK", "totalGames": len(result.get("games") or [])}
    except Exception as e:
        return {"path": path, "status": "FAILED", "error": f"{type(e).__name__}: {e}"}


def main(date_str=None, kalshi_snapshot_path=None, standalone_context_path=None, n_sims=None, dry_run=False):
    run_started_at = ids.utc_now_iso()
    stage_timings = {}
    overall_start = time.time()

    date_str = date_str or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    if not kalshi_snapshot_path or not os.path.exists(kalshi_snapshot_path):
        print(f"[run_standalone_hitter_research] No immutable Kalshi snapshot at {kalshi_snapshot_path!r} -- "
              f"cannot honor the immutable-snapshot-linkage invariant, aborting the hitter research capture "
              f"(the caller's own Kalshi corpus archive is completely unaffected by this).")
        return {"status": "NO_SNAPSHOT", "runId": None, "kalshiSnapshotPath": kalshi_snapshot_path}

    run_id = _mint_run_id(date_str, kalshi_snapshot_path)

    # Stage B0 -- standalone pregame context (schedule + official lineups), independent of
    # data/slate.json -- see module docstring's "INDEPENDENCE FROM data/slate.json"
    stage_start = time.time()
    context_stage = _build_standalone_pregame_context(date_str, run_id, standalone_context_path)
    slate_path = context_stage["path"]
    stage_timings["standalonePregameContext"] = round(time.time() - stage_start, 2)

    # Stage B -- pregame context refresh (isolated files only, see docstring)
    stage_start = time.time()
    context_result = refresh_pregame_context()
    stage_timings["pregameContextRefresh"] = round(time.time() - stage_start, 2)

    # Stage C -- Statcast catch-up for today's own games (final-status-only, idempotent),
    # reading gamePks from the Stage B0 standalone context (slate-compatible shape)
    stage_start = time.time()
    try:
        statcast_result = catch_up_todays_slate(slate_path=slate_path)
    except Exception as e:
        statcast_result = {"error": f"{type(e).__name__}: {e}"}
    stage_timings["statcastCatchUp"] = round(time.time() - stage_start, 2)

    # Stage F -- hitter feature board, reading the Stage B0 standalone context
    stage_start = time.time()
    feature_board_summary = None
    feature_board_status = "OK"
    try:
        feature_board_summary = build_hitter_feature_board.main(date_str=date_str, slate_path=slate_path, dry_run=dry_run)
    except Exception as e:
        feature_board_status = "FAILED"
        feature_board_summary = {"error": f"{type(e).__name__}: {e}"}
    stage_timings["hitterFeatureBoard"] = round(time.time() - stage_start, 2)

    # Stage G -- hitter projection board, against the EXACT immutable snapshot, also
    # reading the Stage B0 standalone context
    stage_start = time.time()
    projection_board_summary = None
    projection_status = "OK"
    try:
        kwargs = dict(date_str=date_str, slate_path=slate_path, kalshi_search_path=kalshi_snapshot_path,
                       research_run_id=run_id, dry_run=dry_run)
        if n_sims is not None:
            kwargs["n_sims"] = n_sims
        projection_board_summary = build_hitter_projection_board.main(**kwargs)
    except Exception as e:
        projection_status = "FAILED"
        projection_board_summary = {"error": f"{type(e).__name__}: {e}"}
    stage_timings["hitterProjectionBoard"] = round(time.time() - stage_start, 2)

    overall_status = "SUCCESS" if projection_status == "OK" and feature_board_status == "OK" else "DEGRADED"
    completed_at = ids.utc_now_iso()

    capture = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "HITTER_PROJECTION_STANDALONE",
        "status": overall_status,
        "startedAt": run_started_at,
        "completedAt": completed_at,
        "date": date_str,
        "sourceCapturePath": kalshi_snapshot_path,
        "standalonePregameContext": context_stage,
        "featureBoardStatus": feature_board_status,
        "projectionBoardStatus": projection_status,
        "featureBoardPath": (feature_board_summary or {}).get("artifactPath"),
        "projectionBoardPath": (projection_board_summary or {}).get("artifactPath"),
        "pregameContextRefresh": context_result,
        "statcastCatchUp": statcast_result,
        "summary": {
            "totalHitterMarketsDiscovered": (projection_board_summary or {}).get("totalHitterMarketsDiscovered"),
            "totalRows": (projection_board_summary or {}).get("totalRows"),
            "rowsByProjectionStatus": (projection_board_summary or {}).get("rowsByProjectionStatus"),
            "hittersProjected": (projection_board_summary or {}).get("hittersProjected"),
            "monteCarloSimulationsPerHitter": (projection_board_summary or {}).get("monteCarloSimulationsPerHitter"),
        },
        "stageTimingsSeconds": stage_timings,
        "totalElapsedSeconds": round(time.time() - overall_start, 2),
    }

    capture_artifact_path = None
    if not dry_run:
        try:
            capture_artifact_path = write_stage_artifact(
                "hitter_research_capture", date_str, capture,
                produced_by="scripts/run_standalone_hitter_research.py",
                source_stage="hitter_projection_board",
            )
        except Exception as e:
            print(f"[run_standalone_hitter_research] WARNING: failed to write hitter_research_capture artifact: {e}")

        try:
            _write_research_run_record(date_str, capture)
        except Exception as e:
            print(f"[run_standalone_hitter_research] WARNING: failed to append research_runs manifest row: {e}")

    capture["captureArtifactPath"] = capture_artifact_path
    return capture


def _print_summary(capture: dict) -> None:
    s = capture.get("summary") or {}
    print(f"[run_standalone_hitter_research] runId={capture.get('runId')} status={capture.get('status')} date={capture.get('date')}")
    print(f"  sourceCapturePath: {capture.get('sourceCapturePath')}")
    ctx = capture.get("standalonePregameContext") or {}
    print(f"  standalonePregameContext: status={ctx.get('status')} path={ctx.get('path')} totalGames={ctx.get('totalGames')}")
    print(f"  hitterMarketsDiscovered={s.get('totalHitterMarketsDiscovered')} totalRows={s.get('totalRows')} hittersProjected={s.get('hittersProjected')}")
    print(f"  rowsByProjectionStatus: {s.get('rowsByProjectionStatus')}")
    statcast = capture.get("statcastCatchUp") or {}
    print(f"  statcastCatchUp: newlyArchived={statcast.get('newlyArchived')} alreadyArchived={statcast.get('alreadyArchived')} deferred={statcast.get('deferred')} failed={statcast.get('failed')}")
    print(f"  featureBoardPath={capture.get('featureBoardPath')}")
    print(f"  projectionBoardPath={capture.get('projectionBoardPath')}")
    print(f"  captureArtifactPath={capture.get('captureArtifactPath')}")
    print(f"  stageTimingsSeconds: {capture.get('stageTimingsSeconds')} totalElapsedSeconds={capture.get('totalElapsedSeconds')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    parser.add_argument("--kalshi-snapshot-path", default=None, required=True,
                         help="Path to the immutable Kalshi snapshot this run's hitter projections must be priced against.")
    parser.add_argument("--standalone-context-path", default=None,
                         help="Optional: use an already-built standalone pregame context instead of fetching a fresh one (recovery/replay).")
    parser.add_argument("--n-sims", type=int, default=None)
    args = parser.parse_args()
    result = main(date_str=args.date, kalshi_snapshot_path=args.kalshi_snapshot_path,
                  standalone_context_path=args.standalone_context_path, n_sims=args.n_sims)
    _print_summary(result)
    print(json.dumps(result, indent=2))
    sys.exit(0)
