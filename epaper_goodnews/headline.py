from __future__ import annotations

import json
import logging
from threading import Event
from typing import List, Optional

from openai import OpenAI

from .config import AppConfig
from .models import HeadlineResult, PositiveArticle

LOGGER = logging.getLogger(__name__)


class HeadlineError(RuntimeError):
    pass


def _build_prompt(positives: List[PositiveArticle]) -> str:
    payload = [
        {
            "title": item.article.title,
            "summary": item.article.summary,
            "link": item.article.link,
            "positivity": item.score,
            "rationale": item.rationale,
        }
        for item in positives
    ]
    articles_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Compose a single upbeat news headline and optional subheading that combine "
        "the positive elements from these RTÉ stories. The headline should be fun, "
        "hopeful, and suitable for Irish readers. Provide JSON with keys 'headline' "
        "and optional 'subheading'. Keep headline under 80 characters. Respond with "
        "JSON only and no extra commentary.\n\n"
        f"Stories:\n{articles_json}"
    )


def _extract_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    output = getattr(response, "output", None)
    if not output:
        raise HeadlineError("OpenAI response had no output text")
    parts: List[str] = []
    for item in output:
        content = getattr(item, "content", [])
        for block in content:
            block_text = getattr(block, "text", None)
            if block_text:
                parts.append(block_text)
    if not parts:
        raise HeadlineError("OpenAI response missing textual content")
    return "".join(parts)


def generate_headline(
    *,
    config: AppConfig,
    client: OpenAI,
    positives: List[PositiveArticle],
    cancel_event: Optional[Event] = None,
) -> HeadlineResult:
    if cancel_event and cancel_event.is_set():
        raise HeadlineError("Headline generation cancelled")

    if not positives:
        raise HeadlineError("No positive articles to summarize")

    prompt = _build_prompt(positives)
    LOGGER.info("Requesting headline generation from OpenAI")

    try:
        response = client.responses.create(
            model=config.openai.text_model,
            input=prompt,
        )
    except Exception as exc:  # pragma: no cover - network failure
        raise HeadlineError(f"OpenAI headline call failed: {exc}") from exc

    if cancel_event and cancel_event.is_set():
        raise HeadlineError("Headline generation cancelled after response")

    text = ""
    try:
        text = _extract_text(response)
        payload = json.loads(text)
    except Exception as exc:
        LOGGER.error("Failed to parse headline response: %s", text)
        raise HeadlineError("Unable to parse headline response") from exc

    headline = payload.get("headline")
    if not headline:
        raise HeadlineError("Headline missing from response")
    subheading = payload.get("subheading")
    response_id = getattr(response, "id", None)

    return HeadlineResult(headline=headline.strip(), subheading=subheading, prompt=prompt, response_id=response_id)
