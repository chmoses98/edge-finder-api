#!/usr/bin/env python3
"""
tests/edgelab/test_observation_linkage.py
=============================================
Bet-to-Observation Linkage milestone: correct ticker selects the latest
valid pregame observation, a post-start observation is never chosen when
a valid pregame one exists, no observation timestamp is ever
misrepresented as a placement timestamp, and a ticker with no valid
pregame observation is left explicitly unlinked.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.observation_linkage import (
    build_linkage_field,
    link_bet_to_observation,
    load_observations_for_ticker,
    select_linked_observation,
)

TICKER = "KXMLBF5-TEST-DET"


def _obs(**overrides):
    base = {
        "marketObservationId": "obsA", "runId": "RUN1", "marketTicker": TICKER,
        "capturedAt": "2026-08-03T22:00:00Z", "yesAsk": 0.55, "noAsk": 0.45,
        "isValidPregameObservation": True, "source": "kalshi_registry_snapshots",
    }
    base.update(overrides)
    return base


def test_latest_valid_pregame_observation_is_selected():
    early = _obs(marketObservationId="early", capturedAt="2026-08-03T20:00:00Z", yesAsk=0.50)
    late = _obs(marketObservationId="late", capturedAt="2026-08-03T22:00:00Z", yesAsk=0.55)
    observation, method, reason = select_linked_observation([early, late])
    assert observation["marketObservationId"] == "late"
    assert method == "EXACT_TICKER_PREGAME_LATEST"
    assert reason is None


def test_post_start_observation_never_chosen_when_pregame_exists():
    pregame = _obs(marketObservationId="pregame", capturedAt="2026-08-03T22:00:00Z", isValidPregameObservation=True)
    post_start = _obs(marketObservationId="post", capturedAt="2026-08-03T23:00:00Z", isValidPregameObservation=False)
    observation, method, reason = select_linked_observation([pregame, post_start])
    assert observation["marketObservationId"] == "pregame"


def test_no_valid_observation_yields_explicit_unlinked_not_a_guess():
    post_start_only = _obs(isValidPregameObservation=False)
    observation, method, reason = select_linked_observation([post_start_only])
    assert observation is None
    assert method is None
    assert reason == "no_valid_pregame_observation_for_ticker"

    linkage = build_linkage_field([post_start_only])
    assert linkage["linkageStatus"] == "UNLINKED"
    assert linkage["observationId"] is None
    assert linkage["unavailableReason"] == "no_valid_pregame_observation_for_ticker"


def test_standalone_capture_preferred_at_the_same_latest_moment():
    automated = _obs(marketObservationId="auto", capturedAt="2026-08-03T22:00:00Z", source="kalshi_registry_snapshots")
    standalone = _obs(marketObservationId="manual", capturedAt="2026-08-03T22:00:00Z", source="standalone_price_check")
    linkage = build_linkage_field([automated, standalone])
    assert linkage["linkageMethod"] == "EXACT_TICKER_STANDALONE_CHECK"
    assert linkage["linkageConfidence"] == "HIGH"


def test_linkage_never_claims_observation_time_is_placement_time():
    """The linkage dict only ever exposes 'observedAt' -- callers must never copy this into entryTimestamp."""
    obs = _obs()
    linkage = build_linkage_field([obs])
    assert linkage["observedAt"] == obs["capturedAt"]
    assert "entryTimestamp" not in linkage
    assert "placedAt" not in linkage


def test_side_no_uses_no_ask_price():
    obs = _obs(yesAsk=0.55, noAsk=0.48)
    linkage = build_linkage_field([obs], side="NO")
    assert linkage["observedPrice"] == 0.48


def test_load_observations_for_ticker_reads_only_matching_ticker(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "EDGELAB_ROOT", str(tmp_path))
    path = storage.partition_path("observations", "2026-08-03", compressed=True)
    storage.append_records(path, [_obs(marketObservationId="mine"), _obs(marketObservationId="other", marketTicker="OTHER-TICKER")], "marketObservationId")
    rows = load_observations_for_ticker(TICKER, ["2026-08-03"], storage_module=storage)
    assert len(rows) == 1
    assert rows[0]["marketObservationId"] == "mine"


def test_link_bet_to_observation_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "EDGELAB_ROOT", str(tmp_path))
    path = storage.partition_path("observations", "2026-08-03", compressed=True)
    storage.append_records(path, [_obs()], "marketObservationId")
    linkage = link_bet_to_observation(TICKER, "2026-08-03", side="YES")
    assert linkage["linkageStatus"] == "LINKED"
    assert linkage["observedPrice"] == 0.55


def test_link_bet_to_observation_missing_partition_is_unlinked_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "EDGELAB_ROOT", str(tmp_path))
    linkage = link_bet_to_observation(TICKER, "2026-08-03", side="YES")
    assert linkage["linkageStatus"] == "UNLINKED"
