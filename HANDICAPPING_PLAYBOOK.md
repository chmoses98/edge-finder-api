# HANDICAPPING_PLAYBOOK.md

`PLAYBOOK_VERSION: 1.2.0` · `LAST_UPDATED: 2026-09-18`

> Read this file at the start of **every** slate analysis, before looking at any
> price. Record the `PLAYBOOK_VERSION` above in the slate output so a later
> postmortem knows which methodology produced the card.
>
> This is the durable memory. It is **methodology**, not a rule engine:
> `RULES.md` and `config/rules.json` still own thresholds and hard gates.
>
> Accumulated, evidence-graded lessons live in **`PLAYBOOK_LESSONS.md`**
> (structured source: `config/playbook_lessons.json`). Read that too; it is
> short.

---

## 0. VOCABULARY — five different questions, never blurred

| Term | Question it answers |
|---|---|
| **BETTING-ELIGIBLE GAME** | May this game be on the real-money card at all? |
| **FULL MARKET UNIVERSE** | Every Kalshi contract attributable to that game |
| **PRODUCTION MODEL SUPPORT** | Does our production model price this market? |
| **MANUAL HANDICAPPING ELIGIBILITY** | Can I evaluate it from the evidence I have? |
| **AUTOMATIC SETTLEMENT SUPPORT** | Can the repo grade it automatically afterwards? |

No production adapter ≠ invisible (axis 3, not 4). No automatic settlement ≠
forbidden — flagged, because it needs manual reconciliation. And an
unconfirmed-lineup game never becomes real-money because its markets look good.

## 1. GAME ELIGIBILITY COMES FIRST

Before any thesis, decide **which games you may bet**. A game is eligible for
normal real-money analysis only when:

1. it has **not started**, and
2. **BOTH official lineups are confirmed** (`lineupConfirmedOfficial` on the
   away *and* home team), and
3. the existing data-integrity / staleness gates pass.

Probable/projected lineups are **never** confirmed. Failing games stay
archived, visible and researchable — the early-value surface — and must not
produce a real-money recommendation. `scripts/build_handicapping_card.py`
applies this gate; `researchOnly` rows carry `realMoneyEligible: false`.

**Then**, for each eligible game, inspect the **complete** market universe.

## 2. THESIS FIRST, MARKET SECOND

Do not open a game by asking "what F5 should I bet?" — that picks the market
before the reasoning and then goes looking for support.

Decide **what the strongest baseball thesis actually is**, from: starter
quality and this matchup; arsenal vs. opposing hitter profiles;
handedness/platoon; lineup quality and confirmed changes; offensive form read
for consistency (§6); bullpen quality, availability, workload and leverage
arms; defense/baserunning, park, weather where material; umpire only where the
data justifies it; travel/rest; prices and where model or data **disagrees**
with them; injuries and news.

Then write the **strongest evidence against** it. A thesis with no stated
failure mode has not been examined, and a *material* failure mode must **lower
the estimated probability** — not merely appear as a narrative caveat.

## 3. MARKET EXPRESSION

Compare **every available market** for the eligible game and ask: *which
expresses this thesis most directly, with the least irrelevant risk, at a price
that still offers value?*

A market family is a **payoff structure, not an edge**. Choose the expression
whose irrelevant risk is smallest for this thesis:

- **F5** removes bullpen variance *and* adds tie risk — Kalshi F5 is
  **three-way**, so a tie after five loses a YES. Price the tie.
- **Full-game ML / run line** when bullpen or depth advantages *strengthen* the
  thesis.
- **Team totals** isolate an offensive thesis without needing the opponent to lose.
- **Game totals** when the scoring thesis applies to both sides.
- **NRFI/YRFI** on the specific first-inning matchup and price, not starter ERA.
- **Alternate/rung markets** on marginal price vs. marginal probability; an
  attractive payout is not a reason.
- **Props** where researchable; check settlement support before recording one.

No family is automatically superior, and the reconciled sample is far too small
to claim otherwise.

