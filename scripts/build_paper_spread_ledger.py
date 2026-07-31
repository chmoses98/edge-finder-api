#!/usr/bin/env python3
"""
scripts/build_paper_spread_ledger.py
========================================
Spread-correction mission, Part 1/5 -- persistent PAPER-ONLY wager
ledger for every spread (winning_margin family, any period) contract
that clears the SAME minimum-edge floor production uses for its other
markets (THRESHOLD_PAPER, imported from scripts/build_market_ledger.py,
never reimplemented) but is real-money BLOCKED (Rule 81 for full-game,
"never yet activated" for F3/F5/F7 -- see
docs/SPREAD_ANALYSIS_AND_ACTIVATION_POLICY.md).

WHY A SEPARATE LEDGER FROM bets.json
--------------------------------------
bets.json is the canonical REAL-MONEY wager ledger (Phase 3's
scripts/build_wager_research_db.py docstring: "Use the root bets.json
as the canonical wager ledger"). A hypothetical/paper position was
never placed, has no real stake, and must never be counted toward
real bankroll ROI -- mixing it into bets.json (or fabricating a fake
bet row there) would violate that separation. This ledger is
append-only and keyed by (date, ticker) so a rerun against unchanged
discovery data never produces a duplicate row (idempotent, matching
the existing precedent in scripts/write_pending_bets.py's
existing_keys pattern).

Entry-time capture happens the MOMENT this script runs against a
day's discovery output -- the fair probability, executable ask, and
edge at analysis time can never be reconstructed later once the market
closes, so this script's job is capture-now, settle-later (via
settle_paper_spread_row(), called separately once final scores are
available -- see module docstring below for why automated live
settlement wiring is intentionally left as a documented follow-up
rather than guessed at: bets.json settlement in this repository
appears to be human-curated (BET_LOG.md), not a fully-automated
pipeline this phase should invent a new pattern for).

Writes (append-only, idempotent):
    data/research/paper_spread_ledger.jsonl
"""
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

from scripts.build_market_ledger import THRESHOLD_PAPER, confidence_from_edge  # noqa: E402

DEFAULT_LEDGER_PATH = os.path.join(ROOT_DIR, "data", "research", "paper_spread_ledger.jsonl")

# Flat, nominal per-wager unit for paper tracking -- deliberately NOT
# derived from any real bankroll/Kelly sizing logic (that logic is
# production-only and this mission must not duplicate or guess at it).
# Used solely so ROI/net-profit math has a consistent denominator
# across every paper row; never mixed with real bankroll profit.
HYPOTHETICAL_UNIT_STAKE = 5.0


def _row_key(row):
    return (row["date"], row["ticker"])


