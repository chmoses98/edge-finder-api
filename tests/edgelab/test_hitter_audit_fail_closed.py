"""Regression tests: audit must not count unknown or post-start market observations as pregame PIT joins."""
import json
from scripts.edgelab.audit_hitter_snapshot_join import audit

T = "KXMLBHIT-26AUG191235DETPIT-PITOCRUZ15-3"
BAD = "UNRECOGNIZED-26AUG19-PROP"

def test_audit_quarantines_unknown_start_and_post_start_observation(tmp_path):
    root = tmp_path
    (root / "observations").mkdir()
    rows = [
        {"marketTicker": BAD, "marketFamily": "hitter_hits", "capturedAt": "2026-08-19T16:00:00Z"},
        {"marketTicker": T, "marketFamily": "hitter_hits", "capturedAt": "2026-08-19T17:00:00Z"},
        {"marketTicker": T, "marketFamily": "hitter_hits", "capturedAt": "2026-08-19T16:35:00Z"},
        {"marketTicker": T, "marketFamily": "hitter_hits", "capturedAt": "bad-time"},
        {"marketTicker": T, "marketFamily": "hitter_hits", "capturedAt": "2026-08-19T16:00:00Z"},
    ]
    (root / "observations" / "2026-08-19.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows)
    )
    def snap(ticker):
        return {"marketTicker": ticker, "marketFamily": "hitter_hits",
                "projectionStatus": "PROJECTED", "modelProbability": 0.4,
                "projectionGeneratedAt": "2026-08-19T15:00:00Z"}
    result = audit("2026-08-19", "2026-08-19", root=str(root),
                   snapshots={BAD: [snap(BAD)], T: [snap(T)]})
    assert result["hitterObservationRows"] == 5
    assert result["pitJoinedRows"] == 1
    assert result["unjoinedReasonsByRow"] == {
        "TICKER_START_UNPARSEABLE": 1,
        "OBSERVATION_AT_OR_AFTER_TICKER_START": 2,
        "OBSERVATION_TIMESTAMP_INVALID": 1,
    }
    assert result["pregameCrossCheck"]["tickerStartUnparseable"] == 1
