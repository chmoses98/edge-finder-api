# MODEL_CORE.md
# Last updated: May 28, 2026

---

## SECTION 1: PROBABILITY ENGINE

The goal of this model is to generate a **true probability from first principles**, then compare it to the market. We do not start with the market price and ask "do I disagree?" We build the probability independently, then measure the gap.

### Step 1 — Starter True Talent Estimate

The single most important input. Do not use raw xERA. Use xFIP as the primary metric — it removes defense and BABIP variance and is more predictive of future run prevention.

**True Talent xFIP Formula:**

```
true_xFIP = (N_recent × recent_xFIP + M_season × season_xFIP) / (N_recent + M_season)
```

Regression weights by pitcher type:

| Pitcher Type | N_recent | M_season | Notes |
|---|---|---|---|
| Established starter (3+ seasons) | 1 | 3 | Season xFIP dominates |
| Younger starter (<3 seasons full-time) | 2 | 3 | More recent weight |
| IL returner (first 3 starts back) | 2 | 2 | Equal weight — do not penalize rust |
| Streak divergence (recent 3 starts ±1.5 xFIP from season) | 3 | 2 | Recent form matters more |

"Recent" = last 5 starts, each weighted equally.

**Additional scalar adjustments to true_xFIP:**

- **xFIP vs xERA divergence**: if xFIP > xERA by 0.5+, the starter is outperforming true talent (fade signal — regress toward xFIP). If xFIP < xERA by 0.5+, they are underperforming (buy signal).
- **Velocity flag**: if starter's recent velocity is 1+ mph below season average → add 0.3 to true_xFIP. Do not use if velocity data unavailable.
- **Handedness adjustment**: adjust for today's lineup composition (see Step 3 below).

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

### Step 2 — Offense Quality Scalar

Use **wRC+** (weighted runs created plus) as the offense input. It park-adjusts and accounts for league context.

| wRC+ | Dampened Scalar |
|---|---|
| 70 | 0.790 |
| 80 | 0.860 |
| 88 | 0.916 |
| 95 | 0.965 |
| 100 | 1.000 (league average) |
| 105 | 1.035 |
| 110 | 1.070 |
| 115 | 1.105 |
| 120 | 1.140 |
| 125 | 1.175 |

Dampen formula: `scalar = 1.0 + (wRC+/100 − 1.0) × 0.70`

**Lineup Adjustment Factor (apply daily):**

Compare today's projected lineup wRC+ to the team's season wRC+. The difference is the daily adjustment.

```
lineup_adj = (today_lineup_wRC+ − season_team_wRC+) / 100
```

Apply as an additive scalar: `adjusted_offense_scalar = base_offense_scalar + lineup_adj × 0.70`

- Full lineup confirmed → use today's projected lineup wRC+
- Lineup not yet confirmed → use season wRC+ with no adjustment
- Missing cleanup hitter (wRC+ >130): subtract ~0.05 from offense scalar
- Missing leadoff or top-2 hitter (wRC+ >115): subtract ~0.03

---

### Step 3 — Handedness Matchup Scalar

Adjust the pitcher's effectiveness for the specific lineup handedness composition facing them today.

```
handedness_scalar = (pct_LHH × pitcher_K%_vs_L + pct_RHH × pitcher_K%_vs_R) / pitcher_overall_K%
```

Where pct_LHH and pct_RHH are the fraction of today's lineup that is left- and right-handed.

- If starter is RHP and lineup is LHH-heavy (>60% LHH) and pitcher has platoon disadvantage (K%_vs_L significantly lower): apply a +0.15 to true_xFIP
- If starter is LHP and lineup is RHH-heavy: same adjustment
- If no platoon split data available: skip adjustment, note "no split data"

This scalar is **required** before logging K props. For totals, it is applied as a modifier to the starter's xFIP in the run projection.

---

### Step 4 — Bullpen Quality Scalar

See Bullpen Modeling section for full tier table. Dampen formula is identical:

`bullpen_scalar = 1.0 + (bullpen_xFIP/4.5 − 1.0) × 0.70`

Apply the **workload/fatigue flag** before using the scalar. If fatigued, step the xFIP down one tier before calculating the scalar.

---

### Step 5 — Run Projection Formula

```
projected_runs = LEAGUE_AVG × offense_scalar × starter_scalar × bullpen_scalar + park_adj
```

