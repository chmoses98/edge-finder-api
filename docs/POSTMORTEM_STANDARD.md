# MLB Postmortem Standard

Status: mandatory, project-wide procedure for every MLB postmortem produced
in any ChatGPT/Claude project conversation about this repository. Builds on
`docs/CANONICAL_BET_LEDGER.md` — read that first for the ledger, schema,
write-path, and bankroll mechanics this document assumes and does not
repeat. This document is procedure and policy; it does not change
`lib/edgelab/bets.py`, `lib/edgelab/clv.py`, `lib/edgelab/settlement.py`,
`lib/edgelab/replay.py`, or any production recommendation/staking logic.

## 0. The canonical-era boundary

The official canonical betting era begins **2026-08-03**
(`lib.edgelab.canonical_era.CANONICAL_ERA_START_DATE`). The starting
bankroll for the canonical era is **$350.00**, recorded as the one
`STARTING_BALANCE` transaction in `data/edgelab/bankroll/transactions.jsonl`.

- Official postmortems begin with wagers placed on or after 2026-08-03.
- Pre-2026-08-03 wagers are **legacy-only**: they remain in
  `data/edgelab/bets/bets.jsonl` exactly as ingested (nothing about them is
  added, modified, cancelled, reinterpreted, or migrated by this standard),
  and stay fully queryable as history — but they are **excluded by default**
  from every official bankroll figure, win/loss record, total risked/
  returned, ROI, CLV summary, market-family performance breakdown,
  cumulative postmortem, bankroll chart, and canonical-era analytic.
  `lib.edgelab.canonical_era.canonical_era_bets()` / `legacy_bets()` and the
  `--include-legacy` flag on `generate_postmortem.py` /
  `query_bets.py --filter bankroll-history|canonical-era-summary` are the
  only mechanisms that draw this line, and it is a plain date-string
  comparison, not a schema change.
- Historical chats and pre-canonical-era postmortems may still be consulted
  for research/context, but they are **never** auto-imported into the
  official canonical ledger. That only happens if the user explicitly
  reverses this policy later, in writing, in a future conversation.

## 1. Read the ledger before doing anything else

**Before answering any question about actual wagers, or producing a
postmortem, read the canonical placed-bet ledger**
(`scripts/edgelab/query_bets.py` or `lib.edgelab.query` directly against
`data/edgelab/bets/bets.jsonl`). Do not rely on conversation memory as the
source of truth when the ledger is available — conversation memory can be
incomplete, stale, or simply wrong about what was actually confirmed and
saved.

If a wager was confirmed in chat but is not yet reflected in the ledger:

1. Generate the structured write payload (§ Section C format below, or the
   compact form in `docs/CANONICAL_BET_LEDGER.md` § 5).
2. Perform the real canonical write (`log_bet.py`, the "Record Placed Bet"
   GitHub Actions form, or `write_placed_bet` directly) — or instruct the
   user to do so.
3. Wait for confirmation (the real receipt — see § 5 of
   `CANONICAL_BET_LEDGER.md`).
4. Return a saved-bet receipt **only after** that write succeeds
   (`success: true`).

If direct GitHub/repository writing is unavailable in the current
conversation surface: provide the exact "Record Placed Bet" workflow input
values the user needs, tell them to run it manually, and **do not claim the
bet is saved**. A recommendation is not a placed bet, and a user's statement
that they placed a bet is not itself a repository write.

## 2. Lifecycle statuses

Every wager discussed in any project conversation is, at all times, in
exactly one of these states. Stating or implying a later state than the
evidence supports is never acceptable.

| Status | Meaning |
|---|---|
| `DISCUSSED` | Mentioned in conversation. No recommendation, no ledger row. |
| `RECOMMENDED` | The model or a manual analysis suggested it. Still no ledger row. |
| `USER_CONFIRMED_PLACED` | The user stated they placed this bet. No repository write has been attempted or completed yet. |
| `REPOSITORY_WRITE_ATTEMPTED` | A write to `bets.jsonl` (via `write_placed_bet`) was invoked, but its outcome (`success`, `duplicateStatus`, or an error) has not yet been confirmed back to the chat. |
| `REPOSITORY_SAVED` | The write succeeded — `write_placed_bet` (or the GitHub form) returned `success: true` with `duplicateStatus` one of `NEW`, `DUPLICATE_NOOP`, `CORRECTED`. The bet is now genuinely in `bets.jsonl`. |
| `SETTLED` | `status: "settled"` with a real `result`, via `scripts/edgelab/settle_markets.py` — evidence-based, never guessed. |

Two rules follow directly from this table and must never be violated:

- **A recommendation is not a placed bet.** `RECOMMENDED` never implies
  `USER_CONFIRMED_PLACED`, no matter how strong the edge.
- **A user statement confirming placement is not repository-saved until a
  real GitHub write succeeds.** `USER_CONFIRMED_PLACED` and
  `REPOSITORY_WRITE_ATTEMPTED` are not `REPOSITORY_SAVED`. **No chat may
  claim a bet is "saved" without repository confirmation** (the real
  receipt, `success: true`).

## 3. Missing-information policy

Never fabricate any of the following, in a postmortem, a research note, or
a structured ledger-update payload:

