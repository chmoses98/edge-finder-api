export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const apiKey = process.env.ODDS_API_KEY;
  if (!apiKey) return res.status(500).json({ error: 'ODDS_API_KEY not configured' });

  const { date } = req.query;
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return res.status(400).json({ error: 'date required (YYYY-MM-DD)' });
  }

  const TEAM_ABBR = {
    'Arizona Diamondbacks':'AZ','Atlanta Braves':'ATL','Baltimore Orioles':'BAL',
    'Boston Red Sox':'BOS','Chicago Cubs':'CHC','Chicago White Sox':'CWS',
    'Cincinnati Reds':'CIN','Cleveland Guardians':'CLE','Colorado Rockies':'COL',
    'Detroit Tigers':'DET','Houston Astros':'HOU','Kansas City Royals':'KC',
    'Los Angeles Angels':'LAA','Los Angeles Dodgers':'LAD','Miami Marlins':'MIA',
    'Milwaukee Brewers':'MIL','Minnesota Twins':'MIN','New York Mets':'NYM',
    'New York Yankees':'NYY','Oakland Athletics':'OAK','Philadelphia Phillies':'PHI',
    'Pittsburgh Pirates':'PIT','San Diego Padres':'SD','San Francisco Giants':'SF',
    'Seattle Mariners':'SEA','St. Louis Cardinals':'STL','Tampa Bay Rays':'TB',
    'Texas Rangers':'TEX','Toronto Blue Jays':'TOR','Washington Nationals':'WSH',
  };

  const SUPPORTED  = { 'ML':'h2h','F5 ML':'h2h_h1','Total':'totals','Game Total':'totals','Run Line':'spreads','RL':'spreads','Team Total':'team_totals','TT':'team_totals' };
  const UNSUPPORTED = new Set(['YRFI','NRFI','K Prop','Pitcher Prop','Batter Prop']);
  const SHARP       = ['lowvig','draftkings','fanduel','betmgm'];

  const toImp = p => p == null ? null : p >= 100 ? 100/(p+100) : Math.abs(p)/(Math.abs(p)+100);
  const vigFree = (a,b) => { const ia=toImp(a),ib=toImp(b); if(!ia||!ib) return [null,null]; const t=ia+ib; return [Math.round(ia/t*1000)/10,Math.round(ib/t*1000)/10]; };
  const parseGame = s => { if(!s) return [null,null]; const sep=s.includes(' @ ')?' @ ':'@'; const p=s.split(sep); return p.length===2?[p[0].trim().toUpperCase(),p[1].trim().toUpperCase()]:[null,null]; };
  const abbr = n => TEAM_ABBR[n]||n?.toUpperCase();
  const fmt  = p => `${p>=0?'+':''}${p}`;

  const matchGame = (games,away,home) => games.find(g=>abbr(g.away_team)===away&&abbr(g.home_team)===home)||null;
  const getSharp  = (game,key) => { for(const bk of SHARP){const b=(game.bookmakers||[]).find(x=>x.key===bk);if(!b)continue;const m=(b.markets||[]).find(x=>x.key===key);if(m)return{bkKey:bk,mkt:m};}return null;};

  const fetchHistorical = async (dateStr, markets) => {
    const d = new Date(dateStr+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+1);
    const next     = d.toISOString().slice(0,10);
    const snapshot = `${next}T06:00:00Z`;
    const url = `https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds`
      +`?apiKey=${apiKey}&regions=us&markets=${markets}&oddsFormat=american`
      +`&commenceTimeFrom=${dateStr}T15:00:00Z&commenceTimeTo=${snapshot}&date=${snapshot}`;
    try {
      const r=await fetch(url,{headers:{Accept:'application/json'}});
      const raw=await r.json();
      return { games: Array.isArray(raw)?raw:(raw.data||[]), remaining: r.headers.get('x-requests-remaining') };
    } catch(e) { return { games:[], remaining:null }; }
  };

  const extractML = (game,away,mkt) => { const s=getSharp(game,mkt); if(!s)return null; const outs=s.mkt.outcomes||[]; const aO=outs.find(o=>abbr(o.name)===away),hO=outs.find(o=>abbr(o.name)!==away); if(!aO||!hO)return null; return{awayPrice:aO.price,homePrice:hO.price,book:s.bkKey}; };
  const extractTotal = (game,betStr) => { const s=getSharp(game,'totals'); if(!s)return null; const side=/over|( o )/i.test(betStr)?'over':'under'; const nm=betStr.match(/(\d+\.?\d*)/); const betNum=nm?parseFloat(nm[1]):null; const ov=(s.mkt.outcomes||[]).find(o=>o.name?.toLowerCase()==='over'),un=(s.mkt.outcomes||[]).find(o=>o.name?.toLowerCase()==='under'); if(!ov||!un)return null; const bet=side==='over'?ov:un,opp=side==='over'?un:ov; return{betSide:side,betPrice:bet.price,oppPrice:opp.price,closingNumber:ov.point,betNumber:betNum,book:s.bkKey}; };
  const extractRL = (game,betStr,away) => { const s=getSharp(game,'spreads'); if(!s)return null; const isAway=away&&betStr.toUpperCase().includes(away),isMinus=betStr.includes('-1.5'); for(const o of(s.mkt.outcomes||[])){const oA=abbr(o.name),oIsAway=oA===away,oP=o.point||0; if(oIsAway===isAway&&(oP<0)===isMinus){const opp=(s.mkt.outcomes||[]).find(x=>x!==o); return{betPrice:o.price,oppPrice:opp?.price??null,point:oP,book:s.bkKey};}} return null;};

  const calcCLV = (bet,closing,market) => { if(!closing)return null; const ourImp=toImp(bet.price)*100; if(ourImp==null)return null; if(market==='ML'||market==='F5 ML'){const[vfA,vfH]=vigFree(closing.awayPrice,closing.homePrice); if(!vfA)return null; const[away]=parseGame(bet.game); const txt=(bet.bet||'').toUpperCase(); const isAway=away&&(txt.startsWith(away)||txt.includes(away)); return Math.round(((isAway?vfA:vfH)-ourImp)*100)/100;} if(['Total','Game Total','Run Line','RL'].includes(market)){const[vf]=vigFree(closing.betPrice,closing.oppPrice); if(!vf)return null; return Math.round((vf-ourImp)*100)/100;} return null;};

  const clStr = (bet,closing,market) => { if(!closing)return null; const bk=closing.book||''; if(market==='ML'||market==='F5 ML'){const[away]=parseGame(bet.game); const txt=(bet.bet||'').toUpperCase(); const isAway=away&&(txt.startsWith(away)||txt.includes(away)); return`${fmt(isAway?closing.awayPrice:closing.homePrice)} [${bk}]`;} if(market==='Total'||market==='Game Total'){const side=closing.betSide.charAt(0).toUpperCase()+closing.betSide.slice(1); const numStr=(closing.betNumber!=null&&closing.closingNumber!=null&&closing.betNumber!==closing.closingNumber)?`${closing.betNumber}→${closing.closingNumber}`:`${closing.closingNumber}`; return`${side} ${numStr} ${fmt(closing.betPrice)} [${bk}]`;} if(market==='Run Line'||market==='RL')return`${fmt(closing.point)} ${fmt(closing.betPrice)} [${bk}]`; return null;};

  const mergeGames = (base,extra) => { const map={}; for(const g of extra)map[g.id]=g.bookmakers||[]; for(const g of base)for(const bk of(g.bookmakers||[])){const xBk=(map[g.id]||[]).find(b=>b.key===bk.key); if(xBk)bk.markets=[...(bk.markets||[]),...(xBk.markets||[])];} const ex=new Set(base.map(g=>g.id)); for(const g of extra)if(!ex.has(g.id))base.push(g); };

  try {
    // Fetch bets.json from public repo (no auth needed)
    const betsRaw = await fetch(`https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/bets.json`);
    if (!betsRaw.ok) return res.status(500).json({ error: 'Failed to fetch bets.json' });
    const bets = await betsRaw.json();

    // Mark unsupported, find targets
    for (const b of bets) {
      if (b.clv==null && UNSUPPORTED.has(b.market) && ['WIN','LOSS','PUSH'].includes(b.result))
        b.closingLineSource = 'market_unavailable';
    }

    const targets = bets.filter(b =>
      b.date===date && b.clv==null && ['WIN','LOSS','PUSH'].includes(b.result) && SUPPORTED[b.market]
      && !['expired_no_betTimeLine','market_unavailable'].includes(b.closingLineSource)
    );

    const log = [`CLV for ${date}: ${targets.length} bets to process`];

    if (targets.length > 0) {
      const needed = new Set(targets.map(b=>SUPPORTED[b.market]).filter(Boolean));
      const mainMkts = [...needed].filter(m=>m!=='h2h_h1'&&m!=='team_totals').join(',');
      const needF5 = needed.has('h2h_h1'), needTT = needed.has('team_totals');

      let oddsGames = [], remaining = null;
      if (mainMkts) { const {games,remaining:r}=await fetchHistorical(date,mainMkts); oddsGames=games; remaining=r; log.push(`Main [${mainMkts}]: ${games.length} games | remaining: ${r}`); }
      if (needF5)   { const {games:f5}=await fetchHistorical(date,'h2h_h1');     mergeGames(oddsGames,f5); log.push(`F5: ${f5.length} games`); }
      if (needTT)   { const {games:tt}=await fetchHistorical(date,'team_totals'); mergeGames(oddsGames,tt); log.push(`TT: ${tt.length} games`); }

      log.push(`Odds pool: ${oddsGames.length} games${oddsGames.length?` | Sample: ${oddsGames.slice(0,3).map(g=>`${abbr(g.away_team)}@${abbr(g.home_team)}`).join(', ')}`:''}`);

      let updated = 0;
      for (const b of targets) {
        const [away] = parseGame(b.game);
        if (!away) { log.push(`  SKIP ${b.id}: parse fail`); continue; }
        const game = matchGame(oddsGames, away, parseGame(b.game)[1]);
        if (!game) { log.push(`  NO_MATCH ${b.id}: ${b.game}`); b.closingLineSource='no_game_match'; continue; }

        const mkt = b.market;
        let closing = null;
        if (mkt==='ML')                              closing=extractML(game,away,'h2h');
        else if (mkt==='F5 ML')                      closing=extractML(game,away,'h2h_h1');
        else if (mkt==='Total'||mkt==='Game Total')  closing=extractTotal(game,b.bet||'');
        else if (mkt==='Run Line'||mkt==='RL')       closing=extractRL(game,b.bet||'',away);

        if (!closing) { log.push(`  NO_LINE ${b.id}: ${mkt}`); b.closingLineSource='line_not_found'; continue; }

        const clv = calcCLV(b,closing,mkt);
        b.closingLine          = clStr(b,closing,mkt);
        b.closingLineSource    = closing.book;
        b.closingLineTimestamp = `${date}T06:00:00Z`;
        b.clv                  = clv;
        log.push(`  ${clv>=0?'✓':'✗'} ${b.id} | ${mkt} | CL: ${b.closingLine} | CLV: ${clv!=null?(clv>=0?'+':'')+clv+'%':'N/A'}`);
        updated++;
      }
      log.push(`\nDone: ${updated}/${targets.length} updated | requestsRemaining: ${remaining}`);
    }

    return res.status(200).json({ date, bets, log });

  } catch(e) {
    return res.status(500).json({ error: e.message, stack: e.stack?.slice(0,500) });
  }
}
