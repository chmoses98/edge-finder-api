# SLATE_WORKFLOW.md
# Last updated: June 3, 2026 — v2.3

---

## ⚠️ NON-NEGOTIABLE SESSION OUTPUT CONTRACT

**This block supersedes all other output guidance. Read before any analysis begins.**

The first and only time a user says "run today's slate", "run the slate", "analyze today's games", or any equivalent phrase, Claude MUST produce ALL of the following in a SINGLE response — no incremental delivery, no asking what format is wanted, no abbreviated pass:

### MANDATORY OUTPUT — EVERY SESSION, EVERY SLATE

**A. Pre-Scan Block (one line per team, ALL teams on slate)**
```
PRE-SCAN: [Team] | Last7 R/G: X.X | Last15 R/G: X.X | Season R/G: X.X | rpgIndex: XXX | Flag: [BOUNCEBACK / REGRESSION / NEUTRAL]
```
If rolling data unavailable, write "rolling unavailable — season baseline used." **Skipping the pre-scan is a model failure. No game analysis may appear before this block.**

**B. Full Game-by-Game Analysis (ALL games, in this exact format)**

For EVERY game on the slate, the output MUST contain ALL of the following sections. A game block missing any section is incomplete and constitutes a Rule 67 / Rule 61 violation:

```
═══════════════════════════════════════════════
[AWAY TEAM] @ [HOME TEAM]
[Away Pitcher] vs [Home Pitcher]
[Weather: temp, wind speed/direction, park factor, dome Y/N]

STARTERS
  [Away Pitcher]: xFIP X.XX → xERA X.XX → true_xFIP X.XXX | K% X BB% X | IP/start X.X | recentFIP X.XX | sig=[signal] | [highWalk flag] | [xERAGap flag]
  [Home Pitcher]: same format OR "TBD — Rule 42 active"

RUN PROJECTION
  AWAY: off_baseline X.XXX (15g:X.X | szn:X.X) → factor X.XXX
        vs [home pitcher]: true_xFIP X.XXX → X.XXX R/inn × X.X IP = X.XXX runs
        + home bullpen xFIP X.XX × X.X IP = X.XXX runs
        → AWAY proj: X.XXX runs
  HOME: off_baseline X.XXX (15g:X.X | szn:X.X) → factor X.XXX
        vs [away pitcher]: true_xFIP X.XXX → X.XXX R/inn × X.X IP = X.XXX runs
        + away bullpen xFIP X.XX × X.X IP = X.XXX runs
        → HOME proj: X.XXX runs
  TOTAL proj: X.XXX | F5: AWAY X.XXX / HOME X.XXX

PROBABILITIES  |  Model  |  PinVF  |  KalVF
  ML away:       X.XXX    X.XXX    X.XXX
  ML home:       X.XXX    X.XXX    X.XXX
  RL away [pt]:  X.XXX
  RL home [pt]:  X.XXX
  F5 ML away:    X.XXX
  F5 ML home:    X.XXX
  NRFI:          X.XXX  |  YRFI: X.XXX

MARKET DECISIONS  (✅ = real money | 📋 = paper | ⬜ = no edge/blocked)
  ✅/📋/⬜  ML away            model=X.XXX PinVF=X.XXX KalVF=X.XXX | raw=±X.XXXX → edge X.XX% | [Tier] $X.XX @ [odds]
  ✅/📋/⬜  ML home            [same format]
  ✅/📋/⬜  RL away [pt]       [same format]
  ✅/📋/⬜  RL home [pt]       [same format]
  ✅/📋/⬜  F5 ML away         [same format]
  ✅/📋/⬜  F5 ML home         [same format]
  ✅/📋/⬜  F5 RL away [pt]    [same format]
  ✅/📋/⬜  F5 RL home [pt]    [same format]
  ✅/📋/⬜  Total Over X.X     [same format]
  ✅/📋/⬜  Total Under X.X    [same format]
  ✅/📋/⬜  [Away] TT Over X.X [same format]
  ✅/📋/⬜  [Away] TT Under X.X[same format]
  ✅/📋/⬜  [Home] TT Over X.X [same format]
  ✅/📋/⬜  [Home] TT Under X.X[same format]
  ✅/📋/⬜  NRFI               [same format OR "BLOCKED — Rule 34: [reason]"]
  ✅/📋/⬜  YRFI               [same format OR "BLOCKED — Rule 34: [reason]"]

Gates fired (list any T1/T2 triggers):
  [Rule XX: description] or "None"

Rule 76 Stack Check:
  [N bets this game] | Correlated: [Yes→reduced/No] | Aggregate: $X.XX

WRITTEN THESIS (mandatory per Rule 61 — one sentence per real/paper bet, readable by a bettor):
  → [Market]: [Specific reason why the line is mispriced, what the model sees, what the market is missing]
  → [Market]: [Same]
  → No bets this game: [Specific reason why no market cleared edge threshold]
```

