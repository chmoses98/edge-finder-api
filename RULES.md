# RULES.md — Model Adjustment Rules

1. Never bet Under when one team has elite offense vs struggling starter — take run line or TT instead
2. NRFI reliable with elite starters on **both** sides only — not just one ace
3. Always use pitcher's last 3 starts specifically — never rely on season ERA alone
4. Verified same-day starters mandatory — wrong pitcher errors invalidate entire analysis
5. Skip ATL -1.5 run line — take ML or TT instead (close games + extras too frequent)
6. TJS return pitchers often come back sharp — do not assume rustiness
7. YRFI only with specific 1st-inning pattern evidence — not just ERA. See MODEL_CORE NRFI/YRFI Composite Checklist.
8. K props require two-sided matchup analysis — see MODEL_CORE strikeout checklist
9. Kalshi edge direction: YES = away team. Sanity check all gaps >10%
10. Pitcher prop bets require same-day starter confirmation — search "[pitcher] starting today" before logging
11. Same-day starter confirmation required for ALL prop bets (probable ≠ confirmed)
12. Team streak data is one signal among many — do not treat streak alone as a fade or follow. A team on a losing streak with strong underlying metrics (wRC+, xOPS, barrel%) facing a weak starter is a bounceback spot, not a fade. A team on a hot streak with weak underlying metrics facing an elite starter is a regression spot. See MODEL_CORE Bounceback/Regression Algorithm.
13. ATL TT Over requires specific game-day reason beyond "good offense" — weak starter, favorable park, wind out, or hot recent stretch
14. Pinnacle doubleheader line gaps >15% are likely data errors — verify manually before acting
15. Rain = postponement risk only — never use as scoring suppressor
16. K rate is primary input for total projection, not ERA — 9+ K/9 suppresses totals regardless of ERA
17. Elite starter (sub-2.50 ERA) on either side = lean Under on game total — BUT only if the opposing offense is average or below. If the opposing offense is elite (top-5 R/G, top-5 run diff) AND the opposing starter is below replacement (xERA >6.0), the Under lean is OVERRIDDEN. Log the Over or skip entirely.
18. TT Over requires recent offensive form (last 7-game R/G) — cold offense + shaky pitcher does not automatically produce runs. Check bounceback signal before logging.
19. High walk rate (BB/9 > 3.5) = high-risk for K props — early exit risk kills volume
20. Always check home/road splits before betting any pitcher's ML or F5 — strong overall ERA can mask a dramatically different road profile. Split must be directionally consistent with the bet before logging.
21. Medium-confidence bet cap: max 10 medium bets per session during a losing streak. Total medium exposure capped at $35/session until model restores positive ROI. Quality over quantity in the 2.0–2.9% tier.
22. Before betting any Under or low-total market, check ML line tightness. Lines within 15 cents of pick'em carry meaningful extra-inning risk that inflates totals unpredictably — flag and reduce Under size or skip.
23. Do not heavily penalize pitchers returning from IL for first 2–3 starts if pre-IL track record is strong. Regress toward career/prior-season quality faster for established arms — early IL-return rust is noise, not signal.
24. If confirmed starter averages <3 IP/start (opener role), pull their 1st-inning ERA/xERA from Baseball Savant splits before running any F5 or prop analysis. If 1st-inning data is unavailable or sample <5 appearances, treat F5 ML, F5 spread, and all K props as UNQUALIFIED and skip. Full-game ML and totals may still be modeled normally. Strong opener (sub-3.00 1st-inning xERA) = analyze on actual merits, not blanket skip.
25. F5 ML is a mandatory market scan for every game with a confirmed starter on both sides. Do not skip F5 because the ML or RL is already logged — F5 eliminates second-half bullpen variance and often has independent edge. Log all F5 plays ≥1.5% edge even when full-game bets exist on the same game.
26. Run line must be evaluated independently for every game — do not skip RL because ML is logged. RL at plus money (+120 or better) with model cover probability >50% is almost always worth logging regardless of juice on the ML. The CHC L10 + Taillon xERA 5.20 pattern (PIT RL +178, 4.9% edge) is the canonical example of a missed RL bet.
27. Game total Over is valid even when one team has an elite starter, provided: (a) the opposing starter is high-xERA (>5.5) AND (b) the team facing that starter scores 5.0+ R/G or has +80 run diff. In this scenario the elite starter suppresses only one half; the other half is wide open. Do not reflexively apply Rule 17 to both halves.
28. When model gap vs Pinnacle vig-free exceeds 10%, note the discrepancy but do not auto-cancel the bet. Pinnacle and Kalshi track each other — if both agree against the model, reduce size by one tier (High → Medium) but keep the bet if the qualitative case is strong. Model overconfidence is most common in lopsided records matchups where recency of performance matters.
29. F5 line verification required before logging F5 bets: the slate.json carries model F5 probabilities but not actual F5 market prices. Before finalizing any F5 bet size, note that the price is estimated and confirm the actual line on FD/DK. If actual price is >20% more expensive than estimated (e.g. model says -215, market is -280), recalculate edge before sizing.
30. **[NEW — May 26] Rule 27 is a hard gate, not a suggestion.** Before logging ANY Under at High confidence, explicitly verify: (a) neither offense is top-5 R/G AND (b) neither opposing starter is xERA >5.5. If either condition fails, the Under is BLOCKED at High confidence — downgrade to Medium/Paper or log the Over. The ATL@BOS Under ($8 High, -2.66% CLV, final 13 runs) is the canonical failure case.
31. **[NEW — May 26] Opener on either side = Under is suspect on game totals.** If either team is using an opener (avg <3 IP/start), flag the game total Under before logging. Opener + top-5 offense = do not log Under regardless of total line. This extends Rule 24 from F5/props to totals explicitly.
32. **[NEW — May 26] Same-game thesis conflict check required.** Before logging a total Under on any game where a team ML or F5 is already logged, verify the projected win score is compatible with the Under. If the ML thesis requires the favored team to score 4–5 runs and the total is ≤8.0, the Under margin is dangerously thin — skip the Under or log paper only.
33. **[NEW — May 26] Never buy ML juice above -195 when RL is available at plus money.** When a ML is -200 or worse, the RL almost always has better CLV. Compare both before logging — if RL is plus money with model cover >50%, size the RL and either skip the ML or log it paper only.
34. **[NEW — May 26] NRFI is blocked when game total is 8.0 or higher.** A total of 8+ signals both offenses are live. First-inning run probability is too elevated for NRFI to have positive expected value unless BOTH starters have verified sub-3.00 1st-inning xERA (minimum 5-start sample).
35. **[NEW — May 26] Pull prior-day box score for both offenses before logging any Under.** If either team scored 7+ runs in their last game, require both starters to have 9+ K/9 AND BB/9 <3.0 before logging the Under — otherwise skip or log paper only.
36. **[NEW — May 27] Do not log YRFI based solely on a starter's low K% when their xERA is elite (sub-3.00).** Elite xERA overrides contact-rate YRFI signals. The only exception is when the opposing offense ranks top-5 in 1st-inning run rate — in that case, the team-level signal overrides the pitcher-quality signal. Canonical failure: NYY@KC YRFI — Cole K% flagged as contact-pitcher signal, but Cole threw a shutout with 10Ks.
37. **[NEW — May 27] Kalshi divergence >15% triggers investigation, not automatic confidence reduction.** When the model and Kalshi diverge by more than 15%, dig into: recent form (last 7 and 15 games), injury/lineup news, park factors, weather, bullpen usage. Only reduce confidence if investigation reveals a specific unmodeled factor. Divergence alone means "verify harder," not "bet smaller." Canonical failure: ATL ML 5.0% edge, Kalshi at 52.5% vs model 69% — investigation should have surfaced ATL's recent pitching vulnerability rather than simply reducing size.
38. **[NEW — May 27] Season run differential is not a primary edge input.** Teams go on hot and cold streaks that distort season run diff. Use rolling 15-game R/G and the Bounceback/Regression Divergence Algorithm (MODEL_CORE) as the primary context signal. Season run diff may be referenced as background context only.
39. **[NEW — May 27] Game total Over on lopsided pitcher matchups belongs in Team Total markets, not game totals.** When an elite offense faces a weak starter (xERA >5.5) but the opposing pitcher is also elite (xERA <3.00), the game total Over is dangerous — the dominant pitcher suppresses one entire half of the scoring. In this scenario: log the elite offense's TT Over, look for alternate line Overs at plus money, and skip or paper the game total Over. Canonical failure: COL@LAD Total O8 — Ohtani held COL to 1 run, final total was 5 despite LAD scoring 4.
40. **[NEW — May 27] NRFI/YRFI requires a four-factor composite — do not log based on one or two inputs.** Required: (1) both pitchers' 1st-inning xERA with 5-start sample, (2) both pitchers' recent 1st-inning form last 5 starts, (3) both teams' 1st-inning run rate season and last 15 games, (4) park and lineup factors. A team that leads the league in 1st-inning runs is a YRFI signal regardless of the opposing pitcher. Do not log NRFI against a top-5 1st-inning offense. Canonical failure: Brewers NRFI — MIL leads league in 1st-inning runs, this was missed entirely.

