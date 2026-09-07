# MLB Institutional System Audit — September 2026

**Status: AUDIT ONLY. NO PRODUCTION BEHAVIOR CHANGED.**
No model probability, projection formula, calibration coefficient, threshold,
confidence tier, market eligibility, bet-up-to rule, staking rule, bankroll rule,
fee rule, settlement policy, live risk gate, `bets.json` row, or production API
behavior was modified by this audit. The only files added are this report and a
read-only invariant test module under `tests/audit/`, which nothing in production
imports.

| | |
|---|---|
| **Audited commit (`main`)** | `40aad30d3007ad9854a88736e3107dbdfebaafd6` (2026-09-07 04:55 UTC) |
| **Audit branch** | `claude/mlb-betting-audit-u6d1xp` |
| **Evidence corpus** | 40 archived authoritative slates, 325 Kalshi registry snapshots (132,619 market rows), 556-row root ledger, 385-row canonical ledger, 36 workflows, ~128k lines of Python, 4,763 lines of production JavaScript |
| **Deterministic test suite** | 9,623 passed / 9 skipped / **0 failed**, twice consecutively (full git history) |

---

## CEO SUMMARY

This is a genuinely unusual repository. The **research discipline is close to
institutional grade** — preregistration, DEV/VAL/forward holdouts, FDR control,
frozen-forward scorers, experiments that publish `NO_MEANINGFUL_IMPROVEMENT` and
retire themselves, and research artifacts that publicly correct their own earlier
conclusions. The **market-capture and coverage-accounting infrastructure is
excellent** — 457 archived markets reconcile to 457 accounted, zero unaccounted,
zero duplicates. The **settlement grader is careful and refuses rather than
guesses.** There is **no order-placement code anywhere in the repository**, which
structurally eliminates an entire class of capital risk.

And yet the operation should not be trusted with materially more capital today,
for three reasons that this audit establishes with direct evidence rather than
inference.

**1. The system's own research has already proven the model has no edge, and
production has not acted on it.** `MLB-RSCH-0022` measured production's archived
probabilities against settled outcomes across 3,137 rows / 293 games / 13
families: paired Brier delta **+0.0549** (model worse), 90% CI [+0.0391,
+0.0718], p≈0, **market better in every single family**. This audit independently
re-measured model-vs-market disagreement on the archived slates and found the
"edges" are not edges but **fixed, family-specific mean biases** — NRFI is
−11.87pp on **0.0%** of games above market and YRFI is +11.87pp on **100.0%** of
games (n=246). That is not a signal; that is a broken first-inning distribution.
The system nonetheless continues to place real money where its worse forecaster
disagrees with the better one — the definition of adverse selection.

**2. Every production price is a midpoint labelled "executable."** GitHub issue
#53 is open, unfixed, and now measurable. `g['kalshi']['ml']` carries no ask; the
selected team-total / total / spread rung carries no ask; the ledger's fallback
converts a **mid-derived American price** into `executablePriceUsed`. On the live
slate, `executablePriceUsed == marketProbVF` for **72% of rows exactly** and for
the remaining 28% within 0.5¢ (American round-trip rounding) — i.e. **100% of
production rows price against the mid**. Real ask prices *are* captured, one JSON
level away, and discarded. **5.2% of Accepted team-total rows in the current
regime would fall below the qualification floor if priced at the real ask.** The
whole fee-aware net-EV and bet-up-to apparatus — which is otherwise well-built —
is enforcing a ceiling against a price nobody can trade at.

**3. The feedback loop has been dead for six days and every health signal is
green.** `clv-update.yml` has failed on **every** scheduled run since 2026-09-02
(runs 89–93) on a one-line `ModuleNotFoundError` introduced by the CLV
canonicalisation work itself. Because its final step is the commit step, the
settlement it successfully computes each night is **discarded**. Because
`edgelab-postgame.yml` gates on `workflow_run.conclusion == 'success'`, settlement
has been `skipped` for five consecutive days. `data/edgelab/settlements/` and
`recommendations/` stop at **2026-08-31**; 156 bets are unsettled, the oldest from
**2026-06-06**. `corpus-health-check.yml` reported **success on every one of those
days.** The test suite is green because the tests import the failing module
differently from the way production does.

The through-line is not carelessness — the codebase is unusually well-commented,
well-tested and self-critical. It is that **enormous rigour has been applied to
research while the money-touching seams between components were left
unguarded**: a mid where an ask should be, a `sys.path` that differs between test
and production, a health check that measures the wrong thing, a doubleheader key
made of date and team names.

**Overall grade: C.** A B-grade research organisation bolted to a D-grade
execution and observability layer, betting real money on a model its own
laboratory has shown to be worse than the price it is betting against.

The good news is that the three headline problems are **cheap to fix** (one import
path, one JSON level, one health assertion) and the fourth — the model itself —
is a research question the lab is already equipped to answer honestly.

---

## A. OVERALL INSTITUTIONAL GRADE

### **C**

**Why not higher.** A quantitative firm's first question is "does the alpha
exist?" This operation has already answered it, in its own words, in
`docs/EDGELAB_MLB_RSCH_0022_PRODUCTION_CALIBRATION_AUDIT.md`:
`MARKET_SUPERIOR_EVERYWHERE / SYSTEMATIC OVERCONFIDENCE FOUND`. Real money
continues to flow on the refuted hypothesis. Separately, the price the system
bets against is not the price it can trade at, and the loop that would have
detected either problem has been silently broken for six days while reporting
green.

**Why not lower.** Almost nothing here is *fabricated*. The settlement grader
returns `SETTLEMENT_UNRESOLVED` rather than a guess. The coverage ledger balances
to zero unaccounted. The fee engine is a single, correct, versioned
implementation. `lib/edgelab/clv_convention.py` is a genuinely exemplary module.
The research lab retires its own candidates when they fail validation, and
publishes corrections to its own published conclusions. There is no live-order
code path at all. Most systems that reach this size have invented numbers
somewhere; this one mostly has not.

---

## B. CEO SCORECARD

| Domain | Grade | One-line justification |
|---|---|---|
| Data integrity | **B** | No fabricated values found; sentinel guards, atomic writes, immutable slate protection all real. Loses points for `wrcPlus = rpgIndex` mislabelling and one-sided-book mid halving. |
| Data freshness | **D** | Settlements/recommendations 6 days stale; `corpus-health-check` green throughout. |
| Identity | **D+** | `kalshiKey` is date+teams only; two archived doubleheaders got the *other* leg's tickers and produced real-money-tier rows. PR #172 fixed the settlement path, not the slate path. |
| Kalshi market semantics | **A−** | Ladder monotonicity ≤0.9% violations across 5 families; suffix conventions documented, cross-checked and independently correct. |
| Game model | **D** | Two divergent engines (up to 8.86pp), no home-field term, Poisson run distribution, incoherent 0.72 win-prob clamp. |
| Hitter model | **C** | Sophisticated research stack; not production-eligible; RSCH-0022 rates hitter/pitcher props the *worst* families vs market. |
| Pitcher model | **C−** | xFIP used directly as runs-allowed-per-9; clamped [2.80, 5.50]; TTO adjustment unvalidated. |
| Bullpen model | **B−** | Workload adjustment is capped, conservative and missing-data-safe — good design; season xFIP input is thin. |
| Calibration | **D** | Factors (0.187/0.255/0.18) fit on n=41–76 in-sample win rates; RSCH-0023 and RSCH-0025 recalibrations both failed validation and were retired; no validated correction exists. |
| Market-relative edge | **D** | Edge = model − market with a shrink factor. No market-error model, no conditioning on when disagreement is reliable. |
| Executable pricing | **F** | 100% of production rows price against mid. Issue #53 open and unfixed. |
| Fees | **A−** | `lib/edgelab/kalshi_fees.py` is a single, correct, versioned engine, applied exactly once. Downgraded only because it is applied to the wrong (mid) price. |
| Liquidity / execution realism | **F** | Zero references to depth, size, volume or open interest in any production decision file. No L2 book captured outside research. |
| Recommendation logic | **C** | Gate structure is coherent and well-tested; gates the wrong metric. |
| Bet Up To | **C−** | Correctly *derived* from the model's own edge requirement (a real improvement over the old echo-the-price version) but enforced against a mid. |
| Staking | **D** | Fixed tier × market multiplier. Multipliers set from n=6…n=76 realized win rates. No variance, liquidity, correlation or uncertainty input. |
| Correlation / portfolio | **D+** | Same-game pairwise table is thoughtful; nothing sees cross-game exposure. 79% of current real-money candidates are team-total **Overs**, structurally (no Under market exists in the ledger). |
| Settlement | **B+** | Careful, horizon-safe, refuses rather than invents. Player props honestly `SETTLEMENT_UNRESOLVED`. Undermined operationally by the dead workflow. |
| CLV | **C+** | `clv_convention.py` is exemplary; the root ledger's actual sign is canonical but is **mislabelled as inverted** in the performance report; snapshot CLV closes on a **mid**. |
| Bet ledger / accounting | **C−** | Two ledgers, 556 vs 385 rows; 156 unsettled back to 2026-06-06; issue #54 reproduced verbatim. |
| Full-market archive | **A** | 457/457 accounted, 0 unaccounted, 0 duplicates, unknown-series detector in place. Best component in the repository. |
| Research infrastructure | **A−** | Preregistration, holdouts, FDR, frozen scorers, self-correcting artifacts. Genuinely strong. |
| Workflow reliability | **D** | 5-day silent production failure; 749 scheduled runs/day; `workflow_run` success-gating creates silent skip cascades. |
| Storage | **C−** | 2.8 GB repo (1.7 GB data + 1.1 GB `.git`) cloned ~749×/day. |
| Tests | **B−** | 9,623 tests, genuinely deterministic, real invariant coverage — but they import the production entry points differently from production, which is exactly how the 6-day outage went undetected. |
| Observability | **F** | The one health workflow reported success every day of a total feedback-loop outage. |
| Professional betting discipline | **D** | No pass discipline against a proven-superior benchmark, retrospective multiplier tuning, no CLV-gated promotion, structural directional bias. |
| Architecture maintainability | **C** | Excellent documentation and comments; two probability engines, two ledgers, two CLV lineages, 232 scripts, 36 workflows. |

---

## SECTION 1 — SYSTEM ARCHITECTURE (as built, from current main)

### Production betting path

```
                       ┌─────────────────────────── EXTERNAL ───────────────────────────┐
                       │ Kalshi REST · The Odds API · MLB Stats API · Baseball Savant    │
                       └────────────┬───────────────────────────────────────────────────┘
                                    │
              ┌─────────────────────▼──────────────────────┐
              │ api/*.js  (Vercel serverless, 4,763 lines) │
              │  slate.js · odds.js · enrich.js ·          │
              │  kalshisearch.js · savant*.js · bullpen.js │
              │  ►► CONTAINS A COMPLETE SECOND PROJECTION  │
              │     ENGINE (wrcImplied / modelProb /       │
              │     allEdges / teamTotals / nrfi)          │
              └─────────────────────┬──────────────────────┘
                                    │ data/slate.json, data/kalshi_search.json
   ┌────────────────────────────────▼─────────────────────────────────────────────┐
   │ .github/workflows/fetch-slate.yml   (cron 16:00 / 20:00 / 22:00 UTC)          │
   │                                                                              │
   │  fetch → build_kalshi_registry.py ──► registry price_block                    │
   │            (american = f(MID);  mid = ((bid or 0)+(ask or 0))/2)  ◄── DEFECT  │
   │        → pre-validate → savant/bullpen/lineup/platoon fetchers                │
   │        → post_fetch_gate.py → merge_odds.py → enrich_data.py                  │
   │        → build_market_ledger.py  ◄── THE production model + edge + tier       │
   │             compute_projections()  (Poisson, no HFA, xFIP-as-RA9)             │
   │             build_edge_fields()    (fee-aware netExecutableEdge)              │
   │             enforce_bet_up_to()    (fee-aware ceiling, mid-priced)  ◄── DEFECT│
   │        → build_projection_board.py → regression_test.py                       │
   │        → validate_slate_final.py → protect_slate.py → publish                 │
   │        → risk_gate.py (TT safety · same-game correlation · portfolio)         │
   │        → write_pending_bets.py → validate_bet_logging.py                      │
   │        → write_tracked_tickers.py → capture_closing_lines.py                  │
   │        → create_snapshot.py (PRE_GAME_DECISION) → run_forward_replay.py       │
   └────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ data/slates/<date>/authoritative.json
                                    │ bets.json  (recommendations, PENDING)
                                    ▼
                        ┌───────────────────────┐
                        │  HUMAN places the bet │  ← no order API exists anywhere
                        │  (Kalshi UI, manual)  │
                        └───────────┬───────────┘
                                    │ import-manual-bets.yml / record-placed-bet.yml
                                    ▼
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ clv-update.yml (cron 06:00 UTC)                                              │
   │   clv_update.py  → settlement + Pinnacle CLV  ✔ succeeds                      │
   │   snapshot_coverage_check.py                  ✘ ModuleNotFoundError (|| true) │
   │   run_kalshi_clv_step.py                      ✘ ModuleNotFoundError  ◄─ BREAKS│
   │   → identity audit / rule71 report / COMMIT   ⊘ SKIPPED — output discarded     │
   └────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ workflow_run: conclusion == 'success'  ← never true
                                    ▼  (⊘ skipped 09-02 … 09-06)
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │ edgelab-postgame.yml → settle_markets.py → data/edgelab/settlements/          │
   │                      → build_recommendations.py → recommendations/            │
   │                      → ModelEvaluation ledger → calibration / research feed   │
   └──────────────────────────────────────────────────────────────────────────────┘
```

### Research path (parallel, read-only w.r.t. production)

```
capture-snapshots-scheduled (28/d) ─┐
clv_capture                 (78/d) ─┤
edgelab-capture             (28/d) ─┼─► data/kalshi_registry_snapshots/  (368 MB)
research-mlb-alpha-0002     (84/d) ─┤   data/edgelab/observations/, markets/
research-c01pit-shadow      (90/d) ─┤   research/mlb-alpha-* branches (L2 order book)
research-night-before        (6/d) ─┘
hitter/model-snapshot-scheduler (136/d) ─► prospective model + hitter snapshots
        │
        ▼
lib/edgelab/{experiment_registry, evidence_levels, pit_provenance, research_splits,
             frozen_forward_scorer, promotion_engine, paired_evaluation}
        │
        ▼   preregister → DEV → VAL → FORWARD holdout → FDR → checkpoint ladder
docs/EDGELAB_MLB_RSCH_00xx_*.md  (34 numbered experiments)
        │
        ▼
PROMOTION GOVERNANCE: human-approval-gated. Zero research candidate has been
promoted into production probability logic to date. (This is a strength, and
also why every research finding below is still unremediated.)
```

### Arrow-by-arrow risk table (production path only)

