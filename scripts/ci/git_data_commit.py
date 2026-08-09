#!/usr/bin/env python3
"""
scripts/ci/git_data_commit.py
=================================
Shared, safe "fetch -> rebase --autostash -> add -> commit -> push (with
retry)" path for every automated data-capture/ingestion workflow under
.github/workflows/ -- previously duplicated inline, roughly two dozen
times across 17 workflow files, all carrying the same latent bug.

ROOT CAUSE (reproduced exactly in tests/test_git_data_commit.py):
`git rebase --autostash <upstream>` performs an automatic `git stash
pop` after a successful rebase, and if THAT pop conflicts, git leaves
literal

    <<<<<<< Updated upstream
    ...
    =======
    ...
    >>>>>>> Stashed changes

markers in the working tree -- but the `git rebase` COMMAND ITSELF
STILL EXITS 0. Every inline copy of this pattern ran
`git rebase --autostash origin/main` as a bare statement (no `&&` / exit
-code check), so this failure mode was invisible to the shell: the
script proceeded straight to `git add` (which stages whatever bytes are
on disk, conflict markers included), then `git commit`, then
`git push` -- landing broken JSONL on main. This is exactly what kept
reappearing in data/edgelab/research_runs/*.jsonl.

This module never trusts `git rebase`'s own exit code for the autostash
case. After every rebase attempt it checks `git diff --name-only
--diff-filter=U` (git's own authoritative unmerged-path bookkeeping,
which stays accurate even for a conflicted stash pop) AND, as an
independent second check, greps the exact set of paths this run is
about to commit for literal conflict-marker lines -- belt and
suspenders, matching this repo's own established double-check
philosophy elsewhere. Any conflict at any stage aborts the *local*
rebase/merge state and resets to the fetched upstream tip WITHOUT ever
running `git add` / `git commit` / `git push` -- main is never touched,
and neither side of the unresolved conflict is discarded: origin/main
keeps whatever already landed there, and this run's own local changes
are left recoverable in a runner-local `git stash` entry (never force-
dropped) for this run only. The runner is ephemeral and torn down
either way; the next scheduled run of the same idempotent capture/
ingest script regenerates equivalent output and retries cleanly against
the now-current origin/main.

Deliberately isolated from betting/model behavior: this module only
ever decides HOW to commit/push whatever the caller already produced --
it never reads, writes, or judges the CONTENT of any recommendation,
probability, price, or bet. Callers are responsible for `git config
user.name/user.email` (every workflow already does this as its own
preceding step) and for actually generating the files being committed.
"""
import argparse
import os
import re
import subprocess
import sys
import time

_CONFLICT_MARKER_RE = re.compile(rb'^(<{7} |={7}$|>{7} )', re.MULTILINE)


def _run(args, cwd=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _resolve(path, cwd):
    return os.path.join(cwd, path) if cwd else path


def _iter_files(rel, cwd=None):
    """
    Yields (rel_file_path, full_file_path) under `rel` -- `rel` itself if
    it's a file, or every regular file beneath it (recursively, skipping
    .git) if it's a directory. Several callers pass directory paths (e.g.
    data/clv_snapshots/), and a bare `os.path.isfile` check on a
    directory is always False, which would silently skip it entirely.
    """
    full = _resolve(rel, cwd)
    if os.path.isfile(full):
        yield rel, full
    elif os.path.isdir(full):
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d != '.git']
            for name in filenames:
                file_full = os.path.join(dirpath, name)
                file_rel = os.path.relpath(file_full, cwd) if cwd else file_full
                yield file_rel, file_full


def find_conflict_markers(paths, cwd=None):
    """
    Returns a list of (path, line_no) for every literal git conflict-
    marker line (<<<<<<<, =======, >>>>>>>, at line start) found in any
    of `paths` that currently exists on disk -- files and directories
    alike (a directory is scanned recursively; see _iter_files). Never
    raises on a missing path (skips it) -- callers pass whatever paths
    they intended to add, some of which may legitimately not exist this
    run. Read-only: never modifies any file.
    """
    hits = []
    for rel in paths:
        for file_rel, file_full in _iter_files(rel, cwd=cwd):
            with open(file_full, 'rb') as f:
                for i, line in enumerate(f, start=1):
                    if _CONFLICT_MARKER_RE.match(line):
                        hits.append((file_rel, i))
    return hits


def unmerged_paths(cwd=None):
    """
    git's own authoritative list of currently-conflicted paths (accurate
    mid-rebase, mid-merge, or after a conflicted `stash pop`) -- the
    PRIMARY signal this module trusts, never a command's own exit code
    (see module docstring for why the exit code alone is not enough).
    """
    result = _run(['git', 'diff', '--name-only', '--diff-filter=U'], cwd=cwd)
    return [p for p in result.stdout.splitlines() if p]


def _abort_and_reset(branch, cwd=None):
    """
    Unwinds any in-progress rebase, then resets hard to the fetched
    upstream tip. `rebase --abort` alone does not clean up a conflicted
    *autostash pop* (that conflict is applied to the working tree AFTER
    the rebase itself already completed) -- the hard reset is what
    actually clears it. Never touches `refs/stash`: any auto-created
    stash entry survives this, so the caller's local changes are never
    silently discarded, only left unmerged for this run.
    """
    _run(['git', 'rebase', '--abort'], cwd=cwd)
    _run(['git', 'reset', '--hard', f'origin/{branch}'], cwd=cwd)


