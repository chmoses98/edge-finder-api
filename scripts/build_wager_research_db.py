#!/usr/bin/env python3
"""
scripts/build_wager_research_db.py
=====================================
Builds the canonical wager research database from the root `bets.json`
ledger (NOT the stale `data/bets.json` — see
docs/WAGER_RESEARCH_DATABASE.md for why).

bets.json spans many schema eras (pre-Kalshi sportsbook bets, early
Kalshi bets, Rule-71/81-era bets, manual bets from
scripts/log_manual_bet.py, CLV-instrumented bets from
scripts/capture_closing_lines.py). This script normalizes every one of
them into one canonical row without fabricating any value it cannot
find — every schema-era gap becomes a null field, never a guess, and no
wager is ever dropped.

Writes:
    data/research/wagers.jsonl          one JSON object per line
    data/research/wagers.csv            same rows, flat CSV
    data/research/schema.json           canonical field list + types
    data/research/build_report.json     row counts, per-era coverage,
                                         null-field census
    data/research/calibration_bins.json probability-bin calibration
                                         table (settled binary wagers
                                         with a valid model probability)

Deterministic: running twice on unchanged bets.json produces byte-
identical wagers.jsonl/csv (rows sorted by (date, betId) always).
"""
import csv
import json
import os
import sys
from collections import defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

BETS_PATH = os.path.join(ROOT_DIR, "bets.json")
RESEARCH_DIR = os.path.join(ROOT_DIR, "data", "research")
PAPER_SPREAD_LEDGER_PATH = os.path.join(RESEARCH_DIR, "paper_spread_ledger.jsonl")

SETTLED_RESULTS = {"WIN", "LOSS", "PUSH", "VOID"}
BINARY_MARKET_FAMILIES = {"game_result", "inning_result", "game_total", "inning_total",
                          "team_total", "winning_margin", "first_inning_run"}

# Legacy `market` string -> (marketFamily, period). Mirrors the same
# taxonomy lib.kalshi_mlb_market_classifier / lib.research.market_taxonomy
# use, so historical wagers and newly-discovered contracts share one
# vocabulary in reports.
MARKET_TO_FAMILY_PERIOD = {
    "ML": ("game_result", "full_game"),
    "ML_Away": ("game_result", "full_game"),
    "ML_Home": ("game_result", "full_game"),
    "F5 ML": ("inning_result", "F5"),
    "F5": ("inning_result", "F5"),
    "F5_ML_Away": ("inning_result", "F5"),
    "F5_ML_Home": ("inning_result", "F5"),
    "Run Line": ("winning_margin", "full_game"),
    "RL": ("winning_margin", "full_game"),
    "F5 Spread": ("winning_margin", "F5"),
    "F5 RL": ("winning_margin", "F5"),
    "F5 Run Line": ("winning_margin", "F5"),
    "Total": ("game_total", "full_game"),
    "Total Over": ("game_total", "full_game"),
    "Game Total": ("game_total", "full_game"),
    "F5 Total": ("inning_total", "F5"),
    "Team Total": ("team_total", "full_game"),
    "TT_Over": ("team_total", "full_game"),
    "TT Over": ("team_total", "full_game"),
    "TT_Away_Over": ("team_total", "full_game"),
    "TT_Home_Over": ("team_total", "full_game"),
    "NRFI": ("first_inning_run", "F1"),
    "YRFI": ("first_inning_run", "F1"),
    # "K Prop" / "Pitcher Prop" are deliberately NOT mapped here -- these
    # are manual sportsbook player-prop bets (MODEL_CORE.md's strikeout-
    # prop checklist), not Kalshi contracts. Leaving them unmapped is
    # correct: they have no Kalshi marketFamily to report.
}

CANONICAL_FIELDS = [
    # Identity
    "betId", "date", "gameId", "game", "ticker", "eventTicker", "marketFamily",
    "market", "period", "side", "line", "subjectType", "subjectName", "source",
    "entryTimestamp", "modelVersion", "joinMethod",
    # Entry / closing price
    "entryPricePct", "entryAmerican", "entryBidPct", "entryAskPct", "entryMidPct",
    "closingAskPct", "closingMidPct", "closingAmerican", "closingTimestamp",
    "clvAskPct", "clvMidPct", "clvCaptureStatus", "closingLineUnavailableReason",
    # Projection
    "modelProbPct", "marketImpliedPctAtEntry", "projectedEdgePct",
    "expectedProfitPerDollar", "confidenceTier", "recommendationStatus",
    "modelSupportStatus",
    # Risk and result
    "stake", "result", "grossReturn", "netProfit", "roiPct",
    # Context
    "lineupConfirmationStatus", "awayPitcher", "homePitcher", "bullpenState",
    "park", "weather", "umpire", "favoriteOrUnderdog", "horizon",
    "manualAnalysisNotes", "reasonCodes", "exactContractMetadata",
    # Quality
    "dataQualityStatus", "dataQualityFlags",
    # Tracking-type separation (spread-correction mission Part 5):
    # REAL/PAPER/MODEL_ONLY/MANUAL wagers must never be conflated when
    # computing bankroll performance -- see docs/WAGER_RESEARCH_DATABASE.md.
    "trackingType", "countsTowardBankroll", "hypotheticalStake",
    "hypotheticalNetProfit", "hypotheticalRoiPct", "realMoneyBlockReasons",
]


