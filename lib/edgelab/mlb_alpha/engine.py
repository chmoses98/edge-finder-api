"""
lib/edgelab/mlb_alpha/engine.py
===============================
The universal MLB opportunity engine: discover -> price -> cost -> validate -> select -> rank.

One call scans one game at one capture time. It returns EVERY contract it saw with a verdict,
so a market never disappears because it lacks a model or an edge:

  PASS_UNPRICEABLE       no simulator path (player props, unverified half-innings, no anchor)
  PASS_ILLIQUID          priced, but the book is one-sided, outside the price band or too thin
  PASS_NO_EDGE           priced and executable, but conservative net EV <= threshold
  PASS_VALIDATED_NEGATIVE  family/side was tested and failed out of sample: no edge is claimed
  PAPER_SHADOW           edge >= threshold, family/side NOT qualified: paper-trade only, to
                         accumulate frozen prospective evidence; never real money
  TRADE_ELIGIBLE         edge >= threshold AND family/side QUALIFIED by a registered validation
                         program (none exists today; live orders remain disabled regardless)

One model, one probability: each contract has a single simulator probability from the anchored
run environment. Competing "views" are not shown as equally authoritative. A second anchor
(consensus) is used only to size model uncertainty.

Expression selection: contracts of the same game are not independent bets. Positive-EV legs are
scored under several plausible game scripts, kept only if they remain positive in most scripts, and
combined by a mean-variance rule on SIMULATED joint payoffs, so a second leg is added only when its
incremental value justifies the correlated risk.
"""

from dataclasses import dataclass, field

from lib.edgelab.mlb_alpha import anchor as anc, execution as ex, pricer, sim, taxonomy as tx

VALIDATION_UNVALIDATED = "UNVALIDATED"
VALIDATION_QUALIFIED = "QUALIFIED"
VALIDATION_FAILED = "FAILED"
VALIDATION_INCONCLUSIVE = "INCONCLUSIVE"

# Placeholder structural error (probability points) per family until measured out of sample.
STRUCTURAL_SIGMA = {
    "KXMLBGAME": 0.010, "KXMLBTOTAL": 0.015, "KXMLBTEAMTOTAL": 0.015, "KXMLBSPREAD": 0.015,
    "KXMLBF3": 0.020, "KXMLBF5": 0.020, "KXMLBF7": 0.020, "KXMLBF5TOTAL": 0.020, "KXMLBF5SPREAD": 0.020,
    "KXMLBRFI": 0.025, "KXMLBEXTRAS": 0.030, "KXMLBINNINGWIN": 0.030, "KXMLBINNINGTOTAL": 0.030,
}

# Game scripts: multipliers on (lam_away, lam_home) and a shift of inning weight from innings 1-5 to 6-9.
SCRIPTS = (
    ("BASE", 1.00, 1.00, 0.0),
    ("HIGH_ENV", 1.08, 1.08, 0.0),
    ("LOW_ENV", 0.92, 0.92, 0.0),
    ("HOME_STRONGER", 0.96, 1.04, 0.0),
    ("AWAY_STRONGER", 1.04, 0.96, 0.0),
    ("EARLY_STARTER_EXIT", 1.00, 1.00, 0.06),
    ("STARTER_DOMINANCE", 1.00, 1.00, -0.06),
)


@dataclass
class EngineConfig:
    order_usd: float = 10.0
    min_edge: float = 0.03              # conservative net edge per contract, dollars
    z_conservative: float = 1.0
    min_top_size: int = 50
    price_band: tuple = (0.03, 0.97)
    window_minutes: tuple = (5.0, 240.0)
    max_slippage: float = 0.01
    n_sim: int = 100_000
    n_script_sim: int = 40_000
    min_scripts_positive: int = 5       # of len(SCRIPTS)
    max_legs_per_game: int = 2
    risk_aversion: float = 4.0          # mean-variance gamma on per-dollar-of-bankroll payoffs
    min_incremental_share: float = 0.15
    seed: int = 20261027


