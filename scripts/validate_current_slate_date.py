#!/usr/bin/env python3
"""
scripts/validate_current_slate_date.py
========================================
Validates that all data files match the requested slate date.

Usage:
    python3 scripts/validate_current_slate_date.py YYYY-MM-DD

Exit codes:
    0  = all checks passed
    1  = stale date, mismatch, or invalid data detected

Any failure prints:
    STALE SLATE ABORT: requested=YYYY-MM-DD actual=YYYY-MM-DD source=<file-or-step>

On success, prints a concise pass report with:
    - requested date
    - meta date
    - slate date
    - pitchers date (if available)
    - number of games
    - first 5 games with start time, away/home, probable pitchers
    - Kalshi validation status
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Setup ─────────────────────────────────────────────────────────────────────
ET = timezone(timedelta(hours=-4))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)

def data_path(filename):
    return os.path.join(ROOT_DIR, "data", filename)

def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def abort(requested, actual, source):
    print(f"STALE SLATE ABORT: requested={requested} actual={actual} source={source}",
          file=sys.stderr)
    sys.exit(1)


def main():
    # ── A. Requested date argument ────────────────────────────────────────────
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("STALE SLATE ABORT: requested=missing actual=N/A source=command-line-argument",
              file=sys.stderr)
        print("Usage: python3 scripts/validate_current_slate_date.py YYYY-MM-DD",
              file=sys.stderr)
        sys.exit(1)

    requested_date_str = sys.argv[1].strip()

    try:
        req_date = datetime.strptime(requested_date_str, "%Y-%m-%d").date()
        req_str = req_date.strftime("%Y-%m-%d")
    except ValueError:
        print(
            f"STALE SLATE ABORT: requested={requested_date_str!r} actual=invalid "
            f"source=command-line-argument (must be YYYY-MM-DD)",
            file=sys.stderr
        )
        sys.exit(1)

    failures = []
    warnings = []

    # ── B. fetch_status.json ──────────────────────────────────────────────────
    fetch_status_path = data_path("fetch_status.json")
    fetch_status = load_json(fetch_status_path)
    fetch_status_note = ""

    if fetch_status is None:
        fetch_status_note = "MISSING (will be created on next fetch)"
        warnings.append("data/fetch_status.json not found — treating as UNCHECKED")
    else:
        fs_status = fetch_status.get("status", "UNKNOWN")
        fs_requested = fetch_status.get("requestedDate", "?")
        fs_actual = fetch_status.get("actualDate", "?")
        fetch_status_note = f"status={fs_status} requestedDate={fs_requested} actualDate={fs_actual}"

        if fs_status == "FAILED_STALE_DATE":
            failures.append(
                f"fetch_status.json: FAILED_STALE_DATE "
                f"requested={fs_requested} actual={fs_actual}"
            )
        elif fs_status not in ("OK", "UNCHECKED"):
            failures.append(
                f"fetch_status.json: status={fs_status!r} is not OK"
            )
        elif fs_status == "OK" and fs_requested != req_str:
            failures.append(
                f"fetch_status.json: requestedDate={fs_requested} "
                f"does not match command-line date={req_str}"
            )

    # ── C. meta.json ─────────────────────────────────────────────────────────
    meta_path = data_path("meta.json")
    meta = load_json(meta_path)

    if meta is None:
        failures.append("data/meta.json: MISSING")
    else:
        meta_date_str = (
            meta.get("date") or
            meta.get("slateDate") or
            meta.get("fetchDate") or
            ""
        )
        if not meta_date_str:
            failures.append("data/meta.json: no date field found")
        elif meta_date_str != req_str:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} actual={meta_date_str} "
                f"source=data/meta.json"
            )

        fetched_at = meta.get("fetchedAt", "")
        if fetched_at:
            try:
                fa = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                fa_et = fa.astimezone(ET)
                fa_date = fa_et.strftime("%Y-%m-%d")
                if fa_date != req_str:
                    failures.append(
                        f"STALE SLATE ABORT: requested={req_str} actual={fa_date} "
                        f"source=data/meta.json[fetchedAt]"
                    )
            except Exception:
                warnings.append(f"meta.json fetchedAt={fetched_at!r} could not be parsed")

    # ── D. slate.json ─────────────────────────────────────────────────────────
    slate_path = data_path("slate.json")
    slate = load_json(slate_path)
    games = []

    if slate is None:
        failures.append("data/slate.json: MISSING")
    else:
        slate_date_str = slate.get("date", "")
        if not slate_date_str:
            failures.append("data/slate.json: no 'date' field")
        elif slate_date_str != req_str:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} actual={slate_date_str} "
                f"source=data/slate.json"
            )

        games = slate.get("games", [])
        if not games:
            failures.append(f"data/slate.json: no games for {req_str}")

        # Validate each game
        for g in games:
            away_abbr = (g.get("away") or {}).get("abbr", "?")
            home_abbr = (g.get("home") or {}).get("abbr", "?")
            gid = f"{away_abbr}@{home_abbr}"

            # away/home teams must exist
            if not (g.get("away") and g.get("home")):
                failures.append(f"data/slate.json[{gid}]: missing away or home team")

            # startTime must be present and map to req_str in ET
            start_time = g.get("startTime") or g.get("gameTime")
            if not start_time:
                warnings.append(f"data/slate.json[{gid}]: no startTime/gameTime")
            else:
                try:
                    if start_time.endswith("Z"):
                        start_time_p = start_time[:-1] + "+00:00"
                    else:
                        start_time_p = start_time
                    dt = datetime.fromisoformat(start_time_p)
                    dt_et = dt.astimezone(ET)
                    game_date_et = dt_et.strftime("%Y-%m-%d")
                    if game_date_et != req_str:
                        failures.append(
                            f"STALE SLATE ABORT: requested={req_str} "
                            f"actual={game_date_et} "
                            f"source=data/slate.json[{gid}].startTime"
                        )
                except Exception:
                    warnings.append(f"data/slate.json[{gid}]: unparseable startTime={start_time!r}")

            # Check for obviously stale game IDs (gameId date mismatch)
            game_id = g.get("gameId")
            if game_id and isinstance(game_id, str) and "26" in game_id:
                # Some game IDs embed kalshi-style dates — validate
                months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
                try:
                    expected_k = (
                        str(req_date.year)[2:] +
                        months[req_date.month - 1] +
                        str(req_date.day).zfill(2)
                    )
                    if not game_id.startswith(expected_k):
                        warnings.append(
                            f"data/slate.json[{gid}]: gameId={game_id!r} "
                            f"may not match requested date {req_str}"
                        )
                except Exception:
                    pass

    # ── E. pitchers.json ─────────────────────────────────────────────────────
    pitchers_path = data_path("pitchers.json")
    pitchers = load_json(pitchers_path)
    pitchers_date_str = None

    if pitchers is not None:
        pitchers_date_str = pitchers.get("date", "")
        if pitchers_date_str and pitchers_date_str != req_str:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} actual={pitchers_date_str} "
                f"source=data/pitchers.json"
            )

    # ── F. Kalshi files ───────────────────────────────────────────────────────
    months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    try:
        expected_kalshi_date = (
            str(req_date.year)[2:] +
            months[req_date.month - 1] +
            str(req_date.day).zfill(2)
        )
    except Exception:
        expected_kalshi_date = None

    kalshi_status_lines = []

    # kalshi_search.json
    ks_path = data_path("kalshi_search.json")
    ks = load_json(ks_path)
    if ks is not None:
        ks_date = ks.get("date", "")
        ks_kalshi_date = ks.get("kalshi_date", "")
        if ks_date and ks_date != req_str:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} actual={ks_date} "
                f"source=data/kalshi_search.json"
            )
            kalshi_status_lines.append(f"  kalshi_search.json: STALE date={ks_date}")
        elif expected_kalshi_date and ks_kalshi_date and ks_kalshi_date != expected_kalshi_date:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} "
                f"actual=kalshi_date={ks_kalshi_date} "
                f"source=data/kalshi_search.json[kalshi_date]"
            )
            kalshi_status_lines.append(
                f"  kalshi_search.json: kalshi_date mismatch "
                f"expected={expected_kalshi_date} actual={ks_kalshi_date}"
            )
        else:
            total = ks.get("total_markets", 0)
            kalshi_status_lines.append(
                f"  kalshi_search.json: OK date={ks_date} markets={total}"
            )
    else:
        kalshi_status_lines.append("  kalshi_search.json: MISSING (will be fetched in workflow)")

    # kalshi_market_registry.json — check date embedded in tickers
    kmr_path = data_path("kalshi_market_registry.json")
    kmr = load_json(kmr_path)
    if kmr is not None:
        registry_date = kmr.get("date", "")
        if registry_date and registry_date != req_str:
            failures.append(
                f"STALE SLATE ABORT: requested={req_str} actual={registry_date} "
                f"source=data/kalshi_market_registry.json"
            )
            kalshi_status_lines.append(
                f"  kalshi_market_registry.json: STALE date={registry_date}"
            )
        elif expected_kalshi_date:
            # Sample-check tickers for correct date
            stale_tickers = []
            entries = kmr.get("registry", kmr) if isinstance(kmr.get("registry"), dict) else kmr
            if isinstance(entries, dict):
                for key, val in list(entries.items())[:5]:
                    if isinstance(val, dict):
                        for mkt_key, mkt_val in val.get("markets", {}).items():
                            for ticker_key in ["away_ticker", "home_ticker", "ticker"]:
                                t = mkt_val.get(ticker_key, "") if isinstance(mkt_val, dict) else ""
                                if t and expected_kalshi_date not in t and "26" in t:
                                    stale_tickers.append(t)
            if stale_tickers:
                failures.append(
                    f"STALE SLATE ABORT: requested={req_str} "
                    f"actual=wrong-date-tickers source=data/kalshi_market_registry.json "
                    f"examples={stale_tickers[:2]}"
                )
                kalshi_status_lines.append(
                    f"  kalshi_market_registry.json: stale tickers found"
                )
            else:
                kalshi_status_lines.append(
                    f"  kalshi_market_registry.json: OK date={registry_date or 'embedded-in-tickers'}"
                )
    else:
        kalshi_status_lines.append(
            "  kalshi_market_registry.json: MISSING (built during workflow)"
        )

    # kalshi_registry_snapshots
    snap_dir = data_path("kalshi_registry_snapshots")
    if os.path.isdir(snap_dir):
        today_snaps = [
            f for f in os.listdir(snap_dir)
            if f.startswith(f"kalshi_search_{req_str}") and f.endswith(".json")
        ]
        if today_snaps:
            kalshi_status_lines.append(
                f"  kalshi_registry_snapshots: {len(today_snaps)} snapshot(s) for {req_str}"
            )
        else:
            all_snaps = sorted([
                f for f in os.listdir(snap_dir) if f.endswith(".json")
            ])
            if all_snaps:
                latest = all_snaps[-1]
                kalshi_status_lines.append(
                    f"  kalshi_registry_snapshots: no snapshot for {req_str} "
                    f"(latest: {latest})"
                )
                warnings.append(
                    f"No Kalshi snapshot for {req_str} in kalshi_registry_snapshots/"
                )
            else:
                kalshi_status_lines.append(
                    "  kalshi_registry_snapshots: empty directory"
                )
    else:
        kalshi_status_lines.append(
            "  kalshi_registry_snapshots/: directory missing"
        )

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SLATE DATE VALIDATION — {req_str}")
    print(f"{'='*60}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  WARNING: {w}")

    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for fail in failures:
            print(f"  FAIL: {fail}")

        # Print the STALE SLATE ABORT lines to stderr as well
        for fail in failures:
            if fail.startswith("STALE SLATE ABORT"):
                print(fail, file=sys.stderr)
            else:
                print(
                    f"STALE SLATE ABORT: requested={req_str} actual=see-above source={fail[:50]}",
                    file=sys.stderr
                )

        print(f"\nRESULT: FAILED — stale-date validation did not pass for {req_str}")
        print("No valid bets can be produced until the slate date bug is fixed.")
        sys.exit(1)

    # ── Success: print pass report ────────────────────────────────────────────
    meta_date_out = meta.get("date", "?") if meta else "?"
    slate_date_out = slate.get("date", "?") if slate else "?"

    print(f"\nPass Report:")
    print(f"  Requested date : {req_str}")
    print(f"  meta.json date : {meta_date_out}")
    print(f"  slate.json date: {slate_date_out}")
    if pitchers_date_str:
        print(f"  pitchers date  : {pitchers_date_str}")
    else:
        print(f"  pitchers date  : not available")
    print(f"  fetch_status   : {fetch_status_note}")
    print(f"  Games          : {len(games)}")

    print(f"\nFirst 5 games:")
    for g in games[:5]:
        away = g.get("away") or {}
        home = g.get("home") or {}
        away_abbr = away.get("abbr", "?")
        home_abbr = home.get("abbr", "?")

        # Start time in ET
        start_time = g.get("startTime") or g.get("gameTime", "")
        start_et = ""
        if start_time:
            try:
                if start_time.endswith("Z"):
                    start_time_p = start_time[:-1] + "+00:00"
                else:
                    start_time_p = start_time
                dt_utc = datetime.fromisoformat(start_time_p)
                dt_et = dt_utc.astimezone(ET)
                start_et = dt_et.strftime("%I:%M %p ET").lstrip("0")
            except Exception:
                start_et = start_time

        # Probable pitchers
        away_pitcher = (away.get("pitcher") or {}).get("name", "TBD")
        home_pitcher = (home.get("pitcher") or {}).get("name", "TBD")

        print(
            f"  {away_abbr}@{home_abbr}  {start_et}  "
            f"({away_pitcher} vs {home_pitcher})"
        )

    print(f"\nKalshi Validation:")
    for line in kalshi_status_lines:
        print(line)

    print(f"\nRESULT: PASSED — {req_str} slate is valid")
    print("Slate is now valid and ready for a normal production run.")
    sys.exit(0)


if __name__ == "__main__":
    main()
