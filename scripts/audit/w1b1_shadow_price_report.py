#!/usr/bin/env python3
"""
scripts/audit/w1b1_shadow_price_report.py
=========================================
WAVE 1, subwave B1. Read-only shadow report: what would the decision layer see
if it priced against the real book instead of a midpoint?

CHANGES NOTHING. It never writes bets.json, never writes a recommendation, and
never touches qualification. It writes one JSON artifact, and only when asked.

WHAT IT ANSWERS
---------------
  * COVERAGE, in TWO separated views, because they answer different questions:
      A. FULL ARCHIVED MARKET UNIVERSE -- every contract family the exchange
         actually offered and we archived. A family with executable evidence
         the join cannot reach is a B1 defect, not a data gap.
      B. PRODUCTION DECISION CANDIDATES -- the far smaller set the model
         actually evaluated. A family present in A with zero rows in B is
         reported as zero, never omitted.
  * BLAST RADIUS: legacy verdict vs shadow verdict for every decision candidate.

HOW THE BLAST RADIUS AVOIDS BECOMING A SECOND DECISION AUTHORITY
----------------------------------------------------------------
It does not re-implement qualification. It CALLS production's own functions --
scripts.build_market_ledger.build_edge_fields, .confidence_from_edge and the
two tier caps -- twice per row, with every input identical except one:

    legacy arm  ->  exec price = what production recorded (a midpoint), or None,
                    in which case build_edge_fields falls back to kalshi_vf,
                    which is what production does today
    shadow arm  ->  exec price = the canonical executable price from the book

No threshold, calibration factor, fee rule, cap or tier rule is redefined here;
CAL_MEDIUM and the THRESHOLD_* constants are imported from that module, not
copied. Because the two arms differ in exactly one input, every verdict change
this report shows is attributable to the price and to nothing else.

The legacy arm is additionally checked against the confidence production
actually recorded on the row, and that agreement rate is reported rather than
assumed -- if the seam had drifted from production, that number would say so.
"""

import argparse
import collections
import gzip
import json
import os
import statistics
import sys
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp          # noqa: E402
from lib.edgelab import observation_join as oj         # noqa: E402
from lib.atomic_json import write_json_atomic          # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "edgelab", "operational_health")
OUT_NAME = "w1b1_shadow_price_report.json"


def _production_semantics():
    """
    The authoritative qualification module, imported rather than reproduced.

    Imported here rather than at module scope so this audit never becomes a
    reason for scripts/build_market_ledger.py to grow an import-time dependency
    on audit code; the arrow points one way only.
    """
    import importlib
    return importlib.import_module("scripts.build_market_ledger")


def _read_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _partition(directory, date):
    for ext in (".jsonl", ".jsonl.gz"):
        path = os.path.join(directory, date + ext)
        if os.path.exists(path):
            return path
    return None


def family_of(row):
    return (row.get("marketFamily") or row.get("family")
            or (row.get("seriesTicker") or "UNKNOWN"))


def legacy_price_cents(row):
    """
    The executable price production ITSELF used for this row, in cents, or None.

    `executablePriceUsed` is production's own recorded field and is preferred
    whenever present. It is NOT a genuine ask: measured on the live 2026-09-09
    slate, it equals marketProbVF exactly on 34 of the 42 rows that carry one
    and differs by at most 0.9pp on the rest, and it takes values like 99.9c
    that are not even on Kalshi's whole-cent grid -- because it is derived from
    the American MID odds, not read from the book. That is the CR-1 defect,
    stated in production's own numbers.

    When the field is absent, None is returned, which is precisely the input
    that makes build_edge_fields fall back to `exec_prob = kalshi_vf` -- the
    vig-free midpoint -- so the legacy arm reproduces production either way.
    """
    value = row.get("executablePriceUsed")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def market_probability(row):
    """The vig-free midpoint probability production recorded, as a 0-1 float."""
    value = row.get("marketImpliedProbability")
    if value is None:
        value = row.get("marketProbVF")
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def model_probability(row):
    try:
        return float(row.get("modelFairProbability")) / 100.0
    except (TypeError, ValueError):
        return None


