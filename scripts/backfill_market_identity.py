#!/usr/bin/env python3
"""
PHASE 5 — BACKFILL EXISTING BETS
Attempts to match existing bets to Kalshi market tickers using
registry snapshots and Kalshi market data.

Backfill classification:
  SUCCESSFULLY_MATCHED — exact, non-ambiguous ticker assigned
  PARTIALLY_MATCHED    — event_ticker found but market_ticker ambiguous
  UNMATCHABLE          — no registry snapshot covering this date

Only assigns when match is exact. Never guesses.
"""
import json, os, re, sys
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError

BETS_PATH = os.path.join(os.path.dirname(__file__), "..", "bets.json")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Team abbreviation map for ticker construction
TEAM_ABBR = {
    "ARI": ["ARI", "ARZ", "AZ", "Arizona", "Diamondbacks"],
    "ATL": ["ATL", "Atlanta", "Braves"],
    "BAL": ["BAL", "Baltimore", "Orioles"],
    "BOS": ["BOS", "Boston", "Red Sox"],
    "CHC": ["CHC", "Chicago C", "Cubs"],
    "CWS": ["CWS", "Chicago W", "White Sox"],
    "CIN": ["CIN", "Cincinnati", "Reds"],
    "CLE": ["CLE", "Cleveland", "Guardians"],
    "COL": ["COL", "Colorado", "Rockies"],
    "DET": ["DET", "Detroit", "Tigers"],
    "HOU": ["HOU", "Houston", "Astros"],
    "KC":  ["KC", "KAN", "Kansas City", "Royals"],
    "LAA": ["LAA", "Los Angeles A", "Angels"],
    "LAD": ["LAD", "Los Angeles D", "Dodgers"],
    "MIA": ["MIA", "Miami", "Marlins"],
    "MIL": ["MIL", "Milwaukee", "Brewers"],
    "MIN": ["MIN", "Minnesota", "Twins"],
    "NYM": ["NYM", "New York M", "Mets"],
    "NYY": ["NYY", "New York Y", "Yankees"],
    "ATH": ["ATH", "OAK", "Oakland", "Athletics"],
    "PHI": ["PHI", "Philadelphia", "Phillies"],
    "PIT": ["PIT", "Pittsburgh", "Pirates"],
    "SD":  ["SD", "SDP", "San Diego", "Padres"],
    "SF":  ["SF", "SFG", "San Francisco", "Giants"],
    "SEA": ["SEA", "Seattle", "Mariners"],
    "STL": ["STL", "St. Louis", "Cardinals"],
    "TB":  ["TB", "TBR", "Tampa Bay", "Rays"],
    "TEX": ["TEX", "Texas", "Rangers"],
    "TOR": ["TOR", "Toronto", "Blue Jays"],
    "WSH": ["WSH", "WAS", "Washington", "Nationals"],
}

# Reverse map: any alias -> canonical abbreviation
ALIAS_TO_ABBR = {}
for abbr, aliases in TEAM_ABBR.items():
    for a in aliases:
        ALIAS_TO_ABBR[a.upper()] = abbr

# Market type → Kalshi series prefix
MARKET_TO_SERIES = {
    "ML":        "KXMLBGAME",
    "Run Line":  "KXMLBRL",
    "Total":     "KXMLBGT",
    "Team Total": "KXMLBTT",
    "F5 ML":     "KXMLBF5",
    "F5 RL":     "KXMLBF5RL",
    "F5 Total":  "KXMLBF5T",
    "NRFI":      "KXMLBRFI",
    "YRFI":      "KXMLBRFI",
}

MARKET_MAP = {
    "ML": "ML", "MONEYLINE": "ML",
    "F5 ML": "F5 ML", "F5": "F5 ML", "F5ML": "F5 ML",
    "RL": "Run Line", "Run Line": "Run Line", "RUN LINE": "Run Line",
    "F5 RL": "F5 RL",
    "Total": "Total", "TOTAL": "Total", "Game Total": "Total",
    "TT": "Team Total", "Team Total": "Team Total",
    "NRFI": "NRFI",
    "YRFI": "YRFI",
}

# Date → Kalshi date prefix (YYMONDD format)
MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]


