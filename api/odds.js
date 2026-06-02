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
  // We pull those directly from Kalshi's public trade API.

  function kalshiDateStr() {
    const d = new Date();
    const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
    return String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');
  }

  function parseKalshiTeams(teamsStr) {
    // Handles 2-letter abbreviations: TB, AZ, SF, SD, KC, NY
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

  function kalshiMidToAmerican(yesBidDollars, yesAskDollars) {
    const bid = parseFloat(yesBidDollars) || 0;
    const ask = parseFloat(yesAskDollars) || 0;
    const mid = (bid + ask) / 2;
    if (mid <= 0 || mid >= 1) return null;
    const pct = mid * 100;
    return centToAmerican(pct);
  }

  async function fetchKalshiNative() {
    // Pull ALL open Kalshi markets and sort them into game buckets
    // Series tickers we care about:
    //   KXMLBGAME    = ML (game winner)
    //   KXMLBF5      = F5 (first 5 innings winner) — may vary
    //   KXMLBTOTAL   = game total runs
    //   KXMLBTT      = team total runs — may vary
    //   KXMLBNRFI    = NRFI/YRFI — may vary
    // We fetch broadly and sort by title keywords.

    const kDate = kalshiDateStr();

    let allMarkets = [];
    try {
      // Fetch all open markets with baseball-relevant series
      const seriesKeys = ['KXMLBGAME', 'KXMLBF5', 'KXMLBNRFI', 'KXMLBTT', 'KXMLBTOTAL', 'KXMLB'];
      const fetches = seriesKeys.map(s =>
        fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=${s}&status=open&limit=200`)
          .then(r => r.ok ? r.json() : { markets: [] })
          .then(d => d.markets || [])
          .catch(() => [])
      );
      const results = await Promise.all(fetches);
      // Also fetch broad open markets to catch any series we missed
      const broadFetch = await fetch(
        `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000`
      ).then(r => r.ok ? r.json() : { markets: [] }).then(d => d.markets || []).catch(() => []);

      allMarkets = [...new Map(
        [...results.flat(), ...broadFetch]
          .filter(m => m.ticker)
          .map(m => [m.ticker, m])
      ).values()];
    } catch(e) {
      console.error('Kalshi native fetch error:', e.message);
      return {};
    }

    // Filter to today's games
    const todayMarkets = allMarkets.filter(m => {
      const et = m.event_ticker || m.ticker || '';
      return et.includes(kDate);
    });

    console.log(`Kalshi native: ${allMarkets.length} total, ${todayMarkets.length} today (${kDate})`);

    // Log all unique series tickers for debugging
    const series = [...new Set(todayMarkets.map(m => (m.event_ticker||'').split('-')[0]))];
    console.log('Kalshi series today:', JSON.stringify(series));

    // Log sample titles to understand market naming
    const sampleTitles = todayMarkets.slice(0, 20).map(m => m.title);
    console.log('Sample Kalshi titles:', JSON.stringify(sampleTitles));

    // Group by game (away+home abbreviation pair)
    // Structure: { "DETTB": { ml: {...}, f5ml: {...}, total: {...}, teamTotals: {...}, nrfi: {...} } }
    const byGame = {};

    function ensureGame(key) {
      if (!byGame[key]) byGame[key] = { ml: null, f5ml: null, total: null, teamTotals: null, nrfi: null };
      return byGame[key];
    }

    for (const m of todayMarkets) {
      const et = m.event_ticker || '';
      const ticker = m.ticker || '';
      const title = (m.title || '').toLowerCase();
      const series = et.split('-')[0];

      // Extract team abbreviations from event ticker
      // Format: KXMLBGAME-26JUN021840DETTB
      // After series prefix: -YYMONDD + HHMM + AWAYOME
      const afterSeries = et.replace(`${series}-`, '');
      // afterSeries = "26JUN021840DETTB"
      // Find the time (4 digits after date) then teams after that
      const timeMatch = afterSeries.match(/\d{2}[A-Z]{3}\d{2}(\d{4})([A-Z]+)$/);
      if (!timeMatch) continue;
      const teamsStr = timeMatch[2];
      const { away, home } = parseKalshiTeams(teamsStr);
      const gameKey = `${away}${home}`;

      const mid = ((parseFloat(m.yes_bid_dollars)||0) + (parseFloat(m.yes_ask_dollars)||0)) / 2;
      const american = kalshiMidToAmerican(m.yes_bid_dollars, m.yes_ask_dollars);
      const impliedPct = Math.round(mid * 10000) / 100;

      const marketData = {
        ticker,
        american,
        impliedPct,
        mid: Math.round(mid * 10000) / 100,
        yesBid: Math.round((parseFloat(m.yes_bid_dollars)||0) * 100),
        yesAsk: Math.round((parseFloat(m.yes_ask_dollars)||0) * 100),
        volume: parseFloat(m.volume_fp) || 0,
        closeTime: m.close_time,
        title: m.title,
      };

      const game = ensureGame(gameKey);

      // Classify market type by series and title keywords
      if (series === 'KXMLBGAME' || title.includes('winner')) {
        // ML — ticker ends in -AWAY (away team wins) or -HOME
        // The "YES" contract = that team wins
        if (!game.ml) game.ml = { away: null, home: null, awayImplied: null, homeImplied: null };
        if (ticker.endsWith(`-${away}`)) {
          game.ml.away = american;
          game.ml.awayImplied = impliedPct;
        } else if (ticker.endsWith(`-${home}`)) {
          game.ml.home = american;
          game.ml.homeImplied = impliedPct;
        }

      } else if (
        series.includes('F5') || title.includes('first 5') || title.includes('5 innings')
      ) {
        if (!game.f5ml) game.f5ml = { away: null, home: null, awayImplied: null, homeImplied: null };
        if (ticker.endsWith(`-${away}`)) {
          game.f5ml.away = american;
          game.f5ml.awayImplied = impliedPct;
        } else if (ticker.endsWith(`-${home}`)) {
          game.f5ml.home = american;
          game.f5ml.homeImplied = impliedPct;
        }

      } else if (
        series.includes('NRFI') || title.includes('nrfi') || title.includes('yrfi') ||
        title.includes('first inning') || title.includes('1st inning')
      ) {
        if (!game.nrfi) game.nrfi = { nrfi: null, yrfi: null, nrfiImplied: null, yrfiImplied: null };
        if (title.includes('no run') || title.includes('nrfi')) {
          game.nrfi.nrfi = american;
          game.nrfi.nrfiImplied = impliedPct;
        } else if (title.includes('run scored') || title.includes('yrfi')) {
          game.nrfi.yrfi = american;
          game.nrfi.yrfiImplied = impliedPct;
        }

      } else if (
        title.includes('total run') || title.includes('runs scored') ||
        series.includes('TOTAL') || series.includes('TT')
      ) {
        // Could be game total or team total
        // Team total titles often include the team name
        const awayMentioned = title.includes(away.toLowerCase()) || title.includes(teamsStr.toLowerCase());
        const homeMentioned = title.includes(home.toLowerCase());

        if (awayMentioned && !homeMentioned) {
          if (!game.teamTotals) game.teamTotals = { away: { line: null, over: null, under: null }, home: { line: null, over: null, under: null } };
          // Parse over/under from title: "over X.5 runs" or "X.5+ runs"
          const lineMatch = title.match(/(\d+\.?\d*)\+?\s*run/);
          const line = lineMatch ? parseFloat(lineMatch[1]) : null;
          if (title.includes('over') || title.includes('+')) {
            game.teamTotals.away.over = american;
            game.teamTotals.away.line = line;
          } else if (title.includes('under')) {
            game.teamTotals.away.under = american;
          }
        } else if (homeMentioned && !awayMentioned) {
          if (!game.teamTotals) game.teamTotals = { away: { line: null, over: null, under: null }, home: { line: null, over: null, under: null } };
          const lineMatch = title.match(/(\d+\.?\d*)\+?\s*run/);
          const line = lineMatch ? parseFloat(lineMatch[1]) : null;
          if (title.includes('over') || title.includes('+')) {
            game.teamTotals.home.over = american;
            game.teamTotals.home.line = line;
          } else if (title.includes('under')) {
            game.teamTotals.home.under = american;
          }
        } else {
          // Game total
          if (!game.total) game.total = { line: null, over: null, under: null };
          const lineMatch = title.match(/(\d+\.?\d*)\+?\s*run/);
          const line = lineMatch ? parseFloat(lineMatch[1]) : null;
          if (title.includes('over') || title.includes('+')) {
            game.total.over = american;
            game.total.line = line;
          } else if (title.includes('under')) {
            game.total.under = american;
          }
        }
      }
    }

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
