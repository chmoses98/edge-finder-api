#!/usr/bin/env python3
"""
tests/test_risk_gate_review_parts_v_to_y.py
================================================
PR #8 hardening review, Parts V-Y.

Part V: subprocess compatibility from an explicitly non-repository cwd,
proving __file__-relative path resolution is independent of the
caller's working directory (extends
tests/test_risk_gate_workflow_subprocess.py's existing sandboxed-cwd
coverage).

Part W: tests/risk_gate_trace.py review -- never imported by production
code, observes (calls the real functions) rather than reimplementing
their logic, and would fail if production rule order drifted (proven
structurally + by re-running its own existing equivalence test).

Part X: test isolation -- confirms risk_gate.py makes zero network
calls of any kind (no requests/urllib/http.client/socket imports), so
"no actual betting API or exchange endpoint is touched" is true by
construction, not merely by test discipline.

Part Y: final scope/diff re-verification, now that this whole hardening
review has added many new commits -- confirms the ONLY non-test,
non-docs file this PR touches, from start to finish, is
scripts/risk_gate.py.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_SCRIPTS_DIR = os.path.join(ROOT, "scripts")
REAL_LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, REAL_LIB_DIR)
sys.path.insert(0, REAL_SCRIPTS_DIR)

from test_risk_gate_immutable import make_entry, make_game, make_slate


# ══════════════════════════════════════════════════════════════════════════════
# Part V: subprocess from a non-repository cwd
# ══════════════════════════════════════════════════════════════════════════════

class TestSubprocessFromNonRepositoryCwd:

    def test_slate_and_meta_paths_resolve_correctly_regardless_of_cwd_but_pipeline_root_does_not(self, tmp_path):
        """
        Copies risk_gate.py + its lib/ dependencies into tmp_path/repo/
        (the sandbox), then invokes it with cwd set to a SIBLING tmp
        directory that has NOTHING to do with the sandbox.

        REAL FINDING (not a Phase 7 regression -- see below): SLATE_PATH/
        META_PATH (__file__-relative, computed from risk_gate.py's own
        location) resolve correctly regardless of cwd. But
        lib/pipeline_artifacts.PIPELINE_ROOT = os.path.join("data",
        "pipeline") is a BARE RELATIVE STRING, resolved against the
        process's cwd at write time, not __file__ -- confirmed by
        reading lib/pipeline_artifacts.py directly. So the best-effort
        execution.json artifact lands under cwd/data/pipeline/<date>/,
        NOT sandbox/data/pipeline/<date>/, when cwd differs from the
        script's own directory tree.

        This is NOT introduced by Phase 7: PIPELINE_ROOT and
        write_stage_artifact() are pre-existing lib/pipeline_artifacts.py
        code (Phase 3/4, untouched by this PR -- confirmed in Part Y),
        already shared by build_market_ledger.py's projections.json/
        recommendations.json writes, which have the identical cwd-
        dependence. In actual production
        (.github/workflows/fetch-slate.yml), every step -- including
        risk_gate.py's -- runs with no `working-directory:` override,
        so GitHub Actions' default cwd ($GITHUB_WORKSPACE, the repo
        root) always matches where SLATE_PATH/META_PATH resolve to
        anyway -- confirmed by grepping the workflow file for
        `working-directory:` (zero matches). So this asymmetry is latent
        under real production conditions, not a live bug -- but it IS a
        real, previously-undocumented behavioral difference between the
        two path-resolution strategies, worth recording precisely rather
        than silently discovering it as a surprise later.
        """
        import shutil, json

        sandbox = tmp_path / "repo"
        (sandbox / "scripts").mkdir(parents=True)
        (sandbox / "lib").mkdir(parents=True)
        (sandbox / "data").mkdir(parents=True)
        shutil.copy2(os.path.join(REAL_SCRIPTS_DIR, "risk_gate.py"), sandbox / "scripts" / "risk_gate.py")
        for lib_file in ("postponed_guard.py", "atomic_json.py", "pipeline_artifacts.py"):
            shutil.copy2(os.path.join(REAL_LIB_DIR, lib_file), sandbox / "lib" / lib_file)

        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(sandbox / "data" / "slate.json", 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        unrelated_cwd = tmp_path / "totally_unrelated_directory"
        unrelated_cwd.mkdir()

        result = subprocess.run(
            [sys.executable, str(sandbox / "scripts" / "risk_gate.py")],
            cwd=str(unrelated_cwd),  # NOT the sandbox root
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr

        # SLATE_PATH/META_PATH: correctly resolved via __file__, land in
        # the SANDBOX regardless of cwd.
        with open(sandbox / "data" / "slate.json") as f:
            written_slate = json.load(f)
        assert written_slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'
        assert (sandbox / "data" / "meta.json").exists()

        # PIPELINE_ROOT: cwd-relative -- the execution.json artifact
        # lands under the UNRELATED cwd, not the sandbox, confirming the
        # documented asymmetry precisely (and that this doesn't crash or
        # silently corrupt anything -- it's a location surprise, not a
        # data-integrity issue).
        leaked_artifact = unrelated_cwd / "data" / "pipeline" / "2026-06-16" / "execution.json"
        assert leaked_artifact.exists(), (
            "expected the cwd-relative PIPELINE_ROOT behavior to place "
            "execution.json under the unrelated cwd -- if this now fails, "
            "PIPELINE_ROOT's resolution strategy changed and this test's "
            "documentation above needs updating"
        )
        assert not (sandbox / "data" / "pipeline").exists(), (
            "execution.json unexpectedly landed in the sandbox despite "
            "PIPELINE_ROOT being cwd-relative -- investigate"
        )

    def test_invocation_via_absolute_path_from_repository_root_itself(self, tmp_path):
        """Sanity companion case: invoking via an absolute path while
        cwd IS the sandbox root (the conventional case, matching the
        workflow's own `python3 scripts/risk_gate.py` relative-path
        invocation style) still works identically."""
        import shutil, json

        sandbox = tmp_path / "repo2"
        (sandbox / "scripts").mkdir(parents=True)
        (sandbox / "lib").mkdir(parents=True)
        (sandbox / "data").mkdir(parents=True)
        shutil.copy2(os.path.join(REAL_SCRIPTS_DIR, "risk_gate.py"), sandbox / "scripts" / "risk_gate.py")
        for lib_file in ("postponed_guard.py", "atomic_json.py", "pipeline_artifacts.py"):
            shutil.copy2(os.path.join(REAL_LIB_DIR, lib_file), sandbox / "lib" / lib_file)

        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(sandbox / "data" / "slate.json", 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        result = subprocess.run(
            [sys.executable, "scripts/risk_gate.py"],  # relative, matching the real workflow's own invocation
            cwd=str(sandbox),
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr


# ══════════════════════════════════════════════════════════════════════════════
# Part W: tests/risk_gate_trace.py review
# ══════════════════════════════════════════════════════════════════════════════

class TestTraceHelperIsTestOnly:

    def test_risk_gate_trace_never_imported_by_any_production_module(self):
        """Grep every production file (scripts/, lib/, .github/) for an
        import of risk_gate_trace -- must be zero. Confirms this module
        can never be accidentally packaged into or executed as part of
        the real pipeline."""
        result = subprocess.run(
            ['grep', '-rl', 'risk_gate_trace', 'scripts/', 'lib/', '.github/'],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", (
            f"risk_gate_trace referenced in production code: {result.stdout}"
        )

    def test_risk_gate_trace_calls_real_functions_never_reimplements_decision_logic(self):
        """Structural check: tests/risk_gate_trace.py's build_decision_trace()
        must call rg.apply_tt_safety/rg.apply_portfolio_rules (the REAL
        production functions) rather than containing its own copy of the
        evidence-check/edge-check/portfolio-rule logic."""
        with open(os.path.join(ROOT, "tests", "risk_gate_trace.py")) as f:
            source = f.read()
        assert "rg.apply_tt_safety(" in source
        assert "rg.apply_portfolio_rules(" in source
        # Structural absence proof: none of the ACTUAL rule constants/
        # threshold comparisons risk_gate.py implements are reimplemented
        # here (would indicate a parallel, potentially-drifting logic copy).
        for forbidden in ("TT_MIN_EDGE_PCT", "TT_MAX_STAKE", "DAILY_RISK_CAP", "< 2.5", "> 20.0", "> 40.0"):
            assert forbidden not in source, (
                f"risk_gate_trace.py appears to reimplement a threshold "
                f"comparison ({forbidden!r}) rather than observing the "
                f"real function's output"
            )

    def test_trace_equivalence_test_actually_exists_and_passes(self):
        """Re-run the trace module's own production-equivalence test as
        a live smoke check within this review (not just trusting it was
        green when originally written)."""
        result = subprocess.run(
            [sys.executable, '-m', 'pytest',
             'tests/test_risk_gate_decision_trace.py::TestDecisionTraceProductionEquivalence',
             '-q'],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "2 passed" in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# Part X: test isolation -- zero network capability by construction
# ══════════════════════════════════════════════════════════════════════════════

class TestNoNetworkCapabilityByConstruction:

    def test_risk_gate_imports_no_network_library_at_all(self):
        with open(os.path.join(REAL_SCRIPTS_DIR, "risk_gate.py")) as f:
            source = f.read()
        for forbidden_import in (
            "import requests", "import urllib", "import http.client",
            "import socket", "from requests", "from urllib", "import httpx",
            "import aiohttp",
        ):
            assert forbidden_import not in source, (
                f"risk_gate.py imports a network-capable library: {forbidden_import!r}"
            )

    def test_dependency_chain_also_imports_no_network_library(self):
        """postponed_guard.py, atomic_json.py, pipeline_artifacts.py --
        the only three non-stdlib modules risk_gate.py's own import
        graph touches -- must also be network-free."""
        for module_file in ("postponed_guard.py", "atomic_json.py", "pipeline_artifacts.py"):
            with open(os.path.join(REAL_LIB_DIR, module_file)) as f:
                source = f.read()
            for forbidden_import in ("import requests", "import urllib", "import socket", "import http.client"):
                assert forbidden_import not in source, f"{module_file} imports {forbidden_import!r}"


# ══════════════════════════════════════════════════════════════════════════════
# Part Y: final scope/diff re-verification
# ══════════════════════════════════════════════════════════════════════════════

class TestFinalScopeReVerification:

    def test_only_risk_gate_py_changed_outside_tests_and_docs_across_the_whole_pr(self):
        result = subprocess.run(
            ['git', 'diff', '--name-only', 'origin/main...HEAD', '--',
             '.', ':!tests', ':!docs'],
            cwd=ROOT, capture_output=True, text=True,
        )
        changed = [l for l in result.stdout.strip().splitlines() if l]
        assert changed == ['scripts/risk_gate.py'], (
            f"unexpected non-test, non-docs files changed: {changed}"
        )

    @pytest.mark.parametrize("forbidden_path", [
        'scripts/build_market_ledger.py',
        'scripts/bet_eligibility.py',
        'scripts/protect_slate.py',
        'scripts/validate_slate_final.py',
        'lib/slate_manager.py',
        'config/rules.json',
        'RULES.md',
        'BET_LOG.md',
        'data/bets.json',
        '.github/workflows/fetch-slate.yml',
        '.github/workflows/clv-update.yml',
    ])
    def test_specific_forbidden_files_untouched(self, forbidden_path):
        result = subprocess.run(
            ['git', 'log', '--oneline', 'origin/main...HEAD', '--', forbidden_path],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", f"{forbidden_path} was touched by this PR: {result.stdout}"

    def test_no_secrets_or_credentials_pattern_in_the_diff(self):
        """
        Scoped to the actual PRODUCTION code diff (scripts/risk_gate.py)
        and docs/ -- not tests/, which legitimately contains these very
        pattern strings as literals inside grep-based absence-checking
        tests (this test's own sibling files check for "bankroll",
        "pinnacle", etc. as forbidden substrings, and Part K/J's tests
        literally reference 'api_key'-shaped concepts in assertions and
        comments) -- scanning tests/ would produce guaranteed self-
        referential false positives, not a meaningful secret-leak signal.
        """
        result = subprocess.run(
            ['git', 'diff', 'origin/main...HEAD', '--', 'scripts/', 'docs/'],
            cwd=ROOT, capture_output=True, text=True,
        )
        diff_text = result.stdout.lower()
        for pattern in ('api_key', 'apikey', 'secret_key', 'password=', 'private_key', 'bearer '):
            assert pattern not in diff_text, f"potential secret-like pattern found in diff: {pattern!r}"
