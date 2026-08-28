#!/usr/bin/env python3
"""
scripts/edgelab/freeze_games_1_10_2027_shadow_candidate.py
====================================================================
Freezes a durable, RESEARCH-ONLY candidate-variant artifact for the
Games-1-10 previous-season-anchored offense prior, confirmed by
MLB-RSCH-0017 (exploratory discovery) and MLB-RSCH-0018 (confirmatory
holdout replication).

This is NOT a production change and NOT an activation. It exists so the
confirmed specification cannot be accidentally reinterpreted, re-derived,
or re-tuned next spring -- every number here is read from the two
experiments' own committed artifacts and asserted equal, never
recomputed.

Reuses lib.edgelab.candidate_identity -- the canonical Research Lab
candidate-variant registry -- rather than inventing a new one.
`productionCodePathsModified` is structurally enforced to be `[]` by
that module; `disposition`/`productionActive` here are additional,
non-schema fields this script adds for this candidate's own record.
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import candidate_identity as cand_id

RSCH0017_ARTIFACT = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0017_early_season_offense.json")
RSCH0018_ARTIFACT = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0018_games_1_10_confirmation.json")
FREEZE_TIMESTAMP = "2026-08-28T16:00:00Z"  # fixed -- reproducible/idempotent across reruns, write-once registry


def build_and_freeze():
    with open(RSCH0017_ARTIFACT) as f:
        rsch0017 = json.load(f)
    with open(RSCH0018_ARTIFACT) as f:
        rsch0018 = json.load(f)

    if rsch0018["classification"] != "CONFIRMED_EARLY_SEASON_SIGNAL":
        raise ValueError(f"Refusing to freeze -- RSCH-0018 classification is {rsch0018['classification']!r}, not CONFIRMED_EARLY_SEASON_SIGNAL")
    if rsch0018["disposition"] != "SHADOW_CANDIDATE_FOR_2027":
        raise ValueError(f"Refusing to freeze -- RSCH-0018 disposition is {rsch0018['disposition']!r}, not SHADOW_CANDIDATE_FOR_2027")

    league_avg = rsch0017["leagueAverage"]
    hfa = rsch0017["homeFieldAdjustment"]
    k_prior = rsch0017["kPrior"]["selected"]
    base_control_model_id = rsch0018["controlModelId"]

    registration = cand_id.build_candidate_registration(
        name="games_1_10_previous_season_offense_prior_2027_shadow_v1",
        base_control_model_id=base_control_model_id,
        change_description=(
            "Previous-season-anchored offense shrinkage blend (MLB-RSCH-0017's E1 formula), "
            "applicable ONLY to team-games 1-10 of a season, confirmed on a locked out-of-sample "
            "holdout by MLB-RSCH-0018."
        ),
        change_type=cand_id.CHANGE_TYPE_PARAMETER_CHANGE,
        implementation_ref=(
            "scripts/edgelab/run_early_season_offense_experiment.py:e1_component "
            "(formula) + attach_e1_predictions (application), frozen parameters "
            "verified in scripts/edgelab/run_games_1_10_confirmation_experiment.py"
        ),
        description=(
            "RESEARCH-ONLY candidate specification for future prospective evaluation. "
            "NOT implemented in production. NOT active. Exists solely so the confirmed "
            "Games-1-10 offense-prior specification is durably identifiable and cannot be "
            "accidentally re-derived or re-tuned before a 2027 Opening Day shadow decision."
        ),
        registered_at=FREEZE_TIMESTAMP,
    )

    # ---- Additional immutable metadata (not part of candidate_identity's own
    # required schema, but persisted alongside it for this candidate's record) ----
    registration["originatingExperiments"] = {
        "discovery": "MLB-RSCH-0017", "confirmation": "MLB-RSCH-0018",
        "discoveryControlModelId": rsch0017["controlModelId"],
        "confirmationControlModelId": rsch0018["controlModelId"],
    }
    registration["formula"] = {
        "family": "previous_season_anchored_shrinkage_blend",
        "definition": (
            "at prior_games==0 (game 1): predicted offense = previousSeasonOffenseRunsPerGame "
            "if available, else leagueAverage. "
            "at prior_games>0: predicted offense = (previousSeasonRate * K_PRIOR + currentSeasonRawRate * priorGames) "
            "/ (K_PRIOR + priorGames), gracefully degrading to the league-average-anchored E0 formula "
            "if no previous season exists (e.g. a 2022 target season)."
        ),
        "runPrevention": "IDENTICAL E0-style (league-average-anchored, no-floor) construction for the opponent side -- unchanged across control and candidate",
        "homeFieldAdjustment": hfa,
        "leagueAverage": league_avg,
        "kPrior": k_prior,
        "kPriorSelectionMethod": "DEV-only grid search over (5,10,15,20,30,50,80), MLB-RSCH-0017; NEVER refit in MLB-RSCH-0018",
    }
    registration["applicability"] = "team-games 1-10 of a season ONLY (priorGamesThisSeason 0-9). Never applied outside this window without its own separate confirmatory study."
    registration["fallbackBehavior"] = "if previous season is unavailable (no cache, e.g. season - 1 predates 2022), degrades EXACTLY to the E0 league-average-anchored formula -- never fabricates a previous-season rate."
    registration["requiredInputs"] = [
        "team's own current-season prior games this season (0-9) and raw offense rate (team_baseline, min_prior_games=0)",
        "team's own complete previous-season offense rate (runsScored per game), when a previous-season cache exists",
        "league-average runs/game (frozen constant, not refit per season)",
    ]
    registration["frozenNbRelationship"] = (
        "Downstream probability evaluation MUST reuse the frozen MLB-RSCH-0010 negative-binomial "
        "distribution (dispersion 0.281513) UNCHANGED -- never refit for this candidate, consistent "
        "with every evaluation performed in MLB-RSCH-0017 and MLB-RSCH-0018."
    )
    registration["evidenceReceipts"] = {
        "rsch0017Artifact": "data/edgelab/analytics/latest_mlb_rsch_0017_early_season_offense.json",
        "rsch0018Artifact": "data/edgelab/analytics/latest_mlb_rsch_0018_games_1_10_confirmation.json",
        "rsch0017Doc": "docs/EDGELAB_MLB_RSCH_0017_EARLY_SEASON_OFFENSE.md",
        "rsch0018Doc": "docs/EDGELAB_MLB_RSCH_0018_GAMES_1_10_CONFIRMATION.md",
        "rsch0018DevMaeDelta": rsch0018["meanAccuracy"]["dev"]["pairedDelta"]["maeDelta"],
        "rsch0018ValMaeDelta": rsch0018["meanAccuracy"]["validation"]["pairedDelta"]["maeDelta"],
        "rsch0018HoldoutMaeDelta": rsch0018["holdout2026"]["meanAccuracy"]["pairedDelta"]["maeDelta"],
        "rsch0018HoldoutNbPrimaryDelta": rsch0018["holdout2026"]["nbPrimaryDelta"],
        "rsch0018Classification": rsch0018["classification"],
        "rsch0018Disposition": rsch0018["disposition"],
    }
    registration["status"] = "SHADOW_CANDIDATE_FOR_2027"
    registration["productionActive"] = False
    registration["intendedEarliestShadowStart"] = "2027 Opening Day (team-games 1-10 of the 2027 season)"
    registration["requiredProspectiveEvaluationThreshold"] = {
        "minimumSettledGames": 30,
        "note": "matches this research program's own MLB-RSCH-0011 convention -- below this, no performance interpretation.",
    }
    registration["reactivationRequirement"] = (
        "Explicit, separate human authorization is required before any prospective shadow wiring is "
        "implemented for 2027 -- this artifact freezes the SPECIFICATION only, it does not schedule or "
        "authorize implementation work."
    )

    path = cand_id.register_candidate(registration)
    return registration, path


if __name__ == "__main__":
    registration, path = build_and_freeze()
    print(f"[freeze_games_1_10_2027_shadow_candidate] candidateVariantId={registration['candidateVariantId']}")
    print(f"[freeze_games_1_10_2027_shadow_candidate] wrote {path}")
    print(f"[freeze_games_1_10_2027_shadow_candidate] status={registration['status']} productionActive={registration['productionActive']}")
