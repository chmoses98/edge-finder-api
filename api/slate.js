// ── F5 Three-Way Pricing Correction milestone: additive, pure parity
// functions ────────────────────────────────────────────────────────────────
// These mirror lib/research/three_way_projection.py's
// three_way_result_probs() and scripts/build_market_ledger.py's
// vig_free_3way() EXACTLY (same field names, same math, same rounding
// policy) so a cross-language golden-fixture test can prove no drift.
// Deliberately module-level (not nested inside `handler` below).
//
// Probability-Engine Unification: `evalF5` (nested inside `handler`) now
// calls threeWayResultProbs() directly for its win/tie probabilities
// (see evalF5's own comment) instead of the hand-tuned linear heuristic
// it used previously -- these functions are no longer dead code. Only
// F5 was migrated this way; `handler`'s full-game engine
// (`gameProbs`/`calcModelProb`) keeps its own two-way logic (extra-inning
// blend, 72% win cap) unchanged, since full-game win probability is not
// the family this milestone/mission targeted and those two-way-specific
// adjustments must never be reused for a three-outcome market like F5.
//
// Pure: no I/O, no clock reads, no mutation, deterministic given
// deterministic inputs.

import { readFileSync } from 'fs';

// ── Sentinel Single-Source mission (docs/DUPLICATE_LOGIC_INVENTORY.md #2) ──
// Both languages read the same lib/sentinel_constants.json so the sentinel
// value set never needs to be updated in two places. The hardcoded pair
// below is ONLY a fallback for the rare case that file isn't readable in
// this runtime (e.g. not traced into the Vercel serverless bundle) -- kept
// identical to the JSON by tests/test_sentinel_python_js_parity.py, so any
// drift is caught in CI even in the fallback path. This mirrors the same
// intentional-fallback design lib/slate_manager.py already uses for its own
// sentinel check when it can't import the canonical Python validator.
let SENTINEL_AMERICAN_PRICES = new Set([19900, -19900, 100000, -100000]);
let SENTINEL_ABS_THRESHOLD = 19000;
try {
  const _raw = readFileSync(new URL('../lib/sentinel_constants.json', import.meta.url), 'utf8');
  const _parsed = JSON.parse(_raw);
  SENTINEL_AMERICAN_PRICES = new Set(_parsed.SENTINEL_AMERICAN_PRICES);
  SENTINEL_ABS_THRESHOLD = _parsed.SENTINEL_ABS_THRESHOLD;
} catch (_err) {
  // Fall back silently to the hardcoded values above -- preserves current
  // behavior exactly; never let a missing/unreadable constants file break
  // slate.js's response.
}

export function isSentinelPrice(value) {
  return typeof value === 'number' &&
    (SENTINEL_AMERICAN_PRICES.has(value) || Math.abs(value) >= SENTINEL_ABS_THRESHOLD);
}

export function poissonPmfPure(k, lam) {
  if (lam === null || lam === undefined || lam <= 0) {
    return (k === 0 && lam === 0) ? 1.0 : 0.0;
  }
  let logP = -lam + k * Math.log(lam);
  for (let i = 1; i <= k; i++) logP -= Math.log(i);
  return Math.exp(logP);
}

export function threeWayResultProbs(awayProj, homeProj, maxRuns = 40) {
  const away = awayProj === null || awayProj === undefined ? 0.0 : Number(awayProj);
  const home = homeProj === null || homeProj === undefined ? 0.0 : Number(homeProj);

  const awayPmf = [];
  const homePmf = [];
  for (let k = 0; k <= maxRuns; k++) {
    awayPmf.push(poissonPmfPure(k, away));
    homePmf.push(poissonPmfPure(k, home));
  }

  let pAway = 0.0, pTie = 0.0, pHome = 0.0;
  for (let a = 0; a <= maxRuns; a++) {
    const pa = awayPmf[a];
    if (pa === 0.0) continue;
    for (let h = 0; h <= maxRuns; h++) {
      const p = pa * homePmf[h];
      if (p === 0.0) continue;
      if (a > h) pAway += p;
      else if (a === h) pTie += p;
      else pHome += p;
    }
  }

  const rawTotal = pAway + pTie + pHome;
  const truncationMass = Math.max(0.0, 1.0 - rawTotal);

  let awayCorrected = 0.0, tieCorrected = 0.0, homeCorrected = 0.0;
  if (rawTotal > 0) {
    awayCorrected = pAway / rawTotal;
    tieCorrected = pTie / rawTotal;
    homeCorrected = pHome / rawTotal;
  }

  return {
    awayWinProb: awayCorrected,
    tieProb: tieCorrected,
    homeWinProb: homeCorrected,
    truncationMass,
    maxRuns,
    awayProj: away,
    homeProj: home,
  };
}

