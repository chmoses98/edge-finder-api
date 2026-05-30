# SLATE_WORKFLOW.md
# Last updated: May 30, 2026 — v2.1

## Session Start — Pull Model Files from GitHub
Pull all five files before anything else. No analysis or logging begins until all five are confirmed pulled.

Pull order:
1. `RULES.md` — gate definitions and rule hierarchy
2. `MODEL_CORE.md` — probability engine, sizing, calibration
3. `SLATE_WORKFLOW.md` — this file; session workflow
4. `DATA_SOURCES.md` — data field definitions and fallback chain
5. `bets.json` — authoritative bet ledger (required for duplicate ID check and calibration script)

Use GitHub raw content API: `https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/[filename]`

---

## One-Command Post-Game Trigger
When the user says **"post-game review"** or **"review today's slate"**, automatically execute ALL of the following in one pass:

1. Pull all 4 model files + bets.json from GitHub
2. Fetch scores + box scores for every PENDING game via `fetch_sports_data` (scores and game_stats)
3. Settle every bet (W/L/Push, P&L, CLV via web search for Pinnacle closing line)
4. Update bets.json and regenerate BET_LOG.md
5. Update signal-type win rate table in MODEL_CORE Section 3
6. Flag model improvement areas based on what hit/missed and why
7. Propose specific rule or algorithm edits with canonical examples
8. Push updated bets.json + BET_LOG.md to GitHub

After presenting, wait for user approval on proposed model changes, then push updated model files.

---

## Pre-Slate Review (run first, every session)
1. Pull yesterday's results via `fetch_sports_data` — scores and game_stats for all relevant game IDs
2. Pull box scores for ALL pending bets — get inning-by-inning linescore for NRFI/YRFI/F5 settlement
   → NRFI/YRFI: check inning 1 linescore
   → F5 ML: sum innings 1–5 for both teams
   → Totals: verify final score
   → Team Totals: verify team's final run total vs confirmed TT line
   → Never ask the user for results — always pull box score directly
3. Mark each pending bet WIN/LOSS/PUSH, record P/L
4. Pull Pinnacle closing line via web search for each bet (search "Pinnacle closing line [TEAM] ML [DATE]" or use OddsPortal/Action Network fallback). Log to `closingLine`, `closingLineSource`, `closingLineTimestamp`. If not found → log null, never fabricate. **Settlement window: 48 hours from game time. If closing line cannot be found within 48 hours, log `closingLine: null`, `clv: null`, `closingLineSource: "not_found"` and proceed with settlement — do not hold the bet open waiting for closing line data.** Null is the correct value; an estimated or fabricated closing line is a model failure.
5. Calculate CLV% from closing line. Log to `clv`.
6. Recalculate cumulative summary (record, P/L, ROI, bankroll)
7. Update signal-type win rate table (MODEL_CORE Section 3) — this is the per-session calibration leading indicator
8. Run tier-level calibration check. If any tier reaches 50+ settled bets: recalculate factor per the formula. Even below 50: run per-tier win rate analysis and compare to calibration table. Do NOT update factors until N≥50.
9. Update bets.json with all settled results
10. Regenerate BET_LOG.md from bets.json
11. Push bets.json + BET_LOG.md to GitHub
12. **Simultaneously:** Flag model adjustment lessons → identify patterns → propose RULES.md and MODEL_CORE.md additions if pattern is clear

---

## Slate Data Fetch
1. Trigger `fetch-slate` GitHub Action:
   ```
   POST https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
   Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
   ```
2. Wait ~40 seconds
3. Verify `data/meta.json` fetchedAt matches today and is <4 hours old
4. Read `data/slate.json` — source of truth for analysis
5. If Action fails → fall back to fallback chain in DATA_SOURCES.md

---

## Slate Analysis

### Step 0 — Bounceback/Regression Pre-Scan
**This step is a hard prerequisite. No individual game analysis may begin until this pre-scan output is documented in the session response.** If the pre-scan is absent from the output, the session is incomplete regardless of how many bets are logged.

