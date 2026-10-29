# EdgeLab — MLB-ALPHA-0003: Full-market Kalshi MLB research and opportunity engine

**Status: RESEARCH / PAPER ONLY. No production change.** This program changes no
production probability, recommendation, family eligibility, staking, fee, Bet Up To,
wager ledger, trading credential or workflow schedule. Live order placement is
hard-disabled in code (`lib/edgelab/mlb_alpha/live_guard.py`) and no network call to
an order endpoint exists anywhere in this package. Every frozen program (ALPHA-0001
C01-PIT, ALPHA-0002 C01/C02/C03/D01/I01, MRV, RSCH-0035 TEAM_TOTAL_NB_V1) is untouched.
The SIFT 3.0 redesign (`sift-sports-intelligence`) is untouched.

## Mission

Find genuine, repeatable, profitable-after-fees inefficiencies anywhere in the Kalshi
MLB universe, determine how to exploit them systematically, and prepare (never enable)
automated execution. The prior programs closed with one unambiguous conclusion that this
program treats as a constraint, not a hurdle:

> Fees plus crossing cost consume the apparent inefficiency. 1,985,438 pregame taker
> prints: half-spread 0.58¢ + fee 1.68¢ vs. fair-mid drift +0.33¢ in the taker's favour
> = **−1.93¢ per contract**. Zero cross-market arbitrage at minute resolution; Kalshi
> tracks Pinnacle to 0.42pp; every price band, spread bucket and liquidity bucket is
> fee-negative (`EDGELAB_MRV_RESULTS.md`). One candidate (ALPHA-0001 C01-PIT) reached a
> spent, INCONCLUSIVE holdout.

## What is NOT repeated

Not re-run: price-band ROI mining, YES/NO bias mining, ladder/three-way arbitrage scans,
raw lead/lag of ML vs F5 ML, Pinnacle-vs-Kalshi ML disagreement at the 3pp trigger,
maker simulation on the F5-reversal episodes, the production-model residual on the 14
families (ALPHA-0002 Family E / RSCH-0024). The ALPHA-0001 blind holdout (2026-08-26..31)
is SPENT and is used only as ordinary development data.

## What the earlier studies could not test (and why they were blocked)

| Gap | Evidence | This program |
|---|---|---|
| A coherent model, not 14 separate Poisson/NB heads | `KALSHI_MLB_MARKET_COVERAGE_AUDIT.md`: best line only, two Poisson engines | `lib/edgelab/mlb_alpha/sim.py` — one run-environment simulator, every game-level family from the same simulated games |
| Pricing the *whole* ladder and the *other* periods off a sharp anchor | Family D tested ML/total lead-lag only; Family E tested the production model | **H3-A**: Pinnacle-anchored simulator vs Kalshi F3/F5/F7/F5-total/F5-spread/ML executable prices |
| Delivered capture cadence | MRV: scheduled 10 min, delivered 3–8 runs/day | `audit_capture.py`; findings below |
| Information-event repricing | DATA_BLOCKED: event bounded by 861–904 min | Feasibility measured: **not testable on the existing corpus** (see results) |
| Universe completeness | 8 series in production, 196 in research policy | `registry.py`: every Kalshi series, membership-checked |

## Frozen protocol — H3-A (written and committed BEFORE any outcome on the joined sample was read)

**Hypothesis.** A single simulator whose run environment is recovered from a sharp
sportsbook's vig-free moneyline and total prices the *derivative* Kalshi MLB contracts
(first-3/5/7-inning winner and tie, first-5 total and spread, moneyline) more accurately
than Kalshi's own mid, in a way that survives fees at executable prices. Prior: weak. The
market tracks Pinnacle on the headline contracts, but nobody has checked whether the
derivatives are priced coherently with the same sharp surface.

**Universe.** MLB-ALPHA-0002 prospective corpus (branch `research/mlb-alpha-0002-prospective`),
game dates 2026-09-02 → 2026-10-25, regular season and postseason stratified, never pooled
into a single significance claim. Contracts: `KXMLBF3`, `KXMLBF5`, `KXMLBF7`, `KXMLBF5TOTAL`,
`KXMLBF5SPREAD`, `KXMLBGAME`, and `KXMLBTOTAL`/`KXMLBTEAMTOTAL`/`KXMLBSPREAD`/`KXMLBRFI` which
are reported but expected to fall below the 60-game floor because the collector's
400-book cap starves them. Not tested (no settlement truth in the archive): `KXMLBINNINGWIN`,
`KXMLBINNINGTOTAL`, `KXMLBEXTRAS`. Player props: no simulator path.

