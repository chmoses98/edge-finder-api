# RUN_THE_SLATE.md
# The one file to rule them all.
# Last updated: September 19, 2026 — v1.5 (compact handicap runtime: manifest-first
#                                          serving layer; v1.4 confirmed-lineup
#                                          eligibility, bankroll context, full-market
#                                          manual handicapping)
#
# USAGE: When the user says "run the slate" / "RUN MLB", execute the
# RUNTIME FAST PATH immediately below. Everything after it is the build
# and operations reference behind that path, plus the operator sequences
# that produce the artifacts it reads.
# ─────────────────────────────────────────────────────────────────────────────

---

## ▶ RUN MLB — THE FAST PATH (this is the whole consumer contract)

**A chat handicapper reads FOUR things, in this order, and never the
full-day handicapping card.**

```
1. HANDICAPPING_PLAYBOOK.md      (~8 KB)   methodology
2. PLAYBOOK_LESSONS.md           (~11 KB)  durable lessons
3. data/handicap_runtime/<DATE>/manifest.json        (~32 KB)
4. data/handicap_runtime/<DATE>/games/<gameId>.json  — ONLY for the
   games the manifest marks bettingEligible
```

**"RUN MLB" arrives with no date in it**, so start at the pointer rather than
guessing one — a guess that lands on yesterday is a slate that has already been
played:

```
data/handicap_runtime/latest.json     (~600 B)
  -> { "date", "manifest", "bettingEligibleGames", "eligibleMarketsTotal",
       "bankrollStatus", "dollarSizingVerdict", "readThisFirst" }
```

That is the entire required read set. On 2026-09-18 it is about **625 KB**
for the whole eligible slate, against **8.6 MB** for
`data/handicapping_card/<DATE>.json` alone — and the eligible-market count is
*identical*, because the runtime layer changes representation and never the
universe.

**You do NOT need to load `MODEL_CORE.md`, `RULES.md`, `SLATE_WORKFLOW.md` or
`DATA_SOURCES.md` to handicap a slate.** They remain authoritative reference
for the math, the rule tiers, the pipeline and the sources — open one when a
specific question needs it, not as a startup step. The small,
execution-critical subset (calibration factors, edge thresholds, base sizes,
market multipliers) is projected verbatim from `config/rules.json` into
`manifest.executionConstants`, so it cannot drift from the canonical config.

### The published consumer flow

These steps are `lib.handicap_runtime.CONSUMER_FLOW`, and
`manifest.consumerFlow` carries them in every manifest.
`tests/test_handicap_runtime.py` fails if this document and that constant
ever disagree, so the contract here cannot go stale:

1. Read HANDICAPPING_PLAYBOOK.md and PLAYBOOK_LESSONS.md (methodology; ~19 KB total).
2. Read this manifest.
3. Select games where bettingEligible is true (not started AND both official lineups confirmed).
4. Load ONLY those games' bundles, by the `bundle` path on each manifest row. The full-day handicapping card is NOT required and must not be loaded to handicap a slate.
5. For EACH eligible game, independently: build the baseball thesis first, then inspect EVERY market row in that bundle, compare all viable expressions, and keep only wagers clearing the threshold in manifest.executionConstants.
6. Run one final portfolio/correlation pass across all games (playbook section 5).
7. Return the betting card.

Step 5 is **per game and independent** — the bundles have no cross-references,
so several eligible games can be handicapped in parallel. Step 6 is the only
step that needs all of them at once.

### What the manifest answers without loading anything else

| Question | Field |
|---|---|
| How many eligible games are there? | `counts.bettingEligibleGames` |
| Which ones? | `games[].bettingEligible` / `matchup` / `gameId` |
| Has it started? | `games[].started`, `games[].gameStatus` |
| Are BOTH official lineups confirmed? | `games[].lineups.bothConfirmed` (+ per side) |
| How many markets must I inspect? | `games[].marketsToInspect` |
| Did anything go missing? | `games[].silentRemainderCount` (always 0) and `completeness` |
| Where is the game's data? | `games[].bundle` (and `.bundleGzip`) |
| May I quote dollar stakes? | `bankroll.consumerSizingVerdict.verdict` |
| What thresholds apply? | `executionConstants` |

### What a game bundle contains

* `eligibility` — the game's own verdict, lineups, start, started/not-started.
* `context` — teams, park, starting pitchers (+ Savant), bullpen state and
  recent usage, team offensive form (including the **verbatim**
  `offenseFormLine` the playbook §6 requires), opponent quality, and the
  **official confirmed lineups** with platoon splits.
  `context.productionProjections` is the legacy 11-row model's output and is
  labelled `REFERENCE_ONLY` — it is evidence, not the handicap (playbook §7),
  and it says nothing about the other ~330 markets in the bundle.
* `markets` — **EVERY** Kalshi market attributable to the game, as a compact
  `columns`/`rows` table with integer `legends` for repeated strings.
  Decode a row by zipping `columns` with it and replacing any column listed in
  `internedColumns` with `legends[column][value]`. `gameLevelConstants` and
  `derivedFields` say exactly how to reconstruct the archival card row.
* `registryExcluded` / `unclassified` — contracts that did **not** normalise,
  retained with their reasons. A Kalshi family this repository has never seen
  appears here rather than vanishing.
* `contractAccounting` — the completeness proof for this game.

**Nothing in the bundle is ranked, shortlisted, preselected or filtered by
model edge.** Pitcher and hitter props are present in full. The bundle's
market count equals the card's market count for that game, and a build that
cannot prove it refuses to write.

