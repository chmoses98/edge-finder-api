#!/usr/bin/env python3
"""
tests/test_slate_probability_primitives_parity.py
======================================================
Slate Probability Primitives Unification mission: audited api/slate.js's
remaining independently-implemented probability families against the
canonical production/research engine, scripts/build_market_ledger.py.

Families audited: full-game ML, NRFI/YRFI, game totals, run-line, team
totals. See this file's own docstring sections below for what was found
safe to unify vs. what was left alone and why.

UNIFIED (this file proves parity): the raw Poisson-tail math underneath
game totals, team totals, and the run-line/game-total vig-free
normalization is architecturally identical between the two languages --
api/slate.js's totalProb()/teamTotalProb()/evalRunLine()/evalGameTotal()
now delegate to two additive, pure, exported functions (pOverTotal,
vigFree2Way) that mirror build_market_ledger.py's p_over_total() and
vig_free_2way() exactly. This is the same golden-fixture parity approach
tests/test_f5_python_js_parity.py already established for
threeWayResultProbs()/vigFree3Way().

NOT unified, deliberately (see class docstrings below for the specific
reason each was left alone):
- Full-game ML: audited and found structurally similar to
  build_market_ledger.py's p_team_wins() + evaluate_game()'s extra-inning
  blend / 72% win-cap block (same constants: margin<1.5, 0.90/0.10 blend
  weight, 0.72 cap) -- but NOT unified, for two independent reasons. (1)
  The run projection formula feeding gameProbs() (JS's projectRuns()/
  calcModelProb() vs Python's compute_projections()) is genuinely
  different methodology, not just a different implementation of the same
  formula; unifying it would change slate.js's live win/total
  probabilities, a retune, not a canonicalization. (2) gameProbs()'s own
  raw win/push Poisson math is a hard, test-enforced byte-for-byte freeze
  (tests/test_settlement_reliability_milestone.py::
  TestNoProductionRecommendationChanges::
  test_full_game_js_probability_functions_byte_identical) across every
  milestone since Production Reliability and Settlement Recovery --
  confirmed by actually attempting the delegation-to-a-shared-primitive
  refactor here and having that exact guardrail fail on it, even though
  the primitive was proven bit-for-bit equal. gameProbs()/calcModelProb()
  are left completely unchanged as a result. Also note: even if the
  freeze didn't exist, gameProbs()'s 72% win-cap redistributes the capped
  excess to the other side while Python's equivalent block in
  evaluate_game() does not -- a second, independent reason a literal
  swap-in would have changed live output.
- NRFI/YRFI: JS's evalNRFI() is a categorical K%/BB%/whiff-percentage
  scoring heuristic; Python's NRFI/YRFI pricing is a Poisson(0, lambda)
  model fed by lib.research.first_inning_context's dedicated first-inning
  evidence hierarchy. These are not two implementations of the same
  formula -- there is no proven canonical equivalent to swap JS onto
  without changing its methodology outright.
- Run-line (RL_Away/RL_Home): SUSPENDED in production per Rule 81 (WR
  36%, negative CLV) -- build_market_ledger.py never computes a model
  probability for this market at all (always rejected_row()), so there
  is no canonical run-line probability formula to unify JS's
  evalRunLine() against. Its Poisson-margin computation is left as-is;
  only its vig-free normalization (a genuinely shared, generic
  computation) was unified above.
- Team totals: evalTeamTotals() prices against a synthetic line derived
  from the sportsbook game total (The Odds API has no dedicated Kalshi-
  matching TT market), while build_market_ledger.py's TT_Away_Over/
  TT_Home_Over rows price the real Kalshi team-total line -- a different
  market/data source, not a duplicated formula. (Team-total run-
  projection calibration itself was already addressed in an earlier
  mission -- see docs/DUPLICATE_LOGIC_INVENTORY.md and this repo's PR
  history for "team_total fix".) Its underlying Poisson-tail math now
  shares pOverTotal() with game totals above, same as any other caller.

Requires `node` on PATH, same as tests/test_f5_python_js_parity.py.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.build_market_ledger import p_over_total, vig_free_2way  # noqa: E402

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


def _js_p_over_total(proj, line, max_runs=30):
    snippet = f"""
import {{ pOverTotal }} from './api/slate.js';
console.log(JSON.stringify(pOverTotal({proj}, {line}, {max_runs})));
"""
    return _run_js(snippet)


def _js_vig_free_2way(american_a, american_b):
    def _fmt(v):
        return "null" if v is None else str(v)
    snippet = f"""
