#!/usr/bin/env python3
"""
scripts/edgelab/import_postmortem.py
========================================
Structured Postmortem Ingestion milestone (Part 5): saves a completed
daily postmortem -- typically a ChatGPT-to-Claude handoff containing a
finished Markdown narrative plus structured JSON findings -- as a
repository-backed record, explicitly linked to real canonical betIds.
Never substitutes a recommendation for a bet that was never actually
placed, and never fabricates an analytical field the caller didn't
supply (lib.edgelab.postmortems.build_postmortem_record).

Writes data/edgelab/postmortems/<date>/{postmortem.json, postmortem.md,
revisions.jsonl, bet_linkage.json, import_receipts.json}. Idempotent: an
identical re-import is a no-op; a genuinely different re-import is an
explicit new revision (never a silent overwrite) -- see
lib.edgelab.postmortems.write_postmortem.

Findings JSON shape (--findings-json / --findings-json-inline):
    {
      "betIds": ["<betId>", ...],
      "reportedTotals": {"totalRisked": 0, "totalReturned": 0, "netProfitLoss": 0, "roi": 0},
      "performanceByMarketFamily": [...], "gameLevelConcentration": [...],
      "analyticalWins": [...], "analyticalMisses": [...],
      "processErrors": [...], "proposedInvestigations": [...],
      "markdown": "..."   (optional -- only used if --markdown-file/--markdown-text not given)
    }
The entire findings payload is also preserved verbatim as
structuredFindings, so nothing supplied is ever silently dropped even if
this schema doesn't break it out into its own field.

Usage:
    python3 scripts/edgelab/import_postmortem.py --date 2026-08-03 \\
        --markdown-file postmortem.md --findings-json findings.json
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.postmortems import build_postmortem_record, write_postmortem


def _read_json_array(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _append_receipt(pm_dir, receipt):
    path = os.path.join(pm_dir, "import_receipts.json")
    receipts = _read_json_array(path)
    receipts.append(receipt)
    os.makedirs(pm_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(receipts, f, indent=2, sort_keys=True)


def _write_bet_linkage(pm_dir, record, all_bets_by_id):
    linkage = {
        "postmortemId": record["postmortemId"],
        "gameDate": record["gameDate"],
        "revision": record["revision"],
        "linkedBets": [
            {
                "betId": bid, "marketTicker": (all_bets_by_id.get(bid) or {}).get("marketTicker"),
                "stake": (all_bets_by_id.get(bid) or {}).get("stake"),
                "result": (all_bets_by_id.get(bid) or {}).get("result"),
                "status": (all_bets_by_id.get(bid) or {}).get("status"),
            }
            for bid in record["linkedBetIds"]
        ],
        "unresolvedBetReferences": record["unresolvedBetReferences"],
    }
    os.makedirs(pm_dir, exist_ok=True)
    with open(os.path.join(pm_dir, "bet_linkage.json"), "w") as f:
        json.dump(linkage, f, indent=2, sort_keys=True)


def _regenerate_daily_reports(date, warnings):
    for script in ("generate_daily_report.py", "generate_postmortem.py"):
        result = subprocess.run(
            [sys.executable, os.path.join("scripts", "edgelab", script), "--date", date],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            warnings.append(f"{script} --date {date} failed: {result.stderr.strip()[-500:]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="Game/betting date, YYYY-MM-DD")
    md_group = parser.add_mutually_exclusive_group()
    md_group.add_argument("--markdown-file", default=None)
    md_group.add_argument("--markdown-text", default=None)
    findings_group = parser.add_mutually_exclusive_group(required=True)
    findings_group.add_argument("--findings-json", default=None, help="Path to a JSON file with structured findings")
    findings_group.add_argument("--findings-json-inline", default=None, help="Inline JSON string with structured findings")
    parser.add_argument("--skip-report-regeneration", action="store_true")
    args = parser.parse_args()

    findings_raw = open(args.findings_json).read() if args.findings_json else args.findings_json_inline
    try:
        findings = json.loads(findings_raw)
    except json.JSONDecodeError as exc:
        print(f"[import_postmortem] findings JSON is invalid: {exc}", file=sys.stderr)
        return 1

    markdown_text = None
    if args.markdown_file:
        markdown_text = open(args.markdown_file).read()
    elif args.markdown_text:
        markdown_text = args.markdown_text
    elif findings.get("markdown"):
        markdown_text = findings["markdown"]
    else:
        print("[import_postmortem] no markdown supplied (--markdown-file/--markdown-text/findings.markdown)", file=sys.stderr)
        return 1

    all_bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    all_bets_by_id = {b["betId"]: b for b in all_bets}

    record = build_postmortem_record(
        args.date, findings.get("betIds") or [], all_bets_by_id,
        reported_totals=findings.get("reportedTotals"),
        performance_by_market_family=findings.get("performanceByMarketFamily"),
        game_level_concentration=findings.get("gameLevelConcentration"),
        analytical_wins=findings.get("analyticalWins"),
        analytical_misses=findings.get("analyticalMisses"),
        process_errors=findings.get("processErrors"),
        proposed_investigations=findings.get("proposedInvestigations"),
        structured_findings=findings,
    )

    result = write_postmortem(record, markdown_text)

    pm_dir = os.path.join("data", "edgelab", "postmortems", args.date)
    warnings = []
    if result["success"]:
        _write_bet_linkage(pm_dir, record, all_bets_by_id)
        if not args.skip_report_regeneration:
            _regenerate_daily_reports(args.date, warnings)

    receipt = {
        "generatedAt": ids.utc_now_iso(), "date": args.date, "success": result["success"],
        "duplicateStatus": result["duplicateStatus"], "revision": result.get("revision"),
        "unresolvedBetReferences": record["unresolvedBetReferences"],
        "totalsMatch": record["totalsMatch"], "errors": result.get("errors") or [], "warnings": warnings,
    }
    _append_receipt(pm_dir, receipt)

    print(json.dumps(receipt, indent=2, sort_keys=True))
    if not result["success"]:
        print(f"[import_postmortem] NOT saved: {result['duplicateStatus']}", file=sys.stderr)
        return 1
    if record["unresolvedBetReferences"]:
        print(f"[import_postmortem] saved with {len(record['unresolvedBetReferences'])} unresolved bet reference(s) -- see receipt", file=sys.stderr)
    print(f"[import_postmortem] date={args.date} duplicateStatus={result['duplicateStatus']} revision={result.get('revision')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
