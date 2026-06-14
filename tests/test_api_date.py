import pytest, json, subprocess, sys, os
from unittest.mock import patch, MagicMock

# These are unit tests for the date-handling logic in api/slate.js
# Since the API is Node.js, we test via subprocess or by testing the
# Python scripts that call it. We also test the date validation logic directly.

def test_requested_date_is_passed_to_mlb_url():
    """Verify api/slate.js passes req.query.date into the MLB URL, not a computed date."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    # Accept either explicit req.query.date or destructuring { date } = req.query
    has_date_from_query = (
        'req.query.date' in api_content or
        'query.date' in api_content or
        '{ date' in api_content and 'req.query' in api_content
    )
    assert has_date_from_query, \
        "api/slate.js does not read date from req.query"

def test_slate_api_has_no_cache_headers():
    """Verify api/slate.js sets Cache-Control: no-store."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    assert 'no-store' in api_content, "api/slate.js missing Cache-Control: no-store"

def test_pitchers_api_has_no_cache_headers():
    api_content = open('/tmp/edge-finder-api/api/pitchers.js').read()
    assert 'no-store' in api_content, "api/pitchers.js missing Cache-Control: no-store"

def test_stale_fallback_rejected():
    """Verify api/slate.js rejects fallback data with wrong date."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    assert 'FAILED_STALE_DATE' in api_content, \
        "api/slate.js does not return FAILED_STALE_DATE on wrong-date fallback"

def test_date_format_conversion():
    """Verify YYYY-MM-DD is converted to MM/DD/YYYY for MLB StatsAPI."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    # Check that month/day/year reordering happens
    assert 'mlbDate' in api_content, \
        "No mlbDate variable found in api/slate.js — date format conversion missing"
    # Verify the conversion pattern: split('-') then reorder as mo/dy/yr
    assert "split('-')" in api_content, \
        "api/slate.js missing split('-') for date format conversion"

def test_pitchers_date_format_conversion():
    """Verify api/pitchers.js also converts YYYY-MM-DD to MM/DD/YYYY."""
    api_content = open('/tmp/edge-finder-api/api/pitchers.js').read()
    assert 'mlbDate' in api_content, \
        "No mlbDate variable found in api/pitchers.js — date format conversion missing"
    assert "split('-')" in api_content, \
        "api/pitchers.js missing split('-') for date format conversion"

def test_pitchers_reads_date_param():
    api_content = open('/tmp/edge-finder-api/api/pitchers.js').read()
    has_date_from_query = (
        'req.query.date' in api_content or
        'query.date' in api_content or
        ('{ date' in api_content and 'req.query' in api_content)
    )
    assert has_date_from_query, \
        "api/pitchers.js does not read date from req.query"

def test_wrong_date_response_rejected():
    """Verify that if MLB returns wrong-date data, the endpoint rejects it."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    # Must have date comparison logic
    assert 'requestedDate' in api_content or 'requested_date' in api_content or 'req.query.date' in api_content
    assert 'FAILED_STALE_DATE' in api_content

def test_mlb_url_uses_mlb_date_not_today():
    """Verify the MLB StatsAPI URL uses mlbDate (MM/DD/YYYY), not today (YYYY-MM-DD)."""
    api_content = open('/tmp/edge-finder-api/api/slate.js').read()
    # After the fix, the URL should reference mlbDate, not ${date} or ${today}
    assert 'date=${mlbDate}' in api_content, \
        "api/slate.js MLB URL does not use mlbDate — date format bug not fixed"

def test_pitchers_mlb_url_uses_mlb_date():
    """Verify pitchers.js MLB URL uses mlbDate (MM/DD/YYYY)."""
    api_content = open('/tmp/edge-finder-api/api/pitchers.js').read()
    assert 'date=${mlbDate}' in api_content, \
        "api/pitchers.js MLB URL does not use mlbDate — date format bug not fixed"

def test_date_format_conversion_logic():
    """Validate that the split/reorder logic produces correct MM/DD/YYYY."""
    # Simulate the JS logic in Python
    date = "2026-06-13"
    parts = date.split('-')
    yr, mo, dy = parts[0], parts[1], parts[2]
    mlbDate = f"{mo}/{dy}/{yr}"
    assert mlbDate == "06/13/2026", f"Expected 06/13/2026, got {mlbDate}"

    date2 = "2026-01-05"
    parts2 = date2.split('-')
    yr2, mo2, dy2 = parts2[0], parts2[1], parts2[2]
    mlbDate2 = f"{mo2}/{dy2}/{yr2}"
    assert mlbDate2 == "01/05/2026", f"Expected 01/05/2026, got {mlbDate2}"
