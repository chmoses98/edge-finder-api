#!/usr/bin/env python3
"""
PHASE 5 — BACKFILL EXISTING BETS (v2)
Attempts to match existing bets to Kalshi market tickers using
dated registry snapshots from data/kalshi_registry_snapshots/.

Priority order per bet:
  1. Load snapshot for the bet's date (kalshi_search_YYYY-MM-DD.json)
  2. If snapshot missing → classify UNMATCHABLE_NO_SNAPSHOT (no guessing)
  3. If snapshot present but no match → classify UNMATCHABLE
  4. If snapshot present and exact match → SUCCESSFULLY_MATCHED
  5. If snapshot present, event found but market ambiguous → PARTIALLY_MATCHED

Backfill status values:
  SUCCESSFULLY_MATCHED      — exact non-ambiguous ticker assigned
  PARTIALLY_MATCHED         — event_ticker found, market_ticker ambiguous
  UNMATCHABLE               — snapshot present but no ticker match found
  UNMATCHABLE_NO_SNAPSHOT   — no dated snapshot exists for this bet's date
  ALREADY_PRESENT           — bet already has marketTicker stored
"""
import json, os, re, sys
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError

BETS_PATH = os.path.join(os.path.dirname(__file__), "..", "bets.json")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "kalshi_registry_snapshots")
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Team abbreviation map
TEAM_ABBR = {
    "ARI": ["ARI", "ARZ", "AZ", "ARIZONA", "DIAMONDBACKS"],
    "ATL": ["ATL", "ATLANTA", "BRAVES"],
    "BAL": ["BAL", "BALTIMORE", "ORIOLES"],
    "BOS": ["BOS", "BOSTON", "RED SOX"],
    "CHC": ["CHC", "CHICAGO C", "CUBS"],
    "CWS": ["CWS", "CHICAGO W", "WHITE SOX"],
    "CIN": ["CIN", "CINCINNATI", "REDS"],
    "CLE": ["CLE", "CLEVELAND", "GUARDIANS"],
    "COL": ["COL", "COLORADO", "ROCKIES"],
    "DET": ["DET", "DETROIT", "TIGERS"],
    "HOU": ["HOU", "HOUSTON", "ASTROS"],
    "KC":  ["KC", "KAN", "KANSAS CITY", "ROYALS"],
    "LAA": ["LAA", "LOS ANGELES A", "ANGELS"],
    "LAD": ["LAD", "LOS ANGELES D", "DODGERS"],
    "MIA": ["MIA", "MIAMI", "MARLINS"],
    "MIL": ["MIL", "MILWAUKEE", "BREWERS"],
    "MIN": ["MIN", "MINNESOTA", "TWINS"],
    "NYM": ["NYM", "NEW YORK M", "METS"],
    "NYY": ["NYY", "NEW YORK Y", "YANKEES"],
    "ATH": ["ATH", "OAK", "OAKLAND", "ATHLETICS"],
    "PHI": ["PHI", "PHILADELPHIA", "PHILLIES"],
    "PIT": ["PIT", "PITTSBURGH", "PIRATES"],
    "SD":  ["SD", "SDP", "SAN DIEGO", "PADRES"],
    "SF":  ["SF", "SFG", "SAN FRANCISCO", "GIANTS"],
    "SEA": ["SEA", "SEATTLE", "MARINERS"],
    "STL": ["STL", "ST. LOUIS", "CARDINALS"],
    "TB":  ["TB", "TBR", "TAMPA BAY", "RAYS"],
    "TEX": ["TEX", "TEXAS", "RANGERS"],
    "TOR": ["TOR", "TORONTO", "BLUE JAYS"],
    "WSH": ["WSH", "WAS", "WASHINGTON", "NATIONALS"],
}

ALIAS_TO_ABBR = {}
for abbr, aliases in TEAM_ABBR.items():
    for a in aliases:
        ALIAS_TO_ABBR[a.upper()] = abbr

MARKET_MAP = {
    "ML": "ML", "MONEYLINE": "ML",
    "F5 ML": "F5 ML", "F5": "F5 ML", "F5ML": "F5 ML",
    "RL": "Run Line", "Run Line": "Run Line", "RUN LINE": "Run Line", "RUNLINE": "Run Line",
    "F5 RL": "F5 RL",
    "Total": "Total", "TOTAL": "Total", "Game Total": "Total", "GAME TOTAL": "Total",
    "TT": "Team Total", "Team Total": "Team Total", "TEAM TOTAL": "Team Total",
    "NRFI": "NRFI",
    "YRFI": "YRFI",
    "K Prop": "K Prop", "K PROP": "K Prop",
    "Pitcher Prop": "Pitcher Prop",
}

