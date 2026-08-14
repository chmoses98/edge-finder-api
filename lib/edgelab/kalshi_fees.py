"""
lib/edgelab/kalshi_fees.py
==============================
Kalshi Fee-Aware Execution Economics milestone: the ONE reusable,
pure-function fee/execution engine every other module in this milestone
(historical reconciliation, realized settlement P/L, full-universe
research, and any future production net-EV gate) calls into, so the fee
formula is defined and versioned exactly once.

============================================================================
CORRECTION PASS (see docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md's
"Correction pass" section for the full writeup): the ORIGINAL version of
this module's net_settlement_pl_for_order() computed
    net P/L = payout - order_size
even when the simulated whole-contract order only actually consumed part
of order_size (e.g. $9.83 of a $10.00 budget) -- silently treating the
UNSPENT $0.17 as a gambling loss. That is wrong: unused allocated cash is
still cash, never a loss. Every function in this module that touches
order-level P/L now explicitly separates availableBudget /
contractPrincipal / entryFee / actualCashConsumed / unusedCash (see
simulate_order() below), and net P/L is always computed against
actualCashConsumed, never the full budget, unless the budget was
genuinely fully consumed. This bug also directly explains why the
10%+ edge bucket's original "net-of-fees" ROI (-2.02%, from a bucket
whose average executable price was ~0.4995 -- see the price-bucket
sanity table below, where a true fee-only drag at 50c is roughly 1.75
percentage points, not 6.56) was contaminated by unused-budget and
integer-contract sizing effects, not fees alone.
============================================================================

Fee formula / API semantics source (spec section 3 -- "verify from
authoritative sources, do not copy an approximate formula from memory or
a blog"): this environment's outbound network egress is policy-blocked
for kalshi.com and docs.kalshi.com and every third-party mirror tried
(confirmed: WebFetch returns EGRESS_BLOCKED for kalshi.com,
docs.kalshi.com, trading-api.readme.io, www.botforkalshi.com,
www.oddsshopper.com, pm.wiki, and even en.wikipedia.org -- a blanket
WebFetch restriction in this sandbox, not domain-specific), so none of
the official docs (fixed_point_migration, fee_rounding, get-series,
get-series-fee-changes, historical-orders, historical-fills) could be
fetched and read directly. Everything below is cross-corroborated via
multiple independent WebSearch queries (run August 2026), each agreeing
across sources:

  FEE FORMULA (per-series "quadratic" fee_type, confirmed as the exact
  name the API uses for GET /series/{ticker} and GET /series/fee_changes):
    fee_dollars_per_contract = round_or_ceil(fee_multiplier * price * (1 - price), 2)
  - price is in dollars (0-1). fee_multiplier defaults to 0.07 for taker
    on essentially every category including sports; a per-series
    fee_multiplier is returned by the API and CAN differ (no evidence any
    MLB series here does). Maker fee_multiplier is 0.0175 on a few
    "designated" series, 0 on most -- no evidence any MLB series here is
    designated, so FEE_MULTIPLIER_MAKER_DEFAULT stays 0.0.
  - Cross-check: 100 contracts @ $0.50 -> $1.75 (0.07*100*0.5*0.5=1.75,
    exact even before any rounding direction question -- reproduced
    exactly by taker_fee(100, 0.5) below).
  - No separate settlement fee. Exiting early is its own taker/maker
    trade, independently fee'd at the EXIT price/count -- never derived
    from the entry fee.

  FEE ROUNDING (docs.kalshi.com/getting_started/fee_rounding, per
  WebSearch synthesis): Kalshi maintains a per-ORDER "fee accumulator"
  across every FILL that order generates (taker or maker fills alike --
  a single order can fill in pieces against multiple resting orders at
  slightly different prices). Each fill's fee appears to round up
  (over-collect) by a sub-cent amount; once the accumulated
  over-collection across an order's fills exceeds $0.01, a whole-cent
  REBATE is issued and the accumulator is reduced by that cent -- this
  converges the order's TOTAL fee toward what one single equivalent fill
  would have cost. Target balance precision is $0.01 for ordinary
  accounts, $0.0001 for direct exchange members.

  This module has NO fill-level data for any historical bet in this
  repo (no archived orders/fills, no authenticated API access -- see
  §7/module docstring below) -- it therefore CANNOT reconstruct a real
  accumulator/rebate sequence for any historical wager. Every fee this
  module computes assumes the simplifying case of a SINGLE fill per
  order (the accumulator has nothing to accumulate against with only one
  fill, so simple per-order ceil-to-cent is the correct single-fill
  formula) and is tagged FEE_RULE_SOURCE_ESTIMATED_AGGREGATED_ORDER,
  never a source implying multi-fill accumulator awareness --see the
  FEE_RULE_SOURCE_* taxonomy below.

  FIXED-POINT / FRACTIONAL CONTRACTS (docs.kalshi.com/getting_started/
  fixed_point_migration, per WebSearch synthesis): as of a 2026 Q1 API
  migration, Kalshi represents prices as fixed-point dollar strings (up
  to 4 decimals, i.e. subpenny pricing is possible on markets whose own
  price_ranges/price_level_structure grid allows it) and contract counts
  as fixed-point strings supporting FRACTIONAL sizes -- "even if you are
  not placing fractional orders, you will encounter fractional values
  elsewhere in the API (for example, fills)". This means the traditional
  assumption "contracts must be a whole integer" is no longer
  universally true for every market. This repo's own archived Kalshi
  data (data/kalshi_registry_snapshots/*.json), however, was captured
  through a normalizing ingestion pipeline that discards the raw
  price_ranges/price_level_structure/count_fp fields entirely -- there
  is NO archived evidence of which specific historical MLB market/date
  combinations actually had fractional-order capability enabled. Every
  quantity-granularity determination in this module therefore defaults
  to QUANTITY_GRANULARITY_UNKNOWN for this repo's historical corpus, and
  UNKNOWN mode falls back to a conservative whole-contract simulation
  (Kalshi's traditional pre-migration model) rather than fabricating
  fractional-order certainty -- see simulate_order() below.

Because none of this was independently byte-verified against the live
docs, every fee/rule this module produces is tagged with an explicit
provenance -- FEE_STATUS_* (coarse: actual vs. estimated) and
FEE_RULE_SOURCE_* (finer: what kind of evidence/assumption produced this
specific number) -- never FEE_STATUS_ACTUAL_*/FEE_RULE_SOURCE_EXACT_API_FILL
unless real execution/fill/receipt evidence is actually available (this
repo currently has none for any historical bet -- see §7 above). If a
future session regains the ability to fetch kalshi.com/docs.kalshi.com
directly, re-verify this docstring against the live docs and bump
FEE_SCHEDULE_VERSION if anything changed.
"""
import math