Before analyzing any individual game, scan all teams on the slate:
- Pull last 7 and last 15 game R/G for each team
- Compare to season xOPS / wRC+ / barrel%
- **Bounceback flag:** recent results worse than underlying metrics + facing weak starter = offensive upside likely underpriced
- **Regression flag:** recent results better than underlying metrics + facing elite starter = normalize signal
- Log flags next to each team's context line. Feed directly into TT, total, and ML evaluations.

**Required output format for pre-scan (must appear before game-by-game analysis):**
```
PRE-SCAN: [Team] | Last7 R/G: X.X | Last15 R/G: X.X | Season R/G: X.X | wRC+: XXX | Flag: [BOUNCEBACK / REGRESSION / NEUTRAL]
```
One line per team. If rolling data is unavailable, note "rolling unavailable — season baseline used." This is the minimum acceptable pre-scan output. Skipping it is a model failure.

### Step 0c — Live Data Enrichment (MLB Stats API)
Run before any game-by-game analysis. This feeds Layer 1 (data anchor) of the three-layer framework (Rule 64).

```python
import urllib.request, json

BASE = "https://statsapi.mlb.com/api/v1"

def get_team_rolling_rg(team_id, last_n=15):
    """Pull last N game logs for a team, compute rolling R/G."""
    url = f"{BASE}/teams/{team_id}/stats?stats=gameLog&group=hitting&season=2026&gameType=R"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    logs = data.get('stats', [{}])[0].get('splits', [])
    recent = logs[-last_n:] if len(logs) >= last_n else logs
    if not recent: return None
    total_runs = sum(int(g.get('stat', {}).get('runs', 0)) for g in recent)
    return round(total_runs / len(recent), 2)

def get_bullpen_ip_last3(team_id):
    """Pull bullpen IP last 3 days. Returns total IP and list of pitchers who threw."""
    # Use schedule + boxscore endpoint for last 3 games
    from datetime import date, timedelta
    today = date.today()
    start = (today - timedelta(days=3)).strftime('%Y-%m-%d')
    end = today.strftime('%Y-%m-%d')
    url = f"{BASE}/schedule?teamId={team_id}&startDate={start}&endDate={end}&sportId=1&hydrate=boxscore"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    pitchers = {}
    for date_block in data.get('dates', []):
        for game in date_block.get('games', []):
            bs = game.get('liveData', {}).get('boxscore', {})
            for side in ['home', 'away']:
                team_data = bs.get('teams', {}).get(side, {})
                if str(team_data.get('team', {}).get('id')) == str(team_id):
                    for p in team_data.get('pitchers', []):
                        pid = p
                        pstats = team_data.get('players', {}).get(f'ID{pid}', {}).get('stats', {}).get('pitching', {})
                        name = team_data.get('players', {}).get(f'ID{pid}', {}).get('person', {}).get('fullName', str(pid))
                        ip = pstats.get('inningsPitched', '0')
                        if ip and ip != '0':
                            pitchers[name] = pitchers.get(name, 0) + float(ip.replace('.1', '.33').replace('.2', '.67'))
    return pitchers

def get_starter_last5_xfip_components(pitcher_id):
    """Pull last 5 starts FIP components: HR, BB, K, IP."""
    url = f"{BASE}/people/{pitcher_id}/stats?stats=gameLog&group=pitching&season=2026&gameType=R"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    logs = data.get('stats', [{}])[0].get('splits', [])
    starts = [g for g in logs if int(g.get('stat', {}).get('gamesStarted', 0)) > 0]
    recent = starts[-5:]
    if not recent: return None
    totals = {'hr': 0, 'bb': 0, 'k': 0, 'ip': 0.0}
    for g in recent:
        s = g.get('stat', {})
        totals['hr'] += int(s.get('homeRuns', 0))
        totals['bb'] += int(s.get('baseOnBalls', 0))
        totals['k'] += int(s.get('strikeOuts', 0))
        ip_str = s.get('inningsPitched', '0')
        totals['ip'] += float(str(ip_str).replace('.1', '.33').replace('.2', '.67'))
    # xFIP formula: ((13*HR + 3*BB - 2*K) / IP) + FIP_constant
    # Use FIP constant ~3.10 for 2026
    if totals['ip'] == 0: return None
    xfip = ((13 * totals['hr'] + 3 * totals['bb'] - 2 * totals['k']) / totals['ip']) + 3.10
    return {'xfip': round(xfip, 2), 'ip': round(totals['ip'], 1), 'starts': len(recent), **totals}
```

