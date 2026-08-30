# KXMLBRFI — temporary real-money suspension

**Status: PAPER ONLY. Reversible. Research-gated reactivation.**

This is a **risk control**, not a claim that the family is permanently dead.

## Why

MLB-RSCH-0032 audited KXMLBRFI on the strongest sample this program has assembled
for any family. The family is one binary contract per game — ticker equals
eventTicker, YES means a run scored in the first inning by either team, no
threshold and no side — so **225 settled rows are 225 genuinely independent
games**, with no ladder correlation to cluster away.

| | |
|---|---:|
| Independent games | 225 |
| Dates | 21 |
| **Model Brier** | **0.2577** |
| Kalshi Brier | 0.2481 |
| **Constant base-rate Brier** | **0.2500** |
| Paired model − market delta | +0.009603, CI crossing zero |
| Calibration slope | 0.7987 |
| Leave-one-date-out | model wins **6 of 19** dates |
| Fee-aware historical result | approximately flat / slightly negative |

**The model loses to Kalshi and to a constant.** Methodology V3 returns
`STATISTICAL_SIGNAL = no` and `PREDICTIVE_MATERIALITY = no`; classification is
`MODEL_TRAILS_MARKET`.

Given Kalshi's taker fee, a family with no demonstrated predictive advantage
should not keep generating real-money-qualified recommendations merely because
opportunities exist. At the time of the audit this family carried roughly
**31.9% of live recommendation exposure**.

## What changes

Exactly one thing: **real-money qualification is withdrawn.**

A suspension gate is appended to `gates_nrfi` / `gates_yrfi` in
`scripts/build_market_ledger.py`, using the same canonical mechanism as the
existing Rule 34 and Rule 71 suspensions. That sets confidence to `None`, which
routes the row through `rejected_row(...)` instead of `accepted_row(...)`.

## What does NOT change

- The NRFI/YRFI probability model — lambda derivation, first-inning context,
  Poisson math: **untouched**.
- Edge calculation, confidence tiers, fees, Bet Up To, stake sizing: **untouched**.
- Kalshi capture, EdgeLab persistence, model evaluation, settlement: **untouched**.
- Every other market family: **untouched**.
- Historical artifacts: **never mutated**.

`modelProb` is still computed, emitted and archived on the suspended row, and the
row now also carries its ticker identity — matching the Rule 71 Game_Total
suspension precedent — so the family stays joinable to its settlement and fully
researchable. Prospective shadow validation continues to accumulate.

## Reactivation criteria

Reactivation is **research-gated and requires explicit human approval**. It must
**not** happen automatically on a date or a row count.

1. A materially larger prospective sample than the 225 games audited here.
2. Methodology V3 `STATISTICAL_SIGNAL` **and** `PREDICTIVE_MATERIALITY` both pass,
   under floors preregistered before the results are read.
3. Evidence that production beats **both** Kalshi's vig-free price **and** a
   constant base-rate predictor — beating only one is not sufficient, since the
   current failure is against both.
4. Transport: the advantage replicates across held-out dates rather than pooling.
5. Explicit human approval to restore real-money eligibility.

## Reverting

Delete the two `gates_*.append(RFI_SUSPENSION_REASON)` lines. Nothing else is
required; no state or migration is involved.
