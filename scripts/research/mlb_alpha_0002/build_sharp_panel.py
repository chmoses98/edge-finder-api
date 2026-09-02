#!/usr/bin/env python3
"""MLB-ALPHA-0002 PIT feature layer, part 2: the external sharp-market panel.

Source: every timestamped slate snapshot under data/slates/<date>/*.json
and the frozen normalized_slate.json.gz copies under
data/edgelab/snapshots/<date>/*/<ts>/frozen/. Each carries, per game and
per book (pinnacle, draftkings, fanduel, betmgm), moneyline / total /
F5 moneyline prices with the BOOK's own `updated` timestamp, plus the
Kalshi tickers for the same game.

AS-OF RULE (conservative): a book price is "known" at the snapshot's
CAPTURE time (when the repo fetched it), never at the book's `updated`
time -- we could not have acted on a change before we saw it. `updated`
is kept as the event time for lead/lag measurement.

Vig removal: two-sided multiplicative de-vig on the book's two prices.
Outputs one row per (game, snapshot, book) with vig-free home/over
probabilities. RESEARCH ONLY.
"""

import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "pit_sharp_panel.jsonl.gz")
BOOKS = ("pinnacle", "draftkings", "fanduel", "betmgm")
TS_RE = re.compile(r"(\d{8}T\d{6}Z)")


def american_to_implied(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return 100.0 / (a + 100.0) if a > 0 else (-a) / ((-a) + 100.0)


def devig(p1, p2):
    if p1 is None or p2 is None or (p1 + p2) <= 0:
        return None, None
    s = p1 + p2
    return p1 / s, p2 / s


def snapshot_files():
    out = []
    for p in sorted(glob.glob(os.path.join(REPO, "data", "slates", "*", "*.json"))):
        date = os.path.basename(os.path.dirname(p))
        m = TS_RE.search(os.path.basename(p))
        out.append((date, p, m.group(1) if m else None, "slates:" + os.path.basename(p).split("_")[0]))
    for p in sorted(glob.glob(os.path.join(REPO, "data", "edgelab", "snapshots", "*", "*", "*", "frozen",
                                           "normalized_slate.json.gz"))):
        parts = p.split(os.sep)
        date, kind, ts = parts[-5], parts[-4], parts[-3]
        m = TS_RE.search(ts)
        out.append((date, p, m.group(1) if m else None, "frozen:" + kind))
    return out


def load_json(p):
    opener = gzip.open if p.endswith(".gz") else open
    with opener(p, "rt") as fh:
        return json.load(fh)


def norm_ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def main():
    rows = []
    files = snapshot_files()
    n_files = 0
    for date, path, ts, kind in files:
        try:
            d = load_json(path)
        except Exception:
            continue
        # frozen normalized_slate.json.gz wraps the slate as {"meta", "data"}
        if isinstance(d, dict) and "data" in d and "games" not in d:
            d = d["data"] or {}
        games = d.get("games") or []
        if not games:
            continue
        cap = norm_ts(ts)
        cap_basis = "filename"
        if cap is None:
            lc = [g.get("lineupCheckedAt") for g in games if g.get("lineupCheckedAt")]
            cap = max(lc) if lc else None
            cap_basis = "max_lineupCheckedAt"
        if cap is None:
            continue
        n_files += 1
        for g in games:
            k = (g.get("odds") or {}).get("kalshi") or {}
            ml = k.get("ml") or {}
            ev = None
            for key in ("away_ticker", "home_ticker"):
                if ml.get(key):
                    ev = ml[key].rsplit("-", 1)[0]
                    break
            base = {"gameDate": date, "capturedAt": cap, "captureBasis": cap_basis,
                    "snapshotKind": kind, "kalshiEventTicker": ev,
                    "gameKey": (date + ":" + ev.split("-", 1)[1]) if ev else None,
                    "away": g.get("away"), "home": g.get("home"),
                    "startTime": g.get("startTime"), "oddsApiCommenceTime": g.get("oddsApiCommenceTime"),
                    "lineupStatus": g.get("lineupStatus"), "lineupConfirmed": g.get("lineupConfirmed"),
                    "lineupCheckedAt": g.get("lineupCheckedAt"),
                    "kalshiMlAway": ml.get("away"), "kalshiMlHome": ml.get("home")}
            for book in BOOKS:
                b = (g.get("odds") or {}).get(book)
                if not b:
                    continue
                r = dict(base); r["book"] = book
                bml = b.get("ml") or {}
                pa, ph = american_to_implied(bml.get("away")), american_to_implied(bml.get("home"))
                va, vh = devig(pa, ph)
                r.update({"mlAway": bml.get("away"), "mlHome": bml.get("home"), "mlUpdated": bml.get("updated"),
                          "mlHomeVigFree": round(vh, 5) if vh is not None else None,
                          "mlOverround": round(pa + ph - 1.0, 5) if (pa is not None and ph is not None) else None})
                tot = b.get("total") or {}
                po, pu = american_to_implied(tot.get("over")), american_to_implied(tot.get("under"))
                vo, vu = devig(po, pu)
                r.update({"totalLine": tot.get("line"), "totalOver": tot.get("over"), "totalUnder": tot.get("under"),
                          "totalUpdated": tot.get("updated"),
                          "totalOverVigFree": round(vo, 5) if vo is not None else None})
                f5 = b.get("f5ml") or {}
                fa, fh_ = american_to_implied(f5.get("away")), american_to_implied(f5.get("home"))
                _, vfh = devig(fa, fh_)
                r.update({"f5MlAway": f5.get("away"), "f5MlHome": f5.get("home"), "f5MlUpdated": f5.get("updated"),
                          "f5MlHomeVigFree": round(vfh, 5) if vfh is not None else None})
                rows.append(r)
    # dedupe exact duplicates (same game, capture, book, prices)
    seen, ded = set(), []
    for r in rows:
        key = (r["gameKey"], r["capturedAt"], r["book"], r.get("mlAway"), r.get("mlHome"), r.get("totalLine"), r.get("totalOver"))
        if key in seen:
            continue
        seen.add(key); ded.append(r)
    ded.sort(key=lambda r: (r["gameDate"], r.get("gameKey") or "", r["capturedAt"], r["book"]))
    os.makedirs(ART, exist_ok=True)
    with gzip.open(OUT, "wt") as fh:
        for r in ded:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    per_date = defaultdict(set)
    for r in ded:
        per_date[r["gameDate"]].add(r["capturedAt"])
    meta = {"programId": "MLB-ALPHA-0002", "artifact": os.path.relpath(OUT, REPO), "rows": len(ded),
            "filesRead": n_files, "dates": len(per_date),
            "snapshotsPerDate": {d: len(v) for d, v in sorted(per_date.items())},
            "books": list(BOOKS), "asOfRule": "known at snapshot capture time; book `updated` kept as event time",
            "devig": "two-sided multiplicative"}
    with open(os.path.join(ART, "pit_sharp_panel.meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps({k: v for k, v in meta.items() if k != "snapshotsPerDate"}, indent=1))
    aug = {d: n for d, n in meta["snapshotsPerDate"].items() if d.startswith("2026-08")}
    print("August snapshots per date:", aug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
