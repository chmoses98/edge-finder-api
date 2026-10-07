#!/usr/bin/env python3
"""
lib/research/hitter_prop_projection_loader.py
=============================================
Pure, read-only loader of the prospective hitter projection snapshots
(data/edgelab/hitter_projection_snapshots/<date>.jsonl[.gz], written by
scripts/edgelab/run_hitter_prospective_snapshots.py) for the app export.

No network, no writes, no model code. Nothing here affects pricing,
recommendations, staking or any betting methodology.

SELECTION RULE (per Kalshi marketTicker)
----------------------------------------
Among a ticker's rows whose availability time is <= `now_iso`, the NEWEST
row is the engine's current word on that contract. Availability time is
`snapshotGeneratedAt` (computation completion), falling back to
`createdAt`, then `projectionGeneratedAt`; a row with none of them is
ignored (it cannot be placed in time). Ties break on the later checkpoint,
then the snapshot id, so the result is deterministic.

  * newest row PROJECTED  -> returned (isProjected=True).
  * newest row NOT projected (PLAYER_NOT_IN_STARTING_LINEUP,
    LINEUP_UNCONFIRMED, MODEL_ERROR, ...) -> returned with its
    `projectionStatus` passed through and `modelProbability` None when
    `include_non_projected` (default) -- and NEVER replaced by an older,
    superseded PROJECTED row (a scratched player must not keep showing a
    stale probability). With include_non_projected=False the ticker is
    simply omitted.

Rows dated after `now_iso` are never read, so a replay at an earlier
`now` cannot see later projections.

EXPECTED-STAT SUMMARY
---------------------
Snapshot rows do NOT store the simulated distribution mean (expected
hits / total bases / H+R+RBI / RBIs); they store only P(stat >= N) for
the thresholds Kalshi lists. `hitter_expected_stat_summaries` therefore:
  * uses a row's `distributionMean` field when present (the proposed
    minimal additive field -- see below), else
  * reports `ladderImpliedExpectedLowerBound` = sum of P(X >= n) over the
    contiguous rungs n = 1..K that are present (E[X] = sum_{n>=1} P(X>=n),
    truncated at K, so it is a LOWER bound; exact when P(X >= K+1) ~ 0),
    with `ladderContiguousFromOne` telling whether rungs 1..K are all present.

PROPOSED MINIMAL ADDITIVE FIELD (not implemented here): in
lib/research/hitter_board_builder.build_hitter_projection_rows each
PROJECTED row already has `distributions[dist_key]["mean"]` in scope
(lib/research/hitter_market_distributions._distribution_block computes it);
stamping it as `"distributionMean": distributions[dist_key]["mean"]`
would make the expected stat exact for every family with no schema break.
"""
import os

from lib.edgelab import storage

SNAPSHOT_ENTITY = "hitter_projection_snapshots"
STATUS_PROJECTED = "PROJECTED"
CHECKPOINT_ORDER = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", "HITTER_CLOSING_WINDOW")

# Exact field names of every returned projection row.
PROJECTION_FIELDS = (
    "marketTicker", "marketFamily", "threshold", "naturalLanguageMarket",
    "playerId", "player", "gameId", "matchup",
    "projectionStatus", "projectionStatusReason", "isProjected", "pricingStatus",
    "modelProbability", "monteCarloStderr", "fairAmericanOdds",
    "executableKalshiPrice", "rawProbabilityEdge",
    "checkpoint", "availableAt", "snapshotGeneratedAt", "projectionGeneratedAt", "marketObservedAt",
    "sourceCapturePath", "hitterProjectionSnapshotId", "researchRunId", "engineCommitSha",
    "distributionMean",
)

FAMILY_STAT_LABEL = {
    "hitter_hits": "hits",
    "hitter_total_bases": "totalBases",
    "hitter_hits_runs_rbis": "hitsRunsRbis",
    "hitter_rbis": "rbis",
}


