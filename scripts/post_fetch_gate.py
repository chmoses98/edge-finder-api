#!/usr/bin/env python3
"""
scripts/post_fetch_gate.py v1.1
================================
Data quality gate — runs after fetch_savant_pitchers.py and fetch_lineups.py,
BEFORE odds fetch, Kalshi registry build, merge_odds, and enrich_data.

At this point in the pipeline:
  - slate.json has games + pitcherSavant blocks (from Vercel)
  - savant enrichment has run (fbPct, TTO, velocity — may be partial)
  - lineups have been fetched (may be partial if not yet posted)
  - teamstats are loaded

What is NOT yet present (don't check these here):
  - kalshi_market_registry.json (built later by build_kalshi_registry.py)
  - odds.kalshi.* fields (populated later by merge_odds.py)
  - offenseBaselineAdj (populated later by enrich_data.py)
  - marketLedger (populated later by build_market_ledger.py)

Hard FAIL (exit 1) — pipeline genuinely broken:
  - slate.json missing or empty
  - BOTH starters in the same game have null xFIP AND null seasonFIP
    (projection completely impossible for that game)
  - >50% of games have dual null xFIP (fetch_savant_pitchers likely fully failed)

WARN (continue, log) — data incomplete but pipeline can recover:
  - Single side pitcherSavant=null (TBD starter, expected early in day)
  - lineupConfirmed=null (lineups not yet posted, expected before ~1pm ET)
  - last7RpG and last15RpG both null (season-only data, merge_odds fallback)
  - xFIP=null but seasonFIP available as fallback

v1.1 changes:
  - null pitcherSavant (single side) is now WARN, not FAIL (handles TBD starters)
  - Removed kalshi_market_registry.json check — it runs too early in new pipeline order
  - lineupConfirmed=null is WARN, not FAIL
  - Added null-safe side_data access (same pitcher=null pattern as fetch_savant_pitchers)
"""

import json, sys, os
from datetime import datetime, timezone, timedelta

ET = timezone(timedelta(hours=-4))
TODAY = datetime.now(ET).strftime('%Y-%m-%d')

errors   = []
warnings = []

def fail(msg): errors.append(msg)
def warn(msg): warnings.append(msg)


def safe_side(g, side):
    """Return side dict safely — never raises even if side is None."""
    v = g.get(side)
    return v if isinstance(v, dict) else {}


# ── 1. slate.json baseline ────────────────────────────────────────────────────
slate_path = 'data/slate.json'
if not os.path.exists(slate_path):
    print("GATE FAIL: data/slate.json not found", file=sys.stderr)
    sys.exit(1)

with open(slate_path) as f:
    slate = json.load(f)

games = slate.get('games', [])
if not games:
    print("GATE FAIL: data/slate.json has no games", file=sys.stderr)
    sys.exit(1)

print(f"post_fetch_gate v1.1: {len(games)} games loaded from slate.json "
      f"(date: {slate.get('date', '?')})")


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
            # TBD starter — expected when lineup not posted yet
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

    # Hard fail: BOTH starters in a game have no usable xFIP
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

# Hard fail if majority of games have dual null xFIP — likely a full fetch failure
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
            # Missing teamStats is a real problem — enrich_data hasn't run yet,
            # but teamstats.json should have been loaded
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
        print(f"  ⚠  {w}")
    print()

if errors:
    print(f"GATE FAILED — {len(errors)} hard error(s):", file=sys.stderr)
    for e in errors:
        print(f"  ✗ {e}", file=sys.stderr)
    print("\nThese are pipeline failures, not data timing issues.", file=sys.stderr)
    sys.exit(1)

print(f"GATE PASSED — {len(games)} games, {len(warnings)} warnings")
sys.exit(0)
