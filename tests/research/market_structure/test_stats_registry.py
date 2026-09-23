import json
import os

import pytest

from lib.edgelab.research.market_structure import stats as S, registry as R


def _rows(n_games=30, per=4, effect=0.0, seed=1):
    import random
    rng = random.Random(seed)
    rows = []
    for g in range(n_games):
        shared = rng.gauss(0, 1)
        for i in range(per):
            rows.append({"physicalGameKey": "G%d" % g, "gameDate": "2026-08-%02d" % (1 + g % 20),
                         "v": effect + shared + rng.gauss(0, 0.2), "w": 1.0})
    return rows


def test_cluster_bootstrap_is_deterministic_and_covers_null():
    # exact null by construction: every game carries +v and -v rows, so the sample mean is 0
    rows = []
    for g in range(30):
        v = 0.5 + g * 0.1
        rows += [{"physicalGameKey": "G%d" % g, "v": v}, {"physicalGameKey": "G%d" % g, "v": -v}]
    a = S.cluster_bootstrap(rows, lambda rs: S.mean([r["v"] for r in rs]), n_resamples=300)
    b = S.cluster_bootstrap(rows, lambda rs: S.mean([r["v"] for r in rs]), n_resamples=300)
    assert a == b
    assert a["clusters"] == 30 and a["ciLow"] <= a["point"] <= a["ciHigh"]
    assert a["point"] == 0.0 and a["ciLow"] <= 0 <= a["ciHigh"]
    assert a["pPercentile"] > 0.10


def test_cluster_bootstrap_detects_effect_and_ratio_fast_path_agrees():
    rows = _rows(effect=1.5)
    slow = S.cluster_bootstrap(rows, lambda rs: S.mean([r["v"] for r in rs]), n_resamples=400)
    fast = S.cluster_bootstrap_ratio(rows, "v", "w", n_resamples=400)
    assert slow["ciLow"] > 0 and fast["ciLow"] > 0
    assert abs(slow["point"] - fast["point"]) < 1e-9
    assert fast["pPercentile"] < 0.10


def test_cluster_bootstrap_ignores_rows_without_cluster():
    rows = _rows() + [{"v": 99.0}]
    r = S.cluster_bootstrap(rows, lambda rs: S.mean([r["v"] for r in rs]), n_resamples=50)
    assert r["droppedNoCluster"] == 1 and r["n"] == 120


def test_percentile_p_consistent_with_ci():
    draws = sorted([-1.0 + 0.01 * i for i in range(300)])  # spans -1..2
    p = S.percentile_p(draws, 0.0)
    lo = sum(1 for d in draws if d <= 0) / len(draws)
    assert p == pytest.approx(2 * lo)


def test_benjamini_hochberg_counts_none_toward_search_space():
    surv = S.benjamini_hochberg({"a": 0.001, "b": 0.04, "c": 0.5, "d": None}, q=0.10)
    assert surv["a"] and surv["b"] and not surv["c"] and not surv["d"]
    # with many blocked tests the same p no longer survives
    many = {"a": 0.04}
    many.update({"blocked%d" % i: None for i in range(20)})
    assert not S.benjamini_hochberg(many, q=0.10)["a"]


def test_partial_slope_recovers_coefficient():
    import random
    rng = random.Random(3)
    tr = []
    for _ in range(500):
        x, z = rng.gauss(0, 1), rng.gauss(0, 1)
        tr.append((x, z, 0.7 * x - 0.3 * z + rng.gauss(0, 0.05)))
    assert S.partial_slope(tr) == pytest.approx(0.7, abs=0.02)
    assert S.partial_slope([(1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4)]) is None  # collinear


def test_date_half_stability():
    rows = _rows(effect=1.0)
    st = S.date_half_stability(rows, lambda rs: S.mean([r["v"] for r in rs]))
    assert st["sameSign"] is True and st["dates"] == 20


# ---------------- registry ----------------

def _reg():
    return {"programId": "T", "frozenAt": "x", "multiplicityFamilies": {"MF-A": "a"},
            "hypotheses": [{"id": "T-A-001", "family": "MF-A", "status": "PROPOSED", "title": "t", "statement": "s",
                            "data": "d", "unit": "u", "primaryMetric": "m", "method": "me", "failureCriterion": "f"}]}


def test_registry_validate_and_fingerprint_stable_under_results():
    reg = _reg()
    assert R.validate_registry(reg) == ["T-A-001"]
    fp = R.fingerprint(reg)
    R.record_result(reg, "T-A-001", "EXPLORATORY", {"summary": "ran"})
    assert R.fingerprint(reg) == fp
    assert reg["hypotheses"][0]["results"][0]["summary"] == "ran"


def test_registry_refuses_illegal_transition_and_bad_family():
    reg = _reg()
    with pytest.raises(ValueError):
        R.record_result(reg, "T-A-001", "PROSPECTIVE_PASS", {})
    reg["hypotheses"][0]["family"] = "MF-Z"
    with pytest.raises(ValueError):
        R.validate_registry(reg)


def test_committed_registry_is_valid_and_frozen_fields_present():
    reg = R.load_registry(os.path.join(os.path.dirname(__file__), "..", "..", "..", R.DEFAULT_REGISTRY_PATH))
    ids = R.validate_registry(reg)
    assert len(ids) == len(set(ids)) >= 20
    assert all(h["family"] in reg["multiplicityFamilies"] for h in reg["hypotheses"])


def test_freeze_spec_hashes_and_refuses_overwrite(tmp_path):
    p = tmp_path / "spec.json"
    sha = R.freeze_spec({"specId": "X", "rule": {"a": 1}}, str(p))
    assert len(sha) == 64
    assert R.verify_spec(str(p)) == (True, True)
    with pytest.raises(FileExistsError):
        R.freeze_spec({"specId": "X", "rule": {"a": 2}}, str(p))
    doc = json.load(open(p))
    doc["rule"]["a"] = 2
    json.dump(doc, open(p, "w"))
    assert R.verify_spec(str(p))[0] is False
