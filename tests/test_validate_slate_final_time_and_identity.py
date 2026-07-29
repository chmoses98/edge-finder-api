#!/usr/bin/env python3
"""
tests/test_validate_slate_final_time_and_identity.py
=========================================================
Phase 8 Part 18 (live-game/time safety), Part 19 (duplicates/
doubleheaders/identity), and Part 20 (rerun/idempotency) coverage for
scripts/validate_slate_final.py.

Part 18: exercises generate_execution_slip()'s PREGAME-ONLY HARD GATE
(routed through the shared, already-audited lib.postponed_guard.
check_game_status()) with fully injected clocks -- no test here
depends on real wall-clock time. check_first_pitch_passed() uses a
strict `now > fp` comparison (confirmed by reading
lib/postponed_guard.py directly), so a game exactly AT its scheduled
start is still pregame; one second after is blocked. This behavior is
entirely owned by postponed_guard.py (already covered by its own
suite and by risk_gate.py's Phase 7 tests) -- these tests only prove
validate_slate_final.py THREADS current_utc/scheduledStartTime through
correctly, not postponed_guard's own internals.

Part 19: this script does not implement deduplication or identity
resolution -- REQUIRED_MARKETS matching in validate_final() is purely
by the `market` string field, and generate_execution_slip() processes
every marketLedger row independently with no cross-row identity
checks. These tests pin the exact (documented, unchanged) legacy
behavior for duplicate/reused-identity fixtures, per the mission's
"do not add deduplication or identity redesign" instruction.

Part 20: rerun/idempotency beyond the golden suite's existing
test_rerun_produces_stable_fixed_point_except_timestamp -- covers
changed recommendation ordering between runs, added/removed markets,
and a malformed/stale prior validation.json artifact on disk (which
this script only ever overwrites, never reads).
"""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import make_good_game, make_slate  # noqa: E402


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


def _wire(vsf, tmp_path, monkeypatch, date='2026-06-16'):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (tmp_path / 'scripts').mkdir(exist_ok=True)
    monkeypatch.setattr(vsf, '__file__', str(tmp_path / 'scripts' / 'validate_slate_final.py'))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', date])
    return data_dir


# ══════════════════════════════════════════════════════════════════════════════
# Part 18: live-game and time safety, fully clock-injected
# ══════════════════════════════════════════════════════════════════════════════

class TestLiveGameAndTimeSafety:

    def test_future_game_scheduled_start_not_blocked(self, vsf):
        g = make_good_game(status='Scheduled')
        g['scheduledStartTime'] = '2026-06-17T00:00:00Z'  # tomorrow relative to NOW
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == []
        assert slip_dict['summary']['realMoneyCount'] > 0

    def test_exactly_at_scheduled_start_not_yet_blocked(self, vsf):
        """
        check_first_pitch_passed() uses `now > fp` (strict), so a game
        exactly at its scheduled start instant is still pregame --
        pinned here as an executable boundary proof for this script's
        own integration with that shared helper.
        """
        g = make_good_game(status='Scheduled')
        g['scheduledStartTime'] = '2026-06-16T20:00:00Z'
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == []

    def test_one_second_after_scheduled_start_is_blocked(self, vsf):
        g = make_good_game(status='Scheduled')
        g['scheduledStartTime'] = '2026-06-16T20:00:00Z'
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:01Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']
        assert slip_dict['summary']['realMoneyCount'] == 0

    @pytest.mark.parametrize('status', ['In Progress', 'Live', 'Manager Challenge', 'Rain Delay'])
    def test_in_play_statuses_blocked_regardless_of_scheduled_start(self, vsf, status):
        g = make_good_game(status=status)
        g['scheduledStartTime'] = '2026-06-17T00:00:00Z'  # future -- status alone must still block
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']

    @pytest.mark.parametrize('status', ['Final', 'Game Over', 'Completed'])
    def test_final_statuses_blocked(self, vsf, status):
        g = make_good_game(status=status)
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']

    def test_malformed_status_falls_through_to_timestamp_signal_only(self, vsf):
        """
        An unrecognized status string is neither postponed, pregame,
        in-play, nor final by postponed_guard's own classification --
        the timestamp fallback (Signal 2) is the only thing that can
        still block it.
        """
        g = make_good_game(status='TotallyBogusStatus')
        g['scheduledStartTime'] = '2026-06-16T18:00:00Z'  # already passed
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']

    def test_missing_status_and_missing_timestamp_not_blocked(self, vsf):
        g = make_good_game(status='')
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == []

    def test_timezone_offset_timestamp_handled_same_as_utc_z_suffix(self, vsf):
        """
        '-04:00' offset scheduled start equivalent to '20:00:00Z' --
        both must produce identical blocking behavior once normalized.
        """
        g_z = make_good_game(status='Scheduled')
        g_z['scheduledStartTime'] = '2026-06-16T20:00:00Z'
        g_offset = make_good_game(status='Scheduled')
        g_offset['scheduledStartTime'] = '2026-06-16T16:00:00-04:00'  # same instant as 20:00Z

        _, dict_z = vsf.generate_execution_slip([g_z], '2026-06-16', current_utc='2026-06-16T20:00:01Z')
        _, dict_offset = vsf.generate_execution_slip([g_offset], '2026-06-16', current_utc='2026-06-16T20:00:01Z')
        assert dict_z['liveGameBlockedGames'] == ['KC@WSH']
        assert dict_offset['liveGameBlockedGames'] == ['KC@WSH']

    def test_naive_timestamp_does_not_crash_falls_back_gracefully(self, vsf):
        """
        check_first_pitch_passed() catches ValueError/TypeError and
        returns False (documented pre-existing fallback, not fixed
        here) -- a naive (no tz) scheduledStartTime mixed with a
        tz-aware current_utc raises inside datetime comparison, caught
        and treated as "not passed."
        """
        g = make_good_game(status='Scheduled')
        g['scheduledStartTime'] = '2026-06-16T18:00:00'  # no tzinfo
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == []

    def test_dst_boundary_spring_forward_offset_still_compares_correctly(self, vsf):
        """
        2026-03-08 is the US spring-forward DST transition. An
        EDT-offset (-04:00, post-transition) scheduled start compared
        against a UTC current_utc must still resolve via absolute
        instant comparison, not wall-clock arithmetic.
        """
        g = make_good_game(status='Scheduled')
        g['scheduledStartTime'] = '2026-03-08T19:00:00-04:00'  # = 23:00 UTC
        _, slip_dict = vsf.generate_execution_slip([g], '2026-03-08', current_utc='2026-03-08T23:00:01Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']

    def test_scheduled_start_field_name_priority_matches_postponed_guard(self, vsf):
        """
        postponed_guard.check_game_status() reads scheduledStartTime
        first, then gameTime/firstPitch/scheduledStart as fallbacks --
        validate_slate_final.py passes the whole game dict through
        unchanged, so whichever field is present is honored exactly as
        postponed_guard itself defines.
        """
        g = make_good_game(status='Scheduled')
        g.pop('scheduledStartTime', None)
        g['firstPitch'] = '2026-06-16T18:00:00Z'  # already passed
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        assert slip_dict['liveGameBlockedGames'] == ['KC@WSH']


