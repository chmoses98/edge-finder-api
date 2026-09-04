# EdgeLab — MLB Night-Before / Early-Execution Value Research (2026-09)

**Status: COMPLETE — REVISION 2 (post-review corrections applied).**

**RESEARCH ONLY.** No production betting rule, recommendation, probability,
stake size, lineup gate, market eligibility, bankroll logic, settlement
behaviour or live-execution path was changed. No wager was placed. No
historical bet was modified. `productionBehaviorChanged: false` is stamped on
every artifact. The one operational addition is a research-only capture
workflow, isolated from every production reader (§10), which is **not yet
active** — a scheduled workflow on an unmerged branch does not run.

| | |
|---|---|
| Branch | `claude/mlb-night-before-timing-research-mqkzkk` |
| Report | `docs/EDGELAB_MLB_NIGHT_BEFORE_TIMING_RESEARCH_2026_09.md` |
| Artifacts | `data/edgelab/research_artifacts/night_before_timing/` |
| Research library | `lib/edgelab/research/night_before_timing.py` |
| Runner | `scripts/edgelab/run_night_before_timing_research.py` |
| Evidence level | **E1 / E0** — see §11. Deliberately not E2+. |

---

## 0. What changed in revision 2

Review found five material defects in revision 1. All are corrected here, and
the corrections are described rather than quietly folded in.

| # | Defect | Effect on conclusions |
|---|---|---|
| 1 | **Verdict was scoped too broadly.** "No useful night-before advantage" was stated over a horizon the data does not cover. | **Verdict split.** C now applies only to the observed ~12–22h overnight window. The true D-1 8–10 PM evening horizon is **UNTESTED**, not disproven (§13). |
| 2 | **Clustered CIs used `eventTicker`, which is per-series, not per-game.** One physical game is priced by ~17 Kalshi series. Revision 1 claimed "3,430 independent games". | **Point estimates unchanged; every CI recomputed and widened.** True count is 472 physical games in the corpus, 366 in the headline comparison. The headline still survives (§2.5, §5). |
| 3 | **Player props were excluded from ROI** on the stale belief that issue #43 was open. | **Issue #43 is closed** (PR #44, `c709b0a`). 97,423 settled prop contracts, all carrying settlement evidence, are now included — a 4x sample expansion. Props **strengthen** the finding (§6.2). |
| 4 | **Collector midnight bug.** `date -d 'tomorrow'` at the 00:00 ET checkpoint targeted D+2, skipping a slate. | Fixed with a tested, DST-safe ET anchor (§10). |
| 5 | **Contradictory raw-row totals**, and the audit silently dropped 39 capture files. | Reconciled and labelled (§2.6). The dropped files were the **unfiltered standalone captures** — the evidence most likely to contain a next-day market. Including them, the central claim still holds. |

A sixth defect was found while fixing #2: lineup-confirmation timestamps were
also keyed on `eventTicker`, so **no player prop could ever receive a Policy D
entry point** (`balanced_player_props` was literally 0 contracts). Lineups are
confirmed for a *game*, not a series; re-keying fixed it and expanded the
balanced game-market set from 933 to 1,546 contracts.

---

## 1. The question, and the answer

> *Can information genuinely available the previous evening identify MLB Kalshi
> wagers for which buying before confirmed lineups produces superior execution
> and ultimately positive expected value?*

Two separate answers, because the evidence supports two separate scopes.

**For the horizon we can actually observe (~12–22 hours out, in practice the
00:00–01:59 ET game-day window): no — and the premise fails a step earlier
than expected.** Waiting is *cheaper*, not more expensive. On 18,184 settled
game-market contracts across **366 physical MLB games**, the executable ask for
a YES purchase was on average **1.10 cents *higher*** the night before than at
the close (95% CI **+0.98 to +1.21**, game-clustered), and the early price was
the cheaper one on only **16.8%** of contracts. Player props agree
independently: **+0.85¢** (CI +0.73…+0.97) on YES and **+0.56¢** (CI
+0.44…+0.69) on NO, across 270 physical games.

**For the horizon the user actually asked about — sitting down at 8–10 PM ET to
bet tomorrow's slate — there is no evidence at all.** Zero observations exist.
That question is **UNTESTED**, and this report does not answer it.

The mechanism behind the observed result is mundane and not harvestable:
**overnight books are wider.** Median top-of-book spread at 12–24 hours out is
2 cents against 1 cent near first pitch, and 3 cents in several families. A
buyer pays half the spread on entry, so a wider book costs the early buyer on
*both* sides at once. There is no compensating mid-price drift in our favour.

The effect reproduces on **28 of 29** slate dates for game markets and 28 of 29
for props, in every timing window, on both sides of the book.

---

## 2. Read this before any profitability number

### 2.1 We have never captured a next-day market. Not once.

Auditing **339 capture files** (see §2.6 for how that reconciles with 360 on
disk), **645,589 parseable market rows**, **86 slate dates**, 2026-06-08 →
2026-09-04:

| Audit | Result |
|---|---|
| Market rows whose game date ≠ their capture file's slate date | **0** |
| …including within the 100,963 rows from **unfiltered standalone captures** | **0** |
| Capture files fetched on the ET calendar day *before* their slate date | **0 of 339** |
| Archived observations at **T-24h or earlier** | **0** |
| Archived observations classified `PREVIOUS_CALENDAR_EVENING` | **0** |
| Longest lead time ever achieved, any contract, any date | **22.01 hours** |

**Not a retention bug; nothing was discarded.** The root cause is structural:

1. `.github/workflows/capture-snapshots-scheduled.yml` resolves its target with
   `DATE=$(TZ='America/New_York' date +%Y-%m-%d)` — always *today* ET.
2. `api/kalshisearch.js:185` hard-filters the response to that one date:
   `mkts.filter(m => (m.event_ticker || '').includes(kalshiDate))`.

An 8 PM ET capture on day D asks for, and receives, **day D's** markets — games
already in progress — never day D+1's.

