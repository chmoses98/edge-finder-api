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

  // Convert American odds to implied probability
  function americanToImplied(odds) {
    if (!odds) return null;
    if (odds >= 100) return 100 / (odds + 100);
    return Math.abs(odds) / (Math.abs(odds) + 100);
  }

  // Vig-free implied probability from two-sided market
  function vigFree(homeOdds, awayOdds) {
    const implH = americanToImplied(homeOdds);
    const implA = americanToImplied(awayOdds);
    if (!implH || !implA) return null;
    const total = implH + implA;
    return {
      home: Math.round(implH / total * 1000) / 10,
      away: Math.round(implA / total * 1000) / 10
    };
  }

  // ── MODEL PROBABILITY ──────────────────────────────────────────────────────
  function calcModelProb(g, awaySavant, homeSavant, awayBullpen, homeBullpen,
                          awayStanding, homeStanding, pinVigFree, bookOdds) {
    let awayProb = 0.50;
    const factors = {};

    awayProb -= 0.04;
    factors.homeField = -0.04;

    const awayXERA = awaySavant?.xERA ?? null;
    const homeXERA = homeSavant?.xERA ?? null;
    if (awayXERA !== null && homeXERA !== null) {
      const adj = (homeXERA - awayXERA) * 0.04;
      awayProb += adj;
      factors.starterXERA = Math.round(adj * 1000) / 1000;
    }

    const awayWhiff = awaySavant?.whiffPct ?? null;
    const homeWhiff = homeSavant?.whiffPct ?? null;
    if (awayWhiff !== null && homeWhiff !== null) {
      const adj = (homeWhiff - awayWhiff) * 0.003;
      awayProb += adj;
      factors.starterWhiff = Math.round(adj * 1000) / 1000;
    }

    const awayHH = awaySavant?.hardHitPct ?? null;
    const homeHH = homeSavant?.hardHitPct ?? null;
    if (awayHH !== null && homeHH !== null) {
      const adj = (homeHH - awayHH) * 0.002;
      awayProb += adj;
      factors.starterHardHit = Math.round(adj * 1000) / 1000;
    }

    const awayBPxFIP = awayBullpen?.xFIP ?? null;
    const homeBPxFIP = homeBullpen?.xFIP ?? null;
    if (awayBPxFIP !== null && homeBPxFIP !== null) {
      const weight = awaySavant?.highWalkRisk ? 0.03 : 0.02;
      const adj = (homeBPxFIP - awayBPxFIP) * weight;
      awayProb += adj;
      factors.bullpen = Math.round(adj * 1000) / 1000;
    }

    const awayRD = awayStanding?.runDiff ?? null;
    const homeRD = homeStanding?.runDiff ?? null;
    if (awayRD !== null && homeRD !== null) {
      const adj = (awayRD - homeRD) / 1000;
      awayProb += adj;
      factors.runDiff = Math.round(adj * 1000) / 1000;
    }

    const awayStreak = Math.max(-5, Math.min(5, parseStreak(awayStanding?.streak)));
    const homeStreak = Math.max(-5, Math.min(5, parseStreak(homeStanding?.streak)));
    const streakAdj = (awayStreak - homeStreak) * 0.005;
    awayProb += streakAdj;
    factors.streak = Math.round(streakAdj * 1000) / 1000;

    const parkFactor = PARK_WEATHER[g.home.abbr]?.parkFactor ?? 100;
    const parkAdj = (parkFactor - 100) * 0.001;
    awayProb += parkAdj;
    factors.parkFactor = Math.round(parkAdj * 1000) / 1000;

    const pinnacleTotal = bookOdds?.pinnacle?.total?.point ?? null;
    if (pinnacleTotal !== null && pinVigFree !== null) {
      const totalAdj = (pinnacleTotal - 8.5) * 0.002;
      awayProb += totalAdj;
      factors.vegasTotal = Math.round(totalAdj * 1000) / 1000;
    }

    awayProb = Math.max(0.15, Math.min(0.85, awayProb));
    const homeProb = 1 - awayProb;

    const hasBothSavant  = awaySavant  !== null && homeSavant  !== null;
    const hasBothBullpen = awayBullpen !== null && homeBullpen !== null;
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

  // ── PROJECTED RUN TOTAL ────────────────────────────────────────────────────
  // Estimates expected runs using pitcher quality + park factor + Vegas anchor
  function projectRunTotal(awaySavant, homeSavant, awayBullpen, homeBullpen,
                            parkFactor, vegasTotal) {
    // Start with Vegas total as anchor (most reliable signal)
    let projected = vegasTotal ?? 8.5;

    // Adjust for pitcher strikeout suppression
    // High K pitchers (>25%) suppress scoring by ~0.3 runs each
    const awayK = awaySavant?.kPct ?? null;
    const homeK = homeSavant?.kPct ?? null;
    if (awayK !== null) {
      const suppression = Math.max(0, (awayK - 20) / 100) * 3;
      projected -= suppression;
    }
    if (homeK !== null) {
      const suppression = Math.max(0, (homeK - 20) / 100) * 3;
      projected -= suppression;
    }

    // Park factor adjustment (Coors +1.5, Petco -0.5 etc)
    const parkAdj = (parkFactor - 100) / 100 * 2;
    projected += parkAdj;

    // Bullpen vulnerability adds expected runs in late innings
    const awayBP = awayBullpen?.xFIP ?? null;
    const homeBP = homeBullpen?.xFIP ?? null;
    if (awayBP !== null && awayBP > 4.5) projected += 0.3;
    if (homeBP !== null && homeBP > 4.5) projected += 0.3;

    return Math.round(projected * 10) / 10;
  }

  // ── NRFI / YRFI EVALUATION ─────────────────────────────────────────────────
  function evalNRFI(awaySavant, homeSavant) {
    if (!awaySavant || !homeSavant) return null;

    const awayK   = awaySavant.kPct   ?? null;
    const homeK   = homeSavant.kPct   ?? null;
    const awayBB  = awaySavant.bbPct  ?? null;
    const homeBB  = homeSavant.bbPct  ?? null;
    const awayWh  = awaySavant.whiffPct ?? null;
    const homeWh  = homeSavant.whiffPct ?? null;

    if (awayK === null || homeK === null) return null;

    // NRFI lean: both starters have high K rate + low walk rate
    // YRFI lean: either starter has high walk rate or very low K rate
    let nrfiScore = 0;
    let yrfiScore = 0;
    const reasons = [];

    // K rate signals
    if (awayK >= 25) { nrfiScore += 2; reasons.push(`Away K%: ${awayK}%`); }
    else if (awayK >= 22) { nrfiScore += 1; }
    else if (awayK < 16) { yrfiScore += 2; reasons.push(`Away K% low: ${awayK}%`); }

    if (homeK >= 25) { nrfiScore += 2; reasons.push(`Home K%: ${homeK}%`); }
    else if (homeK >= 22) { nrfiScore += 1; }
    else if (homeK < 16) { yrfiScore += 2; reasons.push(`Home K% low: ${homeK}%`); }

    // Walk rate signals — high BB = more baserunners in inning 1
    if (awayBB !== null) {
      if (awayBB > 10) { yrfiScore += 2; reasons.push(`Away BB% high: ${awayBB}%`); }
      else if (awayBB > 9.2) { yrfiScore += 1; }
      else if (awayBB < 6) { nrfiScore += 1; reasons.push(`Away BB% low: ${awayBB}%`); }
    }

    if (homeBB !== null) {
      if (homeBB > 10) { yrfiScore += 2; reasons.push(`Home BB% high: ${homeBB}%`); }
      else if (homeBB > 9.2) { yrfiScore += 1; }
      else if (homeBB < 6) { nrfiScore += 1; reasons.push(`Home BB% low: ${homeBB}%`); }
    }

    // Whiff rate bonus
    if (awayWh !== null && awayWh >= 30) { nrfiScore += 1; reasons.push(`Away whiff%: ${awayWh}%`); }
    if (homeWh !== null && homeWh >= 30) { nrfiScore += 1; reasons.push(`Home whiff%: ${homeWh}%`); }

    const total = nrfiScore + yrfiScore;
    if (total === 0) return null;

    const nrfiPct = Math.round(nrfiScore / total * 100);
    const yrfiPct = 100 - nrfiPct;

    let lean, leanStrength;
    if (nrfiScore >= 5 && yrfiScore <= 1) { lean = 'NRFI'; leanStrength = 'STRONG'; }
    else if (nrfiScore > yrfiScore + 1)   { lean = 'NRFI'; leanStrength = 'LEAN'; }
    else if (yrfiScore >= 4 && nrfiScore <= 1) { lean = 'YRFI'; leanStrength = 'STRONG'; }
    else if (yrfiScore > nrfiScore + 1)   { lean = 'YRFI'; leanStrength = 'LEAN'; }
    else                                   { lean = 'NEUTRAL'; leanStrength = 'NEUTRAL'; }

    return { lean, leanStrength, nrfiScore, yrfiScore, nrfiPct, yrfiPct, reasons };
  }

  // ── FIRST 5 INNINGS EVALUATION ─────────────────────────────────────────────
  function evalF5(awaySavant, homeSavant, awayStanding, homeStanding,
                   pinVigFree, bookOdds) {
    if (!awaySavant || !homeSavant) return null;

    const awayXERA = awaySavant.xERA ?? null;
    const homeXERA = homeSavant.xERA ?? null;
    if (awayXERA === null || homeXERA === null) return null;

    // F5 removes bullpen so it's purely starter quality
    // Use same model logic but without bullpen factor
    let awayF5 = 0.50;

    awayF5 -= 0.03; // smaller HFA for F5

    const xeraAdj = (homeXERA - awayXERA) * 0.05; // slightly amplified for F5
    awayF5 += xeraAdj;

    const awayWhiff = awaySavant.whiffPct ?? null;
    const homeWhiff = homeSavant.whiffPct ?? null;
    if (awayWhiff !== null && homeWhiff !== null) {
      awayF5 += (homeWhiff - awayWhiff) * 0.003;
    }

    const awayRD = awayStanding?.runDiff ?? null;
    const homeRD = homeStanding?.runDiff ?? null;
    if (awayRD !== null && homeRD !== null) {
      awayF5 += (awayRD - homeRD) / 1200;
    }

    const awayStreak = Math.max(-5, Math.min(5, parseStreak(awayStanding?.streak)));
    const homeStreak = Math.max(-5, Math.min(5, parseStreak(homeStanding?.streak)));
    awayF5 += (awayStreak - homeStreak) * 0.004;

    awayF5 = Math.max(0.15, Math.min(0.85, awayF5));

    // Compare to Pinnacle F5 if available
    const pinF5 = bookOdds?.pinnacle?.h2h
      ? vigFree(bookOdds.pinnacle.h2h.home, bookOdds.pinnacle.h2h.away)
      : null;

    // F5 model vs full game — if xERA gap is large, F5 is more pronounced
    const xERAGap = Math.abs(awayXERA - homeXERA);
    const f5Amplified = xERAGap > 1.5;

    return {
      awayF5Pct:   Math.round(awayF5 * 1000) / 10,
      homeF5Pct:   Math.round((1 - awayF5) * 1000) / 10,
      xERAGap:     Math.round(xERAGap * 100) / 100,
      f5Amplified, // true = starter gap large enough that F5 has more edge than full game
      favoredSide: awayF5 > 0.52 ? 'AWAY' : awayF5 < 0.48 ? 'HOME' : 'NEUTRAL',
    };
  }

  // ── RUN LINE EVALUATION ────────────────────────────────────────────────────
  function evalRunLine(modelAwayPct, bookOdds) {
    if (modelAwayPct === null) return null;

    const pin = bookOdds?.pinnacle;
    if (!pin) return null;

    // Get run line odds from pinnacle if available
    const rl = pin.runLine ?? null;
    if (!rl) return null;

    const favored = modelAwayPct > 50 ? 'AWAY' : 'HOME';
    const modelWinProb = favored === 'AWAY' ? modelAwayPct / 100 : (100 - modelAwayPct) / 100;

    // Run line covers happen ~65% of the time when ML win prob is ~70%
    // Rough conversion: RL cover prob ≈ ML win prob * 0.85 (win by 2+ is harder)
    const rlCoverProb = modelWinProb * 0.82;
    const rlImplied = favored === 'AWAY'
      ? americanToImplied(rl.away)
      : americanToImplied(rl.home);

    if (!rlImplied) return null;

    const rlEdge = Math.round((rlCoverProb - rlImplied) * 1000) / 10;

    return {
      favored,
      modelCoverPct: Math.round(rlCoverProb * 1000) / 10,
      impliedCoverPct: Math.round(rlImplied * 1000) / 10,
      edge: rlEdge,
      actionable: rlEdge >= 3.0,
      logForCLV: rlEdge >= 1.5,
    };
  }

  // ── GAME TOTAL EVALUATION ──────────────────────────────────────────────────
  function evalGameTotal(projectedTotal, bookOdds, awaySavant, homeSavant) {
    const pin = bookOdds?.pinnacle?.total;
    if (!pin || projectedTotal === null) return null;

    const vegasLine  = pin.point;
    const overImplied  = americanToImplied(pin.over);
    const underImplied = americanToImplied(pin.under);
    if (!overImplied || !underImplied) return null;

    const diff = projectedTotal - vegasLine;

    // Model says Over if projected > line by meaningful margin
    const modelLean = diff > 0.4 ? 'OVER' : diff < -0.4 ? 'UNDER' : 'NEUTRAL';

    // Both high K starters = Under lean per model rules
    const awayK = awaySavant?.kPct ?? 0;
    const homeK = homeSavant?.kPct ?? 0;
    const bothHighK = awayK >= 22 && homeK >= 22;
    const eitherElite = (awaySavant?.eliteStarter || homeSavant?.eliteStarter);

    let adjustedLean = modelLean;
    let leanNote = null;
    if (bothHighK && modelLean === 'OVER') {
      adjustedLean = 'NEUTRAL';
      leanNote = 'Both high-K starters override Over lean per model rules';
    }
    if (eitherElite) {
      adjustedLean = 'UNDER';
      leanNote = 'Elite starter on mound — Under lean per model rules';
    }

    // Edge calc vs vig-free implied
    const vigFreeOver  = overImplied  / (overImplied + underImplied);
    const vigFreeUnder = underImplied / (overImplied + underImplied);
    const modelOverProb  = diff > 0 ? 0.50 + Math.min(diff * 0.04, 0.12) : 0.50 - Math.min(Math.abs(diff) * 0.04, 0.12);
    const modelUnderProb = 1 - modelOverProb;

    const overEdge  = Math.round((modelOverProb  - vigFreeOver)  * 1000) / 10;
    const underEdge = Math.round((modelUnderProb - vigFreeUnder) * 1000) / 10;

    const bestEdge = overEdge > underEdge ? { side: 'OVER', edge: overEdge } : { side: 'UNDER', edge: underEdge };

    return {
      vegasLine,
      projectedTotal,
      diff: Math.round(diff * 10) / 10,
      modelLean: adjustedLean,
      leanNote,
      overEdge,
      underEdge,
      bestSide: bestEdge.side,
      bestEdge: bestEdge.edge,
      actionable: Math.abs(bestEdge.edge) >= 3.0,
      logForCLV: Math.abs(bestEdge.edge) >= 1.5,
    };
  }

  // ── TEAM TOTAL EVALUATION ──────────────────────────────────────────────────
  function evalTeamTotals(projectedTotal, modelAwayPct, awaySavant, homeSavant,
                           awayBullpen, homeBullpen, bookOdds) {
    if (projectedTotal === null) return null;

    const modelAway = (modelAwayPct ?? 50) / 100;

    // Split projected total: stronger team scores more
    // Win prob correlates with run scoring ability
    const awayRunShare  = 0.5 + (modelAway - 0.5) * 0.3;
    const homeRunShare  = 1 - awayRunShare;
    const projAwayRuns  = Math.round(projectedTotal * awayRunShare * 10) / 10;
    const projHomeRuns  = Math.round(projectedTotal * homeRunShare * 10) / 10;

    // Opposing pitcher vulnerability check
    // Away offense scores more against a vulnerable home starter
    const homeStarterVuln = homeSavant?.xERA !== null && homeSavant.xERA > 4.5;
    const awayStarterVuln = awaySavant?.xERA !== null && awaySavant.xERA > 4.5;
    const homeBPvuln = homeBullpen?.vulnerable ?? false;
    const awayBPvuln = awayBullpen?.vulnerable ?? false;

    const awayTTOver = homeStarterVuln || homeBPvuln;
    const homeTTOver = awayStarterVuln || awayBPvuln;

    // Get team total lines from odds if available
    const pin = bookOdds?.pinnacle;
    const awayTT = pin?.awayTotal ?? null;
    const homeTT = pin?.homeTotal ?? null;

    return {
      projectedAwayRuns: projAwayRuns,
      projectedHomeRuns: projHomeRuns,
      awayTTLean: awayTTOver ? 'OVER' : 'NEUTRAL',
      homeTTLean: homeTTOver ? 'OVER' : 'NEUTRAL',
      awayTTReason: awayTTOver
        ? (homeStarterVuln ? `Opp starter xERA ${homeSavant?.xERA}` : 'Opp bullpen vulnerable')
        : null,
      homeTTReason: homeTTOver
        ? (awayStarterVuln ? `Opp starter xERA ${awaySavant?.xERA}` : 'Opp bullpen vulnerable')
        : null,
      // Lines if available
      awayTTLine: awayTT,
      homeTTLine: homeTT,
      note: 'Team total lines require alternate markets — check DraftKings/FanDuel manually'
    };
  }

  try {
    const [pitchersRes, oddsRes, kalshiRes, teamStatsRes, standingsRes,
           savantPitcherRes, savantBatterRes, bullpenRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${today}&hydrate=probablePitcher(note),team,linescore`),
      // Added alternate_spreads and alternate_totals for run line + alt total evaluation
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,spreads,totals,alternate_spreads,alternate_totals&oddsFormat=american&bookmakers=pinnacle,draftkings,fanduel,betmgm`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc`),
      fetch(`https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak`),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=pitching&gameType=R&stats=season`)
    ]);

    // ── PARSE SCHEDULE ───────────────────────────────────────────────────────
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

    // ── PARSE ODDS ───────────────────────────────────────────────────────────
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
      return {
        home: home?.price, homePoint: home?.point,
        away: away?.price, awayPoint: away?.point,
        updated: rl.last_update
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
      return Object.entries(lines).map(([pt, odds]) => ({
        point: parseFloat(pt), ...odds
      })).sort((a,b) => a.point - b.point);
    };

    // ── PARSE KALSHI ─────────────────────────────────────────────────────────
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

    // ── PARSE TEAM STATS ──────────────────────────────────────────────────────
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

    // ── PARSE STANDINGS ───────────────────────────────────────────────────────
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

    // ── PARSE SAVANT ──────────────────────────────────────────────────────────
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

    // ── PARSE BULLPENS ────────────────────────────────────────────────────────
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

    // ── ENRICH GAMES ──────────────────────────────────────────────────────────
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
          pinnacle: {
            h2h:       extractH2H(pin, g.home.team, g.away.team),
            total:     extractTotal(pin),
            runLine:   extractRunLine(pin, g.home.team, g.away.team),
            altTotals: extractAltTotals(pin),
          },
          draftkings: {
            h2h:       extractH2H(dk, g.home.team, g.away.team),
            total:     extractTotal(dk),
            runLine:   extractRunLine(dk, g.home.team, g.away.team),
            altTotals: extractAltTotals(dk),
          },
          fanduel: {
            h2h:       extractH2H(fd, g.home.team, g.away.team),
            total:     extractTotal(fd),
            runLine:   extractRunLine(fd, g.home.team, g.away.team),
            altTotals: extractAltTotals(fd),
          },
          betmgm: {
            h2h:       extractH2H(mgm, g.home.team, g.away.team),
            total:     extractTotal(mgm),
            runLine:   extractRunLine(mgm, g.home.team, g.away.team),
            altTotals: extractAltTotals(mgm),
          },
        };
      }

      // Pinnacle vig-free ML
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
      const parkFactor    = g.park?.parkFactor ?? 100;

      let modelProb  = null;
      let mlEdge     = null;
      let runLineEval = null;
      let totalEval  = null;
      let teamTotals = null;
      let nrfi       = null;
      let f5         = null;
      let allEdges   = [];

      if (isScheduled) {
        // Model probability
        modelProb = calcModelProb(
          g, awaySavant, homeSavant, awayBullpen, homeBullpen,
          awayStanding, homeStanding, pinVigFree, bookOdds
        );

        if (kalshiAway !== null) {
          modelProb.vsKalshi = Math.round((modelProb.away - kalshiAway.impliedPct) * 10) / 10;
        }

        // Vegas total
        const vegasTotal = bookOdds?.pinnacle?.total?.point ?? null;

        // Projected run total
        const projectedTotal = projectRunTotal(
          awaySavant, homeSavant, awayBullpen, homeBullpen,
          parkFactor, vegasTotal
        );

        // ML edge vs Kalshi
        if (kalshiAway) {
          const kalAway      = kalshiAway.impliedPct;
          const pinAway      = pinVigFree?.away ?? null;
          const modelEdgeRaw = (modelProb.away - kalAway) / 100;
          const modelEdgeAdj = Math.round(modelEdgeRaw * 0.30 * 1000) / 10;
          const pinGap       = pinAway !== null
            ? Math.round((pinAway - kalAway) * 10) / 10 : null;

          mlEdge = {
            market:           'ML',
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

          if (mlEdge.logForCLV) allEdges.push(mlEdge);
        }

        // Run line
        runLineEval = evalRunLine(modelProb.away, bookOdds);
        if (runLineEval?.logForCLV) {
          allEdges.push({ market: 'RUNLINE', ...runLineEval });
        }

        // Game total
        totalEval = evalGameTotal(projectedTotal, bookOdds, awaySavant, homeSavant);
        if (totalEval?.logForCLV) {
          allEdges.push({ market: 'TOTAL', ...totalEval });
        }

        // Team totals
        teamTotals = evalTeamTotals(
          projectedTotal, modelProb.away,
          awaySavant, homeSavant,
          awayBullpen, homeBullpen, bookOdds
        );

        // NRFI/YRFI
        nrfi = evalNRFI(awaySavant, homeSavant);

        // First 5 innings
        f5 = evalF5(
          awaySavant, homeSavant,
          awayStanding, homeStanding,
          pinVigFree, bookOdds
        );
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
        modelProb,
        mlEdge,
        runLineEval,
        totalEval,
        teamTotals,
        nrfi,
        f5,
        allEdges,   // all edges ≥1.5% across all markets in one array
        awayTeamStats: awayStats,
        homeTeamStats: homeStats
      };
    });

    const result = {
      date:                 today,
      kalshiDate,
      games:                enriched,
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
