"""
Durable health report and research-readiness gates for the MRV collector.

Everything is computed from the persisted corpus (attempt rows, manifests,
partitions) -- never from GitHub run history -- so a capture that ran but
did not persist counts as NOT delivered.

Gates are frozen here.  They are the definition of "the prospective layer
is scientifically usable"; they are not to be moved to fit an outcome.
"""
from datetime import datetime, timedelta, timezone

from lib.edgelab.research.mrv_collector import season_phase as SP

MLB_WINDOW_UTC_HOURS = tuple(list(range(15, 24)) + list(range(0, 5)))   # 15:00-04:59 UTC

GATES = {
    "gateVersion": "MRV_READINESS_GATES_V1_1_2026_09_23",
    "seasonPhaseRule": SP.SEASON_PHASE_RULE_VERSION,
    "samplePhase": SP.REGULAR_SEASON,
    "cadence": {"medianGapMinutesMax": 10.0, "p90GapMinutesMax": 15.0, "maxInWindowGapMinutesMax": 30.0,
                "note": "delivered cadence between consecutive persisted cycle starts inside the MLB window (15:00-04:59 UTC)"},
    "completeness": {"completeShareMin": 0.90, "unaccountedRowsMax": 0, "failedShareMax": 0.05},
    "familyCoverage": {"starvedGameCyclesMax": 0, "coreFamiliesPresentShareMin": 0.98},
    "sportsbook": {"matchedGameShareMin": 0.90, "ambiguousJoinsMax": 0, "booksPerMatchedEventMin": 3,
                   "budgetDegradedCyclesMax": 0,
                   "note": "a cycle whose sportsbook leg is DEGRADED_BUDGET_GUARD (or NOT_CONFIGURED / FETCH_FAILED) fails this gate; missing quotes are never zero disagreement"},
    "timestamps": {"perFetchTimestampShareMin": 1.0},
    "informationEvents": {"gamesWithStateShareMin": 0.95, "lineupTransitionsObservedMin": 20},
    "orderBook": {"twoSidedBookShareMin": 0.95, "booksWithDepthShareMin": 0.95},
    "sample": {"uniqueGamesMin": 60, "datesMin": 10, "contractsPerCoreFamilyMin": 80,
               "note": "the prior program's inferential floor; games, dates (MLB official date) and contracts counted from REGULAR_SEASON observations ONLY -- POSTSEASON is a separate regime and OTHER_OR_UNKNOWN never counts"},
}


def _ts(s):
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def cadence_stats(cycle_starts):
    """cycle_starts: iso strings of DELIVERED cycles. Gaps measured only when both ends fall inside the MLB window."""
    ts = sorted(t for t in (_ts(s) for s in cycle_starts) if t)
    gaps, in_window = [], []
    for a, b in zip(ts, ts[1:]):
        g = (b - a).total_seconds() / 60.0
        gaps.append(g)
        if a.hour in MLB_WINDOW_UTC_HOURS and b.hour in MLB_WINDOW_UTC_HOURS:
            in_window.append(g)
    return {"delivered": len(ts), "gapsAll": len(gaps), "gapsInWindow": len(in_window),
            "medianGapMin": _pct(in_window, 0.5), "p90GapMin": _pct(in_window, 0.9), "maxGapMin": max(in_window) if in_window else None,
            "histogram": {"<=5": sum(1 for g in in_window if g <= 5), "5-10": sum(1 for g in in_window if 5 < g <= 10),
                          "10-15": sum(1 for g in in_window if 10 < g <= 15), "15-30": sum(1 for g in in_window if 15 < g <= 30),
                          ">30": sum(1 for g in in_window if g > 30)}}


