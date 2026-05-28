# SLATE_WORKFLOW.md
# Last updated: May 28, 2026 — v2.0

## Session Start — Pull Model Files from GitHub
Pull latest before anything else:
- RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md, bets.json

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
4. Pull Pinnacle closing line via web search for each bet (search "Pinnacle closing line [TEAM] ML [DATE]" or use OddsPortal/Action Network fallback). Log to `closingLine`, `closingLineSource`, `closingLineTimestamp`. If not found → log null, never fabricate.
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
Before analyzing any individual game, scan all teams on the slate:
- Pull last 7 and last 15 game R/G for each team
- Compare to season xOPS / wRC+ / barrel%
- **Bounceback flag:** recent results worse than underlying metrics + facing weak starter = offensive upside likely underpriced
- **Regression flag:** recent results better than underlying metrics + facing elite starter = normalize signal
- Log flags next to each team's context line. Feed directly into TT, total, and ML evaluations.

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
Do not skip any market without explicitly stating why it has no edge.

| Market | Check |
|---|---|
| ML | Model% vs Pinnacle VF (primary). Kalshi as tertiary only. If Pinnacle + Kalshi both diverge >15% from model → investigate before sizing [T2]. |
| Run Line | Model cover% vs implied. Evaluate independently from ML. Plus-money RL with >50% model cover = log. If ML is -200+, compare RL CLV first (Rule 33). [T1 if ML -200+] |
| Game Total | K rate primary. Run Under Pre-Logging Gate (T1 and T2 tiers). Lopsided matchup (elite offense vs garbage starter + opposing elite pitcher) → TT Over, not game total Over (Rule 39). [T1] |
| Team Total — Away | Opp pitcher true_xFIP + away offense rolling 7-game R/G + bounceback flag. Lineup confirmed? [T1 if not] |
| Team Total — Home | Same. Confirm TT line before logging Medium/High. [T1] |
| **F5 ML** | **Mandatory. Use Poisson F5 projections (5/8.5 ratio × durability × tto_adj). Log all ≥1.5% edge. Confirm actual price on FD/DK before Medium/High. [T1]** |
| F5 Total | If available |
| NRFI/YRFI | Four-factor composite (MODEL_CORE Section 15). Both teams' 1st-inning run rate required. NRFI blocked at total ≥8.0 [T1]. Top-5 1st-inning team = YRFI signal regardless of pitcher [T1 for NRFI]. |
| K Props | Only if starter confirmed + full checklist passes (Section 10). |

### Step 4 — Under Pre-Logging Gate (Tiered)

**Tier 1 Hard Gates (any failure = auto-block at High; log at Medium max)**
1. 🚫 Neither offense top-5 R/G (season ≥5.2 OR rolling 15-game ≥5.5) — Rule 27/30
2. 🚫 Neither opposing starter xERA >5.5 — Rule 27
3. 🚫 Neither team using an opener (unless opener has verified sub-3.00 1st-inning xERA) — Rule 31

**Tier 2 Soft Gates (each failure = downgrade one tier; 2+ failures = Paper only)**
4. ⚠️ Neither team scored 7+ runs yesterday — Rule 35
5. ⚠️ ML not within 15 cents of pick'em — Rule 22
6. ⚠️ No conflicting ML/F5 thesis conflict — Rule 32
7. ⚠️ Neither team flagged as bounceback candidate — Section 9
8. ⚠️ Park check passed — Coors: both starters sub-2.50 xFIP AND K/9 >9.0

Log which gates fired and the result.

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
| **TOTAL** | **91W** | **84L** | **+$17.71** | **$217.71** |

**ROI: +1.7%**

> Note: Per-bet tracking begins May 26. May 27 excludes 11 Team Total bets pending TT line verification.
