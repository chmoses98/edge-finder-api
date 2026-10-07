# MLB player props: inventory, projection status and validation (2026-10-07)

Research publication of MLB player-prop projections through `edge_finder.app.v1`.
Nothing here is a betting recommendation. No prop family is eligible for betting. No prop
projection enters `model_prices` or `recommendations`.

## Publication contract (additive, backwards compatible)

Every Kalshi player-prop market in `app/latest/markets.json` (and in each event's
`event_detail`) now carries two additive fields:

- **`market.player_id`.** The contract participant id
  `ids.participant_id("MLB", "PLAYER", "mlbam_player_id", <mlbam id>)`. It is the same id
  the explorer's player profiles use. It is set when the player resolves to the slate's
  probable starter or to a confirmed-lineup batter; otherwise it is null.
- **`market.extensions.player_prop`** (`schema: "mlb.player_prop.v1"`). This carries:
  - player, team, opponent, role, family, stat label, threshold, `comparison: AT_LEAST`
    and the YES semantics;
  - `projection_status` and a human-readable `status_reason`;
  - `model_probability_yes`, which is non-null **only** for a `*_PROJECTION` status, is
    always the model's own number, and is never a Kalshi price;
  - `expected_stat` (mean / median / p10 / p90);
  - lineup status and lineup slot;
  - drivers, provenance, limitations and validation;
  - `betting_eligible: false`.

| Status | Meaning |
|---|---|
| `VERIFIED_PROJECTION` | Reserved. No family has out-of-sample evidence of beating Kalshi. |
| `RESEARCH_PROJECTION` | A model probability is published and labelled research. |
| `NO_MODEL_SUPPORT` | No projection engine exists for the family. |
| `MISSING_REQUIRED_CONTEXT` | An engine exists, but a named input is absent (no probable starter, no hitter snapshot, no slate match, under 3 prior starts). |
| `LINEUP_UNCONFIRMED` | The hitter projection needs the confirmed batting order. |
| `PLAYER_NOT_STARTING` | The announced starter or confirmed lineup excludes the player. |
| `AMBIGUOUS_MARKET` | The player, team or threshold could not be parsed. |
| `GAME_STARTED` | First pitch has passed, so the pregame projection is withdrawn. |

## Family inventory (archive 2026-08-01 → 2026-10-07; parser `lib/research/player_prop_parser.py`)

| Series | Family | Archived markets | Engine | Distribution | Status |
|---|---|---:|---|---|---|
| KXMLBKS | pitcher_strikeouts | 10,895 | `lib/research/pitcher_prop_projection.py` | Beta-binomial K given batters faced, mixed over the shared outs distribution | RESEARCH |
| KXMLBOUTS | pitcher_outs | 1,477 | same | Empirical outs residuals by expected-outs bucket, plus a shrunk postseason shift | RESEARCH |
| KXMLBHIT | hitter_hits | 43,517 | hitter engine snapshots | Monte Carlo lineup simulation (1,500 sims) | RESEARCH (confirmed lineup + snapshot) |
| KXMLBTB | hitter_total_bases | 52,199 | same | same | RESEARCH (confirmed lineup + snapshot) |
| KXMLBHRR | hitter_hrr | 64,594 | same | same (teammate-dependent) | RESEARCH (confirmed lineup + snapshot) |
| KXMLBRBI | hitter_rbi | 27,862 | same | same (teammate-dependent) | RESEARCH (confirmed lineup + snapshot) |
| KXMLBSB | hitter_stolen_bases | 11,165 | none | — | NO_MODEL_SUPPORT |
| KXMLBHR | hitter_home_runs | 66 | none validated | — | NO_MODEL_SUPPORT |
| KXMLBHA / ERA / WA | pitcher hits / earned runs / walks allowed | 65 / 58 / 24 | none | — | NO_MODEL_SUPPORT |

## Pitcher model

### Why the incumbent was replaced for research display

`lib/research/pitcher_workload_projection.py` uses one constant per-out hook probability,
`T/(T+1)`. That makes outs geometric, with sd ≈ mean, whereas real starter outs have
sd ≈ 4.3. It then prices strikeouts from `Binomial(round(E[BF]), k)` at a single
batters-faced point. Its inputs are sound; its distribution is not.

### The new model, `lib/research/pitcher_prop_projection.py`

The model builds **one** outs distribution and derives batters faced and strikeouts
from it:

1. **Expected outs.** A recency-weighted, shrunk mean of the pitcher's prior starts.
2. **Outs distribution.** The empirical residual distribution for that expected-outs
   bucket, fitted on 2022–24 starts.
