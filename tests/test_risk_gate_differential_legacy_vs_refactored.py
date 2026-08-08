#!/usr/bin/env python3
"""
tests/test_risk_gate_differential_legacy_vs_refactored.py
=============================================================
PR #8 pre-merge hardening review, Part D: independent differential
testing. Rather than trusting the golden-baseline tests written during
Phase 7 itself, this file imports the ACTUAL, UNMODIFIED
scripts/risk_gate.py source as it existed at the PR's base commit
(a25f9c91794755b3b20a8cc9b4499958b6c91492, the PR #7 merge commit --
verified byte-identical to a fresh `git show a25f9c9:scripts/risk_gate.py`
in tests/_legacy_snapshots/risk_gate_pr7_base.py) side-by-side with the
current, refactored scripts/risk_gate.py, and runs the SAME fixtures
through both, asserting the two are equivalent.

This is deliberately independent of every other Phase 7 test file: it
does not reuse apply_tt_safety()/apply_portfolio_rules() call patterns
written to match the refactor's own expectations, it reuses the REAL
legacy module's functions directly, imported under a separate name so
both can be loaded in the same process without collision.

lib/postponed_guard.py was verified byte-identical between the PR base
and now (diff, not assumed) -- both implementations call the exact same
check_game_status(), so any observed difference is attributable only to
risk_gate.py's own refactor, not a shared-dependency drift.

Note: tests/_legacy_snapshots/risk_gate_pr7_base.py is a frozen
snapshot for comparison purposes only -- never imported by production
code, never executed by anything outside this test file.
"""

import copy
import importlib.util
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "lib")
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LEGACY_PATH = os.path.join(ROOT, "tests", "_legacy_snapshots", "risk_gate_pr7_base.py")

sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW


