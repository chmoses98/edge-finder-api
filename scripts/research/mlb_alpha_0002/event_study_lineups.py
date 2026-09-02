#!/usr/bin/env python3
"""MLB-ALPHA-0002 information-event study (historical, bounded): lineup
confirmation as seen in successive slate captures, and Kalshi's minute
response from the exchange record.

Historically there is NO lineup-confirmation timestamp. The best bound is:
the first slate capture at which lineupStatus == confirmed (t_conf) and the
previous capture (t_prev) where it was not. The true event lies in
[t_prev, t_conf]. We measure the Kalshi game_result and F5 mids at
t_prev, t_conf, and t_conf + 5/10/15/30 min, and report the absolute move
between t_prev and t_conf (event window) versus an equal-length window
immediately before t_prev (control). If the event window is not larger
than control, no measurable lineup effect exists at this resolution.
This is a HISTORICAL BOUND ONLY; the prospective capture records the
actual first-seen time. RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from scripts.research.mlb_alpha_0002.build_candle_panel import load_ticker_series, state_at, two_sided, EPOCH  # noqa: E402

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "event_study_lineups.json")


def iter_gz(p):
    with gzip.open(p, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def minute_of(ts):
    return int((datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None) - EPOCH).total_seconds() // 60)


def main():
    # lineup status per (gameKey, capture) from the sharp panel (one row per book; take any)
    status = defaultdict(dict)
    for r in iter_gz(os.path.join(ART, "pit_sharp_panel.jsonl.gz")):
        if r.get("gameKey"):
            status[r["gameKey"]][r["capturedAt"]] = (r.get("lineupStatus"), r.get("startTime"))
    events = []
    for gk, caps in status.items():
        ordered = sorted(caps)
        for i in range(1, len(ordered)):
            prev_s, cur_s = caps[ordered[i - 1]][0], caps[ordered[i]][0]
            if cur_s == "confirmed" and prev_s != "confirmed":
                events.append((gk, ordered[i - 1], ordered[i], caps[ordered[i]][1]))
    dates = sorted({e[0][:10] for e in events})
    series = load_ticker_series(dates)
    by_event = defaultdict(list)
    for t, s in series.items():
        if s["family"] in ("game_result", "inning_result"):
            by_event[t.rsplit("-", 1)[0]].append(t)
    out_rows = []
    for gk, t_prev, t_conf, start in events:
        ev = gk.split(":", 1)[1]
        m_prev, m_conf = minute_of(t_prev), minute_of(t_conf)
        if start:
            m0 = minute_of(start)
            if m_conf >= m0:
                continue
        L = m_conf - m_prev
        if L <= 0 or L > 360:
            continue
        for t in by_event.get(next((k for k in by_event if k.endswith(ev)), ""), []):
            cd = series[t]["candles"]
            a, b, c = state_at(cd, m_prev - L), state_at(cd, m_prev), state_at(cd, m_conf)
            if not (two_sided(a) and two_sided(b) and two_sided(c)):
                continue
            mid = lambda x: (x[0] + x[1]) / 2.0
            row = {"game": gk, "ticker": t, "family": series[t]["family"], "tPrev": t_prev, "tConf": t_conf,
                   "windowMin": L, "controlAbsMove": abs(mid(b) - mid(a)), "eventAbsMove": abs(mid(c) - mid(b))}
            for h in (5, 10, 15, 30):
                d = state_at(cd, m_conf + h)
                row["postAbsMove%d" % h] = abs(mid(d) - mid(c)) if two_sided(d) else None
            out_rows.append(row)
    res = {"programId": "MLB-ALPHA-0002", "study": "lineup confirmation (bounded by slate captures)",
           "eventsFound": len(events), "rows": len(out_rows), "games": len({r["game"] for r in out_rows})}
    if out_rows:
        res["medianWindowMin"] = float(np.median([r["windowMin"] for r in out_rows]))
        res["meanControlAbsMoveCents"] = float(np.mean([r["controlAbsMove"] for r in out_rows]))
        res["meanEventAbsMoveCents"] = float(np.mean([r["eventAbsMove"] for r in out_rows]))
        for h in (5, 10, 15, 30):
            v = [r["postAbsMove%d" % h] for r in out_rows if r["postAbsMove%d" % h] is not None]
            res["meanPostAbsMove%dCents" % h] = float(np.mean(v)) if v else None
        res["verdict"] = ("event window moves more than control" if res["meanEventAbsMoveCents"] > res["meanControlAbsMoveCents"] * 1.25
                          else "no measurable lineup effect at slate-capture resolution")
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
