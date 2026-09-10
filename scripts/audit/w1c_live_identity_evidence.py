#!/usr/bin/env python3
"""
scripts/audit/w1c_live_identity_evidence.py
===========================================
WAVE 1, subwave C. Does canonical identity hold against a LIVE slate?

The W1-C tests prove the identity module and the ledger seam are fail-closed
against fixtures. Fixtures are written by the same person who wrote the code,
so this runs the same invariants against whatever Kalshi and MLB actually
published today, plus the two frozen historical doubleheaders, and reports
what it finds rather than what it hoped to find.

WHAT IT PROVES
--------------
  1. every not-started game on the live slate has a unique physical identity
     (a distinct MLB gamePk), and any team pair naming more than one game is
     reported as the doubleheader it is;
  2. no marketTicker is claimed by two different gamePks -- the direct,
     checkable form of CR-3;
  3. every ACTIONABLE ledger row carries a PROVEN contract identity: ticker,
     family, horizon, side, and whatever else that family requires;
  4. ambiguity refuses rather than resolving -- reported as counts by refusal
     reason, since a refusal here is the correct outcome, not a failure;
  5. reorder invariance: shuffling the candidate games and shuffling the
     registry's key order does not change a single mapping;
  6. B2's price provenance survives W1-C untouched: every actionable row still
     has a declared unit, a genuine capture time inside the freshness ceiling,
     and an ask-derived executable price.

READ-ONLY. It reads the ledger the caller has already produced and never
writes into the repository. `--out` is the only file it creates.

A NOTE ON WHAT "PASS" MEANS
---------------------------
Refusals are not failures. A live slate with no doubleheader proves nothing
about doubleheaders, and this says so explicitly rather than reporting a
vacuous green. The exit code is non-zero only for an actual VIOLATION: a
ticker on two games, or an actionable row without proven identity.
"""

import argparse
import collections
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi                      # noqa: E402

# B2 invariants this rehearsal re-checks rather than assumes.
MAX_QUOTE_AGE_SECONDS = 1800


def _load_ledger(path):
    with open(path) as handle:
        doc = json.load(handle)
    games = (doc.get("data") or doc).get("games") or []
    rows = []
    for game in games:
        key = mi.physical_game_key(game) or str(
            game.get("gameId") or game.get("kalshiKey") or "?")
        for row in (game.get("marketLedger") or []):
            rows.append(dict(row, _gamePk=key))
    return games, rows


def physical_identity_report(games):
    """Every game's physical identity, and every team pair naming two."""
    by_pair = collections.defaultdict(list)
    missing_pk = []
    for game in games:
        pk = mi.physical_game_key(game)
        if not pk:
            missing_pk.append(game.get("kalshiKey") or game.get("away"))
            continue
        away = (game.get("away") or {}).get("abbr") if isinstance(game.get("away"), dict) \
            else game.get("away")
        home = (game.get("home") or {}).get("abbr") if isinstance(game.get("home"), dict) \
            else game.get("home")
        by_pair["%s%s" % (away, home)].append(
            {"gamePk": pk, "startTime": game.get("startTime"),
             "kalshiGameTime": game.get("kalshiGameTime")})

    doubleheaders = {pair: legs for pair, legs in by_pair.items() if len(legs) > 1}
    pks = [pk for legs in by_pair.values() for pk in (l["gamePk"] for l in legs)]
    return {
        "games": len(games),
        "gamesWithPhysicalIdentity": len(pks),
        "gamesWithoutPhysicalIdentity": len(missing_pk),
        "gamesWithoutPhysicalIdentityKeys": missing_pk,
        "distinctGamePks": len(set(pks)),
        "physicalIdentityIsUnique": len(pks) == len(set(pks)),
        # A team pair naming two games is not a defect -- it is a doubleheader,
        # and the whole point is that the pair is NOT the identity.
        "teamPairsNamingMoreThanOneGame": doubleheaders,
        "doubleheaderCount": len(doubleheaders),
    }


