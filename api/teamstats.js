export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  const MLB_TEAM_ID_MAP = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
  };

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

  async function fetchRollingRpG(teamId, numDays) {
    try {
      const today = new Date();
      const endDate   = new Date(today); endDate.setDate(today.getDate() - 1);
      const startDate = new Date(today); startDate.setDate(today.getDate() - numDays);
      const fmt = d => d.toISOString().slice(0, 10);
      const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=${teamId}` +
                  `&startDate=${fmt(startDate)}&endDate=${fmt(endDate)}&hydrate=linescore`;
      const r = await fetch(url);
      if (!r.ok) return null;
      const d = await r.json();
      const games = [];
      for (const dt of (d.dates || [])) {
        for (const g of (dt.games || [])) {
          const st = g.status?.detailedState || '';
          if (!['Final','Game Over','Completed Early','Postponed'].includes(st)) continue;
          if (st === 'Postponed') continue;
          const ls = g.linescore?.teams;
          if (!ls) continue;
          const homeId = g.teams?.home?.team?.id;
          const awayId = g.teams?.away?.team?.id;
          if (homeId === teamId && ls.home?.runs != null) games.push(ls.home.runs);
          else if (awayId === teamId && ls.away?.runs != null) games.push(ls.away.runs);
        }
      }
      if (!games.length) return null;
      return Math.round((games.reduce((a, b) => a + b, 0) / games.length) * 100) / 100;
    } catch(e) { return null; }
  }

  // NEW: Fetch team wOBA and offensive FB% from Baseball Savant team batting leaderboard
  async function fetchSavantTeamBatting(year) {
    try {
      const url = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter&filter=&min=1&selections=xwoba,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate,fb_percent&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true&groupBy=team`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      if (!r.ok) return {};
      const rows = parseCSV(await r.text());
      const teamData = {};
      for (const row of rows) {
        // Team abbreviation in Savant grouped output is in 'team_name' or 'player_name' field
        const teamAbbr = row['team_name'] || row['player_name'] || row['Team'] || row['team'];
        if (!teamAbbr) continue;
        const xwoba = pf(row['xwoba'] || row['xwOBA']);
        const fbPct = pf(row['fb_percent'] || row['FB%'] || row['fb%']);
        const bbPct = pf(row['bb_percent']);
        const kPct  = pf(row['k_percent']);
        const hardHit = pf(row['hard_hit_percent']);
        const barrel  = pf(row['barrel_batted_rate']);
        if (xwoba !== null || fbPct !== null) {
          teamData[teamAbbr.trim().toUpperCase()] = { xwoba, fbPct, bbPct, kPct, hardHit, barrel };
        }
      }
      return teamData;
    } catch(e) { return {}; }
  }

  // NEW: Fetch individual batter wOBA — used for lineup adjustment
  // Returns a map of player_id -> xwOBA
  async function fetchBatterWOBA(year) {
    try {
      const url = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter&filter=&min=10&selections=xwoba&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      if (!r.ok) return {};
      const rows = parseCSV(await r.text());
      const batters = {};
      for (const row of rows) {
        const id = row['player_id'];
        const xwoba = pf(row['xwoba'] || row['xwOBA']);
        if (id && xwoba !== null) batters[id] = xwoba;
      }
      return batters;
    } catch(e) { return {}; }
  }

  try {
    const [seasonRes, standingsRes, savantTeamData, batterWOBA] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division,league'),
      fetchSavantTeamBatting('2026'),   // NEW
      fetchBatterWOBA('2026'),           // NEW
    ]);

    const [seasonData, standingsData] = await Promise.all([
      seasonRes.json(),
      standingsRes.json()
    ]);

    const seasonStats = {};
    const teamIdByAbbr = {};
    const splits = seasonData?.stats?.[0]?.splits || [];

    const opsValues = [];
    for (const rec of splits) {
      const ops = pf(rec.stat?.ops);
      if (ops) opsValues.push(ops);
    }
    const lgOPS = opsValues.length > 0
      ? opsValues.reduce((a, b) => a + b, 0) / opsValues.length
      : 0.720;

    for (const rec of splits) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      const s = rec.stat || {};
      const gp = s.gamesPlayed || 1;
      const ops = pf(s.ops);
      const teamId = rec.team?.id;
      teamIdByAbbr[abbr] = teamId;

      // wRC+ proxy kept for backwards compat
      const wrcPlus = ops !== null && lgOPS > 0 ? Math.round(ops / lgOPS * 100) : 100;

      // NEW: pull Savant team batting data (wOBA, FB%)
      const savant = savantTeamData[abbr] || {};

      seasonStats[abbr] = {
        teamId, name: rec.team?.name, abbr,
        gamesPlayed: gp,
        runs: s.runs, hits: s.hits, doubles: s.doubles, triples: s.triples,
        homeRuns: s.homeRuns, strikeOuts: s.strikeOuts, baseOnBalls: s.baseOnBalls,
        avg: s.avg, obp: s.obp, slg: s.slg, ops: s.ops, atBats: s.atBats,
        runsPerGame: (s.runs / gp).toFixed(2),
        wrcPlus,
        lgOPS: Math.round(lgOPS * 1000) / 1000,
        // NEW fields
        teamWOBA:   savant.xwoba  ?? null,   // team season xwOBA from Savant — lineup adj baseline
        teamFBPct:  savant.fbPct  ?? null,   // team batting FB% — offensive park factor modifier
        teamBBPct:  savant.bbPct  ?? null,
        teamKPct:   savant.kPct   ?? null,
        teamHardHit: savant.hardHit ?? null,
        teamBarrel:  savant.barrel  ?? null,
      };
    }

    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins: team.wins, losses: team.losses, pct: team.winningPercentage,
          streak: team.streak?.streakCode,
          runsScored: team.runsScored, runsAllowed: team.runsAllowed,
          runDiff: team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank, leagueRank: team.leagueRank,
          gb: team.gamesBack, divisionGb: team.divisionGamesBack,
        };
      }
    }

    const allAbbrs = new Set([...Object.keys(seasonStats), ...Object.keys(standings)]);
    const rollingData = {};
    await Promise.all([...allAbbrs].map(async (abbr) => {
      const teamId = teamIdByAbbr[abbr] || MLB_TEAM_ID_MAP[abbr];
      if (!teamId) return;
      const [r7, r15] = await Promise.all([
        fetchRollingRpG(teamId, 7),
        fetchRollingRpG(teamId, 15),
      ]);
      rollingData[abbr] = { last7RpG: r7, last15RpG: r15 };
    }));

    const teams = {};
    for (const abbr of allAbbrs) {
      teams[abbr] = {
        ...(seasonStats[abbr] || { abbr }),
        record:    standings[abbr] || null,
        last7RpG:  rollingData[abbr]?.last7RpG  ?? null,
        last15RpG: rollingData[abbr]?.last15RpG ?? null,
      };
    }

    const result = {
      updatedAt:      new Date().toISOString(),
      teamCount:      Object.keys(teams).length,
      lgOPS:          Math.round(lgOPS * 1000) / 1000,
      wrcSource:      'ops_proxy',
      wobaSource:     'savant_xwoba',       // NEW
      fbPctSource:    'savant_team_batting', // NEW
      rollingSource:  'schedule_linescore',
      batterWOBA,   // NEW: individual batter wOBA map (player_id -> xwOBA) for lineup adj
      teams,
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch(error) {
    const result = { error: error.message };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
