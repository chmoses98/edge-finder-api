#!/usr/bin/env python3
"""
tests/edgelab/test_ingest_market_observations_script.py
============================================================
Market Research Corpus milestone: end-to-end coverage for
scripts/edgelab/ingest_market_observations.py -- FIRST_DAILY tracking
across separate runs, the growth-control retention filter (a later,
unchanged-price snapshot commits nothing new), and that production files
are never touched.
"""
import copy
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kalshi_search_sample.json")
DATE = "2026-07-31"


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ingest_script = _load_script("ingest_market_observations.py")


def _seed_snapshot(tmp_path, filename, *, ts_suffix, price_bump=0.0):
    with open(FIXTURE) as f:
        data = json.load(f)
    data = copy.deepcopy(data)
    new_ts = f"2026-07-31T{ts_suffix}.000Z"
    data["fetched_at"] = new_ts
    for m in data.get("markets", []):
        m["snapshot_ts"] = new_ts
        if price_bump and m.get("yes_bid") is not None:
            m["yes_bid"] = round(m["yes_bid"] + price_bump, 2)
    for m in data.get("discoveredUnknownSeriesMarkets", []):
        m["snapshot_ts"] = new_ts

    snap_dir = os.path.join("data", "kalshi_registry_snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    dest = os.path.join(snap_dir, filename)
    with open(dest, "w") as f:
        json.dump(data, f)
    return dest


def test_first_run_writes_observations_games_markets_and_research_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    exit_code = ingest_script.main()
    assert exit_code == 0

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert len(observations) == 31  # matches test_market_universe.py's full-eligible-market-capture count
    assert all(o["checkpoint"] == "FIRST_DAILY" for o in observations)

    games = list(storage.read_records(storage.partition_path("games", DATE)))
    markets = list(storage.read_records(storage.partition_path("markets", DATE)))
    assert len(games) > 0
    assert len(markets) == 31

    runs = list(storage.read_records(storage.partition_path("research_runs", DATE)))
    assert len(runs) == 1
    assert runs[0]["counts"]["observationsRetained"] == 31


def test_later_unchanged_snapshot_retains_nothing_new(tmp_path, monkeypatch):
    # GITHUB_RUN_ID set (as it always is in a real Actions job) so this
    # reproduces the exact CI environment the Research-Run Manifest
    # Identity bug occurred in -- two invocations in the same process
    # (and, in real CI, the same wall-clock second) under the same
    # GITHUB_RUN_ID must not collide on runId.
    monkeypatch.setenv("GITHUB_RUN_ID", "555000")
    monkeypatch.chdir(tmp_path)
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()

    # A later capture with byte-identical prices -- growth-control
    # requirement: must add ZERO new committed rows, even though every
    # observation gets a genuinely new marketObservationId (new capturedAt).
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2230.json", ts_suffix="22:30:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE, "--all-snapshots"])
    ingest_script.main()

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert len(observations) == 31  # unchanged -- the second tick contributed nothing new

    runs = list(storage.read_records(storage.partition_path("research_runs", DATE)))
    assert len(runs) == 2  # both invocations got their OWN manifest -- the second was not silently discarded
    assert runs[0]["runId"] != runs[1]["runId"]
    assert runs[-1]["counts"]["observationsBuilt"] == 31 + 31  # both snapshots parsed...
    assert runs[-1]["counts"]["observationsRetained"] == 0     # ...but nothing new is worth keeping
    assert runs[-1]["counts"]["observationsDroppedNoChange"] == 62


def test_later_changed_price_is_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "555001")
    monkeypatch.chdir(tmp_path)
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()

    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2230.json", ts_suffix="22:30:00", price_bump=0.05)
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE, "--all-snapshots"])
    ingest_script.main()

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    # Every ticker whose yesBid moved is retained a second time; the rest are not.
    assert len(observations) > 31

    runs = list(storage.read_records(storage.partition_path("research_runs", DATE)))
    assert len(runs) == 2
    assert runs[0]["runId"] != runs[1]["runId"]
    assert runs[-1]["counts"]["observationsRetained"] > 0


def test_two_invocations_same_github_run_different_snapshot_sets_produce_separate_run_records(tmp_path, monkeypatch):
    """
    Research-Run Manifest Identity fix, direct reproduction: two
    ingestion invocations inside the SAME GitHub Actions run (same
    GITHUB_RUN_ID), landing in the same wall-clock second in practice,
    processing DIFFERENT snapshot sets, must each get their own
    research_runs manifest -- never silently collapse into one via
    dedup-by-runId.
    """
    monkeypatch.setenv("GITHUB_RUN_ID", "777000")
    monkeypatch.chdir(tmp_path)
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()

    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2230.json", ts_suffix="22:30:00", price_bump=0.05)
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE, "--all-snapshots"])
    ingest_script.main()

    runs = list(storage.read_records(storage.partition_path("research_runs", DATE)))
    assert len(runs) == 2
    first, second = runs[0], runs[1]
    assert first["runId"] != second["runId"]
    assert "gh777000" in first["runId"] and "gh777000" in second["runId"]
    # The first invocation's manifest is untouched by the second.
    assert first["counts"]["snapshotsProcessed"] == 1
    assert second["counts"]["snapshotsProcessed"] == 2


