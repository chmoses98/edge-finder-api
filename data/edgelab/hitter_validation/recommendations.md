# Hitter Projection Engine — Recommendations

Status: **recommendations only. Nothing in this document has been
implemented.** No formula, threshold, weight, prior, recommendation
logic, staking rule, or settlement semantics has been changed. See
`summary.md` for the full evidence this is based on.

**Updated 2026-08-18**: re-evaluated against the fully-settled 1,872-row
corpus (2026-08-14's 89 previously-unresolved rows are now settled —
`summary.md`'s "What changed after settling 2026-08-14" callout has the
full before/after). **Classification is unchanged.** The newly-settled
date was, if anything, the worst-performing date in the archive, and
overall ROI/calibration moved in the *unfavorable* direction — there is
no basis here to upgrade, and per this audit's own instruction, the
sample-size increase alone (+89 rows) is never by itself grounds to
upgrade. The core pattern (0–5pp edges promising, 5pp+ edges an
anti-signal, `hitter_total_bases` strongest, bottom-of-order/longshot
weakness) replicated on the new date rather than being contradicted by
it — see below for what specifically firmed up vs. weakened.

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

The overall picture is still not uniformly bad. Overall calibration
error is still small (−1.54pp, was −0.80pp) and inside its own
confidence interval. Monotonicity is still perfect (0/526 ladder
violations) — the engine's internal probability logic is sound. A
genuinely large, `CALIBRATED`-status subset (the <35% bucket, n=1,395 —
still three-quarters of the whole archive) is still well-calibrated
(+0.72pp) and still simulated-profitable, though less so than before
(+0.98% ROI, was +3.7%, from `roi_by_probability_bucket.json`). One of
four market families (`hitter_total_bases`) still posts clearly
positive simulated ROI; a second (`hitter_hits`) that was previously
positive is now narrowly negative (−1.4%, was +6.3%) after the
2026-08-14 settlement — see `summary.md` §5. This is still not the
profile of a broken model, but it is a weaker picture than before on
several of the specific numbers that argued for "not A."

### Why not C or D (production candidate / production-ready)

1. **The independent-evidence base is 4 calendar dates**, up from 3.
   Every `CALIBRATED`-status number in this audit is drawn from
   2026-08-13/08-14/08-15/08-16. Four dates still cannot support a
   real-money-testing decision, however good or bad the numbers look on
   those four days — this is squarely the "be conservative, do not
   recommend D (or C) based on small samples" instruction this audit was
   given, and it applies just as much at 4 dates as it did at 3.
2. **The edge signal is still actively counterproductive above ~5pp**
   (`summary.md` §6): larger declared edges are still *more*
   overconfident and *less* profitable, not more, across a real
   498–216-row sample at each larger bucket (was 480–204) — the pattern
   got slightly *worse*, not better, on the new data. A system whose own
   confidence signal points the wrong direction cannot be trusted to
   size or select bets, which is a precondition for any production
   candidacy, however narrow.
3. **CLV is still flat-to-slightly-negative overall** (45.6% positive,
   avg −0.015¢, unchanged — 2026-08-14 added no new CLV-eligible rows)
   and still does not improve with declared edge — still no evidence the
   engine identifies value ahead of the market.
4. **The newly-settled evidence skews unfavorable, not favorable.**
   2026-08-14 is now the single worst-performing date in the archive
   (`summary.md` §11: −43.8% ROI, −16.6pp calibration error, both far
   outside the range of the other three dates) — the opposite of a
   reason to loosen the classification.

### Why B, not A

The <35% bucket and the `hitter_total_bases` family are still real,
`CALIBRATED`-status, positive-ROI signals — not cherry-picked after the
fact (they were the natural, pre-specified cuts this audit's own
methodology required: probability bucket, market family, edge bucket).
`hitter_hits` no longer clearly belongs in this list (§5). These
remaining signals are promising enough to justify **continued research
investment**, specifically to grow the sample past 4 dates before
revisiting classification — but not promising enough, on 4 dates of
evidence (one of which was the worst date measured), to justify even a
capped real-money trial today.

---

## Specific next steps (research/operational — NOT model-tuning)

