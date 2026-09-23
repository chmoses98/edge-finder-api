# MRV results: where money leaks out of the Kalshi MLB ecosystem

**RESEARCH ONLY. Every number below is TIER B (exploratory). No hypothesis
reached CANDIDATE. No production logic, threshold, eligibility, staking,
bankroll, recommendation or execution path changed.**

Program design: `docs/EDGELAB_MRV_MARKET_STRUCTURE_PROGRAM.md`.
Registry with full results blocks:
`data/edgelab/research_artifacts/market_structure/hypothesis_registry.json`
(rendered `HYPOTHESIS_REGISTRY.md` beside it). Runner artifacts in the same
directory. Registry fingerprint of frozen fields
`9625d7363dd29b0ca54fa48879578c73e2729e9eaeb6cc5c6aa8a02904bcf841`.

## 1. The answer in one paragraph

Money leaks out of the Kalshi MLB ecosystem in one place: **the taker's
crossing cost, most of which is the exchange fee**. Volume-weighted over
1,985,438 pregame taker prints (407 million contracts, 391 games, 29 August
dates), a taker pays 0.58¢ of half-spread plus 1.68¢ of fee per contract
and then sees the fair mid drift 0.33¢ *in their favour* by the last
pregame minute. Net: **−1.93¢ per contract** for the taker, of which the
exchange keeps 1.68¢ and makers keep roughly nothing (maker realized spread
at +30 minutes: −0.04¢ after the conservative 0.0175 maker fee, +0.4¢ if
makers are not charged). There is no arbitrage, no persistent cross-market
incoherence, no exploitable lag behind sportsbooks at the resolution we can
observe, and no price band, spread bucket or liquidity bucket that pays
after fees. Prices are coherent to the cent, minute by minute.

## 2. Data actually used

| Evidence | Span | Rows | Tier |
|---|---|---|---|
| Recovered 1-minute candles + trade tape (exchange record) | 08-02 → 08-31 ex 08-17 | 13,121 contracts, 6.9M candles, 12.9M prints, 391 games | B |
| Prospective corpus (research branch): order books + 4-book odds + MLB state | 09-02 → 09-22, 3–8 runs/day | 46,508 books, 2,088 odds rows, 889 state rows | B |
| Observation archive + settlements | 09-01 → 09-21 | 17,540 last-pregame quotes, 274 games, 21 dates | B |
| Settlements for portfolio structure | 08-02 → 09-21 | 664 games | B |

TIER A (research-qualified COMPLETE): two snapshots, both 2026-09-22
morning, no settled rows. **No confirmatory inference was possible.**

Two data-integrity facts discovered on the way, both material for any
future consumer of the archive:

1. **The archived total-ladder settlement rule switched on 2026-09-01.**
   Reconstructing each game's final total from its two team-total ladders
   (whose semantics never changed) and reading the archived game-total
   result at the rung equal to that total gives NO on all 167 August
   games (old `> N` rule) and YES on all 138 September games (Kalshi's
   `>= N` rule). The ALPHA-0001 corrected mapping must therefore be
   applied only to game dates ≤ 08-31; applying the rung shift to
   September would double-correct. `settlements.py` in this program is
   date-aware for exactly this reason.
2. **Observation-archive price units switched on 2026-09-10** (cents such
   as 47.0 before, dollars such as 0.47 after, same field name). A
   consumer that assumes one unit silently drops or corrupts half the
   September rows.

## 3. Results by family (all TIER B)

### A. Cross-market coherence (MRV-COH-001..006) — REJECTED / DATA_BLOCKED

At 1-minute resolution across 94,231 pregame game-minutes:

| Constraint | Pairs / event-minutes | Pre-fee violations | Post-fee violations |
|---|---:|---:|---:|
| Game-total ladder monotonicity | 1,348,014 | 0 | 0 |
| F5-total ladder monotonicity | 248,534 | 0 | 0 |
| F5 total ≥ N ⇒ game total ≥ N (never tested before) | 182,721 | 0 | 0 |
| Team total ≥ N ⇒ game total ≥ N (08-02..07) | 102,092 | 0 | 0 |
| Wins by ≥ k ⇒ wins (08-02..07) | 72,205 | 0 | 0 |
| F5 three-way sum | 16,031 | 668 | 0 |
| F3 three-way sum | 10,039 | 2,644 | 0 |
| F7 three-way sum | 8,032 | 938 | 0 |

