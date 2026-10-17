"""Scorecard segment checks for the live record (research only).

Segments for the priority pitcher families (batters faced, outs, strikeouts), computed on settled non-void forecasts and
joined to the forecast-time flags: openers, short rest (<= 4 days), season debut / layoff returns, early exits (outcome
defined: BF <= 12, a diagnostic of the unforecastable tail), starter certainty at forecast time (CONFIRMED vs PROJECTED).
Also counts starter swaps (listed probable did not start), relief appearances and unforecast actual starters, and hitter
forecasts by participation. Incumbent rows are scored on the same keys (they have no flags of their own; the challenger's
flags are applied to both)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import prospective as pr

PRIORITY = ("batters_faced", "outs", "pitcher_strikeouts")


def _flags_frame(f: pd.DataFrame) -> pd.DataFrame:
    c = f[(f["model_role"] == "challenger") & f["family"].isin(PRIORITY)].copy()
    if c.empty:
        return pd.DataFrame(columns=["game_id", "entity_id", "opener_flag", "days_rest", "season_debut", "layoff15", "certainty"])
    c = c.sort_values("generated_utc")
    fl = c["flags"].apply(lambda x: x if isinstance(x, dict) else {})
    c["opener_flag"] = fl.map(lambda x: x.get("opener_flag"))
    c["days_rest"] = fl.map(lambda x: x.get("days_rest"))
    c["season_debut"] = fl.map(lambda x: x.get("season_debut"))
    c["layoff15"] = fl.map(lambda x: x.get("layoff15"))
    c["certainty"] = c["participation"].map(lambda p: (p or {}).get("certainty") if isinstance(p, dict) else None)
    return c.groupby(["game_id", "entity_id"], as_index=False).tail(1)[
        ["game_id", "entity_id", "opener_flag", "days_rest", "season_debut", "layoff15", "certainty"]]


def _agg(g: pd.DataFrame) -> dict:
    e = g["mean"].to_numpy(float) - g["actual"].to_numpy(float)
    return {"n": int(len(g)), "mae": round(float(np.mean(np.abs(e))), 4), "bias": round(float(np.mean(e)), 4)}


def segments(root: str, sport: str, since_utc: str | None = None) -> dict:
    s = pr.load_settlements(root, sport)
    f = pr.load_forecasts(root, sport)
    out: dict = {"families": {}, "participation": {}}
    if s.empty or f.empty:
        return out
    if since_utc:
        s = s[pd.to_datetime(s["game_start_utc"], utc=True) >= pd.to_datetime(since_utc, utc=True)]
    fl = _flags_frame(f)
    ok = s[~s["void"].astype(bool) & s["family"].isin(PRIORITY)].merge(fl, on=["game_id", "entity_id"], how="left")
    bf = s[(s["family"] == "batters_faced") & ~s["void"].astype(bool) & (s["model_role"] == "challenger")][["game_id", "entity_id", "actual"]]
    bf = bf.rename(columns={"actual": "actual_bf"}).drop_duplicates(["game_id", "entity_id"])
    ok = ok.merge(bf, on=["game_id", "entity_id"], how="left")
    seg_defs = {
        "opener_flag=1": ok["opener_flag"] == 1, "opener_flag=0": ok["opener_flag"] == 0,
        "short_rest(<=4d)": ok["days_rest"].astype(float) <= 4, "normal_rest(>4d)": ok["days_rest"].astype(float) > 4,
        "season_debut": ok["season_debut"] == 1, "layoff_return(>=15d)": ok["layoff15"] == 1,
        "early_exit(actual BF<=12)": ok["actual_bf"].astype(float) <= 12, "went_deeper(actual BF>12)": ok["actual_bf"].astype(float) > 12,
        "starter_CONFIRMED": ok["certainty"] == "CONFIRMED", "starter_PROJECTED": ok["certainty"] == "PROJECTED"}
    for fam in PRIORITY:
        res = {}
        sub = ok[ok["family"] == fam]
        for name, m in seg_defs.items():
            g = sub[m.loc[sub.index]]
            if g.empty:
                continue
            res[name] = {role: _agg(gg) for role, gg in g.groupby("model_role") if len(gg)}
        if res:
            out["families"][fam] = res
    # participation accounting (forecast entities that did not start / relief / unforecast starters)
    o = pd.DataFrame(pr._read(root, "outcomes", sport))
    if len(o):
        o = o.sort_values("observed_utc").groupby(["game_id", "entity_id", "family"], as_index=False).tail(1)
        pit = o[o["family"].isin(PRIORITY)]
        fc = pit[pit.get("forecasted", True) == True]  # noqa: E712
        out["participation"] = {
            "pitcher_forecasts_resolved": int(fc.drop_duplicates(["game_id", "entity_id"]).shape[0]),
            "starter_did_not_appear": int(fc[fc["reason"] == "did_not_appear"].drop_duplicates(["game_id", "entity_id"]).shape[0]),
            "starter_relief_appearance": int(fc[fc["reason"] == "relief_appearance"].drop_duplicates(["game_id", "entity_id"]).shape[0]),
            "unforecast_actual_starters": int(pit[pit["forecasted"] == False].drop_duplicates(["game_id", "entity_id"]).shape[0]),  # noqa: E712
            "hitter_forecasts_void": int(o[o["family"].isin(["plate_appearances", "hits"]) & ~o["participated"].astype(bool)].drop_duplicates(["game_id", "entity_id"]).shape[0])}
    return out


def render_segments(seg: dict) -> str:
    lines = ["", "**Segment checks (priority pitcher families; MAE / bias per model role)**", ""]
    for fam, res in seg.get("families", {}).items():
        lines.append(f"_{fam}_")
        lines += ["", "| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |", "|---|---|---|---|---|---|"]
        for name, r in res.items():
            c, i = r.get("challenger"), r.get("incumbent")
            lines.append(f"| {name} | {c['n'] if c else 0} | {c['mae'] if c else '—'} | {c['bias'] if c else '—'} | "
                         f"{i['n'] if i else 0} | {i['mae'] if i else '—'} |")
        lines.append("")
    p = seg.get("participation")
    if p:
        lines.append("**Participation:** " + "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in p.items()))
    return "\n".join(lines) + "\n"
