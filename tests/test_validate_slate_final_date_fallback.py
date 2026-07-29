#!/usr/bin/env python3
"""
tests/test_validate_slate_final_date_fallback.py
=====================================================
PR #9 hardening addition (Part 10): dedicated, clock-injected coverage
of expected_date() itself. Neither this file's predecessor test suites
nor the golden-baseline suite ever called expected_date() directly and
asserted on its output under an injected clock -- a real, material gap
given this is the ONE genuinely clock-dependent code path anywhere in
scripts/validate_slate_final.py (every other clock-shaped parameter,
`current_utc` in generate_execution_slip(), is always explicitly
threaded in by the caller, never read from the real clock inside a
pure function).

Grep-confirmed before writing these tests: expected_date() reads
sys.argv[1] and, if absent/falsy, datetime.now(timezone.utc). It never
reads ANY environment variable at all -- so "invalid/valid environment
override" (mentioned in the review mission) does not apply to this
function; the only override mechanism is the CLI date argument.
Similarly, the fallback is a fixed ET APPROXIMATION only -- Central
Time is never referenced anywhere in this file (grep-confirmed), so
"Central Time date rollover" is not a code path this function has.

datetime.now cannot be monkeypatched directly on the built-in type, so
a subclass-substitution technique is used (bound to `vsf.datetime`,
the name resolved inside expected_date()'s own module namespace).
"""
import os
import sys
from datetime import timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


def _fixed_clock(vsf, monkeypatch, fixed_utc_dt):
    """Substitutes vsf.datetime with a stand-in whose .now(tz) always
    returns `fixed_utc_dt` (must already be UTC-aware), regardless of
    the `tz` argument passed -- matching how expected_date() calls
    datetime.now(timezone.utc)."""

    class _Fixed:
        @classmethod
        def now(cls, tz=None):
            return fixed_utc_dt

    monkeypatch.setattr(vsf, "datetime", _Fixed)


class TestNoEnvironmentOrCentralTimeDependency:

    def test_grep_confirms_no_env_var_read_anywhere_in_expected_date(self):
        import inspect
        sys.path.insert(0, SCRIPTS_DIR)
        import validate_slate_final as vsf
        src = inspect.getsource(vsf.expected_date)
        assert 'os.environ' not in src
        assert 'os.getenv' not in src

    def test_grep_confirms_no_central_time_reference_anywhere_in_file(self):
        import re
        with open(os.path.join(SCRIPTS_DIR, 'validate_slate_final.py')) as f:
            src = f.read()
        assert 'Central' not in src
        assert 'America/Chicago' not in src
        # word-boundary check for a standalone "CT" token (timezone
        # abbreviation) -- a bare substring check would false-positive
        # on unrelated words like "function" or "selection"
        assert re.search(r'\bCT\b', src) is None


class TestCliArgTakesPriorityOverClock:

    def test_cli_arg_used_verbatim_clock_never_read(self, vsf, monkeypatch):
        class _NoClockDatetime:
            @classmethod
            def now(cls, tz=None):
                raise AssertionError('expected_date() read the clock despite a CLI arg being present')

        monkeypatch.setattr(vsf, 'datetime', _NoClockDatetime)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', '2026-01-01'])
        assert vsf.expected_date() == '2026-01-01'

    def test_cli_arg_malformed_still_used_verbatim_no_validation(self, vsf, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', 'not-a-real-date-at-all'])
        assert vsf.expected_date() == 'not-a-real-date-at-all'

    def test_cli_arg_empty_string_is_falsy_falls_through_to_clock(self, vsf, monkeypatch):
        fixed = __import__('datetime').datetime(2026, 6, 16, 20, 0, 0, tzinfo=timezone.utc)
        _fixed_clock(vsf, monkeypatch, fixed)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', ''])
        assert vsf.expected_date() == '2026-06-16'

    def test_no_cli_arg_at_all_falls_through_to_clock(self, vsf, monkeypatch):
        fixed = __import__('datetime').datetime(2026, 6, 16, 20, 0, 0, tzinfo=timezone.utc)
        _fixed_clock(vsf, monkeypatch, fixed)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-06-16'


class TestFixedFourHourOffsetFallbackBehavior:
    """
    Pins the EXACT (buggy, undocumented-to-callers, not fixed in this
    PR) fixed-4-hour-UTC-offset approximation: always UTC-4, regardless
    of actual EDT/EST/DST status.
    """

    def _at(self, y, m, d, hh, mm=0):
        import datetime as _dt
        return _dt.datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)

    def test_edt_season_fixed_offset_happens_to_be_correct(self, vsf, monkeypatch):
        """
        Mid-July: real US Eastern is EDT (UTC-4) -- the fixed -4h
        formula is ACTUALLY CORRECT here, coincidentally.
        04:30 UTC - 4h = 00:30 ET on the same calendar day.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 7, 15, 4, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-07-15'

    def test_est_season_fixed_offset_is_off_by_one_hour_real_pre_existing_defect(self, vsf, monkeypatch):
        """
        Mid-January: real US Eastern is EST (UTC-5), but the code
        always subtracts exactly 4 hours. At 04:30 UTC:
          - real EST local time = 23:30 the PREVIOUS calendar day
          - this function's fixed-offset result = 00:30 the SAME
            calendar day as the UTC timestamp
        These disagree by one full calendar date near this boundary --
        pinning the documented, NOT-fixed-in-this-PR defect as an
        executable proof, not just prose.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 1, 15, 4, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        # the buggy fixed-offset result:
        assert vsf.expected_date() == '2026-01-15'
        # what a DST-correct EST (UTC-5) computation would have produced,
        # for contrast (not what the function returns):
        real_est_date = (self._at(2026, 1, 15, 4, 30) - timedelta(hours=5)).strftime('%Y-%m-%d')
        assert real_est_date == '2026-01-14'
        assert vsf.expected_date() != real_est_date

    def test_utc_midnight_crossover_shifts_date_backward_by_fixed_offset(self, vsf, monkeypatch):
        """02:00 UTC minus 4h crosses back over UTC midnight to the
        previous calendar date."""
        _fixed_clock(vsf, monkeypatch, self._at(2026, 6, 17, 2, 0))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-06-16'

    def test_dst_spring_forward_transition_day_2026_03_08_not_specially_handled(self, vsf, monkeypatch):
        """
        2026-03-08 02:00 local is the US spring-forward instant. The
        fixed-offset formula has no concept of this transition at all
        -- it applies -4h uniformly on both sides, unlike a real
        zoneinfo-aware ET conversion would (which would use -5h just
        before the transition and -4h just after). This test does not
        assert "correct" DST behavior (there is none) -- it pins that
        the same -4h math runs through the transition unchanged.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 3, 8, 6, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-03-08'  # 06:30 - 4h = 02:30, same day

    def test_dst_fall_back_transition_day_2026_11_01_not_specially_handled(self, vsf, monkeypatch):
        _fixed_clock(vsf, monkeypatch, self._at(2026, 11, 1, 3, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-10-31'  # 03:30 - 4h = 23:30 previous day

    def test_deterministic_given_fixed_clock(self, vsf, monkeypatch):
        fixed = self._at(2026, 6, 16, 20, 0)
        _fixed_clock(vsf, monkeypatch, fixed)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        r1 = vsf.expected_date()
        r2 = vsf.expected_date()
        assert r1 == r2 == '2026-06-16'
