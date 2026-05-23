export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    // Search for specific market titles we know exist
    const searches = [
      'First Inning Run',
      'wins by over 1.5 runs',
      'wins by over 2.5 runs',
      'First 5 Innings',
      'Total Runs',
      'strikeouts',
      'YRFI',
      'NRFI'
    ];

    const results = {};

    for (const term of searches) {
      const url = `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=5&search=${encodeURIComponent(term)}`;
      const r = await fetch(url);
      const d = await r.json();
      const markets = (d.markets || []).slice(0, 3).map(m => ({
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        seriesTicker: (m.event_ticker || '').split('-')[0],
        title: m.title,
        yesBid: m.yes_bid_dollars,
        yesAsk: m.yes_ask_dollars
      }));
      results[term] = markets;
    }

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(results)})`);
    }
    return res.status(200).json(results);

  } catch (error) {
    const result = { error: error.message };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
