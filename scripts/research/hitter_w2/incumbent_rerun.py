#!/usr/bin/env python3
"""
scripts/research/hitter_w2/incumbent_rerun.py
=============================================
SIFT W2 hitter lane -- RESEARCH ONLY. Re-runs the INCUMBENT hitter engine
(docs/HITTER_SIMULATION_ENGINE.md) on the FULL eligible population: every
confirmed-lineup starter in every game whose official lineup was posted in a
pregame slate capture -- not only hitters that happened to have a Kalshi
contract.

Why a rerun is needed: production (scripts/build_hitter_projection_board.py ->
lib.research.hitter_board_builder.build_hitter_projection_rows) only simulates a
hitter when `match_real_contracts_for_hitter` finds a Kalshi contract
("NO_ARCHIVED_CONTRACTS" otherwise), and only for games that have Kalshi hitter
markets at all (`_build_rows_for_game` returns early). The archived snapshots
therefore cover a market-selected population and only at market thresholds.

Fidelity: the per-hitter inputs are built with the SAME functions production
uses (build_hitter_feature_context, _build_team_other_hitter_rates,
_hitter_entry from scripts/build_hitter_projection_board.py; the
derive_pa_outcomes_by_pitch_family / derive_pitcher_pitch_mix /
live_simulation_resample_targets calls of build_hitter_projection_rows), read
from the pregame slate capture (data/slates/<date>/*.json, capture time from
the file name, strictly before scheduled first pitch, lineupConfirmedOfficial
on that side) and the Statcast archive as-of the slate date (exclusive).
Deviations, all documented in docs/research/HITTER_ENGINE_AUDIT.md:
  * weather: data/weather.json has no history in git (3 commits), so
    weather_by_team=None (no wind; production down-weights wind 0.3x);
  * savant batters / bat tracking are not passed: they do not reach the
    simulator's kwargs (verified: _hitter_entry never reads them);
  * the game simulation is an INSTRUMENTED REPLICA of
    lib.research.lineup_game_simulator.simulate_game that returns the same
    stat line plus decomposition counters (PA vs starter / bullpen, runners on
    base at each target PA). tests/research/test_hitter_w2_engine.py proves the
    replica's stat lines equal the original's for identical seeds.
The slate game records carry odds/Kalshi fields; nothing here reads them
(the engine code path never accesses those keys -- see the audit doc).
"""
import argparse
import glob
import gzip
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.research import lineup_game_simulator as LGS  # noqa: E402
from lib.research.hitter_market_distributions import build_hitter_market_distributions  # noqa: E402
from lib.research.hitter_pa_outcome_model import live_simulation_resample_targets  # noqa: E402
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family  # noqa: E402
from lib.research.pitch_environment_model import derive_pitcher_pitch_mix  # noqa: E402
from lib.research.hitter_feature_context import build_hitter_feature_context  # noqa: E402
from lib.research.statcast_pitch_store import load_pitches_for_batter  # noqa: E402
from lib.research.defense_store import latest_snapshot as latest_defense_snapshot  # noqa: E402
from lib.research.sprint_speed_store import latest_snapshot as latest_sprint_speed_snapshot  # noqa: E402
from lib.research.catcher_framing_store import latest_snapshot as latest_catcher_framing_snapshot  # noqa: E402
import scripts.build_hitter_projection_board as B  # noqa: E402

SLATES = os.path.join(_ROOT, "data", "slates")
STATS = ("PA", "H", "TB", "HR", "RBI", "R", "BB", "K", "1B", "2B", "3B")


def capture_time(path, doc):
    m = re.search(r"_(\d{8}T\d{6})Z\.json$", path)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    t = doc.get("lastRerunAt") or doc.get("_officialRunAt")
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def pregame_confirmed_sides(date_str):
    """{(gameId, side): (capture_dt, file, game_record)} -- latest capture strictly before first
    pitch in which that side's lineupConfirmedOfficial is true with 9 batters."""
    best = {}
    for f in sorted(glob.glob(os.path.join(SLATES, date_str, "*.json"))):
        with open(f) as fh:
            doc = json.load(fh)
        ct = capture_time(f, doc)
        if ct is None:
            continue
        for g in doc.get("games") or []:
            st = g.get("startTime")
            if not st or ct >= _parse(st):
                continue
            for side in ("away", "home"):
                ts = g.get(side + "TeamStats") or {}
                if not ts.get("lineupConfirmedOfficial") or len(ts.get("confirmedLineup") or []) != 9:
                    continue
                key = (str(g.get("gameId")), side)
                if key not in best or best[key][0] < ct:
                    best[key] = (ct, os.path.basename(f), g)
    return best


