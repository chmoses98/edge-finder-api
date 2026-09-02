#!/usr/bin/env python3
"""MLB-ALPHA-0002 prospective shadow writers.

Turns the day's captured state into candidate ENTRY ROWS -- one per
candidate, append-only, written strictly before any outcome exists. A row
records what was true at capture time and nothing else: no settlement, no
closing price, no realised CLV. Scoring happens later, from a separate
pass that joins these rows to settlement.

Candidates (frozen_candidates.json; rules and hashes unchanged):
  C01-F5REV     F5 moneyline 60-minute >=3c reversal, both sides
  C02-OFI       taker order-flow follow-through (price discovery only)
  D01-SHARPLAG  Pinnacle moved and Kalshi has not yet
  I01-LINEUP    first capture at which a lineup/pitcher fact appears
  C03-BOOKIMB   order-book size imbalance at the touch

MLB-ALPHA-0001's C01-PIT is NOT written here. Its frozen
c01pit_trigger_v1 stream keeps its own writer
(scripts/research/mlb_alpha_0001/shadow_capture.py) and its own storage,
so the two programs' entries are never merged.

RESEARCH ONLY. Reads captured files; writes entry rows. No order path.
"""

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
CAP = os.path.join(ART, "prospective")
SHADOW = os.path.join(CAP, "shadows")
FROZEN = os.path.join(ART, "frozen_candidates.json")

F5_SERIES = "KXMLBF5-"
REVERSAL_CENTS = 3.0
REVERSAL_WINDOW_MIN = 60
OFI_WINDOW_MIN = 30
OFI_THRESHOLD = 0.2
SHARP_MOVE_PP = 0.02
KALSHI_STILL_PP = 0.01
BOOK_IMBALANCE_RATIO = 3.0


def iter_gz(path):
    if not os.path.exists(path):
        return
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def rule_hashes():
    if not os.path.exists(FROZEN):
        return {}
    with open(FROZEN) as fh:
        return {c["candidateId"]: c["ruleSha256"] for c in json.load(fh)["candidates"]}


def already(path):
    seen = set()
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    try:
                        r = json.loads(line)
                        seen.add((r.get("marketTicker"), r.get("episodeKey")))
                    except Exception:
                        continue
    return seen


