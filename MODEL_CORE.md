# MODEL_CORE.md

## Edge Calculation
- Primary: (model prob − Kalshi implied) × 0.30 calibration
- Sanity check: Pinnacle vig-free prob
- Kalshi YES = **away team**. Sanity-check any gap >10% — likely matching error
- When Kalshi unavailable: derive from pitcher quality gap, team form, streaks, park factors, line inefficiency vs FD/DK

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
1. Pitcher matchup (last 3 starts, both sides)
2. Team context (record, streak, run diff, R/G)
3. Market table: `Market | Price | Implied% | Model% | Edge×30% | Conf`
4. Key reasons (bullets)
5. Narrative notes

## Markets to Scan Every Game
ML, run line, game total, both team totals, YRFI, NRFI, F5 ML, F5 spread, pitcher Ks, hits, TB, HR, RBI

**F5 ML is mandatory for every game with confirmed starters on both sides — never skip.**

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
- **Team Total Over** requires: opposing pitcher vulnerable AND offense has recent scoring form
- Cold offense + shaky pitcher ≠ automatic runs
- **[NEW] Prior-day offense flag**: if either team scored 7+ runs yesterday, require both starters to have 9+ K/9 AND BB/9 <3.0 before logging the Under (Rule 35)
- **[NEW] Opener flag**: if either team uses an opener, flag the Under before logging. Opener + top-5 offense = Under blocked (Rule 31)

## Under Pre-Logging Gate (run in order — all must pass for High confidence)
1. ✅ Rule 27/30: Neither offense is top-5 R/G AND neither opposing starter is xERA >5.5
2. ✅ Rule 31: Neither team is using an opener (or opener has sub-3.00 verified 1st-inning xERA)
3. ✅ Rule 35: Neither team scored 7+ runs yesterday (or both starters are 9+ K/9, BB/9 <3.0)
4. ✅ Rule 22: ML is not within 15 cents of pick'em (extra-inning inflation risk)
5. ✅ Rule 32: No conflicting same-game ML/F5 already logged in the same direction as a win projection that exceeds the total

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
- **[NEW] When ML is -200 or worse, compare RL CLV before sizing.** If RL is plus money with model cover >50%, size the RL and log ML at paper only. Do not pay -200+ juice when the RL is available at plus (Rule 33).

---

## NRFI / YRFI Rules
- **NRFI**: both starters need low walk rate, high K rate, clean 1st-inning ERA — not just one ace
- **YRFI**: requires specific 1st-inning pattern evidence (pitcher history of allowing 1st-inning runs OR offense high 1st-inning rate). Season ERA alone insufficient.
- **YRFI boost**: hitter-friendly parks (Chase, GABP, Coors) + vulnerable starter
- **Opener YRFI lean**: opener-role pitcher (avg <3 IP/start) with no verified 1st-inning ERA data = default YRFI lean, not NRFI — opener faces top of lineup cold with no ramp-up.
- **[NEW] NRFI total gate**: game total ≥ 8.0 = NRFI blocked unless BOTH starters have verified sub-3.00 1st-inning xERA (5+ start sample). High totals signal both offenses are live — first-inning run probability is too elevated (Rule 34).

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
