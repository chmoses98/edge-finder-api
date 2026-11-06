"""
lib/edgelab/official_result_evidence.py
=======================================
Pure helpers that turn a verified official-exchange reconciliation (scripts/edgelab/verify_kalshi_hitter_hit_results.py)
into append-only, idempotent EVIDENCE records -- never settlements.

Guarantees (each is tested):
  * Evidence is stored in its own entity (official_result_evidence). Canonical Settlement / PlacedBet / ModelEvaluation
    records are never read for writing, modified or "upgraded" here; an unresolved settlement stays unresolved.
  * Only an official binary YES/NO from a terminal market becomes `officialResult`. Scalar, void-like, non-final, missing
    or not-returned markets keep officialResult=None -- never inferred as NO.
  * Each record carries provenance: exact ticker, official status/rules hashes, and the sha256+url of every response
    page that was requested for the batch containing the ticker.
  * Idempotent: records contain no wall-clock fields, so rerunning on identical evidence rewrites identical bytes.
  * A later contradictory official result for the same ticker is QUARANTINED (returned as a conflict, existing evidence
    kept); an official binary is never silently replaced, and a binary is never regressed to non-binary.
"""
import hashlib
import re
from urllib.parse import parse_qs, urlparse

ENTITY = "official_result_evidence"
CONFLICT_ENTITY = "official_result_evidence_conflicts"
AUTHORITY = "OFFICIAL_EXCHANGE_EVIDENCE_NOT_A_SETTLEMENT"

A_CONFIRMS = "CONFIRMS_RECORDED_SETTLEMENT"
A_EVIDENCE_ONLY = "OFFICIAL_BINARY_RECORDED_UNSETTLED_EVIDENCE_ONLY"
A_CONFLICT = "CONFLICTS_WITH_RECORDED_SETTLEMENT_QUARANTINED"
A_UNVERIFIED = "RECORDED_SETTLED_WITHOUT_OFFICIAL_BINARY"
A_NONBINARY = "OFFICIAL_NOT_BINARY"
A_NOT_FINAL = "OFFICIAL_NOT_FINAL"
A_NOT_RETURNED = "OFFICIAL_NOT_RETURNED"
A_NO_RECORD_NO_OFFICIAL = "NO_RECORDED_SETTLEMENT_NO_OFFICIAL_BINARY"

_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_EVENT = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})")


def evidence_id(ticker):
    return hashlib.sha1(f"{ENTITY}/v1:{ticker}".encode()).hexdigest()


