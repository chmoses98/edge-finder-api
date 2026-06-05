# SLATE_WORKFLOW.md
# Last updated: June 3, 2026 — v2.3

---

## ⚠️ NON-NEGOTIABLE SESSION OUTPUT CONTRACT

**This block supersedes all other output guidance. Read before any analysis begins.**

## MANDATORY SESSION OPENING — "RUN THE SLATE"

When the user says "run the slate" (or any equivalent), Claude MUST execute ALL of the following steps in order, with NO abbreviation, NO skipping, and NO asking for clarification first. This is the complete automated workflow:

### STEP A: Pull model files from GitHub (REQUIRED FIRST)
Pull these 4 files from raw.githubusercontent.com before ANY analysis:
- `RULES.md`, `MODEL_CORE.md`, `SLATE_WORKFLOW.md`, `DATA_SOURCES.md`
Token: `${GITHUB_TOKEN} (stored in repo secret WORKFLOW_TOKEN)` | Repo: `chmoses98/edge-finder-api`

### STEP B: Trigger fetch-slate GitHub Action
POST to `/actions/workflows/fetch-slate.yml/dispatches` with `{"ref":"main"}`. Wait 90 seconds. Then pull `data/slate.json` AND `data/kalshi_search.json` from the repo. **Never call Vercel API directly (403).**

### STEP C: Pull kalshi_search.json for all market prices
`data/kalshi_search.json` contains all 726 Kalshi markets across 8 series:
`KXMLBGAME` (ML) | `KXMLBSPREAD` (RL) | `KXMLBTOTAL` (Game Total) | `KXMLBTEAMTOTAL` (TT) | `KXMLBF5` (F5 ML) | `KXMLBF5SPREAD` (F5 RL) | `KXMLBF5TOTAL` (F5 Total) | `KXMLBRFI` (NRFI/YRFI)
Index ALL markets by event_ticker before starting game analysis.

### STEP D: Run Poisson engine via bash_tool
For every game compute: `a_proj`, `h_proj`, `a_f5`, `h_f5`, `total_proj` using:
- Offense baseline: `L7×0.30 + L15×0.30 + Szn×0.40` (NO bounceback flip)
- Starter true_xFIP: `(rec_FIP×weight + szn_xFIP×weight)` per MODEL_CORE Section 3
- Run projection: `off_factor × (starter_IP × txFIP/9 + pen_IP × pen_xFIP/9) ± park`

### STEP E: Evaluate ALL 8 markets on EVERY game
For each game, evaluate in this order. **Silence is not rejection — every market gets a row:**
1. **NRFI** — from `KXMLBRFI`. Blocked by Rule 34 if Kalshi total line ≥8.0. Otherwise evaluate.
2. **YRFI** — same market, complement probability. Always evaluate alongside NRFI.
3. **F5 ML (both sides)** — from `KXMLBF5`. Three-way market: normalize VF over away+home+tie implied. Both sides get independent edge calc. Rule 77: if both qualify, log higher edge as real, lower as paper.
4. **F5 Total** — from `KXMLBF5TOTAL`. Best qualifying line only.
5. **F5 RL** — from `KXMLBF5SPREAD`. Paper/evaluating status.
6. **Team Totals (both teams)** — from `KXMLBTEAMTOTAL`. Best qualifying line per team. No pin_div check — TT has no sharp reference. Edge ≥2.0% = real money.
7. **ML (both sides)** — from `KXMLBGAME`. Rule 71: block if |model% − PinVF%| > 8%.
8. **Game Total** — from `KXMLBTOTAL`. Best qualifying Over line. Paper-only (Rule 71, WR 41%).
9. **RL** — from `KXMLBSPREAD`. Paper/suspended (Rule 81).

**Rule 71 applies ONLY to ML (vs true Pinnacle VF) and F5 ML (vs FanDuel F5 VF). It does NOT apply to TT, NRFI, YRFI, F5 Total, Game Total, or RL.**

### STEP F: Edge thresholds and sizing
- HIGH ≥3.0% calibrated (cal factor 0.187): real, $4–6 base × market multiplier
- MEDIUM ≥1.5% calibrated (cal factor 0.255): real, $3–4 base × market multiplier
- PAPER ≥1.0% calibrated (cal factor 0.18): paper $1.00 always
- F5 amp (xERA gap ≥1.5): MEDIUM threshold drops to 1.0%
- Market multipliers: ML×1.0 | F5 ML×1.5 | TT×1.25 | YRFI×1.25 | NRFI×1.0 | F5 Total×1.0

