#!/usr/bin/env python3
"""
lib/edgelab/thesis_classification.py
========================================
Same-underlying-thesis correlation classification.

THE GAP THIS MODULE CLOSES
-----------------------------
scripts/risk_gate.py's evaluate_correlation_gate() already implements a
real, tested, downgrade-only same-game correlation gate -- one-primary-
expression dedup, a per-game concentration cap, and a correlated-cluster
stake cap (see that module's own docstring). It is NOT replaced or
duplicated here. But it is deliberately scoped to the 8 markets that can
ever reach marketLedger's real-money tier (CORRELATION_RULES is a
pairwise table over exactly those markets), and its correlation "types"
(SAME_SIDE_THESIS/SIDE_TEAM_TOTAL/PITCHER_DEPENDENT/SAME_MARKET_BOTH_SIDES)
are internal codes, not the human-readable "what causal driver do these
two bets actually share" explanation postmortems have repeatedly asked
for (STL F5 side + STL team total; Miami ML + Miami team total; Seattle
F5 protected side + Seattle ML + Hunter Brown outs under; pitcher Ks +
pitcher outs; multiple alternate K-ladder thresholds on the same
pitcher -- none of the pitcher-prop examples are wired into
marketLedger at all today, since pitcher_strikeouts/pitcher_outs are not
in REQUIRED_MARKETS).

This module adds:
  1. A thesis-tag classification (reusing lib.edgelab.tags's controlled
     vocabulary, extended additively for the handful of genuinely new
     concepts this analysis needs -- see data/edgelab/schema_v1/tags.json)
     for any entry, whether or not it currently reaches marketLedger.
  2. A three-tier severity classification -- INDEPENDENT_THESIS /
     MODERATELY_CORRELATED / DUPLICATE_THESIS -- distinguishing
     "genuinely independent same-game bets" from "share a causal driver
     but pay off on different conditions" from "literally the same bet
     twice" (e.g. two alternate strikeout-ladder thresholds on the same
     pitcher, or NRFI+YRFI).
  3. aggregate_thesis_exposure(): groups a list of entries by
     (severity-DUPLICATE cluster) and reports combined stake -- the
     "calculate aggregate same-thesis exposure" building block a future
     production gate (or a research/manual-review script) can consume,
     without this module itself downgrading or resizing anything.

WHAT THIS MODULE DOES NOT DO
-------------------------------
It does not gate, downgrade, or resize any real-money entry itself --
scripts/risk_gate.py remains the sole place production staking decisions
are made (see MLB Model Expression Guardrails milestone's own scope:
"Do NOT change bankroll sizing/staking mechanics unless required
specifically for correlation exposure enforcement"). It is additive
research/classification tooling, consumed by scripts/risk_gate.py only
to enrich the ALREADY-emitted correlationGroups/clusters provenance on
the 8-market universe it already governs (see risk_gate.py's own
wiring), never to change which entries get downgraded there.

SCOPE / SAFETY
---------------
Every function here is pure: no I/O, no network, no mutation of any
argument, deterministic given deterministic inputs.
"""

from lib.edgelab.tags import THESIS_TAGS

DUPLICATE_THESIS = "DUPLICATE_THESIS"
MODERATELY_CORRELATED = "MODERATELY_CORRELATED"
INDEPENDENT_THESIS = "INDEPENDENT_THESIS"