def _first(bet, *keys):
    """First non-None value among keys, else None."""
    for k in keys:
        v = bet.get(k)
        if v is not None:
            return v
    return None


def _american_to_prob_pct(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    prob = 100 / (o + 100) if o >= 0 else abs(o) / (abs(o) + 100)
    return round(prob * 100, 3)


def _to_pct(value):
    """Normalize a 0-1 fraction or 0-100 value to 0-100. None stays None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v * 100, 3) if 0 <= v <= 1 else round(v, 3)


def resolve_market_family_period(bet):
    market = bet.get("market")
    if market in MARKET_TO_FAMILY_PERIOD:
        return MARKET_TO_FAMILY_PERIOD[market]
    return (None, None)


def resolve_identity(bet, index_in_date):
    """Priority: 1) exact ticker, 2) gameId+family+period+side+line,
    3) documented legacy fallback (game string + market + betSide)."""
    ticker = _first(bet, "marketTicker", "ticker")
    if ticker:
        return ticker, "exact_ticker"

    game_id = bet.get("gameId")
    family, period = resolve_market_family_period(bet)
    side = _first(bet, "betSide", "side")
    line = bet.get("line")
    if game_id and family:
        return f"{game_id}:{family}:{period}:{side}:{line}", "gameId_family_period_side_line"

    game = bet.get("game") or "UNKNOWN_GAME"
    market = bet.get("market") or "UNKNOWN_MARKET"
    date = bet.get("date") or "UNKNOWN_DATE"
    return f"legacy:{date}:{game}:{market}:{side}:#{index_in_date}", "legacy_fallback"


def resolve_outcome(bet):
    result = str(bet.get("result") or "").upper()
    if result in SETTLED_RESULTS:
        return result
    status = str(bet.get("status") or "").upper()
    if status in SETTLED_RESULTS:
        return status
    return "PENDING"


def compute_financials(bet, outcome):
    """
    Uses ONLY the ledger's own stored settlement fields (`pl`, `stake`) —
    never recomputes an outcome from a game score. Push/void returns the
    stake with zero net profit; pending is null across the board; a WIN
    with no stored `pl` stays null rather than guessing a payout from
    American odds (which can differ from the actual fill).
    """
    stake = _first(bet, "stake", "betSize", "size")
    stake = float(stake) if stake is not None else None

    if outcome == "PENDING":
        return {"grossReturn": None, "netProfit": None, "roiPct": None}

    if outcome in ("PUSH", "VOID"):
        return {
            "grossReturn": stake,
            "netProfit": 0.0 if stake is not None else None,
            "roiPct": 0.0 if stake is not None else None,
        }

    pl = _first(bet, "pl", "pnl")
    net_profit = float(pl) if pl is not None else None
    if net_profit is None and outcome == "LOSS" and stake is not None:
        net_profit = -stake  # a LOSS's downside is always exactly the stake, never a guess

    if net_profit is None or stake is None:
        return {"grossReturn": None, "netProfit": net_profit, "roiPct": None}

    gross_return = round(stake + net_profit, 2)
    roi_pct = round((net_profit / stake) * 100, 3) if stake else None
    return {"grossReturn": gross_return, "netProfit": round(net_profit, 2), "roiPct": roi_pct}


def resolve_data_quality(bet, family, join_method):
    flags = []
    if join_method == "legacy_fallback":
        flags.append("NO_EXACT_OR_GAME_IDENTITY")
    if family is None:
        flags.append("UNRECOGNIZED_MARKET_STRING")
    if bet.get("stake") is None and bet.get("betSize") is None and bet.get("size") is None:
        flags.append("MISSING_STAKE")
    entry_price = _first(bet, "betTimeLine", "price", "entryBidPct", "entryAskPct")
    if entry_price is None:
        flags.append("MISSING_ENTRY_PRICE")
    status = "CLEAN" if not flags else ("DEGRADED" if len(flags) == 1 else "POOR")
    return status, flags


def resolve_tracking_type(bet):
    """
    REAL/MANUAL wagers from bets.json always countsTowardBankroll=True
    -- a real (or manually-placed real) wager's stake/netProfit/roiPct
    ARE the bankroll math, never a hypothetical figure. Paper rows are
    built separately by build_paper_row() and are never routed through
    this function.
    """
    source = str(_first(bet, "source", "betType") or "").upper()
    return "MANUAL" if source == "MANUAL" else "REAL"


def build_row(bet, index_in_date):
    family, period = resolve_market_family_period(bet)
    join_key, join_method = resolve_identity(bet, index_in_date)
    outcome = resolve_outcome(bet)
    financials = compute_financials(bet, outcome)
    data_quality_status, data_quality_flags = resolve_data_quality(bet, family, join_method)

    entry_price_raw = _first(bet, "betTimeLine", "price")
    entry_price_pct = _american_to_prob_pct(entry_price_raw) if entry_price_raw is not None else _to_pct(
        _first(bet, "entryAskPct", "entryBidPct"))

    row = {
        "betId": bet.get("id") or join_key,
        "date": bet.get("date"),
        "gameId": bet.get("gameId"),
        "game": bet.get("game"),
        "ticker": _first(bet, "marketTicker", "ticker"),
        "eventTicker": bet.get("eventTicker"),
        "marketFamily": family,
        "market": bet.get("market"),
        "period": period,
        "side": _first(bet, "betSide", "side"),
        "line": bet.get("line"),
        "subjectType": None,
        "subjectName": _first(bet, "betSide", "side"),
        "source": _first(bet, "source", "betType"),
        "entryTimestamp": _first(bet, "entryTimestamp", "loggedAt", "closingLineTimestamp"),
        "modelVersion": bet.get("modelVersion"),
        "joinMethod": join_method,

        "entryPricePct": entry_price_pct,
        "entryAmerican": entry_price_raw if isinstance(entry_price_raw, (int, float)) else None,
        "entryBidPct": _to_pct(bet.get("entryBidPct")),
        "entryAskPct": _to_pct(bet.get("entryAskPct")),
        "entryMidPct": _to_pct(bet.get("entryMidPct")),
        "closingAskPct": bet.get("closingAskPct"),
        "closingMidPct": bet.get("closingMidPct"),
        "closingAmerican": bet.get("closingPriceAmerican"),
        "closingTimestamp": _first(bet, "closingLineTimestamp", "closingTimestamp"),
        "clvAskPct": bet.get("clvAskPct"),
        "clvMidPct": bet.get("clvMidPct"),
        "clvCaptureStatus": _first(bet, "clvCaptureStatus", "clvStatus"),
        "closingLineUnavailableReason": bet.get("closingLineUnavailableReason"),

        "modelProbPct": _to_pct(_first(bet, "modelPct", "trueProbPct", "probabilityPct")),
        "marketImpliedPctAtEntry": _to_pct(_first(bet, "kalshiPct", "marketImpliedProb")),
        "projectedEdgePct": bet.get("edgePct"),
        "expectedProfitPerDollar": None,
        "confidenceTier": _first(bet, "confidenceTier", "confidence"),
        "recommendationStatus": bet.get("status"),
        "modelSupportStatus": "SUPPORTED" if family in BINARY_MARKET_FAMILIES else None,

        "stake": _first(bet, "stake", "betSize", "size"),
        "result": outcome,
        "grossReturn": financials["grossReturn"],
        "netProfit": financials["netProfit"],
        "roiPct": financials["roiPct"],

        "lineupConfirmationStatus": bet.get("lineupConfirmed"),
        "awayPitcher": bet.get("awayPitcher"),
        "homePitcher": bet.get("homePitcher"),
        "bullpenState": None,
        "park": bet.get("park"),
        "weather": bet.get("weather"),
        "umpire": bet.get("umpire"),
        "favoriteOrUnderdog": None,
        "horizon": period,
        "manualAnalysisNotes": bet.get("notes"),
        "reasonCodes": bet.get("gatesFired"),
        "exactContractMetadata": {
            "seriesTicker": bet.get("seriesTicker"),
            "scheduledStartTime": bet.get("scheduledStartTime"),
            "betBook": bet.get("betBook"),
        },

        "dataQualityStatus": data_quality_status,
        "dataQualityFlags": data_quality_flags,

        "trackingType": resolve_tracking_type(bet),
        "countsTowardBankroll": True,
        "hypotheticalStake": None,
        "hypotheticalNetProfit": None,
        "hypotheticalRoiPct": None,
        "realMoneyBlockReasons": [],
    }
    return row


def build_paper_row(paper_row):
    """
    Converts one row from data/research/paper_spread_ledger.jsonl (see
    scripts/build_paper_spread_ledger.py) into the SAME canonical
    schema real wagers use, so both appear side by side in one research
    database -- but with trackingType="PAPER", countsTowardBankroll=
    False, and its profit/loss carried ONLY in hypotheticalNetProfit/
    hypotheticalRoiPct, never in `netProfit`/`roiPct`/`stake` (those
    stay null here) -- this is the structural guarantee against ever
    mixing hypothetical paper profit with real bankroll profit.
    """
    date = paper_row.get("date")
    ticker = paper_row.get("ticker")
    return {
        "betId": f"PAPER:{date}:{ticker}",
        "date": date,
        "gameId": paper_row.get("gameId"),
        "game": (f"{paper_row['awayTeam']} @ {paper_row['homeTeam']}"
                 if paper_row.get("awayTeam") and paper_row.get("homeTeam") else None),
        "ticker": ticker,
        "eventTicker": None,
        "marketFamily": paper_row.get("marketFamily"),
        "market": None,
        "period": paper_row.get("period"),
        "side": paper_row.get("side"),
        "line": paper_row.get("line"),
        "subjectType": None,
        "subjectName": paper_row.get("side"),
        "source": "PAPER_SPREAD_LEDGER",
        "entryTimestamp": None,
        "modelVersion": None,
        "joinMethod": "exact_ticker",

        "entryPricePct": paper_row.get("entryAskPct"),
        "entryAmerican": None,
        "entryBidPct": None,
        "entryAskPct": paper_row.get("entryAskPct"),
        "entryMidPct": paper_row.get("entryMidpointPct"),
        "closingAskPct": paper_row.get("closingAskPct"),
        "closingMidPct": paper_row.get("closingMidpointPct"),
        "closingAmerican": None,
        "closingTimestamp": None,
        "clvAskPct": paper_row.get("clvAskPct"),
        "clvMidPct": paper_row.get("clvMidPct"),
        "clvCaptureStatus": None,
        "closingLineUnavailableReason": None,

        "modelProbPct": paper_row.get("fairProbabilityPct"),
        "marketImpliedPctAtEntry": paper_row.get("entryAskPct"),
        "projectedEdgePct": paper_row.get("rawEdgePct"),
        "expectedProfitPerDollar": None,
        "confidenceTier": paper_row.get("confidenceTier"),
        "recommendationStatus": "PAPER_ONLY_REAL_MONEY_BLOCKED",
        "modelSupportStatus": "SUPPORTED",

        "stake": None,
        "result": paper_row.get("result", "PENDING"),
        "grossReturn": None,
        "netProfit": None,
        "roiPct": None,

        "lineupConfirmationStatus": None,
        "awayPitcher": None,
        "homePitcher": None,
        "bullpenState": None,
        "park": None,
        "weather": None,
        "umpire": None,
        "favoriteOrUnderdog": None,
        "horizon": paper_row.get("period"),
        "manualAnalysisNotes": None,
        "reasonCodes": paper_row.get("realMoneyBlockReasons") or [],
        "exactContractMetadata": {"rank": paper_row.get("rank"), "alternateLine": paper_row.get("alternateLine")},

        "dataQualityStatus": "CLEAN",
        "dataQualityFlags": [],

        "trackingType": "PAPER",
        "countsTowardBankroll": False,
        "hypotheticalStake": paper_row.get("hypotheticalStake"),
        "hypotheticalNetProfit": paper_row.get("hypotheticalNetProfit"),
        "hypotheticalRoiPct": paper_row.get("hypotheticalRoiPct"),
        "realMoneyBlockReasons": paper_row.get("realMoneyBlockReasons") or [],
    }


def load_paper_ledger(path=None):
    path = path or PAPER_SPREAD_LEDGER_PATH
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_bets(path=None):
    path = path or BETS_PATH
    with open(path) as f:
        return json.load(f)


def build_rows(bets):
    """Pure: bets -> canonical rows, sorted deterministically by (date, betId)."""
    per_date_counter = defaultdict(int)
    rows = []
    for bet in bets:
        date = bet.get("date") or "UNKNOWN_DATE"
        idx = per_date_counter[date]
        per_date_counter[date] += 1
        rows.append(build_row(bet, idx))
    rows.sort(key=lambda r: (r["date"] or "", str(r["betId"] or "")))
    return rows


def build_calibration_bins(rows, bin_width=10):
    """
    Calibration bins for settled BINARY wagers with a valid model
    probability. Excludes pushes, voids, pending bets, and any market
    family not in BINARY_MARKET_FAMILIES (a binary win/loss framing is
    not meaningful for e.g. an unclassified/unsupported market). Does
    NOT modify model calibration -- purely descriptive.
    """
    eligible = [
        r for r in rows
        if r["result"] in ("WIN", "LOSS")
        and r["marketFamily"] in BINARY_MARKET_FAMILIES
        and r["modelProbPct"] is not None
    ]
    bins = defaultdict(list)
    for r in eligible:
        p = min(99.999, max(0.0, r["modelProbPct"]))
        bin_idx = int(p // bin_width)
        bins[bin_idx].append(r)

    out = []
    for bin_idx in sorted(bins):
        members = bins[bin_idx]
        n = len(members)
        avg_pred = round(sum(m["modelProbPct"] for m in members) / n, 3)
        wins = sum(1 for m in members if m["result"] == "WIN")
        actual_win_rate = round(wins / n * 100, 3)
        clv_vals = [m["clvMidPct"] for m in members if m["clvMidPct"] is not None]
        avg_clv = round(sum(clv_vals) / len(clv_vals), 3) if clv_vals else None
        roi_vals = [m["roiPct"] for m in members if m["roiPct"] is not None]
        avg_roi = round(sum(roi_vals) / len(roi_vals), 3) if roi_vals else None
        out.append({
            "binLabel": f"{bin_idx * bin_width}-{bin_idx * bin_width + bin_width}%",
            "sampleSize": n,
            "avgPredictedProbabilityPct": avg_pred,
            "actualWinRatePct": actual_win_rate,
            "calibrationErrorPct": round(actual_win_rate - avg_pred, 3),
            "avgClvMidPct": avg_clv,
            "avgRoiPct": avg_roi,
        })
    return out


def build_report(rows, bets_count):
    join_methods = defaultdict(int)
    quality = defaultdict(int)
    flags = defaultdict(int)
    for r in rows:
        join_methods[r["joinMethod"]] += 1
        quality[r["dataQualityStatus"]] += 1
        for f in r["dataQualityFlags"]:
            flags[f] += 1
    return {
        "sourceBetsCount": bets_count,
        "canonicalRowsCount": len(rows),
        "rowsDroppedCount": bets_count - len(rows),
        "joinMethodCounts": dict(join_methods),
        "dataQualityStatusCounts": dict(quality),
        "dataQualityFlagCounts": dict(flags),
    }


def write_jsonl(rows, path):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            flat = dict(r)
            for k, v in flat.items():
                if isinstance(v, (list, dict)):
                    flat[k] = json.dumps(v, sort_keys=True)
            writer.writerow(flat)


def write_schema(path):
    schema = {
        "fields": CANONICAL_FIELDS,
        "note": "Missing values are always null, never zero or a fabricated placeholder. "
                "See docs/WAGER_RESEARCH_DATABASE.md for field-by-field definitions.",
    }
    with open(path, "w") as f:
        json.dump(schema, f, indent=2)


def main(bets_path=None, out_dir=None, dry_run=False, paper_ledger_path=None):
    out_dir = out_dir or RESEARCH_DIR
    bets = load_bets(bets_path)
    real_rows = build_rows(bets)

    paper_ledger_rows = load_paper_ledger(paper_ledger_path)
    paper_rows = [build_paper_row(p) for p in paper_ledger_rows]
    paper_rows.sort(key=lambda r: (r["date"] or "", str(r["betId"] or "")))

    rows = real_rows + paper_rows
    # Calibration/report remain scoped to REAL/MANUAL (bankroll-counting)
    # wagers only -- paper spread performance is reported separately
    # (scripts/generate_wager_research_report.py's paper section) so it
    # is never blended into the real-money calibration/quality picture.
    calibration = build_calibration_bins(real_rows)
    report = build_report(real_rows, len(bets))
    report["paperRowsCount"] = len(paper_rows)

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        write_jsonl(rows, os.path.join(out_dir, "wagers.jsonl"))
        write_csv(rows, os.path.join(out_dir, "wagers.csv"))
        write_schema(os.path.join(out_dir, "schema.json"))
        with open(os.path.join(out_dir, "build_report.json"), "w") as f:
            json.dump(report, f, indent=2)
        with open(os.path.join(out_dir, "calibration_bins.json"), "w") as f:
            json.dump(calibration, f, indent=2)

    return {"rows": rows, "realRows": real_rows, "paperRows": paper_rows,
            "calibration": calibration, "report": report}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result["report"], indent=2))