**What to pull for each game:**
1. `get_team_rolling_rg(team_id)` for both teams → Layer 1 offense input
2. `get_bullpen_ip_last3(team_id)` for both teams → flag fatigue before any Under
3. `get_starter_last5_xfip_components(pitcher_id)` for both starters → true_xFIP from real data

**MLB team IDs reference** (common): NYY=147, BOS=111, TB=139, BAL=110, TOR=141, CLE=114, MIN=142, CWS=145, DET=116, KC=118, HOU=117, TEX=140, LAA=108, OAK/ATH=133, SEA=136, ATL=144, NYM=121, PHI=143, MIA=146, WSH=120, CHC=112, STL=138, MIL=158, CIN=113, PIT=134, LAD=119, SF=137, COL=115, SD=135, AZ=109

**Rolling R/G divergence flag:** If rolling 15-game R/G differs from season R/G by >0.5 in either direction, flag it and explain how it moves the projection. Rolling is primary; season is context only (Rule 65).

**Bullpen fatigue flag:** If any key reliever threw yesterday or team bullpen threw 5+ IP in last 2 days, fire `bullpenFatigued` and step down one tier on any Under (Rule 66).

### Step 0b — Compute Poisson Engine (bash_tool)
Before any game-by-game analysis, have the Poisson computation ready. Run this in bash_tool once per session:

```python
import math

def poisson_pmf(k, lam):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def game_probs(away_proj, home_proj, max_runs=20):
    p_away_win = p_home_win = p_push = 0
    for a in range(max_runs+1):
        for h in range(max_runs+1):
            p = poisson_pmf(a, away_proj) * poisson_pmf(h, home_proj)
            if a > h: p_away_win += p
            elif a < h: p_home_win += p
            else: p_push += p
    p_away_net = p_away_win / (1 - p_push)
    p_home_net = p_home_win / (1 - p_push)
    return round(p_away_net*100, 1), round(p_home_net*100, 1), round(p_push*100, 1)

def total_prob(total_proj, line, max_runs=30):
    p_over = sum(poisson_pmf(r, total_proj) for r in range(int(line)+1, max_runs+1))
    return round(p_over*100, 1)

# Example:
# away_p, home_p, push_p = game_probs(4.8, 3.2)
# over_p = total_prob(8.0, 7.5)
```

Use this for any projection not clearly in the lookup table, and for any edge within 1% of a tier threshold.

### Step 1 — Starter Confirmation
For every game:
- Confirm same-day starters. Flag TBD starters — model confidence drops to LOW for that game.
- Flag any pitcher averaging <3 IP/start as opener → check `pitcher.firstInningSplit`
- If opener xERA unavailable or sample <5: F5 and K props UNQUALIFIED [T1 gate]
- Flag game total Under as suspect when either team uses an opener (Rule 31) [T1 gate]

### Step 1a — Prior-Day Offense Scan
Pull yesterday's box scores for all teams on today's slate:
- Flag any team that scored 7+ runs yesterday → Under on their game today [T2 soft gate fires]
- If flagged team faces starter with K/9 <9.0 OR BB/9 >3.0: soft gate fires

### Step 1b — 1st Inning Run Rate Pull
For every game where NRFI or YRFI is considered:
- Pull each team's season and last-15-game 1st-inning runs scored per game
- Flag any team in top 5 in 1st-inning run rate as YRFI signal
- Hard gate: do not log NRFI against top-5 1st-inning offense without dual sub-3.00 1st-inning xERA [T1]

### Step 1c — Starter True Talent Calculation
For every confirmed starter, calculate true_xFIP before game analysis:

1. Pull: season xFIP, last 5 starts xFIP, xERA, K/9, BB/9, avg IP/start, season depth (GS count), FB%
2. Identify pitcher type (established/younger/IL returner/streak divergence) AND season depth → apply regression weights from MODEL_CORE Section 1
3. Calculate: `true_xFIP = (N_recent × recent_xFIP + M_season × season_xFIP) / (N + M)`
4. Check xFIP vs xERA divergence: if >0.5 gap, flag fade/buy signal
5. Check velocity trend if available: 1+ mph below season avg → add 0.3 to true_xFIP
6. Check TTO split if available: document for F5 adjustment
7. Log true_xFIP for each starter — feeds directly into run projection

### Step 1d — Lineup Construction Pull
For every game, pull confirmed or projected lineups:

1. Check `data/slate.json` → `game.homeLineup` / `game.awayLineup`
2. If not in slate: check `mlb.com/probable-pitchers` for lineup cards (~3–4 hrs pre-game)
3. **Lineup timing check:** If >3 hours before first pitch and lineup still unconfirmed → flag as potential injury/roster hold, not routine delay
4. Calculate lineup adjustment factor:
   - `adj = (today_wRC+ − season_wRC+) / 100 × 0.70`
5. Identify handedness composition (% LHH vs RHH) → feed into Step 3 of run projection
6. Flag missing key bats (wRC+ >130): −0.05 offense scalar
7. If lineup unconfirmed: use season wRC+, note "lineup unconfirmed — using season baseline" — TT bets must be Paper only [T1]

### Step 1e — betTimeLine Capture
At the start of analysis for each game, record the current Pinnacle line for all markets being evaluated. Store as `betTimeLine` in each bet entry. This is CLV insurance — it survives even if closing lines are unavailable at settlement.

### Step 2 — Game-by-Game Analysis
Full MODEL_CORE output format for each game:

1. **Starter True Talent** — xFIP, xERA, K/9, BB/9, season depth, regression weights applied, true_xFIP, divergence flag, TTO split, handedness note, FB%
2. **Run Projection** — show the full math:
   ```
   AWAY: 4.5 × [off_scalar] × [pit_scalar] × [pen_scalar] + [park_adj (FB% modified)] = X.X runs
   HOME: 4.5 × [off_scalar] × [pit_scalar] × [pen_scalar] + [park_adj] = Y.Y runs
   TOTAL PROJ: Z.Z
   F5 PROJ: AWAY A.A (×5/8.5 × durability × tto_adj) / HOME B.B
   ```
3. **Team context** — rolling 7 and 15-game R/G + record, wRC+, bounceback/regression flag, prior-day runs, 1st-inning run rate (NRFI/YRFI), lineup adjustment applied
4. **Poisson Probabilities** — computed live via bash_tool for close calls, reference table for clear cases: P(away wins), P(home wins), P(push), P(over line), P(TT over)
5. **Gate Check** — explicitly list any T1 or T2 gates that fired and how resolved
6. **Market Table** — `Market | Price | Pinnacle VF% | Kalshi% | Model True% | Edge | Conf`
7. **Thesis bullets** — with signal weights in factors{}
8. **Model improvement flags**

### Step 3 — Full Market Scan
**Every market listed below must appear in every game's analysis block — either with an edge figure or a documented rejection reason. Absence is not rejection. A game block missing any market without a written reason is a model failure (Rule 67).**