# market name (production ledger names, e.g. 'ML_Away') or market-family
# string (research-only names not yet in marketLedger, e.g.
# 'pitcher_strikeouts') -> the thesis tag(s) representing its primary
# underlying driver(s). Every value here is a real, existing entry in
# lib.edgelab.tags.THESIS_TAGS -- validated at import time below.
_MARKET_THESIS_TAGS = {
    "ML_Away": frozenset({"FULL_GAME_SIDE"}),
    "ML_Home": frozenset({"FULL_GAME_SIDE"}),
    "F5_ML_Away": frozenset({"FIRST_FIVE_SIDE", "STARTER_EDGE"}),
    "F5_ML_Home": frozenset({"FIRST_FIVE_SIDE", "STARTER_EDGE"}),
    # Systematic Best-Expression Comparison mission (F3/F5/F7 canonical
    # relation follow-up): F3/F7 winner and the run-line/winning-margin
    # side were previously absent from this table entirely, so
    # classify_pair_severity() silently fell through to INDEPENDENT_THESIS
    # for e.g. an F3 YES paired with the SAME team's ML/F5 -- an obviously
    # wrong classification for markets that are all just alternate
    # expressions of "does this team win (early)". F3_ML_*/F7_ML_* use the
    # market-family-string names this repository's live expression-
    # comparison layer emits (F3/F7 are research-only per
    # lib.research.market_taxonomy.HORIZON_MARKET_STATUS's
    # productionEnabled=False, never actual marketLedger rows -- see that
    # module for why they still carry a real, evidence-confirmed
    # CONFIRMED_THREE_WAY structure despite not being production-enabled).
    "F3_ML_Away": frozenset({"FIRST_THREE_SIDE", "STARTER_EDGE"}),
    "F3_ML_Home": frozenset({"FIRST_THREE_SIDE", "STARTER_EDGE"}),
    "F7_ML_Away": frozenset({"FIRST_SEVEN_SIDE", "STARTER_EDGE"}),
    "F7_ML_Home": frozenset({"FIRST_SEVEN_SIDE", "STARTER_EDGE"}),
    "RL_Away": frozenset({"WINNING_MARGIN_SIDE"}),
    "RL_Home": frozenset({"WINNING_MARGIN_SIDE"}),
    "TT_Away_Over": frozenset({"OFFENSE_UPSIDE"}),
    "TT_Home_Over": frozenset({"OFFENSE_UPSIDE"}),
    "TT_Away_Under": frozenset({"LOW_SCORING_ENVIRONMENT"}),
    "TT_Home_Under": frozenset({"LOW_SCORING_ENVIRONMENT"}),
    "NRFI": frozenset({"LOW_SCORING_ENVIRONMENT", "STARTER_EDGE"}),
    "YRFI": frozenset({"HIGH_SCORING_ENVIRONMENT"}),
    "pitcher_strikeouts": frozenset({"PITCHER_DOMINANCE"}),
    "pitcher_outs": frozenset({"PITCHER_WORKLOAD_UNDER"}),
    "Game_Total_Under": frozenset({"LOW_SCORING_ENVIRONMENT"}),
    "Game_Total_Over": frozenset({"HIGH_SCORING_ENVIRONMENT"}),
}

_unknown_tags = {t for tags in _MARKET_THESIS_TAGS.values() for t in tags} - THESIS_TAGS
if _unknown_tags:
    raise ValueError(
        f"_MARKET_THESIS_TAGS references tag(s) not in the controlled vocabulary: "
        f"{sorted(_unknown_tags)}. Add to data/edgelab/schema_v1/tags.json first."
    )

# Market families that are literally complementary sides of ONE binary
# event -- always DUPLICATE_THESIS on the same game/subject regardless of
# the generic same-identity-different-family rule below (NRFI/YRFI are
# not "two bets that share a driver", they are the same coin viewed from
# both faces).
_COMPLEMENTARY_MARKET_PAIRS = frozenset({frozenset({"NRFI", "YRFI"})})

# Full-game ML, F3/F5/F7 winner, and the run-line/winning-margin side for
# the SAME team are all alternate expressions of "does this team win
# (early)" -- F5 (and now F3/F7) are leading-indicator horizons of the
# same "this team wins" outcome, and a run-line side is the same win
# direction at a stricter margin threshold. scripts/risk_gate.py's
# CORRELATION_RULES already encodes the ML+F5 case as SAME_SIDE_THESIS.
# FULL_GAME_SIDE, FIRST_FIVE_SIDE, FIRST_THREE_SIDE, FIRST_SEVEN_SIDE, and
# WINNING_MARGIN_SIDE deliberately do NOT share a thesis tag with each
# other (they describe genuinely different horizons/thresholds), so this
# family needs its own identity-based rule rather than falling out of the
# generic shared-tag check below.
_WIN_THESIS_FAMILIES = frozenset({
    "ML_Away", "ML_Home",
    "F3_ML_Away", "F3_ML_Home",
    "F5_ML_Away", "F5_ML_Home",
    "F7_ML_Away", "F7_ML_Home",
    "RL_Away", "RL_Home",
})

