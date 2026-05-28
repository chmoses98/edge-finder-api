# SLATE_WORKFLOW.md

## Session Start — Pull Model Files from GitHub
Pull latest before anything else:
- RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md, bets.json

---

## One-Command Post-Game Trigger
When the user says **"post-game review"** or **"review today's slate"**, automatically execute ALL of the following in one pass — do not split into separate steps:

1. Pull all 4 model files + bets.json from GitHub
2. Fetch scores + box scores for every PENDING game via `fetch_sports_data` (get both scores and game_stats)
3. Settle every bet (W/L/Push, P&L, CLV where possible) including Team Totals from slate data
4. Update bets.json and regenerate BET_LOG.md
5. Flag model improvement areas based on what hit/missed and why
6. Propose specific rule or algorithm edits with canonical examples
7. Push updated bets.json + BET_LOG.md to GitHub

**The bet log update, results review, and model improvement proposals all happen together in one response.** User provides zero data. Claude pulls everything autonomously and presents results + improvements simultaneously.

After presenting, wait for user approval on proposed model changes, then push updated model files to GitHub.

---

## Pre-Slate Review (run first, every session)
1. Pull yesterday's results via `fetch_sports_data` — scores and game_stats for all relevant game IDs
2. Pull box scores for ALL pending bets — get inning-by-inning linescore for NRFI/YRFI/F5 settlement
   → NRFI/YRFI: check inning 1 linescore — did a run score?
   → F5 ML: sum innings 1–5 for both teams
   → Totals: verify final score
   → Team Totals: verify team's final run total vs logged TT line
   → Never ask the user for results — always pull box score directly
3. Mark each pending bet WIN/LOSS/PUSH, record P/L, record closing line → calculate CLV
4. Recalculate cumulative summary (record, P/L, ROI, bankroll)
5. Run calibration check — if 30+ settled bets in any edge bucket, recalculate factor
6. Update bets.json with all settled results
7. Regenerate BET_LOG.md from bets.json
8. Push bets.json + BET_LOG.md to GitHub
9. **Simultaneously with steps 6–8:** Flag model adjustment lessons → identify patterns → propose RULES.md and MODEL_CORE.md additions if pattern is clear

---

## Slate Data Fetch
1. Trigger `fetch-slate` GitHub Action:
   ```
   POST https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
   Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
   ```
2. Wait ~40 seconds
3. Verify `data/meta.json` fetchedAt matches today
4. Read `data/slate.json` — this is the source of truth for all analysis
5. If Action fails → fall back to fallback chain in DATA_SOURCES.md

---

## Slate Analysis

### Step 0 — Bounceback/Regression Pre-Scan [NEW]
Before analyzing any individual game, run a quick scan of all teams on the slate:
- Pull last 7 and last 15 game R/G for each team
- Compare to season xOPS / wRC+ / barrel% profile
- Flag any team where recent results diverge significantly from underlying quality
- **Bounceback flag:** recent results worse than underlying metrics + facing weak starter = offensive upside likely underpriced
- **Regression flag:** recent results better than underlying metrics + facing elite starter = offensive output likely to normalize
- Log these flags next to each team's context line in the game-by-game analysis
- These flags feed directly into TT, total, and ML market evaluations

### Step 1 — Starter Confirmation
For every game:
- Confirm same-day starters. Flag TBD starters — model confidence drops to LOW for that game.
- Flag any pitcher averaging <3 IP/start as opener role → check `pitcher.firstInningSplit`
- If opener xERA unavailable or sample <5: mark F5 and K props UNQUALIFIED
- Also flag game total Under as suspect when either team uses an opener (Rule 31)

### Step 1a — Prior-Day Offense Scan
Before analyzing any totals, pull yesterday's box scores for all teams on today's slate:
- Flag any team that scored 7+ runs yesterday → Under on their game today requires extra justification
- Log the prior-day run total next to each team's context line
- If flagged team faces a starter with K/9 <9.0 OR BB/9 >3.0, skip the Under or log paper only

### Step 1b — 1st Inning Run Rate Pull [NEW]
For every game where NRFI or YRFI is being considered:
- Pull each team's season and last-15-game 1st-inning runs scored per game
- Flag any team in the top 5 in 1st-inning run rate as a YRFI signal
- This is a hard gate: do not log NRFI against a top-5 1st-inning offense without confirmed dual sub-3.00 1st-inning xERA

### Step 2 — Game-by-Game Analysis
Run the full MODEL_CORE output format for each game. For every game produce:
1. Pitcher matchup (xERA, K%, BB%, whiff%, last 3 starts, 1st-inning xERA for NRFI/YRFI games)
2. Team context (rolling 7 and 15-game R/G + record, season context, **bounceback/regression flag**, **prior-day runs scored**, **1st-inning run rate for NRFI/YRFI**)
3. Full market table covering ALL markets below

### Step 3 — Full Market Scan (mandatory for every game)
Do not skip any market without explicitly stating why it has no edge.

