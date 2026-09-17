#!/usr/bin/env python3
"""
scripts/build_handicapping_card.py
======================================
THE real-money handicapping card: which games may be bet, every Kalshi
market available for each of them, and the bankroll they may be sized
against.

    data/handicapping_card/<YYYY-MM-DD>.json
    data/handicapping_card/latest.json      (pointer)

WHAT IT RESOLVES
----------------
Two things were previously in tension. `HANDICAPPING_PLAYBOOK.md` says
"inspect the complete available market universe"; `RUN_THE_SLATE.md`
said the 11-row `marketLedger` is the ONLY source of truth for
recommendation-eligible markets and called full-market coverage
"research/audit visibility, not a betting input". Both cannot be the
methodology.

This card is the resolution, and the order is the whole point:

    1. archive the COMPLETE MLB Kalshi universe        (already done by
                                                        the capture jobs)
    2. determine which games have NOT started
    3. determine which of those have BOTH official
       lineups confirmed                               -> BETTING-ELIGIBLE
    4. for each BETTING-ELIGIBLE game, surface EVERY
       Kalshi market attributable to it
    5. everything else stays archived and researchable,
       and can never produce a real-money recommendation

So "analyze every available market" means *every available market for
every unstarted, lineup-confirmed, otherwise-eligible game* -- never
"recommend bets on games whose lineups are unconfirmed".

FIVE AXES, NEVER BLURRED
------------------------
Each market row carries them separately (see
`lib/betting_eligibility.py`):
  * GAME ELIGIBILITY            -- decided once, at the game
  * MARKET AVAILABILITY         -- does Kalshi list it
  * PRODUCTION MODEL SUPPORT    -- is it one of the 11 modelled rows
  * MANUAL HANDICAPPING ELIGIBILITY -- can the analyst evaluate it
  * AUTOMATIC SETTLEMENT SUPPORT -- can this repo grade it afterwards

A market with no production adapter is still visible and still
comparable. A market with no automatic settlement support is still
comparable but is flagged, because it will need manual reconciliation.
Neither fact is ever silently hidden, and no model probability is ever
fabricated for an unsupported family.

WHAT IT NEVER DOES
------------------
No probability, edge, or recommendation is computed here. It never
writes `data/slate.json`, `bets.json`, `marketLedger` or any pipeline
artifact. It never places or records a wager. It is an assembly of
already-captured evidence plus two gates.

EARLY-VALUE SURFACE
-------------------
Unconfirmed-lineup games are retained under `researchOnly` with their
full market list, explicitly outside the executable card. That is the
early-value/research surface, and `realMoneyEligible: false` on every
one of its rows is what stops it leaking into the betting card.

Usage:
    python3 scripts/build_handicapping_card.py [--date YYYY-MM-DD]
    python3 scripts/build_handicapping_card.py --source snapshot --as-of 2026-09-17T18:00:00Z
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import bankroll_context as bankroll_ctx
from lib.betting_eligibility import partition_slate
from lib.kalshi_price_check import apply_filters, apply_strict_game_registry, normalize_batch
from lib.playbook import read_playbook_version
from lib.research.market_taxonomy import CONFIRMED_SINGLE_GAME_SERIES_TICKERS  # noqa: F401  (documents the allowlist source)

CARD_DIR = os.path.join("data", "handicapping_card")
SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")

def auto_settleable_families():
    """
    The families this repository can grade AUTOMATICALLY, derived from
    the canonical settler itself rather than re-listed by hand: the
    game-level FAMILY_* constants `lib.edgelab.settlement.
    settle_market_full` dispatches on, plus the player-prop families
    `lib.edgelab.player_stats.STAT_CATEGORY_BY_FAMILY` defines.

    On any import failure this returns the empty set, which makes every
    market report MANUAL_RECONCILIATION_REQUIRED. Under-claiming
    automatic settlement support is the safe direction; over-claiming it
    would tell an analyst a wager will settle itself when it will not.
    """
    families = set()
    try:
        from lib.edgelab import settlement
        families |= {
            getattr(settlement, name) for name in dir(settlement)
            if name.startswith("FAMILY_") and isinstance(getattr(settlement, name), str)
        }
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib.edgelab import player_stats
        families |= set(player_stats.STAT_CATEGORY_BY_FAMILY)
    except Exception:  # noqa: BLE001
        pass
    return families


def production_model_series():
    """
    The SERIES tickers the 11-row production model actually prices, read
    from `config/rules.json -> market_list` rather than duplicated here.
    A market outside this set is NOT invisible to a handicapper -- it
    simply has no production adapter, which is axis 3, not axis 4.
    """
    try:
        with open(os.path.join("config", "rules.json"), encoding="utf-8") as f:
            rows = json.load(f).get("market_list") or []
    except (OSError, ValueError):
        return set()
    return {row.get("series") for row in rows if row.get("series")}


def latest_snapshot(date):
    paths = sorted(glob.glob(os.path.join(SNAPSHOT_DIR, f"kalshi_search_{date}*.json")))
    return paths[-1] if paths else None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def extract_markets(payload):
    markets = (payload or {}).get("markets", [])
    if isinstance(markets, dict):
        markets = list(markets.values())
    return markets if isinstance(markets, list) else []


def unwrap_pipeline_artifact(payload):
    """
    Pure. A pipeline artifact wraps its payload in a versioned envelope
    (`lib/pipeline_artifacts.py`: `{"meta": {...}, "data": <payload>}`).
    A plain file is its own payload. `payload` is also accepted for
    forward/backward compatibility with any other envelope shape in the
    tree -- reading the wrong key is how a slate silently comes back
    empty.
    """
    if not isinstance(payload, dict):
        return payload
    for key in ("data", "payload"):
        inner = payload.get(key)
        if isinstance(inner, dict) and ("games" in inner or "date" in inner):
            return inner
    return payload


def load_slate(date):
    """This date's canonical slate, from data/slate.json when it matches
    the requested date, else that date's frozen normalized-slate pipeline
    artifact. Never another date's."""
    for label, path in (
        ("data/slate.json", os.path.join("data", "slate.json")),
        (f"data/pipeline/{date}/normalized_slate.json",
         os.path.join("data", "pipeline", date, "normalized_slate.json")),
    ):
        payload = load_json(path)
        if payload is None:
            continue
        slate = unwrap_pipeline_artifact(payload)
        if (slate or {}).get("date") == date:
            return slate, label
    return None, None


