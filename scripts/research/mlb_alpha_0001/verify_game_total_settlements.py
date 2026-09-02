#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section F support: INDEPENDENT verification of
KXMLBTOTAL (full-game total) contract semantics against MLB final scores.

The F5 verification proved KXMLBF5TOTAL pays YES iff F5 runs >= N. This
script proves (or refutes) the same ">= N" semantics for the full-game
total ladder, so the production fix rests on external ground truth for
BOTH families rather than on internal inference for one of them.

Samples deterministically: every KXMLBTOTAL contract on the discovery +
validation dates whose rung sits at or adjacent to the game's final
total (the only rungs where "> N" and ">= N" disagree), plus a control
set of far-from-boundary rungs that must agree under either rule.

Exact identity only; ambiguous games refused. Read-only. The blind
holdout is never read.
"""

import glob
import gzip
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR, parse_schedule_games  # noqa: E402
from scripts.research.mlb_alpha_0001.build_entry_rows import parse_event  # noqa: E402
from scripts.research.mlb_alpha_0001.verify_f5_settlements import (  # noqa: E402
    http_json, resolve_game_pk, MLB_API)

TICK = re.compile(r"^KXMLBTOTAL-([0-9A-Z]+)-(\d+)$")


def main():
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        sp = json.load(fh)
    dates = set(sp["discovery"]["dates"]) | set(sp["validation"]["dates"])

    # archived settlements + the research correction, for KXMLBTOTAL
    archived, teams_of = {}, {}
    for p in sorted(glob.glob(os.path.join(REPO, "data", "edgelab", "settlements", "*.jsonl*"))):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                t = r.get("marketTicker", "")
                if TICK.match(t) and r.get("result") in ("YES", "NO"):
                    archived[t] = r["result"]
    with open(os.path.join(ART, "corrected_total_settlements.json")) as fh:
        corrected = json.load(fh)["tickers"]

    # team identity per event, from the observation store
    for p in sorted(glob.glob(os.path.join(REPO, "data", "edgelab", "observations", "*.jsonl*"))):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("marketFamily") == "game_total" and r.get("homeTeam"):
                    teams_of[r["eventTicker"]] = (r["awayTeam"], r["homeTeam"])

    by_event = defaultdict(dict)
    for t in archived:
        m = TICK.match(t)
        ev_suffix, rung = m.group(1), int(m.group(2))
        by_event["KXMLBTOTAL-" + ev_suffix][rung] = t

    counts = Counter()
    results = []
    sched_cache = {}
    for ev, rungs in sorted(by_event.items()):
        parsed = parse_event(ev)
        if parsed is None:
            counts["unparseable_event"] += 1
            continue
        gdate, start_utc, _ = parsed
        if gdate not in dates:
            continue
        if ev not in teams_of:
            counts["no_team_identity"] += 1
            continue
        away, home = teams_of[ev]
        if gdate not in sched_cache:
            js = http_json("%s/schedule?sportId=1&date=%s&gameType=R" % (MLB_API, gdate))
            sched_cache[gdate] = parse_schedule_games(js) if js else []
            time.sleep(0.3)
        game_pk, reason = resolve_game_pk(sched_cache[gdate], away, home, start_utc)
        if game_pk is None:
            counts["unresolved_identity"] += 1
            continue
        ls = http_json("%s/game/%s/linescore" % (MLB_API, game_pk))
        time.sleep(0.3)
        t_away = ((ls or {}).get("teams") or {}).get("away", {}).get("runs")
        t_home = ((ls or {}).get("teams") or {}).get("home", {}).get("runs")
        if t_away is None or t_home is None:
            counts["no_final_score"] += 1
            continue
        final_total = t_away + t_home
        counts["verified_games"] += 1
        for rung, ticker in sorted(rungs.items()):
            true_ge = "YES" if final_total >= rung else "NO"
            true_gt = "YES" if final_total > rung else "NO"
            arch = archived.get(ticker)
            corr = (corrected.get(ticker) or {}).get("corrected")
            boundary = (final_total == rung)
            counts["boundary_rungs" if boundary else "control_rungs"] += 1
            if boundary:
                counts["boundary_ge_matches_kalshi" if arch != true_ge else "boundary_ge_conflicts"] += 0
            counts["archived_matches_GE" if arch == true_ge else "archived_differs_from_GE"] += 1
            counts["archived_matches_GT" if arch == true_gt else "archived_differs_from_GT"] += 1
            counts["corrected_matches_GE" if corr == true_ge else "corrected_differs_from_GE"] += 1
            if boundary:
                results.append({
                    "marketTicker": ticker, "gameDate": gdate, "gamePk": game_pk,
                    "finalTotal": final_total, "rung": rung,
                    "trueUnderGE": true_ge, "trueUnderGT": true_gt,
                    "archivedResult": arch, "researchCorrectedResult": corr,
                })
    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "F_support_game_total_semantics_verification",
        "researchOnly": True,
        "question": "does KXMLBTOTAL rung N pay YES iff final total >= N (Kalshi) or > N (production)?",
        "counts": dict(counts),
        "boundaryCases": results,
        "blindHoldout": "NOT READ",
    }
    path = os.path.join(ART, "game_total_semantics_verification.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote", path)
    for k, v in sorted(counts.items()):
        print("  %-34s %d" % (k, v))
    print("boundary cases (final total == rung):", len(results))
    for r in results[:12]:
        print("   ", r["marketTicker"], "total=%d rung=%d" % (r["finalTotal"], r["rung"]),
              "archived=%s corrected=%s trueGE=%s" % (r["archivedResult"],
                                                      r["researchCorrectedResult"], r["trueUnderGE"]))


if __name__ == "__main__":
    sys.exit(main())
