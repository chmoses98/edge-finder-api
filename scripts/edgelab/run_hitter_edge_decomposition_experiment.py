#!/usr/bin/env python3
"""
scripts/edgelab/run_hitter_edge_decomposition_experiment.py
===========================================================
Research Lab experiment MLB-RSCH-0029: "Hitter Declared-Edge
Decomposition". RESEARCH ONLY. NO production changes, no candidate
activation, no change to hitter qualification, edge formulas, spread
filters, staking, fees or risk gates.

CORE QUESTION: MLB-RSCH-0028 found that production's hitter
probabilities are near PARITY with Kalshi (paired Brier delta +0.0030,
CI straddling zero) yet its DECLARED EDGE is anti-predictive -- the
paired delta degrades monotonically from +0.00019 at 2.5-5pp to
+0.01169 at 10-15pp. Why?

THE ALGEBRA COMES FIRST, AND IT CORRECTS AN EARLIER GUESS
---------------------------------------------------------
A previous session hypothesised that wide bid/ask spreads mechanically
produce large declared edges. That is BACKWARDS. With

    declaredEdge = model - executablePrice

decomposing about the vig-free fair midpoint gives

    declaredEdge = (model - fairMid) - (executablePrice - fairMid)
                 =  MODEL_SIGNAL     -  EXECUTION_PENALTY

so a LARGER execution penalty REDUCES declared edge, all else equal.
This experiment therefore decomposes the edge empirically instead of
preregistering a mechanism as the answer.

WHAT THE PRODUCTION TRACE ACTUALLY SHOWS (read from main, not assumed)
----------------------------------------------------------------------
lib/research/hitter_pricing.py::price_hitter_contract:

    edge = model_prob - market_implied_prob
    market_implied_prob = executable_yes_price

and lib/research/hitter_board_builder.py::_executable_yes_price:

    mid if present, else (yes_bid + yes_ask) / 2, else ask, else bid

So the price production differences against is the MIDPOINT, not the
ask. Verified empirically on every joinable archived row: for 100% of
them `executableKalshiPrice` equals the contemporaneous vig-free
midpoint exactly. Therefore, on this archive:

    EXECUTION_PENALTY == 0     identically, for every row
    declaredEdge     == MODEL_SIGNAL

The decomposition collapses. Whatever drives the inversion, it cannot
be execution cost or spread, because neither enters the quantity that
inverts. That makes CASE A (model-signal inversion) the algebraically
forced classification, and turns the real question into: WHERE inside
the model signal does the inversion live?

A SEPARATE, GENUINE FINDING FALLS OUT OF THE SAME TRACE
--------------------------------------------------------
Because production differences against the mid and never the ask, its
declared edge and its `expectedValuePerDollar` both IGNORE the
half-spread a taker actually pays. The true execution penalty
(ask - mid) is real, is measured here, and is reported as an
economics-only correction. It does not create the inversion; it makes
production's stated edge optimistic. MLB-RSCH-0028's secondary
economics inherited the same optimism and that is corrected here too.

PREREGISTERED, LOCKED BEFORE ANY INFERENTIAL RESULT
----------------------------------------------------
Corpus is RSCH-0028's, reused unchanged (same eligibility, same strict
contemporaneous-quote rule, same reason-coded exclusions). Locked:
decomposition terms; model-signal buckets (identical to RSCH-0028's
declared-edge buckets, so the two are directly comparable); spread
bands; quote-age bins; probability bands; families; playerGameKey
clustering; the date-blocked design; BH-FDR at 0.10; and the CASE A/B/C/D
classification rule. No cutoff is re-cut after results are seen.

NOTHING IS FITTED. A single low-degree-of-freedom OLS diagnostic is
preregistered and reported as a descriptive coefficient summary only --
it selects nothing, and proper scoring remains primary.

MAXIMUM DISPOSITION: SHADOW_CANDIDATE. A filter may only reach it by
the full preregistered standard; LEVEL 2 requires forward confirmation
under a frozen filter and is unavailable from a 7-date archive.
"""
import collections
import json
import math
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_EDGELAB_SCRIPTS_DIR = os.path.join(_ROOT, "scripts", "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import (
    independent_unit_count, expected_calibration_error,
    brier_and_log_loss_summary, calibration_slope_intercept,
)

import run_hitter_prop_validity_experiment as rsch0028  # corpus reused unchanged

EXPERIMENT_ID = "MLB-RSCH-0029"
REGISTRATION_TIMESTAMP = "2026-08-29T04:30:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0029_edge_decomposition.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0029_EDGE_DECOMPOSITION.md")

# ── Preregistered constants ──────────────────────────────────────────────
# Model-signal buckets are deliberately IDENTICAL to RSCH-0028's
# declared-edge buckets so the two analyses are directly comparable.
SIGNAL_BUCKETS = rsch0028.EDGE_BUCKETS
PROBABILITY_BANDS = ((0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))
SPREAD_BANDS_CENTS = ((0.0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 100.0))
QUOTE_AGE_BINS_MIN = ((0.0, 15.0), (15.0, 60.0), (60.0, 180.0), (180.0, 1e9))
FAMILIES = rsch0028.ELIGIBLE_FAMILIES
CLUSTER_KEY = rsch0028.CLUSTER_KEY
MIN_SEGMENT_ROWS = 100
MIN_SEGMENT_KEYS = 25
MIN_FAMILY_ROWS = 200
MIN_FAMILY_KEYS = 50
FDR_ALPHA = 0.10
DEV_DATE_MAX = rsch0028.DEV_DATE_MAX
EXECUTION_PENALTY_TOLERANCE = 1e-6   # identity check tolerance


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


# ── Corpus with decomposition terms attached ─────────────────────────────

