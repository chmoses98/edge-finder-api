#!/usr/bin/env python3
"""
lib/slate_manager.py
=====================
Authoritative Slate Protection + Run Type Management

Run types:
  OFFICIAL_PREGAME        - First clean pregame run. Produces authoritative.json.
  LINEUP_RECHECK          - Subsequent run to update lineup/pitcher completeness.
                            Can only update games not yet started.
  IN_PLAY_RECHECK         - Run while some games are in progress.
                            Must not overwrite started games.
  REJECTED_CONTAMINATED   - Run contains sentinel prices, post-start prices, or
                            widespread data contamination. Quarantined, not saved.

Authoritative slate rules:
  1. authoritative.json is written ONCE from the first OFFICIAL_PREGAME run.
  2. Subsequent runs (LINEUP_RECHECK) may update NOT-YET-STARTED games.
  3. Started games are FROZEN — their official entry data cannot be overwritten.
  4. If a rerun contains sentinel prices or post-start prices for a game,
     that game is REJECTED (not the full slate).
  5. If widespread contamination (>50% games bad), quarantine the entire run.
  6. Post-slate review MUST use authoritative.json, never the latest recheck.

Sentinel prices (always hard-reject):
  19900, -19900, 100000, -100000 and any abs >= 19000
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Run type constants ────────────────────────────────────────────────────────
RUN_TYPE_OFFICIAL_PREGAME      = "OFFICIAL_PREGAME"
RUN_TYPE_LINEUP_RECHECK        = "LINEUP_RECHECK"
RUN_TYPE_IN_PLAY_RECHECK       = "IN_PLAY_RECHECK"
RUN_TYPE_REJECTED_CONTAMINATED = "REJECTED_CONTAMINATED"

ALL_RUN_TYPES = {
    RUN_TYPE_OFFICIAL_PREGAME,
    RUN_TYPE_LINEUP_RECHECK,
    RUN_TYPE_IN_PLAY_RECHECK,
    RUN_TYPE_REJECTED_CONTAMINATED,
}

# ── Sentinel detection ────────────────────────────────────────────────────────
SENTINEL_PRICES = {19900, -19900, 100000, -100000}
SENTINEL_ABS_THRESHOLD = 19000


def is_sentinel_price(value):
    """Return True if value is a known sentinel/impossible price."""
    if value is None:
        return False
    try:
        v = float(value)
        if v in SENTINEL_PRICES:
            return True
        if abs(v) >= SENTINEL_ABS_THRESHOLD:
            return True
        return False
    except (TypeError, ValueError):
        return False


def find_sentinel_in_object(obj, path=""):
    """Recursively scan an object for sentinel prices. Returns list of (path, value) tuples."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            found.extend(find_sentinel_in_object(v, sub))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_sentinel_in_object(v, f"{path}[{i}]"))
    elif isinstance(obj, (int, float)):
        if is_sentinel_price(obj):
            found.append((path, obj))
    return found


def parse_ts(ts_str):
    """Parse ISO timestamp → aware datetime. Returns None on failure."""
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_slate_dir(date_str, root_dir):
    """Return path to data/slates/YYYY-MM-DD/"""
    return os.path.join(root_dir, "data", "slates", date_str)


def get_authoritative_path(date_str, root_dir):
    """Return path to authoritative.json for a date."""
    return os.path.join(get_slate_dir(date_str, root_dir), "authoritative.json")


def authoritative_exists(date_str, root_dir):
    """Return True if authoritative.json exists for date."""
    return os.path.exists(get_authoritative_path(date_str, root_dir))


