# MLB-ALPHA-0003 — results (2026-10-27)

**RESEARCH / PAPER ONLY. No strategy is qualified. No production behaviour, eligibility, staking, fee, credential or
workflow schedule was changed, and live execution is disabled in code.** Protocol and amendments:
`EDGELAB_MLB_ALPHA_0003_PROGRAM.md` (H3-A frozen at commit `701f37e10` before any joined outcome was read; Addenda A and B
registered before their computations). Artifacts: `data/edgelab/research_artifacts/mlb_alpha_0003/`.

## Bottom line

* Across the whole Kalshi MLB universe we can see, **we found no executable, post-fee, out-of-sample inefficiency.**
  Not "weak evidence": every test that had power came back flat or negative, and the one apparently large number
  (H3-A pooled ROI +133%) is nine trades in six games and is not significant after multiplicity.
* The prior conclusion survives a genuinely different method. A single simulator anchored on Pinnacle's no-vig moneyline
  and total reproduces Kalshi's first-3/5/7-inning, F5-total, F5-spread, spread, total and team-total prices to a mean
  absolute **0.4–1.3¢** (moneyline 0.4¢, derivatives 1.0–1.3¢), against a median spread of 1–2¢ and a taker fee of ~1.7¢.
  The prices are coherent with the sharp surface within the cost of crossing.
* Passive entry is not a way out at inside prices: with the **measured** queue (median 1,500–5,000 contracts ahead at the
  best bid) a $10 join-the-bid order filled **0.4% (YES) / 1.6% (NO)** of the time.
* The most valuable output is infrastructure truth. Three defects made large parts of the prior forward evidence
  unusable; they are documented, and two are fixed in separate small PRs (see G).
* The 2026 season is over (World Series only). New regular-season evidence cannot exist before ~late March 2027.

## A. Families discovered, modelled, historically tested

`scripts/research/mlb_alpha_0003/build_registry.py` → `market_registry.json`, `MARKET_REGISTRY.md` (218 series seen).

| Verdict | Series | Notes |
|---|---:|---|
| MLB | 189 | 29 with a per-game price path; 70 season futures; 24 awards; 33 events; 15 leaders; 9 postseason-series; 9 corporate |
| NOT MLB | 25 | **`KXMLBBGAME` is Mobile Legends esports under the `KXMLB` prefix**; WBC (`KXWBC*`, `KXMLBWORLD`), college (`KXNCAA*`), Mexican league, ESPY, novelty |
| MLB exhibition (never pooled) | 3 | spring training, All-Star Game, Futures Game |
| Multi-leg combination | 1 | `KXMVE*` — price only by leg decomposition |
| Unclassified | 0 | an unmatched MLB-looking series is reported as `UNCLASSIFIED_NEEDS_REVIEW`, never dropped |

**The universe is growing, and the registry flags it.** The daily Kalshi series catalogue went from 179 to 215 series between 2026-07-30 and
2026-10-26 (36 added, none dropped): inning winner/total (08-18), pitcher ER/hits/walks-allowed and hitter walks (08-17), the whole postseason-series
block (09-22), walk-offs, weather delays, starting-lineup specials (09-29…10-13), and the esports collision `KXMLBBGAME` (10-08). Each is reported
with its first-seen date in `MARKET_REGISTRY.md`; none is silently dropped for lacking a model.

Game-level families (13 priced by the one simulator; 12 player-stat series):

| Family | Priced by `RunEnvSim` | Archived settlement | Sample (games) | Tested here |
|---|---|---|---:|---|
| Moneyline `KXMLBGAME` | yes | yes | 779 | H3-A, H3-C |
| F3 / F5 / F7 winner+tie | yes (approx. inning split) | yes | 782–784 | H3-A, H3-C, H3-H |
| Full-game spread, F5 spread | yes | yes (F5 spread **only from Sep-2026**: Aug rows are on the wrong horizon) | 784 | H3-A, H3-C, H3-H |
| Game total, F5 total, team total | yes (all rungs) | yes (`≥ N` semantics, date-aware) | 784 | H3-A, H3-C, H3-H |
| NRFI/YRFI `KXMLBRFI` | yes | yes | 780 | H3-C; H3-A below floor |
| Inning winner, inning total, extras | yes through the 8th (rules for the 9th unverified) | **none in the archive** | 0 | **research gap** |
| Hitter hits/TB/HRR/RBI/SB, pitcher K/outs | **no** (existing player model not wired; production evaluations say `NO_MODEL_SUPPORT`) | yes | 740–777 | H3-C calibration only |
| Hitter HR, pitcher hits/walks/ER allowed, walks | no | **none** (12.6k HR markets, 0 settled) | 0 | research gap |
| Futures, awards, leaders, series, events | no | no | – | catalogued |

