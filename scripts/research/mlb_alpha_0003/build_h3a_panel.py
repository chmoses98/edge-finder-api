#!/usr/bin/env python3
"""
H3-A point-in-time panel: one row per (capture run, Kalshi contract) with the executable
book state, the same-run pregame sportsbook anchors, and the settled outcome.

Everything on a row was known at that run's capture time except `outcome`, which is the
label. Rows with a sportsbook event that started before the capture are excluded (the
Odds API serves live lines for games in progress). Doubleheader / ambiguous joins are
excluded, never guessed.

    python3 scripts/research/mlb_alpha_0003/build_h3a_panel.py \
        --prospective-root <.../mlb_alpha_0002/prospective> --out <panel.jsonl.gz>
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.mlb_alpha import corpus, taxonomy as tx  # noqa: E402
from lib.edgelab.research.market_structure import book as B, sharp, settlements  # noqa: E402

FIRST_DATE, LAST_DATE = "2026-09-02", "2026-10-25"
JOIN_TOL_S = 45 * 60
BOOKS = ("pinnacle", "draftkings", "fanduel", "betmgm")


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_anchors(prospective_root):
    """{(runId, away, home): [event anchors]} pregame only."""
    out = collections.defaultdict(list)
    for r in corpus.iter_prospective(prospective_root, "odds"):
        o = sharp.odds_row_probabilities(r)
        if not (o["awayAbbr"] and o["homeAbbr"] and o["commenceTs"]):
            continue
        cap = _ts(r["capturedAt"])
        if cap >= o["commenceTs"]:
            continue
        books = o["books"]
        pin = books.get("pinnacle") or {}
        anchor = {"commenceTs": o["commenceTs"], "capturedTs": cap, "eventId": r.get("eventId"), "pinnacle": None,
                  "consensus": None, "nBooks": len(books)}
        if "pHome" in pin and "totalPoint" in pin:
            anchor["pinnacle"] = {"pHome": 1.0 - pin["pAway"], "line": pin["totalPoint"], "pOver": pin["pOver"],
                                  "h2hUpdate": pin.get("lastUpdate"), "totalUpdate": pin.get("totalLastUpdate")}
            # consensus: mean no-vig home prob over books with h2h; Over prob over books quoting Pinnacle's line
            hp = [v["pHome"] for v in books.values() if "pHome" in v]
            po = [v["pOver"] for v in books.values() if v.get("totalPoint") == pin["totalPoint"] and "pOver" in v]
            anchor["consensus"] = {"pHome": sum(hp) / len(hp), "line": pin["totalPoint"], "pOver": sum(po) / len(po),
                                   "nH2h": len(hp), "nTotal": len(po)}
        out[(r["runId"], o["awayAbbr"], o["homeAbbr"])].append(anchor)
    return out


def load_books(prospective_root):
    """Yield (runId, capturedAt, ticker, orderbook) with change-suppressed rows resolved through fp."""
    by_fp = {}
    changed = []
    for r in corpus.iter_prospective(prospective_root, "books"):
        if r.get("orderbook"):
            by_fp[(r["marketTicker"], r["fp"])] = r["orderbook"]
            changed.append((r["runId"], r["capturedAt"], r["marketTicker"], r["orderbook"]))
    unresolved = 0
    refs = []
    for r in corpus.iter_prospective(prospective_root, "books_unchanged"):
        bk = by_fp.get((r["marketTicker"], r["fp"]))
        if bk is None:
            unresolved += 1
            continue
        refs.append((r["runId"], r["capturedAt"], r["marketTicker"], bk))
    return changed + refs, unresolved


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=REPO)
    ap.add_argument("--prospective-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    anchors = load_anchors(a.prospective_root)
    outcomes = settlements.build_outcomes(a.repo_root)
    rows, unresolved = load_books(a.prospective_root)
    stats = collections.Counter(unresolvedBookRefs=unresolved)
    n_out = 0
    with gzip.open(a.out, "wt") as fh:
        for run_id, cap, ticker, bk in rows:
            p = tx.parse_market_ticker(ticker)
            if not str(p["parseStatus"]).startswith("PARSED") or not p.get("gameKey"):
                stats["unparsed"] += 1
                continue
            gdate = p["gameDate"]
            if gdate < FIRST_DATE or gdate > LAST_DATE:
                stats["outsideDates"] += 1
                continue
            start_ts = p["scheduledStartUtc"].timestamp()
            cap_ts = _ts(cap)
            if cap_ts >= start_ts:
                stats["postStart"] += 1
                continue
            cands = [x for x in anchors.get((run_id, p["awayTeam"], p["homeTeam"]), [])
                     if abs(x["commenceTs"] - start_ts) <= JOIN_TOL_S]
            if len(cands) != 1:
                stats["anchor_none" if not cands else "anchor_ambiguous"] += 1
                continue
            an = cands[0]
            if an["pinnacle"] is None:
                stats["no_pinnacle_pair"] += 1
                continue
            yb, ya, ybs, yas = B.top_of_book(bk)
            walk_yes = B.walk_buy(bk, "YES", 1000)       # $10 principal
            walk_no = B.walk_buy(bk, "NO", 1000)
            res = outcomes.get(ticker)
            row = {"runId": run_id, "capturedAt": cap, "minToStart": round((start_ts - cap_ts) / 60.0, 2),
                   "ticker": ticker, "series": p["seriesTicker"], "gameKey": p["gameKey"], "gameDate": gdate,
                   "away": p["awayTeam"], "home": p["homeTeam"], "selection": p.get("selection"),
                   "threshold": p.get("threshold"), "inning": p.get("inning"),
                   "yesBidC": yb, "yesAskC": ya, "yesBidSize": ybs, "yesAskSize": yas,
                   "buyYes10": {k: walk_yes[k] for k in ("contracts", "avgPriceCents", "filled", "depthAtTop")},
                   "buyNo10": {k: walk_no[k] for k in ("contracts", "avgPriceCents", "filled", "depthAtTop")},
                   "anchor": {"pinnacle": an["pinnacle"], "consensus": an["consensus"], "nBooks": an["nBooks"],
                              "oddsCapturedTs": an["capturedTs"]},
                   "outcome": res}
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            n_out += 1
    stats["rowsWritten"] = n_out
    print(json.dumps(dict(stats), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
