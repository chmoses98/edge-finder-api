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