def game_date_from_ticker(ticker):
    """YYYY-MM-DD encoded in the exact ticker (partition key), or None."""
    m = _EVENT.match(ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    return f"{2000 + int(m.group(1)):04d}-{_MONTHS[m.group(2)]:02d}-{int(m.group(3)):02d}"


def _text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest() if isinstance(value, str) and value else None


def _proofs_by_ticker(page_evidence):
    out = {}
    for page in page_evidence or []:
        url = page.get("url") or ""
        for chunk in parse_qs(urlparse(url).query).get("tickers", []):
            for ticker in chunk.split(","):
                out.setdefault(ticker, []).append({"sha256": page.get("sha256"), "url": url})
    return out


def _action(entry):
    cls = entry.get("exchangeClassification") or ""
    recorded_status, recorded = entry.get("recordedStatus"), entry.get("recordedResult")
    if cls.startswith("EXCHANGE_CONFIRMED_"):
        official = cls.rsplit("_", 1)[1]
        if recorded_status == "SETTLED" and recorded in ("YES", "NO"):
            return A_CONFIRMS if recorded == official else A_CONFLICT
        return A_EVIDENCE_ONLY
    if recorded_status == "SETTLED":
        return A_UNVERIFIED
    return {"EXCHANGE_NON_BINARY_SCALAR": A_NONBINARY, "EXCHANGE_NON_BINARY_OR_MISSING_RESULT": A_NONBINARY,
            "EXCHANGE_NOT_FINAL": A_NOT_FINAL, "EXCHANGE_MARKET_NOT_RETURNED": A_NOT_RETURNED}.get(cls, A_NO_RECORD_NO_OFFICIAL)


def build_evidence_records(reconciliation):
    """reconciliation: output of verify_kalshi_hitter_hit_results.verify_manifest. Returns (records, summary)."""
    if reconciliation.get("authority") != "READ_ONLY_OFFICIAL_KALSHI_HISTORY_RECONCILIATION":
        raise ValueError("Source must be a verified official-history reconciliation")
    rows = reconciliation.get("byTicker")
    if not isinstance(rows, list) or len(rows) != reconciliation.get("uniqueTrackedTickers"):
        raise ValueError("Missing or inconsistent per-ticker inventory")
    proofs = _proofs_by_ticker(reconciliation.get("sourcePageEvidence"))
    records, seen = [], set()
    for entry in rows:
        ticker = entry.get("marketTicker")
        if not isinstance(ticker, str) or not ticker or ticker in seen:
            raise ValueError("Missing or duplicate marketTicker in reconciliation")
        seen.add(ticker)
        game_date = game_date_from_ticker(ticker)
        if game_date is None:
            raise ValueError("Cannot derive game date from ticker " + ticker)
        cls = entry.get("exchangeClassification")
        official = cls.rsplit("_", 1)[1] if (cls or "").startswith("EXCHANGE_CONFIRMED_") else None
        records.append({
            "evidenceId": evidence_id(ticker),
            "authority": AUTHORITY,
            "marketTicker": ticker,
            "marketFamily": reconciliation.get("marketFamily"),
            "gameDate": game_date,
            "exchangeClassification": cls,
            "officialStatus": entry.get("officialStatus"),
            "officialResult": official,
            "exchangeSettledAt": entry.get("exchangeSettledAt"),
            "exchangeRulesPrimarySha256": _text_hash(entry.get("exchangeRulesPrimary")),
            "exchangeRulesSecondarySha256": _text_hash(entry.get("exchangeRulesSecondary")),
            "recordedStatus": entry.get("recordedStatus"),
            "recordedResult": entry.get("recordedResult"),
            "reconciliationAction": _action(entry),
            "settlementOverwritten": False,
            "source": reconciliation.get("source"),
            "sourceResponses": sorted(proofs.get(ticker, []), key=lambda p: (p.get("url") or "", p.get("sha256") or "")),
        })
    records.sort(key=lambda r: r["marketTicker"])
    counts = {}
    for r in records:
        counts[r["reconciliationAction"]] = counts.get(r["reconciliationAction"], 0) + 1
    return records, {"tickers": len(records), "byAction": dict(sorted(counts.items()))}


def merge_evidence(existing, incoming):
    """
    existing/incoming: lists of evidence records (same entity). Returns
    (merged_records, conflicts, stats). Never replaces an official binary with a different binary or with a
    non-binary; a contradiction is returned in `conflicts` and the existing record is kept.
    """
    merged = {r["evidenceId"]: r for r in existing}
    conflicts, stats = [], {"inserted": 0, "unchanged": 0, "upgraded": 0, "conflictsQuarantined": 0, "regressionsPrevented": 0}
    for new in incoming:
        old = merged.get(new["evidenceId"])
        if old is None:
            merged[new["evidenceId"]] = new
            stats["inserted"] += 1
        elif old == new:
            stats["unchanged"] += 1
        elif old.get("officialResult") and new.get("officialResult") and old["officialResult"] != new["officialResult"]:
            conflicts.append({"conflictId": evidence_id(new["marketTicker"] + "|" + str(old["officialResult"]) + "|" + str(new["officialResult"])),
                              "marketTicker": new["marketTicker"], "gameDate": new["gameDate"],
                              "existingOfficialResult": old["officialResult"], "incomingOfficialResult": new["officialResult"],
                              "existingEvidenceId": old["evidenceId"], "incomingSourceResponses": new["sourceResponses"],
                              "resolution": "QUARANTINED_EXISTING_EVIDENCE_KEPT"})
            stats["conflictsQuarantined"] += 1
        elif old.get("officialResult") and not new.get("officialResult"):
            stats["regressionsPrevented"] += 1
        else:
            # old had no official binary (not final / not returned / scalar) and the new one adds information, or the
            # same binary was re-confirmed with refreshed provenance/recorded-state fields
            merged[new["evidenceId"]] = new
            stats["upgraded"] += 1
    return sorted(merged.values(), key=lambda r: r["marketTicker"]), conflicts, stats
