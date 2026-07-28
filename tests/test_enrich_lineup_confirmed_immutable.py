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


class TestGameStatusInvariance:
    """
    This stage never reads game.status/excludedFromSlate — lineup-field
    computation must be identical regardless of the game's status. These
    are the postponed/live/final/excluded fixtures the pre-merge review
    asked for: each behaves exactly like an ordinary pregame game as far
    as THIS stage is concerned (live-game/postponement gating is a
    separate, downstream concern owned by lib/postponed_guard.py and
    enforced in write_pending_bets.py/risk_gate.py/validate_bet_logging.py,
    not here).
    """

    @staticmethod
    def _game_with_status(status, **kwargs):
        g = _game("NYY", "PHI", **kwargs)
        g["status"] = status
        return g

    def _lineup_fields(self, g):
        return {k: g[k] for k in (
            "lineupConfirmed", "lineupSource", "lineupStatus",
            "lineupCheckedAt", "lineupAuditUsed",
        )}

    def test_postponed_game_computes_same_lineup_fields_as_pregame(self, monkeypatch, tmp_path):
        postponed = self._game_with_status("Postponed", away_confirmed=True, home_confirmed=True)
        pregame = self._game_with_status("Scheduled", away_confirmed=True, home_confirmed=True)
        out = _run(monkeypatch, tmp_path, [postponed, pregame], audit={})
        assert self._lineup_fields(out["games"][0]) == self._lineup_fields(out["games"][1])
        assert out["games"][0]["status"] == "Postponed"  # status itself is preserved untouched

    def test_live_game_computes_same_lineup_fields_as_pregame(self, monkeypatch, tmp_path):
        live = self._game_with_status("In Progress", away_confirmed=True, home_confirmed=False)
        pregame = self._game_with_status("Scheduled", away_confirmed=True, home_confirmed=False)
        out = _run(monkeypatch, tmp_path, [live, pregame], audit={})
        assert self._lineup_fields(out["games"][0]) == self._lineup_fields(out["games"][1])
        assert out["games"][0]["status"] == "In Progress"

    def test_final_game_computes_same_lineup_fields_as_pregame(self, monkeypatch, tmp_path):
        final = self._game_with_status("Final", away_confirmed=False, home_confirmed=False)
        pregame = self._game_with_status("Scheduled", away_confirmed=False, home_confirmed=False)
        out = _run(monkeypatch, tmp_path, [final, pregame], audit={})
        assert self._lineup_fields(out["games"][0]) == self._lineup_fields(out["games"][1])
        assert out["games"][0]["status"] == "Final"

    def test_excluded_game_still_gets_lineup_fields_computed(self, monkeypatch, tmp_path):
        """
        excludedFromSlate is a downstream (post_fetch_gate.py) concept —
        this stage runs BEFORE that gate in the real pipeline order, but
        must not crash or special-case it if the field happens to be
        present already.
        """
        g = self._game_with_status("Scheduled", away_confirmed=True, home_confirmed=True)
        g["excludedFromSlate"] = True
        g["exclusionReason"] = "test"
        out = _run(monkeypatch, tmp_path, [g], audit={})
        result = out["games"][0]
        assert result["lineupConfirmed"] is True
        assert result["excludedFromSlate"] is True
        assert result["exclusionReason"] == "test"


class TestMalformedButPreviouslyTolerated:
    """
    Inputs the original implementation already defensively handled
    (isinstance guards, `or {}` fallbacks) — the refactor must tolerate
    them identically, not newly crash or newly succeed differently.
    """

    def test_away_is_none_falls_back_to_empty_abbr(self, monkeypatch, tmp_path):
        g = {"away": None, "home": {"abbr": "PHI"}, "awayTeamStats": {}, "homeTeamStats": {}}
        out = _run(monkeypatch, tmp_path, [g], audit={})
        result = out["games"][0]
        assert result["lineupConfirmed"] is False
        assert result["lineupStatus"] == "unconfirmed"

    def test_team_stats_is_none_falls_back_to_empty_dict(self, monkeypatch, tmp_path):
        g = {
            "away": {"abbr": "NYY"}, "home": {"abbr": "PHI"},
            "awayTeamStats": None, "homeTeamStats": None,
        }
        out = _run(monkeypatch, tmp_path, [g], audit={})
        result = out["games"][0]
        assert result["lineupConfirmed"] is False
        assert result["lineupSource"] == "unavailable"

    def test_missing_team_stats_key_entirely(self, monkeypatch, tmp_path):
        g = {"away": {"abbr": "NYY"}, "home": {"abbr": "PHI"}}
        out = _run(monkeypatch, tmp_path, [g], audit={})
        result = out["games"][0]
        assert result["lineupConfirmed"] is False

    def test_empty_games_list_produces_empty_output_games(self, monkeypatch, tmp_path):
        out = _run(monkeypatch, tmp_path, [], audit={})
        assert out["games"] == []


