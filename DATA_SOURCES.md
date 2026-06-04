# DATA_SOURCES.md
# Last updated: June 2, 2026 — v3.0

---

## ODDS ARCHITECTURE — THREE-LAYER SYSTEM

This model uses a three-layer odds structure. Each layer has a distinct purpose and must not be confused with another.

| Layer | Purpose | Source | API Region/Key |
|---|---|---|---|
| **Layer 1 — Market Reference** | Edge detection. What is the true probability? | Pinnacle VF (primary), FanDuel/DraftKings/BetMGM (confirmation) | `eu` + `us` |
| **Layer 2 — Bet Price** | What price are you actually getting right now? | Kalshi (ML/RL/Total ONLY — Kalshi does NOT post TT, F5, or NRFI via API); FanDuel/DraftKings for F5/TT/NRFI | `us_ex` |
| **Layer 3 — CLV** | Did you beat the closing line? | Kalshi close for ML/RL/Total; FD/DK closing line for F5/TT/NRFI | `us_ex` historical |

### Edge Calculation
Edge is computed against Kalshi implied probability — because that is the market you are betting into:
```
edge = modelProb − kalshiImplied × calibration_factor
```
Where `kalshiImplied` is the vig-free implied probability from Kalshi's two-sided market.

The model's projected probability IS the signal. You are not arbitraging Pinnacle vs Kalshi — you are betting your model's estimate of true probability against what Kalshi is offering.

### Why Pinnacle Is Still Critical
Pinnacle is the sharpest market in the world. If your model says 58% and Pinnacle VF says 48%, that is a 10-point divergence — not an edge, it's a model error flag. Pinnacle acts as a sanity check:
- **Model and Pinnacle within ~3–5%:** Trust the edge. The model and the sharpest book broadly agree on the game; Kalshi is mispriced.
- **Model and Pinnacle diverge by >7%:** Flag the game. Either the model is wrong on this specific game (park factor, lineup, injury not captured) or Pinnacle has sharp action that hasn't hit Kalshi yet. Do not bet without understanding why.
- **Pinnacle and Kalshi diverge significantly (>5 cents) with model agreement:** Strongest edge signal. Sharp market and your model agree; Kalshi hasn't caught up.

Pinnacle is never subtracted from modelProb. It is a reference only.

### Bet Logging Field Mapping
```json
"pinnacleVF": 0.54,          // Layer 1 — edge reference, never your bet price
"pinnacleML": -117,           // Layer 1 — Pinnacle American odds at bet time (context)
"fdLine": -118,               // Layer 1 — FanDuel confirmation line
"dkLine": -120,               // Layer 1 — DraftKings confirmation line
"betmgmLine": -115,           // Layer 1 — BetMGM confirmation line
"betPrice": +108,             // Layer 2 — Kalshi price at time of bet (YOUR actual price)
"betTimeLine": +108,          // Layer 2 — same as betPrice; preserved for CLV insurance
"closingLine": +102,          // Layer 3 — Kalshi price at first pitch (historical snapshot)
"closingLineSource": "Kalshi",
"closingLineTimestamp": "2026-06-02T18:10:00Z",
"clv": 2.8                    // Layer 3 — computed against Kalshi close
```

---

## THE ODDS API — PRIMARY ODDS SOURCE

**Host:** `https://api.the-odds-api.com`
**API Key:** stored as GitHub secret `ODDS_API_KEY` in the fetch-slate Action
**Plan:** Paid — 20,000 credits/month. Historical endpoint unlocked.

### Bookmaker Keys

| Bookmaker | Key | Region | Layer |
|---|---|---|---|
| Pinnacle | `pinnacle` | `eu` | 1 — Market reference |
| FanDuel | `fanduel` | `us` | 1 — Confirmation |
| DraftKings | `draftkings` | `us` | 1 — Confirmation |
| BetMGM | `betmgm` | `us` | 1 — Confirmation |
| Kalshi | `kalshi` | `us_ex` | 2 & 3 — Bet price + CLV |

Do NOT pull: novig, prophetx, betopenly, polymarket, or any other exchange. Kalshi is the only exchange we use.

