#!/usr/bin/env python3
"""
tests/edgelab/test_generate_postmortem_era_filtering.py
============================================================
Coverage for scripts/edgelab/generate_postmortem.py's canonical-era
filtering: by default the postmortem's bet list and bankroll figures
only ever count bets on or after canonical_era.CANONICAL_ERA_START_DATE
(2026-08-03); --include-legacy is the only way to see the full-history
view, and it's always labelled explicitly in the output.
"""
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage

_spec = importlib.util.spec_from_file_location(
    "generate_postmortem",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "generate_postmortem.py"),
)
generate_postmortem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_postmortem)


def _bet(bet_id, game_date, *, stake=10.0, status="settled", result="WIN", net_pl=9.0):
    return {
        "betId": bet_id, "gameDate": game_date, "entryTimestamp": f"{game_date}T18:00:00Z",
        "stake": stake, "status": status, "result": result, "netProfitLoss": net_pl,
        "clv": None, "marketFamily": "ML", "recordStatus": "ACTIVE", "trackingType": "REAL",
    }


@pytest.fixture(autouse=True)
def _default_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py"])


def _setup(tmp_path, monkeypatch, bets):
    bets_path = str(tmp_path / "bets.jsonl")
    txn_path = str(tmp_path / "transactions.jsonl")
    storage.write_all_records(bets_path, bets)
    storage.write_all_records(txn_path, [])

    def fake_singleton_path(entity, filename):
        return bets_path if entity == "bets" else txn_path

    monkeypatch.setattr(storage, "singleton_path", fake_singleton_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_default_run_excludes_legacy_bets_from_the_target_dates_postmortem(tmp_path, monkeypatch):
    bets = [_bet("legacy-1", "2026-07-15", stake=100.0, result="LOSS", net_pl=-100.0)]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py", "--date", "2026-07-15"])

    generate_postmortem.main()

    report = json.loads((tmp_path / "data" / "edgelab" / "reports" / "2026-07-15_postmortem.json").read_text())
    assert report["betsPlaced"] == 0
    assert report["legacyIncluded"] is False
    assert report["canonicalEraStartDate"] == "2026-08-03"


def test_include_legacy_flag_restores_legacy_bets(tmp_path, monkeypatch):
    bets = [_bet("legacy-1", "2026-07-15", stake=100.0, result="LOSS", net_pl=-100.0)]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py", "--date", "2026-07-15", "--include-legacy"])

    generate_postmortem.main()

    report = json.loads((tmp_path / "data" / "edgelab" / "reports" / "2026-07-15_postmortem.json").read_text())
    assert report["betsPlaced"] == 1
    assert report["legacyIncluded"] is True


def test_era_date_postmortem_is_unaffected_by_default_filtering(tmp_path, monkeypatch):
    bets = [_bet("era-1", "2026-08-03", stake=20.0, result="WIN", net_pl=18.0)]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py", "--date", "2026-08-03"])

    generate_postmortem.main()

    report = json.loads((tmp_path / "data" / "edgelab" / "reports" / "2026-08-03_postmortem.json").read_text())
    assert report["betsPlaced"] == 1
    assert report["totalNetProfitLoss"] == 18.0


def test_legacy_bets_never_inflate_bankroll_for_an_era_date_report(tmp_path, monkeypatch):
    """A legacy pending bet's stake must not show up as exposure in an era-date postmortem's bankroll."""
    bets = [
        _bet("legacy-pending", "2026-07-15", stake=500.0, status="pending", result=None, net_pl=None),
        _bet("era-1", "2026-08-03", stake=20.0, result="WIN", net_pl=18.0),
    ]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py", "--date", "2026-08-03"])

    generate_postmortem.main()

    report = json.loads((tmp_path / "data" / "edgelab" / "reports" / "2026-08-03_postmortem.json").read_text())
    assert report["bankroll"]["totalExposure"] == 0.0


def test_does_not_modify_the_source_bets_file(tmp_path, monkeypatch):
    bets = [_bet("legacy-1", "2026-07-15"), _bet("era-1", "2026-08-03")]
    _setup(tmp_path, monkeypatch, bets)
    before = (tmp_path / "bets.jsonl").read_bytes()

    monkeypatch.setattr(sys, "argv", ["generate_postmortem.py", "--date", "2026-08-03"])
    generate_postmortem.main()

    after = (tmp_path / "bets.jsonl").read_bytes()
    assert before == after
