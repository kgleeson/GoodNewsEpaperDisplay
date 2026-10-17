from __future__ import annotations

import json
import logging
from threading import Event

from openai import OpenAI
from pydantic import BaseModel

from .config import AppConfig
from .models import HeadlineResult, PositiveArticle
from .structured import parse_structured

LOGGER = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 4000


class HeadlineError(RuntimeError):
    pass


class HeadlinePayload(BaseModel):
    headline: str
    subheading: str


def _build_prompt(positives: list[PositiveArticle]) -> str:
    payload = [
        {
            "title": item.article.title,
            "summary": item.article.summary,
            "rationale": item.rationale,
        }
        for item in positives
    ]
    articles_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "You are writing for a good-news ePaper display aimed at Irish readers.\n"
        "Compose a single upbeat headline and a short subheading from these positive stories.\n\n"
        "Rules:\n"
        "- Headline: 8 words or fewer, warm and hopeful in tone, Irish English\n"
        "- Subheading: one sentence, adds context without repeating the headline\n"
        "- No exclamation marks, no clickbait phrasing, no ALL CAPS\n"
        "- Draw from the combined theme of all stories, not just one\n\n"
        f"Stories:\n{articles_json}"
    )


def generate_headline(
    *,
    config: AppConfig,
    client: OpenAI,
    positives: list[PositiveArticle],
    cancel_event: Event | None = None,
) -> HeadlineResult:
    if cancel_event and cancel_event.is_set():
        raise HeadlineError("Headline generation cancelled")

    if not positives:
        raise HeadlineError("No positive articles to summarize")

    prompt = _build_prompt(positives)
    LOGGER.info("Requesting headline generation from OpenAI")

    response, parsed = parse_structured(
        client=client,
        model=config.openai.text_model,
        prompt=prompt,
        text_format=HeadlinePayload,
        reasoning_effort=config.openai.headline_reasoning_effort,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        error_cls=HeadlineError,
        label="headline",
    )

    if cancel_event and cancel_event.is_set():
        raise HeadlineError("Headline generation cancelled after response")

    headline = parsed.headline.strip()
    if not headline:
        raise HeadlineError("Headline missing from response")
    subheading = parsed.subheading.strip()

    return HeadlineResult(
        headline=headline,
        subheading=subheading or None,
        prompt=prompt,
        response_id=getattr(response, "id", None),
    )
