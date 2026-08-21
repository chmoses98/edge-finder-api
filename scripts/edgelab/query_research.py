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
    python3 scripts/edgelab/query_research.py research-query --date 2026-08-03 --market-family team_total
    python3 scripts/edgelab/query_research.py research-query --start-date 2026-08-03 --end-date 2026-08-09 \\
        --market-family team_total --min-edge 2 --group-by confidence
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import query, storage


def _load_date(date):
    return {
        "observations": list(storage.read_records(storage.partition_path("observations", date, compressed=True))),
        "recommendations": list(storage.read_partition("recommendations", date)),
        "settlements": list(storage.read_partition("settlements", date)),
        "games": list(storage.read_records(storage.partition_path("games", date))),
        "research_runs": {r["runId"]: r for r in storage.read_records(storage.partition_path("research_runs", date))},
    }


def _date_range(start_date, end_date):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if end < start:
        raise ValueError(f"end date {end_date} is before start date {start_date}")
    days = (end - start).days
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]


def _load_research_universe(dates):
    """
    Loads every canonical source build_research_rows() joins against, for
    the FULL observed-market population across `dates` -- read-only
    (storage.read_records only). Bets are loaded once from the single
    canonical ledger (not date-partitioned) and matched to observed
    tickers by build_research_rows() itself.
    """
    observations, settlements, evaluations, recommendations, games = [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=True)))
        settlements.extend(storage.read_partition("settlements", date))
        evaluations.extend(storage.read_partition("model_evaluations", date))
        recommendations.extend(storage.read_partition("recommendations", date))
        games.extend(storage.read_records(storage.partition_path("games", date)))
    return query.build_research_rows(
        observations, settlements, evaluations=evaluations, recommendations=recommendations,
        bets=_load_bets(), games=games,
    )


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


def _parse_bool(value):
    if value is None:
        return None
    return value == "true"


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

    p = sub.add_parser(
        "research-query",
        help="Full-observed-market research engine: filter + aggregate hypothetical ROI, recommendation "
             "performance, and actual bet P/L (kept separate) over every settled observed market, not only bets placed.",
    )
    p.add_argument("--date", default=None, help="Single game date, YYYY-MM-DD")
    p.add_argument("--start-date", default=None, help="Start of an inclusive game-date range, YYYY-MM-DD")
    p.add_argument("--end-date", default=None, help="End of an inclusive game-date range, YYYY-MM-DD")
    p.add_argument("--market-family", default=None)
    p.add_argument("--market-horizon", default=None, choices=["FULL_GAME", "F3", "F5", "F7"])
    p.add_argument("--threshold", type=float, default=None, help="Exact threshold/rung match")
    p.add_argument("--min-threshold", type=float, default=None)
    p.add_argument("--max-threshold", type=float, default=None)
    p.add_argument("--side", default=None, choices=["YES", "NO"])
    p.add_argument("--min-price", type=float, default=None, help="Standardized pregame YES price, 0-1")
    p.add_argument("--max-price", type=float, default=None)
    p.add_argument("--min-fair-probability", type=float, default=None, help="0-100 scale")
    p.add_argument("--max-fair-probability", type=float, default=None)
    p.add_argument("--min-edge", type=float, default=None, help="estimatedEdge, percentage points")
    p.add_argument("--max-edge", type=float, default=None)
    p.add_argument("--confidence", default=None, choices=["HIGH", "MEDIUM", "PAPER"])
    p.add_argument("--settlement-status", default=None,
                    choices=["SETTLED", "VOID", "SETTLEMENT_UNRESOLVED", "UNAVAILABLE", "NOT_SETTLED"])
    p.add_argument("--recommendation-status", default=None)
    p.add_argument("--was-recommended", default=None, choices=["true", "false"])
    p.add_argument("--was-placed", default=None, choices=["true", "false"])
    p.add_argument("--disagreement", default=None, choices=["MODEL_HIGHER", "MODEL_LOWER", "AGREE"])
    p.add_argument("--thesis-tag", default=None)
    p.add_argument("--game-id", default=None)
    p.add_argument("--clv-available", default=None, choices=["true", "false"])
    p.add_argument("--stake-unit", type=float, default=1.0)
    p.add_argument("--group-by", default=None,
                    help="Break the aggregate out by this row field (e.g. marketFamily, confidence, "
                         "modelVsMarketDisagreement) instead of returning one combined aggregate")

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
    elif args.command == "research-query":
        if args.date:
            dates = [args.date]
        elif args.start_date and args.end_date:
            dates = _date_range(args.start_date, args.end_date)
        else:
            print("[query_research] research-query requires either --date or both --start-date/--end-date", file=sys.stderr)
            return 1

        rows = _load_research_universe(dates)
        rows = query.filter_research_rows(
            rows,
            market_family=args.market_family,
            market_horizon=args.market_horizon,
            threshold=args.threshold,
            min_threshold=args.min_threshold,
            max_threshold=args.max_threshold,
            side=args.side,
            min_price=args.min_price,
            max_price=args.max_price,
            min_fair_probability=args.min_fair_probability,
            max_fair_probability=args.max_fair_probability,
            min_edge=args.min_edge,
            max_edge=args.max_edge,
            confidence=args.confidence,
            settlement_status=args.settlement_status,
            recommendation_status=args.recommendation_status,
            was_recommended=_parse_bool(args.was_recommended),
            was_placed=_parse_bool(args.was_placed),
            disagreement=args.disagreement,
            thesis_tag=args.thesis_tag,
            game_id=args.game_id,
            # date/start_date/end_date are not reapplied here -- _load_research_universe(dates)
            # already scoped the loaded observations/settlements to exactly this date range.
            clv_available=_parse_bool(args.clv_available),
        )
        if args.group_by:
            _print(query.aggregate_research_rows_by(rows, args.group_by, stake_unit=args.stake_unit))
        else:
            _print(query.aggregate_research_rows(rows, stake_unit=args.stake_unit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