### MLB Market Keys

**Featured markets** (bulk `/odds` endpoint — all games in one call):
| Model Market | API `markets` key |
|---|---|
| ML (full game) | `h2h` |
| Runline | `spreads` |
| Game Total | `totals` |
| Team Totals | `team_totals` |

**Additional markets** (per-event `/events/{eventId}/odds` — one call per game):
| Model Market | API `markets` key | Kalshi available? |
|---|---|---|
| F5 ML | `h2h_1st_5_innings` | ❌ Not on Kalshi public API — use FD/DK price |
| F5 Spread | `spreads_1st_5_innings` | ❌ Same |
| NRFI / YRFI | `h2h_1st_1_innings` | ❌ Same |

**⚠️ CRITICAL: Kalshi Market Availability — Confirmed from live diagnostic (June 2026)**

**Kalshi historical API is fully accessible** from GitHub Actions via `GET /historical/markets` and `GET /markets` (live endpoint for recent data within 3-month window). The historical cutoff as of June 2026 is approximately April 5, 2026 — all current season bets are in the live window.

**However: Kalshi does NOT post any MLB markets at the direct API level.** A full diagnostic sweep of all settled markets for a June 2026 game date found zero MLB-related markets. The Kalshi exchange is dominated by crypto prices, S&P 500, weather, sports scores (NBA Finals attendance, etc.), and esports — not granular MLB prop/game betting.

Kalshi posts MLB markets **only** via The Odds API's `us_ex` region aggregation, which exposes exactly three Kalshi MLB markets:
- ✅ ML (moneyline) — all games
- ✅ RL (runline, both sides) — most games
- ✅ Game Total (over/under) — most games, **always half-run increments**

Kalshi posts ZERO of the following (confirmed via direct API and The Odds API):
- ❌ Team Totals — NOT available on Kalshi
- ❌ F5 ML — NOT available on Kalshi
- ❌ F5 RL / F5 Spread — NOT available on Kalshi
- ❌ NRFI / YRFI — NOT available on Kalshi

**CLV for F5 and TT markets therefore requires The Odds API** (FanDuel/DraftKings closing lines via the per-event historical endpoint). The Kalshi direct historical API provides no incremental value for MLB CLV beyond what The Odds API already delivers.

**Implications for the model:**

1. **Game Totals:** Kalshi always posts half-run lines (7.5, 8.5, 9.0 — never whole numbers like 7, 8). Pinnacle may post whole-number lines (7, 8) that do not exist on Kalshi. **Edge calculation for totals MUST use Kalshi's line and Kalshi's VF — never Pinnacle's line.** If Kalshi has no total posted, no total bet is logged.

2. **Team Totals:** Bet on Pinnacle or DraftKings. VF comparison = Pinnacle VF. CLV source = Pinnacle or DK closing line. Label in bet log: `"book": "Pinnacle"` or `"book": "DraftKings"`.

3. **F5 ML/RL:** Bet on FanDuel or DraftKings (Pinnacle posts F5 RL but not F5 ML for most games). VF comparison = FanDuel/DK VF from two-sided market. CLV source = FanDuel/DK closing line.

4. **NRFI/YRFI:** Bet on FanDuel or DraftKings. VF and CLV from FD/DK.

**Per-market bet book and VF source (definitive):**

| Market | Bet Book | VF Source | CLV Source |
|---|---|---|---|
| ML | Kalshi | Kalshi VF | Kalshi close |
| RL | Kalshi | Kalshi VF | Kalshi close |
| Game Total | Kalshi (use Kalshi line only) | Kalshi VF | Kalshi close |
| Team Total | Pinnacle or DraftKings | Pinnacle VF | Pinnacle/DK close |
| F5 ML | FanDuel or DraftKings | FD/DK VF | FD/DK close |
| F5 RL | Pinnacle (if available) else FD/DK | Pinnacle/FD VF | Pinnacle/FD close |
| NRFI/YRFI | FanDuel or DraftKings | FD/DK VF | FD/DK close |

Additional markets are only available via the per-event endpoint. These must be fetched individually for each game on the slate.