MARKET_TO_SERIES = {
    "ML":         "KXMLBGAME",
    "Run Line":   "KXMLBSPREAD",
    "Total":      "KXMLBTOTAL",
    "Team Total": "KXMLBTEAMTOTAL",
    "F5 ML":      "KXMLBF5",
    "F5 RL":      "KXMLBF5SPREAD",
    "F5 Total":   "KXMLBF5TOTAL",
    "NRFI":       "KXMLBRFI",
    "YRFI":       "KXMLBRFI",
}

MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]


# ── Snapshot loading ──────────────────────────────────────────────────────────

_snapshot_cache = {}  # date_str -> registry dict (or None if missing)


def snapshot_path_for_date(date_str, snapshots_dir=None):
    """Return the expected snapshot file path for a given date."""
    sd = snapshots_dir or SNAPSHOTS_DIR
    return os.path.join(sd, f"kalshi_search_{date_str}.json")


def load_snapshot_for_date(date_str, snapshots_dir=None):
    """
    Load the Kalshi registry snapshot for a specific date.
    Returns dict of {market_ticker: market_record} or None if snapshot missing.
    Uses an in-process cache to avoid re-reading the same file.
    """
    global _snapshot_cache
    if date_str in _snapshot_cache:
        return _snapshot_cache[date_str]

    path = snapshot_path_for_date(date_str, snapshots_dir)
    if not os.path.exists(path):
        _snapshot_cache[date_str] = None
        return None

    with open(path) as f:
        data = json.load(f)

    markets = data.get("markets", data.get("results", []))
    registry = {m["market_ticker"]: m for m in markets if "market_ticker" in m}
    _snapshot_cache[date_str] = registry
    return registry


def list_available_snapshots(snapshots_dir=None):
    """Return sorted list of dates for which snapshots exist."""
    sd = snapshots_dir or SNAPSHOTS_DIR
    if not os.path.isdir(sd):
        return []
    dates = []
    for fname in os.listdir(sd):
        m = re.match(r"kalshi_search_(\d{4}-\d{2}-\d{2})\.json$", fname)
        if m:
            dates.append(m.group(1))
    return sorted(dates)


# ── Parsing helpers ───────────────────────────────────────────────────────────

def parse_game(game_str):
    """Parse 'KC @ MIN' → ('KC', 'MIN') using canonical abbreviations."""
    m = re.match(r"(.+?)\s*@\s*(.+)", game_str.strip())
    if not m:
        return None, None
    away = ALIAS_TO_ABBR.get(m.group(1).strip().upper(), m.group(1).strip().upper())
    home = ALIAS_TO_ABBR.get(m.group(2).strip().upper(), m.group(2).strip().upper())
    return away, home


