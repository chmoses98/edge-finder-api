# KALSHI EXECUTABLE PAYOUT / DOUBLE-FEE AUDIT

Status: **research integrity audit, complete.** No production betting policy
changed. PR #179 (`claude/mlb-alpha-0002-capture-activation`) was **not**
merged. Alpha discovery was not resumed. This document is the final report
requested by the audit program; supporting artifact:
`data/edgelab/research_artifacts/executable_payout_audit/field_semantics_manifest.json`.
New regression coverage: `tests/edgelab/test_executable_payout_audit.py`.

Method: five independent read-only research agents traced every economics
call site on `main` and on PR #179's head (`cb8aad91`, fetched locally),
covering (1) a repo-wide double-fee pattern search across `lib/` and
`scripts/`, (2) all 18 named MLB research projects, (3) the real bet
ledger / stake-evidence semantics, (4) the production recommendation
display layer, and (5) the C01-PIT holdout + maker-feasibility economics
row-by-row. Findings below are the synthesis, cross-checked directly
against `lib/edgelab/kalshi_fees.py`, `lib/edgelab/execution_economics.py`,
`lib/edgelab/settlement.py`, `scripts/build_market_ledger.py`,
`docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md`, and
`docs/PRODUCTION_FEE_AWARE_NET_EV.md`.

---

## A — USER ECONOMICS

1. **Canonical meaning of "$10 bet"**: `stake` = the user's whole-dollar
   intended cash allocation. "I put $10 on it" means `stake = 10.00`, full
   stop, even when Kalshi's own displayed "Initial Cost" is a few cents to
   a few dollars less (`lib/edgelab/execution_economics.py:52-107`,
   `determine_canonical_stake`).
2. **Authoritative win/loss math**: for an all-in `$S` wager with a known
   total winning payout `$P`: `win P/L = P - S`, `loss P/L = -S`, full
   stop — the internal split of `S` into contract principal and fee never
   changes this. Proven for the user's own worked example
   (`tests/edgelab/test_executable_payout_audit.py::test_example_a_*`):
   stake $10, payout $17.52 → **+$7.52** win / **-$10.00** loss.
