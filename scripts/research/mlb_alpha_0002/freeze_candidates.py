#!/usr/bin/env python3
"""MLB-ALPHA-0002: freeze the candidate list at the end of broad discovery.

Refuses to overwrite an existing freeze. Records, per candidate: stable ID,
exact deterministic rule, feature definitions and as-of semantics, code
SHA, rule hash, feature-schema hash (sha256 of the panel builder source),
data-source versions, market universe, side, entry logic, execution price,
fee handling, exclusions, economic + statistical hypothesis, minimum
sample, prospective evaluation rule and checkpoint, and status
(HISTORICALLY_SUPPORTED vs PROSPECTIVE_ONLY). No candidate is authorized
for real money by this file. RESEARCH ONLY.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
OUT = os.path.join(ART, "frozen_candidates.json")


def sha_file(rel):
    with open(os.path.join(REPO, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def sha_obj(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()


EXEC = {"order": "USD 10 taker, whole contracts, lib.edgelab.kalshi_fees taker schedule, actual-cash-consumed denominator",
        "buyYesPrice": "ask (yes_ask candle close at the decision minute)",
        "buyNoPrice": "100 - bid (yes_bid candle close); no midpoint fills ever",
        "clv": "lib.edgelab.clv_convention POSITIVE_IS_GOOD_V1, executable and fair-mid reported separately"}
CHECKPOINT = {"firstMaterial": {"independentGames": 100, "independentDates": 10},
              "sparseAlternate": {"episodes": 60, "independentGames": 40, "independentDates": 12,
                                  "reason": "the rule fires ~3 episodes/date; preregistered BEFORE any prospective outcome"},
              "neverLoweredAfterResults": True}

CANDIDATES = [
    {
        "candidateId": "MLB-ALPHA-0002-C01-F5REV",
        "title": "F5 moneyline 60-minute overreaction reversal",
        "status": "HISTORICALLY_SUPPORTED_PRICE_DISCOVERY_POST_FEE_UNPROVEN",
        "economicHypothesis": "KXMLBF5 moneylines are thin; a >=3c mid move over 60 minutes pregame overshoots and partially reverts before first pitch (liquidity-driven price pressure, not information).",
        "statisticalHypothesis": "Fair-mid move to close on the contrarian side > 0 and $10 post-fee P/L > 0, game-cluster bootstrap, on forward data.",
        "universe": "marketFamily=inning_result (KXMLBF5-*), active two-sided quote, 5 <= minutesToStart <= 240",
        "rule": {"feature": "dMid60 = mid(t) - mid(t-60min) from 1-minute candle closes (cents)",
                 "trigger": "dMid60 <= -3 -> BUY YES at ask; dMid60 >= +3 -> BUY NO at 100-bid",
                 "oneDecisionPerEpisode": "first 5-minute grid point at which the trigger holds; no re-entry while it keeps holding",
                 "exclusions": "spread > 10c; ask or complement outside [1,99]; game already started; doubleheader identity unresolved",
                 "asOf": "all inputs from candles/trades with end minute <= decision minute"},
        "development": "candidate_eval_f5_reversal.json, 29 dates / 391 games: fair-mid reversal positive with CI excluding 0 in ALL 12 predeclared variants (+0.7c to +5.8c); executable CLV +0.8c to +4.7c; $10 post-fee P/L never significant (h60_k3_DOWN +2.19 [-0.44,+4.59] p=0.089, 55 games; h60_k4_DOWN +3.33 [-0.47,+6.63], 23 games). Robust price discovery, unproven fill economics.",
        "knownRisks": ["small game count in the k=3 cells", "30-minute variants unstable across date halves", "entry near 35c/59c: fee drag ~0.5c, spread 4-6c dominates", "single-month single-regime archive"],
    },
    {
        "candidateId": "MLB-ALPHA-0002-C02-OFI",
        "title": "Taker order-flow imbalance follow-through (price discovery only)",
        "status": "HISTORICALLY_SUPPORTED_PRICE_DISCOVERY_NOT_TAKER_TRADABLE",
        "economicHypothesis": "Net taker flow over the prior 30-60 minutes predicts the direction of the next fair-mid move (+0.2-0.4c), but the move is smaller than spread+fee at taker.",
        "statisticalHypothesis": "Directional fair-mid move on the flow side > 0 on forward data; executable taker P/L expected <= 0 (registered as the null for real-money purposes).",
        "universe": "inning_result, inning_total, game_total, winning_margin; active two-sided quote; 5 <= minutesToStart <= 240",
        "rule": {"feature": "ofi30 = (taker YES qty - taker NO qty)/total over the prior 30 min from the public trade tape",
                 "trigger": "ofi30 > +0.2 -> flow side YES; ofi30 < -0.2 -> flow side NO",
                 "asOf": "trades with created_time minute <= decision minute"},
        "development": "family_c_results.json, 29 dates / 391 games / 105 cells: 43 BH-FDR survivors on fair-mid direction, 47 on executable CLV, 21 on post-fee P/L -- every one NEGATIVE; zero cells with post-fee P/L > 0 at 95%.",
        "purpose": "shadow-only price-discovery tracking; a maker-execution variant is a separate future hypothesis, not registered here",
    },
    {
        "candidateId": "MLB-ALPHA-0002-D01-SHARPLAG",
        "title": "Kalshi lags Pinnacle on moneylines by minutes",
        "status": "PROSPECTIVE_ONLY",
        "economicHypothesis": "When Pinnacle moves >= 2pp within 15 minutes and Kalshi has not yet moved, Kalshi subsequently moves toward Pinnacle.",
        "rule": {"feature": "dPinnacle15 (vig-free h2h, book last_update) and Kalshi mid change over the same window",
                 "trigger": "|dPinnacle15| >= 0.02 and |dKalshi15| < 0.01 -> BUY Pinnacle's side at executable price",
                 "asOf": "Pinnacle value known at OUR capture time (prospective_capture.py odds_<date>.jsonl), never at last_update"},
        "development": "pilot 2 dates / 20 games / 1,250 snapshot rows: mean |Kalshi-Pinnacle| 0.50pp; lead/lag corr(Pinnacle past 15m, Kalshi next 15m) = 0.05 -- no measurable lag at 15-min resolution; requires credit-gated 5-min history or forward capture",
    },
    {
        "candidateId": "MLB-ALPHA-0002-I01-LINEUP",
        "title": "Lineup / starter confirmation repricing lag",
        "status": "PROSPECTIVE_ONLY",
        "economicHypothesis": "Kalshi ML/F5 reprice with a measurable lag after a lineup or probable-pitcher change first becomes visible.",
        "rule": {"feature": "first capture at which mlb_state_<date>.jsonl shows lineup posted / probable pitcher changed (event time = our capture time)",
                 "trigger": "measure Kalshi mid at event-0/+5/+10/+15/+30; trade only if a preregistered lag > 5 minutes with >= 2c move is observed on >= 40 events",
                 "asOf": "capture time"},
        "development": "no historical confirmation timestamps; slate-bounded study (163 rows, 82 games, median window 181 min): event-window |move| 0.51c vs control 0.62c -- no measurable effect at that resolution",
    },
    {
        "candidateId": "MLB-ALPHA-0002-C03-BOOKIMB",
        "title": "Order-book size imbalance predicts next move / fillability",
        "status": "PROSPECTIVE_ONLY",
        "economicHypothesis": "Top-of-book and depth imbalance (public orderbook) predicts the next mid move and bounds executable size.",
        "rule": {"feature": "orderbook_<date>.jsonl: sum of YES-side vs NO-side resting quantity within 5c of the touch",
                 "trigger": "imbalance ratio >= 3:1 -> side of the heavier book (registered both ways)", "asOf": "capture time"},
        "development": "no order-book history exists anywhere",
    },
]


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s exists (candidates are frozen)" % OUT)
        return 1
    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip()
    feature_schema_sha = sha_file("scripts/research/mlb_alpha_0002/build_candle_panel.py")
    for c in CANDIDATES:
        c["ruleSha256"] = sha_obj(c["rule"])
        c["execution"] = EXEC
        c["prospectiveCheckpoint"] = CHECKPOINT
        c["realMoney"] = False
    doc = {"programId": "MLB-ALPHA-0002", "frozenAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "codeSha": code_sha, "featureSchemaSha256": feature_schema_sha,
           "dataSourceVersions": {"kalshiCandles": "trade-api/v2 candlesticks period_interval=1 (recovered 2026-09-02)",
                                  "kalshiTrades": "trade-api/v2 markets/trades (recovered 2026-09-02)",
                                  "settlement": "data/edgelab/settlements + research >=N ladder correction; F5 spreads excluded",
                                  "pinnacle": "the-odds-api /historical bookmakers=pinnacle (pilot 2026-08-19/20)",
                                  "fees": "KALSHI_TAKER_STANDARD_2026_WEBSEARCH_CORROBORATED_V1"},
           "maxCandidatesAllowed": 10, "candidateCount": len(CANDIDATES), "candidates": CANDIDATES,
           "evaluationProtocol": "prospective_capture.py append-only rows; entries recorded at capture time with no results; scored only after settlement; no rule change after results begin",
           "realMoneyFirewall": "nothing here may place, recommend, stake, or alter any production path; CEO approval required after prospective evidence"}
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True); fh.write("\n")
    print("frozen %d candidates -> %s" % (len(CANDIDATES), OUT))
    for c in CANDIDATES:
        print(" ", c["candidateId"], c["status"], c["ruleSha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
