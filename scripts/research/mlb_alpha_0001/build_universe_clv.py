#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section A: universe-wide CLV for the COMPLETE eligible
Kalshi MLB archive (not merely placed bets).

RESEARCH ONLY. Production CLV behaviour is untouched: this script writes
one new research artifact and imports lib.edgelab.checkpoints'
select_closing_quote so official closing-quote selection semantics are
REUSED, not reinvented.

SIGN CONVENTION (stated explicitly because it is the OPPOSITE of the
repo's production CLV formula -- see "Production sign note" below):

    clvCents = closingExecutable - entryExecutable

    POSITIVE clvCents = GOOD. You bought below where the market closed:
    the price moved TOWARD your entry after you bought it.

  BUY YES : entry = yesAsk                     close = closing yesAsk
  BUY NO  : entry = noAsk if archived else 100 - yesBid
            close = closing noAsk if archived else 100 - closing yesBid

  Midpoint is NEVER used, on either leg.

Production sign note (read-only observation, nothing changed): both
lib.edgelab.clv.compute_clv_for_bet and scripts/clv_from_snapshot.py
compute `entry_implied - closing_implied`, i.e. the NEGATION of the
convention above, while lib/edgelab/clv.py's own docstring describes a
positive value as "entered at a better (cheaper) price than the close".
For a buyer, entering cheaper than the close means closing > entry, which
that formula scores NEGATIVE. This research artifact does not depend on
resolving that; it is reported to the maintainers as a separate question.

A CLV row is NEVER fabricated. Excluded, with an explicit reason:
post-start quotes, missing executable entry price, no valid closing
quote, unresolved market identity.

The BLIND HOLDOUT is never read.
"""

import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from lib.edgelab.checkpoints import select_closing_quote  # noqa: E402  (REUSED)
from scripts.research.mlb_alpha_0001.build_entry_rows import (  # noqa: E402
    parse_event, parse_ts, iter_jsonl, partition_paths)

SEED = 20260904
BOOT = 2000
# The standardized pregame checkpoints Section A asks for. LAST_PREGAME is
# deliberately NOT here: it is the same quote that defines the close, so
# its CLV is mechanically ~0 (quantified separately in the artifact).
STANDARD_CHECKPOINTS = ("FIRST_DAILY", "T_MINUS_90", "T_MINUS_60",
                        "T_MINUS_30", "LINEUP_CONFIRMATION",
                        "T_MINUS_15", "T_MINUS_5")


def band_of(cents):
    b = int(cents // 10) * 10
    return "%02d-%02d" % (min(b, 90), min(b + 10, 100))


def exec_prices(q):
    """(buy-YES executable, buy-NO executable) in cents, or None each."""
    ya, yb = q.get("yesAsk"), q.get("yesBid")
    no_ask = q.get("noAsk")
    yes_exec = ya if ya is not None else None
    if no_ask is not None:
        no_exec = no_ask
    else:
        no_exec = (100.0 - yb) if yb is not None else None
    return yes_exec, no_exec


def main():
    rng = np.random.default_rng(SEED)
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    dates = list(splits["discovery"]["dates"]) + list(splits["validation"]["dates"])
    parts = partition_paths("observations")

    out_path = os.path.join(ART, "universe_clv.jsonl.gz")
    unavailable = Counter()
    groups = defaultdict(lambda: {"clv": [], "games": defaultdict(list)})
    totals = Counter()
    contracts_seen = set()
    games_seen = set()
    dates_seen = set()
    lastpregame_clv = []

    with gzip.open(out_path, "wt") as out:
        for date in dates:
            if date not in parts:
                continue
            by_ticker = defaultdict(list)
            for r in iter_jsonl(parts[date]):
                ev = parse_event(r.get("eventTicker"))
                if ev is None:
                    unavailable["UNRESOLVED_MARKET_IDENTITY"] += 1
                    continue
                game_date, start_utc, _ = ev
                if game_date != date:
                    continue
                by_ticker[r["marketTicker"]].append((start_utc, r))

            for ticker, pairs in by_ticker.items():
                start_utc = pairs[0][0]
                quotes = [q for _, q in pairs]
                pregame = []
                for q in quotes:
                    if parse_ts(q["capturedAt"]) >= start_utc:
                        unavailable["POST_START_QUOTE"] += 1
                        continue
                    pregame.append(q)
                if not pregame:
                    continue
                # REUSED production closing-quote semantics.
                closing = select_closing_quote(
                    pregame, scheduled_start=start_utc.isoformat() + "Z")
                if closing is None:
                    unavailable["NO_VALID_CLOSING_QUOTE"] += len(pregame)
                    continue
                c_yes, c_no = exec_prices(closing)
                if c_yes is None and c_no is None:
                    unavailable["CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE"] += len(pregame)
                    continue

                ev_suffix = closing["eventTicker"].split("-", 1)[1]
                game_key = date + ":" + ev_suffix

                for q in pregame:
                    cp = q.get("checkpoint")
                    is_close = q["capturedAt"] == closing["capturedAt"]
                    e_yes, e_no = exec_prices(q)
                    for side, entry_c, close_c in (("BUY_YES", e_yes, c_yes),
                                                   ("BUY_NO", e_no, c_no)):
                        if entry_c is None or close_c is None:
                            unavailable["MISSING_EXECUTABLE_ENTRY"] += 1
                            continue
                        if not (1 <= entry_c <= 99):
                            unavailable["ENTRY_PRICE_UNEXECUTABLE"] += 1
                            continue
                        clv = round(close_c - entry_c, 2)
                        if is_close:
                            lastpregame_clv.append(clv)
                            continue          # mechanically the close itself
                        if cp not in STANDARD_CHECKPOINTS:
                            unavailable["NON_STANDARD_CHECKPOINT"] += 1
                            continue
                        row = {
                            "programId": "MLB-ALPHA-0001",
                            "gameDate": date,
                            "eventTicker": q["eventTicker"],
                            "gameId": q.get("gameId"),
                            "marketTicker": ticker,
                            "marketFamily": q.get("marketFamily"),
                            "side": side,
                            "entryCheckpoint": cp,
                            "entryCapturedAt": q["capturedAt"],
                            "minutesToStart": round(
                                (start_utc - parse_ts(q["capturedAt"])).total_seconds() / 60.0, 1),
                            "entryExecutableCents": entry_c,
                            "closingCapturedAt": closing["capturedAt"],
                            "closingExecutableCents": close_c,
                            "clvCents": clv,
                            "beatClose": bool(clv > 0),
                            "yesBid": q.get("yesBid"),
                            "yesAsk": q.get("yesAsk"),
                            "spreadCents": q.get("spreadCents"),
                            "volume": q.get("volume"),
                            "openInterest": q.get("openInterest"),
                            "sourceSystem": (q.get("provenance") or {}).get("sourceSystem"),
                            "closeSelectionReason": "select_closing_quote:last_active_pregame",
                            "validationStatus": q.get("validationStatus"),
                        }
                        out.write(json.dumps(row, sort_keys=True) + "\n")
                        totals["rows"] += 1
                        contracts_seen.add(ticker)
                        games_seen.add(game_key)
                        dates_seen.add(date)
                        fam = row["marketFamily"]
                        for gkey in (("checkpoint", cp), ("family", fam),
                                     ("side", side),
                                     ("band", band_of(entry_c)),
                                     ("family_side_cp", "%s|%s|%s" % (fam, side, cp)),
                                     ("band_side_cp", "%s|%s|%s" % (band_of(entry_c), side, cp))):
                            g = groups["%s=%s" % gkey]
                            g["clv"].append(clv)
                            g["games"][game_key].append(clv)
            print(date, "rows so far:", totals["rows"])

    # --- aggregate reporting with game-clustered CI ---
    report = []
    for name, g in sorted(groups.items()):
        vals = np.array(g["clv"], dtype=float)
        per_game = np.array([float(np.mean(v)) for v in g["games"].values()])
        n_g = len(per_game)
        entry = {
            "group": name,
            "observations": int(vals.size),
            "uniqueGames": n_g,
            "meanClvCents": round(float(vals.mean()), 3),
            "medianClvCents": round(float(np.median(vals)), 3),
            "pctBeatingClose": round(float((vals > 0).mean()) * 100, 2),
            "pctExactlyFlat": round(float((vals == 0).mean()) * 100, 2),
        }
        if n_g >= 20:
            idx = rng.integers(0, n_g, size=(BOOT, n_g))
            means = per_game[idx].mean(axis=1)
            lo, hi = np.percentile(means, [5, 95])
            entry["gameClusteredCI90"] = [round(float(lo), 3), round(float(hi), 3)]
        report.append(entry)

    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "A_universe_clv",
        "researchOnly": True,
        "productionClvUnchanged": True,
        "signConvention": ("clvCents = closingExecutable - entryExecutable; "
                           "POSITIVE = GOOD (bought below the close). This is "
                           "the negation of production's entry-minus-closing "
                           "formula -- see module docstring."),
        "splitsCovered": ["discovery", "validation"],
        "blindHoldout": "NOT READ",
        "eligibleObservations": int(totals["rows"]),
        "uniqueContracts": len(contracts_seen),
        "uniqueGames": len(games_seen),
        "dates": len(dates_seen),
        "unavailableReasons": dict(unavailable),
        "lastPregameIsCloseCheck": {
            "rows": len(lastpregame_clv),
            "meanClvCents": round(float(np.mean(lastpregame_clv)), 4) if lastpregame_clv else None,
            "note": ("LAST_PREGAME is the SAME quote select_closing_quote picks "
                     "as the close, so its CLV is identically zero by "
                     "construction -- it is not an independent CLV checkpoint."),
        },
        "checkpointsRequestedButAbsent": [],
        "groups": report,
    }
    present = {r["group"].split("=", 1)[1] for r in report if r["group"].startswith("checkpoint=")}
    doc["checkpointsRequestedButAbsent"] = [c for c in STANDARD_CHECKPOINTS if c not in present]

    with open(os.path.join(ART, "universe_clv_report.json"), "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("rows:", totals["rows"], "contracts:", len(contracts_seen),
          "games:", len(games_seen), "dates:", len(dates_seen))
    print("absent checkpoints:", doc["checkpointsRequestedButAbsent"])
    print("unavailable:", dict(unavailable))


if __name__ == "__main__":
    sys.exit(main())
