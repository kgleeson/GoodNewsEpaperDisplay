from __future__ import annotations

import logging
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _find_refusal(response: Any) -> str | None:
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                return getattr(block, "refusal", None) or "refused"
    return None


def usage_dict(response: Any) -> dict | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)
    if isinstance(usage, dict):
        return usage
    return None


def parse_structured(
    *,
    client: OpenAI,
    model: str,
    prompt: str,
    text_format: type[T],
    reasoning_effort: str | None,
    max_output_tokens: int,
    error_cls: type[Exception],
    label: str,
) -> tuple[Any, T]:
    """Call the Responses API with Structured Outputs and return (response, parsed).

    Raises ``error_cls`` on API errors, truncated ("incomplete") responses, refusals,
    or a missing parsed payload.
    """
    kwargs: dict = {
        "model": model,
        "input": prompt,
        "text_format": text_format,
        "max_output_tokens": max_output_tokens,
    }
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    try:
        response = client.responses.parse(**kwargs)
    except Exception as exc:
        raise error_cls(f"OpenAI {label} call failed: {exc}") from exc

    usage = usage_dict(response)
    if usage:
        LOGGER.info("OpenAI %s usage: %s", label, usage)

    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) or "unknown reason"
        raise error_cls(f"OpenAI {label} response incomplete ({reason})")

    refusal = _find_refusal(response)
    if refusal:
        raise error_cls(f"OpenAI {label} request refused: {refusal}")

    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise error_cls(f"OpenAI {label} response had no parsed output")
    return response, parsed
