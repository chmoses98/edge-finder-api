# DATA_SOURCES.md
# Last updated: May 30, 2026 — v2.1

## Primary Data Path (use this every session)
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
| `data/slate.json` | Full slate — odds, pitchers, Kalshi, bullpen, model probs, edges |
| `data/pitchers.json` | Confirmed starters with IDs |
| `data/teamstats.json` | Team hitting stats including wRC+, xOPS, barrel%, rolling R/G |
| `data/weather.json` | Park weather |
| `data/meta.json` | Fetch timestamp and date — verify before using |

### Model Files (pulled separately via GitHub raw content API at session start)
These are not written by the Action — they are the model's source-of-truth documents and bet ledger. Pull at the start of every session using the GitHub raw content URL.

| File | Path | Contents |
|---|---|---|
| `RULES.md` | `/RULES.md` | All betting rules and gate definitions |
| `MODEL_CORE.md` | `/MODEL_CORE.md` | Probability engine, sizing, calibration, market rules |
| `SLATE_WORKFLOW.md` | `/SLATE_WORKFLOW.md` | Session workflow and bet entry format |
| `DATA_SOURCES.md` | `/DATA_SOURCES.md` | Data field definitions and fallback chain |
| `bets.json` | `/bets.json` | Authoritative bet ledger — all logged, pending, and settled bets |

**Pull order at session start:** RULES.md → MODEL_CORE.md → SLATE_WORKFLOW.md → DATA_SOURCES.md → bets.json. All five must be pulled before any analysis or logging begins. bets.json is required to avoid duplicate bet IDs and to run the calibration script.

### Key Fields for Probability Engine
These fields feed directly into MODEL_CORE Section 1. Pull and verify before analysis.

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

**Market fields:**
- `game.pinnacleML` — Pinnacle ML price (primary market comparison; VF-adjust before using)
- `game.pinnacleVF` — Pinnacle vig-free probability if pre-computed by Action
- `game.kalshiPct` — Kalshi implied probability (tertiary reference only)
- `game.f5.awayF5Pct` / `game.f5.homeF5Pct` — model F5 probabilities (estimated; confirm actual price on FD/DK before logging Medium/High)

### Verify Before Using
Always check `data/meta.json` fetchedAt timestamp. If stale (>4 hours old), re-trigger the workflow before analyzing.

---

## Market Comparison Priority
When evaluating edge:
1. **Pinnacle vig-free** — primary market. Sharpest line available. This is the model's comparison point.
2. **FanDuel / DraftKings vig-free** — fallback if Pinnacle unavailable.
3. **Kalshi** — tertiary sanity check only. Thinner market, frequently stale. A Kalshi divergence not confirmed by Pinnacle is noise.

To vig-free a Pinnacle two-sided market:
```
implied_away = 1 / (1 + 100/|away_line|)  [if away is minus]
implied_home = 1 / (1 + |home_line|/100)  [if home is plus]
vig = implied_away + implied_home - 1
vf_away = implied_away / (implied_away + implied_home)
vf_home = implied_home / (implied_away + implied_home)
```

---

## Vercel API (called by GitHub Action — not called directly)
**URL:** https://edge-finder-api.vercel.app
Endpoints: `/api/slate` `/api/odds` `/api/pitchers` `/api/kalshi` `/api/teamstats` `/api/weather` `/api/savant` `/api/bullpen`
Note: Vercel API is NOT directly accessible from Claude's network. Always go through the GitHub Action.

---

## Fallback Chain (only if GitHub Action fails)
1. `fetch_sports_data` tool — scores, standings, game_stats by game ID
2. `mlb.com/[team]/roster/probable-pitchers`
3. `fanduel.com/research/[away]-vs-[home]-mlb-odds-prediction-...`
4. Google: `"[Team A] vs [Team B] starting pitcher [date] 2026"`
5. Bleacher Nation series preview articles

---

## Opener Role Lookup
- If starter averages <3 IP/start → pull 1st-inning xERA from Baseball Savant
- Available in `data/slate.json` under `pitcher.firstInningSplit` when Action runs
- URL pattern if manual: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Minimum 5 appearances required for data to be actionable

---

## FB% Lookup (for Park Factor Modifier)
Required for Coors, GABP, and Dodger Stadium games:
- `pitcher.fbPct` in slate.json (when available from Action)
- Manual fallback: Baseball Savant pitcher page → "Batted Ball" tab → GB%, FB%, LD% by season
- If unavailable: use standard park_adj, note "no FB% data — standard park_adj applied"

---

