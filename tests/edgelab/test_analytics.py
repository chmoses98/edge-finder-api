#!/usr/bin/env python3
"""
tests/edgelab/test_analytics.py
===================================
Coverage for lib/edgelab/analytics.py + lib/edgelab/market_family_mapping.py
(EdgeLab Phase 2 Milestone 1 -- docs/EDGELAB_PHASE2_DESIGN.md).
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.analytics import (
    AnalyticsDataError,
    bets_by_canonical_family,
    clv_summary_by_canonical_family,
    completeness_metrics,
    open_session,
    roi_by_canonical_family,
    row_counts_by_entity_and_date,
    unmapped_market_family_values,
)
from lib.edgelab.market_family_mapping import MARKET_FAMILY_ALIASES, UNKNOWN, UNMAPPED, canonicalize_market_family


def _write_jsonl(path, records, compressed=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(path, "wt") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _bet(bet_id, market_family=None, stake=None, entry_price=0.5, entry_timestamp="2026-07-31T22:00:00Z",
         status="pending", clv=None, net_profit_loss=None, **overrides):
    rec = {
        "betId": bet_id, "marketTicker": f"T-{bet_id}", "marketFamily": market_family,
        "stake": stake, "entryPrice": entry_price, "entryTimestamp": entry_timestamp,
        "status": status, "clv": clv, "netProfitLoss": net_profit_loss,
        "thesisTags": [], "correlationGroup": None, "recommendationId": None,
        "side": "YES", "selection": "x",
    }
    rec.update(overrides)
    return rec


# ── Reading plain / gzipped JSONL, cross-date, missing/malformed ───────────

def test_reads_plain_jsonl(tmp_path):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [_bet("b1")])
    with open_session(root=str(tmp_path)) as session:
        assert session.is_available("bets")
        assert session.fetchall("SELECT COUNT(*) FROM raw_bets")[0][0] == 1


def test_reads_gzipped_jsonl(tmp_path):
    _write_jsonl(str(tmp_path / "observations" / "2026-07-31.jsonl.gz"),
                 [{"marketObservationId": "o1", "marketTicker": "T", "capturedAt": "2026-07-31T22:00:00Z"}],
                 compressed=True)
    with open_session(root=str(tmp_path)) as session:
        assert session.is_available("observations")
        assert session.fetchall("SELECT COUNT(*) FROM raw_observations")[0][0] == 1


def test_reads_mixed_plain_and_gzipped_in_one_glob(tmp_path):
    _write_jsonl(str(tmp_path / "observations" / "2026-07-30.jsonl"),
                 [{"marketObservationId": "o1", "marketTicker": "T", "capturedAt": "2026-07-30T22:00:00Z"}])
    _write_jsonl(str(tmp_path / "observations" / "2026-07-31.jsonl.gz"),
                 [{"marketObservationId": "o2", "marketTicker": "T", "capturedAt": "2026-07-31T22:00:00Z"}],
                 compressed=True)
    with open_session(root=str(tmp_path)) as session:
        assert session.fetchall("SELECT COUNT(*) FROM raw_observations")[0][0] == 2


def test_cross_date_queries_span_every_partition(tmp_path):
    for date in ("2026-07-01", "2026-07-02", "2026-07-03"):
        _write_jsonl(str(tmp_path / "observations" / f"{date}.jsonl"),
                     [{"marketObservationId": f"o-{date}", "marketTicker": "T", "capturedAt": f"{date}T22:00:00Z"}])
    with open_session(root=str(tmp_path)) as session:
        rows = row_counts_by_entity_and_date(session)
        dates = {r["date"] for r in rows if r["entity"] == "observations"}
        assert dates == {"2026-07-01", "2026-07-02", "2026-07-03"}
        assert session.fetchall("SELECT COUNT(*) FROM raw_observations")[0][0] == 3


def test_missing_entity_directory_is_reported_unavailable_never_a_crash(tmp_path):
    os.makedirs(str(tmp_path), exist_ok=True)
    with open_session(root=str(tmp_path)) as session:
        for entity in ("observations", "bets", "settlements", "recommendations", "clv_quotes"):
            assert session.is_available(entity) is False
        # No raw_bets view should even exist -- querying it must fail clearly, not return empty.
        try:
            session.fetchall("SELECT * FROM raw_bets")
            assert False, "expected an error querying a view that was never registered"
        except Exception:
            pass


def test_malformed_json_file_raises_analytics_data_error_with_file_name(tmp_path):
    """
    DuckDB's read_json_auto samples a file to infer its columns as part
    of binding the view, so a malformed file's error surfaces as soon
    as open_session() registers it -- not deferred to the first query.
    """
    path = str(tmp_path / "bets" / "bets.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{this is not valid json\n")
    try:
        open_session(root=str(tmp_path))
        assert False, "expected AnalyticsDataError"
    except AnalyticsDataError as exc:
        assert "bets.jsonl" in str(exc)


def test_corrupt_gzip_raises_analytics_data_error(tmp_path):
    path = str(tmp_path / "observations" / "2026-07-31.jsonl.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"not actually gzip data")
    try:
        open_session(root=str(tmp_path))
        assert False, "expected AnalyticsDataError"
    except AnalyticsDataError:
        pass


# ── Backward compatibility ──────────────────────────────────────────────────

def test_backward_compatible_records_without_sport_or_platform(tmp_path):
    """The exact scenario of every record committed before Milestone 1 -- sport/platform never written at all."""
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [_bet("b1", market_family="ML")])
    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT sport, platform FROM v_placed_bets WHERE betId = 'b1'")[0]
        assert row == ("MLB", "KALSHI")


def test_mixed_old_and_new_records_in_one_glob(tmp_path):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [
        _bet("old", market_family="ML"),  # no sport/platform key at all
        _bet("new", market_family="ML", sport="MLB", platform="KALSHI"),
    ])
    with open_session(root=str(tmp_path)) as session:
        rows = {r[0]: (r[1], r[2]) for r in session.fetchall("SELECT betId, sport, platform FROM v_placed_bets")}
        assert rows["old"] == ("MLB", "KALSHI")
        assert rows["new"] == ("MLB", "KALSHI")


# ── Canonicalization ─────────────────────────────────────────────────────────

# Every distinct raw marketFamily spelling actually observed in the real
# committed data/edgelab/bets/bets.jsonl at the time this milestone was
# built (see docs/EDGELAB_PHASE2_DESIGN.md's audit finding).
REAL_OBSERVED_SPELLINGS = [
    "KXMLBTEAMTOTAL", "ML", "F5_ML_Away", "ML_Away", "KXMLBGAME",
    "TT_Away_Over", "KXMLBRFI", "F5 ML", "YRFI", "KXMLBF5", "ML_Home",
]


def test_every_real_observed_spelling_canonicalizes_not_unmapped(tmp_path):
    records = [_bet(f"b{i}", market_family=spelling) for i, spelling in enumerate(REAL_OBSERVED_SPELLINGS)]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = session.fetchall("SELECT rawMarketFamily, canonicalMarketFamily FROM v_placed_bets")
        by_raw = dict(rows)
        for spelling in REAL_OBSERVED_SPELLINGS:
            assert by_raw[spelling] not in (UNMAPPED, None), f"{spelling} canonicalized to {by_raw[spelling]!r}"
        assert unmapped_market_family_values(session) == []


def test_real_committed_bets_ledger_has_zero_unmapped_values():
    """Direct proof against the actual repo data, not just a synthetic fixture."""
    with open_session() as session:
        assert unmapped_market_family_values(session) == []


def test_canonicalization_agrees_between_python_and_sql():
    for raw, expected in MARKET_FAMILY_ALIASES.items():
        assert canonicalize_market_family(raw) == expected


def test_multiple_spellings_of_the_same_family_group_together(tmp_path):
    """KXMLBGAME / ML / ML_Away / ML_Home all mean game_result -- must collapse to one canonical bucket."""
    records = [_bet(f"b{i}", market_family=s) for i, s in enumerate(["KXMLBGAME", "ML", "ML_Away", "ML_Home"])]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = bets_by_canonical_family(session)
        assert len(rows) == 1
        assert rows[0]["canonicalMarketFamily"] == "game_result"
        assert rows[0]["count"] == 4


def test_raw_value_is_preserved_verbatim(tmp_path):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [_bet("b1", market_family="F5_ML_Away")])
    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT rawMarketFamily FROM v_placed_bets WHERE betId = 'b1'")[0]
        assert row[0] == "F5_ML_Away"  # not normalized, not uppercased, not touched


def test_null_and_placeholder_values_are_unknown_not_unmapped(tmp_path):
    records = [
        _bet("b1", market_family=None),
        _bet("b2", market_family="N/A"),
        _bet("b3", market_family=""),
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = session.fetchall("SELECT betId, canonicalMarketFamily FROM v_placed_bets ORDER BY betId")
        assert all(r[1] == UNKNOWN for r in rows)
        assert unmapped_market_family_values(session) == []  # UNKNOWN is never reported as an unmapped spelling


def test_genuinely_new_spelling_is_unmapped_never_silently_guessed(tmp_path):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [_bet("b1", market_family="SOME_BRAND_NEW_SPELLING")])
    with open_session(root=str(tmp_path)) as session:
        row = session.fetchall("SELECT canonicalMarketFamily FROM v_placed_bets WHERE betId = 'b1'")[0]
        assert row[0] == UNMAPPED
        audit = unmapped_market_family_values(session)
        assert audit == [{"rawMarketFamily": "SOME_BRAND_NEW_SPELLING", "count": 1}]


# ── ROI / CLV calculations ───────────────────────────────────────────────────

def test_roi_calculation_settled_bets_only(tmp_path):
    records = [
        _bet("b1", market_family="ML", status="settled", stake=10.0, net_profit_loss=10.0),
        _bet("b2", market_family="ML", status="settled", stake=10.0, net_profit_loss=-10.0),
        _bet("b3", market_family="ML", status="pending", stake=10.0, net_profit_loss=None),  # must be excluded
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = roi_by_canonical_family(session)
        assert len(rows) == 1
        assert rows[0]["n"] == 2
        assert rows[0]["totalStake"] == 20.0
        assert rows[0]["totalNetProfitLoss"] == 0.0
        assert rows[0]["roi"] == 0.0


def test_roi_reflects_real_dollar_amounts_not_just_win_rate(tmp_path):
    """Two wins at different prices must NOT produce the same ROI -- this must be price-weighted, not a bare win count."""
    records = [
        _bet("b1", market_family="ML", status="settled", stake=100.0, net_profit_loss=400.0),  # big favorable win
        _bet("b2", market_family="ML", status="settled", stake=100.0, net_profit_loss=-100.0),
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = roi_by_canonical_family(session)
        assert rows[0]["roi"] == 1.5  # (400 - 100) / 200


def test_clv_summary_calculation(tmp_path):
    records = [
        _bet("b1", market_family="ML", clv=2.0),
        _bet("b2", market_family="ML", clv=-1.0),
        _bet("b3", market_family="ML", clv=None),  # must be excluded from the average
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = clv_summary_by_canonical_family(session)
        assert rows[0]["n"] == 2
        assert rows[0]["avgClv"] == 0.5
        assert rows[0]["positiveCount"] == 1
        assert rows[0]["negativeCount"] == 1


# ── Completeness metrics ─────────────────────────────────────────────────────

def test_completeness_metrics_percentages(tmp_path):
    records = [
        _bet("b1", thesisTags=["STARTER_EDGE"], correlationGroup="g1", recommendationId="r1"),
        _bet("b2", thesisTags=[], correlationGroup=None, recommendationId=None),
        _bet("b3", thesisTags=[], correlationGroup=None, recommendationId=None),
        _bet("b4", thesisTags=[], correlationGroup=None, recommendationId=None),
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        metrics = {(m["entity"], m["field"]): m for m in completeness_metrics(session)}
        assert metrics[("bets", "thesisTags")]["populated"] == 1
        assert metrics[("bets", "thesisTags")]["total"] == 4
        assert metrics[("bets", "thesisTags")]["pct"] == 25.0
        assert metrics[("bets", "correlationGroup")]["pct"] == 25.0
        assert metrics[("bets", "recommendationId")]["pct"] == 25.0


def test_completeness_reports_field_never_written_distinctly_from_zero_populated(tmp_path):
    """A field with 0% because every row has it null (OK) must be distinguishable from a field that doesn't exist as a column at all (FIELD_NEVER_WRITTEN)."""
    records = [_bet("b1")]  # no sport/platform key at all
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        metrics = {(m["entity"], m["field"]): m for m in completeness_metrics(session)}
        assert metrics[("bets", "sport")]["status"] == "FIELD_NEVER_WRITTEN"
        assert metrics[("bets", "correlationGroup")]["status"] == "OK"  # column exists (always null), just 0% populated
        assert metrics[("bets", "correlationGroup")]["pct"] == 0.0


