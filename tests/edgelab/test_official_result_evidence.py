"""#309 official-result evidence: never a settlement, never inferred, idempotent, quarantine on contradiction."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from lib.edgelab import official_result_evidence as E
from lib.edgelab import storage
from scripts.edgelab.reconcile_official_hitter_results import reconcile

T1 = "KXMLBHIT-26AUG021920BOSLAD-LADPLAYER1-1"
T2 = "KXMLBHIT-26AUG021920BOSLAD-LADPLAYER2-1"
T3 = "KXMLBHIT-26AUG021920BOSLAD-LADPLAYER3-1"
T4 = "KXMLBHIT-26AUG031920BOSLAD-LADPLAYER4-1"
T5 = "KXMLBHIT-26AUG031920BOSLAD-LADPLAYER5-1"
T6 = "KXMLBHIT-26AUG031920BOSLAD-LADPLAYER6-1"


def entry(ticker, cls, official_status="finalized", official_result=None, recorded_status="NOT_SETTLED", recorded=None):
    return {"marketTicker": ticker, "checkpointRows": 2, "recordedStatus": recorded_status, "recordedResult": recorded,
            "officialResult": official_result, "officialStatus": official_status, "exchangeClassification": cls,
            "exchangeRulesPrimary": "Resolves Yes if the player records 1+ hits.", "exchangeRulesSecondary": None,
            "exchangeSettledAt": "2026-08-03T03:00:00Z", "mismatch": False}


def recon(entries, pages=None):
    pages = pages if pages is not None else [{"sha256": "ab" * 32, "url": "https://x/historical/markets?tickers=" + ",".join(sorted(e["marketTicker"] for e in entries)) + "&limit=1000", "bytes": 10}]
    return {"authority": "READ_ONLY_OFFICIAL_KALSHI_HISTORY_RECONCILIATION", "marketFamily": "hitter_hits",
            "uniqueTrackedTickers": len(entries), "byTicker": entries, "sourcePageEvidence": pages,
            "source": "https://external-api.kalshi.com/trade-api/v2/historical/markets"}


FULL = [
    entry(T1, "EXCHANGE_CONFIRMED_YES", "finalized", "yes", "SETTLED", "YES"),          # confirms
    entry(T2, "EXCHANGE_CONFIRMED_NO", "finalized", "no", "SETTLEMENT_UNRESOLVED"),      # evidence only
    entry(T3, "EXCHANGE_CONFIRMED_YES", "finalized", "yes", "SETTLED", "NO"),            # conflict
    entry(T4, "EXCHANGE_NON_BINARY_SCALAR", "finalized", "scalar"),                      # never NO
    entry(T5, "EXCHANGE_NOT_FINAL", "active"),
    entry(T6, "EXCHANGE_MARKET_NOT_RETURNED", None, None, "SETTLED", "YES"),             # recorded settled, unverified
]


def test_actions_and_no_inference():
    recs, summary = E.build_evidence_records(recon(FULL))
    by = {r["marketTicker"]: r for r in recs}
    assert by[T1]["reconciliationAction"] == E.A_CONFIRMS and by[T1]["officialResult"] == "YES"
    assert by[T2]["reconciliationAction"] == E.A_EVIDENCE_ONLY and by[T2]["officialResult"] == "NO"
    assert by[T3]["reconciliationAction"] == E.A_CONFLICT
    assert by[T4]["reconciliationAction"] == E.A_NONBINARY and by[T4]["officialResult"] is None   # scalar is never NO
    assert by[T5]["reconciliationAction"] == E.A_NOT_FINAL and by[T5]["officialResult"] is None
    assert by[T6]["reconciliationAction"] == E.A_UNVERIFIED and by[T6]["officialResult"] is None
    assert all(r["settlementOverwritten"] is False and r["authority"] == E.AUTHORITY for r in recs)
    assert sum(summary["byAction"].values()) == 6


def test_records_carry_provenance_and_partition_date():
    recs, _ = E.build_evidence_records(recon(FULL))
    r = next(x for x in recs if x["marketTicker"] == T1)
    assert r["gameDate"] == "2026-08-02" and r["sourceResponses"][0]["sha256"] == "ab" * 32
    assert len(r["exchangeRulesPrimarySha256"]) == 64 and "tickers=" in r["sourceResponses"][0]["url"]


def test_malformed_reconciliation_fails_closed():
    good = recon(FULL)
    with pytest.raises(ValueError):
        E.build_evidence_records({**good, "authority": "something else"})
    with pytest.raises(ValueError):
        E.build_evidence_records({**good, "uniqueTrackedTickers": 99})
    dup = recon([FULL[0], FULL[0]])
    with pytest.raises(ValueError):
        E.build_evidence_records(dup)
    with pytest.raises(ValueError):
        E.build_evidence_records(recon([entry("BADTICKER", "EXCHANGE_NOT_FINAL", "active")]))


def test_merge_is_idempotent_and_deterministic():
    recs, _ = E.build_evidence_records(recon(FULL))
    merged, conflicts, stats = E.merge_evidence([], recs)
    assert stats["inserted"] == 6 and not conflicts
    again, conflicts2, stats2 = E.merge_evidence(merged, recs)
    assert again == merged and stats2["unchanged"] == 6 and not conflicts2
    shuffled, _, _ = E.merge_evidence([], list(reversed(recs)))
    assert shuffled == merged


def test_contradictory_official_result_is_quarantined_not_overwritten():
    first, _ = E.build_evidence_records(recon([entry(T1, "EXCHANGE_CONFIRMED_YES", "finalized", "yes")]))
    flipped, _ = E.build_evidence_records(recon([entry(T1, "EXCHANGE_CONFIRMED_NO", "finalized", "no")]))
    merged, conflicts, stats = E.merge_evidence(first, flipped)
    assert merged == first and stats["conflictsQuarantined"] == 1
    assert conflicts[0]["existingOfficialResult"] == "YES" and conflicts[0]["incomingOfficialResult"] == "NO"
    assert conflicts[0]["resolution"] == "QUARANTINED_EXISTING_EVIDENCE_KEPT"


def test_binary_is_never_regressed_and_non_final_can_upgrade():
    binary, _ = E.build_evidence_records(recon([entry(T1, "EXCHANGE_CONFIRMED_YES", "finalized", "yes")]))
    nonfinal, _ = E.build_evidence_records(recon([entry(T1, "EXCHANGE_NOT_FINAL", "active")]))
    kept, conflicts, stats = E.merge_evidence(binary, nonfinal)
    assert kept == binary and stats["regressionsPrevented"] == 1 and not conflicts
    upgraded, _, stats2 = E.merge_evidence(nonfinal, binary)
    assert upgraded == binary and stats2["upgraded"] == 1


def test_cli_dry_run_writes_nothing_apply_is_idempotent_and_settlements_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settlements_path = storage.partition_path("settlements", "2026-08-02")
    storage.write_all_records(settlements_path, [{"settlementId": "s1", "marketTicker": T2, "settlementStatus": "SETTLEMENT_UNRESOLVED", "result": None}])
    before = open(settlements_path, "rb").read()
    reconciliation = recon(FULL)

    dry = reconcile(reconciliation, apply=False)
    assert dry["dryRun"] is True and not os.path.exists(storage.partition_path(E.ENTITY, "2026-08-02"))
    assert not os.path.exists(os.path.dirname(storage.partition_path(E.ENTITY, "2026-08-02")))   # no dir, no lock file

    applied = reconcile(reconciliation, apply=True)
    assert applied["merge"]["inserted"] == 6 and applied["settlementsModified"] == 0
    p1, p2 = storage.partition_path(E.ENTITY, "2026-08-02"), storage.partition_path(E.ENTITY, "2026-08-03")
    snap = {p: open(p, "rb").read() for p in (p1, p2)}
    assert len(list(storage.read_records(p1))) == 3 and len(list(storage.read_records(p2))) == 3

    again = reconcile(reconciliation, apply=True)
    assert again["merge"]["unchanged"] == 6 and again["merge"]["inserted"] == 0
    assert {p: open(p, "rb").read() for p in (p1, p2)} == snap          # byte-identical rerun
    assert open(settlements_path, "rb").read() == before                  # unresolved settlement untouched


def test_apply_quarantines_a_later_contradiction_in_its_own_partition(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reconcile(recon([entry(T1, "EXCHANGE_CONFIRMED_YES", "finalized", "yes")]), apply=True)
    out = reconcile(recon([entry(T1, "EXCHANGE_CONFIRMED_NO", "finalized", "no")]), apply=True)
    assert out["merge"]["conflictsQuarantined"] == 1
    kept = list(storage.read_records(storage.partition_path(E.ENTITY, "2026-08-02")))
    assert kept[0]["officialResult"] == "YES"
    conflicts = list(storage.read_records(storage.partition_path(E.CONFLICT_ENTITY, "2026-08-02")))
    assert len(conflicts) == 1
    reconcile(recon([entry(T1, "EXCHANGE_CONFIRMED_NO", "finalized", "no")]), apply=True)   # rerun: no duplicate conflict
    assert len(list(storage.read_records(storage.partition_path(E.CONFLICT_ENTITY, "2026-08-02")))) == 1
