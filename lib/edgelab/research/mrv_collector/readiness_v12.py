"""
MRV research-readiness gates V1.2 (MRV_READINESS_GATES_V1_2_2026_09_24).

WHY V1.2 EXISTS.  The first live window (2026-09-23/24, 29 COMPLETE cycles)
showed that V1.1 (health.GATES, kept unchanged and still reported) judges
three things that are not research-relevant core capture:
  * tomorrow's games whose Kalshi families are only partly listed (Kalshi
    lists KXMLBGAME first and the other families later),
  * tomorrow's games whose sportsbooks have not posted lines yet,
  * thin inning families inside the global two-sided-book share.
V1.2 changes ONLY the populations those quality gates are evaluated on, and
only for readiness; capture is unchanged and no observation is discarded.

FIXED MATURITY WINDOW: T-240 through T-5 minutes before scheduled first
pitch, both ends included.  It is not chosen from the live data: it is the
grid the MRV lead/lag design already used before any prospective capture
(lib/edgelab/research/market_structure/leadlag.py
grid_rows(first_before=240, last_before=5, step=5)).

CORE FAMILIES: exactly the six the V1.1 contractsPerCoreFamily sample gate
already used.  Inning, RFI, F3/F7, extras and props stay captured and are
reported, but cannot make the core readiness layer unhealthy.

Timing: a game-cycle's minutes-to-start = scheduled start minus the cycle's
start; a book's = scheduled start minus that book's own respondedAt.  The
scheduled start is the latest persisted MLB state value observed by the end
of that cycle (a postponement or time change is honoured as of the cycle).
A game with no known start is UNKNOWN_START: excluded from readiness and
counted, never guessed.

Every threshold is V1.1's value.  Sample floors count REGULAR_SEASON
observations inside the window only; POSTSEASON and OTHER_OR_UNKNOWN never
count toward them.
"""
from collections import defaultdict
from datetime import datetime, timezone

from lib.edgelab.research.mrv_collector import season_phase as SP

GATE_VERSION = "MRV_READINESS_GATES_V1_2_2026_09_24"
WINDOW_MAX_MINUTES = 240.0
WINDOW_MIN_MINUTES = 5.0
CORE_FAMILIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL")

IN_WINDOW = "IN_READINESS_WINDOW"
BEFORE_WINDOW = "OUTSIDE_READINESS_WINDOW_BEFORE_T240"
AFTER_WINDOW = "OUTSIDE_READINESS_WINDOW_AFTER_T5"
UNKNOWN_START = "UNKNOWN_START"

GATES_V1_2 = {
    "gateVersion": GATE_VERSION,
    "supersedesForReadiness": "MRV_READINESS_GATES_V1_1_2026_09_23 (still computed and reported unchanged)",
    "window": {"fromMinutesBeforeFirstPitch": WINDOW_MAX_MINUTES, "toMinutesBeforeFirstPitch": WINDOW_MIN_MINUTES,
               "inclusive": True, "basis": "MRV lead/lag design grid T-240..T-5 (market_structure/leadlag.py grid_rows; predeclared, not fitted to live data)"},
    "coreFamilies": list(CORE_FAMILIES),
    "familyCoverage": {"starvedGameCyclesMax": 0, "coreFamiliesPresentShareMin": 0.98,
                       "population": "in-window eligible game-cycles, six core families only"},
    "sportsbook": {"matchedGameShareMin": 0.90, "population": "eligible games observed inside the window"},
    "orderBook": {"twoSidedBookShareMin": 0.95, "booksWithDepthShareMin": 0.95,
                  "population": "archived core-family books whose own fetch time is inside the window"},
    "sample": {"uniqueGamesMin": 60, "datesMin": 10, "contractsPerCoreFamilyMin": 80, "phase": SP.REGULAR_SEASON,
               "population": "REGULAR_SEASON games/contracts with at least one in-window observation"},
    "unchangedFromV1_1": ["cadence.*", "completeness.*", "sportsbook.ambiguousJoins", "sportsbook.booksPerEvent",
                          "sportsbook.notBudgetDegraded", "timestamps.perFetchShare",
                          "informationEvents.gamesWithStateShare", "informationEvents.lineupTransitions"],
}


