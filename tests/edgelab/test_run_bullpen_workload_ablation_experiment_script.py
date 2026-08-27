import copy
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_bullpen_workload_ablation_experiment as exp  # noqa: E402
import build_market_ledger as bml  # noqa: E402


def _recent_usage(as_of_date="2026-08-10", back_to_back=None, heavy_pitch=None, hl_taxed=None,
                   team_pitch_window=90, games_considered=2, data_available=True):
    return {
        "dataAvailable": data_available, "unavailableReason": None,
        "asOfDate": as_of_date, "gamesConsidered": games_considered,
        "relieversUsedLastGame": [],
        "backToBackRelievers": back_to_back or [],
        "recentPitchCounts": heavy_pitch or [],
        "highLeverageRecentUsage": hl_taxed or [],
        "handednessMix": {"L": 1, "R": 1, "unknown": 0},
        "teamPitchCountLastGame": 30,
        "teamPitchCountWindow": team_pitch_window,
    }


def _make_game(away_recent_usage=None, home_recent_usage=None, game_id=555555):
    return {
        "gameId": game_id,
        "away": {
            "abbr": "AAA",
            "pitcherSavant": {"xFIP": 4.0, "seasonFIP": 4.0, "avgIPperStart": 6.0, "openerRole": False},
            "bullpen": {"xFIP": 4.0, "recentUsage": away_recent_usage},
        },
        "home": {
            "abbr": "HHH",
            "pitcherSavant": {"xFIP": 3.8, "seasonFIP": 3.8, "avgIPperStart": 6.0, "openerRole": False},
            "bullpen": {"xFIP": 3.9, "recentUsage": home_recent_usage},
        },
        "awayTeamStats": {"offenseBaselineAdj": 4.5},
        "homeTeamStats": {"offenseBaselineAdj": 4.3},
        "park": {"parkFactor": 100},
    }


_LIGHT = _recent_usage()
_TAXED = _recent_usage(
    back_to_back=[{"playerId": "1", "name": "R1"}, {"playerId": "2", "name": "R2"}],
    heavy_pitch=[
        {"playerId": "1", "name": "R1", "totalPitches": 40, "appearances": 2},
        {"playerId": "2", "name": "R2", "totalPitches": 38, "appearances": 2},
    ],
    hl_taxed=[{"playerId": "3", "name": "Closer", "saves": 2, "holds": 0, "totalPitches": 25}],
    team_pitch_window=140,
)


# ── neutralize_recent_usage / recent_usage_leakage_safe ────────────────────

def test_neutralize_recent_usage_only_touches_recent_usage_field():
    g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_LIGHT)
    g2 = exp.neutralize_recent_usage(g)
    assert g2["away"]["bullpen"]["recentUsage"] is None
    assert g2["home"]["bullpen"]["recentUsage"] is None
    # every other field is byte-identical
    g_sans_ru = copy.deepcopy(g)
    g_sans_ru["away"]["bullpen"]["recentUsage"] = None
    g_sans_ru["home"]["bullpen"]["recentUsage"] = None
    assert g2 == g_sans_ru
    # original is untouched (deep copy, not mutation)
    assert g["away"]["bullpen"]["recentUsage"] == _TAXED


def test_recent_usage_leakage_safe_requires_strictly_before_game_date():
    assert exp.recent_usage_leakage_safe(_recent_usage(as_of_date="2026-08-09"), "2026-08-10") is True
    assert exp.recent_usage_leakage_safe(_recent_usage(as_of_date="2026-08-10"), "2026-08-10") is False
    assert exp.recent_usage_leakage_safe(_recent_usage(as_of_date="2026-08-11"), "2026-08-10") is False


def test_recent_usage_leakage_safe_requires_data_available():
    ru = _recent_usage(data_available=False)
    assert exp.recent_usage_leakage_safe(ru, "2026-08-10") is False
    assert exp.recent_usage_leakage_safe(None, "2026-08-10") is False
    assert exp.recent_usage_leakage_safe({}, "2026-08-10") is False


def test_recent_usage_leakage_safe_requires_as_of_date_present():
    ru = _recent_usage()
    ru["asOfDate"] = None
    assert exp.recent_usage_leakage_safe(ru, "2026-08-10") is False


# ── game_projection_state: PIT reconstruction excludes future games (via leakage guard) ──

class TestPitLeakageGuard:
    def test_leakage_unsafe_recent_usage_makes_the_game_ineligible(self):
        g = _make_game(away_recent_usage=_recent_usage(as_of_date="2026-08-10"), home_recent_usage=_LIGHT)
        assert exp.game_projection_state(g, "2026-08-10") is None  # asOfDate == gameDate, not strictly before

    def test_leakage_safe_recent_usage_on_both_sides_makes_the_game_eligible(self):
        g = _make_game(away_recent_usage=_LIGHT, home_recent_usage=_LIGHT)
        state = exp.game_projection_state(g, "2026-08-15")
        assert state is not None

    def test_missing_recent_usage_makes_the_game_ineligible(self):
        g = _make_game(away_recent_usage=None, home_recent_usage=_LIGHT)
        assert exp.game_projection_state(g, "2026-08-15") is None


