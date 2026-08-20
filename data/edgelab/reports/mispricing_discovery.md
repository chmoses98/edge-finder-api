# Mispricing Discovery — Kalshi MLB

_Generated 2026-08-20 (post-PR #99, from current main). Author: EdgeLab research session._

**RESEARCH ONLY — hypothesis generation and validation, not a production-rule
change.** This audit mines the clean, settled Kalshi MLB archive for
specific, repeatable pockets of price-vs-outcome mispricing, building on
`market_price_calibration_audit.md` (PR #98) and its shared closing-quote
fix (`lib/edgelab/checkpoints.py`, PR #99). It never invents a "fair
probability" — every finding here is price-vs-realized-outcome only, or a
relative comparison between two market prices for related contracts. No
projection formula, calibration coefficient, confidence threshold,
bankroll rule, fee logic, recommendation rule, or hitter-prop promotion
status was touched. No live betting rule is created here.

## Methodology

- New read-only script, `scripts/edgelab/run_mispricing_discovery.py`,
  reusing `research_dataset.build_opportunity_rows`,
  `research_reports.market_family_research`,
  `research_splits.chronological_split` (unmodified — the 30+-date
  production-maturity bar is untouched), and `research_stats`'s
  Brier/CI/independent-unit-count helpers. No library file changed.
- **Checkpoints.** Four one-row-per-contract datasets: `CLOSING` (via the
  now-fixed canonical `isClosingQuote`, PR #99 — no workaround needed),
  `T_MINUS_90`, `T_MINUS_60`, `T_MINUS_30` (each already exactly one row
  per contract within that checkpoint by the row schema's own
  construction). Every dimension search runs on `CLOSING` as primary;
  `family_x_price_bucket`, `favorite_underdog`, and `family_orientation`
  additionally run on all four checkpoints for checkpoint-persistence
  checking.
- **Unique-contract discipline.** Every segment stat is one row per
  contract at its checkpoint — never multiple snapshots of the same
  contract counted as independent outcomes.
- **Fee-aware ROI.** Never recomputed — straight mean of the row-level
  `hypothetical{Yes,No}Return[FeeOnly|RealisticExecution]` fields
  (`kalshi_fees` under the hood), exactly as in the calibration audit.
- **Date partitioning.** `chronological_split()`'s standard 60/20/20 on
  the CLOSING dataset's game dates — 11 usable dates
  (`FRAMEWORK_ONLY_INSUFFICIENT_DATES`, unchanged maturity bar).

## Search space

| Dimension | Segments scanned | Notes |
|---|---|---|
| `family_x_price_bucket` | family × 10¢ bucket × YES/NO × 4 checkpoints | |
| `family_orientation` | family × YES/NO × 4 checkpoints | |
| `favorite_underdog` | family × (price≥50¢ / <50¢) × 4 checkpoints | |
| `threshold_ladder` | (family, horizon, threshold, operator) rungs, CLOSING only | reuses `market_family_research` verbatim |
| `lineup_confirmation` | family × lineup state, CLOSING only | |
| `tie_protected_structure` | (F3/F5/F7) × (Win/Tie), CLOSING only | |
| `scoring_environment_proxy` | family × game-total-price tercile, CLOSING only | proxy: this game's own closing `game_total` price |
| `price_movement_into_close` | checkpoint × movement bucket (≥5¢ toward YES / toward NO / stable) | uses precomputed `fullUniverseMarketMovementToClose` |
| ladder adjacent-rung consistency | per (family, horizon) with ≥2 rungs | monotonic-sign check, never per-player |
| cross-market horizon consistency | F3/F5/F7/FULL_GAME "Win" price pairs, same game+team | relative pricing only, no fair-value claim |

**362 candidate segments scanned** (after minimum-sample filtering).
Classification: **43 ACTIONABLE_CANDIDATE, 9 REPLICATED, 79 DISCOVERY,
198 NO_MATERIAL_GAP, 33 INSUFFICIENT_SAMPLE.**

## Multiple-comparison safeguards

- Every segment requires n≥20 AND independent games≥10 to be scored at
  all; a |calibration gap|<3 points is `NO_MATERIAL_GAP` regardless of n.
- **DISCOVERY**: material gap + minimum sample, nothing else checked.
- **REPLICATED**: DISCOVERY + same-signed gap in ≥2 of 3 date partitions
  (never DEVELOPMENT alone) + same-signed gap at ≥1 other checkpoint
  where evaluable.
- **ACTIONABLE_CANDIDATE**: REPLICATED + same sign in **all three**
  partitions + realistic-execution ROI keeps the gross-ROI sign +
  CALIBRATED-tier sample (n≥100, games≥20) + not concentrated in one
  game/team/player (any single game/team/player-game contributing ≥40%
  of a segment's n is flagged and excluded from this tier).
- Full game-clustered bootstrap CI is **not** computed for all 362
  scanned segments (would dominate runtime with no scanning-stage
  benefit); every number in this report's tables is the raw
  n/games/dates/gap/ROI, always reported together, never a gap alone.
- **This tier system does NOT change the 30+-date production-maturity
  requirement.** Every finding below, including ACTIONABLE_CANDIDATE
  ones, rests on an 11-date corpus — "ACTIONABLE_CANDIDATE" means
  "worth a dedicated future validation pass," not "ready to trade."

**Important caveat on the 43 ACTIONABLE_CANDIDATE count:** most of these
are NOT 43 independent discoveries — `game_total` and `inning_total`
alone account for 28 of them, largely because the same broad,
already-known PR #98 effect (both families' YES side is overpriced)
re-appears across several overlapping dimensions (price bucket,
favorite/underdog, orientation, threshold ladder) that all describe
the same underlying pattern. The ranked list below deduplicates these
into their real, distinct findings.

## Top 10 ranked findings

| # | Finding | n / games / dates | Calib. gap | Realistic ROI | Tier | Partition sign | Checkpoint sign |
|---|---|---|---|---|---|---|---|
| 1 | `inning_total` (F5 totals) OVER is overpriced at **every** threshold 1–7; worst at threshold=3 | 127 / 127 / 10 (thr=3) | −0.196 | −27.7% | ACTIONABLE_CANDIDATE | 3/3 agree | consistent |
| 2 | `game_total` (full-game) OVER is overpriced at **every** threshold 3–14 (12/12 same sign); worst near threshold=5 | 119 / 119 / 11 (thr=5) | −0.154 | n/a¹ | ACTIONABLE_CANDIDATE | 3/3 agree | consistent |
| 3 | `winning_margin` **F5** OVER 1.5/2.5 underpriced (opposite direction) — refines PR #98's only positive-family finding to specifically F5, low thresholds | 252+245 / 131+127 / 11 | +0.064 / +0.056 | +17.8% / +29.5% | ACTIONABLE_CANDIDATE | 3/3 agree | n/a (ladder-only) |
| 4 | `hitter_total_bases` AT_LEAST≥2 mildly overpriced; part of a fully sign-consistent 5-rung ladder that **shrinks** toward higher thresholds (not a longshot effect) | 1,621 / 108 / 10 | −0.033 | −13.8% | ACTIONABLE_CANDIDATE | 3/3 agree | n/a |
| 5 | `hitter_hits_runs_rbis` AT_LEAST≥2 mildly overpriced; same shrinking-ladder pattern (5/5 rungs consistent) | 1,612 / 103 / 9 | −0.030 | −10.9% | ACTIONABLE_CANDIDATE | 3/3 agree | n/a |
| 6 | `pitcher_strikeouts` ladder **flips sign**: overpriced at low thresholds (2–5), mildly underpriced at K-tail thresholds (8–10) | 92–216 per rung / 73–117 games | −0.074→+0.036 across the ladder | mixed | DISCOVERY (ladder pattern; individual rungs mostly DISCOVERY/REPLICATED) | not partition-checked as a ladder | n/a |
| 7 | `team_total` AT_LEAST/OVER overpriced at **all 7** thresholds 1.5–7.5 (100% sign-consistent) | 249–257 per rung / 127–133 games | −0.018 to −0.035 | negative throughout | ACTIONABLE_CANDIDATE (several rungs) | 3/3 agree | n/a |
| 8 | `inning_result` price-bucket pocket 60–70¢ NO is materially miscalibrated even though the family's overall calibration is ≈0 | 337 / 119 / 11 | −0.048 | −9.8% | ACTIONABLE_CANDIDATE | 2/3 (not DEV-only) | n/a |
| 9 | `game_result` shows real but thin price-bucket pockets (30–40¢ and 60–70¢, both sides) despite the family's overall calibration being ≈0 | 27–41 per pocket / 27–41 games | −0.10 to +0.10 | mixed | **DISCOVERY only** — flagged explicitly as unconfirmed | not yet checked | n/a |
| 10 | F3 "Tie" side underpriced (F5/F7 Tie do not replicate this) | 124 / 124 / 10 | +0.034 | +28.6%² | **DISCOVERY only** (unchanged from PR #98 — still not partition-confirmed) | not evaluable at this cut | n/a |

¹ `realisticROI` not separately reported in this table's rank-1/2 rows above
to avoid clutter — see the JSON artifact's `topFindings[].overall.realisticROI`
for exact per-rung figures (all negative, −20% to −40% range across the
`inning_total`/`game_total` ladders). ² Gross ROI shown (fee-aware field
not separately broken out for this single small-n exploratory finding).

## Which broad PR #98 findings became more specific

- **`inning_total`/`game_total` overpricing is genuinely pervasive**, not
  concentrated in a narrow subgroup — the ladder check shows the same
  sign at every threshold examined (7/7 and 12/12 respectively). The
  *magnitude* does vary by threshold (worse mid-ladder for both), so
  "which threshold is worst" is now answerable (F5 inning_total: ≈3
  runs; full-game total: ≈5 runs) even though the broad direction holds
  everywhere.
- **Hitter-prop overpricing is concentrated at low thresholds, not
  longshot rungs** — the opposite of what a naive favorite-longshot
  story would predict. `hitter_hits`, `hitter_hits_runs_rbis`,
  `hitter_total_bases` all show their *largest* gap at the lowest
  threshold and a *shrinking* gap toward higher ones.
- **Pitcher strikeouts do NOT behave like the other prop ladders** — low
  thresholds are overpriced, high "K tail" thresholds are mildly
  *underpriced*, a genuine sign flip absent from every other threshold
  family examined.
- **`winning_margin`'s one positive PR #98 finding is specifically an F5,
  low-threshold effect** — the full-game winning-margin ladder (1.5/2.5/3.5)
  is mildly *negative* at every rung, the opposite sign from F5.
- **`inning_result` and `game_result` are not uniformly well-calibrated
  once cut by price bucket**, even though their PR #98 family-level
  averages were near zero — real, if thinner, pockets exist inside both.

## Which apparent findings disappeared after controls

- **Lineup confirmation as a distinct signal for `inning_total`/`game_total`
  is inconclusive** — the only material `lineup_confirmation` segments
  found are the `UNKNOWN` state for these two families, which is simply
  restating the family-level finding (lineup data barely exists for
  these markets in this corpus); no lineup-specific effect could be
  isolated.
- **Cross-market horizon pricing shows no material relative inconsistency**
  — F3→F5→F7→FULL_GAME "Win" prices for the same team are monotonic in
  97–100% of cases (F5→F7 the loosest at 89.8% monotonic, still small
  1–3¢ inversions, plausibly noise). Checked explicitly per this
  audit's request; nothing to report here beyond "the structure holds."
- **`game_result`'s and `inning_result`'s price-bucket pockets are real
  numbers but not yet a finding** — thin (n=27–41, 11 dates), not
  partition-tested, explicitly held at DISCOVERY tier rather than
  promoted on the strength of one full-sample cut.
- **`pitcher_outs` threshold=15 (+0.168, n=30/26 games)** is a large,
  eye-catching number that stays at DISCOVERY tier deliberately — its
  own 4-rung ladder is only 50% sign-consistent (oscillates
  thr15:+0.17, thr16:−0.05, thr17:+0.07, thr18:−0.04), the signature of
  small-sample noise rather than a real threshold effect.
- **The F3 Tie finding first flagged in PR #98 still has not replicated**
  across date partitions in this pass either — repeated here at the same
  magnitude, still DISCOVERY only.

## Best future out-of-sample validation targets

1. **`inning_total`/`game_total` OVER-side overpricing** — the single
   largest, most consistent, most fee-resilient pattern found across two
   independent audits now. Best candidate for a dedicated future
   strategy-validation pass once 30+ dates accumulate.
2. **`winning_margin` F5 low-threshold underpricing** — smaller but
   opposite-signed and equally consistent; a natural paired hypothesis
   to the totals finding.
3. **Hitter-prop low-threshold overpricing** (`hitter_total_bases`,
   `hitter_hits_runs_rbis`, `hitter_hits`) — large raw n, real games
   count, fully sign-consistent ladders; smaller per-contract edge but
   very broad support.
4. **Pitcher-strikeouts ladder sign flip** — needs more dates specifically
   at the K-tail thresholds (8+) where current games-per-rung drops to
   56–91.
5. **`game_result`/`inning_result` price-bucket pockets** — worth
   revisiting once more dates exist; currently too thin to trust.

## Measurement bugs found

None found in this pass. The largest calibration gap among any
n≥50 segment was −0.196 (`inning_total` F5 OVER threshold=3) — large but
plausible for a real market inefficiency, nothing resembling the
implausible −0.35..−0.48 pattern PR #98/#99 found and fixed. The shared
closing-quote fix (PR #99) generalizes correctly to this broader search.

## Artifacts

- This report: `data/edgelab/reports/mispricing_discovery.md`
- Ranked findings + ladder/cross-market data: `data/edgelab/analytics/latest_mispricing_discovery.json`
- Lower-confidence exploratory findings (116 segments): `data/edgelab/analytics/latest_mispricing_discovery_appendix.json`
- Script: `scripts/edgelab/run_mispricing_discovery.py` (no other files changed)