def load_authoritative(date_str, root_dir):
    """Load authoritative.json. Returns dict or None."""
    path = get_authoritative_path(date_str, root_dir)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def detect_run_type(date_str, root_dir, now_utc=None):
    """
    Determine the run type for the current slate generation.

    Rules:
    - If no authoritative.json exists → OFFICIAL_PREGAME
    - Else if all games not yet started → LINEUP_RECHECK
    - Else if some games started → IN_PLAY_RECHECK
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    if not authoritative_exists(date_str, root_dir):
        return RUN_TYPE_OFFICIAL_PREGAME

    auth = load_authoritative(date_str, root_dir)
    if not auth:
        return RUN_TYPE_OFFICIAL_PREGAME

    games = auth.get("games", [])
    any_started = False
    for g in games:
        start_str = g.get("startTime") or g.get("gameDate") or g.get("scheduledStartTime")
        start_dt = parse_ts(start_str)
        if start_dt and now_utc >= start_dt:
            any_started = True
            break

    if any_started:
        return RUN_TYPE_IN_PLAY_RECHECK
    return RUN_TYPE_LINEUP_RECHECK


def validate_game_for_rerun(game_entry, now_utc=None):
    """
    Validate a single game entry from a rerun for sentinel prices and start time.

    Returns:
        (is_valid, reason)
        is_valid=True if game can be used to update authoritative slate
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    game_pk = game_entry.get("gameId") or game_entry.get("gamePk")
    start_str = game_entry.get("startTime") or game_entry.get("gameDate") or game_entry.get("scheduledStartTime")
    start_dt = parse_ts(start_str)

    # Game already started → freeze
    if start_dt and now_utc >= start_dt:
        return False, f"Game {game_pk} already started at {start_str} — frozen, cannot update"

    # Scan for sentinel prices using field-aware scanner.
    # find_sentinel_in_object() is intentionally not used here because it scans ALL
    # numeric fields including non-price fields like gameId and volume, causing false
    # positives.  scan_for_sentinels() only checks known price/odds field names.
    try:
        from lib.sentinel_validator import scan_for_sentinels as _field_aware_scan
        sentinels = _field_aware_scan(game_entry)
        if sentinels:
            paths = ", ".join(f"{s['path']}={s['value']}" for s in sentinels[:5])
            return False, f"Game {game_pk} contains sentinel prices: {paths}"
    except ImportError:
        # Fallback: use find_sentinel_in_object (less precise)
        sentinels = find_sentinel_in_object(game_entry)
        if sentinels:
            paths = ", ".join(f"{p}={v}" for p, v in sentinels[:5])
            return False, f"Game {game_pk} contains sentinel prices: {paths}"

    return True, "OK"