def markets_for_game(records, matchup, date):
    """Every normalized Kalshi market attributable to one matchup. The
    canonical filter chain, reused -- no second classifier."""
    kept, stage_report = apply_filters(records, {
        "date": date,
        "games": [matchup],
        "include_closed": True,   # a closed contract is still part of "every market"
        "include_unknown": True,  # never hide a contract the parser could not classify
    })
    return kept, stage_report


def annotate_market(record, *, game_eligible, production_series, settleable):
    """
    Pure. Attach the five axes to one market row, each as its own field.
    Nothing here computes a probability or an edge.
    """
    family = record.get("family")
    return {
        **record,
        # Axis 1: decided at the game, repeated here so a row is never
        # read out of context.
        "gameEligibleForRealMoney": game_eligible,
        "realMoneyEligible": game_eligible,
        # Axis 2 is implicit: this row exists because Kalshi lists it.
        "marketAvailable": True,
        # Axis 3 -- membership in the legacy 11-row production universe,
        # by SERIES ticker. "NO_PRODUCTION_ADAPTER" is a statement about
        # the model, never about whether the analyst may compare it.
        "productionModelSupport": (
            "MODELLED" if record.get("seriesTicker") in production_series
            else "NO_PRODUCTION_ADAPTER"
        ),
        # Axis 4 -- a market is manually evaluable when it is a real,
        # classified single-game contract with a price. An unclassified
        # family is flagged rather than hidden.
        "manualHandicappingEligibility": (
            "EVALUABLE" if (family and family != "unknown" and record.get("yesAsk") is not None)
            else "NEEDS_ANALYST_JUDGEMENT"
        ),
        # Axis 5
        "automaticSettlementSupport": (
            "SUPPORTED" if family in settleable else "MANUAL_RECONCILIATION_REQUIRED"
        ),
        # Never fabricated: this card computes no model probability at all.
        "modelProbability": None,
        "modelProbabilityNote": (
            "This card computes no model probability. An unsupported family is never given a "
            "fabricated one; price the thesis yourself."
        ),
    }


