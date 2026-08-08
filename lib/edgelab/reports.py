"""
lib/edgelab/reports.py
=========================
Daily research report (Phase 1 section L): aggregates the day's already-
written EdgeLab partitions into one summary — no new collection logic,
purely a read-and-aggregate step. Also emits a machine-readable
calibration export (model probability vs. settled outcome, for every
market that has both) for future calibration work; Phase 1 does not
build the calibration model itself.
"""

from collections import Counter

from lib.edgelab import bets as bets_lib
from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION
from lib.edgelab.calibration import calibration_status

_RECOMMENDED_LIKE_STATUSES = {"RECOMMENDED", "BET_PLACED", "RECOMMENDED_NOT_BET", "WATCH"}

# Tier/confidence calibration & canonical rolling reporting mission:
# default window for build_rolling_window_report() -- "rolling last 30
# settled bets" per the reporting requirement. Never padded when fewer
# than this many canonical settled bets exist yet (see
# build_rolling_window_report's docstring) -- an early, thin canonical
# era reports its true, small windowActual rather than implying a full
# 30-bet sample.
ROLLING_WINDOW_SIZE = 30


def build_daily_report(date, games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs):
    """
    All arguments are already-loaded lists of records for `date` (the
    caller is responsible for reading the right partitions/filtering
    bets to this date) -- this function only aggregates, never reads
    files itself, so it's directly unit-testable with plain lists.
    """
    family_counts = Counter(o.get("marketFamily") for o in observations)
    pass_counts = Counter(r["status"] for r in recommendations if r["status"].startswith("PASS_"))
    recommended_count = sum(1 for r in recommendations if r["status"] in _RECOMMENDED_LIKE_STATUSES)
    not_evaluated_count = sum(1 for r in recommendations if r["status"] == "NOT_EVALUATED")
    insufficient_support_count = sum(1 for r in recommendations if r["status"] == "INSUFFICIENT_MODEL_SUPPORT")
    closing_quotes_captured = sum(1 for q in clv_quotes if q.get("isClosingQuote"))

    clv_values = [b["clv"] for b in bets if b.get("clv") is not None]
    clv_summary = {
        "betsTotal": len(bets),
        "betsWithClv": len(clv_values),
        "avgClvCents": round(sum(clv_values) / len(clv_values), 2) if clv_values else None,
        "positiveClvCount": sum(1 for v in clv_values if v > 0),
        "negativeClvCount": sum(1 for v in clv_values if v < 0),
    }

    settlement_completion = {
        "marketsObserved": len(markets),
        "settled": sum(1 for s in settlements if s["settlementStatus"] == "SETTLED"),
        "void": sum(1 for s in settlements if s["settlementStatus"] == "VOID"),
        "unresolved": sum(1 for s in settlements if s["settlementStatus"] == "SETTLEMENT_UNRESOLVED"),
        "notYetAttempted": max(0, len(markets) - len(settlements)),
    }
    unresolved_reasons = Counter(
        s.get("unavailableReason") for s in settlements if s["settlementStatus"] == "SETTLEMENT_UNRESOLVED"
    )

    api_errors = []
    new_series_warnings = []
    data_quality_warnings = []
    for run in research_runs:
        api_errors.extend(run.get("errors") or [])
        for w in run.get("warnings") or []:
            (new_series_warnings if "NEW_UNCLASSIFIED_MLB_SERIES" in w else data_quality_warnings).append(w)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "date": date,
        "generatedAt": ids.utc_now_iso(),
        # A Game row marked supersededBy (lib.edgelab.market_universe.
        # mark_superseded_game_identities) is a duplicate identity for a
        # game already counted under its canonical row -- never double-
        # counted here just because ingestion once created two rows for
        # the same real-world game (see the 2026-08-04 30-Game-row case).
        "gamesObserved": sum(1 for g in games if not g.get("supersededBy")),
        "marketsObserved": len(markets),
        "quotesCaptured": len(observations),
        "marketFamilyCounts": dict(family_counts),
        "placedBets": len(bets),
        "recommendedBets": recommended_count,
        "passCountsByReason": dict(pass_counts),
        "notEvaluatedCount": not_evaluated_count,
        "insufficientModelSupportCount": insufficient_support_count,
        "closingQuotesCaptured": closing_quotes_captured,
        "clvSummary": clv_summary,
        "settlementCompletion": settlement_completion,
        "settlementUnresolvedReasons": dict(unresolved_reasons),
        "apiErrors": api_errors,
        "newUnclassifiedSeriesWarnings": new_series_warnings,
        "dataQualityWarnings": data_quality_warnings,
    }


