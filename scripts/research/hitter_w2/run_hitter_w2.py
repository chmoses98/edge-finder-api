#!/usr/bin/env python3
"""
scripts/research/hitter_w2/run_hitter_w2.py
===========================================
SIFT W2 hitter lane -- RESEARCH ONLY. End-to-end driver:

  fit       fit MLB_HITTER_W2_PURE_V1 + baseline parameters on TRAINING seasons only
            (2024 as 'previous season', 2025 as the fitting season) and freeze them to
            data/research/hitter_w2/params_v1.json
  forecast  produce holdout (2026 regular season) forecasts for every eligible starter:
            challenger + baseline pmfs; writes a gzip pickle cache
  sidecars  write pure_forecast.v1 JSONL sidecars (+ outcomes) for challenger, baseline and the
            incumbent rerun (matched rows)

Sports inputs only (data/research/hitter_w2/mlb_feed/<season>/). The `--market-file`
argument exists only for the runtime non-leakage proof (pure_gate rerun): the PURE path never
opens it; `--control-read-market` is the deliberately market-reading NEGATIVE CONTROL.
"""
import argparse
import gzip
import hashlib
import json
import os
import pickle
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib.research import hitter_w2_engine as E  # noqa: E402
import features as F  # noqa: E402
import model as M  # noqa: E402
import sidecars as SC  # noqa: E402

PARAMS_PATH = os.path.join(_ROOT, "data", "research", "hitter_w2", "params_v1.json")
MODEL_VERSION = "MLB_HITTER_W2_PURE_V1"
BASELINE_VERSION = "MLB_HITTER_W2_BASELINE_V1"


def load(season, feed_root):
    sd = E.load_season(season, feed_root)
    pas = E.annotate_pa(sd)
    return sd, pas


