export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { season = '2026' } = req.query;

  function pf(val) {
    const n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  function bullpenGrade(xfip) {
    if (xfip === null) return 'UNKNOWN';
    if (xfip < 3.50) return 'ELITE';
    if (xfip < 4.00) return 'ABOVE_AVERAGE';
    if (xfip < 4.50) return 'AVERAGE';
    if (xfip < 5.00) return 'BELOW_AVERAGE';
    return 'VULNERABLE';
  }

  try {
    const [relieverRes, teamRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=${season}&sportId=1&group=pitching&gameType=R&stats=season&playerPool=relief`),
      fetch(`https://statsapi.mlb.com/api/v1/teams?sportId=1&season=${season}`)
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

      bullpens[abbr] = {
        abbr,
        name:           teamMap[teamId]?.name || '',
        season,
        era, xFIP, whip, kPer9, bbPer9, hr9,
        inningsPitched: ip,
        grade:          bullpenGrade(xFIP),
        elite:          xFIP !== null && xFIP < 3.50,
        vulnerable:     xFIP !== null && xFIP > 4.50,
        // HL fields populated by GitHub Actions fetch_savant_bullpen_hl.py
        hlXFIP:     null,
        hlGrade:    null,
        hlAvailable: false,
      };
    }

    return res.status(200).json({
      ok: true, season,
      fetchedAt: new Date().toISOString(),
      teamCount: Object.keys(bullpens).length,
      bullpens
    });

  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
