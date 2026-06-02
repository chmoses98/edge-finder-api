// Temporary debug: list all Kalshi market keys for a game
export default async function handler(req, res) {
  const apiKey = process.env.ODDS_API_KEY;
  const eventId = req.query.eventId || '507ec1a59ca1a2aa1386a2dbe2fbfa7b';
  
  const url = `https://api.the-odds-api.com/v4/sports/baseball_mlb/events/${eventId}/markets`
    + `?apiKey=${apiKey}&regions=us_ex&bookmakers=kalshi`;
  
  const r = await fetch(url);
  const data = await r.json();
  return res.status(200).json(data);
}
