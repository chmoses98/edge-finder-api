export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date, callback } = req.query;

  // Default to today ET
  const todayET = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  // Format date for Kalshi ticker: 2026-05-23 -> 26MAY23
  const d = new Date(todayET + 'T12:00:00Z');
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kalshiDate = String(d.getUTCFullYear()).slice(2) +
    months[d.getUTCMonth()] +
    String(d.getUTCDate()).padStart(2, '0');

  try {
    // Correct series ticker is KXMLBGAME
    // Event tickers look like: KXMLBGAME-26MAY221840STLCIN
    const url = `https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`;

    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json' }
    });

    if (!response.ok) {
      const err = await response.text();
      const result = { error: `Kalshi API error: ${response.status}`, details: err, date: todayET };
      if (callback) {
        res.setHeader('Content-Type', 'application/javascript');
        return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
      }
      return res.status(200).json(result);
    }

    const data = await response.json();
    const allMarkets = data.markets || [];

    // Filter to today's games only using kalshiDate prefix
    const todayMarkets = allMarkets.filter(m =>
      m.event_ticker && m.event_ticker.includes(kalshiDate)
    );

    // Parse into clean format
    // Kalshi prices: yes_bid_dollars / yes_ask_dollars are in dollar format (0.00-1.00)
    // yes_bid = what buyer will pay, yes_ask = what seller wants
    // Mid price = (bid + ask) / 2 = implied probability
    const markets = todayMarkets.map(m => {
      const yesBidD = parseFloat(m.yes_bid_dollars) || 0;
      const yesAskD = parseFloat(m.yes_ask_dollars) || 0;
      const lastD = parseFloat(m.last_price_dollars) || 0;
      const mid = (yesBidD + yesAskD) / 2;
      const impliedPct = mid * 100; // percentage

      // Convert to American odds
      const p = mid;
      let americanOdds = 0;
      if (p > 0 && p < 1) {
        americanOdds = p >= 0.5
          ? Math.round(-(p / (1 - p)) * 100)
          : Math.round(((1 - p) / p) * 100);
      }

      // Parse team abbreviations from event ticker
      // Format: KXMLBGAME-26MAY231840STLCIN
      // After the date+time block: last 6 chars are away(3)+home(3)
      const et = m.event_ticker || '';
      const afterDate = et.replace(`KXMLBGAME-${kalshiDate}`, '');
      // afterDate looks like "1840STLCIN" - time(4) + teams(6)
      const timeStr = afterDate.slice(0, 4);
      const teamsStr = afterDate.slice(4);
      const knownTwoLetter = ['TB','AZ','SF','SD','KC','NY'];
      let awayAbbr, homeAbbr;
      if (knownTwoLetter.some(t => teamsStr.startsWith(t))) {
        awayAbbr = teamsStr.slice(0, 2);
        homeAbbr = teamsStr.slice(2);
      } else if (knownTwoLetter.some(t => teamsStr.slice(3).startsWith(t))) {
        awayAbbr = teamsStr.slice(0, 3);
        homeAbbr = teamsStr.slice(3);
      } else {
        awayAbbr = teamsStr.slice(0, 3);
        homeAbbr = teamsStr.slice(3, 6);
}

      // Format game time
      const hr = parseInt(timeStr.slice(0, 2));
      const mn = timeStr.slice(2, 4);
      const ampm = hr >= 12 ? 'PM' : 'AM';
      const hr12 = hr > 12 ? hr - 12 : hr === 0 ? 12 : hr;
      const gameTime = `${hr12}:${mn} ${ampm} ET`;

      return {
        ticker: m.ticker,
        eventTicker: et,
        title: m.title || '',
        awayTeam: awayAbbr,
        homeTeam: homeAbbr,
        gameTime,
        timeStr,
        yesBid: Math.round(yesBidD * 100),   // cents
        yesAsk: Math.round(yesAskD * 100),   // cents
        lastPrice: Math.round(lastD * 100),   // cents
        mid: Math.round(mid * 100),           // cents
        impliedPct: Math.round(impliedPct * 10) / 10, // e.g. 64.5
        americanOdds,
        volume: parseFloat(m.volume_fp) || 0,
        openInterest: parseFloat(m.open_interest_fp) || 0,
        closeTime: m.close_time || null,
        status: m.status,
        yesBidDollars: m.yes_bid_dollars,
        yesAskDollars: m.yes_ask_dollars,
        lastPriceDollars: m.last_price_dollars
      };
    });

    // Also include any markets that don't match today but are open
    // (in case game was listed with different date format)
    const allParsed = allMarkets.map(m => {
      const yesBidD = parseFloat(m.yes_bid_dollars) || 0;
      const yesAskD = parseFloat(m.yes_ask_dollars) || 0;
      const mid = (yesBidD + yesAskD) / 2;
      return {
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        title: m.title,
        mid: Math.round(mid * 100),
        impliedPct: Math.round(mid * 1000) / 10,
        yesBidDollars: m.yes_bid_dollars,
        yesAskDollars: m.yes_ask_dollars,
        lastPriceDollars: m.last_price_dollars,
        closeTime: m.close_time,
        volume: parseFloat(m.volume_fp) || 0
      };
    });

    const result = {
      date: todayET,
      kalshiDate,
      totalMarketsOpen: allMarkets.length,
      todayGames: markets.length,
      games: markets,
      allOpenMarkets: allParsed // full list for debugging
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    const result = { error: error.message, date: todayET };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
