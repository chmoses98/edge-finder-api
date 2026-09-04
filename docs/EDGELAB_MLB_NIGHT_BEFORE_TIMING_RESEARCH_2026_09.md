# EdgeLab — MLB Night-Before / Early-Execution Value Research (2026-09)

**Status: COMPLETE. Verdict C — NO USEFUL NIGHT-BEFORE ADVANTAGE.**

**RESEARCH ONLY.** No production betting rule, recommendation, probability,
stake size, lineup gate, market eligibility, bankroll logic, settlement
behaviour or live-execution path was changed by this milestone. No wager was
placed. No historical bet was modified. `productionBehaviorChanged: false` is
stamped on every artifact this study emits. The one operational addition is a
research-only capture workflow that writes to its own isolated directory and
that no production script reads (§10).

| | |
|---|---|
| Branch | `claude/mlb-night-before-timing-research-mqkzkk` |
| Report | `docs/EDGELAB_MLB_NIGHT_BEFORE_TIMING_RESEARCH_2026_09.md` |
| Artifacts | `data/edgelab/research_artifacts/night_before_timing/` |
| Research library | `lib/edgelab/research/night_before_timing.py` |
| Runner | `scripts/edgelab/run_night_before_timing_research.py` |
| Tests | `tests/edgelab/test_night_before_timing.py`, `tests/edgelab/test_night_before_capture_isolation.py` |
| Evidence level claimed | **E1 / E0** — see §11. Deliberately *not* E2+. |

---

## 1. The question, and the answer

> *Can information genuinely available the previous evening identify MLB Kalshi
> wagers for which buying before confirmed lineups produces superior execution
> and ultimately positive expected value?*

**No — and the premise fails one step earlier than expected.**

The study set out to measure how much execution value we sacrifice by waiting
for confirmed lineups. It found that we sacrifice none, because **waiting is
cheaper, not more expensive.** On 18,184 settled non-prop contracts spanning
3,430 independent games, the executable ask for a YES purchase was on average
**1.10 cents *higher*** the night before than at the close
(95% CI +1.03 to +1.17), and the early price was the cheaper one on only
**16.8%** of contracts. The NO side moves the same direction, more weakly
(+0.17 cents, CI +0.11 to +0.24).

This is not a subtle or fragile effect. It reproduces on **28 of 29** slate
dates, in every timing window tested, on both sides of the book, and on player
props as well as game markets. Leave-one-date-out recomputation moves the
headline between 1.07 and 1.13 cents — a range narrower than the effect itself.

The mechanism is mundane and is not an inefficiency we can harvest: **overnight
books are wider.** Median top-of-book spread at 12–24 hours out is 2 cents
against 1 cent near first pitch, and in several families it is 3 cents. A buyer
pays half the spread on entry, so a wider book costs the early buyer roughly
0.5–1.0 cent on *both* sides simultaneously. There is no compensating mid-price
drift in our favour.

Because the execution-value premise is refuted, the betting-edge question
(§7B) never becomes live: there is no discount to be selective about.

---

## 2. Read this before any profitability number: what evidence actually exists

The single most important finding of this project is a **coverage** finding,
and it materially narrows what could be concluded.

### 2.1 We have never captured a next-day market. Not once.

Scanning all **360** committed capture files (`data/kalshi_registry_snapshots/`,
2026-06-08 → 2026-09-04, **568,464** parseable market rows across **86** slate
dates):

| Audit | Result |
|---|---|
| Market rows whose game date ≠ their capture file's slate date | **0** |
| Capture files fetched on the ET calendar day *before* their slate date | **0 of 360** |
| Archived observations at **T-24h or earlier** | **0** |
| Archived observations classified `PREVIOUS_CALENDAR_EVENING` (18:00–23:59 ET on D-1) | **0** |
| Longest lead time ever achieved, any contract, any date | **22.01 hours** |

**This is not a retention bug and nothing was discarded.** The root cause is
structural and sits in two places:

1. `.github/workflows/capture-snapshots-scheduled.yml` resolves its target with
   `DATE=$(TZ='America/New_York' date +%Y-%m-%d)` — always *today* ET.
2. `api/kalshisearch.js:185` then hard-filters the upstream response to that one
   date: `mkts.filter(m => (m.event_ticker || '').includes(kalshiDate))`.

So an 8 PM ET capture on day D asks for, and receives, **day D's** markets —
the games already in progress — never day D+1's. Tomorrow's contracts were
never fetched, so no date-partitioning or retention behaviour could have lost
them. The EdgeLab observation partitions are internally consistent with this:
partition D contains only games dated D, because that is all that was ever
collected.

**Consequence.** The colloquial "night before" — sitting down at 8 or 10 PM to
bet tomorrow's slate — has **zero historical evidence in this repository**.
Every number in this report describes the *earliest horizon that does exist*:
the overnight game-day capture window, 00:00–01:59 ET, which for a 9:40 PM ET
game is a ~20-hour lead and for a 1:10 PM ET game is a ~12-hour lead. Those
64,365 `OVERNIGHT` observations are the entire night-before evidence base.

We do not treat that as equivalent to the previous evening, and §10 adds the
collector needed to make the real previous-evening horizon observable going
forward.

### 2.2 Intraday history is a rolling 21-day window

`scripts/prune_kalshi_snapshots.py --execute` runs inside the capture workflow
and deletes timestamped snapshots older than
`lib/snapshot_retention.DEFAULT_RETENTION_DAYS` (21). Only one dated file per
slate date survives beyond that. This is exactly why multi-capture density
begins at 2026-08-14 — 21 days before the 2026-09-04 run — and why slate dates
from 2026-06-21 to 2026-08-03 carry a maximum lead of ~0 hours.

Any future re-run of this study will see a *different, later* 21-day window.
The artifacts committed here are the point-in-time record.

### 2.3 What is original evidence, what is reconstructed, what is unavailable

