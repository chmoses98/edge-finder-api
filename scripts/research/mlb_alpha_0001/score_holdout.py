#!/usr/bin/env python3
"""MLB-ALPHA-0001: blind-holdout scorer for MLB-ALPHA-0001-C01-PIT.

THIS SCRIPT IS LOCKED. It refuses to read a single byte of holdout market
data, settlement data, or outcome data unless an explicit authorization
file exists AND names the exact frozen candidate rule hash.

The authorization file is NOT created by the session that wrote this
scorer. Creating it is a deliberate human act.

Structure is deliberate: `authorize_or_refuse()` runs BEFORE any import of
data-loading helpers is used and before any path is opened, so an
unauthorized invocation cannot touch holdout data even accidentally.
"""

import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
PROTOCOL_PATH = os.path.join(ART, "frozen_holdout_protocol.json")
AUTH_PATH = os.path.join(ART, "HOLDOUT_AUTHORIZATION.json")


class HoldoutSealed(RuntimeError):
    """Raised when the holdout is scored without explicit authorization."""


def load_protocol(protocol_path=None):
    with open(protocol_path or PROTOCOL_PATH) as fh:
        return json.load(fh)


def result_path(art_root=None):
    """Where the ONE-TIME holdout result lives. `art_root` exists so tests
    can point at an isolated directory -- no test may ever write the real
    canonical artifact (see tests/research/test_mlb_alpha_0001_holdout_seal.py)."""
    return os.path.join(art_root or ART, "holdout_result.json")


def authorize_or_refuse(protocol=None, auth_path=AUTH_PATH):
    """Returns the authorization record, or raises HoldoutSealed.

    Requires ALL of:
      * the authorization file exists;
      * it parses as JSON;
      * its candidateRuleSha256 exactly equals the frozen protocol's;
      * it sets authorized == True.
    Any failure refuses. Nothing is read from the holdout before this
    function returns successfully.
    """
    protocol = protocol or load_protocol()
    if not os.path.exists(auth_path):
        raise HoldoutSealed(
            "BLIND HOLDOUT IS SEALED. No authorization file at %s. "
            "Scoring refused; no holdout data was read." % auth_path)
    try:
        with open(auth_path) as fh:
            auth = json.load(fh)
    except Exception as exc:
        raise HoldoutSealed("authorization file unreadable (%s); refusing" % exc)
    if auth.get("authorized") is not True:
        raise HoldoutSealed("authorization file does not set authorized=true; refusing")
    expected = protocol["candidateRuleSha256"]
    if auth.get("candidateRuleSha256") != expected:
        raise HoldoutSealed(
            "authorization names rule %r but the frozen protocol is %r; refusing"
            % (auth.get("candidateRuleSha256"), expected))
    return auth



# ---------------------------------------------------------------------------
# SCORING IMPLEMENTATION -- applies the FROZEN rule verbatim.
#
# Frozen rule (MLB-ALPHA-0001-C01-PIT, sha256 882f16d8...):
#   universe : KXMLBF5TOTAL (marketFamily inning_total)
#   side     : BUY YES at the archived executable yesAsk
#   band     : 90-99 cents inclusive
#   entry    : FIRST qualifying ACTIVE quote captured inside [T-60, T-0)
#   settle   : corrected AT_LEAST_N (rung N pays YES iff F5 total >= N)
#   order    : USD 10 taker, whole contracts, Tier C realistic execution
#
# Nothing here may be tuned after results are visible. Depth is NOT
# archived, so a $10 fill is never claimed to be proven.
# ---------------------------------------------------------------------------

# NOTE ON IMPORTS (CI incident, 2026-09-01): numpy, gzip and the repo's
# research modules are imported INSIDE the scoring functions, never at
# module scope. The authorization/seal layer above must remain importable
# with the standard library alone -- CI installs only requirements-ci.txt
# (duckdb, PyYAML), so a module-level `import numpy` made every seal test
# fail with ModuleNotFoundError even though the gate needs no numpy at
# all. Keep the scientific stack behind the gate.

