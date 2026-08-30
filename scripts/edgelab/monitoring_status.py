#!/usr/bin/env python3
"""
scripts/edgelab/monitoring_status.py
====================================
Read-only checkpoint status for PROSPECTIVE MONITORING MODE.

The MLB active research sprint closed on 2026-08-30. Nothing new is
being studied; the repository simply accumulates evidence until a
preregistered threshold is crossed. This script answers one question --
"has anything reached its trigger yet?" -- by counting what is already
committed.

DELIBERATELY SMALL. It is not a governance subsystem, it schedules
nothing, and it starts nothing. It reads committed partitions, prints a
table, and writes one JSON + one Markdown file under the existing
data/edgelab/reports/ convention. There is no workflow wiring, on
purpose: a monitoring check that runs itself every day creates daily
churn and daily work, which is the opposite of monitoring mode.

READ-ONLY. Writes only to data/edgelab/reports/. Never touches
observations/, model_evaluations/, settlements/, recommendations/,
games/ or bets/. Never promotes anything, and reports no inference below
a preregistered floor.

Usage:
    python3 scripts/edgelab/monitoring_status.py
"""
import glob
import gzip
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

REPORTS_DIR = os.path.join(_ROOT, "data", "edgelab", "reports")
ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")

SPRINT_STATUS = "CLOSED"
MODE = "PROSPECTIVE_MONITORING"
CLOSED_ON = "2026-08-30"

# Preregistered floors, restated here for display only. Each is owned by
# its own experiment registration -- this file is not their source of
# truth and may not change them.
F5_GAME_FLOOR = 100
TEAM_TOTAL_NB_GAME_FLOOR = 100
TEAM_TOTAL_NB_DATE_FLOOR = 10

TRIGGERS = (
    "TRIGGER A -- F5 reaches 100 independent games",
    "TRIGGER B -- TEAM_TOTAL_NB_V1 reaches 100 games / 10 dates",
    "TRIGGER C -- the general frozen forward scorer reaches its checkpoint",
    "TRIGGER D -- a genuine production bug is discovered",
    "TRIGGER E -- a new research question is explicitly authorised",
)


def _count_partition_rows(entity):
    """(partitions, rows, latest) for a committed EdgeLab entity."""
    paths = sorted(glob.glob(os.path.join(_ROOT, "data", "edgelab", entity, "*")))
    rows = 0
    for path in paths:
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                rows += sum(1 for line in fh if line.strip())
        except OSError:
            continue
    return len(paths), rows, (os.path.basename(paths[-1]) if paths else None)


def _settled_family_counts(family):
    """Independent games/dates for a settled+evaluated market family."""
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))
    try:
        import run_production_calibration_audit_experiment as calib
        outcomes, _ = calib.load_settled_outcomes()
        evaluated, _ = calib.load_evaluated_rows()
        rows = [r for r in calib.build_audit_rows(evaluated, outcomes, pick="last")
                if r.get("marketFamily") == family]
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc)}
    return {
        "status": "COUNTED",
        "rows": len(rows),
        "independentGames": len({r["gameId"] for r in rows}),
        "independentDates": len({r["settleDate"] for r in rows}),
    }


def _team_total_nb_forward():
    """Post-registration TEAM_TOTAL_NB_V1 shadow captures."""
    partitions, rows, latest = _count_partition_rows("team_total_nb_shadow_evaluations")
    out = {"partitions": partitions, "capturedRows": rows, "latestPartition": latest,
           "gameFloor": TEAM_TOTAL_NB_GAME_FLOOR, "dateFloor": TEAM_TOTAL_NB_DATE_FLOOR}
    artifact = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0035_team_total_nb_shadow.json")
    if os.path.exists(artifact):
        try:
            payload = json.load(open(artifact, encoding="utf-8"))
            out["registeredAt"] = payload.get("registeredAt")
            out["candidateFingerprint"] = payload.get("candidate", {}).get("fingerprint")
            out["forwardStatus"] = payload.get("forward", {}).get("status")
        except (OSError, json.JSONDecodeError):
            pass
    if rows == 0:
        out["health"] = "HEALTH_PENDING_NATURAL_CYCLE"
    return out


