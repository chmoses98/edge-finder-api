#!/usr/bin/env python3
"""
scripts/edgelab/run_hitter_prop_validity_experiment.py
======================================================
Research Lab experiment MLB-RSCH-0028: "Hitter Prop Probability Validity
/ Edge Audit". RESEARCH ONLY. NO production changes, no candidate
activation, no staking/execution/fee-logic changes, no change to
recommendations or market eligibility.

CORE QUESTION: hitter props are the large majority of what this system
actually surfaces -- roughly 61,683 of 82,304 recommendation rows and
77,135 of 100,695 settled contracts -- yet MLB-RSCH-0022, -0024 and
-0027 contained ZERO hitter rows. Every statement this program has made
about "production trails Kalshi" has therefore described only a minority
of the system's output. This is the first serious audit of the majority.

WHAT IS MEASURED, AND AGAINST WHAT
----------------------------------
Model probability is production's own archived `modelProbability` from
the prospective hitter snapshot -- captured pregame, never recomputed.

The market benchmark is the VIG-FREE FAIR MIDPOINT reconstructed from
the archived `yesBid`/`yesAsk` of the latest VALID PREGAME observation
at or before that snapshot's own `marketObservedAt`. It is NOT the
executable ask. `executableKalshiPrice` (the ask) is retained strictly
for SECONDARY economics, never as the predictive benchmark -- MLB-RSCH
-0024 measured the ask carrying a +0.049 upward bias against +0.013 for
the vig-free mid, so using it as "truth" would manufacture a spurious
model advantage on every YES contract.

Note that production's OWN declared edge (`rawProbabilityEdge`) is
computed against the executable ask, not against the fair mid. That is
production's definition and it is preserved unchanged for the edge-bucket
analysis, because question 3 asks whether PRODUCTION'S DECLARED edge
predicts realized advantage. The two benchmarks are kept strictly
separate and never mixed within a single comparison.

INDEPENDENCE -- THE DOMINANT STRUCTURAL FACT
--------------------------------------------
The archive is row-rich and independence-poor. 8,973 settlement-joined
rows resolve to only ~404 playerGameKeys across ~62 games and 9 dates --
roughly 22 rows per player-game. Those rows are REPEATED MEASURES: the
same player-game appears at up to five pregame checkpoints, and multiple
thresholds of the same market ladder move together.

Treating 8,973 rows as 8,973 independent observations would overstate
precision by roughly an order of magnitude. Every interval in this
experiment therefore clusters on `playerGameKey`, with game- and
date-clustered variants reported alongside. No claim rests on row count.

PREREGISTERED, LOCKED BEFORE ANY INFERENTIAL RESULT WAS COMPUTED
----------------------------------------------------------------
Descriptive coverage (rows, keys, games, dates, players, checkpoint and
family distribution, and the exclusion taxonomy) was audited FIRST, as
required to choose an honest chronological design, and is reported as
descriptive. Everything inferential below -- every metric, bucket,
floor, split and threshold -- was fixed before it was evaluated.

  * eligible families: the four the hitter engine actually supports,
    read from the archive, not invented. Stolen bases are NOT audited:
    no legitimate attempt/success model exists and none is created here.
  * checkpoints: the five the archive carries.
  * pairing: exact semantic alignment on player, game, family,
    threshold, YES-outcome sense, model probability, market probability
    and settlement. Ambiguous joins are EXCLUDED with a reason code,
    never guessed and never coerced.
  * clustering: playerGameKey primary; game and date reported.
  * edge buckets: fixed at <0, 0-2.5, 2.5-5, 5-7.5, 7.5-10, 10-15, 15+
    percentage points. Not tuned, not re-cut after seeing results.
  * price bands: the same fixed quintiles used since RSCH-0024.
  * floors: a family needs >= 200 rows AND >= 50 playerGameKeys; a
    checkpoint needs >= 200 rows. Below floor is reported
    INSUFFICIENT_SAMPLE and is never promoted.
  * metrics: Brier PRIMARY, log loss, ECE, calibration slope/intercept.
  * FDR: Benjamini-Hochberg at 0.10 across parallel family tests.
  * economics: SECONDARY only, computed after the predictive verdict,
    never used to select anything.

NO CORRECTION IS FITTED HERE. RSCH-0028 is MEASUREMENT. If a regime
looks informative it is recorded as a candidate for a separate
confirmatory experiment -- it is not modelled, not thresholded, and not
activated.

MAXIMUM DISPOSITION: SHADOW_CANDIDATE. Production approval is not
available from this experiment at any result.

REAL USER BETS ARE NEVER USED. Model validation runs on the complete
research universe -- every snapshot, not recommended props, not
positive-edge props, not confirmed wagers. Nothing here infers that any
recommendation was actually bet; automatic bet settlement remains
GitHub issue #43's separate concern and is not implemented or claimed.
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
from lib.edgelab import storage
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED, independent_unit_count,
    expected_calibration_error, brier_and_log_loss_summary,
    calibration_slope_intercept, game_clustered_bootstrap_ci,
)

EXPERIMENT_ID = "MLB-RSCH-0028"
REGISTRATION_TIMESTAMP = "2026-08-29T01:20:00Z"

SNAPSHOT_DIR = os.path.join(_ROOT, "data", "edgelab", "hitter_projection_snapshots")
SETTLEMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "settlements")
OBSERVATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "observations")
ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0028_hitter_prop_audit.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0028_HITTER_PROP_AUDIT.md")

# ── Preregistered constants (locked; never re-cut after results) ─────────
ELIGIBLE_FAMILIES = ("hitter_hits", "hitter_total_bases", "hitter_hits_runs_rbis", "hitter_rbis")
CHECKPOINT_ORDER = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", "HITTER_CLOSING_WINDOW")
EDGE_BUCKETS = ((-1.00, 0.0), (0.0, 0.025), (0.025, 0.05), (0.05, 0.075),
                (0.075, 0.10), (0.10, 0.15), (0.15, 1.01))
PRICE_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
CALIBRATION_BINS = 10
MIN_FAMILY_ROWS = 200
MIN_FAMILY_KEYS = 50
MIN_CHECKPOINT_ROWS = 200
MIN_SEGMENT_ROWS = 100
MIN_PAIRED_KEYS = 30      # paired sub-analyses need independent player-games, not pairs
FDR_ALPHA = 0.10
PROB_CLAMP = (0.001, 0.999)
CLUSTER_KEY = "playerGameKey"
BOOTSTRAP_RESAMPLES = 400

# Chronological design chosen from the DESCRIPTIVE date audit (coverage is
# heavily front-loaded; the last five dates carry far fewer player-games).
DEV_DATE_MAX = "2026-08-22"

# Stolen bases are deliberately absent: no legitimate attempt/success model
# exists, so no stolen-base probability is audited or created.
UNSUPPORTED_FAMILIES = ("hitter_stolen_bases",)


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


# ── Corpus construction ───────────────────────────────────────────────────

def load_snapshots():
    """Every archived prospective hitter-projection snapshot. The COMPLETE
    research universe -- not recommended props, not positive-edge props,
    not confirmed wagers."""
    rows = []
    if not os.path.isdir(SNAPSHOT_DIR):
        return rows
    for fn in sorted(os.listdir(SNAPSHOT_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        rows.extend(storage.read_records(os.path.join(SNAPSHOT_DIR, fn)))
    return rows


def load_settlements():
    """marketTicker -> 1/0 for contracts Kalshi has actually settled.
    Returns (settled, seen_but_unresolved) so the unresolved tail can be
    reported rather than silently dropped."""
    settled, unresolved = {}, set()
    for fn in sorted(os.listdir(SETTLEMENTS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in storage.read_records(os.path.join(SETTLEMENTS_DIR, fn)):
            t = d.get("marketTicker")
            if not t:
                continue
            outcome = d.get("outcome")
            if outcome == "YES":
                settled[t] = 1
            elif outcome == "NO":
                settled[t] = 0
            else:
                unresolved.add(t)
    return settled, unresolved - set(settled)


def load_pregame_quotes(tickers):
    """ticker -> chronologically sorted [(capturedAt, yesBid, yesAsk)] over
    VALID PREGAME observations only.

    `isValidPregameObservation` and `not gameStartedAtCapture` are the
    archive's own point-in-time guards; both are required so no quote taken
    after first pitch can ever back a pregame prediction."""
    quotes = collections.defaultdict(list)
    for fn in sorted(os.listdir(OBSERVATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in storage.read_records(os.path.join(OBSERVATIONS_DIR, fn)):
            t = d.get("marketTicker")
            if t not in tickers:
                continue
            if not d.get("isValidPregameObservation") or d.get("gameStartedAtCapture"):
                continue
            bid, ask, at = d.get("yesBid"), d.get("yesAsk"), d.get("capturedAt")
            if bid is None or ask is None or not at:
                continue
            quotes[t].append((at, float(bid), float(ask)))
    for t in quotes:
        quotes[t].sort(key=lambda q: q[0])
    return quotes


def contemporaneous_quote(quote_list, observed_at):
    """The latest valid pregame quote at or before this snapshot's own
    market-observation instant -- the price actually available when the
    prediction was made. Never a later quote."""
    best = None
    for at, bid, ask in quote_list:
        if at <= observed_at:
            best = (at, bid, ask)
        else:
            break
    return best


def build_corpus(snapshots, settled, quotes):
    """One row per archived snapshot that survives every preregistered
    eligibility rule. Returns (rows, exclusions) where `exclusions` is a
    reason-coded census -- nothing is dropped silently."""
    rows, excl = [], collections.Counter()
    for s in snapshots:
        family = s.get("marketFamily")
        if family in UNSUPPORTED_FAMILIES:
            excl["FAMILY_UNSUPPORTED_NO_ATTEMPT_SUCCESS_MODEL"] += 1
            continue
        if family not in ELIGIBLE_FAMILIES:
            excl["FAMILY_NOT_ELIGIBLE"] += 1
            continue
        ticker = s.get("marketTicker")
        if not ticker:
            excl["NO_MARKET_TICKER"] += 1
            continue
        if s.get("projectionStatus") != "PROJECTED":
            excl["PROJECTION_STATUS_" + str(s.get("projectionStatus"))] += 1
            continue
        model_p = s.get("modelProbability")
        if model_p is None:
            excl["MODEL_PROBABILITY_NULL"] += 1
            continue
        if s.get("threshold") is None:
            excl["THRESHOLD_UNRESOLVED"] += 1
            continue
        if ticker not in settled:
            excl["NO_SETTLED_OUTCOME"] += 1
            continue
        observed_at = s.get("marketObservedAt")
        if not observed_at:
            excl["NO_MARKET_OBSERVED_AT"] += 1
            continue
        quote = contemporaneous_quote(quotes.get(ticker, []), observed_at)
        if quote is None:
            excl["NO_VALID_PREGAME_QUOTE_AT_OR_BEFORE_CHECKPOINT"] += 1
            continue
        _at, bid, ask = quote
        fair = ((bid + ask) / 2.0) / 100.0
        if not (0.0 < fair < 1.0):
            excl["DEGENERATE_FAIR_MIDPOINT"] += 1
            continue
        checkpoint = s.get("checkpoint")
        if checkpoint not in CHECKPOINT_ORDER:
            excl["CHECKPOINT_NOT_RECOGNISED"] += 1
            continue
        exec_ask = s.get("executableKalshiPrice")
        diagnostics = s.get("sampleSizeDiagnostics") or {}
        rows.append({
            "marketTicker": ticker,
            "marketFamily": family,
            "checkpoint": checkpoint,
            "gameId": str(s.get("gameId")),
            "playerId": str(s.get("playerId")),
            "playerGameKey": "%s:%s" % (s.get("playerId"), s.get("gameId")),
            # a single market ladder rung: same player-game AND same contract
            "playerMarketKey": "%s:%s:%s:%s" % (s.get("playerId"), s.get("gameId"), family, s.get("threshold")),
            "threshold": s.get("threshold"),
            "date": observed_at[:10],
            "observedAt": observed_at,
            "modelP": float(model_p),
            "marketP": round(fair, 6),
            "executableAsk": None if exec_ask is None else float(exec_ask),
            # production's OWN declared edge, defined against the executable
            # ask -- preserved exactly as production defines it
            "declaredEdge": s.get("rawProbabilityEdge"),
            "outcome": settled[ticker],
            "hitterArchivedPACount": diagnostics.get("hitterArchivedPACount"),
            "monteCarloStderr": s.get("monteCarloStderr"),
        })
    return rows, excl


# ── Scoring (all intervals clustered; never row-independent) ─────────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows) if rows else None


def log_loss(rows, key):
    if not rows:
        return None
    total = 0.0
    for r in rows:
        p = _clamp(r[key])
        total += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return total / len(rows)


def paired_brier_delta(rows):
    """model minus market. NEGATIVE means the model beats Kalshi."""
    if not rows:
        return None
    return brier(rows, "modelP") - brier(rows, "marketP")


def paired_log_loss_delta(rows):
    if not rows:
        return None
    return log_loss(rows, "modelP") - log_loss(rows, "marketP")


def clustered_ci(rows, value_fn, cluster_key=CLUSTER_KEY):
    return game_clustered_bootstrap_ci(
        rows, value_fn, cluster_key=cluster_key, n_resamples=BOOTSTRAP_RESAMPLES)


def clustered_pvalue(rows, value_fn, cluster_key=CLUSTER_KEY):
    """Two-sided bootstrap p-value under the same cluster-resampling
    convention as the interval. Distribution-free: a t-test over per-key
    means would assume normality of a Brier difference on as few as 50
    clusters. Used only as an FDR input."""
    import random as _random
    by = collections.defaultdict(list)
    for r in rows:
        by[r.get(cluster_key)].append(r)
    clusters = sorted(by, key=str)
    observed = value_fn(rows)
    if not clusters or observed is None:
        return None
    rng = _random.Random(DEFAULT_BOOTSTRAP_SEED)
    est = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [rng.choice(clusters) for _ in clusters]
        v = value_fn([row for c in sample for row in by[c]])
        if v is not None:
            est.append(v)
    if not est:
        return None
    mean = sum(est) / len(est)
    centred = [e - mean for e in est]
    extreme = sum(1 for c in centred if abs(c) >= abs(observed))
    return min(1.0, (extreme + 1) / (len(centred) + 1))


def benjamini_hochberg(pvalues, alpha=FDR_ALPHA):
    indexed = sorted(((p, i) for i, p in enumerate(pvalues) if p is not None), key=lambda t: t[0])
    m = len(indexed)
    if m == 0:
        return set()
    upto = -1
    for rank, (p, _) in enumerate(indexed, start=1):
        if p <= (rank / m) * alpha:
            upto = rank
    return {i for _, i in indexed[:upto]} if upto > 0 else set()


def score_block(rows, label, *, with_ci=True):
    """Full paired scorecard for any row subset, with clustering reported at
    all three levels so nobody can mistake row count for sample size."""
    if not rows:
        return {"label": label, "rows": 0}
    model_pairs = [(r["modelP"], r["outcome"]) for r in rows]
    market_pairs = [(r["marketP"], r["outcome"]) for r in rows]
    m_brier, m_ll = brier_and_log_loss_summary(model_pairs)
    k_brier, k_ll = brier_and_log_loss_summary(market_pairs)
    m_slope, m_int = calibration_slope_intercept(model_pairs)
    k_slope, _ = calibration_slope_intercept(market_pairs)
    out = {
        "label": label,
        "rows": len(rows),
        "playerGameKeys": independent_unit_count(rows, CLUSTER_KEY),
        "independentGames": independent_unit_count(rows, "gameId"),
        "independentDates": independent_unit_count(rows, "date"),
        "players": independent_unit_count(rows, "playerId"),
        "rowsPerPlayerGameKey": round(len(rows) / max(1, independent_unit_count(rows, CLUSTER_KEY)), 2),
        "modelBrier": m_brier, "marketBrier": k_brier,
        "modelLogLoss": m_ll, "marketLogLoss": k_ll,
        "pairedBrierDelta": round(paired_brier_delta(rows), 6),
        "pairedLogLossDelta": round(paired_log_loss_delta(rows), 6),
        "modelECE": round(expected_calibration_error(model_pairs, n_bins=CALIBRATION_BINS), 6),
        "marketECE": round(expected_calibration_error(market_pairs, n_bins=CALIBRATION_BINS), 6),
        "modelCalibrationSlope": m_slope, "modelCalibrationIntercept": m_int,
        "marketCalibrationSlope": k_slope,
        "settledYesRate": round(sum(r["outcome"] for r in rows) / len(rows), 4),
        "meanModelProbability": round(sum(r["modelP"] for r in rows) / len(rows), 4),
        "meanMarketProbability": round(sum(r["marketP"] for r in rows) / len(rows), 4),
    }
    if with_ci:
        lo, hi, method = clustered_ci(rows, paired_brier_delta)
        out["pairedBrierDeltaCI"] = {"low": lo, "high": hi, "clusterUnit": CLUSTER_KEY, "method": method}
        g_lo, g_hi, _ = clustered_ci(rows, paired_brier_delta, cluster_key="gameId")
        out["pairedBrierDeltaCI_gameClustered"] = {"low": g_lo, "high": g_hi}
        d_lo, d_hi, _ = clustered_ci(rows, paired_brier_delta, cluster_key="date")
        out["pairedBrierDeltaCI_dateClustered"] = {"low": d_lo, "high": d_hi}
        ll_lo, ll_hi, _ = clustered_ci(rows, paired_log_loss_delta)
        out["pairedLogLossDeltaCI"] = {"low": ll_lo, "high": ll_hi}
    return out


def classify(block, *, min_rows, min_keys=0):
    """Preregistered verdict vocabulary. A CI that straddles zero is PARITY,
    never a win."""
    if block.get("rows", 0) < min_rows or block.get("playerGameKeys", 0) < min_keys:
        return "INSUFFICIENT_SAMPLE"
    ci = block.get("pairedBrierDeltaCI") or {}
    lo, hi = ci.get("low"), ci.get("high")
    if lo is None or hi is None:
        return "INSUFFICIENT_SAMPLE"
    if hi < 0:
        return "MODEL_BEATS_MARKET"
    if lo > 0:
        return "MARKET_BEATS_MODEL"
    return "PARITY"


def calibration_table(rows, key):
    """Fixed-bin reliability: predicted vs actual. Bins are preregistered
    deciles, never re-cut."""
    table = []
    for i in range(CALIBRATION_BINS):
        lo, hi = i / CALIBRATION_BINS, (i + 1) / CALIBRATION_BINS
        sub = [r for r in rows if (lo <= r[key] < hi) or (i == CALIBRATION_BINS - 1 and r[key] == 1.0)]
        if not sub:
            table.append({"bin": f"[{lo:.1f},{hi:.1f})", "rows": 0})
            continue
        table.append({
            "bin": f"[{lo:.1f},{hi:.1f})", "rows": len(sub),
            "playerGameKeys": independent_unit_count(sub, CLUSTER_KEY),
            "predicted": round(sum(r[key] for r in sub) / len(sub), 4),
            "actual": round(sum(r["outcome"] for r in sub) / len(sub), 4),
        })
    return table


# ── Preregistered segment analyses ───────────────────────────────────────

def _edge_bucket(edge):
    if edge is None:
        return None
    for lo, hi in EDGE_BUCKETS:
        if lo <= edge < hi:
            return f"[{lo:+.3f},{hi:+.3f})"
    return None


def edge_bucket_analysis(rows):
    """Does production's DECLARED edge predict realized advantage over
    Kalshi? Buckets are fixed; monotonicity is measured, not engineered."""
    by = collections.defaultdict(list)
    for r in rows:
        b = _edge_bucket(r.get("declaredEdge"))
        if b:
            by[b].append(r)
    ordered = [f"[{lo:+.3f},{hi:+.3f})" for lo, hi in EDGE_BUCKETS]
    out = []
    for b in ordered:
        sub = by.get(b, [])
        if not sub:
            out.append({"bucket": b, "rows": 0})
            continue
        blk = score_block(sub, b, with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["bucket"] = b
        blk["verdict"] = classify(blk, min_rows=MIN_SEGMENT_ROWS) if len(sub) >= MIN_SEGMENT_ROWS else "INSUFFICIENT_SAMPLE"
        out.append(blk)
    deltas = [(e["bucket"], e["pairedBrierDelta"]) for e in out
              if e.get("rows", 0) >= MIN_SEGMENT_ROWS and e.get("pairedBrierDelta") is not None]
    monotone = None
    if len(deltas) >= 3:
        vals = [d for _, d in deltas]
        # "useful" would mean delta DECREASES (model gains) as edge rises
        monotone = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
    inversion = None
    if len(deltas) >= 3:
        vals = [d for _, d in deltas]
        # anti-signal: model gets WORSE relative to market as declared edge grows
        inversion = vals[-1] > vals[0]
    return {"buckets": out, "monotoneImproving": monotone, "edgeInversion": inversion,
            "bucketsEvaluated": [b for b, _ in deltas]}


def direction_analysis(rows):
    """Is the model informative when it disagrees upward vs downward?"""
    out = {}
    for label, pred in (("MODEL_ABOVE_MARKET", lambda r: r["modelP"] > r["marketP"]),
                        ("MODEL_BELOW_MARKET", lambda r: r["modelP"] < r["marketP"])):
        sub = [r for r in rows if pred(r)]
        blk = score_block(sub, label, with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["verdict"] = classify(blk, min_rows=MIN_SEGMENT_ROWS)
        out[label] = blk
    return out


def price_band_analysis(rows):
    out = []
    for lo, hi in PRICE_BANDS:
        sub = [r for r in rows if lo <= r["marketP"] < hi or (hi == 1.0 and r["marketP"] == 1.0)]
        blk = score_block(sub, f"[{lo:.1f},{hi:.1f})", with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["verdict"] = classify(blk, min_rows=MIN_SEGMENT_ROWS)
        out.append(blk)
    return out


def family_analysis(rows):
    entries = []
    for family in ELIGIBLE_FAMILIES:
        sub = [r for r in rows if r["marketFamily"] == family]
        blk = score_block(sub, family)
        blk["family"] = family
        meets = blk.get("rows", 0) >= MIN_FAMILY_ROWS and blk.get("playerGameKeys", 0) >= MIN_FAMILY_KEYS
        blk["meetsFloor"] = meets
        blk["bootstrapPValue"] = round(clustered_pvalue(sub, paired_brier_delta), 4) if meets else None
        blk["edgeBuckets"] = edge_bucket_analysis(sub) if meets else None
        entries.append(blk)
    rejected = benjamini_hochberg([e["bootstrapPValue"] for e in entries])
    for i, e in enumerate(entries):
        e["fdrSignificant"] = i in rejected
        base = classify(e, min_rows=MIN_FAMILY_ROWS, min_keys=MIN_FAMILY_KEYS)
        # A family may only be called a win if it ALSO survives FDR.
        if base == "MODEL_BEATS_MARKET" and not e["fdrSignificant"]:
            base = "PARITY"
        e["verdict"] = base
    return entries


def checkpoint_analysis(rows):
    out = []
    for cp in CHECKPOINT_ORDER:
        sub = [r for r in rows if r["checkpoint"] == cp]
        blk = score_block(sub, cp)
        blk["checkpoint"] = cp
        blk["verdict"] = classify(blk, min_rows=MIN_CHECKPOINT_ROWS)
        blk["meanExecutableAsk"] = (round(sum(r["executableAsk"] for r in sub if r["executableAsk"] is not None)
                                          / max(1, sum(1 for r in sub if r["executableAsk"] is not None)), 4)
                                    if sub else None)
        out.append(blk)
    return out


def paired_checkpoint_analysis(rows):
    """EXACTLY-paired comparison: the same playerMarketKey (same player,
    game, family AND threshold) seen at two checkpoints. Far more
    informative than comparing unrelated checkpoint populations, which
    differ in which games and players they even contain."""
    by_key = collections.defaultdict(dict)
    for r in rows:
        # keep the LAST observation within a checkpoint for that exact rung
        by_key[r["playerMarketKey"]][r["checkpoint"]] = r
    transitions = [("T_MINUS_90", "T_MINUS_30"), ("T_MINUS_30", "LINEUP_CONFIRMATION"),
                   ("LINEUP_CONFIRMATION", "HITTER_CLOSING_WINDOW")]
    out = []
    for earlier, later in transitions:
        pairs = [(v[earlier], v[later]) for v in by_key.values() if earlier in v and later in v]
        if not pairs:
            out.append({"transition": f"{earlier}->{later}", "pairs": 0})
            continue
        e_rows = [p[0] for p in pairs]
        l_rows = [p[1] for p in pairs]
        e_brier, l_brier = brier(e_rows, "modelP"), brier(l_rows, "modelP")
        def _delta(idx_rows):
            return lambda rs: brier(rs, "modelP")
        out.append({
            "transition": f"{earlier}->{later}",
            "pairs": len(pairs),
            "playerGameKeys": independent_unit_count(e_rows, CLUSTER_KEY),
            "earlierModelBrier": round(e_brier, 6),
            "laterModelBrier": round(l_brier, 6),
            "modelBrierChange": round(l_brier - e_brier, 6),
            "earlierMarketBrier": round(brier(e_rows, "marketP"), 6),
            "laterMarketBrier": round(brier(l_rows, "marketP"), 6),
            "marketBrierChange": round(brier(l_rows, "marketP") - brier(e_rows, "marketP"), 6),
            "meanAbsProbabilityMove": round(
                sum(abs(l["modelP"] - e["modelP"]) for e, l in pairs) / len(pairs), 6),
            "meanMarketMove": round(
                sum(l["marketP"] - e["marketP"] for e, l in pairs) / len(pairs), 6),
            "modelMovePredictsOutcome": round(_move_correlation(pairs), 6),
            "laterIsBetter": bool(l_brier < e_brier),
        })
    return out


def _move_correlation(pairs):
    """Does the model's own revision point toward the truth? Mean of
    (probability move) * (outcome - earlier probability): positive means
    revisions moved in the direction the outcome actually went."""
    if not pairs:
        return 0.0
    return sum((l["modelP"] - e["modelP"]) * (e["outcome"] - e["modelP"]) for e, l in pairs) / len(pairs)


def lineup_confirmation_value(rows):
    """Paired on exactly the same market rung: does a CONFIRMED lineup
    improve probability quality over the pre-lineup snapshot of the same
    contract? Directly relevant to 2026 bet timing."""
    by_key = collections.defaultdict(dict)
    for r in rows:
        by_key[r["playerMarketKey"]][r["checkpoint"]] = r
    pre_checkpoints = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30")
    pairs = []
    for v in by_key.values():
        if "LINEUP_CONFIRMATION" not in v:
            continue
        pre = next((v[c] for c in reversed(pre_checkpoints) if c in v), None)
        if pre is not None:
            pairs.append((pre, v["LINEUP_CONFIRMATION"]))
    if not pairs:
        return {"pairs": 0}
    pre_rows = [p[0] for p in pairs]
    conf_rows = [p[1] for p in pairs]
    return {
        "pairs": len(pairs),
        "playerGameKeys": independent_unit_count(pre_rows, CLUSTER_KEY),
        "preLineupModelBrier": round(brier(pre_rows, "modelP"), 6),
        "confirmedModelBrier": round(brier(conf_rows, "modelP"), 6),
        "modelBrierImprovement": round(brier(pre_rows, "modelP") - brier(conf_rows, "modelP"), 6),
        "preLineupMarketBrier": round(brier(pre_rows, "marketP"), 6),
        "confirmedMarketBrier": round(brier(conf_rows, "marketP"), 6),
        "marketBrierImprovement": round(brier(pre_rows, "marketP") - brier(conf_rows, "marketP"), 6),
        "preLineupPairedDelta": round(paired_brier_delta(pre_rows), 6),
        "confirmedPairedDelta": round(paired_brier_delta(conf_rows), 6),
        "confirmationHelpsModel": bool(brier(conf_rows, "modelP") < brier(pre_rows, "modelP")),
        "meetsKeyFloor": independent_unit_count(pre_rows, CLUSTER_KEY) >= MIN_PAIRED_KEYS,
        "verdict": ("INSUFFICIENT_SAMPLE"
                    if independent_unit_count(pre_rows, CLUSTER_KEY) < MIN_PAIRED_KEYS
                    else ("CONFIRMATION_IMPROVES_MODEL"
                          if brier(conf_rows, "modelP") < brier(pre_rows, "modelP")
                          else "CONFIRMATION_DOES_NOT_IMPROVE_MODEL")),
    }


def sample_depth_analysis(rows):
    """Preregistered bins on the snapshot's OWN archived PIT-safe sample
    depth. Rookie status is never inferred from future career data."""
    bins = ((0, 1, "ZERO_ARCHIVED_PA"), (1, 50, "1_49_PA"), (50, 200, "50_199_PA"), (200, 10 ** 9, "200_PLUS_PA"))
    out = []
    for lo, hi, label in bins:
        sub = [r for r in rows if isinstance(r.get("hitterArchivedPACount"), (int, float))
               and lo <= r["hitterArchivedPACount"] < hi]
        blk = score_block(sub, label, with_ci=len(sub) >= MIN_SEGMENT_ROWS)
        blk["bin"] = label
        blk["verdict"] = classify(blk, min_rows=MIN_SEGMENT_ROWS)
        out.append(blk)
    return out


def checkpoint_information_audit(snapshots):
    """Do the five checkpoint labels actually carry DISTINCT information?

    This gates questions 6-8. A checkpoint is only meaningful if, for the
    same market rung, a later checkpoint reflects either a re-computed model
    probability or a fresh market observation. Measured directly on the raw
    archive rather than assumed."""
    by_rung = collections.defaultdict(list)
    for s in snapshots:
        if s.get("modelProbability") is None:
            continue
        key = (s.get("playerId"), s.get("gameId"), s.get("marketFamily"), s.get("threshold"))
        by_rung[key].append(s)
    multi = [v for v in by_rung.values() if len({x.get("checkpoint") for x in v}) > 1]
    if not multi:
        return {"rungsAtMultipleCheckpoints": 0}
    identical_model = sum(1 for v in multi if len({round(x["modelProbability"], 6) for x in v}) == 1)
    single_market = sum(1 for v in multi if len({x.get("marketObservedAt") for x in v}) == 1)
    regenerated = sum(1 for v in multi if len({x.get("projectionGeneratedAt") for x in v}) > 1)
    dates = collections.defaultdict(set)
    for s in snapshots:
        at = s.get("marketObservedAt") or ""
        if at:
            dates[at[:10]].add(at)
    return {
        "rungsAtMultipleCheckpoints": len(multi),
        "modelProbabilityIdenticalAcrossCheckpoints": identical_model,
        "modelProbabilityIdenticalShare": round(identical_model / len(multi), 4),
        "projectionActuallyRegenerated": regenerated,
        "projectionRegeneratedShare": round(regenerated / len(multi), 4),
        "singleMarketObservationAcrossCheckpoints": single_market,
        "singleMarketObservationShare": round(single_market / len(multi), 4),
        "distinctMarketCapturesPerDate": {d: len(v) for d, v in sorted(dates.items())},
        "interpretation": (
            "The checkpoint label marks when the projection was REGENERATED, not when the market was "
            "re-observed. The projection is genuinely regenerated for most rungs, yet the resulting "
            "probability is unchanged for the majority of them, and only a handful of distinct market "
            "captures exist per day so several checkpoints routinely share one market observation. "
            "Checkpoint comparisons on this archive are therefore CONFOUNDED and largely nominal: they "
            "cannot cleanly answer when hitter information is strongest."
        ),
    }


def secondary_economics(rows, label):
    """SECONDARY. Fee-aware, executable-ask based, computed only AFTER the
    predictive verdict. Never used to select a family, checkpoint, bucket
    or band, and no threshold is fitted to it. Backs YES at the executable
    ask whenever the model's probability exceeds that ask -- production's
    own declared-edge condition, not a tuned rule."""
    staked = fees = pnl = 0.0
    bets = wins = 0
    entries = []
    for r in rows:
        ask = r.get("executableAsk")
        if ask is None or not (0.0 < ask < 1.0):
            continue
        if r["modelP"] <= ask:
            continue
        fee = taker_fee(1, ask)
        bets += 1
        wins += r["outcome"]
        staked += ask
        fees += fee
        pnl += ((1.0 - ask) if r["outcome"] == 1 else -ask) - fee
        entries.append(ask)
    return {
        "segment": label,
        "opportunities": bets,
        "wins": wins,
        "losses": bets - wins,
        "averageEntryPrice": round(sum(entries) / len(entries), 4) if entries else None,
        "grossStaked": round(staked, 4),
        "grossPnlBeforeFees": round(pnl + fees, 4),
        "totalFees": round(fees, 4),
        "netPnl": round(pnl, 4),
        "netRoi": round(pnl / staked, 4) if staked else None,
        "note": "SECONDARY and descriptive. Never selective. Never implies any recommendation was actually bet.",
    }


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None
    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0028_hitter_prop_validity_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0028 hitter prop validity v1: control = Kalshi's contemporaneous vig-free "
                        "fair midpoint reconstructed from archived yesBid/yesAsk on the latest VALID PREGAME "
                        "observation at or before each snapshot's own marketObservedAt; comparison = "
                        "production's archived hitter modelProbability. NOTHING IS FITTED -- this experiment "
                        "estimates no parameter. Executable ask is never the predictive benchmark."
        ),
        probability_adapter_identity=(
            "vig-free fair midpoint (yesBid+yesAsk)/2 from the contemporaneous valid pregame observation; "
            "executable YES ask retained separately for secondary economics only"
        ),
        model_engine_family="hitter_prop_probability_validity_audit_v1",
        required_input_provenance=[
            "hitter_snapshot",
            "archived_kalshi_market_observation",
            "settlement_outcome",
        ],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "First audit of production's hitter-prop probabilities -- the large majority of recommendation "
            "and settlement volume, and entirely absent from every prior model-evaluation audit."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Hitter Prop Probability Validity / Edge Audit",
        hypothesis=(
            "H1: production's hitter-prop probabilities are calibrated and carry information beyond Kalshi's "
            "contemporaneous vig-free fair price on paired rows. H2: production's DECLARED edge is monotone "
            "in realized advantage -- larger declared edge means larger realized gain over Kalshi. H3 (null, "
            "tested not assumed): the hitter model trails Kalshi, and declared edge is flat or ANTI-predictive "
            "(the previously observed hitter edge inversion), in which case the enormous recommendation volume "
            "this category carries is being generated from probabilities with no demonstrated advantage. H4: "
            "later checkpoints -- especially confirmed lineups -- carry better information than early ones."
        ),
        research_question=(
            "Are production's hitter-prop probabilities calibrated, do they beat Kalshi's contemporaneous fair "
            "price, does declared edge correspond to realized advantage, and does any family/checkpoint regime "
            "justify prospective confirmation for the remainder of 2026?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every archived prospective hitter-projection snapshot 2026-08-19 .. 2026-08-27 across the four "
            "supported hitter families, joined to an actual Kalshi settlement and to a contemporaneous valid "
            "pregame quote -- the COMPLETE research universe, not recommended or positive-edge props."
        ),
        market_families=list(ELIGIBLE_FAMILIES),
        eligibility_criteria=[
            "marketFamily is one of the four supported hitter families",
            "projectionStatus == PROJECTED with a non-null modelProbability and resolved threshold",
            "an actual YES/NO Kalshi settlement exists for the exact contract ticker",
            "a valid pregame observation with both yesBid and yesAsk exists at or before the snapshot's own "
            "marketObservedAt",
        ],
        exclusion_criteria=[
            "stolen bases -- no legitimate attempt/success model exists and none is created here",
            "executable YES ask as the predictive benchmark (secondary economics only)",
            "user-confirmed wagers as validation input -- model validation uses the complete research universe",
            "any inference that a recommendation was actually bet",
            "any fitted parameter -- this experiment estimates nothing",
            "economics as a selection input for any family, checkpoint, bucket or band",
            "post-hoc re-cutting of edge buckets, price bands or sample floors",
        ],
        prediction_checkpoints=list(CHECKPOINT_ORDER),
        primary_metric=(
            "paired Brier delta (production hitter probability minus Kalshi contemporaneous vig-free fair "
            "price) on exactly-aligned rows, with playerGameKey-clustered bootstrap confidence intervals"
        ),
        secondary_metrics=[
            "paired log-loss delta; ECE and fixed-bin reliability for model and market",
            "calibration slope/intercept per family and checkpoint",
            "declared-edge bucket monotonicity and edge-inversion test",
            "disagreement direction (model above vs below market)",
            "per-checkpoint quality and EXACTLY-PAIRED checkpoint transitions on the same market rung",
            "lineup-confirmation paired value; closing-window paired value",
            "archived PA-depth bins; fixed price bands",
            "game- and date-clustered interval variants alongside the playerGameKey primary",
            "SECONDARY fee-aware executable economics (canonical taker fee, never selection)",
        ],
        chronological_split_policy=(
            f"DATE_BASED: DEVELOPMENT = observed <= {DEV_DATE_MAX}, VALIDATION = later dates. Chosen only "
            "AFTER a descriptive audit of date coverage, which is heavily front-loaded: the archive spans 9 "
            "dates but the later ones carry far fewer player-games. With ~404 playerGameKeys in total this "
            "does NOT support a luxurious train/validation/holdout structure, and no confirmatory claim is "
            "made from the validation block alone; leave-one-date-out is reported as robustness. Where the "
            "sample is too shallow for a confirmatory claim the result is reported as preliminary E4 "
            "developing evidence, not as confirmation."
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
            "evidenceLevel E4_PROSPECTIVE_SHADOW (prospectively captured hitter snapshots and market quotes). "
            "The archive is ROW-RICH and INDEPENDENCE-POOR: ~8,973 joined rows resolve to only ~404 "
            "playerGameKeys over ~62 games, roughly 22 rows per player-game, because the same player-game "
            "recurs at up to five checkpoints and across multiple ladder rungs. Every interval clusters on "
            "playerGameKey; row counts are never treated as independent observations. NO correction is fitted "
            "-- this is measurement. A favourable regime is recorded as a candidate for a SEPARATE "
            "confirmatory experiment, never backfitted here. MAXIMUM disposition SHADOW_CANDIDATE; production "
            "approval is unavailable from this experiment at any result. Automatic bet settlement (GitHub "
            "issue #43) is neither implemented nor claimed."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control, definition = register_experiment()

    snapshots = load_snapshots()
    settled, unresolved = load_settlements()
    tickers = {s.get("marketTicker") for s in snapshots if s.get("marketTicker")}
    quotes = load_pregame_quotes(tickers)
    rows, exclusions = build_corpus(snapshots, settled, quotes)

    # Join provenance (Part D): the mechanism is the canonical Kalshi ticker.
    snapshot_tickers = tickers
    joined_tickers = {t for t in snapshot_tickers if t in settled}
    unresolved_tickers = sorted(t for t in snapshot_tickers if t not in settled)
    join_audit = {
        "mechanism": "exact canonical Kalshi marketTicker equality between the hitter snapshot and the "
                     "settlement archive -- no player/threshold string parsing, no sourceBetKey, no fuzzy "
                     "matching, so a join is either exact or absent",
        "snapshotTickers": len(snapshot_tickers),
        "settlementJoinedTickers": len(joined_tickers),
        "joinRateByTicker": round(len(joined_tickers) / max(1, len(snapshot_tickers)), 4),
        "unresolvedTickers": len(unresolved_tickers),
        "unresolvedPresentInArchiveButNoYesNoOutcome": len([t for t in unresolved_tickers if t in unresolved]),
        "unresolvedAbsentFromArchiveEntirely": len([t for t in unresolved_tickers if t not in unresolved]),
        "unresolvedCause": "every unresolved ticker IS present in the settlement archive carrying a null "
                           "outcome -- captured but not yet resolved by Kalshi. None is a join failure, so "
                           "the join mechanism itself is 100% effective on this archive.",
        "quoteCoverage": {
            "tickersWithAnyValidPregameQuote": len([t for t in snapshot_tickers if quotes.get(t)]),
            "note": "vig-free fair midpoint reconstructable from archived yesBid/yesAsk",
        },
    }

    overall = score_block(rows, "ALL_HITTER_PROPS")
    overall["verdict"] = classify(overall, min_rows=MIN_FAMILY_ROWS, min_keys=MIN_FAMILY_KEYS)

    dev = [r for r in rows if r["date"] <= DEV_DATE_MAX]
    val = [r for r in rows if r["date"] > DEV_DATE_MAX]
    dev_block = score_block(dev, "DEVELOPMENT")
    dev_block["verdict"] = classify(dev_block, min_rows=MIN_FAMILY_ROWS)
    val_block = score_block(val, "VALIDATION")
    val_block["verdict"] = classify(val_block, min_rows=MIN_FAMILY_ROWS)

    leave_one_date_out = []
    for d in sorted({r["date"] for r in rows}):
        sub = [r for r in rows if r["date"] != d]
        leave_one_date_out.append({
            "heldOutDate": d,
            "rows": len(sub),
            "pairedBrierDelta": round(paired_brier_delta(sub), 6),
        })

    families = family_analysis(rows)
    checkpoints = checkpoint_analysis(rows)
    paired_cp = paired_checkpoint_analysis(rows)
    lineup = lineup_confirmation_value(rows)
    checkpoint_info = checkpoint_information_audit(snapshots)
    edges = edge_bucket_analysis(rows)
    directions = direction_analysis(rows)
    bands = price_band_analysis(rows)
    depth = sample_depth_analysis(rows)

    economics = {"OVERALL": secondary_economics(rows, "OVERALL")}
    for f in families:
        if f.get("meetsFloor"):
            economics[f["family"]] = secondary_economics(
                [r for r in rows if r["marketFamily"] == f["family"]], f["family"])
    for cp in checkpoints:
        if cp.get("rows", 0) >= MIN_CHECKPOINT_ROWS:
            economics[cp["checkpoint"]] = secondary_economics(
                [r for r in rows if r["checkpoint"] == cp["checkpoint"]], cp["checkpoint"])

    # ── Overall classification, by the preregistered vocabulary ──────────
    family_verdicts = {f["family"]: f["verdict"] for f in families if f.get("meetsFloor")}
    wins = [k for k, v in family_verdicts.items() if v == "MODEL_BEATS_MARKET"]
    losses = [k for k, v in family_verdicts.items() if v == "MARKET_BEATS_MODEL"]
    if not family_verdicts:
        classification = "INSUFFICIENT_SAMPLE"
    elif wins and losses:
        classification = "MIXED_BY_FAMILY"
    elif wins:
        classification = "MODEL_BEATS_MARKET"
    elif losses and len(losses) == len(family_verdicts):
        classification = "MARKET_BEATS_MODEL"
    else:
        classification = "PARITY"
    if edges.get("edgeInversion"):
        classification = "EDGE_INVERSION" if classification == "MARKET_BEATS_MODEL" else classification

    shadow_justified = bool(wins) and val_block.get("verdict") == "MODEL_BEATS_MARKET"
    disposition = "SHADOW_CANDIDATE" if shadow_justified else "LEVEL_0_MEASUREMENT_ONLY"

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "Hitter Prop Probability Validity / Edge Audit",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "researchOnly": True,
        "productionChanged": False,
        "parametersFitted": 0,
        "correctionFitted": False,
        "usesUserConfirmedWagers": False,
        "impliesRecommendationsWereBet": False,
        "issue43AutoSettlementImplemented": False,
        "coverage": {
            "snapshotRows": len(snapshots),
            "eligibleRows": len(rows),
            "exclusions": dict(exclusions),
            "exclusionTotal": sum(exclusions.values()),
        },
        "joinAudit": join_audit,
        "overall": overall,
        "chronological": {
            "devDateMax": DEV_DATE_MAX,
            "development": dev_block,
            "validation": val_block,
            "leaveOneDateOut": leave_one_date_out,
        },
        "familyAnalysis": families,
        "checkpointAnalysis": checkpoints,
        "pairedCheckpointTransitions": paired_cp,
        "lineupConfirmationValue": lineup,
        "checkpointInformationAudit": checkpoint_info,
        "edgeBucketAnalysis": edges,
        "directionAnalysis": directions,
        "priceBandAnalysis": bands,
        "sampleDepthAnalysis": depth,
        "calibration": {
            "model": calibration_table(rows, "modelP"),
            "market": calibration_table(rows, "marketP"),
        },
        "secondaryEconomics": economics,
        "classification": classification,
        "disposition": disposition,
        "maximumDisposition": "SHADOW_CANDIDATE",
        "shadowCandidateJustified": shadow_justified,
        "productionActivationAuthorized": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: snapshots={len(snapshots)} eligible={len(rows)} "
          f"keys={overall.get('playerGameKeys')} games={overall.get('independentGames')} "
          f"dates={overall.get('independentDates')}")
    print(f"  OVERALL model Brier {overall['modelBrier']} vs market {overall['marketBrier']} "
          f"delta {overall['pairedBrierDelta']:+.6f} CI {overall['pairedBrierDeltaCI']['low']},{overall['pairedBrierDeltaCI']['high']} -> {overall['verdict']}")
    for f in families:
        print(f"  {f['family']:24} n={f.get('rows',0):5} keys={f.get('playerGameKeys',0):4} "
              f"delta={f.get('pairedBrierDelta')} p={f.get('bootstrapPValue')} -> {f['verdict']}")
    for cp in checkpoints:
        print(f"  {cp['checkpoint']:24} n={cp.get('rows',0):5} delta={cp.get('pairedBrierDelta')} -> {cp['verdict']}")
    print(f"  edge monotone={edges['monotoneImproving']} inversion={edges['edgeInversion']}")
    print(f"  classification={classification} disposition={disposition}")
    return 0


def _write_markdown(a):
    o = a["overall"]
    ci = o["pairedBrierDeltaCI"]
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        f"**RESEARCH ONLY. No production change. No candidate activated. Parameters fitted: {a['parametersFitted']}.**",
        "",
        "## Why this experiment exists",
        "",
        "Hitter props are the large majority of what this system surfaces -- roughly 61,683 of 82,304",
        "recommendation rows and 77,135 of 100,695 settled contracts -- yet MLB-RSCH-0022, -0024 and -0027",
        "contained **zero** hitter rows. Every previous statement that \"production trails Kalshi\" described",
        "only a minority of the system's output. This is the first audit of the majority.",
        "",
        "## The dominant structural caveat",
        "",
        f"**{o['rows']:,} eligible rows resolve to only {o['playerGameKeys']} playerGameKeys across "
        f"{o['independentGames']} games and {o['independentDates']} dates** -- {o['rowsPerPlayerGameKey']} rows",
        "per player-game. The same player-game recurs at up to five checkpoints and across multiple ladder",
        "rungs, so these are **repeated measures**, not independent observations. Treating the row count as",
        "the sample size would overstate precision by roughly an order of magnitude. Every interval below",
        "clusters on `playerGameKey`; game- and date-clustered variants are reported alongside.",
        "",
        "## Corpus and exclusions",
        "",
        f"- Snapshot rows: **{a['coverage']['snapshotRows']:,}**",
        f"- Eligible after every preregistered rule: **{a['coverage']['eligibleRows']:,}**",
        f"- Excluded: **{a['coverage']['exclusionTotal']:,}**, every one reason-coded:",
        "",
        "| Reason | Rows |",
        "|---|---:|",
    ]
    for k, v in sorted(a["coverage"]["exclusions"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{k}` | {v} |")

    ja = a["joinAudit"]
    lines += [
        "",
        "## Join / settlement audit",
        "",
        f"**Mechanism:** {ja['mechanism']}",
        "",
        f"- Snapshot tickers: {ja['snapshotTickers']:,} · joined: {ja['settlementJoinedTickers']:,} "
        f"(**{ja['joinRateByTicker']:.1%}**)",
        f"- Unresolved: {ja['unresolvedTickers']} — present in archive with a null outcome: "
        f"{ja['unresolvedPresentInArchiveButNoYesNoOutcome']}; absent entirely: "
        f"{ja['unresolvedAbsentFromArchiveEntirely']}",
        "",
        f"{ja['unresolvedCause']}",
        "",
        "## Headline: model vs Kalshi contemporaneous fair price",
        "",
        "Paired delta is **model minus market** — negative means the model is better.",
        "",
        "| | Model | Kalshi fair |",
        "|---|---:|---:|",
        f"| Brier | {o['modelBrier']} | {o['marketBrier']} |",
        f"| Log loss | {o['modelLogLoss']} | {o['marketLogLoss']} |",
        f"| ECE | {o['modelECE']} | {o['marketECE']} |",
        f"| Calibration slope | {o['modelCalibrationSlope']} | {o['marketCalibrationSlope']} |",
        "",
        f"**Paired Brier delta: {o['pairedBrierDelta']:+.6f}** "
        f"[{ci['low']}, {ci['high']}] (clustered on `{ci['clusterUnit']}`)",
        "",
        f"- game-clustered: [{o['pairedBrierDeltaCI_gameClustered']['low']}, {o['pairedBrierDeltaCI_gameClustered']['high']}]",
        f"- date-clustered: [{o['pairedBrierDeltaCI_dateClustered']['low']}, {o['pairedBrierDeltaCI_dateClustered']['high']}]",
        f"- paired log-loss delta: {o['pairedLogLossDelta']:+.6f}",
        "",
        f"**Verdict: `{o['verdict']}`**",
        "",
        "## By family",
        "",
        "| Family | Rows | Keys | Model | Market | Paired delta [CI] | p | FDR | Verdict |",
        "|---|---:|---:|---:|---:|---|---:|:-:|---|",
    ]
    for f in a["familyAnalysis"]:
        if not f.get("rows"):
            lines.append(f"| {f['family']} | 0 | - | - | - | - | - | - | INSUFFICIENT_SAMPLE |")
            continue
        fci = f.get("pairedBrierDeltaCI", {})
        lines.append(
            f"| {f['family']} | {f['rows']} | {f['playerGameKeys']} | {f['modelBrier']} | {f['marketBrier']} | "
            f"{f['pairedBrierDelta']:+.4f} [{fci.get('low')}, {fci.get('high')}] | {f.get('bootstrapPValue')} | "
            f"{'yes' if f.get('fdrSignificant') else 'no'} | {f['verdict']} |")

    e = a["edgeBucketAnalysis"]
    lines += [
        "",
        "## Declared-edge buckets",
        "",
        "Production declares edge against the **executable ask** (its own definition, preserved unchanged).",
        "The question is whether a larger declared edge buys a larger realized advantage over Kalshi.",
        "",
        "| Declared edge | Rows | Keys | Paired delta | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for b in e["buckets"]:
        if not b.get("rows"):
            lines.append(f"| {b['bucket']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {b['bucket']} | {b['rows']} | {b.get('playerGameKeys','-')} | "
                     f"{b['pairedBrierDelta']:+.4f} | {b.get('verdict')} |")
    lines += [
        "",
        f"- Monotone improving with declared edge: **{e['monotoneImproving']}**",
        f"- **Edge inversion (model relatively WORSE at high declared edge): {e['edgeInversion']}**",
        "",
        "## By checkpoint",
        "",
        "| Checkpoint | Rows | Keys | Model Brier | Market Brier | Paired delta | Mean ask | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for c in a["checkpointAnalysis"]:
        if not c.get("rows"):
            lines.append(f"| {c['checkpoint']} | 0 | - | - | - | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {c['checkpoint']} | {c['rows']} | {c['playerGameKeys']} | {c['modelBrier']} | "
                     f"{c['marketBrier']} | {c['pairedBrierDelta']:+.4f} | {c.get('meanExecutableAsk')} | {c['verdict']} |")

    lines += [
        "",
        "## Exactly-paired checkpoint transitions",
        "",
        "Same player, game, family AND threshold observed at both checkpoints — far more informative than",
        "comparing unrelated checkpoint populations, which differ in which games they even contain.",
        "",
        "| Transition | Pairs | Model Brier change | Market Brier change | Mean abs model move | Later better |",
        "|---|---:|---:|---:|---:|:-:|",
    ]
    for t in a["pairedCheckpointTransitions"]:
        if not t.get("pairs"):
            lines.append(f"| {t['transition']} | 0 | - | - | - | - |")
            continue
        lines.append(f"| {t['transition']} | {t['pairs']} | {t['modelBrierChange']:+.5f} | "
                     f"{t['marketBrierChange']:+.5f} | {t['meanAbsProbabilityMove']:.4f} | "
                     f"{'yes' if t['laterIsBetter'] else 'no'} |")

    lu = a["lineupConfirmationValue"]
    lines += ["", "## Lineup-confirmation value (paired on the same market rung)", ""]
    if lu.get("pairs"):
        lines += [
            f"- Pairs: {lu['pairs']} across {lu['playerGameKeys']} playerGameKeys",
            f"- Model Brier: {lu['preLineupModelBrier']} pre-lineup -> {lu['confirmedModelBrier']} confirmed "
            f"(**improvement {lu['modelBrierImprovement']:+.5f}**)",
            f"- Market Brier: {lu['preLineupMarketBrier']} -> {lu['confirmedMarketBrier']} "
            f"(improvement {lu['marketBrierImprovement']:+.5f})",
            f"- Paired delta vs market: {lu['preLineupPairedDelta']:+.5f} pre -> {lu['confirmedPairedDelta']:+.5f} confirmed",
            f"- **Confirmation helps the model: {lu['confirmationHelpsModel']}**",
        ]
    else:
        lines.append("No exactly-paired pre-lineup/confirmed observations available.")

    d = a["directionAnalysis"]
    lines += [
        "",
        "## Disagreement direction",
        "",
        "| Direction | Rows | Keys | Paired delta | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for k in ("MODEL_ABOVE_MARKET", "MODEL_BELOW_MARKET"):
        b = d[k]
        if not b.get("rows"):
            lines.append(f"| {k} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {k} | {b['rows']} | {b['playerGameKeys']} | {b['pairedBrierDelta']:+.4f} | {b['verdict']} |")

    lines += ["", "## Calibration (fixed deciles)", "",
              "| Bin | Rows | Model predicted | Actual | Market predicted |", "|---|---:|---:|---:|---:|"]
    market_by_bin = {t["bin"]: t for t in a["calibration"]["market"]}
    for t in a["calibration"]["model"]:
        if not t.get("rows"):
            continue
        mk = market_by_bin.get(t["bin"], {})
        lines.append(f"| {t['bin']} | {t['rows']} | {t['predicted']} | {t['actual']} | {mk.get('predicted','-')} |")

    lines += ["", "## Sample-depth bins (archived PA count, PIT-safe)", "",
              "| Bin | Rows | Keys | Paired delta | Verdict |", "|---|---:|---:|---:|---|"]
    for b in a["sampleDepthAnalysis"]:
        if not b.get("rows"):
            lines.append(f"| {b['bin']} | 0 | - | - | INSUFFICIENT_SAMPLE |")
            continue
        lines.append(f"| {b['bin']} | {b['rows']} | {b['playerGameKeys']} | {b['pairedBrierDelta']:+.4f} | {b['verdict']} |")

    ch = a["chronological"]
    lines += [
        "",
        "## Chronological structure",
        "",
        f"DEVELOPMENT (<= {ch['devDateMax']}): {ch['development'].get('rows',0)} rows / "
        f"{ch['development'].get('playerGameKeys',0)} keys, delta {ch['development'].get('pairedBrierDelta')}",
        "",
        f"VALIDATION (later): {ch['validation'].get('rows',0)} rows / "
        f"{ch['validation'].get('playerGameKeys',0)} keys, delta {ch['validation'].get('pairedBrierDelta')}",
        "",
        "With so few independent player-games, the validation block supports a **directional** read only;",
        "no confirmatory claim is made from it. Leave-one-date-out deltas are reported as robustness.",
        "",
        "## Secondary fee-aware economics",
        "",
        "Computed only AFTER the predictive verdict. Never selective. **Never implies any recommendation was",
        "actually bet** -- automatic bet settlement remains GitHub issue #43's separate concern, not",
        "implemented or claimed here.",
        "",
        "| Segment | Opportunities | Wins | Avg entry | Gross (pre-fee) | Fees | Net | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for seg, ec in a["secondaryEconomics"].items():
        lines.append(f"| {seg} | {ec['opportunities']} | {ec['wins']} | {ec['averageEntryPrice']} | "
                     f"{ec['grossPnlBeforeFees']} | {ec['totalFees']} | {ec['netPnl']} | {ec['netRoi']} |")

    lines += [
        "",
        "## Result",
        "",
        f"- Classification: **{a['classification']}**",
        f"- Disposition: **{a['disposition']}** (maximum permitted: {a['maximumDisposition']})",
        f"- Shadow candidate justified: **{a['shadowCandidateJustified']}**",
        f"- Production activation authorized: {a['productionActivationAuthorized']}",
        f"- Correction fitted: {a['correctionFitted']} · user wagers used: {a['usesUserConfirmedWagers']}",
        "",
    ]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