# ── candidate differs from control ONLY through the workload adjustment ────

class TestCandidateDiffersOnlyThroughWorkloadAdjustment:
    def test_rested_bullpen_produces_identical_control_and_candidate_projections(self):
        """When compute_bullpen_workload_adjustment finds nothing to
        penalize, removing recentUsage entirely must produce EXACTLY the
        same projections -- multiplier 1.0 either way."""
        g = _make_game(away_recent_usage=_LIGHT, home_recent_usage=_LIGHT)
        state = exp.game_projection_state(g, "2026-08-15")
        assert state["control"][:4] == state["candidate"][:4]

    def test_taxed_bullpen_produces_different_full_game_projections(self):
        # away's bullpen is taxed -- away pitches TO home, so home_proj (which
        # uses away's pitching) moves; away_proj (which uses home's pitching,
        # unchanged/_LIGHT) does not.
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_LIGHT)
        state = exp.game_projection_state(g, "2026-08-15")
        assert state["control"][0] == state["candidate"][0]  # away_proj unaffected
        assert state["control"][1] != state["candidate"][1]  # home_proj moves

    def test_only_recent_usage_differs_in_the_underlying_compute_projections_inputs(self):
        """Directly re-derive candidate's input from control's and confirm
        the only difference is recentUsage -- proves the ablation script
        isn't accidentally varying anything else."""
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_TAXED)
        g2 = exp.neutralize_recent_usage(g)
        g_reference = copy.deepcopy(g)
        g_reference["away"]["bullpen"]["recentUsage"] = None
        g_reference["home"]["bullpen"]["recentUsage"] = None
        assert g2 == g_reference


# ── starter-only (F5) projections remain identical ──────────────────────────

class TestF5Invariance:
    def test_f5_projections_are_exactly_identical_between_control_and_candidate(self):
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_TAXED)
        state = exp.game_projection_state(g, "2026-08-15")
        control_f5_away, control_f5_home = state["control"][2], state["control"][3]
        candidate_f5_away, candidate_f5_home = state["candidate"][2], state["candidate"][3]
        assert control_f5_away == candidate_f5_away
        assert control_f5_home == candidate_f5_home

    def test_f5_invariance_holds_even_when_full_game_projection_moves(self):
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_LIGHT)
        state = exp.game_projection_state(g, "2026-08-15")
        assert state["control"][1] != state["candidate"][1]  # full game (home_proj) DID move
        assert state["control"][2] == state["candidate"][2]  # F5 away did NOT
        assert state["control"][3] == state["candidate"][3]  # F5 home did NOT


# ── resolve_probability ─────────────────────────────────────────────────────

class TestResolveProbability:
    def _state(self):
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_LIGHT)
        return exp.game_projection_state(g, "2026-08-15")

    def test_game_result_home_team(self):
        state = self._state()
        pc, px = exp.resolve_probability(state, "game_result", "HHH", None)
        assert 0.0 <= pc <= 1.0 and 0.0 <= px <= 1.0
        assert pc != px  # home's opponent (away) is taxed, moving home's win prob

    def test_game_result_unknown_team_is_unresolvable(self):
        state = self._state()
        assert exp.resolve_probability(state, "game_result", "ZZZ", None) == (None, None)

    def test_game_total_uses_combined_projection(self):
        state = self._state()
        pc, px = exp.resolve_probability(state, "game_total", None, 8.5)
        assert pc is not None and px is not None

    def test_game_total_missing_threshold_is_unresolvable(self):
        state = self._state()
        assert exp.resolve_probability(state, "game_total", None, None) == (None, None)

    def test_team_total_uses_that_teams_own_projection(self):
        state = self._state()
        pc, px = exp.resolve_probability(state, "team_total", "AAA", 4.5)
        assert pc is not None and px is not None


# ── build_eligible_market_rows exclusions ───────────────────────────────────

