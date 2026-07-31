#!/usr/bin/env python3
"""
scripts/check_kalshi_prices.py
===================================
Standalone Kalshi MLB price-check tool (Model Performance phase:
"kalshi-standalone-price-check").

Retrieves, normalizes, filters, displays, and (optionally) archives
current Kalshi MLB market prices WITHOUT running the slate,
projection, recommendation, risk, execution, or settlement pipeline.
This is a pricing and discovery tool only.

SAFETY: this script does NOT import scripts/build_market_ledger.py,
scripts/risk_gate.py, scripts/write_pending_bets.py,
scripts/protect_slate.py, scripts/validate_slate_final.py, or any
execution/recommendation/settlement/bankroll module. It never writes
to data/slate.json, bets.json, or any production pipeline artifact.
It generates NO recommendation and NO model edge.

    "This tool reports market prices only. It does not determine
    whether a wager has positive expected value."

Usage:
    python3 scripts/check_kalshi_prices.py --date 2026-07-29 --team Yankees \\
        --scope F5 --family inning_result --include-unknown --format table

See docs/KALSHI_PRICE_CHECKER.md for full documentation.
"""
import argparse
import csv
import glob
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_price_check import (
    normalize_batch,
    apply_strict_game_registry,
    apply_filters,
    group_inning_result_threeway,
    group_by_game,
    format_csv,
    format_by_game,
    format_threeway_groups,
    diagnose_result,
    STATUS_CLASSIFICATION_UNKNOWN,
)

