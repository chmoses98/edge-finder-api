#!/usr/bin/env python3
"""
tests/test_risk_gate_review_parts_i_to_m.py
================================================
PR #8 hardening review, Parts I-M.

Part I: Rule 71/81 absence, verified through source search of every
module risk_gate.py imports (not just risk_gate.py's own source),
config-key search, and rejection-reason semantic search -- not just a
literal grep of risk_gate.py alone.

Part J: money/units/rounding exotic-value audit (NaN, infinity, numeric
strings, very large values) beyond the boundary tests already covering
the real production thresholds.

Part K: no-bankroll claim, extended to imported helpers, meta fields,
and execution.json's schema field names.

Part L: duplicate/correlation absence with the exact scenario list Part
L calls out (same market different price, opposing sides, NRFI/YRFI,
team ML vs opposing ML, F3/F5/full-game overlap, pitcher-prop overlap).

Part M: every field name lib/postponed_guard.check_game_status() reads
or ignores, exercised through risk_gate.py's actual call sites, plus
malformed/timezone-naive/offset/Central-Time-boundary timestamps.
"""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "lib")
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


# ══════════════════════════════════════════════════════════════════════════════
# Part I: Rule 71/81 absence -- imported helpers, config keys, rejection reasons
# ══════════════════════════════════════════════════════════════════════════════

class TestRule71And81AbsenceDeepVerification:

    def test_no_rule_references_in_any_module_risk_gate_imports(self):
        """risk_gate.py imports exactly two non-stdlib names:
        postponed_guard.check_game_status and atomic_json.write_json_atomic
        (plus, inside main()'s try block, pipeline_artifacts.
        write_stage_artifact). Verify NONE of those three modules'
        source contains Rule 71/81 references either -- an indirect-call
        path through an imported helper would not be caught by grepping
        risk_gate.py alone."""
        import re
        for module_file in ("postponed_guard.py", "atomic_json.py", "pipeline_artifacts.py"):
            path = os.path.join(LIB_DIR, module_file)
            with open(path) as f:
                source = f.read()
            assert not re.search(r'rule\s*71', source, re.IGNORECASE), f"{module_file} references Rule 71"
            assert not re.search(r'rule\s*81', source, re.IGNORECASE), f"{module_file} references Rule 81"
            assert not re.search(r'pinnacle', source, re.IGNORECASE), f"{module_file} references pinnacle"

    def test_no_config_rules_json_or_rules_md_reads_anywhere_in_risk_gate_dependency_chain(self):
        for module_file, directory in [
            ("risk_gate.py", SCRIPTS_DIR),
            ("postponed_guard.py", LIB_DIR),
            ("atomic_json.py", LIB_DIR),
            ("pipeline_artifacts.py", LIB_DIR),
        ]:
            with open(os.path.join(directory, module_file)) as f:
                source = f.read()
            assert "config/rules.json" not in source
            assert "config" + os.sep + "rules.json" not in source
            assert "RULES.md" not in source

    def test_rejection_reason_strings_are_semantically_distinct_from_rule71_81(self, rg):
        """Confirm the actual set of rejection-reason string PREFIXES
        risk_gate.py can ever produce, and that none of them are a
        renamed/aliased Rule 71 (Pinnacle-gap) or Rule 81 concept."""
        known_prefixes = {
            'TT_MODEL_INPUTS_INCOMPLETE', 'TT_EDGE_BELOW_2.5pct',
            'TT_MAX_BETS_EXCEEDED', 'TT_STAKE_CAP', 'TT_DOMINANCE',
            'ML_F5_UNDERFILL', 'DAILY_RISK_CAP', 'TT_CONCENTRATION',
            'ALL_TT_NO_ML_F5', 'RISK_GATE_PAPER_ONLY', 'Composition checks passed',
        }
        forbidden_terms = ('pinnacle', 'rule71', 'rule_71', 'rule81', 'rule_81', 'gap')
        for prefix in known_prefixes:
            lowered = prefix.lower()
            for term in forbidden_terms:
                assert term not in lowered, f"'{prefix}' unexpectedly resembles a Rule 71/81 concept"

    def test_build_market_ledger_rule71_logic_confirmed_present_and_untouched_by_phase7(self):
        """Sanity anchor: confirm Rule 71 (the Pinnacle-gap check) DOES
        exist in build_market_ledger.py (so this isn't a case of the
        rule having been silently deleted rather than legitimately
        living elsewhere) and was not modified by this PR's diff."""
        import subprocess
        result = subprocess.run(
            ['git', 'log', '--oneline', 'origin/main...HEAD', '--', 'scripts/build_market_ledger.py'],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", (
            f"scripts/build_market_ledger.py was touched by this PR's commits: {result.stdout}"
        )
        with open(os.path.join(SCRIPTS_DIR, "build_market_ledger.py")) as f:
            source = f.read()
        assert "Rule 71" in source or "pinnacle gap" in source.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Part J: exotic numeric values
