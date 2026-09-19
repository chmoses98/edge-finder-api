#!/usr/bin/env python3
"""
scripts/build_handicap_runtime.py
=================================
Build the COMPACT HANDICAPPER SERVING LAYER for one date.

    data/handicap_runtime/<YYYY-MM-DD>/manifest.json
    data/handicap_runtime/<YYYY-MM-DD>/games/<gameId>.json
    data/handicap_runtime/<YYYY-MM-DD>/games/<gameId>.json.gz

WHAT THIS IS FOR
----------------
`data/handicapping_card/<date>.json` is the archive and stays exactly as
it is -- on 2026-09-18 that is 8.6 MB covering 4,247 attributable Kalshi
contracts, and none of that coverage is reduced by anything here.

The archive is not a serving layer. A fresh chat handicapper had to
ingest the entire card plus six governance documents before it could
price a single contract. This builds the small thing it reads instead:
a manifest that answers "which games may I bet and how many markets must
I inspect" in a few kilobytes, and one compact bundle per game holding
EVERY market for that game plus the evidence needed to handicap it.

WHAT IT DOES NOT DO
-------------------
It does not filter the universe. Not by edge, not by production-model
support, not by settlement support, not by family. Pitcher and hitter
props are in the bundles exactly as they are on the card. The only thing
that changes is representation: game facts normalised out of every market
row, a columnar table instead of 4,247 repeated key sets, and integer
legends for the low-cardinality strings.

It is a PROJECTION of the card, built by importing the card builder
itself -- so there is no second market-discovery implementation here to
drift from the first.

BUILD ORDER
-----------
    scripts/build_handicapping_card.py   (unchanged; writes the archive)
    scripts/build_handicap_runtime.py    (this; projects it)

Usage:
    python3 scripts/build_handicap_runtime.py --date 2026-09-18
    python3 scripts/build_handicap_runtime.py --card data/handicapping_card/2026-09-18.json
    python3 scripts/build_handicap_runtime.py --date 2026-09-18 --print-sizes
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import handicap_runtime as runtime  # noqa: E402


def load_card_builder():
    """
    The card builder, imported as a module.

    `scripts/` is not a package, so this is the same `importlib`
    approach the card's own tests use. Importing it -- rather than
    re-implementing its loaders -- is what keeps this layer downstream
    of the canonical slate/snapshot resolution instead of beside it.
    """
    spec = importlib.util.spec_from_file_location(
        "build_handicapping_card",
        os.path.join(ROOT, "scripts", "build_handicapping_card.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_rules():
    try:
        return load_json(os.path.join(ROOT, "config", "rules.json"))
    except (OSError, ValueError):
        return {}


def slate_games_by_id(slate):
    """Every slate game keyed by gameId, for the per-game context."""
    out = {}
    for game in (slate or {}).get("games") or []:
        game_id = game.get("gameId")
        if game_id is not None:
            out[game_id] = game
    return out


def build(card, slate, *, rules=None, root=runtime.RUNTIME_ROOT, gzip_bundles=True):
    """
    Project one card (+ its slate, for game context) into the runtime
    tree. Returns ``(manifest, manifest_path, problems)``; a non-empty
    `problems` means NOTHING was written.
    """
    slate_games = slate_games_by_id(slate)
    date_root = os.path.join(root, card["date"]).replace(os.sep, "/")
    manifest_path = f"{date_root}/manifest.json"

    bundles = {}
    for game_block in card["bettingEligibleGames"] + card["researchOnlyGames"]:
        game_id = game_block["gameId"]
        bundles[game_id] = (
            game_block,
            runtime.build_bundle(
                card, game_block, slate_games.get(game_id), manifest_path=manifest_path),
        )

    # VERIFY BEFORE WRITING, against in-memory bundles. A runtime tree
    # that lost a contract must never reach the disk, because the file
    # that is there is the one a chat will trust.
    provisional_rows = [
        runtime.manifest_game_row(block, f"{date_root}/games/{game_id}.json")
        for game_id, (block, _bundle) in bundles.items()
    ]
    provisional = runtime.build_manifest(
        card, provisional_rows, rules=rules, date_root=date_root)
    problems = runtime.verify(
        card, provisional, {game_id: bundle for game_id, (_b, bundle) in bundles.items()})
    if problems:
        return None, None, problems

    manifest, path, _rows = runtime.write_runtime(
        card, bundles,
        lambda rows: runtime.build_manifest(card, rows, rules=rules, date_root=date_root),
        root=root, gzip_bundles=gzip_bundles,
    )
    return manifest, path, []


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: the card pointer's date)")
    parser.add_argument("--card", default=None,
                        help="Explicit handicapping card to project (default: "
                             "data/handicapping_card/<date>.json)")
    parser.add_argument("--slate-path", default=None,
                        help="Explicit slate artifact for the per-game context "
                             "(default: the card's own slateSource, then the "
                             "canonical resolution)")
    parser.add_argument("--out-root", default=runtime.RUNTIME_ROOT)
    parser.add_argument("--no-gzip", action="store_true",
                        help="Write only the uncompressed bundles")
    parser.add_argument("--print-sizes", action="store_true",
                        help="Print the old-vs-new size evidence")
    args = parser.parse_args(argv)

    started = time.time()
    builder = load_card_builder()

    card_path = args.card
    if not card_path:
        date = args.date
        if not date:
            pointer = os.path.join("data", "handicapping_card", "latest.json")
            try:
                date = load_json(pointer)["date"]
            except (OSError, ValueError, KeyError):
                print("ERROR: no --date, no --card, and no readable "
                      "data/handicapping_card/latest.json", file=sys.stderr)
                return 1
        card_path = os.path.join("data", "handicapping_card", f"{date}.json")

    if not os.path.exists(card_path):
        print(f"ERROR: no handicapping card at {card_path} -- run "
              "scripts/build_handicapping_card.py first", file=sys.stderr)
        return 1

    card = load_json(card_path)
    if args.date and card.get("date") != args.date:
        print(f"ERROR: {card_path} is for {card.get('date')!r}, not {args.date!r} -- "
              "refusing to publish one date's games under another date's label",
              file=sys.stderr)
        return 1

    # The slate supplies the per-game handicapping evidence. Preference
    # order: an explicit path, then the card's OWN recorded slateSource
    # (so the runtime describes the same slate the card was built from),
    # then the canonical resolution.
    slate = None
    slate_source = args.slate_path or card.get("slateSource")
    if slate_source and os.path.exists(slate_source):
        slate = builder.unwrap_pipeline_artifact(builder.load_json(slate_source))
    if slate is None:
        slate, slate_source = builder.load_slate(card["date"])
    if slate is None:
        print(f"WARNING: no slate for {card['date']} -- bundles will carry no game "
              "context (markets are unaffected)", file=sys.stderr)

    manifest, manifest_path, problems = build(
        card, slate, rules=load_rules(), root=args.out_root,
        gzip_bundles=not args.no_gzip,
    )
    if problems:
        print("ERROR: the compact runtime is not a faithful projection of the card. "
              "Refusing to write it.", file=sys.stderr)
        for problem in problems[:40]:
            print(f"  {problem}", file=sys.stderr)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more", file=sys.stderr)
        return 1

    elapsed = time.time() - started
    counts = manifest["counts"]
    eligible = [row for row in manifest["games"] if row["bettingEligible"]]
    print(f"[build_handicap_runtime] {manifest['date']}: "
          f"{counts['bettingEligibleGames']} betting-eligible of {counts['gamesTotal']} "
          f"games; {counts['eligibleMarketsTotal']} eligible markets, "
          f"{counts['runtimeBundleMarketRows']} rows across all bundles "
          f"(silentRemainder={counts['silentRemainderTotal']})")
    print(f"[build_handicap_runtime] wrote {manifest_path} "
          f"+ {len(manifest['games'])} game bundles in {elapsed:.2f}s")

    if args.print_sizes:
        card_bytes = os.path.getsize(card_path)
        manifest_bytes = os.path.getsize(manifest_path)
        total_raw = sum(row["bundleBytes"] for row in manifest["games"])
        total_gz = sum(row["bundleGzipBytes"] or 0 for row in manifest["games"])
        eligible_raw = sum(row["bundleBytes"] for row in eligible)
        eligible_gz = sum(row["bundleGzipBytes"] or 0 for row in eligible)
        print()
        print("  SIZE EVIDENCE")
        print(f"    old handicapping card            {card_bytes:>10,} B")
        print(f"    new manifest                     {manifest_bytes:>10,} B")
        print(f"    all game bundles (uncompressed)  {total_raw:>10,} B")
        print(f"    all game bundles (gzip)          {total_gz:>10,} B")
        print(f"    manifest + eligible bundles      "
              f"{manifest_bytes + eligible_raw:>10,} B  "
              f"({len(eligible)} eligible game(s), uncompressed)")
        print(f"    manifest + eligible bundles gz   "
              f"{manifest_bytes + eligible_gz:>10,} B")
        if card_bytes:
            ratio = card_bytes / max(manifest_bytes + eligible_raw, 1)
            print(f"    reduction to handicap the slate  {ratio:>10.1f}x")
        print()
        print("  MARKET COUNTS (must be identical -- nothing was filtered)")
        print(f"    card eligible markets            {counts['eligibleMarketsTotal']:>10,}")
        print(f"    card research-only markets       {counts['researchOnlyMarketsTotal']:>10,}")
        print(f"    runtime bundle rows              {counts['runtimeBundleMarketRows']:>10,}")
        print(f"    raw universe contracts           {counts['rawUniverseContracts']:>10,}")
        print(f"    silent remainder                 {counts['silentRemainderTotal']:>10,}")
        print()
        for row in manifest["games"]:
            flag = "ELIGIBLE" if row["bettingEligible"] else "research"
            print(f"    {flag:9s} {str(row['matchup']):10s} "
                  f"{row['marketsToInspect']:>5,} markets  "
                  f"{row['bundleBytes']:>9,} B / {row['bundleGzipBytes'] or 0:>7,} B gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
