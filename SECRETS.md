# GitHub Actions Secrets — Required Configuration

This document is the authoritative reference for secrets required by all workflows in this repo.

## Required Secrets

| Secret | Used By | Purpose |
|--------|---------|---------|
| `ODDS_API_KEY` | `fetch-slate.yml`, `clv-update.yml` | The Odds API key for fetching sportsbook odds and historical scores for settlement |
| `KALSHI_BANKROLL_CONTEXT` | `build-handicapping-card.yml` → "Build real-money handicapping card" | The sealed authenticated available-cash reading. **Written by `kalshi-bet-router`, never by a human.** |
| `ROUTER_BANKROLL_DISPATCH_TOKEN` | `fetch-slate.yml` → job `refresh_bankroll` | Lets a slate build ask the router for a *fresh* reading before the card is built. Optional; absent, the card falls back to fail-closed. |

`ODDS_API_KEY` is used by:
- `fetch-slate.yml` → step "Fetch odds" (`scripts/validate_odds.py`) and "Capture Kalshi closing lines" (`scripts/capture_closing_lines.py`)
- `clv-update.yml` → step "Run CLV update" (`clv_update.py`) for post-game settlement via The Odds API historical scores endpoint

### `KALSHI_BANKROLL_CONTEXT` — do not set this by hand

`kalshi-bet-router` reads the balance (one `GET /portfolio/balance`) and
seals the result into this secret with libsodium. Its contents are a
small JSON object — `schemaVersion, bankroll, currency, observedAt,
source, valueType` — and `lib/bankroll_context.py` refuses to size in
dollars unless `valueType == KALSHI_AVAILABLE_CASH_BALANCE`, `source ==
kalshi_authenticated_balance`, and `observedAt` is **less than 30
minutes old**. A hand-written value will simply be refused.

The amount never reaches a committed file or a log: the card stores
`lib/bankroll_context.py`'s `redacted()` projection (an allowlist, so a
new field cannot leak by omission), and `data/handicap_runtime/` carries
only `status`, `source`, `valueType`, `observedAt`, `ageMinutes`,
`sizingAllowed` and the sizing verdict.

### `ROUTER_BANKROLL_DISPATCH_TOKEN` — how to create it

**Why it exists.** The 30-minute sizing window and GitHub's scheduler had
no relationship to each other. The router's publisher is scheduled
`*/15`, but on 2026-09-19 the scheduler actually fired it at 17:48,
19:31, 20:08, 22:19 and 00:15. Every one of those runs SUCCEEDED, and the
public runtime still read `bankrollStatus: STALE` /
`dollarSizingVerdict: NO_DOLLAR_SIZING` all evening, because no card
build ever happened within 30 minutes of one. Raising the cron frequency
does not fix that; it just gives the same best-effort scheduler more
chances to be late.

So `fetch-slate.yml` now **pulls** a reading, and the card is built by a
**separate run** that starts afterwards.

That second part is not incidental. `secrets.*` is snapshotted when a
workflow **run is created**, not when a job starts — measured in
production on 2026-09-20: run `35520484888` was created at 15:43:41Z,
sealed a fresh reading at 15:45:23Z, and the card it built three minutes
later still read the *previous* 13:58:57Z reading (`ageMinutes 109.9`,
`STALE`). A run created before the seal can never see it, however its
jobs are arranged. So `fetch-slate.yml` refreshes the bankroll, publishes
the slate, and then dispatches **`Build Handicapping Card`**, whose run is
created after the seal and therefore snapshots the fresh value.

`Build Handicapping Card` is also runnable by hand with a date, which is
the supported way to rebuild a card after lineups confirm.

The coupling needs one credential:

1. GitHub → Settings → Developer settings → **Fine-grained personal
   access tokens** → Generate new token.
2. **Resource owner:** `chmoses98`. **Repository access:** *Only select
   repositories* → **`chmoses98/kalshi-bet-router`** and nothing else.
3. **Repository permissions:** **Actions: Read and write**. Leave every
   other permission at *No access* — in particular Contents and Secrets.
4. Add it to **this** repository (`edge-finder-api`) as an Actions secret
   named `ROUTER_BANKROLL_DISPATCH_TOKEN`.

That token can start a workflow in the router and read run status. It
cannot read a secret, cannot push a commit, and cannot touch any other
repository. It never sees the balance — only the router holds a Kalshi
credential.

**If it is absent** (forks, or before you create it) the job reports
`NO_CREDENTIAL`, exits 0, and the slate fetch proceeds exactly as before.
The card then reads whatever the secret already holds and refuses dollar
sizing if it is stale — today's behaviour, unchanged. Nothing breaks; you
just do not get just-in-time freshness.

## Not Required

| Credential | Status | Why |
|------------|--------|-----|
| `KALSHI_API_KEY` | **NOT NEEDED — removed** | Was referenced in `clv_capture.yml` but never used by the script. Kalshi's direct API (`api.elections.kalshi.com`) requires auth and returns 403 unauthenticated, but our pipeline never calls it directly. |
| `KALSHI_API_KEY_ID` | **NOT NEEDED** | Kalshi's RSA-key auth model uses `key_id` + `private_key` pair. We do not use Kalshi's authenticated API. |
| `KALSHI_PRIVATE_KEY` | **NOT NEEDED** | Same as above. |

## How Kalshi Data Flows (No Auth Required)

All Kalshi market price data reaches the repo through our **Vercel proxy** (`edge-finder-api.vercel.app`), which runs server-side and handles any necessary access. The proxy endpoints are public:

```
https://edge-finder-api.vercel.app/api/kalshi          → ML market prices
https://edge-finder-api.vercel.app/api/kalshisearch    → All MLB markets (F5, TT, NRFI/YRFI, etc.)
```

These are called by `fetch-slate.yml` and `capture-snapshots-scheduled.yml`.

### Data flow for CLV capture

```
capture-snapshots-scheduled.yml  (every 30min, no auth)
  └── calls edge-finder-api.vercel.app/api/kalshisearch
  └── writes data/kalshi_registry_snapshots/kalshi_search_DATE.json
             data/kalshi_registry_snapshots/kalshi_search_DATE_HHMM.json

clv_capture.yml  (every 10min, no auth)
  └── runs scripts/capture_clv_pregame.py
  └── reads data/kalshi_registry_snapshots/kalshi_search_DATE.json  ← fresh prices
  └── fallback: data/kalshi_raw.json  ← ML markets from last fetch-slate run
  └── writes data/clv_snapshots/DATE/pregame_GAMEPK.json

clv-update.yml  (daily 1am ET, uses ODDS_API_KEY only)
  └── runs scripts/run_kalshi_clv_step.py
  └── reads data/kalshi_registry_snapshots/  ← same snapshot archive
  └── calls The Odds API for historical scores (ODDS_API_KEY required)
```

## Setting Up

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add only:
```
Name:  ODDS_API_KEY
Value: <your The Odds API key>
```

`GITHUB_TOKEN` is built-in and does not need to be created manually.