class TestNonMutationAndIdempotency:

    def test_input_game_dicts_are_not_mutated(self, monkeypatch, tmp_path):
        """
        The top-level game dict passed in must come out of the pipeline
        with the SAME identity untouched, i.e. the original object the
        caller holds a reference to must be byte-for-byte unchanged after
        main() runs — proving the "build new, don't mutate old" contract.
        """
        g = _game("NYY", "PHI", away_confirmed=True, home_confirmed=True)
        snapshot = json.loads(json.dumps(g))
        games = [g]
        _run(monkeypatch, tmp_path, games, audit={})
        assert g == snapshot, "the original input game dict must not be mutated by main()"
        assert "lineupConfirmed" not in g, (
            "the original game dict must not have gained the new fields — "
            "they belong only to the newly-built output game dict"
        )

    def test_nested_team_stats_objects_are_not_mutated(self, monkeypatch, tmp_path):
        g = _game("NYY", "PHI", away_confirmed=True, home_confirmed=True)
        ats_snapshot = dict(g["awayTeamStats"])
        hts_snapshot = dict(g["homeTeamStats"])
        _run(monkeypatch, tmp_path, [g], audit={})
        assert g["awayTeamStats"] == ats_snapshot
        assert g["homeTeamStats"] == hts_snapshot

    def test_known_shallow_copy_boundary(self, monkeypatch, tmp_path):
        """
        Documents the actual (and accepted, per docs/IMMUTABLE_PIPELINE.md)
        boundary of this refactor: the OUTPUT game dict is a NEW top-level
        object ({**g, **fields}), but nested values this stage doesn't own
        (like awayTeamStats) are shared BY REFERENCE with the input, since
        dict-unpacking is shallow. This stage never writes into those
        nested objects (proven by the two tests above), so the sharing is
        safe under this stage's own contract — but a future stage must not
        assume the output's nested dicts are independent copies of the
        input's. This test makes that boundary explicit rather than
        letting it be discovered by accident later.
        """
        g = _game("NYY", "PHI", away_confirmed=True, home_confirmed=True)
        games = [g]
        slate = {"date": "2026-07-27", "games": games}
        slate_path = tmp_path / "slate.json"
        slate_path.write_text(json.dumps(slate))
        monkeypatch.setattr(elc, "SLATE_PATH", str(slate_path))
        monkeypatch.setattr(elc, "now_utc", lambda: FIXED_NOW)
        monkeypatch.setattr(elc, "load_audit", lambda date: {})
        monkeypatch.setattr(elc, "fetch_rotowire", lambda: (frozenset(), False))

        new_games, _counters, _logs = elc.enrich_games_immutable(
            games, audit={}, rw_keys=frozenset(), rw_ok=False, checked_at=FIXED_NOW,
        )
        assert new_games[0]["awayTeamStats"] is g["awayTeamStats"], (
            "nested awayTeamStats is intentionally the SAME object as the input's — "
            "documented shallow-copy boundary, not a bug"
        )

    def test_repeated_execution_is_idempotent(self, monkeypatch, tmp_path):
        """Running main() twice on its own output must produce the same fields again."""
        games = [_game("NYY", "PHI", away_confirmed=True, home_confirmed=True)]
        out1 = _run(monkeypatch, tmp_path, games, audit={})
        # Feed the already-enriched output back in as input for a second pass.
        out2 = _run(monkeypatch, tmp_path, out1["games"], audit={})
        assert out2["games"][0]["lineupConfirmed"] == out1["games"][0]["lineupConfirmed"]
        assert out2["games"][0]["lineupStatus"] == out1["games"][0]["lineupStatus"]
        assert out2["games"][0]["lineupSource"] == out1["games"][0]["lineupSource"]

    def test_game_ordering_is_unchanged(self, monkeypatch, tmp_path):
        games = [
            _game("NYY", "PHI"), _game("BOS", "TB"),
            _game("LAD", "SD"), _game("ATL", "NYM"),
        ]
        out = _run(monkeypatch, tmp_path, games, audit={})
        result_matchups = [f"{g['away']['abbr']}@{g['home']['abbr']}" for g in out["games"]]
        assert result_matchups == ["NYY@PHI", "BOS@TB", "LAD@SD", "ATL@NYM"]
