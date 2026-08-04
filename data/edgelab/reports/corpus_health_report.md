# EdgeLab Forward Replay Corpus Health Report
Generated: 2026-08-04T09:38:45Z

## Enforcement
- Status: **ACTIVE**
- Boundary date: 2026-08-03
- Activated at: 2026-08-04T09:38:45Z
- Exit should fail: False
- Exit-code reason: Forward operational health is clean -- no hard-fail dates, no consecutive-degraded escalation.

## Historical corpus quality (descriptive only -- never fails this check)
- Historical/backfill dates: 4
- DEGRADED_CONFIG_PARTIAL: 2
- DEGRADED_MISSING_SNAPSHOT: 2

## Forward operational health (drives pass/fail)
- Expected forward runs: 1
- Forward snapshots captured: 1
- Forward snapshots missing: 0 []
- Forward provenance coverage: 1/1
- Forward replay: attempted 1, completed 1, failed 0
- Forward CLV-linked markets: 0
- Forward settlement-linked markets: 0
- Consecutive degraded forward runs: 0
- Hard-fail dates: []
- FORWARD_HEALTHY: 1

## Storage
- Snapshots: 7,608,494 bytes
- Replay runs: 615,876 bytes
- Total: 8,224,370 bytes

## Per-date detail
| Date | Era | Gate Status | Forward Gate Status | Completeness | Commit SHA Known | Replay | Runs |
|---|---|---|---|---|---|---|---|
| 2026-07-30 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
| 2026-07-31 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-01 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-02 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
| 2026-08-03 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
