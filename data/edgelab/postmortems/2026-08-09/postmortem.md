# EdgeLab Postmortem — 2026-08-09 (MLB / Kalshi)

## Session summary

19 confirmed wagers, 12-7, $218.58 risked, $277.31 returned, net +$58.73, ROI +26.87%.
All figures are sourced from the user's Kalshi position/settlement screenshots and
are authoritative for stake, executed price, result, and returned amount — never
replaced by archived midpoint, reconstructed recommendation price, theoretical
payout, or later market price.

Overall process: complete-market manual analysis (scanning the full archived
Kalshi corpus, not only recommendation-pipeline output) added substantial value
relative to a recommendation-pipeline-only approach. Profitable positions came
from game totals, team totals, F5, pitcher outs, NRFI, spreads, and hitter props
— several of them (COL team total, CWS team total) were not pipeline
recommendations at all. The recommendation pipeline should continue to be
treated as evidence, not as the complete market universe or as a
decision-maker in its own right.

## Settlement provenance

This session's own sandboxed egress policy blocks outbound access to
statsapi.mlb.com (confirmed via the agent-proxy status endpoint: 403 on
CONNECT). Real settlement for all 19 wagers was obtained by dispatching
`.github/workflows/edgelab-postgame.yml` (GitHub Actions run 31354603648,
`workflow_dispatch`, `date=2026-08-09`, conclusion `success`) — the repository's
own normal automated `scripts/edgelab/settle_markets.py` path, run from a
runner with real network access. Every one of the 19 tickers settled cleanly
(zero `SETTLEMENT_UNRESOLVED`), and the settled WIN/LOSS record independently
reproduces the user's screenshot-reported 12-7 record exactly. GitHub issue #43
(automatic pitcher/hitter player-prop settlement) is closed/completed in this
repository, so all 8 player-prop legs (6 pitcher-strikeout, 1 pitcher-outs, 1
hitter-hits) settled automatically through that same real pipeline rather than
needing to be preserved as unresolved manual overrides.

Each bet's exact dollar return/net P&L — which the ledger's own derived
formula does not reproduce exactly, because Kalshi trades in whole contracts
and the formula assumes fractional contract counts — was separately recorded
via `lib.edgelab.bets.confirm_realized_return`
(`confirmedReceiptSource=MANUAL_POSTMORTEM_RECEIPT`), which never touches the
objective result/status fields. Objective settlement outcome and confirmed
real cash receipt are deliberately kept as two independent, separately
sourced facts on the ledger.

## Performance by market family

| Family | Horizon | W-L | Risk | Returned | Net | ROI |
|---|---|---|---|---|---|---|
| Game totals | Full game | 2-0 | $34.65 | $63.13 | +$28.48 | +82.19% |
| Team totals | Full game | 2-0 | $25.58 | $46.35 | +$20.77 | +81.20% |
| F5 winner | F5 | 1-0 | $9.79 | $24.59 | +$14.80 | +151.17% |
| Pitcher outs | — | 1-0 | $14.77 | $25.54 | +$10.77 | +72.92% |
| NRFI | — | 1-0 | $9.81 | $20.51 | +$10.70 | +109.07% |
| Hitter hits | — | 1-0 | $7.99 | $14.35 | +$6.36 | +79.60% |
| Run line / spread | Full game | 1-0 | $5.89 | $11.37 | +$5.48 | +93.04% |
| Full-game ML | Full game | 1-2 | $32.52 | $21.15 | -$11.37 | -34.96% |
| Pitcher strikeouts | — | 2-4 | $75.64 | $50.32 | -$25.32 | -33.47% |
| F7 tie | F7 | 0-1 | $1.94 | $0.00 | -$1.94 | -100.00% |

Pitcher strikeout props consumed $75.64 — approximately 34.6% of all session
risk — and lost $25.32. All non-strikeout markets combined: risk $142.94,
returned $226.99, net +$84.05. This is a single-session finding, not proof
that K props are inherently -EV; see Proposed Investigations.

## Analytical wins

1. **DET@SF Total Runs NO on Over 7.5** — actual $19.66 at 51.44c NO
   (precision preserved, not rounded to 51c); WIN +$17.93.
2. **Chicago White Sox F5** — model 50.25% vs. archived executable 39.06c
   (rawEdge +11.19pp, calEdge +2.85%, maxBetPrice 46.33c), executed 39c. An
   ~11pp model-vs-market gap remains a flagged audit item despite the win —
   it had independent analyst support and excellent actual execution.
3. **Detroit ML** — model 59.7% vs. archived executable 53.49c (rawEdge
   +6.21%, calEdge +1.58%, maxBetPrice 55.77c), executed 55c, inside max.
   Independent manual estimate ~57-59%, consistent with the model.