These do not touch model math. They close the data gaps this audit
found so a future audit can actually answer the readiness question with
enough evidence:

1. ~~**Settle 2026-08-14.**~~ **DONE (2026-08-18).** Ran the existing,
   already-production `scripts/edgelab/settle_markets.py` via
   `edgelab-postgame.yml` workflow_dispatch in the real GitHub Actions
   environment (the sandbox this audit was originally built in had
   `statsapi.mlb.com` network access policy-blocked) — closed all 89
   previously-ungraded rows at zero new-code risk, exactly as planned.
   9 YES / 80 NO, full settlement evidence on every row.
2. ~~**Put the hitter engine on a recurring schedule.**~~ **DONE, as of
   this PR's merge.** `.github/workflows/hitter-snapshot-scheduler.yml`
   now runs every 15 minutes, 13:00–23:45 UTC + 00:00–05:45 UTC, and
   captures all five checkpoints
   (`T_MINUS_90`/`T_MINUS_60`/`T_MINUS_30`/`LINEUP_CONFIRMATION`/
   `HITTER_CLOSING_WINDOW`) automatically, append-only and idempotent —
   see `docs/HITTER_CHECKPOINT_COVERAGE_FIX.md`. This is the single
   largest lever for getting past "a handful of calendar dates of
   evidence"; every day going forward now adds new, real, independent
   data points at zero modeling risk with no manual trigger required.
   **Next audit rerun should specifically check whether the archive has
   grown past 4 dates and whether the <35%-bucket / low-edge-bucket
   pattern keeps replicating as it grows** — that replication check,
   not a bigger N on the same handful of dates, is what would justify
   moving from B toward C.
3. **Fix the 2026-08-15 filename/date-mismatch pattern** at the source
   (`scripts/run_standalone_hitter_research.py`/
   `scripts/fetch_standalone_pregame_context.py`): stamp the snapshot
   filename from the actual wall-clock UTC capture instant, not the
   `--date` argument, so a future filename-based date parse (by this
   audit's own re-run, or any other tool) is never silently wrong. Purely
   a naming/provenance fix — no pricing logic involved.
4. ~~**Re-run this audit** after 2026-08-14 settles.~~ **DONE
   (2026-08-18)**, alongside items 1–2 above — see `summary.md`'s "What
   changed after settling 2026-08-14" callout for the full before/after.
   The <35%-bucket / low-edge-bucket promising pattern **did replicate**
   on the new date (still `CALIBRATED`, still positive ROI), though at a
   weaker magnitude than the 3-date figure. **Keep re-running this audit
   (idempotent, safe to run anytime) as the now-automatically-growing
   archive (item 2) adds new dates** — the next meaningful checkpoint is
   watching whether the pattern holds past 4 dates, not re-deriving it
   from the same 4.
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
6. **Investigate `hitter_rbis`'s ROI/CLV divergence** (§5): still
   best-in-class CLV (+0.73¢, 91% positive) but worst ROI (−10.25%,
   essentially unchanged by settlement) of any family. This pairing is
   still unusual enough to warrant a dedicated look once CLV coverage
   for this family (still only 68/271 rows, `DESCRIPTIVE_ONLY` —
   2026-08-14 added no new CLV-eligible `hitter_rbis` rows) grows.
7. **Do not size, rank, or gate anything by declared edge magnitude** in
   any future prototype recommendation logic for this engine until
   finding 5 is understood — an edge-sorted "top picks" view built on
   this engine today would, per this archive, actively surface the
   worst-performing bets first.

## Explicitly not recommended right now

- Wiring the hitter engine into any recommendation, staking, or
  risk-gate path.
- Building a capped/limited real-money hitter-prop pilot, even for the
  <35%-bucket or `hitter_total_bases` subset — 4 calendar dates is still
  not enough replication for real money, regardless of how good or bad
  those four days look; this holds even more clearly now that one of
  the four (2026-08-14) was the worst date measured so far.
- Any model-formula, shrinkage-weight, or prior change — this audit
  was explicitly scoped to grading and measurement only, and every
  finding above is a *hypothesis for future research*, not a validated
  fix.
