from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # nothing passed the positivity threshold; previous image kept


@dataclass
class Article:
    guid: str
    title: str
    summary: str
    link: str
    published: datetime | None = None


@dataclass
class PositiveArticle:
    article: Article
    score: float
    rationale: str | None = None


@dataclass
class ArticleScore:
    """Positivity score for one fetched article, persisted for quality analysis."""

    guid: str
    title: str
    score: float
    rationale: str | None = None


@dataclass
class PositivitySelection:
    selected: list[PositiveArticle]
    scores: list[ArticleScore]


@dataclass
class HeadlineResult:
    headline: str
    subheading: str | None
    prompt: str
    response_id: str | None = None


@dataclass
class ImageResult:
    image_path: Path
    prompt: str
    response_id: str | None = None
    width: int = 800
    height: int = 480
    requested_size: str | None = None
    source_size: str | None = None
    usage: dict | None = None


@dataclass
class GenerationMetadata:
    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: RunStatus = RunStatus.SUCCESS
    timezone: str = "Europe/Dublin"
    device_type: str = ""
    articles: list[Article] = field(default_factory=list)
    positive_articles: list[PositiveArticle] = field(default_factory=list)
    article_scores: list[ArticleScore] = field(default_factory=list)
    positivity_min_score: float | None = None
    headline: HeadlineResult | None = None
    image: ImageResult | None = None
    errors: list[str] = field(default_factory=list)
    text_model: str | None = None
    image_model: str | None = None


@dataclass
class HealthStatus:
    last_success: datetime | None
    last_run: datetime | None
    last_status: RunStatus | None
    message: str | None = None