**Consequence.** The colloquial "night before" has **zero historical evidence
here**. Every number below describes the earliest horizon that *does* exist: the
overnight game-day window (00:00–01:59 ET), which is a ~20-hour lead on a
9:40 PM game and a ~12-hour lead on a 1:10 PM game. Those 64,365 `OVERNIGHT`
observations are the entire night-before evidence base. They are **not** the
same decision moment as 8 PM the previous evening, and this report never treats
them as equivalent.

### 2.2 Intraday history is a rolling 21-day window

`scripts/prune_kalshi_snapshots.py --execute` runs inside the capture workflow
and deletes timestamped snapshots older than
`lib/snapshot_retention.DEFAULT_RETENTION_DAYS` (21). That is why multi-capture
density begins 2026-08-14 — 21 days before this run — and why slate dates from
2026-06-21 to 2026-08-03 carry a maximum lead near zero.

### 2.3 Original evidence vs reconstruction vs unavailable

**ORIGINAL EVIDENCE**: archived top-of-book quotes, capture timestamps,
settlement outcomes (including all seven prop families), `LINEUP_CONFIRMATION`
capture timestamps.

**DETERMINISTIC RECONSTRUCTION** — computed by a stated rule, never presented as
captured:

- *Scheduled first pitch*, decoded from the ET date/time digits in the event
  ticker (needed because `scheduledStart` is populated on 54.2% of rows while
  the ticker is present on 100%). **Validated: 256,316 of 256,316 rows carrying
  an authoritative `scheduledStart` agree to within one minute — 100.0000%.**
- *Executable NO ask*, derived as `100 − yesBid` (§3).
- *Physical game identity*, from the event-ticker suffix (§2.5).

**UNAVAILABLE HISTORICALLY** — stated, not approximated: directly captured
NO-side quotes (**0 of 473,130 rows**); order-book depth; suspended market
states (every archived row is `status=active`); any next-calendar-day market;
per-quote exchange timestamps; `lineupConfirmationState` on price rows
(**0 of 473,130**).

### 2.4 Coverage totals

| | |
|---|---|
| Observation store span | 2026-08-01 → 2026-09-04 (35 slate dates) |
| Archived observations | 473,130 (0 duplicates) |
| Book usability | USABLE 346,585 · non-tradable bound 100,039 · spread > 15c 26,506 |
| Contracts with ≥1 usable pregame quote | 127,178 |

### 2.5 Four population counts that must never be conflated

Revision 1 reported `eventTicker` counts under the label "games". They are not
games. Corrected, from `coverage_report.json → gameIdentity`:

| Quantity | Count |
|---|---:|
| **Distinct physical MLB games** | **472** |
| Distinct Kalshi event tickers | 7,905 |
| Distinct market-series events (series × game) | 7,905 |
| Distinct contracts (market tickers) | 144,350 |
| Distinct MLB gamePks present | 346 |
| Distinct raw `gameId` values (two incompatible formats) | 797 |
| **Mean event tickers per physical game** | **16.75** |
| Physical games spanning more than one series | 472 of 472 |

**Why `eventTicker` is wrong.** One baseball game is priced by ~17 Kalshi
series, each with its own event ticker. Clustering on it treats one game as
~17 independent observations.

**Why raw `gameId` is also wrong.** The corpus stores it in two incompatible
formats — a dated string on game markets (`2026-08-01_DET_ATH_2140`) and a bare
gamePk on props (`824972`) — so one game appears under two values, yielding 797
"games" for the same 472. `mlbGameId` is genuinely canonical but is populated on
only 54.2% of rows.

**The key used, and its validation.** The event-ticker *suffix*
(`26AUG152138KCLAA`) is shared by every series pricing that game, present on
100% of rows, and re-validated on every run:

- 0 suffixes map to more than one gamePk (346 of 472 carry one)
- 0 gamePks map to more than one suffix
- 0 suffixes map to more than one dated `gameId` (451 of 472 carry one)
- Conclusion: **`VALIDATED_ONE_TO_ONE`**

Kalshi's doubleheader marker (`…1305BOSNYYG1`/`G2`) is part of the suffix and is
deliberately **not** stripped, so the two legs stay distinct games.

**A useful self-check on the correction.** For a sub-family drawn from a *single*
series, `eventTicker` and the physical-game key produce identical clusters — and
indeed every single-series cell in §6.1 is numerically unchanged from revision 1.
The correction bites exactly where tables **pool** series: the headline moved
from 3,430 to 366 clusters, `inning_result` (which pools F3+F5+F7) from 986 to
340, `winning_margin` (run line + F5 spread) from 682 to 346.

### 2.6 Reconciling the raw-row counts

Revision 1 quoted two different totals in two places. Both were wrong, for
opposite reasons. Corrected and labelled:

| Population | Count |
|---|---:|
| `kalshi_search_*.json` files on disk | 360 |
| Files matching a known capture shape | 360 |
| **Files audited** (after removing exact duplicates) | **339** |
| Duplicate capture files skipped | 21 |
| Duplicate market rows skipped | 24,098 |
| **Parsed market rows in audited files** | **645,589** |

Two distinct bugs produced the two wrong numbers:

- **568,464** came from a filename pattern matching only
  `kalshi_search_<date>[_HHMM].json`. That **silently dropped 39 files** —
  the `_standalone` research captures written by the standalone price checker
  and one `_recheck` capture, together 101,223 rows. Those are the *unfiltered*
  captures, i.e. exactly the evidence most likely to contain an out-of-slate
  market. Dropping them weakened the study's central claim by never looking at
  its best test. They are included now, and the cross-date count is **still 0**.
- **669,687** came from counting every file including the
  `kalshi_search_<date>.json` "latest for this date" copies, which are byte
  duplicates of a timestamped capture (same `date`, same `fetched_at`). That
  double-counted 24,098 rows.

Capture-kind breakdown of the audited population:

| Capture kind | Files | Market rows | Cross-date rows |
|---|---:|---:|---:|
| `SCHEDULED_TIMESTAMPED_CAPTURE` | 213 | 483,129 | 0 |
| `STANDALONE_RESEARCH_CAPTURE` | 38 | 100,963 | 0 |
| `SCHEDULED_LATEST_FOR_DATE_COPY` | 87 | 61,237 | 0 |
| `LINEUP_RECHECK_CAPTURE` | 1 | 260 | 0 |

### 2.7 Player-prop settlement is complete

Revision 1 excluded props from ROI citing GitHub issue #43. **That was stale.**
Issue #43 is closed; PR #44 (squash `c709b0a890e1a876c883451d36e71d9d767bf823`)
added automatic settlement for all seven captured families, and
`lib/edgelab/settlement.py::settle_market_full()` routes them to
`lib/edgelab/player_prop_settlement.py`.

Audited directly against the committed settlement corpus:

| Prop family | SETTLED | Unresolved |
|---|---:|---:|
| `hitter_hits_runs_rbis` | 29,199 | 1,069 |
| `hitter_total_bases` | 24,173 | 925 |
| `hitter_hits` | 20,157 | 699 |
| `hitter_rbis` | 12,880 | 424 |
| `pitcher_strikeouts` | 5,331 | 132 |
| `hitter_stolen_bases` | 4,958 | 134 |
| `pitcher_outs` | 725 | 16 |
| **Total** | **97,423** | **3,399** |

**All 97,423 settled prop rows carry `settlementEvidence`.** Nothing was
re-settled, guessed or fabricated.

Unresolved props are excluded **individually, by their own recorded reason** —
never as a class. Reasons, aggregated: `player_not_resolved_zero_candidates`
1,699 · `game_not_final` 778 · `player_participation_unverified` 641 ·
`player_prop_token_malformed` 145 · `player_prop_market_not_parseable` 136.
Full per-family breakdown in `coverage_report.json → settlements.unresolvedReasonsByFamily`.

---

## 3. Executable prices

- **YES purchase** → the displayed **YES ask**. Never the midpoint, never the
  last trade.
- **NO purchase** → **`100 − yesBid`**. No NO quote exists anywhere in the
  archive. One YES and one NO contract settle to exactly $1.00 between them, so
  buying NO at *p* is selling YES at `100 − p`; the best price to buy NO is 100
  minus the best resting YES bid. **Contractually exact for top of book.** Its
  limitation is *depth*, not price.
- **No fee adjustment** in any headline; the displayed executable price is the
  cost basis. Fee sensitivity: §14.
- **Unusable books excluded with an enumerable reason**:
  `MISSING_TOP_OF_BOOK`, `NON_TRADABLE_PRICE_BOUND`, `CROSSED_OR_INVERTED_BOOK`,
  `SPREAD_EXCEEDS_LIMIT`.
- **Staleness** flagged (`REPEATED_IDENTICAL_BOOK`), never used to delete rows.

---

## 4. Research-only timing classification

Production checkpoint semantics were **not modified**;
`lib/edgelab/checkpoints.py` is untouched. Three additive axes:

**Axis 1 — lead-time horizon**: `T_MINUS_0_4` … `T_MINUS_24_PLUS`,
`POST_START`, `UNKNOWN_TIMING`.
**Axis 2 — calendar context**: `PREVIOUS_CALENDAR_EVENING`, `OVERNIGHT`,
`GAME_DAY_MORNING/AFTERNOON/EVENING`, … A 19-hour lead is a 3 AM overnight
capture for a 10:10 PM game but a 6 PM previous-evening capture for a 1:10 PM
game; the study never conflates them.
**Axis 3 — executability**, per §3.

| Lead-time horizon | Obs | | Calendar context | Obs |
|---|---:|---|---|---:|
| `T_MINUS_24_PLUS` | **0** | | `PREVIOUS_CALENDAR_EVENING` | **0** |
| `T_MINUS_18_24` | 21,861 | | `OVERNIGHT` | 64,365 |
| `T_MINUS_12_18` | 36,136 | | `GAME_DAY_MORNING` | 7,885 |
| `T_MINUS_8_12` | 14,582 | | `GAME_DAY_AFTERNOON` | 263,092 |
| `T_MINUS_4_8` | 42,533 | | `GAME_DAY_EVENING` | 137,788 |
| `T_MINUS_0_4` | 195,092 | | | |
| `POST_START` | 162,926 | | | |

---

## 5. Price evolution and CLV

Paired **within one contract**, so composition is fully controlled. Sign
convention on every emitted row: **positive = the early price was HIGHER =
waiting was cheaper.** All CIs are **physical-game-clustered** (2,000 bootstrap
iterations, seed 20260904).

### 5.1 Game markets (settled, 20,998 contracts)

| Early | Late | Side | Contracts | **Games** | Mean Δ (¢) | 95% CI | Early cheaper |
|---|---|---|---:|---:|---:|---|---:|
| 12h+ | LINEUP CONFIRMED | YES | 8,312 | 166 | **+1.024** | +0.862 … +1.188 | 15.3% |
| 12h+ | LINEUP CONFIRMED | NO | 8,312 | 166 | +0.231 | +0.081 … +0.388 | 27.4% |
| 12h+ | CLOSING | YES | 18,184 | **366** | **+1.099** | **+0.983 … +1.207** | 16.8% |
| 12h+ | CLOSING | NO | 18,184 | 366 | +0.175 | +0.074 … +0.287 | 31.9% |
| 18h+ | CLOSING | YES | 9,099 | 191 | **+1.273** | +1.128 … +1.443 | 16.8% |
| 18h+ | CLOSING | NO | 9,099 | 191 | +0.204 | +0.051 … +0.351 | 33.7% |
| 8h+ | CLOSING | YES | 19,731 | 390 | +1.062 | +0.961 … +1.168 | 16.1% |
| FIRST GAME DAY | CLOSING | YES | 19,014 | 359 | +0.182 | +0.142 … +0.222 | 14.3% |
| LINEUP CONFIRMED | CLOSING | YES | 9,456 | 178 | +0.060 | +0.014 … +0.111 | 10.8% |
| LINEUP CONFIRMED | CLOSING | NO | 9,456 | 178 | +0.015 | −0.028 … +0.058 | 11.5% |

