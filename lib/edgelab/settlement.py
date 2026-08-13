"""
lib/edgelab/settlement.py
============================
Settlement linkage (Phase 1 section I): settle every OBSERVED eligible
market, not only placed bets, so unbet market families (F5 spreads,
alternate totals, team totals, pitcher/player props) remain researchable
via price-dependent hypothetical returns.

Pure functions only, deliberately -- reuses the repo's existing settle_*
logic rather than re-deriving it:
  - lib.research.inning_result_settlement.settle_inning_result for F3/F5/F7
    three-way result markets, gated on lib.research.market_taxonomy's
    HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"] -- currently
    CONFIRMED_THREE_WAY for all of F3/F5/F7, so all three settle the same
    way; if a future change ever retracts a horizon's confirmation, this
    module needs no change -- settle_inning_result() falls back to
    SETTLEMENT_UNRESOLVED/"structure_unverified" on its own.
  - Full-game moneyline/total/team-total/winning-margin/first-inning-run
    settlement is implemented directly here (simple final-score
    comparisons; no existing library function covers these families).
  - Pitcher/hitter prop families (strikeouts, outs, hits, total bases,
    HRR, RBIs, stolen bases) have NO settlement implementation in this
    repo at all -- Phase 1 is explicit and honest about that gap
    (SETTLEMENT_UNRESOLVED, unavailableReason="player_prop_settlement_not_implemented"),
    not a fabricated result. See docs/EDGELAB_PHASE1.md's Phase 2
    recommendations and
    docs/MARKET_RESEARCH_CORPUS_AND_MANUAL_LOGGING.md's §5/§14 (these
    markets ARE fully observable/queryable after that milestone -- they
    are simply not outcome-settled yet). Tracked as a scoped follow-up:
    https://github.com/chmoses98/edge-finder-api/issues/43.

Fetching the actual game_outcome (final score, per-period score, game
status) is a CLI/wiring concern (scripts/edgelab/settle_markets.py),
kept out of this module so the settlement decision itself stays pure and
directly testable, matching every settle_* function elsewhere in this
repo.
"""

from lib.edgelab import ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.edgelab import player_prop_settlement
from lib.edgelab.execution_economics import realized_pl_for_bet
from lib.research.inning_result_settlement import SETTLEMENT_UNRESOLVED, settle_inning_result
from lib.research.market_taxonomy import (
    FAMILY_FIRST_INNING_RUN,
    FAMILY_GAME_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_RESULT,
    FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
)

# Recommendation.status values that mean the market was actually SURFACED
# to a human/model decision, as opposed to merely observed/extended-
# coverage (NOT_EVALUATED/INSUFFICIENT_MODEL_SUPPORT/PASS_*) -- see
# data/edgelab/schema_v1/recommendation.schema.json's status enum.
_RECOMMENDED_STATUSES = {"WATCH", "RECOMMENDED", "BET_PLACED", "RECOMMENDED_NOT_BET"}

_VOIDABLE_STATUSES = {"Postponed", "Cancelled", "Suspended"}
_PLAYER_PROP_FAMILIES = {
    "pitcher_strikeouts", "pitcher_outs", "hitter_hits", "hitter_total_bases",
    "hitter_hits_runs_rbis", "hitter_rbis", "hitter_stolen_bases",
}


