# Hitter Projection Engine — Recommendations

Status: **recommendations only. Nothing in this document has been
implemented.** No formula, threshold, weight, prior, recommendation
logic, staking rule, or settlement semantics has been changed. See
`summary.md` for the full evidence this is based on.

## Readiness classification: **B — RESEARCH-ONLY WITH PROMISING SUBSETS**

Full framework (from the audit mission):

- A. NOT READY — poor/unstable calibration, no reliable CLV, negative
  ROI, substantial data-quality issues.
- **B. RESEARCH-ONLY WITH PROMISING SUBSETS — overall not ready, but
  some market families / edge ranges show promising calibration and
  CLV.** ← **this audit's conclusion**
- C. LIMITED PRODUCTION CANDIDATE — specific constrained subsets
  statistically credible enough for tightly capped testing.
- D. PRODUCTION-READY — strong prospective evidence across calibration,
  CLV, and simulated ROI with adequate sample size.

### Why not A (NOT READY)

The overall picture is not uniformly bad. Overall calibration error is
small (−0.8pp) and inside its own confidence interval. Monotonicity is
perfect (0/526 ladder violations) — the engine's internal probability
logic is sound. A genuinely large, `CALIBRATED`-status subset (the
<35% bucket, n=1,327 — three-quarters of the whole archive) is
well-calibrated (+1.5pp) and simulated-profitable (+3.7% ROI, from
`roi_by_probability_bucket.json`). Two of four market families
(`hitter_total_bases`, `hitter_hits`) post positive simulated ROI. This
is not the profile of a broken model.

### Why not C or D (production candidate / production-ready)

1. **The independent-evidence base is 3 calendar dates.** Every
   `CALIBRATED`-status number in this audit is drawn from
   2026-08-13/08-15/08-16 only. Three dates cannot support a
   real-money-testing decision, however good the numbers look on those
   three days — this is squarely the "be conservative, do not recommend
   D (or C) based on small samples" instruction this audit was given.
2. **The edge signal is actively counterproductive above ~5pp**
   (`summary.md` §6): larger declared edges are *more* overconfident and
   *less* profitable, not more, across a real 480–204-row sample at each
   larger bucket. A system whose own confidence signal points the wrong
   direction cannot be trusted to size or select bets, which is a
   precondition for any production candidacy, however narrow.
3. **CLV is flat-to-slightly-negative overall** (45.6% positive, avg
   −0.015¢) and does not improve with declared edge — no evidence the
   engine identifies value ahead of the market.
4. **89 real projections (2026-08-14) are still ungraded** — the
   evidence base isn't even fully assembled yet.

### Why B, not A

The <35% bucket and the `hitter_total_bases`/`hitter_hits` families are
real, `CALIBRATED`-status, positive-ROI signals — not cherry-picked
after the fact (they were the natural, pre-specified cuts this audit's
own methodology required: probability bucket, market family, edge
bucket). They are promising enough to justify **continued research
investment**, specifically to grow the sample past 3 dates before
revisiting classification — but not promising enough, on 3 dates of
evidence, to justify even a capped real-money trial today.

---

## Specific next steps (research/operational — NOT model-tuning)

These do not touch model math. They close the data gaps this audit
found so a future audit can actually answer the readiness question with
enough evidence:

1. **Settle 2026-08-14.** Run the existing, already-production
   `scripts/edgelab/settle_markets.py` (or
   `scripts/edgelab/backfill_player_prop_settlement.py --date
   2026-08-14`) — closes 89 rows this audit could not grade, at zero
   new-code risk.
2. **Put the hitter engine on a recurring schedule**, the way
   `model-snapshot-scheduler.yml` already does for game-level markets.
   Today it only runs when a human manually triggers
   `kalshi-price-check.yml`; this is the single largest lever for
   getting past "3 calendar dates of evidence" — every additional
   archived date is a new, real, independent data point at zero
   modeling risk. (This is an operational/workflow change, not a model
   change — it runs the exact same, unmodified engine more often.)
3. **Fix the 2026-08-15 filename/date-mismatch pattern** at the source
   (`scripts/run_standalone_hitter_research.py`/
   `scripts/fetch_standalone_pregame_context.py`): stamp the snapshot
   filename from the actual wall-clock UTC capture instant, not the
   `--date` argument, so a future filename-based date parse (by this
   audit's own re-run, or any other tool) is never silently wrong. Purely
   a naming/provenance fix — no pricing logic involved.
4. **Re-run this audit** (`scripts/research/build_hitter_projection_audit.py`,
   idempotent, safe to run anytime) after each new archived date lands,
   and specifically re-check whether the <35%-bucket / low-edge-bucket
   promising pattern **replicates** on new dates, or whether it was
   itself a 3-date artifact. That replication check, not a bigger N on
   the same 3 dates, is what would justify moving from B toward C.
5. **Investigate the edge-bucket inversion before touching model
   internals.** §6 of `summary.md` is the most actionable, most
   surprising finding here: larger declared edge correlates with *worse*
   outcomes. Two candidate explanations worth investigating with more
   data (not fixing yet, per this audit's explicit no-tuning
   instruction):
   - The model's own probability, not the market's price, is usually
     the less-reliable side of a large edge (i.e. the model is
     overconfident specifically in the situations where it disagrees
     most with the market) — consistent with the probability-bucket
     overconfidence finding in §4.
   - Small-sample Monte Carlo noise (`monteCarloStderr` is carried on
     every row but not yet cross-analyzed against edge size in this
     audit) inflates some declared edges artificially — worth a
     dedicated follow-up cut of `graded_projections.jsonl` by
     `monteCarloStderr` once more dates exist.
6. **Investigate `hitter_rbis`'s ROI/CLV divergence** (§5): best-in-class
   CLV (+0.73¢, 91% positive) but worst ROI (−10.5%) of any family. This
   pairing is unusual enough to warrant a dedicated look once CLV
   coverage for this family (currently only 68/269 rows,
   `DESCRIPTIVE_ONLY`) grows.
7. **Do not size, rank, or gate anything by declared edge magnitude** in
   any future prototype recommendation logic for this engine until
   finding 5 is understood — an edge-sorted "top picks" view built on
   this engine today would, per this archive, actively surface the
   worst-performing bets first.

## Explicitly not recommended right now

- Wiring the hitter engine into any recommendation, staking, or
  risk-gate path.
- Building a capped/limited real-money hitter-prop pilot, even for the
  <35%-bucket or `hitter_total_bases` subset — 3 calendar dates is not
  enough replication for real money, regardless of how good those three
  days look.
- Any model-formula, shrinkage-weight, or prior change — this audit
  was explicitly scoped to grading and measurement only, and every
  finding above is a *hypothesis for future research*, not a validated
  fix.