class ValidationRegistry:
    """family|side -> {'status', 'evidence'}; anything absent is UNVALIDATED. Loaded from JSON."""

    def __init__(self, table=None):
        self.table = dict(table or {})

    def get(self, series, side):
        e = self.table.get("%s|%s" % (series, side)) or self.table.get("%s|*" % series)
        return (e or {}).get("status", VALIDATION_UNVALIDATED), (e or {}).get("evidence")


def _shifted_weights(base, shift):
    """Move `shift` of total weight from innings 1-5 to 6-9 (negative = the reverse)."""
    w = list(base)
    early = sum(w[:5])
    late = sum(w[5:])
    e2, l2 = early - shift, late + shift
    return [x * e2 / early for x in w[:5]] + [x * l2 / late for x in w[5:]]


def describe(parsed):
    s = parsed["seriesTicker"]
    sel = parsed.get("team") or parsed.get("selection") or ""
    thr = parsed.get("threshold")
    fam = (parsed.get("classification") or {}).get("family")
    bits = [fam or s]
    if parsed.get("inning"):
        bits.append("inning %d" % parsed["inning"])
    if sel:
        bits.append(str(sel))
    if thr is not None:
        bits.append(">= %s" % thr)
    return " ".join(bits)


def scan_game(game_key, markets, anchor_row, surface, validation=None, cfg=None, consensus_row=None):
    """
    markets    [{'ticker','book','minToStart'}] contracts of ONE game at ONE capture time
    anchor_row {'pHome','line','pOver'} sharp anchor (primary); consensus_row same shape (optional)
    Returns {'gameKey','anchorStatus','opportunities':[...], 'selected':[...], 'lam':...}
    """
    cfg = cfg or EngineConfig()
    validation = validation or ValidationRegistry()
    out = {"gameKey": game_key, "anchorStatus": None, "lam": None, "opportunities": [], "selected": []}
    parsed = {m["ticker"]: tx.parse_market_ticker(m["ticker"]) for m in markets}
    sol = anc.solve(surface, **{"p_home": anchor_row["pHome"], "line": anchor_row["line"], "p_over": anchor_row["pOver"]}) if anchor_row else None
    if sol is None:
        out["anchorStatus"] = "NO_ANCHOR_OR_OUTSIDE_SURFACE"
        for m in markets:
            out["opportunities"].append(_row(m, parsed[m["ticker"]], "PASS_UNPRICEABLE", why="no usable sharp anchor"))
        return out
    la, lh, resid = sol
    out["anchorStatus"] = "OK"
    out["lam"] = {"away": la, "home": lh}
    draws = sim.Draws(n=cfg.n_sim, seed=cfg.seed)
    base = sim.run(la, lh, draws)
    sigma_anchor = 0.005
    if consensus_row:
        s2 = anc.solve(surface, p_home=consensus_row["pHome"], line=consensus_row["line"], p_over=consensus_row["pOver"])
        if s2:
            alt = sim.run(s2[0], s2[1], draws)
        else:
            alt = None
    else:
        alt = None

    cands = []
    lo, hi = cfg.window_minutes
    for m in markets:
        t, p = m["ticker"], parsed[m["ticker"]]
        vec, status, why = pricer.yes_vector(base, p)
        if vec is None:
            out["opportunities"].append(_row(m, p, "PASS_UNPRICEABLE", why=why))
            continue
        p_yes = float(vec.mean())
        if alt is not None:
            v2, _, _ = pricer.yes_vector(alt, p)
            sigma_anchor = max(sigma_anchor, abs(float(v2.mean()) - p_yes))
        q = ex.best_quotes(m["book"])
        if not ex.two_sided(m["book"]) or not (lo < m.get("minToStart", 0) <= hi):
            out["opportunities"].append(_row(m, p, "PASS_ILLIQUID", p_yes=p_yes, quotes=q,
                                             why="one-sided book or outside the %s-%s minute window" % (lo, hi)))
            continue
        sigma = (STRUCTURAL_SIGMA.get(p["seriesTicker"], 0.03) ** 2 + sigma_anchor ** 2
                 + (p_yes * (1 - p_yes) / cfg.n_sim)) ** 0.5
        best = None
        for side in ("YES", "NO"):
            price = q["yesAsk"] if side == "YES" else q["noAsk"]
            size = q["yesAskSize"] if side == "YES" else q["yesBidSize"]
            if price is None or not (cfg.price_band[0] <= price <= cfg.price_band[1]):
                continue
            p_side = p_yes if side == "YES" else 1.0 - p_yes
            p_cons = p_side - cfg.z_conservative * sigma
            fee = ex.kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1 - price)
            edge_raw, edge_cons = p_side - price - fee, p_cons - price - fee
            if best is None or edge_cons > best["edgeCons"]:
                best = {"side": side, "price": price, "topSize": size, "pSide": p_side, "pCons": p_cons,
                        "fee": fee, "edgeRaw": edge_raw, "edgeCons": edge_cons}
        if best is None:
            out["opportunities"].append(_row(m, p, "PASS_ILLIQUID", p_yes=p_yes, quotes=q, why="executable price outside band"))
            continue
        vstat, vev = validation.get(p["seriesTicker"], best["side"])
        if (best["topSize"] or 0) < cfg.min_top_size:
            verdict, vwhy = "PASS_ILLIQUID", "top-of-book size %.0f < %d" % (best["topSize"] or 0, cfg.min_top_size)
        elif best["edgeCons"] < cfg.min_edge:
            verdict, vwhy = "PASS_NO_EDGE", "conservative net edge %.4f < %.4f" % (best["edgeCons"], cfg.min_edge)
        elif vstat == VALIDATION_FAILED:
            verdict, vwhy = "PASS_VALIDATED_NEGATIVE", vev
        elif vstat == VALIDATION_QUALIFIED:
            verdict, vwhy = "TRADE_ELIGIBLE", vev
        else:
            verdict, vwhy = "PAPER_SHADOW", "edge present but family/side is %s: paper only" % vstat
        n = ex.contracts_for_budget(m["book"], best["side"], cfg.order_usd)
        fill = ex.fill_buy(m["book"], best["side"], max(n, 1))
        row = _row(m, p, verdict, p_yes=p_yes, quotes=q, why=vwhy)
        row.update(side=best["side"], modelProbSide=round(best["pSide"], 4), conservativeProbSide=round(best["pCons"], 4),
                   executablePrice=best["price"], feePerContract=round(best["fee"], 4),
                   netEdgeRaw=round(best["edgeRaw"], 4), netEdgeConservative=round(best["edgeCons"], 4),
                   expectedValuePerDollar=round(best["edgeRaw"] / (best["price"] + best["fee"]), 4),
                   orderContracts=fill["filled"], orderCash=fill["cash"], orderAvgPrice=fill["avgPrice"],
                   maxSizeWithin1c=ex.max_size_within_slippage(m["book"], best["side"], cfg.max_slippage),
                   modelSigma=round(sigma, 4), validationStatus=vstat, validationEvidence=vev)
        out["opportunities"].append(row)
        if verdict in ("PAPER_SHADOW", "TRADE_ELIGIBLE"):
            cands.append((row, vec, best))

    if cands:
        out["selected"] = _select(cands, la, lh, markets, parsed, cfg, out["opportunities"])
    _rank(out["opportunities"])
    return out


