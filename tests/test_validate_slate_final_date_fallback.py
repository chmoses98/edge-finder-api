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
Similarly, Central Time is never referenced anywhere in this file
(grep-confirmed), so "Central Time date rollover" is not a code path
this function has.

Date Reliability mission update: the clock fallback used to subtract a
FIXED 4-hour UTC offset (correct only while US Eastern observes EDT;
silently off by one calendar day near the UTC-morning boundary during
EST/winter -- a real, latent stale-date risk of exactly the class this
mission's "TZ boundary/rollover" requirement targets, even though it
was never the literal cause of the reported Aug 19 incident, which
lived in a different file's date-format bug, already fixed and pinned
by tests/test_api_date.py). expected_date() now converts via a real
zoneinfo-aware America/New_York conversion instead, so this class pins
the CORRECTED behavior across the EDT/EST boundary and both DST
transition days, rather than documenting the old defect as pre-
existing-and-not-fixed.

datetime.now cannot be monkeypatched directly on the built-in type, so
a subclass-substitution technique is used (bound to `vsf.datetime`,
the name resolved inside expected_date()'s own module namespace) --
its `.now(tz)` returns a real `datetime` instance, so the subsequent
real `.astimezone(ZoneInfo(...))` call inside expected_date() still
works unmodified against it.
"""
import os
import sys
from datetime import timezone

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


class TestZoneinfoAwareFallbackBehavior:
    """
    Pins the CORRECTED behavior: a real America/New_York zoneinfo
    conversion, using -4h (EDT) in summer and -5h (EST) in winter, with
    the DST transition itself handled correctly rather than
    approximated by a fixed offset.
    """

    def _at(self, y, m, d, hh, mm=0):
        import datetime as _dt
        return _dt.datetime(y, m, d, hh, mm, 0, tzinfo=timezone.utc)

    def test_edt_season_uses_utc_minus_4(self, vsf, monkeypatch):
        """
        Mid-July: real US Eastern is EDT (UTC-4).
        04:30 UTC - 4h = 00:30 ET on the same calendar day.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 7, 15, 4, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-07-15'

    def test_est_season_uses_utc_minus_5_previously_a_real_defect(self, vsf, monkeypatch):
        """
        Mid-January: real US Eastern is EST (UTC-5). At 04:30 UTC:
          - real EST local time = 23:30 the PREVIOUS calendar day
          - the OLD fixed -4h-offset code returned 00:30 the SAME
            calendar day as the UTC timestamp -- a genuine one-day-off
            defect this fix closes (see the module docstring).
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 1, 15, 4, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-01-14'

    def test_utc_midnight_crossover_shifts_date_backward(self, vsf, monkeypatch):
        """02:00 UTC minus 4h (EDT, June) crosses back over UTC midnight
        to the previous calendar date."""
        _fixed_clock(vsf, monkeypatch, self._at(2026, 6, 17, 2, 0))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-06-16'

    def test_dst_spring_forward_transition_day_2026_03_08_before_transition_uses_est(self, vsf, monkeypatch):
        """
        2026-03-08 07:00 UTC is the US spring-forward instant (2:00 AM
        EST -> 3:00 AM EDT). At 06:30 UTC (before it), real Eastern is
        still EST (UTC-5): 06:30 - 5h = 01:30 ET, same calendar day —
        the SAME date this function returned under the old fixed -4h
        approximation (06:30 - 4h = 02:30, also same day) purely by
        coincidence of this particular instant not crossing a date
        boundary either way; the local TIME differs (01:30 vs 02:30)
        even though the DATE happens to match.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 3, 8, 6, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-03-08'

    def test_dst_fall_back_transition_day_2026_11_01_before_transition_uses_edt(self, vsf, monkeypatch):
        """
        2026-11-01 06:00 UTC is the US fall-back instant (2:00 AM EDT ->
        1:00 AM EST). At 03:30 UTC (before it), real Eastern is still
        EDT (UTC-4): 03:30 - 4h = 23:30 the previous calendar day — same
        date the old fixed -4h approximation produced for this instant.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 11, 1, 3, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-10-31'

    def test_dst_fall_back_transition_day_after_transition_uses_est_diverges_from_old_defect(self, vsf, monkeypatch):
        """
        The day after fall-back (EST now in effect), at a UTC instant
        where the correct -5h and the old buggy -4h approximation land
        on DIFFERENT calendar dates: 2026-11-02 04:30 UTC.
          - correct EST (-5h): 2026-11-01 23:30 -> date 2026-11-01
          - old fixed -4h bug: 2026-11-02 00:30 -> date 2026-11-02
        Proves the fix is genuinely DST-aware after the transition, not
        just coincidentally matching the old value near it.
        """
        _fixed_clock(vsf, monkeypatch, self._at(2026, 11, 2, 4, 30))
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        assert vsf.expected_date() == '2026-11-01'

    def test_deterministic_given_fixed_clock(self, vsf, monkeypatch):
        fixed = self._at(2026, 6, 16, 20, 0)
        _fixed_clock(vsf, monkeypatch, fixed)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py'])
        r1 = vsf.expected_date()
        r2 = vsf.expected_date()
        assert r1 == r2 == '2026-06-16'