**C. Slate Output — Two sections, in this order**

**C1. BET SLIP — sorted by game time (earliest game first)**

All real-money bets in one table. Columns in this exact order:

```
| # | Game | Time (ET) | Bet | Size | Kalshi | Edge | Conf |
|---|---|---|---|---|---|---|---|
| 1 | NYY @ BOS | 7:10p | NYY F5 ML | $5.00 | +118 | 2.8% | 🟢 High |
| 2 | NYY @ BOS | 7:10p | NYY TT Over 4.5 | $3.75 | -112 | 2.1% | 🟡 Med |
| 3 | MIL @ PHI | 7:15p | PHI ML | $3.00 | -138 | 1.9% | 🟡 Med |
```

- Bet numbers (#) are sequential across the entire slate — never reset per game
- Size = quarter Kelly dollar amount, calculated per MODEL_CORE Section 4
- Games with multiple bets appear as consecutive rows (same game, same time)
- Games sorted by first pitch time ET, earliest first
- **Total real exposure: $XX.XX** shown below the table

**C2. PAPER BETS — separate table below real bets**

Same column format. Size always $1.00. Numbered sequentially continuing from real bets.

```
| # | Game | Time (ET) | Bet | Size | Kalshi | Edge | Conf |
|---|---|---|---|---|---|---|---|
| 6 | SD @ LAD | 10:10p | SD ML | $1.00 | +162 | 1.7% | 📋 Paper |
```

- Any Rule 71 blocks documented here with "Skip" status and reason
- Any market capped to Paper by gate (Rule 62, 63, 78, 81) listed here

**C3. GAME ANALYSIS — full block for every game, sorted by game time**

Header format for games WITH real bets:
`[BETS #X–Y] AWAY @ HOME — H:MMp ET`

Header format for games with ONLY paper bets:
`[NO REAL BETS | PAPER #X] AWAY @ HOME — H:MMp ET`

Header format for games with NO qualifying bets at all:
`[NO BETS] AWAY @ HOME — H:MMp ET`

Full analysis block under each header per MODEL_CORE Section 7 and SLATE_WORKFLOW Step 2 format. Every game appears — silence is not rejection.

### ENFORCEMENT

**If the user receives anything less than A + B + C on the first ask, the session has failed.** There is no acceptable abbreviated first pass that is "corrected later." The workflow, calibration, and bet log are all triggered from the complete first-pass output.

**Common failures that are PROHIBITED:**
1. Running only Poisson math and showing a bet table without written thesis per game — Rule 61 violation
2. Showing analysis for some games and skipping others — Rule 67 violation
3. Asking the user how much detail they want — the answer is always: full analysis, every game, every market
4. Producing a "first pass" and waiting for user to ask for corrections — there is no second pass
5. Logging bets to GitHub before the full written output is produced and confirmed complete
6. Skipping the pre-scan block
7. Showing any game without all 16+ market rows in the MARKET DECISIONS table

**The three-question completeness test (run internally before output):**
- Does every game have a WRITTEN THESIS with a human-readable sentence per bet?
- Does every game have all 16 market rows (some ⬜, some ✅/📋, but all present)?
- Is the pre-scan block present before the first game?

If any answer is No → do not output. Go back and complete the missing sections first.

---


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
3. Settle every bet (W/L/Push, P&L, CLV from Kalshi historical prices — pull closing line for each bet via Kalshi historical API at first-pitch timestamp)
4. Update bets.json and regenerate BET_LOG.md
5. Update signal-type win rate table in MODEL_CORE Section 3
6. Flag model improvement areas based on what hit/missed and why
7. Propose specific rule or algorithm edits with canonical examples
8. Push updated bets.json + BET_LOG.md to GitHub
9. **Present CLV Summary Block** (mandatory — see format below)

After presenting, wait for user approval on proposed model changes, then push updated model files.

### CLV Summary Block (present in every post-game review)

This block is required in every review output. Do not skip even if CLV is null for some bets.

```
## CLV Summary — [DATE]

| Bet | Market | Price | Closing | CLV% | Result |
|-----|--------|-------|---------|------|--------|
| TEAM ML | ML | -145 | -155 | +2.1% | WIN |
| TEAM RL | RL | +130 | +122 | -1.8% | LOSS ⚠️ |
| ...   |    |       |         |      |        |

Rolling 30-bet avg CLV: +X.X% [HEALTHY / WARNING / RED FLAG]
Rolling 100-bet avg CLV: +X.X% [HEALTHY / WARNING / RED FLAG]

Flags:
- ⚠️ Negative CLV + Loss: [BET ID] — autopsy required
- ℹ️ Flat CLV (round-trip): [BET ID] — monitor
- ✅ No flags (if clean slate)
```

**Rules for this block:**
- Show every bet settled this session, including nulls (log as `—` if closing line unavailable)
- Calculate rolling averages from all settled bets in bets.json with non-null CLV
- Health status per MODEL_CORE Section 17 targets: ≥+1.5% = HEALTHY, +0.5–1.4% = WARNING, <+0.5% = RED FLAG
- Any Negative CLV + Loss must be flagged inline with ⚠️ and listed in Flags with autopsy note
- Present this block before model improvement proposals — CLV drives the agenda

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
4. **Pull closing lines from Kalshi historical prices.** For each settled bet, retrieve the Kalshi price for the relevant market at or just before first pitch. Log to `closingLine`, `closingLineSource: "Kalshi"`, `closingLineTimestamp: "{first_pitch_utc}"`.
   - Applies to ALL markets: ML, RL, Game Total, Team Total, F5 ML, F5 RL, NRFI, YRFI — all on Kalshi.
   - **If Kalshi historical pull fails:** use `betTimeLine` (Kalshi price at bet time) as closing line proxy → flag as `closingLineSource: "betTimeLine_proxy"`
   - Log `closingLine: null`, `clv: null` only if betTimeLine is also unavailable. Never fabricate.
   - **Settlement window:** 7 days. Kalshi historical data is stable — no degradation.
   - OddsPortal is NOT used. The Odds API historical endpoint is NOT used. Kalshi historical is the sole CLV source.
5. Calculate CLV% from closing line. Log to `clv`.
6. Recalculate cumulative summary (record, P/L, ROI, bankroll)
7. Update signal-type win rate table (MODEL_CORE Section 3) — this is the per-session calibration leading indicator
8. Run tier-level calibration check. If any tier reaches 50+ settled bets: recalculate factor per the formula. Even below 50: run per-tier win rate analysis and compare to calibration table. Do NOT update factors until N≥50.
9. Update bets.json with all settled results
10. Regenerate BET_LOG.md from bets.json
11. Push bets.json + BET_LOG.md to GitHub
12. **Present CLV Summary Block** (same format as post-game trigger — mandatory)
13. **Simultaneously:** Flag model adjustment lessons → identify patterns → propose RULES.md and MODEL_CORE.md additions if pattern is clear

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

### CORE PRINCIPLE — MARKET INDEPENDENCE
Every market is evaluated independently on every game. A game with no ML edge is not a game with no edge — the RL, TT, F5, NRFI, and YRFI are completely separate bets driven by separate probabilities. The ML line has zero bearing on whether those markets have edge.

**Pre-screening entire games based on ML juice or ML edge is a model failure.** Run the full market block on every game, every time. The goal is to find every +EV bet on the slate regardless of which market it lives in.

---

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
