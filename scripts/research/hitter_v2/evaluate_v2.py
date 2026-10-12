#!/usr/bin/env python3
"""
scripts/research/hitter_v2/evaluate_v2.py
=========================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Scores the preregistered comparison
(docs/research/HITTER_V2_PREREG.md) on the 2026 holdout and applies the frozen
decision rule mechanically:

  arms: v2 (MLB_HITTER_V2_PURE), w2 (frozen MLB_HITTER_W2_PURE_V1), baseline
        (MLB_HITTER_W2_BASELINE_V1 + BB/K/HRR), champion (production hitter engine
        rerun, git f7371b2, with HRR from the same draws)
  populations: PIT (pregame captured lineups, 2026-08-12..09-27), PIT_matched
        (PIT rows where the champion is PROJECTED), OFF_H1 (official-lineup proxy,
        2026-03-25..08-11)

Writes data/research/hitter_v2/holdout_2026/{scorecard.json, verdict.json, diagnostics.json}.
"""
import argparse
import gzip
import json
import os
import pickle
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib.research import hitter_w2_engine as W2  # noqa: E402
from lib.research import hitter_v2_engine as V  # noqa: E402
import scoring_v2 as S  # noqa: E402

DECISION = ("H", "TB", "HR", "RBI", "R", "BB", "K", "HRR")
REPORTED = ("PA",)
IMPROVE_VS_CHAMPION = ("H", "TB", "RBI", "R")
MAE_MARGIN_REL = 0.02
CAL_PIT80_TOL = 0.03
CAL_ECE_MAX = 0.025
CAL_CITL_MAX = 0.02
LEGACY_PRIMARY = ("crps", "abs", "sq", "brier1", "logloss1")
LEGACY_STATS = ("H", "TB", "HR", "RBI", "R")
ARMS_ALL = ("v2", "w2", "baseline", "champion")


def load_champion(path):
    out = {}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("status") == "PROJECTED":
                out[(int(r["gameId"]), int(r["playerId"]))] = r
    return out


def rows_from_cache(fc, champion=None):
    rows = []
    counts = defaultdict(int)
    for pk in sorted(fc["games"]):
        g = fc["games"][pk]
        for side in ("A", "H"):
            rec = g["records"][side]
            if not rec.get("scored", True):
                counts["team_games_not_scored(proxy_lineup)"] += 1
                continue
            counts["team_games_scored"] += 1
            for i, b in enumerate(rec["batters"]):
                if b["y"] is None or not b["started_any_slot"]:
                    counts["player_games_not_in_box_score_starting_lineup"] += 1
                    continue
                counts["player_games"] += 1
                ch = champion.get((pk, b["playerId"])) if champion is not None else None
                if champion is not None and ch is None:
                    counts["player_games_without_champion_projection"] += 1
                sub = player_subgroups(rec, b)
                for st in V.STATS:
                    pm = {"v2": g["v2"][side]["players"][i][st],
                          "baseline": g["baseline"][side][i][st]}
                    w = g["w2"][side]["players"][i].get(st)
                    if w is not None:
                        pm["w2"] = w
                    if ch is not None and st in ch:
                        pm["champion"] = {int(k): v for k, v in ch[st]["pmf"].items()}
                    rows.append({"cluster": pk, "stat": st, "y": b["y"][st], "pmfs": pm, "sub": sub,
                                 "matched": ch is not None,
                                 "v2_joint": g["v2"][side]["players"][i]["_joint"]})
    return rows, dict(counts)


def player_subgroups(rec, b):
    h = rec["oppStarterHand"] or "R"
    if b["batSide"] == "S":
        plat = "switch"
    else:
        plat = "same_hand" if b["batSide"] == h else "opposite_hand"
    pen = 0.0
    a_, b_ = rec.get("pen_cur"), rec.get("pen_prev")
    cnt = [(a_[i] if a_ else 0) + 0.5 * (b_[i] if b_ else 0) for i in range(len(W2.CATS))] if (a_ or b_) else None
    if cnt:
        n = sum(cnt)
        pen = (cnt[W2.CI["BB"]] + cnt[W2.CI["HBP"]] + sum(cnt[W2.CI[c]] for c in ("1B", "2B", "3B", "HR"))) / n
    return {"slot": b["slot"], "home": rec["home"], "platoon": plat, "pen_obp": pen, "date": rec["date"],
            "month": rec["date"][:7], "position": b.get("position")}