def render_markdown(report):
    lines = [
        f"# EdgeLab Daily Research Report — {report['date']}",
        "",
        f"_Generated {report['generatedAt']}_",
        "",
        "## Coverage",
        f"- Games observed: {report['gamesObserved']}",
        f"- Markets observed: {report['marketsObserved']}",
        f"- Quotes captured: {report['quotesCaptured']}",
        f"- Closing quotes captured: {report['closingQuotesCaptured']}",
        "",
        "## Market family counts",
    ]
    for family, count in sorted(report["marketFamilyCounts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {family}: {count}")

    lines += [
        "",
        "## Decisions",
        f"- Placed bets: {report['placedBets']}",
        f"- Recommended (incl. watch/bet-placed): {report['recommendedBets']}",
        f"- Not evaluated: {report['notEvaluatedCount']}",
        f"- Insufficient model support: {report['insufficientModelSupportCount']}",
        "",
        "### Pass counts by reason",
    ]
    if report["passCountsByReason"]:
        for reason, count in sorted(report["passCountsByReason"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- (none)")

    clv = report["clvSummary"]
    lines += [
        "",
        "## CLV summary",
        f"- Bets with CLV computed: {clv['betsWithClv']} / {clv['betsTotal']}",
        f"- Average CLV (cents): {clv['avgClvCents']}",
        f"- Positive / negative CLV: {clv['positiveClvCount']} / {clv['negativeClvCount']}",
    ]

    sc = report["settlementCompletion"]
    lines += [
        "",
        "## Settlement completion",
        f"- Settled: {sc['settled']}",
        f"- Void: {sc['void']}",
        f"- Unresolved: {sc['unresolved']}",
        f"- Not yet attempted: {sc['notYetAttempted']}",
    ]
    if report["settlementUnresolvedReasons"]:
        lines.append("")
        lines.append("### Unresolved reasons")
        for reason, count in sorted(report["settlementUnresolvedReasons"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {reason}: {count}")

    lines += ["", "## Warnings"]
    lines.append(f"- API errors: {len(report['apiErrors'])}")
    lines.append(f"- New/unclassified series: {len(report['newUnclassifiedSeriesWarnings'])}")
    lines.append(f"- Data-quality warnings: {len(report['dataQualityWarnings'])}")
    for w in report["newUnclassifiedSeriesWarnings"]:
        lines.append(f"  - {w}")

    return "\n".join(lines) + "\n"


def _postmortem_bet_row(bet):
    """
    One line item for the postmortem's bet-level detail. grossReturn/
    netProfitLoss reflect realized economics via
    lib.edgelab.bets.realized_bet_economics -- a manually confirmed real
    receipt (lib.edgelab.bets.confirm_realized_return), when present,
    takes priority over this system's own derived binary WIN/LOSS/PUSH/
    VOID economics; result/status (objective settlement outcome) are
    always the plain ledger values either way, never overwritten by a
    confirmed receipt. Computed here for reporting only, never stored
    back onto the ledger row itself.
    """
    stake = bet.get("stake") or 0
    gross_return, net_pl = (None, None)
    if bet.get("status") == "settled":
        gross_return, net_pl = bets_lib.realized_bet_economics(bet)
    return {
        "betId": bet.get("betId"),
        "marketTicker": bet.get("marketTicker"),
        "marketFamily": bet.get("marketFamily"),
        "selection": bet.get("selection"),
        "side": bet.get("side"),
        "stake": stake,
        "entryPrice": bet.get("entryPrice"),
        "status": bet.get("status"),
        "result": bet.get("result"),
        "grossReturn": gross_return,
        "netProfitLoss": net_pl,
        "confirmedReceipt": bet.get("confirmedReceiptNetProfitLoss") is not None,
        "clv": bet.get("clv"),
        "source": bet.get("source"),
        "entryMethod": bet.get("entryMethod"),
        "modelSupported": bet.get("modelSupported"),
        "recommendationId": bet.get("recommendationId"),
        "snapshotId": bet.get("snapshotId"),
        "replayRunId": bet.get("replayRunId"),
    }


def _bucket_stats(bets):
    settled_bets = [b for b in bets if b.get("status") == "settled"]
    net_pls = [bets_lib.realized_bet_economics(b)[1] for b in settled_bets]
    return {
        "count": len(bets),
        "settledCount": len(settled_bets),
        "stake": round(sum(b.get("stake") or 0 for b in bets), 2),
        "netProfitLoss": round(sum(n or 0 for n in net_pls), 2),
        "wins": sum(1 for b in settled_bets if b.get("result") == "WIN"),
        "losses": sum(1 for b in settled_bets if b.get("result") == "LOSS"),
    }


def build_postmortem(date, bets, bankroll_summary=None):
    """
    Daily postmortem (Canonical Placed-Bet Ledger milestone, requirement
    14): built EXCLUSIVELY from the canonical placed-bet ledger for
    `date` -- never from the recommendation list or chat memory. A
    recommendation the user never confirmed placing is never counted
    here as a bet.

    `bets` is every PlacedBet record already loaded by the caller (the
    caller is responsible for reading data/edgelab/bets/bets.jsonl --
    this function only aggregates, same convention as build_daily_report).
    Filters to `date` (by gameDate, falling back to entryTimestamp's date)
    and excludes CANCELLED rows and PAPER/REAL_PROBE tracking (paper
    trades never count toward real P&L/ROI -- broken out separately isn't
    needed since Phase 1 has no paper-specific reporting requirement, but
    they are never silently included in the real totals either).
    """
    day_bets = [
        b for b in bets
        if (b.get("gameDate") or (b.get("entryTimestamp") or "")[:10]) == date
        and (b.get("recordStatus") or "ACTIVE") != "CANCELLED"
    ]
    real_bets = [b for b in day_bets if b.get("trackingType") in (None, "REAL")]
    settled_bets = [b for b in real_bets if b.get("status") == "settled"]
    pending_bets = [b for b in real_bets if b.get("status") == "pending"]
    void_bets = [b for b in real_bets if b.get("status") == "void"]

    total_risked = round(sum(b.get("stake") or 0 for b in real_bets), 2)
    total_risked_settled = round(sum(b.get("stake") or 0 for b in settled_bets), 2)
    # Prefers a manually confirmed real receipt over derived binary
    # settlement economics when present -- see
    # lib.edgelab.bets.realized_bet_economics/confirm_realized_return.
    _economics = [bets_lib.realized_bet_economics(b) for b in settled_bets]
    total_net_pl = round(sum(net for _gross, net in _economics if net is not None), 2)
    total_returned = round(sum(gross for gross, _net in _economics if gross is not None), 2)
    roi_pct = round((total_net_pl / total_risked_settled) * 100, 2) if total_risked_settled else None

    clv_values = [b["clv"] for b in real_bets if b.get("clv") is not None]
    avg_clv = round(sum(clv_values) / len(clv_values), 2) if clv_values else None

    family_stats = {}
    for b in settled_bets:
        family_stats.setdefault(b.get("marketFamily") or "UNKNOWN", []).append(b)
    performance_by_family = {fam: _bucket_stats(fam_bets) for fam, fam_bets in family_stats.items()}

    model_supported_ids = {
        b["betId"] for b in real_bets if b.get("modelSupported") or b.get("modelEvaluationId")
    }
    model_supported_bets = [b for b in real_bets if b["betId"] in model_supported_ids]
    manual_only_bets = [b for b in real_bets if b["betId"] not in model_supported_ids]
    recommended_bets = [b for b in real_bets if b.get("recommendationId")]
    non_recommended_bets = [b for b in real_bets if not b.get("recommendationId")]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "date": date,
        "generatedAt": ids.utc_now_iso(),
        "betsPlaced": len(real_bets),
        "bets": [_postmortem_bet_row(b) for b in real_bets],
        "dailyRecord": {
            "wins": sum(1 for b in settled_bets if b.get("result") == "WIN"),
            "losses": sum(1 for b in settled_bets if b.get("result") == "LOSS"),
            "pushes": sum(1 for b in settled_bets if b.get("result") == "PUSH"),
            "voids": len(void_bets),
            "pending": len(pending_bets),
        },
        "totalRisked": total_risked,
        "totalRiskedSettled": total_risked_settled,
        "totalReturned": total_returned,
        "totalNetProfitLoss": total_net_pl,
        "roiPct": roi_pct,
        "avgClvCents": avg_clv,
        "performanceByMarketFamily": performance_by_family,
        "modelSupportedVsManual": {
            "modelSupported": _bucket_stats(model_supported_bets),
            "manual": _bucket_stats(manual_only_bets),
        },
        "recommendedVsNonRecommended": {
            "recommended": _bucket_stats(recommended_bets),
            "nonRecommended": _bucket_stats(non_recommended_bets),
        },
        "snapshotLinkedCount": sum(1 for b in real_bets if b.get("snapshotId")),
        "replayLinkedCount": sum(1 for b in real_bets if b.get("replayRunId")),
        "unresolvedCount": len(pending_bets),
        "unresolvedBetIds": [b["betId"] for b in pending_bets],
        "bankroll": bankroll_summary,
    }


def render_postmortem_markdown(report):
    lines = [
        f"# Daily Postmortem — {report['date']}",
        "",
        f"_Generated {report['generatedAt']}_",
        "",
        f"- Bets placed: {report['betsPlaced']}",
        f"- Record: {report['dailyRecord']['wins']}-{report['dailyRecord']['losses']}"
        f"-{report['dailyRecord']['pushes']} (pushes), {report['dailyRecord']['voids']} void, "
        f"{report['dailyRecord']['pending']} still pending",
        f"- Total risked: ${report['totalRisked']} (${report['totalRiskedSettled']} settled)",
        f"- Total returned: ${report['totalReturned']}",
        f"- Net P/L: ${report['totalNetProfitLoss']}",
        f"- ROI: {report['roiPct']}%" if report["roiPct"] is not None else "- ROI: n/a (nothing settled yet)",
        f"- Avg CLV (cents): {report['avgClvCents']}",
        f"- Snapshot-linked: {report['snapshotLinkedCount']} / Replay-linked: {report['replayLinkedCount']}",
        f"- Unresolved (still pending): {report['unresolvedCount']}",
        "",
        "## Performance by market family",
    ]
    if report["performanceByMarketFamily"]:
        for fam, stats in sorted(report["performanceByMarketFamily"].items(), key=lambda kv: -kv[1]["stake"]):
            lines.append(f"- {fam}: {stats['wins']}-{stats['losses']}, stake ${stats['stake']}, P/L ${stats['netProfitLoss']}")
    else:
        lines.append("- (none settled)")

    ms = report["modelSupportedVsManual"]
    lines += [
        "",
        "## Model-supported vs. manual",
        f"- Model-supported: {ms['modelSupported']['count']} bets, P/L ${ms['modelSupported']['netProfitLoss']}",
        f"- Manual (no model support): {ms['manual']['count']} bets, P/L ${ms['manual']['netProfitLoss']}",
    ]

    rv = report["recommendedVsNonRecommended"]
    lines += [
        "",
        "## Recommended vs. non-recommended",
        f"- Recommended: {rv['recommended']['count']} bets, P/L ${rv['recommended']['netProfitLoss']}",
        f"- Non-recommended: {rv['nonRecommended']['count']} bets, P/L ${rv['nonRecommended']['netProfitLoss']}",
    ]

    if report.get("bankroll"):
        b = report["bankroll"]
        lines += [
            "",
            "## Bankroll",
            f"- Available: ${b['availableBankroll']} / Settled: ${b['settledBankroll']} / Exposure: ${b['totalExposure']}",
        ]
        if b.get("userReportedBalance") is not None:
            lines.append(f"- User-reported balance: ${b['userReportedBalance']} (delta ${b['userReportedDelta']})")

    if report["unresolvedBetIds"]:
        lines += ["", "## Unresolved bets (still pending)"]
        for bid in report["unresolvedBetIds"]:
            lines.append(f"- {bid}")

    return "\n".join(lines) + "\n"


def build_canonical_era_summary(bets, bankroll_summary=None, *, include_legacy=False):
    """
    Cumulative canonical-era performance summary -- the same aggregate
    fields as a single day's build_postmortem (win/loss record, total
    risked/returned, ROI, avg CLV, performance by market family), but
    across every canonical-era bet rather than one date. This is the
    "cumulative postmortem" / "canonical-era analytics" view: by default
    it excludes every bet before lib.edgelab.canonical_era.
    CANONICAL_ERA_START_DATE from every total below, exactly like
    build_postmortem excludes CANCELLED/PAPER/REAL_PROBE. Pass
    include_legacy=True only for an explicit full-history view -- never
    the default for an "official" report -- in which case the output's
    own `legacyIncluded: true` flag makes that plain to whoever reads it,
    so a legacy-inclusive view is never mistaken for the official one.

    Never reads or writes any file itself -- `bets` is already-loaded,
    same convention as build_postmortem/build_daily_report.
    """
    from lib.edgelab import canonical_era

    scoped_bets = bets if include_legacy else canonical_era.canonical_era_bets(bets)
    active_bets = [b for b in scoped_bets if (b.get("recordStatus") or "ACTIVE") != "CANCELLED"]
    real_bets = [b for b in active_bets if b.get("trackingType") in (None, "REAL")]
    settled_bets = [b for b in real_bets if b.get("status") == "settled"]
    pending_bets = [b for b in real_bets if b.get("status") == "pending"]
    void_bets = [b for b in real_bets if b.get("status") == "void"]

    total_risked = round(sum(b.get("stake") or 0 for b in real_bets), 2)
    total_risked_settled = round(sum(b.get("stake") or 0 for b in settled_bets), 2)
    # Prefers a manually confirmed real receipt over derived binary
    # settlement economics when present -- see
    # lib.edgelab.bets.realized_bet_economics/confirm_realized_return.
    _economics = [bets_lib.realized_bet_economics(b) for b in settled_bets]
    total_net_pl = round(sum(net for _gross, net in _economics if net is not None), 2)
    total_returned = round(sum(gross for gross, _net in _economics if gross is not None), 2)
    roi_pct = round((total_net_pl / total_risked_settled) * 100, 2) if total_risked_settled else None

    clv_values = [b["clv"] for b in real_bets if b.get("clv") is not None]
    avg_clv = round(sum(clv_values) / len(clv_values), 2) if clv_values else None

    family_stats = {}
    for b in settled_bets:
        family_stats.setdefault(b.get("marketFamily") or "UNKNOWN", []).append(b)
    performance_by_family = {fam: _bucket_stats(fam_bets) for fam, fam_bets in family_stats.items()}

    return {
        "schemaVersion": SCHEMA_VERSION,
        "canonicalEraStartDate": canonical_era.CANONICAL_ERA_START_DATE,
        "legacyIncluded": include_legacy,
        "generatedAt": ids.utc_now_iso(),
        "betsPlaced": len(real_bets),
        "dailyRecord": {
            "wins": sum(1 for b in settled_bets if b.get("result") == "WIN"),
            "losses": sum(1 for b in settled_bets if b.get("result") == "LOSS"),
            "pushes": sum(1 for b in settled_bets if b.get("result") == "PUSH"),
            "voids": len(void_bets),
            "pending": len(pending_bets),
        },
        "totalRisked": total_risked,
        "totalRiskedSettled": total_risked_settled,
        "totalReturned": total_returned,
        "totalNetProfitLoss": total_net_pl,
        "roiPct": roi_pct,
        "avgClvCents": avg_clv,
        "performanceByMarketFamily": performance_by_family,
        "bankroll": bankroll_summary,
    }


def build_calibration_rows(recommendations, settlements):
    """
    One row per market that has BOTH a model fair probability and a
    settled YES/NO result -- the minimum needed for a future calibration
    pass. Markets missing either side are excluded, never filled in.
    """
    settlement_by_ticker = {s["marketTicker"]: s for s in settlements if s["settlementStatus"] == "SETTLED"}
    rows = []
    for r in recommendations:
        if r.get("modelFairProbability") is None:
            continue
        settlement = settlement_by_ticker.get(r.get("marketTicker"))
        if settlement is None or settlement.get("result") not in ("YES", "NO"):
            continue
        rows.append({
            "marketTicker": r["marketTicker"],
            "marketFamily": r.get("marketFamily"),
            "modelFairProbability": r["modelFairProbability"],
            "marketImpliedProbability": r.get("marketImpliedProbability"),
            "result": settlement["result"],
            "won": settlement["result"] == "YES",
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Tier/confidence calibration & canonical rolling performance reporting
# ══════════════════════════════════════════════════════════════════════════════

def _fair_probability_for_calibration(bet):
    """
    The single 'valid fair probability' to calibrate a bet's outcome
    against: the model's own estimate when a real model backs this bet,
    else the human's own manual estimate for a purely manual bet --
    never fabricated, never blended/averaged between the two. Returns
    None when neither is on record, so the caller can exclude the bet
    from calibration entirely rather than guessing.
    """
    if bet.get("modelFairProbability") is not None:
        return bet["modelFairProbability"]
    return bet.get("manualFairProbability")


def _rolling_window_order_key(bet):
    """
    Most-recently-settled-first ordering key for the rolling window.
    updatedAt is bumped whenever a bet is settled (lib.edgelab.settlement)
    or its receipt is confirmed (lib.edgelab.bets.confirm_realized_return)
    -- the closest thing this schema stores to a real settlement
    timestamp -- so it is preferred; entryTimestamp/gameDate (placement
    order) is only a fallback for an older row with no updatedAt on
    record, never the primary signal for a "most recently settled" sort.
    """
    return bet.get("updatedAt") or bet.get("entryTimestamp") or bet.get("gameDate") or ""


def _tier_bucket_stats(bets):
    """
    Record/risked/return/P&L/ROI for one bucket of bets, using confirmed-
    receipt-aware economics (lib.edgelab.bets.realized_bet_economics --
    a manually confirmed real receipt takes priority over this system's
    own derived binary settlement economics, exactly like every other
    bucket helper in this module). winRate's denominator is WIN+LOSS
    only (pushes/voids have no win/loss to rate, matching
    lib.edgelab.calibration's "decided bets" convention) -- sampleStatus
    reuses that same module's three-tier INSUFFICIENT_SAMPLE/
    DESCRIPTIVE_ONLY/CALIBRATED gate so this report and the calibration
    engine never disagree about what counts as "enough".
    """
    settled_bets = [b for b in bets if b.get("status") == "settled"]
    economics = [bets_lib.realized_bet_economics(b) for b in settled_bets]
    stake = round(sum(b.get("stake") or 0 for b in bets), 2)
    gross_return = round(sum(g or 0 for g, _n in economics if g is not None), 2)
    net_pl = round(sum(n or 0 for _g, n in economics if n is not None), 2)
    wins = sum(1 for b in settled_bets if b.get("result") == "WIN")
    losses = sum(1 for b in settled_bets if b.get("result") == "LOSS")
    pushes = sum(1 for b in settled_bets if b.get("result") == "PUSH")
    decided = wins + losses
    return {
        "count": len(bets),
        "settledCount": len(settled_bets),
        "record": {"wins": wins, "losses": losses, "pushes": pushes},
        "winRate": round(wins / decided, 4) if decided else None,
        "stakeRisked": stake,
        "realizedReturn": gross_return,
        "netProfitLoss": net_pl,
        "roiPct": round((net_pl / stake) * 100, 2) if stake else None,
        "sampleStatus": calibration_status(decided),
    }


def build_rolling_window_report(bets, window_size=ROLLING_WINDOW_SIZE, *, include_legacy=False):
    """
    Rolling-window canonical performance report: the most recent
    `window_size` SETTLED canonical wagers -- never more, and never
    padded with fewer than that many available; a thin early canonical
    era honestly reports its true, small windowActual (see
    windowSampleStatus) rather than implying significance from a tiny
    sample.

    Built EXCLUSIVELY from `bets` (the already-loaded canonical placed-
    bet ledger the caller read from data/edgelab/bets/bets.jsonl --
    same convention as build_postmortem/build_canonical_era_summary) --
    never the recommendation list, never a legacy pre-canonical-ledger
    row, never chat memory. By default (include_legacy=False, matching
    lib.edgelab.canonical_era's own default) only canonical-era bets are
    considered; CANCELLED rows and PAPER/REAL_PROBE tracking are always
    excluded, exactly like every other real-performance view in this
    module.

    Tier grouping uses PlacedBet.confidence EXACTLY as recorded (HIGH/
    MEDIUM/PAPER/LOW -- informally "Tier A"/"Tier B"/"Tier C" in human
    conversation, see docs on confidence_from_edge's tier-cap logic in
    scripts/build_market_ledger.py). A bet with no confidence on record
    gets its own explicit "UNRECORDED" bucket -- this function never
    invents, infers, or backfills a historical tier for it.

    Calibration compares _fair_probability_for_calibration(bet) (model
    or manual, never both) against the settled WIN/LOSS outcome, for
    exactly the bets in this window that have BOTH -- a bet with no fair
    probability on record contributes to every other section of this
    report but is silently excluded from the calibration section only,
    never assigned a fabricated 50% or a guess.

    CLV coverage counts a bet as "covered" only when PlacedBet.clv is
    actually on record. lib.edgelab.clv.compute_clv_for_bet only ever
    sets that field when a real, valid pre-suspension/pre-start closing
    quote existed for the bet's exact ticker -- every other case
    (NO_VALID_PRE_CLOSE_QUOTE, ENTRY_PRICE_MISSING, etc.) leaves it null
    -- so "clv is not None" already and only ever means "a legitimate
    pregame close exists for this bet", nothing further to check here.
    """
    from lib.edgelab import canonical_era

    active_bets = [b for b in bets if (b.get("recordStatus") or "ACTIVE") != "CANCELLED"]
    real_bets = [b for b in active_bets if b.get("trackingType") in (None, "REAL")]
    scoped_bets = real_bets if include_legacy else canonical_era.canonical_era_bets(real_bets)
    settled_bets = [b for b in scoped_bets if b.get("status") == "settled"]

    window = sorted(settled_bets, key=_rolling_window_order_key, reverse=True)[:window_size]
    window_actual = len(window)

    tier_groups = {}
    for b in window:
        tier_groups.setdefault(b.get("confidence") or "UNRECORDED", []).append(b)
    tier_breakdown = {tier: _tier_bucket_stats(bs) for tier, bs in tier_groups.items()}

    family_groups = {}
    for b in window:
        family_groups.setdefault(b.get("marketFamily") or "UNKNOWN", []).append(b)
    family_breakdown = {fam: _tier_bucket_stats(bs) for fam, bs in family_groups.items()}

    calibration_rows = []
    for b in window:
        if b.get("result") not in ("WIN", "LOSS"):
            continue
        fair_prob = _fair_probability_for_calibration(b)
        if fair_prob is None:
            continue
        calibration_rows.append({
            "betId": b.get("betId"),
            "marketFamily": b.get("marketFamily"),
            "predictedProbability": fair_prob,
            "probabilitySource": "MODEL" if b.get("modelFairProbability") is not None else "MANUAL",
            "won": b.get("result") == "WIN",
        })
    calibration_n = len(calibration_rows)
    avg_predicted = (
        round(sum(r["predictedProbability"] for r in calibration_rows) / calibration_n, 4)
        if calibration_n else None
    )
    actual_win_rate = (
        round(sum(1 for r in calibration_rows if r["won"]) / calibration_n, 4)
        if calibration_n else None
    )
    calibration_error = (
        round(actual_win_rate - avg_predicted, 4)
        if calibration_n else None
    )

    clv_values = [b["clv"] for b in window if b.get("clv") is not None]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": ids.utc_now_iso(),
        "windowRequested": window_size,
        "windowActual": window_actual,
        "windowSampleStatus": calibration_status(window_actual),
        "canonicalEraStartDate": canonical_era.CANONICAL_ERA_START_DATE,
        "legacyIncluded": include_legacy,
        "overall": _tier_bucket_stats(window),
        "tierBreakdown": tier_breakdown,
        "marketFamilyBreakdown": family_breakdown,
        "calibration": {
            "n": calibration_n,
            "sampleStatus": calibration_status(calibration_n),
            "avgPredictedProbability": avg_predicted,
            "actualWinRate": actual_win_rate,
            "calibrationError": calibration_error,
            "rows": calibration_rows,
        },
        "clvCoverage": {
            "windowSize": window_actual,
            "withClv": len(clv_values),
            "withoutClv": window_actual - len(clv_values),
            "coveragePct": round(len(clv_values) / window_actual * 100, 1) if window_actual else None,
            "avgClvCents": round(sum(clv_values) / len(clv_values), 2) if clv_values else None,
        },
        "oldestBetIdInWindow": window[-1].get("betId") if window else None,
        "newestBetIdInWindow": window[0].get("betId") if window else None,
    }


def render_rolling_window_markdown(report):
    lines = [
        f"# Rolling Last-{report['windowRequested']} Canonical Performance Report",
        "",
        f"_Generated {report['generatedAt']}_",
        "",
        f"Window: {report['windowActual']} / {report['windowRequested']} settled canonical bets "
        f"({report['windowSampleStatus']}), canonical era starting {report['canonicalEraStartDate']}"
        + ("" if not report["legacyIncluded"] else ", legacy included"),
        "",
    ]

    o = report["overall"]
    lines += [
        "## Overall",
        f"- Record: {o['record']['wins']}-{o['record']['losses']}-{o['record']['pushes']} (W-L-Push)",
        f"- Win rate: {o['winRate']}" if o["winRate"] is not None else "- Win rate: n/a",
        f"- Stake risked: ${o['stakeRisked']}",
        f"- Realized return: ${o['realizedReturn']}",
        f"- Net P/L: ${o['netProfitLoss']}",
        f"- ROI: {o['roiPct']}%" if o["roiPct"] is not None else "- ROI: n/a",
        f"- Sample status: {o['sampleStatus']}",
        "",
        "## Tier breakdown (PlacedBet.confidence: HIGH ≈ Tier A, MEDIUM ≈ Tier B, PAPER/LOW ≈ Tier C, UNRECORDED = no tier stored)",
    ]
    for tier, stats in sorted(report["tierBreakdown"].items(), key=lambda kv: -kv[1]["count"]):
        roi_text = f"{stats['roiPct']}%" if stats["roiPct"] is not None else "n/a"
        lines.append(
            f"- {tier}: {stats['record']['wins']}-{stats['record']['losses']}-{stats['record']['pushes']}, "
            f"stake ${stats['stakeRisked']}, P/L ${stats['netProfitLoss']}, "
            f"ROI {roi_text} ({stats['sampleStatus']})"
        )

    lines += ["", "## Market family breakdown"]
    for fam, stats in sorted(report["marketFamilyBreakdown"].items(), key=lambda kv: -kv[1]["count"]):
        lines.append(
            f"- {fam}: {stats['record']['wins']}-{stats['record']['losses']}-{stats['record']['pushes']}, "
            f"stake ${stats['stakeRisked']}, P/L ${stats['netProfitLoss']} ({stats['sampleStatus']})"
        )

    c = report["calibration"]
    lines += [
        "",
        "## Calibration (predicted probability vs. outcome, model or manual fair probability only)",
        f"- n = {c['n']} ({c['sampleStatus']})",
    ]
    if c["n"]:
        lines += [
            f"- Avg predicted win probability: {c['avgPredictedProbability']}",
            f"- Actual win rate: {c['actualWinRate']}",
            f"- Calibration error (actual - predicted): {c['calibrationError']}",
        ]
    else:
        lines.append("- No bet in this window has a valid fair probability on record.")

    clv = report["clvCoverage"]
    lines += [
        "",
        "## CLV coverage (legitimate pregame close only)",
        f"- Covered: {clv['withClv']} / {clv['windowSize']}"
        + (f" ({clv['coveragePct']}%)" if clv["coveragePct"] is not None else ""),
        f"- Without a legitimate close: {clv['withoutClv']}",
    ]
    if clv["avgClvCents"] is not None:
        lines.append(f"- Avg CLV (cents): {clv['avgClvCents']}")

    return "\n".join(lines) + "\n"
