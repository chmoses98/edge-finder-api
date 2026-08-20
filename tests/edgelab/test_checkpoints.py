#!/usr/bin/env python3
"""
tests/edgelab/test_checkpoints.py
=====================================
Focused coverage for lib/edgelab/checkpoints.py's select_closing_quote()
-- the single canonical pregame-closing-quote selection rule shared by
lib.edgelab.research_dataset (research/calibration reports),
lib.edgelab.clv.finalize_closing_quotes (production CLV collection via
scripts/edgelab/collect_clv.py), and every downstream consumer of
isClosingQuote/researchCheckpoint="CLOSING".

Regression coverage for the fix documented in
data/edgelab/reports/market_price_calibration_audit.md: when NEITHER
scheduled_start NOR actual_start is resolved, select_closing_quote() must
return None (no candidate can be verified pre-start) rather than falling
back to "the chronologically last observation ever captured" -- the
fallback that previously let a post-start (even post-game) quote on a
ticker whose scheduledStart never resolved be misclassified as the
official pregame closing price.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.checkpoints import select_closing_quote


def _obs(captured_at, *, status="active", market_observation_id=None):
    return {
        "marketObservationId": market_observation_id or f"obs-{captured_at}",
        "capturedAt": captured_at,
        "marketStatus": status,
    }


def test_valid_pregame_closing_quote_still_qualifies():
    """Baseline happy path: a resolved scheduled_start with real pregame ticks still selects the last one strictly before start."""
    observations = [
        _obs("2026-08-14T05:26:45Z"),
        _obs("2026-08-14T06:52:41Z"),
        _obs("2026-08-14T18:55:00Z"),  # last tick, still before the 19:10 start
    ]
    closing = select_closing_quote(observations, scheduled_start="2026-08-14T19:10:00Z")
    assert closing is not None
    assert closing["marketObservationId"] == "obs-2026-08-14T18:55:00Z"


def test_valid_pregame_closing_quote_qualifies_via_actual_start_alone():
    """actual_start is preferred over scheduled_start and is on its own sufficient to verify pregame timing."""
    observations = [
        _obs("2026-08-14T18:00:00Z"),
        _obs("2026-08-14T19:05:00Z"),  # before the ACTUAL 19:12 first pitch
    ]
    closing = select_closing_quote(observations, scheduled_start=None, actual_start="2026-08-14T19:12:00Z")
    assert closing is not None
    assert closing["marketObservationId"] == "obs-2026-08-14T19:05:00Z"


def test_post_start_quote_never_becomes_closing_even_with_resolved_start():
    """A tick captured at/after the resolved start is never eligible, regardless of how 'good' its price looks."""
    observations = [
        _obs("2026-08-14T18:55:00Z"),  # last genuinely pregame tick
        _obs("2026-08-14T23:53:18Z"),  # captured hours after first pitch
    ]
    closing = select_closing_quote(observations, scheduled_start="2026-08-14T19:10:00Z")
    assert closing is not None
    assert closing["marketObservationId"] == "obs-2026-08-14T18:55:00Z"
    assert closing["marketObservationId"] != "obs-2026-08-14T23:53:18Z"


def test_post_start_quote_never_becomes_closing_with_no_earlier_candidate():
    """If every observation is at/after start, there is no valid closing quote at all -- never falls back to the least-bad post-start tick."""
    observations = [_obs("2026-08-14T20:00:00Z"), _obs("2026-08-14T23:53:18Z")]
    closing = select_closing_quote(observations, scheduled_start="2026-08-14T19:10:00Z")
    assert closing is None


def test_no_closing_quote_when_start_timing_entirely_unresolved():
    """
    THE regression this file exists for: KXMLBHRR-26AUG141910SDCLE-CLESKWAN38-5
    had scheduledStart/actualStart both unresolved, and its last-ever
    observation (23:53Z, hours after a ~19:10 first pitch) was previously
    selected as the "closing" quote purely because there was no start
    bound to compare it against. With no way to verify any tick is
    pre-start, the correct answer is "no closing quote", not "guess the
    last one" -- never silently invent a start time.
    """
    observations = [
        _obs("2026-08-14T05:26:45Z"),  # a genuinely early, sensible-looking tick
        _obs("2026-08-14T23:53:18Z"),  # the actual post-start tick that was misclassified before the fix
    ]
    closing = select_closing_quote(observations, scheduled_start=None, actual_start=None)
    assert closing is None


def test_no_closing_quote_with_single_observation_and_unresolved_start():
    """Even a single, otherwise-plausible observation is ineligible when timing can't be verified -- not just multi-tick histories."""
    observations = [_obs("2026-08-14T05:26:45Z")]
    assert select_closing_quote(observations, scheduled_start=None, actual_start=None) is None


def test_empty_observations_with_unresolved_start_returns_none_not_an_error():
    assert select_closing_quote([], scheduled_start=None, actual_start=None) is None


def test_suspended_quote_ineligible_even_when_start_resolved():
    """marketStatus gating is unaffected by this fix: a suspended tick is still never a candidate."""
    observations = [
        _obs("2026-08-14T18:00:00Z"),
        _obs("2026-08-14T18:55:00Z", status="suspended"),
    ]
    closing = select_closing_quote(observations, scheduled_start="2026-08-14T19:10:00Z")
    assert closing is not None
    assert closing["marketObservationId"] == "obs-2026-08-14T18:00:00Z"