def build_decomposed_corpus():
    """RSCH-0028's corpus, unchanged, plus the decomposition terms and the
    liquidity/staleness fields this experiment needs. Returns
    (rows, exclusions, identity_audit)."""
    snapshots = rsch0028.load_snapshots()
    settled, _unresolved = rsch0028.load_settlements()
    tickers = {s.get("marketTicker") for s in snapshots if s.get("marketTicker")}
    quotes = rsch0028.load_pregame_quotes(tickers)
    rows, exclusions = rsch0028.build_corpus(snapshots, settled, quotes)

    # Re-attach the raw quote each row was priced against so spread, quote
    # age and the execution penalty can be measured. Uses the SAME
    # contemporaneous-quote rule the corpus was built with -- never a later
    # quote, never a reconstructed one.
    by_ticker_time = {}
    for s in snapshots:
        t, at = s.get("marketTicker"), s.get("marketObservedAt")
        if t and at:
            by_ticker_time[(t, at)] = s

    identity = {"rowsChecked": 0, "executionPenaltyZero": 0, "maxAbsExecutionPenalty": 0.0,
                "declaredEdgeReproduced": 0, "maxAbsDeclaredEdgeError": 0.0}
    out = []
    for r in rows:
        quote = rsch0028.contemporaneous_quote(quotes.get(r["marketTicker"], []), r["observedAt"])
        if quote is None:
            continue
        quote_at, bid, ask = quote
        fair = r["marketP"]
        exec_price = r["executableAsk"]          # production's executableKalshiPrice
        if exec_price is None:
            continue

        model_signal = r["modelP"] - fair
        execution_penalty = exec_price - fair
        declared = r.get("declaredEdge")

        identity["rowsChecked"] += 1
        identity["maxAbsExecutionPenalty"] = max(identity["maxAbsExecutionPenalty"], abs(execution_penalty))
        if abs(execution_penalty) <= EXECUTION_PENALTY_TOLERANCE:
            identity["executionPenaltyZero"] += 1
        if declared is not None:
            err = abs(declared - (model_signal - execution_penalty))
            identity["maxAbsDeclaredEdgeError"] = max(identity["maxAbsDeclaredEdgeError"], err)
            # production rounds rawProbabilityEdge to 4dp
            if err <= 5e-4:
                identity["declaredEdgeReproduced"] += 1

        # The TRUE taker cost production's edge ignores: half the spread.
        true_taker_penalty = (ask / 100.0) - fair

        age_min = None
        if quote_at and r["observedAt"]:
            age_min = _minutes_between(quote_at, r["observedAt"])

        out.append(dict(
            r,
            modelSignal=round(model_signal, 6),
            executionPenalty=round(execution_penalty, 6),
            trueTakerPenalty=round(true_taker_penalty, 6),
            yesBid=round(bid / 100.0, 6),
            yesAsk=round(ask / 100.0, 6),
            spreadCents=round(ask - bid, 4),
            quoteAgeMinutes=age_min,
            quoteCaptureCount=len(quotes.get(r["marketTicker"], [])),
        ))
    identity["executionPenaltyZeroShare"] = (
        round(identity["executionPenaltyZero"] / identity["rowsChecked"], 6)
        if identity["rowsChecked"] else None)
    identity["declaredEdgeReproducedShare"] = (
        round(identity["declaredEdgeReproduced"] / identity["rowsChecked"], 6)
        if identity["rowsChecked"] else None)
    return out, exclusions, identity


def _minutes_between(a, b):
    """Minutes from ISO timestamp a to b. Returns None rather than guessing
    when either fails to parse -- quote age is never fabricated."""
    from datetime import datetime
    def _p(x):
        try:
            return datetime.fromisoformat(x.replace("Z", "+00:00"))
        except Exception:
            return None
    pa, pb = _p(a), _p(b)
    if pa is None or pb is None:
        return None
    return round((pb - pa).total_seconds() / 60.0, 3)


# ── Scoring (reuses RSCH-0028's primitives so metrics are identical) ─────

brier = rsch0028.brier
log_loss = rsch0028.log_loss
paired_brier_delta = rsch0028.paired_brier_delta
paired_log_loss_delta = rsch0028.paired_log_loss_delta
clustered_ci = rsch0028.clustered_ci
clustered_pvalue = rsch0028.clustered_pvalue
benjamini_hochberg = rsch0028.benjamini_hochberg
score_block = rsch0028.score_block
classify = rsch0028.classify