def test_completeness_unavailable_entity_reported_distinctly(tmp_path):
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), [_bet("b1")])
    with open_session(root=str(tmp_path)) as session:
        metrics = {(m["entity"], m["field"]): m for m in completeness_metrics(session)}
        assert metrics[("observations", "lineupConfirmationState")]["status"] == "UNAVAILABLE"
        assert metrics[("observations", "lineupConfirmationState")]["pct"] is None


# ── Sample-size gating ───────────────────────────────────────────────────────

def test_sample_size_gate_boundary_at_20(tmp_path):
    records_19 = [_bet(f"a{i}", market_family="ML") for i in range(19)]
    records_20 = [_bet(f"b{i}", market_family="YRFI") for i in range(20)]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records_19 + records_20)
    with open_session(root=str(tmp_path)) as session:
        rows = {r["canonicalMarketFamily"]: r for r in bets_by_canonical_family(session)}
        assert rows["game_result"]["count"] == 19
        assert rows["game_result"]["sampleStatus"] == "INSUFFICIENT_SAMPLE"
        assert rows["first_inning_run"]["count"] == 20
        assert rows["first_inning_run"]["sampleStatus"] == "DESCRIPTIVE_ONLY"


def test_small_sample_roi_is_never_silently_presented_as_significant(tmp_path):
    """A 100% win rate on 2 bets must still be flagged INSUFFICIENT_SAMPLE, never treated as evidence."""
    records = [
        _bet("b1", market_family="ML", status="settled", stake=10.0, net_profit_loss=10.0),
        _bet("b2", market_family="ML", status="settled", stake=10.0, net_profit_loss=10.0),
    ]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)
    with open_session(root=str(tmp_path)) as session:
        rows = roi_by_canonical_family(session)
        assert rows[0]["roi"] == 1.0  # the number is still computed and returned...
        assert rows[0]["sampleStatus"] == "INSUFFICIENT_SAMPLE"  # ...but explicitly flagged as noise, not evidence


# ── Determinism ──────────────────────────────────────────────────────────────

def test_repeated_runs_produce_identical_results(tmp_path):
    records = [_bet(f"b{i}", market_family=REAL_OBSERVED_SPELLINGS[i % len(REAL_OBSERVED_SPELLINGS)], clv=float(i), stake=10.0, status="settled", net_profit_loss=float(i) - 5) for i in range(25)]
    _write_jsonl(str(tmp_path / "bets" / "bets.jsonl"), records)

    def _run():
        with open_session(root=str(tmp_path)) as session:
            return (
                bets_by_canonical_family(session),
                roi_by_canonical_family(session),
                clv_summary_by_canonical_family(session),
                completeness_metrics(session),
            )

    first = _run()
    second = _run()
    third = _run()
    assert first == second == third