def date_to_kalshi_prefix(date_str):
    """Convert '2026-06-06' → '26JUN06'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{str(dt.year)[2:]}{MONTHS[dt.month-1]}{dt.day:02d}"
    except:
        return None


def parse_bet_side(bet_str, away, home, market):
    """Extract which team the bet is on from the bet description string."""
    if not bet_str:
        return None
    bu = bet_str.upper()
    if market in ("NRFI", "YRFI"):
        return None
    if away and away.upper() in bu:
        return away
    if home and home.upper() in bu:
        return home
    return None


# ── Matching logic ────────────────────────────────────────────────────────────

def find_match_in_registry(registry, away, home, market, bet_side, date_str):
    """
    Search a registry dict for a matching market ticker.

    Returns list of (candidate_ticker, record) tuples — could be 0, 1, or many.
    Caller decides how to handle multiple matches.
    """
    if not registry:
        return []

    kdate = date_to_kalshi_prefix(date_str)
    if not kdate:
        return []

    series = MARKET_TO_SERIES.get(market)
    if not series:
        return []

    away_u = away.upper() if away else ""
    home_u = home.upper() if home else ""

    matches = []
    for ticker, rec in registry.items():
        t = ticker.upper()
        et = (rec.get("event_ticker") or "").upper()
        mt = rec.get("market_type", "")

        # Must contain the date prefix
        if kdate.upper() not in t:
            continue

        # Must contain both team abbreviations (in event ticker or market ticker)
        ref = et or t
        if away_u not in ref or home_u not in ref:
            continue

        # Must match the series prefix
        if not t.startswith(series.upper()):
            continue

        # Side filter: if bet_side known, only keep tickers ending in that team
        # (for NRFI/YRFI there's no team suffix — accept all)
        if market not in ("NRFI", "YRFI") and bet_side:
            if not t.endswith(f"-{bet_side.upper()}"):
                continue

        matches.append((ticker, rec))

    return matches


def backfill_bet(b, registry=None, index=None, snapshots_dir=None):
    """
    Attempt to backfill Kalshi identity for a single bet.

    Registry parameter is IGNORED — this version always loads the date-specific
    snapshot. Pass registry=None. The index param is kept for API compat.

    Returns (updated_bet, status_string).
    """
    # Already has ticker — skip
    if b.get("marketTicker"):
        return b, "ALREADY_PRESENT"

    date_str = b.get("date", "")
    game = b.get("game", "")
    market_raw = (b.get("market") or "").strip()
    market = MARKET_MAP.get(market_raw, market_raw)
    bet_str = b.get("bet") or b.get("betSide") or ""

    if not game or not market_raw or not date_str:
        return b, "UNMATCHABLE"

    away, home = parse_game(game)
    if not away or not home:
        return b, "UNMATCHABLE"

    bet_side = parse_bet_side(bet_str, away, home, market)

    # Load dated snapshot — do NOT fall back to kalshi_search.json
    snap = load_snapshot_for_date(date_str, snapshots_dir)
    if snap is None:
        updated = dict(b)
        updated["backfillStatus"] = "UNMATCHABLE_NO_SNAPSHOT"
        updated["backfillNote"] = (
            f"No snapshot at data/kalshi_registry_snapshots/kalshi_search_{date_str}.json"
        )
        return updated, "UNMATCHABLE_NO_SNAPSHOT"

    matches = find_match_in_registry(snap, away, home, market, bet_side, date_str)

    if len(matches) == 0:
        updated = dict(b)
        updated["backfillStatus"] = "UNMATCHABLE"
        updated["backfillNote"] = (
            f"Snapshot exists for {date_str} but no ticker matched "
            f"{away}@{home} {market} {bet_side or ''}"
        )
        return updated, "UNMATCHABLE"

    if len(matches) == 1:
        ticker, rec = matches[0]
        updated = dict(b)
        series = MARKET_TO_SERIES.get(market, "")
        updated["marketTicker"] = ticker
        updated["ticker"]       = ticker
        updated["seriesTicker"] = series
        updated["eventTicker"]  = rec.get("event_ticker")
        # scheduledStartTime: try to derive from event_ticker if not already set
        if not updated.get("scheduledStartTime"):
            et = rec.get("event_ticker", "")
            # e.g. KXMLBGAME-26JUN061410KCMIN → parse HHMM from the date+time prefix
            import re as _re
            m = _re.search(r"-\d{2}[A-Z]{3}\d{2}(\d{4})[A-Z]", et)
            if m:
                hhmm = m.group(1)
                hh, mm = int(hhmm[:2]), int(hhmm[2:])
                # Convert ET to UTC (+4 hours)
                hh_utc = (hh + 4) % 24
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    dt = _dt.strptime(date_str, "%Y-%m-%d")
                    ts = dt.replace(hour=hh_utc, minute=mm, tzinfo=_tz.utc)
                    updated["scheduledStartTime"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    pass
        updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
        updated["backfillSource"] = f"snapshot_{date_str}"
        return updated, "SUCCESSFULLY_MATCHED"

    # Multiple matches — try narrowing by bet_side
    if bet_side:
        sided = [(t, r) for t, r in matches if t.upper().endswith(f"-{bet_side.upper()}")]
        if len(sided) == 1:
            ticker, rec = sided[0]
            updated = dict(b)
            series = MARKET_TO_SERIES.get(market, "")
            updated["marketTicker"] = ticker
            updated["ticker"]       = ticker
            updated["seriesTicker"] = series
            updated["eventTicker"]  = rec.get("event_ticker")
            if not updated.get("scheduledStartTime"):
                et = rec.get("event_ticker", "")
                import re as _re
                m = _re.search(r"-\d{2}[A-Z]{3}\d{2}(\d{4})[A-Z]", et)
                if m:
                    hhmm = m.group(1)
                    hh, mm = int(hhmm[:2]), int(hhmm[2:])
                    hh_utc = (hh + 4) % 24
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        dt = _dt.strptime(date_str, "%Y-%m-%d")
                        ts = dt.replace(hour=hh_utc, minute=mm, tzinfo=_tz.utc)
                        updated["scheduledStartTime"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except Exception:
                        pass
            updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
            updated["backfillSource"] = f"snapshot_{date_str}_narrowed"
            return updated, "SUCCESSFULLY_MATCHED"

    # Still ambiguous — store event_ticker only if all matches share one
    event_tickers = list(set(r.get("event_ticker", "") for _, r in matches))
    if len(event_tickers) == 1 and event_tickers[0]:
        updated = dict(b)
        updated["eventTicker"] = event_tickers[0]
        updated["seriesTicker"] = MARKET_TO_SERIES.get(market, "")
        bet_line = b.get("line")
        if bet_line is not None:
            # Try to narrow by line number in ticker suffix
            line_sided = [(t, r) for t, r in matches if str(int(float(bet_line))) in t.split("-")[-1]]
            if len(line_sided) == 1:
                ticker_m, rec_m = line_sided[0]
                updated["marketTicker"] = ticker_m
                updated["ticker"]       = ticker_m
                updated["backfillStatus"] = "SUCCESSFULLY_MATCHED"
                updated["backfillSource"] = f"snapshot_{date_str}_line_narrowed"
                return updated, "SUCCESSFULLY_MATCHED"
        updated["backfillStatus"] = "PARTIALLY_MATCHED"
        updated["backfillNote"] = (
            f"{len(matches)} market tickers share event {event_tickers[0]} — "
            f"need line number to select one (bet line: {b.get('line', 'not stored')})"
        )
        return updated, "PARTIALLY_MATCHED"

    updated = dict(b)
    updated["backfillStatus"] = "UNMATCHABLE"
    updated["backfillNote"] = f"Ambiguous: {len(matches)} matches, {len(event_tickers)} distinct events"
    return updated, "UNMATCHABLE"


# ── Batch runner ──────────────────────────────────────────────────────────────

def run_backfill(bets_path=None, snapshots_dir=None, write=False):
    path = bets_path or BETS_PATH
    sd = snapshots_dir or SNAPSHOTS_DIR

    with open(path) as f:
        bets = json.load(f)

    available_snapshots = list_available_snapshots(sd)
    print(f"Available snapshots: {len(available_snapshots)}")
    for s in available_snapshots:
        print(f"  {s}")

    tally = {
        "ALREADY_PRESENT": 0,
        "SUCCESSFULLY_MATCHED": 0,
        "PARTIALLY_MATCHED": 0,
        "UNMATCHABLE": 0,
        "UNMATCHABLE_NO_SNAPSHOT": 0,
    }
    results = []

    for b in bets:
        updated_b, status = backfill_bet(b, snapshots_dir=sd)
        tally[status] = tally.get(status, 0) + 1
        results.append((updated_b, status))

    total = len(bets)
    summary = {
        "total": total,
        "available_snapshots": available_snapshots,
        "already_present": tally["ALREADY_PRESENT"],
        "successfully_matched": tally["SUCCESSFULLY_MATCHED"],
        "partially_matched": tally["PARTIALLY_MATCHED"],
        "unmatchable": tally["UNMATCHABLE"],
        "unmatchable_no_snapshot": tally["UNMATCHABLE_NO_SNAPSHOT"],
        "coverage_pct": round(tally["SUCCESSFULLY_MATCHED"] / total * 100, 1) if total else 0,
    }

    print("\nBACKFILL RESULTS")
    print("=" * 40)
    for k, v in summary.items():
        if k == "available_snapshots":
            continue
        print(f"  {k}: {v}")

    if write:
        updated_bets = [b for b, _ in results]
        with open(path, "w") as f:
            json.dump(updated_bets, f, indent=2)
        print(f"\nWrote {len(updated_bets)} bets to {path}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "bets": [
            {
                "id": b.get("id"),
                "date": b.get("date"),
                "game": b.get("game"),
                "market": b.get("market"),
                "status": s,
                "marketTicker": b.get("marketTicker"),
                "backfillSource": b.get("backfillSource"),
                "backfillNote": b.get("backfillNote"),
            }
            for b, s in results
        ],
    }
    report_path = os.path.join(DATA_DIR, "backfill_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report written to {report_path}")

    return [b for b, _ in results], summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill Kalshi market identities from dated snapshots")
    parser.add_argument("--write", action="store_true", help="Write updates to bets.json")
    parser.add_argument("--snapshots-dir", default=None, help="Path to snapshots directory")
    args = parser.parse_args()
    run_backfill(write=args.write, snapshots_dir=args.snapshots_dir)