Adjacent total rungs sit 6–14¢ apart at executable prices (modal −9¢ of
slack), so a within-ladder inversion never came within 2¢ of happening.
Three-way books show asks summing to 98–99 or bids to 101–102 for 4–26 %
of minutes; three taker fees (~5¢) exceed that every time. COH-006
(near-arbitrage convergence) is DATA_BLOCKED for the best possible reason:
no episode exists to study. This extends ALPHA-0001 Family B (hours
cadence, zero violations) to minute resolution and to the cross-horizon
constraint: **there is no arbitrage, transient or persistent.**

### B. Cross-family lead/lag (MRV-LL-001..004) — no tradable lead

Partial slope of the follower's next-h move on the leader's last-h move,
controlling the follower's own move, 5-minute grid T-240..T-5, game-cluster
CI90, 12 registered tests:

- ML → F5 ML: h5 −0.31 [−0.69, 0.10]; **h15 +0.93 [0.04, 1.87] (BH)**;
  h30 −0.53 [−0.65, 0.17]. The h15 survivor is unstable (first date-half
  1.69, second 0.05) and the leader's mean |move| is 0.14¢, so the implied
  follower move is ~0.13¢ against a 1.5–2¢ F5 spread. Price-discovery
  noise, not a trade. Status EXPLORATORY, not CANDIDATE.
- F5 ML → ML: no survivor.
- Totals ladders: only 19–38 games carry a fixed two-sided rung set
  through the window → DATA_BLOCKED (below the 60-game floor).

### C. External price discovery (MRV-XV-001..004)

Prospective corpus, 205 games / 20 dates, Kalshi ML books joined to
same-run sportsbook odds (Pinnacle, DraftKings, FanDuel, BetMGM):

- **Kalshi ML and Pinnacle no-vig agree to 0.42pp on average and never
  disagree by 2pp** in 451 game-captures. The preregistered 3pp trigger
  (ALPHA-0002 D3) fired **zero** times → MRV-XV-002 REJECTED.
- Convergence exists: Kalshi moves 0.47¢ per 1pp of Pinnacle disagreement
  by the last pregame capture (CI [−0.02, 0.90], not a BH survivor) and
  0.40¢ per 1pp of 4-book consensus disagreement (CI [0.15, 0.66], BH
  survivor, 172 games). Given the fitted slope, a hold-to-settlement taker
  breaks even only at |d| ≈ 5.9–6.8pp, a level never observed. **Kalshi
  is not lagging the sharp market at hours resolution; it is tracking
  it.** Timing caveat: the odds leg is fetched minutes after the run's
  Kalshi leg, so a sliver of each same-run disagreement is already stale.
- Totals: the collector's 400-book-per-run cap captured only 5 game-total
  ladders in 21 days → MRV-XV-004 DATA_BLOCKED.

### D. Information arrival (MRV-INFO-001/002) — DATA_BLOCKED

490 lineup-posted and 5 pitcher-change first-seen events, but the event
time is bounded only by the previous collector run: median bound 861–904
minutes. A repricing-lag test needs ≤ 30-minute resolution. The collector
requested 10-minute cadence and GitHub delivered 3–8 runs/day; this is the
same scheduling failure documented in `MLB_ALPHA_0002_SCHEDULE_HEALTH.md`.

### E. Time to first pitch (MRV-TTP-001) — descriptive

Median spread is 1¢ in every lead bucket inside T-240; mean spread rises
from 1.15¢ (T-120..60) to 4.1¢ beyond T-240 (6.5¢ for F5/F3/F7 winners).
Taker fee at the ask averages 1.44¢. Two-sided share ≥ 99.7 % inside
T-240. Volume per contract-minute triples from T-240 to T-5. Consistent
with the night-before study: early entry pays a wider spread; there is no
cheap window.

### F. Liquidity / attention (MRV-LIQ-001) — cost, not opportunity

