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
philosophy elsewhere.

APPEND-ONLY JSONL CONCURRENCY RECOVERY -- Kalshi Price Check
(Standalone) taking ~22 minutes for hitter projection widened the
window in which OTHER scheduled workflows advance main and append their
own rows to the same shared `data/edgelab/*.jsonl` daily partitions,
which made the plain fail-closed behavior above fire routinely for a
completely benign case: two independently-generated, valid, append-only
JSONL records landing in the same daily file. Rather than weaken the
conflict detection above, this module now proves -- deterministically,
from this run's OWN pre-rebase local diff, never by guessing or picking
a side -- whether every conflicted path is a pure local append onto
whatever origin/<branch> currently has, and if so reconciles and
continues; otherwise it falls back to the exact same fail-closed path as
before. See `capture_append_only_deltas` / `_reconcile_append_only` /
`_try_resolve_conflicts_as_append_only` below, and the module-level
"APPEND-ONLY DESIGN" note further down for the full contract.

Any conflict this module cannot PROVE is a pure local append aborts the
*local* rebase/merge state and resets to the fetched upstream tip
WITHOUT ever running `git add` / `git commit` / `git push` -- main is
never touched, and neither side of the unresolved conflict is
discarded: origin/main keeps whatever already landed there, and this
run's own local changes are left recoverable in a runner-local `git
stash` entry (never force-dropped) for this run only. The runner is
ephemeral and torn down either way; the next scheduled run of the same
idempotent capture/ingest script regenerates equivalent output and
retries cleanly against the now-current origin/main.

Deliberately isolated from betting/model behavior: this module only
ever decides HOW to commit/push whatever the caller already produced --
it never reads, writes, or judges the CONTENT of any recommendation,
probability, price, or bet (the one exception -- reading/reconciling
the literal bytes of a caller-supplied .jsonl path -- never inspects
what KIND of record it is; it only ever proves a byte-level append
relationship and unions JSON objects it never interprets). Callers are
responsible for `git config user.name/user.email` (every workflow
already does this as its own preceding step) and for actually
generating the files being committed.

APPEND-ONLY DESIGN
-------------------
`commit_and_push` captures `capture_append_only_deltas(paths)` FIRST,
before anything touches the working tree -- for every `.jsonl` file
among `paths` (files or whole directories, expanded recursively) whose
current on-disk content is a strict, byte-exact prefix EXTENSION of
that same file's content at the current local HEAD (i.e. this run only
ever appended complete lines after HEAD's content, never touched an
existing line), where:
  * HEAD's own content is itself valid newline-delimited JSON (one
    complete JSON *object* per nonblank line, no conflict markers), and
  * every nonblank appended line parses as exactly one complete JSON
    object (no partial trailing line, no conflict markers).
Anything else about that file -- a local edit to an existing line, a
deleted line, non-JSON garbage, a brand-new file with no HEAD version,
a directory, a `.jsonl.gz` (binary/compressed, no meaningful text-
prefix relationship) -- is simply never added to this captured set, and
therefore can never be auto-recovered later; it always falls back to
the fail-closed abort path if it conflicts.

If the subsequent `git rebase --autostash origin/<branch>` (or, in the
push-retry loop, a plain `git rebase origin/<branch>` replaying this
run's own already-made commit) leaves any unmerged path, recovery is
attempted ONLY when every single unmerged path is in that captured set.
Recovery never inspects, trusts, or keeps any byte of the conflicted
working-tree content (the literal conflict-marker text is discarded
outright) -- instead, for each conflicted path, it:
  1. Restores the path to the freshly fetched `origin/<branch>` blob
     exactly (`git checkout origin/<branch> -- <path>`), and validates
     that blob is itself valid JSONL with no conflict markers.
  2. Re-applies ONLY this run's own pre-captured appended JSON objects
     on top, in their original order, after upstream's rows (which are
     NEVER reordered, edited, or dropped) --
       * an appended object that is EXACTLY equal (by parsed value) to
         one already present upstream is skipped (no duplicate line);
       * an appended object that shares a recognized stable identity
         field (runId, betId, marketObservationId, ... -- see
         STABLE_ID_FIELDS) with a *different* upstream object hard-
         fails the whole reconciliation (never silently picks a side);
       * everything else is appended.
  3. Re-validates the reconciled text as clean JSONL with no conflict
     markers before ever writing it to disk, and `git add`s only the
     paths this succeeded for.
If ANY conflicted path fails ANY of the above, the WHOLE reconciliation
attempt fails and this module falls straight back to the pre-existing
fail-closed abort (`_abort_and_reset`) -- a partial reconciliation is
never staged or committed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

_CONFLICT_MARKER_RE = re.compile(rb'^(<{7} |={7}$|>{7} )', re.MULTILINE)
_CONFLICT_MARKER_RE_TEXT = re.compile(r'^(<{7} |={7}$|>{7} )', re.MULTILINE)

# Stable per-record identity fields used across data/edgelab/*.jsonl (and a
# few sibling research corpora) -- see lib/edgelab/storage.py's
# append_records/upsert_records `id_field` callers. Checked in this order;
# the first one present on a record wins. Used ONLY to decide whether an
# appended local record collides in IDENTITY with a different upstream
# record (see _reconcile_append_only) -- never to decide append-only-ness
# itself, which is proven purely by the byte-level prefix check.
STABLE_ID_FIELDS = (
    'runId', 'recommendationId', 'modelEvaluationId', 'betId',
    'marketObservationId', 'clvQuoteId', 'settlementId', 'gameId',
    'marketTicker', 'snapshotId', 'postmortemId', 'importBatchId',
    'clusterId', 'id',
)


def _run(args, cwd=None, env=None):
    run_env = None
    if env:
        run_env = os.environ.copy()
        run_env.update(env)
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=run_env)


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


# ── Append-only JSONL detection & reconciliation ────────────────────────

def _git_show(ref, relpath, cwd=None):
    """`git show <ref>:<relpath>` as text, or None if that path does not
    exist at that ref (or the ref/lookup otherwise fails) -- never
    raises; a lookup failure just means "not a candidate", the safe
    direction for every caller of this helper."""
    result = _run(['git', 'show', f'{ref}:{relpath}'], cwd=cwd)
    if result.returncode != 0:
        return None
    return result.stdout


def _split_jsonl_strict(text):
    """
    Parses `text` as newline-delimited JSON under this module's strict
    append-only rules. Returns a list of (raw_line, obj) for every
    nonblank line (obj is always a dict), in order -- or None if:
      * `text` is None,
      * `text` contains a literal conflict-marker line,
      * `text` is non-empty and does not end with a trailing newline
        (a partial/truncated final line), or
      * any nonblank line fails to parse as exactly one JSON *object*
        (a bare JSON array/number/string/etc is not a valid record
        here, matching every writer in lib/edgelab/storage.py).
    Blank/whitespace-only lines are ignored, matching
    lib.edgelab.storage.read_records' own handling. An empty string
    parses to an empty list (a valid, empty JSONL file).
    """
    if text is None:
        return None
    if _CONFLICT_MARKER_RE_TEXT.search(text):
        return None
    if text == '':
        return []
    if not text.endswith('\n'):
        return None
    records = []
    for raw_line in text[:-1].split('\n'):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        records.append((raw_line, obj))
    return records


def capture_append_only_deltas(paths, cwd=None):
    """
    Returns {rel_path: [(raw_line, obj), ...]} for every `.jsonl` file
    among `paths` (files and/or directories, expanded recursively; see
    _iter_files) whose CURRENT on-disk content is a provable, strict
    append-only extension of that file's content at the CURRENT local
    HEAD -- see the module docstring's "APPEND-ONLY DESIGN" section for
    the exact rules. Must be called before anything (a rebase, a stash)
    touches the working tree, since it compares disk against HEAD right
    now. Purely read-only -- never modifies any file or git state.

    A `.jsonl.gz` path is never a candidate (binary/compressed content
    has no meaningful text-prefix relationship to its appended form).
    Neither is a brand-new file with no HEAD version (that's an add/add
    shape, not append-only), nor a file whose disk content doesn't
    literally begin with its exact HEAD bytes (a local edit or delete
    to an existing line), nor one where HEAD's own content isn't itself
    clean JSONL, nor one where the appended suffix doesn't parse as
    complete, whole JSON objects.
    """
    deltas = {}
    for rel in paths:
        for file_rel, file_full in _iter_files(rel, cwd=cwd):
            if not file_rel.endswith('.jsonl'):
                continue

            head_text = _git_show('HEAD', file_rel, cwd=cwd)
            if head_text is None or _split_jsonl_strict(head_text) is None:
                continue

            try:
                with open(file_full, 'r', encoding='utf-8') as f:
                    disk_text = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            if disk_text == head_text or not disk_text.startswith(head_text):
                continue

            appended_records = _split_jsonl_strict(disk_text[len(head_text):])
            if appended_records:
                deltas[file_rel] = appended_records
    return deltas


def _reconcile_append_only(path, local_records, branch, cwd=None):
    """
    Reconciles a single conflicted `path` this run's own captured local
    delta has proven is a pure append: fetches origin/<branch>'s current
    blob for `path` (never any other version -- upstream's existing
    content is NEVER edited, reordered, or dropped), validates it, and
    unions `local_records` onto it (order preserved, exact duplicates
    skipped, a colliding stable-identity field on a DIFFERENT payload
    hard-fails). On success, overwrites `path` on disk with the
    reconciled, re-validated JSONL text and returns (True, count of
    local records actually appended). On any failure, returns (False,
    reason) and writes nothing.
    """
    upstream_text = _git_show(f'origin/{branch}', path, cwd=cwd)
    if upstream_text is None:
        return False, f'{path}: no origin/{branch} version found to reconcile against'

    upstream_records = _split_jsonl_strict(upstream_text)
    if upstream_records is None:
        return False, f'{path}: origin/{branch} version is not clean valid JSONL -- cannot safely reconcile'

    seen_objs = [obj for _, obj in upstream_records]
    identity_index = {}
    for obj in seen_objs:
        for field in STABLE_ID_FIELDS:
            val = obj.get(field)
            if val is not None:
                identity_index[(field, val)] = obj
                break

    out_lines = [raw for raw, _ in upstream_records]
    appended_count = 0
    for raw_line, obj in local_records:
        if obj in seen_objs:
            continue  # exact duplicate (already upstream, or already reapplied this run) -- never doubled

        for field in STABLE_ID_FIELDS:
            val = obj.get(field)
            if val is not None and (field, val) in identity_index:
                return False, (
                    f'{path}: local record with {field}={val!r} already exists upstream with '
                    f'different content -- refusing to silently choose either payload'
                )

        out_lines.append(raw_line)
        seen_objs.append(obj)
        appended_count += 1

    reconciled_text = ''.join(line + '\n' for line in out_lines)
    if _split_jsonl_strict(reconciled_text) is None:
        return False, f'{path}: reconciled content failed re-validation -- refusing to write'

    with open(_resolve(path, cwd), 'w', encoding='utf-8') as f:
        f.write(reconciled_text)
    return True, appended_count


def _try_resolve_conflicts_as_append_only(conflicted_paths, append_only_delta, branch, cwd=None):
    """
    Attempts to resolve EVERY path in `conflicted_paths` via
    _reconcile_append_only, but only ever all-or-nothing: if any single
    conflicted path is not in `append_only_delta` (not proven append-
    only) or fails reconciliation for any reason, this returns (False,
    reason) immediately WITHOUT staging or writing anything for any
    OTHER path either -- a partial reconciliation is never left behind.
    On full success, every path in `conflicted_paths` has been restored
    to origin/<branch> + reapplied local records, re-checked for
    conflict markers, and `git add`ed (clearing the unmerged state).
    """
    if not conflicted_paths:
        return False, 'no conflicted paths to resolve'

    not_recoverable = [p for p in conflicted_paths if p not in append_only_delta]
    if not_recoverable:
        return False, f'not provably append-only JSONL: {not_recoverable}'

    reconciled_summary = []
    for path in conflicted_paths:
        checkout = _run(['git', 'checkout', f'origin/{branch}', '--', path], cwd=cwd)
        if checkout.returncode != 0:
            return False, f'{path}: could not restore origin/{branch} content ({checkout.stderr.strip()})'

        ok, result = _reconcile_append_only(path, append_only_delta[path], branch, cwd=cwd)
        if not ok:
            return False, result
        reconciled_summary.append(f'{path} (+{result} record(s))')

    marker_hits = find_conflict_markers(conflicted_paths, cwd=cwd)
    if marker_hits:
        return False, f'reconciled file(s) still contain conflict markers: {marker_hits}'

    add = _run(['git', 'add'] + list(conflicted_paths), cwd=cwd)
    if add.returncode != 0:
        return False, f'could not stage reconciled path(s): {add.stderr.strip()}'

    still_conflicted = unmerged_paths(cwd=cwd)
    if still_conflicted:
        return False, f'unmerged paths remain after reconciliation: {still_conflicted}'

    return True, '; '.join(reconciled_summary)


# ── Rebase + recovery ────────────────────────────────────────────────────

def safe_rebase_onto(branch, cwd=None, append_only_delta=None):
    """
    `git fetch origin <branch>` then `git rebase --autostash
    origin/<branch>`, but NEVER trusting that command's own exit code
    for the autostash-pop-conflict case. Returns (ok: bool, message: str).

    If the autostash pop leaves any unmerged path, recovery is attempted
    via `append_only_delta` (see capture_append_only_deltas) -- succeeds
    only if EVERY unmerged path is a proven append-only local delta;
    otherwise this falls back to the pre-existing fail-closed behavior.

    On failure, the working tree is left clean at origin/<branch>'s tip
    -- see _abort_and_reset.
    """
    append_only_delta = append_only_delta or {}

    fetch = _run(['git', 'fetch', 'origin', branch], cwd=cwd)
    if fetch.returncode != 0:
        return False, f'git fetch origin {branch} failed: {fetch.stderr.strip()}'

    rebase = _run(['git', 'rebase', '--autostash', f'origin/{branch}'], cwd=cwd)
    conflicted = unmerged_paths(cwd=cwd)
    if rebase.returncode != 0 or conflicted:
        reason = rebase.stderr.strip() or rebase.stdout.strip() or 'unmerged paths after autostash'
        if conflicted:
            recovered, detail = _try_resolve_conflicts_as_append_only(conflicted, append_only_delta, branch, cwd=cwd)
            if recovered:
                print(f'Append-only conflict safely reconciled during autostash pop: {detail}')
                return True, 'ok (append-only autostash conflict reconciled)'
            reason = f'{reason} -- append-only recovery not applicable: {detail}'
        _abort_and_reset(branch, cwd=cwd)
        return False, f'rebase/autostash conflict: {reason} (conflicted paths: {conflicted})'
    return True, 'ok'


def _rebase_retry_with_recovery(branch, append_only_delta, cwd=None):
    """
    Used only from the push-retry loop in commit_and_push, where this
    run's own already-made commit is replayed onto a newly-advanced
    origin/<branch> via a plain (non-autostash) `git rebase
    origin/<branch>`. Unlike the autostash-pop case, a conflict here is
    a genuine in-progress rebase, so a successful append-only recovery
    must `git rebase --continue` to actually finish it. Returns
    (ok: bool, message: str); on failure the rebase is aborted and the
    working tree reset to origin/<branch>'s tip, same as safe_rebase_onto.
    """
    rebase = _run(['git', 'rebase', f'origin/{branch}'], cwd=cwd)
    conflicted = unmerged_paths(cwd=cwd)
    if rebase.returncode == 0 and not conflicted:
        return True, 'ok'

    reason = rebase.stderr.strip() or rebase.stdout.strip() or 'unmerged paths during retry rebase'
    if conflicted:
        recovered, detail = _try_resolve_conflicts_as_append_only(conflicted, append_only_delta, branch, cwd=cwd)
        if recovered:
            cont = _run(['git', 'rebase', '--continue'], cwd=cwd, env={'GIT_EDITOR': 'true', 'EDITOR': 'true'})
            if cont.returncode != 0 or unmerged_paths(cwd=cwd):
                _abort_and_reset(branch, cwd=cwd)
                return False, (
                    f'append-only reconciliation staged cleanly but `git rebase --continue` failed: '
                    f'{cont.stderr.strip()}'
                )
            print(f'Append-only conflict safely reconciled during push-retry rebase: {detail}')
            return True, 'ok (append-only retry-rebase conflict reconciled)'
        reason = f'{reason} -- append-only recovery not applicable: {detail}'

    _abort_and_reset(branch, cwd=cwd)
    return False, f'rebase conflict during push retry: {reason} (conflicted paths: {conflicted})'


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

    Captures this run's own append-only JSONL delta (see
    capture_append_only_deltas) FIRST, before the rebase below (or its
    retry-loop counterpart) can touch the working tree at all -- see
    the module docstring's "APPEND-ONLY DESIGN" section.
    """
    append_only_delta = capture_append_only_deltas(paths, cwd=cwd)

    ok, reason = safe_rebase_onto(branch, cwd=cwd, append_only_delta=append_only_delta)
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
        ok, reason = _rebase_retry_with_recovery(branch, append_only_delta, cwd=cwd)
        if not ok:
            print(f'ERROR: {reason}', file=sys.stderr)
            print(
                'Aborting without pushing -- main is untouched; any local '
                'changes for this run are preserved in `git stash list` for '
                'this run only, never force-dropped.',
                file=sys.stderr,
            )
            return 1

        # A rebase with no conflicts (or a successfully-reconciled one)
        # can still, in principle, land a file containing conflict-marker
        # text only in the pathological case where a PRIOR broken commit
        # already exists on origin -- checked here too so this retry loop
        # can never push on top of that without at least refusing its OWN
        # contribution.
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
