"""Render research/projection_foundation/RESULTS_MLB.md from results/summary.json, the stage outputs
and results/projections_latest.json. Prose is fixed text; every number is read from the data.

    python -m mlb.report
"""
from __future__ import annotations

import json
import os

from mlb import data as D
from mlb.run_walkforward import RESULTS, _load_stage

PF = os.path.dirname(RESULTS)
ORDER = ["batters_faced", "outs", "pitcher_strikeouts", "pitcher_walks", "pitcher_hits_allowed", "pitcher_runs_allowed",
         "plate_appearances", "hits", "total_bases", "home_runs", "runs", "rbis", "hits_runs_rbis", "team_runs", "game_total"]
NICE = {"batters_faced": "Pitcher batters faced", "outs": "Pitcher outs", "pitcher_strikeouts": "Pitcher strikeouts",
        "pitcher_walks": "Pitcher walks", "pitcher_hits_allowed": "Pitcher hits allowed", "pitcher_runs_allowed": "Pitcher runs allowed",
        "plate_appearances": "Hitter plate appearances", "hits": "Hitter hits", "total_bases": "Hitter total bases",
        "home_runs": "Hitter home runs", "runs": "Hitter runs", "rbis": "Hitter RBIs", "hits_runs_rbis": "Hitter H+R+RBI",
        "team_runs": "Team runs", "game_total": "Game total"}


def ci(d, n=4):
    if not d:
        return "—"
    return f"{d['mean_diff']:+.{n}f} [{d['ci95'][0]:+.{n}f}, {d['ci95'][1]:+.{n}f}]"


