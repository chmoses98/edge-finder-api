# DATA_SOURCES.md
# Last updated: June 5, 2026 — v3.1

---

## ODDS ARCHITECTURE — KALSHI IS THE SOLE BET SOURCE

All bets are placed on Kalshi. Kalshi is the primary, and ONLY, source for all market prices, edge calculation, and CLV. FanDuel, DraftKings, and BetMGM are NOT used as bet books and are NOT fallbacks. Pinnacle is used as a sanity check only — it is never the bet source.

**Kalshi posts ALL markets used by this model:**
- ✅ ML (moneyline) — all games
- ✅ RL (runline) — all games
- ✅ Game Total (over/under) — all games, always half-run increments
- ✅ Team Totals — all games
- ✅ F5 ML — all games with confirmed starters
- ✅ F5 RL — all games with confirmed starters
- ✅ NRFI / YRFI — all games

There is no scenario where a bet is placed on FD/DK instead of Kalshi. If a market is not posted on Kalshi for a specific game, no bet is logged for that market on that game — do not redirect to another book.

---

## THREE-LAYER ODDS STRUCTURE

| Layer | Purpose | Source |
|---|---|---|
| **Layer 1 — Market Reference** | Sharpest-market sanity check | Pinnacle VF (reference only — never the bet target) |
| **Layer 2 — Bet Price** | Actual price received | Kalshi (all markets) |
| **Layer 3 — CLV** | Did you beat the closing line? | Kalshi historical prices |

### Edge Calculation
Edge is computed against Kalshi implied probability — because Kalshi is the market you are betting into:
```
edge = (modelProb − kalshiImplied) × calibration_factor
```
Where `kalshiImplied` is the vig-free implied probability from Kalshi's two-sided market. This formula is uniform across ALL markets: ML, RL, Game Total, Team Total, F5 ML, F5 RL, NRFI, YRFI.

### Why Pinnacle Is Still Referenced
Pinnacle is the sharpest market in the world and serves as a model sanity check:
- **Model and Pinnacle within ~3–5%:** Trust the edge.
- **Model and Pinnacle diverge by >7%:** Flag the game. Either the model is wrong or Pinnacle has sharp action that hasn't hit Kalshi yet. Do not bet without understanding why.
- **Pinnacle and Kalshi diverge significantly (>5 cents) with model agreement:** Strongest edge signal — Kalshi hasn't caught up.

Pinnacle is never subtracted from modelProb. It is a reference only.

### Bet Logging Field Mapping
```json
"pinnacleVF": 0.54,          // Layer 1 — sanity check reference, never your bet price
"pinnacleML": -117,           // Layer 1 — Pinnacle American odds at bet time (context)
"betPrice": +108,             // Layer 2 — Kalshi price at time of bet (YOUR actual price)
"betTimeLine": +108,          // Layer 2 — same as betPrice; preserved for CLV insurance
"closingLine": +102,          // Layer 3 — Kalshi price at first pitch (historical)
"closingLineSource": "Kalshi",
"closingLineTimestamp": "2026-06-05T18:10:00Z",
"clv": 2.8                    // Layer 3 — computed against Kalshi close
```

---

## KALSHI — ALL MARKETS, ALL GAMES

Kalshi is the sole betting and CLV source. Pull ALL market prices from Kalshi directly via the Kalshi API. No other exchange or sportsbook is used for pricing.

**Market availability on Kalshi (confirmed):**
| Market | Kalshi Available | Line Format | Edge VF Source |
|---|---|---|---|
| ML | ✅ YES — all games | American odds | Kalshi VF |
| RL | ✅ YES — all games | American odds | Kalshi VF |
| Game Total | ✅ YES — all games | Half-run only (7.5, 8.5 — never 7, 8) | Kalshi VF on Kalshi's line |
| Team Totals | ✅ YES — all games | Half-run lines | Kalshi VF |
| F5 ML | ✅ YES — all games | American odds | Kalshi VF |
| F5 RL | ✅ YES — all games | American odds | Kalshi VF |
| NRFI / YRFI | ✅ YES — all games | American odds | Kalshi VF |

**Game Total line rule:** Kalshi only posts half-run lines. If Pinnacle has the total at 8 and Kalshi has it at 8.5, these are different bets. Always use Kalshi's line for the total edge calculation — never Pinnacle's line. If Kalshi has no total posted for a specific game, no total bet is logged.

**Per-market bet book and VF source (definitive):**
| Market | Bet Book | VF Source | CLV Source |
|---|---|---|---|
| ML | Kalshi | Kalshi VF | Kalshi historical |
| RL | Kalshi | Kalshi VF | Kalshi historical |
| Game Total | Kalshi | Kalshi VF | Kalshi historical |
| Team Total | Kalshi | Kalshi VF | Kalshi historical |
| F5 ML | Kalshi | Kalshi VF | Kalshi historical |
| F5 RL | Kalshi | Kalshi VF | Kalshi historical |
| NRFI / YRFI | Kalshi | Kalshi VF | Kalshi historical |

