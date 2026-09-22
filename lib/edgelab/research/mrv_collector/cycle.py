"""
One MRV capture cycle.  Pure orchestration over injectable fetcher/store/
clock so it is fully testable without network.

Order of work (each stage records its own timestamps and failures):
  1. MLB schedule (today + tomorrow ET) -> eligible PREGAME games
  2. Kalshi series list -> policy classification
  3. every included series paged to exhaustion -> quotes (change-suppressed)
  4. per-game markets of eligible games -> order books (ALL of them; no cap)
  5. trade tape since the previous cycle start (overlap 60 s, dedup by trade_id)
  6. sportsbook odds (if a key is present) -> flattened rows + deterministic joins
  7. live feed per eligible game -> state rows on change, with transitions
  8. cross-section digest, reconciliation, manifest (write-once)
"""
import os
import random
import time
from datetime import datetime, timezone

from lib.edgelab.research.market_structure.identity import parse_ticker
from lib.edgelab.research.mrv_collector import COLLECTOR_VERSION
from lib.edgelab.research.mrv_collector import books as BK, mlb_state as MS, reconcile as RC, sportsbook as SB, universe as UV
from lib.edgelab.research.mrv_collector.storage import fingerprint, version_stamp

TRADE_OVERLAP_S = 60


def new_run_id(now_dt):
    return "MRV1_%s_%06x" % (now_dt.strftime("%Y%m%dT%H%M%SZ"), random.randrange(16 ** 6))


def et_game_date(now_dt):
    return (now_dt.replace(tzinfo=None) - __import__("datetime").timedelta(hours=4)).strftime("%Y-%m-%d")


