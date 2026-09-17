# HANDICAPPING_PLAYBOOK.md

`PLAYBOOK_VERSION: 1.0.0` · `LAST_UPDATED: 2026-09-17`

> Read this file at the start of **every** slate analysis, before looking at any
> price. Record the `PLAYBOOK_VERSION` above in the slate output so a later
> postmortem knows which methodology produced the card.
>
> This is the durable memory. It replaces re-deriving the same lessons in every
> new conversation. It is **methodology**, not a rule engine: `RULES.md` and
> `config/rules.json` still own thresholds and hard gates.
>
> Accumulated, evidence-graded lessons live in **`PLAYBOOK_LESSONS.md`**
> (structured source: `config/playbook_lessons.json`). Read that too; it is
> short.

---

## 1. THESIS FIRST, MARKET SECOND

Do **not** open a game by asking "what F5 should I bet?" That question picks the
market before the reasoning and then goes looking for support.

For each game, first decide **what the strongest baseball thesis actually is** —
a sentence about what will happen and why. Build it from:

- starting pitcher quality and this specific matchup; pitch arsenal vs. the
  opposing hitter profile where available
- handedness / platoon context; lineup quality and **confirmed** lineup changes
- offensive form, read for consistency and outlier dependence (§5)
- bullpen quality, availability, workload, and which leverage arms are actually
  free
- defense and baserunning where materially relevant; park; weather
- umpire **only** where the data quality justifies it
- travel, rest and schedule spot where material
- market prices, and where the model or the data **disagrees** with the market
- injury / news / roster context

Then write the **strongest evidence against the thesis**, explicitly. A thesis
with no stated failure mode has not been examined. When the known failure mode is
severe, it must **lower the estimated probability** — not merely appear as a
caveat in the narrative.

## 2. MARKET EXPRESSION

Only once the thesis exists: compare **every available market** for that game and
ask *"which market expresses this thesis most directly, with the least
irrelevant risk, at a price that still offers value?"*

No market family is automatically superior. These are decision rules, not
rankings:

- **F5** is often best when the edge is primarily the starting pitcher and
  bullpen variance is unwanted noise. Kalshi F5 is a **three-way** contract — a
  tie after five *loses* a YES. Price the tie; do not mention it and move on.
- **Full-game ML / run line** is better when bullpen or depth advantages
  *strengthen* the thesis rather than dilute it.
- **Team totals** isolate an offensive or matchup thesis without requiring the
  opponent to lose.
- **Game totals** fit when the scoring thesis genuinely applies to both sides.
- **NRFI / YRFI** must be judged on the specific first-inning matchup and the
  price, not on overall starter ERA.
- **Alternate / rung markets** are compared on *marginal price versus marginal
  probability*. An attractive payout is not a reason.
- **Props** are evaluated where available and researchable, respecting current
  settlement and production limitations (an unsupported family will not settle
  automatically — see `docs/SETTLEMENT_RECONCILIATION.md`).

## 3. PRICE IS PART OF THE BET

A correct baseball direction is not automatically a good wager. For every
candidate:

1. what probability does the price imply?
2. what is your own defensible probability, as a **range**, not a point?
3. how much uncertainty is in that range, and what widens it?
4. what is the **bet-up-to price** beyond which this stops being a bet?
5. if the market has already priced the edge away — **pass**.

Passing an entire game is a complete, correct answer.

## 4. DO NOT OVERSTACK ONE THESIS

If one underlying idea produces Team ML, Team F5, Team TT over, Opponent TT
under, and a pitcher prop, that is **one exposure wearing five costumes**, not
five edges.

Default behaviour: find the **single best expression**. A second correlated
wager requires a defensible reason it adds *distinct, incremental* edge — and
sizing that reflects that it is correlated.

Track concentration explicitly by: **game · team · starting pitcher · offensive
thesis · run-environment/weather thesis**. Report the largest single-thesis
share of the card. Do not let "different markets" disguise one giant position.

## 5. RECENT FORM

Never label an offense hot from rolling mean runs/game alone. A mean over a
handful of games is dominated by its largest value.

Read the whole line the slate now provides per team, e.g.
`L7: 5.1 avg | 3.0 med | 3,4,2,20,5,1,1 | 3/7 >=4 | 2/7 >=5 | max 20 (56% of L7 runs) | OUTLIER-DEPENDENT | TT overs 2/7`

Ask: repeated scoring or one explosion? What is the median? How often did it
clear the threshold you are about to buy? How did it do **against its own team
total**? Who was it facing? A window flagged `OUTLIER_INFLATED` is a warning,
not a green light.

## 6. MODEL VS HANDICAP

The production model is **evidence, not the source of truth**. The handicap may
disagree — but must say *why*, specifically. Do not count the same underlying
fact twice because it appears both as a model feature and as a narrative point.

## 7. FULL MARKET SEARCH

For every game that has **not started**, inspect the complete available market
universe. Do not stop at the first attractive market. For a strong thesis,
explicitly compare the alternative expressions before choosing.

## 8. LINEUPS AND FRESHNESS

For real-money decisions use the freshest practical price, starting-pitcher
status, lineup, weather, bullpen availability and major news. Clearly separate
*early-value analysis* from *confirmed-lineup execution*.

## 9. LEARNING WITHOUT OVERFITTING

Lessons live in `PLAYBOOK_LESSONS.md` with a status: **HYPOTHESIS** →
**SUPPORTED** → **REJECTED / RETIRED**. One wager winning or losing never
rewrites this methodology. Promote a lesson only on repeated evidence across
independent dates; record the evidence with it.

## 10. OUTPUT DISCIPLINE

Be compact. For each serious candidate:

```
GAME      AWAY@HOME
THESIS    one sentence
FOR       strongest supporting evidence
AGAINST   strongest contradictory evidence / failure mode
MARKETS   which expressions were compared
BEST      chosen market + side
PRICE     current price -> implied prob | my prob (range) | bet-up-to
EXPOSURE  correlation/concentration warning, or NONE
```

Games with no actionable edge get one line: `PASS — reason`. No essays.

**Never record a recommended wager as placed.** A recommendation becomes a real
bet only when the user explicitly confirms it and it is written through the
canonical import path.