def score(rows):
    return S.score_rows(rows)


def _sel(scored, st, arms, pred=lambda s: True):
    return [s for s in scored if s["stat"] == st and pred(s) and all(a in s["m"] for a in arms)]


def composite(scored, arms, stats, pred=lambda s: True):
    """Per player-game sum of CRPS over `stats` (each arm must have all stats)."""
    by = defaultdict(dict)
    for s in scored:
        if s["stat"] in stats and pred(s) and all(a in s["m"] for a in arms):
            by[(s["cluster"], id(s["sub"]))][s["stat"]] = s
    out = []
    for _k, d in by.items():
        if len(d) != len(stats):
            continue
        any_s = next(iter(d.values()))
        m = {a: {mt: sum(d[st]["m"][a][mt] for st in stats) for mt in ("crps", "abs_med", "abs", "sq",
                                                                         "ladder_brier", "err")} for a in arms}
        out.append({"cluster": any_s["cluster"], "stat": "COMPOSITE", "y": 0, "sub": any_s["sub"], "m": m})
    return out


def block(scored, arms, comparisons, pred=lambda s: True, stats=V.STATS, reps=1000):
    out = {}
    for st in stats:
        avail = [a for a in arms if any(a in s["m"] for s in scored if s["stat"] == st)]
        ss = _sel(scored, st, avail, pred)
        if not ss:
            continue
        summ = S.summarize(ss, avail)
        out[st] = {"summary": summ}
        for a, b in comparisons:
            if a in avail and b in avail:
                out[st][f"{b}_minus_{a}"] = S.paired_bootstrap(ss, a, b, reps=reps)
    return out


def bootstrap_composite(scored, a, b, stats, pred, reps=1000):
    comp = composite(scored, (a, b), stats, pred)
    if not comp:
        return None
    r = S.paired_bootstrap(comp, a, b, metrics=("crps",), reps=reps)
    r["stats"] = list(stats)
    return r


def legacy_bootstrap(scored, a, b, pred, reps=1000):
    out = {}
    for st in LEGACY_STATS:
        ss = _sel(scored, st, (a, b), pred)
        if ss:
            out[st] = S.paired_bootstrap(ss, a, b, metrics=LEGACY_PRIMARY, reps=reps)
    return out


# ---------------------------------------------------------------------------------------------
# Decision rule (frozen in the prereg)
# ---------------------------------------------------------------------------------------------
def _worse(ci):
    return ci[0] > 0


def _better(ci):
    return ci[1] < 0


