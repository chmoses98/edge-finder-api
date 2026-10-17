"""MLB Projection Foundation - live prospective validation driver (research only, authority research_only).

    cd research/projection_foundation
    python -m mlb.live.run --stage all --archive <checkout of the projection-data branch> [--dry-run] [--now ISO]

Stages (each idempotent; `all` runs them in order)
  capture  fetch the MLB schedule (probable pitchers + posted lineups) and write an immutable availability snapshot
  project  frozen-model projections for games starting in the next --horizon-hours, using ONLY the as-of archived
           availability; archive challenger forecasts and the incumbents' current pregame forecasts (never late ones)
  publish  projections/mlb/latest.json (+ timestamped copy when content changed) and summary/mlb/latest.json
  results  FINAL box scores -> outcomes for every archived forecast entity (void rows for non-starters)
  settle   prospective.settle (settle-once; changed finals become correction records)
  score    scorecards (daily=7d, weekly=28d, season) + segment checks; appended to $GITHUB_STEP_SUMMARY
  guard    pf_core.archive_guard on the archive (additions-only for immutable directories)

--dry-run runs everything against a temporary COPY of the archive: live fetches, real projections, archive/settle/score
all happen, nothing is written to the real archive. No games in the window (offseason / off-day) is a successful no-op with
a clear summary line.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PF = os.path.dirname(os.path.dirname(HERE))
if PF not in sys.path:
    sys.path.insert(0, PF)
warnings.filterwarnings("ignore")

from pf_core import availability as av, prospective as pr  # noqa: E402
from mlb.live import availability as live_av, forecast as FC, incumbent as INC, results as RES, score as SC, statsapi as api  # noqa: E402

SPORT = "MLB"
STAGES = ["capture", "project", "publish", "results", "settle", "score", "guard"]
FAMILY_LABEL_NOTE = ("Research only: nothing here is a betting signal and nothing changes betting authority. "
                     "Hitter rows stay SHADOW until point-in-time batting orders are proven.")


class Ctx:
    def __init__(self, a):
        self.a = a
        self.fixed_now = bool(a.now)
        self.now = api.parse_ts(a.now) if a.now else datetime.now(timezone.utc).replace(microsecond=0)
        self.now_iso = api.iso(self.now)
        self.dry_run = a.dry_run
        self.repo = os.path.abspath(a.repo)
        self.recorded = a.recorded
        self.horizon = float(a.horizon_hours)
        self.real_archive = os.path.abspath(a.archive)
        self.tmp = tempfile.mkdtemp(prefix="mlbpf_")
        if self.dry_run:
            self.archive = os.path.join(self.tmp, "archive")
            if os.path.isdir(self.real_archive) and os.listdir(self.real_archive):
                shutil.copytree(self.real_archive, self.archive, symlinks=True)
            else:
                os.makedirs(self.archive)
                subprocess.run(["git", "init", "-q", self.archive], check=True)
        else:
            self.archive = self.real_archive
            os.makedirs(self.archive, exist_ok=True)
        self.workdir = a.workdir or os.path.join(self.tmp, "work")
        os.makedirs(self.workdir, exist_ok=True)
        self._sched = {}
        self.games = None
        self.captured_iso = None
        self.url_by_date = {}
        self.summary = {"sport": SPORT, "now": self.now_iso, "dry_run": self.dry_run, "stages": {}}

    # ---- sources (network or recorded)
    def schedule(self, date):
        if date in self._sched:
            return self._sched[date]
        url = api.SCHEDULE_URL.format(date=date)
        if self.recorded:
            p = os.path.join(self.recorded, f"schedule_{date}.json")
            doc = json.load(open(p)) if os.path.exists(p) else {"dates": []}
            res = (doc, self.now_iso, url)
        else:
            doc, fetched = api.http_json(url)
            res = (doc, fetched, url)
        self._sched[date] = res
        return res

    def boxscore(self, pk):
        if self.recorded:
            p = os.path.join(self.recorded, f"boxscore_{pk}.json")
            return json.load(open(p)) if os.path.exists(p) else None
        return api.http_json(api.BOXSCORE_URL.format(pk=pk))[0]

    def load_games(self, force=False):
        if self.games is not None and not force:
            return self.games
        seen, games, fetched = set(), [], []
        for d in api.capture_dates(self.now, ahead_hours=self.horizon + 6):
            doc, f, url = self.schedule(d)
            fetched.append(f)
            self.url_by_date[d] = url
            for g in api.parse_schedule(doc):
                if g["game_pk"] not in seen:
                    seen.add(g["game_pk"])
                    games.append(g)
        self.games = games
        self.captured_iso = api.iso(self.now - timedelta(seconds=30)) if self.fixed_now else max(fetched)
        return games

    def refresh_clock(self):
        """Live runs: every stage sees the real current time (a capture made seconds ago must be 'before now')."""
        if not self.fixed_now:
            self.now = datetime.now(timezone.utc).replace(microsecond=0)
            self.now_iso = api.iso(self.now)

    def avail_rows(self):
        days = sorted({(self.now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)})
        return av.load_snapshots(self.archive, SPORT, days)


def log(stage, msg):
    print(f"[mlb-pf {stage}] {msg}", flush=True)


# --------------------------------------------------------------------------- stages
def stage_capture(ctx: Ctx):
    games = ctx.load_games()
    res = live_av.capture(games, ctx.archive, ctx.captured_iso, ctx.url_by_date)
    if res["games_pregame"] == 0:
        log("capture", f"no pregame MLB games in the window ({res['games_seen']} games seen): nothing to capture (offseason/off-day or all started)")
    else:
        log("capture", f"pregame games {res['games_pregame']}; probables {res['probables_listed']}/{res['team_sides']}; "
                       f"lineups posted {res['lineups_posted']}/{res['team_sides']}; observations {res['observations']} "
                       f"(written {res['written']}, rejected {res['rejected']}, not_starting {res['not_starting_emitted']})")
    return res


def _empty_doc(ctx, note):
    return {"schema": "sift.projections.v1", "sport": SPORT, "generated_utc": ctx.now_iso, "data_asof_utc": None,
            "model": {"id": "pf-mlb", "version": "pf-1.0.0", "authority": "research_only"},
            "slate": {"status": "NO_GAMES_IN_WINDOW", "label": note}, "families": {}, "games": [], "players": []}


def stage_project(ctx: Ctx):
    import pandas as pd
    from mlb import data as D, project as PJ
    games = ctx.load_games()
    now = ctx.now
    target = [g for g in games if api.is_preview(g) and timedelta(0) < api.parse_ts(g["start_utc"]) - now <= timedelta(hours=ctx.horizon)]
    stats = {"stage": "project", "target_games": len(target), "horizon_hours": ctx.horizon}
    if not target:
        note = "No MLB games start in the next %.0f hours (offseason / off-day): nothing to project." % ctx.horizon
        with open(os.path.join(ctx.workdir, "projection_out.json"), "w") as f:
            json.dump(_empty_doc(ctx, note), f)
        stats.update(note=note, archived={"written": 0})
        log("project", note)
        return stats
    rows = ctx.avail_rows()
    lat = av.asof(rows, ctx.now_iso)
    abbr_known = set(D.load_games()["home_abbr"].dropna()) | set(D.load_games()["away_abbr"].dropna())
    slate, who, skipped = FC.slate_from_games(target, lat, abbr_known)
    stats["skipped_games"] = skipped
    if not slate["games"]:
        raise RuntimeError(f"no projectable games: {skipped}")

    def part_fn(kind, game_id, entity_id):
        return av.resolve_participation(lat, game_id, entity_id, ctx.now_iso, requires_lineup=(kind == "hitter"))

    cutoff = now - timedelta(hours=FC.HISTORY_HOURS)
    inputs = PJ.build_inputs(slate, cutoff=cutoff, extra_root=ctx.archive)
    stats["history"] = {"archived_box_games_used": inputs["n_extra_games"], "inputs_through": inputs["asof_utc"]}
    info = {"status": "LIVE_PREGAME", "label": "Live pregame projections for games starting in the next %.0f h. Who plays comes "
            "from archived availability observations (probable pitcher: certainty per observation; hitters: posted lineup card "
            "only). Research only." % ctx.horizon, "date": ctx.now_iso[:10], "inputs_through": inputs["asof_utc"]}
    out = PJ.compute(inputs, pd.Timestamp(now), participation_fn=part_fn, emit_pmf=True, slate_info=info)
    start_by_game = {g["game_pk"]: g["start_utc"] for g in target}
    odate = {g["game_pk"]: g["official_date"] for g in target}
    both = {pk for pk, w in who.items() if w["away"]["pitcher"] and w["home"]["pitcher"]}
    ch = FC.challenger_records(out, inputs, ctx.now_iso, start_by_game, both)
    inc, inc_stats = INC.all_records(ctx.repo, target, now)
    for r in ch + inc:
        r["official_date"] = odate.get(str(r["game_id"]))
    ch_new, skipped_same = FC.drop_unchanged(ch, ctx.archive, now)
    arch = pr.archive_forecasts(ch_new + inc, ctx.archive, ctx.now_iso)
    stats.update(archived={k: arch[k] for k in ("written", "late_rejected", "invalid", "duplicates")},
                 challenger_records=len(ch), challenger_unchanged_skipped=skipped_same, incumbent_records=len(inc),
                 incumbent_sources=inc_stats)
    stats["by_family"] = {}
    for r in ch_new + inc:
        k = f"{r['model_role']}:{r['family']}"
        stats["by_family"][k] = stats["by_family"].get(k, 0) + 1
    # gate / participation / coverage
    gcount, pcount = {}, {}
    for r in ch:
        gcount[r["gate_status"]] = gcount.get(r["gate_status"], 0) + 1
        st_ = (r.get("participation") or {}).get("status")
        if st_:
            pcount[st_] = pcount.get(st_, 0) + 1
    stats["challenger_gate"] = gcount
    stats["challenger_participation"] = pcount
    pub_players = [p for p in out["players"]]
    stats["published_players"] = len(pub_players)
    stats["hitters_assumed_not_archived"] = sum(1 for p in out["players"] if p.get("slot_source") == "ASSUMED_LAST_LINEUP")
    exp_p, exp_h, cut = {}, {}, {}
    for g in target:
        w = who.get(g["game_pk"])
        if not w:
            continue
        cut[g["game_pk"]] = g["start_utc"]
        exp_p[g["game_pk"]] = [w[s]["pitcher"]["id"] for s in ("away", "home") if w[s]["pitcher"]]
    for p in out["players"]:
        if p.get("slot") is not None:
            exp_h.setdefault(p["game_id"], set()).add(p["player_id"])
    cov_p = av.coverage(rows, {k: v for k, v in exp_p.items() if v}, cut, ctx.now_iso)
    cov_h = av.coverage(rows, {k: sorted(v) for k, v in exp_h.items()}, {k: cut[k] for k in exp_h if k in cut}, ctx.now_iso)
    stats["coverage"] = {"pitchers": cov_p["mean_coverage"], "hitters": cov_h["mean_coverage"],
                         "hitter_games_with_posted_lineup": sum(1 for g in target if who.get(g["game_pk"]) and
                                                                (who[g["game_pk"]]["away"]["lineup"] or who[g["game_pk"]]["home"]["lineup"])),
                         "events": cov_p["n_events"], "per_event_pitchers": cov_p["events"]}
    with open(os.path.join(ctx.workdir, "projection_out.json"), "w") as f:
        json.dump(out, f, default=lambda o: None if isinstance(o, float) else str(o))
    log("project", f"games {len(slate['games'])}; forecasts archived {arch['written']} (late rejected {arch['late_rejected']}, "
                   f"invalid {arch['invalid']}, duplicates {arch['duplicates']}, unchanged skipped {skipped_same}); "
                   f"incumbent records {len(inc)}; gate {gcount}; participation {pcount}; "
                   f"coverage pitchers {cov_p['mean_coverage']} hitters {cov_h['mean_coverage']}")
    return stats


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not k.startswith("_")}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def _same(a, b):
    drop = lambda d: None if d is None else {k: v for k, v in d.items() if k != "generated_utc"}  # noqa: E731
    return drop(a) == drop(b)


def stage_publish(ctx: Ctx):
    p = os.path.join(ctx.workdir, "projection_out.json")
    if not os.path.exists(p):
        raise RuntimeError("publish needs the project stage output (run --stage project,publish together)")
    out = _strip(json.load(open(p)))
    out["authority"] = "research_only"
    d = os.path.join(ctx.archive, "projections", "mlb")
    os.makedirs(d, exist_ok=True)
    latest = os.path.join(d, "latest.json")
    old = json.load(open(latest)) if os.path.exists(latest) else None
    changed = not _same(out, old)
    if changed:
        stamp = ctx.now.strftime("%Y-%m-%dT%H%MZ")
        for name in ("latest.json", f"{stamp}.json"):
            with open(os.path.join(d, name), "w") as f:
                json.dump(out, f, indent=1, sort_keys=True)
    sd = os.path.join(ctx.archive, "summary", "mlb")
    os.makedirs(sd, exist_ok=True)
    src = os.path.join(PF, "results", "summary.json")
    if os.path.exists(src):
        dst = os.path.join(sd, "latest.json")
        if not os.path.exists(dst) or open(dst).read() != open(src).read():
            shutil.copyfile(src, dst)
    res = {"stage": "publish", "changed": changed, "games": len(out["games"]), "players": len(out["players"])}
    log("publish", f"projections latest.json {'updated' if changed else 'unchanged'}: {res['games']} games, {res['players']} player rows")
    return res


def _recent_days(ctx, n=6):
    return sorted({(ctx.now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)} |
                  {(ctx.now + timedelta(days=1)).strftime("%Y-%m-%d")})


def stage_results(ctx: Ctx):
    """FINAL box scores -> outcomes (for archived forecast entities) and box-score history (for model inputs)."""
    import pandas as pd
    from mlb.live import history as HIST
    days = _recent_days(ctx, 8)
    f = pr.load_forecasts(ctx.archive, SPORT, days)
    stats = {"stage": "results", "games_checked": 0, "final": 0, "pending": 0, "not_played": 0, "rows_written": 0,
             "history_games_written": 0, "errors": []}
    now_ts = pd.Timestamp(ctx.now)
    # --- schedule statuses for forecast game dates and the history window
    hist_days = int(ctx.a.history_days)
    dates = {(ctx.now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(0, hist_days + 1)}
    if not f.empty and "official_date" in f:
        dates |= {d for d in f["official_date"].dropna().unique()}
    sched = {}
    for d in sorted(dates):
        for g in api.parse_schedule(ctx.schedule(d)[0]):
            sched[g["game_pk"]] = g
    boxes = {}

    def box_of(pk):
        if pk not in boxes:
            boxes[pk] = api.parse_boxscore(ctx.boxscore(pk))
        return boxes[pk]

    rows = []
    # --- outcomes for archived forecasts of games that have started
    if not f.empty:
        f = f[pd.to_datetime(f["game_start_utc"], utc=True) < now_ts - pd.Timedelta(minutes=15)]
    if f.empty:
        log("results", "no archived-forecast game has started yet: no outcomes to resolve")
    else:
        latest = {}
        for r in pr._read(ctx.archive, "outcomes", SPORT):
            k = (r["game_id"], r["entity_id"], r["family"])
            if k not in latest or r["observed_utc"] >= latest[k]["observed_utc"]:
                latest[k] = r
        resolved = {k[0] for k in latest}
        recheck_after = now_ts - pd.Timedelta(days=3)
        for gid, gf in f.groupby("game_id"):
            gid = str(gid)
            g = sched.get(gid)
            stats["games_checked"] += 1
            if g is None:
                stats["pending"] += 1
                continue
            wanted = {(str(a), b) for a, b in zip(gf["entity_id"], gf["family"])}
            if g["detailed"] in api.NOT_PLAYED:
                stats["not_played"] += 1
                if gid not in resolved:
                    rows += RES.void_rows(g, wanted, g["detailed"])
                continue
            if not api.is_final(g):
                stats["pending"] += 1
                continue
            stats["final"] += 1
            if gid in resolved and pd.Timestamp(g["start_utc"]) <= recheck_after:
                continue
            try:
                box = box_of(gid)
            except Exception as e:  # noqa: BLE001
                stats["errors"].append(f"{gid}: {e}")
                continue
            if box is None:
                stats["errors"].append(f"{gid}: boxscore missing/empty")
                continue
            rows += RES.changed_rows(RES.outcome_rows(g, box, wanted), latest)
    arch = pr.archive_outcomes(rows, ctx.archive, SPORT, ctx.now_iso) if rows else {"written": 0}
    stats["rows_written"] = arch["written"]
    # --- history: every final game of the window not yet stored
    have = HIST.stored_ids(ctx.archive)
    recs = []
    for pk, g in sched.items():
        if pk in have or not api.is_final(g) or g["detailed"] in api.NOT_PLAYED:
            continue
        if pd.Timestamp(g["start_utc"]) > now_ts - pd.Timedelta(hours=1):
            continue
        try:
            box = box_of(pk)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"history {pk}: {e}")
            continue
        if box and any(box["pitchers"][s] for s in ("away", "home")):
            recs.append(HIST.record_from_box(g, box))
    stats["history_games_written"] = HIST.write_records(recs, ctx.archive, ctx.now)
    log("results", f"games {stats['games_checked']}: final {stats['final']}, pending {stats['pending']}, not played {stats['not_played']}; "
                   f"outcome rows written {arch['written']}; box-score history games stored {stats['history_games_written']}"
                   + (f"; ERRORS {stats['errors']}" if stats["errors"] else ""))
    if stats["errors"]:
        raise RuntimeError("results: " + "; ".join(stats["errors"]))
    return stats


def stage_settle(ctx: Ctx):
    res = pr.settle(ctx.archive, SPORT, ctx.now_iso)
    log("settle", f"settled {res['settled']}, void {res['void']}, pending {res['pending']}, already settled "
                  f"{res['already_settled']}, corrections {res['corrections']}")
    return {"stage": "settle", **res}


def stage_score(ctx: Ctx):
    summary_p = os.path.join(PF, "results", "summary.json")
    reference = pr.reference_from_summary(json.load(open(summary_p))) if os.path.exists(summary_p) else {}
    rows = av.load_snapshots(ctx.archive, SPORT, sorted({(ctx.now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)}))
    cap_stats = {"availability_freshness": live_av.freshness_at_start(rows),
                 "last_capture": ctx.summary["stages"].get("capture")}
    md_all = []
    out = {"stage": "score", "windows": {}}
    for tag, days in (("daily", 7), ("weekly", 28), ("season", None)):
        card = pr.scorecard(SPORT, ctx.archive, reference, now_utc=ctx.now_iso, window_days=days, capture_stats=cap_stats)
        since = card["window"]["since_utc"]
        seg = SC.segments(ctx.archive, SPORT, since)
        card["segments"] = seg
        path = pr.write_scorecard(card, ctx.archive, tag=tag)
        md = pr.render_markdown(card, title=f"MLB projection scorecard ({tag})") + SC.render_segments(seg)
        with open(path.replace(".json", ".md"), "w") as fh:
            fh.write(md)
        md_all.append(md)
        out["windows"][tag] = {"settled": card["settlement"]["settled"], "void": card["settlement"]["void"],
                               "corrections": card["settlement"]["corrections"], "alerts": [a["status"] for a in card["alerts"]]}
    gs = os.environ.get("GITHUB_STEP_SUMMARY")
    if gs and not ctx.a.no_step_summary:
        with open(gs, "a") as fh:
            fh.write("\n".join(md_all) + "\n")
    s = out["windows"]["season"]
    log("score", f"season settled {s['settled']}, void {s['void']}, corrections {s['corrections']}; "
                 f"archived forecasts {card['capture']['forecasts_archived']} over {card['capture']['games']} games")
    return out


def stage_guard(ctx: Ctx):
    r = subprocess.run([sys.executable, "-m", "pf_core.archive_guard", ctx.archive], cwd=PF, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        raise RuntimeError("archive_guard failed: " + r.stdout)
    return {"stage": "guard", "ok": True}


FUNCS = {"capture": stage_capture, "project": stage_project, "publish": stage_publish, "results": stage_results,
         "settle": stage_settle, "score": stage_score, "guard": stage_guard}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="all")
    ap.add_argument("--archive", required=True, help="checkout of the projection-data branch")
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(PF)), help="repo root holding data/ (incumbent inputs, history)")
    ap.add_argument("--now", default=None, help="ISO UTC; fixes the clock (tests/replays)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recorded", default=None, help="directory of recorded schedule_<date>.json / boxscore_<pk>.json payloads")
    ap.add_argument("--horizon-hours", default=20)
    ap.add_argument("--history-days", default=21, help="results stage: also store box scores of final games this many days back")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--summary-out", default=None)
    ap.add_argument("--no-step-summary", action="store_true")
    a = ap.parse_args(argv)
    ctx = Ctx(a)
    try:
        return _run(ctx, a)
    finally:
        shutil.rmtree(ctx.tmp, ignore_errors=True)


def _run(ctx, a):
    stages = STAGES if a.stage == "all" else a.stage.split(",")
    for s in stages:
        if s not in FUNCS:
            raise SystemExit(f"unknown stage {s}")
    failed = None
    for s in stages:
        t0 = time.time()
        if s != "capture":
            ctx.refresh_clock()
        try:
            ctx.summary["stages"][s] = FUNCS[s](ctx)
        except Exception as e:  # noqa: BLE001
            failed = (s, e)
            ctx.summary["stages"][s] = {"stage": s, "error": f"{type(e).__name__}: {e}"}
            import traceback
            traceback.print_exc()
            print(f"[mlb-pf {s}] FAILED: {type(e).__name__}: {e}", flush=True)
            break
        ctx.summary["stages"][s]["seconds"] = round(time.time() - t0, 1)
    # one-line summary
    cap, prj = ctx.summary["stages"].get("capture", {}), ctx.summary["stages"].get("project", {})
    if cap.get("games_pregame") == 0 and prj.get("target_games", 0) == 0:
        line = "MLB-PF SUMMARY: no MLB games in the window (offseason/off-day): no-op success"
    else:
        line = (f"MLB-PF SUMMARY: stages {[s for s in stages if s in ctx.summary['stages']]}; "
                f"archived {prj.get('archived', {}).get('written', 'n/a')}; "
                f"settled {ctx.summary['stages'].get('settle', {}).get('settled', 'n/a')}")
    print(line)
    ctx.summary["line"] = line
    if not a.dry_run:
        d = os.path.join(ctx.archive, "runs", "mlb", ctx.now.strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, ctx.now.strftime("%H%M%S") + "Z.json"), "w") as f:
            json.dump(ctx.summary, f, indent=1, sort_keys=True, default=str)
    if a.summary_out:
        with open(a.summary_out, "w") as f:
            json.dump(ctx.summary, f, indent=1, sort_keys=True, default=str)
    gs = os.environ.get("GITHUB_STEP_SUMMARY")
    if gs and not a.no_step_summary:
        with open(gs, "a") as fh:
            fh.write(f"\n### MLB projection foundation run\n\n`{line}`\n\n```json\n{json.dumps(ctx.summary, indent=1, default=str)[:6000]}\n```\n")
    if failed:
        raise SystemExit(1)
    return ctx.summary


if __name__ == "__main__":
    main()
