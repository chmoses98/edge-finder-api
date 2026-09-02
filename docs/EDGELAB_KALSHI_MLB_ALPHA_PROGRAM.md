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

---

## Program status after A+B discovery (2026-09-01)

**A+B DISCOVERY IS CLOSED.** 584 Family-A cells evaluated (513 tested,
BH-FDR q=0.10), full Family-B structural audit complete. One candidate
frozen; it passed validation; **the blind holdout remains sealed.**

### Two settlement-integrity defects found (and worked around, research-layer only)

1. **Total-ladder semantics (game_total, inning_total).** Kalshi settles
   KXMLBTOTAL / KXMLBF5TOTAL rung "-N" as "N or more" (YES iff value
   ≥ N); `lib.edgelab.settlement` settles YES iff value > N. Proof:
   13/13 archive anomalies where a rung's last in-game quote was ≥97¢
   yet the archive settled NO land exactly on the independently-pinned
   final total. 564 settlements flip under the corrected rule; 95
   sparse-ladder rungs are unresolvable. Research scoring uses the
   corrected results (`corrected_total_settlements.json`). **Production
   implication (NOT changed here, needs separate authorization):**
   `build_kalshi_registry.py`/the model price these contracts as
   strictly-over, so production systematically undervalues YES on every
   integer-rung total contract by P(value = N).
2. **F5 spread horizon (KXMLBF5SPREAD).** Every archived F5-spread
   settlement equals the full-game margin result (1512/1512), and 88 are
   logically impossible against the correct KXMLBF5 winner settlements —
   the engine settled F5 spreads on full-game scores. No F5 linescore
   exists in-repo to correct them, so the family is excluded from the
   research universe. KXMLBF5 (winner), KXMLBF5TOTAL, and full-game
   KXMLBSPREAD settlements pass integrity checks (0 dominance
   violations for full-game spread vs moneyline).

Both defects manufactured large fake "edges" (+83–128% ROI cheap-NO
totals cells; +58% F5-spread longshot cell) that were caught by
adversarial verification before candidate freezing and are recorded as
rejected in `frozen_candidates.json`.

### Family A findings (clean data)

Kalshi MLB pricing is broadly efficient after fees: 65 of 513 tested
cells are net-positive, and only **one** survives FDR — most cells,
including nearly all high-price BUY_NO/defensive cells, are reliably
fee-negative. No YES-side, home/away, or favorite/underdog bias
survived. Deep-tail: laying 0–10¢ longshots (BUY_NO at 90–100¢) loses
≈1.6–2.5% net — longshots are, if anything, slightly underpriced here,
the reverse of the classic bias.

### Family B findings

**No pure arbitrage exists post-fee anywhere in the archive**: 169,603
books (0 crossed), 719 three-way F5 batches (0 sum violations), 103,409
adjacent ladder pairs (0 executable inversions), 39,030 dominance pairs
(1 executable pre-fee crossing, 0 post-fee). All predeclared
relative-value corrective trades lose after spread+fees (F5-total ladder
inversions −38.7%, p=0.0005; hits-vs-HRR dominance −11.5%, p=0.0005) —
apparent inconsistencies are bid/ask noise, not tradable dislocations.

### Frozen candidate and validation result

**MLB-ALPHA-0001-C01** (rule sha256 `baa8dddf…`): BUY YES on
KXMLBF5TOTAL contracts with executable `yesAsk` ∈ [90, 99]¢ at
LAST_PREGAME; $10 taker order, Tier C economics, corrected settlements.

| Split | Contracts | Games | Dates | W–L | Net ROI | CI90 | p |
|---|---:|---:|---:|---:|---:|---|---|
| Discovery | 276 | 203 | 17 | 272–4 | +3.31% | [+1.9%, +4.5%] | 0.0005 |
| Validation (scored once) | 80 | 67 | 6 | 80–0 | +5.01% | [+4.7%, +5.3%] | 0.0005 |

Verdict: **PASS** (direction preserved, ≥40 games, positive net ROI).
Honest caveats: the validation CI is optimistic (zero losses in the
resample pool); the effect is checkpoint-specific (FIRST_DAILY entries
in the same band lose −6.3%); the result inherits the ≥-semantics
correction; capacity is modest (~13 contracts/day at $10 each in
archive terms); all data is one month of one season.

