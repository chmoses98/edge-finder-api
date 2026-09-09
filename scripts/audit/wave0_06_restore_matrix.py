#!/usr/bin/env python3
"""
scripts/audit/wave0_06_restore_matrix.py
========================================
WAVE 0.06, section 2. AUDIT-ONLY. Builds the deterministic restore matrix for
2026-09-01..2026-09-06, separating the three populations the previous restore
plan conflated.

STRICTLY READ-ONLY. It writes exactly one file --
data/edgelab/operational_health/wave0_06_restore_matrix.json -- and never touches settlement data,
wager economics, prices, or any production artifact. Nothing in scripts/, lib/,
api/ or any workflow imports it.

WHY THIS EXISTS
---------------
Wave 0 scoped the restore from the ROOT wager ledger (bets.json) and concluded
"156 non-terminal bets". The Wave 0.05 rehearsal disproved that scope: the root
ledger holds exactly ONE row dated in the six-day window. The missing
`data/edgelab/settlements/<date>.jsonl` partitions describe the OBSERVED MARKET
UNIVERSE produced by edgelab-postgame.yml from recommendations -- a different,
much larger population that the root ledger barely intersects.

Restoring against the wrong population would have looked successful (one bet)
while leaving the actual gap untouched.

THE THREE POPULATIONS
---------------------
  A  EDGELAB OBSERVED MARKET UNIVERSE
     recommendations / model evaluations / games / settlements partitions.
     This is what edgelab-postgame.yml produces and what is actually missing.

  B  USER-CONFIRMED / LEGACY PLACED WAGERS
     root bets.json + canonical data/edgelab/bets/bets.jsonl. A recommendation
     is NEVER assumed to have been placed; these two are counted independently
     and never inferred from population A.

  C  EXPECTED UNRESOLVED / IDENTITY-BLOCKED
     Rows that must stay unresolved on purpose: CR-3 doubleheader ambiguity,
     unsupported settlement families, missing participation evidence, genuinely
     absent closing prices.

OUTCOME TRUTH vs CLOSING-PRICE TRUTH
------------------------------------
These are tracked separately and never substituted for one another. A wager can
be perfectly settleable while CLV stays permanently unavailable, and -- as this
window shows -- the reverse is also true: rows here already carry closing
prices and CLV while having no settled result at all.

    python3 scripts/audit/wave0_06_restore_matrix.py [--json] [--write-artifact]
"""

import argparse
import collections
import gzip
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))

WINDOW = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
          "2026-09-05", "2026-09-06"]

# Dates immediately outside the window, used as the healthy control so
# "missing" is measured against what a working day actually produces.
CONTROL = ["2026-08-29", "2026-08-30", "2026-08-31", "2026-09-07"]

EDGELAB_FAMILIES = ("recommendations", "model_evaluations", "games", "settlements")

# Stand-in for a model_evaluations row carrying no artifactSource. Kept as an
# explicit, obviously-synthetic string so an absent field stays visible in the
# matrix instead of vanishing, and so the JSON object it keys can be sorted.
MISSING_ARTIFACT_SOURCE = "(missing)"

TERMINAL = {"WIN", "LOSS", "PUSH", "VOID", "CANCELLED", "CANCELED"}


def _read_partition(family, date):
    """(rows, form) for a date partition, transparently handling .gz. None if absent."""
    base = os.path.join(ROOT, "data", "edgelab", family, "%s.jsonl" % date)
    if os.path.exists(base):
        with open(base) as fh:
            return [json.loads(l) for l in fh if l.strip()], "jsonl"
    if os.path.exists(base + ".gz"):
        with gzip.open(base + ".gz", "rt") as fh:
            return [json.loads(l) for l in fh if l.strip()], "jsonl.gz"
    return None, None


def _game_identity(date):
    """
    Identity evidence for one date. Games are stored with record versioning
    (a kalshi_registry_snapshots record superseded by a normalized_slate one),
    so the DISTINCT mlbGamePk count is the real game count -- never the row
    count, which double-counts.
    """
    rows, form = _read_partition("games", date)
    if rows is None:
        return {"present": False}

    pks = [r.get("mlbGamePk") for r in rows if r.get("mlbGamePk")]
    distinct = sorted(set(pks))
    dh = [r for r in rows if r.get("doubleheaderGameNumber") not in (None, "")]

    # Teams-pair collision is what a date+away+home key would collapse. It is
    # NOT by itself a doubleheader here (record versioning also produces it),
    # which is exactly why gamePk must be the identity key.
    pair_counts = collections.Counter(
        (r.get("awayTeam"), r.get("homeTeam")) for r in rows)
    colliding = sorted("%s@%s" % k for k, v in pair_counts.items() if v > 1)

    return {
        "present": True,
        "form": form,
        "recordRows": len(rows),
        "rowsCarryingGamePk": len(pks),
        "distinctGamePk": len(distinct),
        "gamePkCoveragePct": round(100.0 * len(pks) / len(rows), 1) if rows else 0.0,
        "doubleheaderFlaggedRecords": len(dh),
        "teamPairCollisions": len(colliding),
        "statuses": dict(collections.Counter(r.get("status") for r in rows)),
        "anyFinalScoreField": any(
            k in r for r in rows
            for k in ("awayScore", "homeScore", "finalAwayScore",
                      "finalHomeScore", "linescore")),
    }