Where:
- `LEAGUE_AVG = 4.5` (2026 MLB runs per team per game)
- `offense_scalar` = dampened wRC+ scalar (Step 2) × lineup adjustment factor
- `starter_scalar` = dampened xFIP scalar from true_xFIP (Steps 1 + 3)
- `bullpen_scalar` = dampened bullpen xFIP scalar (Step 4)
- `park_adj` = additive run adjustment from park factors table (see Section 5)

**Calculate for both teams independently.** This produces:
- `away_proj` = projected runs for away team
- `home_proj` = projected runs for home team
- `total_proj` = away_proj + home_proj

Show these two numbers in every game analysis. They are the foundation of all market probabilities.

---

### Step 6 — Poisson Probability Conversion

With two projected run totals, convert to market probabilities using Poisson distribution math.

**Win Probability Lookup Table:**

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
| 3.8 | 4.0 | 39.9% | 45.6% | 14.5% |
| 3.6 | 4.2 | 34.5% | 51.3% | 14.2% |
| 3.2 | 4.8 | 22.6% | 65.0% | 12.3% |
| 2.4 | 5.2 | 11.0% | 80.0% | 9.0% |

For projections not in the table, interpolate or use the formula directly.

**Note on Push (Extra Innings):** Poisson gives P(tie after 9) ≈ 12–14% for most games. For ML betting, the effective win probability excluding push is: `P(team wins | not push) = P(team wins) / (1 − P(push))`. Use this for ML edge calculation.

**Total Probability Lookup Table:**

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

**Team Total Lookup Table:**

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

F5 covers only the first 5 innings. Starters typically handle 5 IP; bullpen handles the rest.

```
away_f5_proj = away_proj × (5/9) × starter_durability_away
home_f5_proj = home_proj × (5/9) × starter_durability_home
```

`starter_durability = min(avg_IP_per_start / 5.0, 1.0)`

- Starter averaging 6.0 IP → durability = 1.0 (handles full F5 window)
- Starter averaging 4.5 IP → durability = 0.90 (some pen exposure in F5)
- Opener (<3 IP avg) → F5 is UNQUALIFIED per Rule 24

Then apply Poisson to f5 projections → F5 win probability.

---

### Step 8 — Run Line Cover Probability

RL cover requires winning by 2+ runs. From the Poisson run projections:

```
P(cover -1.5) = P(team_proj − opp_proj ≥ 2)
```

Approximate lookup:
- When projected margin is ≥ 2.0 runs: P(cover) ≈ 45–55%
- When projected margin is ≥ 3.0 runs: P(cover) ≈ 55–65%
- When projected margin is ≥ 4.0 runs: P(cover) ≈ 65–72%

RL at plus money (+120 or better): log if P(cover) > 45%.
RL at minus money: require P(cover) > 52% before logging.

---

## SECTION 2: EDGE CALCULATION

Edge is only meaningful if the probability in Section 1 was built correctly.

```
edge = (true_prob − market_implied_prob) × calibration_factor
```

**Probability sources in priority order:**
1. Section 1 Poisson output (primary — ground-up)
2. Kalshi implied (comparison only — not an input to true_prob)
3. Pinnacle vig-free (sanity check)

**Kalshi direction:** YES = away team. Sanity-check any gap >10%.

**When Kalshi diverges >15% from model:**
Do not auto-downgrade. Investigate: recent form (last 7 and 15 games), injury/lineup news, park, weather, bullpen usage. Only downgrade if investigation reveals a specific unmodeled factor. Log: "Kalshi divergence [X]% — investigated, [finding or 'no adjustment']."

---

## SECTION 3: CALIBRATION FACTORS (Per-Tier)

**Do not use a flat factor. Use per-tier.**

| Edge Tier | N (as of May 28) | Actual WR | Expected WR | Ratio | Factor |
|---|---|---|---|---|---|
| ≥3.0% (High) | 17 | 52.9% | 66.2% | 0.80 | **0.24** |
| 2.0–2.9% (Medium) | 25 | 76.0% | 63.7% | 1.19 | **0.36** |
| 1.0–1.9% (Paper) | 9 | 44.4% | 56.6% | 0.78 | **0.23** |

**Key findings (May 28):**
- High tier is overconfident. Streak signals drove most failures. xERAGap (F5 amplified) is 3W 0L — strongest confirmed signal.
- Medium tier is underconfident — the real edge is here. $36.98 P/L vs -$2.68 for High.
- Recalibrate when each tier reaches 30+ settled bets. Re-run calibration script after every 10-bet increment in the High tier.