def _sched_ts(iso):
    try:
        return int(datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def run_cycle(fetcher, store, *, now=None, odds_api_key=None, policy=None, trigger="local", attempt=1,
              include_props=True, include_trades=True, include_state=True):
    now_dt = now or datetime.now(timezone.utc)
    run_id = new_run_id(now_dt)
    date = et_game_date(now_dt)
    started = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    t0 = time.time()
    policy = policy or UV.load_policy()
    stamp = version_stamp(run_id)
    st = store.load_state()
    store.append_attempt(date, {**stamp, "attempt": attempt, "startedAt": started, "trigger": trigger, "gameDate": date})

    failures = []
    # 1. schedule -> eligible pregame games
    games, eligible = [], []
    sched_fetches = []
    for d in MS.et_dates_for(now_dt):
        r = fetcher.get(MS.schedule_url(d))
        sched_fetches.append({"date": d, "ok": r.ok, "error": r.error, **r.stamp()})
        if r.ok:
            games.extend(MS.games_from_schedule(r.json))
        else:
            failures.append({"stage": "schedule", "date": d, "error": r.error})
    by_pk = {}
    for g in games:
        if g.get("gamePk") is None:
            continue
        by_pk[g["gamePk"]] = g
        if MS.is_pregame(g):
            eligible.append(g)
    # Kalshi identity for eligible games: physical key = date+time+teams from the event ticker; map by (away, home, start)
    elig_by_key = {}
    for g in eligible:
        ts = _sched_ts(g.get("gameDate") or "")
        g["scheduledStartTs"] = ts
        elig_by_key[(g.get("awayAbbr"), g.get("homeAbbr"))] = elig_by_key.get((g.get("awayAbbr"), g.get("homeAbbr")), []) + [g]

    def game_for_ident(ident):
        cands = [g for g in elig_by_key.get((ident["awayTeam"], ident["homeTeam"]), [])
                 if g.get("scheduledStartTs") is not None and abs(g["scheduledStartTs"] - int(ident["scheduledStartUtc"].replace(tzinfo=timezone.utc).timestamp())) <= 45 * 60]
        if len(cands) == 1:
            return cands[0], None
        if len(cands) > 1:
            return None, "AMBIGUOUS_GAME_MATCH"
        return None, None

    # 2. series
    series_all, sr = UV.list_mlb_series(fetcher)
    series_list_ok = sr.ok
    if not sr.ok:
        failures.append({"stage": "series", "error": sr.error})
    classified = {s: UV.classify_series(s, policy) for s in series_all}
    included = [s for s, c in classified.items() if c in ("PER_GAME_BOOK", "PROP_QUOTE") and (include_props or c == "PER_GAME_BOOK")]

    # 3. markets per series
    quote_anchors = store.persisted_anchors(date, "kalshi_quotes")
    quotes_full, digest, excluded = [], {}, []
    series_evidence = {}
    received = 0
    per_game_markets = []          # (ticker, ident, game)
    markets_by_game = {}
    counts = {"received": 0, "archived": 0, "referenced": 0, "excluded": 0}
    for s in included:
        items, pg = UV.fetch_open_markets(fetcher, s)
        series_evidence[s] = {"class": classified[s], "items": len(items), "pagination": pg}
        for m in items:
            received += 1
            q = BK.quote_from_market(m)
            t = q["marketTicker"]
            if not t:
                excluded.append({"marketTicker": None, "series": s, "reason": "NO_TICKER"})
                continue
            qfp = fingerprint(BK.quote_fingerprint_payload(q))
            page = m.get("_page") or {}
            entry = digest.setdefault(t, {})
            entry.update(q=qfp, qAt=page.get("respondedAt"), s=s)
            if st["quoteFp"].get(t) == qfp and quote_anchors.get((t, qfp)):
                counts["referenced"] += 1
                entry["qRef"] = quote_anchors[(t, qfp)]
            else:
                st["quoteFp"][t] = qfp
                quotes_full.append({**stamp, **page, "seriesTicker": s, "seriesClass": classified[s], "fp": qfp, **q})
                counts["archived"] += 1
            if classified[s] == "PER_GAME_BOOK":
                ident = parse_ticker(t)
                if ident.get("status") != "RESOLVED":
                    entry["book"] = "NOT_ELIGIBLE:UNPARSED"
                    continue
                g, why = game_for_ident(ident)
                if g is None:
                    entry["book"] = "NOT_ELIGIBLE:%s" % (why or "GAME_NOT_PREGAME_OR_UNLISTED")
                    continue
                per_game_markets.append((t, ident, g))
                markets_by_game.setdefault(ident["physicalGameKey"], {}).setdefault(s, 0)
                markets_by_game[ident["physicalGameKey"]][s] += 1
                entry["gamePk"] = g["gamePk"]
    counts["excluded"] = len(excluded)

    # 4. order books for EVERY per-game market of an eligible pregame game (no cap)
    book_anchors = store.persisted_anchors(date, "kalshi_books")
    books_full = []
    books_requested = books_received = books_failed = books_empty = 0
    for t, ident, g in per_game_markets:
        books_requested += 1
        r = UV.fetch_orderbook(fetcher, t)
        entry = digest[t]
        if not r.ok:
            books_failed += 1
            entry["book"] = "FAILED:%s" % r.error
            continue
        nb = BK.normalize_book(r.json)
        books_received += 1
        entry["bAt"] = r.respondedAt
        if nb is None:
            books_empty += 1
            entry["b"] = None
            entry["book"] = "EMPTY_PAYLOAD"
            st["bookFp"].pop(t, None)
            books_full.append({**stamp, **r.stamp(), "marketTicker": t, "physicalGameKey": ident["physicalGameKey"], "gamePk": g["gamePk"],
                               "fp": None, "book": None, "responseKeys": sorted(r.json.keys()) if isinstance(r.json, dict) else None})
            continue
        bfp = fingerprint(BK.book_fingerprint_payload(nb))
        entry["b"] = bfp
        if st["bookFp"].get(t) == bfp and book_anchors.get((t, bfp)):
            entry["bRef"] = book_anchors[(t, bfp)]
            continue
        st["bookFp"][t] = bfp
        books_full.append({**stamp, **r.stamp(), "marketTicker": t, "physicalGameKey": ident["physicalGameKey"], "gamePk": g["gamePk"],
                           "seriesTicker": ident["seriesTicker"], "fp": bfp, "book": nb, "elapsedMs": r.elapsedMs})

    # 5. trades
    trades_rows, trade_pg = [], None
    if include_trades:
        last = st.get("lastCycleStartTs")
        min_ts = (last - TRADE_OVERLAP_S) if last else int(t0) - 900
        items, trade_pg = UV.fetch_trades_since(fetcher, min_ts)
        seen = set(st.get("recentTradeIds") or [])
        new_ids = []
        for tr in items:
            tk = (tr.get("ticker") or "").upper()
            if not tk.startswith(("KXMLB", "MLB")):
                continue
            tid = tr.get("trade_id")
            if tid in seen:
                continue
            page = tr.pop("_page", {})
            trades_rows.append({**stamp, **page, **tr})
            new_ids.append(tid)
        st["recentTradeIds"] = (list(seen) + new_ids)[-20000:]
    st["lastCycleStartTs"] = int(t0)

    # 6. sportsbook
    odds_rows, join_rows, odds_meta = [], [], None
    if odds_api_key:
        elig_join = [{"gamePk": g["gamePk"], "physicalGameKey": None, "awayAbbr": g.get("awayAbbr"), "homeAbbr": g.get("homeAbbr"),
                      "scheduledStartTs": g.get("scheduledStartTs")} for g in eligible]
        r = fetcher.get(SB.odds_url(odds_api_key))
        odds_meta = {"ok": r.ok, "error": r.error, "credits": SB.credits_from_headers(r.headers), **r.stamp(),
                     "bookmakersRequested": list(SB.BOOKMAKERS), "marketsRequested": list(SB.MARKETS)}
        if r.ok:
            for ev in (r.json or []):
                odds_rows.extend([{**stamp, **row} for row in SB.flatten_event(ev, r.stamp())])
                j = SB.join_event(ev, elig_join)
                join_rows.append({**stamp, **r.stamp(), **j})
        else:
            failures.append({"stage": "odds", "error": r.error})
    # 7. MLB state (live feed per eligible game)
    state_rows = []
    state_fetch_failed = 0
    if include_state:
        prev_states = store.previous_states(date)
        for g in eligible:
            r = fetcher.get(MS.live_feed_url(g["gamePk"]))
            if not r.ok:
                state_fetch_failed += 1
                failures.append({"stage": "live_feed", "gamePk": g["gamePk"], "error": r.error})
                continue
            new_state, source_ts = MS.state_from_feed(g, r.json)
            sfp = fingerprint(new_state)
            key = str(g["gamePk"])
            if st["stateFp"].get(key) == sfp:
                continue
            prev = prev_states.get(g["gamePk"]) or {}
            trans = MS.transitions(prev.get("state"), new_state)
            st["stateFp"][key] = sfp
            state_rows.append({**stamp, "gamePk": g["gamePk"], "awayAbbr": g.get("awayAbbr"), "homeAbbr": g.get("homeAbbr"),
                               "observedAt": r.respondedAt, "requestedAt": r.requestedAt, "sourceDate": r.sourceDate,
                               "sourceTimestamp": source_ts, "sourceTimestampSemantics": "feed_last_update_not_event_time",
                               "prevObservedAt": prev.get("observedAt"), "firstObservation": not bool(prev),
                               "fp": sfp, "state": new_state, "transitions": trans})

    # 8. cross-section, reconciliation, manifest
    listed_games = sorted(markets_by_game.keys())
    starvation = RC.family_starvation(listed_games, markets_by_game, policy["coreFamiliesPerGame"])
    listed_pks = {g["gamePk"] for _, _, g in per_game_markets}
    starvation["unlistedEligibleGamePks"] = sorted(g["gamePk"] for g in eligible if g["gamePk"] not in listed_pks)
    books_archived_with_book = sum(1 for b in books_full if b.get("book"))
    recon = RC.build({"seriesListFetchOk": series_list_ok, "seriesSeen": len(series_all), "seriesIncluded": len(included),
                      "seriesByClass": {c: sum(1 for v in classified.values() if v == c) for c in set(classified.values())},
                      "seriesIncomplete": sum(1 for v in series_evidence.values() if not v["pagination"]["complete"]),
                      "seriesFetchFailed": sum(1 for v in series_evidence.values() if v["pagination"]["truncationReason"] == "PAGE_FETCH_FAILED"),
                      "marketsReceived": received, "marketsArchived": counts["archived"], "marketsReferenced": counts["referenced"],
                      "marketsExcluded": counts["excluded"], "booksRequested": books_requested, "booksReceived": books_received,
                      "booksFailed": books_failed, "booksEmptyPayload": books_empty, "booksArchived": len(books_full),
                      "booksReferenced": books_received - books_empty - books_archived_with_book,
                      "eligiblePregameGames": len(eligible), "gamesWithMarkets": len(markets_by_game),
                      "familyStarvationCount": starvation["count"], "stateFetchFailed": state_fetch_failed})
    cross = {**stamp, "cycleStartedAt": started, "gameDate": date, "tickers": digest}
    written = {}
    written["kalshi_quotes"] = store.append_gz("kalshi_quotes", date, quotes_full)
    written["kalshi_books"] = store.append_gz("kalshi_books", date, books_full)
    written["kalshi_crosssection"] = store.append_gz("kalshi_crosssection", date, [cross])
    written["kalshi_trades"] = store.append_gz("kalshi_trades", date, trades_rows)
    written["sportsbook_odds"] = store.append_gz("sportsbook_odds", date, odds_rows)
    written["sportsbook_joins"] = store.append_gz("sportsbook_joins", date, join_rows)
    written["mlb_state"] = store.append_gz("mlb_state", date, state_rows)
    store.save_state(st)
    manifest = {**stamp, "attempt": attempt, "trigger": trigger, "gameDate": date, "cycleStartedAt": started,
                "cycleCompletedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "wallClockSeconds": round(time.time() - t0, 1), "readOnly": True, "ordersPlaced": 0,
                "policyVersion": policy.get("policyVersion"), "reconciliation": recon, "captureClass": recon["captureClass"],
                "researchComplete": recon["researchComplete"], "starvation": starvation, "schedule": sched_fetches,
                "seriesEvidence": {s: {"class": v["class"], "items": v["items"], "pages": v["pagination"]["pages"],
                                       "complete": v["pagination"]["complete"], "truncationReason": v["pagination"]["truncationReason"]}
                                   for s, v in series_evidence.items()},
                "seriesExcludedByPolicy": sorted(s for s, c in classified.items() if c == "EXCLUDED"),
                "seriesUnclassified": sorted(s for s, c in classified.items() if c == "UNCLASSIFIED_MLB"),
                "excludedRows": excluded[:200], "tradesPagination": {k: v for k, v in (trade_pg or {}).items() if k != "attempts"},
                "odds": odds_meta, "joins": {"matched": sum(1 for j in join_rows if j["status"] == "MATCHED"),
                                            "ambiguous": sum(1 for j in join_rows if j["status"] == "AMBIGUOUS"),
                                            "unmatched": sum(1 for j in join_rows if j["status"] == "UNMATCHED")},
                "stateRowsWritten": len(state_rows), "eligibleGamePks": [g["gamePk"] for g in eligible],
                "written": written, "failures": failures, "http": dict(fetcher.stats),
                "collectorVersion": COLLECTOR_VERSION}
    store.write_manifest(date, run_id, manifest)
    return manifest
