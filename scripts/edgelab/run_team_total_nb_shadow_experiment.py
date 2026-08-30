"""
MLB-RSCH-0035: TEAM_TOTAL_NB_V1 CURRENT-ERA PROSPECTIVE SHADOW
==============================================================

MLB-RSCH-0034 established, on the CURRENT v1.2 production era (219 rows /
108 independent games / 9 dates):

  * the frozen negative binomial materially beats production's Poisson
    conversion -- Brier -0.0244, CI [-0.0354, -0.0134];
  * threshold-standardised against Kalshi it is -0.0100,
    CI [-0.0217, +0.0026] -- suggestive, NOT confirmed;
  * the raw pooled deficit against Kalshi is largely a threshold-mix
    artifact;
  * within-threshold favourable signs are EXPLORATORY only.

That is enough to SHADOW the candidate. It is not remotely enough to
promote it, and this experiment cannot promote it.

REGISTRATION-TIME COMMITMENTS
-----------------------------
Everything below is fixed BEFORE any post-registration outcome is
scored, and nothing here may be revised once forward rows exist:
candidate definition, dispersion value, eligible family, contract
semantics, threshold strata, standardisation weights, clustering unit,
forward sample floors, materiality floor, transport rule, economic
capacity requirement.

The candidate is FROZEN: production's own teamProj, the contract's own
ticker-derived threshold, AT_LEAST_N semantics, full-game period, and
dispersion 0.281513. No fitting. No new dispersion estimation. No
threshold-specific coefficient. No calibration map. No market anchoring.
No use of any outcome.

WHY STANDARDISATION IS MANDATORY HERE
-------------------------------------
Raw pooling across AT_LEAST_2..8 mixes contracts with materially
different base rates. RSCH-0034 showed that a pooled Kalshi deficit
significant at +0.0126 collapsed to +0.0007 and non-significance once
standardised. The forward scorer therefore reports BOTH, always, and the
weights are frozen from the preregistered reference corpus -- never
recomputed from forward outcomes.
"""
import argparse
import collections
import glob
import gzip
import json
import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab import storage
from lib.edgelab.research import methodology_v3 as v3
from lib.edgelab.shadow_distribution import FROZEN_DISPERSION
from lib.edgelab.team_total_nb_shadow import CANDIDATE_VERSION, CONTROL_VERSION

EXPERIMENT_ID = "MLB-RSCH-0035"
REGISTRATION_TIMESTAMP = "2026-08-30T19:00:00Z"
SHADOW_ENTITY = "team_total_nb_shadow_evaluations"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0035_team_total_nb_shadow.json")

# ── FROZEN AT REGISTRATION ───────────────────────────────────────────────

# Threshold weights from the MLB-RSCH-0034 CURRENT-ERA reference corpus
# (v1.2 rows only). Fixed here, in source, so a forward run cannot
# silently recompute them from the outcomes it is scoring.
FROZEN_THRESHOLD_WEIGHTS = {2: 11, 3: 29, 4: 104, 5: 54, 6: 13, 7: 2, 8: 6}

STRATUM_ROW_FLOOR = 30
CLUSTERING_UNIT = "gameId"

# Forward checkpoints. Chosen before any forward outcome exists; the
# 100-game material floor matches the fixed KXMLBF5 floor already in use
# so no family is held to a lower evidential bar than another.
CHECKPOINTS = (
    {"name": "CHECKPOINT_0_HEALTH", "minGames": 0, "minDates": 0,
     "label": "HEALTH_ONLY", "inference": False},
    {"name": "CHECKPOINT_1_EARLY_DIRECTION", "minGames": 50, "minDates": 5,
     "label": "DIRECTIONAL_NO_INFERENCE", "inference": False},
    {"name": "CHECKPOINT_2_FIRST_MATERIAL", "minGames": 100, "minDates": 10,
     "label": "FIRST_MATERIAL_CHECK", "inference": True},
    {"name": "CHECKPOINT_3_STRONGER", "minGames": 200, "minDates": 20,
     "label": "STRONGER_CHECK", "inference": True},
)

