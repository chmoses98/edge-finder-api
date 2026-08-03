# EdgeLab Forward Replay Corpus Health Report
Generated: 2026-08-03T01:14:43Z

## Coverage
- Production runs: 4
- Expected pregame snapshots: 4
- Snapshots captured: 4
- Missing snapshots: 0 []
- Snapshots recovered (cumulative): 0
- productionCommitSha coverage: 0/4
- effectiveConfigHash coverage: 4/4

## Candidate replay
- Attempted: 2
- Completed: 2
- Failed: 0
- Markets replayed: 330 (comparable: 245)
- CLV-linked: 95, settlement-linked: 0
- Oldest trustworthy (Level 2) replay date: 2026-08-01
- Newest trustworthy (Level 2) replay date: 2026-08-02

## Storage
- Snapshots: 5,069,458 bytes
- Replay runs: 486,360 bytes
- Total: 5,555,818 bytes

## Quality gates
- Consecutive degraded runs (most recent backward): 4
- DEGRADED_CONFIG_PARTIAL: 2
- DEGRADED_MISSING_SNAPSHOT: 2

## Per-date detail
| Date | Gate Status | Completeness | Commit SHA Known | Replay | Runs |
|---|---|---|---|---|---|
| 2026-07-30 | DEGRADED_CONFIG_PARTIAL | PARTIAL_REPLAY | False | None | 1 |
| 2026-07-31 | DEGRADED_MISSING_SNAPSHOT | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-01 | DEGRADED_MISSING_SNAPSHOT | MISSING_REQUIRED_INPUT | False | None | 1 |
| 2026-08-02 | DEGRADED_CONFIG_PARTIAL | PARTIAL_REPLAY | False | None | 1 |
