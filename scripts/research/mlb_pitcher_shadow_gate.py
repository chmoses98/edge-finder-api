#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_shadow_gate.py
===========================================
Cheap scheduled gate for the MLB pitcher PURE shadow workflow (RESEARCH ONLY). Standard
library only: it runs on a sparse, blobless checkout (lib/research + scripts/research +
the store) with no pip install, and says whether the full job has anything to do now.

work=true when any of these holds (each listed in `reasons`):
  * a capture window is due for some game (the collector's own `window_for`);
  * a window closed with no capture and is not yet recorded as missed;
  * a captured game is old enough to have finished and has no final outcome yet;
  * the daily evaluation (15:xx UTC) has not been written today;
  * the schedule could not be fetched (fail open: the full job then reports the failure).

    python3 scripts/research/mlb_pitcher_shadow_gate.py [--store DIR] [--github-output FILE]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import mlb_pitcher_shadow_plan as P  # noqa: E402

EVALUATE_HOUR_UTC = 15


def fetch_schedule(date, timeout=20):
    req = urllib.request.Request(P.SCHEDULE_URL.format(date=date),
                                 headers={"User-Agent": "edge-finder-edgelab/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def evaluated_today(root, now):
    return bool(glob.glob(os.path.join(root, "evaluations", f"{now.strftime('%Y%m%d')}T*.json")))


def decide(games, now, root, schedule_ok=True):
    """Pure given its inputs. Returns {"work", "evaluate", "reasons", ...}."""
    reasons = []
    due = []
    done_by_date = {}
    for g in games:
        d = g["official_date"]
        if d not in done_by_date:
            done_by_date[d] = P.existing_windows(root, d)
        w, _why = P.window_for(g, now, done_by_date[d].get(g["pk"], set()))
        if w:
            due.append({"game_id": g["pk"], "window": w, "kickoff": g["kickoff"]})
    if due:
        reasons.append(f"capture_due:{len(due)}")
    missed = P.missed_windows(games, now, root)
    if missed:
        reasons.append(f"missed_to_record:{len(missed)}")
    pending = P.outcomes_pending(root, now)
    if pending:
        reasons.append(f"outcomes_pending:{len(pending)}")
    evaluate = now.hour == EVALUATE_HOUR_UTC and not evaluated_today(root, now)
    if evaluate:
        reasons.append("daily_evaluation")
    if not schedule_ok:
        reasons.append("schedule_fetch_failed")
    return {"now": P.iso(now), "work": bool(reasons), "evaluate": evaluate, "reasons": reasons, "due": due,
            "missed": [(m["game_id"], m["window"]) for m in missed], "outcomes_pending": pending}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--store", default=os.path.join(ROOT, "data", "shadow_pure", "mlb_pitcher"))
    ap.add_argument("--github-output", default=None)
    a = ap.parse_args(argv)
    now = P.utcnow()
    games, ok = [], True
    for d in P.capture_dates(now):
        try:
            games += P.parse_schedule(fetch_schedule(d), P.iso(P.utcnow()))
        except Exception as exc:  # noqa: BLE001 - fail open, the full job reports it
            print(json.dumps({"date": d, "schedule_error": f"{type(exc).__name__}: {exc}"}))
            ok = False
    out = decide(games, now, a.store, schedule_ok=ok)
    out["games"] = len(games)
    print(json.dumps(out, indent=1))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"work={'true' if out['work'] else 'false'}\nevaluate={'true' if out['evaluate'] else 'false'}\n"
                     f"reasons={','.join(out['reasons']) or 'none'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
