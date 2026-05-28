# MODEL_CORE.md

## Edge Calculation
- Primary: (model prob − Kalshi implied) × calibration factor (see below)
- Sanity check: Pinnacle vig-free prob
- Kalshi YES = **away team**. Sanity-check any gap >10% — likely matching error
- When Kalshi unavailable: derive from pitcher quality gap, team form, streaks, park factors, line inefficiency vs FD/DK
- **Kalshi divergence >15%**: do not auto-downgrade. Flag for deeper investigation — check recent form, injury news, ballpark, weather, lineup construction. Only downgrade if investigation reveals a specific unmodeled factor. Divergence alone is not a reason to reduce confidence; it is a reason to earn that confidence.

## Calibration Factor — Per-Tier (updated May 28, 2026)
**Do not use a single 0.30 flat factor. Use the per-tier factors below.**

| Edge Tier | N | Actual WR | Model Expected WR | Calibration Ratio | Factor to Use |
|---|---|---|---|---|---|
| ≥3.0% (High) | 17 | 52.9% | 66.2% | 0.80 | **0.24** |
| 2.0–2.9% (Medium) | 25 | 76.0% | 63.7% | 1.19 | **0.36** |
| 1.0–1.9% (Paper) | 9 | 44.4% | 56.6% | 0.78 | **0.23** |

**Key findings:**
- The High tier (≥3%) is significantly overconfident — model is assigning ~66% probability to bets winning at only 53%. The model is generating false confidence at the top end, likely from streak signals inflating edge. High-tier bets have -$2.68 P/L despite 9W/8L because they are sized $6–8 and losing frequently.
- The Medium tier (2–2.9%) is actually underconfident — bets are winning at 76% vs 64% expected. These are the model's best bets right now. $36.98 P/L from this tier alone.
- The streak factor is the primary culprit in High-tier failures: 7L 2W on streak-weighted bets overall.
- xERAGap (F5 amplified) is 3W 0L — the most reliable signal in the dataset.

**Recalibrate when each tier reaches 30+ settled bets.** Current samples are below threshold; these ratios are directionally correct but will shift. Re-run the calibration script after each 10-bet increment in the High tier.

### Calibration Script (run in bash_tool after each session)
```python
# Group settled bets by edge tier, compute actual WR vs model expected WR
# Output new per-tier calibration factors
# Update this table when ratio shifts >0.05 from current
```

## Kelly Sizing
| Edge | Confidence | Size |
|---|---|---|
| ≥3.0% | 🟢 High | $6–8 |
| 2.0–2.9% | 🟡 Medium | $4–5 |
| 1.5–1.9% | 🔴 Paper | $0–1 (log only) |

Session cap: $100–120. Quarter Kelly is a ceiling, not a floor.

## Bet Log Format
Log ALL ≥1.5% edge plays.

| # | Game | Bet | Price | Model% | Edge | Size | Conf |
|---|---|---|---|---|---|---|---|

## Output Format Per Game
For every game, produce ALL of the following simultaneously in one block:
1. Pitcher matchup (last 3 starts, both sides)
2. Team context (rolling 15-game R/G + record, season context, bounceback/regression flag — see below)
3. Market table: `Market | Price | Implied% | Model% | Edge×30% | Conf`
4. Key reasons (bullets)
5. Narrative notes
6. **Model improvement flags** — any pattern observed that suggests a rule addition or adjustment

The slate review, bet log updates, and proposed model tweaks all happen together in one pass. Do not run them as separate steps.

## Markets to Scan Every Game
ML, run line, game total, both team totals, YRFI, NRFI, F5 ML, F5 spread, pitcher Ks, hits, TB, HR, RBI

**F5 ML is mandatory for every game with confirmed starters on both sides — never skip.**

---

## Team Context: Rolling Window + Bounceback/Regression Algorithm

**Do not use season-long run differential as a primary edge signal.** Season run diff is inflated by blowouts and early-season variance. Use the following layered approach instead:

### Rolling Performance Window
Pull last 7-game and last 15-game data for each team:
- R/G (last 7, last 15)
- Record (last 7, last 15)
- Team OPS or wRC+ where available

