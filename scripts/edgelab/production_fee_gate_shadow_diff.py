#!/usr/bin/env python3
"""
scripts/edgelab/production_fee_gate_shadow_diff.py
========================================================
Production Fee-Aware Net EV Integration milestone: a DESCRIPTIVE /
IN-SAMPLE behavior audit (spec sections 24-26) comparing the OLD
(fee-blind) production decision logic against the NEW (fee-aware)
production decision logic, run against the causal, no-look-ahead
historical opportunity corpus (lib.edgelab.research_dataset).

READ-ONLY: writes only to data/edgelab/analytics/ and
data/edgelab/reports/ -- never touches data/slate.json, bets.json, or
any live pipeline artifact. This script does not activate anything; it
only measures what WOULD have changed.

METHODOLOGY, stated plainly: scripts/build_market_ledger.py's
evaluate_game() takes a full live game dict (pitcher stats, lineups,
bullpen, park factors, ...) that the causal research corpus does not
preserve in that shape -- reconstructing one from research_dataset rows
is not possible without fabricating inputs. Instead, this script calls
the SAME production functions (build_edge_fields, confidence_from_edge,
bet_up_to_price_cents, fee_aware_bet_up_to_price_cents) directly against
each causal opportunity's own (modelFairProbability, executable price,
mid-derived VF) triple -- i.e. it reuses production's real qualification
math verbatim, just fed from the research corpus's causal snapshot
instead of a live slate.json row. This is NOT a re-derivation of a
different formula; it is the identical code path PR calling into
scripts/build_market_ledger.py's build_edge_fields()/confidence_from_edge()/
bet_up_to_price_cents()/fee_aware_bet_up_to_price_cents(), so "old
qualifies" and "new qualifies" below mean exactly what they would mean
on a live slate.json row with the same inputs.

`kalshi_vf` (the mid-derived reference price) is approximated as
(yesBid + yesAsk) / 2 from the observation the opportunity was drawn
from -- the same structural role production's own kalshi_vf argument
plays (a vig-free/mid reference distinct from the executable ask).

Does NOT retune thresholds (THRESHOLD_PAPER/MEDIUM/HIGH, CAL_MEDIUM are
imported unchanged from scripts/build_market_ledger.py) -- spec section
11 explicitly forbids threshold mining from this corpus.
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))

from lib.edgelab import ids, storage
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_reports import _edge_side_opportunities, _settled_rows
from lib.edgelab.research_splits import chronological_split
import build_market_ledger as bml

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")
SCHEMA_VERSION = "1"

_PRICE_BUCKET_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
_TIER_RANK = {None: 0, "PAPER": 1, "MEDIUM": 2, "HIGH": 3}


def _discover_dates():
    paths = glob.glob(storage.partition_path("observations", "*", compressed=True)) + glob.glob(storage.partition_path("observations", "*", compressed=False))
    return sorted({os.path.basename(p).split(".")[0] for p in paths})


def _load_universe(dates):
    observations, settlements, evaluations, recommendations, games = [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=True)))
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=False)))
        settlements.extend(storage.read_records(storage.partition_path("settlements", date)))
        evaluations.extend(storage.read_records(storage.partition_path("model_evaluations", date)))
        recommendations.extend(storage.read_records(storage.partition_path("recommendations", date)))
        games.extend(storage.read_records(storage.partition_path("games", date)))
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    return observations, settlements, evaluations, recommendations, games, bets


def _price_bucket(price):
    if price is None:
        return None
    for i in range(len(_PRICE_BUCKET_EDGES) - 1):
        lo, hi = _PRICE_BUCKET_EDGES[i], _PRICE_BUCKET_EDGES[i + 1]
        if lo <= price < hi or (i == len(_PRICE_BUCKET_EDGES) - 2 and price == hi):
            return f"{int(lo*100)}-{int(hi*100)}c"
    return None


def _edge_bucket(raw_edge_pct_value):
    """Mirrors research_reports.edge_backtest's own bucket boundaries (percentage points)."""
    if raw_edge_pct_value is None:
        return None
    e = raw_edge_pct_value
    if e < 0:
        return "<0"
    if e < 2:
        return "0-2"
    if e < 4:
        return "2-4"
    if e < 6:
        return "4-6"
    if e < 8:
        return "6-8"
    if e < 10:
        return "8-10"
    return "10+"