def _load_legacy():
    """Loads the frozen PR-base risk_gate.py under the module name
    'risk_gate_legacy_snapshot' so it coexists with the current
    'risk_gate' module without sys.modules collision. The legacy
    module's own `from postponed_guard import check_game_status` import
    statement resolves against the SAME lib/postponed_guard.py already
    on sys.path (verified byte-identical to the PR-base copy above)."""
    spec = importlib.util.spec_from_file_location("risk_gate_legacy_snapshot", LEGACY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["risk_gate_legacy_snapshot"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy():
    return _load_legacy()


@pytest.fixture
def current():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


def _run_both(legacy, current, slate_builder, now_ts=NOW):
    """slate_builder() must return a FRESH slate dict each call (no
    shared mutable state between the two runs). Runs apply_tt_safety
    then apply_portfolio_rules through both implementations and returns
    (legacy_result, current_result) as
    (final_slate, tt_downgrades, decision, report) tuples."""
    slate_a = slate_builder()
    slate_b = slate_builder()
    assert slate_a == slate_b, "slate_builder() must be deterministic"

    tt_downgrades_a = legacy.apply_tt_safety(slate_a, now_ts=now_ts)
    decision_a, report_a = legacy.apply_portfolio_rules(slate_a, now_ts=now_ts)

    tt_downgrades_b = current.apply_tt_safety(slate_b, now_ts=now_ts)
    decision_b, report_b = current.apply_portfolio_rules(slate_b, now_ts=now_ts)

    return (slate_a, tt_downgrades_a, decision_a, report_a), (slate_b, tt_downgrades_b, decision_b, report_b)


def _assert_equivalent(result_a, result_b, label):
    slate_a, downgrades_a, decision_a, report_a = result_a
    slate_b, downgrades_b, decision_b, report_b = result_b
    assert slate_a == slate_b, f"[{label}] final slate diverged:\nlegacy={slate_a}\ncurrent={slate_b}"
    assert downgrades_a == downgrades_b, f"[{label}] tt_downgrades diverged: {downgrades_a} vs {downgrades_b}"
    assert decision_a == decision_b, f"[{label}] decision diverged: {decision_a} vs {decision_b}"
    assert report_a == report_b, f"[{label}] report diverged:\nlegacy={report_a}\ncurrent={report_b}"


# ══════════════════════════════════════════════════════════════════════════════
# Hand-picked adversarial fixtures
# ══════════════════════════════════════════════════════════════════════════════

class TestHandPickedDifferential:

    def test_single_passing_tt_entry(self, legacy, current):
        def build():
            return make_slate([make_game('A', 'B', [make_tt_entry(tier='HIGH', edge=4.0, stake=5.0)])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "single_passing_tt")

    def test_single_failing_edge_only(self, legacy, current):
        def build():
            return make_slate([make_game('A', 'B', [make_tt_entry(tier='HIGH', edge=1.0, stake=5.0)])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "single_failing_edge_only")

    def test_both_evidence_and_edge_fail(self, legacy, current):
        def build():
            e = make_tt_entry(tier='HIGH', edge=1.0)
            e['awayProjRuns'] = None
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "both_evidence_and_edge_fail")

    def test_line_present_not_accepted(self, legacy, current):
        """Part F fixture: line present but entry NOT Accepted -- this is
        exactly the requiredRunsToWin near-miss regression's scenario."""
        def build():
            e = make_tt_entry(status='Missing Data', tier=None, edge=None, line=4)
            e['confidenceTier'] = None
            e['confidence'] = None
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "line_present_not_accepted")
        assert 'requiredRunsToWin' not in ra[0]['games'][0]['marketLedger'][0]

    def test_tier_low(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='LOW', edge=1.0, line=4)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "tier_low")

    def test_tier_absent(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier=None, edge=1.0, line=4)
            e['confidenceTier'] = None
            e['confidence'] = None
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "tier_absent")

    def test_edge_absent(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=None, line=4)
            e['edge'] = None
            e['calibratedEdgeVsExecutable'] = None
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "edge_absent")

    def test_status_malformed(self, legacy, current):
        def build():
            e = make_tt_entry(status='SomeWeirdStatus', tier='HIGH', edge=4.0, line=4)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "status_malformed")

    def test_line_null(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=4.0, line=None)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "line_null")

    def test_line_zero(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=4.0, line=0)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "line_zero")
        assert ra[0]['games'][0]['marketLedger'][0]['requiredRunsToWin'] == 1

    def test_line_fractional(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=4.0, line=4.7)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "line_fractional")

    def test_line_negative_fractional(self, legacy, current):
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=4.0, line=-4.5)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "line_negative_fractional")

    def test_requiredRunsToWin_already_exists(self, legacy, current):
        """Entry already carries a stale requiredRunsToWin field (e.g.
        from a previous risk_gate.py run) -- must be overwritten
        identically by both, only when evaluated."""
        def build():
            e = make_tt_entry(status='Accepted', tier='HIGH', edge=4.0, line=4)
            e['requiredRunsToWin'] = 999
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "requiredRunsToWin_already_exists")
        assert ra[0]['games'][0]['marketLedger'][0]['requiredRunsToWin'] == 5

    def test_fails_before_reaching_evidence_edge_evaluation(self, legacy, current):
        """Rejected-status entry -- never reaches evidence/edge at all."""
        def build():
            e = make_tt_entry(status='Rejected', tier='HIGH', edge=1.0, line=4)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "fails_before_evidence_edge")

    def test_tt_max_bets_exceeded_tie_edges(self, legacy, current):
        def build():
            entries = [make_tt_entry(tier='HIGH', edge=4.0, stake=5.0, ticker=f'T{i}') for i in range(6)]
            return make_slate([make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(entries)])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "tt_max_bets_tie_edges")

    def test_tt_max_bets_plus_dominance_plus_daily_cap_all_fire(self, legacy, current):
        def build():
            tt_entries = [make_tt_entry(tier='HIGH', edge=10.0 - i, stake=8.0, ticker=f'TT{i}') for i in range(6)]
            ml1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=0.01, ticker='M1')
            ml2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=0.01, ticker='M2')
            games = [make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)]
            games.append(make_game('X', 'Y', [ml1, ml2]))
            return make_slate(games)
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "all_warnings_fire")

    def test_daily_cap_exact_boundary(self, legacy, current):
        def build():
            entries = [make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker=f'M{i}') for i in range(8)]
            return make_slate([make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(entries)])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "daily_cap_exact_boundary")
        assert ra[3]['total_real_stake'] == 40.0

    def test_ml_f5_underfill_boundary(self, legacy, current):
        def build():
            e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=4.99, ticker='M1')
            e2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=0.0, ticker='M2')
            other = make_entry(market='Spread_Away', tier='HIGH', edge=4.0, stake=10.0, ticker='M3')
            return make_slate([make_game('A', 'B', [e1, e2, other])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "ml_f5_underfill_boundary")

    def test_all_tt_no_ml_f5(self, legacy, current):
        def build():
            return make_slate([make_game('A', 'B', [make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "all_tt_no_ml_f5")
        assert ra[2] == 'PAPER_ONLY'

    def test_excluded_game(self, legacy, current):
        def build():
            e = make_tt_entry(tier='HIGH', edge=1.0)
            return make_slate([make_game('A', 'B', [e], excluded=True)])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "excluded_game")

    @pytest.mark.parametrize("status", ["Postponed", "Cancelled", "Suspended", "In Progress", "Final"])
    def test_non_pregame_statuses(self, legacy, current, status):
        def build():
            e = make_tt_entry(tier='HIGH', edge=4.0)
            return make_slate([make_game('A', 'B', [e], status=status)])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, f"status_{status}")

    def test_negative_stake(self, legacy, current):
        def build():
            e = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=-5.0)
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "negative_stake")

    def test_none_stake(self, legacy, current):
        def build():
            e = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
            e['betSize'] = None
            return make_slate([make_game('A', 'B', [e])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "none_stake")

    def test_duplicate_market_entries_exact_dict_copy(self, legacy, current):
        def build():
            e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='DUP')
            e2 = copy.deepcopy(e1)
            return make_slate([make_game('A', 'B', [e1, e2])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "duplicate_market_entries")

    def test_no_candidates_empty_slate(self, legacy, current):
        def build():
            return make_slate([make_game('A', 'B', [])])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "no_candidates")

    def test_all_rejected(self, legacy, current):
        def build():
            entries = [make_entry(market='ML_Away', status='Rejected', stake=5.0, ticker=f'R{i}') for i in range(3)]
            return make_slate([make_game('A', 'B', entries)])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "all_rejected")

    def test_mixed_decisions_multi_game(self, legacy, current):
        def build():
            good_tt = make_tt_entry(tier='HIGH', edge=4.0, ticker='G1')
            bad_tt = make_tt_entry(tier='HIGH', edge=1.0, ticker='G2', side='Home')
            ml = make_entry(market='ML_Away', tier='MEDIUM', edge=3.0, stake=4.0, ticker='G3')
            rejected = make_entry(market='ML_Home', status='Rejected', ticker='G4')
            excluded_entry = make_tt_entry(tier='HIGH', edge=4.0, ticker='G5')
            return make_slate([
                make_game('A', 'B', [good_tt, bad_tt]),
                make_game('C', 'D', [ml, rejected]),
                make_game('E', 'F', [excluded_entry], excluded=True),
            ])
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, "mixed_decisions_multi_game")


