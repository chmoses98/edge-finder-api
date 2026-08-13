# Kalshi Fee-Aware Execution Economics milestone

Status: research/ledger-accuracy milestone. Does **not** change production
model probabilities, projection formulas, recommendation logic, bet/tier
thresholds, risk gates, bankroll sizing rules, or production market
selection — see "Production betting behavior" below for why that's a
deliberate, explicit scope boundary, not an oversight.

## Correction pass: unused allocated budget was incorrectly treated as lost capital

A second review of this milestone found a material bug in the very fee-
aware simulation this milestone had just built, and this section
documents it honestly rather than hiding the fact that the first
implementation was wrong.

**Old behavior.** `lib.edgelab.kalshi_fees.net_settlement_pl_for_order`
simulated a standardized order (e.g. $10.00), determined the largest
whole-contract count that budget could afford, computed the resulting
payout, and then returned `payout - order_size` — the **full** $10.00
budget, even when the whole-contract order only actually consumed part of
it (e.g. $9.84). The unspent $0.16 was silently treated as part of the
loss. `lib.edgelab.execution_economics.realized_pl_for_bet` (real-wager
settlement) had the identical bug: a WIN's net P/L was
`grossSettlementPayout - stake`, and a LOSS's net P/L was simply `-stake`
— both used the full stake/budget as the cash basis, never the amount
actually deployed.

**Why it was wrong.** Unused allocated cash never leaves the bettor's
account — it is not a fee, not slippage, not part of the wager's economic
exposure, and never a loss. Treating it as one systematically understated
every WIN's profit and overstated every LOSS's damage by the same unused-
cash amount, and contaminated the "net-of-fees" research metric with a
sizing/rounding effect that had nothing to do with fees at all.

**Affected reports/fields (all fixed in this pass).** Every
`hypotheticalYesReturnRealisticExecution`/`hypotheticalNoReturnRealisticExecution`
row field and the corresponding `roiRealisticExecution` bucket field in
`edge_backtest`'s output; every real REAL-wager `netProfitLoss` computed
by `lib.edgelab.settlement.settle_bets_for_ticker` going forward (existing
already-settled bets' stored `netProfitLoss` were **not** retroactively
rewritten — see the `$7.08` diagnostic discussion below and "No
retroactive settlement write yet").

**Corrected behavior.** `lib.edgelab.kalshi_fees.simulate_order` is now
the single source of truth for every order-level dollar computation,
returning the full decomposition —
`availableBudget` / `contractPrincipal` / `entryFee` /
`actualCashConsumed` (= `contractPrincipal + entryFee`) /
`unusedCash` (= `availableBudget - actualCashConsumed`, always ≥ 0, never
a loss) — and every net-P/L function in this module
(`simulate_settlement_order`, `net_settlement_pl_for_order`) computes net
P/L as `settlementCashReturned - actualCashConsumed`, never
`- availableBudget`. `lib.edgelab.execution_economics.realized_pl_for_bet`
was updated identically for real-wager settlement: `stake` remains the
user's *allocated/intended* budget (unchanged meaning — "I put $10 on it"
is still authoritative), but when exact `actualCashConsumed` evidence
isn't available, it's estimated via the same `simulate_settlement_order`
engine rather than assumed to equal the full stake.

**Did historical stake classifications change?** **No.** The stake-
evidence priority ladder and `lib.edgelab.kalshi_fees.reconstruct_whole_dollar_stake`
(which determines whether a screenshot's Initial cost uniquely
reconstructs to a whole-dollar stake) never depended on the buggy net-P/L
function at all — it only ever matched a candidate stake's simulated
`actualCashConsumed` against a displayed Initial cost, which was already
correct. All four previously auto-corrected stakes ($14.73→$15.00,
$19.70→$20.00, $14.73→$15.00, $14.76→$15.00) were explicitly re-audited
from a fresh, unreconciled baseline under the corrected code (not
grandfathered) and reached **identical** classifications and corrected
values — see "Re-audit of the four auto-corrected stakes" below. The
full 207-wager re-audit also reproduced identical counts (152 already
correct / 4 safe / 50 ambiguous / 1 source error).

**Did the $7.08 historical overstatement diagnostic change?** **Yes, and
it is not reproducible against today's corpus at all** — see "The $7.08
diagnostic, recomputed" (§8) below. The real-wager corpus grew during
this session (background capture/settlement jobs kept committing newly
settled real bets throughout), so the original $7.08 figure and any
recomputation are not snapshots of the same bet set; it is retired, not
patched. Recomputed from scratch against the full current corpus (207
REAL wagers, 117 settled: 52 WIN / 65 LOSS), the bug's own distortion —
first-pass-buggy total vs. corrected total — is **$27.38** (WIN side:
bug understated profit by $11.51; LOSS side: bug overstated losses by
$15.87, exactly the "pre-correction-pass LOSS formula" effect the
original $7.08 claim never examined, since that formula was previously
identical to the fee-free baseline).

