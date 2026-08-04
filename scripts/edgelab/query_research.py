#!/usr/bin/env python3
"""
scripts/edgelab/query_research.py
=====================================
Part 6 (Research Query Surface) of the MLB Market Research Corpus &
Frictionless Manual Logging milestone: a READ-ONLY CLI over the full
EdgeLab corpus (market observations, recommendations, settlements,
placed bets, postmortems). Every subcommand here only ever calls
lib.edgelab.storage.read_records -- never append_records/upsert_records/
write_all_records, and never lib.edgelab.bets.write_placed_bet or
lib.edgelab.postmortems.write_postmortem. This script cannot mutate the
ledger no matter what arguments it's given.

Usage:
    python3 scripts/edgelab/query_research.py observed-markets --date 2026-08-03 --game-id 123
    python3 scripts/edgelab/query_research.py alternate-thresholds --date 2026-08-03 --family team_total
    python3 scripts/edgelab/query_research.py pitcher-strikeout-closings --date 2026-08-03
    python3 scripts/edgelab/query_research.py checkpoint-comparison --date 2026-08-03 --ticker KXMLB...
    python3 scripts/edgelab/query_research.py observed-never-recommended --date 2026-08-03
    python3 scripts/edgelab/query_research.py recommended-not-placed --date 2026-08-03
    python3 scripts/edgelab/query_research.py manual-bets-without-slate --date 2026-08-03
    python3 scripts/edgelab/query_research.py performance-by-family --date 2026-08-03
    python3 scripts/edgelab/query_research.py capture-for-bet --bet-id <betId>
    python3 scripts/edgelab/query_research.py postmortem-for-date --date 2026-08-03
    python3 scripts/edgelab/query_research.py postmortem-for-bet --bet-id <betId>
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import query, storage


def _load_date(date):
    return {
        "observations": list(storage.read_records(storage.partition_path("observations", date, compressed=True))),
        "recommendations": list(storage.read_records(storage.partition_path("recommendations", date))),
        "settlements": list(storage.read_records(storage.partition_path("settlements", date))),
        "games": list(storage.read_records(storage.partition_path("games", date))),
        "research_runs": {r["runId"]: r for r in storage.read_records(storage.partition_path("research_runs", date))},
    }


def _load_bets():
    return list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))


def _load_postmortem(date):
    path = os.path.join("data", "edgelab", "postmortems", date, "postmortem.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _print(result):
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("observed-markets")
    p.add_argument("--date", required=True)
    p.add_argument("--game-id", required=True)

    p = sub.add_parser("alternate-thresholds")
    p.add_argument("--date", required=True)
    p.add_argument("--family", required=True)
    p.add_argument("--horizon", default=None)

    p = sub.add_parser("pitcher-strikeout-closings")
    p.add_argument("--date", required=True)

    p = sub.add_parser("checkpoint-comparison")
    p.add_argument("--date", required=True)
    p.add_argument("--ticker", required=True)

    p = sub.add_parser("observed-never-recommended")
    p.add_argument("--date", required=True)

    p = sub.add_parser("recommended-not-placed")
    p.add_argument("--date", required=True)

    p = sub.add_parser("manual-bets-without-slate")
    p.add_argument("--date", required=True)

    p = sub.add_parser("performance-by-family")
    p.add_argument("--date", required=True)

    p = sub.add_parser("capture-for-bet")
    p.add_argument("--bet-id", required=True)
    p.add_argument("--date", default=None, help="Restrict the research-run lookup to this date; defaults to the bet's own gameDate")

    p = sub.add_parser("postmortem-for-date")
    p.add_argument("--date", required=True)

    p = sub.add_parser("postmortem-for-bet")
    p.add_argument("--bet-id", required=True)

    args = parser.parse_args()

    if args.command == "observed-markets":
        data = _load_date(args.date)
        _print(query.observed_markets_for_game(data["observations"], args.game_id))
    elif args.command == "alternate-thresholds":
        data = _load_date(args.date)
        _print(query.alternate_thresholds(data["observations"], args.family, args.horizon))
    elif args.command == "pitcher-strikeout-closings":
        data = _load_date(args.date)
        _print(query.pitcher_strikeout_closings(data["observations"], data["settlements"]))
    elif args.command == "checkpoint-comparison":
        data = _load_date(args.date)
        _print(query.checkpoint_price_comparison(data["observations"], args.ticker))
    elif args.command == "observed-never-recommended":
        data = _load_date(args.date)
        _print(query.observed_never_recommended(data["observations"], data["recommendations"]))
    elif args.command == "recommended-not-placed":
        data = _load_date(args.date)
        _print(query.recommended_not_placed(data["recommendations"], _load_bets()))
    elif args.command == "manual-bets-without-slate":
        data = _load_date(args.date)
        _print(query.manual_bets_without_slate(_load_bets(), data["games"]))
    elif args.command == "performance-by-family":
        data = _load_date(args.date)
        _print(query.performance_by_family_all_observed(data["settlements"], _load_bets()))
    elif args.command == "capture-for-bet":
        bets_by_id = {b["betId"]: b for b in _load_bets()}
        bet = bets_by_id.get(args.bet_id)
        if bet is None:
            print(f"[query_research] no bet found with betId={args.bet_id}", file=sys.stderr)
            return 1
        date = args.date or bet.get("gameDate")
        research_runs = _load_date(date)["research_runs"] if date else {}
        _print(query.market_corpus_capture_for_bet(bet, research_runs))
    elif args.command == "postmortem-for-date":
        _print(query.postmortem_for_date({args.date: _load_postmortem(args.date)}, args.date))
    elif args.command == "postmortem-for-bet":
        bets_by_id = {b["betId"]: b for b in _load_bets()}
        bet = bets_by_id.get(args.bet_id)
        date = bet.get("gameDate") if bet else None
        pm = _load_postmortem(date) if date else None
        _print(query.postmortem_for_bet(args.bet_id, [pm] if pm else []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
