export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  // Accept date + bets from POST body (Action reads bets.json locally and sends targets)
  let date, bets;
  if (req.method === 'POST') {
    ({ date, bets } = req.body || {});
  } else {
    date = req.query.date;
    bets = null; // GET not supported for CLV processing
  }

  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ error: 'date required (YYYY-MM-DD)' });
  }
  if (!bets || !Array.isArray(bets)) {
    return res.status(400).json({ error: 'bets array required in POST body' });
  }

  // ── Constants ──────────────────────────────────────────────────────────────

  const TEAM_ABBR = {
    'Arizona Diamondbacks':'AZ','Atlanta Braves':'ATL','Baltimore Orioles':'BAL',
    'Boston Red Sox':'BOS','Chicago Cubs':'CHC','Chicago White Sox':'CWS',
    'Cincinnati Reds':'CIN','Cleveland Guardians':'CLE','Colorado Rockies':'COL',
    'Detroit Tigers':'DET','Houston Astros':'HOU','Kansas City Royals':'KC',
    'Los Angeles Angels':'LAA','Los Angeles Dodgers':'LAD','Miami Marlins':'MIA',
    'Milwaukee Brewers':'MIL','Minnesota Twins':'MIN','New York Mets':'NYM',
    'New York Yankees':'NYY','Oakland Athletics':'OAK','Philadelphia Phillies':'PHI',
    'Pittsburgh Pirates':'PIT','San Diego Padres':'SD','San Francisco Giants':'SF',
    'Seattle Mariners':'SEA','St. Louis Cardinals':'STL','Tampa Bay Rays':'TB',
    'Texas Rangers':'TEX','Toronto Blue Jays':'TOR','Washington Nationals':'WSH',
  };

  const SHARP_PRIORITY = ['lowvig','draftkings','fanduel','betmgm'];

  // ── Helpers ────────────────────────────────────────────────────────────────

  const toImp = p => p == null ? null : p >= 100 ? 100/(p+100) : Math.abs(p)/(Math.abs(p)+100);

  const vigFree = (pA, pB) => {
    const iA = toImp(pA), iB = toImp(pB);
    if (iA == null || iB == null) return [null, null];
    const t = iA + iB;
    return [Math.round(iA/t*1000)/10, Math.round(iB/t*1000)/10];
  };

  const parseGame = str => {
    if (!str) return [null, null];
    const sep = str.includes(' @ ') ? ' @ ' : '@';
    const p = str.split(sep);
    return p.length === 2 ? [p[0].trim().toUpperCase(), p[1].trim().toUpperCase()] : [null, null];
  };

  const abbr = name => TEAM_ABBR[name] || name?.toUpperCase();

  const matchGame = (games, away, home) =>
    games.find(g => abbr(g.away_team) === away && abbr(g.home_team) === home) || null;

  const getSharpMkt = (game, key) => {
    for (const bkKey of SHARP_PRIORITY) {
      const bk = (game.bookmakers||[]).find(b => b.key === bkKey);
      if (!bk) continue;
      const mkt = (bk.markets||[]).find(m => m.key === key);
      if (mkt) return { bkKey, mkt };
    }
    return null;
  };

  // ── Historical odds fetch ──────────────────────────────────────────────────

  const fetchHistorical = async (dateStr, markets) => {
    const d = new Date(dateStr + 'T12:00:00Z');
    d.setUTCDate(d.getUTCDate() + 1);
    const nextISO  = d.toISOString().slice(0, 10);
    const snapshot = `${nextISO}T06:00:00Z`;
    const commenceFrom = `${dateStr}T15:00:00Z`;
    const commenceTo   = snapshot;

    const url = `https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds`
      + `?apiKey=${apiKey}&regions=us&markets=${markets}&oddsFormat=american`
      + `&commenceTimeFrom=${commenceFrom}&commenceTimeTo=${commenceTo}&date=${snapshot}`;

    try {
      const r   = await fetch(url, { headers: { Accept: 'application/json' } });
      const raw = await r.json();
      const games = Array.isArray(raw) ? raw : (raw.data || []);
      return { games, remaining: r.headers.get('x-requests-remaining'), ok: r.ok };
    } catch (e) {
      return { games: [], ok: false, error: e.message };
    }
  };

  // ── Closing line extractors ────────────────────────────────────────────────

  const extractML = (game, awayAbbr, mktKey) => {
    const s = getSharpMkt(game, mktKey);
    if (!s) return null;
    const outs = s.mkt.outcomes || [];
    const awayOut = outs.find(o => abbr(o.name) === awayAbbr);
    const homeOut = outs.find(o => abbr(o.name) !== awayAbbr);
    if (!awayOut || !homeOut) return null;
    return { awayPrice: awayOut.price, homePrice: homeOut.price, book: s.bkKey };
  };

  const extractTotal = (game, betStr) => {
    const s = getSharpMkt(game, 'totals');
    if (!s) return null;
    const side  = /over|( o )/i.test(betStr) ? 'over' : 'under';
    const nm    = betStr.match(/(\d+\.?\d*)/);
    const betNum = nm ? parseFloat(nm[1]) : null;
    const outs  = s.mkt.outcomes || [];
    const ov    = outs.find(o => o.name?.toLowerCase() === 'over');
    const un    = outs.find(o => o.name?.toLowerCase() === 'under');
    if (!ov || !un) return null;
    const bet = side === 'over' ? ov : un;
    const opp = side === 'over' ? un : ov;
    return { betSide: side, betPrice: bet.price, oppPrice: opp.price, closingNumber: ov.point, betNumber: betNum, book: s.bkKey };
  };

  const extractRL = (game, betStr, awayAbbr) => {
    const s = getSharpMkt(game, 'spreads');
    if (!s) return null;
    const isAway  = awayAbbr && betStr.toUpperCase().includes(awayAbbr);
    const isMinus = betStr.includes('-1.5');
    for (const o of (s.mkt.outcomes || [])) {
      const oAbbr   = abbr(o.name);
      const oIsAway = oAbbr === awayAbbr;
      const oPoint  = o.point || 0;
      if (oIsAway === isAway && (oPoint < 0) === isMinus) {
        const opp = (s.mkt.outcomes||[]).find(x => x !== o);
        return { betPrice: o.price, oppPrice: opp?.price ?? null, point: oPoint, book: s.bkKey };
      }
    }
    return null;
  };

  // ── CLV calculation ────────────────────────────────────────────────────────

  const calcCLV = (bet, closing, market) => {
    if (!closing) return null;
    const ourImp = toImp(bet.price) * 100;
    if (ourImp == null) return null;

    if (market === 'ML' || market === 'F5 ML') {
      const [vfA, vfH] = vigFree(closing.awayPrice, closing.homePrice);
      if (vfA == null) return null;
      const [away] = parseGame(bet.game);
      const txt = (bet.bet || '').toUpperCase();
      const isAway = away && (txt.startsWith(away) || txt.includes(away));
      return Math.round(((isAway ? vfA : vfH) - ourImp) * 100) / 100;
    }
    if (['Total','Game Total','Run Line','RL'].includes(market)) {
      const [vf] = vigFree(closing.betPrice, closing.oppPrice);
      if (vf == null) return null;
      return Math.round((vf - ourImp) * 100) / 100;
    }
    return null;
  };

  const clStr = (bet, closing, market) => {
    if (!closing) return null;
    const bk = closing.book || '';
    const fmt = p => `${p >= 0 ? '+' : ''}${p}`;
    if (market === 'ML' || market === 'F5 ML') {
      const [away] = parseGame(bet.game);
      const txt = (bet.bet||'').toUpperCase();
      const isAway = away && (txt.startsWith(away) || txt.includes(away));
      return `${fmt(isAway ? closing.awayPrice : closing.homePrice)} [${bk}]`;
    }
    if (market === 'Total' || market === 'Game Total') {
      const side = closing.betSide.charAt(0).toUpperCase() + closing.betSide.slice(1);
      const numStr = (closing.betNumber != null && closing.closingNumber != null && closing.betNumber !== closing.closingNumber)
        ? `${closing.betNumber}→${closing.closingNumber}` : `${closing.closingNumber}`;
      return `${side} ${numStr} ${fmt(closing.betPrice)} [${bk}]`;
    }
    if (market === 'Run Line' || market === 'RL') {
      return `${fmt(closing.point)} ${fmt(closing.betPrice)} [${bk}]`;
    }
    return null;
  };

  // ── Main ───────────────────────────────────────────────────────────────────

  try {
    const log = [`CLV for ${date} — ${bets.length} bets`];

    // Build needed markets
    const MKTMAP = { 'ML':'h2h','F5 ML':'h2h_h1','Total':'totals','Game Total':'totals','Run Line':'spreads','RL':'spreads','Team Total':'team_totals','TT':'team_totals' };
    const needed = new Set(bets.map(b => MKTMAP[b.market]).filter(Boolean));
    const mainMkts = [...needed].filter(m => m !== 'h2h_h1' && m !== 'team_totals').join(',');
    const needF5 = needed.has('h2h_h1');
    const needTT = needed.has('team_totals');

    let oddsGames = [];
    let remaining = null;

    if (mainMkts) {
      const { games, remaining: rem } = await fetchHistorical(date, mainMkts);
      oddsGames = games; remaining = rem;
      log.push(`Main [${mainMkts}]: ${games.length} games | remaining: ${rem}`);
    }

    const mergeGames = (base, extra, mktKey) => {
      const map = {};
      for (const g of extra) map[g.id] = g.bookmakers || [];
      for (const g of base) {
        for (const bk of (g.bookmakers||[])) {
          const xBk = (map[g.id]||[]).find(b => b.key === bk.key);
          if (xBk) bk.markets = [...(bk.markets||[]), ...(xBk.markets||[])];
        }
      }
      const existing = new Set(base.map(g => g.id));
      for (const g of extra) if (!existing.has(g.id)) base.push(g);
    };

    if (needF5) {
      const { games: f5 } = await fetchHistorical(date, 'h2h_h1');
      mergeGames(oddsGames, f5, 'h2h_h1');
      log.push(`F5: ${f5.length} games`);
    }
    if (needTT) {
      const { games: tt } = await fetchHistorical(date, 'team_totals');
      mergeGames(oddsGames, tt, 'team_totals');
      log.push(`TT: ${tt.length} games`);
    }

    log.push(`Odds pool: ${oddsGames.length} games`);
    if (oddsGames.length > 0) {
      log.push(`Sample games: ${oddsGames.slice(0,3).map(g=>`${abbr(g.away_team)}@${abbr(g.home_team)}`).join(', ')}`);
    }

    const updatedBets = [];
    let updated = 0;

    for (const b of bets) {
      const [away, home] = parseGame(b.game);
      if (!away) { log.push(`  SKIP ${b.id}: parse fail`); updatedBets.push(b); continue; }

      const game = matchGame(oddsGames, away, home);
      if (!game) {
        log.push(`  NO_MATCH ${b.id}: ${b.game}`);
        b.closingLineSource = 'no_game_match';
        updatedBets.push(b); continue;
      }

      const mkt = b.market;
      let closing = null;
      if (mkt === 'ML')                         closing = extractML(game, away, 'h2h');
      else if (mkt === 'F5 ML')                 closing = extractML(game, away, 'h2h_h1');
      else if (mkt === 'Total'||mkt==='Game Total') closing = extractTotal(game, b.bet||'');
      else if (mkt === 'Run Line'||mkt==='RL')  closing = extractRL(game, b.bet||'', away);

      if (!closing) {
        log.push(`  NO_LINE ${b.id}: ${mkt}`);
        b.closingLineSource = 'line_not_found';
        updatedBets.push(b); continue;
      }

      const clv = calcCLV(b, closing, mkt);
      b.closingLine          = clStr(b, closing, mkt);
      b.closingLineSource    = closing.book;
      b.closingLineTimestamp = `${date}T06:00:00Z`;
      b.clv                  = clv;

      log.push(`  ${clv >= 0 ? '✓' : '✗'} ${b.id} | ${mkt} | CL: ${b.closingLine} | CLV: ${clv != null ? (clv>=0?'+':'')+clv+'%' : 'N/A'}`);
      updatedBets.push(b);
      updated++;
    }

    log.push(`\nDone: ${updated}/${bets.length} updated`);
    return res.status(200).json({ date, updatedBets, log, requestsRemaining: remaining });

  } catch (e) {
    return res.status(500).json({ error: e.message, stack: e.stack?.slice(0, 500) });
  }
}
