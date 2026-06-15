# GitHub Actions Secrets — Required Configuration

This document is the authoritative reference for secrets required by all workflows in this repo.

## Required Secrets

| Secret | Used By | Purpose |
|--------|---------|---------|
| `ODDS_API_KEY` | `fetch-slate.yml`, `clv-update.yml` | The Odds API key for fetching sportsbook odds and historical scores for settlement |

That is the **only** secret required. Both workflows that use it are:
- `fetch-slate.yml` → step "Fetch odds" (`scripts/validate_odds.py`) and "Capture Kalshi closing lines" (`scripts/capture_closing_lines.py`)
- `clv-update.yml` → step "Run CLV update" (`clv_update.py`) for post-game settlement via The Odds API historical scores endpoint

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
