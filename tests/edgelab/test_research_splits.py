#!/usr/bin/env python3
"""
tests/edgelab/test_research_splits.py
==========================================
Coverage for lib/edgelab/research_splits.py -- chronological (date-based,
never contract-random) DEVELOPMENT/VALIDATION/HOLDOUT split.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import research_splits as rspl


def _dates(n, start="2026-01-01"):
    import datetime
    start_dt = datetime.date.fromisoformat(start)
    return [str(start_dt + datetime.timedelta(days=i)) for i in range(n)]


def test_split_is_by_date_position_not_shuffled():
    dates = _dates(10)
    split = rspl.chronological_split(dates)
    assert split[rspl.DEVELOPMENT] == dates[:6]
    assert split[rspl.VALIDATION] == dates[6:8]
    assert split[rspl.HOLDOUT] == dates[8:10]


def test_default_ratios_60_20_20():
    dates = _dates(100)
    split = rspl.chronological_split(dates)
    assert len(split[rspl.DEVELOPMENT]) == 60
    assert len(split[rspl.VALIDATION]) == 20
    assert len(split[rspl.HOLDOUT]) == 20


def test_holdout_is_strictly_latest_dates():
    dates = _dates(10)
    split = rspl.chronological_split(dates)
    assert max(split[rspl.DEVELOPMENT]) < min(split[rspl.VALIDATION])
    assert max(split[rspl.VALIDATION]) < min(split[rspl.HOLDOUT])


def test_duplicate_dates_deduplicated():
    dates = _dates(5) * 3  # each date repeated 3x, as if from many contracts on that date
    split = rspl.chronological_split(dates)
    assert split["totalDates"] == 5


def test_configurable_ratios():
    dates = _dates(10)
    split = rspl.chronological_split(dates, ratios={rspl.DEVELOPMENT: 0.5, rspl.VALIDATION: 0.3, rspl.HOLDOUT: 0.2})
    assert len(split[rspl.DEVELOPMENT]) == 5
    assert len(split[rspl.VALIDATION]) == 3
    assert len(split[rspl.HOLDOUT]) == 2


def test_ratios_must_sum_to_one():
    with pytest.raises(ValueError):
        rspl.chronological_split(_dates(10), ratios={rspl.DEVELOPMENT: 0.5, rspl.VALIDATION: 0.3, rspl.HOLDOUT: 0.3})


def test_ratios_must_have_exact_keys():
    with pytest.raises(ValueError):
        rspl.chronological_split(_dates(10), ratios={rspl.DEVELOPMENT: 0.7, rspl.VALIDATION: 0.3})


def test_empty_dates_returns_empty_partitions_not_error():
    split = rspl.chronological_split([])
    assert split[rspl.DEVELOPMENT] == split[rspl.VALIDATION] == split[rspl.HOLDOUT] == []
    assert split["totalDates"] == 0


def test_small_sample_labeled_framework_only():
    """The real current corpus (~13 days) must read as framework-only, never falsely mature."""
    split = rspl.chronological_split(_dates(13))
    assert split["maturity"] == rspl.MATURITY_FRAMEWORK_ONLY


def test_large_sample_labeled_usable():
    split = rspl.chronological_split(_dates(60))
    assert split["maturity"] == rspl.MATURITY_USABLE


def test_assign_split_and_label_rows():
    dates = _dates(10)
    split = rspl.chronological_split(dates)
    assert rspl.assign_split(dates[0], split) == rspl.DEVELOPMENT
    assert rspl.assign_split(dates[9], split) == rspl.HOLDOUT
    assert rspl.assign_split("1999-01-01", split) is None

    rows = [{"gameDate": dates[0], "x": 1}, {"gameDate": "1999-01-01", "x": 2}]
    labeled = rspl.label_rows_with_split(rows, split)
    assert labeled[0]["researchSplit"] == rspl.DEVELOPMENT
    assert labeled[1]["researchSplit"] is None
    assert "researchSplit" not in rows[0]  # original rows never mutated
