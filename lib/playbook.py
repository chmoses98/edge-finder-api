"""
lib/playbook.py
===================
The canonical handicapping playbook's VERSION and its accumulated,
evidence-graded LESSONS.

Two files, one purpose:
  * HANDICAPPING_PLAYBOOK.md -- the methodology a fresh AI session reads
    at the start of every slate. Deliberately short and deliberately
    stable.
  * config/playbook_lessons.json -- the things the methodology has
    LEARNED, each with a status, the evidence behind it, and the dates
    that evidence came from. This is the part that grows.

WHY THE SPLIT
-------------
The failure this exists to prevent is the opposite of forgetting: it is
a methodology that gets rewritten every time a wager loses. Keeping
lessons in a separate, status-graded file means a new observation can be
recorded honestly (HYPOTHESIS) without touching the methodology, and can
only become standing guidance (SUPPORTED) by clearing a promotion bar
that a single result cannot clear.

Pure: every function takes already-loaded data or a path. No network.
"""
import json
import os
import re

PLAYBOOK_PATH = "HANDICAPPING_PLAYBOOK.md"
LESSONS_PATH = os.path.join("config", "playbook_lessons.json")
RENDERED_LESSONS_PATH = "PLAYBOOK_LESSONS.md"

VERSION_PATTERN = re.compile(r"PLAYBOOK_VERSION:\s*([0-9]+\.[0-9]+\.[0-9]+)")

# ── Evidence classes ────────────────────────────────────────────────────
#
# The promotion bar cannot be one number, because two very different
# kinds of claim live in this file.
#
#   MECHANICAL      A structural fact about a contract, a market, a
#                   portfolio or arithmetic. "A three-way F5 YES loses a
#                   tie." "Five expressions of one thesis are one
#                   exposure." "A price defines the implied probability."
#                   These do not need a betting sample to be TRUE -- they
#                   need a citation showing the mechanism is real.
#
#   EMPIRICAL_EDGE  A claim that something WINS or LOSES money, or
#                   predicts an outcome: a family, a horizon, a price
#                   shape, a feature. Three wagers pointing the same way
#                   is not evidence of an edge, it is a coin landing
#                   heads three times. Promotion REQUIRES a registered,
#                   leakage-free, out-of-sample experiment -- dates alone
#                   can never do it, however many there are.
#
# Repeated anecdotal observations stay HYPOTHESIS under either class:
# useful to mention in a handicap, useful for motivating research, never
# standing guidance.
CLASS_MECHANICAL = "MECHANICAL"
CLASS_EMPIRICAL_EDGE = "EMPIRICAL_EDGE"
VALID_EVIDENCE_CLASSES = (CLASS_MECHANICAL, CLASS_EMPIRICAL_EDGE)

# A registered experiment id. Only these can promote an EMPIRICAL_EDGE
# claim to SUPPORTED.
EXPERIMENT_ID_PATTERN = r"MLB-(RSCH|ALPHA)-\d{4}"

STATUS_HYPOTHESIS = "HYPOTHESIS"
STATUS_SUPPORTED = "SUPPORTED"
STATUS_REJECTED = "REJECTED"
STATUS_RETIRED = "RETIRED"
VALID_STATUSES = (STATUS_HYPOTHESIS, STATUS_SUPPORTED, STATUS_REJECTED, STATUS_RETIRED)

# The promotion bar, as a checkable number rather than a vibe. A lesson
# claiming SUPPORTED must cite at least this many DISTINCT dates (or a
# registered experiment, which counts as independent out-of-sample
# evidence in its own right -- see lesson_meets_promotion_bar).
MIN_DISTINCT_DATES_FOR_SUPPORTED = 3

REQUIRED_LESSON_FIELDS = ("id", "status", "evidenceClass", "title", "guidance", "tags", "evidence")

# A MECHANICAL lesson needs enough to show the mechanism is real: either
# repeated dated instances, or an explicit mechanism citation (a contract
# rule, a module, a piece of arithmetic).
MIN_DISTINCT_DATES_FOR_MECHANICAL = 2


def read_playbook_version(text_or_path=PLAYBOOK_PATH):
    """
    Pure-ish. The playbook's declared semantic version, so a slate output
    and a later postmortem can agree on which methodology produced a
    card. Returns None when no version marker is present -- never a
    guessed default.
    """
    text = text_or_path
    if os.path.exists(str(text_or_path)):
        with open(text_or_path, encoding="utf-8") as f:
            text = f.read()
    match = VERSION_PATTERN.search(text or "")
    return match.group(1) if match else None