# Strikeouts and outs on the SAME pitcher are correlated (a dominant,
# healthy start racks up both; an early hook or a rocky outing hurts
# both) even though they carry different thesis tags (PITCHER_DOMINANCE
# vs. PITCHER_WORKLOAD_UNDER describe different observable effects of
# the same underlying pitcher-quality/health driver) -- same identity
# principle as _WIN_THESIS_FAMILIES above, but MODERATE rather than
# DUPLICATE since they are genuinely different payoff conditions, not
# two expressions of the identical outcome.
_PITCHER_PROP_FAMILIES = frozenset({"pitcher_strikeouts", "pitcher_outs"})


def thesis_tags_for_market(market_name):
    """Pure. Returns the frozenset of thesis tags for a market/family name, or an empty frozenset if unknown (never fabricated)."""
    return _MARKET_THESIS_TAGS.get(market_name, frozenset())


def _ladder_family(market_name):
    """
    Strips a trailing alternate-threshold suffix (e.g.
    'pitcher_strikeouts_6plus' -> 'pitcher_strikeouts') so alternate
    rungs of the same ladder are recognized as the same underlying
    market family. Production ledger market names (ML_Away, F5_ML_Home,
    etc.) never carry this suffix shape and pass through unchanged.
    """
    if market_name is None:
        return None
    for family in ("pitcher_strikeouts", "pitcher_outs"):
        if market_name == family or market_name.startswith(family + "_"):
            return family
    return market_name


def underlying_identity(entry):
    """
    Pure. Returns a (kind, value) tuple identifying the concrete
    real-world driver an entry's thesis rests on, so two entries that
    happen to share a thesis TAG but point at unrelated underlying
    drivers (e.g. NRFI in one game vs. an unrelated pitcher's outs prop
    in a different game) are never conflated as correlated. Never
    fabricates an identity when the entry doesn't carry one -- returns
    ('unknown', None) rather than guessing.

    `entry` is a loosely-typed dict: accepts production marketLedger
    field names (market, gameId, awayAbbr/homeAbbr) as well as the
    research-only field names a pitcher-prop-shaped entry would carry
    (market or marketFamily, pitcherName or subjectId).
    """
    market = entry.get("market") or entry.get("marketFamily") or ""
    family = _ladder_family(market)
    if family in ("ML_Away", "F3_ML_Away", "F5_ML_Away", "F7_ML_Away", "RL_Away",
                  "TT_Away_Over", "TT_Away_Under"):
        return ("team", entry.get("awayAbbr") or entry.get("team"))
    if family in ("ML_Home", "F3_ML_Home", "F5_ML_Home", "F7_ML_Home", "RL_Home",
                  "TT_Home_Over", "TT_Home_Under"):
        return ("team", entry.get("homeAbbr") or entry.get("team"))
    if family in ("NRFI", "YRFI", "Game_Total_Under", "Game_Total_Over"):
        return ("game", entry.get("gameId"))
    if family in ("pitcher_strikeouts", "pitcher_outs"):
        return ("pitcher", entry.get("pitcherName") or entry.get("subjectId"))
    return ("unknown", None)


def _cross_identity_link(entry_a, entry_b):
    """
    Pure. An entry may explicitly declare, via `opposingTeamAbbr`, which
    OTHER team's own thesis its bet is a leading indicator for -- e.g. a
    pitcher-outs-under prop on a Houston starter naming
    `opposingTeamAbbr='SEA'` (the team actually batting against that
    pitcher), so it can be recognized as correlated with Seattle's own
    F5/ML win thesis (their offense beating that starter is the shared
    driver behind both bets winning or losing together). Also covers a
    team-side market naming the opposing team it depends on suppressing
    (e.g. a team total UNDER naming the opponent it's betting will be
    held down).

    NEVER inferred from team abbreviations alone -- only present when the
    caller explicitly supplies it, exactly like
    lib.research.pitcher_workload_projection's `recent_workload_restricted`
    convention (real evidence only, never guessed from proximity).
    """
    id_a = underlying_identity(entry_a)
    id_b = underlying_identity(entry_b)
    link_a = entry_a.get("opposingTeamAbbr")
    link_b = entry_b.get("opposingTeamAbbr")
    if link_a and id_b == ("team", link_a):
        return True
    if link_b and id_a == ("team", link_b):
        return True
    return False


