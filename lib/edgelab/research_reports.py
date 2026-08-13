"""
lib/edgelab/research_reports.py
====================================
EdgeLab Research Trustworthiness milestone: the report generators over
lib.edgelab.research_dataset.build_opportunity_rows()'s canonical
(marketTicker x checkpoint) rows. READ-ONLY, descriptive-statistics-only
-- exactly like lib.edgelab.calibration, whose conventions (sample-size
status is a reading instruction never a filter; a computed number is
never withheld for a small n) this module follows throughout, extended
with the game-clustering awareness lib.edgelab.research_stats adds.

Produces the eight machine-readable reports spec section 17 asks for
(market_calibration, model_calibration, edge_backtest,
market_family_research, checkpoint_research, ladder_research,
research_data_quality, strategy_validation) plus one concise
human-readable summary. Every report function is a pure function over
already-built `rows` (or, for research_data_quality, the raw source
lists) -- no file I/O in this module; scripts/edgelab/run_research_reports.py
does the loading/writing.

Never calls this a validated/proven edge. Every report that touches a
win rate or ROI carries lib.edgelab.research_stats.sample_size_status's
explicit, conservative interpretation text, and section 20's rule is
enforced throughout: nothing here is fit, tuned, or threshold-selected
against the sample it is reporting on.
"""

from collections import defaultdict

from lib.edgelab.prospective_snapshot import CORE_CHECKPOINTS
from lib.edgelab.research_dataset import PRICE_AGE_UNAVAILABLE, STANDARDIZED_CHECKPOINT_ORDER
from lib.edgelab.research_splits import DEVELOPMENT, HOLDOUT, VALIDATION, chronological_split, label_rows_with_split
from lib.edgelab.research_stats import (
    brier_and_log_loss_summary,
    calibration_slope_intercept,
    expected_calibration_error,
    game_clustered_bootstrap_ci,
    independent_unit_count,
    roi_value_fn,
    sample_size_status,
    win_rate_value_fn,
)
from lib.edgelab.settlement import derive_bet_result

PRICE_BUCKET_WIDTH_PCT = 5     # spec section 7: 0-5%, 5-10%, ... 95-100%
EDGE_BUCKET_WIDTH_PCT = 2      # spec section 9: <0%, 0-2%, 2-4%, ..., 10%+
MODEL_PROB_BUCKET_WIDTH_PCT = 10  # spec section 8: 5- or 10-point bins -- 10 chosen (documented)


# ── Shared row-set helpers ────────────────────────────────────────────────

def _settled_rows(rows):
    return [r for r in rows if r.get("settlementStatus") == "SETTLED" and r.get("settlementResult") in ("YES", "NO")]


def _player_game_key(row):
    return f"{row.get('gameId')}:{row.get('player')}" if row.get("player") else None


