#!/usr/bin/env python3
"""
tests/test_slate_scheduled_vs_manual_authority.py
======================================================
Scheduled Research Freshness mission: fetch-slate.yml's 12pm/4pm/6pm ET
`schedule` runs and its `workflow_dispatch`/`push` (manual) runs both
write toward the same data/slates/<date>/authoritative.json, but must
never compete for it the same way two manual reruns do. Required
operating rule: "scheduled = research freshness only; manual
workflow_dispatch = authoritative betting slate."

Covers the acceptance criteria from that mission:
  1. a scheduled refresh occurs earlier in the day
  2. a scheduled event never executes any bet-placement step (workflow
     structure -- see tests/test_fetch_slate_workflow_structure.py, not
     duplicated here)
  3. a scheduled event does not consume/lock the manual slate
  4. a later same-date workflow_dispatch still runs normally and uses
     fresh inputs
  5. a manual run can update/replace the earlier research-only artifact
  6. stale-date protection remains intact (unaffected file -- see
     scripts/validate_current_slate_date.py, not duplicated here)
  7. prospective ModelEvaluation/checkpoint collection still receives
     fresh data (data/slate.json keeps being synced from authoritative.json
     on every successful run, scheduled or manual -- unchanged mechanism)
  8. repeated scheduled refreshes remain safe/idempotent
  9. no automatic betting is introduced (workflow structure, not
     duplicated here)
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.slate_manager import (  # noqa: E402
    detect_run_type,
    save_slate,
    load_authoritative,
    get_authoritative_path,
    RUN_TYPE_OFFICIAL_PREGAME,
    RUN_TYPE_LINEUP_RECHECK,
    RUN_TYPE_IN_PLAY_RECHECK,
    RUN_TYPE_SCHEDULED_REFRESH,
    TRIGGER_SCHEDULE,
    TRIGGER_MANUAL,
)


def make_game(away="KC", home="WSH", game_id="1", price=-120, lineup_len=9,
              pitcher_confirmed=True, start_time="2099-01-01T00:00:00Z"):
    return {
        "gameId": game_id,
        "startTime": start_time,
        "away": {
            "abbr": away,
            "pitcher": {"id": "p1", "name": "A"} if pitcher_confirmed else {},
            "lineup": list(range(lineup_len)),
        },
        "home": {
            "abbr": home,
            "pitcher": {"id": "p2", "name": "B"} if pitcher_confirmed else {},
            "lineup": list(range(lineup_len)),
        },
        "markets": [{"market": "ML_Away", "price": price, "modelProb": 55.0}],
    }


@pytest.fixture
def ps():
    if "protect_slate" in sys.modules:
        del sys.modules["protect_slate"]
    scripts_dir = os.path.join(ROOT, "scripts")
    sys.path.insert(0, scripts_dir)
    import protect_slate as _ps
    return _ps


def _wire(ps, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_slate(root, games, date_str="2026-06-16"):
    with open(root / "data" / "slate.json", "w") as f:
        json.dump({"date": date_str, "games": games}, f)


class TestDetectRunTypeScheduledAlwaysReturnsScheduledRefresh:
    """A `schedule`-triggered call must never return
    OFFICIAL_PREGAME/LINEUP_RECHECK/IN_PLAY_RECHECK -- those are reserved
    for a manual call, regardless of authoritative.json's own state."""

    def test_no_authoritative_yet(self, tmp_path):
        rt = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_SCHEDULE)
        assert rt == RUN_TYPE_SCHEDULED_REFRESH

    def test_authoritative_exists_no_games_started(self, tmp_path):
        auth_dir = tmp_path / "data" / "slates" / "2026-06-16"
        auth_dir.mkdir(parents=True)
        (auth_dir / "authoritative.json").write_text(json.dumps({"games": []}))
        rt = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_SCHEDULE)
        assert rt == RUN_TYPE_SCHEDULED_REFRESH

    def test_default_trigger_is_manual_not_schedule(self, tmp_path):
        """Omitting trigger_source entirely must behave exactly like
        TRIGGER_MANUAL (back-compat for every pre-existing caller)."""
        rt = detect_run_type("2026-06-16", str(tmp_path))
        assert rt == RUN_TYPE_OFFICIAL_PREGAME  # manual, no authoritative yet


