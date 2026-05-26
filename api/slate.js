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

  const d = new Date(today + 'T12:00:00Z');
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kalshiDate = String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');

  const ABBR_MAP = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KCA':'KC','KC':'KC','LAA':'LAA','LAD':'LAD',
    'MIA':'MIA','MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY',
    'OAK':'OAK','ATH':'ATH','PHI':'PHI','PIT':'PIT','STL':'STL',
    'SDP':'SD','SD':'SD','SF':'SF','SEA':'SEA','TBR':'TB',
    'TB':'TB','TEX':'TEX','TOR':'TOR','WSH':'WSH','WSN':'WSH',
    'AZ':'AZ'
  };

  const PARK_WEATHER = {
    'NYY': { dome: false, name: 'Yankee Stadium',           parkFactor: 103 },
    'TOR': { dome: true,  name: 'Rogers Centre',            parkFactor: 100 },
    'BOS': { dome: false, name: 'Fenway Park',              parkFactor: 104 },
    'BAL': { dome: false, name: 'Oriole Park',              parkFactor:  99 },
    'TB':  { dome: false, name: 'Tropicana Field',          parkFactor:  97, covered: true },
    'CLE': { dome: false, name: 'Progressive Field',        parkFactor:  96 },
    'DET': { dome: false, name: 'Comerica Park',            parkFactor:  97 },
    'CWS': { dome: false, name: 'Guaranteed Rate Field',    parkFactor: 101 },
    'MIN': { dome: false, name: 'Target Field',             parkFactor:  97 },
    'KC':  { dome: false, name: 'Kauffman Stadium',         parkFactor: 100 },
    'TEX': { dome: true,  name: 'Globe Life Field',         parkFactor: 100 },
    'HOU': { dome: true,  name: 'Minute Maid Park',         parkFactor:  99 },
    'SEA': { dome: true,  name: 'T-Mobile Park',            parkFactor:  95 },
    'LAA': { dome: false, name: 'Angel Stadium',            parkFactor:  99 },
    'ATH': { dome: false, name: 'Sutter Health Park',       parkFactor: 100 },
    'ATL': { dome: false, name: 'Truist Park',              parkFactor: 101 },
    'PHI': { dome: false, name: 'Citizens Bank Park',       parkFactor: 101 },
    'NYM': { dome: false, name: 'Citi Field',               parkFactor:  95 },
    'WSH': { dome: false, name: 'Nationals Park',           parkFactor:  99 },
    'MIA': { dome: true,  name: 'loanDepot park',           parkFactor:  98 },
    'MIL': { dome: true,  name: 'American Family Field',    parkFactor: 100 },
    'CHC': { dome: false, name: 'Wrigley Field',            parkFactor: 101 },
    'STL': { dome: false, name: 'Busch Stadium',            parkFactor:  99 },
    'CIN': { dome: false, name: 'Great American Ball Park', parkFactor: 108 },
    'PIT': { dome: false, name: 'PNC Park',                 parkFactor:  98 },
    'LAD': { dome: false, name: 'Dodger Stadium',           parkFactor:  96 },
    'SD':  { dome: false, name: 'Petco Park',               parkFactor:  97 },
    'SF':  { dome: false, name: 'Oracle Park',              parkFactor:  96 },
    'ARI': { dome: true,  name: 'Chase Field',              parkFactor: 105 },
    'COL': { dome: false, name: 'Coors Field',              parkFactor: 115 },
  };

  // Games that are not yet started — only these get model + edge calc
  const SCHEDULED_STATUSES = ['Scheduled', 'Pre-Game', 'Warmup'];

  function parseKalshiTeams(teamsStr) {
    const twoLetter = ['TB','AZ','SF','SD','KC'];
    for (const t of twoLetter) {
      if (teamsStr.startsWith(t)) return { awayK: t, homeK: teamsStr.slice(t.length) };
    }
    const away3 = teamsStr.slice(0, 3);
    const rest = teamsStr.slice(3);
    for (const t of twoLetter) {
      if (rest.startsWith(t)) return { awayK: away3, homeK: t };
    }
    return { awayK: away3, homeK: teamsStr.slice(3, 6) };
  }

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    function splitCSVLine(line) {
      const result = [];
      let current = '';
      let inQuotes = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
        else { current += ch; }
      }
      result.push(current.trim());
      return result;
    }
    const headers = splitCSVLine(lines[0]);
    return lines.slice(1).map(line => {
      const values = splitCSVLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = values[i] || ''; });
      return obj;
    });
  }

  function pf(val) {
    const n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  function parseStreak(streakCode) {
    if (!streakCode) return 0;
    const match = streakCode.match(/([WL])(\d+)/);
    if (!match) return 0;
    return match[1] === 'W' ? parseInt(match[2]) : -parseInt(match[2]);
  }

  function calcModelProb(g, awaySavant, homeSavant, awayBullpen, homeBullpen,
                          awayStanding, homeStanding, pinVigFree, bookOdds) {

    let awayProb = 0.50;
    const factors = {};

    // 1. Home field advantage
    awayProb -= 0.04;
    factors.homeField = -0.04;

    // 2. Starter xERA gap
    const awayXERA = awaySavant?.xERA ?? null;
    const homeXERA = homeSavant?.xERA ?? null;
    if (awayXERA !== null && homeXERA !== null) {
      const adj = (homeXERA - awayXERA) * 0.04;
      awayProb += adj;
      factors.starterXERA = Math.round(adj * 1000) / 1000;
    }

    // 3. Starter whiff gap
    const awayWhiff = awaySavant?.whiffPct ?? null;
    const homeWhiff = homeSavant?.whiffPct ?? null;
    if (awayWhiff !== null && homeWhiff !== null) {
      const adj = (homeWhiff - awayWhiff) * 0.003;
      awayProb += adj;
      factors.starterWhiff = Math.round(adj * 1000) / 1000;
    }

    // 4. Starter hard hit gap
    const awayHH = awaySavant?.hardHitPct ?? null;
    const homeHH = homeSavant?.hardHitPct ?? null;
    if (awayHH !== null && homeHH !== null) {
      const adj = (homeHH - awayHH) * 0.002;
      awayProb += adj;
      factors.starterHardHit = Math.round(adj * 1000) / 1000;
    }

    // 5. Bullpen xFIP gap
    const awayBPxFIP = awayBullpen?.xFIP ?? null;
    const homeBPxFIP = homeBullpen?.xFIP ?? null;
    if (awayBPxFIP !== null && homeBPxFIP !== null) {
      const weight = awaySavant?.highWalkRisk ? 0.03 : 0.02;
      const adj = (homeBPxFIP - awayBPxFIP) * weight;
      awayProb += adj;
      factors.bullpen = Math.round(adj * 1000) / 1000;
    }

    // 6. Run differential gap — tightened to /1000 to prevent dominating
    const awayRD = awayStanding?.runDiff ?? null;
    const homeRD = homeStanding?.runDiff ?? null;
    if (awayRD !== null && homeRD !== null) {
      const adj = (awayRD - homeRD) / 1000;
      awayProb += adj;
      factors.runDiff = Math.round(adj * 1000) / 1000;
    }

    // 7. Streak gap — capped at 5 games to prevent outliers
    const awayStreak = Math.max(-5, Math.min(5, parseStreak(awayStanding?.streak)));
    const homeStreak = Math.max(-5, Math.min(5, parseStreak(homeStanding?.streak)));
    const streakAdj = (awayStreak - homeStreak) * 0.005;
    awayProb += streakAdj;
    factors.streak = Math.round(streakAdj * 1000) / 1000;

    // 8. Park factor
    const parkFactor = PARK_WEATHER[g.home.abbr]?.parkFactor ?? 100;
    const parkAdj = (parkFactor - 100) * 0.001;
    awayProb += parkAdj;
    factors.parkFactor = Math.round(parkAdj * 1000) / 1000;

    // 9. Vegas total proxy — only for scheduled games with valid totals
    const pinnacleTotal = bookOdds?.pinnacle?.total?.point ?? null;
    if (pinnacleTotal !== null && pinVigFree !== null) {
      const parkNeutralTotal = 8.5;
      const totalAdj = (pinnacleTotal - parkNeutralTotal) * 0.002;
      awayProb += totalAdj;
      factors.vegasTotal = Math.round(totalAdj * 1000) / 1000;
    }

    // Clamp to reasonable range
    awayProb = Math.max(0.15, Math.min(0.85, awayProb));
    const homeProb = 1 - awayProb;

    // Confidence rating
    const hasBothSavant  = awaySavant  !== null && homeSavant  !== null;
    const hasBothBullpen = awayBullpen !== null && homeBullpen !== null;
    const xERAGap = (awayXERA !== null && homeXERA !== null)
      ? Math.abs(awayXERA - homeXERA) : 0;

    let confidence;
    if      (hasBothSavant && hasBothBullpen && xERAGap > 1.0) confidence = 'HIGH';
    else if (hasBothSavant && hasBothBullpen)                   confidence = 'MEDIUM';
    else if (hasBothSavant || hasBothBullpen)                   confidence = 'LOW';
    else                                                         confidence = 'INSUFFICIENT';

    const vsPin = pinVigFree
      ? Math.round((awayProb * 100 - pinVigFree.away) * 10) / 10
      : null;

    return {
      away: Math.round(awayProb * 1000) / 10,
      home: Math.round(homeProb * 1000) / 10,
      confidence,
      factors,
      vsPin,
      vsKalshi: null,
    };
  }

  try {
    const [pitchersRes, oddsRes, kalshiRes, teamStatsRes, standingsRes,
           savantPitcherRes, savantBatterRes, bullpenRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc`),
      fetch(`https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak`),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=pitching&gameType=R&stats=season&playerPool=relief`)
    ]);

    // ── PARSE SCHEDULE ─────────────────────────────────────────────────────────
    const pitcherData = await pitchersRes.json();
    const games = [];
    for (const dt of pitcherData.dates || []) {
      for (const game of dt.games || []) {
        const away     = game.teams?.away;
        const home     = game.teams?.home;
        const homeAbbr = home?.team?.abbreviation;
        const park     = PARK_WEATHER[homeAbbr] || { dome: false, name: game.venue?.name, parkFactor: 100 };
        games.push({
          gameId:    game.gamePk,
          status:    game.status?.detailedState,
          startTime: game.gameDate,
          venue:     game.venue?.name,
          park,
          away: {
            team: away?.team?.name,
            abbr: away?.team?.abbreviation,
            record: `${away?.leagueRecord?.wins}-${away?.leagueRecord?.losses}`,
            pitcher: away?.probablePitcher ? {
              name: away.probablePitcher.fullName,
              id:   String(away.probablePitcher.id),
              note: away.probablePitcher.note || ''
            } : null
          },
          home: {
            team: home?.team?.name,
            abbr: homeAbbr,
            record: `${home?.leagueRecord?.wins}-${home?.leagueRecord?.losses}`,
            pitcher: home?.probablePitcher ? {
              name: home.probablePitcher.fullName,
              id:   String(home.probablePitcher.id),
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
      const over  = tot.outcomes?.find(o => o.name === 'Over');
      const under = tot.outcomes?.find(o => o.name === 'Under');
      return { point: over?.point, over: over?.price, under: under?.price };
    };

    // ── PARSE KALSHI ───────────────────────────────────────────────────────────
    const kalshiData    = kalshiRes.ok ? await kalshiRes.json() : { markets: [] };
    const kalshiMarkets = (kalshiData.markets || []).filter(m =>
      m.event_ticker && m.event_ticker.includes(kalshiDate)
    );

    const parsedKalshi = kalshiMarkets.map(m => {
      const yesBidD = parseFloat(m.yes_bid_dollars) || 0;
      const yesAskD = parseFloat(m.yes_ask_dollars) || 0;
      const mid     = (yesBidD + yesAskD) / 2;
      const et      = m.event_ticker || '';
      const afterDate = et.replace(`KXMLBGAME-${kalshiDate}`, '');
      const timeStr   = afterDate.slice(0, 4);
      const teamsStr  = afterDate.slice(4);
      const { awayK, homeK } = parseKalshiTeams(teamsStr);
      return {
        ticker: m.ticker, eventTicker: et, title: m.title || '',
        awayAbbr: awayK, homeAbbr: homeK, timeStr,
        yesBid:     Math.round(yesBidD * 100),
        yesAsk:     Math.round(yesAskD * 100),
        mid:        Math.round(mid * 100),
        impliedPct: Math.round(mid * 1000) / 10,
        volume:     parseFloat(m.volume_fp) || 0,
        closeTime:  m.close_time
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

    // ── PARSE STANDINGS ────────────────────────────────────────────────────────
    const standingsData = await standingsRes.json();
    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins: team.wins, losses: team.losses, pct: team.winningPercentage,
          streak:      team.streak?.streakCode,
          runsScored:  team.runsScored,
          runsAllowed: team.runsAllowed,
          runDiff:     team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank,
          leagueRank:   team.leagueRank
        };
      }
    }

    // ── PARSE SAVANT PITCHERS ──────────────────────────────────────────────────
    const savantPitchers = {};
    const savantBatters  = {};

    if (savantPitcherRes.ok) {
      const rows = parseCSV(await savantPitcherRes.text());
      for (const p of rows) {
        const id = p['player_id'];
        if (!id) continue;
        const bbPct = pf(p['bb_percent']);
        const xERA  = pf(p['xera']);
        savantPitchers[id] = {
          name:         p['last_name, first_name'] || '',
          kPct:         pf(p['k_percent']),
          bbPct,
          whiffPct:     pf(p['whiff_percent']),
          xERA,
          hardHitPct:   pf(p['hard_hit_percent']),
          exitVeloAvg:  pf(p['exit_velocity_avg']),
          barrelPct:    pf(p['barrel_batted_rate']),
          highWalkRisk: bbPct !== null && bbPct > 9.2,
          eliteStarter: xERA !== null && xERA < 2.50,
        };
      }
    }

    if (savantBatterRes.ok) {
      const rows = parseCSV(await savantBatterRes.text());
      for (const b of rows) {
        const id = b['player_id'];
        if (!id) continue;
        savantBatters[id] = {
          name:        b['last_name, first_name'] || '',
          kPct:        pf(b['k_percent']),
          bbPct:       pf(b['bb_percent']),
          whiffPct:    pf(b['whiff_percent']),
          xwOBA:       pf(b['xwoba']),
          hardHitPct:  pf(b['hard_hit_percent']),
          barrelPct:   pf(b['barrel_batted_rate']),
          exitVeloAvg: pf(b['exit_velocity_avg']),
        };
      }
    }

    // ── PARSE BULLPENS ─────────────────────────────────────────────────────────
    // Fix: try multiple response structures since playerPool=relief
    // may return data under different paths
    const bullpens = {};
    if (bullpenRes.ok) {
      const bullpenData = await bullpenRes.json();
      const leagueHR9   = 1.20;

      // Try both possible response paths
      const splits =
        bullpenData?.stats?.[0]?.splits ||
        bullpenData?.stats?.[0]?.teams  ||
        [];

      for (const rec of splits) {
        const abbr = rec.team?.abbreviation;
        if (!abbr) continue;
        const s      = rec.stat || {};
        const era    = pf(s.era);
        const hr9    = pf(s.homeRunsPer9);
        const kPer9  = pf(s.strikeoutsPer9Inn);
        const bbPer9 = pf(s.walksPer9Inn);
        let xFIP = null;
        if (era !== null && hr9 !== null) {
          xFIP = Math.round((era - (hr9 - leagueHR9) * 1.35) * 100) / 100;
        }
        bullpens[abbr] = {
          era, xFIP,
          whip:       pf(s.whip),
          kPer9,
          bbPer9,
          hr9,
          elite:      xFIP !== null && xFIP < 3.50,
          vulnerable: xFIP !== null && xFIP > 4.50,
        };
      }
    }

    // ── ENRICH GAMES ───────────────────────────────────────────────────────────
    const enriched = games.map(g => {
      const isScheduled = SCHEDULED_STATUSES.includes(g.status);

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
          const tot   = implH + implA;
          pinVigFree  = {
            home: Math.round(implH/tot*1000)/10,
            away: Math.round(implA/tot*1000)/10
          };
        }
      }

      // Kalshi match
      const awayK      = ABBR_MAP[g.away.abbr] || g.away.abbr;
      const homeK      = ABBR_MAP[g.home.abbr] || g.home.abbr;
      const kalshiKey  = `${awayK}${homeK}`;
      const gameKalshi = kalshiByGame[kalshiKey] || [];
      const kalshiAway = gameKalshi.find(m => m.ticker.endsWith('-' + awayK)) || null;
      const kalshiML   = kalshiAway || gameKalshi.sort((a,b) => b.volume - a.volume)[0] || null;

      // Savant + bullpen
      const awayPitcherId = g.away.pitcher?.id || null;
      const homePitcherId = g.home.pitcher?.id || null;
      const awaySavant    = awayPitcherId ? (savantPitchers[awayPitcherId] || null) : null;
      const homeSavant    = homePitcherId ? (savantPitchers[homePitcherId] || null) : null;
      const awayBullpen   = bullpens[g.away.abbr] || null;
      const homeBullpen   = bullpens[g.home.abbr] || null;
      const awayStanding  = standings[g.away.abbr] || null;
      const homeStanding  = standings[g.home.abbr] || null;

      // Model probability — only for scheduled games
      let modelProb = null;
      let edge      = null;

      if (isScheduled) {
        modelProb = calcModelProb(
          g, awaySavant, homeSavant, awayBullpen, homeBullpen,
          awayStanding, homeStanding, pinVigFree, bookOdds
        );

        if (kalshiAway !== null) {
          modelProb.vsKalshi = Math.round((modelProb.away - kalshiAway.impliedPct) * 10) / 10;
        }

        if (kalshiAway) {
          const kalAway        = kalshiAway.impliedPct;
          const pinAway        = pinVigFree?.away ?? null;
          const modelEdgeRaw   = (modelProb.away - kalAway) / 100;
          const modelEdgeAdj   = Math.round(modelEdgeRaw * 0.30 * 1000) / 10;
          const pinGap         = pinAway !== null
            ? Math.round((pinAway - kalAway) * 10) / 10
            : null;

          edge = {
            yesTeam:          g.away.team,
            noTeam:           g.home.team,
            modelAwayPct:     modelProb.away,
            kalshiYesImplied: kalAway,
            pinVfAway:        pinAway,
            pinVfHome:        pinVigFree?.home ?? null,
            modelEdgeAdj,
            pinGap,
            actionable:  Math.abs(modelEdgeAdj) >= 3.0,
            logForCLV:   Math.abs(modelEdgeAdj) >= 1.5,
            direction:   modelEdgeRaw > 0 ? 'BUY_YES' : 'BUY_NO',
            betTeam:     modelEdgeRaw > 0 ? g.away.team : g.home.team,
            betSide:     modelEdgeRaw > 0 ? 'YES' : 'NO',
            confidence:  modelProb.confidence,
          };
        }
      }

      const awayStats = { ...teamStats[g.away.abbr], record: awayStanding };
      const homeStats = { ...teamStats[g.home.abbr], record: homeStanding };

      return {
        ...g,
        away: { ...g.away, pitcherSavant: awaySavant, bullpen: awayBullpen },
        home: { ...g.home, pitcherSavant: homeSavant, bullpen: homeBullpen },
        odds: bookOdds,
        pinVigFree,
        kalshi:    { markets: gameKalshi, ml: kalshiML },
        modelProb,
        edge,
        awayTeamStats: awayStats,
        homeTeamStats: homeStats
      };
    });

    const result = {
      date: today,
      kalshiDate,
      games: enriched,
      requestsRemaining:    remaining,
      kalshiMarketsFound:   parsedKalshi.length,
      savantPitchersLoaded: Object.keys(savantPitchers).length,
      bullpensLoaded:       Object.keys(bullpens).length,
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
