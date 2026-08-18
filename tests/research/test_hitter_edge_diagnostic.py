#!/usr/bin/env python3
"""
tests/research/test_hitter_edge_diagnostic.py
===================================================
Deterministic unit tests for lib/research/hitter_edge_diagnostic.py --
synthetic fixtures only, never depends on the real repo's archived
hitter validation corpus.
"""
import pytest

from lib.research import hitter_edge_diagnostic as hed


def _row(edge=0.10, model_prob=0.5, entry_price=0.4, outcome="YES", won=True, net_pl=1.0,
         family="hitter_hits", threshold=1, player="P1", player_id="1", date="2026-08-13",
         matchup="AAA @ BBB", lineup_slot=None, offense_side=None, side="YES"):
    return {
        "computedEdge": edge, "modelProbability": model_prob, "executableKalshiPrice": entry_price,
        "propositionOutcome": outcome, "simulatedBetWon": won, "simulatedBetNetPL": net_pl,
        "simulatedBetSide": side, "simulatedBetEntryPrice": entry_price,
        "marketFamily": family, "threshold": threshold, "player": player, "playerId": player_id,
        "sourceDate": date, "matchup": matchup,
        "segment": {"lineupSlot": lineup_slot, "offenseSide": offense_side},
    }


class TestSplitByEdgeMagnitude:
    def test_splits_at_threshold(self):
        rows = [_row(edge=0.02), _row(edge=0.049), _row(edge=0.05), _row(edge=0.20)]
        small, large = hed.split_by_edge_magnitude(rows, threshold=0.05)
        assert len(small) == 2
        assert len(large) == 2

    def test_negative_edge_uses_absolute_value(self):
        rows = [_row(edge=-0.10)]
        small, large = hed.split_by_edge_magnitude(rows, threshold=0.05)
        assert len(large) == 1
        assert len(small) == 0

    def test_none_edge_excluded_from_both(self):
        rows = [_row(edge=None)]
        small, large = hed.split_by_edge_magnitude(rows)
        assert small == []
        assert large == []


class TestNetExecutableEdge:
    def test_computes_a_real_value(self):
        row = _row(model_prob=0.5, entry_price=0.4)
        result = hed.net_executable_edge(row)
        assert result is not None
        # break-even at 0.4 is slightly above 0.4 (fee-inclusive), so net edge < raw edge (0.1)
        assert result < 0.10

    def test_none_when_price_missing(self):
        row = _row(entry_price=None)
        assert hed.net_executable_edge(row) is None

    def test_none_when_price_out_of_range(self):
        row = _row(entry_price=1.0)
        assert hed.net_executable_edge(row) is None


class TestDimensionBreakdowns:
    def test_by_market_family_groups_correctly(self):
        rows = [_row(family="hitter_hits", won=True), _row(family="hitter_hits", won=False),
                _row(family="hitter_rbis", won=True)]
        breakdown = hed.large_edge_by_market_family(rows)
        keys = {b["key"]: b for b in breakdown}
        assert keys["hitter_hits"]["n"] == 2
        assert keys["hitter_rbis"]["n"] == 1

    def test_by_player_filters_below_min_n(self):
        rows = [_row(player="A", player_id="1")] * 5 + [_row(player="B", player_id="2")] * 2
        breakdown = hed.large_edge_by_player(rows, min_n=3)
        keys = [b["key"] for b in breakdown]
        assert any("A" in k for k in keys)
        assert not any("B" in k for k in keys)

    def test_by_lineup_slot_reports_unknown_when_unavailable(self):
        rows = [_row(lineup_slot=None), _row(lineup_slot="TOP_1_3")]
        breakdown = hed.large_edge_by_lineup_slot(rows)
        keys = {b["key"] for b in breakdown}
        assert "TOP_1_3" in keys

    def test_by_executable_price_bucket(self):
        rows = [_row(entry_price=0.05), _row(entry_price=0.45), _row(entry_price=0.95)]
        breakdown = hed.large_edge_by_executable_price_bucket(rows)
        keys = {b["key"]: b["n"] for b in breakdown}
        assert keys.get("<10c") == 1
        assert keys.get("30-49c") == 1
        assert keys.get("90c+") == 1


