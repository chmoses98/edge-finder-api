#!/usr/bin/env python3
"""
scripts/research/mlb_postseason_coverage_report.py
==================================================
W2 Phase 3 (RESEARCH ONLY): verify the sports-source postseason pitcher cache
written by scripts/research/mlb_postseason_boxscore_backfill.py.

Checks (every number in docs/research/PITCHER_POSTSEASON_COVERAGE.md comes from
the JSON this writes):

1. Completeness: every FINAL game in the official StatsAPI postseason schedule
   snapshot has a cached record; per-season and per-series game counts; games
   not final (scheduled / in progress) listed separately.
2. Line integrity: each side has a starter (orderIndex 0) with outs, BF, K and
   pitches; team outs per side; feed/live vs /boxscore endpoint agreement.
3. Identity: every postseason pitcher MLBAM id vs the same season's
   regular-season sports caches (starter_workload box scores, Statcast pitch
   logs): id present, name equal, team consistent with the last regular-season
   appearance.
4. Kalshi settlement cross-check (CHECK ONLY, never an input): the postseason
   pitcher-games whose settled KS/OUTS markets carry settlementEvidence values
   are joined on (gamePk, playerId) and compared with the cached outs / K.
5. Timestamps: fetched_at > game end for every record; game end > first pitch;
   the as-of rule for any later projection is `gameEnd < projection cutoff`.

    python3 scripts/research/mlb_postseason_coverage_report.py [--out docs/research/pitcher_postseason_coverage.json]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SEASONS = (2022, 2023, 2024, 2025, 2026)
FINAL = {"Final", "Game Over", "Completed Early"}
PITCHER_FAMILIES = ("pitcher_outs", "pitcher_strikeouts")


def _jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _norm(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(n.replace(".", " ").split())


def load_postseason(data_root, season):
    base = os.path.join(data_root, "research_cache", "starter_workload", str(season))
    sp = os.path.join(base, "postseason_schedule.json")
    bp = os.path.join(base, "postseason_boxscores.jsonl.gz")
    sched = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else None
    recs = list(_jsonl(bp)) if os.path.exists(bp) else []
    return sched, recs


def regular_season_identity(data_root, season):
    """pid -> {name, last_date, last_team_id} from the regular-season box cache + Statcast ids."""
    team_of = {}
    for f in glob.glob(os.path.join(data_root, "research_cache", "bullpen_backtest", str(season), "schedules", "*.json")):
        for d in json.load(open(f, encoding="utf-8")).get("dates", []):
            for g in d.get("games", []):
                t = g.get("teams") or {}
                team_of[g["gamePk"]] = (d["date"], ((t.get("away") or {}).get("team") or {}).get("id"),
                                        ((t.get("home") or {}).get("team") or {}).get("id"))
    ids = {}
    p = os.path.join(data_root, "research_cache", "starter_workload", str(season), "boxscores.jsonl.gz")
    if os.path.exists(p):
        for r in _jsonl(p):
            date, away, home = team_of.get(r["gamePk"], (None, None, None))
            for side, tid in (("away", away), ("home", home)):
                for ln in r.get(f"{side}Pitchers") or []:
                    cur = ids.setdefault(str(ln["playerId"]), {"name": ln.get("name"), "last_date": "", "last_team_id": None})
                    if date and date >= cur["last_date"]:
                        cur.update(last_date=date, last_team_id=tid, name=ln.get("name") or cur["name"])
    sc_ids = set()
    for f in glob.glob(os.path.join(data_root, "statcast_raw", "games", "*.jsonl")):
        first = None
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    first = json.loads(line)
                    break
        if not first or str(first.get("gameDate", ""))[:4] != str(season):
            continue
        for r in _jsonl(f):
            if r.get("pitcherId"):
                sc_ids.add(str(r["pitcherId"]))
    return ids, sc_ids


_ID_CACHE = {}


def _ids_for(data_root, season):
    if season not in _ID_CACHE:
        ids, sc = regular_season_identity(data_root, season)
        _ID_CACHE[season] = set(ids) | sc
    return _ID_CACHE[season]


def settled_postseason_values(data_root):
    """(gamePk, playerId) -> {pitcher_outs|pitcher_strikeouts: actualValue} from settlement evidence of
    settled Kalshi pitcher markets dated in the 2026 postseason. CHECK ONLY."""
    out, meta = {}, {}
    for f in sorted(glob.glob(os.path.join(data_root, "edgelab", "settlements", "*.jsonl*"))):
        if os.path.basename(f) < "2026-09-28":
            continue
        for r in _jsonl(f):
            if r.get("marketFamily") not in PITCHER_FAMILIES:
                continue
            ev = r.get("settlementEvidence") or {}
            c = ev.get("candidates") or []
            if len(c) != 1 or ev.get("actualValue") is None:
                continue
            key = (int(ev.get("gamePk") or r.get("gameId")), str(c[0]["playerId"]))
            out.setdefault(key, {})[r["marketFamily"]] = ev["actualValue"]
            meta[key] = {"fetchedAt": ev.get("fetchedAt"), "file": os.path.basename(f)}
    return out, meta


def series_key(g):
    ids = sorted([g["away"]["id"], g["home"]["id"]]) if "away" in g else sorted([g["awayTeam"]["id"], g["homeTeam"]["id"]])
    return f"{g['gameType']}:{g.get('seriesDescription')}:{ids[0]}-{ids[1]}"


def build(data_root):
    report = {"generated_from": "data/research_cache/starter_workload/<season>/postseason_{schedule.json,boxscores.jsonl.gz}",
              "seasons": {}, "identity": {}, "kalshi_crosscheck": {}, "timestamps": {}}
    all_recs = {}
    for season in SEASONS:
        sched, recs = load_postseason(data_root, season)
        if sched is None:
            report["seasons"][str(season)] = {"status": "NO_SCHEDULE_SNAPSHOT"}
            continue
        games = sched["games"]
        final = [g for g in games if g.get("detailedState") in FINAL]
        by_pk = {r["gamePk"]: r for r in recs}
        all_recs.update({(season, k): v for k, v in by_pk.items()})
        missing = [g["gamePk"] for g in final if g["gamePk"] not in by_pk]
        extra = [pk for pk in by_pk if pk not in {g["gamePk"] for g in final}]
        series = defaultdict(lambda: {"schedule_final": 0, "cached": 0, "not_final": 0, "dates": []})
        for g in games:
            s = series[series_key(g)]
            if g.get("detailedState") in FINAL:
                s["schedule_final"] += 1
                s["cached"] += g["gamePk"] in by_pk
                s["dates"].append(g["officialDate"])
            else:
                s["not_final"] += 1
        for s in series.values():
            s["dates"] = [min(s["dates"]), max(s["dates"])] if s["dates"] else []
        integrity = Counter()
        anomalies = []
        for r in recs:
            for side in ("away", "home"):
                ps = r.get(f"{side}Pitchers") or []
                st = ps[0] if ps else {}
                ok = bool(ps) and st.get("orderIndex") == 0 and all(
                    st.get(k) is not None for k in ("outs", "battersFaced", "strikeOuts", "numberOfPitches"))
                integrity["starter_complete" if ok else "starter_incomplete"] += 1
                team_outs = sum(p.get("outs") or 0 for p in ps)
                if team_outs < 24 and not (r.get("detailedState") == "Completed Early"):
                    anomalies.append({"gamePk": r["gamePk"], "side": side, "team_outs": team_outs})
                integrity["pitcher_lines"] += len(ps)
            integrity["endpoint_agree" if r.get("boxscoreEndpointAgreement") else
                      ("endpoint_disagree" if r.get("boxscoreEndpointAgreement") is False else "endpoint_missing")] += 1
        report["seasons"][str(season)] = {
            "schedule_fetched_at": sched.get("fetched_at"), "schedule_source_url": sched.get("source_url"),
            "schedule_games": len(games), "schedule_by_state": dict(Counter(g.get("detailedState") for g in games)),
            "schedule_final": len(final), "cached_games": len(recs), "missing_final_games": missing,
            "cached_not_in_final_schedule": extra,
            "by_game_type": {gt: {"final": sum(1 for g in final if g["gameType"] == gt),
                                  "cached": sum(1 for r in recs if r["gameType"] == gt)} for gt in ("F", "D", "L", "W")},
            "series": dict(sorted(series.items())),
            "integrity": dict(integrity), "team_outs_lt24_anomalies": anomalies,
            "latest_final_game_date": max((g["officialDate"] for g in final), default=None),
            "complete": not missing and integrity.get("starter_incomplete", 0) == 0,
        }
        # identity
        ids, sc_ids = regular_season_identity(data_root, season)
        idc = Counter()
        mism = []
        for r in recs:
            for side in ("away", "home"):
                tid = r[f"{side}Team"]["id"]
                for ln in r.get(f"{side}Pitchers") or []:
                    pid = str(ln["playerId"])
                    idc["pitcher_lines"] += 1
                    reg = ids.get(pid)
                    if reg is None and pid not in sc_ids:
                        idc["not_in_regular_season_cache"] += 1
                        mism.append({"gamePk": r["gamePk"], "pid": pid, "name": ln.get("name"), "issue": "absent_from_regular_season_cache"})
                        continue
                    idc["id_found"] += 1
                    if reg is not None:
                        if _norm(reg["name"]) != _norm(ln.get("name")):
                            idc["name_mismatch"] += 1
                            mism.append({"gamePk": r["gamePk"], "pid": pid, "issue": "name_mismatch",
                                         "postseason": ln.get("name"), "regular": reg["name"]})
                        if reg["last_team_id"] is not None and reg["last_team_id"] != tid:
                            idc["team_mismatch_vs_last_box_appearance"] += 1
                            mism.append({"gamePk": r["gamePk"], "pid": pid, "issue": "team_differs_from_last_regular_box",
                                         "postseason_team": tid, "regular_team": reg["last_team_id"], "regular_last_date": reg["last_date"]})
                    else:
                        idc["found_in_statcast_only"] += 1
        # an id absent from this season's regular-season caches: is it a known MLBAM id in another season?
        for m in mism:
            if m["issue"] == "absent_from_regular_season_cache":
                m["seen_in_other_regular_seasons"] = [s for s in SEASONS if s != season and m["pid"] in _ids_for(data_root, s)]
        report["identity"][str(season)] = {"counts": dict(idc), "issues": mism}
    # Kalshi cross-check (check only)
    vals, meta = settled_postseason_values(data_root)
    rows = []
    for (pk, pid), v in sorted(vals.items()):
        rec = all_recs.get((2026, pk))
        line, role = None, None
        if rec:
            for side in ("away", "home"):
                for ln in rec.get(f"{side}Pitchers") or []:
                    if str(ln["playerId"]) == pid:
                        line, role = ln, ("starter" if ln["orderIndex"] == 0 else "reliever")
        row = {"gamePk": pk, "pid": pid, "in_cache": line is not None, "role": role,
               "settlement_file": meta[(pk, pid)]["file"], "settlement_fetchedAt": meta[(pk, pid)]["fetchedAt"]}
        for fam, key in (("pitcher_outs", "outs"), ("pitcher_strikeouts", "strikeOuts")):
            if fam in v:
                row[fam] = {"settlement": v[fam], "boxscore": line.get(key) if line else None,
                            "agree": (line is not None and line.get(key) == v[fam])}
        rows.append(row)
    agg = {}
    for fam in PITCHER_FAMILIES:
        rs = [r[fam] for r in rows if fam in r]
        agg[fam] = {"n": len(rs), "in_cache": sum(1 for r in rs if r["boxscore"] is not None),
                    "agree": sum(1 for r in rs if r["agree"])}
    report["kalshi_crosscheck"] = {"note": "CHECK ONLY: settlement evidence is never a model input or label here",
                                   "pitcher_games": len(rows), "in_cache": sum(r["in_cache"] for r in rows),
                                   "roles": dict(Counter(r["role"] for r in rows)), "by_family": agg,
                                   "disagreements": [r for r in rows if any(fam in r and not r[fam]["agree"] for fam in PITCHER_FAMILIES)],
                                   "rows": rows}
    # timestamps
    ts = Counter()
    lag_h, dur_min = [], []
    for (season, pk), r in sorted(all_recs.items()):
        end, first, fa = _t(r["gameEnd"]), _t(r["firstPitch"]) if r.get("firstPitch") else None, _t(r["fetched_at"])
        ts["fetched_after_game_end"] += fa > end
        ts["fetched_not_after_game_end"] += not (fa > end)
        if first:
            ts["end_after_first_pitch"] += end > first
            dur_min.append((end - first).total_seconds() / 60)
        for s in r.get("sources", []):
            ts["sources_with_url_and_fetched_at"] += bool(s.get("url") and s.get("fetched_at"))
        lag_h.append((fa - end).total_seconds() / 3600)
    dur_min.sort()
    lag_h.sort()
    report["timestamps"] = {"counts": dict(ts), "records": len(all_recs),
                            "game_minutes_first_pitch_to_end": {"min": round(dur_min[0], 1), "median": round(dur_min[len(dur_min) // 2], 1),
                                                                "max": round(dur_min[-1], 1)} if dur_min else None,
                            "fetch_lag_hours_after_end": {"min": round(lag_h[0], 2), "max": round(lag_h[-1], 2)} if lag_h else None,
                            "as_of_rule": "a cached line may enter a projection only if gameEnd < projection cutoff (as_of); "
                                          "fetched_at is the archive time, not an availability time (a backfill fetches long after the game)"}
    report["complete_all_seasons"] = all(v.get("complete") for v in report["seasons"].values())
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "research", "pitcher_postseason_coverage.json"))
    a = ap.parse_args(argv)
    rep = build(a.data_root)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
        fh.write("\n")
    summ = {s: {k: v.get(k) for k in ("schedule_final", "cached_games", "missing_final_games", "complete", "latest_final_game_date")}
            for s, v in rep["seasons"].items()}
    print(json.dumps({"seasons": summ, "kalshi": rep["kalshi_crosscheck"]["by_family"],
                      "timestamps": rep["timestamps"]["counts"], "complete_all_seasons": rep["complete_all_seasons"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