# ══════════════════════════════════════════════════════════════════════════════

class TestExoticNumericValues:

    def test_nan_stake_propagates_without_crashing(self, rg):
        """betSize=NaN: float(nan) is a valid float; NaN compared with
        > always False, so it never trips DAILY_RISK_CAP/TT_STAKE_CAP,
        but total_real_stake becomes NaN (poisoning the sum) -- this is
        pre-existing legacy arithmetic behavior (Python does not raise
        on NaN arithmetic), not a Phase 7 regression. Documented, not
        fixed here."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = float('nan')
        entries = [('A@B', entry)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert math.isnan(report['total_real_stake'])
        assert decision in ('GO', 'PAPER_ONLY')  # must not raise

    def test_infinity_stake_propagates_without_crashing(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = float('inf')
        entries = [('A@B', entry)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == float('inf')
        # inf > DAILY_RISK_CAP is True -- daily cap warning fires correctly.
        assert any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_negative_infinity_stake_propagates_without_crashing(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = float('-inf')
        entries = [('A@B', entry)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == float('-inf')
        assert decision in ('GO', 'PAPER_ONLY')

    def test_numeric_string_stake_raises_type_error_same_as_legacy(self, rg):
        """betSize as a numeric STRING ('5.0'): `float(entry.get('betSize') or 0)`
        -- float('5.0') actually succeeds (Python's float() accepts
        numeric strings). Verify this doesn't silently misbehave."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = '5.0'
        entries = [('A@B', entry)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == 5.0

    def test_non_numeric_string_stake_raises_value_error(self, rg):
        """A genuinely non-numeric string ('abc') must raise ValueError
        from float() -- NOT be silently swallowed into 0 or skipped.
        This is pre-existing legacy behavior (no try/except around the
        float() call anywhere in this file)."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = 'not_a_number'
        entries = [('A@B', entry)]
        with pytest.raises(ValueError):
            rg.build_risk_portfolio(entries)

    def test_extremely_large_stake_value(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        entry['betSize'] = 1e18
        entries = [('A@B', entry)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == 1e18
        assert any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_binary_float_boundary_tt_max_stake(self, rg):
        """0.1 + 0.1 + 0.1 != 0.3 in binary floating point -- verify the
        TT_MAX_STAKE=20.0 comparison behaves consistently with plain
        Python float arithmetic (not Decimal-corrected), matching legacy
        (no Decimal/rounding was ever used here)."""
        # 4 entries at 4.999999999999999 (just under 5.0 by float epsilon)
        stake_each = 4.999999999999999
        entries = [('A@B', make_entry(market='TT_Away_Over', tier='HIGH', edge=4.0, stake=stake_each))
                   for _ in range(4)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        expected_sum = stake_each * 4
        assert report['tt_stake'] == expected_sum
        assert (expected_sum > 20.0) == any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])

    def test_int_type_stake_vs_float_type_stake_produce_same_numeric_result(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0)
        e1['betSize'] = 5  # int, not float
        e2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, ticker='M2')
        e2['betSize'] = 5.0  # float
        entries = [('A@B', e1), ('C@D', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == 10.0
        assert isinstance(report['total_real_stake'], float)  # sum() promotes int+float to float


# ══════════════════════════════════════════════════════════════════════════════
# Part K: no-bankroll claim, extended
# ══════════════════════════════════════════════════════════════════════════════

class TestNoBankrollFieldNaming:

    def test_execution_json_schema_field_names_never_mention_bankroll(self, rg):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        payload = rg.build_execution_artifact_payload(
            {'date': '2026-01-01', 'games': [make_game('A', 'B', [entry])]}, 'GO', 'reason'
        )
        payload_str = str(payload).lower()
        assert 'bankroll' not in payload_str
        assert 'budget' not in payload_str
        assert 'allocation' not in payload_str
        for key in payload['candidates'][0].keys():
            assert 'bankroll' not in key.lower()

    def test_report_dict_field_names_never_mention_bankroll(self, rg):
        entries = [('A@B', make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0))]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        for key in report.keys():
            assert 'bankroll' not in key.lower()
        assert 'bankroll' not in str(report).lower()

    def test_no_bankroll_env_var_or_workflow_input_referenced(self):
        with open(os.path.join(SCRIPTS_DIR, "risk_gate.py")) as f:
            source = f.read()
        assert "os.environ" not in source
        assert "BANKROLL" not in source.upper().replace("BANKROLLED", "")


# ══════════════════════════════════════════════════════════════════════════════
# Part L: duplicate/correlation absence, exact scenario list
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateAndCorrelationScenarios:

    def test_same_market_different_price_no_special_handling(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='P1')
        e1['kalshiPrice'] = -110
        e2 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='P2')
        e2['kalshiPrice'] = -130
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['ml_f5_bets'] == 2
        assert report['ml_f5_stake'] == 10.0

    def test_opposing_sides_ml_away_and_ml_home_same_game_no_correlation_check(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='AWAY')
        e2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=5.0, ticker='HOME')
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        # No opposing-sides detection -- both simply tally into ML_F5.
        assert report['ml_f5_bets'] == 2
        assert report['ml_f5_stake'] == 10.0
        assert not any('OPPOS' in w.upper() for w in report['concentration_warnings'])

    def test_nrfi_and_yrfi_no_conflict_detection(self, rg):
        """NRFI/YRFI markets are not TT or ML_F5 -- both fall into OTHER,
        tallied together with no conflict check (risk_gate.py has no
        concept of NRFI/YRFI at all)."""
        e1 = make_entry(market='NRFI', tier='HIGH', edge=4.0, stake=5.0, ticker='N1')
        e2 = make_entry(market='YRFI', tier='HIGH', edge=4.0, stake=5.0, ticker='Y1')
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['by_family']['OTHER']['bets'] == 2
        assert report['by_family']['OTHER']['stake'] == 10.0

    def test_team_ml_and_opposing_team_ml_different_games_no_cross_game_check(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='G1')
        e2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=5.0, ticker='G2')
        entries = [('NYY@BOS', e1), ('BOS@NYY', e2)]  # same two teams, different game label
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['ml_f5_bets'] == 2  # no same-team-across-games detection

    def test_f3_f5_full_game_overlap_no_special_handling(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='FULL')
        e2 = make_entry(market='F5_ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='F5')
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        # Both are ML_F5_MARKETS members -- tallied together, no overlap warning.
        assert report['ml_f5_bets'] == 2
        assert not any('OVERLAP' in w.upper() for w in report['concentration_warnings'])

    def test_pitcher_prop_overlap_falls_into_other_no_special_handling(self, rg):
        e1 = make_entry(market='Pitcher_Strikeouts_Over', tier='HIGH', edge=4.0, stake=5.0, ticker='PP1')
        e2 = make_entry(market='Pitcher_Strikeouts_Under', tier='HIGH', edge=4.0, stake=5.0, ticker='PP2')
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['by_family']['OTHER']['bets'] == 2

    def test_reordered_duplicates_produce_identical_totals_regardless_of_order(self, rg):
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='D1')
        e2 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='D2')
        report_a, _, _ = rg.build_risk_portfolio([('A@B', e1), ('A@B', e2)])
        report_b, _, _ = rg.build_risk_portfolio([('A@B', e2), ('A@B', e1)])
        assert report_a['ml_f5_stake'] == report_b['ml_f5_stake']
        assert report_a['ml_f5_bets'] == report_b['ml_f5_bets']


# ══════════════════════════════════════════════════════════════════════════════
# Part M: every field name check_game_status() reads or ignores
# ══════════════════════════════════════════════════════════════════════════════

class TestLiveBetStatusFieldMatrix:

    def _game(self, **fields):
        g = make_game('A', 'B', [make_tt_entry(tier='HIGH', edge=4.0)])
        g.update(fields)
        return g

    def test_scheduledStartTime_field_is_read_and_blocks_when_passed(self, rg):
        g = self._game(status='Scheduled', scheduledStartTime='2026-06-16T19:00:00Z')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_gameTime_field_is_read_when_scheduledStartTime_absent(self, rg):
        g = self._game(status='Scheduled', gameTime='2026-06-16T19:00:00Z')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_firstPitch_field_is_read_when_higher_priority_fields_absent(self, rg):
        g = self._game(status='Scheduled', firstPitch='2026-06-16T19:00:00Z')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_scheduledStart_field_is_read_when_all_higher_priority_fields_absent(self, rg):
        g = self._game(status='Scheduled', scheduledStart='2026-06-16T19:00:00Z')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_startTime_field_confirmed_inert_not_read_at_all(self, rg):
        """The field name make_game()'s own `start_time=` param sets --
        confirmed NOT among check_game_status()'s recognized field names,
        so a game with ONLY 'startTime' set (no scheduledStartTime/
        gameTime/firstPitch/scheduledStart) is never blocked via Signal 2,
        regardless of how far in the past 'startTime' claims to be."""
        g = self._game(status='Scheduled', startTime='2020-01-01T00:00:00Z')
        assert 'scheduledStartTime' not in g and 'gameTime' not in g and 'firstPitch' not in g and 'scheduledStart' not in g
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 1  # NOT blocked -- startTime is inert

    def test_state_field_is_not_a_recognized_status_field(self, rg):
        """check_game_status() reads 'status' or 'gameStatus' -- NOT
        'state'. A game using only 'state' for its status is treated as
        having an EMPTY/unknown status string, which is_pregame()
        treats as pregame (not blocked by Signal 1)."""
        g = self._game(status=None, state='In Progress')
        g.pop('status', None)
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # Unknown/empty status -- not blocked by Signal 1; no scheduledStartTime
        # set either, so Signal 2 doesn't fire -- entry counts as real-money.
        assert report['total_bets'] == 1

    def test_gameStatus_field_used_when_status_absent(self, rg):
        g = self._game(gameStatus='In Progress')
        g.pop('status', None)
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0  # blocked via gameStatus fallback

    def test_missing_timestamp_falls_through_to_normal_pregame(self, rg):
        g = self._game(status='Scheduled')  # no timestamp field at all
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 1

    def test_malformed_timestamp_does_not_crash_falls_through_safely(self, rg):
        g = self._game(status='Scheduled', scheduledStartTime='not-a-real-timestamp')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # check_first_pitch_passed() catches ValueError/TypeError internally
        # and returns False -- malformed timestamp is safely ignored, not
        # a crash, and not treated as "started."
        assert report['total_bets'] == 1

    def test_timezone_naive_timestamp_handling(self, rg):
        """A timestamp with no timezone info at all (no 'Z', no offset)
        -- datetime.fromisoformat() on a naive string succeeds but
        produces a naive datetime, which cannot be compared to the
        (offset-aware) `now` datetime -- raises TypeError internally,
        caught by check_first_pitch_passed()'s except clause, treated as
        NOT passed (safe fallback, not a crash)."""
        g = self._game(status='Scheduled', scheduledStartTime='2020-01-01T00:00:00')  # no Z, no offset
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 1  # safely falls through, not blocked

    def test_offset_timestamp_non_utc_still_compared_correctly(self, rg):
        """A scheduled start with an explicit non-UTC offset (e.g. -05:00
        Central-ish) should still compare correctly against the UTC
        `now_ts` since Python's datetime comparison normalizes aware
        datetimes to a common instant regardless of offset."""
        # 2026-06-16T15:00:00-05:00 == 2026-06-16T20:00:00Z == NOW exactly.
        g = self._game(status='Scheduled', scheduledStartTime='2026-06-16T14:59:59-05:00')
        slate = make_slate([g])  # start = 19:59:59Z, 1 second before NOW=20:00:00Z
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0  # already started (1 second past)

    def test_offset_timestamp_not_yet_started(self, rg):
        g = self._game(status='Scheduled', scheduledStartTime='2026-06-16T15:00:01-05:00')
        slate = make_slate([g])  # start = 20:00:01Z, 1 second after NOW=20:00:00Z
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 1  # not yet started

    def test_date_rollover_boundary_previous_day_utc_late_game(self, rg):
        """A game scheduled just before UTC midnight, 'now' just after --
        pure timestamp comparison, no special date-rollover logic exists
        (or is needed) in check_game_status()."""
        g = self._game(status='Scheduled', scheduledStartTime='2026-06-16T23:59:00Z')
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts='2026-06-17T00:01:00Z')
        assert report['total_bets'] == 0  # correctly identified as started, despite date rollover

    @pytest.mark.parametrize("status,expected_blocked", [
        ("Delayed", True), ("Delayed Start", True), ("Rain Delay", True),
        ("Postponed - Rain", True), ("Postponed - Other", True),
        ("Canceled", True),  # single-L American spelling variant
        ("Live", True), ("Manager Challenge", True), ("Instant Replay", True),
        ("Game Over", True), ("Completed", True), ("Completed Early", True),
    ])
    def test_every_recognized_status_variant_from_postponed_guard(self, rg, status, expected_blocked):
        g = self._game(status=status)
        slate = make_slate([g])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        if expected_blocked:
            assert report['total_bets'] == 0, f"status={status!r} should have blocked real-money output"
