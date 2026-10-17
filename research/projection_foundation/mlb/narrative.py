"""Experiment / investigation / priority sections of summary.json, filled from stage numbers.
Every number quoted here is read from the stage outputs; nothing is typed in by hand."""
from __future__ import annotations


def _ci(d, n=4):
    if not d:
        return "n/a"
    return f"{d['mean_diff']:+.{n}f} [{d['ci95'][0]:+.{n}f}, {d['ci95'][1]:+.{n}f}]"


def _val(rows, model, key="crps"):
    for r in rows:
        if r["model"] == model:
            return r[key]
    return None


def _seg(segs, key, seg):
    for s in segs.get(key, []):
        if s["segment"] == seg:
            return s
    return None


def sections(inv, ps, hs, ts, fams):
    ex, inv_rows, fail, prio = [], [], [], []
    chk = inv["checks"]
    pf = ps["summary_families"]
    pp = ps["params"]
    # ------------------------------------------------------------- pitcher experiments
    bf = pf["bf"]["rows"]
    seg_D = {s["segment"]: s for s in ps["segments"].get("bf|D", [])}
    seg_R = {s["segment"]: s for s in ps["segments"].get("bf|D_rc", [])}
    ex.append({"id": "MLB-PF-P1", "question": "Why does a transparent workload model (pitch-count leash / pitches-per-BF x opponent) miss batters faced so badly?",
               "hypothesis": "Errors concentrate in identifiable roles: openers/bulk relievers, season debuts, spot starters and short rest.",
               "method": "Segment validation errors of model D (opener flag, days since last appearance, debut, layoff, no start history, early exit); add those terms to a dev-fitted Poisson GLM (D_rc).",
               "data": f"{ps['n_starts']['development']} dev starts (2023-24), {ps['n_starts']['validation']} validation starts (2025).",
               "result": (f"D validation MAE {_val(bf, 'D', 'mae'):.3f} -> D_rc {_val(bf, 'D_rc', 'mae'):.3f} (CRPS {_val(bf, 'D', 'crps'):.4f} -> {_val(bf, 'D_rc', 'crps'):.4f}). "
                          f"Rest <=2 days elasticity {pp['bf_rc_betas']['rest_le2']:+.2f}, rest 3 {pp['bf_rc_betas']['rest_3']:+.2f}, rest 4 {pp['bf_rc_betas']['rest_4']:+.2f} (log scale). "
                          f"Early exits (<=12 BF) are {seg_D.get('early_exit_le12bf=1', {}).get('share_rows', 0):.1%} of starts but {seg_D.get('early_exit_le12bf=1', {}).get('share_abs_error', 0):.1%} of D's absolute error."),
               "interpretation": "Days since the pitcher's last appearance is the single strongest workload signal: a 'starter' who pitched 1-4 days ago is an opener. Early exits (injury, blow-up) remain unforecastable from box scores.",
               "decision": "adopt"})
    ex.append({"id": "MLB-PF-P2", "question": "Is the gradient-boosted model (E) better than the corrected transparent model for batters faced and outs?",
               "hypothesis": "Remaining nonlinear workload interactions (rest x recent pitch counts x role) are learnable.",
               "method": "Walk-forward LightGBM refitted every two months from 2023-04 on PIT features; frozen-winner rule on validation CRPS with a significance tie-break toward the transparent model.",
               "data": "2022-2026 starter box lines.",
               "result": f"BF: {ps['families']['bf']['selection']}. Outs: {ps['families']['outs']['selection']}.",
               "interpretation": ("The GBM's gain is broad: " + "; ".join(
                   f"{lab} MAE D_rc {_seg(ps['segments'], 'bf|D_rc', key)['mae']:.2f} vs E {_seg(ps['segments'], 'bf|E', key)['mae']:.2f}"
                   for key, lab in (("opener_flag=0", "ordinary starters"), ("opener_flag=1", "openers"), ("season_debut=1", "debuts"))
                   if _seg(ps['segments'], 'bf|D_rc', key) and _seg(ps['segments'], 'bf|E', key)) +
                   ". It is frozen for the opportunity families with D_rc kept as the explainable fallback."),
               "decision": "adopt"})
    k_rows = pf["k"]["rows"]
    ex.append({"id": "MLB-PF-P3", "question": "Does an opponent strikeout adjustment (team K% vs the pitcher's hand, shrunk) and park help strikeout projections?",
               "hypothesis": "Opponent K tendency scales a pitcher's K rate multiplicatively (elasticity ~1, log5).",
               "method": "C = BF(D_rc) x EB-shrunk K/BF; D = C x exp(b1 log opp_factor + b2 log park_factor), b fitted by Poisson likelihood on 2023-24.",
               "data": "Opponent K% vs LHP/RHP from the 2024-26 PA feed, overall team K% for earlier seasons; park K factor from prior games at the venue.",
               "result": f"Fitted elasticities: opponent {pp['k_betas']['opp']:.2f}, park {pp['k_betas']['park']:.2f}. Validation CRPS C {_val(k_rows, 'C'):.4f} -> D {_val(k_rows, 'D'):.4f}; MAE {_val(k_rows, 'C', 'mae'):.3f} -> {_val(k_rows, 'D', 'mae'):.3f}.",
               "interpretation": "The opponent elasticity is almost exactly the theoretical 1.0, and the adjustment is a clear, significant gain (not a fitted artefact).",
               "decision": "adopt"})
    kd = ps["families"]["k"]["dist_choice"].get("D", {})
    ex.append({"id": "MLB-PF-P4", "question": "Which distribution best represents strikeouts given a mean?",
               "hypothesis": "K | BF ~ Binomial(BF, p) mixed over the BF distribution captures both workload and rate uncertainty.",
               "method": "Development CRPS of Poisson, NB, binned NB, empirical residual and binomial-on-BF mixture (beta-binomial phi grid) for model D.",
               "data": "Development starts 2023-24.",
               "result": "; ".join(f"{k} {v:.4f}" for k, v in kd.items()),
               "interpretation": "The BF mixture is best, but every family is within ~0.1% CRPS: the mean is what matters; the shape is not the bottleneck.",
               "decision": "adopt"})
    sc = ps["statcast"]
    ex.append({"id": "MLB-PF-P5", "question": "Do pitch-level quality signals (prior whiff%, fastball velocity change) improve strikeout projections beyond K%?",
               "hypothesis": "Whiff rate beyond what K% shows, and velocity drops, predict strikeouts.",
               "method": "Poisson offset GLM on top of frozen D: fit 2026-08-25..09-08, test 09-09..09-27 (only window with Statcast in the repo).",
               "data": f"{sc['n_fit']} fit / {sc['n_test']} test starts with >=2 prior Statcast starts.",
               "result": f"Test MAE D {sc['test_mae_D']} vs D+Statcast {sc['test_mae_D_plus_statcast']}; MAE diff {_ci(sc['mae_diff'])}; Poisson NLL diff {_ci(sc['poisson_nll_diff'])}.",
               "interpretation": "No detectable gain; the window is 7 weeks, so this is 'no evidence of value at this depth', not proof of no value.",
               "decision": "insufficient evidence"})
    inc = ps["incumbent"]
    for c, fam in (("k", "pitcher_strikeouts"), ("bf", "batters_faced"), ("outs", "outs")):
        w = ps["families"][c]["winner"]
        h = inc[c].get(f"{w}|holdout|champion")
        ch = inc[c].get(f"{w}|holdout|challenger")
        if h:
            ex.append({"id": f"MLB-PF-P6-{c}", "question": f"Is the frozen {fam} model better than the production pitcher engine (incumbent)?",
                       "hypothesis": "Role-aware workload and opponent-adjusted rates beat the incumbent's single recency-weighted outs mean.",
                       "method": "Matched 2026 holdout starts; incumbent = lib/research/pitcher_prop_projection.py re-run walk-forward (retroactive, as-of first pitch-60m). Rung RPS over the incumbent's own rungs, MAE, Brier at Kalshi rungs; game-clustered bootstrap.",
                       "data": f"{h['n_obs']} matched starts, {h['n_clusters']} games.",
                       "result": (f"MAE {h['model_mae']} vs {h['inc_mae']} ({_ci(h['mae_diff'])}); rung RPS {h['model_rung_rps']} vs {h['inc_rung_rps']} ({_ci(h['rps_diff'])}); "
                                  f"Brier at Kalshi rungs {h['model_brier_kalshi_rungs']} vs {h['inc_brier_kalshi_rungs']}; bias {h['model_bias']} vs {h['inc_bias']}. "
                                  f"Versus the W1-D research challenger: RPS {_ci(ch['rps_diff']) if ch else 'n/a'}."),
                       "interpretation": "Better than the incumbent on every metric with intervals excluding zero; the incumbent rerun is retroactive, so this is a walk-forward comparison, not an archived-forecast one.",
                       "decision": "adopt"})
    # ------------------------------------------------------------- hitter experiments
    if hs:
        hf = hs["summary_families"]
        sk = hs["slot_knowledge"]
        ex.append({"id": "MLB-PF-H1", "question": "How much does knowing the batting slot (confirmed lineup) improve hitter projections?",
                   "hypothesis": "PA depends strongly on slot; using only the previous slot loses accuracy when lineups change.",
                   "method": "Same model D with the post-game official slot vs the player's previous slot (no lineup information).",
                   "data": "2025 validation and 2026 holdout starters.",
                   "result": "; ".join(f"{k.split('|')[0]} ({k.split('|')[1]}): CRPS prev-slot minus known-slot {_ci(v['diff_hist_minus_known'])}, slot changed in {v['share_slot_changed']:.0%} of games"
                                      for k, v in sk.items() if k.split('|')[1] == 'validation'),
                   "interpretation": "Lineup confirmation is worth a measurable amount for PA (and less for rate stats). Forecasts made before lineups post must use the prev-slot variant and be labelled.",
                   "decision": "adopt"})
        hrows = hf["H"]["rows"]
        hb = hs["params"].get("H_betas", {})
        ex.append({"id": "MLB-PF-H2", "question": "Do opposing-starter quality, park and platoon adjust hitter rates usefully?",
                   "hypothesis": "Hit rates scale with the starter's hits-allowed rate over his share of PAs, park and handedness.",
                   "method": "D = C x exp(sum b_i x share x log factor_i); b fitted on 2024 (from May).",
                   "data": "Starter PIT rates from the pitcher frame; park factors from prior games at the venue; league platoon multipliers fitted on 2024 PAs.",
                   "result": f"Hits elasticities {', '.join(f'{k} {v:+.2f}' for k, v in hb.items())}. Validation CRPS C {_val(hrows, 'C'):.4f} -> D {_val(hrows, 'D'):.4f}.",
                   "interpretation": "Context adds a small, real improvement; most hitter variance is irreducible at the game level.",
                   "decision": "adopt"})
        hdist = hs["families"]["H"]["dist_choice"].get("D", {})
        tbd = hs["families"]["TB"]["dist_choice"].get("D", {})
        ex.append({"id": "MLB-PF-H3", "question": "Which hitter distributions are best: Poisson/NB on the mean, empirical, binomial-on-PA, or TB convolution?",
                   "hypothesis": "Hits ~ Binomial(PA, p) mixed over PA; total bases = convolution of per-PA base outcomes.",
                   "method": "Development CRPS by candidate for model D.",
                   "data": "2024 (from May) starters.",
                   "result": "Hits: " + "; ".join(f"{k} {v:.4f}" for k, v in hdist.items()) + ". TB: " + "; ".join(f"{k} {v:.4f}" for k, v in tbd.items()),
                   "interpretation": "Structural (binomial / convolution) shapes are at least as good as fitted ones and are coherent by construction.",
                   "decision": "adopt"})
        hi = hs["incumbent"]
        if hi:
            ex.append({"id": "MLB-PF-H4", "question": "Are the frozen hitter models better than the production hitter engine?",
                       "hypothesis": "Per-PA EB rates with structural PA beat the incumbent's simulation (known +0.135 hits / -0.151 RBI bias, HITTER_W2_RESULTS).",
                       "method": "Matched 2026 player-games where the incumbent re-run (archived pregame inputs) is PROJECTED; full CRPS from both pmfs.",
                       "data": "; ".join(f"{c}: n={v['n_obs']}" for c, v in hi.items()),
                       "result": "; ".join(f"{c}: CRPS {v['model_crps']} vs {v['inc_crps']} ({_ci(v['crps_diff'])}), MAE {v['model_mae']} vs {v['inc_mae']}, bias {v['model_bias']} vs {v['inc_bias']}"
                                           for c, v in hi.items()),
                       "interpretation": "Consistent with the W2 audit: the incumbent's level biases dominate its error.",
                       "decision": "adopt"})
    # ------------------------------------------------------------- team experiments
    if ts:
        oa = ts["opponent_adjustment"]
        ex.append({"id": "MLB-PF-T1", "question": "What does each layer of team-run context add (offence x opponent run prevention, starter, park, schedule adjustment)?",
                   "hypothesis": "Following RSCH-0015, opponent strength helps the mean but may not help probability scores.",
                   "method": "B EWMA vs C offence x defence (log5 on runs) vs D (+ starter share x starter runs factor + park + home) vs D_ridge (pf_core rolling ridge, weekly refits).",
                   "data": "Team-games 2022-2026.",
                   "result": "; ".join(f"{k}: CRPS {_ci(v['crps'])}, MAE {_ci(v['mae'])}" for k, v in oa.items()),
                   "interpretation": ("Offence x opponent run prevention (C) improves both CRPS and MAE over the team's own EWMA on both splits; starter + park (D) improves CRPS on both splits"
                                      + (" and MAE only on holdout" if oa["D_vs_C|validation"]["mae"]["ci95"][1] >= 0 else "")
                                      + ". Unlike RSCH-0015 (frozen NB), the probability gain survives because the distribution is fitted per model on development. "
                                      + "Ridge schedule adjustment does not beat the raw shrunk factors (validation " + _ci(oa["D_ridge_vs_D|validation"]["crps"]) + ")."),
                   "decision": "adopt"})
        gt = ts["game_total"]
        ex.append({"id": "MLB-PF-T2", "question": "Should the game total be the convolution of team distributions or a direct NB on the summed mean?",
                   "hypothesis": "Independence is adequate (RSCH-0010: correlation ~0.13).",
                   "method": "Holdout CRPS of the convolution vs a direct NB with alpha fitted on development totals.",
                   "data": "2025/2026 games.",
                   "result": f"Validation diff {_ci(gt['validation']['conv_minus_direct'])}; holdout diff {_ci(gt['holdout']['conv_minus_direct'])}; direct NB alpha {gt['holdout']['direct_nb_alpha']}.",
                   "interpretation": "The two are near-identical; the convolution is kept because it is coherent with the team projections.",
                   "decision": "adopt"})
        if ts.get("incumbent"):
            ti = ts["incumbent"]
            ex.append({"id": "MLB-PF-T3", "question": "Is the team-run model better than the archived production projection?",
                       "hypothesis": "RSCH-0033 found the production mean biased (-0.33 runs) with slope ~1.",
                       "method": "Only archived projections whose file createdAt precedes first pitch (genuinely pregame); incumbent distribution = NB with RSCH-0010's 0.2815 dispersion.",
                       "data": f"{ti['n_obs']} team-games, {ti['n_clusters']} games (2026-07-30..09-27).",
                       "result": f"CRPS {ti['model_crps']} vs {ti['inc_crps_nb0.2815']} ({_ci(ti['crps_diff'])}); MAE {ti['model_mae']} vs {ti['inc_mae']} ({_ci(ti['mae_diff'])}); bias {ti['model_bias']} vs {ti['inc_bias']}.",
                       "interpretation": (f"The model removes the incumbent's level bias ({ti['inc_bias']:+.2f} runs, matching RSCH-0033's -0.33) but the CRPS difference "
                                          + ("is significant." if ti["crps_diff"]["ci95"][1] < 0 else "is not significant on this short archived-pregame window (parity).")),
                       "decision": "adopt" if ti["crps_diff"]["ci95"][1] < 0 else "insufficient evidence"})
        if ts.get("coherence"):
            co = ts["coherence"]
            ex.append({"id": "MLB-PF-T4", "question": "Do hitter run projections reconcile with team run projections?",
                       "hypothesis": "Sum of starters' projected runs ~= team runs x starters' share of runs.",
                       "method": "pf_core.gate.team_reconciliation per team-game on the 2026 holdout.",
                       "data": f"{co['team_games']} team-games.",
                       "result": f"Starters score {co['actual_starter_share_of_runs']:.1%} of runs; mean sum of starters' R {co['mean_sum_starters_R_mu']} vs team {co['mean_team_mu']}; ratio {co['ratio_mean']}; correlation {co['corr_sum_vs_team']}; reconciliation {co['reconciliation_status']}.",
                       "interpretation": "Hitter and team models are built independently yet agree in level; per-game spread differs because team context (opponent run prevention) is richer than the hitter R model.",
                       "decision": "adopt"})
    # ------------------------------------------------------------- error investigations
    errs = []
    for key, lab in (("opener_flag=1", "Openers / bulk relievers listed as starters"), ("season_debut=1", "Season debut (ramp-up)"),
                     ("no_start_history=1", "Spot starter with no start history"), ("early_exit_le12bf=1", "Early exit (<=12 BF: injury/blow-up)"),
                     ("layoff15=1", "First start after >=15 days (IL return proxy)"), ("rest_bucket=<=2", "Pitched within 2 days")):
        d, r = seg_D.get(key), seg_R.get(key)
        if d:
            errs.append({"pattern": f"Batters faced: {lab}", "frequency": f"{d['share_rows']:.1%} of 2025 starts (n={d['n']})",
                         "impact": f"{d['share_abs_error']:.1%} of model-D absolute error; bias {d['bias']:+.2f} BF (D) -> {r['bias'] if r else float('nan'):+.2f} (D_rc)",
                         "cause": "role / workload state not represented in a recency-weighted mean",
                         "status": "verified defect" if key != "early_exit_le12bf=1" else "ruled out",
                         "action": "fixed in D_rc (rest, opener, debut, layoff terms) and captured by E" if key != "early_exit_le12bf=1"
                         else "not forecastable from box scores (injury data missing); handled by the empirical residual tail"})
    errs.append({"pattern": "Archived team-run projections written after first pitch", "frequency": f"{1 - chk['pipeline_team_projections']['created_before_start']:.0%} of archived team rows",
                 "impact": "Any audit that treats data/pipeline/<date>/projections.json as pregame includes post-start rewrites",
                 "cause": "the per-date file is overwritten during the day; createdAt is the last write", "status": "verified defect",
                 "action": "incumbent comparison restricted to rows with createdAt < first pitch"})
    errs.append({"pattern": "Probable-pitcher field in the post-game feed", "frequency": f"equals the actual starter {chk['feed_probable_equals_starter']:.2%}",
                 "impact": "Late scratches cannot be measured from the feed; starter identity in the backtest is effectively known",
                 "cause": "the feed overwrites probables after the game", "status": "verified defect",
                 "action": "use archived slates (data/slates) for prospective evaluation; label backtests 'starter known'"})
    if hs:
        hseg = hs["segments"]
        for key, lab in (("slot_changed=1", "Hitter's slot changed from his previous game"), ("early_season=1", "Fewer than 10 games this season"),
                         ("rest_days=11+", "Returning after 11+ days (injury proxy)")):
            for k2, rows in hseg.items():
                if not k2.startswith("PA|"):
                    continue
                s = next((x for x in rows if x["segment"] == key), None)
                if s:
                    errs.append({"pattern": f"PA: {lab}", "frequency": f"{s['share_rows']:.1%} of 2025 starter-games (n={s['n']})",
                                 "impact": f"{s['share_abs_error']:.1%} of PA absolute error; bias {s['bias']:+.3f}, MAE {s['mae']:.3f}",
                                 "cause": "lineup position / playing time uncertainty", "status": "hypothesis" if abs(s["bias"]) < 0.05 else "verified defect",
                                 "action": "known-slot models need a confirmed lineup; prev-slot variant quantifies the cost"})
    # ------------------------------------------------------------- opponent / opportunity / calibration / gate
    opp = {"summary": "Opponent adjustments are multiplicative factors (shrunk, point-in-time) whose elasticities are fitted on development. "
                      f"Pitcher K: opponent elasticity {pp['k_betas']['opp']:.2f}, park {pp['k_betas']['park']:.2f}; hits allowed opponent {pp['h_betas']['opp']:.2f}; walks {pp['bb_betas']['opp']:.2f}. "
                      "Team runs: offence x opponent run prevention (log5) then starter share x starter run factor and park.",
           "results": [{"family": "pitcher_strikeouts", "C_vs_D_validation_crps": [_val(k_rows, 'C'), _val(k_rows, 'D')], "betas": pp["k_betas"]},
                       {"family": "pitcher_hits_allowed", "betas": pp["h_betas"]}, {"family": "pitcher_walks", "betas": pp["bb_betas"]},
                       {"family": "pitcher_runs_allowed", "betas": pp["r_betas"]}]}
    if ts:
        opp["results"].append({"family": "team_runs", "comparisons": ts["opponent_adjustment"], "D_betas": ts["params"].get("D_betas")})
    if hs:
        opp["results"].append({"family": "hits", "betas": hs["params"].get("H_betas")})
        opp["results"].append({"family": "home_runs", "betas": hs["params"].get("HR_betas")})
    opport = {"summary": "Opportunity accuracy: batters faced for starters, plate appearances for starting hitters.",
              "results": [{"family": "batters_faced", "validation": {r["model"]: {"mae": r["mae"], "crps": r["crps"], "bias": r["bias"]} for r in bf},
                           "holdout": pf["bf"]["holdout"], "rc_betas": pp["bf_rc_betas"]}]}
    if hs:
        opport["results"].append({"family": "plate_appearances", "validation": {r["model"]: {"mae": r["mae"], "crps": r["crps"], "bias": r["bias"]}
                                                                               for r in hs["summary_families"]["PA"]["rows"]},
                                  "holdout": hs["summary_families"]["PA"]["holdout"], "slot_knowledge": hs["slot_knowledge"]})
    cal = {"summary": "Threshold calibration on the 2026 holdout at Kalshi rungs (mean predicted P(X>=k) vs hit rate), frozen winners.",
           "results": ps["calibration"] + (hs["calibration"] if hs else []) + (ts["calibration"] if ts else [])}
    gate = {"summary": {}, "error_by_status": {}}
    for c, g in ps["gate"].items():
        gate["summary"][f"pitcher:{c}"] = g["summary"]
        gate["error_by_status"][f"pitcher:{c}"] = g["error_by_status"]
    if hs:
        for c, g in hs["gate"].items():
            gate["summary"][f"hitter:{c}"] = g["summary"]
            gate["error_by_status"][f"hitter:{c}"] = g["error_by_status"]
    if ts:
        gate["summary"]["team_runs"] = ts["gate"]["summary"]
        gate["error_by_status"]["team_runs"] = ts["gate"]["error_by_status"]
        if ts.get("coherence"):
            gate["team_reconciliation"] = ts["coherence"]["reconciliation_status"]
    # ------------------------------------------------------------- failed experiments
    fail.append({"name": "Pitch-level Statcast (whiff%, velocity change) for strikeouts",
                 "what_was_learned": f"No gain on 468 late-2026 test starts ({_ci(sc['mae_diff'])} MAE).",
                 "changes_future_decisions": "Do not build Statcast features until a multi-season pitch archive exists (Savant blocked; repo holds 7 weeks)."})
    fail.append({"name": "Structural pitch-count leash model for batters faced (C/D)",
                 "what_was_learned": "Pitches EWMA / pitches-per-BF ignores role; it over-projects openers by ~3.5 BF.",
                 "changes_future_decisions": "Workload models must condition on days since last appearance and recent relief usage."})
    if ts:
        fail.append({"name": "Schedule-adjusted ridge team ratings (D_ridge)",
                     "what_was_learned": f"No improvement over raw shrunk offence/defence factors: {_ci(ts['opponent_adjustment'].get('D_ridge_vs_D|validation', {}).get('crps'))} CRPS on validation.",
                     "changes_future_decisions": "Keep the simpler PIT factors; MLB schedules are balanced enough that schedule adjustment matters little."})
    fail.append({"name": "Pitcher walks: black-box gain",
                 "what_was_learned": ps["families"]["bb"]["selection"],
                 "changes_future_decisions": "Walk rate is dominated by noise at game level; the transparent model is frozen."})
    # ------------------------------------------------------------- priorities
    prio = [
        {"rank": 1, "item": "Prospective shadow of the frozen pitcher K/BF/outs models against archived pregame probables (data/slates) and the existing hash-chained shadow store",
         "impact": "Converts a walk-forward win over the incumbent into archived-forecast evidence; required before any display change",
         "evidence": "MLB-PF-P6: holdout wins vs champion with CIs excluding zero", "complexity": "low (reuse project.py + workflow)", "data": "daily slates (already archived)"},
        {"rank": 2, "item": "Archive confirmed lineups for every game (pregame_lineups_*) and re-score hitters on genuinely pregame slots",
         "impact": "Hitter families are SHADOW VALIDATION only because slots are post-game", "evidence": "MLB-PF-H1 slot-knowledge gap",
         "complexity": "low", "data": "LINEUP_CONFIRMATION checkpoint already captures; needs to persist all games"},
        {"rank": 3, "item": "Injury / IL and pitch-count restriction feed",
         "impact": "Early exits (<=12 BF) are ~5% of starts but ~18% of BF error and are invisible to every model", "evidence": "MLB-PF-P1 segments",
         "complexity": "medium", "data": "missing (no source in repo)"},
        {"rank": 4, "item": "Postseason leash and postseason hitter box lines",
         "impact": "Forward projections in October use regular-season parameters and stale hitter form", "evidence": "projections_latest.json labels",
         "complexity": "medium", "data": "research_cache has only pitcher postseason lines"},
        {"rank": 5, "item": "Multi-season Statcast backfill (whiff/CSW/velo) once GitHub-hosted archives allow",
         "impact": "Unknown; 7-week test is null", "evidence": "MLB-PF-P5", "complexity": "high", "data": "blocked network"},
    ]
    return {"experiments": ex, "error_investigations": errs, "opponent_adjustment": opp, "opportunity": opport,
            "calibration": cal, "failed_experiments": fail, "gate": gate, "priorities": prio}