## 4. PRICE AND BANKROLL ARE PART OF THE BET

For every candidate: what probability does the price imply? Your own
defensible probability, **as a range**? What widens it? The **bet-up-to
price**? If the market has priced the edge away — **pass**.

**Price shape is not edge.** There is no reason to prefer a cheap contract: a
70¢ contract at a genuine 80% fair probability beats a 52¢ contract at 53%. The
gap between fair probability and price is the only thing that matters.

**Dollar sizing needs two things, and one does not imply the other:** a fresh
sizing-authoritative bankroll (`bankroll.sizingAllowed`) AND the numeric value
in your hands (`bankroll.numericBankrollAvailable`).
`bankroll.consumerSizingVerdict.verdict` says which you have: the committed
card is public, so its amount is redacted even when `sizingAllowed` is true.

Missing either: handicap normally, give edge, confidence and **bet-up-to
fractions** (no amount needed), present **no dollar stakes**, and say which is
missing. Never substitute a remembered or derived number.

## 5. DO NOT OVERSTACK ONE THESIS

Team ML + Team F5 + Team TT over + Opponent TT under + a pitcher prop is **one
exposure wearing five costumes**. Find the single best expression; a second
correlated wager needs a stated reason it adds *distinct* edge, and sizing that
reflects the correlation.

Track concentration by **game · team · starting pitcher · offensive thesis ·
run-environment/weather thesis**, and report the largest single-thesis share of
the card.

## 6. RECENT FORM

Never label an offense hot from rolling mean runs/game alone — a mean over a
handful of games is dominated by its largest value. Read the whole line the
slate provides, e.g.
`L7: 5.1 avg | 3.0 med | 3,4,2,20,5,1,1 | 3/7 >=4 | 2/7 >=5 | max 20 (56% of L7 runs) | OUTLIER-DEPENDENT`

`HOT`/`COLD`/`OUTLIER_INFLATED` are **descriptive context only**: the
registered research (MLB-RSCH-0005, MLB-RSCH-0036) found **no demonstrated
out-of-sample predictive value** in recent offensive form, and no production
weight uses it. It stops you being fooled by one explosion; it is not an edge.

## 7. MODEL VS HANDICAP

The production model is **evidence, not the source of truth** — and it prices
only 11 market rows. The handicap may disagree, but must say *why*,
specifically. Do not count the same fact twice because it appears both as a
model feature and as a narrative point.

## 8. LINEUPS, FRESHNESS, EXECUTION

Use the freshest practical price, starter status, lineup, weather, bullpen
availability and news. Keep *early-value analysis* (unconfirmed lineups,
research only) separate from *confirmed-lineup execution*.

## 9. LEARNING WITHOUT OVERFITTING

Lessons carry a status (**HYPOTHESIS → SUPPORTED → REJECTED/RETIRED**) *and* an
evidence class:

- **MECHANICAL** — a structural fact about a contract, a portfolio or
  arithmetic. True without a betting sample; promoted by showing the mechanism.
- **EMPIRICAL_EDGE** — a claim that something *wins, loses or predicts*.
  Promoted **only** by a registered, leakage-free, out-of-sample experiment. No
  number of same-direction wagers can promote a claim about money.

One result never rewrites this methodology.

## 10. OUTPUT DISCIPLINE

```
GAME      AWAY@HOME            (betting-eligible only)
THESIS    one sentence
FOR       strongest supporting evidence
AGAINST   strongest contradictory evidence / failure mode
MARKETS   which expressions were compared
BEST      chosen market + side
PRICE     current price -> implied prob | my prob (range) | bet-up-to
SIZE      fraction of bankroll; dollars ONLY if you hold the number
          (else "no dollar sizing: <consumerSizingVerdict>")
EXPOSURE  correlation/concentration warning, or NONE
```

Games with no actionable edge get one line: `PASS — reason`. No essays.

**Never record a recommended wager as placed.** A recommendation becomes a real
bet only when the user explicitly confirms it and it is written through the
canonical import path.
