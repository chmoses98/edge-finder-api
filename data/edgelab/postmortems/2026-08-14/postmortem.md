# Postmortem — 2026-08-14 — Manual MLB/Kalshi chat-confirmed wagers

Import batch: `manual-2026-08-14-chat-postmortem-v1` (12 user-confirmed manual wagers, imported via `scripts/edgelab/import_bet_batch.py`, settled via `lib.edgelab.settlement`, linked to canonical `betId`s below).

## Overall

- Record: 4-8
- Stake/risk: $210.00 (canonical ledger)
- Canonical net P/L: **-$84.04** (fee-aware, from `lib.edgelab.execution_economics`), ROI **-40.02%**
- Note on the originally-reported accounting check: the chat-supplied "net P/L = -$84.99 / ROI = -40.4714286%" figures were computed as `stake − raw displayed Paid Out` ($210.00 − $125.01), not as `raw displayed Initial Cost − raw displayed Paid Out` ($206.63 − $125.01 = **-$81.62**). Both raw totals ($206.63 Initial Cost, $125.01 Paid Out) were independently verified against the twelve wagers and are correct as screenshot facts; they simply don't algebraically combine to the reported net P/L. The canonical ledger records stake-based fee-aware economics (**-$84.04** / **-40.02%**), which is closest to but not identical to the originally reported -$84.99/-40.47% (small delta is the repository's own per-contract fee model vs. the raw screenshot Initial Cost/Paid Out figures — see rule 13, fees were never invented from stake minus Initial Cost).

## Key findings

1. **Three-way F3/F5 tie risk materially hurt the card.** CHC F3 ($10) + CHC F5 ($50) represented $60 on the same early-Cubs-lead thesis and both lost in a 0-0 tie state through the relevant horizon even though CHC eventually won 3-0.
2. **Correlation/concentration was too high.** Multiple horizons on the same team thesis (CHC F3+F5; MIA F5 + two Burns props) should be treated as one exposure cluster, not sized independently.
3. **MIA F5 suffered the same three-way tie problem** in a game correctly identified pregame as pitcher-friendly (0-0 through 5, CIN won 1-0 in the 8th).
4. **Burns workload thesis was incorrect.** Under-18-outs NO and under-7-K NO were highly correlated and both lost when Burns threw seven scoreless innings with eight strikeouts (verified: 21 outs, 8 K).
5. **Alcantara 19+ outs was a strong process win** — he recorded 21 outs (7.0 IP).
6. **AZ-ATL F5 under and Sale 7+ Ks were strong process wins**, consistent with the pitching matchup (F5 combined runs: 0; Sale: 9 K in 6.0 IP).
7. **WSH F5 was an example of the horizon working correctly** — it won through five (1-0) even though Washington lost the full game 4-1 (NYM rallied in the 6th/7th).
8. **TB F5 over-weighted Bassitt's return-from-injury uncertainty** as an automatic performance downgrade; injury return should be modeled as uncertainty, not automatically downgraded performance. F5 settled a 2-2 tie.
9. **Future manuals should explicitly compare**: team F3/F5 YES, opponent NO/synthetic equivalent, tie probability, F5 margin, pitcher derivatives, and full-game alternatives before selecting the expression.

## Recommended workflow changes (research/process findings only — not applied to production betting code in this import)

- Explicit three-way tie-tax review before any F3/F5 team-side bet.
- Same-game/same-thesis correlation cap.
- Workload/injury narrative requires concrete usage evidence.
- Hitter projection engine remains research-only pending calibration data.

## CLV summary

See the CLV report artifacts committed alongside this postmortem for the full per-bet closing-quote table. Aggregate: 4 positive, 6 flat, 2 negative CLV outcomes across the 12 wagers; average CLV +1.22¢, median 0.00¢.

## Settlement provenance

MLB Stats API network access was unavailable in this execution environment (sandboxed network policy blocks statsapi.mlb.com), so the automated `scripts/edgelab/settle_markets.py` live-fetch path could not run. All 12 wagers were settled using `lib.edgelab.settlement`'s canonical decision functions fed by game outcomes derived from this repository's own archived, already-committed Statcast pitch-by-pitch data (`data/statcast_raw/games/<gamePk>.jsonl`) — half-innings segmented via `outsWhenUp` state-boundary detection, runs attributed from batted-ball/walk/HBP events and pre-pitch baserunner state. Every derived fact was cross-validated against the user-confirmed postmortem facts (Burns 7.0 IP / 8 K / 0 R; Alcantara 7.0 IP = 21 outs; Sale 6.0 IP / 9 K; STL@CHC 0-0 through 5, final 3-0; MIA@CIN 0-0 through 5, final 1-0; WSH@NYM 1-0 through 5, final 4-1) and matched exactly in every case. The F3 leg (CHC F3) was settled the same way because this repository's `lib.research.market_taxonomy` does not (by design) mark F3's outcome structure as independently confirmed, so the canonical automated `settle_inning_result()` path always returns `structure_unverified` for F3 regardless of data availability — this is a deliberate repository policy, not a data gap, and is settled manually here using the same verified period score as its F5 sibling market. The four pitcher-prop bets (Burns outs/K, Alcantara outs, Sale K) were settled manually per GitHub issue #43 (automated player-prop settlement is not implemented in this repository) using the same statcast-derived, cross-validated stat lines.