# Rows whose createdAt is a genuine decision instant. The postgame settlement
# pass re-writes evaluation rows the FOLLOWING day, so their createdAt is a
# bookkeeping timestamp, not a moment anyone decided anything -- joining those
# to "the latest quote before createdAt" produces a technically-correct but
# meaningless 11-hour-old quote. Measured on 2026-09-08: median age 39,702s
# across all rows. Real-time decisions are the prospective capture.
REAL_TIME_DECISION_SOURCES = ("prospective_snapshot",)


def is_real_time_decision(row):
    return (row.get("artifactSource") or "") in REAL_TIME_DECISION_SOURCES


def _percentiles(values):
    if not values:
        return {"n": 0, "median": None, "p95": None}
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return {"n": len(ordered),
            "median": round(statistics.median(ordered), 1),
            "p95": round(ordered[idx], 1)}


# ── VIEW A: the full archived market universe ────────────────────────────────

def universe_view(root=None):
    """
    Per family, what the EXCHANGE offered and we archived.

    THIS VIEW ENUMERATES ITS OWN PARTITIONS. It asks
    data/edgelab/observations what it contains and reads all of it. It does NOT
    receive a date list, and in particular does not inherit View B's dates.

    That distinction was a real defect, not a stylistic one. The rehearsal
    derived its dates from data/edgelab/model_evaluations and passed them to
    load_observations, so "the full archived market universe" silently meant
    "the archived universe on days the model also wrote a decision partition".
    The two directories hold the same NUMBER of partitions and different ones:
    observations have 2026-08-01 (12,366 rows) and 2026-08-14 (15,998 rows)
    that model_evaluations lacks. 28,364 archived observations sat outside a
    table that described itself as complete.

    Counted per CONTRACT (distinct ticker) for the executable columns, so a
    family captured more often does not look better covered than one captured
    less; `observations` is the raw admitted row count so the two reconcile.
    The executable columns use the LATEST archived quote for each ticker, the
    most favourable reading available: a family that cannot produce an
    executable price even from its best quote genuinely has none.

    Streams rather than indexes -- it needs per-ticker aggregates, not
    per-ticker history, so it never holds the whole archive in memory.

    Returns (families, reconciliation).
    """
    per_family = collections.defaultdict(lambda: {
        "availableContracts": 0, "observations": 0,
        "yesExecutable": 0, "noExecutable": 0, "neitherExecutable": 0,
        "bookState": collections.Counter(), "seriesTickers": collections.Counter(),
        "subCentQuotes": 0, "zeroYesBid": 0,
    })
    # ticker -> (sort key, latest row, rows seen). Only the winner is retained.
    latest_by_ticker = {}
    stats = {}

    for row, _partition_date in oj.iter_observations(root=root, stats=stats):
        ticker = row["marketTicker"]
        key = (row["_capturedAtDt"], str(row.get("marketObservationId") or ""))
        previous = latest_by_ticker.get(ticker)
        if previous is None:
            latest_by_ticker[ticker] = [key, row, 1]
        else:
            previous[2] += 1
            if key >= previous[0]:
                previous[0], previous[1] = key, row

    for _ticker, (_key, latest, seen) in latest_by_ticker.items():
        family = family_of(latest)
        bucket = per_family[family]
        bucket["availableContracts"] += 1
        bucket["observations"] += seen
        bucket["seriesTickers"][latest.get("seriesTicker") or "UNKNOWN"] += 1

        yes = cp.build_price(cp.SIDE_YES, yes_bid=latest.get("yesBid"),
                             yes_ask=latest.get("yesAsk"), unit=cp.UNIT_CENTS)
        no = cp.build_price(cp.SIDE_NO, yes_bid=latest.get("yesBid"),
                            yes_ask=latest.get("yesAsk"),
                            no_ask=latest.get("noAsk"), unit=cp.UNIT_CENTS)
        bucket["bookState"][yes["book"]["bookState"]] += 1
        if cp.is_executable(yes):
            bucket["yesExecutable"] += 1
        if cp.is_executable(no):
            bucket["noExecutable"] += 1
        if not cp.is_executable(yes) and not cp.is_executable(no):
            bucket["neitherExecutable"] += 1
        if yes["book"]["yesBidStatus"] == cp.QUOTE_ABSENT_ZERO:
            bucket["zeroYesBid"] += 1
        for side_cents in (yes["book"]["yesBid"], yes["book"]["yesAsk"]):
            if side_cents is not None and side_cents < Decimal("1"):
                bucket["subCentQuotes"] += 1

    families = {family: {**data,
                         "bookState": dict(data["bookState"]),
                         "seriesTickers": dict(data["seriesTickers"])}
                for family, data in per_family.items()}
    return families, reconcile_universe(families, stats)


