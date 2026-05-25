export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  const { date, callback } = req.query;

  const today = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  // Kalshi date format: 2026-05-23 -> 26MAY23
  const d = new Date(today + 'T12:00:00Z');
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kalshiDate = String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');

  // MLB team abbr -> Kalshi 3-letter
  const ABBR_MAP = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KCA':'KC','KC':'KC','LAA':'LAA','LAD':'LAD',
    'MIA':'MIA','MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY',
    'OAK':'OAK','ATH':'OAK','PHI':'PHI','PIT':'PIT','STL':'STL',
    'SDP':'SD','SD':'SD','SF':'SF','SEA':'SEA','TBR':'TB',
    'TB':'TB','TEX':'TEX','TOR':'TOR','WSH':'WSH','WSN':'WSH',
    'AZ':'ARI'
  };

  // Ballpark weather lookup by home team abbr
  const PARK_WEATHER = {
    'NYY': { dome: false, name: 'Yankee Stadium' },
    'TOR': { dome: true,  name: 'Rogers Centre' },
    'BOS': { dome: false, name: 'Fenway Park' },
    'BAL': { dome: false, name: 'Oriole Park' },
    'TB':  { dome: false, name: 'Tropicana Field', covered: true },
    'CLE': { dome: false, name: 'Progressive Field' },
    'DET': { dome: false, name: 'Comerica Park' },
    'CWS': { dome: false, name: 'Guaranteed Rate Field' },
    'MIN': { dome: false, name: 'Target Field' },
    'KC':  { dome: false, name: 'Kauffman Stadium' },
    'TEX': { dome: true,  name: 'Globe Life Field' },
    'HOU': { dome: true,  name: 'Minute Maid Park' },
    'SEA': { dome: true,  name: 'T-Mobile Park' },
    'LAA': { dome: false, name: 'Angel Stadium' },
    'ATH': { dome: false, name: 'Sutter Health Park' },
    'ATL': { dome: false, name: 'Truist Park' },
    'PHI': { dome: false, name: 'Citizens Bank Park' },
    'NYM': { dome: false, name: 'Citi Field' },
    'WSH': { dome: false, name: 'Nationals Park' },
    'MIA': { dome: true,  name: 'loanDepot park' },
    'MIL': { dome: true,  name: 'American Family Field' },
    'CHC': { dome: false, name: 'Wrigley Field' },
    'STL': { dome: false, name: 'Busch Stadium' },
    'CIN': { dome: false, name: 'Great American Ball Park' },
    'PIT': { dome: false, name: 'PNC Park' },
    'LAD': { dome: false, name: 'Dodger Stadium' },
    'SD':  { dome: false, name: 'Petco Park' },
    'SF':  { dome: false, name: 'Oracle Park' },
    'ARI': { dome: true,  name: 'Chase Field' },
    'COL': { dome: false, name: 'Coors Field' },
  };

  try {
    // Fetch all sources in parallel
    const [pitchersRes, oddsRes, kalshiRes, teamStatsRes, weatherRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc`),
      fetch(`https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak`)
    ]);

    // ── PARSE PITCHERS ─────────────────────────────────────────────────────────
    const pitcherData = await pitchersRes.json();
    const games = [];
    for (const dt of pitcherData.dates || []) {
      for (const game of dt.games || []) {
        const away = game.teams?.away;
        const home = game.teams?.home;
        const homeAbbr = home?.team?.abbreviation;
        const park = PARK_WEATHER[homeAbbr] || { dome: false, name: game.venue?.name };
        games.push({
          gameId: game.gamePk,
          status: game.status?.detailedState,
          startTime: game.gameDate,
          venue: game.venue?.name,
          park,
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
            abbr: homeAbbr,
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

    // ── PARSE ODDS ─────────────────────────────────────────────────────────────
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

    // ── PARSE KALSHI ───────────────────────────────────────────────────────────
    const kalshiData = kalshiRes.ok ? await kalshiRes.json() : { markets: [] };
    const kalshiMarkets = (kalshiData.markets || []).filter(m =>
      m.event_ticker && m.event_ticker.includes(kalshiDate)
    );
    const parsedKalshi = kalshiMarkets.map(m => {
      const yesBidD = parseFloat(m.yes_bid_dollars) || 0;
      const yesAskD = parseFloat(m.yes_ask_dollars) || 0;
      const mid = (yesBidD + yesAskD) / 2;
      const et = m.event_ticker || '';
      const afterDate = et.replace(`KXMLBGAME-${kalshiDate}`, '');
      const timeStr = afterDate.slice(0, 4);
      const teamsStr = afterDate.slice(4);
      const awayK = teamsStr.slice(0, 3);
      const homeK = teamsStr.slice(3, 6);
      return {
        ticker: m.ticker, eventTicker: et, title: m.title || '',
        awayAbbr: awayK, homeAbbr: homeK, timeStr,
        yesBid: Math.round(yesBidD * 100), yesAsk: Math.round(yesAskD * 100),
        mid: Math.round(mid * 100),
        impliedPct: Math.round(mid * 1000) / 10,
        volume: parseFloat(m.volume_fp) || 0,
        closeTime: m.close_time
      };
    });
    const kalshiByGame = {};
    for (const km of parsedKalshi) {
      const key = `${km.awayAbbr}${km.homeAbbr}`;
      if (!kalshiByGame[key]) kalshiByGame[key] = [];
      kalshiByGame[key].push(km);
    }

    // ── PARSE TEAM STATS ───────────────────────────────────────────────────────
    const teamStatsData = await teamStatsRes.json();
    const teamStats = {};
    for (const rec of (teamStatsData?.stats?.[0]?.splits || [])) {
      const abbr = rec.team?.abbreviation;
      if (!abbr) continue;
      const s = rec.stat || {};
      teamStats[abbr] = {
        gamesPlayed: s.gamesPlayed, runs: s.runs, hits: s.hits,
        homeRuns: s.homeRuns, strikeOuts: s.strikeOuts,
        baseOnBalls: s.baseOnBalls, avg: s.avg, obp: s.obp,
        slg: s.slg, ops: s.ops, atBats: s.atBats,
        runsPerGame: s.gamesPlayed ? (s.runs / s.gamesPlayed).toFixed(2) : null
      };
    }

    // ── PARSE STANDINGS / STREAKS ──────────────────────────────────────────────
    const standingsData = await weatherRes.json(); // reusing slot for standings
    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins: team.wins, losses: team.losses, pct: team.winningPercentage,
          streak: team.streak?.streakCode,
          runsScored: team.runsScored, runsAllowed: team.runsAllowed,
          runDiff: team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank, leagueRank: team.leagueRank
        };
      }
    }

    // ── ENRICH GAMES ───────────────────────────────────────────────────────────
    const enriched = games.map(g => {
      // Match Odds API
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

      // Pinnacle vig-free
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

      // Match Kalshi — YES = away team
      const awayK = ABBR_MAP[g.away.abbr] || g.away.abbr;
      const homeK = ABBR_MAP[g.home.abbr] || g.home.abbr;
      const kalshiKey = `${awayK}${homeK}`;
      const gameKalshi = kalshiByGame[kalshiKey] || [];
      const kalshiML = gameKalshi.find(m => m.awayAbbr === awayK) || gameKalshi.sort((a,b) => b.volume - a.volume)[0] || null;

      // Kalshi edge (YES = away)
      let edge = null;
      if (pinVigFree && kalshiML) {
        const pinAway = pinVigFree.away;
        const kalAway = kalshiML.impliedPct;
        const gap = Math.round((pinAway - kalAway) * 10) / 10;
        edge = {
          yesTeam: g.away.team, noTeam: g.home.team,
          pinVfAway: pinAway, pinVfHome: pinVigFree.home,
          kalshiYesImplied: kalAway, gapPct: gap,
          direction: gap > 0 ? 'BUY_YES' : 'BUY_NO',
          betTeam: gap > 0 ? g.away.team : g.home.team,
          betSide: gap > 0 ? 'YES' : 'NO'
        };
      }

      // Team stats
      const awayStats = { ...teamStats[g.away.abbr], record: standings[g.away.abbr] };
      const homeStats = { ...teamStats[g.home.abbr], record: standings[g.home.abbr] };

      return {
        ...g,
        odds: bookOdds,
        pinVigFree,
        kalshi: { markets: gameKalshi, ml: kalshiML },
        edge,
        awayTeamStats: awayStats,
        homeTeamStats: homeStats
      };
    });

    const result = {
      date: today, kalshiDate,
      games: enriched,
      requestsRemaining: remaining,
      kalshiMarketsFound: parsedKalshi.length
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch(error) {
    if (req.query.callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${req.query.callback}(${JSON.stringify({ error: error.message })})`);
    }
    return res.status(500).json({ error: error.message });
  }
}
