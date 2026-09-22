"""A deterministic fake of the three public APIs the MRV collector reads. No network."""
import json
import re
from urllib.parse import parse_qs, urlparse

from lib.edgelab.research.mrv_collector import universe as UV, mlb_state as MS


class FakeWorld(object):
    """Configurable responses keyed by URL pattern; records every URL requested."""

    def __init__(self, *, games=None, series=None, markets=None, books=None, trades=None, odds=None, feeds=None,
                 page_size=2, fail_urls=None, rate_limit_first_n=0, http_date="Tue, 22 Sep 2026 20:00:00 GMT"):
        self.games = games or []
        self.series = series or []
        self.markets = markets or {}          # series -> [market dict]
        self.books = books or {}              # ticker -> orderbook payload
        self.trades = trades or []
        self.odds = odds or []
        self.feeds = feeds or {}              # gamePk -> live feed payload
        self.page_size = page_size
        self.fail_urls = fail_urls or []      # substrings -> HTTP 500
        self.rate_limit_first_n = rate_limit_first_n
        self.http_date = http_date
        self.requested = []
        self.clock_calls = 0

    def clock(self):
        self.clock_calls += 1
        return "2026-09-22T20:00:%02d.%06dZ" % (self.clock_calls // 1000000 % 60, self.clock_calls % 1000000)

    def transport(self, url):
        self.requested.append(url)
        for bad in self.fail_urls:
            if bad in url:
                return 500, {"date": self.http_date}, b"{}"
        if self.rate_limit_first_n > 0:
            self.rate_limit_first_n -= 1
            return 429, {"date": self.http_date}, b"{}"
        u = urlparse(url)
        q = parse_qs(u.query)
        hdr = {"date": self.http_date, "x-requests-remaining": "1000", "x-requests-used": "5", "x-requests-last": "3"}
        if u.path.endswith("/series"):
            return 200, hdr, json.dumps({"series": [{"ticker": s} for s in self.series]}).encode()
        if u.path.endswith("/markets") and "series_ticker" in q:
            items = self.markets.get(q["series_ticker"][0], [])
            start = int(q.get("cursor", ["0"])[0] or 0)
            page = items[start:start + self.page_size]
            nxt = str(start + self.page_size) if start + self.page_size < len(items) else ""
            return 200, hdr, json.dumps({"markets": page, "cursor": nxt}).encode()
        m = re.search(r"/markets/([^/]+)/orderbook$", u.path)
        if m:
            payload = self.books.get(m.group(1), {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
            return 200, hdr, json.dumps(payload).encode()
        if u.path.endswith("/markets/trades"):
            start = int(q.get("cursor", ["0"])[0] or 0)
            page = self.trades[start:start + self.page_size]
            nxt = str(start + self.page_size) if start + self.page_size < len(self.trades) else ""
            return 200, hdr, json.dumps({"trades": page, "cursor": nxt}).encode()
        if "/schedule" in u.path:
            date = q.get("date", [""])[0]
            gs = [g for g in self.games if g.get("officialDate") == date]
            return 200, hdr, json.dumps({"dates": [{"date": date, "games": gs}]}).encode()
        m = re.search(r"/game/(\d+)/feed/live$", u.path)
        if m:
            return 200, hdr, json.dumps(self.feeds.get(int(m.group(1)), {})).encode()
        if "/sports/baseball_mlb/odds" in u.path:
            return 200, hdr, json.dumps(self.odds).encode()
        return 404, hdr, b"{}"


def sched_game(pk, away, home, iso_start, state="Scheduled", away_pp=101, home_pp=102, official=None, lineups=None):
    return {"gamePk": pk, "gameDate": iso_start, "officialDate": official or iso_start[:10],
            "status": {"detailedState": state, "abstractGameState": "Preview" if state in MS.PREGAME_STATES else "Live"},
            "teams": {"away": {"team": {"id": 1, "abbreviation": away}, "probablePitcher": {"id": away_pp}},
                      "home": {"team": {"id": 2, "abbreviation": home}, "probablePitcher": {"id": home_pp}}},
            "lineups": lineups or {}}


def market(ticker, event, yb=0.45, ya=0.47, status="active"):
    return {"ticker": ticker, "event_ticker": event, "status": status, "yes_bid_dollars": str(yb), "yes_ask_dollars": str(ya),
            "no_bid_dollars": str(round(1 - ya, 2)), "no_ask_dollars": str(round(1 - yb, 2)), "last_price_dollars": str(ya),
            "volume_fp": "100.0", "open_interest_fp": "50.0", "close_time": "2026-09-23T03:00:00Z", "title": ticker}


def book(yes_levels, no_levels):
    return {"orderbook_fp": {"yes_dollars": [[str(p / 100.0), str(q)] for p, q in yes_levels],
                             "no_dollars": [[str(p / 100.0), str(q)] for p, q in no_levels]}}


def feed(status="Scheduled", away_pp=101, home_pp=102, away_lu=(), home_lu=(), weather=None, ts="20260922_200000", start="2026-09-22T23:10:00Z"):
    return {"metaData": {"timeStamp": ts},
            "gameData": {"status": {"detailedState": status}, "probablePitchers": {"away": {"id": away_pp}, "home": {"id": home_pp}},
                         "weather": weather or {"condition": "Clear", "temp": "72", "wind": "5 mph, Out To CF"},
                         "datetime": {"dateTime": start}},
            "liveData": {"boxscore": {"teams": {"away": {"battingOrder": list(away_lu)}, "home": {"battingOrder": list(home_lu)}}}}}


def odds_event(eid, away, home, commence, books=("pinnacle", "draftkings", "fanduel", "betmgm")):
    bms = []
    for b in books:
        bms.append({"key": b, "last_update": "2026-09-22T19:59:30Z", "markets": [
            {"key": "h2h", "last_update": "2026-09-22T19:59:30Z", "outcomes": [{"name": home, "price": 1.9}, {"name": away, "price": 2.0}]},
            {"key": "totals", "last_update": "2026-09-22T19:59:31Z", "outcomes": [{"name": "Over", "point": 8.5, "price": 1.9}, {"name": "Under", "point": 8.5, "price": 1.95}]},
            {"key": "spreads", "last_update": "2026-09-22T19:59:32Z", "outcomes": [{"name": home, "point": -1.5, "price": 2.2}, {"name": away, "point": 1.5, "price": 1.7}]}]})
    return {"id": eid, "commence_time": commence, "home_team": home, "away_team": away, "bookmakers": bms}


def standard_world(**kw):
    """Two pregame games (PIT@CWS 23:10Z, NYY@BOS 23:10Z), one started game, full per-game families for game 1,
    game 2 missing the total ladder (starvation), props, trades, odds incl. one ambiguous event."""
    ev1, ev2 = "26SEP221910PITCWS", "26SEP221910NYYBOS"
    games = [sched_game(700001, "PIT", "CWS", "2026-09-22T23:10:00Z", official="2026-09-22"),
             sched_game(700002, "NYY", "BOS", "2026-09-22T23:10:00Z", official="2026-09-22"),
             sched_game(700003, "SEA", "LAA", "2026-09-22T17:10:00Z", state="In Progress", official="2026-09-22"),
             sched_game(700004, "SD", "SF", "2026-09-23T23:10:00Z", official="2026-09-23")]
    per_game = {
        "KXMLBGAME": [market("KXMLBGAME-%s-PIT" % ev1, "KXMLBGAME-" + ev1), market("KXMLBGAME-%s-CWS" % ev1, "KXMLBGAME-" + ev1),
                      market("KXMLBGAME-%s-NYY" % ev2, "KXMLBGAME-" + ev2), market("KXMLBGAME-%s-BOS" % ev2, "KXMLBGAME-" + ev2),
                      market("KXMLBGAME-26SEP221310SEALAA-SEA", "KXMLBGAME-26SEP221310SEALAA")],
        "KXMLBTOTAL": [market("KXMLBTOTAL-%s-%d" % (ev1, n), "KXMLBTOTAL-" + ev1, 0.9 - 0.1 * i, 0.92 - 0.1 * i) for i, n in enumerate((6, 7, 8, 9, 10))],
        "KXMLBSPREAD": [market("KXMLBSPREAD-%s-PIT2" % ev1, "KXMLBSPREAD-" + ev1), market("KXMLBSPREAD-%s-NYY2" % ev2, "KXMLBSPREAD-" + ev2)],
        "KXMLBTEAMTOTAL": [market("KXMLBTEAMTOTAL-%s-PIT4" % ev1, "KXMLBTEAMTOTAL-" + ev1), market("KXMLBTEAMTOTAL-%s-NYY4" % ev2, "KXMLBTEAMTOTAL-" + ev2)],
        "KXMLBF5": [market("KXMLBF5-%s-PIT" % ev1, "KXMLBF5-" + ev1), market("KXMLBF5-%s-TIE" % ev1, "KXMLBF5-" + ev1), market("KXMLBF5-%s-NYY" % ev2, "KXMLBF5-" + ev2)],
        "KXMLBF5TOTAL": [market("KXMLBF5TOTAL-%s-5" % ev1, "KXMLBF5TOTAL-" + ev1), market("KXMLBF5TOTAL-%s-5" % ev2, "KXMLBF5TOTAL-" + ev2)],
        "KXMLBHIT": [market("KXMLBHIT-%s-XYZ1" % ev1, "KXMLBHIT-" + ev1)],
        "KXMLBWS": [market("KXMLBWS-26-NYY", "KXMLBWS-26")],
        "KXMLBMYSTERY": [market("KXMLBMYSTERY-%s-A" % ev1, "KXMLBMYSTERY-" + ev1)],
    }
    books = {}
    for s, ms in per_game.items():
        if s in UV.DEFAULT_POLICY["perGameBookSeries"]:
            for m in ms:
                books[m["ticker"]] = book([(45, 300), (44, 120), (30, 1000)], [(53, 250), (52, 80)])
    trades = [{"trade_id": "t%d" % i, "ticker": "KXMLBGAME-%s-PIT" % ev1, "created_time": "2026-09-22T19:5%d:00.000000Z" % (i % 10),
               "count_fp": "10", "yes_price_dollars": "0.46", "no_price_dollars": "0.54", "taker_side": "yes"} for i in range(5)]
    trades.append({"trade_id": "tx", "ticker": "KXNFLGAME-X-Y", "created_time": "2026-09-22T19:55:00Z", "count_fp": "1", "yes_price_dollars": "0.5", "no_price_dollars": "0.5", "taker_side": "no"})
    odds = [odds_event("e1", "Pittsburgh Pirates", "Chicago White Sox", "2026-09-22T23:10:00Z"),
            odds_event("e2", "New York Yankees", "Boston Red Sox", "2026-09-22T23:12:00Z"),
            odds_event("e3", "San Diego Padres", "San Francisco Giants", "2026-09-23T23:10:00Z"),
            odds_event("e4", "Seattle Mariners", "Los Angeles Angels", "2026-09-22T17:10:00Z")]
    feeds = {700001: feed(away_lu=(1, 2, 3), home_lu=()), 700002: feed(status="Pre-Game", away_lu=(4, 5), home_lu=(6, 7)), 700004: feed(start="2026-09-23T23:10:00Z")}
    series = list(per_game.keys()) + ["KXMLBTOTAL"]
    w = FakeWorld(games=games, series=sorted(set(series)), markets=per_game, books=books, trades=trades, odds=odds, feeds=feeds, **kw)
    return w