| Arrow | Owner | Identity key | Timestamp basis | Failure mode | Silent? | Test coverage |
|---|---|---|---|---|---|---|
| Kalshi → registry `price_block` | `build_kalshi_registry.py:198` | `market_ticker` | `snapshot_ts` (UTC) | one-sided book ⇒ mid halved | **YES** | none |
| registry → `slate.kalshi.*` | `merge_odds.py` | `kalshiKey` (date+teams) | inherited | doubleheader collapse | **YES** | partial (post-#172, settlement path only) |
| `slate.kalshi` → `executablePriceUsed` | `build_market_ledger.py:1401-1410, 1629-1634` | market row | `kalshiSnapshotTs` | mid substituted for ask | **YES** | none |
| edge → tier | `build_market_ledger.py:557` | market row | — | fails safe | no | strong |
| tier → `bets.json` | `write_pending_bets.py` | `sourceBetKey` | ET date | live-game guard shared w/ risk_gate | no | strong |
| `bets.json` → settlement | `clv_update.py` | `id` + `ticker` | ET date | **currently 100% failing** | **YES** | tests import differently |
| settlement → EdgeLab ledger | `edgelab-postgame.yml` | `betId` | UTC | skipped on upstream non-success | **YES** | workflow-structure tests only |
| ledger → calibration | `lib/edgelab/calibration.py` | `marketFamily` | settle date | stale input ⇒ stale calibration | **YES** | none |

---

## SECTION 2 — DATA SOURCES AND FRESHNESS

| Source | Consumer | Cadence | TZ basis | Fallback | Stale-data behaviour | Risk |
|---|---|---|---|---|---|---|
| Kalshi markets (17 series) | `api/kalshisearch.js` → `build_kalshi_registry.py` | 3×/day prod + ~230×/day research | UTC `snapshot_ts` | none | `validate_current_slate_date.py` + `stale_date_guard` | **one-sided book halves mid — unguarded** |
| Kalshi order book (L2) | research only (`prospective_capture.py`) | 84×/day | UTC | none | n/a | **production never sees depth** |
| The Odds API (Pinnacle/DK/FD/MGM) | `api/odds.js`, `clv_update.py` | 3×/day + settlement | UTC | `lowvig` etc. | credits tracked | secret carries **trailing whitespace** → `URL can't contain control characters` (see H-6) |
| MLB Stats API | `api/slate.js`, `lib/edgelab/mlb_schedule.py` | per run | ET date derived | live-schedule fallback | `scheduleSource` recorded | doubleheader `gamePk` present but not carried into `kalshiKey` |
| Baseball Savant | `fetch_savant_*.py` (6 scripts) | 1×/day | date-only | `xFIP → seasonFIP` | fields null ⇒ `Missing Data` row | **team Statcast fields written but never read** |
| Lineups | `fetch_lineups.py` → `enrich_lineup_confirmed.py` | fetch + `lineup-recheck.yml` | ET | `lineupConfirmedOfficial=False` ⇒ PAPER | explicit `lineupNote` | good |
| Bullpen usage | `fetch_bullpen_usage.py` | 1×/day | ET | multiplier 1.0 | never fabricates "rested" | good |
| Weather / park | `api/weather.js`, `config/park_geometry.json` | per run | — | `parkFactor=100` | default looks real | minor |

### Flagged

- **DS-1 (CRITICAL, latent).** `scripts/build_kalshi_registry.py:202`
  `mid = round(((bid or 0)+(ask or 0))/2, 4) if (bid or ask) else None`.
  When only one side of the book is quoted the midpoint becomes **half the true
  price** and every downstream field (`implied_pct`, `american`,
  `executablePriceUsed`, `marketProbVF`, `best_line` selection) inherits it.
  Measured across 132,619 archived market rows: **29.43% are one-sided**, and
  **all of them are ask-only** (`yes_bid == 0`). Worked example from the archive:
  `KXMLBTEAMTOTAL-26SEP021940DETMIN-MIN7`, `bid=0 / ask=0.81` → `mid=0.405`.
  Production-family exposure during production capture hours: 1.09%–3.86% of
  rows at 19:00–22:00 UTC.
  **Not yet realised:** across all 40 archived authoritative slates, 2,618
  selected rungs were checked and **0** had a one-sided book. The defect is live
  but has not yet landed on a chosen rung.
  Aggravating factor: `best_line()` selects the rung whose implied probability is
  closest to 50%, so a halved mid makes a broken book *more* likely to be chosen.
- **DS-2 (MEDIUM).** `norm()` in the registry and `_to_cents()` in the ledger
  both infer units from magnitude (`f if f <= 1.0 else f/100`). A legitimate
  **1¢** price is read as **$1.00**. Fails conservatively in the ledger (price
  becomes 100¢ ⇒ row rejected) but not necessarily in `scripts/executable_price.py`,
  where `yes_bid=1` yields `no_ask = 100 − 100 = 0`.
- **DS-3 (LOW).** `stats['wrcPlus'] = td.get('rpgIndex')` in
  `scripts/enrich_data.py:132` assigns an R/G index to a field named wRC+.
  `MODEL_CORE.md:108` documents this; `api/slate.js` computes a *different*
  `wrcPlus` from OPS. Two fields, one name, two meanings.

---

## SECTION 3 — IDENTITY

**Finding I-1 (CRITICAL — can produce a wrong wager; historical contamination
confirmed).**

`kalshiKey` is constructed from **date + team abbreviations only**, with no
doubleheader leg discriminator. Seven doubleheaders exist in the archived
production slates; in three of them the two legs shared identical Kalshi tickers.

| Date | Key | Leg A start | Leg B start | Tickers | Accepted rows |
|---|---|---|---|---|---|
| 2026-06-17 | `SFATL` | 18:00Z | 23:15Z | **identical** `KXMLBGAME-26JUN17**1915**SFATL-SF` on both | 3 (incl. 2 real-money MEDIUM) |
| 2026-07-11 | `MILPIT` | 16:05Z | 20:05Z | **identical** `...26JUL11**1605**MILPIT-MIL` on both | 2 |
| 2026-07-07 | `MILSTL` | 18:15Z | 23:45Z | identical TT ticker on both | 1 |
| 2026-08-17 | `STLCIN` | — | — | both `None` (refusal) | 0 |
| 2026-08-29 | `BOSNYY` | — | — | both `None` | 0 |
| 2026-08-29 | `AZSF` | — | — | both `None` | 0 |
| 2026-09-04 | `DETCLE` | — | — | both `None` | 0 |

On **2026-06-17** the 18:00Z leg (`gameId 824912`) was assigned the **19:15Z
leg's** contracts and produced `F5_ML_Away` at MEDIUM/4.5u and `ML_Away` at
MEDIUM/3.0u. The contamination reached the ledger:

```
2026-06-17-111  F5_ML_Away  ticker KXMLBF5-26JUN171915SFATL-SF     stake 4.5  result null
2026-06-17-112  ML_Away     ticker KXMLBGAME-26JUN171915SFATL-SF   stake 3.0  result null
```

Both still unsettled, ~3 months later.

**Partial fix already landed, sibling path still open.** PR #172 (2026-08-31)
states *"a doubleheader can never be resolved from DATE+AWAY+HOME"* — that fix
landed in the **market-linkage/settlement** path. The **production slate's**
`kalshiKey` is still date+teams. The post-fix behaviour (08-17 onward) is
*refusal* — both legs get `None` tickers and `Missing Data` across the board —
which is safe but means **a doubleheader silently produces zero market coverage
for both games**. This is the classic "fixed one path, left the sibling
vulnerable" pattern.

**Finding I-2 (MEDIUM).** 52 of 165 `marketLedger` rows on the current slate
carry `marketTicker: null` (including every `ML_Away`/`ML_Home` row). A row that
reaches `Accepted` without a ticker cannot be settled, CLV'd or reconciled by
exact contract.

**Finding I-3 (informational, and a strength).** No doubleheader `G1`/`G2` event
ticker appears in any of the 325 archived Kalshi snapshots. Either Kalshi did not
list both legs in this window or the capture did not see them. `UNKNOWN` — worth
one targeted check, because the parser (`lib/kalshi_mlb_contract_parser.py:97`)
is ready for the marker and has never been exercised against real data.

---

## SECTION 4 — KALSHI UNIVERSE / MARKET-SEMANTIC CERTIFICATION

All 17 archived MLB series were checked. Semantics were verified two ways:
against the repository's own parser/settler, and against **independent logical
consistency in the archived price data** (ladder monotonicity — a semantic
inversion on any rung breaks it).

| Series | Family | Horizon | YES means | Threshold convention | Settlement (`lib/edgelab/settlement.py`) | Ladder violations | Certification |
|---|---|---|---|---|---|---|---|
| `KXMLBGAME` | game_result | full | named team wins | — | winner == ticker side | — | **CERTIFIED** |
| `KXMLBF5` | inning_result | F5 | team wins/ties F5 | 3-way (Away/Tie/Home) | `settle_inning_result` on `periodScores.F5` | — | **CERTIFIED** |
| `KXMLBF3` | inning_result | F3 | ditto | 3-way, `-TIE` ticker directly observed 2026-08-18 | same | — | **CERTIFIED** |
| `KXMLBF7` | inning_result | F7 | ditto | 3-way, direct evidence | same | — | **CERTIFIED** |
| `KXMLBTOTAL` | game_total | full | combined runs **≥ N** | pure integer | `(away+home) >= threshold` | 6 / 3,783 (0.16%) | **CERTIFIED** (`≥`, not `>` — verified against MLB ground truth, `EDGELAB_KALSHI_TOTAL_LADDER_SEMANTICS.md`) |
| `KXMLBF5TOTAL` | inning_total | F5 | F5 runs ≥ N | pure integer | same, on `periodScores.F5` | 0 / 2,081 | **CERTIFIED** |
| `KXMLBTEAMTOTAL` | team_total | full | team runs **> N−0.5** | `TEAM` + digit ⇒ N−0.5 | `team_runs > threshold` | 15 / 4,771 (0.31%) | **CERTIFIED** |
| `KXMLBSPREAD` | winning_margin | full | team wins by **> N−0.5** | `TEAM` + digit ⇒ N−0.5 | `(team−opp) > threshold` | 11 / 1,758 (0.63%) | **CERTIFIED** |
| `KXMLBF5SPREAD` | winning_margin | **F5** | F5 margin > N−0.5 | same | **horizon-safe** since the F5-spread fix | 6 / 677 (0.89%) | **CERTIFIED** (the previously-broken full-game settlement is fixed; `_PERIOD_HORIZONS` guard verified in source) |
| `KXMLBRFI` | first_inning_run | F1 | **YRFI** (a run scores) | binary, no suffix | `(away+home) > 0` | — | **CERTIFIED**; NRFI is the complement, priced as `100 − yrfi_bid` |
| `KXMLBKS` | pitcher_strikeouts | full | K ≥ N | 4-segment ticker | `player_prop_settlement` | — | CERTIFIED (research-only) |
| `KXMLBOUTS` | pitcher_outs | full | outs ≥ N | 4-segment | same | — | CERTIFIED (research-only) |
| `KXMLBHIT` / `TB` / `HRR` / `RBI` / `SB` | hitter props | full | stat ≥ N | 4-segment, shared parser | same | — | CERTIFIED (research-only) |

**No cross-family contradiction was found.** The two historical semantic defects
the CEO brief referenced — F5-spread settling on the full-game margin, and the
total-rung `>` vs `≥` error — are both **genuinely fixed** in current main and
the fixes are structurally guarded (`_PERIOD_HORIZONS`, and an explicit comment
citing the MLB ground-truth verification). This is the strongest section of the
system and should be **left alone**.

One note: `KXMLBSPREAD` has no rung `1`. Rung N encodes "win by more than N−0.5",
so rung 1 would duplicate the moneyline; Kalshi does not list it. The intended
ML-vs-spread-rung-1 equivalence check is therefore not available — an absence
that *confirms* the N−0.5 convention rather than leaving it unverified.

---

## SECTION 5 — PROJECTION ENGINES (production math as actually executed)

The entire production run-scoring model is
`scripts/build_market_ledger.py::compute_projections()`, ~90 lines:

```
away_proj = (away_baseline/4.5) × (home_SP_IP × home_xFIP/9 + home_pen_IP × home_pen_xFIP/9)
            + park_adj + away_platoon_rpg
f5_away   = (away_baseline/4.5) × (min(home_SP_IP,5) × home_xFIP/9 × home_TTO_adj)
            + park_adj×(5/9) + away_platoon_rpg×(5/9)
```

then Poisson: `p_team_wins`, `p_over_total`, `poisson_pmf(0, λ₁)` for NRFI.

### Input classification

| Class | Inputs |
|---|---|
| **A — empirically supported** | starter xFIP; bullpen season xFIP; team R/G blend with Bayesian shrinkage (15/35 toward 4.5); opponent-quality adjustment (rolling 15); confirmed-lineup wOBA delta (capped ±0.25); bullpen workload multiplier (capped, ≥1.0, missing-safe) |
| **B — baseball-reasonable, weakly validated** | platoon RPG adjustment; park factor scaled ×0.5; `avgIPperStart` as expected starter IP; TTO adjustment `1 − ttoSplit×0.15` |
| **C — legacy assumptions** | **xFIP used directly as runs-allowed-per-9** (FIP is an ERA-scale, earned-runs-only metric; RA9 runs ~8% higher); Poisson run distribution; `9.0 − starter_IP` for bullpen innings; extra-inning blend `p×0.90 + 0.05`; **win-prob ceiling 0.72**; xFIP clamp [2.80, 5.50]; pen xFIP clamp [2.5, 6.0]; proj clamp [2.5, 7.0]; F5 clamp [1.2, 4.1] |
| **D — duplicated implementations** | `api/slate.js` is a **complete second engine** (`wrcImplied`, `modelProb`, `allEdges`, `teamTotals`, `nrfi`) |
| **E — unsupported / default** | `away_pen_xfip = bp.get('xFIP') or 4.0`; `avgIPperStart or 6.0`; `parkFactor or 100`; `poisson_pmf(0, 0) == 0.0` (should be 1.0) |

### Finding M-1 (CRITICAL) — two production probability engines that disagree

`api/slate.js` and `scripts/build_market_ledger.py` each compute a full set of
game probabilities into the **same `data/slate.json`**.

Measured over 251 games in archived authoritative slates from 2026-08-15:

| Metric | Value |
|---|---|
| mean \|JS − PY\| on `ML_Away` model probability | **1.76 pp** |
| median | 1.51 pp |
| p90 / p99 / max | 3.58 / 6.22 / **8.86 pp** |
| games diverging by **> 1.0 pp** (the entire PAPER floor) | **166 / 251 = 66%** |
| games diverging by **> 3.0 pp** (the HIGH threshold) | 38 / 251 = 15% |

`RUN_THE_SLATE.md:129,229` correctly states `marketLedger` is authoritative and
`allEdges` "is not the coverage source of truth". But `allEdges` still ships a
fully-formed competing recommendation — `confidence: "HIGH"`, `betSize: 6`,
`actionable: true` — in the same file a human or LLM reads. On the current slate,
MIL@CIN carries `allEdges.confidence = HIGH, betSize 6, actionable true` while
the authoritative `ML_Away` row is **Rejected**.

### Finding M-2 (CRITICAL) — the NRFI/YRFI distribution is structurally wrong

`p_nrfi = poisson_pmf(0, λ_home) × poisson_pmf(0, λ_away)`, with λ falling back to
`proj / 9`. At a typical 4.3 R/G that gives λ≈0.478 and `p_yrfi ≈ 1 − e^{−0.956}
≈ 0.615`, against a market that prices YRFI near 0.485.

Measured over archived authoritative slates from 2026-08-15 (n=246 rows):

| Market | mean(model − market) | median | share of games model > market |
|---|---|---|---|
| **YRFI** | **+11.87 pp** | +12.04 | **100.0 %** |
| **NRFI** | **−11.87 pp** | −12.04 | **0.0 %** |

A 100.0%/0.0% split across 246 independent games is a **mean bias, not an edge**.
Run scoring within a single inning is heavily over-dispersed relative to Poisson
(innings are 1-2-3 or they blow up), so `e^{−λ}` materially understates P(zero
runs). This is the mechanical cause.

**Mitigated today**: `RFI_SUSPENSION_REASON` (MLB-RSCH-0032) forces both sides to
PAPER, and this audit verified **148 / 148** archived Accepted YRFI rows since
2026-08-15 carry `confidence = PAPER`. The suspension holds. But it was justified
on Brier score, not on this structural defect — so the reactivation criteria do
not currently test for the thing that is actually broken.

### Finding M-3 (HIGH) — no home-field advantage term, and a measurable away bias

`grep -rniE "home_?field|hfa|home_advantage"` over `scripts/`, `lib/` and `api/`
returns matches **only in research modules** (`run_games_1_10_confirmation_experiment.py`
reads a frozen `homeFieldAdjustment = −0.0065` from RSCH-0017;
`run_distribution_calibration_experiment.py` fits one). The production engine has
**no home-field term at all**.

Consequence, measured (2026-08-15 onward):

| Market | mean(model − market) | share above market |
|---|---|---|
| `ML_Away` | **+3.81 pp** | **70.4 %** |
| `ML_Home` | −4.01 pp | 29.6 % |
| `F5_ML_Away` | +0.12 pp | 52.1 % |
| `F5_ML_Home` | **−5.65 pp** | 23.8 % |
| `TT_Away_Over` | −0.21 pp | 54.3 % |
| `TT_Home_Over` | −2.84 pp | 44.5 % |

And in the resulting book (real-money-tier Accepted rows, same window):
ML **63% away**, F5 ML **71% away**, TT **60% away**.

### Finding M-4 (MEDIUM) — the win-probability clamp produces incoherent pairs

```python
p_home_net = 1 - p_away_net
p_away_net = min(p_away_net, 0.72)
p_home_net = min(p_home_net, 0.72)
```
`p_home_net` is computed as the complement **before** the clamp, then clamped
independently. A game where the model says 80/20 emerges as **0.72 / 0.20 — a
pair summing to 0.92.** Directionally conservative (heavy favourites become
unbettable) but the two sides of the same binary no longer describe one
probability distribution, and `PY = 72.0` appears verbatim in the divergence
outliers in M-1.

### Finding M-5 (MEDIUM, latent) — `poisson_pmf(0, 0) == 0.0`

`build_market_ledger.py:161`: `if lam <= 0: return 0.0`. A Poisson(0) is a point
mass at 0, so `P(X=0)` must be **1.0**. `MLB-RSCH-0010` **found and fixed exactly
this bug — inside research only**, deliberately not touching the production
function (documented in that artifact). In production, a λ of 0 (an opener with
`f5_starter_ip = 0`, or a zero projection) would yield `p_nrfi = 0` ⇒
**`p_yrfi = 1.0`**. Currently unreachable because λ falls back to 0.5, but the
guard is a fallback, not a proof.

### Per-family production status

| Family | Production-eligible | Real-money tier reachable | Model |
|---|---|---|---|
| Full-game ML | yes | MEDIUM only in practice (HIGH capped by 7pt disagreement rule) | Poisson from run projections |
| F5 ML | yes | MEDIUM | three-way, tie-aware (`f5_three_way_v1`) — a genuine improvement |
| Team total (Over only) | yes | MEDIUM | `p_over_total(proj, N−1)` — semantics **correct** post-v1.2 |
| NRFI / YRFI | yes | **PAPER only** (suspended) | Poisson, structurally biased (M-2) |
| Game total | Rejected always | no | suspended (WR 41%, CLV −1.43%) |
| Run line | Rejected always | no | suspended |
| F3 / F7 | not wired | no | `productionEnabled: False` |
| Pitcher K / outs, hitter hits/TB/RBI/HRR/SB | research only | no | rich hitter stack, not production-wired |

**There is no "Under" or "NO" side anywhere in the production ledger.** The 11
required markets are all YES purchases on Over/Win contracts. The system is
structurally incapable of expressing a bearish view.

---

## SECTION 6 — OFFENSIVE HANDICAP DEPTH

**What the game model actually uses for offence:** one scalar per team,
`offenseBaselineAdj` = `blend(last7, last15, season R/G)` → Bayesian shrink
`(15·raw + 20·4.5)/35` → `+ oppQualityAdj` → `+ lineupAdj` (capped ±0.25, only
when `lineupConfirmed`), clamped to [2.5, 7.0]. Then `/4.5` to form a
multiplicative factor. Plus a bounded platoon RPG term.

**What is captured, written into `slate.json`, and never read:**

| Field | Written by | Read by production math |
|---|---|---|
| `teamWOBA` (xwOBA) | `enrich_data.py:136` | **0 references** |
| `teamBarrel` | `enrich_data.py:141` | **0 references** |
| `teamHardHit` | `enrich_data.py:140` | **0 references** |
| `teamFBPct` | `enrich_data.py:137` | **0 references** |
| `teamKPct` | `enrich_data.py:139` | 3 (JS/NRFI reason strings only) |
| `rpgIndex` | `enrich_data.py:127` | **0 references** |

**Lineup ordering is not used at all** — only an aggregate wOBA delta. No
hitter-vs-pitcher context, no pitch-mix, no per-batter Statcast reaches the game
model. The hitter research stack (`lib/research/hitter_*` — 15 modules, pitch
taxonomy, PA outcome model, contact model, bat tracking, sprint speed, catcher
framing) is fully built and produces its own boards, but **none of it flows into
the game model**, and `slate.json` preserves the aggregate scalars rather than
the underlying detail, so it is not available for manual handicapping either.

**Classification:** the *plumbing* (getting xwOBA/barrel/lineup-order into a
handicap packet) is an **engineering** task. Whether any of it improves
calibrated probabilities is a **Research Lab** question — and `MLB-RSCH-0012
(Offense Talent)` and `MLB-RSCH-0005 (Team Offense Recency)` have already begun
answering it.

---

## SECTION 7 — STARTING PITCHER / WORKLOAD / BULLPEN

| System | Production inputs | Quality |
|---|---|---|
| **Starting pitcher** | `xFIP` (fallback `seasonFIP`), clamped [2.80, 5.50]; `ttoSplit`; `openerRole` | Thin. `fbPct`, velocity trends and contact-suppression metrics are fetched by `fetch_savant_pitchers.py` and not consumed by `compute_projections`. `recentFIP` is read and then **discarded by a no-op** (`away_xfip = away_xfip` on both branches of the negative-recentFIP guard, `build_market_ledger.py:987-990`) — dead code that reads as an adjustment. |
| **Workload** | `avgIPperStart` (default 6.0, capped 9.0); `openerRole` ⇒ F5 starter IP 0; `min(IP, 5.0)` for F5 | No pitch count, no rest days, no recent usage, no innings restriction. `lib/research/pitcher_workload_projection.py` and `MLB-RSCH-0004` exist but are research-only. |
| **Bullpen** | season `xFIP` (default 4.0) × `compute_bullpen_workload_adjustment(recentUsage)`, clamped [2.5, 6.0] | The workload adjustment is the **best-designed input in the model**: capped, always ≥1.0, and returns 1.0 on missing data rather than fabricating a rested bonus. No closer/setup availability, no handedness, no high-leverage split (despite `fetch_savant_bullpen_hl.py` fetching one), no manager tendency. |

**Duplicated/stale:** the `recentFIP` no-op above; `fetch_savant_bullpen_hl.py`
output unconsumed by the game model.

---

## SECTION 8 — CALIBRATION: WHAT IS ACTUALLY TRUE TODAY

**What production applies:** a single multiplicative shrink on the edge,
`config/rules.json`:

```json
"calibration": {
  "High":   {"factor": 0.187, "n_settled": 52, "wr": 0.519},
  "Medium": {"factor": 0.255, "n_settled": 76, "wr": 0.658},
  "Paper":  {"factor": 0.180, "n_settled": 41, "wr": 0.512}
}
```

There is **no family-specific calibration**, no probability-band calibration, no
price-band calibration, no data-quality-conditioned calibration. Every family
uses `CAL_MEDIUM = 0.255`.

**Verified current truth:**

1. `MLB-RSCH-0022` (n=3,137 / 293 games / 13 families, walk-forward on archived
   production predictions) — **still the standing conclusion, and this audit's
   independent measurements are consistent with it**:
   Brier 0.2268 (model) vs 0.1719 (Kalshi); ECE 0.1020 vs 0.0445; paired Brier
   delta **+0.0549**, 90% CI [+0.0391, +0.0718], p≈0.
   FDR-significant model-worse in `game_total`, `inning_result`, `game_result`,
   `team_total`, `winning_margin`, `pitcher_strikeouts`. **None** replicated as
   model-better.
2. Overconfidence signature confirmed: model 0.0–0.2 band realises 0.233
   (bias −0.139); model 0.8–1.0 band realises 0.801 (bias +0.100).
3. `MLB-RSCH-0023` (global R1 + tiered R2 logit-affine shrink) — **failed VAL
   replication**, retired at LEVEL 0. `MLB-RSCH-0025` (mean calibration) — passed
   DEV+VAL, **failed its own 2026 holdout**, retired REJECT.
4. The 2026-09-03 10-day review returned **NO CHANGE**, correctly, because no
   correction had been validated.
5. The forward holdout is stuck at **CHECKPOINT_2 (INTERMEDIATE_UNCONFIRMED)**,
   913 rows / 47 games, needing ≥1,000 rows / ≥60 games. **It cannot advance,
   because settlement has been dead since 2026-08-31 (see W-1).** The 09-03 review
   attributed the absence of new forward data to games not yet having settled;
   four more days have since passed with no new settlements. The stall is
   infrastructural, not natural.

**The uncomfortable synthesis.** A shrink factor of 0.255 is an implicit
statement that the model captures about a quarter of its claimed edge. RSCH-0022
says the model is *worse* than the price. A shrink factor consistent with that
evidence would be ≤ 0. The calibration constants are not a correction for
overconfidence; they are **in-sample win rates from 41–76 settled bets**
repurposed as a shrink.

**No calibration correction is proposed here**, per the brief. The correct next
step is empirical, not engineering: unblock settlement, let the preregistered
forward holdout reach CHECKPOINT_3, and read the answer.

---

## SECTION 9 — MARKET-RELATIVE EDGE

**The formula, exactly:**

```
exec_prob            = yes_ask_cents / 100      if yes_ask_cents is not None
                     = kalshi_vf (MIDPOINT)     otherwise          ← always taken
rawEdgeVsExecutable  = (model_prob − exec_prob) × 100
feeAdjBreakEven      = exec_prob + 0.07·exec_prob·(1−exec_prob)
expectedFeeDrag      = (feeAdjBreakEven − exec_prob) × 100
netRawExecutableEdge = rawEdgeVsExecutable − expectedFeeDrag
netExecutableEdge    = netRawExecutableEdge × 0.255      ← THE qualification metric
```

Fees are applied exactly once, by shifting the reference price, then the same
calibration factor is applied to gross and net. **That part is correct and well
reasoned** (`docs/PRODUCTION_FEE_AWARE_NET_EV.md`).

**Sharp-market use is limited to hard rejection gates**, never to conditioning:
- Rule 71: |model − PinnacleVF| > 8pp ⇒ hard reject (ML only)
- Rule 71-F5: > 12pp vs Kalshi VF ⇒ hard reject (F5 only)
- `cap_tier_for_disagreement`: |rawEdgeVsVF| > 7pp ⇒ HIGH capped to MEDIUM

**Answer to the question posed:**

> Are we (A) a baseball prediction model that compares itself to Kalshi, or (B) a
> market-error model that understands when its disagreement is reliable?

**Unambiguously (A).** The system computes a probability, subtracts the market's
probability, multiplies by 0.255, and bets if the result clears 1.0. It never
asks *when* its disagreement has historically been informative. The three gates
above are the only acknowledgement that a large disagreement is more likely to be
model error than alpha — and they are blunt thresholds, not conditional models.

**The architectural gap.** A professional market-relative system would carry, per
family and per regime, an explicit **P(model is right | disagreement size, price
band, data-quality state, time-to-first-pitch)** learned from settled outcomes,
and would size on that posterior rather than on the raw disagreement. The
repository already has the substrate for exactly this —
`lib/edgelab/market_comparison.py`, `market_intelligence.py`,
`model_evaluation.py`, and MLB-RSCH-0024 (Market Residual) / 0026 (Kalshi Internal
Efficiency) / 0029 (Edge Decomposition). **This is the single highest-value
architectural change available**, and it is mostly assembly of parts that already
exist.

---

## SECTION 10 — EXECUTABLE PRICE / ORDER BOOK / FEES

### Finding P-1 (CRITICAL) — every production price is a midpoint. Issue #53 is STILL VALID.

**The chain, traced end to end:**

1. `scripts/build_kalshi_registry.py:194` — `def american(mid)`. Line 846 of the
   same file documents the field: *"Equivalent American odds derived from mid"*.
2. `merge_odds.py` writes into `slate.json` only the **selected-line summary**:
   ```json
   "team_totals": {"away": {"best_ticker": "...MIL5", "line": 5,
                            "implied_pct": 51.5, "american": -106,
                            "all_lines": [ {..., "yes_bid": 0.51, "yes_ask": 0.52, ...} ]}}
   ```
   The real `yes_ask` exists — **one level down, inside `all_lines`**.
3. `build_market_ledger.py:1629` reads `tt_side.get('yes_ask')` on the **top-level
   summary**, which has no such key ⇒ `None` ⇒ falls back to `tt_implied`
   (the mid) ⇒ `tt_yes_ask_c = mid`.
4. `build_market_ledger.py:1401-1410` does the same for ML: `ml.get('away_yes_ask')`
   does not exist in the slate (verified on live data — the `ml` block contains
   only `away`, `home`, `away_ticker`, `home_ticker`, `source`), so it converts
   `ml_away_am` — the **mid-derived American** — into `away_yes_ask_c`.
5. `build_edge_fields()` labels the result `executablePriceUsed`,
   `executableMarketProb`, `rawEdgeVsExecutable`, `netExecutableEdge`.

**Measured on the live slate (`data/slate.json`, 2026-09-06, 15 games):**

| | |
|---|---|
| Ledger rows with both `marketProbVF` and `executablePriceUsed` | 89 |
| `executablePriceUsed == marketProbVF` **exactly** (±0.06¢) | **64 (71.9%)** |
| Remainder | 25 rows, all within **0.5¢** — American round-trip rounding, still mid |
| Rows priced against a real ask | **0** |

Worked example, `ML_Away`, MIL@CIN:
`marketProbVF 57.447` · `executablePriceUsed 57.45` · `rawEdgeVsVF 7.686` ·
`rawEdgeVsExecutable 7.682`. The two "different" edges are the same number.

**Measured cost.** Mean bid-ask spread by family across 132,619 archived rows,
and the implied overstatement (half-spread) of every production edge:

| Family | mean spread | median | mid-vs-ask bias |
|---|---|---|---|
| moneyline | 1.06¢ | 1¢ | **0.53 pp** |
| f5_moneyline | 2.51¢ | 1¢ | **1.25 pp** |
| team_total | 3.46¢ | 2¢ | **1.73 pp** |
| game total | 1.78¢ | 1¢ | 0.89 pp |
| nrfi_yrfi | 1.43¢ | 1¢ | 0.72 pp |
| spread | 2.10¢ | 1¢ | 1.05 pp |
| f3_moneyline | 4.74¢ | 3¢ | 2.37 pp |
| f7_moneyline | 8.90¢ | 4¢ | **4.45 pp** |
| pitcher_outs | 11.00¢ | 3¢ | **5.50 pp** |

**Real-money consequence, measured against the ask prices already in the
archive** (Accepted team-total rows on authoritative slates ≥ 2026-08-15, ask
recovered from `all_lines` for the exact chosen ticker):

| | |
|---|---|
| Accepted TT rows | 135 |
| Would fall **below the 1.0 pp qualification floor** at the real ask | **7 (5.2%)** |
| Spread on the chosen rung | mean 1.57¢ · median 1.00¢ · p90 2.00¢ · **max 38.00¢** |

Examples (`netExecutableEdge` as shipped → recomputed at the real ask):
`26AUG301920CINCHC-CIN5` 1.157 → 0.900 · `26AUG221610WSHMIA-WSH4` 1.079 → 0.950 ·
`26SEP061410AZHOU-AZ4` 1.016 → 0.889.

The **max 38¢ spread** on a chosen rung is the tail that matters: at that width
the mid overstates the edge by 19 pp.

### Field-by-field price semantics audit

| Field | Documented meaning | **Actual meaning today** |
|---|---|---|
| `kalshiPrice` | Kalshi American odds | American from **mid** |
| `kalshiVF` / `marketProbVF` | vig-free market probability | **mid** (correct by definition) |
| `executablePriceUsed` | "yes_ask for YES bets" (`build_market_ledger.py:786`) | **mid** |
| `executableMarketProb` | probability from executable price | **mid** |
| `rawEdgeVsExecutable` | model − executable | model − mid |
| `netExecutableEdge` | fee-adjusted, calibrated, ask-based | fee-adjusted, calibrated, **mid**-based |
| `betUpToPriceNet` | fee-aware max payable ask | correct formula, compared against **mid** |
| `modelSnapshotPrice`, `executablePriceAtOutput` | archived entry references | **mid** |
| `mid` (registry) | (bid+ask)/2 | (bid+ask)/2, or **ask/2** on a one-sided book |

### What is correct

- **Fees are applied exactly once.** `kalshi_fees.py` is the single engine for
  production and research; `expectedFeeDrag` shifts the reference price and the
  same `cal_factor` is then applied to gross and net — no double-count. The
  `KALSHI_EXECUTABLE_PAYOUT_DOUBLE_FEE_AUDIT.md` fix holds.
- **Unused budget is no longer treated as a loss** (the `simulate_order`
  correction pass) — a real and correctly-reasoned fix.
- **`bet_up_to` is genuinely derived** from the model's own edge requirement
  rather than echoing the observed price, and the net ceiling provably never
  exceeds the gross ceiling. Good work, wrong input.
- **NO-side economics are not a live risk**: production only ever buys YES on a
  named contract. The one NO-adjacent path (NRFI priced as `100 − yrfi_bid`) is
  the *correct* complement.

---

## SECTION 11 — LIQUIDITY / DEPTH / EXECUTION REALISM

`grep -niE "open_interest|openInterest|volume|depth|liquidity|queue|fill"` across
`build_market_ledger.py`, `risk_gate.py` and `write_pending_bets.py` returns **no
liquidity concept whatsoever** (the five hits are the words "filled", "fills" and
"UNDERFILL" in unrelated prose and a portfolio warning string).

| Dimension | Production | Research |
|---|---|---|
| Bid-ask spread | not read | captured |
| Top-of-book size | **not captured at all** | captured (MLB-ALPHA-0002 L2) |
| Depth beyond top | **not captured** | captured |
| `volume` / `open_interest` | present in every snapshot row, **never read** | used |
| Queue position | — | `queue_observation.py` |
| Maker vs taker | assumed **taker** everywhere (correct and conservative) | maker feasibility studied |
| Partial fills | not modelled | `maker_feasibility_eval.py` |
| Price movement during execution | not modelled | night-before timing research |

**Where the system can call something profitable with insufficient size:** a
1-contract ask at 52¢ backed by a 4,000-contract offer at 61¢ is indistinguishable
from a deep 52¢ market, because only top-of-book is captured and even that is not
consulted. Combined with P-1 (mid pricing) and DS-1 (halved mid on one-sided
books), the thinnest, widest, most adversely-selected rungs are exactly the ones
most likely to look attractive and to be selected by `best_line`.

**THEORETICAL EDGE and EXECUTABLE EDGE are not separated anywhere in
production.** They are separated rigorously in research.

---

## SECTION 12 — QUALIFICATION PATH AND FAMILY CERTIFICATION

**Traced path (single canonical chain, verified — no bypass found):**

```
compute_projections → model_prob
   → build_edge_fields(model_prob, kalshi_vf, yes_ask_cents, CAL_MEDIUM, series_ticker)
        → netExecutableEdge                     ◄ THE canonical qualification field
   → confidence_from_edge(netExecutableEdge)    (≥3.0 HIGH · ≥1.5 MEDIUM · ≥1.0 PAPER)
   → cap_tier_for_disagreement(|rawEdgeVsVF| > 7 ⇒ HIGH→MEDIUM)
   → [family gates: Rule 50/51/52 lineup · Rule 71 / 71-F5 Pinnacle gap
      · Rule 34 · first-inning evidence quality · KXMLBRFI suspension]
   → enforce_bet_up_to(model_p, exec_price, ...) ⇒ conf=None if price > net ceiling
   → bet_size(conf, market) = min(8.0, round(base × MARKET_MULTIPLIER × 2)/2)
   → risk_gate.py: TT safety → same-game correlation → portfolio composition
   → write_pending_bets.py (live/final/postponed guard shared with risk_gate)
```

**Verified good:** one canonical edge field (`edgeUsedForQualification =
'netExecutableEdge'`, self-documenting on every row); fees applied once; no
recommendation path bypasses this chain; every gate appends to `gatesFired` and
maps to a structured `reasonCode`; the live-game guard is shared, not duplicated.

**Verified bad:** the "hidden midpoint use" the brief asked about **is present** —
not as a deliberate substitution but as the silent `else kalshi_vf` fallback at
`build_edge_fields:452` combined with the missing `yes_ask` key (P-1).

### Family certification — GREEN / YELLOW / RED

*Based on current evidence. No eligibility was changed by this audit.*

| Family | Real-money today | Empirical justification as of today | Verdict |
|---|---|---|---|
| **Team total Over** | yes (74 MEDIUM rows since 08-15) | RSCH-0022 Δ Brier **+0.0499** (market better), CI [+0.035,+0.064], p≈0, n=953 — **formally replicates as MARKET_BETTER**. Semantics fixed v1.2. 5.2% of Accepted rows fail at the real ask. | **RED** |
| **F5 ML** | yes (14 MEDIUM rows) | RSCH-0022 `inning_result` Δ +0.0467, p=0.008, FDR-significant market-better. `rules.json` multiplier 1.5 justified by **n=45, wr 0.56** in-sample. Three-way pricing is correct. Home side −5.65pp biased. | **RED** |
| **Full-game ML** | yes (6 MEDIUM rows) | RSCH-0022 Δ +0.0487, p=0.006, replicates MARKET_BETTER. `rules.json` itself marks ML `"status": "NEUTRAL_negative_clv"`, `avg_clv −1.58`, and still sizes it at 1.0×. No HFA term. | **RED** |
| **YRFI / NRFI** | no (PAPER, suspended) | Suspension verified holding: 148/148 rows PAPER. Structurally +11.87pp biased on 100% of games (M-2). | **RED — keep suspended**; reactivation criteria must be rewritten to test the distribution, not the Brier score |
| **Game total** | no (always Rejected) | Suspended: WR 41%, CLV −1.43% | **RED — correctly suspended** |
| **Run line** | no (always Rejected) | Suspended | **RED — correctly suspended** |
| **F3 / F7** | no (`productionEnabled: False`) | Semantics certified; no model validation; f7 spread 8.9¢ | **RED — correctly not enabled** |
| **Pitcher / hitter props** | no (research only) | RSCH-0022: the **worst** families vs market (`pitcher_outs` +0.1131, `pitcher_strikeouts` +0.0935) | **RED — correctly not enabled** |

**There is no GREEN family.** Not one production-eligible market family has
evidence of positive expected value against Kalshi's own price. The nearest thing
to a YELLOW is team total, where the semantic correction is real and recent
enough that the post-v1.2 record is too short to judge — but its own audit
already replicates as market-better.

---

## SECTION 13 — BEST EXPRESSION / CORRELATION

**What exists (and is good):** `scripts/risk_gate.py` carries an 11-pair
`CORRELATION_RULES` table over the 8 markets that can reach real money, with a
severity vocabulary (`DUPLICATE_THESIS` vs `MODERATELY_CORRELATED`) matching
`lib/edgelab/thesis_classification.py`. It correctly identifies ML↔F5 same-side as
duplicate thesis, side↔team-total and NRFI↔F5 as shared-driver, and NRFI↔YRFI as
both sides of one event. `GAME_MAX_REAL_MONEY_BETS = 2`,
`GAME_CLUSTER_MAX_STAKE_PCT = 0.15`.

**What does not exist:**

| Correlated exposure | Seen? |
|---|---|
| Same game, pairwise | **yes** |
| Same game, 3+ leg cluster stake cap | yes (0.15 of daily) |
| **Same team across games** (multi-day, or DH legs) | no |
| **Same starting pitcher across the slate** | no |
| **League-wide scoring environment** (all-Overs day) | **no** |
| **Same-direction concentration** | **no** |
| F3 vs F5 vs F7 vs full-game nesting | n/a (F3/F7 not enabled) |
| Correlated variance in the daily risk cap | no — `DAILY_RISK_CAP = 40.0` is a plain sum of stakes |

**The concrete exposure this misses.** Of 94 real-money-tier Accepted rows since
2026-08-15, **74 (79%) are team-total Overs** — and the ledger has no Under
market at all, so this is structural, not a view. On any given day the four
surviving TT bets (`TT_MAX_BETS = 4`) are four same-direction bets on the same
latent factor: league scoring environment, weather, umpire zone, ball
characteristics. The portfolio treats them as four independent positions summing
to 4×3.75u; their true joint variance is far closer to one 15u position.

`ML_F5_MIN_STAKE_PCT = 0.50` and the `ALL_TT_NO_ML_F5 ⇒ PAPER_ONLY` rule are
partial, heuristic acknowledgements of this — they force *some* family diversity
without ever measuring correlation.

---

## SECTION 14 — BANKROLL / STAKING / RISK

**What drives size today, in full:**

```python
MARKET_MULTIPLIERS = {'F5_ML_*': 1.5, 'TT_*_Over': 1.25, 'YRFI': 1.25,
                      'ML_*': 1.0, 'NRFI': 1.0, 'RL_*': 0.0, 'Game_Total': 0.0}
bet_size(conf, market) = min(8.0, round({HIGH:4, MEDIUM:3, PAPER:1}[conf] × mult × 2) / 2)
```

Two inputs: confidence tier and market family. Nothing else.

**Where the multipliers come from** (`config/rules.json`):

| Market | Multiplier | n | wr | avg_clv | status |
|---|---|---|---|---|---|
| F5_ML | 1.5 | **45** | 0.56 | +2.31 | ACTIVE |
| Team_Total | 1.25 | **30** | 0.57 | null | ACTIVE |
| YRFI | 1.25 | **13** | 0.58 | null | ACTIVE |
| ML | 1.0 | 76 | 0.55 | **−1.58** | `NEUTRAL_negative_clv` |
| NRFI | 1.0 | **6** | 0.50 | null | NEUTRAL |
| K_Prop | 1.5 | **6** | 0.67 | null | — |

These are **realized in-sample win rates on 6–76 settled bets, used directly as
stake multipliers.** A 45-bet sample at 56% has a standard error of ~7.4pp; the
observed result is under one standard error from a coin flip. Sizing F5 at 1.5×
on that basis is selection on noise. Sizing K_Prop at 1.5× on **n=6** is not a
decision, it is an artifact.

**Not present anywhere:** Kelly or any variance-aware sizing (correctly — the
brief forbids implementing one), edge-reliability weighting, liquidity-aware
sizing, uncertainty/dispersion input, correlation-adjusted stake, drawdown
control, bankroll-fraction linkage. `DAILY_RISK_CAP = 40.0` units and
`TT_MAX_STAKE = 20.0` are absolute constants unlinked to bankroll.

**Does this behave like a professional capital allocator?** No. It behaves like a
disciplined flat-bettor with family-level tilts fitted to small samples. The
discipline is real and worth preserving; the tilts are not evidence-based.

**What should eventually drive size** (research first, engineering second):
posterior probability that the model's disagreement is real, conditioned on
family/price band/data quality; executable depth at the target price; correlation
with the rest of the day's book; and an explicit bankroll fraction. Every one of
those is a research question this lab is equipped to answer.

**Deliberate legacy asymmetry to leave alone:** `build_risk_portfolio` does not
recompute `total_stake` after a TT downgrade, so a downgraded TT stake still
counts in the denominator of `tt_pct`. It is documented in-place as *"a precise,
load-bearing legacy asymmetry preserved exactly, not fixed."* That is the right
call — it makes the TT dominance test stricter, not looser.

---

## SECTION 15 — SETTLEMENT

`lib/edgelab/settlement.py::settle_market` was read line by line and its edge
cases independently reproduced against the family conventions in Section 4.

| Edge case | Behaviour | Verdict |
|---|---|---|
| Integer rung (game/inning total) | `(away+home) >= threshold` | correct — `≥` verified against MLB ground truth, not inferred |
| Half-point (team total, winning margin) | `> N−0.5` | correct; `>` vs `≥` immaterial at a half-point |
| Exact threshold | resolved by the two rules above | correct |
| **F3 / F5 / F7** | `_PERIOD_HORIZONS` forces `periodScores[horizon]`; a period-scoped market with no period score is `SETTLEMENT_UNRESOLVED` | **correct — this is the F5-spread fix and it holds** |
| Full game | requires `gameStatus == "Final"` and both scores | correct |
| Winning margin | `(team_runs − opp_runs) > threshold` on the market's own horizon | correct |
| NRFI / YRFI | `(away+home) > 0` on `firstInningRuns`; missing ⇒ UNRESOLVED | correct |
| Void / push | `Postponed`/`Cancelled`/`Suspended` ⇒ `VOID` | correct |
| Postponed | same | correct |
| Doubleheader | `mlbGamePk` left `null` on an unresolvable leg; market left unsettled | correct **refusal** (post-PR #172) |
| Corrected player stat | `settle_market_full` re-settles and re-links safely | correct |
| Missing period score | `missing_period_score_<H>` — never falls back to the full-game figure | **correct, and this is the important one** |
| Player props | `SETTLEMENT_UNRESOLVED`, `player_prop_settlement_not_implemented` at the family gate; `settle_market_full` settles them when boxscore context exists | honest |
| Tie (game_result) | resolves to `"TIE"` then to the ticker's own YES/NO | correct |

**Nothing invents a settlement.** Every failure path returns
`SETTLEMENT_UNRESOLVED` with a named reason. Exchange comparison exists via
`lib/edgelab/exchange_settlement.py` and `compare_confirmed_receipt_to_settlement`.

**This module is the second-best component in the repository and should be left
alone.** Its problem is not correctness — it is that it has not been *executed*
since 2026-08-31 (W-1).

---

## SECTION 16 — CLV

**Canonical convention** (`lib/edgelab/clv_convention.py`,
`POSITIVE_IS_GOOD_V1`): `clv = closing − entry`, side-relevant, explicit units,
`convert()` the only unit bridge, and an explicit refusal to substitute midpoint:
*"Midpoint is never used here."* A repository-wide **AST guard** fails the suite if
any assignment into a CLV-named target reintroduces `entry − closing` outside the
one named legacy helper. This module is exemplary and should be **left alone**.

### Finding C-1 (HIGH) — the root ledger is correctly signed and incorrectly labelled

`docs/EDGELAB_CLV_SIGN_AUDIT.md` states the root `bets.json` rows are *"not
recomputable"* (no `side`/`entryPrice`/`closingPrice`) and therefore stamps them
`LEGACY_ENTRY_MINUS_CLOSING`, with `scripts/generate_performance_report.py:234-240`
emitting to every report:

> *"Its CLV sign is the NEGATION of the canonical POSITIVE_IS_GOOD_V1 convention:
> here a **NEGATIVE** value means the bet beat the close."*

**That label is wrong.** The writer computes the canonical direction:

- `clv_update.py:1050` — `clv = (close_imp − our_imp) * 100`
- `clv_update.py:1074` — `_clv()` — `(close_imp − our_imp) * 100`
- `clv_update.py:1709` — `kalshi_clv = (our_closing_impl − bet_impl) * 100`
- `clv_update.py:1718` — `flag = '✓' if kalshi_clv > 0 else '✗'`

Recomputed independently from the rows' own `price` and `closingLine` fields
(restricted to cleanly-parseable `"<american> [book]"` closing strings):

| | |
|---|---|
| rows compared | 58 |
| match **canonical** (closing − entry) | **24** |
| match **legacy** (entry − closing) | **0** |
| match neither (older devigged sportsbook rows) | 34 — signs still track canonical |

The doc's conclusion was inferred from the absence of a migration rather than
from reading the writer. `reports/performance_2026-09-02.json` today carries
`"clvConvention": "LEGACY_ENTRY_MINUS_CLOSING"` on **correctly-signed data.**

**Blast radius.** The report's own *logic* is correct (its promotion rules read
`avg CLV >= 0.0%` as good), so no decision has been inverted. The risk is
forward-looking and real: any analyst, or any future remediation that "corrects"
the data to match the stamp, would flip every CLV conclusion in the primary
performance report and in `BET_LOG.md`.

### Finding C-2 (MEDIUM) — snapshot CLV closes on a mid

`scripts/clv_from_snapshot.py`'s own docstring: *"closing_implied = **mid** from
snapshot"*, and `get_mid_from_entry()` is the primary extractor (`yes_ask` is read
but explicitly *"used only for research/calibration, never..."*). This directly
contradicts `clv_convention.py`'s guarantee, in a module that
`tests/edgelab/test_clv_convention.py` lists as an `ACTIVE_WRITER` delegating to
the canonical helper. The delegation is real for the *sign*; the *price basis* is
not.