def merge_rerun_into_authoritative(auth_data, rerun_data, run_type, now_utc=None):
    """
    Merge a rerun slate into the authoritative slate.

    Rules:
    - For each game in rerun:
        * If game already started → keep authoritative version (freeze)
        * If sentinel prices → reject only that game
        * If update improves lineup/pitcher completeness → update
    - If >50% games rejected → quarantine entire rerun

    Returns:
        (merged_data, run_report)
        run_report: dict with accepted_games, rejected_games, frozen_games, run_type
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    auth_games = {str(g.get("gameId") or g.get("gamePk")): g
                  for g in auth_data.get("games", [])}
    rerun_games = rerun_data.get("games", [])

    accepted = []
    rejected = []
    frozen = []

    for game in rerun_games:
        gpk = str(game.get("gameId") or game.get("gamePk") or "unknown")
        is_valid, reason = validate_game_for_rerun(game, now_utc)

        if not is_valid:
            if "already started" in reason or "frozen" in reason.lower():
                frozen.append({"gamePk": gpk, "reason": reason,
                               "action": "KEPT_AUTHORITATIVE"})
            else:
                rejected.append({"gamePk": gpk, "reason": reason,
                                 "action": "REJECTED_SENTINEL_OR_CONTAMINATED"})
        else:
            # Check if rerun improves lineup/pitcher completeness
            auth_game = auth_games.get(gpk, {})
            if _improves_completeness(auth_game, game):
                auth_games[gpk] = game
                accepted.append({"gamePk": gpk, "action": "UPDATED"})
            else:
                accepted.append({"gamePk": gpk, "action": "UNCHANGED_NO_IMPROVEMENT"})

    total = len(rerun_games)
    rejection_rate = len(rejected) / total if total > 0 else 0

    # Widespread contamination check (>50% rejected)
    if rejection_rate > 0.5 and len(rejected) >= 2:
        run_report = {
            "runType": RUN_TYPE_REJECTED_CONTAMINATED,
            "reason": f"Widespread contamination: {len(rejected)}/{total} games rejected",
            "accepted": accepted,
            "rejected": rejected,
            "frozen": frozen,
            "quarantined": True,
        }
        return auth_data, run_report  # Return unchanged authoritative

    merged = {**auth_data, "games": list(auth_games.values())}
    merged["lastRerunAt"] = now_utc.isoformat()
    merged["lastRunType"] = run_type

    run_report = {
        "runType": run_type,
        "accepted": accepted,
        "rejected": rejected,
        "frozen": frozen,
        "quarantined": False,
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "frozenCount": len(frozen),
    }

    return merged, run_report


def _improves_completeness(old_game, new_game):
    """
    Return True if new_game has better lineup/pitcher completeness than old_game.
    Completeness = more confirmed pitcher fields + more lineup fields.
    """
    def completeness_score(g):
        score = 0
        away = g.get("away", {})
        home = g.get("home", {})
        # Pitcher confirmed
        if away.get("pitcher") and away["pitcher"].get("id"):
            score += 2
        if home.get("pitcher") and home["pitcher"].get("id"):
            score += 2
        # Lineup confirmed
        if away.get("lineup") and len(away["lineup"]) >= 8:
            score += 3
        if home.get("lineup") and len(home["lineup"]) >= 8:
            score += 3
        # Market data
        if g.get("markets"):
            score += 1
        return score

    old_score = completeness_score(old_game)
    new_score = completeness_score(new_game)
    return new_score > old_score


def save_slate(date_str, root_dir, slate_data, run_type, timestamp_str=None):
    """
    Save a slate to the appropriate file based on run_type.

    OFFICIAL_PREGAME   → official_<timestamp>.json + authoritative.json (if not exists)
    LINEUP_RECHECK     → recheck_<timestamp>.json + update authoritative if valid
    IN_PLAY_RECHECK    → recheck_<timestamp>.json (frozen games not updated)
    REJECTED_CONTAMINATED → rejected_contaminated_<timestamp>.json (never touches authoritative)

    Returns:
        dict with saved paths and run report
    """
    slate_dir = get_slate_dir(date_str, root_dir)
    os.makedirs(slate_dir, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    if not timestamp_str:
        timestamp_str = now_utc.strftime("%Y%m%dT%H%M%SZ")

    auth_path = get_authoritative_path(date_str, root_dir)
    result = {"runType": run_type, "savedPaths": [], "runReport": None}

    if run_type == RUN_TYPE_REJECTED_CONTAMINATED:
        # Quarantine — never touch authoritative
        out_path = os.path.join(slate_dir, f"rejected_contaminated_{timestamp_str}.json")
        _write_json(out_path, {**slate_data, "_runType": run_type, "_quarantined": True})
        result["savedPaths"].append(out_path)
        result["runReport"] = {"runType": run_type, "quarantined": True}
        return result

    if run_type == RUN_TYPE_OFFICIAL_PREGAME:
        # First clean run
        official_path = os.path.join(slate_dir, f"official_{timestamp_str}.json")
        _write_json(official_path, {**slate_data, "_runType": run_type})
        result["savedPaths"].append(official_path)

        # Write authoritative.json ONLY if it doesn't exist
        if not os.path.exists(auth_path):
            _write_json(auth_path, {**slate_data, "_runType": run_type,
                                     "_authoritative": True,
                                     "_officialRunAt": now_utc.isoformat()})
            result["savedPaths"].append(auth_path)
            result["authoritativeWritten"] = True
        else:
            result["authoritativeWritten"] = False
            result["warning"] = "authoritative.json already exists — not overwritten"
        return result

    if run_type in (RUN_TYPE_LINEUP_RECHECK, RUN_TYPE_IN_PLAY_RECHECK):
        # Save recheck file
        recheck_path = os.path.join(slate_dir, f"recheck_{timestamp_str}.json")
        _write_json(recheck_path, {**slate_data, "_runType": run_type})
        result["savedPaths"].append(recheck_path)

        # Merge into authoritative if exists
        auth_data = load_authoritative(date_str, root_dir)
        if auth_data:
            merged, run_report = merge_rerun_into_authoritative(
                auth_data, slate_data, run_type, now_utc
            )
            result["runReport"] = run_report

            if run_report.get("quarantined"):
                # Widespread contamination — save as quarantined, don't update authoritative
                q_path = os.path.join(slate_dir, f"rejected_contaminated_{timestamp_str}.json")
                _write_json(q_path, {**slate_data, "_runType": RUN_TYPE_REJECTED_CONTAMINATED,
                                      "_quarantined": True, "_reason": run_report.get("reason")})
                result["savedPaths"].append(q_path)
                result["authoritativeUpdated"] = False
            else:
                # Update authoritative with frozen game protection
                _write_json(auth_path, merged)
                result["authoritativeUpdated"] = True
        else:
            result["warning"] = "No authoritative.json found — recheck saved but authoritative not updated"

        return result

    raise ValueError(f"Unknown run_type: {run_type}")


def _write_json(path, data):
    """Write JSON to path, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[slate_manager] Written: {path}")


