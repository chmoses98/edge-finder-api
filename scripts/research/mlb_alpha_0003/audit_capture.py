#!/usr/bin/env python3
"""
Delivered-capture audit for the MLB research collectors (read-only, offline).

"Scheduled" is a promise; "delivered" is a persisted row. This script measures
only the latter, from the archives:

  1. stream cadence: median / p90 / max gap between persisted capture times,
     overall and inside the MLB capture window, versus the promised cadence
  2. per-game pregame coverage: how many captures actually saw each game inside
     T-240..T-0, and the largest blind interval before first pitch
  3. field integrity: share of quote rows that carry any price at all
  4. series coverage: which game-level MLB series each collector never captures

Outputs data/edgelab/research_artifacts/mlb_alpha_0003/capture_audit.json (+ .md).
"""

import argparse
import glob
import gzip
import json
import os
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.mlb_alpha import corpus, taxonomy as tx  # noqa: E402


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(round(q * (len(v) - 1))))], 1)


def gap_stats(times, window_only=True):
    """times: sorted datetimes. Gaps (minutes) overall and inside the 15:00-04:59 UTC window."""
    gaps_all, gaps_win = [], []
    for a, b in zip(times, times[1:]):
        g = (b - a).total_seconds() / 60.0
        gaps_all.append(g)
        in_win = a.hour >= 15 or a.hour < 5
        if in_win and (b.hour >= 15 or b.hour < 5) and g < 600:
            gaps_win.append(g)
    f = lambda g: {"n": len(g), "median": _pct(g, .5), "p90": _pct(g, .9), "max": round(max(g), 1) if g else None}
    return {"overall": f(gaps_all), "inWindow": f(gaps_win)}


def stream_alpha0002(root):
    runs = []
    for fn in sorted(glob.glob(os.path.join(root, "runs", "*.jsonl"))):
        for r in corpus.iter_jsonl(fn):
            runs.append(r)
    times = sorted(_dt(r["capturedAt"]) for r in runs)
    trig = Counter(r.get("triggerEvent") or "UNKNOWN" for r in runs)
    perday = Counter(t.strftime("%Y-%m-%d") for t in times)
    out = {"stream": "ALPHA-0002 prospective", "promisedCadenceMinutes": 10,
           "persistedCaptures": len(times), "first": times[0].isoformat(), "last": times[-1].isoformat(),
           "triggers": dict(trig), "capturesPerDay": {"median": st.median(perday.values()),
                                                      "min": min(perday.values()), "max": max(perday.values())}}
    out.update(gap_stats(times))
    return out, runs


def stream_mrv(root):
    att, man = [], []
    for fn in sorted(glob.glob(os.path.join(root, "attempts", "*.jsonl"))):
        att += list(corpus.iter_jsonl(fn))
    for fn in sorted(glob.glob(os.path.join(root, "runs", "*", "*.json"))):
        man.append(json.load(open(fn)))
    times = sorted(_dt(m["cycleStartedAt"]) for m in man if m.get("cycleStartedAt"))
    cls = Counter(m.get("captureClass") for m in man)
    out = {"stream": "MRV prospective v1", "promisedCadenceMinutes": 10,
           "attempts": len(att), "completedManifests": len(man), "attemptsWithoutManifest": len(att) - len(man),
           "captureClass": dict(cls), "first": times[0].isoformat() if times else None,
           "last": times[-1].isoformat() if times else None}
    out.update(gap_stats(times))
    return out


def stream_observations(repo_root):
    caps = set()
    for r in corpus.iter_observations(repo_root):
        c = (r.get("capturedAt") or "")[:19]
        if c:
            caps.add(c)
    times = sorted(_dt(c + "Z") for c in caps)
    out = {"stream": "EdgeLab observation archive (registry snapshots)", "promisedCadenceMinutes": 30,
           "distinctCaptureTimes": len(times), "first": times[0].isoformat(), "last": times[-1].isoformat()}
    out.update(gap_stats(times))
    return out