# The status vocabulary deliberately contains no approval token.
FORWARD_STATUSES = (
    "INSUFFICIENT_FORWARD_DATA",
    "ACCRUING_BELOW_MATERIAL_FLOOR",
    "MATERIAL_CHECK_AVAILABLE",
    "STRONGER_CHECK_AVAILABLE",
)

PROMOTION_CRITERIA = (
    "material proper-score improvement vs CURRENT production",
    "threshold-standardised result not materially worse than Kalshi",
    "preferably positive incremental signal vs Kalshi",
    "consistency across dates",
    "no single threshold dominates the effect",
    "sufficient executable fee-positive opportunity capacity",
)


def _current_git_commit_sha():
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                              capture_output=True, text=True).stdout.strip() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def candidate_fingerprint():
    """A hash over the candidate's COMPLETE definition.

    If any element of the frozen definition changes, this changes -- so a
    later run cannot quietly claim to be shadowing the same candidate.
    """
    return rlids.config_fingerprint(config_text=json.dumps({
        "candidateVersion": CANDIDATE_VERSION,
        "controlVersion": CONTROL_VERSION,
        "dispersion": FROZEN_DISPERSION,
        "mean": "production compute_projections teamProj, unmodified",
        "threshold": "ticker suffix digit N, exact",
        "semantics": "AT_LEAST_N (YES iff team_runs >= N)",
        "period": "FULL_GAME",
        "cap": "control only: min(p, 0.95), reproducing production v1.2",
        "fitting": "NONE",
        "calibrationMap": "NONE",
        "marketAnchoring": "NONE",
        "thresholdWeights": FROZEN_THRESHOLD_WEIGHTS,
    }, sort_keys=True))


def preregistration():
    return v3.MaterialityPreregistration(
        null_value=0.0,
        effect_floor=0.005,
        harm_tolerance=0.0,
        require_ci_excludes_null=True,
        min_score_improvement=0.005,
        min_independent_games=100,
        min_independent_dates=10,
        min_replicating_blocks=0,
        required_transport=v3.TRANSPORT_WALK_FORWARD,
        require_executable_capacity=True,
        min_executable_opportunities=25,
        subject_unit="team-game contract",
        justification=(
            "A team-total conversion change is worth making only if it improves the Brier score "
            "by at least 0.005 against CURRENT production and is not materially worse than Kalshi "
            "once standardised across thresholds, because a family priced against a sharp market "
            "cannot be carried by internal calibration alone. The 100-game floor matches the fixed "
            "KXMLBF5 floor so no family is held to a lower evidential bar, and clustering is by "
            "game because both team-total contracts in a game share one game state and are not "
            "independent observations. Transport is WALK_FORWARD_REPLICATION rather than "
            "chronological validation because this is a genuinely prospective shadow: every scored "
            "row is captured before its outcome exists."),
        notes=("Fixed at registration, before any post-registration outcome was scored. Dispersion "
               "%.6f is MLB-RSCH-0010's frozen value, imported, never estimated here. Threshold "
               "standardisation weights are frozen in source from the RSCH-0034 current-era "
               "reference corpus and are never recomputed from forward outcomes." % FROZEN_DISPERSION),
    )