# ---------------------------------------------------------------------------
# Fee formula
# ---------------------------------------------------------------------------

FEE_SCHEDULE_VERSION = "KALSHI_TAKER_STANDARD_2026_WEBSEARCH_CORROBORATED_V1"
# Best-known effective-date lower bound for this formula, per public
# sources describing a "July 2026" fee-schedule PDF revision -- NOT
# independently verified against the live document (see module
# docstring). Never asserted as exact.
FEE_SCHEDULE_EFFECTIVE_DATE_LOWER_BOUND = "2026-07-07"

FEE_MULTIPLIER_TAKER_STANDARD = 0.07
FEE_MULTIPLIER_MAKER_DESIGNATED = 0.0175
FEE_MULTIPLIER_MAKER_DEFAULT = 0.0  # most markets charge makers nothing

FEE_TYPE_TAKER = "TAKER"
FEE_TYPE_MAKER = "MAKER"
FEE_TYPE_MIXED = "MIXED"
FEE_TYPE_UNKNOWN = "UNKNOWN"

# Fee provenance/confidence taxonomy (spec section 3 + section 17):
# strict priority ACTUAL > RECONSTRUCTED_EXACT > ESTIMATED > UNKNOWN.
# An ESTIMATED fee must never overwrite an ACTUAL one anywhere in this
# codebase -- see lib.edgelab.execution_economics.merge_fee_status.
FEE_STATUS_ACTUAL_API_FILL = "ACTUAL_API_FILL"
FEE_STATUS_ACTUAL_RECEIPT = "ACTUAL_RECEIPT"
FEE_STATUS_RECONSTRUCTED_EXACT = "RECONSTRUCTED_EXACT"
FEE_STATUS_ESTIMATED_FEE_SCHEDULE = "ESTIMATED_FEE_SCHEDULE"
FEE_STATUS_UNKNOWN = "UNKNOWN"

FEE_STATUS_RANK = {
    FEE_STATUS_ACTUAL_API_FILL: 4,
    FEE_STATUS_ACTUAL_RECEIPT: 4,
    FEE_STATUS_RECONSTRUCTED_EXACT: 3,
    FEE_STATUS_ESTIMATED_FEE_SCHEDULE: 2,
    FEE_STATUS_UNKNOWN: 0,
}

