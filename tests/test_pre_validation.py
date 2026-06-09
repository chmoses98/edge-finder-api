#!/usr/bin/env python3
"""
tests/test_pre_validation.py
Tests for validate_slate_pre.py — Phase 3.
"""
import sys, os, unittest
from unittest.mock import patch

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

from validate_slate_pre import validate_pre


def _make_game(away='COL', home='SD', away_pitcher='A. Smith', home_pitcher='B. Jones', pvf_away=47.5):
    g = {
        'away': {'abbr': away, 'pitcher': {'name': away_pitcher} if away_pitcher else None},
        'home': {'abbr': home, 'pitcher': {'name': home_pitcher} if home_pitcher else None},
    }
    if pvf_away is not None:
        g['pinnacleVF'] = {'away': pvf_away, 'home': 100 - pvf_away}
    return g


def _make_slate(date='2026-06-09', games=None):
    return {
        'date': date,
        'games': games if games is not None else [_make_game()]
    }


class TestPreValidation(unittest.TestCase):

    # ── Missing starters still blocks (exit 2) ────────────────────────────────
    def test_missing_away_starter_is_soft_fail(self):
        slate = _make_slate(games=[_make_game(away_pitcher=None)])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [], 'Should not be a hard fail')
        self.assertTrue(len(soft) > 0, 'Missing starter must produce soft error')
        away_issue = any('away starter' in e for e in soft)
        self.assertTrue(away_issue, 'Soft error must mention away starter')

    def test_missing_home_starter_is_soft_fail(self):
        slate = _make_slate(games=[_make_game(home_pitcher=None)])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [])
        self.assertTrue(len(soft) > 0)

    # ── Stale date hard-fails ─────────────────────────────────────────────────
    def test_stale_date_is_hard_fail(self):
        slate = _make_slate(date='2026-06-08')  # yesterday
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertTrue(len(hard) > 0, 'Stale date must be a hard fail')
        stale_msg = any('STALE' in e.upper() for e in hard)
        self.assertTrue(stale_msg)

    # ── Missing slate date hard-fails ─────────────────────────────────────────
    def test_missing_date_is_hard_fail(self):
        slate = {'games': [_make_game()]}  # no 'date' key
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertTrue(len(hard) > 0)

    # ── Pinnacle VF present after merge → passes pre-validation ──────────────
    def test_pinnacle_vf_present_passes_pre_validation(self):
        """pinnacleVF is NOT checked in pre-validation — present or absent, must pass."""
        slate = _make_slate(games=[_make_game(pvf_away=47.5)])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [])
        self.assertEqual(soft, [])

    # ── Pinnacle VF missing → warning only, not blocking ─────────────────────
    def test_pinnacle_vf_missing_does_not_block_pre_validation(self):
        """pinnacleVF is checked POST-merge, not in pre-validation.
        A game with no pinnacleVF but valid starters must pass pre-validation."""
        slate = _make_slate(games=[_make_game(pvf_away=None)])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [], 'Missing pinnacleVF must not cause hard fail')
        self.assertEqual(soft, [], 'Missing pinnacleVF must not cause soft fail in pre-validation')

    # ── Pre-validation no longer falsely blocks because unmerged slate lacks pinnacleVF
    def test_unmerged_slate_without_pvf_passes_pre_validation(self):
        """Simulates the state where slate.json just came from Vercel (no pinnacleVF yet).
        Pre-validation must pass so the pipeline can proceed to merge_odds.py."""
        # Vercel slate has starters but no pinnacleVF
        slate = _make_slate(games=[_make_game(pvf_away=None)])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [])
        self.assertEqual(soft, [], 'Pre-validation must not block on missing pinnacleVF (not yet merged)')

    # ── All starters present + correct date → full pass ──────────────────────
    def test_full_pass_when_starters_and_date_correct(self):
        slate = _make_slate(games=[_make_game()])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertEqual(hard, [])
        self.assertEqual(soft, [])

    # ── Empty games list hard-fails ───────────────────────────────────────────
    def test_empty_games_is_hard_fail(self):
        slate = _make_slate(games=[])
        hard, soft, warnings = validate_pre(slate, '2026-06-09')
        self.assertTrue(len(hard) > 0, 'No games must be a hard fail')


if __name__ == '__main__':
    unittest.main()
