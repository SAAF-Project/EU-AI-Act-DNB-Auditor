"""Deterministic selection of criteria. The model classifies; code decides what gets tested.

`applies_when` in regulatory/criteria.json is a small AND-only expression over these facts:
  tier:high_risk | tier:prohibited | tier:limited_risk_art50 | role:<role>
  fria_required | financial_institution | third_party_provider | automated_decisions_on_individuals
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Classification

FINANCIAL_INSTITUTION_TYPES = {
    "credit_institution", "bank", "insurer", "reinsurer", "investment_firm",
    "payment_institution", "e_money_institution", "pension_fund", "asset_manager",
}

CATALOGUE_PATH = Path(__file__).resolve().parent.parent / "regulatory" / "criteria.json"


def load_catalogue(path: Path = CATALOGUE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def facts(system: dict, classification: Classification) -> set[str]:
    f: set[str] = set()
    tiers = set(classification.tiers)
    if tiers & {"high_risk_annex_i", "high_risk_annex_iii"}:
        f.add("tier:high_risk")
    if "prohibited" in tiers:
        f.add("tier:prohibited")
    if "limited_risk_art50" in tiers:
        f.add("tier:limited_risk_art50")
    f.add(f"role:{system.get('role', classification.role)}")
    if classification.fria_required:
        f.add("fria_required")
    if str(system.get("organisation_type", "")).lower() in FINANCIAL_INSTITUTION_TYPES:
        f.add("financial_institution")
    if system.get("third_party_provider"):
        f.add("third_party_provider")
    if system.get("personal_data") and system.get("automated_decisions_on_individuals"):
        f.add("automated_decisions_on_individuals")
    return f


def _matches(expr: str, fs: set[str]) -> bool:
    terms = [t.strip() for t in expr.split(" AND ")]
    return all(t in fs for t in terms if t)


def select(catalogue: dict, fs: set[str]) -> tuple[list[dict], list[dict]]:
    criteria = [c for c in catalogue["criteria"] if _matches(c["applies_when"], fs)]
    notes = [n for n in catalogue.get("financial_sector_notes", []) if _matches(n["applies_when"], fs)]
    return criteria, notes
