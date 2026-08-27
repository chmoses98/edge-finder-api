import pytest

from lib.edgelab.backtest.pinnacle_reconstruction import (
    MAX_MINUTES_BEFORE_START,
    minutes_before_start,
    select_closest_pregame_snapshot,
    reject_reason,
    american_to_implied_probability,
    devig_two_sided,
    matched_total_line,
)


def _snap(requested_at, **kw):
    return {"requestedAt": requested_at, **kw}


class TestMinutesBeforeStart:
    def test_positive_when_snapshot_before_start(self):
        assert minutes_before_start(0, 1800) == 30.0

    def test_zero_at_exact_start(self):
        assert minutes_before_start(1000, 1000) == 0.0

    def test_negative_when_snapshot_after_start(self):
        assert minutes_before_start(2000, 1000) == pytest.approx(-16.6667, abs=1e-3)


class TestSelectClosestPregameSnapshot:
    COMMENCE = 100_000  # arbitrary epoch seconds

    def test_selects_the_closest_qualifying_snapshot(self):
        snaps = [
            _snap(self.COMMENCE - 50 * 60, label="50min"),
            _snap(self.COMMENCE - 20 * 60, label="20min"),
            _snap(self.COMMENCE - 40 * 60, label="40min"),
        ]
        result = select_closest_pregame_snapshot(snaps, self.COMMENCE)
        assert result["label"] == "20min"
        assert result["minutesBeforeStart"] == pytest.approx(20.0)

    def test_rejects_post_start_snapshot(self):
        snaps = [_snap(self.COMMENCE + 5 * 60, label="after")]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE) is None

    def test_rejects_snapshot_at_exact_start(self):
        snaps = [_snap(self.COMMENCE, label="exact")]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE) is None

    def test_rejects_snapshot_beyond_max_lookback(self):
        snaps = [_snap(self.COMMENCE - 90 * 60, label="too_early")]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE) is None

    def test_accepts_snapshot_exactly_at_max_lookback_boundary(self):
        snaps = [_snap(self.COMMENCE - MAX_MINUTES_BEFORE_START * 60, label="boundary")]
        result = select_closest_pregame_snapshot(snaps, self.COMMENCE)
        assert result is not None
        assert result["minutesBeforeStart"] == pytest.approx(float(MAX_MINUTES_BEFORE_START))

    def test_never_uses_a_different_games_only_snapshot_when_none_qualify(self):
        """A snapshot far outside the window must never be substituted --
        this is the exact 'never use a later game's snapshot as a proxy
        for an earlier game' guard, generalized to any disqualified
        candidate."""
        snaps = [_snap(self.COMMENCE - 500 * 60, label="way_too_early"), _snap(self.COMMENCE + 500 * 60, label="way_too_late")]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE) is None

    def test_empty_candidates_returns_none(self):
        assert select_closest_pregame_snapshot([], self.COMMENCE) is None

    def test_custom_max_minutes_respected(self):
        snaps = [_snap(self.COMMENCE - 45 * 60)]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE, max_minutes_before=30) is None
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE, max_minutes_before=60) is not None

    def test_deterministic(self):
        snaps = [_snap(self.COMMENCE - 50 * 60), _snap(self.COMMENCE - 20 * 60)]
        first = select_closest_pregame_snapshot(snaps, self.COMMENCE)
        second = select_closest_pregame_snapshot(snaps, self.COMMENCE)
        assert first == second


class TestRejectReason:
    COMMENCE = 100_000

    def test_no_candidates(self):
        assert reject_reason([], self.COMMENCE) == "NO_CANDIDATES"

    def test_all_post_start(self):
        snaps = [_snap(self.COMMENCE + 100), _snap(self.COMMENCE + 200)]
        assert reject_reason(snaps, self.COMMENCE) == "ALL_POST_START"

    def test_all_too_early(self):
        snaps = [_snap(self.COMMENCE - 200 * 60)]
        assert reject_reason(snaps, self.COMMENCE) == "ALL_TOO_EARLY"

    def test_never_called_when_a_qualifying_snapshot_exists(self):
        # sanity: reject_reason is diagnostic-only, doesn't affect selection
        snaps = [_snap(self.COMMENCE - 30 * 60)]
        assert select_closest_pregame_snapshot(snaps, self.COMMENCE) is not None


class TestAmericanToImpliedProbability:
    def test_negative_price(self):
        assert american_to_implied_probability(-150) == pytest.approx(0.6)

    def test_positive_price(self):
        assert american_to_implied_probability(150) == pytest.approx(0.4)

    def test_even_money(self):
        assert american_to_implied_probability(100) == pytest.approx(0.5)

    def test_none_price(self):
        assert american_to_implied_probability(None) is None


class TestDevigTwoSided:
    def test_removes_vig_and_sums_to_one(self):
        fair_a, fair_b, overround = devig_two_sided(-120, 100)
        assert fair_a + fair_b == pytest.approx(1.0)
        assert overround > 0

    def test_symmetric_market_has_fifty_fifty_fair(self):
        fair_a, fair_b, overround = devig_two_sided(-110, -110)
        assert fair_a == pytest.approx(0.5, abs=1e-6)
        assert fair_b == pytest.approx(0.5, abs=1e-6)

    def test_none_on_missing_price(self):
        assert devig_two_sided(None, -110) == (None, None, None)
        assert devig_two_sided(-110, None) == (None, None, None)

    def test_overround_reflects_vig_magnitude(self):
        _, _, overround_tight = devig_two_sided(-105, -105)
        _, _, overround_wide = devig_two_sided(-130, 110)
        assert overround_wide > overround_tight


class TestMatchedTotalLine:
    def _totals_market(self, outcomes):
        return {"key": "totals", "outcomes": outcomes}

    def test_exact_line_match(self):
        market = self._totals_market([
            {"name": "Over", "point": 8.5, "price": -110},
            {"name": "Under", "point": 8.5, "price": -110},
        ])
        over, under = matched_total_line(market, 8.5)
        assert over == -110
        assert under == -110

    def test_rejects_different_line_never_cross_compares(self):
        market = self._totals_market([
            {"name": "Over", "point": 9.0, "price": -110},
            {"name": "Under", "point": 9.0, "price": -110},
        ])
        over, under = matched_total_line(market, 8.5)
        assert over is None
        assert under is None

    def test_only_one_side_present_at_target_line(self):
        market = self._totals_market([
            {"name": "Over", "point": 8.5, "price": -110},
            {"name": "Under", "point": 9.0, "price": -110},
        ])
        over, under = matched_total_line(market, 8.5)
        assert over == -110
        assert under is None

    def test_none_market(self):
        assert matched_total_line(None, 8.5) == (None, None)

    def test_empty_outcomes(self):
        assert matched_total_line(self._totals_market([]), 8.5) == (None, None)
