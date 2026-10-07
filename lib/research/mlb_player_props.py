#!/usr/bin/env python3
"""
lib/research/mlb_player_props.py
================================
Per-market MLB player-prop projection records for the edge_finder.app.v1 publication
(``market.extensions.player_prop``, schema ``mlb.player_prop.v1``). RESEARCH ONLY.

Every archived Kalshi player-prop market gets a record -- including the ones that
cannot be priced, which carry an explicit ``projection_status`` and reason instead of
being hidden. A probability is attached ONLY for a ``*_PROJECTION`` status and is
always the model's own; it is never a Kalshi price and never falls back to one.
Records are deliberately kept out of model_prices / recommendations: a research
projection that differs from Kalshi is not an edge (see ``FAMILY_EVIDENCE``).

Statuses
  VERIFIED_PROJECTION      reserved: no family has evidence of beating Kalshi out of sample
  RESEARCH_PROJECTION      model probability published, labelled research
  NO_MODEL_SUPPORT         no projection engine exists for this family
  MISSING_REQUIRED_CONTEXT engine exists but a required input is absent (named)
  LINEUP_UNCONFIRMED       hitter projection needs a confirmed batting order
  PLAYER_NOT_STARTING      confirmed lineup / probable starter excludes the player
  AMBIGUOUS_MARKET         the contract's player / threshold could not be parsed
  GAME_STARTED             first pitch passed: the pregame projection is withdrawn

Pitchers: lib/research/pitcher_prop_projection.py with params fitted by
scripts/research/mlb_pitcher_prop_calibration.py (DEV 2022-24; holdout + Kalshi
evaluation in data/edgelab/analytics/mlb_pitcher_prop_calibration_2026-10-07.json).
Hitters: the existing hitter engine's committed prospective snapshots
(data/edgelab/hitter_projection_snapshots/<date>.jsonl), newest pregame row per ticker.
Pure apart from the explicit loader functions.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import unicodedata

from lib.research import pitcher_prop_projection as PP
from lib.research.hitter_prop_projection_loader import select_latest_rows

SCHEMA = "mlb.player_prop.v1"

PROP_SERIES = {
    "KXMLBKS": ("pitcher_strikeouts", "PITCHER", "Strikeouts", "K"),
    "KXMLBOUTS": ("pitcher_outs", "PITCHER", "Outs recorded", "outs"),
    "KXMLBHIT": ("hitter_hits", "HITTER", "Hits", "H"),
    "KXMLBTB": ("hitter_total_bases", "HITTER", "Total bases", "TB"),
    "KXMLBHRR": ("hitter_hrr", "HITTER", "Hits + runs + RBIs", "H+R+RBI"),
    "KXMLBRBI": ("hitter_rbi", "HITTER", "RBIs", "RBI"),
    "KXMLBSB": ("hitter_stolen_bases", "HITTER", "Stolen bases", "SB"),
    "KXMLBHR": ("hitter_home_runs", "HITTER", "Home runs", "HR"),
    "KXMLBRUNS": ("hitter_runs", "HITTER", "Runs", "R"),
    "KXMLBHA": ("pitcher_hits_allowed", "PITCHER", "Hits allowed", "HA"),
    "KXMLBERA": ("pitcher_earned_runs", "PITCHER", "Earned runs", "ER"),
    "KXMLBWA": ("pitcher_walks_allowed", "PITCHER", "Walks allowed", "BB"),
}

#: hitter-engine distribution keys priced from the committed snapshots
HITTER_ENGINE_FAMILIES = {"hitter_hits", "hitter_total_bases", "hitter_hrr", "hitter_rbi"}
PITCHER_ENGINE_FAMILIES = {"pitcher_strikeouts", "pitcher_outs"}

NO_SUPPORT_REASON = {
    "hitter_stolen_bases": "No stolen-base attempt model: no attempt-rate, catcher pop-time or caught-stealing inputs exist.",
    "hitter_home_runs": "Home-run props are not priced: the hitter engine's HR tail has no validation and the family is rarely listed.",
    "hitter_runs": "Runs-scored props are not priced on their own (only inside H+R+RBI, which depends on teammates).",
    "pitcher_hits_allowed": "No hits-allowed distribution exists for starters.",
    "pitcher_earned_runs": "No earned-runs distribution exists for starters.",
    "pitcher_walks_allowed": "No walks-allowed distribution exists for starters.",
}

#: What the evidence supports, per family -- copied into every record's `validation`.
FAMILY_EVIDENCE = {
    "pitcher_strikeouts": {
        "status": "RESEARCH",
        "summary": ("Beats the incumbent engine out of sample (2025-26 holdout Brier 0.143 vs 0.175) but is worse than "
                    "Kalshi on 6,610 settled 2026 markets (Brier 0.166 vs 0.157; postseason 0.174 vs 0.167, n=202). "
                    "Not an edge."),
    },
    "pitcher_outs": {
        "status": "RESEARCH",
        "summary": ("Beats the incumbent engine out of sample (holdout Brier 0.162 vs 0.228) but is worse than Kalshi "
                    "(0.259 vs 0.239, 888 markets) and clearly worse in the 2026 postseason (0.313 vs 0.199, n=25): "
                    "October hooks are shorter than any regular-season history implies. Not an edge."),
    },
    "hitter": {
        "status": "RESEARCH",
        "summary": ("Hitter engine is at parity with Kalshi at best (MLB-RSCH-0028: Brier 0.158 vs 0.155, slope 0.86, "
                    "hits over-confident); lineup confirmation did not improve it. Not an edge."),
    },
}

LIMITATIONS_PITCHER = [
    "Postseason leash is a single shrunk outs shift estimated from this postseason's starts; manager-specific hooks are not modeled.",
    "Opponent strikeout tendency is team-level season-to-date; confirmed-lineup K% is shown as context, not used in the math.",
    "No pitch-count, umpire or park strikeout adjustment; bullpen availability is not used for the starter's leash.",
]
LIMITATIONS_HITTER = [
    "Projection comes from the hitter engine's last pregame snapshot; it is not regenerated when the lineup changes afterwards.",
    "RBI and H+R+RBI depend on simulated teammates; postseason bullpen usage is not modeled separately.",
]


def _name_key(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def _series(ticker):
    return str(ticker or "").split("-", 1)[0]


def is_player_prop(ticker):
    return _series(ticker) in PROP_SERIES


def _threshold(row, ticker):
    t = row.get("threshold")
    if t is not None:
        try:
            return int(float(t))
        except (TypeError, ValueError):
            return None
    tail = str(ticker).rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


# ------------------------------------------------------------------ loaders (I/O)

def load_pitcher_model(data_root):
    """(params, starts, team_games) or (None, [], []) when the research artifacts are absent."""
    root = os.path.join(data_root, "edgelab", "analytics")
    try:
        with open(os.path.join(root, "mlb_pitcher_prop_model_params.json"), encoding="utf-8") as fh:
            params = json.load(fh)
        with gzip.open(os.path.join(root, "mlb_pitcher_start_history.json.gz"), "rt", encoding="utf-8") as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        return None, [], []
    return params, hist.get("starts") or [], hist.get("team_games") or []


def postseason_starts_from_settlements(settlement_rows, since="2026-09-29"):
    """Postseason pitcher-games (pid, date, outs, k) from settled KS / OUTS markets."""
    games = {}
    for r in settlement_rows:
        fam = r.get("marketFamily")
        if fam not in PITCHER_ENGINE_FAMILIES:
            continue
        ev = r.get("settlementEvidence") or {}
        cands = ev.get("candidates") or []
        if len(cands) != 1 or ev.get("actualValue") is None:
            continue
        m = re.match(r"^KXMLB(?:KS|OUTS)-(\d{2})([A-Z]{3})(\d{2})", r.get("marketTicker") or "")
        mon = {"SEP": 9, "OCT": 10, "NOV": 11}.get(m.group(2)) if m else None
        if not mon:
            continue
        date = f"20{m.group(1)}-{mon:02d}-{m.group(3)}"
        if date < since:
            continue
        g = games.setdefault((str(cands[0]["playerId"]), date), {"pid": str(cands[0]["playerId"]), "date": date,
                                                                  "outs": None, "k": None, "bf": None})
        g["outs" if fam == "pitcher_outs" else "k"] = ev["actualValue"]
    return [g for g in games.values() if g["outs"] is not None]


def load_hitter_snapshots(data_root, date):
    path = os.path.join(data_root, "edgelab", "hitter_projection_snapshots", f"{date}.jsonl")
    rows = []
    for p in (path, path + ".gz"):
        if os.path.exists(p):
            op = gzip.open if p.endswith(".gz") else open
            with op(p, "rt", encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            break
    return rows


# ------------------------------------------------------------------ context

class PitcherContext:
    def __init__(self, params, starts, team_games, postseason_starts=()):
        self.params = params
        self.by_pid = {}
        for s in sorted(list(starts) + list(postseason_starts), key=lambda s: (s["date"], s["pid"])):
            self.by_pid.setdefault(str(s["pid"]), []).append(s)
        self.team = {}
        for t in team_games:
            self.team.setdefault(t["team"], []).append(t)

    def history(self, pid, date):
        return [s for s in self.by_pid.get(str(pid), []) if s["date"] < date]

    def opp_rate(self, team, date):
        rows = [t for t in self.team.get(team, []) if t["date"] < date and t["date"][:4] == date[:4]]
        pa = sum(t["pa"] for t in rows)
        return (sum(t["k"] for t in rows) / pa if pa else None), pa


def _lineup(game, side):
    ts = game.get(f"{side}TeamStats") or {}
    return ts.get("confirmedLineup") or [], bool(ts.get("lineupConfirmed")), ts.get("lineupStatus")


def _lineup_k_pct(lineup):
    vals = []
    for b in lineup:
        sp = (b.get("platoonSplits") or {}).get("vsRHP") or {}
        if sp.get("kPct") is not None:
            vals.append(sp["kPct"])
    return round(sum(vals) / len(vals), 1) if vals else None


def _q(pmf, q):
    return PP.quantile(pmf, q)


# ------------------------------------------------------------------ records

def _base(ticker, row, family, role, label, unit, player, team, opp, threshold):
    yes = f"{player} records {threshold}+ {label.lower()}" if player and threshold is not None else row.get("title")
    return {
        "schema": SCHEMA, "player_name": player, "mlbam_player_id": None, "player_id": None, "role": role,
        "team": team, "opponent": opp, "family": family, "stat_label": label, "stat_unit": unit,
        "threshold": threshold, "comparison": "AT_LEAST", "yes_semantics": yes,
        "projection_status": None, "status_reason": None, "model_probability_yes": None, "expected_stat": None,
        "projection_generated_at": None, "inputs_as_of": None, "lineup_status": None, "lineup_slot": None,
        "drivers": [], "provenance": None, "limitations": [], "validation": None, "betting_eligible": False,
    }


def _status(rec, status, reason):
    rec["projection_status"] = status
    rec["status_reason"] = reason
    return rec


def build_records(*, slate, markets, now_iso, event_for, pregame_closed, player_id_for,
                  pitcher_ctx=None, hitter_rows=None):
    """{ticker: player_prop record} for every player-prop market row.

    slate          authoritative slate dict for the date (or None)
    markets        edgelab market rows (marketTicker, player, team, threshold, marketFamily, title)
    event_for      ticker -> v1 event (or None)
    pregame_closed (event) -> bool
    player_id_for  mlbam id -> contract participant id
    """
    games_by_pk = {str(g.get("gameId")): g for g in (slate or {}).get("games") or []}
    # newest snapshot available at or before now per ticker (lib/research/hitter_prop_projection_loader:
    # a newer non-PROJECTED row, e.g. a scratch, wins over an older probability)
    hitter_latest = select_latest_rows(hitter_rows or [], now_iso) if hitter_rows else {}
    pitcher_cache = {}
    out = {}
    for row in markets:
        ticker = str(row.get("marketTicker") or "").strip().upper()
        if not is_player_prop(ticker) or ticker in out:
            continue
        family, role, label, unit = PROP_SERIES[_series(ticker)]
        player, team, threshold = row.get("player"), row.get("team"), _threshold(row, ticker)
        ev = event_for(ticker)
        g = games_by_pk.get(str(((ev or {}).get("source_ids") or {}).get("mlb_game_pk") or "")) if ev else None
        opp = None
        if g and team:
            a, h = (g.get("away") or {}).get("abbr"), (g.get("home") or {}).get("abbr")
            opp = h if team == a else a if team == h else None
        rec = _base(ticker, row, family, role, label, unit, player, team, opp, threshold)
        out[ticker] = rec
        if family not in PITCHER_ENGINE_FAMILIES and family not in HITTER_ENGINE_FAMILIES:
            _status(rec, "NO_MODEL_SUPPORT", NO_SUPPORT_REASON.get(family, "No projection engine for this family."))
            continue
        if not player or threshold is None or not team:
            _status(rec, "AMBIGUOUS_MARKET", "The contract's player, team or threshold could not be parsed.")
            continue
        if ev is None or g is None:
            _status(rec, "MISSING_REQUIRED_CONTEXT", "The game is not on the published slate yet (no schedule match).")
            continue
        if pregame_closed(ev):
            _status(rec, "GAME_STARTED", "First pitch has passed; the pregame projection is withdrawn.")
            continue
        side = "away" if team == (g.get("away") or {}).get("abbr") else "home"
        if family in PITCHER_ENGINE_FAMILIES:
            _pitcher(rec, g, side, ticker, ev, now_iso, pitcher_ctx, player_id_for, pitcher_cache)
        else:
            _hitter(rec, g, side, ticker, hitter_latest.get(ticker), player_id_for)
    return out


def _pitcher(rec, g, side, ticker, ev, now_iso, ctx, player_id_for, cache):
    sp = (g.get(side) or {}).get("pitcher") or {}
    rec["lineup_status"] = "PROBABLE_STARTER" if sp.get("id") else None
    if not sp.get("id"):
        return _status(rec, "MISSING_REQUIRED_CONTEXT", "No probable starter is announced for this team.")
    if _name_key(sp.get("name")) != _name_key(rec["player_name"]):
        return _status(rec, "PLAYER_NOT_STARTING", f"The announced starter is {sp.get('name')}, not {rec['player_name']}.")
    rec["mlbam_player_id"] = str(sp["id"])
    rec["player_id"] = player_id_for(str(sp["id"]))
    if ctx is None or not ctx.params:
        return _status(rec, "MISSING_REQUIRED_CONTEXT", "Pitcher research model parameters are not available.")
    date = ev["extensions"].get("game_date") or ev["start_time_utc"][:10]
    key = (str(sp["id"]), date)
    if key not in cache:
        prior = ctx.history(sp["id"], date)
        if len(prior) < 3:
            cache[key] = None
        else:
            post = (ev["extensions"].get("game_type") in ("F", "D", "L", "W")) or date >= "2026-09-29"
            opp_team = (g.get("home" if side == "away" else "away") or {}).get("abbr")
            opp_k, opp_pa = ctx.opp_rate(opp_team, date)
            lineup, _conf, _st = _lineup(g, "home" if side == "away" else "away")
            cache[key] = (PP.project(prior, ctx.params, postseason=post, opp_k_rate=opp_k, opp_pa=opp_pa), prior, post,
                          _lineup_k_pct(lineup))
    proj = cache[key]
    if proj is None:
        return _status(rec, "MISSING_REQUIRED_CONTEXT", "Fewer than 3 prior MLB starts in the research history for this pitcher.")
    pr, prior, post, lineup_k = proj
    is_k = rec["family"] == "pitcher_strikeouts"
    table, pmf = (pr["p_k_ge"], pr["k_pmf"]) if is_k else (pr["p_outs_ge"], pr["outs_pmf"])
    mean = pr["expected_strikeouts"] if is_k else pr["expected_outs"]
    rec["model_probability_yes"] = round(PP.p_at_least(table, rec["threshold"]), 4)
    rec["expected_stat"] = {"stat": "strikeouts" if is_k else "outs", "mean": round(mean, 2), "median": _q(pmf, 0.5),
                            "p10": _q(pmf, 0.1), "p90": _q(pmf, 0.9), "unit": rec["stat_unit"]}
    rec["projection_generated_at"] = now_iso
    rec["inputs_as_of"] = prior[-1]["date"] if prior else None
    shift = pr["postseason_shift_applied"]
    rec["drivers"] = [
        {"label": "Projected outs (shared workload)", "value": f"{pr['expected_outs']:.1f} (~{pr['expected_outs'] / 3:.1f} IP)"},
        {"label": "Strikeout rate per batter (shrunk)", "value": f"{pr['k_rate'] * 100:.1f}%"},
        {"label": "Expected batters faced", "value": f"{pr['expected_batters_faced']:.1f}"},
        {"label": "Opponent K tendency", "value": f"x{pr['opponent_k_factor']:.2f}"},
    ]
    if post:
        rec["drivers"].append({"label": "Postseason leash", "value": f"{shift:+.1f} outs (n={ctx.params.get('postseason_shift_n')}, shrunk)"})
    if lineup_k is not None:
        rec["drivers"].append({"label": "Confirmed opposing lineup K% (context)", "value": f"{lineup_k}%"})
    rec["drivers"].append({"label": "Starts in history (last)", "value": f"{len(prior)} ({prior[-1]['outs']} outs on {prior[-1]['date']})"})
    rec["provenance"] = {"engine": "lib/research/pitcher_prop_projection.py", "engine_version": f"params asof {ctx.params.get('asof')}",
                         "source": "research_cache starter box scores + Statcast-derived starts + settled postseason props"}
    rec["limitations"] = list(LIMITATIONS_PITCHER)
    rec["validation"] = dict(FAMILY_EVIDENCE[rec["family"]])
    return _status(rec, "RESEARCH_PROJECTION", "Research projection: calibrated better than the incumbent engine, not better than Kalshi.")


def _hitter(rec, g, side, ticker, snap, player_id_for):
    lineup, confirmed, lineup_status = _lineup(g, side)
    rec["lineup_status"] = "CONFIRMED" if confirmed else (str(lineup_status).upper() if lineup_status else "UNCONFIRMED")
    slot = None
    for b in lineup:
        if _name_key(b.get("name")) == _name_key(rec["player_name"]):
            slot = b.get("order")
            rec["mlbam_player_id"] = str(b.get("playerId")) if b.get("playerId") else None
            rec["player_id"] = player_id_for(rec["mlbam_player_id"]) if rec["mlbam_player_id"] else None
    rec["lineup_slot"] = slot
    if not confirmed:
        return _status(rec, "LINEUP_UNCONFIRMED", "The batting order is not confirmed; hitter projections need the player's slot.")
    if slot is None:
        return _status(rec, "PLAYER_NOT_STARTING", "Not in the confirmed starting lineup.")
    if snap is None:
        return _status(rec, "MISSING_REQUIRED_CONTEXT", "The hitter engine has not produced a pregame snapshot for this market.")
    st = str(snap.get("projectionStatus") or "")
    if st in ("NOT_IN_LINEUP", "PLAYER_NOT_IN_STARTING_LINEUP"):
        return _status(rec, "PLAYER_NOT_STARTING", "The hitter engine's snapshot found the player out of the lineup.")
    p = snap.get("modelProbability")
    if not snap.get("isProjected") or p is None:
        reason = snap.get("projectionStatusReason") or st or "unknown"
        return _status(rec, "MISSING_REQUIRED_CONTEXT", f"The hitter engine's latest snapshot is not a projection ({reason}).")
    p = float(p)
    if p > 1.0:
        p /= 100.0
    rec["model_probability_yes"] = round(p, 4)
    exp = snap.get("distributionMean")
    if exp is not None:
        rec["expected_stat"] = {"stat": rec["family"].replace("hitter_", ""), "mean": round(float(exp), 2), "median": None,
                                "p10": None, "p90": None, "unit": rec["stat_unit"]}
    rec["projection_generated_at"] = snap.get("snapshotGeneratedAt") or snap.get("projectionGeneratedAt")
    rec["inputs_as_of"] = snap.get("marketObservedAt") or rec["projection_generated_at"]
    diag = snap.get("sampleSizeDiagnostics") or {}
    rec["drivers"] = [d for d in (
        {"label": "Lineup slot", "value": str(slot)},
        {"label": "Snapshot checkpoint", "value": str(snap.get("checkpoint"))} if snap.get("checkpoint") else None,
        {"label": "Monte Carlo std. error", "value": f"{float(snap['monteCarloStderr']):.3f}"} if snap.get("monteCarloStderr") is not None else None,
    ) if d]
    rec["provenance"] = {"engine": "hitter engine (lib/research/lineup_game_simulator.py)", "engine_version": snap.get("engineCommitSha"),
                         "source": "data/edgelab/hitter_projection_snapshots"}
    rec["limitations"] = list(LIMITATIONS_HITTER) + list(snap.get("modelLimitations") or [])[:3]
    rec["validation"] = dict(FAMILY_EVIDENCE["hitter"])
    return _status(rec, "RESEARCH_PROJECTION", "Research projection from the hitter engine's latest pregame snapshot.")