### Fetch Sequence (called by GitHub Action)

**Step 1 — Bulk featured markets (one call, all games):**
```
GET /v4/sports/baseball_mlb/odds
  ?apiKey={ODDS_API_KEY}
  &regions=eu,us,us_ex
  &bookmakers=pinnacle,fanduel,draftkings,betmgm,kalshi
  &markets=h2h,spreads,totals,team_totals
  &oddsFormat=american
  &dateFormat=iso
```
Cost: 4 markets × 3 regions = 12 credits (bookmakers param overrides region cost — ~12 credits total)

**Step 2 — Per-event additional markets (one call per game, ~15 games):**
```
GET /v4/sports/baseball_mlb/events/{eventId}/odds
  ?apiKey={ODDS_API_KEY}
  &regions=eu,us,us_ex
  &bookmakers=pinnacle,fanduel,draftkings,betmgm,kalshi
  &markets=h2h_1st_5_innings,spreads_1st_5_innings,h2h_1st_1_innings
  &oddsFormat=american
```
Cost: 3 markets × ~15 games = ~45 credits per session

**Total per session: ~57 credits. Monthly budget at daily sessions: ~1,700 credits. Remaining for CLV/historical: ~18,300 credits/month.**

### Vercel API (legacy — still called by Action for non-odds data)
**URL:** https://edge-finder-api.vercel.app
Still used for: `/api/pitchers`, `/api/teamstats`, `/api/weather`, `/api/savant`, `/api/bullpen`
NOT used for: odds (fully replaced by The Odds API)
Note: Vercel is NOT directly accessible from Claude's network — it returns 403 immediately. **Never call Vercel endpoints directly from a Claude session.** Always trigger the `fetch-slate` GitHub Action and read results from `data/` files.

---

## Primary Data Path (unchanged)
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
| `data/slate.json` | Full slate — odds from The Odds API, pitchers, bullpen, model probs, edges |
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
| `bets.json` | `/bets.json` | Authoritative bet ledger — all logged, pending, and settled bets. **Flat JSON array — parse directly with `json.load(f)`, NOT via `.get('bets', [])`.** |

**Pull order at session start:** RULES.md → MODEL_CORE.md → SLATE_WORKFLOW.md → DATA_SOURCES.md → bets.json. All five must be pulled before any analysis or logging begins.

### Verify Before Using
Always check `data/meta.json` fetchedAt timestamp. If stale (>4 hours old), re-trigger the workflow before analyzing.

---

## SLATE.JSON — MARKET FIELDS (updated schema)

All odds fields in slate.json now follow the three-layer structure:

```json
"game": {
  // Layer 1 — Market reference
  "pinnacleML": { "away": -125, "home": +112 },
  "pinnacleVF": { "away": 0.549, "home": 0.451 },
  "pinnacleRL": { "away": { "line": -1.5, "price": +155 }, "home": { "line": +1.5, "price": -178 } },
  "pinnacleTotalLine": 8.5,
  "pinnacleTotalOver": -108,
  "pinnacleTotalUnder": -108,
  "fdML": { "away": -122, "home": +104 },
  "dkML": { "away": -124, "home": +106 },
  "betmgmML": { "away": -120, "home": +100 },
  // Layer 2 — Bet price (Kalshi)
  "kalshiML": { "away": +112, "home": -118 },
  "kalshiRL": { "away": { "line": -1.5, "price": +162 }, "home": { "line": +1.5, "price": -175 } },
  "kalshiTotalLine": 8.5,
  "kalshiTotalOver": -105,
  "kalshiTotalUnder": -112,
  // NOTE: Kalshi does NOT post team totals — TT bets use Pinnacle or DK
  // F5 and NRFI — per-event fetch
  "f5": {
    "pinnacleF5ML": { "away": -128, "home": +116 },
    "pinnacleF5RL": { "away": { "line": -0.5, "price": +185 }, "home": { "line": +0.5, "price": -215 } },
    "kalshiF5ML": { "away": +118, "home": -125 },
    "kalshiF5RL": { "away": { "line": -0.5, "price": +192 }, "home": { "line": +0.5, "price": -208 } }
  },
  "nrfi": {
    "pinnacleNRFI": -145,
    "pinnacleYRFI": +122,
    "kalshiNRFI": -138,
    "kalshiYRFI": +128
  }
}
```

