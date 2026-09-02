#!/usr/bin/env python3
"""MLB-ALPHA-0002: recover Kalshi's OWN exchange record for archived MLB
contracts -- 1-minute candlesticks (yes bid/ask/price OHLC, volume, open
interest) and the public trade tape -- for the settled August universe.

WHY: the in-repo observation archive is a ~10-minute polling capture with
no trade prints and no depth. Kalshi publishes its own minute-resolution
candles and every executed trade through documented public endpoints.
That is the exchange's record, not a reconstruction, so it is admissible
historical data for Family C (microstructure / momentum / staleness).

READ-ONLY. Public GET endpoints only, no credentials, no order paths.
Kalshi egress is blocked in the research sandbox, so this runs in GitHub
Actions (research-kalshi-history-recovery.yml) and commits append-only
artifacts to a research branch, never main.

Storage (raw JSON preserved -- never re-shaped so later readers can
re-parse exactly what the exchange returned):
  data/edgelab/research_artifacts/mlb_alpha_0002/kalshi_history/
      candles/<gameDate>.jsonl.gz   one line per ticker: {ticker, series,
                                     gameDate, startTs, endTs, fetchedAt,
                                     candlesticks:[raw...]}
      trades/<gameDate>.jsonl.gz    one line per ticker: {ticker, ...,
                                     trades:[raw...]}
      recovery_manifest.json        per-ticker status (done/empty/error)
Idempotent: tickers already present in the manifest with status done or
empty are skipped, so the job can be re-run to resume after a timeout.
"""

import argparse
import glob
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab.mlb_alpha_identity import parse_event_ticker  # noqa: E402

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002",
                   "kalshi_history")
MANIFEST = os.path.join(OUT, "recovery_manifest.json")
SETTLEMENTS = os.path.join(REPO, "data", "edgelab", "settlements")

# Core families first: these carry the liquidity. Player props (~95k
# tickers) are a second pass if the core recovery proves useful.
CORE_FAMILIES = ["game_result", "game_total", "inning_result", "inning_total",
                 "team_total", "winning_margin", "first_inning_run",
                 "pitcher_strikeouts", "pitcher_outs"]
SLEEP = 0.13          # ~7.5 req/s, under Kalshi's documented basic read limit
CANDLE_WINDOW_BEFORE_H = 72
CANDLE_WINDOW_AFTER_H = 8


def http_json(url, retries=3, timeout=30):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "edgelab-research"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode()), None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None, "HTTP %d" % exc.code
        except Exception as exc:  # network blips
            time.sleep(1.0 * (attempt + 1))
            last = str(exc)
    return None, "retries exhausted: %s" % locals().get("last", "?")


def iter_settled_tickers(families, dates):
    """Yield (ticker, family, gameDate, scheduledStartUtc) from the settlement store."""
    seen = set()
    for p in sorted(glob.glob(os.path.join(SETTLEMENTS, "*.jsonl*"))):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.jsonl", os.path.basename(p))
        if not m:
            continue
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                fam = r.get("marketFamily")
                if families and fam not in families:
                    continue
                t = r.get("marketTicker")
                if not t or t in seen:
                    continue
                ident = parse_event_ticker(t.rsplit("-", 1)[0])
                if ident.get("status") != "RESOLVED":
                    continue
                gd = ident["gameDate"]
                if dates and gd not in dates:
                    continue
                seen.add(t)
                yield t, fam, gd, ident["scheduledStartUtc"]


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            return json.load(fh)
    return {"program": "MLB-ALPHA-0002", "readOnly": True, "tickers": {}}


