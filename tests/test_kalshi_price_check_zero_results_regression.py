#!/usr/bin/env python3
"""
tests/test_kalshi_price_check_zero_results_regression.py
==============================================================
Regression tests for the "successful run returns zero markets with no
explanation" bug.

Root cause (see docs/KALSHI_PRICE_CHECKER.md and this branch's commit
message for the full investigation): scripts/check_kalshi_prices.py's
run() always computed rich per-stage diagnostics, but main() never
persisted them anywhere the GitHub Actions job summary or artifact
upload steps could read -- they were only ever printed to stderr
behind --verbose. A second, independent bug: normalize_market()
hardcoded the `date` field to None for every record, so a --date
filter (or any future date-based diagnostic) could never work
correctly. This file proves both are fixed and stay fixed.
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

from lib.kalshi_price_check import (
    normalize_market,
    normalize_batch,
    apply_filters,
    find_zero_stage,
    diagnose_result,
    format_job_summary_markdown,
    parse_kalshi_event_date,
)


@pytest.fixture
def ckp():
    if "check_kalshi_prices" in sys.modules:
        del sys.modules["check_kalshi_prices"]
    import check_kalshi_prices as _ckp
    return _ckp


F5_AWAY = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-SEA", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
           "title": "Seattle first 5 innings winner?", "yes_bid": 0.42, "yes_ask": 0.44, "status": "open"}
F5_TIE = {"market_ticker": "KXMLBF5-26JUL292210SEALAD-TIE", "event_ticker": "KXMLBF5-26JUL292210SEALAD",
          "title": "tie after 5", "yes_bid": 0.17, "yes_ask": 0.19, "status": "open"}


class TestDateFieldBugFixed:
    """Regression: normalize_market() previously hardcoded date=None
    for every record, unconditionally."""

    def test_date_parsed_from_event_ticker(self):
        assert parse_kalshi_event_date("KXMLBF5-26JUL292210SEALAD") == "2026-07-29"

    def test_date_field_populated_not_none(self):
        record, status, reason = normalize_market(F5_AWAY)
        assert record["date"] == "2026-07-29"

    def test_date_filter_now_actually_works(self):
        records, _, _ = normalize_batch([F5_AWAY])
        kept, _ = apply_filters(records, {"date": "2026-07-29"})
        assert len(kept) == 1
        kept_wrong_date, _ = apply_filters(records, {"date": "2026-01-01"})
        assert len(kept_wrong_date) == 0

    def test_missing_event_ticker_date_is_honestly_none(self):
        record, status, reason = normalize_market({"market_ticker": "X-1"})
        assert record["date"] is None

    def test_doubleheader_suffix_date_still_parses(self):
        assert parse_kalshi_event_date("KXMLBF5-26JUL291310ATLNYMG1") == "2026-07-29"


class TestPerStageFilterDiagnostics:
    """Regression: apply_filters() previously returned only an
    aggregate dropped-count int, making "exactly which filter reduced
    the result to zero" unanswerable."""

    def test_stage_report_shape(self):
        records, _, _ = normalize_batch([F5_AWAY])
        kept, stage_report = apply_filters(records, {"team": "ZZZ"})
        assert "removedByStage" in stage_report
        assert "remainingAfterStage" in stage_report

    def test_find_zero_stage_identifies_exact_stage(self):
        records, _, _ = normalize_batch([F5_AWAY])
        kept, stage_report = apply_filters(records, {"team": "ZZZNOTATEAM"})
        assert len(kept) == 0
        assert find_zero_stage(stage_report["remainingAfterStage"]) == "team"

    def test_find_zero_stage_reports_first_stage_to_zero_not_a_later_one(self):
        """If 'team' zeroes the result, a later stage (e.g. 'family')
        must not be reported instead, even though it also "removed" 0
        records from an already-empty set."""
        records, _, _ = normalize_batch([F5_AWAY])
        kept, stage_report = apply_filters(records, {"team": "ZZZ", "family": "inning_result"})
        assert find_zero_stage(stage_report["remainingAfterStage"]) == "team"

    def test_find_zero_stage_none_when_result_nonzero(self):
        records, _, _ = normalize_batch([F5_AWAY])
        kept, stage_report = apply_filters(records, {})
        assert len(kept) == 1
        assert find_zero_stage(stage_report["remainingAfterStage"]) is None

    def test_max_results_stage_tracked(self):
        records, _, _ = normalize_batch([F5_AWAY, F5_TIE])
        kept, stage_report = apply_filters(records, {"max_results": 1})
        assert stage_report["removedByStage"]["max_results"] == 1


class TestDiagnoseResult:
    """Regression: a zero-result run previously had NO machine- or
    human-readable explanation anywhere -- diagnose_result() always
    returns one now."""

    def test_zero_raw_records_reason(self):
        assert "zero raw records" in diagnose_result(0, 0, {"remainingAfterStage": []}, 0).lower()

    def test_zero_normalized_reason(self):
        msg = diagnose_result(5, 0, {"remainingAfterStage": []}, 0)
        assert "normalization" in msg.lower()

    def test_zero_final_names_the_filter_stage(self):
        records, _, _ = normalize_batch([F5_AWAY])
        kept, stage_report = apply_filters(records, {"team": "ZZZ"})
        msg = diagnose_result(1, 1, stage_report, 0)
        assert "team" in msg

    def test_nonzero_result_reason_states_count(self):
        msg = diagnose_result(5, 5, {"remainingAfterStage": [("x", 3)]}, 3)
        assert "3 record(s) matched" in msg

    def test_diagnosis_never_empty_string(self):
        for raw, norm, final in [(0, 0, 0), (5, 0, 0), (5, 5, 0), (5, 5, 5)]:
            msg = diagnose_result(raw, norm, {"remainingAfterStage": []}, final)
            assert msg


