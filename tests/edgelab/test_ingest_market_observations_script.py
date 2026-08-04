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
    assert runs[-1]["counts"]["observationsBuilt"] == 31 + 31  # both snapshots parsed...
    assert runs[-1]["counts"]["observationsRetained"] == 0     # ...but nothing new is worth keeping


def test_later_changed_price_is_retained(tmp_path, monkeypatch):
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
    assert runs[-1]["counts"]["observationsRetained"] > 0


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