def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0035_team_total_nb_shadow_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=candidate_fingerprint(),
        probability_adapter_identity="team_total_at_least_n_conversion",
        model_engine_family="team_total_nb_prospective_shadow_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived",
                                   "season_to_date_stats", "pitcher_snapshot"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("CONTROL is production's own v1.2 Poisson team-total conversion; CANDIDATE is "
                     "the same mean and the same AT_LEAST_N semantics with the frozen RSCH-0010 "
                     "negative binomial."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="TEAM_TOTAL_NB_V1 Current-Era Prospective Shadow",
        hypothesis=(
            "H1: for CURRENT v1.2 team-total contracts, the frozen negative-binomial conversion "
            "produces materially better probabilities than production's Poisson conversion, on "
            "prospectively captured rows. H2: after threshold standardisation it additionally "
            "carries incremental information beyond the Kalshi vig-free fair probability. H1 and "
            "H2 are separate; H1 passing does NOT establish H2, and neither promotes the "
            "candidate."),
        research_question=("Does the frozen NB conversion beat current production going forward, "
                           "and does anything survive against the market once thresholds are "
                           "standardised?"),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=("Every KXMLBTEAMTOTAL contract captured prospectively by the "
                           "production snapshot cycle on or after registration, whose ticker "
                           "parses and whose team carries a production projection."),
        market_families=["team_total"],
        eligibility_criteria=[
            "captured at a production prediction checkpoint STRICTLY AFTER registration",
            "canonical KXMLBTEAMTOTAL ticker parsing to (team, threshold N)",
            "a production teamProj for that team",
            "a settled full-game outcome for the contract",
        ],
        exclusion_criteria=[
            "any row captured before registration -- this is a forward experiment and "
            "retrospective rows may not be scored into it",
            "any dispersion fitted on forward data; 0.281513 is imported and frozen",
            "threshold-specific coefficients, calibration maps or market anchoring",
            "ROI or P&L as a fitting objective -- economics is a gate applied after scoring",
            "F5 team totals, a different series and a different period",
        ],
        prediction_checkpoints=["PRE_GAME_DECISION", "LINEUP_CONFIRMATION"],
        primary_metric="paired Brier of candidate vs control, clustered by independent game",
        secondary_metrics=[
            "paired log loss vs control and vs Kalshi",
            "raw pooled AND threshold-standardised candidate-minus-market effect",
            "calibration / ECE",
            "per-threshold, home/away, and per-date consistency",
            "executable fee-positive opportunity capacity",
        ],
        chronological_split_policy=(
            "Strictly prospective. Every scored row is captured before its outcome exists, so "
            "there is no train/validation split to draw and no retrospective row is admissible."),
        minimum_sample_requirement={"independentGames": 100, "independentDates": 10},
        clustering_unit=CLUSTERING_UNIT,
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_OTHER_DOCUMENTED,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "season_to_date_stats": "PREDICTIVE_INPUT",
            "pitcher_snapshot": "PREDICTIVE_INPUT",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "CONFIRMATORY: one frozen candidate, two preregistered hypotheses, no search over "
            "variants. falseDiscoveryHandling=OTHER_DOCUMENTED because the per-threshold strata "
            "are reported descriptively and never selected from -- the preregistered inference is "
            "the standardised aggregate, and a favourable stratum may not be promoted out of it. "
            "E4_PROSPECTIVE_SHADOW: rows are captured by the production snapshot cycle before "
            "outcomes exist. This experiment CANNOT promote the candidate; promotion requires all "
            "six criteria in PROMOTION_CRITERIA and a separate authorisation."),
    )
    reg.register_experiment(definition)
    return control, definition


# ── FORWARD SCORING ──────────────────────────────────────────────────────

def load_shadow_records(after_date=None):
    records = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "data", "edgelab", SHADOW_ENTITY, "*"))):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    if after_date:
        records = [r for r in records if (r.get("capturedAt") or "") >= after_date]
    return records


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def log_loss(rows, key, eps=1e-6):
    total = 0.0
    for r in rows:
        p = min(max(r[key], eps), 1 - eps)
        total += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return total / len(rows)


def ece(rows, key, bins=10):
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[min(int(r[key] * bins), bins - 1)].append(r)
    total = 0.0
    for block in buckets.values():
        conf = sum(r[key] for r in block) / len(block)
        acc = sum(r["outcome"] for r in block) / len(block)
        total += (len(block) / len(rows)) * abs(conf - acc)
    return total


