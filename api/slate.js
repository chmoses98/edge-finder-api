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

  const MLB_TEAM_ID_MAP = {
    'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,
    'COL':115,'DET':116,'HOU':117,'KC':118,'LAD':119,'WSH':120,'NYM':121,
    'ATH':133,'PIT':134,'SD':135,'SEA':136,'SF':137,'STL':138,'TB':139,
    'TEX':140,'TOR':141,'MIN':142,'PHI':143,'ATL':144,'CWS':145,'MIA':146,
    'NYY':147,'MIL':158,
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

  // ── MLB Stats API with retry + fallback ─────────────────────────────────────
  // statsapi.mlb.com sometimes returns 400/500. Wrap every call so a single
  // endpoint failure cannot crash the entire slate build.
  async function mlbFetch(url, retries = 2) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const r = await fetch(url);
        if (r.ok) return r;
        // 400 errors are permanent — no point retrying
        if (r.status === 400 || r.status === 404) return null;
        // 5xx: retry after short delay
        if (attempt < retries) await new Promise(res => setTimeout(res, 1000 * (attempt + 1)));
      } catch(e) {
        if (attempt < retries) await new Promise(res => setTimeout(res, 1000 * (attempt + 1)));
      }
    }
    return null;
  }

  // ── Schedule: try statsapi; fall back to pitchers endpoint ──────────────────
  // This is the critical fix. Old code called statsapi directly with no fallback;
  // a 400 from that single call would crash the handler before any data was written.
  async function fetchSchedule(date) {
    // Primary: statsapi
    const statsUrl = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${date}&hydrate=probablePitcher(note),team,linescore`;
    const statsRes = await mlbFetch(statsUrl);
    if (statsRes) {
      try {
        const data = await statsRes.json();
        const games = [];
        for (const dt of data.dates || []) {
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
              scheduleSource: 'statsapi',
              away: {
                team:   away?.team?.name,
                abbr:   away?.team?.abbreviation,
                record: `${away?.leagueRecord?.wins}-${away?.leagueRecord?.losses}`,
                pitcher: away?.probablePitcher ? {
                  name: away.probablePitcher.fullName,
                  id:   String(away.probablePitcher.id),
                  note: away.probablePitcher.note || ''
                } : null
              },
              home: {
                team:   home?.team?.name,
                abbr:   homeAbbr,
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
        if (games.length > 0) return { games, source: 'statsapi' };
      } catch(e) { /* fall through to pitchers endpoint */ }
    }

    // Fallback: self-hosted pitchers endpoint (always works — separate fetch path)
    try {
      const pitchersRes = await fetch(
        `https://edge-finder-api.vercel.app/api/pitchers?date=${date}`,
        { headers: { 'Accept': 'application/json' } }
      );
      if (pitchersRes.ok) {
        const data = await pitchersRes.json();
        if (data.games?.length > 0) {
          // Normalize pitchers.json schema → slate schema
          const games = data.games.map(g => {
            const homeAbbr = g.home?.teamAbbr;
            const park = PARK_WEATHER[homeAbbr] || { dome: false, name: g.venue || '', parkFactor: 100 };
            return {
              gameId:         g.gameId,
              status:         g.status || 'Scheduled',
              startTime:      g.startTime,
              venue:          g.venue || '',
              park,
              scheduleSource: 'pitchers_endpoint',
              away: {
                team:    g.away?.team   || '',
                abbr:    g.away?.teamAbbr || '',
                record:  g.away?.record  || '',
                pitcher: g.away?.pitcher ? {
                  name: g.away.pitcher.name,
                  id:   String(g.away.pitcher.id),
                  note: g.away.pitcher.note || ''
                } : null
              },
              home: {
                team:    g.home?.team   || '',
                abbr:    homeAbbr        || '',
                record:  g.home?.record  || '',
                pitcher: g.home?.pitcher ? {
                  name: g.home.pitcher.name,
                  id:   String(g.home.pitcher.id),
                  note: g.home.pitcher.note || ''
                } : null
              }
            };
          });
          return { games, source: 'pitchers_endpoint' };
        }
      }
    } catch(e) { /* both sources failed */ }

    return { games: [], source: 'none' };
  }

  // ── Standings: try statsapi; fall back to teamstats endpoint ────────────────
  async function fetchStandings() {
    const url = `https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason&hydrate=team,record,streak`;
    const r = await mlbFetch(url);
    if (r) {
      try {
        const data = await r.json();
        const standings = {};
        for (const league of (data.records || [])) {
          for (const team of (league.teamRecords || [])) {
            const abbr = team.team?.abbreviation;
            if (!abbr) continue;
            standings[abbr] = {
              wins:         team.wins,
              losses:       team.losses,
              pct:          team.winningPercentage,
              streak:       team.streak?.streakCode,
              runsScored:   team.runsScored,
              runsAllowed:  team.runsAllowed,
              runDiff:      team.runsScored - team.runsAllowed,
              divisionRank: team.divisionRank,
              leagueRank:   team.leagueRank
            };
          }
        }
        if (Object.keys(standings).length > 0) return { standings, source: 'statsapi' };
      } catch(e) { /* fall through */ }
    }

    // Fallback: pull from teamstats endpoint which caches standings independently
    try {
      const tsRes = await fetch('https://edge-finder-api.vercel.app/api/teamstats');
      if (tsRes.ok) {
        const tsData = await tsRes.json();
        const standings = {};
        for (const [abbr, t] of Object.entries(tsData.teams || {})) {
          const rec = t.record || {};
          standings[abbr] = {
            wins:         rec.wins,
            losses:       rec.losses,
            pct:          rec.pct,
            streak:       rec.streak,
            runsScored:   rec.runsScored,
            runsAllowed:  rec.runsAllowed,
            runDiff:      rec.runDiff,
            divisionRank: rec.divisionRank,
            leagueRank:   rec.leagueRank
          };
        }
        if (Object.keys(standings).length > 0) return { standings, source: 'teamstats_endpoint' };
      }
    } catch(e) { /* both failed */ }

    return { standings: {}, source: 'none' };
  }

  // ── Team season hitting stats with robust fallback ───────────────────────────
  async function fetchTeamStats() {
    const url = `https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=hitting&gameType=R&stats=season&order=asc`;
    const r = await mlbFetch(url);
    if (r) {
      try {
        const data = await r.json();
        const teamStats = {};
        for (const rec of (data?.stats?.[0]?.splits || [])) {
          const abbr = rec.team?.abbreviation;
          if (!abbr) continue;
          const s  = rec.stat || {};
          const gp = s.gamesPlayed || 1;
          teamStats[abbr] = {
            teamId:      rec.team?.id,
            gamesPlayed: gp,
            runs:        s.runs,
            hits:        s.hits,
            homeRuns:    s.homeRuns,
            strikeOuts:  s.strikeOuts,
            baseOnBalls: s.baseOnBalls,
            avg: s.avg, obp: s.obp, slg: s.slg, ops: s.ops,
            atBats:      s.atBats,
            runsPerGame: Math.round((s.runs / gp) * 100) / 100,
            wrcPlus:     null,   // computed below from standings
            last7RpG:    null,
            last15RpG:   null,
          };
        }
        if (Object.keys(teamStats).length > 0) return { teamStats, source: 'statsapi' };
      } catch(e) { /* fall through */ }
    }

    // Fallback: pull from teamstats endpoint which has its own hitting stats cache
    try {
      const tsRes = await fetch('https://edge-finder-api.vercel.app/api/teamstats');
      if (tsRes.ok) {
        const tsData = await tsRes.json();
        const teamStats = {};
        for (const [abbr, t] of Object.entries(tsData.teams || {})) {
          teamStats[abbr] = {
            abbr,
            teamId:      MLB_TEAM_ID_MAP[abbr] || null,
            gamesPlayed: (t.record?.wins || 0) + (t.record?.losses || 0),
            runs:        t.record?.runsScored || null,
            avg:  t.avg  || null,
            obp:  t.obp  || null,
            slg:  t.slg  || null,
            ops:  t.ops  || null,
            runsPerGame: t.runsPerGame || null,
            wrcPlus:     t.wrcPlus    || null,
            last7RpG:    t.last7RpG   || null,
            last15RpG:   t.last15RpG  || null,
          };
        }
        if (Object.keys(teamStats).length > 0) return { teamStats, source: 'teamstats_endpoint' };
      }
    } catch(e) { /* both failed */ }

    return { teamStats: {}, source: 'none' };
  }

  // ── Bullpen stats: try statsapi; degrade gracefully on failure ───────────────
  async function fetchBullpens() {
    const url = `https://statsapi.mlb.com/api/v1/teams/stats?season=2026&sportId=1&group=pitching&gameType=R&stats=season`;
    const r = await mlbFetch(url);
    if (!r) return {};
    try {
      const data = await r.json();
      const bullpens = {};
      const leagueHR9 = 1.20;
      for (const rec of (data?.stats?.[0]?.splits || [])) {
        const abbr = MLB_ID_TO_ABBR[rec.team?.id];
        if (!abbr) continue;
        const s   = rec.stat || {};
        const era = pf(s.era);
        const hr9 = pf(s.homeRunsPer9);
        let xFIP  = null;
        if (era !== null && hr9 !== null) {
          xFIP = Math.round((era - (hr9 - leagueHR9) * 1.35) * 100) / 100;
        }
        bullpens[abbr] = {
          era, xFIP,
          whip:        pf(s.whip),
          kPer9:       pf(s.strikeoutsPer9Inn),
          bbPer9:      pf(s.walksPer9Inn),
          hr9,
          elite:       xFIP !== null && xFIP < 3.50,
          vulnerable:  xFIP !== null && xFIP > 4.50,
          last3DaysIP: null,
          fatigued:    false,
        };
      }
      return bullpens;
    } catch(e) { return {}; }
  }

  // ── IP/start for opener detection ────────────────────────────────────────────
  async function fetchIPsForPitcher(pitcherId) {
    try {
      const r = await mlbFetch(
        `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats?stats=gameLog&group=pitching&season=2026&gameType=R&limit=10`
      );
      if (!r) return null;
      const d = await r.json();
      const logs = (d?.stats?.[0]?.splits || []).filter(l => l.stat?.gamesStarted > 0);
      if (!logs.length) return null;
      const totalIP = logs.reduce((sum, l) => {
        const ip = parseFloat(l.stat?.inningsPitched || '0');
        const full = Math.floor(ip); const frac = (ip % 1) / 0.3 * 0.333;
        return sum + full + frac;
      }, 0);
      return Math.round((totalIP / logs.length) * 100) / 100;
    } catch(e) { return null; }
  }

  function calcModelProb(g, awaySavant, homeSavant, awayBullpen, homeBullpen,
                          awayStanding, homeStanding, pinVigFree, bookOdds) {
    let awayProb = 0.50;
    const factors = {};

    awayProb -= 0.04;
    factors.homeField = -0.04;

    // xFIP priority: season xFIP → season xERA → recentFIP (last-5-starts proxy)
    // recentFIP is always available (from statsapi game logs) and is directionally
    // correct for recency weighting. Flag the source for transparency.
    const awayXFIP = safeGet(awaySavant, 'xFIP')
                  ?? safeGet(awaySavant, 'xERA')
                  ?? safeGet(awaySavant, 'recentFIP');
    const homeXFIP = safeGet(homeSavant, 'xFIP')
                  ?? safeGet(homeSavant, 'xERA')
                  ?? safeGet(homeSavant, 'recentFIP');
    const awayXERA = safeGet(awaySavant, 'xERA');
    const homeXERA = safeGet(homeSavant, 'xERA');
    if (awayXFIP !== null && homeXFIP !== null) {
      const adj = (homeXFIP - awayXFIP) * 0.04;
      awayProb += adj;
      factors.starterXFIP = Math.round(adj * 1000) / 1000;
      factors.starterXERA = (awayXERA !== null && homeXERA !== null)
        ? Math.round((homeXERA - awayXERA) * 1000) / 1000 : null;
      // Record which metric was actually used as the xFIP proxy for transparency
      factors.awayPitcherQualitySource = safeGet(awaySavant,'xFIP') != null ? 'xFIP'
        : safeGet(awaySavant,'xERA') != null ? 'xERA' : 'recentFIP';
      factors.homePitcherQualitySource = safeGet(homeSavant,'xFIP') != null ? 'xFIP'
        : safeGet(homeSavant,'xERA') != null ? 'xERA' : 'recentFIP';
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

    const awayXFIP_f5 = safeGet(awaySavant, 'xFIP')
                     ?? safeGet(awaySavant, 'xERA')
                     ?? safeGet(awaySavant, 'recentFIP');
    const homeXFIP_f5 = safeGet(homeSavant, 'xFIP')
                     ?? safeGet(homeSavant, 'xERA')
                     ?? safeGet(homeSavant, 'recentFIP');
    if (awayXFIP_f5 === null || homeXFIP_f5 === null) return null;

    let awayF5 = 0.50;
    awayF5 -= 0.03;

    const xeraAdj = (homeXFIP_f5 - awayXFIP_f5) * 0.05;
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

    const xERAGap = Math.abs(awayXFIP_f5 - homeXFIP_f5);

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
    // ── Phase 1: Fetch schedule + parallel independent sources ─────────────────
    // Schedule is now resilient — falls back to pitchers endpoint if statsapi fails.
    // All other sources are fetched in parallel and degrade individually on failure.
    const [
      scheduleResult,
      oddsRes,
      oddsF5Res,
      kalshiRes,
      savantPitcherRes,
      savantBatterRes,
    ] = await Promise.all([
      fetchSchedule(today),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h,spreads,totals&oddsFormat=american`),
      fetch(`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey=${apiKey}&regions=us&markets=h2h_h1&oddsFormat=american`),
      fetch(`https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,xfip,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
    ]);

    const games = scheduleResult.games;
    const scheduleSource = scheduleResult.source;

    // ── Phase 2: Fetch standings + team stats + bullpens (all with fallbacks) ──
    // These are now separate async functions that each handle their own failures.
    // FIX: standings was previously referenced before it was defined — now fetched
    // before teamStats so wRC+ proxy computation has access to standings data.
    const [standingsResult, teamStatsResult, bullpens] = await Promise.all([
      fetchStandings(),
      fetchTeamStats(),
      fetchBullpens(),
    ]);

    const standings  = standingsResult.standings;
    let teamStats    = teamStatsResult.teamStats;

    // ── Phase 3: wRC+ proxy computation ────────────────────────────────────────
    // Compute wRC+ from standings (runs scored / GP) for any team missing from
    // hitting stats, and inject into teamStats. This also provides the fallback
    // wRC+ when the statsapi hitting endpoint fails entirely.
    const LEAGUE_AVG_RPG = 4.5;
    for (const abbr of Object.keys(standings)) {
      const s  = standings[abbr];
      const gp = (s.wins || 0) + (s.losses || 0);
      const teamRpG = gp > 0 ? s.runsScored / gp : LEAGUE_AVG_RPG;
      const wrcProxy = Math.round((teamRpG / LEAGUE_AVG_RPG) * 100);
      if (teamStats[abbr]) {
        // Only overwrite wrcPlus — don't clobber detailed hitting stats
        if (teamStats[abbr].wrcPlus === null) {
          teamStats[abbr].wrcPlus   = wrcProxy;
          teamStats[abbr].seasonRpG = Math.round(teamRpG * 100) / 100;
        }
      } else {
        // Team missing from season stats entirely — build minimal entry
        teamStats[abbr] = {
          abbr, wrcPlus: wrcProxy,
          seasonRpG: Math.round(teamRpG * 100) / 100,
          last7RpG: null, last15RpG: null,
        };
      }
    }

    // ── Phase 4: Per-pitcher and per-team async enrichment ─────────────────────
    const allPitcherIds    = [];
    const slateTeamAbbrs   = [];
    for (const g of games) {
      if (g.away.pitcher?.id) allPitcherIds.push(g.away.pitcher.id);
      if (g.home.pitcher?.id) allPitcherIds.push(g.home.pitcher.id);
      if (g.away.abbr) slateTeamAbbrs.push(g.away.abbr);
      if (g.home.abbr) slateTeamAbbrs.push(g.home.abbr);
    }
    const uniqueTeamAbbrs = [...new Set(slateTeamAbbrs)];

    const ipPerStart     = {};

    await Promise.all([
      ...allPitcherIds.map(async (id) => {
        ipPerStart[id] = await fetchIPsForPitcher(id);
      }),
      // Rolling R/G is fetched by teamstats endpoint (its own dedicated Vercel function)
      // and read from the teamstats cache in fetchTeamStats() above.
      // No additional fetches needed here — avoids timeout pressure.
    ]);

    // Opener flags
    const openerFlags = {};
    for (const [id, avg] of Object.entries(ipPerStart)) {
      openerFlags[id] = avg !== null && avg < 3.0;
    }

    // First-inning splits for flagged openers
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
      } catch(e) { /* splits unavailable — gate logic handles gracefully */ }
    }

    // ── Phase 5: Savant pitcher/batter leaderboards ────────────────────────────
    const savantPitchers = {};
    const savantBatters  = {};

    if (savantPitcherRes.ok) {
      const rows = parseCSV(await savantPitcherRes.text());
      for (const p of rows) {
        const id = p['player_id'];
        if (!id) continue;
        const bbPct = pf(p['bb_percent']);
        const xERA  = pf(p['xera']);
        const xFIP  = pf(p['xfip'] ?? p['p_xfip']);
        savantPitchers[id] = {
          name:         p['last_name, first_name'] || '',
          kPct:         pf(p['k_percent']),
          bbPct,
          whiffPct:     pf(p['whiff_percent']),
          xERA, xFIP,
          hardHitPct:   pf(p['hard_hit_percent']),
          exitVeloAvg:  pf(p['exit_velocity_avg']),
          barrelPct:    pf(p['barrel_batted_rate']),
          highWalkRisk: bbPct !== null && bbPct > 9.2,
          eliteStarter: xFIP !== null ? xFIP < 2.50 : (xERA !== null && xERA < 2.50),
          xFIPvsXERA:   (xFIP !== null && xERA !== null) ? Math.round((xFIP - xERA) * 100) / 100 : null,
        };
      }
    }

    // Enrich today's starters with avgIP, recentFIP, platoon splits
    if (allPitcherIds.length > 0) {
      try {
        const enrichRes = await fetch(
          `https://edge-finder-api.vercel.app/api/savant?playerIds=${allPitcherIds.join(',')}&year=2026`,
          { headers: { 'User-Agent': 'Mozilla/5.0' } }
        );
        if (enrichRes.ok) {
          const enrichData = await enrichRes.json();
          for (const [id, enriched] of Object.entries(enrichData.pitchers || {})) {
            if (!savantPitchers[id]) {
              // Pitcher wasn't in leaderboard (e.g. <1 IP) — seed from enrichment
              savantPitchers[id] = { name: enriched.name || '' };
            }
            // Always overwrite xFIP/xERA from savant endpoint — it uses correct
            // column name fallbacks that the raw leaderboard CSV parse may miss
            if (enriched.xFIP  != null) savantPitchers[id].xFIP  = enriched.xFIP;
            if (enriched.xERA  != null) savantPitchers[id].xERA  = enriched.xERA;
            savantPitchers[id].seasonFIP     = enriched.seasonFIP     ?? null;
            savantPitchers[id].seasonIP      = enriched.seasonIP      ?? null;
            savantPitchers[id].seasonStarts  = enriched.seasonStarts  ?? null;
            savantPitchers[id].avgIPperStart = enriched.avgIPperStart ?? null;
            savantPitchers[id].recentFIP     = enriched.recentFIP     ?? null;
            savantPitchers[id].startsSampled = enriched.startsSampled ?? null;
            savantPitchers[id].vsLHH         = enriched.vsLHH         ?? null;
            savantPitchers[id].vsRHH         = enriched.vsRHH         ?? null;
            // Recompute derived flags with updated xFIP/xERA
            const xf = savantPitchers[id].xFIP;
            const xe = savantPitchers[id].xERA;
            savantPitchers[id].eliteStarter  = xf != null ? xf < 2.50 : (xe != null && xe < 2.50);
            savantPitchers[id].xFIPvsXERA    = (xf != null && xe != null) ? Math.round((xf - xe) * 100) / 100 : null;
          }
        }
      } catch(e) { /* enrichment failed — base Savant data still usable */ }
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

    // ── Phase 6: Odds + Kalshi ─────────────────────────────────────────────────
    const oddsData = oddsRes.ok ? await oddsRes.json() : [];
    const oddsF5Data = oddsF5Res.ok ? await oddsF5Res.json() : [];
    const remaining = oddsRes.headers.get('x-requests-remaining');

    // Merge F5 markets into main odds data by game id
    if (Array.isArray(oddsData) && Array.isArray(oddsF5Data) && oddsF5Data.length > 0) {
      const f5Map = {};
      for (const game of oddsF5Data) {
        f5Map[game.id] = game.bookmakers || [];
      }
      for (const game of oddsData) {
        const f5Books = f5Map[game.id];
        if (!f5Books) continue;
        for (const bk of game.bookmakers || []) {
          const f5Bk = f5Books.find(b => b.key === bk.key);
          if (f5Bk) {
            bk.markets = [...(bk.markets || []), ...(f5Bk.markets || [])];
          }
        }
      }
    }

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

    const extractF5 = (bk, homeTeam, awayTeam) => {
      if (!bk) return null;
      const f5 = bk.markets?.find(m => m.key === 'h2h_h1');
      if (!f5) return null;
      const home = f5.outcomes?.find(o => o.name === homeTeam);
      const away = f5.outcomes?.find(o => o.name === awayTeam);
      if (!home && !away) return null;
      return {
        home: home?.price ?? null,
        away: away?.price ?? null,
        updated: f5.last_update,
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

    // ── Phase 7: Enrich each game ──────────────────────────────────────────────
    const enriched = games.map(g => {
      const isScheduled = SCHEDULED_STATUSES.includes(g.status) ||
        // pitchers_endpoint reports games as 'Final' if already played today —
        // check startTime instead so future games still get model output
        new Date(g.startTime) > new Date();

      const oddsMatch = Array.isArray(oddsData) ? oddsData.find(o =>
        o.home_team === g.home.team || o.away_team === g.away.team
      ) : null;

       let bookOdds = null;
       if (oddsMatch) {
         const pin = oddsMatch.bookmakers?.find(b => b.key === 'pinnacle');
         const lv  = oddsMatch.bookmakers?.find(b => b.key === 'lowvig');
         const dk  = oddsMatch.bookmakers?.find(b => b.key === 'draftkings');
         const fd  = oddsMatch.bookmakers?.find(b => b.key === 'fanduel');
         const mgm = oddsMatch.bookmakers?.find(b => b.key === 'betmgm');
         // Sharp: Pinnacle (paid tier) -> LowVig (sharpest free-tier proxy) -> DK
         const sharp = pin || lv || dk;
         bookOdds = {
           pinnacle:   pin ? { h2h: extractH2H(pin,g.home.team,g.away.team), total: extractTotal(pin), runLine: extractRunLine(pin,g.home.team,g.away.team), altTotals: extractAltTotals(pin), f5: extractF5(pin,g.home.team,g.away.team) } : null,
           lowvig:     lv  ? { h2h: extractH2H(lv,g.home.team,g.away.team),  total: extractTotal(lv),  runLine: extractRunLine(lv,g.home.team,g.away.team),  altTotals: extractAltTotals(lv),  f5: extractF5(lv,g.home.team,g.away.team)  } : null,
           draftkings: { h2h: extractH2H(dk,g.home.team,g.away.team),  total: extractTotal(dk),  runLine: extractRunLine(dk,g.home.team,g.away.team),  altTotals: extractAltTotals(dk),  f5: extractF5(dk,g.home.team,g.away.team)  },
           fanduel:    { h2h: extractH2H(fd,g.home.team,g.away.team),  total: extractTotal(fd),  runLine: extractRunLine(fd,g.home.team,g.away.team),  altTotals: extractAltTotals(fd),  f5: extractF5(fd,g.home.team,g.away.team)  },
           betmgm:     { h2h: extractH2H(mgm,g.home.team,g.away.team), total: extractTotal(mgm), runLine: extractRunLine(mgm,g.home.team,g.away.team), altTotals: extractAltTotals(mgm), f5: extractF5(mgm,g.home.team,g.away.team) },
           sharpBook:  pin ? "pinnacle" : lv ? "lowvig" : dk ? "draftkings" : null,
           sharp:      { h2h: extractH2H(sharp,g.home.team,g.away.team), total: extractTotal(sharp), runLine: extractRunLine(sharp,g.home.team,g.away.team) },
         };
       }

      let pinVigFree = null;
      if (bookOdds?.sharp?.h2h) {
        const ph = bookOdds.sharp.h2h;
        if (ph.home != null && ph.away != null) {
          const implH = ph.home >= 100 ? 100/(ph.home+100) : Math.abs(ph.home)/(Math.abs(ph.home)+100);
          const implA = ph.away >= 100 ? 100/(ph.away+100) : Math.abs(ph.away)/(Math.abs(ph.away)+100);
          const tot   = implH + implA;
          pinVigFree  = {
            home: Math.round(implH/tot*1000)/10,
            away: Math.round(implA/tot*1000)/10,
            source: bookOdds.sharpBook,
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

        const vegasTotal     = bookOdds?.pinnacle?.total?.point ?? null;
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

        // ── Opener gate logic (Rule 24) ────────────────────────────────────────
        const awayIsOpener = openerFlags[g.away.pitcher?.id] || false;
        const homeIsOpener = openerFlags[g.home.pitcher?.id] || false;
        const awaySplit    = firstInningSplits[g.away.pitcher?.id] || null;
        const homeSplit    = firstInningSplits[g.home.pitcher?.id] || null;

        function openerQualified(isOpener, split) {
          if (!isOpener) return true;
          return split?.openerQualified === true;
        }

        const awayOpenerOK = openerQualified(awayIsOpener, awaySplit);
        const homeOpenerOK = openerQualified(homeIsOpener, homeSplit);
        const f5Blocked    = (awayIsOpener && !awayOpenerOK) || (homeIsOpener && !homeOpenerOK);
        const nrfiForceYRFI = f5Blocked;

        if (awayIsOpener && awaySavant) {
          awaySavant.openerRole       = true;
          awaySavant.avgIPperStart    = ipPerStart[g.away.pitcher?.id];
          awaySavant.firstInningSplit = awaySplit;
          awaySavant.openerQualified  = awayOpenerOK;
        }
        if (homeIsOpener && homeSavant) {
          homeSavant.openerRole       = true;
          homeSavant.avgIPperStart    = ipPerStart[g.home.pitcher?.id];
          homeSavant.firstInningSplit = homeSplit;
          homeSavant.openerQualified  = homeOpenerOK;
        }

        try {
          nrfi = evalNRFI(awaySavant, homeSavant);
          if (nrfiForceYRFI && nrfi) {
            nrfi.lean = 'YRFI'; nrfi.leanStrength = 'LEAN'; nrfi.openerForced = true;
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
          const f5Model = f5Blocked ? {
            blocked: true,
            reason: 'Opener role with insufficient 1st-inning data — F5 unqualified per Rule 24',
            awayIsOpener, homeIsOpener, awaySplit, homeSplit
          } : evalF5(awaySavant, homeSavant, awayStanding, homeStanding);

          // Enrich with actual F5 book prices from The Odds API (h2h_h1 market)
          if (f5Model && !f5Model.blocked && bookOdds) {
            const pinF5 = bookOdds.pinnacle?.f5;
            const lvF5  = bookOdds.lowvig?.f5;
            const dkF5  = bookOdds.draftkings?.f5;
            const fdF5  = bookOdds.fanduel?.f5;

            // Best sharp F5: Pinnacle (paid) -> LowVig -> DK -> FD
            const sharpF5 = pinF5 || lvF5 || dkF5 || fdF5 || null;

            if (sharpF5) {
              const homeImp = sharpF5.home != null
                ? (sharpF5.home >= 100 ? 100/(sharpF5.home+100) : Math.abs(sharpF5.home)/(Math.abs(sharpF5.home)+100))
                : null;
              const awayImp = sharpF5.away != null
                ? (sharpF5.away >= 100 ? 100/(sharpF5.away+100) : Math.abs(sharpF5.away)/(Math.abs(sharpF5.away)+100))
                : null;
              const vigTotal = (homeImp && awayImp) ? homeImp + awayImp : null;
              const pinF5VFaway = (vigTotal && awayImp) ? Math.round(awayImp/vigTotal*1000)/10 : null;
              const pinF5VFhome = (vigTotal && homeImp) ? Math.round(homeImp/vigTotal*1000)/10 : null;

              f5Model.bookF5 = {
                pinnacle:   pinF5  || null,
                draftkings: dkF5   || null,
                fanduel:    fdF5   || null,
                sharpSource: pinF5 ? 'pinnacle' : dkF5 ? 'draftkings' : 'fanduel',
              };
              f5Model.pinF5VigFree = { away: pinF5VFaway, home: pinF5VFhome };

              // Edge vs vig-free sharp F5 line
              if (pinF5VFaway !== null) {
                f5Model.awayF5Edge = Math.round((f5Model.awayF5Pct - pinF5VFaway) * 10) / 10;
                f5Model.homeF5Edge = Math.round((f5Model.homeF5Pct - pinF5VFhome) * 10) / 10;
                f5Model.awayF5Actionable = f5Model.awayF5Edge >= 1.5;
                f5Model.homeF5Actionable = f5Model.homeF5Edge >= 1.5;
              }
            } else {
              f5Model.bookF5 = null;
              f5Model.pinF5VigFree = null;
              f5Model.f5PriceNote = 'F5 market not offered on any book today';
            }
          }
          f5 = f5Model;
        } catch(e) { f5 = null; }
      }

      const awayStats = {
        ...teamStats[g.away.abbr],
        record:    awayStanding,
        wrcPlus:   teamStats[g.away.abbr]?.wrcPlus  ?? null,
        last7RpG:  teamStats[g.away.abbr]?.last7RpG  ?? null,
        last15RpG: teamStats[g.away.abbr]?.last15RpG ?? null,
      };
      const homeStats = {
        ...teamStats[g.home.abbr],
        record:    homeStanding,
        wrcPlus:   teamStats[g.home.abbr]?.wrcPlus  ?? null,
        last7RpG:  teamStats[g.home.abbr]?.last7RpG  ?? null,
        last15RpG: teamStats[g.home.abbr]?.last15RpG ?? null,
      };

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

    const firstMatch = Array.isArray(oddsData) && oddsData.length > 0 ? oddsData[0] : null;
    const firstGame = Array.isArray(oddsData) && oddsData.length > 0 ? oddsData[0] : null;
    const result = {
      date: today, kalshiDate,
      scheduleSource,
      games: enriched,
      allBookmakerKeys: Array.isArray(oddsData) && oddsData.length > 0 ? (oddsData[0].bookmakers||[]).map(b=>b.key) : [],
      requestsRemaining:    remaining,
      kalshiMarketsFound:   parsedKalshi.length,
      savantPitchersLoaded: Object.keys(savantPitchers).length,
      bullpensLoaded:       Object.keys(bullpens).length,
      dataSources: {
        schedule:  scheduleSource,
        standings: standingsResult.source,
        teamStats: teamStatsResult.source,
      }
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
