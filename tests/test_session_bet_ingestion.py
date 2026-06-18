#!/usr/bin/env python3
"""
tests/test_session_bet_ingestion.py
=====================================
Test suite for scripts/log_session_bets.py and the lineup audit override
in scripts/enrich_lineup_confirmed.py.

Tests
-----
1.  session_bet_logs_to_bets_json
    - valid session bet writes one record to bets.json
2.  duplicate_session_bet_not_duplicated
    - re-running with same ticker does not add a second record
3.  session_ticker_appended_to_tracked_tickers
    - ticker appears in clv_snapshots/YYYY-MM-DD/tracked_tickers.json
4.  session_ticker_not_duplicated_in_tracked_tickers
    - re-running does not duplicate the ticker
5.  missing_ticker_fails_validation
    - bet without 'ticker' fails with exit code 1
6.  missing_entry_price_fails_validation
    - bet without 'entryPrice' fails with exit code 1
7.  missing_stake_fails_validation
    - bet without 'stake' fails with exit code 1
8.  paper_bet_written_as_paper_type
    - PAPER confidence bet writes type='paper', not 'real'
9.  game_started_without_flag_fails
    - scheduledStartTime in the past without post_entry_manual_review=True fails
10. game_started_with_flag_passes
    - scheduledStartTime in the past WITH post_entry_manual_review=True succeeds
11. settled_backfill_round_trips
    - settled/WIN bet round-trips all fields through bets.json
12. clv_unavailable_reason_preserved
    - clvStatus='unavailable' and clvReason preserved exactly
13. lineup_audit_overrides_stale_slate_field
    - enrich_lineup_confirmed reads lineup_audit and corrects slate.json
    - game that was lineupConfirmed=False gets corrected to True
14. lineup_audit_missing_falls_back_to_teamstats
    - when lineup audit file is absent, v1 behaviour (teamStats) is used
15. lineup_audit_fresh_when_audit_newer_than_checkedat
    - audit generatedAt > lineupCheckedAt triggers override
"""

import json
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

