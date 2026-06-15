# BETTING FRAMEWORK — Edge Finder Model

*Last updated: 2026-06-14 (reliability upgrade v3.0)*

---

## 1. trackingType Schema

Every bet in bets.json must have these three fields:

```json
{
  "trackingType": "REAL" | "MODEL_ONLY" | "PAPER" | "REAL_PROBE",
  "actuallyPlaced": true | false | null,
  "placementConfirmedAt": "ISO timestamp or null"
}
```

### Rules

| trackingType | Counts for Bankroll P/L | Notes |
|---|---|---|
| REAL | YES (if actuallyPlaced=true + placementConfirmedAt set) | Standard real-money bets |
| REAL_PROBE | YES (if actuallyPlaced=true + placementConfirmedAt set) | $1.00 max stake probes |
| MODEL_ONLY | NO | Model tracking only, never placed |
| PAPER | NO | Paper tracking bets, never placed |

Critical rules:
- Official bankroll P/L uses ONLY REAL or REAL_PROBE with actuallyPlaced=true
- MODEL_ONLY and PAPER NEVER affect bankroll regardless of stake size
- betSize > $1 does NOT upgrade a bet trackingType — PAPER with betSize=$8 is still PAPER
- Final slip is canonical source of actuallyPlaced=true
- Anything not in confirmed final slip defaults to actuallyPlaced=false

---

## 2. Run Types and Authoritative Slate Protection

### Run Types

- OFFICIAL_PREGAME: First clean pregame slate run. Produces authoritative.json
- LINEUP_RECHECK: Subsequent run to update lineup/pitcher data. Cannot overwrite started games
- IN_PLAY_RECHECK: Run during active games. Started games are frozen
- REJECTED_CONTAMINATED: Run contains sentinel prices or widespread contamination. Quarantined

### File Structure

```
data/slates/YYYY-MM-DD/
  official_20260614T115900Z.json      First pregame run
  authoritative.json                   Canonical pointer (copy of first clean run)
  recheck_20260614T140000Z.json       Lineup recheck
  rejected_contaminated_XYZ.json      Quarantined contaminated run
```

### Authoritative Slate Rules

1. authoritative.json is written ONCE from the first OFFICIAL_PREGAME run
2. Subsequent runs (LINEUP_RECHECK) may update ONLY not-yet-started games
3. Started games are FROZEN — their official entry data cannot be overwritten
4. If a rerun contains sentinel prices for a game, that game alone is REJECTED
5. If >50% of games in a rerun are contaminated: quarantine entire run as REJECTED_CONTAMINATED
6. Post-slate review MUST use authoritative.json, never a recheck file

### Per-Game Update Rules (Lineup Reruns)

A rerun can update a game ONLY if ALL of:
- Game has not yet started (current time < scheduled start)
- Market is still pregame/open
- Ticker matches original
- Price is valid (not sentinel, not post-start, not settlement price)
- No sentinel prices in any field
- Entry timestamp before first pitch
- Update improves lineup/pitcher completeness (more confirmed fields)

---

## 3. CLV Workflow and Valid/Invalid Statuses

### CLV Snapshot Pipeline

1. Slate generation: persist tracked_tickers.json in data/clv_snapshots/YYYY-MM-DD/
2. Before first pitch: capture_clv_pregame.py writes pregame_{gamePk}.json
3. Post-game review: validate_clv() reads pregame snapshots only
4. Report: clvStatus, clvPct written to bets.json

### CLV Status Values

| Status | Meaning |
|---|---|
| VALID | Pregame price captured, timestamp before first pitch |
| MISSING | No snapshot file found for this ticker/date |
| INVALID_POST_START | Snapshot taken after scheduled first pitch — rejected |
| TICKER_NOT_FOUND | Ticker not found in any snapshot source |
| STALE_MARKET | Market not updated in >6h before first pitch |
| SENTINEL_PRICE | Price is a known sentinel (19900, -19900, etc.) — rejected |
| NO_VALID_PRICE | Price field exists but is null/zero/impossible |
| MARKET_LOCKED | Market in locked/closed state before settlement |
| SETTLEMENT_PRICE_ONLY | Only post-settlement price available |
| ENTRY_PRICE_MISSING | No entry price in bet record |

Rules:
- NEVER use post-game/settlement/stale/sentinel prices for CLV
- If CLV unavailable: returns status string, clvPct=null, NEVER zero
- Positive clvPct = bought cheaper than market closed = good CLV

### CLV Formula

```
entry_implied = american_to_implied(entry_american_price)
close_implied = yes_price / 100.0   (Kalshi yes_price in cents)
clv_pct = (entry_implied - close_implied) * 100
```

---

## 4. REAL_PROBE Lane

### What is REAL_PROBE?

A controlled $1.00 probe bet on a market not yet eligible for standard real-money sizing.
Used to build CLV and performance history before promotion.

### Eligibility Requirements