Per spec requirement 1, these are kept strictly apart and are enumerated in
`coverage_report.json → evidenceClassification`.

**ORIGINAL EVIDENCE** — captured at the time, immutable, append-only:
archived top-of-book quotes (`yesBid`/`yesAsk`/`lastPrice`/`volume`/
`openInterest`), capture timestamps, settlement outcomes for auto-settled
families, and `LINEUP_CONFIRMATION` capture timestamps.

**DETERMINISTIC RECONSTRUCTION** — computed after the fact by a stated rule,
never presented as captured:

- *Scheduled first pitch*, decoded from the ET date/time digits Kalshi embeds
  in every event ticker. Necessary because `scheduledStart` is populated on
  only 54.2% of observations while the event ticker is present on 100%.
  **Validated: 256,316 of 256,316 observations that carry an authoritative
  `scheduledStart` agree with the reconstruction to within one minute —
  100.0000%, zero disagreements.**
- *Executable NO ask*, derived as `100 − yesBid` (§3).

**UNAVAILABLE HISTORICALLY** — stated rather than approximated:

- Directly captured NO-side quotes: **0 of 473,130 rows**. The upstream
  endpoint has no NO fields at all.
- Order-book depth beyond top of book: never captured.
- Suspended/inactive market states: every archived row is `status=active`.
- Any next-calendar-day market: never fetched (§2.1).
- Per-quote exchange timestamps: only our own capture time exists.
- `lineupConfirmationState` on price observations: **0 of 473,130 rows**
  populated. The confirmation moment had to be located from an independent
  store (`data/edgelab/model_evaluations`, 932 events, 2026-08-16 → 09-03).

### 2.4 Coverage totals

| | |
|---|---|
| Observation store span | 2026-08-01 → 2026-09-04 (35 slate dates) |
| Archived observations | 473,130 (0 duplicates on `(marketTicker, capturedAt)`) |
| Distinct contracts | 144,350 |
| Distinct games (event tickers) | 7,905 |
| Settled contracts | 118,438 (3,668 unresolved rows; **0** conflicting tickers) |
| Contracts with ≥1 usable pregame quote | 127,178 |
| Book usability | USABLE 346,585 · non-tradable bound 100,039 · spread > 15c 26,506 |

Per-slate-date and per-family coverage: `coverage_by_slate_date.csv`,
`coverage_by_market_family.csv`, `raw_archive_by_slate_date.csv`.

---

## 3. Executable prices (spec requirement 3)

Every price in this study is a price someone could actually have transacted at.

- **YES purchase** → the contemporaneous displayed **YES ask**. Never the
  midpoint, never the last trade. Asserted by test
  `test_yes_entry_is_the_displayed_ask_never_the_midpoint`.
- **NO purchase** → **`100 − yesBid`**, and the derivation is stated rather
  than assumed. No NO quote exists anywhere in the archive, so one had to be
  derived. One YES and one NO contract on the same market settle to exactly
  $1.00 between them, so buying NO at *p* is the same trade as selling YES at
  `100 − p`; the best price to buy NO is therefore 100 minus the best resting
  YES bid. This is **contractually exact for top of book**, not an
  approximation. Its real limitation is *depth*, not price: it inherits
  whatever size rests at the best YES bid, and the archive stores no depth at
  all. Every derived NO entry is treated as top-of-book-only.
- **Exit prices** (bids) are computed separately and never mixed into
  settlement-based returns.
- **No fee adjustment** anywhere in the headline, per spec: the user's real
  cost basis for this workflow is the displayed executable contract price.
  Fee sensitivity is §9.
- **Unusable books are excluded with an enumerable reason**, never silently
  dropped: `MISSING_TOP_OF_BOOK`, `NON_TRADABLE_PRICE_BOUND` (bid 0 or ask
  100 — no counterparty), `CROSSED_OR_INVERTED_BOOK`, `SPREAD_EXCEEDS_LIMIT`.
- **Staleness** is flagged (`REPEATED_IDENTICAL_BOOK`, ≥3 consecutive
  byte-identical books) but never used to delete rows, because the archive
  has no exchange-side quote timestamp to prove a quote is dead.

A useful sanity property, asserted in tests: buying both sides at the same
instant costs `100 + spread`. The spread is a real cost, and it is the whole
story of this report.

---

## 4. Research-only timing classification (spec requirement 2)

Production checkpoint semantics were **not modified**.
`lib/edgelab/checkpoints.py` is untouched: its ~7.5-minute tolerance around
T-90/60/30/15/5 is tuned for near-first-pitch work, and 155,768 of the
corpus's 473,130 observations fall into its single catch-all `INTERMEDIATE`
bucket — it cannot tell a 20-hour-out quote from a 3-hour-out one. Widening it
would silently reclassify historical rows that production code and prior
research reports already cite.

Instead `lib/edgelab/research/night_before_timing.py` adds a second,
independent classification axis over the same immutable rows. Three axes,
deliberately not collapsed:

**Axis 1 — lead-time horizon.** `T_MINUS_0_4`, `T_MINUS_4_8`, `T_MINUS_8_12`,
`T_MINUS_12_18`, `T_MINUS_18_24`, `T_MINUS_24_PLUS`, `POST_START`,
`UNKNOWN_TIMING`. A missing timestamp yields `UNKNOWN_TIMING` — a quote is
never inferred pregame without evidence of both timestamps.

**Axis 2 — calendar context.** `PREVIOUS_CALENDAR_EVENING`, `OVERNIGHT`,
`GAME_DAY_MORNING/AFTERNOON/EVENING`, `EARLIER_THAN_PREVIOUS_EVENING`,
`AFTER_GAME_DAY`. This axis exists because lead time alone is ambiguous about
the real decision moment: a 19-hour lead is a 3 AM overnight capture for a
10:10 PM game but a 6 PM previous-evening capture for a 1:10 PM game. Those are
different human decisions and the study never conflates them.

**Axis 3 — executability**, as in §3.

