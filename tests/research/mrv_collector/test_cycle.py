import json
import os
from datetime import datetime, timezone

import pytest

from lib.edgelab.research.mrv_collector import COLLECTOR_VERSION, SCHEMA_VERSION
from lib.edgelab.research.mrv_collector import fetch as F, cycle as CY, health as H
from lib.edgelab.research.mrv_collector.storage import Store
from tests.research.mrv_collector.fake_world import standard_world, book

NOW = datetime(2026, 9, 22, 20, 0, 0, tzinfo=timezone.utc)


def _run(world, root, **kw):
    f = F.Fetcher(transport=world.transport, clock=world.clock, sleeper=lambda s: None, base_sleep=0.0, min_sleep=0.0)
    return CY.run_cycle(f, Store(root), now=kw.pop("now", NOW), odds_api_key=kw.pop("odds_api_key", "k"), **kw)


def test_full_universe_books_are_requested_for_every_pregame_per_game_market(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    rc = m["reconciliation"]
    # per-game markets of the two pregame games listed by Kalshi: game1 2 ML + 5 TOTAL + 1 SPREAD + 1 TT + 2 F5 + 1 F5TOTAL = 12; game2 2 ML + 1 SPREAD + 1 TT + 1 F5 + 1 F5TOTAL = 6
    assert rc["booksRequested"] == 18 and rc["booksReceived"] == 18 and rc["booksFailed"] == 0
    book_urls = [u for u in w.requested if u.endswith("/orderbook")]
    assert len(book_urls) == 18
    assert not any("SEALAA" in u for u in book_urls)       # started game: no book
    assert rc["captureClass"] == "COMPLETE"


def test_reconciliation_arithmetic_closes_and_exclusions_have_reasons(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    rc = m["reconciliation"]
    assert rc["marketsReceived"] == rc["marketsArchived"] + rc["marketsReferenced"] + rc["marketsExcluded"]
    assert rc["unaccountedRows"] == 0
    assert "KXMLBWS" in m["seriesExcludedByPolicy"] and "KXMLBMYSTERY" in m["seriesUnclassified"]
    assert m["seriesEvidence"]["KXMLBTOTAL"]["complete"] and m["seriesEvidence"]["KXMLBTOTAL"]["pages"] == 3


def test_family_starvation_is_reported_per_game(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    st = m["starvation"]
    assert st["count"] == 1 and st["starved"][0]["game"] == 700002 and st["starved"][0]["missing"] == ["KXMLBTOTAL"]
    assert st["starved"][0]["physicalGameKeys"] == ["26SEP221910NYYBOS"]
    assert m["researchComplete"] is False and m["captureClass"] == "COMPLETE"


def test_every_row_and_manifest_carries_collector_version(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    for kind in ("kalshi_quotes", "kalshi_books", "kalshi_crosssection", "kalshi_trades", "sportsbook_odds", "sportsbook_joins", "mlb_state"):
        rows = list(s.iter_gz(kind, m["gameDate"]))
        assert rows, kind
        assert all(r["collectorVersion"] == COLLECTOR_VERSION and r["schemaVersion"] == SCHEMA_VERSION and r["runId"] == m["runId"] for r in rows), kind
    assert m["collectorVersion"] == COLLECTOR_VERSION


def test_books_and_quotes_carry_per_fetch_timestamps(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    books = list(s.iter_gz("kalshi_books", m["gameDate"]))
    assert all(b["requestedAt"] and b["respondedAt"] and b["sourceDate"] for b in books)
    assert len({b["respondedAt"] for b in books}) == len(books)          # distinct per fetch, not one per cycle
    b0 = books[0]["book"]
    assert b0["bestYesBid"] == 45 and b0["bestYesAsk"] == 47 and b0["bestNoBid"] == 53 and b0["bestNoAsk"] == 55
    assert b0["levelsYes"] == 3 and b0["yesBids"][0] == [45, 300.0] and b0["sourceKey"] == "orderbook_fp"
    x = list(s.iter_gz("kalshi_crosssection", m["gameDate"]))[0]
    assert all(e.get("qAt") for e in x["tickers"].values())


def test_change_suppression_references_only_persisted_anchors(tmp_path):
    w = standard_world()
    m1 = _run(w, str(tmp_path))
    m2 = _run(w, str(tmp_path))
    r1, r2 = m1["reconciliation"], m2["reconciliation"]
    assert r1["marketsArchived"] > 0 and r1["marketsReferenced"] == 0
    assert r2["marketsArchived"] == 0 and r2["marketsReferenced"] == r2["marketsReceived"] - r2["marketsExcluded"]
    assert r2["booksArchived"] == 0 and r2["booksReferenced"] == 18
    s = Store(str(tmp_path))
    xs = list(s.iter_gz("kalshi_crosssection", m2["gameDate"]))
    assert len(xs) == 2 and all(e.get("qRef") for e in xs[1]["tickers"].values())
    # state says unchanged but the partition is gone -> full row is rewritten (no dangling reference)
    os.remove(s.part("kalshi_books", m2["gameDate"]))
    m3 = _run(w, str(tmp_path))
    assert m3["reconciliation"]["booksArchived"] == 18


def test_book_change_is_captured(tmp_path):
    w = standard_world()
    _run(w, str(tmp_path))
    w.books["KXMLBGAME-26SEP221910PITCWS-PIT"] = book([(46, 10)], [(52, 10)])
    m = _run(w, str(tmp_path))
    assert m["reconciliation"]["booksArchived"] == 1


def test_trades_dedup_and_mlb_filter(tmp_path):
    w = standard_world()
    m1 = _run(w, str(tmp_path))
    m2 = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    rows = list(s.iter_gz("kalshi_trades", m1["gameDate"]))
    assert len(rows) == 5 and all(r["ticker"].startswith("KXMLB") for r in rows)
    assert m2["written"]["kalshi_trades"] == 0


def test_sportsbook_rows_and_joins(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    joins = {j["eventId"]: j for j in s.iter_gz("sportsbook_joins", m["gameDate"])}
    assert joins["e1"]["status"] == "MATCHED" and joins["e1"]["gamePk"] == 700001
    assert joins["e2"]["status"] == "MATCHED" and joins["e2"]["gamePk"] == 700002
    assert joins["e3"]["status"] == "MATCHED" and joins["e3"]["gamePk"] == 700004
    assert joins["e4"]["status"] == "UNMATCHED" and joins["e4"]["reason"] == "NO_ELIGIBLE_GAME_WITHIN_TOLERANCE"
    odds = list(s.iter_gz("sportsbook_odds", m["gameDate"]))
    assert len(odds) == 4 * 4 * 6
    assert all(o["providerLastUpdate"] and o["requestedAt"] and o["respondedAt"] for o in odds)
    assert m["odds"]["credits"]["remaining"] == "13997" and m["odds"]["status"] == "OK" and m["odds"]["creditsConsumedThisCycle"] == 3


def test_ambiguous_join_is_refused(tmp_path):
    w = standard_world()
    from tests.research.mrv_collector.fake_world import sched_game
    w.games.append(sched_game(700009, "PIT", "CWS", "2026-09-22T23:40:00Z", official="2026-09-22"))   # doubleheader twin
    m = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    j = {j["eventId"]: j for j in s.iter_gz("sportsbook_joins", m["gameDate"])}["e1"]
    assert j["status"] == "AMBIGUOUS" and j["gamePk"] is None and sorted(j["candidates"]) == [700001, 700009]
    assert m["joins"]["ambiguous"] == 1


def test_state_rows_record_first_seen_transitions_and_source_timestamp(tmp_path):
    w = standard_world()
    m1 = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    rows = list(s.iter_gz("mlb_state", m1["gameDate"]))
    r1 = {r["gamePk"]: r for r in rows}
    assert r1[700001]["firstObservation"] and r1[700001]["transitions"] == [] and r1[700001]["sourceTimestamp"] == "20260922_200000"
    assert r1[700001]["sourceTimestampSemantics"] == "feed_last_update_not_event_time"
    assert r1[700001]["state"]["awayLineupPosted"] and not r1[700001]["state"]["homeLineupPosted"]
    from tests.research.mrv_collector.fake_world import feed
    w.feeds[700001] = feed(away_lu=(1, 2, 3), home_lu=(9, 8, 7), home_pp=555, ts="20260922_201500")
    m2 = _run(w, str(tmp_path))
    rows2 = [r for r in s.iter_gz("mlb_state", m2["gameDate"]) if r["runId"] == m2["runId"]]
    assert len(rows2) == 1 and rows2[0]["gamePk"] == 700001
    fields = {t["field"]: t for t in rows2[0]["transitions"]}
    assert fields["homeLineupPosted"]["from"] is False and fields["homeLineupPosted"]["to"] is True
    assert fields["homeProbableId"]["from"] == 102 and fields["homeProbableId"]["to"] == 555
    assert rows2[0]["prevObservedAt"] == r1[700001]["observedAt"] and rows2[0]["observedAt"] > rows2[0]["prevObservedAt"]
    # a third identical cycle writes nothing for that game
    m3 = _run(w, str(tmp_path))
    assert m3["stateRowsWritten"] == 0


def test_partial_and_failed_cycles_are_preserved_and_retries_get_new_ids(tmp_path):
    w = standard_world(fail_urls=["/KXMLBTOTAL-26SEP221910PITCWS-8/orderbook"])
    m1 = _run(w, str(tmp_path))
    assert m1["captureClass"] == "PARTIAL" and m1["reconciliation"]["booksFailed"] == 1
    s = Store(str(tmp_path))
    assert os.path.exists(s.manifest_path(m1["gameDate"], m1["runId"]))
    w2 = standard_world()
    m2 = _run(w2, str(tmp_path), attempt=2)
    assert m2["runId"] != m1["runId"] and m2["captureClass"] == "COMPLETE"
    assert os.path.exists(s.manifest_path(m1["gameDate"], m1["runId"]))   # first attempt never disappears
    attempts = list(s.iter_attempts(m1["gameDate"]))
    assert [a["attempt"] for a in attempts] == [1, 2]
    with pytest.raises(FileExistsError):
        s.write_manifest(m1["gameDate"], m1["runId"], {"x": 1})


def test_series_list_failure_is_a_failed_capture_not_a_silent_empty_one(tmp_path):
    w = standard_world(fail_urls=["/series?"])
    m = _run(w, str(tmp_path))
    assert m["captureClass"] == "FAILED" and m["reconciliation"]["marketsReceived"] == 0
    assert any(f["stage"] == "series" for f in m["failures"])


def test_health_report_and_gates_from_persisted_corpus(tmp_path):
    w = standard_world()
    for i in range(4):
        _run(w, str(tmp_path), now=NOW.replace(minute=i * 10))
    s = Store(str(tmp_path))
    rep = H.build_health(s, end_date="2026-09-22", days=2)
    mt = rep["metrics"]
    assert mt["captures"]["requested"] == 4 and mt["captures"]["delivered"] == 4 and mt["captures"]["complete"] == 4
    assert mt["cadence"]["medianGapMin"] == pytest.approx(10.0) and mt["cadence"]["maxGapMin"] == pytest.approx(10.0)
    assert mt["markets"]["unaccounted"] == 0
    assert mt["sportsbook"]["matched"] == 12 and mt["sportsbook"]["ambiguous"] == 0
    assert mt["timestamps"]["perFetchTimestampShare"] == 1.0
    assert mt["orderBook"]["twoSidedShare"] == 1.0
    g = rep["gates"]
    assert g["checks"]["cadence.medianGap"] and g["checks"]["completeness.unaccountedRows"] and g["checks"]["timestamps.perFetchShare"]
    assert not g["checks"]["familyCoverage.starvedGameCycles"]       # game 2 is starved of KXMLBTOTAL
    assert not g["checks"]["sample.uniqueGames"] and not g["researchReady"] and not g["infrastructureHealthy"]
    assert "familyCoverage.starvedGameCycles" in g["failing"]


def test_cadence_gaps_only_counted_inside_mlb_window():
    st = H.cadence_stats(["2026-09-22T07:00:00Z", "2026-09-22T09:00:00Z", "2026-09-22T16:00:00Z", "2026-09-22T16:10:00Z", "2026-09-22T16:35:00Z"])
    assert st["delivered"] == 5 and st["gapsInWindow"] == 2 and st["maxGapMin"] == 25.0 and st["medianGapMin"] == 17.5


def test_attempt_without_manifest_counts_as_not_delivered(tmp_path):
    s = Store(str(tmp_path))
    s.append_attempt("2026-09-22", {"runId": "MRV1_x", "attempt": 1, "startedAt": "2026-09-22T16:00:00Z"})
    rep = H.build_health(s, end_date="2026-09-22", days=1)
    assert rep["metrics"]["captures"]["requested"] == 1 and rep["metrics"]["captures"]["delivered"] == 0
    assert rep["metrics"]["captures"]["attemptsWithoutManifest"] == 1 and not rep["gates"]["infrastructureHealthy"]


def test_collector_never_touches_the_alpha_0002_path_or_production_modules():
    """Import statements and string literals only (prose in docstrings may name what is forbidden)."""
    import ast
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib", "edgelab", "research", "mrv_collector")
    forbidden_imports = ("build_market_ledger", "risk_gate", "lib.edgelab.recommendations", "lib.edgelab.bankroll", "settle_markets",
                         "log_bet", "mlb_alpha_0002", "kalshi_price_check", "write_pending_bets")
    for fn in os.listdir(root):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(root, fn)).read())
        docstrings = {ast.get_docstring(n) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert not any(b in n for b in forbidden_imports), (fn, n)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip() not in {d.strip() for d in docstrings if d}:
                assert "mlb_alpha_0002/" not in node.value and "portfolio/orders" not in node.value and "place_order" not in node.value, (fn, node.value[:60])