def _mid_vf(obs_yes_bid, obs_yes_ask):
    if obs_yes_bid is None or obs_yes_ask is None:
        return None
    return round((obs_yes_bid + obs_yes_ask) / 2.0, 6)


def classify_opportunity(opp):
    """
    Pure. Runs the SAME production functions (see module docstring) on
    one causal opportunity. Returns a dict with old/new
    qualification+tier+bet-up-to, and a classification label:
    RETAINED / REJECTED_BY_FEES / TIER_DOWNGRADED / UNCHANGED_UNQUALIFIED
    / UNCHANGED_QUALIFIED.
    """
    model_p = opp.get("opportunityModelProbability")
    exec_price = opp.get("opportunityPrice")
    kalshi_vf = _mid_vf(opp.get("yesBid"), opp.get("yesAsk"))
    if model_p is None or exec_price is None or not (0 < exec_price < 1):
        return None
    if kalshi_vf is None or not (0 < kalshi_vf < 1):
        kalshi_vf = exec_price  # fall back to the executable price itself, never fabricate a mid

    series_ticker = None  # canonicalMarketFamily != a Kalshi series ticker; SERIES_FEE_METADATA lookup falls back to the documented standard rate (never silently zero -- see fee_rule_for_series).
    ef = bml.build_edge_fields(model_p, kalshi_vf, exec_price * 100.0, bml.CAL_MEDIUM, series_ticker=series_ticker)

    old_conf = bml.confidence_from_edge(ef["calibratedEdgeVsExecutable"])
    new_conf = bml.confidence_from_edge(ef["netExecutableEdge"])

    gross_ceiling = bml.bet_up_to_price_cents(model_p, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
    net_ceiling = bml.fee_aware_bet_up_to_price_cents(model_p, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)

    old_qualifies = old_conf is not None
    new_qualifies = new_conf is not None
    if old_qualifies and not new_qualifies:
        classification = "REJECTED_BY_FEES"
    elif old_qualifies and new_qualifies and _TIER_RANK[new_conf] < _TIER_RANK[old_conf]:
        classification = "TIER_DOWNGRADED"
    elif old_qualifies and new_qualifies:
        classification = "RETAINED"
    else:
        classification = "UNCHANGED_UNQUALIFIED"

    bet_up_to_reduction = None
    if gross_ceiling is not None and net_ceiling is not None:
        bet_up_to_reduction = round(gross_ceiling - net_ceiling, 4)

    return {
        "oldConfidenceTier": old_conf,
        "newConfidenceTier": new_conf,
        "oldQualifies": old_qualifies,
        "newQualifies": new_qualifies,
        "classification": classification,
        "grossEdge": ef["calibratedEdgeVsExecutable"],
        "netEdge": ef["netExecutableEdge"],
        "expectedFeeDrag": ef["expectedFeeDrag"],
        "betUpToPriceGross": gross_ceiling,
        "betUpToPriceNet": net_ceiling,
        "betUpToReduction": bet_up_to_reduction,
        "priceBucket": _price_bucket(exec_price),
        "edgeBucket": _edge_bucket(ef["rawEdgeVsExecutable"]),
        "side": opp.get("opportunitySide"),
        "marketFamily": opp.get("canonicalMarketFamily"),
        "checkpoint": opp.get("researchCheckpoint") or opp.get("checkpoint"),
        "gameId": opp.get("gameId"),
        "gameDate": opp.get("gameDate"),
        "opportunityReturn": opp.get("opportunityReturn"),
        "opportunityReturnFeeOnly": opp.get("opportunityReturnFeeOnly"),
        "opportunityReturnRealisticExecution": opp.get("opportunityReturnRealisticExecution"),
        "opportunityWin": opp.get("opportunityWin"),
    }


def _roi_stats(items):
    gross = [i["opportunityReturn"] for i in items if i["opportunityReturn"] is not None]
    fee_only = [i["opportunityReturnFeeOnly"] for i in items if i.get("opportunityReturnFeeOnly") is not None]
    realistic = [i["opportunityReturnRealisticExecution"] for i in items if i.get("opportunityReturnRealisticExecution") is not None]
    games = {i["gameId"] for i in items if i.get("gameId")}
    yes_n = sum(1 for i in items if i["side"] == "YES")
    no_n = sum(1 for i in items if i["side"] == "NO")
    return {
        "n": len(items),
        "independentGames": len(games),
        "grossROI": round(sum(gross) / len(gross), 4) if gross else None,
        "feeOnlyROI": round(sum(fee_only) / len(fee_only), 4) if fee_only else None,
        "realisticExecutionROI": round(sum(realistic) / len(realistic), 4) if realistic else None,
        "yesCount": yes_n,
        "noCount": no_n,
    }


def build_validation_report(rows, dates):
    settled = _settled_rows(rows)
    opportunities = _edge_side_opportunities(settled)
    classified = []
    for opp in opportunities:
        c = classify_opportunity(opp)
        if c is not None:
            classified.append(c)

    old_qualifiers = [c for c in classified if c["oldQualifies"]]
    new_qualifiers = [c for c in classified if c["newQualifies"]]
    retained = [c for c in classified if c["classification"] == "RETAINED"]
    rejected = [c for c in classified if c["classification"] == "REJECTED_BY_FEES"]
    tier_downgraded = [c for c in classified if c["classification"] == "TIER_DOWNGRADED"]
    unchanged_unqualified = [c for c in classified if c["classification"] == "UNCHANGED_UNQUALIFIED"]

    bet_up_to_reductions = [c["betUpToReduction"] for c in old_qualifiers if c["betUpToReduction"] is not None]
    avg_bet_up_to_reduction = round(sum(bet_up_to_reductions) / len(bet_up_to_reductions), 4) if bet_up_to_reductions else None

    def _breakdown(key):
        buckets = defaultdict(lambda: {"old": 0, "new": 0, "retained": 0, "rejectedByFees": 0, "tierDowngraded": 0})
        for c in classified:
            k = c.get(key)
            b = buckets[str(k)]
            if c["oldQualifies"]:
                b["old"] += 1
            if c["newQualifies"]:
                b["new"] += 1
            if c["classification"] == "RETAINED":
                b["retained"] += 1
            elif c["classification"] == "REJECTED_BY_FEES":
                b["rejectedByFees"] += 1
            elif c["classification"] == "TIER_DOWNGRADED":
                b["tierDowngraded"] += 1
        return dict(buckets)

    split = chronological_split(dates)

    report = {
        "causalOpportunitiesAudited": len(classified),
        "oldQualifierCount": len(old_qualifiers),
        "newQualifierCount": len(new_qualifiers),
        "retainedCount": len(retained),
        "rejectedByFeesCount": len(rejected),
        "tierDowngradedCount": len(tier_downgraded),
        "unchangedUnqualifiedCount": len(unchanged_unqualified),
        "sideChangedCount": 0,
        "sideChangedNote": (
            "Production has no mechanism to flip a candidate from one side "
            "to the other within a single evaluation -- each side "
            "(YES/NO, e.g. NRFI vs YRFI) is an independently priced "
            "contract audited on its own. This category is structurally "
            "always 0, not a gap in the audit."
        ),
        "averageBetUpToReductionCents": avg_bet_up_to_reduction,
        "breakdownByMarketFamily": _breakdown("marketFamily"),
        "breakdownBySide": _breakdown("side"),
        "breakdownByPriceBucket": _breakdown("priceBucket"),
        "breakdownByEdgeBucket": _breakdown("edgeBucket"),
        "breakdownByCheckpoint": _breakdown("checkpoint"),
        "oldQualifyingSetDescriptiveOutcomes": _roi_stats(old_qualifiers),
        "newQualifyingSetDescriptiveOutcomes": _roi_stats(new_qualifiers),
        "chronologicalSplit": {
            "totalDates": split["totalDates"],
            "maturity": split["maturity"],
            "ratiosUsed": split["ratiosUsed"],
        },
        "label": "DESCRIPTIVE / IN-SAMPLE BEHAVIOR AUDIT -- not proof the new gate is superior; see docs/PRODUCTION_FEE_AWARE_NET_EV.md",
        "methodologyNote": (
            "Every old/new qualification decision above is computed by "
            "calling scripts/build_market_ledger.py's real "
            "build_edge_fields()/confidence_from_edge()/"
            "bet_up_to_price_cents()/fee_aware_bet_up_to_price_cents() "
            "functions directly against each causal opportunity's own "
            "(modelFairProbability, executable price, mid-derived VF) "
            "triple -- not a re-derived approximation. THRESHOLD_PAPER/"
            "MEDIUM/HIGH and CAL_MEDIUM are unchanged, imported constants; "
            "no threshold mining was performed against this corpus."
        ),
    }
    return report, classified


def render_summary_markdown(report):
    sp = report["chronologicalSplit"]
    lines = [
        "# Production Fee-Aware Net EV -- Shadow Behavior Diff",
        "",
        "**DESCRIPTIVE / IN-SAMPLE BEHAVIOR AUDIT** -- this compares what the OLD "
        "(fee-blind) and NEW (fee-aware) production decision logic would have "
        "done on the causal historical corpus. It is not proof the new gate "
        "is superior out of sample.",
        "",
        f"- Causal opportunities audited: **{report['causalOpportunitiesAudited']}**",
        f"- Old qualifiers: **{report['oldQualifierCount']}**",
        f"- New qualifiers: **{report['newQualifierCount']}**",
        f"- Retained: **{report['retainedCount']}**",
        f"- Rejected by fees: **{report['rejectedByFeesCount']}**",
        f"- Tier downgraded (still qualifies, lower tier): **{report['tierDowngradedCount']}**",
        f"- Unchanged (never qualified either way): **{report['unchangedUnqualifiedCount']}**",
        f"- Average Bet Up To reduction: **{report['averageBetUpToReductionCents']} cents**",
        "",
        f"Chronological split maturity: **{sp['maturity']}** ({sp['totalDates']} distinct dates).",
    ]
    if sp["maturity"] != "USABLE":
        lines.append(
            "The strategy-validation dataset is still immature for a real "
            "DEV/VALIDATION/HOLDOUT split (fewer than "
            "lib.edgelab.research_splits.MIN_DATES_FOR_MATURE_SPLIT dates) -- "
            "the split is computed and labeled honestly above, not skipped, "
            "but should not be treated as a real out-of-sample validation yet."
        )
    lines.append("")
    old_o = report["oldQualifyingSetDescriptiveOutcomes"]
    new_o = report["newQualifyingSetDescriptiveOutcomes"]
    lines.append("## Old vs. new qualifying-set descriptive outcomes")
    lines.append("")
    lines.append("| Metric | Old qualifying set | New qualifying set |")
    lines.append("|---|---:|---:|")
    lines.append(f"| n | {old_o['n']} | {new_o['n']} |")
    lines.append(f"| Independent games | {old_o['independentGames']} | {new_o['independentGames']} |")
    lines.append(f"| Gross ROI | {old_o['grossROI']} | {new_o['grossROI']} |")
    lines.append(f"| Fee-only ROI | {old_o['feeOnlyROI']} | {new_o['feeOnlyROI']} |")
    lines.append(f"| Realistic-execution ROI | {old_o['realisticExecutionROI']} | {new_o['realisticExecutionROI']} |")
    lines.append(f"| YES / NO split | {old_o['yesCount']}/{old_o['noCount']} | {new_o['yesCount']}/{new_o['noCount']} |")
    return "\n".join(lines) + "\n"


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    dates = _discover_dates()
    if args.start_date:
        dates = [d for d in dates if d >= args.start_date]
    if args.end_date:
        dates = [d for d in dates if d <= args.end_date]
    if not dates:
        print("No observation dates found -- nothing to do.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets = _load_universe(dates)
    rows = build_opportunity_rows(
        observations, settlements=settlements, evaluations=evaluations,
        recommendations=recommendations, bets=bets, games=games,
    )

    report, classified = build_validation_report(rows, dates)
    generated_at = ids.utc_now_iso()
    payload = {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at,
        "datesAudited": dates, "report": report,
    }
    _write_json(os.path.join(ANALYTICS_DIR, "latest_production_fee_gate_validation.json"), payload)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(os.path.join(REPORTS_DIR, "production_fee_gate_shadow_diff.md"), "w") as f:
        f.write(render_summary_markdown(report))

    print(f"Causal opportunities audited: {report['causalOpportunitiesAudited']}")
    print(f"Old qualifiers: {report['oldQualifierCount']}  New qualifiers: {report['newQualifierCount']}")
    print(f"Retained: {report['retainedCount']}  Rejected by fees: {report['rejectedByFeesCount']}  Tier downgraded: {report['tierDowngradedCount']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