### Build it

```bash
python3 scripts/build_handicapping_card.py                 # the archive (unchanged)
python3 scripts/build_handicap_runtime.py --date YYYY-MM-DD --print-sizes
```

`build_handicap_runtime.py` is a **projection** of the card — it runs no market
discovery, no normalisation, no registry and no eligibility logic of its own,
so it cannot develop semantics that differ from the canonical ones. It verifies
ticker-set equality and field-level price equality against the card before it
writes, and exits non-zero having written nothing if either fails.

---

## STEP 0 (MANDATORY) — READ THE CANONICAL HANDICAPPING PLAYBOOK

**Before any analysis, before any price, read
[`HANDICAPPING_PLAYBOOK.md`](HANDICAPPING_PLAYBOOK.md) and
[`PLAYBOOK_LESSONS.md`](PLAYBOOK_LESSONS.md), in full.**

They are short on purpose (playbook ≈ 950 words of methodology). Together they
are the repository's durable handicapping memory: game eligibility, thesis
before market, market expression, price and bankroll discipline, correlation
control, how to read recent form, and the evidence-graded lessons earned from
past postmortems. Without them, every new chat re-derives the same lessons from
scratch and re-makes the same mistakes.

**Record the playbook version in the slate output**, so a later postmortem knows
which methodology produced the card:

```bash
grep -m1 PLAYBOOK_VERSION HANDICAPPING_PLAYBOOK.md
python3 scripts/playbook_lessons.py --list        # what is SUPPORTED vs HYPOTHESIS
```

Nothing below this line overrides the playbook's methodology; `RULES.md` and
`config/rules.json` still own the numeric thresholds and hard gates.

---

## START-OF-SLATE PROMPT (copy/paste into a fresh chat)

```
Read HANDICAPPING_PLAYBOOK.md and PLAYBOOK_LESSONS.md in
chmoses98/edge-finder-api (main) first, and follow them.
Load the newest valid data/handicap_runtime/<DATE>/manifest.json, then ONLY
  the game bundles it marks bettingEligible -- never the full-day card.
Use ONLY unstarted games with BOTH official lineups confirmed.
For each eligible game, inspect EVERY available Kalshi market.
Build the baseball thesis BEFORE choosing its best market expression.
Respect price, uncertainty and correlated exposure.
Use the canonical bankroll ONLY if its actual NUMERIC value is in your hands
  and fresh; otherwise give no dollar stake sizes -- bet-up-to still applies.
State the strongest evidence AGAINST every proposed wager.
Return only bets clearing the threshold; passing is fine.
Never assume a recommendation was placed -- I confirm every wager myself.
Report the PLAYBOOK_VERSION and the bankroll source/status used.
```

---

## MANDATORY STALE-DATE SAFETY SEQUENCE

**This section is non-negotiable. Follow it before every slate run.**

**Step 1: Pull latest main**
```bash
git pull origin main
```

**Step 2: Determine requested slate date in America/New_York**
```bash
TZ='America/New_York' date +%Y-%m-%d
# Use this date (YYYY-MM-DD) for all subsequent steps
```

**Step 3: Trigger fetch-slate workflow for the requested date**
```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Body: {"ref":"main", "inputs":{"date":"YYYY-MM-DD"}}
```
Wait for the workflow to complete successfully before proceeding.

**Step 4: Run stale-date validation**
```bash
python3 scripts/validate_current_slate_date.py YYYY-MM-DD
```
This script checks:
-  status == "OK"
-  date matches requested date
-  date matches requested date
- All game start times map to the requested date in America/New_York
-  date matches (if present)
- Kalshi data date matches

**Step 5: Only if Step 4 passes, proceed**
- Run slate validation
- Run Poisson engine
- Produce real-money slip
- Produce paper bets

**If stale-date validation fails, stop. Do not use web-searched pitchers plus stale repo files. Do not manually run the model on old data/slate.json. Do not produce paper or real bets from stale data.**

---

## WHAT THIS FILE IS

This is the **single authoritative execution sequence** for every slate session.
It replaces the startup sections of RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, and DATA_SOURCES.md.
Those files are now **reference-only** — they define the math and rules but never the execution order.

---

## STARTUP SEQUENCE (exactly once, in this order)

> **This section is the OPERATOR / PIPELINE sequence** — what produces the
> artifacts, and what a human or automation runs. A chat handicapper does not
> execute it; it follows the **RUN MLB fast path** at the top of this file.

### S1 — Model files are reference, not a startup load

`RULES.md`, `MODEL_CORE.md`, `SLATE_WORKFLOW.md` and `DATA_SOURCES.md` are
**reference documents**. They are **no longer a startup read** for
handicapping: fetching ~163 KB of governance prose before looking at a price
was the single largest cost of starting a slate, and none of it was needed to
decide which games are eligible or what a contract is worth.

