# EdgeLab — Kalshi MLB Alpha Discovery Program (MLB-ALPHA-0001)

**Status: RESEARCH ONLY / DISCOVERY–RETROSPECTIVE evidence.**
This program changes no production probability, recommendation, family
eligibility, staking, fee, Bet Up To, or wager-ledger behavior. It does
not reopen MLB-RSCH-0035, F5, RFI, hitter, bullpen, or calibration model
research. The prior MLB model-research sprint remains in prospective
monitoring mode, untouched.

## Program goal

Not "make the baseball model better." The question is:

> **Where does Kalshi misprice MLB markets, and can we trade those
> inefficiencies after fees?**

Find repeatable, executable, fee-aware trading strategies in the complete
Kalshi MLB market archive that survive unseen-data validation. Search
aggressively in discovery, test ruthlessly, promote reluctantly. Required
lifecycle: DISCOVERY → FREEZE EXACT RULE → HISTORICAL VALIDATION → BLIND
HOLDOUT → PROSPECTIVE SHADOW → explicit human production approval.
Methodology V3 governance remains the foundation where applicable.

## Data universe

The research universe is the **complete market archive** — every Kalshi
MLB contract the capture infrastructure observed, whether or not it was
ever production-qualified, recommended, rejected, or wagered. Populations
kept explicitly separate via provenance (`sourceSystem`,
`wasRecommended`, `wasPlaced` on settlements): complete archive;
production-qualified rows; RESEARCH_ONLY rows; recommendation rows;
rejected rows; confirmed wagers. A market never needs to have been
recommended to be studied; every row retains provenance.

Canonical stores (all under `data/edgelab/`):

| Store | Content |
|---|---|
| `observations/` | 440,200 timestamped quotes, 2026-08-01 → 2026-09-01, every row with `yesBid`/`yesAsk`/`lastPrice`/`spreadCents`/`volume`/`openInterest` |
| `settlements/` | 122,689 rows, 2026-08-02 → 2026-08-31; 119,021 SETTLED YES/NO |
| `markets/`, `games/` | contract + game identity |
| `model_evaluations/` | production model probabilities 2026-07-30 → 2026-08-31 (Family E, later phase) |
| `data/kalshi_registry_snapshots/` | raw captures back to 2026-06-08 (1/day pre-August; no settlement store coverage → **not** in the reliable universe) |

Fourteen market families: `game_result`, `winning_margin`, `game_total`,
`team_total`, `inning_result` (incl. three-way F5 with explicit TIE),
`inning_total`, `first_inning_run`, `pitcher_strikeouts`, `pitcher_outs`,
`hitter_hits`, `hitter_total_bases`, `hitter_hits_runs_rbis`,
`hitter_rbis`, `hitter_stolen_bases`.

## Reliable date range and data limitations

**Reliable settled range: 2026-08-02 → 2026-08-31, excluding 2026-08-17
(29 dates).** Determined from artifacts, not assumption
(`data/edgelab/research_artifacts/mlb_alpha_0001/coverage_manifest.json`):

- 2026-08-01: observed (12,366 obs) but 0% of tickers settled — excluded.
- 2026-08-17: **full settlement hole** (zero settlement rows for its 18
  games) — excluded.
- 2026-09-01: in-flight capture, unsettled — excluded.
- 2026-08-02/03: usable, but `checkpoint` labels are null (labels begin
  2026-08-04) — derived checkpoints only.
- Partial-capture dates (kept, flagged): 08-03 (8 games), 08-06 (13),
  08-13 (9), 08-27 (7 games, FIRST_DAILY only).
- `noBid`/`noAsk` were **never** populated; NO-side executable prices are
  derived from the YES book (binary complement).
- No quote-age field exists; staleness is only inferable from capture
  cadence. `marketStatus` is archived per quote.
- Pinnacle/external sharp history: **not archived in-repo** (live slate
  fields only; see `docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md`).
  Family D is deferred until an external archive exists.
