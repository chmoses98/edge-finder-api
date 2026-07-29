# PROJECTION_AUDIT.md

Model Performance Phase 1 (Market Audit) — Parts 1 and 4.

**This document is discovery and design only.** No production formula,
calibration factor, confidence threshold, bet-sizing rule, or
real-money eligibility behavior was changed to produce it. Every
finding below was independently verified by reading the actual
production source referenced, not inferred from prior documentation.

---

## Part 1: Repository audit — files read in full this phase

- `scripts/build_market_ledger.py` (evaluation core: `p_team_wins()`,
  `poisson_pmf()`, `vig_free_2way()`, `build_edge_fields()`,
  `confidence_from_edge()`, the `ML_Away`/`ML_Home` and
  `F5_ML_Away`/`F5_ML_Home` evaluation blocks, `REQUIRED_MARKETS`).
- `scripts/merge_odds.py` (Kalshi odds injection into `data/slate.json`,
  including the `f5ml.tie_american` field).
- `scripts/risk_gate.py`, `scripts/write_pending_bets.py`,
  `scripts/protect_slate.py`, `scripts/validate_slate_final.py`
  (already fully audited across Phases 7–10; re-confirmed unchanged).
- `scripts/build_kalshi_registry.py`, `scripts/fetch_kalshi_markets.py`,
  `scripts/fetch_kalshi_clv.py`, `scripts/fetch_kalshi_clv_v2.py`,
  `scripts/run_kalshi_clv_step.py`, `scripts/preview_kalshi.py`.
- `api/kalshi.js`, `api/kalshisearch.js` (the two independent Kalshi
  API wrappers referenced in `docs/DUPLICATE_LOGIC_INVENTORY.md`).
- `tests/test_kalshi_f5_pipeline.py`.
- All real discovery data: `data/kalshi_registry_snapshots/*.json`
  (~250 files, 2026-06-08 through 2026-07-29), the most recent being
  `kalshi_search_2026-07-29_0803.json` (720 markets, 8 series);
  `archive/data/kalshi_full_enumeration.json`,
  `archive/data/kalshi_series_discovery.json`,
  `archive/data/kalshi_remaining_discovery.json`,
  `archive/data/kalshi_confirmed_series.json` (earlier, 2026-06-04
  hand-run discovery probes).

## Current Kalshi ingestion path (traced end to end)

```
api/kalshisearch.js / api/kalshi.js  (curl steps in fetch-slate.yml)
  -> data/kalshi_search.json, data/kalshi_raw.json
  -> scripts/build_kalshi_registry.py
       -> data/kalshi_market_registry.json  ("the persistent source of truth")
  -> scripts/merge_odds.py
       -> reads the registry + data/kalshi_search.json backfill
       -> injects odds.kalshi.{ml,f5ml,total,team_totals,rl,nrfi_yrfi}
          into each game object in data/slate.json
  -> scripts/enrich_data.py (offense-baseline enrichment, no Kalshi involvement)
  -> scripts/build_market_ledger.py
       -> reads game['odds']['kalshi'][...], populates marketLedger[]
  -> scripts/validate_slate_final.py / scripts/protect_slate.py /
     scripts/risk_gate.py / scripts/write_pending_bets.py
       (Execution Layer — all previously audited in Phases 7-10, unchanged)
```

## Hardcoded market lists / ticker prefixes / enumerations found

- `scripts/build_market_ledger.py`'s module-level `REQUIRED_MARKETS`
  list (confirmed present, ~line 70-75): `NRFI, YRFI, F5_ML_Away,
  F5_ML_Home, TT_Away_Over, TT_Home_Over, ML_Away, ML_Home, Game_Total,
  RL_Away, RL_Home`. This is the single static list the mission's Part
  6 asks to design a dynamic replacement for (see
  `docs/research/PROJECTION_UPGRADE_ROADMAP.md` Wave 1).
- Series-ticker prefixes hardcoded across the Kalshi fetch layer:
  `KXMLBGAME`, `KXMLBF5`, `KXMLBSPREAD`, `KXMLBTOTAL`,
  `KXMLBTEAMTOTAL`, `KXMLBF5SPREAD`, `KXMLBF5TOTAL`, `KXMLBRFI` (plus
  legacy bare `MLBNRFI`/`MLBYRFI`/`MLBF5`/`MLBTOT` aliases probed by
  earlier discovery scripts but not confirmed populated in any
  examined snapshot).
- No regular-expression-based ticker parser was found operating on
  arbitrary/unknown series — every current parser assumes one of the
  fixed series names above.
- No hidden "unsupported-market drop": markets outside
  `REQUIRED_MARKETS` are simply never looked at by
  `build_market_ledger.py` at all (not fetched-then-dropped — never
  read from `odds.kalshi` in the first place), which is functionally
  equivalent to a silent drop from the model's perspective, just
  implemented as an omission rather than an explicit filter.

## Part 4: Current projection methodology — independently audited

### The core math (confirmed, read directly)

`poisson_pmf(k, lam)` — a plain independent Poisson PMF.

