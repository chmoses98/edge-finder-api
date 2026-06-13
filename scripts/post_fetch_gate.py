#!/usr/bin/env python3
"""
scripts/post_fetch_gate.py v2.0
================================
Data quality gate — runs after fetch_savant_pitchers.py and fetch_lineups.py,
BEFORE odds fetch, Kalshi registry build, merge_odds, and enrich_data.

NEW in v2.0:
  - Accepts requested_date as first CLI argument (passed from GitHub Actions as $DATE)
  - Hard-fails if slate.json date != requested_date (STALE DATE detection)
  - Writes data/fetch_status.json on both pass and fail
  - Prints STALE SLATE ABORT messages on date mismatch

At this point in the pipeline:
  - slate.json has games + pitcherSavant blocks (from Vercel)
  - savant enrichment has run (fbPct, TTO, velocity — may be partial)
  - lineups have been fetched (may be partial if not yet posted)
  - teamstats are loaded

Hard FAIL (exit 1) — pipeline genuinely broken:
  - slate.json missing or empty
  - slate.json date != requested_date (STALE DATA)
  - BOTH starters in the same game have null xFIP AND null seasonFIP
  - >50% of games have dual null xFIP (fetch_savant_pitchers likely fully failed)

WARN (continue, log) — data incomplete but pipeline can recover:
  - Single side pitcherSavant=null
  - lineupConfirmed=null
  - last7RpG and last15RpG both null
  - xFIP=null but seasonFIP available
"""

import json, sys, os
from datetime import datetime, timezone, timedelta

ET = timezone(timedelta(hours=-4))
TODAY = datetime.now(ET).strftime('%Y-%m-%d')

# Accept requested_date as first CLI arg (GitHub Actions passes $DATE)
REQUESTED_DATE = sys.argv[1] if len(sys.argv) > 1 else TODAY

errors   = []
warnings = []

def fail(msg): errors.append(msg)
def warn(msg): warnings.append(msg)


def write_fetch_status(status, requested_date, actual_date, reason=None):
    """Write data/fetch_status.json with the current gate result."""
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if status == "OK":
        payload = {
            "status": "OK",
            "requestedDate": requested_date,
            "actualDate": actual_date,
            "fetchedAt": now_utc,
            "source": "fetch-slate/post_fetch_gate"
        }
    else:
        payload = {
            "status": status,
            "requestedDate": requested_date,
            "actualDate": actual_date,
            "failedAt": now_utc,
            "source": "fetch-slate/post_fetch_gate",
            "reason": reason or "Gate check failed"
        }
    os.makedirs("data", exist_ok=True)
    with open("data/fetch_status.json", "w") as f:
        json.dump(payload, f, indent=2)


def safe_side(g, side):
    """Return side dict safely — never raises even if side is None."""
    v = g.get(side)
    return v if isinstance(v, dict) else {}


# ── 1. slate.json baseline ────────────────────────────────────────────────────
slate_path = 'data/slate.json'
if not os.path.exists(slate_path):
    msg = "GATE FAIL: data/slate.json not found"
    print(msg, file=sys.stderr)
    print(f"STALE SLATE ABORT: requested={REQUESTED_DATE} actual=missing source=data/slate.json",
          file=sys.stderr)
    write_fetch_status("FAILED_STALE_DATE", REQUESTED_DATE, "missing",
                       "slate.json not found")
    sys.exit(1)

with open(slate_path) as f:
    slate = json.load(f)

games = slate.get('games', [])
if not games:
    msg = "GATE FAIL: data/slate.json has no games"
    print(msg, file=sys.stderr)
    print(f"STALE SLATE ABORT: requested={REQUESTED_DATE} actual=no-games source=data/slate.json",
          file=sys.stderr)
    write_fetch_status("FAILED_STALE_DATE", REQUESTED_DATE, "no-games",
                       "slate.json has no games")
    sys.exit(1)

