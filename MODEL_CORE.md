# MODEL_CORE.md
# Last updated: June 1, 2026 — v2.2

---

## SECTION 0: RULE HIERARCHY

Rules and gates are tiered. Higher tiers always override lower tiers.

### Tier 1 — Hard Gates (auto-block, no override)
These are absolute. A bet cannot be logged at Medium or High confidence if any Tier 1 gate fails.
- Top-5 offense (R/G ≥5.2 season or ≥5.5 rolling 15) on an Under — Rule 27/30
- Opener on either side + Under on game total — Rule 31
- NRFI when total ≥8.0 (unless dual sub-3.00 1st-inning xERA confirmed) — Rule 34
- F5 price unconfirmed → Paper only — Rule 42
- TT line unconfirmed → Paper only — Rule 44
- Starter unconfirmed → Paper only for all props/F5 — Rule 11

### Tier 2 — Soft Gates (each failed gate downgrades size one tier; two failures = block at Medium)
- Prior-day 7+ run offense — Rule 35
- ML within 15 cents of pick'em (extra-inning inflation) — Rule 22
- ML/F5 already logged with implied score near Under line — Rule 32
- Bounceback flag active on team in the Under thesis — Section 9
- Kalshi divergence >15% with no investigated explanation — Rule 37
- Same-game thesis conflict: ML thesis implies 4-5 runs and total ≤8.0 — Rule 32
- Missing Layer 2 stress test on Total/TT bets — Rule 64
- Missing streak weight written analysis — Rule 68
- Model% diverges >8% from Pinnacle VF AND Kalshi also aligns with Pinnacle (both sharp markets disagree with model) — Rule 71
- Edge >12% at near-even ML price (pick'em ±20 cents) — Rule 70
- Bullpen threw 5+ IP last 2 days (fatigue flag) — Rule 66
- Starter has thin sample (<4 GS this season) — Rule 70

### Tier 3 — Sizing Scalars (affect size, not log/no-log)
- Model vs Pinnacle VF gap >10% AND Kalshi aligns with Pinnacle → reduce size one tier — Rule 28 (both sharp markets disagree with model)
- Streak weight >0.2 in factors{} → cap at Medium regardless of edge — Rule 41
- High-walk pitcher (BB/9 >3.5) on K prop → reduce to Paper — Rule 19

---

## SECTION 1: PROBABILITY ENGINE

The goal: generate **true probability from first principles**, then compare to market. Never start from the market price.

### Step 1 — Starter True Talent Estimate

Primary metric: **xFIP** (removes defense and BABIP variance — most predictive of future run prevention).
Secondary context: **xERA** (for divergence flagging only — never the basis for edge calculation).

**True Talent xFIP Formula:**
```
true_xFIP = (N_recent × recent_xFIP + M_season × season_xFIP) / (N_recent + M_season)
```

**Regression weights by pitcher type and season depth:**

| Pitcher Type | Early Season (<10 GS) | Mid Season (10–20 GS) | Late Season (20+ GS) |
|---|---|---|---|
| Established starter (3+ full seasons) | N=1, M=5 | N=1, M=3 | N=1, M=3 |
| Younger starter (<3 full seasons) | N=2, M=3 | N=2, M=3 | N=1, M=3 |
| IL returner (first 3 starts back) | N=2, M=2 | N=2, M=2 | N=2, M=2 |
| Streak divergence (recent 3 starts ±1.5 xFIP from season) | N=3, M=2 | N=3, M=2 | N=2, M=2 |

"Recent" = last 5 starts, each weighted equally. Season reference = prior full season + current season blended by IP ratio.

**Why season depth matters:** xFIP stabilizes at ~300 batters faced (~8–10 starts). Before that, season xFIP is itself a small-sample estimate — weight toward career/prior-year more heavily early in the season.

**Additional scalar adjustments to true_xFIP:**
- **xFIP vs xERA divergence**: if xFIP > xERA by 0.5+, starter is outperforming true talent → fade signal (regress toward xFIP). If xFIP < xERA by 0.5+, underperforming → buy signal. Log both values.
- **Velocity flag**: recent velocity 1+ mph below season average → add 0.3 to true_xFIP. Skip if data unavailable.
- **Handedness adjustment**: see Step 3.
- **Times-Through-Order (TTO) penalty**: pitchers with documented TTO splits (xFIP difference 3rd TTO vs 1st TTO >0.8) get +0.2 added to starter_scalar in F5 context for starts where they are expected to reach 5+ IP. This affects F5 projection accuracy. Check `pitcher.ttoSplit` in slate data or Baseball Savant.

**xFIP Tier Reference:**

| xFIP | Tier | Dampened Scalar |
|---|---|---|
| ≤2.50 | Historic ace | 0.69 |
| 2.51–3.00 | Elite | 0.69–0.77 |
| 3.01–3.50 | Above average | 0.77–0.84 |
| 3.51–4.00 | Average | 0.84–0.92 |
| 4.01–4.50 | Below average | 0.92–1.00 |
| 4.51–5.00 | Vulnerable | 1.00–1.08 |
| 5.01–5.50 | Poor | 1.08–1.16 |
| 5.51+ | Replacement-level | 1.16+ |

---

### Step 2 — Offense Baseline

Primary goal: establish what this specific offense is expected to score per game — directly, without anchoring to league average.

**Offense Baseline Formula:**
```
offense_baseline = (rolling_15_R/G × 0.55) + (season_R/G × 0.45)
```

**Data source:** `teamstats.json` → `last15RpG` and `runsPerGame`.

**Weight rationale:**
- Rolling 15-game R/G (55%) — what the offense has actually been doing recently
- Season R/G (45%) — full-season baseline; regresses extreme hot/cold streaks

**Data limitation note:** The `rpgIndex` field in teamstats.json (formerly mislabeled `wrcPlus`) is simply season R/G normalized to 100 at the 4.5 league average. It is NOT park-adjusted wRC+. Do not treat it as a quality metric independent of R/G — it is the same data. Use `runsPerGame` directly for the season R/G component above.

**Bounceback/Regression Override (from Section 9):**
- If rolling 15-game R/G is >0.5 below season R/G → weight season R/G at 0.60, rolling at 0.40 (bounceback spot)
- If rolling 15-game R/G is >0.5 above season R/G → weight season R/G at 0.60, rolling at 0.40 (regression spot)
- Log which condition applies in the analysis output

**Lineup Adjustment (apply daily):**
```
lineup_adj = (today_confirmed_lineup_avg_OPS − season_team_OPS) × 2.0
offense_baseline_adj = offense_baseline + lineup_adj
```

- If lineup is confirmed: apply the adjustment. Cap at ±0.3 R/G.
- If lineup not yet confirmed: use season baseline, no adjustment, note it. TT bets must be Paper only [T1].
- Missing cleanup hitter (projected OPS >.900): subtract ~0.05 × offense_baseline
- Missing leadoff or top-2 hitter (projected OPS >.800): subtract ~0.03 × offense_baseline

**Lineup Timing Rule:** If it is past 3 hours before first pitch and lineup is still unconfirmed, flag as potential injury/roster hold — not just routine delay. Do not assume standard lineup.

---

### Step 3 — Handedness Matchup Adjustment

Required before logging K props. Applied as a direct adjustment to true_xFIP.

```
handedness_scalar = (pct_LHH × pitcher_K%_vs_L + pct_RHH × pitcher_K%_vs_R) / pitcher_overall_K%
```

- RHP vs LHH-heavy lineup (>60% LHH) with platoon disadvantage → add +0.15 to true_xFIP
- LHP vs RHH-heavy lineup → same adjustment
- No split data available (sample <50 PA) → skip adjustment, note "no split data — no adjustment applied"
- Do not guess at handedness adjustment. Missing data = no adjustment.

---

### Step 4 — Pitcher Type Classification

Classify each starter into one of three types using Savant data. This determines which offensive counter-metric to pull and informs matchup quality beyond handedness.

| Pitcher Type | Definition | Key Offensive Counter-Metric |
|---|---|---|
| **Power** | K% >25% OR primary pitch FB/SL >50% usage | Team K% vs. pitcher handedness (last 14d) |
| **Groundball** | GB% >50% | Team hard-hit rate and BABIP profile |
| **Command/Mix** | BB% <6%, 3+ pitch types each >15% usage | Team O-Swing% and chase rate |

- Pull the relevant counter-metric from Baseball Savant for the opposing lineup
- A poor matchup for the offense (e.g., high-K% team vs. Power pitcher) → add +0.10 to true_xFIP
- A favorable matchup (e.g., low chase rate team vs. Command pitcher) → subtract 0.10 from true_xFIP
- If data unavailable: note "pitcher type unclassified — no counter-metric applied"
- Log `pitcherType` in bet record

---

### Step 5 — Bullpen (Innings-Weighted)

The bullpen's impact is proportional to how many innings it actually pitches — not a flat multiplier on the full game.

**Expected innings split:**
```
starter_IP = pitcher's avg IP/start (from slate data)
bullpen_IP = 9 − starter_IP
```

**Runs allowed per inning (directly from xFIP):**
```
starter_R_per_inning = true_xFIP / 9
bullpen_R_per_inning = bullpen_xFIP / 9
```

Apply **workload/fatigue flag** before using bullpen_xFIP: if bullpen threw 15+ IP in last 3 days → add 0.40 to bullpen_xFIP before calculating.

See Section 16 for full bullpen tier reference.

**F5 context:** Bullpen is NOT included in F5 projections. Starter innings govern everything in F5 context.

---

### Step 6a — Run Projection Formula

**No league average anchor. Each team projected directly from their own offense against the opposing pitching.**

```
offense_matchup_factor = offense_baseline_adj / 4.5

projected_runs =
  offense_matchup_factor × [
    (starter_IP × starter_R_per_inning)
  + (bullpen_IP × bullpen_R_per_inning)
  ]
+ park_adj
```

**What this means simply:** Take how many runs the pitcher(s) would allow against an average team, then scale that up or down based on how much better or worse this offense is than average. A team scoring 5.4 R/G has a factor of 1.20 — they'll score 20% more than an average offense would against the same pitcher.

**Calculate for both teams independently:**
- `away_proj` = away offense vs. home starter + home bullpen
- `home_proj` = home offense vs. away starter + away bullpen
- `total_proj` = away_proj + home_proj

**Show all math explicitly in every game analysis block:**
```
AWAY offense_baseline: X.X R/G (15-game: X.X | season: X.X | blend: X.X)
AWAY lineup_adj: applied / not confirmed
AWAY offense_matchup_factor: X.XX
HOME starter: true_xFIP X.XX → X.XX R/inn × X.X IP = X.XX runs
HOME bullpen: xFIP X.XX → X.XX R/inn × X.X IP = X.XX runs
AWAY park_adj: +/− X.X
AWAY proj: X.X runs

HOME offense_baseline: X.X R/G (15-game: X.X | season: X.X | blend: X.X)
HOME lineup_adj: applied / not confirmed
HOME offense_matchup_factor: X.XX
AWAY starter: true_xFIP X.XX → X.XX R/inn × X.X IP = X.XX runs
AWAY bullpen: xFIP X.XX → X.XX R/inn × X.X IP = X.XX runs
HOME park_adj: +/− X.X
HOME proj: X.X runs

TOTAL proj: X.X
```

**These numbers are mandatory in every game analysis block.**

---

### Step 6b — Poisson Probability Conversion

**Compute Poisson directly — do not use the lookup table as primary.** The table is a sanity check only. Use the formula:

```python
# Win probability via Poisson
import math

def poisson_pmf(k, lam):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def p_team_wins(team_proj, opp_proj, max_runs=20):
    p_win = 0
    p_push = 0
    for team_runs in range(0, max_runs+1):
        for opp_runs in range(0, max_runs+1):
            p = poisson_pmf(team_runs, team_proj) * poisson_pmf(opp_runs, opp_proj)
            if team_runs > opp_runs:
                p_win += p
            elif team_runs == opp_runs:
                p_push += p
    return p_win, p_push

def p_over(total_proj, line, max_runs=30):
    p = 0
    for runs in range(int(line)+1, max_runs+1):
        # Sum Poisson for combined total — approximate as single Poisson(total_proj)
        p += poisson_pmf(runs, total_proj)
    return p
```

Run this via bash_tool for any projection not obviously handled by interpolation. For quick estimates, use the reference table below — but flag any case where the projection falls between rows and the edge is within 1% of a tier threshold.

**Win Probability Reference Table (sanity check only):**

| Away Proj | Home Proj | P(Away Wins) | P(Home Wins) | P(Push) |
|---|---|---|---|---|
| 6.0 | 1.8 | 91.5% | 3.9% | 4.7% |
| 5.5 | 2.0 | 86.9% | 6.5% | 6.6% |
| 5.2 | 2.4 | 80.0% | 11.0% | 9.0% |
| 5.0 | 2.7 | 74.2% | 15.2% | 10.5% |
| 4.8 | 3.0 | 67.9% | 20.2% | 11.9% |
| 4.6 | 3.2 | 62.5% | 24.6% | 12.9% |
| 4.4 | 3.4 | 56.9% | 29.4% | 13.7% |
| 4.2 | 3.6 | 51.3% | 34.5% | 14.2% |
| 4.0 | 3.8 | 45.6% | 39.9% | 14.5% |
| 3.9 | 4.0 | 41.4% | 44.2% | 14.4% |
| 4.5 | 4.5 | 43.3% | 43.3% | 13.5% |
| 3.6 | 4.2 | 34.5% | 51.3% | 14.2% |
| 3.2 | 4.8 | 22.6% | 65.0% | 12.3% |
| 2.4 | 5.2 | 11.0% | 80.0% | 9.0% |

**Note on Push:** Poisson gives P(tie after 9) ≈ 12–14%. Effective win probability excluding push: `P(team wins | not push) = P(team wins) / (1 − P(push))`. Use this for ML edge calculation.

**Total Probability Reference Table:**

| Proj Total | P(O 6.5) | P(O 7.5) | P(O 8.5) | P(O 9.5) |
|---|---|---|---|---|
| 6.0 | 39.4% | 25.6% | 15.3% | 8.4% |
| 6.5 | 47.3% | 32.7% | 20.8% | 12.3% |
| 7.0 | 55.0% | 40.1% | 27.1% | 17.0% |
| 7.5 | 62.2% | 47.5% | 33.8% | 22.4% |
| 8.0 | 68.7% | 54.7% | 40.7% | 28.3% |
| 8.5 | 74.4% | 61.4% | 47.7% | 34.7% |
| 9.0 | 79.3% | 67.6% | 54.4% | 41.3% |
| 9.5 | 83.5% | 73.1% | 60.8% | 47.8% |
| 10.0 | 87.0% | 78.0% | 66.7% | 54.2% |
| 10.5 | 89.8% | 82.1% | 72.1% | 60.3% |
| 11.0 | 92.1% | 85.7% | 76.8% | 65.9% |

**Team Total Reference Table:**

| Proj Runs | P(O 2.5) | P(O 3.5) | P(O 4.5) | P(O 5.5) |
|---|---|---|---|---|
| 2.0 | 32.3% | 14.3% | 5.3% | 1.7% |
| 2.5 | 45.6% | 24.2% | 10.9% | 4.2% |
| 3.0 | 57.7% | 35.3% | 18.5% | 8.4% |
| 3.5 | 67.9% | 46.3% | 27.5% | 14.2% |
| 4.0 | 76.2% | 56.7% | 37.1% | 21.5% |
| 4.5 | 82.6% | 65.8% | 46.8% | 29.7% |
| 5.0 | 87.5% | 73.5% | 56.0% | 38.4% |
| 5.5 | 91.2% | 79.8% | 64.2% | 47.1% |
| 6.0 | 93.8% | 84.9% | 71.5% | 55.4% |
| 6.5 | 95.7% | 88.8% | 77.6% | 63.1% |

---

### Step 7 — F5 Probability

F5 covers only the first 5 innings — starter only, no bullpen. Build the F5 projection directly from the starter's numbers and offense baseline, not by scaling the full-game projection.

```
starter_durability = min(avg_IP_per_start / 5.0, 1.0)
effective_starter_IP_f5 = min(starter_IP, 5.0) × starter_durability × tto_adj

away_f5_proj = offense_matchup_factor_away × (effective_starter_IP_f5_home × home_starter_R_per_inning) + park_adj × (5/9)
home_f5_proj = offense_matchup_factor_home × (effective_starter_IP_f5_away × away_starter_R_per_inning) + park_adj × (5/9)
```

**Why this is better than scaling full-game proj:** The old formula (away_proj × 5/8.5) imported bullpen noise into the F5 number. This version builds F5 purely from starter performance and offense — which is what F5 actually measures.

`starter_durability = min(avg_IP_per_start / 5.0, 1.0)`

**Times-Through-Order adjustment for F5:**
```
tto_adj = 1.0 − (tto_split × 0.15)
```
Where `tto_split` = xFIP difference in xFIP points between 3rd TTO and 1st TTO (from Savant). Apply only if tto_split > 0.50 xFIP points and starter is expected to pitch into inning 5. A split of 0.50+ represents a meaningful performance degradation late in starts. If no TTO data: tto_adj = 1.0.

- Starter averaging 6.0+ IP → durability = 1.0 (full F5 window)
- Starter averaging 4.5 IP → durability = 0.90
- Opener (<3 IP avg) → F5 is UNQUALIFIED per Rule 24

Then apply Poisson to f5 projections → F5 win probability.

**Bullpen is excluded from F5 projection.** Do not apply bullpen_scalar to F5 runs.

---

### Step 8 — Run Line Cover Probability

RL cover requires winning by 2+ runs.

```
P(cover -1.5) = P(team_proj − opp_proj ≥ 2)
```

Approximate lookup:
- Projected margin ≥ 2.0 runs: P(cover) ≈ 45–55%
- Projected margin ≥ 3.0 runs: P(cover) ≈ 55–65%
- Projected margin ≥ 4.0 runs: P(cover) ≈ 65–72%

RL at plus money (+120 or better): log if P(cover) > 45%.
RL at minus money: require P(cover) > 52%.

---

## SECTION 2: EDGE CALCULATION

Edge is only meaningful if probability in Section 1 was built correctly.

```
raw_gap    = true_prob − kalshi_implied_prob          ← NEVER log this as edgePct
edge       = raw_gap × calibration_factor             ← THIS is edgePct in bets.json
```

**Step-by-step — do not skip any step:**
1. Compute `true_prob` from Poisson (Section 1) — this is the model's projection, built from first principles
2. Compute `kalshi_implied_prob` from Kalshi vig-free price — this is the market you are betting into
3. Compute `raw_gap = true_prob − kalshi_implied_prob`
4. Look up `calibration_factor` from Section 3 per-tier table (High=0.187, Med=0.255, Paper=0.18)
5. Compute `edge = raw_gap × calibration_factor`
6. **Log `edge` as `edgePct` in bets.json — not `raw_gap`.** If edgePct >10% at near-even prices, check Rule 70 gate before logging.
7. Assign confidence tier from the calibrated `edge` (not raw_gap): High ≥3.0%, Medium 1.5–2.9%, Paper 1.0–1.49%

**⚠️ Common failure mode (June 1, 2026 flag):** 42 Medium bets were logged with edgePct 20–31% — impossible at Medium tier. Cause: raw_gap was written to edgePct without applying calibration_factor. The calibration factor is mandatory, not optional. A bet record where edgePct and confidence are logically inconsistent (e.g., edgePct=22%, confidence=Medium) indicates a logging bug — flag for investigation. Confidence field is ground truth; edgePct must match it after calibration.

**Probability sources:**
- `true_prob` → Section 1 Poisson output — the model's projection, always computed from first principles. Never derived from any market.
- `kalshi_implied_prob` → Kalshi vig-free — the market you are betting into. This is what the model is compared against.

**Kalshi direction:** YES = away team.

**Pinnacle — sanity check only, not the edge target:**
Pinnacle VF is computed and displayed alongside every edge but is NOT subtracted from true_prob. It answers one question: *"Is the sharpest market in the world in the same ballpark as my model?"*

| Model vs Pinnacle VF Divergence | Action |
|---|---|
| ≤5% | Proceed. Model and sharp market broadly agree on the game. |
| 5–7% | Note the divergence. Review game notes for anything the model may have missed. |
| >7% | Flag. Do not bet without a specific reason to trust the model over Pinnacle here. |

Pinnacle diverging from your model is a prompt to review — not an automatic rejection. The model may be capturing something Pinnacle hasn't priced yet. But a >7% gap demands an explanation.

**When Kalshi diverges >15% from model AND Pinnacle agrees with Kalshi:**
This is a meaningful signal against the bet. Investigate: recent form (last 7 and 15 games), injury/lineup news, park, weather, bullpen usage. This is a Tier 2 soft gate — only downgrade if investigation reveals a specific unmodeled factor. Log the finding.

**When Kalshi diverges >15% from model AND Pinnacle also aligns with model:**
Strongest edge signal. The sharpest market and your model agree; Kalshi hasn't caught up. High confidence the Kalshi gap is real and exploitable.

**When Kalshi diverges >15% from model BUT Pinnacle aligns with Kalshi:**
The model is likely wrong on this specific game. Sharp money is telling you something your model missed. Tier 2 soft gate fires. Investigate before logging.

---

## SECTION 3: CALIBRATION FACTORS (Per-Tier)

**Do not use a flat factor. Use per-tier. Classify by confidence field — not edge%. Edge% and confidence field can diverge due to multiplier adjustments; confidence field drives actual sizing decisions and is the correct calibration classifier.**

**IMPORTANT — Current Sample Sizes (as of June 1, 2026):**

| Confidence Tier | N (settled) | Actual WR | Expected WR | Ratio | Current Factor | Suggested | Status |
|---|---|---|---|---|---|---|---|
| High | 52 | 51.9% | 54.9% | 0.945 | 0.187 | 0.187 | **UPDATED June 1, 2026** |
| Medium | 76 | 65.8% | 58.7% | 1.122 | 0.255 | 0.255 | **UPDATED June 1, 2026** |
| Paper | 41 | 51.2% | 51.3% | 0.999 | 0.18 | 0.18 | **DO NOT UPDATE — N<50** |

**Calibration note (June 1, 2026 — v2.2):** Both High and Medium updated. High: 0.198 → 0.187 (N=52, ratio=0.945 — mild overconfidence, small correction). Medium: 0.227 → 0.255 (N=76, ratio=1.122 — mild outperformance, factor adjusted up). Paper held at N=41 — ratio=0.999, essentially perfect calibration, no update needed regardless. Paper threshold is N≥50.

**⚠️ Data quality flag (June 1, 2026):** 42 bets logged as `confidence: Medium` have `edgePct` values of 20–31%, which is logically inconsistent (Medium tier = edge 1.5–3.0%). This indicates an edge% logging bug — likely edge% was computed without the calibration factor applied, or the field was populated from a different calculation. **Do not use edgePct as the primary tier classifier.** The confidence field is ground truth. Investigate and normalize edgePct in bets.json when time permits.

**What to track instead (more signal per bet):** Break down performance by signal type:

| Signal | W | L | WR | P/L | Status |
|---|---|---|---|---|---|
| starterXERA | 50 | 36 | 58% | +$50.19 | ✅ Positive |
| eliteStarter | 13 | 11 | 54% | +$20.86 | ✅ Positive |
| xERAGap | 9 | 5 | 64% | +$7.16 | ✅ Strong |
| f5Amplified | 7 | 5 | 58% | +$1.61 | ✅ Positive |
| bullpenVulnerable | 4 | 3 | 57% | +$10.34 | ✅ Positive |
| streak | 3 | 6 | 33% | -$20.37 | 🚨 Weak — cap weight ≤0.2 |
| reaVulnerable_trueXFIP | 1 | 2 | 33% | -$10.99 | 🚨 Non-standard key — retire |
| bassittVulnerable_trueXFIP | 0 | 2 | 0% | -$9.00 | 🚨 Non-standard key — retire |
| balBullpenVulnerable | 0 | 2 | 0% | -$9.00 | 🚨 Non-standard key — retire |

**Market type P/L (settled bets — June 1, 2026):**

| Market | W | L | WR | P/L |
|---|---|---|---|---|
| ML | 36 | 19 | 65% | +$49.66 |
| Team Total | 14 | 6 | 70% | +$33.49 |
| F5 ML | 14 | 10 | 58% | +$14.25 |
| Run Line / RL | 8 | 5 | 62% | +$13.89 |
| K Prop | 4 | 2 | 67% | +$7.01 |
| YRFI | 6 | 5 | 55% | -$0.01 |
| NRFI | 3 | 3 | 50% | -$7.87 |
| Total (Game) | 12 | 17 | 41% | -$21.12 |
| Game Total | 1 | 2 | 33% | -$9.99 |
| Team Total | 14 | 6 | 70% | +$33.49 |
| F5 ML | 14 | 10 | 58% | +$14.25 |
| Run Line | 6 | 4 | 60% | +$5.18 |
| K Prop | 4 | 2 | 67% | +$7.01 |
| YRFI | 6 | 5 | 55% | -$0.01 |
| NRFI | 3 | 3 | 50% | -$7.87 |
| **Total** | **12** | **17** | **41% ⚠️** | **-$21.12 — PAPER ONLY (Rule 71)** |

**Recalibrate when:** Each tier reaches 50+ settled bets. Run per-tier WR analysis after every session. Update this table. Update the factor only when ratio shifts by >0.05 AND N≥50.

**Calibration Update Procedure (run in bash_tool after each session):**
```python
# 1. Pull settled bets from bets.json
# 2. Group by CONFIDENCE FIELD (not edgePct — see Section 3 data quality note)
# 3. actual_wr = wins / (wins + losses) per tier
# 4. expected_wr = avg(modelPct/100) per tier
# 5. ratio = actual_wr / expected_wr
# 6. Compute 95% CI: se = sqrt(wr*(1-wr)/N); ci = wr ± 1.96*se
# 7. If N≥50 AND ratio shifts >0.05: new_factor = current_factor × ratio → update this table
# 8. Also update signal-type table after every session
```

---

## SECTION 4: KELLY SIZING

### Base Size Table
| Edge | Confidence | Base Size |
|---|---|---|
| ≥3.0% | 🟢 High | $4 |
| 2.0–2.9% | 🟡 Medium | $3 |
| 1.5–1.9% | 🔴 Paper | $1 (log only) |

**High tier base reduced to $4 (from $6–8) until N≥50 settled High bets.** Current sample (N=23) has ±23.7% CI — insufficient to justify premium sizing. Restore $6–8 when High tier reaches N≥50 and recalibration confirms edge.

Session cap: $100–120. Quarter Kelly is a ceiling, not a floor.

Medium bet cap during losing streak: max 10 medium bets/session, total medium exposure ≤$35 (post-multiplier dollar amounts) until positive ROI restored.

**Streak weight cap:** If streak is a factor in the bet with weight >0.2 in factors{}, cap at Medium regardless of calculated edge.

---

### Dynamic Multiplier System (Temporary — expires at N≥30 per category)

When a market type or signal type has N≥10 settled bets with realized win rate meaningfully diverging from 50%, apply a multiplier to the base size. This corrects for the fact that realized edge varies by market type independent of the raw edge% calculation.

**Multiplier formula:**
```
multiplier = max(0.5, min(2.0, 1.0 + (realized_WR − 0.50) × 4))
```
Round to nearest 0.25x. Apply to base size, then round to nearest $0.50. Never exceed session cap.

**Current multipliers (updated June 1, 2026 — v2.2):**

| Category | N | Realized WR | Multiplier | Status |
|---|---|---|---|---|
| K Props | 6 | 67% | 1.50x | ACTIVE |
| F5 ML | 24 | 58% | 1.25x | ACTIVE |
| NRFI | 6 | 50% | 1.00x | NEUTRAL |
| YRFI | 11 | 55% | 1.00x | HOLD — activates at WR ≥58% over N≥15, or WR ≥55% sustained over N≥20. Review at next calibration once N=15 reached. |
| eliteStarter signal | 24 | 54% | 1.00x | NEUTRAL |
| streak signal | 9 | 33% | 0.50x | ACTIVE PENALTY (Rule 41 cap still applies) |
| Team Total | 20 | 70% | 1.75x | ACTIVE |
| ML | 55 | 65% | 1.50x | ACTIVE |
| Run Line / RL | 13 | 62% | 1.50x | ACTIVE |
| Game Total / Total | 32 | 41% | PAPER ONLY | Rule 71 — WR<52%, all Total bets Paper until ≥52% over N≥30 |
| starterXERA signal | 86 | 58% | 1.25x | ACTIVE (downgraded from 1.75x — N now large enough to normalize) |
| xERAGap signal | 14 | 64% | 1.50x | ACTIVE |

**How to apply:**
1. Start with base size from tier table above
2. Identify the primary market type of the bet
3. If the bet's primary signal is `starterXERA` or `eliteStarter`, apply the signal multiplier instead of (not in addition to) the market multiplier — use whichever is higher
4. Multiply base size × multiplier, round to nearest $0.50
5. Never exceed $8 per bet regardless of multiplier
6. streak-weighted bets (Rule 41): multiplier is capped at 0.75x regardless of market type
7. **Medium bets (after multiplier) cannot exceed the dollar size of High bets in the same session.** If a Medium bet's multiplied size exceeds the current High bet size ($4 base × High multiplier), cap the Medium bet at that High bet dollar amount. This prevents market-type multipliers from inverting the confidence hierarchy in dollar terms. Example: High Game Total = $4.00; Medium ML with starterXERA = $5.50 → cap at $4.00.

**Examples:**
- Medium ML with starterXERA signal: $3 × 1.25x = $3.75 → **$4.00** (then check cap vs current High size)
- Medium ML with xERAGap signal: $3 × 1.50x = $4.50 → **$4.50** (use whichever signal multiplier is higher)
- Medium Team Total (no special signal): $3 × 1.75x = $5.25 → **$5.00** (cap at current High bet size if exceeded)
- High ML: $4 × 1.50x = $6.00 → **$6.00**
- High Game Total: $4 × 1.00x = $4.00 → **$4.00** (Paper only per Rule 71 — sizing moot)
- Medium F5 ML (f5Amplified): $3 × 1.25x = $3.75 → **$4.00**

**⚠️ Note: starterXERA multiplier was downgraded from 1.75x → 1.25x as of v2.2 (N now large enough to normalize). Any examples or notes referencing 1.75x for starterXERA are stale — use 1.25x.**

**Sunset condition:** Once any category reaches N≥30 settled bets, freeze its multiplier and flag for full recalibration at N≥50. At N≥50, retire the multiplier system for that category and let the per-tier calibration factor absorb it. Goal is for this system to be fully retired once all major categories hit N≥50 and the base Kelly table is properly calibrated.

---

## SECTION 5: PARK FACTORS

Apply **additively** before comparing projection to line. Apply to both teams' projections for game totals; apply full amount to relevant team for TT markets.

**NEW: GB%/FB% Modifier**
Park factors are not uniform across pitcher types. Extreme fly-ball pitchers are disproportionately affected by hitter-friendly parks. Apply a modifier for Coors, GABP, and Dodger Stadium only:

```
park_adj_modified = park_adj × (1 + (starter_FB% − 0.35) × 0.5)
```

- Starter with 45% FB% at Coors → modifier = 1 + (0.45-0.35)×0.5 = 1.05 → Coors adj × 1.05
- Starter with 25% FB% at Coors → modifier = 1 + (0.25-0.35)×0.5 = 0.95 → Coors adj × 0.95
- If no FB% data: use standard park_adj with no modifier

### Hitter-Friendly Parks
| Park | Team | Run Adj (per team) |
|---|---|---|
| Coors Field | COL | +0.75 per team (+1.5 total) |
| GABP | CIN | +0.35 per team (+0.7 total) |
| Globe Life Field | TEX | +0.25 per team (+0.5 total) |
| Chase Field (open roof) | AZ | +0.25 per team (+0.5 total, closed: 0) |
| Camden Yards | BAL | +0.20 per team (+0.4 total) |

### Pitcher-Friendly Parks
| Park | Team | Run Adj (per team) |
|---|---|---|
| Oracle Park | SF | −0.30 per team (−0.6 total) |
| Petco Park | SD | −0.25 per team (−0.5 total) |
| Dodger Stadium | LAD | −0.20 per team (−0.4 total) |
| Kauffman Stadium | KC | −0.20 per team (−0.4 total) |
| Guaranteed Rate | CWS | −0.15 per team (−0.3 total) |

### Wind (from weather.json)
| Wind | Adj |
|---|---|
| OUT >15 mph | +0.6 total |
| OUT 10–15 mph | +0.3 total |
| IN >15 mph | −0.5 total |
| IN 10–15 mph | −0.3 total |
| Crosswind / <10 mph | 0 |

**Rain = postponement risk only. Never a scoring suppressor. (Rule 15)**

**Coors override:** At Coors, require both starters sub-2.50 xFIP AND K/9 >9.0 before logging any Under.

---

## SECTION 6: "ELITE OFFENSE" THRESHOLDS

Rules 17, 27, 30, 31 reference these terms. Defined numerically:

| Label | Season R/G | Rolling 15-game R/G |
|---|---|---|
| Elite / Top-5 (hard gates Under at High) | ≥5.2 | ≥5.5 (either triggers Tier 1 gate) |
| Above average | 4.8–5.1 | 5.0–5.4 |
| Average | 4.3–4.7 | 4.5–4.9 |
| Below average / allows Under lean | <4.5 (AND) | <4.8 (both must be true) |

Run differential: background context only. Season run diff is NOT a primary edge signal (Rule 38). Use rolling 15-game R/G + barrel%/hard-hit rate as primary context.

---

## SECTION 7: OUTPUT FORMAT PER GAME

For every game, produce ALL of the following in one block:

1. **Starter True Talent** — **both sides required**: xFIP, xERA, K/9, BB/9, season depth, regression weights used, true_xFIP, xFIP/xERA divergence flag, TTO split if available, handedness flag. Both starters must appear even when only one side has edge — the opposing starter is the single most common unmodeled variable on ML/RL/F5 bets (Rule 73).
2. **Run Projection** — show the full math explicitly:
   ```
   AWAY: 4.5 × [off_scalar] × [pit_scalar] × [pen_scalar] + [park_adj (with FB% modifier)] = X.X runs
   HOME: 4.5 × [off_scalar] × [pit_scalar] × [pen_scalar] + [park_adj] = Y.Y runs
   TOTAL PROJ: Z.Z | F5 PROJ: AWAY A.A (5/8.5 ratio × durability × tto_adj) / HOME B.B
   ```
3. **Team Context** — rolling 7 and 15-game R/G + record, season R/G (rpgIndex as context), bounceback/regression flag, prior-day runs, 1st-inning run rate (NRFI/YRFI), lineup adjustment applied, lineup timing note if late
4. **Poisson Probabilities** — computed live (not just from table): P(away wins), P(home wins), P(push), P(over line), P(TT over)
5. **Market Table** — `Market | Kalshi Price | Kalshi Implied% | Pinnacle VF% | Model True% | Edge | Conf`
   (Kalshi is the bet price and edge target; Pinnacle is the sanity check reference)
6. **Stack Check [MANDATORY when logging 2+ bets on same game]** — Required before any multi-market bet is logged. Answer three questions explicitly:
   - How many bets are being logged on this game?
   - Are any of them correlated (ML + RL + F5 + TT all on same team = same thesis)? If yes → keep only the single best market at real size; log the rest Paper.
   - What is the aggregate game exposure in dollars? Does it exceed what one High-confidence bet would cost? If yes → size down.
   Output format: `STACK CHECK: [N bets] | Correlated: [Yes→reduced / No] | Aggregate: $X | Independent angles: [list]`
   A game with 2+ real-size bets and no Stack Check in the output is a Rule 76 violation. Do not log the bets without it.
7. **Gate Check** — list any Tier 1 or Tier 2 gates that fired and how they were resolved
8. **Thesis bullets** — what drives the edge, with signal weights in factors{}. For ML/RL/F5 bets: factors{} must include a key for **both** starters (e.g. `eliteStarter`, `starterXERA`, `xERAGap`). A factors{} with only one starter's signal is a Rule 73 violation.
9. **Written thesis sentence** — one plain-English sentence in the notes field explaining why this bet wins. Must reference both starters by name and true_xFIP. Data-only notes (numbers without narrative) are a Rule 61 violation → Paper only.
10. **Model improvement flags** — any new pattern

### ⚠️ MANDATORY MARKET EVALUATION CHECKLIST (Rule 67)

Every game block must include every row of this table. A missing row is a model failure — silence is NOT a rejection.

```
GAME: [AWAY @ HOME] — [Date]
┌──────────────────────┬───────────────────────────────────────────────────┐
│ Market               │ Edge / Gate Result                                │
├──────────────────────┼───────────────────────────────────────────────────┤
│ ML — Away            │ Edge: X.X% | Conf: [High/Med/Paper/BLOCKED]       │
│ ML — Home            │ Edge: X.X% | Conf: [High/Med/Paper/BLOCKED]       │
│ RL Away -1.5         │ Edge: X.X% | P(cover): XX% | Conf: [...]          │
│ RL Home -1.5         │ Edge: X.X% | P(cover): XX% | Conf: [...]          │
│ Game Total Over      │ Edge: X.X% | Proj: X.X vs Line X.X | Conf: [...] │
│ Game Total Under     │ Edge: X.X% | Buffer: X.X | Conf: [...]            │
│ Away TT Over         │ Edge: X.X% | Proj: X.X vs Line X.X | Conf: [...] │
│ Home TT Over         │ Edge: X.X% | Proj: X.X vs Line X.X | Conf: [...] │
│ F5 ML — Away         │ Edge: X.X% | F5 proj: A.A/B.B | Conf: [...]      │
│ F5 ML — Home         │ Edge: X.X% | F5 proj: A.A/B.B | Conf: [...]      │
│ NRFI                 │ Edge: X.X% | 4-factor composite: [pass/fail]      │
│ YRFI                 │ Edge: X.X% | 4-factor composite: [pass/fail]      │
│ Pitcher K Prop (Away)│ Edge: X.X% | Conf: [N/A if starter unconfirmed]  │
│ Pitcher K Prop (Home)│ Edge: X.X% | Conf: [N/A if starter unconfirmed]  │
└──────────────────────┴───────────────────────────────────────────────────┘
```

**Acceptable rejection reasons** (must be explicit, not blank):
- `BLOCKED — [Rule #]: [reason]` (e.g., `BLOCKED — Rule 34: NRFI, total ≥8.0`)
- `N/A — starter unconfirmed` (for K props, F5 when starter not verified)
- `N/A — TT line unconfirmed, Paper only` (per Rule 44)
- `No edge — model X.X% vs Pinnacle VF X.X% = X.X% raw gap, calibrated edge below 1.0% threshold`
- `Paper only — Rule 71 (Game Total WR 41%)`

A row that is simply absent = model failure. Fill every row, every game.

---

## SECTION 8: MARKETS TO SCAN EVERY GAME

ML, run line, game total (over AND under), both team totals, YRFI, NRFI, F5 ML, F5 spread, pitcher Ks, hits, TB.

**F5 ML is mandatory for every game with confirmed starters on both sides — never skip.**

---

## SECTION 9: TEAM CONTEXT — BOUNCEBACK/REGRESSION ALGORITHM

**Season-long run differential is NOT a primary edge signal.** Use rolling windows + underlying metrics.

### Rolling Performance Window
- Last 7-game and last 15-game R/G and record
- Season R/G (`runsPerGame`) as quality baseline for bounceback/regression detection
- Barrel%, hard-hit rate, walk rate from Savant for confirmation

### Bounceback Spot
Team's recent results meaningfully worse than underlying metrics:
- Last 7–15 game R/G well below season R/G and/or barrel%/hard-hit profile
- Losing streak but underlying contact quality, hard-hit rate, walk rate remain solid
- Facing weak starter (xFIP >4.5) or vulnerable bullpen (xFIP >4.3)
→ Weight offensive output higher than recent R/G suggests. Lean TT Over. Consider fading Under (Tier 2 soft gate fires).

### Regression Spot
Team's recent results meaningfully better than underlying metrics:
- Last 7–15 game R/G well above season R/G and/or barrel%/hard-hit profile
- Hot streak driven by BABIP or bullpen variance, not hard contact
- Facing strong starter (xFIP <3.50) or elite bullpen
→ Weight output lower than recent R/G suggests. Lean TT Under. Fade Over if pitcher is elite.

No hard thresholds — divergence between results and underlying quality drives the signal, not streak length.

---

## SECTION 10: STRIKEOUT PROP CHECKLIST

ALL required before logging any K prop:

0. Opener check: <3 IP/start → verify 1st-inning xERA via Savant. Unavailable or <5 appearances → STOP.
1. Same-day starter confirmed
2. BB/9 < 3.0 (high walk = early exit risk, kills K volume) — Tier 3 scalar if 3.0–3.5; Tier 1 gate if >3.5
3. 5+ IP in 4 of last 5 starts (durability)
4. **Handedness-adjusted K%** — weighted average vs today's lineup L/R composition (Step 3 — required)
5. Opposing team K% vs pitcher's handedness (last 14 days)
6. Lineup construction (injuries, platoon)
7. Recent form both sides (pitcher L3, team K rate L14)

Season K average alone is never sufficient.

---

## SECTION 11: UNDER PRE-LOGGING GATE (TIERED)

Run in order before logging ANY Under at Medium or High confidence.

### Tier 1 Hard Gates (any failure = auto-block at High; must reach Medium minimum to log)
1. 🚫 Neither offense top-5 R/G (season ≥5.2 OR rolling 15-game ≥5.5) — Rule 27/30
2. 🚫 Neither opposing starter xERA >5.5 — Rule 27
3. 🚫 Neither team using an opener (unless opener has verified sub-3.00 1st-inning xERA) — Rule 31

### Tier 2 Soft Gates (each failure downgrades one tier; two failures = block at Medium)
4. ⚠️ Neither team scored 7+ runs yesterday (unless both starters 9+ K/9 AND BB/9 <3.0) — Rule 35
5. ⚠️ ML not within 15 cents of pick'em (extra-inning inflation risk) — Rule 22
6. ⚠️ No conflicting ML/F5 already logged implying favored team scores 4–5+ runs — Rule 32
7. ⚠️ Neither team flagged as bounceback candidate — Section 9
8. ⚠️ Park check passed (Coors: both starters sub-2.50 xFIP AND K/9 >9.0)

**Scoring:** 0 Tier 2 failures → proceed. 1 failure → downgrade one tier. 2+ failures → Paper only or skip.

---

## SECTION 12: TOTAL & TEAM TOTAL RULES

### ⚠️ GAME TOTAL MARKET — PAPER ONLY UNTIL WIN RATE RECOVERS (Rule 71)
**Current Total record: 12W 17L (41%), -$21.12. All game total bets (Over AND Under) are capped at Paper ($1) until rolling WR ≥52% over N≥30 settled Total bets. Log the current WR at session start. TT (Team Total) bets are NOT affected.**

- K rate primary for total suppression. 9+ K/9 suppresses totals regardless of ERA.
- Use Poisson Total Probability (Section 1 — computed live) for true probability.
- Elite starter (xFIP <3.00) on either side → lean Under, UNLESS Rule 27 override applies.

**Game Total Over Decision Tree (apply in order before logging any Over):**
0. **[T1] f5Amplified check first:** If xERAGap ≥1.5 on this game (f5Amplified=True), game total Over is BLOCKED at Medium/High — redirect to the vulnerable team's TT Over. Paper only unless both starters have true_xFIP >4.50. Log: "Rule 70: f5Amplified gate → game total Over blocked → [team] TT Over." (Rule 39/Rule 46)
1. **Both starters elite (xFIP <3.00):** Lean Under on game total. Over requires both offenses to be top-5 R/G and neither starter dominant enough to suppress. Default: skip Over, evaluate TT Unders.
2. **One starter elite, one starter average/below (xFIP 3.51–4.50):** Do NOT reflexively skip the Over. Run the Poisson projection. If the weak-side offense projects 4.5+ runs and the elite pitcher only suppresses one half, the Over may still be live. Check Rule 27 override (is the opposing offense top-5 R/G AND opposing starter xERA >6.0?). If yes → log Over or skip; if no → evaluate TT Over for the weak-side team.
3. **One starter elite, one starter weak/replacement (xFIP >4.50 or xERA >5.5):** This is a lopsided matchup. Evaluate the elite offense's TT Over. Do NOT log game total Over (Rule 39) — the elite pitcher suppresses the other half. This is the canonical Rule 39 pattern.
4. **Full Rule 39 pattern (elite offense vs weak starter AND opposing elite pitcher):** Log elite team's TT Over only. Skip game total Over entirely.

- **TT analysis must be completed regardless of line confirmation status.** Do not skip TT analysis because the line is unconfirmed. Complete the full projection, document the edge, and log at Paper ($1) if the actual TT line cannot be confirmed during analysis. (Rule 44)
- TT Over requires: opposing pitcher vulnerable AND offense has recent scoring form (last 7-game R/G). Check bounceback signal.
- **TT line must be confirmed before logging Medium/High.** Tier 1 hard gate. (Rule 44)
- Prior-day offense flag: 7+ runs yesterday → require both starters 9+ K/9 AND BB/9 <3.0 for any Under. (Rule 35)
- **Three-layer framework required for ALL Total and TT bets** (not just Unders) — Rule 64. Layer 2 stress test is required for Overs too: "What is the single most likely event that prevents the Over from hitting?" If no stress test is written, the bet is incomplete → Paper only.

---

## SECTION 13: F5 ANALYSIS RULES

- F5 mandatory for every game with confirmed starters on both sides
- F5 projection: use 5/8.5 ratio (not 5/9) × durability × TTO adjustment (Step 7)
- `f5Amplified: true` = xFIP gap ≥1.5 — highest confirmed signal. Prioritize.
- **[T1] When f5Amplified=True fires, trigger Rule 70 gate on same-game total Over.** The conditions that make an F5 bet strong (one elite pitcher) are the same conditions that make the game total Over weak. Log the F5, redirect runs to TT Over, block game total Over at Medium/High.
- F5 is independent from full-game ML/RL — betting both is additive
- Opener blocked games: F5 UNQUALIFIED (Rule 24)
- **F5 price must be confirmed on FD/DK before logging Medium/High.** Tier 1 hard gate. Paper only if unconfirmed. (Rule 42)
- Bullpen is NOT a factor for F5 edge calculation.

---

## SECTION 14: RUN LINE RULES

- Evaluate RL independently every game
- Use Poisson run projection margin to estimate P(cover) — compute live
- RL plus money (+120+) with P(cover) >45%: log it
- RL minus money: require P(cover) >52%
- **ML -200 or worse:** compare RL CLV first. If RL plus money with P(cover) >50%: log RL as primary, ML paper only. (Rule 33)

---

## SECTION 15: NRFI / YRFI COMPOSITE CHECKLIST

All four factors required. Do not log on one or two inputs.

1. **Both pitchers' 1st-inning xERA** (min 5-start sample from Savant)
   - NRFI: both sub-3.00. One ace alone is not sufficient.
   - YRFI: either pitcher 1st-inning xERA >4.00 or BB/9 >3.5
2. **Both pitchers' recent 1st-inning form** (last 5 starts)
   - Run scored in 3 of last 5 → YRFI lean
   - Clean 1st inning in 4 of last 5 → NRFI lean
3. **Both teams' 1st-inning run rate** (season + last 15 games)
   - Top-5 1st-inning offense → YRFI signal regardless of pitcher (Tier 1 gate for NRFI)
4. **Park and lineup factors**
   - Hitter-friendly parks increase YRFI probability
   - Contact hitters 1–3 in lineup → higher 1st-inning run rate

**NRFI blocked (Tier 1):** game total ≥8.0 unless both pitchers verified sub-3.00 1st-inning xERA (Rule 34)
**YRFI:** do not log based on "low K%" alone for elite starters (xFIP <3.00) — Rule 36
**Opener = default YRFI lean**, satisfies factors 1 and 2 automatically.

**Partial-Data Protocol (when one or more factors are unavailable):**
- If **Factor 1** (1st-inning xERA) is unavailable for one starter due to insufficient sample (<5 starts): complete factors 2, 3, and 4. Document the gap explicitly ("Factor 1 unavailable — [pitcher] has <5 1st-inning starts in sample"). Cap confidence at **Paper only**. Do not skip the analysis.
- If **Factor 1** is unavailable for *both* starters: skip NRFI/YRFI entirely for this game and document the reason.
- If **Factor 2** (recent 1st-inning form) is unavailable: use factor 1 xERA as a substitute signal. Note the gap. One-tier downgrade from calculated confidence.
- If **Factors 3 or 4** data is unavailable: complete remaining factors. Note which data was missing. Do not downgrade — these are supporting signals, not gates.
- **In all partial-data cases: run the composite, document the result, and log at the appropriate (potentially downgraded) confidence.** Never silently skip an NRFI/YRFI because one factor is missing.

---

## SECTION 16: BULLPEN MODELING

### Bullpen Quality Tiers
| xFIP | Tier Label | Run Adj to Opp TT | Dampened Scalar |
|---|---|---|---|
| <3.50 | `bullpenElite` | −0.3 to −0.5 | 0.77–0.84 |
| 3.50–4.20 | `bullpenAverage` | 0 | 0.84–0.97 |
| 4.21–4.80 | `bullpenVulnerable` | +0.5 to +0.8 | 1.00–1.08 |
| >4.80 | `bullpenFatigued` or `bullpenTerrible` | +1.0 to +1.5 | 1.08+ |

### Workload/Fatigue Flag
15+ IP in last 3 days → step down one tier before calculating scalar. Use `bullpenFatigued` label.

### Market Application
- Game Total: both bullpens vulnerable/terrible → Over lean. One elite bullpen → Under lean on that TT.
- TT: opposing bullpen xFIP >4.20 → adds 0.4–0.8 to TT projection
- ML/RL: secondary factor only. Tiebreaker when within 20 cents of pick'em.
- **F5: bullpen is NOT a factor.** Do not include in F5 calculations.

### Logging in factors{}
Use exact labels: `"bullpenVulnerable": 1.0`, `"bullpenElite": 1.0`, `"bullpenFatigued": 1.0`
Do NOT use generic `"bullpen": 0.X`

---

## SECTION 17: CLV TRACKING

After each slate: logged price → bet-time line → closing line → direction → WIN/LOSS/PUSH → P/L → CLV%

**Pre-game line snapshot.** At bet-log time, record the current Pinnacle line as `betTimeLine`. This is separate from `closingLine` (at first pitch). Having `betTimeLine` means CLV is computable even if the closing line degrades after 48 hours.

**Closing line sources:**
- All markets: Kalshi historical snapshot at first pitch via The Odds API (primary — v3.0).
- Pull using: `GET /v4/historical/sports/baseball_mlb/odds?bookmakers=kalshi&date={first_pitch_utc}`
- If Kalshi historical pull fails: use `betTimeLine` (Kalshi price at bet time) as proxy → flag as "estimated".
- Log null only if betTimeLine is also unavailable. Never fabricate.

---

### CLV% Formula (Standardized)

CLV is expressed as implied probability difference — not raw odds points. This normalizes comparisons across ML, RL, and totals bets.

**Step 1 — Convert American odds to implied probability (vig-free not required here; use raw):**
- Favorite (negative): `|odds| / (|odds| + 100)`
- Underdog (positive): `100 / (odds + 100)`

**Step 2 — Calculate CLV%:**
```
CLV% = impliedProb(closingLine) − impliedProb(betPrice)
```
- Positive = you beat the close (edge confirmed)
- Negative = market moved against you (process review)
- Use `betTimeLine` as the closing reference if `closingLine` is unavailable

**Example:**
> Bet: Team ML at +115 → impliedProb = 100/215 = 46.5%
> Closing line: +105 → impliedProb = 100/205 = 48.8%
> CLV% = 48.8% − 46.5% = **+2.3%** ✅

Log `clv` field as a decimal percentage (e.g., `2.3`, not `0.023`).

---

### CLV Interpretation Matrix

| CLV | Result | Meaning | Action |
|---|---|---|---|
| Positive | WIN | Best outcome | None |
| Positive | LOSS | Correct process, wrong outcome — variance | None |
| Negative | WIN | Lucky — market knew something | Review |
| Negative | LOSS | Process error | **Mandatory autopsy** |
| Flat — stable | Any | Matched market exactly — neutral signal | None |
| Flat — round-trip | Any | Line moved against you then recovered — monitor | Note in log |

**Flat CLV sub-classification:** Log a `clvNote` when flat CLV is observed.
- `"stable"` — line barely moved from bet to close (within ±0.5%)
- `"round-trip"` — line moved against you mid-window then returned to near entry price. Check if the adverse move was meaningful (>1.5%) — if so, treat as soft negative for process review even if final CLV is flat.

**Negative CLV + Loss = mandatory autopsy.** Identify rule violated, log it.

---

### CLV by Market Type

Track CLV separately per market to identify where the model is generating real alpha vs. noise. Log `market` on every bet entry (already required). At each model review, segment CLV averages:

| Market | Min Sample for Signal | Target Avg CLV |
|---|---|---|
| ML | 30 bets | ≥ +1.0% |
| RL | 20 bets | ≥ +1.5% |
| Game Total (O/U) | 20 bets | ≥ +1.0% |
| Team Total (TT) | 15 bets | ≥ +1.5% |
| NRFI/YRFI | 15 bets | ≥ +1.5% |
| F5 ML/RL | 20 bets | ≥ +1.5% |

If a market segment falls below target over a sufficient sample → pause that market type and audit inputs.

---

### Model Health: Rolling CLV Targets

Track rolling CLV averages at every periodic review. These are the benchmarks for model validity:

| Window | Healthy | Warning | Red Flag |
|---|---|---|---|
| Last 30 bets (all markets) | ≥ +1.5% avg CLV | +0.5% to +1.4% | Below +0.5% or negative |
| Last 100 bets (all markets) | ≥ +1.2% avg CLV | +0.3% to +1.1% | Below +0.3% or negative |

**Red flag protocol:** If rolling 30-bet CLV drops below +0.5%:
1. Pause new bets pending review
2. Audit last 10 negative-CLV bets — identify common factors (market type, data source, rule applied)
3. Do not resume until root cause is identified or sample resolves to warning zone

**CLV is the primary model health signal.** Win rate fluctuates with variance. CLV does not — it measures process quality independent of outcomes.

---

## SECTION 18: BET ENTRY FORMAT

```json
{
  "id": "2026-05-28-001",
  "date": "2026-05-28",
  "game": "AWAY @ HOME",
  "market": "ML",
  "bet": "TEAM ML",
  "price": -145,
  "betTimeLine": -148,
  "awayProjRuns": 4.8,
  "homeProjRuns": 3.2,
  "totalProj": 8.0,
  "trueProbPct": 62.1,
  "modelPct": 62.1,
  "pinnacleVFPct": 58.5,     // sanity check — NOT the edge target
  "kalshiPct": 54.0,          // edge target — model is compared against this
  "edgePct": 2.4,             // edge = (modelPct − kalshiPct) × calibration_factor
  "size": 5,
  "confidence": "Medium",
  "factors": {"xERAGap": 1.4, "bullpenVulnerable": 1.0},
  "underBuffer": null,
  "gatesFired": [],
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "closingLineSource": null,
  "closingLineTimestamp": null,
  "clv": null,
  "notes": ""
}
```

**Fields:**
- `betTimeLine` — Pinnacle line at the moment of bet logging (new — CLV insurance)
- `trueProbPct` — probability from first-principles Poisson, before calibration
- `modelPct` — same as trueProbPct; will diverge if manual adjustments applied
- `pinnacleVFPct` — Pinnacle vig-free probability (sanity check — sharpest market reference, NOT the edge target)
- `kalshiPct` — Kalshi vig-free implied probability (edge target — model is compared against this)
- `gatesFired` — list any Tier 1 or Tier 2 gates that triggered and how resolved
---

## SECTION 19: APPROVED FACTOR KEY REFERENCE LIST

All keys used in `factors{}` must come from this list. Do not create pitcher-specific keys or ad-hoc labels (Rule 60). The signal-type win rate table in Section 3 is aggregated from these keys — non-standard keys break tracking.

### Starter Quality Signals
| Key | When to Use |
|---|---|
| `eliteStarter` | Starter true_xFIP ≤3.00 — ace/elite tier |
| `starterXERA` | Starter is vulnerable/average (true_xFIP >3.50) — primary xERA gap signal |
| `xERAGap` | xFIP gap between the two starters ≥1.5 — F5 amplified signal |
| `f5Amplified` | xFIP gap ≥1.5 AND f5 edge confirmed — highest confidence signal |

### Offense/Context Signals
| Key | When to Use |
|---|---|
| `bounceback` | Team's recent R/G meaningfully below season R/G and barrel%/hard-hit profile |
| `regression` | Team's recent R/G meaningfully above underlying metrics — fade signal |
| `streak` | Win/loss streak is a contributing factor (use with weight per Rule 68) |
| `hotOffense` | Rolling 15-game R/G elevated vs season (>0.5 above) |
| `coldOffense` | Rolling 15-game R/G depressed vs season (>0.5 below) |
| `lineupDowngrade` | Key bat(s) missing from today's confirmed lineup |

### Bullpen Signals (use exact label — no generic `bullpen: X`)
| Key | When to Use |
|---|---|
| `bullpenElite` | Opposing bullpen xFIP <3.50 |
| `bullpenAverage` | Opposing bullpen xFIP 3.50–4.20 |
| `bullpenVulnerable` | Opposing bullpen xFIP 4.21–4.80 |
| `bullpenFatigued` | Bullpen threw 5+ IP last 2 days OR 15+ IP last 3 days |

### Market/Structural Signals
| Key | When to Use |
|---|---|
| `parkOver` | Park factor meaningfully adds runs (hitter-friendly) |
| `parkUnder` | Park factor meaningfully suppresses runs (pitcher-friendly) |
| `windOut` | Wind blowing out >10 mph |
| `windIn` | Wind blowing in >10 mph |
| `handednessAdv` | Pitcher has platoon advantage vs today's lineup composition |
| `handednessDisadv` | Pitcher has platoon disadvantage vs today's lineup composition |
| `ttoRisk` | Starter has meaningful TTO split (>0.50 xFIP points) — F5 context |

### Values
- Weight values in `factors{}` represent relative signal strength, not probability. Use 1.0 for a primary signal, 0.5 for a secondary, 0.2 or less for a minor/confirming signal.
- Streak weight assignment must follow Rule 68 defaults.
- Total weights do not need to sum to any specific value — they are qualitative labels for the signal-type tracking system.

### Rule 73 Requirement — Both Starters in factors{} for ML/RL/F5
For every ML, RL, or F5 bet, factors{} must contain a starter quality key for **both** pitchers:
- The starter being faded (vulnerable/average side): `starterXERA` or `xERAGap`
- The opposing starter (elite/average side): `eliteStarter` or `starterXERA`

Example — NYY ML vs ATH (Weathers vs Ginn):
```json
"factors": {
  "starterXERA": 0.5,
  "eliteStarter": 0.5
}
```
A factors{} with only one starter key on an ML/RL/F5 bet is a Rule 73 violation → bet is Paper only.

### Rule 74 — Adverse Line Movement Flag in bets.json
When settling, if `closingLine` diverged 10+ cents against the bet vs `betTimeLine`, add to the bet record:
```json
"clvNote": "adverse-move"
```
Flag all `clvNote: "adverse-move"` bets for session review. These are the highest-priority autopsy candidates — sharp money moved against the bet after logging, indicating an unmodeled factor.