### STEP G: Output format (mandatory, no abbreviation)
**C1. BET SLIP** — all real bets sorted by game time, table format
**C2. PAPER BETS** — all paper bets, table format with Rule note
**C3. GAME ANALYSIS** — every game gets a block with:
  - Starter true_xFIPs and baseline blends
  - Run projection table (a_proj / h_proj / total / F5 each side)
  - All 9 market rows with: model% | Kal% | edge | conf | size | Kalshi price
  - One-sentence written thesis per qualifying bet

### STEP H: Log all qualifying bets to bets.json
After output is complete and confirmed, push to GitHub. Real bets status="open". Paper bets type="paper".

**A "run the slate" session that produces fewer than 12 qualifying bets (real + paper) on a full 14-game slate is a model failure. Re-examine Rule 71 applications and ensure all 8 market series are indexed before concluding.**

### Step 0 — Bounceback/Regression Pre-Scan
**This step is a hard prerequisite. No individual game analysis may begin until this pre-scan output is documented in the session response.** If the pre-scan is absent from the output, the session is incomplete regardless of how many bets are logged.

Before analyzing any individual game, scan all teams on the slate:
- Pull last 7 and last 15 game R/G for each team
- Compare to season R/G and barrel% / hard-hit rate where available
- **Bounceback flag:** recent results worse than underlying metrics + facing weak starter = offensive upside likely underpriced
- **Regression flag:** recent results better than underlying metrics + facing elite starter = normalize signal
- Log flags next to each team's context line. Feed directly into TT, total, and ML evaluations.