| Market | Check | Rejection must state |
|---|---|---|
| ML (both sides) | Model% vs Pinnacle VF (primary). Kalshi as tertiary only. If Pinnacle + Kalshi both diverge >15% from model → investigate before sizing [T2]. | Why neither side has edge |
| Run Line (both sides) | Model cover% vs implied. Evaluate independently from ML. Plus-money RL with >50% model cover = log. If ML is -200+, compare RL CLV first (Rule 33). [T1 if ML -200+] | Cover probability and why it doesn't meet threshold |
| Game Total Over | Apply Rule 27/39 decision tree (Section 12). One starter weak → Over may still be live. Do not auto-skip because one starter is elite. | Which step of the decision tree fired and why |
| Game Total Under | K rate primary. Run full three-layer framework (Rule 64). Run Under Pre-Logging Gate (T1 and T2 tiers). | Which gate fired; underBuffer value |
| Team Total — Away Over | Opp pitcher true_xFIP + away offense rolling 7-game R/G + bounceback flag. **Three-layer framework required (Rule 64, applies to Overs too).** Lineup confirmed? [T1 if not]. Analyze regardless of TT line confirmation — Paper if unconfirmed (Rule 44). | Why projection doesn't clear the TT line |
| Team Total — Home Over | Same as Away TT. Confirm TT line before logging Medium/High. [T1] | Same |
| **F5 ML (both sides)** | **Mandatory — model failure if absent (Rule 25). Use Poisson F5 projections (5/8.5 ratio × durability × tto_adj). Log all ≥1.5% edge. Confirm actual price on FD/DK before Medium/High. [T1]** | Edge calculated and reason it fell below 1.5% |
| F5 Total | If market available on FD/DK | N/A if market not offered |
| NRFI | Four-factor composite (Section 15). Run partial-data protocol if any factor missing — do not silently skip. NRFI blocked at total ≥8.0 [T1]. | Which factor(s) fired against NRFI |
| YRFI | Four-factor composite (Section 15). Top-5 1st-inning team = YRFI signal regardless of pitcher. | Why composite doesn't support YRFI |
| K Props | Only if starter confirmed + full Section 10 checklist passes. | Which checklist step failed |

### Step 4 — Under Pre-Logging Gate (Tiered)

**Three-Layer Framework (Rule 64) — run in order before gate checks:**

**Layer 1 — Data Anchor (required inputs, pulled from MLB Stats API via Step 0c):**
- Rolling 15-game R/G for both teams (Rule 65 — primary offense input, not season R/G)
- Bullpen IP last 3 days for both teams (Rule 66 — fatigue check)
- True_xFIP from last-5-start FIP components for both starters
- Under buffer: `buffer = line − total_proj` — log as `underBuffer` in bet entry
- If buffer <1.0 → Paper only. If 1.0–1.49 → Medium max. If ≥1.5 → High eligible. (Rule 63)

**Layer 2 — Qualitative Stress Test (required, one explicit sentence):**
Answer: *"What is the single most likely event that blows up this Under, and what is the probability it happens?"*
- Name the specific risk: bullpen implosion, crooked inning, starter early exit, hot offense carry-over
- If the risk connects to a Layer 1 data flag, reference it explicitly
- If no credible stress-test answer exists → proceed. If the answer is "probable and connected to data" → downgrade one tier
- A bet with no stress test written is incomplete → Paper only [T2]

**Layer 3 — Gate System:**

**Tier 1 Hard Gates (any failure = auto-block at High; log at Medium max)**
1. 🚫 Neither offense top-5 R/G (season ≥5.2 OR rolling 15-game ≥5.5) — Rule 27/30
2. 🚫 Neither opposing starter xERA >5.5 — Rule 27
3. 🚫 Neither team using an opener (unless opener has verified sub-3.00 1st-inning xERA) — Rule 31
4. 🚫 Under line ≤8.0 → cap at Medium (Rule 62). Under line ≤7.5 → cap at Paper unless both starters true_xFIP ≤3.50 AND both teams rolling R/G below season average.

**Tier 2 Soft Gates (each failure = downgrade one tier; 2+ failures = Paper only)**
5. ⚠️ Neither team scored 7+ runs yesterday — Rule 35
6. ⚠️ ML not within 15 cents of pick'em — Rule 22
7. ⚠️ No conflicting ML/F5 thesis conflict — Rule 32
8. ⚠️ Neither team flagged as bounceback candidate — Section 9
9. ⚠️ Park check passed — Coors: both starters sub-2.50 xFIP AND K/9 >9.0
10. ⚠️ Bullpen fatigue: neither team's bullpen threw 5+ IP last 2 days — Rule 66
11. ⚠️ Layer 2 stress test written and risk assessed as low — Rule 64

Log which gates fired and the result. Log `underBuffer` value in bet entry.

