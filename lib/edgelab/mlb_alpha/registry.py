"""
lib/edgelab/mlb_alpha/registry.py
=================================
Dynamic MLB market registry: every Kalshi series that could be MLB, what it is,
how far we can price / settle / test it, and where our captures miss it.

The registry is a pure function of its inputs (series rows, market rows,
settlement rows, collector policies) so that it is reproducible from archived
evidence and from a live read-only pull alike. No I/O, no clock beyond the
`as_of` the caller supplies.

STATUS LADDER (per series), each rung requires the one before it:
  DISCOVERED     seen in a series catalogue or a market row
  CLASSIFIED     taxonomy gave a membership verdict that is not UNCLASSIFIED
  PARSEABLE      >=99% of observed market tickers parse into structured fields
  PRICEABLE      a model path exists (unified sim, or an existing player model)
  SETTLEABLE     settlement truth for the series exists in the archive
  SAMPLE_FLOOR_MET  >= MIN_TESTED_SETTLED settled contracts across >= MIN_TESTED_GAMES games: enough
                 data EXISTS to test the family. It says nothing about whether any test was run or passed.
  QUALIFIED      never assigned here: only an explicit validation program can

A series that stops short of a rung is listed in `gaps` with the exact
reason. Nothing is dropped because it lacks a model.
"""

from collections import defaultdict

from lib.edgelab.mlb_alpha import taxonomy as tx

MIN_TESTED_SETTLED = 200
MIN_TESTED_GAMES = 60
PARSEABLE_FLOOR = 0.99

LADDER = ("DISCOVERED", "CLASSIFIED", "PARSEABLE", "PRICEABLE", "SETTLEABLE", "SAMPLE_FLOOR_MET")


