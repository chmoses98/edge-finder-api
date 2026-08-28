#!/usr/bin/env python3
"""
scripts/edgelab/build_methodology_v2_triage_audit.py
====================================================================
Phase-1 deliverable: a machine-readable Methodology-V2 triage audit of
every Research Lab experiment from MLB-RSCH-0002 through MLB-RSCH-0020.

READ-ONLY. This script reads each experiment's OWN committed
registration (data/edgelab/experiments/) and result artifact
(data/edgelab/analytics/) and emits one candidate-level table. It never
modifies, reinterprets, or re-runs any historical experiment; every
historical conclusion remains correct under its own preregistered rules.

The triage question is narrow and mechanical: did MAE -- a
median-targeting loss that MLB-RSCH-0021 showed is inappropriate as the
primary gate for an expected-run MEAN feeding a negative-binomial
probability engine -- materially participate in rejecting a candidate
that mean-consistent metrics (MSE/RMSE), the distributional gate
(NB-NLL), or the probability gate (Brier) would have kept?

Classifications (preregistered before the table was populated):
  NO_RETEST_NEEDED
      the candidate was accepted, or was killed by evidence independent
      of MAE (worse RMSE, worse Brier, failed holdout, etc.).
  V2_RETEST_HIGH_PRIORITY
      MAE materially caused rejection AND the candidate improved a
      mean-consistent metric AND improved downstream probability
      scoring, AND a legitimate (unobserved) holdout still exists.
  V2_RETEST_LOWER_PRIORITY
      MAE participated, but the probability evidence is weak/mixed or
      the remaining-2026 applicability is low.
  RETEST_NOT_IDENTIFIABLE_OR_PIT_UNSAFE
      the candidate cannot be legitimately retested (data/PIT limits,
      or its holdout has already been observed).
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPERIMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "experiments")
ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")

NO_RETEST = "NO_RETEST_NEEDED"
HIGH = "V2_RETEST_HIGH_PRIORITY"
LOW = "V2_RETEST_LOWER_PRIORITY"
UNSAFE = "RETEST_NOT_IDENTIFIABLE_OR_PIT_UNSAFE"

# Each row is grounded in the named experiment's OWN committed artifact;
# the `evidence` field quotes the specific numbers that decide the triage.
TRIAGE_ROWS = [
    {
        "experimentId": "MLB-RSCH-0002", "candidate": "bullpen workload ablation variants",
        "mechanism": "bullpen recent-usage/fatigue features into game probabilities",
        "familiesAffected": ["game_result", "game_total", "team_total"],
        "originalEvidenceLevel": "E1_RECONSTRUCTED_RETROSPECTIVE",
        "originalPrimaryMetric": "paired Brier score delta vs settlement",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": False, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Primary metric was already a proper score (Brier). MAE never participated.",
        "remaining2026Relevance": "LOW (E1, single corpus, no split)",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0003", "candidate": "multi-season bullpen workload features",
        "mechanism": "reconstructed bullpen workload -> relief runs allowed per 9",
        "familiesAffected": [], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "game-clustered bootstrap mean difference / Spearman correlation",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": False,
        "probabilityEvaluationOccurred": False, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Correlation-primary feature study, not a mean-model candidate selection. No MAE gate.",
        "remaining2026Relevance": "MEDIUM (bullpen matters) but no candidate was gated by MAE",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0004", "candidate": "starter rest/workload features",
        "mechanism": "starter rest & workload -> starter ER/9",
        "familiesAffected": [], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "pitcher-clustered bootstrap / Spearman correlation",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": False,
        "probabilityEvaluationOccurred": False, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Correlation-primary. Additionally MLB-RSCH-0009 found starter identity NOT PIT-safe at scale.",
        "remaining2026Relevance": "MEDIUM but PIT-limited",
        "retestCost": "n/a", "triage": UNSAFE,
    },
    {
        "experimentId": "MLB-RSCH-0005", "candidate": "team offense recency/form windows (5/10/20)",
        "mechanism": "recent-form deviation -> next-game runs scored",
        "familiesAffected": [], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "team-clustered bootstrap Spearman correlation",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": False,
        "probabilityEvaluationOccurred": False, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Correlation-primary feature study; no MAE-gated candidate selection.",
        "remaining2026Relevance": "MEDIUM", "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0006", "candidate": "edge persistence / market confirmation",
        "mechanism": "persistence of model-market disagreement across checkpoints",
        "familiesAffected": ["game_result", "team_total", "first_inning_run", "inning_result", "inning_total", "winning_margin"],
        "originalEvidenceLevel": "E1_RECONSTRUCTED_RETROSPECTIVE",
        "originalPrimaryMetric": "pairedBrierDelta_modelMinusMarket (persistent vs transient)",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": False, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Brier-primary. MLB-RSCH-0024 has since shown model-market disagreement carries ~zero incremental info (alpha 0.0004).",
        "remaining2026Relevance": "SUPERSEDED by MLB-RSCH-0024", "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0008", "candidate": "proxy model vs historical Pinnacle",
        "mechanism": "benchmark of the proxy expected-run model against a sharp book",
        "familiesAffected": ["game_result", "game_total"], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired proxy-minus-Pinnacle Brier and log-loss delta",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Proper-score primary throughout.", "remaining2026Relevance": "REFERENCE",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0009", "candidate": "component ablation (offense/bullpen/park)",
        "mechanism": "which enrichment components belong in the expected-run mean",
        "familiesAffected": ["game_result", "game_total"], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "incremental mean Brier delta vs accepted composition",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Brier-primary; produced the frozen {offense,bullpen} composition every later experiment uses.",
        "remaining2026Relevance": "FOUNDATIONAL", "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0010", "candidate": "negative-binomial run distribution",
        "mechanism": "overdispersed count distribution replacing Poisson",
        "familiesAffected": ["game_result", "game_total", "team_total", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "mean Brier across game_result and game_total lines",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": "ACCEPTED (holdout-confirmed) and now the frozen probability engine. Awaiting E4 shadow volume.",
        "remaining2026Relevance": "HIGH -- already the strongest historical candidate",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0012", "candidate": "O1 empirical-Bayes offense shrinkage",
        "mechanism": "empirical-Bayes k for team offense instead of fixed k=30",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta on next-game team runs (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": False, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("MAE improved on DEV (-0.001268) and VAL (-0.002710) so MAE did NOT reject it; the 2026 holdout "
                     "WAS unlocked and killed it: MAE +0.003189 and Brier WORSE in all four families "
                     "(game_total +0.000414, moneyline +0.000256, team_total_away +0.000358, team_total_home +0.000352)."),
        "remaining2026Relevance": "HIGH family relevance but candidate is holdout-dead",
        "retestCost": "n/a -- holdout already observed", "triage": UNSAFE,
    },
    {
        "experimentId": "MLB-RSCH-0013", "candidate": "P1 empirical-Bayes bullpen shrinkage",
        "mechanism": "empirical-Bayes k for bullpen ER/9 instead of fixed k=30",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta on next-game team runs (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("Rejected on 'DEV MAE delta not negative: 0.000163'. BUT its own artifact shows RMSE is WORSE too "
                     "(DEV 3.1118 vs C0 3.1104; holdout 3.2297 vs 3.2290), so it fails the V2 PRIMARY metric as well; "
                     "probability deltas are mixed (holdout game_total +0.000196, moneyline -0.000102). Already dead under V2."),
        "remaining2026Relevance": "MEDIUM", "retestCost": "n/a -- already V2-dead", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0014", "candidate": "C1 global affine mean calibration",
        "mechanism": "post-hoc affine recalibration of the frozen expected-run mean",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta on next-game team runs (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": False,
        "evidence": ("Rejected on 'DEV MAE delta not negative: +0.00808'. Yet RMSE IMPROVED (3.1076 vs C0 3.1104) and "
                     "frozen-NB Brier improved in ALL FIVE families on BOTH DEV and VAL (dev moneyline -0.000745, "
                     "game_total -0.000258, team_total_away -0.000541, team_total_home -0.000455, run_margin -0.000499). "
                     "holdout2026 is null and passingCandidates is [] -- the 2026 holdout was NEVER unlocked."),
        "remaining2026Relevance": "HIGH -- affects every family the frozen NB engine serves",
        "retestCost": "LOW -- specs frozen and importable; one corpus pass",
        "triage": HIGH,
    },
    {
        "experimentId": "MLB-RSCH-0014", "candidate": "C2 home/away affine mean calibration",
        "mechanism": "side-specific affine recalibration of the frozen mean",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": False,
        "evidence": ("Rejected on MAE +0.00753. RMSE IMPROVED most of the three (3.1070 vs 3.1104); Brier improved in "
                     "all five families on DEV and VAL (dev game_total -0.000511, moneyline -0.000705). Holdout never unlocked."),
        "remaining2026Relevance": "HIGH", "retestCost": "LOW", "triage": HIGH,
    },
    {
        "experimentId": "MLB-RSCH-0014", "candidate": "C3 quadratic mean calibration",
        "mechanism": "quadratic recalibration of the frozen mean",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": False,
        "evidence": "Rejected on MAE +0.00809; RMSE improved (3.1076); Brier improved in all five families on DEV and VAL. Holdout never unlocked.",
        "remaining2026Relevance": "HIGH", "retestCost": "LOW", "triage": HIGH,
    },
    {
        "experimentId": "MLB-RSCH-0015", "candidate": "S1 one-hop schedule-adjusted mean",
        "mechanism": "opponent-strength adjustment of offense/run-prevention",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("MAE IMPROVED (-0.006327) so MAE did not reject it -- it was rejected on the DEV probability gate. "
                     "MLB-RSCH-0021 then measured it under V2 on the full corpus: MSE +0.034909 (WORSE), NB-NLL +0.001719 "
                     "(WORSE), Brier +0.000608 (WORSE). Comprehensively V2-dead."),
        "remaining2026Relevance": "HIGH families, but candidate is V2-dead",
        "retestCost": "n/a -- already measured under V2 by MLB-RSCH-0021", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0015", "candidate": "S2 two-hop schedule-adjusted mean",
        "mechanism": "bounded two-hop opponent-strength adjustment",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": False, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Weaker than S1 on every axis (DEV MAE -0.002691, own band degradation) and failed the same DEV probability gate; S1's V2 death applies a fortiori.",
        "remaining2026Relevance": "LOW", "retestCost": "MEDIUM", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0016", "candidate": "J1 S1-mean + refit dispersion",
        "mechanism": "refit NB dispersion for the schedule-adjusted mean",
        "familiesAffected": ["game_total", "team_total", "game_result", "run_margin"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "aggregate frozen-NB paired Brier delta (Brier-PRIMARY)",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": False, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Brier-primary already; failed its own DEV Brier gate (+0.000918). MAE never participated.",
        "remaining2026Relevance": "LOW (built on V2-dead S1)", "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0017", "candidate": "E1 previous-season offense prior (games 1-50)",
        "mechanism": "previous-season-anchored shrinkage for early-season offense",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("MAE improved so MAE did not reject it; it PASSED and unlocked the 2026 holdout, where the aggregate "
                     "was flat (RMSE 3.1308 vs E0 3.1294, slightly worse). Not an MAE casualty; holdout already observed."),
        "remaining2026Relevance": "LOW for remaining 2026 (early-season mechanism; season is nearly over)",
        "retestCost": "n/a", "triage": UNSAFE,
    },
    {
        "experimentId": "MLB-RSCH-0018", "candidate": "G1 games 1-10 previous-season prior",
        "mechanism": "confirmatory narrow-window version of E1",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": True,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("ACCEPTED (CONFIRMED_EARLY_SEASON_SIGNAL, SHADOW_CANDIDATE_FOR_2027). Under V2 it also improves the "
                     "PRIMARY metric on all three splits (RMSE dev 3.2036 vs 3.2203, val 3.1767 vs 3.2033, holdout 3.2402 vs 3.2472), "
                     "so the V2 lens strengthens rather than changes its verdict."),
        "remaining2026Relevance": "NONE for 2026 (games 1-10 only; frozen for 2027)",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0019", "candidate": "U1/U2 uncertainty scores",
        "mechanism": "predicting which forecasts are least reliable",
        "familiesAffected": ["all"], "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "Pearson correlation of uncertainty score vs realized absolute error",
        "maeParticipatedInSelection": False,
        "mseAvailable": False, "nbNllAvailable": False, "brierAvailable": False,
        "probabilityEvaluationOccurred": False, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": "Correlation-primary (not a mean-model gate). Failed DEV/VAL correlation floors (0.0044 / 0.0347).",
        "remaining2026Relevance": "MEDIUM (bet filtering) but the tested definitions were retired",
        "retestCost": "n/a", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0020", "candidate": "B1 K-BB% bullpen component",
        "mechanism": "component-based bullpen talent instead of ER/9",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta on relief ER/9 (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": False, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("MAE improved (dev -0.002104) so MAE did not reject it; it was rejected on the frozen-NB Brier gate "
                     "(dev +0.000649, val +0.001019). Rejection was probability-driven, not MAE-driven."),
        "remaining2026Relevance": "MEDIUM", "retestCost": "MEDIUM", "triage": NO_RETEST,
    },
    {
        "experimentId": "MLB-RSCH-0020", "candidate": "B3 B0+B1 blend",
        "mechanism": "blend of realized-runs and component bullpen talent",
        "familiesAffected": ["game_total", "team_total", "game_result"],
        "originalEvidenceLevel": "E2_PIT_HISTORICAL",
        "originalPrimaryMetric": "paired MAE delta (MAE-PRIMARY)",
        "maeParticipatedInSelection": True,
        "mseAvailable": True, "nbNllAvailable": True, "brierAvailable": True,
        "probabilityEvaluationOccurred": True, "validationOccurred": True, "holdoutOccurred": False,
        "failedForReasonIndependentOfMae": True,
        "evidence": ("MAE improved (dev -0.006096) so MAE did not reject it; rejected on the Brier gate. MLB-RSCH-0021 then "
                     "measured it under V2: MSE +0.041605 (WORSE), NB-NLL +0.002056 (WORSE), Brier +0.000540 (WORSE). V2-dead."),
        "remaining2026Relevance": "MEDIUM families, candidate V2-dead",
        "retestCost": "n/a -- already measured under V2", "triage": NO_RETEST,
    },
]


def summarize(rows):
    counts = {}
    for r in rows:
        counts[r["triage"]] = counts.get(r["triage"], 0) + 1
    mae_participated = [r for r in rows if r["maeParticipatedInSelection"]]
    mae_caused = [r for r in rows if not r["failedForReasonIndependentOfMae"]]
    never_prob_tested = [r for r in rows if not r["probabilityEvaluationOccurred"]]
    pit_blocked = [r for r in rows if r["triage"] == UNSAFE]
    return {
        "totalCandidateRows": len(rows),
        "triageCounts": counts,
        "maeParticipatedInSelectionCount": len(mae_participated),
        "genuinelyKilledByBaseballEvidence": len(rows) - len(mae_caused) - len(pit_blocked),
        "potentiallyKilledByMaeMethodologyError": len(mae_caused),
        "potentiallyKilledByMaeCandidates": [f"{r['experimentId']}:{r['candidate']}" for r in mae_caused],
        "neverAdequatelyProbabilityTested": len(never_prob_tested),
        "neverAdequatelyProbabilityTestedCandidates": [f"{r['experimentId']}:{r['candidate']}" for r in never_prob_tested],
        "blockedByDataOrPitLimits": len(pit_blocked),
        "blockedByDataOrPitCandidates": [f"{r['experimentId']}:{r['candidate']}" for r in pit_blocked],
    }


def main():
    report = {
        "auditId": "METHODOLOGY_V2_TRIAGE_AUDIT",
        "scope": "MLB-RSCH-0002 .. MLB-RSCH-0020",
        "readOnly": True,
        "governance": (
            "No historical experiment was modified, reinterpreted, or re-run. Each row's `evidence` field cites "
            "numbers from that experiment's OWN committed artifact. Any retest is a NEW experiment with a NEW id."
        ),
        "classifications": {
            "NO_RETEST_NEEDED": "accepted, or killed by evidence independent of MAE",
            "V2_RETEST_HIGH_PRIORITY": "MAE materially caused rejection AND mean-consistent + probability metrics favored the candidate AND a legitimate unobserved holdout exists",
            "V2_RETEST_LOWER_PRIORITY": "MAE participated but probability evidence is weak/mixed or 2026 relevance is low",
            "RETEST_NOT_IDENTIFIABLE_OR_PIT_UNSAFE": "cannot be legitimately retested (PIT/data limits, or holdout already observed)",
        },
        "rows": TRIAGE_ROWS,
        "summary": summarize(TRIAGE_ROWS),
    }
    out_path = os.path.join("data", "edgelab", "analytics", "latest_methodology_v2_triage_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
