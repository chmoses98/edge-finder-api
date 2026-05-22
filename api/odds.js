export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  res.setHeader('Access-Control-Allow-Credentials', 'false');
  res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'unsafe-none');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'API key not configured' });
  }

  const { sport, market, region } = req.query;
  if (!sport) {
    return res.status(400).json({ error: 'Sport parameter required' });
  }

  try {
    const url = `https://api.the-odds-api.com/v4/sports/${sport}/odds/?apiKey=${apiKey}&regions=${region || 'us'}&markets=${market || 'h2h'}&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`;
    const response = await fetch(url);
    const remaining = response.headers.get('x-requests-remaining');
    const used = response.headers.get('x-requests-used');
    if (!response.ok) {
      const error = await response.text();
      return res.status(response.status).json({ error, remaining, used });
    }
    const data = await response.json();
    return res.status(200).json({ data, remaining, used });
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
}
