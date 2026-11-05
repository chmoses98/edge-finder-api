"""
tests/test_research_export_team_form.py
=======================================
Regression tests for the Edge Finder App Export failure that began 2026-10-18 14:59Z: every research
(explorer) export with a published postseason event died in scripts/research_export.py _model_inputs with

    AttributeError: 'NoneType' object has no attribute 'get'

because a slate team's ``offenseForm.windows`` carried ``null`` for a window the team could not fill
(lib/team_offense_form.build_form: "a window with fewer completed games than it needs returns None").
The fixture holds the exact records, trimmed verbatim from data/slates/<date>/authoritative.json:
  all_windows_null  2026-10-27 World Series MIL @ TB (gamePk 849803): L5, L7, L10 all null (2 games on record)
  mixed_windows     2026-10-23 World Series TB @ MIL (gamePk 849805): L10 null for TB; L7 and L10 null for MIL
  full_windows      2026-10-17 ALCS TB @ CLE (gamePk 849816): the last event the explorer published (all filled)
A null window must stay null (never an empty or invented profile), be listed with the count that kept it
empty, and say so in the event notes; a filled window keeps exactly the fields it always had.
"""
import glob
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


research_export = _load("research_export")

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "research_export_team_form_postseason.json")
WINDOW_FIELDS = {"games", "mean", "median", "min", "max", "ewma", "trimmedMean", "marketRelative", "outlierDependence"}
LEGACY_KEYS = {"windows", "form_label", "as_of_date", "analysis_only"}


@pytest.fixture(scope="module")
def cases():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _inputs(case):
    return research_export._model_inputs(case["game"], case["slate_date"])


def _source_form(case, side):
    return case["game"][f"{side}TeamStats"]["offenseForm"]


# 1. the exact production record: every window null -> no crash, every window null in the output, each one counted
def test_all_null_windows_export_as_null_with_a_counted_reason(cases):
    mi = _inputs(cases["all_windows_null"])
    assert mi["game_pk"] == 849803
    for side, team in (("away", "MIL"), ("home", "TB")):
        tf = mi[side]["team_form"]
        assert mi[side]["team"] == team
        assert tf["windows"] == {"L5": None, "L7": None, "L10": None}
        assert tf["games_available"] == 2
        assert tf["unavailable_windows"] == {
            k: {"reason": "INSUFFICIENT_COMPLETED_GAMES", "games_available": 2, "games_required": n}
            for k, n in (("L5", 5), ("L7", 7), ("L10", 10))
        }
        assert tf["form_label"] == {"label": "UNKNOWN", "reason": "no L7 window available", "window": "L7"}
        assert tf["as_of_date"] == "2026-10-26" and tf["analysis_only"] is True


# 2. a team with some windows filled: filled windows are the exact projection of the source, null ones stay null
def test_mixed_windows_keep_filled_profiles_and_list_only_the_null_ones(cases):
    case = cases["mixed_windows"]
    mi = _inputs(case)
    for side in ("away", "home"):
        src = _source_form(case, side)["windows"]
        tf = mi[side]["team_form"]
        assert set(tf["windows"]) == set(src)
        for k, v in src.items():
            if v is None:
                assert tf["windows"][k] is None
                assert tf["unavailable_windows"][k]["games_required"] == int(k[1:])
            else:
                assert tf["windows"][k] == {x: v.get(x) for x in WINDOW_FIELDS}
                assert k not in tf["unavailable_windows"]
        assert sorted(tf["unavailable_windows"]) == sorted(k for k, v in src.items() if v is None)
    assert sorted(mi["away"]["team_form"]["unavailable_windows"]) == ["L10"]          # TB, 7 games
    assert sorted(mi["home"]["team_form"]["unavailable_windows"]) == ["L10", "L7"]    # MIL, 6 games
    assert mi["home"]["team_form"]["windows"]["L5"]["mean"] == 5.2


# 3. a fully filled team (the last event the explorer published) keeps its shape; the additions are additive only
def test_full_windows_keep_the_published_shape(cases):
    mi = _inputs(cases["full_windows"])
    for side in ("away", "home"):
        tf = mi[side]["team_form"]
        assert LEGACY_KEYS <= set(tf)
        assert set(tf) - LEGACY_KEYS == {"games_available", "unavailable_windows"}
        assert tf["unavailable_windows"] == {}
        for v in tf["windows"].values():
            assert set(v) == WINDOW_FIELDS


# 4. the event says so in words, once per affected team, and says nothing when every window is filled
def test_event_notes_name_the_unavailable_windows(cases):
    notes = research_export._team_form_notes(_inputs(cases["all_windows_null"]))
    assert notes == [
        "Team form (MIL): L5, L7, L10 unavailable (2 completed games on record); those windows are null, not estimated.",
        "Team form (TB): L5, L7, L10 unavailable (2 completed games on record); those windows are null, not estimated.",
    ]
    assert research_export._team_form_notes(_inputs(cases["full_windows"])) == []


# 5. not a mask: a malformed window (neither a profile nor the contract's null) still fails loudly
def test_a_malformed_window_is_not_swallowed(cases):
    game = json.loads(json.dumps(cases["full_windows"]["game"]))
    game["awayTeamStats"]["offenseForm"]["windows"]["L7"] = "garbage"
    with pytest.raises(AttributeError):
        research_export._model_inputs(game, "2026-10-17")


# 6. a team with no offenseForm at all keeps its existing (empty) behaviour
def test_missing_offense_form_is_unchanged(cases):
    game = json.loads(json.dumps(cases["full_windows"]["game"]))
    game["homeTeamStats"]["offenseForm"] = None
    tf = research_export._model_inputs(game, "2026-10-17")["home"]["team_form"]
    assert tf["windows"] == {} and tf["unavailable_windows"] == {} and tf["form_label"] is None


# 7. end to end on the committed corpus: the 2026-10-27 World Series slate that failed in production now exports
def _corpus_present():
    need = [os.path.join(ROOT, "data", "slates", "2026-10-27", "authoritative.json"),
            os.path.join(ROOT, "data", "research_cache", "bullpen_backtest", "2026", "schedules")]
    return all(os.path.exists(p) for p in need)


@pytest.mark.skipif(not _corpus_present(), reason="committed data corpus not present in this checkout")
def test_the_failing_world_series_slate_exports(tmp_path):
    from edge_finder_contract import publish
    from edge_finder_contract import research as R
    app_export = _load("app_export")
    data = os.path.join(ROOT, "data")
    root = tmp_path / "latest"
    assert app_export.main(["--out", str(root), "--data-root", data, "--now", "2026-10-28T03:56:00Z", "--date", "2026-10-27"]) == 0
    research_export.export_explorer(str(root), data_root=data, slate_range=("2026-10-27", "2026-10-27"), seasons=["2026"],
                                    statcast_range=("2026-09-20", "2026-09-27"))
    assert R.verify_explorer(root) == [] and publish.verify_published(root) == []
    docs = []
    for path in glob.glob(os.path.join(str(root), "explorer", "events", "*.json")):
        with open(path, encoding="utf-8") as fh:
            docs.append(json.load(fh))
    assert len(docs) == 1
    mi = docs[0]["extensions"]["model_inputs"]
    assert mi["game_pk"] == 849803
    assert {mi["away"]["team"], mi["home"]["team"]} == {"MIL", "TB"}
    for side in ("away", "home"):
        assert mi[side]["team_form"]["windows"] == {"L5": None, "L7": None, "L10": None}
    assert sum("Team form (" in n for n in docs[0]["context"]["notes"]) == 2
