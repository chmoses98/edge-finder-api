#!/usr/bin/env python3
"""
tests/edgelab/test_market_universe.py
=========================================
Fixture-based coverage for lib/edgelab/market_universe.py: full eligible
market capture, no forbidden market leakage, dedup, immutable history.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage
from lib.edgelab.market_universe import (
    backfill_missing_game_pks,
    build_game_records,
    build_market_records,
    build_observations_from_snapshot,
    new_unclassified_series_warnings,
    select_observations_for_retention,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")


def _build():
    return build_observations_from_snapshot(FIXTURE, run_id="TEST_RUN_1", game_context={})


def test_full_eligible_market_capture():
    observations, excluded = _build()
    # Every legitimate market in the fixture, PLUS the one genuinely
    # unclassified-but-MLB-prefixed market (Market Research Corpus
    # milestone: "including currently unclassified markets") -- only the
    # confirmed futures/award/non-MLB-competition market stays excluded.
    assert len(observations) == 31
    families = {o["marketFamily"] for o in observations}
    assert "game_result" in families
    assert "winning_margin" in families
    assert "hitter_hits" in families


def test_no_forbidden_market_leakage():
    observations, excluded = _build()
    tickers = {o["marketTicker"] for o in observations}
    assert "KXMLBALCY-26-JVERLANDER" not in tickers
    reasons = {e["exclusionReason"] for e in excluded}
    assert "FUTURES_OR_AWARD" in reasons


def test_unclassified_mlb_series_archived_but_never_production_eligible():
    """
    Market Research Corpus milestone: a brand-new KXMLB*-prefixed series
    with no allowlist entry is now ARCHIVED into the research corpus
    (registryClassificationStatus=UNCLASSIFIED_MLB, validationStatus=
    warning) rather than dropped outright -- but the production-facing
    gate (classify_series_for_price_check, used by the standalone price
    checker and the slate pipeline) is completely untouched: this test
    only asserts the observation-corpus behavior changed, not that
    anything downstream now trusts this market.
    """
    observations, excluded = _build()
    unclassified = [o for o in observations if o["marketTicker"] == "KXMLBNEWFAM-26JUL311810PITCIN-PIT"]
    assert len(unclassified) == 1
    obs = unclassified[0]
    assert obs["registryClassificationStatus"] == "UNCLASSIFIED_MLB"
    assert obs["validationStatus"] == "warning"
    assert schema.validate_record("market_observation", obs) == []

    # Still never silently missing from the "needs human review" telemetry,
    # even though it's no longer in `excluded` now that it's archived.
    warnings = new_unclassified_series_warnings(observations, excluded)
    assert any(w["seriesTicker"] == "KXMLBNEWFAM" for w in warnings)

    # And a CLASSIFIED market never gets this flag.
    classified = [o for o in observations if o["marketTicker"] != "KXMLBNEWFAM-26JUL311810PITCIN-PIT"]
    assert all(o["registryClassificationStatus"] == "CLASSIFIED" for o in classified)


def test_every_observation_is_schema_valid():
    observations, _ = _build()
    for obs in observations:
        errors = schema.validate_record("market_observation", obs)
        assert errors == [], errors


def test_deterministic_ids_enable_dedup_across_reruns(tmp_path):
    observations, _ = _build()
    path = str(tmp_path / "observations.jsonl")
    written1, skipped1 = storage.append_records(path, observations, "marketObservationId")
    assert written1 == len(observations)
    assert skipped1 == 0

    # Re-running ingestion against the exact same snapshot must be a pure no-op.
    observations_again, _ = _build()
    written2, skipped2 = storage.append_records(path, observations_again, "marketObservationId")
    assert written2 == 0
    assert skipped2 == len(observations)


def test_multiple_same_day_snapshots_preserve_time_series(tmp_path):
    """A later snapshot with a moved price must add a new row, never overwrite the earlier one."""
    observations, _ = _build()
    one_ticker_obs = [o for o in observations if o["marketTicker"] == observations[0]["marketTicker"]]
    later = dict(one_ticker_obs[0])
    later["capturedAt"] = "2026-07-31T23:00:00.000Z"
    later["yesBid"] = (later["yesBid"] or 0) + 1
    from lib.edgelab.ids import build_market_observation_id
    later["marketObservationId"] = build_market_observation_id(later["marketTicker"], later["capturedAt"])

    path = str(tmp_path / "observations.jsonl")
    storage.append_records(path, [one_ticker_obs[0]], "marketObservationId")
    storage.append_records(path, [later], "marketObservationId")

    rows = [r for r in storage.read_records(path) if r["marketTicker"] == later["marketTicker"]]
    assert len(rows) == 2
    assert {r["capturedAt"] for r in rows} == {one_ticker_obs[0]["capturedAt"], later["capturedAt"]}


def test_raw_normalized_linkage():
    observations, _ = _build()
    for obs in observations:
        assert obs["provenance"]["sourceFile"] == FIXTURE
        assert obs["provenance"]["sourceKey"] == obs["marketTicker"]


def test_checkpoint_and_pregame_flags_wired_from_game_context():
    """Market Research Corpus milestone: checkpoint/gameStarted/pregame-validity are no longer hardcoded None."""
    game_context = {
        ("BOS", "LAD"): {"gameId": "9001", "scheduledStart": "2026-07-31T23:04:16.000Z", "status": "Scheduled", "venue": "Fenway", "kalshiKey": None},
    }
    already_seen = {"KXMLBGAME-26JUL312210BOSLAD-LAD", "KXMLBGAME-26JUL312210BOSLAD-BOS"}
    observations, _ = build_observations_from_snapshot(
        FIXTURE, run_id="TEST_RUN_1", game_context=game_context,
        existing_tickers_seen_today=already_seen, github_run_id="RUN123", commit_sha="deadbeef",
    )
    bos_lad = [o for o in observations if o["marketTicker"] in already_seen]
    assert len(bos_lad) == 2
    for obs in bos_lad:
        assert obs["checkpoint"] == "T_MINUS_30"  # 30 min before the 23:04:16 scheduled start
        assert obs["gameStartedAtCapture"] is False
        assert obs["isValidPregameObservation"] is True
        assert obs["isClosingCandidate"] is True
        assert obs["githubRunId"] == "RUN123"
        assert obs["commitSha"] == "deadbeef"


def test_first_observation_of_a_ticker_today_is_first_daily():
    observations, _ = build_observations_from_snapshot(FIXTURE, run_id="TEST_RUN_1", game_context={}, existing_tickers_seen_today=set())
    assert all(o["checkpoint"] == "FIRST_DAILY" for o in observations)  # nothing seen before this call


def test_post_start_observation_flagged_and_invalid_pregame():
    game_context = {
        ("BOS", "LAD"): {"gameId": "9001", "scheduledStart": "2026-07-31T22:00:00.000Z", "status": "InProgress", "venue": "Fenway", "kalshiKey": None},
    }
    already_seen = {"KXMLBGAME-26JUL312210BOSLAD-LAD"}
    observations, _ = build_observations_from_snapshot(
        FIXTURE, run_id="TEST_RUN_1", game_context=game_context, existing_tickers_seen_today=already_seen,
    )
    obs = next(o for o in observations if o["marketTicker"] == "KXMLBGAME-26JUL312210BOSLAD-LAD")
    assert obs["checkpoint"] == "POST_START"  # scheduled 22:00, captured 22:34:16
    assert obs["gameStartedAtCapture"] is True
    assert obs["isValidPregameObservation"] is False
    assert obs["isClosingCandidate"] is False


def test_checkpoint_flags_null_when_scheduled_start_unknown():
    """Never guessed -- absent scheduledStart means gameStarted/isValidPregame stay null, not a fabricated default."""
    observations, _ = build_observations_from_snapshot(FIXTURE, run_id="TEST_RUN_1", game_context={}, existing_tickers_seen_today={o for o in []})
    # Nothing in game_context, so every market falls back to no scheduled_start.
    non_first = [o for o in observations]  # all FIRST_DAILY here, but flags should still be null since scheduledStart is None
    for obs in non_first:
        assert obs["scheduledStart"] is None
        assert obs["gameStartedAtCapture"] is None
        assert obs["isValidPregameObservation"] is None


def test_retention_filter_drops_unchanged_repeat_but_keeps_real_changes():
    observations, _ = _build()
    first_tick = observations[:5]
    # Round 1: nothing retained yet -- everything is new.
    retained_1 = select_observations_for_retention(first_tick, previous_by_ticker={})
    assert retained_1 == first_tick

    previous_by_ticker = {o["marketTicker"]: o for o in retained_1}

    # Round 2: identical repeat tick (same prices/status, just a later capturedAt/id) -- dropped.
    repeat_tick = []
    for o in first_tick:
        r = dict(o)
        r["capturedAt"] = "2026-07-31T23:00:00.000Z"
        r["checkpoint"] = "INTERMEDIATE"
        repeat_tick.append(r)
    retained_2 = select_observations_for_retention(repeat_tick, previous_by_ticker=previous_by_ticker)
    assert retained_2 == []

    # Round 3: one ticker's price moved -- that one (and only that one) is retained.
    changed_tick = []
    for o in first_tick:
        r = dict(o)
        r["capturedAt"] = "2026-07-31T23:30:00.000Z"
        r["checkpoint"] = "INTERMEDIATE"
        changed_tick.append(r)
    changed_tick[0]["yesBid"] = (changed_tick[0]["yesBid"] or 0) + 1
    retained_3 = select_observations_for_retention(changed_tick, previous_by_ticker=previous_by_ticker)
    assert len(retained_3) == 1
    assert retained_3[0]["marketTicker"] == changed_tick[0]["marketTicker"]


def test_retention_filter_always_keeps_named_checkpoints_even_if_unchanged():
    observations, _ = _build()
    one = dict(observations[0])
    previous_by_ticker = {one["marketTicker"]: one}
    named = dict(one)
    named["capturedAt"] = "2026-07-31T23:04:16.000Z"
    named["checkpoint"] = "T_MINUS_30"  # unchanged price, but a named research checkpoint
    retained = select_observations_for_retention([named], previous_by_ticker=previous_by_ticker)
    assert retained == [named]


def test_retention_filter_keeps_the_pregame_to_post_start_transition():
    observations, _ = _build()
    pregame = dict(observations[0])
    pregame["gameStartedAtCapture"] = False
    previous_by_ticker = {pregame["marketTicker"]: pregame}
    post_start = dict(pregame)
    post_start["capturedAt"] = "2026-07-31T23:10:00.000Z"
    post_start["checkpoint"] = "POST_START"
    post_start["gameStartedAtCapture"] = True
    retained = select_observations_for_retention([post_start], previous_by_ticker=previous_by_ticker)
    assert retained == [post_start]


def test_game_and_market_dimension_records_dedup_by_key():
    observations, _ = _build()
    games = build_game_records(observations, {})
    markets = build_market_records(observations)
    assert len(markets) == len(observations)  # every fixture market is a distinct ticker
    game_ids = [g["gameId"] for g in games]
    assert len(game_ids) == len(set(game_ids))
    for m in markets:
        assert schema.validate_record("market", m) == []
    for g in games:
        assert schema.validate_record("game", g) == []


# ---------------------------------------------------------------------------
# backfill_missing_game_pks -- root-cause fix for the real Aug 5 2026 case:
# an early-starting game's Kalshi markets stop being freshly captured
# before that day's slate (data/pipeline/<date>/normalized_slate.json)
# exists, so its Game row is created with mlbGamePk=null and, since
# storage.upsert_records only ever replaces a row sharing its exact
# gameId, nothing ever revisits it even once the slate becomes available.
# ---------------------------------------------------------------------------

def _stuck_game(game_id="2026-08-05_TOR_HOU_1410", away="TOR", home="HOU"):
    return {
        "schemaVersion": "1", "gameId": game_id, "sport": "MLB", "platform": "KALSHI",
        "mlbGamePk": None, "gameDate": "2026-08-05", "scheduledStartTime": None,
        "actualStartTime": None, "awayTeam": away, "homeTeam": home, "venue": None,
        "status": None, "doubleheaderGameNumber": None, "kalshiKey": None,
        "createdAt": "2026-08-05T19:57:19Z", "updatedAt": None, "source": "kalshi_registry_snapshots",
        "validationStatus": "warning",
        "provenance": {"sourceSystem": "kalshi_registry_snapshots", "sourceFile": "x.json", "sourceKey": game_id, "capturedAt": "2026-08-05T19:57:19Z", "ingestedAt": "2026-08-05T19:57:19Z"},
    }


def test_backfill_fills_null_mlbGamePk_from_an_exact_date_away_home_match():
    game = _stuck_game()
    game_context = {("TOR", "HOU"): {"gameId": "824158", "scheduledStart": "2026-08-05T18:10:00Z", "status": "Final", "venue": "Daikin Park", "kalshiKey": "TORHOU"}}

    updated = backfill_missing_game_pks([game], game_context, now="2026-08-08T00:00:00Z")

    assert len(updated) == 1
    fixed = updated[0]
    assert fixed["gameId"] == "2026-08-05_TOR_HOU_1410"  # never renamed -- markets/bets/settlements already reference it
    assert fixed["mlbGamePk"] == "824158"
    assert fixed["venue"] == "Daikin Park"
    assert fixed["status"] == "Final"
    assert fixed["kalshiKey"] == "TORHOU"
    assert fixed["validationStatus"] == "valid"
    assert fixed["createdAt"] == game["createdAt"]  # original provenance preserved
    assert fixed["mlbGamePkBackfill"]["method"] == "DATE_AWAY_HOME_UNIQUE_MATCH"
    assert fixed["mlbGamePkBackfill"]["backfilledAt"] == "2026-08-08T00:00:00Z"
    assert schema.validate_record("game", fixed) == []


def test_backfill_never_touches_a_row_that_already_has_mlbGamePk():
    game = dict(_stuck_game(), mlbGamePk="999999")
    game_context = {("TOR", "HOU"): {"gameId": "824158", "scheduledStart": None, "status": "Final", "venue": None, "kalshiKey": None}}
    assert backfill_missing_game_pks([game], game_context) == []


def test_backfill_never_guesses_when_no_exact_match_exists():
    """A genuinely unresolvable game (truly absent from the slate) is left exactly as before -- never fuzzy-matched, never touched."""
    game = _stuck_game(away="TOR", home="HOU")
    game_context = {("SF", "TEX"): {"gameId": "822866", "scheduledStart": None, "status": "Final", "venue": None, "kalshiKey": None}}
    assert backfill_missing_game_pks([game], game_context) == []


def test_backfill_requires_a_unique_away_home_pair_match_not_partial():
    """Only an exact (away, home) tuple match counts -- a same-away-different-home game is never conflated with it."""
    game = _stuck_game(away="TOR", home="HOU")
    game_context = {("TOR", "BOS"): {"gameId": "999888", "scheduledStart": None, "status": "Final", "venue": None, "kalshiKey": None}}
    assert backfill_missing_game_pks([game], game_context) == []
