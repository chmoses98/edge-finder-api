#!/usr/bin/env python3
"""
tests/test_validate_slate_final_differential.py
=================================================
Differential harness for the Phase 8 conversion of
scripts/validate_slate_final.py: runs the FROZEN ORIGINAL
implementation (tests/_legacy_snapshots/validate_slate_final_phase8_base.py,
captured from main before this phase's refactor) side-by-side with the
current (refactored) implementation against identical fixtures, and
asserts identical results.

This is independent of tests/test_validate_slate_final_immutable.py's
golden-equivalence suite (which encodes expected behavior as fixed
assertions) -- it instead proves the refactor changed nothing by
executing BOTH implementations against the SAME inputs and diffing
their outputs directly, closing the gap where a golden-suite bug could
mask a real regression (or vice versa).

Loaded under a separate module name via importlib so it does not
collide with the real `scripts.validate_slate_final` import, matching
the technique established in the Phase 7 hardening review.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from test_validate_slate_final_immutable import (  # noqa: E402
    make_good_game, make_slate, make_pitcher_savant, make_team_stats,
    make_full_ledger, NOW,
)


def _load_legacy():
    path = os.path.join(ROOT, 'tests', '_legacy_snapshots', 'validate_slate_final_phase8_base.py')
    spec = importlib.util.spec_from_file_location('validate_slate_final_legacy_phase8', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_current():
    import validate_slate_final as current
    return current


@pytest.fixture
def legacy():
    return _load_legacy()


@pytest.fixture
def current():
    return _load_current()


class TestValidateFinalDifferential:
    """validate_final()/validate_final_pure() must produce identical
    (errors, warnings) to the frozen legacy validate_final() for every
    fixture below."""

    def _diff(self, legacy, current, slate, exp_date):
        legacy_errors, legacy_warnings = legacy.validate_final(slate, exp_date)
        current_errors, current_warnings = current.validate_final(
            json.loads(json.dumps(slate)), exp_date,
        )
        assert current_errors == legacy_errors
        assert current_warnings == legacy_warnings

    def test_fully_valid_game(self, legacy, current):
        g = make_good_game()
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_no_games(self, legacy, current):
        self._diff(legacy, current, make_slate([]), '2026-06-16')

    def test_missing_starter(self, legacy, current):
        g = make_good_game()
        g['away']['pitcher'] = None
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_pinnacle_missing_scheduled(self, legacy, current):
        g = make_good_game(status='Scheduled')
        g['pinnacleVF'] = {}
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_pinnacle_missing_final_game(self, legacy, current):
        g = make_good_game(status='Final')
        g['pinnacleVF'] = {}
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_team_stats_absent(self, legacy, current):
        g = make_good_game()
        g['awayTeamStats'] = None
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_team_stats_wrong_type(self, legacy, current):
        g = make_good_game()
        g['awayTeamStats'] = 'not-a-dict'
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_lineup_not_confirmed(self, legacy, current):
        g = make_good_game()
        g['awayTeamStats'] = make_team_stats(lineup_confirmed=None)
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_kalshi_all_missing(self, legacy, current):
        g = make_good_game()
        g['odds'] = {'kalshi': {}}
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_empty_ledger(self, legacy, current):
        g = make_good_game()
        g['marketLedger'] = []
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_missing_required_market(self, legacy, current):
        g = make_good_game()
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') != 'RL_Home']
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_invalid_row_status(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Bogus'
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_rejected_without_reason(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = ''
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_accepted_multi_error_row(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['edge'] = None
        g['marketLedger'][0]['confidence'] = 'BOGUS'
        g['marketLedger'][0]['kalshiPrice'] = None
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_pitcher_savant_none_known_name(self, legacy, current):
        g = make_good_game()
        g['away']['pitcherSavant'] = None
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_pitcher_savant_wrong_type(self, legacy, current):
        g = make_good_game()
        g['away']['pitcherSavant'] = 'nope'
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_negative_recent_fip(self, legacy, current):
        g = make_good_game()
        g['away']['pitcherSavant'] = make_pitcher_savant(recent_fip=-1.0)
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_xfip_none(self, legacy, current):
        g = make_good_game()
        g['away']['pitcherSavant'] = make_pitcher_savant(xfip=None)
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_multiple_games_mixed(self, legacy, current):
        g1 = make_good_game(away='KC', home='WSH')
        g2 = make_good_game(away='NYY', home='BOS')
        g2['marketLedger'] = []
        g3 = make_good_game(away='LAD', home='SF', status='Final')
        g3['pinnacleVF'] = {}
        self._diff(legacy, current, make_slate([g1, g2, g3]), '2026-06-16')

    def test_multiple_simultaneous_row_failures_across_games(self, legacy, current):
        g1 = make_good_game(away='KC', home='WSH')
        g1['marketLedger'][0]['status'] = 'Rejected'
        g1['marketLedger'][0]['rejectionReason'] = ''
        g1['marketLedger'][1]['status'] = 'Missing Data'
        g1['marketLedger'][1]['missingFields'] = []
        g2 = make_good_game(away='NYY', home='BOS')
        g2['marketLedger'][0]['status'] = 'Evaluation Failed'
        g2['marketLedger'][0]['evaluationError'] = ''
        self._diff(legacy, current, make_slate([g1, g2]), '2026-06-16')

    def test_reordered_ledger_rows(self, legacy, current):
        g = make_good_game()
        g['marketLedger'] = list(reversed(g['marketLedger']))
        self._diff(legacy, current, make_slate([g]), '2026-06-16')

    def test_does_not_mutate_slate(self, legacy, current):
        slate = make_slate([make_good_game()])
        frozen = json.loads(json.dumps(slate))
        current.validate_final(slate, '2026-06-16')
        assert slate == frozen


class TestGenerateExecutionSlipDifferential:
    """generate_execution_slip() must produce identical slip_dict
    content (and text, once printed lines are compared with any
    incidental whitespace normalization) to the frozen legacy
    implementation for every fixture below."""

    def _diff(self, legacy, current, games, exp_date, current_utc=NOW):
        legacy_text, legacy_dict = legacy.generate_execution_slip(
            games, exp_date, current_utc=current_utc,
        )
        current_text, current_dict = current.generate_execution_slip(
            json.loads(json.dumps(games)), exp_date, current_utc=current_utc,
        )
        assert current_dict == legacy_dict
        assert current_text == legacy_text

    def test_accepted_high_tier(self, legacy, current):
        g = make_good_game()
        self._diff(legacy, current, [g], '2026-06-16')

    def test_accepted_paper(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['confidence'] = 'PAPER'
        self._diff(legacy, current, [g], '2026-06-16')

    def test_price_moved_reason_code(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['reasonCodes'] = ['PRICE_MOVED_BEYOND_MAX']
        self._diff(legacy, current, [g], '2026-06-16')

    def test_rejected(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = 'edge too small'
        self._diff(legacy, current, [g], '2026-06-16')

    def test_rejected_suspended_text(self, legacy, current):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = 'market suspended'
        self._diff(legacy, current, [g], '2026-06-16')

    def test_live_game_blocked(self, legacy, current):
        g = make_good_game(status='In Progress')
        self._diff(legacy, current, [g], '2026-06-16')

    def test_final_game_blocked(self, legacy, current):
        g = make_good_game(status='Final')
        self._diff(legacy, current, [g], '2026-06-16')

    def test_postponed_falls_through(self, legacy, current):
        g = make_good_game(status='Postponed')
        self._diff(legacy, current, [g], '2026-06-16')

    def test_suspended_falls_through(self, legacy, current):
        g = make_good_game(status='Suspended')
        self._diff(legacy, current, [g], '2026-06-16')

    def test_empty_games(self, legacy, current):
        self._diff(legacy, current, [], '2026-06-16')

    def test_multiple_games_mixed_routing(self, legacy, current):
        g1 = make_good_game(away='KC', home='WSH')
        g2 = make_good_game(away='NYY', home='BOS', status='Final')
        g3 = make_good_game(away='LAD', home='SF')
        g3['marketLedger'][0]['confidence'] = 'PAPER'
        self._diff(legacy, current, [g1, g2, g3], '2026-06-16')

    def test_does_not_mutate_games(self, legacy, current):
        games = [make_good_game()]
        frozen = json.loads(json.dumps(games))
        current.generate_execution_slip(games, '2026-06-16', current_utc=NOW)
        assert games == frozen
