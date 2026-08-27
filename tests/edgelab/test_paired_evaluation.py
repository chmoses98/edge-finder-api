from lib.edgelab import paired_evaluation as pe


def _row(game_id, ticker, checkpoint, **extra):
    row = {"gameId": game_id, "marketTicker": ticker, "researchCheckpoint": checkpoint}
    row.update(extra)
    return row


def test_pairing_keeps_only_intersection_of_keys():
    control = [_row("g1", "T1", "T_MINUS_30"), _row("g1", "T2", "T_MINUS_30"), _row("g2", "T3", "T_MINUS_30")]
    candidate = [_row("g1", "T1", "T_MINUS_30"), _row("g2", "T3", "T_MINUS_30"), _row("g3", "T4", "T_MINUS_30")]
    result = pe.pair_eligible_observations(control, candidate)
    paired_keys = {k for k, _, _ in result["paired"]}
    assert paired_keys == {("g1", "T1", "T_MINUS_30"), ("g2", "T3", "T_MINUS_30")}
    assert result["nPaired"] == 2


def test_control_only_and_candidate_only_are_reported_never_silently_dropped():
    control = [_row("g1", "T1", "T_MINUS_30"), _row("g1", "T2", "T_MINUS_30")]
    candidate = [_row("g1", "T1", "T_MINUS_30")]
    result = pe.pair_eligible_observations(control, candidate)
    assert result["controlOnlyKeys"] == [("g1", "T2", "T_MINUS_30")]
    assert result["candidateOnlyKeys"] == []
    assert result["nControlOnly"] == 1


def test_duplicate_keys_within_one_side_are_excluded_and_reported_not_guessed():
    control = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.4), _row("g1", "T1", "T_MINUS_30", modelFairProbability=0.6)]
    candidate = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.5)]
    result = pe.pair_eligible_observations(control, candidate)
    assert result["controlDuplicateKeys"] == [("g1", "T1", "T_MINUS_30")]
    assert result["paired"] == []  # the ambiguous key never makes it into the pairing


def test_empty_inputs_produce_empty_pairing():
    result = pe.pair_eligible_observations([], [])
    assert result["paired"] == []
    assert result["nControlOnly"] == 0
    assert result["nCandidateOnly"] == 0


def _paired_probability_fixture():
    """5 games, one row each, control always predicts 0.5 (a coinflip),
    candidate predicts closer to the true outcome -- candidate should show
    a lower (better) Brier score."""
    control, candidate = [], []
    outcomes = [1, 1, 0, 0, 1]
    candidate_probs = [0.7, 0.8, 0.2, 0.3, 0.75]
    for i, (o, cp) in enumerate(zip(outcomes, candidate_probs)):
        game_id = f"g{i}"
        control.append(_row(game_id, f"T{i}", "T_MINUS_30", modelFairProbability=0.5, outcome=o, gameDate="2026-08-01"))
        candidate.append(_row(game_id, f"T{i}", "T_MINUS_30", modelFairProbability=cp, outcome=o, gameDate="2026-08-01"))
    return control, candidate


def test_probability_evaluation_computes_paired_metrics_over_intersection_only():
    control, candidate = _paired_probability_fixture()
    pairing = pe.pair_eligible_observations(control, candidate)
    result = pe.evaluate_probability_model_pair(pairing, n_resamples=200, seed=42)
    assert result["n"] == 5
    assert result["independentGames"] == 5
    assert result["independentDates"] == 1
    assert result["control"]["brierScore"] == 0.25  # (0.5-1)^2 == (0.5-0)^2 == 0.25 always
    assert result["candidate"]["brierScore"] < result["control"]["brierScore"]
    assert result["pairedDelta"]["brierScore"] < 0  # negative == candidate improved


def test_probability_evaluation_reports_raw_n_vs_independent_games_separately():
    """Multiple rows from the SAME game must not inflate independentGames."""
    control = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.5, outcome=1, gameDate="2026-08-01"),
               _row("g1", "T2", "T_MINUS_30", modelFairProbability=0.5, outcome=0, gameDate="2026-08-01")]
    candidate = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.6, outcome=1, gameDate="2026-08-01"),
                 _row("g1", "T2", "T_MINUS_30", modelFairProbability=0.4, outcome=0, gameDate="2026-08-01")]
    pairing = pe.pair_eligible_observations(control, candidate)
    result = pe.evaluate_probability_model_pair(pairing, n_resamples=50, seed=1)
    assert result["n"] == 2
    assert result["independentGames"] == 1


def test_missing_probability_is_dropped_and_counted_not_fabricated():
    control = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=None, outcome=1)]
    candidate = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.6, outcome=1)]
    pairing = pe.pair_eligible_observations(control, candidate)
    result = pe.evaluate_probability_model_pair(pairing)
    assert result["n"] == 0
    assert result["droppedForMissingProbability"] == 1


def test_outcome_mismatch_between_sides_is_dropped_not_averaged():
    control = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.5, outcome=1)]
    candidate = [_row("g1", "T1", "T_MINUS_30", modelFairProbability=0.6, outcome=0)]
    pairing = pe.pair_eligible_observations(control, candidate)
    result = pe.evaluate_probability_model_pair(pairing)
    assert result["n"] == 0
    assert result["droppedForOutcomeMismatch"] == 1


def test_probability_evaluation_is_deterministic_given_fixed_seed():
    control, candidate = _paired_probability_fixture()
    pairing = pe.pair_eligible_observations(control, candidate)
    result1 = pe.evaluate_probability_model_pair(pairing, n_resamples=500, seed=7)
    result2 = pe.evaluate_probability_model_pair(pairing, n_resamples=500, seed=7)
    assert result1 == result2


def test_market_economics_pair_reuses_canonical_fee_module_and_is_supplementary():
    control = [_row("g1", "T1", "T_MINUS_30", executableYesPrice=0.5, settlementResult="YES")]
    candidate = [_row("g1", "T1", "T_MINUS_30", executableYesPrice=0.5, settlementResult="YES")]
    pairing = pe.pair_eligible_observations(control, candidate)
    result = pe.evaluate_market_economics_pair(pairing)
    assert result["control"]["nSettled"] == 1
    assert result["candidate"]["nSettled"] == 1
    assert "warning" in result and "SUPPLEMENTARY" in result["warning"]


def test_market_economics_uses_same_paired_rows_as_probability_evaluation():
    """Economics and probability evaluation must never silently diverge on which rows they consider."""
    control, candidate = _paired_probability_fixture()
    for row in control + candidate:
        row["executableYesPrice"] = row["modelFairProbability"]
        row["settlementResult"] = "YES" if row["outcome"] == 1 else "NO"
    pairing = pe.pair_eligible_observations(control, candidate)
    prob_result = pe.evaluate_probability_model_pair(pairing)
    econ_result = pe.evaluate_market_economics_pair(pairing)
    assert prob_result["n"] == econ_result["control"]["nSettled"] == econ_result["candidate"]["nSettled"]
