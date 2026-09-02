# Manual Betting Calibration Review — 2026-09-02

Research/reporting only. No production model, staking engine, or bet-selection
behavior is changed by this document. Companion to the 2026-08-31/09-01/09-02
manual postmortem import (`importBatchId`s `manual-postmortem-20260831-v1`,
`manual-postmortem-20260901-v1`, `manual-postmortem-20260902-partial-v1`) and to
`docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md` §13 (Kalshi payout-multiplier
evidence).

## 1. What can be calibrated today, and what cannot

**Cannot be calibrated: manual P(win) vs. outcome.** Every one of the 300
MANUAL-source bets in this repository's ledger (as of this session) has
`manualFairProbability = null` — 0/300. No manual pregame probability estimate
has ever been recorded at entry time, for these 24 newly-imported bets or any
prior manual bet. This is by design (`build_manual_bet_record`'s
`manual_fair_probability` is never inferred), and per this task's own
instruction, none is backfilled here — a post-hoc "I probably thought 55%"
estimate would be manufactured precision, not evidence. **Reliability/Brier/
log-loss calibration against manual probability estimates is therefore not
computable from this repository today, for any date.**

**Cannot be cleanly checked: production model calibration on the manual-bet
population.** Of 300 MANUAL-source bets, 181 carry a `recommendationId` (i.e.
were confirmed production recommendations) but only 5 carry a populated
`modelFairProbability` — too small and too oddly-selected a sample (whatever
caused those 5 specifically to have model-probability linkage) to support any
claim about production model calibration. None of the 24 bets imported this
session carry a `recommendationId` at all — this week's manual workflow
explicitly stopped copying `modelProb`/`mlEdge`/market-ledger fields into
manual decisions (see §"New handicapping framework" below), so there is
structurally nothing to compare a production probability against for this
enlarged sample. **No claim of measurable production miscalibration is made
here** — the data to support or refute one doesn't exist yet in this
selected population.

**Can be, and is, computed: exact realized economics.** Every one of the 24
newly-imported bets carries `confirmedReceiptReturn`/`confirmedReceiptNetProfitLoss`
(via `confirm_realized_return`, source=`MANUAL_POSTMORTEM_RECEIPT`) and, where
an archived settlement exists (all 12 straight 8/31 bets), an independently
objective `result`/`status`. Family/date/threshold-bucket win-rate and ROI
slicing (§3-4 below) is real, reproducible from `data/edgelab/bets/bets.jsonl`,
and not dependent on any unrecorded probability.

## 2. Going-forward schema proposal (NOT implemented in this session)

The task asks for a way to preserve, going forward:
`manualProbabilityLow`/`manualProbabilityMid`/`manualProbabilityHigh`,
`displayedExecutionMultiplier` (**implemented this session** — see
`shareCardEvidence.shareCardDisplayedMultiplier`, §13 of the Kalshi doc),
`minimumAcceptableMultiplier`, `recommendedStake`, `actualUserStake`, and a
`sizingOverride` flag/reason.

This document deliberately does **not** add `manualProbabilityLow/Mid/High` to
`placed_bet.schema.json` in this same session, for two reasons: (a) there is no
concrete near-term producer of that data yet (no manual bet in this batch had a
recorded probability *range*, only single displayed probabilities/multipliers,
which are execution evidence, not a handicap estimate), and (b) the mission's
own "MODEL/PRODUCTION CHANGE GUARDRAIL" cautions against speculative schema
growth ahead of an actual consumer. `manualFairProbability` (existing field)
already covers a single-point manual estimate; a **range** is a genuinely new
concept. Proposed shape for a future PR, once the manual workflow is actually
producing probability ranges at bet time:

```
"manualProbabilityLow": {"type": ["number","null"]},   // 0-1, conservative end
"manualProbabilityMid":  {"type": ["number","null"]},   // 0-1, point estimate (may mirror manualFairProbability)
"manualProbabilityHigh": {"type": ["number","null"]},   // 0-1, optimistic end
"minimumAcceptableMultiplier": {"type": ["number","null"]},  // breakeven-plus-margin multiplier threshold used at decision time
"recommendedStake": {"type": ["number","null"]},        // half-Kelly-derived suggestion, pre-override
"actualUserStake": {"type": ["number","null"]},         // DUPLICATES `stake` by design -- makes an override visually undeniable without re-deriving it from sizingOverride
"sizingOverride": {
  "type": ["object","null"],
  "properties": {
    "overridden": {"type": "boolean"},
    "reason": {"type": ["string","null"]}
  }
}
```

