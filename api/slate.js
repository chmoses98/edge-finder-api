export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'API key not configured' });

  const { date, callback } = req.query;

  const today = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  try {
    // Fetch pitchers and odds in parallel
    const [pitchersRes, oddsRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`)
    ]);

    // Parse pitchers
    const pitcherData = await pitchersRes.json();
    const games = [];
    for (const d of pitcherData.dates || []) {
      for (const game of d.games || []) {
        const away = game.teams?.away;
        const home = game.teams?.home;
        games.push({
          gameId: game.gamePk,
          status: game.status?.detailedState,
          startTime: game.gameDate,
          venue: game.venue?.name,
          away: {
            team: away?.team?.name,
            abbr: away?.team?.abbreviation,
            record: `${away?.leagueRecord?.wins}-${away?.leagueRecord?.losses}`,
            pitcher: away?.probablePitcher ? {
              name: away.probablePitcher.fullName,
              id: away.probablePitcher.id,
              note: away.probablePitcher.note || ''
            } : null
          },
          home: {
            team: home?.team?.name,
            abbr: home?.team?.abbreviation,
            record: `${home?.leagueRecord?.wins}-${home?.leagueRecord?.losses}`,
            pitcher: home?.probablePitcher ? {
              name: home.probablePitcher.fullName,
              id: home.probablePitcher.id,
              note: home.probablePitcher.note || ''
            } : null
          }
        });
      }
    }

    // Parse odds
    const oddsData = await oddsRes.json();
    const remaining = oddsRes.headers.get('x-requests-remaining');

    // Match odds to games by team name
    const enriched = games.map(g => {
      const match = Array.isArray(oddsData) ? oddsData.find(o =>
        o.home_team === g.home.team || o.away_team === g.away.team ||
        o.home_team.includes(g.home.abbr) || o.away_team.includes(g.away.abbr)
      ) : null;

      if (!match) return { ...g, odds: null };

      const pinnacle = match.bookmakers?.find(b => b.key === 'pinnacle');
      const dk = match.bookmakers?.find(b => b.key === 'draftkings');
      const fd = match.bookmakers?.find(b => b.key === 'fanduel');
      const mgm = match.bookmakers?.find(b => b.key === 'betmgm');

      const extractH2H = (bk) => {
        if (!bk) return null;
        const h2h = bk.markets?.find(m => m.key === 'h2h');
        if (!h2h) return null;
        const home = h2h.outcomes?.find(o => o.name === g.home.team);
        const away = h2h.outcomes?.find(o => o.name === g.away.team);
        return { home: home?.price, away: away?.price, updated: h2h.last_update };
      };

      const extractTotal = (bk) => {
        if (!bk) return null;
        const tot = bk.markets?.find(m => m.key === 'totals');
        if (!tot) return null;
        const over = tot.outcomes?.find(o => o.name === 'Over');
        const under = tot.outcomes?.find(o => o.name === 'Under');
        return { point: over?.point, over: over?.price, under: under?.price };
      };

      return {
        ...g,
        odds: {
          pinnacle: { h2h: extractH2H(pinnacle), total: extractTotal(pinnacle) },
          draftkings: { h2h: extractH2H(dk), total: extractTotal(dk) },
          fanduel: { h2h: extractH2H(fd), total: extractTotal(fd) },
          betmgm: { h2h: extractH2H(mgm), total: extractTotal(mgm) }
        }
      };
    });

    const result = { date: today, games: enriched, requestsRemaining: remaining };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    if (req.query.callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${req.query.callback}(${JSON.stringify({ error: error.message })})`);
    }
    return res.status(500).json({ error: error.message });
  }
}
