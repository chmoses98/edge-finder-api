#!/usr/bin/env python3
"""
scripts/research/hitter_v2/extract_pregame_lineups.py
=====================================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Extracts the point-in-time
(pregame) confirmed lineups and probable starters from the committed slate
captures (data/slates/<date>/*.json) into a small sports-only table:

    data/research/hitter_v2/pregame_lineups_2026.csv.gz

Selection is IDENTICAL to the champion rerun's (scripts/research/hitter_w2/
incumbent_rerun.py::pregame_confirmed_sides): for each (game, side), the
latest capture strictly before scheduled first pitch in which that side's
`lineupConfirmedOfficial` is true with 9 batters.

Only these keys of a slate game record are read: gameId, startTime,
<side>TeamStats.lineupConfirmedOfficial, <side>TeamStats.confirmedLineup[*].
{order, playerId, position}, <side>.pitcher.id. The odds / Kalshi /
modelProb / teamTotals / totalEval blocks that sit in the same records are
never read (tests/research/test_hitter_v2_engine.py checks the output columns).
"""
import argparse
import csv
import gzip
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_W2 = os.path.join(_ROOT, "scripts", "research", "hitter_w2")
if _W2 not in sys.path:
    sys.path.insert(0, _W2)

import incumbent_rerun as IR  # noqa: E402  (same capture-selection function as the champion rerun)

OUT_DEFAULT = os.path.join(_ROOT, "data", "research", "hitter_v2", "pregame_lineups_2026.csv.gz")
COLUMNS = ("gamePk", "side", "captureUTC", "captureFile", "startUTC", "slot", "playerId", "position",
           "ownProbableId", "oppProbableId")


def extract_game_side(gid, side, ct, fname, g):
    """Sports-only rows for one captured (game, side)."""
    ts = g.get(side + "TeamStats") or {}
    opp = "home" if side == "away" else "away"
    own_p = ((g.get(side) or {}).get("pitcher") or {}).get("id")
    opp_p = ((g.get(opp) or {}).get("pitcher") or {}).get("id")
    rows = []
    for h in ts.get("confirmedLineup") or []:
        rows.append({
            "gamePk": str(gid), "side": "A" if side == "away" else "H",
            "captureUTC": ct.strftime("%Y-%m-%dT%H:%M:%SZ"), "captureFile": fname,
            "startUTC": g.get("startTime"), "slot": h.get("order"), "playerId": h.get("playerId"),
            "position": h.get("position") or "", "ownProbableId": own_p or "", "oppProbableId": opp_p or "",
        })
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-03-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args(argv)
    dates = sorted(d for d in os.listdir(IR.SLATES) if a.start <= d <= a.end)
    n_sides = 0
    with gzip.open(a.out, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for d in dates:
            sides = IR.pregame_confirmed_sides(d)
            for (gid, side), (ct, fname, g) in sorted(sides.items()):
                for r in extract_game_side(gid, side, ct, fname, g):
                    w.writerow(r)
                n_sides += 1
            print(f"{d}: {len(sides)} captured sides", flush=True)
    print(f"total captured team-games: {n_sides}")


if __name__ == "__main__":
    main()
