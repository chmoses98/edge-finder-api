"""
lib/edgelab/prospective_snapshot.py
========================================
EdgeLab Prospective Model Snapshots milestone: safely re-evaluate the
SAME production model (scripts.build_market_ledger.evaluate_game,
unmodified, imported directly -- the identical reuse pattern
lib/edgelab/replay.py already established) multiple times per pregame
window, so future research can pair a durable, causally-timestamped
model probability with the Kalshi price actually available at that same
moment -- closing the coverage gap
docs/EDGELAB_RESEARCH_TRUSTWORTHINESS.md documented (264 of 75,280
historical opportunity rows had a causally valid model evaluation,
because the production pipeline runs only once per day, in the
evening).

THIS MODULE NEVER:
  - writes data/slate.json, data/edgelab/recommendations/, or
    data/edgelab/bets/bets.jsonl
  - calls scripts/risk_gate.py or scripts/write_pending_bets.py
  - mutates bankroll or any canonical placed-bet record
  - fabricates a lineup, weather, or market state that wasn't actually
    observed
  - retroactively labels a later computation as an earlier prediction

It ONLY appends new, distinctly-timestamped ModelEvaluation records to
data/edgelab/model_evaluations/<date>.jsonl (via the existing
lib.edgelab.storage.append_records, idempotent, same as every other
EdgeLab writer) with artifactSource="prospective_snapshot" and an
explicit `checkpoint` label, so they are trivially distinguishable from
the once-daily pipeline-derived rows in every report.

DESIGN NOTE on WHY inputs are not fully re-fetched every checkpoint
(spec section 4's "cheapest safe way to re-evaluate"): evaluate_game()'s
own modelFairProbability computation (`modelProb`) depends on
projections (team/pitcher/bullpen/weather), NOT on the current Kalshi
price -- the Kalshi price only feeds evaluate_game()'s OWN
kalshiVF/marketProbVF/estimatedEdge fields, which this module does not
rely on for research purposes at all (lib.edgelab.research_dataset's
`contemporaneousEdge` already correctly re-derives edge against each
checkpoint's OWN contemporaneously-captured MarketObservation price,
independent of whatever price evaluate_game() itself saw -- see that
module's docstring). So this module re-runs evaluate_game() against the
day's already-fetched slate context UNCHANGED for T_MINUS_90/60/30 and
CLOSING checkpoints (cheap, CPU-only, no new paid/rate-limited API
calls -- see this milestone's audit: the-odds-api.com and Baseball
Savant are both metered/rate-limited, confirmed by a real documented
429-flood incident), and refreshes ONLY the lineup fields (a free MLB
Stats API call, the one input that genuinely changes in a
research-relevant way during the pregame window) for the
LINEUP_CONFIRMATION checkpoint specifically. This is a deliberate,
documented scope limitation, not an oversight -- weather/odds/bullpen
re-fetching may be added in a future milestone if the tradeoff proves
worthwhile.
"""

import copy
from datetime import datetime, timezone

from lib.edgelab import checkpoints as ckpt
from lib.edgelab import ids
from lib.edgelab.model_evaluation import (
    _git_commit_sha,
    _model_config_version,
    _ticker_lookup_from_observations,
    build_model_evaluation_records_for_games,
)

ARTIFACT_SOURCE = "prospective_snapshot"
MODEL_SOURCE = "scripts/build_market_ledger.py"

# Time-distance checkpoints this module targets by default -- T_MINUS_15/
# T_MINUS_5 are deliberately excluded from the default set (spec section
# 14: "If T_MINUS_15/T_MINUS_5 coverage is operationally expensive or
# unreliable, document the tradeoff rather than compromising the
# reliable core system") to keep the number of evaluate_game() calls
# per game per day bounded and predictable; a caller may still pass a
# wider `target_checkpoints` set explicitly.
TIME_TARGET_CHECKPOINTS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30")

