export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'API key not configured' });

  const { date, callback } = req.query;

  const today = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  // Kalshi date format: 2026-05-23 -> 26MAY23
  const d = new Date(today + 'T12:00:00Z');
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kalshiDate = String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');

  // MLB team abbreviation map: MLB abbr -> Kalshi 3-letter code
  const ABBR_MAP = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KCA':'KC', 'KC' :'KC', 'LAA':'LAA','LAD':'LAD',
    'MIA':'MIA','MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY',
    'OAK':'OAK','ATH':'OAK','PHI':'PHI','PIT':'PIT','STL':'STL',
    'SDP':'SD', 'SD' :'SD', 'SF' :'SF', 'SEA':'SEA','TBR':'TB',
    'TB' :'TB', 'TEX':'TEX','TOR':'TOR','WSH':'WSH','WSN':'WSH'
  };

  try {
    // Fetch all three sources in parallel
    const [pitchersRes, oddsRes, kalshiRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`)
    ]);

    // ── PITCHERS ──────────────────────────────────────────────────────────────
    const pitcherData = await pitchersRes.json();
    const games = [];
    for (const dt of pitcherData.dates || []) {
      for (const game of dt.games || []) {
        const away = game.teams?.away;
        const home = game.teams?.home;
        games.push({
          gameId: game.gamePk,
          status: game.status?.detailedState,
          startTime: game.gameDate,
          venue: game.venue?.name,
          away: {
            team: away?.team?.name,
            abbr: away?.team?.abbreviation,
            record: `${away?.leagueRecord?.wins}-${away?.leagueRecord?.losses}`,
            pitcher: away?.probablePitcher ? {
              name: away.probablePitcher.fullName,
              id: away.probablePitcher.id,
              note: away.probablePitcher.note || ''
            } : null
          },
          home: {
            team: home?.team?.name,
            abbr: home?.team?.abbreviation,
            record: `${home?.leagueRecord?.wins}-${home?.leagueRecord?.losses}`,
            pitcher: home?.probablePitcher ? {
              name: home.probablePitcher.fullName,
              id: home.probablePitcher.id,
              note: home.probablePitcher.note || ''
            } : null
          }
        });
      }
    }

    // ── PINNACLE / BOOKS ODDS ─────────────────────────────────────────────────
    const oddsData = await oddsRes.json();
    const remaining = oddsRes.headers.get('x-requests-remaining');

    const extractH2H = (bk, homeTeam, awayTeam) => {
      if (!bk) return null;
      const h2h = bk.markets?.find(m => m.key === 'h2h');
      if (!h2h) return null;
      const home = h2h.outcomes?.find(o => o.name === homeTeam);
      const away = h2h.outcomes?.find(o => o.name === awayTeam);
      return { home: home?.price, away: away?.price, updated: h2h.last_update };
    };

    const extractTotal = (bk) => {
      if (!bk) return null;
      const tot = bk.markets?.find(m => m.key === 'totals');
      if (!tot) return null;
      const over = tot.outcomes?.find(o => o.name === 'Over');
      const under = tot.outcomes?.find(o => o.name === 'Under');
      return { point: over?.point, over: over?.price, under: under?.price };
    };

    // ── KALSHI MARKETS ────────────────────────────────────────────────────────
    const kalshiData = kalshiRes.ok ? await kalshiRes.json() : { markets: [] };
    const kalshiMarkets = (kalshiData.markets || []).filter(m =>
      m.event_ticker && m.event_ticker.includes(kalshiDate)
    );

    // Parse each Kalshi market
    const parsedKalshi = kalshiMarkets.map(m => {
      const yesBidD = parseFloat(m.yes_bid_dollars) || 0;
      const yesAskD = parseFloat(m.yes_ask_dollars) || 0;
      const mid = (yesBidD + yesAskD) / 2;
      const et = m.event_ticker || '';
      // Strip series prefix and date: "KXMLBGAME-26MAY23" leaving "1840STLCIN"
      const afterDate = et.replace(`KXMLBGAME-${kalshiDate}`, '');
      const timeStr = afterDate.slice(0, 4);
      const teamsStr = afterDate.slice(4);
      const awayK = teamsStr.slice(0, 3);
      const homeK = teamsStr.slice(3, 6);
      return {
        ticker: m.ticker,
        eventTicker: et,
        title: m.title || '',
        awayAbbr: awayK,
        homeAbbr: homeK,
        timeStr,
        yesBid: Math.round(yesBidD * 100),
        yesAsk: Math.round(yesAskD * 100),
        mid: Math.round(mid * 100),
        impliedPct: Math.round(mid * 1000) / 10,
        volume: parseFloat(m.volume_fp) || 0,
        closeTime: m.close_time
      };
    });

    // Group Kalshi markets by game (away+home key)
    const kalshiByGame = {};
    for (const km of parsedKalshi) {
      const key = `${km.awayAbbr}${km.homeAbbr}`;
      if (!kalshiByGame[key]) kalshiByGame[key] = [];
      kalshiByGame[key].push(km);
    }

    // ── MATCH & ENRICH ────────────────────────────────────────────────────────
    const enriched = games.map(g => {
      // Match Pinnacle odds
      const oddsMatch = Array.isArray(oddsData) ? oddsData.find(o =>
        o.home_team === g.home.team || o.away_team === g.away.team
      ) : null;

      let bookOdds = null;
      if (oddsMatch) {
        const pin = oddsMatch.bookmakers?.find(b => b.key === 'pinnacle');
        const dk  = oddsMatch.bookmakers?.find(b => b.key === 'draftkings');
        const fd  = oddsMatch.bookmakers?.find(b => b.key === 'fanduel');
        const mgm = oddsMatch.bookmakers?.find(b => b.key === 'betmgm');
        bookOdds = {
          pinnacle:   { h2h: extractH2H(pin, g.home.team, g.away.team), total: extractTotal(pin) },
          draftkings: { h2h: extractH2H(dk,  g.home.team, g.away.team), total: extractTotal(dk)  },
          fanduel:    { h2h: extractH2H(fd,  g.home.team, g.away.team), total: extractTotal(fd)  },
          betmgm:     { h2h: extractH2H(mgm, g.home.team, g.away.team), total: extractTotal(mgm) }
        };
      }

      // Match Kalshi markets using abbreviation map
      const awayK = ABBR_MAP[g.away.abbr] || g.away.abbr;
      const homeK = ABBR_MAP[g.home.abbr] || g.home.abbr;
      const kalshiKey = `${awayK}${homeK}`;
      const gameKalshi = kalshiByGame[kalshiKey] || [];

      // Compute Pinnacle vig-free probabilities
      let pinVigFree = null;
      if (bookOdds?.pinnacle?.h2h) {
        const ph = bookOdds.pinnacle.h2h;
        if (ph.home && ph.away) {
          const implH = ph.home >= 100 ? 100/(ph.home+100) : Math.abs(ph.home)/(Math.abs(ph.home)+100);
          const implA = ph.away >= 100 ? 100/(ph.away+100) : Math.abs(ph.away)/(Math.abs(ph.away)+100);
          const tot = implH + implA;
          pinVigFree = {
            home: Math.round(implH/tot*1000)/10,
            away: Math.round(implA/tot*1000)/10
          };
        }
      }

      // Find Kalshi ML market (highest volume moneyline)
      // Kalshi ML title contains "win" — pick the one with most volume
      const mlMarkets = gameKalshi.filter(k =>
        k.title.toLowerCase().includes('win') ||
        k.title.toLowerCase().includes('moneyline') ||
        k.title.toLowerCase().includes('ml')
      ).sort((a,b) => b.volume - a.volume);

      const kalshiML = mlMarkets[0] || gameKalshi.sort((a,b) => b.volume - a.volume)[0] || null;

      // Calculate edge: Pinnacle vig-free vs Kalshi implied
      // Kalshi YES = AWAY team wins (first team in title: "Pittsburgh vs Toronto Winner?")
      // Event ticker PIKTOR = away PIT, home TOR, YES = PIT wins
      let edge = null;
      if (pinVigFree && kalshiML) {
        const pinAway = pinVigFree.away;
        const kalAway = kalshiML.impliedPct;
        const gap = Math.round((pinAway - kalAway) * 10) / 10;
        edge = {
          yesTeam: g.away.team,
          noTeam: g.home.team,
          pinVfAway: pinAway,
          pinVfHome: pinVigFree.home,
          kalshiYesImplied: kalAway,
          gapPct: gap,
          direction: gap > 0 ? 'BUY_YES' : 'BUY_NO',
          betTeam: gap > 0 ? g.away.team : g.home.team,
          betSide: gap > 0 ? 'YES' : 'NO'
        };
      }

      return {
        ...g,
        odds: bookOdds,
        pinVigFree,
        kalshi: {
          markets: gameKalshi,
          ml: kalshiML,
          allTickers: gameKalshi.map(k => k.ticker)
        },
        edge
      };
    });

    const result = {
      date: today,
      kalshiDate,
      games: enriched,
      requestsRemaining: remaining,
      kalshiMarketsFound: parsedKalshi.length
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    if (req.query.callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${req.query.callback}(${JSON.stringify({ error: error.message })})`);
    }
    return res.status(500).json({ error: error.message });
  }
}