**Joins.** Kalshi price = top of the archived full order book of the same capture run as
the sportsbook row (the quote store carries no prices; see results). A sportsbook row is
used only if `capturedAt` is before its commence time. Game identity: team abbreviations +
scheduled start within ±45 minutes; doubleheader or ambiguity ⇒ excluded, never guessed.

**Model.** `RunEnvSim_v1`, structural constants fitted to 2022–2025 box scores only
(q = 0.36, league σ = 0.23, home edge 1.075, extras Poisson 0.92). Anchor (primary):
Pinnacle no-vig home probability and Pinnacle total line with its no-vig Over probability,
solved on the tabulated surface `anchor_surface_v1.json.gz`. Secondary anchor: four-book
mean no-vig home probability with the Pinnacle line and the mean Over probability of books
quoting that same line. 200,000 simulated games per anchored game, fixed seed.

**Decision rule R1 (frozen).** For each contract at each capture run with
5 < minutes-to-start ≤ 240, a two-sided book, executable price in [0.03, 0.97] and
top-of-book size ≥ 50 contracts:
`BUY YES` if `p_model − ask − taker_fee(ask) ≥ 0.03`;
`BUY NO` if `(1 − p_model) − (1 − bid) − taker_fee(1 − bid) ≥ 0.03`.
One entry per ticker, at the first run that satisfies the rule. $10 whole-contract market
order walked through the archived book; fee from `lib.edgelab.kalshi_fees` on the filled
quantity. Held to settlement.

**Sensitivity (counted in multiplicity).** threshold ∈ {0, 1.5¢, 5¢}; consensus anchor.

**Information test I1 (rule-free).** Per family, one observation per ticker (its first
eligible run): paired log-loss difference `LL(mid) − LL(p_model)` with 0.001 clamp, game-
cluster bootstrap CI90. Positive ⇒ the simulator beats the market mid. Reported separately
from trading profitability.

**Inference.** Game-cluster bootstrap (`lib.edgelab.research.market_structure.stats`),
2000 resamples, seed 20261027, CI90; BH-FDR q = 0.10 across *every* test below, with the
count recorded. Date-half stability reported.

**Qualification bar (unchanged from MRV).** BH survivor AND post-fee CI excludes 0 AND
net ROI ≥ +1% AND ≥ 60 independent games AND ≥ 10 dates AND same sign in both date halves
AND a stated mechanism AND no midpoint fill. Anything short is a hypothesis, not a result.

**Registered tests (m).** I1 × families with ≥ 30 games; R1 × {family × side} + pooled;
sensitivity × 3 thresholds + consensus anchor; H3-B (C01-PIT forward re-score); H3-C
calibration baseline is descriptive (no test, no multiplicity charge).

## Other registered items

* **H3-B.** Score the *frozen, unmodified* ALPHA-0001 C01-PIT rule (`882f16d8330af1af…`) on
  KXMLBF5TOTAL observations dated 2026-09-02 → 2026-10-25 from the observation archive. The
  trigger stream differs from the frozen prospective protocol (the archive cadence is not the
  10-minute poll), so this is reported as *market-rule* evidence, explicitly not shadow evidence.
* **H3-C.** Descriptive calibration baseline: Brier, log loss, reliability by family, side,
  price band on 2026-09-02 → 2026-10-25 using the observation archive. No test.
* **H3-D.** Information-event repricing: feasibility measurement only (events, bounds).
* **H3-E.** Extras market vs the 9,719-game long-run base rate (8.66%): descriptive.

## Production firewall

No file under `api/`, `app/`, `scripts/` outside `scripts/research/mlb_alpha_0003/`, or
`lib/` outside `lib/edgelab/mlb_alpha/` is modified. The canonical-evidence hash guard
(`tests/conftest.py`) passes unchanged.

## Addendum A (registered 2026-10-27, after H3-A/H3-B/H3-C ran, BEFORE any multi-season outcome was read)

**H3-F — late-season over-scoring surprise: market-wide or Kalshi-specific, and does it repeat?**
Motivation (post hoc, from H3-C): in 2026-09-02..09-27 the over-type ladders were realized more often
than priced (KXMLBTOTAL mid 0.566 vs 0.616 realized; TEAMTOTAL 0.451 vs 0.491; F5TOTAL 0.602 vs 0.641)
while H3-A showed Kalshi's derivatives sit on the Pinnacle surface within about a cent. If both hold,
the surprise belongs to the sharp market (a common-mode scoring shock), not to Kalshi, and it is
tradable ex ante only if it recurs by season. Hypothesis generated from 2026; therefore it is tested
**only on the independent seasons 2022-2025** (Pinnacle pre-commence totals from
`data/research_cache/pinnacle_historical`, runs from the cached box scores). 2026 is the motivating
sample and cannot confirm anything.