def _row(m, parsed, verdict, p_yes=None, quotes=None, why=None):
    return {"ticker": m["ticker"], "description": describe(parsed), "series": parsed["seriesTicker"],
            "threshold": parsed.get("threshold"), "minToStart": m.get("minToStart"), "verdict": verdict, "why": why,
            "modelProbYes": None if p_yes is None else round(p_yes, 4),
            "yesBid": (quotes or {}).get("yesBid"), "yesAsk": (quotes or {}).get("yesAsk"),
            "yesBidSize": (quotes or {}).get("yesBidSize"), "yesAskSize": (quotes or {}).get("yesAskSize")}


def _rank(rows):
    order = {"TRADE_ELIGIBLE": 0, "PAPER_SHADOW": 1, "PASS_NO_EDGE": 2, "PASS_VALIDATED_NEGATIVE": 3, "PASS_ILLIQUID": 4, "PASS_UNPRICEABLE": 5}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -(r.get("netEdgeConservative") or -9)))


def _select(cands, la, lh, markets, parsed, cfg, all_rows):
    """Script-robust, correlation-aware choice of at most cfg.max_legs_per_game legs."""
    import numpy as np
    draws = sim.Draws(n=cfg.n_script_sim, seed=cfg.seed + 1)
    base_w = sim.SimParams().normalised_weights()
    script_results = []
    for name, ma, mh, shift in SCRIPTS:
        r = sim.run(la * ma, lh * mh, draws, weights=_shifted_weights(base_w, shift) if shift else None)
        script_results.append((name, r))
    robust = []
    for row, vec, best in cands:
        evs = {}
        for name, r in script_results:
            v, _, _ = pricer.yes_vector(r, parsed[row["ticker"]])
            p_side = float(v.mean()) if best["side"] == "YES" else 1.0 - float(v.mean())
            evs[name] = round(p_side - best["price"] - best["fee"], 4)
        n_pos = sum(1 for e in evs.values() if e > 0)
        row["scriptNetEdge"] = evs
        row["scriptsPositive"] = n_pos
        row["robustNetEdge"] = min(evs.values())
        if n_pos >= cfg.min_scripts_positive:
            robust.append((row, vec, best))
        else:
            row["why"] = (row["why"] or "") + " | not robust across game scripts (%d/%d positive)" % (n_pos, len(SCRIPTS))
    if not robust:
        return []
    # per-contract unit net payoff vectors from the BASE simulation (cash-normalised)
    pay = []
    for row, vec, best in robust:
        win = vec if best["side"] == "YES" else ~vec
        cost = best["price"] + best["fee"]
        pay.append(np.where(win, 1.0 - cost, -cost).astype(float))
    mu = np.array([p.mean() for p in pay])
    chosen, objective = [], 0.0
    gamma = cfg.risk_aversion
    remaining = list(range(len(robust)))
    while remaining and len(chosen) < cfg.max_legs_per_game:
        best_j, best_obj = None, objective
        for j in remaining:
            idx = chosen + [j]
            w = _optimal_weights(mu[idx], np.cov(np.vstack([pay[i] for i in idx])) if len(idx) > 1 else np.array([[pay[idx[0]].var()]]), gamma)
            obj = float(mu[idx] @ w - 0.5 * gamma * w @ (np.cov(np.vstack([pay[i] for i in idx])) if len(idx) > 1 else np.array([[pay[idx[0]].var()]])) @ w)
            if obj > best_obj:
                best_j, best_obj = j, obj
        if best_j is None:
            break
        if chosen and (best_obj - objective) < cfg.min_incremental_share * objective:
            break
        chosen.append(best_j)
        remaining.remove(best_j)
        objective = best_obj
    sel = []
    for pos, j in enumerate(chosen):
        row, _, best = robust[j]
        corr = {}
        for k in chosen:
            if k != j:
                corr[robust[k][0]["ticker"]] = round(float(np.corrcoef(pay[j], pay[k])[0, 1]), 3)
        row["selected"] = True
        row["correlationWithOtherSelections"] = corr
        row["competingExpressions"] = [r["ticker"] for r, _, _ in robust if r["ticker"] != row["ticker"]]
        sel.append(row)
    return sel


def _optimal_weights(mu, cov, gamma):
    import numpy as np
    cov = np.atleast_2d(cov) + 1e-12 * np.eye(len(mu))
    w = np.linalg.solve(gamma * cov, mu)
    return np.clip(w, 0.0, None)
