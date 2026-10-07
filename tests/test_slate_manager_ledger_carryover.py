#!/usr/bin/env python3
"""A ledger-free rerun (Lineup Recheck) must not erase a not-started game's priced
marketLedger (2026-10-07: a manual IN_PLAY_RECHECK wiped TB@NYY / MIL@SD model prices)."""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import slate_manager as sm  # noqa: E402

NOW = datetime(2026, 10, 7, 21, 20, tzinfo=timezone.utc)
LEDGER = [{"market": "ML_Away", "modelProb": 48.1, "executablePriceSide": "YES",
           "executablePriceMarketTicker": "KXMLBGAME-26OCT072000TBNYY-TB", "priceSnapshotTimestamp": "2026-10-07T19:57:00Z"}]


def _game(pk, start, **kw):
    g = {"gameId": pk, "startTime": start, "status": "Pre-Game", "away": {"abbr": "TB"}, "home": {"abbr": "NYY"}}
    g.update(kw)
    return g


def test_ledger_free_manual_rerun_keeps_the_priced_ledger_and_says_so():
    auth = {"date": "2026-10-07", "games": [_game(849838, "2026-10-08T00:00:00Z", marketLedger=LEDGER, lineupStatus="partial")]}
    rerun = {"date": "2026-10-07", "games": [_game(849838, "2026-10-08T00:00:00Z", lineupStatus="confirmed")]}
    merged, report = sm.merge_rerun_into_authoritative(auth, rerun, sm.RUN_TYPE_IN_PLAY_RECHECK, now_utc=NOW,
                                                       trigger_source=sm.TRIGGER_MANUAL)
    g = merged["games"][0]
    assert g["lineupStatus"] == "confirmed"                 # the refresh still lands
    assert g["marketLedger"] == LEDGER                      # the priced ledger is not erased
    assert g["marketLedgerCarriedOver"]["asOf"] == "2026-10-07T19:57:00Z"


def test_a_rerun_with_its_own_ledger_replaces_it():
    new = [dict(LEDGER[0], modelProb=50.0, priceSnapshotTimestamp="2026-10-07T21:19:00Z")]
    auth = {"date": "2026-10-07", "games": [_game(849838, "2026-10-08T00:00:00Z", marketLedger=LEDGER)]}
    rerun = {"date": "2026-10-07", "games": [_game(849838, "2026-10-08T00:00:00Z", marketLedger=new)]}
    merged, _ = sm.merge_rerun_into_authoritative(auth, rerun, sm.RUN_TYPE_LINEUP_RECHECK, now_utc=NOW,
                                                  trigger_source=sm.TRIGGER_MANUAL)
    assert merged["games"][0]["marketLedger"] == new
    assert "marketLedgerCarriedOver" not in merged["games"][0]


def test_started_games_stay_frozen():
    auth = {"date": "2026-10-07", "games": [_game(849833, "2026-10-07T20:00:00Z", marketLedger=LEDGER, status="Warmup")]}
    rerun = {"date": "2026-10-07", "games": [_game(849833, "2026-10-07T20:00:00Z", status="In Progress")]}
    merged, report = sm.merge_rerun_into_authoritative(auth, rerun, sm.RUN_TYPE_IN_PLAY_RECHECK, now_utc=NOW,
                                                       trigger_source=sm.TRIGGER_MANUAL)
    assert merged["games"][0]["status"] == "Warmup" and merged["games"][0]["marketLedger"] == LEDGER