### Bounceback/Regression Divergence Signal
Compare recent results to underlying quality metrics. When they diverge significantly, flag the team:

**Bounceback spot** — team's recent results are meaningfully worse than their underlying metrics:
- Last 7–15 game R/G is well below their season xOPS / wRC+ / barrel% profile
- Team is on a losing streak but underlying contact quality, hard-hit rate, and walk rate remain solid
- They are facing a weak starter (xERA >4.5) or a vulnerable bullpen (xFIP >4.3)
→ Flag as bounceback candidate. Weight offensive output higher than recent R/G suggests. Lean toward their TT Over and consider fading a total Under on their game.

**Regression spot** — team's recent results are meaningfully better than their underlying metrics:
- Last 7–15 game R/G is well above their season xOPS / wRC+ / barrel% profile
- Team is on a hot streak fueled by BABIP or bullpen variance, not hard contact
- They are facing a strong starter (xERA <3.50) or elite bullpen
→ Flag as regression candidate. Weight offensive output lower than recent R/G suggests. Lean toward their TT Under. Fade the Over on their game if the pitcher is elite.

**No hard thresholds** — this is a proportional divergence signal. A 6-game cold streak with bad underlying numbers is not a bounceback spot. A 6-game cold streak with elite underlying numbers facing a garbage starter is. The divergence between results and underlying quality drives the signal, not the length of the streak alone.

### Integration with Betting Markets
- Bounceback spot identified → check TT Over, consider fading the total Under if logged
- Regression spot identified → check TT Under, treat hot streak as noise when evaluating total Overs
- Both signals feed into the same-game thesis conflict check (Rule 32)

---

## Strikeout Prop Checklist (ALL required)
0. **Opener check**: if starter averages <3 IP/start, verify 1st-inning xERA via Baseball Savant splits. If unavailable or sample <5 appearances → STOP, do not proceed with K prop.
1. Same-day starter confirmed (mandatory — do not log if unconfirmed)
2. BB/9 < 3.0
3. 5+ IP in 4 of last 5 starts
4. K rate vs opposing lineup's dominant handedness (last 3 starts)
5. Opposing team K% vs that pitcher's handedness
6. Lineup construction that day (injuries, platoon)
7. Recent form both sides (pitcher L3, team K rate L14)

**Season K average alone is never sufficient.**

---

## Total & Team Total Rules
- **K rate > ERA** for total projection. 9+ K/9 suppresses totals regardless of ERA
- **Elite starter (sub-2.50 ERA)** on either side → lean Under — UNLESS opposing offense is elite (5.0+ R/G or +80 run diff) AND opposing starter is replacement-level (xERA >5.5). In that case the Over has a valid case — the elite starter suppresses only one half.
- **Over** requires both pitchers vulnerable — one elite arm kills it — EXCEPT when the other half is so lopsided it overcomes
- **Team Total Over** requires: opposing pitcher vulnerable AND offense has recent scoring form (last 7-game R/G, not season average alone)
- Cold offense + shaky pitcher ≠ automatic runs — check bounceback signal first
- **[NEW] Prior-day offense flag**: if either team scored 7+ runs yesterday, require both starters to have 9+ K/9 AND BB/9 <3.0 before logging the Under (Rule 35)
- **[NEW] Opener flag**: if either team uses an opener, flag the Under before logging. Opener + top-5 offense = Under blocked (Rule 31)