Answering the brief's own example: **if YES was purchasable at 52% the previous
night, its executable price at lineup confirmation was on average 51.0%.**
Waiting was cheaper, by about a cent, roughly five times out of six.

### 5.2 Player props (settled, 83,853 contracts)

| Early | Late | Side | Contracts | **Games** | Mean Δ (¢) | 95% CI | Early cheaper |
|---|---|---|---:|---:|---:|---|---:|
| 12h+ | LINEUP CONFIRMED | YES | 7,168 | 122 | **+0.880** | +0.716 … +1.062 | 12.5% |
| 12h+ | LINEUP CONFIRMED | NO | 7,168 | 122 | **+0.183** | +0.080 … +0.300 | 24.9% |
| 12h+ | CLOSING | YES | 13,622 | 270 | **+0.847** | +0.726 … +0.966 | 16.9% |
| 12h+ | CLOSING | NO | 13,622 | 270 | **+0.558** | +0.436 … +0.686 | 24.3% |
| 18h+ | CLOSING | YES | 4,901 | 118 | **+1.040** | +0.833 … +1.246 | 19.0% |
| 18h+ | CLOSING | NO | 4,901 | 118 | **+0.690** | +0.511 … +0.879 | 26.9% |
| FIRST GAME DAY | CLOSING | YES | 80,543 | 356 | +0.213 | +0.179 … +0.250 | 7.3% |

Props are an **independent confirmation**, and a stronger one on the NO side:
where game markets show +0.17¢, props show +0.56¢, both entirely above zero.

### 5.3 Closing-line value

Closing **mid** (side-adjusted) minus the executable entry ask. Positive = the
entry beat the closing mid.

| Population | Entry | Side | Contracts | Games | Mean CLV (¢) | 95% CI |
|---|---|---|---:|---:|---:|---|
| Game markets | 18h+ | YES | 9,099 | 191 | **−1.841** | −2.010 … −1.700 |
| Game markets | 12h+ | YES | 18,184 | 366 | **−1.686** | −1.791 … −1.571 |
| Game markets | 12h+ | NO | 18,184 | 366 | −0.762 | −0.874 … −0.659 |
| Game markets | FIRST GAME DAY | YES | 19,014 | 359 | −0.765 | −0.806 … −0.724 |
| Game markets | LINEUP CONFIRMED | YES | 9,456 | 178 | −0.698 | −0.754 … −0.648 |
| Props | 12h+ | YES | 13,622 | 270 | **−1.480** | −1.592 … −1.367 |
| Props | 12h+ | NO | 13,622 | 270 | **−1.191** | −1.315 … −1.072 |
| Props | LINEUP CONFIRMED | YES | 21,584 | 174 | −0.814 | −0.869 … −0.764 |

Every early entry has **negative** CLV, in both populations, on both sides, and
CLV improves monotonically the longer you wait. Some negativity is mechanical
(you always pay half a spread) but the magnitude tracks the spread exactly:
~1.7¢ overnight against ~0.70¢ at lineup confirmation.

---

## 6. Results by market family

### 6.1 Research sub-families (F3 / F5 / F7 and run line unpooled)

The corpus's `marketFamily` collapses `KXMLBF3`, `KXMLBF5` and `KXMLBF7` into
`inning_result`, and full-game run line with F5 spread into `winning_margin`.
Pooling those is exactly what the brief forbids, so the study adds a
research-only sub-family label keyed on the Kalshi series ticker.
`market_family_mapping.py` is **not** modified.

Early (12h+) vs closing, executable ask, physical-game clustered, BH-corrected:

| Research sub-family | Side | Contracts | Games | Mean Δ (¢) | 95% CI | BH sig. |
|---|---|---:|---:|---:|---|:--:|
| Full-game moneyline | YES | 732 | 366 | +0.006 | −0.025 … +0.037 | no |
| Full-game moneyline | NO | 732 | 366 | +0.048 | +0.016 … +0.078 | no |
| Full-game run line | YES | 2,076 | 346 | **+0.136** | +0.069 … +0.208 | yes |
| Full-game run line | NO | 2,076 | 346 | +0.062 | +0.006 … +0.121 | no |
| Full-game total | YES | 3,795 | 346 | **+0.899** | +0.709 … +1.099 | yes |
| Full-game total | NO | 3,795 | 346 | **−0.367** | −0.549 … −0.178 | yes |
| Team total | YES | 4,746 | 346 | **+1.906** | +1.757 … +2.059 | yes |
| Team total | NO | 4,746 | 346 | +0.105 | −0.034 … +0.245 | yes |
| NRFI / YRFI | YES | 366 | 366 | **+0.254** | +0.055 … +0.448 | yes |
| NRFI / YRFI | NO | 366 | 366 | −0.087 | −0.298 … +0.120 | no |
| F3 result | YES | 954 | 325 | **+1.354** | +1.192 … +1.539 | yes |
| F3 result | NO | 954 | 325 | **+1.039** | +0.927 … +1.152 | yes |
| F5 moneyline | YES | 1,006 | 339 | **+0.391** | +0.308 … +0.479 | yes |
| F5 moneyline | NO | 1,006 | 339 | **+1.054** | +0.938 … +1.179 | yes |
| F5 spread | YES | 1,334 | 336 | **+1.046** | +0.936 … +1.155 | yes |
| F5 spread | NO | 1,334 | 336 | **+0.430** | +0.334 … +0.521 | yes |
| F5 total | YES | 2,225 | 338 | **+1.378** | +1.193 … +1.556 | yes |
| F5 total | NO | 2,225 | 338 | +0.289 | +0.108 … +0.493 | no |
| F7 result | YES | 950 | 322 | **+1.047** | +0.954 … +1.146 | yes |
| F7 result | NO | 950 | 322 | **+0.710** | +0.606 … +0.814 | yes |
| Pitcher outs | YES | 461 | 255 | **+1.909** | +1.522 … +2.286 | yes |
| Pitcher strikeouts | YES | 3,510 | 262 | **+0.810** | +0.571 … +1.030 | yes |
| Hitter RBIs | YES | 1,456 | 207 | **+1.023** | +0.858 … +1.193 | yes |
| Hitter hits+runs+RBIs | YES | 3,126 | 209 | **+0.946** | +0.787 … +1.116 | yes |
| Hitter total bases | YES | 2,956 | 221 | **+0.657** | +0.534 … +0.789 | yes |
| Hitter hits | YES | 2,074 | 215 | **+0.681** | +0.559 … +0.803 | yes |
| Hitter stolen bases | YES | 39 | 19 | +0.333 | −0.050 … +0.710 | no |