def test_repeating_the_exact_same_invocation_is_idempotent(tmp_path, monkeypatch):
    """A true retry of the exact same inputs (same date, same single snapshot file, same GITHUB_RUN_ID) must remain a no-op, not create a duplicate manifest."""
    monkeypatch.setenv("GITHUB_RUN_ID", "777001")
    monkeypatch.chdir(tmp_path)
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()
    ingest_script.main()  # exact same argv, same inputs on disk, same GITHUB_RUN_ID

    runs = list(storage.read_records(storage.partition_path("research_runs", DATE)))
    assert len(runs) == 1  # deterministic content_signature -> same runId -> true no-op, not a duplicate

    observations = list(storage.read_records(storage.partition_path("observations", DATE, compressed=True)))
    assert len(observations) == 31  # no duplicate observations either


def test_stuck_game_row_is_backfilled_once_a_slate_match_becomes_available(tmp_path, monkeypatch):
    """
    Root-cause regression (real Aug 5 2026 case): a Game row created
    before that date's normalized_slate.json existed/matched stays
    mlbGamePk=null forever under the OLD logic, even once a later run
    has a perfectly good exact date+away+home match available. This run
    should self-heal the pre-existing stuck row in place -- never
    renaming its gameId, never creating a second duplicate row for the
    same match.

    This same fixture also produces a FRESH, gameId=777123-keyed sibling
    row for BOS@LAD (real 2026-08-04 case: this is exactly how a game
    ends up with two Game rows -- one stuck under the old fallback
    gameId, one freshly built under the authoritative gameId once
    game_context resolves). mark_superseded_game_identities is the
    companion self-heal for that half: it must flag the stuck row as
    superseded by the fresh one, without deleting or renaming either.
    """
    monkeypatch.chdir(tmp_path)
    stuck_game_id = "2026-07-31_BOS_LAD_2210"
    storage.write_all_records(storage.partition_path("games", DATE), [{
        "schemaVersion": "1", "gameId": stuck_game_id, "sport": "MLB", "platform": "KALSHI",
        "mlbGamePk": None, "gameDate": DATE, "scheduledStartTime": None, "actualStartTime": None,
        "awayTeam": "BOS", "homeTeam": "LAD", "venue": None, "status": None,
        "doubleheaderGameNumber": None, "kalshiKey": None, "createdAt": "2026-07-31T20:00:00Z",
        "updatedAt": None, "source": "kalshi_registry_snapshots", "validationStatus": "warning",
        "provenance": {"sourceSystem": "kalshi_registry_snapshots", "sourceFile": "old.json", "sourceKey": stuck_game_id, "capturedAt": "2026-07-31T20:00:00Z", "ingestedAt": "2026-07-31T20:00:00Z"},
    }])

    os.makedirs(os.path.join("data", "pipeline", DATE), exist_ok=True)
    with open(os.path.join("data", "pipeline", DATE, "normalized_slate.json"), "w") as f:
        json.dump({"data": {"games": [{
            "away": {"abbr": "BOS"}, "home": {"abbr": "LAD"}, "gameId": 777123,
            "startTime": "2026-07-31T22:10:00Z", "status": "Final", "venue": "Dodger Stadium", "kalshiKey": "BOSLAD",
        }]}}, f)

    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()

    games = list(storage.read_records(storage.partition_path("games", DATE)))
    stuck_row = next(g for g in games if g["gameId"] == stuck_game_id)
    assert stuck_row["mlbGamePk"] == "777123"
    assert stuck_row["validationStatus"] == "valid"
    assert stuck_row["mlbGamePkBackfill"]["method"] == "DATE_AWAY_HOME_UNIQUE_MATCH"
    # backfill_missing_game_pks never renames/removes the stuck row --
    # it is patched in place, still present under its original gameId.

    fresh_row = next(g for g in games if g["gameId"] == "777123")
    assert fresh_row["mlbGamePk"] == "777123"
    assert "supersededBy" not in fresh_row or fresh_row["supersededBy"] is None

    # mark_superseded_game_identities flags the stuck row as a duplicate
    # of the fresh, authoritative row -- gameId still never renamed.
    assert stuck_row["supersededBy"]["canonicalGameId"] == "777123"
    assert stuck_row["supersededBy"]["method"] == "DATE_AWAY_HOME_UNIQUE_MATCH"


def test_ingest_never_touches_production_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data/pipeline", exist_ok=True)
    with open("data/slate.json", "w") as f:
        f.write('{"untouched": true}')
    _seed_snapshot(tmp_path, f"kalshi_search_{DATE}_2200.json", ts_suffix="22:00:00")
    monkeypatch.setattr(sys, "argv", ["ingest_market_observations.py", "--date", DATE])
    ingest_script.main()

    with open("data/slate.json") as f:
        assert json.load(f) == {"untouched": True}
    assert not os.path.exists("bets.json")
    assert not os.path.exists(os.path.join("data", "bets.json"))
