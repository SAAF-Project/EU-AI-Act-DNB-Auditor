# Plan: EU AI Act + DNB Auditor (financial-sector companion to the EU AI Act Compliance Monitor)

## 1. Metadata

| Field | Value |
|---|---|
| **Plan file** | `barking-sniffer-eu-ai-act-dnb-auditor.md` |
| **Submitter** | barking-sniffer |
| **Session** | Hackathon #10 (Mollie, 10 November 2026) |
| **Status** | Do: prototype built |
| **Type** | Agent |
| **Track** | 3a, Compliance (GDPR, EU AI Act); secondary 5, AI Quality |
| **Primary domain** | Compliance & Regulatory: AI systems in financial services |
| **Builds on** | `plans/hackathon-3/eu-ai-act-compliance-monitor.md` · `plans/alignment/group-5-hackers-ai-audit-agents.md` |

## 2. Problem statement

Banks, insurers and other financial institutions are deploying AI for credit scoring, insurance pricing, fraud detection and KYC. Their internal auditors have to assess these systems against the EU AI Act and against what the financial supervisor expects. The existing SAAF plans cover the EU AI Act in general (Mathijs Schouten's compliance monitor) or AI audit in financial services in broad terms (Group 5). None of them covers the Dutch financial-supervision angle:
- The DNB SAFEST principles (2019) for AI in the financial sector.
- The AI Act provisions specific to financial institutions: Art. 74(6), under which the market surveillance authority is the financial supervisor, and the governance-equivalence clauses.
- DORA third-party risk for AI vendors.

Group 5's open question 2 ("are there regulatory benchmarks from the EBA or ECB?") is exactly this gap.

## 3. Scope

**In:** one AI system per run; EU AI Act classification (based on Mathijs's Prompt 1.5); deterministic criteria selection; assessment against AI Act Arts. 5, 9, 10, 12-15, 26, 27, 50, the six DNB SAFEST principles, GDPR Art. 22 and DORA Arts. 28-30; verified quotes; SAAF finding schema; HMAC audit trail.

**Out (next iterations):** inventory-wide monitoring and the regulatory-update feed (Mathijs's plan, Prompts 3-4); fairness and drift testing on model outputs (Group 5's agents 3 and 5); ALTAI self-assessment.

## 4. Four-pillar mapping

| Pillar | Contribution |
|---|---|
| Prompts | `prompts/classify.md` (adapted from the EU AI Act monitor's Prompt 1.5), `prompts/assess.md` |
| Tools | Python CLI, Claude API (`claude-opus-5-5`, structured outputs), quote-grounding verifier, HMAC trail |
| Regulatory | `regulatory/criteria.json`: AI Act, DNB SAFEST, GDPR Art. 22, DORA, and financial-sector notes. Proposed for upstreaming to `regulatory/frameworks/` once verified |
| Outputs | `finding-schema.json` findings, Markdown report with sign-off block |

## 5. How it fits with Mathijs's plan

The two are designed to chain. `system.json` uses the same per-system fields as the monitor's AI system inventory (id, name, classification inputs, owner). Once the monitor exists, it can call this agent for each system and merge the findings into its gap report. This agent then becomes the financial-sector layer of the monitor rather than a parallel tool.

## 6. PDCA

| Phase | Status | Notes |
|---|---|---|
| Plan | Complete | This document |
| Do | Prototype | Working CLI, 23 offline tests, synthetic credit-scoring sample |
| Check | Open | Live run on the sample; auditor verifies catalogue references; A1 stress test |
| Act | Open | Merge into, or chain with, the EU AI Act Compliance Monitor; add EBA loan-origination guidelines |

## 7. Help wanted

- A **developer** to own the repo and CI, and to add PDF input and the `saaf-guard` CI check.
- An **auditor or legal reviewer** to verify every entry in `regulatory/criteria.json` against EUR-Lex and dnb.nl, especially Art. 74(6), the governance-equivalence clauses and the current Dutch supervisory designation.
- A **financial institution** willing to run it on a redacted real model card and policy.
