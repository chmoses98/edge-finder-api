#!/usr/bin/env python3
"""MLB-ALPHA-0002 PIT feature layer, part 1: the Kalshi observation panel.

One row per (contract, pregame observation t) that has at least one
STRICTLY LATER pregame observation of the same contract. Every feature is
computed from observations captured at or before t (WHAT DID WE KNOW /
WHEN DID WE KNOW IT = capturedAt). Targets look forward to the contract's
last pregame observation ("close") and to corrected settlement.

Data: data/edgelab/observations/<date>.jsonl.gz, settled range only
(2026-08-02 .. 2026-08-31, ex 2026-08-17 -- the MLB-ALPHA-0001 coverage
facts), settlement store + the research-layer >=N ladder correction, F5
spreads excluded (settled on the wrong horizon, defect #2).

Executable economics use side-relevant executable prices: BUY YES at
yesAsk, BUY NO at 100-yesBid (binary complement; the archive never held a
NO book). Midpoint is a diagnostic only (fair-mid CLV) and is never used
as a fill. CLV signs come from lib.edgelab.clv_convention.

RESEARCH ONLY.
"""

import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab import clv_convention as cc                      # noqa: E402
from lib.edgelab.kalshi_fees import net_settlement_pl_for_order    # noqa: E402
from lib.edgelab.mlb_alpha_identity import parse_event_ticker     # noqa: E402