### Next step (requires explicit CEO authorization)

Open the sealed blind holdout for C01 only, exactly as frozen — or hold
it sealed and move C01 to prospective shadow first. No further
discovery slicing is permitted under the stopping rule.

---

## Scientific cleanup pass (maintainer review of PR #174)

Four findings were raised against the first pass. All four are resolved
below. **The blind holdout remains SEALED and was not read.**

### 1. Inference was not a valid hypothesis test — REPAIRED

The first pass reported `2 * min(P(ROI* <= 0), P(ROI* >= 0))` from an
ordinary, *unshifted* cluster bootstrap and called it a p-value. It never
imposed the null, so it was a CI-inversion heuristic, not a test. It is
**withdrawn**.

Replaced (`scripts/research/mlb_alpha_0001/inference.py`) by a
**null-centered game-cluster bootstrap** (primary) — each cluster is
recentered `net0_g = net_g − ROI_hat · cash_g` so the resampling
population satisfies H0 exactly, preserving cluster sizes, exposure and
heteroskedasticity — plus a **restricted wild cluster bootstrap**
(Rademacher, Cameron–Gelbach–Miller) as an independent secondary. Both
cluster on the independent GAME. The percentile interval is retained but
only ever labelled a CI.

**The method was validated, not assumed.**
`inference_calibration_study.py` (1000 sims, fixed seed) measures both
tests against a true null with C01's payoff shape and finds them
**anti-conservative**: nominal 0.05 rejects 0.081, nominal 0.10 rejects
0.136. Rather than hide this, every cell now also carries a conservative
size-corrected p (`p × 1.62`) and **BH-FDR at q=0.10 runs on both**; only
cells clearing both count. This was fixed before re-scoring and can only
remove survivors.

| | old (invalid) | new (valid, dual FDR) |
|---|---:|---:|
| FDR survivors | 169 | **149** |
| positive survivors | 1 | **1** |

**C01 remains the only positive FDR survivor.** Corrected inference:
discovery net ROI +3.31%, p=0.0010 (wild 0.0005, conservative 0.0016);
validation +5.01%, p=0.0005 (conservative 0.0008). Both tests agree on
both splits. **The scientific conclusion does not change.**

### 2. Universe-wide CLV — BUILT

`universe_clv.jsonl.gz` + `universe_clv_report.json`: **139,186** CLV rows
over **60,244 contracts / 288 games / 21 dates**, executable prices only
(`yesAsk`; NO as `100 − yesBid`), never midpoint, reusing production's
`select_closing_quote`. Sign convention stated explicitly:
`clvCents = closing − entry`, **positive = good**.

Headline characterization: **mean CLV is negative at every checkpoint and
on both sides** (FIRST_DAILY −1.17¢; BUY_YES −1.16¢, BUY_NO −0.82¢) —
i.e. executable spreads *tighten into first pitch*, so early entries
systematically pay up. Deep price bands pay the most (90-100¢: −2.39¢).

Two honest reporting points: `LAST_PREGAME` is the *same quote*
`select_closing_quote` selects, so its CLV is identically zero and it is
**not** an independent CLV checkpoint (183,264 such rows, mean exactly
0.00). `LINEUP_CONFIRMATION`, `T_MINUS_15` and `T_MINUS_5` produce no
independent CLV rows — those labels, when present, *are* the closing
quote.

**Production sign note (read-only):** both `lib/edgelab/clv.py` and
`scripts/clv_from_snapshot.py` compute `entry − closing`, the negation of
the convention above, while `clv.py`'s docstring describes a positive
value as "entered at a better (cheaper) price than the close". For a
buyer, cheaper-than-close means `closing > entry`, which that formula
scores negative. Reported, not changed.

### 3. C01 is not PIT-executable as frozen — RECLASSIFIED, and translated

`c01_execution_audit.json`. C01 is reclassified
**DISCOVERY/VALIDATION SIGNAL — NOT YET PIT-EXECUTABLE**; its frozen
historical record is unchanged.

- minutes-to-start at entry: p5 5.8, p25 20.1, **median 43.9**, p75 126.7,
  p95 434.0 — entries are *not* concentrated near first pitch.
