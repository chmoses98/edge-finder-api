#!/usr/bin/env python3
"""
tests/edgelab/test_collect_clv.py
=====================================
Maintainer review of PR #37 (item 7). Re-verified against the real
2026-08-01/08-02 data that the confirmed "620/4844 (462/4135) tickers
have more than one ClvQuote row" statistic does NOT currently mean any
ticker has more than one row flagged isClosingQuote=True -- real data
shows 0 ambiguous tickers on both dates; every multi-row ticker already
resolves to exactly one closing quote via the existing
finalize_closing_quotes()/select_closing_quote() mechanism. The original
bug this milestone fixed was on the CONSUMER side
(lib.edgelab.replay._closing_clv_by_ticker, tested in
test_production_provenance.py): the old replay code picked "whichever row
happens to be last in file iteration order" instead of checking
isClosingQuote at all, which is unrelated to file order.

This file covers a DIFFERENT, theoretical gap found while tracing that
logic -- not yet observed in real committed data, but real code today
does not guard against it: project_observations_to_clv_quotes()
classifies each observation's checkpoint using is_first_of_day=(i==0)
relative to THIS call's own observation list. Under real monotonic
(always-append) capture that index never changes across reruns, so this
is unreachable in the ordinary case -- but a backfill/reprocessing run
that ingests a previously-missing, genuinely EARLIER observation for a
ticker (a supported, real recovery pattern elsewhere in this repository)
could still shift it, reclassifying a previously-FIRST_DAILY row to a
non-standard checkpoint and silently dropping it from that run's
projection -- orphaning its isClosingQuote flag if it had been set, since
finalize_closing_quotes() would never revisit it. The fix in
scripts/edgelab/collect_clv.py re-runs finalize_closing_quotes() over the
FULL known history for a ticker (existing stored rows unioned with
freshly projected ones) every time, closing that gap defensively.
"""
import gzip
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab import storage  # noqa: E402

DATE = "2026-07-31"
TICKER = "KXMLBML-26JUL312200DETATH-DET"
SCHEDULED_START = "2026-07-31T22:00:00Z"


def _obs(captured_at, yes_bid=50, yes_ask=52):
    return {
        "marketObservationId": f"{TICKER}|{captured_at}",
        "marketTicker": TICKER,
        "capturedAt": captured_at,
        "gameId": "g1",
        "scheduledStart": SCHEDULED_START,
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": None,
        "noAsk": None,
        "lastPrice": None,
        "marketStatus": "active",
        "checkpoint": None,
        "provenance": {"sourceSystem": "test", "sourceFile": "test", "sourceKey": TICKER, "capturedAt": captured_at},
    }


def _write_observations(tmp_path, observations):
    _write_observations_for_date(tmp_path, DATE, observations)


def _write_observations_for_date(tmp_path, date, observations):
    path = os.path.join(tmp_path, storage.partition_path("observations", date, compressed=True))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for obs in observations:
            f.write(json.dumps(obs) + "\n")


def _run_collect_clv(tmp_path):
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab", "collect_clv.py"), "--date", DATE],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result


