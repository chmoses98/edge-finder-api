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

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION

_RECOMMENDED_LIKE_STATUSES = {"RECOMMENDED", "BET_PLACED", "RECOMMENDED_NOT_BET", "WATCH"}


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
        "gamesObserved": len(games),
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
