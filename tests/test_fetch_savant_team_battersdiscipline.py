#!/usr/bin/env python3
"""
tests/test_fetch_savant_team_battersdiscipline.py
=====================================================
Hitter Projection Engine Phase 2: proves the K%/BB%/whiff%/hardHit%/
barrel%/exitVelo columns api/enrich.js's type=batting CSV request was
already fetching -- but previously discarded before reaching
data/savant_team.json -- are now actually persisted, via
scripts/fetch_savant_team.py's new battersDiscipline wiring.
"""
import json
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.fetch_savant_team as fetch_savant_team


class _TempCwd:
    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmpdir)
        return self.tmpdir

    def __exit__(self, *exc):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestBattersDisciplinePersists:
    def test_battersdiscipline_written_to_savant_team_json(self, monkeypatch):
        def fake_fetch_json(url, timeout=30):
            if 'type=batting' in url:
                return {
                    "ok": True,
                    "teams": {"NYY": {"xwoba": 0.32, "fbPct": 0.36}},
                    "batters": {"12345": 0.345},
                    "battersDiscipline": {"12345": {"kPct": 21.0, "bbPct": 8.5, "whiffPct": 27.0,
                                                      "hardHitPct": 42.0, "barrelPct": 9.1, "exitVeloAvg": 90.2}},
                }
            if 'api/savant' in url:
                return {"ok": True, "pitchers": {}}
            return None

        monkeypatch.setattr(fetch_savant_team, "fetch_json", fake_fetch_json)
        with _TempCwd():
            os.makedirs("data", exist_ok=True)
            fetch_savant_team.main()
            with open(os.path.join("data", "savant_team.json")) as f:
                saved = json.load(f)
            assert "battersDiscipline" in saved
            assert saved["battersDiscipline"]["12345"]["hardHitPct"] == 42.0
            assert saved["battersDiscipline"]["12345"]["kPct"] == 21.0
            # batters (xwOBA scalar map) shape unchanged -- backward compatible
            assert saved["batters"]["12345"] == 0.345

    def test_missing_battersdiscipline_in_response_degrades_gracefully(self, monkeypatch):
        def fake_fetch_json(url, timeout=30):
            if 'type=batting' in url:
                return {"ok": True, "teams": {}, "batters": {"1": 0.3}}  # no battersDiscipline key
            return {"ok": True, "pitchers": {}}

        monkeypatch.setattr(fetch_savant_team, "fetch_json", fake_fetch_json)
        with _TempCwd():
            os.makedirs("data", exist_ok=True)
            fetch_savant_team.main()
            with open(os.path.join("data", "savant_team.json")) as f:
                saved = json.load(f)
            assert saved["battersDiscipline"] == {}