* Statistic: `d = actual total runs - Pinnacle total line` (half-lines; push-free lines only) and the
  realized Over rate versus the no-vig implied Over probability, per calendar month and for September.
* Clustering: game date. Per-season reported separately.
* Pre-declared decision: "seasonality supported" only if the September mean `d` is positive in >= 3 of
  the 4 seasons AND the pooled date-clustered CI90 excludes 0. Otherwise the 2026 pattern is classed as
  a one-season common-mode surprise. All 12 monthly cells and the pooled bias are reported (m = 13 for BH).
* Also reported: the same decomposition on 2026 from the ALPHA-0002 Pinnacle odds (descriptive), to
  verify the claim "Kalshi is no more over-biased than Pinnacle".

## Addendum B (registered 2026-10-27, BEFORE any maker-fill outcome was computed)

**H3-H — passive (limit) entry with a measured queue, versus crossing the spread.**
ALPHA-0002's maker simulation (264 F5-reversal episodes) had to *sweep* queue position because candles
carry no sizes. The prospective corpus does carry them: every archived order book records the resting
size at each price, and the trade tape records every print. So the queue ahead of a join-the-bid order at
placement is **known**, not assumed, and a fill requires subsequent tape volume at or through the price
after that queue is consumed. Cancellations ahead of us are ignored (conservative).

* Population: every eligible (run, contract) in the H3-A panel (5 < minutes-to-start <= 240, two-sided
  book, outcome settled), families `KXMLBF5TOTAL, KXMLBF5SPREAD, KXMLBF3, KXMLBF5, KXMLBF7, KXMLBGAME`
  (the six with >= 60 games). One order per (ticker, side), at the first eligible run.
* Orders: BUY YES joining the best YES bid; BUY NO joining the best NO bid (= 100 - best YES ask).
  Size = floor($10 / (price + fee)) contracts. Order rests until first pitch, then cancels.
* Fill rule: tape prints with the taker on the opposite side, at or through our price, after the measured
  queue ahead is consumed (`lib.edgelab.mlb_alpha.paper.simulate_resting_fill`). A price touch alone never fills.
* Economics: held to settlement; maker fee 0.0175 (headline, `FEE_MULTIPLIER_MAKER_DESIGNATED`) and 0
  (sensitivity, reported only). Inference: game-cluster bootstrap of ROI on **filled** orders; BH q = 0.10
  over {6 families x 2 sides + 2 pooled} = 14 cells.
* Also reported (descriptive): fill rate; the unconditional "if every placed order had filled" ROI (the gap
  between that and the filled-order ROI IS the adverse-selection cost); the taker alternative at the same
  moment (cross the spread at the archived ask/bid-complement).
* The postseason is reported separately and never pooled into the significance claim.

## Live execution design (specification only; DISABLED)

Nothing here is enabled, scheduled or credentialed. The skeleton in `lib/edgelab/mlb_alpha/live_guard.py` fixes the controls so they
can be reviewed and frozen before anyone is asked to authorise execution:

| Control | Mechanism |
|---|---|
| Disabled by construction | `LIVE_EXECUTION_CODE_ENABLED = False`; `submit_order` raises `LiveExecutionDisabled` unconditionally; no exchange client exists in the repo |
| No network, no secrets | the module imports no network library and reads no environment variable (an AST test enforces both) |
| Authorisation | a file naming the exact rule hash at `data/edgelab/research_artifacts/mlb_alpha_0003/LIVE_EXECUTION_AUTHORIZATION.json`; **not created** |
| Strategy gate | `ExecutionPolicy.validate` refuses any strategy whose validation status is not `QUALIFIED` |
| Bankroll | `BankrollProvider` interface = the authenticated account balance; only a `PaperBankroll` implementation exists; per-trade size is capped at a fraction of it |
| Limits | per trade, per game, portfolio open exposure, daily realised loss, price floor/ceiling |
| Idempotency | `client_order_id = sha256(rule hash, run id, ticker, side)`; the paper ledger uses the same discipline |
| Reconciliation | `reconcile(intended, reported)` reports missing / unexpected / quantity / price-through-limit discrepancies |
| Emergency stop | file-backed `EmergencyStop`; presence blocks every order; no code path removes it |

Read-only security boundary: unchanged. The collectors remain public-endpoint, read-only.

## Status at 2026-10-27

H3-A rejected, H3-B inconclusive/flat, H3-F no seasonality, H3-H infeasible at inside prices, H3-D untestable. No candidate frozen for prospective
shadow: freezing a rule with no fee-positive development evidence would only spend future data (same decision as MRV). Results:
`EDGELAB_MLB_ALPHA_0003_RESULTS.md`. Registered multiplicity counts in the amendments are upper bounds; the cells actually tested were
H3-A m = 11, H3-F m = 7 + 7, H3-H m = 3.
