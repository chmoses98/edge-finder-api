export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    const [seasonRes, standingsRes] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division,league')
    ]);

    const [seasonData, standingsData] = await Promise.all([
      seasonRes.json(),
      standingsRes.json()
    ]);

    // Parse season hitting stats — use actual field names from API
    const seasonStats = {};
    const splits = seasonData?.stats?.[0]?.splits || [];
    for (const rec of splits) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      const s = rec.stat || {};
      seasonStats[abbr] = {
        teamId: rec.team?.id,
        name: rec.team?.name,
        abbr,
        gamesPlayed: s.gamesPlayed,
        runs: s.runs,
        hits: s.hits,
        doubles: s.doubles,
        triples: s.triples,
        homeRuns: s.homeRuns,
        strikeOuts: s.strikeOuts,
        baseOnBalls: s.baseOnBalls,
        avg: s.avg,
        obp: s.obp,
        slg: s.slg,
        ops: s.ops,
        stolenBases: s.stolenBases,
        leftOnBase: s.leftOnBase,
        atBats: s.atBats,
        // runs per game
        runsPerGame: s.gamesPlayed ? (s.runs / s.gamesPlayed).toFixed(2) : null
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
          runDiff: team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank,
          leagueRank: team.leagueRank,
          gb: team.gamesBack,
          divisionGb: team.divisionGamesBack
        };
      }
    }

    // Merge season stats + standings
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
      teams
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
