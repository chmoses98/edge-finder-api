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

  const SCHEDULED_STATUSES = ['Scheduled', 'Pre-Game', 'Warmup'];

  const MLB_ID_TO_ABBR = {
    133:'ATH',134:'PIT',135:'SD',136:'SEA',137:'SF',138:'STL',
    139:'TB',140:'TEX',141:'TOR',142:'MIN',143:'PHI',144:'ATL',
    145:'CWS',146:'MIA',147:'NYY',158:'MIL',108:'LAA',109:'AZ',
    110:'BAL',111:'BOS',112:'CHC',113:'CIN',114:'CLE',115:'COL',
    116:'DET',117:'HOU',118:'KC',119:'LAD',120:'WSH',121:'NYM'
  };

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

  function americanToImplied(odds) {
    if (odds == null) return null;
    if (odds >= 100) return 100 / (odds + 100);
    return Math.abs(odds) / (Math.abs(odds) + 100);
  }

  function safeGet(obj, key) {
    if (obj == null) return null;
    const val = obj[key];
    return val == null ? null : val;
  }

  function calcModelProb(g, awaySavant, homeSavant, awayBullpen, homeBullpen,
                          awayStanding, homeStanding, pinVigFree, bookOdds) {
    let awayProb = 0.50;
    const factors = {};

    awayProb -= 0.04;
    factors.homeField = -0.04;

    const awayXERA = safeGet(awaySavant, 'xERA');
    const homeXERA = safeGet(homeSavant, 'xERA');
    if (awayXERA !== null && homeXERA !== null) {
      const adj = (homeXERA - awayXERA) * 0.04;
      awayProb += adj;
      factors.starterXERA = Math.round(adj * 1000) / 1000;
    }

    const awayWhiff = safeGet(awaySavant, 'whiffPct');
    const homeWhiff = safeGet(homeSavant, 'whiffPct');
    if (awayWhiff !== null && homeWhiff !== null) {
      const adj = (homeWhiff - awayWhiff) * 0.003;
      awayProb += adj;
      factors.starterWhiff = Math.round(adj * 1000) / 1000;
    }

    const awayHH = safeGet(awaySavant, 'hardHitPct');
    const homeHH = safeGet(homeSavant, 'hardHitPct');
    if (awayHH !== null && homeHH !== null) {
      const adj = (homeHH - awayHH) * 0.002;
      awayProb += adj;
      factors.starterHardHit = Math.round(adj * 1000) / 1000;
    }

    const awayBPxFIP = safeGet(awayBullpen, 'xFIP');
    const homeBPxFIP = safeGet(homeBullpen, 'xFIP');
    if (awayBPxFIP !== null && homeBPxFIP !== null) {
      const weight = safeGet(awaySavant, 'highWalkRisk') ? 0.03 : 0.02;
      const adj = (homeBPxFIP - awayBPxFIP) * weight;
      awayProb += adj;
      factors.bullpen = Math.round(adj * 1000) / 1000;
    }

    const awayRD = safeGet(awayStanding, 'runDiff');
    const homeRD = safeGet(homeStanding, 'runDiff');
    if (awayRD !== null && homeRD !== null) {
      const adj = (awayRD - homeRD) / 1000;
      awayProb += adj;
      factors.runDiff = Math.round(adj * 1000) / 1000;
    }

    const awayStreak = Math.max(-5, Math.min(5, parseStreak(safeGet(awayStanding, 'streak'))));
    const homeStreak = Math.max(-5, Math.min(5, parseStreak(safeGet(homeStanding, 'streak'))));
    const streakAdj = (awayStreak - homeStreak) * 0.005;
    awayProb += streakAdj;
    factors.streak = Math.round(streakAdj * 1000) / 1000;

    const parkFactor = safeGet(PARK_WEATHER[g.home.abbr], 'parkFactor') ?? 100;
    const parkAdj = (parkFactor - 100) * 0.001;
    awayProb += parkAdj;
    factors.parkFactor = Math.round(parkAdj * 1000) / 1000;

    const pinnacleTotal = safeGet(safeGet(safeGet(bookOdds, 'pinnacle'), 'total'), 'point');
    if (pinnacleTotal !== null && pinVigFree !== null) {
      const totalAdj = (pinnacleTotal - 8.5) * 0.002;
      awayProb += totalAdj;
      factors.vegasTotal = Math.round(totalAdj * 1000) / 1000;
    }

    awayProb = Math.max(0.15, Math.min(0.85, awayProb));
    const homeProb = 1 - awayProb;

    const hasBothSavant  = awaySavant  != null && homeSavant  != null;
    const hasBothBullpen = awayBullpen != null && homeBullpen != null;
    const xERAGap = (awayXERA !== null && homeXERA !== null) ? Math.abs(awayXERA - homeXERA) : 0;

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
      confidence, factors, vsPin, vsKalshi: null,
    };
  }

  function projectRunTotal(awaySavant, homeSavant, awayBullpen, homeBullpen,
                            parkFactor, vegasTotal) {
    let projected = vegasTotal != null ? vegasTotal : 8.5;

    const awayK = safeGet(awaySavant, 'kPct');
    const homeK = safeGet(homeSavant, 'kPct');
    if (awayK !== null) projected -= Math.max(0, (awayK - 20) / 100) * 3;
    if (homeK !== null) projected -= Math.max(0, (homeK - 20) / 100) * 3;

    const parkAdj = (parkFactor - 100) / 100 * 2;
    projected += parkAdj;

    const awayBP = safeGet(awayBullpen, 'xFIP');
    const homeBP = safeGet(homeBullpen, 'xFIP');
    if (awayBP !== null && awayBP > 4.5) projected += 0.3;
    if (homeBP !== null && homeBP > 4.5) projected += 0.3;

    return Math.round(projected * 10) / 10;
  }

  function evalNRFI(awaySavant, homeSavant) {
    if (awaySavant == null || homeSavant == null) return null;

    const awayK  = safeGet(awaySavant, 'kPct');
    const homeK  = safeGet(homeSavant, 'kPct');
    const awayBB = safeGet(awaySavant, 'bbPct');
    const homeBB = safeGet(homeSavant, 'bbPct');
    const awayWh = safeGet(awaySavant, 'whiffPct');
    const homeWh = safeGet(homeSavant, 'whiffPct');

    if (awayK === null || homeK === null) return null;

    let nrfiScore = 0;
    let yrfiScore = 0;
    const reasons = [];

    if (awayK >= 25)      { nrfiScore += 2; reasons.push(`Away K%: ${awayK}%`); }
    else if (awayK >= 22) { nrfiScore += 1; }
    else if (awayK < 16)  { yrfiScore += 2; reasons.push(`Away K% low: ${awayK}%`); }

    if (homeK >= 25)      { nrfiScore += 2; reasons.push(`Home K%: ${homeK}%`); }
    else if (homeK >= 22) { nrfiScore += 1; }
    else if (homeK < 16)  { yrfiScore += 2; reasons.push(`Home K% low: ${homeK}%`); }

    if (awayBB !== null) {
      if (awayBB > 10)       { yrfiScore += 2; reasons.push(`Away BB% high: ${awayBB}%`); }
      else if (awayBB > 9.2) { yrfiScore += 1; }
      else if (awayBB < 6)   { nrfiScore += 1; reasons.push(`Away BB% low: ${awayBB}%`); }
    }

    if (homeBB !== null) {
      if (homeBB > 10)       { yrfiScore += 2; reasons.push(`Home BB% high: ${homeBB}%`); }
      else if (homeBB > 9.2) { yrfiScore += 1; }
      else if (homeBB < 6)   { nrfiScore += 1; reasons.push(`Home BB% low: ${homeBB}%`); }
    }

    if (awayWh !== null && awayWh >= 30) { nrfiScore += 1; reasons.push(`Away whiff%: ${awayWh}%`); }
    if (homeWh !== null && homeWh >= 30) { nrfiScore += 1; reasons.push(`Home whiff%: ${homeWh}%`); }

    const total = nrfiScore + yrfiScore;
    if (total === 0) return null;

    const nrfiPct = Math.round(nrfiScore / total * 100);
    const yrfiPct = 100 - nrfiPct;

    let lean, leanStrength;
    if      (nrfiScore >= 5 && yrfiScore <= 1) { lean = 'NRFI'; leanStrength = 'STRONG'; }
    else if (nrfiScore > yrfiScore + 1)         { lean = 'NRFI'; leanStrength = 'LEAN'; }
    else if (yrfiScore >= 4 && nrfiScore <= 1)  { lean = 'YRFI'; leanStrength = 'STRONG'; }
    else if (yrfiScore > nrfiScore + 1)         { lean = 'YRFI'; leanStrength = 'LEAN'; }
    else                                         { lean = 'NEUTRAL'; leanStrength = 'NEUTRAL'; }

    return { lean, leanStrength, nrfiScore, yrfiScore, nrfiPct, yrfiPct, reasons };
  }

  function evalF5(awaySavant, homeSavant, awayStanding, homeStanding) {
    if (awaySavant == null || homeSavant == null) return null;

    const awayXERA = safeGet(awaySavant, 'xERA');
    const homeXERA = safeGet(homeSavant, 'xERA');
    if (awayXERA === null || homeXERA === null) return null;

    let awayF5 = 0.50;
    awayF5 -= 0.03;

    const xeraAdj = (homeXERA - awayXERA) * 0.05;
    awayF5 += xeraAdj;

    const awayWhiff = safeGet(awaySavant, 'whiffPct');
    const homeWhiff = safeGet(homeSavant, 'whiffPct');
    if (awayWhiff !== null && homeWhiff !== null) {
      awayF5 += (homeWhiff - awayWhiff) * 0.003;
    }

    const awayRD = safeGet(awayStanding, 'runDiff');
    const homeRD = safeGet(homeStanding, 'runDiff');
    if (awayRD !== null && homeRD !== null) {
      awayF5 += (awayRD - homeRD) / 1200;
    }

    const awayStreak = Math.max(-5, Math.min(5, parseStreak(safeGet(awayStanding, 'streak'))));
    const homeStreak = Math.max(-5, Math.min(5, parseStreak(safeGet(homeStanding, 'streak'))));
    awayF5 += (awayStreak - homeStreak) * 0.004;

    awayF5 = Math.max(0.15, Math.min(0.85, awayF5));

    const xERAGap = Math.abs(awayXERA - homeXERA);

    return {
      awayF5Pct:   Math.round(awayF5 * 1000) / 10,
      homeF5Pct:   Math.round((1 - awayF5) * 1000) / 10,
      xERAGap:     Math.round(xERAGap * 100) / 100,
      f5Amplified: xERAGap > 1.5,
      favoredSide: awayF5 > 0.52 ? 'AWAY' : awayF5 < 0.48 ? 'HOME' : 'NEUTRAL',
    };
  }

  function evalRunLine(modelAwayPct, bookOdds) {
    if (modelAwayPct == null) return null;
    const pin = safeGet(bookOdds, 'pinnacle');
    if (pin == null) return null;
    const rl = safeGet(pin, 'runLine');
    if (rl == null) return null;

    const favored = modelAwayPct > 50 ? 'AWAY' : 'HOME';
    const modelWinProb = favored === 'AWAY' ? modelAwayPct / 100 : (100 - modelAwayPct) / 100;
    const rlCoverProb = modelWinProb * 0.82;
    const rlImplied = favored === 'AWAY'
      ? americanToImplied(safeGet(rl, 'away'))
      : americanToImplied(safeGet(rl, 'home'));

    if (rlImplied == null) return null;

    const rlEdge = Math.round((rlCoverProb - rlImplied) * 1000) / 10;

    return {
      favored,
      modelCoverPct:   Math.round(rlCoverProb * 1000) / 10,
      impliedCoverPct: Math.round(rlImplied * 1000) / 10,
      edge:      rlEdge,
      actionable: rlEdge >= 3.0,
      logForCLV:  rlEdge >= 1.5,
    };
  }

  function evalGameTotal(projectedTotal, bookOdds, awaySavant, homeSavant) {
    if (projectedTotal == null) return null;
    const pin = safeGet(safeGet(bookOdds, 'pinnacle'), 'total');
    if (pin == null) return null;

    const vegasLine    = safeGet(pin, 'point');
    const overImplied  = americanToImplied(safeGet(pin, 'over'));
    const underImplied = americanToImplied(safeGet(pin, 'under'));
    if (vegasLine == null || overImplied == null || underImplied == null) return null;

    const diff       = projectedTotal - vegasLine;
    const modelLean  = diff > 0.4 ? 'OVER' : diff < -0.4 ? 'UNDER' : 'NEUTRAL';

    const awayK = safeGet(awaySavant, 'kPct') ?? 0;
    const homeK = safeGet(homeSavant, 'kPct') ?? 0;
    const bothHighK   = awayK >= 22 && homeK >= 22;
    const eitherElite = safeGet(awaySavant, 'eliteStarter') || safeGet(homeSavant, 'eliteStarter');

    let adjustedLean = modelLean;
    let leanNote = null;
    if (bothHighK && modelLean === 'OVER') {
      adjustedLean = 'NEUTRAL';
      leanNote = 'Both high-K starters override Over lean';
    }
    if (eitherElite) {
      adjustedLean = 'UNDER';
      leanNote = 'Elite starter on mound — Under lean per model rules';
    }

    const vigFreeOver  = overImplied  / (overImplied + underImplied);
    const vigFreeUnder = underImplied / (overImplied + underImplied);
    const modelOverProb  = 0.50 + Math.max(-0.12, Math.min(0.12, diff * 0.04));
    const modelUnderProb = 1 - modelOverProb;

    const overEdge  = Math.round((modelOverProb  - vigFreeOver)  * 1000) / 10;
    const underEdge = Math.round((modelUnderProb - vigFreeUnder) * 1000) / 10;
    const bestEdge  = overEdge > underEdge
      ? { side: 'OVER', edge: overEdge }
      : { side: 'UNDER', edge: underEdge };

    return {
      vegasLine,
      projectedTotal,
      diff:        Math.round(diff * 10) / 10,
      modelLean:   adjustedLean,
      leanNote,
      overEdge,
      underEdge,
      bestSide:    bestEdge.side,
      bestEdge:    bestEdge.edge,
      actionable:  Math.abs(bestEdge.edge) >= 3.0,
      logForCLV:   Math.abs(bestEdge.edge) >= 1.5,
    };
  }

  function evalTeamTotals(projectedTotal, modelAwayPct, awaySavant, homeSavant,
                           awayBullpen, homeBullpen) {
    if (projectedTotal == null) return null;

    const modelAway    = (modelAwayPct ?? 50) / 100;
    const awayRunShare = 0.5 + (modelAway - 0.5) * 0.3;
    const homeRunShare = 1 - awayRunShare;
    const projAwayRuns = Math.round(projectedTotal * awayRunShare * 10) / 10;
    const projHomeRuns = Math.round(projectedTotal * homeRunShare * 10) / 10;

    const homeStarterVuln = safeGet(homeSavant, 'xERA') !== null && safeGet(homeSavant, 'xERA') > 4.5;
    const awayStarterVuln = safeGet(awaySavant, 'xERA') !== null && safeGet(awaySavant, 'xERA') > 4.5;
    const homeBPvuln = safeGet(homeBullpen, 'vulnerable') ?? false;
    const awayBPvuln = safeGet(awayBullpen, 'vulnerable') ?? false;

    const awayTTOver = homeStarterVuln || homeBPvuln;
    const homeTTOver = awayStarterVuln || awayBPvuln;

    return {
      projectedAwayRuns: projAwayRuns,
      projectedHomeRuns: projHomeRuns,
      awayTTLean:   awayTTOver ? 'OVER' : 'NEUTRAL',
      homeTTLean:   homeTTOver ? 'OVER' : 'NEUTRAL',
      awayTTReason: awayTTOver
        ? (homeStarterVuln ? `Opp starter xERA ${safeGet(homeSavant,'xERA')}` : 'Opp bullpen vulnerable')
        : null,
      homeTTReason: homeTTOver
        ? (awayStarterVuln ? `Opp starter xERA ${safeGet(awaySavant,'xERA')}` : 'Opp bullpen vulnerable')
        : null,
    };
  }

  try {
    const [pitchersRes, oddsRes, kalshiRes, teamStatsRes, standingsRes,
           savantPitcherRes, savantBatterRes, bullpenRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,spreads,totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc`),
      fetch(`https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak`),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=pitching&gameType=R&stats=season`)
    ]);

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

    const extractRunLine = (bk, homeTeam, awayTeam) => {
      if (!bk) return null;
      const rl = bk.markets?.find(m => m.key === 'spreads');
      if (!rl) return null;
      const home = rl.outcomes?.find(o => o.name === homeTeam);
      const away = rl.outcomes?.find(o => o.name === awayTeam);
      if (!home || !away) return null;
      return {
        home: home.price, homePoint: home.point,
        away: away.price, awayPoint: away.point,
      };
    };

    const extractAltTotals = (bk) => {
      if (!bk) return [];
      const alt = bk.markets?.find(m => m.key === 'alternate_totals');
      if (!alt) return [];
      const lines = {};
      for (const o of (alt.outcomes || [])) {
        const pt = o.point;
        if (!lines[pt]) lines[pt] = {};
        if (o.name === 'Over')  lines[pt].over  = o.price;
        if (o.name === 'Under') lines[pt].under = o.price;
      }
      return Object.entries(lines)
        .map(([pt, odds]) => ({ point: parseFloat(pt), ...odds }))
        .sort((a, b) => a.point - b.point);
    };

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

    const standingsData = await standingsRes.json();
    const standings = {};
    for (const league of (standingsData.records || [])) {
      for (const team of (league.teamRecords || [])) {
        const abbr = team.team?.abbreviation;
        if (!abbr) continue;
        standings[abbr] = {
          wins: team.wins, losses: team.losses, pct: team.winningPercentage,
          streak:       team.streak?.streakCode,
          runsScored:   team.runsScored,
          runsAllowed:  team.runsAllowed,
          runDiff:      team.runsScored - team.runsAllowed,
          divisionRank: team.divisionRank,
          leagueRank:   team.leagueRank
        };
      }
    }

    // ── Opener role detection via MLB game logs ──────────────────────────────
    // Fetch last 10 game logs for each probable pitcher.
    // If avg IP/start < 3.0 → flag as openerRole.
    // Then fetch 1st-inning splits from Savant for flagged pitchers.
    async function fetchIPsForPitcher(pitcherId) {
      try {
        const r = await fetch(`https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats?stats=gameLog&group=pitching&season=2026&gameType=R&limit=10`);
        if (!r.ok) return null;
        const d = await r.json();
        const logs = d?.stats?.[0]?.splits || [];
        if (!logs.length) return null;
        const starts = logs.filter(l => l.stat?.gamesStarted > 0);
        if (!starts.length) return null;
        const totalIP = starts.reduce((sum, l) => {
          const ip = parseFloat(l.stat?.inningsPitched || '0');
          const full = Math.floor(ip); const frac = (ip % 1) / 0.3 * 0.333;
          return sum + full + frac;
        }, 0);
        return Math.round((totalIP / starts.length) * 100) / 100;
      } catch(e) { return null; }
    }

    // Collect all pitcher IDs from today's slate
    const allPitcherIds = [];
    for (const g of games) {
      if (g.away.pitcher?.id) allPitcherIds.push(g.away.pitcher.id);
      if (g.home.pitcher?.id) allPitcherIds.push(g.home.pitcher.id);
    }

    // Fetch IP/start for all pitchers in parallel
    const ipPerStart = {};
    await Promise.all(allPitcherIds.map(async (id) => {
      ipPerStart[id] = await fetchIPsForPitcher(id);
    }));

    // Flag opener roles
    const openerFlags = {};
    for (const [id, avg] of Object.entries(ipPerStart)) {
      openerFlags[id] = avg !== null && avg < 3.0;
    }

    // Fetch 1st-inning splits for flagged pitchers
    const flaggedIds = Object.entries(openerFlags)
      .filter(([, flagged]) => flagged)
      .map(([id]) => id);

    const firstInningSplits = {};
    if (flaggedIds.length) {
      try {
        const splitRes = await fetch(
          `https://edge-finder-api.vercel.app/api/savant?splits=true&playerIds=${flaggedIds.join(',')}&year=2026`
        );
        if (splitRes.ok) {
          const splitData = await splitRes.json();
          Object.assign(firstInningSplits, splitData.firstInningSplits || {});
        }
      } catch(e) { /* splits unavailable — gate logic handles this */ }
    }
    // ── End opener detection ──────────────────────────────────────────────────

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

    const bullpens = {};
    if (bullpenRes.ok) {
      const bullpenData = await bullpenRes.json();
      const leagueHR9   = 1.20;
      const splits      = bullpenData?.stats?.[0]?.splits || [];
      for (const rec of splits) {
        const abbr = MLB_ID_TO_ABBR[rec.team?.id];
        if (!abbr) continue;
        const s      = rec.stat || {};
        const era    = pf(s.era);
        const hr9    = pf(s.homeRunsPer9);
        let xFIP = null;
        if (era !== null && hr9 !== null) {
          xFIP = Math.round((era - (hr9 - leagueHR9) * 1.35) * 100) / 100;
        }
        bullpens[abbr] = {
          era, xFIP,
          whip:       pf(s.whip),
          kPer9:      pf(s.strikeoutsPer9Inn),
          bbPer9:     pf(s.walksPer9Inn),
          hr9,
          elite:      xFIP !== null && xFIP < 3.50,
          vulnerable: xFIP !== null && xFIP > 4.50,
        };
      }
    }

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
          pinnacle:   { h2h: extractH2H(pin,g.home.team,g.away.team), total: extractTotal(pin), runLine: extractRunLine(pin,g.home.team,g.away.team), altTotals: extractAltTotals(pin) },
          draftkings: { h2h: extractH2H(dk,g.home.team,g.away.team),  total: extractTotal(dk),  runLine: extractRunLine(dk,g.home.team,g.away.team),  altTotals: extractAltTotals(dk)  },
          fanduel:    { h2h: extractH2H(fd,g.home.team,g.away.team),  total: extractTotal(fd),  runLine: extractRunLine(fd,g.home.team,g.away.team),  altTotals: extractAltTotals(fd)  },
          betmgm:     { h2h: extractH2H(mgm,g.home.team,g.away.team), total: extractTotal(mgm), runLine: extractRunLine(mgm,g.home.team,g.away.team), altTotals: extractAltTotals(mgm) },
        };
      }

      let pinVigFree = null;
      if (bookOdds?.pinnacle?.h2h) {
        const ph = bookOdds.pinnacle.h2h;
        if (ph.home != null && ph.away != null) {
          const implH = ph.home >= 100 ? 100/(ph.home+100) : Math.abs(ph.home)/(Math.abs(ph.home)+100);
          const implA = ph.away >= 100 ? 100/(ph.away+100) : Math.abs(ph.away)/(Math.abs(ph.away)+100);
          const tot   = implH + implA;
          pinVigFree  = {
            home: Math.round(implH/tot*1000)/10,
            away: Math.round(implA/tot*1000)/10
          };
        }
      }

      const awayK      = ABBR_MAP[g.away.abbr] || g.away.abbr;
      const homeK      = ABBR_MAP[g.home.abbr] || g.home.abbr;
      const kalshiKey  = `${awayK}${homeK}`;
      const gameKalshi = kalshiByGame[kalshiKey] || [];
      const kalshiAway = gameKalshi.find(m => m.ticker.endsWith('-' + awayK)) || null;
      const kalshiML   = kalshiAway || gameKalshi.sort((a,b) => b.volume - a.volume)[0] || null;

      const awayPitcherId = g.away.pitcher?.id || null;
      const homePitcherId = g.home.pitcher?.id || null;
      const awaySavant    = awayPitcherId ? (savantPitchers[awayPitcherId] || null) : null;
      const homeSavant    = homePitcherId ? (savantPitchers[homePitcherId] || null) : null;
      const awayBullpen   = bullpens[g.away.abbr] || null;
      const homeBullpen   = bullpens[g.home.abbr] || null;
      const awayStanding  = standings[g.away.abbr] || null;
      const homeStanding  = standings[g.home.abbr] || null;
      const parkFactor    = g.park?.parkFactor ?? 100;

      let modelProb   = null;
      let mlEdge      = null;
      let runLineEval = null;
      let totalEval   = null;
      let teamTotals  = null;
      let nrfi        = null;
      let f5          = null;
      const allEdges  = [];

      if (isScheduled) {
        try {
          modelProb = calcModelProb(
            g, awaySavant, homeSavant, awayBullpen, homeBullpen,
            awayStanding, homeStanding, pinVigFree, bookOdds
          );
        } catch(e) { modelProb = null; }

        if (modelProb && kalshiAway) {
          modelProb.vsKalshi = Math.round((modelProb.away - kalshiAway.impliedPct) * 10) / 10;
        }

        const vegasTotal    = bookOdds?.pinnacle?.total?.point ?? null;
        const projectedTotal = projectRunTotal(awaySavant, homeSavant, awayBullpen, homeBullpen, parkFactor, vegasTotal);

        if (modelProb && kalshiAway) {
          try {
            const kalAway      = kalshiAway.impliedPct;
            const pinAway      = pinVigFree?.away ?? null;
            const modelEdgeRaw = (modelProb.away - kalAway) / 100;
            const modelEdgeAdj = Math.round(modelEdgeRaw * 0.30 * 1000) / 10;
            const pinGap       = pinAway !== null ? Math.round((pinAway - kalAway) * 10) / 10 : null;

            mlEdge = {
              market: 'ML',
              yesTeam: g.away.team, noTeam: g.home.team,
              modelAwayPct: modelProb.away, kalshiYesImplied: kalAway,
              pinVfAway: pinAway, pinVfHome: pinVigFree?.home ?? null,
              modelEdgeAdj, pinGap,
              actionable:  Math.abs(modelEdgeAdj) >= 3.0,
              logForCLV:   Math.abs(modelEdgeAdj) >= 1.5,
              direction:   modelEdgeRaw > 0 ? 'BUY_YES' : 'BUY_NO',
              betTeam:     modelEdgeRaw > 0 ? g.away.team : g.home.team,
              betSide:     modelEdgeRaw > 0 ? 'YES' : 'NO',
              confidence:  modelProb.confidence,
            };
            if (mlEdge.logForCLV) allEdges.push(mlEdge);
          } catch(e) {}
        }

        try { runLineEval = evalRunLine(modelProb?.away ?? null, bookOdds); } catch(e) { runLineEval = null; }
        if (runLineEval?.logForCLV) allEdges.push({ market: 'RUNLINE', ...runLineEval });

        try { totalEval = evalGameTotal(projectedTotal, bookOdds, awaySavant, homeSavant); } catch(e) { totalEval = null; }
        if (totalEval?.logForCLV) allEdges.push({ market: 'TOTAL', ...totalEval });

        try { teamTotals = evalTeamTotals(projectedTotal, modelProb?.away ?? null, awaySavant, homeSavant, awayBullpen, homeBullpen); } catch(e) { teamTotals = null; }
        // ── Opener gate logic (Rule 24) ──────────────────────────────────────
        const awayIsOpener = openerFlags[g.away.pitcher?.id] || false;
        const homeIsOpener = openerFlags[g.home.pitcher?.id] || false;
        const awaySplit    = firstInningSplits[g.away.pitcher?.id] || null;
        const homeSplit    = firstInningSplits[g.home.pitcher?.id] || null;

        // Helper: is an opener qualified (has 5+ appearance Savant data)?
        function openerQualified(isOpener, split) {
          if (!isOpener) return true; // not an opener — no gate needed
          return split?.openerQualified === true;
        }

        const awayOpenerOK = openerQualified(awayIsOpener, awaySplit);
        const homeOpenerOK = openerQualified(homeIsOpener, homeSplit);

        // F5: skip entirely if either opener is unqualified
        const f5Blocked = (awayIsOpener && !awayOpenerOK) || (homeIsOpener && !homeOpenerOK);

        // NRFI/YRFI: if opener unqualified, force YRFI lean (Rule 24 + MODEL_CORE)
        const nrfiForceYRFI = (awayIsOpener && !awayOpenerOK) || (homeIsOpener && !homeOpenerOK);

        // Attach opener context to savant data so downstream analysis has it
        if (awayIsOpener && awaySavant) {
          awaySavant.openerRole     = true;
          awaySavant.avgIPperStart  = ipPerStart[g.away.pitcher?.id];
          awaySavant.firstInningSplit = awaySplit;
          awaySavant.openerQualified  = awayOpenerOK;
        }
        if (homeIsOpener && homeSavant) {
          homeSavant.openerRole     = true;
          homeSavant.avgIPperStart  = ipPerStart[g.home.pitcher?.id];
          homeSavant.firstInningSplit = homeSplit;
          homeSavant.openerQualified  = homeOpenerOK;
        }
        // ── End opener gate ───────────────────────────────────────────────────

        try {
          nrfi = evalNRFI(awaySavant, homeSavant);
          // Force YRFI lean if unqualified opener present
          if (nrfiForceYRFI && nrfi) {
            nrfi.lean         = 'YRFI';
            nrfi.leanStrength = 'LEAN';
            nrfi.openerForced = true;
            nrfi.reasons.push('Opener role detected — no qualified 1st-inning data, defaulting YRFI per Rule 24');
          } else if (nrfiForceYRFI) {
            nrfi = {
              lean: 'YRFI', leanStrength: 'LEAN', openerForced: true,
              nrfiScore: 0, yrfiScore: 1, nrfiPct: 0, yrfiPct: 100,
              reasons: ['Opener role detected — no qualified 1st-inning data, defaulting YRFI per Rule 24']
            };
          }
        } catch(e) { nrfi = null; }

        try {
          f5 = f5Blocked ? {
            blocked: true,
            reason: 'Opener role with insufficient 1st-inning data — F5 unqualified per Rule 24',
            awayIsOpener, homeIsOpener, awaySplit, homeSplit
          } : evalF5(awaySavant, homeSavant, awayStanding, homeStanding);
        } catch(e) { f5 = null; }
      }

      const awayStats = { ...teamStats[g.away.abbr], record: awayStanding };
      const homeStats = { ...teamStats[g.home.abbr], record: homeStanding };

      return {
        ...g,
        away:      { ...g.away, pitcherSavant: awaySavant, bullpen: awayBullpen },
        home:      { ...g.home, pitcherSavant: homeSavant, bullpen: homeBullpen },
        odds:      bookOdds,
        pinVigFree,
        kalshi:    { markets: gameKalshi, ml: kalshiML },
        modelProb, mlEdge, runLineEval, totalEval, teamTotals, nrfi, f5, allEdges,
        awayTeamStats: awayStats,
        homeTeamStats: homeStats
      };
    });

    const result = {
      date: today, kalshiDate,
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
