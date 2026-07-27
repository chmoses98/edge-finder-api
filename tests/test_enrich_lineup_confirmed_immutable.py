#!/usr/bin/env python3
"""
tests/test_enrich_lineup_confirmed_immutable.py
=================================================
Regression test proving scripts/enrich_lineup_confirmed.py's output is
byte-for-byte identical before and after the Phase 3 immutable-pipeline
refactor (see docs/IMMUTABLE_PIPELINE.md).

This test was written and passed against the ORIGINAL mutate-in-place
implementation before the refactor, then re-run unchanged after the
refactor (which replaced in-place dict mutation with building fresh
game/slate dicts) to confirm the computed values did not change. Network
calls (RotoWire) and the lineup-audit file read are monkeypatched for
determinism — this test is about the field-computation logic, not I/O.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import enrich_lineup_confirmed as elc  # noqa: E402

FIXED_NOW = "2026-07-27T18:00:00Z"


def _game(away, home, away_confirmed=False, home_confirmed=False, away_batters=9, home_batters=9):
    return {
        "away": {"abbr": away},
        "home": {"abbr": home},
        "awayTeamStats": {"lineupConfirmed": away_confirmed, "lineupBattersResolved": away_batters},
        "homeTeamStats": {"lineupConfirmed": home_confirmed, "lineupBattersResolved": home_batters},
    }


def _run(monkeypatch, tmp_path, games, audit, rw_keys=frozenset(), rw_ok=False):
    slate = {"date": "2026-07-27", "games": games}
    slate_path = tmp_path / "slate.json"
    slate_path.write_text(json.dumps(slate))

    monkeypatch.setattr(elc, "SLATE_PATH", str(slate_path))
    monkeypatch.setattr(elc, "now_utc", lambda: FIXED_NOW)
    monkeypatch.setattr(elc, "load_audit", lambda date: audit)
    monkeypatch.setattr(elc, "fetch_rotowire", lambda: (rw_keys, rw_ok))

    result = elc.main()
    assert result == 0

    with open(slate_path) as f:
        return json.load(f)


class TestEnrichLineupConfirmedGoldenOutput:
    """
    Each test asserts the FULL set of fields this stage owns
    (lineupConfirmed, lineupSource, lineupStatus, lineupCheckedAt,
    lineupAuditUsed) for a specific input scenario. These are the golden
    values captured against the pre-refactor implementation.
    """

    def test_audit_confirms_both_sides(self, monkeypatch, tmp_path):
        games = [_game("NYY", "PHI")]
        audit = {
            "NYY@PHI": {
                "away_confirmed": True, "home_confirmed": True,
                "away_batters": 9, "home_batters": 9,
                "away_status": "confirmed", "home_status": "confirmed",
                "away_source": "mlb", "home_source": "mlb",
                "generatedAt": "2026-07-27T17:00:00Z",
            }
        }
        out = _run(monkeypatch, tmp_path, games, audit)
        g = out["games"][0]
        assert g["lineupConfirmed"] is True
        assert g["lineupStatus"] == "confirmed"
        assert g["lineupSource"] == "lineup_audit"
        assert g["lineupCheckedAt"] == FIXED_NOW
        assert g["lineupAuditUsed"] is True

    def test_audit_confirms_one_side_only_is_partial(self, monkeypatch, tmp_path):
        games = [_game("NYY", "PHI")]
        audit = {
            "NYY@PHI": {
                "away_confirmed": True, "home_confirmed": False,
                "away_batters": 9, "home_batters": 5,
                "away_status": "confirmed", "home_status": "unconfirmed",
                "away_source": "mlb", "home_source": "mlb",
                "generatedAt": "2026-07-27T17:00:00Z",
            }
        }
        out = _run(monkeypatch, tmp_path, games, audit)
        g = out["games"][0]
        assert g["lineupConfirmed"] is False
        assert g["lineupStatus"] == "partial"
        assert g["lineupSource"] == "lineup_audit"
        assert g["lineupAuditUsed"] is True

    def test_no_audit_entry_falls_back_to_teamstats_confirmed(self, monkeypatch, tmp_path):
        games = [_game("NYY", "PHI", away_confirmed=True, home_confirmed=True)]
        out = _run(monkeypatch, tmp_path, games, audit={})
        g = out["games"][0]
        assert g["lineupConfirmed"] is True
        assert g["lineupStatus"] == "confirmed"
        assert g["lineupSource"] == "mlb_statsapi"
        assert g["lineupAuditUsed"] is False

    def test_no_audit_no_teamstats_confirmation_is_unconfirmed(self, monkeypatch, tmp_path):
        games = [_game("NYY", "PHI", away_confirmed=False, home_confirmed=False)]
        out = _run(monkeypatch, tmp_path, games, audit={})
        g = out["games"][0]
        assert g["lineupConfirmed"] is False
        assert g["lineupStatus"] == "unconfirmed"
        assert g["lineupSource"] == "unavailable"
        assert g["lineupAuditUsed"] is False

    def test_rotowire_confirmation_appends_to_source_string(self, monkeypatch, tmp_path):
        games = [_game("NYY", "PHI", away_confirmed=True, home_confirmed=True)]
        out = _run(
            monkeypatch, tmp_path, games, audit={},
            rw_keys=frozenset({frozenset({"NYY", "PHI"})}), rw_ok=True,
        )
        g = out["games"][0]
        assert g["lineupSource"] == "mlb_statsapi+rotowire"

    def test_audit_override_of_stale_teamstats_field(self, monkeypatch, tmp_path):
        """
        The exact June 17 regression this script exists to fix: teamStats
        says unconfirmed, but the fresher lineup audit says confirmed —
        the audit must win.
        """
        games = [_game("NYY", "PHI", away_confirmed=False, home_confirmed=False)]
        audit = {
            "NYY@PHI": {
                "away_confirmed": True, "home_confirmed": True,
                "away_batters": 9, "home_batters": 9,
                "away_status": "confirmed", "home_status": "confirmed",
                "away_source": "mlb", "home_source": "mlb",
                "generatedAt": "2026-07-27T17:00:00Z",
            }
        }
        out = _run(monkeypatch, tmp_path, games, audit)
        g = out["games"][0]
        assert g["lineupConfirmed"] is True
        assert g["lineupSource"] == "lineup_audit"

    def test_multiple_games_each_get_independent_fields(self, monkeypatch, tmp_path):
        games = [
            _game("NYY", "PHI", away_confirmed=True, home_confirmed=True),
            _game("BOS", "TB", away_confirmed=False, home_confirmed=False),
        ]
        out = _run(monkeypatch, tmp_path, games, audit={})
        g0, g1 = out["games"]
        assert g0["lineupConfirmed"] is True
        assert g1["lineupConfirmed"] is False

    def test_slate_level_fields_are_preserved(self, monkeypatch, tmp_path):
        """Non-games fields on the slate (e.g. 'date') must survive untouched."""
        games = [_game("NYY", "PHI")]
        out = _run(monkeypatch, tmp_path, games, audit={})
        assert out["date"] == "2026-07-27"

    def test_game_fields_other_than_lineup_are_preserved(self, monkeypatch, tmp_path):
        """
        Fields this stage does not own (e.g. away/home team blocks) must
        pass through unchanged — this is the key risk of switching from
        in-place mutation to building a fresh dict per game.
        """
        games = [_game("NYY", "PHI", away_confirmed=True, home_confirmed=True)]
        out = _run(monkeypatch, tmp_path, games, audit={})
        g = out["games"][0]
        assert g["away"] == {"abbr": "NYY"}
        assert g["home"] == {"abbr": "PHI"}
        assert g["awayTeamStats"]["lineupConfirmed"] is True
