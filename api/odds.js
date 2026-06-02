// /api/odds.js — v3.0
// Fetches all MLB odds from The Odds API for the fetch-slate GitHub Action.
// Returns a structured JSON object with Pinnacle, FD, DK, BetMGM, and Kalshi
// for all markets: h2h, spreads, totals, team_totals, F5 ML, F5 spread, NRFI/YRFI.

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  const BASE    = 'https://api.the-odds-api.com/v4';
  const SPORT   = 'baseball_mlb';
  const BOOKS   = 'pinnacle,fanduel,draftkings,betmgm,kalshi';
  const REGIONS = 'eu,us,us_ex';

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

  function price(outcomes, name) {
    const o = outcomes?.find(x => x.name?.toLowerCase() === name?.toLowerCase());
    return o?.price ?? null;
  }

  function point(outcomes, name) {
    const o = outcomes?.find(x => x.name?.toLowerCase() === name?.toLowerCase());
    return o?.point ?? null;
  }

  function parseBook(book, awayTeam, homeTeam, markets) {
    const get = key => book.markets?.find(m => m.key === key);
    const result = {};

    // h2h → ml
    const h2h = get('h2h');
    if (h2h) result.ml = {
      away: price(h2h.outcomes, awayTeam),
      home: price(h2h.outcomes, homeTeam),
      updated: h2h.last_update,
    };

    // spreads → rl
    const spreads = get('spreads');
    if (spreads) result.rl = {
      away:      price(spreads.outcomes, awayTeam),
      awayPoint: point(spreads.outcomes, awayTeam),
      home:      price(spreads.outcomes, homeTeam),
      homePoint: point(spreads.outcomes, homeTeam),
    };

    // totals → total
    const totals = get('totals');
    if (totals) {
      const over = totals.outcomes?.find(o => o.name === 'Over');
      result.total = {
        line:  over?.point ?? null,
        over:  over?.price ?? null,
        under: totals.outcomes?.find(o => o.name === 'Under')?.price ?? null,
      };
    }

    // team_totals → teamTotals
    const tt = get('team_totals');
    if (tt) {
      const init = t => ({ line: null, over: null, under: null, team: t });
      const away = init(awayTeam), home = init(homeTeam);
      for (const o of tt.outcomes || []) {
        const side = o.description?.toLowerCase() === awayTeam.toLowerCase() ? away
                   : o.description?.toLowerCase() === homeTeam.toLowerCase() ? home : null;
        if (!side) continue;
        if (o.name === 'Over')  { side.over = o.price; side.line = o.point; }
        if (o.name === 'Under') { side.under = o.price; }
      }
      result.teamTotals = { away, home };
    }

    // h2h_1st_5_innings → f5ml
    const f5h2h = get('h2h_1st_5_innings');
    if (f5h2h) result.f5ml = {
      away:    price(f5h2h.outcomes, awayTeam),
      home:    price(f5h2h.outcomes, homeTeam),
      updated: f5h2h.last_update,
    };

    // spreads_1st_5_innings → f5rl
    const f5rl = get('spreads_1st_5_innings');
    if (f5rl) result.f5rl = {
      away:      price(f5rl.outcomes, awayTeam),
      awayPoint: point(f5rl.outcomes, awayTeam),
      home:      price(f5rl.outcomes, homeTeam),
      homePoint: point(f5rl.outcomes, homeTeam),
    };

    // h2h_1st_1_innings → nrfi (YRFI = Yes run scores, NRFI = No)
    const nrfi = get('h2h_1st_1_innings');
    if (nrfi) result.nrfi = {
      yrfi: price(nrfi.outcomes, 'Yes') ?? price(nrfi.outcomes, awayTeam),
      nrfi: price(nrfi.outcomes, 'No'),
      updated: nrfi.last_update,
    };

    return result;
  }

  function buildGameEntry(game, bookmakers) {
    const away = game.away_team;
    const home = game.home_team;

    // Debug: log all market keys available per bookmaker for first game
  const isFirstGame = !global._debuggedFirstGame;
  if (isFirstGame) {
    global._debuggedFirstGame = true;
    for (const bk of bookmakers) {
      const keys = bk.markets?.map(m => m.key) || [];
      console.log('MARKETS', bk.key, JSON.stringify(keys));
    }
  }
  const books = {};
    for (const bk of bookmakers) {
      books[bk.key] = parseBook(bk, away, home, bk.markets);
    }

    // Pinnacle VF — primary sharp reference
    const pinML = books.pinnacle?.ml;
    const pinnacleVF = pinML?.away && pinML?.home
      ? { ...vigFree(pinML.away, pinML.home), source: 'pinnacle', available: true }
      : (() => {
          for (const fb of ['fanduel', 'draftkings', 'betmgm']) {
            const fbML = books[fb]?.ml;
            if (fbML?.away && fbML?.home)
              return { ...vigFree(fbML.away, fbML.home), source: fb, available: false,
                       note: 'Pinnacle unavailable — fallback used' };
          }
          return null;
        })();

    // Kalshi VF — edge target
    const kalML = books.kalshi?.ml;
    const kalshiVF = kalML?.away && kalML?.home
      ? vigFree(kalML.away, kalML.home)
      : null;

    // F5 Pinnacle VF
    const pinF5 = books.pinnacle?.f5ml;
    const pinnacleF5VF = pinF5?.away && pinF5?.home
      ? vigFree(pinF5.away, pinF5.home)
      : (() => {
          for (const fb of ['fanduel', 'draftkings', 'betmgm']) {
            const fbF5 = books[fb]?.f5ml;
            if (fbF5?.away && fbF5?.home)
              return { ...vigFree(fbF5.away, fbF5.home), source: fb };
          }
          return null;
        })();

    // Kalshi F5 VF
    const kalF5 = books.kalshi?.f5ml;
    const kalshiF5VF = kalF5?.away && kalF5?.home
      ? vigFree(kalF5.away, kalF5.home)
      : null;

    return {
      eventId:      game.id,
      commenceTime: game.commence_time,
      awayTeam:     away,
      homeTeam:     home,
      books,
      pinnacleVF,
      kalshiVF,
      pinnacleF5VF,
      kalshiF5VF,
    };
  }

  // ── fetch featured markets (bulk) ──────────────────────────────────────────

  async function fetchFeatured() {
    const url = `${BASE}/sports/${SPORT}/odds`
      + `?apiKey=${apiKey}`
      + `&bookmakers=${BOOKS}`
      + `&markets=h2h,spreads,totals`
      + `&oddsFormat=american&dateFormat=iso`;
    const r = await fetch(url);
    const remaining = r.headers.get('x-requests-remaining');
    const used      = r.headers.get('x-requests-last');
    if (!r.ok) {
      const err = await r.text();
      return { games: [], remaining, used, error: err };
    }
    return { games: await r.json(), remaining, used };
  }

  // ── fetch additional markets per event ────────────────────────────────────

  async function fetchEventMarkets(eventId) {
    const url = `${BASE}/sports/${SPORT}/events/${eventId}/odds`
      + `?apiKey=${apiKey}`
      + `&bookmakers=${BOOKS}`
      + `&markets=h2h_1st_5_innings,spreads_1st_5_innings,h2h_1st_1_innings,team_totals`
      + `&oddsFormat=american`;
    const r = await fetch(url);
    if (!r.ok) return null;
    return r.json();
  }

  // ── main ──────────────────────────────────────────────────────────────────

  try {
    // Step 1: featured markets
    const { games: rawGames, remaining, used, error } = await fetchFeatured();
    if (error) return res.status(502).json({ error, source: 'the-odds-api' });

    // Step 2: additional markets per event (parallel, capped at 20 concurrent)
    const eventResults = await Promise.all(
      rawGames.map(g => fetchEventMarkets(g.id))
    );

    // Step 3: merge additional markets into game bookmakers
    const merged = rawGames.map((game, i) => {
      const extra = eventResults[i];
      if (!extra?.bookmakers) return game;
      const bkMap = {};
      for (const bk of game.bookmakers || []) bkMap[bk.key] = bk;
      for (const bk of extra.bookmakers || []) {
        if (!bkMap[bk.key]) {
          bkMap[bk.key] = bk;
        } else {
          // append additional markets to existing bookmaker entry
          bkMap[bk.key].markets = [
            ...(bkMap[bk.key].markets || []),
            ...(bk.markets || []),
          ];
        }
      }
      return { ...game, bookmakers: Object.values(bkMap) };
    });

    // Step 4: build clean output
    const games = merged.map(g => buildGameEntry(g, g.bookmakers || []));

    const output = {
      fetchedAt:         new Date().toISOString(),
      sport:             SPORT,
      gamesCount:        games.length,
      creditsUsedLast:   used,
      creditsRemaining:  remaining,
      games,
    };

    return res.status(200).json(output);

  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
