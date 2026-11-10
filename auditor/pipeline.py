"""classify -> select criteria (code) -> assess -> verify (code) -> findings + report."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import applicability
from .findings import build_findings
from .guard import FENCE_RULE, AuditTrail, detect_injection, fence, is_grounded, overall_verdict, sha256_text
from .llm import LLM, LLMError
from .models import Assessment, Classification, VerifiedCriterion

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
REQUIRED_SYSTEM_FIELDS = ["id", "name", "organisation", "organisation_type", "role", "intended_purpose"]
DOC_SUFFIXES = {".md", ".txt"}
MAX_DOC_BYTES = 400_000
MAX_DOCS = 30
MAX_TOTAL_BYTES = 1_500_000
BATCH = 8


class InputError(ValueError):
    pass


@dataclass
class AuditInput:
    system: dict
    documents: dict[str, str]          # name -> text (includes system.json narrative)
    system_sha256: str = ""
    skipped: list[str] = field(default_factory=list)   # files refused at load time, with reason

    @property
    def corpus(self) -> str:
        return "\n\n".join(self.documents.values())

    def fenced(self) -> str:
        return "\n\n".join(fence(text, name) for name, text in self.documents.items())


@dataclass
class AuditResult:
    system: dict
    classification: Classification
    classification_notes: list[str]
    criteria: list[VerifiedCriterion]
    notes: list[dict]
    findings: list[dict]
    verdict: str
    documents: dict[str, str] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    scope_facts: list[str] = field(default_factory=list)
    system_sha256: str = ""
    skipped: list[str] = field(default_factory=list)
    trail_head: str = ""


def _safe_file(p: Path, root: Path) -> str | None:
    """Return a reason to refuse `p`, or None. Refuses symlinks and anything resolving outside `root`."""
    if p.is_symlink():
        return "symbolic link (refused: could point to files outside the evidence folder)"
    try:
        p.resolve(strict=True).relative_to(root)
    except (ValueError, OSError):
        return "resolves outside the evidence folder"
    if not p.is_file():
        return "not a regular file"
    return None


def load_input(input_dir: Path) -> AuditInput:
    if input_dir.is_symlink():
        raise InputError(f"{input_dir} is a symbolic link; pass the real folder.")
    root = input_dir.resolve()
    sys_path = input_dir / "system.json"
    if not sys_path.exists():
        raise InputError(f"{sys_path} not found")
    reason = _safe_file(sys_path, root)
    if reason:
        raise InputError(f"system.json refused: {reason}")
    raw = sys_path.read_text(encoding="utf-8")
    try:
        system = json.loads(raw)
    except ValueError as e:
        raise InputError(f"system.json is not valid JSON: {e}") from e
    if not isinstance(system, dict):
        raise InputError("system.json must be a JSON object")
    missing = [k for k in REQUIRED_SYSTEM_FIELDS if not system.get(k)]
    if missing:
        raise InputError(f"system.json is missing required fields: {missing}")

    narrative = "\n".join(f"{k}: {v}" for k, v in system.items() if isinstance(v, (str, bool, int)))
    documents = {"system.json": narrative}
    skipped: list[str] = []
    total = 0
    doc_dir = input_dir / "docs"
    if doc_dir.is_dir() and not doc_dir.is_symlink():
        for p in sorted(doc_dir.iterdir()):
            if p.suffix.lower() not in DOC_SUFFIXES:
                continue
            reason = _safe_file(p, root)
            if reason:
                skipped.append(f"docs/{p.name}: {reason}")
                continue
            size = p.stat().st_size
            if size > MAX_DOC_BYTES:
                raise InputError(f"{p.name} exceeds {MAX_DOC_BYTES} bytes; split it or extract the relevant part.")
            total += size
            if len(documents) > MAX_DOCS or total > MAX_TOTAL_BYTES:
                raise InputError(f"Evidence exceeds the limits ({MAX_DOCS} documents / {MAX_TOTAL_BYTES} bytes in total); "
                                 "select the relevant documents.")
            documents[f"docs/{p.name}"] = p.read_text(encoding="utf-8", errors="replace")
    elif doc_dir.is_symlink():
        raise InputError("docs/ is a symbolic link; pass the real folder.")
    if len(documents) == 1:
        raise InputError("No evidence documents found in docs/ (.md or .txt).")
    return AuditInput(system=system, documents=documents, system_sha256=sha256_text(raw), skipped=skipped)


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8") + "\n\n" + FENCE_RULE


def classify(inp: AuditInput, llm: LLM, trail: AuditTrail | None) -> tuple[Classification, list[str], dict]:
    user = "Classify this AI system. Evidence documents follow.\n\n" + inp.fenced()
    result, meta = llm.call(_prompt("classify"), user, Classification)
    notes: list[str] = []
    for t in result.triggers:
        if not is_grounded(t.trigger_quote, inp.documents):
            notes.append(f"Trigger quote for {t.article} not found verbatim in the documents; treat as unsupported.")
    declared = inp.system.get("role")
    if declared and result.role != declared:
        notes.append(f"Model role '{result.role}' differs from declared role '{declared}'; declared role used.")
        result = result.model_copy(update={"role": declared})
    if not result.tiers:
        notes.append("Model returned no risk tier.")
    call = {"step": "classify", **meta.__dict__}
    if trail:
        trail.append("llm_call", **call)
        trail.append("classification", tiers=list(result.tiers), role=result.role,
                     fria_required=result.fria_required, verifier_notes=notes)
    return result, notes, call


def _criteria_block(criteria: list[dict]) -> str:
    slim = [{k: c[k] for k in ("id", "framework", "reference", "title", "requirement", "good_evidence")} for c in criteria]
    return "<criteria>\n" + json.dumps(slim, indent=2, ensure_ascii=False) + "\n</criteria>"


def verify_assessment(raw: Assessment, criteria: list[dict], corpus: str | dict[str, str]) -> list[VerifiedCriterion]:
    by_id = {c["id"]: c for c in criteria}
    seen: dict[str, VerifiedCriterion] = {}
    for a in raw.assessments:
        c = by_id.get(a.criterion_id)
        if c is None or a.criterion_id in seen:
            continue  # unknown or duplicate id from the model: ignored
        notes: list[str] = []
        grounded = [q for q in a.evidence_quotes if is_grounded(q, corpus)]
        dropped = len(a.evidence_quotes) - len(grounded)
        if dropped:
            notes.append(f"{dropped} quote(s) not found verbatim in the documents were removed.")
        status = a.status
        if status in ("met", "partially_met") and not grounded:
            notes.append(f"Model said '{status}' without a verifiable quote; downgraded to not_evidenced.")
            status = "not_evidenced"
        if status == "not_applicable" and not a.gap.strip():
            notes.append("Marked not_applicable without a reason; set to inconclusive.")
            status = "inconclusive"
        seen[a.criterion_id] = VerifiedCriterion(
            criterion_id=c["id"], framework=c["framework"], reference=c["reference"], title=c["title"],
            status=status, evidence_quotes=grounded, gap=a.gap, recommendation=a.recommendation,
            verifier_notes=notes, applies_from=c.get("applies_from", ""),
        )
    out = []
    for c in criteria:  # catalogue order; anything the model skipped is inconclusive, never green
        out.append(seen.get(c["id"]) or VerifiedCriterion(
            criterion_id=c["id"], framework=c["framework"], reference=c["reference"], title=c["title"],
            status="inconclusive", evidence_quotes=[], gap="The model returned no assessment for this criterion.",
            recommendation="Assess manually or re-run.", verifier_notes=["Missing from model output."],
            applies_from=c.get("applies_from", ""),
        ))
    return out


def assess(inp: AuditInput, criteria: list[dict], llm: LLM, trail: AuditTrail | None) -> tuple[list[VerifiedCriterion], list[dict]]:
    verified: list[VerifiedCriterion] = []
    calls: list[dict] = []
    fenced = inp.fenced()
    for i in range(0, len(criteria), BATCH):
        batch = criteria[i:i + BATCH]
        user = _criteria_block(batch) + "\n\nEvidence documents:\n\n" + fenced
        raw, meta = llm.call(_prompt("assess"), user, Assessment)
        call = {"step": f"assess[{i // BATCH + 1}]", **meta.__dict__}
        calls.append(call)
        part = verify_assessment(raw, batch, inp.documents)
        verified.extend(part)
        if trail:
            trail.append("llm_call", **call)
            trail.append("verified", results={v.criterion_id: v.status for v in part},
                         verifier_notes={v.criterion_id: v.verifier_notes for v in part if v.verifier_notes})
    return verified, calls


INJECTION_CRITERION_ID = "SAAF-EVIDENCE-INTEGRITY"


def injection_check(inp: AuditInput) -> VerifiedCriterion | None:
    """Deterministic evidence-integrity check; becomes its own finding when it fires."""
    hits = detect_injection(inp.documents)
    if not hits:
        return None
    docs = sorted({d for d, _ in hits})
    return VerifiedCriterion(
        criterion_id=INJECTION_CRITERION_ID,
        framework="Evidence integrity",
        reference="OWASP LLM01 Prompt Injection / IIA Std 14.2",
        title="Auditee evidence contains instructions aimed at AI audit tools",
        status="not_met",
        evidence_quotes=[line for _, line in hits][:5],
        gap=(f"{len(hits)} passage(s) in {', '.join(docs)} attempt to instruct an AI audit tool. "
             "This undermines the reliability of the evidence and may indicate an attempt to influence the audit outcome."),
        recommendation=("Establish who added the passage and why; obtain a clean, authorised version of the document "
                        "from its owner and re-perform the assessment on that version."),
        verifier_notes=["Detected by code (pattern scan), independent of the model."],
    )


SCOPE_CRITERION_ID = "SAAF-SCOPE-INTEGRITY"

# (declared fact in system.json, what the evidence shows according to the classification, label)
_SCOPE_CHECKS = [
    ("financial_institution", "evidence_indicates_financial_institution",
     "organisation_type does not mark a financial institution, so DNB SAFEST and DORA were not tested"),
    ("personal_data", "evidence_indicates_personal_data",
     "personal_data is not set, so GDPR Art. 22 was not tested"),
    ("automated_decisions_on_individuals", "evidence_indicates_automated_decisions",
     "automated_decisions_on_individuals is not set, so GDPR Art. 22 / AI Act Art. 86 were not tested"),
    ("third_party_provider", "evidence_indicates_third_party_provider",
     "third_party_provider is empty, so DORA third-party risk was not tested"),
]


def scope_check(system: dict, classification: Classification, fs: set[str]) -> VerifiedCriterion | None:
    """Flag when system.json declares a narrower scope than the evidence shows (it decides which laws are tested)."""
    declared = {
        "financial_institution": "financial_institution" in fs,
        "personal_data": bool(system.get("personal_data")),
        "automated_decisions_on_individuals": bool(system.get("automated_decisions_on_individuals")),
        "third_party_provider": bool(system.get("third_party_provider")),
    }
    gaps = [label for key, attr, label in _SCOPE_CHECKS
            if getattr(classification, attr) and not declared[key]]
    if not gaps:
        return None
    return VerifiedCriterion(
        criterion_id=SCOPE_CRITERION_ID, framework="Scope integrity",
        reference="IIA Std 13.3 (engagement scope) / system.json",
        title="Declared scope is narrower than the evidence shows",
        status="not_met", evidence_quotes=[],
        gap="The evidence indicates more than system.json declares: " + "; ".join(gaps) + ".",
        recommendation=("Have the auditor (not the auditee) confirm the scope fields in system.json and re-run, "
                        "or document why the narrower scope is justified."),
        verifier_notes=["Comparison of system.json with the model's reading of the evidence (code-level check)."],
    )


def run(input_dir: Path, llm: LLM, trail: AuditTrail | None = None) -> AuditResult:
    inp = load_input(input_dir)
    integrity = injection_check(inp)
    if trail:
        trail.append("run_start", system_id=inp.system["id"], system_json_sha256=inp.system_sha256,
                     documents={n: sha256_text(t) for n, t in inp.documents.items()},
                     skipped_files=inp.skipped)
        trail.append("evidence_integrity", injection_passages=len(integrity.evidence_quotes) if integrity else 0)
    classification, cnotes, ccall = classify(inp, llm, trail)
    catalogue = applicability.load_catalogue()
    fs = applicability.facts(inp.system, classification)
    criteria, notes = applicability.select(catalogue, fs)
    scope = scope_check(inp.system, classification, fs)
    if trail:
        trail.append("applicability", facts=sorted(fs), criteria=[c["id"] for c in criteria],
                     scope_mismatch=scope.gap if scope else "")

    verified, calls = assess(inp, criteria, llm, trail) if criteria else ([], [])
    if scope:
        verified.insert(0, scope)
    if integrity:
        verified.insert(0, integrity)
    statuses = [v.status for v in verified]
    if not classification.tiers:
        statuses.append("inconclusive")
    verdict = overall_verdict(statuses)
    findings = build_findings(verified, inp.system.get("owner", ""))
    if trail:
        trail.append("result", verdict=verdict, findings=len(findings))
    return AuditResult(
        system=inp.system, classification=classification, classification_notes=cnotes,
        criteria=verified, notes=notes, findings=findings, verdict=verdict,
        documents=inp.documents, calls=[ccall, *calls],
        scope_facts=sorted(fs), system_sha256=inp.system_sha256, skipped=inp.skipped,
    )


__all__ = ["run", "load_input", "verify_assessment", "InputError", "LLMError", "AuditResult"]