## B. What had already failed, and why

| Program | Result | Why |
|---|---|---|
| Pricing coherence / arbitrage (ALPHA-0001 B, MRV A) | zero executable violations at 1-minute resolution | adjacent rungs sit 6–14¢ apart; three-way books 4–26% off 100 but three taker fees (~5¢) exceed it |
| Price-band, YES/NO, favourite/longshot (ALPHA-0001 A, MRV G) | 1 positive FDR survivor in 513 cells; every other band fee-negative | fees + spread; longshot bias did not persist |
| C01-PIT (F5-total YES 90–99¢) | discovery +2.79%, spent holdout −0.03% on 17 games, **forward −0.06% on 93 games (H3-B)** | break-even ≈ 95.4% win rate; observed 95.3% |
| F5-moneyline reversal (C01-F5REV), maker version | predictable move 1–5¢ vs 4–9¢ spread; maker fills 6–16%, adverse selection −2.1¢ at +1 min | **and this program found its shadow could never run** (null prices, see G) |
| Production model residual (RSCH-0022/0024/0026) | `EDGELAB_FROZEN_FORWARD_SCORECARD`: production − market Brier **+0.0161** (CI 0.0127–0.0195), 687 games | the production Poisson heads are *worse* than Kalshi's price |
| NRFI/YRFI | real-money suspended | lost to Kalshi and to a constant over 225 games |

Taker economics (MRV): −1.93¢/contract (half-spread 0.58 + fee 1.68 − drift 0.33). This program's H3-C re-measures the
same on three months of observation data: **the average taker loses in every family on both sides** (below).

## C/D. New hypotheses and whether anything survives

| ID | Question | Result | Verdict |
|---|---|---|---|
| **H3-A** | Does a Pinnacle-anchored coherent simulator price Kalshi's derivatives better than Kalshi's mid, enough to trade after fees? | Information (regular season, game-clustered, BH over m = 11): F5-total **worse** than mid (ΔLL −0.0043, CI −0.0074…−0.0013, p = 0.016); F5 winner +0.0025 (p = 0.082); F3, F5-spread, F7, moneyline indistinguishable. **No BH survivor.** Trading rule R1 (net edge ≥ 3¢, $10 taker, depth-walked, hold): 9 trades / 6 games; at 0¢ threshold 778 trades / 201 games, **net ROI −2.9% (CI −15.3%…+10.4%)**; 1.5¢: −4.3% (57 games); 5¢: no trades. Consensus anchor: 11 trades. | **REJECTED** |
| **H3-B** | Does the frozen C01-PIT rule replicate after its freeze? | Regular season: 107 opportunities, 93 games, 22 dates, 102–5, **net ROI −0.06%** (CI90 −4.0%…+3.1%, p = 0.97). Postseason 15–0, +5.4% on 15 games (below the 60-game floor). Trigger stream ≠ the frozen 10-minute poll. | **NOT REPLICATED / INCONCLUSIVE (flat)** |
| **H3-F** | The 2026 September over-scoring surprise: recurring? | 2022–2025 Pinnacle totals (1,162 games): realised Over − implied Over: **September −4.8pp (1 of 4 seasons positive; CI −10.8…+1.2)**, August +5.3pp (p = 0.035; **no BH survivor over the 7 cells**), pooled +0.5pp. My registered statistic (mean actual − line) is biased positive by construction (lines are medians, runs are right-skewed); the over-rate statistic is the correct one and is reported as a declared correction. | **No seasonality. 2026 was a one-season common-mode shock** |
| **H3-H** | Can a join-the-bid limit order with a *measured* queue earn the spread? | ~4,900 orders per side (regular season): **fill rate 0.4% (YES), 1.6% (NO)**; median queue ahead 1,578 / 2,802 contracts. Filled-order ROI −29% / −8% on 20 / 79 fills (CIs span 0). The few cells with large positive ROI (F3-NO +30% on 8 fills, F7-NO +45% on 3) are below any floor. | **Infeasible at inside prices** |
| H3-D | Do lineup / pitcher-change events reprice with a lag? | MRV 10-minute corpus: 41 games, **3 lineup transitions**. Needs ≥ 20 and ≥ 60 games. | **Untestable on existing data** |

`H3-C` (descriptive, no test) — the market's own calibration and taker economics, 2026-09-02…09-27 regular season, first
pregame quote with 5 < minutes ≤ 240, $10 taker order, game-clustered CI90:

| Family | Games | Mid vs base rate | Spread | Buy YES ROI | Buy NO ROI |
|---|---:|---|---:|---|---|
| Game total | 256 | 0.566 vs 0.616 | 1.1¢ | **+8.8% [+0.9, +17.1]** | −16.7% [−28.6, −5.7] |
| Team total | 256 | 0.451 vs 0.491 | 1.3¢ | +4.9% [−2.4, +13.1] | −14.1% [−20.1, −8.4] |
| F5 total | 255 | 0.602 vs 0.641 | 1.3¢ | +5.4% [−2.2, +12.8] | −16.0% [−27.9, −3.2] |
| F5 spread / spread | 251 / 256 | ≈ calibrated | 1.2 / 1.0¢ | +1.7% / −9.4% | −5.3% / −1.7% |
| Moneyline, F3, F5, F7 | 216–254 | calibrated | 1.0–2.7¢ | −6.5% … −13.9% | −1.8% … −4.7% |
| Hitter/pitcher props (7 series) | 239–254 | calibrated (slope 0.92–1.04) | 1.7–3.0¢ | −1.5% … −17.1% | −3.3% … −8.5% |

Of 34 family×side cells exactly **one** has a positive CI90 — game-total YES — and H3-F shows that cell reflects September 2026
scoring, not a repeating mispricing. Postseason cells are mostly negative on 30 games each.

**D. Strategies that survive fees, liquidity and unseen data: none.** `validation_status.json` lists the only non-default status
(`KXMLBF5TOTAL|YES` = INCONCLUSIVE).

## E. Expected return, capacity, uncertainty, downside of each *qualified* strategy

