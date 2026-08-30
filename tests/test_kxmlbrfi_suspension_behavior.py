"""Behavioural proof that the KXMLBRFI suspension actually fires.

Why this file exists, separately from tests/test_kxmlbrfi_suspension.py:

Every assertion in that file inspects the ledger's SOURCE TEXT. All of them
passed while the suspension was half-applied -- NRFI rejected, YRFI still
returning Accepted with a real-money bet size -- because they all asserted
the NRFI pattern and none of them ran the code. The bug was found by
running a real slate, not by reading one.

So these tests call evaluate_game() on committed archived slates and assert
on the ROWS IT RETURNS. If the suspension is ever half-applied again, or
regressed entirely, these fail even if every source-text assertion still
holds.
"""
import glob
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.build_market_ledger import evaluate_game  # noqa: E402

_NOT_STARTED = {"Scheduled", "Pre-Game"}


def _archived_games():
    """Every not-started game in every committed archived slate."""
    for path in sorted(glob.glob(os.path.join(_ROOT, "data", "pipeline", "*",
                                              "normalized_slate.json"))):
        try:
            payload = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        games = payload.get("data", {}).get("games") or payload.get("games") or []
        for g in games:
            if g.get("status") in _NOT_STARTED:
                yield os.path.basename(os.path.dirname(path)), g


def _rfi_rows():
    """(date, gameId, row) for every NRFI/YRFI row evaluate_game emits."""
    out = []
    for date, game in _archived_games():
        try:
            rows = evaluate_game(game)
        except Exception:
            continue
        if not isinstance(rows, list):
            rows = [rows]
        for row in rows:
            if isinstance(row, dict) and row.get("market") in ("NRFI", "YRFI"):
                out.append((date, game.get("gameId"), row))
    return out


ROWS = _rfi_rows()

# Games where the first-inning inputs are absent entirely emit a
# "Missing Data" row: no probability, no ticker, no confidence, no bet
# size. That is a pre-existing state the suspension does not govern and
# must not be confused with a suspended row -- so the suspension
# assertions are scoped to rows that actually reached evaluation, and
# Missing Data rows get their own (weaker but still real) assertions.
MISSING = "Missing Data"
EVALUATED = [(d, g, r) for d, g, r in ROWS if r.get("status") != MISSING]
MISSING_ROWS = [(d, g, r) for d, g, r in ROWS if r.get("status") == MISSING]


class TestTheSuspensionActuallyFires:
    def test_the_fixture_is_not_empty(self):
        """Guard against a vacuous pass.

        If the archived slates ever stop yielding RFI rows, every assertion
        below would pass over an empty list and this file would silently
        stop protecting anything."""
        assert ROWS, "no NRFI/YRFI rows produced from any archived slate"
        assert EVALUATED, "no NRFI/YRFI row reached evaluation"
        assert len({r["market"] for _, _, r in EVALUATED}) == 2, (
            "expected BOTH NRFI and YRFI rows in the fixture")

    @pytest.mark.parametrize("market", ["NRFI", "YRFI"])
    def test_no_side_is_ever_accepted(self, market):
        """The whole point of the suspension, asserted on real output."""
        accepted = [(d, g) for d, g, r in EVALUATED
                    if r["market"] == market and r.get("status") != "Rejected"]
        assert not accepted, (
            f"{market} produced {len(accepted)} non-Rejected rows, e.g. {accepted[:3]}")

    @pytest.mark.parametrize("market", ["NRFI", "YRFI"])
    def test_no_side_ever_carries_a_confidence_or_a_bet_size(self, market):
        """PAPER is a TIER, not a block -- bet_size('PAPER') returns 1.0, a
        real stake. This is the assertion that would have caught the
        original half-application, which left YRFI at PAPER/1.0."""
        for date, game_id, row in ROWS:          # ALL rows, Missing Data included
            if row["market"] != market:
                continue
            assert row.get("confidence") is None, (
                f"{market} {date}/{game_id} carries confidence={row.get('confidence')!r}")
            assert not row.get("betSize"), (
                f"{market} {date}/{game_id} carries betSize={row.get('betSize')!r}")

    @pytest.mark.parametrize("market", ["NRFI", "YRFI"])
    def test_the_probability_is_still_computed(self, market):
        """Suspension withdraws QUALIFICATION, not the model. Research and
        settlement joins depend on the probability still being emitted."""
        for date, game_id, row in EVALUATED:
            if row["market"] != market:
                continue
            assert row.get("modelProb") is not None, (
                f"{market} {date}/{game_id} lost its modelProb")

    @pytest.mark.parametrize("market", ["NRFI", "YRFI"])
    def test_ticker_identity_survives(self, market):
        for date, game_id, row in EVALUATED:
            if row["market"] != market:
                continue
            ticker = row.get("ticker") or row.get("marketTicker")
            assert ticker and "KXMLBRFI" in str(ticker), (
                f"{market} {date}/{game_id} lost its ticker identity: {ticker!r}")

    def test_both_sides_are_suspended_not_just_one(self):
        """The exact shape of the original bug: NRFI rejected while YRFI
        stayed qualified. Asserted per game, so a fix that only covers one
        side cannot pass by averaging over the corpus."""
        by_game = {}
        for date, game_id, row in EVALUATED:
            by_game.setdefault((date, game_id), {})[row["market"]] = row
        both = [k for k, v in by_game.items() if len(v) == 2]
        assert both, "no game produced both an NRFI and a YRFI row"
        for key in both:
            sides = by_game[key]
            assert sides["NRFI"].get("status") == "Rejected", key
            assert sides["YRFI"].get("status") == "Rejected", key


class TestOtherFamiliesAreUnaffected:
    def test_other_families_still_produce_accepted_rows(self):
        """A suspension that accidentally rejected everything would pass
        every test above. This proves the ledger still qualifies OTHER
        markets, so the RFI rejections are specific rather than global."""
        statuses = {}
        for _, game in _archived_games():
            try:
                rows = evaluate_game(game)
            except Exception:
                continue
            if not isinstance(rows, list):
                rows = [rows]
            for row in rows:
                if isinstance(row, dict) and row.get("market") not in ("NRFI", "YRFI"):
                    statuses.setdefault(row.get("market"), set()).add(row.get("status"))
        accepted_families = [m for m, s in statuses.items() if "Accepted" in s]
        assert accepted_families, (
            "no non-RFI family produced an Accepted row -- the suspension is "
            "over-broad, or the fixture is degenerate")


class TestMissingDataRowsAreStillSafe:
    """A Missing Data row is not a suspended row, but it must never carry a
    stake either -- otherwise the absence of first-inning inputs would
    become a back door around the suspension."""

    def test_missing_data_rows_exist_in_the_fixture(self):
        assert MISSING_ROWS, "fixture no longer exercises the Missing Data path"

    def test_missing_data_rows_carry_no_confidence_and_no_bet_size(self):
        for date, game_id, row in MISSING_ROWS:
            assert row.get("confidence") is None, (date, game_id, row.get("confidence"))
            assert not row.get("betSize"), (date, game_id, row.get("betSize"))
