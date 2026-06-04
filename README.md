# edge-finder-api

MLB sports betting model — data-driven edge identification using Poisson probability engine, Savant pitcher metrics, and Kalshi market comparison.

---

## What This Is

A systematic MLB wagering model that:
- Ingests live slate data (confirmed starters, team stats, weather, bullpen)
- Computes Poisson-derived run projections for both teams
- Calculates edge against Kalshi vig-free implied probabilities
- Applies a tiered rule gate system before logging any bet
- Tracks Closing Line Value (CLV) as the primary model health signal

**Current record (as of June 4, 2026):** 121W 106L 7P | P/L: +$6.38  
**Primary edge source:** F5 ML on xERAGap ≥1.5 (f5Amplified) — 56% WR, +2.31% avg CLV

---

## File Map

### Core Model Files (source of truth — pull at start of every session)
| File | Purpose |
|---|---|
| `RULES.md` | All 83 betting rules and gate definitions (T1/T2/T3 tier hierarchy) |
| `MODEL_CORE.md` | Probability engine, edge calculation, sizing, calibration, CLV formula |
| `SLATE_WORKFLOW.md` | Session workflow — startup sequence, analysis steps, output contract |
| `DATA_SOURCES.md` | Odds API architecture, data field definitions, fallback chain |

### Bet Records
| File | Purpose |
|---|---|
| `bets.json` | Authoritative bet ledger — flat JSON array of all bets |
| `BET_LOG.md` | Human-readable bet log — generated from bets.json |

### Scripts
| File | Purpose |
|---|---|
| `clv_update.py` | CLV settlement script — pulls Kalshi/Pinnacle closing lines, settles results, computes P/L |
| `scripts/fetch_savant_pitchers.py` | Fetches pitcher Savant metrics |
| `scripts/fetch_opp_quality.py` | Computes opponent quality adjustment for rolling R/G |
| `scripts/capture_closing_lines.py` | Captures Kalshi live prices at slate fetch time |
| `scripts/merge_odds.py` | Merges odds from multiple books into slate.json |
| `scripts/validate_slate.py` | Validates slate.json completeness before analysis |

### GitHub Actions (.github/workflows/)
| File | Status | Purpose |
|---|---|---|
| `fetch-slate.yml` | ✅ Production | Fetches all slate data, builds data/ files |
| `update-clv.yml` | ✅ Production | Runs clv_update.py to settle bets and compute CLV |
| `test-kalshi.yml` | 🗄️ Diagnostic | Kalshi API diagnostic — not for routine use |
| `discover-*.yml` | 🗄️ Diagnostic | Kalshi market discovery — not for routine use |
| `enumerate-*.yml` | 🗄️ Diagnostic | Market enumeration — not for routine use |

### Documentation
| File | Purpose |
|---|---|
| `docs/GITHUB_WRITE_GUIDE.md` | How to write files to this repo from Claude (SHA pattern, large payload pattern) |
| `docs/MODEL_HISTORY.md` | Version changelog, key decisions log, operational transitions |

### Data Files (written by Actions — not committed manually)
| File | Contents |
|---|---|
| `data/slate.json` | Full slate — odds, pitchers, bullpen, model probs, edges |
| `data/pitchers.json` | Confirmed starters |
| `data/teamstats.json` | Team hitting stats |
| `data/weather.json` | Park weather |
| `data/meta.json` | Fetch timestamp — verify before using |

---

## Session Startup (mandatory sequence)

1. Pull all four model files from GitHub raw:
   ```
   https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/RULES.md
   https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/MODEL_CORE.md
   https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/SLATE_WORKFLOW.md
   https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/DATA_SOURCES.md
   ```

2. Trigger `fetch-slate` GitHub Action with today's date

3. Wait ~40 seconds, verify `data/meta.json` fetchedAt is current

4. Pull `data/slate.json`, `data/pitchers.json`, `data/teamstats.json`, `data/weather.json`

5. Run full analysis per SLATE_WORKFLOW.md

---

## Key Design Decisions

**Why Kalshi as edge target?** Kalshi is the bet execution market. Edge is measured against what you can actually bet, not Pinnacle (which you can't access in the US).

**Why Pinnacle as sanity check?** Pinnacle is the sharpest market in the world and reflects sharp money accurately. Model diverging >7% from Pinnacle VF is a red flag.

**Why CLV over win rate?** Win rate has high variance. CLV measures process quality — if you consistently beat the closing line, you're finding real edges regardless of short-term outcomes.

**Why paper-only for Game Totals?** 32 bets, 41% WR, -$21.12. The market prices totals more efficiently than our model currently projects.

---

## Architecture Notes

- Vercel API (`https://edge-finder-api.vercel.app`) is **not directly accessible** from external networks (returns 403). All data flows through the `fetch-slate` GitHub Action.
- `bets.json` is a **flat JSON array** — no wrapper object. `json.load(f)` gives the list directly.
- GitHub writes require a **fresh SHA fetch** before every PUT. See `docs/GITHUB_WRITE_GUIDE.md`.
- CLV data before May 31, 2026 used Pinnacle closing lines as proxy (unreliable). CLV tracking with Kalshi lines started May 31, 2026.