# ── 1b. STALE DATE GUARD: slate date must match requested date ────────────────
slate_date = slate.get('date', '')
if not slate_date:
    print(f"STALE SLATE ABORT: requested={REQUESTED_DATE} actual=missing-date-field "
          f"source=data/slate.json", file=sys.stderr)
    write_fetch_status("FAILED_STALE_DATE", REQUESTED_DATE, "missing-date-field",
                       "slate.json has no date field")
    sys.exit(1)

if slate_date != REQUESTED_DATE:
    print(
        f"STALE SLATE ABORT: requested={REQUESTED_DATE} actual={slate_date} "
        f"source=data/slate.json — Vercel API returned wrong-date slate",
        file=sys.stderr
    )
    write_fetch_status(
        "FAILED_STALE_DATE",
        REQUESTED_DATE,
        slate_date,
        f"Fetched slate date {slate_date!r} did not match requested date {REQUESTED_DATE!r}"
    )
    sys.exit(1)

print(f"post_fetch_gate v2.0: {len(games)} games loaded from slate.json "
      f"(date: {slate_date}) — requested: {REQUESTED_DATE}")

# Also validate each game's startTime maps to requested date in ET
months_abbr = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
for g in games:
    away_abbr = safe_side(g, 'away').get('abbr', '?')
    home_abbr = safe_side(g, 'home').get('abbr', '?')
    gid = f"{away_abbr}@{home_abbr}"
    start_time = g.get('startTime') or g.get('gameTime')
    if start_time:
        try:
            if start_time.endswith('Z'):
                start_time = start_time[:-1] + '+00:00'
            dt = datetime.fromisoformat(start_time)
            dt_et = dt.astimezone(ET)
            game_date_et = dt_et.strftime('%Y-%m-%d')
            if game_date_et != REQUESTED_DATE:
                print(
                    f"STALE SLATE ABORT: requested={REQUESTED_DATE} actual={game_date_et} "
                    f"source=data/slate.json[{gid}].startTime",
                    file=sys.stderr
                )
                write_fetch_status(
                    "FAILED_STALE_DATE",
                    REQUESTED_DATE,
                    game_date_et,
                    f"Game {gid} startTime maps to {game_date_et}, not {REQUESTED_DATE}"
                )
                sys.exit(1)
        except Exception:
            pass  # Unparseable startTime — skip


# ── 2. pitcherSavant checks ───────────────────────────────────────────────────
null_xfip_games = 0
tbd_starters    = 0
for g in games:
    away_side = safe_side(g, 'away')
    home_side = safe_side(g, 'home')
    away_abbr = away_side.get('abbr', '?')
    home_abbr = home_side.get('abbr', '?')
    gid = f"{away_abbr}@{home_abbr}"

    for side_label, side_data in [('away', away_side), ('home', home_side)]:
        ps = side_data.get('pitcherSavant')

        if ps is None:
            pitcher = side_data.get('pitcher')
            pitcher_name = pitcher.get('name', '') if isinstance(pitcher, dict) else ''
            if pitcher_name:
                warn(f"{gid}/{side_label}: pitcherSavant=null for {pitcher_name} "
                     f"— Savant data not available (new pitcher or not in leaderboard)")
            else:
                warn(f"{gid}/{side_label}: pitcherSavant=null, starter TBD "
                     f"— game will use league-average xFIP fallback")
            tbd_starters += 1
            continue

        if not isinstance(ps, dict):
            fail(f"{gid}/{side_label}: pitcherSavant is not a dict (type={type(ps).__name__})")
            continue

        xfip      = ps.get('xFIP')
        season_fip = ps.get('seasonFIP')
        if xfip is None and season_fip is None:
            fail(f"{gid}/{side_label}: pitcherSavant.xFIP=null AND seasonFIP=null "
                 f"— no xFIP data available; projection requires at least one")
        elif xfip is None:
            warn(f"{gid}/{side_label}: xFIP=null, fallback to seasonFIP={season_fip}")

        rfip = ps.get('recentFIP')
        if rfip is not None and rfip < 0:
            warn(f"{gid}/{side_label}: recentFIP={rfip} is negative "
                 f"(startsSampled={ps.get('startsSampled')}) — "
                 f"should have been cleared by fetch_savant_pitchers.py v5.1")

    away_ps = away_side.get('pitcherSavant') or {}
    home_ps = home_side.get('pitcherSavant') or {}
    away_has_xfip = isinstance(away_ps, dict) and (
        away_ps.get('xFIP') is not None or away_ps.get('seasonFIP') is not None)
    home_has_xfip = isinstance(home_ps, dict) and (
        home_ps.get('xFIP') is not None or home_ps.get('seasonFIP') is not None)

    if not away_has_xfip and not home_has_xfip:
        null_xfip_games += 1
        fail(f"{gid}: BOTH starters have no xFIP/seasonFIP — "
             f"game projection completely impossible")