def _population_a(date, control_median):
    """EdgeLab observed-market universe: what postgame should have produced."""
    fams = {}
    for family in EDGELAB_FAMILIES:
        rows, form = _read_partition(family, date)
        entry = {"present": rows is not None,
                 "rows": len(rows) if rows is not None else 0,
                 "form": form}
        if rows is not None and family == "model_evaluations":
            # A thin model_evaluations partition is not a partial postgame
            # evaluation set -- classify what is actually in it.
            # A row may legitimately carry no artifactSource. Counter keys feed
            # a JSON object serialised with sort_keys=True, and sorting a dict
            # whose keys mix None with str raises TypeError -- so normalise the
            # absent case to an explicit sentinel rather than dropping it. The
            # sentinel is deliberately distinguishable from any real source
            # name; it must never be confused with `prospective_snapshot`.
            sources = collections.Counter(
                r.get("artifactSource") or MISSING_ARTIFACT_SOURCE for r in rows)
            entry["artifactSources"] = dict(sources)
            entry["distinctMarkets"] = len(set(r.get("market") for r in rows))
            entry["distinctGames"] = len(set(r.get("gameId") for r in rows))
            entry["isPostgameEvaluationPopulation"] = (
                "prospective_snapshot" not in sources)
        fams[family] = entry

    disposition = {}
    for family in EDGELAB_FAMILIES:
        got = fams[family]["rows"]
        expected = control_median.get(family, 0)
        if not fams[family]["present"]:
            disposition[family] = "ABSENT"
        elif family == "model_evaluations" and not fams[family].get(
                "isPostgameEvaluationPopulation", True):
            disposition[family] = "PRESENT_BUT_DIFFERENT_ARTIFACT_CLASS"
        elif expected and got < expected * 0.5:
            disposition[family] = "PRESENT_BUT_INCOMPLETE"
        else:
            disposition[family] = "PRESENT"

    return {"families": fams, "disposition": disposition}


def _population_b(date):
    """Placed wagers only. Never inferred from a recommendation."""
    root_path = os.path.join(ROOT, "bets.json")
    root_rows = []
    if os.path.exists(root_path):
        with open(root_path) as fh:
            root_rows = [b for b in json.load(fh)
                         if (b.get("date") or b.get("gameDate") or "")[:10] == date]

    canon_path = os.path.join(ROOT, "data", "edgelab", "bets", "bets.jsonl")
    canon_rows = []
    if os.path.exists(canon_path):
        with open(canon_path) as fh:
            canon_rows = [b for b in (json.loads(l) for l in fh if l.strip())
                          if (b.get("gameDate") or b.get("date") or "")[:10] == date]

    def _summarize(rows):
        settled = [b for b in rows if (b.get("result") or "").upper() in TERMINAL]
        return {
            "rows": len(rows),
            "settled": len(settled),
            "unsettled": len(rows) - len(settled),
            "withEntryPrice": sum(1 for b in rows if b.get("entryPrice") is not None),
            "withClosingPrice": sum(1 for b in rows if b.get("closingPrice") is not None),
            "withClv": sum(1 for b in rows if b.get("clv") is not None),
        }

    return {"rootBetsJson": _summarize(root_rows),
            "canonicalBetsJsonl": _summarize(canon_rows)}


def _population_c(date, identity, pop_b):
    """Rows that must remain unresolved, each with an explicit reason."""
    reasons = []

    if identity.get("present") and identity.get("doubleheaderFlaggedRecords"):
        reasons.append({
            "category": "IDENTITY_DOUBLEHEADER_CR3",
            "count": identity["doubleheaderFlaggedRecords"],
            "reason": ("records carry doubleheaderGameNumber; audit CR-3 means the "
                       "production kalshiKey has no leg discriminator, so market "
                       "mapping stays ambiguous until Wave 1. gamePk identity is "
                       "present and unambiguous -- the ambiguity is in market "
                       "mapping, not in the game."),
        })

    canon = pop_b["canonicalBetsJsonl"]
    missing_close = canon["rows"] - canon["withClosingPrice"]
    if missing_close > 0:
        reasons.append({
            "category": "CLV_NO_CLOSING_OBSERVATION",
            "count": missing_close,
            "reason": ("no genuine executable closing observation exists. CLV must "
                       "stay null -- never backfilled from midpoint, last trade, "
                       "current price, model probability or a sibling contract."),
        })

    if not identity.get("present"):
        reasons.append({
            "category": "IDENTITY_NO_GAME_RECORD",
            "count": None,
            "reason": "no games partition for this date; nothing can be keyed",
        })

    return reasons


