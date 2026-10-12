#!/usr/bin/env python3
"""
scripts/research/hitter_v2/champion_hrr_rerun.py
================================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Re-runs the CHAMPION (production
hitter engine, git f7371b2) exactly as the committed W2 audit rerun did
(scripts/research/hitter_w2/incumbent_rerun.py, same capture selection, same
seeds, same instrumented replica), adding only the H+R+RBI (HRR) pmf taken from
the same simulated draws. The committed rerun stored per-stat marginals only, and
HRR cannot be rebuilt from marginals.

Fidelity: for every PROJECTED row, the PA/H/TB/HR/RBI/R/BB/K pmfs must equal the
committed rerun's (data/research/hitter_w2/audit/incumbent_rerun.jsonl.gz);
`--check` reports the number of mismatching rows.
"""
import argparse
import gzip
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_W2 = os.path.join(_ROOT, "scripts", "research", "hitter_w2")
if _W2 not in sys.path:
    sys.path.insert(0, _W2)

import incumbent_rerun as IR  # noqa: E402

_orig_summarize = IR.summarize


def summarize_with_hrr(lines, inss):
    out = _orig_summarize(lines, inss)
    n = len(lines)
    pmf = {}
    for ln in lines:
        v = ln["H"] + ln["R"] + ln["RBI"]
        pmf[v] = pmf.get(v, 0) + 1
    out["HRR"] = {"mean": sum(k * c for k, c in pmf.items()) / n,
                  "pmf": {int(k): c / n for k, c in sorted(pmf.items())}}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-12")
    ap.add_argument("--end", default="2026-09-27")
    ap.add_argument("--n-sims", type=int, default=500)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    IR.summarize = summarize_with_hrr
    dates = sorted(d for d in os.listdir(IR.SLATES) if a.start <= d <= a.end)
    t0 = time.time()
    with gzip.open(a.out, "wt") as fh:
        for d in dates:
            n = IR.run_date(d, a.n_sims, fh)
            print(f"{d}: {n} hitters ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