# ══════════════════════════════════════════════════════════════════════════════
# Randomized fuzz differential -- broad coverage, deterministic seed
# ══════════════════════════════════════════════════════════════════════════════

def _random_entry(rng, i):
    market = rng.choice(['TT_Away_Over', 'TT_Home_Over', 'ML_Away', 'ML_Home',
                          'F5_ML_Away', 'F5_ML_Home', 'Spread_Away'])
    status = rng.choice(['Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed'])
    tier = rng.choice(['HIGH', 'MEDIUM', 'PAPER', 'LOW', None])
    edge = rng.choice([None, -5.0, 0.0, 1.0, 2.49, 2.5, 2.51, 4.0, 10.0])
    stake = rng.choice([0.0, 0.01, 1.0, 5.0, 5.01, 20.0, -1.0])
    line = rng.choice([None, 0, 3, 4, 4.5, -4.5, 8])
    kalshi_price = rng.choice([None, -120, 100, 0])
    away_proj = rng.choice([None, 4.5])
    if market.startswith('TT'):
        entry = make_tt_entry(
            side='Away' if 'Away' in market else 'Home',
            status=status, tier=tier, edge=edge, stake=stake, line=line,
            kalshi_price=kalshi_price, away_proj=away_proj, ticker=f'FZ{i}',
        )
    else:
        entry = make_entry(
            market=market, status=status, tier=tier, edge=edge, stake=stake,
            line=line, kalshi_price=kalshi_price, away_proj=away_proj, ticker=f'FZ{i}',
        )
    if tier is None:
        entry['confidenceTier'] = None
        entry['confidence'] = None
    return entry