**Calibration Update Procedure (run in bash_tool after each session):**
```python
# 1. Pull settled bets from bets.json
# 2. Group by edgePct tier
# 3. actual_wr = wins / (wins + losses) per tier
# 4. expected_wr = avg(modelPct/100) per tier
# 5. ratio = actual_wr / expected_wr
# 6. new_factor = current_factor × ratio
# 7. Update this table if any ratio shifts >0.05
```

---

## SECTION 4: KELLY SIZING

| Edge | Confidence | Size |
|---|---|---|
| ≥3.0% | 🟢 High | $6–8 |
| 2.0–2.9% | 🟡 Medium | $4–5 |
| 1.5–1.9% | 🔴 Paper | $0–1 (log only) |

Session cap: $100–120. Quarter Kelly is a ceiling, not a floor.

Medium bet cap during losing streak: max 10 medium bets/session, total medium exposure ≤$35 until positive ROI restored.

---

## SECTION 5: PARK FACTORS

Apply **additively** to total projection before comparing to the line. Split evenly per team for game totals; apply full amount to the relevant team's projection for TT markets.

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

**Coors override:** At Coors, require both starters sub-2.50 xFIP AND K/9 > 9.0 before logging any Under.

---

## SECTION 6: "ELITE OFFENSE" THRESHOLDS

Rules 17, 27, 30, 31 reference these terms. Defined numerically:

| Label | Season R/G | Rolling 15-game R/G |
|---|---|---|
| Elite / Top-5 (blocks Under at High) | ≥5.2 | ≥5.5 (either triggers gate) |
| Above average | 4.8–5.1 | 5.0–5.4 |
| Average | 4.3–4.7 | 4.5–4.9 |
| Below average / allows Under lean | <4.5 (AND) | <4.8 (both must be true) |

Run differential context (background only):
- Elite: +80+ | Average: −20 to +79 | Weak: below −20

---

## SECTION 7: OUTPUT FORMAT PER GAME

For every game, produce ALL of the following in one block:

1. **Starter True Talent** — both sides: xFIP, xERA, K/9, BB/9, true_xFIP after regression, handedness flag
2. **Run Projection** — show the math: `Away: LEAGUE_AVG × off × pit × pen + park = X.X runs | Home: Y.Y runs | Total: Z.Z`
3. **Team Context** — rolling 7 and 15-game R/G + record, wRC+, bounceback/regression flag, prior-day runs, 1st-inning run rate (for NRFI/YRFI games)
4. **Poisson Probabilities** — derived from projections: P(away wins), P(home wins), P(over line), P(TT over)
5. **Market Table** — `Market | Price | Market Implied% | Model True% | Edge | Conf`
6. **Thesis bullets** — what drives the edge
7. **Model improvement flags** — any pattern suggesting a rule addition

---

## SECTION 8: MARKETS TO SCAN EVERY GAME

ML, run line, game total (over AND under), both team totals, YRFI, NRFI, F5 ML, F5 spread, pitcher Ks, hits, TB.

**F5 ML is mandatory for every game with confirmed starters on both sides — never skip.**

---

## SECTION 9: TEAM CONTEXT — BOUNCEBACK/REGRESSION ALGORITHM

**Do not use season-long run differential as a primary edge signal.** Use rolling windows + underlying metrics.

### Rolling Performance Window
- Last 7-game and last 15-game R/G and record
- Season wRC+ and/or xOPS as underlying quality baseline
- Barrel%, hard-hit rate, walk rate for confirmation

### Bounceback Spot
Team's recent results meaningfully worse than underlying metrics:
- Last 7–15 game R/G well below season xOPS/wRC+/barrel% profile
- Losing streak but underlying contact quality, hard-hit rate, walk rate remain solid
- Facing weak starter (xFIP >4.5) or vulnerable bullpen (xFIP >4.3)
→ Weight offensive output higher than recent R/G suggests. Lean TT Over. Consider fading Under.

### Regression Spot
Team's recent results meaningfully better than underlying metrics:
- Last 7–15 game R/G well above xOPS/wRC+/barrel% profile
- Hot streak driven by BABIP or bullpen variance, not hard contact
- Facing strong starter (xFIP <3.50) or elite bullpen
→ Weight output lower than recent R/G suggests. Lean TT Under. Fade Over if pitcher is elite.

**No hard thresholds** — divergence between results and underlying quality drives the signal, not streak length alone.

