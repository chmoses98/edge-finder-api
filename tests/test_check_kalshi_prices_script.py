#!/usr/bin/env python3
"""
tests/test_check_kalshi_prices_script.py
=============================================
Tests for scripts/check_kalshi_prices.py -- CLI orchestration, source
mode resolution, exit codes, output formats, caching, and the
"standalone from the full slate pipeline" requirement.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def ckp(monkeypatch):
    if "check_kalshi_prices" in sys.modules:
        del sys.modules["check_kalshi_prices"]
    import check_kalshi_prices as _ckp
    return _ckp


def _fake_snapshot(tmp_path, markets, fetched_at="2026-07-29T17:22:01.000Z"):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    path = snap_dir / "kalshi_search_2026-07-29_1722.json"
    path.write_text(json.dumps({"fetched_at": fetched_at, "markets": markets}))
    return str(path)


F5_TIE = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
          "title": "tie after 5", "yes_bid": 0.17, "yes_ask": 0.19, "status": "open"}


class TestSnapshotMode:

    def test_snapshot_mode_reads_specified_file(self, ckp, tmp_path, monkeypatch):
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        records = json.loads(output)
        assert len(records) == 1
        assert result["metadata"]["sourceUsed"].startswith("snapshot:")
        assert result["metadata"]["pricesMayBeStale"] is True

    def test_snapshot_mode_never_attempts_network(self, ckp, tmp_path, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("snapshot mode must never call fetch_live")
        monkeypatch.setattr(ckp, "fetch_live", _boom)
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0

    def test_snapshot_mode_missing_file_fails_clearly(self, ckp):
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", "/nonexistent/path.json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code != 0
        assert "FETCH ERROR" in output


class TestLiveMode:

    def test_live_failure_returns_nonzero_no_fallback(self, ckp, monkeypatch):
        def _boom():
            raise ckp.FetchError("simulated live failure")
        monkeypatch.setattr(ckp, "fetch_live", _boom)
        monkeypatch.setattr(ckp, "read_cache", lambda ttl: None)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "live"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 1
        assert "FETCH ERROR" in output

    def test_live_success_labels_source_used_live(self, ckp, monkeypatch, tmp_path):
        monkeypatch.setattr(ckp, "fetch_live", lambda *a, **kw: ({"markets": [F5_TIE]}, 200, "https://example.test/api/kalshisearch", 123))
        monkeypatch.setattr(ckp, "read_cache", lambda ttl: None)
        monkeypatch.setattr(ckp, "write_cache", lambda data, fetch_info: None)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "live", "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        assert result["metadata"]["sourceUsed"] == "live"
        assert result["metadata"]["pricesMayBeStale"] is False


class TestAutoMode:

    def test_auto_falls_back_to_snapshot_on_live_failure(self, ckp, monkeypatch, tmp_path):
        def _boom():
            raise ckp.FetchError("network blocked")
        monkeypatch.setattr(ckp, "fetch_live", _boom)
        monkeypatch.setattr(ckp, "read_cache", lambda ttl: None)
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "auto", "--snapshot-path", snap_path, "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        assert result["metadata"]["sourceUsed"].startswith("snapshot:")
        assert result["metadata"]["fallbackReason"] is not None

    def test_auto_uses_live_when_available(self, ckp, monkeypatch):
        monkeypatch.setattr(ckp, "fetch_live", lambda *a, **kw: ({"markets": [F5_TIE]}, 200, "https://example.test/api/kalshisearch", 123))
        monkeypatch.setattr(ckp, "read_cache", lambda ttl: None)
        monkeypatch.setattr(ckp, "write_cache", lambda data, fetch_info: None)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "auto", "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        assert result["metadata"]["sourceUsed"] == "live"

    def test_auto_fails_when_both_live_and_snapshot_unavailable(self, ckp, monkeypatch, tmp_path):
        def _boom():
            raise ckp.FetchError("network blocked")
        monkeypatch.setattr(ckp, "fetch_live", _boom)
        monkeypatch.setattr(ckp, "read_cache", lambda ttl: None)
        monkeypatch.setattr(ckp, "find_latest_snapshot", lambda: None)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "auto"])
        exit_code, output, result = ckp.run(args)
        assert exit_code != 0


class TestExitCodes:

    def test_empty_result_is_exit_zero(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--team", "ZZZNOTATEAM"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        assert "No matching" in output

    def test_genuine_failure_is_nonzero(self, ckp):
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", "/nope.json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code != 0


class TestOutputFormats:

    def test_json_output_is_valid(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "json"])
        exit_code, output, result = ckp.run(args)
        json.loads(output)  # must not raise

    def test_csv_output_is_valid(self, ckp, tmp_path):
        import csv
        import io
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "csv"])
        exit_code, output, result = ckp.run(args)
        rows = list(csv.DictReader(io.StringIO(output)))
        assert len(rows) == 1

    def test_stale_snapshot_output_labeled(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "table"])
        exit_code, output, result = ckp.run(args)
        assert "SNAPSHOT PRICE" in output


class TestCache:

    def test_cache_write_then_read_within_ttl(self, ckp, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(ckp, "CACHE_DIR", str(cache_dir))
        monkeypatch.setattr(ckp, "CACHE_FILE", str(cache_dir / "live_response.json"))
        ckp.write_cache({"markets": [F5_TIE]}, {"endpoint": "https://example.test/api/kalshisearch", "httpStatus": 200})
        cached = ckp.read_cache(ttl_seconds=60)
        assert cached is not None
        assert cached["markets"] == [F5_TIE]

    def test_cache_expired_returns_none(self, ckp, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(ckp, "CACHE_DIR", str(cache_dir))
        monkeypatch.setattr(ckp, "CACHE_FILE", str(cache_dir / "live_response.json"))
        ckp.write_cache({"markets": [F5_TIE]}, {"endpoint": "https://example.test/api/kalshisearch", "httpStatus": 200})
        cached = ckp.read_cache(ttl_seconds=0)
        assert cached is None

    def test_corrupt_cache_does_not_crash(self, ckp, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / "live_response.json"
        cache_file.write_text("{not valid json")
        monkeypatch.setattr(ckp, "CACHE_DIR", str(cache_dir))
        monkeypatch.setattr(ckp, "CACHE_FILE", str(cache_file))
        assert ckp.read_cache(ttl_seconds=60) is None

    def test_cache_never_contains_credentials(self, ckp, monkeypatch, tmp_path):
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(ckp, "CACHE_DIR", str(cache_dir))
        monkeypatch.setattr(ckp, "CACHE_FILE", str(cache_dir / "live_response.json"))
        ckp.write_cache({"markets": [F5_TIE]}, {"endpoint": "https://example.test/api/kalshisearch", "httpStatus": 200})
        raw = (cache_dir / "live_response.json").read_text()
        assert "api_key" not in raw.lower()
        assert "token" not in raw.lower()
        assert "credential" not in raw.lower()


class TestPaginationExhaustive:

    def test_all_markets_in_snapshot_considered_without_max_results(self, ckp, tmp_path):
        markets = [dict(F5_TIE, market_ticker=f"KXMLBF5-X-{i}") for i in range(50)]
        snap_path = _fake_snapshot(tmp_path, markets)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert len(json.loads(output)) == 50
        assert result["metadata"]["rawRecordsFetched"] == 50

    def test_max_results_caps_but_does_not_affect_raw_count_reporting(self, ckp, tmp_path):
        markets = [dict(F5_TIE, market_ticker=f"KXMLBF5-X-{i}") for i in range(50)]
        snap_path = _fake_snapshot(tmp_path, markets)
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path,
                                   "--format", "json", "--max-results", "5"])
        exit_code, output, result = ckp.run(args)
        assert len(json.loads(output)) == 5
        assert result["metadata"]["rawRecordsFetched"] == 50


PAST_START_MKT = {"market_ticker": "KXMLBF5-26JUL292210PITCIN-PIT", "event_ticker": "KXMLBF5-26JUL292210PITCIN",
                   "title": "Pittsburgh first 5 innings winner?", "yes_bid": 0.42, "yes_ask": 0.44,
                   "status": "open", "open_time": "2020-01-01T00:00:00Z"}
FUTURE_START_MKT = {"market_ticker": "KXMLBF5-26JUL292210NYYBOS-NYY", "event_ticker": "KXMLBF5-26JUL292210NYYBOS",
                     "title": "New York first 5 innings winner?", "yes_bid": 0.5, "yes_ask": 0.52,
                     "status": "open", "open_time": "2030-01-01T00:00:00Z"}


class TestGameSelectionAndNotStartedFilters:
    """
    End-to-end (CLI argparse -> run()) coverage for the standalone
    price-check usability mission's two new filters. Confirms the real
    wiring in scripts/check_kalshi_prices.py (build_filters() +
    apply_filters(..., as_of=retrieved_at)), not just the pure
    lib.kalshi_price_check functions these tests exercise directly
    elsewhere.
    """

    def test_games_flag_selects_exactly_one_game(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [PAST_START_MKT, FUTURE_START_MKT])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path,
                                   "--format", "json", "--games", "NYY@BOS"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        records = json.loads(output)
        assert len(records) == 1
        assert records[0]["matchup"] == "NYY@BOS"

    def test_games_flag_is_exact_not_substring(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [PAST_START_MKT, FUTURE_START_MKT])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path,
                                   "--format", "json", "--games", "PIT"])
        exit_code, output, result = ckp.run(args)
        assert json.loads(output) == []

    def test_games_flag_accepts_comma_separated_multiple_games(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [PAST_START_MKT, FUTURE_START_MKT])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path,
                                   "--format", "json", "--games", "PIT@CIN, NYY@BOS"])
        exit_code, output, result = ckp.run(args)
        assert len(json.loads(output)) == 2

    def test_exclude_started_removes_only_the_past_game(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [PAST_START_MKT, FUTURE_START_MKT])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path,
                                   "--format", "json", "--exclude-started"])
        exit_code, output, result = ckp.run(args)
        records = json.loads(output)
        assert len(records) == 1
        assert records[0]["matchup"] == "NYY@BOS"

    def test_exclude_started_off_by_default(self, ckp, tmp_path):
        snap_path = _fake_snapshot(tmp_path, [PAST_START_MKT, FUTURE_START_MKT])
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert len(json.loads(output)) == 2


class TestArchiveIdempotent:

    def test_repeated_archive_runs_produce_same_records(self, ckp, tmp_path):
        """
        Idempotent means "no duplication/corruption on rerun," not
        "byte-identical regardless of real time" -- retrievedAt is a
        genuine live timestamp and legitimately differs between two
        separate invocations. The market records themselves (the
        actual price data) must be identical given identical input.
        """
        snap_path = _fake_snapshot(tmp_path, [F5_TIE])
        archive_dir = tmp_path / "archive"
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", snap_path, "--format", "json"])
        _, _, result1 = ckp.run(args)
        path1 = ckp.write_archive(result1["records"], result1["metadata"], str(archive_dir))
        with open(path1[0]) as f:
            records1 = json.load(f)

        _, _, result2 = ckp.run(args)
        path2 = ckp.write_archive(result2["records"], result2["metadata"], str(archive_dir))
        with open(path2[0]) as f:
            records2 = json.load(f)

        def _strip_timestamps(recs):
            return [{k: v for k, v in r.items() if k != "retrievedAt"} for r in recs]

        assert _strip_timestamps(records1) == _strip_timestamps(records2)
        assert len(records1) == len(records2) == 1


class TestStandaloneFromSlatePipeline:

    def test_script_runs_via_subprocess_without_slate_json(self, tmp_path):
        """Runs the actual script as a subprocess in an empty working
        directory containing no data/slate.json at all, proving it
        never requires the slate pipeline to have run."""
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "kalshi_search_2026-07-29_1722.json").write_text(
            json.dumps({"fetched_at": "2026-07-29T17:22:01.000Z", "markets": [F5_TIE]})
        )
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot",
             "--snapshot-path", str(snap_dir / "kalshi_search_2026-07-29_1722.json"),
             "--format", "json"],
            cwd=str(tmp_path), capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        records = json.loads(result.stdout)
        assert len(records) == 1