def _read_stored_quotes(tmp_path):
    path = os.path.join(tmp_path, storage.partition_path("clv_quotes", DATE))
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_reclassified_early_observation_does_not_orphan_stale_closing_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Run 1: only ONE observation exists so far this ticker, well before
    # scheduled start -- it is index 0 in this run's own (short) list, so
    # it is classified FIRST_DAILY (a standard checkpoint, always kept)
    # and, being the only candidate, becomes the closing quote AS OF NOW.
    obs_a = _obs("2026-07-31T19:00:00Z")
    _write_observations(tmp_path, [obs_a])
    _run_collect_clv(tmp_path)

    stored_after_run1 = _read_stored_quotes(tmp_path)
    assert len(stored_after_run1) == 1
    assert stored_after_run1[0]["isClosingQuote"] is True
    assert stored_after_run1[0]["checkpoint"] == "FIRST_DAILY"

    # Run 2: two more observations have arrived since -- a genuinely
    # earlier one (now the real FIRST_DAILY) and a late, near-start one
    # (the real closing quote). obs_a is no longer index 0, so on
    # reclassification it lands on INTERMEDIATE (180 minutes before start
    # matches no standard checkpoint target) and -- absent this fix --
    # would be silently dropped from this run's own projection, leaving
    # its stale isClosingQuote=True untouched in the store.
    obs_z = _obs("2026-07-31T15:00:00Z")
    obs_b = _obs("2026-07-31T21:55:00Z")
    _write_observations(tmp_path, [obs_z, obs_a, obs_b])
    _run_collect_clv(tmp_path)

    stored_after_run2 = _read_stored_quotes(tmp_path)
    closing_rows = [q for q in stored_after_run2 if q["isClosingQuote"]]
    assert len(closing_rows) == 1, f"expected exactly one closing quote, got {closing_rows}"
    assert closing_rows[0]["clvQuoteId"] == obs_b["marketObservationId"]

    # obs_a's row must still exist (never silently deleted) -- just
    # correctly no longer flagged as the closing quote.
    obs_a_row = next(q for q in stored_after_run2 if q["clvQuoteId"] == obs_a["marketObservationId"])
    assert obs_a_row["isClosingQuote"] is False


def test_third_run_with_no_new_data_for_ticker_is_stable(tmp_path, monkeypatch):
    """A ticker whose own observation set is unchanged between runs must
    keep the same decision-relevant outcome (checkpoint classification,
    which single row is the closing quote) -- createdAt naturally
    refreshes on every re-projection of the same underlying data, which
    is pre-existing pipeline behavior unrelated to this fix, so that
    field is deliberately excluded from the comparison."""
    monkeypatch.chdir(tmp_path)
    obs_a = _obs("2026-07-31T19:00:00Z")
    obs_b = _obs("2026-07-31T21:55:00Z")
    _write_observations(tmp_path, [obs_a, obs_b])
    _run_collect_clv(tmp_path)
    first = _read_stored_quotes(tmp_path)

    other_ticker_obs = {**_obs("2026-07-31T20:00:00Z"), "marketTicker": "OTHER-TICKER",
                         "marketObservationId": "OTHER-TICKER|2026-07-31T20:00:00Z"}
    _write_observations(tmp_path, [obs_a, obs_b, other_ticker_obs])
    _run_collect_clv(tmp_path)
    second = _read_stored_quotes(tmp_path)

    def _decision_fields(rows):
        return {q["clvQuoteId"]: (q["isClosingQuote"], q["checkpoint"]) for q in rows if q["marketTicker"] == TICKER}

    assert _decision_fields(first) == _decision_fields(second)
    assert sum(1 for q in second if q["marketTicker"] == TICKER and q["isClosingQuote"]) == 1


