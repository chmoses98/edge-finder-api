#!/usr/bin/env python3
"""
tests/test_validate_slate_final_object_ownership.py
========================================================
Phase 8 Part 12 object-ownership and immutability proofs for
scripts/validate_slate_final.py's pure functions, beyond the basic
"argument unchanged after call" checks already covered in the purity
and golden-equivalence suites:

  - no returned entry/report aliases a caller-owned nested object
    (mutating the return value must never mutate the input)
  - one game's/row's result cannot affect another game's/row's result
  - validation reports never carry a reference back into recommendation/
    projection data structures that would let a caller accidentally
    corrupt them via the report
"""
import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import make_good_game, make_slate, NOW  # noqa: E402


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


class TestNoAliasingBackIntoCallerData:

    def test_validate_final_pure_report_lists_are_not_aliases_of_slate_fields(self, vsf):
        """
        Mutating the returned errors/warnings lists after the call must
        never mutate anything reachable from the original `slate`
        argument -- proving the report owns freshly-built strings/lists,
        not references into the input.
        """
        slate = make_slate([make_good_game()])
        before = copy.deepcopy(slate)
        report = vsf.validate_final_pure(slate, '2026-06-16')
        report['errors'].append('INJECTED')
        report['warnings'].append('INJECTED')
        report['diagnosticLines'].append('INJECTED')
        assert slate == before

    def test_slip_entry_dicts_are_not_aliases_of_ledger_rows(self, vsf):
        """
        Mutating a returned slip entry dict must never mutate the
        source marketLedger row it was built from.
        """
        g = make_good_game()
        row_before = copy.deepcopy(g['marketLedger'][0])
        lines, slip_dict = vsf.build_execution_slip_pure([g], '2026-06-16', current_utc=NOW)
        entry = slip_dict['realMoney'][0]
        entry['market'] = 'CORRUPTED'
        entry['reasonCodes'].append('INJECTED')
        entry['gatesFired'].append('INJECTED')
        assert g['marketLedger'][0] == row_before

    def test_slip_reason_codes_list_is_a_fresh_copy_not_the_row_list(self, vsf):
        """
        The PREGAME-ONLY HARD GATE branch explicitly does
        `list(row.get('reasonCodes', []) or [])` before appending the
        block reason -- confirms this produces an independent list, not
        a mutation of the row's own reasonCodes list.
        """
        g = make_good_game(status='In Progress')
        row = g['marketLedger'][0]
        row['reasonCodes'] = ['EXISTING']
        original_reason_codes = row['reasonCodes']
        lines, slip_dict = vsf.build_execution_slip_pure([g], '2026-06-16', current_utc=NOW)
        entry = slip_dict['rejectedBlocked'][0]
        assert entry['reasonCodes'] is not original_reason_codes
        assert original_reason_codes == ['EXISTING']

    def test_two_calls_on_the_same_slate_produce_independent_report_objects(self, vsf):
        slate = make_slate([make_good_game()])
        r1 = vsf.validate_final_pure(slate, '2026-06-16')
        r2 = vsf.validate_final_pure(slate, '2026-06-16')
        assert r1 == r2
        assert r1 is not r2
        assert r1['errors'] is not r2['errors']
        assert r1['warnings'] is not r2['warnings']


class TestCrossGameIsolation:

    def test_one_games_errors_do_not_leak_into_another_games_results(self, vsf):
        """
        A broken game (empty marketLedger, forcing an error) must not
        cause a fully-valid neighboring game to pick up any of the
        broken game's errors/warnings, and vice versa.
        """
        broken = make_good_game(away='BRK', home='ENN')
        broken['marketLedger'] = []
        healthy = make_good_game(away='HLT', home='HYY')

        errors, warnings = vsf.validate_final(make_slate([broken, healthy]), '2026-06-16')
        assert all('HLT@HYY' not in e for e in errors)
        assert all('BRK@ENN' in e for e in errors)

    def test_one_games_live_block_does_not_affect_another_games_routing(self, vsf):
        live = make_good_game(away='LIV', home='BLK', status='In Progress')
        pregame = make_good_game(away='PRE', home='GAM')

        _, slip_dict = vsf.generate_execution_slip([live, pregame], '2026-06-16', current_utc=NOW)
        assert all(e['game'] != 'PRE@GAM' for e in slip_dict['rejectedBlocked'])
        assert any(e['game'] == 'PRE@GAM' for e in slip_dict['realMoney'])
        assert all(e['game'] == 'LIV@BLK' for e in slip_dict['rejectedBlocked'])

    def test_mutating_one_games_fixture_after_validation_does_not_retroactively_change_report(self, vsf):
        """
        Once validate_final_pure() has returned, later mutating the
        original `games` list/dicts must never change the ALREADY
        RETURNED report -- the report was built from values read at
        call time, not from live references re-read later.
        """
        g = make_good_game()
        slate = make_slate([g])
        report = vsf.validate_final_pure(slate, '2026-06-16')
        assert report['errors'] == []

        g['marketLedger'] = []  # mutate after the fact
        assert report['errors'] == []  # already-returned report unaffected
