"""
Kalshi universe enumeration for the MRV collector.

Every cursor-driven endpoint is paged to EXHAUSTION or the run says exactly
why not: each page attempt is recorded (cursorBefore, cursorAfter, status,
items, error) and a series is `complete` only when the final response
carried no cursor and no page failed.  There is no silent page cap: the
safety maximum is high and, if ever hit, is recorded as truncationReason
"SAFETY_PAGE_CAP" -- the exact defect class the capture-remediation program
(#237/#238) removed from the production capture.
"""
import json
import os

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_LIMIT = 1000
SAFETY_PAGE_CAP = 50
# The trade tape is exchange-wide (no series filter), so it needs far more pages
# than a series: live run 35928372851 exhausted 50 pages x 1000 trades on ~6 of
# its 15 minutes (~9 pages/minute on a Wednesday evening).  A cycle window is
# ~11-14 minutes, so this leaves ~3x headroom; hitting it is still recorded.
TRADES_PAGE_CAP = 400
TRUNCATION_NONE = None

DEFAULT_POLICY = {
    "policyVersion": "mrv_series_policy_v1",
    "perGameBookSeries": ["KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL",
                          "KXMLBF5SPREAD", "KXMLBF3", "KXMLBF7", "KXMLBRFI", "KXMLBINNINGWIN", "KXMLBINNINGTOTAL", "KXMLBEXTRAS"],
    "coreFamiliesPerGame": ["KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL"],
    "propQuoteSeries": ["KXMLBHRR", "KXMLBHR", "KXMLBHIT", "KXMLBRBI", "KXMLBKS", "KXMLBTB", "KXMLBSB", "KXMLBOUTS",
                        "KXMLBHA", "KXMLBWA", "KXMLBWALK", "KXMLBERA", "KXMLBPITCH"],
    "excludedPrefixes": ["KXLEADERMLB", "KXMLBWINS-", "KXMLBAL", "KXMLBNL", "KXMLBWS", "KXMLBDIVWINNER", "KXMLBBESTRECORD",
                         "KXMLBWORSTRECORD", "KXMLBPLAYOFFS", "KXMLBMATCHUP", "KXMLBAWARD", "KXMLBGG", "KXMLBSS",
                         "KXMLBTRIPLECROWN", "KXMLBLSTREAK", "KXMLBWSTREAK", "KXMLBSISTREAK", "KXMLBCOACH", "KXNEXTMANAGERMLB",
                         "KXNEXTTEAMMLB", "KXMLBNEXTTEAM", "KXCOACHOUTMLB", "KXMLBPLAYEROT", "KXMLBPITCHEROTM", "KXMLBEOTY",
                         "KXMLB500", "KXMLBSERIES", "KXMLBRETURN", "KXMLBTRADE", "KXMLBDRAFT", "KXMLBOPENINGDAY", "KXESPYMLB",
                         "KXMEDIACOVERMLBTHESHOW", "KXATTENDMLB", "KXMLBMENTION", "KXMLBFOD", "KXMLBALLSTAR", "KXMLBASG",
                         "KXMLBHRDERBY", "KXMLBFTGAME", "KXMLBSTGAME", "KXMLBNEXT", "KXNCAA", "KXWBC", "KXLMB", "KXEWCMLBB",
                         "KXCONGRESSBASEBALL", "KXMLBCBA", "KXMLBSTRIKE", "KXMLBTEAMSALE", "KXCITYMLBEXPAND", "KXMLBOAK",
                         "MLBCBA", "MLBOAK", "KXMLBSEASON", "KXMLBDEBUT", "KXMLBFASTPITCH", "KXMLBNEXTHR", "KXMLBTEAMSTAT",
                         "KXMLBSTAT", "KXMLBWORLD"],
}


def load_policy(path=None):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return DEFAULT_POLICY


def classify_series(series, policy):
    """-> 'PER_GAME_BOOK' | 'PROP_QUOTE' | 'EXCLUDED' | 'UNCLASSIFIED_MLB'"""
    if series in policy["perGameBookSeries"]:
        return "PER_GAME_BOOK"
    if series in policy["propQuoteSeries"]:
        return "PROP_QUOTE"
    for pre in policy["excludedPrefixes"]:
        if series.startswith(pre):
            return "EXCLUDED"
    return "UNCLASSIFIED_MLB"


def list_mlb_series(fetcher):
    """All series tickers that look MLB-related, plus the fetch evidence."""
    r = fetcher.get("%s/series?limit=%d" % (KALSHI, PAGE_LIMIT))
    out = []
    if r.ok:
        for s in (r.json.get("series") or []):
            t = (s.get("ticker") or "").upper()
            if t.startswith(("KXMLB", "MLB")) or "MLB" in t:
                out.append(t)
    return sorted(set(out)), r


def page_to_exhaustion(fetcher, base_url, item_key, *, page_cap=SAFETY_PAGE_CAP):
    """
    -> (items, pagination) where pagination = {complete, pages, truncationReason,
    finalCursor, attempts:[{page, cursorBefore, cursorAfter, status, items, error,
    requestedAt, respondedAt, sourceDate}]}.  Every item is annotated with the
    timestamps of the page that delivered it (in `_page`).
    """
    items, attempts, cursor = [], [], ""
    truncation = TRUNCATION_NONE
    for page in range(page_cap):
        url = base_url + ("&cursor=" + cursor if cursor else "")
        r = fetcher.get(url)
        att = {"page": page, "cursorBefore": cursor or None, "cursorAfter": None, "status": r.status, "items": 0,
               "error": r.error, "requestedAt": r.requestedAt, "respondedAt": r.respondedAt, "sourceDate": r.sourceDate}
        if not r.ok:
            att["cursorAfter"] = cursor or None
            attempts.append(att)
            truncation = "PAGE_FETCH_FAILED"
            break
        got = r.json.get(item_key) or []
        for it in got:
            it["_page"] = r.stamp()
        items.extend(got)
        cursor = r.json.get("cursor") or ""
        att["items"] = len(got)
        att["cursorAfter"] = cursor or None
        attempts.append(att)
        if not cursor:
            break
        if not got:
            truncation = "CURSOR_WITHOUT_ITEMS"
            break
        if page == page_cap - 1:
            truncation = "SAFETY_PAGE_CAP"
    pagination = {"complete": truncation is None and not cursor, "pages": len(attempts),
                  "truncationReason": truncation, "finalCursor": cursor or None, "attempts": attempts}
    return items, pagination


def fetch_open_markets(fetcher, series):
    url = "%s/markets?series_ticker=%s&status=open&limit=%d" % (KALSHI, series, PAGE_LIMIT)
    return page_to_exhaustion(fetcher, url, "markets")


def fetch_orderbook(fetcher, ticker):
    return fetcher.get("%s/markets/%s/orderbook" % (KALSHI, ticker))


def fetch_trades_since(fetcher, min_ts):
    url = "%s/markets/trades?min_ts=%d&limit=%d" % (KALSHI, int(min_ts), PAGE_LIMIT)
    return page_to_exhaustion(fetcher, url, "trades", page_cap=TRADES_PAGE_CAP)
