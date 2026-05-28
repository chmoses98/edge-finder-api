export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  // wRC+ lookup table — FanGraphs 2026 season values
  // These are fetched from FanGraphs public leaderboard endpoint
  // Updated periodically; fall back to 100 (league average) if missing
  async function fetchWrcPlus() {
    try {
      // FanGraphs team batting leaderboard — type=8 includes wRC+
      const r = await fetch(
        'https://www.fangraphs.com/api/leaders/major-league/data?pos=all&stats=bat&lg=all&qual=0&type=8&season=2026&season1=2026&ind=0&team=0,ts&rost=0&age=0&filter=&players=0&startdate=&enddate=&page=1_100',
        { headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json' } }
      );
      if (!r.ok) return {};
      const d = await r.json();
      const rows = d?.data || [];
      const wrcMap = {};
      for (const row of rows) {
        // FanGraphs uses full team names; map to abbreviations
        const abbr = FANGRAPHS_ABBR_MAP[row.Team] || row.Team;
        if (abbr && row['wRC+'] != null) {
          wrcMap[abbr] = Math.round(parseFloat(row['wRC+']));
        }
      }
      return wrcMap;
    } catch(e) { return {}; }
  }

  // Rolling R/G from last N games using MLB Stats API game logs
  async function fetchRollingRpG(teamId, numGames) {
    try {
      const r = await fetch(
        `https://statsapi.mlb.com/api/v1/teams/${teamId}/stats?stats=gameLog&group=hitting&gameType=R&season=2026&limit=${numGames}`
      );
      if (!r.ok) return null;
      const d = await r.json();
      const logs = d?.stats?.[0]?.splits || [];
      if (!logs.length) return null;
      const recent = logs.slice(0, numGames);
      const totalRuns = recent.reduce((sum, l) => sum + (parseInt(l.stat?.runs) || 0), 0);
      return Math.round((totalRuns / recent.length) * 100) / 100;
    } catch(e) { return null; }
  }

  const FANGRAPHS_ABBR_MAP = {
    'Angels': 'LAA', 'Astros': 'HOU', 'Athletics': 'ATH', 'Blue Jays': 'TOR',
    'Braves': 'ATL', 'Brewers': 'MIL', 'Cardinals': 'STL', 'Cubs': 'CHC',
    'Diamondbacks': 'ARI', 'Dodgers': 'LAD', 'Giants': 'SF', 'Guardians': 'CLE',
    'Mariners': 'SEA', 'Marlins': 'MIA', 'Mets': 'NYM', 'Nationals': 'WSH',
    'Orioles': 'BAL', 'Padres': 'SD', 'Phillies': 'PHI', 'Pirates': 'PIT',
    'Rangers': 'TEX', 'Rays': 'TB', 'Red Sox': 'BOS', 'Reds': 'CIN',
    'Rockies': 'COL', 'Royals': 'KC', 'Tigers': 'DET', 'Twins': 'MIN',
    'White Sox': 'CWS', 'Yankees': 'NYY',
  };

  const MLB_TEAM_IDS = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
  };

  try {
    const [seasonRes, standingsRes, wrcData] = await Promise.all([
      fetch('https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc'),
      fetch('https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak,division,league'),
      fetchWrcPlus(),
    ]);

    const [seasonData, standingsData] = await Promise.all([
      seasonRes.json(),
      standingsRes.json(),
    ]);

    // Parse season hitting stats
    const seasonStats = {};
    const teamIdMap = {};
    const splits = seasonData?.stats?.[0]?.splits || [];
    for (const rec of splits) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      const s = rec.stat || {};
      const gp = s.gamesPlayed || 1;
      teamIdMap[abbr] = rec.team?.id;
      seasonStats[abbr] = {
        teamId:      rec.team?.id,
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
        // wRC+ from FanGraphs (100 = league average)
        wrcPlus: wrcData[abbr] ?? 100,
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

    // Fetch rolling R/G (last 7 and last 15 games) for all teams in parallel
    const allAbbrs = new Set([...Object.keys(seasonStats), ...Object.keys(standings)]);
    const rollingData = {};
    await Promise.all([...allAbbrs].map(async (abbr) => {
      const teamId = teamIdMap[abbr] || MLB_TEAM_IDS[abbr];
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
        record:     standings[abbr] || null,
        last7RpG:   rollingData[abbr]?.last7RpG ?? null,
        last15RpG:  rollingData[abbr]?.last15RpG ?? null,
      };
    }

    const result = {
      updatedAt:  new Date().toISOString(),
      teamCount:  Object.keys(teams).length,
      wrcSource:  Object.keys(wrcData).length > 0 ? 'fangraphs' : 'unavailable_using_100',
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