class TestScheduledSeedsButNeverLocksTheManualSlate:
    """Acceptance criteria 1, 3, 4, 5: a scheduled refresh happening
    earlier in the day must never prevent, weaken, or "consume" a later
    manual run's ability to fully re-establish the authoritative slate."""

    def test_scheduled_seeds_authoritative_tagged_schedule(self, tmp_path):
        result = save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [make_game()]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )
        assert result["authoritativeWritten"] is True
        auth = load_authoritative("2026-06-16", str(tmp_path))
        assert auth["_authoritativeSource"] == TRIGGER_SCHEDULE
        # Saved to a distinctly-named file, never official_*.json.
        assert any("scheduled_refresh_" in p for p in result["savedPaths"])
        assert not any("official_" in p for p in result["savedPaths"])

    def test_later_manual_run_fully_reclaims_authority_even_without_completeness_improvement(self, tmp_path):
        """
        The exact bug this mission fixes: an earlier scheduled run already
        had complete lineups/pitchers (common by mid-afternoon), so the
        old completeness-only heuristic would silently discard a later
        manual run's fresher odds/prices for the SAME lineup. A manual
        run must now win unconditionally.
        """
        # Scheduled run establishes a complete lineup with an old price.
        g_sched = make_game(price=-120, lineup_len=9, pitcher_confirmed=True)
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [g_sched]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )

        # Manual run later: SAME lineup completeness, but a moved price --
        # completeness heuristic alone would call this "no improvement".
        g_manual = make_game(price=-145, lineup_len=9, pitcher_confirmed=True)
        run_type = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_MANUAL)
        assert run_type in (RUN_TYPE_LINEUP_RECHECK, RUN_TYPE_IN_PLAY_RECHECK)
        result = save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [g_manual]},
            run_type, trigger_source=TRIGGER_MANUAL,
        )
        assert result["authoritativeUpdated"] is True
        auth = load_authoritative("2026-06-16", str(tmp_path))
        assert auth["_authoritativeSource"] == TRIGGER_MANUAL
        assert auth["games"][0]["markets"][0]["price"] == -145  # fresh price won

    def test_manual_run_never_returns_scheduled_refresh(self, tmp_path):
        """detect_run_type() must never route a manual call to
        SCHEDULED_REFRESH just because a scheduled run happened earlier."""
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [make_game()]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )
        run_type = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_MANUAL)
        assert run_type != RUN_TYPE_SCHEDULED_REFRESH


class TestScheduledNeverOverwritesAnAlreadyManualSlate:
    """The reverse case: once a manual run has claimed today's
    authoritative slate, a LATER scheduled run (e.g. the 6pm safety retry
    firing after a 5pm manual dispatch) must never dilute or overwrite it."""

    def test_scheduled_after_manual_skips_the_merge_entirely(self, tmp_path):
        # Manual run establishes authority first.
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [make_game(price=-145)]},
            RUN_TYPE_OFFICIAL_PREGAME, trigger_source=TRIGGER_MANUAL,
        )
        before = load_authoritative("2026-06-16", str(tmp_path))
        assert before["_authoritativeSource"] == TRIGGER_MANUAL

        # A later scheduled run fires with DIFFERENT (e.g. stale/older-look)
        # data -- must not touch authoritative.json's content at all.
        result = save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [make_game(price=-999)]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )
        assert result["authoritativeUpdated"] is False
        assert result["authoritativeWritten"] is False
        after = load_authoritative("2026-06-16", str(tmp_path))
        assert after == before
        assert after["games"][0]["markets"][0]["price"] == -145

        # It still writes its own timestamped snapshot for provenance.
        assert any("scheduled_refresh_" in p for p in result["savedPaths"])


class TestScheduledOnlyIdempotency:
    """Acceptance criteria 8: repeated scheduled refreshes (no manual run
    involved) remain safe/idempotent, using the pre-existing
    completeness-gated merge among scheduled-only runs."""

    def test_identical_scheduled_rerun_does_not_change_authoritative_content(self, tmp_path):
        game = make_game()
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [game]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )
        first = load_authoritative("2026-06-16", str(tmp_path))

        result = save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [game]},
            RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
        )
        second = load_authoritative("2026-06-16", str(tmp_path))
        assert result["authoritativeUpdated"] is True  # merge ran (idempotent, not skipped)
        assert first["games"] == second["games"]
        assert second["_authoritativeSource"] == TRIGGER_SCHEDULE

    def test_three_repeated_scheduled_refreshes_stay_stable(self, tmp_path):
        game = make_game()
        for _ in range(3):
            save_slate(
                "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [game]},
                RUN_TYPE_SCHEDULED_REFRESH, trigger_source=TRIGGER_SCHEDULE,
            )
        auth = load_authoritative("2026-06-16", str(tmp_path))
        assert auth["games"][0]["gameId"] == "1"
        assert auth["_authoritativeSource"] == TRIGGER_SCHEDULE