3. **Batters faced given outs.** `outs + Poisson(r·outs)`, where `r` is the shrunk
   number of non-out plate appearances per out.
4. **Strikeouts given batters faced.** `BetaBinomial(BF, p_k, φ)`, where `p_k` is the
   shrunk K/BF multiplied by an opponent team K-rate factor.

The postseason leash is a single outs shift. It is estimated from this postseason's
settled starts, shrunk with `n0 = 25`, and applied only to postseason games. The current
value is −1.21 outs (raw −2.22, n = 30). Because the shift lowers outs, batters faced and
strikeouts together, the two families cannot disagree about workload.

### Fit and data

- Every constant is fitted by `scripts/research/mlb_pitcher_prop_calibration.py` on the
  **2022–2024** regular season only.
- Starter lines come from the research-cache box scores (to 2026-08-26). Statcast-derived
  lines fill 08-27 → 09-27; the reconstruction exactly matches the box scores on all
  70 overlapping starts.
- 2026 postseason starts come from settled KS/OUTS markets.

### Holdout (2025 + 2026 regular season, 9,203 pitcher-games; thresholds K 3–10, outs 12–21)

| Family | Model Brier | Incumbent Brier | Δ (95% CI, clustered by pitcher-game) | Model calibration slope / ECE | Incumbent slope / ECE |
|---|---:|---:|---|---|---|
| strikeouts | 0.1435 | 0.1752 | −0.0317 [−0.0340, −0.0296] | 1.13 / 0.019 | degenerate / 0.147 |
| outs | 0.1623 | 0.2282 | −0.0659 [−0.0681, −0.0634] | 1.03 / 0.010 | 4.47 / 0.185 |

### Real Kalshi markets

The sample is 2026 settled markets from 08-04 to 10-06 (54 dates), priced at the
latest valid pregame quote mid on identical rows.

| Family | Markets | Pitcher-games | Model | Incumbent | Kalshi | Model − Kalshi (95% CI) |
|---|---:|---:|---:|---:|---:|---|
| strikeouts | 6,610 | 941 | 0.1657 | 0.1986 | 0.1567 | +0.0091 [+0.0058, +0.0122] |
| outs | 888 | 888 | 0.2587 | 0.2622 | 0.2394 | +0.0193 [+0.0106, +0.0284] |

The last 40% of dates (from 09-13) look the same:

- K: 0.1785 vs 0.1684.
- Outs: 0.2599 vs 0.2350.

The **postseason** (from 09-29) is weaker still:

- K: 0.1741 vs 0.1671 (n = 202, 25 pitcher-games; CI includes 0).
- Outs: 0.3134 vs 0.1989 (n = 25). The shift is estimated leave-future-out, so the
  earliest playoff games got almost no leash adjustment. The market already prices short
  October hooks.

Edge buckets show no edge. When the model is at least 10 points above the market mid on
K, YES hits 22.4% against a 27.4% mid, for an ROI of −24% buying YES at the ask before
fees. On outs, ROI is −3.9% before fees.

**Verdict:** a large, real improvement over the incumbent engine and well calibrated in
holdout, but **not** better than Kalshi. It is published as `RESEARCH_PROJECTION` and is
never an edge. A calibration transform cannot fix this: the model is already calibrated
(slope ≈ 1). What it lacks is the market's information about the specific leash.

## Hitter model

The hitter path reuses the existing hitter engine unchanged:

- `hitter_feature_context` → PA outcome model → lineup game simulator → market
  distributions.
- The publication reads the newest **pregame** snapshot written at or before the export
  time from `data/edgelab/hitter_projection_snapshots/<date>.jsonl`. A later snapshot is
  never used.
- It is published only for a confirmed-lineup starter.
- Validation: MLB-RSCH-0028 found Brier 0.158 vs Kalshi 0.155, slope 0.86, hits
  over-confident, LEVEL_0_MEASUREMENT_ONLY. The re-run is in
  `docs/EDGELAB_MLB_PROP_CALIBRATION_2026_10_HITTERS.md`.

Two `gameType=R` filters made every postseason game invisible to the hitter path and to
the Statcast archive. They are removed in PR #270.

## Limitations

- No manager-specific hook, pitch-count, umpire or park strikeout adjustment.
- Opponent K tendency is team-level season-to-date. Confirmed-lineup K% is shown as
  context only, because the lineup-level version is not validated.
- No family has betting eligibility. Automatic prop settlement remains tracked in
  issue #43.