def settle_market(market, game_outcome):
    """
    market: dict with at least marketFamily, marketHorizon, team, threshold.
    game_outcome: {"awayRuns", "homeRuns", "awayAbbr", "homeAbbr",
    "completedInnings", "gameStatus", "periodScores": {"F3"/"F5"/"F7": (away,home)},
    "firstInningRuns": (away,home) or None}. Any key may be missing/None.

    Returns (settlementStatus, result, unavailableReason):
      settlementStatus in {"SETTLED", "VOID", "SETTLEMENT_UNRESOLVED"}
      result in {"YES","NO","AWAY","HOME","TIE", None}
      unavailableReason non-None only when settlementStatus != "SETTLED"
      and not a clean VOID.
    """
    game_status = game_outcome.get("gameStatus")
    if game_status in _VOIDABLE_STATUSES:
        return "VOID", None, None

    family = market.get("marketFamily")
    horizon = market.get("marketHorizon") or "F5"

    if family in _PLAYER_PROP_FAMILIES:
        return "SETTLEMENT_UNRESOLVED", None, "player_prop_settlement_not_implemented"

    if family in (FAMILY_GAME_RESULT, FAMILY_INNING_RESULT):
        if family == FAMILY_GAME_RESULT:
            away, home = game_outcome.get("awayRuns"), game_outcome.get("homeRuns")
            if away is None or home is None or game_status != "Final":
                return "SETTLEMENT_UNRESOLVED", None, "missing_final_score"
            winner = "TIE" if away == home else ("AWAY" if away > home else "HOME")
        else:
            period = (game_outcome.get("periodScores") or {}).get(horizon)
            if period is None:
                return "SETTLEMENT_UNRESOLVED", None, f"missing_period_score_{horizon}"
            away, home = period
            raw_result, reason = settle_inning_result(horizon, away, home, game_outcome.get("completedInnings"), game_status)
            if raw_result == SETTLEMENT_UNRESOLVED:
                return "SETTLEMENT_UNRESOLVED", None, reason
            winner = raw_result.upper()

        # Every Kalshi moneyline/result ticker is its OWN binary market for
        # ONE specific team (or the tie outcome) -- resolve to that ticker's
        # own YES/NO rather than the raw 3-way game winner.
        ticker_outcome = market.get("outcomeLabel")  # classify_market()'s 'Win'/'Tie', see Market.outcomeLabel
        if ticker_outcome == "Tie":
            return "SETTLED", ("YES" if winner == "TIE" else "NO"), None

        ticker_team = market.get("team")
        away_abbr, home_abbr = game_outcome.get("awayAbbr"), game_outcome.get("homeAbbr")
        if not ticker_team or ticker_team not in (away_abbr, home_abbr):
            return "SETTLEMENT_UNRESOLVED", None, "ticker_team_not_resolved"
        ticker_side = "AWAY" if ticker_team == away_abbr else "HOME"
        return "SETTLED", ("YES" if winner == ticker_side else "NO"), None

    if family == FAMILY_FIRST_INNING_RUN:
        first_inning = game_outcome.get("firstInningRuns")
        if first_inning is None:
            return "SETTLEMENT_UNRESOLVED", None, "missing_first_inning_score"
        away, home = first_inning
        return "SETTLED", ("YES" if (away + home) > 0 else "NO"), None

    if family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_WINNING_MARGIN):
        threshold = market.get("threshold")
        if threshold is None:
            return "SETTLEMENT_UNRESOLVED", None, "missing_threshold"

        if family == FAMILY_INNING_TOTAL:
            period = (game_outcome.get("periodScores") or {}).get(horizon)
            if period is None:
                return "SETTLEMENT_UNRESOLVED", None, f"missing_period_score_{horizon}"
            away, home = period
        else:
            away, home = game_outcome.get("awayRuns"), game_outcome.get("homeRuns")
            if away is None or home is None or game_status != "Final":
                return "SETTLEMENT_UNRESOLVED", None, "missing_final_score"

        if family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL):
            return "SETTLED", ("YES" if (away + home) > threshold else "NO"), None

        team = market.get("team")
        away_abbr, home_abbr = game_outcome.get("awayAbbr"), game_outcome.get("homeAbbr")
        if not team or team not in (away_abbr, home_abbr):
            return "SETTLEMENT_UNRESOLVED", None, "team_not_resolved"
        team_runs, opp_runs = (away, home) if team == away_abbr else (home, away)

        if family == FAMILY_TEAM_TOTAL:
            return "SETTLED", ("YES" if team_runs > threshold else "NO"), None
        return "SETTLED", ("YES" if (team_runs - opp_runs) > threshold else "NO"), None  # winning_margin

    return "SETTLEMENT_UNRESOLVED", None, "unrecognized_market_family"


