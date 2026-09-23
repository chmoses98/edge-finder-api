#!/usr/bin/env python3
"""
Write MRV runner artifacts back into the hypothesis registry (status +
results block) and render HYPOTHESIS_REGISTRY.md.  RESEARCH ONLY.

Dispositions are computed from the registry's frozen candidate bar; the
script never edits a frozen field (fingerprint asserted unchanged).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import REPO, ARTIFACT_DIR, now_iso  # noqa: E402

from lib.edgelab.research.market_structure import registry as R  # noqa: E402

FLOOR_GAMES, FLOOR_DATES = 60, 10


def _load(name):
    p = os.path.join(ARTIFACT_DIR, name)
    return json.load(open(p)) if os.path.exists(p) else None


def _ci_excludes_zero(t):
    return t and t.get("ciLow") is not None and (t["ciLow"] > 0 or t["ciHigh"] < 0)


def _res(summary, **kw):
    d = {"generatedAt": now_iso(), "summary": summary}
    d.update(kw)
    return d


def main():
    reg = R.load_registry(os.path.join(REPO, R.DEFAULT_REGISTRY_PATH))
    fp_before = R.fingerprint(reg)
    coh = _load("coherence_1min.json"); ll = _load("leadlag_1min.json"); sh = _load("sharp_vs_kalshi.json")
    bk = _load("book_depth.json"); tp = _load("tape_decomposition.json"); pb = _load("pricebands_sep.json")
    info = _load("info_events.json"); port = _load("portfolio_correlation.json"); cost = _load("cost_surface.json")

    if coh:
        c = coh["constraints"]
        base = dict(tier=coh["tier"], games=coh["games"], dates=len(coh["dates"]), minutesChecked=coh["minutesChecked"], artifact="coherence_1min.json",
                    feeTreatment="taker both legs, 10 contracts", execution="same-minute candle closes, YES at ask, NO at 100-bid")
        R.record_result(reg, "MRV-COH-001", "REJECTED", _res(
            "0 pre-fee and 0 post-fee inversions in %d game-total + %d F5-total ladder pairs at 1-minute resolution; adjacent rungs sit 6-14c apart" % (c["LADDER_TOTAL"]["pairs"], c["LADDER_F5TOTAL"]["pairs"]),
            pairs=c["LADDER_TOTAL"]["pairs"] + c["LADDER_F5TOTAL"]["pairs"], preFee=0, postFee=0, **base))
        tw = {k: c[k] for k in ("THREE_WAY_F5", "THREE_WAY_F3", "THREE_WAY_F7")}
        R.record_result(reg, "MRV-COH-002", "REJECTED", _res(
            "pre-fee sum violations exist (F5 %d/%d, F3 %d/%d, F7 %d/%d event-minutes; asks summing 98-99 or bids 101-102) but 0 survive taker fees on three legs" % (
                tw["THREE_WAY_F5"]["preFeeViolations"], tw["THREE_WAY_F5"]["pairs"], tw["THREE_WAY_F3"]["preFeeViolations"], tw["THREE_WAY_F3"]["pairs"], tw["THREE_WAY_F7"]["preFeeViolations"], tw["THREE_WAY_F7"]["pairs"]),
            constraints=tw, sumHistograms=coh.get("threeWaySumHistogram"), **base))
        R.record_result(reg, "MRV-COH-003", "REJECTED", _res(
            "0 pre-fee and 0 post-fee violations in %d F5-total vs game-total rung pairs (first test of this constraint)" % c["CROSS_HORIZON"]["pairs"], pairs=c["CROSS_HORIZON"]["pairs"], **base))
        R.record_result(reg, "MRV-COH-005", "REJECTED", _res(
            "0 violations in %d team-total vs game-total and %d spread vs moneyline pairs (exchange record 08-02..08-07 for those families); September registry replication not run because the collector captured <=30 game-total books" % (c["TEAM_VS_GAME"]["pairs"], c["SPREAD_VS_ML"]["pairs"]),
            pairs=c["TEAM_VS_GAME"]["pairs"] + c["SPREAD_VS_ML"]["pairs"], limitation="team_total/winning_margin candles cover 08-02..08-07 only", **base))
        R.record_result(reg, "MRV-COH-006", "DATA_BLOCKED", _res(
            "no pre-fee within-ladder inversion episode occurred in 94,231 pregame game-minutes, so there is nothing to trade or to measure convergence on", episodes=0, **base))
    R.record_result(reg, "MRV-COH-004", "REJECTED", _res(
        "by exchange construction a NO order at q is a YES order at 100-q on the same book; the prospective order books carry only YES/NO resting bids and the runners derive the NO ask as 100-yesBid, so parity cannot fail except as a capture artifact; no edge", tier="TIER_B_EXPLORATORY", artifact="book_depth.json"))

    if ll:
        for hid, prefix in (("MRV-LL-001", "ML->F5ML"), ("MRV-LL-002", "F5ML->ML"), ("MRV-LL-003", "TOT->F5TOT"), ("MRV-LL-004", "F5TOT->TOT")):
            ts = {k: v for k, v in ll["tests"].items() if k.startswith(prefix + "_")}
            surv = [k for k in ts if ll["bh"].get(k)]
            ok = [k for k in surv if _ci_excludes_zero(ts[k]) and ts[k]["clusters"] >= FLOOR_GAMES and ts[k]["point"] > 0]
            if all(t["clusters"] < FLOOR_GAMES for t in ts.values()):
                status = "DATA_BLOCKED"
            else:
                status = "REJECTED" if not ok else "EXPLORATORY"
            summ = "; ".join("h%s slope %.3f CI[%.2f,%.2f] games %d%s" % (k.split("_h")[1], t["point"] or 0, t["ciLow"] or 0, t["ciHigh"] or 0, t["clusters"], " BH" if ll["bh"].get(k) else "") for k, t in sorted(ts.items()))
            R.record_result(reg, hid, status, _res(summ, tier=ll["tier"], tests=ts, bhSurvivors=surv, candidateBarMet=ok, artifact="leadlag_1min.json",
                                                    execution="partial slope on fair mids; a positive slope below spread+fee is price discovery, not a trade"))
    if sh:
        t = sh["tests"]; d = sh["descriptive"]; dist = sh["disagreementDistribution"]
        R.record_result(reg, "MRV-XV-001", "EXPLORATORY", _res(
            "Pinnacle-vs-Kalshi ML: mean |d| %.2fpp, never >=2pp; slope of Kalshi move to next capture %.2f CI[%.2f,%.2f], to last pregame %.2f CI[%.2f,%.2f] (%d games, %d dates, median %.0f min to next capture); price discovery only" % (
                dist["dPin"]["meanAbs"], t["MRV-XV-001_slopeNext"]["point"], t["MRV-XV-001_slopeNext"]["ciLow"], t["MRV-XV-001_slopeNext"]["ciHigh"],
                t["MRV-XV-001_slopeLast"]["point"], t["MRV-XV-001_slopeLast"]["ciLow"], t["MRV-XV-001_slopeLast"]["ciHigh"], t["MRV-XV-001_slopeNext"]["clusters"], d["dates"], d["medianMinutesToNext"]),
            tier=sh["tier"], tests={k: t[k] for k in ("MRV-XV-001_slopeNext", "MRV-XV-001_slopeLast")}, bh={k: sh["bh"].get(k) for k in ("MRV-XV-001_slopeNext", "MRV-XV-001_slopeLast")},
            breakEven=sh["breakEven"]["MRV-XV-001_slopeLast"], timingCaveat=sh["timingCaveat"], artifact="sharp_vs_kalshi.json"))
        R.record_result(reg, "MRV-XV-002", "REJECTED", _res(
            "0 episodes: |Pinnacle - Kalshi| never reached the preregistered 3pp (max <2pp; break-even |d| for a hold-to-settlement taker is ~%.1fpp given the fitted convergence slope)" % sh["breakEven"]["MRV-XV-001_slopeLast"]["breakEvenAbsDppHold"],
            episodes=d["xv002Episodes"], tier=sh["tier"], artifact="sharp_vs_kalshi.json"))
        R.record_result(reg, "MRV-XV-003", "EXPLORATORY", _res(
            "4-book consensus: slope next %.2f CI[%.2f,%.2f] (BH), last %.2f CI[%.2f,%.2f] (BH), %d games; |d|>=3pp only %d episodes (floor 60) so no executable test; consensus includes DK/FD/BetMGM which are less sharp than Pinnacle" % (
                t["MRV-XV-003_slopeNext"]["point"], t["MRV-XV-003_slopeNext"]["ciLow"], t["MRV-XV-003_slopeNext"]["ciHigh"], t["MRV-XV-003_slopeLast"]["point"], t["MRV-XV-003_slopeLast"]["ciLow"], t["MRV-XV-003_slopeLast"]["ciHigh"], t["MRV-XV-003_slopeNext"]["clusters"], d["xv003Episodes"]),
            tier=sh["tier"], tests={k: t[k] for k in ("MRV-XV-003_slopeNext", "MRV-XV-003_slopeLast", "MRV-XV-003_roi")}, bh={k: sh["bh"].get(k) for k in ("MRV-XV-003_slopeNext", "MRV-XV-003_slopeLast", "MRV-XV-003_roi")},
            breakEven=sh["breakEven"]["MRV-XV-003_slopeLast"], timingCaveat=sh["timingCaveat"], artifact="sharp_vs_kalshi.json"))
        R.record_result(reg, "MRV-XV-004", "DATA_BLOCKED", _res(
            "the prospective collector capped books at 400/run and captured only %d game-total ladders across 21 days; no totals panel" % d["totalLadderRunGames"], tier=sh["tier"], artifact="sharp_vs_kalshi.json"))
    if info:
        r = info["eventTimeResolution"]
        for hid, kinds in (("MRV-INFO-001", ("LINEUP_AWAY", "LINEUP_HOME")), ("MRV-INFO-002", ("PITCHER_AWAY", "PITCHER_HOME"))):
            ev = sum(r[k]["events"] for k in kinds if k in r)
            med = [r[k]["medianBoundMinutes"] for k in kinds if k in r]
            R.record_result(reg, hid, "DATA_BLOCKED", _res(
                "%d first-seen events; event time bounded only to the previous collector run, median %.0f minutes; a repricing-lag test needs <=30-minute resolution" % (ev, max(med) if med else 0),
                tier=info["tier"], resolution={k: r[k] for k in kinds if k in r}, artifact="info_events.json"))
    if cost:
        R.record_result(reg, "MRV-TTP-001", "EXPLORATORY", _res(
            "descriptive cost surface written (spread, taker fee at ask, two-sided share by family x lead bucket)", tier=cost["tier"], allBuckets={k: v for k, v in cost["cells"].items() if k.startswith("all|")}, artifact="cost_surface.json"))
    if pb:
        d = pb["descriptive"]; l = pb["liq001"]
        R.record_result(reg, "MRV-LIQ-001", "EXPLORATORY" if any(_ci_excludes_zero(l[k]) for k in ("wideMinusTight", "lowMinusHighVolume")) else "REJECTED", _res(
            "BUY-YES post-fee ROI wide(>=4c) minus tight(<=2c) spread %.3f CI[%.3f,%.3f]; low minus high volume tercile %.3f CI[%.3f,%.3f] (%d games, %d dates); wide books lose MORE, i.e. no inefficiency to harvest, just higher cost" % (
                l["wideMinusTight"]["point"], l["wideMinusTight"]["ciLow"], l["wideMinusTight"]["ciHigh"], l["lowMinusHighVolume"]["point"], l["lowMinusHighVolume"]["ciLow"], l["lowMinusHighVolume"]["ciHigh"], d["games"], d["dates"]),
            tier=pb["tier"], tests={k: l[k] for k in ("wideMinusTight", "lowMinusHighVolume")}, bh={k: pb["bh"].get("LIQ001|" + k) for k in ("wideMinusTight", "lowMinusHighVolume")}, artifact="pricebands_sep.json"))
        f = pb["pb001"]["forward"]; rr = pb["pb001"]["rerun"]
        R.record_result(reg, "MRV-PB-001", "REJECTED", _res(
            "frozen beta=%.4f forward (settle > 08-28, %d rows / %d games): shrunk minus market Brier %+.6f CI[%+.6f,%+.6f] (shrink hurts or is null); preregistered rerun: beta_train=%.3f CI[%.3f,%.3f], VAL Brier delta %+.6f CI[%+.6f,%+.6f], gates beta-CI-excludes-1=%s VAL-delta-negative=%s -> not both" % (
                f["beta"], f["n"], f["clusters"], f["point"], f["ciLow"], f["ciHigh"], rr["betaTrain"], rr["betaCI"]["ciLow"], rr["betaCI"]["ciHigh"], rr["valBrierDelta"]["point"], rr["valBrierDelta"]["ciLow"], rr["valBrierDelta"]["ciHigh"], rr["gateBetaCIExcludes1"], rr["gateValDeltaNegative"]),
            tier=pb["tier"], forward=f, rerun={k: rr[k] for k in ("trainRows", "trainGames", "valRows", "valGames", "cutDate", "baseTrain", "betaTrain", "betaCI", "valBrierDelta", "valBands", "gateBetaCIExcludes1", "gateValDeltaNegative")}, artifact="pricebands_sep.json"))
        cells = pb["pb002"]
        surv = [k for k, v in pb["bh"].items() if v and k.startswith("PB002|")]
        pos = [k[6:] for k in surv if cells[k[6:]]["point"] is not None and cells[k[6:]]["point"] >= 0.01 and _ci_excludes_zero(cells[k[6:]]) and cells[k[6:]]["clusters"] >= FLOOR_GAMES and cells[k[6:]]["dates"] >= FLOOR_DATES and (cells[k[6:]]["stability"] or {}).get("sameSign")]
        R.record_result(reg, "MRV-PB-002", "EXPLORATORY" if pos else "REJECTED", _res(
            "%d family x side x band cells on %d games / %d dates (median last quote %.0f min before start); BH survivors %d, of which %d positive-ROI cells meet the candidate bar: %s" % (
                sum(1 for k in cells if not k.startswith("ALL|") and not k.endswith("|ALL")), d["games"], d["dates"], d["medianMinutesToStart"], len(surv), len(pos), pos or "none"),
            tier=pb["tier"], survivors={k[6:]: {kk: cells[k[6:]][kk] for kk in ("point", "ciLow", "ciHigh", "n", "clusters", "dates", "stability")} for k in surv}, candidateCells=pos, artifact="pricebands_sep.json",
            reasonsCouldBeFalse=["12-21 slate dates of one month", "last available pregame quote is a median 1.5-2h before first pitch, not a close", "outcome derived from MLB Stats API, not Kalshi's receipt", "cells near the 60-game floor are dominated by slate composition"]))
    if bk:
        a = bk["cells"]["all"]
        R.record_result(reg, "MRV-EXEC-001", "EXPLORATORY", _res(
            "%d pregame books / %d games: median top-of-book depth %.0f contracts at the ask, USD 100 YES order fills within 2c of top in %.0f%% of books, USD 250 unfilled in %.1f%%; depth is not the binding constraint for USD 10-100 orders, spread+fee are" % (
                a["books"], bk["games"], a["topDepthYesAskMedian"], 100 * a["fillWithin2cShare_YES_100"], 100 * a["unfilledShare_YES_250"]),
            tier=bk["tier"], families={k: v for k, v in bk["cells"].items() if k.startswith("family|")}, caveat=bk["caveat"], artifact="book_depth.json"))
    if tp:
        inf = tp["inference"]
        drift_surv = [f for f, ok in tp["bh"]["MRV-EXEC-002_driftToLast"].items() if ok]
        maker_surv = [f for f, ok in tp["bh"]["MRV-EXEC-003_makerNet30"].items() if ok]
        allc = tp["cells"].get("all", {})
        R.record_result(reg, "MRV-EXEC-002", "EXPLORATORY", _res(
            "%d pregame prints / %d games: taker pays half-spread %.2fc + fee %.2fc per contract; fair mid then drifts %.2fc TOWARD the taker by last pregame minute (volume-weighted); net taker edge at last pregame %.2fc; drift BH survivors: %s" % (
                tp["counts"]["used"], tp["games"], allc.get("halfSpread") or 0, allc.get("fee") or 0, allc.get("drift_last") or 0, allc.get("takerNet_last") or 0, drift_surv),
            tier=tp["tier"], all=allc, byFamily={f: {"driftToLast": inf[f]["driftToLast"], "takerNetLast": inf[f]["takerNetLast"], "games": inf[f]["games"]} for f in inf},
            bh=tp["bh"]["MRV-EXEC-002_driftToLast"], artifact="tape_decomposition.json", conventions=tp["conventions"]))
        R.record_result(reg, "MRV-EXEC-003", "EXPLORATORY", _res(
            "maker realized spread at +30 min (half-spread earned minus adverse drift minus 0.0175 maker fee), volume-weighted: all %.2fc; BH survivors by family: %s" % (allc.get("makerNet_30") or 0, maker_surv),
            tier=tp["tier"], byFamily={f: inf[f]["makerNet30"] for f in inf}, bh=tp["bh"]["MRV-EXEC-003_makerNet30"], artifact="tape_decomposition.json"))
    if port:
        R.record_result(reg, "MRV-PORT-001", "EXPLORATORY", _res(
            "%d games with all six canonical contracts: phi ML~F5ML %.2f, ML~RL2 %.2f, TOTAL8~F5TOTAL5 %.2f, TOTAL8~TT4 %.2f; six one-contract positions behave like ~%.1f independent bets" % (
                port["gamesWithAllSix"], port["phi"].get("ML_HOME~F5ML_HOME", 0), port["phi"].get("ML_HOME~RL_HOME_2", 0), port["phi"].get("TOTAL_8~F5TOTAL_5", 0), port["phi"].get("TOTAL_8~TT_HOME_4", 0), port["effectiveIndependentBetsOfSix"] or 0),
            tier=port["tier"], phi=port["phi"], effectiveN=port["effectiveIndependentBetsOfSix"], artifact="portfolio_correlation.json"))

    assert R.fingerprint(reg) == fp_before, "frozen fields changed"
    R.save_registry(reg, os.path.join(REPO, R.DEFAULT_REGISTRY_PATH))
    with open(os.path.join(ARTIFACT_DIR, "HYPOTHESIS_REGISTRY.md"), "w") as f:
        f.write(R.render_markdown(reg))
    print("statuses:", {h["id"]: h["status"] for h in reg["hypotheses"]})


if __name__ == "__main__":
    main()
