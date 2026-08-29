"""
lib/edgelab/research/frozen_forward_scorer.py
=====================================
The frozen-hypothesis FORWARD confirmation engine. RESEARCH ONLY.

This is deliberately NOT another fitting experiment. Three independent
research paths (MLB-RSCH-0023 probability recalibration, MLB-RSCH-0024
market-residual alpha, MLB-RSCH-0026 Kalshi band shrinkage) each found a
relationship that held in-window and evaporated out-of-window. The only
way to break that pattern is to stop fitting and start confirming:

    FROZEN MODEL -> NEW DATA -> NO REFIT -> SCORE

Every parameter used here is read from an already-committed frozen
artifact and is NEVER re-estimated. The module exposes no fitting
function at all; there is nothing in this file that could refit alpha,
beta, a band edge, or a quality category even by accident.

FORWARD WINDOW: settlement date strictly after FORWARD_START_DATE
(2026-08-28). Rows are joined from prospectively-captured pregame
model evaluations and pregame market observations to settled outcomes,
so every predictive field predates the event it predicts.

CHECKPOINTS are reporting tiers only -- reaching one never changes a
model, only what may be claimed:
    CHECKPOINT_0  <250 rows or <20 games  -> HEALTH ONLY
    CHECKPOINT_1  >=250 rows, >=20 games  -> EARLY DIRECTIONAL
    CHECKPOINT_2  >=500 rows, >=40 games  -> INTERMEDIATE
    CHECKPOINT_3  >=1000 rows, >=60 games -> FIRST MEANINGFUL CONFIRMATION
    CHECKPOINT_4  >=2000 rows, >=100 games-> STRONGER CONFIRMATION

DECISION RULES (preregistered here, before any forward row exists, and
never to be relaxed): a frozen candidate may be reported as
FORWARD_SUPPORTS_FROZEN_FINDING only at CHECKPOINT_3 or better AND when
it beats its benchmark on BOTH Brier and log loss, AND the direction
holds on a majority of forward dates, AND the effect is not concentrated
in a single family. `PRODUCTION_APPROVED` is not a value this module can
ever emit.
"""
import json
import math
import os

from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    independent_unit_count,
    expected_calibration_error,
    brier_and_log_loss_summary,
    game_clustered_bootstrap_ci,
)

FORWARD_START_DATE = "2026-08-28"  # strictly greater than this

# Status vocabulary -- PRODUCTION_APPROVED deliberately absent.
INSUFFICIENT = "INSUFFICIENT_FORWARD_DATA"
EARLY_DIRECTIONAL = "EARLY_DIRECTIONAL"
INTERMEDIATE_UNCONFIRMED = "INTERMEDIATE_UNCONFIRMED"
SUPPORTS = "FORWARD_SUPPORTS_FROZEN_FINDING"
CONTRADICTS = "FORWARD_CONTRADICTS_FROZEN_FINDING"
MIXED = "MIXED_BY_FAMILY"

CHECKPOINTS = (
    ("CHECKPOINT_4", 2000, 100, "STRONGER_CONFIRMATION"),
    ("CHECKPOINT_3", 1000, 60, "FIRST_MEANINGFUL_CONFIRMATION"),
    ("CHECKPOINT_2", 500, 40, "INTERMEDIATE"),
    ("CHECKPOINT_1", 250, 20, "EARLY_DIRECTIONAL"),
    ("CHECKPOINT_0", 0, 0, "HEALTH_ONLY"),
)
MIN_CHECKPOINT_FOR_CONFIRMATION = "CHECKPOINT_3"

# Fixed segment definitions -- copied from the frozen experiments, never
# re-derived from forward outcomes.
PRICE_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
DISAGREEMENT_BANDS = ((0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01))
MIN_SEGMENT_ROWS = 50
MIN_SEGMENT_GAMES = 10
FDR_ALPHA = 0.10
PROB_CLAMP = (0.01, 0.99)


