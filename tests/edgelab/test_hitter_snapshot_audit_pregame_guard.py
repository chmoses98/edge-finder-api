"""Regression: unknown scheduled first pitch must never count as PIT verified."""
import gzip
import json
from pathlib import Path

from scripts.edgelab.audit_hitter_snapshot_join import audit


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")


def test_unparseable_start_is_quarantined_even_with_eligible_snapshot(tmp_path):
    bad = "KXMLBHIT-NOTAPARSE-PLAYER"
    good = "KXMLBHIT-26AUG191235DETPIT-PLAYER"
    obs = [
        {"marketTicker": ticker, "marketFamily": "hitter_hits",
         "capturedAt": "2026-08-19T15:00:00Z"}
        for ticker in (bad, good)
    ]
    _write(tmp_path / "observations" / "2026-08-19.jsonl.gz", obs)
    snaps = {
        ticker: [{"marketTicker": ticker, "marketFamily": "hitter_hits",
                  "projectionStatus": "PROJECTED", "modelProbability": 0.4,
                  "projectionGeneratedAt": "2026-08-19T14:00:00Z"}]
        for ticker in (bad, good)
    }
    result = audit("2026-08-19", "2026-08-19", root=str(tmp_path), snapshots=snaps)
    assert result["hitterObservationRows"] == 2
    assert result["pitJoinedRows"] == 1
    assert result["pitJoinedTickers"] == 1
    assert result["unjoinedReasonsByRow"]["TICKER_START_UNPARSEABLE"] == 1
    assert result["pregameCrossCheck"]["tickerStartUnparseable"] == 1
    assert result["pregameCrossCheck"]["selectedSnapshotsGeneratedAfterTickerStart"] == 0
    assert sum(result["unjoinedReasonsByRow"].values()) + result["pitJoinedRows"] == 2


def test_unparseable_start_with_no_snapshot_is_still_quarantined(tmp_path):
    _write(tmp_path / "observations" / "2026-08-19.jsonl.gz", [
        {"marketTicker": "INVALID", "marketFamily": "hitter_hits",
         "capturedAt": "2026-08-19T15:00:00Z"}
    ])
    result = audit("2026-08-19", "2026-08-19", root=str(tmp_path), snapshots={})
    assert result["pitJoinedRows"] == 0
    assert result["unjoinedReasonsByRow"] == {"TICKER_START_UNPARSEABLE": 1}



def test_post_start_observation_is_quarantined_despite_pregame_snapshot(tmp_path):
    ticker = "KXMLBHIT-26AUG191235DETPIT-PLAYER"
    _write(tmp_path / "observations" / "2026-08-19.jsonl.gz", [
        {"marketTicker": ticker, "marketFamily": "hitter_hits",
         "capturedAt": "2026-08-19T17:00:00Z"}
    ])
    snaps = {ticker: [{"marketTicker": ticker, "marketFamily": "hitter_hits",
                       "projectionStatus": "PROJECTED", "modelProbability": 0.4,
                       "projectionGeneratedAt": "2026-08-19T14:00:00Z"}]}
    result = audit("2026-08-19", "2026-08-19", root=str(tmp_path), snapshots=snaps)
    assert result["hitterObservationRows"] == 1
    assert result["pitJoinedRows"] == 0
    assert result["unjoinedReasonsByRow"] == {"OBSERVATION_AFTER_TICKER_START": 1}
    assert sum(result["unjoinedReasonsByRow"].values()) + result["pitJoinedRows"] == 1


def test_invalid_observation_clock_is_quarantined(tmp_path):
    ticker = "KXMLBHIT-26AUG191235DETPIT-PLAYER"
    _write(tmp_path / "observations" / "2026-08-19.jsonl.gz", [
        {"marketTicker": ticker, "marketFamily": "hitter_hits",
         "capturedAt": "not-a-timestamp"}
    ])
    result = audit("2026-08-19", "2026-08-19", root=str(tmp_path), snapshots={})
    assert result["hitterObservationRows"] == 1
    assert result["pitJoinedRows"] == 0
    assert result["unjoinedReasonsByRow"] == {"OBSERVATION_TIMESTAMP_INVALID": 1}
