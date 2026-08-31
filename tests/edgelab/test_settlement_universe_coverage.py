"""
Regression tests for the two settlement-coverage defects found by the
2026-08-24..30 manual-bet audit.

DEFECT A -- late-market coverage. The settlement universe is the canonical
market dimension (scripts/edgelab/settle_markets.py builds it from
storage.read_partition("markets", date)), which the ingest job fills from
"the latest" Kalshi registry snapshot. "Latest" was the lexicographically
last FILENAME, but a game date's captures continue past UTC midnight and
the filename suffix is the capture's UTC HHMM, so a post-midnight capture
(`_0030`) sorts before a same-morning one (`_0822`). On 2026-08-28 that
made the morning capture win, and every market that only opened later that
day -- 3,068 of them -- never entered the market dimension, so settlement
never considered them and four user-confirmed wagers could not settle.

DEFECT B -- doubleheader identity. Kalshi marks a doubleheader leg in the
event ticker ("...1305BOSNYYG1"). That digit made the team segment
non-alphabetic, so _split_teams refused it and away/home/gameId all came
back None: no Game row, markets with gameId=null, settlement recording
"missing_final_score". Separately, the slate context was keyed by
(away, home) alone, so the two legs collapsed onto one entry and a lookup
would have returned one arbitrary leg's mlbGamePk for both.
"""
import json
import os

import pytest

from lib.edgelab.market_universe import (
    build_game_records,
    build_observations_from_snapshot,
    find_latest_snapshot,
    find_snapshots_for_date,
    load_game_context,
    resolve_game_context,
    snapshot_captured_at,
)
from lib.kalshi_mlb_contract_parser import parse_contract, parse_event_suffix


def _snapshot(tmp_path, name, fetched_at, tickers):
    payload = {
        "date": "2026-08-28",
        "kalshi_date": "2026-08-28",
        "fetched_at": fetched_at,
        "markets": [
            {"event_ticker": t.rsplit("-", 1)[0], "market_ticker": t, "title": "t",
             "status": "active", "yes_bid": 0.5, "yes_ask": 0.51}
            for t in tickers
        ],
    }
    path = os.path.join(str(tmp_path), name)
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


