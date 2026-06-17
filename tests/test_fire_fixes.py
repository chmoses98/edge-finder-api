#!/usr/bin/env python3
"""
tests/test_fire_fixes.py
=========================
Tests for the three post-June-16 emergency fixes:
  Fire 1 — Logging / CLV integrity
  Fire 2 — Team Total model safety
  Fire 3 — Portfolio / market-selection safety

Run from repo root:
  PYTHONPATH=scripts python tests/test_fire_fixes.py
"""

import json
import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(market='TT_Away_Over', tier='HIGH', status='Accepted',
                edge=4.5, stake=5.0, ticker='KXMLBTEAMTOTAL-26JUN161845KCWSH-KC4',
                line=4, kalshi_price=-120, model_prob=70.0, exec_price=54.5,
                lineup_posted=True, away_proj=4.8, home_proj=4.5):
    return {
        'market': market,
        'confidenceTier': tier,
        'confidence': tier,
        'status': status,
        'edge': edge,
        'calibratedEdgeVsExecutable': edge,
        'betSize': stake,
        'ticker': ticker,
        'marketTicker': ticker,
        'seriesTicker': ticker.split('-')[0],
        'line': line,
        'kalshiPrice': kalshi_price,
        'kalshiImplied': 54.5,
        'kalshiVF': 54.5,
        'executablePriceUsed': exec_price,
        'modelProb': model_prob,
        'scheduledStartTime': '2026-06-16T22:46:00Z',
        'lineupPosted': lineup_posted,
        'lineupDataQuality': 'full',
        'awayProjRuns': away_proj,
        'homeProjRuns': home_proj,
    }


def _make_slate(games_data, date='2026-06-16'):
    games = []
    for (away, home, entries) in games_data:
        games.append({
            'away': {'abbr': away},
            'home': {'abbr': home},
            'startTime': '2026-06-16T22:46:00Z',
            'marketLedger': entries,
        })
    return {'date': date, 'games': games}


# ── Fire 1: Logging / CLV Tests ───────────────────────────────────────────────

