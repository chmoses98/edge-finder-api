#!/usr/bin/env python3
"""
scripts/risk_gate.py v1.0
==========================
Post-evaluation safety pass. Reads data/slate.json after build_market_ledger.py
has run, enforces:

  1. TT Safety Gate
     - All TT bets require ttInputs evidence block
     - Missing critical evidence → downgrade to PAPER
     - TT Over N: requiredRunsToWin = N+1; edge threshold 2.5% minimum
     - Max 4 TT real-money bets per slate
     - Max 20u total TT real-money stake

  2. Portfolio Composition Gate
     - TT ≤ 40% of total real-money stake
     - ML+F5 must be ≥ 50% of real-money stake if ≥2 qualify
     - Max 40u total real-money stake (daily risk cap)
     - If slate is TT-dominated and ML/F5 < 2 qualify, output PAPER_ONLY

  3. Correlation / Concentration Gate (portfolio-level, runs BEFORE
     Portfolio Composition so its downgrades are reflected in the
     family/stake math above)
     - Detects documented same-game correlation/shared-dependency groups
       (same-side ML+F5 thesis duplication, side+team-total, NRFI/YRFI
       vs. the pitcher-driven F5 markets) between entries that have
       ALREADY independently cleared the normal EV threshold — never
       touches probability models, edge computation, or executable
       pricing (scripts/build_market_ledger.py).
     - One-primary-expression: when two markets express the same win
       thesis (full-game ML + F5 ML, same side), keeps the higher-edge
       one and downgrades the other to PAPER.
     - Default max 2 real-money bets per game — excess (by ascending
       edge) downgraded to PAPER.
     - Target cap: a correlated same-game cluster's combined stake
       should not exceed ~15% of the slate's proposed daily allocation —
       trimmed (lowest edge first, at least one kept) when exceeded.
     - Downgrade-only, exactly like the TT/portfolio gates above — never
       a hard reject, never reallocates a downgraded bet's stake
       elsewhere. Every real-money-tier entry gets a `correlationGroups`
       field explaining what it was found correlated with, whether or
       not it was downgraded.

Writes a risk_gate_report to data/meta.json and exits:
  0 — GO (or PAPER_ONLY downgrade applied, slate still valid)
  1 — hard FAIL (e.g. slate.json unreadable)

Prints portfolio summary and GO / PAPER_ONLY / NO_GO decision.
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH = os.path.join(ROOT, 'data', 'slate.json')
META_PATH  = os.path.join(ROOT, 'data', 'meta.json')

# Same live/final/postponed game gate used by write_pending_bets.py and
# validate_bet_logging.py. Centralized here so all three scripts agree on
# which games can produce real-money output — divergence between this file
# and write_pending_bets.py previously caused portfolio composition (and the
# downstream validate_bet_logging expected-count) to include bets that
# write_pending_bets correctly refused to log for live/final games.
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from postponed_guard import check_game_status
from atomic_json import write_json_atomic

REAL_MONEY_TIERS  = {'HIGH', 'MEDIUM'}
TT_MARKETS        = {'TT_Away_Over', 'TT_Home_Over'}
ML_F5_MARKETS     = {'ML_Away', 'ML_Home', 'F5_ML_Away', 'F5_ML_Home'}

# ── Thresholds ────────────────────────────────────────────────────────────────
TT_MIN_EDGE_PCT        = 2.5    # below this → downgrade TT to PAPER
TT_MAX_BETS            = 4      # max real-money TT bets per slate
TT_MAX_STAKE           = 20.0   # max total TT real-money stake (u)
TT_MAX_STAKE_PCT       = 0.40   # TT ≤ 40% of total stake
ML_F5_MIN_STAKE_PCT    = 0.50   # ML+F5 ≥ 50% of total stake if ≥2 qualify
DAILY_RISK_CAP         = 40.0   # max total real-money stake (u)

GAME_MAX_REAL_MONEY_BETS   = 2      # default max actionable (real-money) bets per game
GAME_CLUSTER_MAX_STAKE_PCT = 0.15   # target cap: one correlated same-game cluster vs. proposed daily allocation

# ── Correlation / Concentration Gate ────────────────────────────────────────────
# Prevents recommendation cards from over-concentrating risk in the same game
# or shared game script, while preserving genuinely independent +EV positions.
# Operates ONLY on entries that have ALREADY independently cleared the normal
# EV/edge threshold (the same real-money-tier Accepted collection
# apply_portfolio_rules has always built) — never touches probability
# models, edge computation, or executable pricing.
#
# CORRELATION_RULES is a pairwise table over the 8 markets that can ever
# reach status='Accepted' + a real-money tier in marketLedger
# (REQUIRED_MARKETS in scripts/build_market_ledger.py minus Game_Total /
# RL_Away / RL_Home, which are unconditionally suspended/Rejected in
# scripts/build_market_ledger.py and never qualify). Neither literal
# pitcher-performance props nor a totals-under market are wired into
# marketLedger today (see
# lib/kalshi_probability_adapters.py / scripts/discover_kalshi_mlb_markets.py,
# which price/discover pitcher props for a SEPARATE research artifact,
# explicitly NOT governed by this pipeline) — so "NRFI + pitcher-
# performance", "same starting pitcher", and "low-scoring script" all
# fold into PITCHER_DEPENDENT below: F5_ML is the only real-money market
# in this ledger whose primary driver is starting-pitcher quality, and
# there are only ever two starting pitchers in a game, so a NRFI/F5_ML
# pair unambiguously shares one of them without needing a separate
# pitcher-identity match.
SAME_SIDE_THESIS      = 'SAME_SIDE_THESIS'
SIDE_TEAM_TOTAL        = 'SIDE_TEAM_TOTAL'
PITCHER_DEPENDENT      = 'PITCHER_DEPENDENT'
SAME_MARKET_BOTH_SIDES = 'SAME_MARKET_BOTH_SIDES'

CORRELATION_RULES = {
    frozenset({'ML_Away', 'F5_ML_Away'}): (
        SAME_SIDE_THESIS,
        'Full-game ML and F5 ML for the away team both express the same '
        '"away wins" thesis -- F5 is a leading indicator of the same '
        'outcome, not an independent signal.'),
    frozenset({'ML_Home', 'F5_ML_Home'}): (
        SAME_SIDE_THESIS,
        'Full-game ML and F5 ML for the home team both express the same '
        '"home wins" thesis -- F5 is a leading indicator of the same '
        'outcome, not an independent signal.'),
    frozenset({'ML_Away', 'TT_Away_Over'}): (
        SIDE_TEAM_TOTAL,
        'Away ML and the away team total both depend on the away offense '
        'outperforming the home pitching.'),
    frozenset({'F5_ML_Away', 'TT_Away_Over'}): (
        SIDE_TEAM_TOTAL,
        'Away F5 ML and the away team total both depend on the away '
        'offense outperforming the home starter.'),
    frozenset({'ML_Home', 'TT_Home_Over'}): (
        SIDE_TEAM_TOTAL,
        'Home ML and the home team total both depend on the home offense '
        'outperforming the away pitching.'),
    frozenset({'F5_ML_Home', 'TT_Home_Over'}): (
        SIDE_TEAM_TOTAL,
        'Home F5 ML and the home team total both depend on the home '
        'offense outperforming the away starter.'),
    frozenset({'NRFI', 'F5_ML_Away'}): (
        PITCHER_DEPENDENT,
        'NRFI and away F5 ML both hinge on the same two starting pitchers '
        'pitching well early -- the "same starting pitcher" / "low-'
        'scoring script" cluster.'),
    frozenset({'NRFI', 'F5_ML_Home'}): (
        PITCHER_DEPENDENT,
        'NRFI and home F5 ML both hinge on the same two starting pitchers '
        'pitching well early -- the "same starting pitcher" / "low-'
        'scoring script" cluster.'),
    frozenset({'YRFI', 'F5_ML_Away'}): (
        PITCHER_DEPENDENT,
        'YRFI and away F5 ML share the same starting-pitcher-quality '
        'driver (a shaky start raises both).'),
    frozenset({'YRFI', 'F5_ML_Home'}): (
        PITCHER_DEPENDENT,
        'YRFI and home F5 ML share the same starting-pitcher-quality '
        'driver (a shaky start raises both).'),
    frozenset({'NRFI', 'YRFI'}): (
        SAME_MARKET_BOTH_SIDES,
        'NRFI and YRFI are complementary sides of the identical first-'
        'inning event -- betting both concentrates risk in the same '
        'outcome rather than diversifying.'),
}

# Critical TT evidence fields — if any are None, downgrade to PAPER
TT_CRITICAL_FIELDS = [
    'awayProjRuns', 'homeProjRuns',   # projected runs (the betting team's)
    'kalshiPrice',                     # market price exists
    'modelProb',                       # model probability
    'line',                            # the line to beat
]

# Fields that must exist for the SPECIFIC TT side being bet
TT_CRITICAL_SIDE_FIELDS = {
    'TT_Away_Over': 'awayProjRuns',
    'TT_Home_Over': 'homeProjRuns',
}


def check_tt_evidence(entry):
    """
    Returns (ok, missing_fields) for a TT entry.
    ok=True if all critical evidence is present.
    """
    missing = []
    for field in TT_CRITICAL_FIELDS:
        if entry.get(field) is None:
            missing.append(field)
    # Side-specific
    mkt = entry.get('market', '')
    side_field = TT_CRITICAL_SIDE_FIELDS.get(mkt)
    if side_field and entry.get(side_field) is None:
        if side_field not in missing:
            missing.append(side_field)
    return (len(missing) == 0), missing


def compute_tt_inputs(entry):
    """
    Pure. Returns the ttInputs summary block for a TT marketLedger entry
    as a NEW dict, reading `entry` but never mutating it. Same field-by-
    field construction enrich_tt_inputs() has always used; factored out
    so the decision logic below can be evaluated without touching the
    caller's entry until the impure wrapper (apply_tt_safety) decides to
    apply it.
    """
    mkt  = entry.get('market', '')
    line = entry.get('line')
    tt_inputs = {
        'projectedTeamRuns':    entry.get('awayProjRuns') if 'Away' in mkt else entry.get('homeProjRuns'),
        'teamTotalLine':        line,
        'requiredRunsToWin':    (int(line) + 1) if line is not None else None,
        'modelProbOver':        entry.get('modelProb'),
        'marketImpliedProb':    entry.get('kalshiImplied') or entry.get('kalshiVF'),
        # Production Fee-Aware Net EV Integration milestone: prefers
        # netExecutableEdge -- same fee-aware metric the TT_MIN_EDGE_PCT
        # check below reads (_entry_edge) -- but preserves the original
        # None-when-truly-unknown semantics this display/audit field has
        # always had (unlike _entry_edge's own 0-fallback, which exists
        # only for its sort/comparison use case).
        'edgePct': (
            entry.get('netExecutableEdge') if entry.get('netExecutableEdge') is not None
            else (entry.get('edge') or entry.get('calibratedEdgeVsExecutable'))
        ),
        'calibrationFactor':    entry.get('calibrationFactor'),
        'awayProjRuns':         entry.get('awayProjRuns'),
        'homeProjRuns':         entry.get('homeProjRuns'),
        # Pitcher/context fields — populated if available from enrich_data output
        # These are advisory — absence triggers PAPER downgrade
        'opposingStarterName':  None,   # not yet in ledger — future enrichment
        'starterXfip':          None,
        'bullpenRating':        None,
        'parkAdjustment':       entry.get('awayProjRuns'),  # park_adj embedded in proj
        'weatherAdjustment':    None,
        'confirmedLineup':      entry.get('lineupPosted'),
        'lineupDataQuality':    entry.get('lineupDataQuality'),
        'dataCompletenessScore': None,
        'missingInputs':        [],
    }
    missing_advisory = []
    if tt_inputs['opposingStarterName'] is None:
        missing_advisory.append('opposingStarterName')
    if tt_inputs['starterXfip'] is None:
        missing_advisory.append('starterXfip')
    if tt_inputs['bullpenRating'] is None:
        missing_advisory.append('bullpenRating')
    if tt_inputs['weatherAdjustment'] is None:
        missing_advisory.append('weatherAdjustment')
    tt_inputs['missingInputs'] = missing_advisory
    tt_inputs['dataCompletenessScore'] = round(
        1.0 - len(missing_advisory) / 4.0, 2
    )
    return tt_inputs


def enrich_tt_inputs(entry):
    """Add ttInputs summary block to a TT marketLedger entry in place."""
    entry['ttInputs'] = compute_tt_inputs(entry)
    return entry


def evaluate_candidate_tt_risk(entry):
    """
    Pure decision function (Phase 7 Part 6). Takes a read-only TT
    marketLedger entry and returns a decision object describing exactly
    what apply_tt_safety() should do with it, without reading the clock,
    touching any file, printing, or mutating `entry` in any way:

      {
        'ttInputs':           the enrichment block (always present),
        'requiredRunsToWin':  int(line)+1, or None if line is None,
        'evaluated':          True iff status=='Accepted' and tier is
                               real-money (HIGH/MEDIUM) — the same gate
                               apply_tt_safety has always used before
                               running the two downgrade checks,
        'reasons':            [] unless evaluated and at least one rule
                               failed; evidence-check reason (if any)
                               always precedes the edge-check reason
                               (legacy order, not reordered),
        'downgrade':          bool(reasons),
      }
    """
    tt_inputs = compute_tt_inputs(entry)
    line = entry.get('line')
    required_runs = (int(line) + 1) if line is not None else None

    tier   = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
    status = entry.get('status', '')
    evaluated = (status == 'Accepted' and tier in REAL_MONEY_TIERS)

    reasons = []
    if evaluated:
        # Production Fee-Aware Net EV Integration milestone: _entry_edge()
        # prefers netExecutableEdge -- this TT-specific 2.5pp floor is a
        # SECOND, independent check on top of build_market_ledger.py's
        # own (already fee-aware) tier/threshold gate, so it must read
        # the same fee-aware metric to stay consistent with it rather
        # than re-permitting a row on its stale gross edge.
        edge = _entry_edge(entry)
        ev_ok, missing_ev = check_tt_evidence(entry)
        if not ev_ok:
            reasons.append(f"TT_MODEL_INPUTS_INCOMPLETE: missing {missing_ev}")
        if edge < TT_MIN_EDGE_PCT:
            reasons.append(f"TT_EDGE_BELOW_2.5pct: edge={edge:.2f}%")

    return {
        'ttInputs': tt_inputs,
        'requiredRunsToWin': required_runs,
        'evaluated': evaluated,
        'reasons': reasons,
        'downgrade': bool(reasons),
    }


def apply_tt_safety(slate, now_ts=None):
    """
    Applies TT safety rules to all TT entries in the marketLedger.
    Modifies slate in place. Returns list of downgrade events.

    Thin impure shell around evaluate_candidate_tt_risk(): the pure
    function decides what should happen to each candidate, this function
    is the only place that actually writes those decisions back onto the
    entry — mutation is isolated here, not inside the decision function.
    """
    downgrades = []

    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"

        # Skip quarantined games entirely — they produce no real-money output
        if g.get('excludedFromSlate'):
            continue

        # Skip live/final/postponed games — write_pending_bets.py will never
        # log a real-money bet for these, so they must not factor into the
        # TT safety pass either (same gate, same "now").
        if check_game_status(g, current_utc=now_ts).get('shouldSkip'):
            continue

        for entry in g.get('marketLedger', []):
            mkt  = entry.get('market', '')
            if mkt not in TT_MARKETS:
                continue

            decision = evaluate_candidate_tt_risk(entry)

            # Always enrich ttInputs
            entry['ttInputs'] = decision['ttInputs']

            if not decision['evaluated']:
                continue

            # Set requiredRunsToWin whenever line is present, regardless
            # of whether this entry gets downgraded — only reached for
            # evaluated (Accepted + real-money-tier) entries, exactly as
            # the original single-pass implementation did (the `continue`
            # above ran before this line ever executed for other
            # statuses/tiers).
            if entry.get('line') is not None:
                entry['requiredRunsToWin'] = decision['requiredRunsToWin']

            if decision['downgrade']:
                reasons = decision['reasons']
                entry['status']          = 'Accepted'    # keep in ledger
                entry['confidence']      = 'PAPER'
                entry['confidenceTier']  = 'PAPER'
                entry['betSize']         = 1.0
                entry['realMoneyBlocked'] = True
                entry['blockReason']     = '; '.join(reasons)
                entry.setdefault('gatesFired', []).extend(reasons)
                downgrades.append({
                    'game': game, 'market': mkt, 'reason': reasons
                })

    return downgrades


def _entry_edge(entry):
    """
    Production Fee-Aware Net EV Integration milestone: prefer
    `netExecutableEdge` (fee-aware -- see build_market_ledger.py's
    build_edge_fields()) when present, falling back to the legacy
    fee-blind `edge`/`calibratedEdgeVsExecutable` for any row that
    predates this milestone (e.g. a stale slate.json). Correlation
    pruning/best-expression selection (evaluate_correlation_gate below)
    and the TT-cap sort (build_risk_portfolio) both call this function,
    so ranking becomes fee-aware everywhere a decision is made about
    which correlated candidate to keep, with a single explicit change
    here rather than an implicit reinterpretation of the `edge` field's
    established meaning (which many other consumers outside this file
    also read -- see docs/PRODUCTION_FEE_AWARE_NET_EV.md for why this
    narrow, explicit preference was chosen over repurposing `edge`).
    """
    net = entry.get('netExecutableEdge')
    if net is not None:
        return float(net)
    return float(entry.get('edge') or entry.get('calibratedEdgeVsExecutable') or 0)


def _entry_stake(entry):
    return float(entry.get('betSize') or 0)


def correlation_edge(market_a, market_b):
    """Pure. (type, reason) if market_a/market_b are a documented
    CORRELATION_RULES pair, else None. Order-independent; a market never
    correlates with itself."""
    if not market_a or not market_b or market_a == market_b:
        return None
    return CORRELATION_RULES.get(frozenset({market_a, market_b}))


def build_same_game_clusters(game_entries):
    """
    Pure. `game_entries` = list of (game_label, entry) tuples for ONE
    game only. Returns a list of clusters, each a connected component of
    CORRELATION_RULES edges among those entries:

      {'types': [...], 'reasons': [...], 'entries': [(game, entry), ...]}

    An entry with no documented correlation to any OTHER entry in this
    same list is never included in any cluster — it is genuinely
    independent, not treated as a "cluster of one" (requirement: two
    genuinely independent bets from one game must remain eligible).
    """
    n = len(game_entries)
    adj = [[] for _ in range(n)]
    edge_info = {}
    for i in range(n):
        for j in range(i + 1, n):
            rule = correlation_edge(game_entries[i][1].get('market'), game_entries[j][1].get('market'))
            if rule:
                adj[i].append(j)
                adj[j].append(i)
                edge_info[frozenset({i, j})] = rule

    seen = set()
    clusters = []
    for i in range(n):
        if i in seen or not adj[i]:
            continue
        comp, stack = [], [i]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            comp.append(node)
            stack.extend(k for k in adj[node] if k not in seen)
        comp_set = set(comp)
        types, reasons = [], []
        for key, (t, r) in edge_info.items():
            a, b = tuple(key)
            if a in comp_set and b in comp_set:
                types.append(t)
                reasons.append(r)
        clusters.append({
            'types': sorted(set(types)),
            'reasons': reasons,
            'entries': [game_entries[k] for k in sorted(comp)],
        })
    return clusters


def evaluate_correlation_gate(real_entries):
    """
    Pure decision function. `real_entries` = list of (game_label, entry)
    for every currently-real-money-tier Accepted entry across the WHOLE
    slate — the SAME collection build_risk_portfolio() is given. Never
    mutates any entry; only decides. Every wager considered here has
    ALREADY independently cleared the normal EV threshold — this
    function only decides which of several already-qualified bets stay
    real-money-eligible once shared/correlated risk is accounted for.

    Applies, in order, downgrade-only rules (never a hard reject, never
    reallocates a downgraded entry's stake elsewhere):

      1. One-primary-expression: when a SAME_SIDE_THESIS pair (full-game
         ML + F5 ML, same team) are BOTH still real-money in the same
         game, keep the higher-edge one, downgrade the other.
      2. Per-game concentration cap: keep the top GAME_MAX_REAL_MONEY_BETS
         by edge in each game, downgrade the rest.
      3. Same-game correlated-cluster stake cap: any correlated cluster
         (connected component of CORRELATION_RULES edges, computed AFTER
         steps 1-2) whose combined stake exceeds
         GAME_CLUSTER_MAX_STAKE_PCT of the slate's proposed daily
         allocation (total stake at the START of this function, never
         recomputed mid-pass — the same "denominator fixed before any of
         this gate's own downgrades" convention build_risk_portfolio's
         TT_DOMINANCE check already uses) is trimmed, lowest edge first,
         always keeping at least the highest-edge member — a target/
         warning cap, not a hard zero-correlated-exposure rule.

    Returns (decisions, report):
      decisions — one dict per real_entries item, SAME order:
        {'game', 'entry', 'correlationGroups': [...], 'downgrade': bool,
         'downgradeReason': str or None}
        correlationGroups reflects the FULL, pre-downgrade relationship
        set for every entry (even a downgraded one), so the reason a
        card was flagged is always visible (requirement: expose the
        correlation reason/group clearly).
      report — {'warnings': [...], 'downgrades': [...], 'clusters': [...],
                'total_stake_basis': float}
    """
    total_stake = sum(_entry_stake(e) for _, e in real_entries)

    by_game = {}
    order_index = {}
    for idx, (game, entry) in enumerate(real_entries):
        by_game.setdefault(game, []).append((game, entry))
        order_index[id(entry)] = idx

    downgraded = {}   # id(entry) -> reason
    warnings = []
    downgrades_applied = []

    def _active(pairs):
        return [(g, e) for g, e in pairs if id(e) not in downgraded]

    # ── Step 1: one-primary-expression (SAME_SIDE_THESIS dedup) ─────────────
    for game, entries in by_game.items():
        for side_market, f5_market in (('ML_Away', 'F5_ML_Away'), ('ML_Home', 'F5_ML_Home')):
            side_entry = next((e for _, e in entries if e.get('market') == side_market), None)
            f5_entry   = next((e for _, e in entries if e.get('market') == f5_market), None)
            if side_entry is None or f5_entry is None:
                continue
            if id(side_entry) in downgraded or id(f5_entry) in downgraded:
                continue
            rule_type, rule_reason = CORRELATION_RULES[frozenset({side_market, f5_market})]
            if _entry_edge(side_entry) >= _entry_edge(f5_entry):
                keep, drop = side_entry, f5_entry
            else:
                keep, drop = f5_entry, side_entry
            reason = (f"CORRELATION_DUPLICATE_THESIS: {drop.get('market')} drops in favor of "
                      f"{keep.get('market')} (same-side thesis, higher edge kept) -- {rule_reason}")
            downgraded[id(drop)] = reason
            warnings.append(f"{game}: {reason}")
            downgrades_applied.append({'game': game, 'market': drop.get('market'), 'reason': reason})

    # ── Step 2: per-game concentration cap (default max 2 per game) ─────────
    for game, entries in by_game.items():
        active = _active(entries)
        if len(active) <= GAME_MAX_REAL_MONEY_BETS:
            continue
        active_sorted = sorted(active, key=lambda ge: (-_entry_edge(ge[1]), order_index[id(ge[1])]))
        for g, e in active_sorted[GAME_MAX_REAL_MONEY_BETS:]:
            reason = (f"GAME_CONCENTRATION_CAP: {game} has {len(active)} real-money bets "
                      f"(max {GAME_MAX_REAL_MONEY_BETS} per game) -- {e.get('market')} downgraded")
            downgraded[id(e)] = reason
            warnings.append(reason)
            downgrades_applied.append({'game': game, 'market': e.get('market'), 'reason': reason})

    # ── Step 3: same-game correlated-cluster stake cap (target ~15%) ────────
    clusters_report = []
    cap = GAME_CLUSTER_MAX_STAKE_PCT * total_stake if total_stake > 0 else 0
    for game, entries in by_game.items():
        active = _active(entries)
        for cluster in build_same_game_clusters(active):
            cluster_stake = sum(_entry_stake(e) for _, e in cluster['entries'])
            clusters_report.append({
                'game': game, 'types': cluster['types'],
                'markets': [e.get('market') for _, e in cluster['entries']],
                'stake': cluster_stake,
            })
            if total_stake <= 0 or cluster_stake <= cap:
                continue
            members_sorted = sorted(cluster['entries'], key=lambda ge: (-_entry_edge(ge[1]), order_index[id(ge[1])]))
            running = 0.0
            for g, e in members_sorted:
                stake = _entry_stake(e)
                if running == 0.0 or running + stake <= cap:
                    running += stake
                    continue
                reason = (f"CLUSTER_STAKE_CAP: {game} {'+'.join(cluster['types'])} cluster is "
                          f"{cluster_stake:.1f}u ({(cluster_stake / total_stake):.0%} of {total_stake:.1f}u proposed "
                          f"daily allocation, target max {GAME_CLUSTER_MAX_STAKE_PCT:.0%}) -- "
                          f"{e.get('market')} downgraded")
                downgraded[id(e)] = reason
                warnings.append(reason)
                downgrades_applied.append({'game': game, 'market': e.get('market'), 'reason': reason})

    # ── correlationGroups metadata: full pre-downgrade relationships ────────
    groups_by_id = {}
    for game, entries in by_game.items():
        for cluster in build_same_game_clusters(entries):
            member_markets = {e.get('market') for _, e in cluster['entries']}
            for _, e in cluster['entries']:
                groups_by_id.setdefault(id(e), []).extend(
                    {'type': t, 'withMarkets': sorted(member_markets - {e.get('market')})}
                    for t in cluster['types']
                )

    decisions = [
        {
            'game': game,
            'entry': entry,
            'correlationGroups': groups_by_id.get(id(entry), []),
            'downgrade': id(entry) in downgraded,
            'downgradeReason': downgraded.get(id(entry)),
        }
        for game, entry in real_entries
    ]

    report = {
        'warnings': warnings,
        'downgrades': downgrades_applied,
        'clusters': clusters_report,
        'total_stake_basis': total_stake,
    }
    return decisions, report


def apply_correlation_gate(slate, now_ts=None):
    """
    Thin impure shell around evaluate_correlation_gate(): collects the
    real-money-tier candidates (the SAME filter apply_portfolio_rules
    uses — quarantined/live/final/postponed games excluded), hands them
    to the pure decision function, then applies its decisions here —
    tagging `correlationGroups` on every real-money entry (always, even
    when empty) and downgrading exactly the entries it flagged, using
    the SAME downgrade-to-PAPER shape every other gate in this file uses
    (confidence/confidenceTier='PAPER', betSize=1.0, realMoneyBlocked,
    blockReason, gatesFired) so downstream consumers (write_pending_bets.py,
    validate_slate_final.py) need no new wiring to respect it. Returns
    the report dict from evaluate_correlation_gate().
    """
    real_entries = []
    for g in slate.get('games', []):
        if g.get('excludedFromSlate'):
            continue
        if check_game_status(g, current_utc=now_ts).get('shouldSkip'):
            continue
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"
        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue
            real_entries.append((game, entry))

    decisions, report = evaluate_correlation_gate(real_entries)

    for decision in decisions:
        entry = decision['entry']
        entry['correlationGroups'] = decision['correlationGroups']
        if not decision['downgrade']:
            continue
        entry['confidence']       = 'PAPER'
        entry['confidenceTier']   = 'PAPER'
        entry['betSize']          = 1.0
        entry['realMoneyBlocked'] = True
        entry['blockReason']      = decision['downgradeReason']
        entry.setdefault('gatesFired', []).append(decision['downgradeReason'])

    return report


def build_risk_portfolio(real_entries):
    """
    Pure decision function (Phase 7 Part 6). `real_entries` is a list of
    (game_label, entry) tuples for every currently-real-money-tier
    Accepted entry — the SAME collection apply_portfolio_rules has always
    built before doing anything else. Never mutates any entry it is
    given, never reads the clock, never touches a file, never prints.

    Returns (report, to_downgrade, decision):
      report        — the risk_gate_report dict, in the exact key order
                       the original single-function implementation built
                       it in (so meta.json's serialized bytes are
                       unaffected by this refactor).
      to_downgrade  — list of (game_label, entry) tuples the caller must
                       force to PAPER (TT_MAX_BETS_EXCEEDED only — the
                       later PAPER_ONLY-driven full-portfolio downgrade
                       is a separate step, still performed by main()).
      decision      — 'GO' or 'PAPER_ONLY' (see apply_portfolio_rules'
                       docstring for why 'NO_GO' is never produced).
    """
    report = {
        'by_family': {},
        'total_real_stake': 0.0,
        'total_bets': 0,
        'tt_stake': 0.0,
        'tt_bets': 0,
        'ml_f5_stake': 0.0,
        'ml_f5_bets': 0,
        'concentration_warnings': [],
        'downgrades_applied': [],
    }

    # Tally by family
    fam_map = {}
    for game, entry in real_entries:
        mkt   = entry.get('market', '')
        stake = float(entry.get('betSize') or 0)
        if mkt in TT_MARKETS:    fam = 'TT'
        elif mkt in ML_F5_MARKETS: fam = 'ML_F5'
        else:                      fam = 'OTHER'

        if fam not in fam_map:
            fam_map[fam] = {'bets': 0, 'stake': 0.0, 'entries': []}
        fam_map[fam]['bets'] += 1
        fam_map[fam]['stake'] += stake
        fam_map[fam]['entries'].append((game, entry))

    total_stake = sum(v['stake'] for v in fam_map.values())
    total_bets  = sum(v['bets']  for v in fam_map.values())

    tt_stake   = fam_map.get('TT', {}).get('stake', 0.0)
    tt_bets    = fam_map.get('TT', {}).get('bets', 0)
    mlf5_stake = fam_map.get('ML_F5', {}).get('stake', 0.0)
    mlf5_bets  = fam_map.get('ML_F5', {}).get('bets', 0)

    report.update({
        'by_family':        {k: {'bets': v['bets'], 'stake': v['stake']} for k, v in fam_map.items()},
        'total_real_stake': total_stake,
        'total_bets':       total_bets,
        'tt_stake':         tt_stake,
        'tt_bets':          tt_bets,
        'ml_f5_stake':      mlf5_stake,
        'ml_f5_bets':       mlf5_bets,
    })

    warnings = []
    to_downgrade = []

    # ── Rule: daily risk cap ───────────────────────────────────────────────
    if total_stake > DAILY_RISK_CAP:
        warnings.append(f"DAILY_RISK_CAP exceeded: {total_stake:.1f}u > {DAILY_RISK_CAP}u")

    # ── Rule: TT max bets ──────────────────────────────────────────────────
    tt_entries = fam_map.get('TT', {}).get('entries', [])
    if tt_bets > TT_MAX_BETS:
        # Keep top N by edge, downgrade the rest — decided here, applied
        # by the caller (this function never mutates `entry`).
        tt_entries_sorted = sorted(
            tt_entries,
            key=lambda x: _entry_edge(x[1]),
            reverse=True
        )
        kept = tt_entries_sorted[:TT_MAX_BETS]
        to_downgrade = tt_entries_sorted[TT_MAX_BETS:]
        warnings.append(f"TT_CONCENTRATION: {tt_bets} TT bets (max {TT_MAX_BETS}) → downgraded {len(to_downgrade)}")
    else:
        kept = tt_entries

    downgrades = [f"{game} {entry.get('market')} → PAPER (TT cap)" for game, entry in to_downgrade]

    # tt_stake_post: stake of the TT entries NOT downgraded above. `kept`
    # entries are never touched by this function, so their tier is still
    # whatever real_entries collected them with (always real-money-tier at
    # this point) — summing their stake here is equivalent to summing
    # AFTER the caller applies the downgrade, without this function ever
    # mutating anything itself.
    tt_stake_post = sum(
        float(e.get('betSize') or 0)
        for _, e in kept
        if (e.get('confidenceTier') or e.get('confidence') or '').upper() in REAL_MONEY_TIERS
    )

    # ── Rule: TT stake cap ─────────────────────────────────────────────────
    if tt_stake_post > TT_MAX_STAKE:
        warnings.append(f"TT_STAKE_CAP: TT stake {tt_stake_post:.1f}u > {TT_MAX_STAKE}u")

    # ── Rule: TT % of total ────────────────────────────────────────────────
    # NOTE: total_stake is deliberately NOT recomputed post-downgrade — a
    # downgraded TT entry's stake still counts in this denominator even
    # though it no longer counts in tt_stake_post's numerator. This is a
    # precise, load-bearing legacy asymmetry preserved exactly, not fixed.
    tt_pct = tt_stake_post / total_stake if total_stake > 0 else 0
    if tt_pct > TT_MAX_STAKE_PCT:
        warnings.append(f"TT_DOMINANCE: TT is {tt_pct:.0%} of stake (max {TT_MAX_STAKE_PCT:.0%})")

    # ── Rule: ML/F5 must be ≥50% if ≥2 qualify ────────────────────────────
    mlf5_pct = mlf5_stake / total_stake if total_stake > 0 else 0
    if mlf5_bets >= 2 and mlf5_pct < ML_F5_MIN_STAKE_PCT:
        warnings.append(
            f"ML_F5_UNDERFILL: ML+F5 only {mlf5_pct:.0%} of stake "
            f"(min {ML_F5_MIN_STAKE_PCT:.0%} when ≥2 qualify)"
        )

    report['concentration_warnings'] = warnings
    report['downgrades_applied']     = downgrades

    # ── GO / PAPER_ONLY decision ───────────────────────────────────────────
    hard_block = any(
        w.startswith('DAILY_RISK_CAP') or w.startswith('TT_DOMINANCE')
        for w in warnings
    )

    if tt_bets > 0 and mlf5_bets == 0 and total_bets == tt_bets:
        # Card is 100% TT with no ML/F5
        decision = 'PAPER_ONLY'
        report['decision_reason'] = 'ALL_TT_NO_ML_F5'
    elif hard_block:
        decision = 'PAPER_ONLY'
        report['decision_reason'] = '; '.join(w for w in warnings if 'DOMINANCE' in w or 'CAP' in w)
    else:
        decision = 'GO'
        report['decision_reason'] = 'Composition checks passed'

    return report, to_downgrade, decision


def apply_portfolio_rules(slate, now_ts=None):
    """
    Enforces concentration limits after TT safety pass.
    Returns (go_decision, report_dict).
    go_decision: 'GO', 'PAPER_ONLY', or 'NO_GO'

    Thin impure shell around build_risk_portfolio(): collects the
    real-money-tier candidates (the only I/O-adjacent, clock-dependent
    part — check_game_status(now_ts)), hands them to the pure decision
    function, then applies the ONLY mutation this stage performs
    (TT_MAX_BETS_EXCEEDED downgrades) here, not inside the decision
    function.
    """
    # Collect all Accepted real-money entries
    real_entries = []   # (game, entry_ref)
    for g in slate.get('games', []):
        # Skip quarantined games
        if g.get('excludedFromSlate'):
            continue
        # Skip live/final/postponed games — same gate write_pending_bets.py
        # applies. Without this, portfolio composition (and the GO/PAPER_ONLY
        # decision) counts stake that will never actually be logged.
        if check_game_status(g, current_utc=now_ts).get('shouldSkip'):
            continue
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"
        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue
            real_entries.append((game, entry))

    report, to_downgrade, decision = build_risk_portfolio(real_entries)

    for game, entry in to_downgrade:
        entry['confidence']      = 'PAPER'
        entry['confidenceTier']  = 'PAPER'
        entry['betSize']         = 1.0
        entry['realMoneyBlocked'] = True
        entry['blockReason']     = f'TT_MAX_BETS_EXCEEDED: capped at {TT_MAX_BETS}'

    return decision, report


def build_execution_artifact_payload(slate, decision, decision_reason):
    """
    Pure decision function (Phase 7 Part 10-11). Extracts the canonical
    execution-decision payload from an ALREADY fully-decided slate — i.e.
    called after apply_tt_safety(), apply_portfolio_rules(), and any
    PAPER_ONLY third-pass downgrade have all already run. Never
    recomputes any decision; only reads and reshapes what main() already
    decided, so the artifact and the legacy slate.json write are always
    powered by the exact same in-memory decision, never two independent
    computations that could disagree (mission: "Do not compute the
    decision twice").

    Narrow, canonical schema (per-candidate): game/candidate identity,
    market identity, final decision (real-money vs PAPER), rejection
    reason, approved stake, approved price, evaluation order, and the
    source recommendation's ticker identity. Deliberately excludes any
    settlement result, PnL, final score, or historical reconciliation
    data — none of that exists in risk_gate.py's scope, and this
    artifact must not become a place to start adding it.
    """
    candidates = []
    order = 0
    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game_label = f"{away}@{home}"
        game_excluded = bool(g.get('excludedFromSlate', False))
        for entry in g.get('marketLedger', []):
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            candidates.append({
                'game': game_label,
                'market': entry.get('market'),
                'sourceRecommendationTicker': entry.get('ticker') or entry.get('marketTicker'),
                'status': entry.get('status'),
                'tier': entry.get('confidenceTier'),
                'realMoneyEligible': entry.get('status') == 'Accepted' and tier in REAL_MONEY_TIERS,
                'rejectionReason': entry.get('blockReason'),
                'approvedStake': entry.get('betSize'),
                'approvedPrice': entry.get('executablePriceUsed'),
                'gameExcluded': game_excluded,
                'order': order,
            })
            order += 1

    return {
        'date': slate.get('date', ''),
        'decision': decision,
        'decisionReason': decision_reason,
        'rulesVersion': '1.0',
        'candidates': candidates,
    }


def main():
    if not os.path.exists(SLATE_PATH):
        print(f"ERROR: {SLATE_PATH} not found"); sys.exit(1)

    with open(SLATE_PATH) as f:
        slate = json.load(f)

    date = slate.get('date', 'unknown')
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    print(f"[risk_gate] Running for slate date: {date}")

    # ── Pass 1: TT safety ─────────────────────────────────────────────────
    tt_downgrades = apply_tt_safety(slate, now_ts=now_ts)
    print(f"  TT safety pass: {len(tt_downgrades)} bets downgraded to PAPER")
    for d in tt_downgrades:
        print(f"    {d['game']} {d['market']}: {d['reason']}")

    # ── Pass 1.5: Correlation / concentration gate ────────────────────────
    # Runs after TT safety (so already-PAPER TT bets don't count) and
    # before Portfolio composition (so its downgrades are reflected in
    # the family/stake math below) — same layering apply_tt_safety ->
    # apply_portfolio_rules already established.
    correlation_report = apply_correlation_gate(slate, now_ts=now_ts)
    print(f"  Correlation/concentration pass: {len(correlation_report['downgrades'])} bets downgraded to PAPER")
    for w in correlation_report['warnings']:
        print(f"    ⚠️  {w}")

    # ── Pass 2: Portfolio rules ───────────────────────────────────────────
    decision, report = apply_portfolio_rules(slate, now_ts=now_ts)

    print(f"\n  Portfolio composition:")
    print(f"    Total real-money stake: {report['total_real_stake']:.1f}u across {report['total_bets']} bets")
    for fam, d in report['by_family'].items():
        print(f"    {fam}: {d['bets']} bets, {d['stake']:.1f}u")
    if report['concentration_warnings']:
        for w in report['concentration_warnings']:
            print(f"    ⚠️  {w}")
    for d in report['downgrades_applied']:
        print(f"    ↓  {d}")

    print(f"\n  ══ DECISION: {decision} ══")
    print(f"  Reason: {report['decision_reason']}")

    if decision == 'PAPER_ONLY':
        # Force ALL remaining real-money bets to PAPER
        downgraded_count = 0
        for g in slate.get('games', []):
            if g.get('excludedFromSlate'):
                continue
            for entry in g.get('marketLedger', []):
                tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
                if entry.get('status') == 'Accepted' and tier in REAL_MONEY_TIERS:
                    entry['confidence']      = 'PAPER'
                    entry['confidenceTier']  = 'PAPER'
                    entry['betSize']         = 1.0
                    entry['realMoneyBlocked'] = True
                    entry['blockReason']     = f'RISK_GATE_PAPER_ONLY: {report["decision_reason"]}'
                    downgraded_count += 1
        print(f"  Downgraded {downgraded_count} bets to PAPER (portfolio rule)")

    # ── Write back slate with modifications ───────────────────────────────
    # Phase 7 Part 18: migrated from a plain open()+json.dump() (which can
    # leave a truncated file at SLATE_PATH if the process is interrupted
    # mid-write) to the shared atomic helper already used by
    # fetch_lineups.py/fetch_savant_pitchers.py/post_fetch_gate.py.
    # indent=2 preserves the exact pre-existing pretty-printed format —
    # write_json_atomic() defaults to compact (indent=None) for its other
    # callers, so this is passed explicitly to keep slate.json's byte
    # format identical to before this migration.
    write_json_atomic(slate, SLATE_PATH, indent=2)
    print(f"\n  Slate updated in-place: {SLATE_PATH}")

    # ── Append risk_gate_report to meta.json ──────────────────────────────
    meta = {}
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH) as f:
                meta = json.load(f)
        except Exception:
            pass

    meta['risk_gate'] = {
        'runAt':       now_ts,
        'decision':    decision,
        'correlation': correlation_report,
        **report,
    }
    write_json_atomic(meta, META_PATH, indent=2)
    print(f"  risk_gate_report written to meta.json")

    # ── Phase 7 immutable pipeline: Execution Layer artifact ───────────────
    # Best-effort, additive, published from the exact same in-memory
    # decision already written to slate.json/meta.json above — never a
    # second computation. Wrapped so any failure (disk full, permission
    # denied, anything) can only produce a warning, never change the
    # decision already made, never touch slate.json/meta.json again, and
    # never affect this function's return value.
    try:
        from pipeline_artifacts import write_stage_artifact
        payload = build_execution_artifact_payload(slate, decision, report['decision_reason'])
        write_stage_artifact(
            'execution', date, payload,
            produced_by='scripts/risk_gate.py',
            status='canonical',
            source_stage='recommendations',
        )
        print(f"  execution pipeline artifact written for {date}")
    except Exception as e:
        print(f"WARNING: could not write execution pipeline artifact: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
