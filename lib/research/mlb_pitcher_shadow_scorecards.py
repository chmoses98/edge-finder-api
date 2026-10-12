"""
lib/research/mlb_pitcher_shadow_scorecards.py
=============================================
Accuracy and betting-value scorecards on matured MLB pitcher PURE shadow rows (RESEARCH ONLY, descriptive,
append-only). Stdlib only.

Runs inside `scripts/research/mlb_pitcher_shadow.py evaluate` ALONGSIDE the pure_gate compare results (unchanged),
with the vendored, unmodified tools/pure_gate/accuracy_scorecard.py and value_scorecard.py (see
tools/pure_gate/SCORECARDS.md).

accuracy_scorecard   challenger mlb_pitcher_workload_joint_v1, `production_champion` the frozen
                     pitcher_prop_projection champion, `statistical_baseline` naive_recent5_v1; one scorecard per season
                     phase (postseason rows stay labelled BLOCKED_NOT_VALIDATED_POSTSEASON) on (a) the full eligible
                     population (every probable starter x statistic of a matured game the collector forecast OR
                     excluded, so failed captures are counted as missing forecasts) and (b) the pregame market-listed
                     cohort. Missing forecasts / outcomes, conditional (did not start) exclusions, missed windows and
                     unprojected actual starters are counted, never dropped. Zero matured rows gives
                     INSUFFICIENT_SAMPLE, not an error.
value_scorecard      DRY-RUN DIAGNOSTIC only, under the committed preregistration
                     docs/research/PITCHER_SHADOW_VALUE_PREREG.json (explained in the .md beside it). Only the
                     challenger at the pregame window in the regular season is eligible for an independent-value
                     verdict. The collector has no market-informed arm; any source flagged market-informed would read
                     NOT_INDEPENDENT_EVIDENCE.

Nothing here reads a live venue, places or sizes an order, or touches wagering, staking, routing or any production
surface. Records are write-once files under data/shadow_pure/mlb_pitcher/scorecards/.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_DIR = os.path.join(ROOT, "tools", "pure_gate")
PREREG_PATH = os.path.join(ROOT, "docs", "research", "PITCHER_SHADOW_VALUE_PREREG.json")
SPORT = "MLB"
ARM_ROLES = {"challenger": "challenger", "champion": "production_champion", "baseline": "statistical_baseline"}
STATS = ("pitcher_outs", "pitcher_batters_faced", "pitcher_strikeouts")
POSTSEASON_TYPES = ("F", "D", "L", "W")
NOT_PLAYED = {"Postponed", "Cancelled", "Suspended"}
ACCURACY_MIN_GAMES = 20          # accuracy_scorecard sets few_game_clusters_warning below 20 game clusters
MATURED_AFTER_HOURS = 6.0        # a game is matured once it has outcomes or kickoff + 6 h has passed (unless not played)
POP_FULL, POP_COHORT = "full_eligible", "pregame_market_listed"
VALUE_MODE = "DRY_RUN_DIAGNOSTIC"
NO_WAGERING = ("diagnostic only: no order is placed or sized, no stake, no routing, no production surface; the verdict "
               "is a statement about a recorded evaluation window, not wagering authority")


# ------------------------------------------------------------------------------------------------ vendored modules
def vendored(name: str):
    """tools/pure_gate/<name>.py (vendored, stdlib only), imported from its file. accuracy_scorecard loads pure_gate.py
    from the same directory."""
    path = os.path.join(GATE_DIR, f"{name}.py")
    mod = sys.modules.get(name)
    if mod is not None and os.path.abspath(getattr(mod, "__file__", "") or "") == os.path.abspath(path):
        return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


# ------------------------------------------------------------------------------------------------ small helpers
def _utc(s) -> datetime | None:
    if s in (None, ""):
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _price(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return math.nan
    return x if math.isfinite(x) else math.nan


def kalshi_at_least(threshold, operator: str | None = ">=", *, integer_ladder: bool = True) -> float | None:
    """A Kalshi player-stat strike in the at_least representation of the PURE ladder (SCORECARDS.md).

    Kalshi "K+" / `>= K` is the event X >= K. The pitcher arms' ladders publish P(X >= k) at integer k, so on those
    ladders the event is the rung at_least = K exactly (on a half-integer ladder it would be K - 0.5). A `> f` strike on
    an integer statistic is X >= floor(f) + 1. Anything else (no strike, less-than, ranges) is None, which the caller
    counts; the value scorecard matches strikes exactly, so a strike off the ladder is a missing quote, never an
    approximation."""
    if threshold in (None, ""):
        return None
    t = float(threshold)
    if not math.isfinite(t):
        return None
    if operator in (">=", "AT_LEAST", "greater_or_equal", None):
        k = t
    elif operator in (">", "GREATER_THAN", "greater", "gt"):
        k = float(math.floor(t) + 1)
    else:
        return None
    return k if integer_ladder else k - 0.5


def file_sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_prereg(path: str = PREREG_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        pr = json.load(fh)
    if not isinstance(pr, dict):
        raise ValueError("preregistration must be a JSON object")
    return pr


# ------------------------------------------------------------------------------------------------ accuracy
def sample_state(report: dict, challenger: str, min_games: int = ACCURACY_MIN_GAMES) -> dict:
    """Per population: INSUFFICIENT_SAMPLE (no scored challenger rows, or fewer game clusters than the scorecard's own
    warning threshold) or DESCRIPTIVE_SAMPLE. Neither is a promotion signal."""
    out = {}
    for pop in (POP_FULL, POP_COHORT):
        body = (report.get("populations") or {}).get(pop)
        if body is None:
            out[pop] = {"state": "INSUFFICIENT_SAMPLE", "reason": "population not reported (no market family supplied)"}
            continue
        acc = body["accounting"]["arms"].get(challenger, {})
        pooled = body["all_statistics_scale_free"].get(challenger, {})
        n, g = int(acc.get("scored_rows", 0)), int(pooled.get("n_games", 0))
        if n == 0:
            state, why = "INSUFFICIENT_SAMPLE", "no matured, scored challenger rows"
        elif g < min_games:
            state, why = "INSUFFICIENT_SAMPLE", f"{g} game clusters < {min_games}: CIs unstable, read descriptively only"
        else:
            state, why = "DESCRIPTIVE_SAMPLE", f"{g} game clusters; descriptive only, never an automatic promotion"
        out[pop] = {"state": state, "reason": why, "scored_rows": n, "games": g,
                    **{k: int(acc.get(k, 0)) for k in ("eligible_identities", "forecast_rows", "missing_forecast",
                                                        "missing_forecast_with_outcome", "forecast_rows_missing_outcome",
                                                        "conditional_dnp_excluded")}}
    return out


def eligible_rows(arm_rows: list[list[dict]]) -> list[dict]:
    """The full eligible population: every identity any PURE arm forecast, at its earliest as_of."""
    first: dict[tuple, str] = {}
    for rows in arm_rows:
        for r in rows:
            k = (r["sport"], r["game_id"], str(r["player_id"]), r["statistic"])
            if k not in first or r["as_of"] < first[k]:
                first[k] = r["as_of"]
    return [{"sport": k[0], "game_id": k[1], "player_id": k[2], "statistic": k[3], "as_of": t} for k, t in sorted(first.items())]


def run_accuracy(challenger_rows, outcome_rows, *, comparators: dict, market_rows: list[dict], eligible: list[dict],
                 breakdowns: dict, challenger_label: str, bootstrap: int, seed: int = 20261010) -> dict:
    A = vendored("accuracy_scorecard")
    rep = A.score(challenger_rows, outcome_rows, comparators=comparators, market_rows=market_rows, eligible_rows=eligible,
                  breakdowns=breakdowns, cluster_by=("game", "player"), bootstrap=bootstrap,
                  breakdown_bootstrap=min(200, bootstrap), seed=seed, challenger_label=challenger_label)
    return {"sample_state": sample_state(rep, challenger_label), "scorecard": rep}


# ------------------------------------------------------------------------------------------------ value
def probability_rows(rows: list[dict], *, basis: str, market_informed: bool = False) -> list[dict]:
    """One value_scorecard probability row per ladder rung: p_yes = P(actual >= at_least) exactly as published."""
    out = []
    for r in rows:
        for t in (r.get("projection") or {}).get("thresholds") or []:
            out.append({"sport": r["sport"], "game_id": r["game_id"], "player_id": str(r["player_id"]),
                        "statistic": r["statistic"], "at_least": float(t["at_least"]), "as_of": r["as_of"],
                        "kickoff": r.get("kickoff"), "p_yes": float(t["probability"]), "probability_basis": basis,
                        "model_version": r.get("model_version"), "market_informed": bool(market_informed)})
    return out


def evaluate_value(prob_rows, quote_rows, outcome_rows, prereg: dict, *, market_informed: bool,
                   dnp_policy: str | None = None) -> dict:
    """value_scorecard.evaluate with every setting taken from the preregistration (nothing chosen at run time)."""
    V = vendored("value_scorecard")
    rule = V.RULES[prereg["rule"]](float(prereg["min_edge"]))
    fees = V.FeeModel(role=prereg["role"], contracts=int(prereg["contracts"]), multiplier=float(prereg["fee_multiplier"]),
                      slippage=float(prereg["slippage"]), tick=float(prereg["tick"]))
    thresholds = {k: prereg[k] for k in V.DEFAULT_THRESHOLDS if k in prereg}
    return V.evaluate(prob_rows, quote_rows, outcome_rows, rule=rule,
                      rule_name=f"{prereg['rule']}(min_edge={float(prereg['min_edge']):g})", fees=fees,
                      thresholds=thresholds, prereg=prereg, market_informed_source=market_informed,
                      cluster_by=tuple(prereg.get("cluster", ["game"])), bootstrap=int(prereg["bootstrap"]),
                      seed=int(prereg["seed"]), as_of_policy=prereg["as_of_policy"],
                      dnp_policy=dnp_policy or prereg["dnp_policy"],
                      max_quote_age_seconds=prereg.get("max_quote_age_seconds"),
                      calibration_fit_end=prereg.get("calibration_fit_end"))


def value_entry(report: dict, *, arm: str, window: str, role: str, eligible: bool, market_informed: bool,
                dnp_policy: str) -> dict:
    v = report["verdict"]
    return {"arm": arm, "capture_window": window, "role": role, "dnp_policy": dnp_policy,
            "eligible_for_independent_value_verdict": eligible, "market_informed_source": market_informed,
            "verdict": v["verdict"], "verdict_reasons": v.get("reasons"),
            "verdict_counts_toward_preregistered_decision": bool(eligible and role == "primary"),
            "n_decisions": report["decisions_summary"].get("n_decisions", 0),
            "n_games": report["decisions_summary"].get("n_games", 0), "report": report}


# ------------------------------------------------------------------------------------------------ MLB store adapters
def _phase(game_type) -> str:
    return "postseason" if game_type in POSTSEASON_TYPES else "regular_season"


def matured_games(proj, outcomes, provenance, now):
    """(matured game ids, not-played game ids). Matured: outcomes recorded, or kickoff + MATURED_AFTER_HOURS passed and
    the game was not recorded as postponed / cancelled / suspended (an unfetched box score is then a counted missing
    outcome, never a silently absent row)."""
    have = {k[0] for k in outcomes}
    not_played = {p["game_id"] for p in provenance if p.get("status") in NOT_PLAYED} - have
    out = set()
    for r in proj:
        g = r["game_id"]
        if g in have or (g not in not_played and _utc(r["kickoff"]) + timedelta(hours=MATURED_AFTER_HOURS) <= now):
            out.add(g)
    return out, not_played


def excluded_eligible(run_docs, games):
    """Eligible identities the collector excluded at capture (a probable starter with a reason such as
    challenger_lt3_prior_starts): each is a failed capture and is counted by the scorecard as a missing forecast."""
    out = []
    for d in run_docs:
        gtype = {g["game_id"]: g.get("game_type") for g in d.get("games", [])}
        for x in d.get("exclusions", []):
            if x.get("player_id") and x.get("game_id") in games:
                for st in STATS:
                    out.append({"sport": SPORT, "game_id": x["game_id"], "player_id": str(x["player_id"]), "statistic": st,
                                "as_of": d.get("as_of"), "_phase": _phase(gtype.get(x["game_id"])),
                                "_reason": x.get("reason")})
    return out


def market_family(listing_rows, kickoff):
    """The MLB market family mapped to (accuracy cohort rows, value_scorecard quote rows, counts).

    cohort rows   {sport, game_id, player_id, statistic, open_time, observed_at} = first_listed_at for both: the
                  listing snapshots carry no venue open_time, and the collector's own cohort rule is "first seen in our
                  captured listings at or before as_of" (open_time_basis records the proxy).
    quote rows    + at_least = kalshi_at_least(threshold, comparisonOperator): "AT_LEAST K" (the edgelab "K+" rows) is
                  X >= K, the integer rung K; yes_bid / yes_ask in DOLLARS as captured, observed_at = quote.quoted_at.
    A quote observed after the as_of of the snapshot that recorded it is dropped and counted; the scorecard itself also
    only ever enters at the latest quote observed at or before each as_of."""
    c = Counter()
    cohort, quotes, seen = [], [], set()
    for r in listing_rows:
        c["listing_rows"] += 1
        if not r.get("player_id"):
            c["dropped_unmatched_player"] += 1
            continue
        base = {"sport": SPORT, "game_id": str(r["game_id"]), "player_id": str(r["player_id"]), "statistic": r["statistic"]}
        first = r.get("first_listed_at")
        if first:
            cohort.append({**base, "open_time": first, "observed_at": first, "open_time_basis": "first_listed_at_proxy"})
        else:
            c["cohort_dropped_no_first_listed_at"] += 1
        q = r.get("quote")
        if not q:
            c["quote_absent"] += 1
            continue
        k = kalshi_at_least(r.get("at_least"), r.get("comparison") or "AT_LEAST")
        if k is None:
            c["quote_dropped_no_or_unknown_strike"] += 1
            continue
        obs, at = _utc(q.get("quoted_at")), _utc(r.get("pure_as_of"))
        if obs is None:
            c["quote_dropped_no_observed_at"] += 1
            continue
        if at is not None and obs > at:
            c["quote_dropped_observed_after_snapshot_as_of"] += 1
            continue
        bid, ask = _price(q.get("yes_bid")), _price(q.get("yes_ask"))
        if any(x is not None and (math.isnan(x) or not 0.0 <= x <= 1.0) for x in (bid, ask)):
            c["quote_dropped_invalid_price"] += 1
            continue
        if bid is not None and ask is not None and bid > ask:
            c["quote_dropped_crossed"] += 1
            continue
        key = (r.get("ticker"), q.get("quoted_at"), bid, ask)
        if key in seen:
            c["quote_duplicate_across_snapshots"] += 1
            continue
        seen.add(key)
        t = r.get("ticker") or ""
        quotes.append({**base, "at_least": k, "observed_at": q.get("quoted_at"), "yes_bid": bid, "yes_ask": ask,
                       "price_unit": "dollars", "ticker": r.get("ticker"), "market": t.split("-")[0] if "-" in t else None,
                       "kickoff": kickoff.get(str(r["game_id"]))})
    c["cohort_rows"], c["quote_rows"] = len(cohort), len(quotes)
    return cohort, quotes, dict(sorted(c.items()))


def build(proj, outcomes, listing_rows, run_docs, missed, provenance, *, now, run_id, reps=1000,
          prereg_path=PREREG_PATH):
    """(accuracy_scorecard record, value_scorecard record) for one evaluation. Works with zero matured rows.

    proj: one capture per (window, arm, key), the earliest (as cmd_evaluate dedupes). outcomes: {(game, player, stat):
    outcome row}."""
    games, not_played = matured_games(proj, outcomes, provenance, now)
    rows = [r for r in proj if r["game_id"] in games and r.get("x_arm") in ARM_ROLES]
    kickoff = {r["game_id"]: r["kickoff"] for r in rows}
    idents = {(r["game_id"], r["player_id"], r["statistic"]) for r in rows}
    excl = excluded_eligible(run_docs, games)
    outs = [outcomes[k] for k in sorted(idents | {(x["game_id"], x["player_id"], x["statistic"]) for x in excl})
            if k in outcomes]
    cohort_rows, quote_rows, qcounts = market_family([r for r in listing_rows if str(r.get("game_id")) in games], kickoff)
    A = vendored("accuracy_scorecard")
    breakdowns = {"capture_window": lambda ctx: ctx["forecast"].get("x_capture_window"),
                  "hours_to_first_pitch": A.capture_window}
    scorecards = {}
    for phase in ("regular_season", "postseason"):
        sel = [r for r in rows if r.get("x_season_phase") == phase]
        ex = [{k: v for k, v in x.items() if not k.startswith("_")} for x in excl if x["_phase"] == phase]
        if not sel and not ex and phase == "postseason":
            continue
        by = {a: [r for r in sel if r["x_arm"] == a] for a in ARM_ROLES}
        comps = {a: {"rows": by[a], "role": role} for a, role in ARM_ROLES.items() if a != "challenger"}
        sc = run_accuracy(by["challenger"], outs, comparators=comps, market_rows=cohort_rows,
                          eligible=eligible_rows(list(by.values()) + [ex]), breakdowns=breakdowns,
                          challenger_label="challenger", bootstrap=reps)
        sc["validation_status"] = "BLOCKED_NOT_VALIDATED_POSTSEASON" if phase == "postseason" else "RESEARCH_SHADOW"
        sc["excluded_at_capture"] = dict(sorted(Counter(
            r for _g, _p, r in {(x["game_id"], x["player_id"], x["_reason"]) for x in excl if x["_phase"] == phase}).items()))
        scorecards[phase] = sc
    unproj = sorted({(p["game_id"], s) for p in provenance if p.get("game_id") in games
                     for s in p.get("unprojected_actual_starters") or []})
    missed_m = [m for m in missed if str(m.get("game_id")) in games]
    accuracy = {
        "record": "accuracy_scorecard", "record_version": 1, "sport": SPORT, "run_id": run_id, "evaluated_at": now.isoformat(),
        "status": "DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION",
        "arms": {a: f"{role} ({sorted({r.get('model_version') for r in rows if r['x_arm'] == a} - {None})})"
                 for a, role in ARM_ROLES.items()},
        "matured_games": len(games), "not_played_games": len(not_played), "matured_forecast_rows": len(rows),
        "collection_accounting": {
            "missed_capture_windows": len(missed_m),
            "missed_capture_windows_list": [{"game_id": m.get("game_id"), "window": m.get("window")} for m in missed_m][:200],
            "excluded_at_capture_player_stat_identities": len(excl),
            "unprojected_actual_starters": len(unproj),
            "note": "a missed window has no forecasts at all, and an actual starter who was not the probable starter has "
                    "no forecast and no outcome row; both are counted here"},
        "market_informed_benchmark": {"state": "NO_MARKET_INFORMED_ARM_IN_THIS_COLLECTOR"},
        "market_family_mapping": qcounts, "scorecards": scorecards,
        "note": "Accuracy only, alongside the pure_gate compare results; never evidence of betting value.",
    }
    pr = load_prereg(prereg_path)
    arm0, win0, ph0 = pr["primary_arm"], pr["primary_capture_window"], pr["primary_season_phase"]
    evals = {}

    def run(arm, win, phase, dnp, role, eligible):
        sel = [r for r in rows if r["x_arm"] == arm and r.get("x_capture_window") == win and r.get("x_season_phase") == phase]
        rep = evaluate_value(probability_rows(sel, basis="conditional_on_starting"), quote_rows, outs, pr,
                             market_informed=False, dnp_policy=dnp)
        name = f"{arm}/{win}/{phase}" + (f"/dnp_{dnp}" if role == "dnp_sensitivity" else "")
        e = value_entry(rep, arm=arm, window=win, role=role, eligible=eligible, market_informed=False, dnp_policy=dnp)
        e["season_phase"] = phase
        if phase == "postseason":
            e["validation_status"] = "BLOCKED_NOT_VALIDATED_POSTSEASON"
        evals[name] = e

    run(arm0, win0, ph0, pr["dnp_policy"], "primary", True)
    for win in sorted({r.get("x_capture_window") for r in rows if r.get("x_capture_window")} - {win0}):
        run(arm0, win, ph0, pr["dnp_policy"], "secondary_descriptive", False)
    for arm in pr.get("descriptive_arms", []):
        run(arm, win0, ph0, pr["dnp_policy"], "secondary_descriptive", False)
    if any(r.get("x_season_phase") == "postseason" for r in rows):
        run(arm0, win0, "postseason", pr["dnp_policy"], "secondary_descriptive", False)
    if pr.get("dnp_sensitivity_policy"):
        run(arm0, win0, ph0, pr["dnp_sensitivity_policy"], "dnp_sensitivity", False)
    prim = evals[f"{arm0}/{win0}/{ph0}"]
    value = {
        "record": "value_scorecard", "record_version": 1, "sport": SPORT, "run_id": run_id, "evaluated_at": now.isoformat(),
        "mode": VALUE_MODE, "wagering": NO_WAGERING,
        "preregistration": {"id": pr.get("preregistration_id"), "path": os.path.relpath(prereg_path, ROOT),
                            "file_sha256": file_sha256(prereg_path)},
        "primary": {"arm": arm0, "capture_window": win0, "season_phase": ph0, "verdict": prim["verdict"],
                    "verdict_reasons": prim["verdict_reasons"], "n_decisions": prim["n_decisions"], "n_games": prim["n_games"]},
        "quote_mapping": qcounts, "evaluations": evals,
        "note": "Betting value only, separate from accuracy promotion. Only the preregistered PURE arm, window and season "
                "phase are eligible for an independent-value verdict.",
    }
    return accuracy, value


def summary(accuracy, value):
    """Compact stdout view of the two records."""
    return {"accuracy_scorecard": {lab: {p: s["state"] for p, s in sc["sample_state"].items()}
                                   for lab, sc in accuracy["scorecards"].items()},
            "value_scorecard": {"mode": value["mode"], "primary": value["primary"],
                                "verdicts": {k: e["verdict"] for k, e in value["evaluations"].items()}}}


def build_safe(*args, **kwargs):
    """`build`, but a scorecard failure never stops the collector: it is recorded as an ERROR record (fail closed,
    visible) and the pure_gate compare evaluation is written as before."""
    try:
        return build(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
        now = kwargs.get("now")
        base = {"record_version": 1, "sport": SPORT, "run_id": kwargs.get("run_id"),
                "evaluated_at": now.isoformat() if now else None, "state": "ERROR",
                "error": f"{type(exc).__name__}: {exc}"[:2000]}
        return ({"record": "accuracy_scorecard", **base, "scorecards": {}},
                {"record": "value_scorecard", **base, "mode": VALUE_MODE, "wagering": NO_WAGERING, "evaluations": {}})


def summary_safe(accuracy, value):
    if accuracy.get("state") == "ERROR" or value.get("state") == "ERROR":
        return {"scorecards_state": "ERROR", "error": accuracy.get("error") or value.get("error")}
    return summary(accuracy, value)
