#!/usr/bin/env python3
"""
tests/test_kalshi_registry_series_parity.py
===============================================
Market-Universe Parity mission regression guard.

Root cause this locks in against recurring: api/kalshisearch.js's
ALL_SERIES list (the broad fetch/archive layer) and
lib.research.market_taxonomy.CONFIRMED_SINGLE_GAME_SERIES_TICKERS (the
Python source of truth for confirmed real single-game MLB series) both
grew from 8 to 17 series over time (F3/F7 winner markets plus 7
pitcher/hitter player-prop families), but
scripts/build_kalshi_registry.py's own SERIES_CATALOGUE -- which builds
the per-game registry scripts/merge_odds.py and, ultimately,
data/slate.json are derived from -- was never updated to match, so
those 9 families were fetched and archived but silently never reached
the slate ChatGPT sees. This test reads (never imports/executes;
build_kalshi_registry.py makes real, unconditional live Kalshi HTTP
calls at import time) both source files and fails if their series sets
diverge again, with no ambiguity about which side moved.

scripts/build_kalshi_registry.py has no `if __name__` guard (documented,
intentional -- see tests/test_kalshi_discovery_entry_points.py's
docstring), so its SERIES_CATALOGUE dict is extracted via `ast`
(literal_eval on the assignment's value node) rather than importing the
module -- exact same technique already used by
tests/test_discover_kalshi_series_catalogue.py for a sibling script.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.research.market_taxonomy import (  # noqa: E402
    CONFIRMED_SINGLE_GAME_SERIES_TICKERS, SPECULATIVE_UNCONFIRMED_SERIES_TICKERS,
)


def _read(rel_path):
    with open(os.path.join(ROOT, rel_path)) as f:
        return f.read()


def _extract_dict_literal_keys(source, var_name):
    """AST-parse `source` and literal_eval the dict assigned to `var_name`
    at module scope, returning its key set. Raises AssertionError with a
    clear message if the name isn't found or isn't a dict literal."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var_name for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, dict), f"{var_name} is not a dict literal"
            return set(value.keys())
    raise AssertionError(f"{var_name} not found as a module-level assignment")


def _extract_js_array_literal(source, var_name):
    """Extract a simple `const NAME = [...]` JS string-array literal's entries."""
    m = re.search(rf"const {re.escape(var_name)} = \[(.*?)\];", source, re.DOTALL)
    assert m, f"{var_name} array literal not found in source"
    entries = re.findall(r"'([^']+)'", m.group(1))
    assert entries, f"{var_name} array literal parsed to zero entries"
    return set(entries)


class TestBuildKalshiRegistrySeriesCatalogueParity:

    def test_series_catalogue_matches_kalshisearch_all_series(self):
        """
        The exact regression this mission fixes: build_kalshi_registry.py's
        SERIES_CATALOGUE must cover every series api/kalshisearch.js's
        ALL_SERIES fetches — anything ALL_SERIES adds in the future and
        SERIES_CATALOGUE doesn't pick up would silently vanish before
        reaching data/slate.json again, exactly as F3/F7 and the 7
        player-prop families did.
        """
        registry_series = _extract_dict_literal_keys(
            _read("scripts/build_kalshi_registry.py"), "SERIES_CATALOGUE"
        )
        all_series = _extract_js_array_literal(_read("api/kalshisearch.js"), "ALL_SERIES")

        missing_from_registry = all_series - registry_series
        assert not missing_from_registry, (
            f"api/kalshisearch.js fetches {sorted(missing_from_registry)} but "
            f"scripts/build_kalshi_registry.py's SERIES_CATALOGUE does not — "
            f"these market families will never reach data/slate.json. Add them "
            f"to SERIES_CATALOGUE (and a parsing block) to fix."
        )

    def test_series_catalogue_matches_confirmed_taxonomy(self):
        """
        Cross-check against the Python source of truth
        (lib.research.market_taxonomy.CONFIRMED_SINGLE_GAME_SERIES_TICKERS)
        independently of the JS file, so a drift in either direction is
        caught even if only one of the two sources moves.
        """
        registry_series = _extract_dict_literal_keys(
            _read("scripts/build_kalshi_registry.py"), "SERIES_CATALOGUE"
        )
        missing = CONFIRMED_SINGLE_GAME_SERIES_TICKERS - registry_series
        assert not missing, (
            f"Confirmed series {sorted(missing)} exist in "
            f"lib.research.market_taxonomy but are missing from "
            f"scripts/build_kalshi_registry.py's SERIES_CATALOGUE."
        )

    def test_speculative_unconfirmed_series_intentionally_excluded(self):
        """
        KXMLBF3SPREAD/F3TOTAL/F7SPREAD/F7TOTAL are documented, evidence-based
        exclusions (guessed names, never observed on the real Kalshi
        exchange) -- not a parity gap. This test documents that exclusion
        explicitly so it reads as intentional, not as this suite failing
        to notice a real gap.
        """
        registry_series = _extract_dict_literal_keys(
            _read("scripts/build_kalshi_registry.py"), "SERIES_CATALOGUE"
        )
        assert registry_series.isdisjoint(SPECULATIVE_UNCONFIRMED_SERIES_TICKERS), (
            "SERIES_CATALOGUE should not include never-confirmed speculative "
            "series tickers"
        )

    def test_original_eight_series_still_present(self):
        """The original 8 real-money-eligible series must never be removed."""
        registry_series = _extract_dict_literal_keys(
            _read("scripts/build_kalshi_registry.py"), "SERIES_CATALOGUE"
        )
        for series in ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL",
                       "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI"):
            assert series in registry_series