def build_card(*, date, slate, slate_source, records, bankroll, now_utc=None):
    partition = partition_slate(slate, now_utc=now_utc)
    production_series = production_model_series()
    settleable = auto_settleable_families()

    def _game_block(verdict, eligible):
        matchup = verdict["matchup"]
        markets, stage_report = ([], {}) if not matchup else markets_for_game(records, matchup, date)
        annotated = [
            annotate_market(m, game_eligible=eligible, production_series=production_series,
                            settleable=settleable)
            for m in markets
        ]
        families, unsupported_settlement = {}, set()
        for row in annotated:
            families[row.get("family") or "unknown"] = families.get(row.get("family") or "unknown", 0) + 1
            if row["automaticSettlementSupport"] != "SUPPORTED":
                unsupported_settlement.add(row.get("family") or "unknown")
        return {
            "gameId": verdict["gameId"],
            "matchup": matchup,
            "scheduledStart": verdict["scheduledStart"],
            "eligibilityStatus": verdict["status"],
            "realMoneyEligible": eligible,
            "eligibilityReason": verdict["reason"],
            "lineups": verdict["lineups"],
            "gameStatus": verdict["gameStatus"],
            "marketCount": len(annotated),
            "marketsByFamily": dict(sorted(families.items())),
            "familiesWithoutAutomaticSettlement": sorted(unsupported_settlement),
            "marketFilterStageReport": stage_report,
            "markets": annotated,
        }

    eligible_games = [_game_block(v, True) for v in partition["bettingEligible"]]
    research_games = [_game_block(v, False) for v in partition["researchOnly"]]

    return {
        "schemaVersion": "1",
        "artifactType": "REAL_MONEY_HANDICAPPING_CARD",
        "date": date,
        "generatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # The instant started/not-started was evaluated against. Equal to
        # generatedAt on a normal run; a replay sets it explicitly so the
        # two are never silently conflated.
        "eligibilityEvaluatedAt": now_utc or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "playbookVersion": read_playbook_version(os.path.join(ROOT, "HANDICAPPING_PLAYBOOK.md")),
        "slateSource": slate_source,
        "bankroll": bankroll,
        "eligibilityPolicy": partition["policy"],
        "counts": {
            "gamesTotal": partition["gamesTotal"],
            "bettingEligibleGames": len(eligible_games),
            "researchOnlyGames": len(research_games),
            "researchOnlyByReason": partition["researchOnlyByReason"],
            "eligibleMarketsTotal": sum(g["marketCount"] for g in eligible_games),
            "researchOnlyMarketsTotal": sum(g["marketCount"] for g in research_games),
            "rawUniverseMarkets": len(records),
        },
        # The executable card. Every market here is comparable.
        "bettingEligibleGames": eligible_games,
        # Archived, visible, researchable -- and never real-money.
        "researchOnlyGames": research_games,
        "axes": {
            "GAME_ELIGIBILITY": "not started AND both official lineups confirmed AND integrity gates pass",
            "MARKET_AVAILABILITY": "Kalshi lists the contract (this row exists)",
            "PRODUCTION_MODEL_SUPPORT": "the production model prices it (config/rules.json market_list)",
            "MANUAL_HANDICAPPING_ELIGIBILITY": "the analyst can evaluate it from the available evidence",
            "AUTOMATIC_SETTLEMENT_SUPPORT": "scripts/edgelab/settle_markets.py can grade it automatically",
        },
        "guarantees": [
            "Only games that have NOT started and have BOTH official lineups confirmed appear under "
            "bettingEligibleGames. Everything else is researchOnly and carries realMoneyEligible=false "
            "on every market row.",
            "For a betting-eligible game, EVERY Kalshi market attributable to it is present -- not just "
            "the 11 production-model rows.",
            "No probability, edge or recommendation is computed here, and no unsupported family is ever "
            "given a fabricated model probability.",
            "Markets without automatic settlement support are surfaced, not hidden -- they need manual "
            "reconciliation after the game.",
        ],
    }


