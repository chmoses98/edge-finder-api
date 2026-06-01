export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  const { date } = req.query;
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ error: 'date param required (YYYY-MM-DD)' });
  }

  // ── Constants ──────────────────────────────────────────────────────────────

  const TEAM_ABBR = {
    'Arizona Diamondbacks': 'AZ',   'Atlanta Braves': 'ATL',
    'Baltimore Orioles': 'BAL',     'Boston Red Sox': 'BOS',
    'Chicago Cubs': 'CHC',          'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN',       'Cleveland Guardians': 'CLE',
    'Colorado Rockies': 'COL',      'Detroit Tigers': 'DET',
    'Houston Astros': 'HOU',        'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA',    'Los Angeles Dodgers': 'LAD',
    'Miami Marlins': 'MIA',         'Milwaukee Brewers': 'MIL',
    'Minnesota Twins': 'MIN',       'New York Mets': 'NYM',
    'New York Yankees': 'NYY',      'Oakland Athletics': 'OAK',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT',
    'San Diego Padres': 'SD',       'San Francisco Giants': 'SF',
    'Seattle Mariners': 'SEA',      'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB',         'Texas Rangers': 'TEX',
    'Toronto Blue Jays': 'TOR',     'Washington Nationals': 'WSH',
    'Sacramento River Cats': 'SAC',
  };

  const SUPPORTED_MARKETS = {
    'ML':         'h2h',
    'F5 ML':      'h2h_h1',
    'Total':      'totals',
    'Game Total': 'totals',
    'Run Line':   'spreads',
    'RL':         'spreads',
    'Team Total': 'team_totals',
    'TT':         'team_totals',
  };

  const UNSUPPORTED = new Set(['YRFI', 'NRFI', 'K Prop', 'Pitcher Prop', 'Batter Prop']);
  const SHARP_PRIORITY = ['lowvig', 'draftkings', 'fanduel', 'betmgm'];

  // ── Helpers ────────────────────────────────────────────────────────────────

  const toImp = (price) => {
    if (price == null) return null;
    return price >= 100 ? 100 / (price + 100) : Math.abs(price) / (Math.abs(price) + 100);
  };

  const vigFree = (pA, pB) => {
    const iA = toImp(pA), iB = toImp(pB);
    if (iA == null || iB == null) return [null, null];
    const tot = iA + iB;
    return [Math.round(iA / tot * 1000) / 10, Math.round(iB / tot * 1000) / 10];
  };

  const parseGame = (str) => {
    if (!str) return [null, null];
    const sep = str.includes(' @ ') ? ' @ ' : '@';
    const parts = str.split(sep);
    return parts.length === 2 ? [parts[0].trim().toUpperCase(), parts[1].trim().toUpperCase()] : [null, null];
  };

  const matchGame = (games, away, home) => {
    for (const g of games) {
      const gAway = TEAM_ABBR[g.away_team] || g.away_team?.toUpperCase();
      const gHome = TEAM_ABBR[g.home_team] || g.home_team?.toUpperCase();
      if (gAway === away && gHome === home) return g;
    }
    return null;
  };

  const getSharpMarket = (game, marketKey) => {
    for (const bkKey of SHARP_PRIORITY) {
      const bk = (game.bookmakers || []).find(b => b.key === bkKey);
      if (!bk) continue;
      const mkt = (bk.markets || []).find(m => m.key === marketKey);
      if (mkt) return { bkKey, mkt };
    }
    return null;
  };

  // ── Historical odds fetch ──────────────────────────────────────────────────

  const fetchHistorical = async (dateStr, markets) => {
    // Snapshot at 06:00 UTC next day (~1am ET) = all games finished, lines settled
    const nextDay = new Date(dateStr + 'T12:00:00Z');
    nextDay.setUTCDate(nextDay.getUTCDate() + 1);
    const snapshot = nextDay.toISOString().replace(/\.\d+Z/, 'Z').slice(0, 19) + 'Z';
    // Format: YYYY-MM-DDT06:00:00Z
    const snapDate = dateStr.split('T')[0];
    const nextDateStr = new Date(dateStr + 'T12:00:00Z');
    nextDateStr.setUTCDate(nextDateStr.getUTCDate() + 1);
    const nextISO = nextDateStr.toISOString().slice(0, 10);
    const snapshotStr = `${nextISO}T06:00:00Z`;

    // Game window: covers full day in ET (noon UTC to next day noon UTC)
    const commenceFrom = `${dateStr}T15:00:00Z`;  // noon ET
    const commenceTo   = `${nextISO}T06:00:00Z`;  // 1am ET next day

    const url = `https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds`
      + `?apiKey=${apiKey}`
      + `&regions=us`
      + `&markets=${markets}`
      + `&oddsFormat=american`
      + `&commenceTimeFrom=${commenceFrom}`
      + `&commenceTimeTo=${commenceTo}`
      + `&date=${snapshotStr}`;

    try {
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      const remaining = r.headers.get('x-requests-remaining');
      const raw = await r.json();
      // Historical endpoint wraps response in { data: [...], timestamp, ... }
      const games = Array.isArray(raw) ? raw : (raw.data || []);
      return { games, remaining, ok: r.ok, status: r.status };
    } catch (e) {
      return { games: [], remaining: null, ok: false, error: e.message };
    }
  };

  // ── Closing line extractors ────────────────────────────────────────────────

  const extractML = (game, awayAbbr, marketKey) => {
    const sharp = getSharpMarket(game, marketKey);
    if (!sharp) return null;
    const { bkKey, mkt } = sharp;
    const outcomes = mkt.outcomes || [];
    const awayOut = outcomes.find(o => (TEAM_ABBR[o.name] || o.name?.toUpperCase()) === awayAbbr);
    const homeOut = outcomes.find(o => (TEAM_ABBR[o.name] || o.name?.toUpperCase()) !== awayAbbr);
    if (!awayOut || !homeOut) return null;
    return { awayPrice: awayOut.price, homePrice: homeOut.price, book: bkKey };
  };

  const extractTotal = (game, betStr) => {
    const sharp = getSharpMarket(game, 'totals');
    if (!sharp) return null;
    const { bkKey, mkt } = sharp;
    const side = /over|( o )/i.test(betStr) ? 'over' : 'under';
    const numMatch = betStr.match(/(\d+\.?\d*)/);
    const betNumber = numMatch ? parseFloat(numMatch[1]) : null;
    const overOut  = (mkt.outcomes || []).find(o => o.name?.toLowerCase() === 'over');
    const underOut = (mkt.outcomes || []).find(o => o.name?.toLowerCase() === 'under');
    if (!overOut || !underOut) return null;
    const closingNumber = overOut.point;
    const betOut = side === 'over' ? overOut : underOut;
    const oppOut = side === 'over' ? underOut : overOut;
    return { betSide: side, betPrice: betOut.price, oppPrice: oppOut.price, closingNumber, betNumber, book: bkKey };
  };

  const extractRL = (game, betStr, awayAbbr) => {
    const sharp = getSharpMarket(game, 'spreads');
    if (!sharp) return null;
    const { bkKey, mkt } = sharp;
    const isAway  = awayAbbr && betStr.toUpperCase().includes(awayAbbr);
    const isMinus = betStr.includes('-1.5');
    const outcomes = mkt.outcomes || [];
    for (const o of outcomes) {
      const oAbbr   = TEAM_ABBR[o.name] || o.name?.toUpperCase();
      const oIsAway = oAbbr === awayAbbr;
      const oPoint  = o.point || 0;
      if (oIsAway === isAway && (oPoint < 0) === isMinus) {
        const opp = outcomes.find(x => x !== o);
        return { betPrice: o.price, oppPrice: opp?.price ?? null, point: oPoint, book: bkKey };
      }
    }
    return null;
  };

  // ── CLV calculation ────────────────────────────────────────────────────────

  const calcCLV = (bet, closing, market) => {
    if (!closing) return null;
    const betPrice = bet.price;
    if (betPrice == null) return null;
    const ourImp = toImp(betPrice) * 100;

    if (market === 'ML' || market === 'F5 ML') {
      const [vfAway, vfHome] = vigFree(closing.awayPrice, closing.homePrice);
      if (vfAway == null) return null;
      const [away] = parseGame(bet.game);
      const betText = (bet.bet || '').toUpperCase();
      const isAway  = away && (betText.startsWith(away) || betText.includes(away));
      const closeVF = isAway ? vfAway : vfHome;
      return Math.round((closeVF - ourImp) * 100) / 100;
    }

    if (['Total','Game Total','Run Line','RL'].includes(market)) {
      const [vfBet] = vigFree(closing.betPrice, closing.oppPrice);
      if (vfBet == null) return null;
      return Math.round((vfBet - ourImp) * 100) / 100;
    }

    return null;
  };

  const closingLineStr = (bet, closing, market) => {
    if (!closing) return null;
    const bk = closing.book || '';
    if (market === 'ML' || market === 'F5 ML') {
      const [away] = parseGame(bet.game);
      const betText = (bet.bet || '').toUpperCase();
      const isAway  = away && (betText.startsWith(away) || betText.includes(away));
      const price   = isAway ? closing.awayPrice : closing.homePrice;
      return `${price > 0 ? '+' : ''}${price} [${bk}]`;
    }
    if (market === 'Total' || market === 'Game Total') {
      const side = (closing.betSide || '').charAt(0).toUpperCase() + closing.betSide.slice(1);
      const num  = closing.closingNumber;
      const betNum = closing.betNumber;
      const price  = closing.betPrice;
      const numStr = (betNum != null && num != null && betNum !== num)
        ? `${betNum}→${num}` : `${num}`;
      return `${side} ${numStr} ${price > 0 ? '+' : ''}${price} [${bk}]`;
    }
    if (market === 'Run Line' || market === 'RL') {
      const pt = closing.point;
      const pr = closing.betPrice;
      return `${pt >= 0 ? '+' : ''}${pt} ${pr >= 0 ? '+' : ''}${pr} [${bk}]`;
    }
    return null;
  };

  // ── Main logic ─────────────────────────────────────────────────────────────

  try {
    // Read bets.json from GitHub
    const ghToken = process.env.GITHUB_TOKEN;
    const betsUrl = 'https://api.github.com/repos/chmoses98/edge-finder-api/contents/bets.json';
    const betsReq = await fetch(betsUrl, {
      headers: {
        Authorization: `token ${ghToken}`,
        Accept: 'application/vnd.github.v3+json',
      }
    });
    if (!betsReq.ok) return res.status(500).json({ error: 'Failed to fetch bets.json from GitHub' });
    const betsFile = await betsReq.json();
    const bets = JSON.parse(Buffer.from(betsFile.content, 'base64').toString());
    const betsSha = betsFile.sha;

    // Filter bets for this date that need CLV
    const targets = bets.filter(b =>
      b.date === date
      && b.clv == null
      && ['WIN','LOSS','PUSH'].includes(b.result)
      && (SUPPORTED_MARKETS[b.market] || UNSUPPORTED.has(b.market))
    );

    // Mark unsupported markets immediately
    for (const b of targets) {
      if (UNSUPPORTED.has(b.market)) {
        b.closingLineSource = 'market_unavailable';
      }
    }

    const supported = targets.filter(b => SUPPORTED_MARKETS[b.market]);
    const log = [`CLV update for ${date} — ${supported.length} bets to process`];

    if (supported.length > 0) {
      // Determine which market endpoints are needed
      const needed = new Set(supported.map(b => SUPPORTED_MARKETS[b.market]));
      const mainMkts = [...needed].filter(m => m !== 'h2h_h1' && m !== 'team_totals').join(',');
      const needF5 = needed.has('h2h_h1');
      const needTT = needed.has('team_totals');

      // Fetch all needed markets
      let oddsGames = [];
      let requestsRemaining = null;

      if (mainMkts) {
        const { games, remaining } = await fetchHistorical(date, mainMkts);
        oddsGames = games;
        requestsRemaining = remaining;
        log.push(`Main markets [${mainMkts}]: ${games.length} games`);
      }

      if (needF5) {
        const { games: f5Games } = await fetchHistorical(date, 'h2h_h1');
        log.push(`F5 [h2h_h1]: ${f5Games.length} games`);
        const f5Map = {};
        for (const g of f5Games) f5Map[g.id] = g.bookmakers || [];
        for (const g of oddsGames) {
          for (const bk of (g.bookmakers || [])) {
            const f5Bk = (f5Map[g.id] || []).find(b => b.key === bk.key);
            if (f5Bk) bk.markets = [...(bk.markets || []), ...(f5Bk.markets || [])];
          }
        }
        const existing = new Set(oddsGames.map(g => g.id));
        for (const g of f5Games) if (!existing.has(g.id)) oddsGames.push(g);
      }

      if (needTT) {
        const { games: ttGames } = await fetchHistorical(date, 'team_totals');
        log.push(`TT [team_totals]: ${ttGames.length} games`);
        const ttMap = {};
        for (const g of ttGames) ttMap[g.id] = g.bookmakers || [];
        for (const g of oddsGames) {
          for (const bk of (g.bookmakers || [])) {
            const ttBk = (ttMap[g.id] || []).find(b => b.key === bk.key);
            if (ttBk) bk.markets = [...(bk.markets || []), ...(ttBk.markets || [])];
          }
        }
        const existing = new Set(oddsGames.map(g => g.id));
        for (const g of ttGames) if (!existing.has(g.id)) oddsGames.push(g);
      }

      log.push(`Total games in odds pool: ${oddsGames.length}`);
      if (oddsGames.length > 0) {
        log.push(`Sample: ${TEAM_ABBR[oddsGames[0]?.away_team]}@${TEAM_ABBR[oddsGames[0]?.home_team]}`);
      }

      // Process each bet
      let updated = 0;
      for (const b of supported) {
        const [away, home] = parseGame(b.game);
        if (!away) { log.push(`  SKIP ${b.id}: could not parse game`); continue; }

        const game = matchGame(oddsGames, away, home);
        if (!game) {
          log.push(`  NO_MATCH ${b.id}: ${b.game} (${away}@${home})`);
          b.closingLineSource = 'no_game_match';
          continue;
        }

        const market  = b.market;
        let closing   = null;

        if (market === 'ML')                        closing = extractML(game, away, 'h2h');
        else if (market === 'F5 ML')                closing = extractML(game, away, 'h2h_h1');
        else if (market === 'Total' || market === 'Game Total') closing = extractTotal(game, b.bet || '');
        else if (market === 'Run Line' || market === 'RL')      closing = extractRL(game, b.bet || '', away);

        if (!closing) {
          log.push(`  NO_LINE ${b.id}: ${market}`);
          b.closingLineSource = 'line_not_found';
          continue;
        }

        const clv    = calcCLV(b, closing, market);
        const clStr  = closingLineStr(b, closing, market);

        b.closingLine          = clStr;
        b.closingLineSource    = closing.book;
        b.closingLineTimestamp = `${date}T06:00:00Z`;
        b.clv                  = clv;

        log.push(`  ${clv >= 0 ? '✓' : '✗'} ${b.id} | ${market} | CL: ${clStr} | CLV: ${clv != null ? (clv >= 0 ? '+' : '') + clv + '%' : 'N/A'}`);
        updated++;
      }

      log.push(`\nUpdated: ${updated}/${supported.length} | requestsRemaining: ${requestsRemaining}`);
    }

    return res.status(200).json({
      date,
      bets,       // full updated bets array for the Action to commit
      log,
      betsSha,    // needed for GitHub commit
    });

  } catch (e) {
    return res.status(500).json({ error: e.message, stack: e.stack });
  }
}