def emit(candidate_id, date, rows):
    if not rows:
        return 0
    path = os.path.join(SHADOW, candidate_id, date + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seen = already(path)
    n = 0
    with open(path, "a") as fh:                       # append-only
        for r in rows:
            k = (r.get("marketTicker"), r.get("episodeKey"))
            if k in seen:
                continue
            seen.add(k)
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
            n += 1
    return n


def quote_series(date):
    """ticker -> [(capturedAt, quote)] from the change-suppressed store."""
    out = defaultdict(list)
    for r in iter_gz(os.path.join(CAP, "quotes", date + ".jsonl.gz")):
        if r.get("yesBid") is None or r.get("yesAsk") is None:
            continue
        out[r["marketTicker"]].append((parse(r["capturedAt"]), r))
    for t in out:
        out[t].sort(key=lambda x: x[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(datetime.utcnow() - timedelta(hours=4)).strftime("%Y-%m-%d"))
    args = ap.parse_args()
    date = args.date
    hashes = rule_hashes()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    written = {}

    quotes = quote_series(date)

    # ---------------------------------------------------- C01-F5REV
    rows = []
    for t, series in quotes.items():
        if not t.startswith(F5_SERIES):
            continue
        for i, (ts, q) in enumerate(series):
            mid = (q["yesBid"] + q["yesAsk"]) / 2.0
            past = [p for p in series[:i] if (ts - p[0]).total_seconds() <= REVERSAL_WINDOW_MIN * 60]
            if not past:
                continue
            ref = past[0][1]
            d = mid - (ref["yesBid"] + ref["yesAsk"]) / 2.0
            if abs(d) < REVERSAL_CENTS:
                continue
            side = "YES" if d <= -REVERSAL_CENTS else "NO"
            rows.append({"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-C01-F5REV",
                         "ruleSha256": hashes.get("MLB-ALPHA-0002-C01-F5REV"),
                         "episodeKey": ts.strftime("%Y%m%dT%H%M"), "marketTicker": t,
                         "eventTicker": q.get("eventTicker"), "capturedAt": q["capturedAt"],
                         "writtenAt": now, "signalSide": side, "dMid60Cents": round(d, 2),
                         "yesBid": q["yesBid"], "yesAsk": q["yesAsk"],
                         "executablePriceCents": q["yesAsk"] if side == "YES" else 100 - q["yesBid"],
                         "passiveLimitCents": q["yesBid"] if side == "YES" else 100 - q["yesAsk"],
                         "spreadCents": q["yesAsk"] - q["yesBid"],
                         "volume": q.get("volume"), "openInterest": q.get("openInterest"),
                         "outcomeFieldsPresent": False})
    written["MLB-ALPHA-0002-C01-F5REV"] = emit("MLB-ALPHA-0002-C01-F5REV", date, rows)

    # -------------------------------------------------------- C02-OFI
    flow = defaultdict(list)
    for r in iter_gz(os.path.join(CAP, "trades", date + ".jsonl.gz")):
        try:
            q = float(r.get("count_fp") or 0)
        except (TypeError, ValueError):
            q = 0.0
        flow[r.get("ticker")].append((parse(r["capturedAt"]), r.get("taker_side"), q))
    rows = []
    for t, prints in flow.items():
        prints.sort(key=lambda x: x[0])
        last = prints[-1][0]
        w = [p for p in prints if (last - p[0]).total_seconds() <= OFI_WINDOW_MIN * 60]
        ys = sum(q for _, s, q in w if s == "yes")
        ns = sum(q for _, s, q in w if s == "no")
        tot = ys + ns
        if tot <= 0:
            continue
        ofi = (ys - ns) / tot
        if abs(ofi) < OFI_THRESHOLD:
            continue
        q_now = quotes.get(t, [])
        cur = q_now[-1][1] if q_now else {}
        rows.append({"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-C02-OFI",
                     "ruleSha256": hashes.get("MLB-ALPHA-0002-C02-OFI"),
                     "episodeKey": last.strftime("%Y%m%dT%H%M"), "marketTicker": t,
                     "capturedAt": last.strftime("%Y-%m-%dT%H:%M:%SZ"), "writtenAt": now,
                     "signalSide": "YES" if ofi > 0 else "NO", "ofi30": round(ofi, 4),
                     "tradeQty30": round(tot, 2), "yesBid": cur.get("yesBid"),
                     "yesAsk": cur.get("yesAsk"),
                     "priceDiscoveryOnly": True, "takerTradable": False,
                     "outcomeFieldsPresent": False})
    written["MLB-ALPHA-0002-C02-OFI"] = emit("MLB-ALPHA-0002-C02-OFI", date, rows)

    # --------------------------------------------------- D01-SHARPLAG
    pinn = defaultdict(list)
    for r in iter_gz(os.path.join(CAP, "odds", date + ".jsonl.gz")):
        for b in r.get("bookmakers") or []:
            if b.get("key") != "pinnacle":
                continue
            for mk in b.get("markets") or []:
                if mk.get("key") != "h2h":
                    continue
                outs = {o["name"]: o.get("price") for o in mk.get("outcomes") or []}
                h, a = outs.get(r.get("home")), outs.get(r.get("away"))
                if not h or not a:
                    continue
                ih, ia = 1.0 / h, 1.0 / a
                pinn[r["eventId"]].append((parse(r["capturedAt"]), ih / (ih + ia),
                                           r.get("home"), r.get("away"), b.get("last_update")))
    rows = []
    for eid, seq in pinn.items():
        seq.sort(key=lambda x: x[0])
        if len(seq) < 2:
            continue
        ts, p_now, home, away, upd = seq[-1]
        prev = [s for s in seq[:-1] if (ts - s[0]).total_seconds() <= 15 * 60]
        if not prev:
            continue
        d_pinn = p_now - prev[0][1]
        if abs(d_pinn) < SHARP_MOVE_PP:
            continue
        rows.append({"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-D01-SHARPLAG",
                     "ruleSha256": hashes.get("MLB-ALPHA-0002-D01-SHARPLAG"),
                     "episodeKey": ts.strftime("%Y%m%dT%H%M"), "marketTicker": None,
                     "oddsApiEventId": eid, "home": home, "away": away,
                     "capturedAt": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "writtenAt": now,
                     "pinnacleHomeVigFree": round(p_now, 5),
                     "pinnacleMove15Pp": round(d_pinn * 100, 3),
                     "pinnacleLastUpdate": upd,
                     "kalshiJoinPending": True, "outcomeFieldsPresent": False})
    written["MLB-ALPHA-0002-D01-SHARPLAG"] = emit("MLB-ALPHA-0002-D01-SHARPLAG", date, rows)

    # ----------------------------------------------------- I01-LINEUP
    rows = []
    for r in iter_gz(os.path.join(CAP, "mlb_state", date + ".jsonl.gz")):
        if not (r.get("awayLineupPosted") or r.get("homeLineupPosted")
                or r.get("awayProbableId") or r.get("homeProbableId")):
            continue
        rows.append({"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-I01-LINEUP",
                     "ruleSha256": hashes.get("MLB-ALPHA-0002-I01-LINEUP"),
                     "episodeKey": str(r.get("fp")), "marketTicker": None,
                     "gamePk": r.get("gamePk"), "capturedAt": r["capturedAt"], "writtenAt": now,
                     "eventType": "LINEUP_OR_PITCHER_STATE_CHANGE",
                     "awayLineupPosted": r.get("awayLineupPosted"),
                     "homeLineupPosted": r.get("homeLineupPosted"),
                     "awayProbableId": r.get("awayProbableId"),
                     "homeProbableId": r.get("homeProbableId"),
                     "firstSeenAtCaptureTime": True, "outcomeFieldsPresent": False})
    written["MLB-ALPHA-0002-I01-LINEUP"] = emit("MLB-ALPHA-0002-I01-LINEUP", date, rows)

    # ---------------------------------------------------- C03-BOOKIMB
    rows = []
    for r in iter_gz(os.path.join(CAP, "books", date + ".jsonl.gz")):
        ob = r.get("orderbook") or {}
        yes, no = ob.get("yes") or [], ob.get("no") or []
        yq = sum(float(l[1]) for l in yes[:5] if len(l) > 1)
        nq = sum(float(l[1]) for l in no[:5] if len(l) > 1)
        if yq <= 0 or nq <= 0:
            continue
        ratio = yq / nq if yq >= nq else nq / yq
        if ratio < BOOK_IMBALANCE_RATIO:
            continue
        rows.append({"programId": "MLB-ALPHA-0002", "candidateId": "MLB-ALPHA-0002-C03-BOOKIMB",
                     "ruleSha256": hashes.get("MLB-ALPHA-0002-C03-BOOKIMB"),
                     "episodeKey": str(r.get("fp")), "marketTicker": r["marketTicker"],
                     "capturedAt": r["capturedAt"], "writtenAt": now,
                     "yesTop5Qty": yq, "noTop5Qty": nq, "imbalanceRatio": round(ratio, 3),
                     "heavierSide": "YES" if yq > nq else "NO",
                     "outcomeFieldsPresent": False})
    written["MLB-ALPHA-0002-C03-BOOKIMB"] = emit("MLB-ALPHA-0002-C03-BOOKIMB", date, rows)

    summary = {"programId": "MLB-ALPHA-0002", "date": date, "writtenAt": now,
               "entryRowsWritten": written,
               "note": "entry rows carry no outcome fields; MLB-ALPHA-0001 C01-PIT is written by its own frozen stream"}
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
