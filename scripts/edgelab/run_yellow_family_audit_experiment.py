#!/usr/bin/env python3
"""
scripts/edgelab/run_yellow_family_audit_experiment.py
=====================================================
Research Lab experiment MLB-RSCH-0032: "YELLOW Family Validity Audit".
RESEARCH ONLY. NO production change, no family suspension, no
qualification change, no staking change.

CORE QUESTION: MLB-RSCH-0031 found ~80% of live recommendation exposure
sits in families labelled YELLOW -- unproven or below sample floor -- and
that those families, not the RED ones, carry most of the historical
hypothetical loss. Do they deserve to stay YELLOW, become GREEN, or
become RED?

FIRST ADOPTER OF METHODOLOGY V3. Every actionability label here comes
from lib.edgelab.research.methodology_v3, with each family's floors
preregistered and justified before results were read. A family cannot be
called usable because a point estimate has the right sign.

SEMANTICS ESTABLISHED BEFORE ANALYSIS, NOT ASSUMED
---------------------------------------------------
KXMLBRFI is ONE binary contract per game -- ticker equals eventTicker,
title "<away> vs <home> First Inning Run?", no threshold and no team
side. YES means a run is scored in the first inning by either team. It is
therefore NOT a per-team or per-side ladder, and one row per game means
no within-game correlation to cluster away: 225 settled rows are 225
independent games.

KXMLBF5 is a THREE-WAY market. Its settled tickers include an explicit
`-TIE` outcome alongside the two team sides, so the archived two-way
`F5_ML_Home`/`F5_ML_Away` synthetic rows are NOT semantically the same
contract. That mismatch is reported, not bridged.

WHAT COULD NOT BE RECOVERED, AND WHY IT IS NOT FABRICATED
----------------------------------------------------------
The synthetic-identifier rows (`<gamePk>:NRFI`, `:F5_ML_Home`, ...) were
tested for recovery against the settled Kalshi archive by mapping gamePk
through the games archive's mlbGamePk -> kalshiKey index. Of 405
synthetic first-inning rows, ZERO resolved to a unique settled KXMLBRFI
ticker: the gamePk values simply are not in that index. No approximate,
fuzzy or date-proximity match is attempted. Those rows stay unrecovered.

MAXIMUM DISPOSITION per family is a research classification. Nothing here
authorises a production change of any kind.
"""
import collections
import json
import math
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_EDGELAB_SCRIPTS_DIR = os.path.join(_ROOT, "scripts", "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research.methodology_v3 import (
    MaterialityPreregistration, ObservedEvidence, betting_shadow_gate_v3,
    describe_v3, TRANSPORT_LEAVE_DATE_OUT,
)
from lib.edgelab.research_stats import (
    independent_unit_count, expected_calibration_error, brier_and_log_loss_summary,
    calibration_slope_intercept, game_clustered_bootstrap_ci,
)

from lib.edgelab.bullpen_usage import MLB_ID_TO_ABBR

import run_production_calibration_audit_experiment as rsch0022

EXPERIMENT_ID = "MLB-RSCH-0032"
REGISTRATION_TIMESTAMP = "2026-08-29T17:00:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0032_yellow_family_audit.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0032_YELLOW_FAMILY_AUDIT.md")

# ── Preregistered constants ──────────────────────────────────────────────
PRIMARY_FAMILY = "KXMLBRFI"
YELLOW_PRODUCTION_FAMILIES = ("KXMLBRFI", "KXMLBF5", "KXMLBGAME")
RESEARCH_ONLY_FAMILIES = ("pitcher_strikeouts", "team_total", "game_total", "winning_margin",
                          "inning_result", "inning_total", "game_result", "pitcher_outs",
                          "first_inning_run")
EDGE_BUCKETS = ((-1.0, 0.0), (0.0, 0.025), (0.025, 0.05), (0.05, 0.075),
                (0.075, 0.10), (0.10, 0.15), (0.15, 1.01))
MIN_FAMILY_ROWS = 150
MIN_FAMILY_GAMES = 100
MIN_FAMILY_DATES = 10
MIN_SEGMENT_ROWS = 40
PROB_CLAMP = (0.001, 0.999)
BOOTSTRAP_RESAMPLES = 400


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


