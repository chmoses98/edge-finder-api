"""
Coherence constraints between related Kalshi MLB contracts and fee-aware
violation tests.  Prices in cents.  Every "locked profit" is computed with
lib.edgelab.research.market_structure.economics (taker fee on every leg,
whole contracts, no midpoint fills).

Contract semantics (docs/EDGELAB_KALSHI_TOTAL_LADDER_SEMANTICS.md):
  LADDER rung N       -> YES iff total >= N  (no push)
  TEAM_LADDER (TT) N  -> YES iff team runs >= N
  SPREAD team k       -> YES iff margin >= k
  WINNER3             -> exactly one of AWAY/HOME/TIE pays

Constraints (event inclusions, each yields a min-payout-100 pair):
  C1 ladder:        {total >= N+1} subset {total >= N}           -> YES N + NO N+1
  C2 three-way:     AWAY + HOME + TIE = 1                          -> all YES (or all NO)
  C3 cross-horizon: {F5 total >= N} subset {game total >= M<=N}    -> YES TOTAL-M + NO F5TOTAL-N
  C4 team vs game:  {team runs >= N} subset {game total >= N}      -> YES TOTAL-N + NO TEAMTOTAL-N
  C5 spread vs ML:  {margin >= k>=1} subset {team wins}            -> YES GAME-team + NO SPREAD-team-k
"""
from lib.edgelab.research.market_structure import economics as E

STANDARD_CONTRACTS = 10


def _pair_result(kind, key, buy_yes_cents, buy_no_from_bid_cents, contracts=STANDARD_CONTRACTS):
    """buy YES leg at `buy_yes_cents`; NO leg priced 100 - bid."""
    if buy_yes_cents is None or buy_no_from_bid_cents is None:
        return None
    no_cents = 100 - buy_no_from_bid_cents
    slack_prefee = buy_no_from_bid_cents - buy_yes_cents  # >0 means pre-fee inversion
    locked = E.locked_profit_cents([buy_yes_cents, no_cents], payout_cents=100, contracts=contracts)
    return {"constraint": kind, "key": key, "yesLegCents": buy_yes_cents, "noLegCents": no_cents,
            "preFeeSlackCents": slack_prefee,
            "lockedProfitCentsPerContract": None if locked is None else round(locked / contracts, 3),
            "preFeeViolation": slack_prefee > 0,
            "postFeeViolation": locked is not None and locked > 0}


def ladder_pairs(rung_quotes):
    """
    rung_quotes: {N: (bid, ask)} for one ladder at one instant.  Tests every
    N < M (not only adjacent): YES N at ask(N) + NO M at 100-bid(M).
    """
    out = []
    rungs = sorted(k for k, v in rung_quotes.items() if v is not None)
    for i, n in enumerate(rungs):
        bn, an = rung_quotes[n]
        for m in rungs[i + 1:]:
            bm, am = rung_quotes[m]
            r = _pair_result("LADDER", (n, m), an, bm)
            if r:
                out.append(r)
    return out


def three_way(quotes):
    """quotes: {"AWAY": (bid, ask), "HOME": (bid, ask), "TIE": (bid, ask)}."""
    if any(quotes.get(s) is None for s in ("AWAY", "HOME", "TIE")):
        return None
    asks = [quotes[s][1] for s in ("AWAY", "HOME", "TIE")]
    bids = [quotes[s][0] for s in ("AWAY", "HOME", "TIE")]
    res = {"constraint": "THREE_WAY", "sumAsks": None, "sumBids": None,
           "buyAllYesLockedPerContract": None, "buyAllNoLockedPerContract": None,
           "preFeeViolation": False, "postFeeViolation": False}
    if None not in asks:
        res["sumAsks"] = sum(asks)
        lk = E.locked_profit_cents(asks, payout_cents=100, contracts=STANDARD_CONTRACTS)
        res["buyAllYesLockedPerContract"] = None if lk is None else round(lk / STANDARD_CONTRACTS, 3)
        res["preFeeViolation"] |= sum(asks) < 100
        res["postFeeViolation"] |= (lk is not None and lk > 0)
    if None not in bids:
        res["sumBids"] = sum(bids)
        nos = [100 - b for b in bids]
        lk = E.locked_profit_cents(nos, payout_cents=200, contracts=STANDARD_CONTRACTS)
        res["buyAllNoLockedPerContract"] = None if lk is None else round(lk / STANDARD_CONTRACTS, 3)
        res["preFeeViolation"] |= sum(bids) > 100
        res["postFeeViolation"] |= (lk is not None and lk > 0)
    return res


def cross_horizon(game_ladder, f5_ladder):
    """{M:(bid,ask)} game total, {N:(bid,ask)} F5 total.  For every F5 rung N and game rung M <= N."""
    out = []
    for n, (bn, an) in sorted(f5_ladder.items()):
        for m, (bm, am) in sorted(game_ladder.items()):
            if m > n:
                continue
            r = _pair_result("CROSS_HORIZON", (m, n), am, bn)
            if r:
                out.append(r)
    return out


def team_vs_game(game_ladder, team_ladder):
    """{N:(bid,ask)} game total; {N:(bid,ask)} one team's total.  For team rung N and game rung M <= N."""
    out = []
    for n, (bn, an) in sorted(team_ladder.items()):
        for m, (bm, am) in sorted(game_ladder.items()):
            if m > n:
                continue
            r = _pair_result("TEAM_VS_GAME", (m, n), am, bn)
            if r:
                out.append(r)
    return out


def spread_vs_ml(ml_quote, spread_ladder):
    """ml_quote (bid, ask) for the team; spread_ladder {k:(bid,ask)} same team, k >= 1."""
    out = []
    if ml_quote is None:
        return out
    for k, (bk, ak) in sorted(spread_ladder.items()):
        if k < 1:
            continue
        r = _pair_result("SPREAD_VS_ML", (k,), ml_quote[1], bk)
        if r:
            out.append(r)
    return out


def summarize(results):
    """Aggregate a list of pair/three-way results into counts."""
    n = len(results)
    pre = sum(1 for r in results if r and r.get("preFeeViolation"))
    post = sum(1 for r in results if r and r.get("postFeeViolation"))
    return {"pairs": n, "preFeeViolations": pre, "postFeeViolations": post,
            "preFeeShare": (pre / n) if n else None, "postFeeShare": (post / n) if n else None}