- **100%** of C01 entries are the official closing quote (399/399), which
  is the definition of the ex-post problem.
- spread 1¢ on 242/399, mean 1.52¢; median volume 113, median OI 109.
- the exact quoted ask had already been standing at a prior capture in
  259/399 cases (median persistence 83 min).
- **Fill depth: `TOP_OF_BOOK_PRICE_OBSERVED` only.** The observation
  schema contains **zero** size/depth/quantity fields, so a $10 fill is
  **UNKNOWN/UNVERIFIED** and is never claimed as proven.

**C01-PIT** (`frozen_candidate_c01_pit.json`, rule sha256
`882f16d8330af1af…`) — chosen on capture mechanics only, fixed before any
scoring: `T_MINUS_5` covers just **9.5%** of eligible contracts
(T-15 12.8%, T-30 12.5%) and is inadequate, so a predeclared
**operational viability floor of 50%** plus a "most proximate qualifier"
tie-break selects the window **[T-60, T-0)**, entering on the **first**
qualifying quote (requiring no future information). Discovery-only sanity
check, rule frozen first: 152 opportunities, 120 games, 149–3,
**+2.79% net, p=0.022 (conservative 0.036)**; a strict *subset* of C01's
opportunities (152 of 309, zero PIT-only). **Validation was not
re-scored** for this second rule, and the holdout was not read.

### 4. F5 outcomes needed independent verification — VERIFIED, PASSES

`f5_settlement_verification.json`. First-five-innings scoring was
re-fetched from `statsapi.mlb.com` on GitHub Actions (egress-blocked
locally) and summed directly from the raw innings array, with exact
identity resolution only (ambiguous games refused).

| | |
|---|---:|
| C01 games verified | **270 / 270** |
| Unresolved | **0** |
| Contracts checked | 356 |
| Agreeing with the research correction | **356 / 356 (100%)** |
| Disagreeing with the *archived* result | 10 |

All 10 archived disagreements sit **exactly at `totalF5 == threshold`**,
the boundary the `>= N` correction fixes, and external truth says YES in
every case. **C01's outcome basis is trustworthy**; KXMLBF5TOTAL uses the
correct F5 horizon, unlike KXMLBF5SPREAD.

### Production defects — separate, unmerged PRs

- **#175** total-ladder `>= N` semantics: fixes settlement
  (`lib/edgelab/settlement.py`) **and both probability engines**
  (`scripts/build_market_ledger.py`, `api/slate.js`) consistently.
- **#176** F5 spread horizon: period-scoped markets grade on
  `periodScores[horizon]`; a missing period score is `UNRESOLVED`, never
  the full-game score.

Neither is merged. No historical ledger row is rewritten by either.

Also surfaced: **Kalshi's own settlement result is never archived** — all
686,220 raw market records are `status="active"` because the capture path
only fetches open markets. That gap is why a settlement-semantics defect
could persist undetected; capturing settled markets is recommended as a
separate change.

### Status

C01: **trustworthy historical signal**, statistically valid under
corrected inference, outcome-verified externally, with a frozen
PIT-executable translation ready. **The blind holdout remains SEALED and
is not authorized.**

---

## Second cleanup pass (maintainer review #2)

### Production fixes MERGED

- **#175** (merge `12ffdbac`) — total-ladder `>= N`. Corrected in review:
  the `api/slate.js` hunk was **wrong and reverted** (that function prices
  Pinnacle sportsbook totals, which legitimately have a push outcome, and
  reads no Kalshi data). The genuine third strict-over Kalshi path was
  found instead: `lib/kalshi_probability_adapters.adapt_total`, which
  prices every rung of the `game_total`/`inning_total` ladders. A 200-cell
  grid now pins `YES = P(X >= N)`, `NO = P(X < N)`, `YES + NO = 1`.
- **#176** (merge `b8edbf92`) — F5-spread horizon. Synced onto post-#175
  main with explicit coexistence tests, since both PRs edit
  `settle_market`.

Final main: `b8edbf92676c5ed4aedfcc65892d5110fdd6219c`.

### CLV sign — audited, PR open and unmerged

