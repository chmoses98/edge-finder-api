#!/usr/bin/env python3
"""MLB-ALPHA-0002 Family D: does Kalshi LAG the sharp market?

Not a static Pinnacle-vs-Kalshi price comparison. Event time is the
Pinnacle snapshot (recovered at ~15-minute grid, each carrying Pinnacle's
own last_update). At each snapshot s for a game:

  p_s  = Pinnacle vig-free probability (h2h home; totals over at an
         exactly matching Kalshi rung: Pinnacle line x.5 <-> rung x+1)
  k_s  = Kalshi mid (from the 1-minute exchange record) at s
  disagreement d_s = p_s - k_s  (probability points)

Questions (each a registered hypothesis):
  D1 lead/lag: corr(dPinn[s-15,s], dKalshi[s,s+15]) vs the reverse.
  D2 disagreement predicts Kalshi's subsequent fair-mid move to +30/+60/close.
  D3 executable: buy the side Pinnacle favours when |d| >= 3pp at the
     executable price; executable CLV to close and $10 post-fee P/L.
  D4 settlement: y ~ logit(k) + logit(p) (descriptive at pilot size).

Game identity: Pinnacle event id -> slate oddsApiEventId -> Kalshi event
ticker (exact join, never fuzzy). Game-cluster bootstrap CIs.
RESEARCH ONLY.
"""

import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from lib.edgelab import clv_convention as cc                      # noqa: E402
from lib.edgelab.kalshi_fees import net_settlement_pl_for_order    # noqa: E402
from scripts.research.mlb_alpha_0002.build_candle_panel import (   # noqa: E402
    load_ticker_series, state_at, two_sided, EPOCH)
from scripts.research.mlb_alpha_0002.build_kalshi_panel import load_settlements  # noqa: E402

ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
PINN = os.path.join(ART, "pinnacle_history")
OUT = os.path.join(ART, "family_d_results.json")
SEED = 20260902


def minute_of(ts):
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    return int((dt - EPOCH).total_seconds() // 60)


def devig(a, b):
    if not a or not b:
        return None
    ia, ib = 1.0 / a, 1.0 / b
    return ia / (ia + ib)


def event_map():
    """oddsApiEventId -> kalshi event ticker, from every slate snapshot."""
    m = {}
    paths = glob.glob(os.path.join(REPO, "data", "slates", "*", "*.json"))
    paths += glob.glob(os.path.join(REPO, "data", "edgelab", "snapshots", "*", "*", "*", "frozen", "normalized_slate.json.gz"))
    for p in paths:
        try:
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt") as fh:
                d = json.load(fh)
        except Exception:
            continue
        if isinstance(d, dict) and "data" in d and "games" not in d:
            d = d["data"] or {}
        for g in (d.get("games") or []):
            eid = g.get("oddsApiEventId")
            ml = ((g.get("odds") or {}).get("kalshi") or {}).get("ml") or {}
            tk = ml.get("home_ticker") or ml.get("away_ticker")
            if eid and tk:
                m[eid] = tk.rsplit("-", 1)[0]
    return m


def load_pinnacle_snapshots():
    """-> {date: [(snapshotMinute, snapshotIso, {eventId: game})]}"""
    out = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(PINN, "*", "*.json.gz"))):
        date = os.path.basename(os.path.dirname(p))
        with gzip.open(p, "rt") as fh:
            d = json.load(fh)
        ts = d.get("timestamp")
        if not ts:
            continue
        games = {g["id"]: g for g in (d.get("data") or [])}
        out[date].append((minute_of(ts), ts, games))
    for k in out:
        out[k].sort()
    return out


def pinnacle_probs(game):
    """-> (homeProb, {rung: overProb}) from the pinnacle bookmaker block."""
    home_p, totals = None, {}
    for b in game.get("bookmakers") or []:
        if b.get("key") != "pinnacle":
            continue
        for mk in b.get("markets") or []:
            outs = {o["name"]: o for o in mk.get("outcomes") or []}
            if mk["key"] == "h2h":
                h, a = outs.get(game["home_team"]), outs.get(game["away_team"])
                if h and a:
                    home_p = devig(h["price"], a["price"])
            elif mk["key"] == "totals":
                o, u = outs.get("Over"), outs.get("Under")
                if o and u and o.get("point") is not None and abs(o["point"] * 2 - round(o["point"] * 2)) < 1e-9 \
                        and abs(o["point"] - int(o["point"])) > 0.25:       # x.5 line only
                    totals[int(math.floor(o["point"])) + 1] = devig(o["price"], u["price"])
    return home_p, totals