def test_production_bet_clv_unchanged_in_ordinary_non_reclassifying_case(tmp_path, monkeypatch):
    """Item 14 (maintainer review of PR #37): this fix directly touches
    collect_clv.py, which back-fills clv/closingPrice onto the PRODUCTION
    bets.json ledger (compute_clv_for_bet), not just research output --
    so it must not silently alter that production value. Proves the fix
    is a no-op for the ordinary case (no reclassification-drop scenario):
    a real placed bet's computed CLV is byte-for-byte identical whether
    computed in one pass or across two collect_clv.py runs where the
    second run's own union-with-existing-history logic is exercised but
    has nothing to correct."""
    monkeypatch.chdir(tmp_path)
    bets_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    os.makedirs(os.path.dirname(bets_path), exist_ok=True)
    bet = {
        "schemaVersion": "1", "betId": "bet-1", "marketTicker": TICKER, "side": "YES",
        "entryPrice": 0.45, "status": "pending", "clv": None, "closingPrice": None, "clvQuoteId": None,
    }
    with open(bets_path, "w") as f:
        f.write(json.dumps(bet) + "\n")

    obs_a = _obs("2026-07-31T19:00:00Z")
    obs_b = _obs("2026-07-31T21:55:00Z")

    # One-pass baseline: both observations available from the start.
    _write_observations(tmp_path, [obs_a, obs_b])
    _run_collect_clv(tmp_path)
    with open(bets_path) as f:
        baseline_bet = json.loads(f.readline())

    # Reset and replay as two separate runs (obs_a first, obs_b later) --
    # exercises the new union-with-existing-history path, but since
    # neither observation gets reclassified out of a standard checkpoint
    # between runs, the outcome must be identical.
    with open(bets_path, "w") as f:
        f.write(json.dumps(bet) + "\n")
    os.remove(os.path.join(tmp_path, storage.partition_path("clv_quotes", DATE)))
    _write_observations(tmp_path, [obs_a])
    _run_collect_clv(tmp_path)
    _write_observations(tmp_path, [obs_a, obs_b])
    _run_collect_clv(tmp_path)
    with open(bets_path) as f:
        two_pass_bet = json.loads(f.readline())

    assert baseline_bet["clv"] == two_pass_bet["clv"]
    assert baseline_bet["closingPrice"] == two_pass_bet["closingPrice"]
    assert baseline_bet["clvQuoteId"] == two_pass_bet["clvQuoteId"]


def test_catchup_pass_matches_bet_imported_after_its_own_market_day(tmp_path, monkeypatch):
    """
    CLV Coverage Reliability mission: root cause of most decided-but-
    UNKNOWN-CLV bets in this repository's real production data was a bet
    imported/logged (e.g. via a historical postmortem import) AFTER its
    own market's day, whose gameDate's clv_quotes partition already had a
    correctly finalized closing quote -- but no collect_clv.py run was
    ever invoked again for that historical date, so the match never
    happened even though nothing was actually missing. This proves the
    fix: a bet whose gameDate differs from the run's own --date, but whose
    OWN gameDate already has an archived, finalized closing quote for its
    exact ticker, gets matched on a run for a LATER date.
    """
    monkeypatch.chdir(tmp_path)

    # First, populate DATE's clv_quotes archive with a real finalized
    # closing quote the ordinary way (simulates that date's own normal
    # capture/collection having already happened, long before the bet
    # below ever existed).
    _write_observations(tmp_path, [_obs("2026-07-31T19:00:00Z"), _obs("2026-07-31T21:55:00Z")])
    _run_collect_clv(tmp_path)
    stored = _read_stored_quotes(tmp_path)
    assert any(q["isClosingQuote"] for q in stored)

    # Now a bet for that SAME ticker/gameDate is imported/logged with no
    # observations for it captured under a LATER date (the run below uses
    # a different --date entirely -- this bet's own market data lives
    # only under DATE, which this later run never directly reads).
    bets_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    os.makedirs(os.path.dirname(bets_path), exist_ok=True)
    late_bet = {
        "schemaVersion": "1", "betId": "late-imported-bet", "marketTicker": TICKER, "side": "YES",
        "entryPrice": 0.40, "status": "settled", "clv": None, "closingPrice": None, "clvQuoteId": None,
        "gameDate": DATE,
    }
    with open(bets_path, "w") as f:
        f.write(json.dumps(late_bet) + "\n")

    later_date = "2026-08-04"
    _write_observations_for_date(tmp_path, later_date, [])  # no games that day; run still executes
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab", "collect_clv.py"), "--date", later_date],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    with open(bets_path) as f:
        updated = json.loads(f.readline())
    assert updated["clv"] is not None
    assert updated["closingPrice"] is not None
    assert updated["clvQuoteId"] is not None

    # The later run's own research_run record must show the catch-up path
    # explicitly, never silently folded into ordinary same-day counts.
    run_records_path = os.path.join(tmp_path, storage.partition_path("research_runs", later_date))
    with open(run_records_path) as f:
        run_records = [json.loads(line) for line in f if line.strip()]
    clv_run = next(r for r in run_records if r["runType"] == "CLV_COLLECTION")
    assert clv_run["counts"]["betClvComputedViaCatchup"] == 1


