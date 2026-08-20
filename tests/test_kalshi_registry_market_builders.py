#!/usr/bin/env python3
"""
tests/test_kalshi_registry_market_builders.py
=================================================
Market-Universe Parity mission: unit coverage for
lib/kalshi_registry_market_builders.py — the F3/F7 three-way builder
and the per-player prop-ladder builder that
scripts/build_kalshi_registry.py's SERIES_CATALOGUE previously never
reached (see that dict's docstring for the root-cause history: these
series were fetched/archived by api/kalshisearch.js's ALL_SERIES all
along, but build_kalshi_registry.py's own fixed 8-series allowlist
never read them into the per-game registry that scripts/merge_odds.py
and, ultimately, data/slate.json are built from).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.kalshi_registry_market_builders import (  # noqa: E402
    build_three_way_period_market, build_player_prop_ladders, price_block,
)
from lib.kalshi_mlb_contract_parser import parse_contract  # noqa: E402
from lib.research.player_prop_parser import parse_player_prop_market  # noqa: E402


def _mkt(ticker, event_ticker, title=None, yes_bid=0.40, yes_ask=0.45, status='active'):
    return {
        'ticker': ticker, 'event_ticker': event_ticker, 'title': title,
        'yes_bid': yes_bid, 'yes_ask': yes_ask, 'status': status,
    }


class TestBuildThreeWayPeriodMarket:

    def test_f3_style_three_way_prices_all_three_legs(self):
        mkts = [
            _mkt('KXMLBF3-26JUL302140BOSATH-BOS', 'KXMLBF3-26JUL302140BOSATH', yes_bid=0.30, yes_ask=0.32),
            _mkt('KXMLBF3-26JUL302140BOSATH-ATH', 'KXMLBF3-26JUL302140BOSATH', yes_bid=0.55, yes_ask=0.57),
            _mkt('KXMLBF3-26JUL302140BOSATH-TIE', 'KXMLBF3-26JUL302140BOSATH', yes_bid=0.10, yes_ask=0.12),
        ]
        result = build_three_way_period_market('KXMLBF3', mkts, 'BOS', 'ATH')
        assert result['series'] == 'KXMLBF3'
        assert result['away_ticker'] == 'KXMLBF3-26JUL302140BOSATH-BOS'
        assert result['home_ticker'] == 'KXMLBF3-26JUL302140BOSATH-ATH'
        assert result['tie_ticker'] == 'KXMLBF3-26JUL302140BOSATH-TIE'
        assert result['prices']['away']['yes_bid'] == 0.30
        assert result['prices']['home']['yes_bid'] == 0.55
        assert result['prices']['tie']['yes_bid'] == 0.10
        assert result['researchOnly'] is True

    def test_f7_missing_tie_leg_still_returns_away_home(self):
        """A period with no tradable tie contract yet (e.g. thin market) must
        still expose away/home — never drop the whole entry for one missing leg."""
        mkts = [
            _mkt('KXMLBF7-26JUL302140BOSATH-BOS', 'KXMLBF7-26JUL302140BOSATH'),
            _mkt('KXMLBF7-26JUL302140BOSATH-ATH', 'KXMLBF7-26JUL302140BOSATH'),
        ]
        result = build_three_way_period_market('KXMLBF7', mkts, 'BOS', 'ATH')
        assert result['away_ticker'] is not None
        assert result['home_ticker'] is not None
        assert result['tie_ticker'] is None
        assert result['prices']['tie'] is None

    def test_no_markets_returns_none(self):
        assert build_three_way_period_market('KXMLBF3', [], 'BOS', 'ATH') is None


class TestBuildPlayerPropLadders:

    def test_real_strikeout_ladder_groups_by_player_sorted_by_threshold(self):
        """Real ticker/title shapes (KXMLBKS), verified against this repo's
        own archive per lib/research/player_prop_parser.py's docstring."""
        mkts = [
            _mkt('KXMLBKS-26JUL302140BOSATH-ATHGRAY54-8', 'KXMLBKS-26JUL302140BOSATH',
                 'Sonny Gray: 8+ strikeouts?', yes_bid=0.20, yes_ask=0.24),
            _mkt('KXMLBKS-26JUL302140BOSATH-ATHGRAY54-6', 'KXMLBKS-26JUL302140BOSATH',
                 'Sonny Gray: 6+ strikeouts?', yes_bid=0.55, yes_ask=0.59),
            _mkt('KXMLBKS-26JUL302140BOSATH-ATHGRAY54-4', 'KXMLBKS-26JUL302140BOSATH',
                 'Sonny Gray: 4+ strikeouts?', yes_bid=0.85, yes_ask=0.88),
        ]
        result = build_player_prop_ladders(
            'KXMLBKS', mkts, 'BOS', 'ATH', parse_contract, parse_player_prop_market
        )
        assert result['series'] == 'KXMLBKS'
        assert result['family'] == 'pitcher_strikeouts'
        assert result['researchOnly'] is True
        assert result['unparseableCount'] == 0
        assert len(result['players']) == 1
        (player,) = result['players'].values()
        assert player['displayName'] == 'Sonny Gray'
        assert player['team'] == 'ATH'
        thresholds = [t['threshold'] for t in player['thresholds']]
        assert thresholds == [4, 6, 8], "ladder must be sorted ascending by threshold"
        # Monotonic decreasing YES price as the threshold rises (harder to clear).
        yes_bids = [t['yes_bid'] for t in player['thresholds']]
        assert yes_bids == sorted(yes_bids, reverse=True)

    def test_two_distinct_players_are_not_merged(self):
        mkts = [
            _mkt('KXMLBHIT-26JUL302140BOSATH-BOSDEVERS11-1', 'KXMLBHIT-26JUL302140BOSATH',
                 'Rafael Devers: 1+ hits?'),
            _mkt('KXMLBHIT-26JUL302140BOSATH-ATHLANGELIERS17-1', 'KXMLBHIT-26JUL302140BOSATH',
                 'Shea Langeliers: 1+ hits?'),
        ]
        result = build_player_prop_ladders(
            'KXMLBHIT', mkts, 'BOS', 'ATH', parse_contract, parse_player_prop_market
        )
        assert len(result['players']) == 2
        names = {p['displayName'] for p in result['players'].values()}
        assert names == {'Rafael Devers', 'Shea Langeliers'}

    def test_unparseable_market_counted_not_dropped_silently(self):
        """A structurally broken ticker (wrong event prefix) must be counted,
        not silently vanish with no trace it was ever seen."""
        mkts = [
            _mkt('KXMLBHIT-WRONGPREFIX-BOSDEVERS11-1', 'KXMLBHIT-26JUL302140BOSATH',
                 'Rafael Devers: 1+ hits?'),
        ]
        result = build_player_prop_ladders(
            'KXMLBHIT', mkts, 'BOS', 'ATH', parse_contract, parse_player_prop_market
        )
        assert result['unparseableCount'] == 1
        assert result['players'] == {}

    def test_no_markets_at_all_returns_none(self):
        result = build_player_prop_ladders(
            'KXMLBHIT', [], 'BOS', 'ATH', parse_contract, parse_player_prop_market
        )
        assert result is None

    def test_family_mapping_correct_for_all_seven_series(self):
        from lib.kalshi_registry_market_builders import PLAYER_PROP_FAMILY
        assert PLAYER_PROP_FAMILY == {
            'KXMLBKS': 'pitcher_strikeouts',
            'KXMLBOUTS': 'pitcher_outs',
            'KXMLBHIT': 'hitter_hits',
            'KXMLBTB': 'hitter_total_bases',
            'KXMLBHRR': 'hitter_hits_runs_rbis',
            'KXMLBRBI': 'hitter_rbis',
            'KXMLBSB': 'hitter_stolen_bases',
        }


class TestPriceBlockMatchesScriptOwnCopy:
    """
    price_block()/norm()/american() here are deliberate small copies of
    scripts/build_kalshi_registry.py's own inline versions (see this
    module's docstring for why they aren't imported from that
    unconditionally-network-calling script). This locks in that the
    copy stays behaviorally identical.
    """

    def test_basic_bid_ask_mid(self):
        pb = price_block({'yes_bid': 0.40, 'yes_ask': 0.44, 'status': 'active'})
        assert pb['yes_bid'] == 0.40
        assert pb['yes_ask'] == 0.44
        assert pb['mid'] == 0.42
        assert pb['implied_pct'] == 42.0
        assert pb['status'] == 'active'

    def test_missing_prices_yield_none_mid(self):
        pb = price_block({'status': 'active'})
        assert pb['yes_bid'] is None
        assert pb['mid'] is None
        assert pb['american'] is None
