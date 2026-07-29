#!/usr/bin/env python3
"""
lib/research/inning_result_report.py
=========================================
Model Performance Phase 2A, Part 15 -- pure human-readable research
reporting formatters for F3/F5/F7 inning-result shadow-ledger rows.

Every formatter is pure (string in, string out; no file I/O, no
network, no clock reads) and never presents a legacy conditional
probability as if it were directly comparable to a three-way Kalshi
price without the mandated warning sentence.
"""

LEGACY_WARNING = (
    "Legacy probabilities are conditional on no tie and are not directly "
    "comparable with unconditional three-way Kalshi contract prices."
)


def _pct(p):
    return f"{p * 100:.1f}%" if p is not None else "n/a"


def _cents(p):
    return f"{round(p * 100)}¢" if p is not None else "n/a"


def _signed_pct(p):
    if p is None:
        return "n/a"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p * 100:.1f}%"


def format_f5_result_report(away_row, tie_row, home_row):
    """
    Pure. Builds the Part 15 human-readable F5 report from three
    shadow-ledger rows (Away/Tie/Home for the same market). Any row
    may be None if that leg wasn't discovered/priced -- rendered as
    "n/a" rather than omitted, so a caller can see a leg is missing
    instead of silently not showing it.
    """
    lines = ["F5 result:"]
    lines.append(f"- Away model: {_pct(away_row['canonicalModelProb'] if away_row else None)}")
    lines.append(f"- Tie model: {_pct(tie_row['canonicalModelProb'] if tie_row else None)}")
    lines.append(f"- Home model: {_pct(home_row['canonicalModelProb'] if home_row else None)}")
    lines.append(f"- Away ask: {_cents(away_row['yesAsk'] if away_row else None)}")
    lines.append(f"- Tie ask: {_cents(tie_row['yesAsk'] if tie_row else None)}")
    lines.append(f"- Home ask: {_cents(home_row['yesAsk'] if home_row else None)}")
    away_edge = away_row["executableYesEdge"] if away_row else None
    tie_edge = tie_row["executableYesEdge"] if tie_row else None
    home_edge = home_row["executableYesEdge"] if home_row else None
    lines.append(f"- Shadow edge: {_signed_pct(away_edge)}, {_signed_pct(tie_edge)}, {_signed_pct(home_edge)}")
    lines.append("")
    lines.append("Legacy F5 no-tie conditional:")
    lines.append(f"- Away: {_pct(away_row['legacyConditionalProb'] if away_row else None)}")
    lines.append(f"- Home: {_pct(home_row['legacyConditionalProb'] if home_row else None)}")
    lines.append("")
    lines.append(LEGACY_WARNING)
    return "\n".join(lines)


def format_unresolved_horizon_report(scope):
    """
    Pure. Builds the Part 15 human-readable report for an F3/F7
    horizon whose contract structure remains unresolved -- never
    fabricates a model probability or price for it.
    """
    return "\n".join([
        f"{scope} result:",
        "- Market existence: confirmed",
        "- Contract structure: unresolved",
        "- Repository ingestion: newly retained",
        "- Model binding: disabled pending structure verification",
    ])