def decide(card):
    crit = []

    def add(name, status, detail):
        crit.append({"criterion": name, "status": status, "detail": detail})

    pm = card["PIT_matched"]
    # C1 vs champion
    for st in DECISION:
        c = pm.get(st, {}).get("v2_minus_champion")
        if c is None:
            add(f"C1 vs champion CRPS {st}", "NOT_AVAILABLE", None)
            continue
        ci = c["crps"]["ci95"]
        if st in IMPROVE_VS_CHAMPION:
            add(f"C1 vs champion CRPS {st} better", "PASS" if _better(ci) else ("FAIL" if _worse(ci) else "INCONCLUSIVE"),
                c["crps"])
        else:
            add(f"C1 vs champion CRPS {st} not worse", "FAIL" if _worse(ci) else "PASS", c["crps"])
    # C2 vs W2 / C3 vs baseline, per stat on PIT and OFF_H1
    for comp, label in (("w2", "C2 vs W2"), ("baseline", "C3 vs baseline")):
        for pop in ("PIT", "OFF_H1"):
            for st in DECISION:
                c = card[pop].get(st, {}).get(f"v2_minus_{comp}")
                if c is None:
                    continue
                add(f"{label} CRPS {st} not worse ({pop})", "FAIL" if _worse(c["crps"]["ci95"]) else "PASS", c["crps"])
        cc = card["composite"].get(f"PIT:v2_minus_{comp}")
        ci = cc["crps"]["ci95"]
        add(f"{label} composite CRPS better (PIT)", "PASS" if _better(ci) else ("FAIL" if _worse(ci) else "INCONCLUSIVE"),
            cc["crps"])
        co = card["composite"].get(f"OFF_H1:v2_minus_{comp}")
        add(f"{label} composite CRPS not worse (OFF_H1)", "FAIL" if _worse(co["crps"]["ci95"]) else "PASS", co["crps"])
    # C4 MAE guardrail (MAE of the predictive median), non-inferiority with a 2% relative margin
    for comp, pop in (("champion", "PIT_matched"), ("baseline", "PIT"), ("baseline", "OFF_H1")):
        for st in DECISION:
            c = card[pop].get(st, {}).get(f"v2_minus_{comp}")
            if c is None:
                continue
            margin = MAE_MARGIN_REL * c["abs_med"]["comparator_mean"]
            ok = c["abs_med"]["ci95"][1] <= margin
            add(f"C4 MAE(median) {st} vs {comp} ({pop}) upper CI <= {MAE_MARGIN_REL:.0%} margin",
                "PASS" if ok else "FAIL", dict(c["abs_med"], margin=round(margin, 6)))
    # C5 calibration guardrails (V2 point estimates)
    for pop in ("PIT", "OFF_H1"):
        for st in DECISION:
            s = card[pop].get(st, {}).get("summary", {}).get("v2")
            if s is None:
                continue
            ok = (abs(s["pit_in80"] - 0.80) <= CAL_PIT80_TOL and s["max_rung_ece"] <= CAL_ECE_MAX
                  and s["max_rung_abs_citl"] <= CAL_CITL_MAX)
            add(f"C5 calibration {st} ({pop})", "PASS" if ok else "FAIL",
                {"pit_in80": round(s["pit_in80"], 4), "max_rung_ece": round(s["max_rung_ece"], 4),
                 "max_rung_abs_citl": round(s["max_rung_abs_citl"], 4)})
    statuses = [c["status"] for c in crit]
    if "FAIL" in statuses:
        verdict = "REJECTED"
    elif "INCONCLUSIVE" in statuses:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "ACCEPTED_RESEARCH_ONLY_PENDING_C6_C7"
    # legacy #285 rule (reported, not the decision): no primary metric worse vs champion (H2 matched) and vs
    # baseline (window 1 = OFF_H1, window 2 = PIT); CRPS H and RBI better vs champion; |bias| H and RBI smaller
    leg = []
    for comp, pop in (("champion", "PIT_matched"), ("baseline", "OFF_H1"), ("baseline", "PIT")):
        for st, d in card["legacy"][f"{pop}:v2_minus_{comp}"].items():
            for mt in LEGACY_PRIMARY:
                if _worse(d[mt]["ci95"]):
                    leg.append(f"{st} {mt} worse vs {comp} ({pop}) {d[mt]}")
    for st in ("H", "RBI"):
        d = card["PIT_matched"][st]["v2_minus_champion"]
        if not _better(d["crps"]["ci95"]):
            leg.append(f"{st} CRPS not better vs champion")
        if not _better(d["abs_bias"]["ci95"]):
            leg.append(f"{st} |bias| not smaller vs champion")
    return {"verdict": verdict, "criteria": crit, "n_fail": statuses.count("FAIL"),
            "n_inconclusive": statuses.count("INCONCLUSIVE"),
            "legacy_285_rule": {"verdict": "REJECTED" if leg else "PASS", "failures": leg}}


