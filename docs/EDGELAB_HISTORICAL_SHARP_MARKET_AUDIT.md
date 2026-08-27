# EdgeLab Research Lab — Historical Sharp-Market Data Feasibility + Acquisition Audit

**Status: AUDIT / RESEARCH ONLY. No production model probability, feature,
recommendation logic, threshold, confidence tier, Bet Up To logic, Kalshi
fee calculation, bankroll/staking, market eligibility, lineup gate, slate
output, risk gate, settlement, or production cron behavior was changed.
The Kalshi archive remains the execution-layer truth; nothing here
supplements or replaces it without a further, separately authorized step.**

## 1. Question this document answers

Can we obtain a trustworthy, reproducible, legally/operationally usable
multi-season (2022–2026) historical MLB sharp-market dataset — priority
benchmark Pinnacle — suitable for true market backtesting (moneyline, game
total, run line, and ideally F5 ML/total/run line)?

## 2. Existing repo capabilities (audited before acquiring anything new)

| Capability | Status |
|---|---|
| Existing Pinnacle integration | **Yes** — `scripts/merge_odds.py` already merges live pregame `pinnacleVF`/`pinnacleML`/`pinnacleRL`/`pinnacleTotalLine`/`pinnacleF5VF` fields into the daily slate, sourced from The Odds API (see `DATA_SOURCES.md`: *"Pinnacle is used as a sanity check only — it is never the bet source"*). This is a **live, current-day** integration, not a historical archive. |
| Existing sportsbook API | **Yes** — [The Odds API](https://the-odds-api.com) (`https://api.the-odds-api.com/v4`), already used by `scripts/merge_odds.py` (live odds) and `clv_update.py` (`fetch_scores`, for settlement). |
| Historical odds code | **Yes, already written, currently unused.** `clv_update.py` defines `fetch_historical()`, `fetch_historical_events()`, and `fetch_historical_event_odds()` — all three call The Odds API's `/historical/` endpoints (`regions`, `bookmakers`, `markets`, arbitrary `date=` snapshot parameter). None of the three is called from `clv_update.py`'s current `main()` flow — the module's own v6.4 changelog states *"Kalshi is the ONLY closing line source. Odds API removed entirely."* This is dead-but-present, previously-working code, not a stub. |
| API credentials expected by workflows | **Yes** — `ODDS_API_KEY`, documented in `SECRETS.md`, consumed by `.github/workflows/fetch-slate.yml` and `.github/workflows/clv-update.yml`. **Only the credential's name is reported here — its value was never read, printed, or exposed.** The credential is not available in this research/development sandbox (confirmed: `ODDS_API_KEY` unset locally, and direct network egress to `api.the-odds-api.com` is blocked by this environment's proxy — same constraint every prior MLB-RSCH milestone requiring real network access has hit, resolved the same way, via GitHub Actions). |
| Prior archived sportsbook snapshots | **No.** No per-date archive of historical odds exists anywhere in this repo — `merge_odds.py`'s Pinnacle fields are captured live into each day's slate only, not separately archived by date for later backtesting. |
| Other data provider used by the MLB model | MLB Stats API (`statsapi.mlb.com`, free, already used extensively by the entire MLB-RSCH-000{2,3,4,5} backtest track) and Baseball Savant (season-aggregate, `SEASON`-hardcoded, no per-date archive — see Milestone 2 audit, §5 below). |

**Conclusion: a historical Pinnacle pathway already exists in this repo's own codebase and already has provisioned credentials — this is the highest-priority candidate by a wide margin, and is investigated first per the mission's own priority order.**

## 3. Candidate source evaluation

### 3a. Priority 1 — The Odds API's historical endpoint (existing integration)

Already covered above. Empirical validation (coverage across 2022–2026,
actual Pinnacle presence, market/timestamp depth) is reported in §7 below
once the bounded proof-of-concept probe (§6) completes.

### 3b. Priority 2 — other providers already connected to this repo