**Did the 10%+ bucket's +4.54% → −2.02% figure survive?** **No, not as
originally labeled.** The −2.02% number was never a clean "fee-only"
measurement — it mixed genuine fee drag with unused-budget/whole-contract
sizing contamination, exactly the bug described above. See "10%+ edge
bucket, recomputed" for the corrected three-way decomposition (gross /
fee-only / realistic execution), each computed and named separately so
this conflation can't recur.

## 0. The bug, in one sentence (original milestone)

A Kalshi share-card screenshot's **"Initial cost"** — the actual whole-
contract cash debited, which is typically a few cents to a few dollars
*less* than what the user intended to risk (whole-contract rounding, and
possibly a trading fee) — was being recorded as canonical `stake`
whenever a screenshot was the evidence, silently understating what the
user actually said they bet.

## 1. The Colorado $10 acceptance case

Real example, from a Kalshi share card:

- Market: Colorado team total over 4.5 runs, YES @ approximately 41%
- Initial cost: **$9.80**
- Paid out: **$23.42**
- Card state: **CLOSED POSITION**

The user explicitly said: *"I put $10 on it."*

Required (and now proven, see `tests/edgelab/test_colorado_fixture.py`):

- canonical `stake` = **10.00**, never 9.80
- `shareCardEvidence.shareCardInitialCost` = 9.80, preserved raw, never
  overwriting `stake`
- `shareCardEvidence.shareCardPaidOut` = 23.42, preserved raw
- the $0.20 gap is **not** automatically labeled a fee (`entryFees`
  stays `null`, `feeStatus` stays `null`) — it could be a fee, or it
  could be unspent budget (a whole-contract order can't always deploy
  the full requested amount; the remainder never leaves the account)
- `CLOSED POSITION` does **not** automatically mean `$23.42` is a final
  $1/contract settlement payout — `executionStatus`/`exitSaleProceeds`/
  `grossSettlementPayout` all stay `null` until that's independently
  verified
- **once** verified (executionStatus=SOLD_EARLY, exitSaleProceeds=$23.42
  confirmed as real cash returned), the arithmetic is exactly
  `netProfitLoss = 23.42 - 10.00 = +13.42`, `realizedROI = 1.342` — see
  `test_once_semantics_are_verified_net_pl_and_roi_compute_correctly`.

## 2. Verified Kalshi fee semantics — and an honest sourcing caveat

**This environment's outbound network egress is policy-blocked for
`kalshi.com` and every third-party mirror tried** (`WebFetch` returned
`EGRESS_BLOCKED` for `kalshi.com`, `trading-api.readme.io`,
`www.botforkalshi.com`, `www.oddsshopper.com`, `pm.wiki`, and even
`en.wikipedia.org` — a blanket restriction on this tool in this sandbox,
not domain-specific). The authoritative
`kalshi.com/docs/kalshi-fee-schedule.pdf` could **not** be fetched and
read directly.

What follows is instead **cross-corroborated via two independent
WebSearch queries** (run August 2026), both citing the same current
public description of that PDF, and independently agreeing on every
figure:

```
fee_dollars = ceil_to_cent( 0.07 × contracts × price × (1 − price) )
```

- `price` in dollars (0–1), `contracts` a positive integer, rounded
  **up** to the next cent.
- The `0.07` **taker** multiplier applies to essentially every market
  category, sports included (politics/economics/weather/sports are all
  documented as using the same base case; a small number of premium
  categories like crypto reportedly use a higher multiplier — not
  relevant to this MLB-only repo).
