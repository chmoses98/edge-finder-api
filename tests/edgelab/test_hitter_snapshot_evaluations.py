"""#309: PIT-valid ModelEvaluation-shaped records from prospective hitter snapshots. Deterministic; tmp_path storage."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import hitter_snapshot_evaluations as H
from lib.edgelab import storage
from lib.edgelab import temporal_alignment as ta
from scripts.edgelab.build_hitter_snapshot_evaluations import emit
from scripts.edgelab.run_research_reports import _load_universe

T = "KXMLBHIT-26AUG191940SEAMIL-SEARAROZARENA56-3"   # first pitch 2026-08-19 19:40 ET = 23:40Z


def snap(sid="s1", gen="2026-08-19T20:33:28Z", prob=0.078, price=0.06, status="PROJECTED", ticker=T):
    return {"hitterProjectionSnapshotId": sid, "marketTicker": ticker, "marketFamily": "hitter_hits", "threshold": 3, "gameId": 1,
            "projectionStatus": status, "modelProbability": prob, "projectionGeneratedAt": gen, "executableKalshiPrice": price,
            "rawProbabilityEdge": 0.018, "naturalLanguageMarket": "P: 3+ hits?", "checkpoint": "LINEUP_CONFIRMATION",
            "researchRunId": "R1", "engineCommitSha": "abc", "marketObservedAt": "2026-08-19T05:00:00Z"}


def test_record_mapping_units_and_causal_stamp():
    rec, reason = H.record_from_snapshot(snap())
    assert reason is None
    assert rec["pipelineRunId"] == "2026-08-19T20:33:28Z" and rec["partitionDate"] == "2026-08-19"
    assert rec["modelFairProbability"] == 7.8 and rec["marketImpliedProbability"] == 6.0 and rec["estimatedEdge"] == 1.8
    assert rec["evaluationStatus"] == "EVALUATED" and rec["gameStartUtc"] == "2026-08-19T23:40:00Z"
    assert rec["source"] == "hitter_projection_snapshot" and rec["sourceSnapshotId"] == "s1"


def test_no_price_is_partial_evaluation_never_a_fabricated_implied_probability():
    rec, _ = H.record_from_snapshot(snap(price=None))
    assert rec["evaluationStatus"] == "PARTIAL_EVALUATION" and rec["marketImpliedProbability"] is None


def test_every_rejection_reason():
    cases = {
        H.REJECT_NOT_PROJECTED: snap(status="LINEUP_UNCONFIRMED"),
        H.REJECT_BAD_PROBABILITY: snap(prob=1.4),
        H.REJECT_BAD_TIME: snap(gen="2026-08-19T20:33:28"),          # naive: never guessed
        H.REJECT_AFTER_START: snap(gen="2026-08-19T23:41:00Z"),
        H.REJECT_START_UNPARSEABLE: snap(ticker="KXMLBHIT-GARBAGE"),
        H.REJECT_NO_ID: {**snap(), "hitterProjectionSnapshotId": None},
        H.REJECT_NO_TICKER: {**snap(), "marketTicker": ""},
    }
    for expected, s in cases.items():
        rec, reason = H.record_from_snapshot(s)
        assert rec is None and reason == expected, expected


def test_generation_exactly_at_first_pitch_is_allowed_one_second_later_is_not():
    assert H.record_from_snapshot(snap(gen="2026-08-19T23:40:00Z"))[1] is None
    assert H.record_from_snapshot(snap(gen="2026-08-19T23:40:01Z"))[1] == H.REJECT_AFTER_START


def test_build_records_dedupes_and_counts_rejects_deterministically():
    recs, rej = H.build_records([snap("b"), snap("a"), snap("a"), snap("c", status="MODEL_ERROR")])
    assert [r["sourceSnapshotId"] for r in recs] == sorted(r["sourceSnapshotId"] for r in recs) and len(recs) == 2
    assert rej == {H.REJECT_NOT_PROJECTED: 1}


def test_existing_no_look_ahead_selector_links_only_observations_after_generation():
    rec, _ = H.record_from_snapshot(snap(gen="2026-08-19T20:33:28Z"))
    before = ta.select_temporally_valid_evaluation([rec], "2026-08-19T20:00:00Z")
    after = ta.select_temporally_valid_evaluation([rec], "2026-08-19T21:00:00Z")
    assert before[0] is None and before[2] == ta.ALL_EVALUATIONS_AFTER_CHECKPOINT
    assert after[0]["modelEvaluationId"] == rec["modelEvaluationId"] and after[2] is None


def test_emit_dry_run_creates_nothing_apply_is_idempotent_and_canonical_evals_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    canon = storage.partition_path("model_evaluations", "2026-08-19")
    storage.write_all_records(canon, [{"modelEvaluationId": "x", "marketTicker": T, "evaluationStatus": "NO_MODEL_SUPPORT"}])
    before = open(canon, "rb").read()
    snaps = [snap("s1"), snap("s2", gen="2026-08-19T22:00:00Z"), snap("s3", status="MODEL_ERROR")]

    dry = emit(snaps, apply=False)
    assert dry["dryRun"] is True and dry["recordsEligible"] == 2
    assert not os.path.exists(os.path.dirname(storage.partition_path(H.ENTITY, "2026-08-19")))

    applied = emit(snaps, apply=True)
    assert applied["write"] == {"inserted": 2, "unchanged": 0, "updated": 0} and applied["canonicalModelEvaluationsModified"] == 0
    path = storage.partition_path(H.ENTITY, "2026-08-19")
    first = open(path, "rb").read()
    again = emit(snaps, apply=True)
    assert again["write"] == {"inserted": 0, "unchanged": 2, "updated": 0} and open(path, "rb").read() == first
    assert open(canon, "rb").read() == before


def test_research_loader_includes_snapshot_evaluations_and_tolerates_absence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _load_universe(["2026-08-19"])[2] == []                     # nothing present -> nothing, no error
    emit([snap("s1")], apply=True)
    evaluations = _load_universe(["2026-08-19"])[2]
    assert [e["sourceSnapshotId"] for e in evaluations] == ["s1"]
