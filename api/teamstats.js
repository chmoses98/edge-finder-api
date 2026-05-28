export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  // Rolling R/G from MLB game log — last N games for a team
  async function fetchRollingRpG(teamId, numGames) {
    try {
      const r = await fetch(
        `https://statsapi.mlb.com/api/v1/teams/${teamId}/stats?stats=gameLog&group=hitting&gameType=R&season=2026&limit=${numGames}`
      );
      if (!r.ok) return null;
      const d = await r.json();
      const logs = (d?.stats?.[0]?.splits || []).slice(0, numGames);
      if (!logs.length) return null;
      const totalRuns = logs.reduce((sum, l) => sum + (parseInt(l.stat?.runs) || 0), 0);
      return Math.round((totalRuns / logs.length) * 100) / 100;
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

    const result = {
      updatedAt:  new Date().toISOString(),
      teamCount:  Object.keys(teams).length,
      lgOPS:      Math.round(lgOPS * 1000) / 1000,
      wrcSource:  'ops_proxy',
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