# NAMING, DELIBERATELY NOT "CLOSING" (Prospective Model Snapshots
# reliability pass, spec section 7): lib.edgelab.checkpoints/
# lib.edgelab.research_dataset already use the bare label "CLOSING" for
# a DIFFERENT concept -- the canonical Kalshi closing QUOTE (the final
# valid pre-suspension/pre-start tradable MARKET price, selected by
# lib.edgelab.checkpoints.select_closing_quote). This module's
# MODEL_CLOSING_WINDOW checkpoint is NOT that -- it is only "the final
# targeted MODEL snapshot in the designated pregame closing window,"
# which may land at a different instant than the market's own closing
# quote. Reusing the bare "CLOSING" string for both would silently
# conflate two different things a reader could easily mistake for one
# timestamp; MODEL_CLOSING_WINDOW keeps them visibly distinct in every
# report. See docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md section 7.
MODEL_CLOSING_WINDOW = "MODEL_CLOSING_WINDOW"

CORE_CHECKPOINTS = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", MODEL_CLOSING_WINDOW)

# MODEL_CLOSING_WINDOW is attempted once minutesToStart falls into this
# window (still strictly pregame, > 0) -- deliberately wider than
# T_MINUS_5's own +/-7.5 min tolerance so a scheduler running every 15
# minutes reliably catches at least one tick in the window even on an
# unlucky cadence.
CLOSING_WINDOW_MINUTES = 12

# Exclusion / skip reasons -- always recorded, never a silent skip (spec
# section 10 / section 13's run-log requirement).
EXCLUDED_STARTED = "STARTED"
EXCLUDED_POSTPONED = "POSTPONED"
EXCLUDED_CANCELLED_OR_SUSPENDED = "CANCELLED_OR_SUSPENDED"
EXCLUDED_STATUS_AMBIGUOUS_BUT_PROCEEDING = "LIVE_STATUS_AMBIGUOUS_PROCEEDING_ON_CLOCK_TIME_ONLY"
EXCLUDED_MISSING_SCHEDULED_START = "MISSING_SCHEDULED_START"
SKIPPED_NO_CHECKPOINT_DUE = "NO_CHECKPOINT_DUE"
SKIPPED_ALREADY_CAPTURED = "ALREADY_CAPTURED_THIS_CHECKPOINT"

# Input-freshness notes (spec section 3) -- what this specific evaluation
# genuinely refreshed vs reused, never fabricated. Every prospective
# ModelEvaluation record carries exactly one of these two values (never
# a bare "fresh"/"stale" claim without saying what was actually
# refreshed).
INPUT_FRESHNESS_LINEUP_REFRESHED = "LINEUP_REFRESHED_LIVE_OTHER_INPUTS_PERSISTED_FROM_SLATE"
INPUT_FRESHNESS_ALL_PERSISTED = "ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH"
# Reserved for a future caller that genuinely cannot determine any input's
# age at all (spec section 3's "INPUT_TIMESTAMP_UNAVAILABLE or an
# equivalent explicit state") -- this module always knows which of the
# two notes above applies, so it never needs this value itself, but it
# is exported for other callers/tests that may.
INPUT_TIMESTAMP_UNAVAILABLE = "INPUT_TIMESTAMP_UNAVAILABLE"

# MLB Stats API detailedState values (see lib.edgelab.mlb_schedule) that
# mean this game is no longer in a pregame-safe-to-evaluate state.
_LIVE_STATUS_STARTED = frozenset({
    "In Progress", "Live", "Manager Challenge", "Umpire Review", "Final", "Game Over", "Completed Early",
})
_LIVE_STATUS_POSTPONED = frozenset({"Postponed"})
_LIVE_STATUS_CANCELLED = frozenset({"Cancelled", "Suspended", "Suspended: Rain"})


def _parse_iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minutes_to_start(now, scheduled_start):
    now_dt, sched_dt = _parse_iso(now), _parse_iso(scheduled_start)
    if now_dt is None or sched_dt is None:
        return None
    return (sched_dt - now_dt).total_seconds() / 60.0