def classify_checkpoint(n_rows, n_games):
    for name, min_rows, min_games, label in CHECKPOINTS:
        if n_rows >= min_rows and n_games >= min_games:
            return {"checkpoint": name, "label": label, "rows": n_rows, "games": n_games}
    return {"checkpoint": "CHECKPOINT_0", "label": "HEALTH_ONLY", "rows": n_rows, "games": n_games}


def checkpoint_rank(name):
    order = [c[0] for c in reversed(CHECKPOINTS)]  # CHECKPOINT_0 .. CHECKPOINT_4
    return order.index(name) if name in order else 0


def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def _logit(p):
    p = _clamp(p)
    return math.log(p / (1 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def apply_frozen_residual(model_p, market_p, alpha):
    """MLB-RSCH-0024's frozen form. `alpha` MUST come from the committed
    artifact; this function has no way to estimate one."""
    m = _logit(market_p)
    return _sigmoid(m + alpha * (_logit(model_p) - m))


def apply_frozen_shrink(market_p, beta, base):
    """MLB-RSCH-0026's frozen form. `beta`/`base` MUST come from the
    committed artifact."""
    b = _logit(base)
    return _sigmoid(b + beta * (_logit(market_p) - b))


def load_frozen_artifact(path):
    """Reads a frozen artifact READ-ONLY. Never writes, never mutates."""
    with open(path) as f:
        return json.load(f)


def _row_log_loss(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score_forecaster(rows, prob_fn):
    if not rows:
        return {"n": 0, "independentGames": 0, "brier": None, "logLoss": None, "ece": None}
    pairs = [(prob_fn(r), r["outcome"]) for r in rows]
    brier, log_loss = brier_and_log_loss_summary(pairs)
    return {"n": len(rows), "independentGames": independent_unit_count(rows, key="gameId"),
            "brier": brier, "logLoss": log_loss, "ece": expected_calibration_error(pairs)}


def paired_delta(rows, cand_fn, ref_fn, *, with_ci=True):
    """Paired Brier and log-loss deltas (candidate minus reference).
    CIs are computed only when the sample can support them."""
    if not rows:
        return {"n": 0, "brierDelta": None, "logLossDelta": None, "brierDeltaCI": None}
    paired = [{"gameId": r["gameId"],
               "b": (cand_fn(r) - r["outcome"]) ** 2 - (ref_fn(r) - r["outcome"]) ** 2,
               "l": _row_log_loss(cand_fn(r), r["outcome"]) - _row_log_loss(ref_fn(r), r["outcome"])}
              for r in rows]

    def _mb(s):
        return sum(x["b"] for x in s) / len(s) if s else None

    def _ml(s):
        return sum(x["l"] for x in s) / len(s) if s else None

    out = {"n": len(paired), "independentGames": independent_unit_count(paired, key="gameId"),
           "brierDelta": round(_mb(paired), 6), "logLossDelta": round(_ml(paired), 6),
           "interpretation": "negative == candidate better than reference"}
    if with_ci and independent_unit_count(paired, key="gameId") >= MIN_SEGMENT_GAMES:
        lo, hi, _ = game_clustered_bootstrap_ci(paired, _mb, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
        out["brierDeltaCI"] = {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"}
    else:
        out["brierDeltaCI"] = None
    return out


def per_date_direction(rows, cand_fn, ref_fn):
    """Fraction of forward settle-dates on which the candidate's Brier
    delta is favourable -- the 'direction holds across dates' rule."""
    dates = sorted({r["settleDate"] for r in rows})
    per = {}
    for d in dates:
        day = [r for r in rows if r["settleDate"] == d]
        dd = paired_delta(day, cand_fn, ref_fn, with_ci=False)
        per[d] = {"n": dd["n"], "brierDelta": dd["brierDelta"]}
    favourable = [d for d, v in per.items() if v["brierDelta"] is not None and v["brierDelta"] < 0]
    return {"byDate": per, "datesTotal": len(dates), "datesFavourable": len(favourable),
            "majorityFavourable": len(dates) > 0 and len(favourable) > len(dates) / 2}


def segment_scores(rows, cand_fn, ref_fn, key_fn, label):
    """Generic fixed-segment breakdown honouring minimum-sample rules."""
    groups = {}
    for r in rows:
        k = key_fn(r)
        if k is not None:
            groups.setdefault(k, []).append(r)
    out = {}
    for k, sub in sorted(groups.items(), key=lambda kv: str(kv[0])):
        games = independent_unit_count(sub, key="gameId")
        if len(sub) < MIN_SEGMENT_ROWS or games < MIN_SEGMENT_GAMES:
            out[str(k)] = {"status": "BELOW_MINIMUM_SAMPLE", "n": len(sub), "games": games}
            continue
        d = paired_delta(sub, cand_fn, ref_fn)
        out[str(k)] = {"status": "SCORED", "n": len(sub), "games": games,
                       "brierDelta": d["brierDelta"], "logLossDelta": d["logLossDelta"],
                       "brierDeltaCI": d["brierDeltaCI"]}
    return {"segment": label, "groups": out}


def benjamini_hochberg(pvalues_by_key, alpha=FDR_ALPHA):
    items = sorted(((k, p) for k, p in pvalues_by_key.items() if p is not None), key=lambda kv: kv[1])
    m = len(items)
    max_i = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= alpha * i / m:
            max_i = i
    return {k: (i <= max_i) for i, (k, _) in enumerate(items, start=1)}


def decide_status(checkpoint_name, paired, direction, family_segment):
    """The preregistered forward decision rule. Never relaxed, and it
    cannot emit PRODUCTION_APPROVED."""
    rank = checkpoint_rank(checkpoint_name)
    if rank < checkpoint_rank("CHECKPOINT_1"):
        return INSUFFICIENT, ["below CHECKPOINT_1 -- health only, no interpretation"]

    reasons = []
    brier, ll = paired.get("brierDelta"), paired.get("logLossDelta")
    if brier is None or ll is None:
        return INSUFFICIENT, ["no scoreable forward rows"]

    beats_both = brier < 0 and ll < 0
    harms_both = brier > 0 and ll > 0

    scored = {k: v for k, v in family_segment.get("groups", {}).items() if v.get("status") == "SCORED"}
    improving = [k for k, v in scored.items() if v.get("brierDelta") is not None and v["brierDelta"] < 0]
    concentrated = len(scored) >= 2 and len(improving) <= 1

    if rank < checkpoint_rank(MIN_CHECKPOINT_FOR_CONFIRMATION):
        label = EARLY_DIRECTIONAL if rank == checkpoint_rank("CHECKPOINT_1") else INTERMEDIATE_UNCONFIRMED
        reasons.append(f"{checkpoint_name} reached; confirmation requires {MIN_CHECKPOINT_FOR_CONFIRMATION}")
        reasons.append(f"directional read: brierDelta={brier} logLossDelta={ll}")
        return label, reasons

    if beats_both and direction.get("majorityFavourable") and not concentrated:
        return SUPPORTS, [f"beats benchmark on Brier ({brier}) and log loss ({ll})",
                          f"direction holds on {direction['datesFavourable']}/{direction['datesTotal']} forward dates",
                          f"improvement spread across {len(improving)}/{len(scored)} scored families"]
    if harms_both:
        return CONTRADICTS, [f"worse on Brier ({brier}) and log loss ({ll})"]
    if concentrated and beats_both:
        return MIXED, [f"improvement concentrated in {len(improving)}/{len(scored)} scored families"]
    return INTERMEDIATE_UNCONFIRMED, [f"mixed evidence: brierDelta={brier} logLossDelta={ll}",
                                      f"datesFavourable={direction.get('datesFavourable')}/{direction.get('datesTotal')}"]