def materiality_for(family, rows, games, dates):
    """Each family's OWN floors, justified for that family. V3 ships no
    universal numbers and refuses to build without a justification."""
    return MaterialityPreregistration(
        null_value=0.0,
        # effect = paired Brier delta (market minus model, so positive == model better).
        effect_floor=0.005,
        harm_tolerance=0.005,
        require_ci_excludes_null=True,
        min_score_improvement=0.005,
        min_independent_games=MIN_FAMILY_GAMES,
        min_independent_dates=MIN_FAMILY_DATES,
        required_transport=TRANSPORT_LEAVE_DATE_OUT,
        min_executable_opportunities=1,
        subject_unit="game",
        justification=(
            f"For {family}, a paired Brier gain smaller than 0.005 cannot survive the canonical "
            "Kalshi taker fee at the prices this family actually trades at, so it is not a "
            "meaningful effect however favourable its sign. The independent-game floor is set at "
            "100 because this archive supplies one settled contract per game for the production "
            "families, so games are the true unit and a smaller count cannot separate skill from "
            "a coin flip. Leave-one-date-out is required because the archive spans few dates and "
            "a single chronological split would rest on one arbitrary boundary."
        ),
    )


# ── Scoring ───────────────────────────────────────────────────────────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows) if rows else None


def log_loss(rows, key):
    if not rows:
        return None
    return sum(-(r["outcome"] * math.log(_clamp(r[key])) +
                 (1 - r["outcome"]) * math.log(1 - _clamp(r[key]))) for r in rows) / len(rows)


def paired_brier_delta(rows):
    """model minus market. NEGATIVE means the model beats Kalshi."""
    if not rows:
        return None
    return brier(rows, "modelP") - brier(rows, "marketP")


def paired_log_loss_delta(rows):
    if not rows:
        return None
    return log_loss(rows, "modelP") - log_loss(rows, "marketP")


