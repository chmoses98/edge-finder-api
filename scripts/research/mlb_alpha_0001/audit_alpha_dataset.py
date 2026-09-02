#!/usr/bin/env python3
"""MLB-ALPHA-0001 Mission 1: audit the Kalshi MLB alpha dataset.

Read-only over data/edgelab/* and data/kalshi_registry_snapshots/*.
Produces a machine-readable coverage manifest:

    data/edgelab/research_artifacts/mlb_alpha_0001/coverage_manifest.json

RESEARCH ONLY. Touches no production artifact.
"""

import glob
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EDGELAB = os.path.join(REPO, "data", "edgelab")
RAW_SNAP = os.path.join(REPO, "data", "kalshi_registry_snapshots")
OUT_DIR = os.path.join(EDGELAB, "research_artifacts", "mlb_alpha_0001")


def iter_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def partition_dates(subdir):
    out = {}
    for p in sorted(glob.glob(os.path.join(EDGELAB, subdir, "*.jsonl*"))):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$", os.path.basename(p))
        if m:
            out[m.group(1)] = p
    return out


def audit_observations():
    per_date = {}
    for date, path in partition_dates("observations").items():
        n = 0
        tickers = set()
        games = set()
        fam_tickers = defaultdict(set)
        checkpoints = Counter()
        families = Counter()
        has_yes_bid = has_yes_ask = has_no_bid = has_no_ask = 0
        has_last = has_vol = has_oi = has_spread = 0
        capture_ts = set()
        obs_per_ticker = Counter()
        status = Counter()
        for r in iter_jsonl(path):
            n += 1
            t = r.get("marketTicker")
            tickers.add(t)
            obs_per_ticker[t] += 1
            if r.get("gameId"):
                games.add(r["gameId"])
            fam = r.get("marketFamily")
            families[fam] += 1
            fam_tickers[fam].add(t)
            checkpoints[r.get("checkpoint")] += 1
            status[r.get("marketStatus")] += 1
            if r.get("yesBid") is not None:
                has_yes_bid += 1
            if r.get("yesAsk") is not None:
                has_yes_ask += 1
            if r.get("noBid") is not None:
                has_no_bid += 1
            if r.get("noAsk") is not None:
                has_no_ask += 1
            if r.get("lastPrice") is not None:
                has_last += 1
            if r.get("volume") is not None:
                has_vol += 1
            if r.get("openInterest") is not None:
                has_oi += 1
            if r.get("spreadCents") is not None:
                has_spread += 1
            ts = r.get("capturedAt")
            if ts:
                capture_ts.add(ts[:16])  # minute resolution
        per_date[date] = {
            "observations": n,
            "uniqueTickers": len(tickers),
            "uniqueGames": len(games),
            "families": dict(families),
            "familyTickerCounts": {k: len(v) for k, v in fam_tickers.items()},
            "checkpoints": dict(checkpoints),
            "marketStatus": dict(status),
            "distinctCaptureMinutes": len(capture_ts),
            "captureMinuteFirst": min(capture_ts) if capture_ts else None,
            "captureMinuteLast": max(capture_ts) if capture_ts else None,
            "fieldCoverage": {
                "yesBid": has_yes_bid,
                "yesAsk": has_yes_ask,
                "noBid": has_no_bid,
                "noAsk": has_no_ask,
                "lastPrice": has_last,
                "volume": has_vol,
                "openInterest": has_oi,
                "spreadCents": has_spread,
            },
            "medianObsPerTicker": (
                sorted(obs_per_ticker.values())[len(obs_per_ticker) // 2]
                if obs_per_ticker
                else 0
            ),
            "maxObsPerTicker": max(obs_per_ticker.values()) if obs_per_ticker else 0,
        }
    return per_date


def audit_settlements():
    per_date = {}
    all_settled = {}
    for date, path in partition_dates("settlements").items():
        n = 0
        results = Counter()
        statuses = Counter()
        tickers = set()
        fams = Counter()
        for r in iter_jsonl(path):
            n += 1
            results[r.get("result")] += 1
            statuses[r.get("settlementStatus")] += 1
            t = r.get("marketTicker")
            tickers.add(t)
            fams[r.get("marketFamily")] += 1
            if r.get("settlementStatus") == "SETTLED" and r.get("result") in (
                "YES",
                "NO",
            ):
                all_settled[t] = r.get("result")
        per_date[date] = {
            "settlements": n,
            "uniqueTickers": len(tickers),
            "results": dict(results),
            "statuses": dict(statuses),
            "families": dict(fams),
        }
    return per_date, all_settled


def audit_join(all_settled):
    """Per observation date: what fraction of observed tickers have a
    YES/NO settlement anywhere in the settlement store."""
    join = {}
    for date, path in partition_dates("observations").items():
        tickers = set()
        fam_of = {}
        for r in iter_jsonl(path):
            t = r.get("marketTicker")
            tickers.add(t)
            fam_of[t] = r.get("marketFamily")
        settled = {t for t in tickers if t in all_settled}
        missing_by_fam = Counter(fam_of[t] for t in tickers - settled)
        join[date] = {
            "observedTickers": len(tickers),
            "settledTickers": len(settled),
            "settledFraction": round(len(settled) / len(tickers), 4) if tickers else None,
            "unsettledByFamily": dict(missing_by_fam),
        }
    return join


def audit_games():
    per_date = {}
    for date, path in partition_dates("games").items():
        n = 0
        ids = set()
        for r in iter_jsonl(path):
            n += 1
            ids.add(r.get("gameId"))
        per_date[date] = {"rows": n, "uniqueGames": len(ids)}
    return per_date


def audit_model_evaluations():
    per_date = {}
    for date, path in partition_dates("model_evaluations").items():
        n = 0
        with_prob = 0
        fams = Counter()
        for r in iter_jsonl(path):
            n += 1
            if r.get("modelFairProbability") is not None:
                with_prob += 1
            fams[r.get("marketFamily")] += 1
        per_date[date] = {
            "evaluations": n,
            "withModelFairProbability": with_prob,
            "families": dict(fams),
        }
    return per_date


def audit_raw_registry_snapshots():
    per_date = defaultdict(lambda: {"files": 0, "markets": 0})
    for p in sorted(glob.glob(os.path.join(RAW_SNAP, "kalshi_search_*.json"))):
        m = re.search(r"kalshi_search_(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if not m:
            continue
        date = m.group(1)
        per_date[date]["files"] += 1
        try:
            with open(p) as fh:
                d = json.load(fh)
            per_date[date]["markets"] += len(d.get("markets") or [])
        except Exception:
            per_date[date].setdefault("errors", 0)
            per_date[date]["errors"] += 1
    return dict(per_date)


def _clean(obj):
    if isinstance(obj, dict):
        return {("null" if k is None else str(k)): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def sha256_of(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    obs = audit_observations()
    setl, all_settled = audit_settlements()
    join = audit_join(all_settled)
    games = audit_games()
    evals = audit_model_evaluations()
    raw = audit_raw_registry_snapshots()

    manifest = {
        "program": "MLB-ALPHA-0001",
        "generatedBy": "scripts/research/mlb_alpha_0001/audit_alpha_dataset.py",
        "researchOnly": True,
        "sources": {
            "canonicalObservationStore": "data/edgelab/observations/",
            "settlementStore": "data/edgelab/settlements/",
            "gameStore": "data/edgelab/games/",
            "modelEvaluationStore": "data/edgelab/model_evaluations/",
            "rawRegistrySnapshots": "data/kalshi_registry_snapshots/",
        },
        "observationsByDate": obs,
        "settlementsByDate": setl,
        "observationSettlementJoinByDate": join,
        "gamesByDate": games,
        "modelEvaluationsByDate": evals,
        "rawRegistrySnapshotsByDate": raw,
        "settledTickerCount": len(all_settled),
    }
    manifest = _clean(manifest)
    manifest["manifestSha256"] = sha256_of(
        {k: v for k, v in manifest.items() if k != "manifestSha256"}
    )
    out = os.path.join(OUT_DIR, "coverage_manifest.json")
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out)

    # Console summary
    dates = sorted(obs)
    print("observation dates: %s .. %s (%d)" % (dates[0], dates[-1], len(dates)))
    for d in dates:
        j = join[d]
        print(
            "%s obs=%6d tickers=%5d games=%3d settledFrac=%s cpts=%s"
            % (
                d,
                obs[d]["observations"],
                obs[d]["uniqueTickers"],
                obs[d]["uniqueGames"],
                j["settledFraction"],
                ",".join(sorted(k for k in obs[d]["checkpoints"] if k)),
            )
        )


if __name__ == "__main__":
    sys.exit(main())
