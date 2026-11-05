"""Regression: named checkpoint selection uses causal UTC ordering."""
from lib.edgelab.research_dataset import _select_named_checkpoint_observations


def _obs(ident, timestamp, label="T_MINUS_30"):
    return {"marketObservationId": ident, "capturedAt": timestamp, "checkpoint": label}


def test_offset_ordering_selects_earliest_real_instant():
    # Lexicographically 08:00 precedes 09:00, but 09:00+02 is 07:00Z.
    rows = [_obs("later", "2026-08-01T08:00:00Z"),
            _obs("earlier", "2026-08-01T09:00:00+02:00")]
    assert _select_named_checkpoint_observations(rows)["T_MINUS_30"]["marketObservationId"] == "earlier"
    assert _select_named_checkpoint_observations(list(reversed(rows)))["T_MINUS_30"]["marketObservationId"] == "earlier"


def test_midnight_rollover_and_invalid_timestamp_do_not_override():
    rows = [_obs("invalid", "not-a-timestamp"),
            _obs("later", "2026-08-02T00:05:00Z"),
            _obs("earlier", "2026-08-01T19:00:00-04:00")]
    assert _select_named_checkpoint_observations(rows)["T_MINUS_30"]["marketObservationId"] == "earlier"


def test_equal_instants_tie_break_deterministically():
    rows = [_obs("b", "2026-08-01T09:00:00+02:00"),
            _obs("a", "2026-08-01T07:00:00Z")]
    for ordering in (rows, list(reversed(rows))):
        assert _select_named_checkpoint_observations(ordering)["T_MINUS_30"]["marketObservationId"] == "a"


def test_no_valid_capture_does_not_create_checkpoint():
    assert _select_named_checkpoint_observations([_obs("bad", "broken")]) == {}
