#!/usr/bin/env python3
"""
tests/test_kalshi_discovery_entry_points.py
================================================
Model Performance Phase 2A -- structural content tests for the four
discovery entry points the mission named as previously fixed-allowlist-
limited: api/kalshisearch.js, scripts/build_kalshi_registry.py,
scripts/fetch_kalshi_markets.py, api/odds.js.

These files are Node.js serverless functions (api/*.js) or top-level
executing Python scripts with real, unconditional live Kalshi HTTP
calls (no `if __name__` guard) -- consistent with this repository's
existing convention (see tests/test_api_date.py), they are tested by
reading their source and asserting specific structural properties, not
by importing/executing them (which would attempt real network I/O).
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel_path):
    with open(os.path.join(ROOT, rel_path)) as f:
        return f.read()


class TestKalshiSearchJsBroadDiscovery:

    def test_all_series_allowlist_unchanged(self):
        """The existing 8-series allowlist must remain -- production's
        registry-building backfill still depends on its exact shape."""
        src = _read("api/kalshisearch.js")
        for series in ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL",
                       "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI"):
            assert series in src

    def test_broad_unfiltered_discovery_pass_added(self):
        src = _read("api/kalshisearch.js")
        assert "discoveredUnknownSeriesMarkets" in src
        assert "ALL_SERIES.includes(series)" in src

    def test_broad_pass_does_not_replace_per_series_loop(self):
        """Every series in the allowlist is still fetched individually.

        This used to grep for the literal `for (const series of ALL_SERIES)`.
        The loop now iterates `fetchOrder`, a deterministic rotation of
        ALL_SERIES (PHASE F: a rate limit must not keep truncating the same
        families), so the string no longer appears -- but the invariant it
        was standing in for is unchanged and is now asserted directly: the
        loop covers a permutation of ALL_SERIES, losing none of them.
        """
        src = _read("api/kalshisearch.js")
        assert "for (const series of fetchOrder)" in src
        assert "rotateSeries(ALL_SERIES, seed)" in src

    def test_the_rotated_fetch_order_is_a_permutation_losing_no_series(self):
        """The rotation must never be able to drop a family it reorders."""
        import json as _json
        import shutil as _shutil
        import subprocess as _subprocess
        if _shutil.which("node") is None:
            import pytest as _pytest
            _pytest.skip("node is not available in this environment")
        endpoint = os.path.join(ROOT, "api", "kalshisearch.js")
        driver = (
            "import { rotateSeries } from %s;\n"
            "const S = ['a','b','c','d','e','f','g'];\n"
            "const out = [];\n"
            "for (let s = 0; s < 40; s++) out.push(rotateSeries(S, s).slice().sort().join(','));\n"
            "process.stdout.write(JSON.stringify([...new Set(out)]));\n"
            % _json.dumps(endpoint)
        )
        proc = _subprocess.run(["node", "--input-type=module", "-e", driver],
                               capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert _json.loads(proc.stdout) == ["a,b,c,d,e,f,g"], (
            "every rotation must contain exactly the same series")

    def test_f3_f7_title_classification_added(self):
        src = _read("api/kalshisearch.js")
        assert "f3_moneyline" in src
        assert "f7_moneyline" in src

    def test_existing_output_fields_preserved(self):
        """Additive-only: existing consumers read markets/results/series_counts."""
        src = _read("api/kalshisearch.js")
        for field in ("markets:", "results:", "series_counts:"):
            assert field in src


class TestOddsJsNoSilentDrop:

    def test_f3_f7_branches_added(self):
        src = _read("api/odds.js")
        assert "f3ml" in src
        assert "f7ml" in src

    def test_catch_all_no_silent_drop_added(self):
        src = _read("api/odds.js")
        assert "game.unclassified" in src

    def test_f5_branch_unchanged(self):
        src = _read("api/odds.js")
        assert "game.f5ml" in src


class TestBuildKalshiRegistryPyDiscovery:

    def test_series_catalogue_allowlist_unchanged(self):
        """Production's registry shape/activation gate must remain
        unchanged -- broad discovery is additive only."""
        src = _read("scripts/build_kalshi_registry.py")
        assert "SERIES_CATALOGUE = {" in src
        for series in ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL",
                       "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI"):
            assert f"'{series}'" in src

    def test_discover_unknown_series_imported_not_reimplemented(self):
        src = _read("scripts/build_kalshi_registry.py")
        assert "from lib.kalshi_discovery import discover_unknown_series" in src

    def test_discovered_unknown_series_written_additively(self):
        src = _read("scripts/build_kalshi_registry.py")
        assert "'discoveredUnknownSeries': discovered_unknown_series" in src
        assert "'registry': registry," in src  # unchanged primary key still present

    def test_syntax_valid(self):
        ast.parse(_read("scripts/build_kalshi_registry.py"))


class TestFetchKalshiMarketsPyDiscovery:

    def test_single_hardcoded_series_ticker_unchanged(self):
        src = _read("scripts/fetch_kalshi_markets.py")
        assert "SERIES_TICKER = 'KXMLBGAME'" in src

    def test_broad_discovery_pass_added(self):
        src = _read("scripts/fetch_kalshi_markets.py")
        assert "discovered_unknown_series" in src
        assert "status=open&limit=1000" in src

    def test_market_index_output_additive(self):
        src = _read("scripts/fetch_kalshi_markets.py")
        assert "'markets': market_index" in src or "'markets': market_index," in src
        assert "'discoveredUnknownSeries': discovered_unknown_series" in src

    def test_syntax_valid(self):
        ast.parse(_read("scripts/fetch_kalshi_markets.py"))
