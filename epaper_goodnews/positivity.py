from __future__ import annotations

import json
import logging
from threading import Event

from openai import OpenAI
from pydantic import BaseModel, Field

from .config import AppConfig
from .models import Article, ArticleScore, PositiveArticle, PositivitySelection
from .structured import parse_structured

LOGGER = logging.getLogger(__name__)

# Upper bound on articles sent for scoring, to cap cost if a feed suddenly balloons.
MAX_ARTICLES_TO_SCORE = 100
# ~60 articles x one-sentence rationale is ~2-3k tokens; leave generous headroom.
MAX_OUTPUT_TOKENS = 16000


class PositivityError(RuntimeError):
    pass


class ScoredItem(BaseModel):
    id: int = Field(description="The integer id of the article as given in the input")
    positivity: float = Field(description="Positivity score from 0.0 to 1.0")
    rationale: str = Field(description="One short sentence explaining the score")


class PositivityScores(BaseModel):
    items: list[ScoredItem]


def _build_payload(articles: list[Article]) -> list[dict]:
    return [
        {"id": index, "title": article.title, "summary": article.summary}
        for index, article in enumerate(articles)
    ]


def _make_prompt(payload: list[dict]) -> str:
    items = json.dumps(payload, ensure_ascii=False, indent=1)
    return (
        "You are a positivity filter for an Irish good-news display.\n"
        "Score EVERY article on a scale of 0.0 to 1.0 for how uplifting and positive it is:\n"
        "  1.0 — clearly good news: achievements, breakthroughs, community stories, nature, acts of kindness\n"
        "  0.5 — neutral or mixed: informational, neither good nor bad\n"
        "  0.0 — negative: crime, conflict, death, disaster, controversy\n\n"
        "Favour stories about: people helping people, scientific or medical breakthroughs, "
        "environmental wins, cultural celebrations, animals, sport achievements, and community milestones.\n\n"
        "Return one item per article, using the article's integer 'id', a 'positivity' score, "
        "and a one-sentence 'rationale'.\n\n"
        f"Articles:\n{items}"
    )


def _clamp(score: float) -> float:
    return max(0.0, min(1.0, score))


def select_positive_articles(
    *,
    config: AppConfig,
    client: OpenAI,
    articles: list[Article],
    cancel_event: Event | None = None,
) -> PositivitySelection:
    """Score all articles and select up to ``positivity_select_count`` above the threshold.

    Returns every score (for metadata) alongside the selection. An empty selection
    means nothing passed ``positivity_min_score``.
    """
    if cancel_event and cancel_event.is_set():
        raise PositivityError("Selection cancelled")

    if not articles:
        return PositivitySelection(selected=[], scores=[])

    to_score = articles[:MAX_ARTICLES_TO_SCORE]
    if len(articles) > len(to_score):
        LOGGER.warning(
            "Feed returned %d articles; scoring only the first %d",
            len(articles),
            len(to_score),
        )
    prompt = _make_prompt(_build_payload(to_score))

    LOGGER.info("Requesting positivity scores for %d articles", len(to_score))
    _, parsed = parse_structured(
        client=client,
        model=config.openai.text_model,
        prompt=prompt,
        text_format=PositivityScores,
        reasoning_effort=config.openai.positivity_reasoning_effort,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        error_cls=PositivityError,
        label="positivity",
    )

    if cancel_event and cancel_event.is_set():
        raise PositivityError("Selection cancelled after response")

    scored: dict[int, PositiveArticle] = {}
    for item in parsed.items:
        if not 0 <= item.id < len(to_score):
            LOGGER.warning("Positivity response referenced unknown id %s", item.id)
            continue
        if item.id in scored:
            continue
        scored[item.id] = PositiveArticle(
            article=to_score[item.id],
            score=_clamp(item.positivity),
            rationale=item.rationale,
        )

    missing = len(to_score) - len(scored)
    if missing:
        LOGGER.warning("Positivity response omitted %d of %d articles", missing, len(to_score))
    if not scored:
        raise PositivityError("Positivity response contained no usable scores")

    in_feed_order = [scored[index] for index in sorted(scored)]
    scores = [
        ArticleScore(
            guid=entry.article.guid,
            title=entry.article.title,
            score=entry.score,
            rationale=entry.rationale,
        )
        for entry in in_feed_order
    ]

    threshold = config.positivity_min_score
    passing = [entry for entry in in_feed_order if entry.score >= threshold]
    passing.sort(key=lambda entry: entry.score, reverse=True)
    selected = passing[: config.positivity_select_count]
    LOGGER.info(
        "%d of %d articles scored >= %.2f; selected %d",
        len(passing),
        len(scored),
        threshold,
        len(selected),
    )
    return PositivitySelection(selected=selected, scores=scores)
