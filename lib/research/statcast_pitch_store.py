#!/usr/bin/env python3
"""
lib/research/statcast_pitch_store.py
=======================================
Hitter Projection Engine -- Phase 2 raw pitch archive.

STORAGE DESIGN (three separate layers, per this mission's spec)
-------------------------------------------------------------------
  A. RAW HISTORICAL PITCH ARCHIVE (this module) -- one append-only,
     deduplicated JSONL file per gamePk under data/statcast_raw/games/,
     plus a small per-batter index (data/statcast_raw/index/
     batter_games.jsonl) mapping batterId -> which game files contain
     pitches to that batter, so a per-batter history load never has to
     scan every game file ever ingested.
  B. DERIVED HITTER/PITCHER FEATURE TABLES -- lib.research.
     hitter_pitch_derivation, computed on demand from (A); never itself
     persisted as a separate historical archive (it is cheap to
     recompute from the raw archive, and NOT persisting it avoids a
     second place windowed stats could silently go stale).
  C. DAILY PREGAME HITTER SNAPSHOT -- lib.research.hitter_feature_context
     (Phase 1) + scripts/build_hitter_feature_board.py's
     data/pipeline/<date>/hitter_features.json artifact, which calls (B)
     with an as-of cutoff at build time.

REUSE, NOT DUPLICATION
-------------------------
This module does not reimplement JSONL append/dedup/atomic-write/locking
-- it calls lib.edgelab.storage's existing, tested primitives
(append_records, read_records, locked) directly, the same primitives
EdgeLab's own settlement/observation records already use. Only the path
layout (data/statcast_raw/, not data/edgelab/) is new, because this is a
different domain (raw third-party pitch history, not EdgeLab's
research/settlement entities) — see docs/SOURCE_OF_TRUTH_MAP.md's own
precedent of scoping storage "by concept, not one crown."

IDEMPOTENCY / DEDUPLICATION
------------------------------
Every raw pitch record gets a stable `pitchId` (see pitch_identity())
built only from immutable identity fields (gamePk, atBatIndex/inning+
pitcherId+batterId, pitchNumber) -- never from a fetch timestamp or any
value that could differ between two ingestions of the same real pitch.
ingest_game_pitches() calls lib.edgelab.storage.append_records() keyed
on this id, so re-ingesting the same game (or a game whose pitch log was
re-fetched) is a true no-op for already-present pitches: the second run
writes zero new lines for anything already on disk.

Historical data is NEVER overwritten with a current aggregate here --
this module has no "upsert a whole game" operation, only append-new-
pitches-only. A pitch record, once written, keeps its original committed
bytes forever (short of an explicit backfill/correction script, which
does not exist in this Phase 2 milestone).

NO REDOWNLOAD ON EVERY RUN
------------------------------
has_game(game_pk) is a single os.path.exists() check -- the intended
caller pattern (see scripts/fetch_statcast_pitch_log.py) is "skip any
gamePk that already has a raw file," so a slate run only ever fetches
pitches for games not yet archived, never re-downloads a player's entire
history.
"""

import os
from typing import Optional

from lib.edgelab.storage import append_records, read_records, locked

STATCAST_RAW_ROOT = os.path.join("data", "statcast_raw")
GAMES_DIR = os.path.join(STATCAST_RAW_ROOT, "games")
BATTER_INDEX_PATH = os.path.join(STATCAST_RAW_ROOT, "index", "batter_games.jsonl")
# Phase 4: symmetric per-PITCHER index -- added because Phase 4's pitch-
# environment model (lib.research.pitch_environment_model) needs a
# pitcher's OWN pitch-mix/velocity/shape tendencies across every batter
# he's faced, which the batter-only index can't answer without scanning
# every archived game file. Same design as BATTER_INDEX_PATH (this is
# the "modeling-critical ingestion gap" this milestone's own scope
# instructions allow extending existing ingestion for, not a rebuild).
PITCHER_INDEX_PATH = os.path.join(STATCAST_RAW_ROOT, "index", "pitcher_games.jsonl")


def game_path(game_pk) -> str:
    return os.path.join(GAMES_DIR, f"{game_pk}.jsonl")


def pitch_identity(pitch: dict) -> str:
    """
    Stable identity for one raw pitch, built only from fields that can
    never change between two fetches of the same real pitch. Prefers
    (gamePk, atBatIndex, pitchNumber) -- Statcast's own stable per-PA
    sequence -- and falls back to (gamePk, pitcherId, batterId, inning,
    pitchNumber) when atBatIndex isn't supplied (e.g. a source that
    doesn't expose it), which is still deterministic for a normal PA but
    marginally less precise for a rare same-inning re-entry edge case
    (documented here rather than silently assumed away).
    """
    game_pk = pitch.get("gamePk")
    pitch_number = pitch.get("pitchNumber")
    at_bat_index = pitch.get("atBatIndex")
    if at_bat_index is not None:
        return f"{game_pk}:ab{at_bat_index}:p{pitch_number}"
    return f"{game_pk}:{pitch.get('pitcherId')}:{pitch.get('batterId')}:in{pitch.get('inning')}:p{pitch_number}"


