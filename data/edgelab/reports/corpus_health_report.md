# EdgeLab Forward Replay Corpus Health Report
Generated: 2026-08-03T04:19:31Z

## Enforcement
- Status: **AWAITING_FIRST_FORWARD_CAPTURE**
- Boundary date: None
- Activated at: None
- Exit should fail: False
- Exit-code reason: No qualifying forward production run has been captured yet (no PRE_GAME_DECISION snapshot with captureMode=LIVE_CAPTURE and productionProvenance.status=CAPTURED exists) -- enforcement is not yet active, so this check always passes. See historicalCorpusQuality for descriptive-only corpus state.

## Historical corpus quality (descriptive only -- never fails this check)
- Historical/backfill dates: 4
- DEGRADED_CONFIG_PARTIAL: 2
- DEGRADED_MISSING_SNAPSHOT: 2

## Forward operational health (drives pass/fail)
- Expected forward runs: 0
- Forward snapshots captured: 0
- Forward snapshots missing: 0 []
- Forward provenance coverage: 0/0
- Forward replay: attempted 0, completed 0, failed 0
- Forward CLV-linked markets: 0
- Forward settlement-linked markets: 0
- Consecutive degraded forward runs: 0
- Hard-fail dates: []

## Storage
- Snapshots: 5,069,458 bytes
- Replay runs: 486,360 bytes
- Total: 5,555,818 bytes

## Per-date detail
| Date | Era | Gate Status | Forward Gate Status | Completeness | Commit SHA Known | Replay | Runs |
|---|---|---|---|---|---|---|---|
| 2026-07-30 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
| 2026-07-31 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-01 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-02 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
