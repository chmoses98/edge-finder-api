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
from lib import kalshi_mlb_contract_parser as kmcp                 # noqa: E402

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

    # W1-C final correction. Exclusivity is necessary and NOT sufficient: a
    # ticker from the wrong doubleheader leg, claimed by one game only, has no
    # collision to find. The binding is what catches it -- the event the
    # contract encodes must be the event this physical game resolved to.
    binding_violations = []
    price_identity_splits = []
    # W1-C final correction, second half. The binding proves the contract
    # belongs to this game; it says nothing about whether the row's claimed
    # selection/threshold/direction/side are that contract's. Re-derived here
    # from the row's own fields and the exchange's own parsed contract, rather
    # than read back off the ledger's verdict -- a rehearsal that only reads
    # the stamp cannot notice the stamp and the payload diverging.
    claim_mismatches = []
    unverified_claim_actionable = []
    semantics_checked = 0
    for r in actionable:
        parsed = kmcp.parse_contract_condition(r.get("marketTicker"))
        if r.get("contractClaimVerified") is not True:
            unverified_claim_actionable.append(
                {"gamePk": r["_gamePk"], "market": r.get("market"),
                 "marketTicker": r.get("marketTicker"),
                 "contractClaimVerified": r.get("contractClaimVerified"),
                 "identityStatus": r.get("identityStatus")})
        if parsed["parseStatus"] != kmcp.PARSE_STATUS_PARSED:
            continue
        claim = mi.normalize_ledger_claim(
            r.get("marketFamily"), selection=r.get("selection"),
            direction=r.get("direction"), threshold=r.get("threshold"),
            side=r.get("contractSide"))
        disagreements = mi.compare_contract_claim(parsed, claim)
        semantics_checked += 1
        if disagreements:
            claim_mismatches.append(
                {"gamePk": r["_gamePk"], "market": r.get("market"),
                 "marketTicker": r.get("marketTicker"),
                 "disagreements": disagreements})

    for r in actionable:
        resolved = r.get("resolvedEventTickerSuffix")
        carried = r.get("contractEventTickerSuffix") or mi.event_suffix_of(
            r.get("marketTicker"))
        if not resolved or not carried or resolved != carried:
            binding_violations.append(
                {"gamePk": r["_gamePk"], "market": r.get("market"),
                 "resolvedEventTickerSuffix": resolved,
                 "contractEventTickerSuffix": carried,
                 "marketTicker": r.get("marketTicker")})
        # The contract that was PRICED must be the contract that was
        # IDENTIFIED. Two ticker fields that can disagree are two contracts.
        priced = r.get("executablePriceMarketTicker")
        if priced and r.get("marketTicker") and priced != r.get("marketTicker"):
            price_identity_splits.append(
                {"gamePk": r["_gamePk"], "market": r.get("market"),
                 "identityTicker": r.get("marketTicker"),
                 "pricedTicker": priced})

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
        "eventGameBindingViolations": binding_violations,
        "priceIdentityTickerSplits": price_identity_splits,
        "rowsCarryingAResolvedEvent": sum(
            1 for r in rows if r.get("resolvedEventTickerSuffix")),
        # The contract-claim half, reported as its own count so a board that
        # happens to list no totals cannot make this look proven when it is
        # merely unexercised.
        "contractClaimMismatchViolations": claim_mismatches,
        "unverifiedContractClaimActionableRows": unverified_claim_actionable,
        "actionableRowsWithSemanticsReChecked": semantics_checked,
        "rowsWithParsedContractCondition": sum(
            1 for r in rows if r.get("contractCondition")),
        "contractConditionDistribution": dict(collections.Counter(
            r.get("contractCondition") or "(none)" for r in rows)),
        # Non-vacuity: a threshold family actually present on this board. A
        # moneyline-only slate proves the selection check and nothing about the
        # normalization, so the two are counted apart.
        "actionableRowsCarryingAStrike": sum(
            1 for r in actionable
            if r.get("parsedMinimumInclusive") is not None
            and r.get("thresholdConvention")),
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
    into the current working directory -- its one and only write. I hit that
    while building this and overwrote a live slate.

    A path check cannot prevent it: the CI rehearsal runs from inside a COPY of
    the checkout, so the script's own root IS the working directory and no
    inspection can tell a copy from the original. My first attempt asserted
    exactly that and failed the rehearsal it was meant to protect.

    So the guarantee is made rather than guessed: `data/slate.json` is read
    before the import and written back byte-for-byte afterwards if it changed.
    That holds in a scratch copy and in a real checkout alike, and it needs the
    caller to remember nothing.
    """
    slate_on_disk = os.path.join(os.getcwd(), "data", "slate.json")
    before = None
    if os.path.exists(slate_on_disk):
        with open(slate_on_disk, "rb") as handle:
            before = handle.read()

    try:
        from scripts.merge_odds import find_registry_entry          # noqa: E402
    finally:
        if before is not None and os.path.exists(slate_on_disk):
            with open(slate_on_disk, "rb") as handle:
                after = handle.read()
            if after != before:
                with open(slate_on_disk, "wb") as handle:
                    handle.write(before)
                print("restored data/slate.json (rewritten by the merge_odds "
                      "import, which has no __main__ guard)")

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


def _alignment(slate_path, registry_path):
    with open(slate_path) as handle:
        slate_date = (json.load(handle) or {}).get("date")
    with open(registry_path) as handle:
        doc = json.load(handle)
    registry_date = doc.get("date")
    return {
        "slateDate": slate_date,
        "registryDate": registry_date,
        "sameDate": bool(slate_date) and slate_date == registry_date,
        "note": ("the binding is exercised POSITIVELY" if slate_date == registry_date
                 else "different dates: every row correctly refuses, so this run "
                      "proves fail-closed behaviour but NOT a positive binding"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="W1-C live identity rehearsal evidence (read-only)")
    parser.add_argument("--ledger", required=True,
                        help="recommendations.json produced by the live run")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--slate", required=True)
    parser.add_argument("--work-dir", required=True,
                        help="the checkout (or copy) to run inside; it must "
                             "contain data/, because the reorder probe imports "
                             "merge_odds and that resolves paths from the cwd")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    # The reorder probe imports scripts.merge_odds, which resolves its paths
    # from the cwd, so the run has to happen inside a tree that has a data/.
    # The write that import performs is undone by reorder_invariance_report --
    # see its docstring for why a path check cannot be the guard here.
    work_dir = os.path.realpath(args.work_dir)
    if not os.path.isdir(os.path.join(work_dir, "data")):
        parser.error("--work-dir %s has no data/ directory" % work_dir)
    os.chdir(work_dir)

    games, rows = _load_ledger(args.ledger)
    payload = {
        "reportVersion": "w1c-live-identity-evidence-1",
        "modelDrivenRealMoneyAuthority": "OFF",
        "recommendationAuthorityExercised": False,
        "wagersWritten": 0,
        # A green run proves REFUSAL, not binding, unless the committed slate
        # and the live registry describe the same date. In CI the registry is
        # rebuilt for today while data/slate.json is whatever was last
        # committed, so the binding is usually exercised only negatively. That
        # is a real limit of this rehearsal and it is reported rather than left
        # for a reader to mistake a green for a positive proof.
        "slateRegistryAlignment": _alignment(args.slate, args.registry),
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
    if ci["eventGameBindingViolations"]:
        violations.append("an actionable row's contract belongs to a different "
                          "Kalshi event than its physical game resolved to")
    if ci["priceIdentityTickerSplits"]:
        violations.append("a row was priced from one contract and identified "
                          "as another")
    if ci["contractClaimMismatchViolations"]:
        violations.append("an actionable row claims selection/threshold/"
                          "direction/side its own Kalshi contract contradicts")
    if ci["unverifiedContractClaimActionableRows"]:
        violations.append("an actionable row's contract claim was never "
                          "checked against the exchange")
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
    print("  contract-claim mismatches   %s (re-checked on %s actionable row(s); "
          "%s carrying a strike)"
          % (len(ci["contractClaimMismatchViolations"]),
             ci["actionableRowsWithSemanticsReChecked"],
             ci["actionableRowsCarryingAStrike"]))
    if not ci["actionableRowsCarryingAStrike"]:
        print("    (no actionable total/team-total/run-line row on this board --")
        print("     the threshold NORMALIZATION is not exercised live here; the")
        print("     frozen archived fixtures cover it)")
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
    print("  event->game binding         %s violation(s) (%s rows carry a "
          "resolved event)" % (len(ci["eventGameBindingViolations"]),
                               ci["rowsCarryingAResolvedEvent"]))
    al = payload["slateRegistryAlignment"]
    print("  slate vs registry date      %s vs %s -- %s"
          % (al["slateDate"], al["registryDate"], al["note"]))
    print("  priced vs identified ticker %s split(s)"
          % len(ci["priceIdentityTickerSplits"]))
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