def has_game(game_pk) -> bool:
    return os.path.exists(game_path(game_pk))


def _index_rows(stamped_pitches, id_key: str, id_field_name: str, index_id_suffix: str, game_pk):
    rows = []
    seen = set()
    for p in stamped_pitches:
        entity_id = p.get(id_key)
        game_date = p.get("gameDate")
        if entity_id is None or (entity_id, game_pk) in seen:
            continue
        seen.add((entity_id, game_pk))
        rows.append({
            index_id_suffix: f"{entity_id}:{game_pk}",
            id_field_name: entity_id,
            "gamePk": game_pk,
            "gameDate": game_date,
        })
    return rows


def ingest_game_pitches(game_pk, pitches) -> dict:
    """
    Append `pitches` (raw pitch dicts, see this module's field-list
    companion lib.research.hitter_pitch_derivation's module docstring
    for the canonical schema) to this game's archive file, deduplicated
    by pitch_identity(), and update both the per-batter AND per-pitcher
    (Phase 4) indexes so future per-batter/per-pitcher loads find this
    game. Idempotent: calling this twice with the same pitches writes
    zero new rows the second time.
    """
    stamped = []
    for p in pitches:
        p = dict(p)
        p["gamePk"] = game_pk
        p.setdefault("pitchId", pitch_identity(p))
        stamped.append(p)

    written, skipped = append_records(game_path(game_pk), stamped, id_field="pitchId")

    batter_index_rows = _index_rows(stamped, "batterId", "batterId", "batterGameId", game_pk)
    index_written, index_skipped = append_records(BATTER_INDEX_PATH, batter_index_rows, id_field="batterGameId")

    pitcher_index_rows = _index_rows(stamped, "pitcherId", "pitcherId", "pitcherGameId", game_pk)
    pitcher_index_written, pitcher_index_skipped = append_records(PITCHER_INDEX_PATH, pitcher_index_rows, id_field="pitcherGameId")

    return {
        "gamePk": game_pk,
        "pitchesWritten": written,
        "pitchesSkipped": skipped,
        "indexRowsWritten": index_written,
        "indexRowsSkipped": index_skipped,
        "pitcherIndexRowsWritten": pitcher_index_written,
        "pitcherIndexRowsSkipped": pitcher_index_skipped,
    }


def load_pitches_for_game(game_pk):
    return list(read_records(game_path(game_pk)))


def _load_pitches_by_index(index_path, id_field_name, entity_id, id_pitch_field, as_of, since):
    entity_id_str = str(entity_id)
    game_pks = []
    for row in read_records(index_path):
        if str(row.get(id_field_name)) != entity_id_str:
            continue
        game_date = row.get("gameDate")
        if as_of is not None and game_date is not None and not (game_date < as_of):
            continue
        if since is not None and game_date is not None and game_date < since:
            continue
        game_pks.append(row.get("gamePk"))

    pitches = []
    for game_pk in game_pks:
        for p in load_pitches_for_game(game_pk):
            if str(p.get(id_pitch_field)) != entity_id_str:
                continue
            game_date = p.get("gameDate")
            if as_of is not None and game_date is not None and not (game_date < as_of):
                continue
            if since is not None and game_date is not None and game_date < since:
                continue
            pitches.append(p)
    return pitches


def load_pitches_for_batter(batter_id, as_of: Optional[str] = None, since: Optional[str] = None):
    """
    Load every archived pitch thrown to `batter_id`, optionally bounded
    to [since, as_of) by gameDate (ISO 'YYYY-MM-DD' strings compare
    correctly as plain strings). as_of is EXCLUSIVE -- a pitch on
    gameDate == as_of is NOT included, matching "as_of=<slate date>"
    meaning "everything known strictly before today's games" (the
    pregame-safe cutoff for a projection built for that date). Reads via
    the per-batter index first (data/statcast_raw/index/batter_games.jsonl)
    so this never scans every game file ever archived -- only the games
    that index says involved this batter -- then re-filters at the
    individual-pitch level as defense-in-depth in case the index and a
    game file ever disagree.

    Returns [] (never raises) if the index or a referenced game file is
    absent -- an unfetched batter simply has no history yet, which is a
    normal, expected state for this foundation, not an error.
    """
    return _load_pitches_by_index(BATTER_INDEX_PATH, "batterId", batter_id, "batterId", as_of, since)


def load_pitches_for_pitcher(pitcher_id, as_of: Optional[str] = None, since: Optional[str] = None):
    """
    Phase 4 symmetric counterpart to load_pitches_for_batter() -- every
    archived pitch THROWN BY `pitcher_id` (to any batter), same as-of/
    since/index-first/defense-in-depth contract. Used by
    lib.research.pitch_environment_model to derive a pitcher's own
    pitch-mix/velocity/shape tendencies.
    """
    return _load_pitches_by_index(PITCHER_INDEX_PATH, "pitcherId", pitcher_id, "pitcherId", as_of, since)