BUY-YES at the last pregame ask, 274 games / 21 dates: wide books (≥ 4¢)
return **18.0pp less** than tight books (CI [−26.3, −8.8], BH survivor);
low- vs high-volume terciles −2.9pp (CI [−9.8, +3.4]). Thin books are
where takers are hurt most, not where they are paid.

### G. Price buckets (MRV-PB-001/002)

- **MLB-RSCH-0026 forward score** (preregistered; 17,540 rows, 274 games,
  settle > 08-28): the frozen β = 0.9833 shrink is *worse* than the raw
  fair mid by +0.000053 Brier (CI [+0.000009, +0.000097]). The
  preregistered rerun (TRAIN 09-01..10, VAL 09-11..21) fits β = 0.974
  (CI [0.91, 1.05], includes 1) and VAL delta +0.000138 (CI excludes 0 on
  the wrong side). Both gates fail. **LEVEL 0 stands; the late-August
  favourite-longshot pattern did not persist.** Population is broader
  than RSCH-0026's (all settled non-prop rows with a two-sided last
  pregame quote, not only production-evaluated tickers).
- **Price-band ROI** (73 family × side × band cells, USD 10 taker, both
  sides): 16 BH survivors, every one negative except a 4-row cell.
  Pooled family sides: buying NO on totals ladders loses 13–19 % (game
  total −19.2 % CI [−29.5, −8.0]; F5 total −18.7 %; team total −12.7 %)
  while the mirrored YES sides are +4.8 %, +2.3 %, −0.2 % with CIs
  covering 0 → a September over-bias that does not clear fees on the
  buy side and is a one-month slate composition until proven otherwise.
  **Zero cells meet the candidate bar.**

### H. Maker vs taker (MRV-EXEC-001..003)

- **Depth is not the constraint.** 34,819 pregame books: median 750
  contracts at the top ask (1,474 for moneylines); a USD 100 YES order
  fills within 2¢ of the top in 82 % of books (98 % for moneylines);
  USD 250 is unfilled in 3.7 %.
- **The leak, per contract, volume-weighted (August tape):**

| Family | prints | half-spread | fee | drift → taker (last) | taker net (last) | maker net +30 (fee 0.0175) |
|---|---:|---:|---:|---:|---:|---:|
| all | 1,985,438 | 0.58¢ | 1.68¢ | +0.33¢ | **−1.93¢** | −0.04¢ |
| game_result | 1,407,956 | 0.52 | 1.69 | +0.33 [0.28, 0.39] | −1.89 | −0.09 |
| game_total | 369,712 | 0.66 | 1.67 | +0.32 [0.25, 0.39] | −2.01 | −0.01 |
| inning_result | 82,661 | 0.83 | 1.65 | +0.22 [0.14, 0.29] | −2.27 | +0.84 [0.22, 1.49] |
| inning_total | 50,057 | 0.84 | 1.66 | +0.53 [0.43, 0.66] | −1.97 | −0.45 |
| team_total | 11,614 | 0.91 | 1.61 | +0.59 [0.45, 0.73] | −1.93 | +0.10 |
| winning_margin | 63,438 | 0.60 | 1.65 | +0.41 [0.23, 0.60] | −1.83 | +0.17 |

  Takers are mildly informed in every family (drift toward the taker, BH
  survivors in all six, same sign in both date halves), most so more than
  4 hours out (+0.58¢ ML, +1.76¢ F5 totals), but never by more than the
  fee. Makers earn the half-spread and give back most of it to informed
  flow; no family survives BH on maker net, and fill probability is
  unknowable from the tape (ALPHA-0002 measured 6–16 %).

### I. Synthetic distributions — subsumed

With zero coherence violations at 1-minute resolution and every derivative
(F5 totals, team totals, margins) sitting inside its dominance bounds by
6–14¢, a fitted joint run distribution cannot find a mispriced derivative
that the raw constraints missed; the model-derived comparison was already
rejected by RSCH-0022/0024 and ALPHA-0002 Family E. Not re-run.

### J. Ensemble signals — not attempted