---

## What Model Does Well
- Kalshi ML edge on clean verified lines
- TT analysis on clear pitcher mismatches (both sides)
- Public narrative bias identification
- Run line value on lopsided matchups (e.g. AZ -1.5 +175)
- Under when two quality starters matched
- Underdog ML spots (CLE-type) where public anchors on home field
- F5 ML on amplified xERA gaps (>1.5) — especially when one starter is elite
- Plus-money RL on heavy favorites (Rule 26/33 combo — e.g. NYY RL +102, +15.4% CLV)
- DET-style ML/RL/F5 stacks when elite starter faces vulnerable offense (3W sweep May 27)

## Still Being Refined
- K props: two-sided analysis + BB/9 + durability filters (mandatory now)
- NRFI/YRFI: four-factor composite now required — both teams' 1st-inning run rate is critical input
- Game totals with high-K starters: K rate now primary
- Doubleheader Pinnacle line matching: sanity check all >15%
- Opposing team K% by handedness: currently manual
- Opener role detection: <3 IP/start flag + Savant 1st-inning xERA lookup (now extends to totals per Rule 31)
- F5 actual market price verification: slate carries model prob only, not live F5 lines
- Elite offense vs garbage starter total: redirect to TT Over or alt lines (Rule 39)
- Same-game thesis conflict detection: ML direction vs total direction (Rule 32)
- High-juice ML value vs RL: always compare before logging -200+ ML (Rule 33)
- NRFI total threshold: blocked at 8.0+ unless dual sub-3.00 1st-inning xERA confirmed (Rule 34)
- Prior-day offense carry-over: hot offense flag on Under markets (Rule 35)
- Bounceback/regression divergence: rolling 15-game results vs underlying quality metrics (Rule 38 + MODEL_CORE)
- Kalshi large divergence: investigation required before confidence adjustment (Rule 37)
 — never primary edge driver.** Streak alone cannot push a bet from Medium to High confidence. Streak may contribute ≤0.2 weight in the factors{} object. If a bet's edge calculation relies on streak as the dominant signal (weight >0.3), downgrade to Medium regardless of calculated edge%. Canonical failures: CHC@PIT ML/RL (-$11, streak weight 0.5/1.0), ATL ML (-$8, streak weight 0.2 on top of inflated model%), MIN@CWS ML (-$4, streak weight 0.1). 7 losses 2 wins on streak-weighted bets overall.

