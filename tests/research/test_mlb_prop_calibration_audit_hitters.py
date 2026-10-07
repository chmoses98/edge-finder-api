#!/usr/bin/env python3
"""
Dedupe / no-leakage tests for scripts/research/mlb_prop_calibration_audit_hitters.py
using synthetic fixtures only (no committed data is read).
"""
import inspect
import math
import os
import random
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.research import mlb_prop_calibration_audit_hitters as A  # noqa: E402

START = datetime(2026, 9, 23, 19, 45, tzinfo=timezone.utc)
TICKER = "KXMLBHIT-26SEP231545MINSF-MINJBELL56-2"


def _snap(at, *, ticker=TICKER, status="PROJECTED", p=0.3, observed=None, checkpoint="T_MINUS_30", sid=None, game_id=823168):
    return {"marketTicker": ticker, "marketFamily": "hitter_hits", "threshold": 2, "gameId": game_id,
            "playerId": "605137", "projectionStatus": status, "modelProbability": p if status == "PROJECTED" else None,
            "snapshotGeneratedAt": at, "projectionGeneratedAt": at, "marketObservedAt": observed or at,
            "checkpoint": checkpoint, "hitterProjectionSnapshotId": sid or f"{ticker}|{at}|{checkpoint}",
            "executableKalshiPrice": 0.25}


def _start(_row):
    return START


def _dt(s):
    return A.parse_iso(s)


class TestSelection:
    def test_one_row_per_ticker_newest_pregame_wins(self):
        rows = [_snap("2026-09-23T17:00:00Z", p=0.30, checkpoint="T_MINUS_90"),
                _snap("2026-09-23T19:10:00Z", p=0.33, checkpoint="T_MINUS_30"),
                _snap("2026-09-23T18:20:00Z", p=0.31, checkpoint="T_MINUS_60")]
        sel, excl, stats = A.select_last_pregame_snapshots(rows, _start)
        assert list(sel) == [TICKER]
        assert sel[TICKER]["modelProbability"] == 0.33
        assert stats["supersededPregameRegenerations"] == 2

    def test_row_at_or_after_first_pitch_is_never_selected(self):
        rows = [_snap("2026-09-23T19:10:00Z", p=0.33),
                _snap("2026-09-23T19:45:00Z", p=0.90, checkpoint="HITTER_CLOSING_WINDOW"),   # exactly at start
                _snap("2026-09-23T20:30:00Z", p=0.99, checkpoint="HITTER_CLOSING_WINDOW")]   # in-game
        sel, excl, _ = A.select_last_pregame_snapshots(rows, _start)
        assert sel[TICKER]["modelProbability"] == 0.33
        assert excl["ROW_NOT_STRICTLY_PREGAME"] == 2

    def test_post_start_market_observation_disqualifies_row(self):
        rows = [_snap("2026-09-23T19:10:00Z", p=0.33),
                _snap("2026-09-23T19:40:00Z", p=0.50, observed="2026-09-23T19:50:00Z")]
        sel, excl, _ = A.select_last_pregame_snapshots(rows, _start)
        assert sel[TICKER]["modelProbability"] == 0.33

    def test_scratched_last_row_excludes_ticker_without_fallback(self):
        rows = [_snap("2026-09-23T18:00:00Z", p=0.33),
                _snap("2026-09-23T19:20:00Z", status="PLAYER_NOT_IN_STARTING_LINEUP", checkpoint="LINEUP_CONFIRMATION")]
        sel, excl, _ = A.select_last_pregame_snapshots(rows, _start)
        assert sel == {}
        assert excl["TICKER_LAST_PREGAME_STATUS_PLAYER_NOT_IN_STARTING_LINEUP"] == 1

    def test_tie_breaks_deterministically_on_checkpoint_then_id(self):
        a = _snap("2026-09-23T19:00:00Z", p=0.40, checkpoint="T_MINUS_60")
        b = _snap("2026-09-23T19:00:00Z", p=0.41, checkpoint="LINEUP_CONFIRMATION")
        for order in ([a, b], [b, a]):
            sel, _, _ = A.select_last_pregame_snapshots(order, _start)
            assert sel[TICKER]["modelProbability"] == 0.41

    def test_unknown_start_is_excluded_not_guessed(self):
        sel, excl, _ = A.select_last_pregame_snapshots([_snap("2026-09-23T19:00:00Z")], lambda r: None)
        assert sel == {} and excl["ROW_NO_SCHEDULED_START"] == 1

    def test_selection_cannot_see_settlements(self):
        params = inspect.signature(A.select_last_pregame_snapshots).parameters
        assert set(params) == {"rows", "start_for_row", "families"}


