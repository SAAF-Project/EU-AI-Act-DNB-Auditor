"""classify -> select criteria (code) -> assess -> verify (code) -> findings + report."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import applicability
from .findings import build_findings
from .guard import FENCE_RULE, AuditTrail, fence, is_grounded, overall_verdict, sha256_text
from .llm import LLM, LLMError
from .models import Assessment, Classification, VerifiedCriterion

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
REQUIRED_SYSTEM_FIELDS = ["id", "name", "organisation", "organisation_type", "role", "intended_purpose"]
DOC_SUFFIXES = {".md", ".txt"}
MAX_DOC_BYTES = 400_000
BATCH = 8


class InputError(ValueError):
    pass


@dataclass
class AuditInput:
    system: dict
    documents: dict[str, str]          # name -> text (includes system.json narrative)

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


def load_input(input_dir: Path) -> AuditInput:
    sys_path = input_dir / "system.json"
    if not sys_path.is_file():
        raise InputError(f"{sys_path} not found")
    try:
        system = json.loads(sys_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise InputError(f"system.json is not valid JSON: {e}") from e
    missing = [k for k in REQUIRED_SYSTEM_FIELDS if not system.get(k)]
    if missing:
        raise InputError(f"system.json is missing required fields: {missing}")

    narrative = "\n".join(f"{k}: {v}" for k, v in system.items() if isinstance(v, (str, bool, int)))
    documents = {"system.json": narrative}
    doc_dir = input_dir / "docs"
    if doc_dir.is_dir():
        for p in sorted(doc_dir.iterdir()):
            if p.suffix.lower() not in DOC_SUFFIXES or not p.is_file():
                continue
            if p.stat().st_size > MAX_DOC_BYTES:
                raise InputError(f"{p.name} exceeds {MAX_DOC_BYTES} bytes; split it or extract the relevant part.")
            documents[f"docs/{p.name}"] = p.read_text(encoding="utf-8", errors="replace")
    if len(documents) == 1:
        raise InputError("No evidence documents found in docs/ (.md or .txt).")
    return AuditInput(system=system, documents=documents)


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8") + "\n\n" + FENCE_RULE


def classify(inp: AuditInput, llm: LLM, trail: AuditTrail | None) -> tuple[Classification, list[str], dict]:
    user = "Classify this AI system. Evidence documents follow.\n\n" + inp.fenced()
    result, meta = llm.call(_prompt("classify"), user, Classification)
    notes: list[str] = []
    for t in result.triggers:
        if not is_grounded(t.trigger_quote, inp.corpus):
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


def verify_assessment(raw: Assessment, criteria: list[dict], corpus: str) -> list[VerifiedCriterion]:
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
            verifier_notes=notes,
        )
    out = []
    for c in criteria:  # catalogue order; anything the model skipped is inconclusive, never green
        out.append(seen.get(c["id"]) or VerifiedCriterion(
            criterion_id=c["id"], framework=c["framework"], reference=c["reference"], title=c["title"],
            status="inconclusive", evidence_quotes=[], gap="The model returned no assessment for this criterion.",
            recommendation="Assess manually or re-run.", verifier_notes=["Missing from model output."],
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
        part = verify_assessment(raw, batch, inp.corpus)
        verified.extend(part)
        if trail:
            trail.append("llm_call", **call)
            trail.append("verified", results={v.criterion_id: v.status for v in part},
                         verifier_notes={v.criterion_id: v.verifier_notes for v in part if v.verifier_notes})
    return verified, calls


def run(input_dir: Path, llm: LLM, trail: AuditTrail | None = None) -> AuditResult:
    inp = load_input(input_dir)
    if trail:
        trail.append("run_start", system_id=inp.system["id"],
                     documents={n: sha256_text(t) for n, t in inp.documents.items()})
    classification, cnotes, ccall = classify(inp, llm, trail)
    catalogue = applicability.load_catalogue()
    fs = applicability.facts(inp.system, classification)
    criteria, notes = applicability.select(catalogue, fs)
    if trail:
        trail.append("applicability", facts=sorted(fs), criteria=[c["id"] for c in criteria])

    verified, calls = assess(inp, criteria, llm, trail) if criteria else ([], [])
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
    )


__all__ = ["run", "load_input", "verify_assessment", "InputError", "LLMError", "AuditResult"]
