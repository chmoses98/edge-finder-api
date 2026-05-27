# RULES.md — Model Adjustment Rules

1. Never bet Under when one team has elite offense vs struggling starter — take run line or TT instead
2. NRFI reliable with elite starters on **both** sides only — not just one ace
3. Always use pitcher's last 3 starts specifically — never rely on season ERA alone
4. Verified same-day starters mandatory — wrong pitcher errors invalidate entire analysis
5. Skip ATL -1.5 run line — take ML or TT instead (close games + extras too frequent)
6. TJS return pitchers often come back sharp — do not assume rustiness
7. YRFI only with specific 1st-inning pattern evidence — not just ERA
8. K props require two-sided matchup analysis — see MODEL_CORE strikeout checklist
9. Kalshi edge direction: YES = away team. Sanity check all gaps >10%
10. Pitcher prop bets require same-day starter confirmation — search "[pitcher] starting today" before logging
11. Same-day starter confirmation required for ALL prop bets (probable ≠ confirmed)
12. Team streak data weighted equally with season record — W3 team on 24-27 record is playing like a winner now
13. ATL TT Over requires specific game-day reason beyond "good offense" — weak starter, favorable park, wind out, or hot recent stretch
14. Pinnacle doubleheader line gaps >15% are likely data errors — verify manually before acting
15. Rain = postponement risk only — never use as scoring suppressor
16. K rate is primary input for total projection, not ERA — 9+ K/9 suppresses totals regardless of ERA
17. Elite starter (sub-2.50 ERA) on either side = lean Under on game total — BUT only if the opposing offense is average or below. If the opposing offense is elite (top-5 R/G, top-5 run diff) AND the opposing starter is below replacement (xERA >6.0), the Under lean is OVERRIDDEN. Log the Over or skip entirely.
18. TT Over requires recent offensive form — cold offense + shaky pitcher does not automatically produce runs
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
31. **[NEW — May 26] Opener on either side = Under is suspect on game totals.** If either team is using an opener (avg <3 IP/start), flag the game total Under before logging. Opener + top-5 offense = do not log Under regardless of total line. The NYY@KC Total U8.5 (KC opener Bailey Falter, final 16 runs) is the canonical failure case. This extends Rule 24 from F5/props to totals explicitly.
32. **[NEW — May 26] Same-game thesis conflict check required.** Before logging a total Under on any game where a team ML or F5 is already logged, verify the projected win score is compatible with the Under. If the ML thesis requires the favored team to score 4–5 runs and the total is ≤8.0, the Under margin is dangerously thin — skip the Under or log paper only. Canonical example: MIN ML (WIN, 5-3) + MIN@CWS Total U7.5 (LOSS, final 8 runs).
33. **[NEW — May 26] Never buy ML juice above -195 when RL is available at plus money.** When a ML is -200 or worse, the RL almost always has better CLV. Compare both before logging — if RL is plus money with model cover >50%, size the RL and either skip the ML or log it paper only. Canonical example: MIL ML -202 (-3.52% CLV) vs MIL RL which would have been available at a far better price.
34. **[NEW — May 26] NRFI is blocked when game total is 8.0 or higher.** A total of 8+ signals both offenses are live. First-inning run probability is too elevated for NRFI to have positive expected value unless BOTH starters have verified sub-3.00 1st-inning xERA (minimum 5-start sample). If either starter's 1st-inning xERA is unavailable or above 3.00, skip NRFI entirely when total ≥ 8.0. Canonical failure: ATL@BOS NRFI -140 with a total of 8 — BOS scored in the 1st.
35. **[NEW — May 26] Pull prior-day box score for both offenses before logging any Under.** If either team scored 7+ runs in their last game, require both starters to have 9+ K/9 AND BB/9 <3.0 before logging the Under — otherwise skip or log paper only. Hot offense carry-over is a real suppressor of Under value. Canonical failure: WAS scored 10 runs on May 25, then scored 6 on May 26 — WAS@CLE Under 7.5 lost.

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

## Still Being Refined
- K props: two-sided analysis + BB/9 + durability filters (mandatory now)
- YRFI: 1st-inning specific evidence required
- Game totals with high-K starters: K rate now primary
- Doubleheader Pinnacle line matching: sanity check all >15%
- Opposing team K% by handedness: currently manual
- Opener role detection: <3 IP/start flag + Savant 1st-inning xERA lookup (now extends to totals per Rule 31)
- F5 actual market price verification: slate carries model prob only, not live F5 lines
- Elite offense vs garbage starter total: Rule 17 override logic (Rule 27/30)
- Same-game thesis conflict detection: ML direction vs total direction (Rule 32)
- High-juice ML value vs RL: always compare before logging -200+ ML (Rule 33)
- NRFI total threshold: blocked at 8.0+ unless dual sub-3.00 1st-inning xERA confirmed (Rule 34)
- Prior-day offense carry-over: hot offense flag on Under markets (Rule 35)