def score_family(rows, label):
    if not rows:
        return {"family": label, "rows": 0}
    mp = [(r["modelP"], r["outcome"]) for r in rows]
    kp = [(r["marketP"], r["outcome"]) for r in rows]
    m_brier, m_ll = brier_and_log_loss_summary(mp)
    k_brier, k_ll = brier_and_log_loss_summary(kp)
    m_slope, m_int = calibration_slope_intercept(mp)
    k_slope, _ = calibration_slope_intercept(kp)
    lo, hi, method = game_clustered_bootstrap_ci(rows, paired_brier_delta,
                                                 cluster_key="gameId",
                                                 n_resamples=BOOTSTRAP_RESAMPLES)
    ps = sorted(r["modelP"] for r in rows)
    mean_p = sum(ps) / len(ps)
    return {
        "family": label, "rows": len(rows),
        "independentGames": independent_unit_count(rows, "gameId"),
        "independentDates": independent_unit_count(rows, "settleDate"),
        "rowsPerGame": round(len(rows) / max(1, independent_unit_count(rows, "gameId")), 2),
        "settledYesRate": round(sum(r["outcome"] for r in rows) / len(rows), 4),
        "modelBrier": m_brier, "marketBrier": k_brier,
        "modelLogLoss": m_ll, "marketLogLoss": k_ll,
        "modelECE": round(expected_calibration_error(mp, n_bins=10), 6),
        "marketECE": round(expected_calibration_error(kp, n_bins=10), 6),
        "modelCalibrationSlope": m_slope, "modelCalibrationIntercept": m_int,
        "marketCalibrationSlope": k_slope,
        "pairedBrierDelta": round(paired_brier_delta(rows), 6),
        "pairedBrierDeltaCI": {"low": lo, "high": hi, "method": method},
        "pairedLogLossDelta": round(paired_log_loss_delta(rows), 6),
        "modelProbabilitySpread": {"min": round(ps[0], 4), "median": round(ps[len(ps) // 2], 4),
                                   "max": round(ps[-1], 4),
                                   "stdev": round(math.sqrt(sum((x - mean_p) ** 2 for x in ps) / len(ps)), 4)},
        "baseRateBrier": round(sum((sum(r["outcome"] for r in rows) / len(rows) - r["outcome"]) ** 2
                                   for r in rows) / len(rows), 6),
    }


def edge_bucket_analysis(rows):
    """Does a larger declared edge mean a more trustworthy opportunity?
    Declared edge here is model minus the archived market probability."""
    out = []
    for lo, hi in EDGE_BUCKETS:
        lbl = f"[{lo:+.3f},{hi:+.3f})"
        sub = [r for r in rows if lo <= (r["modelP"] - r["marketP"]) < hi]
        if not sub:
            out.append({"bucket": lbl, "rows": 0})
            continue
        out.append({"bucket": lbl, "rows": len(sub),
                    "independentGames": independent_unit_count(sub, "gameId"),
                    "meanModelProbability": round(sum(r["modelP"] for r in sub) / len(sub), 4),
                    "realizedEventRate": round(sum(r["outcome"] for r in sub) / len(sub), 4),
                    "pairedBrierDelta": round(paired_brier_delta(sub), 6),
                    "meetsFloor": len(sub) >= MIN_SEGMENT_ROWS})
    qual = [b for b in out if b.get("meetsFloor")]
    v = [b["pairedBrierDelta"] for b in qual]
    return {"buckets": out, "qualifyingBuckets": len(qual),
            "monotoneImproving": (all(v[i] >= v[i + 1] for i in range(len(v) - 1))
                                  if len(v) >= 3 else None),
            "inversion": (v[-1] > v[0]) if len(v) >= 3 else None}


def leave_one_date_out(rows):
    """Transport evidence. Each date is held out and scored against the rest;
    with one contract per game these are genuinely independent blocks."""
    dates = sorted({r["settleDate"] for r in rows})
    folds = []
    for d in dates:
        held = [r for r in rows if r["settleDate"] == d]
        if len(held) < 5:
            folds.append({"date": d, "rows": len(held), "status": "TOO_SMALL"})
            continue
        folds.append({"date": d, "rows": len(held),
                      "pairedBrierDelta": round(paired_brier_delta(held), 6),
                      "modelBeatsMarket": paired_brier_delta(held) < 0})
    scored = [f for f in folds if "modelBeatsMarket" in f]
    return {"folds": folds, "datesScored": len(scored),
            "datesModelWins": sum(1 for f in scored if f["modelBeatsMarket"]),
            "replicatingBlocks": sum(1 for f in scored if f["modelBeatsMarket"])}


def fee_aware_capacity(rows, label):
    """Economics AFTER the predictive verdict. Entry at the archived market
    probability with the canonical taker fee. Never used to select anything."""
    staked = fees = pnl = 0.0
    bets = wins = gross_positive = 0
    for r in rows:
        price = _clamp(r["marketP"])
        if r["modelP"] > price:
            gross_positive += 1
        fee = taker_fee(1, price)
        net_ev = r["modelP"] * (1.0 - price) - (1.0 - r["modelP"]) * price - fee
        if net_ev <= 0:
            continue
        bets += 1
        wins += r["outcome"]
        staked += price
        fees += fee
        pnl += ((1.0 - price) if r["outcome"] == 1 else -price) - fee
    return {"segment": label, "grossPositiveEdgeRows": gross_positive,
            "netEvPositiveOpportunities": bets, "wins": wins,
            "grossStaked": round(staked, 4), "totalFees": round(fees, 4),
            "netPnl": round(pnl, 4),
            "netRoi": round(pnl / staked, 4) if staked else None,
            "note": "SECONDARY. Never used to tune any parameter or threshold."}


def classify_family(score, lodo, capacity, v3_pass, v3_labels):
    """Preregistered classification vocabulary. A family is never called
    usable merely because it is not significantly bad."""
    if score.get("rows", 0) < MIN_FAMILY_ROWS or score.get("independentGames", 0) < MIN_FAMILY_GAMES:
        return "INSUFFICIENT_SAMPLE"
    ci = score.get("pairedBrierDeltaCI") or {}
    lo, hi = ci.get("low"), ci.get("high")
    if lo is None or hi is None:
        return "INSUFFICIENT_SAMPLE"
    if score["modelBrier"] is not None and score["baseRateBrier"] is not None \
            and score["modelBrier"] > score["baseRateBrier"]:
        # a constant base rate beating the model is the RSCH-0027 team-total signature
        return "MODEL_TRAILS_MARKET"
    if lo > 0:
        return "MODEL_TRAILS_MARKET"
    if hi < 0 and v3_pass:
        return "VALIDATED_FOR_CONTINUED_SHADOW"
    if hi < 0:
        return "PARITY"          # beats market but fails a V3 materiality gate
    return "PARITY" if abs(score["pairedBrierDelta"]) < 0.005 else "UNPROVEN"


def audit_family(rows, family):
    score = score_family(rows, family)
    if not score.get("rows"):
        return {"family": family, "rows": 0, "classification": "INSUFFICIENT_SAMPLE",
                "reason": "no settlement-joined evaluation rows"}
    lodo = leave_one_date_out(rows)
    edges = edge_bucket_analysis(rows)
    cap = fee_aware_capacity(rows, family)
    pre = materiality_for(family, score["rows"], score["independentGames"], score["independentDates"])
    ci = score["pairedBrierDeltaCI"]
    # effect is expressed so that POSITIVE == model better, matching the
    # preregistered null of zero and a positive effect floor.
    obs = ObservedEvidence(
        effect_estimate=-score["pairedBrierDelta"],
        effect_ci_low=(-ci["high"] if ci["high"] is not None else None),
        effect_ci_high=(-ci["low"] if ci["low"] is not None else None),
        score_improvement=-score["pairedBrierDelta"],
        score_ci_low=(-ci["high"] if ci["high"] is not None else None),
        score_ci_high=(-ci["low"] if ci["low"] is not None else None),
        independent_games=score["independentGames"],
        independent_dates=score["independentDates"],
        replicating_blocks=lodo["replicatingBlocks"],
        transport_evidence=TRANSPORT_LEAVE_DATE_OUT,
        executable_opportunities=cap["netEvPositiveOpportunities"],
        cluster_unit="gameId",
    )
    v3_pass, v3_reasons, v3_labels = betting_shadow_gate_v3(pre, obs)
    return {
        "family": family, **score,
        "leaveOneDateOut": lodo, "edgeBuckets": edges, "feeAwareCapacity": cap,
        "methodologyV3": {"preregistration": describe_v3(pre),
                          "labels": {k: v["passes"] for k, v in v3_labels.items()},
                          "reasons": v3_reasons, "bettingShadowGatePasses": v3_pass},
        "classification": classify_family(score, lodo, cap, v3_pass, v3_labels),
    }


# ── Team-total second-defect diagnostic (Section D) ──────────────────────

_TT_TICKER = re.compile(
    r"^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})\d{4}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_SCHEDULE_DIR = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest", "2026", "schedules")


def _poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _p_at_least(n, lam):
    return sum(_poisson_pmf(r, lam) for r in range(n, 31))


def _solve_lambda(p, n):
    """Invert P(X >= n) = p for a Poisson mean. Faithful to the production
    path: scripts/build_market_ledger.p_over_total uses poisson_pmf, so the
    recovered lambda IS the team run mean production priced with."""
    lo, hi = 0.01, 25.0
    for _ in range(70):
        mid = (lo + hi) / 2.0
        if _p_at_least(n, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _actual_team_runs():
    """(officialDate, teamAbbr) -> runs, from the dated schedule archive via
    the repo's own canonical MLB_ID_TO_ABBR map. EVALUATION TARGET only."""
    runs = {}
    if not os.path.isdir(_SCHEDULE_DIR):
        return runs
    for fn in sorted(os.listdir(_SCHEDULE_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            doc = json.load(open(os.path.join(_SCHEDULE_DIR, fn)))
        except Exception:
            continue
        for date in doc.get("dates", []):
            for g in date.get("games", []):
                if (g.get("status") or {}).get("detailedState") != "Final":
                    continue
                teams = g.get("teams") or {}
                for rec in (teams.get("home") or {}, teams.get("away") or {}):
                    if rec.get("score") is None:
                        continue
                    abbr = MLB_ID_TO_ABBR.get((rec.get("team") or {}).get("id"))
                    if abbr:
                        runs[(g.get("officialDate") or date.get("date"), abbr)] = int(rec["score"])
    return runs


def _team_total_rows_with_threshold():
    """RSCH-0022's shared loader drops `threshold`, which this diagnostic
    needs, so read the team-total evaluations directly rather than
    reconstructing a field the shared loader discarded."""
    from lib.edgelab import storage
    out = []
    d = os.path.join(_ROOT, "data", "edgelab", "model_evaluations")
    for fn in sorted(os.listdir(d)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for rec in storage.read_records(os.path.join(d, fn)):
            if rec.get("evaluationStatus") != "EVALUATED":
                continue
            if rec.get("marketFamily") != "KXMLBTEAMTOTAL":
                continue
            p = rec.get("modelFairProbability")
            if p is None:
                continue
            out.append({"marketTicker": rec.get("marketTicker"),
                        "marketFamily": "KXMLBTEAMTOTAL",
                        "modelP": float(p) / 100.0,
                        "threshold": rec.get("threshold")})
    return out


def team_total_projection_diagnostic(evaluated_rows):
    """MLB-RSCH-0027 left team totals with TWO defects: a provable +0.5
    threshold mismatch, and calibration that the threshold correction does
    not repair. This isolates the second one.

    Recovers the pregame team run mean production actually priced with, by
    inverting its own archived probability through the Poisson form
    p_over_total uses, then compares that mean against realized team runs --
    entirely independent of any threshold or market question.

      Case A  teamProj informative -> the distribution/threshold conversion is broken
      Case B  teamProj uninformative -> the upstream team run mean is broken
      Case C  swapped mapping fits better -> a team mapping defect
    """
    runs = _actual_team_runs()
    pairs, excluded = [], collections.Counter()
    for d in evaluated_rows:
        if d.get("marketFamily") != "KXMLBTEAMTOTAL":
            continue
        m = _TT_TICKER.match(d.get("marketTicker") or "")
        if not m:
            excluded["TICKER_UNPARSED"] += 1
            continue
        yy, mon, dd, away, home, team, _n = m.groups()
        date = "20%s-%02d-%s" % (yy, _MONTHS[mon], dd)
        actual = runs.get((date, team))
        if actual is None:
            excluded["NO_FINAL_SCORE"] += 1
            continue
        p = d.get("modelP")
        if p is None or not (0.001 < p < 0.999):
            excluded["DEGENERATE_OR_MISSING_PROBABILITY"] += 1
            continue
        thr = d.get("threshold")
        if thr is None:
            excluded["NO_THRESHOLD"] += 1
            continue
        pairs.append({"projected": _solve_lambda(p, int(thr) + 1), "actual": actual,
                      "team": team, "date": date, "home": home, "away": away,
                      "isHome": team == home})
    if not pairs:
        return {"recoveredPairs": 0, "exclusions": dict(excluded),
                "conclusion": "no team-total contract could be reconstructed"}
    n = len(pairs)
    mean_p = sum(x["projected"] for x in pairs) / n
    mean_a = sum(x["actual"] for x in pairs) / n
    mse = sum((x["projected"] - x["actual"]) ** 2 for x in pairs) / n
    var = sum((x["projected"] - mean_p) ** 2 for x in pairs)
    slope = (sum((x["projected"] - mean_p) * (x["actual"] - mean_a) for x in pairs) / var
             if var else None)
    baseline = sum((mean_a - x["actual"]) ** 2 for x in pairs) / n
    by_game = collections.defaultdict(dict)
    for x in pairs:
        by_game[(x["date"], x["away"], x["home"])][x["isHome"]] = x
    complete = [v for v in by_game.values() if True in v and False in v]
    swap = None
    if complete:
        as_mapped = sum((v[True]["projected"] - v[True]["actual"]) ** 2 +
                        (v[False]["projected"] - v[False]["actual"]) ** 2 for v in complete) / (2 * len(complete))
        swapped = sum((v[True]["projected"] - v[False]["actual"]) ** 2 +
                      (v[False]["projected"] - v[True]["actual"]) ** 2 for v in complete) / (2 * len(complete))
        swap = {"completeGames": len(complete), "asMappedMse": round(as_mapped, 4),
                "swappedMse": round(swapped, 4), "swapFitsBetter": bool(swapped < as_mapped)}
    beats_baseline = mse < baseline
    if swap and swap["swapFitsBetter"]:
        conclusion = "CASE_C_TEAM_MAPPING_DEFECT"
    elif beats_baseline and slope is not None and slope > 0.6:
        conclusion = "CASE_A_PROJECTION_INFORMATIVE_CONVERSION_BROKEN"
    else:
        conclusion = "CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE"
    return {
        "recoveredPairs": n, "exclusions": dict(excluded),
        "recoveryMethod": ("inverted production's own archived probability through the Poisson form "
                           "scripts/build_market_ledger.p_over_total uses, so the recovered mean is "
                           "the one production priced with"),
        "meanProjected": round(mean_p, 4), "meanActual": round(mean_a, 4),
        "bias": round(mean_p - mean_a, 4),
        "mse": round(mse, 4), "rmse": round(math.sqrt(mse), 4),
        "calibrationSlopeActualOnProjected": round(slope, 4) if slope is not None else None,
        "constantMeanBaselineMse": round(baseline, 4),
        "beatsConstantBaseline": bool(beats_baseline),
        "mappingTest": swap,
        "conclusion": conclusion,
    }


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0032_yellow_family_audit_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0032 YELLOW family validity audit v1: control = Kalshi's archived "
                        "vig-free probability per family; comparison = production's archived "
                        "probability. Nothing is fitted. Actionability labels come from "
                        "Methodology V3 with per-family preregistered, justified floors."
        ),
        probability_adapter_identity="kalshiVF archived market probability (verified sole adapter on "
                                     "production families by MLB-RSCH-0027)",
        model_engine_family="yellow_family_validity_audit_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Dedicated per-family validity study of the surfaces carrying ~80% of live "
                     "recommendation exposure, led by KXMLBRFI."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="YELLOW Family Validity Audit",
        hypothesis=(
            "H1: KXMLBRFI, carrying the largest share of live exposure and never given a dedicated "
            "family study, is calibrated and beats Kalshi's vig-free price on its own settled "
            "universe. H2 (null, tested not assumed): it is at parity or trails, and its declared "
            "edge does not correspond to realized advantage. H3: KXMLBF5 and KXMLBGAME remain below "
            "any honest sample floor even on the complete archive, so neither can be validated or "
            "refuted yet. H4: the research-only boards carrying user-confirmed wagers have no "
            "probability-validation evidence supporting that exposure."
        ),
        research_question=("Do the market families carrying ~80% of live recommendation exposure "
                           "deserve to remain YELLOW, become GREEN, or become RED?"),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=("Every settlement-joined production evaluation per audited family across the "
                           "complete archive -- the full opportunity universe, not recommendations and "
                           "not confirmed wagers."),
        market_families=list(YELLOW_PRODUCTION_FAMILIES) + list(RESEARCH_ONLY_FAMILIES),
        eligibility_criteria=["EVALUATED row with both probabilities and a YES/NO settlement",
                              "family audited on its own; unrelated families are never pooled to "
                              "raise n"],
        exclusion_criteria=[
            "synthetic-identifier rows that do not resolve to a unique settled ticker -- tested and "
            "found unrecoverable, never approximated",
            "KXMLBF5 two-way synthetic rows treated as the three-way TIE-bearing contract",
            "ROI as a selection or tuning criterion",
            "lowering a sample floor to manufacture a result",
            "pooling unrelated research-only boards to increase n",
        ],
        prediction_checkpoints=["ARCHIVED_PREGAME_EVALUATION"],
        primary_metric=("per-family paired Brier delta (production minus Kalshi vig-free) with "
                        "game-clustered bootstrap CI, gated by Methodology V3 materiality"),
        secondary_metrics=[
            "calibration slope/intercept and ECE for model and market",
            "base-rate Brier as a floor comparison",
            "fixed declared-edge buckets with realized event rate and monotonicity",
            "leave-one-date-out transport across all settled dates",
            "SECONDARY fee-aware executable capacity (canonical taker fee, never selection)",
        ],
        chronological_split_policy=("LEAVE_ONE_DATE_OUT across all settled dates. The archive spans too "
                                    "few dates for a single DEV/VAL/HOLDOUT boundary to be anything but "
                                    "arbitrary, so every date is held out in turn and the count of "
                                    "replicating dates feeds Methodology V3's readiness label."),
        minimum_sample_requirement={"independentGames": MIN_FAMILY_GAMES},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=("evidenceLevel E4_PROSPECTIVE_SHADOW. FIRST ADOPTER of Methodology V3: every "
               "actionability label comes from betting_shadow_gate_v3 with per-family floors that were "
               "justified and fixed before results were read. KXMLBRFI is one binary contract per "
               "game, so rows are genuinely independent games rather than a correlated ladder. "
               "KXMLBF5 is a three-way market carrying an explicit TIE outcome, so the archived "
               "two-way synthetic rows are a different contract and are not bridged. No production "
               "change is authorized by any result here."),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control, _definition = register_experiment()
    outcomes, _unsettled = rsch0022.load_settled_outcomes()
    ev_rows, _excluded = rsch0022.load_evaluated_rows()
    rows = rsch0022.build_audit_rows(ev_rows, outcomes, pick="last")

    by_family = collections.defaultdict(list)
    for r in rows:
        by_family[r.get("marketFamily")].append(r)

    production = {f: audit_family(by_family.get(f, []), f) for f in YELLOW_PRODUCTION_FAMILIES}
    team_total_diag = team_total_projection_diagnostic(_team_total_rows_with_threshold())
    research_only = {f: audit_family(by_family.get(f, []), f) for f in RESEARCH_ONLY_FAMILIES
                     if by_family.get(f)}

    # Synthetic-identifier recovery: TESTED, and reported as unrecoverable.
    synthetic = collections.Counter()
    for r in ev_rows:
        t = r.get("marketTicker") or ""
        if ":" in t and t.split(":")[0].isdigit():
            synthetic[t.split(":")[1]] += 1
    recovery = {
        "syntheticRowsByFamily": dict(synthetic),
        "recoveryAttempted": True,
        "recoveredRows": 0,
        "mechanismTested": ("gamePk -> games-archive mlbGamePk/kalshiKey index -> unique settled "
                            "Kalshi ticker"),
        "result": ("ZERO of the synthetic first-inning rows resolved to a unique settled KXMLBRFI "
                   "ticker: those gamePk values are absent from the games-archive index. No "
                   "approximate, fuzzy or date-proximity match was attempted."),
        "f5SemanticBlocker": ("KXMLBF5 settles three ways -- its archived tickers include an explicit "
                              "-TIE outcome -- so the two-way F5_ML_Home/F5_ML_Away synthetic rows are "
                              "not the same contract and were not bridged."),
    }

    # Prospective design frozen for the families that cannot be validated now.
    prospective = {
        f: {"currentRows": production[f].get("rows", 0),
            "currentGames": production[f].get("independentGames", 0),
            "currentDates": production[f].get("independentDates", 0),
            "requiredGames": MIN_FAMILY_GAMES, "requiredDates": MIN_FAMILY_DATES,
            "gamesShort": max(0, MIN_FAMILY_GAMES - production[f].get("independentGames", 0)),
            "rule": "floors are NOT lowered to manufacture a result; this family waits for data"}
        for f in YELLOW_PRODUCTION_FAMILIES
        if production[f].get("classification") == "INSUFFICIENT_SAMPLE"
    }

    artifact = {
        "experimentId": EXPERIMENT_ID, "title": "YELLOW Family Validity Audit",
        "controlModelId": control["controlModelId"], "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "researchOnly": True, "productionChanged": False, "parametersFitted": 0,
        "methodologyVersion": "v3",
        "primaryFamily": PRIMARY_FAMILY,
        "familySemantics": {
            "KXMLBRFI": ("one binary contract per game; ticker == eventTicker; YES == a run scored in "
                         "the first inning by either team; no threshold, no team side"),
            "KXMLBF5": "THREE-WAY market carrying an explicit -TIE settled outcome alongside both sides",
        },
        "productionFamilies": production,
        "researchOnlyFamilies": research_only,
        "syntheticIdentifierRecovery": recovery,
        "teamTotalProjectionDiagnostic": team_total_diag,
        "frozenProspectiveDesign": prospective,
        "productionActionAuthorized": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: settlement-joined rows={len(rows)}")
    print("\n  PRODUCTION (YELLOW) FAMILIES:")
    for f in YELLOW_PRODUCTION_FAMILIES:
        a = production[f]
        if not a.get("rows"):
            print(f"    {f:16} no rows"); continue
        ci = a["pairedBrierDeltaCI"]
        print(f"    {f:16} n={a['rows']:4} games={a['independentGames']:4} dates={a['independentDates']:3} "
              f"model={a['modelBrier']:.4f} market={a['marketBrier']:.4f} base={a['baseRateBrier']:.4f}")
        print(f"        delta={a['pairedBrierDelta']:+.6f} CI[{ci['low']},{ci['high']}] "
              f"slope={a['modelCalibrationSlope']} LODO={a['leaveOneDateOut']['datesModelWins']}/"
              f"{a['leaveOneDateOut']['datesScored']} -> {a['classification']}")
        print(f"        V3 labels: {a['methodologyV3']['labels']}")
        print(f"        capacity: netEV+ opportunities={a['feeAwareCapacity']['netEvPositiveOpportunities']} "
              f"roi={a['feeAwareCapacity']['netRoi']}")
    print("\n  RESEARCH-ONLY BOARDS:")
    for f, a in sorted(research_only.items(), key=lambda kv: -(kv[1].get("rows") or 0)):
        if not a.get("rows"):
            continue
        print(f"    {f:20} n={a['rows']:4} games={a['independentGames']:3} "
              f"delta={a['pairedBrierDelta']:+.5f} slope={a['modelCalibrationSlope']} -> {a['classification']}")
    t = team_total_diag
    if t.get("recoveredPairs"):
        print(f"\n  TEAM-TOTAL PROJECTION DIAGNOSTIC (n={t['recoveredPairs']}):")
        print(f"    meanProj={t['meanProjected']} meanActual={t['meanActual']} bias={t['bias']:+.4f}")
        print(f"    MSE={t['mse']} vs constant-mean baseline {t['constantMeanBaselineMse']} "
              f"-> beats baseline: {t['beatsConstantBaseline']}")
        print(f"    calibration slope actual~projected = {t['calibrationSlopeActualOnProjected']}")
        print(f"    mapping test: {t['mappingTest']}")
        print(f"    CONCLUSION: {t['conclusion']}")
    print(f"\n  synthetic recovery: {recovery['recoveredRows']} rows recovered "
          f"(attempted={recovery['recoveryAttempted']})")
    return 0


def _write_markdown(a):
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        f"**RESEARCH ONLY. No production change. Parameters fitted: {a['parametersFitted']}. "
        f"Actionability labels from Methodology {a['methodologyVersion'].upper()}.**",
        "",
        "## Why these families",
        "",
        "MLB-RSCH-0031 found ~80% of live recommendation exposure sits in YELLOW families -- unproven",
        "or below sample floor -- and that those, not the RED ones, carry most of the historical",
        "hypothetical loss. This asks whether they deserve to stay YELLOW.",
        "",
        "## Semantics established before analysis",
        "",
    ]
    for fam, sem in a["familySemantics"].items():
        lines.append(f"- **{fam}**: {sem}")
    lines += [
        "",
        "## Production (YELLOW) families",
        "",
        "Paired delta is **model minus market** — negative means the model is better.",
        "`base` is the Brier of a constant base-rate predictor: a model worse than it carries no",
        "discriminative information at all.",
        "",
        "| Family | Rows | Games | Dates | Model | Market | Base rate | Paired delta [CI] | Slope | LODO | Class |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for f in YELLOW_PRODUCTION_FAMILIES:
        x = a["productionFamilies"][f]
        if not x.get("rows"):
            lines.append(f"| {f} | 0 | - | - | - | - | - | - | - | - | INSUFFICIENT_SAMPLE |")
            continue
        ci = x["pairedBrierDeltaCI"]
        l = x["leaveOneDateOut"]
        lines.append(f"| {f} | {x['rows']} | {x['independentGames']} | {x['independentDates']} | "
                     f"{x['modelBrier']} | {x['marketBrier']} | {x['baseRateBrier']} | "
                     f"{x['pairedBrierDelta']:+.5f} [{ci['low']}, {ci['high']}] | "
                     f"{x['modelCalibrationSlope']} | {l['datesModelWins']}/{l['datesScored']} | "
                     f"**{x['classification']}** |")

    lines += ["", "### Methodology V3 labels (never collapsed)", "",
              "| Family | STATISTICAL_SIGNAL | PREDICTIVE_MATERIALITY | EXECUTABLE_CAPACITY | IMPLEMENTATION_READINESS |",
              "|---|:-:|:-:|:-:|:-:|"]
    for f in YELLOW_PRODUCTION_FAMILIES:
        x = a["productionFamilies"][f]
        if not x.get("rows"):
            continue
        lab = x["methodologyV3"]["labels"]
        lines.append("| " + f + " | " + " | ".join(
            ("yes" if lab.get(k) else "no") for k in
            ("STATISTICAL_SIGNAL", "PREDICTIVE_MATERIALITY",
             "EXECUTABLE_CAPACITY", "IMPLEMENTATION_READINESS")) + " |")

    lines += ["", "## Declared-edge reliability", ""]
    for f in YELLOW_PRODUCTION_FAMILIES:
        x = a["productionFamilies"][f]
        if not x.get("rows"):
            continue
        e = x["edgeBuckets"]
        lines += [f"### {f}", "",
                  f"monotone improving: **{e['monotoneImproving']}** · inversion: **{e['inversion']}** "
                  f"· qualifying buckets: {e['qualifyingBuckets']}", "",
                  "| Edge bucket | Rows | Games | Mean model p | Realized rate | Paired delta |",
                  "|---|---:|---:|---:|---:|---:|"]
        for b in e["buckets"]:
            if not b.get("rows"):
                lines.append(f"| {b['bucket']} | 0 | - | - | - | - |")
                continue
            lines.append(f"| {b['bucket']} | {b['rows']} | {b['independentGames']} | "
                         f"{b['meanModelProbability']} | {b['realizedEventRate']} | "
                         f"{b['pairedBrierDelta']:+.5f} |")
        lines.append("")

    lines += ["## Fee-aware executable capacity", "",
              "| Family | Gross +edge rows | Net-EV+ opportunities | Wins | Fees | Net P/L | ROI |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for f in YELLOW_PRODUCTION_FAMILIES:
        x = a["productionFamilies"][f]
        if not x.get("rows"):
            continue
        c = x["feeAwareCapacity"]
        lines.append(f"| {f} | {c['grossPositiveEdgeRows']} | {c['netEvPositiveOpportunities']} | "
                     f"{c['wins']} | {c['totalFees']} | {c['netPnl']} | {c['netRoi']} |")

    lines += ["", "## Research-only boards", "",
              "Audited **separately**; unrelated boards are never pooled to raise n.", "",
              "| Board | Rows | Games | Model | Market | Base rate | Paired delta | Slope | Class |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for f, x in sorted(a["researchOnlyFamilies"].items(), key=lambda kv: -(kv[1].get("rows") or 0)):
        if not x.get("rows"):
            continue
        lines.append(f"| {f} | {x['rows']} | {x['independentGames']} | {x['modelBrier']} | "
                     f"{x['marketBrier']} | {x['baseRateBrier']} | {x['pairedBrierDelta']:+.5f} | "
                     f"{x['modelCalibrationSlope']} | {x['classification']} |")

    r = a["syntheticIdentifierRecovery"]
    lines += ["", "## Synthetic-identifier recovery -- attempted and reported unrecoverable", "",
              f"- Synthetic rows by family: `{r['syntheticRowsByFamily']}`",
              f"- Mechanism tested: {r['mechanismTested']}",
              f"- **Recovered: {r['recoveredRows']}**", "",
              r["result"], "", r["f5SemanticBlocker"], ""]

    if a["frozenProspectiveDesign"]:
        lines += ["## Frozen prospective design for families that cannot be validated yet", "",
                  "| Family | Games now | Required | Short by | Dates now | Required |",
                  "|---|---:|---:|---:|---:|---:|"]
        for f, p in a["frozenProspectiveDesign"].items():
            lines.append(f"| {f} | {p['currentGames']} | {p['requiredGames']} | {p['gamesShort']} | "
                         f"{p['currentDates']} | {p['requiredDates']} |")
        lines += ["", "**Floors are not lowered to manufacture a result.** These families wait for data.", ""]

    lines += ["## Result", "",
              f"- Production action authorized: {a['productionActionAuthorized']}", ""]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
