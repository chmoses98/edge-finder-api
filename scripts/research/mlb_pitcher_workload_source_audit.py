#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_workload_source_audit.py
=====================================================
W1-D source audit for starter workload data (RESEARCH ONLY, read-only).

    python3 scripts/research/mlb_pitcher_workload_source_audit.py [--data-root data] [--out PATH]

Counts what the committed SPORTS datasets carry for official outs, batters faced,
strikeouts, pitch counts, start times, rest days, bullpen usage and lineup status,
by season, and audits the champion's 2026 postseason prior-start path, which takes
outs / strikeouts from settled Kalshi KS / OUTS markets
(scripts/research/mlb_pitcher_prop_calibration.load_settled_props / postseason_starts,
lib/research/mlb_player_props.postseason_starts_from_settlements). The market files
are opened here ONLY to audit that dependency; nothing in this script feeds a model.
Writes docs/research/pitcher_workload_source_audit.json (numbers quoted in
docs/research/PITCHER_WORKLOAD_SOURCE_AUDIT.md).
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import pitcher_workload_sources as S  # noqa: E402

POSTSEASON_START = "2026-09-29"


def _jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def regular_season_coverage(data_root):
    sched = S.load_schedule(data_root)
    games = S.load_pitching_games(data_root, sched)
    out = {}
    for yr in ("2022", "2023", "2024", "2025", "2026"):
        sched_r = [g for g in sched.values() if g["game_type"] == "R" and g["date"][:4] == yr]
        rows = [g for g in games if g["season"] == yr]
        st = [g["starter"] for g in rows if g.get("starter")]
        src = Counter(g["source"] for g in rows)
        out[yr] = {
            "schedule_regular_season_games": len(sched_r),
            "schedule_postponed_or_cancelled": sum(1 for g in sched_r if g["status"] in ("Postponed", "Cancelled")),
            "schedule_with_start_time_utc": sum(1 for g in sched_r if g.get("start_utc")),
            "games_with_pitching_lines": len({g["pk"] for g in rows}),
            "team_games_by_source": dict(src),
            "starter_lines": len(st),
            "starter_missing_outs": sum(1 for s in st if s.get("outs") is None),
            "starter_missing_bf": sum(1 for s in st if not s.get("bf")),
            "starter_missing_k": sum(1 for s in st if s.get("k") is None),
            "starter_missing_pitches": sum(1 for s in st if not s.get("pitches")),
            "date_range": [min(g["date"] for g in rows), max(g["date"] for g in rows)] if rows else None,
            "postseason_games_in_sports_caches": 0,
        }
    # postseason presence anywhere in the two sports caches
    types = Counter(g["game_type"] for g in sched.values())
    return out, dict(types), games


def statcast_vs_box(data_root, games):
    box = {(g["pk"], g["side"]): g["starter"] for g in games if g["source"].startswith("statsapi") and g["date"] >= "2026-08-11"}
    diff = defaultdict(Counter)
    n = 0
    for f in sorted(glob.glob(os.path.join(data_root, "statcast_raw", "games", "*.jsonl"))):
        pk = int(os.path.basename(f).split(".")[0])
        sc = S.statcast_lines(list(_jsonl(f)))
        for side in ("home", "away"):
            b = box.get((pk, side))
            if not b:
                continue
            n += 1
            sp = sc[f"{side}_sp"]
            ln = sc["lines"].get(sp, {})
            diff["starter_id"]["match" if sp == b["pid"] else "mismatch"] += 1
            for k in ("outs", "bf", "k", "pitches"):
                diff[k][str((ln.get(k) or 0) - (b[k] or 0))] += 1
    files = sorted(glob.glob(os.path.join(data_root, "statcast_raw", "games", "*.jsonl")))
    idx = [json.loads(x) for x in open(os.path.join(data_root, "statcast_raw", "index", "pitcher_games.jsonl"), encoding="utf-8")]
    return {"overlap_starts": n, "statcast_minus_box": {k: dict(v) for k, v in diff.items()},
            "statcast_game_files": len(files),
            "statcast_index_date_range": [min(r["gameDate"] for r in idx), max(r["gameDate"] for r in idx)]}