# ---- instrumented replica of lib.research.lineup_game_simulator.simulate_game ----------------
def simulate_game_instrumented(target_slot, target_hitter_pitches, batter_hand, starter_context, bullpen_context,
                               starter_pitch_mix, bullpen_pitch_mix, park_geometry_entry, field_relative_wind,
                               defense_snapshot, hitter_speed_snapshot, rng, other_hitter_rates=None, n_innings=9,
                               resample_targets=None):
    """Line-for-line replica of LGS.simulate_game (same RNG call order) plus counters."""
    other_hitter_rates = other_hitter_rates or [LGS.LEAGUE_PRIOR_RATES] * 9
    stats = {k: 0 for k in ("PA", "AB", "1B", "2B", "3B", "HR", "BB", "HBP", "K", "OUT", "RBI", "R")}
    ins = {"PA_starter": 0, "H_starter": 0, "PA_bullpen": 0, "H_bullpen": 0, "ROB": 0, "ROB2": 0, "ROB3": 0,
           "RBI_nonHR": 0, "team_runs": 0}
    starter_innings_pitched = 0.0
    starter_active = True
    avg_ip = starter_context.get("avgIPperStart") if starter_context else None
    target_slot_idx = target_slot - 1
    lineup_index = 0
    for inning in range(1, n_innings + 1):
        if inning > 1 and starter_active:
            if not LGS.should_starter_continue(starter_innings_pitched, avg_ip, rng):
                starter_active = False
        bases = [None, None, None]
        outs = 0
        while outs < 3:
            slot = lineup_index % 9
            is_target = (slot == target_slot_idx)
            if is_target:
                pitch_mix = starter_pitch_mix if starter_active else bullpen_pitch_mix
                ins["ROB"] += sum(1 for b in bases if b is not None)
                ins["ROB2"] += int(bases[1] is not None)
                ins["ROB3"] += int(bases[2] is not None)
                outcome = LGS._resolve_target_pa(
                    target_hitter_pitches, pitch_mix, batter_hand, park_geometry_entry,
                    field_relative_wind, defense_snapshot, hitter_speed_snapshot, rng,
                    resample_targets=resample_targets,
                )
                batter_tag = "TARGET"
                k = "starter" if starter_active else "bullpen"
                ins["PA_" + k] += 1
                if outcome in LGS.HIT_CATEGORIES:
                    ins["H_" + k] += 1
            else:
                outcome = LGS._categorical_draw(other_hitter_rates[slot], rng)
                batter_tag = "OTHER"
            if is_target:
                stats["PA"] += 1
                if outcome not in ("BB", "HBP"):
                    stats["AB"] += 1
            if outcome in LGS.TERMINAL_OUT_CATEGORIES:
                outs += 1
                if is_target:
                    stats[outcome] += 1
                lineup_index += 1
                continue
            bases, runs = LGS._advance_bases(bases, batter_tag, outcome)
            ins["team_runs"] += len(runs)
            for tag in runs:
                if tag == "TARGET":
                    stats["R"] += 1
            if is_target:
                stats[outcome] += 1
                stats["RBI"] += len(runs)
                if outcome != "HR":
                    ins["RBI_nonHR"] += len(runs)
            lineup_index += 1
        if starter_active:
            starter_innings_pitched += 1.0
    stats["H"] = stats["1B"] + stats["2B"] + stats["3B"] + stats["HR"]
    stats["TB"] = stats["1B"] + 2 * stats["2B"] + 3 * stats["3B"] + 4 * stats["HR"]
    return stats, ins


