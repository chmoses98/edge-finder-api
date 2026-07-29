#!/usr/bin/env python3
"""
tests/test_validate_slate_final_rule_order_and_lockdown.py
=============================================================
Phase 8 Part 8 (exact rule-order preservation) + Part 9 (Rule 71/81
lockdown) + Part 11 (one-validation-multiple-outputs) regression
guards for scripts/validate_slate_final.py.

Part 8: for games/rows failing multiple checks simultaneously, proves
the refactor preserves the EXACT original error/warning ordering,
count, and text -- not just "same set of problems," which could mask
a reordering or an accidentally-merged/split check.

Part 9: validate_slate_final.py does not implement Rule 71 or Rule 81
-- it validates a PRECONDITION Rule 71 depends on elsewhere
(pinnacleVF.away's presence, which build_market_ledger.py's actual
Pinnacle-gap check needs to be computable at all). These tests pin
that finding down as an executable regression guard: "Rule 71" must
appear in exactly one place (the precondition-check error message),
and "Rule 81" must not appear anywhere in the file.

Part 11: proves generate_execution_slip() computes its result via
exactly ONE call to build_execution_slip_pure() -- the same in-memory
(lines, slip_dict) pair drives both the returned slip_text and the
returned slip_dict, never two separate computations that could
silently diverge.
"""
import copy
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import (  # noqa: E402
    make_good_game, make_slate, make_pitcher_savant, NOW,
)


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


# ══════════════════════════════════════════════════════════════════════════════
# Part 8: exact rule-order preservation under multiple simultaneous failures
# ══════════════════════════════════════════════════════════════════════════════

