#!/usr/bin/env python3
"""
scripts/research/build_inning_result_shadow_ledger.py
==========================================================
Model Performance Phase 2A, Part 9 -- builds
data/research/inning_result_shadow_ledger.json from ALREADY-SAVED,
LOCAL repository data. Makes NO live network call.

Market side (tickers, YES bid/ask, volume): 100% REAL, read from
data/kalshi_market_registry.json's `registry[*].markets.f5_moneyline`
(the persistent, already-committed source of truth this repository's
own production pipeline built from real Kalshi prices).

Projection side (away/home run expectation used to compute
canonicalModelProb/legacyConditionalProb): this repository does NOT
persist the exact away/home full-game run projection production
computed at evaluation time anywhere joinable by game after the fact
(scripts/build_market_ledger.py computes it in-memory from live
enrichment data and does not write it back to the archived slate).
Rather than fabricate or guess at reproducing production's full
formula (starter xFIP, park factor, lineup adjustments, etc. -- see
docs/research/PROJECTION_AUDIT.md's full checklist), this script uses
a documented, SIMPLE, transparent research proxy: a blend of each
team's `last15RpG` and `seasonRpG` (the same two inputs
docs/research/PROJECTION_AUDIT.md confirmed production itself blends,
though not necessarily with the same weights or the additional
factors production applies). This is clearly labeled
`projectionMethod: "research_proxy_last15_season_blend"` in every row
-- NEVER presented as reproducing production's real-money projection.

Only F3/F5/F7 inning-result markets are in scope for this ledger (per
Part 9) -- F3/F7 are not yet ingested by this repository at all (see
docs/research/INNING_RESULT_MIGRATION.md), so every row in the
generated artifact today is F5 (the only horizon with real market
data currently available). This is expected and honestly reported,
not a bug in this script.
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_shadow_ledger import build_shadow_ledger

REGISTRY_PATH = os.path.join(ROOT, "data", "kalshi_market_registry.json")
SLATES_GLOB = os.path.join(ROOT, "data", "slates", "*", "authoritative.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "research", "inning_result_shadow_ledger.json")

PROJECTION_METHOD = "research_proxy_last15_season_blend"


def _team_run_proxy(team_stats):
    """
    Pure. Simple, documented, transparent projection proxy -- NOT a
    reproduction of production's real formula (see module docstring).
    Returns None if the required fields are absent.
    """
    if not team_stats:
        return None
    last15 = team_stats.get("last15RpG")
    season = team_stats.get("seasonRpG")
    if last15 is None or season is None:
        return None
    return round(0.5 * float(last15) + 0.5 * float(season), 4)


def _load_latest_authoritative_slates():
    """Returns {(away_abbr, home_abbr): game_dict} from the most recent
    archived authoritative slate that has any games, so the projection
    proxy can be joined against a real (if not perfectly date-matched)
    team-stats snapshot."""
    paths = sorted(glob.glob(SLATES_GLOB))
    for path in reversed(paths):
        try:
            with open(path) as f:
                d = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        games = d.get("games", [])
        if games:
            by_teams = {}
            for g in games:
                away = (g.get("away") or {}).get("abbr")
                home = (g.get("home") or {}).get("abbr")
                if away and home:
                    by_teams[(away, home)] = g
            if by_teams:
                return by_teams, os.path.relpath(path, ROOT)
    return {}, None


def build_ledger():
    with open(REGISTRY_PATH) as f:
        registry_doc = json.load(f)
    registry = registry_doc.get("registry", {})
    slate_games, slate_source = _load_latest_authoritative_slates()

    all_rows = []
    games_with_f5 = 0
    games_with_projection = 0

    for key, entry in sorted(registry.items()):
        f5 = (entry.get("markets") or {}).get("f5_moneyline")
        if not f5:
            continue
        games_with_f5 += 1
        away, home = entry.get("away"), entry.get("home")

        markets = []
        prices = f5.get("prices", {})
        for side, ticker_key in (("away", "away_ticker"), ("home", "home_ticker"), ("tie", "tie_ticker")):
            ticker = f5.get(ticker_key)
            pb = prices.get(side) or {}
            if not ticker:
                continue
            markets.append({
                "market_ticker": ticker,
                "event_ticker": f5.get("eventTicker") or ticker.rsplit("-", 1)[0],
                "yes_bid": pb.get("yes_bid"),
                "yes_ask": pb.get("yes_ask"),
                "volume": None,
            })

        slate_game = slate_games.get((away, home))
        away_proj = _team_run_proxy((slate_game or {}).get("awayTeamStats"))
        home_proj = _team_run_proxy((slate_game or {}).get("homeTeamStats"))
        if away_proj is not None and home_proj is not None:
            games_with_projection += 1

        context = {
            "away_team": away,
            "home_team": home,
            "away_full_proj": away_proj,
            "home_full_proj": home_proj,
            "snapshot_timestamp": entry.get("snapshot_ts"),
        }
        rows = build_shadow_ledger(entry.get("date"), key, f"{away}@{home}", markets, context)
        for row in rows:
            row["projectionMethod"] = PROJECTION_METHOD if away_proj is not None else None
            row["projectionSource"] = slate_source if away_proj is not None else None
        all_rows.extend(rows)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generatorScript": "scripts/research/build_inning_result_shadow_ledger.py",
        "note": (
            "RESEARCH-ONLY, SHADOW/PAPER-ONLY artifact. Market-side data "
            "(tickers, YES bid/ask) is REAL, read from "
            "data/kalshi_market_registry.json. Projection-side data uses a "
            f"documented simple proxy ({PROJECTION_METHOD}), NOT a "
            "reproduction of production's real projection formula -- see "
            "this script's module docstring. Never consumed by production "
            "betting logic. No real-money eligible row exists in this "
            "artifact by construction."
        ),
        "registrySource": os.path.relpath(REGISTRY_PATH, ROOT),
        "projectionSlateSource": slate_source,
        "gamesWithF5Markets": games_with_f5,
        "gamesWithProjectionProxy": games_with_projection,
        "totalRows": len(all_rows),
        "rows": all_rows,
    }


def main():
    ledger = build_ledger()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
    print(f"Wrote {ledger['totalRows']} shadow-ledger rows to {os.path.relpath(OUTPUT_PATH, ROOT)} "
          f"({ledger['gamesWithF5Markets']} games with F5 markets, "
          f"{ledger['gamesWithProjectionProxy']} with a projection proxy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
