#!/usr/bin/env python3
"""MLB-ALPHA-0001: research-layer settlement correction for the
game_total / inning_total ladder-semantics defect.

FINDING (proven during Family A quality control, before any candidate was
frozen): Kalshi settles KXMLBTOTAL / KXMLBF5TOTAL rung "-N" as
"N OR MORE runs" (YES iff total >= N), but lib.edgelab.settlement settles
those families as YES iff total > N (integer threshold stored verbatim,
unlike team_total / winning_margin, which store the correct N-0.5).
Evidence:
  - 13/13 anomalies where a rung's last in-game quote was >= 97c yet the
    archive settled NO occur at EXACTLY the true final total T (pinned
    independently from the half-point team_total ladders) -- e.g.
    KXMLBTOTAL-26AUG231515SFBOS-9 quoted 99c, final total exactly 9,
    archived result NO.
  - No event anywhere in the archive shows the reverse anomaly pattern
    exceeding +1 rung.
Player props already settle >= N (correct); team_total / winning_margin
already use half-point thresholds (correct). Only game_total and
inning_total are affected.

CORRECTION (mechanical, outcome-agnostic, no leakage): the archived
engine's underlying totals are internally consistent (163/163 events
agree with team-total-pinned exact totals), so for a rung N:

  archived(N)  = [T > N] = [T >= N+1]
  corrected(N) = [T >= N]  = archived(N-1)

Per event: T >= maxYES+1 and T <= minNO (from archived results), so
  corrected(N) = YES  if N <= maxYES + 1
  corrected(N) = NO   if N > minNO
  otherwise UNRESOLVED (sparse ladder) -> excluded from research scoring.

Writes corrected_total_settlements.json mapping ticker ->
{"corrected": "YES"|"NO", "archived": ..., "basis": ...}.

PRODUCTION FIREWALL: this is a research artifact only. It does not touch
lib.edgelab.settlement, the settlement store, the wager ledger, or any
production path. The production-side implication (the model prices
"> N" for a ">= N" contract) is REPORTED in the program findings for a
separately authorized production fix, never silently changed here.
"""

import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EDGELAB = os.path.join(REPO, "data", "edgelab")
ART = os.path.join(EDGELAB, "research_artifacts", "mlb_alpha_0001")

TICKER_RE = {
    "game_total": re.compile(r"^(KXMLBTOTAL)-([0-9A-Z]+)-(\d+)$"),
    "inning_total": re.compile(r"^(KXMLBF5TOTAL)-([0-9A-Z]+)-(\d+)$"),
}


def iter_settlements():
    for p in sorted(glob.glob(os.path.join(EDGELAB, "settlements", "*.jsonl*"))):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def main():
    ladders = defaultdict(dict)  # (family, series, event) -> {rung: result}
    for r in iter_settlements():
        fam = r.get("marketFamily")
        if fam not in TICKER_RE or r.get("result") not in ("YES", "NO"):
            continue
        m = TICKER_RE[fam].match(r["marketTicker"])
        if not m:
            continue
        series, event, rung = m.group(1), m.group(2), int(m.group(3))
        ladders[(fam, series, event)][rung] = r["result"]

    out = {}
    stats = defaultdict(int)
    for (fam, series, event), ladder in ladders.items():
        yes = [k for k, v in ladder.items() if v == "YES"]
        no = [k for k, v in ladder.items() if v == "NO"]
        if yes and no and max(yes) > min(no):
            stats["nonmonotone_event_skipped"] += len(ladder)
            continue
        lower = (max(yes) + 1) if yes else None  # T >= lower
        upper = min(no) if no else None          # T <= upper
        for rung, archived in ladder.items():
            ticker = "%s-%s-%d" % (series, event, rung)
            if lower is not None and rung <= lower:
                corrected = "YES"
            elif upper is not None and rung > upper:
                corrected = "NO"
            else:
                corrected = None
            if corrected is None:
                stats[fam + "_unresolved"] += 1
                out[ticker] = {"family": fam, "archived": archived,
                               "corrected": None, "basis": "sparse_ladder_unresolved"}
            else:
                stats[fam + ("_flipped" if corrected != archived else "_kept")] += 1
                out[ticker] = {"family": fam, "archived": archived,
                               "corrected": corrected, "basis": "rung_shift_ge_semantics"}

    doc = {
        "program": "MLB-ALPHA-0001",
        "researchOnly": True,
        "semantics": "Kalshi total ladders settle YES iff value >= rung (N+); archive engine used value > rung",
        "affectedFamilies": ["game_total", "inning_total"],
        "stats": dict(stats),
        "tickers": out,
    }
    path = os.path.join(ART, "corrected_total_settlements.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote", path)
    for k, v in sorted(stats.items()):
        print("%-32s %d" % (k, v))


if __name__ == "__main__":
    sys.exit(main())