Reading it:

- **Full-game moneyline is the cleanest market and shows nothing** either way
  (+0.006¢, CI straddling zero). Waiting for lineups on a moneyline is
  essentially *free* — but so is not waiting.
- **Exactly one cell favours early entry**: full-game total NO, −0.367¢
  (BH-significant). §9 shows it is not tradable.
- **Period markets are the worst place to be early** — F3 result +1.354¢/+1.039¢.
- **Unpooling mattered.** Separated, the full-game run line is nearly neutral
  (+0.136¢/+0.062¢); the pooled `winning_margin` figure (+0.492¢/+0.206¢) was
  driven by F5 spread (+1.046¢/+0.430¢) — a ~4x overstatement of the run line's
  early cost.
- **Every prop family is worse early on YES**, most BH-significant.

Where the BH flag and the CI disagree (team total NO is flagged while its CI
crosses zero), **the game-clustered CI is authoritative**. The sign test treats
4,746 contracts as independent when they sit on 346 games; it is used only as
the ranking statistic the BH step needs.

### 6.2 Realized ROI by prop family

`realized_roi_by_prop_family.csv`. **These rows are not a paired comparison** —
each policy is computed over the contracts that *have* that entry point, so the
ROIs are not directly subtractable. The rigorous timing comparison is §8.1.
Directionally, night-before entry is worse than the closing baseline on nearly
every family and side; e.g. `hitter_total_bases` YES: −0.093 night-before vs
−0.059 at close; `pitcher_outs` YES: −0.081 vs −0.032.

---

## 7. The two questions, kept apart

**A. Execution value — does buying earlier produce a better price?**
For the observed 12–22h horizon: **no, it produces a worse one** (§5), in both
populations, robust to correct clustering.

**B. Betting edge — can we identify which early contracts are worth buying?**
Not applicable, and separately unsupported (§8.2). Question B only matters if A
yields a discount worth being selective about. It does not.

Per the brief, this study was **not** built on the premise that the production
model tells us which night-before prices are wrong. **No production model
probability enters at any point.** Candidate selection uses only price, spread,
liquidity, family and clock.

---

## 8. Policy comparison and walk-forward

### 8.1 Policies A–E on one identical candidate set

The candidate set uses no future prices and no outcomes. It *does* condition on
future **data availability** — a contract must be quoted at all five policy
points to be comparable — which is stated rather than hidden; the far larger
unbalanced results in §5 show the same effect.

Win rate is identical across policies by construction (same contracts, same
outcomes), so the paired ROI difference isolates timing exactly.

| Population | Side | Comparison | Contracts | **Games** | Mean ROI(A) − ROI(B) | 95% CI |
|---|---|---|---:|---:|---:|---|
| Game markets | YES | vs B first game day | 1,546 | 36 | **−0.0317** | −0.0457 … −0.0177 |
| Game markets | YES | vs C T-90 | 1,546 | 36 | **−0.0328** | −0.0500 … −0.0160 |
| Game markets | YES | vs **D lineups confirmed** | 1,546 | 36 | **−0.0324** | **−0.0477 … −0.0176** |
| Game markets | YES | vs E T-30 | 1,546 | 36 | **−0.0329** | −0.0502 … −0.0161 |
| Game markets | NO | vs D lineups confirmed | 1,546 | 36 | −0.0108 | −0.0283 … +0.0058 |
| Props | YES | vs B first game day | 768 | 24 | **−0.0518** | −0.0973 … −0.0089 |
| Props | YES | vs **D lineups confirmed** | 768 | 24 | **−0.0552** | **−0.0986 … −0.0131** |
| Props | YES | vs E T-30 | 768 | 24 | **−0.0352** | −0.0676 … −0.0054 |
| Props | NO | vs D lineups confirmed | 768 | 24 | −0.0050 | −0.0126 … +0.0032 |

**Every YES confidence interval lies entirely below zero, in both populations,
after correct physical-game clustering.** Entering the night before cost about
**3.2 points of ROI** on game markets and **5.5 points** on props, versus
waiting for confirmed lineups, on identical bets. On the NO side the difference
is indistinguishable from zero.

This answers the brief's central question — *does lineup confirmation add enough
forecasting accuracy to compensate for the price deterioration incurred by
waiting?* — by dissolving it. **There is no price deterioration to compensate
for.** Waiting is free on moneylines and strictly better elsewhere, *before* any
information gain from seeing the lineup is counted.

### 8.2 Walk-forward

Rolling-origin, chronological, 24 folds. Rule: enter only in
`(family, price band, spread band)` cells whose mean early-minus-late executable
price on **strictly earlier slate dates** was favourable, with ≥20 prior
observations. Every ingredient is knowable at the entry timestamp. Thresholds
were fixed before the fold loop and never tuned against fold output.

