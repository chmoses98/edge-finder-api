#!/usr/bin/env python3
"""
scripts/edgelab/backfill_scheduled_start.py
================================================
CLI entry point: scheduledStart/CLV metadata fix, requirement 8 -- a
one-time historical catch-up for a date whose Game/MarketObservation rows
were already ingested with scheduledStart(Time)=null (a standalone/
manual-research day, or a slate day ingested before this fix existed).

Touches TWO already-committed entities for one date:
  - data/edgelab/games/<date>.jsonl: Game.scheduledStartTime, via
    lib.edgelab.market_universe.backfill_missing_scheduled_start. Going
    forward this same pass also runs automatically inside
    scripts/edgelab/ingest_market_observations.py and
    scripts/edgelab/repair_game_identity.py -- this script exists for the
    one-time historical catch-up, and for the Observation-side backfill
    below, which neither of those touches.
  - data/edgelab/observations/<date>.jsonl.gz: MarketObservation.scheduledStart
    plus the four fields derived from it at original ingest time
    (checkpoint/gameStartedAtCapture/isValidPregameObservation/
    isClosingCandidate), recomputed via lib.edgelab.market_universe.
    compute_observation_temporal_fields -- the SAME pure function
    build_observations_from_snapshot itself calls, so a backfilled row is
    indistinguishable from one that had scheduledStart correctly at
    ingest time. MarketObservation is otherwise append-only/immutable
    (see lib/edgelab/storage.py's module docstring); this is the one
    sanctioned, explicit, auditable exception -- an in-place REWRITE of
    already-archived rows, keyed by marketObservationId (a hash of
    marketTicker+capturedAt, unaffected by scheduledStart's value, so
    identity/dedup is preserved across the rewrite).

Schedule resolution never derives scheduledStart from a Kalshi ticker's
embedded time or a closeTime/expirationTime field -- ONLY from the same
canonical MLB schedule source used everywhere else in this milestone:
data/pipeline/<date>/normalized_slate.json (lib.edgelab.market_universe.
load_game_context) as the primary, free/offline source, and
lib.edgelab.mlb_schedule's live MLB Stats API second source ONLY when at
least one (awayTeam, homeTeam) pair this date actually needs isn't
already covered by the pipeline slate (never an unconditional network
call). A pair with no match in either source is left completely
untouched and reported in the receipt -- never guessed, never fabricated.
If the live fetch itself fails (network policy, timeout, non-2xx), that
is reported as an explicit warning too; scheduledStart simply stays null
for whatever it would have resolved, and CLV for those rows correctly
stays CLV_UNAVAILABLE rather than ever being backfilled from a
lower-confidence source.

Usage:
    python3 scripts/edgelab/backfill_scheduled_start.py --date 2026-08-16 [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.market_universe import (
    backfill_missing_scheduled_start,
    compute_observation_temporal_fields,
    load_game_context,
)
from lib.edgelab.mlb_schedule import resolve_schedule_game_context


def _resolve_game_context(date, games, observations):
    """
    Pipeline-slate context first (free, offline, already-canonical) --
    the live MLB-schedule second source is only fetched when at least one
    (awayTeam, homeTeam) pair this date's still-unresolved Game/
    MarketObservation rows actually need isn't already covered by the
    pipeline slate, so an already-fully-resolved-by-slate date (e.g. a
    normal slate-backed day being re-run defensively) never makes a live
    call at all. Returns (context, warnings).
    """
    pipeline_context = load_game_context(date)

    needed_pairs = {
        (g.get("awayTeam"), g.get("homeTeam"))
        for g in games if g.get("scheduledStartTime") is None
    }
    needed_pairs |= {
        (o.get("awayTeam"), o.get("homeTeam"))
        for o in observations if o.get("scheduledStart") is None
    }
    needed_pairs.discard((None, None))

    context = dict(pipeline_context)
    warnings = []
    if needed_pairs - set(pipeline_context):
        schedule_context, warnings = resolve_schedule_game_context(date)
        for pair, ctx in schedule_context.items():
            context.setdefault(pair, ctx)  # pipeline slate wins on overlap -- it's the primary source

    return context, warnings


def _backfill_observations(observations, game_context):
    """
    Pure. Returns (updated_rows, updated_count, unresolved_pairs) --
    updated_rows is the FULL rewritten list (unchanged rows included, in
    original order) ready for storage.write_all_records. is_first_of_day
    is reconstructed from this same list's own order (the one contextual
    input compute_observation_temporal_fields can't infer from a single
    row alone) -- identical to how the original ingest run tracked it.
    """
    seen_tickers = set()
    updated_rows = []
    unresolved_pairs = set()
    updated_count = 0
    for row in observations:
        ticker = row.get("marketTicker")
        is_first_of_day = ticker not in seen_tickers
        seen_tickers.add(ticker)

        if row.get("scheduledStart") is not None:
            updated_rows.append(row)
            continue

        away, home = row.get("awayTeam"), row.get("homeTeam")
        ctx = game_context.get((away, home)) if away and home else None
        scheduled_start = ctx.get("scheduledStart") if ctx else None
        if not scheduled_start:
            if away and home:
                unresolved_pairs.add((away, home))
            updated_rows.append(row)
            continue

        temporal_fields = compute_observation_temporal_fields(
            row["capturedAt"], scheduled_start, row.get("marketStatus"), is_first_of_day=is_first_of_day,
        )
        new_row = dict(row)
        new_row["scheduledStart"] = scheduled_start
        new_row.update(temporal_fields)
        updated_rows.append(new_row)
        updated_count += 1

    return updated_rows, updated_count, sorted(unresolved_pairs)


def backfill_date(date, dry_run=False):
    games_path = storage.partition_path("games", date)
    obs_path = storage.partition_path("observations", date, compressed=True)

    games = list(storage.read_records(games_path))
    observations = list(storage.read_records(obs_path))

    game_context, schedule_warnings = _resolve_game_context(date, games, observations)

    games_backfilled = backfill_missing_scheduled_start(games, game_context)
    if games_backfilled and not dry_run:
        storage.upsert_records(games_path, games_backfilled, "gameId")

    updated_rows, obs_updated_count, unresolved_pairs = _backfill_observations(observations, game_context)
    if obs_updated_count and not dry_run:
        with storage.locked(obs_path):
            storage.write_all_records(obs_path, updated_rows)

    return {
        "date": date,
        "gamesBackfilled": len(games_backfilled),
        "observationsBackfilled": obs_updated_count,
        "observationsTotal": len(observations),
        "unresolvedTeamPairs": [f"{a}@{h}" for a, h in unresolved_pairs],
        "scheduleWarnings": schedule_warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="UTC slate date YYYY-MM-DD to backfill")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print counts without writing")
    args = parser.parse_args()

    result = backfill_date(args.date, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}[backfill_scheduled_start] date={result['date']} "
        f"games_backfilled={result['gamesBackfilled']} "
        f"observations_backfilled={result['observationsBackfilled']}/{result['observationsTotal']} "
        f"unresolved_team_pairs={len(result['unresolvedTeamPairs'])}"
    )
    for pair in result["unresolvedTeamPairs"]:
        print(
            f"{prefix}[backfill_scheduled_start] unresolved: {pair} -- no canonical schedule match found; "
            "scheduledStart left null, CLV stays UNAVAILABLE for this pair's markets",
            file=sys.stderr,
        )
    for w in result["scheduleWarnings"]:
        print(f"{prefix}[backfill_scheduled_start] schedule warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