---

## MARKET COMPARISON — EDGE DETECTION LOGIC

### Primary Edge Signal — Model vs Kalshi
**The model's projected probability minus Kalshi's implied probability is the edge.** The model IS the signal.

To vig-free a Kalshi two-sided market:
```
implied_away = 1 / (1 + 100/|away_line|)  [if away is minus]
implied_home = 1 / (1 + |home_line|/100)  [if home is plus]
vig = implied_away + implied_home - 1
vf_away = implied_away / (implied_away + implied_home)
vf_home = implied_home / (implied_away + implied_home)
```
Then: `edge = modelProb − vf_kalshi × calibration_factor`

### Pinnacle — Sanity Check Only
Pinnacle VF is computed and displayed alongside every edge, but is NOT subtracted from modelProb. It answers one question: *"Is the sharpest market in the world in the same ballpark as my model?"*

| Divergence (model vs Pinnacle VF) | Action |
|---|---|
| ≤5% | Proceed. Model and sharp market broadly agree. Edge is real. |
| 5–7% | Note the divergence. Review game notes for anything model may have missed. |
| >7% | Flag. Do not bet without a specific reason the model should be trusted over Pinnacle here. |

Pinnacle divergence alone is NOT a reason to skip a bet — it is a prompt to review. The model may be capturing something Pinnacle hasn't priced yet.

### Confirmation Books — Secondary Sanity Check
FanDuel, DraftKings, and BetMGM are additional reference points:
- If they agree with Pinnacle's direction and your model disagrees → stronger flag
- If they agree with your model and Pinnacle disagrees → Pinnacle may have stale data; note it

### Kalshi — Edge Target and Bet Execution
Kalshi implied probability is what the model is betting against. It is used for:
- Edge calculation (model − Kalshi VF)
- `betPrice` at time of bet logging
- `closingLine` at settlement via historical API

---

## CLV — KALSHI HISTORICAL SNAPSHOT (v3.0)

### Architecture Change from v2.x
Previous versions used manual web search to find Pinnacle closing lines. This was unreliable, degraded after 48 hours, and measured CLV against a book you can't bet. **v3.0 uses The Odds API historical endpoint to pull Kalshi's closing price automatically.** This is more honest — you're measuring whether you beat your own market's close.

### Closing Line Pull — Automated
At settlement, call:
```
GET /v4/historical/sports/baseball_mlb/odds
  ?apiKey={ODDS_API_KEY}
  &bookmakers=kalshi
  &regions=us_ex
  &markets=h2h
  &oddsFormat=american
  &date={GAME_DATE}T{FIRST_PITCH_TIME}Z
```
The API returns the closest snapshot at or before the timestamp provided. Use the actual first-pitch time in UTC. This gives you Kalshi's line at first pitch — the true closing line for your market.

**For F5/NRFI closing lines**, use the historical event odds endpoint:
```
GET /v4/historical/sports/baseball_mlb/events/{eventId}/odds
  ?apiKey={ODDS_API_KEY}
  &bookmakers=kalshi
  &markets=h2h_1st_5_innings,h2h_1st_1_innings
  &oddsFormat=american
  &date={FIRST_PITCH_TIME}Z
```

### Cost
- 10 credits per market per region for historical calls
- ML close: 10 credits per game settled
- F5+NRFI close: 20 credits per game settled (2 markets)
- Monthly budget impact at 5 bets/day settled: ~1,500 credits/month

### What to Store in bets.json
```json
"betPrice": +108,
"betTimeLine": +108,
"pinnacleVFAtBet": 0.537,
"closingLine": +102,
"closingLineSource": "Kalshi",
"closingLineTimestamp": "2026-06-02T18:10:00Z",
"clv": 2.8
```

