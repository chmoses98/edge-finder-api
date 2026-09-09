#!/usr/bin/env python3
"""
tests/test_wave0_07_research_runs_persistence.py
================================================
WAVE 0.07. Guards the research-run manifest persistence defect that made five
of the ten CEO-authorized production restore runs fail closed.

THE DEFECT
----------
`edgelab-postgame.yml` runs three steps that append a research-run manifest:

    build_recommendations.py   -> research_runs/<DATE>.jsonl      (date-scoped)
    settle_markets.py          -> research_runs/<DATE>.jsonl      (date-scoped)
    ingest_existing_bets.py    -> research_runs/<TODAY>.jsonl     (NOT date-scoped)

`ingest_existing_bets.py` re-ingests the WHOLE root ledger rather than one
slate date, so it partitions its manifest by wall-clock UTC today
(`date = started_at[:10]`, ingest_existing_bets.py:166). That is semantically
correct -- the run happened today and covered every date -- and it is
deliberately NOT changed to <DATE>, which would make the manifest claim a run
that did not happen.

The bug was that the workflow's commit step declared only
`research_runs/<DATE>.jsonl`. On a historical replay DATE != TODAY, so
`research_runs/<TODAY>.jsonl` was left as an undeclared, uncommitted working
tree change.

WHY AN UNDECLARED PATH IS FATAL RATHER THAN MERELY UNTIDY
---------------------------------------------------------
`git_data_commit.capture_append_only_deltas` only captures `.jsonl` files that
appear in the caller's `paths` allow-list. A file outside that list can never
enter the captured set, so it can never be reconciled -- and seven workflows
append to `research_runs/<TODAY>.jsonl`, each under its own concurrency group.
The moment any of them pushed first, postgame's `git rebase --autostash` hit an
unresolvable conflict on a path it had never declared and fail-closed.

Declaring the path is therefore not a cosmetic tidy-up: it is what ARMS the
append-only reconciliation that already exists. That is why no new concurrency
group and no broad serialization is needed.

The cost of the bug was not a lost manifest -- it was the loss of the entire
run's successfully computed settlement output, which is asserted below.
"""

import os
import re
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "edgelab-postgame.yml")
COMMITTER = os.path.join(ROOT, "scripts", "ci", "git_data_commit.py")
INGEST = os.path.join(ROOT, "scripts", "edgelab", "ingest_existing_bets.py")


def _workflow():
    with open(WORKFLOW) as handle:
        return handle.read()


# ── Structural guards ────────────────────────────────────────────────────────

def test_postgame_declares_the_run_date_research_runs_partition():
    """The fix itself. Without this line the job cannot reconcile the file."""
    body = _workflow()
    assert '"data/edgelab/research_runs/${{ env.RUN_DATE }}.jsonl"' in body, (
        "edgelab-postgame.yml must declare research_runs/<RUN_DATE>.jsonl among "
        "its commit paths; ingest_existing_bets.py writes it on every run")


def test_postgame_still_declares_the_slate_date_research_runs_partition():
    """The date-scoped writers still need their own partition declared."""
    assert '"data/edgelab/research_runs/${{ env.DATE }}.jsonl"' in _workflow()


def test_run_date_is_utc_to_match_the_writer():
    """
    RUN_DATE must be UTC. ingest_existing_bets.py stamps
    `datetime.now(timezone.utc)`, so computing RUN_DATE in any other zone would
    name the wrong partition for part of every day -- the exact failure this
    guard exists to prevent, reintroduced silently.
    """
    body = _workflow()
    match = re.search(r"RUN_DATE=\$\((.*?)\)", body)
    assert match, "RUN_DATE must be computed in the workflow"
    command = match.group(1)
    assert "date -u" in command, (
        "RUN_DATE must be computed with `date -u` (UTC) to match "
        "ingest_existing_bets.py's datetime.now(timezone.utc); got %r" % command)
    assert "TZ=" not in command


def test_ingest_existing_bets_still_partitions_by_run_date_not_slate_date():
    """
    Pins the semantics the workflow fix depends on. If this script ever becomes
    date-scoped, RUN_DATE stops being the right partition and this whole guard
    set must be revisited rather than silently drifting.
    """
    with open(INGEST) as handle:
        source = handle.read()
    assert "date = started_at[:10]" in source, (
        "ingest_existing_bets.py is expected to partition its run manifest by "
        "wall-clock run date; if that changed, edgelab-postgame.yml's RUN_DATE "
        "declaration must change with it")
    assert "--date" not in source, (
        "ingest_existing_bets.py is deliberately not date-scoped")


def test_no_broad_global_serialization_was_introduced():
    """
    The fix must not paper over the race with a repo-wide writer lock. The
    append-only reconciliation handles concurrent appenders once the path is
    declared, so postgame keeps its own narrow concurrency group.
    """
    body = _workflow()
    match = re.search(r"^concurrency:\s*\n\s*group:\s*(\S+)", body, re.M)
    assert match, "postgame must keep an explicit concurrency group"
    assert match.group(1) == "edgelab-postgame", (
        "postgame's concurrency group changed to %r -- a shared/global writer "
        "lock serialises unrelated workflows and is not required here"
        % match.group(1))


# ── Behavioral guards ────────────────────────────────────────────────────────

