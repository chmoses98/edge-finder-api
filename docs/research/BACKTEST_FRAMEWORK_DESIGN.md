# BACKTEST_FRAMEWORK_DESIGN.md

Model Performance Phase 1 (Market Audit) — Part 8.

## Historical data availability (independently checked, not assumed)

| Data type | Available? | Where | Notes |
|---|---|---|---|
| Game projections (full-game) | Partial | Embedded in `data/slates/<date>/` archives and `data/slate.json` history is NOT separately versioned per run — only the most recent `data/slate.json` state is ever live; historical PER-RUN slate state exists only via `data/slates/<date>/official_*.json`/`recheck_*.json`/`authoritative.json` (confirmed present for ~50+ dates, June-July 2026). | Usable for a walk-forward backtest, but only from the point this archival began. |
| F5 projections | Partial | Same archive, if `f5AwayProj`/`f5HomeProj` fields were populated on those historical runs — not independently verified this phase whether every archived date has them. | Needs a dedicated audit pass before backtest use. |
| F3/F7 projections | **None captured by this repository** | No F3/F7 field or fetch path exists in this repository's slate/enrichment pipeline. **CORRECTED**: F3/F7 markets themselves are confirmed to exist on Kalshi (user-confirmed; see `docs/research/KALSHI_MARKET_TAXONOMY.md`'s "F3/F7 correction" section) — the absence here is a repository ingestion gap, not evidence the markets don't exist. | No backtest possible until this repository ingests F3/F7 market/price data going forward; no retroactive fix is possible for the period already elapsed without ingestion (see the gaps list below). |
| Market prices (mid/last) | Yes | `data/kalshi_registry_snapshots/*.json`, ~250 files, 2026-06-08 through 2026-07-29. | Good historical price coverage for the markets that ARE snapshotted. |
| Executable prices | Partial | `executablePriceUsed`/`executablePriceAtOutput` fields exist on `marketLedger` rows in archived slates, but only for markets `build_market_ledger.py` currently evaluates (the 8 `REQUIRED_MARKETS`). | F5 Tie and every unsupported family have NO historical executable-price record at all, by construction — a backtest of those markets could only start from whenever they're first evaluated in production. |
| Lineups | Yes | Captured in archived slate snapshots. | |
| Starting pitchers | Yes | Captured in archived slate snapshots. | |
| Pitch counts | Partial | `avgIPperStart` present; explicit pitch-count history not separately confirmed. | |
| Bullpen status | Partial | `bullpen.fatigued`/`last3DaysIP` fields present in enriched slates; historical depth not independently confirmed beyond the archive window. | |
| Weather | Unconfirmed | `api/weather.js` exists; historical archival of weather data was not independently confirmed this phase. | |
| Park | Yes | `park.parkFactor`/`park.dome` present in every archived slate. | |
| Umpire | **None** | No umpire data source found anywhere in the repository. | |
| Settlement | Yes | `bets.json` records `result`/`pnl` post-settlement. | |
| CLV | Partial | `data/clv_snapshots/<date>/` exists for dates `write_tracked_tickers.py` ran; coverage gaps likely (per `docs/SOURCE_OF_TRUTH_MAP.md`'s CLV section). | |
| Accepted recommendations | Yes | `bets.json` (real, placed real-money bets) + `data/pipeline/<date>/execution.json` (Phase 7+ dates only, additive). | |
| Rejected recommendations | Partial | Only visible in archived `marketLedger` rows with `status: "Rejected"`, not separately indexed anywhere. | |
| Unsupported markets | **None historically** | Since production has never evaluated them, there is by definition no historical record of how an unsupported market's real outcome compared to anything — this is exactly why Wave 1's "no silent drop" ledger visibility matters: it starts building this record going forward. | |
| Actual bets | Yes | `bets.json`, 509 confirmed entries (per `docs/SOURCE_OF_TRUTH_MAP.md`). | |
| Closing prices | Partial | `capture_closing_lines.py`/CLV modules exist; coverage depth not independently re-verified this phase. | |

## Gaps preventing an honest out-of-sample comparison today

1. **No F3/F7 historical data exists in this repository, and none can
   be retroactively recovered for the period already elapsed** —
   ingestion never happened, so no historical record was ever
   captured. **CORRECTED**: this is a repository ingestion gap to
   close going forward (F3/F7 markets themselves are confirmed to
   exist on Kalshi; see `docs/research/KALSHI_MARKET_TAXONOMY.md`'s
   "F3/F7 correction" section), not an immutable fact about market
   nonexistence. A future phase that adds F3/F7 ingestion begins
   building a usable historical record from that point forward, the
   same way this document already describes for F5 Tie (item 2 below).
2. **F5 Tie has zero historical executable-price-vs-outcome record**
   — production has never priced it. A backtest of the Wave 1 F5-Tie
   fix must start from the day it is first evaluated (paper mode),
   not retroactively.
3. **Archived slate history depth is short** (~2 months as of this
   phase) — likely insufficient for a stable negative-binomial/
   bivariate-Poisson parameter fit (Wave 2 candidates) without a full
   season or more.
4. **Unsupported-market outcomes have no historical record by
   construction** (see table) — this is the single biggest reason
   Wave 1's ledger-visibility work has value independent of any
   projection improvement: it is the ONLY way future waves get
   evaluable historical data for currently-unsupported families.
5. **No independently confirmed weather/umpire historical archive** —
   candidates 15/16 in the roadmap cannot be backtested until this is
   resolved.

## Rolling backtest framework design (not implemented this phase — design only)

```
for each historical date D (walk-forward, oldest to newest):
    train/fit on data strictly BEFORE D
    generate the candidate model's projection AS IF running on date D,
        using ONLY data that was actually available before D's slate closed
        (no lineup/weather/pitcher data revealed after D's games started)
    compare against:
        - the executable price that was actually available before D's close
        - the actual settled outcome (Away/Tie/Home, total, etc.)
    record:
        - calibration bucket membership
        - Brier score contribution (binary markets)
        - multiclass Brier score contribution (Away/Tie/Home)
        - log loss / multiclass log loss contribution
        - ROI if a bet would have been placed under current sizing rules
          (SECONDARY metric only -- never the primary selection criterion)
        - CLV vs. the closing price, where available
    advance D by one day; repeat
```

Required properties (explicit, not assumed):
- **Train/validation/test date separation**: a candidate model fitted
  using data through date D-1 must never be evaluated against date D
  using any parameter that was influenced by date D or later.
- **No future-data leakage**: lineup confirmation, weather, and
  in-game states must be time-sliced to "what was known at slate-lock
  time," not "what is known now."
- **Executable-price evaluation**: edges must be computed against the
  executable price a real order could have hit, not the mid-price.
- **Settlement-rule-aware scoring**: an F5 backtest must settle on the
  inning-5 score, a full-game backtest on the extra-inning-inclusive
  final score — using the WRONG horizon's actual result to score a
  projection would silently corrupt every metric below it.
- **Calibration curves, Brier score, multiclass Brier (Away/Tie/Home),
  log loss, multiclass log loss** — computed per the standard
  definitions, segmented by:
  - market family,
  - probability bucket,
  - price bucket,
  - lineup status (confirmed vs. unconfirmed at evaluation time),
  - model version,
  - season,
  - horizon (full game vs. F5),
  - favorite/underdog/tie outcome,
  - liquidity bucket (volume/open-interest tier).
- **ROI is a secondary metric only** — the mission is explicit that
  model selection must not be based on ROI alone; Brier/log-loss/
  calibration are the primary bar.
- **Calibration must be fitted and evaluated on DIFFERENT samples** —
  a walk-forward split inherently satisfies this (each date's
  evaluation uses only prior-date fitting), but this must be enforced
  in the implementation, not merely assumed by the walk-forward
  structure's shape.

## Minimum historical sample size before activation (recommendation, not a hard rule until validated)

| Activation stage | Recommended minimum sample (per market family) | Rationale |
|---|---|---|
| Paper activation | ~1 full season (or ~1,000+ resolved market instances) with walk-forward calibration curves showing no material miscalibration in any probability bucket | Matches the general sports-analytics convention that team-level Poisson-style models need a full season to average out schedule/weather/roster variance; this phase does not have independently-verified MLB-specific literature to cite a harder number. |
| Limited real-money activation | The above, PLUS at least one additional season (or equivalent volume) of PAPER performance showing calibration and CLV stability, with confidence intervals on Brier/log-loss narrow enough to distinguish the candidate from the current production method at a pre-registered significance threshold | Prevents activating on a single lucky/unlucky season. |
| Normal (full) real-money activation | The above, PLUS successful limited real-money activation across at least one full season with no material CLV or calibration degradation vs. the paper-trading period | Confirms paper performance transfers to real execution (price impact, fill quality, timing). |

This table is a Phase 1 design recommendation, not itself a
backtested, validated number — a future phase's actual backtest
results should supersede it once real walk-forward data exists.