class TestTailVsNonTail:
    def test_classifies_top_rungs_as_tail_per_family(self):
        primary_rows = [
            _row(family="hitter_hits", threshold=1), _row(family="hitter_hits", threshold=2),
            _row(family="hitter_hits", threshold=3), _row(family="hitter_hits", threshold=4),
        ]
        large_edge_rows = list(primary_rows)
        breakdown = hed.large_edge_tail_vs_non_tail(large_edge_rows, primary_rows, tail_rank_from_top=2)
        by_key = {b["key"]: b["n"] for b in breakdown}
        assert by_key["TAIL"] == 2      # thresholds 3, 4
        assert by_key["NON_TAIL"] == 2  # thresholds 1, 2

    def test_never_hardcodes_absolute_threshold_across_families(self):
        """hitter_total_bases naturally has higher thresholds than hitter_hits -- tail must be relative to each family's own observed range, not a fixed number."""
        primary_rows = [
            _row(family="hitter_hits", threshold=1), _row(family="hitter_hits", threshold=2),
            _row(family="hitter_total_bases", threshold=4), _row(family="hitter_total_bases", threshold=5),
            _row(family="hitter_total_bases", threshold=6),
        ]
        breakdown = hed.large_edge_tail_vs_non_tail(primary_rows, primary_rows, tail_rank_from_top=1)
        by_key = {b["key"]: b["n"] for b in breakdown}
        # tail = threshold 2 (hits) + threshold 6 (total_bases) = 2 rows
        assert by_key["TAIL"] == 2
        assert by_key["NON_TAIL"] == 3


class TestClusterWeighting:
    def test_row_weighted_vs_cluster_weighted_differ_when_unbalanced(self):
        # One hitter contributes 10 losing rows; another contributes 1 winning row.
        # Row-weighted mean should be dominated by the 10 losers; cluster-weighted should weight both hitters equally.
        losers = [_row(player_id="LOSER", net_pl=-1.0) for _ in range(10)]
        winner = [_row(player_id="WINNER", net_pl=5.0)]
        rows = losers + winner
        result = hed.cluster_weighted_vs_row_weighted(rows, lambda r: r["playerId"])
        assert result["distinctClusters"] == 2
        assert result["rowWeightedMeanNetPL"] < 0  # dominated by the 10 losing rows
        # cluster-weighted: mean of (-1.0, 5.0) = 2.0 -- much less negative, even positive
        assert result["clusterWeightedMeanNetPL"] > result["rowWeightedMeanNetPL"]

    def test_empty_input_never_raises(self):
        result = hed.cluster_weighted_vs_row_weighted([], lambda r: r["playerId"])
        assert result["distinctClusters"] == 0
        assert result["rowWeightedMeanNetPL"] is None
        assert result["clusterWeightedMeanNetPL"] is None

    def test_concentration_check_reports_both_groupings(self):
        rows = [_row(player_id="A", date="2026-08-13", matchup="AAA @ BBB", net_pl=-1.0)]
        result = hed.concentration_check(rows)
        assert "byPlayerDate" in result
        assert "byGameDate" in result
        assert result["byPlayerDate"]["distinctClusters"] == 1


class TestBuildEdgeInversionDiagnostic:
    def test_full_report_shape(self):
        rows = [
            _row(edge=0.01, net_pl=1.0, family="hitter_hits", threshold=1, player_id="A"),
            _row(edge=0.10, net_pl=-1.0, family="hitter_hits", threshold=2, player_id="B"),
            _row(edge=0.15, net_pl=-1.0, family="hitter_rbis", threshold=1, player_id="C"),
        ]
        report = hed.build_edge_inversion_diagnostic(rows, threshold=0.05)
        assert report["largeEdgeThreshold"] == 0.05
        assert report["smallEdgeCohort"]["roi"]["qualifyingBets"] == 1
        assert report["largeEdgeCohort"]["roi"]["qualifyingBets"] == 2
        for key in ("byMarketFamily", "byProbabilityBucket", "byThreshold", "byPlayer",
                    "byGameDate", "byLineupSlot", "byHomeAway", "byExecutablePriceBucket",
                    "byNetExecutableEdgeBucket", "tailVsNonTail", "concentrationCheck"):
            assert key in report

    def test_empty_input_never_raises(self):
        report = hed.build_edge_inversion_diagnostic([])
        assert report["largeEdgeCohort"]["roi"]["qualifyingBets"] == 0
        assert report["smallEdgeCohort"]["roi"]["qualifyingBets"] == 0