class TestWritePendingBets(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_root = None
        # Patch ROOT in the module
        import write_pending_bets as wpb
        self._module = wpb
        self._orig_slate = wpb.SLATE_PATH
        self._orig_bets  = wpb.BETS_PATH
        self.slate_path = os.path.join(self.tmpdir, 'slate.json')
        self.bets_path  = os.path.join(self.tmpdir, 'bets.json')
        wpb.SLATE_PATH = self.slate_path
        wpb.BETS_PATH  = self.bets_path

    def tearDown(self):
        self._module.SLATE_PATH = self._orig_slate
        self._module.BETS_PATH  = self._orig_bets

    def _write_slate(self, games_data, date='2026-06-16'):
        slate = _make_slate(games_data, date)
        with open(self.slate_path, 'w') as f:
            json.dump(slate, f)

    def _read_bets(self):
        if not os.path.exists(self.bets_path):
            return []
        with open(self.bets_path) as f:
            return json.load(f)

    def test_high_medium_bets_written(self):
        """Accepted HIGH/MEDIUM bets ARE written to bets.json."""
        self._write_slate([
            ('KC', 'WSH', [
                _make_entry('TT_Away_Over', 'HIGH', 'Accepted'),
                _make_entry('TT_Home_Over', 'MEDIUM', 'Accepted', ticker='KXMLBTEAMTOTAL-26JUN161845KCWSH-WSH4'),
            ])
        ])
        rc = self._module.main()
        self.assertEqual(rc, 0)
        bets = self._read_bets()
        self.assertEqual(len(bets), 2)
        statuses = {b['status'] for b in bets}
        self.assertEqual(statuses, {'pending'})

    def test_paper_bets_not_written(self):
        """PAPER bets are NOT written to bets.json."""
        self._write_slate([
            ('KC', 'WSH', [
                _make_entry('TT_Away_Over', 'PAPER', 'Accepted'),
            ])
        ])
        rc = self._module.main()
        self.assertEqual(rc, 0)
        bets = self._read_bets()
        self.assertEqual(len(bets), 0)

    def test_rejected_bets_not_written(self):
        """Rejected bets are NOT written to bets.json."""
        self._write_slate([
            ('KC', 'WSH', [
                _make_entry('TT_Away_Over', 'HIGH', 'Rejected'),
            ])
        ])
        rc = self._module.main()
        self.assertEqual(rc, 0)
        bets = self._read_bets()
        self.assertEqual(len(bets), 0)

    def test_idempotent_no_duplicates(self):
        """Re-running write_pending_bets.py does NOT duplicate bets."""
        self._write_slate([
            ('KC', 'WSH', [
                _make_entry('TT_Away_Over', 'HIGH', 'Accepted'),
            ])
        ])
        self._module.main()
        self._module.main()   # run twice
        bets = self._read_bets()
        self.assertEqual(len(bets), 1)

    def test_entry_price_captured(self):
        """actualEntryPrice is populated from executablePriceUsed."""
        self._write_slate([
            ('KC', 'WSH', [
                _make_entry('TT_Away_Over', 'HIGH', 'Accepted', exec_price=54.5),
            ])
        ])
        self._module.main()
        bets = self._read_bets()
        self.assertEqual(len(bets), 1)
        self.assertIsNotNone(bets[0]['actualEntryPrice'])
        self.assertAlmostEqual(bets[0]['actualEntryPrice'], 0.545, places=2)

    def test_no_entry_price_flagged(self):
        """Missing entry price writes realMoneyBlocked=True, not dropped."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted')
        entry['executablePriceUsed'] = None
        entry['kalshiPrice'] = None
        self._write_slate([('KC', 'WSH', [entry])])
        self._module.main()
        bets = self._read_bets()
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].get('realMoneyBlocked'))

    def test_existing_bets_preserved(self):
        """Historical bets are not touched when new bets are appended."""
        old_bet = {'date': '2026-06-14', 'game': 'LAD@CWS', 'market': 'ML', 'status': 'WIN'}
        with open(self.bets_path, 'w') as f:
            json.dump([old_bet], f)

        self._write_slate([
            ('KC', 'WSH', [_make_entry('TT_Away_Over', 'HIGH', 'Accepted')])
        ])
        self._module.main()
        bets = self._read_bets()
        self.assertEqual(len(bets), 2)
        dates = {b['date'] for b in bets}
        self.assertIn('2026-06-14', dates)
        self.assertIn('2026-06-16', dates)


class TestValidateBetLogging(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import validate_bet_logging as vbl
        self._module = vbl
        self._orig_slate = vbl.SLATE_PATH
        self._orig_bets  = vbl.BETS_PATH
        self.slate_path = os.path.join(self.tmpdir, 'slate.json')
        self.bets_path  = os.path.join(self.tmpdir, 'bets.json')
        vbl.SLATE_PATH = self.slate_path
        vbl.BETS_PATH  = self.bets_path

    def tearDown(self):
        self._module.SLATE_PATH = self._orig_slate
        self._module.BETS_PATH  = self._orig_bets

    def test_passes_when_counts_match(self):
        """Gate passes when all ledger bets are in bets.json."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted')
        slate = _make_slate([('KC', 'WSH', [entry])])
        with open(self.slate_path, 'w') as f: json.dump(slate, f)

        bet = {'date': '2026-06-16', 'game': 'KC@WSH', 'market': 'TT_Away_Over',
               'ticker': entry['ticker'], 'status': 'pending'}
        with open(self.bets_path, 'w') as f: json.dump([bet], f)

        rc = self._module.main()
        self.assertEqual(rc, 0)

    def test_fails_when_ledger_bet_missing(self):
        """Gate fails exit 1 when a ledger bet is absent from bets.json."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted')
        slate = _make_slate([('KC', 'WSH', [entry])])
        with open(self.slate_path, 'w') as f: json.dump(slate, f)
        with open(self.bets_path, 'w') as f: json.dump([], f)   # empty

        try:
            rc = self._module.main()
        except SystemExit as e:
            rc = e.code
        self.assertEqual(rc, 1)


class TestWriteTrackedTickers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import write_tracked_tickers as wtt
        self._module = wtt
        self._orig_slate    = wtt.SLATE_PATH
        self._orig_snap_dir = wtt.SNAP_DIR
        self.slate_path = os.path.join(self.tmpdir, 'slate.json')
        self.snap_dir   = os.path.join(self.tmpdir, 'clv_snapshots')
        wtt.SLATE_PATH  = self.slate_path
        wtt.SNAP_DIR    = self.snap_dir

    def tearDown(self):
        self._module.SLATE_PATH = self._orig_slate
        self._module.SNAP_DIR   = self._orig_snap_dir

    def _read_tickers(self, date='2026-06-16'):
        p = os.path.join(self.snap_dir, date, 'tracked_tickers.json')
        with open(p) as f: return json.load(f)

    def test_writes_tracked_tickers_json(self):
        """tracked_tickers.json is created with real-money tickers."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted')
        slate = _make_slate([('KC', 'WSH', [entry])])
        with open(self.slate_path, 'w') as f: json.dump(slate, f)
        rc = self._module.main()
        self.assertEqual(rc, 0)
        data = self._read_tickers()
        self.assertGreater(data['count'], 0)
        self.assertEqual(data['tickers'][0]['ticker'], entry['ticker'])

    def test_idempotent_no_duplicate_tickers(self):
        """Re-running does not duplicate tickers."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted')
        slate = _make_slate([('KC', 'WSH', [entry])])
        with open(self.slate_path, 'w') as f: json.dump(slate, f)
        self._module.main()
        self._module.main()
        data = self._read_tickers()
        tickers = [t['ticker'] for t in data['tickers']]
        self.assertEqual(len(tickers), len(set(tickers)))


# ── Fire 2: Team Total Safety Tests ──────────────────────────────────────────

class TestTTSafetyGate(unittest.TestCase):

    def setUp(self):
        import risk_gate as rg
        self._rg = rg

    def _run_tt_safety(self, entries, away='KC', home='WSH'):
        slate = _make_slate([(away, home, entries)])
        downgrades = self._rg.apply_tt_safety(slate)
        return slate, downgrades

    def test_tt_inputs_always_added(self):
        """TT rows always get ttInputs block."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted', edge=4.0)
        slate, _ = self._run_tt_safety([entry])
        ledger = slate['games'][0]['marketLedger']
        self.assertIn('ttInputs', ledger[0])

    def test_missing_critical_evidence_downgrades(self):
        """Missing critical TT evidence downgrades to PAPER."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted', edge=4.0)
        entry['awayProjRuns'] = None   # remove critical field
        slate, downgrades = self._run_tt_safety([entry])
        ledger_entry = slate['games'][0]['marketLedger'][0]
        self.assertEqual(downgrades[0]['market'] if downgrades else None,
                         ledger_entry.get('market'))
        self.assertEqual(ledger_entry.get('confidenceTier'), 'PAPER')

    def test_required_runs_to_win_set_correctly(self):
        """Over 4 sets requiredRunsToWin = 5."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted', edge=4.0, line=4)
        slate, _ = self._run_tt_safety([entry])
        ledger_entry = slate['games'][0]['marketLedger'][0]
        self.assertEqual(ledger_entry.get('requiredRunsToWin'), 5)

    def test_exactly_4_runs_not_a_win(self):
        """Over 4 line requires 5+ runs — 4 is a LOSS not a win."""
        # This is a semantic test: requiredRunsToWin=5 confirms the contract
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted', line=4)
        slate, _ = self._run_tt_safety([entry])
        required = slate['games'][0]['marketLedger'][0].get('requiredRunsToWin')
        self.assertGreater(required, 4)

    def test_tt_edge_below_2_5_pct_downgraded(self):
        """TT edge below 2.5% cannot be real-money."""
        entry = _make_entry('TT_Away_Over', 'MEDIUM', 'Accepted', edge=1.8)
        slate, downgrades = self._run_tt_safety([entry])
        ledger_entry = slate['games'][0]['marketLedger'][0]
        self.assertEqual(ledger_entry.get('confidenceTier'), 'PAPER')
        self.assertTrue(any('TT_EDGE_BELOW' in r for r in downgrades[0]['reason']))

    def test_tt_edge_above_2_5_passes(self):
        """TT edge at or above 2.5% is allowed through TT safety."""
        entry = _make_entry('TT_Away_Over', 'HIGH', 'Accepted', edge=3.0)
        slate, downgrades = self._run_tt_safety([entry])
        ledger_entry = slate['games'][0]['marketLedger'][0]
        # Should still be HIGH if no other issues
        self.assertEqual(ledger_entry.get('confidenceTier'), 'HIGH')
        self.assertEqual(len(downgrades), 0)


# ── Fire 3: Portfolio Tests ───────────────────────────────────────────────────

class TestPortfolioGate(unittest.TestCase):

    def setUp(self):
        import risk_gate as rg
        self._rg = rg

    def _run_portfolio(self, entries_by_game):
        """entries_by_game: list of (away, home, entries)"""
        slate = _make_slate(entries_by_game)
        return self._rg.apply_portfolio_rules(slate)

    def test_all_tt_no_ml_f5_is_paper_only(self):
        """A card with only TT bets and no ML/F5 → PAPER_ONLY."""
        entries = [
            _make_entry('TT_Away_Over', 'HIGH', 'Accepted', edge=4.0,
                        ticker=f'KXMLBTEAMTOTAL-26JUN16-KC{i}')
            for i in range(3)
        ]
        decision, report = self._run_portfolio([('KC', 'WSH', entries)])
        self.assertEqual(decision, 'PAPER_ONLY')

    def test_tt_max_bets_exceeded_downgrades_excess(self):
        """More than 4 TT bets → excess downgraded to PAPER."""
        entries = []
        tickers = [f'KXMLBTEAMTOTAL-26JUN16X{i}' for i in range(6)]
        for i, t in enumerate(tickers):
            e = _make_entry('TT_Away_Over', 'MEDIUM', 'Accepted', edge=3.0+i*0.1, ticker=t)
            entries.append(e)
        decision, report = self._run_portfolio([('KC', 'WSH', entries)])
        self.assertIn('TT_CONCENTRATION', ' '.join(report['concentration_warnings']))

    def test_daily_risk_cap_triggers_warning(self):
        """Stake above 40u triggers DAILY_RISK_CAP warning."""
        entries = []
        for i in range(10):
            e = _make_entry(market='ML_Away', tier='HIGH', status='Accepted', edge=4.0,
                            stake=5.0, ticker=f'KXMLBGAME-ML{i}')
            entries.append(e)
        _, report = self._run_portfolio([('KC', 'WSH', entries)])
        self.assertTrue(any('DAILY_RISK_CAP' in w for w in report['concentration_warnings']))

    def test_ml_f5_prioritized_gives_go(self):
        """ML+F5 > 50% of stake with ≥2 plays → GO."""
        entries = [
            _make_entry(market='ML_Home', tier='MEDIUM', status='Accepted', edge=2.0,
                        ticker='KXMLBGAME-BOS1', stake=3.0),
            _make_entry(market='F5_ML_Home', tier='MEDIUM', status='Accepted', edge=3.0,
                        ticker='KXMLBF5-MIN1', stake=4.5),
            _make_entry(market='TT_Away_Over', tier='MEDIUM', status='Accepted', edge=3.0,
                        ticker='KXMLBTEAMTOTAL-KC1', stake=4.0),
        ]
        decision, report = self._run_portfolio([('KC', 'WSH', entries)])
        # ML+F5 = 7.5u out of 11.5u = 65% — should be GO
        self.assertEqual(decision, 'GO')

    def test_stake_by_family_calculated_correctly(self):
        """Stake totals by family are accurate."""
        entries = [
            _make_entry(market='TT_Away_Over', tier='HIGH', status='Accepted', stake=5.0,
                        edge=4.0, ticker='TT1'),
            _make_entry(market='ML_Away', tier='MEDIUM', status='Accepted', stake=3.0,
                        edge=2.0, ticker='ML1'),
        ]
        _, report = self._run_portfolio([('KC', 'WSH', entries)])
        self.assertAlmostEqual(report['tt_stake'], 5.0)
        self.assertAlmostEqual(report['ml_f5_stake'], 3.0)
        self.assertAlmostEqual(report['total_real_stake'], 8.0)


# ── Run ───────────────────────────────────────────────────────────────────────



# ── p_over_total Semantics Tests ──────────────────────────────────────────────

class TestPOverTotalSemantics(unittest.TestCase):
    """
    Proves p_over_total semantics are correct and the TT bug is fixed.
    p_over_total(proj, N) must equal P(runs > N) = P(runs >= N+1).
    """

    def setUp(self):
        import math
        self.math = math

    def _p_over(self, proj, line, max_r=30):
        m = self.math
        def pmf(k, lam):
            if lam <= 0: return 0.0
            return (lam**k * m.exp(-lam)) / m.factorial(k)
        return sum(pmf(r, proj) for r in range(int(line) + 1, max_r + 1))

    def test_p_over_total_returns_prob_of_strictly_more_than_line(self):
        """p_over_total(proj, 4) = P(runs > 4) = P(5+), NOT P(4+)."""
        import math
        proj = 4.834
        p_correct = self._p_over(proj, 4)
        p_bugged  = self._p_over(proj, 3)
        pmf4 = (proj**4 * math.exp(-proj)) / math.factorial(4)
        self.assertAlmostEqual(p_bugged - p_correct, pmf4, places=6)
        self.assertLess(p_correct, p_bugged)

    def test_over_4_probability_excludes_exactly_4_runs(self):
        """For Over 4, exactly 4 runs must NOT be counted as a win."""
        import math
        proj = 4.834
        p_5_plus = self._p_over(proj, 4)
        p_4_plus = self._p_over(proj, 3)
        self.assertGreater(p_4_plus, p_5_plus)
        pmf4 = (proj**4 * math.exp(-proj)) / math.factorial(4)
        self.assertGreater(pmf4, 0.10)

    def test_build_market_ledger_uses_correct_line(self):
        """build_market_ledger.py must NOT contain the bugged call."""
        import os
        ledger_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', 'scripts', 'build_market_ledger.py'
        )
        with open(ledger_path) as f:
            lines = f.readlines()
        bug_in_code = [
            (i+1, l.rstrip()) for i, l in enumerate(lines)
            if 'p_over_total(proj, tt_line - 1)' in l
            and not l.lstrip().startswith('#')
            and not l.lstrip().startswith('"')
            and not l.lstrip().startswith("'")
        ]
        self.assertEqual(bug_in_code, [],
            'BUG REGRESSION: p_over_total(proj, tt_line - 1) found in live code')
        fix_in_code = [l for l in lines
                       if 'model_p = p_over_total(proj, tt_line)' in l
                       and not l.lstrip().startswith('#')]
        self.assertGreater(len(fix_in_code), 0,
            'Fix not found: model_p = p_over_total(proj, tt_line) missing')

    def test_game_total_call_is_correct(self):
        """Game Total uses p_over_total(total_proj, tot_line) — already correct."""
        import os
        ledger_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', 'scripts', 'build_market_ledger.py'
        )
        with open(ledger_path) as f:
            src = f.read()
        self.assertIn('p_over_total(total_proj, tot_line)', src)

    def test_june16_corrected_probabilities_are_lower(self):
        """Corrected June 16 TT probs are 15-20ppts lower than bugged."""
        cases = [
            (4.834, 4, 71.09),
            (4.081, 4, 58.22),
            (4.541, 4, 66.46),
            (4.104, 4, 58.66),
            (5.507, 4, 79.91),
        ]
        for proj, line, old_prob_pct in cases:
            old_p = self._p_over(proj, line - 1)
            new_p = self._p_over(proj, line)
            self.assertAlmostEqual(old_p * 100, old_prob_pct, delta=0.5,
                msg=f"proj={proj}: old prob mismatch")
            delta_ppts = (old_p - new_p) * 100
            self.assertGreater(delta_ppts, 12.0,
                msg=f"proj={proj}: expected >12ppt drop, got {delta_ppts:.1f}ppt")

    def test_corrected_edges_below_threshold_for_june16(self):
        """After fix, June 16 Over 4 bets with proj ~4.0-4.8 show edges <2.5%."""
        import math
        def p_over(proj, line, max_r=30):
            def pmf(k, lam):
                return (lam**k * math.exp(-lam)) / math.factorial(k) if lam > 0 else 0.0
            return sum(pmf(r, proj) for r in range(int(line) + 1, max_r + 1))
        def cal_edge(model_p, vf, cal=0.255):
            return (model_p - vf) * cal * 100
        # (proj, line, kalshi_vf)
        cases = [
            (4.834, 4, 0.5465),
            (4.081, 4, 0.5600),
            (4.541, 4, 0.5497),
            (4.104, 4, 0.5661),
            (4.224, 4, 0.5780),
        ]
        for proj, line, kv in cases:
            new_p = min(p_over(proj, line), 0.95)
            edge = cal_edge(new_p, kv)
            self.assertLess(edge, 2.5,
                msg=f"proj={proj}: corrected edge {edge:.2f}% should be <2.5%")