`docs/EDGELAB_CLV_SIGN_AUDIT.md` + **PR #177**. The ledger carries **one**
convention, uniformly: 184 decisive rows are `entry − closing` (the
negation of positive-is-good), 0 are positive-is-good, 97 are
sign-ambiguous zeros, 104 null. **No live gate reads a CLV field**, so no
recommendation, stake or family eligibility was ever changed
automatically; the exposure is human-mediated (Rule 71/81 rationales cite
CLV and do block real bets, but their figures are unreconstructable and
their win-rate half is sign-independent). Every advisory promotion verdict
flips. **C01/C01-PIT are unaffected** — their economics never use CLV.

### Fair-mid CLV answers the price-discovery question

At FIRST_DAILY, executable CLV of −1.167¢ decomposes into **−0.002¢
(0.2%) informational** and **−1.165¢ (99.8%) spread compression** (entry
spread 3.74¢ → closing 1.41¢). The identity
`executableCLV = fairMidCLV − spreadCompression/2` holds in **222/222**
groups, and fair-mid CLV is ~0.000 at every checkpoint, symmetric between
YES (−0.033) and NO (+0.031). **The consensus price does not move against
early buyers; they simply pay a wider spread.** Entering later buys
execution quality, not information — the correct and narrower
justification for C01-PIT's proximity tie-break.

### Blind holdout — protocol frozen, scorer locked, still sealed

`frozen_holdout_protocol.json`, sha256
`e1ad727cd15aaef7dbc62644c113a5ed0eac29e86883be67af0a14eca931a055`, for
rule `882f16d8330af1af…`. Sample floor 30 games / 4 dates; verdicts
INCONCLUSIVE / REPLICATED_FOR_PROSPECTIVE_SHADOW / FAILED_TO_REPLICATE.
CIs, both bootstrap p-values, CLV, win rate and drawdown are reported but
are explicitly **not** pass criteria — six dates are a screening gate into
prospective shadow, not production evidence.

`score_holdout.py` refuses to read any holdout byte without an
authorization file naming the exact rule hash. **That file is not created
here.** Ten tests prove the seal.

**BLIND HOLDOUT: SEALED — NOT AUTHORIZED.**

---

## BLIND HOLDOUT — SPENT (2026-09-01)

**HOLDOUT_STATUS = SPENT.** The six dates 2026-08-26 → 2026-08-31 were
opened under explicit CEO authorization, scored **exactly once** under the
frozen C01-PIT rule, and are **permanently retired**. No future research
may treat them as unseen. Earlier discovery/validation artifacts are
unchanged.

### VERDICT: INCONCLUSIVE

Decided by the pre-registered sample floor, checked before any economic
criterion: **17 independent games against the required 30** (dates: 4,
which does meet its own ≥4 threshold). Per the frozen protocol this is
neither a validation nor a failure.

| | Holdout | (reference) C01-PIT discovery | (reference) C01 validation |
|---|---:|---:|---:|
| Contracts | 21 | 152 | 80 |
| Games | 17 | 120 | 67 |
| Dates | 4 of 6 | 17 | 6 |
| W–L | 20–1 | 149–3 | 80–0 |
| Net ROI | **−0.03%** | +2.79% | +5.01% |

Net P/L −$0.07 on $202.07 actually deployed — flat, not a collapse. Win
rate 95.24% against a break-even of **94.93%** at the observed entries:
the strategy landed within a third of a percentage point of its own
break-even, which 17 games cannot separate from either side.

### Why the sample was so small

Not the price band, and not the strategy: **2026-08-27 and 2026-08-28
produced zero F5-total quotes inside [T-60, T-0) at all.** The window
requires a near-start capture, and those dates' cadence never fired there
(08-27 is FIRST_DAILY-only in the coverage manifest). Qualifying quotes by
date: 08-26 → 10, 08-29 → 6, 08-30 → 3, 08-31 → 4, 08-27 → 0, 08-28 → 0.
This is exactly the capture-coverage risk the 56.9% window coverage
measured at freeze time, now realized.

### Integrity

- Settlement-semantic mismatches: **0**. Integer-boundary contracts: **0**.
- Duplicate opportunities: 0. Stale/inactive exclusions: 0. Post-start
  exclusions: 210 (correctly refused).
