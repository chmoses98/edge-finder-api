"""Network-free tests for the MLB pitcher shadow scheduling plan (lib/research/mlb_pitcher_shadow_plan.py):
missed-window recording and the cheap workflow gate (scripts/research/mlb_pitcher_shadow_gate.py)."""
import ast
import importlib.util
import os
from datetime import datetime, timezone

from lib.research import mlb_pitcher_shadow_plan as P
from lib.research.shadow_pure_store import ShadowStore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location("mlb_pitcher_shadow_gate", os.path.join(ROOT, "scripts", "research", "mlb_pitcher_shadow_gate.py"))
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)

DATE = "2026-07-01"
START = "2026-07-01T14:00:00Z"   # 10:00 EDT: the first live capture run


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _game(pk, kickoff, status=("Preview", "Scheduled"), lineups=True):
    return {"pk": pk, "game_type": "R", "official_date": DATE, "kickoff": kickoff, "abstract": status[0], "detailed": status[1],
            "schedule_fetched_at": START,
            "away": {"team_id": 133, "probable_id": "1", "probable_name": "A", "lineup_posted": lineups},
            "home": {"team_id": 117, "probable_id": "2", "probable_name": "B", "lineup_posted": lineups}}


def _store(tmp_path, captured=(("A", "pregame"),), dry_run=False):
    st = ShadowStore(str(tmp_path / "store"))
    st.write_once(f"runs/{DATE}/20260701T140000Z_pregame.json", {"as_of": START, "dry_run": dry_run, "window": "pregame"}, START)
    for pk, w in captured:
        st.write_once(f"projections/{DATE}/20260701T140000Z_{w}__champion_{pk}.jsonl",
                      [{"game_id": pk, "kickoff": "2026-07-01T23:05:00Z", "player_id": "1"}], START)
    return st


def test_windows_that_close_uncaptured_are_missed_once_and_never_back_filled(tmp_path):
    st = _store(tmp_path)
    games = [_game("A", "2026-07-01T23:05:00Z"),                       # pregame captured; its windows still open
             _game("B", "2026-07-01T18:05:00Z"),                       # 14:05 EDT: nothing captured
             _game("C", "2026-07-01T17:05:00Z", status=("Preview", "Postponed"))]
    missed = P.missed_windows(games, _t("2026-07-01T19:00:00Z"), st.root)
    assert sorted((m["game_id"], m["window"]) for m in missed) == [("B", "morning"), ("B", "pregame")]
    assert all(m["reason"] == "NO_CAPTURE_IN_WINDOW" and m["window_closed_at"] < m["detected_at"] for m in missed)
    st.write_once(f"missed/{DATE}/20260701T190000Z.jsonl", missed)
    assert P.missed_windows(games, _t("2026-07-01T19:10:00Z"), st.root) == []      # recorded exactly once
    assert st.verify()["ok"]


def test_nothing_is_missed_before_collection_starts_or_from_dry_runs(tmp_path):
    games = [_game("B", "2026-07-01T18:05:00Z")]
    assert P.missed_windows(games, _t("2026-07-01T19:00:00Z"), str(tmp_path / "empty")) == []
    st = _store(tmp_path, captured=(), dry_run=True)
    assert P.missed_windows(games, _t("2026-07-01T19:00:00Z"), st.root) == []


def test_windows_closed_before_collection_started_are_not_misses(tmp_path):
    st = _store(tmp_path, captured=())
    early = _game("E", "2026-07-01T15:05:00Z")      # morning closed 13:05Z, before the 14:00Z start; pregame closed 14:45Z
    assert [(m["game_id"], m["window"]) for m in P.missed_windows([early], _t("2026-07-01T16:00:00Z"), st.root)] == [("E", "pregame")]