def clustered_delta(rows, key_a, key_b, *, metric=brier, draws=2000, seed=20260830):
    by = collections.defaultdict(list)
    for r in rows:
        by[r[CLUSTERING_UNIT]].append(r)
    keys = list(by)
    rnd = random.Random(seed)
    deltas = []
    for _ in range(draws):
        sample = [x for k in (rnd.choice(keys) for _ in keys) for x in by[k]]
        deltas.append(metric(sample, key_a) - metric(sample, key_b))
    deltas.sort()
    lo, hi = deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))]
    return {"mean": round(sum(deltas) / len(deltas), 6), "ciLow": round(lo, 6),
            "ciHigh": round(hi, 6), "excludesNull": bool(lo * hi > 0)}


def threshold_standardized(rows, key_a, key_b):
    """Paired within-threshold effect under FROZEN weights.

    The weights come from FROZEN_THRESHOLD_WEIGHTS, fixed in source at
    registration. They are never derived from `rows`.
    """
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["threshold"]].append(r)
    contributions, used = [], 0
    for threshold in sorted(groups):
        block = groups[threshold]
        weight = FROZEN_THRESHOLD_WEIGHTS.get(threshold, 0)
        if len(block) < STRATUM_ROW_FLOOR or weight == 0:
            continue
        effect = brier(block, key_a) - brier(block, key_b)
        contributions.append({"threshold": threshold, "rows": len(block),
                              "frozenWeight": weight, "effect": round(effect, 6)})
        used += weight
    if not contributions:
        return {"status": "NO_STRATUM_MEETS_FLOOR",
                "weightSource": "FROZEN_THRESHOLD_WEIGHTS, fixed at registration"}
    return {
        "status": "COMPUTED",
        "comparison": "%s minus %s" % (key_a, key_b),
        "weightSource": "FROZEN_THRESHOLD_WEIGHTS, fixed at registration",
        "perThreshold": contributions,
        "standardizedEffect": round(
            sum(c["effect"] * c["frozenWeight"] for c in contributions) / used, 6),
    }


def checkpoint_for(games, dates):
    reached = CHECKPOINTS[0]
    for cp in CHECKPOINTS:
        if games >= cp["minGames"] and dates >= cp["minDates"]:
            reached = cp
    return reached


def status_for(checkpoint):
    return {
        "CHECKPOINT_0_HEALTH": "INSUFFICIENT_FORWARD_DATA",
        "CHECKPOINT_1_EARLY_DIRECTION": "ACCRUING_BELOW_MATERIAL_FLOOR",
        "CHECKPOINT_2_FIRST_MATERIAL": "MATERIAL_CHECK_AVAILABLE",
        "CHECKPOINT_3_STRONGER": "STRONGER_CHECK_AVAILABLE",
    }[checkpoint["name"]]