### Step 4a — ML Juice Check
Before logging any ML at -200 or worse:
- Pull the RL price for the same side
- If RL is plus money AND model cover >50% → log RL as primary, ML paper only [T1]
- Log both with note: "ML at -2XX juice; RL +XXX logged as primary per Rule 33"

### Step 4b — F5 Price Confirmation Gate [T1]
Before logging any F5 bet at Medium or High confidence:
1. Pull actual F5 line from FD or DK
2. Recalculate edge using live price
3. If actual price >20% more expensive than estimated → recalculate, downgrade tier if needed
4. If live line unavailable → Paper ($1) only
5. Log: "F5 price confirmed: [price] on [book]"

### Step 4c — Same-Game Thesis Conflict Check [T2]
Before logging a total Under on any game where ML or F5 is already logged:
- Estimate implied win score from the ML thesis
- If projected total is within 1 run of Under line → soft gate fires
- Log: "Under within 1 run of ML win projection — [tier downgrade or skip] per Rule 32"

### Step 4d — Pinnacle vs Model Gap Check [T3]
For any bet where model% differs from Pinnacle VF by >10%:
- Flag explicitly
- Check if Kalshi also agrees with Pinnacle against the model
- If both agree vs model: reduce size one tier, keep if qualitative case is strong [T3]
- Note in the bet's notes field

### Step 5 — Poisson Verification for Close-Call Edges
For any bet where calculated edge is within 1% of a tier threshold (e.g., 2.9% edge → is it really Medium or High?):
- Run the Poisson formula directly via bash_tool using the Step 0b script
- Do not rely on table interpolation for tier boundary decisions

### Step 5b — Pre-Output Completeness Checklist
**Run this check before finalizing any session output. If any item fails, the session is incomplete — go back and fix it before pushing.**

- [ ] Every game on the slate has a full market block (ML, RL, Total O/U, Away TT, Home TT, F5 both sides, NRFI, YRFI)
- [ ] No game was pre-screened as "no edge" before Poisson ran — gates fire after math, not before
- [ ] Every TT was Poisson-projected (not just directionally noted)
- [ ] Games with missing Pinnacle lines have full analysis documented and bets logged at Paper
- [ ] All f5Amplified=True plays with xERAGap ≥1.5 were evaluated at the 1.0% threshold (Rule 69)
- [ ] Every market rejection includes a specific reason — "pass" or "no edge" alone is not acceptable
- [ ] Session total documented markets: target ≥ 10 per game × number of games. A 15-game slate should produce 150+ evaluated markets, most rejected with reasons, with 15–30 actionable plays surfaced

**If the session output has fewer than 15 actionable plays on a full slate (15 games), that is a signal the checklist was not followed — review for pre-screening and incomplete market sweeps before pushing.**

### Step 6 — Log, Review, and Push (all at once)
1. Log ALL ≥1.5% edge plays to bets.json as status: PENDING
2. Record `betTimeLine` (current Pinnacle line) for every bet at log time
3. F5 bets: confirm actual price on FD/DK before logging Medium/High [T1]
4. TT bets: confirm actual TT line before logging Medium/High [T1]
5. Size plays per Kelly table using per-tier calibration factors (MODEL_CORE Section 3). Do NOT update factors until N≥50.
6. Apply park factor adjustments numerically (with FB% modifier per MODEL_CORE Section 5)
7. List `gatesFired` in each bet entry
8. In the same response: present full bet log, summary of model signals, improvement proposals
9. Push bets.json + BET_LOG.md to GitHub

---

