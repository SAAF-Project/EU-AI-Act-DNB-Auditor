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
from .guard import AuditTrail, TrailError, sha256_text, verify_trail
from .llm import LLMError
from .pipeline import InputError, load_input, run, _criteria_block, _prompt
from .report import render


def _out_dir(path: str) -> Path:
    out = Path(path)
    if out.is_symlink():
        raise SystemExit(f"Output directory {out} is a symbolic link; choose a real directory.")
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
        findings_json = json.dumps(result.findings, indent=2, ensure_ascii=False)
        assessment_json = json.dumps({
            "system": result.system,
            "system_json_sha256": result.system_sha256,
            "verdict": result.verdict,
            "scope_facts": result.scope_facts,
            "skipped_files": result.skipped,
            "classification": result.classification.model_dump(),
            "classification_notes": result.classification_notes,
            "criteria": [v.model_dump() for v in result.criteria],
            "financial_sector_notes": result.notes,
            "calls": result.calls,
        }, indent=2, ensure_ascii=False)
        (out / "findings.json").write_text(findings_json, encoding="utf-8")
        (out / "assessment.json").write_text(assessment_json, encoding="utf-8")
        trail.append("outputs", findings_json_sha256=sha256_text(findings_json),
                     assessment_json_sha256=sha256_text(assessment_json))
        result.trail_head = trail.close("completed")
        (out / "report.md").write_text(render(result), encoding="utf-8")
    except LLMError as e:
        trail.append("run_failed", error=str(e))
        trail.close("failed")
        print(f"Model call failed, no verdict produced: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # every failure is logged in the trail; no partial verdict is produced
        try:
            trail.append("run_failed", error=f"{type(e).__name__}: {e}"[:500])
            trail.close("failed")
        except TrailError:
            pass
        print(f"Run failed, no verdict produced: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"{result.verdict} - {len(result.findings)} draft finding(s). Report: {out / 'report.md'}")
    print(f"Audit trail head: {result.trail_head}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    key = os.environ.get(AuditTrail.ENV_KEY, "")
    if not key:
        print(f"{AuditTrail.ENV_KEY} not set.", file=sys.stderr)
        return 2
    ok, msg = verify_trail(Path(args.trail), key.encode("utf-8"), expected_head=args.head)
    print(("OK: " if ok else "FAILED: ") + msg)
    return 0 if ok else 1


ENV_KEYS = ("ANTHROPIC_API_KEY", "AUDITOR_TRAIL_KEY", "AUDITOR_MODEL")


def load_env_file(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE pairs for the known keys from .env without executing anything.

    Values already set in the environment win. Unknown keys and any other syntax are ignored.
    """
    if not path.is_file() or path.is_symlink():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        value = value.strip('"').strip("'")
        if key in ENV_KEYS and value and value not in ("xxxx", "PASTE_YOUR_KEY_HERE") and key not in os.environ:
            os.environ[key] = value


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
    v.add_argument("--head", default=None, help="trail head printed in the report; must match")
    v.set_defaults(func=cmd_verify)
    args = p.parse_args(argv)
    load_env_file()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
