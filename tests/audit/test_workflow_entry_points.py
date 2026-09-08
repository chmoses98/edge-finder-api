#!/usr/bin/env python3
"""
tests/audit/test_workflow_entry_points.py
=========================================
REMEDIATION WAVE 0, section F. Tests that exercise production entry points the
way GitHub Actions exercises them.

WHY THIS FILE EXISTS
--------------------
The 2026-09 settlement outage survived 9,623 green tests. Not because the
tests were weak in general -- they are unusually thorough -- but because every
one of them imported the failing module DIFFERENTLY from the way production
ran it:

    tests/test_clv_snapshot_pipeline.py     sys.path.insert(0, _root)   <- works
    tests/edgelab/test_clv_convention.py    from scripts.clv_from_snapshot import ...
    scripts/run_kalshi_clv_step.py          sys.path.insert(0, _here)   <- ModuleNotFoundError

Three import shapes for one module, and the only shape nobody tested was the
one production used. This file closes that class of gap rather than the single
instance of it.

A NOTE ON WHY THESE TESTS DO NOT `import` THE SCRIPTS
-----------------------------------------------------
Several production scripts execute their entire body at import time --
`build_kalshi_registry.py`, `merge_odds.py` and `enrich_data.py` have no
`if __name__ == "__main__":` guard at all. During this Wave's investigation an
import-based scan of the scripts tree silently REWROTE
data/kalshi_market_registry.json (8,936 lines -> 6) simply by importing
build_kalshi_registry. So:

  - the repo-wide guard below is PURE AST. It parses; it never executes.
  - the one subprocess probe targets `clv_from_snapshot` only, which was first
    verified to be `__main__`-guarded and side-effect-free at import.

`-P` in that probe is load-bearing. When Python runs a SCRIPT, sys.path[0] is
the SCRIPT'S directory, not the working directory. `python3 -c` instead puts
the CWD on the path, so without `-P` the probe passes vacuously against a
genuinely broken entry point -- which is exactly how this bug hid.
"""

import ast
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
SCRIPTS = os.path.join(ROOT, "scripts")


# ── helpers ──────────────────────────────────────────────────────────────────

