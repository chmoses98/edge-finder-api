# MRV: Kalshi MLB market-structure inefficiency research program

**Status: RESEARCH ONLY. No production change. Nothing here changes
eligibility, thresholds, staking, bankroll, recommendations or execution.**

Baseline at program start: `main` = `c2f1cd8f2b80f84a9ae653039d2b2b32f2bfb215`
(14 routine data-capture commits after the #243 merge
`13fdd49da84d806ea6ced1a10a4ff35af50078e5`). Baseline test suite:
11,856 passed / 0 failed. Canonical wager ledger untouched.

## 1. Question

Where, if anywhere, does money systematically leak out of the Kalshi MLB
market ecosystem — through market structure, relative value between
related contracts, information arrival, or execution — without requiring
us to out-predict the market on baseball outcomes?

The baseball model is one input at most. A market-derived prior may be
stronger than the model; prior work (MLB-RSCH-0022/0024, ALPHA-0002 Family
E) already showed the production model adds no information beyond Kalshi's
own price, and MLB-RSCH-0026 showed Kalshi's fair mid is near-calibrated.

## 2. What was already established (do not re-derive)

| Program | Finding that binds this one |
|---|---|
| MLB-ALPHA-0001 Family A | 513 price-band cells on August coarse archive: 1 BH survivor; deep longshots slightly *under*priced (laying them loses). |
| MLB-ALPHA-0001 Family B | **No pure post-fee arbitrage** in 169,603 books, 719 three-way batches, 103,409 ladder pairs, 39,030 dominance pairs at the hours-cadence archive; corrective RV trades lose (−38.7%). |
| MLB-ALPHA-0001 C01 | F5-total 90–99¢ YES at last pregame: discovery/validation PASS, blind holdout **INCONCLUSIVE** (17/30 games), moved to prospective shadow C01-PIT (active, its own workflow). |
| MLB-ALPHA-0002 Family C | 1-minute microstructure: order-flow imbalance and 30/60-min **reversal** predict the next fair-mid move by +0.1…+0.8¢ (BH survivors) but **every post-fee taker cell is negative**. |
| MLB-ALPHA-0002 Family D | Pinnacle→Kalshi lag at 15-min resolution, 2-date pilot: corr 0.05, INCONCLUSIVE, |Kalshi − Pinnacle| ≈ 0.5pp. |
| MLB-ALPHA-0002 maker | Passive fills 6–16 %, adverse selection −2.1¢ at +1 min; per-episode passive < taker; queue unknowable. |
| Night-before timing | Buying 12h+ early costs ≈ +1.1¢ on YES; CLV negative at every early entry. |
| Capture-completeness program (#236–#243) | Only 2 research-qualified COMPLETE snapshots exist (both 2026-09-22 morning). No family-level model edge survives clustering + BH. |

This program therefore concentrates on what those programs could **not**
see: (a) coherence and lead/lag at **1-minute** resolution on the recovered
exchange record, (b) cross-**horizon** dominance constraints never tested,
(c) sportsbook disagreement at **≥200 games** instead of 20, (d) an explicit
**accounting** of where taker money goes from the trade tape, and (e) the
first look at archived **order-book depth**.

## 3. Data reality (Phase 0 result)

| Source | Span | Cadence | Fields | Tier |
|---|---|---|---|---|
| `data/edgelab/observations/<date>.jsonl.gz` | 08-01 → 09-22, 676,852 rows | 4–6 captures/day (10–21/day on 08-11..16); **change-log** (unchanged quotes dropped) | yesBid/yesAsk/lastPrice/volume/OI; noBid/noAsk from 09-10 | B (2 COMPLETE snapshots = A, unsettled) |
| `data/kalshi_registry_snapshots/` | 06-08 → 09-22 | intraday files kept 21 days; full cross-sections | Era B (≥ 09-10) adds no-side + book_state | B |
| Exchange record (Release `MLB-ALPHA-0002-KALSHI-RAW-V1`) | 08-02 → 08-31 ex 08-17 | **1-minute candles** (bid/ask OHLC, volume, OI) + **full trade tape** (µs, taker side, size) | 13,121 contracts: ML, game total, F5/F3/F7 winner, F5 total all dates; team total + spread 08-02..07 only; **no props** | B (universe conditioned on observation archive) |
| Research branch `research/mlb-alpha-0002-prospective` | 09-02 → 09-22 | **3–8 runs/day** (health gate for 10-min cadence never passed) | quotes + **full order book** + trade tape (≈15 min before each run only) + Pinnacle/DK/FD/BetMGM h2h & totals with `last_update` + MLB lineup/pitcher state first-seen | B |
| `data/slates/<date>/` | 06-15 → 09-22 | 2–6/day | 4-book ML/RL/total/TT/F5 (American), file-timestamped; 69 rejected files | B |
| `data/edgelab/settlements/` | 08-02 → 09-21, 214,454 rows | — | YES/NO derived from MLB Stats API (**never Kalshi's own result**); integer-total ladders need the `≥N` correction | B |
| Lineup / pitcher / postponement **timestamps** | none historically; first-seen bounds only from 09-02 at hours resolution | | | blocked |
| Order-book depth history | none before 09-02 | | | blocked |
| Fee schedule | taker 0.07·C·P·(1−P) (web-corroborated); maker per series **unknown** | | | assumption |

Sandbox has no egress to Kalshi or MLB (verified: HTTP 000). No new data
can be pulled in this session; everything below is on archived evidence.

## 4. Evidence tiers

- **TIER A (confirmatory)** — research-qualified COMPLETE snapshots only.
  Two exist, both 2026-09-22 morning, zero settled rows. **No confirmatory
  inference is possible in this program.** Nothing is called VALIDATED.
- **TIER B (exploratory)** — everything else, with incompleteness carried
  forward: a missing market is never treated as absent; the exchange record
  covers only tickers the observation archive knew about; the prospective
  corpus never met its cadence gate.
- **TIER C (prospective)** — COMPLETE captures after a spec is frozen here.

## 5. Statistical rules (binding for every test)

- **Cluster** = physical game (`physical_game_key(eventTicker)`), never
  eventTicker or the two-format `gameId`.
- **Interval** = game-clustered bootstrap CI90 (2,000 resamples, seed
  20260922) via `lib.edgelab.research.market_structure.stats`.
- **p-values** = null-centred cluster bootstrap; **BH q = 0.10 within each
  multiplicity family**, over every registered test in that family.
  Blocked tests count toward the search space.
- **Sample floor** for any inferential claim: ≥ 60 games, ≥ 10 dates,
  ≥ 80 contracts.
- **Candidate bar**: BH survivor + post-fee executable effect with CI
  excluding 0 and ≥ +1 % of cash risked (or ≥ +1¢ on price-move targets) +
  sample floor + same sign in both date halves + stated mechanism + no
  midpoint fill.
- **Discovery ≠ confirmation**: any rule found here is frozen with a
  sha256 before it may be evaluated on TIER C data; it is never adjusted
  against that data.

## 6. Execution model (binding)

BUY YES at `yesAsk`; BUY NO at `noAsk` when archived, else `100 − yesBid`.
Taker fee from `lib.edgelab.kalshi_fees.taker_fee` (rounded up to the
cent, one fill). Maker legs are reported at 0.0175 **and** 0.0, never
pooled. USD 10 whole-contract order; actual-cash-consumed denominator.
Midpoint is never a fill. Top-of-book only unless a depth ladder is
archived (prospective books), in which case a ladder walk gives the fill
at size. A quote older than 30 minutes is stale and not executable.

## 7. Hypothesis registry

Machine-readable, frozen: `data/edgelab/research_artifacts/market_structure/hypothesis_registry.json`
(24 hypotheses, 9 multiplicity families). Rendered:
`data/edgelab/research_artifacts/market_structure/HYPOTHESIS_REGISTRY.md`.
Status vocabulary: PROPOSED → DATA_BLOCKED | EXPLORATORY | REJECTED |
CANDIDATE → FROZEN_FOR_PROSPECTIVE → PROSPECTIVE_PASS | PROSPECTIVE_FAIL.

Every result — positive, negative, blocked — is written back to the
registry's `results` block by the runner scripts, never by hand.

## 8. Infrastructure (Phase 2)

`lib/edgelab/research/market_structure/`:

| module | purpose |
|---|---|
| `identity.py` | ticker → series / family / physical game key / side / rung; three-way and ladder grouping |
| `economics.py` | executable YES/NO cents, taker/maker fee, post-fee pair cost, settlement P/L per USD, break-even |
| `stats.py` | game-clustered bootstrap CI + null-centred p, BH-FDR, Wilson fallback, date-half stability |
| `candles.py` | loader for the recovered 1-minute record (root injected; never committed) |
| `coherence.py` | ladder monotonicity, three-way sums, cross-horizon and cross-family dominance with fee-aware violation tests |
| `leadlag.py` | cross-family lead/lag partial-regression on minute grids |
| `sharp.py` | decimal → no-vig probabilities, sportsbook ↔ Kalshi join, disagreement panel |
| `book.py` | order-book ladder walk: fill price and slippage at size |
| `tape.py` | trade-tape taker/maker decomposition |
| `registry.py` | registry load/validate/render, result write-back, spec freezing with sha256 |

Runners live in `scripts/research/market_structure/`; outputs in
`data/edgelab/research_artifacts/market_structure/`. Raw exchange-record
and prospective-corpus files are read from an injected root outside git.
Tests in `tests/research/market_structure/` (stdlib only; CI installs no
numpy).

## 9. Results

Full results, negative findings, data-integrity discoveries and the
prospective-instrumentation requirements are in
`docs/EDGELAB_MRV_RESULTS.md`. Headline: no candidate; the only systematic
leak is the taker's crossing cost (≈1.9¢/contract net, 1.7¢ of it exchange
fee); prices are coherent to the cent at 1-minute resolution; Kalshi ML
tracks Pinnacle within 0.4pp.

Reproduce (raw archives hydrated outside git):

    python3 scripts/research/market_structure/run_coherence.py --raw-root <candles root>
    python3 scripts/research/market_structure/run_tape.py --raw-root <candles+trades root>
    python3 scripts/research/market_structure/run_leadlag.py --raw-root <candles root>
    python3 scripts/research/market_structure/run_cost_surface.py --raw-root <candles root>
    python3 scripts/research/market_structure/run_sharp.py --corpus-root <prospective dir>
    python3 scripts/research/market_structure/run_books.py --corpus-root <prospective dir>
    python3 scripts/research/market_structure/run_info_events.py --corpus-root <prospective dir>
    python3 scripts/research/market_structure/run_pricebands.py
    python3 scripts/research/market_structure/run_portfolio.py
    python3 scripts/research/market_structure/write_registry_results.py

The candles/trades root is the hydrated Release
`MLB-ALPHA-0002-KALSHI-RAW-V1` (`scripts/research/mlb_alpha_0002/hydrate_raw_dataset.py`);
the prospective dir is
`data/edgelab/research_artifacts/mlb_alpha_0002/prospective/` on branch
`research/mlb-alpha-0002-prospective`.

## 10. Prospective observation layer

The DATA_BLOCKED hypotheses are served by a separate, parallel collector
(`docs/EDGELAB_MRV_PROSPECTIVE_COLLECTOR.md`): MRV prospective collector v1,
own storage root and research branch, full per-game order-book universe
with no cap, per-fetch timestamps, deterministic sportsbook joins,
first-seen information-state transitions, bounded multi-cycle cadence, and
frozen research-readiness gates. No MRV inference run is authorised on that
corpus until its `researchReady` gate passes.

## 11. Failure is a result

If no hypothesis reaches CANDIDATE, the program's output is the map of
where price discovery works, the measured size of each cost component,
and the frozen prospective instrumentation needed to test what history
cannot. That outcome is preferred to a false positive.