def score_forward(scored_rows):
    """Score post-registration rows that have settled outcomes."""
    games = len({r[CLUSTERING_UNIT] for r in scored_rows})
    dates = len({r["settleDate"] for r in scored_rows})
    cp = checkpoint_for(games, dates)
    out = {
        "rows": len(scored_rows),
        "independentGames": games,
        "independentDates": dates,
        "checkpoint": cp["name"],
        "checkpointLabel": cp["label"],
        "inferencePermitted": cp["inference"],
        "status": status_for(cp),
    }
    if not cp["inference"] or len(scored_rows) < STRATUM_ROW_FLOOR:
        out["note"] = ("Below the preregistered material floor (%d games / %d dates). Counts only; "
                       "no inference is drawn and none may be." % (
                           CHECKPOINTS[2]["minGames"], CHECKPOINTS[2]["minDates"]))
        return out
    out["candidateVsControl"] = {
        "brier": clustered_delta(scored_rows, "candidateProbability", "controlProbability"),
        "logLoss": clustered_delta(scored_rows, "candidateProbability", "controlProbability",
                                   metric=log_loss),
    }
    out["candidateVsMarket"] = {
        "rawPooledBrier": clustered_delta(scored_rows, "candidateProbability",
                                          "marketVigFreeProbability"),
        "rawPooledLogLoss": clustered_delta(scored_rows, "candidateProbability",
                                            "marketVigFreeProbability", metric=log_loss),
        "thresholdStandardized": threshold_standardized(scored_rows, "candidateProbability",
                                                        "marketVigFreeProbability"),
    }
    out["calibration"] = {
        "candidateEce": round(ece(scored_rows, "candidateProbability"), 6),
        "controlEce": round(ece(scored_rows, "controlProbability"), 6),
        "marketEce": round(ece(scored_rows, "marketVigFreeProbability"), 6),
    }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()

    control, definition = register_experiment()
    pre = preregistration()

    captured = load_shadow_records()
    forward = [r for r in captured if (r.get("capturedAt") or "") >= REGISTRATION_TIMESTAMP]
    computed = [r for r in forward if r.get("computationStatus") == "COMPUTED"]

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "TEAM_TOTAL_NB_V1 Current-Era Prospective Shadow",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "registeredAt": REGISTRATION_TIMESTAMP,
        "candidate": {
            "version": CANDIDATE_VERSION,
            "controlVersion": CONTROL_VERSION,
            "fingerprint": candidate_fingerprint(),
            "frozenDispersion": FROZEN_DISPERSION,
            "definition": {
                "mean": "production compute_projections teamProj, unmodified",
                "team": "exact ticker suffix team, matched against the event ticker",
                "threshold": "exact ticker suffix digit N",
                "semantics": "AT_LEAST_N -- YES iff team_runs >= N",
                "period": "FULL_GAME",
                "fitting": "NONE",
                "calibrationMap": "NONE",
                "marketAnchoring": "NONE",
                "outcomeUse": "NONE at registration",
            },
        },
        "persistence": {
            "entity": SHADOW_ENTITY,
            "path": "data/edgelab/%s/<date>.jsonl" % SHADOW_ENTITY,
            "writer": "scripts/edgelab/run_prospective_snapshots.py::run_team_total_nb_shadow_step",
            "reusesExistingE4Path": True,
        },
        "frozenThresholdWeights": {str(k): v for k, v in sorted(FROZEN_THRESHOLD_WEIGHTS.items())},
        "checkpoints": list(CHECKPOINTS),
        "forwardStatuses": list(FORWARD_STATUSES),
        "promotionCriteria": list(PROMOTION_CRITERIA),
        "capture": {
            "totalCapturedRows": len(captured),
            "postRegistrationRows": len(forward),
            "computedRows": len(computed),
            "failedIsolatedRows": len(forward) - len(computed),
            "capturedDates": sorted({(r.get("capturedAt") or "")[:10] for r in forward if r.get("capturedAt")}),
        },
        "forward": score_forward([]) if not computed else {
            "status": "INSUFFICIENT_FORWARD_DATA",
            "note": ("Rows are being captured, but forward scoring requires settled outcomes "
                     "joined to these captures. No post-registration row has settled yet."),
            "rows": len(computed),
            "independentGames": len({r.get("gameId") for r in computed}),
        },
        "methodologyV3": {"preregistration": v3.describe_v3(pre)},
        "cannotPromote": (
            "This experiment cannot promote the candidate under any result. Promotion requires "
            "all six criteria in promotionCriteria AND a separate explicit authorisation. No "
            "status in forwardStatuses is an approval token."),
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "candidateFingerprint": candidate_fingerprint(),
        "capturedRows": len(captured),
        "postRegistrationRows": len(forward),
        "forwardStatus": artifact["forward"]["status"],
    }, indent=2))
    return artifact


if __name__ == "__main__":
    main()