All nullable/additive, same pattern as `shareCardDisplayedMultiplier`. Until
implemented, this same information can be (and was, for the 9/2 SD F5 bet)
captured losslessly in `rationale` + a Postmortem's `structuredFindings` —
the sanctioned fallback the task itself names ("If changing the canonical bet
schema is too invasive, use a sanctioned postmortem/research evidence
structure instead").

## 3. F5-total market research

**Archive coverage is excellent and settlement is already fully implemented.**
Across all 33 archived market-partition dates in this repository (2026-08-01
through 2026-09-02), **every single date** has `inning_total`/`F5` markets
archived — 3,129 F5-total markets total, ~7 threshold rungs per game
(`data/edgelab/markets/*.jsonl[.gz]`). `lib.edgelab.settlement.settle_market`
already settles this family correctly from `game_outcome["periodScores"]["F5"]`
using Kalshi's `>= N` integer-rung semantics
(`docs/EDGELAB_KALSHI_TOTAL_LADDER_SEMANTICS.md`) — confirmed working: the
already-archived `data/edgelab/settlements/2026-08-31.jsonl` contains 84 settled
`inning_total`/F5 records for that one date alone.

**Zero real-money manual wagers have ever been placed on this family.** A
full-ledger scan (`marketFamily == "inning_total" and marketHorizon == "F5"`)
returns **0** bets, before or after this session's import. This confirms the
task's framing: F5 totals are a genuinely underexplored market, not merely an
under-bet one — there is no manual outcome history to calibrate against at
all, only archive coverage to backtest against.

**What this enables, and what it doesn't, right now:** the archive + settlement
pipeline supports a rigorous *backtest* of F5-total closing-price calibration
today (e.g. via `scripts/edgelab/run_market_price_calibration_audit.py`-style
tooling, scoped to `inning_total`/F5) without needing a single new real wager —
this is squarely a "prospective evidence" research task, not blocked on
anything missing. It does **not** yet support a *manual-handicap* calibration
check (no manual probability estimates exist for this family, same as
everywhere else — see §1), and this session does not run that backtest (out of
scope for a postmortem-import task; flagged as a proposed investigation).

**Concrete situations worth targeting, once backtested** (not evaluated here,
per the task's explicit "not automatic authorization for production bets"):
two-weak-starter games (F5 over), two-strong-starter games (F5 under), and the
one-weak-starter-plus-strong-offense case where an F5 total may express the
thesis more cleanly than a three-way F5 side (no tie-tax exposure).

## 4. Opener/bulk role capture — already implemented, gap was procedural

The 9/2 process note (Brad Lord's low average-IP/start initially read as
"starter uncertainty" rather than recognized as an opener) is **not** a missing
capability in this repository. Checked directly:

- `data/slates/2026-09-02/authoritative.json`'s `pitcherSavant` block for Brad
  Lord already carries `"openerRole": true` (alongside `"avgIPperStart": 1.69`,
  `"openerQualified": false` — a separate small-sample flag, `appearances >= 5`
  per `lib/research/first_inning_context.py`'s `MIN_APPEARANCES_THIN`) —
  correctly captured by the production data pipeline for the real 9/2 slate.
- `lib.research.pitcher_workload_projection` already has a dedicated,
  tested opener model: `survival_curve(..., opener=True)` uses
  `OPENER_CAP_OUTS=8` / `OPENER_BASE_SURVIVAL=0.90` /
  `OPENER_POST_CAP_SURVIVAL=0.10` instead of the standard traditional-starter
  survival curve, precisely so a short expected outing that's an opener isn't
  penalized the same way as workload uncertainty in a traditional starter.
- `lib.kalshi_probability_adapters` already wires
  `opener=bool(ctx.get("pitcherOpenerRole", False))` through to that model, fed
  from `scripts/discover_kalshi_mlb_markets.py`'s
  `ctx["pitcherOpenerRole"] = savant.get("openerRole", False)`.

**So the gap was procedural, not architectural:** the automated Kalshi-market
pitcher-prop discovery path already consults `pitcherSavant.openerRole`; the
*manual* F5/team-total handicapping workflow (a human/Claude reading
`avgIPperStart` directly) did not, for this one game. **Proposed maintainable
improvement:** add "check `pitcherSavant.openerRole` (and `openerQualified`)
before drawing any conclusion from `avgIPperStart` alone" as a checklist step
in the manual handicapping workflow docs, and, for F5 handicapping
specifically, estimate the combined OPENER + EXPECTED BRIDGE/BULK-through-five
using the same `project_pitcher_workload(..., opener=True)` function the
automated path already calls, rather than hand-rolling a fresh estimate per
game. This deliberately avoids building a new, brittle, manually-maintained
role table — the data and the model both already exist; they just need to be
*consulted* in the manual workflow.

## 5. Kelly / bet-sizing framework — research only, not adopted in production

For a Kalshi position at displayed payout multiplier `M` (gross, pre-fee — see
`docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md` §13 on why a multiplier is never
assumed exactly fee/contract-adjusted) and estimated true win probability `p`:

```
full Kelly fraction f* = (p*M - 1) / (M - 1)
```

Worked example against the one 9/2 data point, purely illustrative (M=1.97
displayed, gross, unverified against fees):

- Breakeven `p` at M=1.97 is `1/1.97 ≈ 50.76%`.
- If the independent pregame handicap's conservative-end estimate were, say,
  `p_low = 55%` (illustrative only — no such range was actually recorded for
  this bet; see §1/§2), `f* = (0.55*1.97 - 1)/(1.97 - 1) ≈ 0.1134*0.5 ≈` roughly
  11% full Kelly (half-Kelly ≈ 5-6%) of bankroll — already near or above the
  task's own "normal single-bet cap around 5% of bankroll" even before
  applying a fee-aware haircut. The **actual** $60/$630 ≈ 9.5% stake used was
  therefore -- independent of how the bet resolved -- larger than even an
  aggressive half-Kelly estimate would support at a 55% conservative
  probability, and the user's own stated override rationale (Auburn alumnus)
  is explicitly *not* a valid input to this formula. This is exactly the
  sizing lesson the task itself already identifies from this one bet.

**Operating concept (research proposal, not implemented as a gate):**
conservative `p` range → compare to observed multiplier → breakeven after
Kalshi fee-aware execution economics (`lib.edgelab.kalshi_fees`) → full Kelly →
half Kelly → cap at ~5% of bankroll normally → an explicit user
override/justification required above that. **Not built into any production
staking engine here** — one data point (and a small manual sample overall)
cannot justify that; this section documents the research and worked formula
only, per the guardrail.

## 6. Team-total history — a real reconciliation gap, reported honestly

The task's supplied "Team total history through 9/2" (14-21, exact risk
$589.98, P/L -$140.43) does **not** match what this repository's own ledger
returns for the same shape of query, and this is reported rather than forced:

- All `marketFamily == "team_total"` bets with `source == "MANUAL"`:
  **69 positions, 33-36, $1,233.95 risked, -$147.89 net** (spanning
  2026-08-03 through 2026-09-01 — 21 distinct dates).
- The same scoped to only bets linked from an actual `Postmortem` record
  (i.e. bets that went through a completed structured postmortem import, the
  narrowest plausible "audited" subset): **293 total positions across all
  families** (not team-total-specific-filtered further here), itself far
  larger than the task's cumulative "132/133 positions through 9/2" figure.

**This session could not identify, in the time available, the exact scoping
rule that reproduces the user's stated 14-21/35/$589.98 team-total figure or
the 132/133 all-family figure from repository content alone** — plausible
explanations include a personal tracking spreadsheet with a narrower date
window or inclusion rule than "every MANUAL-source/postmortem-linked row ever
ingested," legacy-ingested rows (dating back to 2026-06-17) that were never
part of the user's own running count, or a `trackingType`/paper-vs-real
distinction not reflected in this ledger's `trackingType=None` default. Per
the task's explicit instruction, this gap is **not** forced to match --
flagged here as a proposed investigation (reconcile the user's personal
running tally against `data/edgelab/bets/bets.jsonl` team-total rows
explicitly, once the intended scope is confirmed) rather than silently
adjusted or hidden.

**What the enlarged repository sample DOES support:** team totals are a
persistently losing family in this repository's own MANUAL-source data
(33-36, -$147.89, close to breakeven win-rate but net-negative — consistent
with a small structural payout disadvantage rather than a catastrophic
selection failure) — directionally consistent with the task's own "-23.80%
ROI, demands a much stricter manual gate" framing even though the exact
denominator differs. This supports (without over-fitting to 2 days'
additional evidence) the qualitative process change already adopted this
week: team-total legs require their own independent scoring thesis and an
explicit threshold-ladder comparison, not a side opinion mechanically paired
with one headline total. No production market suspension is proposed --
consistent with the task's explicit instruction not to blindly suspend a
market from sample evidence alone.

## 7. Correlation / portfolio tagging

`correlationGroups` (array field, already on `PlacedBet`) is the sanctioned
place to record `DUPLICATE_THESIS` / `MODERATELY_CORRELATED` /
`INDEPENDENT_THESIS` tags going forward -- no schema change needed. This
session did not retroactively populate `correlationGroups` on the 24 newly
imported bets (would be a judgment call best made at bet time, not
reconstructed after the fact) but DID record the same classification in each
date's Postmortem `gameLevelConcentration` entries (e.g. 8/31's NYY ML + NYY
5+ runs and AZ F5 + AZ 4+ runs legs, both tagged DUPLICATE_THESIS in
`data/edgelab/postmortems/2026-08-31/postmortem.json`; BAL ML + BAL 6+ tagged
MODERATELY_CORRELATED). Proposed forward convention: populate
`correlationGroups` directly on same-game multi-leg bets at import time going
forward (e.g. `["GAME:2026-08-31_NYY_LAA", "THESIS:DUPLICATE"]`), so a future
portfolio-risk query doesn't need to re-derive it from postmortem text.

## 8. Summary

Nothing in this document changes production model probabilities, staking, or
market-selection behavior. It documents: what calibration evidence exists
today (very little — realized economics only, no manual probabilities ever
recorded), what F5-total and opener/bulk research already has strong
infrastructure support for (archive + settlement + a working opener model,
all pre-existing), where this session's own totals genuinely don't reconcile
to the task's supplied figures (team-total and all-time manual-history
denominators) and why that is reported rather than forced, and a Kelly-sizing
research framework with one honestly-labeled illustrative (not authoritative)
worked example.