class TestStartedGameFreezeAndSentinelRulesUnaffectedByTrigger:
    """Safety-critical rules (started-game freeze, sentinel rejection) must
    hold identically regardless of trigger_source -- the manual-always-wins
    relaxation only applies to the completeness heuristic, never to these."""

    def test_started_game_frozen_even_for_a_manual_rerun(self, tmp_path):
        from datetime import datetime, timezone
        started_game = make_game(price=-120, start_time="2020-01-01T00:00:00Z")
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [started_game]},
            RUN_TYPE_OFFICIAL_PREGAME, trigger_source=TRIGGER_MANUAL,
        )
        # Manual rerun tries to change the started game's price.
        updated_game = make_game(price=-999, start_time="2020-01-01T00:00:00Z")
        run_type = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_MANUAL)
        assert run_type == RUN_TYPE_IN_PLAY_RECHECK
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [updated_game]},
            run_type, trigger_source=TRIGGER_MANUAL,
        )
        auth = load_authoritative("2026-06-16", str(tmp_path))
        assert auth["games"][0]["markets"][0]["price"] == -120  # frozen, NOT overwritten

    def test_sentinel_priced_game_rejected_even_for_a_manual_rerun(self, tmp_path):
        save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [make_game(price=-120)]},
            RUN_TYPE_OFFICIAL_PREGAME, trigger_source=TRIGGER_MANUAL,
        )
        bad_game = make_game(price=19900)
        run_type = detect_run_type("2026-06-16", str(tmp_path), trigger_source=TRIGGER_MANUAL)
        result = save_slate(
            "2026-06-16", str(tmp_path), {"date": "2026-06-16", "games": [bad_game]},
            run_type, trigger_source=TRIGGER_MANUAL,
        )
        auth = load_authoritative("2026-06-16", str(tmp_path))
        assert auth["games"][0]["markets"][0]["price"] == -120  # rejected, NOT overwritten
        assert result["runReport"]["rejectedCount"] == 1


class TestEndToEndViaProtectSlateMain:
    """Full-stack proof through protect_slate.main() itself (the actual
    entry point fetch-slate.yml invokes), not just the library functions
    directly."""

    def test_scheduled_main_run_then_manual_main_run_same_date(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)

        _write_slate(root, [make_game(price=-120)])
        ps.main("2026-06-16", trigger_source="schedule")
        out = capsys.readouterr().out
        assert "Run type: SCHEDULED_REFRESH" in out

        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        assert json.loads(auth_path.read_text())["_authoritativeSource"] == "schedule"

        _write_slate(root, [make_game(price=-145)])
        ps.main("2026-06-16", trigger_source="manual")
        out = capsys.readouterr().out
        assert "Run type: SCHEDULED_REFRESH" not in out

        auth = json.loads(auth_path.read_text())
        assert auth["_authoritativeSource"] == "manual"
        assert auth["games"][0]["markets"][0]["price"] == -145

        # data/slate.json (the file every downstream consumer -- research
        # AND betting -- reads) reflects the manual, fresher content.
        slate_json = json.loads((root / "data" / "slate.json").read_text())
        assert slate_json["games"][0]["markets"][0]["price"] == -145

    def test_scheduled_main_run_after_manual_leaves_slate_json_on_the_manual_content(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)

        _write_slate(root, [make_game(price=-145)])
        ps.main("2026-06-16", trigger_source="manual")

        _write_slate(root, [make_game(price=-999)])
        ps.main("2026-06-16", trigger_source="schedule")

        slate_json = json.loads((root / "data" / "slate.json").read_text())
        assert slate_json["games"][0]["markets"][0]["price"] == -145  # unchanged by the later scheduled run

    def test_unspecified_trigger_source_behaves_as_manual(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        _write_slate(root, [make_game()])
        ps.main("2026-06-16")
        out = capsys.readouterr().out
        assert "Run type: OFFICIAL_PREGAME" in out
        assert "(trigger=manual)" in out
