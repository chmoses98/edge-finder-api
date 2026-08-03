"""
lib/edgelab/query.py
========================
Cross-chat read-only query interface over the canonical placed-bet
ledger (Canonical Placed-Bet Ledger milestone, requirement 9). Every
function here is a pure filter/aggregate over an already-loaded list of
PlacedBet (or BankrollTransaction) records -- no file I/O, so any
project chat/script/test can call these directly against whatever it
already has loaded, and scripts/edgelab/query_bets.py wraps them as a
CLI for anything that instead wants to shell out.

Read-only by construction: nothing in this module ever calls
lib.edgelab.bets.write_placed_bet or lib.edgelab.storage.append_records/
upsert_records. Before answering a question about actual wagers, a chat
should read through here (or the CLI), never rely on its own memory of
the conversation or on the recommendation list (see
docs/CANONICAL_BET_LEDGER.md's cross-chat operating protocol).
"""

from collections import defaultdict


def _entry_date(bet):
    """Prefer the explicit gameDate; fall back to entryTimestamp's date component."""
    return bet.get("gameDate") or (bet.get("entryTimestamp") or "")[:10] or None


def by_date(bets, date):
    return [b for b in bets if _entry_date(b) == date]


def by_date_range(bets, start_date, end_date):
    return [b for b in bets if start_date <= (_entry_date(b) or "") <= end_date]


def unsettled(bets):
    """
    Real, still-open wagers -- excludes CANCELLED (a bet logged in error
    is not a genuinely open position, even while its `status` field
    still reads "pending"; found during the maintainer review of this
    milestone -- see also compute_bankroll_summary's identical fix).
    """
    return [b for b in active(bets) if b.get("status") == "pending"]


def settled(bets):
    return [b for b in active(bets) if b.get("status") == "settled"]


def voided(bets):
    return [b for b in active(bets) if b.get("status") == "void"]


def by_market_family(bets, market_family):
    return [b for b in bets if b.get("marketFamily") == market_family]


def by_game(bets, game_id):
    return [b for b in bets if b.get("gameId") == game_id]


def linked_to_snapshot(bets, snapshot_id=None):
    if snapshot_id is not None:
        return [b for b in bets if b.get("snapshotId") == snapshot_id]
    return [b for b in bets if b.get("snapshotId")]


def linked_to_recommendation(bets, recommendation_id=None):
    if recommendation_id is not None:
        return [b for b in bets if b.get("recommendationId") == recommendation_id]
    return [b for b in bets if b.get("recommendationId")]


def manual_without_model_support(bets):
    """
    A MANUAL-sourced bet with no genuine model backing: modelSupported is
    not True AND there is no modelEvaluationId to fall back on (a
    pre-milestone row may have the link but not yet the modelSupported
    field -- never treated as "no model support" just because the newer
    field is null on an old row).
    """
    return [
        b for b in bets
        if b.get("source") == "MANUAL" and not b.get("modelEvaluationId") and b.get("modelSupported") is not True
    ]


def active(bets):
    """Excludes CANCELLED rows -- the normal filter to apply before any ROI/exposure aggregation."""
    return [b for b in bets if (b.get("recordStatus") or "ACTIVE") != "CANCELLED"]


def todays_card(bets, date):
    """
    Every ACTIVE bet placed on `date`, plus totals -- the "today's
    complete placed-bet card" (requirement 9). Never computes a win/loss
    -- only what was staked and what could return, exactly like a
    per-bet receipt.
    """
    day_bets = active(by_date(bets, date))
    total_staked = round(sum(b.get("stake") or 0 for b in day_bets), 2)
    by_family = defaultdict(lambda: {"count": 0, "staked": 0.0})
    for b in day_bets:
        fam = b.get("marketFamily") or "UNKNOWN"
        by_family[fam]["count"] += 1
        by_family[fam]["staked"] = round(by_family[fam]["staked"] + (b.get("stake") or 0), 2)
    return {
        "date": date,
        "betCount": len(day_bets),
        "totalStaked": total_staked,
        "pendingCount": sum(1 for b in day_bets if b.get("status") == "pending"),
        "settledCount": sum(1 for b in day_bets if b.get("status") == "settled"),
        "voidCount": sum(1 for b in day_bets if b.get("status") == "void"),
        "byMarketFamily": dict(by_family),
        "bets": day_bets,
    }


def render_human(bets, *, title="Bets"):
    """Compact human-readable table, for a chat/terminal reader rather than a script."""
    lines = [f"# {title}", ""]
    if not bets:
        lines.append("(none)")
        return "\n".join(lines) + "\n"
    lines.append("| betId | date | ticker | selection | side | stake | entry | status | result |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for b in bets:
        lines.append(
            f"| {b.get('betId', '')[:12]} | {_entry_date(b) or ''} | {b.get('marketTicker', '')} | "
            f"{b.get('selection', '')} | {b.get('side') or ''} | {b.get('stake')} | {b.get('entryPrice')} | "
            f"{b.get('status', '')} | {b.get('result') or ''} |"
        )
    return "\n".join(lines) + "\n"
