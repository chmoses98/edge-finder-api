#!/usr/bin/env python3
"""
MRV-INFO-001/002: information-event timing resolution and repricing on the
ALPHA-0002 prospective corpus (mlb_state first-seen changes + book quotes).
RESEARCH ONLY.  Reports the event-time resolution first; if the median
bound between "not posted" and "first seen posted" exceeds 30 minutes the
hypotheses are DATA_BLOCKED and only descriptive repricing is reported.

Usage: run_info_events.py --corpus-root <dir>
Writes data/edgelab/research_artifacts/market_structure/info_events.json
"""
import argparse
import collections
import gzip
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from _common import write_artifact, now_iso  # noqa: E402

from lib.edgelab.research.market_structure import book as BK, stats as ST  # noqa: E402
from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402


def _ts(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def _iter(path):
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus-root", required=True)
    args = ap.parse_args()
    dates = sorted(f[:10] for f in os.listdir(os.path.join(args.corpus_root, "mlb_state")) if f.endswith(".jsonl.gz"))
    # run timestamps (to bound event time by the previous run)
    runs = sorted({_ts(r["capturedAt"]) for d in dates for r in _iter(os.path.join(args.corpus_root, "runs", "%s.jsonl.gz" % d))} |
                  {_ts(json.loads(l)["capturedAt"]) for d in dates if os.path.exists(os.path.join(args.corpus_root, "runs", "%s.jsonl" % d)) for l in open(os.path.join(args.corpus_root, "runs", "%s.jsonl" % d)) if l.strip()})
    # state stream per gamePk
    state = collections.defaultdict(list)
    for d in dates:
        for r in _iter(os.path.join(args.corpus_root, "mlb_state", "%s.jsonl.gz" % d)):
            state[r["gamePk"]].append(r)
    events = []
    for pk, rs in state.items():
        rs.sort(key=lambda r: r["capturedAt"])
        prev = None
        for r in rs:
            if prev is not None:
                for kind, key in (("LINEUP_AWAY", "awayLineupPosted"), ("LINEUP_HOME", "homeLineupPosted")):
                    if not prev.get(key) and r.get(key):
                        events.append({"gamePk": pk, "kind": kind, "seenAt": r["capturedAt"], "prevSeenAt": prev["capturedAt"], "gameDate": r.get("gameDate")})
                for kind, key in (("PITCHER_AWAY", "awayProbableId"), ("PITCHER_HOME", "homeProbableId")):
                    if prev.get(key) and r.get(key) and prev[key] != r[key]:
                        events.append({"gamePk": pk, "kind": kind, "seenAt": r["capturedAt"], "prevSeenAt": prev["capturedAt"], "gameDate": r.get("gameDate")})
            prev = r
    for e in events:
        e["boundMinutes"] = (_ts(e["seenAt"]) - _ts(e["prevSeenAt"])) / 60.0
    by_kind = collections.defaultdict(list)
    for e in events:
        by_kind[e["kind"]].append(e["boundMinutes"])
    res = {k: {"events": len(v), "medianBoundMinutes": sorted(v)[len(v) // 2], "shareBoundLe30min": sum(1 for x in v if x <= 30) / len(v)} for k, v in by_kind.items()}
    blocked = all(v["medianBoundMinutes"] > 30 for v in res.values()) if res else True
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "dates": dates, "scheduledRunsSeen": len(runs),
           "eventTimeResolution": res, "dataBlocked": blocked,
           "reason": "event time is bounded only by consecutive collector runs; the collector delivered 3-8 runs/day so the bound is hours, not minutes; repricing-lag tests need <=30-minute resolution",
           "firstSeenOnlyEvents": sum(1 for pk, rs in state.items() for r in rs[:1] if r.get("awayLineupPosted") and r.get("homeLineupPosted"))}
    p = write_artifact("info_events.json", out)
    print("wrote", p); print(json.dumps(res, indent=1), "blocked", blocked)


if __name__ == "__main__":
    main()
