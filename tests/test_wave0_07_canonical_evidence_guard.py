#!/usr/bin/env python3
"""
tests/test_wave0_07_canonical_evidence_guard.py
===============================================
WAVE 0.07. Mutation tests for the canonical-evidence guard in tests/conftest.py.

A guard that has never been shown to fail is not a guard. Wave 0.05A's original
guard passed for a month while `tests/edgelab/test_frozen_forward_scorer.py`
rewrote canonical analytics on every run, because its watch list happened not to
include the two files that test wrote. So this module does not assert that the
guard is configured a particular way -- it MUTATES a throwaway git repository
and asserts the guard actually reports each mutation, by name.

Every test here builds its own repository under tmp_path. None of them touch
this repository's real canonical data.
"""

import os
import subprocess

import pytest

from conftest import (  # noqa: E402  -- tests/ is on sys.path under pytest
    CANONICAL_EVIDENCE_TREES,
    diff_status,
    git_status_map,
)


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    """A throwaway repo carrying a miniature of the canonical corpus."""
    _git(["init", "-q", "-b", "main", "."], tmp_path)
    _git(["config", "user.email", "t@test"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    seeded = {
        "data/edgelab/settlements/2026-09-01.jsonl": '{"settlementId":"s1"}\n',
        "data/edgelab/recommendations/2026-09-01.jsonl": '{"recommendationId":"r1"}\n',
        "data/edgelab/model_evaluations/2026-09-01.jsonl": '{"modelEvaluationId":"m1"}\n',
        "data/edgelab/games/2026-09-01.jsonl": '{"gameId":"g1"}\n',
        "data/edgelab/health/2026-09-08.json": '{"date":"2026-09-08"}\n',
        "data/edgelab/bets/bets.jsonl": '{"betId":"b1"}\n',
    }
    for rel, body in seeded.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body)
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-qm", "seed canonical corpus"], tmp_path)
    return tmp_path


def _drift(repo, mutate):
    before = git_status_map(str(repo), CANONICAL_EVIDENCE_TREES)
    mutate()
    after = git_status_map(str(repo), CANONICAL_EVIDENCE_TREES)
    return diff_status(before, after)


def test_a_clean_corpus_reports_no_drift(repo):
    """Guard the guard: no mutation must mean no findings."""
    assert _drift(repo, lambda: None) == []


# ── The three mutations the mission requires ─────────────────────────────────

def test_mutation_1_settlement_modification_is_caught(repo):
    """A restored settlement partition edited in place."""
    target = repo / "data/edgelab/settlements/2026-09-01.jsonl"
    drift = _drift(repo, lambda: target.write_text('{"settlementId":"TAMPERED"}\n'))
    assert drift, "modifying a settlement partition was not detected"
    assert any("data/edgelab/settlements/2026-09-01.jsonl" in d for d in drift), drift


def test_mutation_2_recommendation_modification_is_caught(repo):
    """A restored recommendation partition edited in place."""
    target = repo / "data/edgelab/recommendations/2026-09-01.jsonl"
    drift = _drift(repo, lambda: target.write_text('{"recommendationId":"TAMPERED"}\n'))
    assert drift, "modifying a recommendation partition was not detected"
    assert any("data/edgelab/recommendations/2026-09-01.jsonl" in d for d in drift), drift


def test_mutation_3_new_health_file_creation_is_caught(repo):
    """
    The case a pure content-hash-of-known-files guard cannot see at all, and the
    one that actually happened: a diagnostic run left an untracked
    data/edgelab/health/<today>.json behind.
    """
    target = repo / "data/edgelab/health/2026-09-09.json"
    drift = _drift(repo, lambda: target.write_text('{"date":"2026-09-09"}\n'))
    assert drift, "creating a canonical-looking health file was not detected"
    assert any("data/edgelab/health/2026-09-09.json" in d for d in drift), drift


# ── The other required properties ────────────────────────────────────────────

def test_deletion_is_caught(repo):
    target = repo / "data/edgelab/settlements/2026-09-01.jsonl"
    drift = _drift(repo, target.unlink)
    assert any("data/edgelab/settlements/2026-09-01.jsonl" in d for d in drift), drift


def test_the_offending_path_is_named(repo):
    """A finding that does not name the file is not actionable."""
    target = repo / "data/edgelab/games/2026-09-01.jsonl"
    drift = _drift(repo, lambda: target.write_text('{"gameId":"TAMPERED"}\n'))
    assert len(drift) == 1
    assert "data/edgelab/games/2026-09-01.jsonl" in drift[0]


def test_pre_existing_dirty_state_is_not_a_false_failure(repo):
    """
    The requirement that stops this guard becoming noise. Production workflows
    legitimately leave modified canonical files in a working tree. A file that
    was already dirty when the session started must not be reported.
    """
    target = repo / "data/edgelab/settlements/2026-09-01.jsonl"
    target.write_text('{"settlementId":"written-by-a-workflow"}\n')   # BEFORE baseline
    assert _drift(repo, lambda: None) == [], (
        "a file already dirty at session start must not be reported as drift")


def test_pre_existing_dirty_file_is_still_caught_if_the_suite_changes_it_again(repo):
    """
    ...but pre-existing dirt must not become a blanket exemption: a file that
    was already modified and is then modified AGAIN by the suite is real drift.
    """
    target = repo / "data/edgelab/settlements/2026-09-01.jsonl"
    target.write_text('{"settlementId":"workflow"}\n')

    before = git_status_map(str(repo), CANONICAL_EVIDENCE_TREES)
    target.unlink()                      # the suite deletes it outright
    after = git_status_map(str(repo), CANONICAL_EVIDENCE_TREES)
    drift = diff_status(before, after)
    assert any("data/edgelab/settlements/2026-09-01.jsonl" in d for d in drift), drift


def test_the_guard_covers_every_restored_canonical_family():
    """The scope the mission specifies, pinned so it cannot silently narrow."""
    for family in ("settlements", "recommendations", "model_evaluations",
                   "games", "health"):
        assert "data/edgelab/%s" % family in CANONICAL_EVIDENCE_TREES, (
            "%s is a restored canonical family and must be guarded" % family)


def test_the_guard_is_cheap_enough_for_routine_ci():
    """
    Overhead requirement. The whole point of using git status over content
    hashing is that it does not read 290 MB of corpus on every pytest run.
    """
    import time
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    start = time.time()
    git_status_map(root, CANONICAL_EVIDENCE_TREES)
    elapsed = time.time() - start
    assert elapsed < 5.0, (
        "canonical-evidence guard took %.2fs; it runs on every invocation of "
        "the suite and must stay cheap" % elapsed)


def test_a_non_git_checkout_does_not_break_collection(tmp_path):
    """This guard must never be the reason a test run cannot start."""
    assert git_status_map(str(tmp_path), CANONICAL_EVIDENCE_TREES) == {}