- Pass ALL DATA_HARD blocks
- Pass ALL MARKET_MECHANICS_HARD blocks
- Fail at most ONE RISK_SOFT or CALIBRATION block
- Exact valid Kalshi ticker
- Valid pregame price (not sentinel, not stale)
- Entry timestamp before first pitch
- CLV capture supported (ticker in tracked_tickers.json)
- Explicitly listed in final slip as REAL_PROBE
- Default actuallyPlaced=false until confirmed in final slip

### Stake Cap

- Default max: $1.00
- Absolute max: $1.50 (never exceed)

### REAL_PROBE Exclusions

REAL_PROBE must NOT include:
- Stale odds or sentinel prices
- Missing ticker or missing game ID
- Postponed/cancelled games
- Invalid market mechanics
- YRFI/NRFI using bullpen or full-game-only logic
- Post-start prices

---

## 5. Block Class Taxonomy

| Class | Description | Hard/Soft | Probe Eligible? |
|---|---|---|---|
| DATA_HARD | Missing/invalid input data. Model output unreliable | HARD | NO |
| MARKET_MECHANICS_HARD | No ticker, stale price, unconfirmed line | HARD | YES if ticker+price valid |
| RISK_SOFT | Risk management rule. Downgrades tier | SOFT | YES (at most 1 failure) |
| CALIBRATION | Model performance insufficient for real money | SOFT | YES (at most 1 failure) |
| OPPORTUNITY_FILTER | Market suspended due to negative historical performance | HARD | NO — must still track |

---

## 6. Promotion/Demotion Rules

### PAPER/MODEL_ONLY → REAL_PROBE

All required:
- Data quality clean, market mechanics valid, exact tickers exist, CLV capture working
- Sample size >= 20 settled markets
- Average CLV >= 0%
- Higher edge buckets outperform lower edge buckets

### REAL_PROBE → REAL

All required:
- Positive average CLV (>= 1.0%) over sample >= 30
- Positive ROI or acceptable variance with strong CLV (ROI >= -5%)
- No data-quality failures in last 10 games
- Settlement method reliable

### Demotion Triggers

- >= 3 consecutive negative CLV bets
- Rolling 10-game average CLV < -2%
- Profits driven by variance but CLV is negative
- Repeated DATA_HARD block violations, settlement ambiguity

---

## 7. F5 Settlement Rules

### Settlement Source Hierarchy

1. PRIMARY: MLB linescore API /api/v1/game/{gamePk}/linescore — sum innings 1-5
2. CROSS-CHECK: Final boxscore (verify against linescore)
3. FALLBACK only: Raw boxscore run totals if linescore unavailable (flagged with fallbackUsed=true)
4. NEVER: Raw RBI event summation as primary source

### F5 Tie Handling

- Away wins ONLY if away F5 > home F5
- Home wins ONLY if home F5 > away F5
- TIE (equal score after 5 innings): F5 ML = LOSS for both sides
- Exception: Only if Kalshi contract explicitly specifies refund

### Regressions Fixed

- NYY@TOR June 14: F5 score 2-2 — NYY F5 ML Away = LOSS (correctly graded)
- TB@LAA June 14: RBI discrepancy — linescore now overrides raw RBI

---

## 8. YRFI/NRFI Allowed/Disallowed Inputs

### ALLOWED (first-inning specific only)

- first_inning_xera_away/home: Starter 1st-inning xERA from Baseball Savant
- first_inning_form_*: Last 5 starts 1st-inning results
- first_inning_run_rate_*: Team 1st-inning R/game (season + L15)
- leadoff_quality_*, top_order_quality_*: Top 3-4 hitters
- park_first_inning_factor: Park factor specific to 1st inning
- weather_first_inning: Wind/dome affecting first inning
- umpire_run_environment: Historical umpire run environment
- lambda_first_inning: Combined 1st-inning Poisson lambda

### DISALLOWED (bullpen/full-game factors)

- bullpen_exposure, bullpen_weakness, bullpen_xfip/era: Bullpen not relevant in inning 1
- short_starter_leash, early_hook: Relates to innings 2+
- avg_innings_per_start, avgIP: Full-game metric
- bullpen_fatigue, pen_arrives_inning_X: Full-game bullpen fatigue
- full_game_total (unconverted): Not 1st-inning specific

### Required Output Fields

Every YRFI/NRFI output must include:
- lambda_used: float
- lambda_formula: string
- lambda_is_first_inning_specific: bool
- lambda_derived_from_full_game: bool
- park_first_inning_included: bool
- team_first_inning_rates_included: bool
- independent_poisson_first_inning_valid: bool

---

## 9. Sentinel Price Rejection

Hard-rejected values (19900, -19900, 100000, -100000, any abs >= 19000, probability 0 or 100 exactly).

Sentinel detection runs on:
- All market price fields at slate generation time
- Any rerun slate before merging with authoritative
- Any bet before real-money classification

A bet or slate with sentinel prices is quarantined, never committed to official records.
