"""Typed shapes for what the model returns and what the pipeline produces."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Tier = Literal[
    "prohibited",
    "high_risk_annex_i",
    "high_risk_annex_iii",
    "limited_risk_art50",
    "gpai",
    "minimal_risk",
]
Role = Literal["provider", "deployer", "importer", "distributor", "unknown"]
ModelStatus = Literal["met", "partially_met", "not_met", "not_evidenced", "not_applicable"]
# "inconclusive" is never produced by the model; the verifier sets it.
Status = Literal["met", "partially_met", "not_met", "not_evidenced", "not_applicable", "inconclusive"]


# ---- model output (structured outputs) -------------------------------------

class ArticleTrigger(BaseModel):
    article: str
    trigger_quote: str


class Classification(BaseModel):
    tiers: list[Tier]
    annex_iii_area: str
    art_6_3_carve_out_applies: bool
    role: Role
    fria_required: bool
    triggers: list[ArticleTrigger]
    uncertainty_flags: list[str]
    rationale: str


class CriterionAssessment(BaseModel):
    criterion_id: str
    status: ModelStatus
    evidence_quotes: list[str]
    gap: str
    recommendation: str


class Assessment(BaseModel):
    assessments: list[CriterionAssessment]


# ---- verified results -------------------------------------------------------

class VerifiedCriterion(BaseModel):
    criterion_id: str
    framework: str
    reference: str
    title: str
    status: Status
    evidence_quotes: list[str]
    gap: str
    recommendation: str
    verifier_notes: list[str] = []