- Known schema/version boundaries: checkpoint labeling begins 2026-08-04;
  intraday multi-capture cadence begins ~2026-08-11; a second capture
  stream (`standalone_price_check`) joins 2026-08-09+; `scheduledStart`
  field coverage varies by date (0% on 08-03, 08-06, 08-11→15, 08-27) —
  mitigated by decoding scheduled start from the eventTicker
  (YYMMMDDHHMM US/Eastern, validated at 0.0 min median error vs the
  `scheduledStart` field on 26,261 rows).

## Frozen splits

Frozen **before any outcome inspection**, boundaries chosen from coverage
facts and calendar order only
(`data/edgelab/research_artifacts/mlb_alpha_0001/frozen_splits.json`,
sha256 hashes recorded there):

| Split | Dates | Count | Share |
|---|---|---|---|
| DISCOVERY | 2026-08-02 → 2026-08-19 (ex 08-17) | 17 | 58.6% |
| VALIDATION | 2026-08-20 → 2026-08-25 | 6 | 20.7% |
| BLIND HOLDOUT | 2026-08-26 → 2026-08-31 | 6 | 20.7% — **SEALED** |

Discovery holds 232 independent games; validation and holdout each hold
>130. All three splits span many independent dates and games — this is
not a 2–3-date pseudo-holdout. Limitation acknowledged: all splits sit
inside a single month of a single season; no cross-regime coverage is
possible with this archive.

**The blind holdout remains untouched during discovery and validation.**
The entry-row builder (`build_entry_rows.py`) structurally refuses to
build holdout rows. Opening the holdout requires explicit CEO
authorization recorded in this document.

## Unit of independence

- **CONTRACT** = one Kalshi `marketTicker` (one settlement event).
- **OBSERVATION** = one timestamped quote of a contract. Repeated
  observations of one ticker are **not** independent settlements.
- **GAME** = one underlying MLB game (decoded from eventTicker
  date+time+teams). Adjacent ladder contracts, home/away contracts of
  the same game, complements, and multiple checkpoints of the same game
  are all **correlated**.
- **DATE** = independent calendar trading date.
- **STRATEGY OPPORTUNITY** = one frozen rule firing on one contract at
  one predeclared entry checkpoint.

All inference clusters at the **game** level (bootstrap over games);
date- and ticker-level counts are reported alongside. A cell's sample
size claim is its independent game/date count, never its row count.

## Entry checkpoints (predeclared)

Two deterministic, pregame-guarded entry checkpoints; both require
`marketStatus` active and capture strictly before scheduled start:

1. `FIRST_DAILY_DERIVED` — earliest qualifying observation of the
   contract on its game date.
2. `LAST_PREGAME` — latest qualifying observation before scheduled start
   (closing-line proxy; a conservative proxy since capture cadence means
   the true close may be minutes later).

## Fee model and execution price rules

- Executable buy-YES price = archived `yesAsk`. Executable buy-NO price
  = `100 − yesBid` (binary complement of the YES book; the archive holds
  no direct NO quotes). **Midpoint is never an executable fill** and is
  used only where explicitly labeled theoretical.
- Fees: the repo's canonical `lib.edgelab.kalshi_fees` —
  `ceil_to_cent(0.07 × contracts × price × (1−price))` taker fee,
  `FEE_STATUS_ESTIMATED_FEE_SCHEDULE`, schedule version
  `KALSHI_TAKER_STANDARD_2026_WEBSEARCH_CORROBORATED_V1`.
- Standardized $10 research order via `simulate_settlement_order` (whole
  contracts, actual-cash-consumed denominator — Tier C realistic
  execution). Every cell reports **GROSS EDGE**, **FEE DRAG**, and **NET
  EXECUTABLE EDGE** separately. No strategy qualifies on midpoint or
  gross economics.
- Entry rows with `yesAsk` outside [1, 99] (or complement outside
  [1, 99]) are unexecutable and excluded.

## Discovery program

Exactly five finite top-level families; **this program phase executes A
and B only**. C (price movement/timing), D (external sharp disagreement)
and E (our model as residual signal) are later phases and were not
scored.