### Lopsided Matchup Rule (Elite Offense vs Weak Pitcher)
When an elite offense faces a weak starter (xERA >5.5), do **not** log the game total Over expecting the elite team to carry the total alone. Instead:
- Log the elite team's **Team Total Over** — isolates their half of the scoring
- Look for **alternate line Overs at plus money** if the TT line is too juiced
- The game total Over is only valid if the opposing team also has realistic run-scoring upside (xERA >4.5 against the elite team's starter, bullpen vulnerability, or bounceback signal)
- Canonical failure: COL@LAD Total O8 — Ohtani shut COL to 1 run, game totaled 5. LAD TT Over was the correct market.

## Under Pre-Logging Gate (run in order — all must pass for High confidence)
1. ✅ Rule 27/30: Neither offense is top-5 R/G AND neither opposing starter is xERA >5.5
2. ✅ Rule 31: Neither team is using an opener (or opener has sub-3.00 verified 1st-inning xERA)
3. ✅ Rule 35: Neither team scored 7+ runs yesterday (or both starters are 9+ K/9, BB/9 <3.0)
4. ✅ Rule 22: ML is not within 15 cents of pick'em (extra-inning inflation risk)
5. ✅ Rule 32: No conflicting same-game ML/F5 already logged in the same direction as a win projection that exceeds the total
6. ✅ Bounceback check: neither team is flagged as a bounceback candidate (divergence between recent results and underlying quality suggests offensive upside is underpriced)

If any gate fails → downgrade to Paper or skip. Do not log Under at High confidence with any gate failed.

---

## F5 Analysis Rules
- Run F5 evaluation for every game with confirmed starters on both sides
- F5 model probability is in `slate.json` under `game.f5.awayF5Pct` / `homeF5Pct`
- `f5Amplified: true` = xERA gap is large enough that F5 diverges meaningfully from full-game ML → stronger edge signal
- F5 is independent from full-game ML/RL — betting both is additive, not redundant. F5 eliminates second-half bullpen variance.
- **F5 edge tiers** (same as Kelly table): ≥3% High, 2–2.9% Medium, 1.5–1.9% Paper
- **F5 price not in slate** — always note price is estimated. Verify actual line on FD/DK before placing. If actual line is >20% more expensive than estimated, recalculate edge.
- Opener blocked games: F5 UNQUALIFIED per Rule 24 — do not log

---

## Run Line Rules
- Evaluate RL independently from ML for every game — do not skip because ML is already logged
- RL at plus money (+120 or better) with model cover >50% = log it regardless of ML status
- RL at minus money (-130 or worse): require model cover >55% before logging
- Plus-money RL on lopsided streaks/records often has 4–5% edge while ML is too juiced to size properly
- **When ML is -200 or worse, compare RL CLV before sizing.** If RL is plus money with model cover >50%, size the RL and log ML at paper only. Do not pay -200+ juice when the RL is available at plus (Rule 33).

---

## NRFI / YRFI Composite Checklist
NRFI and YRFI require a full four-factor composite — do not log based on one or two inputs alone.

### Required Inputs (all four must be evaluated)
1. **Both pitchers' 1st-inning xERA** (minimum 5-start sample from Baseball Savant)
   - NRFI: both must be sub-3.00. One ace alone is not sufficient (Rule 2).
   - YRFI: either pitcher with 1st-inning xERA >4.00 or walk rate >3.5 BB/9 is a YRFI signal
2. **Both pitchers' recent 1st-inning form** (last 5 starts)
   - Has the pitcher allowed a run in the 1st inning in 3 of last 5 starts? → YRFI lean
   - Has the pitcher retired the first inning cleanly in 4 of last 5? → NRFI lean
3. **Both teams' 1st-inning run rate** (season and last 15 games)
   - Pull each team's 1st-inning runs scored per game
   - Teams that lead the league in 1st-inning runs (e.g. Brewers, Yankees) are YRFI signals regardless of opposing pitcher
   - Do not log NRFI against a top-5 1st-inning offense without elite dual pitching confirmed
4. **Park and lineup factors**
   - Hitter-friendly parks (Chase, GABP, Coors, Globe Life) boost YRFI probability
   - Lineup stacking at top of order (contact hitters 1–3) increases 1st-inning run probability

### NRFI Confidence Gate
- NRFI at High confidence: all four factors lean NRFI — both pitchers sub-3.00 1st-inning xERA, both teams bottom-third 1st-inning run rate, neutral park
- NRFI at Medium: three of four factors lean NRFI, one is neutral
- NRFI blocked: any factor clearly leans YRFI (top-5 1st-inning team, pitcher with 1st-inning xERA >4.00, game total ≥8.0 per Rule 34)

### YRFI Confidence Gate
- YRFI: requires at least two of the four factors to lean YRFI with specific evidence
- Do not log YRFI based on "low K%" for elite starters (xERA <3.00). Elite xERA overrides contact-rate YRFI signals unless the 1st-inning run rate of the opposing team is top-5 (Rule 36)
- Opener role = default YRFI lean, satisfies factor 1 and 2 automatically

---

## Team Total Settlement Procedure

TT bets cannot be left PENDING indefinitely. Follow this procedure after every slate:

1. **Primary:** Pull final box score via `fetch_sports_data` (game_stats) — the `linescore` field has each team's final runs. Compare to TT line.
2. **Verify the TT line:** The line logged at bet time must be confirmed. If only `"Verify TT line"` was noted at logging, pull the actual line from DK/FD historical odds (Google: `"[team] team total [date] DraftKings"`).
3. **If TT line unrecoverable:** Mark as `"status": "UNVERIFIED"` with a note. Do NOT mark WIN or LOSS without confirmed line. Count as 0 P/L for calibration purposes but keep in log.
4. **Settlement formula:** Team scored R runs. TT line is X. If R > X → Over wins. If R < X → Under wins. If R = X → Push.
5. **Log format:** Update `result`, `pl`, `status`. Add `closingLine` = TT line confirmed.

**Going forward:** When logging a TT bet, the TT line MUST be confirmed before logging — not "verify TT line" as a note. If the line can't be confirmed at logging time, log as Paper ($1) with `confidence: "Paper"` until verified. Do not size a TT bet at Medium or High without a confirmed line.

---

## Park Factors — Numeric Adjustments

Apply these adjustments to total and TT projections. Adjustments are additive to the base projection.

### Hitter-Friendly Parks
| Park | Team | Adjustment |
|---|---|---|
| Coors Field | COL | +1.5 runs to game total |
| GABP | CIN | +0.7 runs |
| Globe Life Field | TEX | +0.5 runs |
| Chase Field (open roof) | AZ | +0.5 runs (closed: neutral) |
| Camden Yards | BAL | +0.4 runs |

### Pitcher-Friendly Parks
| Park | Team | Adjustment |
|---|---|---|
| Oracle Park | SF | -0.6 runs |
| Petco Park | SD | -0.5 runs |
| Dodger Stadium | LAD | -0.4 runs |
| Kauffman Stadium | KC | -0.4 runs |
| Guaranteed Rate | CWS | -0.3 runs |

### Wind (from weather.json)
- OUT >15 mph: +0.6 runs | OUT 10–15: +0.3 runs
- IN >15 mph: -0.5 runs | IN 10–15: -0.3 runs
- Crosswind or <10 mph: neutral
- **Rain = postponement risk only, never a scoring suppressor (Rule 15)**

### Application Rules
- Apply park adjustment BEFORE comparing projection to the line
- Coors is the only park that overrides an elite-starter Under lean — at Coors, require both starters sub-2.50 ERA AND K/9 > 9.0 before logging any Under

---

## "Elite Offense" — Hard Threshold Definitions

Rules 17, 27, 30, 31 reference "top-5 R/G" and "elite offense." Defined numerically:

**Top-5 R/G (blocks Under at High confidence):**
- Season R/G ≥ 5.2 OR rolling 15-game R/G ≥ 5.5 (either threshold triggers gate)

**Elite offense for Rule 27 override (Over valid despite one elite starter):**
- Opposing offense R/G ≥ 5.0 (season) AND opposing starter xERA ≥ 5.5 — both required

**"Average or below" (allows elite-starter Under lean):**
- Season R/G < 4.5 AND rolling 15-game R/G < 4.8 — both must be true

**Run differential tiers:**
- Elite: +80 or better | Average: -20 to +79 | Weak: below -20
- Season run diff = background context only (Rule 38), never primary edge input

---

## CLV Tracking
After each slate record: logged price → closing line → direction → WIN/LOSS/PUSH/TBD → P/L
Positive CLV on losing bet = correct process, wrong outcome (variance). This is the key signal.

**Closing line source: Pinnacle (primary). Pull before first pitch. Log under `closingLine` in bets.json.**
For markets Pinnacle doesn't carry (NRFI/YRFI, TT), use DraftKings closing line. If unavailable, log `closingLine: null` and note it — do not fabricate.

**Closing line source: Pinnacle (primary). Pull before first pitch. Log under `closingLine` in bets.json.**
For markets Pinnacle doesn't carry (NRFI/YRFI, TT), use DraftKings closing line. If unavailable, log `closingLine: null` and note it — do not fabricate.

## CLV Interpretation Guide
| CLV | Result | Meaning |
|---|---|---|
| Positive | WIN | Best outcome — got value and won |
| Positive | LOSS | Correct process, wrong outcome — variance, not error |
| Negative | WIN | Got lucky — won despite bad price. Review why market disagreed. |
| Negative | LOSS | Process error — market knew something. Autopsy required. |
| Flat (0%) | Any | No edge signal either way — outcome is pure variance |

**Negative CLV + Loss = mandatory autopsy.** Identify which rule was violated and log it.

---

## Bullpen Modeling

Bullpen quality is a required input for totals, TT Overs, and late-inning ML confidence. The following rules govern how bullpen data feeds into markets.

### Bullpen Data Sources
- Primary: `data/slate.json` → `team.bullpen.xFIP` and `team.bullpen.recentERA` (last 14 days)
- Secondary: Baseball Savant team reliever page for individual xFIP
- Flag if data is >5 days stale — mid-season bullpen composition shifts fast

### Bullpen Quality Tiers
| Tier | xFIP Range | Label |
|---|---|---|
| Elite | < 3.50 | Suppresses late-inning run scoring significantly |
| Average | 3.50–4.20 | Neutral — no meaningful adjustment |
| Vulnerable | 4.21–4.80 | Adds ~0.5–0.8 runs to projected total |
| Terrible | > 4.80 | Adds ~1.0–1.5 runs to projected total |

### Bullpen Workload Flag
If a team's bullpen has thrown 15+ innings in the last 3 days, flag as **fatigued**:
- Fatigued elite bullpen → treat as average for projection
- Fatigued average bullpen → treat as vulnerable
- Fatigued vulnerable bullpen → treat as terrible
- Log: `bullpen: "fatigued"` in bet factors

### Bullpen Integration Rules by Market

**Game Total:**
- Both bullpens vulnerable/terrible → add 0.5–1.0 to total projection (Over lean)
- One elite bullpen → suppress 0.3–0.5 runs from that team's half (Under lean on that TT)
- Canonical success: MIN @ CWS Total O 8.5 — both bullpens xFIP 4.51/4.57, +$6.06 WIN

**Team Total:**
- Opposing bullpen xFIP > 4.20 → adds 0.4–0.8 runs to TT projection (Over lean)
- Opposing bullpen xFIP < 3.50 → subtracts 0.3–0.5 runs from TT projection (Under lean or skip Over)
- Always verify TT line before logging — edge depends on where the line is set vs projection

**ML / Run Line:**
- Bullpen is a secondary factor for ML/RL — starter quality is primary
- Exception: when ML is a pick'em or within 20 cents, bullpen tier can be the tiebreaker
- Fatigued elite bullpen on a -150 favorite = consider fading or reducing size

**F5:**
- Bullpen is explicitly NOT a factor for F5 edge — F5 eliminates bullpen variance entirely
- Do not add or subtract runs for bullpen quality when calculating F5 model probability

### Bullpen Signal Weight in factors{}
Log bullpen contribution to edge:
- `"bullpenVulnerable": 1.0` = vulnerable/terrible, meaningful edge input
- `"bullpenElite": 1.0` = elite, suppression factor
- `"bullpenFatigued": 1.0` = fatigue flag applied
- Do NOT use generic `"bullpen": 0.X` — this was ambiguous in prior bets (see May 27 TEX RL loss)

## CLV Interpretation Guide
| CLV | Result | Meaning |
|---|---|---|
| Positive | WIN | Best outcome — got value and won |
| Positive | LOSS | Correct process, wrong outcome — variance, not error |
| Negative | WIN | Got lucky — won despite bad price. Review why market disagreed. |
| Negative | LOSS | Process error — market knew something. Autopsy required. |
| Flat (0%) | Any | No edge signal either way — outcome is pure variance |

**Negative CLV + Loss = mandatory autopsy.** Identify which rule was violated and log it.