class TestBuildEligibleMarketRows:
    def _row(self, **kw):
        base = {
            "marketTicker": "T1", "gameId": "G1", "canonicalMarketFamily": "game_result",
            "settlementStatus": "SETTLED", "settlementResult": "YES", "comparisonOperator": None,
            "team": "AAA", "threshold": None,
        }
        base.update(kw)
        return base

    def _games_by_id(self):
        return {"G1": {"mlbGamePk": "123", "gameDate": "2026-08-15"}}

    def test_unsettled_row_is_excluded(self):
        rows, exclusions = exp.build_eligible_market_rows([self._row(settlementStatus="PENDING")], self._games_by_id())
        assert rows == {}
        assert exclusions["not_settled"] == 1

    def test_unresolvable_settlement_result_is_excluded(self):
        rows, exclusions = exp.build_eligible_market_rows([self._row(settlementResult="VOID")], self._games_by_id())
        assert rows == {}
        assert exclusions["unresolvable_settlement_result"] == 1

    def test_non_over_comparison_operator_excluded_for_totals(self):
        row = self._row(canonicalMarketFamily="game_total", comparisonOperator="UNDER", threshold=8.5)
        rows, exclusions = exp.build_eligible_market_rows([row], self._games_by_id())
        assert rows == {}
        assert exclusions["non_over_comparison_operator"] == 1

    def test_missing_mlb_game_pk_excluded(self):
        rows, exclusions = exp.build_eligible_market_rows([self._row()], {"G1": {"mlbGamePk": None, "gameDate": "2026-08-15"}})
        assert rows == {}
        assert exclusions["no_mlb_game_pk_or_date"] == 1

    def test_duplicate_ticker_captured_only_once(self):
        rows, _ = exp.build_eligible_market_rows([self._row(), self._row()], self._games_by_id())
        assert len(rows["G1"]) == 1

    def test_eligible_row_included_with_expected_fields(self):
        rows, _ = exp.build_eligible_market_rows([self._row()], self._games_by_id())
        r = rows["G1"][0]
        assert r["outcome"] == 1
        assert r["mlbGamePk"] == "123"
        assert r["gameDate"] == "2026-08-15"


# ── paired-row identity (control/candidate rows share identical keys) ──────

def test_control_and_candidate_rows_share_identical_pairing_keys():
    g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_LIGHT, game_id="G1")
    exp._slate_cache["2026-08-15"] = {"123": g}
    rows_by_game_id = {
        "G1": [{
            "marketTicker": "T1", "gameId": "G1", "gameDate": "2026-08-15", "mlbGamePk": "123",
            "canonicalMarketFamily": "game_result", "team": "HHH", "threshold": None, "outcome": 1,
        }],
    }
    states = {"G1": exp.game_projection_state(g, "2026-08-15")}
    control_rows, candidate_rows, unresolvable = exp.build_control_candidate_rows(rows_by_game_id, states)
    assert unresolvable == 0
    assert len(control_rows) == len(candidate_rows) == 1
    for key in ("gameId", "marketTicker", "researchCheckpoint", "gameDate", "outcome", "marketFamily"):
        assert control_rows[0][key] == candidate_rows[0][key]
    assert control_rows[0]["modelFairProbability"] != candidate_rows[0]["modelFairProbability"]
    del exp._slate_cache["2026-08-15"]


# ── classify_adjustment ──────────────────────────────────────────────────

class TestClassifyAdjustment:
    def test_insufficient_sample_is_weak_unproven(self):
        assert exp.classify_adjustment(0.02, 0.01, 0.03, independent_games=5) == "WEAK_UNPROVEN"

    def test_ci_crossing_zero_is_weak_unproven(self):
        assert exp.classify_adjustment(0.002, -0.001, 0.005, independent_games=100) == "WEAK_UNPROVEN"

    def test_confidently_positive_small_magnitude_is_probably_helpful(self):
        assert exp.classify_adjustment(0.002, 0.001, 0.003, independent_games=100) == "PROBABLY_HELPFUL"

    def test_confidently_positive_large_magnitude_is_clearly_helpful(self):
        assert exp.classify_adjustment(0.02, 0.015, 0.025, independent_games=100) == "CLEARLY_HELPFUL"

    def test_confidently_negative_small_magnitude_is_probably_harmful(self):
        assert exp.classify_adjustment(-0.002, -0.003, -0.001, independent_games=100) == "PROBABLY_HARMFUL"

    def test_confidently_negative_large_magnitude_is_clearly_harmful(self):
        assert exp.classify_adjustment(-0.02, -0.025, -0.015, independent_games=100) == "CLEARLY_HARMFUL"


# ── median helper ────────────────────────────────────────────────────────

def test_median_odd_and_even_length():
    assert exp.median([1, 3, 2]) == 2
    assert exp.median([1, 2, 3, 4]) == 2.5
    assert exp.median([]) is None


# ── production behavior unchanged ───────────────────────────────────────

class TestProductionUnchanged:
    def test_script_imports_production_functions_rather_than_reimplementing(self):
        assert exp.compute_projections is bml.compute_projections
        assert exp.p_team_wins is bml.p_team_wins
        assert exp.p_over_total is bml.p_over_total

    def test_compute_bullpen_workload_adjustment_is_the_real_unmodified_function(self):
        from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment as real_fn
        assert exp.compute_bullpen_workload_adjustment is real_fn

    def test_neutralizing_recent_usage_never_mutates_the_caller_supplied_game_dict(self):
        g = _make_game(away_recent_usage=_TAXED, home_recent_usage=_TAXED)
        original = copy.deepcopy(g)
        exp.neutralize_recent_usage(g)
        assert g == original
