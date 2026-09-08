#!/usr/bin/env python3
"""
tests/test_harness_integrity.py
===============================
WAVE 0.05A. Guards on the TEST HARNESS ITSELF, after two independent
false-success defects of the same class surfaced during PR #193.

  A. tests/edgelab/test_clv_convention.py ran a data migration with --apply
     against the canonical ledger. The first CI run failed AND mutated
     data/edgelab/bets/bets.jsonl; the second run then passed on the state
     the first had written.

  B. scripts/regression_test.py (and scripts/test_f5_parse_suffix.py) matched
     pytest's default `test_*.py` / `*_test.py` collection glob and called
     sys.exit at module scope, so `python3 -m pytest` from the repo root
     aborted during collection with INTERNALERROR and ran ZERO tests.

Both share one root cause: a module or test whose mere execution has side
effects it was never meant to have. The guards below make each recurrence a
test failure rather than a discovery.
"""

import ast
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# pytest's default collection globs.
COLLECTED_GLOBS = ("test_*.py", "*_test.py")

DESTRUCTIVE_FLAGS = frozenset({
    "--apply", "--execute", "--force", "--prune", "--compact",
    "--repair", "--rewrite", "--delete",
})

SUBPROCESS_CALLS = frozenset({"run", "check_call", "check_output", "Popen", "call"})


def _is_collected(filename):
    return filename.startswith("test_") and filename.endswith(".py") \
        or filename.endswith("_test.py")


def _collected_files():
    """Every .py file a bare repo-root `pytest` would try to import."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__",
                                    ".pytest_cache", ".venv")]
        for name in filenames:
            if name.endswith(".py") and _is_collected(name):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _is_main_guard(node):
    """True for an `if __name__ == "__main__":` statement."""
    if not isinstance(node, ast.If):
        return False
    for cmp_node in ast.walk(node.test):
        if isinstance(cmp_node, ast.Compare):
            for operand in [cmp_node.left] + list(cmp_node.comparators):
                if isinstance(operand, ast.Constant) and operand.value == "__main__":
                    return True
    return False


def _unguarded_module_level_exits(path):
    """
    (lineno, ...) for every sys.exit/exit/quit that executes on IMPORT --
    i.e. at module scope and not inside an `if __name__ == "__main__":`
    guard and not inside a function or class body.
    """
    with open(path) as handle:
        tree = ast.parse(handle.read(), filename=path)

    hits = []
    for node in tree.body:
        if _is_main_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr == "exit" \
                    and isinstance(func.value, ast.Name) and func.value.id == "sys":
                hits.append(sub.lineno)
            elif isinstance(func, ast.Name) and func.id in ("exit", "quit"):
                hits.append(sub.lineno)
    return hits


# ── B. Importing a collected module must never terminate the interpreter ─────

def test_no_collected_file_exits_the_interpreter_at_import():
    """
    THE defect that made a bare repo-root pytest run zero tests. Any file
    matching pytest's collection globs is imported during collection; a
    module-scope sys.exit kills the whole session with INTERNALERROR.
    """
    offenders = []
    for path in _collected_files():
        for lineno in _unguarded_module_level_exits(path):
            offenders.append("%s:%d" % (os.path.relpath(path, ROOT), lineno))
    assert offenders == [], (
        "these pytest-collected files terminate the interpreter at import, "
        "which aborts collection for the ENTIRE session:\n  "
        + "\n  ".join(offenders)
        + "\nMove the CLI into a main() behind `if __name__ == \"__main__\":`.")


def test_the_exit_detector_would_catch_a_regression(tmp_path):
    """Guard the guard: prove the AST check is not vacuous."""
    bad = tmp_path / "test_bad_example.py"
    bad.write_text("import sys\nsys.exit(0)\n")
    assert _unguarded_module_level_exits(str(bad)) == [2]

    good = tmp_path / "test_good_example.py"
    good.write_text(
        "import sys\n\n\ndef main():\n    return 0\n\n\n"
        "if __name__ == \"__main__\":\n    sys.exit(main())\n")
    assert _unguarded_module_level_exits(str(good)) == []


@pytest.mark.parametrize("module_name,script_rel", [
    ("regression_test", "scripts/regression_test.py"),
    ("test_f5_parse_suffix", "scripts/test_f5_parse_suffix.py"),
])
def test_importing_the_script_raises_no_systemexit(module_name, script_rel):
    """
    Imports the module in a clean subprocess exactly as pytest's collector
    would, and requires that it neither exits nor prints.
    """
    probe = (
        "import importlib, sys, io, contextlib\n"
        "sys.path.insert(0, %r)\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):\n"
        "    importlib.import_module(%r)\n"
        "assert buf.getvalue() == '', 'module printed at import: ' + buf.getvalue()[:200]\n"
        "print('CLEAN')\n"
    ) % (os.path.join(ROOT, "scripts"), module_name)

    proc = subprocess.run([sys.executable, "-c", probe],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        "importing %s is not side-effect free:\n%s" % (script_rel, proc.stderr[-1500:]))
    assert "CLEAN" in proc.stdout


def test_pytest_collection_of_the_scripts_tree_is_not_aborted():
    """
    The end-to-end property: pytest can COLLECT the scripts/ tree without the
    session dying. Collection-only, so nothing is executed.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "scripts/"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    combined = proc.stdout + proc.stderr
    assert "INTERNALERROR" not in combined, combined[-2000:]
    assert "caught unexpected SystemExit" not in combined, combined[-2000:]