EDGELAB = os.path.join(REPO, "data", "edgelab")
ART01 = os.path.join(EDGELAB, "research_artifacts", "mlb_alpha_0001")
ART = os.path.join(EDGELAB, "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "pit_kalshi_panel.jsonl.gz")
PROGRAM = "MLB-ALPHA-0002"
ORDER_USD = 10.0
DATES = ["2026-08-%02d" % d for d in range(2, 32) if d != 17]


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def iter_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_settlements():
    settled = {}
    for p in sorted(glob.glob(os.path.join(EDGELAB, "settlements", "*.jsonl*"))):
        for r in iter_jsonl(p):
            if r.get("settlementStatus") == "SETTLED" and r.get("result") in ("YES", "NO"):
                settled[r["marketTicker"]] = r["result"]
    for t in [t for t in settled if t.startswith("KXMLBF5SPREAD-")]:
        del settled[t]
    with open(os.path.join(ART01, "corrected_total_settlements.json")) as fh:
        corr = json.load(fh)["tickers"]
    for t, info in corr.items():
        if t in settled:
            if info["corrected"] is None:
                del settled[t]
            else:
                settled[t] = info["corrected"]
    return settled


def valid_quote(r):
    yb, ya = r.get("yesBid"), r.get("yesAsk")
    return (isinstance(yb, (int, float)) and isinstance(ya, (int, float))
            and 1 <= ya <= 99 and 1 <= (100 - yb) <= 99 and ya >= yb)


def build():
    settled = load_settlements()
    series = defaultdict(list)     # ticker -> [obs]
    meta = {}
    n_obs = 0
    for date in DATES:
        p = os.path.join(EDGELAB, "observations", date + ".jsonl.gz")
        if not os.path.exists(p):
            continue
        for r in iter_jsonl(p):
            t = r.get("marketTicker")
            if not t or t not in settled or r.get("marketStatus") != "active":
                continue
            if not valid_quote(r):
                continue
            ident = parse_event_ticker(r.get("eventTicker"))
            if ident.get("status") != "RESOLVED" or ident["gameDate"] != date:
                continue
            cap = parse_ts(r["capturedAt"])
            if cap >= ident["scheduledStartUtc"]:
                continue
            n_obs += 1
            series[t].append((cap, r))
            if t not in meta:
                meta[t] = {"marketFamily": r.get("marketFamily"), "seriesTicker": r.get("seriesTicker"),
                           "eventTicker": r.get("eventTicker"), "gameDate": date,
                           "gameKey": date + ":" + r["eventTicker"].split("-", 1)[1],
                           "homeTeam": ident["homeTeam"], "awayTeam": ident["awayTeam"],
                           "scheduledStartUtc": ident["scheduledStartUtc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "threshold": r.get("threshold"), "team": r.get("team"),
                           "player": r.get("player")}
    rows = []
    for t, obs in series.items():
        obs.sort(key=lambda x: x[0])
        # dedupe identical capture timestamps (two streams can coincide)
        ded = []
        for cap, r in obs:
            if ded and ded[-1][0] == cap:
                continue
            ded.append((cap, r))
        obs = ded
        if len(obs) < 2:
            continue
        m = meta[t]
        start = parse_ts(m["scheduledStartUtc"])
        close_cap, close = obs[-1]
        c_mid = (close["yesBid"] + close["yesAsk"]) / 2.0
        c_yes_ask = float(close["yesAsk"]); c_no_ask = 100.0 - float(close["yesBid"])
        result = settled[t]

        unchanged_run = 0
        prev = None
        for i, (cap, r) in enumerate(obs[:-1]):
            mid = (r["yesBid"] + r["yesAsk"]) / 2.0
            yes_ask = float(r["yesAsk"]); no_ask = 100.0 - float(r["yesBid"])
            if prev is not None:
                same = (prev[1]["yesBid"] == r["yesBid"] and prev[1]["yesAsk"] == r["yesAsk"])
                unchanged_run = unchanged_run + 1 if same else 0
            row = {
                "programId": PROGRAM, "marketTicker": t, **m,
                "capturedAt": cap.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "minutesToStart": round((start - cap).total_seconds() / 60.0, 1),
                "obsIndex": i, "obsCountPregame": len(obs), "captureSource": r.get("source"),
                # ---- price state (known at t)
                "yesBid": r["yesBid"], "yesAsk": r["yesAsk"], "mid": mid,
                "spreadCents": r.get("spreadCents", r["yesAsk"] - r["yesBid"]),
                "distFrom50": abs(mid - 50.0), "lastPrice": r.get("lastPrice"),
                "lastPriceMinusMid": (r["lastPrice"] - mid) if r.get("lastPrice") is not None else None,
                "volume": r.get("volume"), "openInterest": r.get("openInterest"),
                # ---- dynamics vs previous observation (known at t)
                "prevGapMin": round((cap - prev[0]).total_seconds() / 60.0, 1) if prev else None,
                "dMidPrev": (mid - (prev[1]["yesBid"] + prev[1]["yesAsk"]) / 2.0) if prev else None,
                "dSpreadPrev": ((r["yesAsk"] - r["yesBid"]) - (prev[1]["yesAsk"] - prev[1]["yesBid"])) if prev else None,
                "dVolumePrev": ((r.get("volume") or 0) - (prev[1].get("volume") or 0)) if prev else None,
                "dOIPrev": ((r.get("openInterest") or 0) - (prev[1].get("openInterest") or 0)) if prev else None,
                "unchangedRun": unchanged_run if prev else None,
                "midMinusFirst": mid - (obs[0][1]["yesBid"] + obs[0][1]["yesAsk"]) / 2.0,
                "minutesSinceFirst": round((cap - obs[0][0]).total_seconds() / 60.0, 1),
                # ---- targets (strictly later than t)
                "closeCapturedAt": close_cap.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "closeMinutesToStart": round((start - close_cap).total_seconds() / 60.0, 1),
                "closeYesBid": close["yesBid"], "closeYesAsk": close["yesAsk"],
                "fairMidMoveToClose": round(c_mid - mid, 3),
                "clvYesCents": cc.clv_for_yes(yes_ask, c_yes_ask, unit=cc.UNIT_CENTS),
                "clvNoCents": cc.clv_for_no(no_ask, c_no_ask, unit=cc.UNIT_CENTS),
                "settlementResult": result,
                "netPlBuyYes": round(net_settlement_pl_for_order(ORDER_USD, yes_ask / 100.0, result == "YES"), 4),
                "netPlBuyNo": round(net_settlement_pl_for_order(ORDER_USD, no_ask / 100.0, result == "NO"), 4),
                "clvConvention": cc.CONVENTION_ID, "clvUnit": cc.UNIT_CENTS,
            }
            rows.append(row)
            prev = (cap, r)
    return rows, n_obs, len(series)


def main():
    rows, n_obs, n_tickers = build()
    os.makedirs(ART, exist_ok=True)
    with gzip.open(OUT, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    games = {r["gameKey"] for r in rows}; dates = {r["gameDate"] for r in rows}
    fam = defaultdict(int)
    for r in rows:
        fam[r["marketFamily"]] += 1
    meta = {"programId": PROGRAM, "artifact": os.path.relpath(OUT, REPO), "rows": len(rows),
            "pregameObservationsScanned": n_obs, "tickersWithAnyPregameQuote": n_tickers,
            "games": len(games), "dates": sorted(dates), "byFamily": dict(fam),
            "asOfRule": "features use observations captured <= t; targets use the last pregame observation (> t) and corrected settlement",
            "clvConvention": cc.CONVENTION_ID}
    with open(os.path.join(ART, "pit_kalshi_panel.meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps(meta, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