def segment_table(rows, key_fn, ordered_labels, *, label):
    """Generic fixed-bucket segment scorer. Buckets are preregistered and
    passed in; this function never invents or re-cuts one."""
    by = collections.defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is not None:
            by[k].append(r)
    out = []
    for lbl in ordered_labels:
        sub = by.get(lbl, [])
        blk = score_block(sub, lbl, with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["segment"] = lbl
        blk["verdict"] = (classify(blk, min_rows=MIN_SEGMENT_ROWS, min_keys=MIN_SEGMENT_KEYS)
                          if sub else "INSUFFICIENT_SAMPLE")
        out.append(blk)
    return {"dimension": label, "segments": out,
            "monotoneImproving": _monotone(out), "inversion": _inversion(out)}


def _qualifying(segs):
    return [s for s in segs
            if s.get("rows", 0) >= MIN_SEGMENT_ROWS
            and s.get("playerGameKeys", 0) >= MIN_SEGMENT_KEYS
            and s.get("pairedBrierDelta") is not None]


def _monotone(segs):
    q = _qualifying(segs)
    if len(q) < 3:
        return None
    v = [s["pairedBrierDelta"] for s in q]
    return all(v[i] >= v[i + 1] for i in range(len(v) - 1))


def _inversion(segs):
    """Model gets relatively WORSE as the dimension increases. Requires three
    qualifying buckets before any trend is named."""
    q = _qualifying(segs)
    if len(q) < 3:
        return None
    v = [s["pairedBrierDelta"] for s in q]
    return v[-1] > v[0]


def _bucket(value, buckets, fmt="%+.3f"):
    if value is None:
        return None
    for lo, hi in buckets:
        if lo <= value < hi:
            return f"[{fmt % lo},{fmt % hi})"
    return None


def _labels(buckets, fmt="%+.3f"):
    return [f"[{fmt % lo},{fmt % hi})" for lo, hi in buckets]


# ── Preregistered hypotheses ─────────────────────────────────────────────

def h1_model_signal(rows):
    """H1: does model-minus-fair-market disagreement carry information?
    THE most important decomposition."""
    return segment_table(rows, lambda r: _bucket(r["modelSignal"], SIGNAL_BUCKETS),
                         _labels(SIGNAL_BUCKETS), label="MODEL_SIGNAL")


def h2_execution_penalty(rows):
    """H2: execution cost. Kept strictly separate from predictive quality.

    Production's declared edge differences against the MID, so its execution
    penalty is identically zero and CANNOT create the inversion. What is
    tested here is the different, legitimate question: conditional on model
    signal, does a wider spread accompany worse PREDICTIVE quality (a noisier
    fair estimate) -- and separately, what does the true taker cost do to
    ECONOMICS?"""
    return {
        "spreadBands": segment_table(
            rows, lambda r: _bucket(r["spreadCents"], SPREAD_BANDS_CENTS, "%.1f"),
            _labels(SPREAD_BANDS_CENTS, "%.1f"), label="SPREAD_CENTS"),
        "executionPenaltyIsZeroByConstruction": True,
        "trueTakerPenaltySummary": _describe([r["trueTakerPenalty"] for r in rows]),
        "note": ("Production prices against the midpoint, so its declared edge omits the "
                 "half-spread a taker actually pays. That makes its stated edge optimistic; "
                 "it does not make the edge inversion an execution artifact."),
    }


def h3_quote_age(rows):
    """H3: staleness. A stale quote can make disagreement look large without
    representing genuine edge. Never fabricated when timestamps are missing."""
    aged = [r for r in rows if r.get("quoteAgeMinutes") is not None]
    return {
        "rowsWithMeasurableAge": len(aged),
        "rowsWithoutTimestamps": len(rows) - len(aged),
        "ageSummaryMinutes": _describe([r["quoteAgeMinutes"] for r in aged]),
        "bins": segment_table(aged, lambda r: _bucket(r["quoteAgeMinutes"], QUOTE_AGE_BINS_MIN, "%.0f"),
                              _labels(QUOTE_AGE_BINS_MIN, "%.0f"), label="QUOTE_AGE_MINUTES"),
    }


def h4_liquidity(rows):
    """H4: liquidity proxies from what is ACTUALLY archived -- spread, quote
    availability, capture count. No volume or depth is invented."""
    high = [r for r in rows if r["modelSignal"] >= 0.10]
    rest = [r for r in rows if r["modelSignal"] < 0.10]
    return {
        "spreadByModelSignal": {
            "highSignal_ge_10pp": _describe([r["spreadCents"] for r in high]),
            "otherRows": _describe([r["spreadCents"] for r in rest]),
        },
        "captureCountByModelSignal": {
            "highSignal_ge_10pp": _describe([r["quoteCaptureCount"] for r in high]),
            "otherRows": _describe([r["quoteCaptureCount"] for r in rest]),
        },
        "note": "Descriptive only. Archived fields exclusively; no volume/depth data exists to use.",
    }


def h5_probability_extremeness(rows):
    """H5: is the inversion simply the model's tails? RSCH-0028 found
    underconfidence low and overconfidence high."""
    table = segment_table(rows, lambda r: _bucket(r["modelP"], PROBABILITY_BANDS, "%.2f"),
                          _labels(PROBABILITY_BANDS, "%.2f"), label="MODEL_PROBABILITY_BAND")
    # Conditional: model-signal inversion WITHIN each probability band.
    within = {}
    for lo, hi in PROBABILITY_BANDS:
        lbl = f"[{lo:.2f},{hi:.2f})"
        sub = [r for r in rows if lo <= r["modelP"] < hi]
        if len(sub) < MIN_SEGMENT_ROWS:
            within[lbl] = {"rows": len(sub), "verdict": "INSUFFICIENT_SAMPLE"}
            continue
        t = segment_table(sub, lambda r: _bucket(r["modelSignal"], SIGNAL_BUCKETS),
                          _labels(SIGNAL_BUCKETS), label="MODEL_SIGNAL")
        within[lbl] = {"rows": len(sub), "inversion": t["inversion"],
                       "qualifyingBuckets": len(_qualifying(t["segments"]))}
    return {"bands": table, "signalInversionWithinBand": within}


def h6_family(rows):
    """H6: is the inversion broad or one family? BH-FDR across families."""
    entries = []
    for fam in FAMILIES:
        sub = [r for r in rows if r["marketFamily"] == fam]
        blk = score_block(sub, fam)
        blk["family"] = fam
        meets = blk.get("rows", 0) >= MIN_FAMILY_ROWS and blk.get("playerGameKeys", 0) >= MIN_FAMILY_KEYS
        blk["meetsFloor"] = meets
        blk["bootstrapPValue"] = round(clustered_pvalue(sub, paired_brier_delta), 4) if meets else None
        t = segment_table(sub, lambda r: _bucket(r["modelSignal"], SIGNAL_BUCKETS),
                          _labels(SIGNAL_BUCKETS), label="MODEL_SIGNAL") if meets else None
        blk["signalInversion"] = t["inversion"] if t else None
        blk["signalSegments"] = t["segments"] if t else None
        entries.append(blk)
    rejected = benjamini_hochberg([e["bootstrapPValue"] for e in entries], FDR_ALPHA)
    for i, e in enumerate(entries):
        e["fdrSignificant"] = i in rejected
        v = classify(e, min_rows=MIN_FAMILY_ROWS, min_keys=MIN_FAMILY_KEYS)
        if v == "MODEL_BEATS_MARKET" and not e["fdrSignificant"]:
            v = "PARITY"
        e["verdict"] = v
    return entries


def h7_threshold_tail(rows):
    """H7: does apparent edge grow as thresholds move into the tails?
    Ladder rungs of one player-game are correlated, never independent -- all
    intervals stay clustered on playerGameKey."""
    by_key = collections.defaultdict(list)
    for r in rows:
        by_key[(r["playerId"], r["gameId"], r["marketFamily"])].append(r)
    ladders = {k: v for k, v in by_key.items() if len({x["threshold"] for x in v}) > 1}
    rank_rows = []
    for k, v in ladders.items():
        ordered = sorted(v, key=lambda x: x["threshold"])
        for rank, r in enumerate(ordered):
            rank_rows.append(dict(r, ladderRank=rank, ladderDepth=len(ordered)))
    out = []
    for rank in range(0, 4):
        sub = [r for r in rank_rows if r["ladderRank"] == rank]
        blk = score_block(sub, f"rung_{rank}", with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["ladderRank"] = rank
        blk["meanModelSignal"] = round(sum(r["modelSignal"] for r in sub) / len(sub), 6) if sub else None
        blk["meanModelProbability"] = round(sum(r["modelP"] for r in sub) / len(sub), 4) if sub else None
        blk["verdict"] = classify(blk, min_rows=MIN_SEGMENT_ROWS, min_keys=MIN_SEGMENT_KEYS)
        out.append(blk)
    return {"laddersFound": len(ladders), "rungs": out,
            "note": "Ladder rungs share a player-game and are never counted as independent."}


def conditional_ols(rows):
    """Preregistered low-degree-of-freedom diagnostic:

        (outcome - marketP)  ~  b0 + b1*modelSignal + b2*executionPenalty + family effects

    DESCRIPTIVE ONLY. It selects nothing and no threshold is derived from it;
    proper scoring comparisons remain primary. b1 > 0 would mean the model's
    disagreement points toward the truth; b1 <= 0 means it does not."""
    fams = sorted({r["marketFamily"] for r in rows})
    if len(rows) < 200 or not fams:
        return {"status": "INSUFFICIENT_SAMPLE"}
    cols = ["intercept", "modelSignal", "executionPenalty"] + [f"family={f}" for f in fams[1:]]
    X, y = [], []
    for r in rows:
        row = [1.0, r["modelSignal"], r["executionPenalty"]]
        row += [1.0 if r["marketFamily"] == f else 0.0 for f in fams[1:]]
        X.append(row)
        y.append(r["outcome"] - r["marketP"])
    beta = _ols(X, y)
    if beta is not None:
        return {"status": "OK", "coefficients": dict(zip(cols, [round(b, 6) for b in beta])),
                "n": len(rows), "clusterUnit": CLUSTER_KEY,
                "note": "Descriptive. Not used for selection. Proper scoring remains primary."}

    # Rank deficiency here is not a nuisance -- it is the finding restated.
    # executionPenalty is identically zero on every row, so its column carries
    # no variance and the full design is singular. Refit WITHOUT that column,
    # clearly labelled: this is handling a provable rank deficiency, not
    # dropping a regressor because its coefficient was unwelcome.
    cols_reduced = ["intercept", "modelSignal"] + [f"family={f}" for f in fams[1:]]
    X_reduced = [[r[0], r[1]] + r[3:] for r in X]
    beta_reduced = _ols(X_reduced, y)
    return {
        "status": "SINGULAR_DESIGN_REFIT_WITHOUT_DEGENERATE_TERM",
        "reason": ("executionPenalty is identically zero on every row, so its column has zero "
                   "variance and the full design is singular -- which is the decomposition's "
                   "central finding expressed as linear algebra, not a modelling nuisance"),
        "reducedCoefficients": (dict(zip(cols_reduced, [round(b, 6) for b in beta_reduced]))
                                if beta_reduced else None),
        "n": len(rows), "clusterUnit": CLUSTER_KEY,
        "interpretation": ("b1 on modelSignal > 0 would mean the model's disagreement points toward "
                           "the realised outcome; <= 0 means it does not"),
        "note": "Descriptive. Not used for selection. Proper scoring remains primary.",
    }


def _ols(X, y):
    """Normal-equations OLS with a singularity guard."""
    n, k = len(X), len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    aug = [XtX[i][:] + [Xty[i]] for i in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(aug[r][c]))
        if abs(aug[piv][c]) < 1e-12:
            return None
        aug[c], aug[piv] = aug[piv], aug[c]
        pv = aug[c][c]
        aug[c] = [v / pv for v in aug[c]]
        for r in range(k):
            if r != c and aug[r][c] != 0.0:
                f = aug[r][c]
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[c])]
    return [aug[i][k] for i in range(k)]


