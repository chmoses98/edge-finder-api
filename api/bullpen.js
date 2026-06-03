export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { season = '2026' } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  function bullpenGrade(xfip) {
    if (xfip === null) return 'UNKNOWN';
    if (xfip < 3.50) return 'ELITE';
    if (xfip < 4.00) return 'ABOVE_AVERAGE';
    if (xfip < 4.50) return 'AVERAGE';
    if (xfip < 5.00) return 'BELOW_AVERAGE';
    return 'VULNERABLE';
  }

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    const splitLine = (line) => {
      const result = []; let current = ''; let inQuotes = false;
      for (const ch of line) {
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
        else { current += ch; }
      }
      result.push(current.trim());
      return result;
    };
    const headers = splitLine(lines[0]);
    return lines.slice(1).map(line => {
      const values = splitLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = values[i] || ''; });
      return obj;
    });
  }

  // NEW: Fetch high-leverage reliever stats from Savant
  // Filters to only appearances where leverage index >= 1.5 (high-leverage situations)
  // This gives us the xFIP of arms actually used in close games
  async function fetchHighLeverageBullpen(season) {
    try {
      // Savant statcast_search with hfSit=high_leverage (LI >= 1.5) for relievers
      // group_by=team aggregates all high-leverage relief appearances per team
      const url = `https://baseballsavant.mlb.com/statcast_search/csv?all=true` +
        `&hfSea=${season}%7C&player_type=pitcher&hfGT=R%7C` +
        `&hfSit=high_lev%7C` +           // high leverage situations (LI >= 1.5)
        `&hfRO=1%7C` +                   // 1 = relief appearances only
        `&min_pitches=0&min_results=0&min_pas=0` +
        `&group_by=team&sort_col=pitches&sort_order=desc` +
        `&chk_stats_pa=on&chk_stats_so=on&chk_stats_bb=on&chk_stats_hrs=on&chk_stats_era=on&chk_stats_xera=on` +
        `&type=details`;

      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      if (!r.ok) return {};

      const rows = parseCSV(await r.text());
      const teamHL = {};

      for (const row of rows) {
        const team = row['team_name'] || row['player_name'] || row['team'];
        if (!team) continue;
        const abbr = team.trim().toUpperCase();

        const pa  = pf(row['pa'] || row['plate_appearances']);
        const so  = pf(row['so'] || row['strikeouts']);
        const bb  = pf(row['bb'] || row['walks']);
        const hr  = pf(row['hrs'] || row['home_runs'] || row['hr']);
        const xera = pf(row['estimated_era_using_speedangle'] || row['xera']);

        if (pa === null || pa < 10) continue;

        // Compute FIP proxy for high-leverage appearances
        const FIP_CONST = 3.10;
        const paAsIP = pa / 4.3;
        const hlFIP = (paAsIP > 0 && so !== null && bb !== null && hr !== null)
          ? Math.round(((13 * hr + 3 * bb - 2 * so) / paAsIP + FIP_CONST) * 100) / 100
          : null;

        teamHL[abbr] = {
          pa,
          hlFIP,
          hlXERA: xera,
          // Use hlFIP as primary; fall back to hlXERA
          hlXFIP: hlFIP ?? xera,
          grade:  bullpenGrade(hlFIP ?? xera),
          sampleSize: pa,
        };
      }

      return teamHL;
    } catch(e) { return {}; }
  }

  try {
    const [relieverRes, teamRes, hlData] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=${season}&sportId=1&group=pitching&gameType=R&stats=season&playerPool=relief`),
      fetch(`https://statsapi.mlb.com/api/v1/teams?sportId=1&season=${season}`),
      fetchHighLeverageBullpen(season),   // NEW
    ]);

    if (!relieverRes.ok) throw new Error(`Reliever stats fetch failed: ${relieverRes.status}`);
    if (!teamRes.ok) throw new Error(`Team fetch failed: ${teamRes.status}`);

    const relieverData = await relieverRes.json();
    const teamData     = await teamRes.json();

    const teamMap = {};
    for (const t of (teamData.teams || [])) {
      teamMap[t.id] = { abbr: t.abbreviation, name: t.name };
    }

    const bullpens = {};
    for (const rec of (relieverData?.stats?.[0]?.splits || [])) {
      const teamId = rec.team?.id;
      const abbr   = rec.team?.abbreviation || teamMap[teamId]?.abbr;
      if (!abbr) continue;

      const s = rec.stat || {};
      const era    = pf(s.era);
      const whip   = pf(s.whip);
      const kPer9  = pf(s.strikeoutsPer9Inn);
      const bbPer9 = pf(s.walksPer9Inn);
      const hr9    = pf(s.homeRunsPer9);
      const ip     = pf(s.inningsPitched);

      const leagueHR9 = 1.20;
      let xFIP = null;
      if (era !== null && hr9 !== null && kPer9 !== null && bbPer9 !== null) {
        const hrDiff = (hr9 - leagueHR9) * 1.35;
        xFIP = Math.round((era - hrDiff) * 100) / 100;
      }

      // NEW: merge high-leverage split
      const hl = hlData[abbr] || null;

      bullpens[abbr] = {
        abbr,
        name:           teamMap[teamId]?.name || '',
        season,
        era,
        xFIP,                              // overall reliever xFIP
        whip, kPer9, bbPer9, hr9,
        inningsPitched: ip,
        grade:          bullpenGrade(xFIP),
        elite:          xFIP !== null && xFIP < 3.50,
        vulnerable:     xFIP !== null && xFIP > 4.50,
        // NEW: high-leverage split
        hlXFIP:         hl?.hlXFIP  ?? null,   // use in close-game projections
        hlFIP:          hl?.hlFIP   ?? null,
        hlXERA:         hl?.hlXERA  ?? null,
        hlGrade:        hl ? bullpenGrade(hl.hlXFIP) : null,
        hlSamplePA:     hl?.sampleSize ?? null,
        hlAvailable:    hl !== null && hl.hlXFIP !== null,
        // Divergence flag: if overall vs HL xFIP differ by >0.5, the bullpen manages leverage atypically
        hlDivergence:   (xFIP !== null && hl?.hlXFIP !== null)
                          ? Math.round((hl.hlXFIP - xFIP) * 100) / 100
                          : null,
      };
    }

    return res.status(200).json({
      ok: true, season,
      fetchedAt: new Date().toISOString(),
      teamCount: Object.keys(bullpens).length,
      hlDataAvailable: Object.keys(hlData).length > 0,
      bullpens
    });

  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