class TestMetadataPersistedNotJustPrinted:
    """Regression: metadata was previously computed in run() but only
    ever printed to stderr behind --verbose -- nothing downstream
    (job summary, artifact upload) could ever read it. This is the
    core bug: main() must actually WRITE it when asked."""

    def test_run_metadata_includes_all_new_diagnostic_fields(self, ckp, tmp_path):
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        snap_path = snap_dir / "kalshi_search_2026-07-29.json"
        snap_path.write_text(json.dumps({"fetched_at": "2026-07-29T18:00:00Z", "markets": [F5_AWAY, F5_TIE]}))
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", str(snap_path), "--format", "json"])
        exit_code, output, result = ckp.run(args)
        metadata = result["metadata"]
        for key in ("fetchInfo", "classifiedCount", "unknownCount", "removedByFilterStage",
                    "remainingAfterFilterStage", "diagnosis"):
            assert key in metadata, f"missing diagnostic field: {key}"
        assert metadata["diagnosis"] == "2 record(s) matched."

    def test_metadata_output_flag_writes_a_real_file(self, ckp, tmp_path):
        """This is the literal bug: --metadata-output must actually
        produce a file main() writes -- not just something run()
        computes and main() discards."""
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        snap_path = snap_dir / "kalshi_search_2026-07-29.json"
        snap_path.write_text(json.dumps({"fetched_at": "2026-07-29T18:00:00Z", "markets": [F5_AWAY]}))
        meta_out = tmp_path / "meta.json"
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot", "--snapshot-path", str(snap_path),
             "--format", "json", "--metadata-output", str(meta_out)],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        assert meta_out.exists(), "--metadata-output must write a real file"
        metadata = json.loads(meta_out.read_text())
        assert metadata["rawRecordsFetched"] == 1
        assert metadata["resultCount"] == 1
        assert metadata["diagnosis"]

    def test_zero_result_run_still_writes_full_metadata(self, ckp, tmp_path):
        """The exact reported bug scenario: a run that returns zero
        markets must still produce a metadata file explaining why --
        not an empty/absent one."""
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        snap_path = snap_dir / "kalshi_search_2026-07-29.json"
        snap_path.write_text(json.dumps({"fetched_at": "2026-07-29T18:00:00Z", "markets": []}))
        meta_out = tmp_path / "meta.json"
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot", "--snapshot-path", str(snap_path),
             "--format", "json", "--metadata-output", str(meta_out)],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        metadata = json.loads(meta_out.read_text())
        assert metadata["rawRecordsFetched"] == 0
        assert metadata["resultCount"] == 0
        assert "zero raw records" in metadata["diagnosis"].lower()

    def test_diagnosis_always_printed_to_stderr_even_without_verbose(self, ckp, tmp_path):
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        snap_path = snap_dir / "kalshi_search_2026-07-29.json"
        snap_path.write_text(json.dumps({"fetched_at": "2026-07-29T18:00:00Z", "markets": []}))
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot", "--snapshot-path", str(snap_path), "--format", "json"],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert "zero raw records" in result.stderr.lower()


class TestFetchLiveDiagnostics:
    """Regression: fetch_live() previously returned only the parsed
    JSON body, with no way to know which endpoint was hit or what HTTP
    status came back."""

    def test_fetch_live_returns_status_endpoint_and_size(self, ckp, monkeypatch):
        import io
        from urllib.request import Request

        class _FakeResponse:
            status = 200
            def read(self):
                return b'{"markets": []}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def getcode(self):
                return 200

        monkeypatch.setattr(ckp, "urlopen", lambda req, timeout=15: _FakeResponse())
        data, status, endpoint, size = ckp.fetch_live()
        assert status == 200
        assert endpoint.endswith("/api/kalshisearch")
        assert size == len(b'{"markets": []}')
        assert data == {"markets": []}

    def test_fetch_error_message_includes_endpoint(self, ckp, monkeypatch):
        def _boom(req, timeout=15):
            raise OSError("connection refused")
        monkeypatch.setattr(ckp, "urlopen", _boom)
        with pytest.raises(ckp.FetchError) as exc_info:
            ckp.fetch_live()
        assert "endpoint=" in str(exc_info.value)


class TestJobSummaryFormatting:

    def test_format_job_summary_includes_all_counters(self):
        metadata = {
            "sourceUsed": "live",
            "fetchInfo": {"endpoint": "https://x/api/kalshisearch", "httpStatus": 200,
                          "responseSizeBytes": 642, "marketsKeyPresent": True},
            "rawRecordsFetched": 642, "normalizedRecordCount": 642,
            "classifiedCount": 601, "unknownCount": 41, "malformedRecordCount": 0,
            "removedByFilterStage": {"date": 570, "status": 18, "family": 5},
            "resultCount": 8, "diagnosis": "8 record(s) matched.",
        }
        text = format_job_summary_markdown(metadata)
        for expected in ("Source used", "Endpoint", "HTTP status", "642", "601", "41",
                          "Filtered by date", "570", "Returned", "8", "Reason"):
            assert expected in text

    def test_format_job_summary_zero_result_shows_reason(self):
        metadata = {
            "sourceUsed": "live",
            "fetchInfo": {"endpoint": "https://x/api/kalshisearch", "httpStatus": 200,
                          "responseSizeBytes": 2, "marketsKeyPresent": True},
            "rawRecordsFetched": 0, "normalizedRecordCount": 0,
            "classifiedCount": 0, "unknownCount": 0, "malformedRecordCount": 0,
            "removedByFilterStage": {}, "resultCount": 0,
            "diagnosis": "Live endpoint (or snapshot) returned zero raw records.",
        }
        text = format_job_summary_markdown(metadata)
        assert "zero raw records" in text