- **Maker** orders (resting limit orders that don't fill immediately)
  use a much lower multiplier (0.0175) on a few "designated" series, and
  **most markets charge makers nothing at all**. No evidence any MLB
  series here is one of those designated series, so this repo's default
  maker multiplier is `0.0`.
- **No settlement fee exists.** Holding to settlement is free. Selling
  early is its own taker (or maker) trade and pays the same formula
  again, using the exit trade's own price/contract count — entry and
  exit fees are computed **independently**, never derived from one
  another.
- One search result's worked example — 100 contracts at $0.50 → $1.75 —
  is reproduced exactly by `lib.edgelab.kalshi_fees.taker_fee(100, 0.5)`,
  giving real (if not byte-verified-against-the-PDF) confidence in the
  formula.

Because this is WebSearch-synthesized rather than a direct, byte-
verified fetch, **every fee this system estimates is tagged
`FEE_STATUS_ESTIMATED_FEE_SCHEDULE`, never `ACTUAL_*`** — see §4's
taxonomy. `feeScheduleVersion` (`KALSHI_TAKER_STANDARD_2026_WEBSEARCH_CORROBORATED_V1`)
and `feeEffectiveDate` (a lower bound, `2026-07-07`, per a "July 2026
revision" a search result mentioned) are stamped on every fee this
system computes so a **future** session that regains the ability to
fetch `kalshi.com` directly can re-verify and, if anything changed, bump
the version — never silently.

Historical fee-schedule status uses the controlled vocabulary this
milestone defines in `lib.edgelab.kalshi_fees`:
`EXACT_HISTORICAL_RULE` / `EXACT_EXECUTION_RECEIPT` / `EXACT_API_FILL` /
`ESTIMATED_USING_DOCUMENTED_RULE` / `FEE_RULE_UNAVAILABLE`. Every fee
this milestone computed for a historical bet is
`ESTIMATED_USING_DOCUMENTED_RULE` — this system has no evidence of what
the fee formula was on any date before its own verification, so no
historical bet is ever evaluated under today's rule and labeled exact.

## 3. Canonical execution economics — exact field meanings

`stake` (existing field, description sharpened, **unchanged semantics**):
**total cash the user intentionally committed/risked** — the user's own
whole-dollar intent. If they say "$10", `stake = 10.00`, full stop, even
if a screenshot's Initial cost is $9.80. `cashStake` was considered as a
separate field name and rejected — `stake` already carries that exact
meaning; a second field would only diverge, never clarify.

`contractCost` (new): the raw dollar cost of the contracts themselves
(`contracts × averageFillPrice`), **excluding fees** — distinct from
`stake`. `stake` can legitimately exceed `contractCost + entryFees` by
an **unspent-budget remainder** that was simply never deployed (whole-
contract rounding) — that gap is not automatically a fee.

Full new field list on `PlacedBet` (all additive/nullable —
`data/edgelab/schema_v1/placed_bet.schema.json`):
`contractCost`, `averageFillPrice`, `entryFees`, `exitFees`, `totalFees`,
`grossCashReturned`, `grossSettlementPayout`, `exitSaleProceeds`,
`realizedROI`, `executionStatus`, `feeStatus`, `feeType`,
`feeMultiplier`, `feeSource`, `feeScheduleVersion`, `feeEffectiveDate`,
`economicsSource`, `economicsConfidence`, plus two structured objects:

- `shareCardEvidence` — **raw** Kalshi share-card facts exactly as
  displayed (`shareCardInitialCost`, `shareCardPaidOut`,
  `shareCardDisplayedProbability`, `shareCardPositionState`), never
  overwritten by any interpretation.
- `executionEconomicsReconciliation` — full correction provenance
  (previous/corrected stake, classification, reason, evidence source,
  method, `exactOrInferred`, `reconciledAt`) for a stake this milestone's
  reconciler actually changed. A **separate, purpose-built object** from
  the pre-existing `reconciliation` field (which is entryPrice-ambiguity-
  specific) — abusing that field for stake/fee semantics would confuse
  two unrelated concepts.

## 4. Stake-evidence priority ladder (never guess)

`lib.edgelab.execution_economics.determine_canonical_stake` — strict
priority, first non-null wins:

1. **User-confirmed** (`"I put $10 on it"`) — authoritative. A later
   screenshot never overwrites it.
2. **Exact API execution** — real Kalshi order/fill data identifying
   total cash debited (this repo has no authenticated read access to
   this today — see §7).
3. **Exact receipt** — a receipt that explicitly gives fee-inclusive
   cash outlay.
4. **Fee-aware whole-dollar reconstruction** — `lib.edgelab.kalshi_fees.
   reconstruct_whole_dollar_stake` simulates Kalshi's own "spend $S"
   order-entry flow (largest whole-contract count affordable within a
   candidate budget `S`, `lib.edgelab.kalshi_fees.max_contracts_for_cash`)
   for every candidate whole dollar, and only returns a stake when
   **exactly one** candidate's simulated cost lands within a strict
   tolerance ($0.01) of the displayed Initial cost. **Never simple
   nearest-dollar rounding** — a real regression test
   (`test_reconstruct_is_not_simple_nearest_dollar_rounding`) proves the
   reconstruction is driven by actual order-entry economics, which can
   legitimately disagree with naive rounding at larger stake sizes (the
   spec's own $48.70 → not-necessarily-$49 example).
5. **Ambiguous** — no evidence, or reconstruction found zero or multiple
   candidates. `stake` is `None`. **Never guessed.**

### The nightly-screenshot rule

**A Kalshi share-card "Initial cost" must never automatically populate
canonical `stake`.** This is enforced structurally: `build_manual_bet_record`
has no code path that reads `shareCardEvidence` into `stake` — the two
are entirely independent function arguments (see
`tests/edgelab/test_colorado_fixture.py::test_initial_cost_never_leaks_into_stake_field_on_the_written_record`).

## 5. Historical REAL-wager audit (`scripts/edgelab/reconcile_execution_economics.py`)

Read-only by default (`--dry-run` implicit; `--apply` to write). Every
REAL wager (`trackingType in (None, "REAL")`, not `CANCELLED` — this
repo's existing bankroll convention, matching `lib.edgelab.bankroll`)
classified as one of `EXACT_USER_CONFIRMED` / `EXACT_API_EXECUTION` /
`EXACT_RECEIPT` / `ALREADY_CORRECT` /
`SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE` /
`AMBIGUOUS_REQUIRES_USER_CONFIRMATION` / `INSUFFICIENT_EVIDENCE` /
`SOURCE_DATA_ERROR`.

**Corroboration before correction** (spec's explicit warning: don't fix
a cents-stake merely because it has cents): a non-whole-dollar stake
that repeats **identically across ≥3 bets with genuinely different
entryPrice values** is treated as `ALREADY_CORRECT` — a real
contract-cost/fee artifact would vary per market, not repeat an
identical value across wildly different prices. This repo's real corpus
has exactly this pattern: `$4.50` repeats 47 times, `$0.50` 10 times,
`$1.50` 6 times, all `LEGACY_BACKFILL`, spanning very different
entryPrice values — corroborating evidence of a genuine historical
stake convention, not a bug. **Left untouched.**

Results against this repo's real ledger (207 REAL wagers, run
2026-08-13):

| Classification | Count |
|---|---|
| `ALREADY_CORRECT` | 152 |
| `AMBIGUOUS_REQUIRES_USER_CONFIRMATION` | 50 |
| `SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE` (auto-applied) | 4 |
| `SOURCE_DATA_ERROR` | 1 (stake = $0.00 — reported, not "fixed") |

The 4 safe corrections (unique whole-dollar reconstruction matches):

| betId (prefix) | old stake | corrected stake |
|---|---|---|
| `f8acaaa40d` | $14.73 | **$15.00** |
| `8bab8482dc` | $19.70 | **$20.00** |
| `1d6ab17f1a` | $14.73 | **$15.00** |
| `2b130393f0` | $14.76 | **$15.00** |

Each also gained `contractCost`/`entryFees`/`contracts`/`averageFillPrice`
tagged `feeStatus=RECONSTRUCTED_EXACT` — the unique match pins those down
exactly, not merely as an estimate. **Idempotent** (verified: re-running
`--apply` three times in a row produces a byte-identical file after the
first run — `tests/edgelab/test_reconcile_execution_economics.py`).
`betId`/`importBatchId`/`sourceBetKey`/`recommendationId`/
`modelEvaluationId` are all preserved unchanged (same tests).

The **50 ambiguous rows are reported, never touched** — e.g.:

```
Aug 5 -- <bet> ... current ledger stake $11.83
Likely whole-dollar candidates: $11 / $12
No unique fee-aware reconstruction match within tolerance.
USER CONFIRMATION REQUIRED.
```

Full machine-readable audit:
`data/edgelab/analytics/latest_execution_economics_reconciliation.json`.

**An important, honest finding from this audit**: 3 of the 4 safely-
corrected bets already carried a manually-confirmed receipt
(`confirmedReceiptNetProfitLoss`) whose dollar amount matched the **old**
(uncorrected, cents) stake value exactly — e.g. a $14.73 loss receipt
matching a $14.73 old stake. This reconciler **never touches**
`confirmedReceiptNetProfitLoss`/`confirmedReceiptReturn` (protected,
evidence-based fields set only via `lib.edgelab.bets.confirm_realized_return`)
— and per that function's own existing precedence rule, a confirmed
receipt already outranks derived settlement math in every report, so
this discrepancy doesn't propagate anywhere. It's flagged here for
transparency, not "fixed," since the receipt is quite plausibly correct
in its own right — it likely reflects the amount actually **deployed**
(contract cost + fee) rather than the round-number amount **intended**,
which is exactly the `stake` vs. `contractCost` distinction this
milestone's schema was built to represent.

## 6. Fee-aware realized P/L (settlement)

`lib.edgelab.settlement.realized_return_for_bet` (the pre-existing
function) is **unchanged** — still correct for LOSS/PUSH/VOID, kept for
any caller that wants the simple historical formula.
`lib.edgelab.settlement.settle_bets_for_ticker` (the actual production
settlement path) now calls
`lib.edgelab.execution_economics.realized_pl_for_bet` instead, which is
**execution-status-aware**:

- **HELD_TO_SETTLEMENT** (default when `executionStatus` is unset —
  behavior-preserving for every bet settled before this milestone):
  `net = grossSettlementPayout − stake`, where
  `grossSettlementPayout = contracts × $1.00` on a WIN, `$0` on a LOSS
  (Kalshi pays exactly $1/contract, no settlement fee). `contracts` uses
  real evidence when known, otherwise a **fee-aware estimate**
  (`lib.edgelab.kalshi_fees.max_contracts_for_cash(stake, entryPrice)`)
  — a genuine improvement over the pre-milestone `stake / entryPrice`
  shortcut, which implicitly assumed zero fees and perfect fractional
  divisibility. LOSS is **unaffected** — forfeiting the full stake
  doesn't depend on contract count or fees.
- **SOLD_EARLY / PARTIAL_CLOSE**: `net = exitSaleProceeds − stake`.
  **Never** the win/loss settlement formula, even if the market
  eventually settles and `result`/`status` still record that objective
  outcome for context.
- **VOID_REFUND**: `net = 0.0` (full stake returned, no fee retained).

**Quantified historical impact of the WIN-case fix** (diagnostic only —
**not** applied to the stored ledger in this milestone; see below): among
this repo's 8 already-settled REAL WIN bets with no confirmed-receipt
override, the pre-milestone formula's total net P/L was **$55.33**; the
fee-aware formula's is **$48.25** — a **$7.08 aggregate historical
overstatement**. LOSS bets are provably unaffected (diff = $0.00 across
6 bets).

**This diagnostic was deliberately not written back into `bets.jsonl`.**
Doing so correctly requires re-running `scripts/edgelab/settle_markets.py`
for the affected tickers (which re-fetches each game's authoritative
outcome and re-invokes `settle_bets_for_ticker` — the existing
`bet_needs_settlement_update` idempotency check would then naturally
persist only the bets whose recomputed economics actually changed). That
is a distinct, larger operational action from *stake* reconciliation
(this milestone's actual scope) and was left as an explicit, mechanically
straightforward follow-up rather than bundled in here — see §8.

## 7. Authenticated Kalshi API — unavailable, and that's fine

`KALSHI_API_KEY` (used elsewhere in this repo only for public
market-data endpoints — candlesticks, market listings) is **not set** in
this environment, and no authenticated portfolio/orders/fills-reading
code exists anywhere in this repo (`tests/edgelab/test_no_automatic_wagering.py`
guards against ever adding order-placing capability). Per the spec: "If
authenticated history is unavailable, continue without it. Do not block
the milestone." Confirmed unavailable — the milestone proceeded using
Priorities 1/3/4/5 of the evidence ladder only (never 2).

## 8. Full-universe research fee-awareness

Gross hypothetical metrics (`hypotheticalYesReturn`/`hypotheticalNoReturn`,
`edge_backtest`'s `roi`/`grossROI`) are **completely unchanged** —
reproducibility preserved. **Correction pass:** the original milestone's
single `hypotheticalYesReturnNetOfFees`/`roiNetOfFees` pair (which
conflated fee drag with the unused-budget bug) has been **replaced** by
three explicitly separate, additive tiers — never call anything simply
"net of fees" in this codebase again, name the tier:

- **Tier A — Gross** (`roi`/`grossROI`, unchanged): no fees, no execution
  constraints, idealized continuous exposure.
- **Tier B — Fee-only** (`roiAfterFeesOnly`, `feeOnlyDragPercentagePoints`
  per bucket; `hypotheticalYesReturnFeeOnly`/`hypotheticalNoReturnFeeOnly`
  per row): holds exposure **exactly constant** with Tier A and subtracts
  **only** the legitimate Kalshi trading fee
  (`lib.edgelab.kalshi_fees.net_settlement_pl_fee_only`, closed-form
  `multiplier * (1 - price)` drag, independent of order size and outcome)
  — contains **zero** unused-budget or whole-contract-sizing penalty by
  construction.
- **Tier C — Realistic execution** (`roiRealisticExecution`,
  `executionDragPercentagePoints` per bucket;
  `hypotheticalYesReturnRealisticExecution`/
  `hypotheticalNoReturnRealisticExecution` per row): full platform
  constraints — whole-contract quantity granularity (or fractional, where
  known), fees, and **actual cash consumed** as the ROI denominator. This
  is the tier that used to be mislabeled "net of fees"; it is not
  fees-only, it is fees **plus** sizing/rounding effects, and
  `roiDenominatorNote` on every bucket says so explicitly.

`grossToFeeOnlyDrag` and `feeOnlyToExecutionDrag` (which sum to
`totalExecutionDrag`) decompose exactly which portion of the total gap is
pure fees vs. sizing/rounding. All driven by
`lib.edgelab.kalshi_fees.simulate_order`/`simulate_settlement_order`,
still at the standardized `DEFAULT_RESEARCH_ORDER_SIZE = $10`
(`STANDARD_RESEARCH_ORDER_SIZES = ($10, $25, $50, $100)` all supported)
— deliberately never a theoretical $1 stake, for the same fee-rounding-
distortion reason as the original milestone.

**Real finding from this repo's actual corpus** (13-date, regenerated
2026-08-13, corrected engine): the `10+` edge bucket's **gross** ROI is
**+4.54%** (n=168, 67 independent games, avg executable price 0.4995).
Its **fee-only** ROI is **+1.04%** (a genuine **3.50 pp** fee-only drag —
matches the price-bucket sanity table's ~3.5 pp figure at a ~50¢ average
price almost exactly, see §8a). Its **realistic-execution** ROI is
**+0.93%** — only **0.11 pp** *further* drag beyond fees alone, from
whole-contract sizing/rounding. In other words: **this bucket is fee-
negative-adjacent but not badly execution-broken** — a materially
different, and far more defensible, finding than the original pass's
conflated −2.02% figure. Full YES/NO decomposition and the "did this
figure survive" discussion: see §8d.

### 8a. Price-bucket fee sanity table (correction pass)

Fee-only drag at a standardized $10 allocation, taker side, standard
0.07 multiplier, across the 10¢–90¢ grid — the sanity check spec section
20 asked for, so a ~3.5 pp fee-only drag at ~50¢ is visibly explained by
the math rather than looking anomalous:

| Price | Contracts | Contract principal | Trade fee | Fee % of principal | Fee-only drag (pp) |
|------:|----------:|--------------------:|----------:|--------------------:|---------------------:|
| $0.10 | 94        | $9.40                | $0.60     | 6.38%                | 6.3                   |
| $0.20 | 47        | $9.40                | $0.53     | 5.64%                | 5.6                   |
| $0.30 | 31        | $9.30                | $0.46     | 4.95%                | 4.9                   |
| $0.40 | 23        | $9.20                | $0.39     | 4.24%                | 4.2                   |
| $0.50 | 19        | $9.50                | $0.34     | 3.58%                | 3.5                   |
| $0.60 | 16        | $9.60                | $0.27     | 2.81%                | 2.8                   |
| $0.70 | 13        | $9.10                | $0.20     | 2.20%                | 2.1                   |
| $0.80 | 12        | $9.60                | $0.14     | 1.46%                | 1.4                   |
| $0.90 | 11        | $9.90                | $0.07     | 0.71%                | 0.7                   |

(`roundingFee`/`rebate` columns omitted — always `None`/unmodeled per
the `ROUNDING_SEQUENCE_UNAVAILABLE` fee-rule-source honesty rule; no
per-fill sequence is available to simulate the accumulator exactly.)
Fee-only drag is symmetric in `price` and `1-price` by the closed-form
identity, which is why the 10%+ bucket's ~49.95¢ average price producing
a ~3.50 pp fee-only drag is expected, not suspicious — it is *not*, on
its own, evidence of the kind of 6.56 pp drag the original −2.02% figure
implied.

### 8b. Re-audit of the four auto-corrected historical stakes

Re-run from a fresh, unreconciled baseline (`git show
main:data/edgelab/bets/bets.jsonl`, not the branch's already-corrected
state) under the corrected reconciler — genuinely re-evaluated, not
grandfathered:

| betId (truncated) | Old stake | Corrected stake | actualCashConsumed | unusedAllocatedCash | Classification |
|---|---:|---:|---:|---:|---|
| `f8acaaa4…` | $14.73 | $15.00 | $14.72 | $0.28 | SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE |
| `8bab8482…` | $19.70 | $20.00 | $19.70 | $0.30 | SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE |
| `1d6ab17f…` | $14.73 | $15.00 | $14.73 | $0.27 | SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE |
| `2b130393…` | $14.76 | $15.00 | $14.75 | $0.25 | SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE |

All four reached **identical** corrected-stake values to the original
pass. This is expected, not a coincidence: `reconstruct_whole_dollar_stake`
only ever matched a candidate stake's simulated `actualCashConsumed`
against the displayed Initial cost — a computation the P/L bug never
touched. The full 207-real-wager re-audit under the corrected classifier
reproduced identical counts: 152 `ALREADY_CORRECT`, 4
`SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE`, 50
`AMBIGUOUS_REQUIRES_USER_CONFIRMATION`, 1 `SOURCE_DATA_ERROR`. None of
the four required reverting.

### 8c. The $7.08 diagnostic, recomputed

The original milestone's headline claim — "historical settled real
wagers were overstated by $7.08" — is **not reproducible** against
today's corpus and is retired rather than patched, for an honest,
identifiable reason: the real-wager corpus grew during this session
(background `edgelab capture`/settlement jobs kept committing new
settled real bets throughout, per the commit log), so the original
figure and any recomputation are not snapshots of the same bet set. It
is not being "corrected" to a nearby number; it is being replaced with a
number derived from scratch against the corpus as it exists now.

Recomputed from scratch, full scope, no cherry-picking: all 207 REAL
wagers, of which **117 are settled WIN/LOSS with known stake and entry
price** (52 WIN / 65 LOSS) — every settled real wager in the current
corpus, not a subset:

| Formula | WIN total (n=52) | LOSS total (n=65) | Combined |
|---|---:|---:|---:|
| Fee-free baseline (pre-milestone, no fee model at all) | $547.14 | −$741.26 | −$194.12 |
| First-pass buggy (fee-aware, but charges full stake/budget as basis) | $511.14 | −$741.26 | −$230.12 |
| Corrected (this pass, charges `actualCashConsumed`) | $522.65 | −$725.39 | −$202.74 |

Two separate, non-conflated findings:

1. **Genuine fee cost** (fee-free vs. corrected): the real-money corpus
   paid an estimated **$8.62** in aggregate Kalshi trading fees relative
   to a no-fee world (WIN side: $24.49 fee-related shortfall; LOSS side:
   the fee-free and corrected LOSS totals only diverge through the
   unused-cash effect below, not fees directly, since a LOSS forfeits the
   `actualCashConsumed` amount either way).
2. **The bug's own distortion** (first-pass buggy vs. corrected) — this
   is the number that answers "how much did the unused-cash bug alone
   overstate historical losses": **$27.38** total (WIN side: the bug
   understated profit by $11.51; LOSS side: the bug overstated losses by
   $15.87, since it charged the full stake instead of the smaller amount
   actually consumed).

Labeled **ESTIMATED**: the overwhelming majority of these 117 settled
real wagers have `feeRuleSource = ESTIMATED_AGGREGATED_ORDER` (no
authenticated Kalshi fill/fee history is available in this environment —
see §7), so these are the fee-schedule's best documented estimate, not
receipts. **Not retroactively applied**: no stored `bets.jsonl`
`netProfitLoss` value was rewritten by this diagnostic — see "No
retroactive settlement write yet" in §11.

### 8d. 10%+ edge bucket, recomputed

The original pass's single +4.54% → −2.02% "net of fees" comparison is
retired in favor of the three-tier decomposition (§8), computed against
the same regenerated 13-date corpus (n=168, 67 independent games):

**Combined (YES+NO):** grossROI +4.54%, feeOnlyROI +1.04% (drag 3.50 pp),
realisticExecutionROI +0.93% (additional drag 0.11 pp beyond fees) — avg
fee $0.334 per $10 allocated (3.43% of actual cash consumed), avg unused
cash $0.28 per $10 allocation.

**YES side (n=34, 28 independent games):** grossROI −7.20%, feeOnlyROI
−10.98% (drag 3.78 pp), realisticExecutionROI −10.70% (execution
partially *offsets* fee drag by 0.28 pp here — whole-contract rounding
happened to favor this side's small sample).

**NO side (n=134, 65 independent games):** grossROI +7.52%, feeOnlyROI
+4.08% (drag 3.43 pp), realisticExecutionROI +3.88% (additional 0.20 pp
drag).

The original −2.02% figure does **not** survive as labeled: it mixed a
genuine ~3.5 pp fee-only drag with an unused-budget/rounding artifact
that had nothing to do with fees, and it was never broken out by side.
The corrected fee-only drag (~3.5 pp) is fully explained by §8a's sanity
table at this bucket's ~50¢ average price — no anomaly remains. This
bucket, still exploratory (n=168 across only 67 independent games, no
out-of-sample validation), now has a defensible, side-aware,
methodologically-clean decomposition instead of a single conflated
number.

## 9. Net edge / break-even (forward-looking, reusable, **not wired into production**)

`lib.edgelab.kalshi_fees` also provides:
`fee_adjusted_break_even_probability(price)` (always strictly above raw
price — there's no free edge), `fee_adjusted_bet_up_to_price(model_prob)`
(inverts the above), `net_expected_value_per_dollar(model_prob, price)`
(exactly 0 at break-even by construction — proven in
`tests/edgelab/test_kalshi_fees.py`). These answer "after paying the
expected cost of executing this wager, is the remaining edge still
positive?" — but are **pure, standalone functions not called from
`scripts/build_market_ledger.py`, `scripts/risk_gate.py`, or any
production recommendation path**. See §11.

## 10. Audit of existing "post-friction" language

**Does the existing model's "friction" explicitly calculate actual
Kalshi transaction fees? NO.**

Traced `scripts/build_market_ledger.py`'s `calibratedEdgeVsExecutable`
(the field every "post-friction" comment refers to) to its actual
formula: `raw_edge_pct(model_prob, executable_price) * cal_factor`, where
`cal_factor` (`CAL_HIGH=0.187`, `CAL_MEDIUM=0.255`, `CAL_PAPER=0.18`) is
a **statistical calibration/safety haircut** — a multiplier shrinking the
raw model-vs-market gap toward zero, presumably derived from historical
over/under-confidence, not a dollar transaction cost. The `executable_price`
half of "friction" (using the ask instead of the vig-free mid) **does**
capture real bid/ask-spread cost of entering a position — but that's
slippage, not Kalshi's per-contract trading fee, and no Kalshi fee dollar
amount is computed anywhere in this pipeline. Kept as **four explicitly
distinct concepts**, never conflated: calibration haircut (`cal_factor`),
bid/ask slippage (ask vs. mid), actual projected Kalshi transaction fee
(this milestone's new `lib.edgelab.kalshi_fees`), and final net edge
(§9's reusable functions — not wired in).

## 11. Production betting behavior — explicitly unchanged

This milestone builds and validates the fee-aware net-EV engine (§9) but
**does not** wire it into `scripts/build_market_ledger.py`'s
`calibratedEdgeVsExecutable`/qualification gates or `scripts/risk_gate.py`.
Confirmed via `git diff --stat` against both files: **empty** — neither
was touched.

This is deliberate, not an oversight: comparing current recommendation
thresholds against fee-aware net EV would very plausibly change which
markets qualify as bettable, and shipping that silently inside a
historical-reconciliation PR is exactly the kind of "poorly validated
live gate" the milestone spec explicitly warned against introducing. The
user does want future bet selection to eventually account for
transaction costs — that belongs in a **separate, clearly-labeled,
extensively-tested follow-up PR** (stacked on this one or independent),
with an explicit before/after comparison of which historical
recommendations would have flipped qualification status under a
fee-aware gate. Not attempted here.

## 12. Postmortem / screenshot workflow — for future ChatGPT → Claude handoffs

The fastest nightly recap path — sending a screenshot of each Kalshi
position after the games — is fully preserved. The canonical importer
(`scripts/edgelab/import_bet_batch.py`) never requires order IDs, fill
IDs, ticker IDs, placement timestamps, exact fee amounts, or contract
counts for an ordinary import; a plain-language report like *"Colorado TT
over 4.5, $10, 41¢"* still imports with **zero** fee-related fields
supplied (`tests/edgelab/test_import_bet_batch.py::test_normal_chat_report_imports_with_no_fee_details_at_all`).

Going forward, keep these four concepts distinct in every handoff and
every import:

| Concept | Meaning | Field |
|---|---|---|
| **User stake** | Whole-dollar cash the user says they committed | `stake` |
| **Share-card Initial cost** | Contract-cost display — **NOT** stake | `shareCardEvidence.shareCardInitialCost` |
| **Paid out** | Raw display value until its semantics are established | `shareCardEvidence.shareCardPaidOut` |
| **Fee** | Actual if known, estimated if modeled, unresolved if unknown | `entryFees`/`exitFees` + `feeStatus` |

The importer's optional `shareCardEvidence`/`executionEconomics` row
fields exist for when a screenshot **is** available — passed straight
through, stored verbatim, never used to infer `stake`. Fees/economics
can always be reconciled later, from screenshot or (if ever available)
API evidence, via `scripts/edgelab/reconcile_execution_economics.py` —
never blocking the nightly import itself.