4. **Chicago White Sox team total over 3.5** — non-redundant expression of
   the same White Sox-offense thesis as the F5 win.
5. **Colorado team total over 3.5** — not a pipeline recommendation; evidence
   complete-market scanning adds value.
6. **Jacob Misiorowski K ladder** — 9+ ($17.72 @ 55c) won as the larger core
   position; 10+ ($4.89 @ 38c) lost as the smaller, deliberately aggressive
   satellite. Good ladder construction.
7. **Troy Melton 18+ outs** — reasonable threshold below the riskier 19+
   range. WIN.
8. **Taylor Trammell 1+ hit** — execution 54c vs. ~53c pregame reference;
   appropriately small ($7.99) single-hitter sizing.

## Analytical misses

1. **Cristian Javier 4+ strikeouts — PRIMARY MISS.** $19.65 at 50c (+100),
   LOSS. Independent manual estimate ~62-66% fair, explicitly sized up on
   that confidence. Overweighted 19 Ks in prior 18 IP, recent 6-inning
   workload, apparent post-return improvement; underweighted post-shoulder
   uncertainty, small post-return sample, workload/BF volatility, exact
   pitch-count/leash uncertainty, opponent contact, and K-rate regression
   risk. The >12pp independent-vs-market divergence should have triggered
   skepticism, not conviction. Process grade: D+. This is not filed as an
   acceptable variance loss — the confidence estimate itself is the finding.
2. **Emerson Hancock 5+ strikeouts** — $11.77 at 45c, LOSS. Combined with Ian
   Seymour's position, created same-game exposure to both starters producing
   sufficient K/workload outcomes. Hancock should have been the first cut.
3. **Cincinnati cluster** — CIN ML ($15.72, LOSS) and Brady Singer 5+ Ks
   ($11.78, LOSS) both lost; flagged for audit on shared game-script
   dependence and combined sizing.
4. **CIN@WSH F7 Tie** — only $1.94 risked (appropriately tiny), LOSS. Cheap
   longshot price alone is not evidence of value without an explicit
   scoring-distribution / tie-probability edge.

## Pricing / CLV notes

Full bid/ask/mid/recommendation/execution/bet-up-to reconstruction is only
supportable, from real archived evidence, for: CLE@CWS F5 (CWS) and DET@SF ML
(DET) — both have real `data/execution_slip_2026-08-09.json` rows; and the
HOU@SD 10+ runs YES contract, which has a real standalone bid/ask snapshot
(`yes_bid=43c`, `yes_ask=44c`, mid 43.5c, ~2026-08-09T23:38:22Z) — though the
user's actual confirmed execution on that game was the NO side of Over 9.5 at
57c, a different rung on the same total-runs ladder. For the remaining
tickers this postmortem uses the bulk importer's own automatic bet-to-
observation linkage as the best available archived **pregame reference**
price — never labeled as placement-time, since no placement timestamp was
supplied or exists for this session. No `data/clv_snapshots/2026-08-09/`
artifact keyed to these 19 tickers with a tracked pregame/closing pair was
found; true CLV for the remaining tickers is recorded as unavailable, not
invented.

## Process errors / findings

- Pitcher-K exposure concentration (34.6% of session risk, -33.47% ROI) —
  scoped investigation into workload modeling, not a condemnation of K props.
- Same-game opposing-pitcher K exposure at TB@SEA — tightening order should
  cut the losing leg first, not flip a coin.
- Cincinnati game-script concentration across CIN ML and Singer Ks — flagged
  for audit, not resolved.
- First-inning input completeness for the CIN@WSH NRFI win was not
  verifiable from this session's archived research capture — recorded as an
  open question despite the win.
- CLV reconstruction is partial — real evidence exists for 3 of 19 tickers;
  the rest report pregame reference prices only, with true CLV marked
  unavailable rather than fabricated.
- This session's settlement path required a live GitHub Actions dispatch
  (network-blocked sandbox) rather than local settlement — documented above
  under Settlement provenance.

## Tomorrow / workflow changes

1. Keep the analytical order: independent handicap → complete archived
   market scan → model/reference comparison → disagreement audit → portfolio
   construction.
2. Continue scanning all archived markets, not only recommendation output.
3. Require a pessimistic workload sensitivity analysis before Tier A sizing
   on any pitcher prop with >7pp analyst-vs-market disagreement.
4. Review cumulative pitcher-prop exposure before finalizing the card.
5. Prefer reliable K rungs as core exposure, aggressive rungs as small
   satellites, unless the aggressive rung has clearly superior risk-adjusted
   EV.
6. Investigate automatic CLV capture for recommended/considered tickers.
7. Continue treating model recommendations as evidence, never instructions.