class TestLateMarketCoverage:
    """Defect A."""

    def test_snapshots_are_ordered_by_capture_time_not_filename(self, tmp_path):
        # _0030 is captured AFTER _0822 (it is the next UTC day) -- exactly
        # the real 2026-08-28 shape that lost 3,068 markets.
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBGAME-26AUG281840TBDET-TB"])
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                  "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        ordered = find_snapshots_for_date("2026-08-28", snapshot_dir=str(tmp_path))
        assert [os.path.basename(p) for p in ordered] == [
            "kalshi_search_2026-08-28_0822.json",
            "kalshi_search_2026-08-28_0030.json",
        ]

    def test_latest_snapshot_is_the_late_capture_carrying_late_markets(self, tmp_path):
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBGAME-26AUG281840TBDET-TB"])
        late = _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                         "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        assert find_latest_snapshot("2026-08-28", snapshot_dir=str(tmp_path)) == late

    def test_a_market_only_in_the_late_capture_still_reaches_the_dimension(self, tmp_path):
        """A late-opening market must not be dropped just because an EARLIER
        snapshot did not contain it."""
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBGAME-26AUG281840TBDET-TB"])
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json", "2026-08-29T00:30:48.000Z",
                  ["KXMLBGAME-26AUG281840TBDET-TB", "KXMLBF5-26AUG282215AZSF-SF"])
        latest = find_latest_snapshot("2026-08-28", snapshot_dir=str(tmp_path))
        observations, _ = build_observations_from_snapshot(latest, "RUN", {})
        assert "KXMLBF5-26AUG282215AZSF-SF" in {o["marketTicker"] for o in observations}

    def test_a_market_absent_from_every_slate_still_reaches_the_dimension(self, tmp_path):
        """Settlement completeness must not depend on slate/recommendation
        membership -- an empty game_context must not drop markets."""
        path = _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                         "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        observations, _ = build_observations_from_snapshot(path, "RUN", {})
        assert [o["marketTicker"] for o in observations] == ["KXMLBF5-26AUG282215AZSF-SF"]

    def test_universe_construction_is_deterministic(self, tmp_path):
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBGAME-26AUG281840TBDET-TB"])
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                  "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        first = find_snapshots_for_date("2026-08-28", snapshot_dir=str(tmp_path))
        for _ in range(5):
            assert find_snapshots_for_date("2026-08-28", snapshot_dir=str(tmp_path)) == first

    def test_ties_and_unreadable_capture_times_still_order_totally(self, tmp_path):
        """An unreadable/absent fetched_at must not raise and must not make
        ordering non-deterministic."""
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBGAME-26AUG281840TBDET-TB"])
        broken = os.path.join(str(tmp_path), "kalshi_search_2026-08-28_0900.json")
        with open(broken, "w") as f:
            f.write("{not json")
        assert snapshot_captured_at(broken) is None
        ordered = find_snapshots_for_date("2026-08-28", snapshot_dir=str(tmp_path))
        assert len(ordered) == 2 and ordered == find_snapshots_for_date(
            "2026-08-28", snapshot_dir=str(tmp_path))

    def test_duplicate_captures_of_one_ticker_produce_one_identity(self, tmp_path):
        """The same ticker seen in two captures is one market, not two."""
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0822.json",
                  "2026-08-28T08:22:56.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                  "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        built = []
        for path in find_snapshots_for_date("2026-08-28", snapshot_dir=str(tmp_path)):
            observations, _ = build_observations_from_snapshot(path, "RUN", {})
            built.extend(observations)
        assert len(built) == 2                                    # two observations
        assert len({o["marketTicker"] for o in built}) == 1       # one market identity

    def test_an_unobserved_ticker_is_never_fabricated(self, tmp_path):
        path = _snapshot(tmp_path, "kalshi_search_2026-08-28_0030.json",
                         "2026-08-29T00:30:48.000Z", ["KXMLBF5-26AUG282215AZSF-SF"])
        observations, _ = build_observations_from_snapshot(path, "RUN", {})
        tickers = {o["marketTicker"] for o in observations}
        assert "KXMLBF5-26AUG282215AZSF-AZ" not in tickers
        assert "KXMLBTEAMTOTAL-26AUG282140BALATH-BAL5" not in tickers


@pytest.fixture
def doubleheader_context(tmp_path):
    """A slate carrying BOTH legs of one doubleheader, plus a normal game."""
    pipeline = tmp_path / "2026-08-29"
    pipeline.mkdir(parents=True)
    (pipeline / "normalized_slate.json").write_text(json.dumps({"data": {"games": [
        {"away": {"abbr": "BOS"}, "home": {"abbr": "NYY"}, "gameId": 823539,
         "startTime": "2026-08-29T17:05:00Z", "kalshiKey": "BOSNYY"},
        {"away": {"abbr": "BOS"}, "home": {"abbr": "NYY"}, "gameId": 823501,
         "startTime": "2026-08-29T23:15:00Z", "kalshiKey": "BOSNYY"},
        {"away": {"abbr": "KC"}, "home": {"abbr": "CLE"}, "gameId": 824392,
         "startTime": "2026-08-29T20:10:00Z", "kalshiKey": "KCCLE"},
    ]}}))
    return load_game_context("2026-08-29", pipeline_dir=str(tmp_path))


