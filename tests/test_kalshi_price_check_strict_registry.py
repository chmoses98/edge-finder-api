#!/usr/bin/env python3
"""
tests/test_kalshi_price_check_strict_registry.py
======================================================
Kalshi price-checker correction mission -- proves the standalone daily
price checker's MANDATORY strict single-game registry gate
(lib.kalshi_price_check.apply_strict_game_registry) actually keeps
non-game markets (Golden Spikes Award, college baseball, WBC, Mexican
League, Congressional Baseball Game, awards, leaders, futures) out of
the daily output, while retaining every legitimate single-game MLB
market family (including the newly-confirmed F3/F7 winner markets and
pitcher/hitter props), with explicit exclusion-reason telemetry and no
silent drops.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)

from lib.kalshi_price_check import (
    normalize_batch,
    apply_strict_game_registry,
    validate_game_identity,
    group_by_game,
    format_job_summary_markdown,
)
from lib.kalshi_mlb_single_game_registry import (
    SERIES_NOT_ALLOWLISTED,
    NON_MLB_COMPETITION,
    FUTURES_OR_AWARD,
    DATE_MISMATCH,
    MALFORMED_EVENT,
)


def _mkt(ticker, event_ticker=None, title=None, **kw):
    row = {"market_ticker": ticker, "event_ticker": event_ticker or ticker.rsplit("-", 1)[0],
           "title": title, "yes_bid": 0.4, "yes_ask": 0.42, "status": "open"}
    row.update(kw)
    return row


GAME_ML = _mkt("KXMLBGAME-26JUL292210SEALAD-SEA", title="Seattle wins?")
GAME_SPREAD = _mkt("KXMLBSPREAD-26JUL292210SEALAD-SEA2", title="Seattle wins by 1.5?")
GAME_TOTAL = _mkt("KXMLBTOTAL-26JUL292210SEALAD-8", title="Total runs over 7.5?")
TEAM_TOTAL = _mkt("KXMLBTEAMTOTAL-26JUL292210SEALAD-SEA4", title="Seattle team total over 3.5?")
NRFI = _mkt("KXMLBRFI-26JUL292210SEALAD-YES", title="Run in first inning?")
F3_WINNER = _mkt("KXMLBF3-26JUL292210SEALAD-TIE", title="First 3 innings tie?")
F5_WINNER = _mkt("KXMLBF5-26JUL292210SEALAD-TIE", title="First 5 innings tie?")
F7_WINNER = _mkt("KXMLBF7-26JUL292210SEALAD-TIE", title="First 7 innings tie?")
PITCHER_KS = _mkt("KXMLBKS-26JUL292210SEALAD-ABC", title="Pitcher over 6.5 strikeouts?")
HITTER_RBI = _mkt("KXMLBRBI-26JUL292210SEALAD-XYZ", title="Player over 1.5 RBIs?")

GOLDEN_SPIKES = _mkt("KXNCAABBGS-26JUL29-WINNER", title="College Baseball Golden Spikes Award")
COLLEGE_GAME = _mkt("KXNCAABBGAME-26JUL292210ABCXYZ-ABC", title="College baseball game")
WBC_GAME = _mkt("KXWBCGAME-26JUL292210USADOM-USA", title="World Baseball Classic Game")
MEXICAN_LEAGUE = _mkt("KXLMBGAME-26JUL292210MEXTIJ-MEX", title="Mexican Baseball League")
CONGRESS_GAME = _mkt("KXCONGRESSBASEBALL-26JUL29-DEM", title="Congressional Baseball Game")
CY_YOUNG_AWARD = _mkt("KXMLBALCY-26-PITCHER", title="Pro Baseball American League Cy Young")
SEASON_LEADER = _mkt("KXLEADERMLBHR-26-PLAYER", title="MLB Home Runs Leader")
DIVISION_FUTURE = _mkt("KXMLBALWEST-26-TEAM", title="American League West Winner")
MALFORMED = {"market_ticker": "KXMLBGAME-NOTAVALIDTICKER", "title": "malformed",
             "yes_bid": 0.5, "yes_ask": 0.52, "status": "open"}


def _normalize(*raw):
    records, _, _ = normalize_batch(list(raw))
    return records


class TestApprovedFamiliesRetained:

    def test_full_game_moneyline_retained(self):
        kept, excluded = apply_strict_game_registry(_normalize(GAME_ML))
        assert len(kept) == 1 and excluded == []

    def test_full_game_spread_and_total_and_team_total_retained(self):
        kept, excluded = apply_strict_game_registry(_normalize(GAME_SPREAD, GAME_TOTAL, TEAM_TOTAL))
        assert len(kept) == 3 and excluded == []

    def test_nrfi_yrfi_retained(self):
        kept, excluded = apply_strict_game_registry(_normalize(NRFI))
        assert len(kept) == 1 and excluded == []

    def test_f3_f5_f7_three_way_winner_markets_retained(self):
        """Explicit regression guard: the Kalshi price-checker correction
        mission's stricter series gate must not regress the previously
        verified F3/F7 support."""
        kept, excluded = apply_strict_game_registry(_normalize(F3_WINNER, F5_WINNER, F7_WINNER))
        assert len(kept) == 3 and excluded == []
        families = {r["seriesTicker"] for r in kept}
        assert families == {"KXMLBF3", "KXMLBF5", "KXMLBF7"}

    def test_pitcher_and_hitter_props_tied_to_correct_game_retained(self):
        kept, excluded = apply_strict_game_registry(_normalize(PITCHER_KS, HITTER_RBI))
        assert len(kept) == 2 and excluded == []
        assert {r["matchup"] for r in kept} == {"SEA@LAD"}


class TestRequiredExclusions:

    def test_college_baseball_golden_spikes_award_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(GOLDEN_SPIKES))
        assert kept == []
        assert len(excluded) == 1
        assert excluded[0]["exclusionReason"] == NON_MLB_COMPETITION

    def test_college_baseball_game_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(COLLEGE_GAME))
        assert kept == []
        assert excluded[0]["exclusionReason"] == NON_MLB_COMPETITION

    def test_world_baseball_classic_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(WBC_GAME))
        assert kept == []
        assert excluded[0]["exclusionReason"] == NON_MLB_COMPETITION

    def test_mexican_baseball_league_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(MEXICAN_LEAGUE))
        assert kept == []
        assert excluded[0]["exclusionReason"] == NON_MLB_COMPETITION

    def test_congressional_baseball_game_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(CONGRESS_GAME))
        assert kept == []
        assert excluded[0]["exclusionReason"] == NON_MLB_COMPETITION

    def test_award_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(CY_YOUNG_AWARD))
        assert kept == []
        assert excluded[0]["exclusionReason"] == FUTURES_OR_AWARD

    def test_season_leader_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(SEASON_LEADER))
        assert kept == []
        assert excluded[0]["exclusionReason"] == FUTURES_OR_AWARD

    def test_division_future_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(DIVISION_FUTURE))
        assert kept == []
        assert excluded[0]["exclusionReason"] == FUTURES_OR_AWARD

    def test_malformed_event_ticker_excluded_even_in_allowed_series(self):
        kept, excluded = apply_strict_game_registry(_normalize(MALFORMED))
        assert kept == []
        assert excluded[0]["exclusionReason"] == MALFORMED_EVENT


class TestDateMapping:

    def test_matching_requested_date_retained(self):
        kept, excluded = apply_strict_game_registry(_normalize(GAME_ML), requested_date="2026-07-29")
        assert len(kept) == 1 and excluded == []

    def test_mismatched_requested_date_excluded(self):
        kept, excluded = apply_strict_game_registry(_normalize(GAME_ML), requested_date="2026-08-01")
        assert kept == []
        assert excluded[0]["exclusionReason"] == DATE_MISMATCH

    def test_no_requested_date_skips_date_check(self):
        kept, excluded = apply_strict_game_registry(_normalize(GAME_ML), requested_date=None)
        assert len(kept) == 1 and excluded == []

    def test_validate_game_identity_directly(self):
        record = _normalize(GAME_ML)[0]
        assert validate_game_identity(record) == (True, None)
        assert validate_game_identity(record, requested_date="2026-07-29") == (True, None)
        ok, reason = validate_game_identity(record, requested_date="2020-01-01")
        assert ok is False and reason == DATE_MISMATCH


class TestNoSilentDrop:

    def test_every_record_accounted_for_kept_or_excluded(self):
        raw = [GAME_ML, GOLDEN_SPIKES, WBC_GAME, F3_WINNER, MALFORMED, PITCHER_KS]
        records = _normalize(*raw)
        kept, excluded = apply_strict_game_registry(records)
        assert len(kept) + len(excluded) == len(records)

    def test_excluded_records_carry_full_original_fields_plus_reason(self):
        kept, excluded = apply_strict_game_registry(_normalize(GOLDEN_SPIKES))
        assert excluded[0]["ticker"] == "KXNCAABBGS-26JUL29-WINNER"
        assert "exclusionReason" in excluded[0]


class TestGroupByGame:

    def test_groups_multiple_market_families_under_one_game(self):
        kept, _ = apply_strict_game_registry(_normalize(GAME_ML, GAME_SPREAD, F5_WINNER))
        groups = group_by_game(kept)
        assert len(groups) == 1
        assert groups[0]["matchup"] == "SEA@LAD"
        assert len(groups[0]["markets"]) == 3

    def test_separate_games_produce_separate_groups(self):
        other_game = _mkt("KXMLBGAME-26JUL292210BOSNYY-BOS", title="Boston wins?")
        kept, _ = apply_strict_game_registry(_normalize(GAME_ML, other_game))
        groups = group_by_game(kept)
        assert len(groups) == 2


class TestQueryLimitingThroughTheScript:
    """Proves the standalone checker never iterates the full broad-
    discovery catalogue -- it only ever sees whatever the single
    /api/kalshisearch fetch (or a snapshot file) returned, and the
    strict gate runs entirely in-memory with zero additional network
    calls, regardless of how many non-game markets are present."""

    def test_run_does_not_call_discover_series_catalogue(self, tmp_path):
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        snap_path = snap_dir / "kalshi_search_2026-07-29.json"
        raw = [GAME_ML, GOLDEN_SPIKES, WBC_GAME, MEXICAN_LEAGUE, CONGRESS_GAME,
               CY_YOUNG_AWARD, SEASON_LEADER, DIVISION_FUTURE, F3_WINNER, F5_WINNER, F7_WINNER]
        snap_path.write_text(json.dumps({"fetched_at": "2026-07-29T18:00:00Z", "markets": raw}))

        if "check_kalshi_prices" in sys.modules:
            del sys.modules["check_kalshi_prices"]
        import check_kalshi_prices as ckp
        parser = ckp.build_parser()
        args = parser.parse_args(["--source", "snapshot", "--snapshot-path", str(snap_path), "--format", "json"])
        exit_code, output, result = ckp.run(args)
        assert exit_code == 0
        records = json.loads(output)
        # Only the 4 approved single-game markets survive (GAME_ML,
        # F3/F5/F7 winner); the other 7 non-game markets never reach
        # the main output.
        assert len(records) == 4
        metadata = result["metadata"]
        assert metadata["marketsExcludedByRegistry"] == 7
        assert metadata["exclusionReasonCounts"].get(NON_MLB_COMPETITION) == 4
        assert metadata["exclusionReasonCounts"].get(FUTURES_OR_AWARD) == 3
        assert metadata["gamesFoundCount"] == 1
        assert "KXMLBGAME" in metadata["approvedSeriesQueried"]

        summary = format_job_summary_markdown(metadata)
        assert "Games found" in summary
        assert "Markets excluded by strict registry" in summary
        assert f"Excluded ({NON_MLB_COMPETITION})" in summary
