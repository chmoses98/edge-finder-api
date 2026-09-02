#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section B: execution-reality audit of candidate C01.

C01 as frozen enters at LAST_PREGAME -- the latest archived active quote
before scheduled start. That is selected EX POST: at any live moment you
cannot know a later quote will not arrive, so LAST_PREGAME is not a
point-in-time-executable checkpoint. C01's frozen historical record is
NOT altered; it is reclassified as
"DISCOVERY/VALIDATION SIGNAL -- NOT YET PIT-EXECUTABLE", and this audit
measures exactly what its entries were made of.

DEPTH HONESTY: the archive stores top-of-book yesAsk/yesBid ONLY. There
is no ask size, no depth, no book snapshot anywhere in
data/edgelab/schema_v1/market_observation.schema.json (verified: zero
size/depth/quantity fields). Therefore this audit reports
TOP_OF_BOOK_PRICE_OBSERVED and explicitly refuses to claim a $10 order
was fillable. Historical capacity is UNKNOWN/UNVERIFIED.

Also measures, for Section C, how often each standardized pregame
checkpoint is even AVAILABLE for a C01-eligible contract -- capture
mechanics only, no outcome involved.

Uses discovery + validation only. The blind holdout is never read.
"""

import gzip
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from lib.edgelab.checkpoints import select_closing_quote  # noqa: E402
from scripts.research.mlb_alpha_0001.build_entry_rows import (  # noqa: E402
    parse_event, parse_ts, iter_jsonl, partition_paths)

BAND = (90, 99)


def pct(a, qs):
    return {("p%d" % q): round(float(np.percentile(a, q)), 1) for q in qs}


def main():
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    dates = list(splits["discovery"]["dates"]) + list(splits["validation"]["dates"])
    split_of = {d: "discovery" for d in splits["discovery"]["dates"]}
    split_of.update({d: "validation" for d in splits["validation"]["dates"]})
    parts = partition_paths("observations")

    mins, spreads, vols, ois = [], [], [], []
    by_date, by_source, by_regime = Counter(), Counter(), Counter()
    same_as_close = Counter()
    prior_identical, persistence_min = [], []
    checkpoint_label = Counter()
    cp_availability = Counter()
    contracts = 0
    clv_when_distinct = []

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
            start_utc = pairs[0][0]
            pregame = [q for _, q in pairs
                       if parse_ts(q["capturedAt"]) < start_utc
                       and (q.get("marketStatus") or "active").lower() in ("active", "unknown")]
            if not pregame:
                continue
            pregame.sort(key=lambda q: q["capturedAt"])
            entry = pregame[-1]                      # the C01 LAST_PREGAME entry
            ya = entry.get("yesAsk")
            if ya is None or not (BAND[0] <= ya <= BAND[1]):
                continue
            contracts += 1

            m2s = (start_utc - parse_ts(entry["capturedAt"])).total_seconds() / 60.0
            mins.append(m2s)
            by_date[date] += 1
            by_source[(entry.get("provenance") or {}).get("sourceSystem")] += 1
            # capture regime: the archive's own cadence eras
            regime = ("single_daily_capture" if date < "2026-08-09"
                      else "multi_intraday_capture")
            by_regime[regime] += 1
            checkpoint_label[entry.get("checkpoint")] += 1
            if entry.get("spreadCents") is not None:
                spreads.append(entry["spreadCents"])
            if entry.get("volume") is not None:
                vols.append(entry["volume"])
            if entry.get("openInterest") is not None:
                ois.append(entry["openInterest"])

            # official closing quote via REUSED production semantics
            closing = select_closing_quote(
                pregame, scheduled_start=start_utc.isoformat() + "Z")
            is_same = closing is not None and closing["capturedAt"] == entry["capturedAt"]
            same_as_close["same" if is_same else "different"] += 1
            if closing is not None and not is_same and closing.get("yesAsk") is not None:
                clv_when_distinct.append(closing["yesAsk"] - ya)

            # how long had this exact yesAsk already been quoted?
            k = 0
            for q in reversed(pregame[:-1]):
                if q.get("yesAsk") == ya:
                    k += 1
                else:
                    break
            prior_identical.append(k)
            if k > 0:
                first_same = pregame[-1 - k]
                persistence_min.append(
                    (parse_ts(entry["capturedAt"]) - parse_ts(first_same["capturedAt"])
                     ).total_seconds() / 60.0)
            else:
                persistence_min.append(0.0)

            # Section C input: which standardized checkpoints exist at all
            labels = {q.get("checkpoint") for q in pregame}
            for cp in ("FIRST_DAILY", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30",
                       "T_MINUS_15", "T_MINUS_5", "LINEUP_CONFIRMATION"):
                if cp in labels:
                    cp_availability[cp] += 1
            # window-based availability (capture mechanics only)
            for lo, hi in ((0, 15), (0, 30), (0, 60), (0, 90), (0, 120)):
                if any(lo <= (start_utc - parse_ts(q["capturedAt"])).total_seconds() / 60.0 <= hi
                       for q in pregame):
                    cp_availability["WINDOW_T-%d_to_T-%d" % (hi, lo)] += 1

    mins_a = np.array(mins)
    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "B_c01_execution_reality_audit",
        "candidateId": "MLB-ALPHA-0001-C01",
        "reclassification": "DISCOVERY/VALIDATION SIGNAL -- NOT YET PIT-EXECUTABLE",
        "reclassificationReason": (
            "LAST_PREGAME is chosen ex post as the latest archived active quote "
            "before first pitch; live, you cannot know no later quote will "
            "arrive. The frozen historical record is unchanged."),
        "splitsCovered": ["discovery", "validation"],
        "blindHoldout": "NOT READ",
        "c01Entries": contracts,
        "minutesToStart": dict(pct(mins_a, [5, 25, 50, 75, 95]),
                               mean=round(float(mins_a.mean()), 1),
                               min=round(float(mins_a.min()), 1),
                               max=round(float(mins_a.max()), 1)),
        "entriesByDate": dict(by_date),
        "entriesByCaptureSource": {str(k): v for k, v in by_source.items()},
        "entriesByCaptureRegime": dict(by_regime),
        "archivedCheckpointLabelOfEntry": {str(k): v for k, v in checkpoint_label.items()},
        "spreadCents": {"mean": round(float(np.mean(spreads)), 3),
                        "distribution": dict(Counter(int(s) for s in spreads))},
        "volume": dict(pct(np.array(vols), [25, 50, 75])) if vols else None,
        "openInterest": dict(pct(np.array(ois), [25, 50, 75])) if ois else None,
        "priorIdenticalQuotes": {
            "mean": round(float(np.mean(prior_identical)), 2),
            "distribution": dict(Counter(prior_identical)),
        },
        "quotedAskPersistenceMinutes": dict(
            pct(np.array(persistence_min), [25, 50, 75, 95]),
            mean=round(float(np.mean(persistence_min)), 1)),
        "entryVsOfficialClose": {
            "sameQuote": same_as_close["same"],
            "differentQuote": same_as_close["different"],
            "pctSame": round(100.0 * same_as_close["same"] / max(contracts, 1), 2),
            "clvCentsWhenDistinct": {
                "n": len(clv_when_distinct),
                "mean": round(float(np.mean(clv_when_distinct)), 3) if clv_when_distinct else None,
                "note": "closing yesAsk minus entry yesAsk; positive = bought below the close",
            },
        },
        "fillDepth": {
            "askSizeArchived": False,
            "bookDepthArchived": False,
            "claim": "TOP_OF_BOOK_PRICE_OBSERVED",
            "tenDollarFillProven": False,
            "historicalCapacity": "UNKNOWN/UNVERIFIED",
            "reason": ("market_observation.schema.json contains no size, depth, "
                       "quantity or book field; only top-of-book prices are "
                       "archived, so no historical order could be proven fillable."),
        },
        "checkpointAvailabilityForSectionC": dict(cp_availability),
    }
    path = os.path.join(ART, "c01_execution_audit.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("entriesByDate",)}, indent=1, sort_keys=True))
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