### Writer/reader inventory

| Surface | Sign | Price basis | Status |
|---|---|---|---|
| `lib/edgelab/clv.py` | canonical | executable (`yesAsk` / `100−yesBid`) | correct |
| `lib/edgelab/mlb_alpha_shadow.py` | canonical | fair-mid, **explicitly named non-executable** | correct |
| `scripts/clv_from_snapshot.py` | canonical | **mid** | C-2 |
| `clv_update.py` (root ledger) | canonical | American from mid | correct sign, mid basis |
| `scripts/generate_performance_report.py` | reads canonical, **labels inverted** | — | **C-1** |
| `scripts/research/clv_sign_audit/` | recognises legacy on purpose | — | correct (sanctioned) |

---

## SECTION 17 — LEDGER / ACCOUNTING

**Two ledgers coexist:**

| | root `bets.json` | `data/edgelab/bets/bets.jsonl` |
|---|---|---|
| rows | **556** | **385** |
| settled | 201 W / 185 L / 7 P / 6 V | 298 decided |
| unsettled | **156** | 86 pending |
| oldest unsettled | **2026-06-06** (31 distinct dates) | — |
| identity | `id` = `YYYY-MM-DD-NNN` | `betId` from `importBatchId` + `sourceBetKey` |
| CLV convention | canonical, **mislabelled** | canonical, migrated |
| idempotency | none | `DUPLICATE_NOOP` / `CORRECTED` / `CONFLICT` |
| lifecycle fields | minimal | full (`confirmedReceipt*`, `recordStatus`, provenance) |