def write_card(card, *, root=CARD_DIR):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{card['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(card, f, indent=2, sort_keys=True)
    latest = os.path.join(root, "latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump({
            "date": card["date"], "path": path.replace(os.sep, "/"),
            "generatedAt": card["generatedAt"], "playbookVersion": card["playbookVersion"],
            "bettingEligibleGames": card["counts"]["bettingEligibleGames"],
            "bankrollStatus": (card.get("bankroll") or {}).get("status"),
        }, f, indent=2, sort_keys=True)
    return path, latest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: data/slate.json's own date)")
    parser.add_argument("--as-of", default=None,
                        help="ISO-8601 UTC instant to evaluate started/not-started against (tests, replays)")
    parser.add_argument("--slate-path", default=None,
                        help="Explicit slate artifact to evaluate (an archived "
                             "data/slates/<date>/*.json or pipeline artifact). For replays and "
                             "behavioural proofs; production reads the canonical slate.")
    parser.add_argument("--out-root", default=CARD_DIR)
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    date = args.date
    if not date:
        slate_payload = load_json(os.path.join("data", "slate.json")) or {}
        date = slate_payload.get("date")
    if not date:
        print("ERROR: no --date given and data/slate.json has no date", file=sys.stderr)
        return 1

    if args.slate_path:
        slate = unwrap_pipeline_artifact(load_json(args.slate_path))
        slate_source = args.slate_path
        if slate is not None and slate.get("date") != date:
            print(f"ERROR: --slate-path is for {slate.get('date')!r}, not {date!r} -- refusing to "
                  f"evaluate one date's games under another date's label", file=sys.stderr)
            return 1
    else:
        slate, slate_source = load_slate(date)
    if slate is None:
        print(f"ERROR: no canonical slate for {date} -- run the slate fetch first", file=sys.stderr)
        return 1

    snapshot_path = latest_snapshot(date)
    if not snapshot_path:
        print(f"ERROR: no archived complete-universe Kalshi snapshot for {date}", file=sys.stderr)
        return 1
    raw = extract_markets(load_json(snapshot_path))
    records, _status_counts, _malformed = normalize_batch(
        raw, source_mode="snapshot", source_used="snapshot")
    validated, _excluded = apply_strict_game_registry(records, requested_date=date)

    card = build_card(
        date=date, slate=slate, slate_source=slate_source, records=validated,
        bankroll=bankroll_ctx.load_bankroll_context(), now_utc=args.as_of,
    )
    card["rawUniverseSnapshot"] = snapshot_path.replace(os.sep, "/")
    path, latest = write_card(card, root=args.out_root)

    counts = card["counts"]
    print(f"[build_handicapping_card] {date}: {counts['bettingEligibleGames']} betting-eligible of "
          f"{counts['gamesTotal']} games ({counts['researchOnlyByReason']})")
    print(f"[build_handicapping_card] eligible markets: {counts['eligibleMarketsTotal']} "
          f"(research-only, non-executable: {counts['researchOnlyMarketsTotal']})")
    print(f"[build_handicapping_card] {bankroll_ctx.describe_for_output(card['bankroll'])}")
    print(f"[build_handicapping_card] wrote {path} | pointer {latest}")

    if args.print_summary:
        for game in card["bettingEligibleGames"]:
            print(f"  ELIGIBLE  {game['matchup']:10s} {game['marketCount']:4d} markets "
                  f"{game['marketsByFamily']}")
        for game in card["researchOnlyGames"]:
            print(f"  research  {game['matchup']:10s} {game['marketCount']:4d} markets "
                  f"[{game['eligibilityStatus']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