42. **[NEW — May 28] F5 price verification is a hard pre-log gate, not a note.** Before finalizing any F5 bet at Medium or High confidence: (a) pull the actual F5 line from FD or DK, (b) recalculate edge using the live price, (c) if actual price is >20% more expensive than estimated (e.g. model says -215, market is -280), recalculate and downgrade if edge drops below tier threshold. Do not log F5 at Medium/High with only an estimated price. Paper ($1) is acceptable without confirmed price.

43. **[NEW — May 28] Use per-tier calibration factors, not flat 0.30.** High tier (≥3%): factor 0.24. Medium tier (2–2.9%): factor 0.36. Paper tier (1–1.9%): factor 0.23. The model is systematically overconfident at High and underconfident at Medium. Recalibrate after each 10-bet increment in the High tier. See MODEL_CORE Calibration table.

44. **[NEW — May 28] TT line must be confirmed at logging time.** "Verify TT line" is not an acceptable note for a Medium or High confidence bet. If TT line cannot be confirmed during analysis, log at Paper ($1) only until the line is verified. Unconfirmed TT lines were responsible for 11 PENDING bets on May 27 that could not be settled.

45. **[NEW — May 28] Apply park factors numerically before comparing projection to line.** Coors = +1.5 runs. GABP = +0.7. Oracle = -0.6. Petco = -0.5. Full table in MODEL_CORE Park Factors. Do not apply qualitative "hitter-friendly" labels without using the numeric adjustment.

