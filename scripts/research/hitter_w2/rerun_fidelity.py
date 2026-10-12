#!/usr/bin/env python3
"""SIFT W2 hitter lane -- RESEARCH ONLY. Fidelity of the incumbent rerun vs the archived
production hitter-projection snapshots on the same player-game-thresholds (the snapshot's
last PROJECTED row per ticker). Usage: rerun_fidelity.py <rerun.jsonl.gz> <out.json>"""
import glob, gzip, json, os, sys
from collections import defaultdict
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FAM = {"hitter_hits": ["H"], "hitter_total_bases": ["TB"], "hitter_rbis": ["RBI"], "hitter_hits_runs_rbis": None}
inc = {}
for line in gzip.open(sys.argv[1], "rt"):
    r = json.loads(line)
    if r.get("status") == "PROJECTED":
        inc[(str(r["gameId"]), str(r["playerId"]))] = r
last = {}
for f in sorted(glob.glob(os.path.join(ROOT, "data/edgelab/hitter_projection_snapshots/*.jsonl"))):
    for line in open(f):
        r = json.loads(line)
        if r.get("projectionStatus") != "PROJECTED" or r.get("marketFamily") not in FAM:
            continue
        k = r["marketTicker"]
        if k not in last or r["snapshotGeneratedAt"] > last[k]["snapshotGeneratedAt"]:
            last[k] = r
agg = defaultdict(lambda: [0, 0.0, 0.0])
for r in last.values():
    ir = inc.get((str(r.get("gameId")), str(r.get("playerId"))))
    if not ir or FAM[r["marketFamily"]] is None:
        continue
    st = FAM[r["marketFamily"]][0]
    t = int(r["threshold"])
    p = sum(v for k, v in ir[st]["pmf"].items() if int(k) >= t)
    a = agg[r["marketFamily"]]
    a[0] += 1; a[1] += p - r["modelProbability"]; a[2] += abs(p - r["modelProbability"])
out = {k: {"n_tickers": v[0], "mean_rerun_minus_snapshot": round(v[1] / v[0], 4),
           "mean_abs_diff": round(v[2] / v[0], 4)} for k, v in agg.items() if v[0]}
json.dump(out, open(sys.argv[2], "w"), indent=1, sort_keys=True)
print(json.dumps(out))