def run_hitter(kwargs, n_sims, seed):
    """Mirrors build_hitter_projection_rows' distribution call (after its market gate)."""
    raw_pitches = kwargs["raw_pitches"]
    hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(raw_pitches) if raw_pitches else {}
    starter_pitch_mix = None
    if kwargs["starter_pitches"]:
        mix_result = derive_pitcher_pitch_mix(kwargs["starter_pitches"], batter_hand=kwargs["batter_hand"])
        if mix_result["status"] != "MISSING_DATA":
            starter_pitch_mix = mix_result["mix"]
    resample_targets = live_simulation_resample_targets(
        hitter_pa_by_family or {}, kwargs["season_stats"] or {}, pitcher_pitch_mix=starter_pitch_mix,
        platoon_context=kwargs["platoon_context"], season_woba=kwargs["season_woba"],
        starter_context=kwargs["starter_context"] or {},
    )
    master_rng = random.Random(seed)
    lines, inss = [], []
    for _ in range(n_sims):
        game_rng = random.Random(master_rng.randrange(2 ** 32))
        s, ins = simulate_game_instrumented(
            target_slot=kwargs["target_slot"], target_hitter_pitches=raw_pitches or [],
            batter_hand=kwargs["batter_hand"], starter_context=kwargs["starter_context"] or {},
            bullpen_context=kwargs["bullpen_context"] or {}, starter_pitch_mix=starter_pitch_mix,
            bullpen_pitch_mix=None, park_geometry_entry=kwargs["park_geometry_entry"],
            field_relative_wind=kwargs["field_relative_wind"], defense_snapshot=kwargs["defense_snapshot"],
            hitter_speed_snapshot=kwargs["hitter_speed_snapshot"], rng=game_rng,
            other_hitter_rates=kwargs["other_hitter_rates"], resample_targets=resample_targets,
        )
        lines.append(s)
        inss.append(ins)
    return lines, inss, starter_pitch_mix, hitter_pa_by_family


def reference_distributions(kwargs, n_sims, seed):
    """The production distribution call itself (used by the replica-equality check)."""
    raw_pitches = kwargs["raw_pitches"]
    hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(raw_pitches) if raw_pitches else {}
    starter_pitch_mix = None
    if kwargs["starter_pitches"]:
        mix_result = derive_pitcher_pitch_mix(kwargs["starter_pitches"], batter_hand=kwargs["batter_hand"])
        if mix_result["status"] != "MISSING_DATA":
            starter_pitch_mix = mix_result["mix"]
    return build_hitter_market_distributions(
        n_sims=n_sims, target_slot=kwargs["target_slot"], target_hitter_pitches=raw_pitches or [],
        batter_hand=kwargs["batter_hand"], starter_context=kwargs["starter_context"] or {},
        bullpen_context=kwargs["bullpen_context"] or {}, starter_pitch_mix=starter_pitch_mix, bullpen_pitch_mix=None,
        park_geometry_entry=kwargs["park_geometry_entry"], field_relative_wind=kwargs["field_relative_wind"],
        defense_snapshot=kwargs["defense_snapshot"], hitter_speed_snapshot=kwargs["hitter_speed_snapshot"],
        seed=seed, other_hitter_rates=kwargs["other_hitter_rates"], hitter_pa_by_family=hitter_pa_by_family,
        season_stats=kwargs["season_stats"], platoon_context=kwargs["platoon_context"],
        season_woba=kwargs["season_woba"],
    )


def summarize(lines, inss):
    n = len(lines)
    out = {"n_sims": n}
    for k in STATS:
        vals = [ln[k] for ln in lines]
        pmf = {}
        for v in vals:
            pmf[v] = pmf.get(v, 0) + 1
        out[k] = {"mean": sum(vals) / n, "pmf": {int(v): c / n for v, c in sorted(pmf.items())}}
    for k in inss[0]:
        out["ins_" + k] = sum(i[k] for i in inss) / n
    hs = [ln["H"] for ln in lines]
    rb = [ln["RBI"] for ln in lines]
    out["cov_H_RBI"] = (sum(h * r for h, r in zip(hs, rb)) / n) - (sum(hs) / n) * (sum(rb) / n)
    out["coherent"] = all(ln["TB"] >= ln["H"] >= ln["HR"] for ln in lines)
    return out


