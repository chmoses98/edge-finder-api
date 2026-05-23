export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { ticker, callback } = req.query;

  try {
    // If specific ticker provided, fetch those markets directly
    // Otherwise fetch all open markets and find baseball-related ones
    const url = ticker
      ? `https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=${ticker}&status=open&limit=20`
      : `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000`;

    const response = await fetch(url);
    if (!response.ok) {
      const err = await response.text();
      const result = { error: `Kalshi API error: ${response.status}`, details: err };
      if (callback) {
        res.setHeader('Content-Type', 'application/javascript');
        return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
      }
      return res.status(200).json(result);
    }

    const data = await response.json();
    const markets = data.markets || [];

    // Filter to baseball/MLB markets only
    const baseballKeywords = ['mlb', 'baseball', 'run', 'inning', 'pitcher', 'strikeout',
      'cubs', 'yankees', 'dodgers', 'braves', 'astros', 'mets', 'cardinals',
      'pirates', 'phillies', 'brewers', 'padres', 'giants', 'mariners',
      'royals', 'guardians', 'tigers', 'orioles', 'rays', 'nationals',
      'marlins', 'twins', 'red sox', 'athletics', 'rangers', 'angels',
      'rockies', 'diamondbacks', 'reds', 'white sox', 'blue jays'];

    const baseball = markets.filter(m => {
      const text = `${m.title} ${m.event_ticker} ${m.ticker}`.toLowerCase();
      return baseballKeywords.some(k => text.includes(k));
    });

    // Extract unique series tickers
    const seriesTickers = [...new Set(baseball.map(m => {
      // Series ticker = everything before the first hyphen in event_ticker
      const et = m.event_ticker || '';
      return et.split('-')[0];
    }))].filter(Boolean);

    // Group by series ticker
    const bySeries = {};
    for (const m of baseball) {
      const et = m.event_ticker || '';
      const series = et.split('-')[0];
      if (!bySeries[series]) bySeries[series] = [];
      bySeries[series].push({
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        title: m.title,
        yesBid: m.yes_bid_dollars,
        yesAsk: m.yes_ask_dollars,
        volume: m.volume_fp,
        closeTime: m.close_time
      });
    }

    const result = {
      totalMarketsScanned: markets.length,
      baseballMarketsFound: baseball.length,
      seriesTickers,
      bySeries,
      // Also return raw first 5 baseball markets for inspection
      sample: baseball.slice(0, 10).map(m => ({
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        title: m.title,
        seriesTicker: (m.event_ticker || '').split('-')[0]
      }))
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    const result = { error: error.message };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