Family A predeclared dimensions: market family; YES vs NO; coarse
10-cent price bands (0–10, …, 90–100; adjacent bands may be merged for
support, every tested spec recorded); home vs away where meaningful;
favorite vs underdog; contract threshold/tail; market direction
(upside/excitement vs defensive/complement, objectively defined:
OVER-side of totals/props = upside); entry checkpoint. No integer
cut-point searches for maximum ROI.

Family B structural relationships: complement consistency
(YES-book vs NO-book crossings), adjacent game-total / team-total /
hitter / pitcher ladders (settlement semantics: totals settle
`value > threshold`; player props settle `stat ≥ N`), three-way F5
HOME+AWAY+TIE sums, full-game vs winning-margin dominance, and any
mutually-exclusive/collectively-exhaustive sets. **PURE ARBITRAGE**
(simultaneously purchasable, guaranteed combined payoff at archived
executable prices, fees included, leg timing compatible, no tie/void
escape) is reported separately from **RELATIVE VALUE** (deterministic
pre-outcome signal, e.g. ladder monotonicity violation magnitude, then
scored on settlements).

## Candidate freezing rule

At most **10 candidates total** across A+B (prefer fewer). Each requires:
exact deterministic rule; economic rationale; meaningful post-fee effect;
sufficient independent games/dates (minimums below); no one-team/one-date
dependence; realistic execution; zero use of validation/holdout outcomes.
Each candidate is frozen with a stable ID (`MLB-ALPHA-0001-C##`) and a
sha256 hash of its exact rule JSON before validation scoring.

## Minimum sample standards

A discovery cell is candidate-eligible only with ≥ 60 independent games,
≥ 10 distinct dates, and ≥ 80 contracts in discovery, plus a
game-clustered bootstrap 90% CI for net ROI excluding zero (descriptive
cells are still reported). Validation requires ≥ 40 independent games
for a verdict.

## Multiple-testing policy

Every evaluated hypothesis (cell × side × checkpoint) is counted and
recorded — including losers. Benjamini–Hochberg FDR at q = 0.10 across
the full recorded hypothesis set, using game-clustered bootstrap
p-values. Raw p-values alone qualify nothing; a tiny p on thousands of
correlated contract rows is explicitly insufficient. Effect sizes and
independent game/date counts are reported next to every survivor.

## Anti-overfit rules (non-negotiable)

- No holdout peeking; no candidate is modified after seeing its holdout
  (or validation) result and re-tested on the same data.
- No post-hoc price bands around winning cut-points.
- Correlated rows (same game/ladder/checkpoint) are never treated as
  independent observations.
- Historical ROI is not an optimization target during discovery.
- Validation is scored **once** per frozen candidate. Losers are
  rejected, not tuned and retried.

## Stopping rule

Discovery for A+B is CLOSED once the predeclared dimensions are
evaluated and at most 10 candidates are frozen. No further slicing "until
something wins." **NO VALIDATED A/B ALPHA is an acceptable result.**

## Prospective promotion rule

Validation survivors do **not** open the holdout automatically: they are
returned for explicit CEO authorization first. A holdout survivor then
enters PROSPECTIVE SHADOW (forward, no real money) under a new
registered experiment; only after shadow success and explicit human
production approval may anything touch real money.

## Production firewall

This program must not: change probabilities, recommendations, family
eligibility; reactivate RFI; change F5 or TEAM_TOTAL_NB_V1; change
staking, fees, or Bet Up To; place bets; or alter the wager ledger.
All artifacts live under `scripts/research/mlb_alpha_0001/`,
`data/edgelab/research_artifacts/mlb_alpha_0001/`, and this document.
`git diff` against production paths is empty by construction.

## Registry

Experiment `MLB-ALPHA-0001` — "Kalshi MLB Alpha Dataset Audit +
Market/Structural Discovery", evidence level DISCOVERY/RETROSPECTIVE
(`data/edgelab/experiments/MLB-ALPHA-0001.json`). Namespace `MLB-ALPHA`
is distinct from `MLB-RSCH`; discovery results are never labeled
prospective evidence.