def test_the_two_repaired_scripts_still_expose_their_cli():
    """
    Import safety must not have cost the scripts their entry points -- both
    are invoked by workflows (fetch-slate.yml runs regression_test.py).
    """
    for rel, expected in (("scripts/regression_test.py", ("check_slate", "main")),
                          ("scripts/test_f5_parse_suffix.py", ("run_cases", "main"))):
        with open(os.path.join(ROOT, rel)) as handle:
            tree = ast.parse(handle.read())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in expected:
            assert name in defined, "%s lost %s()" % (rel, name)
        assert any(_is_main_guard(n) for n in tree.body), \
            "%s must keep its `if __name__ == \"__main__\":` entry point" % rel


# ── A. A test must not run a destructive command against canonical data ──────

def _destructive_subprocess_calls(path):
    """
    (lineno, flags, isolated) for each subprocess call in `path` whose
    argument list carries a destructive flag. `isolated` is True when the
    enclosing test takes pytest's `tmp_path` fixture -- the repository's
    established way of confining a write.
    """
    with open(path) as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)

    enclosing = {}
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(func):
                enclosing[sub] = func

    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in SUBPROCESS_CALLS):
            continue
        literals = {a.value for a in ast.walk(node)
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        flags = literals & DESTRUCTIVE_FLAGS
        if not flags:
            continue
        func = enclosing.get(node)
        params = set()
        if func is not None:
            params = {a.arg for a in func.args.args}
        found.append((node.lineno, sorted(flags), "tmp_path" in params))
    return found


def test_no_test_runs_a_destructive_command_outside_tmp_path():
    """
    The CLV migration test used to run `--apply` against the canonical
    ledger. Any test invoking a destructive CLI must confine it to tmp_path.
    """
    offenders = []
    for dirpath, dirnames, filenames in os.walk(_HERE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            for lineno, flags, isolated in _destructive_subprocess_calls(path):
                if not isolated:
                    offenders.append("%s:%d runs %s without the tmp_path fixture"
                                     % (os.path.relpath(path, ROOT), lineno, flags))
    assert offenders == [], (
        "a test invokes a destructive command without isolating it:\n  "
        + "\n  ".join(offenders)
        + "\nPass the target path explicitly (e.g. --ledger/--bets-path) into "
          "a tmp_path copy. See tests/conftest.py for the incident this "
          "prevents.")


def test_the_destructive_detector_would_catch_a_regression(tmp_path):
    """Guard the guard, with the exact shape of the original defect."""
    bad = tmp_path / "test_bad.py"
    bad.write_text(
        "import subprocess\n\n\n"
        "def test_thing():\n"
        "    subprocess.run(['python3', 'migrate.py', '--apply'])\n")
    assert _destructive_subprocess_calls(str(bad)) == [(5, ["--apply"], False)]

    good = tmp_path / "test_good.py"
    good.write_text(
        "import subprocess\n\n\n"
        "def test_thing(tmp_path):\n"
        "    subprocess.run(['python3', 'migrate.py', '--apply',\n"
        "                    '--ledger', str(tmp_path / 'x.jsonl')])\n")
    assert _destructive_subprocess_calls(str(good)) == [(5, ["--apply"], True)]


def test_the_canonical_evidence_guard_is_actually_installed():
    """
    tests/conftest.py's session fixture is what proves the suite left the
    ledger byte-identical. Deleting it would silently remove that proof.
    """
    conftest = os.path.join(_HERE, "conftest.py")
    assert os.path.exists(conftest), "tests/conftest.py is missing"
    with open(conftest) as handle:
        source = handle.read()
    assert "_canonical_evidence_unchanged" in source
    assert "data/edgelab/bets/bets.jsonl" in source
    assert 'scope="session"' in source and "autouse=True" in source
