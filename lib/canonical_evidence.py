#!/usr/bin/env python3
"""
lib/canonical_evidence.py
=========================
The one list of canonical production evidence, and nothing else.

It lives here rather than in tests/conftest.py because two things need it and
only one of them is pytest:

  * tests/conftest.py guards the suite against mutating any of it;
  * scripts/ci/w1b1_evidence_fingerprint.py proves a CI job changed none of it.

The fingerprint script originally imported conftest directly to avoid a second
copy of the list. That was the right instinct and the wrong mechanism: conftest
imports pytest at module scope, pytest is not in requirements-ci.txt, and the
read-only rehearsal died on `ModuleNotFoundError: No module named 'pytest'`
before it ran a single check. A plain data module has no such dependency, so
both callers can share one definition without either dragging the other's
runtime along.

This module must stay import-light -- standard library only, no side effects.
"""

# Canonical production evidence. A test may READ any of these; none may be
# written by the suite. Paths that do not exist in a given checkout are skipped
# rather than treated as a failure.
CANONICAL_EVIDENCE = (
    "data/edgelab/bets/bets.jsonl",
    "data/edgelab/analytics/clv_sign_migration_manifest.json",
    "data/edgelab/analytics/clv_sign_migration_receipt.json",
    "bets.json",
    "BET_LOG.md",
    "data/kalshi_market_registry.json",
    # WAVE 0 EXIT. Added after the production restore of 2026-09-01..09-06.
    # The research scorecard runner's own test file called that runner's
    # `main()` five times with no output override, so
    # every pytest run rewrote both of these in the working tree. The original
    # list enumerated only the artifacts implicated in the 2026-09-08 incident,
    # so the same defect class in a different file went unseen; the restore
    # surfaced it by changing the numbers the scorer produces (n=329 -> n=1811).
    "docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md",
)

# Whole directories of derived canonical evidence. Enumerating a directory
# rather than individual files is what generalises this guard beyond the
# specific artifacts a past incident happened to touch: a NEW analytics file
# written by the suite is caught as a creation, and there is no list to keep in
# sync. Non-existent directories are skipped.
CANONICAL_EVIDENCE_DIRS = (
    "data/edgelab/analytics",
)

# WAVE 0.07. The restored canonical corpus -- the 29,427 recommendations and
# 28,781 settlements the production restore put on main, plus every other
# durable EdgeLab family. The suite must not be able to modify, delete, or add
# to any of these.
#
# These are guarded by a git-status baseline diff rather than by content
# hashing, because hashing them is the wrong tool at this size. Measured on
# this repository: sha256 over settlements/recommendations/model_evaluations/
# games/health/analytics reads 74.6 MB and takes ~3.5s, and including
# snapshots/ would add another 214 MB across 2,063 files. The same coverage via
# `git status --porcelain` over all of them takes ~0.02s, and is strictly
# BROADER: it reports modifications, deletions AND untracked creations, naming
# each offending path, without reading file contents at all.
#
# The baseline diff is what keeps this from firing on legitimate pre-existing
# state: a workflow-written file that was already dirty when pytest started
# appears identically in the before and after snapshots and is ignored. Only a
# path whose git status CHANGED during the session is reported.
CANONICAL_EVIDENCE_TREES = (
    "data/edgelab/settlements",
    "data/edgelab/recommendations",
    "data/edgelab/model_evaluations",
    "data/edgelab/games",
    "data/edgelab/health",
    "data/edgelab/analytics",
    "data/edgelab/snapshots",
    "data/edgelab/research_runs",
    "data/edgelab/bets",
)