No individual signal cleared fees, so no combination was searched
(a combination search with zero fee-positive inputs is the search space
the program's rules forbid).

### K. Portfolio (MRV-PORT-001) — descriptive

662 games with all six canonical contracts: φ(ML, F5 ML) = 0.58, φ(ML,
RL −1.5) = 0.71, φ(total 8+, F5 total 5+) = 0.53, φ(total 8+, home TT 4+)
= 0.47, φ(ML, total) ≈ −0.06. Six one-contract positions on one game
behave like **2.3 independent bets**. Any future same-game portfolio must
size on that, not on six.

## 4. Candidates

**NONE.** Nothing met the frozen bar (BH survivor + post-fee CI excluding 0
+ ≥ +1 % + 60 games / 10 dates + same-sign halves + mechanism + no
midpoint fill). Nothing is frozen for prospective testing, because
freezing a spec with no fee-positive development evidence would only spend
future data.

## 5. Methodological decisions made during the run (recorded, not hidden)

- p-values for BH are the **percentile-bootstrap** p (consistent with the
  CI90 gate) rather than the null-centred p first coded; both are stored on
  every test. The change was made after seeing that heavy-tailed slope
  bootstraps produced null-centred p < 0.01 with CIs covering 0. It cannot
  create a candidate (the CI gate is unchanged); it can only stop a
  CI-covering test from being called a survivor.
- Trade-tape and ROI inference use a per-game aggregated ratio bootstrap
  (identical point estimate, O(games) per resample) after the row-level
  bootstrap over 2M prints proved infeasible.
- The lead/lag fixed rung set was relaxed from 80 % to 50 % of pregame
  minutes after the 80 % rule produced zero rows; the result is still
  DATA_BLOCKED.

## 6. Risks and reasons any of this could be wrong

- One month of exchange record, three weeks of prospective corpus, one
  season, one regime. Every effect size is small enough that a different
  month could flip signs on the price-band and lead/lag tables.
- Settlement truth is derived from the MLB Stats API; Kalshi's own result
  is still never archived.
- The prospective collector never met its cadence gate; everything that
  needs minutes (information arrival, sharp lag, book imbalance) remains
  untestable from history.
- The exchange record's ticker universe is conditioned on the observation
  archive; markets the archive never saw are absent, not evidence.
- Fee schedule is web-corroborated, not read from Kalshi; maker fee per
  series unknown (both legs reported).
- Frozen-forward scorer (`lib/edgelab/research/frozen_forward_scorer.py`)
  clamps the candidate to [0.01, 0.99] via `_logit` but the raw market
  reference only to 1e-9 inside the log-loss; the FORWARD_SUPPORTS
  log-loss figures in `EDGELAB_FROZEN_FORWARD_SCORECARD.md` should be
  re-checked with a symmetric clamp before anyone leans on them (this
  program's PB-001 used a symmetric clamp and found the opposite sign).

## 7. What prospective instrumentation would actually be needed

Nothing in this PR changes capture. The program's blocked hypotheses need
three things the existing collectors do not deliver, listed for the owner's
decision rather than implemented, because each touches a collector whose
capture universe is part of another frozen program's identity:

1. **Delivered cadence ≤ 10 minutes** for the research collector. GitHub
   cron will not do it (5.6 % coverage on V1, still 3–8 runs/day on V2);
   an always-on external runner is the only known fix.
2. **Series-balanced book capture.** The 400-book cap starves KXMLBTOTAL /
   SPREAD / TEAMTOTAL (30 / 118 / 211 books in 21 days). A per-series
   quota, or a second pass for the per-game ladders, unblocks XV-004 and
   the September coherence replication — but changing the universe also
   changes what C01-F5REV / C03-BOOKIMB see, so it is an owner decision.
3. **Per-leg fetch timestamps** on every book, quote and odds row (today
   only the run's `capturedAt` is stored), so sportsbook-vs-Kalshi
   disagreement is ordered to the second instead of the run.

Until (1) exists, MRV-INFO-*, D01-SHARPLAG, I01-LINEUP and C03-BOOKIMB
stay DATA_BLOCKED / PROSPECTIVE_ONLY, and there is no experiment worth
freezing against them.