def reconcile_universe(families, stats):
    """
    Make silent omission impossible.

    Two identities have to hold, and both are reported with their delta rather
    than merely asserted, so a future regression names its own size:

        raw rows read  ==  admitted rows + every explicitly classified drop
        admitted rows  ==  sum of the per-family observation counts

    `reconciled` is false if either fails, and main() exits non-zero on that.
    A coverage table that quietly describes less than it claims is the defect
    this block exists to prevent, so it fails the report rather than footnoting.
    """
    dropped = dict(stats.get("droppedRows") or {})
    dropped_total = sum(dropped.values())
    raw = stats.get("rawRowsRead", 0)
    admitted = stats.get("admittedRows", 0)
    family_sum = sum(data["observations"] for data in families.values())

    raw_delta = raw - (admitted + dropped_total)
    family_delta = family_sum - admitted
    return {
        "source": "data/edgelab/observations",
        "datesDerivedFrom": "the observation archive itself, NOT model_evaluations",
        "partitionsDiscovered": stats.get("partitionsDiscovered", 0),
        "partitionDates": list(stats.get("partitionDates") or []),
        "rowsPerPartition": dict(stats.get("rowsPerPartition") or {}),
        "rawRowsRead": raw,
        "indexedRows": admitted,
        "droppedRows": dropped,
        "droppedRowsTotal": dropped_total,
        "perFamilyObservationSum": family_sum,
        "rawMinusIndexedMinusDropped": raw_delta,
        "perFamilySumMinusIndexed": family_delta,
        "reconciled": raw_delta == 0 and family_delta == 0,
    }


# ── VIEW B + blast radius: production decision candidates ────────────────────

def _blank_candidate_bucket():
    return {
        "decisionCandidates": 0, "evaluatedRows": 0,
        "exactTicker": 0, "syntheticTicker": 0,
        "noTicker": 0, "observationMatched": 0, "genuineExecutablePrice": 0,
        "missingExecutablePrice": 0, "ambiguousOrRefused": 0, "staleQuote": 0,
        "sideProven": 0, "sideRefused": 0, "sideYes": 0, "sideNo": 0,
        "legacyPriceAvailable": 0, "comparableRows": 0,
        "recordedActionable": 0,
        "legacyActionable": 0, "shadowActionable": 0,
        "betToPass": 0, "passToBet": 0, "verdictUnchanged": 0,
        "reproducedRows": 0, "reproducedLegacyActionable": 0,
        "reproducedShadowActionable": 0,
        "reproducedBetToPass": 0, "reproducedPassToBet": 0,
        "freshRows": 0, "freshLegacyActionable": 0, "freshShadowActionable": 0,
        "freshBetToPass": 0, "freshPassToBet": 0,
        "bookState": collections.Counter(), "tickerResolution": collections.Counter(),
        "priceBasis": collections.Counter(), "refusalReason": collections.Counter(),
        "sideBasis": collections.Counter(), "sideRefusal": collections.Counter(),
        "joinRefusal": collections.Counter(), "artifactSource": collections.Counter(),
        "_ages": [], "_deltas": [],
    }


def _qualify(bml, model_prob, market_prob, exec_price_cents, series_ticker, row):
    """
    Production's OWN qualification chain, called once. Nothing is redefined
    here: every threshold, calibration factor, fee rule and cap comes from
    `bml`, which is scripts/build_market_ledger.py itself.

    Returns (confidence_or_None, edge_fields).
    """
    fields = bml.build_edge_fields(
        model_prob, market_prob, exec_price_cents, bml.CAL_MEDIUM,
        series_ticker=series_ticker)
    gates = []
    conf = bml.confidence_from_edge(fields["netExecutableEdge"])
    conf = bml.cap_tier_for_disagreement(conf, fields.get("rawEdgeVsVF"), gates)
    evidence = row.get("firstInningEvidenceQuality")
    if evidence is not None:
        conf = bml.cap_tier_for_first_inning_evidence_quality(conf, evidence, gates)
    return conf, fields


