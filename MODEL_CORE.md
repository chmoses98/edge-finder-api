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
- **Elite starter (sub-2.50 ERA)** on either side → lean Under
- **Over** requires both pitchers vulnerable — one elite arm kills it
- **Team Total Over** requires: opposing pitcher vulnerable AND offense has recent scoring form
- Cold offense + shaky pitcher ≠ automatic runs

---

## NRFI / YRFI Rules
- **NRFI**: both starters need low walk rate, high K rate, clean 1st-inning ERA — not just one ace
- **YRFI**: requires specific 1st-inning pattern evidence (pitcher history of allowing 1st-inning runs OR offense high 1st-inning rate). Season ERA alone insufficient.
- **YRFI boost**: hitter-friendly parks (Chase, GABP, Coors) + vulnerable starter
- **Opener YRFI lean**: opener-role pitcher (avg <3 IP/start) with no verified 1st-inning ERA data = default YRFI lean, not NRFI — opener faces top of lineup cold with no ramp-up.

---

## CLV Tracking
After each slate record: logged price → closing line → direction → WIN/LOSS/PUSH/TBD → P/L
Positive CLV on losing bet = correct process, wrong outcome (variance). This is the key signal.