Observed distribution across the corpus:

| Lead-time horizon | Observations | | Calendar context | Observations |
|---|---:|---|---|---:|
| `T_MINUS_24_PLUS` | **0** | | `PREVIOUS_CALENDAR_EVENING` | **0** |
| `T_MINUS_18_24` | 21,861 | | `OVERNIGHT` | 64,365 |
| `T_MINUS_12_18` | 36,136 | | `GAME_DAY_MORNING` | 7,885 |
| `T_MINUS_8_12` | 14,582 | | `GAME_DAY_AFTERNOON` | 263,092 |
| `T_MINUS_4_8` | 42,533 | | `GAME_DAY_EVENING` | 137,788 |
| `T_MINUS_0_4` | 195,092 | | | |
| `POST_START` | 162,926 | | | |

---

## 5. Price evolution and CLV (spec requirements 4 and 5)

### 5.1 Executable price movement

Paired **within one contract**: the same market's own ask at two times, so
composition is fully controlled. Sign convention printed on every emitted row:
**positive = the early price was HIGHER = waiting was cheaper.**

Population: settled, non-prop, quoted at both points.

| Early point | Late point | Side | Contracts | Games | Mean Δ (¢) | 95% CI | Early cheaper |
|---|---|---|---:|---:|---:|---|---:|
| 12h+ | FIRST_GAME_DAY | YES | 16,961 | 3,229 | **+1.009** | +0.948 … +1.073 | 16.9% |
| 12h+ | FIRST_GAME_DAY | NO | 16,961 | 3,229 | +0.175 | +0.119 … +0.234 | 32.6% |
| 12h+ | T-90 | YES | 8,581 | 2,046 | **+1.240** | +1.143 … +1.344 | 17.0% |
| 12h+ | T-90 | NO | 8,581 | 2,046 | +0.179 | +0.082 … +0.270 | 33.9% |
| 12h+ | LINEUP CONFIRMED | YES | 5,040 | 680 | **+0.983** | +0.831 … +1.130 | 15.7% |
| 12h+ | LINEUP CONFIRMED | NO | 5,040 | 680 | +0.040 | −0.094 … +0.178 | 29.9% |
| 12h+ | T-30 | YES | 5,133 | 1,133 | **+1.139** | +1.020 … +1.264 | 17.8% |
| 12h+ | CLOSING | YES | 18,184 | 3,430 | **+1.099** | +1.032 … +1.166 | 16.8% |
| 12h+ | CLOSING | NO | 18,184 | 3,430 | +0.175 | +0.110 … +0.241 | 31.9% |
| 18h+ | CLOSING | YES | 9,099 | 1,734 | **+1.273** | +1.175 … +1.366 | 16.8% |
| 18h+ | CLOSING | NO | 9,099 | 1,734 | +0.204 | +0.111 … +0.302 | 33.7% |
| FIRST_GAME_DAY | CLOSING | YES | 19,014 | 3,552 | +0.182 | +0.157 … +0.209 | 14.3% |
| LINEUP CONFIRMED | CLOSING | YES | 5,569 | 744 | +0.057 | +0.013 … +0.104 | 10.5% |

Answering the spec's own worked example directly: **if YES was purchasable at
52% the previous night, its executable price at lineup confirmation was on
average 51.0%.** Waiting was cheaper, by about one cent, and it was cheaper
roughly five times out of six. The apparent early advantage does not merely
shrink after accounting for bid/ask — it *is* the bid/ask, and it points the
other way.

Player props (price only, no ROI — see §6) show the same direction more
weakly: YES **+0.781¢** (CI +0.711…+0.855, 18,414 contracts, 1,701 games), NO
**+0.476¢** (CI +0.402…+0.552).

### 5.2 Closing-line value

CLV is reported separately from executable movement and never conflated with
it. Definition: closing **mid** (side-adjusted) minus the executable entry ask.
Positive = the entry beat the closing mid.

| Entry point | Side | Contracts | Games | Mean CLV (¢) | 95% CI | Positive CLV |
|---|---|---:|---:|---:|---|---:|
| 18h+ | YES | 9,099 | 1,734 | **−1.841** | −1.934 … −1.742 | 15.8% |
| 18h+ | NO | 9,099 | 1,734 | −0.772 | −0.869 … −0.678 | 32.0% |
| 12h+ | YES | 18,184 | 3,430 | **−1.686** | −1.753 … −1.620 | 15.6% |
| 12h+ | NO | 18,184 | 3,430 | −0.762 | −0.828 … −0.696 | 29.9% |
| FIRST_GAME_DAY | YES | 19,014 | 3,552 | −0.765 | −0.793 … −0.738 | 12.4% |
| LINEUP CONFIRMED | YES | 5,569 | 744 | −0.653 | −0.702 … −0.603 | 9.5% |

Every early entry has **negative** CLV, and CLV improves monotonically the
longer you wait. Some negative CLV is mechanical — you always pay half a
spread — but the *magnitude* tracks the spread exactly: ~1.7¢ overnight against
~0.65¢ at lineup confirmation.

### 5.3 Realized returns

