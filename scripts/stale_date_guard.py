#!/usr/bin/env python3
"""
scripts/stale_date_guard.py
============================
Shared helper — stale-date guard for all bet-writing and model-output scripts.

Usage (in any script that writes bets, CLV, or model output):

    from scripts.stale_date_guard import check_date_or_abort

    check_date_or_abort("2026-06-13")   # aborts with exit(1) on any stale-date issue

The function validates:
  - fetch_status.json status == "OK"
  - meta.json date matches requested_date
  - slate.json date matches requested_date and has games
  - pitchers.json date (if present) matches requested_date
  - All game startTimes map to requested_date in America/New_York

Any failure prints:
  STALE SLATE ABORT: requested=YYYY-MM-DD actual=YYYY-MM-DD source=<file-or-step>
and calls sys.exit(1).

Success: returns True.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────
ET = timezone(timedelta(hours=-4))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)

def _data_path(filename):
    return os.path.join(ROOT_DIR, "data", filename)


def _parse_date(date_str):
    """Parse YYYY-MM-DD string; return datetime.date or raise ValueError."""
    if not date_str or not isinstance(date_str, str):
        raise ValueError(f"Invalid date: {date_str!r}")
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _load_json(path, required=True):
    """Load JSON from path. If required=True and missing, returns None (caller handles)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return None


def _abort(requested, actual, source):
    """Print stale-date abort message and exit nonzero."""
    print(
        f"STALE SLATE ABORT: requested={requested} actual={actual} source={source}",
        file=sys.stderr
    )
    sys.exit(1)


def check_date_or_abort(requested_date):
    """
    Run full stale-date validation for requested_date.

    Aborts (sys.exit(1)) if any stale-date condition is detected.
    Returns True if all checks pass.

    Parameters
    ----------
    requested_date : str
        YYYY-MM-DD string for the requested slate date.
    """
    # ── A. Parse requested date ───────────────────────────────────────────────
    try:
        req_date = _parse_date(requested_date)
    except (ValueError, TypeError) as e:
        print(f"STALE SLATE ABORT: requested={requested_date!r} is not a valid YYYY-MM-DD date",
              file=sys.stderr)
        sys.exit(1)

    req_str = req_date.strftime("%Y-%m-%d")

    # ── B. fetch_status.json ──────────────────────────────────────────────────
    fetch_status_path = _data_path("fetch_status.json")
    fetch_status = _load_json(fetch_status_path)
    if fetch_status is not None:
        status = fetch_status.get("status", "")
        if status != "OK":
            actual_date = fetch_status.get("actualDate", fetch_status.get("requestedDate", "unknown"))
            if status == "FAILED_STALE_DATE":
                print(
                    f"STALE SLATE ABORT: fetch_status=FAILED_STALE_DATE "
                    f"requested={fetch_status.get('requestedDate','?')} "
                    f"actual={fetch_status.get('actualDate','?')}",
                    file=sys.stderr
                )
            else:
                print(
                    f"STALE SLATE ABORT: fetch_status={status!r} (not OK) "
                    f"requested={req_str} actual={actual_date}",
                    file=sys.stderr
                )
            sys.exit(1)

    # ── C. meta.json ─────────────────────────────────────────────────────────
    meta_path = _data_path("meta.json")
    meta = _load_json(meta_path)
    if meta is None:
        print(f"STALE SLATE ABORT: requested={req_str} actual=missing source=data/meta.json",
              file=sys.stderr)
        sys.exit(1)

    meta_date_str = meta.get("date") or meta.get("slateDate") or meta.get("fetchDate")
    if not meta_date_str:
        print(f"STALE SLATE ABORT: requested={req_str} actual=missing-date-field source=data/meta.json",
              file=sys.stderr)
        sys.exit(1)

    if meta_date_str != req_str:
        _abort(req_str, meta_date_str, "data/meta.json")

    # Verify fetchedAt is not obviously stale
    fetched_at = meta.get("fetchedAt")
    if fetched_at:
        try:
            fa = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            fa_et = fa.astimezone(ET)
            if fa_et.strftime("%Y-%m-%d") != req_str:
                _abort(req_str, fa_et.strftime("%Y-%m-%d"), "data/meta.json[fetchedAt]")
        except Exception:
            pass  # Can't parse — skip this check

    # ── D. slate.json ─────────────────────────────────────────────────────────
    slate_path = _data_path("slate.json")
    slate = _load_json(slate_path)
    if slate is None:
        print(f"STALE SLATE ABORT: requested={req_str} actual=missing source=data/slate.json",
              file=sys.stderr)
        sys.exit(1)

    slate_date_str = slate.get("date")
    if not slate_date_str:
        print(f"STALE SLATE ABORT: requested={req_str} actual=missing-date-field source=data/slate.json",
              file=sys.stderr)
        sys.exit(1)

    if slate_date_str != req_str:
        _abort(req_str, slate_date_str, "data/slate.json")

    games = slate.get("games", [])
    if not games:
        print(f"STALE SLATE ABORT: requested={req_str} actual=no-games source=data/slate.json",
              file=sys.stderr)
        sys.exit(1)

    # Validate every game's startTime maps to requested date in ET
    for g in games:
        away_abbr = (g.get("away") or {}).get("abbr", "?")
        home_abbr = (g.get("home") or {}).get("abbr", "?")
        gid = f"{away_abbr}@{home_abbr}"

        start_time = g.get("startTime") or g.get("gameTime")
        if not start_time:
            continue  # No startTime to validate

        try:
            # Parse ISO 8601 UTC time
            if start_time.endswith("Z"):
                start_time = start_time[:-1] + "+00:00"
            dt = datetime.fromisoformat(start_time)
            dt_et = dt.astimezone(ET)
            game_date_et = dt_et.strftime("%Y-%m-%d")
            if game_date_et != req_str:
                _abort(req_str, game_date_et, f"data/slate.json[{gid}].startTime")
        except Exception:
            pass  # Unparseable startTime — skip

    # ── E. pitchers.json ─────────────────────────────────────────────────────
    pitchers_path = _data_path("pitchers.json")
    pitchers = _load_json(pitchers_path)
    if pitchers is not None:
        pitchers_date = pitchers.get("date")
        if pitchers_date and pitchers_date != req_str:
            _abort(req_str, pitchers_date, "data/pitchers.json")

    # ── All checks passed ─────────────────────────────────────────────────────
    return True


def validate_kalshi_ticker_date(ticker, requested_date):
    """
    Check whether a Kalshi ticker's embedded date matches requested_date.

    Kalshi MLB tickers contain dates like 26JUN12 (for 2026-06-12).
    Returns True if date matches, False if mismatch or unparseable.
    """
    if not ticker or not requested_date:
        return False

    months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']

    try:
        req = datetime.strptime(requested_date, "%Y-%m-%d")
        expected_kalshi_date = (
            str(req.year)[2:] +
            months[req.month - 1] +
            str(req.day).zfill(2)
        )
        return expected_kalshi_date in ticker
    except Exception:
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/stale_date_guard.py YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)
    result = check_date_or_abort(sys.argv[1])
    print(f"Date guard passed for {sys.argv[1]}")