def stage_fit(feed_root, out_path=PARAMS_PATH):
    t0 = time.time()
    sd24, pa24 = load(2024, feed_root)
    sd25, pa25 = load(2025, feed_root)
    prev24 = F.season_totals(sd24, pa24)
    rec25 = F.build_team_games(sd25, pa25, prev24)
    league24 = prev24["league"]
    bp = M.fit_batter_shrinkage(rec25, league24)
    pp = M.fit_pitcher_shrinkage(rec25, league24, bp, "sp_cur", "sp_prev", "y_cls_sp", M.GRID_W_PIT, M.GRID_M_PIT)
    pen = M.fit_pitcher_shrinkage(rec25, league24, bp, "pen_cur", "pen_prev", "y_cls_pen", M.GRID_W_PEN, M.GRID_M_PEN)
    table, tinfo = E.fit_transitions(pa24 + pa25)
    lbf25 = M.league_bf(sd25)
    params = {
        "model_version": MODEL_VERSION,
        "fit_seasons": {"previous": 2024, "fitting": 2025},
        "batter": bp, "pitcher": {"w": pp["w"], "m": pp["m"]}, "bullpen": {"w": pen["w"], "m": pen["m"]},
        "fit_logloss_per_pa": {"vs_starter": pp["logloss_per_pa"], "vs_bullpen": pen["logloss_per_pa"]},
        "platoon": M.platoon_factors(pa24 + pa25),
        "park": M.park_factors([sd24, sd25], [pa24, pa25]),
        "survival": M.fit_survival([sd24, sd25], [pa24, pa25]),
        "sub_rates": M.sub_rates([sd24, sd25], [pa24, pa25]),
        "bf_residuals": M.bf_residuals(rec25, M.league_bf(sd24)),
        "transition_info": tinfo,
        "transitions": {f"{k[0]}|{k[1]}|{k[2]}": [v[0], v[1]] for k, v in sorted(table.items())},
        # holdout-time priors: the 2025 season is the 'previous season' of the 2026 holdout
        "league_bf_for_holdout": lbf25,
        "baseline": M.baseline_tables(rec25),
        "constants": {"BF_SHRINK_K": M.BF_SHRINK_K, "BF_PREV_W": M.BF_PREV_W, "PARK_K": M.PARK_K,
                      "BASELINE_M": M.BASELINE_M, "THRESHOLDS": M.THRESHOLDS},
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(params, f, sort_keys=True, separators=(",", ":"))
    print(json.dumps({"batter": bp, "pitcher": pp, "bullpen": pen, "transitions": tinfo,
                      "seconds": round(time.time() - t0)}, indent=1))
    return params


def load_params(path=PARAMS_PATH):
    with open(path) as f:
        P = json.load(f)
    P["baseline"]["pa_pmf"] = {key: {int(k): v for k, v in d.items()} for key, d in P["baseline"]["pa_pmf"].items()}
    table = {}
    for k, v in P["transitions"].items():
        cls, outs, bases = k.split("|")
        table[(cls, int(outs), int(bases))] = (v[0], [tuple(o) for o in v[1]])
    return P, table


def params_hash(path=PARAMS_PATH):
    with open(path, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def holdout_records(feed_root):
    sd25, pa25 = load(2025, feed_root)
    sd26, pa26 = load(2026, feed_root)
    prev25 = F.season_totals(sd25, pa25)
    rec26 = F.build_team_games(sd26, pa26, prev25)
    return sd26, rec26, prev25


def stage_forecast(feed_root, out_cache, n_sims=500, seed=20261011, start=None, end=None, market_file=None,
                   control_read_market=False):
    P, table = load_params()
    sd26, rec26, prev25 = holdout_records(feed_root)
    holdout_params = dict(P)
    holdout_params["league"] = prev25["league"]
    holdout_params["league_bf"] = P["league_bf_for_holdout"]
    market = None
    if control_read_market:
        # NEGATIVE CONTROL ONLY: deliberately reads market run totals and rescales the per-PA
        # non-out rates of each team. Never used by the PURE model.
        with open(market_file) as f:
            market = json.load(f)
    by_game = {}
    excluded = {}
    for rec in rec26:
        if rec.get("excluded"):
            excluded[f"{rec['gamePk']}{rec['side']}"] = rec["excluded"]
            continue
        if start and rec["date"] < start or end and rec["date"] > end:
            continue
        by_game.setdefault(rec["gamePk"], {})[rec["side"]] = rec
    out = {"meta": {"params_hash": params_hash(), "n_sims": n_sims, "seed": seed, "excluded": excluded},
           "games": {}}
    t0 = time.time()
    for n, pk in enumerate(sorted(by_game)):
        g = by_game[pk]
        if "A" not in g or "H" not in g:
            continue
        hp = holdout_params
        if market is not None:
            hp = _control_params(holdout_params, market.get(str(pk)))
        res = M.challenger_game(g["A"], g["H"], hp, table, n_sims, seed + pk % 100000)
        base = {s: [M.baseline_player(b, g[s]["home"], holdout_params) for b in g[s]["batters"]] for s in ("A", "H")}
        out["games"][pk] = {"records": g, "challenger": res, "baseline": base}
        if n % 200 == 0:
            print(f"{n}/{len(by_game)} games ({time.time() - t0:.0f}s)", flush=True)
    if out_cache.endswith(".jsonl"):
        write_challenger_jsonl(out, out_cache)
    else:
        with gzip.open(out_cache, "wb") as f:
            pickle.dump(out, f, protocol=4)
    return out


def challenger_lineage(rec, b, res_side):
    n_cur = sum(b["cur"]) if b["cur"] else 0
    n_prev = sum(b["prev"]) if b["prev"] else 0
    return [("lineup_slot", b["slot"]), ("bat_side", b["batSide"] or None), ("home", rec["home"]),
            ("opp_starter_mlbam_id", rec["oppStarterId"]), ("opp_starter_throws", rec["oppStarterHand"] or None),
            ("opp_starter_expected_bf", round(res_side["exp_sp_bf"], 3)), ("batter_pa_season_to_date", n_cur),
            ("batter_pa_previous_season", n_prev), ("home_venue_id", rec["venueId"]),
            ("lineup_source", "official_starting_lineup")]


def write_challenger_jsonl(out, path, as_of_override=None):
    """Challenger sidecar rows only (used by the runtime non-leakage rerun)."""
    h = out["meta"]["params_hash"]
    rows = []
    for pk in sorted(out["games"]):
        g = out["games"][pk]
        for side in ("A", "H"):
            rec = g["records"][side]
            kick = rec["startUTC"]
            as_of = (as_of_override or {}).get((pk, side)) or SC.default_as_of(kick)
            res = g["challenger"][side]
            for i, b in enumerate(rec["batters"]):
                lin = SC.lineage(challenger_lineage(rec, b, res), as_of)
                for st in E.STATS:
                    rows.append(SC.row(MODEL_VERSION, h, pk, b["playerId"], st, res["players"][i][st], kick, as_of,
                                       lin, "hitter_w2"))
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _control_params(P, total):
    """Negative control: scale non-out category rates by a market total (if any)."""
    if not total:
        return P
    q = dict(P)
    scale = max(0.5, min(1.5, float(total) / 9.0))
    q["league"] = list(P["league"])
    q["sub_rates"] = [r * (scale if E.CATS[i] not in ("K", "OUT") else 1.0) for i, r in enumerate(P["sub_rates"])]
    q["park"] = {k: [f * (scale if E.CATS[i] not in ("K", "OUT") else 1.0) for i, f in enumerate(v)]
                 for k, v in P["park"].items()}
    return q


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fit", "forecast"))
    ap.add_argument("--feed-root", default=os.path.join(_ROOT, "data", "research", "hitter_w2", "mlb_feed"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-sims", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--market-file", default=None, help="present only for the non-leakage rerun; never read by PURE")
    ap.add_argument("--quotes-file", default=None, help="present only for the non-leakage rerun; never read")
    ap.add_argument("--control-read-market", action="store_true", help="NEGATIVE CONTROL: read --market-file")
    a = ap.parse_args(argv)
    if a.stage == "fit":
        stage_fit(a.feed_root)
    else:
        stage_forecast(a.feed_root, a.out, a.n_sims, a.seed, a.start, a.end, a.market_file, a.control_read_market)


if __name__ == "__main__":
    main()