46. **[NEW — May 28] xERAGap in F5 context is the strongest confirmed signal in the dataset.** 3W 0L across 3 games with xERAGap factor logged. Prioritize F5 ML on games with xERA gap >1.5 and f5Amplified: true. This is the clearest edge the model has identified so far.

47. **[NEW — May 28] Bullpen xFIP must be logged with specific tier label.** Use `bullpenVulnerable`, `bullpenElite`, or `bullpenFatigued` in factors{} — not generic `bullpen: 0.X`. Bullpen is a required input for TT Overs and game totals. Bullpen is explicitly NOT a factor for F5 edge. Full bullpen tier table and workload flag rules in MODEL_CORE Bullpen Modeling section.

48. **[NEW — May 28] xFIP is the primary pitcher input. xERA is secondary context.** All run projections use true_xFIP (regression-blended, see MODEL_CORE Section 1). xERA may be referenced for narrative context but is never the basis for edge calculation. When xFIP and xERA diverge by >0.5: if xFIP > xERA, the pitcher is outperforming true talent — fade; if xFIP < xERA, they are underperforming — buy. Log both values in analysis.

49. **[NEW — May 28] Handedness matchup scalar is required before logging K props AND before finalizing any total or TT projection.** Calculate weighted K% using today's lineup L/R composition vs pitcher's platoon splits. If no split data is available, note it and do not adjust — do not guess. A pitcher with a significant platoon disadvantage facing a lineup stacked against them must have that reflected in the effective xFIP used in the run projection.

50. **[NEW — May 28] Lineup adjustment factor is required before logging any TT bet at Medium or High confidence.** Compare today's confirmed lineup wRC+ to the team's season wRC+. Apply the adjustment per MODEL_CORE Section 1 Step 2. If lineup is not yet confirmed, log TT at Paper only. Missing a key bat (wRC+ >130) = subtract ~0.05 from offense scalar.

51. **[NEW — May 28] Projected runs (awayProjRuns, homeProjRuns) must be shown in every game analysis and logged in bets.json.** These are the foundation of all market probabilities. Do not log any bet without first calculating the run projection using the Poisson engine. This replaces the prior approach of estimating model% qualitatively from xERA alone.

52. **[NEW — May 28] True probability and model% are now the same field, derived from Poisson, not from Kalshi.** The Poisson run projection → win probability IS the model%. Kalshi is the comparison point, not the starting point. This is the fundamental upgrade: we generate probability independently, then compare to market. Do not reverse this — do not start from Kalshi and adjust.
