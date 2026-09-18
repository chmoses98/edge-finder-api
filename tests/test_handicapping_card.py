"""
tests/test_handicapping_card.py
===============================
The real-money handicapping card's two hard promises.

1. EXHAUSTIVENESS. "EVERY Kalshi market attributable to this game is
   present" was, until this change, false in exactly the case that
   matters. `main()` discarded the registry's `_excluded` list and the
   normalizer's malformed entries, so a contract Kalshi listed for an
   eligible game could vanish while the card still claimed completeness.
   A brand-new market family -- an unrecognised series, with its own
   event ticker -- is the realistic way that happens, and it is the case
   these tests inject.

2. BANKROLL. The card sizes against the authenticated balance it was
   given, records which one it used, and -- because this repository is
   PUBLIC -- commits the balance's provenance without committing the
   amount.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import bankroll_context as bc  # noqa: E402
from lib import contract_accounting  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_handicapping_card",
        os.path.join(ROOT, "scripts", "build_handicapping_card.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()

DATE = "2026-09-17"
NOW = "2026-09-17T18:40:00Z"
GAME_KEY = "26SEP171510SDCOL"


def _raw(ticker, event_ticker, **extra):
    row = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "series_ticker": event_ticker.split("-", 1)[0],
        "title": f"Contract {ticker}",
        "yes_ask": 50, "yes_bid": 48, "no_ask": 52, "no_bid": 50,
        "status": "active",
        "close_time": "2026-09-18T02:00:00Z",
    }
    row.update(extra)
    return row


def _slate(*, lineups_confirmed=True):
    side = {
        "lineupConfirmedOfficial": lineups_confirmed,
        "lineupConfirmed": lineups_confirmed,
    }
    return {
        "date": DATE,
        "games": [{
            "gameId": 824305,
            "away": {"abbr": "SD"},
            "home": {"abbr": "COL"},
            "startTime": "2026-09-17T20:10:00Z",
            "status": "Scheduled",
            "awayTeamStats": dict(side),
            "homeTeamStats": dict(side),
        }],
    }


# ── attribution ────────────────────────────────────────────────────────

def test_the_game_key_is_the_family_independent_part_of_an_event_ticker():
    assert contract_accounting.event_key("KXMLBGAME-26SEP171510SDCOL") == GAME_KEY
    assert contract_accounting.event_key("KXMLBTEAMTOTAL-26SEP171510SDCOL") == GAME_KEY
    # A brand-new family shares the key and nothing else. That is the
    # whole basis on which it can be attributed at all.
    assert contract_accounting.event_key("KXMLBQUANTUMFLUX-26SEP171510SDCOL") == GAME_KEY


@pytest.mark.parametrize("ticker", [None, "", "NODASH", "KXMLBGAME-SHORT", "KXMLBGAME-"])
def test_an_unusable_event_ticker_never_widens_a_games_attribution(ticker):
    assert contract_accounting.event_key(ticker) is None


def test_a_contract_with_no_event_ticker_is_retained_as_explicit_ambiguity():
    raw = [_raw("A-1", "KXMLBGAME-26SEP171510SDCOL"),
           {"ticker": "ORPHAN-1", "title": "no event ticker at all"}]
    kept = [{"ticker": "A-1", "eventTicker": "KXMLBGAME-26SEP171510SDCOL"}]
    acct = contract_accounting.account_for_game_contracts(raw, kept, [], kept)
    assert acct["unattributableRawContracts"] == 1
    assert "ORPHAN-1" in acct["unattributableSampleTickers"]
    # Ambiguity is reported, never resolved by guessing it into the game.
    assert acct["rawGameAttributableContracts"] == 1
    assert acct["silentRemainderCount"] == 0


def test_a_silent_remainder_is_detected_when_a_contract_is_visible_nowhere():
    """The failure the invariant exists to catch, constructed directly."""
    raw = [_raw("A-1", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("NEW-1", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    kept = [{"ticker": "A-1", "eventTicker": "KXMLBGAME-26SEP171510SDCOL"}]
    acct = contract_accounting.account_for_game_contracts(raw, kept, [], kept)
    assert acct["rawGameAttributableContracts"] == 2
    assert acct["accountedContracts"] == 1
    assert acct["silentRemainderCount"] == 1
    assert acct["silentRemainderTickers"] == ["NEW-1"]
    assert acct["invariantHolds"] is False


def test_showing_the_contract_clears_the_remainder():
    raw = [_raw("A-1", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("NEW-1", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    kept = [{"ticker": "A-1", "eventTicker": "KXMLBGAME-26SEP171510SDCOL"}]
    excluded = [{"ticker": "NEW-1", "eventTicker": "KXMLBQUANTUMFLUX-26SEP171510SDCOL",
                 "exclusionReason": "SERIES_NOT_ALLOWLISTED"}]
    acct = contract_accounting.account_for_game_contracts(raw, kept, excluded, kept + excluded)
    assert acct["silentRemainderCount"] == 0
    assert acct["invariantHolds"] is True


def test_an_unparseable_contract_counts_as_visible_once_it_is_shown():
    raw = [_raw("A-1", "KXMLBGAME-26SEP171510SDCOL"),
           {"ticker": "JUNK-1", "event_ticker": "KXMLBGAME-26SEP171510SDCOL"}]
    kept = [{"ticker": "A-1", "eventTicker": "KXMLBGAME-26SEP171510SDCOL"}]
    unclassified = contract_accounting.unclassified_raw_contracts(raw, kept)
    assert [row["ticker"] for row in unclassified] == ["JUNK-1"]
    assert unclassified[0]["state"] == "UNCLASSIFIED"
    assert unclassified[0]["reason"]

    acct = contract_accounting.account_for_game_contracts(
        raw, kept, [], kept, unclassified_raw=unclassified)
    assert acct["unclassifiedContracts"] == 1
    assert acct["silentRemainderCount"] == 0


def test_the_single_game_fetch_and_the_card_share_one_definition():
    """Two implementations of "every market for this game" would drift,
    and the one that drifts is the one that stops checking."""
    import scripts.fetch_single_game as fetch

    assert fetch.account_for_game_contracts is contract_accounting.account_for_game_contracts


# ── the card, end to end ───────────────────────────────────────────────

def _build(raw_markets, *, slate=None, bankroll=None):
    records, _counts, _malformed = builder.normalize_batch(
        raw_markets, source_mode="snapshot", source_used="snapshot")
    validated, excluded = builder.apply_strict_game_registry(records, requested_date=DATE)
    unclassified = contract_accounting.unclassified_raw_contracts(
        raw_markets, records, normalize_market=builder.normalize_market,
        source_mode="snapshot", source_used="snapshot")
    return builder.build_card(
        date=DATE, slate=slate or _slate(), slate_source="test", records=validated,
        bankroll=bankroll if bankroll is not None else {}, now_utc=NOW,
        raw_markets=raw_markets, excluded=excluded, unclassified=unclassified,
        all_records=records,
    )


def test_the_top_level_counts_distinguish_raw_from_validated():
    """`len(validated)` must never be published as the raw universe: it
    is smaller by exactly the contracts the card would be hiding."""
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    counts = _build(raw)["counts"]

    assert counts["rawUniverseContracts"] == 2
    assert counts["normalizedRegistryValidatedMarkets"] == 1
    assert counts["registryExcludedMarkets"] == 1
    assert "rawUniverseMarkets" not in counts, "the mislabelled count must be gone"
    assert counts["rawUniverseContracts"] != counts["normalizedRegistryValidatedMarkets"]


def test_a_new_market_family_stays_visible_instead_of_disappearing():
    """THE regression. An unrecognised series with its own event ticker
    used to be dropped on the floor while the card still reported
    'EVERY Kalshi market attributable to it is present'."""
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    game = _build(raw)["bettingEligibleGames"][0]

    shown = [row.get("ticker") for row in game["registryExcludedForThisGame"]]
    assert "KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES" in shown
    assert game["registryExcludedForThisGame"][0]["exclusionReason"]

    acct = game["contractAccounting"]
    assert acct["rawAttributableCount"] == 2
    assert acct["normalizedCount"] == 1
    assert acct["excludedOrUnresolvedCount"] == 1
    assert acct["accountedCount"] == 2
    assert acct["silentRemainderCount"] == 0


def test_every_eligible_game_carries_the_five_accounting_fields():
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL")]
    card = _build(raw)
    for game in card["bettingEligibleGames"] + card["researchOnlyGames"]:
        acct = game["contractAccounting"]
        for field in ("rawAttributableCount", "normalizedCount",
                      "excludedOrUnresolvedCount", "accountedCount",
                      "silentRemainderCount"):
            assert field in acct, field
        assert acct["accountedCount"] + acct["silentRemainderCount"] == acct["rawAttributableCount"]


def test_the_card_reports_a_zero_silent_remainder_total():
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    assert _build(raw)["counts"]["silentRemainderTotal"] == 0


def test_the_build_fails_loudly_rather_than_writing_a_falsely_complete_card(tmp_path, capsys):
    """A card that claims completeness while a contract is visible nowhere
    is worse than no card: it is an assurance that is false in exactly the
    case it exists to catch."""
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]

    snapshot = tmp_path / f"kalshi_search_{DATE}.json"
    snapshot.write_text(json.dumps({"date": DATE, "markets": raw}), encoding="utf-8")
    slate_path = tmp_path / "slate.json"
    slate_path.write_text(json.dumps(_slate()), encoding="utf-8")
    out_root = tmp_path / "cards"

    argv = ["--date", DATE, "--slate-path", str(slate_path), "--as-of", NOW,
            "--snapshot-path", str(snapshot), "--out-root", str(out_root)]

    # Healthy first: the excluded contract IS shown, so the build succeeds.
    sys.argv = ["build_handicapping_card.py"] + argv
    assert builder.main() == 0
    assert (out_root / f"{DATE}.json").exists()

    # Now break attribution so the same contract becomes invisible, and
    # prove the build refuses rather than writing the file.
    original = builder.excluded_for_game
    builder.excluded_for_game = lambda *a, **k: []
    try:
        out_root2 = tmp_path / "cards2"
        sys.argv = ["build_handicapping_card.py"] + argv[:-1] + [str(out_root2)]
        code = builder.main()
    finally:
        builder.excluded_for_game = original

    assert code == 1
    captured = capsys.readouterr()
    assert "exhaustiveness invariant violated" in captured.err
    assert "silentRemainder=1" in captured.err
    assert not (tmp_path / "cards2" / f"{DATE}.json").exists()


# ── the bankroll on the card ───────────────────────────────────────────

def _fresh_secret_env(amount=1234.56):
    observed = datetime.now(tz=timezone.utc) - timedelta(minutes=3)
    return {bc.BANKROLL_CONTEXT_ENV: json.dumps({
        "schemaVersion": "1",
        "bankroll": amount,
        "currency": "USD",
        "observedAt": observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": bc.SOURCE_KALSHI_AUTHENTICATED,
        "valueType": bc.VALUE_AVAILABLE_CASH,
    })}


def test_the_card_records_the_bankroll_and_the_instant_it_was_observed():
    """Requirement 9: the card must record the actual bankroll used for
    sizing and when it was observed."""
    env = _fresh_secret_env(1234.56)
    ctx = bc.load_bankroll_context(env=env)
    card = _build([_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL")],
                  bankroll=ctx)

    assert card["bankroll"]["bankroll"] == pytest.approx(1234.56)
    assert card["bankroll"]["sizingAllowed"] is True
    assert card["bankroll"]["observedAt"] == json.loads(env[bc.BANKROLL_CONTEXT_ENV])["observedAt"]
    assert card["bankroll"]["valueType"] == bc.VALUE_AVAILABLE_CASH
    assert card["bankroll"]["source"] == bc.SOURCE_KALSHI_AUTHENTICATED


def test_the_committed_card_carries_the_provenance_but_not_the_amount():
    """This repository is PUBLIC. Everything needed to trust the sizing is
    committed; the balance itself is not."""
    ctx = bc.load_bankroll_context(env=_fresh_secret_env(1234.56))
    card = _build([_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL")],
                  bankroll=bc.redacted(ctx))

    published = card["bankroll"]
    assert published["bankroll"] is None
    assert published["bankrollRedacted"] is True
    assert published["sizingAllowed"] is True          # the VERDICT survives
    assert published["observedAt"] == ctx["observedAt"]
    assert published["status"] == bc.STATUS_FRESH
    assert "1234.56" not in json.dumps(card)
    assert "1234" not in json.dumps(card["bankroll"])


def test_a_card_built_without_a_balance_says_sizing_is_unavailable():
    ctx = bc.load_bankroll_context(env={}, transactions=[], bets=[])
    card = _build([_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL")],
                  bankroll=bc.redacted(ctx))
    assert card["bankroll"]["sizingAllowed"] is False
    # The handicap still happened: the markets are all there.
    assert card["bettingEligibleGames"][0]["marketCount"] == 1


# ── the lineup gate is unchanged ───────────────────────────────────────

def test_unconfirmed_lineups_remain_research_only_with_full_accounting():
    raw = [_raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
           _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES", "KXMLBQUANTUMFLUX-26SEP171510SDCOL")]
    card = _build(raw, slate=_slate(lineups_confirmed=False))

    assert card["bettingEligibleGames"] == []
    game = card["researchOnlyGames"][0]
    assert game["eligibilityStatus"] == "BLOCKED_LINEUPS_UNCONFIRMED"
    assert all(row["realMoneyEligible"] is False for row in game["markets"])
    # Exhaustiveness is not a privilege of eligible games.
    assert game["contractAccounting"]["silentRemainderCount"] == 0
    assert game["contractAccounting"]["rawAttributableCount"] == 2
