#!/usr/bin/env python3
"""
scripts/enrich_lineup_confirmed.py — v1.0
Promotes team-level lineupConfirmed (from fetch_lineups.py / MLB Stats API)
to a game-level lineupConfirmed field on each game object in slate.json.

Also attempts RotoWire confirmation as a secondary source.
Falls back gracefully if RotoWire is unavailable.

Game-level rules:
  lineupConfirmed = True  → BOTH teams have lineupConfirmed=True in their teamStats
  lineupConfirmed = False → either team missing/False

Added fields per game:
  lineupConfirmed   bool
  lineupSource      str   ("mlb_statsapi" | "mlb_statsapi+rotowire" | "unavailable")
  lineupStatus      str   ("confirmed" | "partial" | "unconfirmed")
  lineupCheckedAt   str   ISO timestamp
"""

import json
import re
import urllib.request
import ssl
from datetime import datetime, timezone

SLATE_PATH = "data/slate.json"

def now_utc():
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_rotowire():
    """
    Fetch RotoWire daily lineups page and return set of game keys
    where BOTH teams have confirmed lineups.
    Returns (confirmed_game_keys: set, source_ok: bool)
    confirmed_game_keys uses frozensets of abbrs: e.g. frozenset({'MIA','PHI'})
    """
    url = "https://www.rotowire.com/baseball/daily-lineups.php"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.rotowire.com/",
    }
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  RotoWire fetch failed: {e}")
        return set(), False

    # RotoWire marks confirmed lineups with class "is-confirmed" or text "CONFIRMED"
    # The page structure uses team abbreviations inside lineup boxes
    # We look for game containers with confirmed status
    confirmed_keys = set()

    # Pattern: look for lineup boxes that contain "confirmed" class or text
    # RotoWire uses: <div class="lineup__status ... is-confirmed"> or similar
    game_blocks = re.findall(
        r'<div[^>]+class="[^"]*lineup[^"]*"[^>]*>(.*?)</div\s*>',
        html, re.DOTALL | re.IGNORECASE
    )

    # Simpler approach: find all instances of "CONFIRMED" near team abbreviation pairs
    confirmed_sections = re.findall(
        r'((?:is-confirmed|CONFIRMED).{0,500})',
        html, re.DOTALL | re.IGNORECASE
    )

    # Extract team abbrs from those sections
    # RotoWire uses 2-3 letter team codes in their lineup display
    MLB_ABBRS = {
        'MIA', 'PHI', 'KC', 'WSH', 'NYM', 'CIN', 'SD', 'STL',
        'COL', 'CHC', 'MIN', 'TEX', 'DET', 'HOU', 'LAA', 'AZ',
        'PIT', 'OAK', 'TB', 'LAD', 'NYY', 'BOS', 'ATL', 'SF',
        'SEA', 'CLE', 'MIL', 'TOR', 'BAL', 'CWS', 'ATH'
    }

    for section in confirmed_sections:
        found = set(re.findall(r'\b([A-Z]{2,3})\b', section))
        teams_in_section = found & MLB_ABBRS
        if len(teams_in_section) >= 2:
            # Treat pairs as confirmed
            teams_list = list(teams_in_section)
            for i in range(len(teams_list)):
                for j in range(i+1, len(teams_list)):
                    confirmed_keys.add(frozenset({teams_list[i], teams_list[j]}))

    print(f"  RotoWire: found {len(confirmed_keys)} confirmed game keys from page")
    return confirmed_keys, True


def main():
    checked_at = now_utc()

    with open(SLATE_PATH) as f:
        slate = json.load(f)

    games = slate.get("games", [])
    print(f"Enriching lineup confirmation for {len(games)} games...")

    # Attempt RotoWire (secondary source, non-blocking)
    rw_keys, rw_ok = fetch_rotowire()
    rw_source = "rotowire" if rw_ok else None

    confirmed_count = 0
    partial_count = 0
    unconfirmed_count = 0

    for g in games:
        away_data = g.get("away", {})
        home_data = g.get("home", {})
        away_abbr = away_data.get("abbr", "") if isinstance(away_data, dict) else ""
        home_abbr = home_data.get("abbr", "") if isinstance(home_data, dict) else ""

        # PRIMARY: MLB Stats API results from fetch_lineups.py
        ats = g.get("awayTeamStats", {}) or {}
        hts = g.get("homeTeamStats", {}) or {}
        away_mlb = ats.get("lineupConfirmed", False)
        home_mlb = hts.get("lineupConfirmed", False)

        # Game-level: both must confirm
        mlb_confirmed = bool(away_mlb) and bool(home_mlb)
        either_mlb = bool(away_mlb) or bool(home_mlb)

        # SECONDARY: RotoWire cross-check
        rw_confirmed = False
        if rw_ok and away_abbr and home_abbr:
            game_key = frozenset({away_abbr, home_abbr})
            rw_confirmed = game_key in rw_keys

        # Final decision
        # True = both confirmed by MLB Stats API
        # (RotoWire adds supplemental confirmation but doesn't override MLB false)
        final_confirmed = mlb_confirmed

        # Build source label
        sources = ["mlb_statsapi"]
        if rw_ok:
            sources.append("rotowire")
        source_str = "+".join(sources)

        # Status string
        if final_confirmed:
            status = "confirmed"
            confirmed_count += 1
        elif either_mlb:
            status = "partial"
            partial_count += 1
        else:
            status = "unconfirmed"
            unconfirmed_count += 1

        g["lineupConfirmed"] = final_confirmed
        g["lineupSource"] = source_str if final_confirmed or either_mlb else "unavailable"
        g["lineupStatus"] = status
        g["lineupCheckedAt"] = checked_at

        rw_note = f"(RotoWire: {'✓' if rw_confirmed else '✗'})" if rw_ok else ""
        away_batters = ats.get("lineupBattersResolved", 0)
        home_batters = hts.get("lineupBattersResolved", 0)
        print(
            f"  {away_abbr}@{home_abbr}: "
            f"away={away_mlb}({away_batters}/9) home={home_mlb}({home_batters}/9) "
            f"→ lineupConfirmed={final_confirmed} [{status}] {rw_note}"
        )

    with open(SLATE_PATH, "w") as f:
        json.dump(slate, f)

    print(f"\nDone. Confirmed={confirmed_count} Partial={partial_count} Unconfirmed={unconfirmed_count}")
    print(f"Written: {SLATE_PATH}")
    print(f"RotoWire available: {rw_ok}")


if __name__ == "__main__":
    main()
