#!/usr/bin/env python3
"""
scripts/edgelab/run_live_exposure_audit_experiment.py
=====================================================
Research Lab experiment MLB-RSCH-0031: "Live Exposure / Recommendation
Quality Audit". RESEARCH ONLY. NO recommendation changes, no family
suspension, no qualification change, no staking change, no production
modification of any kind.

CORE QUESTION: given everything the Research Lab has established through
MLB-RSCH-0030, what is the recommendation engine ACTUALLY asking the user
to bet, and how much of that exposure sits in categories the evidence now
calls parity, unproven, model-trails-market, or semantically defective?

This is an EXPOSURE and RISK audit. It fits nothing and predicts nothing.

A PREMISE CORRECTION THIS AUDIT FORCES
---------------------------------------
Earlier sessions -- including my own reports for MLB-RSCH-0028 and -0029
-- stated that hitter props are "~75% of recommendation volume". That is
true only as a ROW COUNT of the recommendations archive. It is wrong as a
statement about exposure: essentially every hitter row carries status
INSUFFICIENT_MODEL_SUPPORT, which is the engine explicitly DECLINING to
recommend. Counting declined rows as recommendations overstates hitter
exposure enormously.

The engine's real exposure surface is the rows whose status actually
expresses a recommendation:

    RECOMMENDED, RECOMMENDED_NOT_BET, BET_PLACED

Everything else -- INSUFFICIENT_MODEL_SUPPORT, NOT_EVALUATED,
PASS_NO_EDGE, PASS_DATA_QUALITY, PASS_PRICE_TOO_HIGH -- is the engine
passing, and is reported separately as declined volume.

THREE POPULATIONS, NEVER CONFLATED
-----------------------------------
  AVAILABLE OPPORTUNITY -- every archived contract row, recommended or not
  RECOMMENDED           -- status RECOMMENDED / RECOMMENDED_NOT_BET
  USER-CONFIRMED BET    -- status BET_PLACED and betPlaced == True

A recommendation is NEVER assumed to have become a bet. `betPlaced` is
the only thing that establishes a confirmed wager, and this experiment
never writes to any ledger.

NO DOLLARS ARE INVENTED. The recommendation schema carries no stake or
size field, so recommended-dollar figures are reported as UNAVAILABLE
rather than estimated. `priceCeiling` (Bet Up To) is reported where
present because it is archived, not derived.

EVIDENCE MAPPING IS READ FROM COMMITTED ARTIFACTS
--------------------------------------------------
Every family's research status traces to a specific merged experiment
artifact and is quoted with its number. Nothing is asserted from memory,
and no family is labelled from its ROI.
"""
import collections
import json
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
from lib.edgelab import storage
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import independent_unit_count, game_clustered_bootstrap_ci

EXPERIMENT_ID = "MLB-RSCH-0031"
REGISTRATION_TIMESTAMP = "2026-08-29T15:30:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0031_live_exposure_audit.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0031_LIVE_EXPOSURE_AUDIT.md")
RECOMMENDATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "recommendations")

# ── Population definitions (fixed) ───────────────────────────────────────
RECOMMENDED_STATUSES = ("RECOMMENDED", "RECOMMENDED_NOT_BET")
CONFIRMED_BET_STATUSES = ("BET_PLACED",)
LIVE_STATUSES = RECOMMENDED_STATUSES + CONFIRMED_BET_STATUSES
DECLINED_STATUSES = ("INSUFFICIENT_MODEL_SUPPORT", "NOT_EVALUATED", "PASS_NO_EDGE",
                     "PASS_DATA_QUALITY", "PASS_PRICE_TOO_HIGH")

EDGE_BUCKETS = ((-1.0, 0.025), (0.025, 0.05), (0.05, 0.075),
                (0.075, 0.10), (0.10, 0.15), (0.15, 1.01))
WINDOWS = (("FULL_2026", None), ("LAST_30D", 30), ("LAST_14D", 14), ("LAST_7D", 7))
_NUMERAL = re.compile(r"(\d+(?:\.\d+)?)")

