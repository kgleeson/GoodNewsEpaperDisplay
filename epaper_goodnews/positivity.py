from __future__ import annotations

import json
import logging
from threading import Event
from typing import Iterable, List, Optional

from openai import OpenAI

from .config import AppConfig
from .models import Article, PositiveArticle

LOGGER = logging.getLogger(__name__)


class PositivityError(RuntimeError):
    pass


def _build_payload(articles: Iterable[Article]) -> List[dict]:
    payload = []
    for article in articles:
        payload.append(
            {
                "guid": article.guid,
                "title": article.title,
                "summary": article.summary,
                "link": article.link,
                "published": article.published.isoformat(),
            }
        )
    return payload


def _make_prompt(payload: List[dict]) -> str:
    items = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "You will receive recent news articles from RTÉ. "
        "Identify the most positive, uplifting stories. "
        "Return a JSON array under the key 'items'. Each item must have keys "
        "'guid', 'positivity' (0-1 float), and 'rationale' (brief sentence). Respond "
        "with JSON only and no additional commentary.\n\n"
        f"Articles:\n{items}"
    )


def _extract_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    output = getattr(response, "output", None)
    if not output:
        raise PositivityError("OpenAI response had no output text")
    parts: List[str] = []
    for item in output:
        content = getattr(item, "content", [])
        for block in content:
            block_text = getattr(block, "text", None)
            if block_text:
                parts.append(block_text)
    if not parts:
        raise PositivityError("OpenAI response missing textual content")
    return "".join(parts)


def select_positive_articles(
    *,
    config: AppConfig,
    client: OpenAI,
    articles: List[Article],
    cancel_event: Optional[Event] = None,
) -> List[PositiveArticle]:
    if cancel_event and cancel_event.is_set():
        raise PositivityError("Selection cancelled")

    if not articles:
        return []

    max_sample = min(config.positivity_sample_size, len(articles))
    sample = articles[:max_sample]
    prompt = _make_prompt(_build_payload(sample))

    LOGGER.info("Requesting positivity scores for %d articles", len(sample))
    try:
        response = client.responses.create(
            model=config.openai.text_model,
            input=prompt,
        )
    except Exception as exc:  # pragma: no cover - network failure
        raise PositivityError(f"OpenAI positivity call failed: {exc}") from exc

    if cancel_event and cancel_event.is_set():
        raise PositivityError("Selection cancelled after response")

    text = ""
    try:
        text = _extract_text(response)
        data = json.loads(text)
    except Exception as exc:
        LOGGER.error("Failed to parse positivity response: %s", text)
        raise PositivityError("Unable to parse positivity response") from exc

    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PositivityError("Unexpected positivity response structure")

    selected: List[PositiveArticle] = []
    by_guid = {article.guid: article for article in sample}

    for item in items:
        try:
            guid = item["guid"]
            score = float(item.get("positivity", 0))
            rationale = item.get("rationale")
        except KeyError:
            continue
        article = by_guid.get(guid)
        if not article:
            continue
        selected.append(PositiveArticle(article=article, score=score, rationale=rationale))

    selected.sort(key=lambda entry: entry.score, reverse=True)
    return selected[: config.positivity_select_count]