Log `"betBook": "Kalshi"` on every bet entry.

---

## CLV — KALSHI HISTORICAL PRICES

Closing lines are pulled from Kalshi's historical price data — not from The Odds API, not from OddsPortal, not from Pinnacle. Kalshi's own historical API provides the closing price for every market at first pitch.

### Closing Line Pull
At settlement, pull the Kalshi historical price for the relevant market at or just before first pitch. Store as:
```json
"closingLine": +102,
"closingLineSource": "Kalshi",
"closingLineTimestamp": "2026-06-05T18:10:00Z"
```

### Fallback if Kalshi Historical Pull Fails
1. Use `betTimeLine` (Kalshi price at bet time) as closing line proxy → flag as `closingLineSource: "betTimeLine_proxy"`
2. Log `closingLine: null`, `clv: null` only if betTimeLine is also unavailable
3. **Never fabricate or estimate a closing line.** Null is correct.

### CLV Calculation
```python
def american_to_decimal(american):
    if american > 0: return (american / 100) + 1
    else: return (100 / abs(american)) + 1

bet_dec = american_to_decimal(bet_price)       # e.g. +108 → 2.08
close_dec = american_to_decimal(closing_line)  # e.g. +102 → 2.02

clv_pct = (bet_dec - close_dec) / close_dec * 100
# Positive = beat the close (good). Negative = line moved against you.
```

### Settlement Window
Settle within 7 days. Kalshi historical data is stable — no degradation.

---

## PRIMARY DATA PATH

1. Trigger the `fetch-slate` GitHub Action via API
2. Wait ~40 seconds for it to complete
3. Verify `data/meta.json` fetchedAt matches today AND is <4 hours old
4. Read `data/` files via GitHub API

### Trigger Call
```
POST https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Headers: Authorization: token <token>
Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
```

### Data Files (written by Action)
| File | Contents |
|---|---|
| `data/slate.json` | Full slate — Kalshi odds (all markets), pitchers, bullpen, model probs, edges |
| `data/pitchers.json` | Confirmed starters with IDs |
| `data/teamstats.json` | Team hitting stats including wRC+, xOPS, barrel%, rolling R/G |
| `data/weather.json` | Park weather |
| `data/meta.json` | Fetch timestamp and date — verify before using |

### Model Files (pulled separately via GitHub raw content API at session start)
| File | Path | Contents |
|---|---|---|
| `RULES.md` | `/RULES.md` | All betting rules and gate definitions |
| `MODEL_CORE.md` | `/MODEL_CORE.md` | Probability engine, sizing, calibration, market rules |
| `SLATE_WORKFLOW.md` | `/SLATE_WORKFLOW.md` | Session workflow and bet entry format |
| `DATA_SOURCES.md` | `/DATA_SOURCES.md` | Data field definitions and fallback chain |
| `bets.json` | `/bets.json` | Authoritative bet ledger — flat JSON array, parse directly with `json.load(f)` |

**Pull order at session start:** RULES.md → MODEL_CORE.md → SLATE_WORKFLOW.md → DATA_SOURCES.md → bets.json. All five must be pulled before any analysis or logging begins.

### Verify Before Using
Always check `data/meta.json` fetchedAt timestamp. If stale (>4 hours old), re-trigger the workflow before analyzing.

---

## SLATE.JSON — MARKET FIELDS

All odds fields in slate.json follow the Kalshi-primary structure. Every market price is sourced from Kalshi.

```json
"game": {
  // Pinnacle — sanity check reference only
  "pinnacleML": { "away": -125, "home": +112 },
  "pinnacleVF": { "away": 0.549, "home": 0.451 },
  "pinnacleRL": { "away": { "line": -1.5, "price": +155 }, "home": { "line": +1.5, "price": -178 } },
  "pinnacleTotalLine": 8.5,
  "pinnacleTotalOver": -108,
  "pinnacleTotalUnder": -108,
  // Kalshi — bet price and edge target (ALL markets)
  "kalshiML": { "away": +112, "home": -118 },
  "kalshiRL": { "away": { "line": -1.5, "price": +162 }, "home": { "line": +1.5, "price": -175 } },
  "kalshiTotalLine": 8.5,
  "kalshiTotalOver": -105,
  "kalshiTotalUnder": -112,
  "kalshiTT": {
    "away": { "line": 4.5, "over": -118, "under": +102 },
    "home": { "line": 4.5, "over": -122, "under": +106 }
  },
  "kalshiF5ML": { "away": +118, "home": -125 },
  "kalshiF5RL": { "away": { "line": -0.5, "price": +192 }, "home": { "line": +0.5, "price": -208 } },
  "kalshiNRFI": -138,
  "kalshiYRFI": +128
}
```

