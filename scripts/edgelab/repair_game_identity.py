#!/usr/bin/env python3
"""
scripts/edgelab/repair_game_identity.py
=============================================
CLI entry point: self-heal one date's already-stored Game dimension rows
(data/edgelab/games/<date>.jsonl) using the same exact-match identity
logic scripts/edgelab/ingest_market_observations.py now runs
automatically on every ingest (lib.edgelab.market_universe.
backfill_missing_game_pks + mark_superseded_game_identities) -- for a
date whose rows were last written before that self-heal step existed in
the codebase, or before it last had both a stored row and a real
data/pipeline/<date>/normalized_slate.json to check against, so the rows
are stuck exactly as first ingested.

The real 2026-08-04 case this was built for: all 15 games that day were
first ingested at 04:58 UTC, before that day's normalized_slate.json
existed (it wasn't captured until 21:27 UTC) -- so every game got a
ticker-derived fallback gameId (e.g. '2026-08-04_NYM_CLE_1840',
mlbGamePk null). Every ingest run after 21:27 then produced a SECOND row
per game keyed by the authoritative MLB gamePk (e.g. '824403'),
doubling the day's Game count from 15 to 30. No ingest ran for this date
after backfill_missing_game_pks/mark_superseded_game_identities existed
(both landed 2026-08-07+), so the stored rows never got the chance to
self-heal automatically.

Touches ONLY data/edgelab/games/<date>.jsonl:
  - backfill_missing_game_pks fills mlbGamePk/venue/status/kalshiKey on
    any row still stuck at mlbGamePk=null whose (awayTeam, homeTeam) now
    has an exact game_context match -- this is what actually unblocks
    settlement (scripts/edgelab/settle_markets.py resolves a market's
    game by gameId, and a null mlbGamePk means "no MLB gamePk resolved;
    settlement will be unresolved" for every market still pointing at
    that row).
  - mark_superseded_game_identities then flags any row whose OWN gameId
    differs from its (awayTeam, homeTeam) pair's authoritative
    game_context gameId with an additive supersededBy marker pointing at
    the canonical row, so research/report code (lib.edgelab.reports.
    build_daily_report's gamesObserved) counts real games, not raw rows.

Never renames, merges, or deletes a row (gameId is a stable join key
already referenced by whatever Market/MarketObservation rows were built
against it) and never fuzzy-matches (only an exact, unique
awayTeam+homeTeam game_context match counts, exactly like both library
functions already require). Never touches observations/markets/
settlements/bets directly -- run scripts/edgelab/settle_markets.py
separately afterward if the identity fix should also refresh
settlement, which then picks up the newly-populated mlbGamePk values
on its own.

Second identity source (lib.edgelab.mlb_schedule): after the
pipeline-slate pass above, any row STILL missing mlbGamePk is retried
against a live MLB Stats API schedule-by-date fetch -- a standalone/
manual-only Kalshi research day that never had a
data/pipeline/<date>/normalized_slate.json run is not otherwise
reachable by the pass above, no matter how many times it's rerun (see
lib.edgelab.mlb_schedule's module docstring for the full root-cause
writeup). Only fetched when at least one row still needs it; a fully
resolved date never triggers a network call. Uses the identical
backfill_missing_game_pks pure function (unmodified) -- just a second
game_context source, distinguished in the persisted
mlbGamePkBackfill.matchedAgainst field by which URL/path actually
resolved it.

Usage:
    python3 scripts/edgelab/repair_game_identity.py --date 2026-08-04 [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.market_universe import (
    backfill_missing_game_pks,
    load_game_context,
    mark_superseded_game_identities,
)
from lib.edgelab.mlb_schedule import backfill_missing_game_pks_via_schedule


def repair_date(date, dry_run=False):
    game_context = load_game_context(date)
    games_path = storage.partition_path("games", date)

    games = list(storage.read_records(games_path))
    backfilled = backfill_missing_game_pks(games, game_context)
    if backfilled and not dry_run:
        storage.upsert_records(games_path, backfilled, "gameId")

    games = list(storage.read_records(games_path)) if not dry_run else _apply(games, backfilled)
    superseded = mark_superseded_game_identities(games, game_context, date)
    if superseded and not dry_run:
        storage.upsert_records(games_path, superseded, "gameId")

    games = list(storage.read_records(games_path)) if not dry_run else _apply(games, superseded)
    schedule_backfilled, schedule_warnings = backfill_missing_game_pks_via_schedule(games, date)
    if schedule_backfilled and not dry_run:
        storage.upsert_records(games_path, schedule_backfilled, "gameId")

    return {
        "gamesBackfilledMlbGamePk": len(backfilled),
        "gamesIdentitySuperseded": len(superseded),
        "gamesBackfilledMlbGamePkViaSchedule": len(schedule_backfilled),
        "scheduleWarnings": schedule_warnings,
    }


def _apply(games, updates):
    by_id = {g["gameId"]: g for g in updates}
    return [by_id.get(g["gameId"], g) for g in games]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="UTC slate date YYYY-MM-DD to repair")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print counts without writing")
    args = parser.parse_args()

    counts = repair_date(args.date, dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}[repair_game_identity] date={args.date} "
        f"backfilled_mlb_game_pk={counts['gamesBackfilledMlbGamePk']} "
        f"identity_superseded={counts['gamesIdentitySuperseded']} "
        f"backfilled_mlb_game_pk_via_schedule={counts['gamesBackfilledMlbGamePkViaSchedule']}"
    )
    for w in counts["scheduleWarnings"]:
        print(f"{prefix}[repair_game_identity] schedule warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