def settle_market_full(market, game_outcome):
    """
    GitHub issue #43: wraps settle_market() with automatic settlement
    for the seven player-prop families. When marketFamily is one of
    those families AND game_outcome carries the boxscore context they
    need (a "boxscoreTeams" key -- see scripts/edgelab/settle_markets.py,
    which fetches it once per gamePk via lib/edgelab/mlb_boxscore.py),
    delegates to
    lib.edgelab.player_prop_settlement.settle_player_prop_market()
    instead of settle_market()'s own unconditional
    "player_prop_settlement_not_implemented" shortcut. Every other
    family, and a player-prop family market when boxscore context isn't
    available at all (game_outcome lacks "boxscoreTeams" -- e.g. a
    caller that hasn't wired in the new fetch), falls through to plain
    settle_market() unchanged -- see that function's own docstring and
    tests/edgelab/test_settlement.py's
    test_player_props_are_explicitly_unimplemented_not_fabricated, which
    documents settle_market()'s OWN contract in isolation, still true
    and still tested, not this repository's overall capability.

    Returns (settlementStatus, result, unavailableReason, evidence).
    evidence is None for every market this delegates to plain
    settle_market() for -- only the player-prop settlement path ever
    populates it (see data/edgelab/schema_v1/settlement.schema.json's
    settlementEvidence).
    """
    family = market.get("marketFamily")
    if family in _PLAYER_PROP_FAMILIES and "boxscoreTeams" in game_outcome:
        return player_prop_settlement.settle_player_prop_market(
            market,
            game_status=game_outcome.get("playerPropGameStatus"),
            boxscore_teams=game_outcome.get("boxscoreTeams") or {},
            away_abbr=game_outcome.get("awayAbbr"),
            home_abbr=game_outcome.get("homeAbbr"),
            kalshi_official_result=(game_outcome.get("kalshiOfficialResultsByTicker") or {}).get(
                market.get("marketTicker")
            ),
            fetch_meta=game_outcome.get("boxscoreFetchMeta"),
        )
    status, result, reason = settle_market(market, game_outcome)
    return status, result, reason, None


def was_market_ever_recommended(recommendations_for_ticker):
    """
    True if ANY Recommendation row for this ticker (any research run that
    date) reached WATCH/RECOMMENDED/RECOMMENDED_NOT_BET/BET_PLACED -- i.e.
    the market was actually surfaced by a decision process, not merely
    observed or given a NOT_EVALUATED/PASS_* extension-coverage row. Pure;
    `recommendations_for_ticker` is whatever rows the caller already
    filtered to this marketTicker (empty list -> False, never a guess).
    """
    return any(r.get("status") in _RECOMMENDED_STATUSES for r in recommendations_for_ticker)


def hypothetical_yes_return(yes_price, result):
    """
    Price-dependent hypothetical return per $1 staked on the YES side at
    `yes_price` (0-1 fraction). result is the settle_market() result:
    win for YES if result == 'YES' (or 'AWAY'/'HOME' callers have already
    mapped to their own YES/NO framing before calling this). None/'VOID'
    -> 0 (no return either way). Never fabricates a return for a missing
    price -- returns None instead.
    """
    if yes_price is None or yes_price <= 0 or yes_price >= 1:
        return None
    if result is None:
        return 0.0
    if result == "YES":
        return round((1.0 - yes_price) / yes_price, 4)
    if result == "NO":
        return -1.0
    return 0.0


def derive_bet_result(settlement_result, bet_side):
    """
    settlement_result: Settlement.result ('YES'/'NO'/None -- None means
    not yet settled or unresolved). bet_side: PlacedBet.side ('YES'/'NO').
    Returns 'WIN'/'LOSS'/None -- None (not a guess) when settlement_result
    is None, i.e. still pending.
    """
    if settlement_result not in ("YES", "NO") or bet_side not in ("YES", "NO"):
        return None
    return "WIN" if settlement_result == bet_side else "LOSS"