3. **Meaning of Initial Cost**: `shareCardEvidence.shareCardInitialCost`
   — Kalshi's own whole-contract cash debit (`contractPrincipal +
   entryFee`), typically less than the user's intended stake because a
   whole-contract order can't always deploy every last cent of a budget.
   It is **never** automatically canonical `stake` — structurally
   enforced (`build_manual_bet_record` has no code path from
   `shareCardEvidence` into `stake`;
   `tests/edgelab/test_colorado_fixture.py::test_initial_cost_never_leaks_into_stake_field_on_the_written_record`).
4. **Meaning of fees**: the Kalshi trading fee, `ceil_cent(multiplier *
   contracts * price * (1-price))`, charged once per order/fill,
   independent of win/loss. Never a settlement fee (settlement itself is
   free). This repo has no authenticated Kalshi API access, so every
   historical/estimated fee is tagged `FEE_STATUS_ESTIMATED_FEE_SCHEDULE`
   unless a real receipt overrides it.
5. **Does the actual payout already incorporate the fee effect? Yes.**
   Once a wager's actual/reconstructed total winning payout is known
   (`grossSettlementPayout`, `exitSaleProceeds`,
   `confirmedReceiptNetProfitLoss`), the fee is already baked into the
   $10-vs-$17.52 relationship — there is no separate fee dollar amount
   left over to subtract.

## B — REPOSITORY ECONOMICS STATES

6. **Observed execution representation**: `confirmedReceiptNetProfitLoss`
   / `exitSaleProceeds` / a real fill/receipt. Used directly
   (`lib/edgelab/settlement.py:317-330`, precedence over derived math).
7. **Reconstructed execution representation**:
   `kf.reconstruct_whole_dollar_stake` / `kf.simulate_order` /
   `kf.simulate_settlement_order` output —
   `availableBudget/contracts/contractPrincipal/entryFee/actualCashConsumed/unusedCash/netProfitLoss`.
   Used as the final all-in number once computed.
8. **Raw quote representation**: a bare `yesAsk`/`noAsk`/`executedPrice`.
   Carries no fee, no sizing. The *only* state where the fee/order-sizing
   engine should run.
9. **Conversion rules**: STATE 3 → STATE 2 happens exactly once, inside
   `simulate_order`/`simulate_settlement_order`/`net_settlement_pl_for_order`/
   `net_settlement_pl_fee_only`/`fee_adjusted_break_even_probability`.
   Once a value is STATE 1 or STATE 2 (i.e. already carries
   `actualCashConsumed` or an observed payout), no further fee subtraction
   may be applied to it. Full field-by-field classification:
   `data/edgelab/research_artifacts/executable_payout_audit/field_semantics_manifest.json`.

## C — BUGS

10. **Unused-budget bug**: **Found and already fixed**, entirely within
    PR #88 (`claude/kalshi-fee-aware-execution-reconciliation`), before
    ever reaching `main` unfixed. Introduced at commit `596df376`
    ("Kalshi fee-aware execution economics..."), fixed 1h44m later in the
    same branch at commit `79c50074` ("Correction pass: stop treating
    unused allocated budget as lost capital"), merged as PR #88 on
    2026-08-14. Documented exhaustively in
    `docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md`'s "Correction pass"
    section. Quantified distortion (diagnostic only, never applied
    retroactively to the ledger): the bug alone would have overstated
    historical losses/understated profit by **$27.38** across 117 settled
    real wagers, had it shipped.
11. **Double-fee bug found: NO.** Zero instances found across a
    repo-wide, two-branch (`main` + PR #179) trace of every economics
    call site, all 18 named research projects, the production
    qualification/Bet-Up-To layer, the C01-PIT holdout, and the maker
    feasibility study. See Section E's key table and the per-project
    detail in Section D/F/G below.
12. **Initial Cost/stake bug found: NO**, as a *code* bug — the priority
    ladder and structural non-leak are intact and verified against
    current code, not just docs. **However**, a **staleness gap** was
    found: the last full 207-wager reconciliation run
    (`reconcile_execution_economics.py`, 2026-08-13T22:14Z) has never
    been re-run against the ledger's growth since — see Section H.
13. **Fractional-sizing issue**: not a bug, a *documented limitation*.
    `QUANTITY_GRANULARITY_UNKNOWN` is the repo-wide default (this repo's
    archived Kalshi data discards the raw fixed-point fields needed to
    know per-market fractional-order capability), and the engine falls
    back to a conservative whole-contract simulation rather than
    fabricating fractional capability. This is a distinct concern from
    double-fee accounting and does not change any of this audit's
    findings — it only affects how *unused cash* is distributed, not
    whether a fee is applied more than once.
14. **Exact affected code paths for bugs 10/12**: `lib/edgelab/kalshi_fees.py`
    (`simulate_order`, `net_settlement_pl_for_order`, fixed);
    `lib/edgelab/execution_economics.py` (`realized_pl_for_bet`, fixed);
    `data/edgelab/bets/bets.jsonl` (the ledger itself — 4 stakes
    corrected, 205 newer real wagers unreconciled, see Section H).

## D — REPO-WIDE DOUBLE-FEE SEARCH — RESULT

**Zero `DOUBLE_FEE_BUG` instances found.** Every traced call site that
starts from an already fee-inclusive number (`netPlBuyYes`/`netPlBuyNo`,
`actualCashConsumed`, `netProfitLoss`, `netExecutableEdge`,
`confirmedReceiptNetProfitLoss`) only ever *reads* it downstream (sum,
mean, bootstrap, ledger write) — never re-subtracts a fee from it. Every
call site that starts from a raw price (`yesAsk`/`noAsk`/executed price)
runs it through the canonical engine exactly once. Full call-graph
detail (file:line) is in the five agents' underlying evidence and
summarized per-project in Section G below. Two structural guardrails
already exist and pass: `test_item28_no_fee_double_counting_across_pipeline`
(source-scan: `scripts/risk_gate.py`/`scripts/write_pending_bets.py`
contain zero occurrences of the fee engine — they only read precomputed
fields) and `test_item29_no_calibration_double_counting`.

## E — VALID VS. DOUBLE-COUNTING — KEY TABLE

| Research conclusion | Input type | Old economics | Correct economics | Double fee? | Conclusion changes? |
|---|---|---|---|---|---|
| PR #88 unused-budget diagnostic | Reconstructed (stake sim) | `payout - stake` (full budget) | `payout - actualCashConsumed` | No (this is the *unused-budget* bug, not double-fee) | Diagnostic-only figures corrected ($27.38); no ledger rewrite; not a bettability flip |
| 10%+ edge bucket (PR #88 §8d) | Raw quote ($10 std. allocation) | Single conflated "net of fees" −2.02% | 3-tier: gross +4.54% / fee-only +1.04% / realistic-exec +0.93% | No | Label/decomposition changed, sign did not (still fee-negative-adjacent, not "broken") |
| Production `netExecutableEdge` gate | Raw executable (ask) price | `calibratedEdgeVsExecutable` (fee-blind) | fee-adjusted break-even shift, once, then same `cal_factor` applied once | No (tested: `test_item28`/`test_item28b`/`test_item29`) | 528 causal opportunities: 256→223 qualifiers (33 rejected by fee, 31 tier-downgraded) — intentional, documented behavior change, not a bug |
| MLB-ALPHA-0002 Family C / C01-F5REV / C02-OFI | Raw ask (`build_candle_panel.py`) | n/a (new research) | `net_settlement_pl_for_order(ORDER_USD, rawAsk, result)` once | No | C02-OFI: correctly `NOT_TAKER_TRADABLE`; C01-F5REV: correctly labeled unproven (p=0.089) |
| C01-PIT spent holdout (21 opportunities) | Reconstructed ($10 std. allocation) | n/a | `$202.07` deployed = Σ`actualCashConsumed`; `-$0.07` net = Σ(`payout - actualCashConsumed`) | No (independently re-derived byte-for-byte from `kalshi_fees.py`) | Holdout verdict (`INCONCLUSIVE`) unchanged, as required |
| Maker feasibility (25-contract fixed) | Raw ask, fixed-contract | n/a | `netProfitLoss = payout(25) - (25*price + makerFee)` once | No | "Passive worse than crossing the spread" finding unchanged |
| Hitter validation / RFI / F5 | Raw archived price | n/a | `net_settlement_pl_fee_only`/`taker_fee` once per row | No | No conclusion changes |
| Team totals (RSCH-0034/0035) | N/A — predictive gate failed first | n/a | No fee-adjusted P/L ever computed (capacity-only, correctly) | N/A | N/A |
| Promotion/demotion (`promotion_engine.py`) | Observed (`bets.json "pl"`, user-entered) | n/a | Pure pass-through sum, no fee code in this file at all | No (nothing to double-count; upstream `"pl"` is user-reported, not fee-derived) | N/A |

## F — MLB-ALPHA-0001

20. **C01** (discovery): `family_a_discovery.py`'s `row_side_econ()`
    builds three genuinely independent tiers (gross / fee-only / net)
    from a raw $10 order-size assumption, each with the fee applied
    exactly once where applicable. `netROI (3.31%) > feeOnlyROI (2.72%)`
    is legitimate — the two use different denominators (full exposure vs.
    `actualCashConsumed`), not evidence of a bug.
21. **C01-PIT**: development/discovery scripts
    (`score_c01_pit_discovery.py`, live shadow writer
    `lib/edgelab/mlb_alpha_shadow.py`) reuse the same single-fee
    `row_side_econ`/`max_contracts_for_cash`+`taker_fee` calls; shadow
    rows carry no outcome field yet, so there is no P/L to double-fee.
22. **Holdout** (21 opportunities, spent, `INCONCLUSIVE`, permanently
    unchanged): independently re-derived row-by-row from
    `data/edgelab/research_artifacts/mlb_alpha_0001/holdout_result.json`
    against the canonical `kalshi_fees.simulate_order` engine — **0
    mismatches** across all 21 rows. `$202.07` deployed is the sum of
    `actualCashConsumed` (never the $210 nominal allocation, which is
    reported separately and correctly labeled `modeledStakeUsd`).
    `-$0.07` net P/L is the sum of `payout - actualCashConsumed`, fee
    embedded exactly once per row. **Both headline numbers are correct
    as reported — no OLD/CORRECTED revision needed.**

## G — MLB-ALPHA-0002

23. **Family C** (microstructure): `build_candle_panel.py` computes
    `netPlBuyYes`/`netPlBuyNo` once per side from raw ask prices via
    `net_settlement_pl_for_order` — this is exactly the pattern
    "preliminary CEO review" in the audit program flagged as probably
    valid, and it **is** valid: input is a raw ask, $10 is a cash budget,
    fee applied exactly once. `family_c_microstructure.py` and every
    sibling (`family_d_multibook.py`, `family_e_residual.py`,
    `family_t_topology.py`, `family_d_leadlag.py`) only read this
    precomputed field afterward (sum/bootstrap), never re-fee it.
    **CONFIRMED: MLB-ALPHA-0002 TAKER ECONOMICS DID NOT DOUBLE-COUNT
    FEES.**
24. **C01-F5REV**: reuses the same precomputed `netPlBuyYes`/`netPlBuyNo`
    fields — no independent fee logic. Correctly labeled
    `HISTORICALLY_SUPPORTED_PRICE_DISCOVERY_POST_FEE_UNPROVEN` (CI
    crosses zero, p=0.089) — not yet bettable, and that's the correct
    conclusion under clean accounting, not an artifact of over-fee'ing.
25. **C02-OFI**: same pipeline; 21 BH-FDR-surviving cells all NEGATIVE
    post-fee, `NOT_TAKER_TRADABLE`, `realMoney: false` — correctly
    computed, not overstated by any double fee.
26. **Maker feasibility**: fixed-25-contract economics, confirmed
    structurally distinct from $10-budget economics throughout (script,
    results JSON, and doc all use "25 contracts" vocabulary, never "$10"
    in the maker section). Maker fee multiplier (0.0175 designated /
    0.0 default) applied exactly once per hypothetical fill; the taker
    baseline used for comparison is fee'd independently and reported as
    a separate field, never summed into the maker P/L. Sample math
    reproduces the published table exactly (MAKER-A queue=0: fill 15.6%,
    net +$0.52/episode vs. taker baseline +$1.47/episode).

    **Whole-vs-fractional quantity assumptions**: kept explicitly
    separate from double-fee accounting throughout (per the audit
    program's own instruction). This repo's historical corpus defaults
    to `QUANTITY_GRANULARITY_UNKNOWN` → conservative whole-contract
    simulation; no evidence exists to justify assuming fractional-order
    capability for any historical MLB market/date, so this default is
    the correct, non-fabricating choice, not a bug to fix here.

## H — REAL BET TRACKING

27. **Does canonical stake reflect intended whole-dollar risk?** Yes, by
    code (`determine_canonical_stake`'s priority ladder is intact and
    verified against current, not stale, code) — but with a **real
    staleness gap**: the ledger has grown from 207 to **378 REAL wagers**
    since the 2026-08-13 reconciliation run (**205** of them recorded
    *after* that run's timestamp). None of those 205 newer wagers have
    been run through `reconcile_execution_economics.py`.
28. **Wagers mis-recorded because Initial Cost was mistaken for stake?**
    None found in the *reconciled* population (152 already correct, 4
    safely auto-corrected to whole dollars, both confirmed still live in
    the current `bets.jsonl`). **Unknown for the 205 un-reconciled newer
    wagers** — they have simply never been checked.
29. **Remediation required**: run
    `scripts/edgelab/reconcile_execution_economics.py` (dry-run first,
    then `--apply` for any new `SAFE_FEE_AWARE_WHOLE_DOLLAR_INFERENCE`
    matches) against the current 378-wager population, and re-publish
    the reconciliation artifact. This is an operational follow-up, not a
    double-fee code bug — flagged here per the audit's Section F
    instruction to report, not silently fix.

## I — PRODUCTION

30. **Current recommendation semantics**: as of this audit, production
    **is** wired to fee-aware economics — this supersedes
    `docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md` §11's "not wired in"
    claim, which was accurate when written (2026-08-14) but stale as of
    a later, separate, fully-tested milestone
    (`docs/PRODUCTION_FEE_AWARE_NET_EV.md`). `edgeUsedForQualification`
    is `netExecutableEdge` (fee applied once, at the raw-price break-even
    shift, then the same calibration factor applied once — verified by
    dedicated source-scan and numeric tests, and by directly re-running
    the relevant suites: 266 tests passed). `calibratedEdgeVsExecutable`
    (gross) remains the primary human-facing display field; net edge and
    fee drag are shown alongside it, never silently replacing it. The
    user-facing stake shown (`betSize`/`stake`) is always the bankroll
    unit-based sizing figure — never a fee-net "Initial Cost"-style
    number.
31. **Bet Up To implications**: `betUpToPriceGross`/`betUpToPriceNet`
    shown side by side (average net ceiling reduction: 1.60¢); gating
    uses the net (fee-aware) ceiling. This is the correct, single
    application of the fee to a raw price — not a double-fee risk.
32. **Live-risk impact**: none identified. The fee is applied exactly
    once, at a single choke point (`build_edge_fields()`), and
    `risk_gate.py`/`write_pending_bets.py` only ever read the
    already-computed fields — enforced by a passing regression test that
    would fail if either file ever imported the fee engine directly.
33. **Production fix required: NO.** The only actionable item from this
    audit's production review is documentation hygiene: update
    `docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md` §9/§11 to point to
    `docs/PRODUCTION_FEE_AWARE_NET_EV.md` as superseding those sections,
    so a future reader doesn't rely on the stale "not wired in" claim.

## J — GITHUB

34. **Audit branch**: `claude/kalshi-payout-fee-audit-66z4sn`.
35. **Tests**: new `tests/edgelab/test_executable_payout_audit.py` (8
    tests, Section M's literal Example A/B/C worked cases — passing). No
    existing test was modified. Full targeted regression run
    (`test_kalshi_fees.py`, `test_execution_economics.py`,
    `test_colorado_fixture.py`, `test_executable_payout_audit.py`): all
    green, no changes to any production or research script in this
    audit.
36. **Commit SHA**: see this branch's git log (this document + the field
    semantics manifest + the new test file, committed together).
37. **Production diff**: **none.** This audit changed zero lines of
    `lib/`, `scripts/`, or `api/` production or research code. It added
    two new files (this document, the field-semantics manifest) and one
    new test file. PR #179 remains unmerged, as instructed.

---

## MOST IMPORTANT FINAL QUESTION

**"Did we ever penalize an already-executable Kalshi wager a second time
for fees, thereby making a real strategy look worse than it was?"**

> **NO — FEE WAS APPLIED ONLY WHEN TRANSLATING RAW QUOTES.**

Across a repo-wide search (both `main` and PR #179), all 18 named MLB
research projects, the production qualification/Bet-Up-To gate, the
real bet ledger's stake semantics, the C01-PIT spent holdout (verified
row-by-row, 21/21 matched), and the maker-feasibility study, every fee
application traces to exactly one call into the canonical
`lib.edgelab.kalshi_fees` engine (or an exact reimplementation of its
formula), always starting from a raw market quote or an allocated
budget — never from an already fee-inclusive, all-in executable number.
The one real accounting bug this program's own history contains (unused
allocated budget silently treated as a loss) is a **different** bug from
double-fee-counting, was already found and fixed inside PR #88 before it
ever shipped un-fixed to `main`, and is well-documented and tested. The
one open item this audit surfaces is operational, not a code bug: 205
real wagers recorded since 2026-08-13 have never been run through the
stake-reconciliation script (Section H).

**"Do any previously rejected MLB strategies become profitable under the
correct user-facing $10 wager economics?"**

> **No.** Every previously-rejected conclusion checked in this audit
> (C02-OFI's `NOT_TAKER_TRADABLE`, C01-F5REV's post-fee-unproven status,
> the team-totals capacity-only findings, RFI's flat/negative fee-aware
> result, the C01-PIT holdout's `INCONCLUSIVE` verdict) was already
> computed with the fee applied correctly and exactly once — there is no
> hidden double-fee penalty to remove. Correcting the accounting (which
> this audit did, independently, line-by-line, for the C01-PIT holdout
> and the 10%+ bucket) reproduces the same sign and the same
> bettable/not-bettable status in every case checked. Nothing flips from
> rejected to profitable.

**KALSHI EXECUTABLE PAYOUT AUDIT: COMPLETE.**
