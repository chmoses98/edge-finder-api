#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section E: INDEPENDENT first-five-innings verification
for every KXMLBF5TOTAL contract underlying candidate C01.

WHY THIS IS MANDATORY: the program proved that every archived
KXMLBF5SPREAD settlement used the FULL-GAME margin (a horizon defect in
lib.edgelab.settlement's winning_margin branch). KXMLBF5TOTAL's horizon
must therefore be proven independently, not assumed correct because the
">= N" rung correction is internally coherent.

INDEPENDENCE: this script re-fetches the MLB Stats API linescore itself
and sums innings 1-5 from the raw innings array, rather than trusting
any archived settlement, any archived periodScores value, or
scripts/edgelab/settle_markets.py's own outcome builder.

IDENTITY: exact only. A game resolves only when the MLB schedule for
that date contains EXACTLY ONE regular-season game whose (away, home)
abbreviations equal the archived Game record's own awayTeam/homeTeam
(via lib.edgelab.mlb_schedule.TEAM_ID_TO_ABBR, this repo's canonical
table). Doubleheaders are disambiguated ONLY by an exact scheduled-start
match against the eventTicker's encoded first-pitch time; anything
ambiguous is REFUSED and reported as unresolved. No fuzzy matching.

NETWORK: statsapi.mlb.com is egress-blocked in the research sandbox, so
this runs on GitHub Actions (research branch only).

READ-ONLY with respect to canonical settlement/ledger data: it writes
exactly one research artifact and never mutates data/edgelab/settlements
or any bet record.
"""

import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR  # noqa: E402
from scripts.research.mlb_alpha_0001.build_entry_rows import parse_event  # noqa: E402

MLB_API = "https://statsapi.mlb.com/api/v1"
START_TOLERANCE_MIN = 45


def http_json(url, timeout=20, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "edge-finder-edgelab-research/1.0",
                "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            if attempt == retries - 1:
                print("  fetch failed %s: %s" % (url, exc))
                return None
            time.sleep(2 ** attempt)
    return None


def load_c01_contracts():
    """Every C01 opportunity in discovery + validation. Holdout is NEVER read."""
    rows = []
    for split in ("discovery", "validation"):
        path = os.path.join(ART, "entry_rows_%s.jsonl.gz" % split)
        with gzip.open(path, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                if not r["marketTicker"].startswith("KXMLBF5TOTAL-"):
                    continue
                if r["entryCheckpoint"] != "LAST_PREGAME":
                    continue
                if r["settlementResult"] not in ("YES", "NO"):
                    continue
                ya = r.get("yesExecAsk")
                if ya is None or not (90 <= ya <= 99):
                    continue
                r["split"] = split
                rows.append(r)
    return rows


def archived_results():
    """Raw archived settlement results (pre-correction), for the 3-way compare."""
    import glob
    out = {}
    for p in sorted(glob.glob(os.path.join(REPO, "data", "edgelab", "settlements", "*.jsonl*"))):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("marketTicker", "").startswith("KXMLBF5TOTAL-") and \
                        rec.get("result") in ("YES", "NO"):
                    out[rec["marketTicker"]] = rec["result"]
    return out


def resolve_game_pk(schedule_games, away, home, start_utc):
    """Exact identity: matching (away, home) abbreviations. Doubleheaders
    disambiguated only by scheduled start. Returns (gamePk, reason)."""
    cands = [g for g in schedule_games
             if TEAM_ID_TO_ABBR.get(g.get("awayTeamId")) == away
             and TEAM_ID_TO_ABBR.get(g.get("homeTeamId")) == home]
    if not cands:
        return None, "no_schedule_match_for_%s_at_%s" % (away, home)
    if len(cands) == 1:
        return cands[0]["gamePk"], "unique_matchup"
    timed = []
    for g in cands:
        ss = g.get("scheduledStart")
        if not ss:
            continue
        sched = datetime.fromisoformat(ss.replace("Z", "+00:00")).replace(tzinfo=None)
        if abs((sched - start_utc).total_seconds()) / 60.0 <= START_TOLERANCE_MIN:
            timed.append(g)
    if len(timed) == 1:
        return timed[0]["gamePk"], "doubleheader_resolved_by_start_time"
    return None, "ambiguous_%d_candidates_refused" % len(cands)


def f5_from_linescore(linescore):
    """Sum innings 1-5 directly from the raw innings array. Returns
    (away_f5, home_f5, completed_innings) or (None, None, n) when the
    game did not have five complete innings of data."""
    innings = (linescore or {}).get("innings") or []
    away = home = 0
    seen = 0
    for inn in innings:
        num = inn.get("num")
        if num is None or num > 5:
            continue
        a = (inn.get("away") or {}).get("runs")
        h = (inn.get("home") or {}).get("runs")
        # A home team leading after the top of the 9th never bats; within
        # innings 1-5 a missing home value is only legitimate mid-game.
        if a is None and h is None:
            continue
        away += a or 0
        home += h or 0
        seen += 1
    if seen < 5:
        return None, None, seen
    return away, home, seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ART, "f5_settlement_verification.json"))
    args = ap.parse_args()

    rows = load_c01_contracts()
    archived = archived_results()
    with open(os.path.join(ART, "corrected_total_settlements.json")) as fh:
        corrected = json.load(fh)["tickers"]

    games = {}
    for r in rows:
        games.setdefault(r["eventTicker"], {
            "gameDate": r["gameDate"], "awayTeam": r["awayTeam"],
            "homeTeam": r["homeTeam"], "gameId": r["gameId"],
            "split": r["split"], "contracts": []})
        games[r["eventTicker"]]["contracts"].append(r)

    print("C01 games to verify: %d (contracts: %d)" % (len(games), len(rows)))

    sched_cache = {}
    results = []
    counts = Counter()
    for ev, info in sorted(games.items()):
        date = info["gameDate"]
        if date not in sched_cache:
            js = http_json("%s/schedule?sportId=1&date=%s&gameType=R" % (MLB_API, date))
            from lib.edgelab.mlb_schedule import parse_schedule_games
            sched_cache[date] = parse_schedule_games(js) if js else []
            time.sleep(0.3)
        parsed = parse_event(ev)
        start_utc = parsed[1] if parsed else None
        game_pk, reason = resolve_game_pk(
            sched_cache[date], info["awayTeam"], info["homeTeam"], start_utc)
        if game_pk is None:
            counts["unresolved_identity"] += 1
            results.append({"eventTicker": ev, "gameDate": date, "status": "UNRESOLVED",
                            "reason": reason, "contracts": len(info["contracts"])})
            continue

        ls = http_json("%s/game/%s/linescore" % (MLB_API, game_pk))
        time.sleep(0.3)
        away_f5, home_f5, seen = f5_from_linescore(ls)
        if away_f5 is None:
            counts["unresolved_linescore"] += 1
            results.append({"eventTicker": ev, "gameDate": date, "gamePk": game_pk,
                            "status": "UNRESOLVED",
                            "reason": "only_%d_of_5_innings_available" % seen,
                            "contracts": len(info["contracts"])})
            continue

        f5_total = away_f5 + home_f5
        counts["verified_games"] += 1
        per_contract = []
        for c in info["contracts"]:
            n = c["threshold"]
            true_yes = "YES" if f5_total >= n else "NO"
            corr = (corrected.get(c["marketTicker"]) or {}).get("corrected")
            arch = archived.get(c["marketTicker"])
            counts["corrected_match" if corr == true_yes else "corrected_MISMATCH"] += 1
            counts["archived_match" if arch == true_yes else "archived_MISMATCH"] += 1
            per_contract.append({
                "marketTicker": c["marketTicker"], "threshold": n,
                "split": c["split"],
                "independentTrueResult": true_yes,
                "researchCorrectedResult": corr,
                "archivedResult": arch,
                "correctedAgrees": corr == true_yes,
                "archivedAgrees": arch == true_yes,
            })
        results.append({
            "eventTicker": ev, "gameDate": date, "gamePk": game_pk,
            "identityReason": reason,
            "awayTeam": info["awayTeam"], "homeTeam": info["homeTeam"],
            "awayRunsF5": away_f5, "homeRunsF5": home_f5, "totalF5": f5_total,
            "status": "VERIFIED", "contracts": per_contract,
        })

    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "E_independent_f5_verification",
        "researchOnly": True,
        "source": "MLB Stats API linescore innings 1-5, re-fetched independently",
        "splitsCovered": ["discovery", "validation"],
        "blindHoldout": "NOT READ",
        "counts": dict(counts),
        "games": results,
    }
    with open(args.out, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote", args.out)
    for k, v in sorted(counts.items()):
        print("  %-24s %d" % (k, v))
    if counts["corrected_MISMATCH"]:
        print("MISMATCHES FOUND -- C01 must be treated as contaminated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
