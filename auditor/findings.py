"""Map verified criteria to findings that conform to SAAF outputs/schemas/finding-schema.json."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import VerifiedCriterion

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "finding-schema.json"

# Rating policy (documented in AUDIT-CRITERIA.md). Binding law gaps rate higher than principles.
BINDING = {"EU AI Act", "GDPR", "DORA"}
ALWAYS_HIGH = {"Evidence integrity"}


def short_title(c: VerifiedCriterion) -> str:
    """'EU AI Act Art. 14: Human oversight' / 'DNB SAFEST: Fairness' (max 100 chars)."""
    if "," in c.reference:
        article = c.reference.split(",", 1)[1].split(" (")[0].strip()
        prefix = f"{c.framework} {article}"
    else:
        prefix = c.reference.split(" - ")[0].split(" / ")[0].strip()
    return f"{prefix}: {c.title}"[:100]


def rating(c: VerifiedCriterion) -> str | None:
    if c.status == "not_met" and c.framework in ALWAYS_HIGH:
        return "High"
    if c.status == "not_met":
        return "High" if c.framework in BINDING else "Medium"
    if c.status in ("partially_met", "not_evidenced"):
        return "Medium" if c.framework in BINDING else "Low"
    if c.status == "inconclusive":
        return "Informational"
    return None  # met / not_applicable -> no finding


STATUS_TEXT = {
    "not_met": "Requirement not met",
    "partially_met": "Requirement partially met",
    "not_evidenced": "No evidence provided",
    "inconclusive": "Assessment inconclusive",
}


def build_findings(criteria: list[VerifiedCriterion], owner: str) -> list[dict]:
    out: list[dict] = []
    for c in criteria:
        r = rating(c)
        if r is None:
            continue
        n = len(out) + 1
        observation = f"{STATUS_TEXT[c.status]}. {c.gap}".strip()
        if c.verifier_notes:
            observation += " Verifier: " + " ".join(c.verifier_notes)
        finding = {
            "id": f"F-{n:02d}",
            "title": short_title(c),
            "risk_rating": r,
            "control_objective": f"{c.framework} - {c.reference} - {c.title}",
            "observation": observation,
            "recommendation": c.recommendation or "Auditor to determine the corrective action after review.",
            "owner": owner or "To be assigned",
            "status": "Open",
            "ai_assisted": True,
            "evidence_references": [f"quote: {q}" for q in c.evidence_quotes][:5],
        }
        out.append(finding)
    for f in out:
        problems = validate_finding(f)
        if problems:
            raise ValueError(f"Finding {f['id']} violates finding-schema.json: {problems}")
    return out


def validate_finding(finding: dict, schema_path: Path = SCHEMA_PATH) -> list[str]:
    """Minimal validator for the SAAF finding schema (no third-party dependency)."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    props = schema["properties"]
    errors = [f"missing {k}" for k in schema["required"] if k not in finding]
    for k, v in finding.items():
        spec = props.get(k)
        if spec is None:
            errors.append(f"unexpected field {k}")
            continue
        t = spec.get("type")
        if t == "string" and not isinstance(v, str):
            errors.append(f"{k} must be a string")
        if t == "boolean" and not isinstance(v, bool):
            errors.append(f"{k} must be a boolean")
        if t == "array" and not (isinstance(v, list) and all(isinstance(i, str) for i in v)):
            errors.append(f"{k} must be a list of strings")
        if "enum" in spec and v not in spec["enum"]:
            errors.append(f"{k}={v!r} not in {spec['enum']}")
        if "pattern" in spec and isinstance(v, str) and not re.fullmatch(spec["pattern"], v):
            errors.append(f"{k}={v!r} does not match {spec['pattern']}")
        if "maxLength" in spec and isinstance(v, str) and len(v) > spec["maxLength"]:
            errors.append(f"{k} longer than {spec['maxLength']}")
    return errors
