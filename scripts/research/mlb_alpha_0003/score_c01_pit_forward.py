#!/usr/bin/env python3
"""
H3-B: score the FROZEN, UNMODIFIED ALPHA-0001 C01-PIT rule (sha256 882f16d8...) on KXMLBF5TOTAL
observations dated 2026-09-02 .. 2026-10-25, i.e. entirely after the rule was frozen (2026-09-01).

The rule is read from its frozen JSON and asserted unchanged; nothing is tuned. Caveat stated in the
protocol: the observation archive is not the 10-minute `c01pit_trigger_v1` poll the prospective
shadow specified, so this is MARKET-RULE evidence on a post-freeze sample, not shadow evidence.
"""
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.mlb_alpha import corpus, taxonomy as tx  # noqa: E402
from lib.edgelab.research.market_structure import settlements  # noqa: E402
from scripts.research.mlb_alpha_0001.family_a_discovery import row_side_econ  # noqa: E402
from scripts.research.mlb_alpha_0001.inference import clustered_roi_inference  # noqa: E402

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0003")
FIRST, LAST = "2026-09-02", "2026-10-25"
BAND = (90, 99)
WIN_OPEN, WIN_CLOSE = 60.0, 0.0


def main():
    frozen = json.load(open(os.path.join(ART, "frozen_candidate_c01_pit.json")))["candidate"]
    assert frozen["ruleSha256"].startswith("882f16d8330af1af")
    assert frozen["rule"]["entryWindowMinutesBeforeStart"] == {"openAt": 60, "closeAt": 0}
    assert tuple(frozen["rule"]["priceBandCentsInclusive"]) == BAND
    outcomes = settlements.build_outcomes(REPO)

    by_ticker = defaultdict(list)
    for r in corpus.iter_observations(REPO):
        t = r.get("marketTicker") or ""
        if not t.startswith("KXMLBF5TOTAL-"):
            continue
        p = tx.parse_market_ticker(t)
        if not p.get("gameKey") or not (FIRST <= p["gameDate"] <= LAST):
            continue
        by_ticker[t].append((p, r))

    rows, dh_skipped = [], 0
    n_contracts_pregame = 0
    for t, pairs in by_ticker.items():
        p0 = pairs[0][0]
        if p0.get("doubleheaderGame"):
            dh_skipped += 1
        start = p0["scheduledStartUtc"]
        pre = sorted([r for p, r in pairs
                      if datetime.fromisoformat(r["capturedAt"].replace("Z", "+00:00")) < start
                      and (r.get("marketStatus") or "active").lower() in ("active", "unknown")],
                     key=lambda r: r["capturedAt"])
        if pre:
            n_contracts_pregame += 1
        for r in pre:
            cap = datetime.fromisoformat(r["capturedAt"].replace("Z", "+00:00"))
            m2s = (start - cap).total_seconds() / 60.0
            if not (WIN_CLOSE <= m2s <= WIN_OPEN):
                continue
            ya = corpus.obs_price_dollars(r, "yesAsk")
            if ya is None:
                continue
            cents = int(round(ya * 100))
            if not (BAND[0] <= cents <= BAND[1]):
                continue
            res = outcomes.get(t)
            if res not in ("YES", "NO"):
                break
            e = row_side_econ(cents, res == "YES")
            if e is None:
                break
            rows.append({"date": p0["gameDate"], "ticker": t, "game": p0["gameKey"], "minutesToStart": round(m2s, 1),
                         "entryAsk": cents, "won": res == "YES", "netPL": e["netPL"], "cash": e["cash"],
                         "phase": "REGULAR" if p0["gameDate"] <= "2026-09-27" else "POSTSEASON"})
            break

    out = {"program": "MLB-ALPHA-0003", "section": "H3-B", "candidateId": frozen["candidateId"],
           "ruleSha256": frozen["ruleSha256"], "ruleModified": False, "window": [FIRST, LAST],
           "triggerStreamCaveat": "observation archive cadence, NOT c01pit_trigger_v1 (10-minute poll)",
           "pregameContractsSeen": n_contracts_pregame, "doubleheaderContractsSeen": dh_skipped, "strata": {}}
    for phase in ("REGULAR", "POSTSEASON", "ALL"):
        rs = [r for r in rows if phase == "ALL" or r["phase"] == phase]
        net_g, cash_g = defaultdict(float), defaultdict(float)
        for r in rs:
            net_g[r["game"]] += r["netPL"]
            cash_g[r["game"]] += r["cash"]
        inf = clustered_roi_inference(net_g, cash_g, np.random.default_rng(20261027), B=2000) if net_g else None
        be = float(np.mean([r["entryAsk"] for r in rs])) / 100.0 if rs else None
        out["strata"][phase] = {
            "opportunities": len(rs), "games": len(net_g), "dates": len({r["date"] for r in rs}),
            "wins": sum(1 for r in rs if r["won"]), "losses": sum(1 for r in rs if not r["won"]),
            "avgEntryAskCents": None if be is None else round(be * 100, 2),
            "netPL": round(sum(r["netPL"] for r in rs), 2), "cash": round(sum(r["cash"] for r in rs), 2),
            "inference": inf,
            "minutesToStart": None if not rs else {"p50": float(np.percentile([r["minutesToStart"] for r in rs], 50))},
        }
    with open(os.path.join(OUT, "h3b_c01_pit_forward.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str)
    print(json.dumps(out, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
