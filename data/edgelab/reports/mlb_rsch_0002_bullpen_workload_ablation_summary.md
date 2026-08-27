# MLB-RSCH-0002: Bullpen Workload Ablation
RESEARCH ONLY. Production behavior unchanged -- see script module docstring.
**Conclusion: WEAK UNPROVEN**
## Overall paired result
- n=4037, independentGames=166, independentDates=12
- Control Brier=0.1931, Candidate Brier=0.1937
- Paired delta (candidate - control) Brier=0.0007, logLoss=0.0034
- 90% CI on Brier delta: [-0.0007, 0.0022]
## Segments
- **highWorkload**: n=2015, games=83, pairedDeltaBrier=0.0005
- **lowWorkload**: n=2022, games=83, pairedDeltaBrier=0.0009
- **backToBackPresent**: n=2477, games=104, pairedDeltaBrier=-0.0003
- **backToBackAbsent**: n=1560, games=62, pairedDeltaBrier=0.0022
- **highLeverageTaxedPresent**: n=2576, games=105, pairedDeltaBrier=0.0027
- **highLeverageTaxedAbsent**: n=1461, games=61, pairedDeltaBrier=-0.0029
## Market family
- **game_result**: n=294, games=152, pairedDeltaBrier=0.0004
- **game_total**: n=1685, games=164, pairedDeltaBrier=0.001
- **team_total**: n=2058, games=160, pairedDeltaBrier=0.0004
## Functional-form diagnostics
- multiplier range [1.0, 1.12], mean=1.0569
- neutral (multiplier==1.0) fraction=0.1386
- capped (at MAX_TOTAL_PENALTY) fraction=0.1084
- component firing fractions: {'backToBack': 0.4187, 'overallWorkload': 1.0, 'heavyRecentPitch': 0.5151, 'highLeverageTaxed': 0.3464}
- top team mean multipliers: [('PIT', 1.0836, 12), ('CWS', 1.0817, 11), ('HOU', 1.0791, 10), ('SF', 1.0772, 13), ('TB', 1.0747, 14)]
- extreme adjustments in small samples (n<5, mean>=1.08): []
## Eligibility
- games with a resolvable projection state: 166
- market rows excluded pre-projection: {'not_settled': 1993, 'no_mlb_game_pk_or_date': 295}
- market rows unresolvable (no matching game state / probability): 3908