## TTO Split Lookup
Required for F5 projections when pitcher is expected to reach inning 5:
- `pitcher.ttoSplit` in slate.json (if computed by Action)
- Manual fallback: Baseball Savant → "Game Log" → filter by "Batter Faced" to find 3rd TTO stats
- Minimum 10 starts with 3rd TTO exposure for data to be actionable
- If unavailable: note "no TTO data — tto_adj = 1.0 applied"

---

## Handedness Split Lookup (Manual Fallback)
If `pitcher.vsLHH` / `pitcher.vsRHH` not in slate data:
- Baseball Savant pitcher page: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Filter by "Split: vs LHH / vs RHH" — pull K%, whiff%, xFIP by handedness
- **Minimum 50 PA sample required.** If sample <50 PA: note "insufficient split sample" and skip handedness adjustment. Do not guess.

---

## wRC+ and Barrel% Lookup (Manual Fallback)
If `team.wrcPlus` not in teamstats.json:
- FanGraphs team batting: `https://www.fangraphs.com/leaders.aspx?pos=all&stats=bat&lg=all&qual=0&type=8&season=2026&team=0,ts&rost=0&age=0&filter=&players=0`
- Pull wRC+, BB%, K%, Hard Hit%, Barrel%
- Rolling 7/15 game R/G: Baseball Reference team game log or fetch_sports_data standings

---

## Bullpen xFIP Lookup (Manual Fallback)
If `team.bullpen.xFIP` not in slate data:
- FanGraphs team pitching (relief only): filter by "Role: RP"
- Pull xFIP, ERA, BB/9 for each team's bullpen
- Use 14-day ERA as recency signal if season xFIP seems stale

---

## Pinnacle Vig-Free Lookup (Manual)
If Pinnacle not available via Action or web search:
- OddsPortal: `https://www.oddsportal.com/baseball/usa/mlb/`
- Action Network: `https://www.actionnetwork.com/mlb/odds`
- Note the source in `pinnacleVFPct` field and flag as "estimated from [source]" if not directly from Pinnacle

---

## Banned Sources (JavaScript-rendered / broken)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `covers.com/sports/mlb/matchups` — nav bloat
- `statsapi.mlb.com` — returning 400 errors

---

## Closing Line Pull (CLV Infrastructure — Required at Settlement)

### Why This Exists
CLV is only meaningful if computed against the **true closing line** at first pitch. Without this, all CLV% values are estimates and model self-evaluation is corrupted.

### Pre-Bet Line Capture (New in v2.0)
At bet-log time, record `betTimeLine` — the current Pinnacle line at the moment of logging. This is separate from `closingLine`. It provides a CLV anchor even if closing-line data is unavailable at settlement. Do this for every bet, every session.

### Closing Line Pull Protocol
At settlement, web search the closing line for each bet. Search queries:
- `"Pinnacle closing line [TEAM] ML [DATE]"`
- `"[TEAM A] vs [TEAM B] closing odds [DATE] Pinnacle"`
- OddsPortal and Action Network both surface final lines publicly

### Settlement Window — CRITICAL
**Settle within 48 hours.** Closing line data on public sites degrades after ~48 hours. Do not let slates pile up.

### What to Store in bets.json
```json
"betTimeLine": -148,
"closingLine": -115,
"closingLineSource": "Pinnacle",
"closingLineTimestamp": "2026-05-28T18:10:00Z",
"clv": 3.2
```

- `betTimeLine` — Pinnacle line at bet-log time (new — CLV insurance)
- `closingLine` — Pinnacle price at first pitch (American odds)
- `closingLineSource` — "Pinnacle" (primary), "ActionNetwork" or "OddsPortal" (fallback)
- `closingLineTimestamp` — date of the game; exact first-pitch timestamp if available
- `clv` — computed CLV% per MODEL_CORE Section 17

### Fallback Chain if Pinnacle Not Found
1. Action Network closing line → `closingLineSource: "ActionNetwork"`
2. OddsPortal → `closingLineSource: "OddsPortal"`
3. If no source found within 48hr window: log `closingLine: null`, `clv: null`, flag for manual review
4. **Never fabricate or estimate a closing line.** Null is correct.

### CLV Calculation
```
# For money line bets:
# Convert American to decimal
def american_to_decimal(american):
    if american > 0: return (american / 100) + 1
    else: return (100 / abs(american)) + 1

bet_dec = american_to_decimal(bet_price)
close_dec = american_to_decimal(closing_line)

# CLV = how much better your price was vs closing
clv_pct = (bet_dec - close_dec) / close_dec * 100
```

### Future Upgrade Path
When model is consistently profitable: upgrade to The Odds API paid tier for automated historical snapshot pulls. Data schema above is already compatible.
