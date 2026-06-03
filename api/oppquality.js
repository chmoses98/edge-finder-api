/**
 * api/oppquality.js — v1.0
 * 
 * Computes rolling opponent starter quality for all 30 teams.
 * For each team: fetches last 15 completed games, identifies actual starter
 * from each game's boxscore, looks up that starter's season FIP from the
 * Savant leaderboard, and returns the average → used as opponent quality
 * adjustment to the rolling R/G baseline in MODEL_CORE Section 1 Step 2.
 *
 * Output per team:
 *   oppXFIPavg       — avg xFIP of starters faced over last 15 games (null if <5 games resolved)
 *   oppQualityAdj    — (oppXFIPavg - 4.00) * 0.08, capped ±0.2
 *   gamesResolved    — how many games had a starter xFIP successfully resolved
 *   gamesTotal       — total completed games found in the window
 *   confidence       — 'full' (≥10 resolved), 'partial' (5-9), 'low' (<5, use with caution)
 */

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { season = '2026', days = '21' } = req.query;
  // Fetch last N calendar days — use 21 to reliably capture 15 completed games
  const WINDOW_DAYS = parseInt(days);
  const LEAGUE_AVG_XFIP = 4.00;
  const MIN_GAMES_FOR_SIGNAL = 5;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    const splitLine = (line) => {
      const result = []; let current = ''; let inQuotes = false;
      for (const ch of line) {
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
        else { current += ch; }
      }
      result.push(current.trim());
      return result;
    };
    const headers = splitLine(lines[0]);
    return lines.slice(1).map(line => {
      const values = splitLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = values[i] || ''; });
      return obj;
    });
  }

  const MLB_TEAM_ID_MAP = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
  };

  const MLB_ID_TO_ABBR = Object.fromEntries(
    Object.entries(MLB_TEAM_ID_MAP).map(([abbr, id]) => [id, abbr])
  );

  // ── Step 1: Fetch Savant pitcher leaderboard for season FIP lookup ─────────
  // This gives us xFIP proxy (season FIP) for any pitcher by player_id
  async function fetchSavantPitcherFIPs() {
    try {
      const url = `https://baseballsavant.mlb.com/leaderboard/custom?year=${season}&type=pitcher` +
        `&filter=&min=1&selections=k_percent,bb_percent,xera,hard_hit_percent&` +
        `chart=false&x=xera&y=xera&r=no&chartType=beeswarm&csv=true`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      if (!r.ok) return {};
      const rows = parseCSV(await r.text());
      const fipMap = {};
      for (const row of rows) {
        const id   = row['player_id'];
        const xera = pf(row['xera']);     // xERA as xFIP proxy (corr ~0.88 over season)
        const kPct = pf(row['k_percent']);
        const bbPct = pf(row['bb_percent']);
        if (!id) continue;
        fipMap[id] = { xera, kPct, bbPct };
      }
      return fipMap;
    } catch(e) { return {}; }
  }

  // ── Step 2: Fetch season FIP for a specific pitcher via MLB Stats API ──────
  // Used as fallback when a pitcher isn't in the Savant leaderboard (small sample)
  const pitcherFIPCache = {};
  async function fetchPitcherSeasonFIP(pitcherId) {
    if (pitcherFIPCache[pitcherId] !== undefined) return pitcherFIPCache[pitcherId];
    try {
      const r = await fetch(
        `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats?stats=season&group=pitching&season=${season}&gameType=R`
      );
      if (!r.ok) { pitcherFIPCache[pitcherId] = null; return null; }
      const d = await r.json();
      const s = d?.stats?.[0]?.splits?.[0]?.stat;
      if (!s) { pitcherFIPCache[pitcherId] = null; return null; }
      const ipRaw = parseFloat(s.inningsPitched || '0');
      const ip = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
      if (ip < 3) { pitcherFIPCache[pitcherId] = null; return null; }
      const hr = parseInt(s.homeRuns || 0);
      const bb = parseInt(s.baseOnBalls || 0);
      const k  = parseInt(s.strikeOuts || 0);
      const FIP_CONST = 3.10;
      const fip = Math.round(((13 * hr + 3 * bb - 2 * k) / ip + FIP_CONST) * 100) / 100;
      pitcherFIPCache[pitcherId] = fip;
      return fip;
    } catch(e) { pitcherFIPCache[pitcherId] = null; return null; }
  }

  // ── Step 3: Fetch last N completed games for a team ───────────────────────
  async function fetchRecentGames(teamId, windowDays) {
    try {
      const today    = new Date();
      const endDate  = new Date(today); endDate.setDate(today.getDate() - 1);
      const startDate = new Date(today); startDate.setDate(today.getDate() - windowDays);
      const fmt = d => d.toISOString().slice(0, 10);

      const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=${teamId}` +
        `&startDate=${fmt(startDate)}&endDate=${fmt(endDate)}&gameType=R&hydrate=probablePitcher`;
      const r = await fetch(url);
      if (!r.ok) return [];

      const data = await r.json();
      const games = [];
      const COMPLETED = ['Final', 'Game Over', 'Completed Early'];

      for (const dt of (data.dates || [])) {
        for (const g of (dt.games || [])) {
          if (!COMPLETED.includes(g.status?.detailedState)) continue;
          const awayId = g.teams?.away?.team?.id;
          const homeId = g.teams?.home?.team?.id;
          const awayAbbr = MLB_ID_TO_ABBR[awayId] || g.teams?.away?.team?.abbreviation;
          const homeAbbr = MLB_ID_TO_ABBR[homeId] || g.teams?.home?.team?.abbreviation;

          // Identify which side is the opponent (the team that isn't teamId)
          const isHome  = homeId === teamId;
          const oppSide = isHome ? 'away' : 'home';
          const oppTeam = g.teams?.[oppSide];

          // probablePitcher on a completed game = the starter who actually pitched
          // This is the most reliable source without needing a boxscore call
          const oppProbable = oppTeam?.probablePitcher;

          games.push({
            gamePk:    g.gamePk,
            date:      dt.date,
            awayAbbr,
            homeAbbr,
            oppSide,
            oppStarterId:   oppProbable?.id   ? String(oppProbable.id)   : null,
            oppStarterName: oppProbable?.fullName || null,
          });
        }
      }
      // Return last 15 completed games
      return games.slice(-15);
    } catch(e) { return []; }
  }

  // ── Step 4: For games where probablePitcher is null, fetch boxscore ───────
  // Boxscore gives us the actual pitchers[] array — first entry is the starter
  async function fetchActualStarter(gamePk, oppSide) {
    try {
      const r = await fetch(`https://statsapi.mlb.com/api/v1/game/${gamePk}/boxscore`);
      if (!r.ok) return null;
      const box = await r.json();
      const pitchers = box?.teams?.[oppSide]?.pitchers || [];
      if (!pitchers.length) return null;
      const starterId = String(pitchers[0]);
      const players = box?.teams?.[oppSide]?.players || {};
      const starterData = players[`ID${starterId}`];
      const name = starterData?.person?.fullName || null;
      return { id: starterId, name };
    } catch(e) { return null; }
  }

  // ── Step 5: Compute opponent quality adj for one team ─────────────────────
  async function computeTeamOppQuality(teamId, savantFIPs, windowDays) {
    const games = await fetchRecentGames(teamId, windowDays);
    if (!games.length) return { oppXFIPavg: null, oppQualityAdj: null, gamesResolved: 0, gamesTotal: 0, confidence: 'low' };

    // For games missing the probable starter, fetch boxscore (batch, max 5 concurrent)
    const missingStarterGames = games.filter(g => !g.oppStarterId);
    const batchSize = 5;
    for (let i = 0; i < missingStarterGames.length; i += batchSize) {
      const batch = missingStarterGames.slice(i, i + batchSize);
      const results = await Promise.all(
        batch.map(g => fetchActualStarter(g.gamePk, g.oppSide))
      );
      results.forEach((starter, idx) => {
        if (starter) {
          batch[idx].oppStarterId   = starter.id;
          batch[idx].oppStarterName = starter.name;
        }
      });
    }

    // Resolve xFIP for each opponent starter
    const xfipValues = [];
    for (const game of games) {
      if (!game.oppStarterId) continue;
      const savant = savantFIPs[game.oppStarterId];
      let xfip = savant?.xera ?? null;  // xERA as primary proxy from Savant leaderboard

      // Fallback: compute season FIP from MLB Stats API
      if (xfip === null) {
        xfip = await fetchPitcherSeasonFIP(game.oppStarterId);
      }

      if (xfip !== null) {
        xfipValues.push(xfip);
        game.resolvedXFIP = xfip;
      }
    }

    const gamesResolved = xfipValues.length;
    const gamesTotal    = games.length;

    if (gamesResolved < MIN_GAMES_FOR_SIGNAL) {
      return { oppXFIPavg: null, oppQualityAdj: null, gamesResolved, gamesTotal, confidence: 'low', games };
    }

    const avg = xfipValues.reduce((a, b) => a + b, 0) / xfipValues.length;
    const oppXFIPavg  = Math.round(avg * 100) / 100;
    // Positive adj = faced weak pitching → baseline inflated → reduce projection
    // Negative adj = faced tough pitching → baseline suppressed → increase projection
    const rawAdj      = (oppXFIPavg - LEAGUE_AVG_XFIP) * 0.08;
    const oppQualityAdj = Math.round(Math.max(-0.2, Math.min(0.2, rawAdj)) * 1000) / 1000;

    const confidence  = gamesResolved >= 10 ? 'full' : 'partial';

    return { oppXFIPavg, oppQualityAdj, gamesResolved, gamesTotal, confidence, games };
  }

  // ── Main: run all 30 teams in parallel (batched to avoid rate limits) ─────
  try {
    const savantFIPs = await fetchSavantPitcherFIPs();
    const savantPitcherCount = Object.keys(savantFIPs).length;

    const allAbbrs = Object.keys(MLB_TEAM_ID_MAP);
    const results  = {};

    // Process in batches of 6 teams to avoid hammering MLB Stats API
    const TEAM_BATCH = 6;
    for (let i = 0; i < allAbbrs.length; i += TEAM_BATCH) {
      const batch = allAbbrs.slice(i, i + TEAM_BATCH);
      const batchResults = await Promise.all(
        batch.map(abbr => computeTeamOppQuality(MLB_TEAM_ID_MAP[abbr], savantFIPs, WINDOW_DAYS))
      );
      batch.forEach((abbr, idx) => { results[abbr] = batchResults[idx]; });
    }

    return res.status(200).json({
      ok: true,
      season,
      fetchedAt:          new Date().toISOString(),
      windowDays:         WINDOW_DAYS,
      savantPitcherCount,
      leagueAvgXFIP:      LEAGUE_AVG_XFIP,
      teams:              results,
    });

  } catch(err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