class TestExactRuleOrderPreservation:

    def test_single_game_every_check_category_fails_at_once(self, vsf):
        """
        Adversarial fixture: one game triggers a warning AND an error
        from nearly every check category in a single pass. Pins the
        EXACT resulting errors/warnings lists (order + text), not just
        their set membership, so any accidental reordering of the
        per-game check sequence in validate_final()/_validate_games_pure()
        fails this test.
        """
        g = make_good_game(status='Scheduled')
        g['away']['pitcher'] = None                       # warning: starter TBD
        g['pinnacleVF'] = {}                                # error: pinnacleVF.away missing (Scheduled)
        g['awayTeamStats'] = None                           # warning: teamStats block missing
        g['homeTeamStats']['lineupConfirmed'] = None        # warning: lineupConfirmed
        g['odds'] = {'kalshi': {}}                          # 7 warnings: kalshi prices
        g['allEdges'] = []                                  # (ledger present -> warning path)
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = ''        # error: Rejected w/o reason
        g['away']['pitcherSavant'] = make_pitcher_savant(recent_fip=-2.0)  # error: negative recentFIP

        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')

        assert errors == [
            'KC@WSH: pinnacleVF.away missing — Rule 71 gap check impossible',
            'KC@WSH/NRFI: Rejected but rejectionReason empty',
            'KC@WSH/away: recentFIP=-2.0 is negative (startsSampled=5) — '
            'sanitization in fetch_savant_pitchers.py should have cleared this',
        ]
        assert warnings == [
            'KC@WSH: away starter TBD/missing (pitcher.name not posted — expected for late-day games)',
            'KC@WSH: awayTeamStats block missing — team may not be in teamstats.json; '
            'model will use league-average baseline',
            'KC@WSH: homeTeamStats.lineupConfirmed=null — lineup not yet posted (expected before ~5pm ET)',
            'KC@WSH: Kalshi ML not in slate (odds.kalshi.ml.away=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi F5 ML not in slate (odds.kalshi.f5ml.away=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi TT Away not in slate (odds.kalshi.team_totals.away.best_ticker=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi TT Home not in slate (odds.kalshi.team_totals.home.best_ticker=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi NRFI/YRFI not in slate (odds.kalshi.nrfi_yrfi.nrfi_american=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi Game Total not in slate (odds.kalshi.total.line=null) — must show Missing Data in marketLedger',
            'KC@WSH: Kalshi RL not in slate (odds.kalshi.rl.best_ticker=null) — must show Missing Data in marketLedger',
            'KC@WSH: awayProjRuns not in allEdges — projection may have used league-average fallback '
            '(marketLedger present, game was evaluated)',
        ]

    def test_multiple_games_error_order_follows_game_list_order(self, vsf):
        """
        Games must be validated in exact list order, and within a game,
        checks in exact category order -- errors from game 1 must all
        precede errors from game 2, never interleaved.
        """
        g1 = make_good_game(away='AAA', home='BBB')
        g1['marketLedger'] = []
        g2 = make_good_game(away='CCC', home='DDD')
        g2['marketLedger'] = []
        g3 = make_good_game(away='EEE', home='FFF')  # fully valid, contributes nothing

        errors, warnings = vsf.validate_final(make_slate([g1, g2, g3]), '2026-06-16')
        assert errors == [
            'AAA@BBB: marketLedger empty/missing — build_market_ledger.py must produce 11 rows',
            'CCC@DDD: marketLedger empty/missing — build_market_ledger.py must produce 11 rows',
        ]

    def test_required_market_absence_lists_in_required_markets_declared_order(self, vsf):
        """
        When multiple required markets are simultaneously absent, the
        resulting errors must appear in REQUIRED_MARKETS' declared
        order (not sorted, not ledger order) -- proving the outer
        `for req in REQUIRED_MARKETS` loop order was preserved exactly.
        """
        g = make_good_game()
        keep = {'ML_Away', 'ML_Home'}
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') in keep]
        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
        market_error_order = [
            e.split('required market "')[1].split('"')[0]
            for e in errors if 'required market' in e
        ]
        expected_missing = [m for m in vsf.REQUIRED_MARKETS if m not in keep]
        assert market_error_order == expected_missing

    def test_accepted_row_accumulates_errors_in_edge_confidence_price_order(self, vsf):
        """
        A single Accepted row failing all three Accepted-only checks
        must produce its three errors in the exact edge -> confidence
        -> kalshiPrice order the code checks them in.
        """
        g = make_good_game()
        row = g['marketLedger'][0]
        row['edge'] = None
        row['confidence'] = 'BOGUS'
        row['kalshiPrice'] = None
        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
        row_errors = [e for e in errors if e.startswith(f"KC@WSH/{row['market']}:")]
        assert row_errors == [
            f"KC@WSH/{row['market']}: Accepted but edge is null",
            f"KC@WSH/{row['market']}: Accepted but confidence=\"BOGUS\"",
            f"KC@WSH/{row['market']}: Accepted but kalshiPrice is null",
        ]

    def test_slip_routing_order_real_money_entries_follow_game_and_ledger_order(self, vsf):
        """
        generate_execution_slip()'s real_money bucket must preserve
        game-list order, then within-game marketLedger row order --
        never resorted by market name or edge size.
        """
        g1 = make_good_game(away='ZZZ', home='YYY')
        g2 = make_good_game(away='AAA', home='BBB')
        _, slip_dict = vsf.generate_execution_slip([g1, g2], '2026-06-16', current_utc=NOW)
        games_seen = [e['game'] for e in slip_dict['realMoney']]
        assert games_seen[0] == 'ZZZ@YYY'
        assert games_seen[-1] == 'AAA@BBB'
        g1_markets = [row['market'] for row in g1['marketLedger']]
        seen_g1_markets = [e['market'] for e in slip_dict['realMoney'] if e['game'] == 'ZZZ@YYY']
        assert seen_g1_markets == g1_markets


# ══════════════════════════════════════════════════════════════════════════════
# Part 9: Rule 71 / Rule 81 lockdown
# ══════════════════════════════════════════════════════════════════════════════