def realized_return_for_bet(stake, entry_price, bet_result):
    """
    Actual dollar return (not counting stake) for a settled bet. Kalshi
    contracts pay $1 per contract on a win; stake/entry_price gives the
    (fractional) contract count bought at entry_price (0-1 implied prob).
    WIN -> stake * (1/entry_price - 1); LOSS -> -stake; PUSH/VOID -> 0.
    Never fabricates a number when a required input is missing.
    """
    if bet_result is None or stake is None:
        return None
    if bet_result in ("PUSH", "VOID"):
        return 0.0
    if bet_result == "LOSS":
        return round(-stake, 4)
    if bet_result == "WIN":
        if not entry_price or entry_price <= 0:
            return None
        return round(stake * (1.0 / entry_price - 1.0), 4)
    return None


def compare_confirmed_receipt_to_settlement(bet, computed_bet, *, now=None):
    """
    Pure. When `bet` already carries a confirmed manual receipt
    (lib.edgelab.bets.confirm_realized_return --
    confirmedReceiptNetProfitLoss set), compares it against
    `computed_bet` (this ticker's freshly computed OBJECTIVE settlement
    outcome, i.e. one entry of settle_bets_for_ticker's own output) and
    returns a small, additive comparison record. NEVER overwrites either
    side: the objective result/status/netProfitLoss/returnAmount stay
    exactly what settle_bets_for_ticker already computed, and the
    confirmedReceipt* fields stay exactly what confirm_realized_return
    already recorded -- both provenance paths are preserved side by
    side. This is purely a flag so a genuine disagreement between the
    two independent sources of truth (a settlement bug, a Kalshi
    partial-fill/fee case this system's binary model can't represent, a
    data-entry mistake in the original manual receipt, or -- the
    ordinary case -- the same real-world outcome computed two different
    ways) is surfaced explicitly rather than one silently masking the
    other.

    Returns None when `bet` has no confirmed receipt at all (nothing to
    reconcile against yet -- the ordinary case for most settled bets).
    """
    if bet.get("confirmedReceiptNetProfitLoss") is None:
        return None
    receipt_net_pl = bet["confirmedReceiptNetProfitLoss"]
    receipt_result = "WIN" if receipt_net_pl > 0 else ("LOSS" if receipt_net_pl < 0 else "PUSH")
    objective_result = computed_bet.get("result")
    objective_net_pl = computed_bet.get("netProfitLoss")
    results_agree = receipt_result == objective_result
    amounts_agree = objective_net_pl is not None and abs(receipt_net_pl - objective_net_pl) <= 0.01
    return {
        "comparedAt": now,
        "objectiveResult": objective_result,
        "objectiveNetProfitLoss": objective_net_pl,
        "confirmedReceiptImpliedResult": receipt_result,
        "confirmedReceiptNetProfitLoss": receipt_net_pl,
        "resultsAgree": results_agree,
        "amountsAgree": amounts_agree,
        "agrees": bool(results_agree and amounts_agree),
    }