def classify_game_eligibility(game, *, now, live_status=None):
    """
    Pure. Returns (eligible: bool, reason_or_None, minutes_to_start).
    Two independent signals, neither trusted alone (spec section 10:
    "Do not trust a stale Kalshi status flag by itself"):
      1. lib.edgelab.checkpoints.classify_checkpoint against the FRESH
         `now` and this game's own scheduledStart -- always accurate
         regardless of when any other field was last fetched, since
         `now` is never stale by construction. Returns POST_START ->
         excluded, regardless of what `live_status` says.
      2. `live_status` (a FRESH, this-run MLB Stats API detailedState
         string, e.g. from lib.edgelab.mlb_schedule.fetch_schedule --
         never a value cached from the morning's slate fetch) -- catches
         Postponed/Cancelled/Suspended, which pure clock-time can't.
         When `live_status` is None (schedule fetch failed, or this
         game's team-pair wasn't uniquely resolvable, e.g. a
         doubleheader) this function does NOT hard-exclude the game --
         a single flaky network call must never silently blank out a
         whole day's coverage -- but the eligibility reason records that
         the live-status corroboration was unavailable.
    A delayed game whose clock-time has passed scheduledStart but is NOT
    yet actually live is conservatively excluded (POST_START) -- this
    module treats "possibly still pregame but ambiguous" as "skip", per
    spec section 10's "never evaluate a started game" being the
    paramount safety property (a missed evaluation is recoverable next
    cycle; a POST_START leak is not).
    """
    scheduled_start = game.get("startTime") or game.get("scheduledStart")
    if not scheduled_start:
        return False, EXCLUDED_MISSING_SCHEDULED_START, None

    minutes_to_start = _minutes_to_start(now, scheduled_start)
    checkpoint_now = ckpt.classify_checkpoint(now, scheduled_start)
    if checkpoint_now == "POST_START":
        return False, EXCLUDED_STARTED, minutes_to_start

    if live_status in _LIVE_STATUS_STARTED:
        return False, EXCLUDED_STARTED, minutes_to_start
    if live_status in _LIVE_STATUS_POSTPONED:
        return False, EXCLUDED_POSTPONED, minutes_to_start
    if live_status in _LIVE_STATUS_CANCELLED:
        return False, EXCLUDED_CANCELLED_OR_SUSPENDED, minutes_to_start
    if live_status is None:
        return True, EXCLUDED_STATUS_AMBIGUOUS_BUT_PROCEEDING, minutes_to_start
    return True, None, minutes_to_start


def _is_lineup_confirmed(game):
    away_ts = game.get("awayTeamStats") or {}
    home_ts = game.get("homeTeamStats") or {}
    return bool(away_ts.get("lineupConfirmedOfficial")) and bool(home_ts.get("lineupConfirmedOfficial"))


def determine_due_checkpoint(game, *, now, already_captured, target_checkpoints=CORE_CHECKPOINTS):
    """
    Pure. Returns (checkpoint_label_or_None, minutes_to_start). Never
    returns a checkpoint already present in `already_captured` (a set of
    checkpoint labels already captured for this game today) -- at-most-
    once per game/checkpoint (spec section 8 step 5), except
    LINEUP_CONFIRMATION is naturally re-evaluated at most once too (it
    is only ever "due" the first time lineups are observed confirmed).

    IMPORTANT (reliability pass): `_is_lineup_confirmed(game)` reads
    WHATEVER lineup state is already on `game` -- it does NOT itself
    perform a live poll. Callers MUST pass the LINEUP-REFRESHED game copy
    here (see run_prospective_snapshot_cycle, which polls live via
    refresh_lineup_fields() BEFORE calling this function whenever
    LINEUP_CONFIRMATION hasn't been captured yet) -- passing the raw,
    possibly-hours-stale data/slate.json game object would mean this
    function could never discover a lineup that became official since
    the slate was last fetched, since nothing else in this pipeline ever
    rewrites that field. This was a real, confirmed bug in the first cut
    of this milestone: the live poll was previously gated BEHIND this
    function's own (stale) eligibility check, so it never ran until the
    stale state already happened to say "confirmed" -- which could only
    ever come from some unrelated production process re-fetching
    data/slate.json, not from this module's own polling.

    Priority: LINEUP_CONFIRMATION (a genuine state change, evaluated the
    moment it's first observed, regardless of clock-time proximity to
    any T_MINUS_X target) > MODEL_CLOSING_WINDOW (takes priority over a
    coincidentally-overlapping T_MINUS_30 target) > time-distance
    checkpoints, using lib.edgelab.checkpoints.classify_checkpoint's own
    nearest-target classification -- never a second, competing
    time-bucketing scheme.
    """
    scheduled_start = game.get("startTime") or game.get("scheduledStart")
    minutes_to_start = _minutes_to_start(now, scheduled_start)

    if "LINEUP_CONFIRMATION" in target_checkpoints and "LINEUP_CONFIRMATION" not in already_captured:
        if _is_lineup_confirmed(game):
            return "LINEUP_CONFIRMATION", minutes_to_start

    if MODEL_CLOSING_WINDOW in target_checkpoints and MODEL_CLOSING_WINDOW not in already_captured:
        if minutes_to_start is not None and 0 < minutes_to_start <= CLOSING_WINDOW_MINUTES:
            return MODEL_CLOSING_WINDOW, minutes_to_start

    if scheduled_start:
        label = ckpt.classify_checkpoint(now, scheduled_start)
        if label in target_checkpoints and label not in already_captured:
            return label, minutes_to_start

    return None, minutes_to_start