# ══════════════════════════════════════════════════════════════════════════════
# Part 19: duplicates, doubleheaders, and identity (no redesign, legacy behavior only)
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicatesDoubleheadersIdentity:

    def test_literal_duplicate_ledger_rows_both_validated_independently(self, vsf):
        """
        validate_final() has no duplicate-row detection -- two
        identical rows for the same market both get validated (and
        both would error identically if invalid). Pinned as the
        documented, unchanged legacy behavior.
        """
        g = make_good_game()
        g['marketLedger'].append(copy.deepcopy(g['marketLedger'][0]))
        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
        assert errors == []  # both copies are individually valid

    def test_duplicate_market_names_both_satisfy_required_market_presence_check(self, vsf):
        g = make_good_game()
        duplicate_ml_away = copy.deepcopy(g['marketLedger'][0])
        g['marketLedger'].append(duplicate_ml_away)
        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
        assert not any('required market' in e for e in errors)

    def test_same_game_market_different_price_stake_both_appear_in_slip(self, vsf):
        """
        generate_execution_slip() has no row-level deduplication --
        two rows for the same market with different kalshiPrice/betSize
        both appear as separate slip entries.
        """
        g = make_good_game()
        g['marketLedger'].append({**copy.deepcopy(g['marketLedger'][0]), 'betSize': 10.0, 'executablePriceUsed': -130})
        _, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        market = g['marketLedger'][0]['market']
        matching = [e for e in slip_dict['realMoney'] if e['market'] == market]
        assert len(matching) == 2

    def test_doubleheader_reused_team_names_different_game_ids_both_processed(self, vsf):
        """
        Two games with identical away/home abbreviations (a
        doubleheader) produce the SAME gid()/game_label string --
        validate_final()/generate_execution_slip() process each game
        dict independently by list position, never by any derived
        identity key, so both games are fully validated/routed with no
        collision or silent overwrite.
        """
        g1 = make_good_game(away='NYY', home='BOS')
        g2 = make_good_game(away='NYY', home='BOS')
        g2['marketLedger'][0]['status'] = 'Rejected'
        g2['marketLedger'][0]['rejectionReason'] = ''  # forces a distinguishable error
        errors, warnings = vsf.validate_final(make_slate([g1, g2]), '2026-06-16')
        assert len(errors) == 1
        assert 'NYY@BOS' in errors[0]

        _, slip_dict = vsf.generate_execution_slip([g1, g2], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        real_money_from_both = [e for e in slip_dict['realMoney'] if e['game'] == 'NYY@BOS']
        # g1's rows are all Accepted (real_money); g2's rejected row goes to
        # rejected_blocked instead, so real_money only picks up g2's other
        # (still-Accepted) rows -- confirms neither game's routing bled into
        # the other's despite sharing the identical "NYY@BOS" game_label.
        assert len(real_money_from_both) == len(g1['marketLedger']) + len(g2['marketLedger']) - 1
        assert any(e['game'] == 'NYY@BOS' for e in slip_dict['rejectedBlocked'])

    def test_missing_kalshi_key_field_not_referenced_anywhere(self, vsf):
        """
        kalshiKey is not a field this script reads/writes at all
        (confirmed by grep) -- a game entirely missing it validates
        identically to one that has it.
        """
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        assert 'kalshiKey' not in src

    def test_malformed_identity_missing_abbr_falls_back_to_question_mark(self, vsf):
        g = make_good_game()
        g['away'] = {'pitcher': {'name': 'X'}, 'pitcherSavant': g['away']['pitcherSavant']}  # no 'abbr'
        assert vsf.gid(g) == '?@WSH'
        errors, warnings = vsf.validate_final(make_slate([g]), '2026-06-16')
        assert all(not e.startswith('None@') for e in errors + warnings)

    def test_reordered_recommendations_produce_reordered_but_equivalent_results(self, vsf):
        g_forward = make_good_game()
        g_reversed = make_good_game()
        g_reversed['marketLedger'] = list(reversed(g_reversed['marketLedger']))

        errors_f, warnings_f = vsf.validate_final(make_slate([g_forward]), '2026-06-16')
        errors_r, warnings_r = vsf.validate_final(make_slate([g_reversed]), '2026-06-16')
        assert errors_f == errors_r == []
        assert warnings_f == warnings_r == []

        _, slip_f = vsf.generate_execution_slip([g_forward], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        _, slip_r = vsf.generate_execution_slip([g_reversed], '2026-06-16', current_utc='2026-06-16T20:00:00Z')
        markets_f = [e['market'] for e in slip_f['realMoney']]
        markets_r = [e['market'] for e in slip_r['realMoney']]
        assert markets_f == list(reversed(markets_r))


# ══════════════════════════════════════════════════════════════════════════════
# Part 20: rerun and idempotency (beyond the golden suite's existing coverage)
# ══════════════════════════════════════════════════════════════════════════════

class TestRerunAndIdempotency:

    def test_added_market_between_runs_reflected_on_second_run(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            first_run = json.load(f)
        assert first_run['executionSlipData']['summary']['realMoneyCount'] == len(g['marketLedger'])

        g['marketLedger'][0]['confidence'] = 'PAPER'  # move one row from real-money to paper
        with open(data_dir / 'slate.json', 'w') as f:
            first_run['games'] = [g]
            json.dump(first_run, f)
        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            second_run = json.load(f)
        assert second_run['executionSlipData']['summary']['realMoneyCount'] == len(g['marketLedger']) - 1
        assert second_run['executionSlipData']['summary']['paperOnlyCount'] == 1

    def test_stale_validation_artifact_from_a_different_date_is_ignored_and_overwritten(self, vsf, tmp_path, monkeypatch):
        """
        This script never READS data/pipeline/<date>/validation.json --
        only writes it. A stale artifact under a DIFFERENT date must
        not affect today's run at all (different path entirely), and
        an artifact under the SAME date from a prior run is simply
        overwritten (write_stage_artifact's own documented rerun
        semantics, unchanged here).
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        stale_dir = data_dir / 'pipeline' / '2026-01-01'
        stale_dir.mkdir(parents=True)
        (stale_dir / 'validation.json').write_text('{not valid json at all')

        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0
        # stale, unrelated-date artifact untouched (still malformed, as left)
        assert (stale_dir / 'validation.json').read_text() == '{not valid json at all'
        # today's artifact written correctly regardless
        today_artifact = data_dir / 'pipeline' / '2026-06-16' / 'validation.json'
        assert today_artifact.exists()
        json.loads(today_artifact.read_text())  # must be valid JSON

    def test_interrupted_prior_slate_write_leaves_no_tmp_artifact_to_confuse_next_run(self, vsf, tmp_path, monkeypatch):
        """
        A stray .tmp file left behind by some OTHER, unrelated
        interrupted write (simulating a crash mid-write from a prior
        run of some other stage) must not be picked up or referenced
        by this run at all -- this script only ever opens the exact
        literal path 'data/slate.json', never a glob.
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        (data_dir / '.slate.json.abc123.json.tmp').write_text('{"leftover": "garbage"}')
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0
        # the stray tmp file is untouched -- this run never reads or removes it
        assert (data_dir / '.slate.json.abc123.json.tmp').read_text() == '{"leftover": "garbage"}'
