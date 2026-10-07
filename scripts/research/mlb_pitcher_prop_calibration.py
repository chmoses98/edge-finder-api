#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_prop_calibration.py
================================================
Fit + chronological validation of lib/research/pitcher_prop_projection.py (RESEARCH)
for Kalshi pitcher_outs (KXMLBOUTS) and pitcher_strikeouts (KXMLBKS), against the
incumbent engine (lib/research/pitcher_workload_projection.py) and against real
Kalshi prices.

    python3 scripts/research/mlb_pitcher_prop_calibration.py \
        [--data-root data] [--asof 2026-10-07] [--out-params ...] [--out-report ...]

Data (all committed):
  research_cache/starter_workload/<yr>/boxscores.jsonl.gz   starter lines 2022 -> 2026-08-26
  research_cache/bullpen_backtest/<yr>/schedules/*.json     gamePk -> date / teams / gameType
  statcast_raw/games/<pk>.jsonl                             pitch logs 2026-08-11 -> 09-27
                                                            (starter lines for the gap)
  edgelab/settlements/*.jsonl(.gz)                          settled KS / OUTS markets incl.
                                                            the 2026 postseason (actualValue)
  edgelab/observations/*.jsonl(.gz)                         Kalshi quotes (valid pregame)

Splits (never fitted on what they score):
  DEV       2022-2024 regular-season starts  -> every fitted constant
  HOLDOUT   2025 + 2026 regular-season starts -> model vs incumbent, all thresholds
  MARKET    2026 settled Kalshi KS/OUTS markets -> model vs incumbent vs Kalshi on
            identical rows (latest VALID pregame quote, mid of bid/ask)
  POSTSEASON shift: estimated only from postseason starts strictly before the date it is
            applied to (leave-future-out); production uses all postseason starts < asof.

Independence: one row per (market ticker); thresholds of the same pitcher-game are
correlated, so every CI is a bootstrap clustered by pitcher-game.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import math
import os
import random
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import pitcher_prop_projection as P  # noqa: E402
from lib.research import pitcher_workload_projection as OLD  # noqa: E402
from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR  # noqa: E402

POSTSEASON_START = "2026-09-29"       # 2026 regular season ended 2026-09-27
K_THRESHOLDS = range(3, 11)
OUTS_THRESHOLDS = range(12, 22)
RESID_EDGES = [13.0, 14.5, 15.5, 16.5, 17.5]
POSTSEASON_SHRINK_N0 = 25.0


def _jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _partition_files(data_root, entity):
    out = {}
    for f in glob.glob(os.path.join(data_root, "edgelab", entity, "*.jsonl*")):
        d = os.path.basename(f).split(".")[0]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            out.setdefault(d, f)
    return out


# ------------------------------------------------------------------ starts dataset

def load_schedule(data_root):
    info = {}
    for f in glob.glob(os.path.join(data_root, "research_cache", "bullpen_backtest", "*", "schedules", "*.json")):
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        for d in doc.get("dates", []):
            for g in d.get("games", []):
                t = g.get("teams") or {}
                info[g["gamePk"]] = {
                    "date": d["date"], "game_type": g.get("gameType"),
                    "away": TEAM_ID_TO_ABBR.get(((t.get("away") or {}).get("team") or {}).get("id")),
                    "home": TEAM_ID_TO_ABBR.get(((t.get("home") or {}).get("team") or {}).get("id")),
                }
    return info


def load_boxscore_starts(data_root, schedule):
    starts, team_games = [], []
    for f in sorted(glob.glob(os.path.join(data_root, "research_cache", "starter_workload", "*", "boxscores.jsonl.gz"))):
        for r in _jsonl(f):
            g = schedule.get(r["gamePk"])
            if not g or g["game_type"] != "R":
                continue
            for side, opp_side in (("away", "home"), ("home", "away")):
                ps = r.get(f"{side}Pitchers") or []
                if not ps:
                    continue
                st = ps[0]
                if st.get("orderIndex") != 0 or st.get("outs") is None:
                    continue
                starts.append({"pid": str(st["playerId"]), "date": g["date"], "pk": r["gamePk"], "team": g[side],
                               "opp": g[opp_side], "outs": st["outs"], "bf": st.get("battersFaced"),
                               "k": st.get("strikeOuts"), "postseason": False, "source": "boxscore"})
                # batting team = opp_side's hitters faced these pitchers
                bf = sum(p.get("battersFaced") or 0 for p in ps)
                k = sum(p.get("strikeOuts") or 0 for p in ps)
                team_games.append({"team": g[opp_side], "date": g["date"], "pa": bf, "k": k})
    return starts, team_games


_OUT_EVENTS = {"field_out": 1, "strikeout": 1, "force_out": 1, "sac_fly": 1, "sac_bunt": 1, "fielders_choice_out": 1,
               "grounded_into_double_play": 2, "double_play": 2, "strikeout_double_play": 2,
               "sac_fly_double_play": 2, "triple_play": 3}


def _pa_outs_from_pitches(rows):
    """Starter lines (outs, BF, K) from one game's Statcast pitches. Outs come from the
    outs-state transition between consecutive plate appearances of the same half inning
    (so caught stealing / pickoffs count, as in a box score); the last PA of a half is
    3 - outsWhenUp, except the game's final PA (walk-off), scored from its event."""
    pas = {}
    for r in rows:
        i = r.get("atBatIndex")
        if i is None:
            continue
        cur = pas.get(i)
        if cur is None or (r.get("pitchNumber") or 0) > (cur.get("pitchNumber") or 0):
            pas[i] = r
    seq = [pas[i] for i in sorted(pas)]
    halves, half, prev = [], -1, None
    for pa in seq:
        if prev is None or pa.get("inning") != prev.get("inning") or (pa.get("outsWhenUp") or 0) < (prev.get("outsWhenUp") or 0):
            half += 1
        halves.append(half)
        prev = pa
    lines = defaultdict(lambda: {"outs": 0, "bf": 0, "k": 0})
    first = {}
    for idx, pa in enumerate(seq):
        h = halves[idx]
        if h in (0, 1) and h not in first:
            first[h] = pa.get("pitcherId")
        before = pa.get("outsWhenUp") or 0
        if idx + 1 < len(seq) and halves[idx + 1] == h:
            after = seq[idx + 1].get("outsWhenUp") or 0
        elif idx + 1 == len(seq):
            after = min(3, before + _OUT_EVENTS.get(pa.get("events"), 0))
        else:
            after = 3
        line = lines[pa.get("pitcherId")]
        line["bf"] += 1
        line["outs"] += max(0, after - before)
        if pa.get("events") in ("strikeout", "strikeout_double_play"):
            line["k"] += 1
    home_sp, away_sp = first.get(0), first.get(1)   # top of the 1st: the home starter pitches
    return {"home": (home_sp, dict(lines[home_sp]) if home_sp else {}),
            "away": (away_sp, dict(lines[away_sp]) if away_sp else {})}


def load_statcast_starts(data_root, schedule, after_date):
    starts, team_games = [], []
    for f in sorted(glob.glob(os.path.join(data_root, "statcast_raw", "games", "*.jsonl"))):
        pk = int(os.path.basename(f).split(".")[0])
        g = schedule.get(pk)
        if not g or g["game_type"] != "R" or g["date"] <= after_date:
            continue
        rows = list(_jsonl(f))
        if not rows:
            continue
        lines = _pa_outs_from_pitches(rows)
        for side, opp_side in (("away", "home"), ("home", "away")):
            pid, line = lines[side]
            if not pid or not line.get("bf"):
                continue
            starts.append({"pid": str(pid), "date": g["date"], "pk": pk, "team": g[side], "opp": g[opp_side],
                           "outs": line["outs"], "bf": line["bf"], "k": line["k"], "postseason": False,
                           "source": "statcast"})
        # team batting K/PA from every PA
        pas = {}
        for r in rows:
            i = r.get("atBatIndex")
            if i is not None and r.get("events"):
                pas[i] = r
        # without half info per PA here, attribute by pitcher -> team via starters is lossy; skip team rates
    return starts, team_games


def load_settled_props(data_root, since="2026-08-01"):
    out = []
    for d, f in sorted(_partition_files(data_root, "settlements").items()):
        if d < since:
            continue
        for r in _jsonl(f):
            fam = r.get("marketFamily")
            if fam not in ("pitcher_strikeouts", "pitcher_outs") or r.get("result") not in ("YES", "NO"):
                continue
            ev = r.get("settlementEvidence") or {}
            cands = ev.get("candidates") or []
            if len(cands) != 1 or ev.get("actualValue") is None:
                continue
            t = r["marketTicker"]
            m = re.match(r"^KXMLB(KS|OUTS)-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)-([A-Z]+?)[A-Z][A-Z]+\d*-(\d+)$", t)
            parts = t.split("-")
            try:
                thr = int(parts[-1])
            except ValueError:
                continue
            out.append({"ticker": t, "family": fam, "pid": str(cands[0]["playerId"]), "side": cands[0].get("side"),
                        "pk": str(ev.get("gamePk") or r.get("gameId")), "date": None, "event": parts[1],
                        "threshold": thr, "y": 1 if r["result"] == "YES" else 0, "actual": ev["actualValue"]})
    return out


def latest_pregame_quotes(data_root, tickers, dates):
    best = {}
    files = _partition_files(data_root, "observations")
    for d in sorted(set(dates)):
        f = files.get(d)
        if not f:
            continue
        for o in _jsonl(f):
            t = o.get("marketTicker")
            if t not in tickers or not o.get("isValidPregameObservation"):
                continue
            yb, ya = o.get("yesBid"), o.get("yesAsk")
            if yb is None or ya is None:
                continue
            # older observation rows store cents, newer ones dollars
            if ya > 1.0 or yb > 1.0:
                yb, ya = yb / 100.0, ya / 100.0
            if ya <= 0 or ya > 1.0 or ya < yb:
                continue
            cur = best.get(t)
            if cur is None or o["capturedAt"] > cur["capturedAt"]:
                best[t] = {"capturedAt": o["capturedAt"], "mid": (yb + ya) / 2.0, "ask": ya, "bid": yb,
                           "gameId": o.get("gameId") or o.get("mlbGameId"), "checkpoint": o.get("checkpoint")}
    return best


# ------------------------------------------------------------------ history / features

class History:
    def __init__(self, starts, team_games):
        self.by_pid = defaultdict(list)
        for s in sorted(starts, key=lambda s: (s["date"], str(s.get("pk", "")))):
            self.by_pid[s["pid"]].append(s)
        self.team = defaultdict(list)
        for t in sorted(team_games, key=lambda t: t["date"]):
            self.team[t["team"]].append(t)

    def before(self, pid, date):
        return [s for s in self.by_pid.get(pid, []) if s["date"] < date]

    def opp_rate(self, team, date, season_only=True):
        rows = [t for t in self.team.get(team, []) if t["date"] < date and (not season_only or t["date"][:4] == date[:4])]
        pa = sum(t["pa"] for t in rows)
        k = sum(t["k"] for t in rows)
        return (k / pa if pa else None), pa


# ------------------------------------------------------------------ metrics

def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def logloss(ps, ys):
    e = 1e-6
    return -sum(y * math.log(min(1 - e, max(e, p))) + (1 - y) * math.log(min(1 - e, max(e, 1 - p))) for p, y in zip(ps, ys)) / len(ps)


def calib(ps, ys, iters=50):
    """Logistic recalibration y ~ a + b*logit(p) (Newton). Returns (intercept, slope)."""
    e = 1e-4
    xs = [math.log(min(1 - e, max(e, p)) / (1 - min(1 - e, max(e, p)))) for p in ps]
    a, b = 0.0, 1.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            q = 1 / (1 + math.exp(-max(-30.0, min(30.0, a + b * x))))
            r = y - q
            w = q * (1 - q)
            ga += r; gb += r * x
            haa += w; hab += w * x; hbb += w * x * x
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (-hab * ga + haa * gb) / det
        a += da; b += db
        if abs(da) + abs(db) < 1e-9:
            break
    return round(a, 4), round(b, 4)


def ece(ps, ys, bins=10):
    tot = 0.0
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, p in enumerate(ps) if (lo <= p < hi) or (i == bins - 1 and p == 1.0)]
        if not idx:
            continue
        mp = sum(ps[j] for j in idx) / len(idx)
        my = sum(ys[j] for j in idx) / len(idx)
        tot += len(idx) / len(ps) * abs(mp - my)
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx), "mean_p": round(mp, 4), "hit_rate": round(my, 4)})
    return round(tot, 4), out


def summarize(ps, ys, clusters=None):
    a, b = calib(ps, ys)
    e, rel = ece(ps, ys)
    return {"n": len(ps), "independent_pitcher_games": len(set(clusters)) if clusters else None,
            "brier": round(brier(ps, ys), 5), "log_loss": round(logloss(ps, ys), 5),
            "calibration_intercept": a, "calibration_slope": b, "ece": e,
            "mean_p": round(sum(ps) / len(ps), 4), "base_rate": round(sum(ys) / len(ys), 4), "reliability": rel}


def paired_delta_ci(pa, pb, ys, clusters, reps=1000, seed=7):
    """Brier(pa) - Brier(pb) with a bootstrap over clusters (pitcher-games)."""
    groups = defaultdict(list)
    for i, c in enumerate(clusters):
        groups[c].append(i)
    keys = list(groups)
    rng = random.Random(seed)

    def delta(idx):
        return sum((pa[i] - ys[i]) ** 2 - (pb[i] - ys[i]) ** 2 for i in idx) / len(idx)

    point = delta(range(len(ys)))
    sims = []
    for _ in range(reps):
        idx = [i for k in (rng.choice(keys) for _ in keys) for i in groups[k]]
        sims.append(delta(idx))
    sims.sort()
    return {"delta_brier": round(point, 5), "ci95": [round(sims[int(0.025 * reps)], 5), round(sims[int(0.975 * reps)], 5)]}


# ------------------------------------------------------------------ fitting (DEV only)

def _proj_inputs(hist, s):
    prior = hist.before(s["pid"], s["date"])
    opp_k, opp_pa = hist.opp_rate(s["opp"], s["date"]) if s.get("opp") else (None, 0)
    return prior, opp_k, opp_pa


def fit(dev, hist):
    params = dict(P.DEFAULT_PARAMS)
    usable = [s for s in dev if len(hist.before(s["pid"], s["date"])) >= 1 and s.get("bf")]
    params["league_outs"] = round(sum(s["outs"] for s in dev) / len(dev), 3)
    params["league_k_rate"] = round(sum(s["k"] for s in dev if s.get("bf")) / sum(s["bf"] for s in dev if s.get("bf")), 4)
    params["league_nonout_per_out"] = round(sum(s["bf"] - s["outs"] for s in dev if s.get("bf")) / sum(s["outs"] for s in dev if s.get("bf")), 4)
    summaries = [(s, P.history_summary(hist.before(s["pid"], s["date"]))) for s in usable]
    # k_outs: minimize squared error of outs vs shrunk mean
    best = None
    for k_outs in (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0):
        pp = {**params, "k_outs": k_outs}
        err = sum((s["outs"] - P.expected_outs(sm, pp)) ** 2 for s, sm in summaries)
        if best is None or err < best[0]:
            best = (err, k_outs)
    params["k_outs"] = best[1]
    # residual table by mu bucket
    buckets = [[] for _ in range(len(RESID_EDGES) + 1)]
    for s, sm in summaries:
        mu = P.expected_outs(sm, params)
        buckets[P._bucket(RESID_EDGES, mu)].append(round(s["outs"] - mu, 2))
    params["outs_residuals"] = {"edges": RESID_EDGES, "buckets": buckets,
                                "bucket_sizes": [len(b) for b in buckets]}
    # k_bf and opponent alpha: minimize binomial deviance of K given actual BF
    def k_dev(pp):
        tot = 0.0
        for s, sm in summaries:
            opp_k, opp_pa = hist.opp_rate(s["opp"], s["date"])
            p, _ = P.k_rate(sm, pp, opp_k, opp_pa)
            tot -= s["k"] * math.log(p) + (s["bf"] - s["k"]) * math.log(1 - p)
        return tot
    best = None
    for k_bf in (80.0, 150.0, 220.0, 300.0, 450.0):
        for alpha in (0.0, 0.5, 0.75, 1.0):
            pp = {**params, "k_bf": k_bf, "opp_alpha": alpha}
            d = k_dev(pp)
            if best is None or d < best[0]:
                best = (d, k_bf, alpha)
    params["k_bf"], params["opp_alpha"] = best[1], best[2]
    # phi: beta-binomial MLE on K | BF
    best = None
    for phi in (15.0, 25.0, 40.0, 60.0, 100.0, 200.0, 1000.0):
        ll = 0.0
        for s, sm in summaries:
            opp_k, opp_pa = hist.opp_rate(s["opp"], s["date"])
            p, _ = P.k_rate(sm, params, opp_k, opp_pa)
            pm = P._betabinom_pmf_all(s["bf"], p, phi)
            ll += math.log(max(1e-12, pm[s["k"]]))
        if best is None or ll > best[0]:
            best = (ll, phi)
    params["phi"] = best[1]
    params["fit"] = {"dev_starts": len(dev), "dev_usable": len(usable), "dev_seasons": sorted({s["date"][:4] for s in dev})}
    return params


def old_engine_probs(prior, opp_k, family, thr):
    rec = [s for s in prior if s.get("outs") is not None][-10:]
    if not rec:
        return None
    ip = sum(s["outs"] for s in rec) / len(rec) / 3.0
    bf = sum(s.get("bf") or 0 for s in rec)
    kp = 100.0 * sum(s.get("k") or 0 for s in rec) / bf if bf else None
    if kp is None:
        return None
    r = OLD.project_pitcher_workload(avg_ip_per_start=ip, k_pct=kp, opponent_k_pct=(100 * opp_k if opp_k else None))
    if r.get("insufficientWorkloadData"):
        return None
    return r["pOutsAtLeast"](thr) if family == "pitcher_outs" else r["pStrikeoutsAtLeast"](thr)


def evaluate_holdout(starts, hist, params):
    rows = {"pitcher_outs": {"new": [], "old": [], "y": [], "c": []},
            "pitcher_strikeouts": {"new": [], "old": [], "y": [], "c": []}}
    for s in starts:
        prior, opp_k, opp_pa = _proj_inputs(hist, s)
        if len(prior) < 3 or not s.get("bf"):
            continue
        pr = P.project(prior, params, opp_k_rate=opp_k, opp_pa=opp_pa)
        for fam, thrs, actual, table in (("pitcher_outs", OUTS_THRESHOLDS, s["outs"], pr["p_outs_ge"]),
                                         ("pitcher_strikeouts", K_THRESHOLDS, s["k"], pr["p_k_ge"])):
            for n in thrs:
                o = old_engine_probs(prior, opp_k, fam, n)
                if o is None:
                    continue
                rows[fam]["new"].append(P.p_at_least(table, n))
                rows[fam]["old"].append(o)
                rows[fam]["y"].append(1 if actual >= n else 0)
                rows[fam]["c"].append(f"{s['pk']}:{s['pid']}")
    out = {}
    for fam, r in rows.items():
        if not r["y"]:
            continue
        out[fam] = {"model": summarize(r["new"], r["y"], r["c"]), "incumbent_engine": summarize(r["old"], r["y"], r["c"]),
                    "model_minus_incumbent": paired_delta_ci(r["new"], r["old"], r["y"], r["c"])}
    return out


def postseason_starts(settled, schedule_dates):
    """One row per postseason pitcher-game with its actual outs / K (from settlements)."""
    games = {}
    for r in settled:
        if r["date"] is None or r["date"] < POSTSEASON_START:
            continue
        key = (r["pk"], r["pid"])
        g = games.setdefault(key, {"pid": r["pid"], "pk": r["pk"], "date": r["date"], "outs": None, "k": None,
                                   "postseason": True, "source": "settlement"})
        if r["family"] == "pitcher_outs":
            g["outs"] = r["actual"]
        else:
            g["k"] = r["actual"]
    return list(games.values())


def postseason_shift(ps_starts, hist, params, before_date):
    resid = []
    for g in ps_starts:
        if g["date"] >= before_date or g["outs"] is None:
            continue
        prior = [s for s in hist.before(g["pid"], g["date"])]
        if len(prior) < 3:
            continue
        mu = P.expected_outs(P.history_summary(prior), {**params, "postseason_shift_outs": 0.0})
        resid.append(g["outs"] - mu)
    if not resid:
        return 0.0, 0, None
    raw = sum(resid) / len(resid)
    shrunk = raw * len(resid) / (len(resid) + POSTSEASON_SHRINK_N0)
    return round(shrunk, 3), len(resid), round(raw, 3)


def evaluate_market(settled, quotes, hist, params, ps_starts, team_of):
    rows = defaultdict(lambda: {"new": [], "old": [], "mkt": [], "y": [], "c": [], "post": [], "date": [], "ask": []})
    for r in settled:
        q = quotes.get(r["ticker"])
        if not q or r["date"] is None:
            continue
        prior = hist.before(r["pid"], r["date"])
        if len(prior) < 3:
            continue
        team = team_of(r)
        opp = None
        ev = r["event"]
        m = re.match(r"^\d{2}[A-Z]{3}\d{2}\d{4}([A-Z]+)$", ev)
        if m and team and m.group(1).startswith(team):
            opp = m.group(1)[len(team):]
        elif m and team and m.group(1).endswith(team):
            opp = m.group(1)[:-len(team)]
        opp_k, opp_pa = hist.opp_rate(opp, r["date"]) if opp else (None, 0)
        post = r["date"] >= POSTSEASON_START
        pp = dict(params)
        if post:
            pp["postseason_shift_outs"], _n, _raw = postseason_shift(ps_starts, hist, params, r["date"])
        pr = P.project(prior, pp, postseason=post, opp_k_rate=opp_k, opp_pa=opp_pa)
        table = pr["p_outs_ge"] if r["family"] == "pitcher_outs" else pr["p_k_ge"]
        o = old_engine_probs(prior, opp_k, r["family"], r["threshold"])
        if o is None:
            continue
        d = rows[r["family"]]
        d["new"].append(P.p_at_least(table, r["threshold"]))
        d["old"].append(o)
        d["mkt"].append(q["mid"])
        d["ask"].append(q["ask"])
        d["y"].append(r["y"])
        d["c"].append(f"{r['pk']}:{r['pid']}")
        d["post"].append(post)
        d["date"].append(r["date"])
    out = {}
    for fam, d in rows.items():
        if not d["y"]:
            continue
        res = {"n_markets": len(d["y"]), "independent_pitcher_games": len(set(d["c"])), "dates": len(set(d["date"])),
               "date_range": [min(d["date"]), max(d["date"])],
               "model": summarize(d["new"], d["y"], d["c"]), "incumbent_engine": summarize(d["old"], d["y"], d["c"]),
               "kalshi_mid": summarize(d["mkt"], d["y"], d["c"]),
               "model_minus_kalshi": paired_delta_ci(d["new"], d["mkt"], d["y"], d["c"]),
               "incumbent_minus_kalshi": paired_delta_ci(d["old"], d["mkt"], d["y"], d["c"])}
        # chronological halves (no refit: params are DEV-only), and postseason subset
        dates = sorted(set(d["date"]))
        cut = dates[int(len(dates) * 0.6)] if len(dates) > 2 else dates[-1]
        for name, sel in (("late_40pct_dates", [i for i, x in enumerate(d["date"]) if x >= cut]),
                          ("postseason", [i for i, x in enumerate(d["post"]) if x])):
            if len(sel) >= 10:
                pick = lambda arr: [arr[i] for i in sel]  # noqa: E731
                res[name] = {"from": min(pick(d["date"])), "n": len(sel), "pitcher_games": len(set(pick(d["c"]))),
                             "model_brier": round(brier(pick(d["new"]), pick(d["y"])), 5),
                             "kalshi_brier": round(brier(pick(d["mkt"]), pick(d["y"])), 5),
                             "incumbent_brier": round(brier(pick(d["old"]), pick(d["y"])), 5),
                             "model_minus_kalshi": paired_delta_ci(pick(d["new"]), pick(d["mkt"]), pick(d["y"]), pick(d["c"]))}
        # edge buckets: model - market, realized YES rate vs market mid; ROI buying YES at the ask
        eb = []
        for lo, hi in ((-1, -0.10), (-0.10, -0.05), (-0.05, 0.05), (0.05, 0.10), (0.10, 1.0)):
            idx = [i for i in range(len(d["y"])) if lo <= d["new"][i] - d["mkt"][i] < hi]
            if not idx:
                continue
            buys = [i for i in idx if d["new"][i] > d["mkt"][i]]
            roi = (sum((d["y"][i] - d["ask"][i]) for i in buys) / sum(d["ask"][i] for i in buys)) if buys else None
            eb.append({"model_minus_mid": f"[{lo:+.2f},{hi:+.2f})", "n": len(idx),
                       "mean_mid": round(sum(d["mkt"][i] for i in idx) / len(idx), 4),
                       "mean_model": round(sum(d["new"][i] for i in idx) / len(idx), 4),
                       "yes_rate": round(sum(d["y"][i] for i in idx) / len(idx), 4),
                       "yes_at_ask_roi_before_fees": None if roi is None else round(roi, 4)})
        res["edge_buckets"] = eb
        out[fam] = res
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--asof", default="2026-10-07")
    ap.add_argument("--out-params", default=os.path.join(ROOT, "data", "edgelab", "analytics", "mlb_pitcher_prop_model_params.json"))
    ap.add_argument("--out-report", default=os.path.join(ROOT, "data", "edgelab", "analytics", "mlb_pitcher_prop_calibration_2026-10-07.json"))
    args = ap.parse_args(argv)
    schedule = load_schedule(args.data_root)
    box_starts, team_games = load_boxscore_starts(args.data_root, schedule)
    last_box = max(s["date"] for s in box_starts if s["date"][:4] == "2026")
    sc_starts, _ = load_statcast_starts(args.data_root, schedule, last_box)
    settled = load_settled_props(args.data_root)
    # settlement rows -> dates via the observation / slate game ids
    quotes = latest_pregame_quotes(args.data_root, {r["ticker"] for r in settled},
                                   [d for d in _partition_files(args.data_root, "observations") if d >= "2026-08-01"])
    ev_date = {}
    for r in settled:
        m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})", r["event"])
        mon = {"AUG": 8, "SEP": 9, "OCT": 10, "JUL": 7}.get(m.group(2)) if m else None
        if mon:
            r["date"] = f"20{m.group(1)}-{mon:02d}-{m.group(3)}"
    ps = postseason_starts(settled, None)
    all_starts = box_starts + sc_starts + [g for g in ps if g["outs"] is not None and g["date"] < args.asof]
    hist = History(all_starts, team_games)
    dev = [s for s in box_starts if s["date"][:4] in ("2022", "2023", "2024")]
    params = fit(dev, hist)
    shift, n_ps, raw = postseason_shift(ps, hist, params, args.asof)
    params["postseason_shift_outs"], params["postseason_shift_n"], params["postseason_shift_raw"] = shift, n_ps, raw
    params["postseason_shrink_n0"] = POSTSEASON_SHRINK_N0
    params["asof"] = args.asof
    params["model"] = "lib/research/pitcher_prop_projection.py"
    holdout = [s for s in box_starts + sc_starts if s["date"][:4] in ("2025", "2026")]
    pitcher_team = {}
    for s in box_starts + sc_starts:
        pitcher_team[s["pid"]] = s["team"]

    def team_of(r):
        return pitcher_team.get(r["pid"])

    report = {
        "asof": args.asof,
        "data": {"boxscore_starts": len(box_starts), "boxscore_last_date_2026": last_box,
                 "statcast_derived_starts": len(sc_starts), "postseason_starts_with_outs": n_ps,
                 "settled_pitcher_prop_markets": len(settled), "markets_with_valid_pregame_quote": len(quotes)},
        "params": {k: v for k, v in params.items() if k != "outs_residuals"},
        "holdout_2025_2026_regular_season": evaluate_holdout(holdout, hist, params),
        "kalshi_2026_settled_markets": evaluate_market(settled, quotes, hist, params, ps, team_of),
    }
    # Compact regular-season history the production publication reads (postseason starts are
    # added at export time from settlements, so they stay current without re-running this).
    hist_path = os.path.join(os.path.dirname(args.out_params), "mlb_pitcher_start_history.json.gz")
    keep = [{k: s[k] for k in ("pid", "date", "team", "opp", "outs", "bf", "k")}
            for s in sorted(box_starts + sc_starts, key=lambda s: (s["date"], s["pid"])) if s["date"] >= "2025-01-01"]
    with gzip.open(hist_path, "wt", encoding="utf-8") as fh:
        json.dump({"asof": args.asof, "starts": keep,
                   "team_games": sorted([t for t in team_games if t["date"] >= "2026-01-01"], key=lambda t: (t["date"], t["team"]))},
                  fh, separators=(",", ":"), sort_keys=True)
    report["data"]["history_artifact"] = os.path.relpath(hist_path, ROOT)
    os.makedirs(os.path.dirname(args.out_params), exist_ok=True)
    with open(args.out_params, "w", encoding="utf-8") as fh:
        json.dump(params, fh, sort_keys=True)
    with open(args.out_report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
    print(json.dumps({k: report[k] for k in ("data", "params")}, indent=1))
    for sect in ("holdout_2025_2026_regular_season", "kalshi_2026_settled_markets"):
        for fam, r in report[sect].items():
            brief = {k: (v["brier"] if isinstance(v, dict) and "brier" in v else v) for k, v in r.items()
                     if k in ("model", "incumbent_engine", "kalshi_mid", "model_minus_kalshi", "model_minus_incumbent",
                              "n_markets", "independent_pitcher_games", "postseason", "late_40pct_dates")}
            print(sect, fam, json.dumps(brief))
    return 0


if __name__ == "__main__":
    sys.exit(main())
