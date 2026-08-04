#!/usr/bin/env python3
"""
scripts/edgelab/import_bet_batch.py
=======================================
Timestamp-Optional Manual Imports milestone: bulk import surface for a
"normal bet list" (e.g. from a ChatGPT betting-session handoff) --
accepts a JSON file or inline JSON payload of multiple wagers and writes
every one of them through the ONE canonical write path
(lib.edgelab.bets.write_placed_bet), exactly like scripts/edgelab/log_bet.py
and the "Record Placed Bet" GitHub Actions form. No other script may
append to data/edgelab/bets/bets.jsonl for a new bet.

A normal row only needs: gameDate, a way to identify the game (away/home
or matchup), a market description (marketFamily/marketHorizon/team/
player/threshold, OR an exact marketTicker when already known), stake,
and entryPrice or entryOdds. No exact placement timestamp is required --
entryTimestamp is optional; when omitted, this bet's identity comes from
hash(importBatchId, sourceRow, marketTicker, side) instead (see
lib.edgelab.ids.build_bet_id), so re-running the identical batch is a
pure no-op, two real separate rows in the same file (or across two files
with a different importBatchId) are never confused, AND -- critically --
fixing one ambiguous/invalid row and resubmitting the same file never
re-writes or duplicates the rows that already succeeded: importBatchId
defaults to a hash of the payload's own gameDate(s) rather than the raw
row content, so it stays stable across that kind of same-day correction
(see main()'s comment for the full reasoning, and
tests/edgelab/test_import_bet_batch.py's
test_rerunning_a_corrected_mixed_batch_is_idempotent_for_the_already_written_row).

Ticker resolution (when marketTicker is not supplied): matches the
archived market corpus (data/edgelab/games/<date>.jsonl +
data/edgelab/markets/<date>.jsonl) on game + family + horizon + team/
player + threshold (lib.edgelab.ticker_resolution.resolve_ticker).
Refuses ambiguous matches -- that row is left UNRESOLVED (never written,
never guessed) and the receipt lists every candidate ticker found.

Bet-to-observation linkage (once a ticker is known, resolved or given):
lib.edgelab.observation_linkage.link_bet_to_observation finds the best
archived pregame MarketObservation for comparison -- never claimed as
the actual placement time.

Input shape (JSON):
  Either a bare list of row objects, or {"importBatchId": "<label>",
  "rows": [...]}. Each row:
    {
      "gameDate": "2026-08-03",           (required)
      "away": "SF", "home": "LAD",        (or "matchup": "SF@LAD")
      "marketTicker": null,               (exact ticker, when known -- skips resolution)
      "marketFamily": "game_result", "marketHorizon": "F5",
      "team": "SF", "player": null, "threshold": null,
      "side": "YES",
      "selection": "SF F5 moneyline",     (optional -- derived if omitted)
      "stake": 12.0,
      "entryPrice": null, "entryOdds": 128,  (exactly one of these two)
      "entryTimestamp": null,             (optional -- omit for timestamp-free entry)
      "confidence": null, "rationale": null, "tags": []
    }

Usage:
    python3 scripts/edgelab/import_bet_batch.py --file bets.json [--receipts-out receipts.json]
    python3 scripts/edgelab/import_bet_batch.py --json '[{...}, {...}]'

Exit codes: 0 if every row wrote successfully (NEW/DUPLICATE_NOOP/
CORRECTED); 1 if any row failed (unresolved ticker, schema-invalid,
unresolved conflict) -- the full per-row receipt list is still always
printed/written so a partially-successful batch is never silently lossy.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage, tags as tags_mod
from lib.edgelab.bets import american_odds_to_probability, build_manual_bet_record, write_placed_bet
from lib.edgelab.observation_linkage import link_bet_to_observation
from lib.edgelab.ticker_resolution import AMBIGUOUS, NOT_FOUND, RESOLVED, resolve_ticker


def _parse_matchup(row):
    away, home = row.get("away"), row.get("home")
    if away and home:
        return away, home
    matchup = row.get("matchup")
    if matchup and "@" in matchup:
        a, h = matchup.split("@", 1)
        return a.strip() or None, h.strip() or None
    return None, None


def _load_game_and_market_dims(game_date):
    games = list(storage.read_records(storage.partition_path("games", game_date)))
    markets = list(storage.read_records(storage.partition_path("markets", game_date)))
    return games, markets


def _resolve_game(games, away, home):
    for g in games:
        if g.get("awayTeam") == away and g.get("homeTeam") == home:
            return g
    return None


def _unresolved_receipt(row, index, reason, candidates=None):
    return {
        "sourceRow": index,
        "success": False,
        "duplicateStatus": "UNRESOLVED",
        "betId": None,
        "marketTicker": row.get("marketTicker"),
        "stake": row.get("stake"),
        "entryPrice": row.get("entryPrice"),
        "timestampStatus": "PROVIDED" if row.get("entryTimestamp") else "NOT_PROVIDED",
        "linkageStatus": "UNLINKED",
        "errors": [reason],
        "ambiguityCandidates": candidates or [],
    }


def _resolve_entry_price(row):
    """Returns (entry_price, entry_odds, error_or_None). Exactly one of entryPrice/entryOdds must be supplied."""
    entry_price, entry_odds = row.get("entryPrice"), row.get("entryOdds")
    if entry_price is not None and entry_odds is not None:
        return None, None, "row supplies both entryPrice and entryOdds -- supply exactly one"
    if entry_price is None and entry_odds is None:
        return None, None, "row must supply entryPrice or entryOdds"
    if entry_price is not None:
        return float(entry_price), None, None
    try:
        return american_odds_to_probability(entry_odds), float(entry_odds), None
    except ValueError as exc:
        return None, None, str(exc)


def process_row(row, index, import_batch_id):
    game_date = row.get("gameDate")
    if not game_date:
        return _unresolved_receipt(row, index, "row is missing required field 'gameDate'")
    stake = row.get("stake")
    if stake is None:
        return _unresolved_receipt(row, index, "row is missing required field 'stake'")

    entry_price, entry_odds, price_error = _resolve_entry_price(row)
    if price_error:
        return _unresolved_receipt(row, index, price_error)

    away, home = _parse_matchup(row)
    games, markets = _load_game_and_market_dims(game_date)
    game = _resolve_game(games, away, home) if (away and home) else None
    game_id = game.get("gameId") if game else None
    scheduled_start = game.get("scheduledStartTime") if game else None

    side = row.get("side") or "YES"
    market_ticker = row.get("marketTicker")
    resolved_market = None
    if not market_ticker:
        market_ticker, status, candidates = resolve_ticker(
            markets, games, away=away, home=home,
            market_family=row.get("marketFamily"), market_horizon=row.get("marketHorizon"),
            team=row.get("team"), player=row.get("player"), threshold=row.get("threshold"),
        )
        if status == NOT_FOUND:
            return _unresolved_receipt(row, index, "no market matched this row's game/family/threshold/participant in the archived corpus")
        if status == AMBIGUOUS:
            return _unresolved_receipt(
                row, index,
                "more than one archived market matches this row -- supply marketTicker directly or a more specific threshold/team/horizon",
                candidates=candidates,
            )
        assert status == RESOLVED
    else:
        resolved_market = next((m for m in markets if m.get("marketTicker") == market_ticker), None)

    market_family = row.get("marketFamily") or (resolved_market or {}).get("marketFamily")
    market_horizon = row.get("marketHorizon") or (resolved_market or {}).get("marketHorizon")
    threshold = row.get("threshold") if row.get("threshold") is not None else (resolved_market or {}).get("threshold")
    selection = row.get("selection") or f"{row.get('team') or (away or '')} {market_family or ''} {market_horizon or ''}".strip()

    tags = row.get("tags") or []
    if tags:
        try:
            tags_mod.validate_tags(tags)
        except ValueError as exc:
            return _unresolved_receipt(row, index, str(exc))

    linkage = link_bet_to_observation(market_ticker, game_date, side=side, scheduled_start=scheduled_start)

    entry_timestamp = row.get("entryTimestamp")
    build_kwargs = dict(
        game_id=game_id, game_date=game_date,
        matchup=f"{away} @ {home}" if away and home else row.get("matchup"),
        market_family=market_family, market_horizon=market_horizon,
        side=side, threshold=threshold, scheduled_start=scheduled_start,
        entry_odds=entry_odds, source="MANUAL", entry_method="IMPORTED_RECEIPT",
        confidence=row.get("confidence"), rationale=row.get("rationale"),
        thesis_tags=tags, market_observation_linkage=linkage,
    )
    # importBatchId/sourceRow are always recorded for traceability -- they
    # only become part of betId's IDENTITY when entry_timestamp is None
    # (see lib.edgelab.ids.build_bet_id); a row that supplies its own real
    # entryTimestamp still keeps the classic gameId+ticker+timestamp identity.
    record = build_manual_bet_record(
        market_ticker, selection, stake, entry_price, entry_timestamp,
        import_batch_id=import_batch_id, source_row=index, **build_kwargs,
    )

    receipt = write_placed_bet(record, on_conflict=row.get("onConflict", "reject"))
    receipt["sourceRow"] = index
    receipt["ambiguityCandidates"] = []
    return receipt


def load_payload(raw):
    payload = json.loads(raw)
    if isinstance(payload, list):
        return None, payload
    return payload.get("importBatchId"), payload.get("rows", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path to a JSON file containing the bet-list payload")
    group.add_argument("--json", help="Inline JSON payload (same shape as --file)")
    parser.add_argument("--receipts-out", default=None, help="Write the full JSON receipt list to this path in addition to stdout")
    args = parser.parse_args()

    raw = open(args.file).read() if args.file else args.json

    def _write_receipts(receipts):
        print(json.dumps(receipts, indent=2, sort_keys=True))
        if args.receipts_out:
            with open(args.receipts_out, "w") as f:
                json.dump(receipts, f, indent=2, sort_keys=True)

    try:
        explicit_batch_id, rows = load_payload(raw)
    except json.JSONDecodeError as exc:
        # A malformed top-level payload must still produce a receipts
        # file (never crash with a bare traceback and no artifact) -- the
        # calling workflow always inspects receipts.json, even on total
        # failure, so it can commit nothing and report cleanly rather
        # than erroring on a missing file.
        print(f"[import_bet_batch] payload is not valid JSON: {exc}", file=sys.stderr)
        _write_receipts([{
            "sourceRow": None, "success": False, "duplicateStatus": "INVALID",
            "betId": None, "marketTicker": None, "stake": None, "entryPrice": None,
            "timestampStatus": None, "linkageStatus": "UNLINKED",
            "errors": [f"payload is not valid JSON: {exc}"], "ambiguityCandidates": [],
        }])
        return 1

    if not rows:
        print("[import_bet_batch] payload contained no rows -- nothing to do", file=sys.stderr)
        _write_receipts([])
        return 0

    # A stable batch identity, so re-running the identical import is a
    # true no-op AND fixing one ambiguous/invalid row and resubmitting
    # the same file never re-writes (or duplicates) rows that already
    # succeeded. Deliberately NOT a hash of the full row list's raw
    # content: a correction typically only touches the failed row's own
    # ticker-resolution fields (marketTicker/marketFamily/threshold/etc),
    # and hashing the whole list would change EVERY row's identity the
    # moment any one row changes -- exactly the bug this fixes (see
    # tests/edgelab/test_import_bet_batch.py's
    # test_rerunning_a_corrected_mixed_batch_is_idempotent_for_the_already_written_row).
    # Instead this hashes the sorted set of distinct gameDate values
    # present in the payload: stable across a same-day correction (the
    # normal "fix one row, resubmit the same file" workflow), but
    # naturally distinct for a different day's batch. A caller that needs
    # finer control (e.g. two genuinely separate same-day sessions that
    # must never collide) should pass an explicit importBatchId, which
    # always wins over this default.
    game_dates = sorted({row.get("gameDate") for row in rows if row.get("gameDate")})
    import_batch_id = explicit_batch_id or ids.build_import_batch_id(*game_dates)

    receipts = [process_row(row, index, import_batch_id) for index, row in enumerate(rows)]
    _write_receipts(receipts)

    failures = [r for r in receipts if not r.get("success")]
    if failures:
        print(f"[import_bet_batch] {len(failures)}/{len(receipts)} row(s) NOT written -- see receipts for detail", file=sys.stderr)
        return 1
    print(f"[import_bet_batch] importBatchId={import_batch_id} rows={len(receipts)} all written", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
