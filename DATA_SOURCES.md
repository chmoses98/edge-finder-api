# DATA_SOURCES.md

## Primary Data Path (use this every session)
1. Trigger the `fetch-slate` GitHub Action via API
2. Wait ~40 seconds for it to complete
3. Read results from `data/` in the repo via GitHub API

### Trigger Call
```
POST https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Headers: Authorization: token <token>
Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
```

### Data Files (written by Action)
| File | Contents |
|---|---|
| `data/slate.json` | Full slate — odds, pitchers, Savant, Kalshi, bullpen, model probs, edges |
| `data/pitchers.json` | Confirmed starters with IDs |
| `data/teamstats.json` | Team hitting stats including wRC+, xOPS, barrel%, rolling R/G |
| `data/weather.json` | Park weather |
| `data/meta.json` | Fetch timestamp and date — verify this matches today before using |

### Key Fields for Probability Engine
These fields feed directly into MODEL_CORE Section 1. Pull and verify before analysis:

**Starter fields (in slate.json or pitchers.json):**
- `pitcher.xFIP` — season xFIP (primary)
- `pitcher.xERA` — season xERA (secondary/comparison)
- `pitcher.recentXFIP` — last 5 starts xFIP average
- `pitcher.kPer9` — K/9
- `pitcher.bbPer9` — BB/9
- `pitcher.avgIP` — average IP per start (opener check)
- `pitcher.firstInningSplit` — 1st-inning xERA (for NRFI/YRFI)
- `pitcher.vsLHH` / `pitcher.vsRHH` — platoon K% and xFIP splits

**Team offense fields (in teamstats.json):**
- `team.wrcPlus` — season wRC+
- `team.xOPS` — expected OPS
- `team.barrelPct` — barrel rate (bounceback/regression signal)
- `team.last7RpG` — rolling 7-game R/G
- `team.last15RpG` — rolling 15-game R/G
- `team.firstInningRpG` — 1st-inning run rate (for NRFI/YRFI)

**Bullpen fields (in slate.json):**
- `team.bullpen.xFIP` — bullpen xFIP
- `team.bullpen.recentERA` — last 14 days ERA
- `team.bullpen.last3DaysIP` — workload flag (fatigue at 15+ IP)

**Lineup fields (in slate.json if available):**
- `game.homeLineup` / `game.awayLineup` — confirmed lineup cards
- If not available: fall back to `mlb.com/probable-pitchers` lineup cards (~3–4 hrs pre-game)

### Verify Before Using
Always check `data/meta.json` fetchedAt timestamp. If it's stale (>4 hours old), re-trigger the workflow before analyzing.

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

## Banned Sources (JavaScript-rendered / broken)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `covers.com/sports/mlb/matchups` — nav bloat
- `statsapi.mlb.com` — returning 400 errors

---

## Handedness Split Lookup (Manual Fallback)
If `pitcher.vsLHH` / `pitcher.vsRHH` not in slate data:
- Baseball Savant pitcher page: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Filter by "Split: vs LHH / vs RHH" — pull K%, whiff%, xFIP by handedness
- Minimum 50 PA sample required for the split to be actionable
- If sample <50 PA: note "insufficient split sample" and skip handedness adjustment

## wRC+ and Barrel% Lookup (Manual Fallback)
If `team.wrcPlus` not in teamstats.json:
- FanGraphs team batting: `https://www.fangraphs.com/leaders.aspx?pos=all&stats=bat&lg=all&qual=0&type=8&season=2026&team=0,ts&rost=0&age=0&filter=&players=0`
- Pull wRC+, BB%, K%, Hard Hit%, Barrel%
- Rolling 7/15 game R/G: Baseball Reference team game log or fetch_sports_data standings

## Bullpen xFIP Lookup (Manual Fallback)
If `team.bullpen.xFIP` not in slate data:
- FanGraphs team pitching (relief only): filter by "Role: RP"
- Pull xFIP, ERA, BB/9 for each team's bullpen
- Use 14-day ERA as recency signal if season xFIP seems stale

---

## Banned Sources (JavaScript-rendered / broken)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `covers.com/sports/mlb/matchups` — nav bloat
- `statsapi.mlb.com` — returning 400 errors

---

## Closing Line Pull (CLV Infrastructure — Required at Settlement)

### Why This Exists
CLV is only meaningful if computed against the **true closing line** at first pitch — not the line at bet-log time, which may be hours earlier. Without this, all CLV% values are estimates and the model's self-evaluation is corrupted. Clean CLV data is the foundation of long-term model improvement.

### How It Works (Current Protocol)
At settlement, Claude web searches the closing line for each bet. This is automatic within the session — no paid API required. Search queries:
- `"Pinnacle closing line [TEAM] ML [DATE]"`
- `"[TEAM A] vs [TEAM B] closing odds [DATE] Pinnacle"`
- OddsPortal and Action Network both surface final lines publicly and are reliable sources

### Settlement Window — CRITICAL
**Settle within 48 hours.** Closing line data on public sites degrades and gets overwritten after ~48 hours. If settlement runs more than 2 days after the game, closing lines may be unrecoverable. Do not let slates pile up.

### What to Store in bets.json
For every settled bet, log all four fields:
```json
"closingLine": -115,
"closingLineSource": "Pinnacle",
"closingLineTimestamp": "2026-05-28T18:10:00Z",
"clv": 3.2
```
- `closingLine` — Pinnacle price at first pitch (American odds), raw value; stored so CLV can be recomputed if formula changes
- `closingLineSource` — "Pinnacle" (primary), "ActionNetwork" or "OddsPortal" (fallback)
- `closingLineTimestamp` — date of the game; exact first-pitch timestamp if available from search results
- `clv` — computed CLV% per MODEL_CORE Section 17; positive = beat the market

### Fallback Chain if Pinnacle Not Found
1. Action Network closing line — log `closingLineSource: "ActionNetwork"`
2. OddsPortal — log `closingLineSource: "OddsPortal"`
3. If no source found within 48hr window: log `closingLine: null`, `clv: null`, flag for manual review
4. **Never fabricate or estimate a closing line.** Null is correct. An estimated closing line corrupts the dataset.

### Future Upgrade Path
When the model becomes consistently profitable: upgrade to The Odds API paid tier for automated historical snapshot pulls. The data schema above is already compatible — swap the source, not the structure.