### Integration
- Bounceback → TT Over lean, fade Under if logged
- Regression → TT Under lean, treat hot streak as noise on Overs
- Both feed into same-game thesis conflict check (Rule 32)

---

## SECTION 10: STRIKEOUT PROP CHECKLIST

ALL required before logging any K prop:

0. Opener check: <3 IP/start → verify 1st-inning xERA via Savant. If unavailable or <5 appearances → STOP.
1. Same-day starter confirmed
2. BB/9 < 3.0 (high walk rate = early exit risk, kills K volume)
3. 5+ IP in 4 of last 5 starts (durability)
4. **Handedness-adjusted K%** — weighted average vs today's lineup L/R composition (Step 3 above — required)
5. Opposing team K% vs pitcher's handedness (last 14 days)
6. Lineup construction (injuries, platoon)
7. Recent form both sides (pitcher L3, team K rate L14)

**Season K average alone is never sufficient.**

---

## SECTION 11: UNDER PRE-LOGGING GATE

Run in order before logging ANY Under at Medium or High confidence. All must pass.

1. ✅ Neither offense top-5 R/G (season ≥5.2 or rolling 15-game ≥5.5) — Rule 27/30
2. ✅ Neither opposing starter xERA >5.5 — Rule 27
3. ✅ Neither team using an opener (or opener has verified sub-3.00 1st-inning xERA) — Rule 31
4. ✅ Neither team scored 7+ runs yesterday (or both starters 9+ K/9 AND BB/9 <3.0) — Rule 35
5. ✅ ML not within 15 cents of pick'em (extra-inning inflation risk) — Rule 22
6. ✅ No conflicting ML/F5 already logged that implies favored team scores 4–5+ runs — Rule 32
7. ✅ Neither team flagged as bounceback candidate — Section 9
8. ✅ Park check: Coors requires both starters sub-2.50 xFIP AND K/9 >9.0

Any gate fails → downgrade to Paper or skip.

---

## SECTION 12: TOTAL & TEAM TOTAL RULES

- K rate primary for total suppression. 9+ K/9 suppresses totals regardless of ERA.
- Use Poisson Total Probability table (Section 1) for true probability — not intuition.
- Elite starter (xFIP <3.00) on either side → lean Under, UNLESS Rule 27 override applies (opposing offense R/G ≥5.0 AND opposing starter xERA ≥5.5).
- **Lopsided matchup (elite offense vs weak starter AND opposing elite pitcher):** Do NOT log game total Over. Log elite team's TT Over instead. Game total is killed by the elite pitcher's half. (Rule 39)
- TT Over requires: opposing pitcher vulnerable AND offense has recent scoring form (last 7-game R/G, not season alone). Check bounceback signal.
- **TT line must be confirmed before logging Medium/High.** Paper only until confirmed. (Rule 44)
- Prior-day offense flag: either team scored 7+ runs yesterday → require both starters 9+ K/9 AND BB/9 <3.0 before logging Under. (Rule 35)

### Team Total Settlement Procedure
1. Pull final box score via `fetch_sports_data` (game_stats) → `linescore` → team final runs
2. Compare to confirmed TT line. R > line = Over wins. R < line = Under wins. R = line = Push.
3. If TT line unrecoverable → mark UNVERIFIED, 0 P/L for calibration
4. Closing line source: DraftKings (primary for TT). If unavailable → log null, do not fabricate.

---

## SECTION 13: F5 ANALYSIS RULES

- Run F5 for every game with confirmed starters on both sides — mandatory
- F5 model probability: `slate.json` → `game.f5.awayF5Pct` / `homeF5Pct`
- `f5Amplified: true` = xFIP gap large enough that F5 diverges meaningfully from full-game ML
- F5 is independent from full-game ML/RL — betting both is additive, not redundant
- Opener blocked games: F5 UNQUALIFIED per Rule 24
- **F5 price must be confirmed on FD/DK before logging Medium/High.** Paper only if unconfirmed. (Rule 42)
- xERAGap in F5 context: 3W 0L in dataset — highest confirmed signal. Prioritize F5 on xERA gap >1.5 with `f5Amplified: true`.

---

## SECTION 14: RUN LINE RULES

- Evaluate RL independently every game — do not skip because ML is logged
- Use Poisson run projection margin to estimate P(cover):
  - Projected margin ≥2.0 runs: P(cover -1.5) ≈ 45–55%
  - Projected margin ≥3.0 runs: ≈55–65%
  - Projected margin ≥4.0 runs: ≈65–72%