def _price(x):
    """A quote already declared to be in dollars, or None. Magnitude is never used to
    guess a unit (see lib.edgelab.price_units): loaders convert by declared field/date
    and hand this module dollars; anything outside [0, 1] is rejected, not rescaled."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 1.0 else None


def _new_series_entry(series, title):
    cls = tx.classify_series(series, title)
    return {
        "seriesTicker": series, "title": title, "classification": cls,
        "observed": {"markets": set(), "events": set(), "games": set(), "thresholds": set(),
                     "firstSeen": None, "lastSeen": None, "twoSided": 0, "quotedRows": 0,
                     "spreads": [], "volume": 0.0, "parseOk": 0, "parseTotal": 0,
                     "parseFailExamples": []},
        "settlement": {"settled": 0, "yes": 0, "unresolved": 0, "games": set(),
                       "firstDate": None, "lastDate": None},
    }


def build_registry(series_rows, market_rows, settlement_rows=(), collector_policies=None, as_of=None, catalogue_history=None):
    """
    series_rows      iterable of {'seriesTicker','title'}   (from /series or a catalogue)
    market_rows      iterable of {'ticker','ts','yesBid','yesAsk','volume','status'} ; prices in DOLLARS (loader converts by declared unit)
    settlement_rows  iterable of {'marketTicker','settlementStatus','result','settledAt'}
    collector_policies  {'production': set(series), 'alpha0002': {series: tier}, 'mrv': {series: tier}}
    catalogue_history   [(date, {series,...})] oldest first: lets the registry report WHEN each series first appeared, so a
                        new market family (Kalshi added inning winner/total on 2026-08-18) is flagged the day it appears
    """
    entries = {}
    for r in series_rows or ():
        s = (r.get("seriesTicker") or "").upper()
        if s and s not in entries:
            entries[s] = _new_series_entry(s, r.get("title"))
            entries[s]["discoveredVia"] = "series_catalogue"

    for m in market_rows or ():
        t = m.get("ticker") or m.get("marketTicker")
        if not t:
            continue
        s = t.split("-")[0].upper()
        if s not in entries:
            entries[s] = _new_series_entry(s, None)
            entries[s]["discoveredVia"] = "market_row_only"
        o = entries[s]["observed"]
        p = tx.parse_market_ticker(t)
        o["parseTotal"] += 1
        if str(p["parseStatus"]).startswith("PARSED") or p["parseStatus"] == "SERIES_GRAMMAR_NOT_DESCRIBED":
            o["parseOk"] += 1
        elif len(o["parseFailExamples"]) < 3:
            o["parseFailExamples"].append([t, p["parseStatus"]])
        o["markets"].add(t)
        parts = t.split("-")
        if len(parts) > 1:
            o["events"].add(parts[1] if p["kind"] in ("SERIES_MARKET", "SEASON_MARKET") else "-".join(parts[:2]))
        if p.get("gameKey"):
            o["games"].add(p["gameKey"])
        if p.get("threshold") is not None:
            o["thresholds"].add(p["threshold"])
        ts = m.get("ts")
        if ts:
            o["firstSeen"] = ts if o["firstSeen"] is None or ts < o["firstSeen"] else o["firstSeen"]
            o["lastSeen"] = ts if o["lastSeen"] is None or ts > o["lastSeen"] else o["lastSeen"]
        yb, ya = _price(m.get("yesBid")), _price(m.get("yesAsk"))
        if yb is not None or ya is not None:
            o["quotedRows"] += 1
        if yb is not None and ya is not None and ya >= yb:
            o["twoSided"] += 1
            if len(o["spreads"]) < 5000:
                o["spreads"].append(round((ya - yb) * 100, 2))
        try:
            o["volume"] += float(m.get("volume") or 0)
        except (TypeError, ValueError):
            pass

    for r in settlement_rows or ():
        t = r.get("marketTicker")
        if not t:
            continue
        s = t.split("-")[0].upper()
        if s not in entries:
            entries[s] = _new_series_entry(s, None)
            entries[s]["discoveredVia"] = "settlement_row_only"
        st = entries[s]["settlement"]
        status = r.get("settlementStatus")
        if status == "SETTLED":
            st["settled"] += 1
            st["yes"] += 1 if r.get("result") == "YES" else 0
            parts = t.split("-")
            p = tx.parse_event_segment(parts[1]) if len(parts) > 1 else None
            if p:
                st["games"].add(p["gameKey"])
            d = (r.get("settledAt") or "")[:10]
            if d:
                st["firstDate"] = d if st["firstDate"] is None or d < st["firstDate"] else st["firstDate"]
                st["lastDate"] = d if st["lastDate"] is None or d > st["lastDate"] else st["lastDate"]
        elif status == "SETTLEMENT_UNRESOLVED":
            st["unresolved"] += 1

    pol = collector_policies or {}
    out_series, gaps = [], []
    for s in sorted(entries):
        e = entries[s]
        cls, o, st = e["classification"], e["observed"], e["settlement"]
        rungs = ["DISCOVERED"]
        reasons = []
        if cls["membership"] != tx.UNCLASSIFIED:
            rungs.append("CLASSIFIED")
        else:
            reasons.append("UNCLASSIFIED: %s" % cls["membershipReason"])
        parse_share = (o["parseOk"] / o["parseTotal"]) if o["parseTotal"] else None
        if parse_share is not None and parse_share >= PARSEABLE_FLOOR and "CLASSIFIED" in rungs:
            rungs.append("PARSEABLE")
        elif parse_share is None and "CLASSIFIED" in rungs:
            reasons.append("NO_MARKET_ROWS_OBSERVED: parse not evidenced")
        elif parse_share is not None:
            reasons.append("PARSE_SHARE %.3f below floor; examples %s" % (parse_share, o["parseFailExamples"]))
        if cls["modelStatus"] in (tx.MS_PRICED_SIM, tx.MS_PRICED_SIM_APPROX, tx.MS_PLAYER_MODEL_EXISTS) \
                and "PARSEABLE" in rungs:
            rungs.append("PRICEABLE")
        elif cls["membership"] == tx.MLB and cls["domain"] in (tx.D_GAME, tx.D_PLAYER_GAME):
            reasons.append("NO_MODEL_PATH (%s)" % cls["modelStatus"])
        if cls["settlement"] == tx.ST_ARCHIVED and st["settled"] > 0 and "PRICEABLE" in rungs:
            rungs.append("SETTLEABLE")
        elif cls["membership"] == tx.MLB and cls["domain"] in (tx.D_GAME, tx.D_PLAYER_GAME):
            reasons.append("SETTLEMENT_TRUTH_MISSING (archived settled rows: %d)" % st["settled"])
        if "SETTLEABLE" in rungs and st["settled"] >= MIN_TESTED_SETTLED and len(st["games"]) >= MIN_TESTED_GAMES:
            rungs.append("SAMPLE_FLOOR_MET")
        elif "SETTLEABLE" in rungs:
            reasons.append("BELOW_TEST_FLOOR (%d settled / %d games)" % (st["settled"], len(st["games"])))

        captured_by = {}
        if "production" in pol:
            captured_by["production"] = s in pol["production"]
        for k in ("alpha0002", "mrv"):
            if k in pol:
                captured_by[k] = pol[k].get(s) or False
        if cls["membership"] == tx.MLB and cls["domain"] in (tx.D_GAME, tx.D_PLAYER_GAME):
            for k, v in captured_by.items():
                if not v:
                    reasons.append("NOT_CAPTURED_BY_%s" % k.upper())

        spreads = sorted(o["spreads"])
        entry = {
            "seriesTicker": s, "title": e["title"], "discoveredVia": e["discoveredVia"],
            "membership": cls["membership"], "membershipReason": cls["membershipReason"],
            "domain": cls["domain"], "family": cls["family"], "scope": cls["scope"],
            "contractShape": cls["contractShape"], "edgelabFamily": cls["edgelabFamily"],
            "modelPath": cls["modelPath"], "modelStatus": cls["modelStatus"],
            "settlementTruth": cls["settlement"], "note": cls["note"],
            "observed": {
                "markets": len(o["markets"]), "events": len(o["events"]), "games": len(o["games"]),
                "distinctThresholds": sorted(o["thresholds"])[:60],
                "firstSeen": o["firstSeen"], "lastSeen": o["lastSeen"],
                "quotedRows": o["quotedRows"], "twoSidedRows": o["twoSided"],
                "medianSpreadCents": spreads[len(spreads) // 2] if spreads else None,
                "parseShare": round(parse_share, 4) if parse_share is not None else None,
            },
            "settlement": {
                "settled": st["settled"], "yesShare": round(st["yes"] / st["settled"], 4) if st["settled"] else None,
                "unresolved": st["unresolved"], "games": len(st["games"]),
                "firstDate": st["firstDate"], "lastDate": st["lastDate"],
            },
            "capturedBy": captured_by,
            "ladderRungs": rungs, "highestRung": rungs[-1],
            "researchStatus": _research_status(cls, rungs),
            "gaps": reasons,
        }
        out_series.append(entry)
        if reasons and cls["membership"] in (tx.MLB, tx.UNCLASSIFIED, tx.MIXED_COMBO):
            gaps.append({"seriesTicker": s, "highestRung": rungs[-1], "reasons": reasons})

    timeline = None
    if catalogue_history:
        first, last = {}, {}
        for day, ss in catalogue_history:
            for t in ss:
                first.setdefault(t, day)
                last[t] = day
        base_day, newest = catalogue_history[0][0], catalogue_history[-1][0]
        by_t = {e["seriesTicker"]: e for e in out_series}
        for t, e in by_t.items():
            e["firstSeenInCatalogue"] = first.get(t)
        timeline = {
            "firstCatalogue": base_day, "newestCatalogue": newest,
            "seriesInFirst": len(catalogue_history[0][1]), "seriesInNewest": len(catalogue_history[-1][1]),
            "addedSinceFirst": sorted(({"series": t, "firstSeen": d, "membership": by_t[t]["membership"] if t in by_t else None,
                                        "researchStatus": by_t[t]["researchStatus"] if t in by_t else None}
                                       for t, d in first.items() if d > base_day), key=lambda x: (x["firstSeen"], x["series"]),
                                      ),
            "droppedSinceFirst": sorted(t for t, d in last.items() if d < newest),
        }
    by_membership = defaultdict(int)
    for e in out_series:
        by_membership[e["membership"]] += 1
    return {
        "schema": "mlb_alpha_0003_registry_v1", "asOf": as_of,
        "counts": {"series": len(out_series), "byMembership": dict(by_membership),
                   "mlbGameLevel": sum(1 for e in out_series if e["membership"] == tx.MLB and e["domain"] in (tx.D_GAME, tx.D_PLAYER_GAME)),
                   "unclassified": sum(1 for e in out_series if e["membership"] == tx.UNCLASSIFIED)},
        "series": out_series, "gaps": gaps, "discoveryTimeline": timeline,
    }


def _research_status(cls, rungs):
    if cls["membership"] == tx.NOT_MLB:
        return "EXCLUDED_NOT_MLB"
    if cls["membership"] == tx.MLB_EXHIBITION:
        return "EXCLUDED_EXHIBITION_NOT_POOLED"
    if cls["membership"] == tx.MIXED_COMBO:
        return "COMBO_LEG_DECOMPOSITION_REQUIRED"
    if cls["membership"] == tx.UNCLASSIFIED:
        return "NEEDS_REVIEW"
    if cls["domain"] in (tx.D_AWARD, tx.D_LEADER, tx.D_SEASON, tx.D_SERIES, tx.D_EVENT, tx.D_CORPORATE):
        return "CATALOGUED_NO_GAME_PATH_NO_MODEL"
    if "SAMPLE_FLOOR_MET" in rungs:
        return "TESTABLE_SEE_RESULTS_NO_QUALIFIED_EDGE"
    if "SETTLEABLE" in rungs:
        return "SETTLEABLE_UNDER_TEST_FLOOR"
    if "PRICEABLE" in rungs:
        return "PRICEABLE_NO_SETTLEMENT_TRUTH"
    return "RESEARCH_GAP"


def render_markdown(registry):
    """Human-readable registry report (deterministic)."""
    L = ["# MLB market registry", "",
         "As of `%s`. Series: %d (%s)." % (registry["asOf"], registry["counts"]["series"],
                                          ", ".join("%s %d" % kv for kv in sorted(registry["counts"]["byMembership"].items())))]
    L += ["", "## Game-level MLB series (priced from game state)", "",
          "| Series | Family | Scope | Rung | Markets seen | Games | Settled | Settled games | Model | Gaps |",
          "|---|---|---|---|---:|---:|---:|---:|---|---|"]
    for e in registry["series"]:
        if e["membership"] == tx.MLB and e["domain"] in (tx.D_GAME, tx.D_PLAYER_GAME):
            L.append("| %s | %s | %s | %s | %d | %d | %d | %d | %s | %s |" % (
                e["seriesTicker"], e["family"], e["scope"], e["highestRung"], e["observed"]["markets"],
                e["observed"]["games"], e["settlement"]["settled"], e["settlement"]["games"],
                e["modelStatus"], "; ".join(e["gaps"]) or "-"))
    L += ["", "## Excluded: not MLB or not pooled", "", "| Series | Verdict | Evidence |", "|---|---|---|"]
    for e in registry["series"]:
        if e["membership"] in (tx.NOT_MLB, tx.MLB_EXHIBITION):
            L.append("| %s | %s | %s |" % (e["seriesTicker"], e["membership"], e["membershipReason"]))
    L += ["", "## MLB catalogue without a game path (futures, awards, series, events)", "",
          "| Series | Domain | Family |", "|---|---|---|"]
    for e in registry["series"]:
        if e["membership"] == tx.MLB and e["domain"] not in (tx.D_GAME, tx.D_PLAYER_GAME):
            L.append("| %s | %s | %s |" % (e["seriesTicker"], e["domain"], e["family"]))
    tl = registry.get("discoveryTimeline")
    if tl:
        L += ["", "## New series since %s (%d -> %d series)" % (tl["firstCatalogue"], tl["seriesInFirst"], tl["seriesInNewest"]), "",
              "| First seen | Series | Verdict | Research status |", "|---|---|---|---|"]
        for a in tl["addedSinceFirst"]:
            L.append("| %s | %s | %s | %s |" % (a["firstSeen"], a["series"], a["membership"], a["researchStatus"]))
    unc = [e for e in registry["series"] if e["membership"] == tx.UNCLASSIFIED]
    L += ["", "## Unclassified (needs review)", ""] + (["- %s: %s" % (e["seriesTicker"], e["title"]) for e in unc] or ["_none_"])
    return "\n".join(L) + "\n"