def already_captured_checkpoints(evaluations, game_id):
    """{checkpoint labels already captured for this game} from prior prospective-snapshot ModelEvaluation rows (any earlier run today)."""
    return {
        e.get("checkpoint")
        for e in evaluations
        if e.get("gameId") == game_id and e.get("artifactSource") == ARTIFACT_SOURCE and e.get("checkpoint")
    }


def refresh_lineup_fields(game, *, lineup_fetch_fn, batter_woba_map, team_woba_map):
    """
    Returns (new_game_copy, warning_or_None) -- NEVER mutates `game`.
    `lineup_fetch_fn(game_pk, away_abbr, home_abbr, batter_woba_map,
    team_woba_map)` is injected (production passes
    scripts.fetch_lineups.fetch_lineup_for_game; tests pass a fake) so
    this function has no network dependency of its own. On ANY failure
    (missing gamePk/abbr, fetch exception, no result), returns the
    ORIGINAL game's lineup fields completely unchanged plus a warning --
    never fabricates a lineup state.
    """
    from scripts.fetch_lineups import compute_game_lineup_stats_fields

    g = copy.deepcopy(game)
    game_pk = g.get("gameId")
    away_abbr = (g.get("away") or {}).get("abbr")
    home_abbr = (g.get("home") or {}).get("abbr")
    if not (game_pk and away_abbr and home_abbr):
        return g, f"lineup refresh skipped for gameId={game_pk!r} -- missing gamePk/team abbreviations"

    try:
        lineup_result = lineup_fetch_fn(game_pk, away_abbr, home_abbr, batter_woba_map, team_woba_map)
    except Exception as exc:  # a live network call must never crash the run -- see module docstring
        return g, f"lineup refresh failed for gameId={game_pk!r}: {exc}"

    if lineup_result is None:
        return g, f"lineup refresh returned no data for gameId={game_pk!r} -- keeping existing lineup state"

    away_ts, home_ts = compute_game_lineup_stats_fields(g, lineup_result)
    g["awayTeamStats"] = away_ts
    g["homeTeamStats"] = home_ts
    return g, None


def evaluate_game_at_checkpoint(game, checkpoint, *, evaluate_game_fn, compute_projection_context_fn):
    """
    Runs the SAME production functions
    scripts.build_market_ledger.compute_game_projection_context /
    evaluate_game (injected as evaluate_game_fn/compute_projection_context_fn
    -- production callers pass those exact functions; this indirection
    exists purely for test isolation, never a second implementation)
    against `game` (already the correct in-memory context for this
    checkpoint -- lineup-refreshed for LINEUP_CONFIRMATION, unchanged
    otherwise) and returns the resulting marketLedger rows. `game` is
    never mutated or written back anywhere.
    """
    projection_context = compute_projection_context_fn(game)
    rows = evaluate_game_fn(game, projection_context)
    game_with_ledger = dict(game, marketLedger=rows)
    return game_with_ledger