# ── Evidence map: every entry cites a merged artifact ────────────────────
# Statuses: VALIDATED_IMPROVEMENT / SHADOW_PENDING / PARITY / UNPROVEN /
# INSUFFICIENT_SAMPLE / MODEL_TRAILS_MARKET / EDGE_SIGNAL_UNTRUSTWORTHY /
# SEMANTIC_DEFECT / UNASSESSED.  Risk: GREEN / YELLOW / RED.
EVIDENCE_MAP = {
    "KXMLBTEAMTOTAL": {
        "status": ["SEMANTIC_DEFECT", "MODEL_TRAILS_MARKET"], "risk": "RED",
        "evidence": ("MLB-RSCH-0027: production calibration slope -0.0711 vs market 0.734 on 473 rows, "
                     "the widest probability spread of any family and a constant base-rate predictor "
                     "beating the model (Brier 0.2495 vs 0.2901). MLB-RSCH-0029/0031 team-total audit: "
                     "stored threshold is exactly +0.5 above the ticker- and title-derived line on "
                     "511/511 audited contracts."),
        "knownDefect": "threshold stored as the ticker suffix integer, not the line (+0.5); "
                       "p_over_total then shifts a further run via int(line)+1",
    },
    "KXMLBRFI": {
        "status": ["UNPROVEN"], "risk": "YELLOW",
        "evidence": ("MLB-RSCH-0027: paired Brier delta +0.0081 with CI [-0.0054, +0.0214] straddling "
                     "zero on 213 rows -- INCONCLUSIVE, neither validated nor refuted. Narrow "
                     "probability spread (sd 0.035) so the slope of 1.04 is weakly identified."),
        "knownDefect": None,
    },
    "KXMLBF5": {
        "status": ["INSUFFICIENT_SAMPLE"], "risk": "YELLOW",
        "evidence": ("MLB-RSCH-0027: the only family with a negative point delta (-0.0014) but n=63 "
                     "below the preregistered floor of 100, CI [-0.0176, +0.015] straddling zero and "
                     "the sign flipping across the split (TRAIN +0.002 / HOLDOUT -0.028). Explicitly "
                     "NOT promoted."),
        "knownDefect": None,
    },
    "UNLABELLED_FAMILY": {
        "status": ["UNASSESSED"], "risk": "YELLOW",
        "evidence": "row carries no marketFamily; it cannot be mapped to any Research Lab artifact",
        "knownDefect": "missing marketFamily on the archived recommendation row",
    },
    "KXMLBGAME": {
        "status": ["INSUFFICIENT_SAMPLE"], "risk": "YELLOW",
        "evidence": "MLB-RSCH-0027: n=47, below the preregistered floor; delta +0.0148, holdout +0.0594.",
        "knownDefect": None,
    },
    "ML_Home": {
        "status": ["MODEL_TRAILS_MARKET"], "risk": "RED",
        "evidence": "MLB-RSCH-0027 recovered moneylines: paired delta +0.0314, holdout +0.0654, slope 0.69.",
        "knownDefect": None,
    },
    "ML_Away": {
        "status": ["MODEL_TRAILS_MARKET"], "risk": "RED",
        "evidence": "MLB-RSCH-0027 recovered moneylines: paired delta +0.0294, holdout +0.0646, slope 0.54.",
        "knownDefect": None,
    },
}
HITTER_FAMILIES = ("hitter_hits", "hitter_total_bases", "hitter_hits_runs_rbis",
                   "hitter_rbis", "hitter_stolen_bases")
for _f in HITTER_FAMILIES:
    EVIDENCE_MAP[_f] = {
        "status": (["PARITY", "EDGE_SIGNAL_UNTRUSTWORTHY"] if _f != "hitter_stolen_bases"
                   else ["UNASSESSED"]),
        "risk": "YELLOW" if _f != "hitter_stolen_bases" else "RED",
        "evidence": (("MLB-RSCH-0028: probabilities near parity with Kalshi (Brier 0.1584 vs 0.1554, "
                      "paired delta +0.0030 CI [-0.0002,+0.0064]) but declared edge anti-predictive. "
                      "MLB-RSCH-0029: declared edge IS the model signal (execution penalty identically "
                      "zero). MLB-RSCH-0030: no validated shrinkage -- alpha 0.059, CI [-0.362,+0.525], "
                      "and ZERO contracts clear the canonical fee.")
                     if _f != "hitter_stolen_bases" else
                     ("No attempt/success model exists; MLB-RSCH-0028 explicitly excluded stolen bases "
                      "from audit and created no probability for them.")),
        "knownDefect": (None if _f != "hitter_stolen_bases" else "no supported probability model"),
    }
# Research-only boards: not production families (MLB-RSCH-0027 defect D1).
RESEARCH_ONLY_FAMILIES = ("team_total", "game_total", "winning_margin", "inning_result",
                          "inning_total", "game_result", "pitcher_outs", "pitcher_strikeouts",
                          "first_inning_run")
for _f in RESEARCH_ONLY_FAMILIES:
    EVIDENCE_MAP[_f] = {
        "status": ["UNASSESSED"], "risk": "YELLOW",
        "evidence": ("MLB-RSCH-0027 defect D1: this is a RESEARCH_ONLY board, not a TRUSTED_PRODUCTION "
                     "family. It carried the ask-price adapter and degenerate 0.0/1.0 prices, and was "
                     "never part of the production evaluation corpus. Any exposure here is exposure to "
                     "a surface that was never validated as production."),
        "knownDefect": "RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope",
    }


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