def _random_slate(seed):
    rng = random.Random(seed)
    n_games = rng.randint(1, 5)
    games = []
    for gi in range(n_games):
        n_entries = rng.randint(0, 4)
        entries = [_random_entry(rng, f'{seed}_{gi}_{ei}') for ei in range(n_entries)]
        status = rng.choice(['Scheduled', 'Postponed', 'In Progress', 'Final', 'Suspended'])
        excluded = rng.random() < 0.15
        games.append(make_game(f'A{gi}', f'B{gi}', entries, excluded=excluded, status=status))
    return make_slate(games)


class TestRandomizedFuzzDifferential:

    @pytest.mark.parametrize("seed", list(range(60)))
    def test_fuzzed_slate_equivalence(self, legacy, current, seed):
        def build():
            return _random_slate(seed)
        ra, rb = _run_both(legacy, current, build)
        _assert_equivalent(ra, rb, f"fuzz_seed_{seed}")


# ══════════════════════════════════════════════════════════════════════════════
# main()-level stdout/stderr/exit-code differential (Part D explicit gap:
# prior Phase 7 tests never diffed ACTUAL captured stdout between the real
# legacy main() and the refactored main() -- only individual function
# return values/report dicts. This closes that gap directly.
# ══════════════════════════════════════════════════════════════════════════════

import json as _json
import pipeline_artifacts as _pa


def _strip_correlation_groups(slate):
    """
    Portfolio Correlation Gate milestone: apply_correlation_gate() adds a
    new, additive `correlationGroups` field to every real-money-tier
    entry it evaluates (empty list when nothing correlates, as in these
    single-entry-per-game fixtures) -- the legacy PR-base snapshot
    predates this entirely and never sets it. Stripped here, the same
    way meta.json's new 'correlation' sub-report is popped below, so the
    REST of the slate (everything the legacy version could have
    produced) is still compared byte-for-byte.
    """
    import copy
    slate = copy.deepcopy(slate)
    for g in slate.get('games', []):
        for entry in g.get('marketLedger', []):
            entry.pop('correlationGroups', None)
    return slate


