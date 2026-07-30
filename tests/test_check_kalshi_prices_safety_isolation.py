#!/usr/bin/env python3
"""
tests/test_check_kalshi_prices_safety_isolation.py
=======================================================
Model Performance "kalshi-standalone-price-check" -- proves the
standalone price-check tool is safety-isolated from the production
slate/projection/recommendation/risk/execution/settlement pipeline
(mission Part 12).
"""
import ast
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

FORBIDDEN_MODULES = {
    "build_market_ledger",
    "risk_gate",
    "write_pending_bets",
    "protect_slate",
    "validate_slate_final",
}

FORBIDDEN_PATHS = {
    "data/slate.json",
    "bets.json",
    "data/pending_bets.json",
    "data/execution_slip.json",
}


def _imported_module_names(source_path):
    """Static AST scan for import/import-from module names -- proves
    absence WITHOUT executing the file (which could itself be unsafe
    for a script with top-level side effects)."""
    with open(source_path) as f:
        tree = ast.parse(f.read(), filename=source_path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class TestNoForbiddenImports:

    def test_check_kalshi_prices_script_imports(self):
        imported = _imported_module_names(os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import found: {imported & FORBIDDEN_MODULES}"

    def test_kalshi_price_check_lib_imports(self):
        imported = _imported_module_names(os.path.join(ROOT, "lib", "kalshi_price_check.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import found: {imported & FORBIDDEN_MODULES}"

    def test_print_price_check_summary_script_imports(self):
        imported = _imported_module_names(os.path.join(SCRIPTS_DIR, "print_price_check_summary.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import found: {imported & FORBIDDEN_MODULES}"

    def test_parse_advanced_filters_script_imports(self):
        imported = _imported_module_names(os.path.join(SCRIPTS_DIR, "parse_advanced_filters.py"))
        assert not (imported & FORBIDDEN_MODULES), f"forbidden import found: {imported & FORBIDDEN_MODULES}"

    def _open_call_path_literals(self, source_path):
        """AST-based (not substring) scan: returns every string
        constant passed as the first argument to an open(...) call --
        prose in docstrings/comments mentioning a forbidden path (to
        document what NOT to do) is not a real code path and must not
        false-positive this check."""
        with open(source_path) as f:
            tree = ast.parse(f.read(), filename=source_path)
        literals = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literals.append(arg.value)
        return literals

    def test_no_forbidden_path_open_calls_in_script(self):
        literals = self._open_call_path_literals(os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"))
        for path in FORBIDDEN_PATHS:
            assert not any(path in lit for lit in literals), f"open() call targets forbidden path {path!r}"

    def test_no_forbidden_path_open_calls_in_lib(self):
        literals = self._open_call_path_literals(os.path.join(ROOT, "lib", "kalshi_price_check.py"))
        for path in FORBIDDEN_PATHS:
            assert not any(path in lit for lit in literals)


class TestNoProductionFileMutation:

    def _hash(self, path):
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_running_the_tool_does_not_touch_slate_or_bets(self, tmp_path):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "kalshi_search_2026-07-29_1722.json").write_text(json.dumps({
            "fetched_at": "2026-07-29T17:22:01.000Z",
            "markets": [{"market_ticker": "KXMLBF5-X-TIE", "event_ticker": "KXMLBF5-X",
                         "title": "tie after 5", "yes_bid": 0.17, "yes_ask": 0.19}],
        }))

        slate_path = os.path.join(ROOT, "data", "slate.json")
        bets_path = os.path.join(ROOT, "bets.json")
        before = (self._hash(slate_path), self._hash(bets_path))

        subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot",
             "--snapshot-path", str(snap_dir / "kalshi_search_2026-07-29_1722.json"),
             "--format", "json", "--archive"],
            cwd=str(tmp_path), capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": ROOT},
        )

        after = (self._hash(slate_path), self._hash(bets_path))
        assert before == after

    def test_archive_writes_only_under_tool_own_directory(self, tmp_path):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "kalshi_search_2026-07-29_1722.json").write_text(json.dumps({
            "fetched_at": "2026-07-29T17:22:01.000Z",
            "markets": [{"market_ticker": "KXMLBF5-X-TIE", "event_ticker": "KXMLBF5-X",
                         "title": "tie after 5", "yes_bid": 0.17, "yes_ask": 0.19}],
        }))
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "check_kalshi_prices.py"),
             "--source", "snapshot",
             "--snapshot-path", str(snap_dir / "kalshi_search_2026-07-29_1722.json"),
             "--format", "json", "--archive"],
            cwd=str(tmp_path), capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": ROOT},
        )
        assert result.returncode == 0
        archive_dir = os.path.join(ROOT, "kalshi_price_check_artifacts")
        assert os.path.isdir(archive_dir)
        import shutil
        shutil.rmtree(archive_dir, ignore_errors=True)


class TestNoRecommendationOrEdgeGenerated:

    def test_normalized_record_has_no_edge_or_recommendation_fields(self):
        from lib.kalshi_price_check import normalize_market
        record, status, reason = normalize_market({
            "market_ticker": "KXMLBF5-X-SEA", "event_ticker": "KXMLBF5-X",
            "title": "Seattle first 5 innings winner?", "yes_bid": 0.42, "yes_ask": 0.44,
        })
        forbidden_keys = {"edge", "modelProb", "recommendation", "confidenceTier", "betSize", "stake"}
        assert not (set(record.keys()) & forbidden_keys)

    def test_no_pending_bet_or_execution_function_exists_in_tool(self):
        """AST-based: no function/method NAMED like an execution
        primitive is ever DEFINED OR CALLED in this module -- prose in
        the docstring documenting what NOT to import is expected and
        must not false-positive this check."""
        with open(os.path.join(ROOT, "lib", "kalshi_price_check.py")) as f:
            tree = ast.parse(f.read())
        forbidden_names = {"write_pending_bet", "execute_bet", "submit_order", "place_bet"}
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in forbidden_names:
                found.add(node.name)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in forbidden_names:
                    found.add(name)
        assert not found
