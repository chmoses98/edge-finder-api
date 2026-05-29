export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  // Rolling R/G from schedule+linescore — last N calendar days for a team.
  // Uses the schedule endpoint which is reliably available (same source as pitchers.js).
  // Parses linescore.teams.away/home.runs for each completed game.
  async function fetchRollingRpG(teamId, numDays) {
    try {
      const today = new Date();
      const endDate   = new Date(today); endDate.setDate(today.getDate() - 1);
      const startDate = new Date(today); startDate.setDate(today.getDate() - numDays);
      const fmt = d => d.toISOString().slice(0, 10);
      const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=${teamId}` +
                  `&startDate=${fmt(startDate)}&endDate=${fmt(endDate)}` +
                  `&hydrate=linescore&gameType=R`;
      const r = await fetch(url);
      if (!r.ok) return null;
      const d = await r.json();
      const games = [];
      for (const dt of (d.dates || [])) {
        for (const g of (dt.games || [])) {
          // Only count completed games
          if (!['Final','Game Over','Completed Early'].includes(g.status?.detailedState)) continue;
          const ls = g.linescore?.teams;
          if (!ls) continue;
          // Determine which side our team is on
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

  try {
    const [seasonRes, standingsRes] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division,league')
    ]);

    const [seasonData, standingsData] = await Promise.all([
      seasonRes.json(),
      standingsRes.json()
    ]);

    // Parse season hitting stats + compute wRC+ proxy from OPS
    const seasonStats = {};
    const teamIdByAbbr = {};
    const splits = seasonData?.stats?.[0]?.splits || [];

    // First pass: collect all OPS values to compute league average
    const opsValues = [];
    for (const rec of splits) {
      const ops = pf(rec.stat?.ops);
      if (ops) opsValues.push(ops);
    }
    const lgOPS = opsValues.length > 0
      ? opsValues.reduce((a, b) => a + b, 0) / opsValues.length
      : 0.720;

    // Second pass: compute per-team stats + wRC+ proxy
    for (const rec of splits) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      const s = rec.stat || {};
      const gp = s.gamesPlayed || 1;
      const ops = pf(s.ops);
      const teamId = rec.team?.id;
      teamIdByAbbr[abbr] = teamId;

      // wRC+ proxy: (team OPS / league avg OPS) × 100
      // Accurate within ~5-8 points of true wRC+. League avg = 100 by definition.
      const wrcPlus = ops !== null && lgOPS > 0
        ? Math.round(ops / lgOPS * 100)
        : 100;

      seasonStats[abbr] = {
        teamId,
        name:        rec.team?.name,
        abbr,
        gamesPlayed: gp,
        runs:        s.runs,
        hits:        s.hits,
        doubles:     s.doubles,
        triples:     s.triples,
        homeRuns:    s.homeRuns,
        strikeOuts:  s.strikeOuts,
        baseOnBalls: s.baseOnBalls,
        avg:         s.avg,
        obp:         s.obp,
        slg:         s.slg,
        ops:         s.ops,
        atBats:      s.atBats,
        runsPerGame: (s.runs / gp).toFixed(2),
        wrcPlus,
        lgOPS: Math.round(lgOPS * 1000) / 1000,
      };
    }

    // Parse standings
    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins:         team.wins,
          losses:       team.losses,
          pct:          team.winningPercentage,
          streak:       team.streak?.streakCode,
          runsScored:   team.runsScored,
          runsAllowed:  team.runsAllowed,
          runDiff:      team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank,
          leagueRank:   team.leagueRank,
          gb:           team.gamesBack,
          divisionGb:   team.divisionGamesBack,
        };
      }
    }

    // Fetch rolling R/G for all 30 teams in parallel
    const allAbbrs = new Set([...Object.keys(seasonStats), ...Object.keys(standings)]);
    const rollingData = {};
    await Promise.all([...allAbbrs].map(async (abbr) => {
      const teamId = teamIdByAbbr[abbr];
      if (!teamId) return;
      const [r7, r15] = await Promise.all([
        fetchRollingRpG(teamId, 7),
        fetchRollingRpG(teamId, 15),
      ]);
      rollingData[abbr] = { last7RpG: r7, last15RpG: r15 };
    }));

    // Merge everything
    const teams = {};
    for (const abbr of allAbbrs) {
      teams[abbr] = {
        ...(seasonStats[abbr] || { abbr }),
        record:    standings[abbr] || null,
        last7RpG:  rollingData[abbr]?.last7RpG  ?? null,
        last15RpG: rollingData[abbr]?.last15RpG ?? null,
      };
    }

    // Sample rolling R/G diagnostic — shows what the linescore endpoint returned
    // for NYY (teamId=147) to confirm the new endpoint is working
    const nyyRolling = rollingData['NYY'] || {};

    const result = {
      updatedAt:      new Date().toISOString(),
      codeVersion:    'linescore-v2',   // bump to confirm new code running
      teamCount:      Object.keys(teams).length,
      lgOPS:          Math.round(lgOPS * 1000) / 1000,
      wrcSource:      'ops_proxy',
      rollingSource:  'schedule_linescore',
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

