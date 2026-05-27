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
| `data/teamstats.json` | Team hitting stats |
| `data/weather.json` | Park weather |
| `data/meta.json` | Fetch timestamp and date — verify this matches today before using |

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
