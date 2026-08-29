#!/usr/bin/env python3
"""
scripts/edgelab/run_frozen_forward_scorer.py
====================================================================
Deterministic FORWARD confirmation engine. RESEARCH ONLY.

Scores already-frozen research hypotheses against genuinely new
post-2026-08-28 settled data WITHOUT refitting anything:

  * MLB-RSCH-0022 reference findings -- production vs Kalshi proper
    scoring, family ranking, production calibration, fixed
    input-quality and disagreement segments.
  * MLB-RSCH-0024 -- M0 (Kalshi fair mid) vs M1 (production) vs M2
    (frozen alpha residual). Alpha is READ from the committed artifact.
  * MLB-RSCH-0026 -- raw Kalshi fair mid vs the frozen beta shrink.
    Beta and base are READ from the committed artifact.

No parameter is ever re-estimated: this script imports no fitting
function, and `lib.edgelab.research.frozen_forward_scorer` exposes none.
Frozen source artifacts are opened read-only and never rewritten.

Usage (idempotent -- rerun freely as the archive grows):

    python3 scripts/edgelab/run_frozen_forward_scorer.py

Outputs (overwritten in place, never appended to the frozen sources):
    data/edgelab/analytics/latest_frozen_forward_scorecard.json
    docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md

Exit status is always 0 on a successful scoring pass, including when
the forward sample is empty -- this is a research reporter and must
never be able to fail a workflow another process depends on.
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_EDGELAB_SCRIPTS_DIR = os.path.join(_ROOT, "scripts", "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab.storage import read_records
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import independent_unit_count
from lib.edgelab.research import frozen_forward_scorer as ffs

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
SETTLEMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "settlements")
MODEL_EVALUATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "model_evaluations")
OBSERVATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "observations")

FROZEN_0024 = os.path.join(ANALYTICS_DIR, "frozen_mlb_rsch_0024_forward_model.json")
FROZEN_0026 = os.path.join(ANALYTICS_DIR, "frozen_mlb_rsch_0026_forward_model.json")

OUT_JSON = os.path.join(ANALYTICS_DIR, "latest_frozen_forward_scorecard.json")
OUT_MD = os.path.join(_ROOT, "docs", "EDGELAB_FROZEN_FORWARD_SCORECARD.md")


# ── Forward corpus assembly (settled outcomes strictly after the cutoff) ──

def load_forward_settled_outcomes():
    """Only settlement partitions strictly after FORWARD_START_DATE."""
    out = {}
    if not os.path.isdir(SETTLEMENTS_DIR):
        return out
    for fn in sorted(os.listdir(SETTLEMENTS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        settle_date = fn.split(".jsonl")[0]
        if settle_date <= ffs.FORWARD_START_DATE:
            continue
        for d in read_records(os.path.join(SETTLEMENTS_DIR, fn)):
            ticker, outcome = d.get("marketTicker"), d.get("outcome")
            if not ticker or outcome not in ("YES", "NO"):
                continue
            out[ticker] = {"outcome": 1 if outcome == "YES" else 0, "settleDate": settle_date,
                           "gameId": d.get("gameId"), "marketFamily": d.get("marketFamily")}
    return out


def load_evaluated_rows():
    """Prospectively-captured pregame production evaluations (last per
    ticker). Every field here predates its own settlement."""
    by_ticker = {}
    if not os.path.isdir(MODEL_EVALUATIONS_DIR):
        return by_ticker
    for fn in sorted(os.listdir(MODEL_EVALUATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in read_records(os.path.join(MODEL_EVALUATIONS_DIR, fn)):
            if d.get("evaluationStatus") != "EVALUATED":
                continue
            model_p = d.get("modelFairProbability")
            if model_p is None:
                continue
            ticker, created = d.get("marketTicker"), d.get("createdAt") or ""
            if not ticker:
                continue
            prev = by_ticker.get(ticker)
            if prev is None or created > prev["createdAt"]:
                by_ticker[ticker] = {
                    "createdAt": created, "modelP": round(float(model_p) / 100.0, 6),
                    "gameId": d.get("gameId"), "marketFamily": d.get("marketFamily"),
                    "dataQuality": d.get("dataQuality"),
                    "lineupConfirmationState": d.get("lineupConfirmationState"),
                }
    return by_ticker


def load_pregame_fair_prices():
    """Vig-free midpoint from the latest VALID PREGAME observation per
    ticker -- the same canonical definition MLB-RSCH-0024/0026 froze."""
    best = {}
    if not os.path.isdir(OBSERVATIONS_DIR):
        return best
    for fn in sorted(os.listdir(OBSERVATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in read_records(os.path.join(OBSERVATIONS_DIR, fn)):
            if not d.get("isValidPregameObservation") or d.get("gameStartedAtCapture"):
                continue
            yes_bid, yes_ask = d.get("yesBid"), d.get("yesAsk")
            if yes_bid is None or yes_ask is None:
                continue
            ticker, captured = d.get("marketTicker"), d.get("capturedAt") or ""
            if not ticker:
                continue
            if ticker not in best or captured > best[ticker]["capturedAt"]:
                best[ticker] = {"capturedAt": captured, "yesBid": yes_bid, "yesAsk": yes_ask,
                                "marketFair": round(((yes_bid + yes_ask) / 2.0) / 100.0, 6),
                                "executableAsk": round(yes_ask / 100.0, 6)}
    return best


def build_forward_rows():
    outcomes = load_forward_settled_outcomes()
    evaluated = load_evaluated_rows()
    fair = load_pregame_fair_prices()
    rows, missing_eval, missing_fair = [], 0, 0
    for ticker, o in outcomes.items():
        ev = evaluated.get(ticker)
        fp = fair.get(ticker)
        if ev is None:
            missing_eval += 1
            continue
        if fp is None:
            missing_fair += 1
            continue
        rows.append({
            "marketTicker": ticker, "gameId": ev.get("gameId") or o.get("gameId") or ticker,
            "family": o.get("marketFamily") or ev.get("marketFamily") or "UNKNOWN",
            "settleDate": o["settleDate"], "outcome": o["outcome"],
            "modelP": ev["modelP"], "marketFair": fp["marketFair"],
            "executableAsk": fp["executableAsk"], "yesBid": fp["yesBid"],
            "dataQuality": ev.get("dataQuality"), "lineupConfirmationState": ev.get("lineupConfirmationState"),
        })
    return sorted(rows, key=lambda r: (r["settleDate"], r["marketTicker"])), {
        "settledForwardTickers": len(outcomes), "excludedNoEvaluation": missing_eval,
        "excludedNoFairPrice": missing_fair, "joinedRows": len(rows),
    }


# ── Fixed segment keys (copied from the frozen experiments) ──────────────

def _price_band_key(r):
    for lo, hi in ffs.PRICE_BANDS:
        if lo <= r["marketFair"] < hi:
            return f"{lo:.1f}_{hi:.1f}"
    return None


def _disagreement_band_key(r):
    d = abs(r["modelP"] - r["marketFair"])
    for lo, hi in ffs.DISAGREEMENT_BANDS:
        if lo <= d < hi:
            return f"{lo:.2f}_{hi:.2f}"
    return None


def _input_quality_key(r):
    return "HIGH_QUALITY_INPUT" if (r.get("dataQuality") == "full" or r.get("lineupConfirmationState") == "CONFIRMED") else "LOWER_OR_UNKNOWN_INPUT"


def _direction_key(r):
    return "MODEL_ABOVE_MARKET" if r["modelP"] > r["marketFair"] else "MODEL_BELOW_MARKET"


def forward_economics(rows, prob_fn):
    """SECONDARY, descriptive. Executable ask / bid-derived NO price,
    canonical taker fee, $1 per contract. No threshold is fitted and no
    betting rule is derived from this."""
    opps, gross, fees, wins = 0, 0.0, 0.0, 0
    for r in rows:
        p = prob_fn(r)
        yes_price = min(max(r["executableAsk"], 0.01), 0.99)
        no_price = min(max(1 - r["yesBid"] / 100.0, 0.01), 0.99)
        if p > yes_price:
            price, won = yes_price, r["outcome"] == 1
        elif (1 - p) > no_price:
            price, won = no_price, r["outcome"] == 0
        else:
            continue
        opps += 1
        gross += (1 - price) if won else (-price)
        fees += taker_fee(1, price)
        wins += 1 if won else 0
    net = gross - fees
    return {"opportunities": opps, "wins": wins,
            "winRate": round(wins / opps, 4) if opps else None,
            "grossPl": round(gross, 4), "fees": round(fees, 4), "netPl": round(net, 4),
            "roiPerContract": round(net / opps, 4) if opps else None,
            "note": "descriptive only -- executable prices, canonical taker fee; no threshold fitted, no betting rule implied"}


def main():
    frozen_0024 = ffs.load_frozen_artifact(FROZEN_0024) if os.path.exists(FROZEN_0024) else None
    frozen_0026 = ffs.load_frozen_artifact(FROZEN_0026) if os.path.exists(FROZEN_0026) else None

    rows, coverage = build_forward_rows()
    n_games = independent_unit_count(rows, key="gameId")
    checkpoint = ffs.classify_checkpoint(len(rows), n_games)
    dates = sorted({r["settleDate"] for r in rows})
    families = sorted({r["family"] for r in rows})

    print(f"[frozen-forward-scorer] FORWARD window: settle > {ffs.FORWARD_START_DATE}")
    print(f"[frozen-forward-scorer] rows={len(rows)} games={n_games} dates={len(dates)} families={len(families)}")
    print(f"[frozen-forward-scorer] checkpoint={checkpoint['checkpoint']} ({checkpoint['label']})")

    def market_fn(r):
        return r["marketFair"]

    def model_fn(r):
        return r["modelP"]

    report = {
        "scorecardId": "FROZEN_FORWARD_SCORECARD",
        "forwardWindow": f"settlement date strictly after {ffs.FORWARD_START_DATE}",
        "coverage": {**coverage, "rows": len(rows), "independentGames": n_games,
                     "independentDates": len(dates), "dates": dates,
                     "tickers": len({r["marketTicker"] for r in rows}), "families": families},
        "checkpoint": checkpoint,
        "frozenArtifacts": {
            "MLB-RSCH-0024": ({"candidateId": frozen_0024.get("candidateId"), "alpha": frozen_0024.get("alpha"),
                               "trainingEndDate": frozen_0024.get("trainingEndDate"), "version": frozen_0024.get("version")}
                              if frozen_0024 else None),
            "MLB-RSCH-0026": ({"candidateId": frozen_0026.get("candidateId"), "beta": frozen_0026.get("beta"),
                               "base": frozen_0026.get("base"), "trainingEndDate": frozen_0026.get("trainingEndDate"),
                               "version": frozen_0026.get("version")} if frozen_0026 else None),
        },
        "governance": {
            "refitPerformed": False, "frozenArtifactsMutated": False,
            "productionChanged": False, "newSegmentsInvented": False,
            "statusVocabularyExcludesProductionApproved": True,
        },
    }

    if not rows:
        report["status"] = ffs.INSUFFICIENT
        report["statusReasons"] = [
            f"no settled rows after {ffs.FORWARD_START_DATE} yet -- the FORWARD window has not begun accumulating",
            "health only: nothing is interpreted, no frozen parameter is touched",
        ]
        report["healthOnly"] = True
    else:
        health_only = ffs.checkpoint_rank(checkpoint["checkpoint"]) < ffs.checkpoint_rank("CHECKPOINT_1")

        # ---- MLB-RSCH-0022 reference findings ----
        m1_vs_m0 = ffs.paired_delta(rows, model_fn, market_fn)
        rsch0022 = {
            "marketM0": ffs.score_forecaster(rows, market_fn),
            "productionM1": ffs.score_forecaster(rows, model_fn),
            "productionMinusMarket": m1_vs_m0,
            "familyRanking": ffs.segment_scores(rows, model_fn, market_fn, lambda r: r["family"], "family"),
            "inputQuality": ffs.segment_scores(rows, model_fn, market_fn, _input_quality_key, "inputQuality"),
            "disagreementBands": ffs.segment_scores(rows, model_fn, market_fn, _disagreement_band_key, "disagreementBand"),
            "priceBands": ffs.segment_scores(rows, model_fn, market_fn, _price_band_key, "priceBand"),
            "direction": ffs.segment_scores(rows, model_fn, market_fn, _direction_key, "direction"),
        }
        m1_direction = ffs.per_date_direction(rows, model_fn, market_fn)
        rsch0022_status, rsch0022_reasons = ffs.decide_status(
            checkpoint["checkpoint"], m1_vs_m0, m1_direction, rsch0022["familyRanking"])
        rsch0022["perDateDirection"] = m1_direction
        rsch0022["status"] = rsch0022_status
        rsch0022["statusReasons"] = rsch0022_reasons
        rsch0022["frozenFindingUnderTest"] = "MLB-RSCH-0022 found production LOSES to Kalshi in every family; a negative delta here would CONTRADICT that frozen finding."
        report["MLB-RSCH-0022"] = rsch0022

        # ---- MLB-RSCH-0024: frozen alpha, never refit ----
        if frozen_0024:
            alpha = frozen_0024["alpha"]

            def m2_fn(r):
                return ffs.apply_frozen_residual(r["modelP"], r["marketFair"], alpha)

            m2_vs_m0 = ffs.paired_delta(rows, m2_fn, market_fn)
            fam = ffs.segment_scores(rows, m2_fn, market_fn, lambda r: r["family"], "family")
            direction = ffs.per_date_direction(rows, m2_fn, market_fn)
            status, reasons = ffs.decide_status(checkpoint["checkpoint"], m2_vs_m0, direction, fam)
            report["MLB-RSCH-0024"] = {
                "alphaUsed": alpha, "alphaRefit": False,
                "M0_market": ffs.score_forecaster(rows, market_fn),
                "M1_production": ffs.score_forecaster(rows, model_fn),
                "M2_frozenResidual": ffs.score_forecaster(rows, m2_fn),
                "M2_minus_M0": m2_vs_m0, "M1_minus_M0": m1_vs_m0,
                "familySegments": fam, "perDateDirection": direction,
                "status": status, "statusReasons": reasons,
                "economics": forward_economics(rows, m2_fn) if not health_only else {"note": "health only -- economics not computed below CHECKPOINT_1"},
            }

        # ---- MLB-RSCH-0026: frozen beta, never refit ----
        if frozen_0026:
            beta, base = frozen_0026["beta"], frozen_0026["base"]

            def shrink_fn(r):
                return ffs.apply_frozen_shrink(r["marketFair"], beta, base)

            shrink_vs_market = ffs.paired_delta(rows, shrink_fn, market_fn)
            fam = ffs.segment_scores(rows, shrink_fn, market_fn, lambda r: r["family"], "family")
            bands = ffs.segment_scores(rows, shrink_fn, market_fn, _price_band_key, "priceBand")
            direction = ffs.per_date_direction(rows, shrink_fn, market_fn)
            status, reasons = ffs.decide_status(checkpoint["checkpoint"], shrink_vs_market, direction, fam)
            report["MLB-RSCH-0026"] = {
                "betaUsed": beta, "baseUsed": base, "betaRefit": False,
                "rawMarket": ffs.score_forecaster(rows, market_fn),
                "frozenShrink": ffs.score_forecaster(rows, shrink_fn),
                "shrinkMinusMarket": shrink_vs_market,
                "familySegments": fam, "priceBands": bands, "perDateDirection": direction,
                "status": status, "statusReasons": reasons,
                "economics": forward_economics(rows, shrink_fn) if not health_only else {"note": "health only -- economics not computed below CHECKPOINT_1"},
            }

        statuses = [report[k]["status"] for k in ("MLB-RSCH-0022", "MLB-RSCH-0024", "MLB-RSCH-0026") if k in report]
        report["status"] = ffs.INSUFFICIENT if all(s == ffs.INSUFFICIENT for s in statuses) else checkpoint["label"]
        report["healthOnly"] = health_only

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    _write_markdown(report)
    print(f"[frozen-forward-scorer] status={report['status']}")
    print(f"[frozen-forward-scorer] wrote {OUT_JSON} and {OUT_MD}")
    return report


def _write_markdown(report):
    cov, cp = report["coverage"], report["checkpoint"]
    lines = [
        "# EdgeLab Frozen Forward Scorecard",
        "",
        "Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**",
        "Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).",
        "",
        f"- **Forward window:** {report['forwardWindow']}",
        f"- **Status:** `{report['status']}`",
        f"- **Checkpoint:** `{cp['checkpoint']}` ({cp['label']}) — {cov['rows']} rows / {cov['independentGames']} games / {cov['independentDates']} dates",
        "",
        "## Frozen artifacts under test (parameters read-only, never re-estimated)",
        "",
        "| Experiment | Frozen parameter | Training end |",
        "|---|---|---|",
    ]
    fa = report.get("frozenArtifacts", {})
    if fa.get("MLB-RSCH-0024"):
        a = fa["MLB-RSCH-0024"]
        lines.append(f"| MLB-RSCH-0024 | alpha = {a['alpha']} | {a['trainingEndDate']} |")
    if fa.get("MLB-RSCH-0026"):
        b = fa["MLB-RSCH-0026"]
        lines.append(f"| MLB-RSCH-0026 | beta = {b['beta']}, base = {b['base']} | {b['trainingEndDate']} |")
    lines += ["", "## Coverage", "",
              f"- settled forward tickers: {cov.get('settledForwardTickers')}",
              f"- joined rows: {cov.get('joinedRows')} (excluded: {cov.get('excludedNoEvaluation')} without a pregame evaluation, {cov.get('excludedNoFairPrice')} without a pregame fair price)",
              f"- families: {', '.join(cov.get('families') or []) or '(none yet)'}",
              f"- dates: {', '.join(cov.get('dates') or []) or '(none yet)'}", ""]

    if report.get("statusReasons"):
        lines += ["## Status reasons", ""] + [f"- {r}" for r in report["statusReasons"]] + [""]

    for key in ("MLB-RSCH-0022", "MLB-RSCH-0024", "MLB-RSCH-0026"):
        if key not in report:
            continue
        blk = report[key]
        lines += [f"## {key}", "", f"- **status:** `{blk['status']}`"]
        for r in blk.get("statusReasons", []):
            lines.append(f"  - {r}")
        for label, field in (("production − market", "productionMinusMarket"),
                             ("M2 (frozen α) − M0", "M2_minus_M0"),
                             ("frozen β shrink − market", "shrinkMinusMarket")):
            d = blk.get(field)
            if d and d.get("brierDelta") is not None:
                lines.append(f"- **{label}:** Brier Δ {d['brierDelta']}, log-loss Δ {d['logLossDelta']}, CI {d.get('brierDeltaCI')}")
        lines.append("")

    lines += ["## Governance", ""]
    for k, v in report["governance"].items():
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # a research reporter must never fail a workflow
        print(f"[frozen-forward-scorer] non-fatal error: {exc}", file=sys.stderr)
    sys.exit(0)