def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _build_two_writer_race(tmp_path):
    """
    Builds a real git remote in tmp_path where another workflow has already
    appended to research_runs/<TODAY>.jsonl and pushed, while our run has a
    local append to that same file plus genuine settlement output.

    Returns (work_dir, remote_dir). Everything lives under tmp_path; no
    repository state outside it is touched.
    """
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)],
                   check=True, capture_output=True)
    for repo, who in ((work, "postgame"), ):
        _git(["config", "user.email", "%s@test" % who], repo)
        _git(["config", "user.name", who], repo)
    _git(["checkout", "-q", "-b", "main"], work)

    runs = work / "data" / "edgelab" / "research_runs"
    settles = work / "data" / "edgelab" / "settlements"
    runs.mkdir(parents=True)
    settles.mkdir(parents=True)
    (runs / "2026-09-09.jsonl").write_text('{"runId":"base"}\n')
    (settles / "2026-09-01.jsonl").write_text('{"settlementId":"s0"}\n')
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "base"], work)
    _git(["push", "-q", "-u", "origin", "main"], work)

    # A different workflow appends to the shared run-date partition and wins
    # the race to origin/main.
    subprocess.run(["git", "clone", "-q", str(remote), str(other)],
                   check=True, capture_output=True)
    _git(["config", "user.email", "other@test"], other)
    _git(["config", "user.name", "other"], other)
    other_runs = other / "data" / "edgelab" / "research_runs" / "2026-09-09.jsonl"
    other_runs.write_text(other_runs.read_text() + '{"runId":"otherworkflow"}\n')
    _git(["add", "-A"], other)
    _git(["commit", "-qm", "other workflow append"], other)
    _git(["push", "-q", "origin", "main"], other)

    # Our run's own output: a real settlement row, plus our run manifest.
    ours = runs / "2026-09-09.jsonl"
    ours.write_text(ours.read_text() + '{"runId":"postgame"}\n')
    s = settles / "2026-09-01.jsonl"
    s.write_text(s.read_text() + '{"settlementId":"s1"}\n')
    return work, remote


def _committed(work, path):
    _git(["fetch", "-q", "origin", "main"], work)
    out = _git(["show", "origin/main:%s" % path], work)
    return out.stdout


def test_declaring_the_run_date_partition_reconciles_two_concurrent_writers(tmp_path):
    """
    The fix, end to end: with the path declared, neither writer is destroyed --
    upstream's rows are preserved in order and ours are appended after.
    """
    work, _ = _build_two_writer_race(tmp_path)
    proc = subprocess.run(
        [sys.executable, COMMITTER, "--message", "postgame", "--branch", "main",
         "data/edgelab/settlements/2026-09-01.jsonl",
         "data/edgelab/research_runs/2026-09-09.jsonl"],
        cwd=str(work), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]

    runs = _committed(work, "data/edgelab/research_runs/2026-09-09.jsonl")
    assert '"otherworkflow"' in runs, "the other workflow's record was destroyed"
    assert '"postgame"' in runs, "this run's own record was lost"
    assert runs.index("otherworkflow") < runs.index("postgame"), (
        "upstream records must never be reordered behind local ones")

    settlements = _committed(work, "data/edgelab/settlements/2026-09-01.jsonl")
    assert '"s1"' in settlements, "core settlement output must land"


def test_an_undeclared_run_date_partition_discards_good_settlement_output(tmp_path):
    """
    The regression proof, and the reason this mattered enough to fix.

    This reproduces the exact production failure: with research_runs/<TODAY>
    undeclared, the job fail-closes and the settlement rows it had already
    computed correctly never reach main. Five of the ten restore runs died
    here. The assertion is deliberately on the LOST SETTLEMENT, not on the
    error text -- the manifest was never the valuable artifact.
    """
    work, _ = _build_two_writer_race(tmp_path)
    proc = subprocess.run(
        [sys.executable, COMMITTER, "--message", "postgame", "--branch", "main",
         "data/edgelab/settlements/2026-09-01.jsonl"],   # <TODAY> NOT declared
        cwd=str(work), capture_output=True, text=True, timeout=300)

    assert proc.returncode != 0, (
        "an unreconcilable conflict must fail closed, never commit partially")
    assert "not provably append-only JSONL" in proc.stderr
    assert "research_runs/2026-09-09.jsonl" in proc.stderr

    settlements = _committed(work, "data/edgelab/settlements/2026-09-01.jsonl")
    assert '"s1"' not in settlements, (
        "this test is meant to demonstrate the LOSS of good settlement output")
    assert '"s0"' in settlements, "main must be left exactly as it was"


def test_the_same_path_declared_twice_is_harmless(tmp_path):
    """
    On a same-day run DATE == RUN_DATE, so the workflow passes the identical
    path twice. That must be a no-op, not a double-append or an error.
    """
    work, _ = _build_two_writer_race(tmp_path)
    proc = subprocess.run(
        [sys.executable, COMMITTER, "--message", "postgame", "--branch", "main",
         "data/edgelab/research_runs/2026-09-09.jsonl",
         "data/edgelab/research_runs/2026-09-09.jsonl"],
        cwd=str(work), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    runs = _committed(work, "data/edgelab/research_runs/2026-09-09.jsonl")
    assert runs.count('"postgame"') == 1, (
        "a duplicated path must not append this run's record twice")