def build_health(store, *, end_date, days=7):
    d1 = datetime.strptime(end_date, "%Y-%m-%d")
    dates = [(d1 - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(days - 1, -1, -1)]
    attempts, manifests = [], []
    for d in dates:
        attempts.extend(store.iter_attempts(d))
        manifests.extend(store.iter_manifests(d))
    starts = [m["cycleStartedAt"] for m in manifests if m.get("cycleStartedAt")]
    classes = {"COMPLETE": 0, "PARTIAL": 0, "FAILED": 0}
    for m in manifests:
        classes[m.get("captureClass", "FAILED")] = classes.get(m.get("captureClass", "FAILED"), 0) + 1
    delivered = len(manifests)
    requested = len(attempts)
    rc = [m.get("reconciliation") or {} for m in manifests]
    tot = lambda k: sum(int(r.get(k) or 0) for r in rc)
    starved_cycles = sum(1 for m in manifests if (m.get("starvation") or {}).get("count", 0) > 0)
    games_cycles = tot("gamesWithMarkets")
    core_present = sum(int(r.get("gamesWithMarkets") or 0) - int((m.get("starvation") or {}).get("count", 0)) for r, m in zip(rc, manifests))
    # sportsbook
    joins = {"matched": 0, "ambiguous": 0, "unmatched": 0}
    for m in manifests:
        for k in joins:
            joins[k] += int((m.get("joins") or {}).get(k) or 0)
    matched_games, elig_games, books_per_event = set(), set(), []
    state_games, lineup_transitions, pitcher_transitions = set(), 0, 0
    ts_rows = ts_ok = 0
    books_seen = two_sided = with_depth = 0
    for d in dates:
        for j in store.iter_gz("sportsbook_joins", d):
            if j.get("status") == "MATCHED":
                matched_games.add(j.get("gamePk"))
        per_event_books = {}
        for o in store.iter_gz("sportsbook_odds", d):
            per_event_books.setdefault(o.get("eventId"), set()).add(o.get("bookmaker"))
            ts_rows += 1
            ts_ok += 1 if (o.get("requestedAt") and o.get("respondedAt") and o.get("providerLastUpdate")) else 0
        books_per_event.extend(len(v) for v in per_event_books.values())
        for s in store.iter_gz("mlb_state", d):
            state_games.add(s.get("gamePk"))
            for t in s.get("transitions") or []:
                if t.get("field") in ("awayLineupPosted", "homeLineupPosted", "awayLineupIds", "homeLineupIds"):
                    lineup_transitions += 1
                if t.get("field") in ("awayProbableId", "homeProbableId"):
                    pitcher_transitions += 1
        for b in store.iter_gz("kalshi_books", d):
            books_seen += 1
            ts_rows += 1
            ts_ok += 1 if (b.get("requestedAt") and b.get("respondedAt")) else 0
            bk = b.get("book") or {}
            if bk.get("twoSided"):
                two_sided += 1
            if (bk.get("levelsYes") or 0) + (bk.get("levelsNo") or 0) > 0:
                with_depth += 1
    for m in manifests:
        for pk in m.get("eligibleGamePks") or []:
            elig_games.add(pk)
    # Sample counts are per season phase; only REGULAR_SEASON feeds the regular-season gate.
    by_phase = {p: {"games": set(), "dates": set(), "contracts": {}} for p in (SP.REGULAR_SEASON, SP.POSTSEASON, SP.OTHER_OR_UNKNOWN)}
    for d in dates:
        for x in store.iter_gz("kalshi_crosssection", d):
            for t, e in (x.get("tickers") or {}).items():
                if e.get("gamePk") is None:
                    continue
                ph = e.get("phase") if e.get("phase") in by_phase else SP.OTHER_OR_UNKNOWN
                b = by_phase[ph]
                b["games"].add(e["gamePk"])
                if e.get("gameOfficialDate"):
                    b["dates"].add(e["gameOfficialDate"])
                b["contracts"].setdefault(e.get("s"), set()).add(t)
    reg = by_phase[SP.REGULAR_SEASON]
    unique_games = reg["games"]
    contracts_by_family = reg["contracts"]
    odds_status = {}
    for m in manifests:
        k = m.get("oddsStatus") or (m.get("odds") or {}).get("status") or "UNREPORTED"
        odds_status[k] = odds_status.get(k, 0) + 1
    cad = cadence_stats(starts)
    metrics = {
        "window": {"dates": dates, "days": days},
        "captures": {"requested": requested, "delivered": delivered, "complete": classes.get("COMPLETE", 0),
                     "partial": classes.get("PARTIAL", 0), "failed": classes.get("FAILED", 0),
                     "attemptsWithoutManifest": max(0, requested - delivered)},
        "cadence": cad,
        "markets": {"received": tot("marketsReceived"), "archived": tot("marketsArchived"), "referenced": tot("marketsReferenced"),
                    "excluded": tot("marketsExcluded"), "unaccounted": tot("unaccountedRows"),
                    "booksRequested": tot("booksRequested"), "booksReceived": tot("booksReceived"), "booksFailed": tot("booksFailed")},
        "coverage": {"samplePhase": SP.REGULAR_SEASON, "uniqueGames": len(unique_games), "dates": len(reg["dates"]),
                     "byPhase": {p: {"uniqueGames": len(v["games"]), "dates": len(v["dates"]),
                                     "contractsPerFamily": {k: len(c) for k, c in v["contracts"].items()}} for p, v in by_phase.items()},
                     "cycleDates": len({m.get("gameDate") for m in manifests}),
                     "gameCycles": games_cycles, "starvedGameCycles": starved_cycles,
                     "coreFamiliesPresentShare": (core_present / games_cycles) if games_cycles else None,
                     "contractsPerCoreFamily": {k: len(v) for k, v in contracts_by_family.items()}},
        "sportsbook": {**joins, "eligibleGames": len(elig_games), "matchedGames": len(matched_games),
                       "legStatusByCycle": odds_status,
                       "degradedCycles": sum(v for k, v in odds_status.items() if k != "OK"),
                       "matchedGameShare": (len(matched_games & elig_games) / len(elig_games)) if elig_games else None,
                       "booksPerEventMedian": _pct(books_per_event, 0.5)},
        "timestamps": {"rowsChecked": ts_rows, "perFetchTimestampShare": (ts_ok / ts_rows) if ts_rows else None},
        "informationEvents": {"gamesWithState": len(state_games), "gamesWithStateShare": (len(state_games & elig_games) / len(elig_games)) if elig_games else None,
                              "lineupTransitionsObserved": lineup_transitions, "pitcherTransitionsObserved": pitcher_transitions},
        "orderBook": {"booksArchived": books_seen, "twoSidedShare": (two_sided / books_seen) if books_seen else None,
                      "withDepthShare": (with_depth / books_seen) if books_seen else None},
    }
    gates = evaluate_gates(metrics)
    # V1.2 readiness (maturity window + core families) is reported ALONGSIDE the unchanged V1.1 gates.
    from lib.edgelab.research.mrv_collector.readiness_v12 import build_readiness_v12
    return {"generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "metrics": metrics, "gates": gates,
            "readinessV1_2": build_readiness_v12(store, dates, metrics, gates["checks"])}


def _ge(v, m):
    return v is not None and v >= m


def _le(v, m):
    return v is not None and v <= m


def evaluate_gates(metrics):
    g = GATES
    cad, cap, mk, cov, sb, ts, ie, ob = (metrics[k] for k in ("cadence", "captures", "markets", "coverage", "sportsbook", "timestamps", "informationEvents", "orderBook"))
    delivered = cap["delivered"] or 0
    checks = {
        "cadence.medianGap": _le(cad["medianGapMin"], g["cadence"]["medianGapMinutesMax"]),
        "cadence.p90Gap": _le(cad["p90GapMin"], g["cadence"]["p90GapMinutesMax"]),
        "cadence.maxInWindowGap": _le(cad["maxGapMin"], g["cadence"]["maxInWindowGapMinutesMax"]),
        "completeness.completeShare": _ge((cap["complete"] / delivered) if delivered else None, g["completeness"]["completeShareMin"]),
        "completeness.unaccountedRows": _le(mk["unaccounted"], g["completeness"]["unaccountedRowsMax"]) and delivered > 0,
        "completeness.failedShare": _le((cap["failed"] / delivered) if delivered else None, g["completeness"]["failedShareMax"]),
        "familyCoverage.starvedGameCycles": _le(cov["starvedGameCycles"], g["familyCoverage"]["starvedGameCyclesMax"]) and delivered > 0,
        "familyCoverage.coreFamiliesPresentShare": _ge(cov["coreFamiliesPresentShare"], g["familyCoverage"]["coreFamiliesPresentShareMin"]),
        "sportsbook.matchedGameShare": _ge(sb["matchedGameShare"], g["sportsbook"]["matchedGameShareMin"]),
        "sportsbook.ambiguousJoins": _le(sb["ambiguous"], g["sportsbook"]["ambiguousJoinsMax"]) and delivered > 0,
        "sportsbook.booksPerEvent": _ge(sb["booksPerEventMedian"], g["sportsbook"]["booksPerMatchedEventMin"]),
        "sportsbook.notBudgetDegraded": delivered > 0 and _le(sb["degradedCycles"], g["sportsbook"]["budgetDegradedCyclesMax"]),
        "timestamps.perFetchShare": _ge(ts["perFetchTimestampShare"], g["timestamps"]["perFetchTimestampShareMin"]),
        "informationEvents.gamesWithStateShare": _ge(ie["gamesWithStateShare"], g["informationEvents"]["gamesWithStateShareMin"]),
        "informationEvents.lineupTransitions": _ge(ie["lineupTransitionsObserved"], g["informationEvents"]["lineupTransitionsObservedMin"]),
        "orderBook.twoSidedShare": _ge(ob["twoSidedShare"], g["orderBook"]["twoSidedBookShareMin"]),
        "orderBook.withDepthShare": _ge(ob["withDepthShare"], g["orderBook"]["booksWithDepthShareMin"]),
        "sample.uniqueGames": _ge(cov["uniqueGames"], g["sample"]["uniqueGamesMin"]),
        "sample.dates": _ge(cov["dates"], g["sample"]["datesMin"]),
        "sample.contractsPerCoreFamily": bool(cov["contractsPerCoreFamily"]) and all(
            _ge(cov["contractsPerCoreFamily"].get(f), g["sample"]["contractsPerCoreFamilyMin"])
            for f in ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL")),
    }
    infra = [k for k in checks if not k.startswith("sample.")]
    return {"gateVersion": g["gateVersion"], "checks": checks,
            "infrastructureHealthy": all(checks[k] for k in infra),
            "researchReady": all(checks.values()),
            "failing": sorted(k for k, v in checks.items() if not v)}
