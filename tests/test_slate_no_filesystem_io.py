#!/usr/bin/env python3
"""
tests/test_slate_no_filesystem_io.py
========================================
Regression coverage for docs/PRODUCTION_INCIDENT_SLATE_FS_IMPORT.md:
a prior revision added `import { readFileSync } from 'fs'` plus an
`import.meta.url`-relative read to api/slate.js (Sentinel Single-Source
mission). That top-level `import` of a Node builtin -- the only
filesystem/builtin-module dependency ever added to any api/*.js file --
broke /api/slate in the deployed Vercel environment on every request,
while every sibling endpoint (teamstats/pitchers/weather/bullpen, none of
which touch `fs`) kept working. The bug was in the *module-level import
statement itself* being unresolvable in that bundled environment, not in
the `readFileSync()` call it wrapped in try/catch -- a static ES `import`
that a bundler can't resolve aborts module evaluation before any
try/catch in the module ever runs, so the existing "safe fallback"
design could not and did not protect against it.

The fix removed the runtime file read entirely; api/slate.js now keeps
only a hardcoded literal for the sentinel value set (matching its own
top-of-file "Pure: no I/O, no clock reads, no mutation" contract).

Requires `node` on PATH, same as the other api/slate.js parity test files.
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH = os.path.join(ROOT, "api", "slate.js")

NODE_AVAILABLE = shutil.which("node") is not None
pytestmark = pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available on PATH")

# Matches an import/require of a filesystem or other I/O-capable Node
# builtin module, or any use of import.meta (the mechanism the incident's
# import.meta.url-relative path depended on). Deliberately broad -- this
# guardrail exists specifically to catch a NEW attempt to reintroduce
# runtime I/O into this file, not just the exact prior pattern.
_FORBIDDEN_PATTERNS = [
    re.compile(r"""from\s+['"]node:?fs(/promises)?['"]"""),
    re.compile(r"""require\(\s*['"]node:?fs(/promises)?['"]\s*\)"""),
    re.compile(r"\breadFileSync\b"),
    re.compile(r"\breadFile\b"),
    re.compile(r"\bimport\.meta\b"),
    re.compile(r"""from\s+['"]node:?path['"]"""),
]


def _slate_source():
    with open(SLATE_PATH) as f:
        return f.read()


def _slate_source_code_only():
    """
    Source with `//`-style line comments stripped, so the guardrail below
    scans actual code -- not this file's own explanatory prose about the
    incident, which necessarily mentions the forbidden identifiers by name.
    Good enough for this file: it has no block comments containing the
    forbidden patterns, and no string literal legitimately needs them
    either.
    """
    return "\n".join(
        line for line in _slate_source().split("\n")
        if not line.strip().startswith("//")
    )


class TestNoFilesystemIoInSlateJs:
    """
    Structural guardrail: api/slate.js must never read from the local
    filesystem or reference a Node builtin I/O module. This is a permanent
    contract (the file's own header comment already says "Pure: no I/O"),
    not just a fix for this one incident.
    """

    def test_no_forbidden_io_patterns_anywhere_in_the_file(self):
        src = _slate_source_code_only()
        hits = [p.pattern for p in _FORBIDDEN_PATTERNS if p.search(src)]
        assert not hits, f"api/slate.js reintroduced filesystem/builtin-module I/O: {hits}"

    def test_module_has_no_top_level_import_statements_at_all(self):
        """
        The incident's root cause was specifically a top-level `import`
        statement failing to resolve in the deployed bundle -- a failure
        mode no in-function try/catch can protect against. The strongest
        guarantee against a repeat is that this module has NO top-level
        `import` statements of any kind (it only ever needs its own
        `export`ed pure functions plus whatever `fetch()`/globals the
        Vercel Node runtime already provides).
        """
        src = _slate_source()
        import_lines = [
            line for line in src.split("\n")
            if re.match(r"^\s*import\s", line)
        ]
        assert import_lines == [], f"api/slate.js has top-level import statement(s): {import_lines}"


class TestModuleLoadsCleanlyEvenWhenTargetFileIsMissing:
    """
    Reproduces the incident's actual environment shape: a copy of
    api/slate.js placed where lib/sentinel_constants.json (the file the
    removed code used to read) does not exist at all -- simulating a
    bundler that either never traces the JSON asset or relocates the
    function relative to a different filesystem layout. Proves the module
    still imports and evaluates without throwing, and that
    isSentinelPrice() still returns correct values -- i.e. the fix doesn't
    merely "usually work locally", it has zero dependency on that file
    being present at all.
    """

    def test_import_succeeds_and_values_are_correct_with_no_lib_directory_present(self, tmp_path):
        api_dir = tmp_path / "api"
        api_dir.mkdir()
        (api_dir / "slate.js").write_text(_slate_source())
        # Deliberately no lib/ directory created under tmp_path at all --
        # lib/sentinel_constants.json cannot be found by any relative path
        # from this copy, simulating the exact incident shape.
        snippet = """
import { isSentinelPrice } from './api/slate.js';
console.log(JSON.stringify({
  sentinel19900: isSentinelPrice(19900),
  sentinelNeg19900: isSentinelPrice(-19900),
  ordinary199: isSentinelPrice(199),
  ordinary50: isSentinelPrice(50),
}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", snippet],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"module import/evaluation failed with no lib/ directory present "
            f"(this is the exact incident reproduction): {result.stderr}"
        )
        import json
        values = json.loads(result.stdout)
        assert values == {
            "sentinel19900": True,
            "sentinelNeg19900": True,
            "ordinary199": False,
            "ordinary50": False,
        }
