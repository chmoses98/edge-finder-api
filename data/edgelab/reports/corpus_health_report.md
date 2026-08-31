# EdgeLab Forward Replay Corpus Health Report
Generated: 2026-08-31T14:52:49Z

## Enforcement
- Status: **ACTIVE**
- Boundary date: 2026-08-03
- Activated at: 2026-08-04T09:38:45Z
- Exit should fail: False
- Exit-code reason: Forward operational health is otherwise clean -- the only hard-fail-status forward date(s) are acknowledged, permanently-unrecoverable legacy gaps (['2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14', '2026-08-15']), which never resolve and therefore never drive this exit code -- see data/edgelab/corpus_acknowledged_forward_gaps.json.

## Historical corpus quality (descriptive only -- never fails this check)
- Historical/backfill dates: 4
- DEGRADED_CONFIG_PARTIAL: 2
- DEGRADED_MISSING_SNAPSHOT: 2

## Forward operational health (drives pass/fail)
- Population note: expectedRuns/snapshotsCaptured/snapshotsMissing/incompleteCaptures/provenanceCoverage all share ONE population: every known forward-era date (from production OR snapshot evidence) excluding pendingTodayDates. snapshotsCaptured + len(snapshotsMissing) == expectedRuns always; incompleteCaptures is a SUBSET of dates counted inside snapshotsCaptured (they have a manifest, it's just incomplete), never inside snapshotsMissing.
- Expected forward runs: 28
- Forward snapshots captured: 23
- Forward snapshots missing (no manifest at all): 5 ['2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14', '2026-08-15']
- Forward incomplete captures (manifest exists, missing a required component): 0 []
- Forward dates pending today (not yet due): 1 ['2026-08-31']
- Forward provenance coverage: 23/28
- Forward replay: attempted 65, completed 37, failed 28
- Forward CLV-linked markets: 44
- Forward settlement-linked markets: 89
- Consecutive degraded forward runs: 0
- Hard-fail dates (drive exitShouldFail): []
- Acknowledged legacy gap dates (excluded from exitShouldFail, see data/edgelab/corpus_acknowledged_forward_gaps.json): ['2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14', '2026-08-15']
- FORWARD_HEALTHY: 20
- FORWARD_MISSING_SNAPSHOT: 5
- FORWARD_PENDING_TODAY: 1
- FORWARD_RESEARCH_ONLY_NO_DECISION: 3

## Storage
- Snapshots: 146,048,471 bytes
- Replay runs: 8,820,916 bytes
- Total: 154,869,387 bytes

## Per-date detail
| Date | Era | Gate Status | Forward Gate Status | Stored Completeness | Effective Completeness | Research-Only | Commit SHA Known | Replay | Runs | Acknowledged Gap |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-07-30 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | PARTIAL_REPLAY | False | False | None | 1 |  |
| 2026-07-31 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | MISSING_REQUIRED_INPUT | False | False | None | 1 |  |
| 2026-08-01 | HISTORICAL | DEGRADED_MISSING_SNAPSHOT | None | MISSING_REQUIRED_INPUT | MISSING_REQUIRED_INPUT | False | False | None | 1 |  |
| 2026-08-02 | HISTORICAL | DEGRADED_CONFIG_PARTIAL | None | PARTIAL_REPLAY | PARTIAL_REPLAY | False | False | None | 1 |  |
| 2026-08-03 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-04 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-05 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-06 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 2 |  |
| 2026-08-07 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-08 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-09 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-10 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-11 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | None | False | False | None | 0 | YES: TERMINAL_UNRECOVERABLE_PRODUCTION_GAP |
| 2026-08-12 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | None | False | False | None | 0 | YES: TERMINAL_UNRECOVERABLE_PRODUCTION_GAP |
| 2026-08-13 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | None | False | False | None | 0 | YES: TERMINAL_UNRECOVERABLE_PRODUCTION_GAP |
| 2026-08-14 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | None | False | False | None | 0 | YES: TERMINAL_UNRECOVERABLE_PRODUCTION_GAP |
| 2026-08-15 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_MISSING_SNAPSHOT | None | None | False | False | None | 0 | YES: TERMINAL_UNRECOVERABLE_PRODUCTION_GAP |
| 2026-08-16 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-17 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-18 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-19 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 2 |  |
| 2026-08-20 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 1 |  |
| 2026-08-21 | FORWARD | HEALTHY | FORWARD_RESEARCH_ONLY_NO_DECISION | MISSING_REQUIRED_INPUT | PARTIAL_REPLAY | True | True | NOT_APPLICABLE_NO_DECISION | 1 |  |
| 2026-08-22 | FORWARD | HEALTHY | FORWARD_RESEARCH_ONLY_NO_DECISION | MISSING_REQUIRED_INPUT | PARTIAL_REPLAY | True | True | NOT_APPLICABLE_NO_DECISION | 3 |  |
| 2026-08-23 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 5 |  |
| 2026-08-24 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 4 |  |
| 2026-08-25 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 5 |  |
| 2026-08-26 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 4 |  |
| 2026-08-27 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 2 |  |
| 2026-08-28 | FORWARD | HEALTHY | FORWARD_RESEARCH_ONLY_NO_DECISION | PARTIAL_REPLAY | PARTIAL_REPLAY | True | True | NOT_APPLICABLE_NO_DECISION | 8 |  |
| 2026-08-29 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 6 |  |
| 2026-08-30 | FORWARD | HEALTHY | FORWARD_HEALTHY | PARTIAL_REPLAY | PARTIAL_REPLAY | False | True | COMPLETED | 5 |  |
| 2026-08-31 | FORWARD | DEGRADED_MISSING_SNAPSHOT | FORWARD_PENDING_TODAY | None | None | False | False | None | 0 |  |
