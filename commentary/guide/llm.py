"""Small interface over the hosted multimodal model.

The pipeline and verifier only call `LLM.json(...)`: images + text in, one JSON
object out, validated against a JSON schema. Provider and model names come
from config.py; the API key from the environment (a Modal secret).

Note: the current Claude models reject non-default sampling parameters, so
there is no temperature=0. Determinism comes from a strict output schema and a
narrow prompt instead.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

from . import config


class LLMError(RuntimeError):
    """Any failure to get a schema-valid answer (network, refusal, bad JSON)."""


@dataclass
class ImagePart:
    jpeg: bytes
    label: str = ""


Part = str | ImagePart


class LLM(Protocol):
    def json(
        self,
        *,
        system: str,
        parts: list[Part],
        schema: dict,
        model: str,
        effort: str = "medium",
        max_tokens: int = 4000,
        timeout_s: float = 120.0,
    ) -> dict: ...


def _content(parts: list[Part]) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        if isinstance(p, ImagePart):
            if p.label:
                out.append({"type": "text", "text": p.label})
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(p.jpeg).decode("ascii"),
                    },
                }
            )
        else:
            out.append({"type": "text", "text": p})
    return out


class AnthropicLLM:
    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=1)

    def json(
        self,
        *,
        system: str,
        parts: list[Part],
        schema: dict,
        model: str,
        effort: str = "medium",
        max_tokens: int = 4000,
        timeout_s: float = 120.0,
    ) -> dict:
        a = self._anthropic
        try:
            resp = self.client.with_options(timeout=timeout_s).beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": _content(parts)}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                # Route policy declines to a fallback model instead of failing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.APITimeoutError as exc:
            raise LLMError("model timed out") from exc
        except a.RateLimitError as exc:
            raise LLMError("model rate limited") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"model error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("model unreachable") from exc

        if resp.stop_reason == "refusal":
            raise LLMError("model declined")
        if resp.stop_reason == "max_tokens":
            raise LLMError("model output truncated")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return parse_json_object(text)


def parse_json_object(text: str) -> dict:
    """Parse the model's JSON. Tolerates code fences / leading prose so a provider
    without schema enforcement still works; anything else is an LLMError."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1 :] if "\n" in text else text
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("no JSON object in model output")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("invalid JSON in model output") from exc
    if not isinstance(data, dict):
        raise LLMError("model output is not a JSON object")
    return data


def make_llm() -> LLM:
    if config.LLM_PROVIDER == "anthropic":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY not set (create the `anthropic` Modal secret)")
        return AnthropicLLM()
    raise LLMError(f"unknown LLM provider {config.LLM_PROVIDER!r}")