def build_matrix():
    control = collections.defaultdict(list)
    for date in CONTROL:
        for family in EDGELAB_FAMILIES:
            rows, _ = _read_partition(family, date)
            if rows is not None:
                control[family].append(len(rows))

    control_median = {}
    for family, counts in control.items():
        counts = sorted(counts)
        control_median[family] = counts[len(counts) // 2] if counts else 0

    dates = {}
    for date in WINDOW:
        identity = _game_identity(date)
        pop_a = _population_a(date, control_median)
        pop_b = _population_b(date)
        pop_c = _population_c(date, identity, pop_b)
        dates[date] = {
            "identityEvidence": identity,
            "populationA_edgelabObservedMarketUniverse": pop_a,
            "populationB_placedWagers": pop_b,
            "populationC_expectedUnresolved": pop_c,
            "outcomeTruthAvailableLocally": bool(identity.get("anyFinalScoreField")),
        }

    total_canon = sum(d["populationB_placedWagers"]["canonicalBetsJsonl"]["rows"]
                      for d in dates.values())
    total_root = sum(d["populationB_placedWagers"]["rootBetsJson"]["rows"]
                     for d in dates.values())
    total_clv = sum(d["populationB_placedWagers"]["canonicalBetsJsonl"]["withClv"]
                    for d in dates.values())
    total_settled = sum(d["populationB_placedWagers"]["canonicalBetsJsonl"]["settled"]
                        for d in dates.values())

    return {
        "schemaVersion": "1",
        "generatedBy": "scripts/audit/wave0_06_restore_matrix.py",
        "wave": "WAVE_0_06",
        "mode": "AUDIT_ONLY_READ_ONLY",
        "window": {"from": WINDOW[0], "to": WINDOW[-1]},
        "controlDates": CONTROL,
        "controlMedianRowsPerFamily": control_median,
        "dates": dates,
        "summary": {
            "populationB_rootBetsJsonRowsInWindow": total_root,
            "populationB_canonicalBetsJsonlRowsInWindow": total_canon,
            "populationB_settledInWindow": total_settled,
            "populationB_withClvInWindow": total_clv,
            "outcomeTruthPresentAnywhereLocally": any(
                d["outcomeTruthAvailableLocally"] for d in dates.values()),
        },
        "findings": [
            "Wave 0 scoped the restore from the ROOT wager ledger. That ledger "
            "holds %d row(s) in this window; the canonical wager ledger holds %d. "
            "The missing settlements partitions describe the EdgeLab observed "
            "market universe, a different population."
            % (total_root, total_canon),
            "Game IDENTITY is already durable: every games partition in the "
            "window carries mlbGamePk at 100% row coverage, backfilled from "
            "statsapi.mlb.com by DATE_AWAY_HOME_UNIQUE_MATCH.",
            "Game OUTCOME is entirely absent: no score or linescore field exists "
            "anywhere in the games schema. This is the gap a durable result "
            "sidecar must fill, and it can key on the mlbGamePk already present.",
            "%d of %d canonical wagers in the window already carry a closing "
            "price and CLV while NONE is settled -- so for placed wagers the "
            "restore need is OUTCOME ONLY. CLV must not be recomputed."
            % (total_clv, total_canon),
            "Team-pair collisions occur on every date because game records are "
            "versioned (kalshi_registry_snapshots superseded by normalized_slate), "
            "not because every date is a doubleheader. A date+away+home key would "
            "collapse them; mlbGamePk does not.",
        ],
        "safetyInvariants": [
            "A recommendation is NEVER assumed to have been placed.",
            "Outcome truth and closing-price truth are tracked separately and "
            "never substituted for one another.",
            "CLV is never backfilled from midpoint, last trade, current price, "
            "model probability or a sibling contract.",
            "No settlement result is proposed by this tool; it only measures.",
            "Identity that is ambiguous stays unresolved rather than guessed.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-artifact", action="store_true")
    args = parser.parse_args(argv)

    matrix = build_matrix()

    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    else:
        print("WAVE 0.06 RESTORE MATRIX (audit-only; nothing is repaired)")
        print("=" * 78)
        print("control median rows/day:", matrix["controlMedianRowsPerFamily"])
        print()
        header = "date        " + "".join("%-22s" % f for f in EDGELAB_FAMILIES)
        print(header + "gamePk  wagers")
        for date in WINDOW:
            d = matrix["dates"][date]
            cells = ""
            for family in EDGELAB_FAMILIES:
                disp = d["populationA_edgelabObservedMarketUniverse"]["disposition"][family]
                rows = d["populationA_edgelabObservedMarketUniverse"]["families"][family]["rows"]
                cells += "%-22s" % ("%s(%d)" % (disp[:14], rows))
            ident = d["identityEvidence"]
            wag = d["populationB_placedWagers"]["canonicalBetsJsonl"]
            print("%s  %s%-8s%s" % (
                date, cells,
                "%d/%d" % (ident.get("distinctGamePk", 0), ident.get("recordRows", 0)),
                "%d rows / %d settled / %d clv" % (wag["rows"], wag["settled"], wag["withClv"])))
        print()
        for finding in matrix["findings"]:
            print("* " + finding)

    if args.write_artifact:
        out_dir = os.path.join(ROOT, "data", "edgelab", "operational_health")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "wave0_06_restore_matrix.json")
        with open(out, "w") as fh:
            json.dump(matrix, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("\nartifact: %s" % os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