class TestQuotesAndUnits:
    def test_quote_is_latest_at_or_before_and_strictly_pregame(self):
        q = [(_dt("2026-09-23T08:11:00Z"), 0.20, 0.22), (_dt("2026-09-23T19:00:00Z"), 0.25, 0.27),
             (_dt("2026-09-23T19:50:00Z"), 0.90, 0.95)]
        assert A.quote_at_or_before(q, _dt("2026-09-23T10:00:00Z"))[1] == 0.20
        assert A.quote_at_or_before(q, _dt("2026-09-23T23:00:00Z"), strictly_before=START)[1] == 0.25
        assert A.quote_at_or_before(q, _dt("2026-09-23T08:00:00Z")) is None

    def test_capture_unit_is_inferred_per_capture_not_per_value(self):
        recs = [{"capturedAt": "c1", "yesBid": 0.0, "yesAsk": 1.0},      # 1 cent ask in a cents capture
                {"capturedAt": "c1", "yesBid": 45.0, "yesAsk": 47.0},
                {"capturedAt": "c2", "yesBid": 0.0, "yesAsk": 1.0},      # $1.00 ask in a dollars capture
                {"capturedAt": "c2", "yesBid": 0.45, "yesAsk": 0.47}]
        scale = A.capture_unit_scale(recs)
        assert scale["c1"] == 100.0
        assert scale.get("c2", 1.0) == 1.0

    def test_mid_rejects_crossed_and_degenerate_books(self):
        assert A.mid_of((None, 0.30, 0.20)) is None
        assert A.mid_of((None, 0.0, 0.0)) is None
        assert A.mid_of((None, 0.20, 0.22)) == pytest.approx(0.21)


class TestCorpus:
    def test_market_prob_uses_snapshot_capture_not_later_quote_and_outcome_joined_after(self):
        snap = _snap("2026-09-23T19:10:00Z", observed="2026-09-23T08:11:00Z")
        quotes = {TICKER: [(_dt("2026-09-23T08:11:00Z"), 0.20, 0.22), (_dt("2026-09-23T19:00:00Z"), 0.30, 0.32),
                           (_dt("2026-09-23T20:00:00Z"), 0.98, 0.99)]}
        settlements = {TICKER: {"outcome": 1, "actualValue": 2, "threshold": 2}}
        rows, excl, cons = A.build_corpus({TICKER: snap}, settlements, quotes, _start)
        r = rows[0]
        assert r["marketP"] == pytest.approx(0.21)
        assert r["marketP_fresh"] == pytest.approx(0.31)
        assert r["closeQuote"][1] == 0.30          # in-game 20:00 quote never used as "close"
        assert r["outcome"] == 1 and cons["CONSISTENT"] == 1
        assert r["execPriceBasis"] == "OTHER"

    def test_unsettled_ticker_excluded(self):
        rows, excl, _ = A.build_corpus({TICKER: _snap("2026-09-23T19:10:00Z")}, {}, {}, _start)
        assert rows == [] and excl["NO_SETTLED_OUTCOME"] == 1

    def test_exec_price_basis(self):
        assert A.classify_exec_price_basis(0.21, 0.20, 0.22) == "MID"
        assert A.classify_exec_price_basis(0.22, 0.20, 0.22) == "ASK"
        assert A.classify_exec_price_basis(0.205, 0.20, 0.21) == "AMBIGUOUS_1C_SPREAD"


class TestTimeAndSplits:
    def test_ticker_start_is_eastern_time(self):
        assert A.ticker_scheduled_start_utc(TICKER) == START
        assert A.ticker_scheduled_start_utc("KXMLBHIT-26NOV041000LADNYY-X-1") == datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)

    def test_eastern_date_of_late_game(self):
        assert A.eastern_date(datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)) == "2026-10-07"

    def test_chronological_split_is_disjoint_and_ordered(self):
        rows = [{"date": d, "gameId": d} for d in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]]
        dev, test, dd, td = A.chronological_split(rows)
        assert dd == ["2026-08-01", "2026-08-02", "2026-08-03"] and td == ["2026-08-04", "2026-08-05"]
        assert max(dd) < min(td)


class TestScoring:
    def test_logistic_recalibration_recovers_known_parameters(self):
        rng = random.Random(1)
        ps, ys = [], []
        for _ in range(4000):
            p = rng.uniform(0.05, 0.9)
            true = A.sigmoid(0.3 + 0.7 * A.logit(p))
            ps.append(p)
            ys.append(1 if rng.random() < true else 0)
        a, b = A.fit_logistic_on_logit(ps, ys)
        assert a == pytest.approx(0.3, abs=0.12)
        assert b == pytest.approx(0.7, abs=0.08)

    def test_brier_logloss_ece_basics(self):
        assert A.brier([1.0, 0.0], [1, 0]) == 0
        assert A.log_loss([0.5], [1]) == pytest.approx(math.log(2))
        assert A.ece([0.25, 0.25, 0.25, 0.25], [1, 0, 0, 0]) == pytest.approx(0.0)

    def test_trade_uses_ask_for_yes_and_one_minus_bid_for_no_with_fee(self):
        yes = A.trade_for_row({"modelP": 0.5, "marketP": 0.4, "yesBid": 0.39, "yesAsk": 0.41, "outcome": 1, "marketTicker": TICKER})
        assert yes["side"] == "YES" and yes["price"] == 0.41 and yes["fee"] > 0
        assert yes["pnl"] == pytest.approx(1 - 0.41 - yes["fee"])
        no = A.trade_for_row({"modelP": 0.3, "marketP": 0.4, "yesBid": 0.39, "yesAsk": 0.41, "outcome": 1, "marketTicker": TICKER})
        assert no["side"] == "NO" and no["price"] == pytest.approx(0.61) and no["pnl"] < 0