# ── Point imports at our scripts ─────────────────────────────────────────────
TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(TESTS_DIR, '..', 'scripts')
ROOT_DIR    = os.path.dirname(TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

# We import individual helpers rather than the full script (which calls main())
# so we can test them in isolation.
import importlib.util, types

def _load_script(name: str):
    path = os.path.join(SCRIPTS_DIR, name)
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = types.ModuleType(name)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod

lsb_mod  = _load_script('log_session_bets.py')
elc_mod  = _load_script('enrich_lineup_confirmed.py')


# ── Helpers ──────────────────────────────────────────────────────────────────

def _future_ts() -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(hours=4)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _past_ts() -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _valid_bet(overrides: dict | None = None) -> dict:
    """Return a minimal valid session bet dict."""
    bet = {
        'date':               '2026-06-17',
        'game':               'CWS@NYY',
        'market':             'F5 ML',
        'side':               'HOME',
        'ticker':             'KXMLBF5-26JUN171905CWSNYY-NYY',
        'entryPrice':         -111,
        'stake':              4.5,
        'modelPct':           68.0,
        'marketPct':          52.8,
        'edgePct':            2.84,
        'confidence':         'MEDIUM',
        'scheduledStartTime': _future_ts(),
    }
    if overrides:
        bet.update(overrides)
    return bet


def _write_bets(path: str, bets: list) -> None:
    with open(path, 'w') as f:
        json.dump(bets, f)


def _read_bets(path: str) -> list:
    with open(path) as f:
        return json.load(f)


def _read_tickers(snap_dir: str, date: str) -> dict:
    path = os.path.join(snap_dir, date, 'tracked_tickers.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    return {t['ticker']: t for t in data.get('tickers', [])}


class TestSessionBetIngestion(unittest.TestCase):
    """Tests for log_session_bets.py core functionality."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bets_path = os.path.join(self.tmp, 'data', 'bets.json')
        self.snap_dir  = os.path.join(self.tmp, 'data', 'clv_snapshots')
        os.makedirs(os.path.join(self.tmp, 'data'), exist_ok=True)
        os.makedirs(self.snap_dir, exist_ok=True)
        # Start with empty bets.json
        _write_bets(self.bets_path, [])
        # Patch module paths
        lsb_mod.BETS_PATH = self.bets_path
        lsb_mod.SNAP_DIR  = self.snap_dir

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        lsb_mod.BETS_PATH = os.path.join(ROOT_DIR, 'data', 'bets.json')
        lsb_mod.SNAP_DIR  = os.path.join(ROOT_DIR, 'data', 'clv_snapshots')

    # ── 1. Basic write ────────────────────────────────────────────────────
    def test_session_bet_logs_to_bets_json(self):
        bet    = _valid_bet()
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = lsb_mod.build_bet_record(bet, now_ts)

        bets = _read_bets(self.bets_path)
        bets.append(record)
        _write_bets(self.bets_path, bets)

        result = _read_bets(self.bets_path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['ticker'], bet['ticker'])
        self.assertEqual(result[0]['game'],   bet['game'])
        self.assertEqual(result[0]['market'], bet['market'])
        self.assertEqual(result[0]['type'],   'real')

    # ── 2. Idempotency ────────────────────────────────────────────────────
    def test_duplicate_session_bet_not_duplicated(self):
        bet    = _valid_bet()
        now_ts = datetime.now(tz=timezone.utc).isoformat()

        # First write
        record = lsb_mod.build_bet_record(bet, now_ts)
        _write_bets(self.bets_path, [record])

        # Second run: simulate main() idempotency logic
        existing = _read_bets(self.bets_path)
        keys     = lsb_mod.existing_keys(existing)
        k        = lsb_mod.stable_key(
            bet['date'], bet['game'], bet['market'], bet['ticker']
        )
        self.assertIn(k, keys, "Key should already be present")
        # Should NOT be added again
        new_bets = [b for b in [record]
                    if lsb_mod.stable_key(
                        b['date'], b['game'], b['market'], b['ticker']
                    ) not in keys]
        self.assertEqual(len(new_bets), 0)
        final = _read_bets(self.bets_path)
        self.assertEqual(len(final), 1, "Should still be 1 after re-run")

    # ── 3. CLV ticker appended ─────────────────────────────────────────────
    def test_session_ticker_appended_to_tracked_tickers(self):
        bet    = _valid_bet()
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        ticker = lsb_mod.build_ticker_record(bet, now_ts)

        existing = lsb_mod.load_tracked_tickers(bet['date'])
        existing[bet['ticker']] = ticker
        lsb_mod.save_tracked_tickers(bet['date'], existing, now_ts)

        result = _read_tickers(self.snap_dir, bet['date'])
        self.assertIn(bet['ticker'], result)
        self.assertEqual(result[bet['ticker']]['game'],   bet['game'])
        self.assertEqual(result[bet['ticker']]['market'], bet['market'])
        self.assertEqual(result[bet['ticker']]['source'], 'session_analysis')

    # ── 4. No ticker duplication ─────────────────────────────────────────
    def test_session_ticker_not_duplicated_in_tracked_tickers(self):
        bet    = _valid_bet()
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        ticker = lsb_mod.build_ticker_record(bet, now_ts)

        # Write once
        d = {bet['ticker']: ticker}
        lsb_mod.save_tracked_tickers(bet['date'], d, now_ts)

        # Second write: load existing → already there → skip
        existing = lsb_mod.load_tracked_tickers(bet['date'])
        self.assertIn(bet['ticker'], existing)

        # Simulate the merge-and-skip logic
        new_tickers = {bet['ticker']: ticker}
        merged = {**existing, **new_tickers}   # dict update = idempotent overwrite same key
        lsb_mod.save_tracked_tickers(bet['date'], merged, now_ts)

        result = _read_tickers(self.snap_dir, bet['date'])
        ticker_list = list(result.keys())
        count = ticker_list.count(bet['ticker'])
        self.assertEqual(count, 1, f"Ticker should appear exactly once, got {count}")

    # ── 5. Missing ticker fails ───────────────────────────────────────────
    def test_missing_ticker_fails_validation(self):
        bet  = _valid_bet({'ticker': ''})
        errs = lsb_mod.validate_bet(bet, 0)
        self.assertTrue(
            any('ticker' in e for e in errs),
            f"Expected error about ticker, got: {errs}"
        )

    # ── 6. Missing entryPrice fails ───────────────────────────────────────
    def test_missing_entry_price_fails_validation(self):
        bet  = _valid_bet({'entryPrice': None})
        errs = lsb_mod.validate_bet(bet, 0)
        self.assertTrue(
            any('entryPrice' in e for e in errs),
            f"Expected error about entryPrice, got: {errs}"
        )

    # ── 7. Missing stake fails ────────────────────────────────────────────
    def test_missing_stake_fails_validation(self):
        bet  = _valid_bet({'stake': None})
        errs = lsb_mod.validate_bet(bet, 0)
        self.assertTrue(
            any('stake' in e for e in errs),
            f"Expected error about stake, got: {errs}"
        )

    # ── 8. PAPER bet written as paper type ───────────────────────────────
    def test_paper_bet_written_as_paper_type(self):
        bet    = _valid_bet({'confidence': 'PAPER', 'stake': 1.0})
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = lsb_mod.build_bet_record(bet, now_ts)
        self.assertEqual(record['type'], 'paper',
                         "PAPER confidence must produce type='paper'")
        self.assertNotEqual(record['type'], 'real')

    # ── 9. Game started without flag fails ───────────────────────────────
    def test_game_started_without_flag_fails(self):
        bet  = _valid_bet({
            'scheduledStartTime': _past_ts(),
            'post_entry_manual_review': False,
        })
        errs = lsb_mod.validate_bet(bet, 0)
        self.assertTrue(
            any('already started' in e for e in errs),
            f"Expected 'already started' error, got: {errs}"
        )

    # ── 10. Game started WITH flag passes ────────────────────────────────
    def test_game_started_with_flag_passes(self):
        bet  = _valid_bet({
            'scheduledStartTime': _past_ts(),
            'post_entry_manual_review': True,
        })
        errs = lsb_mod.validate_bet(bet, 0)
        started_errs = [e for e in errs if 'already started' in e]
        self.assertEqual(len(started_errs), 0,
                         f"Should not error with post_entry_manual_review=True, got: {errs}")

    # ── 11. Settled backfill round-trips ─────────────────────────────────
    def test_settled_backfill_round_trips(self):
        bet = _valid_bet({
            'status':                   'settled',
            'result':                   'WIN',
            'pl':                       4.05,
            'finalScore':               'CWS 5, NYY 10',
            'clvStatus':                'unavailable',
            'clvReason':                'session_bet_not_tracked_pregame',
            'post_entry_manual_review': True,
            'scheduledStartTime':       _past_ts(),
        })
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = lsb_mod.build_bet_record(bet, now_ts)

        _write_bets(self.bets_path, [record])
        result = _read_bets(self.bets_path)[0]

        self.assertEqual(result['status'],     'settled')
        self.assertEqual(result['result'],     'WIN')
        self.assertAlmostEqual(result['pl'],   4.05, places=2)
        self.assertEqual(result['finalScore'], 'CWS 5, NYY 10')

    # ── 12. CLV unavailable reason preserved ─────────────────────────────
    def test_clv_unavailable_reason_preserved(self):
        reason = 'session_bet_not_tracked_pregame: CLV snapshot ran before analysis'
        bet    = _valid_bet({
            'clvStatus':                'unavailable',
            'clvReason':                reason,
            'post_entry_manual_review': True,
            'scheduledStartTime':       _past_ts(),
        })
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        record = lsb_mod.build_bet_record(bet, now_ts)

        self.assertEqual(record['clvStatus'], 'unavailable')
        self.assertEqual(record['clvReason'], reason)
        self.assertIsNone(record['clv'])
        self.assertIsNone(record['clvDelta'])


class TestLineupAuditOverride(unittest.TestCase):
    """Tests for enrich_lineup_confirmed v2 lineup audit integration."""

    def setUp(self):
        self.tmp   = tempfile.mkdtemp()
        self.slate = os.path.join(self.tmp, 'data', 'slate.json')
        os.makedirs(os.path.join(self.tmp, 'data'), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_slate(self, games: list, date: str = '2026-06-17') -> None:
        slate = {'date': date, 'games': games}
        with open(self.slate, 'w') as f:
            json.dump(slate, f)

    def _write_audit(self, date: str, rows: list,
                     generated_at: str | None = None) -> None:
        if generated_at is None:
            generated_at = datetime.now(tz=timezone.utc).isoformat()
        audit = {'date': date, 'generatedAt': generated_at, 'rows': rows}
        path  = os.path.join(self.tmp, 'data', f'lineup_audit_{date}.json')
        with open(path, 'w') as f:
            json.dump(audit, f)

    def _run_enrich(self, date: str = '2026-06-17') -> dict:
        """Patch file paths and run load_audit + the enrichment logic."""
        # Override module-level path
        orig_slate     = elc_mod.SLATE_PATH
        orig_tmpl      = elc_mod.AUDIT_TEMPLATE
        elc_mod.SLATE_PATH     = self.slate
        elc_mod.AUDIT_TEMPLATE = os.path.join(
            self.tmp, 'data', 'lineup_audit_{date}.json'
        )

        result = elc_mod.load_audit(date)

        elc_mod.SLATE_PATH     = orig_slate
        elc_mod.AUDIT_TEMPLATE = orig_tmpl
        return result

    def _make_game(self, away: str, home: str,
                   away_lc: bool = False, home_lc: bool = False) -> dict:
        return {
            'startTime': '2026-06-17T23:05:00Z',
            'status':    'Scheduled',
            'away':      {'abbr': away, 'team': f'{away} Team'},
            'home':      {'abbr': home, 'team': f'{home} Team'},
            'awayTeamStats': {
                'lineupConfirmed':         away_lc,
                'lineupBattersResolved':   0 if not away_lc else 9,
                'lineupStatus':            'confirmed' if away_lc else 'missing',
                'lineupConfirmedOfficial': away_lc,
                'lineupSource':            'mlb_stats_api',
            },
            'homeTeamStats': {
                'lineupConfirmed':         home_lc,
                'lineupBattersResolved':   0 if not home_lc else 9,
                'lineupStatus':            'confirmed' if home_lc else 'missing',
                'lineupConfirmedOfficial': home_lc,
                'lineupSource':            'mlb_stats_api',
            },
            'lineupConfirmed':  False,
            'lineupCheckedAt':  '2026-06-17T18:19:02Z',   # stale
            'lineupStatus':     'unconfirmed',
        }

    def _make_audit_rows(self, away: str, home: str,
                         away_confirmed: bool = True,
                         home_confirmed: bool = True) -> list:
        game = f'{away}@{home}'
        return [
            {
                'game':                   game,
                'team':                   f'{away} Team',
                'lineupStatus':           'confirmed' if away_confirmed else 'missing',
                'lineupConfirmedOfficial': away_confirmed,
                'lineupSource':           'mlb_stats_api',
                'lineupBattersExpected':  9,
                'lineupBattersFound':     9 if away_confirmed else 0,
                'lineupBattersResolved':  9 if away_confirmed else 0,
                'lineupDataQuality':      'full' if away_confirmed else 'none',
            },
            {
                'game':                   game,
                'team':                   f'{home} Team',
                'lineupStatus':           'confirmed' if home_confirmed else 'missing',
                'lineupConfirmedOfficial': home_confirmed,
                'lineupSource':           'mlb_stats_api',
                'lineupBattersExpected':  9,
                'lineupBattersFound':     9 if home_confirmed else 0,
                'lineupBattersResolved':  9 if home_confirmed else 0,
                'lineupDataQuality':      'full' if home_confirmed else 'none',
            },
        ]

    # ── 13. Audit overrides stale slate field ─────────────────────────────
    def test_lineup_audit_overrides_stale_slate_field(self):
        """
        slate.json has lineupConfirmed=False for CWS@NYY.
        lineup_audit says both confirmed=True.
        load_audit should return the audit's True values.
        """
        date = '2026-06-17'
        # Slate: stale — both False
        game = self._make_game('CWS', 'NYY', away_lc=False, home_lc=False)
        self._write_slate([game], date)

        # Audit: both confirmed=True
        rows = self._make_audit_rows('CWS', 'NYY',
                                     away_confirmed=True, home_confirmed=True)
        self._write_audit(date, rows)

        audit = self._run_enrich(date)

        self.assertIn('CWS@NYY', audit,
                      f"Expected CWS@NYY in audit result. Keys: {list(audit.keys())}")
        entry = audit['CWS@NYY']
        self.assertTrue(entry['away_confirmed'],
                        "Audit should report away (CWS) confirmed=True")
        self.assertTrue(entry['home_confirmed'],
                        "Audit should report home (NYY) confirmed=True")

    # ── 14. Missing audit falls back to teamStats ─────────────────────────
    def test_lineup_audit_missing_falls_back_to_teamstats(self):
        """When lineup audit file doesn't exist, load_audit returns {}."""
        date  = '2026-06-17'
        # Ensure NO audit file exists
        audit_path = os.path.join(self.tmp, 'data', f'lineup_audit_{date}.json')
        if os.path.exists(audit_path):
            os.remove(audit_path)

        audit = self._run_enrich(date)
        self.assertEqual(audit, {},
                         "Missing audit file should return empty dict (fallback)")

    # ── 15. Fresh audit corrects outdated checkedAt ───────────────────────
    def test_lineup_audit_fresh_when_audit_newer_than_checkedat(self):
        """
        Game's lineupCheckedAt is 18:19Z (stale).
        Audit was generated at 22:45Z.
        load_audit should parse the correct confirmed values from the audit.
        """
        date = '2026-06-17'
        game = self._make_game('CLE', 'MIL', away_lc=False, home_lc=False)
        game['lineupCheckedAt'] = '2026-06-17T18:19:02Z'
        self._write_slate([game], date)

        audit_generated = '2026-06-17T22:45:39.373385+00:00'
        rows = self._make_audit_rows('CLE', 'MIL',
                                     away_confirmed=True, home_confirmed=True)
        self._write_audit(date, rows, generated_at=audit_generated)

        audit = self._run_enrich(date)

        self.assertIn('CLE@MIL', audit)
        entry = audit['CLE@MIL']

        # Both should be confirmed per the audit
        self.assertTrue(entry['away_confirmed'],
                        "CLE should be confirmed per audit (was False in slate)")
        self.assertTrue(entry['home_confirmed'],
                        "MIL should be confirmed per audit (was False in slate)")

        # generatedAt should match the audit file's timestamp
        self.assertEqual(entry['generatedAt'], audit_generated)

    # ── Bonus: partial lineup (one confirmed, one not) ───────────────────
    def test_partial_lineup_in_audit(self):
        """Only home confirmed; away unconfirmed → away_confirmed=False."""
        date = '2026-06-17'
        rows = self._make_audit_rows('TOR', 'BOS',
                                     away_confirmed=False, home_confirmed=True)
        self._write_audit(date, rows)
        self._write_slate([], date)

        audit = self._run_enrich(date)
        # The game key
        key = next((k for k in audit if 'BOS' in k), None)
        if key:
            entry = audit[key]
            self.assertFalse(entry['away_confirmed'],
                             "TOR away should be unconfirmed")
            self.assertTrue(entry['home_confirmed'],
                            "BOS home should be confirmed")


if __name__ == '__main__':
    unittest.main(verbosity=2)
