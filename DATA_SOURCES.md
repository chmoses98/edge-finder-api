# DATA_SOURCES.md

## Primary Backend
**URL:** https://edge-finder-api.vercel.app
Endpoints: `/api/slate` `/api/odds` `/api/pitchers` `/api/kalshi` `/api/teamstats` `/api/weather`
Status: Intermittently unavailable — use fallback chain below if blocked.

## Fallback Chain (in order)
1. `fetch_sports_data` tool — scores, standings, game_stats by game ID
2. `mlb.com/[team]/roster/probable-pitchers`
3. `fanduel.com/research/[away]-vs-[home]-mlb-odds-prediction-...`
4. Google: `"[Team A] vs [Team B] starting pitcher May [date] 2026"`
5. Bleacher Nation series preview articles

## Required Per Slate
- Verified same-day starters (probable ≠ confirmed — always verify day-of)
- Live Pinnacle vig-free lines (FD/DK fallback)
- Kalshi ML implied probs
- Team records, streaks, run diff
- Park type (dome vs open air)
- Weather for open-air parks → **postponement flag only**

## Opener Role Lookup
- If starter averages <3 IP/start → pull 1st-inning ERA/xERA from Baseball Savant
- URL pattern: `https://baseballsavant.mlb.com/savant-player/[name]-[id]?stats=statcast&playerType=pitcher`
- Filter by inning = 1 in splits tab
- Minimum 5 appearances required for data to be actionable

## Banned Sources (JavaScript-rendered / broken)
- `mlb.com/starting-lineups` — cached shell only
- `rotowire.com/baseball/daily-lineups.php`
- `covers.com/sports/mlb/matchups` — nav bloat
- `statsapi.mlb.com` — returning 400 errors
