/**
 * api/savant_bullpen_hl.js
 *
 * Fetches high-leverage bullpen FIP approximation for all 30 teams.
 * Uses MLB Stats API relief pitcher stats filtered to high-leverage appearances.
 *
 * Approach: fetch team reliever rosters + individual reliever stats,
 * then weight by leverage index usage (closers/setup men = high leverage).
 * Approximation: top 3 relievers by saves+holds = high-leverage arms.
 *
 * Lightweight — MLB Stats API only, no Savant statcast_search.
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { season = '2026' } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  function bullpenGrade(xfip) {
    if (xfip === null) return null;
    if (xfip < 3.50) return 'ELITE';
    if (xfip < 4.00) return 'ABOVE_AVERAGE';
    if (xfip < 4.50) return 'AVERAGE';
    if (xfip < 5.00) return 'BELOW_AVERAGE';
    return 'VULNERABLE';
  }

  const FIP_CONST = 3.10;

  try {
    // Fetch all reliever stats for the season in one call
    const [reliefRes, teamRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/stats?stats=season&group=pitching&gameType=R` +
            `&season=${season}&playerPool=relief&sportId=1&limit=500` +
            `&fields=stats,splits,stat,saves,holds,inningsPitched,homeRuns,baseOnBalls,strikeOuts,era,player,team`),
      fetch(`https://statsapi.mlb.com/api/v1/teams?sportId=1&season=${season}`)
    ]);

    if (!reliefRes.ok) throw new Error(`Relief stats: ${reliefRes.status}`);
    const [reliefData, teamData] = await Promise.all([reliefRes.json(), teamRes.json()]);

    const teamMap = {};
    for (const t of (teamData.teams || [])) {
      teamMap[t.id] = t.abbreviation;
    }

    // Group relievers by team, sort by saves+holds descending
    // Top 3-5 per team = high-leverage arms
    const teamRelievers = {};
    for (const split of (reliefData?.stats?.[0]?.splits || [])) {
      const teamId = split.team?.id;
      const abbr   = split.team?.abbreviation || teamMap[teamId];
      if (!abbr) continue;

      const s = split.stat || {};
      const saves   = parseInt(s.saves  || 0);
      const holds   = parseInt(s.holds  || 0);
      const ipRaw   = parseFloat(s.inningsPitched || '0');
      const ip      = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
      const hr      = parseInt(s.homeRuns    || 0);
      const bb      = parseInt(s.baseOnBalls || 0);
      const k       = parseInt(s.strikeOuts  || 0);

      if (ip < 2) continue;  // skip trivial appearances

      const fip = ip > 0
        ? Math.round(((13*hr + 3*bb - 2*k) / ip + FIP_CONST) * 100) / 100
        : null;

      if (!teamRelievers[abbr]) teamRelievers[abbr] = [];
      teamRelievers[abbr].push({
        saves, holds, ip, fip,
        leverageScore: saves + holds,  // proxy for high-leverage usage
      });
    }

    // For each team: compute HL xFIP from top relievers by leverage score
    const hlResults = {};
    for (const [abbr, relievers] of Object.entries(teamRelievers)) {
      // Sort by saves+holds desc, take top 5
      const sorted   = relievers.sort((a,b) => b.leverageScore - a.leverageScore);
      const topArms  = sorted.slice(0, 5).filter(r => r.fip !== null);
      const lowArms  = sorted.slice(0, 5);

      if (topArms.length === 0) continue;

      // Weighted average FIP (weight by IP)
      const totalIP  = topArms.reduce((s,r) => s + r.ip, 0);
      const wtdFIP   = totalIP > 0
        ? Math.round(topArms.reduce((s,r) => s + r.fip * r.ip, 0) / totalIP * 100) / 100
        : null;

      hlResults[abbr] = {
        hlXFIP:      wtdFIP,
        hlGrade:     bullpenGrade(wtdFIP),
        hlAvailable: wtdFIP !== null,
        hlSamplePA:  Math.round(totalIP * 4.3),  // rough PA estimate
        hlMethod:    'top_saves_holds_weighted',
        hlArmsUsed:  topArms.length,
      };
    }

    return res.status(200).json({
      ok: true, season,
      fetchedAt: new Date().toISOString(),
      teamCount: Object.keys(hlResults).length,
      teams: hlResults,
    });

  } catch(err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