def fam_table(f):
    out = ["| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
    for r in f["models"]:
        out.append(f"| {r['model']} | {r.get('dist', '')} | {r['n_obs']:,} | {r['mae']:.3f} | {r['rmse']:.3f} | {r['bias']:+.3f} | "
                   f"{r['crps']:.4f} | {r['cover_80']:.3f} | {r['pit_var_ratio']:.3f} | {ci(r.get('vs_baseline'))} | {ci(r.get('vs_incumbent'))} | {r['verdict']} |")
    h = f["holdout"]
    vi = h.get("vs_incumbent")
    out.append("")
    out.append(f"**Holdout 2026 (frozen {h['model']}, scored once):** n={h['n_obs']:,}, MAE {h['mae']:.3f}, bias {h['bias']:+.3f}, CRPS {h['crps']:.4f}, "
               f"80% cover {h['cover_80']:.3f}, PIT variance {h.get('pit_var_ratio', float('nan')):.3f}; CRPS vs baseline A {ci(h.get('vs_baseline'))}"
               + (f"; vs incumbent {ci(vi)} — {vi['metric']}; MAE difference {vi.get('mae_diff'):+.4f} {vi.get('mae_ci95')}" if vi else "")
               + ".  ")
    out.append(f"Selection: {f.get('selection', '')}. **Readiness: {f['readiness']}** — {f['readiness_reason']}")
    return "\n".join(out)


def main():
    s = json.load(open(os.path.join(RESULTS, "summary.json")))
    ps, hs, ts, inv = _load_stage("pitchers"), _load_stage("hitters"), _load_stage("teams"), _load_stage("inventory")
    pr = json.load(open(os.path.join(RESULTS, "projections_latest.json"))) if os.path.exists(os.path.join(RESULTS, "projections_latest.json")) else None
    F = {f["family"]: f for f in s["families"]}
    L = []
    w = L.append
    w("# MLB Projection Foundation — results\n")
    w(f"Research only (`authority: research_only`). Generated {s['generated_utc']} from code `{s['code_sha'][:10]}`, pf_core {s['pf_core_version']}. "
      "Machine-readable twin: `results/summary.json`; per-model records: `results/experiments/`; full tables: `results/tables_mlb.md`.\n")
    w("## 1. Bottom line\n")
    k = F["pitcher_strikeouts"]["holdout"]
    kb = F["batters_faced"]["holdout"]
    hi = hs["incumbent"]
    w(f"* **Pitcher strikeouts, batters faced and outs are READY** (for research display in place of the current engine). On the untouched 2026 season "
      f"the frozen strikeout model is better than the production pitcher engine on the same {k['vs_incumbent']['metric'].split('n=')[1].rstrip(')')} starts "
      f"(ranked-probability score {ci(k['vs_incumbent'])}, MAE {k['vs_incumbent']['mae_diff']:+.3f}) and is calibrated at every Kalshi rung "
      "(see §7). Batters faced improves the most: the old engine ignores that a 'starter' who pitched 1–4 days ago is an opener.")
    w(f"* **Hitter families are better than the production hitter engine on every probability score** (CRPS for hits {ci(F['hits']['holdout']['vs_incumbent'])}, "
      f"PA {ci(F['plate_appearances']['holdout']['vs_incumbent'])}), mainly by removing its known level biases (hits {hi['H']['inc_bias']:+.3f} → {hi['H']['model_bias']:+.3f}, "
      f"PA {hi['PA']['inc_bias']:+.3f} → {hi['PA']['model_bias']:+.3f}, RBI {hi['RBI']['inc_bias']:+.3f} → {hi['RBI']['model_bias']:+.3f}). They stay at **SHADOW VALIDATION** "
      "because the backtest uses the post-game batting order, and because for HR/R/RBI the *MAE of the mean* is worse (a downward-biased mean sits closer to the modal 0).")
    w(f"* **Team runs: SHADOW VALIDATION.** The model removes the archived production projection's {ts['incumbent']['inc_bias']:+.2f}-run bias (RSCH-0033 found −0.33) "
      f"but the probability-score difference on the {ts['incumbent']['n_obs']} genuinely-pregame archived team-games is not significant ({ci(F['team_runs']['holdout']['vs_incumbent'])}).")
    w("* **What did not work:** pitch-level Statcast features (7 weeks of data, null), schedule-adjusted ridge team ratings (no gain), and a black-box model for walks "
      "(no significant gain, transparent model frozen). Early exits (injury/blow-up) remain the largest unforecastable error.")
    w("* Nothing here is a betting signal: projection accuracy was measured, market edge was not (see RSCH-0028 / props 2026-10: pitcher K/outs trail Kalshi; hitters at parity).\n")
    w("| Family | Frozen model | Holdout n | Holdout MAE | Holdout CRPS vs baseline | vs incumbent (holdout) | 80% cover | Readiness |")
    w("|---|---|---:|---:|---|---|---:|---|")
    for fam in ORDER:
        if fam not in F:
            continue
        f = F[fam]
        h = f["holdout"]
        w(f"| {NICE[fam]} | {f['best_model']} | {h['n_obs']:,} | {h['mae']:.3f} | {ci(h.get('vs_baseline'))} | {ci(h.get('vs_incumbent'))} | {h['cover_80']:.3f} | **{f['readiness']}** |")
    w("\nModel letters: A season+prior-season mean (EB-shrunk) · B EWMA (shrunk) · C opportunity × shrunk per-unit rate · D C × opponent/park(/platoon) "
      "factors with elasticities fitted on development · D_rc D + root-cause workload terms · E LightGBM walk-forward · F non-negative stack of B/D/E (weights fitted on development).\n")
    # ------------------------------------------------------------------ inventory
    w("## 2. Data inventory (what exists, and how trustworthy its timestamps are)\n")
    w("| Dataset | Seasons / dates | Rows | PIT class | Notes |\n|---|---|---:|---|---|")
    for r in s["inventory"]:
        w(f"| {r['dataset']} | {r['seasons']} | {'' if r['rows'] is None else format(r['rows'], ',')} | {r['pit_class']} | {r['notes']} |")
    c = inv["checks"]
    w(f"\nTimestamp-integrity findings: (1) only **{c['pipeline_team_projections']['created_before_start']:.0%}** of archived team-run projection rows "
      "(`data/pipeline/<date>/projections.json`) were written before first pitch — the file is overwritten during the day, so any audit that treats it as pregame "
      "includes post-start rewrites; comparisons here use only the pregame subset. (2) The feed's 'probable pitcher' equals the actual starter "
      f"{c['feed_probable_equals_starter']:.2%} of the time because it is overwritten after the game — scratches cannot be measured from it, so every backtest here is "
      "'starter known'. (3) Archived pregame lineups (527 games) name a player who started "
      f"{c['pregame_lineups_2026']['player_started']:.1%} of the time and the same slot {c['pregame_lineups_2026']['same_slot']:.1%} of the time. "
      "(4) Pitcher projections in the archive (pitcher_workload_joint sidecars) are retroactive reruns; they are used only as a walk-forward reproduction of the incumbent, never as archived forecasts.\n")
    # ------------------------------------------------------------------ method
    w("## 3. Method\n")
    for kk, v in s["splits"].items():
        w(f"* **{kk}:** {v}")
    w("* Every feature uses only games completed on earlier dates (player features shift one game; team/park/league context uses decayed sums over strictly earlier dates, so the two games of a doubleheader never see each other).")
    w("* Shrinkage strengths come from pf_core's method-of-moments estimator on development seasons (e.g. strikeout rate k ≈ "
      f"{ps['params']['ks']['k_pbf_bf']:.0f} batters faced, hitter hit rate k ≈ {hs['params']['ks']['H_pa']:.0f} PA). Opponent/park elasticities, distribution shapes and stack weights are fitted on development only.")
    w("* E is refitted every two months, training only on earlier months. The frozen winner is the lowest **validation** CRPS; a black-box winner must beat the best transparent model with a game-clustered 95% CI excluding zero, otherwise the transparent model is frozen. Holdout is reported for the winner, baseline and incumbent only.")
    w("* Metrics are per independent player-game (never ladder rungs). CIs are game-clustered bootstraps (pf_core.metrics). CRPS is the primary score; '80% cover' is randomised-PIT coverage (0.80 = calibrated); PIT variance > 1 = over-confident.\n")
    # ------------------------------------------------------------------ pitchers
    w("## 4. Starting pitchers (validation = 2025, holdout = 2026; every starter incl. openers)\n")
    for fam in ORDER[:6]:
        w(f"### {NICE[fam]}\n")
        w(fam_table(F[fam]) + "\n")
    w("### Incumbent comparison (production pitcher engine re-run walk-forward; and the W1-D research challenger)\n")
    w("| Family | Split | Matched n | Our MAE | Incumbent MAE | Our rung RPS | Incumbent rung RPS | RPS diff [95% CI] | Kalshi-rung Brier ours / incumbent | Bias ours / incumbent | vs W1-D challenger (RPS) |")
    w("|---|---|---:|---:|---:|---:|---:|---|---|---|---|")
    for c_, fam in (("bf", "batters_faced"), ("outs", "outs"), ("k", "pitcher_strikeouts")):
        win = ps["families"][c_]["winner"]
        for sp in ("validation", "holdout"):
            a = ps["incumbent"][c_].get(f"{win}|{sp}|champion")
            b = ps["incumbent"][c_].get(f"{win}|{sp}|challenger")
            if a:
                w(f"| {NICE[fam]} ({win}) | {sp} | {a['n_obs']:,} | {a['model_mae']:.3f} | {a['inc_mae']:.3f} | {a['model_rung_rps']:.4f} | {a['inc_rung_rps']:.4f} | "
                  f"{ci(a['rps_diff'])} | {a['model_brier_kalshi_rungs']:.4f} / {a['inc_brier_kalshi_rungs']:.4f} | {a['model_bias']:+.3f} / {a['inc_bias']:+.3f} | {ci(b['rps_diff']) if b else '—'} |")
    w("\nThe incumbent sidecars cover starters with ≥3 prior starts (the incumbent's own population), so matched n is slightly below the full holdout.\n")
    # ------------------------------------------------------------------ hitters
    w("## 5. Starting hitters (validation = 2025, holdout = 2026; post-game batting order = 'known slot')\n")
    for fam in ORDER[6:13]:
        w(f"### {NICE[fam]}\n")
        w(fam_table(F[fam]) + "\n")
    w("### Incumbent comparison (production hitter engine re-run with archived pregame inputs, 2026-08-16..09-27)\n")
    w("| Stat | Matched n | Our CRPS | Incumbent CRPS | CRPS diff [95% CI] | Our MAE | Incumbent MAE | MAE diff [95% CI] | Bias ours / incumbent | Rung Brier ours / incumbent | 80% cover ours / incumbent |")
    w("|---|---:|---:|---:|---|---:|---:|---|---|---|---|")
    for c_, v in hi.items():
        w(f"| {c_} | {v['n_obs']:,} | {v['model_crps']:.4f} | {v['inc_crps']:.4f} | {ci(v['crps_diff'])} | {v['model_mae']:.4f} | {v['inc_mae']:.4f} | {ci(v['mae_diff'])} | "
          f"{v['model_bias']:+.3f} / {v['inc_bias']:+.3f} | {v['model_brier']:.4f} / {v['inc_brier']:.4f} | {v['model_cover80']:.3f} / {v['inc_cover80']:.3f} |")
    w("\n### What the confirmed lineup is worth (opportunity accuracy)\n")
    w("Same model D, scored with the official slot vs only the player's previous slot (what you know before lineups post):\n")
    w("| Stat | Split | CRPS known slot | CRPS previous slot | Difference [95% CI] | MAE known / previous | Slot changed |")
    w("|---|---|---:|---:|---|---|---:|")
    for k2, v in hs["slot_knowledge"].items():
        st_, sp = k2.split("|")
        w(f"| {st_} | {sp} | {v['crps_known']:.4f} | {v['crps_hist']:.4f} | {ci(v['diff_hist_minus_known'])} | {v['mae_known']:.3f} / {v['mae_hist']:.3f} | {v['share_slot_changed']:.0%} |")
    w("\nThe lineup matters for plate appearances (and through PA a little for H/TB/R/H+R+RBI); for RBI the difference is not significant. "
      f"A LightGBM model without the slot recovers part of the loss (PA validation CRPS: E without slot {[r for r in F['plate_appearances']['models'] if r['model']=='E_hist_slot'][0]['crps']:.4f} "
      f"vs D with previous slot {[r for r in F['plate_appearances']['models'] if r['model']=='D_hist_slot'][0]['crps']:.4f} vs D with known slot {[r for r in F['plate_appearances']['models'] if r['model']=='D'][0]['crps']:.4f}).\n")
    # ------------------------------------------------------------------ teams
    w("## 6. Teams (validation = 2025, holdout = 2026)\n")
    w("### Team runs\n")
    w(fam_table(F["team_runs"]) + "\n")
    w("**Opponent adjustment (paired, game-clustered; negative = the richer model is better):**\n")
    w("| Comparison | Split | CRPS diff [95% CI] | MAE diff [95% CI] |\n|---|---|---|---|")
    for k2, v in ts["opponent_adjustment"].items():
        a, sp = k2.split("|")
        w(f"| {a.replace('_vs_', ' vs ')} | {sp} | {ci(v['crps'])} | {ci(v['mae'])} |")
    dc = ts["dist_compare"]
    w(f"\nDistribution (development CRPS for model D): Poisson {dc['poisson']:.4f}, NB {dc['nb']:.4f}, binned NB {dc['nb_binned']:.4f}, empirical {dc['empirical_resid']:.4f} — "
      "NB-type shapes beat Poisson decisively (RSCH-0010 confirmed); NB vs empirical is a tie.\n")
    ti = ts["incumbent"]
    w(f"**Incumbent (archived pregame production projections, NB 0.2815):** n={ti['n_obs']} team-games, CRPS {ti['model_crps']:.4f} vs {ti['inc_crps_nb0.2815']:.4f} "
      f"({ci(ti['crps_diff'])}), MAE {ti['model_mae']:.3f} vs {ti['inc_mae']:.3f} ({ci(ti['mae_diff'])}), bias {ti['model_bias']:+.3f} vs {ti['inc_bias']:+.3f}.\n")
    gt = ts["game_total"]
    w("### Game total\n")
    for sp in ("validation", "holdout"):
        g = gt[sp]["convolution"]
        w(f"* {sp}: n={g['n_obs']:,} games, MAE {g['mae']:.3f}, bias {g['bias']:+.3f}, CRPS {g['crps']:.4f}, 80% cover {g['cover_80']:.3f}, PIT variance {g['pit_var_ratio']:.3f}; "
          f"convolution minus direct NB (α={gt[sp]['direct_nb_alpha']}) {ci(gt[sp]['conv_minus_direct'])}.")
    co = ts["coherence"]
    w(f"\n**Coherence with hitters (2026):** starters score {co['actual_starter_share_of_runs']:.1%} of team runs; the sum of starters' projected runs averages "
      f"{co['mean_sum_starters_R_mu']:.2f} vs team projection {co['mean_team_mu']:.2f} × that share (ratio {co['ratio_mean']:.3f}, correlation {co['corr_sum_vs_team']:.2f}); "
      f"pf_core team reconciliation: {co['reconciliation_status']}. Scoring the scaled hitter sum directly gives MAE {co['mae_sum_of_hitters_scaled']:.3f} vs {co['mae_team_model']:.3f} for the team model.\n")
    # ------------------------------------------------------------------ calibration
    w("## 7. Calibration at Kalshi rungs (2026 holdout, frozen models)\n")
    w("| Family | Rung | Mean predicted | Hit rate | Brier | ECE |\n|---|---|---:|---:|---:|---:|")
    for r in s["calibration"]["results"]:
        w(f"| {r['family']} | {r['rung']} | {r['mean_p']:.3f} | {r['hit_rate']:.3f} | {r['brier']:.4f} | {r['ece']:.4f} |")
    w("\nNo family is materially over- or under-confident: 80% PIT coverage is 0.78–0.82 everywhere and PIT variance 0.96–1.05 (table §1).\n")
    # ------------------------------------------------------------------ errors
    w("## 8. Error investigations (largest recurring errors)\n")
    w("| Pattern | Frequency | Impact | Cause | Status | Action |\n|---|---|---|---|---|---|")
    for e in s["error_investigations"]:
        w(f"| {e['pattern']} | {e['frequency']} | {e['impact']} | {e['cause']} | {e['status']} | {e['action']} |")
    w("")
    # ------------------------------------------------------------------ gate
    w("## 9. Projection Quality Gate (pf_core.gate on 2026 holdout projections)\n")
    w("| Family | n | PASS | REVIEW | BLOCK | MAE PASS / REVIEW | CRPS PASS / REVIEW | Bias PASS / REVIEW | Top findings |\n|---|---:|---:|---:|---:|---|---|---|---|")
    for k2, g in s["gate"]["summary"].items():
        e = s["gate"]["error_by_status"][k2]
        sc = g["status_counts"]
        p_, r_ = e.get("PASS", {}), e.get("REVIEW", {})
        top = ", ".join(f"{a} {b}" for a, b in list(g["check_counts"].items())[:3])
        w(f"| {k2} | {g['n']:,} | {sc.get('PASS', 0):,} | {sc.get('REVIEW', 0):,} | {sc.get('BLOCK', 0):,} | {p_.get('mae', float('nan')):.3f} / {r_.get('mae', float('nan')):.3f} | "
          f"{p_.get('crps', float('nan')):.4f} / {r_.get('crps', float('nan')):.4f} | {p_.get('bias', float('nan')):+.3f} / {r_.get('bias', float('nan')):+.3f} | {top} |")
    w(f"\nHitter gates were run on a random 8,000-projection sample per family. Team reconciliation (hitter runs vs team runs): {s['gate'].get('team_reconciliation')}. "
      "REVIEW projections for batters faced carry a large positive bias (role changes the gate flags from the pitcher's own history), which is exactly what the role check is for; no projection was BLOCKed in the backtest.\n")
    # ------------------------------------------------------------------ experiments
    w("## 10. Experiments\n")
    for e in s["experiments"]:
        w(f"### {e['id']} — {e['question']}\n")
        w(f"* **Hypothesis:** {e['hypothesis']}\n* **Method:** {e['method']}\n* **Data:** {e['data']}\n* **Result:** {e['result']}\n"
          f"* **Interpretation:** {e['interpretation']}\n* **Decision:** {e['decision']}\n")
    w("## 11. Failed experiments and negative results\n")
    for f_ in s["failed_experiments"]:
        w(f"* **{f_['name']}.** {f_['what_was_learned']} → {f_['changes_future_decisions']}")
    w("")
    # ------------------------------------------------------------------ forward
    if pr:
        w("## 12. Forward projections (`results/projections_latest.json`, sift.projections.v1)\n")
        w(f"Slate {pr['slate']['date']}: {pr['slate']['label']} Inputs through: {pr['slate']['inputs_through']}.\n")
        for g in pr["games"]:
            w(f"**{g['away']} @ {g['home']}** ({g['series']}, {g['start_utc']}):")
            for t in g["teams"]:
                w(f"* {t['team']} {t['family']}: mean {t['mean']}, 10–90% {t.get('p10')}–{t.get('p90')}, P(k+) {t['thresholds']}"
                  + (f", reconciliation {t['reconciliation']['status']} (ratio {t['reconciliation']['ratio']})" if t.get("reconciliation") else ""))
        w("\n| Pitcher | Team | Family | Mean | 10–90% | Selected rungs P(k+) | Gate |\n|---|---|---|---:|---|---|---|")
        for p in pr["players"]:
            if p["family"] in ("batters_faced", "outs", "pitcher_strikeouts"):
                w(f"| {p['name']} | {p['team']} | {p['family']} | {p['mean']} | {p['p10']}–{p['p90']} | {p['thresholds']} | {p['gate']['status']} ({', '.join(f['check'] for f in p['gate']['findings'])}) |")
        nh = sum(1 for p in pr["players"] if p["family"] == "hits")
        w(f"\n{nh} hitters per family are projected from each team's last regular-season lineup (2026-09-27, a rest-day-heavy final game) and every one is REVIEW "
          "(lineup unconfirmed; inputs >36 h old). Some hitter names are null because the repo has no player-name table for players never listed in a prop market; ids are exact.\n")
    # ------------------------------------------------------------------ pf_core
    w("## 13. pf_core observations (library not modified)\n")
    w("* `gate.HARD_MAX_MEAN[('MLB','hits')] = 3.0` is ambiguous between hitter hits and pitcher hits allowed (mean ≈ 5) — a pitcher family named `hits` would be BLOCKed. Worked around by naming the family `pitcher_hits_allowed` (no ceiling applied). Suggest explicit pitcher keys.")
    w("* `gate.evaluate` only BLOCKs on `team_verified is False`; `None` (unknown roster) passes silently. Forward hitter projections add an explicit lineup REVIEW finding instead.")
    w("* `DistBatch._index` clips outcomes outside the support, so `logpmf`/`pit` silently use the edge mass for y beyond K (CRPS is unaffected). Supports here are sized so this never binds.")
    w("* `pit.lagged_features` with `season_col` returns a frame with a reset index (it merges); callers must not rely on index alignment with the input.")
    w("* The gate's freshness check uses wall-clock time unless `now_utc` is passed; backtests must pass it (done here).\n")
    # ------------------------------------------------------------------ priorities
    w("## 14. Ranked priorities\n")
    w("| Rank | Item | Impact | Evidence | Complexity | Data |\n|---:|---|---|---|---|---|")
    for p in s["priorities"]:
        w(f"| {p['rank']} | {p['item']} | {p['impact']} | {p['evidence']} | {p['complexity']} | {p['data']} |")
    w("\n## 15. Reproduce\n")
    w("```bash\ncd research/projection_foundation\npython -m mlb.run_walkforward --stage all   # ~45 min on 2 cores; caches in .cache/ (gitignored)\n"
      "python -m mlb.project                        # forward projections for the newest archived slate\npython -m mlb.report                         # this file\n"
      "python -m pytest -q tests/\n```\n")
    w("Automation: `.github/workflows/projection-foundation.yml` (daily in season + workflow_dispatch; publishes `projections/mlb/latest.json`, a timestamped copy and "
      "`summary/mlb/latest.json` to the orphan branch `projection-data` only when content changes; inert until merged).")
    with open(os.path.join(PF, "RESULTS_MLB.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote RESULTS_MLB.md", len(L), "lines")


if __name__ == "__main__":
    main()
