export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    // Correct MLB Stats API endpoints
    const [seasonRes, standingsRes] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division,league')
    ]);

    const [seasonData, standingsData] = await Promise.all([
      seasonRes.json(),
      standingsRes.json()
    ]);

    // Debug: log what we're getting back
    const seasonKeys = Object.keys(seasonData);
    const firstStat = seasonData?.stats?.[0];
    const firstSplit = firstStat?.splits?.[0];

    // Parse season stats — try multiple possible structures
    const seasonStats = {};

    // Structure 1: stats[0].splits[]
    const splits = seasonData?.stats?.[0]?.splits || [];
    for (const rec of splits) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      seasonStats[abbr] = {
        teamId: rec.team?.id,
        name: rec.team?.name,
        abbr,
        avg: rec.stat?.avg,
        obp: rec.stat?.obp,
        slg: rec.stat?.slg,
        ops: rec.stat?.ops,
        runs: rec.stat?.runs,
        homeRuns: rec.stat?.homeRuns,
        strikeOuts: rec.stat?.strikeOuts,
        baseOnBalls: rec.stat?.baseOnBalls,
        hits: rec.stat?.hits,
        gamesPlayed: rec.stat?.gamesPlayed
      };
    }

    // Parse standings
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
          leagueRank: team.leagueRank,
          gb: team.gamesBack,
          divisionGb: team.divisionGamesBack
        };
      }
    }

    // Merge
    const teams = {};
    const allAbbrs = new Set([...Object.keys(seasonStats), ...Object.keys(standings)]);
    for (const abbr of allAbbrs) {
      teams[abbr] = {
        ...(seasonStats[abbr] || { abbr }),
        record: standings[abbr] || null
      };
    }

    const result = {
      updatedAt: new Date().toISOString(),
      teamCount: Object.keys(teams).length,
      splitsFound: splits.length,
      standingsFound: Object.keys(standings).length,
      // Include raw structure debug info
      debug: {
        seasonDataKeys: seasonKeys,
        firstSplitSample: firstSplit ? {
          teamAbbr: firstSplit.team?.abbreviation,
          statKeys: Object.keys(firstSplit.stat || {}).slice(0, 10)
        } : null
      },
      teams
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch(error) {
    const result = { error: error.message, stack: error.stack };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
