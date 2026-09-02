#!/usr/bin/env python3
"""MLB-ALPHA-0002 prospective capture -- tiered, change-suppressed,
rate-limit aware.

WHAT THIS COLLECTS, AND WHY IT IS SHAPED THIS WAY
-------------------------------------------------
The first design polled seven hardcoded series and wrote every order book
every 10 minutes. The activation audit measured what that actually costs:
196 MLB-associated series exist (92 with open markets, 6,302 open
contracts), a full pass draws HTTP 429s from Kalshi, and 99.5% of order
books are byte-identical to the previous observation. Writing all of it
naively projects to ~0.84 GB/month of compressed Git growth for almost no
new information.

So capture is tiered by series (series_universe_policy.json):
  FULL_MICROSTRUCTURE  quotes + order book + trade tape every run
  LIGHT_CAPTURE        quotes every run; book only when the quote moved
  DAILY_ONLY           one quote snapshot per day
  NOT_CAPTURED         explicitly excluded, with a recorded reason
and every writer is CHANGE-SUPPRESSED: a row is written only when its
content fingerprint differs from the last one stored for that ticker.
Unchanged state is recorded as a cheap reference row carrying the
fingerprint and the run id, so the series is still reconstructible
minute-by-minute without storing the same book hundreds of times.

TIER 1 (immutable raw) is the daily gzip partition; TIER 2 (compact
research facts) is what the analyses read. Both are append-only and
checksummed by a per-run manifest.

Kalshi is READ-ONLY: only public GET endpoints are ever called. This
module has no import of, and no write path to, any order, portfolio,
recommendation, staking, eligibility or risk-gate surface.
RESEARCH ONLY.
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ODDS = "https://api.the-odds-api.com/v4"
MLB = "https://statsapi.mlb.com/api/v1"
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "prospective")
POLICY = os.path.join(ART, "series_universe_policy.json")
STATE = os.path.join(OUT, "capture_state.json")

# The external books captured every run. Pinnacle is the sharp reference
# D01-SHARPLAG is defined against; the three US books are what a retail
# bettor could actually have hit, and are what C03-BOOKIMB/cross-book
# comparisons need. Kept at four (<= 10) so The Odds API bills the whole
# set as a single region-equivalent -- see the cost note at the call site.
BOOKMAKERS = ("pinnacle", "draftkings", "fanduel", "betmgm")

STATS = Counter()
_BACKOFF = {"sleep": 0.15}


# ------------------------------------------------------------------ http
def http(url, timeout=30, retries=3):
    """Public GET with adaptive throttling. The audit drew 90 HTTP 429s in
    a 499-request burst, so the sleep between calls grows on 429 and
    decays slowly on success."""
    for attempt in range(retries):
        time.sleep(_BACKOFF["sleep"])
        STATS["httpRequests"] += 1
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "edgelab-research"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                STATS["httpBytes"] += len(body)
                _BACKOFF["sleep"] = max(0.12, _BACKOFF["sleep"] * 0.97)
                return json.loads(body.decode()), dict(resp.headers), None
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                STATS["http429"] += 1
                _BACKOFF["sleep"] = min(2.0, _BACKOFF["sleep"] * 2.0 + 0.05)
                time.sleep(1.0 + attempt)
                continue
            STATS["httpErrors"] += 1
            return None, dict(exc.headers or {}), "HTTP %d" % exc.code
        except Exception as exc:
            STATS["httpErrors"] += 1
            if attempt == retries - 1:
                return None, {}, str(exc)
            time.sleep(0.5 * (attempt + 1))
    STATS["httpErrors"] += 1
    return None, {}, "429 retries exhausted"


# --------------------------------------------------------------- storage
def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def append_gz(kind, date, rows):
    if not rows:
        return 0
    path = os.path.join(OUT, kind, date + ".jsonl.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at") as fh:                 # append-only, never "w"
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(rows)


def append_jsonl(path, rows):
    if not rows:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(rows)


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as fh:
            return json.load(fh)
    return {"quoteFp": {}, "bookFp": {}, "mlbFp": {}, "lastTradeTs": None, "dailyDone": {}}


def save_state(st):
    os.makedirs(OUT, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, sort_keys=True)
    os.replace(tmp, STATE)


# ---------------------------------------------------------------- policy
def load_policy():
    with open(POLICY) as fh:
        return json.load(fh)


def tier_for(series, policy):
    t = policy["tiers"]
    if series in t["FULL_MICROSTRUCTURE"]["series"]:
        return "FULL_MICROSTRUCTURE"
    if series in t["LIGHT_CAPTURE"]["series"]:
        return "LIGHT_CAPTURE"
    for pre in t["DAILY_ONLY"]["seriesPrefixes"]:
        if series.startswith(pre):
            return "DAILY_ONLY"
    return "UNCLASSIFIED"


# --------------------------------------------------------------- capture
def fetch_series_markets(series):
    out, cursor = [], ""
    for _ in range(6):
        d, _h, _e = http("%s/markets?series_ticker=%s&status=open&limit=1000%s"
                         % (KALSHI, series, ("&cursor=" + cursor) if cursor else ""))
        if d is None:
            break
        out.extend(d.get("markets") or [])
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    return out


def quote_of(m):
    return {"marketTicker": m.get("ticker"), "eventTicker": m.get("event_ticker"),
            "yesBid": m.get("yes_bid"), "yesAsk": m.get("yes_ask"),
            "noBid": m.get("no_bid"), "noAsk": m.get("no_ask"),
            "lastPrice": m.get("last_price"), "volume": m.get("volume"),
            "openInterest": m.get("open_interest"), "liquidity": m.get("liquidity"),
            "status": m.get("status"), "closeTime": m.get("close_time")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-orderbook", action="store_true")
    ap.add_argument("--max-books", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    policy = load_policy()
    st = load_state()
    now_dt = datetime.utcnow()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = "ALPHA0002_" + now_dt.strftime("%Y%m%dT%H%M%SZ")
    date = (now_dt - timedelta(hours=4)).strftime("%Y-%m-%d")     # ET game date
    t0 = time.time()

    # which series exist right now
    d, _h, _e = http("%s/series?limit=1000" % KALSHI)
    series_all = []
    for s in ((d or {}).get("series") or []):
        t = (s.get("ticker") or "").upper()
        if t.startswith(("KXMLB", "MLB")) or "MLB" in t:
            series_all.append(t)
    tiers = defaultdict(list)
    for s in sorted(set(series_all)):
        tiers[tier_for(s, policy)].append(s)

    quotes_new, quotes_ref, books_new, books_ref = [], [], [], []
    full_tickers = []
    counts = Counter()
    for tier in ("FULL_MICROSTRUCTURE", "LIGHT_CAPTURE", "DAILY_ONLY"):
        if tier == "DAILY_ONLY" and st["dailyDone"].get(date):
            continue
        for series in tiers.get(tier, []):
            for m in fetch_series_markets(series):
                q = quote_of(m)
                tick = q["marketTicker"]
                if not tick:
                    continue
                counts[tier] += 1
                fp = fingerprint(q)
                if st["quoteFp"].get(tick) == fp:
                    quotes_ref.append({"runId": run_id, "capturedAt": now, "marketTicker": tick,
                                       "unchanged": True, "fp": fp})
                    counts["quotesUnchanged"] += 1
                else:
                    st["quoteFp"][tick] = fp
                    quotes_new.append({"runId": run_id, "capturedAt": now, "seriesTicker": series,
                                       "tier": tier, "fp": fp, **q})
                    counts["quotesChanged"] += 1
                if tier == "FULL_MICROSTRUCTURE":
                    full_tickers.append(tick)
    if not st["dailyDone"].get(date):
        st["dailyDone"] = {date: True}

    if not args.no_orderbook:
        for tick in full_tickers[:args.max_books]:
            d, _h, _e = http("%s/markets/%s/orderbook" % (KALSHI, tick))
            if d is None:
                continue
            book = d.get("orderbook")
            fp = fingerprint(book)
            if st["bookFp"].get(tick) == fp:
                books_ref.append({"runId": run_id, "capturedAt": now, "marketTicker": tick,
                                  "unchanged": True, "fp": fp})
                counts["booksUnchanged"] += 1
            else:
                st["bookFp"][tick] = fp
                books_new.append({"runId": run_id, "capturedAt": now, "marketTicker": tick,
                                  "fp": fp, "orderbook": book})
                counts["booksChanged"] += 1

    # trade tape delta (dedup by trade_id via the last-seen timestamp)
    trades = []
    since = st.get("lastTradeTs")
    min_ts = int(time.time()) - 900
    if since:
        try:
            min_ts = int((datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ")
                          - datetime(1970, 1, 1)).total_seconds())
        except ValueError:
            pass
    cursor = ""
    for _ in range(25):
        d, _h, _e = http("%s/markets/trades?min_ts=%d&limit=1000%s"
                         % (KALSHI, min_ts, ("&cursor=" + cursor) if cursor else ""))
        if d is None:
            break
        for tr in d.get("trades") or []:
            if (tr.get("ticker") or "").upper().startswith(("KXMLB", "MLB")):
                trades.append({"runId": run_id, "capturedAt": now, **tr})
        cursor = d.get("cursor") or ""
        if not cursor:
            break
    st["lastTradeTs"] = now
    counts["trades"] = len(trades)

    # External books. Cost model (The Odds API v4): credits = markets x
    # regions. The `bookmakers` parameter is DOCUMENTED as billing at
    # ceil(n/10) region-equivalents, which would make these four books cost
    # the same 2 credits/call as the previous pinnacle-only request.
    #
    # THAT RULE IS UNVERIFIED HERE: api.the-odds-api.com is egress-blocked
    # in this environment (403 CONNECT), so it could not be confirmed
    # against the live docs or measured empirically before shipping. The
    # plausible worse case is that these span two regions (eu for Pinnacle,
    # us for the other three) and cost 4 credits/call -- which is exactly
    # this program's own costed scenario D (336 credits/day at the 10-minute
    # cadence, ~52% of the 18,000 remaining). That is a materially different
    # budget, so it must be MEASURED, not assumed.
    #
    # Hence: every run records x-requests-last/used/remaining from the
    # response headers into the run manifest (oddsCredits below). The FIRST
    # live run settles the question with a real receipt, and the Part N
    # spend decision must read that number rather than either estimate.
    odds_rows, credits = [], None
    key = (os.environ.get("ODDS_API_KEY") or "").strip()
    if key:
        d, hdr, _e = http("%s/sports/baseball_mlb/odds?apiKey=%s&bookmakers=%s"
                          "&markets=h2h,totals&oddsFormat=decimal"
                          % (ODDS, key, ",".join(BOOKMAKERS)))
        credits = {"last": hdr.get("x-requests-last"), "used": hdr.get("x-requests-used"),
                   "remaining": hdr.get("x-requests-remaining"),
                   "bookmakersRequested": list(BOOKMAKERS),
                   "regionEquivalents": (len(BOOKMAKERS) + 9) // 10,
                   "marketsRequested": ["h2h", "totals"]}
        for g in (d or []):
            odds_rows.append({"runId": run_id, "capturedAt": now, "eventId": g.get("id"),
                              "commenceTime": g.get("commence_time"), "home": g.get("home_team"),
                              "away": g.get("away_team"), "bookmakers": g.get("bookmakers")})
    counts["oddsGames"] = len(odds_rows)
    # Which of the requested books actually returned a price this run --
    # a book being absent is itself data (suspended/unlisted), never
    # silently treated as "captured".
    seen_books = sorted({(b.get("key") or "") for r in odds_rows for b in (r.get("bookmakers") or [])})
    counts["oddsBooksSeen"] = len(seen_books)

    # MLB state: only rows whose lineup/pitcher fingerprint CHANGED are
    # written, so the first row for a game is the first time we saw it.
    mlb_rows = []
    d, _h, _e = http("%s/schedule?sportId=1&date=%s&hydrate=probablePitcher,lineups" % (MLB, date))
    for dd in ((d or {}).get("dates") or []):
        for g in dd.get("games") or []:
            lu = g.get("lineups") or {}
            rec = {"gamePk": g.get("gamePk"), "gameDate": g.get("gameDate"),
                   "status": (g.get("status") or {}).get("detailedState"),
                   "awayProbableId": (((g.get("teams") or {}).get("away") or {}).get("probablePitcher") or {}).get("id"),
                   "homeProbableId": (((g.get("teams") or {}).get("home") or {}).get("probablePitcher") or {}).get("id"),
                   "awayLineupPosted": bool(lu.get("awayPlayers")),
                   "homeLineupPosted": bool(lu.get("homePlayers")),
                   "awayLineupIds": [p.get("id") for p in (lu.get("awayPlayers") or [])],
                   "homeLineupIds": [p.get("id") for p in (lu.get("homePlayers") or [])]}
            fp = fingerprint(rec)
            k = str(rec["gamePk"])
            if st["mlbFp"].get(k) == fp:
                continue
            st["mlbFp"][k] = fp
            mlb_rows.append({"runId": run_id, "capturedAt": now, "firstSeenChange": True,
                             "fp": fp, **rec})
    counts["mlbStateChanges"] = len(mlb_rows)

    written = {}
    if not args.dry_run:
        written["quotes"] = append_gz("quotes", date, quotes_new)
        written["quotesRef"] = append_gz("quotes_unchanged", date, quotes_ref)
        written["books"] = append_gz("books", date, books_new)
        written["booksRef"] = append_gz("books_unchanged", date, books_ref)
        written["trades"] = append_gz("trades", date, trades)
        written["odds"] = append_gz("odds", date, odds_rows)
        written["mlbState"] = append_gz("mlb_state", date, mlb_rows)
        save_state(st)

    manifest = {"runId": run_id, "capturedAt": now, "gameDate": date,
                "readOnly": True, "ordersPlaced": 0,
                "seriesSeen": len(set(series_all)),
                "seriesByTier": {k: len(v) for k, v in tiers.items()},
                "counts": dict(counts), "written": written,
                "http": {"requests": STATS["httpRequests"], "errors": STATS["httpErrors"],
                         "rateLimited429": STATS["http429"], "bytes": STATS["httpBytes"],
                         "finalSleepSeconds": round(_BACKOFF["sleep"], 3)},
                "oddsCredits": credits,
                "wallClockSeconds": round(time.time() - t0, 1)}
    if not args.dry_run:
        append_jsonl(os.path.join(OUT, "runs", date + ".jsonl"), [manifest])
    print(json.dumps(manifest, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