# Historical fee-schedule-applicability taxonomy (spec section 3, ORIGINAL
# pass) -- kept for backward compatibility with anything already using
# these names; FEE_RULE_SOURCE_* below (correction pass, spec section 4)
# is the finer-grained taxonomy new code should prefer.
FEE_RULE_EXACT_HISTORICAL_RULE = "EXACT_HISTORICAL_RULE"
FEE_RULE_EXACT_EXECUTION_RECEIPT = "EXACT_EXECUTION_RECEIPT"
FEE_RULE_EXACT_API_FILL = "EXACT_API_FILL"
FEE_RULE_ESTIMATED_USING_DOCUMENTED_RULE = "ESTIMATED_USING_DOCUMENTED_RULE"
FEE_RULE_FEE_RULE_UNAVAILABLE = "FEE_RULE_UNAVAILABLE"

# Fee-rule-SOURCE taxonomy (correction pass, spec section 4): finer-grained
# than FEE_STATUS_* above -- explains exactly what kind of evidence/
# assumption produced a specific fee number, independent of the coarse
# actual-vs-estimated ranking FEE_STATUS_RANK uses for merge precedence.
FEE_RULE_SOURCE_EXACT_API_FILL = "EXACT_API_FILL"
FEE_RULE_SOURCE_EXACT_ORDER_EXECUTION = "EXACT_ORDER_EXECUTION"
FEE_RULE_SOURCE_EXACT_DOCUMENTED_SINGLE_FILL = "EXACT_DOCUMENTED_SINGLE_FILL"
FEE_RULE_SOURCE_ESTIMATED_AGGREGATED_ORDER = "ESTIMATED_AGGREGATED_ORDER"
FEE_RULE_SOURCE_ROUNDING_SEQUENCE_UNAVAILABLE = "ROUNDING_SEQUENCE_UNAVAILABLE"

# Contract-quantity-granularity capability taxonomy (correction pass, spec
# section 5): per-market/date capability state, never globally assumed.
# This repo's archived Kalshi data does not preserve the raw
# price_ranges/price_level_structure/count_fp fields needed to determine
# this per market -- every historical MLB market/date in this corpus is
# therefore QUANTITY_GRANULARITY_UNKNOWN unless a caller supplies
# stronger evidence. UNKNOWN falls back to a conservative whole-contract
# simulation (see simulate_order()) rather than fabricating a fractional-
# capability claim.
QUANTITY_GRANULARITY_WHOLE_CONTRACT_ONLY = "WHOLE_CONTRACT_ONLY"
QUANTITY_GRANULARITY_FRACTIONAL_ENABLED = "FRACTIONAL_ENABLED"
QUANTITY_GRANULARITY_UNKNOWN = "UNKNOWN"


def _round_up_to_cent(dollars):
    """ceil to the nearest whole cent -- Kalshi's own documented rounding rule."""
    return math.ceil(round(dollars * 100, 6) - 1e-9) / 100.0


def taker_fee(contracts, price, *, multiplier=FEE_MULTIPLIER_TAKER_STANDARD):
    """
    Pure. fee = ceil_cent(multiplier * contracts * price * (1 - price)).
    contracts must be a non-negative integer; price in (0, 1). Returns
    0.0 for contracts <= 0. Never fabricates a number for an invalid
    price -- raises ValueError so a caller's bad input is never silently
    absorbed into a wrong fee.
    """
    if contracts is None or price is None:
        return None
    if contracts <= 0:
        return 0.0
    if not (0 < price < 1):
        raise ValueError(f"price must be in (0, 1), got {price!r}")
    return _round_up_to_cent(multiplier * contracts * price * (1.0 - price))


def maker_fee(contracts, price, *, multiplier=FEE_MULTIPLIER_MAKER_DEFAULT):
    """Same formula/rounding as taker_fee, using the (typically zero) maker multiplier."""
    return taker_fee(contracts, price, multiplier=multiplier)


def cost_for_contracts(contracts, price, *, fee_type=FEE_TYPE_TAKER, multiplier=None):
    """
    Pure. Total cash cost to acquire `contracts` at `price`: the raw
    contract cost (contracts * price) plus the entry fee for that trade.
    Returns (contract_cost, fee, total_cost), each rounded to the cent.
    """
    if multiplier is None:
        multiplier = FEE_MULTIPLIER_MAKER_DEFAULT if fee_type == FEE_TYPE_MAKER else FEE_MULTIPLIER_TAKER_STANDARD
    contract_cost = round(contracts * price, 4)
    fee = taker_fee(contracts, price, multiplier=multiplier)
    return contract_cost, fee, round(contract_cost + fee, 2)