export function vigFree3Way(awayAmerican, tieAmerican, homeAmerican) {
  const imp = (o) => {
    if (o === null || o === undefined) return null;
    return o < 0 ? Math.abs(o) / (Math.abs(o) + 100) : 100 / (o + 100);
  };
  const ia = imp(awayAmerican), it = imp(tieAmerican), ih = imp(homeAmerican);
  if (ia === null || it === null || ih === null) return [null, null, null];
  const tot = ia + it + ih;
  if (tot === 0) return [null, null, null];
  return [ia / tot, it / tot, ih / tot];
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  // No-cache headers — date-sensitive response must never be served stale
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');
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
    // MLB StatsAPI requires MM/DD/YYYY format; date param arrives as YYYY-MM-DD
    const [yr, mo, dy] = date.split('-');
    const mlbDate = `${mo}/${dy}/${yr}`;
    console.log(`[slate.js] fetchSchedule: requested=${date} mlbDate=${mlbDate}`);
    const statsUrl = `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=${mlbDate}&hydrate=probablePitcher(note),team,linescore`;
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
                  note: away.probablePitcher.note || '',
                  // Starter throwing hand. NOTE: probablePitcher(note) does NOT
                  // actually populate pitchHand on this person sub-object -- this
                  // resolves to null here regardless of game state. Consumed by
                  // lib.research.platoon_context; scripts/fetch_lineups.py backfills
                  // it in data/slate.json from the boxscore endpoint once the
                  // starter is listed there (see resolve_starter_pitch_hand()).
                  // Null (never guessed) if still unavailable at that point either.
                  pitchHand: away.probablePitcher.pitchHand?.code || null
                } : null
              },
              home: {
                team:   home?.team?.name,
                abbr:   homeAbbr,
                record: `${home?.leagueRecord?.wins}-${home?.leagueRecord?.losses}`,
                pitcher: home?.probablePitcher ? {
                  name: home.probablePitcher.fullName,
                  id:   String(home.probablePitcher.id),
                  note: home.probablePitcher.note || '',
                  // See the `away` pitcher above: always null from this endpoint;
                  // backfilled downstream by scripts/fetch_lineups.py from the boxscore.
                  pitchHand: home.probablePitcher.pitchHand?.code || null
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
                  note: g.away.pitcher.note || '',
                  pitchHand: g.away.pitcher.pitchHand || null
                } : null
              },
              home: {
                team:    g.home?.team   || '',
                abbr:    homeAbbr        || '',
                record:  g.home?.record  || '',
                pitcher: g.home?.pitcher ? {
                  name: g.home.pitcher.name,
                  id:   String(g.home.pitcher.id),
                  note: g.home.pitcher.note || '',
                  pitchHand: g.home.pitcher.pitchHand || null
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
        const era  = pf(s.era);
        const hr9  = pf(s.homeRunsPer9);
        const ip_raw = pf(s.inningsPitched);
        const ip   = ip_raw !== null
          ? Math.floor(ip_raw) + (ip_raw % 1) / 0.3 * 0.333
          : null;
        const hr   = pf(s.homeRuns);
        const bb   = pf(s.baseOnBalls);
        const hbp  = pf(s.hitByPitch) ?? 0;
        const k    = pf(s.strikeOuts);
        const FIP_CONST = 3.10;
        let xFIP = null;
        if (hr !== null && bb !== null && k !== null && ip !== null && ip > 0) {
          const rawFIP = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONST;
          xFIP = Math.round(Math.min(6.0, Math.max(2.5, rawFIP)) * 100) / 100;
        } else if (era !== null) {
          const approx = hr9 !== null ? era - (hr9 - leagueHR9) * 1.35 : era;
          xFIP = Math.round(Math.min(6.0, Math.max(2.5, approx)) * 100) / 100;
        }
        bullpens[abbr] = {
          era, xFIP,
          whip:        pf(s.whip),
          kPer9:       pf(s.strikeoutsPer9Inn),
          bbPer9:      pf(s.walksPer9Inn),
          hr9, ip,
          xFIPMethod: (hr !== null && bb !== null && k !== null && ip !== null) ? 'real_xFIP' : 'era_approx',
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

  // ── POISSON ENGINE (MODEL_CORE Section 1 / Section 6b) ──────────────────────
  // Ground-up run projection → win probability. This replaces the old linear
  // adjustment model. All probabilities are derived from first principles.

  function poissonPMF(k, lam) {
    // P(X=k) for Poisson(lam). Probability-Engine Unification: delegates
    // to the module-level poissonPmfPure() (identical formula, verified
    // byte-for-byte -- see tests/test_f5_python_js_parity.py) instead of
    // maintaining a second copy of the same log-space computation.
    return poissonPmfPure(k, lam);
  }

  function gameProbs(awayProj, homeProj, maxRuns = 20) {
    // Returns { awayWin, homeWin, push } as fractions (0–1).
    // awayWin/homeWin are EXCLUSIVE of push (net win probability).
    let pAwayRaw = 0, pHomeRaw = 0, pPush = 0;
    for (let a = 0; a <= maxRuns; a++) {
      const pA = poissonPMF(a, awayProj);
      for (let h = 0; h <= maxRuns; h++) {
        const p = pA * poissonPMF(h, homeProj);
        if (a > h) pAwayRaw += p;
        else if (a < h) pHomeRaw += p;
        else pPush += p;
      }
    }
    const notPush = 1 - pPush;
    let awayWin = notPush > 0 ? pAwayRaw / notPush : 0.5;
    let homeWin = notPush > 0 ? pHomeRaw / notPush : 0.5;

    // FIX: Extra-inning blend for close projected games.
    // When the projected run margin is < 1.5 runs, ~10% of games go to extras
    // where the outcome is essentially 50/50. Blend in that randomness.
    const projMargin = Math.abs(awayProj - homeProj);
    if (projMargin < 1.5) {
      const extraInningWeight = 0.10;
      awayWin = awayWin * (1 - extraInningWeight) + 0.50 * extraInningWeight;
      homeWin = homeWin * (1 - extraInningWeight) + 0.50 * extraInningWeight;
    }

    // FIX: Hard cap at 72%. No MLB team wins >72% of individual games
    // regardless of matchup. Model outputs above this are fantasy numbers.
    const WIN_PROB_CAP = 0.72;
    if (awayWin > WIN_PROB_CAP) {
      const excess = awayWin - WIN_PROB_CAP;
      awayWin = WIN_PROB_CAP;
      homeWin = Math.min(homeWin + excess, WIN_PROB_CAP);
    }
    if (homeWin > WIN_PROB_CAP) {
      const excess = homeWin - WIN_PROB_CAP;
      homeWin = WIN_PROB_CAP;
      awayWin = Math.min(awayWin + excess, WIN_PROB_CAP);
    }

    return {
      awayWin,
      homeWin,
      push:    pPush,
      awayWinRaw: pAwayRaw,
      homeWinRaw: pHomeRaw,
    };
  }

  function totalProb(totalProj, line, maxRuns = 35) {
    // P(combined runs > line) using Poisson(totalProj).
    let pOver = 0;
    const threshold = Math.floor(line) + 1;
    for (let r = threshold; r <= maxRuns; r++) pOver += poissonPMF(r, totalProj);
    return pOver;
  }

  function teamTotalProb(proj, line, maxRuns = 20) {
    // P(team scores > line) using Poisson(proj).
    let pOver = 0;
    const threshold = Math.floor(line) + 1;
    for (let r = threshold; r <= maxRuns; r++) pOver += poissonPMF(r, proj);
    return pOver;
  }

  // MODEL_CORE Section 1 xFIP tier → run scalar (dampened, centered on 4.5)
  function xfipToScalar(xfip) {
    // Maps true_xFIP to a run-scaling factor relative to league average (1.0 = average)
    // Derived from dampened scalar table in MODEL_CORE Section 1
    if (xfip === null || xfip === undefined) return 1.0;
    // Linear interpolation across tiers: xFIP 2.5→0.69 scalar, 4.5→1.00, 6.0→1.20
    if (xfip <= 2.50) return 0.69;
    if (xfip <= 3.00) return 0.69 + (xfip - 2.50) / 0.50 * (0.77 - 0.69);
    if (xfip <= 3.50) return 0.77 + (xfip - 3.00) / 0.50 * (0.84 - 0.77);
    if (xfip <= 4.00) return 0.84 + (xfip - 3.50) / 0.50 * (0.92 - 0.84);
    if (xfip <= 4.50) return 0.92 + (xfip - 4.00) / 0.50 * (1.00 - 0.92);
    if (xfip <= 5.00) return 1.00 + (xfip - 4.50) / 0.50 * (1.08 - 1.00);
    if (xfip <= 5.50) return 1.08 + (xfip - 5.00) / 0.50 * (1.16 - 1.08);
    return 1.16 + (xfip - 5.50) * 0.08; // replacement-level and beyond
  }

  function bullpenScalar(xfip) {
    // Bullpen dampened scalar — same table logic as starter
    if (xfip === null || xfip === undefined) return 1.0;
    if (xfip < 3.50) return 0.80;
    if (xfip < 4.20) return 0.92;
    if (xfip < 4.80) return 1.04;
    return 1.14;
  }

  // Park run adjustment per team (additive, MODEL_CORE Section 5)
  const PARK_RUN_ADJ = {
    COL: +0.75, CIN: +0.35, TEX: +0.25, ARI: +0.25, BAL: +0.20,
    SF: -0.30, SD: -0.25, LAD: -0.20, KC: -0.20, CWS: -0.15,
  };

  function projectRuns(offenseSavant, offenseTeamStats, pitcherSavant, bullpenData,
                        parkAdj, isHome) {
    // MODEL_CORE Section 6a: project runs for one team
    // offense_baseline from rolling R/G + wRC+-implied blend
    const LEAGUE_AVG = 4.5;

    const rolling15 = offenseTeamStats?.last15RpG ?? null;
    const seasonRpG = offenseTeamStats?.runsPerGame ?? offenseTeamStats?.seasonRpG ?? null;
    const wrcPlus   = offenseTeamStats?.wrcPlus ?? 100;

    // wRC+-implied R/G
    const wrcImplied = LEAGUE_AVG * (1.0 + (wrcPlus / 100 - 1.0) * 0.70);

    // FIX: Three-way blend with Bayesian shrinkage toward league average.
    // Raw blend: L7×0.30 + L15×0.30 + Szn×0.40 (MODEL_CORE v2.5)
    // Then shrink toward LEAGUE_AVG using M=20 equivalent games.
    // This prevents hot-streak inflation and cold-streak overpenalization.
    const last7RpG = offenseTeamStats?.last7RpG ?? null;
    let rawBaseline;
    if (rolling15 !== null && last7RpG !== null && seasonRpG !== null) {
      rawBaseline = last7RpG * 0.30 + rolling15 * 0.30 + seasonRpG * 0.40;
    } else if (rolling15 !== null && seasonRpG !== null) {
      rawBaseline = rolling15 * 0.55 + seasonRpG * 0.45;
    } else if (rolling15 !== null) {
      rawBaseline = rolling15 * 0.55 + wrcImplied * 0.45;
    } else if (seasonRpG !== null) {
      rawBaseline = seasonRpG * 0.55 + wrcImplied * 0.45;
    } else {
      rawBaseline = wrcImplied;
    }
    // Shrinkage: blend rawBaseline with league average using M=20 anchor games.
    // Equivalent to: (N_games × raw + 20 × 4.5) / (N_games + 20)
    // With ~15 games of rolling data, effective weight on league avg ≈ 57%.
    // Prevents extreme baselines from hot/cold streaks.
    const SHRINK_M = 20;
    const N_GAMES_APPROX = 15; // conservative — rolling window
    const offenseBaseline = (N_GAMES_APPROX * rawBaseline + SHRINK_M * LEAGUE_AVG) / (N_GAMES_APPROX + SHRINK_M);

    // UPGRADE 2: Confirmed lineup wOBA delta adjustment.
    // If today's confirmed lineup wOBA differs from team season avg,
    // scale offense baseline proportionally. Set in enrich_data.py.
    const lineupWOBADelta = offenseTeamStats?.lineupWOBADelta ?? null;
    const adjBaseline = lineupWOBADelta !== null
      ? offenseBaseline * (1 + lineupWOBADelta)
      : offenseBaseline;

    // offense_matchup_factor (relative to league avg)
    const offMatchup = adjBaseline / LEAGUE_AVG;

    // True xFIP: prefer xFIP → xERA → recentFIP
    const trueXFIPRaw = safeGet(pitcherSavant, 'xFIP')
                  ?? safeGet(pitcherSavant, 'xERA')
                  ?? safeGet(pitcherSavant, 'recentFIP')
                  ?? 4.50;
    // FIX: Clamp xFIP to realistic MLB range (2.80–5.50).
    // Sub-2.80 and above-5.50 produce fantasy win probabilities.
    let trueXFIP = Math.min(5.50, Math.max(2.80, trueXFIPRaw));

    // UPGRADE 3: Velocity trend degradation.
    // velocityRecent = avg FB velo last 3 starts; velocitySeason = season avg.
    // Drop >= 1.0 mph → add 0.20 per mph to xFIP (cap +0.40).
    const velocityRecent = safeGet(pitcherSavant, 'velocityRecent');
    const velocitySeason = safeGet(pitcherSavant, 'velocitySeason');
    if (velocityRecent !== null && velocitySeason !== null) {
      const velDrop = velocitySeason - velocityRecent;
      if (velDrop >= 1.0) {
        const velPenalty = Math.min(0.40, velDrop * 0.20);
        trueXFIP = Math.min(5.50, trueXFIP + velPenalty);
      }
    }

    const avgIP = safeGet(pitcherSavant, 'avgIPperStart') ?? 5.5;
    const starterIP = Math.min(avgIP, 9.0);
    const bullpenIP = Math.max(0, 9.0 - starterIP);

    const starterRperInn = trueXFIP / 9;
    const bpXFIP = safeGet(bullpenData, 'xFIP') ?? 4.20;
    const bpRperInn = bpXFIP / 9;

    // Home field: offense gets a small boost at home (MODEL_CORE implicit)
    const homeAdj = isHome ? 0.05 : 0.0;

    const projRuns = offMatchup * (
      starterIP * starterRperInn + bullpenIP * bpRperInn
    ) + parkAdj + homeAdj;

    // FIX: Clamp per-team run projection to realistic MLB range.
    // No team projects below 2.5 or above 7.0 runs/game in a real game.
    const projRunsClamped = Math.min(7.0, Math.max(2.5, projRuns));
    return {
      projRuns:       Math.round(projRunsClamped * 100) / 100,
      offenseBaseline: Math.round(offenseBaseline * 100) / 100,
      offMatchup:     Math.round(offMatchup * 100) / 100,
      trueXFIP:       Math.round(trueXFIP * 100) / 100,
      starterIP:      Math.round(starterIP * 10) / 10,
      bullpenIP:      Math.round(bullpenIP * 10) / 10,
      wrcPlus, rolling15, seasonRpG,
      xfipSource: safeGet(pitcherSavant,'xFIP') != null ? 'xFIP'
        : safeGet(pitcherSavant,'xERA') != null ? 'xERA' : 'recentFIP',
    };
  }

  function projectF5Runs(offenseTeamStats, pitcherSavant, parkAdj) {
    // MODEL_CORE Section 7: F5 projection using 5/8.5 ratio × durability
    // Bullpen excluded from F5 — starter only.
    const LEAGUE_AVG = 4.5;
    const rolling15 = offenseTeamStats?.last15RpG ?? null;
    const wrcPlus   = offenseTeamStats?.wrcPlus ?? 100;
    const wrcImplied = LEAGUE_AVG * (1.0 + (wrcPlus / 100 - 1.0) * 0.70);
    let offenseBaseline = rolling15 !== null
      ? rolling15 * 0.55 + wrcImplied * 0.45
      : wrcImplied;
    // FIX: Apply same Bayesian shrinkage as full-game baseline
    const SHRINK_M_F5 = 20;
    const N_GAMES_F5 = 15;
    const offenseBaselineAnchored = (N_GAMES_F5 * offenseBaseline + SHRINK_M_F5 * LEAGUE_AVG) / (N_GAMES_F5 + SHRINK_M_F5);
    const f5LineupDelta = offenseTeamStats?.lineupWOBADelta ?? null;
    const f5AdjBaseline = f5LineupDelta !== null
      ? offenseBaselineAnchored * (1 + f5LineupDelta)
      : offenseBaselineAnchored;
    const offMatchup = f5AdjBaseline / LEAGUE_AVG;

    const trueXFIPRaw = safeGet(pitcherSavant, 'xFIP')
                  ?? safeGet(pitcherSavant, 'xERA')
                  ?? safeGet(pitcherSavant, 'recentFIP')
                  ?? 4.50;
    // FIX: Same xFIP clamp as full-game projection
    let trueXFIP = Math.min(5.50, Math.max(2.80, trueXFIPRaw));
    // UPGRADE 3: Same velocity degradation for F5
    const f5VelRecent = safeGet(pitcherSavant, 'velocityRecent');
    const f5VelSeason = safeGet(pitcherSavant, 'velocitySeason');
    if (f5VelRecent !== null && f5VelSeason !== null) {
      const f5VelDrop = f5VelSeason - f5VelRecent;
      if (f5VelDrop >= 1.0) {
        trueXFIP = Math.min(5.50, trueXFIP + Math.min(0.40, f5VelDrop * 0.20));
      }
    }
    const avgIP = safeGet(pitcherSavant, 'avgIPperStart') ?? 5.5;
    const durability = Math.min(avgIP / 5.0, 1.0);
    const effectiveIP = Math.min(avgIP, 5.0) * durability;
    const starterRperInn = trueXFIP / 9;

    // 5/8.5 ratio for park adj in F5 context (Rule 56)
    const f5ParkAdj = parkAdj * (5 / 8.5);

    const proj = offMatchup * effectiveIP * starterRperInn + f5ParkAdj;
    // FIX: F5 floor = 1.2 runs (5/9 × 2.5 full-game floor), ceiling = 4.1 (5/9 × 7.0)
    return Math.min(4.1, Math.max(1.2, Math.round(proj * 100) / 100));
  }

  function calcModelProb(g, awaySavant, homeSavant, awayBullpen, homeBullpen,
                          awayStanding, homeStanding, pinVigFree, bookOdds,
                          awayTeamStats, homeTeamStats) {

    const homeAbbr = g.home?.abbr ?? '';
    const parkRunAdj = PARK_RUN_ADJ[homeAbbr] ?? 0;

    // Project runs for each team
    const awayProj = projectRuns(awaySavant, awayTeamStats, homeSavant, homeBullpen, parkRunAdj, false);
    const homeProj = projectRuns(homeSavant, homeTeamStats, awaySavant, awayBullpen, parkRunAdj, true);

    // Poisson win probabilities
    const probs = gameProbs(awayProj.projRuns, homeProj.projRuns);

    const awayXERA = safeGet(awaySavant, 'xERA');
    const homeXERA = safeGet(homeSavant, 'xERA');
    const xERAGap  = (awayXERA !== null && homeXERA !== null) ? Math.abs(awayXERA - homeXERA) : 0;

    const hasBothSavant  = awaySavant  != null && homeSavant  != null;
    const hasBothBullpen = awayBullpen != null && homeBullpen != null;

    let confidence;
    if      (hasBothSavant && hasBothBullpen && xERAGap > 1.0) confidence = 'HIGH';
    else if (hasBothSavant && hasBothBullpen)                   confidence = 'MEDIUM';
    else if (hasBothSavant || hasBothBullpen)                   confidence = 'LOW';
    else                                                         confidence = 'INSUFFICIENT';

    const vsPin = pinVigFree
      ? Math.round((probs.awayWin * 100 - pinVigFree.away) * 10) / 10
      : null;

    return {
      away:       Math.round(probs.awayWin * 1000) / 10,
      home:       Math.round(probs.homeWin * 1000) / 10,
      push:       Math.round(probs.push * 1000) / 10,
      awayProjRuns: awayProj.projRuns,
      homeProjRuns: homeProj.projRuns,
      totalProj:    Math.round((awayProj.projRuns + homeProj.projRuns) * 10) / 10,
      awayProjDetail: awayProj,
      homeProjDetail: homeProj,
      confidence,
      factors: {
        awayTrueXFIP:   awayProj.trueXFIP,
        homeTrueXFIP:   homeProj.trueXFIP,
        awayXfipSource: awayProj.xfipSource,
        homeXfipSource: homeProj.xfipSource,
        awayOffBaseline: awayProj.offenseBaseline,
        homeOffBaseline: homeProj.offenseBaseline,
        parkRunAdj,
      },
      vsPin,
      vsKalshi: null,
    };
  }

  function projectRunTotal(awaySavant, homeSavant, awayBullpen, homeBullpen,
                            parkFactor, vegasTotal, awayTeamStats, homeTeamStats) {
    // Use Poisson projections if available; fall back to vegas-anchored estimate
    const homeAbbr = null; // parkFactor passed directly here
    const parkRunAdj = (parkFactor - 100) / 100 * 1.5; // convert index to run adj

    const awayProjDetail = projectRuns(awaySavant, awayTeamStats, homeSavant, awayBullpen, parkRunAdj, false);
    const homeProjDetail = projectRuns(homeSavant, homeTeamStats, awaySavant, homeBullpen, parkRunAdj, true);

    return {
      total: Math.round((awayProjDetail.projRuns + homeProjDetail.projRuns) * 10) / 10,
      awayRuns: awayProjDetail.projRuns,
      homeRuns: homeProjDetail.projRuns,
    };
  }

  // ── Gap 3: YRFI/NRFI generation-time validation ────────────────────────────
  // Disallowed inputs for YRFI/NRFI at generation time:
  //   - bullpen exposure scores, short starter leash flags
  //   - avg innings per start, bullpen weakness/xFIP scores
  //   - "pen arrives by inning X" logic, full-game bullpen fatigue
  //   - generic full-game total (unless explicitly converted to first-inning lambda)
  //
  // evalNRFI() uses ONLY: kPct, bbPct, whiffPct, firstInningSplit.xERA
  // These are pitcher-level stats with established first-inning correlation.
  // Full-game bullpen logic is intentionally excluded.

  const YRFI_DISALLOWED_KEYS = new Set([
    'bullpen_exposure', 'bullpenExposure', 'bullpen_weakness', 'bullpenWeakness',
    'bullpen_xfip', 'bullpenXFIP', 'bullpen_era', 'bullpenERA',
    'short_starter_leash', 'shortStarterLeash', 'early_hook', 'earlyHook',
    'avg_innings_per_start', 'avgInningsPerStart',
    'bullpen_fatigue', 'bullpenFatigue', 'pen_fatigued', 'penFatigued',
    'pen_arrives_inning', 'penArrivesInning', 'full_game_bullpen_fatigue',
  ]);
  const YRFI_DISALLOWED_PHRASES = [
    'bullpen', 'pen arrives', 'short leash', 'average innings per start',
    'avg innings', 'bullpen fatigue', 'relievers', 'by inning 2', 'by inning 3',
  ];

  /**
   * validateYrfiInputs(bet) — generation-time guard for YRFI/NRFI bets.
   * Returns { valid: boolean, blockReasons: string[] }
   * If invalid, the bet must be downgraded to PAPER and blockReason set.
   */
  function validateYrfiInputs(bet) {
    const blockReasons = [];
    const factors = bet.factors || {};
    for (const key of Object.keys(factors)) {
      if (YRFI_DISALLOWED_KEYS.has(key)) {
        blockReasons.push(`Disallowed factor key '${key}' (bullpen/full-game metric)`);
      }
    }
    const reasons = Array.isArray(bet.reasons) ? bet.reasons : [];
    for (const r of reasons) {
      const lower = String(r).toLowerCase();
      for (const phrase of YRFI_DISALLOWED_PHRASES) {
        if (lower.includes(phrase)) {
          blockReasons.push(`Disallowed phrase '${phrase}' in reasons: '${String(r).slice(0,60)}'`);
          break;
        }
      }
    }
    // Check yrfiMeta required fields
    const meta = bet.yrfiMeta || {};
    if (meta.lambdaIsFirstInningSpecific === false) {
      blockReasons.push('lambdaIsFirstInningSpecific=false — lambda not first-inning specific');
    }
    if (meta.poissonCheckFirstInningValid === false) {
      blockReasons.push('poissonCheckFirstInningValid=false — Poisson check not valid for first inning');
    }
    return { valid: blockReasons.length === 0, blockReasons };
  }

  function evalNRFI(awaySavant, homeSavant) {
    if (awaySavant == null || homeSavant == null) return null;

    // ── ONLY first-inning-relevant pitcher inputs used here ───────────────
    // K%, BB%, whiff% are pitcher-level stats with first-inning correlation.
    // firstInningSplit.xERA is the per-pitcher 1st-inning ERA equivalent from Savant.
    // EXCLUDED (never used here):
    //   bullpen exposure, avgIPperStart, bullpen xFIP, short leash flags,
    //   pen arrives inning X, full-game bullpen fatigue, generic full-game total.
    const awayK  = safeGet(awaySavant, 'kPct');
    const homeK  = safeGet(homeSavant, 'kPct');
    const awayBB = safeGet(awaySavant, 'bbPct');
    const homeBB = safeGet(homeSavant, 'bbPct');
    const awayWh = safeGet(awaySavant, 'whiffPct');
    const homeWh = safeGet(homeSavant, 'whiffPct');

    // First-inning split xERA (from Savant split data, 1st-inning specific)
    const awayFI_xERA = awaySavant?.firstInningSplit?.xERA ?? null;
    const homeFI_xERA = homeSavant?.firstInningSplit?.xERA ?? null;
    const hasFirstInningXERA = awayFI_xERA !== null || homeFI_xERA !== null;

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

    // First-inning split xERA scoring (1st-inning specific — allowed)
    if (awayFI_xERA !== null) {
      if (awayFI_xERA > 5.5) { yrfiScore += 1; reasons.push(`Away 1st-inn xERA: ${awayFI_xERA}`); }
      else if (awayFI_xERA < 3.5) { nrfiScore += 1; reasons.push(`Away 1st-inn xERA: ${awayFI_xERA}`); }
    }
    if (homeFI_xERA !== null) {
      if (homeFI_xERA > 5.5) { yrfiScore += 1; reasons.push(`Home 1st-inn xERA: ${homeFI_xERA}`); }
      else if (homeFI_xERA < 3.5) { nrfiScore += 1; reasons.push(`Home 1st-inn xERA: ${homeFI_xERA}`); }
    }

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

    // ── Compute lambda for yrfiMeta ───────────────────────────────────────
    // Lambda is derived from K% and BB% ratios (first-inning relevant).
    // If first-inning split xERA is available, it's used as the primary lambda.
    // Note: ~0.5 runs/inning is the MLB average for the first inning.
    let lambdaUsed = 0.50; // MLB average first-inning run rate per team
    let lambdaFormula = 'mlb_avg_first_inning_rate';
    let lambdaIsFirstInningSpecific = hasFirstInningXERA;
    let lambdaDerivedFromFullGame = false;

    if (awayFI_xERA !== null && homeFI_xERA !== null) {
      // Convert xERA to per-inning rate: xERA is R/9 equivalent → divide by 9
      const awayLambda = awayFI_xERA / 9;
      const homeLambda = homeFI_xERA / 9;
      lambdaUsed = Math.round((awayLambda + homeLambda) * 1000) / 1000;
      lambdaFormula = 'avg(away_fi_xera/9, home_fi_xera/9)';
      lambdaIsFirstInningSpecific = true;
    } else if (awayK !== null && homeK !== null && awayBB !== null && homeBB !== null) {
      // Proxy lambda from K% and BB% (approximation — not full-game but not 1st-inn specific)
      // K% reduces scoring, BB% increases it — rough calibration to 0.4-0.6 range
      const avgK = (awayK + homeK) / 2;
      const avgBB = (awayBB + homeBB) / 2;
      lambdaUsed = Math.round((0.50 - (avgK - 22) * 0.008 + (avgBB - 8) * 0.015) * 1000) / 1000;
      lambdaUsed = Math.max(0.25, Math.min(0.85, lambdaUsed));
      lambdaFormula = 'proxy_kpct_bbpct';
      lambdaIsFirstInningSpecific = false;  // approximation only
      lambdaDerivedFromFullGame = false;    // K%/BB% are season totals, not per-inning
    }

    // ── Required yrfiMeta output ──────────────────────────────────────────
    const yrfiMeta = {
      lambdaUsed,
      formulaUsed: 'poisson_independence',
      lambdaFormula,
      lambdaIsFirstInningSpecific,
      lambdaDerivedFromFullGame,
      parkFirstInningRateIncluded: false,   // park factor not applied in evalNRFI
      teamFirstInningRatesIncluded: hasFirstInningXERA,
      poissonCheckFirstInningValid: lambdaIsFirstInningSpecific,
    };

    return { lean, leanStrength, nrfiScore, yrfiScore, nrfiPct, yrfiPct, reasons, yrfiMeta };
  }

  function evalF5(awaySavant, homeSavant, awayStanding, homeStanding,
                   awayTeamStats, homeTeamStats, parkRunAdj) {
    if (awaySavant == null || homeSavant == null) return null;

    const awayXFIP_f5 = safeGet(awaySavant, 'xFIP')
                     ?? safeGet(awaySavant, 'xERA')
                     ?? safeGet(awaySavant, 'recentFIP');
    const homeXFIP_f5 = safeGet(homeSavant, 'xFIP')
                     ?? safeGet(homeSavant, 'xERA')
                     ?? safeGet(homeSavant, 'recentFIP');
    if (awayXFIP_f5 === null || homeXFIP_f5 === null) return null;

    // Probability-Engine Unification: F5 win probability now comes from
    // the SAME Poisson run-distribution primitives as the canonical
    // production engine (projectF5Runs mirrors
    // lib/kalshi_period_projections.py's F5 starter-only formula;
    // threeWayResultProbs is the additive, byte-identical-to-Python twin
    // of lib/research/three_way_projection.py's three_way_result_probs --
    // see the module comment above those functions, and
    // tests/test_f5_python_js_parity.py). This replaces the prior
    // hand-tuned linear xFIP/whiff/run-diff/streak heuristic, which never
    // computed a tie probability and diverged materially from the
    // production math for a market family both engines price.
    // Away batters face the HOME starter and vice versa (same
    // offense/opposing-pitcher convention calcModelProb already uses for
    // the full-game projection).
    const awayF5Proj = projectF5Runs(awayTeamStats, homeSavant, parkRunAdj ?? 0);
    const homeF5Proj = projectF5Runs(homeTeamStats, awaySavant, parkRunAdj ?? 0);
    const threeWay = threeWayResultProbs(awayF5Proj, homeF5Proj);

    // Existing consumers compare awayF5Pct/homeF5Pct against a genuinely
    // 2-way sportsbook market (Pinnacle h2h_h1 -- no tradable tie
    // contract there, unlike Kalshi's real F5 market), so the tie mass is
    // renormalized away for that comparison, preserving the existing
    // "sums to 100" contract of these two fields. The raw 3-way tie
    // probability is exposed separately via tieProb.
    const notTie = 1 - threeWay.tieProb;
    const awayF5 = notTie > 0 ? threeWay.awayWinProb / notTie : 0.5;

    const xERAGap = Math.abs(awayXFIP_f5 - homeXFIP_f5);

    return {
      awayF5Pct:   Math.round(awayF5 * 1000) / 10,
      homeF5Pct:   Math.round((1 - awayF5) * 1000) / 10,
      tieProb:     Math.round(threeWay.tieProb * 1000) / 10,
      awayF5Proj, homeF5Proj,
      xERAGap:     Math.round(xERAGap * 100) / 100,
      f5Amplified: xERAGap > 1.5,
      favoredSide: awayF5 > 0.52 ? 'AWAY' : awayF5 < 0.48 ? 'HOME' : 'NEUTRAL',
    };
  }

  // ── CALIBRATION FACTORS (MODEL_CORE Section 3 — updated June 1 2026 v2.2) ──
  // edge = (modelProb - pinnacleVF) * calibration_factor
  // High=0.187, Medium=0.255, Paper=0.18
  // Confidence tier is determined by data quality (hasBothSavant + xERAGap).
  // edgePct output = calibration-adjusted edge in percentage points.
  const CAL = { HIGH: 0.187, MEDIUM: 0.255, PAPER: 0.18 };

  // Dynamic multipliers (MODEL_CORE Section 4 — active June 1 2026)
  const MULTIPLIERS = {
    ML:         1.50,
    'F5 ML':    1.25,
    'TEAM TOTAL': 1.75,
    'RUN LINE': 1.50,
    'K PROP':   1.50,
    YRFI:       1.00,
    NRFI:       1.00,
    TOTAL:      0.00,  // PAPER ONLY — Rule 71 WR<52%
  };

  // Base sizes per confidence tier (MODEL_CORE Section 4)
  const BASE_SIZE = { HIGH: 4, MEDIUM: 3, PAPER: 1 };

  function calcSize(confidence, market) {
    const base = BASE_SIZE[confidence] ?? 1;
    const mult = MULTIPLIERS[market] ?? 1.0;
    if (mult === 0.00) return 1; // paper-only market
    const raw = base * mult;
    return Math.round(raw * 2) / 2; // round to nearest $0.50
  }

  function calcEdge(modelPct, pinVFpct, confidence) {
    // modelPct and pinVFpct are percentages (e.g. 62.1, 58.5)
    if (modelPct == null || pinVFpct == null) return null;
    const calFactor = CAL[confidence] ?? CAL.MEDIUM;
    const rawGap = (modelPct - pinVFpct) / 100; // as fraction
    return Math.round(rawGap * calFactor * 10000) / 100; // as percentage points
  }

  function evalRunLine(modelProb, bookOdds) {
    // modelProb = full output from calcModelProb
    if (modelProb == null) return null;

    // Use sharp book: prefer pinnacle → lowvig
    const sharpOdds = bookOdds?.pinnacle ?? bookOdds?.lowvig ?? null;
    if (sharpOdds == null) return null;
    const rl = sharpOdds.runLine;
    if (rl == null) return null;

    const awayProj = modelProb.awayProjRuns;
    const homeProj = modelProb.homeProjRuns;

    // Evaluate both sides of the run line
    const results = [];
    for (const side of ['AWAY', 'HOME']) {
      const rlLine = side === 'AWAY' ? safeGet(rl, 'awayPoint') : safeGet(rl, 'homePoint');
      const rlOdds = side === 'AWAY' ? safeGet(rl, 'away') : safeGet(rl, 'home');
      if (rlLine == null || rlOdds == null) continue;

      // P(cover) via Poisson: P(away - home > 1.5) or P(home - away > 1.5)
      let pCover = 0;
      const maxRuns = 20;
      for (let a = 0; a <= maxRuns; a++) {
        for (let h = 0; h <= maxRuns; h++) {
          const p = poissonPMF(a, awayProj) * poissonPMF(h, homeProj);
          const margin = side === 'AWAY' ? (a - h) : (h - a);
          if (margin > Math.abs(rlLine)) pCover += p; // cover by more than the spread
        }
      }

      const bookImplied = americanToImplied(rlOdds);
      if (bookImplied == null) continue;

      // Vig-free the run line: use both sides
      const otherOdds = side === 'AWAY' ? safeGet(rl, 'home') : safeGet(rl, 'away');
      const otherImplied = otherOdds != null ? americanToImplied(otherOdds) : null;
      const vigTotal = otherImplied != null ? bookImplied + otherImplied : null;
      const pinVF = vigTotal != null ? bookImplied / vigTotal * 100 : bookImplied * 100;

      const modelPct = Math.round(pCover * 1000) / 10;
      const confidence = modelProb.confidence;
      const edgePct = calcEdge(modelPct, pinVF, confidence);
      if (edgePct == null) continue;

      const betSize = calcSize(confidence, 'RUN LINE');
      results.push({
        side,
        price:          rlOdds,
        rlLine,
        modelCoverPct:  modelPct,
        pinVFpct:       Math.round(pinVF * 10) / 10,
        edgePct:        Math.round(edgePct * 100) / 100,
        confidence,
        betSize,
        actionable:  edgePct >= 1.5,
        logForCLV:   Math.abs(edgePct) >= 1.0,
      });
    }

    if (results.length === 0) return null;
    // Return best edge side as primary; include both
    results.sort((a, b) => b.edgePct - a.edgePct);
    return { best: results[0], both: results };
  }

  function evalGameTotal(modelProb, bookOdds, awaySavant, homeSavant) {
    if (modelProb == null) return null;
    const totalProj = modelProb.totalProj;
    if (totalProj == null) return null;

    const sharpOdds = bookOdds?.pinnacle ?? bookOdds?.lowvig ?? null;
    const sharpTotal = sharpOdds?.total ?? null;
    if (sharpTotal == null) return null;

    const vegasLine    = safeGet(sharpTotal, 'point');
    const overOdds     = safeGet(sharpTotal, 'over');
    const underOdds    = safeGet(sharpTotal, 'under');
    if (vegasLine == null || overOdds == null || underOdds == null) return null;

    // Poisson P(over) and P(under)
    const pOver  = totalProb(totalProj, vegasLine);
    const pUnder = totalProb(totalProj, vegasLine - 1) > 0
      ? 1 - pOver - poissonPMF(Math.round(vegasLine), totalProj)
      : 1 - pOver;
    const pPush  = Math.max(0, 1 - pOver - pUnder);

    // Vig-free the book line
    const overImp  = americanToImplied(overOdds);
    const underImp = americanToImplied(underOdds);
    if (overImp == null || underImp == null) return null;
    const vigTotal   = overImp + underImp;
    const pinVFover  = overImp  / vigTotal * 100;
    const pinVFunder = underImp / vigTotal * 100;

    const conf = modelProb.confidence;
    const overEdgePct  = calcEdge(pOver  * 100, pinVFover,  conf);
    const underEdgePct = calcEdge(pUnder * 100, pinVFunder, conf);

    // Model Rules — TOTAL market is PAPER ONLY (Rule 71, WR 41%)
    // Force all total bets to paper regardless of edge
    const sizeOver  = 1; // paper
    const sizeUnder = 1; // paper

    const bestSide = (overEdgePct ?? 0) > (underEdgePct ?? 0) ? 'OVER' : 'UNDER';
    const bestEdge = bestSide === 'OVER' ? overEdgePct : underEdgePct;

    return {
      vegasLine,
      totalProj,
      diff:         Math.round((totalProj - vegasLine) * 10) / 10,
      pOver:        Math.round(pOver  * 1000) / 10,
      pUnder:       Math.round(pUnder * 1000) / 10,
      pinVFover:    Math.round(pinVFover  * 10) / 10,
      pinVFunder:   Math.round(pinVFunder * 10) / 10,
      overEdgePct:  overEdgePct  != null ? Math.round(overEdgePct  * 100) / 100 : null,
      underEdgePct: underEdgePct != null ? Math.round(underEdgePct * 100) / 100 : null,
      bestSide,
      bestEdge:     bestEdge != null ? Math.round(bestEdge * 100) / 100 : null,
      // Rule 71: all game totals paper only
      paperOnly:    true,
      paperReason:  'Game Total WR 41% — paper only per Rule 71 until WR≥52% over N≥30',
      sizeOver, sizeUnder,
      actionable:   false, // never actionable — paper only
      logForCLV:    Math.abs(bestEdge ?? 0) >= 1.0,
    };
  }

  function evalTeamTotals(modelProb, awaySavant, homeSavant, awayBullpen, homeBullpen,
                           awayTeamStats, homeTeamStats, bookOdds) {
    if (modelProb == null) return null;
    const awayProjRuns = modelProb.awayProjRuns;
    const homeProjRuns = modelProb.homeProjRuns;

    // Starter vulnerability flags for lean context
    const homeStarterVuln = safeGet(homeSavant, 'xERA') !== null && safeGet(homeSavant, 'xERA') > 4.5;
    const awayStarterVuln = safeGet(awaySavant, 'xERA') !== null && safeGet(awaySavant, 'xERA') > 4.5;
    const homeBPvuln = safeGet(homeBullpen, 'vulnerable') ?? false;
    const awayBPvuln = safeGet(awayBullpen, 'vulnerable') ?? false;

    const awayTTLean = (homeStarterVuln || homeBPvuln) ? 'OVER' : 'NEUTRAL';
    const homeTTLean = (awayStarterVuln || awayBPvuln) ? 'OVER' : 'NEUTRAL';

    // Try to get TT lines from DK (primary for TT per DATA_SOURCES.md)
    // We don't have a dedicated TT market from The Odds API's h2h/spreads/totals pull.
    // Use the projected runs vs a synthetic line (half of game total) for edge direction.
    // When actual TT lines are available they'll be confirmed before logging Medium/High (Rule 44).
    const sharpOdds = bookOdds?.pinnacle ?? bookOdds?.lowvig ?? null;
    const gameTotal = sharpOdds?.total?.point ?? null;

    // Synthetic TT lines as proxies (standard market convention)
    const awayTTLine = gameTotal != null ? Math.round((gameTotal / 2 - 0.25) * 2) / 2 : null;
    const homeTTLine = gameTotal != null ? Math.round((gameTotal / 2 - 0.25) * 2) / 2 : null;

    const conf = modelProb.confidence;

    // Poisson P(team scores over TT line)
    let awayEdgePct = null, homeEdgePct = null;
    let awayPover = null, homePover = null;

    if (awayTTLine != null) {
      awayPover = teamTotalProb(awayProjRuns, awayTTLine);
      // Market implied for TT Over is typically ~52% (vig on -115/-115)
      const marketImplied = 52.0;
      awayEdgePct = calcEdge(awayPover * 100, marketImplied, conf);
    }
    if (homeTTLine != null) {
      homePover = teamTotalProb(homeProjRuns, homeTTLine);
      const marketImplied = 52.0;
      homeEdgePct = calcEdge(homePover * 100, marketImplied, conf);
    }

    const awaySize = awayEdgePct != null ? calcSize(conf, 'TEAM TOTAL') : 1;
    const homeSize = homeEdgePct != null ? calcSize(conf, 'TEAM TOTAL') : 1;

    return {
      projectedAwayRuns: awayProjRuns,
      projectedHomeRuns: homeProjRuns,
      awayTTLine, homeTTLine,
      awayPover:  awayPover != null ? Math.round(awayPover * 1000) / 10 : null,
      homePover:  homePover != null ? Math.round(homePover * 1000) / 10 : null,
      awayTTLean, homeTTLean,
      awayTTReason: awayTTLean === 'OVER'
        ? (homeStarterVuln ? `Opp starter xERA ${safeGet(homeSavant,'xERA')}` : 'Opp bullpen vulnerable')
        : null,
      homeTTReason: homeTTLean === 'OVER'
        ? (awayStarterVuln ? `Opp starter xERA ${safeGet(awaySavant,'xERA')}` : 'Opp bullpen vulnerable')
        : null,
      awayEdgePct: awayEdgePct != null ? Math.round(awayEdgePct * 100) / 100 : null,
      homeEdgePct: homeEdgePct != null ? Math.round(homeEdgePct * 100) / 100 : null,
      awaySize, homeSize,
      awayActionable: (awayEdgePct ?? 0) >= 1.5 && awayTTLean === 'OVER',
      homeActionable: (homeEdgePct ?? 0) >= 1.5 && homeTTLean === 'OVER',
      lineNote: 'TT line estimated from game total — confirm actual DK/FD line before logging Medium/High (Rule 44)',
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
      fetch(`https://baseballsavant.mlb.com/leaderboard/custom?year=2026&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,xfip,exit_velocity_avg,barrel_batted_rate,fb_percent&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
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

    // First-inning splits (dedicated Statcast 1st-inning xERA, hfInn=1).
    // Previously fetched ONLY for flagged openers (avgIP < 3.0, Rule 24's
    // opener-qualification gate) -- every other starter's
    // pitcherSavant.firstInningSplit was left null, which meant
    // build_market_ledger.py's NRFI/YRFI Rule 40 four-factor composite was
    // incomplete (and PAPER-capped) on essentially every non-opener start.
    // Fetched for every confirmed starter now (allPitcherIds, not just
    // flaggedIds) so lib.research.first_inning_context has real dedicated
    // first-inning evidence to blend in for normal starts too, not just
    // openers. flaggedIds/openerFlags themselves are unchanged -- Rule 24's
    // own opener-gate logic below still keys off openerFlags exactly as
    // before.
    const flaggedIds = Object.entries(openerFlags)
      .filter(([, flagged]) => flagged)
      .map(([id]) => id);

    const firstInningSplits = {};
    const firstInningIds = [...new Set(allPitcherIds)];
    if (firstInningIds.length) {
      try {
        const splitRes = await fetch(
          `https://edge-finder-api.vercel.app/api/savant?splits=true&playerIds=${firstInningIds.join(',')}&year=2026`
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
          fbPct:        pf(p['fb_percent']),
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

      // Build team stats before model so they're available to run projection
      const awayStats = {
        ...teamStats[g.away.abbr],
        record:    awayStanding,
        wrcPlus:   teamStats[g.away.abbr]?.wrcPlus   ?? null,
        last7RpG:  teamStats[g.away.abbr]?.last7RpG  ?? null,
        last15RpG: teamStats[g.away.abbr]?.last15RpG ?? null,
        seasonRpG: teamStats[g.away.abbr]?.runsPerGame ?? teamStats[g.away.abbr]?.seasonRpG ?? null,
      };
      const homeStats = {
        ...teamStats[g.home.abbr],
        record:    homeStanding,
        wrcPlus:   teamStats[g.home.abbr]?.wrcPlus   ?? null,
        last7RpG:  teamStats[g.home.abbr]?.last7RpG  ?? null,
        last15RpG: teamStats[g.home.abbr]?.last15RpG ?? null,
        seasonRpG: teamStats[g.home.abbr]?.runsPerGame ?? teamStats[g.home.abbr]?.seasonRpG ?? null,
      };

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
            awayStanding, homeStanding, pinVigFree, bookOdds,
            awayStats, homeStats
          );
        } catch(e) { modelProb = null; }

        if (modelProb && kalshiAway) {
          modelProb.vsKalshi = Math.round((modelProb.away - kalshiAway.impliedPct) * 10) / 10;
        }

        const vegasTotal     = bookOdds?.pinnacle?.total?.point ?? bookOdds?.lowvig?.total?.point ?? null;
        const projectedTotal = modelProb?.totalProj ?? null;

        // ── ML Edge (vs Pinnacle VF — primary market comparison per MODEL_CORE) ─
        if (modelProb && pinVigFree) {
          try {
            const pinAway      = pinVigFree.away;
            const pinHome      = pinVigFree.home;
            const conf         = modelProb.confidence;

            // Edge for both sides vs Pinnacle VF, apply calibration factor
            const awayEdgePct = calcEdge(modelProb.away, pinAway, conf);
            const homeEdgePct = calcEdge(modelProb.home, pinHome, conf);

            // Best side
            const betSideAway = (awayEdgePct ?? -99) >= (homeEdgePct ?? -99);
            const betEdgePct  = betSideAway ? awayEdgePct : homeEdgePct;
            const betModelPct = betSideAway ? modelProb.away : modelProb.home;
            const betPinVF    = betSideAway ? pinAway : pinHome;
            const betTeam     = betSideAway ? g.away.team : g.home.team;
            const betSize     = calcSize(conf, 'ML');

            // Kalshi as tertiary comparison only (Rule 9, Rule 37, Rule 59)
            const kalAway = kalshiAway?.impliedPct ?? null;
            const pinGap  = kalAway !== null ? Math.round((pinAway - kalAway) * 10) / 10 : null;

            mlEdge = {
              market:       'ML',
              betTeam,
              betSide:      betSideAway ? 'AWAY' : 'HOME',
              modelAwayPct: modelProb.away,
              modelHomePct: modelProb.home,
              pinVfAway:    pinAway,
              pinVfHome:    pinHome,
              kalshiAway:   kalAway,
              awayEdgePct:  awayEdgePct != null ? Math.round(awayEdgePct * 100) / 100 : null,
              homeEdgePct:  homeEdgePct != null ? Math.round(homeEdgePct * 100) / 100 : null,
              betEdgePct:   betEdgePct  != null ? Math.round(betEdgePct  * 100) / 100 : null,
              betModelPct,
              betPinVF,
              pinGap,       // Kalshi vs Pinnacle gap — flag if >15% AND diverge from model
              confidence:   conf,
              betSize,
              awayProjRuns: modelProb.awayProjRuns,
              homeProjRuns: modelProb.homeProjRuns,
              totalProj:    modelProb.totalProj,
              actionable:   (betEdgePct ?? 0) >= 1.5,
              logForCLV:    Math.abs(betEdgePct ?? 0) >= 1.0,
            };
            if (mlEdge.logForCLV) allEdges.push(mlEdge);
          } catch(e) {}
        }

        try { runLineEval = evalRunLine(modelProb, bookOdds); } catch(e) { runLineEval = null; }
        if (runLineEval?.best?.logForCLV) allEdges.push({ market: 'RUN LINE', ...runLineEval.best });

        try { totalEval = evalGameTotal(modelProb, bookOdds, awaySavant, homeSavant); } catch(e) { totalEval = null; }
        if (totalEval?.logForCLV) allEdges.push({ market: 'TOTAL', paperOnly: true, ...totalEval });

        try {
          teamTotals = evalTeamTotals(modelProb, awaySavant, homeSavant, awayBullpen, homeBullpen,
                                       awayStats, homeStats, bookOdds);
        } catch(e) { teamTotals = null; }
        // Add actionable TT edges to allEdges
        if (teamTotals?.awayActionable) {
          allEdges.push({
            market: 'TEAM TOTAL', side: 'AWAY', team: g.away.team,
            edgePct: teamTotals.awayEdgePct, projRuns: teamTotals.projectedAwayRuns,
            ttLine: teamTotals.awayTTLine, pOver: teamTotals.awayPover,
            confidence: modelProb?.confidence, betSize: teamTotals.awaySize,
            actionable: true, logForCLV: true,
          });
        }
        if (teamTotals?.homeActionable) {
          allEdges.push({
            market: 'TEAM TOTAL', side: 'HOME', team: g.home.team,
            edgePct: teamTotals.homeEdgePct, projRuns: teamTotals.projectedHomeRuns,
            ttLine: teamTotals.homeTTLine, pOver: teamTotals.homePover,
            confidence: modelProb?.confidence, betSize: teamTotals.homeSize,
            actionable: true, logForCLV: true,
          });
        }

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
          awaySavant.openerQualified  = awayOpenerOK;
        }
        if (homeIsOpener && homeSavant) {
          homeSavant.openerRole       = true;
          homeSavant.avgIPperStart    = ipPerStart[g.home.pitcher?.id];
          homeSavant.openerQualified  = homeOpenerOK;
        }
        // firstInningSplit is set for EVERY resolvable starter (see
        // firstInningIds above), not just openers -- Rule 24's own
        // openerRole/openerQualified fields above are unchanged and still
        // opener-only.
        if (awaySavant) awaySavant.firstInningSplit = awaySplit;
        if (homeSavant) homeSavant.firstInningSplit = homeSplit;

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
          // Add NRFI/YRFI to allEdges when lean is strong (STRONG only — composite required)
          if (nrfi && nrfi.lean !== 'NEUTRAL' && nrfi.leanStrength === 'STRONG') {
            const nrfiMarket = nrfi.lean; // 'NRFI' or 'YRFI'
            // Probability from composite score ratio
            const modelPct = nrfi.lean === 'NRFI' ? nrfi.nrfiPct : nrfi.yrfiPct;
            const conf = modelProb?.confidence ?? 'MEDIUM';
            // Market implied for NRFI/YRFI is typically ~52% (vig on -115)
            const marketImplied = 52.0;
            const edgePct = calcEdge(modelPct, marketImplied, conf);
            if ((edgePct ?? 0) >= 1.0) {
              // ── Gap 3: Generation-time YRFI/NRFI validation ─────────────────
              // Build the candidate bet with yrfiMeta for validation
              const yrfiMeta = nrfi.yrfiMeta || {
                lambdaUsed: 0.50,
                formulaUsed: 'poisson_independence',
                lambdaIsFirstInningSpecific: false,
                lambdaDerivedFromFullGame: false,
                parkFirstInningRateIncluded: false,
                teamFirstInningRatesIncluded: false,
                poissonCheckFirstInningValid: false,
              };
              const candidateBet = {
                market: nrfiMarket,
                reasons: nrfi.reasons || [],
                factors: {},  // evalNRFI does not use disallowed factors
                yrfiMeta,
              };
              const inputValidation = validateYrfiInputs(candidateBet);
              // Determine tracking type: must be PAPER if lambda not first-inning specific
              const yrfiTrackingType = (
                yrfiMeta.lambdaIsFirstInningSpecific &&
                yrfiMeta.poissonCheckFirstInningValid &&
                inputValidation.valid
              ) ? 'MODEL' : 'PAPER';
              const yrfiBlockReasons = inputValidation.valid ? [] : inputValidation.blockReasons;
              if (!yrfiMeta.lambdaIsFirstInningSpecific) {
                yrfiBlockReasons.push('lambdaIsFirstInningSpecific=false — PAPER only until 1st-inning rates available');
              }
              allEdges.push({
                market: nrfiMarket, edgePct: edgePct != null ? Math.round(edgePct * 100) / 100 : null,
                modelPct, leanStrength: nrfi.leanStrength,
                confidence: conf, betSize: calcSize(conf, nrfiMarket),
                actionable: (edgePct ?? 0) >= 1.5 && yrfiTrackingType !== 'PAPER',
                logForCLV: true,
                reasons: nrfi.reasons,
                yrfiMeta,
                trackingType: yrfiTrackingType,
                blockReason: yrfiBlockReasons.length > 0 ? yrfiBlockReasons.join('; ') : undefined,
                yrfiInputsValid: inputValidation.valid,
              });
            }
          }
        } catch(e) { nrfi = null; }

        try {
          const f5Model = f5Blocked ? {
            blocked: true,
            reason: 'Opener role with insufficient 1st-inning data — F5 unqualified per Rule 24',
            awayIsOpener, homeIsOpener, awaySplit, homeSplit
          } : evalF5(awaySavant, homeSavant, awayStanding, homeStanding,
                      awayStats, homeStats, modelProb?.factors?.parkRunAdj);

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

              // Edge vs vig-free sharp F5 line — use Pinnacle VF + calibration factor
              if (pinF5VFaway !== null) {
                const conf = modelProb?.confidence ?? 'MEDIUM';
                const awayF5EdgePct = calcEdge(f5Model.awayF5Pct, pinF5VFaway, conf);
                const homeF5EdgePct = calcEdge(f5Model.homeF5Pct, pinF5VFhome, conf);
                f5Model.awayF5Edge = awayF5EdgePct != null ? Math.round(awayF5EdgePct * 100) / 100 : null;
                f5Model.homeF5Edge = homeF5EdgePct != null ? Math.round(homeF5EdgePct * 100) / 100 : null;
                // f5Amplified threshold is 1.0% (Rule 69); standard is 1.5%
                const f5Threshold = f5Model.f5Amplified ? 1.0 : 1.5;
                f5Model.awayF5Actionable = (awayF5EdgePct ?? 0) >= f5Threshold;
                f5Model.homeF5Actionable = (homeF5EdgePct ?? 0) >= f5Threshold;
                f5Model.awayF5Size = calcSize(conf, 'F5 ML');
                f5Model.homeF5Size = calcSize(conf, 'F5 ML');
                // Add F5 edges to allEdges (previously missing — this was the bug)
                if (f5Model.awayF5Actionable || (awayF5EdgePct ?? 0) >= 1.0) {
                  allEdges.push({
                    market: 'F5 ML', side: 'AWAY', team: g.away.team,
                    edgePct: f5Model.awayF5Edge, modelPct: f5Model.awayF5Pct,
                    pinVFpct: pinF5VFaway, xERAGap: f5Model.xERAGap,
                    f5Amplified: f5Model.f5Amplified,
                    confidence: conf, betSize: f5Model.awayF5Size,
                    price: sharpF5.away ?? null,
                    actionable: f5Model.awayF5Actionable, logForCLV: true,
                  });
                }
                if (f5Model.homeF5Actionable || (homeF5EdgePct ?? 0) >= 1.0) {
                  allEdges.push({
                    market: 'F5 ML', side: 'HOME', team: g.home.team,
                    edgePct: f5Model.homeF5Edge, modelPct: f5Model.homeF5Pct,
                    pinVFpct: pinF5VFhome, xERAGap: f5Model.xERAGap,
                    f5Amplified: f5Model.f5Amplified,
                    confidence: conf, betSize: f5Model.homeF5Size,
                    price: sharpF5.home ?? null,
                    actionable: f5Model.homeF5Actionable, logForCLV: true,
                  });
                }
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
    // Stale-date guard: validate that the returned schedule date matches requested date
    const returnedGames = Array.isArray(enriched) ? enriched : [];
    if (returnedGames.length > 0) {
      for (const g of returnedGames) {
        const st = g.startTime;
        if (st) {
          const gameEtDate = new Date(st).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
          if (gameEtDate !== today) {
            const staleResult = {
              status: 'FAILED_STALE_DATE',
              requestedDate: today,
              actualDate: gameEtDate,
              source: 'api/slate',
              error: 'StatsAPI returned game on different date than requested'
            };
            if (callback) {
              res.setHeader('Content-Type', 'application/javascript');
              return res.status(200).send(`${callback}(${JSON.stringify(staleResult)})`);
            }
            return res.status(422).json(staleResult);
          }
        }
      }
    }

    const result = {
      date: today, kalshiDate,
      scheduleSource,
      games: enriched,
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

    // ── Gap 2: Sentinel annotation (slate protection) ────────────────────
    // Scan the result for sentinel prices before the response is serialized.
    // File-routing (authoritative/recheck/quarantine) is handled by
    // scripts/protect_slate.py in the GitHub Actions workflow.
    // We use an inline sentinel check here to avoid a require() dependency
    // in the Vercel serverless environment; isSentinelPrice() below still
    // loads its values from the single canonical lib/sentinel_constants.json
    // (see docs/DUPLICATE_LOGIC_INVENTORY.md #2) rather than a separate JS
    // literal, falling back to a hardcoded copy -- kept identical to the
    // JSON by tests/test_sentinel_python_js_parity.py -- only if that file
    // read ever fails (e.g. not traced into the serverless bundle), so
    // production behavior can never regress below what shipped before.
    (function annotateSentinels(obj) {
      const violations = [];
      function scan(o, path) {
        if (o === null || o === undefined) return;
        if (Array.isArray(o)) { o.forEach((v, i) => scan(v, `${path}[${i}]`)); }
        else if (typeof o === 'object') { Object.entries(o).forEach(([k, v]) => scan(v, path ? `${path}.${k}` : k)); }
        else if (typeof o === 'number' && isSentinelPrice(o)) {
          violations.push({ path, value: o });
        }
      }
      scan(obj.games, 'games');
      result._sentinelCheckRan = true;
      result._containsSentinels = violations.length > 0;
      if (violations.length > 0) {
        result._sentinelViolations = violations.slice(0, 20);
        result._sentinelViolationCount = violations.length;
        console.error(`[slate.js] SENTINEL PRICES DETECTED: ${violations.length} occurrences — workflow will quarantine this run`);
      }
    })(result);

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

