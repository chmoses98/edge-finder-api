#!/usr/bin/env python3
"""The per-(hitter list, family, zone) memoization in
lib/research/pitch_sequence_model.py must be output-identical to the
uncached computation and must never mix up two different hitters."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.research.pitch_sequence_model as psm  # noqa: E402

CALLS = ("swinging_strike", "foul", "in_play", "called_strike", "ball")
TYPES = (("FF", "4-Seam Fastball"), ("SL", "Slider"), ("CH", "Changeup"), ("CU", "Curveball"))


def _pitches(seed, n=300, swing_bias=0.0):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        t, name = rng.choice(TYPES)
        call = rng.choices(CALLS, weights=(1 + swing_bias, 2 + swing_bias, 1 + swing_bias, 2, 3))[0]
        out.append({"pitchType": t, "pitchName": name, "pitchCallType": call,
                    "plateX": rng.uniform(-1.5, 1.5), "plateZ": rng.uniform(0.5, 4.5),
                    "balls": rng.randint(0, 3), "strikes": rng.randint(0, 2),
                    "launchSpeed": rng.uniform(60, 110) if call == "in_play" else None,
                    "launchAngle": rng.uniform(-30, 60) if call == "in_play" else None})
    return out


MIX = {"four_seam": 0.5, "slider": 0.3, "changeup": 0.2}


def _run_pas(hitter, seed, n_pas=200):
    rng = random.Random(seed)
    return [psm.simulate_pa_pitch_by_pitch(hitter, MIX, rng) for _ in range(n_pas)]


def test_cached_simulation_is_bit_identical_to_uncached(monkeypatch):
    hitter = _pitches(1)
    psm._PITCH_PROB_CACHE.clear()
    cached = _run_pas(hitter, 7)
    monkeypatch.setattr(psm, "_estimate_pitch_outcome_probabilities_cached", psm.estimate_pitch_outcome_probabilities)
    uncached = _run_pas(hitter, 7)
    assert cached == uncached


def test_two_hitters_with_equal_length_never_share_cache_entries():
    a, b = _pitches(1, n=300), _pitches(2, n=300, swing_bias=3.0)
    psm._PITCH_PROB_CACHE.clear()
    pa = psm._estimate_pitch_outcome_probabilities_cached(a, "four_seam", True)
    pb = psm._estimate_pitch_outcome_probabilities_cached(b, "four_seam", True)
    assert pa == psm.estimate_pitch_outcome_probabilities(a, "four_seam", True)
    assert pb == psm.estimate_pitch_outcome_probabilities(b, "four_seam", True)
    assert pa != pb


def test_returned_dict_mutation_cannot_poison_cache():
    a = _pitches(3)
    psm._PITCH_PROB_CACHE.clear()
    first = psm._estimate_pitch_outcome_probabilities_cached(a, "slider", False)
    first["swingPct"] = -1
    again = psm._estimate_pitch_outcome_probabilities_cached(a, "slider", False)
    assert again == psm.estimate_pitch_outcome_probabilities(a, "slider", False)
