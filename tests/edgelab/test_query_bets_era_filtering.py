#!/usr/bin/env python3
"""
tests/edgelab/test_query_bets_era_filtering.py
===================================================
Coverage for scripts/edgelab/query_bets.py's canonical-era filtering on
the two aggregate filters (bankroll-history, canonical-era-summary):
legacy bets are excluded by default, --include-legacy restores them and
labels the result accordingly, and every other (non-aggregate) filter
remains an unfiltered historical lookup -- legacy rows stay queryable.
"""
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage

_spec = importlib.util.spec_from_file_location(
    "query_bets",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "query_bets.py"),
)
query_bets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(query_bets)


def _bet(bet_id, game_date, *, stake=10.0, status="settled", result="WIN", net_pl=9.0):
    return {
        "betId": bet_id, "gameDate": game_date, "entryTimestamp": f"{game_date}T18:00:00Z",
        "stake": stake, "status": status, "result": result, "netProfitLoss": net_pl,
        "clv": None, "marketFamily": "ML", "recordStatus": "ACTIVE", "trackingType": "REAL",
    }


@pytest.fixture(autouse=True)
def _default_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "bankroll-history"])


def _setup(tmp_path, monkeypatch, bets, transactions=()):
    bets_path = str(tmp_path / "bets.jsonl")
    txn_path = str(tmp_path / "transactions.jsonl")
    storage.write_all_records(bets_path, bets)
    storage.write_all_records(txn_path, list(transactions))

    def fake_singleton_path(entity, filename):
        return bets_path if entity == "bets" else txn_path

    monkeypatch.setattr(storage, "singleton_path", fake_singleton_path)


def test_bankroll_history_excludes_legacy_bets_by_default(tmp_path, monkeypatch, capsys):
    bets = [
        _bet("legacy-pending", "2026-07-15", stake=500.0, status="pending", result=None, net_pl=None),
        _bet("era-1", "2026-08-03", stake=20.0, result="WIN", net_pl=18.0),
    ]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "bankroll-history"])

    query_bets.main()
    result = json.loads(capsys.readouterr().out)

    assert result["legacyIncluded"] is False
    assert result["canonicalEraStartDate"] == "2026-08-03"
    assert result["summary"]["totalExposure"] == 0.0


def test_bankroll_history_include_legacy_restores_full_history(tmp_path, monkeypatch, capsys):
    bets = [
        _bet("legacy-pending", "2026-07-15", stake=500.0, status="pending", result=None, net_pl=None),
        _bet("era-1", "2026-08-03", stake=20.0, result="WIN", net_pl=18.0),
    ]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "bankroll-history", "--include-legacy"])

    query_bets.main()
    result = json.loads(capsys.readouterr().out)

    assert result["legacyIncluded"] is True
    assert result["summary"]["totalExposure"] == 500.0


def test_canonical_era_summary_filter_excludes_legacy_by_default(tmp_path, monkeypatch, capsys):
    bets = [
        _bet("legacy-1", "2026-06-18", stake=100.0, result="LOSS", net_pl=-100.0),
        _bet("era-1", "2026-08-03", stake=10.0, result="WIN", net_pl=9.0),
    ]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "canonical-era-summary"])

    query_bets.main()
    result = json.loads(capsys.readouterr().out)

    assert result["legacyIncluded"] is False
    assert result["betsPlaced"] == 1
    assert result["totalNetProfitLoss"] == 9.0


def test_date_filter_still_returns_legacy_rows_unfiltered(tmp_path, monkeypatch, capsys):
    """Plain historical lookups (not aggregate reports) are unaffected -- legacy stays queryable."""
    bets = [_bet("legacy-1", "2026-06-18")]
    _setup(tmp_path, monkeypatch, bets)
    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "date", "--date", "2026-06-18"])

    query_bets.main()
    result = json.loads(capsys.readouterr().out)

    assert len(result) == 1
    assert result[0]["betId"] == "legacy-1"


def test_does_not_modify_the_source_bets_file(tmp_path, monkeypatch, capsys):
    bets = [_bet("legacy-1", "2026-06-18"), _bet("era-1", "2026-08-03")]
    _setup(tmp_path, monkeypatch, bets)
    before = (tmp_path / "bets.jsonl").read_bytes()

    monkeypatch.setattr(sys, "argv", ["query_bets.py", "--filter", "canonical-era-summary"])
    query_bets.main()
    capsys.readouterr()

    after = (tmp_path / "bets.jsonl").read_bytes()
    assert before == after
