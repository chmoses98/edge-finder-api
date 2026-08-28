# Lineup / Hitter-Aggregation PIT Feasibility Audit

Performed as part of the overnight 2026-08-28 research session, before any
MLB-RSCH-0013 candidate design. RESEARCH ONLY.

## Question

Can richer hitter-level information (starting lineup, batting order,
hitter identity) improve TEAM expected runs beyond the best team-level
baseline? This requires a genuinely PIT-safe, multi-season-scale
historical archive of actual pregame starting lineups.

## Findings

Two related lineup-capture mechanisms exist in this repository:

1. **`data/lineup_audit_<date>.json`** (daily, via `fetch-slate.yml`) —
   summary-level (`lineupConfirmedOfficial`, batter-count-resolved
   fields; **no player identities or batting-order slots**), earliest
   file **`data/lineup_audit_2026-06-15.json`**. Depth: **~2.5 months**
   (2026-06-15 through today).
2. **`lib/edgelab/prospective_snapshot.py`'s `LINEUP_CONFIRMATION`
   checkpoint** (15-minute cron, live-polled) — genuinely PIT-safe *for
   the fields it live-polls* per its own manifest entry
   (`lib.edgelab.pit_provenance.PIT_MANIFEST["lineup_status_official"]`,
   `pitStatus=PROSPECTIVE_ONLY`), but this system itself only exists
   from its own recent deployment forward — the SAME manifest entry's
   own `knownGaps` states plainly: "No historical archive of lineup
   state AT EACH checkpoint before this system's deployment."

**Neither source has any depth before 2026 at all**, let alone the
2022-2026 multi-season scale this research program's other experiments
use. There is no path to reconstructing a *historical* pregame lineup
for a 2022-2024 or 2025 game — MLB Stats API's own boxscore/lineup
data for a past date reflects the CONFIRMED (postgame-knowable) lineup,
not a preserved pregame announcement, and no separate archive of
pregame lineup cards exists anywhere in this repository or its caches.

## Classification (per the audit's own required distinction)

| | |
|---|---|
| A. TRUE PREGAME LINEUP ARCHIVE (multi-season) | **Does not exist** |
| B. Retrospectively reconstructed final lineup | Would be the only way to get 2022-2025 lineups — explicitly NOT pregame-safe, never used as such |
| C. Postgame batting order | Available via boxscores (not persisted historically for this purpose), same disqualification as B |
| D. **Prospective-only lineup capture** | **This is what exists** — `lineup_audit_*.json` (~2.5 months) and the `LINEUP_CONFIRMATION` checkpoint (recent deployment only), both genuinely PIT-safe *going forward*, zero historical depth |

## Verdict

**NOT FEASIBLE for a multi-season (2022-2024 dev / 2025 val / 2026
holdout) MLB-RSCH-0013 tonight.** The available lineup data is Category
D only — real, genuinely PIT-safe, but single-season and only ~2.5
months deep. This cannot support the chronological split every other
experiment in this program uses, and forcing one would produce an
underpowered, single-season-only result masquerading as the same
evidence tier as MLB-RSCH-0009 through MLB-RSCH-0012's 2022-2026 corpus.

Per the task's own explicit instruction ("Do not force a weak
experiment"), no lineup-based MLB-RSCH-0013 was built. If lineup-driven
research is wanted later, it would need to accumulate for several more
seasons first (a multi-year wait), or be run as an explicitly-labeled
SMALL, single-season, exploratory (E1/E2-limited) study — a materially
different, much weaker experiment than what this research program's
other milestones deliver, and not preregistered or run tonight.

**Fallback chosen**: MLB-RSCH-0013 is instead "Bullpen Talent
Refinement" (Option A from the task's own menu) — see
`docs/EDGELAB_MLB_RSCH_0013_BULLPEN_TALENT.md`.
