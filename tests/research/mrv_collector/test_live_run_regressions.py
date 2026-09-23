"""
Regressions for defects the first live MRV capture (run 35928372851, manifest
MRV1_20260923T222737Z_58d612) exposed and the fake world did not model:

  1. inning markets (KXMLBINNINGWIN / KXMLBINNINGTOTAL / KXMLBEXTRAS) are
     per-game book series by policy, but their tickers do not fit the research
     identity's shapes, so all 854 live markets were marked
     NOT_ELIGIBLE:UNPARSED and none of their books was requested;
  2. Kalshi does not keep the doubleheader suffix consistent across series
     (live: KXMLBGAME-26SEP231835TORBAL-* vs KXMLBTOTAL-26SEP231835TORBALG2-*
     for the same game 2), so keying coverage by the event suffix split one
     game in two and reported both halves as starved;
  3. the exchange-wide trade tape hit the 50-page safety cap on ~6 of its 15
     minutes (SAFETY_PAGE_CAP), silently leaving most of the window uncaptured.
"""
from datetime import datetime, timezone

from lib.edgelab.research.mrv_collector import fetch as F, cycle as CY, universe as UV
from lib.edgelab.research.mrv_collector.storage import Store
from tests.research.mrv_collector.fake_world import standard_world, market, book, sched_game, feed

NOW = datetime(2026, 9, 22, 20, 0, 0, tzinfo=timezone.utc)
EV1 = "26SEP221910PITCWS"


def _run(world, root):
    f = F.Fetcher(transport=world.transport, clock=world.clock, sleeper=lambda s: None, base_sleep=0.0, min_sleep=0.0)
    return CY.run_cycle(f, Store(root), now=NOW, odds_api_key="k")


def _add_series(w, series, markets):
    w.markets[series] = markets
    w.series = sorted(set(w.series) | {series})
    for m in markets:
        w.books[m["ticker"]] = book([(40, 10)], [(55, 12)])


def test_inning_and_extras_markets_get_books_via_the_event_ticker(tmp_path):
    w = standard_world()
    live_shapes = {
        "KXMLBINNINGWIN": ["KXMLBINNINGWIN-%s-9-TIE" % EV1, "KXMLBINNINGWIN-%s-9-PIT" % EV1, "KXMLBINNINGWIN-%s-1-CWS" % EV1],
        "KXMLBINNINGTOTAL": ["KXMLBINNINGTOTAL-%s-9-2" % EV1, "KXMLBINNINGTOTAL-%s-1-1" % EV1],
        "KXMLBEXTRAS": ["KXMLBEXTRAS-%s-EXTRAS" % EV1],
    }
    for s, tickers in live_shapes.items():
        _add_series(w, s, [market(t, "%s-%s" % (s, EV1)) for t in tickers])
    m = _run(w, str(tmp_path))
    book_urls = [u for u in w.requested if u.endswith("/orderbook")]
    for tickers in live_shapes.values():
        for t in tickers:
            assert any("/markets/%s/orderbook" % t in u for u in book_urls), t
    xs = list(Store(str(tmp_path)).iter_gz("kalshi_crosssection", m["gameDate"]))[-1]["tickers"]
    for tickers in live_shapes.values():
        for t in tickers:
            assert xs[t].get("book") is None and xs[t]["gamePk"] == 700001 and xs[t]["identity"] == "EVENT_TICKER_GAME_ONLY", t
    assert m["reconciliation"]["booksRequested"] == 18 + 6 and m["captureClass"] == "COMPLETE"


def test_unresolvable_event_segment_is_still_refused_not_guessed(tmp_path):
    w = standard_world()
    _add_series(w, "KXMLBINNINGWIN", [market("KXMLBINNINGWIN-26SEP22XXXXPITCWS-9-TIE", "KXMLBINNINGWIN-26SEP22XXXXPITCWS")])
    m = _run(w, str(tmp_path))
    xs = list(Store(str(tmp_path)).iter_gz("kalshi_crosssection", m["gameDate"]))[-1]["tickers"]
    assert xs["KXMLBINNINGWIN-26SEP22XXXXPITCWS-9-TIE"]["book"] == "NOT_ELIGIBLE:UNPARSED"
    assert not any("KXMLBINNINGWIN" in u and u.endswith("/orderbook") for u in w.requested)


