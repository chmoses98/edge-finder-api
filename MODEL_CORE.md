# MODEL_CORE.md

## Edge Calculation
- Primary: (model prob − Kalshi implied) × 0.30 calibration
- Sanity check: Pinnacle vig-free prob
- Kalshi YES = **away team**. Sanity-check any gap >10% — likely matching error
- When Kalshi unavailable: derive from pitcher quality gap, team form, streaks, park factors, line inefficiency vs FD/DK
- **Kalshi divergence >15%**: do not auto-downgrade. Flag for deeper investigation — check recent form, injury news, ballpark, weather, lineup construction. Only downgrade if investigation reveals a specific unmodeled factor. Divergence alone is not a reason to reduce confidence; it is a reason to earn that confidence.

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

## CLV Tracking
After each slate record: logged price → closing line → direction → WIN/LOSS/PUSH/TBD → P/L
Positive CLV on losing bet = correct process, wrong outcome (variance). This is the key signal.

## CLV Interpretation Guide
| CLV | Result | Meaning |
|---|---|---|
| Positive | WIN | Best outcome — got value and won |
| Positive | LOSS | Correct process, wrong outcome — variance, not error |
| Negative | WIN | Got lucky — won despite bad price. Review why market disagreed. |
| Negative | LOSS | Process error — market knew something. Autopsy required. |
| Flat (0%) | Any | No edge signal either way — outcome is pure variance |

**Negative CLV + Loss = mandatory autopsy.** Identify which rule was violated and log it.