def load_existing_rows(ledger_path):
    if not os.path.exists(ledger_path):
        return []
    rows = []
    with open(ledger_path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


_PAPER_TRACKED_FAMILIES = {"winning_margin", "inning_result"}


def build_paper_rows(date_str, discovery_contracts):
    """
    Pure. Returns paper-ledger rows for every discovered contract that
    is real-money BLOCKED (spread of any period, or an F3/F7 winner
    market whose structure was just independently verified -- see
    docs/SPREAD_ANALYSIS_AND_ACTIVATION_POLICY.md), SUPPORTED, and
    clears THRESHOLD_PAPER on raw edge -- production's existing
    minimum-edge floor, reused rather than reinvented. A contract
    below the floor is analyzed/ranked (visible in the discovery
    artifact) but is NOT a paper wager -- exactly mirroring how
    production's own Rejected-for-no-qualifying-edge rows are not
    logged to bets.json either. Despite this module's filename, it
    covers both families since both share the identical "newly
    modeled, never real-money activated" policy.
    """
    rows = []
    for c in discovery_contracts:
        if c.get("marketFamily") not in _PAPER_TRACKED_FAMILIES:
            continue
        if c.get("modelSupportStatus") != "SUPPORTED":
            continue
        if c.get("realMoneyEligibilityStatus") != "BLOCKED":
            continue
        # Single source of truth for "is this contract eligible to
        # become a paper wager" -- includes the pregame/live-game gate
        # (scripts/discover_kalshi_mlb_markets.py's compute_status_fields()),
        # not re-derived here.
        if c.get("paperTrackingStatus") != "ELIGIBLE":
            continue
        edge = c.get("rawEdgePct")
        if edge is None or edge < THRESHOLD_PAPER:
            continue

        rows.append({
            "date": date_str,
            "ticker": c.get("ticker"),
            "gameId": c.get("gameId"),
            "awayTeam": c.get("awayTeam"),
            "homeTeam": c.get("homeTeam"),
            "marketFamily": c.get("marketFamily"),
            "period": c.get("period"),
            "side": c.get("side"),
            "line": c.get("line"),
            "alternateLine": c.get("alternateLine"),
            "fairProbabilityPct": c.get("fairProbabilityPct"),
            "entryAskPct": c.get("yesAsk"),
            "entryMidpointPct": (
                round((c["yesBid"] + c["yesAsk"]) / 2, 3)
                if (c.get("yesBid") is not None and c.get("yesAsk") is not None) else None
            ),
            "rawEdgePct": edge,
            "calibratedEdgePct": None,  # no established calibration factor for this family yet
            "confidenceTier": confidence_from_edge(edge),
            "rank": c.get("rank"),
            "hypotheticalStake": HYPOTHETICAL_UNIT_STAKE,
            "trackingType": "PAPER",
            "countsTowardBankroll": False,
            "realMoneyBlockReasons": c.get("realMoneyBlockReasons") or [],
            "closingAskPct": None,
            "closingMidpointPct": None,
            "clvAskPct": None,
            "clvMidPct": None,
            "result": "PENDING",
            "hypotheticalNetProfit": None,
            "hypotheticalRoiPct": None,
            "settledAt": None,
        })
    return rows


def settle_paper_spread_row(row, away_final_score, home_final_score):
    """
    Pure. Settles one PENDING paper row from a final game score using
    the SAME "wins by over N.5 runs" semantics
    lib.kalshi_mlb_market_classifier already establishes for this
    family: YES (this row's `side` team) wins iff
    (side_score - opp_score) > line. Returns a NEW dict (does not
    mutate `row`) with result/hypotheticalNetProfit/hypotheticalRoiPct
    filled in; leaves them untouched (still PENDING) if the side team
    cannot be matched to away/home or scores are missing -- never
    guesses.
    """
    out = dict(row)
    if away_final_score is None or home_final_score is None:
        return out
    side = row.get("side")
    away, home = row.get("awayTeam"), row.get("homeTeam")
    line = row.get("line")
    if side is None or line is None or side not in (away, home):
        return out

    side_score = away_final_score if side == away else home_final_score
    opp_score = home_final_score if side == away else away_final_score
    margin = side_score - opp_score

    stake = row.get("hypotheticalStake") or 0.0
    entry_ask = row.get("entryAskPct")
    if entry_ask is None or entry_ask <= 0:
        return out

    won = margin > line
    out["result"] = "WIN" if won else "LOSS"
    if won:
        gross = stake / (entry_ask / 100.0)
        out["hypotheticalNetProfit"] = round(gross - stake, 4)
    else:
        out["hypotheticalNetProfit"] = round(-stake, 4)
    out["hypotheticalRoiPct"] = round(out["hypotheticalNetProfit"] / stake * 100, 3) if stake else None
    return out


def append_rows(ledger_path, new_rows):
    """
    Idempotent append: rows whose (date, ticker) key already exists in
    the ledger are skipped, never duplicated. Returns the count of
    genuinely new rows appended.
    """
    existing = load_existing_rows(ledger_path)
    existing_keys = {_row_key(r) for r in existing}
    to_append = [r for r in new_rows if _row_key(r) not in existing_keys]

    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    if to_append:
        with open(ledger_path, "a") as f:
            for r in to_append:
                f.write(json.dumps(r) + "\n")
    return len(to_append)


def main(date_str=None, discovery_path=None, ledger_path=None):
    ledger_path = ledger_path or DEFAULT_LEDGER_PATH

    if discovery_path is None:
        discovery_dir = os.path.join(ROOT_DIR, "data", "kalshi", "discovery")
        date_str = date_str or None
        if date_str is None:
            print("[build_paper_spread_ledger] No date_str or discovery_path given -- nothing to do")
            return {"appended": 0, "status": "NO_INPUT"}
        discovery_path = os.path.join(discovery_dir, f"{date_str}.json")

    try:
        with open(discovery_path) as f:
            discovery_doc = json.load(f)
    except FileNotFoundError:
        print(f"[build_paper_spread_ledger] No discovery file at {discovery_path} -- nothing to do")
        return {"appended": 0, "status": "NO_DISCOVERY_FILE"}

    date_str = date_str or discovery_doc.get("date")
    contracts = discovery_doc.get("contracts") or []
    rows = build_paper_rows(date_str, contracts)
    appended = append_rows(ledger_path, rows)
    result = {"date": date_str, "candidateRows": len(rows), "appended": appended, "status": "OK"}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    main(date_str=arg_date)
