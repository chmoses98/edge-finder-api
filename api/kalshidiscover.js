export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    const response = await fetch(
      `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000`
    );
    const data = await response.json();
    const markets = data.markets || [];

    // Only keep markets with baseball-specific keywords in title
    const baseballOnly = markets.filter(m => {
      const t = (m.title || '').toLowerCase();
      return (
        t.includes('inning') ||
        t.includes('run scored') ||
        t.includes('runs scored') ||
        t.includes('winner?') && (
          t.includes('st. louis') || t.includes('cincinnati') ||
          t.includes('tampa bay') || t.includes('new york y') ||
          t.includes('houston') || t.includes('chicago c') ||
          t.includes('pittsburgh') || t.includes('toronto') ||
          t.includes('detroit') || t.includes('baltimore') ||
          t.includes('cleveland') || t.includes('philadelphia') ||
          t.includes('chicago ws') || t.includes('san francisco') ||
          t.includes('seattle') || t.includes('kansas city') ||
          t.includes('minnesota') || t.includes('boston') ||
          t.includes('washington') || t.includes('atlanta') ||
          t.includes('new york m') || t.includes('miami') ||
          t.includes('los angeles d') || t.includes('milwaukee') ||
          t.includes('athletics') || t.includes('san diego') ||
          t.includes('texas') || t.includes('los angeles a') ||
          t.includes('colorado') || t.includes('arizona')
        ) ||
        t.includes('yrfi') ||
        t.includes('nrfi') ||
        t.includes('first inning') ||
        t.includes('wins by over') && t.includes('run') ||
        t.includes('strikeout') ||
        t.includes('skenes') || t.includes('kirby') ||
        t.includes('sasaki') || t.includes('wheeler') ||
        t.includes('gallen') || t.includes('corbin') ||
        t.includes('peralta') || t.includes('paddack') ||
        t.includes('leahy') || t.includes('petty') ||
        t.includes('pallante') || t.includes('rea') ||
        t.includes('valdez') || t.includes('kolek') ||
        t.includes('ginn') || t.includes('giolito') ||
        t.includes('eovaldi') || t.includes('irvin') ||
        t.includes('holmes') || t.includes('meyer') ||
        t.includes('cecconi') || t.includes('gasser') ||
        t.includes('lorenzen')
      );
    });

    // Extract unique series tickers
    const seriesMap = {};
    for (const m of baseballOnly) {
      const series = (m.event_ticker || '').split('-')[0];
      if (!series) continue;
      if (!seriesMap[series]) {
        seriesMap[series] = {
          series,
          count: 0,
          examples: []
        };
      }
      seriesMap[series].count++;
      if (seriesMap[series].examples.length < 3) {
        seriesMap[series].examples.push({
          ticker: m.ticker,
          title: m.title,
          yesBid: m.yes_bid_dollars,
          yesAsk: m.yes_ask_dollars
        });
      }
    }

    const result = {
      totalScanned: markets.length,
      baseballFound: baseballOnly.length,
      seriesTickers: Object.keys(seriesMap),
      detail: seriesMap
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