- RL plus money (+120+) with P(cover) >45%: log it
- RL minus money: require P(cover) >52%
- **ML -200 or worse:** compare RL CLV first. If RL plus money with P(cover) >50%: log RL as primary, ML paper only. (Rule 33)

---

## SECTION 15: NRFI / YRFI COMPOSITE CHECKLIST

All four factors required — do not log on one or two inputs.

1. **Both pitchers' 1st-inning xERA** (min 5-start sample from Savant)
   - NRFI: both sub-3.00. One ace alone is not sufficient.
   - YRFI: either pitcher 1st-inning xERA >4.00 or BB/9 >3.5
2. **Both pitchers' recent 1st-inning form** (last 5 starts)
   - Run scored in 3 of last 5 starts → YRFI lean
   - Clean 1st inning in 4 of last 5 → NRFI lean
3. **Both teams' 1st-inning run rate** (season + last 15 games)
   - Top-5 1st-inning offense → YRFI signal regardless of pitcher
   - Do not log NRFI against top-5 1st-inning offense
4. **Park and lineup factors**
   - Hitter-friendly parks increase YRFI probability
   - Contact hitters 1–3 in lineup → higher 1st-inning run rate

**NRFI blocked if:** game total ≥8.0 (unless both pitchers verified sub-3.00 1st-inning xERA) — Rule 34

**YRFI:** do not log based on "low K%" alone for elite starters (xFIP <3.00) — Rule 36. Elite xFIP overrides K%-based YRFI signal unless opposing team is top-5 in 1st-inning run rate.

**Opener = default YRFI lean**, satisfies factors 1 and 2 automatically.

---

## SECTION 16: BULLPEN MODELING

### Bullpen Quality Tiers
| xFIP | Tier | Run Adj to Projection | Dampened Scalar |
|---|---|---|---|
| <3.50 | Elite | −0.3 to −0.5 to opp TT | 0.77–0.84 |
| 3.50–4.20 | Average | 0 | 0.84–0.97 |
| 4.21–4.80 | Vulnerable | +0.5 to +0.8 to opp TT | 1.00–1.08 |
| >4.80 | Terrible | +1.0 to +1.5 to opp TT | 1.08+ |

### Workload/Fatigue Flag
15+ IP thrown in last 3 days → step down one tier:
- Elite → Average | Average → Vulnerable | Vulnerable → Terrible

### Market Application
- Game Total: both bullpens vulnerable/terrible → Over lean. One elite bullpen → Under lean on that TT.
- TT: opposing bullpen xFIP >4.20 → adds 0.4–0.8 to TT projection
- ML/RL: secondary factor only. Tiebreaker when within 20 cents of pick'em.
- **F5: bullpen is NOT a factor** — F5 eliminates bullpen variance entirely.

### Logging in factors{}
- `"bullpenVulnerable": 1.0` | `"bullpenElite": 1.0` | `"bullpenFatigued": 1.0`
- Do NOT use generic `"bullpen": 0.X`

---

## SECTION 17: CLV TRACKING

After each slate: logged price → closing line → direction → WIN/LOSS/PUSH → P/L → CLV%

**Closing line sources:**
- ML, RL, Game Total: Pinnacle (primary). Pull before first pitch.
- TT, NRFI/YRFI: DraftKings (primary). If unavailable → log null, do not fabricate.

| CLV | Result | Meaning |
|---|---|---|
| Positive | WIN | Best outcome |
| Positive | LOSS | Correct process, wrong outcome — variance |
| Negative | WIN | Lucky — market knew something, review |
| Negative | LOSS | Process error — mandatory autopsy |
| Flat | Any | Pure variance, no signal |

**Negative CLV + Loss = mandatory autopsy.** Identify rule violated, log it.

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
  "awayProjRuns": 4.8,
  "homeProjRuns": 3.2,
  "totalProj": 8.0,
  "trueProbPct": 62.1,
  "modelPct": 62.1,
  "kalshiPct": 54.0,
  "edgePct": 2.4,
  "size": 5,
  "confidence": "Medium",
  "factors": {"xERAGap": 1.4, "bullpenVulnerable": 1.0},
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "clv": null,
  "notes": ""
}
```

**New fields vs prior format:**
- `awayProjRuns` / `homeProjRuns` / `totalProj` — from Poisson engine (Section 1)
- `trueProbPct` — probability from first-principles Poisson, before calibration
- `modelPct` — same as trueProbPct for now; will diverge if manual adjustments applied