**A 171-row divergence between the two authoritative ledgers is unexplained by
any reconciliation artifact in the repository.**

**Finding L-1 (HIGH) — 156-bet settlement backlog reaching back three months.**
Every one of these has `result: null`. Compounded by W-1, this backlog is growing
daily and cannot self-heal. It includes the two doubleheader-contaminated rows
from 2026-06-17.

**Finding L-2 (MEDIUM) — Issue #54 STILL VALID, reproduced verbatim on current main.**

`_ALWAYS_PRESERVE_FIELDS` (`lib/edgelab/bets.py:920`) carries forward
`status/result/returnAmount/netProfitLoss/closingPrice/clv/clvQuoteId/recordStatus`
— and **no `confirmedReceipt*` field**. `_content_fingerprint` pops only
`createdAt/updatedAt/recordedAt/sourceRow/provenance.ingestedAt` — so
`confirmedReceipt*` is compared. Reproduction:

```
SCENARIO: identical original payload resubmitted after settlement + confirm_realized_return
  fields still differing after lifecycle inheritance:
    ['confirmedReceiptAt', 'confirmedReceiptNetProfitLoss', 'confirmedReceiptNote',
     'confirmedReceiptReturn', 'confirmedReceiptSource']
  fingerprints equal? False  ->  CONFLICT   (issue #54 reproduced)
```