def _forward_scorer():
    path = os.path.join(ANALYTICS_DIR, "latest_frozen_forward_scorecard.json")
    if not os.path.exists(path):
        return {"status": "ABSENT"}
    try:
        payload = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "UNREADABLE", "reason": str(exc)}
    checkpoint = payload.get("checkpoint") or {}
    return {"status": payload.get("status"), "checkpoint": checkpoint.get("checkpoint"),
            "games": checkpoint.get("games"), "rows": checkpoint.get("rows")}


def collect():
    f5 = _settled_family_counts("KXMLBF5")
    nb_partitions, nb_rows, nb_latest = _count_partition_rows("mlb_rsch_0011_shadow_evaluations")
    unc_partitions, unc_rows, unc_latest = _count_partition_rows("uncertainty_capture_snapshots")
    team_total = _team_total_nb_forward()

    f5_games = f5.get("independentGames")
    return {
        "sprintStatus": SPRINT_STATUS,
        "mode": MODE,
        "closedOn": CLOSED_ON,
        "f5": dict(f5, gameFloor=F5_GAME_FLOOR,
                   shortBy=(None if f5_games is None else max(0, F5_GAME_FLOOR - f5_games)),
                   triggerReached=bool(f5_games is not None and f5_games >= F5_GAME_FLOOR)),
        "teamTotalNbShadow": team_total,
        "frozenForwardScorer": _forward_scorer(),
        "nbShadowSidecar": {"partitions": nb_partitions, "rows": nb_rows, "latest": nb_latest},
        "uncertaintySidecar": {"partitions": unc_partitions, "rows": unc_rows, "latest": unc_latest},
        "triggers": list(TRIGGERS),
        "note": ("Counts only. No inference is drawn below a preregistered floor, and reaching "
                 "a floor is a trigger to REVIEW, never an approval to promote."),
    }


def render_markdown(status):
    f5 = status["f5"]
    tt = status["teamTotalNbShadow"]
    fs = status["frozenForwardScorer"]
    lines = [
        "# MLB Research Program — Monitoring Status",
        "",
        f"**Active research sprint:** `{status['sprintStatus']}`  ",
        f"**Mode:** `{status['mode']}`  ",
        f"**Closed on:** {status['closedOn']}",
        "",
        "Counts only. No inference is drawn below a preregistered floor, and reaching a floor",
        "is a trigger to REVIEW — never an approval to promote.",
        "",
        "## Checkpoints",
        "",
        "| Surface | Progress | Floor | Reached |",
        "|---|---|---|---|",
    ]
    if f5.get("status") == "COUNTED":
        lines.append("| KXMLBF5 | %d games / %d dates | %d games | %s |" % (
            f5["independentGames"], f5["independentDates"], F5_GAME_FLOOR,
            "**YES**" if f5["triggerReached"] else "no — short by %d" % f5["shortBy"]))
    else:
        lines.append("| KXMLBF5 | unavailable | %d games | — |" % F5_GAME_FLOOR)
    lines.append("| TEAM_TOTAL_NB_V1 shadow | %d captured rows | %d games / %d dates | %s |" % (
        tt["capturedRows"], TEAM_TOTAL_NB_GAME_FLOOR, TEAM_TOTAL_NB_DATE_FLOOR,
        tt.get("health", "no")))
    lines += [
        "| Frozen forward scorer | %s (%s games) | its own checkpoints | %s |" % (
            fs.get("checkpoint"), fs.get("games"), fs.get("status")),
        "",
        "## Sidecars (accumulate automatically)",
        "",
        "| Sidecar | Partitions | Rows | Latest |",
        "|---|---:|---:|---|",
        "| NB shadow (MLB-RSCH-0011) | %d | %d | %s |" % (
            status["nbShadowSidecar"]["partitions"], status["nbShadowSidecar"]["rows"],
            status["nbShadowSidecar"]["latest"]),
        "| Uncertainty capture (MLB-RSCH-0019) | %d | %d | %s |" % (
            status["uncertaintySidecar"]["partitions"], status["uncertaintySidecar"]["rows"],
            status["uncertaintySidecar"]["latest"]),
        "",
        "## A new active experiment requires one of these",
        "",
    ]
    lines += ["- %s" % t for t in status["triggers"]]
    lines += ["", "Otherwise: **monitor only.**"]
    return "\n".join(lines)


def main():
    status = collect()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(os.path.join(REPORTS_DIR, "monitoring_status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2, sort_keys=True)
        fh.write("\n")
    markdown = render_markdown(status)
    with open(os.path.join(REPORTS_DIR, "monitoring_status.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown + "\n")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
