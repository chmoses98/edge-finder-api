#!/usr/bin/env python3
"""
validate_slate.py — Pre-analysis validation gate
Reads data/slate.json and fails with a clear error if any game
is missing required markets, projections, starters, or Kalshi prices.

Exit 0 = OK. Exit 1 = fail with details.
"""

import json, sys, os

REQUIRED_GAME_FIELDS = [
    "awayStarter", "homeStarter",
    "awayProjRuns", "homeProjRuns",
    "pinnacleVF",
]

REQUIRED_KALSHI_FIELDS = [
    "kalshiML",        # dict with away/home
    "kalshiF5ML",      # dict with away/home
    "kalshiTT",        # dict with away/home nested
    "kalshiNRFI",      # scalar
    "kalshiTotalLine", # scalar
]

REJECTION_REASON_REQUIRED = True  # every missing market must have a rejectionReason


def load_slate():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "slate.json")
    if not os.path.exists(path):
        print("FAIL: data/slate.json not found", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def validate(slate):
    errors = []
    games = slate.get("games", [])
    if not games:
        errors.append("slate.json has no 'games' array")
        return errors

    for g in games:
        gid = g.get("game", g.get("id", "UNKNOWN"))
        
        # Required fields
        for field in REQUIRED_GAME_FIELDS:
            if g.get(field) is None:
                errors.append(f"{gid}: missing required field '{field}'")

        # Kalshi market prices
        for field in REQUIRED_KALSHI_FIELDS:
            if g.get(field) is None:
                errors.append(f"{gid}: Kalshi market '{field}' not populated — game cannot be analyzed")

        # Run projections must be positive
        away_proj = g.get("awayProjRuns")
        home_proj = g.get("homeProjRuns")
        if away_proj is not None and (away_proj < 1.0 or away_proj > 10.0):
            errors.append(f"{gid}: awayProjRuns={away_proj} out of valid range [1.0, 10.0]")
        if home_proj is not None and (home_proj < 1.0 or home_proj > 10.0):
            errors.append(f"{gid}: homeProjRuns={home_proj} out of valid range [1.0, 10.0]")

        # Market rows — every allEdges entry must have rejectionReason if no edge
        all_edges = g.get("allEdges", [])
        for row in all_edges:
            market = row.get("market", "UNKNOWN_MARKET")
            edge = row.get("edge")
            conf = row.get("confidence")
            if not edge and not conf and not row.get("rejectionReason"):
                errors.append(
                    f"{gid} / {market}: no edge logged AND no rejectionReason — "
                    f"silence is not a rejection (Rule 67)"
                )

        # lineupConfirmed flag must exist
        for side in ["awayTeamStats", "homeTeamStats"]:
            team = g.get(side, {})
            if "lineupConfirmed" not in team:
                errors.append(f"{gid} / {side}: missing 'lineupConfirmed' flag")

    return errors


def main():
    slate = load_slate()
    errors = validate(slate)
    
    if errors:
        print(f"\nVALIDATION FAILED — {len(errors)} error(s):\n", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print("\nFix these before running analysis.\n", file=sys.stderr)
        sys.exit(1)
    else:
        games = slate.get("games", [])
        print(f"VALIDATION PASSED — {len(games)} games, all required fields present.")
        sys.exit(0)


if __name__ == "__main__":
    main()