**Required output format for pre-scan (must appear before game-by-game analysis):**
```
PRE-SCAN: [Team] | Last7 R/G: X.X | Last15 R/G: X.X | Season R/G: X.X | rpgIndex: XXX | Flag: [BOUNCEBACK / REGRESSION / NEUTRAL]
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

**Offense baseline blend (updated v2.5):** `offense_baseline = (last7_R/G × 0.30) + (last15_R/G × 0.30) + (season_R/G × 0.40)`. Log all three components. No divergence flip applied — the three-way blend handles hot/cold stretches naturally. Flag BOUNCEBACK/REGRESSION as qualitative context only (Section 9).

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
   - `adj = (today_confirmed_lineup_avg_OPS − season_team_OPS) × 2.0` (capped at ±0.3 R/G)
5. Identify handedness composition (% LHH vs RHH) → feed into Step 3 of run projection
6. Flag missing key bats (projected OPS >.900): −0.05 offense scalar
7. If lineup unconfirmed: use season baseline, note "lineup unconfirmed — using season baseline" — TT bets must be Paper only [T1]

### Step 1e — betTimeLine Capture
At the start of analysis for each game, record the current Kalshi line for all markets being evaluated. Store as `betTimeLine` in each bet entry. This is CLV insurance — it survives even if the historical API pull fails at settlement. Also record `pinnacleVFAtBet` (Pinnacle VF at this moment) for model validation.

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
3. **Team context** — rolling 7 and 15-game R/G + record, season R/G (rpgIndex), bounceback/regression flag, prior-day runs, 1st-inning run rate (NRFI/YRFI), lineup adjustment applied
4. **Poisson Probabilities** — computed live via bash_tool for close calls, reference table for clear cases: P(away wins), P(home wins), P(push), P(over line), P(TT over)
5. **Gate Check** — explicitly list any T1 or T2 gates that fired and how resolved
6. **Market Table** — `Market | Kalshi Price | Kalshi Implied% | Pinnacle VF% | Model True% | Edge | Conf`
7. **Thesis bullets** — with signal weights in factors{}
8. **Model improvement flags**

### Step 3 — Full Market Scan
**Every market listed below must appear in every game's analysis block — either with an edge figure or a documented rejection reason. Absence is not rejection. A game block missing any market without a written reason is a model failure (Rule 67).**

| Market | Check | Rejection must state |
|---|---|---|
| ML (both sides) | Model% vs Kalshi implied. Evaluate independently — no ML edge does NOT skip the game. Log if edge ≥1.5%. If Kalshi + Pinnacle both diverge >15% from model → investigate [T2]. | Why neither side has edge — must be explicit, not blank |
| Run Line (both sides) | Model cover% vs implied. Evaluate independently from ML. Plus-money RL with >50% model cover = log. If ML is -200+, compare RL CLV first (Rule 33). [T1 if ML -200+] | Cover probability and why it doesn't meet threshold |
| Game Total Over | Apply Rule 27/39 decision tree (Section 12). One starter weak → Over may still be live. Do not auto-skip because one starter is elite. | Which step of the decision tree fired and why |
| Game Total Under | K rate primary. Run full three-layer framework (Rule 64). Run Under Pre-Logging Gate (T1 and T2 tiers). | Which gate fired; underBuffer value |
| Team Total — Away Over | Opp pitcher true_xFIP + away offense rolling 7-game R/G + bounceback flag. **Three-layer framework required (Rule 64, applies to Overs too).** Lineup confirmed? [T1 if not]. Analyze regardless of TT line confirmation — Paper if unconfirmed (Rule 44). | Why projection doesn't clear the TT line |
| Team Total — Home Over | Same as Away TT. Confirm TT line before logging Medium/High. [T1] | Same |
| **F5 ML (both sides)** | **Mandatory — model failure if absent (Rule 25). Use Poisson F5 projections (5/8.5 ratio × durability × tto_adj). Log all ≥1.5% edge. Confirm Kalshi price before Medium/High. [T1]** | Edge calculated and reason it fell below 1.5% |
| F5 Total | If market available on Kalshi | N/A if not posted on Kalshi for this game |
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
1. Pull actual F5 line from Kalshi
2. Recalculate edge using live Kalshi price
3. If actual price >20% more expensive than estimated → recalculate, downgrade tier if needed
4. If Kalshi line unavailable for this game → Paper ($1) only
5. Log: "F5 price confirmed: [price] on Kalshi"

### Step 4c — Same-Game Thesis Conflict Check [T2]
Before logging a total Under on any game where ML or F5 is already logged:
- Estimate implied win score from the ML thesis
- If projected total is within 1 run of Under line → soft gate fires
- Log: "Under within 1 run of ML win projection — [tier downgrade or skip] per Rule 32"

### Step 4d — Pinnacle vs Model Gap Check [T3]
For any bet where model% differs from Pinnacle VF by >10%:
- Flag explicitly and document why the model diverges from the sharpest market
- Check if Kalshi also agrees with Pinnacle against the model
- If BOTH Kalshi and Pinnacle agree against the model: reduce size one tier (Rule 28/71) — two sharp signals disagreeing with the model is a meaningful warning
- If Pinnacle disagrees but Kalshi still offers the edge: investigate the Pinnacle divergence, but do not automatically downgrade — Kalshi is the bet target and may simply be less efficient
- Note finding in the bet's notes field

### Step 5 — Poisson Verification for Close-Call Edges
For any bet where calculated edge is within 1% of a tier threshold (e.g., 2.9% edge → is it really Medium or High?):
- Run the Poisson formula directly via bash_tool using the Step 0b script
- Do not rely on table interpolation for tier boundary decisions

### Step 5b — Pre-Output Completeness Checklist
**Run this check before finalizing any session output. If any item fails, the session is incomplete — go back and fix it before pushing.**

- [ ] Every game on the slate has a full market block (ML, RL, Total O/U, Away TT, Home TT, F5 both sides, NRFI, YRFI)
- [ ] No game was pre-screened as "no edge" before Poisson ran — gates fire after math, not before
- [ ] No market was skipped because the ML on that game had no edge — market independence is absolute
- [ ] Every TT was Poisson-projected (not just directionally noted)
- [ ] Games with missing Pinnacle lines have full analysis documented and bets logged at Paper
- [ ] All f5Amplified=True plays with xERAGap ≥1.5 were evaluated at the 1.0% threshold (Rule 69)
- [ ] Every market rejection includes a specific reason — "pass" or "no edge" alone is not acceptable
- [ ] Session total documented markets: target ≥ 10 per game × number of games. A 15-game slate should produce 150+ evaluated markets, most rejected with reasons, with 15–30 actionable plays surfaced

**If the session output has fewer than 15 actionable plays on a full slate (15 games), that is a signal the checklist was not followed — review for pre-screening and incomplete market sweeps before pushing.**

### Step 5c — Paper Bet Promotion Check (1–2 hours before first pitch)
Before finalizing the session output, re-examine any bet logged at Paper due to an unconfirmed F5 or TT line:
1. Pull the current Kalshi F5 price or TT line for each Paper-only bet flagged with a line-confirmation hold
2. If the line is now confirmed on Kalshi AND recalculated edge still clears the tier threshold → promote to Medium or High, update size, log the confirmation
3. If the line is confirmed but edge has degraded below threshold → keep at Paper or remove
4. Log the promotion decision in the notes field: "Promoted from Paper: F5 price confirmed -128 on Kalshi at 2:15pm ET"
5. This step must be run for every session with afternoon/evening games where early analysis ran before lines were posted

---

### Step 6 — Log, Review, and Push (all at once)
1. Log ALL ≥1.5% edge plays to bets.json as status: PENDING
2. Record `betTimeLine` (current Kalshi price) and `pinnacleVFAtBet` (current Pinnacle VF) for every bet at log time
3. F5 bets: confirm actual Kalshi price before logging Medium/High [T1]
4. TT bets: confirm actual TT line before logging Medium/High [T1]
5. **Stack Check [T1] — required before logging any game with 2+ bets:**
   - For each game with multiple bets queued: identify which are correlated (same thesis) vs. independent angles
   - Correlated cluster (ML + RL + F5 + TT all on same team) → pick best single market at real size; downgrade rest to Paper
   - Aggregate game exposure must not exceed 1× High-confidence bet size ($8 max)
   - Document in output: `STACK CHECK: [N bets] | Correlated: [Yes→reduced/No] | Aggregate: $X`
   - Logging 2+ real-size bets on same game without this output block is a Rule 76 violation [T1]
6. Size plays per Kelly table using per-tier calibration factors (MODEL_CORE Section 3). Do NOT update factors until N≥50.
7. Apply park factor adjustments numerically (with FB% modifier per MODEL_CORE Section 5)
8. List `gatesFired` in each bet entry
9. In the same response: present full bet log, summary of model signals, improvement proposals
10. Push bets.json + BET_LOG.md to GitHub

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
  "underBuffer": null,
  "gatesFired": [],
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "closingLineSource": null,
  "closingLineTimestamp": null,
  "clv": null,
  "notes": "F5 price confirmed: -130 on Kalshi"
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

# Per-market CLV averages (MODEL_CORE Section 17 targets)
# Targets: ML ≥+1.0%, RL ≥+1.5%, Game Total ≥+1.0%, TT ≥+1.5%, NRFI/YRFI ≥+1.5%, F5 ≥+1.5%
CLV_TARGETS = {'ML': 1.0, 'Run Line': 1.5, 'Game Total': 1.0, 'Team Total': 1.5,
               'NRFI': 1.5, 'YRFI': 1.5, 'F5 ML': 1.5, 'F5 RL': 1.5}
clv_by_market = {}
for b in settled:
    mkt = b.get('market', 'Unknown')
    clv = b.get('clv')
    if clv is not None:
        if mkt not in clv_by_market: clv_by_market[mkt] = []
        clv_by_market[mkt].append(float(clv))
print("\nPer-Market CLV Averages (vs targets from MODEL_CORE Section 17):")
MIN_SAMPLE = {'ML': 30, 'Run Line': 20, 'Game Total': 20, 'Team Total': 15,
              'NRFI': 15, 'YRFI': 15, 'F5 ML': 20, 'F5 RL': 20}
for mkt, clvs in sorted(clv_by_market.items()):
    avg = sum(clvs) / len(clvs)
    n = len(clvs)
    target = CLV_TARGETS.get(mkt, 1.0)
    min_n = MIN_SAMPLE.get(mkt, 15)
    if avg >= target: status = "✅ HEALTHY"
    elif avg >= 0.5: status = "⚠️ WARNING"
    else: status = "🚨 RED FLAG"
    signal_str = f"N={n}" + (" (below min sample)" if n < min_n else "")
    print(f"  {mkt}: avg CLV {avg:+.2f}% [{signal_str}] — target ≥{target}% → {status}")

# Rolling 30 and 100 bet CLV (all markets combined)
all_clvs = [float(b['clv']) for b in settled if b.get('clv') is not None]
if all_clvs:
    r30 = all_clvs[-30:] if len(all_clvs) >= 30 else all_clvs
    r100 = all_clvs[-100:] if len(all_clvs) >= 100 else all_clvs
    avg30 = sum(r30) / len(r30)
    avg100 = sum(r100) / len(r100)
    def clv_health(avg):
        if avg >= 1.5: return "HEALTHY ✅"
        elif avg >= 0.5: return "WARNING ⚠️"
        else: return "RED FLAG 🚨"
    print(f"\nRolling 30-bet CLV: {avg30:+.2f}% [{clv_health(avg30)}]")
    print(f"Rolling 100-bet CLV: {avg100:+.2f}% [{clv_health(avg100)}]")
    if avg30 < 0.5:
        print("  ⛔ RED FLAG PROTOCOL: Pause new bets pending review (MODEL_CORE Section 17)")
```

---

## Cumulative Record

> **Source of truth: `bets.json`.** Do not maintain this table manually — it becomes stale immediately. At session start, run the calibration script in bash_tool against `bets.json` to get the current record. The table below is a historical reference only and is not updated each session.

**As of June 4, 2026:** 121W 106L 7P | P/L: +$6.38 | ROI: +0.3% (all markets combined)

Per-market summary (current):
- F5 ML: 56% WR, CLV +2.31% ✅ Best performing
- Team Total: 57% WR (TT Under performing well)
- ML: 55% WR, CLV -1.58% ⚠️ Multiplier suspended
- Run Line: 36% WR, CLV -4.09% 🚨 SUSPENDED — paper only
- Game Total: 41% WR, CLV -1.43% 🚨 Paper only

> Always derive current record from `bets.json` — never from this section.