def test_gate_says_work_only_when_something_is_due(tmp_path):
    st = _store(tmp_path, captured=(("A", "pregame"), ("B", "pregame")))
    quiet = [_game("A", "2026-07-01T23:05:00Z"), _game("B", "2026-07-01T23:05:00Z")]
    # 16:30Z: both pregame captured already, morning superseded -> nothing to do
    d = G.decide(quiet, _t("2026-07-01T16:30:00Z"), st.root)
    assert d["work"] is False and d["reasons"] == []
    # a new game whose pregame window is open with lineups posted
    d = G.decide(quiet + [_game("D", "2026-07-01T17:20:00Z")], _t("2026-07-01T16:30:00Z"), st.root)
    assert d["work"] and d["due"] == [{"game_id": "D", "window": "pregame", "kickoff": "2026-07-01T17:20:00Z"}]
    # waiting for lineups is not a capture yet -- but that game's morning window closed uncaptured
    d = G.decide(quiet + [_game("D", "2026-07-01T17:40:00Z", lineups=False)], _t("2026-07-01T16:30:00Z"), st.root)
    assert d["due"] == [] and d["reasons"] == ["missed_to_record:1"] and d["missed"] == [("D", "morning")]
    # schedule fetch failure fails open
    assert G.decide([], _t("2026-07-01T16:30:00Z"), st.root, schedule_ok=False)["work"]


def test_gate_daily_evaluation_and_pending_outcomes(tmp_path):
    st = _store(tmp_path)
    d = G.decide([], _t("2026-07-01T15:07:00Z"), st.root)
    assert d["evaluate"] and "daily_evaluation" in d["reasons"]
    st.write_once("evaluations/20260701T151000Z.json", {"ok": True})
    assert not G.decide([], _t("2026-07-01T15:17:00Z"), st.root)["evaluate"]
    # game A kicked off 23:05Z; 3 h later its outcome is pending until a final provenance row exists
    d = G.decide([], _t("2026-07-02T02:30:00Z"), st.root)
    assert d["outcomes_pending"] == ["A"] and d["work"]
    st.write_once("outcome_provenance/2026-07-02/20260702T023000Z.jsonl", [{"game_id": "A", "status": "Final"}])
    assert G.decide([], _t("2026-07-02T02:40:00Z"), st.root)["work"] is False


def test_plan_and_gate_are_standard_library_only():
    for rel in ("lib/research/mlb_pitcher_shadow_plan.py", "scripts/research/mlb_pitcher_shadow_gate.py"):
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        mods = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not mods & {"pandas", "numpy", "scipy", "polars", "pyarrow", "requests"}, rel
        assert mods - {"__future__", "glob", "gzip", "json", "os", "datetime", "zoneinfo", "argparse", "sys", "urllib", "lib"} == set(), rel


# ----------------------------------------------------------------------------- 2026-10-12 duplicate-capture incident

def test_adopt_manifests_an_orphaned_file_by_append_only(tmp_path):
    from lib.research.shadow_pure_store import WriteOnceError
    st = _store(tmp_path, captured=(("A", "pregame"),))
    before = open(st.manifest_path, encoding="utf-8").read()
    orphan = os.path.join(st.root, "runs", DATE, "20261012T122604Z_morning.json")
    with open(orphan, "w", encoding="utf-8") as fh:
        fh.write('{"as_of": "2026-07-01T14:01:00Z"}\n')
    assert not st.verify()["ok"]                                        # unmanifested file
    e = st.adopt(f"runs/{DATE}/20261012T122604Z_morning.json", "2026-07-01T14:01:00Z", "test")
    assert e["adopted"] == "test" and st.verify()["ok"]
    assert open(st.manifest_path, encoding="utf-8").read().startswith(before)   # only appended
    for bad in (f"runs/{DATE}/20261012T122604Z_morning.json", "nope.json", "MANIFEST.jsonl"):
        try:
            st.adopt(bad, "2026-07-01T14:01:00Z", "test")
            raise AssertionError(bad)
        except WriteOnceError:
            pass


def test_the_shadow_workflow_reads_current_main_and_refuses_a_stale_store():
    import yaml
    wf = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "research-mlb-pitcher-shadow.yml")))
    gate = wf["jobs"]["gate"]["steps"][0]["with"]
    assert gate["ref"] == "main"
    shadow = wf["jobs"]["shadow"]["steps"]
    ref = shadow[0]["with"]["ref"]
    # schedule/dispatch -> 'main'; pull_request -> its own merge ref (an `&& '' ||` form would always be 'main')
    assert ref.replace(" ", "") == "${{github.event_name!='pull_request'&&'main'||github.ref}}" 
    names = [s.get("name", "") for s in shadow]
    guard = next(i for i, n in enumerate(names) if n.startswith("Refuse to commit if the store moved"))
    commit = next(i for i, n in enumerate(names) if n.startswith("Commit shadow records"))
    assert guard == commit - 1 and "FETCH_HEAD:data/shadow_pure/mlb_pitcher" in shadow[guard]["run"]
