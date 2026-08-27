"""
lib/edgelab/dispositions.py
===============================
Research Lab Milestone 0A: the standard experiment result/status
contract, and the PRODUCTION PROMOTION FIREWALL -- the single place
that structurally guarantees no Research Lab tooling can ever assign the
PRODUCTION disposition.

PRODUCTION exists in this vocabulary purely as a DOCUMENTED NAME for a
state that a future, entirely separate, deliberate human production-
review process may eventually assign (spec: "eligible for a SEPARATE
deliberate production-review process... Must NEVER be assigned
automatically by Research Lab tooling"). No function in this module, in
lib.edgelab.experiment_report, or anywhere else in this milestone is
capable of returning/writing PRODUCTION -- assign_disposition() and
build_experiment_report() both hard-refuse it unconditionally (no
override parameter exists to bypass the refusal). If a real production
promotion process is ever built, it must live entirely outside this
milestone's code path, reading a PROMOTION_CANDIDATE report as its own
separate input -- never by this module growing a way to emit PRODUCTION.
"""

REJECT = "REJECT"
RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"
SHADOW_CANDIDATE = "SHADOW_CANDIDATE"
PROMOTION_CANDIDATE = "PROMOTION_CANDIDATE"
PRODUCTION = "PRODUCTION"  # documented name only -- see module docstring; never assignable here

ALL_DISPOSITIONS = frozenset({REJECT, RESEARCH_CANDIDATE, SHADOW_CANDIDATE, PROMOTION_CANDIDATE, PRODUCTION})

# The only dispositions any function in this milestone is permitted to
# assign. PRODUCTION is deliberately excluded -- this is the firewall.
AUTOMATICALLY_ASSIGNABLE_DISPOSITIONS = frozenset({REJECT, RESEARCH_CANDIDATE, SHADOW_CANDIDATE, PROMOTION_CANDIDATE})

DISPOSITION_DESCRIPTIONS = {
    REJECT: "Research candidate failed, or evidence argues against continuation.",
    RESEARCH_CANDIDATE: "Interesting enough for further research but not yet sufficiently validated.",
    SHADOW_CANDIDATE: "Passed a leakage-safe chronological/out-of-sample stage strongly enough to justify prospective shadow evaluation.",
    PROMOTION_CANDIDATE: "Passed required prospective shadow evidence and is eligible for a SEPARATE deliberate production-review process.",
    PRODUCTION: "Must NEVER be assigned automatically by Research Lab tooling -- reserved for a future, entirely separate, deliberate human production-review process.",
}


class ProductionDispositionForbiddenError(ValueError):
    """Raised whenever any code path in this milestone is asked to assign PRODUCTION."""


def validate_disposition(disposition: str) -> None:
    if disposition not in ALL_DISPOSITIONS:
        raise ValueError(f"Unknown disposition {disposition!r}. Known: {sorted(ALL_DISPOSITIONS)}")


def assign_disposition(disposition: str) -> str:
    """
    The ONLY sanctioned way any Research Lab function should hand a
    disposition value to a caller -- always call this rather than
    passing a raw string through, so the PRODUCTION firewall is enforced
    at one single choke point. Returns `disposition` unchanged when it
    is automatically-assignable; raises ProductionDispositionForbiddenError
    for PRODUCTION and ValueError for anything not in ALL_DISPOSITIONS.
    """
    validate_disposition(disposition)
    if disposition == PRODUCTION:
        raise ProductionDispositionForbiddenError(
            "PRODUCTION cannot be assigned by Research Lab tooling -- it is reserved for a separate, "
            "deliberate human production-review process outside this milestone's code. "
            "See lib.edgelab.dispositions module docstring."
        )
    return disposition
