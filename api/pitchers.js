export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  // No-cache headers — date-sensitive response must never be served stale
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date } = req.query;

  // Default to today's date in ET
  const today = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  try {
    // MLB StatsAPI requires MM/DD/YYYY format; today is YYYY-MM-DD
    const [yr, mo, dy] = today.split('-');
    const mlbDate = `${mo}/${dy}/${yr}`;
    console.log(`[pitchers.js] Fetching MLB schedule: date=${today} mlbDate=${mlbDate}`);
    const url = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${mlbDate}&hydrate=probablePitcher(note),team,linescore,broadcasts`;
    const response = await fetch(url);
    if (!response.ok) {
      return res.status(response.status).json({ error: 'MLB API error' });
    }
    const data = await response.json();

    // Parse into clean format
    const games = [];
    for (const date of data.dates || []) {
      for (const game of date.games || []) {
        const away = game.teams?.away;
        const home = game.teams?.home;
        games.push({
          gameId: game.gamePk,
          status: game.status?.detailedState,
          startTime: game.gameDate,
          venue: game.venue?.name,
          away: {
            team: away?.team?.name,
            teamAbbr: away?.team?.abbreviation,
            record: `${away?.leagueRecord?.wins}-${away?.leagueRecord?.losses}`,
            pitcher: away?.probablePitcher ? {
              name: away.probablePitcher.fullName,
              id: away.probablePitcher.id,
              note: away.probablePitcher.note || '',
              // Starter throwing hand ('L'/'R'). NOTE: the schedule endpoint's
              // probablePitcher(note) hydrate does NOT actually populate pitchHand
              // on the person sub-object -- this is always null here regardless of
              // game state. scripts/fetch_lineups.py backfills this field in
              // data/slate.json from the boxscore endpoint (which does carry it)
              // once the starter is listed there -- see that script's
              // resolve_starter_pitch_hand(). Left null here (never guessed).
              pitchHand: away.probablePitcher.pitchHand?.code || null
            } : null
          },
          home: {
            team: home?.team?.name,
            teamAbbr: home?.team?.abbreviation,
            record: `${home?.leagueRecord?.wins}-${home?.leagueRecord?.losses}`,
            pitcher: home?.probablePitcher ? {
              name: home.probablePitcher.fullName,
              id: home.probablePitcher.id,
              note: home.probablePitcher.note || '',
              // See the `away` pitcher above: always null from this endpoint;
              // backfilled downstream by scripts/fetch_lineups.py from the boxscore.
              pitchHand: home.probablePitcher.pitchHand?.code || null
            } : null
          }
        });
      }
    }

    const { callback } = req.query;
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify({ games, date: today })})`);
    }
    return res.status(200).json({ games, date: today });

  } catch (error) {
    const { callback } = req.query;
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify({ error: error.message })})`);
    }
    return res.status(500).json({ error: error.message });
  }
}