WINDOW_OPEN_MIN = 60.0
WINDOW_CLOSE_MIN = 0.0
BAND_LO, BAND_HI = 90, 99
SEED = 20260906
BOOT = 2000


def _pct(a, qs):
    import numpy as np
    return {("p%d" % q): round(float(np.percentile(a, q)), 2) for q in qs}


def score(protocol):
    """Heavy path. Imports the scientific stack locally so the seal layer
    stays stdlib-only (see the import note above)."""
    import gzip  # noqa: F401
    from collections import Counter, defaultdict
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    from scripts.research.mlb_alpha_0001.build_entry_rows import (
        parse_event, parse_ts, iter_jsonl, partition_paths, load_settlement_map)
    from scripts.research.mlb_alpha_0001.family_a_discovery import (
        row_side_econ, SIZE_INFLATION, ORDER)
    from scripts.research.mlb_alpha_0001.inference import clustered_roi_inference
    from lib.edgelab.checkpoints import select_closing_quote

    dates = list(protocol["holdoutDates"])
    settled = load_settlement_map()
    with open(os.path.join(ART, "corrected_total_settlements.json")) as fh:
        corrected = json.load(fh)["tickers"]
    parts = partition_paths("observations")

    rows = []
    excl = Counter()
    identity_ok, identity_bad = set(), set()
    dupe_guard = set()

    for date in dates:
        if date not in parts:
            excl["no_observation_partition"] += 1
            continue
        by_ticker = defaultdict(list)
        for r in iter_jsonl(parts[date]):
            if not r["marketTicker"].startswith("KXMLBF5TOTAL-"):
                continue
            ev = parse_event(r.get("eventTicker"))
            if ev is None:
                identity_bad.add(r.get("eventTicker"))
                excl["unresolved_market_identity"] += 1
                continue
            if ev[0] != date:
                continue
            identity_ok.add(r["eventTicker"])
            by_ticker[r["marketTicker"]].append((ev[1], r))

        for ticker, pairs in by_ticker.items():
            start = pairs[0][0]
            quotes = []
            for _, q in pairs:
                if (q.get("marketStatus") or "active").lower() not in ("active", "unknown"):
                    excl["stale_or_inactive_quote"] += 1
                    continue
                if parse_ts(q["capturedAt"]) >= start:
                    excl["post_start_quote"] += 1
                    continue
                quotes.append(q)
            if not quotes:
                continue
            quotes.sort(key=lambda q: q["capturedAt"])

            closing = select_closing_quote(
                quotes, scheduled_start=start.isoformat() + "Z")

            fired = None
            for q in quotes:
                m2s = (start - parse_ts(q["capturedAt"])).total_seconds() / 60.0
                if not (WINDOW_CLOSE_MIN <= m2s <= WINDOW_OPEN_MIN):
                    continue
                ya = q.get("yesAsk")
                if ya is None or not (BAND_LO <= ya <= BAND_HI):
                    continue
                fired = (q, m2s)
                break            # FIRST qualifying quote only
            if fired is None:
                continue
            q, m2s = fired

            if ticker in dupe_guard:
                excl["duplicate_opportunity"] += 1
                continue
            dupe_guard.add(ticker)

            res = settled.get(ticker)
            if res not in ("YES", "NO"):
                excl["no_usable_settlement"] += 1
                continue
            econ = row_side_econ(q["yesAsk"], res == "YES")
            if econ is None:
                excl["unexecutable_entry_price"] += 1
                continue

            corr = corrected.get(ticker) or {}
            thr = q.get("threshold")

            entry_exec = float(q["yesAsk"])
            close_exec = (float(closing["yesAsk"])
                          if closing is not None and closing.get("yesAsk") is not None
                          else None)
            e_bid, e_ask = q.get("yesBid"), q.get("yesAsk")
            entry_mid = ((e_bid + e_ask) / 2.0) if (e_bid is not None and e_ask is not None) else None
            entry_spread = (e_ask - e_bid) if (e_bid is not None and e_ask is not None) else None
            close_mid = close_spread = None
            if closing is not None:
                cb, ca = closing.get("yesBid"), closing.get("yesAsk")
                if cb is not None and ca is not None:
                    close_mid = (cb + ca) / 2.0
                    close_spread = ca - cb

            rows.append({
                "date": date,
                "marketTicker": ticker,
                "eventTicker": q["eventTicker"],
                "game": date + ":" + q["eventTicker"].split("-", 1)[1],
                "threshold": thr,
                "capturedAt": q["capturedAt"],
                "minutesToStart": round(m2s, 1),
                "entryExecutableCents": entry_exec,
                "yesBid": e_bid, "yesAsk": e_ask,
                "spreadCents": entry_spread,
                "volume": q.get("volume"), "openInterest": q.get("openInterest"),
                "settlementResult": res,
                "archivedResult": corr.get("archived"),
                "correctedResult": corr.get("corrected"),
                "won": res == "YES",
                "contracts": econ["contracts"], "cash": econ["cash"],
                "fee": econ["fee"], "netPL": econ["netPL"], "grossPL": econ["grossPL"],
                # CLV, canonical positive-is-good convention (closing - entry)
                "execClvCents": (round(close_exec - entry_exec, 2)
                                 if close_exec is not None else None),
                "fairMidClvCents": (round(close_mid - entry_mid, 2)
                                    if (close_mid is not None and entry_mid is not None)
                                    else None),
                "entrySpreadCents": entry_spread,
                "closingSpreadCents": close_spread,
                "spreadCompressionCents": (round(entry_spread - close_spread, 2)
                                           if (entry_spread is not None and close_spread is not None)
                                           else None),
            })

    return _summarize(rows, excl, protocol, identity_ok, identity_bad,
                      row_side_econ_order=ORDER, size_inflation=SIZE_INFLATION,
                      clustered_roi_inference=clustered_roi_inference)


