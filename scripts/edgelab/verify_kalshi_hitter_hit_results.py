#!/usr/bin/env python3
"""Read-only verification of tracked MLB hitter-hit results against official Kalshi history.

Requires an exact-ticker audit manifest from audit_hitter_hit_coverage.py.
Every tracked ticker is retained, even if the exchange query cannot establish
an official result. It NEVER edits canonical EdgeLab settlement/model ledgers.

Usage:
  python scripts/edgelab/verify_kalshi_hitter_hit_results.py \
    --manifest artifacts/hitter_hit_coverage_aug_01_20.json \
    --out artifacts/hitter_hit_exchange_reconciliation.json
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://external-api.kalshi.com/trade-api/v2/historical/markets"
TERMINAL = frozenset(("finalized", "settled"))
YES_NO = frozenset(("yes", "no"))


def _fetch_batch(tickers, *, request_fetch=None):
    """One bounded historical-market batch, with bounded pagination/retries.

    A caller may supply request_fetch(url) returning raw bytes for tests.
    """
    requested = set(tickers)
    if not 1 <= len(requested) <= 50 or len(requested) != len(tickers):
        raise ValueError("Historical request requires 1-50 distinct tickers")
    found = {}
    proof = []
    cursor = None
    for page in range(4):
        query = {"tickers": ",".join(sorted(tickers)), "limit": 1000}
        if cursor:
            query["cursor"] = cursor
        url = BASE + "?" + urlencode(query)
        for attempt in range(3):
            try:
                if request_fetch is not None:
                    payload = request_fetch(url)
                else:
                    req = Request(url, headers={"User-Agent": "SIFT-MLB-Settlement-Audit/1"})
                    with urlopen(req, timeout=25) as response:
                        if response.geturl() != url:
                            raise ValueError("Historical source redirected")
                        payload = response.read(4000001)
                if not isinstance(payload, bytes) or not 0 < len(payload) <= 4000000:
                    raise ValueError("Invalid/oversized Kalshi response")
                doc = json.loads(payload)
                if not isinstance(doc, dict) or not isinstance(doc.get("markets"), list):
                    raise ValueError("Kalshi response lacks markets array")
                break
            except (HTTPError, URLError, TimeoutError) as exc:
                if request_fetch is not None or (isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504)) or attempt == 2:
                    raise
                time.sleep(1 + 2 * attempt)
        proof.append({"sha256": hashlib.sha256(payload).hexdigest(), "url": url, "bytes": len(payload)})
        for market in doc["markets"]:
            if not isinstance(market, dict):
                raise ValueError("Non-object official market")
            ticker = market.get("ticker")
            if ticker not in requested:
                raise ValueError("Official response returned unrequested ticker")
            if ticker in found and found[ticker] != market:
                raise ValueError("Contradictory pages for ticker " + ticker)
            found[ticker] = market
        next_cursor = doc.get("cursor") or None
        if not next_cursor:
            return found, proof
        if next_cursor == cursor or not isinstance(next_cursor, str):
            raise ValueError("Invalid repeated Kalshi page cursor")
        cursor = next_cursor
    raise ValueError("Official history pagination exceeds bounded limit")


def classify(entry, market):
    """Preserve exchange-state uncertainty; never map scalar/DNP to binary NO."""
    ticker = entry["marketTicker"]
    recorded = entry.get("recordedResult")
    base = {
        "marketTicker": ticker,
        "checkpointRows": entry["checkpointRows"],
        "recordedStatus": entry["settlementStatus"],
        "recordedResult": recorded,
        "officialResult": None,
        "officialStatus": None,
        "exchangeClassification": None,
        "exchangeRulesPrimary": None,
        "exchangeRulesSecondary": None,
        "exchangeSettledAt": None,
        "mismatch": False,
    }
    if market is None:
        base["exchangeClassification"] = "EXCHANGE_MARKET_NOT_RETURNED"
        return base
    if market.get("ticker") != ticker:
        raise ValueError("Mismatched returned Kalshi ticker")
    status = str(market.get("status") or "").lower()
    result = str(market.get("result") or "").lower()
    base.update({
        "officialStatus": status or None, "officialResult": result or None,
        "exchangeRulesPrimary": market.get("rules_primary"),
        "exchangeRulesSecondary": market.get("rules_secondary"),
        "exchangeSettledAt": market.get("settlement_ts"),
    })
    if status not in TERMINAL:
        base["exchangeClassification"] = "EXCHANGE_NOT_FINAL"
    elif result in YES_NO:
        base["exchangeClassification"] = "EXCHANGE_CONFIRMED_" + result.upper()
        base["mismatch"] = recorded in ("YES", "NO") and recorded != result.upper()
    elif result == "scalar":
        base["exchangeClassification"] = "EXCHANGE_NON_BINARY_SCALAR"
    else:
        base["exchangeClassification"] = "EXCHANGE_NON_BINARY_OR_MISSING_RESULT"
    return base


def verify_manifest(manifest, fetch_batch=_fetch_batch, batch_size=50, max_tickers=25000):
    if manifest.get("family") != "hitter_hits" or manifest.get("authority") != "READ_ONLY_RESEARCH_DIAGNOSTIC_NO_KALSHI_SETTLEMENT_VERIFICATION":
        raise ValueError("Source must be a verified read-only hitter-hit audit manifest")
    entries = manifest.get("allTrackedTickers")
    if not isinstance(entries, list) or len(entries) != manifest.get("uniqueMarketTickers"):
        raise ValueError("Missing or inconsistent full ticker inventory")
    tickers = [row.get("marketTicker") for row in entries]
    if not all(isinstance(t, str) and t.startswith("KXMLBHIT-") for t in tickers):
        raise ValueError("Unexpected MLB hitter-hit ticker identity; fail closed")
    if len(set(tickers)) != len(tickers) or not 1 <= len(tickers) <= max_tickers:
        raise ValueError("Duplicate/empty/over-budget market inventory")
    if not 1 <= batch_size <= 50:
        raise ValueError("Batch size outside bounded 1..50 limit")

    fetched, provenance = {}, []
    for offset in range(0, len(tickers), batch_size):
        batch = tickers[offset:offset + batch_size]
        markets, proof = fetch_batch(batch)
        if not set(markets).issubset(set(batch)):
            raise ValueError("Official history returned an unrequested market")
        fetched.update(markets)
        provenance.extend(proof)
    results = [classify(row, fetched.get(row["marketTicker"])) for row in entries]
    counts = Counter(r["exchangeClassification"] for r in results)
    total = len(results)
    assert sum(counts.values()) == total
    conflicts = [r for r in results if r["mismatch"]]
    recorded_settled_without_official_binary = [
        r for r in results
        if r["recordedStatus"] == "SETTLED"
        and not r["exchangeClassification"].startswith("EXCHANGE_CONFIRMED_")
    ]
    official_binary_missing_from_recorded = [
        r for r in results
        if r["exchangeClassification"].startswith("EXCHANGE_CONFIRMED_")
        and r["recordedStatus"] != "SETTLED"
    ]
    return {
        "schemaVersion": 1,
        "authority": "READ_ONLY_OFFICIAL_KALSHI_HISTORY_RECONCILIATION",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "marketFamily": "hitter_hits",
        "sourceReportGeneratedAt": manifest.get("generatedAt"),
        "uniqueTrackedTickers": total,
        "checkpointRows": manifest.get("checkpointRows"),
        "exchangeClassificationCounts": dict(sorted(counts.items())),
        "officialBinaryConfirmedTickers": sum(v for k, v in counts.items() if k.startswith("EXCHANGE_CONFIRMED_")),
        "recordedResultConflicts": len(conflicts),
        "recordedSettledWithoutOfficialBinary": len(recorded_settled_without_official_binary),
        "officialBinaryMissingFromRecorded": len(official_binary_missing_from_recorded),
        "recordedSettledWithoutOfficialBinaryTickers": [r["marketTicker"] for r in recorded_settled_without_official_binary],
        "officialBinaryMissingFromRecordedTickers": [r["marketTicker"] for r in official_binary_missing_from_recorded],
        "source": "https://external-api.kalshi.com/trade-api/v2/historical/markets",
        "sourcePageEvidence": provenance,
        "byTicker": results,
        "caveats": [
            "No canonical EdgeLab settlement was changed by this research audit.",
            "Official exchange result confirms payout, not a historically available model forecast.",
            "Missing/void/scalar cannot be inferred as a binary NO or counted as model wins.",
            "Each marketTicker occurs once even if it has many archived price checkpoints.",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--max-tickers", type=int, default=25000)
    a = p.parse_args()
    source = Path(a.manifest)
    manifest = json.loads(source.read_text(encoding="utf-8"))
    result = verify_manifest(manifest, batch_size=a.batch_size, max_tickers=a.max_tickers)
    result["sourceManifestSha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    output = Path(a.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "trackedTickers": result["uniqueTrackedTickers"],
        "officialBinaryConfirmed": result["officialBinaryConfirmedTickers"],
        "conflicts": result["recordedResultConflicts"],
        "recordedSettledWithoutOfficialBinary": result["recordedSettledWithoutOfficialBinary"],
        "officialBinaryMissingFromRecorded": result["officialBinaryMissingFromRecorded"],
        "classifications": result["exchangeClassificationCounts"],
    }, indent=2))


if __name__ == "__main__":
    main()