Exactly the five fields the issue predicted. Impact remains as the issue states:
no corruption (`on_conflict="reject"`), but the documented idempotency guarantee
is broken once a bet is confirmed.

**Verified good:** `sourceBetKey` + `importBatchId` identity is sound; `sourceRow`
is correctly excluded so payload reordering never manufactures a conflict; a
FINALIZED row can never be reset by a re-import; recommendations are never
auto-promoted to placed bets — **only user-confirmed wagers become PlacedBets**,
which is the correct and important invariant, and it holds.

**Reconciliation chain** (`stake → executed price → cost → fee → payout → return
→ P/L`) is implemented once, in `lib/edgelab/execution_economics.py` /
`kalshi_fees.simulate_order`, with `actualCashConsumed` and `unusedCash` separated
from `availableBudget`. Correct.

---

## SECTION 18 — FULL MARKET ARCHIVE

**This is the best-engineered subsystem in the repository.**

`data/kalshi/discovery/2026-09-06_coverage.json`:

```json
"coverageAccounting": {"archivedTotal": 457, "accountedTotal": 457, "unaccountedCount": 0,
  "byState": {"FULLY_EVALUATED": 114, "UNSUPPORTED_MODEL_FAMILY": 340,
              "PARSER_UNRESOLVED": 3, "GAME_MAPPING_UNRESOLVED": 0,
              "AMBIGUOUS_TICKER_MATCH": 0, "STARTED_GAME_EXCLUDED": 0,
              "NOT_EVALUATED_BUG": 0}},
"rawArchiveAccounting": {"totalRawEntriesSeen": 457, "entriesWithoutTicker": 0,
  "duplicateRawTickerCount": 0, "rawArchivedUnique": 457,
  "trueSilentRemainderCount": 0, "missingTickers": []}
```

Every archived market is accounted for by an explicit named state; there is a
`NOT_EVALUATED_BUG` bucket that exists purely to make silent gaps impossible; the
snapshot carries `discoveredUnknownSeriesMarkets` (0 today) so a newly-launched
Kalshi series is detected rather than silently dropped; a daily
`discover_kalshi_series_catalogue.py` independently enumerates series;
`snapshot-capture-check.yml` recovers missed captures and its commits are visible
in the run history.

All 17 MLB series are captured (`series_counts` on 2026-09-06: 485 markets across
`KXMLBGAME/SPREAD/TOTAL/TEAMTOTAL/F5/F5SPREAD/F5TOTAL/RFI/F3/F7/KS/OUTS/HIT/TB/HRR/RBI/SB`).

