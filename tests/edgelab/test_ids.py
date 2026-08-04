#!/usr/bin/env python3
"""
tests/edgelab/test_ids.py
=============================
Research-Run Manifest Identity fix: lib/edgelab/ids.py's new_run_id must
not collide when two invocations of the same run_type happen inside the
same GitHub Actions run within the same wall-clock second -- confirmed
by a real CI failure (tests/edgelab/test_ingest_market_observations_script.py)
where two ingestion invocations under the same GITHUB_RUN_ID produced
identical run ids with no content_signature, so
storage.append_records' dedup-by-runId silently discarded the second
invocation's manifest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.ids import build_run_content_signature, new_run_id


def test_local_run_without_github_run_id_is_always_unique():
    a = new_run_id("MARKET_OBSERVATION_INGEST")
    b = new_run_id("MARKET_OBSERVATION_INGEST")
    assert a != b


def test_same_github_run_id_and_second_without_content_signature_still_unique():
    """Preserves the old uniqueness guarantee (random suffix) for callers that don't yet pass a content_signature."""
    a = new_run_id("SETTLEMENT", github_run_id="12345")
    b = new_run_id("SETTLEMENT", github_run_id="12345")
    assert a != b
    assert "gh12345" in a and "gh12345" in b


def test_same_github_run_id_different_content_signature_produces_distinct_ids():
    """The exact bug scenario: two invocations in the same run/second, distinguished by what they actually processed."""
    sig_a = build_run_content_signature("kalshi_registry_snapshots", "snap_2200.json")
    sig_b = build_run_content_signature("kalshi_registry_snapshots", "snap_2200.json", "snap_2230.json")
    run_a = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", content_signature=sig_a)
    run_b = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", content_signature=sig_b)
    assert run_a != run_b


def test_same_content_signature_is_deterministic_for_a_true_retry():
    """A true retry of the exact same inputs re-derives the SAME run id -- write-once semantics, not a random duplicate."""
    sig = build_run_content_signature("kalshi_registry_snapshots", "snap_2200.json")
    run_1 = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", content_signature=sig)
    run_2 = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", content_signature=sig)
    # Both calls happen within the same test, i.e. the same wall-clock
    # second in practice -- if this assertion ever becomes flaky because
    # the two calls straddle a second boundary, that is precisely the
    # bug this fix targets (content_signature, not wall-clock time, must
    # be what makes them equal).
    assert run_1 == run_2


def test_run_attempt_distinguishes_a_manual_reattempt():
    sig = build_run_content_signature("kalshi_registry_snapshots", "snap_2200.json")
    attempt_1 = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", github_run_attempt="1", content_signature=sig)
    attempt_2 = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", github_run_attempt="2", content_signature=sig)
    assert attempt_1 != attempt_2
    assert "a1" in attempt_1 and "a2" in attempt_2


def test_run_id_is_still_auditable_back_to_the_github_run():
    sig = build_run_content_signature("kalshi_registry_snapshots", "snap_2200.json")
    run_id = new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999888", content_signature=sig)
    assert "gh999888" in run_id
    assert run_id.startswith("MARKET_OBSERVATION_INGEST_")


def test_content_signature_is_order_sensitive_so_callers_must_pre_sort():
    """
    build_run_content_signature itself does NOT sort its arguments --
    callers (e.g. ingest_market_observations.py) must pass already-sorted
    paths, so a true retry deterministically matches regardless of
    filesystem glob ordering, and two invocations with a genuinely
    different (but coincidentally same-length) input set aren't
    accidentally treated as identical just because order differed.
    """
    assert build_run_content_signature("src", "a.json", "b.json") == build_run_content_signature("src", "a.json", "b.json")
    assert build_run_content_signature("src", "a.json", "b.json") != build_run_content_signature("src", "b.json", "a.json")
