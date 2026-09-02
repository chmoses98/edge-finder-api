#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section D: observation-cadence audit.

"FIRST qualifying observation inside [T-60, T-0)" is only meaningful
relative to a SAMPLING PROCESS. A rule triggered off a 30-minute stream is
not operationally the same strategy as one triggered off a 1-minute
stream: a faster stream fires earlier, at a different price, and would be
a DIFFERENT candidate wearing the same name.

This audit derives, from the archive itself, exactly which capture streams
produced the historical C01-PIT opportunities and at what cadence, so the
prospective shadow can freeze an explicit trigger stream instead of
silently inheriting whatever polling frequency happens to exist later.

Covers the C01-PIT universe (KXMLBF5TOTAL) across the discovery split and
the SPENT holdout dates. Read-only.
"""

import json
import os
import statistics
import sys
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from scripts.research.mlb_alpha_0001.build_entry_rows import (  # noqa: E402
    parse_event, parse_ts, iter_jsonl, partition_paths)

WINDOW_OPEN, WINDOW_CLOSE = 60.0, 0.0


def main():
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    groups = {
        "discovery": splits["discovery"]["dates"],
        "validation": splits["validation"]["dates"],
        "spent_holdout": splits["blindHoldout"]["dates"],
    }
    parts = partition_paths("observations")

    per_date = {}
    stream_counts = Counter()
    stream_in_window = Counter()
    regime_gaps = defaultdict(list)

    for split, dates in groups.items():
        for date in dates:
            if date not in parts:
                continue
            # capture times per (ticker) and per stream
            by_ticker = defaultdict(list)
            streams_today = Counter()
            for r in iter_jsonl(parts[date]):
                if not r["marketTicker"].startswith("KXMLBF5TOTAL-"):
                    continue
                ev = parse_event(r.get("eventTicker"))
                if ev is None or ev[0] != date:
                    continue
                src = (r.get("provenance") or {}).get("sourceSystem") or "unknown"
                stream_counts[src] += 1
                streams_today[src] += 1
                by_ticker[r["marketTicker"]].append((parse_ts(r["capturedAt"]), ev[1], src))

            gaps, in_window_tickers, tickers = [], 0, len(by_ticker)
            window_capture_counts = []
            for _t, obs in by_ticker.items():
                obs.sort(key=lambda x: x[0])
                times = [o[0] for o in obs]
                for a, b in zip(times, times[1:]):
                    gaps.append((b - a).total_seconds() / 60.0)
                start = obs[0][1]
                inwin = [o for o in obs
                         if WINDOW_CLOSE <= (start - o[0]).total_seconds() / 60.0 <= WINDOW_OPEN]
                window_capture_counts.append(len(inwin))
                if inwin:
                    in_window_tickers += 1
                    for o in inwin:
                        stream_in_window[o[2]] += 1

            regime = ("single_daily" if date < "2026-08-09"
                      else ("early_intraday" if date < "2026-08-11" else "multi_intraday"))
            regime_gaps[regime] += gaps
            per_date[date] = {
                "split": split,
                "regime": regime,
                "streams": dict(streams_today),
                "tickers": tickers,
                "tickersWithAnyInWindowCapture": in_window_tickers,
                "missingWindowRate": (round(1 - in_window_tickers / tickers, 4)
                                      if tickers else None),
                "medianInterCaptureGapMin": (round(statistics.median(gaps), 2)
                                             if gaps else None),
                "p90GapMin": (round(sorted(gaps)[int(0.90 * len(gaps))], 2)
                              if len(gaps) >= 10 else None),
                "p95GapMin": (round(sorted(gaps)[int(0.95 * len(gaps))], 2)
                              if len(gaps) >= 20 else None),
                "medianCapturesInsideWindow": (round(statistics.median(window_capture_counts), 2)
                                               if window_capture_counts else None),
            }

    def regime_summary(g):
        if not g:
            return None
        g = sorted(g)
        return {"n": len(g), "medianGapMin": round(statistics.median(g), 2),
                "p90GapMin": round(g[int(0.90 * len(g))], 2),
                "p95GapMin": round(g[int(0.95 * len(g))], 2)}

    dates_all = sorted(per_date)
    missing = [d for d in dates_all if per_date[d]["tickersWithAnyInWindowCapture"] == 0]
    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "D_observation_cadence_audit",
        "windowMinutes": [WINDOW_OPEN, WINDOW_CLOSE],
        "captureStreamsSeen": dict(stream_counts),
        "captureStreamsProducingInWindowObservations": dict(stream_in_window),
        "regimeGapSummary": {k: regime_summary(v) for k, v in regime_gaps.items()},
        "datesWithZeroInWindowCapture": missing,
        "datesWithZeroInWindowCaptureCount": len(missing),
        "totalDatesAudited": len(dates_all),
        "perDate": per_date,
    }
    out = os.path.join(ART, "cadence_audit.json")
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out)
    print("streams (all obs):", dict(stream_counts))
    print("streams (in-window obs):", dict(stream_in_window))
    print("regime gaps:", json.dumps(doc["regimeGapSummary"], sort_keys=True))
    print("dates with ZERO in-window capture: %d of %d -> %s"
          % (len(missing), len(dates_all), missing))


if __name__ == "__main__":
    sys.exit(main())