# ---------------------------------------------------------------------------------------------
# Diagnostics per structural error
# ---------------------------------------------------------------------------------------------
def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def diagnostics(scored, rows, arms, label, feed_root=None, season=2026):
    out = {}
    pa = [s for s in scored if s["stat"] == "PA"]
    # D1 PA by slot x home/away
    t = {}
    for slot in range(1, 10):
        for home in (False, True):
            ss = [s for s in pa if s["sub"]["slot"] == slot and s["sub"]["home"] == home]
            if not ss:
                continue
            key = f"{slot}{'H' if home else 'A'}"
            t[key] = {"n": len(ss), "actual": round(_mean(s["y"] for s in ss), 4),
                      "actual_P_ge5": round(_mean(float(s["y"] >= 5) for s in ss), 4)}
            for a in arms:
                if all(a in s["m"] for s in ss):
                    t[key][a] = round(_mean(s["m"][a]["mean"] for s in ss), 4)
                    t[key][a + "_P_ge5"] = round(_mean(s["m"][a]["p"][4] for s in ss), 4)
    out["D1_pa_by_slot_home"] = t
    # D1b PA pmf (whole population)
    dist = {"actual": defaultdict(float)}
    for s in pa:
        dist["actual"][s["y"]] += 1 / len(pa)
    out["D1b_pa_distribution_actual"] = {k: round(v, 4) for k, v in sorted(dist["actual"].items())}
    for a in arms:
        if pa and all(a in s["m"] for s in pa):
            out[f"D1b_pa_mean_{a}"] = round(_mean(s["m"][a]["mean"] for s in pa), 4)
    # D2 home vs away team PA (sum of starters) -- the home-9th truncation
    side = {}
    for home in (False, True):
        ss = [s for s in pa if s["sub"]["home"] == home]
        side["home" if home else "away"] = {"n": len(ss), "actual": round(_mean(s["y"] for s in ss), 4),
                                            **{a: round(_mean(s["m"][a]["mean"] for s in ss), 4) for a in arms
                                               if ss and all(a in s["m"] for s in ss)}}
    out["D2_pa_home_vs_away"] = side
    # D3 substitution: predicted P(starter replaced) by slot vs actual (V2 only) + slot-9/catcher subset
    sub_rows = [r for r in rows if r["stat"] == "PA"]
    act = actual_replaced(sub_rows, feed_root, season) if feed_root else {}
    d3 = {}
    for slot in range(1, 10):
        rr = [r for r in sub_rows if r["sub"]["slot"] == slot]
        if not rr:
            continue
        d3[str(slot)] = {"n": len(rr), "v2_P_replaced": round(_mean(r["v2_joint"]["P_replaced"] for r in rr), 4),
                         "actual_replaced": round(_mean(act.get((r["cluster"], r["_pid"]), 0) for r in rr), 4)
                         if act else None}
    rr = [r for r in sub_rows if r["sub"]["position"] == "C"]
    if rr:
        d3["catchers"] = {"n": len(rr), "v2_P_replaced": round(_mean(r["v2_joint"]["P_replaced"] for r in rr), 4),
                          "actual_replaced": round(_mean(act.get((r["cluster"], r["_pid"]), 0) for r in rr), 4)
                          if act else None}
    out["D3_substitution"] = d3
    # D4 walk rate: BB per PA forecast vs actual
    bb = {s_key(s): s for s in scored if s["stat"] == "BB"}
    d4 = {}
    for a in arms:
        num = den = 0.0
        ok = True
        for s in pa:
            b = bb.get(s_key(s))
            if b is None or a not in b["m"] or a not in s["m"]:
                ok = False
                break
            num += b["m"][a]["mean"]
            den += s["m"][a]["mean"]
        if ok and den:
            d4[a] = round(num / den, 5)
    d4["actual"] = round(sum(b["y"] for b in bb.values()) / max(1, sum(s["y"] for s in pa)), 5)
    out["D4_bb_per_pa"] = d4
    # D5 platoon subgroups: bias / CRPS for H, TB, BB, K
    d5 = {}
    for grp in ("same_hand", "opposite_hand", "switch"):
        for st in ("H", "TB", "BB", "K", "HR"):
            ss = [s for s in scored if s["stat"] == st and s["sub"]["platoon"] == grp]
            if not ss:
                continue
            d5[f"{grp}:{st}"] = {"n": len(ss), "actual": round(_mean(s["y"] for s in ss), 4),
                                 **{a: {"bias": round(_mean(s["m"][a]["err"] for s in ss), 4),
                                        "crps": round(_mean(s["m"][a]["crps"] for s in ss), 4)}
                                    for a in arms if all(a in s["m"] for s in ss)}}
    out["D5_platoon"] = d5
    # D6 bullpen quality tercile (opposing team as-of bullpen OBP allowed)
    vals = sorted(s["sub"]["pen_obp"] for s in pa)
    if vals:
        cut = (vals[len(vals) // 3], vals[2 * len(vals) // 3])
        d6 = {}
        for st in ("H", "TB", "R", "RBI"):
            for i, name in enumerate(("best_pen", "mid_pen", "worst_pen")):
                lo = -1 if i == 0 else cut[i - 1]
                hi = cut[i] if i < 2 else 2
                ss = [s for s in scored if s["stat"] == st and lo < s["sub"]["pen_obp"] <= hi]
                if ss:
                    d6[f"{name}:{st}"] = {"n": len(ss), "actual": round(_mean(s["y"] for s in ss), 4),
                                          **{a: round(_mean(s["m"][a]["err"] for s in ss), 4) for a in arms
                                             if all(a in s["m"] for s in ss)}}
        out["D6_bullpen_tercile_bias"] = d6
    # D7 baserunning: R and RBI bias by slot group; R per time on base
    d7 = {}
    for st in ("R", "RBI", "HRR"):
        for grp, slots in (("1-2", (1, 2)), ("3-5", (3, 4, 5)), ("6-9", (6, 7, 8, 9))):
            ss = [s for s in scored if s["stat"] == st and s["sub"]["slot"] in slots]
            if ss:
                d7[f"{st}:{grp}"] = {"n": len(ss), "actual": round(_mean(s["y"] for s in ss), 4),
                                     **{a: round(_mean(s["m"][a]["err"] for s in ss), 4) for a in arms
                                        if all(a in s["m"] for s in ss)}}
    out["D7_runs_rbi_bias_by_slot"] = d7
    out["label"] = label
    return out


def s_key(s):
    return (s["cluster"], id(s["sub"]))


def actual_replaced(pa_rows, feed_root, season=2026):
    """(gamePk, playerId) -> 1 if a non-starter batted in that starter's slot (box score)."""
    sd = W2.load_season(season, feed_root)
    sub_slots = set()
    starter_slot = {}
    for r in sd["batting"]:
        pk = int(r["gamePk"])
        if r["isStarter"] == "1":
            starter_slot[(pk, int(r["playerId"]))] = (r["side"], int(r["slot"]))
        elif W2._int(r["PA"]) > 0:
            sub_slots.add((pk, r["side"], W2._int(r["slot"])))
    out = {}
    for r in pa_rows:
        k = (r["cluster"], r["_pid"])
        ss = starter_slot.get(k)
        out[k] = int(bool(ss) and (k[0], ss[0], ss[1]) in sub_slots)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pit-cache", required=True)
    ap.add_argument("--off-cache", required=True)
    ap.add_argument("--champion", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--feed-root", default=os.path.join(_ROOT, "data", "research", "hitter_w2", "mlb_feed"))
    ap.add_argument("--reps", type=int, default=1000)
    ap.add_argument("--season", type=int, default=2026)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    champ = load_champion(a.champion)
    caches = {}
    for name, path in (("PIT", a.pit_cache), ("OFF_H1", a.off_cache)):
        with gzip.open(path, "rb") as f:
            caches[name] = pickle.load(f)
    rows_pit, counts_pit = rows_from_cache(caches["PIT"], champ)
    rows_off, counts_off = rows_from_cache(caches["OFF_H1"], None)
    _attach_pids(rows_pit, caches["PIT"])
    _attach_pids(rows_off, caches["OFF_H1"])
    sc_pit = score(rows_pit)
    for s, r in zip(sc_pit, rows_pit):
        s["matched"] = r["matched"]
    sc_off = score(rows_off)
    cmp_all = [("champion", "v2"), ("w2", "v2"), ("baseline", "v2"), ("champion", "w2")]
    card = {"prereg": "docs/research/HITTER_V2_PREREG.md", "meta": {k: v for k, v in caches["PIT"]["meta"].items()
                                                                  if k != "excluded"},
            "counts": {"PIT": counts_pit, "OFF_H1": counts_off},
            "excluded_team_games": {"PIT": caches["PIT"]["meta"]["excluded"], "OFF_H1": caches["OFF_H1"]["meta"]["excluded"]}}
    matched = lambda s: s["matched"]  # noqa: E731
    card["PIT_matched"] = block(sc_pit, ARMS_ALL, cmp_all, matched, reps=a.reps)
    card["PIT"] = block(sc_pit, ("v2", "w2", "baseline"), [("w2", "v2"), ("baseline", "v2")], reps=a.reps)
    card["OFF_H1"] = block(sc_off, ("v2", "w2", "baseline"), [("w2", "v2"), ("baseline", "v2")], reps=a.reps)
    w2_stats = tuple(st for st in DECISION if st not in ("BB", "K"))
    card["composite"] = {
        "PIT:v2_minus_w2": bootstrap_composite(sc_pit, "w2", "v2", w2_stats, lambda s: True, a.reps),
        "OFF_H1:v2_minus_w2": bootstrap_composite(sc_off, "w2", "v2", w2_stats, lambda s: True, a.reps),
        "PIT:v2_minus_baseline": bootstrap_composite(sc_pit, "baseline", "v2", DECISION, lambda s: True, a.reps),
        "OFF_H1:v2_minus_baseline": bootstrap_composite(sc_off, "baseline", "v2", DECISION, lambda s: True, a.reps),
        "PIT_matched:v2_minus_champion": bootstrap_composite(sc_pit, "champion", "v2", DECISION, matched, a.reps),
        "PIT_matched:w2_minus_champion": bootstrap_composite(sc_pit, "champion", "w2", w2_stats, matched, a.reps),
    }
    card["legacy"] = {"PIT_matched:v2_minus_champion": legacy_bootstrap(sc_pit, "champion", "v2", matched, a.reps),
                      "OFF_H1:v2_minus_baseline": legacy_bootstrap(sc_off, "baseline", "v2", lambda s: True, a.reps),
                      "PIT:v2_minus_baseline": legacy_bootstrap(sc_pit, "baseline", "v2", lambda s: True, a.reps)}
    coh = {"player_draw_sets": 0, "incoherent": 0}
    for r in rows_pit + rows_off:
        if r["stat"] == "PA":
            coh["player_draw_sets"] += 1
            coh["incoherent"] += int(not r["v2_joint"]["coherent"])
    ident = sum(int(not g["v2"][s]["identity_ok"]) for c in caches.values() for g in c["games"].values() for s in ("A", "H"))
    card["coherence"] = dict(coh, team_run_identity_violations=ident)
    verdict = decide(card)
    diags = {"PIT_matched": diagnostics([s for s in sc_pit if s["matched"]], [r for r in rows_pit if r["matched"]],
                                        ARMS_ALL, "PIT_matched", a.feed_root, a.season),
             "PIT": diagnostics(sc_pit, rows_pit, ("v2", "w2", "baseline"), "PIT", a.feed_root, a.season),
             "OFF_H1": diagnostics(sc_off, rows_off, ("v2", "w2", "baseline"), "OFF_H1", a.feed_root, a.season)}
    for name, obj in (("scorecard.json", card), ("verdict.json", verdict), ("diagnostics.json", diags)):
        with open(os.path.join(a.out_dir, name), "w") as f:
            json.dump(obj, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({"verdict": verdict["verdict"], "n_fail": verdict["n_fail"],
                      "n_inconclusive": verdict["n_inconclusive"], "legacy": verdict["legacy_285_rule"]["verdict"],
                      "counts": card["counts"], "coherence": card["coherence"]}, indent=1))


def _attach_pids(rows, fc):
    it = iter(rows)
    for pk in sorted(fc["games"]):
        g = fc["games"][pk]
        for side in ("A", "H"):
            rec = g["records"][side]
            if not rec.get("scored", True):
                continue
            for b in rec["batters"]:
                if b["y"] is None or not b["started_any_slot"]:
                    continue
                for _st in V.STATS:
                    r = next(it)
                    assert r["cluster"] == pk
                    r["_pid"] = b["playerId"]


if __name__ == "__main__":
    main()