class TestDoubleheaderIdentity:
    """Defect B."""

    def test_kalshi_game_marker_is_parsed_off_the_event_ticker(self):
        assert parse_event_suffix(
            "KXMLBGAME", "KXMLBGAME-26AUG291305BOSNYYG1")["game_number"] == 1
        assert parse_event_suffix(
            "KXMLBGAME", "KXMLBGAME-26AUG291915BOSNYYG2")["game_number"] == 2

    def test_doubleheader_ticker_still_yields_teams_and_game_id(self):
        parsed = parse_contract({"market_ticker": "KXMLBGAME-26AUG291305BOSNYYG1-BOS",
                                 "event_ticker": "KXMLBGAME-26AUG291305BOSNYYG1"})
        assert (parsed["awayTeam"], parsed["homeTeam"]) == ("BOS", "NYY")
        assert parsed["gameId"] == "2026-08-29_BOS_NYY_1305"
        assert parsed["doubleheaderGameNumber"] == 1

    def test_two_same_date_same_team_games_remain_distinct(self):
        g1 = parse_contract({"market_ticker": "KXMLBGAME-26AUG291305BOSNYYG1-BOS",
                             "event_ticker": "KXMLBGAME-26AUG291305BOSNYYG1"})
        g2 = parse_contract({"market_ticker": "KXMLBGAME-26AUG291915BOSNYYG2-NYY",
                             "event_ticker": "KXMLBGAME-26AUG291915BOSNYYG2"})
        assert g1["gameId"] != g2["gameId"]
        assert g1["doubleheaderGameNumber"] != g2["doubleheaderGameNumber"]

    def test_non_doubleheader_ticker_is_unaffected(self):
        parsed = parse_contract({"market_ticker": "KXMLBGAME-26AUG291610KCCLE-CLE",
                                 "event_ticker": "KXMLBGAME-26AUG291610KCCLE"})
        assert (parsed["awayTeam"], parsed["homeTeam"]) == ("KC", "CLE")
        assert parsed["doubleheaderGameNumber"] is None

    def test_date_away_home_alone_may_not_resolve_a_doubleheader(self, doubleheader_context):
        assert resolve_game_context(doubleheader_context, "BOS", "NYY") is None
        assert doubleheader_context[("BOS", "NYY")]["gameId"] is None

    def test_explicit_game_number_distinguishes_the_legs(self, doubleheader_context):
        assert resolve_game_context(doubleheader_context, "BOS", "NYY", 1)["gameId"] == "823539"
        assert resolve_game_context(doubleheader_context, "BOS", "NYY", 2)["gameId"] == "823501"

    def test_mlb_game_pk_distinguishes_the_legs_end_to_end(self, tmp_path, doubleheader_context):
        path = _snapshot(tmp_path, "kalshi_search_2026-08-29_2154.json", "2026-08-29T21:54:00.000Z",
                         ["KXMLBGAME-26AUG291305BOSNYYG1-BOS", "KXMLBGAME-26AUG291915BOSNYYG2-NYY"])
        observations, _ = build_observations_from_snapshot(path, "RUN", doubleheader_context)
        games = build_game_records(observations, doubleheader_context, date="2026-08-29")
        legs = sorted((g for g in games if g["awayTeam"] == "BOS"),
                      key=lambda g: g["doubleheaderGameNumber"])
        assert [g["doubleheaderGameNumber"] for g in legs] == [1, 2]
        assert [g["mlbGamePk"] for g in legs] == ["823539", "823501"]
        assert len({g["gameId"] for g in legs}) == 2

    def test_upstream_identity_survives_market_to_observation_to_game(self, tmp_path, doubleheader_context):
        path = _snapshot(tmp_path, "kalshi_search_2026-08-29_2154.json", "2026-08-29T21:54:00.000Z",
                         ["KXMLBGAME-26AUG291305BOSNYYG1-BOS"])
        observations, _ = build_observations_from_snapshot(path, "RUN", doubleheader_context)
        assert observations[0]["gameId"] == "823539"      # the G1 leg's real pk, not G2's
        assert observations[0]["mlbGameId"] == "823539"
        game = build_game_records(observations, doubleheader_context, date="2026-08-29")[0]
        assert game["doubleheaderGameNumber"] == 1 and game["mlbGamePk"] == "823539"

    def test_ambiguous_historical_doubleheader_stays_unresolved(self, tmp_path, doubleheader_context):
        """A doubleheader ticker with NO leg marker (older capture) must be
        left unidentified rather than assigned an arbitrary leg."""
        path = _snapshot(tmp_path, "kalshi_search_2026-08-29_2154.json", "2026-08-29T21:54:00.000Z",
                         ["KXMLBGAME-26AUG291305BOSNYY-BOS"])
        observations, _ = build_observations_from_snapshot(path, "RUN", doubleheader_context)
        assert observations[0]["mlbGameId"] is None
        games = build_game_records(observations, doubleheader_context, date="2026-08-29")
        assert games[0]["mlbGamePk"] is None
        assert games[0]["doubleheaderGameNumber"] is None

    def test_a_normal_game_still_resolves_from_the_team_pair(self, doubleheader_context):
        assert resolve_game_context(doubleheader_context, "KC", "CLE")["gameId"] == "824392"