DEFAULT_API_BASE = os.environ.get("EDGE_FINDER_API_BASE", "https://edge-finder-api.vercel.app")
SNAPSHOT_DIR = os.path.join(ROOT, "data", "kalshi_registry_snapshots")
CACHE_DIR = os.path.join(ROOT, ".kalshi_price_check_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "live_response.json")


class FetchError(Exception):
    """Raised when a genuine fetch or parsing failure occurs."""


def fetch_live(base_url=DEFAULT_API_BASE, timeout=15):
    """
    Network adapter: fetches the deployed /api/kalshisearch endpoint.
    Raises FetchError on any failure -- never returns partial/garbage
    data. Returns (data, http_status, endpoint_url, response_size) so
    the caller can report real fetch diagnostics (mission requirement:
    "which endpoint? HTTP status? response size?") instead of only
    ever knowing "it worked" or "it didn't."
    """
    url = f"{base_url.rstrip('/')}/api/kalshisearch"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
            return json.loads(raw_bytes), status, url, len(raw_bytes)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        status = e.code if isinstance(e, HTTPError) else None
        raise FetchError(f"live fetch failed: {type(e).__name__}: {e} (endpoint={url}, http_status={status})")


def read_cache(ttl_seconds):
    """Returns cached live-response data if fresh enough, else None.
    Never raises -- a corrupt/missing cache is treated as a cache miss."""
    if ttl_seconds <= 0:
        return None
    try:
        with open(CACHE_FILE) as f:
            cached = json.load(f)
        cached_at = cached.get("_cachedAt", 0)
        if time.time() - cached_at > ttl_seconds:
            return None
        return cached.get("data")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return None


def write_cache(data, fetch_info):
    """Best-effort cache write. A write failure must never corrupt or
    block the actual result -- caught and ignored."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"_cachedAt": time.time(), "data": data, "_fetchInfo": fetch_info}, f)
    except OSError:
        pass


def find_latest_snapshot():
    paths = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "kalshi_search_*.json")))
    return paths[-1] if paths else None


def load_snapshot(path):
    if not path or not os.path.exists(path):
        raise FetchError(f"snapshot file not found: {path!r}")
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise FetchError(f"snapshot file is not valid JSON: {path!r}: {e}")


def _fetch_info_from_response(data, http_status, endpoint, response_size):
    """Builds the fetch-diagnostics dict surfaced in metadata -- always
    populated on a live fetch, regardless of how many markets came back."""
    return {
        "endpoint": endpoint,
        "httpStatus": http_status,
        "responseSizeBytes": response_size,
        "marketsKeyPresent": "markets" in data,
        "marketsArrayLength": len(data.get("markets", [])),
        "responseTotalMarketsField": data.get("total_markets"),
        "responseDateField": data.get("date"),
        "responseKalshiDateField": data.get("kalshi_date"),
    }


def resolve_source(source, snapshot_path, cache_ttl_seconds, verbose=False):
    """
    Returns (raw_markets, source_used, snapshot_timestamp, fallback_reason,
    fetch_info). fetch_info is a dict with endpoint/httpStatus/
    responseSizeBytes/marketsKeyPresent/marketsArrayLength (None for
    snapshot-only sources, where there is no live fetch to report on).
    Raises FetchError for a genuine failure (live required but
    unavailable, or no valid snapshot found).
    """
    if source == "live":
        cached = read_cache(cache_ttl_seconds)
        if cached is not None:
            if verbose:
                print("[check_kalshi_prices] using cached live response (within TTL)", file=sys.stderr)
            return cached.get("markets", []), "live-cached", None, None, cached.get("_fetchInfo")
        data, http_status, endpoint, response_size = fetch_live()
        fetch_info = _fetch_info_from_response(data, http_status, endpoint, response_size)
        write_cache(data, fetch_info)
        return data.get("markets", []), "live", None, None, fetch_info

    if source == "snapshot":
        path = snapshot_path or find_latest_snapshot()
        data = load_snapshot(path)
        return data.get("markets", []), f"snapshot:{os.path.relpath(path, ROOT)}", data.get("fetched_at"), None, None

    if source == "auto":
        cached = read_cache(cache_ttl_seconds)
        if cached is not None:
            if verbose:
                print("[check_kalshi_prices] using cached live response (within TTL)", file=sys.stderr)
            return cached.get("markets", []), "live-cached", None, None, cached.get("_fetchInfo")
        try:
            data, http_status, endpoint, response_size = fetch_live()
            fetch_info = _fetch_info_from_response(data, http_status, endpoint, response_size)
            write_cache(data, fetch_info)
            return data.get("markets", []), "live", None, None, fetch_info
        except FetchError as e:
            path = snapshot_path or find_latest_snapshot()
            data = load_snapshot(path)  # raises FetchError if genuinely unavailable
            return data.get("markets", []), f"snapshot:{os.path.relpath(path, ROOT)}", data.get("fetched_at"), str(e), None

    raise FetchError(f"unknown source mode: {source!r}")


def build_parser():
    p = argparse.ArgumentParser(description="Standalone Kalshi MLB price-check tool (pricing/discovery only).")
    p.add_argument("--date")
    p.add_argument("--game")
    p.add_argument("--team")
    p.add_argument("--away-team")
    p.add_argument("--home-team")
    p.add_argument("--family")
    p.add_argument("--scope")
    p.add_argument("--outcome")
    p.add_argument("--participant")
    p.add_argument("--pitcher")
    p.add_argument("--hitter")
    p.add_argument("--ticker")
    p.add_argument("--event-ticker")
    p.add_argument("--series-ticker")
    p.add_argument("--status")
    p.add_argument("--include-closed", action="store_true", default=False)
    p.add_argument("--include-unknown", action="store_true", default=True)
    p.add_argument("--exclude-unknown", dest="include_unknown", action="store_false")
    p.add_argument("--max-results", type=int, default=None)
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--output")
    p.add_argument("--metadata-output",
                    help="Write the full diagnostic metadata (stage-by-stage counts, fetch info, "
                         "diagnosis) to this path as JSON. Bug fix: this metadata was previously "
                         "computed but only ever printed to stderr via --verbose, which the "
                         "GitHub Actions job summary and artifact-upload steps never read -- a "
                         "zero-result run had no persisted explanation anywhere. Always populated "
                         "when set, whether the result is zero or not.")
    p.add_argument("--archive", action="store_true", default=False)
    p.add_argument("--source", choices=["live", "snapshot", "auto"], default="auto")
    p.add_argument("--snapshot-path")
    p.add_argument("--cache-ttl-seconds", type=int, default=45)
    p.add_argument("--verbose", action="store_true", default=False)
    return p


def build_filters(args):
    return {
        "date": args.date,
        "game": args.game,
        "team": args.team,
        "away_team": args.away_team,
        "home_team": args.home_team,
        "family": args.family,
        "scope": args.scope,
        "outcome": args.outcome,
        "participant": args.participant,
        "pitcher": args.pitcher,
        "hitter": args.hitter,
        "ticker": args.ticker,
        "event_ticker": args.event_ticker,
        "series_ticker": args.series_ticker,
        "status": args.status,
        "include_closed": args.include_closed,
        "include_unknown": args.include_unknown,
        "max_results": args.max_results,
    }


def run(args):
    """Pure-ish orchestration (isolated from argparse for testability).
    Returns (exit_code, output_text, metadata_dict).

    Every stage's input/output count is captured in `metadata` and
    ALWAYS returned -- including (and especially) when the final
    result is zero. `metadata["diagnosis"]` is always a populated,
    human-readable explanation of the outcome (never silent), per the
    "no silent zero results" requirement. This function itself never
    silently drops the diagnostics it computes -- see main() for the
    companion fix that ensures they are actually persisted/displayed,
    not just computed and discarded.
    """
    retrieved_at = datetime.now(timezone.utc).isoformat()
    try:
        raw_markets, source_used, snapshot_ts, fallback_reason, fetch_info = resolve_source(
            args.source, args.snapshot_path, args.cache_ttl_seconds, verbose=args.verbose
        )
    except FetchError as e:
        return 1, f"FETCH ERROR: {e}", {"error": str(e)}

    records, status_counts, malformed = normalize_batch(
        raw_markets, source_mode=args.source, source_used=source_used,
        snapshot_timestamp=snapshot_ts, retrieved_at=retrieved_at,
    )
    classified_count = sum(1 for r in records if r["family"] != "unknown")
    unknown_count = sum(1 for r in records if r["family"] == "unknown")

    # Mandatory, non-optional gate (Kalshi price-checker correction
    # mission) -- runs BEFORE the user's optional apply_filters()
    # pipeline and cannot be disabled by any flag. Only markets in a
    # confirmed single-game MLB series family, with a well-formed game
    # identity, ever reach apply_filters().
    registry_kept, registry_excluded = apply_strict_game_registry(records, requested_date=args.date)
    exclusion_reason_counts = {}
    for r in registry_excluded:
        reason = r.get("exclusionReason")
        exclusion_reason_counts[reason] = exclusion_reason_counts.get(reason, 0) + 1

    filters = build_filters(args)
    filtered, stage_report = apply_filters(registry_kept, filters)
    filtered_out_count = sum(stage_report["removedByStage"].values())

    if records and not registry_kept:
        diagnosis = (
            f"All {len(records)} normalized record(s) were excluded by the strict single-game "
            f"MLB registry gate (not an approved single-game series, or a malformed/mismatched "
            f"game identity) -- see exclusionReasonCounts."
        )
    else:
        diagnosis = diagnose_result(len(raw_markets), len(records), stage_report, len(filtered))

    approved_series_queried = sorted({r["seriesTicker"] for r in registry_kept if r.get("seriesTicker")})
    games_found = sorted({(r["date"], r["awayTeam"], r["homeTeam"]) for r in registry_kept
                           if r.get("date") and r.get("awayTeam") and r.get("homeTeam")})
    unresolved_mappings = sum(
        1 for r in registry_excluded if r.get("exclusionReason") in ("DATE_MISMATCH", "MALFORMED_EVENT")
    )

    is_stale = source_used != "live"
    metadata = {
        "filtersUsed": filters,
        "sourceRequested": args.source,
        "sourceUsed": source_used,
        "fetchInfo": fetch_info,
        "retrievedAt": retrieved_at,
        "snapshotTimestamp": snapshot_ts,
        "fallbackReason": fallback_reason,
        "pricesMayBeStale": is_stale,
        "rawRecordsFetched": len(raw_markets),
        "normalizedRecordCount": len(records),
        "classifiedCount": classified_count,
        "unknownCount": unknown_count,
        "malformedRecordCount": len(malformed),
        "malformedReasons": malformed,
        "statusCounts": status_counts,
        "gamesFoundCount": len(games_found),
        "gamesFound": [f"{away}@{home} ({date})" for date, away, home in games_found],
        "approvedSeriesQueried": approved_series_queried,
        "marketsExcludedByRegistry": len(registry_excluded),
        "exclusionReasonCounts": exclusion_reason_counts,
        "unresolvedMappingsCount": unresolved_mappings,
        "queryErrors": [fallback_reason] if fallback_reason else [],
        "removedByFilterStage": stage_report["removedByStage"],
        "remainingAfterFilterStage": stage_report["remainingAfterStage"],
        "filteredOutCount": filtered_out_count,
        "resultCount": len(filtered),
        "diagnosis": diagnosis,
    }

    if args.format == "json":
        output = json.dumps(filtered, indent=2)
    elif args.format == "csv":
        output = format_csv(filtered)
    else:
        if not filtered:
            output = "No matching Kalshi markets found for the given filters."
        else:
            game_groups = group_by_game(filtered)
            sections = [format_by_game(game_groups)]
            threeway_groups = group_inning_result_threeway(filtered)
            if threeway_groups:
                sections.append("")
                sections.append(format_threeway_groups(threeway_groups))
            output = "\n".join(sections)
        if is_stale:
            label = f"SNAPSHOT PRICE — captured {snapshot_ts or 'unknown time'}"
            output = f"{label}\n{output}"

    return 0, output, {"metadata": metadata, "records": filtered, "excluded": registry_excluded}


def write_archive(records, metadata, output_dir, excluded=None):
    """
    Writes the main-output artifacts (json/csv/metadata) plus, when
    `excluded` is supplied, a SEPARATE audit-only artifact
    (kalshi_price_check_excluded.json) listing every market the strict
    single-game registry gate rejected, with its exclusionReason
    (mission requirement #7: never dump excluded markets into the main
    user-facing output, but preserve them for audit).
    """
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "kalshi_price_check.json")
    csv_path = os.path.join(output_dir, "kalshi_price_check.csv")
    meta_path = os.path.join(output_dir, "kalshi_price_check_metadata.json")
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    with open(csv_path, "w") as f:
        f.write(format_csv(records))
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    excluded_path = None
    if excluded is not None:
        excluded_path = os.path.join(output_dir, "kalshi_price_check_excluded.json")
        with open(excluded_path, "w") as f:
            json.dump(excluded, f, indent=2)
    return json_path, csv_path, meta_path, excluded_path


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    exit_code, output, result = run(args)

    if exit_code != 0:
        print(output, file=sys.stderr)
        return exit_code

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        print(output)

    metadata = result.get("metadata", {})

    # Bug fix: metadata (raw/normalized/classified/filter-stage counts,
    # fetch diagnostics, and an always-populated `diagnosis` string) was
    # previously computed but only ever printed to stderr behind
    # --verbose -- nothing downstream (the GitHub Actions job summary
    # step, the artifact-upload steps) ever read stderr, so a
    # zero-result run had no persisted explanation anywhere. Writing it
    # to --metadata-output makes it a real, inspectable artifact.
    if args.metadata_output:
        with open(args.metadata_output, "w") as f:
            json.dump(metadata, f, indent=2)

    # Always print a one-line diagnosis to stderr, independent of
    # --verbose -- "no silent zero results" applies to interactive/CLI
    # use too, not only to the workflow's artifact.
    print(f"[check_kalshi_prices] {metadata.get('diagnosis', 'no diagnosis available')}", file=sys.stderr)

    if args.verbose:
        print(json.dumps(metadata, indent=2), file=sys.stderr)

    if args.archive:
        archive_dir = os.path.join(ROOT, "kalshi_price_check_artifacts")
        json_path, csv_path, meta_path, excluded_path = write_archive(
            result.get("records", []), result.get("metadata", {}), archive_dir,
            excluded=result.get("excluded", []),
        )
        print(f"Archived: {json_path}, {csv_path}, {meta_path}, {excluded_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
