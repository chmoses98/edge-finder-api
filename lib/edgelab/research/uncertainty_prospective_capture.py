"""
lib/edgelab/research/uncertainty_prospective_capture.py
=====================================
Builds MLB-RSCH-0019 prospective uncertainty-capture records from a
prospective-snapshot cycle's own `evaluated_snapshots` list. DATA
COLLECTION ONLY -- reuses `lib.edgelab.research.uncertainty_capture_schema`
(the RSCH-0019 schema, unchanged) to shape each record, and NEVER calls
any production recommendation/edge/staking/confidence function.

Follows the EXACT isolation pattern MLB-RSCH-0011's own
lib.edgelab.shadow_distribution.build_shadow_records_for_snapshot_cycle
already established: pure, one try/except PER GAME (a single game's bad
or missing data produces a FAILED_ISOLATED record, never aborts the
cycle), never mutates its inputs, never performs I/O itself.

Every field is captured from data production ALREADY computed this same
cycle -- `compute_projection_context_fn` is the SAME pure, deterministic
production function the core cycle and the MLB-RSCH-0011 shadow step
both already call against the SAME game object, so calling it once more
here is cheap and produces byte-identical output, never a new/different
computation. No production confidence, qualification, edge, staking, or
recommendation function is ever imported or called from this module.
"""
from lib.edgelab import ids
from lib.edgelab.research.uncertainty_capture_schema import build_uncertainty_snapshot, validate_uncertainty_snapshot

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED_ISOLATED = "FAILED_ISOLATED"

# Explicit missingness vocabulary (mission requirement) -- one status per
# captured field, alongside the field's own value (which is None whenever
# its status is not AVAILABLE -- never a fabricated placeholder).
STATUS_AVAILABLE = "AVAILABLE"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
STATUS_NOT_COMPUTED = "NOT_COMPUTED"
STATUS_UNRESOLVED = "UNRESOLVED"

TOTAL_EXTREMENESS_REFERENCE = 8.5  # a fixed, disclosed anchor (one of this program's own standard game-total lines) -- never refit


def _team_side(game, side):
    return game.get(side) or {}


def _sample_depth_fields(game):
    """{home,away} x {offense games played, bullpen IP} -- pulled directly
    from the game object's OWN already-assembled team-stats/bullpen dicts,
    zero recomputation."""
    out = {}
    for side in ("home", "away"):
        team_stats = game.get(f"{side}TeamStats") or {}
        bullpen = _team_side(game, side).get("bullpen") or {}
        out[side] = {
            "offenseGamesPlayed": team_stats.get("gamesPlayed"),
            "bullpenIp": bullpen.get("ip"),
            "bullpenHighLeverageSamplePA": bullpen.get("hlSamplePA"),
            "bullpenRecentUsageDataAvailable": (bullpen.get("recentUsage") or {}).get("dataAvailable"),
            "bullpenXfipMethod": bullpen.get("xFIPMethod"),
        }
    return out


def _starter_resolved(game, side):
    pitcher = _team_side(game, side).get("pitcher") or {}
    return pitcher.get("id") is not None


def _starter_sample_depth(game, side):
    savant = _team_side(game, side).get("pitcherSavant") or {}
    return savant.get("startsSampled") if savant.get("startsSampled") is not None else savant.get("seasonStarts")


def _lineup_confirmed(game, side):
    team_stats = game.get(f"{side}TeamStats") or {}
    return team_stats.get("lineupConfirmed")


def _stale_age_minutes(game, now_iso):
    checked_at = game.get("lineupCheckedAt")
    if not checked_at or not now_iso:
        return None
    try:
        from datetime import datetime
        checked_dt = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        now_dt = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
        return round((now_dt - checked_dt).total_seconds() / 60.0, 1)
    except (ValueError, TypeError):
        return None