| Population | Side | OOS contracts | **Games** | OOS mean ROI | 95% CI | Same contracts at CLOSE |
|---|---|---:|---:|---:|---|---:|
| Game markets | YES | 1,188 | 301 | +0.0148 | −0.050 … +0.076 | **+0.0147** |
| Game markets | NO | 7,363 | 302 | +0.1904 | +0.104 … +0.290 | **+0.1882** |
| Props | YES | 925 | 195 | −0.0429 | −0.194 … +0.121 | **−0.0343** |
| Props | NO | 3,480 | 218 | −0.0078 | −0.045 … +0.029 | **−0.0103** |

The rule adds **+0.0001, +0.0022, −0.0086 and +0.0024** over simply entering the
identical contracts at the close. It captures nothing anywhere.

The game-market NO **+0.19** absolute figure is **not** a night-before edge: it
appears at *every* entry point including the close, so it is not a timing result
at all. It is the ordinary structure of buying NO on low-probability threshold
contracts in a 29-day window, and this study makes no claim it is real or
tradable.

### 8.3 Stability

- **Leave-one-date-out**: removing any single slate date moves the headline
  between **1.070 and 1.132¢** (game markets) and **0.796 and 0.904¢** (props),
  across 29 folds each.
- **Per-date sign**: early was more expensive on **28 of 29** slate dates in
  both populations.
- **Multiple testing**: Benjamini–Hochberg at FDR 0.05 across all family and
  sub-family movement claims; effect sizes carried by physical-game-clustered
  bootstrap CIs.

---

## 9. Liquidity — why the one favourable cell is not tradable

| Family | Point | Contracts | Median spread | Median volume | Median OI | Zero-volume |
|---|---|---:|---:|---:|---:|---:|
| `game_result` | 12h+ | 848 | 1¢ | 7,436 | 6,582 | 0% |
| `game_result` | CLOSING | 944 | 1¢ | 93,731 | 87,814 | 0% |
| `first_inning_run` | 12h+ | 424 | 1¢ | 3,829 | 3,385 | 0% |
| `game_total` | 12h+ | 4,389 | 1¢ | 27 | 26 | **33.4%** |
| `game_total` | CLOSING | 5,178 | 1¢ | 2,573 | 2,415 | 3.2% |
| `team_total` | 12h+ | 5,494 | **3¢** | **0** | **0** | **82.3%** |
| `winning_margin` | 12h+ | 3,946 | 1¢ | **0** | **0** | **51.9%** |
| `inning_result` | 12h+ | 3,351 | **3¢** | **0** | **0** | **59.4%** |
| `inning_total` | 12h+ | 2,583 | **2¢** | **0** | **0** | **54.4%** |

- **`team_total`, `winning_margin`, `inning_result`, `inning_total` had not
  traded at all overnight** — median volume 0, median open interest 0, 52–82%
  of contracts with literally zero volume. A displayed top-of-book on a contract
  with zero open interest is a quote, not a market.
- **`game_total` — the one family with a favourable early NO price — is thin
  overnight**: median volume 27 against 2,573 at the close.
- **Only `game_result` and `first_inning_run` are genuinely liquid overnight**,
  and those are precisely the two families with no execution advantage.

The archive stores **no order-book depth**, so top-of-book prices imply nothing
about capacity. Every ROI figure assumes a one-contract fill at the displayed
ask; real fills at size overnight would be *worse*, pushing the verdict further
toward C.

**Trade-tape assets.** `data/edgelab/research_artifacts/mlb_alpha_0002/kalshi_history/`
is a different capture lineage, not committed to Git, and was **not** used: it
would need identity reconciliation against this corpus first, and mixing it in
unreconciled would risk contaminating the study.

---

## 10. Collector: audit, corrections, and activation status

**Does the existing capture retain next-day contracts in the prior evening? No**
(§2.1), with root cause in the workflow and the API.

`.github/workflows/research-night-before-capture.yml` is the smallest sufficient
research-only fix. Revision 2 corrects two defects in it.

**A. Midnight target-date bug — FIXED.** Revision 1 resolved the target as
`date -d 'tomorrow'` at every checkpoint. Correct at 20:00 and 22:00 ET; **wrong
at 00:00 ET**, where the ET calendar has already rolled forward, so "tomorrow"
is D+2 — the midnight capture would have skipped the very slate it exists to
observe. The rule now lives in one tested function,
`night_before_target_slate_date()`:

```
evening (ET hour >= 18)  -> target TOMORROW
overnight (ET hour < 6)  -> target TODAY
```

`test_midnight_checkpoint_does_not_skip_a_slate` asserts all three checkpoints
of one evening resolve to the same slate date across the real date transition.

**C. DST — FIXED.** 20:00 ET is 00:00 UTC under EDT but 01:00 UTC under EST, so
a pinned cron drifts an hour twice a year. The workflow now fires across the
**union of both offsets** (`0 0,1,2,3,4,5 * * *`) and **gates on the real
America/New_York hour** at run time; a firing whose actual ET hour is not a
checkpoint prints `SKIP` and the fetch, archive and commit steps are all
skipped. `fetched_at` remains the ground truth for every lead-time
calculation; the cron hour is never used as evidence.
`test_target_date_is_dst_safe_across_the_fall_transition` covers both sides of
the 2026-11-01 switch.

**E. Offseason guard — ADDED (pre-merge cleanup).** Only slate dates in
**March through November inclusive** are captured; December, January and
February skip fetch, archive and commit, and log the reason
(`OUTSIDE_MLB_SEASON_WINDOW`). The window is deliberately wider than the
season on both ends — Opening Day is late March at the earliest (including
international openers), and the World Series has never run past the first week
of November — so the **entire regular season and postseason** sit inside it
with weeks of margin. **This guard cannot exclude a postseason date.**

A calendar window is used rather than a live `lib/edgelab/mlb_schedule.py`
lookup on purpose: that module is a network adapter against statsapi, and a
network call inside the capture gate could skip a **real** capture whenever
statsapi is slow or unreachable — a far worse failure than a few empty winter
commits. The guard also **fails open** on an unparseable date.

