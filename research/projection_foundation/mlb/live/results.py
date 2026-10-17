"""Results stage: FINAL box scores -> outcome rows (sift.outcome.v1) for every archived forecast entity.

Rules
  * only games whose schedule status is Final are read; Postponed/Cancelled forecasts are voided (participated False,
    source not_played:<status>); anything else (live, delayed, suspended) stays pending;
  * a pitcher forecast is a STARTER forecast: participated = True only if he started (first pitcher used). A listed
    probable who did not appear is void (reason did_not_appear); one who appeared only in relief is void too
    (reason relief_appearance - the line is recorded but is not comparable with a start forecast);
  * a hitter forecast is for a posted-lineup starter: participated = True only if he started and had >= 1 PA;
  * the actual starters / lineup starters we did NOT forecast are recorded too (forecasted False) so the scorecard can count
    starter swaps and unforecast starters;
  * a later different final stat is written as a NEW outcome row; prospective.settle turns it into one correction record
    and never touches the original settlement.
"""
from __future__ import annotations

from . import statsapi as api

PITCH_STAT = {"batters_faced": "bf", "outs": "outs", "pitcher_strikeouts": "k", "pitcher_walks": "bb",
              "pitcher_hits_allowed": "h", "pitcher_runs_allowed": "r"}
HIT_STAT = {"plate_appearances": "PA", "hits": "H", "total_bases": "TB", "home_runs": "HR", "runs": "R", "rbis": "RBI",
            "hits_runs_rbis": "HRR"}
SOURCE = "mlb_statsapi_boxscore"


def _row(game, entity, family, actual, participated, reason, forecasted, source=SOURCE):
    return {"game_id": game["game_pk"], "entity_id": str(entity), "family": family, "actual": actual,
            "participated": bool(participated), "reason": reason, "forecasted": bool(forecasted), "final": True,
            "source": source, "game_date": game["official_date"]}


def void_rows(game, wanted, status):
    return [_row(game, e, f, None, False, f"not_played:{status}", True, source=f"not_played:{status}") for e, f in sorted(wanted)]


def outcome_rows(game: dict, box: dict, wanted: set[tuple[str, str]],
                 priority_families=("batters_faced", "outs", "pitcher_strikeouts")) -> list[dict]:
    """`wanted` = {(entity_id, family)} with an archived forecast for this game."""
    rows = []
    pit = {side: {p["player_id"]: p for p in box["pitchers"][side]} for side in ("away", "home")}
    bat = {side: {p["player_id"]: p for p in box["batters"][side]} for side in ("away", "home")}
    all_pit = {**pit["away"], **pit["home"]}
    all_bat = {**bat["away"], **bat["home"]}
    runs = {s: box["team"][s].get("runs") if box["team"][s].get("runs") is not None else game["runs"].get(s)
            for s in ("away", "home")}
    done = set()
    for entity, family in sorted(wanted):
        done.add((entity, family))
        if family in PITCH_STAT:
            p = all_pit.get(entity)
            if p is None:
                rows.append(_row(game, entity, family, None, False, "did_not_appear", True))
                continue
            v = p.get(PITCH_STAT[family])
            started = p["order"] == 0
            rows.append(_row(game, entity, family, v, started and v is not None,
                             None if started else "relief_appearance", True))
        elif family in HIT_STAT:
            b = all_bat.get(entity)
            if b is None:
                rows.append(_row(game, entity, family, None, False, "not_in_box", True))
                continue
            ok = b["is_starter"] and (b["PA"] or 0) > 0
            rows.append(_row(game, entity, family, b.get(HIT_STAT[family]), ok,
                             None if ok else ("no_plate_appearance" if b["is_starter"] else "not_a_starter"), True))
        elif family == "team_runs":
            side = "away" if entity == game["away"]["abbr"] else "home" if entity == game["home"]["abbr"] else None
            v = runs.get(side) if side else None
            rows.append(_row(game, entity, family, v, v is not None, None if v is not None else "no_team_runs", True))
        elif family == "game_total":
            v = None if runs["away"] is None or runs["home"] is None else runs["away"] + runs["home"]
            rows.append(_row(game, entity, family, v, v is not None, None if v is not None else "no_team_runs", True))
    # actual starters without a forecast (swaps / unforecast): priority families only
    for side in ("away", "home"):
        for p in box["pitchers"][side]:
            if p["order"] != 0:
                continue
            for fam in priority_families:
                if (p["player_id"], fam) not in done and p.get(PITCH_STAT[fam]) is not None:
                    rows.append(_row(game, p["player_id"], fam, p[PITCH_STAT[fam]], True, None, False))
    return rows


def changed_rows(new_rows: list[dict], latest_archived: dict[tuple, dict]) -> list[dict]:
    """Keep rows whose (actual, participated) differs from the latest archived observation of the same key."""
    keep = []
    for r in new_rows:
        old = latest_archived.get((r["game_id"], r["entity_id"], r["family"]))
        if old is None or old.get("participated") != r["participated"] or \
                (r["actual"] is not None and (old.get("actual") is None or abs(float(old["actual"]) - float(r["actual"])) > 1e-9)):
            keep.append(r)
    return keep
