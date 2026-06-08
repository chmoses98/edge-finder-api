#!/usr/bin/env python3
"""
scripts/post_fetch_gate.py v1.0
================================
Data quality gate run immediately after fetch_savant_pitchers.py and fetch_lineups.py.
Fails the workflow (exit 1) if critical fields are null or scripts produced error output.

Checks:
  1. pitcherSavant blocks present and non-null for all games
  2. lineupConfirmed flag present for all teams
  3. offenseBaselineAdj computable (last7RpG or last15RpG present)
  4. No game has both starters with null xFIP (total projection impossible)
  5. Kalshi registry exists and has entries for today

Does NOT check Kalshi prices — those are downstream of merge_odds.py.
"""

import json, sys, os
from datetime import datetime, timezone, timedelta

ET = timezone(timedelta(hours=-4))
TODAY = datetime.now(ET).strftime('%Y-%m-%d')

errors = []
warnings = []

def fail(msg):   errors.append(msg)
def warn(msg):   warnings.append(msg)


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

print(f"post_fetch_gate: {len(games)} games loaded from slate.json")


# ── 2. pitcherSavant checks ───────────────────────────────────────────────────
null_xfip_games = 0
for g in games:
    away_abbr = g.get('away', {}).get('abbr', '?')
    home_abbr  = g.get('home', {}).get('abbr', '?')
    gid = f"{away_abbr}@{home_abbr}"

    for side, abbr in [('away', away_abbr), ('home', home_abbr)]:
        ps = g.get(side, {}).get('pitcherSavant')
        if ps is None:
            fail(f"{gid}/{side}: pitcherSavant block is null — "
                 f"fetch_savant_pitchers.py produced no data for this starter. "
                 f"Check if the pitcher ID is valid and Savant returned data.")
            continue

        xfip = ps.get('xFIP')
        season_fip = ps.get('seasonFIP')
        if xfip is None and season_fip is None:
            fail(f"{gid}/{side}: pitcherSavant.xFIP=null AND seasonFIP=null — "
                 f"no xFIP data available. Poisson projection impossible.")
        elif xfip is None:
            warn(f"{gid}/{side}: pitcherSavant.xFIP=null, using seasonFIP={season_fip} as fallback")

        rfip = ps.get('recentFIP')
        if rfip is not None and rfip < 0:
            # This is a pipeline bug (negative FIP on tiny sample) — warn but don't fail
            # build_market_ledger.py handles this by clamping to xFIP
            warn(f"{gid}/{side}: recentFIP={rfip} is negative (startsSampled={ps.get('startsSampled')}) — "
                 f"build_market_ledger.py will use xFIP={xfip} instead (clamped). "
                 f"Root cause: FIP formula produces negative result on 1-start sample. "
                 f"Fix fetch_savant_pitchers.py to floor recentFIP at 0.0 for samples < 3.")

    # Both starters null xFIP is a hard fail (can't project the game at all)
    away_ps = g.get('away', {}).get('pitcherSavant') or {}
    home_ps  = g.get('home', {}).get('pitcherSavant') or {}
    away_has_xfip = away_ps.get('xFIP') is not None or away_ps.get('seasonFIP') is not None
    home_has_xfip  = home_ps.get('xFIP') is not None or home_ps.get('seasonFIP') is not None
    if not away_has_xfip and not home_has_xfip:
        null_xfip_games += 1
        fail(f"{gid}: BOTH starters have no xFIP/seasonFIP — game projection completely impossible")

if null_xfip_games > 3:
    fail(f"{null_xfip_games} games with dual null xFIP — "
         f"fetch_savant_pitchers.py likely failed entirely (not just continue-on-error suppressed)")


# ── 3. teamstats / lineup checks ──────────────────────────────────────────────
for g in games:
    away_abbr = g.get('away', {}).get('abbr', '?')
    home_abbr  = g.get('home', {}).get('abbr', '?')
    gid = f"{away_abbr}@{home_abbr}"

    for side_key, abbr in [('awayTeamStats', away_abbr), ('homeTeamStats', home_abbr)]:
        ts = g.get(side_key)
        if not ts:
            fail(f"{gid}/{side_key}: teamStats block entirely missing — "
                 f"enrich_data.py has not run or team not found in teamstats.json")
            continue

        # lineupConfirmed must be a bool (not null)
        lc = ts.get('lineupConfirmed')
        if lc is None:
            fail(f"{gid}/{side_key}: lineupConfirmed=null — "
                 f"fetch_lineups.py produced no output for this team")

        # Rolling R/G: at least one of last7 or last15 must be present
        l7  = ts.get('last7RpG')
        l15 = ts.get('last15RpG')
        szn = ts.get('runsPerGame') or ts.get('seasonRpG')
        if l7 is None and l15 is None and szn is None:
            fail(f"{gid}/{side_key}: last7RpG, last15RpG, AND runsPerGame all null — "
                 f"offense_baseline computation impossible")
        elif l7 is None and l15 is None:
            warn(f"{gid}/{side_key}: last7RpG and last15RpG null, using season only ({szn})")


# ── 4. Kalshi registry ────────────────────────────────────────────────────────
registry_path = 'data/kalshi_market_registry.json'
if not os.path.exists(registry_path):
    fail("data/kalshi_market_registry.json not found — "
         "build_kalshi_registry.py must run before this gate")
else:
    with open(registry_path) as f:
        reg_doc = json.load(f)
    registry = reg_doc.get('registry', {})
    reg_date  = reg_doc.get('date', '')

    if reg_date != TODAY:
        fail(f"kalshi_market_registry.json date={reg_date} != today={TODAY} — "
             f"registry is stale, build_kalshi_registry.py must re-run")

    if len(registry) == 0:
        fail("kalshi_market_registry.json has 0 entries — "
             "Kalshi API calls in build_kalshi_registry.py returned nothing")
    else:
        print(f"  Kalshi registry: {len(registry)} games, date={reg_date}")
        games_with_moneyline = sum(1 for e in registry.values() if 'moneyline' in e.get('markets', {}))
        games_with_tt        = sum(1 for e in registry.values() if 'team_total_away' in e.get('markets', {}))
        games_with_rfi       = sum(1 for e in registry.values() if 'rfi' in e.get('markets', {}))
        print(f"  Registry coverage: ML={games_with_moneyline} TT={games_with_tt} RFI={games_with_rfi} of {len(registry)} games")

        if games_with_moneyline < len(registry) * 0.8:
            fail(f"Registry: only {games_with_moneyline}/{len(registry)} games have ML — "
                 f"Kalshi ML fetch failed for most games")

        # Games with TT < all games is OK (some games legitimately have no TT posted yet)
        # but flag it so it's visible
        if games_with_tt < len(registry):
            warn(f"Registry: {len(registry) - games_with_tt} games missing TT markets "
                 f"({games_with_tt}/{len(registry)} have TT) — "
                 f"kalshi_search.json fallback should cover these in merge_odds.py")


# ── 5. Output ─────────────────────────────────────────────────────────────────
print()
if warnings:
    print(f"WARNINGS ({len(warnings)}):")
    for w in warnings:
        print(f"  ⚠  {w}")
    print()

if errors:
    print(f"GATE FAILED — {len(errors)} error(s):", file=sys.stderr)
    for e in errors:
        print(f"  ✗ {e}", file=sys.stderr)
    print("\nFix these data issues before proceeding to odds fetch.", file=sys.stderr)
    sys.exit(1)

print(f"GATE PASSED — {len(games)} games, {len(warnings)} warnings")
sys.exit(0)
