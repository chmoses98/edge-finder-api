# EdgeLab Phase 2 Milestone 2: the calibration engine

Status: measurement only. This milestone reports how well past model
probabilities, edges, and confidence levels lined up with real settled
outcomes. **It makes no betting recommendations, and nothing here feeds
back into production recommendation, staking, or settlement logic.**
Source of truth for the architecture this builds on:
`docs/EDGELAB_PHASE2_DESIGN.md` §5; built directly on top of the
Milestone 1 DuckDB query foundation (`docs/EDGELAB_ANALYTICS.md`).

## 0. Running it locally

```bash
python3 scripts/edgelab/run_calibration.py
```

Writes `data/edgelab/analytics/latest_calibration.json` (machine-readable,
one key per query) and `data/edgelab/reports/phase2_calibration.md`
(human-readable), both regenerated (not appended) and committed, exactly
like Milestone 1's analytics outputs. Nothing else is written; this
script never touches `data/edgelab/<entity>/` itself, and never writes a
`.duckdb` file or Parquet export (see `docs/EDGELAB_ANALYTICS.md` §5 for
why).

## 1. Methodology

### 1.1 "Decided" bets

Every calibration bucket in `lib/edgelab/calibration.py` is measured over
**decided bets**: settled bets whose `result` is `WIN` or `LOSS`.
`PUSH`/`VOID` bets are real settled bets but have no win/loss to compare
a predicted probability against, so they're excluded from every
bucket's `n` entirely — not folded into the losses, not silently
dropped from the underlying data, just not counted toward a calibration
question they can't answer. This keeps one `n` meaningful across every
metric in a bucket row: win rate, ROI, CLV, and calibration error all
share the same denominator.

### 1.2 Sample-size status

A non-configurable, three-tier gate (`lib.edgelab.calibration.calibration_status`,
`calibration_status_sql`) — a different, tighter scheme than Milestone
1's single `n>=20` cutoff (`lib.edgelab.analytics.MIN_SAMPLE_SIZE`),
because this milestone's job is calibration, not just description:

| n | Status | Meaning |
|---|---|---|
| < 20 | `INSUFFICIENT_SAMPLE` | Noise, not evidence. |
| 20 – 99 | `DESCRIPTIVE_ONLY` | A real number, but not yet a calibrated statistical claim. |
| >= 100 | `CALIBRATED` | Enough volume for the reliability numbers to be a meaningful summary — still not, by itself, a signal to change strategy. |

The underlying value (win rate, ROI, CLV, calibration error) is **always
computed and returned, regardless of status** — status is a mandatory
reading instruction, never a filter that withholds a number. As of this
milestone, every bucket in the real 77-bet ledger is `INSUFFICIENT_SAMPLE`
(the ledger has 14 decided bets total) — expected this early, not a bug.

### 1.3 What each bucket row reports

