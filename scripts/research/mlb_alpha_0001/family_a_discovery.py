#!/usr/bin/env python3
"""MLB-ALPHA-0001 Family A discovery: pure Kalshi market biases.

Reads ONLY the frozen discovery entry rows (never validation/holdout).
Every evaluated cell is predeclared by dimension grammar below and is
recorded in the hypothesis registry, winners and losers alike.

Predeclared dimensions (charter, Family A):
  - marketFamily
  - side: BUY_YES (at yesAsk) / BUY_NO (at 100 - yesBid)
  - coarse 10-cent price band of the executable entry price
    (0-10, 10-20, ..., 90-100); no integer cut-point searches
  - entry checkpoint: FIRST_DAILY_DERIVED / LAST_PREGAME
  - favorite vs underdog (game_result only: higher-yesAsk team of the
    event = favorite)
  - home vs away (game_result and team_total, where team is meaningful)
  - direction: UPSIDE (buy-YES on OVER-semantics contracts: totals and
    player props) vs DEFENSIVE (buy-NO on the same contracts)

Economics per row, standardized $10 taker order:
  Tier A gross: fractional contracts, no fee (10/p exposure)
  Tier B fee-only: closed-form fee drag, exposure identical to Tier A
  Tier C realistic: whole contracts via lib.edgelab.kalshi_fees,
    denominator = actual cash consumed  <- the qualifying metric

Inference: game-clustered bootstrap (resample independent games with
replacement, B=2000) for Tier-C net ROI; two-sided p-value; BH-FDR at
q=0.10 across the full recorded hypothesis set (cells meeting the
minimum-sample floor).

RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from lib.edgelab.kalshi_fees import max_contracts_for_cash, taker_fee  # noqa: E402

ORDER = 10.00
BOOT = 2000
SEED = 20260901
MIN_GAMES_FOR_TEST = 20  # cells below this are descriptive-only (recorded)
FLOOR = {"games": 60, "dates": 10, "contracts": 80}  # candidate eligibility

UPSIDE_FAMILIES = {
    "game_total", "team_total", "inning_total", "first_inning_run",
    "pitcher_strikeouts", "pitcher_outs", "hitter_hits",
    "hitter_total_bases", "hitter_hits_runs_rbis", "hitter_rbis",
    "hitter_stolen_bases",
}


def econ_table():
    """Per integer cent price 1..99: (contracts, cash_consumed, fee) for a $10 taker order."""
    tab = {}
    for cents in range(1, 100):
        p = cents / 100.0
        c = max_contracts_for_cash(ORDER, p)
        fee = taker_fee(c, p) if c > 0 else 0.0
        cash = round(c * p + fee, 4)
        tab[cents] = (c, cash, fee)
    return tab


ECON = econ_table()


def row_side_econ(price_cents, won):
    """-> dict of tier economics for one side entry, or None if unexecutable."""
    if price_cents is None:
        return None
    cents = int(round(price_cents))
    if not (1 <= cents <= 99):
        return None
    p = cents / 100.0
    c, cash, fee = ECON[cents]
    if c <= 0:
        return None
    gross = ORDER * (1.0 - p) / p if won else -ORDER
    fee_only = gross - (ORDER / p) * (taker_fee(1, p))  # fee scaled to fractional exposure
    net = (c * 1.0 - cash) if won else -cash
    return {
        "grossPL": gross, "feeOnlyPL": fee_only, "netPL": net,
        "cash": cash, "fee": fee, "price": p, "contracts": c,
    }


def game_key(row):
    ev = row["eventTicker"]
    return row["gameDate"] + ":" + ev.split("-", 1)[1]


def load_rows(split):
    path = os.path.join(ART, "entry_rows_%s.jsonl.gz" % split)
    with gzip.open(path, "rt") as fh:
        for line in fh:
            yield json.loads(line)


def favorite_map(rows):
    """game_result: per event, the team ticker with higher LAST_PREGAME yesAsk is favorite."""
    best = {}
    for r in rows:
        if r["marketFamily"] != "game_result":
            continue
        if r["entryCheckpoint"] != "LAST_PREGAME":
            continue
        ev = r["eventTicker"]
        ya = r.get("yesAsk")
        if ya is None:
            continue
        best.setdefault(ev, []).append((ya, r["marketTicker"]))
    fav = {}
    for ev, lst in best.items():
        if len(lst) < 2:
            continue
        lst.sort()
        fav[lst[-1][1]] = "FAVORITE"
        fav[lst[0][1]] = "UNDERDOG"
    return fav


def band_of(price_cents):
    b = int(price_cents // 10) * 10
    return "%02d-%02d" % (b, b + 10)


def build_opportunities(rows, fav):
    """One opportunity per (row, side). Returns list of dicts with econ + dimensions."""
    opps = []
    for r in rows:
        if r["settlementResult"] not in ("YES", "NO"):
            continue
        g = game_key(r)
        for side, price in (("BUY_YES", r.get("yesExecAsk")),
                            ("BUY_NO", r.get("noExecAsk"))):
            won = (r["settlementResult"] == "YES") == (side == "BUY_YES")
            e = row_side_econ(price, won)
            if e is None:
                continue
            fam = r["marketFamily"]
            dims = {
                "family": fam,
                "side": side,
                "checkpoint": r["entryCheckpoint"],
                "band": band_of(round(price)),
                "game": g,
                "date": r["gameDate"],
                "team": r.get("team"),
                "won": won,
            }
            if fam == "game_result":
                fd = fav.get(r["marketTicker"])
                if fd:
                    dims["favdog"] = fd
                if r.get("team") and r.get("homeTeam"):
                    dims["homeaway"] = "HOME" if r["team"] == r["homeTeam"] else "AWAY"
            if fam == "team_total" and r.get("team") and r.get("homeTeam"):
                dims["homeaway"] = "HOME" if r["team"] == r["homeTeam"] else "AWAY"
            if fam in UPSIDE_FAMILIES:
                dims["direction"] = "UPSIDE" if side == "BUY_YES" else "DEFENSIVE"
            dims.update(e)
            opps.append(dims)
    return opps


def cell_specs():
    """The predeclared hypothesis grammar. Each spec: (id, filter_keys) where
    filter_keys is a dict dimension->value or '*' meaning 'iterate values'."""
    specs = []
    # 1. family x side x checkpoint
    specs.append(("A1_family_side_cp", ("family", "side", "checkpoint")))
    # 2. family x side x band x checkpoint
    specs.append(("A2_family_side_band_cp", ("family", "side", "band", "checkpoint")))
    # 3. pooled band x side x checkpoint
    specs.append(("A3_band_side_cp", ("band", "side", "checkpoint")))
    # 4. game_result favorite/underdog x side x checkpoint
    specs.append(("A4_favdog_side_cp", ("favdog", "side", "checkpoint")))
    # 5. home/away x family x side x checkpoint (game_result, team_total)
    specs.append(("A5_homeaway_family_side_cp", ("homeaway", "family", "side", "checkpoint")))
    # 6. direction (upside/defensive) x checkpoint, pooled over upside families
    specs.append(("A6_direction_cp", ("direction", "checkpoint")))
    # 7. direction x band x checkpoint
    specs.append(("A7_direction_band_cp", ("direction", "band", "checkpoint")))
    return specs


def aggregate(opps):
    """Group opportunities into every predeclared cell."""
    cells = defaultdict(list)
    for o in opps:
        for spec_id, keys in cell_specs():
            if any(k not in o for k in keys):
                continue
            cell_id = spec_id + "|" + "|".join("%s=%s" % (k, o[k]) for k in keys)
            cells[cell_id].append(o)
    return cells


def cluster_bootstrap(net_by_game, cash_by_game, rng):
    games = list(net_by_game)
    net = np.array([net_by_game[g] for g in games])
    cash = np.array([cash_by_game[g] for g in games])
    n = len(games)
    idx = rng.integers(0, n, size=(BOOT, n))
    net_s = net[idx].sum(axis=1)
    cash_s = cash[idx].sum(axis=1)
    ok = cash_s > 0
    rois = np.where(ok, net_s / np.maximum(cash_s, 1e-9), 0.0)
    lo, hi = np.percentile(rois, [5, 95])
    point = net.sum() / cash.sum()
    # two-sided bootstrap p for H0: roi == 0
    frac_neg = float((rois <= 0).mean())
    frac_pos = float((rois >= 0).mean())
    p = 2 * min(frac_neg, frac_pos)
    return float(point), float(lo), float(hi), min(max(p, 1.0 / BOOT), 1.0)


def max_drawdown(opps):
    seq = sorted(opps, key=lambda o: (o["date"], o["game"]))
    run = peak = dd = 0.0
    for o in seq:
        run += o["netPL"]
        peak = max(peak, run)
        dd = min(dd, run - peak)
    return round(dd, 2)


def summarize(cell_id, items, rng):
    games = defaultdict(float)
    cash_g = defaultdict(float)
    dates = set()
    teams = defaultdict(float)
    date_pl = defaultdict(float)
    for o in items:
        games[o["game"]] += o["netPL"]
        cash_g[o["game"]] += o["cash"]
        dates.add(o["date"])
        date_pl[o["date"]] += o["netPL"]
        if o.get("team"):
            teams[o["team"]] += o["netPL"]
    n_games = len(games)
    total_cash = sum(o["cash"] for o in items)
    total_net = sum(o["netPL"] for o in items)
    total_gross = sum(o["grossPL"] for o in items)
    total_fee_only = sum(o["feeOnlyPL"] for o in items)
    gross_exposure = ORDER * len(items)
    out = {
        "cellId": cell_id,
        "contracts": len(items),
        "uniqueGames": n_games,
        "dates": len(dates),
        "wins": sum(1 for o in items if o["won"]),
        "losses": sum(1 for o in items if not o["won"]),
        "avgEntryPriceCents": round(100 * float(np.mean([o["price"] for o in items])), 2),
        "grossPL": round(total_gross, 2),
        "feeOnlyPL": round(total_fee_only, 2),
        "netPL": round(total_net, 2),
        "totalFees": round(sum(o["fee"] for o in items), 2),
        "grossROI": round(total_gross / gross_exposure, 4),
        "feeOnlyROI": round(total_fee_only / gross_exposure, 4),
        "netROI": round(total_net / total_cash, 4) if total_cash else None,
        "maxDrawdown": max_drawdown(items),
        "dateConcentration": round(
            max(abs(v) for v in date_pl.values()) / max(sum(abs(v) for v in date_pl.values()), 1e-9), 3),
        "teamConcentration": round(
            max(abs(v) for v in teams.values()) / max(sum(abs(v) for v in teams.values()), 1e-9), 3)
        if teams else None,
    }
    if n_games >= MIN_GAMES_FOR_TEST:
        point, lo, hi, p = cluster_bootstrap(games, cash_g, rng)
        out.update({"netROIClustered": round(point, 4),
                    "ci90": [round(lo, 4), round(hi, 4)],
                    "bootP": round(p, 5), "tested": True})
    else:
        out["tested"] = False
    out["candidateFloorMet"] = (
        n_games >= FLOOR["games"] and len(dates) >= FLOOR["dates"]
        and len(items) >= FLOOR["contracts"])
    return out


def bh_fdr(results, q=0.10):
    tested = [r for r in results if r.get("tested")]
    tested.sort(key=lambda r: r["bootP"])
    m = len(tested)
    thresh_idx = -1
    for i, r in enumerate(tested):
        if r["bootP"] <= q * (i + 1) / m:
            thresh_idx = i
    for i, r in enumerate(tested):
        r["fdrSurvivor"] = i <= thresh_idx
    return m, sum(1 for r in tested if r["fdrSurvivor"])


def main():
    rng = np.random.default_rng(SEED)
    rows = list(load_rows("discovery"))
    fav = favorite_map(rows)
    opps = build_opportunities(rows, fav)
    print("discovery rows:", len(rows), "opportunities:", len(opps))
    cells = aggregate(opps)
    print("cells (hypotheses evaluated):", len(cells))
    results = [summarize(cid, items, rng) for cid, items in sorted(cells.items())]
    m, survivors = bh_fdr(results)
    doc = {
        "program": "MLB-ALPHA-0001",
        "family": "A",
        "split": "discovery",
        "orderSizeUsd": ORDER,
        "bootstrapIterations": BOOT,
        "seed": SEED,
        "minGamesForTest": MIN_GAMES_FOR_TEST,
        "candidateFloor": FLOOR,
        "hypothesesEvaluated": len(cells),
        "hypothesesTested": m,
        "fdrQ": 0.10,
        "fdrSurvivors": survivors,
        "cells": results,
    }
    out = os.path.join(ART, "family_a_discovery_results.json")
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out)
    print("tested:", m, "FDR survivors:", survivors)
    surv = [r for r in results if r.get("fdrSurvivor")]
    surv.sort(key=lambda r: r["netROIClustered"], reverse=True)
    for r in surv[:40]:
        print("%-70s n=%5d g=%4d d=%2d roi=%+.3f ci=[%+.3f,%+.3f] p=%.4f floor=%s" % (
            r["cellId"][:70], r["contracts"], r["uniqueGames"], r["dates"],
            r["netROIClustered"], r["ci90"][0], r["ci90"][1], r["bootP"],
            r["candidateFloorMet"]))


if __name__ == "__main__":
    sys.exit(main())
