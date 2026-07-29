// /api/odds.js — v3.1
// The Odds API for Pinnacle/FD/DK/BetMGM on all markets.
// Kalshi native API for ML/RL/Total (via Odds API) + F5/TT/NRFI (direct from Kalshi).

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  const BASE  = 'https://api.the-odds-api.com/v4';
  const SPORT = 'baseball_mlb';
  const BOOKS = 'pinnacle,fanduel,draftkings,betmgm,kalshi';

  // ── helpers ────────────────────────────────────────────────────────────────

  function vigFree(awayOdds, homeOdds) {
    if (awayOdds == null || homeOdds == null) return { away: null, home: null };
    const toImp = o => o > 0 ? 100 / (o + 100) : Math.abs(o) / (Math.abs(o) + 100);
    const iA = toImp(awayOdds), iH = toImp(homeOdds);
    const tot = iA + iH;
    return {
      away: Math.round(iA / tot * 10000) / 100,
      home: Math.round(iH / tot * 10000) / 100,
    };
  }

  function centToAmerican(mid) {
    // mid = 0–100 (cents/percent)
    if (mid <= 0 || mid >= 100) return null;
    const p = mid / 100;
    return p >= 0.5
      ? Math.round(-(p / (1 - p)) * 100)
      : Math.round(((1 - p) / p) * 100);
  }

  function price(outcomes, name) {
    const o = outcomes?.find(x => x.name?.toLowerCase() === name?.toLowerCase());
    return o?.price ?? null;
  }

  function pt(outcomes, name) {
    const o = outcomes?.find(x => x.name?.toLowerCase() === name?.toLowerCase());
    return o?.point ?? null;
  }

  function parseBook(book, awayTeam, homeTeam) {
    const get = key => book.markets?.find(m => m.key === key);
    const result = {};

    const h2h = get('h2h');
    if (h2h) result.ml = {
      away: price(h2h.outcomes, awayTeam),
      home: price(h2h.outcomes, homeTeam),
      updated: h2h.last_update,
    };

    const spreads = get('spreads');
    if (spreads) result.rl = {
      away: price(spreads.outcomes, awayTeam), awayPoint: pt(spreads.outcomes, awayTeam),
      home: price(spreads.outcomes, homeTeam), homePoint: pt(spreads.outcomes, homeTeam),
    };

    const totals = get('totals');
    if (totals) {
      const over = totals.outcomes?.find(o => o.name === 'Over');
      result.total = {
        line: over?.point ?? null,
        over: over?.price ?? null,
        under: totals.outcomes?.find(o => o.name === 'Under')?.price ?? null,
      };
    }

    const tt = get('team_totals');
    if (tt) {
      const away = { line: null, over: null, under: null };
      const home = { line: null, over: null, under: null };
      for (const o of tt.outcomes || []) {
        const side = o.description?.toLowerCase() === awayTeam.toLowerCase() ? away
                   : o.description?.toLowerCase() === homeTeam.toLowerCase() ? home : null;
        if (!side) continue;
        if (o.name === 'Over')  { side.over = o.price; side.line = o.point; }
        if (o.name === 'Under') { side.under = o.price; }
      }
      result.teamTotals = { away, home };
    }

    const f5h2h = get('h2h_1st_5_innings');
    if (f5h2h) result.f5ml = {
      away: price(f5h2h.outcomes, awayTeam),
      home: price(f5h2h.outcomes, homeTeam),
      updated: f5h2h.last_update,
    };

    const f5rl = get('spreads_1st_5_innings');
    if (f5rl) result.f5rl = {
      away: price(f5rl.outcomes, awayTeam), awayPoint: pt(f5rl.outcomes, awayTeam),
      home: price(f5rl.outcomes, homeTeam), homePoint: pt(f5rl.outcomes, homeTeam),
    };

    const nrfi = get('h2h_1st_1_innings');
    if (nrfi) result.nrfi = {
      yrfi: price(nrfi.outcomes, 'Yes') ?? price(nrfi.outcomes, awayTeam),
      nrfi: price(nrfi.outcomes, 'No'),
      updated: nrfi.last_update,
    };

    return result;
  }

  // ── Kalshi native API ──────────────────────────────────────────────────────
  // The Odds API doesn't carry Kalshi F5, TT, or NRFI markets.
  // We call the existing /api/kalshisearch endpoint which already handles
  // Kalshi's native API correctly, then supplement with a broader market scan.

  function kalshiDateStr() {
    const d = new Date();
    const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    return String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');
  }

  function parseKalshiTeams(teamsStr) {
    const twoLetter = ['TB','AZ','SF','SD','KC','NY','LA'];
    for (const t of twoLetter) {
      if (teamsStr.startsWith(t)) return { away: t, home: teamsStr.slice(t.length) };
    }
    const away3 = teamsStr.slice(0, 3);
    const rest = teamsStr.slice(3);
    for (const t of twoLetter) {
      if (rest.startsWith(t)) return { away: away3, home: t };
    }
    return { away: away3, home: teamsStr.slice(3, 6) };
  }

  function centToAmerican(pct) {
    if (pct <= 0 || pct >= 100) return null;
    const p = pct / 100;
    return p >= 0.5
      ? Math.round(-(p / (1 - p)) * 100)
      : Math.round(((1 - p) / p) * 100);
  }

  function kalshiMidToAmerican(yesBidDollars, yesAskDollars) {
    const bid = parseFloat(yesBidDollars) || 0;
    const ask = parseFloat(yesAskDollars) || 0;
    const mid = (bid + ask) / 2;
    if (mid <= 0 || mid >= 1) return null;
    return centToAmerican(mid * 100);
  }

  async function fetchKalshiNative() {
    const kDate = kalshiDateStr();

    let allMarkets = [];
    try {
      // Use the same endpoint the existing /api/kalshi uses — confirmed working
      const r = await fetch(
        `https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`,
        { headers: { 'Content-Type': 'application/json' } }
      );
      if (r.ok) {
        const data = await r.json();
        allMarkets = data.markets || [];
      }
    } catch(e) {
      console.error('Kalshi KXMLBGAME fetch error:', e.message);
    }

    // Also try broader fetch to catch F5/TT/NRFI series
    try {
      const r2 = await fetch(
        `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000`,
        { headers: { 'Content-Type': 'application/json' } }
      );
      if (r2.ok) {
        const data2 = await r2.json();
        const extra = (data2.markets || []).filter(m => {
          const t = (m.title || '').toLowerCase();
          const et = m.event_ticker || '';
          return et.includes(kDate) && (
            t.includes('inning') || t.includes('run') || t.includes('nrfi') ||
            t.includes('yrfi') || t.includes('first 5') || t.includes('winner')
          );
        });
        // Merge, dedupe by ticker
        const map = new Map(allMarkets.map(m => [m.ticker, m]));
        for (const m of extra) map.set(m.ticker, m);
        allMarkets = [...map.values()];
      }
    } catch(e) {
      console.error('Kalshi broad fetch error:', e.message);
    }

    // Filter to today
    const todayMarkets = allMarkets.filter(m => {
      const et = m.event_ticker || m.ticker || '';
      return et.includes(kDate);
    });

    console.log(`Kalshi native: ${allMarkets.length} total markets, ${todayMarkets.length} today (${kDate})`);

    // Log series tickers found today
    const series = [...new Set(todayMarkets.map(m => (m.event_ticker||'').split('-')[0]).filter(Boolean))];
    console.log('Kalshi series today:', JSON.stringify(series));

    if (todayMarkets.length > 0) {
      const sampleTitles = todayMarkets.slice(0, 10).map(m => m.title);
      console.log('Sample titles:', JSON.stringify(sampleTitles));
    }

    // Group by game key (away+home abbr)
    const byGame = {};

    function ensureGame(key) {
      if (!byGame[key]) byGame[key] = {};
      return byGame[key];
    }

    for (const m of todayMarkets) {
      const et = m.event_ticker || '';
      const ticker = m.ticker || '';
      const title = (m.title || '').toLowerCase();
      const seriesKey = et.split('-')[0];

      // Parse teams from event ticker
      // Format: SERIESKEY-YYMONDDHHMMAWAYOME
      const afterSeries = et.replace(`${seriesKey}-`, '');
      const timeMatch = afterSeries.match(/\d{2}[A-Z]{3}\d{2}(\d{4})([A-Z]+)$/);
      if (!timeMatch) continue;

      const teamsStr = timeMatch[2];
      const { away, home } = parseKalshiTeams(teamsStr);
      const gameKey = `${away}${home}`;
      const game = ensureGame(gameKey);

      const american = kalshiMidToAmerican(m.yes_bid_dollars, m.yes_ask_dollars);
      const bid = parseFloat(m.yes_bid_dollars) || 0;
      const ask = parseFloat(m.yes_ask_dollars) || 0;
      const mid = (bid + ask) / 2;
      const impliedPct = Math.round(mid * 10000) / 100;

      const mkData = { ticker, american, impliedPct, title: m.title, volume: parseFloat(m.volume_fp)||0 };

      // ML — game winner
      if (seriesKey === 'KXMLBGAME' || (title.includes('winner') && !title.includes('inning'))) {
        if (!game.ml) game.ml = { away: null, home: null };
        if (ticker.endsWith(`-${away}`)) game.ml.away = american;
        else if (ticker.endsWith(`-${home}`)) game.ml.home = american;

      // F5
      } else if (title.includes('first 5') || title.includes('5 innings') || title.includes('f5')) {
        if (!game.f5ml) game.f5ml = { away: null, home: null };
        if (ticker.endsWith(`-${away}`)) game.f5ml.away = american;
        else if (ticker.endsWith(`-${home}`)) game.f5ml.home = american;

      // F3 / F7 inning-result (Model Performance Phase 2A correction) --
      // these titles previously fell through every branch below and were
      // silently never added to `game` at all, even when allMarkets already
      // contained the raw market from the broad unfiltered fetch above.
      // Ticker prefixes for F3/F7 are NOT confirmed (see
      // docs/research/INNING_RESULT_MIGRATION.md) -- this branch matches on
      // TITLE TEXT only, so it works regardless of series ticker naming.
      } else if (title.includes('first 3') || title.includes('3 innings') || title.includes('through 3 innings')) {
        if (!game.f3ml) game.f3ml = { away: null, home: null, tie: null };
        if (ticker.endsWith('-TIE')) game.f3ml.tie = american;
        else if (ticker.endsWith(`-${away}`)) game.f3ml.away = american;
        else if (ticker.endsWith(`-${home}`)) game.f3ml.home = american;

      } else if (title.includes('first 7') || title.includes('7 innings') || title.includes('through 7 innings')) {
        if (!game.f7ml) game.f7ml = { away: null, home: null, tie: null };
        if (ticker.endsWith('-TIE')) game.f7ml.tie = american;
        else if (ticker.endsWith(`-${away}`)) game.f7ml.away = american;
        else if (ticker.endsWith(`-${home}`)) game.f7ml.home = american;

      // NRFI / YRFI
      } else if (title.includes('nrfi') || title.includes('yrfi') ||
                 title.includes('first inning') || title.includes('1st inning')) {
        if (!game.nrfi) game.nrfi = { nrfi: null, yrfi: null };
        if (title.includes('no run') || title.includes('nrfi')) game.nrfi.nrfi = american;
        else if (title.includes('yrfi') || title.includes('run scored') || title.includes('run in')) game.nrfi.yrfi = american;

      // Team total or game total
      } else if (title.includes('run') && (title.includes('over') || title.includes('under') || title.includes('+'))) {
        // Try to determine if it's a team total by looking for team name in title
        const isAwayTT = title.includes(away.toLowerCase()) || title.includes(teamsStr.slice(0, away.length).toLowerCase());
        const isHomeTT = title.includes(home.toLowerCase());
        const lineMatch = title.match(/(\d+\.?\d*)/);
        const line = lineMatch ? parseFloat(lineMatch[1]) : null;
        const isOver = title.includes('over') || title.includes('+') || title.includes('more');
        const isUnder = title.includes('under');

        if (isAwayTT && !isHomeTT) {
          if (!game.teamTotals) game.teamTotals = { away: {}, home: {} };
          if (isOver) { game.teamTotals.away.over = american; game.teamTotals.away.line = line; }
          else if (isUnder) game.teamTotals.away.under = american;
        } else if (isHomeTT && !isAwayTT) {
          if (!game.teamTotals) game.teamTotals = { away: {}, home: {} };
          if (isOver) { game.teamTotals.home.over = american; game.teamTotals.home.line = line; }
          else if (isUnder) game.teamTotals.home.under = american;
        } else {
          if (!game.total) game.total = {};
          if (isOver) { game.total.over = american; game.total.line = line; }
          else if (isUnder) game.total.under = american;
        }

      // No-silent-drop catch-all (Model Performance Phase 2A correction):
      // any market that matches none of the branches above (e.g. an
      // unrecognized horizon or title shape) is preserved here instead of
      // being discarded. This field is additive, consumed by nothing in
      // production, and exists purely so a future/unknown market is never
      // invisibly dropped by this endpoint again.
      } else {
        if (!game.unclassified) game.unclassified = [];
        game.unclassified.push({ ticker, title: m.title, seriesKey, american });
      }
    }

    console.log(`Kalshi game keys parsed: ${Object.keys(byGame).length} — ${Object.keys(byGame).join(', ')}`);
    return byGame;
  }

  // ── fetch The Odds API featured markets ───────────────────────────────────

  async function fetchFeatured() {
    const url = `${BASE}/sports/${SPORT}/odds`
      + `?apiKey=${apiKey}&bookmakers=${BOOKS}`
      + `&markets=h2h,spreads,totals`
      + `&oddsFormat=american&dateFormat=iso`;
    const r = await fetch(url);
    const remaining = r.headers.get('x-requests-remaining');
    const used = r.headers.get('x-requests-last');
    if (!r.ok) return { games: [], remaining, used, error: await r.text() };
    return { games: await r.json(), remaining, used };
  }

  async function fetchEventMarkets(eventId) {
    const url = `${BASE}/sports/${SPORT}/events/${eventId}/odds`
      + `?apiKey=${apiKey}&bookmakers=${BOOKS}`
      + `&markets=h2h_1st_5_innings,spreads_1st_5_innings,h2h_1st_1_innings,team_totals`
      + `&oddsFormat=american`;
    const r = await fetch(url);
    if (!r.ok) return null;
    return r.json();
  }

  // ── abbr normalization for Kalshi game key matching ───────────────────────

  const FULL_TO_ABBR = {
    'detroit tigers': 'DET', 'tampa bay rays': 'TB', 'san diego padres': 'SD',
    'philadelphia phillies': 'PHI', 'baltimore orioles': 'BAL', 'boston red sox': 'BOS',
    'miami marlins': 'MIA', 'washington nationals': 'WSH', 'cleveland guardians': 'CLE',
    'new york yankees': 'NYY', 'kansas city royals': 'KC', 'cincinnati reds': 'CIN',
    'toronto blue jays': 'TOR', 'atlanta braves': 'ATL', 'chicago white sox': 'CWS',
    'minnesota twins': 'MIN', 'san francisco giants': 'SF', 'milwaukee brewers': 'MIL',
    'texas rangers': 'TEX', 'st. louis cardinals': 'STL', 'athletics': 'ATH',
    'chicago cubs': 'CHC', 'pittsburgh pirates': 'PIT', 'houston astros': 'HOU',
    'colorado rockies': 'COL', 'los angeles angels': 'LAA', 'los angeles dodgers': 'LAD',
    'arizona diamondbacks': 'AZ', 'new york mets': 'NYM', 'seattle mariners': 'SEA',
    'new york yankees': 'NYY', 'oakland athletics': 'ATH',
  };

  function teamToAbbr(fullName) {
    return FULL_TO_ABBR[fullName.toLowerCase()] || fullName.slice(0,3).toUpperCase();
  }

  // ── main ──────────────────────────────────────────────────────────────────

  try {
    // Fetch all sources in parallel
    const [featuredResult, kalshiNative] = await Promise.all([
      fetchFeatured(),
      fetchKalshiNative(),
    ]);

    if (featuredResult.error) {
      return res.status(502).json({ error: featuredResult.error, source: 'the-odds-api' });
    }

    const rawGames = featuredResult.games;

    // Per-event additional markets (F5, TT, NRFI from non-Kalshi books)
    const eventResults = await Promise.all(
      rawGames.map(g => fetchEventMarkets(g.id))
    );

    // Merge additional markets into bookmaker entries
    const merged = rawGames.map((game, i) => {
      const extra = eventResults[i];
      if (!extra?.bookmakers) return game;
      const bkMap = {};
      for (const bk of game.bookmakers || []) bkMap[bk.key] = bk;
      for (const bk of extra.bookmakers || []) {
        if (!bkMap[bk.key]) {
          bkMap[bk.key] = bk;
        } else {
          bkMap[bk.key].markets = [
            ...(bkMap[bk.key].markets || []),
            ...(bk.markets || []),
          ];
        }
      }
      return { ...game, bookmakers: Object.values(bkMap) };
    });

    // Build output per game
    const games = merged.map(game => {
      const away = game.away_team;
      const home = game.home_team;

      const books = {};
      for (const bk of game.bookmakers || []) {
        books[bk.key] = parseBook(bk, away, home);
      }

      // Inject Kalshi native markets (F5, TT, NRFI) where Odds API is missing them
      const awayAbbr = teamToAbbr(away);
      const homeAbbr = teamToAbbr(home);
      const kalshiKey = `${awayAbbr}${homeAbbr}`;
      const kalshiGame = kalshiNative[kalshiKey] || null;

      if (kalshiGame) {
        if (!books.kalshi) books.kalshi = {};
        // ML from Odds API takes precedence (already there); fill gaps from native
        if (!books.kalshi.ml && kalshiGame.ml?.away && kalshiGame.ml?.home) {
          books.kalshi.ml = {
            away: kalshiGame.ml.away,
            home: kalshiGame.ml.home,
            source: 'kalshi_native',
          };
        }
        // F5 — only from native
        if (kalshiGame.f5ml?.away || kalshiGame.f5ml?.home) {
          books.kalshi.f5ml = {
            away: kalshiGame.f5ml.away,
            home: kalshiGame.f5ml.home,
            awayImplied: kalshiGame.f5ml.awayImplied,
            homeImplied: kalshiGame.f5ml.homeImplied,
            source: 'kalshi_native',
          };
        }
        // NRFI — only from native
        if (kalshiGame.nrfi?.nrfi || kalshiGame.nrfi?.yrfi) {
          books.kalshi.nrfi = {
            nrfi: kalshiGame.nrfi.nrfi,
            yrfi: kalshiGame.nrfi.yrfi,
            nrfiImplied: kalshiGame.nrfi.nrfiImplied,
            yrfiImplied: kalshiGame.nrfi.yrfiImplied,
            source: 'kalshi_native',
          };
        }
        // Team Totals — only from native
        if (kalshiGame.teamTotals) {
          books.kalshi.teamTotals = { ...kalshiGame.teamTotals, source: 'kalshi_native' };
        }
        // Game Total from native (supplement if Odds API missing)
        if (!books.kalshi.total && kalshiGame.total?.line) {
          books.kalshi.total = { ...kalshiGame.total, source: 'kalshi_native' };
        }
      }

      // Compute VF for all Kalshi markets
      const kalML = books.kalshi?.ml;
      const kalshiVF = kalML?.away && kalML?.home ? vigFree(kalML.away, kalML.home) : null;

      const kalF5 = books.kalshi?.f5ml;
      const kalshiF5VF = kalF5?.away && kalF5?.home ? vigFree(kalF5.away, kalF5.home) : null;

      // Pinnacle VF
      const pinML = books.pinnacle?.ml;
      const pinnacleVF = pinML?.away && pinML?.home
        ? { ...vigFree(pinML.away, pinML.home), source: 'pinnacle', available: true }
        : (() => {
            for (const fb of ['fanduel', 'draftkings', 'betmgm']) {
              const fbML = books[fb]?.ml;
              if (fbML?.away && fbML?.home)
                return { ...vigFree(fbML.away, fbML.home), source: fb, available: false };
            }
            return null;
          })();

      // Pinnacle F5 VF
      const pinF5 = books.pinnacle?.f5ml;
      const pinnacleF5VF = pinF5?.away && pinF5?.home
        ? { ...vigFree(pinF5.away, pinF5.home), source: 'pinnacle' }
        : (() => {
            for (const fb of ['fanduel', 'draftkings']) {
              const f = books[fb]?.f5ml;
              if (f?.away && f?.home) return { ...vigFree(f.away, f.home), source: fb };
            }
            return null;
          })();

      return {
        eventId:       game.id,
        commenceTime:  game.commence_time,
        awayTeam:      away,
        homeTeam:      home,
        awayAbbr,
        homeAbbr,
        kalshiKey,
        kalshiMatched: !!kalshiGame,
        books,
        pinnacleVF,
        kalshiVF,
        pinnacleF5VF,
        kalshiF5VF,
      };
    });

    // Summary stats
    const kalshiF5Count   = games.filter(g => g.books.kalshi?.f5ml?.away).length;
    const kalshiNRFICount = games.filter(g => g.books.kalshi?.nrfi?.nrfi).length;
    const kalshiTTCount   = games.filter(g => g.books.kalshi?.teamTotals?.away?.over).length;
    const kalshiMLCount   = games.filter(g => g.books.kalshi?.ml?.away).length;
    console.log(`Kalshi coverage: ML=${kalshiMLCount} F5=${kalshiF5Count} NRFI=${kalshiNRFICount} TT=${kalshiTTCount}`);

    return res.status(200).json({
      fetchedAt:        new Date().toISOString(),
      sport:            SPORT,
      gamesCount:       games.length,
      creditsRemaining: featuredResult.remaining,
      creditsUsedLast:  featuredResult.used,
      kalshiNativeKeys: Object.keys(kalshiNative),
      games,
    });

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