## Bet Entry Format (bets.json)
```json
{
  "id": "2026-05-28-001",
  "date": "2026-05-28",
  "game": "AWAY @ HOME",
  "market": "F5 ML",
  "bet": "AWAY F5 ML",
  "price": -130,
  "betTimeLine": -132,
  "awayProjRuns": 4.8,
  "homeProjRuns": 3.2,
  "totalProj": 8.0,
  "awayF5Proj": 2.82,
  "homeF5Proj": 1.88,
  "trueProbPct": 62.1,
  "modelPct": 62.1,
  "pinnacleVFPct": 58.5,
  "kalshiPct": 54.0,
  "edgePct": 2.4,
  "size": 5,
  "confidence": "Medium",
  "factors": {"xERAGap": 1.6, "f5Amplified": 1.0, "bullpenVulnerable": 1.0},
  "gatesFired": [],
  "status": "PENDING",
  "result": null,
  "pl": null,
  "betTimeLine": -132,
  "closingLine": null,
  "closingLineSource": null,
  "closingLineTimestamp": null,
  "clv": null,
  "notes": "F5 price confirmed: -130 on FanDuel"
}
```

---

## Calibration Script (run in bash_tool each session)
```python
import json, math

with open('bets.json') as f:
    data = json.load(f)
bets = data if isinstance(data, list) else data.get('bets', [])
settled = [b for b in bets if b.get('result') in ('WIN','LOSS','PUSH')]

tiers = {'High': [], 'Medium': [], 'Paper': []}
for b in settled:
    edge = b.get('edgePct', 0) or 0
    c = b.get('confidence', '')
    if c == 'High' or edge >= 3.0: tiers['High'].append(b)
    elif c == 'Medium' or (2.0 <= edge < 3.0): tiers['Medium'].append(b)
    elif c == 'Paper' or (1.0 <= edge < 2.0): tiers['Paper'].append(b)

for tier, bs in tiers.items():
    wins = sum(1 for b in bs if b.get('result') == 'WIN')
    losses = sum(1 for b in bs if b.get('result') == 'LOSS')
    total = wins + losses
    if total == 0: continue
    wr = wins / total
    se = math.sqrt(wr * (1-wr) / total) if total > 0 else 0
    model_pcts = [b.get('modelPct') or b.get('trueProbPct') or 0 for b in bs]
    model_pcts = [p/100 if p > 1 else p for p in model_pcts]
    expected_wr = sum(model_pcts) / len(model_pcts) if model_pcts else 0
    ratio = wr / expected_wr if expected_wr else 0
    print(f"{tier} (N={total}): WR={wr:.1%} ±{se*1.96:.1%} | Expected={expected_wr:.1%} | Ratio={ratio:.2f}")
    print(f"  {'UPDATE factor' if total >= 50 else 'DO NOT UPDATE — N<50'}")

# Signal type breakdown
signal_counts = {}
for b in settled:
    factors = b.get('factors', {})
    if isinstance(factors, dict):
        for k in factors:
            if k not in signal_counts: signal_counts[k] = {'W':0,'L':0}
            if b.get('result') == 'WIN': signal_counts[k]['W'] += 1
            elif b.get('result') == 'LOSS': signal_counts[k]['L'] += 1
print("\nSignal Type Win Rates:")
for sig, rec in sorted(signal_counts.items(), key=lambda x: -(x[1]['W']+x[1]['L'])):
    total = rec['W'] + rec['L']
    if total >= 2:
        print(f"  {sig}: {rec['W']}W {rec['L']}L ({rec['W']/total:.0%})")
```

---

## Cumulative Record
| Date | W | L | P/L | Bankroll |
|---|---|---|---|---|
| May 21 | 17 | 7 | +$77.00 | |
| May 22 | 3 | 5 | -$19.00 | |
| May 23 | 3 | 5 | -$10.00 | |
| May 24 | 21 | 25 | -$12.97 | |
| May 25 | 14 | 22 | -$50.99 | |
| May 26 | 16 | 7 | +$37.71 | |
| May 27 | 17 | 13 | -$4.04 | |
| May 28 | — | — | pending | |
| May 29 | — | — | pending | |
| May 30 | — | — | pending | |
| **TOTAL** | **91W** | **84L** | **+$17.71** | **$217.71** |

**ROI: +1.7%** *(through May 27 — update after each post-game review)*

> **Note:** This table is a running summary only. `bets.json` in the GitHub repo is the authoritative source of record for all individual bet results, CLV, and P/L. When this table and bets.json disagree, bets.json wins. Update this table after each post-game review session.
