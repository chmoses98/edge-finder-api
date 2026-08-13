"""
lib/edgelab/kalshi_fees.py
==============================
Kalshi Fee-Aware Execution Economics milestone: the ONE reusable,
pure-function fee/execution engine every other module in this milestone
(historical reconciliation, realized settlement P/L, full-universe
research, and any future production net-EV gate) calls into, so the fee
formula is defined and versioned exactly once.

Fee formula source (spec section 3 -- "verify from authoritative
sources, do not copy an approximate formula from memory or a blog"):
this environment's outbound network egress is policy-blocked for
kalshi.com and every third-party mirror (confirmed: WebFetch returns
EGRESS_BLOCKED for kalshi.com, trading-api.readme.io,
www.botforkalshi.com, www.oddsshopper.com, pm.wiki, and even
en.wikipedia.org -- this is a blanket WebFetch restriction in this
sandbox, not a domain-specific one), so the authoritative
kalshi.com/docs/kalshi-fee-schedule.pdf could NOT be fetched and read
directly. The formula below is cross-corroborated (two independent
WebSearch queries, run August 2026, agreeing on every figure) from
multiple current public sources describing that same PDF's contents:
  fee_dollars = ceil_to_cent(0.07 * contracts * price * (1 - price))
  - price is in dollars (0-1), contracts is a positive integer.
  - The 0.07 "taker" multiplier applies to essentially all market
    categories including sports (politics/economics/weather/sports all
    documented as using the same 0.07 base case); a small number of
    premium categories (e.g. crypto) reportedly use a higher multiplier
    -- not relevant to this repo's MLB-only markets.
  - "Maker" orders (resting limit orders that do not fill immediately)
    use a much lower multiplier (0.0175) on a few "designated" series,
    and MOST markets charge makers nothing (multiplier 0). This repo has
    no evidence any MLB series is one of the "designated" maker-fee
    series, so the default maker multiplier used here is 0.0
    (FEE_MULTIPLIER_MAKER_DEFAULT) unless a specific record's evidence
    says otherwise.
  - No separate settlement fee exists -- holding a contract to
    settlement is free. Exiting a position early (selling before
    settlement) is its own taker (or maker) trade and pays this same
    formula AGAIN, using the EXIT trade's own execution price and
    contract count -- entry and exit fees are independent, never one
    computed from the other.

Because this was corroborated via WebSearch synthesis rather than a
direct, byte-verified fetch of Kalshi's own PDF, every fee this module
estimates is tagged FEE_SOURCE_ESTIMATED_USING_DOCUMENTED_RULE, never
FEE_SOURCE_ACTUAL_* -- see the fee-status taxonomy below. If a future
session regains the ability to fetch kalshi.com directly, re-verify this
docstring's formula against the live PDF and bump FEE_SCHEDULE_VERSION
if anything changed.

Historical fee-schedule awareness (spec section 3): this module has no
evidence of what the formula was on any date before this milestone's own
verification (August 2026) -- FEE_SCHEDULE_VERSION is a single current
snapshot, not a historical timeline. A bet is never evaluated under
today's schedule and labeled EXACT_HISTORICAL_RULE; every reconstruction
this module performs for a historical bet is explicitly
ESTIMATED_USING_DOCUMENTED_RULE, carrying feeScheduleVersion/
feeEffectiveDate so a future correction to the historical record is
possible without guessing which schedule applied.
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

# Historical fee-schedule-applicability taxonomy (spec section 3).
FEE_RULE_EXACT_HISTORICAL_RULE = "EXACT_HISTORICAL_RULE"
FEE_RULE_EXACT_EXECUTION_RECEIPT = "EXACT_EXECUTION_RECEIPT"
FEE_RULE_EXACT_API_FILL = "EXACT_API_FILL"
FEE_RULE_ESTIMATED_USING_DOCUMENTED_RULE = "ESTIMATED_USING_DOCUMENTED_RULE"
FEE_RULE_FEE_RULE_UNAVAILABLE = "FEE_RULE_UNAVAILABLE"


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


def net_settlement_pl_for_order(order_size, price, won, *, fee_type=FEE_TYPE_TAKER):
    """
    Pure. Retrospective (spec section 19-20): simulates spending
    `order_size` dollars at `price` (via max_contracts_for_cash, the same
    order-entry simulation used everywhere else in this milestone), then
    computes the resulting net P/L for a HELD-TO-SETTLEMENT result --
    `won` True/False/None (None -> push/void, net 0.0). This is
    deliberately NOT a per-$1 formula: fee rounding depends on the real
    integer contract count a given order size actually buys (spec
    section 20 -- "do not let a $1 theoretical stake distort fees if fee
    rounding makes that economically unrealistic"), so a caller MUST
    supply a realistic standardized order_size (see
    STANDARD_RESEARCH_ORDER_SIZES) rather than assuming linearity.
    Returns None for invalid price/order_size, or when order_size can't
    even buy a single contract at this price.
    """
    if order_size is None or order_size <= 0 or price is None or not (0 < price < 1):
        return None
    if won is None:
        return 0.0
    contracts = max_contracts_for_cash(order_size, price, fee_type=fee_type)
    if contracts <= 0:
        return None
    payout = float(contracts) if won else 0.0
    return round(payout - order_size, 4)


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
