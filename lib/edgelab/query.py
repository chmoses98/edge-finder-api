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


# ---------------------------------------------------------------------------
# Research Query Surface (Part 6 of the MLB Market Research Corpus &
# Frictionless Manual Logging milestone): read-only functions over the
# broader corpus (MarketObservation/Market/Game/Recommendation/Settlement/
# Postmortem), alongside the bet-focused functions above. Every function
# here is a pure filter/aggregate over already-loaded lists -- exactly the
# same convention as the PlacedBet functions above -- so
# scripts/edgelab/query_research.py can wrap them as a read-only CLI.
# Nothing in this section ever calls a write function from
# lib.edgelab.bets/storage/postmortems.
# ---------------------------------------------------------------------------

def observed_markets_for_game(observations, game_id):
    """Every observed market (all capture ticks) for one gameId -- Part 6: 'show every observed market for a given game.'"""
    return [o for o in observations if o.get("gameId") == game_id]


def alternate_thresholds(observations, market_family, market_horizon=None):
    """Distinct (marketTicker, threshold) pairs for one family/date -- Part 6: 'show all alternate team-total thresholds for a date.'"""
    seen = {}
    for o in observations:
        if o.get("marketFamily") != market_family:
            continue
        if market_horizon and o.get("marketHorizon") != market_horizon:
            continue
        ticker = o.get("marketTicker")
        if ticker not in seen:
            seen[ticker] = {"marketTicker": ticker, "threshold": o.get("threshold"), "team": o.get("team"), "player": o.get("player")}
    return sorted(seen.values(), key=lambda r: (r["threshold"] if r["threshold"] is not None else float("-inf"), r["marketTicker"]))


def pitcher_strikeout_closings(observations, settlements, market_family="pitcher_strikeouts"):
    """Every pitcher-strikeout market ticker with its closing settlement result -- Part 6."""
    settlement_by_ticker = {s["marketTicker"]: s for s in settlements}
    tickers = sorted({o["marketTicker"] for o in observations if o.get("marketFamily") == market_family})
    out = []
    for ticker in tickers:
        s = settlement_by_ticker.get(ticker)
        out.append({
            "marketTicker": ticker,
            "settlementStatus": s.get("settlementStatus") if s else "SETTLEMENT_UNRESOLVED",
            "result": s.get("result") if s else None,
            "unavailableReason": s.get("unavailableReason") if s else "not_yet_settled",
        })
    return out


def checkpoint_price_comparison(observations, market_ticker):
    """
    For one exact ticker: first observed / lineup-confirmed / closing
    prices side by side -- Part 6: 'compare first observed, lineup-
    confirmed, and closing prices.' Any checkpoint never observed is null,
    never guessed.
    """
    rows = sorted((o for o in observations if o.get("marketTicker") == market_ticker), key=lambda o: o.get("capturedAt") or "")
    by_checkpoint = {}
    for o in rows:
        cp = o.get("checkpoint")
        if cp and cp not in by_checkpoint:
            by_checkpoint[cp] = o
    closing = next((o for o in reversed(rows) if o.get("isClosingCandidate")), None)
    return {
        "marketTicker": market_ticker,
        "firstObserved": by_checkpoint.get("FIRST_DAILY"),
        "lineupConfirmed": by_checkpoint.get("LINEUP_CONFIRMATION"),
        "closing": closing,
    }


def observed_never_recommended(observations, recommendations):
    """Part 6: 'show markets observed but never recommended.'"""
    recommended_tickers = {r["marketTicker"] for r in recommendations if r.get("marketTicker")}
    observed_tickers = {o["marketTicker"] for o in observations}
    return sorted(observed_tickers - recommended_tickers)


def recommended_not_placed(recommendations, bets):
    """Part 6: 'show markets recommended but not placed.' Uses Recommendation.betPlaced, never inferred from the bets ledger alone."""
    placed_tickers = {b["marketTicker"] for b in active(bets) if b.get("marketTicker")}
    surfaced = {"WATCH", "RECOMMENDED", "RECOMMENDED_NOT_BET", "BET_PLACED"}
    return [
        r for r in recommendations
        if r.get("status") in surfaced and not r.get("betPlaced") and r.get("marketTicker") not in placed_tickers
    ]


def manual_bets_without_slate(bets, games):
    """
    Part 6: 'show manually placed bets without a production slate' -- a
    bet whose gameId has no corresponding Game dimension row for that
    date (i.e. the production slate never ran/observed that game at all,
    so this bet's only evidence is the manual import + linkage).
    """
    known_game_ids = {g["gameId"] for g in games if g.get("gameId")}
    return [b for b in active(bets) if b.get("gameId") and b["gameId"] not in known_game_ids]


def performance_by_family_all_observed(settlements, bets):
    """
    Part 6: 'show performance by market family across all observed
    markets' -- hypothetical returns for EVERY settled market (not only
    placed ones), broken out by family, alongside the real placed-bet P/L
    for comparison.
    """
    by_family = defaultdict(lambda: {
        "marketsSettled": 0, "hypotheticalReturnSum": 0.0,
        "betsPlaced": 0, "realizedPnl": 0.0,
    })
    bets_by_ticker = defaultdict(list)
    for b in active(bets):
        if b.get("marketTicker"):
            bets_by_ticker[b["marketTicker"]].append(b)

    for s in settlements:
        if s.get("settlementStatus") != "SETTLED":
            continue
        fam = s.get("marketFamily") or "UNKNOWN"
        stats = by_family[fam]
        stats["marketsSettled"] += 1
        checkpoints_returns = [
            c["hypotheticalYesReturn"] for c in (s.get("hypotheticalReturnsByCheckpoint") or [])
            if c.get("hypotheticalYesReturn") is not None
        ]
        if checkpoints_returns:
            stats["hypotheticalReturnSum"] = round(stats["hypotheticalReturnSum"] + checkpoints_returns[-1], 4)
        for bet in bets_by_ticker.get(s["marketTicker"], []):
            stats["betsPlaced"] += 1
            stats["realizedPnl"] = round(stats["realizedPnl"] + (bet.get("netProfitLoss") or 0), 2)
    return dict(by_family)


def market_corpus_capture_for_bet(bet, research_runs_by_id=None):
    """Part 6: 'show the market-corpus capture linked to a given bet.' Reads the bet's own marketObservationLinkage; never re-derives it."""
    linkage = bet.get("marketObservationLinkage") or {}
    run_id = linkage.get("marketCorpusRunId")
    result = dict(linkage)
    if run_id and research_runs_by_id:
        result["captureRun"] = research_runs_by_id.get(run_id)
    return result


def postmortem_for_date(postmortems_by_date, game_date):
    """Part 6: 'show the postmortem linked to a given date.' postmortems_by_date: {gameDate: current-revision-postmortem-dict}."""
    return postmortems_by_date.get(game_date)


def postmortem_for_bet(bet_id, postmortems):
    """Part 6: 'show the postmortem linked to a given bet.' postmortems: list of current-revision postmortem dicts."""
    for pm in postmortems:
        if bet_id in (pm.get("linkedBetIds") or []):
            return pm
    return None


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
