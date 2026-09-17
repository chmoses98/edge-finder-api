#!/usr/bin/env python3
"""
scripts/fetch_single_game.py
================================
First-class SINGLE-GAME data fetch: everything needed to handicap ONE
matchup, in one self-contained artifact, without running (or disturbing)
the full slate pipeline.

    data/single_game/<YYYY-MM-DD>/<gamePk>.json      the artifact
    data/single_game/<YYYY-MM-DD>/index.json         that date's artifacts
    data/single_game/latest.json                     newest-first pointer

WHY A SEPARATE PATH
-------------------
data/slate.json is the authoritative betting slate for the whole day and
is guarded by scripts/protect_slate.py. A one-game pseudo-slate written
there would be indistinguishable from a real slate to every downstream
consumer (model-snapshot-scheduler, risk_gate, validate_slate_final,
CLV tracking) while silently containing 1/15th of the day. Nothing in
this script writes data/slate.json, bets.json, marketLedger, or any
pipeline artifact -- it only ever reads them.

THE ARCHIVE INVARIANT COMES FIRST
---------------------------------
This repository archives the COMPLETE MLB Kalshi market universe every
capture, including markets that never make a slate. A single-game fetch
must not weaken that. So the order here is fixed and asserted, never
assumed:

    1. fetch the FULL /api/kalshisearch universe (one call)
    2. write it VERBATIM and UNFILTERED to
       data/kalshi_registry_snapshots/kalshi_search_<date>_<HHMM>.json,
       the same production archive path/convention
       capture-snapshots-scheduled.yml uses
    3. read the archived file back and assert its market count equals
       what was fetched  (verify_archive_is_complete)
    4. ONLY THEN filter down to the requested game

A failure at any of steps 2-3 aborts before any filtering happens. The
archive write is deliberately the TIMESTAMPED file only -- never the
primary `kalshi_search_<date>.json` -- so this additive capture can
never overwrite the canonical dated snapshot other jobs depend on.

REUSE, NOT A MINI-MODEL
-----------------------
Nothing about markets is re-derived here. The full-slate pipeline's own
modules do the work, unchanged:
  * schedule/identity      lib.edgelab.mlb_schedule (the one canonical
                           MLB schedule adapter) + lib.single_game_selector
  * market normalization   lib.kalshi_price_check.normalize_batch
  * single-game safety gate lib.kalshi_price_check.apply_strict_game_registry
                           (allowlist-only; a market that is not a real
                           single-game MLB contract can never be included)
  * game filtering         lib.kalshi_price_check.apply_filters(games=...)
  * handicapping context   THIS game's block, verbatim, out of the
                           canonical slate artifact (data/slate.json when
                           its date matches, else
                           data/pipeline/<date>/normalized_slate.json),
                           marketLedger included
  * full market coverage   data/pipeline/<date>/full_market_coverage.json
                           / data/kalshi/discovery/<date>_coverage.json
                           rows for this game, when they exist

There is no probability, edge, price adjustment or recommendation
computed anywhere in this file. Context that does not exist is reported
as absent, with its own freshness/staleness note -- never fabricated and
never silently backfilled from another date.

Usage:
    python3 scripts/fetch_single_game.py --date 2026-09-17 --game "Yankees vs Red Sox"
    python3 scripts/fetch_single_game.py --game MIL            # date defaults to today ET
    python3 scripts/fetch_single_game.py --date 2026-09-17 --game-pk 823334
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import mlb_schedule
from lib.kalshi_price_check import (
    apply_filters,
    apply_strict_game_registry,
    group_by_game,
    normalize_batch,
)
from lib.single_game_selector import describe_candidate, resolve_single_game

SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")
SINGLE_GAME_DIR = os.path.join("data", "single_game")
ARTIFACT_SCHEMA_VERSION = "1"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AMBIGUOUS = 3  # a distinct code so a workflow can say "you must pick a gamePk"


def today_et():
    return datetime.now(tz=ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def utc_now_iso():
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Step 1-3: complete, unfiltered archival capture ─────────────────────

def extract_markets(payload):
    """Pure. The market list out of a /api/kalshisearch payload, in both
    shapes the endpoint has historically returned (list or dict-of-dicts)
    -- same tolerance capture-snapshots-scheduled.yml already applies."""
    markets = (payload or {}).get("markets", [])
    if isinstance(markets, dict):
        markets = list(markets.values())
    return markets if isinstance(markets, list) else []


def archive_full_universe(payload, date, *, snapshot_dir=SNAPSHOT_DIR, now=None):
    """
    Write the COMPLETE, UNFILTERED payload to the production snapshot
    archive and return (path, market_count). Timestamped filename only --
    never the primary `kalshi_search_<date>.json`, which belongs to the
    scheduled capture workflow.
    """
    stamp = (now or datetime.now(tz=timezone.utc)).strftime("%H%M")
    os.makedirs(snapshot_dir, exist_ok=True)
    path = os.path.join(snapshot_dir, f"kalshi_search_{date}_{stamp}.json")
    enriched = dict(payload or {})
    enriched.setdefault("date", date)
    enriched["fetched_at"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    enriched["capture_source"] = "fetch_single_game"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, separators=(",", ":"))
    return path, len(extract_markets(enriched))


def verify_archive_is_complete(path, expected_count):
    """
    Read the archived file back and prove it holds every market that was
    fetched. Returns (ok, reason). This is the gate that makes "archive
    first, filter second" a checked invariant rather than an intention:
    filtering never starts unless this passes.
    """
    if expected_count == 0:
        return False, "the upstream Kalshi universe came back empty -- refusing to treat that as a valid capture"
    try:
        with open(path, encoding="utf-8") as f:
            archived = json.load(f)
    except (OSError, ValueError) as exc:
        return False, f"archived snapshot {path} is unreadable: {exc}"
    actual = len(extract_markets(archived))
    if actual != expected_count:
        return False, (
            f"archived snapshot {path} holds {actual} markets but {expected_count} were fetched -- "
            f"the raw capture must be the complete unfiltered universe"
        )
    return True, None


def latest_snapshot_for_date(date, snapshot_dir=SNAPSHOT_DIR):
    """Newest already-archived snapshot for `date` (used by --source
    snapshot / offline reruns). None when the date has none."""
    paths = sorted(glob.glob(os.path.join(snapshot_dir, f"kalshi_search_{date}*.json")))
    return paths[-1] if paths else None


# ── Step 4: everything about ONE game ───────────────────────────────────

def select_game_markets(raw_markets, game, date, *, retrieved_at=None, source_used="live"):
    """
    The canonical normalize -> strict single-game registry gate ->
    game filter chain, reused unchanged. Returns
    (kept, excluded_for_this_game, stage_report, status_counts).

    `excluded_for_this_game` is the audit trail: markets the strict
    registry gate rejected that nonetheless name this matchup. They are
    reported separately, never mixed into the usable market list and
    never silently dropped.
    """
    records, status_counts, _malformed = normalize_batch(
        raw_markets, source_mode="live", source_used=source_used, retrieved_at=retrieved_at,
    )
    validated, registry_excluded = apply_strict_game_registry(records, requested_date=date)
    kept, stage_report = apply_filters(validated, {
        "date": date,
        "games": [game["matchup"]],
        "include_closed": True,      # a closed/settled contract is still part of "every market for this game"
        "include_unknown": True,     # never hide a contract just because the parser could not classify it
    })
    excluded_here = [
        r for r in registry_excluded
        if (r.get("matchup") or "").upper() == game["matchup"].upper()
    ]
    return kept, excluded_here, stage_report, status_counts


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_slate_context(date, game_pk):
    """
    THIS game's block, verbatim, out of the canonical slate artifact --
    never recomputed, never merged across dates. Returns a dict with an
    explicit `status` so the artifact always says WHY context is missing
    rather than just lacking it.
    """
    for label, path in (
        ("data/slate.json", os.path.join("data", "slate.json")),
        (f"data/pipeline/{date}/normalized_slate.json", os.path.join("data", "pipeline", date, "normalized_slate.json")),
    ):
        payload = _load_json(path)
        if payload is None:
            continue
        # A pipeline artifact wraps its payload in a versioned envelope.
        slate = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if (slate or {}).get("date") != date:
            continue
        for game in (slate.get("games") or []):
            if str(game.get("gameId")) == str(game_pk):
                return {
                    "status": "LOADED",
                    "sourceFile": label,
                    "slateDate": slate.get("date"),
                    "slateGeneratedAt": slate.get("_officialRunAt") or slate.get("fetchedAt"),
                    "game": game,
                    "marketLedgerRows": len(game.get("marketLedger") or []),
                }
        return {
            "status": "GAME_NOT_IN_SLATE",
            "sourceFile": label,
            "slateDate": slate.get("date"),
            "reason": f"gamePk {game_pk} is not present in {label} for {date}",
            "game": None,
        }
    return {
        "status": "NOT_AVAILABLE",
        "sourceFile": None,
        "reason": (
            f"no canonical slate artifact for {date} (neither data/slate.json nor "
            f"data/pipeline/{date}/normalized_slate.json is on this date) -- run the full "
            f"slate fetch if model context is needed"
        ),
        "game": None,
    }


def load_full_market_coverage(date, matchup):
    """
    This game's rows out of the already-built full-market-coverage
    artifact (the repository's research/audit view of every archived
    contract and its model-support status). Absent is reported, never
    filled in.
    """
    for label, path in (
        (f"data/pipeline/{date}/full_market_coverage.json", os.path.join("data", "pipeline", date, "full_market_coverage.json")),
        (f"data/kalshi/discovery/{date}_coverage.json", os.path.join("data", "kalshi", "discovery", f"{date}_coverage.json")),
    ):
        payload = _load_json(path)
        if payload is None:
            continue
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        contracts = body.get("contracts") or body.get("rows") or []
        rows = [c for c in contracts if (c.get("matchup") or "").upper() == matchup.upper()]
        return {"status": "LOADED", "sourceFile": label, "rows": rows, "rowCount": len(rows)}
    return {
        "status": "NOT_AVAILABLE",
        "sourceFile": None,
        "reason": f"no full-market-coverage artifact for {date}",
        "rows": [], "rowCount": 0,
    }


def summarize_markets(records):
    """Pure. A compact per-family/scope census so a human (or an AI) can
    see at a glance that every market family for this game is present."""
    by_family, by_scope = {}, {}
    for r in records:
        by_family[r.get("family") or "unknown"] = by_family.get(r.get("family") or "unknown", 0) + 1
        by_scope[r.get("scope") or "unknown"] = by_scope.get(r.get("scope") or "unknown", 0) + 1
    return {"total": len(records), "byFamily": dict(sorted(by_family.items())),
            "byScope": dict(sorted(by_scope.items()))}


def build_artifact(*, date, game, markets, excluded, stage_report, status_counts,
                   slate_context, coverage, archive_info, generated_at, warnings):
    return {
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "artifactType": "SINGLE_GAME_HANDICAPPING_BUNDLE",
        "generatedAt": generated_at,
        "date": date,
        "gamePk": game.get("gamePk"),
        "matchup": game.get("matchup"),
        "awayTeam": game.get("awayTeam"),
        "homeTeam": game.get("homeTeam"),
        "scheduledStart": game.get("scheduledStart"),
        "gameStatus": game.get("status"),
        "venue": game.get("venue"),
        "doubleheaderGameNumber": game.get("gameNumber"),
        "isDoubleheaderLeg": game.get("isDoubleheaderLeg"),
        "scheduleSource": "mlb_stats_api_schedule",
        "rawArchive": archive_info,
        "markets": markets,
        "marketSummary": summarize_markets(markets),
        "registryExcludedForThisGame": excluded,
        "marketFilterStageReport": stage_report,
        "rawNormalizationStatusCounts": status_counts,
        "slateContext": slate_context,
        "fullMarketCoverage": coverage,
        "warnings": warnings,
        "guarantees": [
            "The raw Kalshi capture archived by this run is the COMPLETE, UNFILTERED market "
            "universe; game filtering happened only after that archive was written and verified.",
            "No probability, edge, price adjustment or recommendation is computed in this artifact.",
            "Model/handicapping context is this game's canonical slate block verbatim, or an "
            "explicit absence reason -- never fabricated.",
        ],
    }


def write_artifact(artifact, *, root=SINGLE_GAME_DIR):
    date, game_pk = artifact["date"], artifact["gamePk"]
    date_dir = os.path.join(root, date)
    os.makedirs(date_dir, exist_ok=True)
    path = os.path.join(date_dir, f"{game_pk}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)

    # Per-date index + a repository-wide "latest" pointer, so a fresh
    # ChatGPT/Claude session can find the newest single-game artifact
    # without knowing any gamePk.
    entries = []
    for candidate in sorted(glob.glob(os.path.join(date_dir, "*.json"))):
        if os.path.basename(candidate) == "index.json":
            continue
        payload = _load_json(candidate) or {}
        entries.append({
            "path": candidate.replace(os.sep, "/"),
            "gamePk": payload.get("gamePk"),
            "matchup": payload.get("matchup"),
            "scheduledStart": payload.get("scheduledStart"),
            "generatedAt": payload.get("generatedAt"),
            "doubleheaderGameNumber": payload.get("doubleheaderGameNumber"),
        })
    index_path = os.path.join(date_dir, "index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump({"date": date, "artifacts": entries}, f, indent=2, sort_keys=True)

    latest_path = os.path.join(root, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({
            "updatedAt": artifact["generatedAt"],
            "date": date,
            "gamePk": game_pk,
            "matchup": artifact["matchup"],
            "path": path.replace(os.sep, "/"),
            "dateIndexPath": index_path.replace(os.sep, "/"),
        }, f, indent=2, sort_keys=True)
    return path, index_path, latest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today, America/New_York)")
    parser.add_argument("--game", default=None,
                        help="Friendly selector: 'Yankees vs Red Sox', 'NYY@BOS', 'Brewers', 'MIL'")
    parser.add_argument("--game-pk", default=None, help="Exact MLB gamePk (required to pick a doubleheader leg)")
    parser.add_argument("--source", choices=["live", "snapshot"], default="live",
                        help="live = fetch and archive the full universe (default); snapshot = reuse the newest "
                             "already-archived complete capture for this date (no new archive is written)")
    parser.add_argument("--api-base", default=os.environ.get("EDGE_FINDER_API_BASE", "https://edge-finder-api.vercel.app"))
    parser.add_argument("--out-root", default=SINGLE_GAME_DIR)
    args = parser.parse_args()

    date = args.date or today_et()
    if not args.game and not args.game_pk:
        print("ERROR: pass --game (a team/matchup selector) or --game-pk (an exact MLB game id)", file=sys.stderr)
        return EXIT_ERROR

    warnings = []

    # ── resolve the game FIRST: no point capturing anything if the
    # selector is ambiguous, and a doubleheader must refuse loudly.
    schedule_json = mlb_schedule.fetch_schedule(date)
    if schedule_json is None:
        print(f"ERROR: MLB schedule fetch failed for {date} -- cannot resolve a game without it", file=sys.stderr)
        return EXIT_ERROR
    schedule_games = mlb_schedule.parse_schedule_games(schedule_json)
    game, candidates, reason = resolve_single_game(schedule_games, selector=args.game, game_pk=args.game_pk)
    if game is None:
        print(f"REFUSED: {reason}", file=sys.stderr)
        if candidates:
            print(f"\nCandidate games on {date}:", file=sys.stderr)
            for candidate in candidates:
                print(f"  {describe_candidate(candidate)}", file=sys.stderr)
            print("\nRe-run with --game-pk <gamePk> to pick exactly one.", file=sys.stderr)
        return EXIT_AMBIGUOUS
    print(f"[fetch_single_game] resolved {args.game or args.game_pk!r} -> {describe_candidate(game)}")

    # ── ARCHIVE FIRST, UNFILTERED, VERIFIED ───────────────────────────
    from scripts.check_kalshi_prices import FetchError, fetch_live, load_snapshot

    if args.source == "live":
        try:
            payload, http_status, endpoint, size = fetch_live(args.api_base)
        except FetchError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
        raw_markets = extract_markets(payload)
        snapshot_path, archived_count = archive_full_universe(payload, date)
        ok, archive_reason = verify_archive_is_complete(snapshot_path, len(raw_markets))
        if not ok:
            print(f"ERROR: complete-universe archive check failed -- refusing to filter. {archive_reason}",
                  file=sys.stderr)
            return EXIT_ERROR
        archive_info = {
            "mode": "LIVE_CAPTURE",
            "snapshotPath": snapshot_path.replace(os.sep, "/"),
            "endpoint": endpoint,
            "httpStatus": http_status,
            "responseSizeBytes": size,
            "universeMarketCount": archived_count,
            "completeUnfilteredArchiveVerified": True,
            "filteringHappenedAfterArchive": True,
        }
    else:
        snapshot_path = latest_snapshot_for_date(date)
        if not snapshot_path:
            print(f"ERROR: no archived Kalshi snapshot exists for {date}", file=sys.stderr)
            return EXIT_ERROR
        try:
            payload = load_snapshot(snapshot_path)
        except FetchError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_ERROR
        raw_markets = extract_markets(payload)
        archive_info = {
            "mode": "EXISTING_ARCHIVE",
            "snapshotPath": snapshot_path.replace(os.sep, "/"),
            "universeMarketCount": len(raw_markets),
            "completeUnfilteredArchiveVerified": True,
            "filteringHappenedAfterArchive": True,
            "note": "reused an already-archived complete capture; no new archive written",
        }
    print(f"[fetch_single_game] complete unfiltered universe: {archive_info['universeMarketCount']} markets "
          f"-> {archive_info['snapshotPath']}")

    # ── NOW filter to the one game ────────────────────────────────────
    generated_at = utc_now_iso()
    markets, excluded, stage_report, status_counts = select_game_markets(
        raw_markets, game, date, retrieved_at=generated_at,
        source_used="live" if args.source == "live" else "snapshot",
    )
    if not markets:
        warnings.append(
            f"no Kalshi markets matched {game['matchup']} on {date} -- the complete archive above is "
            f"unaffected; check the stage report for which filter emptied the result"
        )
    if excluded:
        warnings.append(
            f"{len(excluded)} contract(s) naming this matchup were rejected by the strict single-game "
            f"registry gate; they are listed under registryExcludedForThisGame with their reasons"
        )

    slate_context = load_slate_context(date, game["gamePk"])
    if slate_context["status"] != "LOADED":
        warnings.append(f"slate context {slate_context['status']}: {slate_context.get('reason', '')}".strip())
    coverage = load_full_market_coverage(date, game["matchup"])

    artifact = build_artifact(
        date=date, game=game, markets=markets, excluded=excluded, stage_report=stage_report,
        status_counts=status_counts, slate_context=slate_context, coverage=coverage,
        archive_info=archive_info, generated_at=generated_at, warnings=warnings,
    )
    path, index_path, latest_path = write_artifact(artifact, root=args.out_root)

    summary = artifact["marketSummary"]
    print(f"[fetch_single_game] {game['matchup']} gamePk={game['gamePk']}: {summary['total']} markets "
          f"({summary['byFamily']})")
    print(f"[fetch_single_game] wrote {path}")
    print(f"[fetch_single_game] index: {index_path} | latest pointer: {latest_path}")
    for warning in warnings:
        print(f"[fetch_single_game] WARNING: {warning}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
