#!/usr/bin/env python3
"""
scripts/verify_handicap_runtime.py
==================================
Walk the "RUN MLB" consumer path exactly as a fresh chat session would,
and report what it cost and what it saw.

WHY THIS EXISTS
---------------
`scripts/build_handicap_runtime.py` verifies the runtime against the card
at BUILD time. That proves the artifact is correct. It does not prove the
artifact is USABLE, which is a different claim and the one the whole
change is for: can a consumer that never opens
`data/handicapping_card/<date>.json` still see every Kalshi market for
every betting-eligible game?

So this reads the runtime the way the contract in `RUN_THE_SLATE.md`
says to:

    1. data/handicap_runtime/latest.json          (the date pointer)
    2. data/handicap_runtime/<date>/manifest.json (which games, how many)
    3. the bundles for the ELIGIBLE games only

and counts every byte it opened along the way. It does not read the
archival card, `MODEL_CORE.md`, `RULES.md`, `SLATE_WORKFLOW.md` or
`DATA_SOURCES.md`, and it fails if the manifest ever asks it to.

THE AUDIT STEP IS NOT PART OF THE CONSUMER PATH
-----------------------------------------------
With `--audit-against-card` it ALSO opens the archival card, once, and
proves ticker-set equality per game. That read is reported separately and
is explicitly not counted as consumer cost -- it is the proof, not the
workflow. A consumer never does it.

Usage:
    python3 scripts/verify_handicap_runtime.py
    python3 scripts/verify_handicap_runtime.py --date 2026-09-18 --audit-against-card
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import handicap_runtime as runtime  # noqa: E402

#: Documents the old contract made a consumer ingest before it could look
#: at a price. Measured, not asserted -- the saving is the sum of these.
LEGACY_STARTUP_DOCS = (
    "HANDICAPPING_PLAYBOOK.md",
    "PLAYBOOK_LESSONS.md",
    "RULES.md",
    "MODEL_CORE.md",
    "SLATE_WORKFLOW.md",
    "DATA_SOURCES.md",
)

#: What the new contract requires. The methodology, and nothing else.
RUNTIME_STARTUP_DOCS = ("HANDICAPPING_PLAYBOOK.md", "PLAYBOOK_LESSONS.md")


class Reader:
    """Every file the consumer opens, and what it cost."""

    def __init__(self):
        self.files = []

    def read(self, path):
        with open(path, "rb") as handle:
            blob = handle.read()
        # Recorded repo-relative: the cost report is about which artifacts a
        # consumer opens, and an absolute runner path obscures that.
        label = os.path.relpath(os.path.abspath(path), ROOT).replace(os.sep, "/")
        self.files.append((label, len(blob)))
        return blob

    def json(self, path):
        return json.loads(self.read(path).decode("utf-8"))

    @property
    def total(self):
        return sum(size for _path, size in self.files)


def doc_bytes(names):
    total = 0
    for name in names:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            total += os.path.getsize(path)
    return total


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None,
                        help="skip the pointer and go straight to this date")
    parser.add_argument("--root", default=runtime.RUNTIME_ROOT)
    parser.add_argument("--audit-against-card", action="store_true",
                        help="ALSO open the archival card and prove ticker-set "
                             "equality per game. Not part of the consumer path.")
    parser.add_argument("--include-research", action="store_true",
                        help="also load the research-only bundles (the early-value "
                             "surface). A real-money run does not.")
    args = parser.parse_args(argv)

    started = time.time()
    reader = Reader()
    problems = []

    # ── step 1: the pointer ──────────────────────────────────────────
    date = args.date
    if date is None:
        pointer_path = os.path.join(args.root, "latest.json")
        if not os.path.exists(pointer_path):
            print(f"ERROR: no runtime pointer at {pointer_path}", file=sys.stderr)
            return 1
        pointer = reader.json(pointer_path)
        date = pointer["date"]
        print(f"[1] pointer  -> {date} "
              f"({pointer['bettingEligibleGames']} eligible of {pointer['gamesTotal']}, "
              f"sizing: {pointer['dollarSizingVerdict']})")

    # ── step 2: the manifest ─────────────────────────────────────────
    manifest_path = os.path.join(args.root, date, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"ERROR: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = reader.json(manifest_path)
    counts = manifest["counts"]
    print(f"[2] manifest -> {counts['bettingEligibleGames']} betting-eligible of "
          f"{counts['gamesTotal']} games; {counts['eligibleMarketsTotal']} markets to "
          f"inspect; silentRemainder={counts['silentRemainderTotal']}")

    if not manifest["completeness"]["holds"]:
        problems.append("manifest.completeness.holds is false")
    if not manifest["completeness"]["bundleRowsEqualCardMarkets"]:
        problems.append("manifest counts do not reconcile with the card's")

    # The contract says the consumer must not need the archival card.
    flow = " ".join(manifest.get("consumerFlow") or [])
    if "handicapping card is NOT required" not in flow:
        problems.append("the manifest's consumer flow no longer says the card is "
                        "unnecessary")

    # ── step 3: eligibility, decided from the manifest alone ─────────
    eligible = [row for row in manifest["games"] if row["bettingEligible"]]
    started_games = [row for row in manifest["games"] if row.get("started")]
    unconfirmed = [row for row in manifest["games"]
                   if not (row["lineups"] or {}).get("bothConfirmed")]
    for row in eligible:
        if row.get("started"):
            problems.append(f"{row['matchup']}: started AND betting-eligible")
        if not (row["lineups"] or {}).get("bothConfirmed"):
            problems.append(f"{row['matchup']}: eligible without both official lineups")
    print(f"[3] eligible -> {[row['matchup'] for row in eligible]}")
    print(f"    excluded  -> {len(started_games)} started, "
          f"{len(unconfirmed)} without both official lineups confirmed")

    # ── step 4: load ONLY those bundles ──────────────────────────────
    wanted = eligible + ([row for row in manifest["games"] if not row["bettingEligible"]]
                         if args.include_research else [])
    seen_tickers = {}
    families = {}
    for row in wanted:
        bundle = reader.json(os.path.join(ROOT, row["bundle"]))
        markets = runtime.expand_market_table(bundle["markets"])
        tickers = [m["ticker"] for m in markets]
        seen_tickers[row["gameId"]] = tickers

        if len(tickers) != row["marketsToInspect"]:
            problems.append(
                f"{row['matchup']}: manifest promised {row['marketsToInspect']} markets, "
                f"the bundle holds {len(tickers)}")
        if len(set(tickers)) != len(tickers):
            problems.append(f"{row['matchup']}: the bundle repeats a ticker")
        accounting = bundle.get("contractAccounting") or {}
        if accounting.get("silentRemainderCount"):
            problems.append(f"{row['matchup']}: silentRemainderCount is not 0")
        if bundle["eligibility"]["bettingEligible"] != row["bettingEligible"]:
            problems.append(f"{row['matchup']}: bundle and manifest disagree on eligibility")
        if not bundle["context"].get("available"):
            problems.append(f"{row['matchup']}: the bundle carries no game context")
        for family, count in (bundle.get("marketsByFamily") or {}).items():
            families[family] = families.get(family, 0) + count

    total_markets = sum(len(t) for t in seen_tickers.values())
    print(f"[4] loaded   -> {len(wanted)} bundle(s), {total_markets} market rows")
    print(f"    families -> {dict(sorted(families.items()))}")

    # Pitcher and hitter props are explicitly in scope and explicitly not
    # settled automatically (issue #43). Their ABSENCE would be the bug.
    #
    # ONLY WHEN SOMETHING WAS ACTUALLY LOADED. This check exists to catch
    # the universe being FILTERED DOWN -- props present on the card but
    # missing from what a consumer sees. A slate with zero betting-eligible
    # games loads zero bundles, so there is nothing that could have been
    # filtered, and reporting "no prop families reached the consumer" there
    # states a defect that did not happen.
    #
    # It is not a rare corner. Every night once the last game starts, the
    # committed runtime correctly has bettingEligibleGames: 0, and on
    # 2026-09-21 the slate could not build at all (post-fetch gate:
    # "WSH@DET: BOTH starters have no xFIP/seasonFIP"), so main carried a
    # zero-eligible pointer for eighteen hours. For that whole window this
    # verifier called the committed runtime broken and
    # test_the_committed_runtime_serves_the_real_run_mlb_consumer_path was
    # red on main, blocking every unrelated merge.
    prop_families = sorted(f for f in families if f.startswith(("pitcher_", "hitter_")))
    if wanted and not prop_families:
        problems.append("no pitcher or hitter prop families reached the consumer")
    print(f"    props    -> {prop_families}")
    if not wanted:
        print("    (no betting-eligible games in this runtime, so no bundle was "
              "loaded and there is no market universe to check for filtering)")

    # ── step 5: the consumer never opened the card ───────────────────
    opened = {path for path, _size in reader.files}
    for forbidden in ("data/handicapping_card/",):
        if any(forbidden in path for path in opened):
            problems.append(f"the consumer path opened {forbidden}")

    # ── the audit (NOT the consumer path) ────────────────────────────
    card_bytes = os.path.getsize(
        os.path.join(ROOT, "data", "handicapping_card", f"{date}.json")) \
        if os.path.exists(os.path.join(ROOT, "data", "handicapping_card", f"{date}.json")) \
        else 0
    if args.audit_against_card:
        if not card_bytes:
            problems.append("no archival card to audit against")
        else:
            with open(os.path.join(ROOT, "data", "handicapping_card", f"{date}.json"),
                      encoding="utf-8") as handle:
                card = json.load(handle)
            card_games = {g["gameId"]: g for g in
                          card["bettingEligibleGames"] + card["researchOnlyGames"]}
            for row in wanted:
                want = sorted(m["ticker"] for m in card_games[row["gameId"]]["markets"])
                got = sorted(seen_tickers[row["gameId"]])
                if want != got:
                    missing = sorted(set(want) - set(got))
                    extra = sorted(set(got) - set(want))
                    problems.append(
                        f"{row['matchup']}: the consumer saw a different market set than "
                        f"the card holds (missing {len(missing)}, extra {len(extra)})")
            print(f"[audit] ticker sets match the archival card for all "
                  f"{len(wanted)} loaded game(s)")
            if not args.include_research:
                print(f"[audit] the card also holds "
                      f"{card['counts']['researchOnlyMarketsTotal']} research-only "
                      "markets on games that may not be bet; the consumer correctly "
                      "did not load them")

    # ── the bill ─────────────────────────────────────────────────────
    elapsed = time.time() - started
    methodology = doc_bytes(RUNTIME_STARTUP_DOCS)
    legacy_docs = doc_bytes(LEGACY_STARTUP_DOCS)
    print()
    print("  CONSUMER COST")
    for path, size in reader.files:
        print(f"    {size:>9,} B  {path}")
    print(f"    {reader.total:>9,} B  TOTAL runtime bytes read")
    print(f"    {methodology:>9,} B  + methodology ({', '.join(RUNTIME_STARTUP_DOCS)})")
    print(f"    {reader.total + methodology:>9,} B  = EVERYTHING a RUN MLB consumer reads")
    print()
    print("  THE OLD PATH, FOR COMPARISON")
    print(f"    {card_bytes:>9,} B  data/handicapping_card/{date}.json")
    print(f"    {legacy_docs:>9,} B  + {len(LEGACY_STARTUP_DOCS)} governance documents")
    print(f"    {card_bytes + legacy_docs:>9,} B  = what the previous contract required")
    if card_bytes:
        print(f"    {(card_bytes + legacy_docs) / max(reader.total + methodology, 1):>9.1f}x "
              " reduction")
    print(f"\n  walked in {elapsed:.3f}s")

    if problems:
        print()
        print("FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("\n  OK: every betting-eligible game's complete market universe was reachable "
          "without opening the archival card.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
