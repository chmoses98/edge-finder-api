#!/usr/bin/env python3
"""
scripts/research/hitter_w2/build_rerun_market_inputs.py
=======================================================
SIFT W2 hitter lane -- RESEARCH ONLY. Builds the MARKET inputs used ONLY by the
runtime non-leakage proof (pure_gate rerun) for MLB_HITTER_W2_PURE_V1:

  totals.json         {gamePk: sportsbook full-game total line} read from the
                      committed slate captures (data/slates/<date>/*.json, odds.*.total.line)
  hitter_quotes.jsonl the committed Kalshi hitter-projection snapshot rows for the
                      same dates (data/edgelab/hitter_projection_snapshots/<date>.jsonl)

The PURE model never opens either file. The negative control
(`run_hitter_w2.py forecast --control-read-market`) reads totals.json.
"""
import glob
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main(start, end, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    totals = {}
    for d in sorted(os.listdir(os.path.join(_ROOT, "data", "slates"))):
        if not (start <= d <= end):
            continue
        for f in sorted(glob.glob(os.path.join(_ROOT, "data", "slates", d, "*.json"))):
            with open(f) as fh:
                doc = json.load(fh)
            for g in doc.get("games") or []:
                for book in (g.get("odds") or {}).values():
                    line = ((book or {}).get("total") or {}).get("line") if isinstance(book, dict) else None
                    if line is not None and g.get("gameId") is not None:
                        totals[str(g["gameId"])] = float(line)
    with open(os.path.join(out_dir, "totals.json"), "w") as f:
        json.dump(totals, f, sort_keys=True, indent=0)
    n = 0
    with open(os.path.join(out_dir, "hitter_quotes.jsonl"), "w") as out:
        for f in sorted(glob.glob(os.path.join(_ROOT, "data", "edgelab", "hitter_projection_snapshots", "*.jsonl"))):
            if start <= os.path.basename(f)[:10] <= end:
                with open(f) as fh:
                    for line in fh:
                        out.write(line)
                        n += 1
    print(f"totals for {len(totals)} games; {n} hitter quote rows")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