def cluster_boot(vals, clus, B=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    g = defaultdict(list)
    for v, c in zip(vals, clus):
        g[c].append(v)
    keys = list(g); sums = np.array([sum(g[k]) for k in keys]); cnt = np.array([len(g[k]) for k in keys])
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    return float(np.mean(vals)), [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], len(keys)


def main():
    emap = event_map()
    snaps = load_pinnacle_snapshots()
    dates = sorted(snaps)
    series = load_ticker_series(dates)
    settled = load_settlements()
    by_event = defaultdict(dict)   # event ticker -> {"home": ticker, "totals": {rung: ticker}}
    for t, s in series.items():
        ev = t.rsplit("-", 1)[0]
        if s["family"] == "game_result":
            by_event[ev].setdefault("gr", []).append(t)
        elif s["family"] == "game_total":
            try:
                by_event[ev].setdefault("tot", {})[int(t.rsplit("-", 1)[1])] = t
            except ValueError:
                pass
    obs = []      # one row per (game, snapshot, contract-kind)
    unmatched = set()
    for date in dates:
        prev = {}
        for smin, siso, games in snaps[date]:
            for eid, g in games.items():
                ev = emap.get(eid)
                if ev is None:
                    unmatched.add(eid); continue
                start_min = minute_of(g["commence_time"])
                if smin >= start_min - 1:
                    continue                                    # pregame only
                home_p, totals = pinnacle_probs(g)
                cands = []
                gr = by_event.get(ev, {}).get("gr") or []
                home_abbr = ev.split("-", 1)[1][-3:] if len(ev.split("-", 1)[1]) >= 6 else None
                for t in gr:
                    # ticker suffix is the team; Kalshi YES = that team wins
                    team = t.rsplit("-", 1)[1]
                    if home_p is None:
                        continue
                    from lib.edgelab.mlb_alpha_identity import parse_event_ticker
                    ident = parse_event_ticker(ev)
                    if ident.get("status") != "RESOLVED":
                        continue
                    p = home_p if team == ident["homeTeam"] else (1 - home_p if team == ident["awayTeam"] else None)
                    if p is not None:
                        cands.append(("game_result", t, p))
                for rung, t in (by_event.get(ev, {}).get("tot") or {}).items():
                    if rung in totals and totals[rung] is not None:
                        cands.append(("game_total", t, totals[rung]))
                for kind, t, p in cands:
                    cd = series[t]["candles"]
                    c = state_at(cd, smin)
                    if not two_sided(c) or t not in settled:
                        continue
                    k = (c[0] + c[1]) / 2.0 / 100.0
                    close = None
                    for m in range(start_min - 1, start_min - 60, -1):
                        cc_ = cd.get(m)
                        if two_sided(cc_):
                            close = cc_ + (m,); break
                    if close is None or close[4] <= smin:
                        continue
                    fut = {}
                    for h in (15, 30, 60):
                        f = state_at(cd, min(smin + h, close[4]))
                        fut[h] = ((f[0] + f[1]) / 2.0 / 100.0 - k) if two_sided(f) else None
                    back = state_at(cd, smin - 15)
                    k_back = ((back[0] + back[1]) / 2.0 / 100.0 - k) if two_sided(back) else None   # k(s-15)-k(s)
                    pv = prev.get((eid, kind, t))
                    d_p_back = (p - pv[0]) if pv and (smin - pv[1]) <= 20 else None
                    yes_ask, no_ask = float(c[1]), 100.0 - float(c[0])
                    obs.append({"date": date, "game": ev, "kind": kind, "ticker": t, "smin": smin, "p": p, "k": k,
                                "d": p - k, "mts": start_min - smin,
                                "dPinnBack15": d_p_back, "dKalBack15": (-k_back) if k_back is not None else None,
                                "fut15": fut[15], "fut30": fut[30], "fut60": fut[60],
                                "futClose": (close[0] + close[1]) / 2.0 / 100.0 - k,
                                "clvYes": cc.clv_for_yes(yes_ask, float(close[1])),
                                "clvNo": cc.clv_for_no(no_ask, 100.0 - float(close[0])),
                                "plYes": net_settlement_pl_for_order(10.0, yes_ask / 100.0, settled[t] == "YES"),
                                "plNo": net_settlement_pl_for_order(10.0, no_ask / 100.0, settled[t] == "NO"),
                                "spread": c[1] - c[0], "y": 1.0 if settled[t] == "YES" else 0.0})
                    prev[(eid, kind, t)] = (p, smin)
    res = {"programId": "MLB-ALPHA-0002", "family": "D", "dates": dates, "rows": len(obs),
           "games": len({o["game"] for o in obs}), "unmatchedPinnacleEvents": len(unmatched),
           "hypothesesTested": 0, "byKind": {}}
    for kind in ("game_result", "game_total"):
        rs = [o for o in obs if o["kind"] == kind]
        r = {"rows": len(rs), "games": len({o["game"] for o in rs}), "tickers": len({o["ticker"] for o in rs})}
        if len(rs) < 30:
            r["status"] = "INSUFFICIENT"; res["byKind"][kind] = r; continue
        clus = [o["game"] for o in rs]
        # D1 lead-lag correlations
        ll = [o for o in rs if o["dPinnBack15"] is not None and o["fut15"] is not None and o["dKalBack15"] is not None]
        if len(ll) >= 30:
            a = np.array([o["dPinnBack15"] for o in ll]); b = np.array([o["fut15"] for o in ll])
            c_ = np.array([o["dKalBack15"] for o in ll]); dpf = None
            # forward Pinnacle move needs the next snapshot: approximate with stored dPinnBack of the next row
            r["D1_corr_pinnPast_kalFuture15"] = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None
            r["D1_corr_kalPast_kalFuture15"] = float(np.corrcoef(c_, b)[0, 1]) if c_.std() > 0 and b.std() > 0 else None
            r["D1_rows"] = len(ll)
            # response coefficient: future Kalshi move per 1pp of recent Pinnacle move (through origin)
            r["D1_kalFuture15_per_pinnPast15"] = float((a * b).sum() / (a * a).sum()) if (a * a).sum() > 0 else None
        res["hypothesesTested"] += 1
        # D2 disagreement -> subsequent Kalshi move (fair-mid), per horizon; slope through origin + sign agreement
        for h in ("fut30", "fut60", "futClose"):
            pairs = [(o["d"], o[h]) for o in rs if o[h] is not None]
            if len(pairs) < 30:
                continue
            a = np.array([p[0] for p in pairs]); b = np.array([p[1] for p in pairs])
            agree = np.sign(a) * b
            m, ci, ng = cluster_boot(agree, [o["game"] for o in rs if o[h] is not None])
            r["D2_" + h] = {"rows": len(pairs), "games": ng, "slopePerPp": float((a * b).sum() / (a * a).sum()) if (a * a).sum() > 0 else None,
                            "corr": float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None,
                            "moveTowardPinnacleMeanPp": m * 100, "ci95": [ci[0] * 100, ci[1] * 100]}
            res["hypothesesTested"] += 1
        # D3 executable: |d| >= 3pp, buy Pinnacle's side
        big = [o for o in rs if abs(o["d"]) >= 0.03]
        if len(big) >= 20:
            clv = [o["clvYes"] if o["d"] > 0 else o["clvNo"] for o in big]
            pl = [o["plYes"] if o["d"] > 0 else o["plNo"] for o in big]
            cb = [o["game"] for o in big]
            m1, ci1, ng = cluster_boot(clv, cb); m2, ci2, _ = cluster_boot(pl, cb)
            r["D3_disagreeGe3pp"] = {"rows": len(big), "games": ng, "execClvCentsMean": m1, "clvCi95": ci1,
                                     "netPlPer10UsdMean": m2, "plCi95": ci2,
                                     "meanSpreadCents": float(np.mean([o["spread"] for o in big]))}
            res["hypothesesTested"] += 1
        # D4 settlement residual (descriptive): mean(y - k) on Pinnacle's side when |d|>=3pp
        if len(big) >= 20:
            resid = [(o["y"] - o["k"]) if o["d"] > 0 else ((1 - o["y"]) - (1 - o["k"])) for o in big]
            m3, ci3, ng = cluster_boot(resid, [o["game"] for o in big])
            r["D4_settlementResidualPinnacleSidePp"] = {"mean": m3 * 100, "ci95": [ci3[0] * 100, ci3[1] * 100], "games": ng}
        r["meanAbsDisagreementPp"] = float(np.mean([abs(o["d"]) for o in rs]) * 100)
        r["status"] = "PILOT"
        res["byKind"][kind] = r
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=1, sort_keys=True); fh.write("\n")
    print(json.dumps(res, indent=1, sort_keys=True)[:3500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