Fields:
- `betPrice` — Kalshi price you received (your actual price)
- `betTimeLine` — same as betPrice; preserved as CLV insurance if historical pull fails
- `pinnacleVFAtBet` — Pinnacle VF at time of bet; preserved for model edge validation
- `closingLine` — Kalshi price at first pitch via historical API (American odds)
- `closingLineSource` — always "Kalshi" in v3.0
- `closingLineTimestamp` — first-pitch UTC timestamp
- `clv` — computed CLV%

### CLV Calculation
```python
def american_to_decimal(american):
    if american > 0: return (american / 100) + 1
    else: return (100 / abs(american)) + 1

bet_dec = american_to_decimal(bet_price)       # e.g. +108 → 2.08
close_dec = american_to_decimal(closing_line)  # e.g. +102 → 2.02

clv_pct = (bet_dec - close_dec) / close_dec * 100
# Result: positive = you beat the close (good), negative = line moved against you
```

### Settlement Window
**Settle within 7 days.** Historical API data is stable indefinitely — no degradation unlike public web sources. No longer a 48-hour constraint.

### Fallback if Historical Pull Fails
1. Use `betTimeLine` (Kalshi price at bet time) as closing line proxy → flag as "estimated"
2. Log `closingLine: null`, `clv: null` only if betTimeLine also unavailable
3. **Never fabricate or estimate a closing line.** Null is correct.

---

## Key Fields for Probability Engine

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
- `pitcher.ttoSplit` — xFIP difference 3rd TTO vs 1st TTO (F5 TTO adjustment input; skip if unavailable)
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
- If not available: fall back to `mlb.com/probable-pitchers` lineup cards (~3–4 hrs pre-game)

---

## Fallback Chain (only if GitHub Action fails)
1. `fetch_sports_data` tool — scores, standings, game_stats by game ID
2. `mlb.com/[team]/roster/probable-pitchers`
3. `fanduel.com/research/[away]-vs-[home]-mlb-odds-prediction-...`
4. Google: `"[Team A] vs [Team B] starting pitcher [date] 2026"`
5. Bleacher Nation series preview articles

---

## Manual Lookup Fallbacks

### Opener Role Lookup
- If starter averages <3 IP/start → pull 1st-inning xERA from Baseball Savant
- Available in `data/slate.json` under `pitcher.firstInningSplit` when Action runs
- URL pattern if manual: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Minimum 5 appearances required for data to be actionable

### FB% Lookup (for Park Factor Modifier)
Required for Coors, GABP, and Dodger Stadium games:
- `pitcher.fbPct` in slate.json (when available from Action)
- Manual fallback: Baseball Savant pitcher page → "Batted Ball" tab → GB%, FB%, LD% by season
- If unavailable: use standard park_adj, note "no FB% data — standard park_adj applied"

### TTO Split Lookup
- `pitcher.ttoSplit` in slate.json (if computed by Action)
- Manual fallback: Baseball Savant → "Game Log" → filter by "Batter Faced" to find 3rd TTO stats
- Minimum 10 starts with 3rd TTO exposure for data to be actionable
- If unavailable: note "no TTO data — tto_adj = 1.0 applied"

### Handedness Split Lookup
If `pitcher.vsLHH` / `pitcher.vsRHH` not in slate data:
- Baseball Savant pitcher page — Filter by "Split: vs LHH / vs RHH"
- **Minimum 50 PA sample required.** If sample <50 PA: note "insufficient split sample" and skip handedness adjustment. Do not guess.

### wRC+ and Barrel% Lookup
If `team.wrcPlus` not in teamstats.json:
- FanGraphs team batting: `https://www.fangraphs.com/leaders.aspx?pos=all&stats=bat&lg=all&qual=0&type=8&season=2026&team=0,ts&rost=0&age=0&filter=&players=0`
- Rolling 7/15 game R/G: Baseball Reference team game log or fetch_sports_data standings

### Bullpen xFIP Lookup
If `team.bullpen.xFIP` not in slate data:
- FanGraphs team pitching (relief only): filter by "Role: RP"
- Pull xFIP, ERA, BB/9 for each team's bullpen

---

## Banned Sources (JavaScript-rendered / broken)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `covers.com/sports/mlb/matchups` — nav bloat
- `statsapi.mlb.com` — returning 400 errors