def run_date(date_str, n_sims, out_fh):
    sides = pregame_confirmed_sides(date_str)
    if not sides:
        return 0
    batter_ids, team_abbrs, catcher_ids = set(), set(), set()
    for (_gid, side), (_ct, _f, g) in sides.items():
        for h in (g.get(side + "TeamStats") or {}).get("confirmedLineup") or []:
            if h.get("playerId") is not None:
                batter_ids.add(str(h["playerId"]))
                if h.get("position") == "C":
                    catcher_ids.add(str(h["playerId"]))
        for s in ("away", "home"):
            if (g.get(s) or {}).get("abbr"):
                team_abbrs.add(g[s]["abbr"])
    source_meta = {
        "weatherUpdatedAt": None, "savantTeamFetchedAt": None, "asOfDate": date_str,
        "savantBatters": {}, "savantBattersDiscipline": {},
        "rawPitchesByBatter": B._load_by_batter(load_pitches_for_batter, batter_ids, date_str),
        "batTrackingByBatter": {},
        "defenseByTeam": {a: v for a in team_abbrs if (v := latest_defense_snapshot(a, as_of=date_str))},
        "sprintSpeedByBatter": B._load_by_batter(latest_sprint_speed_snapshot, batter_ids, date_str),
        "catcherFramingByCatcher": B._load_by_batter(latest_catcher_framing_snapshot, catcher_ids, date_str),
        "umpireByGame": {},
    }
    n = 0
    seed_base = 0
    for (gid, side), (ct, fname, g) in sorted(sides.items()):
        away_abbr = (g.get("away") or {}).get("abbr")
        home_abbr = (g.get("home") or {}).get("abbr")
        meta = dict(source_meta, _gameHomeAbbr=home_abbr)
        ctx = build_hitter_feature_context(g, side, weather_by_team=None, source_meta=meta)
        hitters = ctx.get("hitters") or []
        other = B._build_team_other_hitter_rates(hitters)
        team_abbr = away_abbr if side == "away" else home_abbr
        for i, hctx in enumerate(hitters):
            hd, kw = B._hitter_entry(hctx, team_abbr, f"{away_abbr} @ {home_abbr}", date_str, meta,
                                    other_hitter_rates=other)
            if kw is None:
                continue
            seed = seed_base + i
            try:
                lines, inss, mix, fam = run_hitter(kw, n_sims, seed)
            except Exception as e:  # noqa: BLE001 -- production records MODEL_ERROR per hitter, same here
                out_fh.write(json.dumps({"date": date_str, "gameId": gid, "side": side, "playerId": str(hd["playerId"]),
                                         "slot": kw["target_slot"], "status": "MODEL_ERROR",
                                         "error": f"{type(e).__name__}: {e}"[:200]}, sort_keys=True) + "\n")
                continue
            rec = summarize(lines, inss)
            rec["status"] = "PROJECTED"
            rec.update({
                "date": date_str, "gameId": gid, "side": side, "teamAbbr": team_abbr, "playerId": str(hd["playerId"]),
                "slot": kw["target_slot"], "batterHand": kw["batter_hand"], "seed": seed,
                "captureFile": fname, "asOf": ct.strftime("%Y-%m-%dT%H:%M:%SZ"), "kickoff": g.get("startTime"),
                "starterPitcherId": (kw["starter_context"] or {}).get("pitcherId"),
                "starterHand": (kw["starter_context"] or {}).get("pitchHand"),
                "avgIPperStart": (kw["starter_context"] or {}).get("avgIPperStart"),
                "archivedPitches": len(kw["raw_pitches"] or []),
                "seasonPA_archive": (kw["season_stats"] or {}).get("PA"),
                "starterMixDerived": mix is not None, "seasonWobaPassed": kw["season_woba"] is not None,
            })
            out_fh.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
        seed_base += len(hitters) or 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-12")
    ap.add_argument("--end", default="2026-09-27")
    ap.add_argument("--n-sims", type=int, default=600)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    dates = sorted(d for d in os.listdir(SLATES) if args.start <= d <= args.end)
    t0 = time.time()
    with gzip.open(args.out, "wt") as fh:
        for d in dates:
            n = run_date(d, args.n_sims, fh)
            print(f"{d}: {n} hitters ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