Kept strictly separate from price improvement, per spec requirement 5 ("a
timing strategy must not be declared profitable merely because it beats later
prices"). Return on one contract = `(100 − entry)/entry` on a win, `−1.0` on a
loss. A market that did not settle to a clean YES/NO scores **None**, never
`0.0`, so a void market can never be silently counted as a break-even wager.

---

## 6. Results by market family (spec requirement 6)

Fundamentally different families are never pooled into a headline. Early (12h+)
versus closing, executable ask, Benjamini–Hochberg FDR control at 0.05 across
all family-level claims.

| Family | Side | Contracts | Games | Mean Δ (¢) | 95% CI | Early cheaper | BH sig. |
|---|---|---:|---:|---:|---|---:|:--:|
| `game_result` (full-game ML) | YES | 732 | 366 | **+0.006** | −0.025 … +0.037 | 34.6% | no |
| `game_result` | NO | 732 | 366 | +0.048 | +0.016 … +0.078 | 32.9% | no |
| `game_total` | YES | 3,795 | 346 | +0.899 | +0.709 … +1.099 | 20.0% | **yes** |
| `game_total` | NO | 3,795 | 346 | **−0.367** | −0.549 … −0.178 | 39.3% | **yes** |
| `team_total` | YES | 4,746 | 346 | **+1.906** | +1.757 … +2.059 | 8.9% | **yes** |
| `team_total` | NO | 4,746 | 346 | +0.105 | −0.034 … +0.245 | 35.1% | yes |
| `winning_margin` (run line) | YES | 3,410 | 682 | +0.492 | +0.425 … +0.566 | 21.1% | **yes** |
| `winning_margin` | NO | 3,410 | 682 | +0.206 | +0.154 … +0.257 | 28.2% | **yes** |
| `inning_result` (F5 ML) | YES | 2,910 | 986 | +0.921 | +0.846 … +1.000 | 16.9% | **yes** |
| `inning_result` | NO | 2,910 | 986 | +0.936 | +0.871 … +1.005 | 17.2% | **yes** |
| `inning_total` (F5 totals) | YES | 2,225 | 338 | +1.378 | +1.193 … +1.556 | 13.1% | **yes** |
| `inning_total` | NO | 2,225 | 338 | +0.289 | +0.108 … +0.494 | 35.4% | no |
| `first_inning_run` (NRFI/YRFI) | YES | 366 | 366 | +0.254 | +0.055 … +0.448 | 30.9% | **yes** |
| `first_inning_run` | NO | 366 | 366 | −0.087 | −0.298 … +0.120 | 39.9% | no |

Three things matter here.

1. **Full-game moneyline is the cleanest market and shows nothing.** YES
   +0.006¢ with a CI straddling zero; NO +0.048¢. Waiting for lineups on a
   moneyline is essentially *free* — but so is not waiting. There is no
   execution value in either direction to trade against.
2. **Exactly one cell shows a genuine early advantage:** `game_total` NO at
   −0.367¢ (CI −0.549 … −0.178, BH-significant). Overnight game totals were
   priced slightly *high* and drifted down. §8 shows this is not exploitable.
3. **Everything else is worse early**, several of them badly — `team_total`
   YES costs nearly 2 cents more overnight.

### 6.1 Research sub-families: unpooling F3 / F5 / F7 and run line / F5 spread

The corpus's own `marketFamily` field **cannot** express the split the spec
asks for. Via `lib/edgelab/market_family_mapping.py`, `KXMLBF3`, `KXMLBF5` and
`KXMLBF7` all collapse into `inning_result`, and full-game run line
(`KXMLBSPREAD`) collapses together with F5 spread (`KXMLBF5SPREAD`) into
`winning_margin`. Pooling a 3-inning market with a 5-inning one, or a full-game
run line with an F5 spread, is exactly the pooling requirement 6 forbids.

All of these families **are** present in the corpus — `KXMLBF3` has 7,008
observations and `KXMLBF7` 7,610 — so the study adds a research-only
sub-family label keyed on the Kalshi series ticker, which is present on 100% of
rows. `market_family_mapping.py` is **not** modified: production's canonical
17-family vocabulary keeps its meaning and every prior report citing it stays
valid. Full results: `price_movement_by_research_sub_family.csv`,
`realized_roi_by_research_sub_family.csv`.

Early (12h+) versus closing, executable ask, BH-corrected within this table:

| Research sub-family | Side | Contracts | Games | Mean Δ (¢) | 95% CI | Early cheaper | BH sig. |
|---|---|---:|---:|---:|---|---:|:--:|
| Full-game moneyline (`KXMLBGAME`) | YES | 732 | 366 | +0.005 | -0.025 … +0.037 | 34.56% | no |
| Full-game moneyline (`KXMLBGAME`) | NO | 732 | 366 | +0.048 | +0.016 … +0.078 | 32.92% | no |
| Full-game run line (`KXMLBSPREAD`) | YES | 2076 | 346 | **+0.136** | +0.069 … +0.208 | 26.2% | yes |
| Full-game run line (`KXMLBSPREAD`) | NO | 2076 | 346 | +0.062 | +0.006 … +0.121 | 30.97% | no |
| Full-game total (`KXMLBTOTAL`) | YES | 3795 | 346 | **+0.899** | +0.709 … +1.099 | 20.03% | yes |
| Full-game total (`KXMLBTOTAL`) | NO | 3795 | 346 | **-0.367** | -0.549 … -0.177 | 39.26% | yes |
| Team total (`KXMLBTEAMTOTAL`) | YES | 4746 | 346 | **+1.906** | +1.757 … +2.058 | 8.85% | yes |
| Team total (`KXMLBTEAMTOTAL`) | NO | 4746 | 346 | **+0.105** | -0.034 … +0.245 | 35.12% | yes |
| NRFI / YRFI (`KXMLBRFI`) | YES | 366 | 366 | **+0.254** | +0.055 … +0.448 | 30.87% | yes |
| NRFI / YRFI (`KXMLBRFI`) | NO | 366 | 366 | -0.087 | -0.298 … +0.120 | 39.89% | no |
| F3 result (`KXMLBF3`) | YES | 954 | 325 | **+1.354** | +1.192 … +1.539 | 11.84% | yes |
| F3 result (`KXMLBF3`) | NO | 954 | 325 | **+1.039** | +0.927 … +1.152 | 15.2% | yes |
| F5 moneyline (`KXMLBF5`) | YES | 1006 | 339 | **+0.391** | +0.308 … +0.479 | 25.15% | yes |
| F5 moneyline (`KXMLBF5`) | NO | 1006 | 339 | **+1.054** | +0.938 … +1.179 | 15.9% | yes |
| F5 spread (`KXMLBF5SPREAD`) | YES | 1334 | 336 | **+1.046** | +0.936 … +1.155 | 13.19% | yes |
| F5 spread (`KXMLBF5SPREAD`) | NO | 1334 | 336 | **+0.430** | +0.334 … +0.521 | 23.91% | yes |
| F5 total (`KXMLBF5TOTAL`) | YES | 2225 | 338 | **+1.378** | +1.193 … +1.556 | 13.08% | yes |
| F5 total (`KXMLBF5TOTAL`) | NO | 2225 | 338 | +0.289 | +0.108 … +0.493 | 35.37% | no |
| F7 result (`KXMLBF7`) | YES | 950 | 322 | **+1.047** | +0.954 … +1.146 | 13.26% | yes |
| F7 result (`KXMLBF7`) | NO | 950 | 322 | **+0.710** | +0.606 … +0.814 | 20.53% | yes |

Unpooling changes no verdict but sharpens the picture, and it was worth doing:

- **The pooled `winning_margin` figure was misleading.** Separated, the
  full-game run line is nearly neutral (YES +0.136¢, NO +0.062¢); the pooled
  +0.492¢ / +0.206¢ was driven by **F5 spread** (+1.047¢ / +0.430¢). Reporting
  them together would have overstated the cost of early entry on the run line
  by roughly 4x.
- **Period markets are the worst place to be early.** F3 result is the single
  worst cell tested (YES +1.354¢, NO +1.039¢), with F5 total, F7 result and F5
  spread close behind — unsurprising for the thinnest, latest-to-be-priced
  contracts on the board.
- **Three markets are close to neutral early**, all full-game: full-game
  moneyline (+0.006¢ / +0.048¢, BH-non-significant on both sides), full-game
  run line NO (+0.062¢, BH-non-significant), and NRFI/YRFI NO (−0.087¢,
  BH-non-significant). None of them is *favourable* — they are merely not
  costly.

Where the BH flag and the confidence interval disagree — team total NO is
flagged significant while its CI (−0.034 … +0.245) crosses zero — **the
game-clustered CI is authoritative.** The sign test treats 4,746 contracts as
independent when they sit on only 346 games, so it overstates significance; it
is used here purely as the ranking statistic the BH step needs (§8.3).
- **Exactly one cell remains favourable after unpooling**: full-game total NO,
  −0.367¢ — the same cell §9 shows is not tradable overnight.

**Player props are excluded from every realized-ROI conclusion.** Pitcher and
hitter props are archived and researchable, and their *price* behaviour is
reported in §5.1, but their production settlement is unresolved work tracked in
**GitHub issue #43**. No prop outcome was fabricated and no prop contributes to
any ROI or verdict number. Props are 101,802 of the 127,178 linked contracts,
so this exclusion is the single largest restriction on sample size.

---

## 7. The two questions, kept apart (spec requirement 8)

### A. Execution value — does buying earlier produce a better price?

**No. It produces a worse one.** §5.1. The effect is negative, significant,
and stable across 28 of 29 dates. Waiting does not cost execution value; it
*gains* roughly 1.1 cents per contract on the YES side and 0.2 on the NO side.

### B. Betting edge — can we identify which early contracts are worth buying?

**Not applicable, and separately unsupported.** Question B only becomes
interesting if question A yields a discount worth being selective about. It
does not. We nonetheless tested it (§8) and found nothing.

Per the project brief, this study was explicitly *not* built on the premise
that the production model tells us which night-before prices are wrong. The
recent calibration work
(`docs/EDGELAB_MLB_10DAY_PRODUCTION_CALIBRATION_REVIEW_2026_09_03.md`,
`MLB-RSCH-0022/0023/0024`) found model-minus-market disagreement has not
demonstrated reliable betting edge, and large disagreements have historically
been associated with model error. **No production model probability enters this
study at any point.** Candidate selection here uses only price, spread,
liquidity, family and clock — never a fair-value estimate.

---

## 8. Policy comparison and walk-forward validation (requirements 9 and 10)

### 8.1 Policies A–E on one identical candidate set

The candidate set is determined without future information in the sense that
matters: no closing price, no settlement outcome, and no post-start quote
enters selection. What it *does* condition on is future **data availability** —
a contract must have been quoted at all five policy points to be comparable at
all. That restriction is stated rather than hidden, and the far larger
unbalanced results in §5.1 (up to 18,184 contracts) show the same effect, so
the restriction is not driving the conclusion.

Balanced set: **933 contracts across 138 independent games**, all settled, all
non-prop, all quoted at 12h+, first game day, T-90, lineup confirmation, T-30
and close.

| Policy | Entry | Side | Mean entry (¢) | Win rate | Mean ROI | 95% CI |
|---|---|---|---:|---:|---:|---|
| **A** Night before | 12h+ | YES | 47.52 | 42.2% | **−0.0989** | −0.258 … +0.062 |
| **B** First game day | first game-day quote | YES | 46.32 | 42.2% | −0.0647 | −0.234 … +0.106 |
| **C** T-90 | T-90 | YES | 46.04 | 42.2% | −0.0608 | −0.232 … +0.115 |
| **D** Lineups confirmed | confirmation | YES | 46.21 | 42.2% | −0.0626 | −0.233 … +0.110 |
| **E** T-30 | T-30 | YES | 46.04 | 42.2% | −0.0602 | −0.231 … +0.117 |
| **A** Night before | 12h+ | NO | 54.92 | 57.8% | +0.1427 | −0.050 … +0.374 |
| **D** Lineups confirmed | confirmation | NO | 54.90 | 57.8% | +0.1514 | −0.049 … +0.388 |

Win rate is identical across policies by construction — the same contracts, the
same outcomes. **Only the price differs.** So the paired ROI difference isolates
timing exactly:

| Comparison | Side | Contracts | Games | Mean ROI(A) − ROI(B) | 95% CI |
|---|---|---:|---:|---:|---|
| A vs **B** first game day | YES | 933 | 138 | **−0.0342** | −0.0486 … −0.0210 |
| A vs **C** T-90 | YES | 933 | 138 | **−0.0381** | −0.0578 … −0.0211 |
| A vs **D** lineups confirmed | YES | 933 | 138 | **−0.0363** | −0.0512 … −0.0220 |
| A vs **E** T-30 | YES | 933 | 138 | **−0.0387** | −0.0575 … −0.0218 |
| A vs B | NO | 933 | 138 | −0.0091 | −0.0304 … +0.0118 |
| A vs D | NO | 933 | 138 | −0.0087 | −0.0305 … +0.0125 |
| A vs E | NO | 933 | 138 | −0.0048 | −0.0294 … +0.0199 |

**Every YES confidence interval lies entirely below zero.** Entering the night
before cost about **3.4 to 3.9 percentage points of ROI** versus waiting, on
identical bets. On the NO side the difference is indistinguishable from zero.

This answers the central question the project posed — *does lineup confirmation
add enough forecasting accuracy to compensate for the price deterioration
incurred by waiting?* — by dissolving it. **There is no price deterioration to
compensate for.** Waiting is free on moneylines and strictly profitable
elsewhere, *before* any information gain from seeing the lineup is counted. The
information is a bonus on top of a better price, not a trade-off against a
worse one.

### 8.2 Walk-forward

Rolling-origin, chronological, no in-sample optimisation. The rule is the
simplest honest one the evidence could support: enter only in
`(market family, price band, spread band)` cells whose mean early-minus-late
executable price on **strictly earlier slate dates** was favourable, with ≥20
prior observations in the cell. Every ingredient — family, displayed price,
displayed spread, prior-date performance — is knowable at the entry timestamp.
No closing price, no settlement, no same-date information enters cell
selection. 24 folds.

| Side | OOS contracts | Games | OOS mean ROI | 95% CI | Same contracts, entered at CLOSE |
|---|---:|---:|---:|---|---:|
| YES | 1,188 | 743 | +0.0148 | −0.043 … +0.070 | **+0.0147** |
| NO | 7,363 | 1,406 | +0.1904 | +0.126 … +0.263 | **+0.1882** |

The rule adds **+0.0001** (YES) and **+0.0022** (NO) over simply entering the
identical contracts at the close. It captures nothing.

The NO-side **+0.19** absolute figure must not be misread as a night-before
edge. It appears at *every* entry point, including the close, so it is not a
timing result at all. It is the ordinary structure of buying NO on
low-probability threshold contracts over a 29-day window, and this study makes
no claim that it is real, persistent, or tradable — testing it is a separate
research question.

### 8.3 Stability

- **Leave-one-date-out:** removing any single slate date moves the headline
  execution-value number only between **1.070 and 1.132 cents** across 29 folds.
- **Per-date sign:** early was more expensive on **28 of 29** slate dates. The
  sole exception is 2026-08-30, at n = 54 contracts.
- **Multiple testing:** Benjamini–Hochberg at FDR 0.05 across all 28
  family-level movement claims. Sign-test p-values are used only as a ranking
  statistic for that step — contracts on one game are not independent, so every
  effect-size claim is carried by the **game-clustered bootstrap CI** instead
  (2,000 iterations, seed 20260904, cluster unit = one MLB game).

---

## 9. Liquidity — why the one favourable cell is not tradable (requirement 11)

Night-before "value" is useless without a realistic executable market. Median
figures at each research point:

| Family | Point | Contracts | Median spread | Median volume | Median OI | Zero-volume |
|---|---|---:|---:|---:|---:|---:|
| `game_result` | 12h+ | 848 | 1¢ | 7,436 | 6,582 | 0% |
| `game_result` | CLOSING | 944 | 1¢ | 93,731 | 87,814 | 0% |
| `first_inning_run` | 12h+ | 424 | 1¢ | 3,829 | 3,385 | 0% |
| `game_total` | 12h+ | 4,389 | 1¢ | 27 | 26 | **33.4%** |
| `game_total` | CLOSING | 5,178 | 1¢ | 2,573 | 2,415 | 3.2% |
| `team_total` | 12h+ | 5,494 | **3¢** | **0** | **0** | **82.3%** |
| `team_total` | CLOSING | 6,582 | 1¢ | 199 | 194 | 18.6% |
| `winning_margin` | 12h+ | 3,946 | 1¢ | **0** | **0** | **51.9%** |
| `inning_result` | 12h+ | 3,351 | **3¢** | **0** | **0** | **59.4%** |
| `inning_total` | 12h+ | 2,583 | **2¢** | **0** | **0** | **54.4%** |

Read plainly:

- **`team_total`, `winning_margin`, `inning_result` and `inning_total` had not
  traded at all overnight.** Median volume 0, median open interest 0, and
  52–82% of contracts with literally zero volume. A displayed top-of-book on a
  contract with zero open interest is a quote, not a market.
- **`game_total` — the one family with a favourable early NO price — is thin
  overnight**: median volume 27 against 2,573 at the close, one contract in
  three with no volume at all. The −0.37¢ advantage sits in a market with ~1%
  of its eventual liquidity.
- **Only `game_result` and `first_inning_run` are genuinely liquid overnight**,
  and those are precisely the two families with no execution advantage.

The archive stores **no order-book depth**, so top-of-book prices imply nothing
about capacity, and this report does not pretend otherwise. Every ROI figure
here assumes a fill of one contract at the displayed ask. Real fills at size
overnight would be *worse* than modelled, which can only push the verdict
further toward C.

**Trade-tape / order-book research assets.** The recovered Kalshi exchange
history under `data/edgelab/research_artifacts/mlb_alpha_0002/kalshi_history/`
is deliberately not committed to Git (see `.gitignore`) and must be hydrated
separately. It was **not** used here: it is a different capture lineage with its
own provenance and would have to be identity-reconciled against this corpus
before it could add valid evidence, and mixing it in unreconciled would risk
contaminating the study. Whether it can extend the depth analysis is a
worthwhile follow-up, not a gap in this result.

---

## 10. Collector audit and the one research-only change (requirement 12)

**Does the existing capture already retain next-day contracts in the prior
evening? No** — §2.1, with root cause identified in the workflow and the API.

Because the answer is no, the smallest sufficient research-only improvement was
made: **`.github/workflows/research-night-before-capture.yml`**.

- Fetches **tomorrow** ET (`date -d 'tomorrow'`) instead of today, and passes
  it through as `?date=` — `api/kalshisearch.js` already accepts an explicit
  date and only *defaults* to today, so **no API change was needed**.
- Runs at **20:00, 22:00 and 00:00 ET** — the 8 PM, 10 PM and midnight research
  checkpoints. These are research checkpoints, **not betting triggers**.
- Writes **only** to `data/kalshi_research_night_before_snapshots/`, a
  directory no production script reads. `ingest_market_observations.py`,
  `snapshot_retention.py`, `prune_kalshi_snapshots.py` and `collect_clv.py` all
  target `data/kalshi_registry_snapshots/` exclusively, so a research capture
  can never be mistaken for a production slate capture, enter the production
  CLV/settlement path, or be pruned on the production 21-day clock.
- Stamps every file `captureClass: RESEARCH_NIGHT_BEFORE` and
  `productionBehaviorChanged: false`, and uses a filename shape
  (`night_before_<slate>_<utc>.json`) that cannot collide with production's
  `kalshi_search_<date>[_HHMM].json`.
- An empty result is recorded, not hidden: *when* Kalshi first lists next-day
  MLB markets is itself an open research question this capture will answer.

`tests/edgelab/test_night_before_capture_isolation.py` fails if any of that
separation is undone.

No other collector change was made. No production capture, cron, retention or
ingest behaviour was modified.

---

## 11. Hindsight and contamination controls (requirement 13)

| Control | How it is enforced |
|---|---|
| No future lineup data | No lineup content enters any price or policy computation. The lineup-confirmation *timestamp* is used only to locate a moment on the clock. |
| No future starting-pitcher changes | No pitcher data enters the study at all. |
| No closing prices in candidate selection | Walk-forward cells are built from strictly-earlier slate dates only; the closing quote is an evaluation target, never an input. |
| No settlement outcome in candidate selection | Settlement enters only in `realized_return_per_contract`, after selection. |
| No post-start quotes | Every selector rejects `hoursBeforeStart < 0`. Asserted by test. |
| No reaching forward past a decision moment | `select_at_or_before` takes the last quote **at or before** the lineup-confirmation time, never the first one after it. Asserted by test. |
| No best-price cherry-picking | `select_earliest_at_least` takes the *first* qualifying quote, not the cheapest one in the window. Asserted by test. |
| No retroactively-updated source presented as contemporaneous | §2.3 separates original evidence from deterministic reconstruction; the one reconstruction is independently validated at 100.0% on 256,316 observations. |
| No threshold-picking after seeing the holdout | The walk-forward rule's thresholds (≥20 prior observations, favourable prior-date sign) were fixed before the fold loop and never tuned against fold output. |
| Point-in-time validity documented per feature | Every feature used in the prospective rule — family, displayed price, displayed spread, prior-date aggregates — is present in the archived quote itself or derived only from strictly earlier dates. |

**Evidence level: E1 (`RECONSTRUCTED_RETROSPECTIVE`) for the timing/price
results, E0 (`DESCRIPTIVE`) for the lineup-risk section.** Not E2: scheduled
start and the NO-side price are reconstructed rather than observed, and the
21-day retention window means the corpus itself is not a complete
point-in-time record. Per `lib/edgelab/evidence_levels.py`, E0/E1 are never
promotable and cannot carry a `SHADOW_CANDIDATE` or `PROMOTION_CANDIDATE`
disposition — which is consistent with this study's verdict.

---

## 12. Lineup uncertainty (requirement 7) — and what cannot be measured

**Explanatory only. Never used in any prospective rule.**

A hard limitation must be stated first. **Expected-versus-actual lineup change
at the overnight horizon is not reconstructible from this repository.** There is
no point-in-time *expected* lineup stored anywhere before game day:
`data/pipeline/<date>/` is overwritten in place and ends the day holding the
**final confirmed** state (`lineupStatus: confirmed`, `status: Final`), and the
earliest frozen slate is the `PRE_GAME_DECISION` snapshot at roughly 12:52 PM
ET — hours *after* the overnight entry window. The `data/lineup_audit_*.json`
files (47 files, 1,266 team-rows: 1,126 confirmed, 140 missing) are generated
after midnight and record confirmation status, not what was expected earlier.

So the spec's requested ex-post measures — number of changed hitters,
offensive-quality change, handedness/platoon changes, star rest-day absences,
catcher changes, probable-starter replacement, weather change,
cancellation/postponement risk — **cannot be computed at the night-before
horizon with the evidence this repository holds.** They are not reported rather
than estimated from data that would not support them.

What *can* be measured is the market's own revision across the lineup-
confirmation moment — the price impact of whatever lineup news arrived:

| Family | Game-family cells | Mean abs mid revision | Median | p90 |
|---|---:|---:|---:|---:|
| `game_total` | 152 | 1.394¢ | 0.955¢ | 3.227¢ |
| `team_total` | 152 | 1.340¢ | 1.143¢ | 2.821¢ |
| `inning_result` | 37 | 1.135¢ | 1.000¢ | 2.500¢ |
| `game_result` | 26 | 1.115¢ | 1.000¢ | 2.000¢ |
| `first_inning_run` | 154 | 1.110¢ | 1.000¢ | 3.000¢ |
| `winning_margin` | 159 | 0.794¢ | 0.583¢ | 1.500¢ |

The market revises about **1 cent** on median between the overnight quote and
lineup confirmation, with a p90 of 1.5–3.2 cents. That is the scale of the
uncertainty an early bettor absorbs — and it is *larger* than any execution
advantage found anywhere in §5 or §6. The early bettor pays a wider spread to
take on a ~1-cent two-sided revision risk. The trade is negative on both legs.

---

## 13. Verdict

### **C — NO USEFUL NIGHT-BEFORE ADVANTAGE.**

Waiting for additional information is not costing execution value. It is
*saving* it: about 1.1 cents per contract on the YES side, worth roughly 3.4–3.9
points of ROI on identical bets, with every confidence interval below zero and
the effect reproducing on 28 of 29 dates. The only BH-significant cell that
favoured early entry, at either the family or the unpooled sub-family level
(full-game total NO, −0.37¢), sits in a market that had traded a median of 27
contracts overnight against 2,573 by the close, and a
walk-forward rule built to exploit exactly that kind of cell added +0.0001 to
+0.0022 ROI over simply entering the same contracts at the close.

**A is not supported and was not forced. B is not supported either** — B would
require earlier prices to be meaningfully *better* in some situation, and they
are meaningfully *worse* in nearly every situation examined.

**Bounded scope of this verdict.** C is established for the horizon that has
evidence: the overnight game-day window, 12–22 hours before first pitch. The
true previous-evening decision moment (8–10 PM ET the night before) has **zero
historical observations** and is therefore **untested**, not disproven. Nothing
here rules out different behaviour at 26–30 hours out — but nothing here
suggests it either, since the trend runs the wrong way monotonically: the
earlier the entry, the worse the price and the worse the CLV (18h+ is worse than
12h+, which is worse than 8h+, which is worse than first-game-day).

### Should any policy go to prospective shadow testing?

**No.** No candidate night-before policy is proposed, and none deserves shadow
testing. Per §15 of the brief, "if a strategy survives" — none did, so no
inactive policy, entry window, starter-certainty requirement, lineup-risk
threshold, price band, minimum advantage, bet-up-to logic or stake reduction is
specified. Deriving an X-cent discount threshold and a Y lineup-risk score would
mean inventing them, which the brief explicitly forbids.

The honest quantification of "how much discount is necessary to compensate for
unconfirmed-lineup risk" is: **the market does not currently offer a discount at
all — it charges a premium of roughly 1.1 cents on the YES side.** For an early
entry to break even against waiting it would first have to close that ~1.1-cent
gap, then cover the ~1-cent median lineup revision risk documented in §12. No
observed cell comes close.

### What is worth doing next

1. **Let the new collector run.** It is the only way the previous-evening
   horizon ever becomes answerable. Revisit after ~30 slate dates.
2. **Re-run this study on the fuller corpus then** — the runner is
   deterministic and re-runnable. Note the 21-day prune means the historical
   window will have moved.
3. **Consider raising `DEFAULT_RETENTION_DAYS`** if longer intraday history has
   research value. That is a storage-policy decision, not a research finding,
   and is deliberately left to the user.
4. **Resolve player-prop settlement (issue #43)** — props are 101,802 of
   127,178 linked contracts and are currently excluded from every ROI number.
5. Neither the model nor any production rule needs changing on the basis of
   this work, and **the current practice of waiting for confirmed lineups is
   validated by it** — on price grounds alone, before counting the information.

---

## 14. Reproducing

```bash
python3 scripts/edgelab/run_night_before_timing_research.py --stage all
python3 -m pytest tests/edgelab/test_night_before_timing.py \
                  tests/edgelab/test_night_before_capture_isolation.py -q
```

Deterministic given the committed corpus: no network, no wall-clock branching,
and the only randomness is a bootstrap seeded at 20260904 and recorded in
`analysis_report.json`.

### Machine-readable artifacts (`data/edgelab/research_artifacts/night_before_timing/`)

| File | Contents |
|---|---|
| `coverage_report.json` | Historical coverage, evidence classification, reconstruction validation |
| `coverage_by_slate_date.csv` | Per-date contracts, games, early-quote availability |
| `coverage_by_market_family.csv` | Market availability by family × lead-time horizon |
| `raw_archive_by_slate_date.csv` | Raw capture archive audit, per slate date |
| `dataset_summary.json` | Linked-contract counts and research-point coverage |
| `contract_records.jsonl.gz` | Every linked contract, all points, all families |
| `contract_point_prices_nonprop.csv.gz` | Flat per-point price table (non-prop) |
| `price_movement.csv` | Executable price movement, all point pairs, both sides |
| `price_movement_by_family.csv` | Family results with BH significance flags |
| `price_movement_by_research_sub_family.csv` | F3 / F5 ML / F5 spread / F5 total / F7 / run line unpooled |
| `realized_roi_by_research_sub_family.csv` | ROI on the same unpooled sub-families |
| `clv_summary.csv` | CLV by entry point and side |
| `realized_roi_by_policy.csv` | Policies A–E on the balanced set |
| `realized_roi_by_family.csv` | Family ROI, policy A vs D |
| `policy_paired_differences.csv` | Night-before vs each later policy, paired |
| `walk_forward_folds.csv` | Per-fold out-of-sample results |
| `execution_value_by_slate_date.csv` | Per-date stability |
| `liquidity_by_family_and_point.csv` | Spread, volume, OI, zero-volume rate |
| `lineup_risk_by_family.csv`, `lineup_risk_by_game.csv` | Mid revision across confirmation |
| `analysis_report.json` | Everything above, plus fee sensitivity and bootstrap config |

### Fee sensitivity (optional, per requirement 3)

Headline numbers are **not** fee-adjusted; the displayed executable contract
price is the cost basis. `lib/edgelab/kalshi_fees.py` remains production's one
fee engine and is deliberately not applied here. Direction of the omission:
Kalshi's per-contract fee can only *reduce* every realized-ROI figure above, so
it cannot turn a negative headline ROI positive. The verdict is insensitive to
it in the direction that matters.
