"""End-to-end: the real postgame re-ingest path preserves canonical CLV provenance.

Drives scripts/edgelab/ingest_existing_bets.py exactly as
.github/workflows/edgelab-postgame.yml does (it runs immediately after
settle_markets.py), against a temp canonical ledger, and proves:

  D. a second identical re-ingest is a true no-op (byte-identical ledger)
  E. scripts/edgelab/migrate_clv_sign.py has NOTHING to restore afterwards
     -- i.e. the ledger never leaves the re-ingest needing hand repair,
     which is what kept test_clv_convention.py::test_migration_is_idempotent
     red before this fix.

Unit-level coverage of the ownership rule lives in
tests/edgelab/test_legacy_ingest_preserves_canonical_fields.py.
"""
import hashlib
import importlib.util
import json
import os

import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import clv_convention  # noqa: E402


def _load_script(name):
    path = os.path.join(_ROOT, "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest_script = _load_script("ingest_existing_bets.py")

LEDGER = os.path.join("data", "edgelab", "bets", "bets.jsonl")

# One legacy row, and the canonical row the pipeline has since enriched.
LEGACY_ROW = {
    "date": "2026-07-31", "game": "DET@ATH", "market": "F5_ML_Away",
    "ticker": "KXMLBF5-26JUL312140DETATH-DET", "betSize": 4.5,
    "actualEntryPrice": 0.505, "entryTimestamp": "2026-07-31T22:38:09Z",
}


def _seed(tmp_path, canonical_extra):
    """Write the legacy source files plus a canonical ledger row for the
    SAME bet, carrying pipeline-written state the legacy row lacks."""
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    os.makedirs(os.path.dirname(session_bets), exist_ok=True)
    with open(root_bets, "w") as f:
        json.dump([LEGACY_ROW], f)
    with open(session_bets, "w") as f:
        json.dump([], f)

    # Derive the canonical row through the real normalizer so its betId
    # matches what the ingest will compute.
    from lib.edgelab.bets import from_legacy_root_bets_record
    canonical = from_legacy_root_bets_record(dict(LEGACY_ROW), 0, source_file=root_bets)
    canonical.update(canonical_extra)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w") as f:
        f.write(json.dumps(canonical, sort_keys=True) + "\n")
    return root_bets, session_bets, canonical["betId"]


def _run_ingest(monkeypatch, root_bets, session_bets):
    monkeypatch.setattr(sys, "argv", [
        "ingest_existing_bets.py", "--root-bets", root_bets, "--session-bets", session_bets])
    assert ingest_script.main() == 0


def _rows():
    with open(LEDGER) as f:
        return [json.loads(l) for l in f if l.strip()]


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


SETTLED_AND_CLV = {
    "status": "settled", "result": "WIN", "netProfitLoss": 4.41, "returnAmount": 4.41,
    "clv": 3.5, "closingPrice": 0.54, "clvQuoteId": "q-1",
    "clvConvention": clv_convention.CONVENTION_ID,
    "clvUnit": clv_convention.UNIT_PERCENTAGE_POINTS,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "confirmedReceiptReturn": 8.91, "confirmedReceiptNetProfitLoss": 4.41,
}


def test_production_reingest_preserves_clv_provenance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets, session_bets, bet_id = _seed(tmp_path, SETTLED_AND_CLV)
    _run_ingest(monkeypatch, root_bets, session_bets)

    row = next(r for r in _rows() if r["betId"] == bet_id)
    assert row["clv"] == 3.5
    assert row["clvConvention"] == clv_convention.CONVENTION_ID
    assert row["clvUnit"] == clv_convention.UNIT_PERCENTAGE_POINTS
    assert row["status"] == "settled" and row["result"] == "WIN"
    assert row["confirmedReceiptSource"] == "MANUAL_POSTMORTEM_RECEIPT"


def test_D_a_second_identical_reingest_is_byte_identical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root_bets, session_bets, _ = _seed(tmp_path, SETTLED_AND_CLV)

    _run_ingest(monkeypatch, root_bets, session_bets)
    first = _sha(LEDGER)
    _run_ingest(monkeypatch, root_bets, session_bets)
    second = _sha(LEDGER)

    assert first == second, "a repeated identical re-ingest changed the canonical ledger"


def test_E_clv_migration_has_nothing_to_restore_after_a_reingest(tmp_path, monkeypatch):
    """The migration is the sanctioned repair tool. After this fix it must
    have NOTHING to do following a re-ingest -- it must never be the thing
    that puts canonical provenance back.

    Uses migrate_clv_sign's own classify() and its own write condition
    rather than shelling out to it: that script resolves LEDGER/MANIFEST
    from the repo root and ignores cwd, so invoking it here would grade
    the real repository ledger (and rewrite the frozen migration
    evidence) instead of this fixture.
    """
    monkeypatch.chdir(tmp_path)
    root_bets, session_bets, _ = _seed(tmp_path, SETTLED_AND_CLV)
    _run_ingest(monkeypatch, root_bets, session_bets)

    migrate = _load_script("migrate_clv_sign.py")
    would_change = []
    for row in _rows():
        cls, recomputed, _reason = migrate.classify(row)
        if cls not in (migrate.RECOMPUTED, migrate.ZERO, migrate.ALREADY) or recomputed is None:
            continue
        new_row = dict(row)
        new_row["clv"] = recomputed
        new_row["clvConvention"] = clv_convention.CONVENTION_ID
        new_row["clvUnit"] = clv_convention.UNIT_PERCENTAGE_POINTS
        if json.dumps(new_row, sort_keys=True) != json.dumps(row, sort_keys=True):
            would_change.append((row["betId"], sorted(
                k for k in set(new_row) | set(row) if new_row.get(k) != row.get(k))))

    assert would_change == [], (
        "the CLV migration would still have to repair rows after a re-ingest: "
        f"{would_change} -- the re-ingest is dropping canonical CLV provenance")


def test_legacy_row_may_still_settle_a_row_with_no_canonical_outcome(tmp_path, monkeypatch):
    """Guard the other direction: the preservation rule must not stop a
    legacy ledger being the settlement source for bets nothing else can
    reach."""
    monkeypatch.chdir(tmp_path)
    legacy = dict(LEGACY_ROW, result="WIN", pl=4.41)
    root_bets = str(tmp_path / "bets.json")
    session_bets = str(tmp_path / "data" / "bets.json")
    os.makedirs(os.path.dirname(session_bets), exist_ok=True)
    with open(root_bets, "w") as f:
        json.dump([legacy], f)
    with open(session_bets, "w") as f:
        json.dump([], f)

    from lib.edgelab.bets import from_legacy_root_bets_record
    canonical = from_legacy_root_bets_record(dict(LEGACY_ROW), 0, source_file=root_bets)
    canonical.update({"status": "pending", "result": None, "netProfitLoss": None,
                      "clv": 3.5, "clvConvention": clv_convention.CONVENTION_ID,
                      "clvUnit": clv_convention.UNIT_PERCENTAGE_POINTS})
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w") as f:
        f.write(json.dumps(canonical, sort_keys=True) + "\n")

    _run_ingest(monkeypatch, root_bets, session_bets)
    row = next(r for r in _rows() if r["betId"] == canonical["betId"])
    assert row["result"] == "WIN" and row["status"] == "settled"
    assert row["netProfitLoss"] == 4.41
    assert row["clvConvention"] == clv_convention.CONVENTION_ID
    assert row["clvUnit"] == clv_convention.UNIT_PERCENTAGE_POINTS