def load_lessons(path=LESSONS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_lessons(payload):
    """
    Pure. Returns a list of problem strings; empty means valid.
    Structural only -- it never judges whether a lesson is TRUE, only
    whether it is recorded in a way that can be audited later.
    """
    problems = []
    lessons = payload.get("lessons")
    if not isinstance(lessons, list) or not lessons:
        return ["no lessons array"]

    seen = set()
    for index, lesson in enumerate(lessons):
        label = lesson.get("id") or f"lesson[{index}]"
        for field in REQUIRED_LESSON_FIELDS:
            if not lesson.get(field):
                problems.append(f"{label}: missing required field {field!r}")
        if lesson.get("id") in seen:
            problems.append(f"{label}: duplicate lesson id")
        seen.add(lesson.get("id"))
        if lesson.get("status") not in VALID_STATUSES:
            problems.append(f"{label}: status {lesson.get('status')!r} is not one of {VALID_STATUSES}")
        if lesson.get("evidenceClass") not in VALID_EVIDENCE_CLASSES:
            problems.append(
                f"{label}: evidenceClass {lesson.get('evidenceClass')!r} is not one of "
                f"{VALID_EVIDENCE_CLASSES} -- a claim about MONEY and a claim about MECHANISM "
                f"cannot share a promotion bar"
            )
        evidence = lesson.get("evidence") or {}
        if not evidence.get("distinctDates"):
            problems.append(f"{label}: evidence has no distinctDates -- an unsourced lesson is an opinion")
        if not evidence.get("examples"):
            problems.append(f"{label}: evidence has no examples")
        if lesson.get("status") == STATUS_SUPPORTED and not lesson_meets_promotion_bar(lesson):
            if lesson.get("evidenceClass") == CLASS_EMPIRICAL_EDGE:
                problems.append(
                    f"{label}: an EMPIRICAL_EDGE claim may become SUPPORTED only with a registered, "
                    f"leakage-free, out-of-sample experiment (a cited MLB-RSCH-####/MLB-ALPHA-#### id "
                    f"AND an evidence.outOfSampleResult). It cites "
                    f"{len(set(evidence.get('distinctDates') or []))} date(s), which can never promote "
                    f"a claim about money."
                )
            else:
                problems.append(
                    f"{label}: claims SUPPORTED but shows no mechanism -- needs "
                    f"{MIN_DISTINCT_DATES_FOR_MECHANICAL}+ distinct dates, an evidence.mechanism "
                    f"citation, or a registered experiment"
                )
        if lesson.get("status") == STATUS_HYPOTHESIS and not lesson.get("promotionBlockedBy"):
            problems.append(f"{label}: a HYPOTHESIS must say what is blocking promotion")
    return problems


def cites_registered_experiment(lesson):
    """Pure. True iff this lesson cites a registered experiment id
    anywhere in its evidence."""
    return bool(re.search(EXPERIMENT_ID_PATTERN, json.dumps(lesson.get("evidence") or {})))


def lesson_meets_promotion_bar(lesson):
    """
    Pure. True iff this lesson's own cited evidence clears the SUPPORTED
    bar FOR ITS EVIDENCE CLASS.

    EMPIRICAL_EDGE is the strict one, and deliberately so: a claim that
    something wins money is promoted ONLY by a registered, leakage-free,
    out-of-sample experiment. No number of same-direction dates can
    promote it -- that is exactly how a three-wager streak becomes
    institutionalised as a betting rule.

    MECHANICAL is promoted by showing the mechanism is real: repeated
    dated instances, a cited mechanism (a contract rule, a module, an
    arithmetic identity), or a registered experiment.
    """
    evidence = lesson.get("evidence") or {}
    dates = set(evidence.get("distinctDates") or [])
    evidence_class = lesson.get("evidenceClass")

    if evidence_class == CLASS_EMPIRICAL_EDGE:
        return cites_registered_experiment(lesson) and bool(evidence.get("outOfSampleResult"))

    if evidence_class == CLASS_MECHANICAL:
        return (
            len(dates) >= MIN_DISTINCT_DATES_FOR_MECHANICAL
            or bool(evidence.get("mechanism"))
            or cites_registered_experiment(lesson)
        )

    # Unknown class: fall back to the strictest reading.
    return False


def lessons_by_status(payload):
    grouped = {status: [] for status in VALID_STATUSES}
    for lesson in payload.get("lessons", []):
        grouped.setdefault(lesson.get("status"), []).append(lesson)
    return grouped


def render_markdown(payload, playbook_version=None):
    """Pure. The human-readable companion to the JSON. Ordered so the
    things that are actually standing guidance come first."""
    version = playbook_version or payload.get("playbookVersion")
    grouped = lessons_by_status(payload)
    lines = [
        "# PLAYBOOK_LESSONS.md",
        "",
        f"`PLAYBOOK_VERSION: {version}` · `LAST_UPDATED: {payload.get('lastUpdated')}`",
        "",
        "> Generated from `config/playbook_lessons.json` by "
        "`python3 scripts/playbook_lessons.py --render`. Edit the JSON, not this file.",
        "",
        "Read alongside `HANDICAPPING_PLAYBOOK.md`. A lesson is standing guidance only at "
        "**SUPPORTED**. A **HYPOTHESIS** is worth mentioning in a handicap and is never a rule. "
        "Nothing here is promoted, demoted or retired by a single wager's result.",
        "",
        "## Evidence classes",
        "",
        "- **MECHANICAL** — a structural fact about a contract, a market, a portfolio or "
        "arithmetic. True without a betting sample; promoted by showing the mechanism is real.",
        "- **EMPIRICAL_EDGE** — a claim that something wins, loses or predicts. Promoted ONLY by a "
        "registered, leakage-free, out-of-sample experiment. No number of same-direction dates can "
        "promote a claim about money.",
        "",
        "## Promotion policy",
        "",
    ]
    policy = payload.get("promotionPolicy") or {}
    for key in ("toSupported", "toRejected", "review"):
        if policy.get(key):
            lines.append(f"- **{key}** — {policy[key]}")
    lines.append("")

    for status in (STATUS_SUPPORTED, STATUS_HYPOTHESIS, STATUS_REJECTED, STATUS_RETIRED):
        entries = grouped.get(status) or []
        if not entries:
            continue
        lines += [f"## {status} ({len(entries)})", ""]
        for lesson in entries:
            evidence = lesson.get("evidence") or {}
            dates = sorted(set(evidence.get("distinctDates") or []))
            lines += [
                f"### {lesson['title']}",
                "",
                (f"`id: {lesson['id']}` · **{lesson.get('evidenceClass')}** · "
                 f"tags: {', '.join(lesson.get('tags') or []) or '—'} · "
                 f"evidence dates: {len(dates)} ({dates[0]} → {dates[-1]})") if dates else
                f"`id: {lesson['id']}` · **{lesson.get('evidenceClass')}**",
                "",
                lesson["guidance"],
                "",
                "<details><summary>Evidence</summary>",
                "",
            ]
            for example in evidence.get("examples") or []:
                lines.append(f"- {example}")
            if lesson.get("promotionBlockedBy"):
                lines += ["", f"**Promotion blocked by:** {lesson['promotionBlockedBy']}"]
            lines += ["", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def audit_against_postmortems(payload, postmortem_records):
    """
    Pure. Recount each lesson's tag evidence directly from the
    postmortem corpus, so a lesson cannot keep claiming support that the
    record no longer contains.

    `postmortem_records` is an iterable of (gameDate, tags) pairs, one
    per recorded finding. Returns one row per lesson with the dates its
    tags actually appear on, and whether that still clears its own bar.
    """
    by_tag = {}
    for game_date, tags in postmortem_records:
        for tag in tags or []:
            by_tag.setdefault(tag, set()).add(game_date)

    rows = []
    for lesson in payload.get("lessons", []):
        observed = set()
        for tag in lesson.get("tags") or []:
            observed |= by_tag.get(tag, set())
        claimed = set((lesson.get("evidence") or {}).get("distinctDates") or [])
        rows.append({
            "id": lesson["id"],
            "status": lesson["status"],
            "claimedDates": sorted(claimed),
            "observedTagDates": sorted(observed),
            "claimedDatesNotInCorpus": sorted(claimed - observed),
            "meetsOwnPromotionBar": lesson_meets_promotion_bar(lesson),
        })
    return rows
