#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section C: DISCOVERY-ONLY sanity check of C01-PIT.

The rule was frozen (with its sha256) BEFORE this ran; nothing here may
feed back into the rule. Discovery is the legitimate search split, so
scoring the translation there is not a peek. VALIDATION IS NOT SCORED --
validation was already opened once for C01, and reusing it for a second
rule needs explicit authorization. The BLIND HOLDOUT IS NOT READ.

Also reports opportunity overlap with the original C01 entries.
"""

import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from scripts.research.mlb_alpha_0001.build_entry_rows import (  # noqa: E402
    parse_event, parse_ts, iter_jsonl, partition_paths, load_settlement_map)
from scripts.research.mlb_alpha_0001.family_a_discovery import (  # noqa: E402
    row_side_econ, SIZE_INFLATION, BOOT)
from scripts.research.mlb_alpha_0001.inference import clustered_roi_inference  # noqa: E402

SEED = 20260905
WIN_OPEN, WIN_CLOSE = 60.0, 0.0
BAND = (90, 99)


def main():
    with open(os.path.join(ART, "frozen_candidate_c01_pit.json")) as fh:
        frozen = json.load(fh)["candidate"]
    assert frozen["rule"]["entryWindowMinutesBeforeStart"] == {"openAt": 60, "closeAt": 0}

    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        dates = json.load(fh)["discovery"]["dates"]
    settled = load_settlement_map()
    parts = partition_paths("observations")

    pit_rows, c01_tickers, pit_tickers = [], set(), set()
    for date in dates:
        if date not in parts:
            continue
        by_ticker = defaultdict(list)
        for r in iter_jsonl(parts[date]):
            if not r["marketTicker"].startswith("KXMLBF5TOTAL-"):
                continue
            ev = parse_event(r.get("eventTicker"))
            if ev is None or ev[0] != date:
                continue
            by_ticker[r["marketTicker"]].append((ev[1], r))
        for ticker, pairs in by_ticker.items():
            start = pairs[0][0]
            pregame = [q for _, q in pairs
                       if parse_ts(q["capturedAt"]) < start
                       and (q.get("marketStatus") or "active").lower() in ("active", "unknown")]
            if not pregame:
                continue
            pregame.sort(key=lambda q: q["capturedAt"])
            # original C01 entry (ex post last pregame), for overlap only
            last = pregame[-1]
            if last.get("yesAsk") is not None and BAND[0] <= last["yesAsk"] <= BAND[1]:
                c01_tickers.add(ticker)
            # C01-PIT: FIRST qualifying quote inside [T-60, T-0)
            for q in pregame:
                m2s = (start - parse_ts(q["capturedAt"])).total_seconds() / 60.0
                if not (WIN_CLOSE <= m2s <= WIN_OPEN):
                    continue
                ya = q.get("yesAsk")
                if ya is None or not (BAND[0] <= ya <= BAND[1]):
                    continue
                res = settled.get(ticker)
                if res not in ("YES", "NO"):
                    break
                e = row_side_econ(ya, res == "YES")
                if e is None:
                    break
                pit_tickers.add(ticker)
                pit_rows.append({
                    "date": date, "ticker": ticker,
                    "game": date + ":" + q["eventTicker"].split("-", 1)[1],
                    "minutesToStart": round(m2s, 1), "entryAsk": ya,
                    "won": res == "YES", "netPL": e["netPL"], "cash": e["cash"],
                })
                break   # first qualifying quote only

    rng = np.random.default_rng(SEED)
    net_g, cash_g = defaultdict(float), defaultdict(float)
    for r in pit_rows:
        net_g[r["game"]] += r["netPL"]
        cash_g[r["game"]] += r["cash"]
    inf = clustered_roi_inference(net_g, cash_g, rng, B=BOOT) if net_g else None
    if inf:
        inf["pConservative"] = min(1.0, round(inf["pPrimary"] * SIZE_INFLATION, 6))

    m2s = np.array([r["minutesToStart"] for r in pit_rows])
    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "C_c01_pit_discovery_sanity_check",
        "candidateId": frozen["candidateId"],
        "ruleSha256": frozen["ruleSha256"],
        "ruleFrozenBeforeScoring": True,
        "splitScored": "discovery",
        "validationScored": False,
        "blindHoldout": "NOT READ",
        "opportunities": len(pit_rows),
        "uniqueGames": len(net_g),
        "dates": len({r["date"] for r in pit_rows}),
        "wins": sum(1 for r in pit_rows if r["won"]),
        "losses": sum(1 for r in pit_rows if not r["won"]),
        "avgEntryAskCents": round(float(np.mean([r["entryAsk"] for r in pit_rows])), 2) if pit_rows else None,
        "minutesToStartAtEntry": {
            "p5": round(float(np.percentile(m2s, 5)), 1),
            "p50": round(float(np.percentile(m2s, 50)), 1),
            "p95": round(float(np.percentile(m2s, 95)), 1),
        } if pit_rows else None,
        "netPL": round(sum(r["netPL"] for r in pit_rows), 2),
        "inference": inf,
        "overlapWithOriginalC01": {
            "c01DiscoveryContracts": len(c01_tickers),
            "pitDiscoveryContracts": len(pit_tickers),
            "sharedContracts": len(c01_tickers & pit_tickers),
            "pitOnly": len(pit_tickers - c01_tickers),
            "c01Only": len(c01_tickers - pit_tickers),
            "jaccard": round(len(c01_tickers & pit_tickers) /
                             max(len(c01_tickers | pit_tickers), 1), 3),
        },
        "executionCaveat": frozen["executionCaveat"],
    }
    path = os.path.join(ART, "c01_pit_discovery_sanity.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(doc, indent=1, sort_keys=True))
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