def _price_bucket_pct(price, width=PRICE_BUCKET_WIDTH_PCT):
    if price is None:
        return None
    pct = max(0.0, min(100.0 - 1e-9, price * 100.0))
    lo = int(pct // width) * width
    return f"{lo}-{lo + width}"


def _stat_block(rows_subset, win_predicate, pl_key_side_aware=None):
    """
    The shared n/independentGames/winRate/calibrationError/brier/
    logLoss/ci/sampleStatus block every dimension in this module
    reports -- computed exactly once so every report shares identical
    methodology (matches lib.edgelab.calibration's own
    "computed in exactly one place" convention).

    `win_predicate(row) -> True/False/None`. `pl_key_side_aware(row) ->
    hypothetical P/L per $1 stake (already side-correct), or None`.
    """
    n = len(rows_subset)
    independent_games = independent_unit_count(rows_subset, key="gameId")
    decided = [win_predicate(r) for r in rows_subset]
    decided = [d for d in decided if d is not None]
    win_rate = (sum(1 for d in decided if d) / len(decided)) if decided else None

    prob_pairs = [(r.get("modelFairProbability"), 1 if win_predicate(r) else 0)
                  for r in rows_subset if r.get("modelFairProbability") is not None and win_predicate(r) is not None]
    price_pairs = [(r.get("executableYesPrice"), 1 if r.get("settlementResult") == "YES" else 0)
                   for r in rows_subset if r.get("executableYesPrice") is not None and r.get("settlementResult") in ("YES", "NO")]

    avg_brier, avg_log_loss = brier_and_log_loss_summary(prob_pairs) if prob_pairs else (None, None)

    roi = None
    if pl_key_side_aware is not None:
        pls = [(r.get("stake", 1.0), pl_key_side_aware(r)) for r in rows_subset if pl_key_side_aware(r) is not None]
        if pls:
            total_stake = sum(s for s, _ in pls)
            roi = (sum(p for _, p in pls) / total_stake) if total_stake else None

    ci_lo, ci_hi, ci_method = (None, None, None)
    if decided:
        ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(rows_subset, win_rate_value_fn(win_predicate))

    return {
        "n": n,
        "independentGames": independent_games,
        "winRate": round(win_rate, 4) if win_rate is not None else None,
        "roi": round(roi, 4) if roi is not None else None,
        "avgBrierScore": avg_brier,
        "avgLogLoss": avg_log_loss,
        "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
        "sampleSize": sample_size_status(n, independent_games),
        "priceCalibration": {
            "n": len(price_pairs),
            "brier": brier_and_log_loss_summary(price_pairs)[0] if price_pairs else None,
        },
    }


# ── A. market_calibration ─────────────────────────────────────────────────

def market_calibration(rows):
    """
    Full-universe Kalshi implied-probability-vs-outcome calibration --
    NEVER requires a bet to have been placed (spec section 7). Every
    settled row with a real executable YES price contributes, whether
    or not the market was ever recommended or bet.
    """
    eligible = [r for r in _settled_rows(rows) if r.get("executableYesPrice") is not None]

    def _bucket_report(bucket_fn):
        buckets = defaultdict(list)
        for r in eligible:
            key = bucket_fn(r)
            if key is not None:
                buckets[key].append(r)
        out = []
        for key, bucket_rows in buckets.items():
            n = len(bucket_rows)
            avg_implied = sum(r["executableYesPrice"] for r in bucket_rows) / n
            actual_yes_rate = sum(1 for r in bucket_rows if r["settlementResult"] == "YES") / n
            pairs = [(r["executableYesPrice"], 1 if r["settlementResult"] == "YES" else 0) for r in bucket_rows]
            brier, _ = brier_and_log_loss_summary(pairs)
            independent_games = independent_unit_count(bucket_rows, key="gameId")
            ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(
                bucket_rows, win_rate_value_fn(lambda r: r["settlementResult"] == "YES")
            )
            out.append({
                "bucket": key,
                "n": n,
                "avgImpliedProbability": round(avg_implied, 4),
                "actualYesRate": round(actual_yes_rate, 4),
                "calibrationError": round(actual_yes_rate - avg_implied, 4),
                "brierScore": brier,
                "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
                "sampleSize": sample_size_status(n, independent_games),
            })
        return sorted(out, key=lambda r: str(r["bucket"]))

    return {
        "overall": _bucket_report(lambda r: "ALL"),
        "byPriceBucket": _bucket_report(lambda r: _price_bucket_pct(r["executableYesPrice"])),
        "byCanonicalMarketFamily": _bucket_report(lambda r: r.get("canonicalMarketFamily")),
        "byMarketHorizon": _bucket_report(lambda r: r.get("marketHorizon")),
        "byResearchCheckpoint": _bucket_report(lambda r: r.get("researchCheckpoint")),
    }


# ── B. model_calibration ───────────────────────────────────────────────────

def _model_eligible_rows(rows):
    out = []
    for r in _settled_rows(rows):
        if not r.get("modelEvaluationAvailable") or r.get("modelFairProbability") is None:
            continue
        side = r.get("side") or "YES"
        result = derive_bet_result(r["settlementResult"], side)
        if result is None:
            continue
        out.append((r, side, result))
    return out


def model_calibration(rows):
    """
    Model-fair-probability-vs-outcome calibration, restricted to rows
    with a CAUSALLY VALID model evaluation (spec section 8) -- i.e. only
    rows lib.edgelab.temporal_alignment actually resolved a
    no-look-ahead evaluation for. Also reports the contemporaneous
    Kalshi-implied-probability calibration on the exact SAME row set, so
    the two are directly comparable (never a superiority claim from one
    aggregate score alone -- spec section 8's explicit warning).
    """
    eligible = _model_eligible_rows(rows)

    def _bucket_report(bucket_fn):
        buckets = defaultdict(list)
        for r, side, result in eligible:
            key = bucket_fn(r)
            if key is not None:
                buckets[key].append((r, side, result))
        out = []
        for key, items in buckets.items():
            n = len(items)
            model_pairs = [(r["modelFairProbability"], 1 if result == "WIN" else 0) for r, side, result in items]
            market_pairs = [
                ((r["executableYesPrice"] if side == "YES" else r["executableNoPrice"]), 1 if result == "WIN" else 0)
                for r, side, result in items
                if (r["executableYesPrice"] if side == "YES" else r["executableNoPrice"]) is not None
            ]
            avg_model_prob = sum(p for p, _ in model_pairs) / n
            actual_win_rate = sum(o for _, o in model_pairs) / n
            model_brier, model_log_loss = brier_and_log_loss_summary(model_pairs)
            market_brier, market_log_loss = brier_and_log_loss_summary(market_pairs) if market_pairs else (None, None)
            ece = expected_calibration_error(model_pairs)
            slope, intercept = calibration_slope_intercept(model_pairs)
            independent_games = independent_unit_count([r for r, _, _ in items], key="gameId")
            ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(
                [r for r, _, _ in items], win_rate_value_fn(
                    lambda row: derive_bet_result(row["settlementResult"], row.get("side") or "YES") == "WIN"
                ),
            )
            out.append({
                "bucket": key,
                "n": n,
                "avgModelProbability": round(avg_model_prob, 4),
                "actualWinRate": round(actual_win_rate, 4),
                "calibrationError": round(actual_win_rate - avg_model_prob, 4),
                "brierScore": model_brier,
                "logLoss": model_log_loss,
                "expectedCalibrationError": ece,
                "calibrationSlope": slope,
                "calibrationIntercept": intercept,
                "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
                "sampleSize": sample_size_status(n, independent_games),
                "contemporaneousMarketComparison": {
                    "n": len(market_pairs),
                    "brierScore": market_brier,
                    "logLoss": market_log_loss,
                    "note": "Same rows' contemporaneous Kalshi-implied price, for direct comparison -- not a superiority claim by itself.",
                },
            })
        return sorted(out, key=lambda r: str(r["bucket"]))

    def _prob_bucket(r):
        return _price_bucket_pct(r["modelFairProbability"], width=MODEL_PROB_BUCKET_WIDTH_PCT)

    return {
        "overall": _bucket_report(lambda r: "ALL"),
        "byModelProbabilityBucket": _bucket_report(_prob_bucket),
        "byCanonicalMarketFamily": _bucket_report(lambda r: r.get("canonicalMarketFamily")),
    }


# ── C. edge_backtest ────────────────────────────────────────────────────

def _edge_side_opportunities(rows):
    """
    Expands each model-linked settled row into up to TWO side-specific
    opportunities (YES and NO), per spec section 9: "Support YES and NO
    opportunities correctly. Do not evaluate only one side merely
    because settlement is represented as YES/NO." A row's own `side`
    (almost always 'YES' in real data -- ModelEvaluation.side is
    documented as usually null, defaulted to YES elsewhere in this repo)
    is still just ONE of the two possible opportunities this ticker
    represents; the mirror (NO) opportunity -- betting against the
    model's stated side -- is evaluated too, using the complementary
    probability (1 - modelFairProbability) against the NO-side
    executable price.
    """
    opportunities = []
    for r in _settled_rows(rows):
        if not r.get("modelEvaluationAvailable") or r.get("modelFairProbability") is None:
            continue
        model_side = r.get("side") or "YES"
        model_prob = r["modelFairProbability"]

        yes_price = r.get("executableYesPrice")
        if yes_price is not None:
            yes_prob = model_prob if model_side == "YES" else (1.0 - model_prob)
            opportunities.append(dict(
                r, opportunitySide="YES", opportunityModelProbability=round(yes_prob, 4),
                opportunityPrice=yes_price, opportunityEdge=round(yes_prob - yes_price, 4),
                opportunityWin=(r["settlementResult"] == "YES"),
                opportunityReturn=r.get("hypotheticalYesReturn"),
                opportunityMovement=r.get("fullUniverseMarketMovementToClose"),
            ))

        no_price = r.get("executableNoPrice")
        if no_price is not None:
            no_prob = (1.0 - model_prob) if model_side == "YES" else model_prob
            movement = r.get("fullUniverseMarketMovementToClose")
            opportunities.append(dict(
                r, opportunitySide="NO", opportunityModelProbability=round(no_prob, 4),
                opportunityPrice=no_price, opportunityEdge=round(no_prob - no_price, 4),
                opportunityWin=(r["settlementResult"] == "NO"),
                opportunityReturn=r.get("hypotheticalNoReturn"),
                opportunityMovement=(-movement if movement is not None else None),
            ))
    return opportunities


def _edge_bucket_label(edge_fraction, width_pct=EDGE_BUCKET_WIDTH_PCT):
    edge_pct = edge_fraction * 100.0
    if edge_pct < 0:
        return "<0"
    lo = int(edge_pct // width_pct) * width_pct
    if lo >= 10:
        return "10+"
    return f"{lo}-{lo + width_pct}"


def edge_backtest(rows, side_filter=None, max_market_price_age_seconds=None):
    """
    Performance by model-edge bucket (spec section 9), over rows with a
    causally-valid, no-look-ahead model evaluation AND a contemporaneous
    executable price. `side_filter`: None (both sides, default),
    "YES", or "NO".

    `max_market_price_age_seconds` (EdgeLab Prospective Model Snapshots
    reliability pass, spec section 5): when set, drops any opportunity
    whose `marketPriceAgeSeconds` (see lib.edgelab.research_dataset --
    how old the paired Kalshi observation was at the model's OWN
    evaluation instant) is missing or exceeds this threshold, so a
    caller can ask "what happens to model-edge performance when
    marketPriceAge <= 900 seconds?" without a separate code path. None
    (the default) applies no staleness filter at all -- this function
    never silently discards stale-priced data unless explicitly asked
    to. See market_price_staleness_report() for the unfiltered
    distribution every default report should show.

    INTENTIONAL DESIGN, NOT A BUG (EdgeLab Prospective Model Snapshots
    milestone, spec section 16): each causally-valid model row is
    expanded into up to TWO opportunities here -- a YES-side one and a
    NO-side (mirror) one -- via _edge_side_opportunities(), so a single
    underlying row can legitimately contribute a negative-edge YES
    opportunity to one bucket AND a positive-edge NO opportunity to a
    DIFFERENT bucket. This means a bucket's `n` (opportunity count) is
    NOT the same thing as the number of underlying ModelEvaluation rows
    that fed it, and summing `n` across every bucket can legitimately
    exceed the total number of causally-valid rows. `underlyingModelRows`
    (distinct source rows, by marketObservationId) and
    `yesOpportunityCount`/`noOpportunityCount` are reported alongside
    `n` specifically so a reader never mistakes this intentional side
    expansion for duplicated/double-counted data.
    """
    opportunities = _edge_side_opportunities(rows)
    if side_filter:
        opportunities = [o for o in opportunities if o["opportunitySide"] == side_filter]
    if max_market_price_age_seconds is not None:
        opportunities = [
            o for o in opportunities
            if o.get("marketPriceAgeSeconds") is not None and o["marketPriceAgeSeconds"] <= max_market_price_age_seconds
        ]

    buckets = defaultdict(list)
    for o in opportunities:
        buckets[_edge_bucket_label(o["opportunityEdge"])].append(o)

    out = []
    for label, items in buckets.items():
        n = len(items)
        independent_games = independent_unit_count(items, key="gameId")
        player_games = len({_player_game_key(o) for o in items if _player_game_key(o)}) or None
        avg_edge = sum(o["opportunityEdge"] for o in items) / n
        avg_price = sum(o["opportunityPrice"] for o in items) / n
        avg_model_prob = sum(o["opportunityModelProbability"] for o in items) / n
        win_rate = sum(1 for o in items if o["opportunityWin"]) / n
        pairs = [(o["opportunityModelProbability"], 1 if o["opportunityWin"] else 0) for o in items]
        brier, log_loss = brier_and_log_loss_summary(pairs)

        returns = [o["opportunityReturn"] for o in items if o["opportunityReturn"] is not None]
        hypothetical_pl_per_dollar = sum(returns) / len(returns) if returns else None

        movements = [o["opportunityMovement"] for o in items if o["opportunityMovement"] is not None]
        avg_movement = sum(movements) / len(movements) if movements else None
        beat_close_frac = (sum(1 for m in movements if m > 0) / len(movements)) if movements else None

        ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(items, win_rate_value_fn(lambda o: o["opportunityWin"]))
        roi_ci_lo, roi_ci_hi, roi_ci_method = game_clustered_bootstrap_ci(
            items, roi_value_fn(stake_key="_unit_stake", pl_key="opportunityReturn")
        ) if returns else (None, None, None)

        underlying_model_rows = len({o.get("marketObservationId") for o in items if o.get("marketObservationId") is not None})
        yes_opportunity_count = sum(1 for o in items if o["opportunitySide"] == "YES")
        no_opportunity_count = sum(1 for o in items if o["opportunitySide"] == "NO")

        out.append({
            "edgeBucket": label,
            "n": n,
            "underlyingModelRows": underlying_model_rows,
            "yesOpportunityCount": yes_opportunity_count,
            "noOpportunityCount": no_opportunity_count,
            "independentGames": independent_games,
            "playerGames": player_games,
            "avgEstimatedEdge": round(avg_edge, 4),
            "avgExecutablePrice": round(avg_price, 4),
            "avgModelProbability": round(avg_model_prob, 4),
            "actualWinRate": round(win_rate, 4),
            "expectedWinRate": round(avg_model_prob, 4),
            "hypotheticalStake": 1.0,
            "hypotheticalPLPerDollar": round(hypothetical_pl_per_dollar, 4) if hypothetical_pl_per_dollar is not None else None,
            "roi": round(hypothetical_pl_per_dollar, 4) if hypothetical_pl_per_dollar is not None else None,
            "brierScore": brier,
            "logLoss": log_loss,
            "avgPriceMovementToClose": round(avg_movement, 4) if avg_movement is not None else None,
            "fractionBeatingClose": round(beat_close_frac, 4) if beat_close_frac is not None else None,
            "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
            "roiConfidenceInterval": {"low": roi_ci_lo, "high": roi_ci_hi, "method": roi_ci_method, "level": 0.90},
            "sampleSize": sample_size_status(n, independent_games),
        })
    return sorted(out, key=lambda r: (r["edgeBucket"] != "<0", r["edgeBucket"]))


# ── C2. market_price_staleness (Prospective Model Snapshots reliability pass, spec section 5) ──

def market_price_staleness_report(rows):
    """
    Full, UNFILTERED distribution of marketPriceAgeSeconds/
    marketPriceAgeBucket (lib.edgelab.research_dataset) across every row
    with a causally-valid model evaluation -- so stale-price
    concentration is obvious by default, never hidden behind a filter a
    reader has to know to apply. Never discards data globally; use
    edge_backtest(rows, max_market_price_age_seconds=...) to actually
    filter a backtest by a configurable maximum age.
    """
    eligible = [r for r in rows if r.get("modelEvaluationAvailable")]
    n = len(eligible)

    bucket_counts = defaultdict(int)
    ages = []
    for r in eligible:
        bucket_counts[r.get("marketPriceAgeBucket") or PRICE_AGE_UNAVAILABLE] += 1
        if r.get("marketPriceAgeSeconds") is not None:
            ages.append(r["marketPriceAgeSeconds"])

    ages.sort()

    def _percentile(sorted_values, pct):
        if not sorted_values:
            return None
        idx = max(0, min(len(sorted_values) - 1, round(pct * (len(sorted_values) - 1))))
        return sorted_values[idx]

    return {
        "n": n,
        "nWithMarketLinkage": len(ages),
        "nWithoutMarketLinkage": n - len(ages),
        "byBucket": dict(bucket_counts),
        "medianMarketPriceAgeSeconds": _percentile(ages, 0.5),
        "p90MarketPriceAgeSeconds": _percentile(ages, 0.9),
        "note": (
            "Distribution is over every causally-valid model-evaluation row, unfiltered. "
            "A pairing where marketObservationCapturedAt occurred AFTER the model evaluated is never "
            "included here at all (see lib.edgelab.research_dataset's marketPriceAgeSeconds definition) -- "
            "'unavailable' means no prior observation existed, not a future-filled/negative age."
        ),
    }


# ── D. market_family_research ──────────────────────────────────────────

def market_family_research(rows):
    """
    Performance/coverage BY (canonicalMarketFamily, marketHorizon,
    threshold) -- spec section 10: "Do not lump fundamentally different
    markets together simply to raise N. Preserve exact thresholds."
    """
    groups = defaultdict(list)
    for r in rows:
        key = (r.get("canonicalMarketFamily"), r.get("marketHorizon"), r.get("threshold"), r.get("comparisonOperator"))
        groups[key].append(r)

    out = []
    for (family, horizon, threshold, operator), group_rows in groups.items():
        settled = _settled_rows(group_rows)
        n_observed = len(group_rows)
        n_settled = len(settled)
        independent_games = independent_unit_count(group_rows, key="gameId")
        model_coverage = sum(1 for r in group_rows if r.get("modelEvaluationAvailable")) / n_observed if n_observed else None
        priced = [r for r in settled if r.get("executableYesPrice") is not None]
        avg_price = sum(r["executableYesPrice"] for r in priced) / len(priced) if priced else None
        actual_yes_rate = sum(1 for r in priced if r["settlementResult"] == "YES") / len(priced) if priced else None
        calibration_error = (actual_yes_rate - avg_price) if (actual_yes_rate is not None and avg_price is not None) else None

        out.append({
            "canonicalMarketFamily": family,
            "marketHorizon": horizon,
            "threshold": threshold,
            "comparisonOperator": operator,
            "observedContracts": n_observed,
            "settledContracts": n_settled,
            "independentGames": independent_games,
            "modelCoverage": round(model_coverage, 4) if model_coverage is not None else None,
            "avgImpliedProbability": round(avg_price, 4) if avg_price is not None else None,
            "actualYesRate": round(actual_yes_rate, 4) if actual_yes_rate is not None else None,
            "calibrationError": round(calibration_error, 4) if calibration_error is not None else None,
            "sampleSize": sample_size_status(n_settled, independent_games),
        })
    return sorted(out, key=lambda r: (r["canonicalMarketFamily"] or "", r["marketHorizon"] or "", r["threshold"] if r["threshold"] is not None else 0))


# ── E. checkpoint_research ─────────────────────────────────────────────

def checkpoint_research(rows):
    """FIRST_DAILY/T-90/T-60/T-30/T-15/T-5/LINEUP_CONFIRMATION/CLOSING coverage and price comparison (spec section 6)."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("researchCheckpoint")].append(r)

    out = []
    for checkpoint, group_rows in groups.items():
        n = len(group_rows)
        independent_games = independent_unit_count(group_rows, key="gameId")
        unique_tickers = len({r["marketTicker"] for r in group_rows})
        minutes = [r["minutesToStart"] for r in group_rows if r.get("minutesToStart") is not None]
        prices = [r["executableYesPrice"] for r in group_rows if r.get("executableYesPrice") is not None]
        model_coverage = sum(1 for r in group_rows if r.get("modelEvaluationAvailable")) / n if n else None
        settled_coverage = sum(1 for r in group_rows if r.get("settlementStatus") == "SETTLED") / n if n else None

        out.append({
            "checkpoint": checkpoint,
            "n": n,
            "independentGames": independent_games,
            "uniqueMarketTickers": unique_tickers,
            "avgMinutesToStart": round(sum(minutes) / len(minutes), 2) if minutes else None,
            "avgExecutableYesPrice": round(sum(prices) / len(prices), 4) if prices else None,
            "modelEvaluationCoverage": round(model_coverage, 4) if model_coverage is not None else None,
            "settlementCoverage": round(settled_coverage, 4) if settled_coverage is not None else None,
        })

    def _sort_key(row):
        cp = row["checkpoint"]
        return STANDARDIZED_CHECKPOINT_ORDER.index(cp) if cp in STANDARDIZED_CHECKPOINT_ORDER else len(STANDARDIZED_CHECKPOINT_ORDER)

    return sorted(out, key=_sort_key)


# ── F. ladder_research ─────────────────────────────────────────────────

def ladder_research(rows):
    """
    Alternate-threshold ladder consistency, per (gameId, player-or-team,
    canonicalMarketFamily, researchCheckpoint) -- spec section 12. Uses
    the closing-quote price when available for each threshold rung,
    else any settled checkpoint price, never interpolated or invented.
    Detects monotonicity violations: for an OVER/AT_LEAST ("N+") family,
    probability must be non-increasing as the threshold rises; a rung
    that reads HIGHER than the previous (lower) threshold is a violation.
    """
    closing_rows = [r for r in rows if r.get("isClosingQuote") and r.get("executableYesPrice") is not None]

    ladders = defaultdict(dict)  # ladder_key -> {threshold: price}
    ladder_meta = {}
    for r in closing_rows:
        subject = r.get("player") or r.get("team") or r.get("gameId")
        ladder_key = (r.get("gameId"), subject, r.get("canonicalMarketFamily"), r.get("comparisonOperator"))
        threshold = r.get("threshold")
        if threshold is None:
            continue
        ladders[ladder_key][threshold] = r["executableYesPrice"]
        ladder_meta[ladder_key] = r

    out = []
    for ladder_key, rung_map in ladders.items():
        if len(rung_map) < 2:
            continue
        game_id, subject, family, operator = ladder_key
        rungs = sorted(rung_map.items())  # [(threshold, price), ...]
        violations = []
        for (t1, p1), (t2, p2) in zip(rungs, rungs[1:]):
            if operator in ("OVER", "AT_LEAST") and p2 > p1:
                violations.append({"lowerThreshold": t1, "lowerPrice": p1, "higherThreshold": t2, "higherPrice": p2})
            elif operator == "UNDER" and p2 < p1:
                violations.append({"lowerThreshold": t1, "lowerPrice": p1, "higherThreshold": t2, "higherPrice": p2})

        out.append({
            "gameId": game_id,
            "subject": subject,
            "canonicalMarketFamily": family,
            "comparisonOperator": operator,
            "rungs": [{"threshold": t, "closingYesPrice": p} for t, p in rungs],
            "rungCount": len(rungs),
            "monotonicityViolations": violations,
            "isMonotonic": len(violations) == 0,
        })
    return sorted(out, key=lambda r: (r["gameId"] or "", str(r["subject"] or ""), r["canonicalMarketFamily"] or ""))


# ── G. research_data_quality ────────────────────────────────────────────

def research_data_quality(rows, observations=None, settlements=None, evaluations=None):
    """Exact coverage/missingness (spec section 18) -- must be read before interpreting any edge result."""
    dates_with_observations = sorted({r["capturedAt"][:10] for r in rows if r.get("capturedAt")})
    dates_with_settlements = sorted({r["gameDate"] for r in _settled_rows(rows) if r.get("gameDate")})
    unique_games = independent_unit_count(rows, key="gameId")
    unique_tickers = len({r["marketTicker"] for r in rows})
    total_rows = len(rows)

    settlement_status_counts = defaultdict(int)
    for r in rows:
        settlement_status_counts[r.get("settlementStatus") or "NOT_SETTLED"] += 1

    lacking_price = sum(1 for r in rows if r.get("executableYesPrice") is None)
    lacking_model_eval = sum(1 for r in rows if not r.get("modelEvaluationAvailable"))
    lacking_temporally_valid_model_eval = sum(
        1 for r in rows if not r.get("modelEvaluationAvailable") and r.get("modelEvaluationUnavailableReason") is not None
    )

    family_settlement_coverage = defaultdict(lambda: {"observed": 0, "settled": 0})
    family_model_coverage = defaultdict(lambda: {"observed": 0, "modelEvaluated": 0})
    unknown_family_count = 0
    for r in rows:
        family = r.get("canonicalMarketFamily")
        if family in (None, "UNKNOWN", "UNMAPPED"):
            unknown_family_count += 1
        family_settlement_coverage[family]["observed"] += 1
        if r.get("settlementStatus") == "SETTLED":
            family_settlement_coverage[family]["settled"] += 1
        family_model_coverage[family]["observed"] += 1
        if r.get("modelEvaluationAvailable"):
            family_model_coverage[family]["modelEvaluated"] += 1

    checkpoint_coverage = defaultdict(int)
    for r in rows:
        checkpoint_coverage[r.get("researchCheckpoint")] += 1

    closing_coverage_tickers = len({r["marketTicker"] for r in rows if r.get("isClosingQuote")})

    return {
        "dateRange": {
            "earliest": dates_with_observations[0] if dates_with_observations else None,
            "latest": dates_with_observations[-1] if dates_with_observations else None,
        },
        "datesWithObservations": dates_with_observations,
        "datesWithSettlements": dates_with_settlements,
        "uniqueGames": unique_games,
        "uniqueMarketTickers": unique_tickers,
        "totalOpportunityRows": total_rows,
        "settlementStatusCounts": dict(settlement_status_counts),
        "rowsLackingExecutablePregamePrice": lacking_price,
        "rowsLackingModelEvaluation": lacking_model_eval,
        "rowsLackingTemporallyValidModelEvaluation": lacking_temporally_valid_model_eval,
        "familySettlementCoverage": {
            family: {**v, "coverageRate": round(v["settled"] / v["observed"], 4) if v["observed"] else None}
            for family, v in family_settlement_coverage.items()
        },
        "familyModelCoverage": {
            family: {**v, "coverageRate": round(v["modelEvaluated"] / v["observed"], 4) if v["observed"] else None}
            for family, v in family_model_coverage.items()
        },
        "checkpointCoverage": dict(checkpoint_coverage),
        "closingPriceCoverageMarketTickers": closing_coverage_tickers,
        "unknownOrUnclassifiedFamilyCount": unknown_family_count,
    }


# ── H. strategy_validation ─────────────────────────────────────────────

def strategy_validation(rows, split_ratios=None):
    """
    DEVELOPMENT/VALIDATION/HOLDOUT framework (spec section 15). Runs the
    IDENTICAL edge_backtest methodology independently on each partition
    -- this module never selects/tunes an edge threshold based on any
    partition's result; that decision belongs to a human researcher
    using DEVELOPMENT's output only, per this module's docstring.
    """
    dates = [r["gameDate"] for r in rows if r.get("gameDate")]
    split_map = chronological_split(dates, ratios=split_ratios)
    labeled = label_rows_with_split(rows, split_map)

    partitions = {}
    for label in (DEVELOPMENT, VALIDATION, HOLDOUT):
        partition_rows = [r for r in labeled if r.get("researchSplit") == label]
        partitions[label] = {
            "dateCount": len(split_map[label]),
            "rowCount": len(partition_rows),
            "edgeBacktest": edge_backtest(partition_rows),
        }

    return {
        "maturity": split_map["maturity"],
        "ratiosUsed": split_map["ratiosUsed"],
        "totalDates": split_map["totalDates"],
        "partitions": partitions,
        "note": (
            "FRAMEWORK ONLY -- no strategy has been optimized, tuned, or threshold-selected on any partition here, "
            "including HOLDOUT. Intended workflow: discover on DEVELOPMENT, test once on VALIDATION, freeze the rule, "
            "then evaluate on untouched HOLDOUT." if split_map["maturity"] != "USABLE" else
            "No strategy has been optimized, tuned, or threshold-selected on any partition here, including HOLDOUT."
        ),
    }


# ── I. snapshot_coverage ─────────────────────────────────────────────────
#
# EdgeLab Prospective Model Snapshots milestone: is the new intraday
# collection system (lib.edgelab.prospective_snapshot) actually doing
# its job? Reuses research_data_quality(rows) rather than recomputing
# its coverage numbers a second way -- only genuinely NEW breakdowns
# (by ModelEvaluation.checkpoint/artifactSource, by game, and the
# explicit PR #86 baseline comparison) are added here.

# The PR #86 baseline this milestone exists to improve on -- see
# docs/EDGELAB_RESEARCH_TRUSTWORTHINESS.md §7. A literal, historical
# number, never recomputed from current data (recomputing it here would
# defeat the point of a fixed baseline to compare against).
BASELINE_CAUSAL_OPPORTUNITY_ROWS = 264
BASELINE_TOTAL_OPPORTUNITY_ROWS = 75280
BASELINE_INDEPENDENT_GAMES = 68


# Skip reasons that mean "this game was never eligible for a model run
# at all this cycle" (spec section 8's "eligible games") -- distinct
# from SKIPPED_NO_CHECKPOINT_DUE, which means the game WAS eligible but
# simply had nothing due yet.
_INELIGIBLE_SKIP_REASONS = frozenset({
    "STARTED", "POSTPONED", "CANCELLED_OR_SUSPENDED", "MISSING_SCHEDULED_START",
})


def snapshot_coverage_report(rows, evaluations, games=None, research_runs=None):
    """
    `rows`: research_dataset.build_opportunity_rows() output (for
    causal-linkage-by-checkpoint/settled-row percentages and
    marketPriceAgeSeconds distribution, reusing research_data_quality()
    and market_price_staleness_report() rather than recomputing their
    numbers a second way). `evaluations`: raw ModelEvaluation records
    for the date range (for checkpoint/game/artifactSource/
    inputFreshnessNote breakdowns only available on the raw records, not
    on `rows`). `games`: raw Game records, optional (for "games
    scheduled" vs "games observed"). `research_runs`: raw
    ResearchRunMetadata records, optional (for eligible-game/lineup-
    poll/duplicate/skip/persistence-failure counts from
    lib.edgelab.prospective_snapshot's own run log -- see
    scripts/edgelab/run_prospective_snapshots.py).

    Never claims historical rows improved unless they genuinely did
    (spec section 13) -- `improvementOverBaseline` is computed directly
    from `rows`/`evaluations` passed in, not asserted.
    """
    data_quality = research_data_quality(rows)
    staleness = market_price_staleness_report(rows)
    games = games or []
    research_runs = research_runs or []
    prospective_runs = [r for r in research_runs if r.get("runType") == "PROSPECTIVE_SNAPSHOT"]

    prospective_evaluations = [e for e in evaluations if e.get("artifactSource") == "prospective_snapshot"]
    model_supported_statuses = ("EVALUATED", "PARTIAL_EVALUATION")

    games_scheduled = len({g["gameId"] for g in games if g.get("gameId")}) or None
    games_with_prospective_snapshot = len({e["gameId"] for e in prospective_evaluations if e.get("gameId")})

    unique_markets_model_supported = len({
        e["marketTicker"] for e in evaluations
        if e.get("marketTicker") and e.get("evaluationStatus") in model_supported_statuses
    })

    evaluations_by_checkpoint = defaultdict(int)
    for e in prospective_evaluations:
        if e.get("checkpoint"):
            evaluations_by_checkpoint[e["checkpoint"]] += 1

    input_freshness_counts = defaultdict(int)
    for e in prospective_evaluations:
        if e.get("inputFreshnessNote"):
            input_freshness_counts[e["inputFreshnessNote"]] += 1

    coverage_by_game = defaultdict(lambda: {"observed": 0, "modelEvaluated": 0})
    for r in rows:
        game_id = r.get("gameId")
        if not game_id:
            continue
        coverage_by_game[game_id]["observed"] += 1
        if r.get("modelEvaluationAvailable"):
            coverage_by_game[game_id]["modelEvaluated"] += 1
    model_coverage_by_game = {
        game_id: {**v, "coverageRate": round(v["modelEvaluated"] / v["observed"], 4) if v["observed"] else None}
        for game_id, v in coverage_by_game.items()
    }

    causal_rows = [r for r in rows if r.get("modelEvaluationAvailable")]
    causal_opportunity_row_count = len(causal_rows)
    causal_independent_games = independent_unit_count(causal_rows, key="gameId")

    market_linked_snapshots = sum(1 for r in causal_rows if r.get("marketObservationCapturedAtForModelEval") is not None)
    snapshots_lacking_earlier_observation = causal_opportunity_row_count - market_linked_snapshots

    minutes_to_start_by_checkpoint = defaultdict(list)
    for r in causal_rows:
        cp = r.get("modelEvaluationCheckpoint")
        mts = r.get("modelEvaluationMinutesToStart")
        if cp and mts is not None:
            minutes_to_start_by_checkpoint[cp].append(mts)

    def _distribution(values):
        if not values:
            return None
        values = sorted(values)
        n = len(values)
        return {
            "n": n, "min": values[0], "max": values[-1],
            "median": values[n // 2] if n % 2 else round((values[n // 2 - 1] + values[n // 2]) / 2.0, 2),
        }

    minutes_to_start_distribution_by_checkpoint = {
        cp: _distribution(values) for cp, values in minutes_to_start_by_checkpoint.items()
    }

    settled = _settled_rows(rows)
    settled_with_causal_linkage = [r for r in settled if r.get("modelEvaluationAvailable")]
    pct_settled_with_causal_linkage = (
        round(len(settled_with_causal_linkage) / len(settled), 4) if settled else None
    )

    missing_core_checkpoint_count = 0
    missing_checkpoint_reasons = defaultdict(int)
    captured_by_game = defaultdict(set)
    for e in prospective_evaluations:
        if e.get("gameId") and e.get("checkpoint"):
            captured_by_game[e["gameId"]].add(e["checkpoint"])
    for game_id, captured in captured_by_game.items():
        missing_core_checkpoint_count += len(set(CORE_CHECKPOINTS) - captured)

    eligible_games_seen = set()
    checkpoints_targeted_seen = set()
    late_run_count = sum(
        1 for r in causal_rows
        if r.get("checkpointTimingErrorSeconds") is not None and abs(r["checkpointTimingErrorSeconds"]) > 300
    )
    duplicate_count = 0
    skipped_started_game_count = 0
    lineup_poll_attempts = 0
    lineup_poll_successes = 0
    lineup_poll_failures = 0
    model_evaluations_written = 0
    persistence_failure_count = 0
    for run in prospective_runs:
        counts = run.get("counts") or {}
        duplicate_count += counts.get("modelEvaluationsSkippedDuplicate", 0) or 0
        skip_reasons = counts.get("gamesSkippedByReason") or {}
        skipped_started_game_count += skip_reasons.get("STARTED", 0) or 0
        for reason, n in skip_reasons.items():
            missing_checkpoint_reasons[reason] += n
        checkpoints_targeted_seen.update((counts.get("gamesEvaluatedByCheckpoint") or {}).keys())
        lineup_poll_attempts += counts.get("lineupPollAttempts", 0) or 0
        lineup_poll_successes += counts.get("lineupPollSuccesses", 0) or 0
        lineup_poll_failures += counts.get("lineupPollFailures", 0) or 0
        model_evaluations_written += counts.get("modelEvaluationsWritten", 0) or 0
        eligible_this_run = (counts.get("gamesConsidered", 0) or 0) - sum(
            n for reason, n in skip_reasons.items() if reason in _INELIGIBLE_SKIP_REASONS
        )
        eligible_games_seen.add((run.get("runId"), eligible_this_run))  # per-run eligible count, summed below
        if run.get("status") == "failed":
            persistence_failure_count += 1

    eligible_games_total = sum(n for _, n in eligible_games_seen)
    workflow_failure_count = persistence_failure_count + sum(len(r.get("errors") or []) for r in prospective_runs)

    improvement_multiple = (
        round(causal_opportunity_row_count / BASELINE_CAUSAL_OPPORTUNITY_ROWS, 2)
        if BASELINE_CAUSAL_OPPORTUNITY_ROWS else None
    )

    return {
        "gamesScheduled": games_scheduled,
        "gamesObserved": data_quality["uniqueGames"],
        "eligibleGames": eligible_games_total,
        "gamesWithProspectiveSnapshot": games_with_prospective_snapshot,
        "uniqueMarketsObserved": data_quality["uniqueMarketTickers"],
        "uniqueMarketsModelSupported": unique_markets_model_supported,
        "modelEvaluationsCapturedTotal": len(evaluations),
        "modelEvaluationsCapturedProspective": len(prospective_evaluations),
        "modelEvaluationsWritten": model_evaluations_written,
        "checkpointsTargeted": sorted(checkpoints_targeted_seen) or list(CORE_CHECKPOINTS),
        "checkpointsSuccessfullyCaptured": sorted(evaluations_by_checkpoint.keys()),
        "modelEvaluationsByCheckpoint": dict(evaluations_by_checkpoint),
        "modelCoverageByCanonicalFamily": data_quality["familyModelCoverage"],
        "modelCoverageByGame": model_coverage_by_game,
        "causalModelMarketPairCount": causal_opportunity_row_count,
        "causalModelMarketIndependentGames": causal_independent_games,
        "marketLinkedSnapshots": market_linked_snapshots,
        "snapshotsLackingEarlierMarketObservation": snapshots_lacking_earlier_observation,
        "medianMarketPriceAgeSeconds": staleness["medianMarketPriceAgeSeconds"],
        "p90MarketPriceAgeSeconds": staleness["p90MarketPriceAgeSeconds"],
        "marketPriceAgeBucketCounts": staleness["byBucket"],
        "evaluationsByInputFreshnessNote": dict(input_freshness_counts),
        "minutesToStartDistributionByCheckpoint": minutes_to_start_distribution_by_checkpoint,
        "pctSettledOpportunityRowsWithCausalLinkage": pct_settled_with_causal_linkage,
        "missingCoreCheckpointCount": missing_core_checkpoint_count,
        "missingCheckpointReasons": dict(missing_checkpoint_reasons),
        "lineupConfirmationAttempts": lineup_poll_attempts,
        "lineupConfirmationSuccesses": lineup_poll_successes,
        "lineupConfirmationApiFailures": lineup_poll_failures,
        "lateRunCount": late_run_count,
        "duplicateOrIdempotencyCount": duplicate_count,
        "skippedStartedGameCount": skipped_started_game_count,
        "persistenceFailureCount": persistence_failure_count,
        "workflowFailureCount": workflow_failure_count,
        "improvementOverPR86Baseline": {
            "baselineCausalOpportunityRows": BASELINE_CAUSAL_OPPORTUNITY_ROWS,
            "baselineTotalOpportunityRows": BASELINE_TOTAL_OPPORTUNITY_ROWS,
            "baselineIndependentGames": BASELINE_INDEPENDENT_GAMES,
            "currentCausalOpportunityRows": causal_opportunity_row_count,
            "currentTotalOpportunityRows": data_quality["totalOpportunityRows"],
            "currentIndependentGames": causal_independent_games,
            "improvementMultiple": improvement_multiple,
            "note": (
                "improvementMultiple compares CURRENT causally-valid opportunity rows against the PR #86 "
                "historical baseline (264 rows / 68 games) -- a ratio > 1 means genuinely more causal "
                "model-at-checkpoint coverage exists now, never asserted without being computed from the "
                "actual rows/evaluations passed to this report. This NEVER retroactively improves the "
                "historical baseline itself -- it is a fixed reference point, not recomputed."
            ),
        },
    }


# ── Human-readable summary ──────────────────────────────────────────────

def render_summary_markdown(data_quality, market_cal, model_cal, edge_bt, strategy_val):
    lines = [
        "# EdgeLab Research Trustworthiness Summary",
        "",
        f"Date range: {data_quality['dateRange']['earliest']} to {data_quality['dateRange']['latest']}",
        f"Unique games: {data_quality['uniqueGames']} | Unique market tickers: {data_quality['uniqueMarketTickers']} | Opportunity rows: {data_quality['totalOpportunityRows']}",
        "",
        "## Market calibration (full universe, YES side)",
    ]
    overall_market = market_cal["overall"][0] if market_cal["overall"] else None
    if overall_market:
        lines.append(
            f"- n={overall_market['n']}, avgImpliedProbability={overall_market['avgImpliedProbability']}, "
            f"actualYesRate={overall_market['actualYesRate']}, calibrationError={overall_market['calibrationError']}, "
            f"status={overall_market['sampleSize']['status']}"
        )
    else:
        lines.append("- No settled, priced markets available.")

    lines.append("")
    lines.append("## Model calibration (causally-valid rows only)")
    overall_model = model_cal["overall"][0] if model_cal["overall"] else None
    if overall_model:
        lines.append(
            f"- n={overall_model['n']}, avgModelProbability={overall_model['avgModelProbability']}, "
            f"actualWinRate={overall_model['actualWinRate']}, calibrationError={overall_model['calibrationError']}, "
            f"status={overall_model['sampleSize']['status']}"
        )
    else:
        lines.append("- No rows with a causally-valid model evaluation and settled outcome.")

    lines.append("")
    lines.append("## Edge backtest (top buckets by n)")
    for bucket in sorted(edge_bt, key=lambda r: r["n"], reverse=True)[:5]:
        lines.append(
            f"- edge {bucket['edgeBucket']}%: n={bucket['n']} ({bucket['independentGames']} games), "
            f"winRate={bucket['actualWinRate']}, roi={bucket['roi']}, status={bucket['sampleSize']['status']}"
        )

    lines.append("")
    lines.append("## Strategy validation framework")
    lines.append(f"- maturity: {strategy_val['maturity']}")
    lines.append(f"- {strategy_val['note']}")

    lines.append("")
    lines.append("_All findings above are exploratory/descriptive. None constitute a validated betting edge until they survive out-of-sample HOLDOUT evaluation on a mature (30+ trading date) corpus. See research_data_quality for coverage caveats._")
    return "\n".join(lines)