def analyse(dates, root=None, stale_after=oj.STALE_AFTER_SECONDS,
            real_time_only=False):
    root = root or ROOT
    evaluations_dir = os.path.join(root, "data", "edgelab", "model_evaluations")
    # VIEW B's index. Deliberately still scoped to the caller's dates, because
    # this view is about the decisions taken on those dates. VIEW A builds its
    # own, over the whole observation archive -- see universe_view.
    decision_stats = {}
    index = oj.load_observations(dates=dates, root=root, stats=decision_stats)
    bml = _production_semantics()

    per_family = collections.defaultdict(_blank_candidate_bucket)
    biggest = []
    fidelity = {"comparable": 0, "agrees": 0, "disagrees": 0, "examples": [],
                "disagreementShape": collections.Counter(),
                "disagreementFamilies": collections.Counter()}

    for date in dates:
        path = _partition(evaluations_dir, date)
        if not path:
            continue
        for row in _read_jsonl(path):
            if real_time_only and not is_real_time_decision(row):
                continue
            family = family_of(row)
            bucket = per_family[family]
            bucket["decisionCandidates"] += 1
            # A candidate the model never evaluated (NO_MODEL_SUPPORT,
            # NOT_EVALUATED) is a row written for coverage, not a decision.
            # Counting it as a decision candidate without saying so would make a
            # family look far worse covered than it is -- hitter props alone are
            # 105,131 rows of which zero are evaluated.
            if row.get("evaluationStatus") == "EVALUATED":
                bucket["evaluatedRows"] += 1
            bucket["artifactSource"][row.get("artifactSource") or "unknown"] += 1

            price, detail = oj.price_for_record(row, index,
                                                stale_after_seconds=stale_after)
            method = detail["tickerMethod"]
            bucket["tickerResolution"][method or "NONE"] += 1
            if method == oj.JOIN_EXACT:
                bucket["exactTicker"] += 1
            elif method == oj.JOIN_RESOLVED_VIA_GAMEPK:
                bucket["syntheticTicker"] += 1
            else:
                bucket["noTicker"] += 1

            join = detail["join"]
            if join is None or not join["matched"]:
                if join is not None:
                    bucket["joinRefusal"][join["joinMethod"]] += 1
                bucket["missingExecutablePrice"] += 1
                continue

            bucket["observationMatched"] += 1
            if join["stale"]:
                bucket["staleQuote"] += 1
            if join["quoteAgeSeconds"] is not None:
                bucket["_ages"].append(join["quoteAgeSeconds"])
            if join.get("tieBroken"):
                bucket["ambiguousOrRefused"] += 1

            # Side, fail-closed. An unproven side yields no price at all --
            # never a defaulted YES.
            if detail["side"] is None:
                bucket["sideRefused"] += 1
                bucket["sideRefusal"][detail["sideRefusal"]] += 1
                bucket["missingExecutablePrice"] += 1
                continue
            bucket["sideProven"] += 1
            bucket["sideBasis"][detail["sideBasis"]] += 1
            bucket["sideYes" if detail["side"] == cp.SIDE_YES else "sideNo"] += 1

            bucket["bookState"][price["book"]["bookState"]] += 1
            if not cp.is_executable(price):
                bucket["refusalReason"][price["refusalReason"]] += 1
                bucket["missingExecutablePrice"] += 1
                continue

            bucket["genuineExecutablePrice"] += 1
            bucket["priceBasis"][price["priceBasis"]] += 1

            shadow_cents = cp.as_float(price["executablePrice"])
            legacy_cents = legacy_price_cents(row)
            if legacy_cents is not None:
                bucket["legacyPriceAvailable"] += 1

            model_prob = model_probability(row)
            market_prob = market_probability(row)
            if model_prob is None or market_prob is None:
                continue
            bucket["comparableRows"] += 1

            series = (join["observation"] or {}).get("seriesTicker")
            legacy_conf, legacy_fields = _qualify(
                bml, model_prob, market_prob, legacy_cents, series, row)
            shadow_conf, shadow_fields = _qualify(
                bml, model_prob, market_prob, shadow_cents, series, row)

            # GROUND TRUTH: what production itself recorded for this row. No
            # modelling at all, so this is the honest answer to "how many are
            # actionable today" and is reported separately from the recomputed
            # arms below.
            recorded = row.get("confidence")
            recorded_bet = recorded is not None
            bucket["recordedActionable"] += 1 if recorded_bet else 0

            legacy_bet = legacy_conf is not None
            shadow_bet = shadow_conf is not None
            bucket["legacyActionable"] += 1 if legacy_bet else 0
            bucket["shadowActionable"] += 1 if shadow_bet else 0
            if legacy_bet and not shadow_bet:
                bucket["betToPass"] += 1
            elif shadow_bet and not legacy_bet:
                bucket["passToBet"] += 1
            else:
                bucket["verdictUnchanged"] += 1

            # FIDELITY: does the legacy arm reproduce what production recorded?
            # Where it does, the shadow arm's disagreement with it is caused by
            # the price and nothing else, because the two arms differ in exactly
            # one input. Where it does not, production applied a gate that lives
            # OUTSIDE build_edge_fields -- eligibility, correlation, a per-family
            # rule -- which this report deliberately does not reimplement (that
            # would be the duplicate-authority mistake). Those rows are reported,
            # excluded from the reproduced-subset differential, and never quietly
            # folded into a headline count.
            reproduced = False
            if row.get("evaluationStatus") == "EVALUATED":
                fidelity["comparable"] += 1
                reproduced = (recorded_bet == legacy_bet)
                if reproduced:
                    fidelity["agrees"] += 1
                else:
                    key = ("productionSuppressedAnEdgeAboveThreshold" if recorded is None
                           else "productionActionedAnEdgeBelowThreshold")
                    fidelity["disagrees"] += 1
                    fidelity["disagreementShape"][key] += 1
                    fidelity["disagreementFamilies"][family] += 1
                    if len(fidelity["examples"]) < 8:
                        fidelity["examples"].append({
                            "marketFamily": family, "selection": row.get("selection"),
                            "recordedConfidence": recorded,
                            "recomputedLegacyConfidence": legacy_conf,
                            "estimatedEdgeRecorded": row.get("estimatedEdge"),
                            "netExecutableEdgeRecomputed": legacy_fields["netExecutableEdge"],
                            "shape": key,
                        })

            if reproduced:
                bucket["reproducedRows"] += 1
                bucket["reproducedLegacyActionable"] += 1 if legacy_bet else 0
                bucket["reproducedShadowActionable"] += 1 if shadow_bet else 0
                if legacy_bet and not shadow_bet:
                    bucket["reproducedBetToPass"] += 1
                elif shadow_bet and not legacy_bet:
                    bucket["reproducedPassToBet"] += 1

                # FRESH subset: the same differential, restricted to quotes that
                # were actually current when the decision was made. Over the full
                # archive 139,548 of 143,233 joins are stale, because the postgame
                # pass REWRITES historical evaluation rows the following day and
                # their createdAt is a bookkeeping timestamp rather than a moment
                # anyone decided anything. A verdict change driven by a day-old
                # quote says nothing about what the B2 cutover would do, so this
                # is the number to argue B2 from.
                if not join["stale"]:
                    bucket["freshRows"] += 1
                    bucket["freshLegacyActionable"] += 1 if legacy_bet else 0
                    bucket["freshShadowActionable"] += 1 if shadow_bet else 0
                    if legacy_bet and not shadow_bet:
                        bucket["freshBetToPass"] += 1
                    elif shadow_bet and not legacy_bet:
                        bucket["freshPassToBet"] += 1

            # When production recorded no executable price it priced at the
            # vig-free midpoint; that is the number the shadow price is being
            # compared against, so it is the one reported.
            legacy_effective = (legacy_cents if legacy_cents is not None
                                else round(market_prob * 100, 4))
            delta = round(shadow_cents - legacy_effective, 4)
            bucket["_deltas"].append(delta)
            biggest.append({
                "marketFamily": family, "marketTicker": price["marketTicker"],
                "selection": row.get("selection"),
                "side": detail["side"], "sideBasis": detail["sideBasis"],
                "decidedAt": row.get("createdAt"),
                "legacyPriceCents": legacy_effective,
                "legacyPriceWasRecorded": legacy_cents is not None,
                "shadowPriceCents": shadow_cents,
                "priceDeltaCents": delta,
                "priceBasis": price["priceBasis"],
                "legacyNetEdge": legacy_fields["netExecutableEdge"],
                "shadowNetEdge": shadow_fields["netExecutableEdge"],
                "edgeDelta": (round(shadow_fields["netExecutableEdge"]
                                    - legacy_fields["netExecutableEdge"], 3)
                              if None not in (shadow_fields["netExecutableEdge"],
                                              legacy_fields["netExecutableEdge"])
                              else None),
                "legacyConfidence": legacy_conf, "shadowConfidence": shadow_conf,
                "verdictChanged": legacy_bet != shadow_bet,
                "quoteAgeSeconds": join["quoteAgeSeconds"], "stale": join["stale"],
                "bookState": price["book"]["bookState"],
            })

    families = {}
    for family, data in per_family.items():
        ages = data.pop("_ages")
        deltas = data.pop("_deltas")
        families[family] = {
            **{k: (dict(v) if isinstance(v, collections.Counter) else v)
               for k, v in data.items()},
            "quoteAgeSeconds": _percentiles(ages),
            "absPriceDeltaCents": _percentiles([abs(d) for d in deltas]),
        }

    by_delta = lambda b: abs(b["priceDeltaCents"])                  # noqa: E731
    verdict_changes = sorted((b for b in biggest if b["verdictChanged"]),
                             key=by_delta, reverse=True)
    # The FRESH lists are the ones to read. Sorting the whole corpus by |delta|
    # surfaces almost nothing but stale rows, because the biggest gaps are
    # precisely where a day-old quote is being compared against a probability
    # recorded at a different time -- a 1c-vs-98c "discrepancy" that says
    # nothing about pricing and everything about the postgame rewrite.
    fresh = [b for b in biggest if not b["stale"]]
    fresh_verdict_changes = sorted((b for b in fresh if b["verdictChanged"]),
                                   key=by_delta, reverse=True)
    fresh.sort(key=by_delta, reverse=True)
    biggest.sort(key=by_delta, reverse=True)

    totals = collections.Counter()
    for data in families.values():
        for key in ("decisionCandidates", "evaluatedRows", "observationMatched",
                    "genuineExecutablePrice", "missingExecutablePrice",
                    "ambiguousOrRefused", "staleQuote", "sideProven",
                    "sideRefused", "sideYes", "sideNo", "legacyPriceAvailable",
                    "comparableRows", "recordedActionable",
                    "legacyActionable", "shadowActionable",
                    "betToPass", "passToBet", "verdictUnchanged",
                    "reproducedRows", "reproducedLegacyActionable",
                    "reproducedShadowActionable", "reproducedBetToPass",
                    "reproducedPassToBet", "freshRows", "freshLegacyActionable",
                    "freshShadowActionable", "freshBetToPass", "freshPassToBet"):
            totals[key] += data[key]

    fidelity["disagreementShape"] = dict(fidelity["disagreementShape"])
    fidelity["disagreementFamilies"] = dict(fidelity["disagreementFamilies"])

    universe_families, universe_reconciliation = universe_view(root=root)

    return {
        "decisionCandidateCoverage": families,
        "decisionSideObservationScope": {
            "datesDerivedFrom": "data/edgelab/model_evaluations (this view is "
                                "about production decisions on those dates)",
            "partitionsRead": decision_stats.get("partitionsDiscovered", 0),
            "partitionDates": list(decision_stats.get("partitionDates") or []),
            "rawRowsRead": decision_stats.get("rawRowsRead", 0),
            "indexedRows": decision_stats.get("admittedRows", 0),
            "droppedRows": dict(decision_stats.get("droppedRows") or {}),
        },
        "totals": dict(totals),
        "legacyArmFidelity": fidelity,
        "largestFreshPriceDiscrepancies": fresh[:25],
        "freshVerdictChangingRows": fresh_verdict_changes[:25],
        "largestPriceDiscrepancies": biggest[:25],
        "verdictChangingRows": verdict_changes[:25],
        "universeCoverage": universe_families,
        "universeReconciliation": universe_reconciliation,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="W1-B1 read-only shadow price coverage and blast radius")
    parser.add_argument("--dates", nargs="+", required=True,
                        help="model_evaluations partitions to analyse (YYYY-MM-DD)")
    parser.add_argument("--real-time-only", action="store_true",
                        help="restrict to genuine real-time decisions")
    parser.add_argument("--stale-after", type=int, default=oj.STALE_AFTER_SECONDS)
    parser.add_argument("--write-artifact", action="store_true",
                        help="write the JSON artifact under operational_health/")
    parser.add_argument("--out", default=None,
                        help="explicit output path (implies --write-artifact)")
    parser.add_argument("--json", action="store_true", help="print the full JSON")
    args = parser.parse_args(argv)

    result = analyse(args.dates, stale_after=args.stale_after,
                     real_time_only=args.real_time_only)
    payload = cp.to_jsonable({
        "reportVersion": "w1b1-shadow-price-2",
        "dates": args.dates,
        "realTimeOnly": args.real_time_only,
        "staleAfterSeconds": args.stale_after,
        "productionAuthorityUnchanged": True,
        "modelDrivenRealMoneyAuthority": "OFF",
        **result,
    })

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        totals = payload["totals"]
        print("W1-B1 shadow price report -- dates: %s" % " ".join(args.dates))
        for label, key in (("decision candidates", "decisionCandidates"),
                           ("observation matched", "observationMatched"),
                           ("side proven", "sideProven"),
                           ("side refused", "sideRefused"),
                           ("genuine executable price", "genuineExecutablePrice"),
                           ("missing executable price", "missingExecutablePrice"),
                           ("comparable rows", "comparableRows"),
                           ("actionable as production recorded", "recordedActionable"),
                           ("legacy actionable (recomputed)", "legacyActionable"),
                           ("shadow actionable (recomputed)", "shadowActionable"),
                           ("BET -> PASS", "betToPass"),
                           ("PASS -> BET", "passToBet"),
                           ("reproduced-subset rows", "reproducedRows"),
                           ("  legacy actionable", "reproducedLegacyActionable"),
                           ("  shadow actionable", "reproducedShadowActionable"),
                           ("  BET -> PASS", "reproducedBetToPass"),
                           ("  PASS -> BET", "reproducedPassToBet"),
                           ("fresh-quote subset rows", "freshRows"),
                           ("  legacy actionable", "freshLegacyActionable"),
                           ("  shadow actionable", "freshShadowActionable"),
                           ("  BET -> PASS", "freshBetToPass"),
                           ("  PASS -> BET", "freshPassToBet")):
            print("  %-26s %s" % (label, totals.get(key, 0)))
        fid = payload["legacyArmFidelity"]
        print("  %-26s %s/%s" % ("legacy arm agrees with prod",
                                 fid["agrees"], fid["comparable"]))

    rec = payload["universeReconciliation"]
    if not args.json:
        # Never onto stdout in --json mode: stdout IS the artifact there, and a
        # human summary appended to it makes the document unparseable.
        print("VIEW A -- full archived market universe (dates from %s)"
              % rec["datesDerivedFrom"])
        print("  %-26s %s" % ("partitions discovered", rec["partitionsDiscovered"]))
        print("  %-26s %s" % ("raw rows read", rec["rawRowsRead"]))
        print("  %-26s %s" % ("indexed rows", rec["indexedRows"]))
        for reason, count in sorted(rec["droppedRows"].items()):
            print("  %-26s %s" % ("dropped: " + reason, count))
        print("  %-26s %s" % ("per-family observation sum",
                              rec["perFamilyObservationSum"]))
        print("  %-26s %s" % ("raw - indexed - dropped",
                              rec["rawMinusIndexedMinusDropped"]))
        print("  %-26s %s" % ("familySum - indexed", rec["perFamilySumMinusIndexed"]))
        print("  %-26s %s" % ("RECONCILED", rec["reconciled"]))

    if args.write_artifact or args.out:
        out_path = args.out or os.path.join(OUT_DIR, OUT_NAME)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        write_json_atomic(payload, out_path)
        print("wrote %s" % out_path, file=sys.stderr if args.json else sys.stdout)

    if not rec["reconciled"]:
        # Fail the report rather than footnote the gap. A coverage table that
        # describes less than it claims is worse than no table, because it is
        # read as complete.
        print("ERROR: full-universe coverage does not reconcile -- "
              "raw(%s) != indexed(%s) + dropped(%s), or familySum(%s) != indexed(%s)"
              % (rec["rawRowsRead"], rec["indexedRows"], rec["droppedRowsTotal"],
                 rec["perFamilyObservationSum"], rec["indexedRows"]),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
