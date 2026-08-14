# EdgeLab Forward Replay Corpus Health Report
Generated: 2026-08-14T08:16:05Z

## Enforcement
- Status: **ACTIVE**
- Boundary date: 2026-08-03
- Activated at: 2026-08-04T09:38:45Z
- Exit should fail: True
- Exit-code reason: 4 forward-era date(s) with a hard-fail gate status: [('2026-08-11', 'FORWARD_MISSING_SNAPSHOT'), ('2026-08-12', 'FORWARD_MISSING_SNAPSHOT'), ('2026-08-13', 'FORWARD_MISSING_SNAPSHOT'), ('2026-08-14', 'FORWARD_MISSING_SNAPSHOT')]

## Historical corpus quality (descriptive only -- never fails this check)
- Historical/backfill dates: 4
- DEGRADED_CONFIG_PARTIAL: 2
- DEGRADED_MISSING_SNAPSHOT: 2

## Forward operational health (drives pass/fail)
- Expected forward runs: 8
- Forward snapshots captured: 8
- Forward snapshots missing: 0 []
- Forward provenance coverage: 8/12
- Forward replay: attempted 9, completed 9, failed 0
- Forward CLV-linked markets: 0
- Forward settlement-linked markets: 0
- Consecutive degraded forward runs: 4
- Hard-fail dates: ['2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14']
- FORWARD_HEALTHY: 8
- FORWARD_MISSING_SNAPSHOT: 4

## Storage
- Snapshots: 47,836,399 bytes
- Replay runs: 2,342,496 bytes
- Total: 50,178,895 bytes

## Per-date detail
| Date | Era | Gate Status | Forward Gate Status | Completeness | Commit SHA Known | Replay | Runs |
|---|---|---|---|---|---|---|---|
| 2026-07-30 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
| 2026-07-31 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-01 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-02 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | False | None | 1 |
| 2026-08-03 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-04 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-05 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-06 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 2 |
| 2026-08-07 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-08 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-09 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-10 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | True | COMPLETED | 1 |
| 2026-08-11 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | False | None | 0 |
| 2026-08-12 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | False | None | 0 |
| 2026-08-13 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | False | None | 0 |
| 2026-08-14 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | False | None | 0 |