Open one when a specific question requires it (a rule's exact tier, a
projection formula, a pipeline dependency, a source's provenance). The
execution-critical constants a handicapper actually needs — calibration
factors, edge thresholds, base sizes, market multipliers — are projected
verbatim from `config/rules.json` into
`data/handicap_runtime/<DATE>/manifest.json` → `executionConstants`.

```bash
# only when a specific question needs it:
#   https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/RULES.md
```

### S2 — Trigger fetch-slate Action
```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Body: {"ref":"main"}
```
Poll `data/meta.json` every 15s until `fetchedAt` contains today's date (ET). Cap: 3 min. Re-trigger once if stale.

### S3 — Read slate.json and validate
Pull `data/slate.json` via GitHub contents API. Then run validation:
```bash
python3 scripts/validate_slate_final.py "$DATE"   # checks schema + marketLedger completeness
# Failure = STOP. Fix before analysis.
```
The Action writes `g['marketLedger']` for every game via `build_market_ledger.py`.
Validation confirms: starters present, projections computed, marketLedger populated,
all required markets have a row, all rows have a valid status.

### S4 — Run Poisson engine (bash_tool)
```python
import math
def poisson_pmf(k, lam): return (lam**k * math.exp(-lam)) / math.factorial(k)
def game_probs(away, home, max_r=20):
    wa=wh=push=0
    for a in range(max_r+1):
        for h in range(max_r+1):
            p = poisson_pmf(a,away)*poisson_pmf(h,home)
            if a>h: wa+=p
            elif a<h: wh+=p
            else: push+=p
    return round(wa/(1-push)*100,1), round(wh/(1-push)*100,1), round(push*100,1)
def p_over(proj, line, max_r=30):
    return round(sum(poisson_pmf(r,proj) for r in range(int(line)+1,max_r+1))*100,1)
```

### S5 — Produce full output (no abbreviation, every game, every market)
See OUTPUT CONTRACT below.

### S6 — Push bets.json to GitHub
Only after full output is confirmed. Status: open (real) or paper.

---

## PRODUCTION-MODEL MARKET LIST (the 11-row `marketLedger` universe)

> **Scope.** This table is the **legacy production-model and risk-gate**
> universe: the markets `scripts/build_market_ledger.py` prices, `risk_gate.py`
> gates, and the automated execution chain may write to `bets.json`. It is NOT
> the set of markets a manual handicapper may compare on a BETTING-ELIGIBLE
> game — see § FULL MARKET COVERAGE and `HANDICAPPING_PLAYBOOK.md` §3.

| # | Market | Kalshi Series | Rule Gate |
|---|---------|--------------|-----------|
| 1 | NRFI | KXMLBRFI | Rule 34: blocked if total ≥8.0 without dual sub-3.00 1st-inn xERA |
| 2 | YRFI | KXMLBRFI | Four-factor composite required |
| 3 | F5 ML (Away) | KXMLBF5 | **Mandatory** — model failure if absent (Rule 25) |
| 4 | F5 ML (Home) | KXMLBF5 | Rule 77: if both qualify, log higher edge real / lower paper |
| 5 | Team Total Away Over | KXMLBTEAMTOTAL | Rule 44: Paper if line unconfirmed |
| 6 | Team Total Home Over | KXMLBTEAMTOTAL | Rule 44: Paper if line unconfirmed |
| 7 | ML (Both sides) | KXMLBGAME | Rule 71: blocked if model vs Pinnacle VF >8% unexplained |
| 8 | Game Total | KXMLBTOTAL | Paper-only (WR 41%) |

**RL** (KXMLBSPREAD): paper/suspended per Rule 81. Always evaluate; always paper until suspension lifts.

**Every game gets all 11 rows in `marketLedger`. A missing row is a pipeline failure, not an acceptable gap. Within the production-model universe, `allEdges` is not the coverage source of truth — `marketLedger` is.**

---

## SOURCE-OF-TRUTH HIERARCHY

| What | Authority |
|------|-----------|
| Bet prices / edge target | Kalshi VF |
| Sanity check | Pinnacle VF |
| Closing lines / CLV | Kalshi historical |
| Edge formula | `(modelProb − kalshiVF) × calibration_factor` |
| Calibration factors | `config/rules.json` → `calibration` |
| Market list | This file (above) |
| Rule definitions | `RULES.md` (T1/T2/T3 tiers) |
| Math engine | `MODEL_CORE.md` Sections 1–8 |
| Handicapping methodology | `HANDICAPPING_PLAYBOOK.md` (+ `PLAYBOOK_LESSONS.md`) |
| Game eligibility (real money) | both official lineups confirmed + not started — `lib/betting_eligibility.py` |
| Manual handicapping market universe | every Kalshi market for a BETTING-ELIGIBLE game — archived in `data/handicapping_card/<date>.json`, **served** from `data/handicap_runtime/<date>/` (identical universe, compact representation) |
| Production-model / risk-gate coverage | `g['marketLedger']` in `data/slate.json` — 11 rows per game, written by `build_market_ledger.py` |
| Bankroll for sizing | `lib/bankroll_context.py` (the authenticated Kalshi balance sealed in by `kalshi-bet-router`; nothing else can size) |
| Bet ledger | `bets.json` (flat array, parse directly) |

**FD/DK are banned as bet sources or fallbacks. Never used.**

---

## EDGE THRESHOLDS AND SIZING

| Tier | Calibrated Edge | Cal Factor | Base Size | Multiplier Applied? |
|------|----------------|-----------|-----------|---------------------|
| HIGH | ≥3.0% | 0.187 | $4 | Per config/rules.json |
| MEDIUM | ≥1.5% | 0.255 | $3 | Per config/rules.json |
| PAPER | ≥1.0% | 0.18 | $1 | Always $1, no multiplier |

F5 when f5Amplified=true (xERAGap ≥1.5): MEDIUM threshold drops to 1.0%.

`edge = (modelProb − kalshiVF) × calibration_factor` — never raw gap.

Current market multipliers (from `config/rules.json` → `multipliers`):
- F5 ML: 1.5x | Team Total: 1.25x | YRFI: 1.25x | ML: 1.0x | NRFI: 1.0x | RL: SUSPENDED | Game Total: PAPER ONLY

---

## OUTPUT CONTRACT (mandatory, no abbreviation)

For every game on the slate, produce in this exact structure:

```
PRE-SCAN: [Team] | L7: <avg> avg | <med> med | <game-by-game scores> |
          <n>/<w> >=4 | <n>/<w> >=5 | max <r> (<pct>% of L7 runs) |
          [OUTLIER-DEPENDENT] | [TT overs <n>/<w>] | Form: HOT/NEUTRAL/COLD/OUTLIER_INFLATED |
          L15: X.X | Szn: X.X
(one line per team — required before any game analysis)

Take this line VERBATIM from the slate: it is `awayTeamStats.offenseFormLine` /
`homeTeamStats.offenseFormLine` (and `offenseFormLabel`), written by
scripts/fetch_team_offense_form.py -> scripts/enrich_data.py. Do NOT recompute
it and do NOT summarise an offense with the mean alone: a single 20-run game
can lift a 7-game mean above league average while the median sits at 3. A team
labelled OUTLIER_INFLATED is NOT hot. See HANDICAPPING_PLAYBOOK.md §5 and
docs/RESEARCH_OFFENSIVE_FORM.md.

GAME: [AWAY @ HOME] — Date/Time
LINEUP CHECK:
  AWAY: lineupConfirmed=T/F | lineupAdj=±X.XX R/G applied=T/F | offenseBaselineAdj=X.XX
  HOME: lineupConfirmed=T/F | lineupAdj=±X.XX R/G applied=T/F | offenseBaselineAdj=X.XX
  Gate: [TT Paper-only {team} / All markets clear]

STARTERS:
  AWAY: [Name] true_xFIP=X.XX (xFIP=X.XX, xERA=X.XX, K/9=X.X, BB/9=X.X, GS=N)
  HOME: [Name] true_xFIP=X.XX (xFIP=X.XX, xERA=X.XX, K/9=X.X, BB/9=X.X, GS=N)

RUN PROJECTION:
  AWAY offense_baseline_adj: X.XX → off_factor: X.XX
  HOME starter: xFIP X.XX → X.XX R/inn × X.X IP = X.XX; bullpen: X.XX R/inn × X.X IP = X.XX; park ±X.XX
  AWAY proj: X.X runs
  HOME offense_baseline_adj: X.XX → off_factor: X.XX
  AWAY starter: xFIP X.XX → X.XX R/inn × X.X IP = X.XX; bullpen: X.XX R/inn × X.X IP = X.XX; park ±X.XX
  HOME proj: X.X runs
  TOTAL proj: X.X | F5: AWAY X.X / HOME X.X

MARKET LEDGER (read from g['marketLedger'] — 11 rows required, no exceptions):
Market         | Status       | Kalshi | KalVF% | Model% | Edge   | Conf   | Note
NRFI           | ...          | ...    | ...    | ...    | ...    | ...    | ...
YRFI           | ...          | ...    | ...    | ...    | ...    | ...    | ...
F5_ML_Away     | ...          | ...    | ...    | ...    | ...    | ...    | ...
F5_ML_Home     | ...          | ...    | ...    | ...    | ...    | ...    | ...
TT_Away_Over   | ...          | ...    | ...    | ...    | ...    | ...    | ...
TT_Home_Over   | ...          | ...    | ...    | ...    | ...    | ...    | ...
ML_Away        | ...          | ...    | ...    | ...    | ...    | ...    | ...
ML_Home        | ...          | ...    | ...    | ...    | ...    | ...    | ...
Game_Total     | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 71 suspension
RL_Away        | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 81 suspended
RL_Home        | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 81 suspended

Status must be exactly one of: Accepted | Rejected | Missing Data | Evaluation Failed
Evaluation Failed = hard stop. Investigate before logging any bets for this game.

STACK CHECK: N bets | Correlated: Yes→reduced / No | Aggregate: $X | Independent angles: [list]

QUALIFYING BETS:
[bet] | $X | Conf | Gate: [any T1/T2 fired] | Thesis: [one sentence]
```

Any game block missing any section above = model failure. Do not push until complete.

---

## MARKET LEDGER EXECUTION STANDARD

### What the market ledger is

`g['marketLedger']` is the required execution output of every slate run. It is written by
`scripts/build_market_ledger.py` and read by `scripts/validate_slate_final.py` and
`scripts/regression_test.py`. It is the source of truth for market coverage.

`allEdges` is a pipeline artifact. It is not the coverage source of truth. A market
absent from `allEdges` with no entry in `marketLedger` is a pipeline failure, not
an acceptable omission.

### Completeness rule

A slate is not complete unless:

```
len(g['marketLedger']) == 11  for every game g
total ledger rows == games × 11
```

The 11 required markets are those in `config/rules.json` → `market_list`. Any deviation
is a hard stop before logging bets.

### Row status rules

Every row must have exactly one of these statuses:

| Status | When used | Required fields |
|--------|-----------|-----------------|
| `Accepted` | Edge ≥ threshold, no gates blocked | `kalshiPrice`, `edge`, `confidence`, `market` — all non-null |
| `Rejected` | Evaluated; below threshold or gate blocked | `rejectionReason` — non-empty string |
| `Missing Data` | Kalshi price not in slate | `missingFields` — list with at least one field path |
| `Evaluation Failed` | Unexpected error during evaluation | `evaluationError` — non-empty string |

**`Evaluation Failed` is a hard stop.** Do not log any bet for that game until the
error is diagnosed. `Evaluation Failed` with an empty `evaluationError` is also
a hard stop — it means the error was silently swallowed.

### Rejection reason formats

Every `Rejected` row must include one of:

- `Rule N: [specific reason]` — a named rule gate fired
- `edge X.X% below X.X% floor` — evaluated, no qualifying edge
- `Missing Data — [field path]` — price not posted (use `Missing Data` status instead)
- `Rule 71 market suspension: [market] WR X% — Paper only until WR>=X% N>=X`
- `Rule 81: RL suspended — WR X%, CLV X%. Paper until WR>=48% N>=20 AND CLV>=0% N>=15`

Blank `rejectionReason` on a `Rejected` row = validation failure.

### Post-run report (required after every slate)

After every run, before logging any bets, report exactly this block:

```
LEDGER REPORT — [DATE] — [N] games
  Total rows    : [N] / [games × 11] expected
  Accepted      : [N]
  Rejected      : [N]
  Missing Data  : [N]
  Eval Failed   : [N]  ← must be 0 to proceed

  Missing Data rows:
    [game] / [market] — [missingFields]    (or NONE)

  Validation failures:
    [description]                           (or NONE)

  Warnings:
    [description]                           (or NONE)
```

Do not log any bets until `Eval Failed = 0` and `Validation failures = NONE`.

---

## ELIGIBILITY — which games may be bet at all (MANDATORY)

**Only games with BOTH OFFICIAL LINEUPS CONFIRMED are eligible for real-money
evaluation.** The pipeline archives every game and every Kalshi market; the
handicapping card does not.

```
1. archive the COMPLETE MLB Kalshi universe        (capture jobs, unchanged)
2. determine which games have NOT started
3. determine which of those have BOTH official lineups confirmed
4. those — and only those — are BETTING-ELIGIBLE
5. for each eligible game, evaluate EVERY available Kalshi market
6. a game without both official lineups confirmed must NOT produce a
   real-money recommendation from the normal slate card
7. probable/projected lineups are NEVER treated as confirmed
```

Build the card, then the runtime the consumer actually reads:

```bash
python3 scripts/build_handicapping_card.py            # -> data/handicapping_card/<date>.json
python3 scripts/build_handicapping_card.py --print-summary
python3 scripts/build_handicap_runtime.py --print-sizes   # -> data/handicap_runtime/<date>/
```

It writes `bettingEligibleGames` (executable) and `researchOnlyGames`
(archived, visible, `realMoneyEligible: false` on every market row — this is
the **Early Value / research surface**, and it must never leak into the
executable card). `data/handicapping_card/latest.json` is the pointer.

"Analyze every available market" therefore means **every available market for
every UNSTARTED + LINEUP-CONFIRMED + OTHERWISE ELIGIBLE game** — never
recommending bets on games whose official lineups are still unconfirmed.

---

## BANKROLL — size against the real one, or say you cannot

The card carries a read-only `bankroll` context (`lib/bankroll_context.py`).

**Dollar stake sizing requires BOTH of these, and neither implies the other:**

1. `bankroll.sizingAllowed` — a FRESH, sizing-authoritative bankroll exists;
2. `bankroll.numericBankrollAvailable` — **the actual number is in YOUR hands.**

`bankroll.consumerSizingVerdict.verdict` states the answer directly:

| Verdict | What you may present |
|---|---|
| `DOLLAR_SIZING_PERMITTED` | dollar stakes |
| `NO_DOLLAR_SIZING_FOR_THIS_CONSUMER` | **no dollar stakes** — the bankroll exists and is fresh, but its value is redacted from the copy you are reading |
| `NO_DOLLAR_SIZING` | **no dollar stakes** — stale, unavailable, or not sizing-authoritative |

**`sizingAllowed: true` means the PRIVATE WORKFLOW that built this card was
permitted to use the balance. It does NOT mean you know the amount.** The
committed card on this public repository carries `bankroll: null`,
`bankrollRedacted: true`, `numericBankrollAvailable: false` — and yet
`status: FRESH`, `sizingAllowed: true`, because both statements are true of
different readers. Reading only the second is how a chat session invents
dollar figures it has no basis for.

When you may not size: **handicap normally.** Give edge, confidence, and
bet-up-to *fractions* of bankroll — all of that works without the number.
Just say explicitly that the authenticated bankroll exists but is redacted
from this consumer (or is stale/unavailable, as applicable), and present no
dollar amounts. Never substitute a remembered, hand-typed or derived number.

Three things about it are load-bearing:

* **One authority.** Only the authenticated Kalshi account balance
  (`source: kalshi_authenticated_balance`,
  `valueType: KALSHI_AVAILABLE_CASH_BALANCE`) can set a stake size. This
  repository's own derived ledger is diagnostic context and can never size,
  however fresh it looks — its cash history is not proven complete, and a
  recently-updated wager is not evidence that last week's deposit was ever
  recorded.
* **30 minutes.** A balance is a live quantity. A day-old reading is not the
  current bankroll.
* **The committed card does not carry the amount.** This repository is
  public. The build itself received the real number from an encrypted secret;
  the committed file does not. The final output must name the bankroll's
  `source`, `status`, `observedAt` and `consumerSizingVerdict` — report the
  amount **only** if you actually hold it.

See `docs/BANKROLL_CONTEXT.md` for the source, the field semantics and the
freshness rules.

---

## FULL MARKET COVERAGE (the manual handicapper's market universe)

**Scope note (v1.3).** `g['marketLedger']` (11 rows/game) is the source of
truth for the **legacy PRODUCTION-MODEL and RISK-GATE universe** — the markets
`scripts/build_market_ledger.py` prices and `scripts/risk_gate.py` gates, and
the only ones the automated execution chain may write to `bets.json`. That is
still true and unchanged.

It is **NOT** the set of markets a manual handicapper may compare. Once a game
is **BETTING-ELIGIBLE** (§ELIGIBILITY below), every Kalshi market attributable
to it is available for comparison — F3/F7, alternate totals, winning margin,
pitcher and hitter props included — whether or not a production adapter prices
it. See `HANDICAPPING_PLAYBOOK.md` §0 and §3.

Keep the five axes separate:

| Axis | Question | Decided by |
|---|---|---|
| **GAME ELIGIBILITY** | may this game be on the real-money card? | `lib/betting_eligibility.py` |
| **MARKET AVAILABILITY** | does Kalshi list the contract? | the archive |
| **PRODUCTION MODEL SUPPORT** | does our model price it? | the 11-row `marketLedger` |
| **MANUAL HANDICAPPING ELIGIBILITY** | can the analyst evaluate it? | the analyst |
| **AUTOMATIC SETTLEMENT SUPPORT** | can the repo grade it afterwards? | `settle_markets.py` |

A market with **no production adapter is still comparable** (axis 3 ≠ axis 4).
A market with **no automatic settlement support is still comparable**, but is
flagged — it will need manual reconciliation, so check before recording it. No
unsupported family is ever given a fabricated model probability.

### Where to find it (exact operational path)

```
S2 (Trigger fetch-slate Action)
  → data/kalshi_search.json + data/slate.json refreshed
  → "Fetch Slate Data" workflow completes
  → discover-kalshi-mlb-markets.yml fires automatically (workflow_run trigger)
      1. scripts/discover_kalshi_mlb_markets.py        (classify + price every contract)
      2. scripts/build_full_market_coverage.py         (THIS artifact)
      3. scripts/build_paper_spread_ledger.py
      4. scripts/discover_kalshi_series_catalogue.py
  → data/kalshi/discovery/<date>_coverage.json          (flat, read directly)
  → data/pipeline/<date>/full_market_coverage.json      (versioned envelope,
                                                           lib.pipeline_artifacts)
```

No separate trigger, no separate live Kalshi call — step 2 reads the exact
same `data/kalshi_search.json`/`data/slate.json` step 1 just read for the
same observation. If you only just ran S2, wait for the "Discover Kalshi
MLB Markets" Action to complete (same `workflow_run` dependency
`RUN_THE_SLATE.md`'s own polling already accounts for) before reading
`data/kalshi/discovery/<date>_coverage.json`.

### Hitter research linkage (prospective checkpoint store, primary + fallback)

`scripts/build_full_market_coverage.py` links every hitter prop contract
by ticker to hitter research evidence from TWO sources, in priority order
— **never conflating which is which, and never using a projection dated
after the market observation it's priced against**:

1. **PRIMARY**: `data/edgelab/hitter_projection_snapshots/<date>.jsonl` —
   the append-only checkpoint store `lib.research.hitter_prospective_snapshot`'s
   scheduler (`.github/workflows/hitter-snapshot-scheduler.yml`, ~every 15
   minutes during the pregame window) writes one row per hitter per due
   checkpoint (`T_MINUS_90`/`T_MINUS_60`/`T_MINUS_30`/
   `LINEUP_CONFIRMATION`/`HITTER_CLOSING_WINDOW`). For each contract, the
   LATEST snapshot whose own `snapshotGeneratedAt` is at or before this
   contract's current market observation is selected — never a
   later-dated one (no future leakage).
2. **FALLBACK, only when no qualifying snapshot exists**:
   `data/pipeline/<date>/hitter_projection_board.json` — this is **NOT**
   written by the 15-minute scheduler (every scheduler-triggered call
   passes `dry_run=True` to `scripts/build_hitter_projection_board.py` —
   see that scheduler's own module docstring); it is only produced by a
   separate, on-demand/standalone invocation. Used only when its own row
   for that ticker carries a `projectionGeneratedAt` that is itself at or
   before the current market observation — the same no-future-leakage
   rule applied to the primary source.

If neither source has a usable row for this date/ticker, the hitter
contract falls back to `UNSUPPORTED_MODEL_FAMILY` exactly as if no
research engine existed (never blocks, never guesses a stale/missing
linkage). `hitterProspectiveSnapshotStoreStatus`/`hitterLegacyBoardFallbackStatus`
at the top of the artifact say `LOADED` or `NOT_AVAILABLE` for the run
that produced it; each linked row's own `hitterProjectionSourceType`
says `PROSPECTIVE_SNAPSHOT` or `LEGACY_BOARD_FALLBACK`.

**Current vs. projection-time price — never mixed.** A hitter row's
`hitterModelProbability` may legitimately come from an earlier checkpoint
(e.g. `T_MINUS_60`, 40+ minutes old). The price that decides whether the
market is attractive NOW always comes from THIS contract's own current
market observation (`currentMarketObservedAt`/`currentExecutableKalshiPrice`/
`currentYesPrice`/`currentNoPrice`), and every `current*` economics field
(`currentRawProbabilityEdge`, `currentFeeAwareNetExpectedValuePerDollar`,
`currentFeeAdjustedBreakEvenProbability`, `currentFeeAwareBetUpToPrice` —
all via the existing canonical `lib.edgelab.kalshi_fees` utilities) is
computed from that current price, NEVER from the price recorded at
projection time. That historical price is retained separately, under
`projectionTimeExecutablePrice`/`projectionTimeMarketObservedAt`, for
CLV/research provenance only. `hitterProjectionCheckpoint`,
`hitterProjectionSnapshotGeneratedAt`, and `hitterProjectionAgeMinutes`
(current − projection timestamp, in minutes) tell you exactly how stale
the probability is, so a manual analyst can see e.g. "model probability
came from T-60, 43 minutes ago; current Kalshi price is 47¢ now" instead
of a silently blended number.

### Terminal states and status fields

Every archived contract gets exactly one of: `FULLY_EVALUATED` (a
production adapter, `lib.kalshi_probability_adapters`, priced it),
`RESEARCH_MODEL_ONLY` (no production adapter, but hitter research priced
it at or before this observation — research-only, see above),
`MISSING_REQUIRED_CONTEXT`, `UNSUPPORTED_MODEL_FAMILY` (no model anywhere
in this repo for the family AT THIS OBSERVATION), `PARSER_UNRESOLVED`,
`GAME_MAPPING_UNRESOLVED`, `AMBIGUOUS_TICKER_MATCH`, `STARTED_GAME_EXCLUDED`
(decided exclusively from THIS run's own game/slate data, never from a
research snapshot's own possibly-older observation), or `NOT_APPLICABLE`
(different date). See `docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md` section
6-8 and `lib/kalshi_market_coverage.py`'s module docstring for the full
definitions.

Each row also separates three axes that are easy to conflate (item 2):
`productionModelSupportStatus` (the generic adapter's verdict alone),
`researchModelSupportStatus` (the hitter research evidence's own verdict,
`None` for non-hitter families), and `realMoneyEligibilityStatus`
(`"RESEARCH_ONLY"` for every hitter-family row, regardless of research
outcome — hitter props are never promoted to production real-money
eligibility by this artifact, full stop).

The artifact also reports a **pregame-scoped view** (`pregameView`) —
`startedGameExcluded` contracts removed from the denominator — since that
is the number that actually matters before first pitch, and a **raw
archive invariant** (`rawArchiveAccounting`) that is independent of the
discovery engine's own output: it re-derives the unique raw ticker set
directly from `data/kalshi_search.json` and fails
(`trueSilentRemainderCount > 0`) if any raw market vanished anywhere
inside discovery, not just if a returned contract lacks a terminal state.

Use this artifact to inspect any market Kalshi listed. For the **automated
execution chain**, a market still becomes real-money eligible only by being in
`REQUIRED_MARKETS` and clearing every gate in `scripts/build_market_ledger.py`
and `scripts/risk_gate.py` — unchanged. For **manual handicapping**, a market on
a BETTING-ELIGIBLE game is comparable regardless, and the decision to wager is
the analyst's, made under `HANDICAPPING_PLAYBOOK.md`.

---

## SINGLE-GAME FETCH (one matchup, not the whole slate)

When you want fresh data for exactly ONE game — not a slate run — use the
**Fetch Single Game** workflow. It never touches `data/slate.json`, and it
archives the COMPLETE unfiltered Kalshi universe before filtering to your game.

```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-single-game.yml/dispatches
Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD","game":"Yankees vs Red Sox"}}
```

Result: `data/single_game/<date>/<gamePk>.json`, discoverable via
`data/single_game/latest.json`. A doubleheader FAILS CLOSED and prints both
gamePks — re-dispatch with `{"game_pk":"<gamePk>"}`. Full documentation and the
three worked examples: **[`docs/SINGLE_GAME_FETCH.md`](docs/SINGLE_GAME_FETCH.md)**.

---

## "HOW DID WE DO YESTERDAY?" — SETTLEMENT IS SELF-HEALING

A wager imported after the nightly postgame pass (e.g. by the Kalshi bet router)
no longer sits ungraded. **EdgeLab Settlement Reconcile** runs on every change to
the canonical ledger, on a twice-daily sweep, and on manual dispatch; it re-runs
the canonical settlement path for the affected dates and regenerates the daily
report whenever settlement actually changes canonical state.

To answer "how did we do yesterday", read the finished canonical report directly:

```
data/edgelab/reports/<YYYY-MM-DD>.md     (and .json)
data/edgelab/operational_health/settlement_reconciliation_status.json
```

`settlement_reconciliation_status.json` says when reconciliation last ran and
what, if anything, is **still pending**. A wager left pending is an honest
"not settleable yet", never a guessed result — unsupported market families stay
explicitly unresolved. Full lifecycle:
**[`docs/SETTLEMENT_RECONCILIATION.md`](docs/SETTLEMENT_RECONCILIATION.md)**.

---

## DEPRECATED / ARCHIVED FILES

These files are **no longer authoritative** and are moved to `archive/`. Claude must not treat them as current instructions:

| File | Status | Reason |
|------|--------|--------|
| `archive/RULES_INDEX.md` | Archived | Superseded by config/rules.json |
| (prior README workflow sections) | Archived | Startup sequence is now here only |

Model files (RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md) remain active as **reference documents** — they define math and rules but not execution order. Execution order is defined exclusively in this file.

---

## EXECUTION STANDARD AUDIT (June 7, 2026 — v1.1)

✅ **One startup sequence** — S1–S6 above. No other file defines startup order.
✅ **One market evaluation list** — 11-market table above, mirrored in `config/rules.json → market_list`.
✅ **One coverage source of truth** — `g['marketLedger']`. `allEdges` is not the audit source.
✅ **Market ledger completeness enforced** — `regression_test.py` asserts `games × 11` rows every run.
✅ **Every non-Accepted row has a documented reason** — enforced by `validate_slate_final.py` and `regression_test.py`.
✅ **Evaluation Failed is a hard stop** — stated in output contract and post-run report format.
✅ **Accepted rows require price, edge, confidence, market** — asserted by regression_test.py (A7).
✅ **Post-run report format is fixed** — LEDGER REPORT block above is required before any bet logging.
✅ **No deprecated instructions active** — RULES_INDEX.md in archive/.


---

## SECTION: BET PERSISTENCE CHAIN (added June 17, 2026 — v1.2)

**This section supersedes any prior guidance on bet logging order.**

### Automated path (standard slate)

The pipeline writes bets in this exact order. Each step is a gate — failure
stops the chain and means no real-money slip.

```
1. risk_gate.py          → TT safety + portfolio concentration
2. write_pending_bets.py → writes bets.json
3. validate_bet_logging.py → hard integrity check
4. write_tracked_tickers.py → CLV ticker registry
5. COMMIT: data/ + bets.json
```

**No slip is produced unless all 5 complete successfully.**

### Session / late-lineup path (added June 17, 2026)

When lineups confirm after the automated run:

```
1. python3 scripts/enrich_lineup_confirmed.py
   # Refreshes lineupConfirmed from lineup_audit_{date}.json (v2.0 primary source)
   # Fixes the June 17 stale-field bug: audit at 22:45Z was newer than slate 18:19Z

2. [run session analysis, identify bets]

3. Create data/session_bets/YYYY-MM-DD.json (one entry per bet, see schema below)

4. python3 scripts/log_session_bets.py data/session_bets/YYYY-MM-DD.json
   # Writes bets.json (idempotent — no duplicates on re-run)
   # Appends tickers to data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json

5. Verify:
   - bets.json contains the new bets
   - tracked_tickers.json contains the new tickers
   - no duplicates (re-run the script to confirm "Bets skipped: N")

6. COMMIT before first pitch → clv_capture.yml picks up tickers on next 10-min run

7. If logging after first pitch:
   - set "post_entry_manual_review": true
   - set "clvStatus": "unavailable"
   - set "clvReason": "session_bet_not_tracked_pregame: [explanation]"
```

**Session bet schema (minimum required fields):**
```json
{
  "date": "YYYY-MM-DD",
  "game": "AWAY@HOME",
  "market": "F5 ML",
  "side": "HOME",
  "ticker": "KXMLBF5-...",
  "entryPrice": -111,
  "stake": 4.50,
  "modelPct": 68.0,
  "marketPct": 52.8,
  "edgePct": 2.84,
  "confidence": "MEDIUM",
  "scheduledStartTime": "2026-06-17T23:05:00Z",
  "source": "session_analysis"
}
```

### Guardrails (both paths)

| Guardrail | Rule |
|---|---|
| No unlogged real-money bets | Every real-money bet → bets.json before first pitch |
| No CLV-uncapturable bets without marking | clvStatus="unavailable" + clvReason required |
| No TT real-money without risk gate | risk_gate.py must pass; TT line must be confirmed |
| No stale lineupConfirmed | Run enrich_lineup_confirmed.py if lineupCheckedAt > 3h before pitch |
| No slip without persistence confirmation | bets.json + tracked_tickers.json committed |

---

## SECTION: REAL-MONEY SLIP FORMAT (added June 17, 2026 — v1.2)

Every real-money session ends with exactly one slip in this format.
See `OPERATIONAL_RUNBOOK.md` for full detail.

```
═══════════════════════════════════════════════════════════════
REAL-MONEY SLIP — [DATE] — [HH:MM ET]
═══════════════════════════════════════════════════════════════

PIPELINE STATUS
  Workflow run ID  : [run ID]
  Commit SHA       : [12-char SHA]
  Fetch date       : [YYYY-MM-DD]
  lineupCheckedAt  : [ISO timestamp] (must be ≤ 4h before first pitch)
  Lineup audit     : [used / not found / stale]

CHECKPOINTS
  [✅/❌] fetch-slate completed
  [✅/❌] risk_gate.py executed
  [✅/❌] write_pending_bets.py ran
  [✅/❌] validate_bet_logging.py passed
  [✅/❌] write_tracked_tickers.py ran
  [✅/❌] bets.json committed
  [✅/❌] tracked_tickers.json committed
  [✅/❌] stale-date guard passed

SLATE SUMMARY
  Games on slate : N | Included: N | Excluded: N
  Exclusion reasons: [game: reason, ...]

RISK GATE: PASS / FAIL

BETS
  Real-money: N | Paper: N | Total stake: $X

REAL-MONEY BETS:
  [#] [GAME] | [MARKET] | [SIDE] | [PRICE] | $[STAKE] | edge=[X.X]% | [CONF]
      Ticker: [TICKER]
      Thesis: [one sentence]
      Gates:  [T1/T2 gates checked, or NONE]

PAPER BETS:
  [#] [GAME] | [MARKET] | [SIDE] | [PRICE] | $1 | edge=[X.X]% | [reason]

EXCLUDED GAMES: [game — reason, ...]
WARNINGS: [description, or NONE]

═══════════════════════════════════════════════════════════════
DECISION: [GO / PAPER / NO-GO]
  [One sentence justification]
═══════════════════════════════════════════════════════════════
```

**Decision rules:**
- **GO**: all 8 checkpoints green + ≥1 real-money bet with edge ≥ 1.5%
- **PAPER**: all 8 green + 0 real-money bets (valid; log papers, no real action)
- **NO-GO**: any checkpoint red OR stale date OR no valid slate