if tbd_starters > 0:
    print(f"  TBD/null pitcherSavant: {tbd_starters} starters "
          f"(will use league-average xFIP=4.50 fallback in projections)")

if null_xfip_games > len(games) * 0.5:
    fail(f"{null_xfip_games}/{len(games)} games with dual null xFIP — "
         f"fetch_savant_pitchers.py likely failed entirely")


# ── 3. teamstats / lineup checks ─────────────────────────────────────────────
lineup_not_confirmed = 0
no_rolling_rpg       = 0

for g in games:
    away_side = safe_side(g, 'away')
    home_side = safe_side(g, 'home')
    away_abbr = away_side.get('abbr', '?')
    home_abbr = home_side.get('abbr', '?')
    gid = f"{away_abbr}@{home_abbr}"

    for side_key, abbr in [('awayTeamStats', away_abbr), ('homeTeamStats', home_abbr)]:
        ts = g.get(side_key)
        if not ts:
            warn(f"{gid}/{side_key}: teamStats block missing — "
                 f"team may not be in teamstats.json (expansion team?) "
                 f"or enrich_data.py hasn't run yet")
            continue

        lc = ts.get('lineupConfirmed')
        if lc is None:
            warn(f"{gid}/{side_key}: lineupConfirmed=null — "
                 f"lineups not yet posted (expected, safe to continue)")
            lineup_not_confirmed += 1

        l7  = ts.get('last7RpG')
        l15 = ts.get('last15RpG')
        szn = ts.get('runsPerGame') or ts.get('seasonRpG')
        if l7 is None and l15 is None and szn is None:
            fail(f"{gid}/{side_key}: last7RpG, last15RpG, AND runsPerGame all null — "
                 f"offense baseline computation impossible")
            no_rolling_rpg += 1
        elif l7 is None and l15 is None:
            warn(f"{gid}/{side_key}: rolling R/G null, using season ({szn}) only")

if lineup_not_confirmed > 0:
    print(f"  Lineups not yet confirmed: {lineup_not_confirmed} teams "
          f"(expected before ~1pm ET)")


# ── 4. Output ─────────────────────────────────────────────────────────────────
print()
if warnings:
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings:
        print(f"  [WARN]  {w}")
    print()

if errors:
    print(f"GATE FAILED — {len(errors)} hard error(s):", file=sys.stderr)
    for e in errors:
        print(f"  [FAIL] {e}", file=sys.stderr)
    print("\nThese are pipeline failures, not data timing issues.", file=sys.stderr)
    write_fetch_status(
        "FAILED_GATE",
        REQUESTED_DATE,
        slate_date,
        f"{len(errors)} hard gate error(s): " + "; ".join(errors[:3])
    )
    sys.exit(1)

write_fetch_status("OK", REQUESTED_DATE, slate_date)
print(f"GATE PASSED — {len(games)} games, {len(warnings)} warnings, date={slate_date} OK")
sys.exit(0)
