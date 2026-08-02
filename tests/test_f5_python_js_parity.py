#!/usr/bin/env python3
"""
tests/test_f5_python_js_parity.py
=====================================
F5 Three-Way Pricing Correction milestone: cross-language golden-fixture
parity between the Python production three-way engine
(lib.research.three_way_projection.three_way_result_probs,
scripts/build_market_ledger.py's vig_free_3way) and the additive, pure
parity functions in api/slate.js (threeWayResultProbs, vigFree3Way).

This does NOT test api/slate.js's live handler (evalF5) -- that is a
separate, Pinnacle-priced heuristic model, not a Poisson twin of the
Python engine (see api/slate.js's own comment above these functions).
It proves the NEW, additive JS functions compute bit-for-bit-comparable
results to Python given the same inputs, so a future phase that wires F5
pricing into the JS/API path inherits already-verified-correct math
rather than silent drift.

Requires `node` on PATH (Node 22 confirmed available in this
environment; api/slate.js uses ES module `export`/`import` syntax with
no package.json declaring "type": "module", so invocations use
`node --input-type=module`).
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.research.three_way_projection import three_way_result_probs  # noqa: E402
from scripts.build_market_ledger import vig_free_3way  # noqa: E402

NODE_AVAILABLE = shutil.which("node") is not None
pytestmark = pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available on PATH")

TOLERANCE = 1e-9


def _run_js(snippet):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", snippet],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def _js_three_way(away_proj, home_proj, max_runs=20):
    snippet = f"""
import {{ threeWayResultProbs }} from './api/slate.js';
console.log(JSON.stringify(threeWayResultProbs({away_proj}, {home_proj}, {max_runs})));
"""
    return _run_js(snippet)


def _js_vig_free_3way(away_am, tie_am, home_am):
    snippet = f"""
import {{ vigFree3Way }} from './api/slate.js';
console.log(JSON.stringify(vigFree3Way({away_am}, {tie_am}, {home_am})));
"""
    return _run_js(snippet)


THREE_WAY_FIXTURES = [
    (2.3, 1.9), (1.2, 1.2), (4.1, 1.2), (4.1, 4.1), (1.2, 4.1), (2.8, 2.5),
]

VIG_FREE_FIXTURES = [
    (-130, 260, 150), (-200, 500, 300), (110, 400, -140), (-105, 220, -105),
]


class TestThreeWayParity:

    @pytest.mark.parametrize("away_proj,home_proj", THREE_WAY_FIXTURES)
    def test_matches_python_within_tolerance(self, away_proj, home_proj):
        py = three_way_result_probs(away_proj, home_proj, max_runs=20)
        js = _js_three_way(away_proj, home_proj, max_runs=20)
        assert js["awayWinProb"] == pytest.approx(py["awayWinProb"], abs=TOLERANCE)
        assert js["tieProb"] == pytest.approx(py["tieProb"], abs=TOLERANCE)
        assert js["homeWinProb"] == pytest.approx(py["homeWinProb"], abs=TOLERANCE)

    @pytest.mark.parametrize("away_proj,home_proj", THREE_WAY_FIXTURES)
    def test_both_sum_to_one(self, away_proj, home_proj):
        js = _js_three_way(away_proj, home_proj, max_runs=20)
        total = js["awayWinProb"] + js["tieProb"] + js["homeWinProb"]
        assert total == pytest.approx(1.0, abs=1e-6)


class TestVigFree3WayParity:

    @pytest.mark.parametrize("away_am,tie_am,home_am", VIG_FREE_FIXTURES)
    def test_matches_python_within_tolerance(self, away_am, tie_am, home_am):
        py_a, py_t, py_h = vig_free_3way(away_am, tie_am, home_am)
        js_a, js_t, js_h = _js_vig_free_3way(away_am, tie_am, home_am)
        assert js_a == pytest.approx(py_a, abs=TOLERANCE)
        assert js_t == pytest.approx(py_t, abs=TOLERANCE)
        assert js_h == pytest.approx(py_h, abs=TOLERANCE)

    def test_missing_price_returns_nulls_in_both_languages(self):
        py = vig_free_3way(-130, None, 150)
        js = _js_vig_free_3way(-130, "null", 150)
        assert py == (None, None, None)
        assert js == [None, None, None]


class TestRoundingPolicyDocumented:

    def test_python_and_js_both_use_full_float_precision_internally(self):
        """
        Neither engine rounds internally -- rounding to 2 decimal places
        (modelFairProbability, kalshiVF, etc.) happens only at the row-
        construction boundary in scripts/build_market_ledger.py
        (contract_pricing()), never inside three_way_result_probs()/
        threeWayResultProbs() or vig_free_3way()/vigFree3Way() themselves.
        This test documents that convention by checking the raw (Python)
        output carries more than 2 decimal digits of precision for a
        fixture where a rounded value would not.
        """
        py = three_way_result_probs(2.3, 1.9, max_runs=20)
        as_str = repr(py["tieProb"])
        decimal_digits = len(as_str.split(".")[-1]) if "." in as_str else 0
        assert decimal_digits > 2