def _ts(s):
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def window_status(start, at):
    """start, at: datetimes (start may be None) -> one of the four statuses."""
    if start is None or at is None:
        return UNKNOWN_START
    m = (start - at).total_seconds() / 60.0
    if m > WINDOW_MAX_MINUTES:
        return BEFORE_WINDOW
    if m < WINDOW_MIN_MINUTES:
        return AFTER_WINDOW
    return IN_WINDOW


def _share(num, den):
    return (num / den) if den else None


def _ge(v, m):
    return v is not None and v >= m


def _le(v, m):
    return v is not None and v <= m


def build_readiness_v12(store, dates, v11_metrics, v11_checks):
    """Compute V1.2 metrics and gates from the persisted corpus for `dates`, reusing V1.1's unchanged checks."""
    manifests = []
    for d in dates:
        manifests.extend(store.iter_manifests(d))
    cycles = {m["runId"]: m for m in manifests if m.get("runId")}

    # scheduled start as of each cycle, from persisted MLB state rows
    starts = defaultdict(list)                     # gamePk -> [(observedAt, start)]
    for d in dates:
        for s in store.iter_gz("mlb_state", d):
            st = _ts((s.get("state") or {}).get("scheduledStart"))
            obs = _ts(s.get("observedAt"))
            if s.get("gamePk") is not None and st and obs:
                starts[s["gamePk"]].append((obs, st))
    for v in starts.values():
        v.sort()

    def start_as_of(pk, cutoff):
        best = None
        for obs, st in starts.get(pk, ()):
            if cutoff is None or obs <= cutoff:
                best = st
        return best

    cyc_start, cyc_cut = {}, {}
    for rid, m in cycles.items():
        cyc_start[rid] = _ts(m.get("cycleStartedAt"))
        cyc_cut[rid] = _ts(m.get("cycleCompletedAt")) or cyc_start[rid]

    game_status = {}                               # (runId, gamePk) -> status
    status_counts = defaultdict(int)
    for rid, m in cycles.items():
        for pk in m.get("eligibleGamePks") or []:
            stt = window_status(start_as_of(pk, cyc_cut[rid]), cyc_start[rid])
            game_status[(rid, pk)] = stt
            status_counts[stt] += 1

    # families listed per game-cycle and in-window sample, from the cross-sections
    fams = defaultdict(set)
    sample = {p: {"games": set(), "dates": set(), "contracts": defaultdict(set)} for p in (SP.REGULAR_SEASON, SP.POSTSEASON, SP.OTHER_OR_UNKNOWN)}
    for d in dates:
        for x in store.iter_gz("kalshi_crosssection", d):
            rid = x.get("runId")
            for t, e in (x.get("tickers") or {}).items():
                pk = e.get("gamePk")
                if pk is None:
                    continue
                fams[(rid, pk)].add(e.get("s"))
                if game_status.get((rid, pk)) != IN_WINDOW:
                    continue
                ph = e.get("phase") if e.get("phase") in sample else SP.OTHER_OR_UNKNOWN
                b = sample[ph]
                b["games"].add(pk)
                if e.get("gameOfficialDate"):
                    b["dates"].add(e["gameOfficialDate"])
                b["contracts"][e.get("s")].add(t)

    in_window_gc = [k for k, v in game_status.items() if v == IN_WINDOW]
    listed = starved = complete = unlisted = 0
    starved_examples = []
    for k in in_window_gc:
        present = [f for f in CORE_FAMILIES if f in fams.get(k, ())]
        if not present:
            unlisted += 1
            continue
        listed += 1
        if len(present) == len(CORE_FAMILIES):
            complete += 1
        else:
            starved += 1
            if len(starved_examples) < 20:
                starved_examples.append({"runId": k[0], "gamePk": k[1], "missing": [f for f in CORE_FAMILIES if f not in present]})

    # sportsbook: games observed inside the window, matched during an in-window cycle
    window_games = {pk for (rid, pk), v in game_status.items() if v == IN_WINDOW}
    matched_in_window = set()
    for d in dates:
        for j in store.iter_gz("sportsbook_joins", d):
            if j.get("status") == "MATCHED" and game_status.get((j.get("runId"), j.get("gamePk"))) == IN_WINDOW:
                matched_in_window.add(j.get("gamePk"))

    # order books: core family, own fetch time inside the window
    ob = {"core": [0, 0, 0], "nonCore": [0, 0, 0], "outsideWindowOrUnknown": [0, 0, 0]}   # books, twoSided, withDepth
    for d in dates:
        for b in store.iter_gz("kalshi_books", d):
            bk = b.get("book") or {}
            pk, rid = b.get("gamePk"), b.get("runId")
            stt = window_status(start_as_of(pk, cyc_cut.get(rid)), _ts(b.get("respondedAt")))
            key = "outsideWindowOrUnknown" if stt != IN_WINDOW else ("core" if b.get("seriesTicker") in CORE_FAMILIES else "nonCore")
            ob[key][0] += 1
            ob[key][1] += 1 if bk.get("twoSided") else 0
            ob[key][2] += 1 if (bk.get("levelsYes") or 0) + (bk.get("levelsNo") or 0) > 0 else 0

    reg = sample[SP.REGULAR_SEASON]
    metrics = {
        "window": GATES_V1_2["window"],
        "gameCycles": {"total": len(game_status), "byStatus": dict(status_counts)},
        "familyCoverage": {"inWindowGameCycles": len(in_window_gc), "inWindowListed": listed, "inWindowAllCore": complete,
                           "starvedGameCycles": starved, "inWindowUnlistedGameCycles": unlisted,
                           "coreFamiliesPresentShare": _share(complete, listed), "starvedExamples": starved_examples},
        "sportsbook": {"gamesInWindow": len(window_games), "matchedInWindow": len(matched_in_window & window_games),
                       "matchedGameShare": _share(len(matched_in_window & window_games), len(window_games)),
                       "unmatchedInWindowGamePks": sorted(window_games - matched_in_window)},
        "orderBook": {k: {"books": v[0], "twoSidedShare": _share(v[1], v[0]), "withDepthShare": _share(v[2], v[0])} for k, v in ob.items()},
        "sample": {"phase": SP.REGULAR_SEASON, "uniqueGames": len(reg["games"]), "dates": len(reg["dates"]),
                   "contractsPerCoreFamily": {f: len(reg["contracts"].get(f, ())) for f in CORE_FAMILIES},
                   "contractsNonCore": {f: len(c) for f, c in reg["contracts"].items() if f not in CORE_FAMILIES},
                   "byPhase": {p: {"uniqueGames": len(v["games"]), "dates": len(v["dates"])} for p, v in sample.items()}},
    }
    g = GATES_V1_2
    core_ob = metrics["orderBook"]["core"]
    checks = {k: v for k, v in v11_checks.items() if k.startswith(("cadence.", "completeness.", "timestamps.", "informationEvents."))
              or k in ("sportsbook.ambiguousJoins", "sportsbook.booksPerEvent", "sportsbook.notBudgetDegraded")}
    checks.update({
        "familyCoverage.starvedGameCycles": _le(starved, g["familyCoverage"]["starvedGameCyclesMax"]) and len(in_window_gc) > 0,
        "familyCoverage.coreFamiliesPresentShare": _ge(metrics["familyCoverage"]["coreFamiliesPresentShare"], g["familyCoverage"]["coreFamiliesPresentShareMin"]),
        "sportsbook.matchedGameShare": _ge(metrics["sportsbook"]["matchedGameShare"], g["sportsbook"]["matchedGameShareMin"]),
        "orderBook.twoSidedShare": _ge(core_ob["twoSidedShare"], g["orderBook"]["twoSidedBookShareMin"]),
        "orderBook.withDepthShare": _ge(core_ob["withDepthShare"], g["orderBook"]["booksWithDepthShareMin"]),
        "sample.uniqueGames": _ge(metrics["sample"]["uniqueGames"], g["sample"]["uniqueGamesMin"]),
        "sample.dates": _ge(metrics["sample"]["dates"], g["sample"]["datesMin"]),
        "sample.contractsPerCoreFamily": all(_ge(metrics["sample"]["contractsPerCoreFamily"][f], g["sample"]["contractsPerCoreFamilyMin"])
                                             for f in CORE_FAMILIES),
    })
    infra = [k for k in checks if not k.startswith("sample.")]
    return {"gateVersion": GATE_VERSION, "definition": GATES_V1_2, "metrics": metrics, "checks": checks,
            "infrastructureHealthy": all(checks[k] for k in infra), "researchReady": all(checks.values()),
            "failing": sorted(k for k, v in checks.items() if not v)}