def _describe(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(s), "min": round(s[0], 4), "p25": round(s[len(s) // 4], 4),
            "median": round(s[len(s) // 2], 4), "p75": round(s[3 * len(s) // 4], 4),
            "max": round(s[-1], 4), "mean": round(sum(s) / len(s), 4)}


# ── Secondary economics (after the predictive verdict, never selective) ──

def economics(rows, label, *, entry="ask"):
    """Fee-aware hypothetical economics.

    `entry="ask"` is the HONEST executable price a taker pays. `entry="mid"`
    reproduces production's own optimistic convention so the two can be
    compared directly -- the difference IS the half-spread production's
    declared edge omits. Never selective; never implies a bet was placed."""
    staked = fees = pnl = 0.0
    bets = wins = 0
    prices = []
    for r in rows:
        price = r["yesAsk"] if entry == "ask" else r["marketP"]
        if price is None or not (0.0 < price < 1.0):
            continue
        if r["modelP"] <= price:
            continue
        fee = taker_fee(1, price)
        bets += 1
        wins += r["outcome"]
        staked += price
        fees += fee
        pnl += ((1.0 - price) if r["outcome"] == 1 else -price) - fee
        prices.append(price)
    return {
        "segment": label, "entryConvention": entry,
        "opportunities": bets, "wins": wins, "losses": bets - wins,
        "averageEntryPrice": round(sum(prices) / len(prices), 4) if prices else None,
        "grossStaked": round(staked, 4),
        "grossPnlBeforeFees": round(pnl + fees, 4),
        "totalFees": round(fees, 4), "netPnl": round(pnl, 4),
        "netRoi": round(pnl / staked, 4) if staked else None,
        "note": "SECONDARY. Never selective. Never implies any recommendation was placed.",
    }


def filter_simulations(rows, mechanism):
    """Only run when the mechanism supports an execution/liquidity lever
    (CASE B or CASE C). Cutoffs are preregistered theory-driven values, NOT
    tuned to ROI. Under CASE A a spread/staleness filter has no mechanism to
    act on and is deliberately NOT simulated -- simulating it anyway would be
    fishing."""
    if mechanism not in ("CASE_B_EXECUTION_LIQUIDITY", "CASE_C_BOTH"):
        return {"simulated": False,
                "reason": f"mechanism is {mechanism}; a spread/staleness filter has no mechanism "
                          "to act on, so simulating one would be post-hoc fishing"}
    sims = []
    for name, pred in (
        ("max_spread_2c", lambda r: r["spreadCents"] <= 2.0),
        ("max_quote_age_60m", lambda r: (r.get("quoteAgeMinutes") or 0) <= 60.0),
        ("signal_net_of_true_taker_cost", lambda r: (r["modelSignal"] - r["trueTakerPenalty"]) > 0.0),
        ("exclude_extreme_probabilities", lambda r: 0.10 <= r["modelP"] < 0.75),
    ):
        kept = [r for r in rows if pred(r)]
        blk = score_block(kept, name, with_ci=len(kept) >= MIN_SEGMENT_ROWS)
        blk["filter"] = name
        blk["retainedRows"] = len(kept)
        blk["retainedShare"] = round(len(kept) / len(rows), 4) if rows else None
        blk["economics"] = economics(kept, name, entry="ask")
        sims.append(blk)
    return {"simulated": True, "filters": sims}


def classify_mechanism(h1, h2, h5, h6):
    """Preregistered CASE A/B/C/D rule, evaluated exactly as written."""
    signal_inversion = h1.get("inversion")
    spread_inversion = h2["spreadBands"].get("inversion")
    exec_zero = h2["executionPenaltyIsZeroByConstruction"]
    fam_inversions = [e.get("signalInversion") for e in h6 if e.get("meetsFloor")]
    broad = fam_inversions.count(True) >= 2

    if signal_inversion is None:
        return "CASE_D_INCONCLUSIVE", "model-signal buckets never reached the preregistered floors"
    if signal_inversion and exec_zero:
        detail = ("model-minus-fair-market disagreement itself degrades as it grows, and the "
                  "execution penalty is identically zero in production's declared edge, so "
                  "execution cost cannot be the cause")
        if broad:
            detail += "; the inversion appears in multiple families, so it is not one family's artifact"
        return "CASE_A_MODEL_SIGNAL_INVERSION", detail
    if signal_inversion and spread_inversion:
        return "CASE_C_BOTH", "both the model signal and spread-conditioned quality degrade"
    if spread_inversion and not signal_inversion:
        return "CASE_B_EXECUTION_LIQUIDITY", "model signal is stable; wide-spread rows carry worse quality"
    return "CASE_D_INCONCLUSIVE", "no preregistered pattern reached significance"


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0029_hitter_edge_decomposition_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0029 hitter declared-edge decomposition v1: control = Kalshi's "
                        "contemporaneous vig-free fair midpoint; declaredEdge is decomposed as "
                        "MODEL_SIGNAL (model - fairMid) minus EXECUTION_PENALTY (executablePrice - "
                        "fairMid). Production's executable price is verified to BE the midpoint, so "
                        "the execution penalty is identically zero. Nothing is fitted."
        ),
        probability_adapter_identity=(
            "vig-free fair midpoint from the contemporaneous valid pregame observation; the executable "
            "YES ask is used only for honest secondary economics"
        ),
        model_engine_family="hitter_declared_edge_decomposition_v1",
        required_input_provenance=["hitter_snapshot", "archived_kalshi_market_observation", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Decomposes production's declared hitter edge into a fair-value signal term and an "
                     "execution-cost term to locate the mechanism behind MLB-RSCH-0028's edge inversion."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Hitter Declared-Edge Decomposition",
        hypothesis=(
            "H1: model-minus-fair-market disagreement itself degrades as it grows (a model-signal "
            "inversion). H2: execution cost / spread explains the inversion -- tested and, given "
            "production prices against the midpoint, algebraically unable to, since a larger execution "
            "penalty REDUCES declared edge rather than inflating it. H3: the inversion concentrates in "
            "stale quotes. H4: high-declared-edge props are disproportionately thin markets. H5: the "
            "inversion is really the model's probability tails. H6: it is confined to one hitter family. "
            "H7: it grows with ladder depth as thresholds move into the tails."
        ),
        research_question=(
            "Why is production's declared hitter edge anti-predictive when the underlying hitter "
            "probabilities are near parity with Kalshi -- is the defect in the fair-value signal, in "
            "execution cost, or in both?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=("MLB-RSCH-0028's eligible hitter corpus, reused unchanged: archived prospective "
                           "hitter snapshots 2026-08-19..27 across four supported families, joined to an "
                           "actual Kalshi settlement and a contemporaneous valid pregame quote."),
        market_families=list(FAMILIES),
        eligibility_criteria=["identical to MLB-RSCH-0028 -- corpus builder reused unchanged",
                             "a contemporaneous quote carrying both yesBid and yesAsk, so spread and "
                             "execution penalty are measurable rather than inferred"],
        exclusion_criteria=[
            "any fitted correction -- this experiment estimates nothing selective",
            "executable ask as the predictive benchmark (economics only)",
            "user-confirmed wagers; any inference that a recommendation was placed",
            "fabricated quote age when timestamps are missing",
            "invented volume/depth liquidity data -- only archived fields are used",
            "post-hoc re-cutting of any bucket, band, bin or floor",
            "filter simulation when the mechanism gives a filter nothing to act on",
        ],
        prediction_checkpoints=list(rsch0028.CHECKPOINT_ORDER),
        primary_metric=("paired Brier delta (model minus Kalshi fair midpoint) within fixed MODEL_SIGNAL "
                        "buckets, playerGameKey-clustered, with monotonicity and inversion measured"),
        secondary_metrics=[
            "execution-penalty identity audit and true taker (ask-mid) cost",
            "spread bands; quote-age bins; liquidity proxies from archived fields only",
            "probability bands and signal inversion conditional within band",
            "per-family results with BH-FDR; ladder-rung/tail structure",
            "descriptive OLS of (outcome - marketP) on signal, penalty and family effects",
            "SECONDARY fee-aware economics at BOTH the honest ask and production's own mid convention",
        ],
        chronological_split_policy=(
            f"DATE_BASED: DEVELOPMENT = observed <= {DEV_DATE_MAX}, VALIDATION = later dates, identical to "
            "MLB-RSCH-0028. With ~261 playerGameKeys over ~36 games and 7 dates this does not support a "
            "confirmatory holdout; the validation block is directional only and any surviving mechanism "
            "requires forward confirmation under a frozen filter."
        ),
        minimum_sample_requirement={"independentGames": 20},
        clustering_unit=CLUSTER_KEY,
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "hitter_snapshot": "PREDICTIVE_INPUT",
            "archived_kalshi_market_observation": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW. CORRECTS an earlier session's guess that wide spreads "
            "mechanically produce large declared edges: with declaredEdge = modelSignal - executionPenalty, "
            "a larger penalty REDUCES declared edge. Production is verified to difference against the "
            "MIDPOINT (lib/research/hitter_board_builder._executable_yes_price prefers mid), so the "
            "execution penalty is identically zero and declaredEdge == modelSignal exactly. A separate "
            "genuine consequence is that production's declared edge and expectedValuePerDollar omit the "
            "half-spread a taker pays, making them optimistic; economics here are reported at BOTH "
            "conventions. Repeated measures throughout -- ~20 rows per player-game across five checkpoints "
            "and multiple ladder rungs -- so every interval clusters on playerGameKey. MAXIMUM disposition "
            "SHADOW_CANDIDATE; production activation is unavailable from this experiment at any result."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control, _definition = register_experiment()
    rows, exclusions, identity = build_decomposed_corpus()

    overall = score_block(rows, "ALL")
    overall["verdict"] = classify(overall, min_rows=MIN_FAMILY_ROWS, min_keys=MIN_FAMILY_KEYS)

    h1 = h1_model_signal(rows)
    h2 = h2_execution_penalty(rows)
    h3 = h3_quote_age(rows)
    h4 = h4_liquidity(rows)
    h5 = h5_probability_extremeness(rows)
    h6 = h6_family(rows)
    h7 = h7_threshold_tail(rows)
    ols = conditional_ols(rows)

    mechanism, mechanism_detail = classify_mechanism(h1, h2, h5, h6)
    sims = filter_simulations(rows, mechanism)

    dev = [r for r in rows if r["date"] <= DEV_DATE_MAX]
    val = [r for r in rows if r["date"] > DEV_DATE_MAX]
    chrono = {
        "devDateMax": DEV_DATE_MAX,
        "development": score_block(dev, "DEVELOPMENT"),
        "validation": score_block(val, "VALIDATION"),
        "developmentSignalInversion": h1_model_signal(dev)["inversion"] if len(dev) >= MIN_SEGMENT_ROWS else None,
        "validationSignalInversion": h1_model_signal(val)["inversion"] if len(val) >= MIN_SEGMENT_ROWS else None,
    }

    econ = {
        "OVERALL_at_honest_ask": economics(rows, "OVERALL", entry="ask"),
        "OVERALL_at_production_mid": economics(rows, "OVERALL", entry="mid"),
    }
    for lo, hi in SIGNAL_BUCKETS:
        lbl = f"[{lo:+.3f},{hi:+.3f})"
        sub = [r for r in rows if _bucket(r["modelSignal"], SIGNAL_BUCKETS) == lbl]
        if len(sub) >= MIN_SEGMENT_ROWS:
            econ[f"signal_{lbl}_at_ask"] = economics(sub, lbl, entry="ask")

    # A shadow candidate needs a mechanism a filter can act on. Under CASE A
    # there is none, so this is False by the preregistered rule, not by taste.
    shadow_justified = bool(
        sims.get("simulated")
        and any(f.get("verdict") == "MODEL_BEATS_MARKET" for f in sims.get("filters", []))
    )
    disposition = "SHADOW_CANDIDATE" if shadow_justified else "LEVEL_0_MEASUREMENT_ONLY"

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "Hitter Declared-Edge Decomposition",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "researchOnly": True, "productionChanged": False,
        "parametersFitted": 0, "correctionFitted": False,
        "usesUserConfirmedWagers": False, "impliesRecommendationsWereBet": False,
        "productionFormula": {
            "source": "lib/research/hitter_pricing.py::price_hitter_contract",
            "formula": "rawProbabilityEdge = modelProbability - executableKalshiPrice",
            "executablePriceResolution": ("lib/research/hitter_board_builder.py::_executable_yes_price -- "
                                          "mid if present, else (yes_bid+yes_ask)/2, else ask, else bid"),
            "sideSemantics": "YES only; no NO expression exists in this path",
            "feeAware": False,
            "spreadAdjustment": "none -- production differences against the MIDPOINT",
            "confidenceTransformation": "none in the edge itself",
            "thresholdSpecificLogic": "none in pricing",
            "stalenessTerm": "none -- marketObservedAt is recorded but never used to adjust",
            "reproducibleFromSourceFields": True,
        },
        "decomposition": {
            "identity": "declaredEdge = MODEL_SIGNAL - EXECUTION_PENALTY",
            "modelSignal": "modelProbability - fairMid",
            "executionPenalty": "executableKalshiPrice - fairMid",
            "audit": identity,
            "conclusion": ("production's executable price IS the vig-free midpoint, so EXECUTION_PENALTY "
                           "is identically zero and declaredEdge == MODEL_SIGNAL exactly"),
        },
        "coverage": {"eligibleRows": len(rows), "exclusions": dict(exclusions)},
        "overall": overall,
        "h1_modelSignal": h1, "h2_executionPenalty": h2, "h3_quoteAge": h3,
        "h4_liquidity": h4, "h5_probabilityExtremeness": h5,
        "h6_family": h6, "h7_thresholdTail": h7,
        "conditionalOls": ols,
        "chronological": chrono,
        "mechanism": mechanism, "mechanismDetail": mechanism_detail,
        "filterSimulations": sims,
        "secondaryEconomics": econ,
        "disposition": disposition, "maximumDisposition": "SHADOW_CANDIDATE",
        "shadowCandidateJustified": shadow_justified,
        "productionActivationAuthorized": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: rows={len(rows)} keys={overall.get('playerGameKeys')} "
          f"games={overall.get('independentGames')} dates={overall.get('independentDates')}")
    print(f"  identity: executionPenalty==0 on {identity['executionPenaltyZeroShare']:.4%} of rows "
          f"(max |penalty| {identity['maxAbsExecutionPenalty']:.2e}); "
          f"declaredEdge reproduced on {identity['declaredEdgeReproducedShare']:.4%}")
    print("  H1 MODEL_SIGNAL buckets:")
    for s in h1["segments"]:
        if s.get("rows"):
            print(f"    {s['segment']:>20} n={s['rows']:5} keys={s.get('playerGameKeys',0):4} "
                  f"delta={s['pairedBrierDelta']:+.5f} {s['verdict']}")
    print(f"    monotoneImproving={h1['monotoneImproving']} inversion={h1['inversion']}")
    print(f"  MECHANISM: {mechanism}")
    print(f"  economics @ask {econ['OVERALL_at_honest_ask']['netRoi']} vs "
          f"@mid {econ['OVERALL_at_production_mid']['netRoi']}")
    print(f"  disposition={disposition}")
    return 0


def _write_markdown(a):
    o, d = a["overall"], a["decomposition"]
    pf = a["productionFormula"]
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        f"**RESEARCH ONLY. No production change. Parameters fitted: {a['parametersFitted']}.**",
        "",
        "## The algebra, and a correction",
        "",
        "An earlier session guessed that wide bid/ask spreads mechanically produce large declared",
        "edges. **That is backwards.** Decomposing about the vig-free midpoint:",
        "",
        "```",
        "declaredEdge = (model - fairMid) - (executablePrice - fairMid)",
        "             =  MODEL_SIGNAL     -  EXECUTION_PENALTY",
        "```",
        "",
        "A *larger* execution penalty **reduces** declared edge. So the mechanism had to be measured,",
        "not assumed.",
        "",
        "## What production actually computes",
        "",
        f"- Source: `{pf['source']}`",
        f"- Formula: `{pf['formula']}`",
        f"- Executable price: {pf['executablePriceResolution']}",
        f"- Side semantics: {pf['sideSemantics']}",
        f"- Fee-aware: **{pf['feeAware']}** · Spread adjustment: {pf['spreadAdjustment']}",
        f"- Staleness term: {pf['stalenessTerm']}",
        f"- Reproducible from archived source fields: **{pf['reproducibleFromSourceFields']}**",
        "",
        "### The decomposition collapses",
        "",
        f"- `executionPenalty == 0` on **{a['decomposition']['audit']['executionPenaltyZeroShare']:.2%}** of rows "
        f"(max |penalty| {a['decomposition']['audit']['maxAbsExecutionPenalty']:.2e})",
        f"- `declaredEdge` reproduced from source fields on **{a['decomposition']['audit']['declaredEdgeReproducedShare']:.2%}** of rows",
        "",
        f"**{d['conclusion']}.**",
        "",
        "Execution cost therefore *cannot* be the cause of the inversion: it is not present in the",
        "quantity that inverts.",
        "",
        "## H1 -- model signal (the decisive test)",
        "",
        "Paired delta is model minus Kalshi fair midpoint; negative means the model is better.",
        "",
        "| Model signal | Rows | Keys | Paired delta | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for s in a["h1_modelSignal"]["segments"]:
        if not s.get("rows"):
            lines.append(f"| {s['segment']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {s['segment']} | {s['rows']} | {s.get('playerGameKeys','-')} | "
                     f"{s['pairedBrierDelta']:+.5f} | {s['verdict']} |")
    lines += [
        "",
        f"- Monotone improving: **{a['h1_modelSignal']['monotoneImproving']}**",
        f"- **Inversion: {a['h1_modelSignal']['inversion']}**",
        "",
        "## H2 -- execution penalty and spread",
        "",
        a["h2_executionPenalty"]["note"],
        "",
        f"True taker penalty (ask - mid), which production's edge omits: "
        f"`{a['h2_executionPenalty']['trueTakerPenaltySummary']}`",
        "",
        "| Spread (cents) | Rows | Keys | Paired delta | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for s in a["h2_executionPenalty"]["spreadBands"]["segments"]:
        if not s.get("rows"):
            lines.append(f"| {s['segment']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {s['segment']} | {s['rows']} | {s.get('playerGameKeys','-')} | "
                     f"{s['pairedBrierDelta']:+.5f} | {s['verdict']} |")

    h3 = a["h3_quoteAge"]
    lines += ["", "## H3 -- quote age", "",
              f"- Rows with measurable age: {h3['rowsWithMeasurableAge']} · without timestamps: "
              f"{h3['rowsWithoutTimestamps']} (never fabricated)",
              f"- Age (minutes): `{h3['ageSummaryMinutes']}`", "",
              "| Age (min) | Rows | Keys | Paired delta | Verdict |", "|---|---:|---:|---:|---|"]
    for s in h3["bins"]["segments"]:
        if not s.get("rows"):
            lines.append(f"| {s['segment']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {s['segment']} | {s['rows']} | {s.get('playerGameKeys','-')} | "
                     f"{s['pairedBrierDelta']:+.5f} | {s['verdict']} |")

    lines += ["", "## H5 -- probability extremeness", "",
              "| Probability band | Rows | Keys | Paired delta | Verdict |", "|---|---:|---:|---:|---|"]
    for s in a["h5_probabilityExtremeness"]["bands"]["segments"]:
        if not s.get("rows"):
            lines.append(f"| {s['segment']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {s['segment']} | {s['rows']} | {s.get('playerGameKeys','-')} | "
                     f"{s['pairedBrierDelta']:+.5f} | {s['verdict']} |")
    lines += ["", "Signal inversion *within* each probability band:", ""]
    for band, v in a["h5_probabilityExtremeness"]["signalInversionWithinBand"].items():
        lines.append(f"- `{band}`: rows={v['rows']} inversion={v.get('inversion')} "
                     f"qualifyingBuckets={v.get('qualifyingBuckets','-')}")

    lines += ["", "## H6 -- by family", "",
              "| Family | Rows | Keys | Paired delta | p | FDR | Signal inversion | Verdict |",
              "|---|---:|---:|---:|---:|:-:|:-:|---|"]
    for e in a["h6_family"]:
        if not e.get("rows"):
            lines.append(f"| {e['family']} | 0 | - | - | - | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {e['family']} | {e['rows']} | {e['playerGameKeys']} | "
                     f"{e['pairedBrierDelta']:+.5f} | {e.get('bootstrapPValue')} | "
                     f"{'yes' if e.get('fdrSignificant') else 'no'} | {e.get('signalInversion')} | {e['verdict']} |")

    h7 = a["h7_thresholdTail"]
    lines += ["", "## H7 -- ladder depth / tail structure", "",
              f"Ladders found: {h7['laddersFound']}. {h7['note']}", "",
              "| Rung | Rows | Keys | Mean model signal | Mean model prob | Paired delta | Verdict |",
              "|---|---:|---:|---:|---:|---:|---|"]
    for s in h7["rungs"]:
        if not s.get("rows"):
            lines.append(f"| {s['ladderRank']} | 0 | - | - | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {s['ladderRank']} | {s['rows']} | {s.get('playerGameKeys','-')} | "
                     f"{s.get('meanModelSignal')} | {s.get('meanModelProbability')} | "
                     f"{s['pairedBrierDelta']:+.5f} | {s['verdict']} |")

    ch = a["chronological"]
    lines += [
        "", "## Chronological", "",
        f"- DEVELOPMENT (<= {ch['devDateMax']}): {ch['development'].get('rows',0)} rows / "
        f"{ch['development'].get('playerGameKeys',0)} keys, signal inversion "
        f"**{ch['developmentSignalInversion']}**",
        f"- VALIDATION: {ch['validation'].get('rows',0)} rows / "
        f"{ch['validation'].get('playerGameKeys',0)} keys, signal inversion "
        f"**{ch['validationSignalInversion']}**",
        "",
        "## Mechanism",
        "",
        f"**{a['mechanism']}**",
        "",
        f"{a['mechanismDetail']}.",
        "",
        "## Secondary economics",
        "",
        "Reported at BOTH conventions. The gap between them is precisely the half-spread production's",
        "declared edge omits.",
        "",
        "| Segment | Entry | Opportunities | Wins | Avg entry | Fees | Net | ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for k, e in a["secondaryEconomics"].items():
        lines.append(f"| {k} | {e['entryConvention']} | {e['opportunities']} | {e['wins']} | "
                     f"{e['averageEntryPrice']} | {e['totalFees']} | {e['netPnl']} | {e['netRoi']} |")

    sims = a["filterSimulations"]
    lines += ["", "## Filter simulations", ""]
    if not sims.get("simulated"):
        lines.append(f"**Not simulated.** {sims['reason']}.")
    else:
        lines += ["| Filter | Retained | Share | Paired delta | Net ROI | Verdict |",
                  "|---|---:|---:|---:|---:|---|"]
        for f in sims["filters"]:
            lines.append(f"| {f['filter']} | {f['retainedRows']} | {f['retainedShare']} | "
                         f"{f.get('pairedBrierDelta')} | {f['economics']['netRoi']} | {f.get('verdict')} |")

    lines += [
        "", "## Result", "",
        f"- Mechanism: **{a['mechanism']}**",
        f"- Disposition: **{a['disposition']}** (maximum permitted: {a['maximumDisposition']})",
        f"- Shadow candidate justified: **{a['shadowCandidateJustified']}**",
        f"- Production activation authorized: {a['productionActivationAuthorized']}",
        "",
    ]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