def _summarize(rows, excl, protocol, identity_ok, identity_bad,
               row_side_econ_order, size_inflation, clustered_roi_inference):
    from collections import Counter, defaultdict
    import numpy as np
    games = sorted({r["game"] for r in rows})
    dates_hit = sorted({r["date"] for r in rows})
    wins = sum(1 for r in rows if r["won"])
    losses = len(rows) - wins
    total_cash = sum(r["cash"] for r in rows)
    total_net = sum(r["netPL"] for r in rows)
    total_gross = sum(r["grossPL"] for r in rows)
    total_fees = sum(r["fee"] for r in rows)

    by_date_pl = defaultdict(float)
    by_date_cash = defaultdict(float)
    by_date_n = Counter()
    for r in rows:
        by_date_pl[r["date"]] += r["netPL"]
        by_date_cash[r["date"]] += r["cash"]
        by_date_n[r["date"]] += 1
    abs_total = sum(abs(v) for v in by_date_pl.values())
    largest_date_share = (max(abs(v) for v in by_date_pl.values()) / abs_total
                          if abs_total > 0 else 0.0)

    # chronological drawdown and losing streak
    seq = sorted(rows, key=lambda r: (r["date"], r["capturedAt"]))
    run = peak = dd = 0.0
    streak = worst_streak = 0
    for r in seq:
        run += r["netPL"]
        peak = max(peak, run)
        dd = min(dd, run - peak)
        if r["won"]:
            streak = 0
        else:
            streak += 1
            worst_streak = max(worst_streak, streak)

    net_g, cash_g = defaultdict(float), defaultdict(float)
    for r in rows:
        net_g[r["game"]] += r["netPL"]
        cash_g[r["game"]] += r["cash"]
    rng = np.random.default_rng(SEED)
    inf = clustered_roi_inference(net_g, cash_g, rng, B=BOOT) if net_g else None
    if inf:
        inf["pConservative"] = min(1.0, round(inf["pPrimary"] * size_inflation, 6))

    # break-even win rate at the observed entries
    be = (float(np.mean([r["cash"] / (r["contracts"] * 1.0) for r in rows]))
          if rows else None)

    def stat(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return None
        a = np.array(vals, dtype=float)
        return dict(_pct(a, [5, 25, 50, 75, 95]), mean=round(float(a.mean()), 2),
                    n=int(a.size))

    def clv_block(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return {"coverage": 0}
        a = np.array(vals, dtype=float)
        return {"coverage": int(a.size),
                "coveragePct": round(100.0 * a.size / len(rows), 1),
                "mean": round(float(a.mean()), 3),
                "median": round(float(np.median(a)), 3),
                "pctBeatingClose": round(float((a > 0).mean()) * 100, 2)}

    boundary = [r for r in rows
                if r["threshold"] is not None and r["archivedResult"] is not None
                and r["archivedResult"] != r["correctedResult"]]
    semantic_mismatch = [r for r in rows
                         if r["correctedResult"] is not None
                         and r["correctedResult"] != r["settlementResult"]]

    n_games, n_dates = len(games), len(dates_hit)
    floor = protocol["sampleFloor"]
    floor_met = (n_games >= floor["independentGames"]
                 and n_dates >= floor["independentDates"])
    net_roi = (total_net / total_cash) if total_cash else None
    date_ok = largest_date_share <= 0.50
    identity_ok_flag = len(identity_bad) == 0
    settlement_ok = len(semantic_mismatch) == 0
    integrity_ok = settlement_ok and identity_ok_flag

    if not floor_met:
        verdict = "INCONCLUSIVE"
    elif (net_roi is not None and net_roi > 0 and integrity_ok and date_ok):
        verdict = "REPLICATED_FOR_PROSPECTIVE_SHADOW"
    elif net_roi is not None and net_roi <= 0:
        verdict = "FAILED_TO_REPLICATE"
    elif not integrity_ok:
        verdict = "FAILED_TO_REPLICATE"
    else:
        verdict = "INCONCLUSIVE"

    return {
        "program": "MLB-ALPHA-0001",
        "candidateId": protocol["candidateId"],
        "candidateRuleSha256": protocol["candidateRuleSha256"],
        "protocolSha256": protocol["protocolSha256"],
        "holdoutDates": protocol["holdoutDates"],
        "scoredOnce": True,
        "holdoutStatus": "SPENT",
        "opportunities": {
            "qualifyingContracts": len(rows),
            "uniqueGames": n_games,
            "independentDates": n_dates,
            "perDate": dict(by_date_n),
            "perGame": round(len(rows) / n_games, 3) if n_games else None,
            "thresholds": dict(Counter(r["threshold"] for r in rows)),
            "entryPriceCents": stat("entryExecutableCents"),
            "minutesToStart": stat("minutesToStart"),
            "spreadCents": stat("spreadCents"),
            "volume": stat("volume"),
            "openInterest": stat("openInterest"),
            "identityResolvedEvents": len(identity_ok),
            "identityUnresolvedEvents": len(identity_bad),
        },
        "economics": {
            "wins": wins, "losses": losses,
            "winRate": round(wins / len(rows), 4) if rows else None,
            "modeledStakeUsd": round(row_side_econ_order * len(rows), 2),
            "cashActuallyDeployedUsd": round(total_cash, 2),
            "grossPLBeforeFees": round(total_gross, 2),
            "totalFees": round(total_fees, 2),
            "netPL": round(total_net, 2),
            "netROI": round(net_roi, 6) if net_roi is not None else None,
            "avgEntryPriceCents": (round(float(np.mean(
                [r["entryExecutableCents"] for r in rows])), 2) if rows else None),
            "breakEvenWinRateAtObservedEntries": round(be, 4) if be else None,
            "maxDrawdownUsd": round(dd, 2),
            "longestLosingStreak": worst_streak,
            "byDateNetPL": {k: round(v, 2) for k, v in sorted(by_date_pl.items())},
            "byDateROI": {k: (round(by_date_pl[k] / by_date_cash[k], 4)
                              if by_date_cash[k] else None)
                          for k in sorted(by_date_pl)},
            "largestDateShareOfAbsPL": round(largest_date_share, 4),
        },
        "statisticalContextReportingOnly": inf,
        "clv": {
            "convention": "positive-is-good: closing - entry (canonical); legacy inverted stored clv NOT used",
            "executable": clv_block("execClvCents"),
            "fairMid": clv_block("fairMidClvCents"),
            "avgEntrySpreadCents": (round(float(np.mean(
                [r["entrySpreadCents"] for r in rows if r["entrySpreadCents"] is not None])), 3)
                if rows else None),
            "avgClosingSpreadCents": (round(float(np.mean(
                [r["closingSpreadCents"] for r in rows if r["closingSpreadCents"] is not None])), 3)
                if any(r["closingSpreadCents"] is not None for r in rows) else None),
            "avgSpreadCompressionCents": (round(float(np.mean(
                [r["spreadCompressionCents"] for r in rows if r["spreadCompressionCents"] is not None])), 3)
                if any(r["spreadCompressionCents"] is not None for r in rows) else None),
        },
        "dataIntegrity": {
            "integerBoundaryContracts": len(boundary),
            "boundaryDetail": [{"marketTicker": r["marketTicker"],
                                "threshold": r["threshold"],
                                "archived": r["archivedResult"],
                                "corrected": r["correctedResult"]} for r in boundary],
            "settlementSemanticMismatches": len(semantic_mismatch),
            "ambiguousGames": len(identity_bad),
            "duplicateOpportunities": excl.get("duplicate_opportunity", 0),
            "postStartQuoteExclusions": excl.get("post_start_quote", 0),
            "staleOrInactiveQuoteExclusions": excl.get("stale_or_inactive_quote", 0),
            "otherExclusions": {k: v for k, v in excl.items()
                                if k not in ("duplicate_opportunity", "post_start_quote",
                                             "stale_or_inactive_quote")},
        },
        "executionCaveat": {
            "claim": "TOP_OF_BOOK_PRICE_OBSERVED",
            "tenDollarFillProven": False,
            "historicalCapacity": "UNKNOWN/UNVERIFIED -- no ask size or depth is archived",
        },
        "frozenVerdictInputs": {
            "sampleFloorMet": floor_met,
            "netROIPositive": bool(net_roi is not None and net_roi > 0),
            "dateConcentrationCriterionMet": date_ok,
            "identityCriterionMet": identity_ok_flag,
            "settlementIntegrityCriterionMet": settlement_ok,
        },
        "verdict": verdict,
        "rows": rows,
    }


def main(art_root=None, auth_path=None, protocol_path=None):
    protocol = load_protocol(protocol_path)
    try:
        authorize_or_refuse(protocol, auth_path=auth_path or AUTH_PATH)
    except HoldoutSealed as exc:
        print("REFUSED:", exc)
        print("holdout status:", protocol["holdoutStatus"])
        return 2

    # SPENT CHECK RUNS *BEFORE* SCORING. Ordering matters: the first
    # version scored and only then discovered the result already existed,
    # which burned a full scoring pass for nothing and made an accidental
    # invocation far more dangerous than it needed to be.
    out = result_path(art_root)
    if os.path.exists(out):
        print("REFUSED: %s already exists -- the holdout is already spent" % out)
        return 3

    print("AUTHORIZED. Scoring %s on %s"
          % (protocol["candidateId"], protocol["holdoutDates"]))
    result = score(protocol)
    with open(out, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out)
    print("VERDICT:", result["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
