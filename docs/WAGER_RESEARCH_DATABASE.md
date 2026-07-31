# Wager Research Database

## Purpose

A canonical, one-row-per-wager research database built from the root
`bets.json` ledger — the single source of truth for real-money and manual
wagers in this repository. **`data/bets.json` (92 stale legacy rows) is
never used** as a source; only root `bets.json` (512 rows as of this
writing) is read.

## Build

```
python3 scripts/build_wager_research_db.py
python3 scripts/generate_wager_research_report.py [YYYY-MM-DD]
```

`build_wager_research_db.py` is deterministic: unchanged input produces a
byte-identical `wagers.jsonl`/`wagers.csv` (rows sorted by `(date, betId)`).
It never fabricates a value — every field the source bet doesn't have is
`null`, never zero or a guess. It never drops a wager, including manual
bets with no model recommendation, model bets with no settled result,
bets with no CLV ever captured, and legacy wagers whose original schema
predates ticker-level identity.

## Outputs

| File | Contents |
|---|---|
| `data/research/wagers.jsonl` | One canonical row per wager, JSON Lines |
| `data/research/wagers.csv` | Same rows, flat CSV (list/dict fields JSON-encoded as strings) |
| `data/research/schema.json` | The canonical field list |
| `data/research/build_report.json` | Row counts, join-method counts, data-quality counts |
| `data/research/calibration_bins.json` | Probability-bin calibration table |
| `data/research/reports/summary.json` / `.md` | All-time / 7-day / 30-day / season aggregates + breakdowns |
| `data/research/reports/daily/<date>.json` / `.md` | Per-day report |

## Identity resolution (join priority)

1. **Exact ticker** (`marketTicker` or `ticker` on the source bet).
2. **`gameId` + marketFamily + period + side + line** — used when no
   ticker was ever stored but the bet has enough structured fields to
   identify the exact market.
3. **Legacy fallback**: `date + game string + market string + side`,
   tagged `joinMethod: "legacy_fallback"` and flagged
   `NO_EXACT_OR_GAME_IDENTITY` in `dataQualityFlags` — used for pre-Kalshi
   and early-era bets that predate ticker-level logging. Still a unique,
   stable key; never dropped.

## Financial calculations

- **Push/Void**: `grossReturn = stake`, `netProfit = 0`.
- **Pending**: `grossReturn`/`netProfit`/`roiPct` are all `null`.
- **Win/Loss**: `netProfit` is read directly from the bet's own stored
  `pl` (or `pnl`) field when present. A `LOSS` with no stored `pl` falls
  back to `-stake` (the only case where the downside is unambiguous
  without a stored value). A `WIN` with no stored `pl` is left `null`
  rather than recomputed from American odds, since the actual fill price
  can differ from the theoretical payout.
- **The stored `result`/`status` fields are always trusted as-is.** This
  script never re-derives WIN/LOSS from a game score — "official result"
  in `bets.json` is authoritative.

## Market-family taxonomy

Legacy `market` strings (`"ML"`, `"F5 ML"`, `"Run Line"`, `"Total"`,
`"Team Total"`, `"NRFI"`, `"YRFI"`, and their `REQUIRED_MARKETS`-era
variants like `"ML_Away"`/`"F5_ML_Home"`/`"TT_Away_Over"`) are mapped onto
the same `marketFamily`/`period` vocabulary
`lib.kalshi_mlb_market_classifier` uses for newly-discovered Kalshi
contracts, so historical and newly-discovered markets share one taxonomy
in every report. Manual sportsbook player-prop bets (`"K Prop"`,
`"Pitcher Prop"`) are deliberately left unmapped — they are not Kalshi
contracts and have no `marketFamily`.

## Reports

Every aggregate reports its own `sampleSize` (and, where relevant,
`settledSampleSize`) directly alongside every metric — never present a
ROI/CLV number without the N it's based on. Windows: daily, last 7
**settled betting days** (not calendar days — a day with no settled
action doesn't count), last 30 settled betting days, current season
(March 1 of the latest wager's year onward), all time. Breakdowns: market
family, line type (alternate vs. primary/no-line), period, source,
confidence tier, favorite/underdog, lineup-confirmed vs. unconfirmed,
model-support status.

## Calibration bins

Computed only for **settled, binary-outcome** wagers (`WIN`/`LOSS`) in a
recognized binary market family, with a valid stored model probability.
Pushes, voids, pending bets, and non-binary/unrecognized markets are
excluded. This module never modifies model calibration — it only reports
what the existing calibration actually produced, including when it is
poorly calibrated.

## Data quality

Every row gets a `dataQualityStatus` (`CLEAN`/`DEGRADED`/`POOR`) and a
`dataQualityFlags` list (e.g. `NO_EXACT_OR_GAME_IDENTITY`,
`UNRECOGNIZED_MARKET_STRING`, `MISSING_STAKE`, `MISSING_ENTRY_PRICE`) so
a consumer can immediately see which rows are lower-confidence without
guessing.

## Tracking-type separation (spread-correction mission)

Every row also carries `trackingType` (`REAL`/`MANUAL`/`PAPER`),
`countsTowardBankroll`, `hypotheticalStake`, `hypotheticalNetProfit`,
`hypotheticalRoiPct`, and `realMoneyBlockReasons`. `PAPER` rows come
from `data/research/paper_spread_ledger.jsonl` (built by
`scripts/build_paper_spread_ledger.py` from Rule-81/not-yet-activated
spread contracts) and always have `stake`/`netProfit`/`roiPct` = `null`
-- their performance lives only in `hypotheticalNetProfit`/
`hypotheticalRoiPct`, so it can never be blended into real bankroll
math. `scripts/generate_wager_research_report.py`'s summary/daily
reports compute `allTime`/`last7`/`last30`/`currentSeason` from
`REAL`/`MANUAL` rows only, and report paper spread performance
separately under `paperSpreadPerformance`. See
`docs/SPREAD_ANALYSIS_AND_ACTIVATION_POLICY.md` for the full design.