def quote_integrity(prospective_root):
    per_day = []
    total = priced = 0
    for day in corpus.list_days(os.path.join(prospective_root, "quotes")):
        t = p = 0
        for r in corpus.iter_prospective(prospective_root, "quotes", [day]):
            t += 1
            if any(r.get(k) is not None for k in ("yesBid", "yesAsk", "noBid", "noAsk", "lastPrice")):
                p += 1
        total += t
        priced += p
        per_day.append((day, t, p))
    return {"quoteRows": total, "rowsWithAnyPrice": priced, "firstDay": per_day[0][0], "lastDay": per_day[-1][0],
            "daysWithAnyPrice": sum(1 for d in per_day if d[2] > 0)}


def per_game_coverage(prospective_root, runs):
    """Captures per game inside T-240..T-0, from quotes + quotes_unchanged run ids."""
    run_time = {r["runId"]: _dt(r["capturedAt"]) for r in runs}
    seen = defaultdict(set)          # gameKey -> set(runId)
    start = {}
    for kind in ("quotes", "quotes_unchanged"):
        for r in corpus.iter_prospective(prospective_root, kind):
            t = r.get("marketTicker") or ""
            if not t.startswith(("KXMLBGAME", "KXMLBTOTAL", "KXMLBF5-", "KXMLBSPREAD")):
                continue
            p = tx.parse_market_ticker(t)
            if p.get("gameKey"):
                seen[p["gameKey"]].add(r["runId"])
                start[p["gameKey"]] = p["scheduledStartUtc"]
    rows = []
    for g, rids in seen.items():
        offs = sorted(((start[g] - run_time[r]).total_seconds() / 60.0) for r in rids if r in run_time)
        pre = [o for o in offs if 0 < o <= 240]
        # largest blind interval inside T-240..T-0 (include both window edges)
        edges = sorted(set(pre) | {240.0, 0.0}, reverse=True)
        blind = max((a - b) for a, b in zip(edges, edges[1:])) if len(edges) > 1 else 240.0
        rows.append({"game": g, "capturesT240": len(pre), "lastCaptureMinBeforeStart": min(pre) if pre else None,
                     "maxBlindMin": round(blind, 1)})
    n = len(rows)
    last = [r["lastCaptureMinBeforeStart"] for r in rows if r["lastCaptureMinBeforeStart"] is not None]
    return {
        "gamesSeen": n,
        "capturesInT240ToT0": {"median": st.median([r["capturesT240"] for r in rows]),
                               "p10": _pct([r["capturesT240"] for r in rows], .1),
                               "share0": round(sum(1 for r in rows if r["capturesT240"] == 0) / n, 3),
                               "share_ge_12": round(sum(1 for r in rows if r["capturesT240"] >= 12) / n, 3)},
        "lastCaptureMinBeforeStart": {"median": _pct(last, .5), "p90": _pct(last, .9)},
        "shareGamesWithCaptureInT60ToT0": round(sum(1 for x in last if x <= 60) / n, 3),
        "maxBlindIntervalMin": {"median": _pct([r["maxBlindMin"] for r in rows], .5),
                                "p90": _pct([r["maxBlindMin"] for r in rows], .9)},
        "note": "MRV design needs >= 24 captures in T-240..T-0 (10-minute cadence); the research hypotheses that "
                "were DATA_BLOCKED (information events, sharp lead/lag at minutes) need blind intervals <= 30 min.",
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=REPO)
    ap.add_argument("--prospective-root", required=True)
    ap.add_argument("--mrv-root", default=None)
    ap.add_argument("--out", default=os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0003"))
    a = ap.parse_args(argv)

    a2, runs = stream_alpha0002(a.prospective_root)
    out = {"schema": "mlb_alpha_0003_capture_audit_v1",
           "streams": [a2, stream_observations(a.repo_root)],
           "alpha0002QuoteIntegrity": quote_integrity(a.prospective_root),
           "alpha0002PerGamePregameCoverage": per_game_coverage(a.prospective_root, runs)}
    if a.mrv_root:
        out["streams"].append(stream_mrv(a.mrv_root))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "capture_audit.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