def date_to_kalshi_prefix(date_str):
    """Convert 2026-06-06 → 26JUN06"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{str(dt.year)[2:]}{MONTHS[dt.month-1]}{dt.day:02d}"
    except:
        return None


def parse_game(game_str):
    """Parse 'KC @ MIN' → ('KC', 'MIN')"""
    m = re.match(r"(.+?)\s*@\s*(.+)", game_str.strip())
    if not m:
        return None, None
    away_raw = m.group(1).strip().upper()
    home_raw = m.group(2).strip().upper()
    away = ALIAS_TO_ABBR.get(away_raw, away_raw)
    home = ALIAS_TO_ABBR.get(home_raw, home_raw)
    return away, home


def parse_bet_side(bet_str, away, home, market):
    """Determine which team the bet is on from the bet description."""
    if not bet_str:
        return None
    bet_upper = bet_str.upper()
    if away and away in bet_upper:
        return away
    if home and home in bet_upper:
        return home
    # For NRFI/YRFI — no team side
    if market in ("NRFI", "YRFI"):
        return None
    return None


def load_kalshi_registry():
    """Load kalshi_search.json as the primary registry."""
    registry_path = os.path.join(DATA_DIR, "kalshi_search.json")
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            data = json.load(f)
        markets = data.get("markets", data.get("results", []))
        return {m["market_ticker"]: m for m in markets if "market_ticker" in m}
    return {}


def load_kalshi_index():
    """Load kalshi_market_index.json if available."""
    idx_path = os.path.join(DATA_DIR, "kalshi_market_index.json")
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            return json.load(f)
    return {}


def build_candidate_tickers(date_str, away, home, market, bet_side):
    """Build candidate marketTicker values for a bet."""
    kdate = date_to_kalshi_prefix(date_str)
    if not kdate or not away or not home:
        return []

    series = MARKET_TO_SERIES.get(market)
    if not series:
        return []

    # Kalshi time patterns (common first-pitch times in UTC as 4-digit HHMM)
    # We'll try common patterns and let registry validation confirm
    COMMON_TIMES = ["1410", "1415", "1420", "1507", "1605", "1610", "1640",
                    "1705", "1710", "1835", "1905", "1935", "2005", "2010",
                    "2105", "2110", "2205", "2210", "0010", "0035"]

    candidates = []
    for t in COMMON_TIMES:
        event_base = f"{series}-{kdate}{t}{away}{home}"

        if market in ("NRFI", "YRFI"):
            # NRFI/YRFI: market_ticker == event_ticker (no team suffix for YRFI YES)
            candidates.append({
                "marketTicker": event_base,
                "seriesTicker": series,
                "eventTicker": event_base,
                "yesRepresents": "YRFI" if market == "YRFI" else "NRFI",
                "time": t,
            })
        else:
            # For sided markets, ticker ends with team abbreviation
            if bet_side:
                mt = f"{event_base}-{bet_side}"
                candidates.append({
                    "marketTicker": mt,
                    "seriesTicker": series,
                    "eventTicker": event_base,
                    "yesRepresents": bet_side,
                    "time": t,
                })
            else:
                # Try both sides
                for side in [away, home]:
                    mt = f"{event_base}-{side}"
                    candidates.append({
                        "marketTicker": mt,
                        "seriesTicker": series,
                        "eventTicker": event_base,
                        "yesRepresents": side,
                        "time": t,
                    })

    return candidates


def kget(url):
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read()), None
    except HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)


def validate_ticker_via_api(ticker):
    """Check if a ticker exists on Kalshi API."""
    data, err = kget(f"{KALSHI_BASE}/markets/{ticker}")
    if data and "market" in data:
        m = data["market"]
        return True, m.get("status"), m.get("close_time"), m.get("last_price")
    return False, None, None, None


def backfill_bet(b, registry, index, use_api=False):
    """Attempt to backfill Kalshi identity for a single bet."""
    # Skip if already has ticker
    if b.get("marketTicker"):
        return b, "ALREADY_PRESENT"

    market_raw = (b.get("market") or "").strip()
    market = MARKET_MAP.get(market_raw, market_raw)
    date_str = b.get("date", "")
    game = b.get("game", "")
    bet_side_raw = b.get("bet") or b.get("betSide") or ""

    away, home = parse_game(game)
    if not away or not home:
        return b, "UNMATCHABLE"

    bet_side = parse_bet_side(bet_side_raw, away, home, market)
    candidates = build_candidate_tickers(date_str, away, home, market, bet_side)

    # Check registry first
    matched = []
    for c in candidates:
        mt = c["marketTicker"]
        if mt in registry:
            matched.append((c, registry[mt]))

    if len(matched) == 1:
        c, reg_data = matched[0]
        updated = dict(b)
        updated["marketTicker"] = c["marketTicker"]
        updated["seriesTicker"] = c["seriesTicker"]
        updated["eventTicker"] = c.get("eventTicker")
        updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
        updated["backfillSource"] = "kalshi_registry"
        return updated, "SUCCESSFULLY_MATCHED"

    if len(matched) > 1:
        # Ambiguous — try to narrow by bet_side
        sided = [m for m in matched if bet_side and bet_side in m[0]["marketTicker"]]
        if len(sided) == 1:
            c, reg_data = sided[0]
            updated = dict(b)
            updated["marketTicker"] = c["marketTicker"]
            updated["seriesTicker"] = c["seriesTicker"]
            updated["eventTicker"] = c.get("eventTicker")
            updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
            updated["backfillSource"] = "kalshi_registry_narrowed"
            return updated, "SUCCESSFULLY_MATCHED"

        # Store event_ticker as partial match
        event_tickers = list(set(c["eventTicker"] for c, _ in matched))
        if len(event_tickers) == 1:
            updated = dict(b)
            updated["eventTicker"] = event_tickers[0]
            updated["backfillStatus"] = "PARTIALLY_MATCHED"
            updated["backfillSource"] = "kalshi_registry_partial"
            return updated, "PARTIALLY_MATCHED"

        return b, "UNMATCHABLE"

    # Registry miss — check index
    # Look for pattern match in index keys
    kdate = date_to_kalshi_prefix(date_str)
    if kdate and away and home and index:
        series = MARKET_TO_SERIES.get(market, "")
        pattern = f"{series}-{kdate}"
        idx_matches = [k for k in index.keys() if pattern in k and away in k and home in k]
        if len(idx_matches) == 1:
            mt = idx_matches[0]
            updated = dict(b)
            updated["marketTicker"] = mt
            updated["seriesTicker"] = series
            updated["eventTicker"] = index[mt].get("event_ticker", mt)
            updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
            updated["backfillSource"] = "kalshi_index"
            return updated, "SUCCESSFULLY_MATCHED"

    return b, "UNMATCHABLE"


def run_backfill(bets_path=None, use_api=False, write=False):
    path = bets_path or BETS_PATH
    with open(path) as f:
        bets = json.load(f)

    registry = load_kalshi_registry()
    index = load_kalshi_index()
    print(f"Registry: {len(registry)} markets | Index: {len(index)} entries")

    results = []
    tally = {"SUCCESSFULLY_MATCHED": 0, "PARTIALLY_MATCHED": 0,
             "UNMATCHABLE": 0, "ALREADY_PRESENT": 0}

    for b in bets:
        updated_b, status = backfill_bet(b, registry, index, use_api)
        tally[status] = tally.get(status, 0) + 1
        results.append((updated_b, status))

    summary = {
        "total": len(bets),
        "already_present": tally["ALREADY_PRESENT"],
        "successfully_matched": tally["SUCCESSFULLY_MATCHED"],
        "partially_matched": tally["PARTIALLY_MATCHED"],
        "unmatchable": tally["UNMATCHABLE"],
        "coverage_pct": round(tally["SUCCESSFULLY_MATCHED"] / len(bets) * 100, 1),
    }

    print("\nBACKFILL RESULTS")
    print("=" * 40)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    if write:
        updated_bets = [b for b, _ in results]
        with open(path, "w") as f:
            json.dump(updated_bets, f, indent=2)
        print(f"\nWrote {len(updated_bets)} bets to {path}")

    # Write backfill report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "bets": [
            {"id": b.get("id"), "status": s,
             "marketTicker": b.get("marketTicker"),
             "backfillSource": b.get("backfillSource")}
            for b, s in results
        ],
    }
    report_path = os.path.join(DATA_DIR, "backfill_report.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to {report_path}")

    return [b for b, _ in results], summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill Kalshi market identities")
    parser.add_argument("--write", action="store_true", help="Write updates to bets.json")
    parser.add_argument("--api", action="store_true", help="Validate via Kalshi API")
    args = parser.parse_args()
    run_backfill(use_api=args.api, write=args.write)