- **4 doubleheader events excluded** (2026-08-29 BOS@NYY G1/G2, AZ@SF
  G1/G2, 65 observations): Kalshi appends `G1`/`G2`, and the frozen
  identity parser's team group rejects the digit, so it **refuses rather
  than guesses**. Conservative and disclosed — but it is why the
  identity criterion reads false, and it is a forward-looking fix for the
  prospective shadow, never a reason to re-score these dates.

### CLV is uninformative here, and honestly so

Executable and fair-mid CLV are both **exactly 0.00 across all 21 rows**,
with 0% beating the close and zero spread compression (entry spread ==
closing spread == 1.048¢). Cause: for every qualifying contract the
in-window entry quote **was** the closing quote. This is the same
ex-post-closeness the C01 execution audit found at 100%; it means the
holdout says nothing about CLV either way, rather than saying CLV was
neutral.

### Reported, explicitly NOT pass criteria

Game-clustered CI90 [−9.89%, +5.46%]; null-centered cluster bootstrap
p = 0.9915; wild cluster bootstrap p = 1.0; max drawdown −$9.97; longest
losing streak 1; largest single-date share of |P/L| 50.22%. Discovery and
holdout are **never** pooled into one significance test.

### Disposition

The rule stays **frozen and unchanged**. It is neither validated nor
failed. No real-money activation, no rule modification to manufacture
sample, and no reopening of A/B discovery. The proposed next step is
forward-only collection — see
`docs/EDGELAB_MLB_ALPHA_C01_PIT_PROSPECTIVE_SHADOW_PROPOSAL.md`.

---

## HOLDOUT_SCORING_TRIGGER_INCIDENT (2026-09-01)

**What happened.** `tests/research/test_mlb_alpha_0001_holdout_seal.py`
contained `test_scorer_main_exits_nonzero_while_sealed`, which called
`scorer.main()` to prove the sealed scorer exited non-zero. That
assertion was correct while the holdout was sealed. The moment the CEO
authorization file existed, the same call became **destructive**: `main()`
passed the gate, ran the one-time scoring pass, and wrote the real
`holdout_result.json`. The subsequent explicit scoring command then
correctly refused as a duplicate.

**Why the scientific result still stands.** Only one result artifact was
ever written; no result was inspected before either execution; the
persisted artifact was verified **byte-identical** to a fresh
recomputation (deterministic code, same frozen rule, same data); and no
rule parameter changed at any point. The one-look discipline — never
modify the rule after seeing the result — was not violated.

**Why it is still a defect.** A test suite must never be able to spend a
one-time research artifact. Fixes now in place:

- `main()` takes an injected `art_root`/`auth_path`; **every** gate test
  runs against `tmp_path` and never touches the canonical location.
- An autouse fixture snapshots the canonical `holdout_result.json` and
  `HOLDOUT_AUTHORIZATION.json` and **fails the test if either changes**,
  so the spent record is immutable by construction.
- `main()` now checks SPENT **before** scoring rather than after, so an
  accidental invocation cannot burn a scoring pass at all.
- No test invokes `main()` on the real artifact root.

This is recorded rather than quietly repaired.

## CI incident: the seal layer must not import the scientific stack

