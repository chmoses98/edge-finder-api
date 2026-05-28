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

## Closing Line Pull (CLV Infrastructure — Required for Settlement)

### Why This Exists
CLV is only meaningful if computed against the **true closing line** at first pitch — not the line at bet-log time, which may be hours earlier. Without this, all CLV% values are estimates and the model's self-evaluation is corrupted. Clean CLV data is the foundation of long-term model improvement.

### Source
**The Odds API** — historical odds snapshots (paid tier required)
- Documentation: `https://the-odds-api.com/lol-api/`
- Endpoint: `/v4/historical/sports/baseball_mlb/odds`
- Parameters: `regions=us`, `markets=h2h,spreads,totals`, `bookmakers=pinnacle`, `date=<ISO8601 timestamp at first pitch>`

### When to Pull
**During settlement — same day or next day.** Do NOT let bets accumulate more than 24 hours without pulling closing lines. The Odds API retains historical snapshots reliably within 7 days, but same/next-day settlement is the required standard.

### What to Store in bets.json
For every settled bet, log all four fields:
```json
"closingLine": -115,
"closingLineSource": "Pinnacle",
"closingLineTimestamp": "2026-05-28T18:10:00Z",
"clv": 3.2
```
- `closingLine` — Pinnacle price at first pitch (American odds), raw value stored so CLV can be recomputed if formula changes
- `closingLineSource` — always "Pinnacle" unless unavailable; log "DraftKings" as fallback
- `closingLineTimestamp` — ISO8601 timestamp of the snapshot; must be within 15 minutes of first pitch
- `clv` — computed CLV% per MODEL_CORE Section 17; positive = beat the market

### Fallback Chain if Pinnacle Unavailable
1. DraftKings closing line — log `closingLineSource: "DraftKings"`
2. If neither available within retention window: log `closingLine: null`, `clv: null`, flag for manual review
3. **Never fabricate or estimate a closing line.** Null is the correct value. An estimated closing line corrupts the entire evaluation dataset.

### Plan Requirement
The Odds API free tier does NOT include historical snapshots — a paid tier is required. This is a non-negotiable infrastructure cost for a serious long-term model. Without it, CLV tracking is theater.
