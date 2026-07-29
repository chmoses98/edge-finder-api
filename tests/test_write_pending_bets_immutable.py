#!/usr/bin/env python3
"""
tests/test_write_pending_bets_immutable.py
===============================================
Phase 10 (final architecture phase) golden-baseline suite for
scripts/write_pending_bets.py -- the final production script to be
converted into the pure-decision-boundary pattern established in
Phases 3-9.

PRE-REFACTOR BEHAVIOR MAP
--------------------------
Public functions: load_json, stable_key, american_to_decimal_entry,
build_bet_record, main. (New, Phase 10: should_skip_excluded_game_pure,
should_block_game_for_pregame_gate_pure, is_real_money_market_entry_pure.)

CLI: no arguments (unlike protect_slate.py/validate_slate_final.py,
which take an optional DATE positional arg). Always operates on
whatever data/slate.json currently contains -- date comes FROM the
slate file, not from argv.

Input files: data/slate.json (read), bets.json (read if present, else
starts from []).
Output files: bets.json only (rewritten in full via json.dump when
new_bets is non-empty; untouched -- not even re-serialized -- when
new_bets is empty).

Clock: ONE read, `now_ts = datetime.now(tz=timezone.utc).isoformat()`,
at the very top of main() -- no injectable clock (same pre-existing gap
as protect_slate.py). now_ts is threaded into both
check_game_status(current_utc=now_ts) and build_bet_record(..., now_ts)
-- never read twice.

Imported helpers: lib.postponed_guard.check_game_status (pure, shared,
well-tested via tests/test_live_game_gate.py) and is_live_game_blocked
(imported but never actually called in main() -- a genuine pre-existing
dead import, confirmed via grep, not touched this phase).

Exit codes: 1 if data/slate.json missing, 1 if slate.json has no
'date' field, 1 if bets.json exists but is not a list, 0 otherwise
(even when zero new bets are found -- that is normal, not a failure).

Exception behavior: no top-level try/except in main() -- a malformed
data/slate.json (invalid JSON) propagates an uncaught
json.JSONDecodeError; a malformed bets.json propagates the same.

Ordering: existing_keys is built once from bets.json BEFORE the game
loop starts, then mutated incrementally as new_bets are appended within
the SAME loop -- meaning a duplicate entry appearing twice within a
single run's own slate.json (not just against pre-existing bets.json)
is caught by the second occurrence too, since existing_keys.add(key) is
called immediately after each successful append. This is a genuine
design property this phase's pure extraction must not change: the
per-game/per-entry loop must remain a single incremental pass, not a
batch pure computation, or this within-run dedup guarantee would be
silently lost.

Rerun/idempotency: fully idempotent by the stable composite key
(date|game|market|ticker); a second run against unchanged inputs
produces zero new bets and does not rewrite bets.json at all (the
`if new_bets:` guard skips the write entirely -- not even a no-op
rewrite).

Malformed input: non-dict top-level slate.json raises AttributeError
on `.get('date')`; non-list bets.json is explicitly checked and raises
via sys.exit(1) with a clear error message (NOT an uncaught exception --
this script's own defensive check, unlike protect_slate.py's uncaught
JSONDecodeError path for a corrupted authoritative.json).

Duplicate/correlation/Rule 71/Rule 81/bankroll/stake-sizing: zero
references anywhere in this script (grep-confirmed, regression-guarded
in test_write_pending_bets_hardening.py). 'stake'/'betSize' fields are
read and passed through unchanged from the already-computed
marketLedger entry -- never computed or sized here.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def wpb():
    if "write_pending_bets" in sys.modules:
        del sys.modules["write_pending_bets"]
    import write_pending_bets as _wpb
    return _wpb


def _wire(wpb, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(wpb, "SLATE_PATH", str(tmp_path / "data" / "slate.json"))
    monkeypatch.setattr(wpb, "BETS_PATH", str(tmp_path / "bets.json"))
    return tmp_path


def make_entry(market="ML_Away", tier="HIGH", status="Accepted", ticker="KXMLB-26JUN16KCWSH-KC",
               kalshi_price=-120, exec_price=54.5, edge=4.5, stake=5.0, line=None,
               scheduled_start="2026-06-16T22:46:00Z"):
    return {
        "market": market, "confidenceTier": tier, "status": status,
        "ticker": ticker, "marketTicker": ticker, "seriesTicker": ticker.split("-")[0] if ticker else None,
        "kalshiPrice": kalshi_price, "executablePriceUsed": exec_price,
        "edge": edge, "betSize": stake, "line": line,
        "modelProb": 60.0, "kalshiImplied": 54.0,
        "scheduledStartTime": scheduled_start,
        "awayProjRuns": 4.5, "homeProjRuns": 3.8,
    }


def make_game(away="KC", home="WSH", entries=None, status="Scheduled", excluded=False):
    g = {
        "away": {"abbr": away}, "home": {"abbr": home},
        "status": status,
        "marketLedger": entries if entries is not None else [make_entry()],
    }
    if excluded:
        g["excludedFromSlate"] = True
    return g


def make_slate(games, date="2026-06-16"):
    return {"date": date, "games": games}


class TestBuildBetRecordGoldenEquivalence:

    def test_basic_record_shape_unchanged(self, wpb):
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", make_entry(), "2026-06-16T12:00:00+00:00")
        assert record["date"] == "2026-06-16"
        assert record["game"] == "KC@WSH"
        assert record["side"] == "KC"
        assert record["betSide"] == "AWAY"
        assert record["status"] == "pending"
        assert record["result"] is None
        assert record["source"] == "data/slate.json"
        assert record["createdBy"] == "write_pending_bets.py"

    def test_exec_price_takes_priority_over_kalshi_price(self, wpb):
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", make_entry(exec_price=60.0, kalshi_price=-120), "ts")
        assert record["actualEntryPrice"] == 0.6

    def test_falls_back_to_kalshi_price_when_no_exec_price(self, wpb):
        entry = make_entry(kalshi_price=-120)
        entry["executablePriceUsed"] = None
        entry["executablePriceAtOutput"] = None
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert record["actualEntryPrice"] == wpb.american_to_decimal_entry(-120)

    def test_null_entry_price_marks_clv_blocked(self, wpb):
        entry = make_entry()
        entry["executablePriceUsed"] = None
        entry["executablePriceAtOutput"] = None
        entry["kalshiPrice"] = None
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", entry, "ts")
        assert record["actualEntryPrice"] is None
        assert record["realMoneyBlocked"] is True
        assert record["dataHealthWarning"] == "actualEntryPrice_null_CLV_uncapturable"

    def test_tt_market_required_wins_computed(self, wpb):
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", make_entry(market="TT_Away_Over", line=4), "ts")
        assert record["requiredRunsToWin"] == 5

    def test_non_tt_market_no_required_wins(self, wpb):
        record = wpb.build_bet_record("2026-06-16", "KC@WSH", make_entry(market="ML_Away"), "ts")
        assert record["requiredRunsToWin"] is None


class TestMainGoldenEquivalence:

    def test_missing_slate_json_exits_1(self, wpb, tmp_path, monkeypatch, capsys):
        _wire(wpb, tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as e:
            wpb.main()
        assert e.value.code == 1
        assert "not found" in capsys.readouterr().out

    def test_slate_missing_date_exits_1(self, wpb, tmp_path, monkeypatch, capsys):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"games": []}, f)
        with pytest.raises(SystemExit) as e:
            wpb.main()
        assert e.value.code == 1
        assert "no 'date' field" in capsys.readouterr().out

    def test_bets_json_not_a_list_exits_1(self, wpb, tmp_path, monkeypatch, capsys):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game()]), f)
        with open(root / "bets.json", "w") as f:
            json.dump({"not": "a list"}, f)
        with pytest.raises(SystemExit) as e:
            wpb.main()
        assert e.value.code == 1
        assert "not a list" in capsys.readouterr().out

    def test_full_pass_writes_one_new_bet(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game()]), f)
        result = wpb.main()
        assert result == 0
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 1
        assert bets[0]["market"] == "ML_Away"

    def test_paper_tier_not_logged(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        entries = [make_entry(tier="LOW"), make_entry(tier="PAPER")]
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game(entries=entries)]), f)
        wpb.main()
        assert not (root / "bets.json").exists()

    def test_non_accepted_status_not_logged(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game(entries=[make_entry(status="Rejected")])]), f)
        wpb.main()
        assert not (root / "bets.json").exists()

    def test_excluded_game_not_logged(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game(excluded=True)]), f)
        wpb.main()
        assert not (root / "bets.json").exists()

    def test_in_progress_game_blocked(self, wpb, tmp_path, monkeypatch, capsys):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game(status="In Progress")]), f)
        wpb.main()
        assert not (root / "bets.json").exists()
        assert "PREGAME GATE BLOCKED" in capsys.readouterr().out

    def test_no_new_bets_does_not_write_bets_json_at_all(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game()]), f)
        wpb.main()
        first_mtime = (root / "bets.json").stat().st_mtime_ns
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game()]), f)
        wpb.main()
        second_mtime = (root / "bets.json").stat().st_mtime_ns
        assert first_mtime == second_mtime, "bets.json must not be rewritten when there are zero new bets"

    def test_no_ticker_marks_clv_uncapturable_but_still_logs(self, wpb, tmp_path, monkeypatch, capsys):
        root = _wire(wpb, tmp_path, monkeypatch)
        entry = make_entry(ticker=None)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(make_slate([make_game(entries=[entry])]), f)
        wpb.main()
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 1
        assert "no ticker" in capsys.readouterr().out

    def test_non_dict_slate_json_raises_attribute_error(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump([1, 2, 3], f)
        with pytest.raises(AttributeError):
            wpb.main()

    def test_malformed_slate_json_raises_json_decode_error(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        (root / "data" / "slate.json").write_text("{not valid")
        with pytest.raises(json.JSONDecodeError):
            wpb.main()
