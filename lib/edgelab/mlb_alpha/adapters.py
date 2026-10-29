"""
lib/edgelab/mlb_alpha/adapters.py
=================================
Turn the two prospective collectors' storage formats into the engine's input.

  ALPHA-0002  books: {'marketTicker','orderbook':{'yes_dollars','no_dollars'},'runId','capturedAt'}
              odds : one row per (run, event) with nested bookmakers (Odds API shape)
  MRV v1      books: {'book':{'yesBids':[[cents,size]],'noBids':[[cents,size]]},'physicalGameKey','requestedAt',...}
              odds : one flattened row per (event, bookmaker, market, outcome), joins in a separate store

Both are converted to  Snapshot = {'runId','capturedAt','games': {gameKey: {'markets': [{'ticker','book','minToStart'}],
'anchor': {'pHome','line','pOver'} | None, 'consensus': ... | None}}}  with books in the dollars form used by
lib.edgelab.mlb_alpha.execution.  A game with ambiguous identity is dropped and counted; nothing is guessed.
"""

import collections
from datetime import datetime

from lib.edgelab.mlb_alpha import corpus, taxonomy as tx
from lib.edgelab.research.market_structure import sharp


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def mrv_book_to_dollars(book):
    """MRV stores integer-cent levels: {'yesBids':[[cents,size]],'noBids':[...]} -> Kalshi dollar form."""
    if not book:
        return None
    def side(levels):
        return [["%.4f" % (c / 100.0), "%.2f" % s] for c, s in (levels or [])]
    return {"yes_dollars": side(book.get("yesBids")), "no_dollars": side(book.get("noBids"))}


def _anchor_from_books(books, home_name, away_name):
    """Pinnacle-primary anchor + multi-book consensus from {bookmaker: {market: {outcome: (point, decimal)}}}."""
    def two_way(bk):
        h2h = bk.get("h2h") or {}
        tot = bk.get("totals") or {}
        out = {}
        if home_name in h2h and away_name in h2h:
            nv = sharp.novig_two_way(h2h[away_name][1], h2h[home_name][1])
            if nv:
                out["pHome"] = nv[1]
        if "Over" in tot and "Under" in tot and tot["Over"][0] == tot["Under"][0]:
            nv = sharp.novig_two_way(tot["Over"][1], tot["Under"][1])
            if nv:
                out["line"], out["pOver"] = float(tot["Over"][0]), nv[0]
        return out
    per = {k: two_way(v) for k, v in books.items()}
    pin = per.get("pinnacle") or {}
    if not all(k in pin for k in ("pHome", "line", "pOver")):
        return None, None
    anchor = {"pHome": pin["pHome"], "line": pin["line"], "pOver": pin["pOver"]}
    hs = [v["pHome"] for v in per.values() if "pHome" in v]
    os_ = [v["pOver"] for v in per.values() if v.get("line") == pin["line"]]
    cons = {"pHome": sum(hs) / len(hs), "line": pin["line"], "pOver": sum(os_) / len(os_)} if hs and os_ else None
    return anchor, cons


def mrv_snapshots(mrv_root, game_window=(5.0, 240.0)):
    """Yield one Snapshot per MRV cycle. Games are keyed by physicalGameKey; anchors come from MATCHED joins only."""
    pk_to_key = {}
    for r in corpus.iter_prospective(mrv_root, "kalshi_books"):
        if r.get("gamePk") and r.get("physicalGameKey"):
            pk_to_key[r["gamePk"]] = r["physicalGameKey"]
    joins = collections.defaultdict(dict)       # runId -> eventId -> physicalGameKey (MATCHED only)
    for r in corpus.iter_prospective(mrv_root, "sportsbook_joins"):
        key = r.get("physicalGameKey") or pk_to_key.get(r.get("gamePk"))
        if r.get("status") == "MATCHED" and key:
            joins[r["runId"]][r["eventId"]] = key
    odds = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(dict)))
    names = {}
    for r in corpus.iter_prospective(mrv_root, "sportsbook_odds"):
        ev = r["eventId"]
        odds[r["runId"]][ev][r["bookmaker"]].setdefault(r["market"], {})[r["outcomeName"]] = (r.get("point"), r["priceDecimal"])
        names[(r["runId"], ev)] = (r["homeName"], r["awayName"], r["commenceTime"])
    books = collections.defaultdict(lambda: collections.defaultdict(dict))
    stamp = {}
    for r in corpus.iter_prospective(mrv_root, "kalshi_books"):
        bk = mrv_book_to_dollars(r.get("book"))
        if not bk or not r.get("physicalGameKey"):
            continue
        books[r["runId"]][r["physicalGameKey"]][r["marketTicker"]] = bk
        stamp[r["runId"]] = r["requestedAt"]
    for run_id in sorted(books, key=lambda x: stamp[x]):
        cap = stamp[run_id]
        snap = {"runId": run_id, "capturedAt": cap, "games": {}}
        by_game_anchor = {}
        for ev, pk in joins.get(run_id, {}).items():
            home, away, commence = names.get((run_id, ev), (None, None, None))
            if home is None or _ts(cap) >= _ts(commence):
                continue                                    # live or started: never use as an anchor
            a, c = _anchor_from_books(odds[run_id][ev], home, away)
            if a:
                by_game_anchor[pk] = (a, c)
        for pk, tickers in books[run_id].items():
            markets = []
            for t, bk in tickers.items():
                p = tx.parse_market_ticker(t)
                if not p.get("scheduledStartUtc"):
                    continue
                m2s = (p["scheduledStartUtc"].timestamp() - _ts(cap)) / 60.0
                markets.append({"ticker": t, "book": bk, "minToStart": round(m2s, 2)})
            a, c = by_game_anchor.get(pk, (None, None))
            snap["games"][pk] = {"markets": markets, "anchor": a, "consensus": c}
        yield snap