def max_contracts_for_cash(cash_available, price, *, fee_type=FEE_TYPE_TAKER, multiplier=None, max_contracts=100000):
    """
    Pure. The largest integer contract count whose total cost (contracts
    * price + fee) does not exceed `cash_available` -- models how
    Kalshi's own "spend $X" order-entry flow selects a whole-contract
    quantity from a cash budget. Binary search since cost is monotonic
    increasing in contracts. Returns 0 if even 1 contract is unaffordable.
    """
    if cash_available is None or price is None or cash_available <= 0:
        return 0
    lo, hi, best = 0, max_contracts, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        _, _, total = cost_for_contracts(mid, price, fee_type=fee_type, multiplier=multiplier)
        if total <= cash_available + 1e-9:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def simulate_order(
    available_budget, price, *, quantity_granularity=QUANTITY_GRANULARITY_UNKNOWN,
    fee_type=FEE_TYPE_TAKER, multiplier=None,
):
    """
    Pure. THE core execution-economics fix (correction pass, spec
    sections 2/17): simulates spending up to `available_budget` dollars
    at `price`, and returns the FULL decomposition -- never collapses it
    to a single "P/L vs. full budget" number, which is exactly the bug
    this correction pass fixes (see module docstring).

    Returns a dict:
      {
        "availableBudget": 10.0,       # the budget/allocation itself, unchanged
        "contracts": 19,                # int (WHOLE_CONTRACT_ONLY/UNKNOWN) or float (FRACTIONAL_ENABLED)
        "contractPrincipal": 9.5,       # contracts * price, excludes fees
        "entryFee": 0.34,               # the fee actually charged for this fill
        "actualCashConsumed": 9.84,     # contractPrincipal + entryFee -- what Kalshi actually debits
        "unusedCash": 0.16,             # availableBudget - actualCashConsumed -- STILL CASH, never a loss
        "quantityGranularity": "UNKNOWN",
        "feeRuleSource": "ESTIMATED_AGGREGATED_ORDER",
        "feeMultiplier": 0.07,
      }

    QUANTITY_GRANULARITY_WHOLE_CONTRACT_ONLY / _UNKNOWN (the default for
    this repo's historical corpus -- see module docstring §"FIXED-POINT /
    FRACTIONAL CONTRACTS"): `contracts` is the largest INTEGER affordable
    within `available_budget` (max_contracts_for_cash) -- this is the
    only mode where unusedCash can be materially nonzero, since a whole-
    contract order can't always deploy every last cent of a budget.

    QUANTITY_GRANULARITY_FRACTIONAL_ENABLED: `contracts` is solved
    CONTINUOUSLY so the full budget is exactly deployed (fractional
    contract counts mean there is no "leftover" from indivisibility) --
    `contracts = available_budget / (price + multiplier*price*(1-price))`,
    derived by setting contractPrincipal + entryFee (both linear in
    contracts in this continuous formulation) equal to available_budget
    and solving. unusedCash is 0 (or a negligible cent-rounding residual)
    in this mode by construction.

    Returns None for invalid/non-positive available_budget or price.
    """
    if multiplier is None:
        multiplier = FEE_MULTIPLIER_MAKER_DEFAULT if fee_type == FEE_TYPE_MAKER else FEE_MULTIPLIER_TAKER_STANDARD
    if available_budget is None or available_budget <= 0 or price is None or not (0 < price < 1):
        return None

    if quantity_granularity == QUANTITY_GRANULARITY_FRACTIONAL_ENABLED:
        effective_rate_per_contract = price + multiplier * price * (1.0 - price)
        contracts = available_budget / effective_rate_per_contract
        contract_principal = round(contracts * price, 4)
        entry_fee = round(contracts * price * (1.0 - price) * multiplier, 4)
        actual_cash_consumed = round(contract_principal + entry_fee, 2)
    else:
        # WHOLE_CONTRACT_ONLY or UNKNOWN -> conservative whole-contract
        # simulation (never fabricates fractional-order capability this
        # repo has no evidence for -- see module docstring).
        contracts = max_contracts_for_cash(available_budget, price, fee_type=fee_type, multiplier=multiplier)
        contract_principal, entry_fee, actual_cash_consumed = cost_for_contracts(
            contracts, price, fee_type=fee_type, multiplier=multiplier,
        )

    unused_cash = round(available_budget - actual_cash_consumed, 2)
    return {
        "availableBudget": round(available_budget, 2),
        "contracts": contracts,
        "contractPrincipal": contract_principal,
        "entryFee": entry_fee,
        "actualCashConsumed": actual_cash_consumed,
        "unusedCash": unused_cash,
        "quantityGranularity": quantity_granularity,
        # Single-fill assumption -- see module docstring's "FEE ROUNDING"
        # section for why this repo can never claim exact multi-fill
        # accumulator awareness without real fill-level evidence.
        "feeRuleSource": FEE_RULE_SOURCE_ESTIMATED_AGGREGATED_ORDER,
        "feeMultiplier": multiplier,
    }


