# PLAYBOOK_LESSONS.md

`PLAYBOOK_VERSION: 1.0.0` · `LAST_UPDATED: 2026-09-17`

> Generated from `config/playbook_lessons.json` by `python3 scripts/playbook_lessons.py --render`. Edit the JSON, not this file.

Read alongside `HANDICAPPING_PLAYBOOK.md`. A lesson is standing guidance only at **SUPPORTED**. A **HYPOTHESIS** is worth mentioning in a handicap and is never a rule. Nothing here is promoted, demoted or retired by a single wager's result.

## Promotion policy

- **toSupported** — At least 3 findings on 3 DISTINCT dates pointing the same way, or one registered experiment with an out-of-sample result. A single wager winning or losing never promotes, demotes, or retires anything.
- **toRejected** — Explicit contrary evidence of the same weight required for promotion. Never a single result.
- **review** — scripts/playbook_lessons.py --audit recounts every lesson's postmortem evidence from data/edgelab/postmortems/ and flags any lesson whose claimed evidence no longer clears its own promotion bar.

## SUPPORTED (6)

### Kalshi F5 is a three-way contract: a tie after five loses a YES

`id: f5-three-way-tie-tax` · tags: F5, TIE_TAX, PROBABILITY_ESTIMATION · evidence dates: 3 (2026-09-02 → 2026-09-07)

When the thesis is 'this team is better through five', the F5 YES still loses a 0-0 or level game after five innings. Price the tie explicitly into the fair probability rather than noting it as a caveat. In a low-scoring matchup with two good starters, the tie branch is a material share of the distribution.

<details><summary>Evidence</summary>

- 2026-09-02 WSH F5 YES lost to a 0-0 game after five (ATL@WSH).
- 2026-09-06 ATL F5: Atlanta eventually won but did not lead after five.
- 2026-09-07 PHI F5: Philadelphia won 1-0 on an eighth-inning run; F5 lost to a 0-0 tie.

</details>

### Correlated wagers around one thesis are one position, not several edges

`id: one-thesis-many-markets-is-one-exposure` · tags: CORRELATED_EXPOSURE, PORTFOLIO_CONCENTRATION · evidence dates: 4 (2026-08-17 → 2026-08-23)

Team ML + Team TT over + Team F5 on the same game is a single exposure expressed three times. Pick the best expression. A second correlated wager needs a stated reason it adds distinct edge, and sizing that reflects the correlation. Report the largest single-thesis share of the card before submitting it.

<details><summary>Evidence</summary>

- 2026-08-20 five F5 positions were 70.7% of slate exposure ($82 of $116) and went 0-5.
- 2026-08-23 MIL ML (-$10) and MIL 4+ (-$25) failed together: one thesis, two losses.
- 2026-08-17 STL ML won while STL over 4.5 lost -- directionally right, net -$10.09 across the pair.
- 2026-08-21 SEA ML and SEA 4+ both won, and is recorded as ONE validated thesis, not two.

</details>

### No market family is uniformly superior; expression selection is the edge

`id: best-expression-beats-favourite-family` · tags: EXPRESSION_SELECTION, HORIZON_SELECTION · evidence dates: 6 (2026-08-24 → 2026-09-06)

The postmortem record contains the same number of cases where a team total beat an F5 side as where a full-game ML beat a team total. Choose per-thesis, not per-habit: match the market horizon to where the edge actually lives (starter-based -> shorter horizon; depth/bullpen-based -> full game; offence-only -> team total).

<details><summary>Evidence</summary>

- 2026-09-04 the Detroit team total was a more robust expression than the Detroit F5 side.
- 2026-08-28 PHI ML was superior to forcing F5 because the later-game edge mattered; BAL ML was cleaner than BAL TT.
- 2026-08-24 TB ML (+11.37) won while TB 5+ runs lost -- the ML expressed the same thesis with less threshold risk.
- 2026-09-06 LAA/PIT F5 <=3 avoided late-game variance and was the right horizon.

</details>

### Price discipline: buy near-even prices, pass when the edge is priced away

`id: price-discipline-and-passing` · tags: PRICE_DISCIPLINE · evidence dates: 4 (2026-08-17 → 2026-08-22)

Several recorded profitable positions were bought around 50-55 cents rather than laying large favourite prices, and deliberate passes on expensive prices are recorded as correct decisions. A correct baseball read at a bad price is a pass, and a pass is a complete answer.

<details><summary>Evidence</summary>

- 2026-08-17 'price discipline remained valuable across the slate: several successful positions were bought around 50-55 cents rather than laying large favourite prices'.
- 2026-08-17 the decision to pass expensive Dodgers F5 / Sugano-under pricing is recorded as a correct decision, not a missed bet.

</details>

### Rolling mean runs/game is not evidence of a hot offence

`id: recent-mean-runs-is-not-form` · tags: OFFENSIVE_FORM, SMALL_SAMPLE, PROBABILITY_ESTIMATION · evidence dates: 3 (2026-09-02 → 2026-09-07)

