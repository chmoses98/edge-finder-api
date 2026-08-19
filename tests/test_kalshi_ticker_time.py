#!/usr/bin/env python3
"""
tests/test_kalshi_ticker_time.py
======================================
Coverage for lib/kalshi_ticker_time.py -- the canonical elapsed-clock-
minutes distance helper extracted after a real raw-integer-subtraction
bug was found independently in scripts/build_hitter_projection_board.py's
doubleheader resolver and scripts/discover_kalshi_mlb_markets.py's
resolve_game_match (PR #93 review).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.kalshi_ticker_time import hhmm_to_minutes, hhmm_distance_minutes, closest_by_hhmm


class TestHhmmToMinutes:
    def test_parses_valid_time(self):
        assert hhmm_to_minutes("1305") == 13 * 60 + 5

    def test_midnight(self):
        assert hhmm_to_minutes("0000") == 0

    def test_last_minute_of_day(self):
        assert hhmm_to_minutes("2359") == 23 * 60 + 59

    def test_none_for_wrong_length(self):
        assert hhmm_to_minutes("905") is None
        assert hhmm_to_minutes("13055") is None

    def test_none_for_non_digits(self):
        assert hhmm_to_minutes("13:0") is None

    def test_none_for_invalid_hour_or_minute(self):
        assert hhmm_to_minutes("2405") is None
        assert hhmm_to_minutes("1360") is None

    def test_none_for_empty_or_missing(self):
        assert hhmm_to_minutes("") is None
        assert hhmm_to_minutes(None) is None


class TestHhmmDistanceMinutes:
    def test_the_exact_bug_report_example(self):
        """12:55 vs 13:05 is 10 real minutes apart -- the raw-integer-subtraction bug
        (int('1305') - int('1255')) would have produced 50."""
        assert hhmm_distance_minutes("1255", "1305") == 10

    def test_cross_hour_boundary_11_to_12(self):
        assert hhmm_distance_minutes("1155", "1205") == 10

    def test_cross_hour_boundary_18_to_19(self):
        assert hhmm_distance_minutes("1855", "1905") == 10

    def test_a_more_distant_same_hour_candidate_is_never_preferred(self):
        """The bug report's own scenario: a 13:30 candidate (raw int diff from 1305
        is 25) must NOT be preferred over a 12:55 candidate (true diff 10) when the
        ticker time is 13:05."""
        d_1255 = hhmm_distance_minutes("1255", "1305")
        d_1330 = hhmm_distance_minutes("1330", "1305")
        assert d_1255 < d_1330
        assert d_1255 == 10
        assert d_1330 == 25

    def test_normal_same_hour_comparison(self):
        assert hhmm_distance_minutes("1300", "1345") == 45

    def test_exact_match_is_zero(self):
        assert hhmm_distance_minutes("1940", "1940") == 0

    def test_midnight_boundary_not_treated_as_circular(self):
        """23:50 vs 00:10 are NOT wrapped around midnight -- these two times are
        always compared within the SAME calendar date in every real caller (see
        module docstring), so the correct linear distance is large (23:50 is near
        the end of one day, 00:10 is near the start of the same 0-1439 minute
        range), not the circular 20-minute distance a wraparound-aware clock
        comparison would give. This pins the deliberate non-circular choice."""
        assert hhmm_distance_minutes("2350", "0010") == 1420

    def test_none_when_either_side_unparseable(self):
        assert hhmm_distance_minutes(None, "1305") is None
        assert hhmm_distance_minutes("1305", "bad") is None
        assert hhmm_distance_minutes("2599", "1305") is None


class TestClosestByHhmm:
    def _c(self, hhmm, label):
        return {"time_str": hhmm, "label": label}

    def test_unique_closest_is_identified(self):
        candidates = [self._c("1255", "a"), self._c("1330", "b")]
        best, is_unique = closest_by_hhmm("1305", candidates, key=lambda c: c["time_str"])
        assert best["label"] == "a"
        assert is_unique is True

    def test_genuine_tie_is_reported_as_not_unique(self):
        """Two candidates equidistant from the ticker time -- a real, structural
        ambiguity that must never be silently resolved by picking whichever the
        tie-break returns first."""
        candidates = [self._c("1250", "a"), self._c("1320", "b")]
        best, is_unique = closest_by_hhmm("1305", candidates, key=lambda c: c["time_str"])
        assert is_unique is False

    def test_exact_match_beats_a_tie_and_is_unique(self):
        candidates = [self._c("1305", "exact"), self._c("1250", "a"), self._c("1320", "b")]
        best, is_unique = closest_by_hhmm("1305", candidates, key=lambda c: c["time_str"])
        assert best["label"] == "exact"
        assert is_unique is True

    def test_missing_target_time_is_not_unique(self):
        candidates = [self._c("1255", "a"), self._c("1330", "b")]
        best, is_unique = closest_by_hhmm(None, candidates, key=lambda c: c["time_str"])
        assert is_unique is False
        assert best is candidates[0]  # fallback-eligible callers get the first candidate

    def test_single_candidate_is_always_unique(self):
        candidates = [self._c("1255", "a")]
        best, is_unique = closest_by_hhmm("1900", candidates, key=lambda c: c["time_str"])
        assert best["label"] == "a"
        assert is_unique is True
