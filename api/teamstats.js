export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    // MLB Stats API — team hitting stats with splits
    // sportId=1 = MLB, group=hitting, gameType=R = regular season
    const [seasonRes, last15Res, standingsRes] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season'),
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=lastXGames&limit=15'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division')
    ]);

    const [seasonData, last15Data, standingsData] = await Promise.all([
      seasonRes.json(),
      last15Res.json(),
      standingsRes.json()
    ]);

    // Parse season stats
    const seasonStats = {};
    for (const rec of (seasonData.stats?.[0]?.splits || [])) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      seasonStats[abbr] = {
        teamId: rec.team?.id,
        name: rec.team?.name,
        abbr,
        season: {
          avg: rec.stat?.avg,
          obp: rec.stat?.obp,
          slg: rec.stat?.slg,
          ops: rec.stat?.ops,
          runs: rec.stat?.runs,
          homeRuns: rec.stat?.homeRuns,
          strikeOuts: rec.stat?.strikeOuts,
          baseOnBalls: rec.stat?.baseOnBalls,
          hits: rec.stat?.hits,
          atBats: rec.stat?.atBats,
          gamesPlayed: rec.stat?.gamesPlayed
        }
      };
    }

    // Parse last 15 games stats
    const last15Stats = {};
    for (const rec of (last15Data.stats?.[0]?.splits || [])) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      last15Stats[abbr] = {
        avg: rec.stat?.avg,
        obp: rec.stat?.obp,
        slg: rec.stat?.slg,
        ops: rec.stat?.ops,
        runs: rec.stat?.runs,
        homeRuns: rec.stat?.homeRuns,
        gamesPlayed: rec.stat?.gamesPlayed
      };
    }

    // Parse standings for W/L record and streak
    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins: team.wins,
          losses: team.losses,
          pct: team.winningPercentage,
          streak: team.streak?.streakCode,
          runsScored: team.runsScored,
          runsAllowed: team.runsAllowed,
          divisionRank: team.divisionRank,
          leagueRank: team.leagueRank
        };
      }
    }

    // Merge everything
    const merged = {};
    for (const abbr of Object.keys(seasonStats)) {
      merged[abbr] = {
        ...seasonStats[abbr],
        last15: last15Stats[abbr] || null,
        record: standings[abbr] || null
      };
    }

    const result = { updatedAt: new Date().toISOString(), teams: merged };

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