def safe_rebase_onto(branch, cwd=None):
    """
    `git fetch origin <branch>` then `git rebase --autostash
    origin/<branch>`, but NEVER trusting that command's own exit code
    for the autostash-pop-conflict case. Returns (ok: bool, message: str).

    On failure, the working tree is left clean at origin/<branch>'s tip
    -- see _abort_and_reset.
    """
    fetch = _run(['git', 'fetch', 'origin', branch], cwd=cwd)
    if fetch.returncode != 0:
        return False, f'git fetch origin {branch} failed: {fetch.stderr.strip()}'

    rebase = _run(['git', 'rebase', '--autostash', f'origin/{branch}'], cwd=cwd)
    conflicted = unmerged_paths(cwd=cwd)
    if rebase.returncode != 0 or conflicted:
        reason = rebase.stderr.strip() or rebase.stdout.strip() or 'unmerged paths after autostash'
        _abort_and_reset(branch, cwd=cwd)
        return False, f'rebase/autostash conflict: {reason} (conflicted paths: {conflicted})'
    return True, 'ok'


def commit_and_push(paths, message, branch='main', cwd=None, max_push_attempts=4):
    """
    The full safe path: rebase onto the latest <branch>, stage exactly
    the `paths` that exist and changed, scan them for conflict markers,
    commit, then push with retry (re-rebasing between attempts to
    handle a concurrent push race). Returns 0 on success (including the
    legitimate "nothing changed, nothing to commit" no-op) and non-zero
    on any failure. A failed attempt never leaves a broken or unmerged
    state on the local branch -- every failure path resets to a branch
    tip that IS origin/<branch>'s.
    """
    ok, reason = safe_rebase_onto(branch, cwd=cwd)
    if not ok:
        print(f'ERROR: {reason}', file=sys.stderr)
        print(
            'Aborting without committing -- main is untouched; any local '
            'changes for this run are preserved in `git stash list` for '
            'this run only, never force-dropped.',
            file=sys.stderr,
        )
        return 1

    existing_paths = [p for p in paths if os.path.exists(_resolve(p, cwd))]
    if not existing_paths:
        print('Nothing to commit (none of the target paths exist).')
        return 0

    _run(['git', 'add'] + existing_paths, cwd=cwd)

    if _run(['git', 'diff', '--cached', '--quiet'], cwd=cwd).returncode == 0:
        print('No changes to commit.')
        return 0

    marker_hits = find_conflict_markers(existing_paths, cwd=cwd)
    if marker_hits:
        for path, line_no in marker_hits:
            print(f'ERROR: unresolved conflict marker in {path}:{line_no} -- refusing to commit', file=sys.stderr)
        _run(['git', 'reset', '--hard', f'origin/{branch}'], cwd=cwd)
        return 1

    commit = _run(['git', 'commit', '-m', message], cwd=cwd)
    if commit.returncode != 0:
        print(f'ERROR: git commit failed: {commit.stderr.strip()}', file=sys.stderr)
        return 1

    for attempt in range(1, max_push_attempts + 1):
        if _run(['git', 'push', 'origin', f'HEAD:{branch}'], cwd=cwd).returncode == 0:
            print(f'Push succeeded (attempt {attempt}).')
            return 0

        print(f'Push failed (attempt {attempt}) -- re-fetching and rebasing before retry...')
        _run(['git', 'fetch', 'origin', branch], cwd=cwd)
        rebase = _run(['git', 'rebase', f'origin/{branch}'], cwd=cwd)
        conflicted = unmerged_paths(cwd=cwd)
        if rebase.returncode != 0 or conflicted:
            print(f'ERROR: rebase failed while retrying push -- aborting. Conflicted paths: {conflicted}', file=sys.stderr)
            _abort_and_reset(branch, cwd=cwd)
            return 1

        # A rebase with no conflicts can still silently re-stage a file
        # that now contains conflict markers only in the pathological
        # case where a PRIOR broken commit already landed on origin --
        # checked here too so this retry loop can never push on top of
        # that without at least refusing its OWN contribution.
        marker_hits = find_conflict_markers(existing_paths, cwd=cwd)
        if marker_hits:
            for path, line_no in marker_hits:
                print(f'ERROR: unresolved conflict marker in {path}:{line_no} after retry rebase -- refusing to push', file=sys.stderr)
            _abort_and_reset(branch, cwd=cwd)
            return 1

        time.sleep(attempt * 5)

    print('ERROR: commit could not be pushed after all retries', file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--message', required=True, help='Commit message')
    parser.add_argument('--branch', default='main', help='Branch to rebase onto and push to (default: main)')
    parser.add_argument('--cwd', default=None, help='Git repository directory (default: current directory)')
    parser.add_argument('paths', nargs='+', help='Paths to git add if present and changed')
    args = parser.parse_args(argv)
    return commit_and_push(args.paths, args.message, branch=args.branch, cwd=args.cwd)


if __name__ == '__main__':
    sys.exit(main())