def save_manifest(man):
    os.makedirs(OUT, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(man, fh, sort_keys=True)
    os.replace(tmp, MANIFEST)


def append_gz(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at") as fh:                 # append-only
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def fetch_candles(ticker, start_ts, end_ts):
    series = ticker.split("-", 1)[0]
    url = ("%s/series/%s/markets/%s/candlesticks?start_ts=%d&end_ts=%d&period_interval=1"
           % (KALSHI_API, series, ticker, start_ts, end_ts))
    js, err = http_json(url)
    if js is None:
        return None, err
    return js.get("candlesticks") or [], None


def fetch_trades(ticker, min_ts, max_ts):
    out, cursor = [], ""
    while True:
        url = ("%s/markets/trades?ticker=%s&min_ts=%d&max_ts=%d&limit=1000%s"
               % (KALSHI_API, ticker, min_ts, max_ts,
                  ("&cursor=" + cursor) if cursor else ""))
        js, err = http_json(url)
        if js is None:
            return None, err
        out.extend(js.get("trades") or [])
        cursor = js.get("cursor") or ""
        if not cursor:
            return out, None
        time.sleep(SLEEP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", default=",".join(CORE_FAMILIES))
    ap.add_argument("--dates", default="", help="comma list of gameDates; default all")
    ap.add_argument("--max-tickers", type=int, default=0)
    ap.add_argument("--budget-minutes", type=float, default=300)
    ap.add_argument("--skip-trades", action="store_true")
    args = ap.parse_args()
    families = [f for f in args.families.split(",") if f]
    dates = set(d for d in args.dates.split(",") if d)

    man = load_manifest()
    done = man["tickers"]
    t0 = time.time()
    n = n_new = n_err = 0
    for ticker, fam, gd, start in iter_settled_tickers(families, dates):
        if args.max_tickers and n_new >= args.max_tickers:
            break
        if (time.time() - t0) / 60.0 > args.budget_minutes:
            print("budget reached; stopping (resumable)")
            break
        n += 1
        st = done.get(ticker, {}).get("status")
        if st in ("done", "empty"):
            continue
        s_ts = int((start - timedelta(hours=CANDLE_WINDOW_BEFORE_H)
                    - datetime(1970, 1, 1)).total_seconds())
        e_ts = int((start + timedelta(hours=CANDLE_WINDOW_AFTER_H)
                    - datetime(1970, 1, 1)).total_seconds())
        fetched = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        candles, err = fetch_candles(ticker, s_ts, e_ts)
        time.sleep(SLEEP)
        rec = {"ticker": ticker, "family": fam, "gameDate": gd, "status": "done",
               "candles": None, "trades": None, "fetchedAt": fetched}
        if candles is None:
            rec["status"] = "error"; rec["error"] = "candles: " + str(err); n_err += 1
        else:
            rec["candles"] = len(candles)
            if candles:
                append_gz(os.path.join(OUT, "candles", gd + ".jsonl.gz"),
                          {"ticker": ticker, "family": fam, "gameDate": gd,
                           "startTs": s_ts, "endTs": e_ts, "fetchedAt": fetched,
                           "candlesticks": candles})
        if not args.skip_trades and rec["status"] != "error":
            trades, err = fetch_trades(ticker, s_ts, e_ts)
            time.sleep(SLEEP)
            if trades is None:
                rec["status"] = "error"; rec["error"] = "trades: " + str(err); n_err += 1
            else:
                rec["trades"] = len(trades)
                if trades:
                    append_gz(os.path.join(OUT, "trades", gd + ".jsonl.gz"),
                              {"ticker": ticker, "family": fam, "gameDate": gd,
                               "startTs": s_ts, "endTs": e_ts, "fetchedAt": fetched,
                               "trades": trades})
        if rec["status"] == "done" and not rec["candles"] and not rec["trades"]:
            rec["status"] = "empty"
        done[ticker] = rec
        n_new += 1
        if n_new % 200 == 0:
            save_manifest(man)
            print("  %d tickers fetched (%d errors) %.1f min" % (n_new, n_err, (time.time() - t0) / 60))
    save_manifest(man)
    print("scanned %d tickers, fetched %d new, %d errors; manifest %s" % (n, n_new, n_err, MANIFEST))
    return 0


if __name__ == "__main__":
    sys.exit(main())
