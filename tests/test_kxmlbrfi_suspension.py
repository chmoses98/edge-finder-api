#!/usr/bin/env python3
"""
tests/test_kxmlbrfi_suspension.py
=================================
Coverage for the MLB-RSCH-0032 KXMLBRFI real-money suspension.

The suspension must withdraw REAL-MONEY QUALIFICATION and nothing else:
capture, persistence, evaluation, settlement and research consumption all
have to keep working, the probability model must be untouched, and no
other family may move.
"""
import ast
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEDGER = os.path.join(_ROOT, "scripts", "build_market_ledger.py")
SOURCE = open(LEDGER).read()


class TestSuspensionUsesTheCanonicalMechanism:
    def test_reason_constant_exists_and_names_the_experiment(self):
        import scripts.build_market_ledger as bml
        assert "MLB-RSCH-0032" in bml.RFI_SUSPENSION_REASON
        assert "paper only" in bml.RFI_SUSPENSION_REASON.lower()

    def test_gate_is_appended_to_both_sides(self):
        assert "gates_nrfi.append(RFI_SUSPENSION_REASON)" in SOURCE
        assert "gates_yrfi.append(RFI_SUSPENSION_REASON)" in SOURCE

    def test_no_second_gating_system_was_invented(self):
        """It must reuse gates_*/rejected_row, not a parallel mechanism."""
        for invented in ("SUSPENDED_FAMILIES", "suspend_family(", "FAMILY_BLOCKLIST",
                         "DISABLED_MARKETS"):
            assert invented not in SOURCE, f"invented a second gating system: {invented}"

    def test_gate_fires_unconditionally_not_behind_a_data_condition(self):
        i = SOURCE.index("gates_nrfi.append(RFI_SUSPENSION_REASON)")
        preceding = SOURCE[:i].rsplit("\n", 3)[-3:]
        assert not any(ln.strip().startswith(("if ", "elif ")) for ln in preceding), preceding


class TestRealMoneyQualificationIsWithdrawn:
    def test_a_fired_gate_forces_confidence_to_none(self):
        assert "if gates_nrfi:\n                conf_nrfi = None" in SOURCE

    def test_confidence_none_routes_to_rejected_row(self):
        assert "if conf_nrfi is None:\n                row = rejected_row(" in SOURCE
        assert "if conf_yrfi is None:\n                row = rejected_row(" in SOURCE

    def test_only_accepted_rows_carry_a_bet_size(self):
        """betSize is computed on the accepted branch only, so a suspended row
        can never carry a real-money stake. Each block is bounded at its own
        row['reasonCodes'] line rather than a fixed character window, so the
        rejected block cannot bleed into the accepted one."""
        for fam in ("'NRFI'", "'YRFI'"):
            for kind, expect in (("accepted_row", True), ("rejected_row", False)):
                start = SOURCE.index("row = %s(\n                    %s," % (kind, fam))
                end = SOURCE.index("row['reasonCodes']", start)
                block = SOURCE[start:end]
                assert ("betSize=" in block) is expect, (
                    f"{fam} {kind}: betSize present={not expect and 'unexpectedly' or ''}")


class TestResearchAndCaptureAreRetained:
    def test_suspended_rows_still_carry_the_model_probability(self):
        for var in ("p_nrfi", "p_yrfi"):
            rej = SOURCE.index("row = rejected_row(\n                    '%s'," % var[2:].upper())
            assert "modelProb=round(%s*100,2)" % var in SOURCE[rej:rej + 900]

    def test_suspended_rows_carry_ticker_identity_for_settlement_joins(self):
        """Without identity a suspended row could not be joined to its
        settlement, which would break research consumption."""
        for fam in ("'NRFI'", "'YRFI'"):
            rej = SOURCE.index("row = rejected_row(\n                    %s," % fam)
            assert "identity(rfi.get('ticker'), 'KXMLBRFI')" in SOURCE[rej:rej + 1400]

    def test_suspension_does_not_touch_capture_or_persistence(self):
        for banned in ("skip_capture", "del rows['NRFI']", "del rows['YRFI']",
                       "rows.pop('NRFI')", "rows.pop('YRFI')"):
            assert banned not in SOURCE, f"suspension removes the family: {banned}"

    def test_rows_are_still_emitted_for_both_sides(self):
        assert "rows['NRFI'] = row" in SOURCE and "rows['YRFI'] = row" in SOURCE


class TestProbabilityModelIsUnchanged:
    def test_first_inning_lambda_derivation_untouched(self):
        for expr in ("fi_ctx.get('awayLambda1st')", "fi_ctx.get('homeLambda1st')",
                     "(away_proj / 9)", "(home_proj / 9)"):
            assert expr in SOURCE, f"lambda derivation changed: {expr}"

    def test_rule_34_still_present_and_independent(self):
        assert "Rule 34: NRFI blocked" in SOURCE
        assert "total_line >= 8" in SOURCE

    def test_diff_touches_only_the_ledger_and_its_docs_and_tests(self):
        diff = subprocess.run(["git", "diff", "--name-only", "origin/main...HEAD"],
                              cwd=_ROOT, capture_output=True, text=True).stdout.split()
        allowed = {"scripts/build_market_ledger.py",
                   "docs/EDGELAB_KXMLBRFI_SUSPENSION.md",
                   "tests/test_kxmlbrfi_suspension.py"}
        assert set(diff) <= allowed, f"unexpected files changed: {set(diff) - allowed}"


class TestOtherFamiliesUnchanged:
    @pytest.mark.parametrize("family", ["ML_Away", "ML_Home", "Game_Total",
                                        "TT_Away_Over", "TT_Home_Over",
                                        "F5_ML_Away", "F5_ML_Home"])
    def test_other_family_rows_still_built(self, family):
        assert f"'{family}'" in SOURCE

    def test_no_other_family_gained_a_suspension_gate(self):
        assert SOURCE.count("RFI_SUSPENSION_REASON") == 3   # constant + two appends

    def test_existing_rule_71_game_total_suspension_untouched(self):
        assert "Rule 71 market suspension: Game Total WR 41%" in SOURCE


class TestReversibilityIsDocumented:
    DOC = os.path.join(_ROOT, "docs", "EDGELAB_KXMLBRFI_SUSPENSION.md")

    def test_doc_exists_and_states_the_reason(self):
        text = open(self.DOC).read()
        assert "MLB-RSCH-0032" in text
        assert "0.2577" in text and "0.2481" in text and "0.2500" in text

    def test_doc_states_research_gated_reactivation_requiring_human_approval(self):
        text = open(self.DOC).read()
        assert "explicit human approval" in text
        assert "must" in text and "not" in text and "automatically" in text

    def test_doc_requires_beating_both_market_and_base_rate(self):
        text = open(self.DOC).read()
        assert "constant base-rate predictor" in text
        assert "beating only one is not sufficient" in text

    def test_doc_explains_how_to_revert(self):
        assert "gates_*.append(RFI_SUSPENSION_REASON)" in open(self.DOC).read()

    def test_suspension_is_not_described_as_permanent(self):
        text = open(self.DOC).read().lower()
        assert "not a claim that the family is permanently dead" in text