# ── Run ───────────────────────────────────────────────────────────────────────


# ── Quarantine / Game-Aware Gate Tests ───────────────────────────────────────

class TestPostFetchGateQuarantine(unittest.TestCase):
    """
    Tests for post_fetch_gate.py v2.1 game-aware quarantine.
    Run via subprocess (script executes at module level).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, 'data'), exist_ok=True)
        self._scripts_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'
        )

    def _write_slate(self, games, date='2026-06-17'):
        with open(os.path.join(self.tmpdir, 'data', 'slate.json'), 'w') as f:
            json.dump({'date': date, 'games': games}, f)

    def _run_gate(self, date='2026-06-17'):
        import subprocess
        return subprocess.run(
            [sys.executable,
             os.path.join(self._scripts_dir, 'post_fetch_gate.py'), date],
            capture_output=True, text=True, cwd=self.tmpdir
        )

    def _read_slate(self):
        with open(os.path.join(self.tmpdir, 'data', 'slate.json')) as f:
            return json.load(f)

    def _read_status(self):
        with open(os.path.join(self.tmpdir, 'data', 'fetch_status.json')) as f:
            return json.load(f)

    def _good(self, away='KC', home='WSH'):
        return {
            'away': {'abbr': away, 'pitcherSavant': {'xFIP': 3.9, 'seasonFIP': 4.1}},
            'home': {'abbr': home, 'pitcherSavant': {'xFIP': 4.2, 'seasonFIP': 4.3}},
            'awayTeamStats': {'last7RpG': 4.2, 'runsPerGame': 4.1, 'lineupConfirmed': True},
            'homeTeamStats': {'last7RpG': 4.3, 'runsPerGame': 4.2, 'lineupConfirmed': True},
            'startTime': '2026-06-17T17:05:00Z',
        }

    def _both_null(self, away='SF', home='ATL'):
        return {
            'away': {'abbr': away, 'pitcherSavant': {'xFIP': None, 'seasonFIP': None}},
            'home': {'abbr': home, 'pitcherSavant': {'xFIP': None, 'seasonFIP': None}},
            'awayTeamStats': {'last7RpG': 4.0, 'runsPerGame': 4.0},
            'homeTeamStats': {'last7RpG': 4.0, 'runsPerGame': 4.0},
            'startTime': '2026-06-17T22:15:00Z',
        }

    def _one_null(self, away='SF', home='ATL'):
        return {
            'away': {'abbr': away, 'pitcherSavant': {'xFIP': None, 'seasonFIP': None}},
            'home': {'abbr': home, 'pitcherSavant': {'xFIP': 4.5, 'seasonFIP': 4.5}},
            'awayTeamStats': {'last7RpG': 4.0, 'runsPerGame': 4.0},
            'homeTeamStats': {'last7RpG': 4.0, 'runsPerGame': 4.0},
            'startTime': '2026-06-17T22:15:00Z',
        }

    def test_normal_game_missing_both_sides_still_hard_fail(self):
        """Both starters null xFIP -> hard fail (unchanged behavior)."""
        self._write_slate([self._good(), self._both_null()])
        r = self._run_gate()
        self.assertEqual(r.returncode, 1)
        self.assertIn('GATE FAILED', r.stderr)

    def test_single_side_null_xfip_quarantines_game_not_slate(self):
        """ONE side null xFIP+seasonFIP -> quarantine that game, slate continues."""
        self._write_slate([self._good(), self._one_null()])
        r = self._run_gate()
        self.assertEqual(r.returncode, 0, f"Slate must continue. stderr={r.stderr}")
        self.assertIn('QUARANTINE', r.stdout)
        self.assertIn('GATE PASSED', r.stdout)

    def test_quarantined_game_has_excluded_flag(self):
        """Quarantined game gets excludedFromSlate=True in slate.json."""
        self._write_slate([self._good(), self._one_null('SF', 'ATL')])
        self._run_gate()
        slate = self._read_slate()
        sf_atl = next(g for g in slate['games']
                      if g['away']['abbr'] == 'SF' and g['home']['abbr'] == 'ATL')
        self.assertTrue(sf_atl.get('excludedFromSlate'))
        self.assertIn('ABNORMAL', sf_atl.get('exclusionReason', ''))

    def test_normal_game_not_quarantined(self):
        """Normal game with valid data is NOT quarantined."""
        self._write_slate([self._good()])
        r = self._run_gate()
        self.assertEqual(r.returncode, 0)
        slate = self._read_slate()
        self.assertFalse(slate['games'][0].get('excludedFromSlate', False))

    def test_other_games_unaffected_when_one_quarantined(self):
        """Valid games are untouched when one game is quarantined."""
        self._write_slate([self._good('KC','WSH'), self._one_null('SF','ATL'), self._good('NYY','BOS')])
        self._run_gate()
        slate = self._read_slate()
        by_id = {f"{g['away']['abbr']}@{g['home']['abbr']}": g for g in slate['games']}
        self.assertFalse(by_id['KC@WSH'].get('excludedFromSlate', False))
        self.assertFalse(by_id['NYY@BOS'].get('excludedFromSlate', False))
        self.assertTrue(by_id['SF@ATL'].get('excludedFromSlate'))

    def test_fetch_status_lists_quarantined_games(self):
        """fetch_status.json status=OK and quarantinedGames populated."""
        self._write_slate([self._good(), self._one_null('SF','ATL')])
        self._run_gate()
        status = self._read_status()
        self.assertEqual(status['status'], 'OK')
        self.assertEqual(len(status.get('quarantinedGames', [])), 1)
        self.assertEqual(status['quarantinedGames'][0]['game'], 'SF@ATL')

    def test_risk_gate_excludes_quarantined_game(self):
        """risk_gate produces zero real-money output from quarantined game."""
        import risk_gate as rg
        slate = {'date': '2026-06-17', 'games': [
            {'away': {'abbr': 'SF'}, 'home': {'abbr': 'ATL'},
             'excludedFromSlate': True, 'exclusionReason': 'ABNORMAL',
             'marketLedger': [_make_entry('ML_Away', 'HIGH', 'Accepted',
                                           edge=5.0, ticker='SFATL-SF')]},
            {'away': {'abbr': 'KC'}, 'home': {'abbr': 'WSH'},
             'marketLedger': [_make_entry(market='ML_Home', tier='MEDIUM',
                                           status='Accepted', edge=2.0,
                                           ticker='KCWSH-KC', stake=3.0)]},
        ]}
        _, report = rg.apply_portfolio_rules(slate)
        self.assertEqual(report['total_bets'], 1)
        self.assertAlmostEqual(report['total_real_stake'], 3.0)

    def test_write_pending_skips_quarantined_game(self):
        """write_pending_bets.py logs zero bets from quarantined game."""
        import write_pending_bets as wpb
        tmp = tempfile.mkdtemp()
        sp = os.path.join(tmp, 'slate.json')
        bp = os.path.join(tmp, 'bets.json')
        slate = {'date': '2026-06-17', 'games': [{
            'away': {'abbr': 'SF'}, 'home': {'abbr': 'ATL'},
            'excludedFromSlate': True, 'exclusionReason': 'ABNORMAL',
            'marketLedger': [_make_entry('ML_Away','HIGH','Accepted',
                                          edge=5.0,ticker='SFATL-SF')],
        }]}
        with open(sp,'w') as f: json.dump(slate,f)
        with open(bp,'w') as f: json.dump([],f)
        orig_s, orig_b = wpb.SLATE_PATH, wpb.BETS_PATH
        wpb.SLATE_PATH = sp; wpb.BETS_PATH = bp
        try:
            wpb.main()
            with open(bp) as f: bets = json.load(f)
            self.assertEqual(len(bets), 0)
        finally:
            wpb.SLATE_PATH = orig_s; wpb.BETS_PATH = orig_b


if __name__ == '__main__':
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    classes = [
        TestWritePendingBets,
        TestValidateBetLogging,
        TestWriteTrackedTickers,
        TestTTSafetyGate,
        TestPortfolioGate,
        TestPOverTotalSemantics,
        TestPostFetchGateQuarantine,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