---

## MARKET COMPARISON — EDGE DETECTION LOGIC

### Primary Edge Signal — Model vs Kalshi
The model's projected probability minus Kalshi's implied probability is the edge. The model IS the signal. Kalshi is what you are betting against.

To vig-free a Kalshi two-sided market:
```
implied_away = |away_line| / (|away_line| + 100)  [if away is minus]
implied_away = 100 / (away_line + 100)              [if away is plus]
vig = implied_away + implied_home - 1
vf_away = implied_away / (implied_away + implied_home)
vf_home = implied_home / (implied_away + implied_home)
```
Then: `edge = (modelProb − vf_kalshi) × calibration_factor`

### Pinnacle — Sanity Check Only
| Divergence (model vs Pinnacle VF) | Action |
|---|---|
| ≤5% | Proceed. Model and sharp market broadly agree. |
| 5–7% | Note the divergence. Review game notes. |
| >7% | Flag. Do not bet without a specific reason the model should be trusted over Pinnacle. |

Pinnacle divergence alone is NOT a reason to skip a bet — it is a prompt to review.

---

## KEY FIELDS FOR PROBABILITY ENGINE

**Starter fields (in slate.json or pitchers.json):**
- `pitcher.xFIP` — season xFIP (primary)
- `pitcher.xERA` — season xERA (secondary/divergence comparison only)
- `pitcher.recentXFIP` — last 5 starts xFIP average
- `pitcher.kPer9` — K/9
- `pitcher.bbPer9` — BB/9
- `pitcher.avgIP` — average IP per start (opener check)
- `pitcher.gsCount` — games started this season (season depth for regression weights)
- `pitcher.fbPct` — fly ball percentage (park factor modifier input)
- `pitcher.firstInningSplit` — 1st-inning xERA (NRFI/YRFI)
- `pitcher.vsLHH` / `pitcher.vsRHH` — platoon K% and xFIP splits (minimum 50 PA to use)
- `pitcher.ttoSplit` — xFIP difference 3rd TTO vs 1st TTO (F5 TTO adjustment input)
- `pitcher.velocityAvg` — season average velocity
- `pitcher.velocityRecent` — last 3-start velocity average (flag if 1+ mph below season avg)

**Team offense fields (in teamstats.json):**
- `team.wrcPlus` — season wRC+
- `team.xOPS` — expected OPS
- `team.barrelPct` — barrel rate (bounceback/regression signal)
- `team.last7RpG` — rolling 7-game R/G
- `team.last15RpG` — rolling 15-game R/G
- `team.firstInningRpG` — 1st-inning run rate (NRFI/YRFI)
- `team.prevGameRuns` — runs scored yesterday (Under pre-gate input)

**Bullpen fields (in slate.json):**
- `team.bullpen.xFIP` — bullpen xFIP
- `team.bullpen.recentERA` — last 14 days ERA
- `team.bullpen.last3DaysIP` — workload flag (fatigue at 15+ IP → step down one tier)

**Lineup fields (in slate.json if available):**
- `game.homeLineup` / `game.awayLineup` — confirmed lineup cards
- If not available: fall back to mlb.com probable-pitchers lineup cards (~3–4 hrs pre-game)

---

## FALLBACK CHAIN (only if GitHub Action fails — no odds source substitution)

If the fetch-slate Action fails entirely, use these for game/pitcher/score data only. These are NOT odds sources — Kalshi is still the only odds source.

1. `fetch_sports_data` tool — scores, standings, game_stats by game ID
2. `mlb.com/[team]/roster/probable-pitchers` — starter confirmation
3. Google: `"[Team A] vs [Team B] starting pitcher [date] 2026"`

---

## MANUAL LOOKUP FALLBACKS (pitcher/park data only)

### Opener Role Lookup
- `pitcher.firstInningSplit` in slate.json when Action runs
- Manual: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Minimum 5 appearances required

### FB% Lookup (Park Factor Modifier)
- `pitcher.fbPct` in slate.json
- Manual fallback: Baseball Savant pitcher page → "Batted Ball" tab

### TTO Split Lookup
- `pitcher.ttoSplit` in slate.json
- If unavailable: `tto_adj = 1.0` applied, noted in output

### Handedness Split Lookup
- `pitcher.vsLHH` / `pitcher.vsRHH` in slate data
- Minimum 50 PA sample required. If <50 PA: no adjustment applied.

### Bullpen xFIP Lookup
- `team.bullpen.xFIP` in slate data
- Manual fallback: FanGraphs team pitching (relief only), filter by Role: RP

---

## BANNED SOURCES
- FanDuel, DraftKings, BetMGM — NOT used as bet books or fallback price sources
- OddsPortal — NOT used for CLV (replaced by Kalshi historical)
- The Odds API historical endpoint — NOT needed (Kalshi historical API used directly)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `statsapi.mlb.com` — returning 400 errors

