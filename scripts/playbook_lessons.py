#!/usr/bin/env python3
"""
scripts/playbook_lessons.py
===============================
Maintain the canonical handicapping playbook's LESSONS
(config/playbook_lessons.json) and its rendered companion
(PLAYBOOK_LESSONS.md).

This is the mechanism that lets postmortem findings accumulate WITHOUT
letting any single result rewrite the methodology:

    --validate   structural check: every lesson has a status, tags, cited
                 evidence dates and examples; a SUPPORTED lesson actually
                 clears the promotion bar; a HYPOTHESIS says what is
                 blocking it. Exit 1 if anything fails. Run in CI.
    --audit      recount every lesson's evidence directly from
                 data/edgelab/postmortems/ and report any lesson whose
                 claimed dates are no longer in the corpus or that no
                 longer clears its own bar. Read-only; never edits.
    --render     regenerate PLAYBOOK_LESSONS.md from the JSON.
    --list       one line per lesson, grouped by status.
    --set-status promote/demote ONE lesson by id, with a mandatory
                 --reason. Deliberately the only write path that changes
                 a status, and deliberately manual: promotion is a
                 judgement about accumulated evidence, not something a
                 nightly job should do on its own.

Usage:
    python3 scripts/playbook_lessons.py --validate
    python3 scripts/playbook_lessons.py --audit
    python3 scripts/playbook_lessons.py --render
    python3 scripts/playbook_lessons.py --set-status f7-as-a-default-horizon SUPPORTED \\
        --reason "three further same-direction dates: 2026-09-20/22/25"
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.playbook import (
    LESSONS_PATH,
    PLAYBOOK_PATH,
    RENDERED_LESSONS_PATH,
    STATUS_HYPOTHESIS,
    VALID_STATUSES,
    audit_against_postmortems,
    load_lessons,
    read_playbook_version,
    render_markdown,
    validate_lessons,
)

POSTMORTEM_GLOB = os.path.join("data", "edgelab", "postmortems", "*", "postmortem.json")


def read_postmortem_findings(pattern=POSTMORTEM_GLOB):
    """(gameDate, tags) for every recorded finding, win or miss."""
    records = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        date = payload.get("gameDate")
        for key in ("analyticalMisses", "analyticalWins"):
            for finding in payload.get(key) or []:
                records.append((date, finding.get("tags") or []))
    return records


def cmd_validate(payload):
    problems = validate_lessons(payload)
    version = read_playbook_version(PLAYBOOK_PATH)
    if version is None:
        problems.append(f"{PLAYBOOK_PATH} carries no PLAYBOOK_VERSION marker")
    elif payload.get("playbookVersion") != version:
        problems.append(
            f"playbookVersion {payload.get('playbookVersion')!r} in {LESSONS_PATH} does not match "
            f"PLAYBOOK_VERSION {version!r} in {PLAYBOOK_PATH}"
        )
    for problem in problems:
        print(f"  FAIL {problem}", file=sys.stderr)
    if problems:
        print(f"[playbook_lessons] {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(f"[playbook_lessons] OK: {len(payload['lessons'])} lessons, playbook version {version}")
    return 0


def cmd_audit(payload):
    rows = audit_against_postmortems(payload, read_postmortem_findings())
    drifted = 0
    print(f"{'lesson':44s} {'status':11s} {'claimed':8s} {'in corpus':10s} bar")
    for row in rows:
        missing = row["claimedDatesNotInCorpus"]
        bar = "OK" if row["meetsOwnPromotionBar"] else "BELOW"
        print(f"{row['id']:44s} {row['status']:11s} {len(row['claimedDates']):<8d} "
              f"{len(row['observedTagDates']):<10d} {bar}")
        if missing:
            drifted += 1
            print(f"    dates cited but not found by tag in the corpus: {missing}")
    print(f"\n[playbook_lessons] {len(rows)} lessons audited, {drifted} with citation drift")
    print("[playbook_lessons] audit is READ-ONLY -- it never promotes, demotes or edits a lesson")
    return 0


def cmd_render(payload):
    version = read_playbook_version(PLAYBOOK_PATH) or payload.get("playbookVersion")
    markdown = render_markdown(payload, playbook_version=version)
    with open(RENDERED_LESSONS_PATH, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"[playbook_lessons] wrote {RENDERED_LESSONS_PATH} ({len(payload['lessons'])} lessons)")
    return 0


def cmd_list(payload):
    for status in VALID_STATUSES:
        entries = [l for l in payload["lessons"] if l["status"] == status]
        if not entries:
            continue
        print(f"\n{status} ({len(entries)})")
        for lesson in entries:
            dates = sorted(set((lesson.get("evidence") or {}).get("distinctDates") or []))
            print(f"  {lesson['id']:44s} {len(dates)} dates  {lesson['title']}")
    return 0


def cmd_set_status(payload, lesson_id, status, reason, path):
    if status not in VALID_STATUSES:
        print(f"ERROR: status must be one of {VALID_STATUSES}", file=sys.stderr)
        return 1
    matches = [l for l in payload["lessons"] if l["id"] == lesson_id]
    if not matches:
        print(f"ERROR: no lesson with id {lesson_id!r}", file=sys.stderr)
        return 1
    lesson = matches[0]
    previous = lesson["status"]
    if previous == status:
        print(f"[playbook_lessons] {lesson_id} is already {status} -- nothing changed")
        return 0

    lesson["status"] = status
    lesson.setdefault("statusHistory", []).append({
        "from": previous, "to": status, "reason": reason,
        "changedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    if status == STATUS_HYPOTHESIS:
        lesson.setdefault("promotionBlockedBy", reason)
    payload["lastUpdated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    problems = validate_lessons(payload)
    if problems:
        print("ERROR: refusing to write -- the change would leave the file invalid:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)
        f.write("\n")
    print(f"[playbook_lessons] {lesson_id}: {previous} -> {status} ({reason})")
    print(f"[playbook_lessons] re-run with --render to refresh {RENDERED_LESSONS_PATH}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=LESSONS_PATH)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--set-status", nargs=2, metavar=("LESSON_ID", "STATUS"))
    parser.add_argument("--reason", default=None, help="Required with --set-status")
    args = parser.parse_args()

    payload = load_lessons(args.path)

    if args.set_status:
        if not args.reason:
            parser.error("--set-status requires --reason: a status change without a recorded reason "
                         "is exactly the unexamined rewrite this mechanism exists to prevent")
        return cmd_set_status(payload, args.set_status[0], args.set_status[1], args.reason, args.path)
    if args.validate:
        return cmd_validate(payload)
    if args.audit:
        return cmd_audit(payload)
    if args.render:
        return cmd_render(payload)
    if args.list:
        return cmd_list(payload)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
