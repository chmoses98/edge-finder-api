#!/usr/bin/env python3
"""
tests/edgelab/test_standalone_full_universe_evaluation.py
==============================================================
Phase 2 (Full-Universe MLB Kalshi Probability Persistence), item 7/11:
scripts/edgelab/standalone_full_universe_evaluation.py -- unfiltered
research-snapshot persistence for a standalone Kalshi price check, and
its idempotency/dedup rule (item 11).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.probability_status import SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE
from scripts.edgelab import standalone_full_universe_evaluation as sfe


def make_game(game_id, away, home, start_time):
    return {
        "gameId": game_id,
        "away": {"abbr": away, "pitcherSavant": {"xFIP": 3.8, "avgIPperStart": 6.0}},
        "home": {"abbr": home, "pitcherSavant": {"xFIP": 4.0, "avgIPperStart": 6.0}},
        "awayTeamStats": {"offenseBaselineAdj": 4.6},
        "homeTeamStats": {"offenseBaselineAdj": 4.3},
        "startTime": start_time,
        "park": {"parkFactor": 100},
    }


def write_snapshot(path, markets, date_str="2026-08-22"):
    doc = {"date": date_str, "kalshi_date": "26AUG22", "markets": markets, "discoveredUnknownSeriesMarkets": []}
    with open(path, "w") as f:
        json.dump(doc, f)
    return path


def write_slate(path, games):
    with open(path, "w") as f:
        json.dump({"games": games}, f)
    return path


def ml_market(ticker, event_ticker, title, yes_bid=0.5, yes_ask=0.51):
    return {"market_ticker": ticker, "event_ticker": event_ticker, "title": title,
            "subtitle": "", "status": "active", "yes_bid": yes_bid, "yes_ask": yes_ask,
            "close_time": "2026-08-25T23:05:00Z", "volume": 100.0}


def _markets():
    return [
        ml_market("KXMLBGAME-26AUG222040MINSD-SD", "KXMLBGAME-26AUG222040MINSD", "San Diego wins"),
        ml_market("KXMLBHIT-26AUG222010CINAZ-CINTSTEPHENSON37-3", "KXMLBHIT-26AUG222010CINAZ", "3+ hits?"),
    ]


class TestStandaloneFullUniverseEvaluation:

    def test_every_captured_contract_gets_a_row_unfiltered(self, tmp_path):
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        result = sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        assert result["rowsBuilt"] == 2
        assert result["rowsWritten"] == 2

        rows = [json.loads(line) for line in open(out_path)]
        assert len(rows) == 2

    def test_rows_tagged_prospective_standalone(self, tmp_path):
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        rows = [json.loads(line) for line in open(out_path)]
        assert all(r["sourceCaptureType"] == SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE for r in rows)

    def test_unsupported_family_row_persisted_not_dropped(self, tmp_path):
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        rows = [json.loads(line) for line in open(out_path)]
        hitter_row = next(r for r in rows if r["marketFamily"] == "hitter_hits")
        assert hitter_row["modelFairProbability"] is None
        assert hitter_row["probabilityStatus"] == "UNSUPPORTED_FAMILY"

    def test_identical_rerun_is_idempotent_no_op(self, tmp_path, monkeypatch):
        # runId only deterministically re-derives from content_signature
        # when GITHUB_RUN_ID is set (see lib.edgelab.ids.new_run_id's own
        # docstring) -- exactly the real CI condition this script runs
        # under. A local/manual run with no GITHUB_RUN_ID intentionally
        # gets a fresh random runId every invocation (ids.py's documented,
        # pre-existing fallback behavior, unrelated to this script).
        monkeypatch.setenv("GITHUB_RUN_ID", "999000111")
        monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        r1 = sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        r2 = sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        assert r1["rowsWritten"] == 2
        assert r1["runId"] == r2["runId"]  # same snapshot -> same content_signature -> same runId
        assert r2["rowsWritten"] == 0
        assert r2["rowsSkipped"] == 2
        rows = [json.loads(line) for line in open(out_path)]
        assert len(rows) == 2  # not duplicated

    def test_distinct_snapshot_preserves_a_separate_checkpoint(self, tmp_path, monkeypatch):
        # Two DIFFERENT standalone captures (distinct snapshot content,
        # e.g. two runs at different times of day within the same
        # workflow run) must each be preserved as their own rows, never
        # collapsed into one.
        monkeypatch.setenv("GITHUB_RUN_ID", "999000111")
        monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        snap1 = write_snapshot(str(tmp_path / "snap_t1.json"), _markets())
        r1 = sfe.run("2026-08-22", snap1, slate_path=slate_path, out_path=out_path)

        markets2 = _markets()
        markets2[0]["yes_ask"] = 0.60  # different price -> genuinely distinct capture content
        snap2 = write_snapshot(str(tmp_path / "snap_t2.json"), markets2)
        r2 = sfe.run("2026-08-22", snap2, slate_path=slate_path, out_path=out_path)

        assert r1["rowsWritten"] == 2
        assert r2["rowsWritten"] == 2  # preserved as distinct rows, not skipped
        assert r1["runId"] != r2["runId"]
        rows = [json.loads(line) for line in open(out_path)]
        assert len(rows) == 4

    def test_standalone_row_id_never_collides_with_scheduled_extension_id(self, tmp_path, monkeypatch):
        # extend_full_universe_evaluations()'s OWN (date, marketTicker)
        # identity is what RECOMMENDATION_SYNC's scheduled path still
        # uses (unchanged) -- this script must re-key its own rows so
        # they can never collide with, or silently displace, a scheduled
        # row for the same ticker/date.
        from lib.edgelab import ids as ids_mod
        monkeypatch.setenv("GITHUB_RUN_ID", "999000111")
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        result = sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path)
        rows = [json.loads(line) for line in open(out_path)]
        scheduled_style_id = ids_mod.build_model_evaluation_id("2026-08-22", rows[0]["marketTicker"])
        assert rows[0]["modelEvaluationId"] != scheduled_style_id
        assert rows[0]["modelEvaluationId"] == ids_mod.build_model_evaluation_id(result["runId"], rows[0]["marketTicker"])

    def test_dry_run_writes_nothing(self, tmp_path):
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        slate_path = write_slate(str(tmp_path / "slate.json"), [make_game(1, "MIN", "SD", "2026-08-23T02:40:00Z")])
        out_path = str(tmp_path / "model_evaluations.jsonl")

        result = sfe.run("2026-08-22", snapshot_path, slate_path=slate_path, out_path=out_path, dry_run=True)
        assert result["rowsBuilt"] == 2
        assert result["rowsWritten"] == 0
        assert not os.path.exists(out_path)

    def test_main_no_snapshot_file_is_a_graceful_noop(self, tmp_path, capsys):
        missing_path = str(tmp_path / "does_not_exist.json")
        exit_code = sfe.main(["--snapshot-path", missing_path, "--date", "2026-08-22"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "NO_SNAPSHOT_FILE" in out

    def test_missing_slate_falls_back_to_empty_games_gracefully(self, tmp_path):
        snapshot_path = write_snapshot(str(tmp_path / "snap.json"), _markets())
        out_path = str(tmp_path / "model_evaluations.jsonl")
        result = sfe.run("2026-08-22", snapshot_path, slate_path=str(tmp_path / "no_such_slate.json"), out_path=out_path)
        assert result["rowsBuilt"] == 2  # still every contract, just unmatched to a real game