def _iso_key(value):
    """Normalise an ISO-8601 UTC string for ordering: 'YYYY-MM-DDTHH:MM:SS' + fractional, 'Z'/'+00:00' stripped.
    Snapshot timestamps are all UTC; comparing normalised strings avoids tz parsing on hot paths."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1]
    elif v.endswith("+00:00"):
        v = v[:-6]
    if len(v) < 19:
        return None
    if "." in v[19:]:
        head, frac = v[:19], v[20:]
        return head + "." + (frac + "000000")[:6]
    return v[:19] + ".000000"


def row_available_at(row):
    """The instant a snapshot row became available, or None."""
    for f in ("snapshotGeneratedAt", "createdAt", "projectionGeneratedAt"):
        if row.get(f):
            return row[f]
    return None


def _sort_key(row):
    cp = row.get("checkpoint")
    return (_iso_key(row_available_at(row)), CHECKPOINT_ORDER.index(cp) if cp in CHECKPOINT_ORDER else -1,
            str(row.get("hitterProjectionSnapshotId") or ""))


def _normalise(row):
    out = {f: row.get(f) for f in PROJECTION_FIELDS}
    out["availableAt"] = row_available_at(row)
    projected = row.get("projectionStatus") == STATUS_PROJECTED and row.get("modelProbability") is not None
    out["isProjected"] = projected
    if not projected:
        out["modelProbability"] = None
        out["rawProbabilityEdge"] = None
    return out


def select_latest_rows(rows, now_iso, include_non_projected=True):
    """Pure core of the loader over already-read rows. See module docstring."""
    now_key = _iso_key(now_iso)
    if now_key is None:
        raise ValueError(f"now_iso is not an ISO-8601 timestamp: {now_iso!r}")
    latest = {}
    for r in rows:
        ticker = r.get("marketTicker")
        at = _iso_key(row_available_at(r))
        if not ticker or at is None or at > now_key:
            continue
        cur = latest.get(ticker)
        if cur is None or _sort_key(r) > _sort_key(cur):
            latest[ticker] = r
    out = {}
    for ticker, r in latest.items():
        norm = _normalise(r)
        if norm["isProjected"] or include_non_projected:
            out[ticker] = norm
    return out


def _partition_rows(data_root, date):
    plain = os.path.join(data_root, "edgelab", SNAPSHOT_ENTITY, f"{date}.jsonl")
    path = plain if os.path.exists(plain) else plain + ".gz"
    if not os.path.exists(path):
        return []
    return list(storage.read_records(path))


def latest_pregame_hitter_projections(data_root, date, now_iso, include_non_projected=True):
    """
    {marketTicker: projection row} for slate `date` as of `now_iso`.

    `data_root` is the repository's `data` directory (same convention as
    scripts/app_export.py --data-root). Reads only
    <data_root>/edgelab/hitter_projection_snapshots/<date>.jsonl[.gz];
    returns {} when that partition does not exist. Each value carries
    exactly PROJECTION_FIELDS.
    """
    return select_latest_rows(_partition_rows(data_root, date), now_iso, include_non_projected=include_non_projected)


def hitter_expected_stat_summaries(projections):
    """
    Per player-game, per family expected-stat summary from the output of
    latest_pregame_hitter_projections. Returns
    {"<playerId>:<gameId>": {"playerId", "player", "gameId", "matchup",
      "families": {family: {"stat", "expectedStat", "expectedStatSource",
        "ladderImpliedExpectedLowerBound", "ladderContiguousFromOne",
        "thresholds": {N: P(stat >= N)}, "nonProjectedStatuses": [...]}}}}.

    expectedStat is the row's distributionMean when the snapshot carries it
    (expectedStatSource="DISTRIBUTION_MEAN"); otherwise it is None and only
    the ladder lower bound is given (expectedStatSource="NOT_STORED").
    """
    out = {}
    for row in projections.values():
        fam = row.get("marketFamily")
        if fam not in FAMILY_STAT_LABEL:
            continue
        key = f"{row.get('playerId')}:{row.get('gameId')}"
        pg = out.setdefault(key, {"playerId": row.get("playerId"), "player": row.get("player"),
                                  "gameId": row.get("gameId"), "matchup": row.get("matchup"), "families": {}})
        fb = pg["families"].setdefault(fam, {"stat": FAMILY_STAT_LABEL[fam], "thresholds": {}, "means": [],
                                             "nonProjectedStatuses": []})
        if row.get("isProjected"):
            thr = row.get("threshold")
            if isinstance(thr, int):
                fb["thresholds"][thr] = row["modelProbability"]
            if row.get("distributionMean") is not None:
                fb["means"].append(row["distributionMean"])
        else:
            st = row.get("projectionStatus")
            if st not in fb["nonProjectedStatuses"]:
                fb["nonProjectedStatuses"].append(st)
    for pg in out.values():
        for fb in pg["families"].values():
            thr = dict(sorted(fb["thresholds"].items()))
            fb["thresholds"] = thr
            k = 0
            while (k + 1) in thr:
                k += 1
            fb["ladderContiguousFromOne"] = k > 0 and k == len(thr)
            fb["ladderImpliedExpectedLowerBound"] = round(sum(thr[n] for n in range(1, k + 1)), 4) if k else None
            means = fb.pop("means")
            if means:
                fb["expectedStat"] = means[0]
                fb["expectedStatSource"] = "DISTRIBUTION_MEAN"
            else:
                fb["expectedStat"] = None
                fb["expectedStatSource"] = "NOT_STORED"
    return out