| Market | Check |
|---|---|
| ML | Model% vs Kalshi + Pinnacle VF. If divergence >15% → investigate before sizing (Rule 37) |
| Run Line | Model cover% vs implied. Evaluate independently from ML. Plus-money RL with >50% model cover = log it. If ML is -200+, compare RL CLV first (Rule 33). |
| Game Total | K rate primary. Rule 17/27/30 check for elite starter vs elite offense. **Run Under Pre-Logging Gate (MODEL_CORE) before logging any Under.** Lopsided matchup (elite offense vs garbage starter + opposing elite pitcher) → redirect to TT Over, not game total Over (Rule 39). |
| Team Total — Away | Opp pitcher xERA + away offense rolling 7-game R/G + bounceback flag |
| Team Total — Home | Opp pitcher xERA + home offense rolling 7-game R/G + bounceback flag |
| **F5 ML** | **Mandatory. Use `game.f5.awayF5Pct/homeF5Pct`. Log all ≥1.5% edge. F5 is independent from ML/RL.** |
| F5 Total | If available |
| NRFI/YRFI | Run full four-factor composite (MODEL_CORE). Both teams' 1st-inning run rate required. Opener = YRFI default. NRFI blocked if total ≥ 8.0 unless dual sub-3.00 1st-inning xERA confirmed (Rule 34). Top-5 1st-inning team = YRFI signal regardless of pitcher (Rule 40). |
| K Props | Only if starter confirmed + full checklist passes |

### Step 4 — Elite Offense vs Garbage Starter Check (Rule 27/30)
Before logging any Under on a game where one team has:
- Opposing starter xERA > 5.5, AND
- Own team R/G > 5.0 OR rolling 15-game R/G elevated

→ **BLOCK the Under at High confidence.** Model that half of the game separately. If projected runs from the elite offense alone approach the total line, log the TT Over or alt lines at plus money instead of the game total Over.

### Step 4a — Under Pre-Logging Gate
Run this checklist before logging ANY Under at Medium or High confidence:
1. ✅ Rule 27/30: Neither offense top-5 R/G AND neither opposing starter xERA >5.5
2. ✅ Rule 31: Neither team using an opener (or opener has verified sub-3.00 1st-inning xERA)
3. ✅ Rule 35: Neither team scored 7+ runs yesterday (or both starters are 9+ K/9, BB/9 <3.0)
4. ✅ Rule 22: ML not within 15 cents of pick'em
5. ✅ Rule 32: No conflicting ML/F5 already logged that implies the favored team scores 4–5+ runs
6. ✅ Bounceback check: neither team flagged as bounceback candidate (MODEL_CORE algorithm)

**Any gate failure = downgrade to Paper or skip.**

### Step 4b — ML Juice Check
Before logging any ML at -200 or worse:
- Pull the RL price for the same side
- If RL is plus money AND model cover >50% → log RL as primary, ML at paper only
- Log both with a note: "ML at -2XX juice; RL +XXX logged as primary per Rule 33"

### Step 4c — Same-Game Thesis Conflict Check
Before logging a total Under on any game where a ML or F5 is already logged:
- Estimate the implied win score from the ML thesis
- If projected total is within 1 run of the Under line → skip or paper only
- Log a note: "Under within 1 run of ML win projection — downgraded per Rule 32"

### Step 4d — Kalshi Divergence Investigation [NEW]
When model and Kalshi diverge by >15% on any bet:
- Do not auto-reduce confidence
- Investigate: recent form (last 7 and 15 games), injury/lineup news, park, weather, bullpen usage
- If investigation surfaces a specific unmodeled factor → adjust model and re-evaluate
- If investigation finds nothing → keep original confidence, note the divergence in the bet's notes field
- Log: "Kalshi divergence [X]% — investigated, no adjustment" or "Kalshi divergence [X]% — adjusted for [reason]"

### Step 5 — Model Gap Sanity Check (Rule 28)
For any bet where model% differs from Pinnacle VF by >10%:
- Flag it explicitly
- Check if Kalshi and Pinnacle agree
- If both agree vs model: reduce size one tier, keep if qualitative case is strong
- Note it in the bet's notes field

### Step 6 — Log, Review, and Push (all at once)
1. Log ALL ≥1.5% edge plays to bets.json as status: PENDING
2. F5 bets: note "price estimated — verify on FD/DK before placing"
3. Size plays ≥3% per Kelly table; paper-log 1.5–2.9%
4. **In the same response:** present the full bet log, summary of model signals, and any improvement proposals observed during analysis
5. Push bets.json + BET_LOG.md to GitHub

---

## Bet Entry Format (bets.json)
```json
{
  "id": "2026-05-27-001",
  "date": "2026-05-27",
  "game": "NYY @ KC",
  "market": "ML",
  "bet": "NYY ML",
  "price": -145,
  "modelPct": 58.2,
  "kalshiPct": 52.0,
  "edgePct": 1.9,
  "size": 5,
  "confidence": "Medium",
  "factors": {"starterXERA": 0.6, "bullpen": 0.2, "streak": 0.1},
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "clv": null,
  "notes": ""
}
```

---

## Calibration Formula
Run when 30+ settled bets exist in an edge bucket:
1. Group bets by edge tier: 1.5–1.9%, 2.0–2.9%, 3.0%+
2. Per tier: actual_win_rate = wins / (wins + losses)
3. Expected win rate from model implied probability
4. Calibration factor = avg(actual) / avg(expected) across tiers
5. If new factor differs from current by >0.03 → update MODEL_CORE.md

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

> Note: Per-bet tracking begins May 26, 2026. Prior record is session-level summary only. May 27 excludes 11 Team Total bets pending TT line verification.
