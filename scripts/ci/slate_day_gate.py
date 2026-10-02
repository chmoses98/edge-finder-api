#!/usr/bin/env python3
"""
scripts/ci/slate_day_gate.py
============================
Decides, for ONE Fetch Slate Data run, whether the expensive `fetch` job
(and the bankroll refresh in front of it) should run at all. Called by the
`slate_day` job in .github/workflows/fetch-slate.yml AFTER it fetched
/api/slate for the date and ran scripts/validate_slate_pre.py on it.

WHY
---
* 2026-09-28 was an MLB off-day. The empty slate fell through the whole
  pipeline and the post-fetch gate failed the run -- three red runs for a
  day on which there was nothing to do (runs 36486867929, 36502136929,
  36507307766). The offseason would repeat that three times a day.
* The workflow fires three times a day (16:00, 20:00, 22:00 UTC). A slate
  that cannot be verified at 16:00 is normally fine by 20:00; only the
  LAST scheduled attempt (or a human's dispatch/push) is the point at
  which "still broken" is actionable.

DECISION TABLE
--------------
pre_exit is scripts/validate_slate_pre.py's exit code.

  slate fetch HTTP failed       -> SLATE_FETCH_FAILED : proceed only on the
                                   final attempt (the fetch job then fails
                                   with full diagnostics -> red); earlier
                                   attempts are DEGRADED + retried.
  pre_exit 0  (ready)           -> GAME_DAY           : proceed.
  pre_exit 2  (starters TBD)    -> GAME_DAY           : proceed, with a
                                   warning (unchanged de-facto behaviour --
                                   TBD starters use the league-average
                                   fallback; never red on their own).
  pre_exit 3  (verified off-day)-> OFF_DAY            : do NOT proceed; the
                                   run concludes SUCCESS. fetch_status.json
                                   already records NO_GAMES_SCHEDULED with
                                   the MLB schedule evidence.
  pre_exit 1  (not verifiable)  -> NOT_VERIFIED       : proceed only on the
                                   final attempt (fetch job's own
                                   pre-validation then fails -> ONE red);
                                   earlier attempts are DEGRADED + retried.
  anything else                 -> error (exit 2): an unexpected validator
                                   result is never guessed at.

"Final attempt" = any non-schedule event (workflow_dispatch, push), or the
schedule event whose cron is FINAL_CRON. It never decides from the clock.

Pure decide() + a tiny CLI that writes $GITHUB_OUTPUT / $GITHUB_STEP_SUMMARY
and prints ::warning:: annotations. Never touches the network or git.
"""

import argparse
import os
import sys

#: Must equal the LAST `schedule` cron in .github/workflows/fetch-slate.yml
#: (pinned by tests/test_fetch_slate_slate_day_gate.py).
FINAL_CRON = "0 22 * * *"

GAME_DAY = "GAME_DAY"
OFF_DAY = "OFF_DAY"
NOT_VERIFIED = "NOT_VERIFIED"
SLATE_FETCH_FAILED = "SLATE_FETCH_FAILED"

PRE_OK, PRE_HARD_FAIL, PRE_NOT_READY, PRE_NO_GAMES = 0, 1, 2, 3


def is_final_attempt(event_name, cron):
    """Pure. A human-initiated run (dispatch/push) is always final; a
    scheduled run is final only when it is the last cron of the day."""
    if event_name != "schedule":
        return True
    return (cron or "").strip() == FINAL_CRON