def build_uncertainty_capture_records_for_snapshot_cycle(evaluated_snapshots, *, compute_projection_context_fn, run_id, now=None):
    """
    Returns (records, failures) -- one record per `evaluated_snapshots`
    entry regardless of success/failure, `failures` a short list of
    {"gameId", "checkpoint", "reason"} for run-log/monitoring. NEVER
    raises -- an unexpected error for one game becomes that game's own
    FAILED_ISOLATED record, not an aborted cycle.
    """
    if now is None:
        now = ids.utc_now_iso()
    records = []
    failures = []

    for entry in evaluated_snapshots:
        game_id, checkpoint, game = entry.get("gameId"), entry.get("checkpoint"), entry.get("game")
        base = {
            "uncertaintySnapshotId": ids.build_uncertainty_capture_snapshot_id(run_id, str(game_id), str(checkpoint)),
            "experimentId": "MLB-RSCH-0019",
            "runId": run_id,
            "capturedAt": now,
        }
        try:
            ctx = compute_projection_context_fn(game) if game is not None else None
            missing_fields = (ctx or {}).get("missingFields") or []
            model_prob = game.get("modelProb") if game else None
            total_proj = (ctx or {}).get("totalProj")

            sample_depth = _sample_depth_fields(game or {})
            snapshot = build_uncertainty_snapshot(
                game_id=game_id, checkpoint=checkpoint, captured_at=now,
                home_sample_depth=sample_depth["home"]["offenseGamesPlayed"],
                away_sample_depth=sample_depth["away"]["offenseGamesPlayed"],
                home_bullpen_sample_depth=sample_depth["home"]["bullpenIp"],
                away_bullpen_sample_depth=sample_depth["away"]["bullpenIp"],
                starter_resolved_home=_starter_resolved(game or {}, "home"),
                starter_resolved_away=_starter_resolved(game or {}, "away"),
                lineup_confirmed_home=_lineup_confirmed(game or {}, "home"),
                lineup_confirmed_away=_lineup_confirmed(game or {}, "away"),
                weather_data_available=None,  # NOT_COMPUTED -- no weather field currently on the game object (see field statuses)
                mapping_resolved=bool((game or {}).get("kalshiKey")),
                input_stale_age_minutes=_stale_age_minutes(game or {}, now),
                unsupported_feature_fallback_count=len(missing_fields),
                component_disagreement=None,  # NOT_COMPUTED -- see field statuses / module docstring's own disclosed scope decision
                prob_extremeness=round(abs(model_prob - 0.5), 4) if model_prob is not None else None,
            )
            validate_uncertainty_snapshot(snapshot)
            field_statuses = {
                "weatherDataAvailable": STATUS_NOT_COMPUTED,
                "componentDisagreement": STATUS_NOT_COMPUTED,
                "probExtremeness": STATUS_AVAILABLE if model_prob is not None else STATUS_NOT_COMPUTED,
                "starterResolvedHome": STATUS_AVAILABLE, "starterResolvedAway": STATUS_AVAILABLE,
                "lineupConfirmedHome": STATUS_AVAILABLE if sample_depth["home"] else STATUS_UNRESOLVED,
                "lineupConfirmedAway": STATUS_AVAILABLE if sample_depth["away"] else STATUS_UNRESOLVED,
                "homeSampleDepth": STATUS_AVAILABLE if snapshot["homeSampleDepth"] is not None else STATUS_UNRESOLVED,
                "awaySampleDepth": STATUS_AVAILABLE if snapshot["awaySampleDepth"] is not None else STATUS_UNRESOLVED,
                "totalExtremeness": STATUS_AVAILABLE if total_proj is not None else STATUS_NOT_COMPUTED,
            }
            total_extremeness = round(abs(total_proj - TOTAL_EXTREMENESS_REFERENCE), 4) if total_proj is not None else None
            records.append(dict(
                base, computationStatus=STATUS_SUCCESS, snapshot=snapshot,
                totalExtremeness=total_extremeness, fieldStatuses=field_statuses, failureReason=None,
            ))
        except Exception as exc:  # one game's failure must never abort the cycle or affect any other game's record
            failures.append({"gameId": game_id, "checkpoint": checkpoint, "reason": str(exc)})
            records.append(dict(base, computationStatus=STATUS_FAILED_ISOLATED, snapshot=None, totalExtremeness=None, fieldStatuses=None, failureReason=str(exc)))

    return records, failures
