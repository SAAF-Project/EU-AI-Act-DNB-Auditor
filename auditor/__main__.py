"""CLI.

  python -m auditor run samples/credit-scoring --out out/credit-scoring
  python -m auditor run samples/credit-scoring --out out/dry --dry-run
  python -m auditor verify-trail out/credit-scoring/trail.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import applicability
from .guard import AuditTrail, TrailError, verify_trail
from .llm import LLMError
from .pipeline import InputError, load_input, run, _criteria_block, _prompt
from .report import render


def _out_dir(path: str) -> Path:
    out = Path(path)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"Output directory {out} is not empty; choose a new one (results are never overwritten).")
    out.mkdir(parents=True, exist_ok=True)
    return out


def cmd_run(args: argparse.Namespace) -> int:
    input_dir = Path(args.input)
    try:
        inp = load_input(input_dir)
    except InputError as e:
        print(f"Input error: {e}", file=sys.stderr)
        return 2
    out = _out_dir(args.out)

    if args.dry_run:
        catalogue = applicability.load_catalogue()
        (out / "DRY-RUN-classify-prompt.txt").write_text(
            _prompt("classify") + "\n\n---USER---\n\n" + inp.fenced(), encoding="utf-8")
        (out / "DRY-RUN-assess-prompt-sample.txt").write_text(
            _prompt("assess") + "\n\n---USER---\n\n" + _criteria_block(catalogue["criteria"][:3])
            + "\n\nEvidence documents:\n\n" + inp.fenced(), encoding="utf-8")
        (out / "DRY-RUN-README.txt").write_text(
            "DRY RUN - NO MODEL WAS CALLED. THESE FILES ARE NOT AN AUDIT RESULT.\n"
            f"Input: {input_dir} ({len(inp.documents)} documents)\n", encoding="utf-8")
        print(f"DRY RUN: wrote prompts to {out}. No model call, no verdict.")
        return 0

    try:
        trail = AuditTrail.from_env(out / "trail.jsonl")
    except TrailError as e:
        print(f"Audit trail error: {e}", file=sys.stderr)
        return 2
    from .llm import ClaudeLLM

    try:
        result = run(input_dir, ClaudeLLM(model=args.model, effort=args.effort), trail)
    except LLMError as e:
        trail.append("run_failed", error=str(e))
        print(f"Model call failed, no verdict produced: {e}", file=sys.stderr)
        return 1

    (out / "report.md").write_text(render(result), encoding="utf-8")
    (out / "findings.json").write_text(json.dumps(result.findings, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "assessment.json").write_text(json.dumps({
        "system": result.system,
        "verdict": result.verdict,
        "classification": result.classification.model_dump(),
        "classification_notes": result.classification_notes,
        "criteria": [v.model_dump() for v in result.criteria],
        "financial_sector_notes": result.notes,
        "calls": result.calls,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{result.verdict} - {len(result.findings)} draft finding(s). Report: {out / 'report.md'}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    key = os.environ.get(AuditTrail.ENV_KEY, "")
    if not key:
        print(f"{AuditTrail.ENV_KEY} not set.", file=sys.stderr)
        return 2
    ok, msg = verify_trail(Path(args.trail), key.encode("utf-8"))
    print(("OK: " if ok else "FAILED: ") + msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="auditor", description="EU AI Act + DNB auditor for AI systems in financial institutions")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="audit one AI system (folder with system.json + docs/)")
    r.add_argument("input")
    r.add_argument("--out", required=True)
    r.add_argument("--dry-run", action="store_true", help="write the prompts only; no model call, no verdict")
    r.add_argument("--model", default=None, help="override the model (default claude-opus-5-5 or $AUDITOR_MODEL)")
    r.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    r.set_defaults(func=cmd_run)
    v = sub.add_parser("verify-trail", help="verify an audit trail's HMAC chain")
    v.add_argument("trail")
    v.set_defaults(func=cmd_verify)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