def reconstruct_whole_dollar_stake(displayed_initial_cost, price, *, candidates=None, tolerance=0.01, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Priority-4 fee-aware whole-dollar stake reconstruction (spec
    section 6): given a screenshot's displayed "Initial cost" and the
    executed price, finds every whole-dollar candidate stake S such that
    "the largest contract count affordable within $S" produces a
    computed initial cost within `tolerance` of the displayed one --
    modeling Kalshi's own "spend $S" order flow (see
    max_contracts_for_cash). This is NOT nearest-dollar rounding (spec
    section 7): it is a genuine reverse simulation of the order-entry
    economics, so it degrades correctly for larger stakes where naive
    rounding would be wrong (e.g. Initial cost $48.70 does not
    necessarily imply $49 -- it depends on how many whole contracts $49
    vs $50 vs $51 actually buys at this specific price).

    Returns a dict:
      {"status": "UNIQUE_MATCH", "stake": 10.0, "contracts": 23,
       "computedInitialCost": 9.82, "candidates": [10.0]}
      {"status": "MULTIPLE_MATCHES", "stake": None, "candidates": [12.0, 13.0]}
      {"status": "NO_MATCH", "stake": None, "candidates": []}

    NEVER auto-applied by this function itself -- it only reports what
    the economics are consistent with; the caller (see
    lib.edgelab.execution_economics.classify_stake_evidence) decides
    whether UNIQUE_MATCH is safe to apply automatically.

    `candidates` defaults to every whole dollar from $1 to $500 -- this
    repo's real historical stake sizes are all well within that range
    (see the historical audit); a caller with reason to believe a larger
    stake is possible may pass a wider range explicitly.
    """
    if displayed_initial_cost is None or price is None or not (0 < price < 1):
        return {"status": "NO_MATCH", "stake": None, "candidates": [], "matches": []}
    candidates = candidates if candidates is not None else [float(c) for c in range(1, 501)]

    matches = []
    for stake in candidates:
        contracts = max_contracts_for_cash(stake, price, fee_type=fee_type)
        if contracts <= 0:
            continue
        _, _, total_cost = cost_for_contracts(contracts, price, fee_type=fee_type)
        if abs(total_cost - displayed_initial_cost) <= tolerance:
            matches.append({"stake": stake, "contracts": contracts, "computedInitialCost": total_cost})

    if not matches:
        return {"status": "NO_MATCH", "stake": None, "candidates": [], "matches": []}
    if len(matches) > 1:
        return {
            "status": "MULTIPLE_MATCHES", "stake": None,
            "candidates": [m["stake"] for m in matches], "matches": matches,
        }
    only = matches[0]
    return {
        "status": "UNIQUE_MATCH", "stake": only["stake"],
        "candidates": [only["stake"]], "matches": matches,
        "contracts": only["contracts"], "computedInitialCost": only["computedInitialCost"],
    }


# ---------------------------------------------------------------------------
# Forward-looking reusable economics (spec section 21): net edge /
# break-even price for a PROPOSED wager. Pure functions only; not wired
# into any production recommendation/risk-gate path by this milestone
# (see docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md's "production
# betting behavior" section for why that is deliberately deferred).
# ---------------------------------------------------------------------------

def estimated_entry_fee_for_stake(stake, price, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Estimated entry fee for a PROPOSED (not-yet-placed) wager of
    `stake` dollars at `price` -- uses max_contracts_for_cash exactly
    like a real order-entry flow would, so this is consistent with the
    reconstruction/settlement math above, not a separate approximation.
    Returns (contracts, fee, net_cash_deployed).
    """
    if stake is None or price is None or not (0 < price < 1):
        return None, None, None
    contracts = max_contracts_for_cash(stake, price, fee_type=fee_type)
    contract_cost, fee, total = cost_for_contracts(contracts, price, fee_type=fee_type)
    return contracts, fee, total


def _per_contract_fee_rate(price, fee_type):
    """Continuous (unrounded) fee-per-contract rate f(price) = multiplier * price * (1-price) -- the same rate taker_fee's cent-rounding approximates for a specific integer contract count. Used only by the two reusable heuristics below, which intentionally reason in continuous rates rather than a specific contract count."""
    multiplier = FEE_MULTIPLIER_MAKER_DEFAULT if fee_type == FEE_TYPE_MAKER else FEE_MULTIPLIER_TAKER_STANDARD
    return multiplier * price * (1.0 - price)


