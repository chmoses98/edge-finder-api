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
import re

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


# ── valid YAML is not the same thing as a workflow Actions can run ────

def _run_body_lines(source):
    """1-indexed line numbers that lie INSIDE a `run:` block scalar.

    The distinction this whole section rests on. A `#` comment at YAML
    level is never seen by Actions. A `#` comment inside a `run:` body is
    just text in a string that Actions INTERPOLATES before bash ever
    sees it -- so an expression there is evaluated, commented out or not.
    """
    lines = source.splitlines()
    inside = set()
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(- )?run:\s*[|>]", lines[index])
        if not match:
            index += 1
            continue
        indent = len(match.group(1)) + (2 if match.group(2) else 0)
        cursor = index + 1
        while cursor < len(lines) and (
            not lines[cursor].strip()
            or len(lines[cursor]) - len(lines[cursor].lstrip()) > indent
        ):
            inside.add(cursor + 1)
            cursor += 1
        index = cursor
    return inside


def test_no_run_body_contains_an_empty_actions_expression():
    """THE 2026-09-19 STARTUP FAILURE.

    `edgelab-settlement-reconcile.yml` carried, inside a `run:` body, a
    shell comment explaining why the step reads argv from `env:` rather
    than interpolating an Actions expression -- and it spelled that
    expression out literally, empty. Actions interpolates a `run:` body
    before bash sees it, an empty expression does not compile, and the
    workflow died at startup: `conclusion: failure`, ZERO jobs, and
    GitHub falling back to the file path instead of the workflow's own
    `name:`. Nineteen consecutive runs, including the one fired by the
    merge that finally put the router's 41 canonical wagers on main --
    which is why the 2026-09-18 report went on reading `Placed bets: 0`
    after the rows had actually landed.

    `test_every_workflow_file_is_valid_yaml` above passed throughout: the
    file was and is perfectly valid YAML. Valid YAML is not the same
    thing as a workflow Actions can compile, and that gap is what this
    test closes.

    Four OTHER workflows say the same sentence in a YAML-level comment,
    where Actions never evaluates it, and all of them run green. Those
    are correct and this test must not flag them.
    """
    offenders = []
    for name in _workflow_files():
        source = _source(name)
        run_lines = _run_body_lines(source)
        for number, line in enumerate(source.splitlines(), 1):
            if number in run_lines and re.search(r"\$\{\{\s*\}\}", line):
                offenders.append(f"{name}:{number}")
    assert offenders == [], (
        "empty Actions expression inside a run: body -- Actions interpolates "
        "that body before bash sees it, so this fails to compile and the "
        f"workflow will not start at all: {offenders}"
    )


def test_a_yaml_level_comment_may_still_mention_the_expression():
    """The guard above must catch the real defect without banning the
    documentation. Proven against the tree rather than a fixture: at
    least one workflow discusses an empty expression in a YAML comment,
    and the checker leaves it alone."""
    documented = [
        name for name in _workflow_files()
        if any(
            re.search(r"\$\{\{\s*\}\}", line) and number not in _run_body_lines(_source(name))
            for number, line in enumerate(_source(name).splitlines(), 1)
        )
    ]
    assert documented, "expected at least one workflow to document this in a YAML comment"


def test_every_workflow_declares_a_name_actions_can_read():
    """A workflow whose `name:` Actions cannot reach shows up in the UI
    as its file path -- the symptom that made the startup failure above
    identifiable from the run list alone."""
    missing = [
        name for name in _workflow_files()
        if not (yaml.safe_load(_source(name)) or {}).get("name")
    ]
    assert missing == [], f"workflow(s) declare no name: {missing}"
