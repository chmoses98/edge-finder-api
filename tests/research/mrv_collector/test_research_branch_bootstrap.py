"""
Regression for the first live MRV run (35925944608): the research branch did
not exist on the remote, the workflow created it only locally, and
scripts/ci/git_data_commit.py -- which starts with `git fetch origin <branch>`
-- refused every persist, so all captured rows stayed on the ephemeral runner.

These tests execute the workflow's REAL bootstrap step (extracted from the
YAML) against a local bare remote, then persist through the real
git_data_commit.py, and prove the rows land on the remote branch.
"""
import os
import subprocess
import sys

import pytest
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WF = os.path.join(REPO, ".github", "workflows", "research-mrv-prospective-capture.yml")
COMMIT = os.path.join(REPO, "scripts", "ci", "git_data_commit.py")
BRANCH = "research/mrv-prospective-v1"
P = "data/edgelab/research_artifacts/mrv_prospective/v1"


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _steps():
    with open(WF) as f:
        return yaml.safe_load(f)["jobs"]["capture"]["steps"]


def _step(name):
    return next(s for s in _steps() if s.get("name") == name)


def _bootstrap_script():
    """The branch-selection part of the workflow step (the collector-code checkout lines need a full repo)."""
    run = _step("Check out or create the MRV research data branch")["run"]
    return run.split("# Collector code")[0]


@pytest.fixture
def world(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
        _git(work, "config", k, v)
    (work / "README").write_text("x\n")
    _git(work, "add", "README")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return remote, work


def _write_rows(work):
    d = work / P / "runs" / "2026-09-23"
    d.mkdir(parents=True, exist_ok=True)
    (d / "MRV1_20260923T220000Z_abcdef.json").write_text('{"runId": "MRV1_20260923T220000Z_abcdef"}\n')


def _persist(work):
    return subprocess.run([sys.executable, COMMIT, "--message", "t", "--branch", BRANCH, P],
                          cwd=work, capture_output=True, text=True)


def _remote_has_rows(remote):
    ls = _git(remote, "ls-tree", "-r", "--name-only", BRANCH, "--", P + "/runs", check=False)
    return ls.returncode == 0 and bool(ls.stdout.strip())


def test_local_only_branch_cannot_be_persisted(world):
    """The defect itself: a branch the remote lacks makes every persist fail."""
    remote, work = world
    _git(work, "checkout", "-q", "-b", BRANCH)
    _write_rows(work)
    assert _persist(work).returncode != 0
    assert not _remote_has_rows(remote)


def test_workflow_bootstrap_creates_the_remote_branch_and_rows_persist(world):
    remote, work = world
    r = subprocess.run(["bash", "-c", _bootstrap_script()], cwd=work, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert _git(remote, "rev-parse", "--verify", "refs/heads/" + BRANCH, check=False).returncode == 0
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == BRANCH
    _write_rows(work)
    out = _persist(work)
    assert out.returncode == 0, out.stderr
    assert _remote_has_rows(remote)


def test_workflow_bootstrap_reuses_an_existing_remote_branch(world):
    remote, work = world
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/" + BRANCH)
    before = _git(remote, "rev-parse", "refs/heads/" + BRANCH).stdout.strip()
    r = subprocess.run(["bash", "-c", _bootstrap_script()], cwd=work, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert _git(remote, "rev-parse", "refs/heads/" + BRANCH).stdout.strip() == before


def test_workflow_fails_when_rows_did_not_reach_the_research_branch():
    run = _step("Verify the rows reached the research branch")["run"]
    assert "uncommitted MRV rows remain in the working tree" in run
    assert "no MRV run manifests present on" in run
    assert _step("Verify the rows reached the research branch").get("if") == "always()"