def decide(event_name, cron, slate_http_ok, pre_exit):
    """
    Pure. Returns a dict:
      verdict  -- GAME_DAY / OFF_DAY / NOT_VERIFIED / SLATE_FETCH_FAILED
      proceed  -- bool: run refresh_bankroll + fetch
      final    -- bool: this is the last attempt of the day
      health   -- HEALTHY / NOT_APPLICABLE / DEGRADED / FAILING (the fetch
                  job will turn red)
      warnings -- list of human-readable warnings (annotations)
      summary  -- one-line summary
    Raises ValueError for an unexpected pre_exit.
    """
    final = is_final_attempt(event_name, cron)
    warnings = []

    if not slate_http_ok:
        verdict = SLATE_FETCH_FAILED
        proceed = final
        if final:
            health = "FAILING"
            summary = "slate fetch failed on the final attempt -- running the full fetch job for diagnostics"
        else:
            health = "DEGRADED"
            summary = "slate fetch failed on a non-final scheduled attempt -- deferred to the next attempt"
            warnings.append("Slate fetch failed (non-final scheduled attempt): DEGRADED, "
                            "retried at the next scheduled attempt")
    elif pre_exit == PRE_NO_GAMES:
        verdict, proceed, health = OFF_DAY, False, "NOT_APPLICABLE"
        summary = "NO_GAMES_SCHEDULED (MLB schedule evidence) -- nothing to fetch; run concludes success"
    elif pre_exit in (PRE_OK, PRE_NOT_READY):
        verdict, proceed, health = GAME_DAY, True, "HEALTHY"
        summary = "game day -- running the slate pipeline"
        if pre_exit == PRE_NOT_READY:
            warnings.append("Starters not yet posted for some games: TBD starters use the "
                            "league-average fallback; later attempts refresh them (not a failure)")
            summary = "game day, some starters TBD -- running the slate pipeline (warning only)"
    elif pre_exit == PRE_HARD_FAIL:
        verdict = NOT_VERIFIED
        proceed = final
        if final:
            health = "FAILING"
            summary = ("slate not verifiable on the final attempt -- running the fetch job, "
                       "which fails closed")
        else:
            health = "DEGRADED"
            summary = "slate not verifiable on a non-final scheduled attempt -- deferred to the next attempt"
            warnings.append("Slate not verifiable (empty with games scheduled / schedule unknown / "
                            "wrong date): DEGRADED on a non-final scheduled attempt, retried at the "
                            "next scheduled attempt; the final attempt fails if still broken")
    else:
        raise ValueError("unexpected validate_slate_pre exit code: %r" % (pre_exit,))

    return {"verdict": verdict, "proceed": proceed, "final": final, "health": health,
            "warnings": warnings, "summary": summary}


def _append(path, text):
    if path:
        with open(path, "a") as f:
            f.write(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--cron", default="")
    p.add_argument("--slate-http-ok", choices=("true", "false"), required=True)
    p.add_argument("--pre-exit", type=int, default=None,
                   help="validate_slate_pre.py exit code (omit when the slate fetch failed)")
    args = p.parse_args(argv)

    http_ok = args.slate_http_ok == "true"
    if http_ok and args.pre_exit is None:
        print("::error::--pre-exit is required when the slate fetch succeeded", file=sys.stderr)
        return 2
    try:
        d = decide(args.event, args.cron, http_ok, args.pre_exit)
    except ValueError as e:
        print("::error title=Slate-day gate::%s" % e, file=sys.stderr)
        return 2

    for w in d["warnings"]:
        print("::warning title=Fetch slate %s::%s" % (args.date, w))
    print("slate-day gate: date=%s event=%s cron=%r final=%s verdict=%s proceed=%s health=%s"
          % (args.date, args.event, args.cron, d["final"], d["verdict"], d["proceed"], d["health"]))

    _append(os.environ.get("GITHUB_OUTPUT"),
            "verdict=%s\nproceed=%s\nhealth=%s\nfinal=%s\n"
            % (d["verdict"], "true" if d["proceed"] else "false", d["health"],
               "true" if d["final"] else "false"))
    _append(os.environ.get("GITHUB_STEP_SUMMARY"),
            "### Fetch slate %s: %s (%s)\n\n- attempt: %s%s\n- %s\n"
            % (args.date, d["verdict"], d["health"], args.event,
               (" `%s`" % args.cron) if args.cron else "",
               d["summary"]) + "".join("- :warning: %s\n" % w for w in d["warnings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