Critically, the guard is **date-based only and does not suppress zero-market
captures during the season**. An in-season 20:00 ET capture that returns zero
next-day contracts is *real evidence*: it establishes that tomorrow's markets
were not yet listed at that hour, which is one of the open questions this
collector exists to answer. The decision function takes no market data at all
and runs before the fetch; a test asserts the archive and commit steps are
gated only on the skip flag, never on a market count.

Manual `workflow_dispatch` with an explicit target date **bypasses both gates**
and remains usable for ad-hoc research.

**D. Isolation — PRESERVED AND TESTED.** Writes only to
`data/kalshi_research_night_before_snapshots/`, which no production script
reads (`ingest_market_observations.py`, `snapshot_retention.py`,
`prune_kalshi_snapshots.py`, `collect_clv.py` all target
`data/kalshi_registry_snapshots/` exclusively). Every file is stamped
`captureClass: RESEARCH_NIGHT_BEFORE` and `productionBehaviorChanged: false`,
with a filename shape that cannot collide with production's. Commits route
through `scripts/ci/git_data_commit.py`. No betting trigger; no production
recommendation, probability, stake or lineup behaviour changes.
`tests/edgelab/test_night_before_capture_isolation.py` fails if any of this is
undone.

**B. ACTIVATION.** A scheduled GitHub Action on an unmerged feature branch
**does not run** — GitHub schedules workflows from the default branch only.
**Merging this branch to `main` is what starts prospective collection**, and is
the only way the D-1 evening horizon (§13-A) ever becomes answerable. Merging
activates a research collector; it does **not** activate early betting and
changes no production recommendation, probability, stake, lineup or execution
behaviour.

---

## 11. Hindsight and contamination controls

| Control | Enforcement |
|---|---|
| No future lineup data | No lineup content enters any price or policy computation; only the confirmation *timestamp* is used, to locate a moment on the clock. |
| No future starting-pitcher changes | No pitcher data enters the study. |
| No closing prices in candidate selection | Walk-forward cells are built from strictly-earlier slate dates; the closing quote is an evaluation target. |
| No settlement outcome in selection | Settlement enters only after selection. |
| No post-start quotes | Every selector rejects `hoursBeforeStart < 0`. Tested. |
| No reaching past a decision moment | `select_at_or_before` takes the last quote **at or before** confirmation, never the first after. Tested. |
| No best-price cherry-picking | `select_earliest_at_least` takes the *first* qualifying quote, not the cheapest. Tested. |
| No retroactive source as contemporaneous | §2.3 separates original from reconstructed; the reconstruction validates at 100.0% on 256,316 rows. |
| No threshold-picking after the holdout | Walk-forward thresholds fixed before the fold loop, never tuned against fold output. |
| Correct dependence structure | All CIs cluster on the **physical MLB game**, validated 1:1 (§2.5). |

**Evidence level: E1 (`RECONSTRUCTED_RETROSPECTIVE`) for timing/price results,
E0 (`DESCRIPTIVE`) for lineup risk.** Not E2: scheduled start and the NO price
are reconstructed, and the 21-day retention window means the corpus is not a
complete point-in-time record. Per `lib/edgelab/evidence_levels.py`, E0/E1 are
never promotable — consistent with this study's verdict.

---

## 12. Lineup uncertainty — and what cannot be measured

**Explanatory only. Never used in any prospective rule.**

**Expected-versus-actual lineup change at the overnight horizon is not
reconstructible from this repository.** No point-in-time *expected* lineup is
stored before game day: `data/pipeline/<date>/` is overwritten in place and ends
the day holding the **final confirmed** state, and the earliest frozen slate is
the `PRE_GAME_DECISION` snapshot at ~12:52 PM ET — hours *after* the overnight
window. The `lineup_audit_*.json` files record confirmation status after
midnight, not what was expected earlier.

So the brief's requested ex-post measures — changed hitters, offensive-quality
change, platoon/handedness changes, star rest-day absences, catcher changes,
probable-starter replacement, weather change, postponement risk — **cannot be
computed at this horizon** and are not reported rather than estimated from data
that would not support them.

What *can* be measured is the market's own revision across the confirmation
moment, per physical game:

| Family | Game-family cells | Mean abs mid revision | Median | p90 |
|---|---:|---:|---:|---:|
| `game_total` | 152 | 1.394¢ | 0.955¢ | 3.227¢ |
| `team_total` | 152 | 1.340¢ | 1.143¢ | 2.821¢ |
| `inning_result` | 37 | 1.135¢ | 1.000¢ | 2.500¢ |
| `game_result` | 26 | 1.115¢ | 1.000¢ | 2.000¢ |
| `first_inning_run` | 154 | 1.110¢ | 1.000¢ | 3.000¢ |
| `winning_margin` | 159 | 0.794¢ | 0.583¢ | 1.500¢ |

The market revises about **1 cent** on median between the overnight quote and
lineup confirmation, p90 1.5–3.2¢. That is the scale of uncertainty an early
bettor absorbs — and it is *larger* than any execution advantage found anywhere
in §5 or §6. The early bettor pays a wider spread to take on a ~1-cent
two-sided revision risk. Both legs are negative.

---

## 13. Verdict — four separate conclusions

### A. TRUE PREVIOUS-EVENING D-1, 8–10 PM ET → **UNTESTED / INSUFFICIENT EVIDENCE**

**Zero** historical observations exist at this horizon: 0 captures on the ET day
before their slate, 0 `PREVIOUS_CALENDAR_EVENING` observations, 0 observations
at T-24h or earlier, longest lead ever 22.01h. **This report does not answer the
user's literal question, and nothing here should be read as disproving a
previous-evening edge.** It remains open until prospective observations
accumulate.

The only directional hint — and it is a hint, not a finding — is that the trend
runs the wrong way monotonically within the range we *can* see: 18h+ is worse
than 12h+, which is worse than 8h+, which is worse than first-game-day. Nothing
guarantees that continues at 26–30 hours, and nothing suggests it reverses.

