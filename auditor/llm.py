"""The only place that talks to the Claude API.

Any failure (API error, refusal, truncation, unparseable output) raises LLMError.
It never falls back to a default verdict (SAAF Guard rule 2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"


class LLMError(RuntimeError):
    pass


@dataclass
class CallMeta:
    model: str
    request_id: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


class LLM(Protocol):
    def call(self, system: str, user: str, schema: type[T]) -> tuple[T, CallMeta]: ...


class ClaudeLLM:
    def __init__(self, model: str | None = None, effort: str = "high"):
        import anthropic  # imported lazily so tests and --dry-run need no SDK credentials

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY / ant profile
        self.model = model or os.environ.get("AUDITOR_MODEL", DEFAULT_MODEL)
        self.effort = effort

    def call(self, system: str, user: str, schema: type[T]) -> tuple[T, CallMeta]:
        a = self._anthropic
        try:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # on a policy decline the API retries on a fallback model
                output_config={"effort": self.effort},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except a.AuthenticationError as e:
            raise LLMError("Authentication failed: check ANTHROPIC_API_KEY.") from e
        except TypeError as e:
            if "authentication" in str(e).lower():
                raise LLMError("No Claude credentials found: set ANTHROPIC_API_KEY (see .env.example).") from e
            raise
        except a.RateLimitError as e:
            raise LLMError("Rate limited by the API; retry later.") from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("Could not reach the API (network).") from e

        if resp.stop_reason == "refusal":
            raise LLMError("The model declined the request (refusal); no verdict produced.")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Output truncated at max_tokens; no verdict produced.")
        if resp.parsed_output is None:
            raise LLMError("Model output did not match the expected schema.")
        meta = CallMeta(
            model=resp.model,
            request_id=getattr(resp, "_request_id", "") or "",
            stop_reason=str(resp.stop_reason),
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
        return resp.parsed_output, meta
