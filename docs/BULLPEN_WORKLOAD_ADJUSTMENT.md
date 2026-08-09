# Bullpen workload (recent availability) adjustment

Incorporates PR #51's `bullpen.recentUsage` data (see
`docs/BULLPEN_CONTEXT.md`) into the pregame full-game projection engine.
Data/model change, scoped narrowly: touches only
`scripts/build_market_ledger.py`'s `compute_projections()` /
`compute_game_projection_context()` and adds one new pure module. Does
**not** change ledger structure, settlement, CLV, correlation gates,
pitcher-prop modeling, or staking.

## The gap this closes

Before this milestone, `compute_projections()` read `bullpen.xFIP`
(season-long quality) for the bullpen-innings portion of a team's
runs-allowed projection, and nothing else — PR #51's `recentUsage` block
(back-to-back relievers, recent pitch counts, high-leverage recent
usage, team pitch counts) was captured but never wired into the model,
so a materially rested, taxed, or short-handed bullpen produced the
*exact same* full-game ML/Game Total/team-total projection as a fully
healthy one with identical season stats.

## Design

`lib/edgelab/bullpen_availability.compute_bullpen_workload_adjustment(recent_usage)`
is a pure function that turns one team's `bullpen.recentUsage` block
into a conservative multiplier (`>= 1.0`) applied to that team's
season-long pen xFIP:

- **Back-to-back relievers** (`backToBackRelievers`) — penalizes each
  reliever who appeared in both of the last two games.
- **Recent pitch workload** (`recentPitchCounts`) — penalizes individual
  relievers heavily used across the window, independent of role.
- **High-leverage recent usage** (`highLeverageRecentUsage`) — penalizes
  save/hold relievers who are themselves recently taxed (the arms a
  team can least afford to lose).
- **Overall recent bullpen workload** (`teamPitchCountWindow` /
  `teamPitchCountLastGame`) — penalizes an aggregate workload above a
  generic per-game baseline, checked against both the whole window and
  the single most recent game.

Every component, and the combined total, is individually capped
(`MAX_TOTAL_PENALTY = 0.12` → multiplier never exceeds `1.12`) — a
modest, transparent nudge, not a large swing driven by one box score.
Thresholds are generic pitch-count/appearance-count constants, not
tuned to any specific team, date, or slate (no fitting to Aug 3–8
results).

**No-bonus guarantee:** every component is 0 or positive. There is no
code path that returns a multiplier below 1.0 — a lightly-used bullpen
simply gets the neutral multiplier, identical to having no `recentUsage`
data at all. Season-long quality (`bullpen.xFIP`) is never touched by
this module; the two signals stay separate.

**Missing data:** `recentUsage` absent, or present with
`dataAvailable=False` (PR #51's own explicit flag), always yields
multiplier `1.0` — never a guessed "the bullpen must be rested" bonus.

## Where it's applied

`compute_projections()` multiplies each side's season pen xFIP by its
own recent-workload multiplier, applied **only** to the bullpen-innings
term of the full-game runs-allowed formula:

```
away_proj = away_off_factor * (home_starter_ip * home_xfip/9
                                + home_pen_ip * home_pen_xfip/9) + park_adj
```

`home_pen_xfip` now includes the home bullpen's workload multiplier.
Because `home_pen_ip = max(0, 9 - home_starter_ip)`, the adjustment only
moves the projection in games where the bullpen is actually projected to
throw meaningful innings — a starter projected to go the distance
contributes `home_pen_ip = 0`, so the multiplier has no effect regardless
of workload.

This automatically produces the required cross-market behavior:

- **Full-game ML** — a taxed HOME bullpen raises `away_proj` (the away
  team scores more against it), lowering the home team's full-game win
  probability.
- **Opponent team total** — the same `away_proj` increase raises
  `TT_Away_Over`'s model probability.
- **Game total** — `totalProj = away_proj + home_proj` rises, raising
  `Game_Total`'s model probability.
- **A team's own team total is unaffected by its own bullpen** —
  `TT_Home_Over` is driven by `home_proj`, which depends on the *away*
  side's pitching, not the home bullpen.

**F3/F5 are structurally unaffected.** Both `compute_projections()`'s F5
branch and `lib/kalshi_period_projections.py`'s F3 branch use only
starter xFIP (capped at the horizon's innings) — neither reads
`bullpen.xFIP` or `bullpen.recentUsage` at all. A compromised bullpen
therefore moves the full-game projection while F5/F3 stay exactly the
same, preserving the ability for F5 to become the structurally
preferable market when a team's bullpen is compromised.

## Debug/audit output

`compute_game_projection_context()` exposes the applied adjustment as
`awayBullpenAvailability` / `homeBullpenAvailability` — each team's
multiplier, whether it was applied, `dataAvailable`/`unavailableReason`,
and the full component breakdown. These flow through
`evaluate_game()`'s `proj_context` onto every market row (`ML_Away`,
`ML_Home`, `Game_Total`, `TT_Away_Over`, `TT_Home_Over`, `F5_ML_Away`,
`F5_ML_Home`, `RL_Away`, `RL_Home`, `NRFI`, `YRFI`), so any fair
probability's movement can be traced back to its cause.

## Files

- `lib/edgelab/bullpen_availability.py` — pure adjustment function (new)
- `scripts/build_market_ledger.py` — wires the multiplier into
  `compute_projections()`, exposes the debug dicts via
  `compute_game_projection_context()` / `evaluate_game()` / `make_row()`
- `tests/edgelab/test_bullpen_availability.py` — pure-function coverage
- `tests/test_bullpen_workload_pregame.py` — projection/ledger
  integration coverage