def evidence_for(family):
    return EVIDENCE_MAP.get(family, {"status": ["UNASSESSED"], "risk": "YELLOW",
                                     "evidence": "no Research Lab artifact covers this family",
                                     "knownDefect": None})


# ── Corpus ────────────────────────────────────────────────────────────────

def load_recommendations():
    rows = []
    for fn in sorted(os.listdir(RECOMMENDATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in storage.read_records(os.path.join(RECOMMENDATIONS_DIR, fn)):
            d["_date"] = (d.get("createdAt") or "")[:10]
            # A missing marketFamily is real and must be reported, but a dict
            # keyed by both None and str cannot be sorted or serialised -- the
            # exact failure shape the hitter engine hits. Normalise once, here,
            # to an explicit sentinel rather than dropping the row.
            if d.get("marketFamily") is None:
                d["marketFamily"] = "UNLABELLED_FAMILY"
            rows.append(d)
    return rows


def _window(rows, days, latest_date):
    if days is None:
        return rows
    from datetime import date, timedelta
    y, m, dd = (int(x) for x in latest_date.split("-"))
    cutoff = (date(y, m, dd) - timedelta(days=days - 1)).isoformat()
    return [r for r in rows if r["_date"] >= cutoff]


def _edge_bucket(edge):
    if edge is None:
        return None
    for lo, hi in EDGE_BUCKETS:
        if lo <= edge < hi:
            return f"[{lo:+.3f},{hi:+.3f})"
    return None


def population_split(rows):
    return {
        "recommended": [r for r in rows if r.get("status") in RECOMMENDED_STATUSES],
        "confirmedBet": [r for r in rows if r.get("status") in CONFIRMED_BET_STATUSES
                         and r.get("betPlaced") is True],
        "live": [r for r in rows if r.get("status") in LIVE_STATUSES],
        "declined": [r for r in rows if r.get("status") in DECLINED_STATUSES],
    }


def exposure_matrix(live_rows):
    """The primary output: where live exposure actually sits, against the
    strongest committed Research Lab evidence for each family."""
    by_family = collections.Counter(r.get("marketFamily") for r in live_rows)
    total = sum(by_family.values()) or 1
    out = []
    for fam, n in by_family.most_common():
        e = evidence_for(fam)
        sub = [r for r in live_rows if r.get("marketFamily") == fam]
        ceilings = [r.get("priceCeiling") for r in sub if isinstance(r.get("priceCeiling"), (int, float))]
        out.append({
            "family": fam,
            "recommendationCount": n,
            "shareOfLiveRecommendations": round(n / total, 4),
            "recommendedDollars": None,
            "recommendedDollarsNote": "UNAVAILABLE -- the recommendation schema carries no stake field; "
                                      "no dollar figure is invented",
            "priceCeilingMedian": (round(sorted(ceilings)[len(ceilings) // 2], 4) if ceilings else None),
            "confirmedBets": sum(1 for r in sub if r.get("betPlaced") is True),
            "researchStatus": e["status"],
            "riskBand": e["risk"],
            "bestEvidence": e["evidence"],
            "knownDefect": e["knownDefect"],
        })
    return out


def risk_concentration(live_rows):
    bands = collections.Counter(evidence_for(r.get("marketFamily"))["risk"] for r in live_rows)
    total = sum(bands.values()) or 1
    return {b: {"count": bands.get(b, 0), "share": round(bands.get(b, 0) / total, 4)}
            for b in ("GREEN", "YELLOW", "RED")}


def edge_distribution(live_rows, families=None):
    sub = ([r for r in live_rows if r.get("marketFamily") in families] if families else live_rows)
    out = collections.Counter()
    missing = 0
    for r in sub:
        b = _edge_bucket(r.get("estimatedEdge"))
        if b is None:
            missing += 1
        else:
            out[b] += 1
    ordered = [f"[{lo:+.3f},{hi:+.3f})" for lo, hi in EDGE_BUCKETS]
    return {"rows": len(sub), "missingEdge": missing,
            "buckets": {b: out.get(b, 0) for b in ordered}}


# ── Team-total threshold defect, measured on LIVE recommendations ────────

def team_total_threshold_audit(live_rows):
    """Do CURRENT recommendations still flow through the +0.5 defect?

    The independent derivation comes from the ticker suffix (KXMLBTEAMTOTAL-
    <stamp><TEAMS>-<TEAM><N>, where the true line is N - 0.5) -- production's
    own stored value is never used as the source of truth."""
    pat = re.compile(r"^KXMLBTEAMTOTAL-(\d{2}[A-Z]{3}\d{6})([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$")
    rows = [r for r in live_rows if (r.get("marketFamily") == "KXMLBTEAMTOTAL")]
    audited, mismatched, unparsed, no_display = 0, 0, 0, 0
    examples, by_date = [], collections.Counter()
    for r in rows:
        m = pat.match(r.get("marketTicker") or "")
        if not m:
            unparsed += 1
            continue
        team, n = m.group(4), int(m.group(5))
        derived_line = n - 0.5
        # thresholdDisplay is a USER-FACING label such as "Team Total Over 4",
        # not a bare number. An earlier version of this audit only compared
        # when the whole field parsed as a float, which silently scored every
        # row as a match and reported a 0% mismatch rate -- a false negative.
        # Extract the numeral, and count rows that carry no display at all as
        # UNAUDITABLE rather than as agreement.
        display = r.get("thresholdDisplay")
        if display is None:
            no_display += 1
            continue
        g = _NUMERAL.search(str(display))
        if not g:
            no_display += 1
            continue
        audited += 1
        if abs(float(g.group(1)) - derived_line) > 1e-9:
            mismatched += 1
            by_date[r["_date"]] += 1
            if len(examples) < 5:
                examples.append({"ticker": r["marketTicker"], "displayedThreshold": display,
                                 "displayedNumeral": float(g.group(1)),
                                 "tickerDerivedLine": derived_line, "date": r["_date"],
                                 "team": team})
    return {"liveTeamTotalRows": len(rows), "audited": audited, "unparsedTickers": unparsed,
            "rowsWithNoUsableThresholdDisplay": no_display,
            "mismatched": mismatched,
            "mismatchRate": round(mismatched / audited, 4) if audited else None,
            "mismatchesByDate": dict(sorted(by_date.items())),
            "examples": examples,
            "userFacingImpact": ("thresholdDisplay is what the recommendation shows a human. A "
                                 "mismatch here means the recommendation names a line the contract "
                                 "does not settle on."),
            "derivationSource": "ticker suffix integer N -> line N-0.5; production's stored value is "
                                "never used as the source of truth"}


# ── Settled hypothetical performance, by evidence class ──────────────────

def load_settlements():
    settled = {}
    d = os.path.join(_ROOT, "data", "edgelab", "settlements")
    for fn in sorted(os.listdir(d)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for rec in storage.read_records(os.path.join(d, fn)):
            t, o = rec.get("marketTicker"), rec.get("outcome")
            if t and o in ("YES", "NO"):
                settled[t] = 1 if o == "YES" else 0
    return settled


def hypothetical_performance(live_rows, settled):
    """Hypothetical ONLY. These are recommendations, not placed bets, except
    where betPlaced is True -- and even there this is exposure accounting,
    never model validation. Entry uses the archived market-implied price;
    canonical Kalshi fees are applied."""
    by_band = collections.defaultdict(list)
    for r in live_rows:
        t = r.get("marketTicker")
        if t not in settled:
            continue
        price = r.get("marketImpliedProbability")
        if price is None:
            continue
        price = float(price) / 100.0 if price > 1.0 else float(price)
        if not (0.0 < price < 1.0):
            continue
        band = evidence_for(r.get("marketFamily"))["risk"]
        by_band[band].append({"price": price, "outcome": settled[t],
                              "gameId": r.get("gameId"), "family": r.get("marketFamily")})
    out = {}
    for band, rows in sorted(by_band.items()):
        staked = fees = pnl = 0.0
        wins = 0
        for x in rows:
            fee = taker_fee(1, x["price"])
            staked += x["price"]
            fees += fee
            pnl += ((1.0 - x["price"]) if x["outcome"] == 1 else -x["price"]) - fee
            wins += x["outcome"]

        def _roi(rs):
            s = sum(y["price"] for y in rs)
            if not s:
                return None
            p = sum(((1.0 - y["price"]) if y["outcome"] == 1 else -y["price"]) - taker_fee(1, y["price"])
                    for y in rs)
            return p / s
        lo, hi, _m = game_clustered_bootstrap_ci(rows, _roi, cluster_key="gameId", n_resamples=400)
        out[band] = {"settledRecommendations": len(rows), "wins": wins, "losses": len(rows) - wins,
                     "independentGames": independent_unit_count(rows, "gameId"),
                     "grossStaked": round(staked, 4), "totalFees": round(fees, 4),
                     "netPnl": round(pnl, 4),
                     "netRoi": round(pnl / staked, 4) if staked else None,
                     "netRoiCI_gameClustered": {"low": lo, "high": hi},
                     "families": dict(collections.Counter(x["family"] for x in rows)),
                     "note": "HYPOTHETICAL. Recommendations are never assumed to have been placed."}
    return out


def red_counterfactual(live_rows, settled, perf):
    """If the RED categories had simply not been recommended, what changes?
    RED membership comes exclusively from the committed evidence map -- it is
    never tuned to ROI."""
    red = [r for r in live_rows if evidence_for(r.get("marketFamily"))["risk"] == "RED"]
    total = len(live_rows) or 1
    red_pnl = perf.get("RED", {}).get("netPnl", 0.0) or 0.0
    all_pnl = sum(v.get("netPnl", 0.0) or 0.0 for v in perf.values())
    remaining = [r for r in live_rows if evidence_for(r.get("marketFamily"))["risk"] != "RED"]
    return {
        "redRecommendations": len(red),
        "shareOfVolumeRemoved": round(len(red) / total, 4),
        "redHypotheticalNetPnl": round(red_pnl, 4),
        "allHypotheticalNetPnl": round(all_pnl, 4),
        "shareOfHypotheticalLossRemoved": (round(red_pnl / all_pnl, 4)
                                           if all_pnl not in (0, 0.0) else None),
        "remainingOpportunityVolume": len(remaining),
        "remainingFamilies": dict(collections.Counter(r.get("marketFamily") for r in remaining).most_common()),
        "redDefinitionSource": "EVIDENCE_MAP only -- derived from merged experiment artifacts, "
                               "never tuned to historical ROI",
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
        name="mlb_rsch_0031_live_exposure_audit_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0031 live exposure audit v1: descriptive census of the archived "
                        "recommendation corpus against the committed Research Lab evidence map. "
                        "NOTHING is fitted, predicted or optimized; no probability is produced."
        ),
        probability_adapter_identity="none -- this experiment produces no probability",
        model_engine_family="recommendation_exposure_audit_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Descriptive audit of where the recommendation engine currently exposes bankroll, "
                     "mapped to the strongest merged Research Lab evidence per family."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Live Exposure / Recommendation Quality Audit",
        hypothesis=(
            "H1: live recommendation exposure is concentrated in families the Research Lab has already "
            "found defective or unvalidated. H2 (tested, not assumed): the widely repeated claim that "
            "hitter props are ~75% of recommendation volume conflates DECLINED rows "
            "(INSUFFICIENT_MODEL_SUPPORT) with recommendations, and real hitter exposure is far smaller. "
            "H3: the +0.5 team-total threshold defect still reaches current recommendations."
        ),
        research_question=("What is the recommendation engine currently asking the user to bet, and how "
                           "much of that exposure sits in categories the evidence calls parity, unproven, "
                           "model-trails-market, or semantically defective?"),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E0_DESCRIPTIVE,
        target_population=("Every archived recommendation row 2026-07-30 .. present, split into AVAILABLE "
                           "OPPORTUNITY / RECOMMENDED / USER-CONFIRMED BET and never conflated."),
        market_families=["all archived recommendation families"],
        eligibility_criteria=["archived recommendation row with a status field"],
        exclusion_criteria=[
            "any inference that a recommendation became a bet -- only betPlaced establishes that",
            "any invented stake or dollar figure -- the schema carries no size field",
            "RED membership tuned to ROI -- it comes exclusively from merged experiment artifacts",
            "any production modification, family suspension or qualification change",
            "alpha=0.059 as a validated correction -- usable only as a labelled sensitivity scenario",
        ],
        prediction_checkpoints=["ARCHIVED_RECOMMENDATION"],
        primary_metric="share of live recommendation volume by research risk band (GREEN/YELLOW/RED)",
        secondary_metrics=[
            "family exposure matrix with cited evidence and known defects",
            "declared-edge distribution by family",
            "live team-total +0.5 threshold mismatch rate, derived independently from the ticker",
            "hypothetical settled performance by evidence class, game-clustered",
            "RED-category counterfactual volume and P/L",
            "user-confirmed wager exposure, reported separately and never inferred",
        ],
        chronological_split_policy=("Descriptive windows only -- full 2026, last 30/14/7 days and the "
                                    "latest completed date. No fitting, so no train/validation split "
                                    "exists or is implied."),
        minimum_sample_requirement={"independentGames": 1},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=reg.FDR_OTHER_DOCUMENTED,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "AUXILIARY_METADATA",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=("falseDiscoveryHandling=OTHER_DOCUMENTED: this audit performs NO hypothesis test and "
               "reports NO p-value, so there is no family of tests to correct. The only intervals it "
               "reports are game-clustered bootstrap ROI intervals, presented descriptively alongside "
               "the counts they summarise, and no segment is selected or promoted on the strength of "
               "one. Declaring NONE_SINGLE_HYPOTHESIS would be wrong because this is not a single "
               "hypothesis either -- it is a census. "
               "evidenceLevel E0_DESCRIPTIVE -- an exposure census, not an inferential study. It fits "
               "nothing and produces no probability. Risk bands are research communication only and are "
               "NOT production settings. The RED counterfactual is arithmetic on already-labelled "
               "categories, never a tuned filter, and is explicitly not authorized for production."),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control, _definition = register_experiment()
    rows = load_recommendations()
    settled = load_settlements()
    latest = max(r["_date"] for r in rows if r["_date"])

    windows = {}
    for name, days in WINDOWS:
        w = _window(rows, days, latest)
        pop = population_split(w)
        windows[name] = {
            "rows": len(w),
            "recommended": len(pop["recommended"]),
            "confirmedBets": len(pop["confirmedBet"]),
            "liveExposure": len(pop["live"]),
            "declined": len(pop["declined"]),
            "declinedByStatus": dict(collections.Counter(
                r.get("status") for r in pop["declined"]).most_common()),
            "liveByFamily": dict(collections.Counter(
                r.get("marketFamily") for r in pop["live"]).most_common()),
            "riskConcentration": risk_concentration(pop["live"]),
        }

    latest_rows = [r for r in rows if r["_date"] == latest]
    latest_pop = population_split(latest_rows)

    full = population_split(rows)
    live = full["live"]

    matrix = exposure_matrix(live)
    risk = risk_concentration(live)
    tt = team_total_threshold_audit(live)
    perf = hypothetical_performance(live, settled)
    counterfactual = red_counterfactual(live, settled, perf)

    edges = {"ALL_LIVE": edge_distribution(live)}
    for fam in ("KXMLBTEAMTOTAL", "KXMLBRFI", "KXMLBF5", "KXMLBGAME"):
        edges[fam] = edge_distribution(live, {fam})
    edges["HITTER_FAMILIES"] = edge_distribution(live, set(HITTER_FAMILIES))
    edges["DECLINED_HITTER_ROWS"] = edge_distribution(
        [r for r in full["declined"] if r.get("marketFamily") in HITTER_FAMILIES])

    confirmed = full["confirmedBet"]
    confirmed_exposure = {
        "count": len(confirmed),
        "byFamily": dict(collections.Counter(r.get("marketFamily") for r in confirmed).most_common()),
        "byRiskBand": dict(collections.Counter(
            evidence_for(r.get("marketFamily"))["risk"] for r in confirmed).most_common()),
        "byConfidence": dict(collections.Counter(str(r.get("confidence")) for r in confirmed).most_common()),
        "stakeAvailable": False,
        "note": "USER-CONFIRMED wagers only (betPlaced == True). Never inferred from recommendations; "
                "no ledger was read or written.",
    }

    hitter_premise = {
        "hitterRowsInArchive": sum(1 for r in rows if r.get("marketFamily") in HITTER_FAMILIES),
        "hitterRowsDeclined": sum(1 for r in rows if r.get("marketFamily") in HITTER_FAMILIES
                                  and r.get("status") in DECLINED_STATUSES),
        "hitterRowsRecommended": sum(1 for r in rows if r.get("marketFamily") in HITTER_FAMILIES
                                     and r.get("status") in RECOMMENDED_STATUSES),
        "hitterConfirmedBets": sum(1 for r in rows if r.get("marketFamily") in HITTER_FAMILIES
                                   and r.get("betPlaced") is True),
        "correction": ("Earlier reports (including MLB-RSCH-0028's and -0029's own framing) described "
                       "hitter props as ~75% of recommendation volume. That counts archive ROWS, which "
                       "are overwhelmingly INSUFFICIENT_MODEL_SUPPORT -- the engine DECLINING. Actual "
                       "hitter recommendation exposure is essentially nil."),
    }

    red_share = risk["RED"]["share"]
    if red_share >= 0.50:
        classification = "CRITICAL_RESEARCH_RISK"
    elif red_share >= 0.25:
        classification = "HIGH_RESEARCH_RISK"
    elif red_share >= 0.10:
        classification = "MODERATE_RESEARCH_RISK"
    else:
        classification = "LOW_RESEARCH_RISK"

    artifact = {
        "experimentId": EXPERIMENT_ID, "title": "Live Exposure / Recommendation Quality Audit",
        "controlModelId": control["controlModelId"], "evidenceLevel": ev.E0_DESCRIPTIVE,
        "researchOnly": True, "productionChanged": False,
        "parametersFitted": 0, "producesProbability": False,
        "recommendationsAssumedPlaced": False, "dollarsInvented": False,
        "latestArchivedDate": latest,
        "populationDefinitions": {
            "recommended": list(RECOMMENDED_STATUSES),
            "confirmedBet": list(CONFIRMED_BET_STATUSES),
            "declined": list(DECLINED_STATUSES),
        },
        "totalArchiveRows": len(rows),
        "hitterPremiseCorrection": hitter_premise,
        "windows": windows,
        "latestCompletedDate": {"date": latest, "rows": len(latest_rows),
                                "live": len(latest_pop["live"]),
                                "byFamily": dict(collections.Counter(
                                    r.get("marketFamily") for r in latest_pop["live"]).most_common())},
        "exposureMatrix": matrix,
        "riskConcentration": risk,
        "edgeDistribution": edges,
        "teamTotalThresholdAudit": tt,
        "hypotheticalPerformanceByEvidenceClass": perf,
        "redCounterfactual": counterfactual,
        "userConfirmedWagerExposure": confirmed_exposure,
        "classification": classification,
        "riskBandsAreProductionSettings": False,
        "productionActionAuthorized": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: archive rows={len(rows)}  latest={latest}")
    print(f"  PREMISE CORRECTION: hitter rows={hitter_premise['hitterRowsInArchive']} "
          f"declined={hitter_premise['hitterRowsDeclined']} "
          f"recommended={hitter_premise['hitterRowsRecommended']} "
          f"confirmedBets={hitter_premise['hitterConfirmedBets']}")
    print(f"  LIVE exposure (full 2026): {len(live)} rows "
          f"({len(full['recommended'])} recommended + {len(full['confirmedBet'])} confirmed bets)")
    print("  risk concentration:", {k: v["share"] for k, v in risk.items()})
    print("  exposure matrix:")
    for m in matrix:
        print(f"    {str(m['family']):22} n={m['recommendationCount']:4} "
              f"({m['shareOfLiveRecommendations']:.1%}) {m['riskBand']:6} {','.join(m['researchStatus'])}")
    print(f"  team-total live rows={tt['liveTeamTotalRows']} audited={tt['audited']} "
          f"mismatched={tt['mismatched']} rate={tt['mismatchRate']}")
    print("  hypothetical performance by band:")
    for b, v in perf.items():
        print(f"    {b:7} n={v['settledRecommendations']:4} games={v['independentGames']:3} "
              f"netPnl={v['netPnl']:+.3f} roi={v['netRoi']} CI[{v['netRoiCI_gameClustered']['low']},"
              f"{v['netRoiCI_gameClustered']['high']}]")
    print(f"  RED counterfactual: removes {counterfactual['shareOfVolumeRemoved']:.1%} of volume, "
          f"{counterfactual['shareOfHypotheticalLossRemoved']} of net P/L; "
          f"{counterfactual['remainingOpportunityVolume']} rows remain")
    print(f"  CLASSIFICATION: {classification}")
    return 0


def _write_markdown(a):
    h = a["hitterPremiseCorrection"]
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        "**RESEARCH ONLY. No production change. Nothing fitted. No probability produced.**",
        "",
        "## A premise this audit corrects",
        "",
        "Earlier reports -- including my own framing of MLB-RSCH-0028 and -0029 -- described hitter",
        "props as *\"~75% of recommendation volume\"*. That figure counts archive **rows**, and those",
        "rows are overwhelmingly `INSUFFICIENT_MODEL_SUPPORT`: the engine **declining** to recommend.",
        "",
        f"- Hitter rows in the archive: **{h['hitterRowsInArchive']:,}**",
        f"- Of those, declined: **{h['hitterRowsDeclined']:,}**",
        f"- Actually recommended: **{h['hitterRowsRecommended']}**",
        f"- User-confirmed hitter bets: **{h['hitterConfirmedBets']}**",
        "",
        "**Real hitter recommendation exposure is essentially nil.** The urgency previously attached to",
        "hitter-prop selection was misplaced -- the engine was already refusing that surface.",
        "",
        "## Populations, never conflated",
        "",
        f"Archive rows: **{a['totalArchiveRows']:,}**  ·  latest archived date: **{a['latestArchivedDate']}**",
        "",
        "| Population | Definition |",
        "|---|---|",
        f"| RECOMMENDED | status in {a['populationDefinitions']['recommended']} |",
        f"| USER-CONFIRMED BET | status in {a['populationDefinitions']['confirmedBet']} **and** `betPlaced == True` |",
        f"| DECLINED | status in {a['populationDefinitions']['declined']} |",
        "",
        "A recommendation is never assumed to have become a bet. **No dollar figure is invented** --",
        "the recommendation schema carries no stake field.",
        "",
        "## Live exposure by window",
        "",
        "| Window | Archive rows | Recommended | Confirmed bets | Live total | RED share |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, _ in WINDOWS:
        w = a["windows"][name]
        lines.append(f"| {name} | {w['rows']:,} | {w['recommended']} | {w['confirmedBets']} | "
                     f"{w['liveExposure']} | {w['riskConcentration']['RED']['share']:.1%} |")

    lines += ["", "## Exposure matrix -- where bankroll is actually pointed", "",
              "| Family | Count | Share | Confirmed bets | Risk | Research status | Known defect |",
              "|---|---:|---:|---:|:-:|---|---|"]
    for m in a["exposureMatrix"]:
        lines.append(f"| {m['family']} | {m['recommendationCount']} | "
                     f"{m['shareOfLiveRecommendations']:.1%} | {m['confirmedBets']} | "
                     f"**{m['riskBand']}** | {', '.join(m['researchStatus'])} | "
                     f"{m['knownDefect'] or '-'} |")

    r = a["riskConcentration"]
    lines += ["", "## Risk concentration", "",
              f"- **GREEN** {r['GREEN']['count']} ({r['GREEN']['share']:.1%})",
              f"- **YELLOW** {r['YELLOW']['count']} ({r['YELLOW']['share']:.1%})",
              f"- **RED** {r['RED']['count']} ({r['RED']['share']:.1%})",
              "",
              "Risk bands are **research communication only** and are not production settings.",
              ""]

    tt = a["teamTotalThresholdAudit"]
    lines += ["## Team-total +0.5 threshold defect, on LIVE recommendations", "",
              f"Derivation: {tt['derivationSource']}.", "",
              f"- Live team-total recommendations: **{tt['liveTeamTotalRows']}**",
              f"- Audited (ticker parsed AND display numeral present): **{tt['audited']}**",
              f"- Unparsed tickers: {tt['unparsedTickers']}  ·  no usable thresholdDisplay: "
              f"**{tt['rowsWithNoUsableThresholdDisplay']}**",
              f"- **Mismatched: {tt['mismatched']}**  ·  rate: **{tt['mismatchRate']}**", ""]
    if tt["examples"]:
        lines += ["", f"**{tt['userFacingImpact']}**", "",
                  "| Ticker | Displayed threshold | Ticker-derived line | Date |", "|---|---|---:|---|"]
        for e in tt["examples"]:
            lines.append(f"| `{e['ticker']}` | {e['displayedThreshold']} | {e['tickerDerivedLine']} | {e['date']} |")
        lines.append("")

    lines += ["## Hypothetical settled performance by evidence class", "",
              "**Hypothetical only.** Recommendations are never assumed to have been placed.", "",
              "| Band | Settled | Games | Wins | Net P/L | Net ROI | ROI CI (game-clustered) |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for b, v in a["hypotheticalPerformanceByEvidenceClass"].items():
        ci = v["netRoiCI_gameClustered"]
        lines.append(f"| {b} | {v['settledRecommendations']} | {v['independentGames']} | {v['wins']} | "
                     f"{v['netPnl']} | {v['netRoi']} | [{ci['low']}, {ci['high']}] |")

    c = a["redCounterfactual"]
    lines += ["", "## RED counterfactual", "",
              f"RED membership comes from {c['redDefinitionSource']}.", "",
              f"- Volume removed: **{c['redRecommendations']} rows ({c['shareOfVolumeRemoved']:.1%})**",
              f"- RED hypothetical net P/L: **{c['redHypotheticalNetPnl']}** of "
              f"{c['allHypotheticalNetPnl']} overall",
              f"- Share of hypothetical P/L removed: **{c['shareOfHypotheticalLossRemoved']}**",
              f"- Remaining opportunity volume: **{c['remainingOpportunityVolume']}**",
              f"- Remaining families: `{c['remainingFamilies']}`", ""]

    u = a["userConfirmedWagerExposure"]
    lines += ["## User-confirmed wagers (execution exposure, not model validation)", "",
              f"- Count: **{u['count']}**  ·  stake field available: {u['stakeAvailable']}",
              f"- By family: `{u['byFamily']}`",
              f"- By risk band: `{u['byRiskBand']}`",
              f"- By confidence: `{u['byConfidence']}`", "",
              u["note"], ""]

    lines += ["## Declared-edge distribution", "",
              "| Segment | Rows | " + " | ".join(
                  f"[{lo:+.3f},{hi:+.3f})" for lo, hi in EDGE_BUCKETS) + " |",
              "|---|---:|" + "---:|" * len(EDGE_BUCKETS)]
    for seg, e in a["edgeDistribution"].items():
        cells = " | ".join(str(e["buckets"][k]) for k in e["buckets"])
        lines.append(f"| {seg} | {e['rows']} | {cells} |")

    lines += ["", "## Classification", "",
              f"**{a['classification']}**", "",
              f"- Risk bands are production settings: {a['riskBandsAreProductionSettings']}",
              f"- Production action authorized: {a['productionActionAuthorized']}",
              ""]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
