from lib.edgelab import pit_reconstruction as rec


def _schedule(games):
    """games: list of (date, gamePk, detailedState, side) -> raw MLB Stats
    API /schedule-shaped JSON, one entry per day (matches the real
    endpoint's dates[].games[] nesting)."""
    dates = []
    for date, game_pk, status, side in games:
        teams = {"away": {"team": {"id": 999}}, "home": {"team": {"id": 999}}}
        teams[{"away": "away", "home": "home"}[side]] = {"team": {"id": 111}}
        dates.append({
            "date": date,
            "games": [{
                "gamePk": game_pk,
                "status": {"detailedState": status},
                "teams": teams,
            }],
        })
    return {"dates": dates}


def _spy_fetcher(response):
    calls = []

    def fetcher(team_id, start_date, end_date):
        calls.append((team_id, start_date, end_date))
        return response

    fetcher.calls = calls
    return fetcher


# ── as_of_completed_team_games ──────────────────────────────────────────

def test_only_games_strictly_before_as_of_date_are_returned():
    schedule = _schedule([
        ("2026-08-10", 1, "Final", "home"),
        ("2026-08-14", 2, "Final", "home"),
        ("2026-08-15", 3, "Final", "home"),   # == as_of_date, must be excluded
        ("2026-08-16", 4, "Final", "home"),   # after as_of_date, must be excluded
    ])
    fetcher = _spy_fetcher(schedule)
    games = rec.as_of_completed_team_games(111, "2026-08-15", schedule_fetcher=fetcher)
    assert [g["gamePk"] for g in games] == [1, 2]


def test_defense_in_depth_filter_excludes_leakage_even_if_fetcher_misbehaves():
    """A fetcher that IGNORES the requested date range and returns future
    games anyway must still never leak them out of this function --
    Milestone 2's explicit 'test for look-ahead' requirement."""
    schedule = _schedule([
        ("2026-08-01", 1, "Final", "home"),
        ("2026-09-01", 2, "Final", "home"),   # far future, misbehaving fetcher
    ])

    def misbehaving_fetcher(team_id, start_date, end_date):
        return schedule  # ignores start_date/end_date entirely

    games = rec.as_of_completed_team_games(111, "2026-08-15", schedule_fetcher=misbehaving_fetcher)
    assert [g["gamePk"] for g in games] == [1]


def test_only_completed_games_are_included():
    schedule = _schedule([
        ("2026-08-10", 1, "Final", "home"),
        ("2026-08-11", 2, "In Progress", "home"),
        ("2026-08-12", 3, "Postponed", "home"),
        ("2026-08-13", 4, "Scheduled", "home"),
    ])
    fetcher = _spy_fetcher(schedule)
    games = rec.as_of_completed_team_games(111, "2026-08-15", schedule_fetcher=fetcher)
    assert [g["gamePk"] for g in games] == [1]


def test_query_window_never_asks_the_api_about_as_of_date_or_later():
    fetcher = _spy_fetcher(_schedule([]))
    rec.as_of_completed_team_games(111, "2026-08-15", lookback_days=10, schedule_fetcher=fetcher)
    [(team_id, start_date, end_date)] = fetcher.calls
    assert team_id == 111
    assert end_date == "2026-08-14"
    assert start_date == "2026-08-05"
    assert end_date < "2026-08-15"


def test_missing_team_id_or_as_of_date_returns_empty_without_calling_fetcher():
    fetcher = _spy_fetcher(_schedule([("2026-08-10", 1, "Final", "home")]))
    assert rec.as_of_completed_team_games(None, "2026-08-15", schedule_fetcher=fetcher) == []
    assert rec.as_of_completed_team_games(111, None, schedule_fetcher=fetcher) == []
    assert fetcher.calls == []


def test_returned_games_are_tagged_with_the_requested_as_of_date():
    schedule = _schedule([("2026-08-10", 1, "Final", "home")])
    games = rec.as_of_completed_team_games(111, "2026-08-15", schedule_fetcher=_spy_fetcher(schedule))
    assert games[0]["asOfDate"] == "2026-08-15"


# ── reconstruct_team_bullpen_usage_as_of ────────────────────────────────

def _boxscore(pitcher_ids, pitches_by_id):
    players = {}
    for pid in pitcher_ids:
        players[f"ID{pid}"] = {
            "person": {"fullName": f"Pitcher {pid}", "pitchHand": {"code": "R"}},
            "stats": {"pitching": {"numberOfPitches": pitches_by_id.get(pid, 10), "outs": 3, "saves": 0, "holds": 0}},
        }
    return {"teams": {"home": {"pitchers": pitcher_ids, "players": players}}}


def test_reconstruction_never_requests_a_boxscore_for_a_leaking_game():
    schedule = _schedule([
        ("2026-08-10", 1, "Final", "home"),
        ("2026-08-15", 2, "Final", "home"),   # == as_of_date, must never be fetched
    ])
    requested_boxscores = []

    def boxscore_fetcher(game_pk):
        requested_boxscores.append(game_pk)
        return _boxscore([501], {501: 20})

    summary = rec.reconstruct_team_bullpen_usage_as_of(
        111, "2026-08-15",
        schedule_fetcher=_spy_fetcher(schedule),
        boxscore_fetcher=boxscore_fetcher,
    )
    assert requested_boxscores == [1]
    assert summary["dataAvailable"] is True
    assert summary["asOfRequested"] == "2026-08-15"


def test_reconstruction_result_contains_no_trace_of_a_future_reliever():
    """Explicit look-ahead test: a reliever who ONLY appears in a future
    (on/after as_of_date) game must never appear anywhere in the
    reconstructed summary, even if a misbehaving fetcher hands back that
    game's data."""
    schedule = _schedule([
        ("2026-08-10", 1, "Final", "home"),
        ("2026-08-16", 2, "Final", "home"),  # after as_of_date
    ])

    def boxscore_fetcher(game_pk):
        if game_pk == 1:
            return _boxscore([501, 502], {501: 15, 502: 20})
        return _boxscore([999], {999: 99})  # a future-only reliever id

    def misbehaving_fetcher(team_id, start_date, end_date):
        return schedule

    summary = rec.reconstruct_team_bullpen_usage_as_of(
        111, "2026-08-15",
        schedule_fetcher=misbehaving_fetcher,
        boxscore_fetcher=boxscore_fetcher,
    )
    all_player_ids = {r["playerId"] for r in summary["recentPitchCounts"]}
    assert "999" not in all_player_ids
    assert all_player_ids == {"502"}  # 501 is game 1's starter (pitchers[0]), excluded from relief appearances


def test_reconstruction_reports_unavailable_when_no_completed_games_in_window():
    fetcher = _spy_fetcher(_schedule([]))
    summary = rec.reconstruct_team_bullpen_usage_as_of(111, "2026-08-15", schedule_fetcher=fetcher)
    assert summary["dataAvailable"] is False
    assert summary["unavailableReason"] == "no_completed_games_in_window"
    assert summary["asOfRequested"] == "2026-08-15"