def fee_adjusted_break_even_probability(price, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. The true win probability at which a YES position at `price`
    has zero NET expected value after the entry fee (assuming held to
    settlement, so no exit fee). Per-contract derivation: buying 1
    contract costs price + f(price) dollars (f = the continuous
    per-contract fee rate, see _per_contract_fee_rate) and pays $1 on a
    win, $0 on a loss. Expected profit = p*1 - (price + f(price)) = 0
    at break-even, so p_break_even = price + f(price) -- this result is
    scale-invariant (identical whether reasoned per-contract or per
    dollar staked), and net_expected_value_per_dollar below is built
    from the exact same per-contract cost so the two functions agree by
    construction (net EV is exactly 0 at this probability). Returns None
    for an invalid price.
    """
    if price is None or not (0 < price < 1):
        return None
    return round(price + _per_contract_fee_rate(price, fee_type), 6)


def fee_adjusted_bet_up_to_price(model_probability, *, fee_type=FEE_TYPE_TAKER, tolerance=1e-6):
    """
    Pure. The highest YES price at which `model_probability` still clears
    the fee-adjusted break-even bar (fee_adjusted_break_even_probability
    <= model_probability) -- i.e. inverts break_even(price) <= model_prob
    for price. break_even is monotonic increasing in price over (0, 1)
    for any fixed multiplier, so binary search on price is safe. Returns
    None when even price -> 0 already exceeds model_probability (no
    price clears the bar).
    """
    if model_probability is None or not (0 <= model_probability <= 1):
        return None
    lo, hi = 0.0001, 0.9999
    if fee_adjusted_break_even_probability(lo, fee_type=fee_type) > model_probability:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if fee_adjusted_break_even_probability(mid, fee_type=fee_type) <= model_probability:
            lo = mid
        else:
            hi = mid
    return round(lo, 4)


STANDARD_RESEARCH_ORDER_SIZES = (10.0, 25.0, 50.0, 100.0)
DEFAULT_RESEARCH_ORDER_SIZE = 10.0


def simulate_settlement_order(
    order_size, price, won, *, quantity_granularity=QUANTITY_GRANULARITY_UNKNOWN, fee_type=FEE_TYPE_TAKER,
):
    """
    Pure. Tier C ("realistic execution", correction pass spec section
    14C): the full decomposition for a HELD-TO-SETTLEMENT order, built
    on simulate_order() -- THE fix for the original bug (see module
    docstring's CORRECTION PASS section), which computed
    `net P/L = payout - order_size` even when the simulated order only
    actually consumed part of order_size. Here:
      netProfitLoss = grossSettlementPayout - actualCashConsumed
    NEVER `- availableBudget`, unless actualCashConsumed happens to equal
    it exactly. `won`: True/False/None (None -> push/void -- the full
    actualCashConsumed is refunded, netProfitLoss is exactly 0.0, not a
    guess).

    Returns simulate_order()'s dict extended with:
      "won", "grossSettlementPayout" (contracts * $1.00 on a win, $0.00
      on a loss, actualCashConsumed refunded on push/void -- Kalshi pays
      exactly $1/contract, no settlement fee), "netProfitLoss",
      "roiOnActualCashConsumed" (netProfitLoss / actualCashConsumed --
      return on capital genuinely at risk, the PRIMARY betting-
      performance ROI per spec section 16), "roiOnAllocatedBudget"
      (netProfitLoss / availableBudget -- a DIFFERENT, separately-named
      metric, never conflated with the one above).

    Returns None for invalid inputs, or when order_size can't even buy a
    single contract at this price (WHOLE_CONTRACT_ONLY/UNKNOWN mode).
    """
    sim = simulate_order(order_size, price, quantity_granularity=quantity_granularity, fee_type=fee_type)
    if sim is None:
        return None

    if won is None:
        result = dict(sim)
        result.update(won=None, grossSettlementPayout=sim["actualCashConsumed"], netProfitLoss=0.0,
                      roiOnActualCashConsumed=0.0, roiOnAllocatedBudget=0.0)
        return result

    if sim["contracts"] <= 0:
        return None

    gross_settlement_payout = float(sim["contracts"]) if won else 0.0
    net_pl = round(gross_settlement_payout - sim["actualCashConsumed"], 4)
    result = dict(sim)
    result.update(
        won=won, grossSettlementPayout=gross_settlement_payout, netProfitLoss=net_pl,
        roiOnActualCashConsumed=round(net_pl / sim["actualCashConsumed"], 6) if sim["actualCashConsumed"] else None,
        roiOnAllocatedBudget=round(net_pl / sim["availableBudget"], 6) if sim["availableBudget"] else None,
    )
    return result


def net_settlement_pl_for_order(order_size, price, won, *, quantity_granularity=QUANTITY_GRANULARITY_UNKNOWN, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Thin convenience wrapper around simulate_settlement_order()
    returning just its `netProfitLoss` (realistic execution, computed
    against actualCashConsumed -- see that function's docstring and the
    module docstring's CORRECTION PASS section for why this is NOT
    `payout - order_size`). Returns None if simulate_settlement_order
    does. Prefer simulate_settlement_order() directly when the caller
    needs the full decomposition (actualCashConsumed, unusedCash, both
    ROI denominators, etc.) rather than just the dollar P/L.
    """
    result = simulate_settlement_order(order_size, price, won, quantity_granularity=quantity_granularity, fee_type=fee_type)
    return result["netProfitLoss"] if result is not None else None


def net_settlement_pl_fee_only(order_size, price, won, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Tier B ("fee-only adjusted", correction pass spec section 14B):
    isolates the effect of the entry fee ALONE, using the exact SAME
    continuous/idealized exposure as the gross per-dollar formula
    (lib.edgelab.settlement.hypothetical_yes_return/hypothetical_no_return
    -- contracts = order_size / price, no integer-contract rounding, no
    unused-cash effect at all). This is deliberately scale-consistent
    with gross (spec section 18): grossPL uses the identical exposure
    this function starts from, so the ONLY difference between this and
    gross is the fee -- never sizing/rounding, which belongs to Tier C
    (simulate_settlement_order) instead.

    Derivation: contracts = order_size/price (continuous); the
    continuous per-contract fee rate is `multiplier*price*(1-price)`, so
    the total fee for this exposure is
    `multiplier*(order_size/price)*price*(1-price) = multiplier*order_size*(1-price)`
    -- charged regardless of win/loss (an entry fee is paid at trade
    time, not contingent on the outcome), so it's subtracted uniformly
    from the gross win/loss P/L. Returns None for invalid inputs;
    returns 0.0 for won=None (push/void, matching gross's own
    convention -- a voided/pushed order pays no fee and has no P/L).
    """
    if order_size is None or order_size <= 0 or price is None or not (0 < price < 1):
        return None
    if won is None:
        return 0.0
    multiplier = FEE_MULTIPLIER_MAKER_DEFAULT if fee_type == FEE_TYPE_MAKER else FEE_MULTIPLIER_TAKER_STANDARD
    gross_pl_per_dollar = (1.0 - price) / price if won else -1.0
    gross_pl = order_size * gross_pl_per_dollar
    fee = multiplier * order_size * (1.0 - price)
    return round(gross_pl - fee, 4)


def fee_only_drag_percentage_points(price, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. The exact fee-only ROI drag (in ROI fraction, e.g. 0.035 =
    3.5 percentage points) at `price`, independent of order size and of
    win/loss outcome -- algebraically, net_settlement_pl_fee_only's fee
    term divided by order_size is always `multiplier * (1 - price)`
    regardless of size or outcome (the win/loss-dependent gross term
    cancels out of the DRAG itself, only the fee term doesn't). This is
    the single number the price-bucket sanity table (spec section 20) is
    built from, and the number every "fee-only ROI" claim in a research
    report must be consistent with by construction.
    """
    if price is None or not (0 < price < 1):
        return None
    multiplier = FEE_MULTIPLIER_MAKER_DEFAULT if fee_type == FEE_TYPE_MAKER else FEE_MULTIPLIER_TAKER_STANDARD
    return round(multiplier * (1.0 - price), 6)


# ---------------------------------------------------------------------------
# Series-specific fee metadata (correction pass, spec section 6): a
# versioned lookup so a per-series fee_type/fee_multiplier override (from
# GET /series/{ticker} or GET /series/fee_changes, when real API access
# is ever available -- see module docstring §7/authenticated-API-
# unavailable) can be applied without a global assumption. This repo has
# NO authenticated Kalshi API access (confirmed: KALSHI_API_KEY unset, no
# orders/fills-reading code exists anywhere in this repo) and therefore
# NO evidence that any of the MLB series below actually has a
# non-standard fee_multiplier -- every entry defaults to the standard
# taker rate with feeRuleConfidence="ASSUMED_STANDARD_NO_OVERRIDE_EVIDENCE".
# Populating a real per-series override here (once API access exists)
# should set feeRuleConfidence="API_CONFIRMED" and feeEffectiveAt to the
# override's own scheduled_ts.
SERIES_FEE_METADATA = {
    ticker: {
        "seriesTicker": ticker, "feeType": "quadratic", "feeMultiplier": FEE_MULTIPLIER_TAKER_STANDARD,
        "feeEffectiveAt": None, "feeRuleConfidence": "ASSUMED_STANDARD_NO_OVERRIDE_EVIDENCE",
    }
    for ticker in (
        "KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5SPREAD",
        "KXMLBF5TOTAL", "KXMLBRFI", "KXMLBKS", "KXMLBOUTS", "KXMLBHIT", "KXMLBHRR", "KXMLBRBI", "KXMLBSB", "KXMLBTB",
    )
}


def fee_rule_for_series(series_ticker, *, at_timestamp=None):
    """
    Pure. Looks up SERIES_FEE_METADATA for `series_ticker`. Returns a
    dict with feeMultiplier/feeType/feeEffectiveAt/feeRuleConfidence, or
    the same shape with feeMultiplier=FEE_MULTIPLIER_TAKER_STANDARD/
    feeRuleConfidence="UNKNOWN_SERIES" for a series not in the registry
    at all (never raises, never silently defaults to 0). `at_timestamp`
    is accepted for forward compatibility with a future multi-entry
    historical-fee-change timeline per series (spec section 7's
    feeEffectiveAt/feeRuleSource/feeRuleConfidence) -- currently every
    registry entry has exactly one (current, unversioned) rule, so
    `at_timestamp` has no effect yet; it will matter once real
    scheduled_ts-versioned data is available.
    """
    entry = SERIES_FEE_METADATA.get(series_ticker)
    if entry is None:
        return {
            "seriesTicker": series_ticker, "feeType": "quadratic", "feeMultiplier": FEE_MULTIPLIER_TAKER_STANDARD,
            "feeEffectiveAt": None, "feeRuleConfidence": "UNKNOWN_SERIES",
        }
    return dict(entry)


def price_bucket_fee_sanity_table(prices=None, *, order_size=None, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Correction pass, spec section 20: a representative sanity table
    (default 10c-90c step 10c) making it visually obvious whether a
    claimed "fee-only drag" is even mathematically possible. For each
    price, at the standardized `order_size` (default
    DEFAULT_RESEARCH_ORDER_SIZE), reports the exposure assumption
    (contracts), contract principal, trade fee, net fee (== trade fee --
    no rounding-fee/rebate component is ever fabricated here, see module
    docstring's "FEE ROUNDING" section), fee as a % of contract
    principal, and the price-only fee-drag-in-percentage-points identity
    (fee_only_drag_percentage_points) -- the number every "fee-only ROI"
    claim in a research report must be consistent with.
    """
    order_size = order_size if order_size is not None else DEFAULT_RESEARCH_ORDER_SIZE
    prices = prices if prices is not None else [round(c / 100.0, 2) for c in range(10, 100, 10)]
    rows = []
    for price in prices:
        sim = simulate_order(order_size, price, fee_type=fee_type)
        drag = fee_only_drag_percentage_points(price, fee_type=fee_type)
        fee_pct_of_principal = (
            round(sim["entryFee"] / sim["contractPrincipal"] * 100, 4) if sim and sim["contractPrincipal"] else None
        )
        rows.append({
            "price": price,
            "orderSizeAssumption": order_size,
            "contracts": sim["contracts"] if sim else None,
            "contractPrincipal": sim["contractPrincipal"] if sim else None,
            "tradeFee": sim["entryFee"] if sim else None,
            "roundingFee": None,  # unknown -- no fill-level evidence, never fabricated as 0
            "netFee": sim["entryFee"] if sim else None,
            "feeAsPctOfContractPrincipal": fee_pct_of_principal,
            "feeOnlyDragPercentagePoints": round(drag * 100, 4) if drag is not None else None,
        })
    return rows


def net_expected_value_per_dollar(model_probability, price, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Expected net profit per $1 nominally staked at `price`, after
    the entry fee -- built from the SAME per-contract cost
    (price + f(price)) as fee_adjusted_break_even_probability, so this
    function is exactly 0 when model_probability equals that break-even
    probability (verified in tests/edgelab/test_kalshi_fees.py), never a
    separately-approximated formula that could quietly disagree with it.
    Per $1 staked, contracts bought = 1 / (price + f(price)); expected
    profit = model_probability * contracts * $1 - $1 (win case pays $1
    per contract; the $1 stake is already the cost basis, so a loss is
    simply -$1). Returns None for invalid inputs. This is the "after
    paying the expected cost of executing this wager, is the remaining
    edge still positive?" quantity from spec section 21 -- positive
    means yes.
    """
    if model_probability is None or price is None or not (0 < price < 1):
        return None
    effective_cost_per_contract = price + _per_contract_fee_rate(price, fee_type)
    contracts_per_dollar = 1.0 / effective_cost_per_contract
    net_ev = model_probability * contracts_per_dollar - 1.0
    return round(net_ev, 6)
