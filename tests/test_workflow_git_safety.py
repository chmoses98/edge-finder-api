#!/usr/bin/env python3
"""
tests/test_workflow_git_safety.py
=====================================
Confirms every .github/workflows/*.yml automated data-commit step uses
the shared, safe scripts/ci/git_data_commit.py path -- not the old,
inlined `git fetch && git rebase --autostash origin/main && git add
... && git commit && git push` block, which silently committed
unresolved conflict markers to main whenever the autostash's automatic
`git stash pop` collided with a concurrent upstream change (`git
rebase --autostash` exits 0 even when that pop conflicts -- see
scripts/ci/git_data_commit.py's module docstring and
tests/test_git_data_commit.py's reproduction).

This is a "grep for the vulnerable pattern, confirm it's gone"
regression guard in the same spirit as this repo's other documented-
absence tests (e.g. tests/test_risk_gate_rule71_81_bankroll_absence.py)
-- it exists specifically so a future workflow can never quietly
reintroduce a fresh, unmigrated copy of the bug.
"""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS_DIR = os.path.join(ROOT, ".github", "workflows")
GIT_DATA_COMMIT_SCRIPT = os.path.join(ROOT, "scripts", "ci", "git_data_commit.py")


def _workflow_files():
    return sorted(f for f in os.listdir(WORKFLOWS_DIR) if f.endswith((".yml", ".yaml")))


def _source(name):
    with open(os.path.join(WORKFLOWS_DIR, name)) as f:
        return f.read()


def test_git_data_commit_script_exists():
    assert os.path.isfile(GIT_DATA_COMMIT_SCRIPT)


def test_no_workflow_inlines_bare_rebase_autostash():
    """The vulnerable pattern: `git rebase --autostash` invoked directly
    in a workflow's own `run:` shell, rather than through the safe
    wrapper. A workflow may still legitimately mention the phrase in a
    comment explaining the fix (this test only checks for the actual
    executable invocation)."""
    offenders = []
    for name in _workflow_files():
        source = _source(name)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "rebase --autostash" in stripped and "git_data_commit.py" not in source:
                offenders.append(name)
                break
    assert offenders == [], f"workflow(s) still inline the vulnerable pattern directly: {offenders}"


def test_every_workflow_that_commits_data_uses_the_shared_script():
    """Any workflow with a 'git commit' step for automated data output
    must route through git_data_commit.py -- not a second, independently
    -written commit/push block that could drift and reintroduce the
    same bug in a new shape."""
    offenders = []
    for name in _workflow_files():
        source = _source(name)
        if "git commit" not in source:
            continue
        if "git_data_commit.py" not in source:
            offenders.append(name)
    assert offenders == [], (
        f"workflow(s) still commit via their own inline git logic instead of "
        f"scripts/ci/git_data_commit.py: {offenders}"
    )


def test_every_workflow_file_is_valid_yaml():
    for name in _workflow_files():
        with open(os.path.join(WORKFLOWS_DIR, name)) as f:
            yaml.safe_load(f)  # raises on malformed YAML