Read the distribution, not the mean: median, threshold-clear counts, the max and its share of the window, and team-total line-relative performance. A window flagged OUTLIER_INFLATED means one game is carrying the average. Separately, recent-form DEVIATION has no demonstrated out-of-sample predictive value, so it is context for a handicap -- never a model input.

<details><summary>Evidence</summary>

- MLB-RSCH-0005 (12,800 development team-games, 2022-2026): recent runs/game deviation vs next-game runs is NO_USEFUL_SIGNAL; the frozen candidate regression beat the control by 0.0008 MAE.
- MLB-RSCH-0036: offensive-form CONSISTENCY and market-relative form, same leakage-free walk-forward split -- see docs/RESEARCH_OFFENSIVE_FORM.md.
- 2026-09-07 MIA F5: 'recent form should not overwhelm the downside distribution' after Eury Perez allowed a career-high 7 runs in four innings.

</details>

### A named failure mode must lower the number, not just appear in the narrative

`id: known-failure-mode-must-move-the-probability` · tags: PROBABILITY_ESTIMATION, OVERCONFIDENCE · evidence dates: 3 (2026-08-30 → 2026-09-07)

Where a postmortem records a loss whose cause was explicitly identified BEFORE the bet, the failure was pricing, not analysis. If the thesis names a severe risk (volatility, control, short leash, a hostile platoon), the fair probability must move enough to reflect it or the wager does not clear.

<details><summary>Evidence</summary>

- 2026-09-07 CLE/BAL F5 <=3: 'Cantillo's volatility/control was explicitly the known danger' and he gave up 4 runs in just over two innings -- 'when the known failure mode is this severe it must reduce the fair probability enough, rather than merely being mentioned as a narrative caveat'.
- 2026-09-06 recorded as a PROBABILITY_ESTIMATION miss on a thesis whose risk was already named pregame.
- 2026-08-30 recorded under OVERCONFIDENCE / SMALL_SAMPLE: conviction outran the evidence behind it.

</details>

## HYPOTHESIS (3)

### F7 may carry most of a full game's variance without a full game's pricing

`id: f7-as-a-default-horizon` · tags: F7, HORIZON_SELECTION · evidence dates: 2 (2026-08-25 → 2026-09-07)

Treat F7 as a horizon that must be justified by the specific pitching-usage thesis (e.g. a starter expected to finish seven), never as a default middle ground between F5 and the full game. Evidence is currently one bad day plus one good selection -- watch it, do not rule on it.

<details><summary>Evidence</summary>

- 2026-08-25 F7 was 0-4 for -95.00 on $95 risk: 'carries most of a full game's variance without a full game's pricing'.
- 2026-09-07 LAD F7 was recorded as excellent horizon selection when the opposing starter's workload was limited -- the opposite sign, on one day.

**Promotion blocked by:** Only two dates, pointing in opposite directions. Needs repeated same-direction evidence before it becomes guidance.

</details>

### A parlay leg failure costs twice; size as a small overlay

`id: parlays-are-overlay-only` · tags: PARLAY, CORRELATED_EXPOSURE · evidence dates: 3 (2026-08-24 → 2026-08-29)

When a thesis already appears as a straight wager, adding it to a parlay makes one failure cost the straight wager AND the parlay. Keep parlays small and clearly labelled as overlay, and never let a parlay's payout justify a leg that would not clear on its own.

<details><summary>Evidence</summary>

- 2026-08-24 'CIN F5 was a loss and also killed the four-leg parlay -- one thesis failing cost twice'.
- 2026-08-25 both four-leg parlays lost; each had winning legs but neither cleared.
- 2026-08-29 explicitly recorded: '+93.30 parlay profit is not evidence that parlays are due'.

**Promotion blocked by:** Three dates, but the sample of parlays placed is very small and one day was strongly profitable. Directionally consistent, not yet decided.

</details>

### Do not draw market-family conclusions from the reconciled sample

`id: family-level-win-rates-are-not-yet-evidence` · tags: SMALL_SAMPLE, STAKE_SIZING · evidence dates: 2 (2026-08-26 → 2026-08-29)

Across all reconciled postmortems the largest single market family holds single-digit graded wagers. Any statement of the form 'F5 works' or 'team totals do not work' is currently unsupported noise. Judge each wager on its own thesis and price until the family samples are materially larger.

<details><summary>Evidence</summary>

- Aggregated across every reconciled postmortem, the largest family sample is roughly 7 graded wagers.
- 2026-08-26 'moneylines carried the largest family risk ($95) and went 1-2' -- family SIZING, not family edge, drove the day.
- 2026-08-29 team totals (3-6) erased most of the F5/ML advantage on one day.

**Promotion blocked by:** This lesson is about the ABSENCE of evidence; it is promoted only if the samples stay small while conclusions keep being drawn, and retired once family samples are large enough to support real conclusions.

</details>