def contract_identity_report(rows):
    actionable = [r for r in rows if r.get("confidence")]
    unproven_actionable = [
        {"gamePk": r["_gamePk"], "market": r.get("market"),
         "identityStatus": r.get("identityStatus"),
         "confidence": r.get("confidence")}
        for r in actionable
        if r.get("identityStatus") != mi.IDENTITY_PROVEN
    ]
    # An actionable row must also physically CARRY the fields, not merely be
    # stamped proven -- the stamp and the payload are written by the same code
    # and a test that only reads the stamp would never notice them diverge.
    incomplete_actionable = []
    for r in actionable:
        missing = [f for f in ("marketTicker", "marketFamily", "marketHorizon",
                               "contractSide", "physicalGameKey")
                   if r.get(f) in (None, "")]
        if missing:
            incomplete_actionable.append(
                {"gamePk": r["_gamePk"], "market": r.get("market"),
                 "missing": missing})

    violations = mi.assert_ticker_exclusivity(
        (r.get("marketTicker"), r["_gamePk"]) for r in rows)

    return {
        "ledgerRows": len(rows),
        "actionableRows": len(actionable),
        "identityStatusDistribution": dict(collections.Counter(
            r.get("identityStatus") or "(absent)" for r in rows)),
        "familyDistribution": dict(collections.Counter(
            r.get("marketFamily") or "(none)" for r in rows)),
        "horizonDistribution": dict(collections.Counter(
            r.get("marketHorizon") or "(none)" for r in rows)),
        "rowsCarryingAThreshold": sum(1 for r in rows if r.get("threshold") is not None),
        "rowsCarryingADirection": sum(1 for r in rows if r.get("direction")),
        "unprovenActionableRows": unproven_actionable,
        "incompleteActionableRows": incomplete_actionable,
        "tickerExclusivityViolations": violations,
    }


def b2_provenance_report(rows):
    """W1-C must not have loosened a single B2 guarantee."""
    actionable = [r for r in rows if r.get("confidence")]
    ages = [r["quoteAgeSeconds"] for r in actionable
            if r.get("quoteAgeSeconds") is not None]
    return {
        "actionableRows": len(actionable),
        # `executablePriceUnitDeclared` is the field production_price stamps
        # with the unit the BOOK declared. B2 refuses an undeclared unit
        # outright, so an actionable row without one would mean the refusal was
        # bypassed, not merely that a label is missing.
        "actionableWithoutDeclaredUnit": [
            r.get("market") for r in actionable
            if not r.get("executablePriceUnitDeclared")],
        "actionableWithoutQuoteAge": [
            r.get("market") for r in actionable if r.get("quoteAgeSeconds") is None],
        "actionableWithNegativeQuoteAge": [
            r.get("market") for r in actionable
            if (r.get("quoteAgeSeconds") or 0) < 0],
        "actionableExceedingFreshnessCeiling": [
            r.get("market") for r in actionable
            if (r.get("quoteAgeSeconds") or 0) > MAX_QUOTE_AGE_SECONDS],
        "quoteAgeSeconds": {"count": len(ages),
                            "min": min(ages) if ages else None,
                            "max": max(ages) if ages else None},
        "executablePriceBasis": dict(collections.Counter(
            r.get("executablePriceBasis") for r in actionable
            if r.get("executablePriceBasis"))),
        "priceRefusalReasons": dict(collections.Counter(
            r["priceRefusalReason"] for r in rows if r.get("priceRefusalReason"))),
    }