There are none, so there is no expected return to report. Upper bounds from the nearest candidate, C01-PIT: point estimate ±0 %,
CI90 up to +3.1% on ≈ 13 contracts/day at $10 (≈ $130/day deployed, ≈ $4/day at the CI's best end); drawdown is not estimable from
5 losses. That is inside the noise of fee-rounding and well below the program's +1%-and-significant bar.

## F. What can be scanned and paper-traded automatically today

`lib/edgelab/mlb_alpha`: taxonomy → registry → simulator (13 families) → anchor solver (0.6 ms/game from a tabulated surface) →
executable pricing → opportunity engine (every contract gets a verdict) → paper ledger (idempotent, limits, TAKER_AT_CAPTURE /
TAKER_NEXT_BOOK / MAKER_TAPE fills, settlement, drawdown) → live guard (disabled).

* **Replay over the MRV 10-minute corpus** (`paper_replay.py`): 78 cycles, 114 game-scans, 8,332 contract verdicts → 6,007
  `PASS_NO_EDGE`, 1,784 `PASS_ILLIQUID`, 541 `PASS_UNPRICEABLE`, **0 signals**. The pipeline works end to end; it finds nothing to trade.
* Verdict vocabulary: `PASS_UNPRICEABLE / PASS_ILLIQUID / PASS_NO_EDGE / PASS_VALIDATED_NEGATIVE / PAPER_SHADOW / TRADE_ELIGIBLE`.
  `TRADE_ELIGIBLE` needs a `QUALIFIED` entry in the validation table, which does not exist. An edge in an unvalidated family is
  `PAPER_SHADOW`: logged for frozen prospective scoring, never real money.
* Expression selection: legs are scored under seven game scripts (±8% environment, home/away strength, early-starter exit,
  starter dominance), kept only if positive in ≥ 5, and combined by mean-variance on the *simulated joint payoffs*, so a second leg
  is added only if it improves the risk-adjusted objective by ≥ 15%.
* Nothing runs on a schedule: this PR adds no workflow. Live scanning needs collection to be running (G).

## G. What is blocking progress

1. **The prospective ALPHA-0002 quote store has never carried a price.** `quote_of()` read legacy `yes_bid`; Kalshi now returns
   `yes_bid_dollars`. 0 of 190,780 rows (09-02…10-26). Consequence: `shadow_writers.quote_series()` drops null-price rows, so the frozen
   **C01-F5REV and C03-BOOKIMB shadows never created an episode** (only C02-OFI and I01-LINEUP directories exist). Fix: PR
   `claude/fix-alpha0002-quote-dollar-fields` (unit decided by field name, never by magnitude; 6 regression tests, 175 related tests pass).
2. **Delivered cadence.** ALPHA-0002: 270 captures in 55 days, median in-window gap **163 min** (p90 230, max 280) against a promised
   10; per game the median is **1** capture in T-240…T-0, 16% of games have none, none has ≥ 12, median blind interval 146 min. The observation
   archive's in-window median gap is 58 min. MRV proved that 10-minute delivery works (78 COMPLETE cycles, median gap 10.0 min) but ran 13 hours;
   its scheduled runs are now `skipped` — the `MRV_CONTINUOUS_CAPTURE_ENABLED` kill switch is set.
3. **The 400-book cap starves the core families.** In the joined sample, F5-total had 354 games of books but game total / team total / spread had 39–41.
4. **No settlement truth** for inning winner, inning total, extras, HR, hits/walks/ER allowed (the inning rules for the 9th are also unseen).
5. **No player-level model in the evaluation path** (production says `NO_MODEL_SUPPORT` for every prop); props are ~80% of contracts.
6. Defects found and handled: Eastern-time conversion is fixed at UTC-4 (wrong from 2026-11-01; PR `claude/fix-identity-dst-offset`); the
   Odds API returns *live* lines for games already started (excluded by commence-time); observation quote units change inside 2026-09-10
   (19:04Z → 21:48Z); a `"MLB" in ticker` filter admits `KXMLBBGAME` (esports) unless excluded.
7. **The season is over.** Remaining 2026 MLB markets: World Series games and futures.

## H. Milestones before a *limited* live deployment could even be considered

1. Merge the quote-field fix and re-enable continuous capture before opening day 2027 (variable `MRV_CONTINUOUS_CAPTURE_ENABLED`; verify with the
   existing health gates: median gap ≤ 10 min, COMPLETE ≥ 90%, books for **all six core families** — add a per-series book quota, an owner decision).
2. Add settlement truth for inning-level and HR/hits-allowed families (box-score/linescore settlement, verified against Kalshi's own settled result,
   which is still never archived).
3. Freeze, before the season, one registered rule per surviving hypothesis; collect ≥ 100 independent games and ≥ 10 dates (200 / 20 for the stronger
   checkpoint) of **forward** data with the frozen trigger stream; no tuning.
4. A candidate must meet the bar in the program doc *and* show positive net P/L under `TAKER_NEXT_BOOK` (latency) fills at stated size.
5. Only then: a separately approved execution component (authenticated bankroll provider, order client, reconciliation against fills, emergency stop), run in
   paper-with-real-quotes mode first. `live_guard.py` is the reviewed skeleton; the authorization file it requires does not exist and was not created.

## What this program did NOT do (scope boundaries)

* **No live Kalshi pull.** The sandbox has no route to `api.elections.kalshi.com` or `statsapi.mlb.com` (HTTP 403 from the egress policy), so the registry is
  built from the archived daily catalogues, observations, snapshots and settlements (the daily catalogue is itself a Kalshi `GET /series` pull made in
  GitHub Actions). Every number here comes from archives that were verified against each other; nothing was fabricated to stand in for a live call.
* **No fundamentals-driven run environment.** `RunEnvSim` is a coherent *pricing* layer; `lam_away/lam_home` come from a market anchor. A model-based
  `lam` (team offence, starters, bullpen, park, weather, Statcast) is the production input family, and its forward record against Kalshi is
  **worse than the price** (Brier +0.0161, 687 games). A unified simulator cannot repair a worse mean. The next legitimate experiment is to model the
  *anchor's residual* with point-in-time features, preregistered, on 2027 data.
* **No player-level pricing.** Props are calibrated (H3-C) but not modelled; production evaluations mark them `NO_MODEL_SUPPORT`.
* **No inference on inning winner / inning total / extras / HR** (no settlement truth), and no postseason claim (about 30 games per family).
* **No sizing or portfolio optimisation beyond the paper-ledger limits** because nothing qualified.

## Method notes and caveats (read before trusting any number)

* One month-and-a-half of one season in the joined sample (354 games with books; 210–255 with a given family); regular season and postseason are never pooled.
* The sportsbook leg is fetched minutes after the Kalshi leg of the same run, and the run's `capturedAt` is a single stamp; a thin slice of every
  disagreement is stale. Pregame-only filtering was applied; staleness was not used to filter (would have been a new, unregistered rule).
* `RunEnvSim` structural constants were fitted to 2022–2025 box scores only; it over-states extras (9.8% vs 8.7%) and F7 ties by ~1–2pp. Both biases
  make an "edge" on those contracts a model artifact, which is why they are tagged approximate.
* Fees are the repo's web-corroborated schedule; the maker multiplier (0.0175 headline, 0 sensitivity) is unverified against Kalshi's PDF.
* H3-H uses the tape as archived; a complete tape between captures is assumed, cancellations ahead of us are ignored (conservative), orders are placed
  120 seconds after the run stamp.
* H3-A is robust to the code refactor made mid-run: the evaluation was re-executed on the final code and reproduces **byte-for-byte**
  (sha256 `b71c8230…`).