- `n` — decided-bet count.
- `winRate` / `actualWinRate` — identical values (both names present
  because this milestone's spec asked for both explicitly); wins divided
  by `n`.
- `expectedWinRate` — average `PlacedBet.modelFairProbability` across the
  bucket's decided bets. **`None`, not `0`, when no bet in the bucket has
  a recorded value** — see §3's finding on this.
- `calibrationError` — `actualWinRate - expectedWinRate`; `None` whenever
  either side is `None`. A positive value means the model underestimated
  its own win probability for this bucket; negative means it
  overestimated.
- `roi` — `SUM(netProfitLoss) / SUM(stake)` over the bucket's decided
  bets; `None` when total stake is 0 or unavailable.
- `avgClv` — average `PlacedBet.clv` over decided bets that have a
  recorded CLV (a bet can be decided without a CLV, e.g. CLV was never
  collected for it); `None` when none do.
- `status` — §1.2's three-tier gate.

## 2. Bucket definitions

| Dimension | Function | Bucket key | Notes |
|---|---|---|---|
| Estimated edge | `edge_bucket_calibration` | `PlacedBet.estimatedEdgeAtEntry`, 2-point-wide bins (`"2-4"`, half-open `[2,4)`) | Null edge -> its own `UNKNOWN` bucket. |
| Confidence | `confidence_calibration` | `PlacedBet.confidence` verbatim (`HIGH`/`MEDIUM`/`LOW`/`PAPER`) | Null -> `UNKNOWN`. Read-only: never writes or influences confidence generation. |
| Market family | `market_family_calibration` / `market_family_report` | `canonicalMarketFamily` (Milestone 1's canonicalization view) | `market_family_report` additionally reports `avgEdge` and an ordinal `avgConfidenceScore` (`LOW=1`/`MEDIUM=2`/`HIGH=3`; `PAPER` and null excluded from this specific average — it's a paper-trading marker, not a real confidence judgment). |
| Thesis tag | `thesis_tag_calibration` | One row per tag in `PlacedBet.thesisTags` (array — a bet with 2 tags contributes to both tags' buckets) | 0% real coverage today (`docs/EDGELAB_PHASE2_DESIGN.md` §1.2/§9); returns `[]` against the real ledger — the honest answer, not a bug. |
| Thesis-tag co-occurrence | `thesis_tag_cooccurrence` | Unordered tag pairs appearing on the same bet | Computed over **every** placed bet regardless of settlement status (a tagging-pattern statistic, not an outcome one) — the one function in this module that isn't decided-bets-only. |
| CLV bucket | `clv_bucket_calibration` | `PlacedBet.clv`, 5-point-wide bins | Null CLV -> its own `UNKNOWN` bucket. |
| CLV sign study | `clv_sign_study` | `POSITIVE` (`clv > 0.05`) / `NEUTRAL` (`\|clv\| <= 0.05`) / `NEGATIVE` (`clv < -0.05`) / `UNKNOWN` (null) | `NEUTRAL_CLV_BAND = 0.05`, matching the fine-grained bucket's own width so "neutral" isn't an unrelated magic number. |
| Timing bucket | `timing_bucket_calibration` | How long before scheduled first pitch the bet was entered, via `lib.edgelab.checkpoints.classify_checkpoint(entryTimestamp, scheduledStart)` | Reuses the **exact same** classifier `ClvQuote.checkpoint` already uses — not a second, parallel bucketing scheme. No `scheduledStart` -> `INTERMEDIATE` (the classifier's own documented behavior). Computed in Python (see §2.1) over just the small decided-bets subset, not the raw JSONL history. |
| Recommendation path | `recommendation_path_calibration` | `RECOMMENDED_AND_BET` / `MANUAL_BET` / `MODEL_BET` / `OTHER_BET` (bet-backed) plus `RECOMMENDED_NOT_BET` / `PASSED` (recommendation-only, see §2.2) | |
| Trends | `daily_trend_report` / `weekly_trend_report` / `monthly_trend_report` / `season_to_date_report` | `entryTimestamp`'s own UTC calendar day / week (Monday-start) / month / all-time | Same bucket-row shape as every other dimension, just grouped by time instead of category. |

### 2.1 Why timing bucket is computed in Python, not SQL

`classify_checkpoint` carries real tolerance/nearest-target logic
(`lib/edgelab/checkpoints.py`) that must stay identical to what
`ClvQuote.checkpoint` already uses. Duplicating it as a second SQL `CASE`
expression would be exactly the "second, wrong migration" risk
`docs/EDGELAB_PHASE2_DESIGN.md` §9 warns about for the canonicalization
table, so `timing_bucket_calibration` fetches just the decided-bets
subset (small — 14 rows today, not the full raw JSONL history) and calls
the shared Python function directly.

One correctness subtlety this required: `PlacedBet.entryTimestamp` is
**not** reliably UTC on the wire — real committed rows carry genuine
non-UTC offsets (e.g. `-04:00` from an Eastern-time write path), while
`scheduledStart` is always written as `...Z` (UTC). DuckDB's
`read_json_auto` can also infer these two columns as different
underlying types across one glob (a string vs. a naive `TIMESTAMP`).
`lib.edgelab.calibration._to_naive_utc_datetime` normalizes both to a
timezone-naive datetime representing the same UTC instant — actually
converting a non-UTC offset, not merely stripping the offset marker,
which would have silently left the wall-clock time unconverted (a real
correctness bug, not just a type mismatch) — before handing both to
`classify_checkpoint`. The trend reports (§2) hit the same offset issue
for `DATE_TRUNC` grouping in SQL; `_ENTRY_TS_UTC_SQL` fixes it by casting
through `TIMESTAMPTZ` (which correctly interprets the offset) before
truncating.

### 2.2 Why "Recommended, Not Bet" and "Passed" don't report win rate/ROI/CLV

These two categories represent `Recommendation` rows where **no bet was
ever placed** — no stake, no side actually risked, no realized return.
Reporting a win rate for them would require guessing which side the
model favored and assuming a hypothetical stake, i.e. fabricating an
outcome that was never actually risked. That is exactly the kind of
strategy-shaped judgment this milestone is explicitly not allowed to
make ("measure historical model performance," not "tell us what we
would have made"). Instead, these two categories report `n` and the
average of what genuinely is on record at decision time
(`modelFairProbability`, `marketImpliedProbability`, `estimatedEdge`),
so they're still comparable to the bet-backed categories on model
confidence/edge — just not on a win/loss that never happened.

## 3. Surprising findings from real data

Running `scripts/edgelab/run_calibration.py` against the real committed
ledger (77 placed bets, 14 decided) surfaced one finding worth flagging
explicitly: **`expectedWinRate` is `None` in every single bucket today.**
`PlacedBet.modelFairProbability` is populated for some pending bets (58
of 77 overall) but not for any of the 14 that have actually been
settled to WIN/LOSS — meaning calibration error genuinely cannot be
computed yet for any real outcome, even though the query layer is fully
built and tested. This is a real data-completeness gap (not a query bug)
worth closing before Milestone 3's numbers can mean anything: whichever
write path settles a bet should also be carrying `modelFairProbability`
forward from the `Recommendation` it came from, if one exists.

A second, smaller gap: `PlacedBet.recommendationId` is 0% populated
across all 77 real bets, so `RECOMMENDED_AND_BET` never appears yet in
`recommendation_path_calibration`'s real-data output — every decided bet
today falls into `MANUAL_BET` or `OTHER_BET`.

## 4. Statistical limitations

- **No calibration is possible without `modelFairProbability` on settled
  bets** — see §3. Every `calibrationError` in the real report is `None`
  today as a direct consequence, not a query defect.
- **Every bucket is `INSUFFICIENT_SAMPLE`.** 14 decided bets total is far
  below the `n=20` floor for any single bucket, let alone split across
  7 dimensions. Every number in the real report is directionally
  interesting at most, never a finding.
- **Thesis tags have 0% coverage.** `thesis_tag_calibration` and
  `thesis_tag_cooccurrence` are fully implemented and tested against
  synthetic fixtures, but report empty against real data until tagging
  starts (a process gap, not something this query engine can fix — see
  `docs/EDGELAB_PHASE2_DESIGN.md` §9 risk #3).
- **The ordinal confidence score is an assumption, not a measurement.**
  `market_family_report`'s `avgConfidenceScore` encodes `LOW`/`MEDIUM`/
  `HIGH` as `1`/`2`/`3` purely to make an average computable; it is not a
  probability and should not be compared numerically to `expectedWinRate`.
- **`gameId`-based joins inherit the known doubleheader-collision gap**
  already documented in `docs/CANONICAL_SCHEMAS.md`/`docs/EDGELAB_PHASE1.md`
  — unchanged by this milestone.

## 5. Why small samples are gated

A bucket with 3 settled bets showing a 100% win rate is exactly as
likely to be luck as to be signal — reporting it without a loud,
structural `INSUFFICIENT_SAMPLE` label invites exactly the mistake this
whole milestone exists to prevent: mistaking noise for a reason to bet
more (or less) on something. The gate is a hard-coded `CASE` expression
(`calibration_status_sql`) applied identically in every dimension, not a
per-query judgment call, specifically so no future caller can quietly
skip it for one convenient query.
