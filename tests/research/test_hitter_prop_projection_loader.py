#!/usr/bin/env python3
"""Unit tests for lib/research/hitter_prop_projection_loader.py (synthetic rows only)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.research.hitter_prop_projection_loader import (  # noqa: E402
    PROJECTION_FIELDS,
    hitter_expected_stat_summaries,
    latest_pregame_hitter_projections,
    select_latest_rows,
)

T = "KXMLBHIT-26OCT071800LADATL-LADWSMITH16-1"


def _row(ticker=T, at="2026-10-07T20:00:00Z", status="PROJECTED", p=0.6, checkpoint="T_MINUS_90",
         family="hitter_hits", threshold=1, player_id="669257", game_id=849822, sid=None, **extra):
    r = {"marketTicker": ticker, "snapshotGeneratedAt": at, "projectionGeneratedAt": at,
         "projectionStatus": status, "modelProbability": p if status == "PROJECTED" else None,
         "checkpoint": checkpoint, "marketFamily": family, "threshold": threshold,
         "playerId": player_id, "player": "Will Smith", "gameId": game_id, "matchup": "LAD @ ATL",
         "hitterProjectionSnapshotId": sid or f"{ticker}-{at}-{checkpoint}", "rawProbabilityEdge": 0.02}
    r.update(extra)
    return r


class TestSelectLatestRows:
    def test_newest_row_at_or_before_now_wins(self):
        rows = [_row(at="2026-10-07T20:00:00Z", p=0.60), _row(at="2026-10-07T20:30:00Z", p=0.62, checkpoint="T_MINUS_60")]
        out = select_latest_rows(rows, "2026-10-07T20:45:00Z")
        assert out[T]["modelProbability"] == 0.62
        assert out[T]["checkpoint"] == "T_MINUS_60"

    def test_rows_after_now_are_never_used(self):
        rows = [_row(at="2026-10-07T20:00:00Z", p=0.60), _row(at="2026-10-07T21:00:00Z", p=0.70)]
        assert select_latest_rows(rows, "2026-10-07T20:59:59Z")[T]["modelProbability"] == 0.60
        assert select_latest_rows(rows, "2026-10-07T19:00:00Z") == {}

    def test_row_exactly_at_now_is_included(self):
        assert T in select_latest_rows([_row(at="2026-10-07T20:00:00Z")], "2026-10-07T20:00:00Z")

    def test_scratched_player_status_passes_through_and_never_falls_back(self):
        rows = [_row(at="2026-10-07T20:00:00Z", p=0.6),
                _row(at="2026-10-07T21:00:00Z", status="PLAYER_NOT_IN_STARTING_LINEUP", checkpoint="LINEUP_CONFIRMATION",
                     projectionStatusReason="not in confirmed lineup")]
        out = select_latest_rows(rows, "2026-10-07T21:30:00Z")
        assert out[T]["projectionStatus"] == "PLAYER_NOT_IN_STARTING_LINEUP"
        assert out[T]["isProjected"] is False
        assert out[T]["modelProbability"] is None
        assert out[T]["rawProbabilityEdge"] is None
        assert select_latest_rows(rows, "2026-10-07T21:30:00Z", include_non_projected=False) == {}

    def test_tie_on_time_breaks_on_later_checkpoint(self):
        rows = [_row(at="2026-10-07T20:00:00Z", p=0.5, checkpoint="T_MINUS_30"),
                _row(at="2026-10-07T20:00:00Z", p=0.55, checkpoint="LINEUP_CONFIRMATION")]
        assert select_latest_rows(list(reversed(rows)), "2026-10-07T21:00:00Z")[T]["modelProbability"] == 0.55

    def test_fractional_and_offset_timestamps_order_correctly(self):
        rows = [_row(at="2026-10-07T20:00:00.900Z", p=0.51), _row(at="2026-10-07T20:00:00.100+00:00", p=0.50)]
        assert select_latest_rows(rows, "2026-10-07T20:00:01Z")[T]["modelProbability"] == 0.51

    def test_created_at_fallback_and_untimed_rows_ignored(self):
        r1 = _row()
        r1.pop("snapshotGeneratedAt")
        r1.pop("projectionGeneratedAt")
        r1["createdAt"] = "2026-10-07T20:00:00Z"
        r2 = _row(ticker="KXMLBTB-X-2")
        r2.pop("snapshotGeneratedAt")
        r2.pop("projectionGeneratedAt")
        out = select_latest_rows([r1, r2], "2026-10-07T21:00:00Z")
        assert set(out) == {T}
        assert out[T]["availableAt"] == "2026-10-07T20:00:00Z"

    def test_output_has_exactly_the_documented_fields(self):
        out = select_latest_rows([_row()], "2026-10-07T21:00:00Z")
        assert tuple(out[T].keys()) == PROJECTION_FIELDS

    def test_bad_now_raises(self):
        with pytest.raises(ValueError):
            select_latest_rows([_row()], "yesterday")


class TestPartitionLoading:
    def test_reads_date_partition_under_data_root(self, tmp_path):
        d = tmp_path / "edgelab" / "hitter_projection_snapshots"
        d.mkdir(parents=True)
        (d / "2026-10-07.jsonl").write_text("\n".join(json.dumps(r) for r in [
            _row(at="2026-10-07T20:00:00Z", p=0.6), _row(at="2026-10-07T22:30:00Z", p=0.9)]) + "\n")
        out = latest_pregame_hitter_projections(str(tmp_path), "2026-10-07", "2026-10-07T21:00:00Z")
        assert out[T]["modelProbability"] == 0.6

    def test_missing_partition_is_empty(self, tmp_path):
        assert latest_pregame_hitter_projections(str(tmp_path), "2026-10-07", "2026-10-07T21:00:00Z") == {}


class TestExpectedStatSummaries:
    def test_ladder_lower_bound_when_mean_not_stored(self):
        rows = [_row(ticker=f"KXMLBHIT-G-P-{n}", threshold=n, p=p) for n, p in ((1, 0.62), (2, 0.22), (3, 0.05))]
        rows.append(_row(ticker="KXMLBTB-G-P-2", family="hitter_total_bases", threshold=2, p=0.35))
        proj = select_latest_rows(rows, "2026-10-07T21:00:00Z")
        s = hitter_expected_stat_summaries(proj)["669257:849822"]
        hits = s["families"]["hitter_hits"]
        assert hits["expectedStat"] is None and hits["expectedStatSource"] == "NOT_STORED"
        assert hits["ladderImpliedExpectedLowerBound"] == pytest.approx(0.89)
        assert hits["ladderContiguousFromOne"] is True
        tb = s["families"]["hitter_total_bases"]
        assert tb["ladderImpliedExpectedLowerBound"] is None and tb["ladderContiguousFromOne"] is False

    def test_distribution_mean_used_when_present(self):
        proj = select_latest_rows([_row(distributionMean=0.97)], "2026-10-07T21:00:00Z")
        fam = hitter_expected_stat_summaries(proj)["669257:849822"]["families"]["hitter_hits"]
        assert fam["expectedStat"] == 0.97 and fam["expectedStatSource"] == "DISTRIBUTION_MEAN"

    def test_non_projected_status_recorded(self):
        proj = select_latest_rows([_row(status="PLAYER_NOT_IN_STARTING_LINEUP")], "2026-10-07T21:00:00Z")
        fam = hitter_expected_stat_summaries(proj)["669257:849822"]["families"]["hitter_hits"]
        assert fam["nonProjectedStatuses"] == ["PLAYER_NOT_IN_STARTING_LINEUP"]
        assert fam["thresholds"] == {}