def settle_bets_for_ticker(matching_bets, settlement_status, result, *, now=None):
    """
    Settle EVERY bet on one ticker (a ticker can carry multiple tranches
    -- see tests/edgelab/test_bets.py's multi-bet-on-one-market coverage),
    never just the first. Returns a list of updated bet dicts (copies;
    the input list is never mutated) -- empty if settlement_status isn't
    "SETTLED" (a VOID/SETTLEMENT_UNRESOLVED market leaves every bet on it
    untouched, still pending, rather than guessing a result).

    Always returns one computed (fully-settled) dict per input bet,
    regardless of whether that bet's settlement outcome actually
    changed -- callers needing a REPRESENTATIVE settled bet (e.g.
    scripts/edgelab/settle_markets.py's Settlement.betId/realizedReturn
    fields) can always rely on this list being non-empty whenever
    matching_bets is non-empty and the market settled. Use
    bet_needs_settlement_update() to decide which of these actually
    need to be persisted/stamped with a fresh updatedAt -- an unrelated
    already-correct bet must never be rewritten just because this
    function was called again (GitHub issue #43 correction round).

    Also attaches confirmedReceiptSettlementComparison (see
    compare_confirmed_receipt_to_settlement above) whenever the input
    bet already carries a confirmed manual receipt -- so a bet settled
    via lib.edgelab.bets.confirm_realized_return before objective
    settlement was possible (e.g. a standalone/manual betting day; see
    lib.edgelab.mlb_schedule) gets an explicit agree/disagree flag the
    moment objective settlement does become available, rather than one
    provenance path silently overwriting the other.

    Kalshi Fee-Aware Execution Economics milestone (spec section 13):
    netProfitLoss/returnAmount are now computed by
    lib.edgelab.execution_economics.realized_pl_for_bet, which is
    execution-status-aware -- a bet whose executionStatus is
    SOLD_EARLY/PARTIAL_CLOSE (a position closed before this settlement,
    e.g. via a confirmed Kalshi share-card sale) computes its P/L from
    its own actual exitSaleProceeds, NEVER from this ticker's objective
    WIN/LOSS settlement formula, even though `result`/`status` below
    still record the market's objective outcome for informational
    purposes. A bet with no executionStatus recorded (every bet settled
    before this milestone, and any new bet with no known early-close
    evidence) defaults to the pre-existing HELD_TO_SETTLEMENT cash
    shape -- this is a behavior-preserving default, not a silent
    reclassification. The WIN-case dollar amount itself IS a genuine fix
    from the prior stake/entryPrice=contracts assumption (see
    realized_pl_for_bet's own docstring) -- LOSS/PUSH/VOID amounts are
    unchanged.
    """
    if settlement_status != "SETTLED":
        return []
    updated = []
    for bet in matching_bets:
        bet_result = derive_bet_result(result, bet.get("side") or "YES")
        realized_return = realized_pl_for_bet(
            execution_status=bet.get("executionStatus"),
            stake=bet.get("stake"),
            bet_result=bet_result,
            entry_price=bet.get("entryPrice"),
            contracts=bet.get("contracts"),
            exit_sale_proceeds=bet.get("exitSaleProceeds"),
        )
        updated_bet = dict(bet)
        updated_bet["result"] = bet_result
        updated_bet["status"] = "settled"
        updated_bet["netProfitLoss"] = realized_return
        updated_bet["returnAmount"] = realized_return
        comparison = compare_confirmed_receipt_to_settlement(bet, updated_bet, now=now)
        if comparison is not None:
            updated_bet["confirmedReceiptSettlementComparison"] = comparison
        updated.append(updated_bet)
    return updated


def bet_needs_settlement_update(original_bet, computed_bet):
    """
    Pure. True iff `computed_bet` (settle_bets_for_ticker's freshly
    computed settled shape for this bet) actually differs from
    `original_bet`'s already-persisted status/result/netProfitLoss/
    returnAmount -- i.e. this bet genuinely needs to be rewritten with a
    fresh updatedAt. False for a true no-op: an already-settled bet
    whose settlement outcome hasn't changed must never be rewritten
    just because a settlement run happened again (GitHub issue #43
    correction round) -- a changed evidence-fetch timestamp or a
    re-fetched (but factually identical) upstream payload alone must
    never flip this to True, since none of those fields are compared
    here at all.
    """
    return not (
        original_bet.get("status") == "settled"
        and original_bet.get("result") == computed_bet.get("result")
        and original_bet.get("netProfitLoss") == computed_bet.get("netProfitLoss")
        and original_bet.get("returnAmount") == computed_bet.get("returnAmount")
        # Both None (the vast majority of bets, which never have a
        # confirmed manual receipt to reconcile against) is a no-op
        # here just like every other field above; only a GENUINE change
        # to the comparison outcome (first computed, or its inputs
        # changed) triggers a rewrite.
        and original_bet.get("confirmedReceiptSettlementComparison") == computed_bet.get("confirmedReceiptSettlementComparison")
    )


