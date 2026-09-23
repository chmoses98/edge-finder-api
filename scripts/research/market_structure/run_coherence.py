#!/usr/bin/env python3
"""
MRV-COH-001/002/003 (+ COH-004/005 where the record carries the families) and
MRV-COH-006 on the recovered 1-minute exchange record.  RESEARCH ONLY.

Usage: run_coherence.py --raw-root <dir with candles/> [--dates 2026-08-02,...]
Writes data/edgelab/research_artifacts/market_structure/coherence_1min.json
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import REPO, write_artifact, now_iso  # noqa: E402

from lib.edgelab.research.market_structure import candles as CD, coherence as CO, economics as E, stats as ST  # noqa: E402
from lib.edgelab.research.market_structure.settlements import build_outcomes  # noqa: E402

FAMS = ("game_result", "game_total", "inning_result", "inning_total", "team_total", "winning_margin")


def ladder_quotes(items, minute):
    q = {}
    for ident, series, _ in items:
        v = series.get(minute)
        if v is None or v[0] is None or v[1] is None:
            continue
        q[ident["rung"]] = (v[0], v[1])
    return q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-root", required=True)
    ap.add_argument("--dates", default="")
    ap.add_argument("--first-before", type=int, default=240)
    args = ap.parse_args()
    dates = args.dates.split(",") if args.dates else CD.available_dates(args.raw_root)
    outcomes = build_outcomes(REPO)

    counters = {k: collections.Counter() for k in ("LADDER_TOTAL", "LADDER_F5TOTAL", "THREE_WAY_F5", "THREE_WAY_F3",
                                                    "THREE_WAY_F7", "CROSS_HORIZON", "TEAM_VS_GAME", "SPREAD_VS_ML")}
    slack_hist = {k: collections.Counter() for k in counters}
    slack_all = {k: collections.Counter() for k in counters}   # binned pre-fee slack for ALL adjacent/first pairs (negative = margin to arb)
    three_sum = {k: collections.Counter() for k in ("THREE_WAY_F5", "THREE_WAY_F3", "THREE_WAY_F7")}
    post_fee_events = []
    persist = collections.Counter()
    episodes = []          # COH-006 pre-fee ladder inversion episodes
    games_seen = set()
    dates_seen = set()
    minutes_checked = 0
    prev_violation_keys = {}

    for date in dates:
        items = list(CD.iter_date(args.raw_root, date, FAMS))
        if not items:
            continue
        dates_seen.add(date)
        for gkey, gitems in CD.group_by_game(items).items():
            games_seen.add(gkey)
            start = CD.scheduled_start_ts(gitems[0][0])
            by = collections.defaultdict(list)
            for it in gitems:
                ident = it[0]
                by[(ident["seriesTicker"], ident.get("team"))].append(it)
            total_items = by.get(("KXMLBTOTAL", None), [])
            f5tot_items = by.get(("KXMLBF5TOTAL", None), [])
            three = {s: [it for k, its in by.items() if k[0] == s for it in its] for s in ("KXMLBF5", "KXMLBF3", "KXMLBF7")}
            open_eps = {}
            t = start - 60 * args.first_before
            while t <= start:
                minutes_checked += 1
                gq = ladder_quotes(total_items, t)
                fq = ladder_quotes(f5tot_items, t)
                checks = []
                if len(gq) >= 2:
                    checks += [("LADDER_TOTAL", r) for r in CO.ladder_pairs(gq)]
                if len(fq) >= 2:
                    checks += [("LADDER_F5TOTAL", r) for r in CO.ladder_pairs(fq)]
                if gq and fq:
                    checks += [("CROSS_HORIZON", r) for r in CO.cross_horizon(gq, fq)]
                for s, its in three.items():
                    q = {}
                    for ident, series, _ in its:
                        v = series.get(t)
                        if v is not None and v[0] is not None and v[1] is not None:
                            q[ident["side"]] = (v[0], v[1])
                    r = CO.three_way(q)
                    if r:
                        checks.append(("THREE_WAY_" + s[5:], r))
                for (series_t, team), its in by.items():
                    if series_t == "KXMLBTEAMTOTAL" and gq:
                        tq = ladder_quotes(its, t)
                        if tq:
                            checks += [("TEAM_VS_GAME", r) for r in CO.team_vs_game(gq, tq)]
                    if series_t == "KXMLBSPREAD":
                        ml = by.get(("KXMLBGAME", team), [])
                        mlq = None
                        if ml:
                            v = ml[0][1].get(t)
                            if v is not None and v[0] is not None and v[1] is not None:
                                mlq = (v[0], v[1])
                        sq = ladder_quotes(its, t)
                        if mlq and sq:
                            checks += [("SPREAD_VS_ML", r) for r in CO.spread_vs_ml(mlq, sq)]
                cur_keys = set()
                for kind, r in checks:
                    c = counters[kind]
                    c["pairs"] += 1
                    if kind.startswith("THREE_WAY"):
                        if r.get("sumAsks") is not None:
                            three_sum[kind]["asks:%d" % max(min(r["sumAsks"], 120), 90)] += 1
                        if r.get("sumBids") is not None:
                            three_sum[kind]["bids:%d" % max(min(r["sumBids"], 110), 80)] += 1
                    elif r.get("preFeeSlackCents") is not None and (kind not in ("LADDER_TOTAL", "LADDER_F5TOTAL") or r["key"][1] == r["key"][0] + 1):
                        slack_all[kind][max(min(int(r["preFeeSlackCents"]), 10), -30)] += 1
                    if r.get("preFeeViolation"):
                        c["preFee"] += 1
                        slack = r.get("preFeeSlackCents")
                        if slack is not None:
                            slack_hist[kind][min(int(slack), 10)] += 1
                    if r.get("postFeeViolation"):
                        c["postFee"] += 1
                        post_fee_events.append({"date": date, "game": gkey, "kind": kind, "minuteTs": t,
                                                "minutesToStart": (start - t) / 60.0, "detail": r})
                        cur_keys.add((kind, str(r.get("key"))))
                    # COH-006 episodes: within-ladder pre-fee inversion, first minute only
                    if kind in ("LADDER_TOTAL", "LADDER_F5TOTAL") and r.get("preFeeViolation"):
                        ek = (kind, r["key"])
                        if ek not in open_eps:
                            open_eps[ek] = {"date": date, "game": gkey, "kind": kind, "key": list(r["key"]),
                                            "tsStart": t, "minutesToStart": (start - t) / 60.0,
                                            "yesLeg": r["yesLegCents"], "noLeg": r["noLegCents"],
                                            "lockedPerContract": r["lockedProfitCentsPerContract"], "closedBy30": None}
                for ek, ep in list(open_eps.items()):
                    if t - ep["tsStart"] == 1800 and ep["closedBy30"] is None:
                        ep["closedBy30"] = ek not in {(k, tuple(r["key"])) for k, r in checks if r.get("preFeeViolation") and "key" in r}
                # persistence of post-fee violations (consecutive minutes)
                pk = prev_violation_keys.get(gkey, set())
                for k in cur_keys:
                    persist["consecutive" if k in pk else "first"] += 1
                prev_violation_keys[gkey] = cur_keys
                t += 60
            # settle episodes
            series_prefix = {"LADDER_TOTAL": "KXMLBTOTAL", "LADDER_F5TOTAL": "KXMLBF5TOTAL"}
            for ek, ep in open_eps.items():
                n, m = ep["key"]
                tn = "%s-%s-%d" % (series_prefix[ep["kind"]], gkey, n)
                tm = "%s-%s-%d" % (series_prefix[ep["kind"]], gkey, m)
                on, om = outcomes.get(tn), outcomes.get(tm)
                ep["outcomeYesN"], ep["outcomeYesM"] = on, om
                if on in ("YES", "NO") and om in ("YES", "NO"):
                    yes_leg = E.settlement_order(ep["yesLeg"], on == "YES")
                    no_leg = E.settlement_order(ep["noLeg"], om == "NO")
                    if yes_leg and no_leg:
                        ep["netPL"] = round(yes_leg["netProfitLoss"] + no_leg["netProfitLoss"], 4)
                        ep["cash"] = round(yes_leg["actualCashConsumed"] + no_leg["actualCashConsumed"], 4)
                ep["physicalGameKey"] = gkey
                ep["gameDate"] = date
                episodes.append(ep)

    # COH-006 inference
    settled = [e for e in episodes if e.get("netPL") is not None]
    def roi(rows):
        c = sum(r["cash"] for r in rows)
        return (sum(r["netPL"] for r in rows) / c) if c > 0 else None
    coh6 = ST.cluster_bootstrap(settled, roi) if settled else None
    closed = [e for e in episodes if e.get("closedBy30") is not None]
    closure = (sum(1 for e in closed if e["closedBy30"]) / len(closed)) if closed else None
    stability = ST.date_half_stability(settled, roi) if settled else None

    out = {
        "generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY",
        "source": "recovered 1-minute candles (MLB-ALPHA-0002-KALSHI-RAW-V1), pregame minutes T-%d..T-0" % args.first_before,
        "dates": sorted(dates_seen), "games": len(games_seen), "minutesChecked": minutes_checked,
        "executionAssumption": "USD-scale 10-contract taker legs, fee per lib.edgelab.kalshi_fees, YES at ask, NO at 100-bid, same-minute candle closes on both legs",
        "constraints": {k: {"pairs": v["pairs"], "preFeeViolations": v["preFee"], "postFeeViolations": v["postFee"],
                            "preFeeSlackHistogramCents": dict(sorted(slack_hist[k].items()))} for k, v in counters.items()},
        "preFeeSlackAllPairsHistogramCents": {k: dict(sorted(v.items())) for k, v in slack_all.items()},
        "threeWaySumHistogram": {k: dict(sorted(v.items())) for k, v in three_sum.items()},
        "postFeeViolationEvents": post_fee_events[:500], "postFeeViolationCount": len(post_fee_events),
        "postFeePersistence": dict(persist),
        "coh006": {"episodes": len(episodes), "settledEpisodes": len(settled), "games": len({e["game"] for e in settled}),
                   "roiOnCash": coh6, "closureRateAt30min": closure, "closureEvaluated": len(closed),
                   "dateHalfStability": stability, "sampleEpisodes": episodes[:200]},
    }
    p = write_artifact("coherence_1min.json", out)
    print("wrote", p)
    print({k: (v["pairs"], v["preFee"], v["postFee"]) for k, v in counters.items()})
    print("COH-006", out["coh006"]["episodes"], out["coh006"]["settledEpisodes"], coh6)


if __name__ == "__main__":
    main()