def _iter_script_files():
    for dirpath, dirnames, filenames in os.walk(SCRIPTS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _module_level_lib_import_line(tree):
    """Line number of the first module-level `lib.*` import, or None."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "lib":
                return node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "lib":
                    return node.lineno
    return None


def _root_on_syspath_line(tree):
    """
    Line number of the first module-level statement that puts the REPOSITORY
    ROOT on sys.path, or None.

    Recognises the patterns actually used across this repo:
        sys.path.insert(0, ROOT_DIR)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if ROOT_DIR not in sys.path: sys.path.insert(0, ROOT_DIR)
    Deliberately does NOT accept `sys.path.insert(0, os.path.join(ROOT_DIR, "lib"))`
    -- inserting the lib/ DIRECTORY makes `import atomic_json` work but leaves
    `import lib.edgelab` broken, and conflating the two is precisely the
    mistake that caused this outage.
    """
    # Resolve module-level `NAME = <expr>` bindings so an insert of a variable
    # (ROOT_DIR, REPO, _ROOT, ...) is judged by the EXPRESSION it holds rather
    # than by whether its name happens to contain a magic substring. Name
    # heuristics were tried first and produced 20 false positives on scripts
    # that spell it `REPO`.
    bindings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            bindings[node.targets[0].id] = node.value

    def _derives_from_file(expr, depth=0):
        """
        True when `expr` is a path expression ultimately computed from
        __file__, following module-level name bindings transitively.

        Transitivity matters: the common shape here is
            _HERE    = os.path.dirname(os.path.abspath(__file__))
            ROOT_DIR = os.path.dirname(os.path.dirname(_HERE))
        so ROOT_DIR reaches __file__ only through a second hop.
        """
        if depth > 8 or expr is None:
            return False
        if isinstance(expr, ast.Name):
            return _derives_from_file(bindings.get(expr.id), depth + 1)
        for node in ast.walk(expr):
            if isinstance(node, ast.Name):
                if node.id == "__file__":
                    return True
                if node.id in bindings and _derives_from_file(bindings[node.id], depth + 1):
                    return True
        return False

    def _ends_in_literal_subdir(expr, depth=0):
        """
        True for os.path.join(<root>, "lib") and friends -- a path that points
        INTO the tree rather than at its root. Inserting lib/ makes
        `import atomic_json` work while leaving `import lib.edgelab` broken,
        which is the precise confusion that caused audit CR-2, so it must not
        satisfy this check.
        """
        if depth > 6 or expr is None:
            return False
        if isinstance(expr, ast.Name):
            return _ends_in_literal_subdir(bindings.get(expr.id), depth + 1)
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute) and func.attr == "join" and expr.args:
                tail = expr.args[-1]
                # ".." components walk UP toward the root and are fine.
                if isinstance(tail, ast.Constant) and isinstance(tail.value, str):
                    return tail.value not in ("", "..")
        return False

    def _is_root_insert(call):
        if not isinstance(call, ast.Call):
            return False
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            return False
        target = func.value
        if not (isinstance(target, ast.Attribute) and target.attr == "path"
                and isinstance(target.value, ast.Name) and target.value.id == "sys"):
            return False
        if len(call.args) < 2:
            return False
        arg = call.args[1]
        return _derives_from_file(arg) and not _ends_in_literal_subdir(arg)

    for node in tree.body:
        for sub in ast.walk(node):
            if _is_root_insert(sub):
                return node.lineno
    return None


# ── the repo-wide guard ──────────────────────────────────────────────────────

def test_every_script_importing_lib_puts_the_repo_root_on_syspath_first():
    """
    THE regression guard for audit CR-2, generalized.

    Any script that imports `lib.*` at module level must first put the
    repository ROOT on sys.path -- otherwise it works under pytest (which adds
    the root) and dies under `python3 scripts/<name>.py` (which does not).

    Pure static analysis: parses every script, executes none.
    """
    offenders = []
    for path in _iter_script_files():
        try:
            tree = ast.parse(open(path, errors="ignore").read())
        except SyntaxError:
            continue
        import_line = _module_level_lib_import_line(tree)
        if import_line is None:
            continue
        setup_line = _root_on_syspath_line(tree)
        rel = os.path.relpath(path, ROOT)
        if setup_line is None:
            offenders.append("%s: imports lib.* at line %d but never puts the "
                             "repository root on sys.path" % (rel, import_line))
        elif setup_line > import_line:
            offenders.append("%s: imports lib.* at line %d but only adds the "
                             "repository root to sys.path at line %d (too late)"
                             % (rel, import_line, setup_line))
    assert offenders == [], (
        "script(s) will raise ModuleNotFoundError when run the way a workflow runs "
        "them (`python3 scripts/<name>.py`), even though pytest imports them fine:\n  "
        + "\n  ".join(offenders))


# ── the targeted probe ───────────────────────────────────────────────────────

def _import_under_script_run_syspath(module_name):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # the GitHub runner sets none
    return subprocess.run(
        [sys.executable, "-P", "-c",
         "import sys; sys.path.insert(0, 'scripts'); import %s" % module_name],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("module_name", ["clv_from_snapshot", "fetch_kalshi_clv_v2"])
def test_clv_chain_modules_import_under_the_production_syspath(module_name):
    """
    The exact failure of runs 89-94 of clv-update.yml, as a test.

    Both modules are imported by scripts/run_kalshi_clv_step.py, which puts
    ONLY `scripts/` on sys.path. Both are `__main__`-guarded, so importing them
    in a subprocess is side-effect-free.
    """
    if not os.path.exists(os.path.join(SCRIPTS, module_name + ".py")):
        pytest.skip("scripts/%s.py not present" % module_name)
    proc = _import_under_script_run_syspath(module_name)
    assert "ModuleNotFoundError" not in proc.stderr, (
        "scripts/%s.py cannot be imported with the sys.path its production entry "
        "point actually produces:\n%s" % (module_name, proc.stderr.strip()[-900:]))
    assert proc.returncode == 0, proc.stderr.strip()[-900:]


def test_the_probe_itself_would_catch_a_regression():
    """
    Guards the guard. A probe that cannot fail is worse than no probe, and the
    `-P` flag is the entire difference between this test being real and being
    decorative -- so prove that dropping it makes the check pass vacuously.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    without_p = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import json, os; "
         "print('root on path:', os.getcwd() in sys.path or '' in sys.path)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert "root on path: True" in without_p.stdout, (
        "expected `python3 -c` to place the CWD on sys.path; if this ever stops "
        "being true, the -P flag in the probes above is no longer load-bearing "
        "and this file's rationale needs revisiting.\n" + without_p.stdout)


# ── import-time side effects (the hazard this file had to work around) ───────

def test_clv_chain_scripts_are_guarded_against_import_time_side_effects():
    """
    Every module the CLV chain imports must be `__main__`-guarded, because the
    tests above import them. This is what makes the subprocess probes safe, and
    it must stay true.

    Deliberately scoped to the CLV chain rather than all of scripts/: several
    production scripts genuinely do run on import today
    (build_kalshi_registry.py, merge_odds.py, enrich_data.py). That is a real
    hazard -- importing build_kalshi_registry rewrites
    data/kalshi_market_registry.json -- but repairing it means touching the
    slate pipeline, which Wave 0 is explicitly not authorized to do. It is
    recorded as a residual blind spot in
    docs/MLB_INSTITUTIONAL_REMEDIATION_WAVE0_2026_09.md instead of being
    quietly widened into this assertion.
    """
    unguarded = []
    for name in ("clv_from_snapshot.py", "fetch_kalshi_clv_v2.py"):
        path = os.path.join(SCRIPTS, name)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path).read())
        guarded = any(
            isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and getattr(node.test.left, "id", None) == "__name__"
            for node in tree.body
        )
        if not guarded:
            unguarded.append(name)
    assert unguarded == [], (
        "CLV-chain script(s) execute on import, which makes the import probes in "
        "this file unsafe: %r" % unguarded)