def run_prospective_snapshot_cycle(
    date, games, existing_evaluations, observations, *,
    now=None, target_checkpoints=CORE_CHECKPOINTS, live_status_by_team_pair=None,
    evaluate_game_fn=None, compute_projection_context_fn=None,
    lineup_fetch_fn=None, batter_woba_map=None, team_woba_map=None,
    run_id=None,
):
    """
    The orchestration core -- pure aside from the injected
    evaluate_game_fn/compute_projection_context_fn/lineup_fetch_fn
    (production always passes the real
    scripts.build_market_ledger/scripts.fetch_lineups functions; tests
    inject fakes so this function never needs real network access or a
    real data/slate.json to exercise).

    For every game in `games` (already the day's base slate context,
    read-only): determine eligibility (classify_game_eligibility), then
    the due checkpoint (determine_due_checkpoint) given what
    `existing_evaluations` already shows was captured today, evaluate at
    most ONE checkpoint per game per call, and build ModelEvaluation
    records (build_model_evaluation_records_for_games, reused verbatim,
    never reimplemented).

    Returns (new_records, run_log, evaluated_snapshots): `new_records` is
    the list of new ModelEvaluation dicts to append (empty if nothing was
    due this cycle); `run_log` is one entry per game -- {"gameId",
    "action": "EVALUATED"|"SKIPPED", "checkpoint", "reason",
    "minutesToStart", "warnings"} -- a complete accounting of every
    decision this cycle made, never a silent no-op. `evaluated_snapshots`
    (MLB-RSCH-0011 addition) is one {"gameId", "checkpoint", "game"} entry
    per EVALUATED game this cycle, where "game" is the EXACT game object
    (lineup-refreshed copy for LINEUP_CONFIRMATION, the original object
    otherwise) that evaluate_game_fn/compute_projection_context_fn were
    actually called against -- so a caller needing the SAME production
    inputs for a research purpose (e.g. lib.edgelab.shadow_distribution's
    paired-probability shadow) can recompute compute_projection_context_fn
    against the identical object rather than risking any drift from what
    production itself evaluated. This is purely an additive accounting
    list -- it changes nothing about new_records/run_log's own content.
    """
    now = now or ids.utc_now_iso()
    run_id = run_id or ids.new_run_id("PROSPECTIVE_SNAPSHOT")
    live_status_by_team_pair = live_status_by_team_pair or {}
    ticker_lookup = _ticker_lookup_from_observations(observations)
    commit_sha = _git_commit_sha()
    config_version = _model_config_version()

    new_records = []
    run_log = []
    evaluated_snapshots = []

    for game in games:
        game_id = game.get("gameId")
        away_abbr = (game.get("away") or {}).get("abbr")
        home_abbr = (game.get("home") or {}).get("abbr")
        live_status = live_status_by_team_pair.get((away_abbr, home_abbr))

        eligible, exclusion_reason, minutes_to_start = classify_game_eligibility(game, now=now, live_status=live_status)
        if not eligible:
            run_log.append({
                "gameId": game_id, "action": "SKIPPED", "checkpoint": None,
                "reason": exclusion_reason, "minutesToStart": minutes_to_start, "warnings": [],
                "lineupPollAttempted": False, "lineupPollFailed": False, "lineupNewlyConfirmed": False,
            })
            continue

        captured = already_captured_checkpoints(existing_evaluations, game_id)
        warnings = []

        # LINEUP DISCOVERY FIX (reliability pass): poll live BEFORE deciding
        # the due checkpoint, whenever LINEUP_CONFIRMATION hasn't been
        # captured yet -- otherwise a stale data/slate.json that still
        # says "unconfirmed" could never be discovered as newly confirmed
        # (nothing else in this pipeline ever rewrites that field). The
        # poll result is used ONLY to decide/evaluate LINEUP_CONFIRMATION;
        # every other checkpoint this cycle still evaluates against the
        # untouched, original `game` object (never the refreshed copy),
        # so a still-open lineup poll can never leak into a T_MINUS_X/
        # MODEL_CLOSING_WINDOW snapshot for this same game.
        lineup_poll_attempted = False
        lineup_poll_failed = False
        lineup_refreshed_game = game
        if (
            "LINEUP_CONFIRMATION" in target_checkpoints
            and "LINEUP_CONFIRMATION" not in captured
            and lineup_fetch_fn is not None
        ):
            lineup_poll_attempted = True
            lineup_refreshed_game, lineup_warning = refresh_lineup_fields(
                game, lineup_fetch_fn=lineup_fetch_fn, batter_woba_map=batter_woba_map or {}, team_woba_map=team_woba_map or {},
            )
            if lineup_warning:
                lineup_poll_failed = True
                warnings.append(lineup_warning)
        was_confirmed_before_poll = _is_lineup_confirmed(game)
        lineup_newly_confirmed = (not was_confirmed_before_poll) and _is_lineup_confirmed(lineup_refreshed_game)

        checkpoint, minutes_to_start = determine_due_checkpoint(
            lineup_refreshed_game, now=now, already_captured=captured, target_checkpoints=target_checkpoints,
        )
        if checkpoint is None:
            run_log.append({
                "gameId": game_id, "action": "SKIPPED", "checkpoint": None,
                "reason": SKIPPED_NO_CHECKPOINT_DUE, "minutesToStart": minutes_to_start, "warnings": warnings,
                "lineupPollAttempted": lineup_poll_attempted, "lineupPollFailed": lineup_poll_failed,
                "lineupNewlyConfirmed": lineup_newly_confirmed,
            })
            continue

        # Only the LINEUP_CONFIRMATION checkpoint itself is evaluated
        # against the lineup-refreshed copy -- every other checkpoint uses
        # the original, untouched `game` (see docstring above).
        eval_game = lineup_refreshed_game if checkpoint == "LINEUP_CONFIRMATION" else game
        input_freshness_note = (
            INPUT_FRESHNESS_LINEUP_REFRESHED if checkpoint == "LINEUP_CONFIRMATION"
            else INPUT_FRESHNESS_ALL_PERSISTED
        )

        try:
            game_with_ledger = evaluate_game_at_checkpoint(
                eval_game, checkpoint, evaluate_game_fn=evaluate_game_fn, compute_projection_context_fn=compute_projection_context_fn,
            )
        except Exception as exc:  # one malformed game's inputs must never abort the whole cycle
            run_log.append({
                "gameId": game_id, "action": "SKIPPED", "checkpoint": checkpoint,
                "reason": f"evaluate_game raised: {exc}", "minutesToStart": minutes_to_start, "warnings": warnings,
                "lineupPollAttempted": lineup_poll_attempted, "lineupPollFailed": lineup_poll_failed,
                "lineupNewlyConfirmed": lineup_newly_confirmed,
            })
            continue

        records = build_model_evaluation_records_for_games(
            [game_with_ledger], source_run_key=now, run_id=run_id, model_source=MODEL_SOURCE,
            artifact_source=ARTIFACT_SOURCE, ticker_lookup=ticker_lookup, commit_sha=commit_sha,
            config_version=config_version, source_system=ARTIFACT_SOURCE,
            source_file=None, assign_recommendation_id=False, checkpoint=checkpoint,
            input_freshness_note=input_freshness_note,
        )
        new_records.extend(records)
        evaluated_snapshots.append({"gameId": game_id, "checkpoint": checkpoint, "game": eval_game})
        run_log.append({
            "gameId": game_id, "action": "EVALUATED", "checkpoint": checkpoint,
            "reason": None, "minutesToStart": minutes_to_start, "warnings": warnings,
            "recordsWritten": len(records), "lineupPollAttempted": lineup_poll_attempted,
            "lineupPollFailed": lineup_poll_failed, "lineupNewlyConfirmed": lineup_newly_confirmed,
        })

    return new_records, run_log, evaluated_snapshots
