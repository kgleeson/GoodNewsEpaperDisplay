from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Article:
    guid: str
    title: str
    summary: str
    link: str
    published: Optional[datetime] = None


@dataclass
class PositiveArticle:
    article: Article
    score: float
    rationale: Optional[str] = None


@dataclass
class HeadlineResult:
    headline: str
    subheading: Optional[str]
    prompt: str
    response_id: Optional[str] = None


@dataclass
class ImageResult:
    image_path: Path
    prompt: str
    response_id: Optional[str] = None
    width: int = 800
    height: int = 480


@dataclass
class GenerationMetadata:
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: RunStatus = RunStatus.SUCCESS
    timezone: str = "Europe/Dublin"
    device_type: str = ""
    articles: List[Article] = field(default_factory=list)
    positive_articles: List[PositiveArticle] = field(default_factory=list)
    headline: Optional[HeadlineResult] = None
    image: Optional[ImageResult] = None
    errors: List[str] = field(default_factory=list)
    text_model: Optional[str] = None
    image_model: Optional[str] = None


@dataclass
class HealthStatus:
    last_success: Optional[datetime]
    last_run: Optional[datetime]
    last_status: Optional[RunStatus]
    message: Optional[str] = None