import {{ vigFree2Way }} from './api/slate.js';
console.log(JSON.stringify(vigFree2Way({_fmt(american_a)}, {_fmt(american_b)})));
"""
    return _run_js(snippet)


TOTAL_FIXTURES = [
    (8.2, 7), (8.2, 7.5), (9.0, 8), (5.0, 6), (11.5, 9), (7.5, 7),
]

VIG_FREE_2WAY_FIXTURES = [
    (-150, 130), (-110, -110), (200, -250), (-105, -105), (145, 120),
]


class TestPOverTotalParity:
    """totalProb()/teamTotalProb() vs. p_over_total()."""

    @pytest.mark.parametrize("proj,line", TOTAL_FIXTURES)
    def test_matches_python_within_tolerance(self, proj, line):
        py = p_over_total(proj, line, max_r=35)
        js = _js_p_over_total(proj, line, max_runs=35)
        assert js == pytest.approx(py, abs=TOLERANCE)


class TestVigFree2WayParity:
    """evalRunLine()/evalGameTotal()'s vig-free normalization vs. vig_free_2way()."""

    @pytest.mark.parametrize("american_a,american_b", VIG_FREE_2WAY_FIXTURES)
    def test_matches_python_within_tolerance(self, american_a, american_b):
        py_a, py_b = vig_free_2way(american_a, american_b)
        js_a, js_b = _js_vig_free_2way(american_a, american_b)
        assert js_a == pytest.approx(py_a, abs=TOLERANCE)
        assert js_b == pytest.approx(py_b, abs=TOLERANCE)
        assert (py_a + py_b) == pytest.approx(1.0, abs=1e-9)

    def test_missing_price_returns_nulls_in_both_languages(self):
        py = vig_free_2way(-150, None)
        js = _js_vig_free_2way(-150, None)
        assert py == (None, None)
        assert js == [None, None]


class TestSharedPrimitivesActuallyWired:
    """
    Structural proof (mirrors tests/test_f5_python_js_parity.py's
    TestEvalF5UsesCanonicalPrimitives) that totalProb()/teamTotalProb()/
    evalRunLine()/evalGameTotal() actually call the shared primitives above
    rather than keeping their own inline copies -- not just that the
    primitives exist and happen to match. Also proves the converse for
    gameProbs(): it must NOT have been wired to any shared primitive, since
    its body is frozen (see this file's module docstring).
    """

    def _slate_source(self):
        with open(os.path.join(ROOT, "api", "slate.js")) as f:
            return f.read()

    def _function_block(self, src, signature_snippet):
        lines = src.split("\n")
        start = next(i for i, l in enumerate(lines) if signature_snippet in l)
        depth = 0
        end = start
        started = False
        for i in range(start, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            if "{" in lines[i]:
                started = True
            if started and depth <= 0:
                end = i
                break
        return "\n".join(lines[start:end + 1])

    def test_game_probs_keeps_its_own_inline_double_sum(self):
        block = self._function_block(self._slate_source(), "function gameProbs(")
        assert "pOverTotal(" not in block
        assert "vigFree2Way(" not in block
        assert "poissonPMF(a, awayProj)" in block
        assert "poissonPMF(h, homeProj)" in block

    def test_total_prob_calls_p_over_total(self):
        block = self._function_block(self._slate_source(), "function totalProb(")
        assert "pOverTotal(" in block

    def test_team_total_prob_calls_p_over_total(self):
        block = self._function_block(self._slate_source(), "function teamTotalProb(")
        assert "pOverTotal(" in block

    def test_eval_run_line_calls_vig_free_2way(self):
        block = self._function_block(self._slate_source(), "function evalRunLine(")
        assert "vigFree2Way(" in block

    def test_eval_game_total_calls_vig_free_2way(self):
        block = self._function_block(self._slate_source(), "function evalGameTotal(")
        assert "vigFree2Way(" in block


class TestRunLineHasNoCanonicalProbabilityToUnifyAgainst:
    """
    Documents WHY evalRunLine()'s own Poisson-margin cover-probability
    computation was left untouched: build_market_ledger.py's RL_Away/
    RL_Home rows are unconditionally rejected (Rule 81 suspension) and
    never compute a model probability at all, so there is nothing to prove
    parity against for that specific calculation.
    """

    def test_build_market_ledger_never_computes_a_run_line_model_prob(self):
        with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as f:
            src = f.read()
        start = src.index("# ── RL_Away / RL_Home")
        end = src.index("# ── Game_Total")
        rl_block = src[start:end]
        assert "Suspended per Rule 81" in rl_block
        assert "rejected_row(" in rl_block
        assert "p_team_wins(" not in rl_block
        assert "modelProb" not in rl_block


class TestNrfiHasNoSharedFormulaToUnifyAgainst:
    """
    Documents WHY evalNRFI() was left untouched: it's a categorical
    K%/BB%/whiff-percentage scoring heuristic, not a Poisson probability
    model -- there's no shared formula with build_market_ledger.py's
    poisson_pmf(0, lambda)-based NRFI pricing to unify onto without
    replacing JS's methodology outright (a retune, out of this mission's
    scope).
    """

    def test_eval_nrfi_does_not_use_poisson(self):
        with open(os.path.join(ROOT, "api", "slate.js")) as f:
            src = f.read()
        start = src.index("function evalNRFI(")
        end = src.index("function evalF5(")
        nrfi_block = src[start:end]
        assert "poissonPMF(" not in nrfi_block
        assert "pTeamWins(" not in nrfi_block
        assert "nrfiScore" in nrfi_block and "yrfiScore" in nrfi_block

    def test_python_nrfi_is_poisson_based(self):
        with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as f:
            src = f.read()
        start = src.index("# ── NRFI / YRFI")
        end = src.index("p_nrfi = p_nrfi_away * p_nrfi_home") + len("p_nrfi = p_nrfi_away * p_nrfi_home")
        nrfi_block = src[start:end]
        assert "poisson_pmf(0," in nrfi_block