**One residual gap worth naming:** the accounting is **self-referential** —
`archivedTotal` is what discovery itself archived. The `discoveredUnknownSeriesMarkets`
detector and the series-catalogue pass are the only defences against the archive
missing an entire series, and both depend on the same enumeration call succeeding.
A single independent daily count (e.g. Kalshi's own series index vs the archive)
would close it. Low priority.

**Verdict: LEAVE ALONE.**

---

## SECTION 19 — SCHEDULED WORKFLOWS

**749 scheduled runs/day (~22,470/month)**, each performing a full checkout of a
2.8 GB repository.

| Workflow | runs/day | Purpose | Writes | Class |
|---|---|---|---|---|
| `capture-closing-lines` | **288** | pre-first-pitch closing snapshot for games within [−12, +5] min | `data/kalshi_market_registry.json` | prod support |
| `research-c01pit-shadow` | 90 | C01-PIT prospective shadow | research branch | research |
| `research-mlb-alpha-0002-capture` | 84 | L2 order book / queue observation | research branch | research |
| `clv_capture` | 78 | tracked-ticker CLV snapshots | `data/clv_snapshots/` | prod support |
| `hitter-snapshot-scheduler` | 68 | hitter prospective snapshots | `data/edgelab/` | research |
| `model-snapshot-scheduler` | 68 | prospective model snapshots | `data/edgelab/` | research |
| `capture-snapshots-scheduled` | 28 | broad kalshi_search archive | `data/kalshi_registry_snapshots/` | research |
| `edgelab-capture` | 28 | EdgeLab market observations | `data/edgelab/` | research |
| `research-night-before-capture` | 6 | night-before timing | research branch | research |
| `fetch-slate` | 3 | **the production pipeline** | slate, bets.json | **production** |
| `clv-update` | 1 | settlement + CLV | bets.json, BET_LOG.md | **production — FAILING** |
| `edgelab-postgame` | on `workflow_run` | settlement ledger | `data/edgelab/` | **production — SKIPPED** |
| `corpus-health-check` | 1 | health | report | **observability — not measuring the right thing** |
| 8 other daily | 8 | reports, scorer, statcast, snapshot check | various | mixed |

### Finding W-1 (CRITICAL, LIVE) — five-day silent production outage

**Reproduced locally on current main:**

```
$ python3 scripts/run_kalshi_clv_step.py 2026-09-05
Traceback (most recent call last):
  File ".../scripts/run_kalshi_clv_step.py", line 30, in <module>
    import clv_from_snapshot as snap_clv
  File ".../scripts/clv_from_snapshot.py", line 43, in <module>
    from lib.edgelab import clv_convention  # canonical CLV sign
ModuleNotFoundError: No module named 'lib'
```

**The exact mechanism**, which matters because it explains why the tests cannot
see it: when Python runs a **script**, `sys.path[0]` is the **script's own
directory**, not the working directory. So `python3 scripts/run_kalshi_clv_step.py`
starts with `scripts/` on the path and the repository root nowhere on it;
line 27 then inserts `scripts/` again, and `clv_from_snapshot.py:43` imports
`lib.edgelab` at module scope. Demonstrated:

```
$ python3    -c "import sys; sys.path.insert(0,'scripts'); import clv_from_snapshot"
imported OK                      # ← cwd IS on the path for -c; misleading
$ python3 -P -c "import sys; sys.path.insert(0,'scripts'); import clv_from_snapshot"
ModuleNotFoundError: No module named 'lib'      # ← -P drops cwd: matches a script run
$ python3 scripts/run_kalshi_clv_step.py 2026-01-01
ModuleNotFoundError: No module named 'lib'
```

The `from lib.edgelab import clv_convention` line was added by the
CLV-canonicalisation work itself. Its sibling `scripts/fetch_kalshi_clv_v2.py`
imports cleanly under the same path, so this is one module's top-level import,
not a packaging problem.

**Consequence chain, confirmed from GitHub run history:**

| Run | Date | `clv-update` | `edgelab-postgame` |
|---|---|---|---|
| 88 / 59 | 2026-09-01 | success | success |
| 89 / 60 | 2026-09-02 | **failure** | **skipped** |
| 90 / 61 | 2026-09-03 | **failure** | **skipped** |
| 91 / 62 | 2026-09-04 | **failure** | **skipped** |
| 92 / 63 | 2026-09-05 | **failure** | **skipped** |
| 93 / 64 | 2026-09-06 | **failure** | **skipped** |

The workflow's own summary states it exactly:
> *"Kalshi CLV step failed after settlement succeeded — bets.json/BET_LOG.md from
> clv_update.py were written but **NOT committed** (job stops before the commit
> step on any required-step failure)."*
> *"Backlog (non-terminal bets): before=156"*

So settlement is computed every night and **thrown away**, and
`edgelab-postgame.yml`'s `if: github.event.workflow_run.conclusion == 'success'`
turns one failing step into a silent five-day skip of the entire settlement,
recommendation and model-evaluation ledger.

Data effect: `data/edgelab/settlements/` and `recommendations/` end at
**2026-08-31** while `model_evaluations/` runs to **2026-09-06** — predictions
accumulate, outcomes do not.

The same import breaks `snapshot_coverage_check.py`, which is wrapped in
`|| true` and has therefore been silently degraded for the same period.

### Finding W-2 (HIGH) — `ODDS_API_KEY` carries trailing whitespace

From the same log, **before** the import failure, in the step that *succeeded*:

```
Fetching scores (daysFrom=2)...
Error: URL can't contain control characters.
       '/v4/sports/baseball_mlb/scores?apiKey=*** &daysFrom=2' (found at least ' ')
Auto-settled this run: 0
```

Note the space before `&`. The secret has trailing whitespace, so the **score
fetch has been failing independently of W-1** and `clv_update.py` auto-settles
zero bets while exiting 0. This is a second, independent cause of the settlement
backlog, and it would survive a fix to W-1.

### Other workflow findings

- **W-3 (MEDIUM).** `capture-closing-lines` runs 288×/day across all 24 hours,
  including ~06:00–15:00 UTC when no MLB game is within its ±12-minute window.
  Roughly 40% of its runs are structurally incapable of doing work, each paying a
  full 2.8 GB checkout.
- **W-4 (MEDIUM).** `workflow_run` + `conclusion == 'success'` chaining is used by
  `edgelab-postgame`, `edgelab-capture`, `edgelab-daily-report`,
  `build-wager-research`, `discover-kalshi-mlb-markets` and
  `edgelab-clv-collect`. Any upstream failure silently skips all of them with no
  alert. This is the mechanism that turned a one-line import bug into a total
  feedback-loop outage.
- **W-5 (LOW).** `fetch-slate.yml`'s BLOCK 8 comment claims
  `concurrency: group: fetch-slate-${{ github.ref }}`; the actual group is
  `edge-finder-ledger-writer`. Stale comment on a safety-critical mechanism.
- **W-6 (positive).** The scheduled `fetch-slate` trigger is correctly prevented
  from placing bets (`github.event_name != 'schedule'` on `risk_gate`,
  `write_pending_bets`, `validate_bet_logging`, `write_tracked_tickers`), and the
  test suite simulates GitHub's own `if:` semantics against the real YAML rather
  than a copy. Good.

---

## SECTION 20 — STORAGE / REPOSITORY ARCHITECTURE

| | |
|---|---|
| Working tree `data/` | **1.7 GB** |
| `.git` | **1.1 GB** |
| Total clone | **~2.8 GB**, pulled ~749×/day |
| Tracked files | 6,059 |

| Subtree | Size | Class |
|---|---|---|
| `data/kalshi_registry_snapshots/` (325 files) | 368 MB | **COMPRESS** then **MOVE TO RELEASE** — append-only, research-only, never read by production; the single largest win |
| `data/edgelab/` | 364 MB | mixed — `settlements/`, `bets/`, `model_evaluations/` **KEEP IN GIT**; `snapshots/`, `replay_runs/` (104), `observations/` **COMPRESS** |
| `data/pipeline/` | 309 MB | **COMPRESS** — per-date reproducibility inputs; `hitter_features.json` is 11–20 MB/day |
| `data/slates/` | 289 MB | **KEEP IN GIT** — `authoritative.json` is the audit system of record |
| `data/kalshi/` | 253 MB | **COMPRESS** — discovery/coverage; keep the coverage JSONs |
| `data/statcast_raw/` | 89 MB | **MOVE TO RELEASE** — regenerable from Savant |
| `data/research_cache/` | 46 MB | **PRUNE AFTER RETENTION** — by definition a cache |
| `bets.json` (535 KB) + `BET_LOG.md` | — | **KEEP IN GIT** |
| `reports/` | 32 KB | KEEP |
| `archive/` | 380 KB | **ARCHIVE** — already labelled non-authoritative |
| 36 stale feature branches on origin | — | **ARCHIVE** (tag and delete) |

**Are we storing the minimum durable evidence to reproduce research and audit
live behaviour?** No — we are storing considerably more than the minimum, in the
one medium (git history) where the cost is permanent and paid on every one of
749 daily checkouts. The immutable-snapshot design
(`docs/SNAPSHOT_ARCHITECTURE.md`) is the right idea; what is missing is a
retention and compaction policy behind it. `lib/snapshot_retention.py` exists.

**Reproducibility is genuinely strong**: `create_snapshot.py` writes hash-verified
frozen component copies, `run_forward_replay.py` verifies hashes before replaying,
and `MLB-RSCH-0033` demonstrated it can re-run `compute_projections` over archived
inputs and reproduce **636/678 team-games within 0.001**.

**Nothing was deleted by this audit.**

---

## SECTION 21 — DUPLICATION / TECHNICAL-DEBT MAP

`docs/DUPLICATE_LOGIC_INVENTORY.md` is a good, honest existing inventory. It
**misses the largest duplication in the system.**

| # | Concept | Canonical | Duplicate(s) | Divergence measured | Capital risk | Recommended owner |
|---|---|---|---|---|---|---|
| **1** | **Run projection + game probability** | `build_market_ledger.compute_projections` | **`api/slate.js`** (`wrcImplied`, `modelProb`, `allEdges`, `teamTotals`, `nrfi`, `runLineEval`) | **mean 1.76 pp, max 8.86 pp, 66% of games > 1 pp** | **HIGH** | Python; JS should carry data only |
| **2** | Executable-price math | `scripts/executable_price.py` | inline `except ImportError` copy in `build_market_ledger.py:96-108` | identical today | LOW (defence-in-depth) | keep, add a test that they agree |
| 3 | Sentinel-price detection | `lib/sentinel_validator.py` | `slate_manager.py`, `clv_validator.py`, `capture_clv_pregame.py`, `api/slate.js` — **drifted constant sets** | 5 impls, 3 constant sets | MEDIUM | `sentinel_validator` |
| 4 | Kalshi ticker-date formatting | none designated | **6 copies** | pure formatting | LOW | `lib/kalshi_ticker_time.py` |
| 5 | CLV computation | `lib/edgelab/clv_convention.py` | `clv_update.py` (canonical sign, mid basis), `clv_from_snapshot.py` (mid basis) | sign agrees, **price basis differs** | MEDIUM | `clv_convention` |
| 6 | Bet ledger | `data/edgelab/bets/bets.jsonl` | root `bets.json` | **556 vs 385 rows** | MEDIUM | edgelab ledger; root becomes a view |
| 7 | Game-status / live-game | `lib/postponed_guard.py` | `log_session_bets.py` naive check | script unwired | LOW | `postponed_guard` |
| 8 | Fee math | `lib/edgelab/kalshi_fees.py` | **none** | — | — | **already canonical — a model for the rest** |
| 9 | Settlement | `lib/edgelab/settlement.py` | `clv_update.py::get_result` (legacy root path) | separate universes | MEDIUM | `settlement.py` |
| 10 | Market-family mapping | `lib/research/market_taxonomy.SERIES_FAMILY_MAP` | `build_kalshi_registry.SERIES_CATALOGUE`, `api/kalshisearch.js ALL_SERIES` | 3 lists, kept in sync manually | MEDIUM | `market_taxonomy`, with a parity test |

**Modules most likely to create the next bug**, ranked:
1. `scripts/build_market_ledger.py` — 2,561 lines carrying the model, the edge,
   the fees, the tiers, the gates and the price fallbacks in one file.
2. `api/slate.js` — 2,208 lines of a second, undeclared model.
3. `clv_update.py` — 83 KB legacy settlement/CLV engine parallel to `lib/edgelab/`.
4. `scripts/build_kalshi_registry.py` — the `mid` definition everything inherits.
5. The workflow graph — 36 YAML files with implicit `workflow_run` dependencies.

---

## SECTION 22 — DEAD / LEGACY / SUPERSEDED

| Item | Evidence | Class |
|---|---|---|
| 24 of 232 `scripts/*.py` never referenced outside themselves (17 in `mlb_alpha_0001/`, 6 in `mlb_alpha_0002/`, 3 edgelab runners) | reference scan across all `.py/.yml/.md/.js` | **KEEP** — frozen research provenance; these are the experiment artifacts |
| `recentFIP` guard in `compute_projections:987-990` — `away_xfip = away_xfip` on both branches | source | **REMOVE** (reads as an adjustment, is a no-op) |
| `scripts/log_session_bets.py` | not wired to any workflow, last used 2026-06-18 | **DEPRECATE** |
| `scripts/stale_date_guard.py`, `scripts/data_quality_gate.py` | test-only copies of live logic | **NEEDS REVIEW** |
| `lib/clv_validator.py` | no production caller | **NEEDS REVIEW** |
| `archive/RULES_INDEX.md` | superseded by `config/rules.json`, correctly labelled | **ARCHIVE** (already) |
| `README.md` "Current record (June 4, 2026): 121W 106L 7P" and "GitHub Actions: 2 workflows" | ledger now 556 rows; 36 workflows exist | **REMOVE/REFRESH** — actively misleading |
| `MLB-RSCH-0031` / `0032` pricing sections | superseded by 0033/0034, correctly marked in-place, artifacts not rewritten | **KEEP** — exemplary supersession handling |
| `fetch-slate.yml` BLOCK 8 concurrency comment | names a group that does not exist | **REMOVE/REFRESH** |
| `data/edgelab/market_observations/` | 0 entries | **NEEDS REVIEW** |
| 36 stale `origin/*` feature branches | all merged milestones | **ARCHIVE** |
| `scripts/fetch_kalshi_clv.py` vs `fetch_kalshi_clv_v2.py` | v1 superseded | **NEEDS REVIEW** |

**Nothing was removed by this audit.**

---

## SECTION 23 — TEST SUITE QUALITY

**Result on current main, full git history, run twice:**

```
9623 passed, 9 skipped in 262.04s   (run 1)
9623 passed, 9 skipped in 258.33s   (run 2)
```

On the shallow clone this environment starts with, 4 tests fail
(`test_risk_gate_review_parts_v_to_y.py::…::test_only_risk_gate_py_changed…`,
3× `test_validate_slate_final_pr9_changed_file_scope.py`) with
`git diff fe0a19c..b006c39 → exit 128`. **Reproduced and diagnosed, not assumed:**
`git cat-file -e` reports both SHAs absent, `.git/shallow` exists, `git rev-list
--count HEAD` = 50. After `git fetch --unshallow` (5,427 commits) all 5 pass in
0.11 s. These are genuine environment-only failures — and the CI's deselect list
is **over-broad**: the 5th deselected test
(`test_kalshi_market_inventory.py::…::test_f5_tie_marked_as_dead_data_path_not_consumed`)
passes here.

### Finding T-1 (CRITICAL) — the tests import production entry points differently from production

This is *the* reason a five-day total outage went undetected by 9,623 tests.

```python
# tests/test_clv_snapshot_pipeline.py:33-38
sys.path.insert(0, _scripts)
sys.path.insert(0, _root)        # ← makes `lib` importable
import clv_from_snapshot as snap
```

```python
# scripts/run_kalshi_clv_step.py:26-30   (what production actually runs)
sys.path.insert(0, _here)        # scripts/ only — no root
import clv_from_snapshot as snap_clv     # ← ModuleNotFoundError: No module named 'lib'
```

`tests/edgelab/test_clv_convention.py:165` uses a third form,
`from scripts.clv_from_snapshot import calculate_clv`. Three import shapes, one
module, and the only one that fails is the one production uses. **No test invokes
any workflow step the way the workflow invokes it.**

### Other findings

- **T-2 (HIGH).** No test asserts that `executablePriceUsed` differs from
  `marketProbVF`, i.e. nothing protects the ask-vs-mid invariant. The most
  expensive defect in the system is entirely untested.
- **T-3 (HIGH).** No test asserts data freshness (settlement lag, ledger backlog).
- **T-4 (MEDIUM).** 4 tests are permanently coupled to hard-coded historical SHAs
  (`fe0a19c`, `b006c39`) and assert *which files a long-past PR changed*. They have
  no ongoing behavioural value and will break for real on any history rewrite.
- **T-5 (MEDIUM).** Extensive `grep`/AST "documented-absence" tests
  (`test_risk_gate_purity`, `test_slate_no_filesystem_io`, the CLV AST guard).
  The CLV AST guard is genuinely valuable; the file-scope ones are brittle.
- **T-6 (positive).** `tests/test_pipeline_dependency_graph.py` simulates GitHub
  Actions' own `if:` semantics against the **real YAML** and executes the literal
  embedded `jq` filter. That is exactly the right pattern — and if it were
  extended to *execute the steps' commands*, W-1 would have been caught.

### Issue #170 status

| Test | Status on current main |
|---|---|
| `test_replay.py` wall-clock second boundary | **FIXED** — `monkeypatch.setattr(replay.ids, "utc_now_iso", lambda: "2026-07-31T15:45:00Z")` freezes the clock, with the incident cited in-comment |
| 2× `test_settle_markets_player_props_integration.py` | **NOT REPRODUCED** in 2 consecutive full-suite runs |
| `test_ingest_market_observations_script.py::test_repeating_the_exact_same_invocation_is_idempotent` | **NOT REPRODUCED** in 2 consecutive full-suite runs |

**Verdict: PARTIALLY SUPERSEDED.** The one item the issue called "cheap to fix and
highest-value" is fixed. The other three could not be reproduced here; two runs is
not proof of absence, so the issue should stay open with its scope narrowed.

### The most dangerous behaviour not currently protected by a test

1. `executablePriceUsed` may equal the midpoint. *(P-1)*
2. A production workflow step may be unable to import its own dependencies. *(W-1)*
3. The registry `mid` may be half the true price on a one-sided book. *(DS-1)*
4. Two production engines may disagree about the same probability. *(M-1)*
5. Two doubleheader legs may receive the same Kalshi ticker. *(I-1)*

All five are encoded as executable invariants in
`tests/audit/test_audit_invariants_2026_09.py` added by this audit.

---

## SECTION 24 — OBSERVABILITY / HEALTH

Can the owner tell at a glance?

| Question | Answerable today | Evidence |
|---|---|---|
| Did data collection work? | partly | `data/fetch_status.json`, `pipeline_status.json` |
| Were all games captured? | **yes** | coverage accounting — excellent |
| Were all lineups confirmed? | yes | `lineupConfirmationState` per row |
| Are probabilities current? | yes | `meta.json` + stale-date guard |
| Is any provider stale? | **no** | no provider-freshness assertion |
| **Are settlements complete?** | **NO** | 6 days stale, health check green |
| **Is CLV available?** | **NO** | broken 5 days, health check green |
| Are research collectors healthy? | partly | `edgelab-daily-heartbeat` |
| Is full-market coverage intact? | **yes** | best-in-class |
| Did workflows persist their outputs? | **no** | `clv-update` computed and discarded output for 5 days |
| Is a family producing abnormal probabilities? | **no** | YRFI has been +11.87pp on 100% of games for months, unflagged |
| Is market mapping failing? | partly | `PARSER_UNRESOLVED` counted, not alerted |

**Finding O-1 (CRITICAL).** `corpus-health-check.yml` is the only daily health
gate and is explicitly designed to fail (`exit 1`, no `continue-on-error`). It
returned **success on 2026-09-02, 09-03, 09-04, 09-05 and 09-06** — every day of
the outage. It measures corpus shape, not operational freshness.

**Recommended (not built here): a single CEO health artifact**, written by a
workflow that fails when any assertion trips:

| Assertion | Today |
|---|---|
| `max(settlement partition date) >= today − 2` | **FAILING** (7 days) |
| `max(recommendation partition date) >= today − 2` | **FAILING** |
| `count(bets where result is null and gameDate < today − 3) <= 10` | **FAILING** (156) |
| every scheduled production workflow succeeded in the last 24 h | **FAILING** |
| no `workflow_run`-chained workflow skipped ≥2 consecutive days | **FAILING** |
| per-family `mean(model − market)` within ±5 pp over the last 14 days | **FAILING** (YRFI +11.87) |
| per-family share-above-market within [0.25, 0.75] | **FAILING** (YRFI 1.00, NRFI 0.00) |
| `executablePriceUsed != marketProbVF` on ≥90% of Accepted rows | **FAILING** (0%) |

Eight assertions, all computable from data already in the repository, and today
**all eight fail.** That is the gap in one table.

---

## SECTION 25 — PROFESSIONAL-BETTOR GAP ANALYSIS

| Principle | What an elite operation does | What this system does |
|---|---|---|
| **Price discipline** | Prices at the executable ask, always; treats mid as a research quantity | Prices 100% of production rows at the mid and labels it "executable" |
| **Executable EV** | Net of fees **and** of realistic slippage/fill probability | Net of fees only; slippage and fills unmodelled |
| **Market comparison** | Sharp consensus is the *prior*; the model must beat it out-of-sample to be traded | Sharp price used only as a 3-threshold veto; model traded despite being measurably worse |
| **CLV** | The primary KPI; a negative-CLV family is stopped, not resized | CLV computed, `ML` labelled `NEUTRAL_negative_clv` (−1.58) — and still sized at 1.0× |
| **Selection bias** | Understands that betting where you disagree with a better forecaster is adverse selection | Bets exactly there, by construction |
| **Edge calibration** | Family- and band-specific, validated out-of-sample, refit on a schedule | One global shrink from n=41–76 in-sample win rates; both validated recalibrations failed and were retired |
| **Uncertainty** | Probability + variance; size shrinks with uncertainty | Point estimates only; `dataQuality` gates but never sizes |
| **Correlation** | Joint exposure across games, factors and directions | Same-game pairwise only |
| **Portfolio exposure** | Factor limits (scoring environment, direction, pitcher, team) | 79% of the real-money book is same-direction TT Overs, unseen |
| **Bankroll discipline** | Stakes are a bankroll fraction; drawdown reduces size | Fixed unit constants unlinked to bankroll |
| **Information timing** | Explicit edge-decay model; bets when information advantage is greatest | 3 fixed cron times; night-before timing researched, not wired |
| **Lineup timing** | Waits for confirmation before real money | **Does this well** — Rules 50/51/52 |
| **Sharp consensus / movement** | Line movement is a first-class signal | Captured; not consumed by production |
| **Liquidity** | Size against depth; abstains when thin | No depth concept at all |
| **Pass discipline** | Most days are no-bet days | Structurally produces bets every slate; `PAPER_ONLY` is the only "pass" |
| **Avoiding forced action** | No minimum activity | `ML_F5_UNDERFILL` warns when ML/F5 is *under*-weighted — a mild nudge toward action |
| **Avoiding retrospective tuning** | Parameters frozen before the data | `MARKET_MULTIPLIERS` are literally realized win rates (n=6…76) |
| **Untouched holdouts** | Sacred | **Genuinely does this** — `FORWARD_START_DATE`, checkpoint ladder, byte-identical reruns |
| **Preregistration** | Standard | **Genuinely does this** — `experiment_registry`, `evidence_levels`, control hashes |
| **False-discovery control** | Standard | **Genuinely does this** — FDR at 10% in RSCH-0022 |
| **Research→production governance** | Documented promotion with a kill switch | **Genuinely does this** — human-gated; zero unvalidated promotions |

**The single sentence.** An elite operation would have read
`MLB-RSCH-0022` — its own audit, showing the market beats the model on proper
scoring rules in **every** family — and **stopped betting real money that day**,
reverting to paper until a forward-validated edge existed. This operation
published the finding, correctly refused to fit a fix to it, and **kept betting.**

The research half of this organisation is behaving like a professional shop. The
trading half is not listening to it.

---

## SECTION 26 — ARCHITECTURE REDESIGN

**No rewrite is recommended.** Incremental migration is clearly superior: the
domain logic is correct, the tests are real, and the archive is intact. What is
missing is a small number of *seams*.

### Target architecture (evolutionary, in dependency order)

1. **A canonical `Price` object.** One immutable value carrying
   `yesBid / yesAsk / noBid / noAsk / mid / last / basis` where `basis ∈
   {EXECUTABLE_ASK, MIDPOINT, LAST, VIG_FREE, DERIVED_COMPLEMENT}`, plus
   `topOfBookSize`. Every price field in every artifact carries its basis, and
   qualification **refuses** a basis other than `EXECUTABLE_ASK`. This single
   object closes P-1, DS-1, DS-2, C-2 and issue #53 permanently, and makes the
   class of bug structurally unrepresentable.
2. **A canonical `MarketIdentity`.** `(seriesTicker, eventTicker, marketTicker,
   mlbGamePk, doubleheaderGameNumber)` constructed once, propagated everywhere,
   never re-derived from date+teams. Closes I-1 and I-2.
3. **One probability engine.** Delete the model from `api/slate.js`; the JS layer
   becomes a pure data-transport tier. Closes M-1 and removes 2,208 lines of
   shadow logic.
4. **A `MarketRelativeEdge` service.** Replaces `model − market` with
   `P(model correct | family, disagreement, price band, data quality, time to
   first pitch)`, learned from settled outcomes. This is the highest-value change
   in the system and assembles existing parts
   (`market_comparison`, `market_intelligence`, `model_evaluation`,
   RSCH-0024/0026/0029).
5. **One ledger.** `data/edgelab/bets/bets.jsonl` becomes the system of record;
   root `bets.json` becomes a generated view. Closes L-1's reconciliation gap
   and the two-CLV-lineage problem.
6. **Health as a first-class artifact.** The eight assertions in Section 24,
   written by one workflow that fails loudly, replacing implicit trust in
   `workflow_run` chaining.
7. **Workflow consolidation.** ~749 runs/day → a small number of matrixed
   collectors with sparse checkouts. Replace `workflow_run` success-gating with
   explicit scheduled jobs that check their own preconditions, so an upstream
   failure degrades one job instead of silently skipping six.
8. **Retention and compaction behind the snapshot layer.** `lib/snapshot_retention.py`
   exists; wire it, compress `data/kalshi_registry_snapshots/`, move
   `statcast_raw` to release assets.

### What the redesign explicitly should NOT touch

`lib/edgelab/kalshi_fees.py`, `lib/edgelab/settlement.py`,
`lib/edgelab/clv_convention.py`, `lib/research/market_taxonomy.py`, the coverage
accounting, and the research governance stack. These are the parts that already
look like what the rest should become.

---

## SECTION 27 — SECURITY / SAFETY / CAPITAL PROTECTION

| Protection | Status |
|---|---|
| Research code placing orders | **STRUCTURALLY IMPOSSIBLE** — `grep -rniE "create_order\|place_order\|portfolio/orders"` across `scripts/`, `lib/`, `api/` returns **zero** matches. There is no trading API client and no trading credential in the repository. Every bet is placed by a human. |
| Recommendation treated as execution | **PROTECTED** — only user-confirmed wagers become PlacedBets; `write_pending_bets.py` writes PENDING rows that require explicit confirmation |
| Started games recommended | **PROTECTED** — `lib/postponed_guard.check_game_status` shared by `risk_gate`, `write_pending_bets`, `validate_bet_logging`, `validate_slate_final` |
| Stale slate used as current | **PROTECTED** — `validate_current_slate_date.py`, `stale_date_guard`, `meta.json`, `_authoritative` markers, `rejected_contaminated_*` files observed in `data/slates/` |
| Ambiguous ticker match | **PROTECTED in the archive** (`AMBIGUOUS_TICKER_MATCH` state, 0 today); **NOT protected in the slate** — see I-1 |
| Accidental historical rewrite | **PROTECTED** — `protect_slate.py`, sentinel checks, `write_json_atomic`, immutable-pipeline tests |
| Duplicate imports | **PROTECTED** — `sourceBetKey` + `importBatchId` + `on_conflict="reject"` (weakened only by issue #54, which errs toward refusal) |
| Research artifacts affecting recommendations | **PROTECTED** — `RESEARCH_ONLY_SERIES`, `risk_gate` purity tests, replay is `CANDIDATE_MODEL` only and writes only under `data/edgelab/replay_runs/` |
| Production writing to research branches | **PROTECTED** — only 2 workflows touch research branches, both research-only |
| API secrets in logs/artifacts | **PROTECTED** by GitHub masking (`apiKey=***` in the log). **But** W-2 shows the secret has trailing whitespace, which is a hygiene defect with a functional consequence |
| Destructive workflow behaviour | **MOSTLY PROTECTED** — path-scoped `git_data_commit.py`, `--autostash` rebase. 35/36 workflows hold `contents: write` on `main`, which is broader than necessary |

**Read-only research guarantee: CONFIRMED.** This is a genuine and unusual
strength. The worst outcome a bug here can produce is a *wrong recommendation to a
human*, never an unauthorised trade.

---

## SECTION 28 — GITHUB ISSUES / PR HISTORY

### Open issues (3)

| # | Title | Verdict | Evidence |
|---|---|---|---|
| **53** | Use executable Kalshi ask (not midpoint) for user-facing odds and bet-up-to enforcement | **STILL VALID — fully unfixed, and now measured** | 100% of production rows price at mid; 5.2% of Accepted TT rows fail at the real ask. The `bet-up-to` machinery the issue asked for **was built** and enforces against a mid — so the issue is *less* fixed than it looks. The `priceBasis` field the issue proposes is exactly the right remedy. |
| **54** | Manual-import idempotency breaks (CONFLICT instead of DUPLICATE_NOOP) | **STILL VALID** | Reproduced verbatim on current main; the same five `confirmedReceipt*` fields |
| **170** | Four nondeterministic tests | **PARTIALLY SUPERSEDED** | Replay/wall-clock item **fixed** (clock frozen in `test_replay.py`); other three **not reproduced** in 2 consecutive full-suite runs |

### Recent PR history — repeated patterns

**Pattern 1 — a fix lands on one path, the sibling path stays broken.**
PR #172 ("a doubleheader can never be resolved from DATE+AWAY+HOME") fixed the
*settlement/market-linkage* path. The *production slate's* `kalshiKey` is still
date+teams, so doubleheaders now silently produce zero market coverage instead of
wrong coverage (I-1). Same shape:
- The F5-spread horizon fix hardened `settlement.py`; nothing asserts the
  *production* F5 rows carry a horizon.
- The CLV sign canonicalisation migrated the edgelab ledger; the root ledger was
  left labelled inverted **on correctly-signed data** (C-1).
- The fee-aware net-EV milestone made qualification fee-aware; it did not notice
  the price it was made fee-aware *about* is a mid (P-1).

**Pattern 2 — a fix introduces the next outage.** The CLV canonicalisation added
`from lib.edgelab import clv_convention` to `scripts/clv_from_snapshot.py`. The
tests import that module with the repo root on `sys.path`; production does not.
Five-day outage (W-1).

**Pattern 3 — research finds a production bug and deliberately does not fix it.**
`MLB-RSCH-0010` found `poisson_pmf(0,0) == 0.0`, fixed it *locally in research*,
and documented leaving production untouched (M-5). Correct under the research
charter, but there is no mechanism that converts such a finding into a production
work item, so it simply sits.

**The architectural weakness these three patterns share:** each fix is scoped to
the file where the bug was observed, and the repository has no *invariant* layer
that would force the same guarantee across every path expressing the same concept.
That is precisely what the canonical `Price` and `MarketIdentity` objects in
Section 26 would provide.

---

## SECTION 29 — EFFICIENCY REGISTER

| Opportunity | Quantified today | Value | Effort |
|---|---|---|---|
| `capture-closing-lines` 24 h → game-hours only | 288 → ~170 runs/day (−118) | HIGH | LOW |
| Sparse/partial checkout for all collectors | ~749 × 2.8 GB/day of clone I/O | HIGH | LOW |
| Compress `data/kalshi_registry_snapshots/` (325 raw JSON) | 368 MB | HIGH | LOW |
| Move `data/statcast_raw/` to release assets | 89 MB, fully regenerable | MED | LOW |
| Prune `data/research_cache/` on retention | 46 MB | MED | LOW |
| Compact `data/pipeline/<date>/hitter_features.json` | 11–20 MB/day, unbounded growth | HIGH | MED |
| Delete 36 stale `origin/*` feature branches | git ref bloat | LOW | LOW |
| Consolidate 5 near-identical Kalshi collectors (`capture-snapshots`, `clv_capture`, `edgelab-capture`, `c01pit-shadow`, `alpha-0002`) into one matrixed job | 308 runs/day → ~90 | HIGH | MED |
| Reuse one Kalshi fetch across collectors instead of re-fetching | duplicate API load | MED | MED |
| Remove `api/slate.js`'s model | 2,208 → ~800 lines; removes M-1 | HIGH | MED |
| Retire root `bets.json` to a generated view | one ledger instead of two | MED | MED |
| Fix `ODDS_API_KEY` whitespace | restores score fetch | **HIGH** | **TRIVIAL** |

**Explicitly not recommended:** reducing research capture cadence. The
night-before-timing and MLB-ALPHA-0002 programmes depend on high-frequency
observation, and no scientific case exists for thinning them. Consolidate the
*runners*, not the *observations*.

---

## SECTION 30 — REGISTERS AND ACTION PLAN

### C. CAPITAL RISK REGISTER

#### CRITICAL

**CR-1 · Every production price is a midpoint labelled "executable"**
*Files:* `scripts/build_kalshi_registry.py:194,202,208`;
`scripts/build_market_ledger.py:452,1401-1410,1629-1634`; `scripts/merge_odds.py`
*Families:* ML, F5 ML, team total, game total, run line, NRFI/YRFI — **all**
*Wrong bets today?* **YES.** 5.2% of Accepted TT rows fail the qualification floor
at the real ask; all `betUpToPrice` enforcement is against an untradeable price.
*Historical contamination?* YES — every archived `executablePriceUsed`,
`rawEdgeVsExecutable`, `netExecutableEdge` and every CLV entry leg.
*Data repair?* Not required — the true asks are preserved in `all_lines` and in
the 325 registry snapshots, so historical rows are **recomputable**.
*Minimal emergency remediation:* read `yes_ask` from the `all_lines` entry whose
`ticker == best_ticker` (one lookup, data already present), and add `priceBasis`
per issue #53. **Do not implement without CEO authorisation.**

**CR-2 · Settlement/CLV pipeline dead 5 days; all health signals green**
*Files:* `scripts/clv_from_snapshot.py:43`; `scripts/run_kalshi_clv_step.py:26-30`;
`.github/workflows/clv-update.yml`; `.github/workflows/edgelab-postgame.yml:59`
*Wrong bets today?* Indirectly — calibration, CLV and the forward holdout all run
on data frozen at 2026-08-31 while bets are placed daily.
*Historical contamination?* NO — nothing wrong was written; work was discarded.
*Data repair?* NO — settlement is idempotent; a backfill re-run recovers it.
*Minimal emergency remediation:* insert the repo root on `sys.path` in
`clv_from_snapshot.py` (or make the two callers insert it), then
`workflow_dispatch` `clv-update` and `edgelab-postgame` for 2026-09-01…09-06.

**CR-3 · Doubleheader legs collapse to one identity; contamination reached the ledger**
*Files:* `merge_odds.py` / `api/slate.js` `kalshiKey` construction
*Evidence:* 2026-06-17 SFATL and 2026-07-11 MILPIT — both legs, identical tickers;
`bets.json` rows `2026-06-17-111` and `-112` carry the 19:15 leg's tickers for the
18:00 leg's recommendation; both still `result: null`.
*Wrong bets today?* **YES** on any doubleheader date (~1 in 6 in this sample).
Post-#172 behaviour is refusal (safer, but zero coverage for both games).
*Historical contamination?* YES — 3 dates, ≥6 rows, ≥2 real-money-tier.
*Data repair?* YES — those ledger rows need their tickers re-derived from
first-pitch time and then re-settled.

**CR-4 · The model is measurably worse than the price it bets against**
*Evidence:* `MLB-RSCH-0022` (n=3,137/293 games/13 families): paired Brier
**+0.0549**, CI [+0.0391,+0.0718], p≈0, market better in **every** family;
independently corroborated here by the per-family mean-bias table (§M-2, §M-3).
*Wrong bets today?* **YES, systematically** — this is the base rate of the whole
operation.
*Historical contamination?* No data is wrong; the *interpretation* of every
positive-edge row is.
*Data repair?* NO. This is a **research and governance** decision, not an
engineering fix.

**CR-5 · One-sided book halves the registry midpoint**
*File:* `scripts/build_kalshi_registry.py:202`
*Evidence:* 29.43% of 132,619 archived rows are one-sided (all ask-only);
production-family rate 1.09–3.86% during production capture hours;
`KXMLBTEAMTOTAL-26SEP021940DETMIN-MIN7` bid=0/ask=0.81 → mid=0.405.
*Wrong bets today?* **Not yet** — 0 of 2,618 selected rungs across 40 archived
slates hit it. Latent, live, and aggravated by `best_line` preferring the halved
value.
*Historical contamination?* Research/hitter datasets only (34–69% one-sided in
prop families).
*Data repair?* Recomputable from the same snapshots.

**CR-6 · Two production probability engines disagree by up to 8.86 pp**
*Files:* `api/slate.js:741,840` vs `scripts/build_market_ledger.py:952`
*Evidence:* n=251; mean 1.76 pp; 66% > 1.0 pp; 15% > 3.0 pp.
`allEdges` ships `confidence: "HIGH"`, `betSize: 6`, `actionable: true` alongside a
`Rejected` authoritative row for the same market.
*Wrong bets today?* Only via human/LLM misreading — governance names
`marketLedger` authoritative.
*Historical contamination?* Any analysis that read `allEdges`.

#### HIGH

| ID | Finding | Files | Wrong bets today? | Historical? | Repair? |
|---|---|---|---|---|---|
| H-1 | NRFI/YRFI Poisson gives +11.87pp on 100% of games | `build_market_ledger.py:2102-2105` | No — suspension verified 148/148 PAPER | Yes, all archived RFI rows | No |
| H-2 | No home-field term ⇒ +3.81pp away ML bias, 63–71% away book | `compute_projections()` | **Yes** | Yes | No |
| H-3 | Root-ledger CLV correctly signed, **labelled inverted** | `generate_performance_report.py:234-240`; `EDGELAB_CLV_SIGN_AUDIT.md:227-235` | No | Reports mislabelled | No — label fix only |
| H-4 | 156 bets unsettled back to 2026-06-06; ledgers 556 vs 385 | `bets.json`, `bets.jsonl` | No | Yes | Yes — backfill |
| H-5 | Zero liquidity/depth awareness in production | `build_market_ledger.py`, `risk_gate.py` | **Yes** — can recommend size the book cannot fill | — | No |
| H-6 | `ODDS_API_KEY` trailing whitespace breaks score fetch | repo secret | No | Yes — contributes to backlog | No |
| H-7 | Tests import production entry points differently from production | `tests/test_clv_snapshot_pipeline.py:33-38` vs `run_kalshi_clv_step.py:26-30` | No | — | No |
| H-8 | Staking multipliers = in-sample win rates at n=6…76 | `config/rules.json` | **Yes** | Yes | No |
| H-9 | 79% of real-money candidates are same-direction TT Overs; no cross-game correlation | `risk_gate.py` | **Yes** — understated joint variance | Yes | No |

#### MEDIUM

M-a `poisson_pmf(0,0)==0.0` (latent; research fixed it locally, production untouched) ·
M-b unit inference `f<=1.0` treats 1¢ as $1.00 ·
M-c 52/165 ledger rows carry `marketTicker: null` ·
M-d snapshot CLV closes on a mid ·
M-e issue #54 idempotency ·
M-f win-prob clamp yields pairs summing to 0.92 ·
M-g `wrcPlus = rpgIndex` mislabel, two different `wrcPlus` definitions ·
M-h `workflow_run` success-gating silently skips 6 downstream workflows ·
M-i 5 sentinel implementations with 3 drifted constant sets ·
M-j 3 hand-synced series lists ·
M-k `capture-closing-lines` runs 24 h for a game-hours-only job

#### LOW

L-a stale `fetch-slate.yml` concurrency comment ·
L-b `README.md` record/workflow counts badly out of date ·
L-c `recentFIP` no-op guard ·
L-d 4 tests pinned to historical SHAs ·
L-e 36 stale origin branches ·
L-f 24 unreferenced research scripts ·
L-g CI deselect list over-broad by one test ·
L-h `data/edgelab/market_observations/` empty ·
L-i 35/36 workflows hold `contents: write`

### D. EFFICIENCY REGISTER

See Section 29. Highest value/effort ratio, in order:
**(1)** fix the `ODDS_API_KEY` whitespace (trivial / restores settlement);
**(2)** sparse checkouts across all collectors;
**(3)** compress `kalshi_registry_snapshots` (368 MB);
**(4)** restrict `capture-closing-lines` to game hours (−118 runs/day);
**(5)** consolidate the five Kalshi collectors (308 → ~90 runs/day);
**(6)** delete the `api/slate.js` model (removes CR-6 *and* 1,400 lines).

### E. RESEARCH GAP REGISTER

**Engineering fixes (no new evidence needed):**
CR-1 price basis · CR-2 import path · CR-3 doubleheader identity · CR-5 mid
formula · CR-6 engine deletion · H-3 CLV label · H-6 secret whitespace ·
M-a/M-b/M-c/M-d/M-e · O-1 health assertions · all of §29.

**Genuine research questions (do NOT ship code for these):**
1. Does *any* production family have positive CLV-validated edge at the executable
   ask, net of fees, on the preregistered forward holdout? *(blocked on CR-2)*
2. What is the correct single-inning run distribution? Negative binomial is
   indicated (RSCH-0034 found frozen NB dispersion materially better than
   Poisson) but must clear DEV→VAL→FORWARD. *(H-1)*
3. What is the correct home-field adjustment for **this** projection form?
   RSCH-0017 fitted −0.0065 for a *different* model. *(H-2)*
4. Is a market-relative posterior — `P(model right | disagreement, band, family,
   data quality)` — learnable and stable out-of-sample? *(§9)*
5. Which offensive inputs (xwOBA, barrel, hard-hit, lineup order) improve
   calibrated probabilities? *(§6)*
6. What is the true executable depth distribution, and at what size does edge
   vanish? *(H-5 — requires production L2 capture first)*
7. What sizing function is justified once (1) is answered? *(H-8)*
8. Does correlation-adjusted portfolio variance change the daily cap? *(H-9)*

### F. PROFESSIONAL-BETTOR GAP ANALYSIS

See Section 25.

### G. TECHNICAL-DEBT MAP

See Section 21.

### H. ARCHITECTURE REDESIGN

See Section 26.

### I. ACTION PLAN

| Action | Class | Capital impact | Integrity risk | Profit impact | Effort | Depends on |
|---|---|---|---|---|---|---|
| Fix `ODDS_API_KEY` whitespace | **FIX NOW** | HIGH | HIGH | MED | trivial | — |
| Fix `clv_from_snapshot` import; backfill 09-01…09-06 | **FIX NOW** | HIGH | **CRITICAL** | HIGH | XS | — |
| Add the 8 health assertions as a failing gate | **FIX NOW** | HIGH | **CRITICAL** | MED | S | — |
| Read the real `yes_ask` from `all_lines`; add `priceBasis` | **FIX NOW** | **CRITICAL** | HIGH | HIGH | S | — |
| Doubleheader identity in the slate path | **FIX NOW** | HIGH | HIGH | MED | S | — |
| Fix one-sided-book `mid` (require both sides or mark `PARTIAL_BOOK`) | **FIX NOW** | HIGH | HIGH | MED | XS | — |
| **Suspend all real-money families to PAPER pending forward-holdout evidence** | **FIX NOW (CEO decision)** | **CRITICAL** | — | **HIGHEST** | XS | CR-4 |
| Backfill the 156-bet settlement backlog | IMPROVE NEXT | MED | HIGH | MED | S | CR-2 |
| Delete the `api/slate.js` model | SIMPLIFY | MED | HIGH | LOW | M | — |
| Correct the root-ledger CLV convention label | IMPROVE NEXT | LOW | MED | LOW | XS | — |
| Fix issue #54 (`confirmedReceipt*` inheritance) | IMPROVE NEXT | LOW | MED | LOW | XS | — |
| Capture top-of-book size in production | IMPROVE NEXT | HIGH | MED | HIGH | M | — |
| One ledger (root becomes a view) | SIMPLIFY | MED | MED | LOW | M | backfill |
| Canonical `Price` / `MarketIdentity` objects | IMPROVE NEXT | HIGH | HIGH | MED | L | price+identity fixes |
| Sparse checkouts; consolidate collectors; compress archive | AUTOMATE / SIMPLIFY | — | LOW | — | M | — |
| Forward-holdout to CHECKPOINT_3 | **RESEARCH** | **CRITICAL** | — | **HIGHEST** | — | CR-2 |
| Negative-binomial inning distribution | RESEARCH | HIGH | — | HIGH | — | holdout |
| Home-field adjustment for the production form | RESEARCH | MED | — | MED | — | holdout |
| Market-relative posterior | RESEARCH | HIGH | — | **HIGHEST** | L | settlement data |
| Per-family CLV / mean-bias monitors | MONITOR | MED | HIGH | MED | S | health gate |
| Settlement grader, fee engine, CLV convention, coverage accounting, market taxonomy, research governance | **LEAVE ALONE** | — | — | — | — | — |

### J. TOP 10 PRIORITIES

1. **Fix `clv_from_snapshot.py`'s import and re-run settlement for 2026-09-01…09-06.** Five days of blindness ends in one line.
2. **Fix the `ODDS_API_KEY` trailing whitespace.** A second, independent cause of the settlement backlog. Costs nothing.
3. **Ship the eight health assertions as a workflow that fails.** All eight fail today. Nothing else on this list stays fixed without it.
4. **Price against the real `yes_ask` and stamp `priceBasis` on every price.** Closes issue #53, CR-1, CR-5, C-2 and DS-2 with one canonical object. The data is already captured.
5. **Decide, at CEO level, whether real money should be deployed at all before the forward holdout reports.** RSCH-0022 says the market beats the model in every family. The system's own evidence points to PAPER until CHECKPOINT_3.
6. **Fix doubleheader identity in the slate path and repair the 2026-06-17 / 07-11 / 07-07 ledger rows.**
7. **Reject the one-sided-book midpoint** (`PARTIAL_BOOK`, no price) instead of silently halving it.
8. **Delete the model from `api/slate.js`.** One engine, one number, 1,400 fewer lines.
9. **Backfill the 156-bet settlement backlog and reconcile the 556-vs-385 ledger gap.**
10. **Capture top-of-book size in the production registry.** Cheapest possible first step toward executable — rather than theoretical — edge.

### K. THINGS WE SHOULD NOT TOUCH

| Component | Why |
|---|---|
| `lib/edgelab/settlement.py` | Horizon-safe, refuses rather than invents, every edge case correct. Change risk far exceeds benefit. |
| `lib/edgelab/kalshi_fees.py` | Single versioned engine, applied exactly once, with a correction pass that got the unused-budget question right. The model for the rest of the codebase. |
| `lib/edgelab/clv_convention.py` | One definition, explicit units, refuses midpoint, protected by an AST guard. Exemplary. |
| `lib/research/market_taxonomy.py` + the ticker parsers | Independently certified by ladder monotonicity (≤0.9% across 5 families) and by settlement agreement. |
| Kalshi coverage accounting (`build_full_market_coverage.py`, discovery pipeline) | 457/457, zero unaccounted, `NOT_EVALUATED_BUG` bucket, unknown-series detector. Best component in the repository. |
| Research governance (`experiment_registry`, `evidence_levels`, `pit_provenance`, `frozen_forward_scorer`, `research_splits`, `promotion_engine`) | Preregistration, untouched holdouts, FDR control, self-retiring candidates, self-correcting artifacts. Genuinely institutional. |
| The immutable slate/protection layer (`protect_slate.py`, `slate_manager.py`, `atomic_json.py`, sentinel validation) | Extensive regression coverage; has caught real contamination (`rejected_contaminated_*` files in `data/slates/`). |
| The lineup gates (Rules 50/51/52) | Correct professional practice, correctly implemented. |
| The absence of any order-placement code | The single most valuable safety property in the system. Do not add one. |
| `build_risk_portfolio`'s TT-denominator asymmetry | Documented as deliberate; it makes the dominance test stricter. |
| The 34 numbered research artifacts | Even the superseded ones. They are the audit trail. |

---

## SPECIAL REVIEW — HISTORICAL DATA CONTAMINATION MATRIX

| Research project | Affected? | Why | Severity | Rerun? | Data repair? |
|---|---|---|---|---|---|
| **MLB-RSCH-0022** Production calibration | **Partly** | Model-vs-market Brier used archived `marketImpliedProbability` (mid). Mid is the *correct* basis for a probability-forecast comparison, so the **headline stands**. The §6 "descriptive economics" fee-aware net-EV table is priced at mid and is optimistic. | LOW | No — annotate §6 | No |
| MLB-RSCH-0023 / 0025 recalibration | No | Both retired for failing validation. Conclusion unaffected. | NONE | No | No |
| **MLB-RSCH-0031 / 0032** | Already superseded | Mixed TT v1.1/v1.2 pricing; RSCH-0032's team-run-mean recovery invalid | — | Already done by 0033/0034 | No |
| MLB-RSCH-0033 Team-run mean | No | Uses archived `projections.json` directly, not prices. Control valid (636/678 within 0.001). | NONE | No | No |
| **MLB-RSCH-0034** Team-total conversion | Slight | Kalshi comparison uses mid | LOW | No | No |
| **Night-before timing research** | **Yes** | Timing edges measured on mid-based prices; a mid-to-ask move is indistinguishable from an informational move at these magnitudes (1–3¢) | **MED** | **Yes** — recompute at executable ask (data available) | No |
| **MLB-ALPHA-0002** (order book, microstructure) | **No** | Captures L2 directly and uses `clv_convention.executable_price_cents`. **The only programme immune to CR-1.** | NONE | No | No |
| **MLB-ALPHA-0001 / C01-PIT shadow** | Partly | Some legs use fair-mid, but it is **explicitly named non-executable** (`mlb_alpha_shadow.fair_mid_clv_cents`) | LOW | No | No |
| **Hitter research** (0028, projection audit, feature ablation) | **Yes** | Hitter families are **34–69% one-sided books**, so DS-1's halved mid is pervasive in exactly this corpus | **HIGH** | **Yes** — after filtering/repairing one-sided rows | No (recomputable) |
| **Market-family ROI / edge-persistence (0006, 0026, 0029)** | **Yes** | ROI computed against mid overstates realizable return by the half-spread | **MED** | **Yes** | No |
| **Calibration reports** (10-day review, frozen-forward scorecard) | **Yes** | Frozen at 2026-08-31 by CR-2; forward holdout cannot advance | **HIGH** | **Yes** — after backfill | No |
| **CLV analyses on the root ledger** | Label only | Sign is canonical; the stamp says otherwise | MED | No — fix the label | No |
| **Settlement corpus** | **Yes — incomplete, not wrong** | 6-day gap; 156 unsettled bets | **HIGH** | **Backfill**, not rerun | **Yes** — backfill |
| **Doubleheader-date market observations** | **Yes** | 3 dates with cross-leg ticker assignment | MED | Yes for those dates | **Yes** — 6+ ledger rows |

**No prior research is silently invalidated by this audit.** The two research
programmes with the strongest methodology — MLB-ALPHA-0002 and the frozen-forward
scorer — are the two least affected, which is itself a good sign about where the
discipline was applied.

---

## AUDIT SCOPE AND LIMITS

**Reproduced directly:** the CLV import failure; issue #54; the mid-vs-ask
identity on live and archived slates; the JS/Python engine divergence; the
per-family model-market bias; the one-sided-book rate; ladder monotonicity;
doubleheader ticker collision; the 4 shallow-clone test failures and their
resolution under full history; the full test suite, twice.

**Read from GitHub, not inferred:** the `clv-update` / `edgelab-postgame` run
history and job logs; the `ODDS_API_KEY` whitespace error; issue text for #53,
#54, #170.

**UNKNOWN — not determined by this audit:**
- Whether Kalshi lists both legs of a doubleheader (no `G1`/`G2` ticker appears in
  325 snapshots; the parser supports it but has never seen one).
- Whether the three remaining issue-#170 tests are genuinely order-dependent
  (2 clean runs is not proof of absence).
- Whether the 2026-06-17 bets were physically placed on the correct contract —
  only the *recommendation* and its recorded ticker were verified wrong.
- The exact production Actions minute/API-credit spend (run counts are derived
  from cron expressions, not from billing).
- Whether `data/edgelab/market_observations/` being empty is intended.

---

*Audit performed against `40aad30d3007ad9854a88736e3107dbdfebaafd6`.
No production behavior was changed. Remediation requires separate CEO
authorization.*
