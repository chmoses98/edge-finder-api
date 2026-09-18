#!/usr/bin/env python3
"""
tests/test_betting_eligibility.py
=====================================
Confirmed-lineup game eligibility, and the handicapping card built on it.

The two properties this file exists to pin:

  * a game without BOTH official lineups confirmed can NEVER reach the
    real-money card, however attractive its markets are;
  * a game that IS eligible gets EVERY Kalshi market attributable to it,
    not just the 11 production-model rows.

Both are asserted against a real, committed production slate as well as
against constructed fixtures.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.betting_eligibility import (  # noqa: E402
    BLOCKED_DATA,
    BLOCKED_LINEUPS,
    BLOCKED_POSTPONED,
    BLOCKED_STARTED,
    ELIGIBLE,
    LINEUP_FIELD,
    classify_game_eligibility,
    lineup_confirmation,
    partition_slate,
)

_spec = importlib.util.spec_from_file_location(
    "build_handicapping_card", os.path.join(ROOT, "scripts", "build_handicapping_card.py"))
card_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(card_mod)

# A real, committed production slate: SD@COL pre-game with both official
# lineups confirmed, three games already started, five pre-game with
# lineups still unconfirmed. Exactly the mixed slate the review asked for.
REAL_MIXED_SLATE = os.path.join(ROOT, "data", "slates", "2026-09-17",
                                "recheck_20260917T183619Z.json")
REAL_UNIVERSE = os.path.join(ROOT, "data", "kalshi_registry_snapshots",
                             "kalshi_search_2026-09-17_1941.json")


def _game(away="SD", home="COL", *, status="Pre-Game", away_official=True, home_official=True,
          game_id=824305, start="2026-09-17T19:10:00Z", include_field=True):
    away_stats = {"lineupConfirmed": True}
    home_stats = {"lineupConfirmed": True}
    if include_field:
        away_stats[LINEUP_FIELD] = away_official
        home_stats[LINEUP_FIELD] = home_official
    return {
        "gameId": game_id, "status": status, "startTime": start,
        "away": {"abbr": away}, "home": {"abbr": home},
        "awayTeamStats": away_stats, "homeTeamStats": home_stats,
    }


NOW = "2026-09-17T18:40:00Z"


# ── the gate ────────────────────────────────────────────────────────────

def test_unstarted_game_with_both_official_lineups_is_eligible():
    verdict = classify_game_eligibility(_game(), now_utc=NOW)
    assert verdict["status"] == ELIGIBLE
    assert verdict["eligible"] is True
    assert verdict["lineups"]["bothConfirmed"] is True


@pytest.mark.parametrize("away_official,home_official", [(False, True), (True, False), (False, False)])
def test_either_unconfirmed_lineup_blocks_real_money(away_official, home_official):
    verdict = classify_game_eligibility(
        _game(away_official=away_official, home_official=home_official), now_utc=NOW)
    assert verdict["status"] == BLOCKED_LINEUPS
    assert verdict["eligible"] is False
    assert "BOTH official lineups" in verdict["reason"]


def test_a_probable_lineup_is_never_treated_as_confirmed():
    """
    The loose `lineupConfirmed` flag can be set by a partial/probable
    parse. Only `lineupConfirmedOfficial` counts.
    """
    game = _game(away_official=False, home_official=False)
    game["awayTeamStats"]["lineupConfirmed"] = True
    game["homeTeamStats"]["lineupConfirmed"] = True
    verdict = classify_game_eligibility(game, now_utc=NOW)
    assert verdict["status"] == BLOCKED_LINEUPS
    assert verdict["lineups"]["away"]["looseLineupConfirmed"] is True
    assert verdict["lineups"]["away"]["lineupConfirmedOfficial"] is False


def test_a_missing_lineup_field_fails_closed_and_says_so():
    verdict = classify_game_eligibility(_game(include_field=False), now_utc=NOW)
    assert verdict["status"] == BLOCKED_LINEUPS
    assert verdict["lineups"]["away"]["fieldPresent"] is False
    assert "field absent" in verdict["lineups"]["away"]["reason"]


@pytest.mark.parametrize("status", ["In Progress", "Final", "Game Over"])
def test_a_started_game_is_blocked_even_with_confirmed_lineups(status):
    verdict = classify_game_eligibility(_game(status=status), now_utc=NOW)
    assert verdict["status"] == BLOCKED_STARTED
    assert verdict["eligible"] is False
    # The lineups WERE confirmed -- the two facts stay distinguishable.
    assert verdict["lineups"]["bothConfirmed"] is True


def test_first_pitch_timestamp_blocks_a_stale_pregame_status():
    """A slate fetched before first pitch can still read 'Pre-Game' hours
    later. The timestamp fallback (reused from postponed_guard) catches it."""
    verdict = classify_game_eligibility(
        _game(status="Pre-Game", start="2026-09-17T16:35:00Z"), now_utc="2026-09-17T20:00:00Z")
    assert verdict["status"] == BLOCKED_STARTED
    assert verdict["gameStatus"]["timestampBlocked"] is True


def test_a_postponed_game_is_reported_as_postponed_not_as_unconfirmed():
    verdict = classify_game_eligibility(_game(status="Postponed"), now_utc=NOW)
    assert verdict["status"] == BLOCKED_POSTPONED


def test_a_game_without_a_gameid_cannot_be_bet():
    verdict = classify_game_eligibility(_game(game_id=None), now_utc=NOW)
    assert verdict["status"] == BLOCKED_DATA
    assert "cannot be attributed" in verdict["reason"]


def test_lineup_confirmation_names_the_unconfirmed_sides():
    lineups = lineup_confirmation(_game(away_official=False))
    assert lineups["unconfirmedSides"] == ["away"]
    assert lineups["home"]["lineupConfirmedOfficial"] is True


# ── partitioning ────────────────────────────────────────────────────────

def test_partition_keeps_every_game_and_explains_every_exclusion():
    slate = {"date": "2026-09-17", "games": [
        _game("SD", "COL"),
        _game("KC", "HOU", away_official=False, game_id=824141),
        _game("MIL", "PIT", status="In Progress", game_id=823334),
    ]}
    partition = partition_slate(slate, now_utc=NOW)
    assert partition["gamesTotal"] == 3
    assert partition["bettingEligibleCount"] == 1
    assert partition["researchOnlyCount"] == 2
    assert partition["researchOnlyByReason"] == {BLOCKED_LINEUPS: 1, BLOCKED_STARTED: 1}
    assert all(v["reason"] for v in partition["researchOnly"])


# ── the mixed-slate proof, on real committed production data ────────────

def _real_slate():
    if not os.path.exists(REAL_MIXED_SLATE):
        pytest.skip("real mixed-slate fixture not present in this checkout")
    with open(REAL_MIXED_SLATE) as f:
        payload = json.load(f)
    return card_mod.unwrap_pipeline_artifact(payload)


def test_real_production_slate_partitions_into_exactly_one_eligible_game():
    partition = partition_slate(_real_slate(), now_utc=NOW)
    eligible = [v["matchup"] for v in partition["bettingEligible"]]
    assert eligible == ["SD@COL"], f"expected only the confirmed-lineup game, got {eligible}"
    assert partition["researchOnlyByReason"] == {BLOCKED_LINEUPS: 5, BLOCKED_STARTED: 3}


def test_real_mixed_slate_only_the_confirmed_game_enters_the_real_money_card():
    """
    The full behavioural proof, on real production data:
      * both confirmed and unconfirmed games stay archived and visible;
      * ONLY the confirmed-lineup game is real-money eligible;
      * that game keeps EVERY available market for comparison.
    """
    if not os.path.exists(REAL_UNIVERSE):
        pytest.skip("real universe snapshot not present in this checkout")
    with open(REAL_UNIVERSE) as f:
        raw = card_mod.extract_markets(json.load(f))
    from lib.kalshi_price_check import apply_strict_game_registry, normalize_batch
    records, _counts, _malformed = normalize_batch(raw, source_mode="snapshot", source_used="snapshot")
    validated, _excluded = apply_strict_game_registry(records, requested_date="2026-09-17")

    card = card_mod.build_card(
        date="2026-09-17", slate=_real_slate(), slate_source=REAL_MIXED_SLATE,
        records=validated, bankroll={"status": "STALE", "sizingAllowed": False}, now_utc=NOW,
    )

    # 1. exactly one real-money game
    assert [g["matchup"] for g in card["bettingEligibleGames"]] == ["SD@COL"]

    # 2. every other game is STILL PRESENT, with markets, just not executable
    research = {g["matchup"]: g for g in card["researchOnlyGames"]}
    assert len(research) == 8
    assert sum(g["marketCount"] for g in research.values()) > 0, (
        "excluded games must remain visible in full market coverage"
    )
    for game in research.values():
        assert game["realMoneyEligible"] is False
        assert all(m["realMoneyEligible"] is False for m in game["markets"]), (
            "a research-only game's markets leaked into the executable card"
        )
        assert game["eligibilityReason"]

    # 3. the eligible game keeps the COMPLETE market universe for itself --
    #    far more than the 11 production-model rows
    sd = card["bettingEligibleGames"][0]
    assert sd["marketCount"] == 361
    assert len(sd["marketsByFamily"]) == 13
    assert all(m["realMoneyEligible"] is True for m in sd["markets"])

    # 4. families with no production adapter are present and labelled, not hidden
    no_adapter = [m for m in sd["markets"] if m["productionModelSupport"] == "NO_PRODUCTION_ADAPTER"]
    assert no_adapter, "expected prop/alternate families outside the 11-row production universe"
    assert {m["family"] for m in no_adapter} & {"hitter_hits", "pitcher_strikeouts", "inning_total"}

    # 5. no fabricated model probability anywhere
    assert all(m["modelProbability"] is None for m in sd["markets"])


def test_the_card_records_the_bankroll_it_would_size_against():
    card = card_mod.build_card(
        date="2026-09-17", slate={"date": "2026-09-17", "games": [_game()]},
        slate_source="fixture", records=[],
        bankroll={"status": "FRESH", "bankroll": 500.0, "sizingAllowed": True}, now_utc=NOW)
    assert card["bankroll"]["bankroll"] == 500.0
    assert card["bankroll"]["sizingAllowed"] is True


def test_the_card_states_all_five_axes():
    card = card_mod.build_card(
        date="2026-09-17", slate={"date": "2026-09-17", "games": []}, slate_source="fixture",
        records=[], bankroll=None, now_utc=NOW)
    assert set(card["axes"]) == {
        "GAME_ELIGIBILITY", "MARKET_AVAILABILITY", "PRODUCTION_MODEL_SUPPORT",
        "MANUAL_HANDICAPPING_ELIGIBILITY", "AUTOMATIC_SETTLEMENT_SUPPORT",
    }


def test_automatic_settlement_support_is_derived_from_the_canonical_settler():
    families = card_mod.auto_settleable_families()
    # Game families the settler dispatches on...
    assert {"game_result", "team_total", "game_total", "inning_result"} <= families
    # ...and the player-prop families it can grade.
    assert {"pitcher_strikeouts", "hitter_hits"} <= families


def test_production_model_series_comes_from_config_not_a_hardcoded_list():
    series = card_mod.production_model_series()
    assert {"KXMLBF5", "KXMLBGAME", "KXMLBTEAMTOTAL"} <= series
    # A prop series is deliberately NOT in the production universe -- that
    # is axis 3, and must not stop an analyst comparing it.
    assert "KXMLBSTRIKEOUTS" not in series