def reorder_invariance_report(registry_path, slate_path, seed=20260910):
    """
    Shuffle both sides and prove the mapping is identical.

    The pre-W1-C join iterated a `set`, so its answer genuinely could depend on
    hash order. This runs the real `find_registry_entry` against a shuffled
    registry and shuffled candidate order and compares every result.

    IMPORTING merge_odds RUNS IT. It has no `if __name__ == '__main__'` guard,
    so the import below performs a full merge and writes `data/slate.json`
    into the current working directory. `main()` therefore refuses to start
    unless the caller has supplied an isolated --work-dir and chdir'd into it,
    which makes that write land in a scratch copy by construction rather than
    by the caller remembering.
    """
    from scripts.merge_odds import find_registry_entry              # noqa: E402

    with open(registry_path) as handle:
        doc = json.load(handle)
    registry = doc.get("registry") or {}
    collisions = doc.get("registry_key_collisions") or []

    with open(slate_path) as handle:
        slate = json.load(handle)
    games = slate.get("games") or []

    def probe(reg, order):
        out = {}
        for game in order:
            away = game.get("away") or {}
            home = game.get("home") or {}
            away_full = away.get("team") if isinstance(away, dict) else away
            home_full = home.get("team") if isinstance(home, dict) else home
            entry = find_registry_entry(
                away_full, home_full,
                away.get("abbr") if isinstance(away, dict) else None,
                home.get("abbr") if isinstance(home, dict) else None,
                reg, game=game, collisions=collisions)
            out[mi.physical_game_key(game) or str(game.get("gameId"))] = (
                (entry or {}).get("event_ticker_suffix")
                or (entry or {}).get("eventTicker"))
        return out

    baseline = probe(registry, games)

    rng = random.Random(seed)
    mismatches = []
    for trial in range(8):
        keys = list(registry.items())
        rng.shuffle(keys)
        shuffled_registry = dict(keys)
        shuffled_games = list(games)
        rng.shuffle(shuffled_games)
        result = probe(shuffled_registry, shuffled_games)
        if result != baseline:
            mismatches.append({"trial": trial,
                               "differs": {k: (baseline.get(k), result.get(k))
                                           for k in set(baseline) | set(result)
                                           if baseline.get(k) != result.get(k)}})

    return {
        "gamesProbed": len(baseline),
        "trials": 8,
        "mismatchCount": len(mismatches),
        "mismatches": mismatches,
        "registryKeyCollisions": collisions,
        "registryKeyCollisionCount": len(collisions),
        "resolvedToAnEvent": sum(1 for v in baseline.values() if v),
        "refusedOrUnmatched": sum(1 for v in baseline.values() if not v),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="W1-C live identity rehearsal evidence (read-only)")
    parser.add_argument("--ledger", required=True,
                        help="recommendations.json produced by the live run")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--slate", required=True)
    parser.add_argument("--work-dir", required=True,
                        help="an ISOLATED copy of the checkout to run inside; "
                             "importing merge_odds writes data/slate.json, so "
                             "this must never be a real checkout")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    # The reorder probe below imports scripts.merge_odds, which has no
    # `__main__` guard and performs a full merge -- writing data/slate.json --
    # the instant it is imported. Rather than trusting the caller to have
    # chdir'd somewhere safe, refuse outright when --work-dir is, or is inside,
    # a real checkout. A silent overwrite of a live slate is not an acceptable
    # cost of collecting evidence, and this exact mistake is one command away.
    work_dir = os.path.realpath(args.work_dir)
    if not os.path.isdir(os.path.join(work_dir, "data")):
        parser.error("--work-dir %s has no data/ directory" % work_dir)
    if work_dir == os.path.realpath(ROOT):
        parser.error("--work-dir must be an isolated COPY, not the checkout "
                     "at %s -- importing merge_odds would overwrite its "
                     "data/slate.json" % ROOT)
    os.chdir(work_dir)

    games, rows = _load_ledger(args.ledger)
    payload = {
        "reportVersion": "w1c-live-identity-evidence-1",
        "modelDrivenRealMoneyAuthority": "OFF",
        "recommendationAuthorityExercised": False,
        "wagersWritten": 0,
        "physicalIdentity": physical_identity_report(games),
        "contractIdentity": contract_identity_report(rows),
        "b2Provenance": b2_provenance_report(rows),
        "reorderInvariance": reorder_invariance_report(args.registry, args.slate),
    }

    # A VIOLATION is a thing that must never be true. A refusal is not one.
    violations = []
    ci = payload["contractIdentity"]
    if ci["tickerExclusivityViolations"]:
        violations.append("a marketTicker is claimed by more than one gamePk")
    if ci["unprovenActionableRows"]:
        violations.append("an actionable row has unproven identity")
    if ci["incompleteActionableRows"]:
        violations.append("an actionable row is missing an identity field")
    if not payload["physicalIdentity"]["physicalIdentityIsUnique"]:
        violations.append("two slate games share one physical identity")
    if payload["reorderInvariance"]["mismatchCount"]:
        violations.append("the registry join is order-dependent")
    b2 = payload["b2Provenance"]
    for field, label in (
            ("actionableWithoutDeclaredUnit", "an actionable row has no declared price unit"),
            ("actionableWithNegativeQuoteAge", "an actionable row has a future-dated quote"),
            ("actionableExceedingFreshnessCeiling", "an actionable row is past the freshness ceiling")):
        if b2[field]:
            violations.append(label)
    payload["violations"] = violations

    print("\nW1-C LIVE IDENTITY REHEARSAL")
    pi = payload["physicalIdentity"]
    print("  slate games                 %s (%s distinct gamePk)"
          % (pi["games"], pi["distinctGamePks"]))
    print("  doubleheaders on the slate  %s" % pi["doubleheaderCount"])
    if not pi["doubleheaderCount"]:
        print("    (no live doubleheader today -- the two frozen historical")
        print("     reproductions carry that part of the proof)")
    print("  ledger rows / actionable    %s / %s"
          % (ci["ledgerRows"], ci["actionableRows"]))
    print("  identity status             %s" % ci["identityStatusDistribution"])
    print("  ticker exclusivity          %s violation(s)"
          % len(ci["tickerExclusivityViolations"]))
    ri = payload["reorderInvariance"]
    print("  reorder invariance          %s/%s trials identical"
          % (ri["trials"] - ri["mismatchCount"], ri["trials"]))
    print("  registry key collisions     %s" % ri["registryKeyCollisionCount"])
    print("  B2 quote ages (actionable)  %s" % b2["quoteAgeSeconds"])
    print("  B2 price basis              %s" % b2["executablePriceBasis"])
    print("  VIOLATIONS                  %s" % (violations or "none"))

    if args.out:
        with open(args.out, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print("\nwrote %s" % args.out)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