- ticker (`marketTicker`, `eventTicker`, `seriesTicker`)
- price (`entryPrice`, `closingPrice`)
- stake
- timestamp (`entryTimestamp`, `placedAt`)
- side / selection
- threshold
- result
- closing price / CLV
- model linkage (`recommendationId`, `modelEvaluationId`)
- snapshot linkage (`snapshotId`, `replayRunId`)
- the reason/rationale for a wager

If a field is not known with evidence, mark it explicitly as missing (e.g.
`null` with an `unresolvedFieldReasons` entry — see § Section C below) and
name the exact follow-up needed to resolve it (e.g. "need the exact
ticker from the execution slip" or "need the user to confirm stake"). A
placeholder, a best guess, or an inferred value written in as if it were
known is a policy violation regardless of how plausible it looks.

## 4. The three mandatory postmortem outputs

Every MLB postmortem — daily or on request — must contain all three of the
following. None may be skipped or merged into a briefer summary.

### A. Human-readable postmortem

For every wager **actually placed** (i.e. `REPOSITORY_SAVED` or later —
never a `RECOMMENDED`-only pick), include:

- stake
- entry price
- result
- gross return
- net profit/loss
- the day's record (wins-losses-pushes-voids-pending)
- total risked
- total returned
- ROI
- CLV, when available (never fabricated when it isn't)
- updated bankroll (canonical-era bankroll — see § 0)
- a brief process assessment

**Do not call every loss a bad bet or every win a good bet.** Evaluate the
quality of the decision (was the edge real, was the process sound, did new
information emerge post-bet) separately from the result (win or loss is one
outcome of a probabilistic process, not proof the decision was right or
wrong).

`lib.edgelab.reports.build_postmortem` (single day) and
`lib.edgelab.reports.build_canonical_era_summary` (cumulative, canonical-era
bets only by default) already compute every numeric field above from the
canonical ledger — use them rather than hand-computing totals from memory.

### B. Research notes for every bet

A concise, evidence-based note per wager, including only what was actually
part of the decision at the time it was made. Supported themes (use
whichever genuinely applied — never all of them for padding):

- starting-pitcher edge
- bullpen advantage or concern
- lineup impact
- platoon or matchup edge
- weather
- park
- umpire
- workload
- strikeout or contact matchup
- price discrepancy
- why a particular market expression was chosen (F3/F5/F7/full game/total/
  team total/prop) over the alternatives
- whether the bet was model-supported, analyst-recommended, manually added,
  or a user override

**Never invent a rationale or thesis tag after the fact.** If the note
cannot be reconstructed from what was actually said/computed at decision
time, say so — do not backfill a plausible-sounding justification.

### C. Claude-ready canonical-ledger update

Every postmortem must end with a structured, machine-readable block (JSON,
or a fenced code block a chat/tool can parse) containing, for every wager
placed, wherever available:

```json
{
  "date": "2026-08-03",
  "gameDate": "2026-08-03",
  "matchup": "NYY @ BOS",
  "gameId": "2026-08-03_NYY_BOS",
  "marketTicker": "KXMLBF5-26AUG031804NYYBOS-NYY",
  "eventTicker": null,
  "seriesTicker": null,
  "marketFamily": "F5_ML",
  "marketHorizon": "F5",
  "selection": "NYY F5 winner",
  "side": "YES",
  "threshold": null,
  "stake": 25,
  "entryPrice": 0.54,
  "entryOdds": null,
  "placedAt": "2026-08-03T18:04:00Z",
  "entryMethod": "MANUAL_CHAT_CONFIRMED",
  "result": null,
  "grossReturn": null,
  "netProfitLoss": null,
  "closingPrice": null,
  "clv": null,
  "confidence": "MEDIUM",
  "thesisTags": ["starting_pitcher_edge"],
  "correlationGroups": [],
  "recommendationId": null,
  "modelEvaluationId": null,
  "snapshotId": null,
  "replayRunId": null,
  "notes": "Confirmed lineup; SP edge per pregame analysis.",
  "provenance": {"sourceSystem": "chat", "capturedAt": "2026-08-03T18:04:00Z"},
  "validationStatus": "valid",
  "unresolvedFieldReasons": {}
}
```

Any field not knowable with evidence is `null`, with a matching entry in
`unresolvedFieldReasons` naming exactly what's missing and what would
resolve it (e.g. `"closingPrice": "CLV collection has not run for this date yet"`).
This structured output must be directly ready for Claude (or another
repository-writing tool) to reconcile against `data/edgelab/bets/bets.jsonl`
— field names match the `PlacedBet` schema
(`data/edgelab/schema_v1/placed_bet.schema.json`) so no renaming/guessing is
needed before a write is attempted.

## 5. Canonical-era postmortem behavior

- `scripts/edgelab/generate_postmortem.py` and
  `scripts/edgelab/query_bets.py --filter bankroll-history|canonical-era-summary`
  filter to canonical-era bets **by default**; pass `--include-legacy` for an
  explicit full-history view, which is always labelled `legacyIncluded: true`
  in its own output so it is never mistaken for the official record.
- A postmortem requested for a pre-2026-08-03 date is, by definition, a
  **legacy** postmortem — it is not part of the official canonical-era
  record and should be labelled as such if produced.
- Pre-2026-08-03 wagers never affect the canonical-era bankroll, official
  win/loss record, ROI, or any other canonical-era total, by default, in any
  report produced under this standard.