`p_team_wins(team_proj, opp_proj, max_r=20)` — a double sum over
`[0, max_r] x [0, max_r]` of the product of each team's independent
Poisson PMF, splitting into `p_win` (`a > h`) and `p_push` (`a == h`).
**This is the single function computing win/tie probability for BOTH
the full-game moneyline AND the F5 moneyline** — the same code, called
twice with different (horizon-scaled) inputs.

### CRITICAL FINDING — the mission's named anti-pattern is real and current

After computing `pw` (win) and `pp` (tie/push) via `p_team_wins()`,
both the full-game and F5 evaluation blocks do:

```python
p_home_win = 1 - pw - pp
p_away_net = pw / (1 - pp) if (1 - pp) > 0 else pw
p_home_net = p_home_win / (1 - pp) if (1 - pp) > 0 else p_home_win
```

— i.e. **the tie probability is computed correctly, then discarded via
renormalization**, and only the renormalized `p_away_net`/`p_home_net`
feed into edge/calibration/qualification. This is exactly the
behavior the mission's Critical Three-Way Market Requirement forbids
("Away and Home probabilities must not be renormalized after removing
the tie") — confirmed as CURRENT, ACTIVE production behavior, not a
hypothetical risk.

**For full-game (`KXMLBGAME`), this happens not to matter for
settlement correctness** — confirmed via two real snapshots 2 months
apart that `KXMLBGAME` is a genuine two-way Kalshi market (no `-TIE`
ticker exists for any event in either snapshot), because a regulation
tie always continues into extra innings until a winner is decided. The
renormalization is *defensible in effect* for full-game, though for an
undocumented reason (the code offers no comment explaining this), and
it silently assumes the model's Poisson-based `p_push` behaves as a
reasonable proxy for "this game needs extra innings," which is not
verified anywhere.

**For F5 (`KXMLBF5`), the same renormalization is a genuine error**:
confirmed via the same two real snapshots that every `KXMLBF5` event
has exactly 3 market tickers, including an explicit `-TIE` leg (e.g.
`KXMLBF5-26JUL292210SEALAD-TIE`, real title: *"Seattle vs Los Angeles D
first 5 innings tie?"*) — Kalshi genuinely sells a settleable F5 TIE
contract, and the model discards its own correctly-computed probability
for that exact outcome.

### CRITICAL FINDING — F5 tie market price is fetched and then never used

Independently confirmed via `grep`:
- `scripts/merge_odds.py` (~line 313-317) **does** populate
  `odds.kalshi.f5ml.tie_american` from the real Kalshi TIE market's
  price.
- `scripts/build_market_ledger.py` (line 919) reads it:
  `f5_tie_am = f5ml.get('tie_american')` — and this is the **only**
  occurrence of `f5_tie_am` anywhere in the file. The variable is
  assigned and never read again. No market-ledger row, no
  missing-data row, no rejected row — nothing is ever built from this
  real, available market price.

This means: real F5 TIE price data flows all the way from Kalshi
through `merge_odds.py` into `data/slate.json`, and production code
has it sitting in a local variable, unused, every single run. This is
the clearest, most concrete "immediately actionable" finding this
audit produced — see
`docs/research/PROJECTION_UPGRADE_ROADMAP.md` Wave 1/2 for how it maps
onto a prioritized fix (research-only in this phase; NOT activated).

### Other audited items (Part 4's full checklist)

| Item | Finding |
|---|---|
| Independent Poisson assumption | Confirmed, unconditional — `poisson_pmf()` has no correlation term of any kind between away/home scoring. |
| Maximum run truncation | `p_team_wins(max_r=20)` — a `[0,20]x[0,20]` grid. For realistic MLB team-run projections (typically 2-7), the untruncated tail is astronomically small (this phase's own `three_way_result_probs()` reports truncation mass < 1e-6 at `max_runs=40` for realistic inputs) — not a practical accuracy concern, but never explicitly measured or reported by production itself. |
| Probability mass lost beyond truncation | Never computed or reported anywhere in production — this phase's research module (`lib/research/three_way_projection.py`) is the first place `truncationMass` is surfaced as an explicit, inspectable value. |
| Overdispersion | Not modeled at all — real MLB team-run distributions are known (external baseball-analytics literature) to be somewhat overdispersed relative to a pure Poisson; production makes no adjustment. |
| Team-score correlation | Not modeled at all — the two teams' Poisson draws are fully independent, ignoring e.g. weather/park effects that would push both teams' scoring in the same direction on a given day. |
| Inning-horizon scaling | F5 (`f5_away`/`f5_home`, lines ~393-397) uses a materially more sophisticated model than naive `9/5` linear scaling: `away_off_factor * (f5_home_starter_ip * home_xfip / 9 * home_tto_adj) + park_adj * (5/9)`, clamped to `[1.2, 4.1]` — accounts for the OPPOSING starter's expected innings pitched, xFIP, and a times-through-the-order adjustment. No F3 or F7 scaling exists anywhere (no F3/F7 markets exist to scale for — see the market inventory). |
| Starter/bullpen allocation | F5 uses opposing-starter IP + xFIP directly (see above); full game does not separately allocate starter vs. bullpen innings — `away_proj`/`home_proj` are the season/rolling offense-vs-averages blend, not a per-pitcher-segment model. |
| Park adjustment | Present (`park_adj`), applied identically to full-game and F5 (scaled by `5/9` for F5). |
| Weather adjustment | `api/weather.js` exists and is fetched into the slate, but this phase did not trace whether `build_market_ledger.py`'s Poisson mean actually incorporates a weather term — flagged as an open question for the next audit pass, not confirmed either way. |
| Confirmed-lineup adjustment | Present (`lineupAdj`/`lineupConfirmed`/`lineupConfirmedOfficial`, applied in `enrich_data.py` before `build_market_ledger.py` runs). |
| Batting-order weighting | Not found — no per-batting-slot weighting logic located in `build_market_ledger.py` or `enrich_data.py`. |
| Platoon adjustment | A platoon-split estimation exists in `enrich_data.py`'s "Step 3" (`vsLHH`/`vsRHH` k% estimation for pitchers with insufficient real split data), but this phase did not confirm it feeds into the team run projection itself vs. only display fields — flagged as an open question. |
| Starter workload / pitch-count constraints | Present for F5 via `f5_home_starter_ip`; no explicit pitch-count-based innings-remaining model found. |
| Opener/bulk-pitcher handling | Present: `away_opener`/`home_opener` gates F5 markets entirely (Rule 24: "F5 UNQUALIFIED" rejection when the pitcher throwing is an opener). |
| Times-through-the-order effects | Present for F5 (`home_tto_adj`/`away_tto_adj`); not found for full game. |
| Bullpen quality | `away_ps`/`home_ps` (pitcher Savant data) plus a separate `bullpen` block exist on each game object and are read by `enrich_data.py`'s high-leverage xFIP enrichment (`hlXFIP`); this phase did not trace whether `build_market_ledger.py`'s full-game projection actually blends starter and bullpen quality into one combined mean, or uses only the starter — flagged as an open question. |
| Bullpen availability/fatigue | `bullpen.last3DaysIP`/`fatigued` fields exist (confirmed via `enrich_data.py`'s bullpen-enrichment step); not confirmed whether `build_market_ledger.py` actually reads them for anything beyond display. |
| Defense | No defensive-metric input found anywhere in the projection chain. |
| Baserunning | No baserunning input found anywhere in the projection chain. |
| Travel/rest | No travel/rest input found. |
| Umpire | No umpire-effect input found (an `api/` module for umpire data was not located during this audit). |
| Catcher effects | No catcher-framing/effect input found. |
| Roof status | `park.dome` is a captured field (confirmed in the real historical slate structure read this phase); not confirmed whether it independently adjusts the projection beyond the general park factor. |
| Extra-inning handling | Confirmed: full-game moneyline settlement is extra-inning-inclusive (Kalshi's own two-way structure implies this); the model's `p_push` for full game is a REGULATION-innings tie probability being used as a proxy, an approximation whose accuracy this phase did not independently validate against real extra-inning frequency data. |
| Regulation-only settlement handling | F5's settlement is regulation-scoped by construction (an inning-count horizon, not affected by extra innings at all) — confirmed correctly modeled at the PROJECTION level; the bug is downstream (discarding the tie), not in how the horizon itself is scaled. |
| Integer-line ties / push / void rules | Not separately modeled for totals/team-totals in this audit pass — flagged for the Wave 2 total/team-total research work. |
| Market-price priors | Not used anywhere in the current model — `vig_free_2way()` computes the market's implied probability purely for EDGE comparison after the model probability is already final; the model itself never blends in a market prior (a legitimate design choice to avoid leakage, not a defect). |

## Immediate projection defects identified (Part 4's explicit ask)

Ranked by concreteness/actionability, NOT fixed this phase (research
only):

1. **F5 tie renormalization discards a real, priced, currently-fetched
   market outcome** (see above) — the single clearest defect.
2. **Full-game tie renormalization is applied without an explicit
   justification recorded in code** — defensible in effect (Kalshi's
   full-game contract is genuinely two-way), but the reasoning is not
   documented anywhere in `scripts/build_market_ledger.py`, so a future
   maintainer could not distinguish "intentional, correct" from
   "accidental" without this audit.
3. **No F3 or F7 market exists at all** (confirmed via exhaustive
   snapshot/archive search) — the mission's requirement to "explicitly
   support three-way outcomes for F3/F5/F7" is satisfiable at the
   PROJECTION-FUNCTION level (this phase's
   `lib/research/three_way_projection.py` genuinely supports all three
   horizons generically), but F3/F7 currently have no real Kalshi
   market to ever bind that projection to. This is not a defect in the
   model — it is a market-availability fact this phase discovered and
   is recording so no future phase wastes effort building an F3/F7
   MARKET INTEGRATION (as opposed to the projection function itself,
   which is legitimately useful scaffolding regardless).
4. **Truncation mass and overdispersion are never measured or
   reported** — not shown to be materially wrong for realistic inputs,
   but never checked, either.
