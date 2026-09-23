"""
Hypothesis registry + spec freezer for the MRV program.

The registry JSON is the frozen search space.  Runners write results back
through record_result(); they may change `status` and add a `results`
block, never the hypothesis text, family, or the frozen fields.  Any change
to a frozen field is detected by fingerprint().

freeze_spec() writes a prospective specification with a sha256 over the
canonical JSON of its rule; it refuses to overwrite an existing spec.
"""
import hashlib
import json
import os

DEFAULT_REGISTRY_PATH = os.path.join(
    "data", "edgelab", "research_artifacts", "market_structure", "hypothesis_registry.json")

STATUSES = ("PROPOSED", "DATA_BLOCKED", "EXPLORATORY", "REJECTED", "CANDIDATE",
            "FROZEN_FOR_PROSPECTIVE", "PROSPECTIVE_PASS", "PROSPECTIVE_FAIL")
FROZEN_FIELDS = ("id", "family", "title", "statement", "data", "unit", "primaryMetric",
                 "method", "failureCriterion")
ALLOWED_TRANSITIONS = {
    "PROPOSED": {"DATA_BLOCKED", "EXPLORATORY", "REJECTED", "CANDIDATE"},
    "EXPLORATORY": {"REJECTED", "CANDIDATE", "DATA_BLOCKED"},
    "CANDIDATE": {"FROZEN_FOR_PROSPECTIVE", "REJECTED"},
    "FROZEN_FOR_PROSPECTIVE": {"PROSPECTIVE_PASS", "PROSPECTIVE_FAIL"},
    "DATA_BLOCKED": {"EXPLORATORY", "REJECTED"},
    "REJECTED": set(), "PROSPECTIVE_PASS": set(), "PROSPECTIVE_FAIL": set(),
}


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_of(obj):
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def load_registry(path=DEFAULT_REGISTRY_PATH):
    with open(path) as f:
        return json.load(f)


def save_registry(reg, path=DEFAULT_REGISTRY_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def validate_registry(reg):
    """Raises ValueError on a malformed registry.  Returns the list of hypothesis ids."""
    ids = []
    fams = set(reg.get("multiplicityFamilies", {}))
    for h in reg.get("hypotheses", []):
        for k in FROZEN_FIELDS:
            if not h.get(k):
                raise ValueError("hypothesis %r missing frozen field %r" % (h.get("id"), k))
        if h["id"] in ids:
            raise ValueError("duplicate id %s" % h["id"])
        if h["family"] not in fams:
            raise ValueError("%s: unknown family %s" % (h["id"], h["family"]))
        if h.get("status") not in STATUSES:
            raise ValueError("%s: bad status %r" % (h["id"], h.get("status")))
        ids.append(h["id"])
    return ids


def fingerprint(reg):
    """sha256 over the frozen fields of every hypothesis (order-independent)."""
    frozen = sorted(({k: h[k] for k in FROZEN_FIELDS} for h in reg["hypotheses"]), key=lambda d: d["id"])
    return sha256_of(frozen)


def get(reg, hid):
    for h in reg["hypotheses"]:
        if h["id"] == hid:
            return h
    raise KeyError(hid)


def record_result(reg, hid, status, result, *, allow_same=True):
    """
    Attach a result block and move status.  Refuses illegal transitions and
    never edits frozen fields.  `result` must be JSON-serialisable and should
    carry at least: generatedAt, tier, n, games, dates, effect, ci, p,
    bhFamily, feeTreatment, execution, reasonsCouldBeFalse.
    """
    h = get(reg, hid)
    cur = h.get("status")
    if status != cur and status not in ALLOWED_TRANSITIONS.get(cur, set()):
        raise ValueError("%s: illegal transition %s -> %s" % (hid, cur, status))
    if status == cur and not allow_same:
        raise ValueError("%s: status unchanged" % hid)
    json.dumps(result)
    h["status"] = status
    h.setdefault("results", []).append(result)
    return h


def render_markdown(reg):
    lines = ["# %s hypothesis registry" % reg["programId"], "",
             "Frozen %s. Fingerprint of frozen fields: `%s`." % (reg.get("frozenAt"), fingerprint(reg)), "",
             "| ID | Family | Status | Title | Latest result |", "|---|---|---|---|---|"]
    for h in reg["hypotheses"]:
        res = h.get("results", [])
        last = res[-1].get("summary", "") if res else ""
        lines.append("| %s | %s | %s | %s | %s |" % (h["id"], h["family"], h["status"], h["title"], last.replace("|", "/")))
    lines.append("")
    return "\n".join(lines)


def freeze_spec(spec, path):
    """
    Write a prospective spec with `ruleSha256` computed over spec['rule'] and
    `specSha256` over the whole document minus the hashes.  Refuses to
    overwrite.  Returns the sha of the rule.
    """
    if os.path.exists(path):
        raise FileExistsError("frozen spec already exists: %s (never overwrite; write a new version)" % path)
    if "rule" not in spec or "specId" not in spec:
        raise ValueError("spec needs 'specId' and 'rule'")
    doc = dict(spec)
    doc["ruleSha256"] = sha256_of(spec["rule"])
    body = {k: v for k, v in doc.items() if k not in ("specSha256",)}
    doc["specSha256"] = sha256_of(body)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return doc["ruleSha256"]


def verify_spec(path):
    """Recompute both hashes; returns (rule_ok, spec_ok)."""
    with open(path) as f:
        doc = json.load(f)
    rule_ok = sha256_of(doc["rule"]) == doc.get("ruleSha256")
    body = {k: v for k, v in doc.items() if k != "specSha256"}
    spec_ok = sha256_of(body) == doc.get("specSha256")
    return rule_ok, spec_ok