### B. OBSERVED OVERNIGHT / 12–22H HORIZON → **C: NO USEFUL ADVANTAGE**

The revision-1 finding **survives every correction**:

- **Survives correct physical-game clustering.** Point estimate identical
  (+1.0988¢); CI widened from [+1.032, +1.166] on 3,430 mislabelled clusters to
  **[+0.983, +1.207] on 366 real physical games**. Still entirely above zero.
- **Survives inclusion of settled player props.** Props are an independent
  population of 83,853 settled contracts across 270 games and point the same
  way, more strongly on the NO side (+0.558¢ vs +0.175¢). Paired policy
  differences are *more* negative for props (−0.055 vs −0.032 ROI).
- **Survives the corrected family analyses.** Unpooling F3/F5/F7 and the run
  line changed the magnitude on those cells but not the direction anywhere;
  exactly one cell out of 34 favours early entry, and it is illiquid.

Entering the night before cost **3.2 ROI points on game markets and 5.5 on
props** versus waiting for confirmed lineups, on identical bets, with every YES
confidence interval below zero.

### C. CURRENT REAL-MONEY WORKFLOW → **DO NOT CHANGE IT**

**No.** Do not relax or remove the existing requirement to wait for confirmed
lineups. This research supports the current practice on **price grounds alone**,
before any information gain from seeing the lineup is counted: at the horizon we
can observe, waiting is free on full-game moneylines and strictly cheaper
everywhere else. There is no discount to capture and therefore nothing to trade
off against lineup uncertainty.

No candidate night-before policy is proposed. Per the brief's §15 ("if a
strategy survives"), none did, so no entry window, starter-certainty
requirement, lineup-risk threshold, price band, minimum advantage, bet-up-to
logic or stake reduction is specified. The honest quantification of "how much
discount compensates for unconfirmed-lineup risk" is: **the market currently
offers no discount — it charges a premium of ~1.1¢ on YES.** Breaking even
against waiting would require closing that gap *and* covering the ~1-cent median
lineup revision risk (§12). No observed cell comes close.

### D. PROSPECTIVE RESEARCH → **YES, MERGE THE COLLECTOR (after review)**

The corrected collector should be merged so genuine 8 PM / 10 PM previous-evening
evidence begins accumulating. It is the **only** way conclusion A ever becomes
answerable, it changes no production behaviour, and it is isolated and tested.

**Exact next step:** review this branch, then merge it to `main`. The scheduled
workflow only runs from the default branch, so **nothing is collected until that
merge happens.** After ~30 slate dates of previous-evening captures, re-run
`scripts/edgelab/run_night_before_timing_research.py --stage all` (deterministic
and re-runnable) and revisit conclusion A. Note the 21-day prune means the
historical window will have moved.

**Not merged in this session, as instructed.**

### Also worth doing

1. **Consider raising `DEFAULT_RETENTION_DAYS`** if longer intraday history has
   research value. A storage-policy decision, not a research finding — left to
   the user.
2. **3,399 unresolved props** carry explicit reasons (§2.7); 1,699 are
   `player_not_resolved_zero_candidates`, which looks like a tractable
   player-resolution gap rather than a data limitation.

---

## 14. Reproducing

```bash
python3 scripts/edgelab/run_night_before_timing_research.py --stage all
python3 -m pytest tests/edgelab/test_night_before_timing.py \
                  tests/edgelab/test_night_before_capture_isolation.py \
                  tests/test_workflow_git_safety.py -q
```

Deterministic given the committed corpus: no network, no wall-clock branching;
the only randomness is a bootstrap seeded at 20260904 and recorded in
`analysis_report.json`.

### Machine-readable artifacts

| File | Contents |
|---|---|
| `coverage_report.json` | Coverage, evidence classification, **game-identity audit**, settlement audit |
| `coverage_by_slate_date.csv` | Per-date physical games, contracts, early-quote availability |
| `coverage_by_market_family.csv` | Market availability by family × lead-time horizon |
| `raw_archive_by_slate_date.csv` | Raw capture archive audit per slate date |
| `dataset_summary.json` | Linked-contract counts and research-point coverage |
| `contract_records.jsonl.gz` | Every linked contract (all families, props included) |
| `contract_point_prices_nonprop.csv.gz` | Flat per-point price table (game markets) |
| `price_movement.csv` | Movement, all point pairs, both sides, both populations |
| `price_movement_by_family.csv` | Family results with BH flags |
| `price_movement_by_research_sub_family.csv` | F3 / F5 ML / F5 spread / F5 total / F7 / run line / prop families unpooled |
| `clv_summary.csv` | CLV by entry point, side and population |
| `realized_roi_by_policy.csv` | Policies A–E, both populations |
| `realized_roi_by_family.csv` | Family ROI, policy A vs D |
| `realized_roi_by_research_sub_family.csv` | ROI on unpooled sub-families |
| `realized_roi_by_prop_family.csv` | **Prop-family ROI (unpaired — see §6.2)** |
| `policy_paired_differences.csv` | Night-before vs each later policy, paired, both populations |
| `walk_forward_folds.csv` | Per-fold out-of-sample results |
| `execution_value_by_slate_date.csv` | Per-date stability, both populations |
| `liquidity_by_family_and_point.csv` | Spread, volume, OI, zero-volume rate |
| `lineup_risk_by_family.csv`, `lineup_risk_by_game.csv` | Mid revision across confirmation, per physical game |
| `analysis_report.json` | Everything above, plus fee sensitivity and bootstrap config |

### Fee sensitivity

Headlines are **not** fee-adjusted; the displayed executable contract price is
the cost basis, per the brief. `lib/edgelab/kalshi_fees.py` remains production's
one fee engine and is deliberately not applied. Direction: Kalshi's per-contract
fee can only *reduce* every realized-ROI figure, so it cannot turn a negative
headline positive. The verdict is insensitive to it in the direction that
matters.