def classify_pair_severity(entry_a, entry_b):
    """
    Pure. Classifies the correlation severity between two entries.

    Returns (severity, shared_tags) where severity is one of
    DUPLICATE_THESIS / MODERATELY_CORRELATED / INDEPENDENT_THESIS, and
    shared_tags is the frozenset of thesis tags/labels driving that
    classification (empty for INDEPENDENT_THESIS).

    Rules, in order (first match wins):
      1. Different games (or either gameId missing) -> INDEPENDENT_THESIS.
         A shared thesis TAG across two unrelated games is not a
         correlation.
      2. Complementary market pair (NRFI/YRFI) -> DUPLICATE_THESIS,
         regardless of identity granularity -- they are the same coin
         viewed from both faces, not two bets that merely share a driver.
      3. Same underlying identity (team or pitcher), different market
         family, both in the win-thesis family set (ML+F5, same team)
         -> DUPLICATE_THESIS (F5 is a leading indicator of the identical
         "this team wins" outcome).
      4. Same underlying identity, same ladder family (e.g. two alternate
         K-ladder thresholds on the same pitcher) -> DUPLICATE_THESIS --
         a repeated alternate line, not two independent findings.
      5. Same underlying identity, different family, shared thesis tag
         -> MODERATELY_CORRELATED (e.g. pitcher Ks + pitcher outs on the
         same pitcher: same driver, different payoff condition).
      6. An explicit cross-identity link (see _cross_identity_link)
         -> MODERATELY_CORRELATED (e.g. an opposing starter's outs-under
         prop and the batting team's own F5/ML win thesis).
      7. Different identity, shared thesis tag -> MODERATELY_CORRELATED
         (a coarser-grained shared driver, e.g. NRFI (game-level) and an
         F5 pitcher-dependent thesis).
      8. Otherwise -> INDEPENDENT_THESIS (genuinely different thesis,
         same game -- the common, expected case for two markets that
         don't share any of the above).
    """
    market_a = entry_a.get("market") or entry_a.get("marketFamily")
    market_b = entry_b.get("market") or entry_b.get("marketFamily")
    fam_a = _ladder_family(market_a)
    fam_b = _ladder_family(market_b)

    game_a = entry_a.get("gameId")
    game_b = entry_b.get("gameId")
    if not game_a or not game_b or game_a != game_b:
        return INDEPENDENT_THESIS, frozenset()

    if frozenset({fam_a, fam_b}) in _COMPLEMENTARY_MARKET_PAIRS:
        return DUPLICATE_THESIS, frozenset({"CORRELATED_POSITION"})

    id_a = underlying_identity(entry_a)
    id_b = underlying_identity(entry_b)
    same_identity = id_a == id_b and id_a[1] is not None

    if same_identity and fam_a != fam_b and {fam_a, fam_b} <= _WIN_THESIS_FAMILIES:
        # Union of both entries' own tags -- was previously a hardcoded
        # {"FULL_GAME_SIDE", "FIRST_FIVE_SIDE"} literal that silently
        # mis-described any pairing other than exactly ML+F5 once
        # _WIN_THESIS_FAMILIES grew to include F3/F7/RL (e.g. an F3-vs-F7
        # pair would have been tagged with unrelated ML/F5 tags). No
        # caller pins the exact tag set for the pre-existing ML+F5 case
        # (only severity) -- see tests/edgelab/test_thesis_classification.py.
        win_thesis_tags = thesis_tags_for_market(fam_a) | thesis_tags_for_market(fam_b)
        return DUPLICATE_THESIS, (win_thesis_tags or frozenset({"CORRELATED_POSITION"}))

    if same_identity and fam_a == fam_b:
        tags = thesis_tags_for_market(fam_a) or frozenset({"CORRELATED_POSITION"})
        return DUPLICATE_THESIS, tags

    if same_identity and fam_a != fam_b and {fam_a, fam_b} <= _PITCHER_PROP_FAMILIES:
        return MODERATELY_CORRELATED, frozenset({"PITCHER_DOMINANCE", "PITCHER_WORKLOAD_UNDER"})

    tags_a = thesis_tags_for_market(fam_a)
    tags_b = thesis_tags_for_market(fam_b)
    shared_tags = tags_a & tags_b

    if same_identity and shared_tags:
        return MODERATELY_CORRELATED, shared_tags

    if _cross_identity_link(entry_a, entry_b):
        return MODERATELY_CORRELATED, (shared_tags or frozenset({"CORRELATED_POSITION"}))

    if shared_tags:
        return MODERATELY_CORRELATED, shared_tags

    return INDEPENDENT_THESIS, frozenset()


