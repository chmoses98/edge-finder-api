# MLB Research Program — Monitoring Status

**Active research sprint:** `CLOSED`  
**Mode:** `PROSPECTIVE_MONITORING`  
**Closed on:** 2026-08-30

Counts only. No inference is drawn below a preregistered floor, and reaching a floor
is a trigger to REVIEW — never an approval to promote.

## Checkpoints

| Surface | Progress | Floor | Reached |
|---|---|---|---|
| KXMLBF5 | 69 games / 21 dates | 100 games | no — short by 31 |
| TEAM_TOTAL_NB_V1 shadow | 0 captured rows | 100 games / 10 dates | HEALTH_PENDING_NATURAL_CYCLE |
| Frozen forward scorer | CHECKPOINT_0 (11 games) | its own checkpoints | INSUFFICIENT_FORWARD_DATA |

## Sidecars (accumulate automatically)

| Sidecar | Partitions | Rows | Latest |
|---|---:|---:|---|
| NB shadow (MLB-RSCH-0011) | 2 | 29 | 2026-08-30.jsonl |
| Uncertainty capture (MLB-RSCH-0019) | 2 | 29 | 2026-08-30.jsonl |

## A new active experiment requires one of these

- TRIGGER A -- F5 reaches 100 independent games
- TRIGGER B -- TEAM_TOTAL_NB_V1 reaches 100 games / 10 dates
- TRIGGER C -- the general frozen forward scorer reaches its checkpoint
- TRIGGER D -- a genuine production bug is discovered
- TRIGGER E -- a new research question is explicitly authorised

Otherwise: **monitor only.**
