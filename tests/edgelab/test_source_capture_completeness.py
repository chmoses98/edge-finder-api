#!/usr/bin/env python3
"""
tests/edgelab/test_source_capture_completeness.py
=================================================
PHASE 8: a run over a truncated capture must not report a clean success.

api/kalshisearch.js has always recorded its own partial fetches into
every snapshot it writes -- fetchFailures, fetchFailureCount,
priceFetchFailureCount. Nothing read them. Measured over 21 days of
retained snapshots: 9 of 106 captures were partial, every one an HTTP 429
from Kalshi, and all 227 ingest runs reported status=success anyway.

The loss is systematic, not random. fetchAllPages breaks out of its page
loop on a non-ok response and the 17 series are fetched sequentially, so
a rate limit truncates whichever series come LAST -- KXMLBHRR, KXMLBRBI,
KXMLBSB and KXMLBTB, the high-volume hitter prop families, every time. So
the failing series have to be nameable downstream, not just counted.
"""
import copy
import importlib.util
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import mlb_schedule, storage
from lib.edgelab.market_universe import (
    BROAD_DISCOVERY_SCOPE, read_snapshot_fetch_completeness,
    snapshot_fetch_completeness,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")
DATE = "2026-07-31"


def _fail(series, status=429):
    return {"scope": "series", "page": 1, "status": status,
            "url": "https://api.elections.kalshi.com/trade-api/v2/markets"
                   "?series_ticker=%s&status=open&limit=200" % series}


# --------------------------------------------------------------------------
# the classifier
# --------------------------------------------------------------------------

def test_a_clean_snapshot_is_complete():
    result = snapshot_fetch_completeness({"fetchFailureCount": 0, "fetchFailures": []})
    assert result["isComplete"] is True
    assert result["failedSeries"] == []


def test_a_429_names_the_series_it_truncated():
    """Which series failed is the whole point: the loss is systematic."""
    result = snapshot_fetch_completeness({
        "fetchFailureCount": 2, "priceFetchFailureCount": 2,
        "fetchFailures": [_fail("KXMLBHRR"), _fail("KXMLBRBI")]})
    assert result["isComplete"] is False
    assert result["failedSeries"] == ["KXMLBHRR", "KXMLBRBI"]
    assert result["priceFetchFailureCount"] == 2


def test_the_unfiltered_discovery_pass_has_no_series_to_name():
    result = snapshot_fetch_completeness({
        "fetchFailureCount": 1,
        "fetchFailures": [{"scope": "discovery", "status": 429,
                           "url": "https://api.elections.kalshi.com/trade-api/v2/markets?status=open"}]})
    assert result["failedSeries"] == [BROAD_DISCOVERY_SCOPE]
    assert result["isComplete"] is False


def test_a_count_with_no_detail_is_still_incomplete():
    """A snapshot that admits failures but lists none is not thereby clean."""
    assert snapshot_fetch_completeness({"fetchFailureCount": 3})["isComplete"] is False


def test_detail_with_no_count_is_still_incomplete():
    assert snapshot_fetch_completeness(
        {"fetchFailures": [_fail("KXMLBSB")]})["isComplete"] is False


def test_a_snapshot_predating_the_failure_fields_is_not_assumed_broken():
    """Absence of the keys entirely means an older writer, not a failure."""
    assert snapshot_fetch_completeness({"markets": []})["isComplete"] is True


def test_repeated_failures_on_one_series_are_reported_once():
    result = snapshot_fetch_completeness({
        "fetchFailureCount": 3,
        "fetchFailures": [_fail("KXMLBHRR"), _fail("KXMLBHRR"), _fail("KXMLBRBI")]})
    assert result["failedSeries"] == ["KXMLBHRR", "KXMLBRBI"]
    assert result["fetchFailureCount"] == 3     # the count is not deduped


def test_an_unreadable_snapshot_is_never_reported_complete(tmp_path):
    """Absence of evidence is not evidence of completeness."""
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    assert read_snapshot_fetch_completeness(str(bad))["isComplete"] is False
    assert read_snapshot_fetch_completeness(str(tmp_path / "nope.json"))["isComplete"] is False


# --------------------------------------------------------------------------
# the run record
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_real_mlb_schedule_network_call(monkeypatch):
    monkeypatch.setattr(mlb_schedule, "fetch_schedule", lambda date, timeout=15: None)


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest_script = _load_script("ingest_market_observations.py")


def _seed(filename, *, failures=None):
    with open(FIXTURE) as fh:
        data = copy.deepcopy(json.load(fh))
    if failures is not None:
        data["fetchFailures"] = failures
        data["fetchFailureCount"] = len(failures)
        data["priceFetchFailureCount"] = len(failures)
    snap_dir = os.path.join("data", "kalshi_registry_snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    with open(os.path.join(snap_dir, filename), "w") as fh:
        json.dump(data, fh)


def _run(tmp_path, monkeypatch, *, failures=None):
    monkeypatch.chdir(tmp_path)
    _seed("kalshi_search_%s_2200.json" % DATE, failures=failures)
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    assert ingest_script.main() == 0
    return list(storage.read_records(storage.partition_path("research_runs", DATE)))[-1]


def test_a_complete_capture_still_reports_success(tmp_path, monkeypatch):
    """The guard must not make every run look broken."""
    run = _run(tmp_path, monkeypatch)
    assert run["status"] == "success"
    assert run["counts"]["snapshotsWithIncompleteFetch"] == 0
    assert run["counts"]["seriesTruncatedAtSource"] == []


def test_a_truncated_capture_is_no_longer_reported_as_success(tmp_path, monkeypatch):
    """The production symptom: 227 of 227 runs said success, 9 captures were partial."""
    run = _run(tmp_path, monkeypatch, failures=[_fail("KXMLBHRR"), _fail("KXMLBSB")])
    assert run["status"] == "partial"
    assert run["counts"]["snapshotsWithIncompleteFetch"] == 1
    assert run["counts"]["sourceFetchFailures"] == 2


def test_the_truncated_series_are_named_in_the_run_record(tmp_path, monkeypatch):
    """A downstream consumer must be able to say WHICH families are biased."""
    run = _run(tmp_path, monkeypatch, failures=[_fail("KXMLBSB"), _fail("KXMLBHRR")])
    assert run["counts"]["seriesTruncatedAtSource"] == ["KXMLBHRR", "KXMLBSB"]


def test_the_warning_says_missing_from_the_snapshot_not_absent_from_the_exchange(
        tmp_path, monkeypatch):
    """The distinction that makes the warning actionable."""
    run = _run(tmp_path, monkeypatch, failures=[_fail("KXMLBRBI")])
    warning = next(w for w in run["warnings"] if w.startswith("INCOMPLETE_SOURCE_CAPTURE"))
    assert "KXMLBRBI" in warning
    assert "not absent from the exchange" in warning


def test_a_truncated_capture_still_archives_what_it_did_receive(tmp_path, monkeypatch):
    """Fail-closed on the STATUS, not on the data. Throwing away the markets
    that did arrive would turn a partial loss into a total one."""
    run = _run(tmp_path, monkeypatch, failures=[_fail("KXMLBHRR")])
    assert run["counts"]["observationsWritten"] == 31
    observations = list(storage.read_records(
        storage.partition_path("observations", DATE, compressed=True)))
    assert len(observations) == 31


def test_status_is_partial_for_a_truncated_capture_with_no_errors(tmp_path, monkeypatch):
    """Previously `partial` meant only that a snapshot failed to PARSE. A
    snapshot that parses perfectly but holds two thirds of the exchange is
    equally partial, and nothing had ever said so."""
    run = _run(tmp_path, monkeypatch, failures=[_fail("KXMLBTB")])
    assert run["errors"] == []
    assert run["status"] == "partial"