def aggregate_thesis_exposure(entries, *, stake_field="betSize"):
    """
    Pure. `entries` is a list of dicts (production marketLedger rows or
    research-only pitcher-prop-shaped rows) -- never mutated.

    Groups entries into DUPLICATE_THESIS clusters (connected components
    under classify_pair_severity()=='DUPLICATE_THESIS') and returns a
    report:
        {
          'clusters': [
             {'severity': 'DUPLICATE_THESIS', 'members': [...entries...],
              'thesisTags': [...], 'aggregateStake': float},
             ...
          ],
          'moderatelyCorrelatedPairs': [
             {'a': entry, 'b': entry, 'thesisTags': [...]}, ...
          ],
          'independentCount': int,
        }

    Never downgrades, resizes, or reorders anything -- purely descriptive,
    exactly like lib.edgelab.model_evaluation.correlation_groups_for_row()'s
    own "never used to filter recommendations or size stakes" contract.
    Entries with no DUPLICATE_THESIS relationship to any other entry in
    the list are not included in any cluster (a "cluster of one" is not a
    duplication) -- same convention scripts/risk_gate.py's
    build_same_game_clusters() already uses.
    """
    n = len(entries)
    adj = [[] for _ in range(n)]
    moderate_pairs = []
    duplicate_tags_by_edge = {}

    for i in range(n):
        for j in range(i + 1, n):
            severity, shared_tags = classify_pair_severity(entries[i], entries[j])
            if severity == DUPLICATE_THESIS:
                adj[i].append(j)
                adj[j].append(i)
                duplicate_tags_by_edge[frozenset({i, j})] = shared_tags
            elif severity == MODERATELY_CORRELATED:
                moderate_pairs.append({
                    "a": entries[i], "b": entries[j],
                    "thesisTags": sorted(shared_tags),
                })

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
        comp = sorted(comp)
        comp_set = set(comp)
        cluster_tags = set()
        for key, tags in duplicate_tags_by_edge.items():
            a, b = tuple(key)
            if a in comp_set and b in comp_set:
                cluster_tags |= tags
        members = [entries[k] for k in comp]
        clusters.append({
            "severity": DUPLICATE_THESIS,
            "members": members,
            "thesisTags": sorted(cluster_tags),
            "aggregateStake": sum(float(m.get(stake_field) or 0) for m in members),
        })

    # independentCount = entries that appear in NEITHER a duplicate
    # cluster NOR any moderate pair -- genuinely uninvolved in any
    # detected correlation.
    moderate_ids = {id(p["a"]) for p in moderate_pairs} | {id(p["b"]) for p in moderate_pairs}
    involved_indices = seen | {i for i in range(n) if id(entries[i]) in moderate_ids}
    independent_count = n - len(involved_indices)

    return {
        "clusters": clusters,
        "moderatelyCorrelatedPairs": moderate_pairs,
        "independentCount": independent_count,
    }