class TestMainStdoutStderrExitCodeDifferential:

    def _run_main(self, mod, tmp_path, games, is_legacy):
        # Deliberately the SAME filenames for both legacy and current runs
        # (run sequentially, never concurrently, each read back
        # immediately) -- this way the "Slate updated in-place: <path>"
        # stdout line is byte-identical between the two runs too, not
        # just superficially similar modulo an arbitrary filename suffix.
        slate_path = str(tmp_path / "slate.json")
        meta_path = str(tmp_path / "meta.json")
        mod.SLATE_PATH = slate_path
        mod.META_PATH = meta_path
        with open(slate_path, 'w') as f:
            _json.dump(make_slate(games), f)

        import io, contextlib
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exit_code = mod.main()

        with open(slate_path) as f:
            final_slate = _json.load(f)
        with open(meta_path) as f:
            final_meta = _json.load(f)
        return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue(), final_slate, final_meta

    def test_go_decision_stdout_identical_modulo_artifact_line(self, legacy, current, tmp_path):
        original_root = _pa.PIPELINE_ROOT
        _pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')
        try:
            entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
            legacy_result = self._run_main(legacy, tmp_path, [make_game('A', 'B', [entry])], is_legacy=True)
            entry2 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
            current_result = self._run_main(current, tmp_path, [make_game('A', 'B', [entry2])], is_legacy=False)
        finally:
            _pa.PIPELINE_ROOT = original_root

        exit_a, stdout_a, stderr_a, slate_a, meta_a = legacy_result
        exit_b, stdout_b, stderr_b, slate_b, meta_b = current_result

        assert exit_a == exit_b == 0
        assert stderr_a == stderr_b == ""  # risk_gate.py never writes to stderr, either version

        # Strip the Phase 7 execution-artifact lines AND the Portfolio
        # Correlation Gate milestone's new "Pass 1.5" lines from current's
        # stdout before comparing -- all additive, expected new output,
        # not a divergence in EXISTING output.
        current_lines = [l for l in stdout_b.splitlines()
                          if 'execution pipeline artifact' not in l
                          and 'could not write execution pipeline artifact' not in l
                          and 'Correlation/concentration pass' not in l]
        legacy_lines = stdout_a.splitlines()
        assert legacy_lines == current_lines, (
            f"pre-existing stdout lines diverged:\nlegacy={legacy_lines}\ncurrent={current_lines}"
        )

        assert _strip_correlation_groups(slate_a) == _strip_correlation_groups(slate_b)
        # meta.json's risk_gate block: everything except runAt (clock-dependent,
        # both use their own real datetime.now() call here since main() is
        # exercised end-to-end, not with an injected now_ts) must match.
        # 'correlation' is also popped -- an additive new sub-report the
        # legacy PR-base snapshot predates entirely (see 'current_lines'
        # filter above for the matching stdout exclusion).
        rg_a = dict(meta_a['risk_gate']); rg_a.pop('runAt', None)
        rg_b = dict(meta_b['risk_gate']); rg_b.pop('runAt', None); rg_b.pop('correlation', None)
        assert rg_a == rg_b

    def test_paper_only_decision_stdout_identical_modulo_artifact_line(self, legacy, current, tmp_path):
        original_root = _pa.PIPELINE_ROOT
        _pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')
        try:
            entry = make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)
            legacy_result = self._run_main(legacy, tmp_path, [make_game('A', 'B', [entry])], is_legacy=True)
            entry2 = make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)
            current_result = self._run_main(current, tmp_path, [make_game('A', 'B', [entry2])], is_legacy=False)
        finally:
            _pa.PIPELINE_ROOT = original_root

        exit_a, stdout_a, stderr_a, slate_a, meta_a = legacy_result
        exit_b, stdout_b, stderr_b, slate_b, meta_b = current_result

        assert exit_a == exit_b == 0
        assert stderr_a == stderr_b == ""
        current_lines = [l for l in stdout_b.splitlines()
                          if 'execution pipeline artifact' not in l
                          and 'could not write execution pipeline artifact' not in l
                          and 'Correlation/concentration pass' not in l]
        assert stdout_a.splitlines() == current_lines
        assert _strip_correlation_groups(slate_a) == _strip_correlation_groups(slate_b)
        assert meta_a['risk_gate']['decision'] == meta_b['risk_gate']['decision'] == 'PAPER_ONLY'

    def test_missing_slate_json_exit_code_and_stderr_identical(self, legacy, current, tmp_path):
        legacy.SLATE_PATH = str(tmp_path / 'nope_legacy.json')
        legacy.META_PATH = str(tmp_path / 'meta_legacy.json')
        current.SLATE_PATH = str(tmp_path / 'nope_current.json')
        current.META_PATH = str(tmp_path / 'meta_current.json')

        import io, contextlib
        out_a, err_a = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_a), contextlib.redirect_stderr(err_a):
            with pytest.raises(SystemExit) as exc_a:
                legacy.main()
        out_b, err_b = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_b), contextlib.redirect_stderr(err_b):
            with pytest.raises(SystemExit) as exc_b:
                current.main()

        assert exc_a.value.code == exc_b.value.code == 1
        assert err_a.getvalue() == err_b.getvalue() == ""
        # Both print an ERROR line naming their own SLATE_PATH -- the path
        # itself legitimately differs (different tmp filenames), so compare
        # only the message shape, not the exact path string.
        assert "ERROR:" in out_a.getvalue() and "not found" in out_a.getvalue()
        assert "ERROR:" in out_b.getvalue() and "not found" in out_b.getvalue()