# Fields excluded from the idempotency comparison in
# merge_settlement_record() -- these are expected to legitimately differ
# between two runs that determined the EXACT SAME authoritative facts
# (a fresh wall-clock stamp, or a fresh network fetch of byte-identical
# upstream content), and must never by themselves cause a rewrite.
_VOLATILE_SETTLEMENT_FIELDS = frozenset({"createdAt", "updatedAt", "settledAt"})
_VOLATILE_EVIDENCE_FIELDS = frozenset({"fetchedAt", "sourcePayloadHash"})


def _comparable_settlement_view(record):
    """
    Pure. `record` with every volatile/expected-to-drift field removed
    -- the canonical-content comparison key merge_settlement_record()
    uses to decide "is this actually the same settlement, or a genuine
    correction". A changed fetchedAt/sourcePayloadHash ALONE (the
    result of a fresh network fetch of unchanged upstream data) must
    never register as a difference here.
    """
    view = {k: v for k, v in record.items() if k not in _VOLATILE_SETTLEMENT_FIELDS}
    evidence = view.get("settlementEvidence")
    if isinstance(evidence, dict):
        view["settlementEvidence"] = {k: v for k, v in evidence.items() if k not in _VOLATILE_EVIDENCE_FIELDS}
    return view


def merge_settlement_record(existing_record, new_record):
    """
    Semantic-idempotency merge (GitHub issue #43 correction round): a
    rerun against equivalent authoritative final facts must leave the
    canonical settlements file byte-for-byte unchanged, never rewriting
    createdAt/updatedAt/settledAt just because the run happened again.

    - No prior record (`existing_record` is None, i.e. first-ever
      settlement of this ticker): returns `new_record` verbatim.
    - Prior record exists and its canonical content (everything except
      createdAt/updatedAt/settledAt and settlementEvidence's own
      fetchedAt/sourcePayloadHash -- see _comparable_settlement_view)
      is IDENTICAL to the freshly computed one: returns
      `existing_record` COMPLETELY UNCHANGED (the exact same dict,
      including its original settledAt) -- a true no-op.
    - Prior record exists but the canonical content genuinely differs
      (a corrected authoritative statistic, a player now resolvable,
      etc.): returns `new_record` with `createdAt` overridden back to
      `existing_record`'s original createdAt -- the fact was first
      recorded when it was first recorded; only updatedAt/settledAt
      (already fresh on `new_record`) advance to reflect the
      correction.
    """
    if existing_record is None:
        return new_record
    if _comparable_settlement_view(existing_record) == _comparable_settlement_view(new_record):
        return existing_record
    return dict(new_record, createdAt=existing_record.get("createdAt", new_record["createdAt"]))


def build_settlement_record(market_ticker, game_id, market_family, settlement_status, result,
                             settlement_source, settled_at, unavailable_reason=None,
                             hypothetical_returns_by_checkpoint=None, bet_id=None,
                             realized_return=None, source="edgelab_settlement", provenance=None,
                             was_recommended=None, was_placed=None, settlement_evidence=None):
    now = ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "settlementId": ids.build_settlement_id(game_id, market_ticker),
        "gameId": game_id,
        "sport": DEFAULT_SPORT,
        "platform": DEFAULT_PLATFORM,
        "marketTicker": market_ticker,
        "marketFamily": market_family,
        "outcome": result,
        "settlementStatus": settlement_status,
        "unavailableReason": unavailable_reason,
        "settlementSource": settlement_source,
        "settledAt": settled_at,
        "result": result if result in ("YES", "NO") else None,
        "hypotheticalReturnsByCheckpoint": hypothetical_returns_by_checkpoint or [],
        "betId": bet_id,
        "realizedReturn": realized_return,
        "wasRecommended": was_recommended,
        "wasPlaced": was_placed if was_placed is not None else (bet_id is not None),
        "settlementEvidence": settlement_evidence,
        "createdAt": now,
        "updatedAt": now,
        "source": source,
        "validationStatus": "valid",
        "provenance": provenance or {
            "sourceSystem": source,
            "sourceFile": None,
            "sourceKey": market_ticker,
            "capturedAt": settled_at,
            "ingestedAt": now,
        },
    }