def persist_tracked_tickers(date_str, root_dir, slate_data, run_id=None, run_type=None):
    """
    Persist tracked tickers at slate generation time.
    Writes to data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json

    Each ticker entry includes all required tracking fields.
    """
    snap_dir = os.path.join(root_dir, "data", "clv_snapshots", date_str)
    os.makedirs(snap_dir, exist_ok=True)

    tickers = []
    now_utc = datetime.now(timezone.utc)

    games = slate_data.get("games", [])
    for game in games:
        game_pk = str(game.get("gameId") or game.get("gamePk") or "")
        start_time = game.get("startTime") or game.get("gameDate")
        away_abbr = (game.get("away") or {}).get("abbr", "")
        home_abbr = (game.get("home") or {}).get("abbr", "")

        # Extract market rows
        markets = game.get("markets") or game.get("allEdges") or []
        for mkt in markets:
            ticker = mkt.get("ticker") or mkt.get("marketTicker")
            if not ticker:
                continue

            entry = {
                "slateDate": date_str,
                "runId": run_id or now_utc.strftime("%Y%m%dT%H%M%SZ"),
                "runType": run_type or "UNKNOWN",
                "gamePk": game_pk,
                "gameStartTime": start_time,
                "awayTeam": away_abbr,
                "homeTeam": home_abbr,
                "marketType": mkt.get("market") or mkt.get("marketType"),
                "side": mkt.get("side") or mkt.get("betSide"),
                "ticker": ticker,
                "entryPrice": mkt.get("price") or mkt.get("kalshiPrice"),
                "entryTimestamp": now_utc.isoformat(),
                "modelProbability": mkt.get("modelProb") or mkt.get("modelPct"),
                "kvfProbability": mkt.get("kalshiPct") or mkt.get("kvfPct"),
                "edge": mkt.get("edge") or mkt.get("edgePct"),
                "tier": mkt.get("confidence") or mkt.get("tier"),
                "betSize": mkt.get("betSize") or mkt.get("stake"),
                "trackingType": mkt.get("trackingType"),
                "actuallyPlaced": mkt.get("actuallyPlaced", False),
                "placementConfirmedAt": mkt.get("placementConfirmedAt"),
                "blockStatus": mkt.get("blocked"),
                "blockReason": mkt.get("blockReason") or mkt.get("paperReason"),
                "blockClass": mkt.get("blockClass"),
            }
            tickers.append(entry)

    out_path = os.path.join(snap_dir, "tracked_tickers.json")
    with open(out_path, "w") as f:
        json.dump({
            "date": date_str,
            "generatedAt": now_utc.isoformat(),
            "runId": run_id,
            "runType": run_type,
            "tickerCount": len(tickers),
            "tickers": tickers,
        }, f, indent=2)

    print(f"[slate_manager] Tracked tickers persisted: {out_path} ({len(tickers)} tickers)")
    return out_path