None found. MLB Stats API has no odds data. Kalshi's own historical
endpoints (`clv_update.py`'s `kalshi_api_get`/`/historical/markets`) are
Kalshi-only — useful for the existing EdgeLab observation archive, not a
sharp-book benchmark.

### 3c. Priority 3/4 — other historical odds vendors / public datasets

Not independently investigated in depth this pass, for a deliberate reason:
the mission's own priority order places an already-integrated, already-
credentialed, already-proven-in-production source above a brand-new vendor
relationship, and §2 confirms exactly that source exists. Standing
knowledge of this market (not independently re-verified here, so treated
as background context rather than a finding): Pinnacle does not publish a
public historical odds API of its own; third-party historical MLB odds
vendors (e.g., paid archives sold by odds-data aggregators) and public
scraped datasets (e.g., community-maintained closing-line archives) exist,
but the latter are frequently **untimestamped closing-only averages across
multiple books**, which the mission explicitly warns against
(*"Do not call untimestamped scraped averages 'Pinnacle history'"*). If
§7's empirical probe shows The Odds API's historical endpoint cannot reach
the required years/depth, a follow-up, narrower investigation of a specific
paid vendor is the recommended next step (§9's fallback), not a default
substitution.

## 4. Validity requirements (what makes a source usable for real backtesting)

A source is suitable only if it establishes: an actual historical price
observed at or before game start, bookmaker identity, market identity,
both sides (or enough to de-vig), the line/threshold, reliable event/game
matching, and no future/closing information masquerading as earlier
information. The Odds API's historical endpoint, as already wired in
`clv_update.py`, satisfies this **structurally** — its `date=` snapshot
parameter requests the exact state of the market as of that timestamp
(not a retrospective closing average), and per-bookmaker `markets[].outcomes`
carries both sides with `last_update` per market. Whether it satisfies
this **in practice** for Pinnacle/2022-2026/MLB specifically is exactly
what §6/§7's proof of concept tests, not assumed.

## 5. Data-level classification (what each level unlocks)

| Level | Definition | Unlocks |
|---|---|---|
| **A** | Closing Pinnacle only | Historical model-vs-sharp-close calibration; closing residual analysis. Cannot study timing, persistence, or CLV — only a single snapshot per game. |
| **B** | Opening + closing Pinnacle | Everything in A, plus: open-to-close line movement, a coarse two-point persistence signal (did the line move toward or away from the opening number), a simple CLV proxy (closing vs. a fixed entry assumption). |
| **C** | Timestamped Pinnacle snapshots (multiple pregame checkpoints) | Everything in B, plus: historical analogs of MLB-RSCH-0006's own edge-persistence framework (model-vs-Pinnacle disagreement tracked across real checkpoints, not just open/close), realistic simulated pregame entry timing, genuine CLV (not a two-point proxy), market-movement-with/against-model analysis at the sharp-book level. |
| **D** | Multi-book timestamped consensus including Pinnacle | Everything in C, plus: whether Pinnacle specifically leads or lags consensus, a genuine multi-book de-vig (rather than a single-book mid), and robustness checks independent of any one book's own quirks. |

The Odds API's `/historical/` endpoint, if its plan tier and coverage hold
for the required years, is structurally capable of **Level C** (its
`date=` parameter is an arbitrary snapshot request, not merely open/close)
and, since `regions=us` without a `bookmakers` filter returns every US
book The Odds API tracks at that snapshot, **Level D** as well — subject
to the empirical coverage/depth findings in §7.

## 6. Model backtest bridge — historical model reconstruction compatibility

Per the mission's explicit instruction not to assume reconstructability,
this reuses Milestone 2's own PIT feature audit
(`docs/EDGELAB_MILESTONE2_PIT_FEATURE_AUDIT.md`) findings, not a new audit:
team offense (Statcast-level, production feature definition), starter
quality (season-aggregate era/xFIP/whip), and bullpen talent (season-
aggregate era/xFIP/grade) are **D UNAVAILABLE** — every one of their
source fetch scripts hardcodes the current season and overwrites a single
non-date-partitioned file, confirmed by reading each script, not inferred.
Weather and injuries/restrictions are also **D UNAVAILABLE**. Bullpen
recent usage/workload and starter recent workload/rest, by contrast, are
**A PIT_RECONSTRUCTABLE** (Milestone 2 built the mechanism; MLB-RSCH-0003
and MLB-RSCH-0004 then built and ran the actual multi-season backtests on
top of it). Team recent offensive form is similarly reconstructable
(MLB-RSCH-0005 built and ran it, finding NO_USEFUL_SIGNAL).

**No production model probability can be reconstructed for any market
family** — the actual `compute_projections()` formula depends on the
UNAVAILABLE_HISTORICALLY season-aggregate inputs above across the board,
for every family. This finding is unchanged from Milestone 2 and is not
being re-litigated here.

| Market family | Classification | Basis |
|---|---|---|
| Game ML | **C. PROXY_MODEL_POSSIBLE** | Full production formula unreachable (D-level inputs), but a genuine, non-production, PIT-safe proxy is buildable from already-built reconstructable components: bullpen recent workload (MLB-RSCH-0003), starter recent workload/rest (MLB-RSCH-0004), team recent offensive form (MLB-RSCH-0005), handedness/platoon-static identity (time-invariant, carried in every boxscore), and park factor (C RETROSPECTIVE_LIMITED per Milestone 2 — usable with a caveat). |
| F5 ML | **C. PROXY_MODEL_POSSIBLE** | Same components as game ML, restricted to the starter's own expected innings — the starter-workload/rest reconstruction (MLB-RSCH-0004) is directly relevant here, since F5 outcomes are disproportionately starter-driven. |
| Game total | **C. PROXY_MODEL_POSSIBLE** | Same component basis as game ML; team recent offensive form (MLB-RSCH-0005) and bullpen workload (MLB-RSCH-0003) both bear directly on total runs. |
| F5 total | **C. PROXY_MODEL_POSSIBLE** | Same as F5 ML — starter-driven, same reconstructable component set. |
| Team total | **C. PROXY_MODEL_POSSIBLE** | Directly what MLB-RSCH-0005 already modeled (team offense recency vs. season baseline) — the most directly reusable of all six families. |
| NRFI/YRFI (first-inning run) | **C. PROXY_MODEL_POSSIBLE**, with more new work required | The settled outcome itself is cleanly derivable from boxscore/linescore data (already used by `lib/f5_settlement.py`'s inning-level extraction) and MLB-RSCH-0006's own corpus confirms `first_inning_run` is an actively traded, settled market family — but no NRFI/YRFI-specific PIT proxy predictor has been built yet in this program (unlike bullpen/starter workload, which were). Reconstructable components (starter's own recent form, team recent offensive form) exist and could feed a first-inning-specific proxy, but that construction work has not been done. |

**No family reaches A. EXACT_PIT_RECONSTRUCTABLE.** No family is
D. NOT_RECONSTRUCTABLE either — every family has a genuine, if imperfect
and non-production, proxy-model path available today, built from pieces
this research program has already constructed and tested.

**What this means for a future model-vs-sharp-market experiment:** any
comparison against historical Pinnacle data would necessarily compare
*a PIT-safe proxy model* (assembled from MLB-RSCH-0003/0004/0005's
reconstructable components) against the sharp market — never a claim that
this reproduces what production's actual model would have said
historically. This must be stated explicitly in any such experiment's
own preregistration, exactly as MLB-RSCH-0001 already does for its own,
different reconstruction-completeness caveat.

## 7. Proof-of-concept probe: real findings

Two real dispatches. The first (run `33119942770`) failed every single
call with `URL can't contain control characters` — the `ODDS_API_KEY`
value as injected into the workflow environment carried stray whitespace,
which naive f-string URL construction (the same style `clv_update.py`'s
own historical-endpoint functions already use) embeds directly into the
URL. This was a genuine execution bug, not evidence about data
availability — fixed with a defensive `.strip()` (commit `2ea83e8`),
and the corrected rerun (run `33120551742`) is the real result below.
Raw machine output: `data/research_cache/sharp_market_probe/probe_result.json`.

### 7a. Phase 1 — coverage probe (5 credits, one `/events` call per year)

| Year | Reachable | Events (probe date) | Credits remaining |
|---|---|---|---|
| 2022 | Yes | 9 (2022-06-15) | 18,165 |
| 2023 | Yes | 7 (2023-06-15) | 18,164 |
| 2024 | Yes | 5 (2024-06-15) | 18,163 |
| 2025 | **Yes, but 0 events on this specific probe date** (2025-06-15) | 0 | 18,163 |
| 2026 | Yes | 8 (2026-06-15) | 18,162 |

**The 2025 result is a single-date anomaly, not a year-level finding.**
2025-06-15 was a real MLB game day (a Sunday in-season); every other
target year returned real events on the identical relative date. The API
call itself succeeded (no error, `reachable: true`) — it just returned
zero events for that one date, which this bounded probe did not
investigate further (a second, differently-dated check would cost one
more credit and is the natural next step, not performed here to keep
this pass strictly to its preregistered bound). **Do not read this as
"2025 is unreachable"** — every other evidence point (2022/2023/2024/2026
all reachable, ~18,000 credits of headroom) suggests it almost certainly
is, pending that one follow-up check.

### 7b. Phase 2 — small deterministic Pinnacle odds sample (2024 only; 2025 skipped per phase 1's result above)

3 dates (2024-06-10, 2024-06-11, 2024-06-12), `bookmakers=pinnacle`
explicit, markets `h2h,totals,spreads` (moneyline, game total, run line).

| Date | Games | Pinnacle present | Credits remaining |
|---|---|---|---|
| 2024-06-10 | 6 | 6 (100%) | 18,132 |
| 2024-06-11 | 11 | 10 (91%) | 18,102 |
| 2024-06-12 | 8 | 8 (100%) | 18,072 |
| **Total** | **25** | **24 (96%)** | — |

Of the 24 Pinnacle-present games, 20 (83%) carried all three requested
markets (`h2h`/`spreads`/`totals`); the remaining 4 carried two of three
(most commonly missing `h2h`) — plausibly because that specific market
had already suspended by the requested snapshot time, not a structural
gap. **Every market observed carried real, both-sided outcomes with a
real price and a real `last_update` timestamp** — e.g. (from the raw
data) Rockies @ Twins, 2024-06-10, `totals`: Over 5.5 @ +156 / Under 5.5
@ −192, `last_update: 2024-06-11T01:52:08Z`. This satisfies §4's validity
bar structurally: bookmaker identity (Pinnacle, explicit), both sides,
real line/threshold, a real timestamp — not an untimestamped scraped
average.

**One real, material data-quality caveat found**: this probe's snapshot
strategy used one blanket `10pm ET` timestamp for an entire day, not a
per-game pregame cutoff. For at least one observed game (commence
`23:40:00Z`), the captured `last_update` (`01:52:08Z`, ~2h12m later) is
close to or after that specific game's likely finish time — meaning a
day-level snapshot risks capturing a near-final or in-progress price for
early-starting games on a day with late-starting games, not a genuine
PREGAME closing line. **Before this source can be trusted for a real
"closing Pinnacle" backtest, snapshot selection must be done per-game
(a snapshot timestamp strictly before that specific game's own
`commence_time`), not per-day** — this is a real fix required in any
follow-up acquisition, not a reason to distrust the source itself; the
underlying data (price, side, timestamp) is genuine, only this probe's
snapshot-selection strategy needs refinement.

### 7c. F5 markets — not empirically tested this pass

The mission's "ideally F5 ML/total/run line" markets were **not** tested
in this bounded probe — The Odds API's own architecture requires the
more expensive per-event endpoint for these (`clv_update.py`'s own
`fetch_historical_event_odds` docstring: *"Required for additional
markets: h2h_1st_5_innings, h2h_1st_5_innings etc. Cost: 10 credits per
unique market returned"*), and this probe was deliberately scoped to the
cheap bulk endpoint to validate the core hypothesis first. Given ~18,000
credits of remaining headroom, F5 availability is very likely testable
in a small follow-up (a handful of per-event calls, well under 1,000
credits) — but is reported here as **architecturally supported, not yet
empirically confirmed**, per the mission's own "do not overstate" discipline.

### 7d. Cost actually incurred

Phase 1 (5 calls) + Phase 2 (3 calls, `bookmakers=pinnacle` bulk odds) =
**8 real API calls, ~125 credits consumed** (18,197 → 18,072 over the
course of the run, including phase 1's 5-credit cost). No billing was
altered; this consumed quota already provisioned under the existing
active plan.

## 8. Kalshi archive — unaffected

Current prospective Kalshi capture (`fetch-slate.yml`, `capture-snapshots-scheduled.yml`,
`edgelab-capture.yml`, and the rest of the EdgeLab observation pipeline)
is unchanged by this audit. Historical sharp-market data, if acquired,
would supplement the existing Kalshi archive (MLB-RSCH-0006's own
execution-layer truth) for a market-vs-market comparison — never replace
it.

## 9. Recommended path

**Historical Pinnacle data is obtainable, via the existing `ODDS_API_KEY` /
The Odds API integration, with no new vendor relationship or spend
required.** This is a strong, evidence-based conclusion — not merely
"the source exists," but "24 of 25 real games sampled had real, both-sided,
timestamped Pinnacle prices."

**Recommended next steps, in order:**

1. **Fix the snapshot-selection strategy** (§7b) — move from one blanket
   daily snapshot to a per-game pregame cutoff before pulling anything
   larger. This is a small, contained fix to the probe script itself, not
   a new acquisition.
2. **Resolve the 2025 single-date anomaly** (§7a) — one more cheap
   `/events` call on a different 2025 date. High confidence this resolves
   trivially, given every other year and the abundant remaining credit
   balance (~18,000).
3. **Confirm F5 market availability empirically** (§7c) — a small number
   of per-event calls (well under 1,000 credits) against the same 2024
   sample dates, now that the core hypothesis is validated.
4. **Only then**, begin a bounded, still-not-full-multi-season pull (e.g.,
   one full month per target year, opening + closing + a few intermediate
   snapshots) — explicitly NOT a full 2022–2026 download in one step,
   consistent with this audit's own validation-before-volume discipline.

**This unlocks, once step 4 completes:** Level C (timestamped Pinnacle
snapshots) research — historical model-vs-sharp-close calibration,
closing residual analysis, open-to-close movement, and (per §6) a
PIT-safe **proxy model** comparison (never a claim of reproducing
production's actual historical probability) for game ML, F5 ML, game
total, F5 total, and team total. NRFI/YRFI is reachable too, but needs
new proxy-predictor construction work beyond what MLB-RSCH-0003/0004/0005
already built.

**Recommended next experiment if acquisition succeeds**: a new,
separately-preregistered MLB-RSCH milestone — "Proxy Model vs. Historical
Pinnacle Close" — comparing the MLB-RSCH-0003/0004/0005 reconstructable-
component proxy against Pinnacle's closing line for team_total and game
total first (the two families with the cleanest existing reconstructable
components), explicitly labeled as a proxy-vs-market study, never a
production-model reconstruction claim.

**Recommended fallback if a specific year/market later proves genuinely
unreachable**: narrow the study to the years/families that ARE confirmed
(this probe already confirms 2022–2024 and 2026 at the coverage-probe
level, with 2024 empirically validated at the full odds level) rather
than substituting a lower-quality untimestamped source — per §3c, a
scraped closing-average dataset is explicitly not an acceptable substitute
for what this probe has already shown the existing integration can deliver.