def test_inconsistent_doubleheader_suffix_is_one_game_not_two_starved_halves(tmp_path):
    w = standard_world()
    w.games.append(sched_game(700005, "TOR", "BAL", "2026-09-22T22:35:00Z", official="2026-09-22"))
    w.feeds[700005] = feed(start="2026-09-22T22:35:00Z")
    k1, k2 = "26SEP221835TORBAL", "26SEP221835TORBALG2"     # exactly the live inconsistency
    per = {"KXMLBGAME": [market("KXMLBGAME-%s-TOR" % k1, "KXMLBGAME-" + k1), market("KXMLBGAME-%s-BAL" % k1, "KXMLBGAME-" + k1)],
           "KXMLBTOTAL": [market("KXMLBTOTAL-%s-8" % k2, "KXMLBTOTAL-" + k2)],
           "KXMLBSPREAD": [market("KXMLBSPREAD-%s-TOR2" % k2, "KXMLBSPREAD-" + k2)],
           "KXMLBTEAMTOTAL": [market("KXMLBTEAMTOTAL-%s-TOR4" % k2, "KXMLBTEAMTOTAL-" + k2)],
           "KXMLBF5": [market("KXMLBF5-%s-TOR" % k2, "KXMLBF5-" + k2)],
           "KXMLBF5TOTAL": [market("KXMLBF5TOTAL-%s-4" % k2, "KXMLBF5TOTAL-" + k2)]}
    for s, ms in per.items():
        w.markets[s] = w.markets.get(s, []) + ms
        for x in ms:
            w.books[x["ticker"]] = book([(40, 10)], [(55, 12)])
    m = _run(w, str(tmp_path))
    starved = {x["game"] for x in m["starvation"]["starved"]}
    assert 700005 not in starved                          # every core family present for the one game
    assert starved == {700002}                            # the fake world's genuine starvation is still reported
    assert m["reconciliation"]["gamesWithMarkets"] == 3    # 700001, 700002, 700005 -- not 4


def test_trade_tape_pages_past_the_series_safety_cap(tmp_path):
    w = standard_world(page_size=1)
    ev = "KXMLBGAME-%s-PIT" % EV1
    w.trades = [{"trade_id": "t%d" % i, "ticker": ev, "created_time": "2026-09-22T19:55:00Z", "count_fp": "1",
                 "yes_price_dollars": "0.46", "no_price_dollars": "0.54", "taker_side": "yes"} for i in range(UV.SAFETY_PAGE_CAP + 30)]
    m = _run(w, str(tmp_path))
    tp = m["tradesPagination"]
    assert tp["complete"] is True and tp["truncationReason"] is None and tp["pages"] == UV.SAFETY_PAGE_CAP + 30
    assert m["written"]["kalshi_trades"] == UV.SAFETY_PAGE_CAP + 30
    assert UV.TRADES_PAGE_CAP > UV.SAFETY_PAGE_CAP


def test_trade_tape_cap_is_still_recorded_when_hit(tmp_path):
    w = standard_world(page_size=1)
    w.trades = [{"trade_id": "t%d" % i, "ticker": "KXMLBGAME-%s-PIT" % EV1, "created_time": "2026-09-22T19:55:00Z", "count_fp": "1",
                 "yes_price_dollars": "0.46", "no_price_dollars": "0.54", "taker_side": "yes"} for i in range(UV.TRADES_PAGE_CAP + 5)]
    m = _run(w, str(tmp_path))
    assert m["tradesPagination"]["complete"] is False and m["tradesPagination"]["truncationReason"] == "SAFETY_PAGE_CAP"
