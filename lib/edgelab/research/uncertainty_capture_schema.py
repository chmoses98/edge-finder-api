"""
lib/edgelab/research/uncertainty_capture_schema.py
=====================================
MLB-RSCH-0019 data-capture-audit deliverable: a RESEARCH-ONLY schema
for prospectively capturing the pregame uncertainty fields this
experiment's audit found MISSING from the current ModelEvaluation /
prospective-snapshot pipeline (see docs/EDGELAB_MLB_RSCH_0019_UNCERTAINTY_PREDICTION.md
section "Data-capture audit").

THIS MODULE IS NOT WIRED INTO PRODUCTION. Nothing in
lib.edgelab.prospective_snapshot, lib.edgelab.model_evaluation, or any
production entrypoint imports or calls anything here. It exists so a
FUTURE, separately-authorized milestone can wire it in without first
having to design the schema from scratch -- wiring it in is explicitly
NOT part of this experiment.

build_uncertainty_snapshot() is a pure function: given already-available
game/evaluation context (the SAME objects prospective_snapshot.py's own
evaluated_snapshots list already carries), it returns a plain dict of the
missing fields. It never mutates its inputs, never performs I/O, and
raising is impossible to observe from a caller that doesn't call it --
so even a hard bug here structurally cannot interrupt production, because
production never calls it.
"""

REQUIRED_FIELDS = (
    "gameId", "checkpoint", "capturedAt",
    "homeSampleDepth", "awaySampleDepth", "minSampleDepth",
    "homeBullpenSampleDepth", "awayBullpenSampleDepth", "minBullpenSampleDepth",
    "starterResolvedHome", "starterResolvedAway",
    "lineupConfirmedHome", "lineupConfirmedAway",
    "weatherDataAvailable", "mappingResolved",
    "inputStaleAgeMinutes", "unsupportedFeatureFallbackCount",
    "componentDisagreement", "probExtremeness",
)


def build_uncertainty_snapshot(
    *, game_id, checkpoint, captured_at,
    home_sample_depth, away_sample_depth,
    home_bullpen_sample_depth, away_bullpen_sample_depth,
    starter_resolved_home, starter_resolved_away,
    lineup_confirmed_home, lineup_confirmed_away,
    weather_data_available, mapping_resolved,
    input_stale_age_minutes, unsupported_feature_fallback_count,
    component_disagreement, prob_extremeness,
):
    """Pure. Builds one research-only uncertainty-capture record. Every
    argument must be supplied explicitly by the (future, not-yet-built)
    caller from data it already has at evaluation time -- this function
    never fetches or infers anything itself."""
    return {
        "gameId": game_id,
        "checkpoint": checkpoint,
        "capturedAt": captured_at,
        "homeSampleDepth": home_sample_depth,
        "awaySampleDepth": away_sample_depth,
        "minSampleDepth": min(home_sample_depth, away_sample_depth) if home_sample_depth is not None and away_sample_depth is not None else None,
        "homeBullpenSampleDepth": home_bullpen_sample_depth,
        "awayBullpenSampleDepth": away_bullpen_sample_depth,
        "minBullpenSampleDepth": min(home_bullpen_sample_depth, away_bullpen_sample_depth) if home_bullpen_sample_depth is not None and away_bullpen_sample_depth is not None else None,
        "starterResolvedHome": starter_resolved_home,
        "starterResolvedAway": starter_resolved_away,
        "lineupConfirmedHome": lineup_confirmed_home,
        "lineupConfirmedAway": lineup_confirmed_away,
        "weatherDataAvailable": weather_data_available,
        "mappingResolved": mapping_resolved,
        "inputStaleAgeMinutes": input_stale_age_minutes,
        "unsupportedFeatureFallbackCount": unsupported_feature_fallback_count,
        "componentDisagreement": component_disagreement,
        "probExtremeness": prob_extremeness,
    }


def validate_uncertainty_snapshot(record: dict) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in record]
    if missing:
        raise ValueError(f"Uncertainty snapshot missing required fields: {missing}")