def test_catchup_pass_never_matches_a_bet_with_no_archived_quote_for_its_own_date():
    """A bet whose own gameDate has no archived closing quote at all must stay UNKNOWN -- the catch-up pass never infers or fabricates one."""
    from lib.edgelab.clv import compute_clv_for_bet
    bet = {"betId": "b", "marketTicker": "NOPE-TICKER", "side": "YES", "entryPrice": 0.5, "gameDate": "2026-07-01"}
    result = compute_clv_for_bet(bet, [])
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"


def test_catchup_pass_skips_bet_already_carrying_clv(tmp_path, monkeypatch):
    """A bet that already has clv computed must never be re-touched by the catch-up pass, regardless of gameDate."""
    monkeypatch.chdir(tmp_path)
    bets_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    os.makedirs(os.path.dirname(bets_path), exist_ok=True)
    bet = {
        "schemaVersion": "1", "betId": "already-has-clv", "marketTicker": TICKER, "side": "YES",
        "entryPrice": 0.45, "status": "settled", "clv": 3.5, "closingPrice": 0.415, "clvQuoteId": "existing-quote-id",
        "gameDate": "2026-06-01", "updatedAt": "2026-06-02T00:00:00Z",
    }
    with open(bets_path, "w") as f:
        f.write(json.dumps(bet) + "\n")

    _write_observations_for_date(tmp_path, "2026-08-04", [])
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab", "collect_clv.py"), "--date", "2026-08-04"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with open(bets_path) as f:
        unchanged = json.loads(f.readline())
    assert unchanged["updatedAt"] == "2026-06-02T00:00:00Z"
    assert unchanged["clv"] == 3.5


def test_cancelled_bet_never_gets_clv_computed(tmp_path, monkeypatch):
    """
    Maintainer review regression (Canonical Placed-Bet Ledger milestone):
    a CANCELLED bet (logged in error -- lib.edgelab.bets.cancel_placed_bet)
    must never gain a computed clv/closingPrice/clvQuoteId -- it isn't a
    real wager, so it shouldn't be treated as one by the CLV pipeline.
    """
    monkeypatch.chdir(tmp_path)
    bets_path = os.path.join("data", "edgelab", "bets", "bets.jsonl")
    os.makedirs(os.path.dirname(bets_path), exist_ok=True)
    active_bet = {
        "schemaVersion": "1", "betId": "active-bet", "marketTicker": TICKER, "side": "YES",
        "entryPrice": 0.45, "status": "pending", "clv": None, "closingPrice": None, "clvQuoteId": None,
        "recordStatus": "ACTIVE",
    }
    cancelled_bet = {
        "schemaVersion": "1", "betId": "cancelled-bet", "marketTicker": TICKER, "side": "YES",
        "entryPrice": 0.45, "status": "pending", "clv": None, "closingPrice": None, "clvQuoteId": None,
        "recordStatus": "CANCELLED",
    }
    with open(bets_path, "w") as f:
        f.write(json.dumps(active_bet) + "\n")
        f.write(json.dumps(cancelled_bet) + "\n")

    _write_observations(tmp_path, [_obs("2026-07-31T19:00:00Z"), _obs("2026-07-31T21:55:00Z")])
    _run_collect_clv(tmp_path)

    with open(bets_path) as f:
        parsed = [json.loads(line) for line in f if line.strip()]
    rows = {row["betId"]: row for row in parsed}
    assert rows["active-bet"]["clv"] is not None
    assert rows["cancelled-bet"]["clv"] is None
    assert rows["cancelled-bet"]["clvQuoteId"] is None