class TestRequiredMarketsDuplicateLogicAudit:
    """
    PR #9 hardening addition (Part 8): REQUIRED_MARKETS (11 canonical
    market names) is independently defined in both
    scripts/validate_slate_final.py and scripts/build_market_ledger.py
    -- confirmed identical today by direct AST extraction from both
    files (not a string-search or prose claim). This is a genuine
    duplicate source of truth, consistent with
    docs/DUPLICATE_LOGIC_INVENTORY.md's existing pattern, deliberately
    NOT consolidated in this PR. Before this test existed, NOTHING
    would fail if the two lists silently diverged (e.g. a future edit
    to build_market_ledger.py adding a 12th market without updating
    this file) -- this test closes that gap.
    """

    def _extract_required_markets(self, path):
        import ast
        tree = ast.parse(open(path).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'REQUIRED_MARKETS' for t in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError(f'REQUIRED_MARKETS assignment not found in {path}')

    def test_required_markets_identical_across_both_files_including_order(self):
        vsf_markets = self._extract_required_markets(
            os.path.join(SCRIPTS_DIR, 'validate_slate_final.py'))
        bml_markets = self._extract_required_markets(
            os.path.join(SCRIPTS_DIR, 'build_market_ledger.py'))
        assert vsf_markets == bml_markets, (
            'REQUIRED_MARKETS has diverged between validate_slate_final.py '
            'and build_market_ledger.py -- this is a real production risk: '
            'the two scripts would then validate/produce different market '
            'sets. Reconcile deliberately, not silently.'
        )
        assert len(vsf_markets) == 11

    def test_required_markets_defined_exactly_once_per_file_no_runtime_mutation(self):
        for path in (
            os.path.join(SCRIPTS_DIR, 'validate_slate_final.py'),
            os.path.join(SCRIPTS_DIR, 'build_market_ledger.py'),
        ):
            src = open(path).read()
            assert src.count('REQUIRED_MARKETS = [') == 1, path
            assert 'REQUIRED_MARKETS.append' not in src
            assert 'REQUIRED_MARKETS.remove' not in src
            assert 'REQUIRED_MARKETS.extend' not in src
            assert 'REQUIRED_MARKETS[' not in src

class TestRule71Rule81Lockdown:

    def test_rule_71_appears_exactly_once_as_a_precondition_check_message(self):
        """
        REAL FINDING (pinned as a regression guard): "Rule 71" appears
        literally once in this file, inside the pinnacleVF.away-missing
        error message. This is a PRECONDITION check for Rule 71 (which
        build_market_ledger.py actually implements), not an
        implementation of Rule 71 itself. If this count ever changes,
        either Rule 71 logic has been copied into this file (out of
        scope for Phase 8 and every prior phase) or the precondition
        message has been altered -- both warrant human review.
        """
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        matches = re.findall(r'Rule 71', src)
        assert len(matches) == 1
        assert 'pinnacleVF.away missing — Rule 71 gap check impossible' in src

    def test_rule_81_does_not_appear_anywhere(self):
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        assert 'Rule 81' not in src
        assert 'Rule81' not in src

    def test_pinnacle_precondition_error_only_fires_pregame_not_started_final_postponed(self, vsf):
        """
        Locks the exact status-based branch: the pinnacleVF.away-missing
        precondition is an ERROR only for statuses outside
        {Final, In Progress, Postponed} (a WARNING there instead) --
        pinning this branch prevents an accidental widening/narrowing
        of Rule 71's precondition check during any future edit.
        """
        for status in ('Final', 'In Progress', 'Postponed'):
            g = make_good_game(status=status)
            g['pinnacleVF'] = {}
            errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
            assert not any('Rule 71' in e for e in errors), status
            assert any('pinnacleVF.away missing' in w for w in warnings), status

        for status in ('Scheduled', 'Delayed', ''):
            g = make_good_game(status=status)
            g['pinnacleVF'] = {}
            errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
            assert any('Rule 71' in e for e in errors), status


# ══════════════════════════════════════════════════════════════════════════════
# Part 11: one validation/slip computation, multiple outputs
# ══════════════════════════════════════════════════════════════════════════════

class TestOneComputationMultipleOutputs:

    def test_generate_execution_slip_calls_pure_builder_exactly_once(self, vsf, monkeypatch):
        call_count = {'n': 0}
        original = vsf.build_execution_slip_pure

        def _spy(*a, **kw):
            call_count['n'] += 1
            return original(*a, **kw)

        monkeypatch.setattr(vsf, 'build_execution_slip_pure', _spy)
        games = [make_good_game()]
        vsf.generate_execution_slip(games, '2026-06-16', current_utc=NOW)
        assert call_count['n'] == 1

    def test_validate_final_calls_each_pure_core_exactly_once(self, vsf, monkeypatch):
        """
        validate_final() deliberately calls _diagnostic_lines_pure()
        and _validate_games_pure() separately rather than bundling them
        via validate_final_pure() (see validate_final()'s own
        docstring: bundling would drop diagnostic lines whenever the
        per-game loop raises, a real regression this repo's tests
        caught). "One validation, multiple outputs" here means each of
        the two pure primitives runs exactly once per validate_final()
        call -- not that there is a single combined call.
        """
        diag_calls = {'n': 0}
        games_calls = {'n': 0}
        original_diag = vsf._diagnostic_lines_pure
        original_games = vsf._validate_games_pure

        def _diag_spy(*a, **kw):
            diag_calls['n'] += 1
            return original_diag(*a, **kw)

        def _games_spy(*a, **kw):
            games_calls['n'] += 1
            return original_games(*a, **kw)

        monkeypatch.setattr(vsf, '_diagnostic_lines_pure', _diag_spy)
        monkeypatch.setattr(vsf, '_validate_games_pure', _games_spy)
        vsf.validate_final(make_slate([make_good_game()]), '2026-06-16')
        assert diag_calls['n'] == 1
        assert games_calls['n'] == 1

    def test_diagnostic_lines_print_even_when_validation_loop_raises(self, vsf, monkeypatch, capsys):
        """
        Regression guard for the exact bug found while building this
        suite: validate_final()'s diagnostic lines must reach stdout
        even when _validate_games_pure() raises downstream (e.g. the
        malformed-marketLedger-row TypeError documented elsewhere in
        this repo as a real pre-existing defect) -- matching the
        original implementation's statement order (print diagnostics,
        THEN run the per-game loop that can raise).
        """
        g = make_good_game()
        g['marketLedger'][0] = {'status': 'Accepted', 'edge': 4.0, 'confidence': 'HIGH', 'kalshiPrice': -110}
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') != 'RL_Home']
        slate = make_slate([g])
        with pytest.raises(TypeError):
            vsf.validate_final(slate, '2026-06-16')
        captured = capsys.readouterr()
        assert 'validate_final: 1 games in slate for 2026-06-16' in captured.out

    def test_returned_text_and_dict_derive_from_the_same_lines_object(self, vsf):
        """
        Object-identity proof (Phase 7 Part O technique reused): the
        slip_text returned by generate_execution_slip() must be
        reconstructible byte-for-byte from the SAME `lines` list
        build_execution_slip_pure() returned -- proving slip_text and
        slip_dict are two views of one computation, not two separate
        calls that could diverge.
        """
        games = [make_good_game()]
        lines, slip_dict_direct = vsf.build_execution_slip_pure(games, '2026-06-16', current_utc=NOW)
        slip_text, slip_dict_via_shell = vsf.generate_execution_slip(games, '2026-06-16', current_utc=NOW)
        assert slip_text == '\n'.join(lines) + '\n'
        assert slip_dict_direct == slip_dict_via_shell

    def test_validate_final_pure_still_available_as_a_single_call_report_api(self, vsf):
        """
        validate_final_pure() remains available as a convenience API
        for a future caller (e.g. a validation.json artifact writer)
        that wants the whole {errors, warnings, diagnosticLines} report
        from one call on the non-raising path -- it is simply not used
        internally by validate_final() itself, for the ordering reason
        documented on validate_final()'s docstring. On non-raising
        inputs it must agree exactly with what validate_final() itself
        computes.
        """
        slate = make_slate([make_good_game()])
        report = vsf.validate_final_pure(slate, '2026-06-16')
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert report['errors'] == errors
        assert report['warnings'] == warnings