CI run #221 failed 5 tests with `ModuleNotFoundError: No module named
'numpy'`, raised while importing `score_holdout.py`. `requirements-ci.txt`
installs `duckdb` and `PyYAML` only; the research sandbox had numpy
pip-installed, so a module-scope `import numpy` passed locally and failed
in CI. An earlier report attributed run #221 to a replay-engine wall-clock
flake — that was run **#220**, and extrapolating it was wrong.

Fix: numpy, gzip and the repo research modules are imported **inside**
`score()`/`_summarize()`; the authorization/seal layer is stdlib-only. Two
tests pin it — an `ast`-parsed check that no heavy package is imported at
module scope (so a comment mentioning numpy cannot trip it, and a real
import cannot hide), and a counterpart proving the heavy imports still
exist where they are used. Verified with the exact CI command in a
numpy-free venv built from `requirements-ci.txt`.

## C01-PIT PROSPECTIVE SHADOW

`MLB-ALPHA-0001-C01-PIT-SHADOW-V1`. Protocol frozen at
`frozen_prospective_protocol.json`, sha256
`7788dcbec227e403…`; trigger-stream sha256 `b936557ddc7ad8d6…`; candidate
rule **unchanged** at `882f16d8330af1af…`.

### The trigger stream is part of the strategy identity

"FIRST qualifying observation" is meaningless without naming the sampling
process. The cadence audit (`cadence_audit.json`) measured the historical
streams that actually produced C01-PIT entries:

| | |
|---|---|
| Streams | `kalshi_registry_snapshots` (1,315 in-window obs), `standalone_price_check` (222) |
| Median inter-capture gap | **76 min** (multi-intraday), 119 min (single-daily) — **wider than the 60-minute window** |
| p90 / p95 gap | ~10–12 h (overnight-inflated) |
| Missing-window rate | **51.9% mean** — discovery 46.5%, validation 41.3%, **spent holdout 77.9%** |
| Dates with zero in-window capture | 2 of 29 (2026-08-27, 2026-08-28) |
| Median in-window captures per contract | multi-intraday **0.00**, single-daily 1.00 |

So roughly half of all eligible contracts never had an in-window
observation, and the spent holdout was materially worse-covered than
either earlier split. That, not the market, is why the holdout produced 17
games.

**Frozen prospective trigger:** `c01pit_trigger_v1`, polling every **10
minutes** inside `[T-60, T-0)`. Only this stream may create an official
entry.

**Stated honestly:** a 10-minute in-window poll is *denser* than the
historical cadence, so prospective entries will tend to fire earlier
within the window and more often. The **market rule is byte-identical**;
the **sampling process is deliberately re-specified**. Prospective results
are comparable to the historical record at the level of the market rule,
**not** at the level of the trigger process, and the shadow must never be
described as a like-for-like continuation of the historical opportunity
stream.

### Two streams, one trigger

Every persisted observation carries an explicit `canTriggerC01Pit`.
Research-only captures (`c01pit_observational_v1`, registry snapshots,
price checks) are `false` and can never create an entry or change its
price or timing — pinned by test. They exist so CLV finally has a
genuinely **later** closing quote, which is the collection defect that
made holdout CLV identically zero.

### Checkpoints, frozen before the first outcome

**First material: 100 independent games AND 10 independent dates.**
**Stronger: 200 games AND 20 dates.** These may not be lowered. Reaching
one authorizes a **review**, never a wager. Rows quarantined by a
settlement mismatch do not count toward either.

### Supporting infrastructure delivered

- **Doubleheader identity** (`lib/edgelab/mlb_alpha_identity.py`): parses
  Kalshi's `G1`/`G2` marker, splits concatenated team abbreviations
  against the canonical table, and resolves `gamePk` by game number then
  scheduled start. **Refuses on any surviving ambiguity** — one
  doubleheader game is never chosen arbitrarily. All four events the
  holdout excluded now resolve exactly; 13 fixtures cover single game, G1,
  G2 and ambiguity refusal.
- **Volume vs open interest**: raw Kalshi reports them as **distinct**
  fields (17,561 archived rows differ) and the adapter maps them
  correctly. The identical percentile tables in earlier reports were real
  data, not a field-mapping bug: on the KXMLBF5TOTAL universe the two
  coincide in **68.2%** of rows (19 of the 21 holdout rows), so the order
  statistics landed on equal-valued rows.
- **Depth**: the market-listing endpoint exposes **no** size/depth field —
  only `yes_bid`/`yes_ask`. Rows are flagged `DEPTH_UNAVAILABLE` rather
  than given a fabricated size. The collector can additionally read the
  documented public order-book endpoint (`--orderbook`) to record real
  top-of-book sizes. Until then, C01/C01-PIT prove only
  `TOP_OF_BOOK_PRICE_OBSERVED`, never `$10 FULL FILL PROVEN`.
- **Exchange settlement truth** (`lib/edgelab/exchange_settlement.py` +
  capture workflow): immutable write-once snapshots, a read-only
  comparator classifying AGREE / MISMATCH / CANONICAL_MISSING /
  EXCHANGE_MISSING / VOID_DISAGREEMENT, and **quarantine-and-alert on
  disagreement — never a silent overwrite of either source**.

**Production firewall:** no recommendation, staking, eligibility or
risk-gate integration; no orders; research-only. A real-money decision
requires explicit CEO review.