def settlement_postseason_path(data_root):
    """The champion's postseason observations: one row per (gamePk, pitcher) from settled
    pitcher KS / OUTS markets, with the evidence source and fetch time."""
    games = {}
    files = sorted(glob.glob(os.path.join(data_root, "edgelab", "settlements", "*.jsonl*")))
    for f in files:
        part = os.path.basename(f).split(".")[0]
        if part < "2026-09-28":
            continue
        for r in _jsonl(f):
            fam = r.get("marketFamily")
            if fam not in ("pitcher_outs", "pitcher_strikeouts") or r.get("result") not in ("YES", "NO"):
                continue
            ev = r.get("settlementEvidence") or {}
            cands = ev.get("candidates") or []
            if len(cands) != 1 or ev.get("actualValue") is None:
                continue
            m = re.match(r"^KXMLB(?:KS|OUTS)-(\d{2})([A-Z]{3})(\d{2})(\d{4})", r.get("marketTicker") or "")
            mon = {"SEP": 9, "OCT": 10, "NOV": 11}.get(m.group(2)) if m else None
            if not mon:
                continue
            gd = f"20{m.group(1)}-{mon:02d}-{m.group(3)}"
            if gd < POSTSEASON_START:
                continue
            key = (str(ev.get("gamePk") or r.get("gameId")), str(cands[0]["playerId"]))
            g = games.setdefault(key, {"pk": key[0], "pid": key[1], "date": gd, "outs": None, "k": None,
                                       "fetched_at": set(), "source_systems": set(), "endpoints": set(),
                                       "markets": Counter(), "partition_files": set()})
            g["outs" if fam == "pitcher_outs" else "k"] = ev["actualValue"]
            g["fetched_at"].add(ev.get("fetchedAt") or r.get("settledAt"))
            g["source_systems"].add(ev.get("sourceSystem"))
            g["endpoints"].add((ev.get("sourceEndpoint") or "").split("/game/")[0])
            g["markets"][fam] += 1
            g["partition_files"].add(part)
    rows = sorted(games.values(), key=lambda g: (g["date"], g["pk"], g["pid"]))
    lag = []
    for g in rows:
        first = min(x for x in g["fetched_at"] if x)
        g["first_fetched_at"] = first
        lag.append((date.fromisoformat(first[:10]) - date.fromisoformat(g["date"])).days)
    # as-of check of the champion's leave-future-out use: a postseason start dated D is used as
    # history for every later projected date D' > D, regardless of when it was fetched.
    by_pid = defaultdict(list)
    for g in rows:
        by_pid[g["pid"]].append(g)
    later_uses = late_uses = 0
    proj_dates = sorted({g["date"] for g in rows})
    for g in rows:
        if g["outs"] is None:
            continue
        for d in proj_dates:
            if d > g["date"]:
                later_uses += 1
                if g["first_fetched_at"][:10] >= d:
                    late_uses += 1
    with_outs = [g for g in rows if g["outs"] is not None]
    return {
        "settlement_partitions_scanned": [os.path.basename(f) for f in files if os.path.basename(f) >= "2026-09-28"],
        "postseason_pitcher_games": len(rows),
        "with_outs_from_OUTS_markets": len(with_outs),
        "with_k_from_KS_markets": sum(1 for g in rows if g["k"] is not None),
        "with_both": sum(1 for g in rows if g["outs"] is not None and g["k"] is not None),
        "with_batters_faced_or_pitches": 0,
        "evidence_source_systems": dict(Counter(s for g in rows for s in g["source_systems"])),
        "evidence_endpoints": sorted({e for g in rows for e in g["endpoints"]}),
        "fetch_lag_days_after_game_date": dict(Counter(lag)),
        "game_dates": dict(Counter(g["date"] for g in rows)),
        "slate_dates_x_prior_postseason_starts": later_uses,
        "of_which_first_fetched_on_or_after_projected_date": late_uses,
        "rows": [{k: (sorted(v) if isinstance(v, set) else (dict(v) if isinstance(v, Counter) else v))
                  for k, v in g.items()} for g in rows],
    }


def postseason_sports_context(data_root):
    """Which sports-only context exists for 2026 postseason games in committed files."""
    out = {"pipeline_normalized_slate_dates": [], "games": 0, "games_with_probable_pitchers": 0,
           "games_lineup_confirmed_flag": 0, "handicap_runtime_postseason_games": 0,
           "handicap_runtime_with_bullpen_recent_usage": 0}
    for f in sorted(glob.glob(os.path.join(data_root, "pipeline", "*", "normalized_slate.json"))):
        d = os.path.basename(os.path.dirname(f))
        if d < POSTSEASON_START:
            continue
        doc = json.load(open(f, encoding="utf-8"))
        gs = (doc.get("data") or {}).get("games") or []
        out["pipeline_normalized_slate_dates"].append(d)
        for g in gs:
            out["games"] += 1
            if (g.get("away") or {}).get("pitcher", {}).get("id") and (g.get("home") or {}).get("pitcher", {}).get("id"):
                out["games_with_probable_pitchers"] += 1
            if g.get("lineupConfirmed"):
                out["games_lineup_confirmed_flag"] += 1
    for f in sorted(glob.glob(os.path.join(data_root, "handicap_runtime", "*", "games", "*.json"))):
        d = f.split(os.sep)[-3]
        if d < POSTSEASON_START:
            continue
        out["handicap_runtime_postseason_games"] += 1
        doc = json.load(open(f, encoding="utf-8"))
        if (((doc.get("context") or {}).get("away") or {}).get("bullpen") or {}).get("recentUsage", {}).get("dataAvailable"):
            out["handicap_runtime_with_bullpen_recent_usage"] += 1
    return out


def lineup_status_coverage(data_root):
    dates = sorted(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(data_root, "pipeline", "*", "normalized_slate.json")))
    return {"pipeline_slate_dates": len(dates), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "note": "No lineup-confirmation status or lineup timestamps exist for 2022-2025 in committed data."}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "research", "pitcher_workload_source_audit.json"))
    a = ap.parse_args(argv)
    cov, types, games = regular_season_coverage(a.data_root)
    rep = {"regular_season_coverage": cov, "schedule_game_types_in_sports_cache": types,
           "statcast_reconstruction_vs_boxscore": statcast_vs_box(a.data_root, games),
           "champion_postseason_settlement_path": settlement_postseason_path(a.data_root),
           "postseason_sports_context": postseason_sports_context(a.data_root),
           "lineup_status": lineup_status_coverage(a.data_root)}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    brief = {k: v for k, v in rep.items() if k != "champion_postseason_settlement_path"}
    ps = {k: v for k, v in rep["champion_postseason_settlement_path"].items() if k != "rows"}
    print(json.dumps({**brief, "champion_postseason_settlement_path": ps}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
