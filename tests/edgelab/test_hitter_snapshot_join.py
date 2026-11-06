"""Deterministic tests for the hitter snapshot point-in-time join (#309). No network, no corpus."""
from lib.edgelab import hitter_snapshot_join as J

T = "KXMLBHIT-26AUG191235DETPIT-PITOCRUZ15-3"


def snap(sid, gen, prob=0.06, status="PROJECTED", ticker=T, family="hitter_hits", threshold=3):
    return {"hitterProjectionSnapshotId": sid, "marketTicker": ticker, "marketFamily": family, "threshold": threshold,
            "projectionStatus": status, "modelProbability": prob, "projectionGeneratedAt": gen}


def obs(at, ticker=T, family="hitter_hits", threshold=3):
    return {"marketTicker": ticker, "capturedAt": at, "marketFamily": family, "threshold": threshold}


def test_latest_snapshot_at_or_before_observation_wins():
    snaps = [snap("a", "2026-08-19T14:00:00Z"), snap("b", "2026-08-19T15:00:00Z"), snap("c", "2026-08-19T17:00:00Z")]
    s, reason, d = J.select_pit_snapshot(snaps, obs("2026-08-19T16:00:00Z"))
    assert reason is None and s["hitterProjectionSnapshotId"] == "b"
    assert d["eligible"] == 2 and d["snapshotAgeSeconds"] == 3600.0


def test_snapshot_generated_after_observation_is_never_used():
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T17:00:00Z")], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_AFTER_OBSERVATION


def test_equal_timestamps_are_eligible_and_tie_break_is_deterministic():
    snaps = [snap("z", "2026-08-19T16:00:00Z"), snap("m", "2026-08-19T16:00:00Z")]
    s, _, _ = J.select_pit_snapshot(snaps, obs("2026-08-19T16:00:00Z"))
    assert s["hitterProjectionSnapshotId"] == "z"
    s2, _, _ = J.select_pit_snapshot(list(reversed(snaps)), obs("2026-08-19T16:00:00Z"))
    assert s2["hitterProjectionSnapshotId"] == "z"


def test_offsets_are_compared_as_instants_not_strings():
    # 12:30-04:00 is 16:30Z; the observation at 16:00Z precedes it even though "12:30..." < "16:00..." as a string
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T12:30:00-04:00")], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_AFTER_OBSERVATION


def test_non_projected_and_missing_probability_are_rejected_with_reasons():
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z", status="LINEUP_UNCONFIRMED")], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_NOT_PROJECTED
    for bad in (None, "0.5", -0.1, 1.5, True):
        s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z", prob=bad)], obs("2026-08-19T16:00:00Z"))
        assert s is None and reason == J.R_NO_PROBABILITY, bad


def test_unparseable_or_naive_timestamps_are_rejected_not_guessed():
    for bad in (None, "", "garbage", "2026-08-19T14:00:00"):
        s, reason, _ = J.select_pit_snapshot([snap("a", bad)], obs("2026-08-19T16:00:00Z"))
        assert s is None and reason == J.R_BAD_SNAPSHOT_TIME, bad
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z")], obs("2026-08-19T16:00:00"))
    assert s is None and reason == J.R_BAD_OBS_TIME


def test_identity_mismatch_on_family_or_threshold_blocks_the_join():
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z", threshold=2)], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_IDENTITY
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z", family="hitter_total_bases")], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_IDENTITY
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T14:00:00Z", ticker=T + "X")], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_IDENTITY


def test_no_snapshot_is_its_own_reason():
    s, reason, d = J.select_pit_snapshot([], obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_NO_SNAPSHOT and d["candidates"] == 0


def test_pregame_guard_rejects_snapshot_generated_after_first_pitch():
    start = "2026-08-19T16:35:00Z"
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T16:40:00Z")], obs("2026-08-19T17:00:00Z"), game_start=start)
    assert s is None and reason == J.R_AFTER_GAME_START
    s, reason, _ = J.select_pit_snapshot([snap("a", "2026-08-19T16:30:00Z")], obs("2026-08-19T17:00:00Z"), game_start=start)
    assert reason is None and s["hitterProjectionSnapshotId"] == "a"


def test_reported_reason_is_the_furthest_progressed_rejection():
    snaps = [snap("a", "2026-08-19T14:00:00Z", status="LINEUP_UNCONFIRMED"), snap("b", "2026-08-19T18:00:00Z")]
    s, reason, d = J.select_pit_snapshot(snaps, obs("2026-08-19T16:00:00Z"))
    assert s is None and reason == J.R_AFTER_OBSERVATION and d["reasonCounts"][J.R_NOT_PROJECTED] == 1
